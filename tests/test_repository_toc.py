import unittest

from app.repository import LibraryRepository


class RepositoryTocTests(unittest.TestCase):
    def test_hydrate_cookbook_parses_table_of_contents(self) -> None:
        repository = object.__new__(LibraryRepository)

        cookbook = repository._hydrate_cookbook(
            {
                "id": "book-1",
                "title": "Book",
                "author": "Author",
                "cuisine": "Italian",
                "published_at": "2024",
                "collection_slug": "",
                "filename": "book.epub",
                "object_key": "ebooks/book.epub",
                "size_bytes": "100",
                "content_type": "application/epub+zip",
                "uploaded_at": "2026-04-12T00:00:00Z",
                "status": "extracted",
                "recipe_count": "2",
                "needs_review_count": "0",
                "extract_attempted_at": "",
                "extract_completed_at": "",
                "extract_error": "",
                "cover_image_key": "",
                "cover_image_content_type": "",
                "cover_extract_attempted_at": "",
                "metadata_extract_attempted_at": "",
                "table_of_contents": (
                    '[{"label":"Contents","href":"pages/contents.xhtml","children":['
                    '{"label":"Vegetables","href":"pages/chapter_001.xhtml","children":[]}]}]'
                ),
            }
        )

        self.assertEqual(len(cookbook.table_of_contents), 1)
        self.assertEqual(cookbook.table_of_contents[0].label, "Contents")
        self.assertEqual(cookbook.table_of_contents[0].children[0].label, "Vegetables")


if __name__ == "__main__":
    unittest.main()
