from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Heley Family Cookbook"
    app_env: str = "development"
    app_port: int = 8080

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket_name: str = "recipe-library-ebooks"

    redis_url: str = "redis://redis:6379/5"
    redis_key_prefix: str = "recipes"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_cookbook_collection: str = "recipes-cookbooks"
    qdrant_recipe_collection: str = "recipes-recipe-chunks"
    qdrant_vector_size: int = 1536

    openai_api_key: str = ""
    openai_base_url: Optional[str] = None
    openai_recipe_model: str = "gpt-5.4-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_search_model: str = "gpt-5.4-mini"

    upload_prefix: str = "ebooks"
    derived_prefix: str = "derived"
    max_upload_mb: int = 500
    allowed_extensions_raw: str = Field(default="pdf,epub,mobi,azw3", alias="ALLOWED_EXTENSIONS")

    @property
    def allowed_extensions(self) -> set[str]:
        return {
            extension.strip().lower()
            for extension in self.allowed_extensions_raw.split(",")
            if extension.strip()
        }

    @property
    def schema_version_key(self) -> str:
        return f"{self.redis_key_prefix}:schema:version"

    @property
    def schema_metadata_key(self) -> str:
        return f"{self.redis_key_prefix}:schema:metadata"

    @property
    def extract_queue_key(self) -> str:
        return f"{self.redis_key_prefix}:jobs:extract"

    @property
    def cookbook_index_key(self) -> str:
        return f"{self.redis_key_prefix}:cookbooks:index"

    def cookbook_key(self, cookbook_id: str) -> str:
        return f"{self.redis_key_prefix}:cookbooks:{cookbook_id}"

    def cookbook_recipe_index_key(self, cookbook_id: str) -> str:
        return f"{self.redis_key_prefix}:cookbooks:{cookbook_id}:recipes"

    def recipe_key(self, recipe_id: str) -> str:
        return f"{self.redis_key_prefix}:recipes:{recipe_id}"

    def recipe_reference_key(self, recipe_id: str) -> str:
        return f"{self.redis_key_prefix}:recipes:refs:{recipe_id}"

    @property
    def favorite_recipe_index_key(self) -> str:
        return f"{self.redis_key_prefix}:recipes:favorites"

    @property
    def want_to_try_recipe_index_key(self) -> str:
        return f"{self.redis_key_prefix}:recipes:want-to-try"

    def ingredient_key(self, ingredient_name: str) -> str:
        return f"{self.redis_key_prefix}:ingredients:{ingredient_name}"

    @property
    def ingredient_index_prefix(self) -> str:
        return f"{self.redis_key_prefix}:ingredients:"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
