# Heley Family Cookbook

Heley Family Cookbook is a Dockerised FastAPI application for building a private recipe library from ebooks, clipped recipe PDFs, and family planning notes. It stages source files in MinIO, stores library state in Redis, keeps recipe vectors in Qdrant, and exposes a web UI for browsing, search, collections, and weekly meal planning.

## What The App Does

- stages cookbook uploads and derives cover art and metadata where possible
- extracts structured recipes, ingredient lists, method steps, images, and source evidence
- supports two ingestion paths:
  - queued OpenAI-backed extraction for general cookbooks and ebooks
  - synchronous source-specific PDF extraction for `NYTimes`, `Jamie Oliver`, `BBC Good Food`, and `Waitrose Recipes`
- groups recipes into built-in collections such as `Favourites` and `Want To Try`
- indexes ingredients for faceted browse and recipe search
- combines keyword ranking with optional semantic retrieval from Qdrant
- stores and edits a Redis-backed weekly meal plan
- includes backup and restore scripts for the app-owned MinIO, Redis, and Qdrant data

## Architecture

The repository runs three containers:

- `app`: serves the FastAPI site and JSON API
- `worker`: processes queued cookbook extraction jobs
- `bootstrap`: creates the bucket, Redis metadata, and Qdrant collections

The app expects to join an existing external Docker network, `shared-datastores` by default, and talk to:

- MinIO for original uploads and derived images
- Redis for cookbook records, recipe payloads, ingredient indexes, favorites, review state, and meal plans
- Qdrant for recipe embeddings used during semantic search

App-owned schema:

- MinIO bucket: `recipe-library-ebooks`
- Redis prefix: `recipes:*`
- Qdrant collections:
  - `recipes-cookbooks`
  - `recipes-recipe-chunks`

## Main User Flows

### Library

- `/` shows the home shelf, cookbook cards, and built-in recipe collections
- `/library/manage` provides bulk editing for cookbook title, author, cuisine, and publication date
- `/cookbooks/{id}` shows a cookbook, extracted recipes, and reconstructed sections or table of contents
- `/recipes/{id}` shows the recipe detail page, source evidence, images, and collection toggles

### Search And Planning

- `/search` supports ingredient filtering plus keyword and semantic ranking
- `/meal-plan` stores a structured week-by-week planner in Redis and can link slots back to recipes
- if no structured meal plan exists yet, the app can seed one from recent sections in `data-raw/recipes.txt`

### Collections

Built-in collections include:

- `favourites`
- `want-to-try`
- `nytimes`
- `jamie-oliver`
- `bbc-goodfood`
- `waitrose-recipes`

The last four are also upload targets for source-specific PDF parsing.

## Extraction Model

General cookbooks are uploaded first and extracted later by the background worker. This path is intended for `.epub`, `.mobi`, `.azw3`, and general `.pdf` books.

Collection-specific PDFs are handled on upload:

- `nytimes`
- `jamie-oliver`
- `bbc-goodfood`
- `waitrose-recipes`

The generic extractor uses OpenAI for structured recipe extraction and embeddings. Semantic search also uses OpenAI embeddings when `OPENAI_API_KEY` is set. Without an API key, the app still runs, but queued extraction and semantic search degrade or become unavailable.

## Requirements

- Docker and Docker Compose
- a running MinIO, Redis, and Qdrant stack reachable from the external Docker network
- `OPENAI_API_KEY` for queued extraction and semantic search
- optional local tools for backup scripts:
  - `redis-cli`
  - `mc`
  - `gpg` if you want encrypted `.env` backups

## Quick Start

1. Copy the environment file.

```bash
cp .env.example .env
```

2. Set `OPENAI_API_KEY` in `.env` if you want queued extraction or semantic search.

3. Make sure the shared datastore stack is already running and exposes the network named by `DATASTORE_NETWORK_NAME`.

4. Bootstrap the app schema.

```bash
docker compose --profile bootstrap run --rm bootstrap
```

5. Start the web app and worker.

```bash
docker compose up --build
```

6. Open `http://localhost:8080`.

## Configuration

The main runtime settings live in `.env.example`.

