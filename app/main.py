from __future__ import annotations

import asyncio
from collections import Counter
from collections import defaultdict, deque
import logging
import re
import tempfile
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from bs4 import BeautifulSoup
from ebooklib import ITEM_DOCUMENT, epub
import fitz
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.bbc_goodfood_pdf import extract_bbc_goodfood_pdf
from app.config import Settings, get_settings
from app.epub import build_epub_chapter_map, normalize_epub_path
from app.extractor import OpenAIRecipeExtractor, RecipeDraft
from app.jamie_oliver_pdf import extract_jamie_oliver_pdf
from app.ingredients import canonicalize_ingredient_name, ingredient_index_name
from app.meal_plan import (
    MealPlanDocument,
    DEFAULT_IMPORT_WEEK_LIMIT,
    MEAL_LABELS,
    REDIS_MEAL_PLAN_SOURCE,
    WEEKDAY_LABELS,
    append_blank_row,
    append_blank_week,
    add_recipe_to_latest_week,
    populate_week_shopping_lists,
    load_or_import_meal_plan,
    parse_meal_plan_form,
    recipe_option_value,
    remove_week,
    save_meal_plan,
)
from app.models import (
    CookbookMetadataUpdateRequest,
    CookbookTocEntry,
    RecipeImportCommitRequest,
    RecipeImportPreviewRequest,
    RecipeRecord,
    RecipeTagsUpdateRequest,
    ReviewStatus,
    ReviewUpdateRequest,
    SearchResultRecord,
)
from app.nytimes_pdf import extract_nytimes_pdf
from app.repository import LibraryRepository
from app.seasonality import (
    UK_SEASONAL_PRODUCE,
    current_uk_month,
    main_seasonal_ingredients,
    recipe_is_in_season,
)
from app.waitrose_pdf import extract_waitrose_pdf
from app.url_recipe_import import UrlRecipeImporter, UrlRecipeImportError

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
RECIPE_METADATA_LABELS = {
    "active_time": "Active Time",
    "author": "Author",
    "cook_time": "Cook Time",
    "description": "Description",
    "headnote": "Headnote",
    "introduction": "Introduction",
    "makes": "Makes",
    "prep_time": "Prep Time",
    "preparation_notes": "Notes",
    "published_at": "Published",
    "serves": "Serves",
    "subtitle": "Subtitle",
    "total_time": "Total Time",
    "updated_at": "Updated",
    "yield": "Yield",
}
RECIPE_METADATA_PRIORITY = ("author", "published_at", "updated_at", "yield", "serves", "makes")
RECIPE_METADATA_TEXT_BLOCK_KEYS = frozenset({"description", "headnote", "introduction", "subtitle"})
DIETARY_FILTER_OPTIONS = (
    {"value": "vegetarian", "label": "Vegetarian"},
    {"value": "pescatarian", "label": "Pescatarian"},
)
DURATION_FILTER_OPTIONS = (
    {"value": "under-30", "label": "Under 30 min"},
    {"value": "under-60", "label": "Under 1 hour"},
    {"value": "under-120", "label": "Under 2 hours"},
    {"value": "over-120", "label": "2 hours+"},
)
SEASON_FILTER_OPTIONS = (
    {"value": "in-season", "label": "In season now"},
)
MONTH_OPTIONS = (
    {"value": 1, "label": "Jan"},
    {"value": 2, "label": "Feb"},
    {"value": 3, "label": "Mar"},
    {"value": 4, "label": "Apr"},
    {"value": 5, "label": "May"},
    {"value": 6, "label": "Jun"},
    {"value": 7, "label": "Jul"},
    {"value": 8, "label": "Aug"},
    {"value": 9, "label": "Sep"},
    {"value": 10, "label": "Oct"},
    {"value": 11, "label": "Nov"},
    {"value": 12, "label": "Dec"},
)
MONTH_LABELS = {option["value"]: option["label"] for option in MONTH_OPTIONS}
TAG_REVIEW_FILTER_OPTIONS = (
    {"value": "all", "label": "All recipes"},
    {"value": "manual", "label": "Manual overrides"},
    {"value": "needs-review", "label": "Needs review"},
)
DIETARY_TAG_VALUES = ("vegetarian", "pescatarian")
INGREDIENT_CLASSIFICATION_VALUES = ("none", "meat", "fish")
TAG_SEASON_FILTER_OPTIONS = (
    {"value": "all", "label": "Any season"},
    {"value": "in-season", "label": "In season now"},
    {"value": "no-seasonal-produce", "label": "No seasonal produce"},
    *({"value": f"month-{option['value']}", "label": option["label"]} for option in MONTH_OPTIONS),
)
LAND_MEAT_PATTERN = re.compile(
    r"\b("
    r"beef|steak|veal|pork|bacon|ham|prosciutto|pancetta|chorizo|salami|sausage|"
    r"lamb|mutton|goat|venison|duck|chicken|turkey|goose|pheasant|quail|rabbit|"
    r"gelatin|lard|suet|pepperoni|mortadella|guanciale|nduja|bone\s+broth|"
    r"chicken\s+stock|beef\s+stock|pork\s+stock|lamb\s+stock"
    r")s?\b",
    re.IGNORECASE,
)
SEAFOOD_PATTERN = re.compile(
    r"\b("
    r"fish|salmon|tuna|cod|haddock|halibut|trout|anchov(?:y|ies)|sardine|mackerel|"
    r"seafood|prawn|shrimp|crab|lobster|scallop|clam|mussel|oyster|squid|octopus|"
    r"fish\s+sauce|worcestershire"
    r")s?\b",
    re.IGNORECASE,
)
SEAFOOD_FALSE_POSITIVE_PATTERN = re.compile(r"\b(oyster\s+mushrooms?|fish\s+pepper)\b", re.IGNORECASE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    repository = LibraryRepository.from_settings(settings)

    try:
        schema_summary = repository.ensure_schema()
        logger.info("Schema ensured: %s", schema_summary)
    except Exception as exc:  # pragma: no cover - startup should remain tolerant
        logger.warning("Schema bootstrap did not complete at startup: %s", exc)

    app.state.settings = settings
    app.state.repository = repository
    yield
    repository.close()


app = FastAPI(title="Heley Family Cookbook", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
BRAND_IMAGE_MAP = {
    "cottage": BASE_DIR.parent / "data-raw" / "cottage.png",
    "george-chef": BASE_DIR.parent / "data-raw" / "George the chef in a cozy kitchen.png",
}


def normalize_forwarded_prefix(value: str | None) -> str:
    prefix = (value or "").strip()
    if not prefix:
        return ""
    prefix = "/" + prefix.strip("/")
    return "" if prefix == "/" else prefix


@app.middleware("http")
async def apply_forwarded_prefix(request: Request, call_next):
    prefix = normalize_forwarded_prefix(request.headers.get("x-forwarded-prefix"))
    if prefix and not request.scope.get("path", "").startswith("/static/"):
        request.scope["root_path"] = prefix
    return await call_next(request)


def get_repository(request: Request) -> LibraryRepository:
    return request.app.state.repository


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def redirect_with_notice(path: str, notice: str) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{separator}notice={quote(notice)}", status_code=303)


def safe_redirect_target(return_to: str | None, fallback: str) -> str:
    if not return_to:
        return fallback
    parsed = urlparse(return_to)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not parsed.path.startswith("/"):
        return fallback
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def common_template_context(request: Request, *, notice: str | None) -> dict:
    settings = get_app_settings(request)
    repository = get_repository(request)
    return {
        "request": request,
        "app_name": settings.app_name,
        "max_upload_mb": settings.max_upload_mb,
        "allowed_extensions": ", ".join(sorted(settings.allowed_extensions)),
        "notice": notice,
        "datastores": repository.health_report()["datastores"],
    }


def cookbook_sort_options() -> list[dict[str, str]]:
    return [
        {"value": "title", "label": "Title"},
        {"value": "author", "label": "Author"},
        {"value": "cuisine", "label": "Cuisine"},
        {"value": "published_at", "label": "Date Published"},
    ]


def cookbook_management_groups(cookbooks: list[Any]) -> list[dict[str, Any]]:
    groups = [
        {"slug": "books", "title": "Books", "items": []},
        {"slug": "nytimes", "title": "NYTimes", "items": []},
        {"slug": "jamie-oliver", "title": "Jamie Oliver", "items": []},
        {"slug": "bbc-goodfood", "title": "BBC Good Food", "items": []},
        {"slug": "waitrose-recipes", "title": "Waitrose Recipes", "items": []},
    ]
    group_map = {group["slug"]: group for group in groups}

    for cookbook in cookbooks:
        slug = cookbook.collection_slug or "books"
        group_map.setdefault(slug, {"slug": slug, "title": slug.replace("-", " ").title(), "items": []})
        group_map[slug]["items"].append(cookbook)

    return [group for group in groups if group["items"]]


def normalize_tag_review_filter(value: str | None) -> str:
    allowed = {option["value"] for option in TAG_REVIEW_FILTER_OPTIONS}
    cleaned = (value or "all").strip().casefold()
    return cleaned if cleaned in allowed else "all"


def normalize_tag_diet_filter(value: str | None) -> str:
    cleaned = (value or "all").strip().casefold()
    if cleaned == "all":
        return "all"
    return cleaned if cleaned in DIETARY_TAG_VALUES else "all"


def normalize_tag_season_filter(value: str | None) -> str:
    allowed = {option["value"] for option in TAG_SEASON_FILTER_OPTIONS}
    cleaned = (value or "all").strip().casefold()
    return cleaned if cleaned in allowed else "all"


def normalize_tag_collection_filter(value: str | None, cookbooks: list[Any], collections: list[Any]) -> str:
    cleaned = (value or "all").strip()
    allowed = {"all", "books"} | {collection.slug for collection in collections} | {
        cookbook.collection_slug for cookbook in cookbooks if cookbook.collection_slug
    }
    return cleaned if cleaned in allowed else "all"


def normalize_tag_cookbook_filter(value: str | None, cookbooks: list[Any]) -> str:
    cleaned = (value or "all").strip()
    allowed = {"all"} | {cookbook.id for cookbook in cookbooks}
    return cleaned if cleaned in allowed else "all"


def recipe_tag_collection_options(collections: list[Any]) -> list[dict[str, str]]:
    return [
        {"value": "all", "label": "All collections"},
        {"value": "books", "label": "Books"},
        *[
            {"value": collection.slug, "label": collection.title}
            for collection in collections
            if collection.slug not in {"favourites", "want-to-try"}
        ],
    ]


def recipe_tag_cookbook_options(cookbooks: list[Any], *, collection_filter: str = "all") -> list[Any]:
    filtered = cookbooks
    if collection_filter == "books":
        filtered = [cookbook for cookbook in cookbooks if not cookbook.collection_slug]
    elif collection_filter != "all":
        filtered = [cookbook for cookbook in cookbooks if cookbook.collection_slug == collection_filter]
    return sorted(filtered, key=lambda cookbook: cookbook.title.casefold())


def filter_recipes_for_tag_management(
    recipes: list[RecipeRecord],
    *,
    cookbook_filter: str = "all",
    collection_filter: str = "all",
    cookbook_collection_map: dict[str, str],
) -> list[RecipeRecord]:
    filtered = recipes
    if collection_filter == "books":
        filtered = [
            recipe
            for recipe in filtered
            if not cookbook_collection_map.get(recipe.cookbook_id)
        ]
    elif collection_filter != "all":
        filtered = [
            recipe
            for recipe in filtered
            if cookbook_collection_map.get(recipe.cookbook_id) == collection_filter
        ]
    if cookbook_filter != "all":
        filtered = [recipe for recipe in filtered if recipe.cookbook_id == cookbook_filter]
    return filtered


def clamp_tag_page_size(value: int | None) -> int:
    if value is None:
        return 50
    return min(max(value, 25), 200)


def tag_pagination_window(page: int, page_count: int, *, radius: int = 2) -> list[int]:
    if page_count <= 1:
        return [1]
    start = max(1, page - radius)
    end = min(page_count, page + radius)
    if start == 1:
        end = min(page_count, start + radius * 2)
    if end == page_count:
        start = max(1, end - radius * 2)
    return list(range(start, end + 1))


def _metadata_list(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return None


def _dietary_override(recipe: RecipeRecord) -> list[str] | None:
    raw_tags = _metadata_list((recipe.source.metadata or {}).get("dietary_tags_override"))
    if raw_tags is None:
        return None
    tags: list[str] = []
    for value in raw_tags:
        cleaned = str(value).strip().casefold()
        if cleaned in DIETARY_TAG_VALUES and cleaned not in tags:
            tags.append(cleaned)
    return tags


def _seasonal_months_override(recipe: RecipeRecord) -> list[int] | None:
    raw_months = _metadata_list((recipe.source.metadata or {}).get("seasonal_months_override"))
    if raw_months is None:
        return None
    months: list[int] = []
    for value in raw_months:
        try:
            month = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= month <= 12 and month not in months:
            months.append(month)
    return months


def _seasonal_ingredients_override(recipe: RecipeRecord) -> list[str] | None:
    raw_ingredients = _metadata_list((recipe.source.metadata or {}).get("seasonal_ingredients_override"))
    if raw_ingredients is None:
        return None
    ingredients: list[str] = []
    for value in raw_ingredients:
        cleaned = canonicalize_ingredient_name(str(value))
        if cleaned and cleaned not in ingredients:
            ingredients.append(cleaned)
    return ingredients


def ingredient_classification_key(ingredient: Any) -> str:
    if isinstance(ingredient, dict):
        raw = str(ingredient.get("raw", "") or "")
        normalized = str(ingredient.get("normalized_name", "") or "")
        item = str(ingredient.get("item", "") or "")
    else:
        raw = str(getattr(ingredient, "raw", "") or "")
        normalized = str(getattr(ingredient, "normalized_name", "") or "")
        item = str(getattr(ingredient, "item", "") or "")
    return canonicalize_ingredient_name(ingredient_index_name(ingredient) or item or normalized or raw)


def _ingredient_classification_overrides(recipe: RecipeRecord) -> dict[str, str]:
    raw_overrides = (recipe.source.metadata or {}).get("ingredient_classification_overrides")
    if not isinstance(raw_overrides, dict):
        return {}
    overrides: dict[str, str] = {}
    for key, value in raw_overrides.items():
        cleaned_key = canonicalize_ingredient_name(str(key))
        cleaned_value = str(value).strip().casefold()
        if cleaned_key and cleaned_value in INGREDIENT_CLASSIFICATION_VALUES:
            overrides[cleaned_key] = cleaned_value
    return overrides


def auto_ingredient_classification(ingredient: Any) -> str:
    parts: list[str] = []
    for attr in ("raw", "normalized_name", "canonical_name", "item"):
        if isinstance(ingredient, dict):
            parts.append(str(ingredient.get(attr, "") or ""))
        else:
            parts.append(str(getattr(ingredient, attr, "") or ""))
    text = SEAFOOD_FALSE_POSITIVE_PATTERN.sub(" ", " ".join(parts).casefold())
    if LAND_MEAT_PATTERN.search(text):
        return "meat"
    if SEAFOOD_PATTERN.search(text):
        return "fish"
    return "none"


def ingredient_classification_rows(recipe: RecipeRecord) -> list[Any]:
    overrides = _ingredient_classification_overrides(recipe)
    rows: list[Any] = []
    seen: set[str] = set()
    for ingredient in recipe.ingredients:
        key = ingredient_classification_key(ingredient)
        if not key or key in seen:
            continue
        seen.add(key)
        auto_value = auto_ingredient_classification(ingredient)
        value = overrides.get(key, auto_value)
        label = ingredient.raw or ingredient.item or ingredient.normalized_name or key
        rows.append(
            SimpleNamespace(
                key=key,
                label=label,
                auto_classification=auto_value,
                classification=value,
                is_override=key in overrides,
            )
        )
    return rows


def ingredient_classification_summary(rows: list[Any]) -> str:
    meat = [row.label for row in rows if row.classification == "meat"]
    fish = [row.label for row in rows if row.classification == "fish"]
    parts: list[str] = []
    if meat:
        parts.append(f"Meat: {', '.join(meat[:3])}{'...' if len(meat) > 3 else ''}")
    if fish:
        parts.append(f"Fish: {', '.join(fish[:3])}{'...' if len(fish) > 3 else ''}")
    return " · ".join(parts) if parts else "No meat or fish ingredients tagged."


def effective_recipe_dietary_tags(recipe: RecipeRecord) -> list[str]:
    override = _dietary_override(recipe)
    if override is not None:
        return override
    ingredient_rows = ingredient_classification_rows(recipe)
    if any(row.classification == "meat" for row in ingredient_rows):
        return []
    if any(row.classification == "fish" for row in ingredient_rows):
        return ["pescatarian"]
    return ["vegetarian", "pescatarian"]


def effective_recipe_seasonal_ingredients(recipe: RecipeRecord) -> list[str]:
    override = _seasonal_ingredients_override(recipe)
    return override if override is not None else main_seasonal_ingredients(recipe)


def effective_recipe_seasonal_months(recipe: RecipeRecord) -> list[int]:
    override = _seasonal_months_override(recipe)
    if override is not None:
        return override

    ingredients = main_seasonal_ingredients(recipe)
    if not ingredients:
        return []
    common_months = set(UK_SEASONAL_PRODUCE[ingredients[0]])
    for ingredient in ingredients[1:]:
        common_months &= set(UK_SEASONAL_PRODUCE[ingredient])
    return sorted(common_months)


def effective_recipe_is_in_season(recipe: RecipeRecord, *, month: int | None = None) -> bool:
    effective_month = month or current_uk_month()
    return effective_month in effective_recipe_seasonal_months(recipe)


def recipe_tag_source(recipe: RecipeRecord) -> str:
    has_dietary = _dietary_override(recipe) is not None
    has_ingredients = bool(_ingredient_classification_overrides(recipe))
    has_season = _seasonal_months_override(recipe) is not None or _seasonal_ingredients_override(recipe) is not None
    has_dietary_side = has_dietary or has_ingredients
    if has_dietary_side and has_season:
        return "manual"
    if has_dietary_side or has_season:
        return "mixed"
    return "auto"


def _matched_terms(pattern: re.Pattern[str], value: str) -> list[str]:
    terms: list[str] = []
    for match in pattern.finditer(value):
        term = " ".join(match.group(0).split()).casefold()
        if term and term not in terms:
            terms.append(term)
    return terms


def dietary_tag_explanation(recipe: RecipeRecord) -> str:
    ingredient_rows = ingredient_classification_rows(recipe)
    meat = [row.label for row in ingredient_rows if row.classification == "meat"]
    fish = [row.label for row in ingredient_rows if row.classification == "fish"]
    if meat:
        return f"Contains meat: {', '.join(meat[:4])}."
    if fish:
        return f"Contains fish/seafood: {', '.join(fish[:4])}."
    return "No meat or seafood detected."


def seasonal_month_labels(months: list[int]) -> str:
    return ", ".join(MONTH_LABELS[month] for month in months if month in MONTH_LABELS)


def seasonality_explanation(recipe: RecipeRecord) -> str:
    ingredients = effective_recipe_seasonal_ingredients(recipe)
    if not ingredients:
        return "No main seasonal produce detected."
    return "Main produce: " + ", ".join(ingredients) + "."


def build_recipe_tag_rows(
    recipes: list[RecipeRecord],
    *,
    tag_filter: str = "all",
    diet_filter: str = "all",
    season_filter: str = "all",
) -> list[Any]:
    rows: list[Any] = []
    current_month = current_uk_month()
    for recipe in sorted(recipes, key=lambda item: (item.title.casefold(), item.cookbook_title.casefold())):
        dietary_tags = effective_recipe_dietary_tags(recipe)
        ingredient_rows = ingredient_classification_rows(recipe)
        seasonal_ingredients = effective_recipe_seasonal_ingredients(recipe)
        seasonal_months = effective_recipe_seasonal_months(recipe)
        tag_source = recipe_tag_source(recipe)
        in_season_now = current_month in seasonal_months
        metadata = recipe.source.metadata or {}
        row = SimpleNamespace(
            recipe=recipe,
            dietary_tags=dietary_tags,
            is_vegetarian="vegetarian" in dietary_tags,
            is_pescatarian="pescatarian" in dietary_tags,
            dietary_is_auto=_dietary_override(recipe) is None,
            dietary_explanation=dietary_tag_explanation(recipe),
            ingredient_classification_rows=ingredient_rows,
            ingredient_classification_summary=ingredient_classification_summary(ingredient_rows),
            seasonal_ingredients=seasonal_ingredients,
            seasonal_ingredients_text=", ".join(seasonal_ingredients),
            seasonal_months=seasonal_months,
            seasonal_month_labels=seasonal_month_labels(seasonal_months),
            seasonal_is_auto=_seasonal_months_override(recipe) is None and _seasonal_ingredients_override(recipe) is None,
            seasonality_explanation=seasonality_explanation(recipe),
            in_season_now=in_season_now,
            tag_source=tag_source,
            note=str(metadata.get("tag_review_note") or ""),
            reviewed_at=str(metadata.get("tag_reviewed_at") or ""),
            needs_review=recipe.review.status == "needs_review" or bool(recipe.extraction.needs_review_reasons),
        )
        if tag_filter == "manual" and tag_source == "auto":
            continue
        if tag_filter == "needs-review" and not row.needs_review:
            continue
        if tag_filter == "vegetarian" and not row.is_vegetarian:
            continue
        if tag_filter == "pescatarian" and not row.is_pescatarian:
            continue
        if tag_filter == "in-season" and not row.in_season_now:
            continue
        if tag_filter == "no-seasonal-produce" and row.seasonal_ingredients:
            continue
        if diet_filter != "all" and diet_filter not in row.dietary_tags:
            continue
        if season_filter == "in-season" and not row.in_season_now:
            continue
        if season_filter == "no-seasonal-produce" and row.seasonal_ingredients:
            continue
        if season_filter.startswith("month-"):
            try:
                month = int(season_filter.removeprefix("month-"))
            except ValueError:
                month = 0
            if month not in row.seasonal_months:
                continue
        rows.append(row)
    return rows


def load_meal_plan_document(repository: LibraryRepository) -> tuple[MealPlanDocument, list[str]]:
    recipe_references = repository.list_recipe_references()
    meal_plan = load_or_import_meal_plan(
        BASE_DIR.parent,
        recipe_references,
        week_limit=DEFAULT_IMPORT_WEEK_LIMIT,
        source_path=REDIS_MEAL_PLAN_SOURCE,
        load_payload=repository.load_meal_plan_payload,
        save_payload=repository.save_meal_plan_payload,
    )
    populate_week_shopping_lists(meal_plan, repository.list_recipes())
    recipe_options = [
        recipe_option_value(recipe)
        for recipe in sorted(
            recipe_references,
            key=lambda item: (item.title.casefold(), item.cookbook_title.casefold()),
        )
    ]
    return meal_plan, recipe_options


def enrich_cookbooks_for_management(
    repository: LibraryRepository,
    cookbooks: list[Any],
) -> list[Any]:
    enriched: list[Any] = []

    for cookbook in cookbooks:
        recipes = repository.list_recipes(cookbook_id=cookbook.id)
        photo_recipe_count = sum(1 for recipe in recipes if recipe.images)
        photo_recipe_percent = round((photo_recipe_count / len(recipes)) * 100) if recipes else 0

        tag_counts = Counter()
        extraction_reason_counts = Counter()
        for recipe in recipes:
            raw_tags = recipe.source.metadata.get("tags") or []
            if not isinstance(raw_tags, list):
                raw_tags = []

            cleaned_tags = {str(tag).strip() for tag in raw_tags if str(tag).strip()}
            tag_counts.update(cleaned_tags)
            extraction_reason_counts.update(set(recipe.extraction.needs_review_reasons))

        enriched.append(
            SimpleNamespace(
                **cookbook.model_dump(),
                photo_recipe_percent=photo_recipe_percent,
                tag_counts=sorted(
                    tag_counts.items(),
                    key=lambda item: (-item[1], item[0]),
                ),
                extraction_reason_counts=sorted(
                    extraction_reason_counts.items(),
                    key=lambda item: (-item[1], item[0]),
                ),
            )
        )

    return enriched


def sanitize_sort_value(sort: str | None) -> str:
    allowed = {option["value"] for option in cookbook_sort_options()}
    value = (sort or "title").strip()
    return value if value in allowed else "title"


def preferred_recipe_image_indexes(
    recipe: RecipeRecord,
    cookbook: Any | None = None,
) -> list[int]:
    indexes = list(range(len(recipe.images)))
    if len(indexes) < 2:
        return indexes

    cookbook_parts = [
        recipe.cookbook_title,
        recipe.source.metadata.get("cookbook_filename", ""),
    ]
    if cookbook:
        cookbook_parts.extend(
            [
                getattr(cookbook, "title", "") or "",
                getattr(cookbook, "filename", "") or "",
                getattr(cookbook, "collection_slug", "") or "",
            ]
        )

    cookbook_text = " ".join(part.strip().lower() for part in cookbook_parts if part)
    if "jamie" in cookbook_text and "christmas" in cookbook_text:
        return [1, 0, *indexes[2:]]

    return indexes


templates.env.globals["preferred_recipe_image_indexes"] = preferred_recipe_image_indexes


def _compact_metadata_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _split_metadata_paragraphs(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    parts = [
        re.sub(r"[ \t]+", " ", part).strip()
        for part in re.split(r"\n\s*\n+", normalized)
        if part.strip()
    ]
    if len(parts) > 1:
        return parts
    compact = _compact_metadata_text(normalized)
    return [compact] if compact else []


def _metadata_list_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _compact_metadata_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _metadata_label(key: str) -> str:
    return RECIPE_METADATA_LABELS.get(key, key.replace("_", " ").strip().title())


def build_recipe_page_metadata(recipe: RecipeRecord) -> SimpleNamespace:
    source_metadata = dict(recipe.source.metadata or {})
    summary_items: list[dict[str, str]] = []
    text_blocks: list[dict[str, Any]] = []
    list_blocks: list[dict[str, list[str]]] = []
    sections: list[dict[str, Any]] = []
    used_keys: set[str] = set()

    for key in RECIPE_METADATA_PRIORITY:
        value = _compact_metadata_text(source_metadata.get(key))
        if not value:
            continue
        summary_items.append({"label": _metadata_label(key), "value": value})
        used_keys.add(key)

    timing_parts: list[str] = []
    for key, prefix in (("total_time", "Total"), ("prep_time", "Prep"), ("cook_time", "Cook")):
        value = _compact_metadata_text(source_metadata.get(key))
        if not value:
            continue
        timing_parts.append(f"{prefix} {value}")
        used_keys.add(key)
    if timing_parts:
        summary_items.append({"label": "Timing", "value": " · ".join(timing_parts)})

    intro_value = source_metadata.get("intro") or source_metadata.get("introduction") or source_metadata.get("headnote")
    intro_paragraphs = _split_metadata_paragraphs(intro_value)
    intro_heading = "Introduction"
    if intro_paragraphs:
        used_keys.add("intro")
        used_keys.add("introduction")
        used_keys.add("headnote")
    else:
        excerpt = _compact_metadata_text(recipe.source.excerpt)
        if excerpt and excerpt.casefold() != recipe.title.strip().casefold():
            intro_paragraphs = [excerpt]
            intro_heading = "Excerpt"

    notes = _metadata_list_items(source_metadata.get("preparation_notes"))
    if notes:
        list_blocks.append({"heading": "Notes", "items": notes})
        used_keys.add("preparation_notes")

    raw_sections = source_metadata.get("supplemental_sections")
    if isinstance(raw_sections, list):
        normalized_sections: list[dict[str, Any]] = []
        for section in raw_sections:
            if not isinstance(section, dict):
                continue
            heading = _compact_metadata_text(section.get("heading"))
            lines = _metadata_list_items(section.get("lines"))
            if not heading or not lines:
                continue
            normalized_sections.append({"heading": heading, "lines": lines})
        if normalized_sections:
            sections = normalized_sections
            used_keys.add("supplemental_sections")

    for key, value in source_metadata.items():
        if key in used_keys:
            continue
        if key in RECIPE_METADATA_TEXT_BLOCK_KEYS:
            paragraphs = _split_metadata_paragraphs(value)
            if paragraphs:
                text_blocks.append({"heading": _metadata_label(key), "paragraphs": paragraphs})
                continue

        scalar = _compact_metadata_text(value)
        if scalar:
            summary_items.append({"label": _metadata_label(key), "value": scalar})
            continue

        items = _metadata_list_items(value)
        if items:
            list_blocks.append({"heading": _metadata_label(key), "items": items})

    return SimpleNamespace(
        summary_items=summary_items,
        intro_heading=intro_heading,
        intro_paragraphs=intro_paragraphs,
        text_blocks=text_blocks,
        list_blocks=list_blocks,
        sections=sections,
    )


def normalize_search_ingredients(values: list[str] | None) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in (value or "").split(","):
            cleaned = canonicalize_ingredient_name(part.strip())
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
    return normalized


def normalize_dietary_filter(value: str | None) -> str:
    allowed = {option["value"] for option in DIETARY_FILTER_OPTIONS}
    cleaned = (value or "").strip().casefold()
    return cleaned if cleaned in allowed else ""


def normalize_duration_filter(value: str | None) -> str:
    allowed = {option["value"] for option in DURATION_FILTER_OPTIONS}
    cleaned = (value or "").strip().casefold()
    return cleaned if cleaned in allowed else ""


def normalize_season_filter(value: str | None) -> str:
    allowed = {option["value"] for option in SEASON_FILTER_OPTIONS}
    cleaned = (value or "").strip().casefold()
    return cleaned if cleaned in allowed else ""


def _recipe_ingredient_text(recipe: RecipeRecord) -> str:
    parts: list[str] = []
    for ingredient in recipe.ingredients:
        parts.extend(
            [
                ingredient.raw,
                ingredient.normalized_name,
                ingredient.canonical_name or "",
                ingredient.item or "",
            ]
        )
    parts.extend(recipe.ingredient_names)
    return " ".join(part for part in parts if part).casefold()


def recipe_dietary_tags(recipe: RecipeRecord) -> list[str]:
    ingredient_text = SEAFOOD_FALSE_POSITIVE_PATTERN.sub(" ", _recipe_ingredient_text(recipe))
    has_land_meat = bool(LAND_MEAT_PATTERN.search(ingredient_text))
    has_seafood = bool(SEAFOOD_PATTERN.search(ingredient_text))

    if has_land_meat:
        return []
    if has_seafood:
        return ["pescatarian"]
    return ["vegetarian", "pescatarian"]


def _duration_text_to_minutes(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().casefold()
    if not text:
        return None
    iso_match = re.fullmatch(
        r"p(?:t)?(?:(?P<hours>\d+(?:\.\d+)?)h)?(?:(?P<minutes>\d+(?:\.\d+)?)m)?",
        text.replace(" ", ""),
    )
    if iso_match and (iso_match.group("hours") or iso_match.group("minutes")):
        hours = float(iso_match.group("hours") or 0)
        minutes = float(iso_match.group("minutes") or 0)
        return round(hours * 60 + minutes)

    total = 0.0
    found = False
    for match in re.finditer(
        r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>hours?|hrs?|hr|h|minutes?|mins?|min|m)\b",
        text,
    ):
        found = True
        amount = float(match.group("value"))
        unit = match.group("unit")
        total += amount * 60 if unit.startswith(("h", "hr")) else amount
    if found:
        return round(total)

    compact_match = re.fullmatch(r"(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?", text)
    if compact_match and (compact_match.group("hours") or compact_match.group("minutes")):
        return int(compact_match.group("hours") or 0) * 60 + int(compact_match.group("minutes") or 0)

    numeric_match = re.search(r"\d+", text)
    return int(numeric_match.group(0)) if numeric_match else None


def recipe_duration_minutes(recipe: RecipeRecord) -> int | None:
    source_metadata = recipe.source.metadata or {}
    total_minutes = _duration_text_to_minutes(source_metadata.get("total_time"))
    if total_minutes is not None:
        return total_minutes

    parts = [
        _duration_text_to_minutes(source_metadata.get("prep_time")),
        _duration_text_to_minutes(source_metadata.get("cook_time")),
    ]
    known_parts = [part for part in parts if part is not None]
    if known_parts:
        return sum(known_parts)
    return None


def recipe_matches_duration_filter(recipe: RecipeRecord, duration_filter: str) -> bool:
    if not duration_filter:
        return True
    minutes = recipe_duration_minutes(recipe)
    if minutes is None:
        return False
    if duration_filter == "under-30":
        return minutes <= 30
    if duration_filter == "under-60":
        return minutes <= 60
    if duration_filter == "under-120":
        return minutes <= 120
    if duration_filter == "over-120":
        return minutes > 120
    return True


def recipe_matches_dietary_filter(recipe: RecipeRecord, dietary_filter: str) -> bool:
    if not dietary_filter:
        return True
    return dietary_filter in effective_recipe_dietary_tags(recipe)


def recipe_matches_season_filter(recipe: RecipeRecord, season_filter: str) -> bool:
    if not season_filter:
        return True
    if season_filter == "in-season":
        return effective_recipe_is_in_season(recipe)
    return True


def filter_recipe_records(
    recipes: list[RecipeRecord],
    *,
    dietary_filter: str = "",
    duration_filter: str = "",
    season_filter: str = "",
) -> list[RecipeRecord]:
    return [
        recipe
        for recipe in recipes
        if recipe_matches_dietary_filter(recipe, dietary_filter)
        and recipe_matches_duration_filter(recipe, duration_filter)
        and recipe_matches_season_filter(recipe, season_filter)
    ]


def filter_search_results(
    results: list[Any],
    *,
    dietary_filter: str = "",
    duration_filter: str = "",
    season_filter: str = "",
) -> list[Any]:
    return [
        result
        for result in results
        if recipe_matches_dietary_filter(result.recipe, dietary_filter)
        and recipe_matches_duration_filter(result.recipe, duration_filter)
        and recipe_matches_season_filter(result.recipe, season_filter)
    ]


templates.env.globals["recipe_dietary_tags"] = recipe_dietary_tags
templates.env.globals["effective_recipe_dietary_tags"] = effective_recipe_dietary_tags
templates.env.globals["recipe_duration_minutes"] = recipe_duration_minutes
templates.env.globals["recipe_is_in_season"] = recipe_is_in_season
templates.env.globals["effective_recipe_is_in_season"] = effective_recipe_is_in_season
templates.env.globals["main_seasonal_ingredients"] = main_seasonal_ingredients


def build_recipe_embeddings(settings: Settings, drafts: list[RecipeDraft]) -> list[list[float]]:
    if not drafts or not settings.openai_api_key:
        return []
    try:
        extractor = OpenAIRecipeExtractor(settings)
        return extractor.build_embeddings([draft.embedding_text() for draft in drafts])
    except Exception as exc:
        logger.warning("Could not build embeddings for uploaded recipe file: %s", exc)
        return []


def chapter_label_for_recipe(recipe: RecipeRecord, chapter_map: dict[str, str]) -> str:
    anchor = normalize_epub_path(recipe.source.anchor or "")
    anchor_path = anchor.split("#", 1)[0]
    if anchor_path and anchor_path in chapter_map:
        return chapter_map[anchor_path]
    if anchor and anchor in chapter_map:
        return chapter_map[anchor]

    title = " ".join(recipe.title.split())

    chapter_title = (recipe.source.chapter_title or "").strip()
    if chapter_title and chapter_title.lower() != title.lower():
        return chapter_title

    if recipe.source.format.lower() != "epub":
        excerpt = " ".join((recipe.source.excerpt or "").split())
        if excerpt and title:
            position = excerpt.lower().find(title.lower())
            if position > 0:
                prefix = excerpt[:position].strip(" ,:-")
                if prefix and len(prefix.split()) <= 8:
                    return prefix

    anchor_stem = Path(anchor).stem if anchor else ""
    chapter_match = re.search(r"chapter[_ -]?(\d+)$", anchor_stem, re.IGNORECASE)
    if chapter_match:
        return f"Chapter {chapter_match.group(1)}"

    return "Recipes"


def recipe_raw_text(repository: LibraryRepository, recipe: RecipeRecord) -> str:
    cookbook = repository.get_cookbook(recipe.cookbook_id)
    if not cookbook:
        return recipe.source.excerpt or ""

    try:
        file_bytes = repository.download_cookbook(cookbook.id)
    except Exception:
        return recipe.source.excerpt or ""

    source_format = (recipe.source.format or "").lower()
    suffix = Path(cookbook.filename).suffix.lower()

    if source_format == "epub" or suffix == ".epub":
        text = extract_epub_recipe_raw_text(file_bytes, recipe.source.anchor or "")
        return text or recipe.source.excerpt or ""

    if source_format == "pdf" or suffix == ".pdf":
        text = extract_pdf_recipe_raw_text(
            file_bytes,
            page_start=recipe.source.page_start,
            page_end=recipe.source.page_end,
        )
        return text or recipe.source.excerpt or ""

    return recipe.source.excerpt or ""


def extract_epub_recipe_raw_text(file_bytes: bytes, anchor: str) -> str:
    raw_anchor = (anchor or "").strip()
    item_value, _hash, fragment = raw_anchor.partition("#")
    item_path = normalize_epub_path(item_value or raw_anchor)

    with tempfile.NamedTemporaryFile(suffix=".epub") as handle:
        handle.write(file_bytes)
        handle.flush()
        book = epub.read_epub(handle.name)

    for item in book.get_items_of_type(ITEM_DOCUMENT):
        item_href = normalize_epub_path(getattr(item, "file_name", None) or item.get_name())
        if item_path and item_href != item_path:
            continue

        soup = BeautifulSoup(item.get_content(), "html.parser")
        if fragment:
            node = soup.find(id=fragment)
            if node:
                lines: list[str] = []
                current = node if getattr(node, "name", None) in {"h1", "h2", "h3", "h4", "p", "li"} else node.find_parent(["h1", "h2", "h3", "h4", "p", "li"]) or node
                while current is not None:
                    if current is not node and current.name in {"h1", "h2", "h3", "h4"}:
                        current_id = current.get("id") or next(
                            (
                                descendant.get("id")
                                for descendant in current.find_all(True)
                                if descendant.get("id")
                            ),
                            None,
                        )
                        if current_id:
                            break
                    text = current.get_text("\n", strip=True)
                    lines.extend(line for line in text.splitlines() if line)
                    current = current.find_next(["h1", "h2", "h3", "h4", "p", "li"])
                return "\n".join(lines).strip()
        return "\n".join(line for line in soup.get_text("\n", strip=True).splitlines() if line).strip()

    return ""


def extract_pdf_recipe_raw_text(
    file_bytes: bytes,
    *,
    page_start: int | None,
    page_end: int | None,
) -> str:
    document = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        start = max((page_start or 1) - 1, 0)
        end = min(page_end or page_start or document.page_count, document.page_count)
        parts: list[str] = []
        for page_number in range(start, end):
            page = document.load_page(page_number)
            text = "\n".join(
                line.strip()
                for line in page.get_text("text").splitlines()
                if line.strip()
            ).strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts).strip()
    finally:
        document.close()


def group_recipes_by_chapter(
    recipes: list[RecipeRecord],
    chapter_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    groups: dict[str, list[RecipeRecord]] = {}
    chapter_map = chapter_map or {}
    for recipe in recipes:
        label = chapter_label_for_recipe(recipe, chapter_map)
        groups.setdefault(label, []).append(recipe)
    return [{"title": label, "recipes": items} for label, items in groups.items()]


def build_recipe_sections_from_source_toc(
    recipes: list[RecipeRecord],
    source_table_of_contents: list[CookbookTocEntry],
) -> list[dict[str, Any]]:
    if not recipes or not source_table_of_contents:
        return []

    recipes_by_path: dict[str, list[RecipeRecord]] = defaultdict(list)
    for recipe in recipes:
        if recipe.source.format.lower() != "epub":
            continue
        path = normalize_epub_path(recipe.source.anchor or "").split("#", 1)[0]
        if path:
            recipes_by_path[path].append(recipe)

    if not recipes_by_path:
        return []

    nodes: list[dict[str, Any]] = []

    def collect_paths(entry: CookbookTocEntry, depth: int) -> set[str]:
        own_path = normalize_epub_path(entry.href)
        descendant_paths: set[str] = set()
        if own_path in recipes_by_path:
            descendant_paths.add(own_path)
        for child in entry.children:
            descendant_paths.update(collect_paths(child, depth + 1))
        nodes.append({"entry": entry, "depth": depth, "paths": descendant_paths})
        return descendant_paths

    for entry in source_table_of_contents:
        collect_paths(entry, 0)

    candidate_depths = sorted(
        {int(node["depth"]) for node in nodes if node["paths"] and len(node["paths"]) > 1}
    )
    if not candidate_depths:
        return []

    non_root_depths = [depth for depth in candidate_depths if depth > 0]
    target_depth = non_root_depths[0] if non_root_depths else candidate_depths[0]

    ordered_sections: list[dict[str, Any]] = []
    assigned_recipe_ids: set[str] = set()
    for node in nodes:
        if node["depth"] != target_depth or not node["paths"]:
            continue
        section_recipes: list[RecipeRecord] = []
        for recipe in recipes:
            if recipe.id in assigned_recipe_ids:
                continue
            path = normalize_epub_path(recipe.source.anchor or "").split("#", 1)[0]
            if path in node["paths"]:
                section_recipes.append(recipe)
                assigned_recipe_ids.add(recipe.id)
        if section_recipes:
            ordered_sections.append({"title": str(node["entry"].label), "recipes": section_recipes})

    for recipe in recipes:
        if recipe.id in assigned_recipe_ids:
            continue
        ordered_sections.append({"title": chapter_label_for_recipe(recipe, {}), "recipes": [recipe]})

    return ordered_sections


def flatten_cookbook_toc_labels(entries: list[CookbookTocEntry]) -> list[str]:
    labels: list[str] = []
    for entry in entries:
        label = " ".join((entry.label or "").split()).strip()
        if label:
            labels.append(label)
        labels.extend(flatten_cookbook_toc_labels(entry.children))
    return labels


def order_recipe_sections(
    recipe_sections: list[dict[str, Any]],
    source_table_of_contents: list[CookbookTocEntry],
) -> list[dict[str, Any]]:
    if not recipe_sections or not source_table_of_contents:
        return recipe_sections

    pending_sections: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for section in recipe_sections:
        pending_sections[str(section.get("title", ""))].append(section)

    ordered_sections: list[dict[str, Any]] = []
    for label in flatten_cookbook_toc_labels(source_table_of_contents):
        bucket = pending_sections.get(label)
        if bucket:
            ordered_sections.append(bucket.popleft())

    for section in recipe_sections:
        title = str(section.get("title", ""))
        bucket = pending_sections.get(title)
        if bucket and bucket[0] is section:
            ordered_sections.append(bucket.popleft())

    return ordered_sections


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    notice: str | None = Query(default=None),
) -> HTMLResponse:
    return await meal_plan_page(request=request, notice=notice)


@app.get("/library", response_class=HTMLResponse)
async def library_page(
    request: Request,
    notice: str | None = Query(default=None),
    sort: str = Query(default="title"),
) -> HTMLResponse:
    repository = get_repository(request)
    sort_value = sanitize_sort_value(sort)
    context = common_template_context(request, notice=notice)
    context.update(
        {
            "cookbooks": repository.list_cookbooks(
                sort_by=sort_value,
                include_collection_items=False,
            ),
            "total_recipe_count": repository.count_recipes(),
            "cookbook_sort": sort_value,
            "cookbook_sort_options": cookbook_sort_options(),
            "recipe_collections": repository.list_recipe_collections(),
            "brand_image_urls": {
                slug: str(request.url_for("brand_image", image_slug=slug))
                for slug in BRAND_IMAGE_MAP
            },
        }
    )
    return templates.TemplateResponse(request=request, name="index.html", context=context)


@app.get("/brand-images/{image_slug}")
async def brand_image(image_slug: str) -> FileResponse:
    image_path = BRAND_IMAGE_MAP.get(image_slug)
    if not image_path or not image_path.exists():
        raise HTTPException(status_code=404, detail="Brand image not found.")
    return FileResponse(image_path)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> RedirectResponse:
    return RedirectResponse(url="/brand-images/cottage", status_code=307)


@app.get("/library/manage", response_class=HTMLResponse)
async def manage_cookbooks_page(
    request: Request,
    notice: str | None = Query(default=None),
    sort: str = Query(default="title"),
) -> HTMLResponse:
    repository = get_repository(request)
    sort_value = sanitize_sort_value(sort)
    cookbooks = enrich_cookbooks_for_management(
        repository,
        repository.list_cookbooks(sort_by=sort_value, include_collection_items=True),
    )
    context = common_template_context(request, notice=notice)
    context.update(
        {
            "cookbooks": cookbooks,
            "cookbook_groups": cookbook_management_groups(cookbooks),
            "cookbook_sort": sort_value,
            "cookbook_sort_options": cookbook_sort_options(),
        }
    )
    return templates.TemplateResponse(request=request, name="manage_cookbooks.html", context=context)


@app.get("/library/tags", response_class=HTMLResponse)
async def manage_recipe_tags_page(
    request: Request,
    notice: str | None = Query(default=None),
    tag_filter: str | None = Query(default="all", alias="filter"),
    collection: str | None = Query(default="all"),
    cookbook: str | None = Query(default="all"),
    diet: str | None = Query(default="all"),
    season: str | None = Query(default="all"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> HTMLResponse:
    repository = get_repository(request)
    cookbooks = repository.list_cookbooks(include_collection_items=True)
    collections = repository.list_recipe_collections()
    selected_filter = normalize_tag_review_filter(tag_filter)
    selected_collection = normalize_tag_collection_filter(collection, cookbooks, collections)
    selected_cookbook = normalize_tag_cookbook_filter(cookbook, cookbooks)
    selected_diet = normalize_tag_diet_filter(diet)
    selected_season = normalize_tag_season_filter(season)
    cookbook_collection_map = {
        cookbook.id: cookbook.collection_slug or ""
        for cookbook in cookbooks
    }
    if selected_cookbook != "all" and selected_collection != "all":
        cookbook_collection = cookbook_collection_map.get(selected_cookbook, "")
        if (selected_collection == "books" and cookbook_collection) or (
            selected_collection != "books" and cookbook_collection != selected_collection
        ):
            selected_cookbook = "all"
    recipes = filter_recipes_for_tag_management(
        repository.list_recipes(),
        cookbook_filter=selected_cookbook,
        collection_filter=selected_collection,
        cookbook_collection_map=cookbook_collection_map,
    )
    rows = build_recipe_tag_rows(
        recipes,
        tag_filter=selected_filter,
        diet_filter=selected_diet,
        season_filter=selected_season,
    )
    total_rows = len(rows)
    selected_page_size = clamp_tag_page_size(page_size)
    page_count = max(1, (total_rows + selected_page_size - 1) // selected_page_size)
    selected_page = min(max(page, 1), page_count)
    page_start = (selected_page - 1) * selected_page_size
    page_end = page_start + selected_page_size
    page_rows = rows[page_start:page_end]
    page_url_params = {
        "filter": selected_filter,
        "collection": selected_collection,
        "cookbook": selected_cookbook,
        "diet": selected_diet,
        "season": selected_season,
        "page_size": selected_page_size,
    }
    page_base_url = str(request.url_for("manage_recipe_tags_page"))

    def tag_page_url(page_number: int) -> str:
        return f"{page_base_url}?{urlencode({**page_url_params, 'page': page_number})}"

    cookbook_options = recipe_tag_cookbook_options(cookbooks, collection_filter=selected_collection)
    context = common_template_context(request, notice=notice)
    context.update(
        {
            "recipe_tag_rows": page_rows,
            "recipe_tag_total_rows": total_rows,
            "recipe_tag_page": selected_page,
            "recipe_tag_page_count": page_count,
            "recipe_tag_page_size": selected_page_size,
            "recipe_tag_page_size_options": (25, 50, 100, 200),
            "recipe_tag_page_start": page_start + 1 if total_rows else 0,
            "recipe_tag_page_end": min(page_end, total_rows),
            "recipe_tag_previous_page_url": tag_page_url(selected_page - 1) if selected_page > 1 else "",
            "recipe_tag_next_page_url": tag_page_url(selected_page + 1) if selected_page < page_count else "",
            "recipe_tag_page_links": [
                {"number": page_number, "url": tag_page_url(page_number)}
                for page_number in tag_pagination_window(selected_page, page_count)
            ],
            "recipe_tag_filter": selected_filter,
            "recipe_tag_filter_options": TAG_REVIEW_FILTER_OPTIONS,
            "recipe_tag_collection": selected_collection,
            "recipe_tag_collection_options": recipe_tag_collection_options(collections),
            "recipe_tag_cookbook": selected_cookbook,
            "recipe_tag_cookbook_options": cookbook_options,
            "recipe_tag_diet": selected_diet,
            "recipe_tag_diet_options": (
                {"value": "all", "label": "Any diet"},
                *DIETARY_FILTER_OPTIONS,
            ),
            "recipe_tag_season": selected_season,
            "recipe_tag_season_options": TAG_SEASON_FILTER_OPTIONS,
            "month_options": MONTH_OPTIONS,
            "current_uk_month": current_uk_month(),
        }
    )
    return templates.TemplateResponse(request=request, name="manage_recipe_tags.html", context=context)


@app.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: str | None = Query(default=None),
    ingredient: list[str] | None = Query(default=None),
    diet: str | None = Query(default=None),
    duration: str | None = Query(default=None),
    season: str | None = Query(default=None),
    notice: str | None = Query(default=None),
) -> HTMLResponse:
    repository = get_repository(request)
    selected_ingredients = normalize_search_ingredients(ingredient)
    selected_diet = normalize_dietary_filter(diet)
    selected_duration = normalize_duration_filter(duration)
    selected_season = normalize_season_filter(season)
    if q or selected_ingredients:
        results = repository.search_recipes(query=q, ingredients=selected_ingredients)
    elif selected_diet or selected_duration or selected_season:
        results = [
            SearchResultRecord(recipe=recipe, score=1.0, keyword_score=0.0, semantic_score=0.0)
            for recipe in repository.list_recipes()
        ]
    else:
        results = []
    results = filter_search_results(
        results,
        dietary_filter=selected_diet,
        duration_filter=selected_duration,
        season_filter=selected_season,
    )
    context = common_template_context(request, notice=notice)
    context.update(
        {
            "ingredients": repository.list_ingredients(ingredients=selected_ingredients),
            "dietary_filter_options": DIETARY_FILTER_OPTIONS,
            "duration_filter_options": DURATION_FILTER_OPTIONS,
            "season_filter_options": SEASON_FILTER_OPTIONS,
            "search_query": q or "",
            "search_ingredients": selected_ingredients,
            "search_diet": selected_diet,
            "search_duration": selected_duration,
            "search_season": selected_season,
            "current_uk_month": current_uk_month(),
            "search_results": results,
        }
    )
    return templates.TemplateResponse(request=request, name="search.html", context=context)


@app.get("/meal-plan", response_class=HTMLResponse)
async def meal_plan_page(
    request: Request,
    notice: str | None = Query(default=None),
) -> HTMLResponse:
    repository = get_repository(request)
    meal_plan, recipe_options = load_meal_plan_document(repository)
    context = common_template_context(request, notice=notice)
    context.update(
        {
            "meal_plan": meal_plan,
            "recipe_options": recipe_options,
            "weekday_options": WEEKDAY_LABELS,
            "meal_options": MEAL_LABELS,
        }
    )
    return templates.TemplateResponse(request=request, name="meal_plan.html", context=context)


@app.get("/blog", response_class=RedirectResponse)
async def blog_index_page(
    request: Request,
) -> RedirectResponse:
    return RedirectResponse(url=request.url_for("index"), status_code=307)


@app.get("/blog/{post_slug}", response_class=RedirectResponse)
async def blog_post_page(
    request: Request,
    post_slug: str,
) -> RedirectResponse:
    return RedirectResponse(url=request.url_for("index"), status_code=307)


@app.post("/meal-plan")
async def update_meal_plan_form(
    request: Request,
) -> Response:
    repository = get_repository(request)
    form = await request.form()
    planner_action = str(form.get("planner_action", "autosave")).strip().lower()
    recipes = repository.list_recipe_references()
    meal_plan = parse_meal_plan_form(
        form,
        recipes,
        base_dir=BASE_DIR.parent,
        source_path=REDIS_MEAL_PLAN_SOURCE,
    )

    if planner_action == "add_week":
        append_blank_week(meal_plan)
    elif planner_action.startswith("add_row:"):
        week_id = planner_action.split(":", 1)[1]
        target_week = next((week for week in meal_plan.weeks if week.id == week_id), None)
        if target_week:
            append_blank_row(target_week)
    elif planner_action.startswith("remove:"):
        remove_week(meal_plan, planner_action.split(":", 1)[1])

    save_meal_plan(
        BASE_DIR.parent,
        meal_plan,
        save_payload=repository.save_meal_plan_payload,
    )

    if planner_action == "add_week":
        notice_text = "Added a new week."
    elif planner_action.startswith("add_row:"):
        notice_text = "Added a new row."
    elif planner_action.startswith("remove:"):
        notice_text = "Removed the week."
    else:
        notice_text = "Meal plan updated."

    wants_json = (
        request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        or "application/json" in request.headers.get("accept", "").lower()
    )
    if wants_json:
        return JSONResponse(
            {
                "ok": True,
                "notice": notice_text,
                "stats": {
                    "weeks": len(meal_plan.weeks),
                    "slot_count": meal_plan.slot_count,
                    "linked_slot_count": meal_plan.linked_slot_count,
                    "completed_slot_count": meal_plan.completed_slot_count,
                },
            }
        )
    return redirect_with_notice("/meal-plan", notice_text)


@app.post("/recipes/{recipe_id}/meal-plan/latest")
async def add_recipe_to_latest_meal_plan_week(
    recipe_id: str,
    request: Request,
    return_to: str | None = Form(default=None),
) -> Response:
    repository = get_repository(request)
    recipe = repository.get_recipe(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found.")

    recipes = repository.list_recipe_references()
    meal_plan = load_or_import_meal_plan(
        BASE_DIR.parent,
        recipes,
        week_limit=DEFAULT_IMPORT_WEEK_LIMIT,
        source_path=REDIS_MEAL_PLAN_SOURCE,
        load_payload=repository.load_meal_plan_payload,
        save_payload=repository.save_meal_plan_payload,
    )
    added_row = add_recipe_to_latest_week(meal_plan, recipe)
    save_meal_plan(
        BASE_DIR.parent,
        meal_plan,
        save_payload=repository.save_meal_plan_payload,
    )

    notice = f"Added {recipe.title} to {meal_plan.weeks[0].title}."
    fallback = str(request.url_for("recipe_page", recipe_id=recipe_id))
    destination = safe_redirect_target(return_to, fallback)
    accepts_json = "application/json" in request.headers.get("accept", "").lower()
    is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
    if accepts_json or is_ajax:
        return JSONResponse(
            {
                "ok": True,
                "recipe_id": recipe.id,
                "week_id": meal_plan.weeks[0].id,
                "week_title": meal_plan.weeks[0].title,
                "row_id": added_row.id,
                "notice": notice,
            }
        )
    return redirect_with_notice(destination, notice)


@app.get("/collections/{collection_slug}", response_class=HTMLResponse)
async def collection_page(
    collection_slug: str,
    request: Request,
    notice: str | None = Query(default=None),
) -> HTMLResponse:
    repository = get_repository(request)
    collection = repository.get_recipe_collection(collection_slug)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found.")

    context = common_template_context(request, notice=notice)
    context.update(
        {
            "collection": collection,
            "cookbooks": repository.list_cookbooks_for_collection(collection_slug),
            "recipes": repository.list_recipes_for_collection(collection_slug),
        }
    )
    return templates.TemplateResponse(request=request, name="collection.html", context=context)


@app.get("/cookbooks/{cookbook_id}", response_class=HTMLResponse)
async def cookbook_page(
    cookbook_id: str,
    request: Request,
    notice: str | None = Query(default=None),
) -> HTMLResponse:
    repository = get_repository(request)
    cookbook = repository.get_cookbook(cookbook_id)
    if not cookbook:
        raise HTTPException(status_code=404, detail="Cookbook not found.")

    recipes = repository.list_recipes(cookbook_id=cookbook_id)
    recipe_paths = {
        normalize_epub_path(recipe.source.anchor or "")
        for recipe in recipes
        if recipe.source.format.lower() == "epub"
    } - {""}
    chapter_map: dict[str, str] = {}
    if Path(cookbook.filename).suffix.lower() == ".epub":
        try:
            chapter_map = build_epub_chapter_map(
                repository.download_cookbook(cookbook.id),
                recipe_paths=recipe_paths,
            )
        except Exception:
            chapter_map = {}
    context = common_template_context(request, notice=notice)
    recipe_sections = build_recipe_sections_from_source_toc(recipes, cookbook.table_of_contents)
    if not recipe_sections:
        recipe_sections = order_recipe_sections(
            group_recipes_by_chapter(recipes, chapter_map),
            cookbook.table_of_contents,
        )
    context.update(
        {
            "cookbook": cookbook,
            "recipes": recipes,
            "recipe_sections": recipe_sections,
            "source_table_of_contents": cookbook.table_of_contents,
        }
    )
    return templates.TemplateResponse(request=request, name="cookbook.html", context=context)


@app.post("/cookbooks/{cookbook_id}/metadata")
async def update_cookbook_metadata_form(
    cookbook_id: str,
    request: Request,
    title: str = Form(...),
    author: str | None = Form(default=None),
    cuisine: str | None = Form(default=None),
    published_at: str | None = Form(default=None),
) -> RedirectResponse:
    repository = get_repository(request)
    cookbook = repository.update_cookbook_metadata(
        cookbook_id,
        title=title,
        author=author,
        cuisine=cuisine,
        published_at=published_at,
    )
    if not cookbook:
        raise HTTPException(status_code=404, detail="Cookbook not found.")
    return redirect_with_notice(f"/cookbooks/{cookbook_id}", "Metadata updated.")


@app.post("/library/manage")
async def bulk_update_cookbooks_form(request: Request) -> RedirectResponse:
    repository = get_repository(request)
    form = await request.form()
    sort_value = sanitize_sort_value(str(form.get("sort", "title")))
    cookbook_ids = form.getlist("cookbook_id")
    titles = form.getlist("title")
    authors = form.getlist("author")
    cuisines = form.getlist("cuisine")
    published_dates = form.getlist("published_at")

    total = len(cookbook_ids)
    if not total or not all(
        len(values) == total for values in (titles, authors, cuisines, published_dates)
    ):
        raise HTTPException(status_code=400, detail="Metadata form payload is invalid.")

    updated = 0
    for index, cookbook_id in enumerate(cookbook_ids):
        cookbook = repository.update_cookbook_metadata(
            cookbook_id,
            title=titles[index],
            author=authors[index],
            cuisine=cuisines[index],
            published_at=published_dates[index],
        )
        if cookbook:
            updated += 1

    return redirect_with_notice(
        f"/library/manage?sort={quote(sort_value)}",
        f"Updated metadata for {updated} cookbook{'s' if updated != 1 else ''}.",
    )


@app.get("/cookbooks/{cookbook_id}/cover")
async def cookbook_cover(cookbook_id: str, request: Request) -> StreamingResponse:
    repository = get_repository(request)
    payload = repository.get_cookbook_cover(cookbook_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Cover not found.")
    image_bytes, content_type = payload
    return StreamingResponse(BytesIO(image_bytes), media_type=content_type)


@app.get("/cookbooks/{cookbook_id}/file")
async def cookbook_file(cookbook_id: str, request: Request) -> StreamingResponse:
    repository = get_repository(request)
    cookbook = repository.get_cookbook(cookbook_id)
    if not cookbook:
        raise HTTPException(status_code=404, detail="Cookbook not found.")
    if cookbook.source_kind == "web":
        raise HTTPException(status_code=404, detail="Web recipe sources do not have a stored file.")

    file_bytes = repository.download_cookbook(cookbook_id)
    headers = {
        "Content-Disposition": f'inline; filename="{cookbook.filename}"',
    }
    return StreamingResponse(
        BytesIO(file_bytes),
        media_type=cookbook.content_type or "application/octet-stream",
        headers=headers,
    )


@app.get("/recipes/{recipe_id}", response_class=HTMLResponse)
async def recipe_page(
    recipe_id: str,
    request: Request,
    collection: str | None = Query(default=None),
    notice: str | None = Query(default=None),
) -> HTMLResponse:
    repository = get_repository(request)
    recipe = repository.get_recipe(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found.")

    cookbook = repository.get_cookbook(recipe.cookbook_id)
    selected_collection = repository.get_recipe_collection(collection) if collection else None
    if selected_collection:
        related_recipes = repository.list_recipes_for_collection(selected_collection.slug)
    elif cookbook and cookbook.collection_slug:
        related_recipes = repository.list_recipes_for_collection(cookbook.collection_slug)
    else:
        related_recipes = repository.list_recipes(cookbook_id=recipe.cookbook_id)
    position = next((index for index, item in enumerate(related_recipes) if item.id == recipe_id), -1)
    previous_recipe = related_recipes[position - 1] if position > 0 else None
    next_recipe = related_recipes[position + 1] if 0 <= position < len(related_recipes) - 1 else None
    back_href = (
        str(request.url_for("collection_page", collection_slug=selected_collection.slug))
        if selected_collection
        else str(request.url_for("collection_page", collection_slug=cookbook.collection_slug))
        if cookbook and cookbook.collection_slug
        else str(request.url_for("cookbook_page", cookbook_id=recipe.cookbook_id))
    )
    back_label = "Back To Collection" if selected_collection or (cookbook and cookbook.collection_slug) else "Back To Cookbook"

    context = common_template_context(request, notice=notice)
    context.update(
        {
            "cookbook": cookbook,
            "recipe": recipe,
            "recipe_page_metadata": build_recipe_page_metadata(recipe),
            "recipe_image_indexes": preferred_recipe_image_indexes(recipe, cookbook),
            "previous_recipe": previous_recipe,
            "next_recipe": next_recipe,
            "selected_collection_slug": selected_collection.slug if selected_collection else None,
            "back_href": back_href,
            "back_label": back_label,
        }
    )
    return templates.TemplateResponse(request=request, name="recipe.html", context=context)


@app.post("/recipes/{recipe_id}/favorite")
async def toggle_recipe_favorite(
    recipe_id: str,
    request: Request,
    is_favorite: str = Form(...),
    return_to: str | None = Form(default=None),
) -> Response:
    repository = get_repository(request)
    recipe = repository.set_recipe_favorite(
        recipe_id,
        is_favorite=is_favorite.strip().lower() == "true",
    )
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found.")

    fallback = str(request.url_for("recipe_page", recipe_id=recipe_id))
    destination = safe_redirect_target(return_to, fallback)
    notice = "Added to favourites." if recipe.is_favorite else "Removed from favourites."
    accepts_json = "application/json" in request.headers.get("accept", "").lower()
    is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
    if accepts_json or is_ajax:
        return JSONResponse(
            {
                "recipe_id": recipe.id,
                "toggle_kind": "favorite",
                "is_active": recipe.is_favorite,
                "next_value": "false" if recipe.is_favorite else "true",
                "label": "Remove from favourites" if recipe.is_favorite else "Add to favourites",
                "notice": notice,
            }
        )
    return redirect_with_notice(destination, notice)


@app.post("/recipes/{recipe_id}/want-to-try")
async def toggle_recipe_want_to_try(
    recipe_id: str,
    request: Request,
    is_want_to_try: str = Form(...),
    return_to: str | None = Form(default=None),
) -> Response:
    repository = get_repository(request)
    recipe = repository.set_recipe_want_to_try(
        recipe_id,
        is_want_to_try=is_want_to_try.strip().lower() == "true",
    )
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found.")

    fallback = str(request.url_for("recipe_page", recipe_id=recipe_id))
    destination = safe_redirect_target(return_to, fallback)
    notice = "Added to want to try." if recipe.is_want_to_try else "Removed from want to try."
    accepts_json = "application/json" in request.headers.get("accept", "").lower()
    is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
    if accepts_json or is_ajax:
        return JSONResponse(
            {
                "recipe_id": recipe.id,
                "toggle_kind": "want-to-try",
                "is_active": recipe.is_want_to_try,
                "next_value": "false" if recipe.is_want_to_try else "true",
                "label": "Remove from want to try" if recipe.is_want_to_try else "Add to want to try",
                "notice": notice,
            }
        )
    return redirect_with_notice(destination, notice)


@app.get("/recipes/{recipe_id}/raw-text", response_class=HTMLResponse)
async def recipe_raw_text_page(
    recipe_id: str,
    request: Request,
    notice: str | None = Query(default=None),
) -> HTMLResponse:
    repository = get_repository(request)
    recipe = repository.get_recipe(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    cookbook = repository.get_cookbook(recipe.cookbook_id)
    back_href = (
        str(request.url_for("collection_page", collection_slug=cookbook.collection_slug))
        if cookbook and cookbook.collection_slug
        else str(request.url_for("cookbook_page", cookbook_id=recipe.cookbook_id))
    )
    back_label = "Back To Collection" if cookbook and cookbook.collection_slug else "Back To Cookbook"

    context = common_template_context(request, notice=notice)
    context.update(
        {
            "recipe": recipe,
            "raw_text": recipe_raw_text(repository, recipe),
            "back_href": back_href,
            "back_label": back_label,
        }
    )
    return templates.TemplateResponse(request=request, name="recipe_raw_text.html", context=context)


@app.post("/upload")
async def upload_cookbooks_form(
    request: Request,
    files: list[UploadFile] = File(...),
    collection_slug: str | None = Form(default=None),
) -> RedirectResponse:
    repository = get_repository(request)
    if not files:
        raise HTTPException(status_code=400, detail="No files supplied.")

    collection = repository.get_recipe_collection(collection_slug) if collection_slug else None
    if collection_slug and not collection:
        raise HTTPException(status_code=404, detail="Collection not found.")

    uploaded = 0
    for upload in files:
        if collection_slug:
            extension = Path(upload.filename or "").suffix.lower()
            if extension != ".pdf":
                return redirect_with_notice(
                    f"/collections/{collection_slug}",
                    "Collections only accept PDF files.",
                )
        try:
            cookbook = repository.upload_cookbook(upload, collection_slug=collection_slug)
        except ValueError as exc:
            redirect_path = f"/collections/{collection_slug}" if collection_slug else "/"
            return redirect_with_notice(redirect_path, str(exc))
        if collection_slug == "nytimes":
            try:
                file_bytes = repository.download_cookbook(cookbook.id)
                parsed = extract_nytimes_pdf(
                    cookbook_title=cookbook.title,
                    filename=cookbook.filename,
                    object_key=cookbook.object_key,
                    content_type=cookbook.content_type,
                    file_bytes=file_bytes,
                )
                repository.update_cookbook_metadata(
                    cookbook.id,
                    title=parsed.title,
                    author=parsed.author,
                    published_at=parsed.published_at,
                )
                embeddings = build_recipe_embeddings(get_app_settings(request), parsed.drafts)
                repository.store_extracted_recipes(cookbook.id, parsed.drafts, embeddings)
            except Exception as exc:
                logger.exception("NYTimes upload extraction failed for cookbook %s", cookbook.id)
                repository.mark_cookbook_failed(cookbook.id, str(exc))
        elif collection_slug == "jamie-oliver":
            try:
                file_bytes = repository.download_cookbook(cookbook.id)
                parsed = extract_jamie_oliver_pdf(
                    cookbook_title=cookbook.title,
                    filename=cookbook.filename,
                    object_key=cookbook.object_key,
                    content_type=cookbook.content_type,
                    file_bytes=file_bytes,
                )
                repository.update_cookbook_metadata(
                    cookbook.id,
                    title=parsed.title,
                    author=parsed.author,
                )
                embeddings = build_recipe_embeddings(get_app_settings(request), parsed.drafts)
                repository.store_extracted_recipes(cookbook.id, parsed.drafts, embeddings)
            except ValueError as exc:
                logger.info(
                    "Deleting Jamie Oliver upload %s after template mismatch: %s",
                    cookbook.id,
                    exc,
                )
                repository.delete_cookbook(cookbook.id)
                return redirect_with_notice(
                    f"/collections/{collection_slug}",
                    f"Deleted '{upload.filename or cookbook.filename}': {exc}",
                )
            except Exception as exc:
                logger.exception("Jamie Oliver upload extraction failed for cookbook %s", cookbook.id)
                repository.mark_cookbook_failed(cookbook.id, str(exc))
        elif collection_slug == "bbc-goodfood":
            try:
                file_bytes = repository.download_cookbook(cookbook.id)
                parsed = extract_bbc_goodfood_pdf(
                    cookbook_title=cookbook.title,
                    filename=cookbook.filename,
                    object_key=cookbook.object_key,
                    content_type=cookbook.content_type,
                    file_bytes=file_bytes,
                )
                repository.update_cookbook_metadata(
                    cookbook.id,
                    title=parsed.title,
                    author=parsed.author,
                )
                embeddings = build_recipe_embeddings(get_app_settings(request), parsed.drafts)
                repository.store_extracted_recipes(cookbook.id, parsed.drafts, embeddings)
            except ValueError as exc:
                logger.info(
                    "Deleting BBC Good Food upload %s after template mismatch: %s",
                    cookbook.id,
                    exc,
                )
                repository.delete_cookbook(cookbook.id)
                return redirect_with_notice(
                    f"/collections/{collection_slug}",
                    f"Deleted '{upload.filename or cookbook.filename}': {exc}",
                )
            except Exception as exc:
                logger.exception("BBC Good Food upload extraction failed for cookbook %s", cookbook.id)
                repository.mark_cookbook_failed(cookbook.id, str(exc))
        elif collection_slug == "waitrose-recipes":
            try:
                file_bytes = repository.download_cookbook(cookbook.id)
                parsed = extract_waitrose_pdf(
                    cookbook_title=cookbook.title,
                    filename=cookbook.filename,
                    object_key=cookbook.object_key,
                    content_type=cookbook.content_type,
                    file_bytes=file_bytes,
                )
                repository.update_cookbook_metadata(
                    cookbook.id,
                    title=parsed.title,
                )
                embeddings = build_recipe_embeddings(get_app_settings(request), parsed.drafts)
                repository.store_extracted_recipes(cookbook.id, parsed.drafts, embeddings)
            except ValueError as exc:
                logger.info(
                    "Deleting Waitrose upload %s after template mismatch: %s",
                    cookbook.id,
                    exc,
                )
                repository.delete_cookbook(cookbook.id)
                return redirect_with_notice(
                    f"/collections/{collection_slug}",
                    f"Deleted '{upload.filename or cookbook.filename}': {exc}",
                )
            except Exception as exc:
                logger.exception("Waitrose upload extraction failed for cookbook %s", cookbook.id)
                repository.mark_cookbook_failed(cookbook.id, str(exc))
        uploaded += 1

    if collection:
        if collection.slug in {"nytimes", "jamie-oliver", "bbc-goodfood", "waitrose-recipes"}:
            return redirect_with_notice(
                f"/collections/{collection_slug}",
                f"Processed {uploaded} recipe file{'s' if uploaded != 1 else ''} in {collection.title}.",
            )
        return redirect_with_notice(
            f"/collections/{collection_slug}",
            f"Staged {uploaded} recipe file{'s' if uploaded != 1 else ''} in {collection.title}.",
        )
    return redirect_with_notice("/", f"Staged {uploaded} cookbook{'s' if uploaded != 1 else ''}.")


@app.post("/cookbooks/{cookbook_id}/extract")
async def extract_cookbook_form(cookbook_id: str, request: Request) -> RedirectResponse:
    repository = get_repository(request)
    cookbook = repository.enqueue_extraction(cookbook_id)
    if not cookbook:
        raise HTTPException(status_code=404, detail="Cookbook not found.")
    return redirect_with_notice(f"/cookbooks/{cookbook_id}", "Extraction queued.")


@app.post("/recipes/{recipe_id}/review")
async def update_recipe_review_form(
    recipe_id: str,
    request: Request,
    status: ReviewStatus = Form(...),
    note: str | None = Form(default=None),
) -> RedirectResponse:
    repository = get_repository(request)
    recipe = repository.update_recipe_review(recipe_id, status=status, note=(note or "").strip() or None)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    return redirect_with_notice(f"/recipes/{recipe_id}", "Review updated.")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health")
async def health(request: Request) -> dict:
    repository = get_repository(request)
    return repository.health_report()


def recipe_import_response_urls(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    recipe_id = str(payload.get("recipe_id", "")).strip()
    collection_slug = str(payload.get("collection_slug", "")).strip()
    if recipe_id:
        payload["recipe_url"] = str(request.url_for("recipe_page", recipe_id=recipe_id))
        if collection_slug:
            payload["recipe_url"] += f"?collection={quote(collection_slug)}"
    if collection_slug:
        payload["collection_url"] = str(
            request.url_for("collection_page", collection_slug=collection_slug)
        )
    return payload


@app.post("/api/recipe-imports/preview")
async def preview_recipe_import(
    payload: RecipeImportPreviewRequest,
    request: Request,
) -> JSONResponse:
    repository = get_repository(request)
    importer = UrlRecipeImporter(get_app_settings(request))
    try:
        result = await asyncio.to_thread(importer.preview, repository, payload.url)
    except UrlRecipeImportError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc), "code": exc.code},
        )
    except Exception:
        logger.exception("Recipe URL preview failed unexpectedly.")
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The recipe import service is unavailable right now.",
                "code": "recipe_import_unavailable",
            },
        )
    return JSONResponse(recipe_import_response_urls(result, request))


@app.post("/api/recipe-imports/{import_id}/commit")
async def commit_recipe_import(
    import_id: str,
    payload: RecipeImportCommitRequest,
    request: Request,
) -> JSONResponse:
    repository = get_repository(request)
    importer = UrlRecipeImporter(get_app_settings(request))
    try:
        result = await asyncio.to_thread(
            importer.commit,
            repository,
            import_id,
            title=payload.title,
            ingredient_lines=payload.ingredient_lines,
            method_steps=payload.method_steps,
        )
    except UrlRecipeImportError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc), "code": exc.code},
        )
    except Exception:
        logger.exception("Recipe URL commit failed unexpectedly.")
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The recipe could not be saved right now.",
                "code": "recipe_import_unavailable",
            },
        )
    return JSONResponse(recipe_import_response_urls(result, request))


