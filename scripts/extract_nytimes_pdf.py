#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import get_settings
from app.nytimes_pdf import extract_nytimes_pdf
from app.repository import LibraryRepository


def _process_cookbook(repository: LibraryRepository, cookbook_id: str, *, store: bool) -> dict[str, object]:
    cookbook = repository.get_cookbook(cookbook_id)
    if not cookbook:
        raise SystemExit(f"Cookbook {cookbook_id} not found.")
    file_bytes = repository.download_cookbook(cookbook.id)
    result = extract_nytimes_pdf(
        cookbook_title=cookbook.title,
        filename=cookbook.filename,
        object_key=cookbook.object_key,
        content_type=cookbook.content_type,
        file_bytes=file_bytes,
    )
    if store:
        repository.update_cookbook_metadata(
            cookbook.id,
            title=result.title,
            author=result.author,
            published_at=result.published_at,
        )
        repository.store_extracted_recipes(cookbook.id, result.drafts, embeddings=[])
    return {
        "cookbook_id": cookbook.id,
        "title": result.title,
        "author": result.author,
        "published_at": result.published_at,
        "yield": result.yield_text,
        "drafts": [
            {
                "title": draft.title,
                "ingredients": draft.ingredients,
                "method_steps": draft.method_steps,
                "source": draft.source,
            }
            for draft in result.drafts
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract a recipe from a NYTimes recipe PDF.")
    parser.add_argument("--cookbook-id", help="Existing staged cookbook id in the repository.")
    parser.add_argument("--file", help="Local PDF file path.")
    parser.add_argument(
        "--all-collection",
        action="store_true",
        help="Process every cookbook currently assigned to the NYTimes collection.",
    )
    parser.add_argument("--store", action="store_true", help="Store extracted recipe back into the repository.")
    args = parser.parse_args()

    selected = [bool(args.cookbook_id), bool(args.file), bool(args.all_collection)]
    if sum(selected) != 1:
        parser.error("Provide exactly one of --cookbook-id, --file, or --all-collection.")

    settings = get_settings()
    repository = LibraryRepository.from_settings(settings)
    try:
        if args.cookbook_id:
            output: object = _process_cookbook(repository, args.cookbook_id, store=args.store)
        elif args.all_collection:
            output = []
            for cookbook in repository.list_cookbooks_for_collection("nytimes"):
                try:
                    output.append(_process_cookbook(repository, cookbook.id, store=args.store))
                except Exception as exc:
                    output.append(
                        {
                            "cookbook_id": cookbook.id,
                            "title": cookbook.title,
                            "error": str(exc),
                        }
                    )
        else:
            path = Path(args.file)
            file_bytes = path.read_bytes()
            result = extract_nytimes_pdf(
                cookbook_title=path.stem,
                filename=path.name,
                object_key=str(path),
                content_type="application/pdf",
                file_bytes=file_bytes,
            )
            output = {
                "title": result.title,
                "author": result.author,
                "published_at": result.published_at,
                "yield": result.yield_text,
                "drafts": [
                    {
                        "title": draft.title,
                        "ingredients": draft.ingredients,
                        "method_steps": draft.method_steps,
                        "source": draft.source,
                    }
                    for draft in result.drafts
                ],
            }

        print(json.dumps(output, indent=2))
    finally:
        repository.close()


if __name__ == "__main__":
    main()
