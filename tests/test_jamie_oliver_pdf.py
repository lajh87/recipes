from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fitz
from fastapi import UploadFile

from app.jamie_oliver_pdf import extract_jamie_oliver_pdf
from app.main import upload_cookbooks_form
from app.models import RecipeCollectionItem


def build_jamie_template_pdf(*, include_method: bool = True) -> bytes:
    document = fitz.open()
    document.new_page(width=595, height=842)
    document.new_page(width=595, height=842)
    page_one = document[0]
    page_two = document[1]

    page_one.insert_text((60, 40), "HOMEMADE BAGELS")
    page_one.insert_text((140, 135), "50 MINS PLUS PROVING TIME")
    page_one.insert_text((305, 135), "NOT TOO TRICKY")
    page_one.insert_text((408, 135), "MAKES 12")
    page_one.insert_text((17, 190), "INGREDIENTS")
    page_one.insert_text((17, 240), "750g strong white bread flour")
    page_one.insert_text((17, 280), "1 x 7g sachet of instant yeast")
    page_one.insert_text((17, 320), "3 teaspoons soft brown sugar")
    page_one.insert_text((17, 360), "3 teaspoons fine sea salt")
    page_one.insert_text((17, 400), "1 teaspoon bicarbonate of soda")
    page_one.insert_text((17, 440), "1 large free-range egg")
    page_one.insert_text((17, 480), "optional: 2 tablespoons")
    page_one.insert_text((17, 500), "sesame, poppy, pumpkin or")
    page_one.insert_text((17, 520), "sunflower seeds")
    page_one.insert_text((17, 560), "TOP TIP Bagels are best eaten within a")
    page_one.insert_text((17, 580), "few hours, but you can easily")
    page_one.insert_text((17, 600), "freeze them as soon as they are cool.")

    if include_method:
        page_one.insert_text((237, 190), "METHOD")
        page_one.insert_text((237, 240), "Place the flour, yeast, sugar and salt into the bowl.")
        page_one.insert_text((237, 260), "Then follow the recipe from step 2, below.")
        page_one.insert_text((237, 300), "HAND MIX METHOD:")
        page_one.insert_text((237, 320), "Place the flour and sugar in a large bowl.")
        page_one.insert_text((237, 340), "Knead until you have a silky dough.")
        page_one.insert_text((237, 380), "FREE-STANDING MIXER METHOD: 1")
        page_one.insert_text((255, 400), "Place the dough in a lightly oiled bowl.")
        page_one.insert_text((255, 420), "Leave to prove until doubled in size.")
        page_one.insert_text((237, 460), "2")
        page_one.insert_text((255, 480), "Preheat the oven to 200C and line 2 trays.")
        page_one.insert_text((237, 520), "3")
        page_one.insert_text((255, 540), "Shape the dough into 12 equal bagel rings.")

        page_two.insert_text((255, 40), "Boil briefly on each side.")
        page_two.insert_text((237, 80), "4")
        page_two.insert_text((255, 100), "Lift out with a slotted spoon and drain well.")
        page_two.insert_text((237, 140), "5")
        page_two.insert_text((255, 160), "Egg-wash the bagels and sprinkle with seeds.")
        page_two.insert_text((237, 200), "6")
        page_two.insert_text((255, 220), "Bake for 20 to 25 minutes until golden brown.")
        page_two.insert_text((237, 260), "7")
        page_two.insert_text((255, 280), "Cool completely before serving.")

    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


def build_jamie_misaligned_method_pdf() -> bytes:
    document = fitz.open()
    document.new_page(width=595, height=842)
    page_one = document[0]

    page_one.insert_text((60, 40), "SPRING MINESTRONE")
    page_one.insert_text((140, 135), "1 HOUR")
    page_one.insert_text((305, 135), "NOT TOO TRICKY")
    page_one.insert_text((408, 135), "SERVES 6")
    page_one.insert_text((17, 190), "INGREDIENTS")
    page_one.insert_text((17, 240), "2 carrots")
    page_one.insert_text((17, 280), "2 celery sticks")
    page_one.insert_text((17, 320), "1 litre vegetable stock")
    page_one.insert_text((237, 190), "METHOD")
    page_one.insert_text((237, 220), "Hand Mix Method:")
    page_one.insert_text((255, 240), "Bring a pot of stock to the boil.")
    page_one.insert_text((237, 243), "1")
    page_one.insert_text((255, 280), "Add the carrots and celery, then simmer gently.")
    page_one.insert_text((237, 283), "2")
    page_one.insert_text((237, 320), "3")
    page_one.insert_text((255, 317), "Season to taste and serve.")

    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