@app.get("/api/cookbooks")
async def list_cookbooks(
    request: Request,
    sort: str = Query(default="title"),
) -> dict[str, list[dict]]:
    repository = get_repository(request)
    items = [item.model_dump() for item in repository.list_cookbooks(sort_by=sanitize_sort_value(sort))]
    return {"items": items}


@app.patch("/api/cookbooks/{cookbook_id}")
async def update_cookbook_metadata(
    cookbook_id: str,
    payload: CookbookMetadataUpdateRequest,
    request: Request,
) -> dict:
    repository = get_repository(request)
    cookbook = repository.update_cookbook_metadata(
        cookbook_id,
        title=payload.title,
        author=payload.author,
        cuisine=payload.cuisine,
        published_at=payload.published_at,
    )
    if not cookbook:
        raise HTTPException(status_code=404, detail="Cookbook not found.")
    return cookbook.model_dump()


@app.post("/api/cookbooks/{cookbook_id}/extract")
async def extract_cookbook(cookbook_id: str, request: Request) -> dict:
    repository = get_repository(request)
    cookbook = repository.enqueue_extraction(cookbook_id)
    if not cookbook:
        raise HTTPException(status_code=404, detail="Cookbook not found.")
    return cookbook.model_dump()


@app.get("/api/cookbooks/{cookbook_id}/recipes")
async def cookbook_recipes(cookbook_id: str, request: Request) -> dict[str, list[dict]]:
    repository = get_repository(request)
    items = [item.model_dump() for item in repository.list_recipes(cookbook_id=cookbook_id)]
    return {"items": items}


