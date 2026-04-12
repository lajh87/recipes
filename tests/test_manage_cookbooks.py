from types import SimpleNamespace
import unittest

from app.main import enrich_cookbooks_for_management
from app.models import RecipeRecord


def build_recipe(
    *,
    recipe_id: str,
    tags: list[str],
    extraction_reasons: list[str] | None = None,
    has_images: bool = False,
) -> RecipeRecord:
    return RecipeRecord.model_validate(
        {
            "id": recipe_id,
            "cookbook_id": "book-1",
            "cookbook_title": "Book",
            "title": f"Recipe {recipe_id}",
            "ingredients": [],
            "ingredient_names": [],
            "method_steps": [],
            "images": [{"object_key": "images/recipe.jpg", "content_type": "image/jpeg"}] if has_images else [],
            "source": {
                "object_key": "ebooks/book.epub",
                "format": "epub",
                "excerpt": "Recipe excerpt.",
                "metadata": {"tags": tags},
            },
            "extraction": {
                "model": "gpt-5.4-mini",
                "confidence": 0.9,
                "notes": [],
                "extracted_at": "2026-04-12T00:00:00Z",
                "needs_review_reasons": extraction_reasons or [],
            },
            "review": {"status": "pending_review"},
        }
    )


class StubRepository:
    def __init__(self, recipes: list[RecipeRecord]) -> None:
        self.recipes = recipes

    def list_recipes(self, *, cookbook_id: str) -> list[RecipeRecord]:
        assert cookbook_id == "book-1"
        return self.recipes


class ManageCookbooksTests(unittest.TestCase):
    def test_enrich_cookbooks_for_management_uses_recipe_tags_and_extraction_counts(self) -> None:
        cookbook = SimpleNamespace(
            id="book-1",
            model_dump=lambda: {
                "id": "book-1",
                "title": "Book",
                "author": "Author",
                "cuisine": "Italian",
                "published_at": "2024",
                "collection_slug": "",
                "filename": "book.epub",
                "object_key": "ebooks/book.epub",
                "size_bytes": 100,
                "content_type": "application/epub+zip",
                "uploaded_at": "2026-04-12T00:00:00Z",
                "status": "ready",
                "recipe_count": 2,
                "needs_review_count": 0,
                "extract_attempted_at": None,
                "extract_completed_at": None,
                "extract_error": None,
                "cover_image_key": None,
                "cover_image_content_type": None,
                "cover_extract_attempted_at": None,
                "metadata_extract_attempted_at": None,
            }
        )
        repository = StubRepository(
            [
                build_recipe(
                    recipe_id="recipe-1",
                    tags=["Vegetarian", "Weeknight", "Weeknight"],
                    extraction_reasons=["missing_steps", "missing_steps", "unclear_title"],
                    has_images=True,
                ),
                build_recipe(
                    recipe_id="recipe-2",
                    tags=["Vegetarian"],
                    extraction_reasons=["missing_steps"],
                ),
            ]
        )

        enriched = enrich_cookbooks_for_management(repository, [cookbook])

        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0].photo_recipe_percent, 50)
        self.assertEqual(enriched[0].tag_counts, [("Vegetarian", 2), ("Weeknight", 1)])
        self.assertEqual(
            enriched[0].extraction_reason_counts,
            [("missing_steps", 2), ("unclear_title", 1)],
        )


if __name__ == "__main__":
    unittest.main()
