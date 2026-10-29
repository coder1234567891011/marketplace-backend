import json
import uuid
from datetime import date
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Card, CardPrice
from scripts.scryfall_sync import card_rows, upsert_batch


def scryfall_card(**fields: Any) -> dict[str, Any]:
    """A Scryfall card object with the fields the sync reads."""
    return {
        "id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Lightning Bolt", "set": "m10",
        "set_name": "Magic 2010", "collector_number": "146", "lang": "en", "released_at": "2009-07-17",
        "rarity": "common", "layout": "normal", "mana_cost": "{R}", "cmc": 1.0, "type_line": "Instant",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.", "colors": ["R"], "color_identity": ["R"],
        "finishes": ["nonfoil", "foil"], "games": ["paper", "mtgo"],
        "image_uris": {"small": "https://img/s.jpg", "normal": "https://img/n.jpg", "png": "https://img/p.png"},
        "prices": {"usd": "1.50", "usd_foil": None, "eur": "1.20"},
        **fields,
    }


# ----------------------------------------------------------------
# Sync: Scryfall card → rows
# ----------------------------------------------------------------
def test_card_rows_keeps_what_the_catalog_needs() -> None:
    rows = card_rows(scryfall_card())
    assert rows is not None
    row, prices = rows
    assert row["set_code"] == "m10" and row["released_at"] == date(2009, 7, 17)
    assert row["image_uris"] == {"small": "https://img/s.jpg", "normal": "https://img/n.jpg"}
    assert prices == {"usd": "1.50", "eur": "1.20"}


def test_card_rows_skips_digital_only_and_art_cards() -> None:
    assert card_rows(scryfall_card(games=["arena"])) is None
    assert card_rows(scryfall_card(layout="art_series")) is None


def test_card_rows_double_faced_and_reversible() -> None:
    oracle_id = str(uuid.uuid4())
    face = {"oracle_id": oracle_id, "name": "Front", "mana_cost": "", "type_line": "Creature",
            "image_uris": {"small": "s", "normal": "n", "large": "l"}}
    row, _ = card_rows(scryfall_card(oracle_id=None, image_uris=None, card_faces=[face, {"name": "Back"}]))  # type: ignore[misc]
    assert row["oracle_id"] == uuid.UUID(oracle_id)
    assert row["image_uris"] is None
    assert row["card_faces"] == [
        {"name": "Front", "type_line": "Creature", "image_uris": {"small": "s", "normal": "n"}},
        {"name": "Back"},
    ]


async def test_upsert_inserts_then_updates_only_changes(db: AsyncSession) -> None:
    bolt = scryfall_card()
    batch = [card_rows(bolt), card_rows(scryfall_card(name="Counterspell", collector_number="50"))]
    assert await upsert_batch(db, batch) == (2, 2)  # type: ignore[arg-type]
    assert await upsert_batch(db, batch) == (0, 0)  # type: ignore[arg-type]

    card_id = await db.scalar(select(Card.id).where(Card.scryfall_id == uuid.UUID(bolt["id"])))
    changed = [card_rows({**bolt, "oracle_text": "New wording.", "prices": {"usd": "2.00"}})]
    assert await upsert_batch(db, changed) == (1, 1)  # type: ignore[arg-type]
    db.expire_all()
    card = await db.get(Card, card_id)
    assert card is not None and card.oracle_text == "New wording."
    assert (await db.get(CardPrice, card_id)).prices == {"usd": "2.00"}  # type: ignore[union-attr]


# ----------------------------------------------------------------
# API
# ----------------------------------------------------------------
async def test_read_card_includes_reference_prices(client: AsyncClient, db: AsyncSession, make_card) -> None:
    card_id = await make_card()
    db.add(CardPrice(card_id=card_id, prices={"usd": "1.50"}))
    await db.flush()
    resp = await client.get(f"/v1/cards/{card_id}")
    assert resp.status_code == 200
    assert resp.json()["reference_prices"] == {"usd": "1.50"}
    assert resp.json()["finishes"] == ["nonfoil", "foil"]

    assert (await client.get(f"/v1/cards/{uuid.uuid4()}")).status_code == 404


async def test_autocomplete_one_per_card_prefix_first_typo_tolerant(client: AsyncClient, make_card) -> None:
    oracle_id = uuid.uuid4()
    await make_card("Lightning Bolt", oracle_id=oracle_id, set_code="m10")
    await make_card("Lightning Bolt", oracle_id=oracle_id, set_code="2xm")
    await make_card("Chain Lightning")
    await make_card("Counterspell")

    names = [c["name"] for c in (await client.get("/v1/cards/autocomplete", params={"q": "light"})).json()]
    assert names == ["Lightning Bolt", "Chain Lightning"]

    typo = (await client.get("/v1/cards/autocomplete", params={"q": "lighting bolt"})).json()
    assert typo[0] == {"oracle_id": str(oracle_id), "name": "Lightning Bolt"}

    assert (await client.get("/v1/cards/autocomplete", params={"q": "l"})).status_code == 422


