import unittest

from app.models import CookbookItem
from app.repository import LibraryRepository


def build_cookbook(title: str, filename: str) -> CookbookItem:
    return CookbookItem(
        id="book-1",
        title=title,
        author=None,
        cuisine=None,
        published_at=None,
        collection_slug=None,
        filename=filename,
        object_key="uploads/book-1.epub",
        size_bytes=1024,
        content_type="application/epub+zip",
        uploaded_at="2026-04-12T00:00:00Z",
        status="uploaded",
        recipe_count=12,
        needs_review_count=0,
    )


class RecipeCollectionTests(unittest.TestCase):
    def test_favourites_collection_is_listed_first(self) -> None:
        repository = object.__new__(LibraryRepository)
        repository.list_recipes_for_collection = lambda slug: []  # type: ignore[method-assign]

        collections = repository.list_recipe_collections()

        self.assertGreaterEqual(len(collections), 1)
        self.assertEqual(collections[0].slug, "favourites")
        self.assertEqual(collections[0].title, "Favourites")

    def test_collection_slug_can_be_assigned_explicitly(self) -> None:
        cookbook = build_cookbook(
            title="NYT Lemon Chicken",
            filename="nyt-lemon-chicken.epub",
        ).model_copy(update={"collection_slug": "nytimes"})

        self.assertEqual(cookbook.collection_slug, "nytimes")

    def test_bbc_good_food_collection_is_listed(self) -> None:
        repository = object.__new__(LibraryRepository)
        repository.list_recipes_for_collection = lambda slug: []  # type: ignore[method-assign]

        collections = repository.list_recipe_collections()

        self.assertIn("bbc-goodfood", [collection.slug for collection in collections])

    def test_waitrose_recipes_collection_is_listed(self) -> None:
        repository = object.__new__(LibraryRepository)
        repository.list_recipes_for_collection = lambda slug: []  # type: ignore[method-assign]

        collections = repository.list_recipe_collections()

        self.assertIn("waitrose-recipes", [collection.slug for collection in collections])

    def test_display_title_strips_library_suffixes(self) -> None:
        repository = object.__new__(LibraryRepository)

        self.assertEqual(
            repository._display_title("the-cookbook-(ZLibrary.sk)-[1Lib.sk].epub"),
            "The Cookbook",
        )
        self.assertEqual(
            repository._clean_metadata_text(
                "Mowgli Street Food Authentic Indian Street Food (Nisha Katona) (Z Library.Sk, 1Lib.Sk, Z Lib.Sk)"
            ),
            "Mowgli Street Food Authentic Indian Street Food (Nisha Katona)",
        )

    def test_sorts_cookbooks_by_author(self) -> None:
        repository = object.__new__(LibraryRepository)
        books = [
            build_cookbook(title="Z Book", filename="z.epub").model_copy(update={"author": "Zadie Smith"}),
            build_cookbook(title="A Book", filename="a.epub").model_copy(update={"author": "Alice Waters"}),
        ]

        sorted_books = repository._sort_cookbooks(books, "author")

        self.assertEqual([book.title for book in sorted_books], ["A Book", "Z Book"])

    def test_normalizes_published_date_for_sorting(self) -> None:
        repository = object.__new__(LibraryRepository)

        self.assertEqual(repository._normalize_published_at("D:20190517"), "2019-05-17")


if __name__ == "__main__":
    unittest.main()
