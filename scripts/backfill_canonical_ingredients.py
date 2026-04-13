from __future__ import annotations

from datetime import UTC, datetime

from app.config import get_settings
from app.repository import LibraryRepository


def main() -> None:
    settings = get_settings()
    repository = LibraryRepository.from_settings(settings)
    try:
        result = repository.backfill_canonical_ingredients()
        print(
            f"[{datetime.now(UTC).isoformat()}] canonical ingredient backfill complete "
            f"recipes_scanned={result['recipes_scanned']} "
            f"recipes_updated={result['recipes_updated']} "
            f"ingredients_updated={result['ingredients_updated']}",
            flush=True,
        )
    finally:
        repository.close()


if __name__ == "__main__":
    main()
