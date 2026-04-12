from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from app.extractor import RecipeDraft

STEP_RE = re.compile(r"^Step\s+(?P<number>\d+)$", re.IGNORECASE)
MAKES_DIFFICULTY_RE = re.compile(r"^(?P<makes>Makes\s+.+?)\s+(?P<difficulty>[A-Z][A-Za-z -]+)$")
PREP_COOK_RE = re.compile(r"^Prep:\s*(?P<prep>.+?)\s+Cook:\s*(?P<cook>.+)$", re.IGNORECASE)
INGREDIENT_START_RE = re.compile(
    r"^(?:\d|\d+\s*/\s*\d+|[¼½¾⅓⅔⅛]|pinch\b|few\b|small\b|medium\b|large\b)",
    re.IGNORECASE,
)
METADATA_EXCLUDE = {"Ingredients", "Method", "Try our app"}
TAG_EXCLUDE = {"Try our app"}


@dataclass(frozen=True)
class PdfBlock:
    page_number: int
    x0: float
    y0: float
    text: str


@dataclass(frozen=True)
class BbcGoodFoodPdfParseResult:
    title: str
    author: str
    makes: str
    difficulty: str
    prep_time: str
    cook_time: str
    source_metadata: dict[str, Any]
    drafts: list[RecipeDraft]


