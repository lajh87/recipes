from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import fitz
from fastapi import UploadFile
from ebooklib import ITEM_IMAGE, epub
from minio import Minio
from minio.error import S3Error
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from redis import Redis

from app.config import Settings
from app.extractor import RecipeDraft
from app.ingredients import canonicalize_ingredient_name, ingredient_index_name, prepare_ingredient_mapping
from app.models import (
    CookbookItem,
    CookbookTocEntry,
    DatastoreStatus,
    IngredientIndexItem,
    IngredientRecord,
    RecipeCollectionItem,
    RecipeExtractionRecord,
    RecipeImageRecord,
    RecipeReferenceRecord,
    RecipeRecord,
    RecipeReviewRecord,
    RecipeSourceRecord,
    SearchAnswerRecord,
    SearchResultRecord,
)

logger = logging.getLogger(__name__)

FILENAME_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")
SCHEMA_VERSION = "3"
RECIPE_COLLECTIONS = (
    {
        "slug": "favourites",
        "title": "Favourites",
        "description": "Recipes you have starred from cards or individual recipe pages.",
        "allow_upload": False,
    },
    {
        "slug": "nytimes",
        "title": "NYTimes",
        "description": "Stage and manage recipe source files for New York Times recipes.",
        "allow_upload": True,
    },
    {
        "slug": "jamie-oliver",
        "title": "Jamie Oliver",
        "description": "Stage and manage recipe source files for Jamie Oliver recipes.",
        "allow_upload": True,
    },
    {
        "slug": "bbc-goodfood",
        "title": "BBC Good Food",
        "description": "Stage and manage recipe source files for BBC Good Food recipes.",
        "allow_upload": True,
    },
    {
        "slug": "waitrose-recipes",
        "title": "Waitrose Recipes",
        "description": "Stage and manage recipe source files for Waitrose Recipes.",
        "allow_upload": True,
    },
)


