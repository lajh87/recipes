import unittest

from app.main import (
    current_uk_month,
    effective_recipe_dietary_tags,
    effective_recipe_is_in_season,
    effective_recipe_seasonal_months,
    normalize_season_filter,
    recipe_dietary_tags,
    recipe_duration_minutes,
    recipe_matches_duration_filter,
    recipe_matches_dietary_filter,
    recipe_matches_season_filter,
)
from app.models import (
    IngredientRecord,
    RecipeExtractionRecord,
    RecipeRecord,
    RecipeReviewRecord,
    RecipeSourceRecord,
)


def build_recipe(
    ingredient_names: list[str],
    *,
    source_metadata: dict | None = None,
) -> RecipeRecord:
    return RecipeRecord(
        id="recipe-1",
        cookbook_id="book-1",
        cookbook_title="Book",
        title="Test Recipe",
        ingredients=[
            IngredientRecord(raw=name, normalized_name=name)
            for name in ingredient_names
        ],
        ingredient_names=ingredient_names,
        method_steps=["Cook."],
        source=RecipeSourceRecord(
            object_key="sources/test.epub",
            format="epub",
            metadata=source_metadata or {},
        ),
        extraction=RecipeExtractionRecord(model="test-model", extracted_at="2026-05-04T00:00:00Z"),
        review=RecipeReviewRecord(),
    )


class RecipeFilterTests(unittest.TestCase):
    def test_vegetarian_recipe_is_also_pescatarian_compatible(self) -> None:
        recipe = build_recipe(["tomatoes", "butter", "oyster mushrooms"])

        self.assertEqual(recipe_dietary_tags(recipe), ["vegetarian", "pescatarian"])

    def test_fish_recipe_is_pescatarian_not_vegetarian(self) -> None:
        recipe = build_recipe(["salmon fillets", "lemon", "dill"])

        self.assertEqual(recipe_dietary_tags(recipe), ["pescatarian"])

    def test_land_meat_recipe_has_no_dietary_tag(self) -> None:
        recipe = build_recipe(["chicken stock", "rice", "peas"])

        self.assertEqual(recipe_dietary_tags(recipe), [])

    def test_manual_dietary_override_controls_effective_tags_and_filters(self) -> None:
        recipe = build_recipe(["chicken stock", "rice"], source_metadata={"dietary_tags_override": ["vegetarian"]})

        self.assertEqual(effective_recipe_dietary_tags(recipe), ["vegetarian"])
        self.assertTrue(recipe_matches_dietary_filter(recipe, "vegetarian"))
        self.assertFalse(recipe_matches_dietary_filter(recipe, "pescatarian"))

    def test_ingredient_classification_override_controls_effective_dietary_tags(self) -> None:
        recipe = build_recipe(
            ["salmon fillets", "lemon"],
            source_metadata={"ingredient_classification_overrides": {"salmon fillets": "none"}},
        )

        self.assertEqual(effective_recipe_dietary_tags(recipe), ["vegetarian", "pescatarian"])

    def test_ingredient_classification_override_can_mark_meat(self) -> None:
        recipe = build_recipe(
            ["tomatoes", "rice"],
            source_metadata={"ingredient_classification_overrides": {"tomatoes": "meat"}},
        )

        self.assertEqual(effective_recipe_dietary_tags(recipe), [])

    def test_manual_seasonal_month_override_controls_effective_season_filter(self) -> None:
        current_month = current_uk_month()
        recipe = build_recipe(["500g pumpkin"], source_metadata={"seasonal_months_override": [current_month]})

        self.assertEqual(effective_recipe_seasonal_months(recipe), [current_month])
        self.assertTrue(effective_recipe_is_in_season(recipe, month=current_month))
        self.assertTrue(recipe_matches_season_filter(recipe, "in-season"))

    def test_total_duration_is_parsed(self) -> None:
        recipe = build_recipe([], source_metadata={"total_time": "1 hour 20 mins"})

        self.assertEqual(recipe_duration_minutes(recipe), 80)
        self.assertTrue(recipe_matches_duration_filter(recipe, "under-120"))
        self.assertFalse(recipe_matches_duration_filter(recipe, "under-60"))

    def test_duration_falls_back_to_prep_plus_cook(self) -> None:
        recipe = build_recipe([], source_metadata={"prep_time": "10 minutes", "cook_time": "25 mins"})

        self.assertEqual(recipe_duration_minutes(recipe), 35)

    def test_normalize_season_filter_ignores_invalid_values(self) -> None:
        self.assertEqual(normalize_season_filter("in-season"), "in-season")
        self.assertEqual(normalize_season_filter("winter"), "")


if __name__ == "__main__":
    unittest.main()
