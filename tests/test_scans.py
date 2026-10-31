import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient

from conftest import as_user
from services.scans.matching import parse_collector_line
from services.storage.storage import presign
from utils.settings import settings


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {"storage_endpoint": "https://acct.r2.cloudflarestorage.com", "storage_bucket": "scans-test",
                        "storage_access_key_id": "AKIDTEST", "storage_secret_access_key": "secret"}.items():
        monkeypatch.setattr(settings, name, value)


async def _collection(client: AsyncClient, user: uuid.UUID) -> str:
    resp = await client.post("/v1/collections", headers=as_user(user), json={"name": "Binder"})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _scan(client: AsyncClient, user: uuid.UUID, **body: Any) -> dict[str, Any]:
    resp = await client.post("/v1/scans", headers=as_user(user), json=body)
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def _top(scan: dict[str, Any]) -> tuple[str, float, str]:
    best = scan["candidates"][0]
    return best["card"]["name"], best["confidence"], best["match"]


# ----------------------------------------------------------------
# Storage
# ----------------------------------------------------------------
def test_presign_matches_aws_example() -> None:
    # From AWS's Signature Version 4 documentation ("Example: presigned URL").
    url = presign("GET", "https://examplebucket.s3.amazonaws.com/test.txt", region="us-east-1",
                  access_key_id="AKIAIOSFODNN7EXAMPLE", secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                  now=datetime(2013, 5, 24, tzinfo=timezone.utc), expires_seconds=86400)
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")


async def test_photo_upload(client: AsyncClient, make_user, bucket: None) -> None:
    user = await make_user()
    scan = await _scan(client, user, photo={"content_type": "image/jpeg", "size_bytes": 2_000_000})
    upload = urlsplit(scan["upload"]["url"])
    assert upload.netloc == "acct.r2.cloudflarestorage.com"
    assert upload.path == f"/scans-test/scans/{user}/{scan['id']}.jpg"
    query = parse_qs(upload.query)
    assert query["X-Amz-SignedHeaders"] == ["content-length;content-type;host"]
    assert query["X-Amz-Expires"] == [str(settings.storage_upload_seconds)]
    assert scan["upload"]["headers"] == {"Content-Type": "image/jpeg"}
    assert scan["status"] == "pending" and scan["candidates"] == []

    photo = urlsplit((await client.get(f"/v1/scans/{scan['id']}", headers=as_user(user))).json()["photo_url"])
    assert photo.path == upload.path and parse_qs(photo.query)["X-Amz-SignedHeaders"] == ["host"]

    too_big = await client.post("/v1/scans", headers=as_user(user),
                                json={"photo": {"content_type": "image/png", "size_bytes": 60_000_000}})
    assert too_big.status_code == 400
    wrong_type = await client.post("/v1/scans", headers=as_user(user),
                                   json={"photo": {"content_type": "image/gif", "size_bytes": 1000}})
    assert wrong_type.status_code == 422


async def test_photos_need_storage(client: AsyncClient, make_user) -> None:
    user = await make_user()
    resp = await client.post("/v1/scans", headers=as_user(user),
                             json={"photo": {"content_type": "image/jpeg", "size_bytes": 1000}})
    assert (resp.status_code, resp.json()["error"]["code"]) == (503, "storage_unavailable")
    # Scans without photos still work.
    assert (await _scan(client, user, name_text="Anything"))["photo_url"] is None


# ----------------------------------------------------------------
# Matching
# ----------------------------------------------------------------
def test_parse_collector_line() -> None:
    line = parse_collector_line("0150 R\nM21 • EN  Christopher Moeller")
    assert (line.numbers, line.language, line.foil) == (["150"], "en", None)
    assert "m21" in line.set_codes
    line = parse_collector_line("150/274 C\nM15 ★ JP")
    assert (line.numbers, line.language, line.foil) == (["150"], "ja", True)
    # OCR reads a zero as the letter O.
    assert "m20" in parse_collector_line("0042 U M2O EN").set_codes
    assert parse_collector_line("2X2 0012a").numbers == ["12a"]


