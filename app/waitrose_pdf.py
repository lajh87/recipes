from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from app.ingredients import build_ingredient_payload, normalize_ingredient_text

from app.extractor import RecipeDraft

SERVES_RE = re.compile(r"^Serves\s+(.+)$", re.IGNORECASE)
COURSE_RE = re.compile(r"^Course\s+(.+)$", re.IGNORECASE)
PREPARE_RE = re.compile(r"^Prepare\s+(.+)$", re.IGNORECASE)
COOK_RE = re.compile(r"^Cook\s+(.+)$", re.IGNORECASE)
TOTAL_TIME_RE = re.compile(r"^Total time\s+(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class PdfBlock:
    page_number: int
    x0: float
    y0: float
    text: str


@dataclass(frozen=True)
class WaitrosePdfParseResult:
    title: str
    serves: str
    course: str
    prep_time: str
    cook_time: str
    total_time: str
    source_metadata: dict[str, Any]
    drafts: list[RecipeDraft]


def extract_waitrose_pdf(
    *,
    cookbook_title: str,
    filename: str,
    object_key: str,
    content_type: str,
    file_bytes: bytes,
) -> WaitrosePdfParseResult:
    document = fitz.open(stream=file_bytes, filetype="pdf")
    page_count = document.page_count
    try:
        if page_count < 2:
            raise ValueError("upload does not match the Waitrose PDF template: expected a 2-page recipe PDF.")
        pages = [document.load_page(page_number) for page_number in range(page_count)]
        page_one_blocks = _extract_blocks([pages[0]])
        page_two_blocks = _extract_blocks([pages[1]])
        page_two_words = sorted(
            pages[1].get_text("words"),
            key=lambda word: (round(float(word[1]), 1), float(word[0])),
        )
        page_width = pages[1].rect.width
    finally:
        document.close()

    title = _extract_title(page_one_blocks) or _fallback_title(filename, cookbook_title)
    intro = _extract_intro(page_one_blocks)
    serves, course, prep_time, cook_time, total_time = _extract_metadata(page_one_blocks)
    ingredients = _extract_ingredients(page_two_blocks, page_width)
    method_steps = _extract_method_steps(page_two_words, page_two_blocks, page_width)

    if not serves or not course:
        raise ValueError(
            "upload does not match the Waitrose PDF template: expected serves and course metadata on page 1."
        )
    if not prep_time or not cook_time or not total_time:
        raise ValueError(
            "upload does not match the Waitrose PDF template: expected prepare, cook, and total time metadata on page 1."
        )
    if not ingredients:
        raise ValueError(
            "upload does not match the Waitrose PDF template: expected a 2-column Ingredients section on page 2."
        )
    if not method_steps:
        raise ValueError(
            "upload does not match the Waitrose PDF template: expected a 2-column Method section with numbered steps on page 2."
        )

    source_metadata = {
        key: value
        for key, value in {
            "serves": serves,
            "course": course,
            "prep_time": prep_time,
            "cook_time": cook_time,
            "total_time": total_time,
            "intro": intro,
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
            "page_end": 2,
            "anchor": Path(filename).name,
            "excerpt": intro or title,
            "metadata": source_metadata,
        },
        images=[],
        confidence=0.99,
        notes=["Parsed by Waitrose PDF extractor"],
        review_status="verified",
        review_reasons=[],
    )

    return WaitrosePdfParseResult(
        title=title,
        serves=serves,
        course=course,
        prep_time=prep_time,
        cook_time=cook_time,
        total_time=total_time,
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
        if 380 <= block.y0 <= 460:
            return block.text.strip()
    return ""


def _extract_intro(blocks: list[PdfBlock]) -> str:
    lines: list[str] = []
    for block in blocks:
        if 460 <= block.y0 <= 620:
            lines.append(block.text.strip())
    return " ".join(lines).strip()


def _extract_metadata(blocks: list[PdfBlock]) -> tuple[str, str, str, str, str]:
    serves = ""
    course = ""
    prep_time = ""
    cook_time = ""
    total_time = ""
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        if not serves:
            match = SERVES_RE.match(text)
            if match:
                serves = match.group(1).strip()
                continue
        if not course:
            match = COURSE_RE.match(text)
            if match:
                course = match.group(1).strip()
                continue
        if not prep_time:
            match = PREPARE_RE.match(text)
            if match:
                prep_time = match.group(1).strip()
                continue
        if not cook_time:
            match = COOK_RE.match(text)
            if match:
                cook_time = match.group(1).strip()
                continue
        if not total_time:
            match = TOTAL_TIME_RE.match(text)
            if match:
                total_time = match.group(1).strip()
                continue
    return serves, course, prep_time, cook_time, total_time


def _extract_ingredients(blocks: list[PdfBlock], page_width: float) -> list[str]:
    midpoint = page_width / 2
    method_y = next(
        (block.y0 for block in blocks if block.text.strip().lower() == "method"),
        10_000.0,
    )
    lines: list[str] = []
    for block in blocks:
        text = block.text.strip()
        if text.lower() in {"ingredients", "method"}:
            continue
        if block.y0 <= 60 or block.y0 >= method_y:
            continue
        if block.x0 < 20 or block.x0 > page_width:
            continue
        if block.x0 < midpoint or block.x0 >= midpoint:
            lines.append(text)
    return lines


def _extract_method_steps(
    words: list[tuple[Any, ...]],
    blocks: list[PdfBlock],
    page_width: float,
) -> list[str]:
    method_block = next(
        (block for block in blocks if block.text.strip().lower() == "method"),
        None,
    )
    nutrition_y = next(
        (block.y0 for block in blocks if block.text.strip().lower() == "nutritional"),
        10_000.0,
    )
    if not method_block:
        return []

    midpoint = page_width / 2
    columns: dict[int, list[str]] = {1: [], 2: []}
    current_step: int | None = None

    for word in words:
        x0, y0, _x1, _y1, text = float(word[0]), float(word[1]), float(word[2]), float(word[3]), str(word[4])
        if y0 <= method_block.y0 or y0 >= nutrition_y:
            continue
        if x0 < midpoint:
            if text == "1":
                current_step = 1
                continue
            if current_step == 1:
                columns[1].append(text)
        else:
            if text == "2":
                current_step = 2
                continue
            if current_step == 2:
                columns[2].append(text)

    steps = [" ".join(columns[index]).strip() for index in (1, 2)]
    return [step for step in steps if step]


def _ingredient_record(raw: str) -> dict[str, str | bool | None]:
    return build_ingredient_payload(
        raw=raw,
        normalized_name=_normalize_ingredient_name(raw) or raw,
        quantity=None,
        unit=None,
        item=None,
        preparation=None,
        optional=False,
    )


def _normalize_ingredient_name(raw: str) -> str:
    value = raw.replace("\xa0", " ").strip()
    value = re.sub(
        r"^\s*(?:\d+(?:[./]\d+)?|[¼½¾⅓⅔⅛]|handful)\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^(?:g|kg|ml|l|tbsp|tsp|pack|jar|bag)\b\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = value.split(",", 1)[0].strip()
    value = normalize_ingredient_text(value)
    return value


def _fallback_title(filename: str, cookbook_title: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    fallback = stem or cookbook_title
    fallback = re.sub(r"\s+recipe\s+\|\s+waitrose\s*&\s*partners$", "", fallback, flags=re.IGNORECASE)
    return " ".join(fallback.split()).strip().title()
