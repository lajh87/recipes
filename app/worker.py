from __future__ import annotations

import json
import logging
import time

from app.config import get_settings
from app.extractor import OpenAIRecipeExtractor
from app.repository import LibraryRepository

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    repository = LibraryRepository.from_settings(settings)
    extractor = OpenAIRecipeExtractor(settings)

    logger.info("Recipe extraction worker started.")
    try:
        while True:
            job = repository.pop_extraction_job(timeout=5)
            if not job:
                continue

            cookbook_id = job.get("cookbook_id")
            if not cookbook_id:
                continue

            try:
                cookbook = repository.get_cookbook(cookbook_id)
                if not cookbook:
                    logger.warning("Cookbook %s not found for extraction.", cookbook_id)
                    continue

                repository.mark_cookbook_processing(cookbook_id)
                payload = repository.download_cookbook(cookbook_id)
                result = extractor.extract_cookbook(
                    cookbook_title=cookbook.title,
                    filename=cookbook.filename,
                    object_key=cookbook.object_key,
                    content_type=cookbook.content_type,
                    file_bytes=payload,
                )
                embeddings = extractor.build_embeddings([draft.embedding_text() for draft in result.recipes])
                repository.store_extracted_recipes(
                    cookbook_id,
                    result.recipes,
                    embeddings,
                    table_of_contents=result.table_of_contents,
                )
                logger.info("Extracted %s recipes for cookbook %s.", len(result.recipes), cookbook_id)
            except Exception as exc:
                logger.exception("Extraction failed for cookbook %s", cookbook_id)
                repository.mark_cookbook_failed(cookbook_id, str(exc))
                time.sleep(1)
    finally:
        repository.close()


if __name__ == "__main__":
    main()
