import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.main import app, index, normalize_forwarded_prefix


class NavigationTests(unittest.TestCase):
    def test_root_route_delegates_to_meal_plan_page(self) -> None:
        request = object()
        response = object()
        handler = AsyncMock(return_value=response)

        with patch("app.main.meal_plan_page", handler):
            result = asyncio.run(index(request=request, notice="Saved"))

        handler.assert_awaited_once_with(request=request, notice="Saved")
        self.assertIs(result, response)

    def test_named_routes_expose_meal_plan_and_library_paths(self) -> None:
        self.assertEqual(str(app.url_path_for("index")), "/")
        self.assertEqual(str(app.url_path_for("meal_plan_page")), "/meal-plan")
        self.assertEqual(str(app.url_path_for("library_page")), "/library")
        self.assertEqual(str(app.url_path_for("manage_recipe_tags_page")), "/library/tags")
        self.assertEqual(str(app.url_path_for("preview_recipe_import")), "/api/recipe-imports/preview")
        self.assertEqual(
            str(app.url_path_for("commit_recipe_import", import_id="draft-1")),
            "/api/recipe-imports/draft-1/commit",
        )

    def test_forwarded_prefix_is_normalized_for_proxy_mounts(self) -> None:
        self.assertEqual(normalize_forwarded_prefix(None), "")
        self.assertEqual(normalize_forwarded_prefix(""), "")
        self.assertEqual(normalize_forwarded_prefix("/"), "")
        self.assertEqual(normalize_forwarded_prefix("recipes"), "/recipes")
        self.assertEqual(normalize_forwarded_prefix("/recipes/"), "/recipes")

    def test_base_nav_lists_meal_plan_before_library_without_tags(self) -> None:
        base_template = (Path(__file__).resolve().parent.parent / "app" / "templates" / "base.html").read_text()

        meal_plan_link = "<a href=\"{{ url_for('meal_plan_page') }}\">Meal Plan</a>"
        library_link = "<a href=\"{{ url_for('library_page') }}\">Library</a>"
        tags_link = "<a href=\"{{ url_for('manage_recipe_tags_page') }}\">Tags</a>"

        self.assertIn(meal_plan_link, base_template)
        self.assertIn(library_link, base_template)
        self.assertNotIn(tags_link, base_template)
        self.assertLess(base_template.index(meal_plan_link), base_template.index(library_link))

    def test_shared_url_importer_is_available_from_library_and_meal_plan(self) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        index_template = (base_dir / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        meal_plan_template = (base_dir / "app" / "templates" / "meal_plan.html").read_text(encoding="utf-8")
        importer_template = (base_dir / "app" / "templates" / "_recipe_url_import_dialog.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("data-open-recipe-import", index_template)
        self.assertIn('{% include "_recipe_url_import_dialog.html" %}', index_template)
        self.assertIn('{% include "_recipe_url_import_dialog.html" %}', meal_plan_template)
        self.assertIn("data-recipe-import-dialog", importer_template)

    def test_manage_metadata_links_to_recipe_tags(self) -> None:
        metadata_template = (
            Path(__file__).resolve().parent.parent / "app" / "templates" / "manage_cookbooks.html"
        ).read_text()

        tags_link = "<a class=\"button button--secondary\" href=\"{{ url_for('manage_recipe_tags_page') }}\">Manage Tags</a>"
        self.assertIn(tags_link, metadata_template)


if __name__ == "__main__":
    unittest.main()
