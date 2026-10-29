"""
Parse pasted decklists and CSV exports into card lines. Pure text handling:
matching lines to cards happens in collections.import_collection_items.

Decklist lines (Arena, MTGO, Moxfield and plain text):
    4 Lightning Bolt
    4x Lightning Bolt
    1 Lightning Bolt (M10) 146
    1 Lightning Bolt (2XM) 129 *F*        (*F* foil, *E* etched)
    SB: 2 Counterspell                     (MTGO sideboard)
Section headers (Deck, Sideboard, Commander, …) and // or # comments are skipped.

CSV needs a header row. Recognized columns (case and spacing don't matter):
    quantity / qty / count, name / card name, set / set code / edition code,
    collector number / card number / number, finish / foil / printing,
    condition, language / lang
"""
import csv
import io
import re
from dataclasses import dataclass

from models.database import CardCondition, CardFinish

MAX_LINES = 2000
MAX_QUANTITY = 10_000


@dataclass
class ParsedLine:
    line: int
    text: str
    quantity: int
    name: str | None
    set_code: str | None = None
    collector_number: str | None = None
    finish: CardFinish | None = None
    condition: CardCondition | None = None
    language: str | None = None


@dataclass
class LineError:
    line: int
    text: str
    error: str


class ImportTooLarge(ValueError):
    pass


# ----------------------------------------------------------------
# Decklists
# ----------------------------------------------------------------
_SECTION = re.compile(
    r"^(deck|main ?deck|sideboard|commander|companion|maybe ?board|considering|tokens?|about|name\b.*)$",
    re.IGNORECASE)
_DECK_LINE = re.compile(
    r"^(?:(?P<qty>\d+)\s*x?\s+)?"           # 4, 4x, 4 x
    r"(?P<name>.+?)"
    r"(?:\s+\((?P<set>[A-Za-z0-9]{2,6})\)"  # (M10)
    r"(?:\s+(?P<number>[^\s*]+))?)?"        # 146, 12a, ★1
    r"(?:\s+\*(?P<finish>[FfEe])\*)?\s*$",  # *F* / *E*
)


def parse_decklist(text: str) -> tuple[list[ParsedLine], list[LineError]]:
    lines: list[ParsedLine] = []
    errors: list[LineError] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("//", "#")) or _SECTION.match(stripped):
            continue
        if stripped.upper().startswith("SB:"):
            stripped = stripped[3:].strip()
        match = _DECK_LINE.match(stripped)
        if match is None:
            errors.append(LineError(number, raw, "Couldn't read this line"))
            continue
        quantity = int(match["qty"] or 1)
        if not 1 <= quantity <= MAX_QUANTITY:
            errors.append(LineError(number, raw, f"Quantity must be between 1 and {MAX_QUANTITY:,}"))
            continue
        finish = {"f": CardFinish.foil, "e": CardFinish.etched}.get((match["finish"] or "").lower())
        lines.append(ParsedLine(number, raw, quantity, match["name"].strip(),
                                set_code=match["set"].lower() if match["set"] else None,
                                collector_number=match["number"], finish=finish))
        if len(lines) > MAX_LINES:
            raise ImportTooLarge(f"Import up to {MAX_LINES:,} lines at a time")
    return lines, errors


