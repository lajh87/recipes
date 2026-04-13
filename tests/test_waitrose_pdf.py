from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fitz
from fastapi import UploadFile

from app.main import upload_cookbooks_form
from app.models import RecipeCollectionItem
from app.waitrose_pdf import extract_waitrose_pdf


def build_waitrose_template_pdf(*, include_method: bool = True) -> bytes:
    document = fitz.open()
    document.new_page(width=595, height=842)
    document.new_page(width=595, height=842)
    page_one = document[0]
    page_two = document[1]

    page_one.insert_text((40, 448), "Ratatouille pasta salad & crisp crumbs")
    page_one.insert_text(
        (40, 492),
        "Sophie Pryn proves crisps are not just for snacking, using them to add crunch.",
    )
    page_one.insert_text((43, 648), "Serves 2")
    page_one.insert_text((43, 672), "Course Lunch")
    page_one.insert_text((43, 696), "Prepare 10 mins")
    page_one.insert_text((43, 720), "Cook 10 mins")
    page_one.insert_text((43, 744), "Total time 20 mins")

    page_two.insert_text((43, 60), "Ingredients")
    page_two.insert_text((43, 95), "150g Cavatappi pasta")
    page_two.insert_text((43, 120), "145g pack red chilli & tomato pesto")
    page_two.insert_text((43, 145), "160g pack Mediterranean chargrilled vegetables, roughly chopped")
    page_two.insert_text((43, 185), "60g pitted black olives, rinsed and torn in half")
    page_two.insert_text((303, 95), "220g jar No.1 Albacore Tuna, drained")
    page_two.insert_text((303, 120), "24g bag Walkers Salt & Shake crisps")
    page_two.insert_text((303, 145), "1/2 tbsp dried oregano")
    page_two.insert_text((303, 170), "Handful fresh basil, leaves picked, to serve")

    if include_method:
        page_two.insert_text((43, 255), "Method")
        page_two.insert_text((43, 291), "1")
        page_two.insert_text((64, 291), "Cook the pasta according to pack instructions, then drain well.")
        page_two.insert_text((64, 309), "Toss with the pesto, vegetables, olives and tuna.")
        page_two.insert_text((303, 291), "2")
        page_two.insert_text((324, 291), "Meanwhile, season and crush the crisps into crisp crumbs.")
        page_two.insert_text((324, 309), "Scatter over the pasta with fresh basil.")
        page_two.insert_text((43, 489), "Nutritional")
        page_two.insert_text((43, 523), "Typical values per serving when made using specific products in recipe")

    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


def build_waitrose_spanning_template_pdf() -> bytes:
    document = fitz.open()
    document.new_page(width=595, height=842)
    document.new_page(width=595, height=842)
    page_one = document[0]
    page_two = document[1]

    page_one.insert_text((40, 448), "Prawn moilee")
    page_one.insert_text(
        (40, 492),
        "Creamy coconut curry packed with ginger, chilli and juicy prawns.",
    )
    page_one.insert_text((43, 648), "Serves 4")
    page_one.insert_text((43, 672), "Course Dinner")
    page_one.insert_text((43, 696), "Prepare 15 mins")
    page_one.insert_text((43, 720), "Cook 30 mins")
    page_one.insert_text((43, 744), "Total time 45 mins")
    page_one.insert_text((43, 770), "Ingredients")
    page_one.insert_text((43, 795), "1 tbsp sunflower oil")
    page_one.insert_text((43, 820), "1 onion, finely sliced")

    page_two.insert_text((43, 60), "2 garlic cloves, crushed")
    page_two.insert_text((43, 85), "1 x 400ml tin coconut milk")
    page_two.insert_text((303, 60), "400g raw king prawns")
    page_two.insert_text((303, 85), "1 lime, to serve")
    page_two.insert_text((43, 255), "Method")
    page_two.insert_text((43, 291), "1")
    page_two.insert_text((64, 291), "Heat the oil in a large pan and soften the onion.")
    page_two.insert_text((43, 341), "2")
    page_two.insert_text((64, 341), "Stir in the garlic, then pour in the coconut milk.")
    page_two.insert_text((303, 291), "3")
    page_two.insert_text((324, 291), "Add the prawns and simmer until pink.")
    page_two.insert_text((303, 341), "4")
    page_two.insert_text((324, 341), "Finish with lime juice and serve immediately.")
    page_two.insert_text((43, 489), "Nutritional")

    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


