import unittest

from app.blog import _with_network_display_defaults, build_ingredient_network_preview
from app.models import IngredientRecord, RecipeExtractionRecord, RecipeRecord, RecipeReviewRecord, RecipeSourceRecord


def build_recipe(recipe_id: str, ingredient_names: list[str]) -> RecipeRecord:
    return RecipeRecord(
        id=recipe_id,
        cookbook_id="cookbook-1",
        cookbook_title="Test Cookbook",
        title=f"Recipe {recipe_id}",
        ingredients=[IngredientRecord(raw=name, normalized_name=name) for name in ingredient_names],
        ingredient_names=ingredient_names,
        method_steps=["Cook."],
        source=RecipeSourceRecord(object_key="sources/test.epub", format="epub"),
        extraction=RecipeExtractionRecord(model="test-model", extracted_at="2026-04-13T00:00:00Z"),
        review=RecipeReviewRecord(),
    )


class BlogNetworkTests(unittest.TestCase):
    def test_network_display_defaults_cap_initial_slider_value(self) -> None:
        data = _with_network_display_defaults(
            {
                "node_count": 120,
                "edge_count": 2,
                "nodes": [{"id": f"ingredient-{index}"} for index in range(120)],
                "links": [
                    {"source": "ingredient-0", "target": "ingredient-1", "value": 3},
                    {"source": "ingredient-1", "target": "ingredient-2", "value": 2},
                ],
            }
        )

        self.assertEqual(data["preview_node_count"], 120)
        self.assertEqual(data["preview_edge_count"], 2)
        self.assertEqual(data["slider_min_node_count"], 20)
        self.assertEqual(data["slider_max_node_count"], 120)
        self.assertEqual(data["slider_step"], 5)
        self.assertEqual(data["default_display_node_count"], 100)

    def test_build_ingredient_network_preview_keeps_ranked_nodes(self) -> None:
        recipes = [
            build_recipe("1", ["salt", "garlic", "onion"]),
            build_recipe("2", ["salt", "garlic"]),
            build_recipe("3", ["salt", "onion"]),
            build_recipe("4", ["salt", "basil"]),
            build_recipe("5", ["garlic", "onion"]),
        ]

        preview = build_ingredient_network_preview(recipes, min_occurrence=2, max_nodes=3, max_edges=10)

        self.assertEqual([node["id"] for node in preview["nodes"]], ["salt", "garlic", "onion"])
        self.assertEqual(preview["preview_node_count"], 3)
        self.assertEqual(preview["preview_edge_count"], 3)
        self.assertEqual(preview["slider_max_node_count"], 3)
        self.assertEqual(
            preview["links"],
            [
                {"source": "garlic", "target": "onion", "value": 2},
                {"source": "garlic", "target": "salt", "value": 2},
                {"source": "onion", "target": "salt", "value": 2},
            ],
        )


if __name__ == "__main__":
    unittest.main()
