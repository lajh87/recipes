from __future__ import annotations

import argparse
from datetime import UTC, datetime

from app.bbc_goodfood_pdf import extract_bbc_goodfood_pdf
from app.config import get_settings
from app.extractor import OpenAIRecipeExtractor, RecipeDraft
from app.jamie_oliver_pdf import extract_jamie_oliver_pdf
from app.nytimes_pdf import extract_nytimes_pdf
from app.repository import LibraryRepository
from app.waitrose_pdf import extract_waitrose_pdf


def _build_recipe_embeddings(
    settings,
    drafts: list[RecipeDraft],
    *,
    embedding_batch_size: int,
) -> list[list[float]]:
    if not drafts or not settings.openai_api_key:
        return []

    try:
        extractor = OpenAIRecipeExtractor(settings)
        embeddings: list[list[float]] = []
        texts = [draft.embedding_text() for draft in drafts]
        for start in range(0, len(texts), embedding_batch_size):
            batch = texts[start : start + embedding_batch_size]
            embeddings.extend(extractor.build_embeddings(batch))
            print(
                f"[{datetime.now(UTC).isoformat()}] embeddings "
                f"{min(start + len(batch), len(texts))}/{len(texts)}",
                flush=True,
            )
        return embeddings
    except Exception as exc:
        print(f"[{datetime.now(UTC).isoformat()}] embeddings skipped: {exc}", flush=True)
        return []


def _reprocess_collection_pdf(
    repository: LibraryRepository,
    settings,
    cookbook,
    file_bytes: bytes,
    *,
    embedding_batch_size: int,
) -> bool:
    if cookbook.collection_slug == "nytimes":
        parsed = extract_nytimes_pdf(
            cookbook_title=cookbook.title,
            filename=cookbook.filename,
            object_key=cookbook.object_key,
            content_type=cookbook.content_type,
            file_bytes=file_bytes,
        )
        repository.update_cookbook_metadata(
            cookbook.id,
            title=parsed.title,
            author=parsed.author,
            published_at=parsed.published_at,
        )
        embeddings = _build_recipe_embeddings(
            settings,
            parsed.drafts,
            embedding_batch_size=embedding_batch_size,
        )
        repository.store_extracted_recipes(cookbook.id, parsed.drafts, embeddings)
        print(
            f"[{datetime.now(UTC).isoformat()}] stored drafts={len(parsed.drafts)} cookbook_id={cookbook.id}",
            flush=True,
        )
        return True

    if cookbook.collection_slug == "jamie-oliver":
        parsed = extract_jamie_oliver_pdf(
            cookbook_title=cookbook.title,
            filename=cookbook.filename,
            object_key=cookbook.object_key,
            content_type=cookbook.content_type,
            file_bytes=file_bytes,
        )
        repository.update_cookbook_metadata(
            cookbook.id,
            title=parsed.title,
            author=parsed.author,
        )
        embeddings = _build_recipe_embeddings(
            settings,
            parsed.drafts,
            embedding_batch_size=embedding_batch_size,
        )
        repository.store_extracted_recipes(cookbook.id, parsed.drafts, embeddings)
        print(
            f"[{datetime.now(UTC).isoformat()}] stored drafts={len(parsed.drafts)} cookbook_id={cookbook.id}",
            flush=True,
        )
        return True

    if cookbook.collection_slug == "bbc-goodfood":
        parsed = extract_bbc_goodfood_pdf(
            cookbook_title=cookbook.title,
            filename=cookbook.filename,
            object_key=cookbook.object_key,
            content_type=cookbook.content_type,
            file_bytes=file_bytes,
        )
        repository.update_cookbook_metadata(
            cookbook.id,
            title=parsed.title,
            author=parsed.author,
        )
        embeddings = _build_recipe_embeddings(
            settings,
            parsed.drafts,
            embedding_batch_size=embedding_batch_size,
        )
        repository.store_extracted_recipes(cookbook.id, parsed.drafts, embeddings)
        print(
            f"[{datetime.now(UTC).isoformat()}] stored drafts={len(parsed.drafts)} cookbook_id={cookbook.id}",
            flush=True,
        )
        return True

    if cookbook.collection_slug == "waitrose-recipes":
        parsed = extract_waitrose_pdf(
            cookbook_title=cookbook.title,
            filename=cookbook.filename,
            object_key=cookbook.object_key,
            content_type=cookbook.content_type,
            file_bytes=file_bytes,
        )
        repository.update_cookbook_metadata(
            cookbook.id,
            title=parsed.title,
        )
        embeddings = _build_recipe_embeddings(
            settings,
            parsed.drafts,
            embedding_batch_size=embedding_batch_size,
        )
        repository.store_extracted_recipes(cookbook.id, parsed.drafts, embeddings)
        print(
            f"[{datetime.now(UTC).isoformat()}] stored drafts={len(parsed.drafts)} cookbook_id={cookbook.id}",
            flush=True,
        )
        return True

    return False


