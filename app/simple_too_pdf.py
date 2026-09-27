from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

import fitz

from app.extractor import CandidateImage, RecipeDraft
from app.ingredients import build_ingredient_payload


STEP_RE = re.compile(r"^\s*\d+\.\s*(.+)", re.DOTALL)
QUANTITY_RE = re.compile(r"^(?P<quantity>[\d¼½¾⅓⅔⅛]+(?:[–-][\d¼½¾⅓⅔⅛]+)?)\s*(?P<unit>[A-Za-z]+)?$")


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _ingredient(raw: str) -> dict[str, Any]:
    amount, separator, item = raw.partition(" - ")
    name = (item if separator else raw).split(",", 1)[0].strip()
    match = QUANTITY_RE.fullmatch(amount.strip()) if separator else None
    return build_ingredient_payload(
        raw=raw,
        normalized_name=name,
        quantity=match.group("quantity") if match else None,
        unit=match.group("unit") if match else None,
        item=item.strip() if separator else raw,
        preparation=None,
        optional="(optional)" in raw.casefold(),
    )


def extract_simple_too_pdf(
    *,
    title: str,
    chapter: str,
    book_page: int,
    filename: str,
    object_key: str,
    file_bytes: bytes,
    source_page_start: int,
    fallback_method_steps: list[str] | None = None,
    fallback_method_url: str | None = None,
) -> RecipeDraft:
    document = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        if not document.page_count:
            raise ValueError(f"Empty PDF: {filename}")

        blocks = [
            (page_number, block)
            for page_number, page in enumerate(document)
            for block in page.get_text("blocks")
            if block[4].strip()
        ]
        first_page_blocks = [block for page_number, block in blocks if page_number == 0]
        if not any("SIMPLE TOO" in block[4] for block in first_page_blocks):
            raise ValueError(f"Not a Simple Too recipe PDF: {filename}")

        about_heading = next(
            (block[1] for block in first_page_blocks if block[0] >= 280 and _clean(block[4]) == "ABOUT"),
            None,
        )
        method_heading = next(
            (block[1] for block in first_page_blocks if block[0] >= 280 and _clean(block[4]) == "METHOD"),
            None,
        )
        if method_heading is None:
            raise ValueError(f"Method heading missing: {filename}")

        ingredients: list[dict[str, Any]] = []
        for page_number, block in blocks:
            if page_number != 0 or not 50 <= block[0] < 280 or block[1] < 390:
                continue
            raw = _clean(block[4])
            if raw == "NOTES":
                break
            letters = re.sub(r"[^A-Za-z]", "", raw)
            if letters and letters.isupper() and len(raw.split()) <= 6:
                continue
            ingredients.append(_ingredient(raw))

        intro = "\n\n".join(
            _clean(block[4])
            for block in first_page_blocks
            if block[0] >= 280
            and about_heading is not None
            and about_heading < block[1] < method_heading
            and _clean(block[4]) not in {"ABOUT", "METHOD"}
        )
        method_steps = [
            _clean(match.group(1))
            for page_number, block in blocks
            if block[0] >= 280 and (page_number > 0 or block[1] > method_heading)
            if (match := STEP_RE.match(block[4]))
        ]
        if not method_steps and fallback_method_steps:
            method_steps = fallback_method_steps
        if not ingredients or not method_steps:
            raise ValueError(f"Ingredients or method missing: {filename}")

        timing = next(
            (_clean(block[4]) for block in first_page_blocks if "PREP TIME:" in block[4]),
            "",
        )
        metadata: dict[str, Any] = {
            "book_page": book_page,
            "original_pdf_filename": filename,
            "intro": intro,
        }
        for part in timing.split("|"):
            part = part.strip()
            for prefix, key in (("SERVES ", "serves"), ("MAKES ", "makes"), ("PREP TIME: ", "prep_time"), ("COOK TIME: ", "cook_time")):
                if part.startswith(prefix):
                    metadata[key] = part.removeprefix(prefix).strip()
        if fallback_method_url and fallback_method_steps:
            metadata["method_source_url"] = fallback_method_url
            metadata["preparation_notes"] = ["Method supplied from the attributed online guest recipe because the PDF omits it."]

        images: list[CandidateImage] = []
        first_page = document[0]
        for image_info in first_page.get_images(full=True):
            xref = image_info[0]
            positions = first_page.get_image_rects(xref)
            if not any(50 <= position.x0 < 280 and position.y0 < 500 for position in positions):
                continue
            extracted = document.extract_image(xref)
            if not extracted or int(extracted.get("width") or 0) < 180 or int(extracted.get("height") or 0) < 180:
                continue
            image_bytes = extracted.get("image") or b""
            if len(image_bytes) < 10_000:
                continue
            extension = extracted.get("ext", "bin")
            images.append(
                CandidateImage(
                    filename=f"{Path(filename).stem}-photo.{extension}",
                    content_type=mimetypes.guess_type(f"x.{extension}")[0] or "application/octet-stream",
                    data=image_bytes,
                    source_ref=f"{filename}#page=1",
                )
            )
            break

        return RecipeDraft(
            title=title,
            ingredients=ingredients,
            method_steps=method_steps,
            source={
                "object_key": object_key,
                "format": "pdf",
                "chapter_title": chapter,
                "page_start": source_page_start,
                "page_end": source_page_start + document.page_count - 1,
                "anchor": filename,
                "excerpt": intro or title,
                "metadata": metadata,
            },
            images=images,
            confidence=0.99,
            notes=["Parsed from the Simple Too PDF"],
            review_status="verified",
            review_reasons=[],
        )
    finally:
        document.close()