Important values:

- `APP_PORT`
- `MINIO_ENDPOINT`
- `MINIO_BUCKET_NAME`
- `REDIS_URL`
- `REDIS_KEY_PREFIX`
- `QDRANT_URL`
- `QDRANT_COOKBOOK_COLLECTION`
- `QDRANT_RECIPE_COLLECTION`
- `OPENAI_API_KEY`
- `OPENAI_RECIPE_MODEL`
- `OPENAI_EMBEDDING_MODEL`
- `OPENAI_SEARCH_MODEL`
- `DATASTORE_NETWORK_NAME`

Default upload limits and types:

- max upload size: `500 MB`
- allowed extensions: `pdf,epub,mobi,azw3`

## JSON API

The UI is backed by a small JSON API:

- `GET /healthz`
- `GET /api/health`
- `GET /api/cookbooks`
- `PATCH /api/cookbooks/{cookbook_id}`
- `POST /api/cookbooks/{cookbook_id}/extract`
- `GET /api/cookbooks/{cookbook_id}/recipes`
- `GET /api/recipes`
- `GET /api/recipes/{recipe_id}`
- `PATCH /api/recipes/{recipe_id}/review`
- `GET /api/recipes/{recipe_id}/images/{image_index}`
- `GET /api/ingredients`
- `GET /api/search`
- `GET /api/meal-plan/recipe-suggestions`

## Repository Layout

```text
.
├── app/
│   ├── main.py
│   ├── repository.py
│   ├── extractor.py
│   ├── worker.py
│   ├── meal_plan.py
│   ├── blog.py
│   ├── templates/
│   └── static/
├── data-raw/
├── scripts/
├── tests/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

## Development Notes

- The container image is defined in [Dockerfile](/Users/lukeheley/Developer/recipes/Dockerfile).
- Runtime dependencies are listed in [requirements.txt](/Users/lukeheley/Developer/recipes/requirements.txt).
- Tests live under [tests](/Users/lukeheley/Developer/recipes/tests). The repository does not pin `pytest` in `requirements.txt`, so install it separately if you run tests outside your own dev environment.
- Once `pytest` is available, run the suite with `python -m pytest tests`.
- The app performs tolerant startup schema checks, so a broken datastore connection does not necessarily stop the web process from booting.

## Backup And Restore

The repository includes app-scoped backup tooling for the shared datastore layout:

- [scripts/backup_recipes.sh](/Users/lukeheley/Developer/recipes/scripts/backup_recipes.sh) builds and optionally pushes dated GHCR image tags, exports the MinIO bucket, exports Redis keys under the configured prefix, snapshots the Qdrant collections, and prunes old backups
- [scripts/restore_recipes.sh](/Users/lukeheley/Developer/recipes/scripts/restore_recipes.sh) restores those artifacts into the local datastore stack
- [scripts/recipes_backup.env.example](/Users/lukeheley/Developer/recipes/scripts/recipes_backup.env.example) is the configuration template for both scripts
- [ops/launchd/com.lukeheley.recipes-backup.plist](/Users/lukeheley/Developer/recipes/ops/launchd/com.lukeheley.recipes-backup.plist) is a macOS `launchd` template for scheduled weekly backups

Basic setup:

```bash
cp scripts/recipes_backup.env.example scripts/recipes_backup.env
chmod +x scripts/backup_recipes.sh scripts/restore_recipes.sh
```

Manual backup:

```bash
scripts/backup_recipes.sh scripts/recipes_backup.env
```

Restore the most recent backup:

```bash
scripts/restore_recipes.sh scripts/recipes_backup.env
```

Restore a specific dated folder:

```bash
scripts/restore_recipes.sh scripts/recipes_backup.env "$HOME/Library/CloudStorage/OneDrive-Personal/Backups/recipes/weekly/2026-04-13"
```

## Known Constraints

- generic cookbook extraction is best for EPUBs and text-based PDFs
- scan-only OCR is not implemented
- semantic search returns keyword-only results when OpenAI embeddings are unavailable
- the app is designed around a shared external datastore network rather than a fully self-contained local compose stack