def reprocess_cookbook(
    cookbook_id: str,
    *,
    embedding_batch_size: int = 24,
    append_anchors: list[str] | None = None,
) -> None:
    settings = get_settings()
    repository = LibraryRepository.from_settings(settings)

    try:
        cookbook = repository.get_cookbook(cookbook_id)
        if not cookbook:
            raise SystemExit(f"Cookbook {cookbook_id} not found.")

        file_bytes = repository.download_cookbook(cookbook_id)
        append_anchor_set = {anchor.strip() for anchor in (append_anchors or []) if anchor.strip()}

        if append_anchor_set:
            extractor = OpenAIRecipeExtractor(settings)
            sections = extractor._extract_epub_sections(file_bytes)
            section_map = {section.anchor or section.section_key: section for section in sections}
            candidates = [
                section_map[anchor]
                for anchor in append_anchor_set
                if anchor in section_map and extractor._is_recipe_candidate(section_map[anchor])
            ]
            print(
                f"[{datetime.now(UTC).isoformat()}] cookbook={cookbook.title} "
                f"sections={len(sections)} candidates={len(candidates)}",
                flush=True,
            )

            drafts: list[RecipeDraft] = []
            for index, section in enumerate(candidates, start=1):
                payload = extractor._extract_recipe_payload(cookbook.title, section)
                if not payload.is_recipe:
                    print(
                        f"[{datetime.now(UTC).isoformat()}] skipped {index}/{len(candidates)} "
                        f"title={section.chapter_title or section.section_key}",
                        flush=True,
                    )
                    continue

                review_status, review_reasons = extractor._review_flags(payload, section)
                drafts.append(
                    RecipeDraft(
                        title=payload.title.strip() or (section.chapter_title or "Untitled Recipe"),
                        ingredients=[
                            ingredient.model_dump()
                            for ingredient in payload.ingredients
                            if ingredient.raw.strip()
                        ],
                        method_steps=[step.strip() for step in payload.method_steps if step.strip()],
                        source={
                            "object_key": cookbook.object_key,
                            "format": section.source_format,
                            "chapter_title": section.chapter_title,
                            "page_start": section.page_start,
                            "page_end": section.page_end,
                            "anchor": section.anchor or section.section_key,
                            "excerpt": section.excerpt,
                            "metadata": extractor._merge_source_metadata(section.metadata, payload),
                        },
                        images=section.images[:4],
                        confidence=payload.confidence,
                        notes=payload.notes,
                        review_status=review_status,
                        review_reasons=review_reasons,
                    )
                )
                print(
                    f"[{datetime.now(UTC).isoformat()}] extracted {index}/{len(candidates)} "
                    f"title={drafts[-1].title}",
                    flush=True,
                )

            embeddings: list[list[float]] = []
            texts = [draft.embedding_text() for draft in drafts]
            for start in range(0, len(texts), embedding_batch_size):
                batch = texts[start : start + embedding_batch_size]
                embeddings.extend(extractor.build_embeddings(batch))
                print(
                    f"[{datetime.now(UTC).isoformat()}] embeddings "
                    f"{min(start + len(batch), len(texts))}/{len(texts)}",
                    flush=True,
                )

            appended = repository.append_extracted_recipes(cookbook_id, drafts, embeddings)
            print(
                f"[{datetime.now(UTC).isoformat()}] appended drafts={appended} cookbook_id={cookbook_id}",
                flush=True,
            )
            return

        repository.mark_cookbook_processing(cookbook_id)
        if _reprocess_collection_pdf(
            repository,
            settings,
            cookbook,
            file_bytes,
            embedding_batch_size=embedding_batch_size,
        ):
            return

        extractor = OpenAIRecipeExtractor(settings)
        result = extractor.extract_cookbook(
            cookbook_title=cookbook.title,
            filename=cookbook.filename,
            object_key=cookbook.object_key,
            content_type=cookbook.content_type,
            file_bytes=file_bytes,
        )

        embeddings: list[list[float]] = []
        texts = [draft.embedding_text() for draft in result.recipes]
        for start in range(0, len(texts), embedding_batch_size):
            batch = texts[start : start + embedding_batch_size]
            embeddings.extend(extractor.build_embeddings(batch))
            print(
                f"[{datetime.now(UTC).isoformat()}] embeddings "
                f"{min(start + len(batch), len(texts))}/{len(texts)}",
                flush=True,
            )

        repository.store_extracted_recipes(
            cookbook_id,
            result.recipes,
            embeddings,
            table_of_contents=result.table_of_contents,
        )
        print(
            f"[{datetime.now(UTC).isoformat()}] stored drafts={len(result.recipes)} cookbook_id={cookbook_id}",
            flush=True,
        )
    finally:
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cookbook_id")
    parser.add_argument("--embedding-batch-size", type=int, default=24)
    parser.add_argument("--append-anchor", action="append", dest="append_anchors", default=[])
    args = parser.parse_args()
    reprocess_cookbook(
        args.cookbook_id,
        embedding_batch_size=args.embedding_batch_size,
        append_anchors=args.append_anchors,
    )


if __name__ == "__main__":
    main()
