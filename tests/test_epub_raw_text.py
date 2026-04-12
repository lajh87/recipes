from pathlib import Path
import tempfile
import unittest

from ebooklib import epub

from app.main import extract_epub_recipe_raw_text


class EpubRawTextTests(unittest.TestCase):
    def test_extract_epub_recipe_raw_text_uses_fragment_anchor(self) -> None:
        book = epub.EpubBook()
        book.set_identifier("test-book")
        book.set_title("Test Book")
        book.set_language("en")

        recipe_doc = epub.EpubHtml(
            title="Recipe",
            file_name="html/recipe.xhtml",
            content=(
                "<html><body>"
                '<h3 id="recipe-1">Recipe One</h3>'
                "<p>Line one.</p>"
                "<p>Line two.</p>"
                "</body></html>"
            ),
        )
        book.add_item(recipe_doc)
        book.spine = ["nav", recipe_doc]
        book.add_item(epub.EpubNav())
        book.add_item(epub.EpubNcx())

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.epub"
            epub.write_epub(str(path), book)
            text = extract_epub_recipe_raw_text(path.read_bytes(), "html/recipe.xhtml#recipe-1")

        self.assertEqual(text, "Recipe One\nLine one.\nLine two.")


if __name__ == "__main__":
    unittest.main()