# ----------------------------------------------------------------
# CSV
# ----------------------------------------------------------------
_COLUMNS = {
    "quantity": {"quantity", "qty", "count", "amount", "have"},
    "name": {"name", "card name", "card"},
    "set": {"set", "set code", "edition code", "edition"},
    "number": {"collector number", "card number", "number", "collector no", "cn", "collector"},
    "finish": {"finish", "foil", "printing"},
    "condition": {"condition"},
    "language": {"language", "lang"},
}
_FINISHES = {
    "": CardFinish.nonfoil, "no": CardFinish.nonfoil, "false": CardFinish.nonfoil, "0": CardFinish.nonfoil,
    "normal": CardFinish.nonfoil, "nonfoil": CardFinish.nonfoil, "non-foil": CardFinish.nonfoil,
    "foil": CardFinish.foil, "yes": CardFinish.foil, "true": CardFinish.foil, "1": CardFinish.foil,
    "etched": CardFinish.etched, "etched foil": CardFinish.etched, "foil etched": CardFinish.etched,
}
_CONDITIONS = {
    "nm": CardCondition.NM, "near mint": CardCondition.NM, "mint": CardCondition.NM, "m": CardCondition.NM,
    "lp": CardCondition.LP, "lightly played": CardCondition.LP, "excellent": CardCondition.LP,
    "ex": CardCondition.LP, "sp": CardCondition.LP, "slightly played": CardCondition.LP,
    "mp": CardCondition.MP, "moderately played": CardCondition.MP, "good": CardCondition.MP,
    "gd": CardCondition.MP, "played": CardCondition.MP, "pl": CardCondition.MP,
    "hp": CardCondition.HP, "heavily played": CardCondition.HP, "poor": CardCondition.HP,
    "dmg": CardCondition.DMG, "damaged": CardCondition.DMG,
}
_LANGUAGES = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de", "italian": "it", "portuguese": "pt",
    "japanese": "ja", "korean": "ko", "russian": "ru", "chinese simplified": "zhs", "simplified chinese": "zhs",
    "chinese traditional": "zht", "traditional chinese": "zht", "hebrew": "he", "latin": "la",
    "ancient greek": "grc", "arabic": "ar", "sanskrit": "sa", "phyrexian": "ph",
}


def _header_key(header: str) -> str | None:
    normalized = re.sub(r"[\s_]+", " ", header.strip().lower())
    return next((key for key, names in _COLUMNS.items() if normalized in names), None)


def parse_csv(text: str) -> tuple[list[ParsedLine], list[LineError]]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if not header:
        return [], [LineError(1, "", "The CSV is empty")]
    columns: dict[str, int] = {}
    for index, name in enumerate(header):
        key = _header_key(name)
        if key and key not in columns:
            columns[key] = index
    if "name" not in columns and not {"set", "number"} <= columns.keys():
        return [], [LineError(1, ",".join(header), "No card name column (or set and collector number columns)")]

    lines: list[ParsedLine] = []
    errors: list[LineError] = []
    for row in reader:
        number = reader.line_num
        raw = ",".join(row)
        if not any(cell.strip() for cell in row):
            continue

        def cell(key: str, row: list[str] = row) -> str:
            index = columns.get(key)
            return row[index].strip() if index is not None and index < len(row) else ""

        try:
            quantity = int(cell("quantity") or 1)
        except ValueError:
            errors.append(LineError(number, raw, f"Quantity {cell('quantity')!r} isn't a number"))
            continue
        if quantity == 0:
            continue
        if not 1 <= quantity <= MAX_QUANTITY:
            errors.append(LineError(number, raw, f"Quantity must be between 1 and {MAX_QUANTITY:,}"))
            continue
        finish = _FINISHES.get(cell("finish").lower())
        if finish is None:
            errors.append(LineError(number, raw, f"Unknown finish {cell('finish')!r}"))
            continue
        condition = None
        if cell("condition"):
            condition = _CONDITIONS.get(cell("condition").lower())
            if condition is None:
                errors.append(LineError(number, raw, f"Unknown condition {cell('condition')!r}"))
                continue
        language: str | None = None
        if cell("language"):
            language = _LANGUAGES.get(cell("language").lower(), cell("language").lower())
            if not re.fullmatch(r"[a-z]{2,3}", language):
                errors.append(LineError(number, raw, f"Unknown language {cell('language')!r}"))
                continue
        set_code = cell("set").lower()
        # Some exports put the set's full name here; only codes are usable.
        if set_code and not re.fullmatch(r"[a-z0-9]{2,6}", set_code):
            set_code = ""
        card_name = cell("name") or None
        if card_name is None and not (set_code and cell("number")):
            errors.append(LineError(number, raw, "No card name"))
            continue
        lines.append(ParsedLine(number, raw, quantity, card_name, set_code=set_code or None,
                                collector_number=cell("number") or None, finish=finish,
                                condition=condition, language=language))
        if len(lines) > MAX_LINES:
            raise ImportTooLarge(f"Import up to {MAX_LINES:,} rows at a time")
    return lines, errors
