import unittest

from app.nytimes_pdf import (
    PdfBlock,
    _column_split,
    _extract_author,
    _extract_header_details,
    _extract_ingredients,
    _extract_method_content,
    _extract_publication_details,
    _extract_title,
)


class NytimesPdfTests(unittest.TestCase):
    def test_parses_nytimes_layout_blocks(self) -> None:
        blocks = [
            PdfBlock(page_number=1, x0=56.7, y0=58.9, text="Asparagus and Tofu With Black Bean Sauce"),
            PdfBlock(page_number=1, x0=56.7, y0=100.6, text="By Hetty Lui McKinnon"),
            PdfBlock(page_number=1, x0=56.7, y0=120.1, text="Published June 13, 2025"),
            PdfBlock(page_number=1, x0=56.7, y0=184.6, text="Total Time 20 minutes"),
            PdfBlock(page_number=1, x0=56.7, y0=205.5, text="Prep Time 10 minutes"),
            PdfBlock(page_number=1, x0=56.7, y0=224.2, text="Cook Time 10 minutes"),
            PdfBlock(page_number=1, x0=56.7, y0=243.8, text="Rating (106)"),
            PdfBlock(page_number=1, x0=56.7, y0=288.4, text="Intro text about the recipe."),
            PdfBlock(page_number=1, x0=56.7, y0=510.0, text="INGREDIENTS"),
            PdfBlock(page_number=1, x0=56.7, y0=547.6, text="Yield: 4 servings"),
            PdfBlock(page_number=1, x0=56.7, y0=586.6, text="1 tablespoon Shaoxing wine or dry sherry"),
            PdfBlock(page_number=1, x0=56.7, y0=621.1, text="1 garlic clove, finely chopped"),
            PdfBlock(page_number=1, x0=248.7, y0=510.0, text="PREPARATION"),
            PdfBlock(page_number=1, x0=248.7, y0=547.3, text="Step 1"),
            PdfBlock(page_number=1, x0=248.7, y0=566.1, text="Do the first thing."),
            PdfBlock(page_number=1, x0=248.7, y0=650.8, text="Step 2"),
            PdfBlock(page_number=1, x0=248.7, y0=669.6, text="Do the second thing."),
            PdfBlock(page_number=1, x0=248.7, y0=720.0, text="TIP"),
            PdfBlock(page_number=1, x0=248.7, y0=740.0, text="Serve with rice."),
            PdfBlock(page_number=1, x0=248.7, y0=760.0, text="Ratings"),
            PdfBlock(page_number=1, x0=248.7, y0=780.0, text="Private Notes"),
        ]

        split = _column_split(blocks, 595.0)
        yield_text, ingredients, ingredient_sections = _extract_ingredients(blocks, split)
        steps, preparation_notes, supplemental_sections = _extract_method_content(blocks, split)
        published_at, updated_at = _extract_publication_details(blocks)
        header_details = _extract_header_details(blocks)

        self.assertEqual(_extract_title(blocks), "Asparagus and Tofu With Black Bean Sauce")
        self.assertEqual(_extract_author(blocks), "Hetty Lui McKinnon")
        self.assertEqual(published_at, "June 13, 2025")
        self.assertEqual(updated_at, "")
        self.assertEqual(
            header_details,
            {
                "total_time": "20 minutes",
                "prep_time": "10 minutes",
                "cook_time": "10 minutes",
            },
        )
        self.assertEqual(yield_text, "Yield: 4 servings")
        self.assertEqual(
            ingredients,
            [
                "1 tablespoon Shaoxing wine or dry sherry",
                "1 garlic clove, finely chopped",
            ],
        )
        self.assertEqual(steps, ["Do the first thing.", "Do the second thing."])
        self.assertEqual(preparation_notes, [])
        self.assertEqual(ingredient_sections, [])
        self.assertEqual(
            supplemental_sections,
            [
                {
                    "heading": "Tip",
                    "lines": ["Serve with rice."],
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
