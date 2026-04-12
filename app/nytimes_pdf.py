from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from app.extractor import CandidateImage, RecipeDraft

INGREDIENT_STRIP_RE = re.compile(
    r"^\s*(?:"
    r"\d+(?:\s+\d+/\d+)?(?:/\d+)?"
    r"|[¼½¾⅓⅔⅛]"
    r"|a|an"
    r")"
    r"(?:\s*(?:to|-)\s*(?:\d+(?:/\d+)?|[¼½¾⅓⅔⅛]))?"
    r"\s*",
    re.IGNORECASE,
)
LEADING_UNIT_RE = re.compile(
    r"^(?:cup|cups|tablespoon|tablespoons|tbsp|teaspoon|teaspoons|tsp|ounce|ounces|oz|"
    r"pound|pounds|lb|lbs|gram|grams|g|kilogram|kilograms|kg|milliliter|milliliters|ml|"
    r"liter|liters|l|quart|quarts|pint|pints|can|cans|clove|cloves|bunch|bunches|small|"
    r"medium|large)\b\s*",
    re.IGNORECASE,
)
STEP_RE = re.compile(r"^Step\s+\d+$", re.IGNORECASE)
EXCLUDED_TEXT_PREFIXES = (
    "Private Notes",
    "Leave a Private Comment",
    "Ratings",
    "Rating (",
    "What would you rate this recipe",
    "Be the first to rate this recipe",
    "Have you cooked this",
)
SKIP_LEFT_TEXT = {
    "INGREDIENTS",
}


@dataclass(frozen=True)
class PdfBlock:
    page_number: int
    x0: float
    y0: float
    text: str


@dataclass(frozen=True)
class NytimesPdfParseResult:
    title: str
    author: str
    published_at: str
    yield_text: str
    intro: str
    source_metadata: dict[str, Any]
    drafts: list[RecipeDraft]