def extract_bbc_goodfood_pdf(
    *,
    cookbook_title: str,
    filename: str,
    object_key: str,
    content_type: str,
    file_bytes: bytes,
) -> BbcGoodFoodPdfParseResult:
    document = fitz.open(stream=file_bytes, filetype="pdf")
    page_count = document.page_count
    try:
        if page_count == 0:
            raise ValueError("upload is empty or unreadable as a BBC Good Food recipe PDF template.")
        pages = [document.load_page(page_number) for page_number in range(page_count)]
        blocks = _extract_blocks(pages)
    finally:
        document.close()

    title = _extract_title(blocks) or _fallback_title(filename, cookbook_title)
    author = _extract_author(blocks)
    makes, difficulty = _extract_makes_and_difficulty(blocks)
    prep_time, cook_time, extra_timing = _extract_timing(blocks)
    intro = _extract_intro(blocks)
    tags = _extract_tags(blocks)
    ingredients = _extract_ingredients(blocks)
    method_steps = _extract_method_steps(blocks)

    if not author:
        raise ValueError(
            "upload does not match the BBC Good Food PDF template: expected an author line beneath the recipe title."
        )
    if not makes or not difficulty:
        raise ValueError(
            "upload does not match the BBC Good Food PDF template: expected makes and difficulty metadata."
        )
    if not prep_time or not cook_time:
        raise ValueError(
            "upload does not match the BBC Good Food PDF template: expected prep and cook timing metadata."
        )
    if not ingredients:
        raise ValueError(
            "upload does not match the BBC Good Food PDF template: expected an ingredients section."
        )
    if not method_steps:
        raise ValueError(
            "upload does not match the BBC Good Food PDF template: expected a Method section with Step headings."
        )

    preparation_notes = [extra_timing] if extra_timing else []
    source_metadata = {
        key: value
        for key, value in {
            "author": author,
            "makes": makes,
            "difficulty": difficulty,
            "prep_time": prep_time,
            "cook_time": cook_time,
            "intro": intro,
            "preparation_notes": preparation_notes,
            "tags": tags,
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
            "page_end": max(1, page_count),
            "anchor": Path(filename).name,
            "excerpt": intro or title,
            "metadata": source_metadata,
        },
        images=[],
        confidence=0.99,
        notes=["Parsed by BBC Good Food PDF extractor"],
        review_status="verified",
        review_reasons=[],
    )
    return BbcGoodFoodPdfParseResult(
        title=title,
        author=author,
        makes=makes,
        difficulty=difficulty,
        prep_time=prep_time,
        cook_time=cook_time,
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
        if block.page_number == 1 and block.y0 < 120:
            return block.text.strip()
    return ""


def _extract_author(blocks: list[PdfBlock]) -> str:
    title = _extract_title(blocks)
    for block in blocks:
        if block.page_number == 1 and 120 <= block.y0 < 145 and block.text.strip() != title:
            return block.text.strip()
    return ""


def _extract_makes_and_difficulty(blocks: list[PdfBlock]) -> tuple[str, str]:
    for block in blocks:
        if block.page_number != 1:
            continue
        match = MAKES_DIFFICULTY_RE.match(block.text.strip())
        if match:
            makes = match.group("makes").removeprefix("Makes").strip()
            difficulty = match.group("difficulty").strip()
            return makes, difficulty
    return "", ""


def _extract_timing(blocks: list[PdfBlock]) -> tuple[str, str, str]:
    prep_time = ""
    cook_time = ""
    extra_timing = ""
    for block in blocks:
        if block.page_number != 1:
            continue
        match = PREP_COOK_RE.match(block.text.strip())
        if match:
            prep_time = match.group("prep").strip()
            cook_time = match.group("cook").strip()
            continue
        if prep_time and not extra_timing and block.y0 < 220:
            text = block.text.strip()
            if text and text not in METADATA_EXCLUDE:
                extra_timing = text
                break
    return prep_time, cook_time, extra_timing


def _extract_intro(blocks: list[PdfBlock]) -> str:
    lines: list[str] = []
    for block in blocks:
        if block.page_number != 1:
            continue
        if 215 <= block.y0 < 262:
            text = block.text.strip()
            if text and text not in METADATA_EXCLUDE:
                lines.append(text)
    return " ".join(lines).strip()


def _extract_tags(blocks: list[PdfBlock]) -> list[str]:
    tags: list[str] = []
    for block in blocks:
        if block.page_number != 1:
            continue
        if 262 <= block.y0 < 305:
            for part in re.split(r"\s{2,}", block.text.strip()):
                cleaned = part.strip()
                if cleaned and cleaned not in TAG_EXCLUDE:
                    tags.append(cleaned)
    return tags


def _extract_ingredients(blocks: list[PdfBlock]) -> list[str]:
    ingredient_lines: list[str] = []
    method_y = next(
        (
            block.y0
            for block in blocks
            if block.page_number == 1 and block.text.strip().lower() == "method"
        ),
        10_000.0,
    )
    for block in blocks:
        if block.page_number != 1:
            continue
        if not (380 <= block.y0 < method_y):
            continue
        text = block.text.strip()
        if not text or text in METADATA_EXCLUDE:
            continue
        ingredient_lines.append(text)
    return _collapse_ingredients(ingredient_lines)


def _collapse_ingredients(lines: list[str]) -> list[str]:
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


def _extract_method_steps(blocks: list[PdfBlock]) -> list[str]:
    method_heading = next(
        (
            block
            for block in blocks
            if block.page_number == 1 and block.text.strip().lower() == "method"
        ),
        None,
    )
    if not method_heading:
        return []

    steps: list[str] = []
    current: list[str] = []
    started = False
    for block in blocks:
        if block.page_number < method_heading.page_number:
            continue
        if block.page_number == method_heading.page_number and block.y0 <= method_heading.y0:
            continue
        text = block.text.strip()
        if not text:
            continue
        if STEP_RE.match(text):
            if current:
                steps.append(" ".join(current).strip())
            current = []
            started = True
            continue
        if started:
            current.append(text)

    if current:
        steps.append(" ".join(current).strip())
    return [step for step in steps if step]


def _ingredient_record(raw: str) -> dict[str, str | bool | None]:
    return {
        "raw": raw,
        "normalized_name": _normalize_ingredient_name(raw) or raw.lower(),
        "quantity": None,
        "unit": None,
        "item": None,
        "preparation": None,
        "optional": False,
    }


def _normalize_ingredient_name(raw: str) -> str:
    value = raw.replace("\xa0", " ").strip().lower()
    value = re.sub(
        r"^\s*(?:\d+(?:\s*/\s*\d+)?|[¼½¾⅓⅔⅛]|pinch|few|small|medium|large)\s*",
        "",
        value,
    )
    value = re.sub(
        r"^(?:g|kg|ml|l|tbsp|tsp|tablespoon(?:s)?|teaspoon(?:s)?|plain)\b\s*",
        "",
        value,
    )
    value = value.split(",", 1)[0].strip()
    value = re.sub(r"\s+", " ", value)
    return value


def _fallback_title(filename: str, cookbook_title: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    fallback = stem or cookbook_title
    fallback = re.sub(r"\s+\|\s+good food$", "", fallback, flags=re.IGNORECASE)
    return " ".join(fallback.split()).strip().title()
