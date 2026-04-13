import unittest
from pathlib import Path
import tempfile

from bs4 import BeautifulSoup
from ebooklib import ITEM_IMAGE, epub

from app.extractor import CandidateSection, OpenAIRecipeExtractor


class FakeDocumentItem:
    def __init__(self, file_name: str) -> None:
        self.file_name = file_name

    def get_name(self) -> str:
        return self.file_name


class FakeImageItem:
    def __init__(self, file_name: str, *, content: bytes = b"fake", media_type: str = "image/jpeg") -> None:
        self.file_name = file_name
        self.media_type = media_type
        self._content = content

    def get_name(self) -> str:
        return self.file_name

    def get_type(self) -> int:
        return ITEM_IMAGE

    def get_content(self) -> bytes:
        return self._content


class FakeBook:
    def get_item_with_href(self, href: str):  # pragma: no cover - compatibility fallback
        return None


class ExtractorProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = object.__new__(OpenAIRecipeExtractor)

    def test_cooks_britain_profile_splits_multiple_recipes(self) -> None:
        soup = BeautifulSoup(
            """
            <html><body>
              <p class="C287">Roast tomatoes with whipped feta and flatbreads</p>
              <p class="C290">Intro text.</p>
              <p class="C297">Serves 4</p>
              <p class="C300">350g flour</p>
              <ol class="C342"><li class="C306">Bake it.</li></ol>
              <p class="C287">Asparagus, poached eggs and hollandaise</p>
              <p class="C290">More intro.</p>
              <p class="C297">Serves 2</p>
              <p class="C300">4 eggs</p>
              <ol class="C342"><li class="C306">Poach them.</li></ol>
            </body></html>
            """,
            "html.parser",
        )

        profile = self.extractor._match_epub_recipe_paragraph_profile(soup)
        self.assertIsNotNone(profile)
        sections = self.extractor._extract_epub_recipe_paragraph_sections(
            book=None,
            href_to_item={},
            document_item=type("Doc", (), {"get_name": lambda self: "chapter.xhtml", "file_name": "chapter.xhtml"})(),
            soup=soup,
            profile=profile,
        )

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].chapter_title, "Roast tomatoes with whipped feta and flatbreads")
        self.assertIn("350g flour", sections[0].text)
        self.assertEqual(sections[1].chapter_title, "Asparagus, poached eggs and hollandaise")

    def test_road_to_mexico_profile_uses_recipe_title_anchor(self) -> None:
        soup = BeautifulSoup(
            """
            <html><body>
              <p class="recipe-head-translation">HUEVOS RANCHEROS</p>
              <p class="recipe-title" id="ch01rec1">RANCH-STYLE EGGS</p>
              <p class="recipe-intro">Intro.</p>
              <p class="serves1">SERVES FOUR</p>
              <p class="ingredients">4 eggs</p>
              <p class="method1">Cook.</p>
              <p class="recipe-head-translation">CHILAQUILES</p>
              <p class="recipe-title" id="ch01rec2">FRIED TORTILLA CHIPS</p>
              <p class="recipe-intro">Intro.</p>
              <p class="serves">SERVES FOUR</p>
              <p class="ingredients">12 tortillas</p>
              <p class="method">Serve.</p>
            </body></html>
            """,
            "html.parser",
        )

        profile = self.extractor._match_epub_recipe_paragraph_profile(soup)
        sections = self.extractor._extract_epub_recipe_paragraph_sections(
            book=None,
            href_to_item={},
            document_item=type("Doc", (), {"get_name": lambda self: "chapter_001.xhtml", "file_name": "chapter_001.xhtml"})(),
            soup=soup,
            profile=profile,
        )

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].anchor, "chapter_001.xhtml#ch01rec1")
        self.assertTrue(sections[0].text.startswith("HUEVOS RANCHEROS"))
        self.assertEqual(sections[1].anchor, "chapter_001.xhtml#ch01rec2")

    def test_road_to_mexico_profile_supports_recipe_title1_nodes(self) -> None:
        soup = BeautifulSoup(
            """
            <html><body>
              <p class="recipe-title1" id="ch07rec4">SORBETS</p>
              <p class="recipe-intro">Intro.</p>
              <p class="method-head">MANGO SORBET</p>
              <p class="serves1">SERVES FOUR</p>
              <p class="ingredients">200g caster sugar</p>
              <p class="method1">Freeze.</p>
              <p class="recipe-title1" id="ch07rec5">RICK'S MARGARITA</p>
              <p class="recipe-intro">Intro.</p>
              <p class="serves">SERVES ONE</p>
              <p class="ingredients">45ml tequila</p>
              <p class="method1">Shake.</p>
            </body></html>
            """,
            "html.parser",
        )

        profile = self.extractor._match_epub_recipe_paragraph_profile(soup)
        self.assertIsNotNone(profile)
        sections = self.extractor._extract_epub_recipe_paragraph_sections(
            book=None,
            href_to_item={},
            document_item=type("Doc", (), {"get_name": lambda self: "chapter_007.xhtml", "file_name": "chapter_007.xhtml"})(),
            soup=soup,
            profile=profile,
        )

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].chapter_title, "SORBETS")
        self.assertEqual(sections[0].anchor, "chapter_007.xhtml#ch07rec4")
        self.assertIn("MANGO SORBET", sections[0].text)
        self.assertEqual(sections[1].chapter_title, "RICK'S MARGARITA")
        self.assertEqual(sections[1].anchor, "chapter_007.xhtml#ch07rec5")

    def test_ottolenghi_profile_captures_intro_serves_and_supplemental_sections(self) -> None:
        soup = BeautifulSoup(
            """
            <html><body>
              <p class="recipe-title" id="ch01_sec1">Peaches and speck with orange blossom</p>
              <p class="serves">Serves 4-6</p>
              <p class="intro">First intro paragraph.</p>
              <p class="intro">Second intro paragraph.</p>
              <p class="ingredients">5 ripe peaches</p>
              <p class="ingredients">1 tbsp olive oil</p>
              <p class="serves-subhead">Dressing</p>
              <p class="ingredients">3 tbsp orange blossom water</p>
              <p class="ingredients">1 tbsp balsamic vinegar</p>
              <p class="method">Cut the peaches.</p>
              <p class="method">Whisk the dressing.</p>
              <p class="recipe-title_new" id="ch01_sec2">Figs with young pecorino and honey</p>
              <p class="serves">Serves 4</p>
              <p class="intro">Recipe intro.</p>
              <p class="ingredients">4 figs</p>
              <p class="method">Serve.</p>
            </body></html>
            """,
            "html.parser",
        )

        profile = self.extractor._match_epub_recipe_paragraph_profile(soup)
        self.assertIsNotNone(profile)
        sections = self.extractor._extract_epub_recipe_paragraph_sections(
            book=None,
            href_to_item={},
            document_item=type("Doc", (), {"get_name": lambda self: "chapter_001.xhtml", "file_name": "chapter_001.xhtml"})(),
            soup=soup,
            profile=profile,
        )

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].anchor, "chapter_001.xhtml#ch01_sec1")
        self.assertEqual(sections[0].metadata["serves"], "4-6")
        self.assertEqual(
            sections[0].metadata["intro"],
            "First intro paragraph.\n\nSecond intro paragraph.",
        )
        self.assertEqual(
            sections[0].metadata["supplemental_sections"],
            [{"heading": "Dressing", "lines": ["3 tbsp orange blossom water", "1 tbsp balsamic vinegar"]}],
        )
        self.assertIn("Whisk the dressing.", sections[0].text)

    def test_ottolenghi_comfort_profile_captures_intro_serves_and_subrecipe_sections(self) -> None:
        soup = BeautifulSoup(
            """
            <html><body>
              <h1 class="ct">Eggs, crêpes, pancakes</h1>
              <img src="../images/chapter.jpg" />
              <h2 class="rt">Dutch baby with oven-roasted tomatoes</h2>
              <img src="../images/recipe.jpg" />
              <p class="ry">Serves 4</p>
              <p class="rhn">If a pancake and a crêpe had a love child, it would be a Dutch baby!</p>
              <p class="rilf">1 ⅓ cups/160g all-purpose flour</p>
              <p class="ril">3 tbsp finely grated parmesan</p>
              <h3 class="rilh">Oven-roasted tomatoes</h3>
              <p class="ril">14 oz/380g cherry tomatoes</p>
              <p class="ril">6 thyme sprigs</p>
              <p class="rpf">Preheat the oven to 425°F.</p>
              <p class="rp">Combine all the ingredients for the oven-roasted tomatoes.</p>
              <h2 class="rt">Egg and watercress</h2>
              <p class="ry">Serves 4</p>
              <p class="rhn">This is her tribute to the classic.</p>
              <p class="rilf">8 eggs, at room temperature</p>
              <p class="rp">Boil the eggs.</p>
            </body></html>
            """,
            "html.parser",
        )

        profile = self.extractor._match_epub_recipe_paragraph_profile(soup)
        self.assertIsNotNone(profile)
        href_to_item = {
            "images/chapter.jpg": FakeImageItem("images/chapter.jpg"),
            "images/recipe.jpg": FakeImageItem("images/recipe.jpg"),
        }
        sections = self.extractor._extract_epub_recipe_paragraph_sections(
            book=FakeBook(),
            href_to_item=href_to_item,
            document_item=FakeDocumentItem("xhtml/c01.xhtml"),
            soup=soup,
            profile=profile,
        )

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].chapter_title, "Dutch baby with oven-roasted tomatoes")
        self.assertEqual(sections[0].metadata["serves"], "4")
        self.assertEqual(
            sections[0].metadata["intro"],
            "If a pancake and a crêpe had a love child, it would be a Dutch baby!",
        )
        self.assertEqual(
            sections[0].metadata["supplemental_sections"],
            [{"heading": "Oven-roasted tomatoes", "lines": ["14 oz/380g cherry tomatoes", "6 thyme sprigs"]}],
        )
        self.assertIn("1 ⅓ cups/160g all-purpose flour", sections[0].ingredient_lines)
        self.assertIn("Preheat the oven to 425°F.", sections[0].method_lines)
        self.assertEqual([image.filename for image in sections[0].images], ["recipe.jpg"])
        self.assertEqual(sections[1].chapter_title, "Egg and watercress")

    def test_ottolenghi_simple_profile_ignores_leading_section_image_and_keeps_recipe_image_current(self) -> None:
        soup = BeautifulSoup(
            """
            <html><body>
              <img src="../images/pg_3.jpg" />
              <h2 class="rec_list">Recipe List</h2>
              <li class="li_normal">Braised eggs with leek and za’atar</li>
              <h2 class="rec_head">Braised eggs with leek and za’atar</h2>
              <img class="inline_image_2" src="../images/s.jpg" />
              <p class="mc_intro">This is a quick breakfast.</p>
              <p class="ingredient_list">30g unsalted butter</p>
              <p class="ingredient_list">2 tbsp olive oil</p>
              <p class="method">1. Cook the leeks.</p>
              <img src="../images/pg_4.jpg" />
              <h2 class="rec_head">Harissa and Manchego omelettes</h2>
              <p class="mc_intro1">This one is great for brunch.</p>
              <p class="ingredient_list">12 large eggs</p>
              <p class="ingredient_list">100ml whole milk</p>
              <p class="method">1. Preheat the grill.</p>
            </body></html>
            """,
            "html.parser",
        )

        profile = self.extractor._match_epub_recipe_paragraph_profile(soup)
        self.assertIsNotNone(profile)
        href_to_item = {
            "images/pg_3.jpg": FakeImageItem("images/pg_3.jpg"),
            "images/pg_4.jpg": FakeImageItem("images/pg_4.jpg"),
            "images/s.jpg": FakeImageItem("images/s.jpg"),
        }
        sections = self.extractor._extract_epub_recipe_paragraph_sections(
            book=FakeBook(),
            href_to_item=href_to_item,
            document_item=FakeDocumentItem("xhtml/chapter001.xhtml"),
            soup=soup,
            profile=profile,
        )

        self.assertEqual(len(sections), 2)
        self.assertEqual(
            [image.filename for image in sections[0].images],
            ["pg_4.jpg"],
        )
        self.assertEqual(sections[1].images, [])
        self.assertEqual(sections[0].metadata["intro"], "This is a quick breakfast.")
        self.assertEqual(sections[0].ingredient_lines[:2], ["30g unsalted butter", "2 tbsp olive oil"])
        self.assertEqual(sections[1].metadata["intro"], "This one is great for brunch.")

    def test_ottolenghi_test_kitchen_profile_captures_timing_and_keeps_page_images_with_current_recipe(self) -> None:
        soup = BeautifulSoup(
            """
            <html><body>
              <h2 class="rec_head">Cheesy curried butter beans on toast with pickled onion</h2>
              <p class="rec_intro">Beans on toast, all grown up.</p>
              <p class="prep_time">Prep time: 10 minutes</p>
              <p class="prep_time1">Cook time: 15 minutes</p>
              <p class="serves">Serves 4</p>
              <li class="ingred">2½ tbsp olive oil</li>
              <li class="ingred">25g fresh ginger</li>
              <p class="method">1. Preheat the oven.</p>
              <p class="method">2. Make the pickled onion.</p>
              <img src="../images/pg019.jpg" />
              <h2 class="rec_headbk">Feta dumplings with fresh chilli sauce</h2>
              <p class="rec_intro">These dumplings are all at once comforting.</p>
              <p class="prep_time">Prep time: 15 minutes</p>
              <p class="prep_time1">Chilling time: 1 hour</p>
              <li class="ingred">250g Greek feta</li>
              <li class="ingred">200g whole-milk ricotta</li>
              <p class="method">1. Put the feta into a food processor.</p>
            </body></html>
            """,
            "html.parser",
        )

        profile = self.extractor._match_epub_recipe_paragraph_profile(soup)
        self.assertIsNotNone(profile)
        href_to_item = {
            "images/pg019.jpg": FakeImageItem("images/pg019.jpg"),
        }
        sections = self.extractor._extract_epub_recipe_paragraph_sections(
            book=FakeBook(),
            href_to_item=href_to_item,
            document_item=FakeDocumentItem("xhtml/chapter001_03.xhtml"),
            soup=soup,
            profile=profile,
        )

        self.assertEqual(len(sections), 2)
        self.assertEqual(
            sections[0].chapter_title,
            "Cheesy curried butter beans on toast with pickled onion",
        )
        self.assertEqual(sections[0].metadata["intro"], "Beans on toast, all grown up.")
        self.assertEqual(sections[0].metadata["prep_time"], "10 minutes")
        self.assertEqual(sections[0].metadata["cook_time"], "15 minutes")
        self.assertEqual(sections[0].metadata["serves"], "4")
        self.assertEqual(sections[0].ingredient_lines[:2], ["2½ tbsp olive oil", "25g fresh ginger"])
        self.assertEqual(sections[0].method_lines[:2], ["1. Preheat the oven.", "2. Make the pickled onion."])
        self.assertEqual([image.filename for image in sections[0].images], ["pg019.jpg"])
        self.assertEqual(sections[1].images, [])
        self.assertEqual(sections[1].metadata["chilling_time"], "1 hour")

    def test_recipe_candidate_rejects_non_recipe_frontmatter(self) -> None:
        section = CandidateSection(
            source_format="epub",
            section_key="text00008.html#1",
            chapter_title="FOREWORD",
            anchor="text00008.html#1",
            text="FOREWORD\nThis book has ingredients in spirit but no actual recipe.",
            excerpt="FOREWORD This book has ingredients in spirit but no actual recipe.",
        )

        self.assertFalse(self.extractor._is_recipe_candidate(section))

    def test_recipe_candidate_rejects_ottolenghi_frontmatter_by_path(self) -> None:
        section = CandidateSection(
            source_format="epub",
            section_key="pages/ottolenghi.xhtml#3",
            chapter_title="The space",
            anchor="pages/ottolenghi.xhtml#3",
            text="The space\nThis page describes the restaurant and dining room.",
            excerpt="The space This page describes the restaurant and dining room.",
        )

        self.assertFalse(self.extractor._is_recipe_candidate(section))

    def test_recipe_candidate_rejects_untitled_multi_recipe_backmatter(self) -> None:
        section = CandidateSection(
            source_format="epub",
            section_key="xhtml/bm01.xhtml#1",
            chapter_title=None,
            anchor="xhtml/bm01.xhtml#1",
            text=(
                "STAPLES\n"
                "STEWED SALSA VERDE\n"
                "SERVES FOUR\n"
                "380g tinned tomatillos\n"
                "1 green chilli\n"
                "Simmer until softened.\n"
                "ROASTED TOMATILLO SALSA\n"
                "SERVES FOUR TO SIX\n"
                "10 tomatillos\n"
                "2 cloves garlic\n"
                "Grill until charred.\n"
                "PINK PICKLED ONIONS\n"
                "MAKES 1 X 500ML JAR\n"
                "3 red onions\n"
                "Pack into a jar."
            ),
            excerpt="STAPLES STEWED SALSA VERDE SERVES FOUR 380g tinned tomatillos",
        )

        self.assertFalse(self.extractor._is_recipe_candidate(section))

    def test_recipe_candidate_accepts_compact_quantity_units(self) -> None:
        section = CandidateSection(
            source_format="epub",
            section_key="chapter.xhtml#1",
            chapter_title="Eggnog Cream",
            anchor="chapter.xhtml#1",
            text=(
                "Eggnog Cream\n"
                "350ml double cream\n"
                "125ml advocaat liqueur\n"
                "Put the cream into a bowl and whisk."
            ),
            excerpt="Eggnog Cream 350ml double cream 125ml advocaat liqueur",
        )

        self.assertTrue(self.extractor._is_recipe_candidate(section))

    def test_parse_recipe_payload_strips_wrapper_text(self) -> None:
        payload = self.extractor._parse_recipe_payload(
            'Result: {"is_recipe": true, "title": "Soup", "confidence": 0.9, "ingredients": [], "method_steps": [], "notes": []}'
        )
        self.assertEqual(payload.title, "Soup")

    def test_extract_epub_sections_attaches_following_caption_image_page(self) -> None:
        book = epub.EpubBook()
        book.set_identifier("test-book")
        book.set_title("Test Book")
        book.set_language("en")

        recipe_doc = epub.EpubHtml(
            title="Recipe",
            file_name="html/recipe.xhtml",
            content=(
                "<html><body>"
                "<h4>PROPER BAKED BEANS ON SODA BREAD TOAST</h4>"
                "<p>This is the intro.</p>"
                "<p>Serves 2</p>"
                "<p>200g beans</p>"
                "<p>Cook it.</p>"
                "</body></html>"
            ),
        )
        image_doc = epub.EpubHtml(
            title="Image",
            file_name="html/recipe-image.xhtml",
            content=(
                "<html><body>"
                '<img src="docimages/1.jpg" alt="" />'
                '<p class="caption">Proper baked beans on soda bread toast</p>'
                "</body></html>"
            ),
        )
        image_item = epub.EpubItem(
            uid="img1",
            file_name="html/docimages/1.jpg",
            media_type="image/jpeg",
            content=b"fake-jpeg-bytes",
        )

        book.add_item(recipe_doc)
        book.add_item(image_doc)
        book.add_item(image_item)
        book.spine = ["nav", recipe_doc, image_doc]
        book.add_item(epub.EpubNav())
        book.add_item(epub.EpubNcx())

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.epub"
            epub.write_epub(str(path), book)
            sections = self.extractor._extract_epub_sections(path.read_bytes())

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].chapter_title, "PROPER BAKED BEANS ON SODA BREAD TOAST")
        self.assertEqual(len(sections[0].images), 1)
        self.assertEqual(sections[0].images[0].source_ref, "html/docimages/1.jpg")

    def test_generic_epub_section_infers_recipe_structure_without_profile(self) -> None:
        book = epub.EpubBook()
        book.set_identifier("test-book")
        book.set_title("Test Book")
        book.set_language("en")

        recipe_doc = epub.EpubHtml(
            title="Recipe",
            file_name="html/recipe.xhtml",
            content=(
                "<html><body>"
                "<h2>Beans on Toast</h2>"
                "<p>A low-effort dinner for late evenings.</p>"
                "<p>Serves 2</p>"
                "<p>Prep time: 5 minutes</p>"
                "<p>Ingredients</p>"
                "<p>2 slices sourdough</p>"
                "<p>400g baked beans</p>"
                "<p>Method</p>"
                "<p>Step 1</p>"
                "<p>Toast the bread.</p>"
                "<p>Step 2</p>"
                "<p>Warm the beans and spoon them over.</p>"
                '<img src="images/beans.jpg" alt="" />'
                "</body></html>"
            ),
        )
        image_item = epub.EpubItem(
            uid="img1",
            file_name="html/images/beans.jpg",
            media_type="image/jpeg",
            content=b"fake-jpeg-bytes",
        )

        book.add_item(recipe_doc)
        book.add_item(image_item)
        book.spine = ["nav", recipe_doc]
        book.add_item(epub.EpubNav())
        book.add_item(epub.EpubNcx())

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.epub"
            epub.write_epub(str(path), book)
            sections = self.extractor._extract_epub_sections(path.read_bytes())

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].chapter_title, "Beans on Toast")
        self.assertEqual(sections[0].metadata["intro"], "A low-effort dinner for late evenings.")
        self.assertEqual(sections[0].metadata["serves"], "2")
        self.assertEqual(sections[0].metadata["prep_time"], "5 minutes")
        self.assertEqual(sections[0].ingredient_lines, ["2 slices sourdough", "400g baked beans"])
        self.assertEqual(
            sections[0].method_lines,
            ["Toast the bread.", "Warm the beans and spoon them over."],
        )
        self.assertEqual(len(sections[0].images), 1)
        self.assertEqual(sections[0].images[0].source_ref, "html/images/beans.jpg")

        payload = self.extractor._build_deterministic_recipe_payload(sections[0])

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.title, "Beans on Toast")
        self.assertEqual(payload.serves, "2")
        self.assertEqual(payload.prep_time, "5 minutes")
        self.assertEqual(payload.method_steps, ["Toast the bread.", "Warm the beans and spoon them over."])

    def test_generic_epub_section_fields_dedupe_consecutive_duplicates(self) -> None:
        section = CandidateSection(
            source_format="epub",
            section_key="chapter.xhtml#1",
            chapter_title="Green Bagna Cauda",
            anchor="chapter.xhtml#1",
            text="\n".join(
                [
                    "Green Bagna Cauda",
                    "Intro text.",
                    "Intro text.",
                    "Serves 4",
                    "1 cup olive oil",
                    "1 cup olive oil",
                    "Kosher salt",
                    "Kosher salt",
                    "Method",
                    "Stir everything together.",
                    "Stir everything together.",
                ]
            ),
            excerpt="Green Bagna Cauda",
        )

        self.extractor._populate_generic_epub_section_fields(section)

        self.assertEqual(section.metadata["intro"], "Intro text.")
        self.assertEqual(section.metadata["serves"], "4")
        self.assertEqual(section.ingredient_lines, ["1 cup olive oil", "Kosher salt"])
        self.assertEqual(section.method_lines, ["Stir everything together."])

    def test_profile_sections_dedupe_consecutive_duplicates(self) -> None:
        soup = BeautifulSoup(
            """
            <html><body>
              <p class="rec_head" id="r1">Tomato Salad</p>
              <p class="rec_intro">Bright and punchy.</p>
              <p class="rec_intro">Bright and punchy.</p>
              <p class="ingred">2 tomatoes</p>
              <p class="ingred">2 tomatoes</p>
              <p class="method">Slice and serve.</p>
              <p class="method">Slice and serve.</p>
            </body></html>
            """,
            "html.parser",
        )

        profile = self.extractor._match_epub_recipe_paragraph_profile(soup)
        self.assertIsNotNone(profile)
        sections = self.extractor._extract_epub_recipe_paragraph_sections(
            book=None,
            href_to_item={},
            document_item=FakeDocumentItem("chapter.xhtml"),
            soup=soup,
            profile=profile,
        )

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].metadata["intro"], "Bright and punchy.")
        self.assertEqual(sections[0].ingredient_lines, ["2 tomatoes"])
        self.assertEqual(sections[0].method_lines, ["Slice and serve."])
        self.assertEqual(
            sections[0].text.splitlines(),
            ["Tomato Salad", "Bright and punchy.", "2 tomatoes", "Slice and serve."],
        )


if __name__ == "__main__":
    unittest.main()
