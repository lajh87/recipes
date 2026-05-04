from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.ingredients import canonicalize_ingredient_name, ingredient_index_name, normalize_ingredient_text

UK_TIMEZONE = ZoneInfo("Europe/London")
MAIN_SEASONAL_INGREDIENT_LIMIT = 3
ALL_MONTHS = frozenset(range(1, 13))

UK_SEASONAL_PRODUCE: dict[str, frozenset[int]] = {
    "apple": frozenset({8, 9, 10, 11, 12, 1, 2, 3}),
    "apricot": frozenset({6, 7, 8}),
    "artichoke": frozenset({6, 7, 8, 9}),
    "asparagus": frozenset({4, 5, 6}),
    "aubergine": frozenset({6, 7, 8, 9, 10}),
    "beetroot": frozenset({6, 7, 8, 9, 10, 11, 12, 1, 2, 3}),
    "blackberry": frozenset({8, 9, 10}),
    "blackcurrant": frozenset({7, 8}),
    "blueberry": frozenset({6, 7, 8, 9}),
    "broad bean": frozenset({6, 7, 8, 9}),
    "broccoli": frozenset({6, 7, 8, 9, 10, 11}),
    "brussels sprout": frozenset({9, 10, 11, 12, 1, 2, 3}),
    "cabbage": ALL_MONTHS,
    "carrot": ALL_MONTHS,
    "cauliflower": frozenset({1, 2, 3, 4, 5, 6, 9, 10, 11, 12}),
    "celeriac": frozenset({9, 10, 11, 12, 1, 2, 3}),
    "celery": frozenset({7, 8, 9, 10, 11, 12}),
    "cherry": frozenset({6, 7}),
    "chicory": frozenset({1, 2, 3, 10, 11, 12}),
    "courgette": frozenset({6, 7, 8, 9, 10}),
    "cranberry": frozenset({10, 11, 12}),
    "cucumber": frozenset({5, 6, 7, 8, 9}),
    "fennel": frozenset({6, 7, 8, 9, 10}),
    "fig": frozenset({8, 9, 10}),
    "garlic": ALL_MONTHS,
    "gooseberry": frozenset({6, 7, 8}),
    "grape": frozenset({8, 9, 10}),
    "green bean": frozenset({6, 7, 8, 9}),
    "kale": frozenset({9, 10, 11, 12, 1, 2, 3}),
    "leek": frozenset({9, 10, 11, 12, 1, 2, 3, 4}),
    "lettuce": frozenset({5, 6, 7, 8, 9, 10}),
    "marrow": frozenset({8, 9, 10}),
    "mushroom": ALL_MONTHS,
    "nectarine": frozenset({6, 7, 8, 9}),
    "onion": ALL_MONTHS,
    "parsnip": frozenset({9, 10, 11, 12, 1, 2, 3}),
    "pea": frozenset({6, 7, 8}),
    "peach": frozenset({6, 7, 8, 9}),
    "pear": frozenset({8, 9, 10, 11, 12, 1, 2}),
    "pepper": frozenset({7, 8, 9, 10}),
    "plum": frozenset({8, 9, 10}),
    "potato": ALL_MONTHS,
    "purple sprouting broccoli": frozenset({2, 3, 4}),
    "pumpkin": frozenset({9, 10, 11, 12}),
    "radish": frozenset({5, 6, 7, 8, 9, 10}),
    "raspberry": frozenset({6, 7, 8, 9, 10}),
    "redcurrant": frozenset({7, 8}),
    "rhubarb": frozenset({1, 2, 3, 4, 5, 6}),
    "rocket": frozenset({4, 5, 6, 7, 8, 9, 10}),
    "runner bean": frozenset({7, 8, 9, 10}),
    "spinach": frozenset({3, 4, 5, 6, 7, 8, 9, 10}),
    "spring greens": frozenset({3, 4, 5, 6}),
    "spring onion": frozenset({3, 4, 5, 6, 7, 8, 9, 10}),
    "squash": frozenset({9, 10, 11, 12, 1, 2}),
    "strawberry": frozenset({5, 6, 7, 8, 9}),
    "sweetcorn": frozenset({8, 9, 10}),
    "swede": frozenset({9, 10, 11, 12, 1, 2, 3}),
    "tomato": frozenset({5, 6, 7, 8, 9, 10}),
    "turnip": frozenset({6, 7, 8, 9, 10, 11, 12, 1, 2}),
    "watercress": frozenset({4, 5, 6, 7, 8, 9, 10}),
}

