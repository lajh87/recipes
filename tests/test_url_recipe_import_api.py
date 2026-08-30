import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.config import Settings
from app.main import commit_recipe_import, preview_recipe_import
from app.models import RecipeImportCommitRequest, RecipeImportPreviewRequest
from app.url_recipe_import import UrlRecipeImportError


def fake_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                repository=object(),
                settings=Settings(openai_api_key="test-key"),
            )
        ),
        url_for=lambda name, **values: (
            f"http://test/recipes/{values['recipe_id']}"
            if name == "recipe_page"
            else f"http://test/collections/{values['collection_slug']}"
        ),
    )


class RecipeImportApiTests(unittest.TestCase):
    def test_preview_existing_recipe_returns_navigation_urls(self) -> None:
        importer = Mock()
        importer.preview.return_value = {
            "status": "existing",
            "recipe_id": "recipe-1",
            "title": "Lemon Pasta",
            "cookbook_title": "Example Kitchen",
            "collection_slug": "other",
            "collection_title": "Other",
        }

        with patch("app.main.UrlRecipeImporter", return_value=importer):
            response = asyncio.run(
                preview_recipe_import(
                    RecipeImportPreviewRequest(url="https://example.com/recipe"),
                    fake_request(),
                )
            )

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["recipe_url"], "http://test/recipes/recipe-1?collection=other")
        self.assertEqual(payload["collection_url"], "http://test/collections/other")

    def test_preview_error_returns_clear_code_and_message(self) -> None:
        importer = Mock()
        importer.preview.side_effect = UrlRecipeImportError(
            "This recipe page is blocked or requires a login.",
            status_code=422,
            code="source_blocked",
        )

        with patch("app.main.UrlRecipeImporter", return_value=importer):
            response = asyncio.run(
                preview_recipe_import(
                    RecipeImportPreviewRequest(url="https://example.com/recipe"),
                    fake_request(),
                )
            )

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["code"], "source_blocked")
        self.assertIn("requires a login", payload["detail"])

    def test_commit_returns_created_recipe_navigation(self) -> None:
        importer = Mock()
        importer.commit.return_value = {
            "status": "created",
            "recipe_id": "recipe-2",
            "title": "Lemon Pasta",
            "cookbook_title": "Example Kitchen",
            "collection_slug": "other",
            "collection_title": "Other",
        }

        with patch("app.main.UrlRecipeImporter", return_value=importer):
            response = asyncio.run(
                commit_recipe_import(
                    "draft-1",
                    RecipeImportCommitRequest(
                        title="Lemon Pasta",
                        ingredient_lines=["1 lemon"],
                        method_steps=["Cook it."],
                    ),
                    fake_request(),
                )
            )

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "created")
        self.assertEqual(payload["recipe_url"], "http://test/recipes/recipe-2?collection=other")


if __name__ == "__main__":
    unittest.main()
