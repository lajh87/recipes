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

    def test_manage_metadata_links_to_recipe_tags(self) -> None:
        metadata_template = (
            Path(__file__).resolve().parent.parent / "app" / "templates" / "manage_cookbooks.html"
        ).read_text()

        tags_link = "<a class=\"button button--secondary\" href=\"{{ url_for('manage_recipe_tags_page') }}\">Manage Tags</a>"
        self.assertIn(tags_link, metadata_template)


if __name__ == "__main__":
    unittest.main()
