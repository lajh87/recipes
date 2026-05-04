import unittest

from app.main import (
    normalize_season_filter,
    recipe_dietary_tags,
    recipe_duration_minutes,
    recipe_matches_duration_filter,
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
