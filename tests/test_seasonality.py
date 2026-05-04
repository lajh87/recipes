import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from app.seasonality import (
    canonical_seasonal_ingredient,
    current_uk_month,
    is_seasonal_ingredient_in_month,
    main_seasonal_ingredients,
    recipe_is_in_season,
)


def ingredient(raw: str, *, optional: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        raw=raw,
        normalized_name=raw,
        canonical_name=None,
        item=None,
        optional=optional,
    )


def recipe(*ingredients: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(ingredients=list(ingredients))


class SeasonalityTests(unittest.TestCase):
    def test_current_uk_month_uses_london_timezone(self) -> None:
        self.assertEqual(current_uk_month(datetime(2026, 4, 30, 23, 30, tzinfo=UTC)), 5)

    def test_known_may_produce_is_in_season(self) -> None:
        self.assertTrue(is_seasonal_ingredient_in_month("asparagus", 5))
        self.assertFalse(is_seasonal_ingredient_in_month("pumpkin", 5))

    def test_aliases_resolve_to_calendar_entries(self) -> None:
        self.assertEqual(canonical_seasonal_ingredient("zucchini"), "courgette")
        self.assertEqual(canonical_seasonal_ingredient("eggplant"), "aubergine")
        self.assertEqual(canonical_seasonal_ingredient("scallions"), "spring onion")

    def test_main_seasonal_ingredients_use_recipe_order_and_ignore_minor_items(self) -> None:
        item = recipe(
            ingredient("1 tsp chopped parsley"),
            ingredient("400g asparagus"),
            ingredient("200g tomatoes"),
            ingredient("spring onions, to serve"),
        )

        self.assertEqual(main_seasonal_ingredients(item), ["asparagus", "tomato"])

    def test_recipe_is_in_season_requires_main_produce_to_be_in_month(self) -> None:
        self.assertTrue(recipe_is_in_season(recipe(ingredient("500g asparagus")), month=5))
        self.assertFalse(recipe_is_in_season(recipe(ingredient("500g asparagus"), ingredient("300g pumpkin")), month=5))
        self.assertFalse(recipe_is_in_season(recipe(ingredient("200g pasta")), month=5))


if __name__ == "__main__":
    unittest.main()
