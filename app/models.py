from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ReviewStatus = Literal["pending_review", "verified", "needs_review", "rejected"]


class CookbookTocEntry(BaseModel):
    label: str
    href: str
    children: list["CookbookTocEntry"] = Field(default_factory=list)


class CookbookItem(BaseModel):
    id: str
    title: str
    author: str | None = None
    cuisine: str | None = None
    published_at: str | None = None
    collection_slug: str | None = None
    filename: str
    object_key: str
    size_bytes: int
    content_type: str
    uploaded_at: str
    status: str
    recipe_count: int = 0
    needs_review_count: int = 0
    extract_attempted_at: str | None = None
    extract_completed_at: str | None = None
    extract_error: str | None = None
    cover_image_key: str | None = None
    cover_image_content_type: str | None = None
    cover_extract_attempted_at: str | None = None
    metadata_extract_attempted_at: str | None = None
    table_of_contents: list[CookbookTocEntry] = Field(default_factory=list)


class DatastoreStatus(BaseModel):
    name: str
    ok: bool
    detail: str


class IngredientRecord(BaseModel):
    raw: str
    normalized_name: str
    quantity: str | None = None
    unit: str | None = None
    item: str | None = None
    preparation: str | None = None
    optional: bool = False


class RecipeImageRecord(BaseModel):
    object_key: str
    content_type: str
    source_ref: str | None = None


class RecipeSourceRecord(BaseModel):
    object_key: str
    format: str
    chapter_title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    anchor: str | None = None
    excerpt: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecipeExtractionRecord(BaseModel):
    model: str
    confidence: float = 0
    notes: list[str] = Field(default_factory=list)
    extracted_at: str
    needs_review_reasons: list[str] = Field(default_factory=list)


class RecipeReviewRecord(BaseModel):
    status: ReviewStatus = "pending_review"
    note: str | None = None
    reviewed_at: str | None = None


class RecipeRecord(BaseModel):
    id: str
    cookbook_id: str
    cookbook_title: str
    title: str
    ingredients: list[IngredientRecord]
    ingredient_names: list[str]
    method_steps: list[str]
    images: list[RecipeImageRecord] = Field(default_factory=list)
    source: RecipeSourceRecord
    extraction: RecipeExtractionRecord
    review: RecipeReviewRecord
    is_favorite: bool = False


class IngredientIndexItem(BaseModel):
    name: str
    recipe_count: int


class RecipeCollectionItem(BaseModel):
    slug: str
    title: str
    description: str
    recipe_count: int = 0
    allow_upload: bool = True


class CookbookMetadataUpdateRequest(BaseModel):
    title: str | None = None
    author: str | None = None
    cuisine: str | None = None
    published_at: str | None = None


class ReviewUpdateRequest(BaseModel):
    status: ReviewStatus
    note: str | None = None


class SearchResultRecord(BaseModel):
    recipe: RecipeRecord
    score: float
    keyword_score: float = 0
    semantic_score: float = 0
    matched_terms: list[str] = Field(default_factory=list)
    matched_ingredient: str | None = None


class SearchAnswerRecord(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)


CookbookTocEntry.model_rebuild()