async def test_matching(client: AsyncClient, make_user, make_card) -> None:
    user = await make_user()
    await make_card("Lightning Bolt", set_code="m21", collector_number="150")
    await make_card("Lightning Bolt", set_code="m10", collector_number="146")
    await make_card("Lightning Helix", set_code="rav", collector_number="213")
    await make_card("Delver of Secrets // Insectile Aberration", set_code="isd", collector_number="51")

    # Collector line and name agree: one exact printing.
    scan = await _scan(client, user, collector_text="0150 R\nM21 • EN", name_text="Lightning Bolt")
    assert scan["status"] == "matched" and _top(scan) == ("Lightning Bolt", 0.98, "collector_number")
    assert (scan["candidates"][0]["card"]["set_code"], scan["detected_language"]) == ("m21", "en")
    assert _top(await _scan(client, user, collector_text="0150 R M21 EN")) == ("Lightning Bolt", 0.9,
                                                                              "collector_number")

    # A misread name still matches; the set read from the collector line picks the printing.
    scan = await _scan(client, user, collector_text="0999 R M10", name_text="Lightnig Bolt")
    name, confidence, match = _top(scan)
    assert (name, match, scan["candidates"][0]["card"]["set_code"]) == ("Lightning Bolt", "name", "m10")
    assert 0.5 < confidence < 0.9

    # The number points at another card than the name: the name wins.
    scan = await _scan(client, user, collector_text="0150 R M21", name_text="Lightning Helix")
    assert _top(scan)[0] == "Lightning Helix"
    assert {c["card"]["name"]: c["confidence"] for c in scan["candidates"]}["Lightning Bolt"] < 0.5

    # Double-faced cards show only their front name.
    assert _top(await _scan(client, user, name_text="Delver of Secrets"))[0].startswith("Delver of Secrets")
    assert (await _scan(client, user, name_text="Qwxzv Plmok"))["status"] == "unmatched"

    # Re-recognizing replaces the result.
    scan = await _scan(client, user, name_text="Qwxzv Plmok")
    resp = await client.post(f"/v1/scans/{scan['id']}/recognize", headers=as_user(user),
                             json={"name_text": "Lightning Helix"})
    assert resp.status_code == 200 and _top(resp.json())[0] == "Lightning Helix"
    assert (await client.post(f"/v1/scans/{scan['id']}/recognize", headers=as_user(user),
                              json={})).status_code == 400


# ----------------------------------------------------------------
# Confirming into a collection
# ----------------------------------------------------------------
async def test_confirm(client: AsyncClient, make_user, make_card) -> None:
    user, other = await make_user(), await make_user()
    bolt = await make_card("Lightning Bolt", set_code="m21", collector_number="150", lang="ja")
    helix = await make_card("Lightning Helix", set_code="rav", collector_number="213", finishes=["nonfoil"])
    binder = await _collection(client, user)

    scan = await _scan(client, user, collector_text="0150 R M21 ★ JP", name_text="Lightning Bolt")
    assert (scan["detected_language"], scan["detected_foil"]) == ("ja", True)
    resp = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=as_user(user),
                             json={"collection_id": binder, "condition": "NM"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"
    item_id = resp.json()["collection_item_id"]
    item = (await client.get(f"/v1/collections/items/{item_id}", headers=as_user(user))).json()
    assert (item["card"]["id"], item["finish"], item["language"], item["quantity"]) == (str(bolt), "foil", "ja", 1)
    assert (item["recognition_status"], item["recognition_confidence"]) == ("confirmed", 0.98)
    again = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=as_user(user), json={"collection_id": binder})
    assert again.status_code == 409

    # The same card again adds a copy to the same item.
    scan = await _scan(client, user, collector_text="0150 R M21 ★ JP", name_text="Lightning Bolt")
    resp = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=as_user(user),
                             json={"collection_id": binder, "condition": "NM", "quantity": 2})
    assert resp.json()["collection_item_id"] == item_id
    assert (await client.get(f"/v1/collections/items/{item_id}", headers=as_user(user))).json()["quantity"] == 3

    # Picking a card the scan didn't suggest.
    scan = await _scan(client, user, name_text="Qwxzv Plmok")
    resp = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=as_user(user), json={"collection_id": binder})
    assert resp.status_code == 400
    resp = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=as_user(user),
                             json={"collection_id": binder, "card_id": str(helix)})
    item = (await client.get(f"/v1/collections/items/{resp.json()['collection_item_id']}",
                             headers=as_user(user))).json()
    assert (item["recognition_status"], item["finish"], item["language"]) == ("manual", "nonfoil", "en")
    # Finishes the printing doesn't come in are refused.
    scan = await _scan(client, user, name_text="Lightning Helix")
    resp = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=as_user(user),
                             json={"collection_id": binder, "finish": "foil"})
    assert resp.status_code == 400

    # Other people's scans and collections.
    assert (await client.get(f"/v1/scans/{scan['id']}", headers=as_user(other))).status_code == 404
    theirs = await _collection(client, other)
    resp = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=as_user(user), json={"collection_id": theirs})
    assert resp.status_code == 404


async def test_list_and_delete(client: AsyncClient, make_user, make_card) -> None:
    user = await make_user()
    await make_card("Lightning Bolt")
    matched = await _scan(client, user, name_text="Lightning Bolt")
    unmatched = await _scan(client, user, name_text="Qwxzv Plmok")

    page = (await client.get("/v1/scans", headers=as_user(user))).json()
    # (Both have the same created_at: tests run in one transaction.)
    assert {s["id"] for s in page["items"]} == {unmatched["id"], matched["id"]}
    page = (await client.get("/v1/scans", headers=as_user(user), params={"status": "matched"})).json()
    assert [s["id"] for s in page["items"]] == [matched["id"]]

    assert (await client.delete(f"/v1/scans/{matched['id']}", headers=as_user(user))).status_code == 204
    assert (await client.get(f"/v1/scans/{matched['id']}", headers=as_user(user))).status_code == 404