class JamieOliverPdfTests(unittest.TestCase):
    def test_extracts_jamie_oliver_template_pdf(self) -> None:
        result = extract_jamie_oliver_pdf(
            cookbook_title="Homemade bagels | Jamie Oliver recipes",
            filename="Homemade bagels | Jamie Oliver recipes.pdf",
            object_key="ebooks/jamie.pdf",
            content_type="application/pdf",
            file_bytes=build_jamie_template_pdf(),
        )

        self.assertEqual(result.title, "Homemade Bagels")
        self.assertEqual(result.author, "Jamie Oliver")
        self.assertEqual(result.total_time, "50 Mins Plus Proving Time")
        self.assertEqual(result.difficulty, "Not Too Tricky")
        self.assertEqual(result.makes, "12")
        self.assertEqual(len(result.drafts), 1)
        self.assertEqual(
            result.drafts[0].ingredients[-1]["raw"],
            "optional: 2 tablespoons sesame, poppy, pumpkin or sunflower seeds",
        )
        self.assertEqual(len(result.drafts[0].method_steps), 7)
        self.assertIn("Top Tip", result.drafts[0].source["metadata"]["supplemental_sections"][0]["heading"])
        self.assertEqual(
            result.drafts[0].source["metadata"]["preparation_notes"],
            [
                "Place the flour, yeast, sugar and salt into the bowl. Then follow the recipe from step 2, below.",
                "Hand Mix Method Place the flour and sugar in a large bowl. Knead until you have a silky dough.",
                "Free-Standing Mixer Method",
            ],
        )

    def test_extracts_misaligned_jamie_oliver_method_rows(self) -> None:
        result = extract_jamie_oliver_pdf(
            cookbook_title="Spring minestrone | Jamie Oliver recipes",
            filename="Spring minestrone | Jamie Oliver recipes.pdf",
            object_key="ebooks/spring-minestrone.pdf",
            content_type="application/pdf",
            file_bytes=build_jamie_misaligned_method_pdf(),
        )

        self.assertEqual(result.title, "Spring Minestrone")
        self.assertEqual(
            result.drafts[0].method_steps,
            [
                "Bring a pot of stock to the boil.",
                "Add the carrots and celery, then simmer gently.",
                "Season to taste and serve.",
            ],
        )
        self.assertEqual(
            result.drafts[0].source["metadata"]["preparation_notes"],
            ["Hand Mix Method"],
        )

    def test_rejects_pdf_without_method_template_section(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected a METHOD section with numbered steps"):
            extract_jamie_oliver_pdf(
                cookbook_title="Homemade bagels | Jamie Oliver recipes",
                filename="Homemade bagels | Jamie Oliver recipes.pdf",
                object_key="ebooks/jamie.pdf",
                content_type="application/pdf",
                file_bytes=build_jamie_template_pdf(include_method=False),
            )


class StubRepository:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.stored = False

    def get_recipe_collection(self, slug: str) -> RecipeCollectionItem | None:
        if slug != "jamie-oliver":
            return None
        return RecipeCollectionItem(
            slug="jamie-oliver",
            title="Jamie Oliver",
            description="Jamie Oliver recipes",
            recipe_count=0,
            allow_upload=True,
        )

    def upload_cookbook(self, upload: UploadFile, *, collection_slug: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            id="book-1",
            title="Bad Upload",
            filename=upload.filename,
            object_key="ebooks/bad-upload.pdf",
            content_type="application/pdf",
        )

    def download_cookbook(self, cookbook_id: str) -> bytes:
        self.last_download = cookbook_id
        return b"%PDF-1.4"

    def delete_cookbook(self, cookbook_id: str) -> bool:
        self.deleted.append(cookbook_id)
        return True

    def update_cookbook_metadata(self, cookbook_id: str, **kwargs) -> None:
        self.last_metadata = (cookbook_id, kwargs)

    def store_extracted_recipes(self, cookbook_id: str, drafts, embeddings) -> None:
        self.stored = True

    def mark_cookbook_failed(self, cookbook_id: str, reason: str) -> None:
        self.failed = (cookbook_id, reason)


class JamieOliverUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_jamie_upload_is_deleted_and_not_processed(self) -> None:
        repository = StubRepository()
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    repository=repository,
                    settings=SimpleNamespace(openai_api_key=""),
                )
            )
        )
        upload = UploadFile(filename="bad.pdf", file=BytesIO(b"bad pdf"))

        with patch(
            "app.main.extract_jamie_oliver_pdf",
            side_effect=ValueError(
                "upload does not match the Jamie Oliver PDF template: expected an INGREDIENTS section."
            ),
        ):
            response = await upload_cookbooks_form(
                request=request,
                files=[upload],
                collection_slug="jamie-oliver",
            )

        self.assertEqual(repository.deleted, ["book-1"])
        self.assertFalse(repository.stored)
        self.assertEqual(response.status_code, 303)
        self.assertIn("/collections/jamie-oliver?notice=", response.headers["location"])
        self.assertIn("Deleted", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