async def test_search_filters_and_pages(client: AsyncClient, make_card) -> None:
    await make_card("Lightning Bolt", color_identity=["R"], type_line="Instant", finishes=["nonfoil"])
    await make_card("Lightning Helix", color_identity=["R", "W"], type_line="Instant", finishes=["foil"])
    await make_card("Llanowar Elves", color_identity=["G"], type_line="Creature — Elf Druid", set_code="dom",
                    finishes=["nonfoil"])

    async def names(**params: str) -> list[str]:
        resp = await client.get("/v1/cards/search", params=params)
        assert resp.status_code == 200, resp.text
        return [c["name"] for c in resp.json()["items"]]

    assert await names(q="lightning") == ["Lightning Bolt", "Lightning Helix"]
    assert await names(colors="wr") == ["Lightning Helix"]
    assert await names(type="creature") == ["Llanowar Elves"]
    assert await names(set="DOM") == ["Llanowar Elves"]
    assert await names(finish="foil") == ["Lightning Helix"]
    assert await names(q="100%_") == []
    assert (await client.get("/v1/cards/search", params={"colors": "X"})).status_code == 422

    first = (await client.get("/v1/cards/search", params={"limit": 2})).json()
    rest = (await client.get("/v1/cards/search", params={"limit": 2, "cursor": first["next_cursor"]})).json()
    names_in_order = [c["name"] for c in first["items"] + rest["items"]]
    assert names_in_order == ["Lightning Bolt", "Lightning Helix", "Llanowar Elves"]
    assert rest["next_cursor"] is None


async def test_printings_newest_first(client: AsyncClient, make_card) -> None:
    oracle_id = uuid.uuid4()
    for set_code, released in (("lea", date(1993, 8, 5)), ("2xm", date(2020, 8, 7)), ("m10", date(2009, 7, 17))):
        await make_card(oracle_id=oracle_id, set_code=set_code, released_at=released)
    await make_card("Counterspell")

    first = (await client.get(f"/v1/cards/oracle/{oracle_id}/printings", params={"limit": 2})).json()
    rest = (await client.get(f"/v1/cards/oracle/{oracle_id}/printings",
                             params={"limit": 2, "cursor": first["next_cursor"]})).json()
    assert [c["set_code"] for c in first["items"] + rest["items"]] == ["2xm", "m10", "lea"]

    assert (await client.get(f"/v1/cards/oracle/{uuid.uuid4()}/printings")).status_code == 404
    assert (await client.get("/v1/cards/search", params={"cursor": "nonsense"})).status_code == 400


async def test_upsert_adopts_existing_printing(db: AsyncSession, make_card) -> None:
    """Cards added before the sync (with other ids) are updated in place, keeping their id."""
    card_id = await make_card(set_code="M10", collector_number="146")
    bolt = scryfall_card()
    assert await upsert_batch(db, [card_rows(bolt)]) == (1, 1)  # type: ignore[list-item]
    db.expire_all()
    card = await db.get(Card, card_id)
    assert card is not None
    assert (card.scryfall_id, card.set_code, card.type_line) == (uuid.UUID(bolt["id"]), "m10", "Instant")


async def test_sync_skips_and_reports_cards_it_cannot_merge(conn, make_card, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Two existing rows for one printing: one has Scryfall's id, the other the
    set and number. Syncing would make them identical, so that card is
    skipped and reported, and the rest still sync."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from scripts.scryfall_sync import sync

    bolt, counterspell = scryfall_card(), scryfall_card(name="Counterspell", collector_number="50")
    await make_card(scryfall_id=uuid.UUID(bolt["id"]), set_code="M10", collector_number="146")
    await make_card(set_code="m10", collector_number="146")
    path = tmp_path / "cards.jsonl"
    path.write_text("\n".join(json.dumps(c) for c in (bolt, counterspell)))

    sessionmaker = async_sessionmaker(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    conflicts = await sync(path, sessionmaker)
    assert len(conflicts) == 1 and "Lightning Bolt (m10 #146 en" in conflicts[0]
    async with sessionmaker() as session:
        assert await session.scalar(select(Card.name).where(Card.scryfall_id == uuid.UUID(counterspell["id"])))
