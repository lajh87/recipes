import unittest

from app.main import build_recipe_page_metadata
from app.models import RecipeRecord


def build_recipe(*, source_metadata=None, excerpt="Recipe excerpt.") -> RecipeRecord:
    return RecipeRecord.model_validate(
        {
            "id": "recipe-1",
            "cookbook_id": "book-1",
            "cookbook_title": "Book",
            "title": "Roast Tomatoes",
            "ingredients": [],
            "ingredient_names": [],
            "method_steps": [],
            "images": [],
            "source": {
                "object_key": "ebooks/book.epub",
                "format": "epub",
                "chapter_title": "Vegetables",
                "anchor": "xhtml/06_Vegetables.xhtml#recipe_1",
                "excerpt": excerpt,
                "metadata": source_metadata or {},
            },
            "extraction": {
                "model": "gpt-5.4-mini",
                "confidence": 0.9,
                "notes": [],
                "extracted_at": "2026-04-12T00:00:00Z",
                "needs_review_reasons": [],
            },
            "review": {"status": "pending_review"},
        }
    )


class RecipePageMetadataTests(unittest.TestCase):
    def test_build_recipe_page_metadata_includes_intro_and_extra_metadata(self) -> None:
        recipe = build_recipe(
            source_metadata={
                "author": "Hetty Lui McKinnon",
                "published_at": "June 13, 2025",
                "yield": "4 servings",
                "prep_time": "10 minutes",
                "cook_time": "10 minutes",
                "intro": "First paragraph.\n\nSecond paragraph.",
                "description": "A bright, savory tomato dish.",
                "difficulty": "Easy",
                "tags": ["Vegetarian", "Weeknight"],
                "preparation_notes": ["Salt the tomatoes first."],
                "supplemental_sections": [
                    {
                        "heading": "Tip",
                        "lines": ["Serve with whipped feta."],
                    }
                ],
            }
        )

        metadata = build_recipe_page_metadata(recipe)

        self.assertEqual(metadata.intro_heading, "Introduction")
        self.assertEqual(metadata.intro_paragraphs, ["First paragraph.", "Second paragraph."])
        self.assertEqual(
            metadata.summary_items,
            [
                {"label": "Author", "value": "Hetty Lui McKinnon"},
                {"label": "Published", "value": "June 13, 2025"},
                {"label": "Yield", "value": "4 servings"},
                {"label": "Timing", "value": "Prep 10 minutes · Cook 10 minutes"},
                {"label": "Difficulty", "value": "Easy"},
            ],
        )
        self.assertEqual(
            metadata.text_blocks,
            [{"heading": "Description", "paragraphs": ["A bright, savory tomato dish."]}],
        )
        self.assertEqual(
            metadata.list_blocks,
            [
                {"heading": "Notes", "items": ["Salt the tomatoes first."]},
                {"heading": "Tags", "items": ["Vegetarian", "Weeknight"]},
            ],
        )
        self.assertEqual(
            metadata.sections,
            [{"heading": "Tip", "lines": ["Serve with whipped feta."]}],
        )

    def test_build_recipe_page_metadata_falls_back_to_excerpt(self) -> None:
        recipe = build_recipe(source_metadata={}, excerpt="A short source excerpt.")

        metadata = build_recipe_page_metadata(recipe)

        self.assertEqual(metadata.intro_heading, "Excerpt")
        self.assertEqual(metadata.intro_paragraphs, ["A short source excerpt."])

    def test_build_recipe_page_metadata_ignores_excerpt_that_matches_title(self) -> None:
        recipe = build_recipe(source_metadata={}, excerpt="Roast Tomatoes")

        metadata = build_recipe_page_metadata(recipe)

        self.assertEqual(metadata.intro_paragraphs, [])


if __name__ == "__main__":
    unittest.main()
