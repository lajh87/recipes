from __future__ import annotations

import re
from typing import Any

OPTIONAL_PREFIX_RE = re.compile(r"^optional:\s*", re.IGNORECASE)
PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\)")
ARTICLES_RE = re.compile(r"^(?:a|an|the)\s+")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
WHITESPACE_RE = re.compile(r"\s+")

EXACT_CANONICAL_ALIASES = {
    "cheddar cheese": "cheddar",
    "feta cheese": "feta",
    "goats cheese": "goat cheese",
    "goat s cheese": "goat cheese",
    "gruyere cheese": "gruyere",
    "halloumi cheese": "halloumi",
    "linguine pasta": "linguine",
    "mozzarella cheese": "mozzarella",
    "orzo pasta": "orzo",
    "parmesan cheese": "parmesan",
    "pecorino cheese": "pecorino",
    "penne pasta": "penne",
    "ricotta cheese": "ricotta",
    "rigatoni pasta": "rigatoni",
    "spaghetti pasta": "spaghetti",
    "tagliatelle pasta": "tagliatelle",
}

PASTA_SHAPES = frozenset(
    {
        "cavatappi",
        "conchiglie",
        "farfalle",
        "fusilli",
        "linguine",
        "macaroni",
        "orzo",
        "paccheri",
        "pappardelle",
        "penne",
        "rigatoni",
        "spaghetti",
        "tagliatelle",
    }
)
CHEESE_SUFFIX_WHITELIST = frozenset(
    {
        "cheddar",
        "feta",
        "gruyere",
        "halloumi",
        "mozzarella",
        "parmesan",
        "pecorino",
        "ricotta",
    }
)


def normalize_ingredient_text(value: str) -> str:
    normalized = value.casefold()
    normalized = OPTIONAL_PREFIX_RE.sub("", normalized)
    normalized = PARENTHETICAL_RE.sub(" ", normalized)
    normalized = ARTICLES_RE.sub("", normalized)
    normalized = NON_ALNUM_RE.sub(" ", normalized)
    normalized = WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized


def canonicalize_ingredient_name(value: str) -> str:
    normalized = normalize_ingredient_text(value)
    if not normalized:
        return ""

    exact_match = EXACT_CANONICAL_ALIASES.get(normalized)
    if exact_match:
        return exact_match

    if normalized.endswith(" pasta"):
        candidate = normalized.removesuffix(" pasta").strip()
        if candidate in PASTA_SHAPES:
            return candidate

    if normalized.endswith(" cheese"):
        candidate = normalized.removesuffix(" cheese").strip()
        if candidate in CHEESE_SUFFIX_WHITELIST:
            return candidate

    return normalized


def build_ingredient_payload(
    *,
    raw: str,
    normalized_name: str,
    quantity: str | None = None,
    unit: str | None = None,
    item: str | None = None,
    preparation: str | None = None,
    optional: bool = False,
) -> dict[str, Any]:
    normalized = normalize_ingredient_text(normalized_name) or "ingredient"
    canonical = canonicalize_ingredient_name(normalized) or normalized
    return {
        "raw": raw,
        "normalized_name": normalized,
        "canonical_name": canonical,
        "quantity": quantity,
        "unit": unit,
        "item": item,
        "preparation": preparation,
        "optional": optional,
    }


def prepare_ingredient_mapping(ingredient: dict[str, Any]) -> dict[str, Any]:
    raw = str(ingredient.get("raw", "")).strip()
    normalized_source = str(
        ingredient.get("normalized_name")
        or ingredient.get("item")
        or raw
    )
    return build_ingredient_payload(
        raw=raw,
        normalized_name=normalized_source,
        quantity=_optional_text(ingredient.get("quantity")),
        unit=_optional_text(ingredient.get("unit")),
        item=_optional_text(ingredient.get("item")),
        preparation=_optional_text(ingredient.get("preparation")),
        optional=bool(ingredient.get("optional")),
    )


def ingredient_index_name(ingredient: Any) -> str:
    canonical = normalize_ingredient_text(_ingredient_attr(ingredient, "canonical_name"))
    if canonical:
        return canonical

    normalized = normalize_ingredient_text(_ingredient_attr(ingredient, "normalized_name"))
    if normalized:
        return canonicalize_ingredient_name(normalized)

    raw = normalize_ingredient_text(_ingredient_attr(ingredient, "raw"))
    return canonicalize_ingredient_name(raw)


def _ingredient_attr(ingredient: Any, key: str) -> str:
    if isinstance(ingredient, dict):
        return str(ingredient.get(key, "") or "")
    return str(getattr(ingredient, key, "") or "")


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
