# Heley Family Cookbook

Recipe library and meal-planning application that connects to the shared MinIO, Redis, and Qdrant stores already running on the `shared-datastores` Docker network, and includes an OpenAI-backed extraction worker for recipe-only parsing.

The first milestone is ebook intake:

- create and own a dedicated MinIO bucket for cookbook uploads
- expose a UI for uploading ebook files
- keep lightweight cookbook ingest records in Redis
- pre-create Qdrant collections for later semantic indexing

## Current Scope

This scaffold includes:

- a FastAPI web app with an upload UI and cookbook shelf
- a worker service that extracts recipes, ingredients, method steps, images, and source evidence
- idempotent datastore bootstrap for MinIO, Redis, and Qdrant
- Docker assets wired to the external `shared-datastores` network
- OpenAI-backed structured recipe extraction and recipe embeddings
- a search page with ingredient filtering plus hybrid keyword and vector retrieval
- a weekly meal planner with day-by-day breakfast, lunch, and dinner slots
- optional recipe links from meal-plan slots into the extracted recipe library
- one-time seeding of the structured meal plan from the most recent sections of `data-raw/recipes.txt`

## Datastore Schema

This project creates its own application schema on the shared stores:

- MinIO bucket: `recipe-library-ebooks`
- Redis namespace: `recipes:*`
- Qdrant collections:
  - `recipes-cookbooks`
  - `recipes-recipe-chunks`

The worker additionally uses:

- OpenAI Responses API for structured recipe extraction
- OpenAI embeddings for recipe vectors stored in Qdrant

The bootstrap is idempotent, so it can be rerun safely.

## Local Start

Prerequisite: the shared datastore stack is already up from `/Users/lukeheley/Developer/shared-datastores`.

1. Copy the environment file.

```bash
cp .env.example .env
```

2. Create the application schema on MinIO, Redis, and Qdrant.

```bash
docker compose --profile bootstrap run --rm bootstrap
```

3. Start the web app.

```bash
docker compose up --build
```

4. Open `http://localhost:8080`.

## Current UI

The initial UI includes:

- a status strip for MinIO, Redis, Qdrant, and OpenAI readiness
- a cookbook upload form for `.pdf`, `.epub`, `.mobi`, and `.azw3` up to 500 MB per file
- a cookbook shelf showing upload and extraction state
- extracted recipe review panels with source traceability and image previews
- ingredient browse chips sourced from extracted recipe records
- a search page combining exact ingredient filtering, keyword ranking, semantic similarity, and grounded answer generation
- a meal-plan page linked from the top navigation
- structured weekly planning with Monday to Sunday rows and breakfast, lunch, and dinner checklists
- optional linking from each meal slot to a recipe in the library
- Redis-backed autosave for structured meal plans
- fallback import from the last few week/date sections in `data-raw/recipes.txt` when no structured plan exists yet

## OpenAI Configuration

Set `OPENAI_API_KEY` in `.env` before queueing extraction jobs.

Defaults:

- recipe extraction model: `gpt-5.4-mini`
- embedding model: `text-embedding-3-small`

## Roadmap

### Phase 1: Intake

- upload cookbook ebooks into MinIO
- register cookbook records and ingest state in Redis
- show uploaded cookbooks in a shelf-based library view

### Phase 2: Extraction

- parse EPUB and text-based PDF cookbook structure
- extract recipe-only records with ingredients, method steps, source evidence, and images
- persist extracted recipe metadata in Redis
- store recipe embeddings in Qdrant
- extract associated recipe imagery into MinIO

### Phase 3: Browse

- replace filename-only cards with cookbook covers, authors, tags, and extraction progress
- add cookbook detail pages with chapters, sections, and recipe counts
- surface recipe images in browse and detail views

### Phase 4: Semantic Search

- embed cookbook sections and recipe chunks
- add hybrid semantic search across titles, ingredients, methods, and tags
- show grounded search results with cookbook context and recipe previews

### Phase 5: Meal Planning

- assemble saved recipes into weekly meal plans
- support pantry-aware suggestions and richer planning workflows
- add shopping list generation and planning workflows

## Project Layout

```text
.
├── app
│   ├── bootstrap.py
│   ├── config.py
│   ├── main.py
│   ├── meal_plan.py
│   ├── models.py
│   ├── repository.py
│   ├── static
│   │   ├── app.js
│   │   └── styles.css
│   └── templates
│       ├── index.html
│       └── meal_plan.html
├── data-raw
│   └── recipes.txt
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Notes

- The app joins the existing Docker network `shared-datastores` and expects the hostnames `minio`, `redis`, and `qdrant`.
- Redis is used for application records, extraction jobs, ingredient indexes, review state, and meal-plan storage.
- Qdrant stores recipe-derived vectors only.
- The extraction worker currently targets EPUB and text-based PDF. Scan-only OCR is not implemented yet.
- If the Redis meal-plan key is empty, the app first migrates any existing `data-raw/meal-plan.json` document and otherwise seeds from the most recent few dated sections in `data-raw/recipes.txt`.
