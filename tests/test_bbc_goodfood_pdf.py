from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fitz
from fastapi import UploadFile

from app.bbc_goodfood_pdf import extract_bbc_goodfood_pdf
from app.main import upload_cookbooks_form
from app.models import RecipeCollectionItem


def build_bbc_goodfood_template_pdf(*, include_method: bool = True) -> bytes:
    document = fitz.open()
    document.new_page(width=595, height=842)
    page = document[0]

    page.insert_text((146, 115), "Tortillas")
    page.insert_text((144, 138), "Good Food team")
    page.insert_text((110, 167), "Makes 6 Easy")
    page.insert_text((101, 196), "Prep: 30 mins Cook: 25 mins")
    page.insert_text((159, 218), "plus resting")
    page.insert_text((69, 232), "Forget shop-bought and try homemade tortillas. They'll store")
    page.insert_text((65, 245), "for two days or you can freeze them to use later. They're perfect")
    page.insert_text((159, 258), "for wraps")
    page.insert_text((77, 279), "Freezable Dairy-free Egg-free Nut-free Vegan")
    page.insert_text((159, 292), "Vegetarian")
    page.insert_text((165, 317), "Try our app")
    page.insert_text((269, 396), "Ingredients")
    page.insert_text((64, 438), "250g")
    page.insert_text((64, 451), "plain flour")
    page.insert_text((64, 464), "plus a little more for dusting")
    page.insert_text((64, 487), "2 tbsp")
    page.insert_text((64, 500), "vegetable oil")
    page.insert_text((64, 525), "½ tsp")
    page.insert_text((64, 538), "fine salt")

    if include_method:
        page.insert_text((64, 590), "Method")
        page.insert_text((64, 622), "Step 1")
        page.insert_text((69, 637), "Combine the flour, vegetable oil and salt in a bowl.")
        page.insert_text((69, 650), "Pour over 150ml warm water and knead to bring it together.")
        page.insert_text((64, 685), "Step 2")
        page.insert_text((69, 700), "Cut the dough into 6 equal pieces and roll out thinly.")
        page.insert_text((64, 745), "Step 3")
        page.insert_text((69, 760), "Cook in a hot pan for 1-2 mins on each side until toasted.")

    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


class BbcGoodFoodPdfTests(unittest.TestCase):
    def test_extracts_bbc_goodfood_template_pdf(self) -> None:
        result = extract_bbc_goodfood_pdf(
            cookbook_title="Tortillas recipe | Good Food",
            filename="Tortillas recipe _ Good Food.pdf",
            object_key="ebooks/bbc.pdf",
            content_type="application/pdf",
            file_bytes=build_bbc_goodfood_template_pdf(),
        )

        self.assertEqual(result.title, "Tortillas")
        self.assertEqual(result.author, "Good Food team")
        self.assertEqual(result.makes, "6")
        self.assertEqual(result.difficulty, "Easy")
        self.assertEqual(result.prep_time, "30 mins")
        self.assertEqual(result.cook_time, "25 mins")
        self.assertEqual(len(result.drafts), 1)
        self.assertEqual(
            result.drafts[0].ingredients[0]["raw"],
            "250g plain flour plus a little more for dusting",
        )
        self.assertEqual(len(result.drafts[0].method_steps), 3)
        self.assertEqual(result.drafts[0].source["metadata"]["tags"][-1], "Vegetarian")
        self.assertIn("plus resting", result.drafts[0].source["metadata"]["preparation_notes"][0])

    def test_rejects_pdf_without_method_template_section(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected a Method section with Step headings"):
            extract_bbc_goodfood_pdf(
                cookbook_title="Tortillas recipe | Good Food",
                filename="Tortillas recipe _ Good Food.pdf",
                object_key="ebooks/bbc.pdf",
                content_type="application/pdf",
                file_bytes=build_bbc_goodfood_template_pdf(include_method=False),
            )


class StubRepository:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.stored = False

    def get_recipe_collection(self, slug: str) -> RecipeCollectionItem | None:
        if slug != "bbc-goodfood":
            return None
        return RecipeCollectionItem(
            slug="bbc-goodfood",
            title="BBC Good Food",
            description="BBC Good Food recipes",
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


class BbcGoodFoodUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_bbc_upload_is_deleted_and_not_processed(self) -> None:
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
            "app.main.extract_bbc_goodfood_pdf",
            side_effect=ValueError(
                "upload does not match the BBC Good Food PDF template: expected an ingredients section."
            ),
        ):
            response = await upload_cookbooks_form(
                request=request,
                files=[upload],
                collection_slug="bbc-goodfood",
            )

        self.assertEqual(repository.deleted, ["book-1"])
        self.assertFalse(repository.stored)
        self.assertEqual(response.status_code, 303)
        self.assertIn("/collections/bbc-goodfood?notice=", response.headers["location"])
        self.assertIn("Deleted", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
