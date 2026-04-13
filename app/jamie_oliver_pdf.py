from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from app.ingredients import build_ingredient_payload, normalize_ingredient_text

from app.extractor import RecipeDraft

INGREDIENT_START_RE = re.compile(
    r"^(?:optional:|\d+\s*x\s*\d+|\d|[¼½¾⅓⅔⅛]|one\b|two\b|three\b|four\b|five\b|a\b|an\b)",
    re.IGNORECASE,
)
STEP_NUMBER_RE = re.compile(r"^\d+$")
INLINE_STEP_RE = re.compile(r"^(?P<heading>[A-Z][A-Z0-9 &/'().-]+:?)\s+(?P<number>\d+)$")
UPPERCASE_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 &/'().-]+:?$")
TIME_RE = re.compile(r"\b(?:\d+\s*(?:mins?|minutes?|hours?))\b", re.IGNORECASE)
MAKES_RE = re.compile(r"^(?:MAKES|SERVES)\s+(?P<value>.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class PdfBlock:
    page_number: int
    x0: float
    y0: float
    text: str


@dataclass(frozen=True)
class JamieOliverPdfParseResult:
    title: str
    author: str
    total_time: str
    difficulty: str
    makes: str
    source_metadata: dict[str, Any]
    drafts: list[RecipeDraft]


def extract_jamie_oliver_pdf(
    *,
    cookbook_title: str,
    filename: str,
    object_key: str,
    content_type: str,
    file_bytes: bytes,
) -> JamieOliverPdfParseResult:
    document = fitz.open(stream=file_bytes, filetype="pdf")
    page_count = document.page_count
    try:
        if page_count == 0:
            raise ValueError("upload is empty or unreadable as a Jamie Oliver recipe PDF template.")

        pages = [document.load_page(page_number) for page_number in range(page_count)]
        blocks = _extract_blocks(pages)
        page_width = pages[0].rect.width if pages else 0.0
    finally:
        document.close()

    title = _extract_title(blocks) or _fallback_title(filename, cookbook_title)
    split = _column_split(blocks, page_width)
    total_time, difficulty, makes = _extract_header_details(blocks)
    ingredients, top_tip = _extract_ingredients(blocks, split)
    method_steps, preparation_notes = _extract_method_content(blocks, split)

    if not total_time or not difficulty or not makes:
        raise ValueError(
            "upload does not match the Jamie Oliver PDF template: expected time, difficulty, and makes/serves metadata."
        )
    if not ingredients:
        raise ValueError(
            "upload does not match the Jamie Oliver PDF template: expected an INGREDIENTS section."
        )
    if not method_steps:
        raise ValueError(
            "upload does not match the Jamie Oliver PDF template: expected a METHOD section with numbered steps."
        )

    supplemental_sections: list[dict[str, Any]] = []
    if top_tip:
        supplemental_sections.append({"heading": "Top Tip", "lines": [top_tip]})

    source_metadata = {
        key: value
        for key, value in {
            "author": "Jamie Oliver",
            "total_time": total_time,
            "difficulty": difficulty,
            "makes": makes,
            "preparation_notes": preparation_notes,
            "supplemental_sections": supplemental_sections,
        }.items()
        if value
    }

    draft = RecipeDraft(
        title=title,
        ingredients=[_ingredient_record(raw) for raw in ingredients],
        method_steps=method_steps,
        source={
            "object_key": object_key,
            "format": "pdf",
            "chapter_title": title,
            "page_start": 1,
            "page_end": page_count,
            "anchor": Path(filename).name,
            "excerpt": top_tip or title,
            "metadata": source_metadata,
        },
        images=[],
        confidence=0.99,
        notes=["Parsed by Jamie Oliver PDF extractor"],
        review_status="verified",
        review_reasons=[],
    )
    return JamieOliverPdfParseResult(
        title=title,
        author="Jamie Oliver",
        total_time=total_time,
        difficulty=difficulty,
        makes=makes,
        source_metadata=source_metadata,
        drafts=[draft],
    )


def _extract_blocks(pages: list[fitz.Page]) -> list[PdfBlock]:
    blocks: list[PdfBlock] = []
    for page_number, page in enumerate(pages, start=1):
        for raw_block in page.get_text("blocks"):
            text = " ".join(raw_block[4].replace("\xa0", " ").split())
            if not text:
                continue
            blocks.append(
                PdfBlock(
                    page_number=page_number,
                    x0=float(raw_block[0]),
                    y0=float(raw_block[1]),
                    text=text,
                )
            )
    blocks.sort(key=lambda item: (item.page_number, item.y0, item.x0))
    return blocks


def _extract_title(blocks: list[PdfBlock]) -> str:
    for block in blocks:
        if block.page_number == 1 and block.y0 < 90 and block.text not in {"INGREDIENTS", "METHOD"}:
            return _display_text(block.text)
    return ""


def _column_split(blocks: list[PdfBlock], page_width: float) -> float:
    ingredient_heading = next(
        (block for block in blocks if block.page_number == 1 and block.text == "INGREDIENTS"),
        None,
    )
    method_heading = next(
        (block for block in blocks if block.page_number == 1 and block.text == "METHOD"),
        None,
    )
    if ingredient_heading and method_heading and method_heading.x0 > ingredient_heading.x0:
        return (ingredient_heading.x0 + method_heading.x0) / 2
    return page_width * 0.34 if page_width else 200.0


def _extract_header_details(blocks: list[PdfBlock]) -> tuple[str, str, str]:
    ingredients_heading = next(
        (block for block in blocks if block.page_number == 1 and block.text == "INGREDIENTS"),
        None,
    )
    upper_bound = ingredients_heading.y0 if ingredients_heading else 190.0

    header_blocks = [
        block
        for block in blocks
        if block.page_number == 1
        and 80 <= block.y0 < upper_bound
        and block.text not in {"INGREDIENTS", "METHOD"}
    ]

    total_time = ""
    difficulty = ""
    makes = ""
    for block in header_blocks:
        text = " ".join(block.text.split())
        if not total_time and TIME_RE.search(text):
            total_time = _display_text(text)
            continue
        makes_match = MAKES_RE.match(text)
        if not makes and makes_match:
            makes = _display_text(makes_match.group("value"))
            continue
        if not difficulty:
            difficulty = _display_text(text)

    combined = " ".join(block.text for block in header_blocks).strip()
    if not combined:
        return total_time, difficulty, makes

    recompute_from_combined = len(header_blocks) == 1 or bool(
        re.search(r"\b(?:MAKES|SERVES)\b", header_blocks[0].text, re.IGNORECASE)
    )
    if recompute_from_combined:
        total_time = ""
        difficulty = ""
        makes = ""

    makes_match = re.search(r"\b(?:MAKES|SERVES)\s+(?P<value>.+)$", combined, re.IGNORECASE)
    if makes_match:
        makes = _display_text(makes_match.group("value"))
        combined = combined[: makes_match.start()].strip()

    time_match = re.match(r"^(?P<value>.+?\bTIME\b)\s*(?P<rest>.*)$", combined, re.IGNORECASE)
    if not time_match:
        time_match = re.match(
            r"^(?P<value>.+?\b(?:MINS?\b|MINUTES?\b|HOURS?\b))(?:\s+(?P<rest>.+))?$",
            combined,
            re.IGNORECASE,
        )
    if time_match:
        total_time = _display_text(time_match.group("value"))
        combined = (time_match.group("rest") or "").strip()

    if combined:
        difficulty = _display_text(combined)

    return total_time, difficulty, makes


def _extract_ingredients(blocks: list[PdfBlock], split: float) -> tuple[list[str], str]:
    started = False
    collecting_tip = False
    ingredient_lines: list[str] = []
    tip_lines: list[str] = []

    for block in blocks:
        if block.page_number != 1 or block.x0 >= split:
            continue
        text = " ".join(block.text.split())
        if not started:
            if text == "INGREDIENTS":
                started = True
            continue
        if text.startswith("TOP TIP"):
            collecting_tip = True
            remainder = text.removeprefix("TOP TIP").strip(" :-")
            if remainder:
                tip_lines.append(remainder)
            continue
        if collecting_tip:
            tip_lines.append(text)
            continue
        ingredient_lines.append(text)

    return _collapse_wrapped_ingredients(ingredient_lines), " ".join(tip_lines).strip()


def _collapse_wrapped_ingredients(lines: list[str]) -> list[str]:
    ingredients: list[str] = []
    for line in lines:
        normalized = " ".join(line.split())
        if not normalized:
            continue
        if not ingredients or INGREDIENT_START_RE.match(normalized):
            ingredients.append(normalized)
            continue
        ingredients[-1] = f"{ingredients[-1]} {normalized}".strip()
    return ingredients


def _extract_method_content(blocks: list[PdfBlock], split: float) -> tuple[list[str], list[str]]:
    started = False
    steps: list[str] = []
    current_step: list[str] = []
    pre_step_lines: list[str] = []
    step_started = False

    for block in blocks:
        if block.page_number == 1 and block.x0 < split:
            continue
        if block.page_number > 1 and block.x0 < split:
            continue

        text = " ".join(block.text.split())
        if not started:
            if text == "METHOD":
                started = True
            continue

        inline_step = INLINE_STEP_RE.match(text)
        if inline_step:
            if current_step:
                steps.append(" ".join(current_step).strip())
            current_step = []
            step_started = True
            continue

        if STEP_NUMBER_RE.match(text):
            if current_step:
                steps.append(" ".join(current_step).strip())
            current_step = []
            step_started = True
            continue

        if step_started:
            current_step.append(text)
        else:
            pre_step_lines.append(text)

    if current_step:
        steps.append(" ".join(current_step).strip())

    return [step for step in steps if step], _collapse_preparation_notes(pre_step_lines)


def _collapse_preparation_notes(lines: list[str]) -> list[str]:
    notes: list[str] = []
    current: list[str] = []

    for line in lines:
        normalized = " ".join(line.split())
        if not normalized:
            continue
        if UPPERCASE_HEADING_RE.match(normalized):
            if current:
                notes.append(" ".join(current).strip())
            current = [_display_text(normalized.rstrip(":"))]
            continue
        current.append(normalized)

    if current:
        notes.append(" ".join(current).strip())

    return [note for note in notes if note]


def _ingredient_record(raw: str) -> dict[str, str | bool | None]:
    lowered = raw.lower()
    return build_ingredient_payload(
        raw=raw,
        normalized_name=_normalize_ingredient_name(raw) or lowered,
        quantity=None,
        unit=None,
        item=None,
        preparation=None,
        optional=lowered.startswith("optional:"),
    )


def _normalize_ingredient_name(raw: str) -> str:
    value = raw.replace("\xa0", " ").strip()
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"^optional:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"^\s*(?:\d+(?:\s+\d+/\d+)?(?:/\d+)?|\d+\s*x\s*\d+|[¼½¾⅓⅔⅛]|a|an|one|two|three|four|five)\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^(?:x\s*\d+\s+|g|kg|ml|l|tbsp|tsp|teaspoon(?:s)?|tablespoon(?:s)?|sachet|sachets|large|medium|small)\b\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = value.split(",", 1)[0].strip()
    value = normalize_ingredient_text(value)
    return value


def _display_text(text: str) -> str:
    compact = " ".join(text.split()).strip()
    return compact.title() if compact.isupper() else compact


def _fallback_title(filename: str, cookbook_title: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    fallback = stem or cookbook_title
    fallback = re.sub(r"\s+\|\s+jamie oliver recipes$", "", fallback, flags=re.IGNORECASE)
    return " ".join(fallback.split()).title()
