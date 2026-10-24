import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.mcp_server import PlannedMeal, create_meal_plan, mcp, search_recipes, update_meal_plan
from app.models import RecipeReferenceRecord


class RecipeMcpTests(unittest.TestCase):
    def test_search_tool_is_advertised(self) -> None:
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual(
            {"search_recipes", "get_recipe", "get_meal_plan", "create_meal_plan", "update_meal_plan"},
            {tool.name for tool in tools},
        )

    @patch("app.recipe_tools.LibraryRepository")
    def test_search_returns_compact_results_and_closes_repository(self, repository_class) -> None:
        recipe = SimpleNamespace(
            id="recipe-1",
            title="Tomato pasta",
            cookbook_title="Weeknight meals",
            ingredient_names=["tomatoes", "pasta"],
        )
        repository = repository_class.from_settings.return_value
        repository.search_recipes.return_value = [
            SimpleNamespace(recipe=recipe, matched_terms=["pasta"], score=0.03279)
        ]

        result = search_recipes(" pasta ", [" tomatoes "], 5)

        repository.search_recipes.assert_called_once_with(
            query="pasta", ingredients=["tomatoes"], limit=5
        )
        repository.close.assert_called_once_with()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["recipe_path"], "/recipes/recipe-1")
        self.assertEqual(result["items"][0]["ingredient_names"], ["tomatoes", "pasta"])

    @patch("app.recipe_tools.LibraryRepository")
    def test_search_closes_repository_when_search_fails(self, repository_class) -> None:
        repository = repository_class.from_settings.return_value
        repository.search_recipes.side_effect = RuntimeError("datastore unavailable")

        with self.assertRaisesRegex(RuntimeError, "datastore unavailable"):
            search_recipes("pasta")

        repository.close.assert_called_once_with()

    @patch("app.recipe_tools.LibraryRepository")
    def test_search_rejects_unbounded_or_empty_requests(self, repository_class) -> None:
        with self.assertRaisesRegex(ValueError, "query or at least one ingredient"):
            search_recipes("  ", [" "])
        with self.assertRaisesRegex(ValueError, "between 1 and 20"):
            search_recipes("pasta", limit=21)

        repository_class.from_settings.assert_not_called()

    @patch("app.recipe_tools.LibraryRepository")
    def test_create_meal_plan_preserves_weeks_and_links_recipes(self, repository_class) -> None:
        repository = repository_class.from_settings.return_value
        repository.list_recipe_references.return_value = [
            RecipeReferenceRecord(
                id="recipe-1",
                cookbook_id="book-1",
                cookbook_title="Weeknight meals",
                title="Tomato pasta",
            )
        ]
        repository.load_meal_plan_payload.return_value = json.dumps(
            {
                "weeks": [
                    {
                        "id": "week-old",
                        "start_on": "2026-10-11",
                        "entries": [
                            {
                                "id": "row-old",
                                "weekday": "Monday",
                                "meal": "Dinner",
                                "title": "Soup",
                            }
                        ],
                    }
                ]
            }
        )

        result = create_meal_plan(
            "2026-10-18",
            [
                PlannedMeal(weekday="Tuesday", recipe_id="recipe-1", title="Wrong title"),
                PlannedMeal(weekday="Monday", meal="Lunch", title="Leftovers"),
            ],
            notes=" Buy basil ",
        )

        saved = json.loads(repository.save_meal_plan_payload.call_args.args[0])
        self.assertEqual([week["id"] for week in saved["weeks"]], [result["week_id"], "week-old"])
        self.assertEqual(saved["weeks"][0]["notes"], "Buy basil")
        self.assertEqual(
            [(entry["weekday"], entry["meal"], entry["title"], entry["recipe_id"])
             for entry in saved["weeks"][0]["entries"]],
            [
                ("Monday", "Lunch", "Leftovers", None),
                ("Tuesday", "Dinner", "Tomato pasta", "recipe-1"),
            ],
        )
        self.assertEqual(result["meal_plan_path"], "/meal-plan")
        repository.close.assert_called_once_with()

    @patch("app.recipe_tools.LibraryRepository")
    def test_create_meal_plan_rejects_duplicate_week_without_saving(self, repository_class) -> None:
        repository = repository_class.from_settings.return_value
        repository.list_recipe_references.return_value = []
        repository.load_meal_plan_payload.return_value = json.dumps(
            {"weeks": [{"id": "week-old", "start_on": "2026-10-18", "entries": []}]}
        )

        with self.assertRaisesRegex(ValueError, "already starts"):
            create_meal_plan("2026-10-18", [PlannedMeal(weekday="Monday", title="Soup")])

        repository.save_meal_plan_payload.assert_not_called()
        repository.close.assert_called_once_with()

    @patch("app.recipe_tools.LibraryRepository")
    def test_create_meal_plan_rejects_invalid_input_without_saving(self, repository_class) -> None:
        repository = repository_class.from_settings.return_value
        repository.list_recipe_references.return_value = []

        with self.assertRaisesRegex(ValueError, "valid ISO date"):
            create_meal_plan("18 October", [PlannedMeal(weekday="Monday", title="Soup")])
        with self.assertRaisesRegex(ValueError, "between 1 and 50 meals"):
            create_meal_plan("2026-10-18", [])
        with self.assertRaisesRegex(ValueError, "Recipe not found"):
            create_meal_plan("2026-10-18", [PlannedMeal(weekday="Monday", recipe_id="missing")])
        with self.assertRaisesRegex(ValueError, "recipe_id or a title"):
            create_meal_plan("2026-10-18", [PlannedMeal(weekday="Monday")])

        repository.save_meal_plan_payload.assert_not_called()
        self.assertEqual(repository.close.call_count, 2)

    @patch("app.recipe_tools.LibraryRepository")
    def test_create_meal_plan_accepts_mcp_arguments_and_removes_blank_placeholder(self, repository_class) -> None:
        repository = repository_class.from_settings.return_value
        repository.list_recipe_references.return_value = []
        repository.load_meal_plan_payload.return_value = json.dumps(
            {"weeks": [{"id": "empty-week", "entries": []}]}
        )

        result = asyncio.run(
            mcp.call_tool(
                "create_meal_plan",
                {
                    "start_on": "2026-10-18",
                    "meals": [{"weekday": "Monday", "title": "Soup"}],
                },
            )
        )

        saved = json.loads(repository.save_meal_plan_payload.call_args.args[0])
        self.assertEqual(len(saved["weeks"]), 1)
        self.assertEqual(saved["weeks"][0]["entries"][0]["title"], "Soup")
        self.assertEqual(json.loads(result[0].text)["start_on"], "2026-10-18")

    @patch("app.recipe_tools.LibraryRepository")
    def test_update_meal_plan_replaces_only_target_week(self, repository_class) -> None:
        repository = repository_class.from_settings.return_value
        repository.list_recipe_references.return_value = [
            RecipeReferenceRecord(
                id="recipe-1", cookbook_id="book-1", cookbook_title="Weeknight meals", title="Rice and beans"
            )
        ]
        repository.load_meal_plan_payload.return_value = json.dumps(
            {"weeks": [
                {"id": "week-current", "start_on": "2026-10-18", "notes": "Old basket",
                 "shopping_checked_items": ["rice"],
                 "entries": [{"id": "row-old", "weekday": "Sunday", "meal": "Dinner", "title": "Old dinner"}]},
                {"id": "week-prior", "start_on": "2026-10-11", "notes": "Keep me", "entries": []},
            ]}
        )

        result = update_meal_plan(
            "2026-10-18", [PlannedMeal(weekday="Sunday", recipe_id="recipe-1")], "New basket"
        )

        saved = json.loads(repository.save_meal_plan_payload.call_args.args[0])
        self.assertEqual(result["week_id"], "week-current")
        self.assertEqual(saved["weeks"][0]["notes"], "New basket")
        self.assertEqual(saved["weeks"][0]["shopping_checked_items"], ["rice"])
        self.assertEqual(saved["weeks"][0]["entries"][0]["recipe_id"], "recipe-1")
        self.assertEqual(saved["weeks"][1]["notes"], "Keep me")
        repository.close.assert_called_once_with()

    @patch("app.recipe_tools.LibraryRepository")
    def test_update_meal_plan_rejects_missing_week_without_saving(self, repository_class) -> None:
        repository = repository_class.from_settings.return_value
        repository.list_recipe_references.return_value = []
        repository.load_meal_plan_payload.return_value = json.dumps({"weeks": []})

        with self.assertRaisesRegex(ValueError, "No meal-plan week starts"):
            update_meal_plan("2026-10-18", [PlannedMeal(weekday="Sunday", title="Soup")], "")

        repository.save_meal_plan_payload.assert_not_called()
        repository.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
