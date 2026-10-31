import json
import re
import uuid

from httpx import AsyncClient

from conftest import as_user
from scripts.export_api import POSTMAN_PATH, SPEC_PATH, build_postman, build_spec, spec_yaml

STALE = "out of date: run `cd src && ../.venv/bin/python -m scripts.export_api`"


def test_exported_files_are_current() -> None:
    spec = build_spec()
    assert SPEC_PATH.read_text() == spec_yaml(spec), f"api-spec.yaml is {STALE}"
    assert json.loads(POSTMAN_PATH.read_text()) == build_postman(spec), f"postman_collection.json is {STALE}"


async def test_postman_examples_pass_validation(client: AsyncClient, make_user) -> None:
    """Every request in the collection, sent as-is (ids zeroed), gets past request validation."""
    user = await make_user()
    collection = json.loads(POSTMAN_PATH.read_text())
    sent = 0
    for folder in collection["item"]:
        if folder["name"] == "auth":
            continue
        for entry in folder["item"]:
            request = entry["request"]
            url = request["url"]["raw"].replace("{{baseUrl}}", "")
            url = re.sub(r":[a-z_]+", str(uuid.UUID(int=0)), url)
            body = request.get("body", {}).get("raw")
            headers = as_user(user) | ({"Content-Type": "application/json"} if body else {})
            resp = await client.request(request["method"], url, headers=headers,
                                        content=body.encode() if body else None)
            assert resp.status_code != 422, f"{entry['name']}: {resp.text}"
            sent += 1
    assert sent == 95
