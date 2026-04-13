from __future__ import annotations

import json
import logging
import mimetypes
import posixpath
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
from bs4 import BeautifulSoup
from ebooklib import epub, ITEM_DOCUMENT, ITEM_IMAGE
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.epub import extract_epub_table_of_contents, normalize_epub_path
from app.ingredients import build_ingredient_payload, normalize_ingredient_text, prepare_ingredient_mapping
from app.models import CookbookTocEntry

logger = logging.getLogger(__name__)

INGREDIENT_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s+)?(?:\d+(?:[./]\d+)?(?:g|kg|ml|l|oz|lb|lbs|cm)?|\d+/\d+|[¼½¾⅓⅔⅛]|one|two|three|four|five)\b",
    re.IGNORECASE,
)
MEASUREMENT_RE = re.compile(
    r"(?:\b|\d)(cup|cups|tbsp|tsp|teaspoon|teaspoons|tablespoon|tablespoons|g|kg|ml|l|oz|lb|lbs)\b",
    re.IGNORECASE,
)
METHOD_CUES = ("method", "directions", "steps", "instructions")
RECIPE_CUES = ("ingredients", "serves", "yield", "prep", "cook time", "makes")
RECIPE_TITLE_CLASSES = {"rt"}
RECIPE_CATEGORY_CLASSES = {"ct"}
RECIPE_TEXT_CLASSES = {
    "rt",
    "rst",
    "rhn",
    "rh",
    "rsh",
    "ri",
    "ris",
    "rih-top",
    "rd-top",
    "rd",
    "rds",
    "bh",
    "btni",
}
COOKS_BRITAIN_TITLE_CLASSES = {"C287"}
COOKS_BRITAIN_TEXT_CLASSES = {"C287", "C290", "C297", "C299", "C300", "C306"}
COOKS_BRITAIN_INGREDIENT_CLASSES = {"C300"}
COOKS_BRITAIN_METHOD_CLASSES = {"C306"}
ROAD_TO_MEXICO_TITLE_CLASSES = {"recipe-title", "recipe-title1"}
ROAD_TO_MEXICO_CATEGORY_CLASSES = {"recipe-head-translation"}
ROAD_TO_MEXICO_TEXT_CLASSES = {
    "caption",
    "ingredients",
    "ingredients-head",
    "method",
    "method-head",
    "method1",
    "recipe-intro",
    "recipe-title",
    "serves",
    "serves1",
}
ROAD_TO_MEXICO_INGREDIENT_CLASSES = {"ingredients"}
ROAD_TO_MEXICO_METHOD_CLASSES = {"method", "method1"}
OTTOLENGHI_TITLE_CLASSES = {"recipe-title", "recipe-title_new"}
OTTOLENGHI_TEXT_CLASSES = {
    "ingredients",
    "intro",
    "intro_new",
    "method",
    "method1",
    "recipe-title",
    "recipe-title_new",
    "serves",
    "serves-subhead",
}
OTTOLENGHI_INGREDIENT_CLASSES = {"ingredients"}
OTTOLENGHI_METHOD_CLASSES = {"method", "method1"}
NON_RECIPE_SECTION_TITLES = {
    "about the author",
    "about the authors",
    "about the book",
    "acknowledgements",
    "a short note about ingredients",
    "contents",
    "copyright",
    "dedication",
    "foreword",
    "how to use this book",
    "index",
    "introduction",
    "ottolenghi",
    "praise for salt fat acid heat",
    "recipe list",
    "table of contents",
    "thank you",
    "our histories",
    "our shared history",
}
NON_RECIPE_PATH_HINTS = (
    "about_",
    "acknowledg",
    "contents",
    "copyright",
    "dedication",
    "foreword",
    "intro",
    "our_histor",
    "ottolenghi",
    "title",
    "toc",
)
MODEL_SECTION_TEXT_LIMITS = (18000, 12000, 8000)
TITLE_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
GENERIC_METHOD_STEP_RE = re.compile(r"^(?:(?:step)\s*\d+|\d+[.)])\s*", re.IGNORECASE)
LEADING_QUANTITY_RE = re.compile(
    r"^\s*(?:about\s+)?(?:(?:\d+\s*[./-]?\s*\d*)|(?:\d+\s*x\s*\d+)|[¼½¾⅓⅔⅛]+)\s*",
    re.IGNORECASE,
)
LEADING_UNIT_RE = re.compile(
    r"^(?:g|kg|ml|l|oz|lb|lbs|tbsp|tsp|teaspoon(?:s)?|tablespoon(?:s)?|clove(?:s)?|"
    r"sprig(?:s)?|bunch(?:es)?|handful(?:s)?|medium|small|large)\b\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EpubRecipeParagraphProfile:
    title_classes: frozenset[str]
    ingredient_classes: frozenset[str]
    method_classes: frozenset[str]
    text_classes: frozenset[str]
    category_classes: frozenset[str] = frozenset()
    intro_classes: frozenset[str] = frozenset()
    metadata_classes: frozenset[str] = frozenset()
    supplemental_heading_classes: frozenset[str] = frozenset()
    signature_classes: frozenset[str] = frozenset()
    fallback_title_classes: frozenset[str] = frozenset()
    ignored_image_classes: frozenset[str] = frozenset()
    defer_images_after_method: bool = False
    retain_buffered_images_after_category: bool = True
    allow_plain_text_method: bool = False
    min_title_nodes: int = 2


EPUB_RECIPE_PARAGRAPH_PROFILES = (
    EpubRecipeParagraphProfile(
        title_classes=frozenset(RECIPE_TITLE_CLASSES),
        ingredient_classes=frozenset({"ri", "ris"}),
        method_classes=frozenset({"rd-top", "rd", "rds"}),
        text_classes=frozenset(RECIPE_TEXT_CLASSES),
        category_classes=frozenset(RECIPE_CATEGORY_CLASSES),
        min_title_nodes=2,
    ),
    EpubRecipeParagraphProfile(
        title_classes=frozenset(COOKS_BRITAIN_TITLE_CLASSES),
        ingredient_classes=frozenset(COOKS_BRITAIN_INGREDIENT_CLASSES),
        method_classes=frozenset(COOKS_BRITAIN_METHOD_CLASSES),
        text_classes=frozenset(COOKS_BRITAIN_TEXT_CLASSES),
        min_title_nodes=2,
    ),
    EpubRecipeParagraphProfile(
        title_classes=frozenset(ROAD_TO_MEXICO_TITLE_CLASSES),
        ingredient_classes=frozenset(ROAD_TO_MEXICO_INGREDIENT_CLASSES),
        method_classes=frozenset(ROAD_TO_MEXICO_METHOD_CLASSES),
        text_classes=frozenset(ROAD_TO_MEXICO_TEXT_CLASSES),
        category_classes=frozenset(ROAD_TO_MEXICO_CATEGORY_CLASSES),
        intro_classes=frozenset({"recipe-intro"}),
        metadata_classes=frozenset({"serves", "serves1"}),
        signature_classes=frozenset({"recipe-intro", "recipe-head-translation", "serves1"}),
        min_title_nodes=2,
    ),
    EpubRecipeParagraphProfile(
        title_classes=frozenset(OTTOLENGHI_TITLE_CLASSES),
        ingredient_classes=frozenset(OTTOLENGHI_INGREDIENT_CLASSES),
        method_classes=frozenset(OTTOLENGHI_METHOD_CLASSES),
        text_classes=frozenset(OTTOLENGHI_TEXT_CLASSES),
        intro_classes=frozenset({"intro", "intro_new"}),
        metadata_classes=frozenset({"serves"}),
        supplemental_heading_classes=frozenset({"serves-subhead"}),
        signature_classes=frozenset({"intro", "serves-subhead"}),
        min_title_nodes=2,
    ),
    EpubRecipeParagraphProfile(
        title_classes=frozenset({"rt"}),
        ingredient_classes=frozenset({"ril", "rilf"}),
        method_classes=frozenset({"rp", "rpf"}),
        text_classes=frozenset({"rhn", "ril", "rilf", "rp", "rpf", "rt-alt", "ry"}),
        category_classes=frozenset({"ct"}),
        intro_classes=frozenset({"rhn"}),
        metadata_classes=frozenset({"ry"}),
        supplemental_heading_classes=frozenset({"rilh", "rilhf"}),
        signature_classes=frozenset({"rilf", "rpf", "ry"}),
        retain_buffered_images_after_category=False,
        min_title_nodes=2,
    ),
    EpubRecipeParagraphProfile(
        title_classes=frozenset({"rec_head"}),
        ingredient_classes=frozenset({"ingredient_list"}),
        method_classes=frozenset({"method"}),
        text_classes=frozenset(
            {
                "ingredient_list",
                "mc_intro",
                "mc_intro1",
                "mc_intro2",
                "mc_intro3",
                "method",
            }
        ),
        intro_classes=frozenset({"mc_intro", "mc_intro1", "mc_intro2", "mc_intro3"}),
        supplemental_heading_classes=frozenset({"ing_head", "ing_head1"}),
        signature_classes=frozenset({"ingredient_list", "method", "rec_head"}),
        ignored_image_classes=frozenset({"inline_image_2"}),
        defer_images_after_method=True,
        min_title_nodes=1,
    ),
    EpubRecipeParagraphProfile(
        title_classes=frozenset({"rec_head", "rec_headbk"}),
        ingredient_classes=frozenset({"ingred"}),
        method_classes=frozenset({"method"}),
        text_classes=frozenset({"ingred", "method", "rec_intro"}),
        intro_classes=frozenset({"rec_intro"}),
        metadata_classes=frozenset({"prep_time", "prep_time1", "serves"}),
        signature_classes=frozenset({"ingred", "method", "rec_head"}),
        min_title_nodes=1,
    ),
    EpubRecipeParagraphProfile(
        title_classes=frozenset({"recipe_header"}),
        ingredient_classes=frozenset({"ingred"}),
        method_classes=frozenset({"indented"}),
        text_classes=frozenset(
            {
                "center",
                "ingred",
                "indented",
                "serving_size",
                "serving_size1",
            }
        ),
        fallback_title_classes=frozenset({"image_caption"}),
        allow_plain_text_method=True,
        min_title_nodes=1,
    ),
)


class ExtractedIngredient(BaseModel):
    raw: str
    normalized_name: str
    quantity: str | None = None
    unit: str | None = None
    item: str | None = None
    preparation: str | None = None
    optional: bool = False


class ExtractedSupplementalSection(BaseModel):
    heading: str = ""
    lines: list[str] = Field(default_factory=list)


class RecipeExtractionPayload(BaseModel):
    is_recipe: bool
    title: str = ""
    confidence: float = Field(ge=0, le=1, default=0)
    ingredients: list[ExtractedIngredient] = Field(default_factory=list)
    method_steps: list[str] = Field(default_factory=list)
    intro: str = ""
    serves: str = ""
    makes: str = ""
    yield_value: str = ""
    prep_time: str = ""
    cook_time: str = ""
    total_time: str = ""
    preparation_notes: list[str] = Field(default_factory=list)
    supplemental_sections: list[ExtractedSupplementalSection] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


@dataclass
class CandidateImage:
    filename: str
    content_type: str
    data: bytes
    source_ref: str


@dataclass
class CandidateSection:
    source_format: str
    section_key: str
    text: str
    excerpt: str
    chapter_title: str | None = None
    anchor: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    images: list[CandidateImage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    ingredient_lines: list[str] = field(default_factory=list)
    method_lines: list[str] = field(default_factory=list)


@dataclass
class CookbookExtractionResult:
    recipes: list[RecipeDraft]
    table_of_contents: list[CookbookTocEntry]


@dataclass
class RecipeDraft:
    title: str
    ingredients: list[dict[str, Any]]
    method_steps: list[str]
    source: dict[str, Any]
    images: list[CandidateImage]
    confidence: float
    notes: list[str]
    review_status: str
    review_reasons: list[str]

    def embedding_text(self) -> str:
        ingredient_names = ", ".join(
            ingredient.get("canonical_name")
            or ingredient.get("normalized_name")
            or ingredient.get("raw", "")
            for ingredient in self.ingredients
        )
        method = " ".join(self.method_steps)
        return f"{self.title}\nIngredients: {ingredient_names}\nMethod: {method}".strip()


class OpenAIRecipeExtractor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for extraction.")

        client_kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            client_kwargs["base_url"] = settings.openai_base_url
        self.client = OpenAI(**client_kwargs)

    def extract_cookbook(
        self,
        *,
        cookbook_title: str,
        filename: str,
        object_key: str,
        content_type: str,
        file_bytes: bytes,
    ) -> CookbookExtractionResult:
        suffix = Path(filename).suffix.lower()
        if suffix == ".epub":
            sections = self._extract_epub_sections(file_bytes)
        elif suffix == ".pdf":
            sections = self._extract_pdf_sections(file_bytes)
        else:
            raise ValueError(f"Extraction is not implemented for '{suffix or 'unknown'}' files.")

        recipes: list[RecipeDraft] = []
        for section in sections:
            if not self._is_recipe_candidate(section):
                continue

            try:
                payload = self._build_deterministic_recipe_payload(section) or self._extract_recipe_payload(
                    cookbook_title,
                    section,
                )
            except Exception as exc:
                logger.warning(
                    "Skipping section %s in %s after extraction error: %s",
                    section.section_key,
                    cookbook_title,
                    exc,
                )
                continue
            if not payload.is_recipe:
                continue

            review_status, review_reasons = self._review_flags(payload, section)
            source_metadata = self._merge_source_metadata(section.metadata, payload)
            recipes.append(
                RecipeDraft(
                    title=payload.title.strip() or (section.chapter_title or "Untitled Recipe"),
                    ingredients=[
                        prepare_ingredient_mapping(ingredient.model_dump())
                        for ingredient in payload.ingredients
                        if ingredient.raw.strip()
                    ],
                    method_steps=[step.strip() for step in payload.method_steps if step.strip()],
                    source={
                        "object_key": object_key,
                        "format": section.source_format,
                        "chapter_title": section.chapter_title,
                        "page_start": section.page_start,
                        "page_end": section.page_end,
                        "anchor": section.anchor or section.section_key,
                        "excerpt": section.excerpt,
                        "metadata": source_metadata,
                    },
                    images=section.images[:4],
                    confidence=payload.confidence,
                    notes=payload.notes,
                    review_status=review_status,
                    review_reasons=review_reasons,
                )
            )

        table_of_contents: list[CookbookTocEntry] = []
        if suffix == ".epub":
            recipe_paths = {
                normalize_epub_path((recipe.source.get("anchor") or "").strip()).split("#", 1)[0]
                for recipe in recipes
                if recipe.source.get("format") == "epub"
            }
            table_of_contents = extract_epub_table_of_contents(
                file_bytes,
                recipe_paths={path for path in recipe_paths if path},
            )

        return CookbookExtractionResult(recipes=recipes, table_of_contents=table_of_contents)

    def build_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(
            model=self.settings.openai_embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    def _extract_recipe_payload(
        self,
        cookbook_title: str,
        section: CandidateSection,
    ) -> RecipeExtractionPayload:
        schema = self._strict_json_schema(RecipeExtractionPayload.model_json_schema())
        last_error: Exception | None = None
        for text_limit in MODEL_SECTION_TEXT_LIMITS:
            response = self.client.responses.create(
                model=self.settings.openai_recipe_model,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "You extract recipes from cookbook text. "
                                    "Return is_recipe=false for narrative or explanatory content. "
                                    "Use only the supplied text. Do not invent missing fields. "
                                    "Normalize ingredient names to concise lowercase kitchen terms. "
                                    "Capture recipe intro text, yield/serves/makes information, timing, "
                                    "preparation notes, and named subsections when present."
                                ),
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    f"Cookbook: {cookbook_title}\n"
                                    f"Section: {section.chapter_title or section.section_key}\n"
                                    f"Source format: {section.source_format}\n"
                                    f"Text:\n{section.text[:text_limit]}"
                                ),
                            }
                        ],
                    },
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "recipe_extraction",
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
            output_text = self._response_output_text(response)
            try:
                payload = self._parse_recipe_payload(output_text)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                continue
            return payload

        raise ValueError(
            f"Could not parse recipe extraction response for section {section.section_key}: {last_error}"
        )

    def _build_deterministic_recipe_payload(
        self,
        section: CandidateSection,
    ) -> RecipeExtractionPayload | None:
        if not section.ingredient_lines or not section.method_lines:
            return None

        ingredients = [
            self._parse_deterministic_ingredient(line)
            for line in section.ingredient_lines
            if line.strip()
        ]
        supplemental_sections = [
            ExtractedSupplementalSection(heading=section_block["heading"], lines=section_block["lines"])
            for section_block in section.metadata.get("supplemental_sections", [])
            if isinstance(section_block, dict)
            and str(section_block.get("heading", "")).strip()
            and isinstance(section_block.get("lines"), list)
        ]
        return RecipeExtractionPayload(
            is_recipe=True,
            title=(section.chapter_title or "").strip(),
            confidence=0.99,
            ingredients=ingredients,
            method_steps=[line.strip() for line in section.method_lines if line.strip()],
            intro=str(section.metadata.get("intro", "")).strip(),
            serves=str(section.metadata.get("serves", "")).strip(),
            makes=str(section.metadata.get("makes", "")).strip(),
            yield_value=str(section.metadata.get("yield", "")).strip(),
            prep_time=str(section.metadata.get("prep_time", "")).strip(),
            cook_time=str(section.metadata.get("cook_time", "")).strip(),
            total_time=str(section.metadata.get("total_time", "")).strip(),
            preparation_notes=[
                str(note).strip()
                for note in section.metadata.get("preparation_notes", [])
                if str(note).strip()
            ],
            supplemental_sections=supplemental_sections,
            notes=["deterministic_profile_extraction"],
        )

    def _parse_deterministic_ingredient(self, raw: str) -> ExtractedIngredient:
        cleaned = " ".join(raw.split()).strip()
        lowered = cleaned.casefold()
        preparation = None
        base = cleaned
        if "," in cleaned:
            base, trailing = cleaned.split(",", 1)
            preparation = trailing.strip() or None

        quantity_match = LEADING_QUANTITY_RE.match(base)
        quantity = quantity_match.group(0).strip() if quantity_match else None
        remainder = base[quantity_match.end() :] if quantity_match else base

        unit_match = LEADING_UNIT_RE.match(remainder)
        unit = unit_match.group(0).strip() if unit_match else None
        item = remainder[unit_match.end() :] if unit_match else remainder
        item = re.sub(r"\s*\([^)]*\)", "", item).strip(" .;:-")
        item = re.sub(r"^(?:of\s+)", "", item, flags=re.IGNORECASE)

        normalized_name = self._normalize_ingredient_name(item or cleaned)
        return ExtractedIngredient.model_validate(
            build_ingredient_payload(
                raw=cleaned,
                normalized_name=normalized_name,
                quantity=quantity,
                unit=unit,
                item=item or None,
                preparation=preparation,
                optional="optional" in lowered,
            )
        )

    def _normalize_ingredient_name(self, value: str) -> str:
        normalized = value.casefold()
        normalized = re.sub(r"\s*\([^)]*\)", " ", normalized)
        normalized = re.sub(r"\b(?:for|plus)\b.*$", "", normalized).strip()
        normalized = re.sub(r"^(?:a|an|the)\s+", "", normalized)
        normalized = normalize_ingredient_text(normalized)
        return normalized or "ingredient"

    def _merge_source_metadata(
        self,
        section_metadata: dict[str, Any],
        payload: RecipeExtractionPayload,
    ) -> dict[str, Any]:
        metadata = self._payload_source_metadata(payload)
        for key, value in section_metadata.items():
            if key == "supplemental_sections":
                if value:
                    metadata[key] = value
                continue
            if value not in ("", None, [], {}):
                metadata[key] = value
        return metadata

    def _payload_source_metadata(self, payload: RecipeExtractionPayload) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key, value in (
            ("intro", payload.intro.strip()),
            ("serves", payload.serves.strip()),
            ("makes", payload.makes.strip()),
            ("yield", payload.yield_value.strip()),
            ("prep_time", payload.prep_time.strip()),
            ("cook_time", payload.cook_time.strip()),
            ("total_time", payload.total_time.strip()),
        ):
            if value:
                metadata[key] = value

        preparation_notes = [note.strip() for note in payload.preparation_notes if note.strip()]
        if preparation_notes:
            metadata["preparation_notes"] = preparation_notes

        supplemental_sections: list[dict[str, Any]] = []
        for section in payload.supplemental_sections:
            heading = section.heading.strip()
            lines = [line.strip() for line in section.lines if line.strip()]
            if heading and lines:
                supplemental_sections.append({"heading": heading, "lines": lines})
        if supplemental_sections:
            metadata["supplemental_sections"] = supplemental_sections
        return metadata

    def _response_output_text(self, response: Any) -> str:
        output_text = getattr(response, "output_text", "")
        if output_text:
            return output_text

        output = response.model_dump()
        combined: list[str] = []
        for item in output.get("output", []):
            for part in item.get("content", []):
                if part.get("type") in {"output_text", "text"}:
                    combined.append(part.get("text", ""))
        return "".join(combined)

    def _parse_recipe_payload(self, output_text: str) -> RecipeExtractionPayload:
        candidate = output_text.strip()
        if not candidate:
            raise ValueError("Model returned empty output.")

        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]

        return RecipeExtractionPayload.model_validate(json.loads(candidate))

    def _strict_json_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        def visit(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object":
                    node.setdefault("additionalProperties", False)
                    properties = node.get("properties", {})
                    if isinstance(properties, dict):
                        node["required"] = list(properties.keys())
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        strict_schema = json.loads(json.dumps(schema))
        visit(strict_schema)
        return strict_schema

    def _is_recipe_candidate(self, section: CandidateSection) -> bool:
        title = (section.chapter_title or "").strip().lower()
        if title in NON_RECIPE_SECTION_TITLES:
            return False

        source_ref = ((section.anchor or section.section_key or "").strip().lower())
        if any(hint in source_ref for hint in NON_RECIPE_PATH_HINTS):
            return False

        text = section.text.lower()
        if "recipe list" in text and "ingredients" not in text and "method" not in text:
            return False

        lines = [line.strip() for line in section.text.splitlines() if line.strip()]
        ingredient_like_lines = sum(
            1 for line in lines if INGREDIENT_LINE_RE.search(line) or MEASUREMENT_RE.search(line)
        )
        serves_or_makes_markers = sum(
            1 for line in lines if line.lower().startswith(("serves", "makes"))
        )
        if not section.chapter_title and serves_or_makes_markers > 1:
            return False

        has_cues = any(cue in text for cue in RECIPE_CUES) or any(cue in text for cue in METHOD_CUES)
        has_title = bool(section.chapter_title and len(section.chapter_title.split()) <= 12)
        if ingredient_like_lines >= 3:
            return True
        if ingredient_like_lines >= 2 and has_title and len(lines) <= 40:
            return True
        if ingredient_like_lines >= 2 and has_cues:
            return True
        if ingredient_like_lines >= 1 and any(cue in text for cue in ("serves", "makes", "yield")):
            return True
        return has_title and has_cues and len(lines) <= 80

    def _review_flags(
        self,
        payload: RecipeExtractionPayload,
        section: CandidateSection,
    ) -> tuple[str, list[str]]:
        reasons: list[str] = []
        if payload.confidence < 0.78:
            reasons.append("low_model_confidence")
        if len(payload.ingredients) < 2:
            reasons.append("too_few_ingredients")
        if not payload.method_steps:
            reasons.append("missing_method_steps")
        if len(section.images) > 1:
            reasons.append("multiple_candidate_images")

        if reasons:
            return "needs_review", reasons
        return "pending_review", reasons

    def _extract_epub_sections(self, file_bytes: bytes) -> list[CandidateSection]:
        with tempfile.NamedTemporaryFile(suffix=".epub") as handle:
            handle.write(file_bytes)
            handle.flush()
            book = epub.read_epub(handle.name)

        href_to_item: dict[str, Any] = {}
        for item in book.get_items():
            name = getattr(item, "file_name", None) or item.get_name()
            href_to_item[name] = item

        sections: list[CandidateSection] = []
        for item in book.get_items_of_type(ITEM_DOCUMENT):
            section_key = getattr(item, "file_name", None) or item.get_name()
            lowered_section_key = section_key.lower()
            if lowered_section_key.endswith(("nav.xhtml", "nav.html")) or lowered_section_key.startswith("nav."):
                continue
            soup = BeautifulSoup(item.get_content(), "html.parser")
            profile = self._match_epub_recipe_paragraph_profile(soup)
            if profile:
                sections.extend(
                    self._extract_epub_recipe_paragraph_sections(book, href_to_item, item, soup, profile)
                )
                continue

            current_title = None
            current_lines: list[str] = []
            current_images: list[CandidateImage] = []
            counter = 0

            for node in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "img"]):
                if node.name in {"h1", "h2", "h3", "h4"}:
                    if current_lines:
                        counter += 1
                        section = CandidateSection(
                            source_format="epub",
                            section_key=f"{section_key}#{counter}",
                            chapter_title=current_title or self._inferred_section_title(current_lines),
                            anchor=f"{section_key}#{counter}",
                            text="\n".join(current_lines).strip(),
                            excerpt=self._excerpt(current_lines),
                            images=list(current_images),
                        )
                        self._populate_generic_epub_section_fields(section)
                        sections.append(section)
                        current_lines = []
                        current_images = []
                    current_title = node.get_text(" ", strip=True)
                    current_lines.append(current_title)
                    continue

                if node.name == "img":
                    image = self._resolve_epub_image(book, href_to_item, item, node)
                    if image:
                        current_images.append(image)
                    continue

                text = node.get_text(" ", strip=True)
                if text:
                    current_lines.append(text)

            if (
                current_lines
                and current_images
                and not current_title
                and self._image_page_matches_title(
                    self._excerpt(current_lines),
                    sections[-1].chapter_title if sections else None,
                )
            ):
                sections[-1].images.extend(current_images)
            elif current_lines:
                counter += 1
                section = CandidateSection(
                    source_format="epub",
                    section_key=f"{section_key}#{counter}",
                    chapter_title=current_title or self._inferred_section_title(current_lines),
                    anchor=f"{section_key}#{counter}",
                    text="\n".join(current_lines).strip(),
                    excerpt=self._excerpt(current_lines),
                    images=list(current_images),
                )
                self._populate_generic_epub_section_fields(section)
                sections.append(section)
            elif current_images and self._image_page_matches_title(
                self._excerpt(current_lines),
                sections[-1].chapter_title if sections else None,
            ):
                sections[-1].images.extend(current_images)

        return [section for section in sections if section.text.strip()]

    def _populate_generic_epub_section_fields(self, section: CandidateSection) -> None:
        lines = self._dedupe_consecutive_lines(
            [line.strip() for line in section.text.splitlines() if line.strip()]
        )
        if not lines:
            return
        section.text = "\n".join(lines).strip()
        section.excerpt = self._excerpt(lines)

        title = (section.chapter_title or "").strip()
        content_lines = list(lines)
        if title and content_lines and content_lines[0].casefold() == title.casefold():
            content_lines = content_lines[1:]

        metadata: dict[str, Any] = {}
        intro_lines: list[str] = []
        ingredient_lines: list[str] = []
        raw_method_lines: list[str] = []
        mode: str | None = None

        for raw_line in content_lines:
            line = " ".join(raw_line.split()).strip()
            if not line:
                continue

            heading = line.rstrip(":").strip().lower()
            if heading in {"ingredient", "ingredients"}:
                mode = "ingredients"
                continue
            if heading in {"method", "methods", "directions", "instructions", "preparation", "steps"}:
                mode = "method"
                continue

            metadata_key, metadata_value = self._extract_recipe_metadata_from_line(line)
            if metadata_key and metadata_value:
                metadata[metadata_key] = metadata_value
                continue

            if mode == "ingredients":
                ingredient_lines.append(line)
                continue

            if mode == "method":
                raw_method_lines.append(line)
                continue

            if self._looks_like_generic_ingredient_line(line):
                ingredient_lines.append(line)
                mode = "ingredients"
                continue

            if ingredient_lines and self._looks_like_generic_method_line(line):
                raw_method_lines.append(line)
                mode = "method"
                continue

            if not ingredient_lines and not raw_method_lines:
                intro_lines.append(line)
                continue

            if ingredient_lines and not raw_method_lines:
                if self._looks_like_generic_ingredient_continuation(line):
                    ingredient_lines[-1] = f"{ingredient_lines[-1]} {line}".strip()
                else:
                    raw_method_lines.append(line)
                    mode = "method"
                continue

            raw_method_lines.append(line)

        if intro_lines:
            metadata["intro"] = self._dedupe_consecutive_lines(intro_lines)

        section.metadata = self._finalize_section_metadata(metadata)
        section.ingredient_lines = self._dedupe_consecutive_lines(ingredient_lines)
        section.method_lines = self._collapse_generic_method_lines(
            self._dedupe_consecutive_lines(raw_method_lines)
        )

    def _looks_like_generic_ingredient_line(self, line: str) -> bool:
        normalized = " ".join(line.split()).strip()
        lowered = normalized.casefold()
        if not normalized:
            return False
        if self._extract_recipe_metadata_from_line(normalized)[0]:
            return False
        if lowered.startswith(("for the ", "for ", "to serve", "serve with")):
            return False
        return bool(INGREDIENT_LINE_RE.search(normalized) or MEASUREMENT_RE.search(normalized))

    def _looks_like_generic_method_line(self, line: str) -> bool:
        normalized = " ".join(line.split()).strip()
        lowered = normalized.casefold()
        if not normalized or self._looks_like_generic_ingredient_line(normalized):
            return False
        return bool(
            GENERIC_METHOD_STEP_RE.match(normalized)
            or lowered.startswith(("first", "then", "next", "meanwhile", "finally"))
            or normalized.endswith(".")
        )

    def _looks_like_generic_ingredient_continuation(self, line: str) -> bool:
        normalized = " ".join(line.split()).strip()
        if not normalized:
            return False
        if self._looks_like_generic_ingredient_line(normalized):
            return False
        return not self._looks_like_generic_method_line(normalized)

    def _collapse_generic_method_lines(self, lines: list[str]) -> list[str]:
        steps: list[str] = []
        current: str | None = None

        for line in lines:
            normalized = " ".join(line.split()).strip()
            if not normalized:
                continue

            if re.fullmatch(r"step\s+\d+", normalized, re.IGNORECASE):
                if current:
                    steps.append(current)
                    current = None
                continue

            stripped = GENERIC_METHOD_STEP_RE.sub("", normalized).strip() or normalized
            starts_new_step = bool(GENERIC_METHOD_STEP_RE.match(normalized)) or current is None
            if starts_new_step:
                if current:
                    steps.append(current)
                current = stripped
                continue

            current = f"{current} {stripped}".strip() if current else stripped

        if current:
            steps.append(current)
        return steps

    def _dedupe_consecutive_lines(self, lines: list[str]) -> list[str]:
        deduped: list[str] = []
        previous_normalized: str | None = None

        for line in lines:
            normalized = " ".join(str(line).split()).strip()
            if not normalized:
                continue
            if normalized.casefold() == previous_normalized:
                continue
            deduped.append(normalized)
            previous_normalized = normalized.casefold()

        return deduped

    def _match_epub_recipe_paragraph_profile(
        self, soup: BeautifulSoup
    ) -> EpubRecipeParagraphProfile | None:
        for profile in EPUB_RECIPE_PARAGRAPH_PROFILES:
            title_nodes = 0
            has_ingredients = False
            has_method = False
            seen_classes: set[str] = set()
            for node in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
                classes = set(node.get("class", []))
                seen_classes.update(classes)
                if classes & (profile.title_classes | profile.fallback_title_classes):
                    title_nodes += 1
                if classes & profile.ingredient_classes:
                    has_ingredients = True
                if classes & profile.method_classes:
                    has_method = True
                if not classes and profile.allow_plain_text_method:
                    has_method = True
            if profile.signature_classes and not (seen_classes & profile.signature_classes):
                continue
            if title_nodes >= profile.min_title_nodes and has_ingredients and has_method:
                return profile
        return None

    def _extract_epub_recipe_paragraph_sections(
        self,
        book: epub.EpubBook,
        href_to_item: dict[str, Any],
        document_item: Any,
        soup: BeautifulSoup,
        profile: EpubRecipeParagraphProfile,
    ) -> list[CandidateSection]:
        section_key = getattr(document_item, "file_name", None) or document_item.get_name()
        sections: list[CandidateSection] = []
        current_category: str | None = None
        current_title: str | None = None
        current_anchor: str | None = None
        current_lines: list[str] = []
        current_images: list[CandidateImage] = []
        current_metadata: dict[str, Any] = {}
        current_supplemental_heading: str | None = None
        current_ingredient_lines: list[str] = []
        current_method_lines: list[str] = []
        buffered_lines: list[str] = []
        buffered_images: list[CandidateImage] = []
        counter = 0

        def flush() -> None:
            nonlocal counter, current_lines, current_images, current_title, current_anchor, current_metadata, current_supplemental_heading, current_ingredient_lines, current_method_lines
            if not current_title or not current_lines:
                return
            deduped_lines = self._dedupe_consecutive_lines(current_lines)
            counter += 1
            sections.append(
                CandidateSection(
                    source_format="epub",
                    section_key=f"{section_key}#{counter}",
                    chapter_title=current_title,
                    anchor=f"{section_key}#{current_anchor or counter}",
                    text="\n".join(deduped_lines).strip(),
                    excerpt=self._excerpt(deduped_lines),
                    images=list(current_images),
                    metadata=self._finalize_section_metadata(current_metadata),
                    ingredient_lines=self._dedupe_consecutive_lines(current_ingredient_lines),
                    method_lines=self._dedupe_consecutive_lines(current_method_lines),
                )
            )
            current_title = None
            current_anchor = None
            current_lines = []
            current_images = []
            current_metadata = {}
            current_supplemental_heading = None
            current_ingredient_lines = []
            current_method_lines = []

        def start_section(title: str, anchor: str | None) -> None:
            nonlocal current_title, current_anchor, current_lines, current_images, current_metadata, current_supplemental_heading, current_ingredient_lines, current_method_lines, buffered_lines, buffered_images
            current_title = title
            current_anchor = anchor
            current_lines = [title]
            current_metadata = {}
            current_supplemental_heading = None
            current_ingredient_lines = []
            current_method_lines = []
            if current_category:
                current_lines.insert(0, current_category)
            if buffered_lines:
                current_lines.extend(buffered_lines)
                buffered_lines = []
            if buffered_images:
                if profile.retain_buffered_images_after_category or not current_category:
                    current_images = list(buffered_images)
                buffered_images = []

        for node in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "img"]):
            if node.name == "img":
                classes = set(node.get("class", []))
                if classes & profile.ignored_image_classes:
                    continue
                image = self._resolve_epub_image(book, href_to_item, document_item, node)
                if image:
                    if current_title:
                        if profile.defer_images_after_method and current_method_lines:
                            buffered_images.append(image)
                        else:
                            current_images.append(image)
                    else:
                        buffered_images.append(image)
                continue

            text = node.get_text(" ", strip=True)
            if not text:
                continue

            classes = set(node.get("class", []))
            if classes & profile.category_classes:
                current_category = text
                continue

            if classes & profile.title_classes:
                flush()
                start_section(
                    text,
                    node.get("id")
                    or next(
                    (descendant.get("id") for descendant in node.find_all(True) if descendant.get("id")),
                    None,
                )
                )
                continue

            if not current_title and classes & profile.fallback_title_classes:
                start_section(
                    text,
                    node.get("id")
                    or next(
                        (
                            descendant.get("id")
                            for descendant in node.find_all(True)
                            if descendant.get("id")
                        ),
                        None,
                    ),
                )
                continue

            if not current_title:
                if not classes or classes & profile.text_classes:
                    buffered_lines.append(text)
                continue

            if classes & profile.intro_classes:
                self._append_metadata_paragraph(current_metadata, "intro", text)
                current_lines.append(text)
                continue

            if classes & profile.metadata_classes:
                current_supplemental_heading = None
                metadata_key, metadata_value = self._extract_recipe_metadata_from_line(text)
                if metadata_key and metadata_value:
                    current_metadata[metadata_key] = metadata_value
                current_lines.append(text)
                continue

            if classes & profile.supplemental_heading_classes:
                current_supplemental_heading = text
                current_metadata.setdefault("supplemental_sections", []).append(
                    {"heading": text, "lines": []}
                )
                current_lines.append(text)
                continue

            if classes & profile.ingredient_classes and current_supplemental_heading:
                supplemental_sections = current_metadata.setdefault("supplemental_sections", [])
                if supplemental_sections:
                    supplemental_sections[-1]["lines"].append(text)
                current_ingredient_lines.append(text)
                current_lines.append(text)
                continue

            if classes & profile.method_classes:
                current_supplemental_heading = None
                current_method_lines.append(text)
                current_lines.append(text)
                continue

            if not classes or classes & profile.text_classes:
                if classes & profile.ingredient_classes:
                    current_ingredient_lines.append(text)
                current_lines.append(text)

        flush()
        return sections

    def _append_metadata_paragraph(
        self,
        metadata: dict[str, Any],
        key: str,
        text: str,
    ) -> None:
        paragraphs = metadata.setdefault(key, [])
        if isinstance(paragraphs, list):
            paragraphs.append(text)

    def _extract_recipe_metadata_from_line(self, text: str) -> tuple[str | None, str | None]:
        normalized = " ".join(text.split()).strip()
        if not normalized:
            return None, None

        for prefix, key in (
            ("serves", "serves"),
            ("makes", "makes"),
            ("yield", "yield"),
            ("prep time", "prep_time"),
            ("cook time", "cook_time"),
            ("chilling time", "chilling_time"),
            ("total time", "total_time"),
        ):
            if normalized.lower().startswith(prefix):
                value = normalized[len(prefix) :].lstrip(" :.-").strip()
                return key, value or normalized

        if ":" in normalized:
            label, value = [part.strip() for part in normalized.split(":", 1)]
            key_map = {
                "prep time": "prep_time",
                "cook time": "cook_time",
                "total time": "total_time",
                "yield": "yield",
                "serves": "serves",
                "makes": "makes",
                "chilling time": "chilling_time",
            }
            mapped_key = key_map.get(label.lower())
            if mapped_key and value:
                return mapped_key, value

        return None, None

    def _finalize_section_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        finalized: dict[str, Any] = {}
        for key, value in metadata.items():
            if key == "intro":
                paragraphs = self._dedupe_consecutive_lines(
                    [paragraph.strip() for paragraph in value if paragraph.strip()]
                )
                if paragraphs:
                    finalized[key] = "\n\n".join(paragraphs)
                continue
            if key == "supplemental_sections":
                sections: list[dict[str, Any]] = []
                for section in value:
                    if not isinstance(section, dict):
                        continue
                    heading = str(section.get("heading", "")).strip()
                    lines = self._dedupe_consecutive_lines(
                        [
                            str(line).strip()
                            for line in section.get("lines", [])
                            if str(line).strip()
                        ]
                    )
                    if heading and lines:
                        sections.append({"heading": heading, "lines": lines})
                if sections:
                    finalized[key] = sections
                continue
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    finalized[key] = cleaned
                continue
            if value:
                finalized[key] = value
        return finalized

    def _resolve_epub_image(
        self,
        book: epub.EpubBook,
        href_to_item: dict[str, Any],
        document_item: Any,
        node: Any,
    ) -> CandidateImage | None:
        src = (node.get("src") or "").strip()
        if not src:
            return None
        document_name = getattr(document_item, "file_name", None) or document_item.get_name()
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(document_name), src))
        image_item = href_to_item.get(resolved) or book.get_item_with_href(resolved)
        if not image_item or image_item.get_type() != ITEM_IMAGE:
            return None
        filename = getattr(image_item, "file_name", None) or image_item.get_name()
        content_type = getattr(image_item, "media_type", None) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return CandidateImage(
            filename=Path(filename).name,
            content_type=content_type,
            data=image_item.get_content(),
            source_ref=resolved,
        )

    def _extract_pdf_sections(self, file_bytes: bytes) -> list[CandidateSection]:
        document = fitz.open(stream=file_bytes, filetype="pdf")
        sections: list[CandidateSection] = []

        for index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            if not text:
                continue
            images = self._extract_pdf_images(document, page, index)
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = lines[0][:120] if lines else f"Page {index}"
            sections.append(
                CandidateSection(
                    source_format="pdf",
                    section_key=f"page-{index}",
                    chapter_title=title,
                    anchor=f"page-{index}",
                    page_start=index,
                    page_end=index,
                    text=text,
                    excerpt=self._excerpt(lines),
                    images=images,
                )
            )

        document.close()
        return sections

    def _extract_pdf_images(
        self,
        document: fitz.Document,
        page: fitz.Page,
        page_number: int,
    ) -> list[CandidateImage]:
        images: list[CandidateImage] = []
        seen_xrefs: set[int] = set()
        for image_info in page.get_images(full=True):
            xref = image_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            extracted = document.extract_image(xref)
            if not extracted:
                continue
            ext = extracted.get("ext", "bin")
            images.append(
                CandidateImage(
                    filename=f"page-{page_number}-image-{xref}.{ext}",
                    content_type=mimetypes.guess_type(f"x.{ext}")[0] or "application/octet-stream",
                    data=extracted["image"],
                    source_ref=f"page-{page_number}",
                )
            )
        return images

    def _excerpt(self, lines: list[str]) -> str:
        return " ".join(lines[:4])[:420].strip()

    def _inferred_section_title(self, lines: list[str]) -> str | None:
        if not lines:
            return None
        title = (lines[0] or "").strip()
        if not title or len(title.split()) > 16:
            return None
        return title[:160]

    def _image_page_matches_title(self, text: str, title: str | None) -> bool:
        if not text or not title:
            return False
        return self._normalize_title_token(text) == self._normalize_title_token(title)

    def _normalize_title_token(self, value: str) -> str:
        return TITLE_NORMALIZE_RE.sub(" ", value.casefold()).strip()
