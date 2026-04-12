from __future__ import annotations

import json

from app.config import get_settings
from app.repository import LibraryRepository


def main() -> None:
    settings = get_settings()
    repository = LibraryRepository.from_settings(settings)
    summary = repository.ensure_schema()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

