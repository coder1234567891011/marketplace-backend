"""
Cost kill switch for the Cloud Run API.

One function, two kinds of Pub/Sub message on the same topic:

  * Budget updates from Cloud Billing (several times a day). When actual
    spend reaches STOP_AT_FRACTION of the budget, the API is closed.
  * {"check": "usage"} from Cloud Scheduler (hourly). Month-to-date Cloud
    Run usage from Cloud Monitoring is compared with the free tier; when any
    measure reaches FREE_TIER_FRACTION of its allowance, the API is closed.
    Monitoring data is minutes old, so this catches spikes long before
    billing data (hours to a day behind) would.

"Closed" means the API's ingress is set to internal-only: the public URL
stops answering and stops costing money, but nothing is deleted.
deploy/cloudrun-reopen.sh turns it back on.

Environment:
  TARGET_SERVICE       projects/<project>/locations/<region>/services/<service>
  STOP_AT_FRACTION     budget fraction that trips the switch (default 0.9)
  FREE_TIER_FRACTION   free-tier fraction that trips the switch (default 0.8)
  ARTIFACT_LOCATIONS   comma-separated Artifact Registry locations to measure
                       (default: the service's region)
  Free-tier allowances (defaults are Google's published monthly limits; they
  apply per billing account, and only this service's usage is measured):
  FREE_REQUESTS        2,000,000 requests
  FREE_VCPU_SECONDS    180,000 vCPU-seconds
  FREE_GIB_SECONDS     360,000 GiB-seconds
  FREE_EGRESS_BYTES    1 GB outbound data from North America
  FREE_ARTIFACT_BYTES  0.5 GB Artifact Registry storage
"""
import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import functions_framework
from google.cloud import artifactregistry_v1, monitoring_v3, run_v2
from google.protobuf.duration_pb2 import Duration
from google.protobuf.field_mask_pb2 import FieldMask

INTERNAL_ONLY = run_v2.IngressTraffic.INGRESS_TRAFFIC_INTERNAL_ONLY
# Google bills by calendar month in Pacific time.
BILLING_TZ = ZoneInfo("America/Los_Angeles")

FREE_TIER_DEFAULTS = {
    "requests": ("FREE_REQUESTS", 2_000_000),
    "vcpu_seconds": ("FREE_VCPU_SECONDS", 180_000),
    "gib_seconds": ("FREE_GIB_SECONDS", 360_000),
    "egress_bytes": ("FREE_EGRESS_BYTES", 1_000_000_000),
    "artifact_bytes": ("FREE_ARTIFACT_BYTES", 500_000_000),
}


@dataclass(frozen=True)
class Target:
    project: str
    region: str
    service: str

    @classmethod
    def parse(cls, name: str) -> "Target":
        _, project, _, region, _, service = name.split("/")
        return cls(project, region, service)

    @property
    def name(self) -> str:
        return f"projects/{self.project}/locations/{self.region}/services/{self.service}"


# ----------------------------------------------------------------
# Budget check
# ----------------------------------------------------------------
def over_budget(notification: dict, stop_at_fraction: float) -> bool:
    cost = float(notification["costAmount"])
    budget = float(notification["budgetAmount"])
    return budget > 0 and cost >= budget * stop_at_fraction


# ----------------------------------------------------------------
# Usage check
# ----------------------------------------------------------------
def free_tier_limits() -> dict[str, float]:
    return {key: float(os.environ.get(env, default)) for key, (env, default) in FREE_TIER_DEFAULTS.items()}


def month_start(now: datetime) -> datetime:
    local = now.astimezone(BILLING_TZ)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def parse_cpu(value: str) -> float:
    return float(value[:-1]) / 1000 if value.endswith("m") else float(value)


def parse_memory_gib(value: str) -> float:
    units = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "K": 10**3, "M": 10**6, "G": 10**9}
    for suffix, factor in units.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * factor / 2**30
    return float(value) / 2**30


def service_resources(service: run_v2.Service) -> tuple[float, float]:
    """(vCPUs, GiB of memory) per instance, as configured on the service."""
    limits = service.template.containers[0].resources.limits
    return parse_cpu(limits.get("cpu", "1")), parse_memory_gib(limits.get("memory", "512Mi"))