@app.get("/api/recipes")
async def list_recipes(
    request: Request,
    ingredient: list[str] | None = Query(default=None),
    diet: str | None = Query(default=None),
    duration: str | None = Query(default=None),
    season: str | None = Query(default=None),
) -> dict[str, Any]:
    repository = get_repository(request)
    selected_ingredients = normalize_search_ingredients(ingredient)
    selected_diet = normalize_dietary_filter(diet)
    selected_duration = normalize_duration_filter(duration)
    selected_season = normalize_season_filter(season)
    items = [
        item.model_dump()
        for item in filter_recipe_records(
            repository.list_recipes(ingredients=selected_ingredients),
            dietary_filter=selected_diet,
            duration_filter=selected_duration,
            season_filter=selected_season,
        )
    ]
    return {
        "ingredients": selected_ingredients,
        "diet": selected_diet,
        "duration": selected_duration,
        "season": selected_season,
        "items": items,
    }


@app.get("/api/recipes/{recipe_id}")
async def recipe_detail(recipe_id: str, request: Request) -> dict:
    repository = get_repository(request)
    recipe = repository.get_recipe(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    return recipe.model_dump()


@app.patch("/api/recipes/{recipe_id}/review")
async def update_recipe_review(recipe_id: str, payload: ReviewUpdateRequest, request: Request) -> dict:
    repository = get_repository(request)
    recipe = repository.update_recipe_review(
        recipe_id,
        status=payload.status,
        note=payload.note,
    )
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    return recipe.model_dump()


@app.patch("/api/recipes/{recipe_id}/tags")
async def update_recipe_tags(recipe_id: str, payload: RecipeTagsUpdateRequest, request: Request) -> dict[str, Any]:
    dietary_tags: list[str] | None = None
    if payload.dietary_tags is not None:
        dietary_tags = []
        for value in payload.dietary_tags:
            cleaned = str(value).strip().casefold()
            if cleaned in DIETARY_TAG_VALUES and cleaned not in dietary_tags:
                dietary_tags.append(cleaned)

    seasonal_months: list[int] | None = None
    if payload.seasonal_months is not None:
        seasonal_months = []
        for value in payload.seasonal_months:
            try:
                month = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= month <= 12 and month not in seasonal_months:
                seasonal_months.append(month)
        seasonal_months.sort()

    seasonal_ingredients: list[str] | None = None
    if payload.seasonal_ingredients is not None:
        seasonal_ingredients = []
        for value in payload.seasonal_ingredients:
            cleaned = canonicalize_ingredient_name(str(value))
            if cleaned and cleaned not in seasonal_ingredients:
                seasonal_ingredients.append(cleaned)

    ingredient_classifications: dict[str, str] | None = None
    if payload.ingredient_classifications is not None:
        ingredient_classifications = {}
        for key, value in payload.ingredient_classifications.items():
            cleaned_key = canonicalize_ingredient_name(str(key))
            cleaned_value = str(value).strip().casefold()
            if cleaned_key and cleaned_value in INGREDIENT_CLASSIFICATION_VALUES:
                ingredient_classifications[cleaned_key] = cleaned_value

    repository = get_repository(request)
    recipe = repository.update_recipe_tags(
        recipe_id,
        dietary_tags=dietary_tags,
        seasonal_months=seasonal_months,
        seasonal_ingredients=seasonal_ingredients,
        ingredient_classifications=ingredient_classifications,
        note=(payload.note or "").strip() or None,
    )
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found.")

    return {
        "recipe_id": recipe.id,
        "dietary_tags": effective_recipe_dietary_tags(recipe),
        "seasonal_months": effective_recipe_seasonal_months(recipe),
        "seasonal_ingredients": effective_recipe_seasonal_ingredients(recipe),
        "seasonal_month_labels": seasonal_month_labels(effective_recipe_seasonal_months(recipe)),
        "ingredient_classification_summary": ingredient_classification_summary(ingredient_classification_rows(recipe)),
        "in_season_now": effective_recipe_is_in_season(recipe),
        "tag_source": recipe_tag_source(recipe),
        "note": (recipe.source.metadata or {}).get("tag_review_note") or "",
    }


@app.get("/api/recipes/{recipe_id}/images/{image_index}")
async def recipe_image(recipe_id: str, image_index: int, request: Request):
    repository = get_repository(request)
    payload = repository.get_recipe_image(recipe_id, image_index)
    if not payload:
        raise HTTPException(status_code=404, detail="Image not found.")
    image_bytes, content_type = payload
    return StreamingResponse(BytesIO(image_bytes), media_type=content_type)


@app.get("/api/ingredients")
async def ingredient_index(
    request: Request,
    q: str | None = Query(default=None),
    ingredient: list[str] | None = Query(default=None),
) -> dict[str, list[dict]]:
    repository = get_repository(request)
    items = [
        item.model_dump()
        for item in repository.list_ingredients(
            query=q,
            ingredients=normalize_search_ingredients(ingredient),
        )
    ]
    return {"items": items}


@app.get("/api/search")
async def search_api(
    request: Request,
    q: str | None = Query(default=None),
    ingredient: list[str] | None = Query(default=None),
    diet: str | None = Query(default=None),
    duration: str | None = Query(default=None),
    season: str | None = Query(default=None),
) -> dict[str, Any]:
    repository = get_repository(request)
    selected_ingredients = normalize_search_ingredients(ingredient)
    selected_diet = normalize_dietary_filter(diet)
    selected_duration = normalize_duration_filter(duration)
    selected_season = normalize_season_filter(season)
    if q or selected_ingredients:
        results = repository.search_recipes(query=q, ingredients=selected_ingredients)
    elif selected_diet or selected_duration or selected_season:
        results = [
            SearchResultRecord(recipe=recipe, score=1.0, keyword_score=0.0, semantic_score=0.0)
            for recipe in repository.list_recipes()
        ]
    else:
        results = []
    results = filter_search_results(
        results,
        dietary_filter=selected_diet,
        duration_filter=selected_duration,
        season_filter=selected_season,
    )
    return {
        "query": q,
        "ingredients": selected_ingredients,
        "diet": selected_diet,
        "duration": selected_duration,
        "season": selected_season,
        "answer": None,
        "items": [item.model_dump() for item in results],
    }


@app.get("/api/meal-plan/recipe-suggestions")
async def meal_plan_recipe_suggestions_api(
    request: Request,
    q: str | None = Query(default=None),
    limit: int = Query(default=6, ge=1, le=10),
) -> dict[str, Any]:
    repository = get_repository(request)
    items = [
        {
            "id": recipe.id,
            "title": recipe.title,
            "cookbook_title": recipe.cookbook_title,
            "label": recipe_option_value(recipe),
        }
        for recipe in repository.keyword_recipe_suggestions(query=q, limit=limit)
    ]
    return {
        "query": q or "",
        "items": items,
    }
