"""
Matching the text read from a card photo against the catalog.

The app reads two things on the device (OCR):
  * the collector line at the bottom left. Cards printed since 2014 have the
    collector number and set code there, e.g. "0150 R / M21 • EN", which
    identifies the exact printing;
  * the card's name, for older cards or when the collector line is unreadable.
    Names are matched approximately (pg_trgm), so OCR typos still match.

Confidence is a rough 0 to 1 score for ordering candidates and deciding
whether the app can skip asking the user; it isn't a calibrated probability.
"""
import re
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Card, CardFinish
from services.collections import importer
from services.collections.collections import pick_printing

# Language codes as printed on cards → Scryfall's codes.
PRINTED_LANGUAGES = {
    "EN": "en", "DE": "de", "FR": "fr", "IT": "it", "ES": "es", "SP": "es", "PT": "pt", "JP": "ja", "JA": "ja",
    "KO": "ko", "KR": "ko", "RU": "ru", "CS": "zhs", "CT": "zht", "PH": "ph",
}
# Collector number: up to 4 digits (zero-padded on newer cards), maybe a
# letter, maybe "/set size" on 2014-2019 cards.
_NUMBER = re.compile(r"(?<![A-Za-z0-9])0*(\d{1,4}[a-z]?)(?:\s*/\s*\d{1,4})?(?![A-Za-z0-9])")
# Set codes are printed in capitals, 3 to 6 letters and digits (M21, 2X2, PLST).
_SET_CODE = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9]{3,6})(?![A-Za-z0-9])")
_WORD = re.compile(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])")

MAX_CANDIDATES = 5
# How closely the name has to match for a collector number match to be trusted. A
# typo or two stays above it; "Lightning Helix" vs "Lightning Bolt" (0.76) does not.
NAME_AGREES = 0.85

Match = Literal["collector_number", "name"]


@dataclass
class CollectorLine:
    numbers: list[str] = field(default_factory=list)
    set_codes: list[str] = field(default_factory=list)
    language: str | None = None
    # A star instead of a dot between set and language marks a foil.
    foil: bool | None = None


@dataclass
class Candidate:
    card: Card
    confidence: float
    match: Match


def parse_collector_line(text: str) -> CollectorLine:
    line = CollectorLine(foil=True if "★" in text else None)
    for number in _NUMBER.findall(text):
        if number not in line.numbers:
            line.numbers.append(number.lower())
    for word in _WORD.findall(text):
        if word in PRINTED_LANGUAGES:
            line.language = PRINTED_LANGUAGES[word]
            break
    for code in _SET_CODE.findall(text):
        if code.isdigit():
            continue
        # OCR mixes up O and 0, so try both.
        for variant in (code, code.replace("O", "0"), code.replace("0", "O")):
            if variant.lower() not in line.set_codes:
                line.set_codes.append(variant.lower())
    return line


def name_similarity(card: Card, name: str) -> float:
    """How closely name matches the card's name (or the front face of a double-faced card)."""
    wanted = name.strip().lower()
    full = card.name.lower()
    return max(SequenceMatcher(None, wanted, n).ratio() for n in {full, full.split(" // ")[0]})


def _round(confidence: float) -> float:
    return round(min(confidence, 1.0), 3)


async def _by_collector_number(session: AsyncSession, line: CollectorLine) -> list[Card]:
    if not line.numbers or not line.set_codes:
        return []
    stmt = select(Card).where(func.lower(Card.collector_number).in_(line.numbers), Card.set_code.in_(line.set_codes))
    cards = list(await session.scalars(stmt))
    # The same set and number can exist in several languages: keep the one printed on the card.
    same_language = [c for c in cards if c.lang == line.language]
    return same_language or cards


async def _by_name(session: AsyncSession, name: str, line: CollectorLine | None) -> list[Candidate]:
    query = name.strip()
    front = func.split_part(Card.name, " // ", 1)
    score = func.greatest(func.similarity(Card.name, query), func.similarity(front, query))
    # % is pg_trgm's "similar to" operator, served by idx_cards_name_trgm.
    stmt = (select(Card.oracle_id, func.max(score).label("score"))
            .where(or_(Card.name.op("%")(query), Card.name.ilike(f"{query} // %")))
            .group_by(Card.oracle_id).order_by(func.max(score).desc()).limit(MAX_CANDIDATES))
    scores = {oracle_id: float(s) for oracle_id, s in await session.execute(stmt)}
    if not scores:
        return []
    printings: dict[uuid.UUID, list[Card]] = {}
    for printing in await session.scalars(select(Card).where(Card.oracle_id.in_(scores))):
        printings.setdefault(printing.oracle_id, []).append(printing)

    wanted = importer.ParsedLine(line=0, text="", quantity=1, name=None,
                                 finish=CardFinish.foil if line and line.foil else None,
                                 language=line.language if line else None)
    candidates = []
    for oracle_id, similarity in scores.items():
        # A set code read from the collector line narrows the printing even when its number didn't match.
        in_set = [c for c in printings[oracle_id] if line and c.set_code in line.set_codes]
        card = pick_printing(in_set or printings[oracle_id], wanted)
        if card is not None:
            candidates.append(Candidate(card, _round(0.85 * similarity + (0.05 if in_set else 0)), "name"))
    return candidates


async def match_card(session: AsyncSession, collector_text: str | None, name_text: str | None) -> list[Candidate]:
    """Candidate printings for what was read off a card, most likely first."""
    line = parse_collector_line(collector_text) if collector_text else None
    name = name_text.strip() if name_text and len(name_text.strip()) >= 3 else None

    found: dict[uuid.UUID, Candidate] = {}
    numbered = await _by_collector_number(session, line) if line else []
    for card in numbered:
        if name is None:
            confidence = 0.9 if len(numbered) == 1 else 0.6
        elif name_similarity(card, name) >= NAME_AGREES:
            confidence = 0.98 if len(numbered) == 1 else 0.9
        else:
            confidence = 0.4   # the number points at a different card than the name
        found[card.id] = Candidate(card, confidence, "collector_number")

    if name and not any(c.confidence >= 0.9 for c in found.values()):
        for candidate in await _by_name(session, name, line):
            known = found.get(candidate.card.id)
            if known is None or known.confidence < candidate.confidence:
                found[candidate.card.id] = candidate
    return sorted(found.values(), key=lambda c: -c.confidence)[:MAX_CANDIDATES]
