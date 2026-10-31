import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.meal_plan import meal_plan_from_dict
from app.models import RecipeRecord


class MealPlanRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.dumps({"weeks": [
            {"id": "latest", "start_on": "2026-10-25", "notes": "Buy fruit", "entries": [
                {"id": "linked", "title": "Lime rice", "recipe_id": "rice", "completed": True},
                {"id": "custom", "title": "Leftovers"},
            ]},
            {"id": "archive", "start_on": "2026-10-18", "entries": [
                {"id": "linked", "title": "Archive meal"},
            ]},
        ]})
        recipe = RecipeRecord.model_validate({
            "id": "rice", "cookbook_id": "book", "cookbook_title": "Family", "title": "Lime rice",
            "ingredients": [{"raw": "1 lime", "normalized_name": "lime", "canonical_name": "lime"}],
            "ingredient_names": ["lime"], "method_steps": [],
            "source": {"object_key": "book.epub", "format": "epub"},
            "extraction": {"model": "test", "extracted_at": "2026-10-25T00:00:00Z"},
            "review": {"status": "verified"},
        })
        self.repository = Mock()
        self.repository.list_recipe_references.return_value = [recipe]
        self.repository.list_recipes.return_value = [recipe]
        self.repository.load_meal_plan_payload.side_effect = lambda: self.payload
        self.repository.save_meal_plan_payload.side_effect = self.save_payload
        self.repository.health_report.return_value = {"datastores": []}
        for attribute, value in [("repository", self.repository), ("settings", get_settings())]:
            patcher = patch.object(app.state, attribute, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app)

    def save_payload(self, payload: str) -> None:
        self.payload = payload

    def page(self) -> BeautifulSoup:
        response = self.client.get("/meal-plan")
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.text, "html.parser")

    def submit(self, action: str, *, changes: dict | None = None, as_json: bool = False):
        # Submit the actual rendered controls, including all archived weeks.
        fields = {}
        for control in self.page().select("[data-meal-plan-form] input[name], [data-meal-plan-form] textarea[name]"):
            if control.get("type") == "checkbox" and not control.has_attr("checked"):
                continue
            value = control.text if control.name == "textarea" else control.get("value", "")
            fields.setdefault(control["name"], []).append(value)
        fields.update(changes or {})
        fields["planner_action"] = action
        return self.client.post("/meal-plan", data=fields,
                                headers={"Accept": "application/json"} if as_json else {},
                                follow_redirects=False)

    def test_delete_row_preserves_edits_and_other_weeks_and_updates_shopping(self) -> None:
        response = self.submit("remove_row:latest:linked", as_json=True,
                               changes={"entry__latest__custom__title": "Edited leftovers",
                                        "week_notes__latest": "Buy fruit"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stats"], {
            "weeks": 2, "slot_count": 2, "linked_slot_count": 0, "completed_slot_count": 0,
        })
        weeks = json.loads(self.payload)["weeks"]
        self.assertEqual([row["id"] for row in weeks[0]["entries"]], ["custom"])
        self.assertEqual(weeks[0]["entries"][0]["title"], "Edited leftovers")
        self.assertEqual(weeks[0]["notes"], "Buy fruit")
        self.assertEqual(weeks[1]["entries"][0]["title"], "Archive meal")
        self.assertFalse(self.page().select(".shopping-checklist__item"))

    def test_delete_last_row_stays_empty_after_reload_and_autosave_and_can_add(self) -> None:
        self.submit("remove_row:latest:linked")
        response = self.submit("remove_row:latest:custom")
        self.assertEqual(response.status_code, 303)
        self.assertIn("Deleted%20the%20row", response.headers["location"])
        latest = self.page().select_one(".meal-plan-week--latest")
        self.assertFalse(latest.select("[data-meal-plan-row]"))
        self.assertIn("No meals planned", latest.text)
        self.assertIsNotNone(latest.select_one('button[value="add_row:latest"]'))
        self.submit("autosave", as_json=True)
        self.assertEqual(json.loads(self.payload)["weeks"][0]["entries"], [])
        self.submit("add_row:latest")
        self.assertEqual(len(self.page().select(".meal-plan-week--latest [data-meal-plan-row]")), 1)

    def test_delete_archived_row_keeps_latest_week(self) -> None:
        self.submit("remove_row:archive:linked")
        weeks = json.loads(self.payload)["weeks"]
        self.assertEqual(len(weeks[0]["entries"]), 2)
        self.assertEqual(weeks[1]["entries"], [])
        self.assertIsNotNone(self.page().select_one(".meal-plan-week--archive .meal-plan-week__empty"))

    def test_missing_or_stale_row_does_not_delete_other_rows(self) -> None:
        for action in ["remove_row:latest:missing", "remove_row:missing:linked", "remove_row:latest"]:
            with self.subTest(action=action):
                self.submit(action)
                self.assertEqual(len(json.loads(self.payload)["weeks"][0]["entries"]), 2)

    def test_existing_empty_week_has_no_delete_buttons_and_adds_exactly_one_row(self) -> None:
        self.payload = '{"weeks":[{"id":"empty","start_on":"2026-10-25","entries":[]}]}'
        self.assertFalse(self.page().select(".meal-plan-row__delete"))
        response = self.submit("autosave", as_json=True)
        self.assertEqual(response.json()["stats"], {
            "weeks": 1, "slot_count": 0, "linked_slot_count": 0, "completed_slot_count": 0,
        })
        self.submit("add_row:empty")
        self.assertEqual(len(json.loads(self.payload)["weeks"][0]["entries"]), 1)

    def test_empty_entries_do_not_restore_stale_legacy_meals(self) -> None:
        payload = {"weeks": [{"entries": [], "days": [
            {"label": "Monday", "meals": [{"title": "Old meal", "meal_label": "Dinner"}]},
        ]}]}
        document = meal_plan_from_dict(payload, Path("/tmp"))
        self.assertEqual(document.weeks[0].entries, [])

    def test_legacy_meals_still_load_when_entries_are_absent(self) -> None:
        payload = {"weeks": [{"days": [
            {"label": "Monday", "meals": [{"title": "Old meal", "meal_label": "Dinner"}]},
        ]}]}
        document = meal_plan_from_dict(payload, Path("/tmp"))
        self.assertEqual(document.weeks[0].entries[0].title, "Old meal")


if __name__ == "__main__":
    unittest.main()
