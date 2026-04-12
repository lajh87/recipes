import unittest
from pathlib import Path
import tempfile

from app.meal_plan import (
    append_blank_row,
    create_blank_week,
    import_recent_weeks_from_text,
    load_or_import_meal_plan,
    parse_meal_plan_form,
    recipe_option_value,
    resolve_recipe_reference,
)
from app.models import RecipeRecord


def build_recipe(recipe_id: str, title: str, cookbook_title: str = "Book") -> RecipeRecord:
    return RecipeRecord.model_validate(
        {
            "id": recipe_id,
            "cookbook_id": "book-1",
            "cookbook_title": cookbook_title,
            "title": title,
            "ingredients": [],
            "ingredient_names": [],
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
                f"week_title__{week.id}": "Week 1",
                f"week_notes__{week.id}": "Buy limes",
                f"entry__{week.id}__{row.id}__title": "Fish tacos",
                f"entry__{week.id}__{row.id}__weekday": "Monday",
                f"entry__{week.id}__{row.id}__meal": "Dinner",
                f"entry__{week.id}__{row.id}__completed": "true",
                f"entry__{week.id}__{row.id}__recipe_ref": recipe_option_value(recipes[0]),
            }
        )

        document = parse_meal_plan_form(form, recipes, base_dir=Path("/tmp"))
        entry = document.weeks[0].entries[0]

        self.assertEqual(document.weeks[0].notes, "Buy limes")
        self.assertEqual(entry.title, "Fish tacos")
        self.assertEqual(entry.weekday, "Monday")
        self.assertEqual(entry.meal, "Dinner")
        self.assertTrue(entry.completed)
        self.assertEqual(entry.recipe_id, "recipe-1")

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


if __name__ == "__main__":
    unittest.main()