def extract_nytimes_pdf(
    *,
    cookbook_title: str,
    filename: str,
    object_key: str,
    content_type: str,
    file_bytes: bytes,
) -> NytimesPdfParseResult:
    document = fitz.open(stream=file_bytes, filetype="pdf")
    page_count = document.page_count
    try:
        pages = [document.load_page(page_number) for page_number in range(page_count)]
        blocks = _extract_blocks(pages)
        page_width = pages[0].rect.width if pages else 0.0
        split = _column_split(blocks, page_width)
        title = _extract_title(blocks) or _fallback_title(filename, cookbook_title)
        author = _extract_author(blocks)
        published_at, updated_at = _extract_publication_details(blocks)
        header_details = _extract_header_details(blocks)
        intro_lines = _extract_intro_lines(blocks, split)
        intro = "\n\n".join(intro_lines).strip()
        yield_text, ingredients, ingredient_sections = _extract_ingredients(blocks, split)
        method_steps, preparation_notes, preparation_sections = _extract_method_content(blocks, split)
        images = _extract_images(document, pages)
    finally:
        document.close()

    if not ingredients or not method_steps:
        raise ValueError("NYTimes PDF parser could not find both ingredients and preparation steps.")

    source_metadata = {
        key: value
        for key, value in {
            "author": author,
            "published_at": published_at,
            "updated_at": updated_at,
            "total_time": header_details.get("total_time", ""),
            "prep_time": header_details.get("prep_time", ""),
            "cook_time": header_details.get("cook_time", ""),
            "yield": yield_text.removeprefix("Yield:").strip() if yield_text else "",
            "intro": intro,
            "preparation_notes": preparation_notes,
            "supplemental_sections": ingredient_sections + preparation_sections,
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
            "excerpt": intro or title,
            "metadata": source_metadata,
        },
        images=images,
        confidence=0.99,
        notes=[
            note
            for note in [
                yield_text,
                f"Author: {author}" if author else "",
                f"Published: {published_at}" if published_at else "",
                f"Updated: {updated_at}" if updated_at else "",
                "Parsed by NYTimes PDF extractor",
            ]
            if note
        ],
        review_status="verified",
        review_reasons=[],
    )
    return NytimesPdfParseResult(
        title=title,
        author=author,
        published_at=published_at,
        yield_text=yield_text,
        intro=intro,
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
        if block.page_number == 1 and block.y0 < 95 and not block.text.startswith(("By ", "Published ", "Updated ")):
            return block.text
    return ""


def _extract_author(blocks: list[PdfBlock]) -> str:
    for block in blocks:
        if block.page_number == 1 and block.text.startswith("By "):
            return block.text.removeprefix("By ").strip()
    return ""


def _extract_publication_details(blocks: list[PdfBlock]) -> tuple[str, str]:
    published_at = ""
    updated_at = ""
    for block in blocks:
        if block.page_number != 1:
            continue
        if block.text.startswith("Published "):
            published_at = block.text.removeprefix("Published ").strip()
        elif block.text.startswith("Updated "):
            updated_at = block.text.removeprefix("Updated ").strip()
    return published_at or updated_at, updated_at


def _extract_intro_lines(blocks: list[PdfBlock], split: float) -> list[str]:
    lines: list[str] = []
    for block in blocks:
        if block.page_number != 1 or block.x0 >= split:
            continue
        if block.text == "INGREDIENTS":
            break
        if block.y0 < 145:
            continue
        if any(
            block.text.startswith(prefix)
            for prefix in ("Total Time ", "Prep Time ", "Cook Time ", "Yield:")
        ):
            continue
        if block.text.startswith(("By ", "Published ", "Updated ")):
            continue
        if _is_excluded_text(block.text):
            continue
        lines.append(block.text)
    return _dedupe_lines(lines)


def _extract_ingredients(
    blocks: list[PdfBlock],
    split: float,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    started = False
    yield_text = ""
    ingredients: list[str] = []
    supplemental_sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None

    for block in blocks:
        if block.x0 >= split:
            continue
        if not started:
            if block.text == "INGREDIENTS":
                started = True
            continue
        if block.text in SKIP_LEFT_TEXT:
            continue
        if _is_excluded_text(block.text):
            continue
        if block.text.startswith("Yield:"):
            yield_text = block.text
            continue
        if _is_section_heading(block.text):
            if ingredients:
                current_section = {
                    "heading": _display_heading(block.text),
                    "lines": [],
                }
                supplemental_sections.append(current_section)
                continue
        if current_section is None:
            ingredients.append(block.text)
        else:
            current_section["lines"].append(block.text)

    return yield_text, _dedupe_lines(ingredients), _clean_sections(supplemental_sections)


def _extract_method_content(
    blocks: list[PdfBlock],
    split: float,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    started = False
    steps: list[str] = []
    current: list[str] = []
    preparation_notes: list[str] = []
    supplemental_sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    step_started = False

    for block in blocks:
        if block.x0 < split:
            continue
        if not started:
            if block.text == "PREPARATION":
                started = True
            continue
        if _is_excluded_text(block.text):
            continue
        if STEP_RE.match(block.text):
            if current:
                steps.append(" ".join(current).strip())
            current = []
            current_section = None
            step_started = True
            continue
        if _is_section_heading(block.text):
            if current:
                steps.append(" ".join(current).strip())
                current = []
            current_section = {
                "heading": _display_heading(block.text),
                "lines": [],
            }
            supplemental_sections.append(current_section)
            step_started = False
            continue
        if current_section is not None:
            current_section["lines"].append(block.text)
            continue
        if step_started:
            current.append(block.text)
        else:
            preparation_notes.append(block.text)

    if current:
        steps.append(" ".join(current).strip())
    return (
        [step for step in steps if step],
        _dedupe_lines(preparation_notes),
        _clean_sections(supplemental_sections),
    )


def _is_section_heading(text: str) -> bool:
    letters = re.sub(r"[^A-Za-z]+", "", text)
    if not letters:
        return False
    return len(text.split()) <= 8 and letters == letters.upper()


def _is_excluded_text(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in EXCLUDED_TEXT_PREFIXES)


def _column_split(blocks: list[PdfBlock], page_width: float) -> float:
    ingredient_heading = next(
        (block for block in blocks if block.page_number == 1 and block.text == "INGREDIENTS"),
        None,
    )
    preparation_heading = next(
        (block for block in blocks if block.page_number == 1 and block.text == "PREPARATION"),
        None,
    )
    if ingredient_heading and preparation_heading and preparation_heading.x0 > ingredient_heading.x0:
        return (ingredient_heading.x0 + preparation_heading.x0) / 2
    return page_width * 0.38 if page_width else 220.0


def _extract_header_details(blocks: list[PdfBlock]) -> dict[str, str]:
    header_details: dict[str, str] = {}
    prefixes = {
        "Total Time ": "total_time",
        "Prep Time ": "prep_time",
        "Cook Time ": "cook_time",
    }
    for block in blocks:
        if block.page_number != 1:
            continue
        for prefix, field in prefixes.items():
            if block.text.startswith(prefix):
                header_details[field] = block.text.removeprefix(prefix).strip()
    return header_details


def _display_heading(text: str) -> str:
    return " ".join(text.split()).title()


def _dedupe_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = " ".join(line.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def _clean_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for section in sections:
        lines = _dedupe_lines([str(line) for line in section.get("lines", [])])
        if not lines:
            continue
        heading = " ".join(str(section.get("heading", "")).split()) or "Additional Details"
        cleaned.append({"heading": heading, "lines": lines})
    return cleaned


def _extract_images(document: fitz.Document, pages: list[fitz.Page]) -> list[CandidateImage]:
    images: list[CandidateImage] = []
    seen_xrefs: set[int] = set()
    for page_number, page in enumerate(pages, start=1):
        for image_info in page.get_images(full=True):
            xref = image_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            extracted = document.extract_image(xref)
            if not extracted:
                continue
            width = int(extracted.get("width") or 0)
            height = int(extracted.get("height") or 0)
            image_bytes = extracted.get("image") or b""
            if width < 180 or height < 180 or len(image_bytes) < 10_000:
                continue
            ext = extracted.get("ext", "bin")
            images.append(
                CandidateImage(
                    filename=f"page-{page_number}-image-{xref}.{ext}",
                    content_type=mimetypes.guess_type(f"x.{ext}")[0] or "application/octet-stream",
                    data=image_bytes,
                    source_ref=f"page-{page_number}",
                )
            )
    return images


def _ingredient_record(raw: str) -> dict[str, str | bool | None]:
    normalized_name = _normalize_ingredient_name(raw)
    return {
        "raw": raw,
        "normalized_name": normalized_name or raw.lower(),
        "quantity": None,
        "unit": None,
        "item": None,
        "preparation": None,
        "optional": False,
    }


def _normalize_ingredient_name(raw: str) -> str:
    value = raw.replace("\xa0", " ").strip()
    value = re.sub(r"\([^)]*\)", "", value)
    value = INGREDIENT_STRIP_RE.sub("", value)
    value = LEADING_UNIT_RE.sub("", value)
    value = re.sub(r"^(?:of\s+)", "", value, flags=re.IGNORECASE)
    value = value.split(",", 1)[0].strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def _fallback_title(filename: str, cookbook_title: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    fallback = stem or cookbook_title
    fallback = re.sub(r"\s+recipe$", "", fallback, flags=re.IGNORECASE)
    return " ".join(fallback.split())
