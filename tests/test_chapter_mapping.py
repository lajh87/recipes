import unittest

from app.epub import (
    _parse_html_toc_entries,
    _parse_ncx_toc_entries,
    build_chapter_map_from_toc_entries,
)
from app.main import (
    build_recipe_sections_from_source_toc,
    chapter_label_for_recipe,
    flatten_cookbook_toc_labels,
    order_recipe_sections,
)
from app.models import CookbookTocEntry, RecipeRecord


class ChapterMappingTests(unittest.TestCase):
    def test_html_toc_resolves_relative_paths(self) -> None:
        content = b"""
        <html xmlns="http://www.w3.org/1999/xhtml">
          <body>
            <nav epub:type="toc">
              <ol>
                <li>
                  <a href="10_Chapter01.xhtml#ch01">Dips</a>
                  <ol>
                    <li><a href="10_Chapter01.xhtml#pg34lev1">Arabica Hummus</a></li>
                  </ol>
                </li>
                <li>
                  <a href="11_Chapter02.xhtml#ch02">Hot Meze</a>
                  <ol>
                    <li><a href="11_Chapter02.xhtml#pg59lev1">Hummus with Spiced Lamb</a></li>
                  </ol>
                </li>
              </ol>
            </nav>
          </body>
        </html>
        """

        entries = _parse_html_toc_entries(content, item_href="xhtml/00_Nav.xhtml")
        mapping = build_chapter_map_from_toc_entries(
            entries,
            spine_paths=["xhtml/10_Chapter01.xhtml", "xhtml/11_Chapter02.xhtml"],
            recipe_paths={"xhtml/10_Chapter01.xhtml", "xhtml/11_Chapter02.xhtml"},
        )

        self.assertEqual(
            mapping,
            {
                "xhtml/10_Chapter01.xhtml": "Dips",
                "xhtml/11_Chapter02.xhtml": "Hot Meze",
            },
        )

    def test_ncx_nested_chapters_cover_recipe_files(self) -> None:
        content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
          <navMap>
            <navPoint id="chapter-1">
              <navLabel><text>1. Tight for Time</text></navLabel>
              <content src="text/Chapter01.xhtml#sec_1" />
              <navPoint id="recipe-1">
                <navLabel><text>Recipe One</text></navLabel>
                <content src="text/Recipe01.xhtml#sec_2" />
              </navPoint>
              <navPoint id="recipe-2">
                <navLabel><text>Recipe Two</text></navLabel>
                <content src="text/Recipe02.xhtml#sec_3" />
              </navPoint>
            </navPoint>
            <navPoint id="chapter-2">
              <navLabel><text>2. Slow Sundays</text></navLabel>
              <content src="text/Chapter02.xhtml#sec_4" />
              <navPoint id="recipe-3">
                <navLabel><text>Recipe Three</text></navLabel>
                <content src="text/Recipe03.xhtml#sec_5" />
              </navPoint>
            </navPoint>
          </navMap>
        </ncx>
        """

        entries = _parse_ncx_toc_entries(content, item_href="toc.ncx")
        mapping = build_chapter_map_from_toc_entries(
            entries,
            spine_paths=[
                "text/Chapter01.xhtml",
                "text/Recipe01.xhtml",
                "text/Recipe02.xhtml",
                "text/Chapter02.xhtml",
                "text/Recipe03.xhtml",
            ],
            recipe_paths={"text/Recipe01.xhtml", "text/Recipe02.xhtml", "text/Recipe03.xhtml"},
        )

        self.assertEqual(mapping["text/Recipe01.xhtml"], "1. Tight for Time")
        self.assertEqual(mapping["text/Recipe02.xhtml"], "1. Tight for Time")
        self.assertEqual(mapping["text/Recipe03.xhtml"], "2. Slow Sundays")

    def test_nested_recipe_anchors_do_not_override_same_file_chapter_label(self) -> None:
        content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
          <navMap>
            <navPoint id="chapter-1">
              <navLabel><text>Breakfast &amp; Brunch</text></navLabel>
              <content src="text/Chapter01.xhtml#chapter1" />
              <navPoint id="recipe-1">
                <navLabel><text>Recipe One</text></navLabel>
                <content src="text/Chapter01.xhtml#recipe1" />
              </navPoint>
              <navPoint id="recipe-2">
                <navLabel><text>Recipe Two</text></navLabel>
                <content src="text/Chapter01.xhtml#recipe2" />
              </navPoint>
            </navPoint>
            <navPoint id="chapter-2">
              <navLabel><text>Streetfood</text></navLabel>
              <content src="text/Chapter02.xhtml#chapter2" />
              <navPoint id="recipe-3">
                <navLabel><text>Recipe Three</text></navLabel>
                <content src="text/Chapter02.xhtml#recipe3" />
              </navPoint>
            </navPoint>
          </navMap>
        </ncx>
        """

        entries = _parse_ncx_toc_entries(content, item_href="toc.ncx")
        mapping = build_chapter_map_from_toc_entries(
            entries,
            spine_paths=["text/Chapter01.xhtml", "text/Chapter02.xhtml"],
            recipe_paths={"text/Chapter01.xhtml", "text/Chapter02.xhtml"},
        )

        self.assertEqual(mapping["text/Chapter01.xhtml"], "Breakfast & Brunch")
        self.assertEqual(mapping["text/Chapter02.xhtml"], "Streetfood")

    def test_flat_toc_chapter_markers_apply_to_following_recipe_files(self) -> None:
        content = b"""
        <html xmlns="http://www.w3.org/1999/xhtml">
          <body>
            <nav epub:type="toc">
              <ol>
                <li><a href="10_STARTERS.xhtml">STARTERS</a></li>
                <li><a href="35_MAINS.xhtml">MAINS</a></li>
              </ol>
            </nav>
          </body>
        </html>
        """

        entries = _parse_html_toc_entries(content, item_href="xhtml/nav.xhtml")
        mapping = build_chapter_map_from_toc_entries(
            entries,
            spine_paths=[
                "xhtml/10_STARTERS.xhtml",
                "xhtml/13_recipe.xhtml",
                "xhtml/14_recipe.xhtml",
                "xhtml/35_MAINS.xhtml",
                "xhtml/36_recipe.xhtml",
            ],
            recipe_paths={"xhtml/13_recipe.xhtml", "xhtml/14_recipe.xhtml", "xhtml/36_recipe.xhtml"},
        )

        self.assertEqual(mapping["xhtml/13_recipe.xhtml"], "STARTERS")
        self.assertEqual(mapping["xhtml/14_recipe.xhtml"], "STARTERS")
        self.assertEqual(mapping["xhtml/36_recipe.xhtml"], "MAINS")

    def test_flat_toc_single_file_sections_map_when_recipe_file_matches_heading(self) -> None:
        content = b"""
        <html xmlns="http://www.w3.org/1999/xhtml">
          <body>
            <nav epub:type="toc">
              <ol>
                <li><a href="06_Vegetables.xhtml">Vegetables</a></li>
                <li><a href="07_Fish.xhtml">Fish &amp; Shellfish</a></li>
              </ol>
            </nav>
          </body>
        </html>
        """

        entries = _parse_html_toc_entries(content, item_href="xhtml/nav.xhtml")
        mapping = build_chapter_map_from_toc_entries(
            entries,
            spine_paths=["xhtml/06_Vegetables.xhtml", "xhtml/07_Fish.xhtml"],
            recipe_paths={"xhtml/06_Vegetables.xhtml", "xhtml/07_Fish.xhtml"},
        )

        self.assertEqual(
            mapping,
            {
                "xhtml/06_Vegetables.xhtml": "Vegetables",
                "xhtml/07_Fish.xhtml": "Fish & Shellfish",
            },
        )

    def test_nested_grouped_recipe_leaves_prefer_parent_section_labels(self) -> None:
        entries = [
            CookbookTocEntry(
                label="PROCESS",
                href="index_split_008.html",
                children=[
                    CookbookTocEntry(
                        label="Charring",
                        href="index_split_008.html",
                        children=[
                            CookbookTocEntry(
                                label="CALVIN'S GRILLED PEACHES AND RUNNER BEANS",
                                href="index_split_015.html",
                                children=[],
                            ),
                            CookbookTocEntry(
                                label="ICEBERG WEDGES WITH SMOKY AUBERGINE CREAM",
                                href="index_split_017.html",
                                children=[],
                            ),
                        ],
                    ),
                    CookbookTocEntry(
                        label="Browning",
                        href="index_split_029.html",
                        children=[
                            CookbookTocEntry(
                                label="WHOLE ROASTED CELERIAC THREE WAYS",
                                href="index_split_031.html",
                                children=[],
                            ),
                        ],
                    ),
                ],
            )
        ]

        mapping = build_chapter_map_from_toc_entries(
            entries,
            spine_paths=[
                "index_split_008.html",
                "index_split_015.html",
                "index_split_017.html",
                "index_split_029.html",
                "index_split_031.html",
            ],
            recipe_paths={
                "index_split_015.html",
                "index_split_017.html",
                "index_split_031.html",
            },
        )

        self.assertEqual(
            mapping,
            {
                "index_split_015.html": "Charring",
                "index_split_017.html": "Charring",
                "index_split_031.html": "Browning",
            },
        )

    def test_chapter_label_uses_anchor_path_without_fragment(self) -> None:
        recipe = RecipeRecord.model_validate(
            {
                "id": "recipe-2",
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
                    "chapter_title": "Recipes",
                    "anchor": "xhtml/06_Vegetables.xhtml#page_20",
                    "excerpt": "Roast Tomatoes with whipped feta",
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

        self.assertEqual(
            chapter_label_for_recipe(recipe, {"xhtml/06_Vegetables.xhtml": "Vegetables"}),
            "Vegetables",
        )

    def test_epub_fallback_does_not_use_excerpt_prefix_as_chapter(self) -> None:
        recipe = RecipeRecord.model_validate(
            {
                "id": "recipe-1",
                "cookbook_id": "book-1",
                "cookbook_title": "Book",
                "title": "Chicken Soup",
                "ingredients": [],
                "ingredient_names": [],
                "method_steps": [],
                "images": [],
                "source": {
                    "object_key": "ebooks/book.epub",
                    "format": "epub",
                    "chapter_title": "Chicken Soup",
                    "anchor": "Text/recipe.xhtml#1",
                    "excerpt": "For the topping Chicken Soup with herbs",
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

        self.assertEqual(chapter_label_for_recipe(recipe, {}), "Recipes")

    def test_flatten_cookbook_toc_labels_preserves_nested_order(self) -> None:
        entries = [
            CookbookTocEntry(
                label="Contents",
                href="contents.xhtml",
                children=[
                    CookbookTocEntry(label="Vegetables", href="veg.xhtml"),
                    CookbookTocEntry(label="Fish", href="fish.xhtml"),
                ],
            )
        ]

        self.assertEqual(
            flatten_cookbook_toc_labels(entries),
            ["Contents", "Vegetables", "Fish"],
        )

    def test_order_recipe_sections_uses_source_table_of_contents(self) -> None:
        recipe_sections = [
            {"title": "Fish", "recipes": [object()]},
            {"title": "Vegetables", "recipes": [object(), object()]},
            {"title": "Desserts", "recipes": [object()]},
        ]
        source_table_of_contents = [
            CookbookTocEntry(
                label="Contents",
                href="contents.xhtml",
                children=[
                    CookbookTocEntry(label="Vegetables", href="veg.xhtml"),
                    CookbookTocEntry(label="Fish", href="fish.xhtml"),
                ],
            )
        ]

        ordered = order_recipe_sections(recipe_sections, source_table_of_contents)

        self.assertEqual(
            [section["title"] for section in ordered],
            ["Vegetables", "Fish", "Desserts"],
        )

    def test_build_recipe_sections_from_source_toc_prefers_group_headings(self) -> None:
        toc = [
            CookbookTocEntry(
                label="PROCESS",
                href="index_split_008.html",
                children=[
                    CookbookTocEntry(
                        label="Charring",
                        href="index_split_015.html",
                        children=[
                            CookbookTocEntry(label="Recipe One", href="index_split_015.html", children=[]),
                            CookbookTocEntry(label="Recipe Two", href="index_split_017.html", children=[]),
                        ],
                    ),
                    CookbookTocEntry(
                        label="Browning",
                        href="index_split_029.html",
                        children=[
                            CookbookTocEntry(label="Recipe Three", href="index_split_029.html", children=[]),
                            CookbookTocEntry(label="Recipe Four", href="index_split_031.html", children=[]),
                        ],
                    ),
                ],
            )
        ]

        def recipe(recipe_id: str, title: str, anchor: str) -> RecipeRecord:
            return RecipeRecord.model_validate(
                {
                    "id": recipe_id,
                    "cookbook_id": "book-1",
                    "cookbook_title": "Book",
                    "title": title,
                    "ingredients": [],
                    "ingredient_names": [],
                    "method_steps": [],
                    "images": [],
                    "source": {
                        "object_key": "ebooks/book.epub",
                        "format": "epub",
                        "chapter_title": title,
                        "anchor": anchor,
                        "excerpt": title,
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

        sections = build_recipe_sections_from_source_toc(
            [
                recipe("r1", "Recipe One", "index_split_015.html#1"),
                recipe("r2", "Recipe Two", "index_split_017.html#1"),
                recipe("r3", "Recipe Three", "index_split_029.html#1"),
                recipe("r4", "Recipe Four", "index_split_031.html#1"),
            ],
            toc,
        )

        self.assertEqual(
            [(section["title"], len(section["recipes"])) for section in sections],
            [("Charring", 2), ("Browning", 2)],
        )


if __name__ == "__main__":
    unittest.main()
