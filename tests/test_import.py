import uuid
from datetime import date

from httpx import AsyncClient

from conftest import as_user
from models.database import CardCondition, CardFinish
from services.collections.importer import parse_csv, parse_decklist


# ----------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------
def test_parse_decklist_formats() -> None:
    lines, errors = parse_decklist("""
Deck
4 Lightning Bolt
2x Counterspell
1 Lightning Bolt (M10) 146
1 Delver of Secrets (ISD) 51a *F*
Brainstorm
// a comment

Sideboard
SB: 3 Pyroblast
0 Nothing
""")
    got = [(ln.quantity, ln.name, ln.set_code, ln.collector_number, ln.finish) for ln in lines]
    assert got == [
        (4, "Lightning Bolt", None, None, None),
        (2, "Counterspell", None, None, None),
        (1, "Lightning Bolt", "m10", "146", None),
        (1, "Delver of Secrets", "isd", "51a", CardFinish.foil),
        (1, "Brainstorm", None, None, None),
        (3, "Pyroblast", None, None, None),
    ]
    assert [(e.line, e.error) for e in errors] == [(12, "Quantity must be between 1 and 10,000")]


def test_parse_csv_with_export_headers() -> None:
    lines, errors = parse_csv(
        "Count,Name,Edition,Collector Number,Foil,Condition,Language\n"
        "2,Lightning Bolt,M10,146,foil,Near Mint,Japanese\n"
        "1,Counterspell,Fourth Edition,,,lightly played,\n"
        "x,Bad,,,,,\n"
        "1,Odd,,,sparkly,,\n"
    )
    assert [(ln.quantity, ln.name, ln.set_code, ln.collector_number, ln.finish, ln.condition, ln.language)
            for ln in lines] == [
        (2, "Lightning Bolt", "m10", "146", CardFinish.foil, CardCondition.NM, "ja"),
        # A full set name isn't a code, so it's ignored.
        (1, "Counterspell", None, None, CardFinish.nonfoil, CardCondition.LP, None),
    ]
    assert [e.line for e in errors] == [4, 5]

    _, errors = parse_csv("Foo,Bar\n1,2\n")
    assert errors[0].error.startswith("No card name column")


# ----------------------------------------------------------------
# API
# ----------------------------------------------------------------
async def _collection(client: AsyncClient, user: uuid.UUID) -> str:
    return (await client.post("/v1/collections", headers=as_user(user), json={})).json()["id"]


async def test_import_dry_run_then_add_and_merge(client: AsyncClient, make_user, make_card) -> None:
    alice = await make_user()
    oracle = uuid.uuid4()
    old = await make_card("Lightning Bolt", oracle_id=oracle, set_code="m10", collector_number="146",
                          released_at=date(2009, 7, 17))
    new = await make_card("Lightning Bolt", oracle_id=oracle, set_code="2xm", collector_number="129",
                          released_at=date(2020, 8, 7), finishes=["nonfoil"])
    dfc = await make_card("Delver of Secrets // Insectile Aberration", set_code="isd", collector_number="51")
    coll = await _collection(client, alice)
    text = ("4 Lightning Bolt (M10) 146\n"
            "2 Lightning Bolt\n"
            "1 Lightning Bolt *F*\n"
            "1 Delver of Secrets\n"
            "1 Black Lotus\n"
            "1 Lightning Bolt (2XM) 129 *E*\n")

    preview = await client.post(f"/v1/collections/{coll}/import", params={"dry_run": True},
                                headers=as_user(alice), json={"format": "decklist", "text": text})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["dry_run"], body["items_created"]) == (True, 0)
    matched = {m["line"]: m for m in body["matched"]}
    assert matched[1]["card"]["id"] == str(old) and matched[1]["note"] is None
    assert matched[2]["card"]["id"] == str(new) and "No set given" in matched[2]["note"]
    # Only the M10 printing comes in foil.
    assert matched[3]["card"]["id"] == str(old)
    assert matched[4]["card"]["id"] == str(dfc)
    assert [(u["line"], u["error"]) for u in body["unmatched"]] == [
        (5, "No card with this name, set or number"), (6, "Not printed in etched (nonfoil)")]
    items = (await client.get(f"/v1/collections/{coll}/items", headers=as_user(alice))).json()["items"]
    assert items == []

    added = (await client.post(f"/v1/collections/{coll}/import", headers=as_user(alice),
                               json={"format": "decklist", "text": text, "condition": "NM"})).json()
    assert (added["items_created"], added["items_updated"]) == (4, 0)
    again = (await client.post(f"/v1/collections/{coll}/import", headers=as_user(alice),
                               json={"format": "decklist", "text": "3 Lightning Bolt (M10) 146", "condition": "NM"}))
    assert (again.json()["items_created"], again.json()["items_updated"]) == (0, 1)

    items = (await client.get(f"/v1/collections/{coll}/items", headers=as_user(alice))).json()["items"]
    quantities = {(i["card"]["id"], i["finish"]): i["quantity"] for i in items}
    assert quantities == {(str(old), "nonfoil"): 7, (str(new), "nonfoil"): 2, (str(old), "foil"): 1,
                          (str(dfc), "nonfoil"): 1}


async def test_import_csv_and_ownership(client: AsyncClient, make_user, make_card) -> None:
    alice, bob = await make_user(), await make_user()
    await make_card("Counterspell", set_code="4ed", collector_number="65")
    coll = await _collection(client, alice)
    csv_text = "Quantity,Name,Set,Collector Number,Condition\n2,Counterspell,4ED,65,LP\n"

    resp = await client.post(f"/v1/collections/{coll}/import", headers=as_user(bob),
                             json={"format": "csv", "text": csv_text})
    assert resp.status_code == 404

    resp = await client.post(f"/v1/collections/{coll}/import", headers=as_user(alice),
                             json={"format": "csv", "text": csv_text, "language": "de"})
    assert resp.status_code == 200, resp.text
    line = resp.json()["matched"][0]
    assert (line["quantity"], line["condition"], line["language"]) == (2, "LP", "de")


async def test_import_prefers_regular_released_printings(client: AsyncClient, make_user, make_card) -> None:
    alice = await make_user()
    oracle = uuid.uuid4()
    regular = await make_card("Path to Exile", oracle_id=oracle, set_code="con", collector_number="15",
                              set_type="expansion", released_at=date(2009, 2, 6))
    await make_card("Path to Exile", oracle_id=oracle, set_code="pcon", collector_number="15p",
                    set_type="promo", promo=True, released_at=date(2010, 1, 1))
    await make_card("Path to Exile", oracle_id=oracle, set_code="zzz", collector_number="1",
                    set_type="commander", released_at=date(2999, 1, 1))
    await make_card("Counterspell", set_code="con", collector_number="99")
    coll = await _collection(client, alice)

    body = (await client.post(f"/v1/collections/{coll}/import", params={"dry_run": True}, headers=as_user(alice),
                              json={"format": "decklist", "text": "1 Path to Exile\n1 Path to Exile (CON) 99"})).json()
    assert [m["card"]["id"] for m in body["matched"]] == [str(regular), str(regular)]
    # (CON) 99 is Counterspell: the name wins and the number is flagged.
    assert body["matched"][1]["note"] == "#99 isn't this card; matched by name in that set"