class LibraryRepository:
    def __init__(
        self,
        settings: Settings,
        minio_client: Minio,
        redis_client: Redis,
        qdrant_client: QdrantClient,
    ) -> None:
        self.settings = settings
        self.minio = minio_client
        self.redis = redis_client
        self.qdrant = qdrant_client

    @classmethod
    def from_settings(cls, settings: Settings) -> "LibraryRepository":
        minio_client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        qdrant_client = QdrantClient(url=settings.qdrant_url)
        return cls(settings, minio_client, redis_client, qdrant_client)

    def close(self) -> None:
        self.redis.close()
        close_client = getattr(self.qdrant, "close", None)
        if callable(close_client):
            close_client()

    def ensure_schema(self) -> dict[str, Any]:
        bucket_created = False
        if not self.minio.bucket_exists(self.settings.minio_bucket_name):
            self.minio.make_bucket(self.settings.minio_bucket_name)
            bucket_created = True

        created_at = datetime.now(UTC).isoformat()
        self.redis.set(self.settings.schema_version_key, SCHEMA_VERSION)
        self.redis.hset(
            self.settings.schema_metadata_key,
            mapping={
                "version": SCHEMA_VERSION,
                "bucket": self.settings.minio_bucket_name,
                "cookbook_collection": self.settings.qdrant_cookbook_collection,
                "recipe_collection": self.settings.qdrant_recipe_collection,
                "created_at": created_at,
            },
        )

        existing_collections = {
            collection.name
            for collection in self.qdrant.get_collections().collections
        }
        created_collections: list[str] = []
        for name in (
            self.settings.qdrant_cookbook_collection,
            self.settings.qdrant_recipe_collection,
        ):
            if name in existing_collections:
                continue

            self.qdrant.create_collection(
                collection_name=name,
                vectors_config=qdrant_models.VectorParams(
                    size=self.settings.qdrant_vector_size,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
            created_collections.append(name)

        return {
            "minio_bucket": self.settings.minio_bucket_name,
            "bucket_created": bucket_created,
            "redis_prefix": self.settings.redis_key_prefix,
            "qdrant_collections_created": created_collections,
        }

    def health_report(self) -> dict[str, list[dict[str, str | bool]]]:
        statuses = [
            self._check_minio(),
            self._check_redis(),
            self._check_qdrant(),
            self._check_openai(),
        ]
        return {"datastores": [status.model_dump() for status in statuses]}

    def upload_cookbook(self, upload: UploadFile, *, collection_slug: str | None = None) -> CookbookItem:
        filename = upload.filename or "untitled"
        extension = Path(filename).suffix.lower().lstrip(".")
        if extension not in self.settings.allowed_extensions:
            raise ValueError(
                f"Unsupported file type '{extension or 'unknown'}'. "
                f"Allowed types: {', '.join(sorted(self.settings.allowed_extensions))}."
            )

        upload.file.seek(0, os.SEEK_END)
        size_bytes = upload.file.tell()
        upload.file.seek(0)

        max_bytes = self.settings.max_upload_mb * 1024 * 1024
        if size_bytes <= 0:
            raise ValueError(f"'{filename}' is empty.")
        if size_bytes > max_bytes:
            raise ValueError(
                f"'{filename}' exceeds the {self.settings.max_upload_mb} MB upload limit."
            )

        cookbook_id = str(uuid4())
        object_key = self._build_object_key(cookbook_id, filename)
        uploaded_at = datetime.now(UTC).isoformat()
        content_type = upload.content_type or "application/octet-stream"
        file_bytes = upload.file.read()
        upload.file.seek(0)
        metadata = self._extract_cookbook_metadata(filename, file_bytes)
        title = metadata.get("title") or self._display_title(filename)

        self.minio.put_object(
            self.settings.minio_bucket_name,
            object_key,
            self._bytes_stream(file_bytes),
            length=size_bytes,
            content_type=content_type,
        )

        self.redis.hset(
            self.settings.cookbook_key(cookbook_id),
            mapping={
                "id": cookbook_id,
                "title": title,
                "author": metadata.get("author", ""),
                "cuisine": metadata.get("cuisine", ""),
                "published_at": metadata.get("published_at", ""),
                "collection_slug": collection_slug or "",
                "filename": filename,
                "object_key": object_key,
                "size_bytes": str(size_bytes),
                "content_type": content_type,
                "uploaded_at": uploaded_at,
                "status": "staged",
                "recipe_count": "0",
                "needs_review_count": "0",
                "extract_attempted_at": "",
                "extract_completed_at": "",
                "extract_error": "",
                "cover_image_key": "",
                "cover_image_content_type": "",
                "cover_extract_attempted_at": "",
                "metadata_extract_attempted_at": uploaded_at,
                "table_of_contents": "[]",
            },
        )
        self.redis.zadd(
            self.settings.cookbook_index_key,
            {cookbook_id: datetime.now(UTC).timestamp()},
        )

        return self.get_cookbook(cookbook_id)  # type: ignore[return-value]

    def list_cookbooks(
        self,
        sort_by: str = "title",
        *,
        include_collection_items: bool = True,
    ) -> list[CookbookItem]:
        try:
            cookbook_ids = self.redis.zrevrange(self.settings.cookbook_index_key, 0, -1)
            items: list[CookbookItem] = []

            for cookbook_id in cookbook_ids:
                data = self.redis.hgetall(self.settings.cookbook_key(cookbook_id))
                if not data:
                    continue
                cookbook = self._hydrate_cookbook(data)
                cookbook = self._ensure_cookbook_metadata(cookbook)
                cookbook = self._ensure_cookbook_cover(cookbook)
                items.append(cookbook)

            if items:
                return self._sort_cookbooks(
                    self._filter_cookbooks_for_library(items, include_collection_items),
                    sort_by,
                )
        except Exception:
            return self._sort_cookbooks(
                self._filter_cookbooks_for_library(
                    self._list_bucket_objects(),
                    include_collection_items,
                ),
                sort_by,
            )

        return self._sort_cookbooks(
            self._filter_cookbooks_for_library(
                self._list_bucket_objects(),
                include_collection_items,
            ),
            sort_by,
        )

    def list_recipe_collections(self) -> list[RecipeCollectionItem]:
        return [self._build_recipe_collection(collection) for collection in RECIPE_COLLECTIONS]

    def count_recipes(self) -> int:
        total = 0
        for cookbook in self.list_cookbooks(include_collection_items=True):
            total += int(cookbook.recipe_count or 0)
        return total

    def get_recipe_collection(self, slug: str) -> RecipeCollectionItem | None:
        if slug == "favorites":
            slug = "favourites"
        collection = next((item for item in RECIPE_COLLECTIONS if item["slug"] == slug), None)
        if not collection:
            return None
        return self._build_recipe_collection(collection)

    def list_recipes_for_collection(self, slug: str) -> list[RecipeRecord]:
        if slug == "favorites":
            slug = "favourites"
        if slug == "favourites":
            recipe_ids = self.redis.zrevrange(self.settings.favorite_recipe_index_key, 0, -1)
            recipes = [self.get_recipe(recipe_id) for recipe_id in recipe_ids]
            return [recipe for recipe in recipes if recipe]
        if not any(collection["slug"] == slug for collection in RECIPE_COLLECTIONS):
            return []
        collection_cookbook_ids = {
            cookbook.id
            for cookbook in self.list_cookbooks()
            if cookbook.collection_slug == slug
        }
        if not collection_cookbook_ids:
            return []
        return [recipe for recipe in self.list_recipes() if recipe.cookbook_id in collection_cookbook_ids]

    def list_cookbooks_for_collection(self, slug: str, *, sort_by: str = "title") -> list[CookbookItem]:
        if slug == "favorites":
            slug = "favourites"
        if slug == "favourites":
            return []
        if not any(collection["slug"] == slug for collection in RECIPE_COLLECTIONS):
            return []
        return [cookbook for cookbook in self.list_cookbooks(sort_by=sort_by) if cookbook.collection_slug == slug]

    def get_cookbook(self, cookbook_id: str) -> CookbookItem | None:
        data = self._get_cookbook_data(cookbook_id)
        if not data:
            return None
        cookbook = self._hydrate_cookbook(data)
        cookbook = self._ensure_cookbook_metadata(cookbook)
        return self._ensure_cookbook_cover(cookbook)

    def update_cookbook_metadata(
        self,
        cookbook_id: str,
        *,
        title: str | None = None,
        author: str | None = None,
        cuisine: str | None = None,
        published_at: str | None = None,
    ) -> CookbookItem | None:
        cookbook = self.get_cookbook(cookbook_id)
        if not cookbook:
            return None

        cleaned_title = self._clean_metadata_text(title) or cookbook.title
        cleaned_author = self._clean_metadata_text(author)
        cleaned_cuisine = self._clean_metadata_text(cuisine)
        normalized_published_at = self._normalize_published_at(published_at)

        self.redis.hset(
            self.settings.cookbook_key(cookbook_id),
            mapping={
                "title": cleaned_title,
                "author": cleaned_author,
                "cuisine": cleaned_cuisine,
                "published_at": normalized_published_at,
                "metadata_extract_attempted_at": datetime.now(UTC).isoformat(),
            },
        )
        return self.get_cookbook(cookbook_id)

    def delete_cookbook(self, cookbook_id: str) -> bool:
        cookbook = self.get_cookbook(cookbook_id)
        if not cookbook:
            return False

        self._delete_existing_recipes(cookbook_id)

        object_keys = [cookbook.object_key]
        if cookbook.cover_image_key:
            object_keys.append(cookbook.cover_image_key)

        for object_key in object_keys:
            try:
                self.minio.remove_object(self.settings.minio_bucket_name, object_key)
            except Exception:
                logger.warning("Could not remove object %s for cookbook %s.", object_key, cookbook_id)

        derived_prefix = f"{self.settings.derived_prefix}/{cookbook_id}/"
        try:
            for obj in self.minio.list_objects(
                self.settings.minio_bucket_name,
                prefix=derived_prefix,
                recursive=True,
            ):
                try:
                    self.minio.remove_object(self.settings.minio_bucket_name, obj.object_name)
                except Exception:
                    logger.warning(
                        "Could not remove derived object %s for cookbook %s.",
                        obj.object_name,
                        cookbook_id,
                    )
        except Exception:
            logger.warning("Could not enumerate derived objects for cookbook %s.", cookbook_id)

        self.redis.delete(self.settings.cookbook_key(cookbook_id))
        self.redis.delete(self.settings.cookbook_recipe_index_key(cookbook_id))
        self.redis.zrem(self.settings.cookbook_index_key, cookbook_id)
        return True

    def enqueue_extraction(self, cookbook_id: str) -> CookbookItem | None:
        cookbook = self.get_cookbook(cookbook_id)
        if not cookbook:
            return None
        if cookbook.status not in {"queued", "processing"}:
            attempted_at = datetime.now(UTC).isoformat()
            self.redis.hset(
                self.settings.cookbook_key(cookbook_id),
                mapping={
                    "status": "queued",
                    "extract_attempted_at": attempted_at,
                    "extract_error": "",
                },
            )
            self.redis.lpush(
                self.settings.extract_queue_key,
                json.dumps({"cookbook_id": cookbook_id, "queued_at": attempted_at}),
            )
        return self.get_cookbook(cookbook_id)

    def pop_extraction_job(self, timeout: int = 5) -> dict[str, Any] | None:
        result = self.redis.brpop(self.settings.extract_queue_key, timeout=timeout)
        if not result:
            return None
        _, payload = result
        return json.loads(payload)

    def mark_cookbook_processing(self, cookbook_id: str) -> None:
        self.redis.hset(
            self.settings.cookbook_key(cookbook_id),
            mapping={
                "status": "processing",
                "extract_attempted_at": datetime.now(UTC).isoformat(),
                "extract_error": "",
            },
        )

    def mark_cookbook_failed(self, cookbook_id: str, reason: str) -> None:
        self.redis.hset(
            self.settings.cookbook_key(cookbook_id),
            mapping={
                "status": "failed",
                "extract_error": reason[:500],
                "extract_completed_at": datetime.now(UTC).isoformat(),
            },
        )

    def download_cookbook(self, cookbook_id: str) -> bytes:
        data = self._get_cookbook_data(cookbook_id)
        cookbook = self._hydrate_cookbook(data) if data else None
        if not cookbook:
            raise ValueError(f"Cookbook '{cookbook_id}' not found.")
        response = self.minio.get_object(self.settings.minio_bucket_name, cookbook.object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def store_extracted_recipes(
        self,
        cookbook_id: str,
        drafts: list[RecipeDraft],
        embeddings: list[list[float]],
        *,
        table_of_contents: list[CookbookTocEntry] | None = None,
    ) -> None:
        cookbook = self.get_cookbook(cookbook_id)
        if not cookbook:
            raise ValueError(f"Cookbook '{cookbook_id}' not found.")

        self._delete_existing_recipes(cookbook_id)
        self._persist_extracted_recipes(
            cookbook,
            drafts,
            embeddings,
            existing_recipe_count=0,
            existing_needs_review_count=0,
            table_of_contents=table_of_contents,
        )

    def backfill_canonical_ingredients(self) -> dict[str, int]:
        recipe_ids = self._all_recipe_ids()
        recipes = self._get_recipes_by_ids(recipe_ids)

        updated_recipes = 0
        updated_ingredients = 0
        for recipe in recipes:
            prepared_ingredients = [prepare_ingredient_mapping(ingredient.model_dump()) for ingredient in recipe.ingredients]
            canonical_names = sorted(
                {
                    ingredient_index_name(ingredient)
                    for ingredient in prepared_ingredients
                    if ingredient_index_name(ingredient)
                }
            )

            ingredient_changed = any(
                ingredient.canonical_name != prepared.get("canonical_name")
                or ingredient.normalized_name != prepared.get("normalized_name")
                for ingredient, prepared in zip(recipe.ingredients, prepared_ingredients, strict=False)
            )
            ingredient_count_delta = sum(
                1
                for ingredient, prepared in zip(recipe.ingredients, prepared_ingredients, strict=False)
                if ingredient.canonical_name != prepared.get("canonical_name")
                or ingredient.normalized_name != prepared.get("normalized_name")
            )
            if len(prepared_ingredients) != len(recipe.ingredients):
                ingredient_changed = True
                ingredient_count_delta = max(ingredient_count_delta, len(prepared_ingredients))

            if ingredient_changed or recipe.ingredient_names != canonical_names:
                updated_recipe = recipe.model_copy(
                    update={
                        "ingredients": [
                            IngredientRecord.model_validate(item)
                            for item in prepared_ingredients
                        ],
                        "ingredient_names": canonical_names,
                    }
                )
                self.redis.set(self.settings.recipe_key(recipe.id), updated_recipe.model_dump_json())
                self.redis.set(
                    self.settings.recipe_reference_key(recipe.id),
                    self._build_recipe_reference(updated_recipe).model_dump_json(),
                )
                updated_recipes += 1
                updated_ingredients += ingredient_count_delta

        self._rebuild_ingredient_index()

        self.redis.set(self.settings.schema_version_key, SCHEMA_VERSION)
        self.redis.hset(
            self.settings.schema_metadata_key,
            mapping={
                "version": SCHEMA_VERSION,
                "ingredient_backfill_at": datetime.now(UTC).isoformat(),
            },
        )
        return {
            "recipes_scanned": len(recipes),
            "recipes_updated": updated_recipes,
            "ingredients_updated": updated_ingredients,
        }

    def append_extracted_recipes(
        self,
        cookbook_id: str,
        drafts: list[RecipeDraft],
        embeddings: list[list[float]],
        *,
        table_of_contents: list[CookbookTocEntry] | None = None,
    ) -> int:
        cookbook = self.get_cookbook(cookbook_id)
        if not cookbook:
            raise ValueError(f"Cookbook '{cookbook_id}' not found.")

        existing_recipes = self.list_recipes(cookbook_id=cookbook_id)
        existing_anchors = {
            recipe.source.anchor
            for recipe in existing_recipes
            if recipe.source.anchor
        }
        filtered_drafts: list[RecipeDraft] = []
        filtered_embeddings: list[list[float]] = []
        for index, draft in enumerate(drafts):
            anchor = (draft.source.get("anchor") or "").strip()
            if anchor and anchor in existing_anchors:
                continue
            filtered_drafts.append(draft)
            if index < len(embeddings):
                filtered_embeddings.append(embeddings[index])

        self._persist_extracted_recipes(
            cookbook,
            filtered_drafts,
            filtered_embeddings,
            existing_recipe_count=len(existing_recipes),
            existing_needs_review_count=sum(
                1 for recipe in existing_recipes if recipe.review.status == "needs_review"
            ),
            table_of_contents=table_of_contents,
        )
        return len(filtered_drafts)

    def _all_recipe_ids(self) -> list[str]:
        recipe_ids: list[str] = []
        cookbook_ids = self.redis.zrevrange(self.settings.cookbook_index_key, 0, -1)
        for cookbook_id in cookbook_ids:
            recipe_ids.extend(
                self.redis.zrange(self.settings.cookbook_recipe_index_key(cookbook_id), 0, -1)
            )
        return recipe_ids

    def _rebuild_ingredient_index(self) -> None:
        ingredient_keys = list(self.redis.scan_iter(match=f"{self.settings.ingredient_index_prefix}*"))
        if ingredient_keys:
            self.redis.delete(*ingredient_keys)

        for recipe in self.list_recipes():
            for ingredient_name in recipe.ingredient_names:
                self.redis.sadd(self.settings.ingredient_key(ingredient_name), recipe.id)

    def _persist_extracted_recipes(
        self,
        cookbook: CookbookItem,
        drafts: list[RecipeDraft],
        embeddings: list[list[float]],
        *,
        existing_recipe_count: int,
        existing_needs_review_count: int,
        table_of_contents: list[CookbookTocEntry] | None = None,
    ) -> None:
        needs_review_count = existing_needs_review_count
        points: list[qdrant_models.PointStruct] = []
        for index, draft in enumerate(drafts, start=existing_recipe_count + 1):
            recipe_id = str(uuid4())
            images = self._store_recipe_images(cookbook.id, recipe_id, draft.images)
            recipe = RecipeRecord(
                id=recipe_id,
                cookbook_id=cookbook.id,
                cookbook_title=cookbook.title,
                title=draft.title,
                ingredients=draft.ingredients,
                ingredient_names=sorted(
                    {
                        ingredient_index_name(ingredient)
                        for ingredient in draft.ingredients
                        if ingredient_index_name(ingredient)
                    }
                ),
                method_steps=draft.method_steps,
                images=images,
                source=RecipeSourceRecord.model_validate(draft.source),
                extraction=RecipeExtractionRecord(
                    model=self.settings.openai_recipe_model,
                    confidence=draft.confidence,
                    notes=draft.notes,
                    extracted_at=datetime.now(UTC).isoformat(),
                    needs_review_reasons=draft.review_reasons,
                ),
                review=RecipeReviewRecord(status=draft.review_status),
            )
            if recipe.review.status == "needs_review":
                needs_review_count += 1

            self.redis.set(self.settings.recipe_key(recipe_id), recipe.model_dump_json())
            self.redis.set(
                self.settings.recipe_reference_key(recipe_id),
                self._build_recipe_reference(recipe).model_dump_json(),
            )
            self.redis.zadd(self.settings.cookbook_recipe_index_key(cookbook.id), {recipe_id: index})
            for ingredient_name in recipe.ingredient_names:
                self.redis.sadd(self.settings.ingredient_key(ingredient_name), recipe_id)

            embedding_index = index - existing_recipe_count - 1
            if embedding_index < len(embeddings):
                points.append(
                    qdrant_models.PointStruct(
                        id=recipe_id,
                        vector=embeddings[embedding_index],
                        payload={
                            "recipe_id": recipe_id,
                            "cookbook_id": cookbook.id,
                            "title": recipe.title,
                            "ingredients": recipe.ingredient_names,
                            "review_status": recipe.review.status,
                            "source_anchor": recipe.source.anchor,
                            "page_start": recipe.source.page_start,
                            "page_end": recipe.source.page_end,
                        },
                    )
                )

        if points:
            self.qdrant.upsert(
                collection_name=self.settings.qdrant_recipe_collection,
                points=points,
            )

        completed_at = datetime.now(UTC).isoformat()
        cookbook_mapping = {
            "status": "extracted" if (existing_recipe_count + len(drafts)) else "partially_extracted",
            "recipe_count": str(existing_recipe_count + len(drafts)),
            "needs_review_count": str(needs_review_count),
            "extract_completed_at": completed_at,
            "extract_error": "" if (existing_recipe_count + len(drafts)) else "No recipe candidates were extracted.",
        }
        if table_of_contents is not None:
            cookbook_mapping["table_of_contents"] = self._serialize_cookbook_toc(table_of_contents)
        self.redis.hset(
            self.settings.cookbook_key(cookbook.id),
            mapping=cookbook_mapping,
        )

    def list_recipes(
        self,
        cookbook_id: str | None = None,
        ingredient: str | None = None,
        ingredients: list[str] | None = None,
    ) -> list[RecipeRecord]:
        recipe_ids: list[str] = []
        normalized_ingredients = self._normalize_ingredient_list(ingredients)
        if ingredient and not normalized_ingredients:
            normalized_ingredients = self._normalize_ingredient_list([ingredient])

        if cookbook_id:
            recipe_ids = self.redis.zrange(self.settings.cookbook_recipe_index_key(cookbook_id), 0, -1)
        elif normalized_ingredients:
            if len(normalized_ingredients) == 1:
                recipe_ids = sorted(
                    self.redis.smembers(self.settings.ingredient_key(normalized_ingredients[0]))
                )
            else:
                ingredient_keys = [
                    self.settings.ingredient_key(current_ingredient)
                    for current_ingredient in normalized_ingredients
                ]
                recipe_ids = sorted(self.redis.sinter(ingredient_keys))
        else:
            cookbook_ids = self.redis.zrevrange(self.settings.cookbook_index_key, 0, -1)
            for current_id in cookbook_ids:
                recipe_ids.extend(
                    self.redis.zrange(self.settings.cookbook_recipe_index_key(current_id), 0, -1)
                )

        filtered = self._get_recipes_by_ids(recipe_ids)
        if normalized_ingredients and cookbook_id:
            filtered = [
                recipe
                for recipe in filtered
                if all(current_ingredient in recipe.ingredient_names for current_ingredient in normalized_ingredients)
            ]
        return filtered

    def list_recipe_references(self, cookbook_id: str | None = None) -> list[RecipeReferenceRecord]:
        recipe_ids: list[str] = []
        if cookbook_id:
            recipe_ids = self.redis.zrange(self.settings.cookbook_recipe_index_key(cookbook_id), 0, -1)
        else:
            cookbook_ids = self.redis.zrevrange(self.settings.cookbook_index_key, 0, -1)
            for current_id in cookbook_ids:
                recipe_ids.extend(
                    self.redis.zrange(self.settings.cookbook_recipe_index_key(current_id), 0, -1)
                )
        return self._get_recipe_references_by_ids(recipe_ids)

    def get_recipe(self, recipe_id: str) -> RecipeRecord | None:
        payload = self.redis.get(self.settings.recipe_key(recipe_id))
        if not payload:
            return None
        return self._hydrate_recipe_payload(
            payload,
            recipe_id=recipe_id,
            is_favorite=self.is_recipe_favorite(recipe_id),
        )

    def is_recipe_favorite(self, recipe_id: str) -> bool:
        return self.redis.zscore(self.settings.favorite_recipe_index_key, recipe_id) is not None

    def set_recipe_favorite(self, recipe_id: str, *, is_favorite: bool) -> RecipeRecord | None:
        recipe = self.get_recipe(recipe_id)
        if not recipe:
            return None
        if is_favorite:
            self.redis.zadd(
                self.settings.favorite_recipe_index_key,
                {recipe_id: datetime.now(UTC).timestamp()},
            )
        else:
            self.redis.zrem(self.settings.favorite_recipe_index_key, recipe_id)
        return recipe.model_copy(update={"is_favorite": is_favorite})

    def update_recipe_review(
        self,
        recipe_id: str,
        *,
        status: str,
        note: str | None = None,
    ) -> RecipeRecord | None:
        recipe = self.get_recipe(recipe_id)
        if not recipe:
            return None

        recipe.review = RecipeReviewRecord(
            status=status, note=note, reviewed_at=datetime.now(UTC).isoformat()
        )
        self.redis.set(self.settings.recipe_key(recipe_id), recipe.model_dump_json())
        self._refresh_cookbook_review_count(recipe.cookbook_id)
        return recipe

    def list_ingredients(
        self,
        query: str | None = None,
        limit: int | None = None,
        ingredients: list[str] | None = None,
    ) -> list[IngredientIndexItem]:
        normalized_ingredients = self._normalize_ingredient_list(ingredients)
        if normalized_ingredients:
            counts: dict[str, int] = {}
            for recipe in self.list_recipes(ingredients=normalized_ingredients):
                for ingredient_name in recipe.ingredient_names:
                    if ingredient_name in normalized_ingredients:
                        continue
                    if query and self._normalize_ingredient(query) not in ingredient_name:
                        continue
                    counts[ingredient_name] = counts.get(ingredient_name, 0) + 1
            items = [
                IngredientIndexItem(name=name, recipe_count=recipe_count)
                for name, recipe_count in counts.items()
            ]
            items.sort(key=lambda item: (-item.recipe_count, item.name))
            return items[:limit] if limit is not None else items

        if query:
            match = f"{self.settings.ingredient_index_prefix}{self._normalize_ingredient(query)}*"
        else:
            match = f"{self.settings.ingredient_index_prefix}*"

        items: list[IngredientIndexItem] = []
        for key in self.redis.scan_iter(match=match):
            ingredient_name = key.replace(self.settings.ingredient_index_prefix, "", 1)
            if not ingredient_name or ingredient_name == self.settings.redis_key_prefix:
                continue
            items.append(
                IngredientIndexItem(
                    name=ingredient_name,
                    recipe_count=self.redis.scard(key),
                )
            )
        items.sort(key=lambda item: (-item.recipe_count, item.name))
        return items[:limit] if limit is not None else items

    def get_recipe_image(self, recipe_id: str, image_index: int) -> tuple[bytes, str] | None:
        recipe = self.get_recipe(recipe_id)
        if not recipe or image_index < 0 or image_index >= len(recipe.images):
            return None
        image = recipe.images[image_index]
        response = self.minio.get_object(self.settings.minio_bucket_name, image.object_key)
        try:
            return response.read(), image.content_type
        finally:
            response.close()
            response.release_conn()

    def get_cookbook_cover(self, cookbook_id: str) -> tuple[bytes, str] | None:
        cookbook = self.get_cookbook(cookbook_id)
        if not cookbook or not cookbook.cover_image_key:
            return None
        response = self.minio.get_object(self.settings.minio_bucket_name, cookbook.cover_image_key)
        try:
            return response.read(), cookbook.cover_image_content_type or "application/octet-stream"
        finally:
            response.close()
            response.release_conn()

    def search_recipes(
        self,
        *,
        query: str | None = None,
        ingredient: str | None = None,
        ingredients: list[str] | None = None,
        limit: int | None = None,
    ) -> list[SearchResultRecord]:
        normalized_ingredients = self._normalize_ingredient_list(ingredients)
        if ingredient and not normalized_ingredients:
            normalized_ingredients = self._normalize_ingredient_list([ingredient])
        candidates = self.list_recipes(ingredients=normalized_ingredients) if normalized_ingredients else self.list_recipes()
        if not candidates:
            return []

        effective_limit = limit if limit is not None else len(candidates)

        if not query and normalized_ingredients:
            return [
                SearchResultRecord(
                    recipe=recipe,
                    score=1.0,
                    keyword_score=1.0,
                    semantic_score=0.0,
                    matched_terms=[],
                    matched_ingredient=", ".join(normalized_ingredients),
                )
                for recipe in candidates[:effective_limit]
            ]

        query_terms = self._normalize_search_query(query)
        keyword_scores: dict[str, float] = {}
        matched_terms: dict[str, list[str]] = {}
        if query_terms:
            for recipe in candidates:
                score, terms = self._keyword_score(recipe, query_terms)
                if score > 0:
                    keyword_scores[recipe.id] = score
                    matched_terms[recipe.id] = terms

        semantic_scores = (
            self._semantic_scores(
                query or "",
                candidates,
                limit=max(effective_limit * 5, 20),
            )
            if query
            else {}
        )
        keyword_rank = self._rank_map(keyword_scores)
        semantic_rank = self._rank_map(semantic_scores)
        candidate_map = {recipe.id: recipe for recipe in candidates}

        fused: dict[str, float] = {}
        for recipe_id in set(keyword_rank) | set(semantic_rank):
            score = 0.0
            if recipe_id in keyword_rank:
                score += 1.0 / (60 + keyword_rank[recipe_id])
            if recipe_id in semantic_rank:
                score += 1.0 / (60 + semantic_rank[recipe_id])
            if normalized_ingredients and recipe_id in candidate_map:
                score += 0.02
            fused[recipe_id] = score

        if not fused and query:
            return []

        ranked_pairs = sorted(
            (fused or {recipe.id: 1.0 for recipe in candidates}).items(),
            key=lambda item: item[1],
            reverse=True,
        )

        results: list[SearchResultRecord] = []
        for recipe_id, score in ranked_pairs[:effective_limit]:
            recipe = candidate_map.get(recipe_id)
            if not recipe:
                continue
            results.append(
                SearchResultRecord(
                    recipe=recipe,
                    score=round(float(score), 5),
                    keyword_score=round(float(keyword_scores.get(recipe_id, 0.0)), 5),
                    semantic_score=round(float(semantic_scores.get(recipe_id, 0.0)), 5),
                    matched_terms=matched_terms.get(recipe_id, []),
                    matched_ingredient=", ".join(normalized_ingredients) if normalized_ingredients else None,
                )
            )
        return results

    def keyword_recipe_suggestions(
        self,
        *,
        query: str | None,
        limit: int = 6,
    ) -> list[RecipeRecord]:
        query_terms = self._normalize_search_query(query)
        if not query_terms:
            return []

        scored: list[tuple[float, RecipeRecord]] = []
        for recipe in self.list_recipes():
            score, _terms = self._keyword_score(recipe, query_terms)
            if score > 0:
                scored.append((score, recipe))

        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].title.casefold(),
                item[1].cookbook_title.casefold(),
            )
        )
        return [recipe for _score, recipe in scored[: max(limit, 0)]]

    def generate_search_answer(
        self,
        *,
        query: str,
        results: list[SearchResultRecord],
    ) -> SearchAnswerRecord | None:
        if not query.strip() or not results or not self.settings.openai_api_key:
            return None

        contexts: list[str] = []
        citations: list[str] = []
        for item in results[:5]:
            recipe = item.recipe
            citations.append(recipe.id)
            contexts.append(
                "\n".join(
                    [
                        f"Recipe ID: {recipe.id}",
                        f"Title: {recipe.title}",
                        f"Cookbook: {recipe.cookbook_title}",
                        f"Ingredients: {', '.join(recipe.ingredient_names)}",
                        f"Method: {' '.join(recipe.method_steps[:6])}",
                        f"Source anchor: {recipe.source.anchor or recipe.source.chapter_title or 'unknown'}",
                        f"Source excerpt: {recipe.source.excerpt}",
                    ]
                )
            )

        response = self._openai_client().responses.create(
            model=self.settings.openai_search_model,
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You answer recipe search queries using only the provided retrieved recipes. "
                                "Do not invent recipes or ingredients. "
                                "Be concise. Cite recipe ids inline in square brackets like [recipe-id]."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"Query: {query}\n\n"
                                "Retrieved recipe contexts:\n\n"
                                + "\n\n---\n\n".join(contexts)
                            ),
                        }
                    ],
                },
            ],
        )

        answer = getattr(response, "output_text", "").strip()
        if not answer:
            payload = response.model_dump()
            answer = json.dumps(payload.get("output", []))[:1000]
        return SearchAnswerRecord(answer=answer, citations=citations)

    def _delete_existing_recipes(self, cookbook_id: str) -> None:
        recipe_ids = self.redis.zrange(self.settings.cookbook_recipe_index_key(cookbook_id), 0, -1)
        if not recipe_ids:
            return

        qdrant_ids: list[str] = []
        for recipe_id in recipe_ids:
            recipe = self.get_recipe(recipe_id)
            if recipe:
                for ingredient_name in recipe.ingredient_names:
                    self.redis.srem(self.settings.ingredient_key(ingredient_name), recipe_id)
            self.redis.delete(self.settings.recipe_key(recipe_id))
            self.redis.delete(self.settings.recipe_reference_key(recipe_id))
            self.redis.zrem(self.settings.favorite_recipe_index_key, recipe_id)
            qdrant_ids.append(recipe_id)
        self.redis.delete(self.settings.cookbook_recipe_index_key(cookbook_id))

        if qdrant_ids:
            try:
                self.qdrant.delete(
                    collection_name=self.settings.qdrant_recipe_collection,
                    points_selector=qdrant_models.PointIdsList(points=qdrant_ids),
                )
            except Exception:
                logger.warning("Could not clear existing Qdrant recipe points for cookbook %s.", cookbook_id)

    def _store_recipe_images(
        self,
        cookbook_id: str,
        recipe_id: str,
        images: list[Any],
    ) -> list[RecipeImageRecord]:
        records: list[RecipeImageRecord] = []
        for index, image in enumerate(images, start=1):
            filename = self._safe_filename(image.filename)
            object_key = (
                f"{self.settings.derived_prefix}/"
                f"{cookbook_id}/recipes/{recipe_id}/images/{index}-{filename}"
            )
            self.minio.put_object(
                self.settings.minio_bucket_name,
                object_key,
                data=self._bytes_stream(image.data),
                length=len(image.data),
                content_type=image.content_type,
            )
            records.append(
                RecipeImageRecord(
                    object_key=object_key,
                    content_type=image.content_type,
                    source_ref=image.source_ref,
                )
            )
        return records

    def _bytes_stream(self, data: bytes):
        from io import BytesIO

        return BytesIO(data)

    def _refresh_cookbook_review_count(self, cookbook_id: str) -> None:
        count = sum(
            1
            for recipe in self.list_recipes(cookbook_id=cookbook_id)
            if recipe.review.status == "needs_review"
        )
        self.redis.hset(self.settings.cookbook_key(cookbook_id), mapping={"needs_review_count": str(count)})

    def _list_bucket_objects(self) -> list[CookbookItem]:
        items: list[CookbookItem] = []
        try:
            objects = self.minio.list_objects(
                self.settings.minio_bucket_name,
                prefix=f"{self.settings.upload_prefix}/",
                recursive=True,
            )
        except S3Error:
            return items

        for obj in objects:
            filename = Path(obj.object_name).name
            items.append(
                CookbookItem(
                    id=obj.object_name.replace("/", "-"),
                    title=self._display_title(filename),
                    author=None,
                    cuisine=None,
                    published_at=None,
                    collection_slug=None,
                    filename=filename,
                    object_key=obj.object_name,
                    size_bytes=obj.size or 0,
                    content_type="application/octet-stream",
                    uploaded_at=obj.last_modified.isoformat() if obj.last_modified else "",
                    status="staged",
                    cover_image_key=None,
                    cover_image_content_type=None,
                    cover_extract_attempted_at=None,
                    metadata_extract_attempted_at=None,
                )
            )

        items.sort(key=lambda item: item.uploaded_at, reverse=True)
        return items

    def _build_recipe_collection(
        self,
        collection: dict[str, str],
    ) -> RecipeCollectionItem:
        matching_recipes = self.list_recipes_for_collection(str(collection["slug"]))
        return RecipeCollectionItem(
            slug=str(collection["slug"]),
            title=str(collection["title"]),
            description=str(collection["description"]),
            recipe_count=len(matching_recipes),
            allow_upload=bool(collection.get("allow_upload", True)),
        )

    def _hydrate_cookbook(self, data: dict[str, str]) -> CookbookItem:
        status = data.get("status", "staged")
        if status == "uploaded":
            status = "staged"
        return CookbookItem(
            id=data["id"],
            title=data["title"],
            author=data.get("author") or None,
            cuisine=data.get("cuisine") or None,
            published_at=data.get("published_at") or None,
            collection_slug=data.get("collection_slug") or None,
            filename=data["filename"],
            object_key=data["object_key"],
            size_bytes=int(data["size_bytes"]),
            content_type=data.get("content_type", "application/octet-stream"),
            uploaded_at=data["uploaded_at"],
            status=status,
            recipe_count=int(data.get("recipe_count", "0") or 0),
            needs_review_count=int(data.get("needs_review_count", "0") or 0),
            extract_attempted_at=data.get("extract_attempted_at") or None,
            extract_completed_at=data.get("extract_completed_at") or None,
            extract_error=data.get("extract_error") or None,
            cover_image_key=data.get("cover_image_key") or None,
            cover_image_content_type=data.get("cover_image_content_type") or None,
            cover_extract_attempted_at=data.get("cover_extract_attempted_at") or None,
            metadata_extract_attempted_at=data.get("metadata_extract_attempted_at") or None,
            table_of_contents=self._hydrate_cookbook_toc(data.get("table_of_contents")),
        )

    def _get_cookbook_data(self, cookbook_id: str) -> dict[str, str]:
        return self.redis.hgetall(self.settings.cookbook_key(cookbook_id))

    def _ensure_cookbook_metadata(self, cookbook: CookbookItem) -> CookbookItem:
        if cookbook.metadata_extract_attempted_at:
            return cookbook

        attempted_at = datetime.now(UTC).isoformat()
        mapping: dict[str, str] = {"metadata_extract_attempted_at": attempted_at}
        try:
            file_bytes = self.download_cookbook(cookbook.id)
            metadata = self._extract_cookbook_metadata(cookbook.filename, file_bytes)
            mapping.update(
                {
                    "title": metadata.get("title", cookbook.title),
                    "author": metadata.get("author", ""),
                    "cuisine": metadata.get("cuisine", ""),
                    "published_at": metadata.get("published_at", ""),
                }
            )
        except Exception as exc:
            logger.warning("Could not derive metadata for cookbook %s: %s", cookbook.id, exc)

        self.redis.hset(self.settings.cookbook_key(cookbook.id), mapping=mapping)
        refreshed = self.redis.hgetall(self.settings.cookbook_key(cookbook.id))
        return self._hydrate_cookbook(refreshed) if refreshed else cookbook

    def _ensure_cookbook_cover(self, cookbook: CookbookItem) -> CookbookItem:
        if cookbook.cover_image_key or cookbook.cover_extract_attempted_at:
            return cookbook

        attempted_at = datetime.now(UTC).isoformat()
        mapping: dict[str, str] = {"cover_extract_attempted_at": attempted_at}
        try:
            file_bytes = self.download_cookbook(cookbook.id)
            cover = self._extract_cookbook_cover(cookbook.filename, file_bytes)
            if cover:
                image_bytes, content_type, extension = cover
                object_key = (
                    f"{self.settings.derived_prefix}/"
                    f"{cookbook.id}/cover/front-cover.{extension}"
                )
                self.minio.put_object(
                    self.settings.minio_bucket_name,
                    object_key,
                    data=self._bytes_stream(image_bytes),
                    length=len(image_bytes),
                    content_type=content_type,
                )
                mapping.update(
                    {
                        "cover_image_key": object_key,
                        "cover_image_content_type": content_type,
                    }
                )
        except Exception as exc:
            logger.warning("Could not derive cover image for cookbook %s: %s", cookbook.id, exc)

        self.redis.hset(self.settings.cookbook_key(cookbook.id), mapping=mapping)
        refreshed = self.redis.hgetall(self.settings.cookbook_key(cookbook.id))
        return self._hydrate_cookbook(refreshed) if refreshed else cookbook

    def _hydrate_cookbook_toc(self, payload: str | None) -> list[CookbookTocEntry]:
        if not payload:
            return []
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        entries: list[CookbookTocEntry] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(CookbookTocEntry.model_validate(item))
            except Exception:
                continue
        return entries

    def _serialize_cookbook_toc(self, entries: list[CookbookTocEntry]) -> str:
        return json.dumps([entry.model_dump() for entry in entries], ensure_ascii=False)

    def _sort_cookbooks(self, cookbooks: list[CookbookItem], sort_by: str) -> list[CookbookItem]:
        normalized_sort = (sort_by or "title").strip().lower()
        if normalized_sort == "author":
            return sorted(cookbooks, key=lambda item: self._text_sort_key(item.author, item.title))
        if normalized_sort == "cuisine":
            return sorted(cookbooks, key=lambda item: self._text_sort_key(item.cuisine, item.title))
        if normalized_sort in {"published_at", "published", "date_published"}:
            return sorted(
                cookbooks,
                key=lambda item: (self._published_sort_key(item.published_at), self._sortable_text(item.title)),
                reverse=True,
            )
        return sorted(cookbooks, key=lambda item: self._text_sort_key(item.title, item.author))

    def _filter_cookbooks_for_library(
        self,
        cookbooks: list[CookbookItem],
        include_collection_items: bool,
    ) -> list[CookbookItem]:
        if include_collection_items:
            return cookbooks
        return [cookbook for cookbook in cookbooks if not cookbook.collection_slug]

    def _extract_cookbook_cover(
        self,
        filename: str,
        file_bytes: bytes,
    ) -> tuple[bytes, str, str] | None:
        suffix = Path(filename).suffix.lower()
        if suffix == ".epub":
            return self._extract_epub_cover(file_bytes)
        if suffix == ".pdf":
            return self._extract_pdf_cover(file_bytes)
        return None

    def _extract_cookbook_metadata(self, filename: str, file_bytes: bytes) -> dict[str, str]:
        suffix = Path(filename).suffix.lower()
        metadata: dict[str, str] = {}
        if suffix == ".epub":
            metadata = self._extract_epub_metadata(file_bytes)
        elif suffix == ".pdf":
            metadata = self._extract_pdf_metadata(file_bytes)

        if not metadata.get("title"):
            metadata["title"] = self._display_title(filename)
        return {key: value for key, value in metadata.items() if value}

    def _extract_epub_metadata(self, file_bytes: bytes) -> dict[str, str]:
        with tempfile.NamedTemporaryFile(suffix=".epub") as handle:
            handle.write(file_bytes)
            handle.flush()
            book = epub.read_epub(handle.name)

        title = self._first_epub_metadata_value(book, "title")
        author = self._first_epub_metadata_value(book, "creator")
        published_at = self._normalize_published_at(self._first_epub_metadata_value(book, "date"))
        subjects = [value for value in self._epub_metadata_values(book, "subject") if value]
        cuisine = self._derive_cuisine(subjects)
        return {
            "title": self._clean_metadata_text(title),
            "author": self._clean_metadata_text(author),
            "cuisine": self._clean_metadata_text(cuisine),
            "published_at": published_at,
        }

    def _extract_pdf_metadata(self, file_bytes: bytes) -> dict[str, str]:
        document = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            metadata = document.metadata or {}
        finally:
            document.close()

        title = self._clean_metadata_text(metadata.get("title"))
        author = self._clean_metadata_text(metadata.get("author"))
        published_at = self._normalize_published_at(
            metadata.get("creationDate") or metadata.get("modDate") or metadata.get("subject")
        )
        cuisine = self._derive_cuisine(
            [
                metadata.get("subject") or "",
                metadata.get("keywords") or "",
            ]
        )
        return {
            "title": title,
            "author": author,
            "cuisine": self._clean_metadata_text(cuisine),
            "published_at": published_at,
        }

    def _epub_metadata_values(self, book: epub.EpubBook, name: str) -> list[str]:
        values: list[str] = []
        for value, _attrs in book.get_metadata("DC", name):
            if value:
                values.append(str(value))
        return values

    def _first_epub_metadata_value(self, book: epub.EpubBook, name: str) -> str:
        values = self._epub_metadata_values(book, name)
        return values[0] if values else ""

    def _extract_epub_cover(self, file_bytes: bytes) -> tuple[bytes, str, str] | None:
        with tempfile.NamedTemporaryFile(suffix=".epub") as handle:
            handle.write(file_bytes)
            handle.flush()
            book = epub.read_epub(handle.name)

        preferred: Any | None = None
        fallback: Any | None = None
        for item in book.get_items_of_type(ITEM_IMAGE):
            fallback = fallback or item
            name = ((getattr(item, "file_name", None) or item.get_name()) or "").lower()
            item_id = ""
            get_id = getattr(item, "get_id", None)
            if callable(get_id):
                item_id = (get_id() or "").lower()
            if "cover" in name or "cover" in item_id:
                preferred = item
                break

        image_item = preferred or fallback
        if not image_item:
            return None

        image_bytes = image_item.get_content()
        name = getattr(image_item, "file_name", None) or image_item.get_name()
        content_type = getattr(image_item, "media_type", None) or mimetypes.guess_type(name)[0] or "image/jpeg"
        extension = Path(name).suffix.lower().lstrip(".") or self._extension_for_content_type(content_type)
        return image_bytes, content_type, extension

    def _extract_pdf_cover(self, file_bytes: bytes) -> tuple[bytes, str, str] | None:
        document = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            if document.page_count == 0:
                return None
            first_page = document.load_page(0)
            pixmap = first_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            return pixmap.tobytes("png"), "image/png", "png"
        finally:
            document.close()

    def _extension_for_content_type(self, content_type: str) -> str:
        guessed = mimetypes.guess_extension(content_type) or ""
        return guessed.lstrip(".") or "jpg"

    def _check_minio(self) -> DatastoreStatus:
        try:
            self.minio.bucket_exists(self.settings.minio_bucket_name)
            return DatastoreStatus(
                name="MinIO",
                ok=True,
                detail=f"bucket {self.settings.minio_bucket_name}",
            )
        except Exception as exc:
            return DatastoreStatus(name="MinIO", ok=False, detail=str(exc))

    def _check_redis(self) -> DatastoreStatus:
        try:
            self.redis.ping()
            return DatastoreStatus(
                name="Redis",
                ok=True,
                detail=f"prefix {self.settings.redis_key_prefix}:*",
            )
        except Exception as exc:
            return DatastoreStatus(name="Redis", ok=False, detail=str(exc))

    def _check_qdrant(self) -> DatastoreStatus:
        try:
            self.qdrant.get_collections()
            return DatastoreStatus(
                name="Qdrant",
                ok=True,
                detail=f"collection {self.settings.qdrant_recipe_collection}",
            )
        except Exception as exc:
            return DatastoreStatus(name="Qdrant", ok=False, detail=str(exc))

    def _check_openai(self) -> DatastoreStatus:
        if not self.settings.openai_api_key:
            return DatastoreStatus(name="OpenAI", ok=False, detail="OPENAI_API_KEY is not configured")
        return DatastoreStatus(
            name="OpenAI",
            ok=True,
            detail=f"model {self.settings.openai_recipe_model}",
        )

    def _build_object_key(self, cookbook_id: str, filename: str) -> str:
        now = datetime.now(UTC)
        safe_name = self._safe_filename(filename)
        return (
            f"{self.settings.upload_prefix}/"
            f"{now:%Y/%m/%d}/"
            f"{cookbook_id}/"
            f"{safe_name}"
        )

    def _safe_filename(self, filename: str) -> str:
        sanitized = FILENAME_SANITIZER.sub("-", filename.strip())
        return sanitized.strip("-") or "untitled"

    def _display_title(self, filename: str) -> str:
        stem = Path(filename).stem.replace("_", " ").replace("-", " ")
        stem = self._strip_library_suffixes(stem)
        return re.sub(r"\s+", " ", stem).strip().title() or "Untitled Cookbook"

    def _clean_metadata_text(self, value: str | None) -> str:
        cleaned = self._strip_library_suffixes((value or "").strip())
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" -_,;:/") if cleaned else ""

    def _strip_library_suffixes(self, value: str) -> str:
        cleaned = value
        cleaned = re.sub(
            r"[\(\[][^)\]]*(?:z[\s-]*library(?:\.sk)?|1lib(?:\.sk)?|z[\s-]*lib(?:\.sk|rary)?)"
            r"[^)\]]*[\)\]]",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*[-–—,]?\s*(?:z[\s-]*library(?:\.sk)?|1lib(?:\.sk)?|z[\s-]*lib(?:\.sk|rary)?).*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", cleaned).strip()

    def _derive_cuisine(self, candidates: list[str]) -> str:
        for candidate in candidates:
            cleaned = self._clean_metadata_text(candidate)
            if cleaned:
                return cleaned.title()
        return ""

    def _normalize_published_at(self, value: str | None) -> str:
        cleaned = self._clean_metadata_text(value)
        if not cleaned:
            return ""

        cleaned = cleaned.replace("D:", "")
        match = re.search(r"(19|20)\d{2}(?:[-/]?\d{2})?(?:[-/]?\d{2})?", cleaned)
        if not match:
            return ""

        token = match.group(0).replace("/", "-")
        if len(token) == 8 and "-" not in token:
            return f"{token[:4]}-{token[4:6]}-{token[6:8]}"
        if len(token) == 6 and "-" not in token:
            return f"{token[:4]}-{token[4:6]}"
        return token

    def _sortable_text(self, value: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

    def _text_sort_key(self, primary: str | None, secondary: str | None = None) -> tuple[int, str, str]:
        primary_value = self._sortable_text(primary)
        secondary_value = self._sortable_text(secondary)
        return (0 if primary_value else 1, primary_value or secondary_value, secondary_value)

    def _published_sort_key(self, value: str | None) -> tuple[int, str]:
        normalized = self._normalize_published_at(value)
        return (1 if normalized else 0, normalized)

    def _normalize_ingredient(self, value: str) -> str:
        return canonicalize_ingredient_name(value)

    def _get_recipes_by_ids(self, recipe_ids: list[str]) -> list[RecipeRecord]:
        if not recipe_ids:
            return []

        payloads = self.redis.mget([self.settings.recipe_key(recipe_id) for recipe_id in recipe_ids])
        favorite_scores = self.redis.zmscore(self.settings.favorite_recipe_index_key, recipe_ids)

        recipes: list[RecipeRecord] = []
        for recipe_id, payload, favorite_score in zip(recipe_ids, payloads, favorite_scores, strict=False):
            if not payload:
                continue
            recipe = self._hydrate_recipe_payload(
                payload,
                recipe_id=recipe_id,
                is_favorite=favorite_score is not None,
            )
            recipes.append(recipe)
        return recipes

    def _get_recipe_references_by_ids(self, recipe_ids: list[str]) -> list[RecipeReferenceRecord]:
        if not recipe_ids:
            return []

        reference_payloads = self.redis.mget(
            [self.settings.recipe_reference_key(recipe_id) for recipe_id in recipe_ids]
        )
        missing_ids = [
            recipe_id
            for recipe_id, payload in zip(recipe_ids, reference_payloads, strict=False)
            if not payload
        ]
        missing_reference_map: dict[str, RecipeReferenceRecord] = {}

        if missing_ids:
            recipe_payloads = self.redis.mget([self.settings.recipe_key(recipe_id) for recipe_id in missing_ids])
            for recipe_id, payload in zip(missing_ids, recipe_payloads, strict=False):
                if not payload:
                    continue
                reference = self._recipe_reference_from_full_payload(payload, recipe_id=recipe_id)
                missing_reference_map[recipe_id] = reference
                self.redis.set(self.settings.recipe_reference_key(recipe_id), reference.model_dump_json())

        references: list[RecipeReferenceRecord] = []
        for recipe_id, payload in zip(recipe_ids, reference_payloads, strict=False):
            if payload:
                references.append(self._hydrate_recipe_reference_payload(payload, recipe_id=recipe_id))
                continue
            reference = missing_reference_map.get(recipe_id)
            if reference:
                references.append(reference)
        return references

    def _hydrate_recipe_payload(
        self,
        payload: str,
        *,
        recipe_id: str,
        is_favorite: bool,
    ) -> RecipeRecord:
        recipe = RecipeRecord.model_validate_json(payload)
        cleaned_cookbook_title = self._clean_metadata_text(recipe.cookbook_title)
        canonical_ingredient_names = sorted(
            {
                ingredient_index_name(ingredient)
                for ingredient in recipe.ingredients
                if ingredient_index_name(ingredient)
            }
        )
        if cleaned_cookbook_title and cleaned_cookbook_title != recipe.cookbook_title:
            recipe = recipe.model_copy(update={"cookbook_title": cleaned_cookbook_title})
        if (
            recipe.id != recipe_id
            or recipe.is_favorite != is_favorite
            or recipe.ingredient_names != canonical_ingredient_names
        ):
            recipe = recipe.model_copy(
                update={
                    "id": recipe_id,
                    "is_favorite": is_favorite,
                    "ingredient_names": canonical_ingredient_names,
                }
            )
        return recipe

    def _build_recipe_reference(self, recipe: RecipeRecord) -> RecipeReferenceRecord:
        return RecipeReferenceRecord(
            id=recipe.id,
            cookbook_id=recipe.cookbook_id,
            cookbook_title=self._clean_metadata_text(recipe.cookbook_title) or recipe.cookbook_title,
            title=recipe.title,
        )

    def _recipe_reference_from_full_payload(
        self,
        payload: str,
        *,
        recipe_id: str,
    ) -> RecipeReferenceRecord:
        data = json.loads(payload)
        cookbook_title = self._clean_metadata_text(str(data.get("cookbook_title", "")).strip())
        return RecipeReferenceRecord(
            id=recipe_id,
            cookbook_id=str(data.get("cookbook_id", "")).strip(),
            cookbook_title=cookbook_title or str(data.get("cookbook_title", "")).strip(),
            title=str(data.get("title", "")).strip(),
        )

    def _hydrate_recipe_reference_payload(
        self,
        payload: str,
        *,
        recipe_id: str,
    ) -> RecipeReferenceRecord:
        reference = RecipeReferenceRecord.model_validate_json(payload)
        cleaned_cookbook_title = self._clean_metadata_text(reference.cookbook_title)
        if cleaned_cookbook_title and cleaned_cookbook_title != reference.cookbook_title:
            reference = reference.model_copy(update={"cookbook_title": cleaned_cookbook_title})
        if reference.id != recipe_id:
            reference = reference.model_copy(update={"id": recipe_id})
        return reference

    def _normalize_ingredient_list(self, values: list[str] | None) -> list[str]:
        if not values:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            for part in (value or "").split(","):
                cleaned = self._normalize_ingredient(part)
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    normalized.append(cleaned)
        return normalized

    def _normalize_search_query(self, value: str | None) -> list[str]:
        if not value:
            return []
        cleaned = re.sub(r"[^a-z0-9\s-]", " ", value.lower())
        return [token for token in re.sub(r"\s+", " ", cleaned).strip().split(" ") if token]

    def _keyword_score(self, recipe: RecipeRecord, query_terms: list[str]) -> tuple[float, list[str]]:
        fields = {
            "title": recipe.title.lower(),
            "cookbook": recipe.cookbook_title.lower(),
            "ingredients": " ".join(recipe.ingredient_names).lower(),
            "ingredient_raw": " ".join(ingredient.raw.lower() for ingredient in recipe.ingredients),
            "method": " ".join(step.lower() for step in recipe.method_steps),
            "source": recipe.source.excerpt.lower(),
        }
        weights = {
            "title": 5.0,
            "ingredients": 4.0,
            "ingredient_raw": 3.0,
            "cookbook": 2.0,
            "method": 1.5,
            "source": 1.0,
        }

        score = 0.0
        matches: list[str] = []
        for term in query_terms:
            for field_name, field_value in fields.items():
                if term in field_value:
                    score += weights[field_name]
                    if term not in matches:
                        matches.append(term)
        return score, matches

    def _semantic_scores(
        self,
        query: str,
        candidates: list[RecipeRecord],
        *,
        limit: int,
    ) -> dict[str, float]:
        if not query.strip() or not self.settings.openai_api_key:
            return {}

        try:
            embedding_response = self._openai_client().embeddings.create(
                model=self.settings.openai_embedding_model,
                input=query.strip(),
            )
            vector = embedding_response.data[0].embedding
            query_response = self.qdrant.query_points(
                collection_name=self.settings.qdrant_recipe_collection,
                query=vector,
                limit=limit,
            )
        except Exception as exc:
            logger.warning("Semantic search unavailable: %s", exc)
            return {}

        candidate_ids = {recipe.id for recipe in candidates}
        semantic_scores: dict[str, float] = {}
        for point in getattr(query_response, "points", []):
            point_id = str(point.id)
            if point_id in candidate_ids:
                semantic_scores[point_id] = float(point.score or 0.0)
        return semantic_scores

    def _rank_map(self, score_map: dict[str, float]) -> dict[str, int]:
        ranked = sorted(score_map.items(), key=lambda item: item[1], reverse=True)
        return {recipe_id: index + 1 for index, (recipe_id, _) in enumerate(ranked)}

    def _openai_client(self) -> OpenAI:
        client_kwargs: dict[str, Any] = {"api_key": self.settings.openai_api_key}
        if self.settings.openai_base_url:
            client_kwargs["base_url"] = self.settings.openai_base_url
        return OpenAI(**client_kwargs)
