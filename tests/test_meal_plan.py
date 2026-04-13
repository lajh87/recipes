import unittest
from pathlib import Path
import tempfile

from app.meal_plan import (
    append_blank_row,
    build_week_shopping_list,
    create_blank_week,
    import_recent_weeks_from_text,
    normalize_week_start_value,
    load_or_import_meal_plan,
    parse_meal_plan_form,
    recipe_option_value,
    resolve_recipe_reference,
)
from app.models import RecipeRecord
from app.repository import LibraryRepository


def build_recipe(
    recipe_id: str,
    title: str,
    cookbook_title: str = "Book",
    ingredients: list[dict[str, object]] | None = None,
) -> RecipeRecord:
    ingredient_payload = ingredients or []
    return RecipeRecord.model_validate(
        {
            "id": recipe_id,
            "cookbook_id": "book-1",
            "cookbook_title": cookbook_title,
            "title": title,
            "ingredients": ingredient_payload,
            "ingredient_names": [str(item.get("canonical_name") or item.get("normalized_name") or "") for item in ingredient_payload],
            "method_steps": [],
            "images": [],
            "source": {
                "object_key": "ebooks/book.epub",
                "format": "epub",
                "anchor": f"xhtml/{recipe_id}.xhtml",
                "excerpt": "",
                "metadata": {},
            },
            "extraction": {
                "model": "gpt-5.4-mini",
                "confidence": 0.95,
                "notes": [],
                "extracted_at": "2026-04-12T00:00:00Z",
                "needs_review_reasons": [],
            },
            "review": {"status": "verified"},
        }
    )


class FakeForm(dict):
    def getlist(self, key: str) -> list[str]:
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