def sum_metric(client: monitoring_v3.MetricServiceClient, target: Target, metric: str,
               start: datetime, end: datetime) -> float:
    """Total of a DELTA metric for the service between start and end."""
    seconds = max(60, int((end - start).total_seconds()))
    results = client.list_time_series(request={
        "name": f"projects/{target.project}",
        "filter": (f'metric.type="run.googleapis.com/{metric}" AND resource.type="cloud_run_revision" '
                   f'AND resource.labels.service_name="{target.service}"'),
        "interval": monitoring_v3.TimeInterval(
            start_time={"seconds": int(start.timestamp())}, end_time={"seconds": int(end.timestamp())}),
        "aggregation": monitoring_v3.Aggregation(
            alignment_period=Duration(seconds=seconds),
            per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
            cross_series_reducer=monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
        ),
        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
    })
    total = 0.0
    for series in results:
        for point in series.points:
            total += point.value.int64_value or point.value.double_value
    return total


def artifact_storage_bytes(client: artifactregistry_v1.ArtifactRegistryClient, project: str,
                           locations: list[str]) -> float:
    # Covers every repository in these locations: the API's images
    # (marketplace) and this function's (gcf-artifacts).
    return float(sum(
        repo.size_bytes
        for location in locations
        for repo in client.list_repositories(parent=f"projects/{project}/locations/{location}")
    ))


def measure_usage(target: Target, service: run_v2.Service, now: datetime) -> dict[str, float]:
    start = month_start(now)
    monitoring = monitoring_v3.MetricServiceClient()
    vcpus, gib = service_resources(service)
    instance_seconds = sum_metric(monitoring, target, "container/billable_instance_time", start, now)
    locations = [loc.strip() for loc in os.environ.get("ARTIFACT_LOCATIONS", target.region).split(",") if loc.strip()]
    return {
        "requests": sum_metric(monitoring, target, "request_count", start, now),
        "vcpu_seconds": instance_seconds * vcpus,
        "gib_seconds": instance_seconds * gib,
        # All outbound traffic, including responses and queries to Supabase:
        # deliberately counts more than Google may bill as egress.
        "egress_bytes": sum_metric(monitoring, target, "container/network/sent_bytes_count", start, now),
        "artifact_bytes": artifact_storage_bytes(artifactregistry_v1.ArtifactRegistryClient(),
                                                 target.project, locations),
    }


def over_free_tier(usage: dict[str, float], limits: dict[str, float], fraction: float) -> list[str]:
    return [key for key, used in usage.items() if limits[key] > 0 and used >= limits[key] * fraction]


def usage_summary(usage: dict[str, float], limits: dict[str, float]) -> str:
    return ", ".join(f"{key} {used:,.0f}/{limits[key]:,.0f} ({used / limits[key]:.0%})"
                     for key, used in usage.items() if limits[key] > 0)


# ----------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------
def close_api(client: run_v2.ServicesClient, service: run_v2.Service, reason: str) -> None:
    if service.ingress == INTERNAL_ONLY:
        print(f"{reason}: {service.name} is already closed to the public")
        return
    service.ingress = INTERNAL_ONLY
    # Only the ingress field changes, so no new revision is created.
    client.update_service(service=service, update_mask=FieldMask(paths=["ingress"])).result()
    print(f"{reason}: closed {service.name} to public traffic")


@functions_framework.cloud_event
def stop_api(cloud_event) -> None:  # type: ignore[no-untyped-def]
    message = json.loads(base64.b64decode(cloud_event.data["message"]["data"]))
    target = Target.parse(os.environ["TARGET_SERVICE"])
    client = run_v2.ServicesClient()

    if message.get("check") == "usage":
        service = client.get_service(name=target.name)
        limits = free_tier_limits()
        fraction = float(os.environ.get("FREE_TIER_FRACTION", "0.8"))
        usage = measure_usage(target, service, datetime.now(timezone.utc))
        summary = usage_summary(usage, limits)
        exceeded = over_free_tier(usage, limits, fraction)
        if not exceeded:
            print(f"Free-tier usage this month: {summary}")
            return
        close_api(client, service, f"Free-tier usage over {fraction:.0%} ({', '.join(exceeded)}): {summary}")
        return

    stop_at = float(os.environ.get("STOP_AT_FRACTION", "0.9"))
    cost, budget = message.get("costAmount"), message.get("budgetAmount")
    if not over_budget(message, stop_at):
        print(f"Spend {cost} of {budget} {message.get('currencyCode', '')}: under the limit")
        return
    close_api(client, client.get_service(name=target.name), f"Spend {cost} of {budget}")