class WaitrosePdfTests(unittest.TestCase):
    def test_extracts_waitrose_template_pdf(self) -> None:
        result = extract_waitrose_pdf(
            cookbook_title="Ratatouille Pasta Salad & Crisp Crumbs Recipe | Waitrose & Partners",
            filename="Ratatouille Pasta Salad & Crisp Crumbs Recipe _ Waitrose & Partners.pdf",
            object_key="ebooks/waitrose.pdf",
            content_type="application/pdf",
            file_bytes=build_waitrose_template_pdf(),
        )

        self.assertEqual(result.title, "Ratatouille pasta salad & crisp crumbs")
        self.assertEqual(result.serves, "2")
        self.assertEqual(result.course, "Lunch")
        self.assertEqual(result.prep_time, "10 mins")
        self.assertEqual(result.cook_time, "10 mins")
        self.assertEqual(result.total_time, "20 mins")
        self.assertEqual(len(result.drafts), 1)
        self.assertEqual(result.drafts[0].ingredients[0]["raw"], "150g Cavatappi pasta")
        self.assertEqual(len(result.drafts[0].method_steps), 2)
        self.assertEqual(
            result.drafts[0].source["metadata"]["intro"],
            "Sophie Pryn proves crisps are not just for snacking, using them to add crunch.",
        )

    def test_extracts_waitrose_template_with_ingredients_spanning_pages(self) -> None:
        result = extract_waitrose_pdf(
            cookbook_title="Prawn moilee recipe | Waitrose & Partners",
            filename="Prawn moilee recipe _ Waitrose & Partners.pdf",
            object_key="ebooks/prawn-moilee.pdf",
            content_type="application/pdf",
            file_bytes=build_waitrose_spanning_template_pdf(),
        )

        self.assertEqual(result.title, "Prawn moilee")
        self.assertEqual(
            [ingredient["raw"] for ingredient in result.drafts[0].ingredients],
            [
                "1 tbsp sunflower oil",
                "1 onion, finely sliced",
                "2 garlic cloves, crushed",
                "1 x 400ml tin coconut milk",
                "400g raw king prawns",
                "1 lime, to serve",
            ],
        )
        self.assertEqual(
            result.drafts[0].method_steps,
            [
                "Heat the oil in a large pan and soften the onion.",
                "Stir in the garlic, then pour in the coconut milk.",
                "Add the prawns and simmer until pink.",
                "Finish with lime juice and serve immediately.",
            ],
        )

    def test_rejects_pdf_without_method_template_section(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected a 2-column Method section with numbered steps"):
            extract_waitrose_pdf(
                cookbook_title="Ratatouille Pasta Salad & Crisp Crumbs Recipe | Waitrose & Partners",
                filename="Ratatouille Pasta Salad & Crisp Crumbs Recipe _ Waitrose & Partners.pdf",
                object_key="ebooks/waitrose.pdf",
                content_type="application/pdf",
                file_bytes=build_waitrose_template_pdf(include_method=False),
            )


class StubRepository:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.stored = False

    def get_recipe_collection(self, slug: str) -> RecipeCollectionItem | None:
        if slug != "waitrose-recipes":
            return None
        return RecipeCollectionItem(
            slug="waitrose-recipes",
            title="Waitrose Recipes",
            description="Waitrose recipes",
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


class WaitroseUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_waitrose_upload_is_deleted_and_not_processed(self) -> None:
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
            "app.main.extract_waitrose_pdf",
            side_effect=ValueError(
                "upload does not match the Waitrose PDF template: expected a 2-column Ingredients section on page 2."
            ),
        ):
            response = await upload_cookbooks_form(
                request=request,
                files=[upload],
                collection_slug="waitrose-recipes",
            )

        self.assertEqual(repository.deleted, ["book-1"])
        self.assertFalse(repository.stored)
        self.assertEqual(response.status_code, 303)
        self.assertIn("/collections/waitrose-recipes?notice=", response.headers["location"])
        self.assertIn("Deleted", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
