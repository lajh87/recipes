"""Build and import the user-supplied Simple Too recipe PDFs as one book."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from io import BytesIO
from pathlib import Path

import fitz
from fastapi import UploadFile
from qdrant_client.http import models as qdrant_models
from starlette.datastructures import Headers

from app.config import get_settings
from app.models import CookbookTocEntry
from app.repository import LibraryRepository
from app.simple_too_pdf import extract_simple_too_pdf
from scripts.reprocess_cookbook import _build_recipe_embeddings


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "uploads" / "Simple too"
DATA_DIR = ROOT / "app" / "data"
OUTPUT_PDF = ROOT / "output" / "pdf" / "Ottolenghi Simple Too.pdf"
# simple_too_cover.png: https://ottolenghi.co.uk/products/simple-too
# simple_too_jacket.jpg: https://www.penguin.co.uk/books/443556/ottolenghi-simple-too-by-lochmuller-yotam-ottolenghi-and-verena/9781529109511
BOOK_FILENAME = OUTPUT_PDF.name
BOOK_TITLE = "Ottolenghi Simple Too"
BOOK_AUTHOR = "Yotam Ottolenghi, Verena Lochmuller"
CHAPTERS = (
    "Breakfast and brunch",
    "Snacks, spreads and soups",
    "Meat",
    "Fish",
    "Veggie mains",
    "Pasta",
    "Sides",
    "Salads",
    "Desserts",
    "Fundamentals",
)
CHAPTER_BOOK_PAGES = (13, 35, 69, 103, 123, 153, 175, 211, 251, 283)


def load_sources() -> tuple[list[dict], dict]:
    index = json.loads((DATA_DIR / "simple_too_index.json").read_text())
    rows = index["recipes"]
    fallback = json.loads((DATA_DIR / "simple_too_breakfast_potatoes_method.json").read_text())
    source_files = {path.name for path in SOURCE_DIR.glob("*.pdf")}
    indexed_files = {row["filename"] for row in rows}
    if len(rows) != 135 or len(indexed_files) != 135 or source_files != indexed_files:
        raise ValueError(
            f"Expected 135 matching PDFs; index={len(rows)}, files={len(source_files)}, "
            f"missing={sorted(indexed_files - source_files)}, extra={sorted(source_files - indexed_files)}"
        )
    if tuple(dict.fromkeys(row["chapter"] for row in rows)) != CHAPTERS:
        raise ValueError("Chapter order does not match the published contents.")
    if [(row["book_page"], row["title"]) for row in rows] != sorted(
        (row["book_page"], row["title"]) for row in rows
    ):
        raise ValueError("Recipe index is not sorted by book page.")
    return rows, fallback


def build_book(rows: list[dict], fallback: dict) -> list:
    OUTPUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    cover = DATA_DIR / "simple_too_cover.png"
    book = fitz.open()
    cover_page = book.new_page(width=595, height=842)
    cover_page.insert_image(cover_page.rect, filename=str(cover), keep_proportion=True)

    contents_page = book.new_page(width=595, height=842)
    contents_page.insert_text((60, 75), BOOK_TITLE, fontsize=24, fontname="helv")
    contents_page.insert_text((60, 116), "Contents", fontsize=18, fontname="helv")
    for index, (chapter, book_page) in enumerate(zip(CHAPTERS, CHAPTER_BOOK_PAGES, strict=True)):
        contents_page.insert_text((65, 155 + index * 36), chapter, fontsize=12, fontname="helv")
        contents_page.insert_text((500, 155 + index * 36), str(book_page), fontsize=12, fontname="helv")
    contents_page.insert_text((65, 565), "Page numbers above refer to the published book.", fontsize=10, fontname="helv")
    contents_page.insert_text((65, 585), "This source PDF contains the supplied recipe printouts in book order.", fontsize=10, fontname="helv")

    drafts = []
    toc = []
    last_chapter = None
    for row in rows:
        source_file = SOURCE_DIR / row["filename"]
        source_bytes = source_file.read_bytes()
        start_page = book.page_count + 1
        with fitz.open(stream=source_bytes, filetype="pdf") as recipe_pdf:
            if row["chapter"] != last_chapter:
                toc.append([1, row["chapter"], start_page])
                last_chapter = row["chapter"]
            toc.append([2, row["title"], start_page])
            book.insert_pdf(recipe_pdf)

        fallback_args = (
            {
                "fallback_method_steps": fallback["method_steps"],
                "fallback_method_url": fallback["source"],
            }
            if row["title"] == "Breakfast potatoes"
            else {}
        )
        draft = extract_simple_too_pdf(
            title=row["title"],
            chapter=row["chapter"],
            book_page=row["book_page"],
            filename=row["filename"],
            object_key="",
            file_bytes=source_bytes,
            source_page_start=start_page,
            **fallback_args,
        )
        if len(draft.images) != 1:
            raise ValueError(f"Expected one recipe photo for {row['title']}, got {len(draft.images)}")
        drafts.append(draft)

    book.set_toc(toc)
    book.set_metadata({"title": BOOK_TITLE, "author": BOOK_AUTHOR, "subject": "135 recipes from Simple Too"})
    book.save(str(OUTPUT_PDF), garbage=4, deflate=True)
    book.close()
    with fitz.open(str(OUTPUT_PDF)) as check:
        if len(check.get_toc()) != len(toc):
            raise ValueError("Source PDF bookmarks were not saved.")
        if check.page_count != 2 + sum(
            draft.source["page_end"] - draft.source["page_start"] + 1 for draft in drafts
        ):
            raise ValueError("Source PDF page count mismatch.")
    return drafts


def import_book(repository: LibraryRepository, rows: list[dict], drafts: list, *, embeddings: list[list[float]]) -> str:
    existing = [
        cookbook
        for cookbook in repository.list_cookbooks(include_collection_items=True)
        if cookbook.filename == BOOK_FILENAME and cookbook.collection_slug is None
    ]
    if existing:
        raise ValueError(f"Simple Too book already exists: {existing[0].id}")

    old_books = [
        cookbook
        for cookbook in repository.list_cookbooks(include_collection_items=True)
        if cookbook.collection_slug == "simple-too"
    ]
    if len(old_books) != 5:
        raise ValueError(f"Expected five existing Simple Too source records, found {len(old_books)}")
    old_by_title = {}
    for cookbook in old_books:
        recipes = repository.list_recipes(cookbook_id=cookbook.id)
        if len(recipes) != 1 or recipes[0].title != cookbook.title:
            raise ValueError(f"Unexpected existing source record: {cookbook.id}")
        old_by_title[recipes[0].title] = (cookbook, recipes[0])
    manifest_titles = {row["title"] for row in rows}
    if not set(old_by_title).issubset(manifest_titles):
        raise ValueError("An existing Simple Too recipe is absent from the new book index.")

    meal_plan_before = repository.load_meal_plan_payload()
    with OUTPUT_PDF.open("rb") as file:
        upload = UploadFile(
            file=file,
            filename=BOOK_FILENAME,
            headers=Headers({"content-type": "application/pdf"}),
        )
        cookbook = repository.upload_cookbook(upload)
    cookbook = repository.update_cookbook_metadata(
        cookbook.id,
        title=BOOK_TITLE,
        author=BOOK_AUTHOR,
        published_at="2026",
    )
    if not cookbook:
        raise ValueError("The new book could not be loaded after upload.")

    cover_bytes = (DATA_DIR / "simple_too_jacket.jpg").read_bytes()
    cover_key = f"{repository.settings.derived_prefix}/{cookbook.id}/cover/front-cover.jpg"
    repository.minio.put_object(
        repository.settings.minio_bucket_name,
        cover_key,
        BytesIO(cover_bytes),
        length=len(cover_bytes),
        content_type="image/jpeg",
    )
    repository.redis.hset(
        repository.settings.cookbook_key(cookbook.id),
        mapping={
            "cover_image_key": cover_key,
            "cover_image_content_type": "image/jpeg",
            "cover_extract_attempted_at": cookbook.uploaded_at,
        },
    )

    for draft in drafts:
        draft.source["object_key"] = cookbook.object_key
    for _old_cookbook, old_recipe in old_by_title.values():
        for ingredient_name in old_recipe.ingredient_names:
            repository.redis.srem(repository.settings.ingredient_key(ingredient_name), old_recipe.id)
    toc = [CookbookTocEntry(label=chapter, href=f"#chapter-{index + 1}") for index, chapter in enumerate(CHAPTERS)]
    recipe_ids = [old_by_title[draft.title][1].id if draft.title in old_by_title else None for draft in drafts]
    repository.store_extracted_recipes(
        cookbook.id,
        drafts,
        embeddings,
        table_of_contents=toc,
        recipe_ids=recipe_ids,
    )

    imported = repository.list_recipes(cookbook_id=cookbook.id)
    if len(imported) != 135 or [recipe.title for recipe in imported] != [row["title"] for row in rows]:
        raise ValueError("New book did not persist all 135 recipes in order; old sources were retained.")
    if any(repository.get_recipe(recipe.id) is None for _, recipe in old_by_title.values()):
        raise ValueError("An existing recipe ID was lost; old sources were retained.")
    if repository.load_meal_plan_payload() != meal_plan_before:
        raise ValueError("Meal plan changed during import; old sources were retained.")

    if not embeddings:
        try:
            repository.qdrant.delete(
                collection_name=repository.settings.qdrant_recipe_collection,
                points_selector=qdrant_models.PointIdsList(
                    points=[recipe.id for _, recipe in old_by_title.values()]
                ),
            )
        except Exception:
            pass

    # Detach the reused IDs before deleting the old source records, whose normal
    # deletion path would otherwise remove the recipes and meal-plan targets.
    for old_cookbook, _old_recipe in old_by_title.values():
        repository.redis.delete(repository.settings.cookbook_recipe_index_key(old_cookbook.id))
        repository.delete_cookbook(old_cookbook.id)
    if repository.load_meal_plan_payload() != meal_plan_before:
        raise ValueError("Meal plan changed while deleting old source records.")
    return cookbook.id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the book and replace the existing collection records.")
    args = parser.parse_args()
    rows, fallback = load_sources()
    drafts = build_book(rows, fallback)
    print(
        json.dumps(
            {
                "recipes": len(drafts),
                "chapters": dict(Counter(draft.source["chapter_title"] for draft in drafts)),
                "photos": sum(len(draft.images) for draft in drafts),
                "source_pdf": str(OUTPUT_PDF),
                "source_pdf_bytes": OUTPUT_PDF.stat().st_size,
            },
            indent=2,
        ),
        flush=True,
    )
    if not args.apply:
        return
    settings = get_settings()
    repository = LibraryRepository.from_settings(settings)
    try:
        embeddings = _build_recipe_embeddings(settings, drafts, embedding_batch_size=32)
        cookbook_id = import_book(repository, rows, drafts, embeddings=embeddings)
        print(f"Imported {len(drafts)} recipes into {BOOK_TITLE}: {cookbook_id}", flush=True)
    finally:
        repository.close()


if __name__ == "__main__":
    main()