class MealPlanTests(unittest.TestCase):
    def test_import_recent_weeks_only_uses_latest_sections(self) -> None:
        recipes = [
            build_recipe("recipe-1", "Feta, Spinach Orzo", "Flavour"),
            build_recipe("recipe-2", "Fish Tacos with Mango Lime", "Simple"),
            build_recipe("recipe-3", "Giant Cous Cous", "Weeknight"),
        ]
        text = """
11 apr
- [ ] Pasta (Feta, Spinach Orzo)

5 April
- [x] Fish tacos

29 March
- [x] Wednesday Giant Cous Cous

20 Mar
- [x] Crab linguine

15 March
- [x] Prawn Tacos
"""

        document = import_recent_weeks_from_text(
            text,
            recipes,
            base_dir=Path("/tmp"),
            source_path="data-raw/meal-plan.json",
            legacy_source_path="data-raw/recipes.txt",
            week_limit=3,
        )

        self.assertEqual([week.title for week in document.weeks], ["11 apr", "5 April", "29 March"])
        self.assertEqual(document.weeks[0].entries[0].recipe_id, "recipe-1")
        self.assertEqual(document.weeks[0].entries[0].meal, "Dinner")
        self.assertEqual(document.weeks[1].entries[0].recipe_id, "recipe-2")
        self.assertEqual(document.weeks[2].entries[0].weekday, "Wednesday")
        self.assertEqual(document.weeks[2].entries[0].recipe_id, "recipe-3")
        self.assertEqual(document.weeks[0].start_on, normalize_week_start_value("11 apr"))

    def test_resolve_recipe_reference_accepts_datalist_value(self) -> None:
        recipes = [build_recipe("recipe-1", "Fish Tacos with Mango Lime", "Simple")]
        recipe_map = {recipe.id: recipe for recipe in recipes}

        recipe = resolve_recipe_reference(
            recipe_option_value(recipes[0]),
            recipe_map,
            recipes,
        )

        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.id, "recipe-1")

    def test_parse_meal_plan_form_builds_structured_slots(self) -> None:
        recipes = [build_recipe("recipe-1", "Fish Tacos with Mango Lime", "Simple")]
        week = create_blank_week("Week 1")
        append_blank_row(week)
        row = week.entries[0]
        form = FakeForm(
            {
                "week_id": [week.id],
                f"week_entry_id__{week.id}": [row.id],
                f"week_start_on__{week.id}": "2026-04-14",
                f"week_notes__{week.id}": "Buy limes",
                f"entry__{week.id}__{row.id}__title": recipe_option_value(recipes[0]),
                f"entry__{week.id}__{row.id}__weekday": "Monday",
                f"entry__{week.id}__{row.id}__meal": "Dinner",
                f"entry__{week.id}__{row.id}__completed": "true",
                f"entry__{week.id}__{row.id}__recipe_id": "recipe-1",
            }
        )

        document = parse_meal_plan_form(form, recipes, base_dir=Path("/tmp"))
        entry = document.weeks[0].entries[0]

        self.assertEqual(document.weeks[0].start_on, "2026-04-14")
        self.assertEqual(document.weeks[0].notes, "Buy limes")
        self.assertEqual(entry.title, "Fish Tacos with Mango Lime")
        self.assertEqual(entry.weekday, "Monday")
        self.assertEqual(entry.meal, "Dinner")
        self.assertTrue(entry.completed)
        self.assertEqual(entry.recipe_id, "recipe-1")

    def test_parse_meal_plan_form_preserves_submitted_row_order_within_week(self) -> None:
        recipes = [
            build_recipe("recipe-1", "Fish Tacos with Mango Lime", "Simple"),
            build_recipe("recipe-2", "Tomato Pasta", "Simple"),
        ]
        week = create_blank_week("Week 1")
        append_blank_row(week)
        first_row, second_row = week.entries[:2]
        form = FakeForm(
            {
                "week_id": [week.id],
                f"week_entry_id__{week.id}": [second_row.id, first_row.id],
                f"entry__{week.id}__{first_row.id}__title": "Fish Tacos with Mango Lime",
                f"entry__{week.id}__{first_row.id}__weekday": "Monday",
                f"entry__{week.id}__{first_row.id}__meal": "Dinner",
                f"entry__{week.id}__{first_row.id}__recipe_id": "recipe-1",
                f"entry__{week.id}__{second_row.id}__title": "Tomato Pasta",
                f"entry__{week.id}__{second_row.id}__weekday": "Tuesday",
                f"entry__{week.id}__{second_row.id}__meal": "Lunch",
                f"entry__{week.id}__{second_row.id}__recipe_id": "recipe-2",
            }
        )

        document = parse_meal_plan_form(form, recipes, base_dir=Path("/tmp"))

        self.assertEqual(
            [entry.id for entry in document.weeks[0].entries],
            [second_row.id, first_row.id],
        )
        self.assertEqual(
            [entry.title for entry in document.weeks[0].entries],
            ["Tomato Pasta", "Fish Tacos with Mango Lime"],
        )

    def test_resolve_recipe_reference_prefers_explicit_recipe_id(self) -> None:
        recipes = [build_recipe("recipe-1", "Fish Tacos with Mango Lime", "Simple")]
        recipe_map = {recipe.id: recipe for recipe in recipes}

        recipe = resolve_recipe_reference(
            "",
            recipe_map,
            recipes,
            fallback_title="Something else entirely",
            recipe_id="recipe-1",
        )

        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.id, "recipe-1")

    def test_load_or_import_meal_plan_persists_imported_document(self) -> None:
        recipes = [build_recipe("recipe-1", "Fish Tacos with Mango Lime", "Simple")]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            data_dir = base_dir / "data-raw"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "recipes.txt").write_text("5 April\n- [x] Fish tacos\n", encoding="utf-8")

            document = load_or_import_meal_plan(base_dir, recipes, week_limit=1)

            saved_path = data_dir / "meal-plan.json"
            self.assertTrue(saved_path.exists())
            self.assertEqual(document.weeks[0].entries[0].recipe_id, "recipe-1")
            self.assertEqual(document.weeks[0].start_on, normalize_week_start_value("5 April"))

    def test_load_or_import_meal_plan_uses_redis_callbacks_without_writing_file(self) -> None:
        recipes = [build_recipe("recipe-1", "Fish Tacos with Mango Lime", "Simple")]
        saved_payloads: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            data_dir = base_dir / "data-raw"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "recipes.txt").write_text("5 April\n- [x] Fish tacos\n", encoding="utf-8")

            document = load_or_import_meal_plan(
                base_dir,
                recipes,
                week_limit=1,
                source_path="Redis",
                load_payload=lambda: None,
                save_payload=saved_payloads.append,
            )

            saved_path = data_dir / "meal-plan.json"
            self.assertFalse(saved_path.exists())
            self.assertEqual(document.source_path, "Redis")
            self.assertEqual(document.weeks[0].entries[0].recipe_id, "recipe-1")
            self.assertEqual(len(saved_payloads), 1)

    def test_keyword_recipe_suggestions_returns_short_keyword_ranked_list(self) -> None:
        repository = object.__new__(LibraryRepository)
        repository.list_recipes = lambda: [  # type: ignore[method-assign]
            build_recipe("recipe-1", "Fish Tacos with Mango Lime", "Simple"),
            build_recipe("recipe-2", "Mango Chutney Chicken", "Book"),
            build_recipe("recipe-3", "Tomato Pasta", "Book"),
        ]

        suggestions = repository.keyword_recipe_suggestions(query="mango", limit=2)

        self.assertEqual([item.id for item in suggestions], ["recipe-1", "recipe-2"])

    def test_build_week_shopping_list_aggregates_ingredients_by_recipe(self) -> None:
        week = create_blank_week("2026-04-14")
        week.entries = [week.entries[0]]
        week.entries[0].title = "Fish Tacos with Mango Lime"
        week.entries[0].recipe_id = "recipe-1"

        shopping_list = build_week_shopping_list(
            week,
            [
                build_recipe(
                    "recipe-1",
                    "Fish Tacos with Mango Lime",
                    "Simple",
                    ingredients=[
                        {
                            "raw": "2 mangos",
                            "normalized_name": "mango",
                            "canonical_name": "mango",
                        },
                        {
                            "raw": "1 lime",
                            "normalized_name": "lime",
                            "canonical_name": "lime",
                        },
                        {
                            "raw": "extra lime wedges",
                            "normalized_name": "lime",
                            "canonical_name": "lime",
                        },
                    ],
                )
            ],
        )

        self.assertEqual([item.name for item in shopping_list], ["lime", "mango"])
        self.assertEqual(shopping_list[0].recipe_summary, "Fish Tacos with Mango Lime x2")
        self.assertEqual(shopping_list[1].recipe_summary, "Fish Tacos with Mango Lime")

    def test_build_week_shopping_list_preserves_raw_decimal_ingredient_labels(self) -> None:
        week = create_blank_week("2026-04-14")
        week.entries = [week.entries[0]]
        week.entries[0].title = "Risotto"
        week.entries[0].recipe_id = "recipe-1"

        shopping_list = build_week_shopping_list(
            week,
            [
                build_recipe(
                    "recipe-1",
                    "Risotto",
                    "Book",
                    ingredients=[
                        {
                            "raw": "1.5 litres organic chicken, ham or vegetable stock",
                            "normalized_name": "5 litres organic chicken ham or vegetable stock",
                            "canonical_name": "stock",
                        }
                    ],
                )
            ],
        )

        self.assertEqual(
            [item.name for item in shopping_list],
            ["1.5 litres organic chicken, ham or vegetable stock"],
        )


if __name__ == "__main__":
    unittest.main()