SEASONAL_ALIASES = {
    "bell pepper": "pepper",
    "bramley apple": "apple",
    "brussel sprout": "brussels sprout",
    "chard": "spinach",
    "chestnut mushroom": "mushroom",
    "corn": "sweetcorn",
    "cos lettuce": "lettuce",
    "courgettes": "courgette",
    "eggplant": "aubergine",
    "flat mushroom": "mushroom",
    "green beans": "green bean",
    "new potato": "potato",
    "new potatoes": "potato",
    "peppers": "pepper",
    "portobello mushroom": "mushroom",
    "red onion": "onion",
    "romaine lettuce": "lettuce",
    "scallion": "spring onion",
    "scallions": "spring onion",
    "shallot": "onion",
    "spring onions": "spring onion",
    "sugar snap pea": "pea",
    "sugar snap peas": "pea",
    "tenderstem broccoli": "broccoli",
    "white onion": "onion",
    "wild mushroom": "mushroom",
    "zucchini": "courgette",
}

IGNORED_SEASONAL_INGREDIENTS = frozenset(
    {
        "basil",
        "bay leaf",
        "chilli",
        "chile",
        "coriander",
        "dill",
        "ginger",
        "herb",
        "lemon",
        "lime",
        "mint",
        "parsley",
        "rosemary",
        "sage",
        "tarragon",
        "thyme",
    }
)

GARNISH_HINT_RE = re.compile(r"\b(to serve|garnish|optional|plus extra|serve with)\b", re.IGNORECASE)
TINY_QUANTITY_RE = re.compile(r"\b(pinch|sprig|handful|tsp|teaspoons?|tbsp|tablespoons?)\b", re.IGNORECASE)


def current_uk_month(now: datetime | None = None) -> int:
    if now is None:
        return datetime.now(UK_TIMEZONE).month
    if now.tzinfo is None:
        return now.replace(tzinfo=UK_TIMEZONE).month
    return now.astimezone(UK_TIMEZONE).month


def canonical_seasonal_ingredient(value: str) -> str:
    normalized = canonicalize_ingredient_name(value)
    if not normalized:
        return ""
    normalized = SEASONAL_ALIASES.get(normalized, normalized)
    normalized = _singular_seasonal_name(normalized)
    if normalized in UK_SEASONAL_PRODUCE:
        return normalized

    parts = normalized.split()
    for start in range(len(parts)):
        candidate = " ".join(parts[start:])
        candidate = SEASONAL_ALIASES.get(candidate, candidate)
        candidate = _singular_seasonal_name(candidate)
        if candidate in UK_SEASONAL_PRODUCE:
            return candidate
    return ""


def is_seasonal_ingredient_in_month(name: str, month: int) -> bool:
    canonical = canonical_seasonal_ingredient(name)
    return bool(canonical and month in UK_SEASONAL_PRODUCE[canonical])


def main_seasonal_ingredients(recipe: Any, *, limit: int = MAIN_SEASONAL_INGREDIENT_LIMIT) -> list[str]:
    ingredients = getattr(recipe, "ingredients", []) or []
    found: list[str] = []
    seen: set[str] = set()

    for ingredient in ingredients:
        if _is_minor_ingredient(ingredient):
            continue

        candidates = [
            ingredient_index_name(ingredient),
            normalize_ingredient_text(_ingredient_attr(ingredient, "item")),
            normalize_ingredient_text(_ingredient_attr(ingredient, "normalized_name")),
            normalize_ingredient_text(_ingredient_attr(ingredient, "raw")),
        ]
        for candidate in candidates:
            canonical = canonical_seasonal_ingredient(candidate)
            if not canonical or canonical in IGNORED_SEASONAL_INGREDIENTS or canonical in seen:
                continue
            seen.add(canonical)
            found.append(canonical)
            break

        if len(found) >= limit:
            break

    return found


def recipe_is_in_season(recipe: Any, *, month: int | None = None) -> bool:
    effective_month = month or current_uk_month()
    ingredients = main_seasonal_ingredients(recipe)
    if not ingredients:
        return False
    return all(effective_month in UK_SEASONAL_PRODUCE[ingredient] for ingredient in ingredients)


def _is_minor_ingredient(ingredient: Any) -> bool:
    raw = _ingredient_attr(ingredient, "raw")
    if bool(_ingredient_attr(ingredient, "optional")):
        return True
    if GARNISH_HINT_RE.search(raw):
        return True
    return bool(TINY_QUANTITY_RE.search(raw))


def _singular_seasonal_name(value: str) -> str:
    if value in UK_SEASONAL_PRODUCE:
        return value
    if value.endswith("ies"):
        candidate = f"{value[:-3]}y"
        if candidate in UK_SEASONAL_PRODUCE:
            return candidate
    if value.endswith("oes"):
        candidate = value[:-2]
        if candidate in UK_SEASONAL_PRODUCE:
            return candidate
    if value.endswith("s"):
        candidate = value[:-1]
        if candidate in UK_SEASONAL_PRODUCE:
            return candidate
    return value


def _ingredient_attr(ingredient: Any, key: str) -> str:
    if isinstance(ingredient, dict):
        return str(ingredient.get(key, "") or "")
    return str(getattr(ingredient, key, "") or "")
