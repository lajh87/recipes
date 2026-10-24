"""Restricted GPT Actions API; deliberately does not import the cookbook web app."""
from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.config import get_settings
from app.models import IngredientRecord
from app import recipe_tools

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    expected = get_settings().gpt_api_key
    if not expected:
        raise HTTPException(503, "GPT API authentication is not configured.")
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(), expected.encode()
    ):
        raise HTTPException(401, "Invalid API key.", headers={"WWW-Authenticate": "Bearer"})


class SearchItem(BaseModel):
    id: str
    title: str
    cookbook: str
    ingredient_names: list[str]
    recipe_path: str
    matched_terms: list[str]
    score: float


class SearchResponse(BaseModel):
    query: str
    ingredients: list[str]
    count: int
    items: list[SearchItem]


class Source(BaseModel):
    chapter_title: str | None
    page_start: int | None
    page_end: int | None


class RecipeResponse(BaseModel):
    id: str
    title: str
    cookbook: str
    ingredients: list[IngredientRecord]
    method_steps: list[str]
    metadata: dict[str, str | int | float]
    source: Source
    review_status: str
    warnings: list[str]
    recipe_path: str


class Meal(BaseModel):
    weekday: str
    meal: str
    title: str
    recipe_id: str | None


class WeekResponse(BaseModel):
    week_id: str
    week_title: str
    start_on: str
    notes: str
    meal_plan_path: str
    meals: list[Meal]


class WeekUpdate(BaseModel):
    meals: list[recipe_tools.PlannedMeal] = Field(min_length=1, max_length=50)
    notes: str = Field(description="Complete week notes; retained notes must be included when replacing a week.")


class WeekCreate(WeekUpdate):
    start_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="First date of the seven-day plan, YYYY-MM-DD.")
    notes: str = ""


class ErrorResponse(BaseModel):
    detail: str


ERRORS = {code: {"model": ErrorResponse} for code in (401, 404, 409, 503)}
app = FastAPI(
    title="Heley Family Cookbook GPT API", version="1.0.0",
    description="Search the private cookbook, retrieve complete recipes, and read or save weekly meal plans.",
    docs_url=None, redoc_url=None,
    servers=[{"url": "https://recipes-open.heley.uk"}],
)


def invoke(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except ValueError as exc:
        message = str(exc)
        status = 409 if message.startswith("A meal-plan week already") else (
            404 if message.startswith(("Recipe not found:", "No meal-plan week")) else 422
        )
        raise HTTPException(status, message) from exc
    except Exception as exc:
        logger.error("GPT operation %s failed (%s)", getattr(operation, "__name__", "unknown"), type(exc).__name__)
        raise HTTPException(503, "Cookbook service temporarily unavailable.") from exc


@app.get("/healthz", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.get("/api/search", operation_id="search_recipes", response_model=SearchResponse,
         dependencies=[Depends(authenticate)], responses=ERRORS)
def search_recipes(query: str = "", ingredients: Annotated[list[str] | None, Query()] = None,
                   limit: int = Query(10, ge=1, le=20)):
    """Search by keywords and/or required ingredients (all ingredients must match)."""
    return invoke(recipe_tools.search_recipes, query, ingredients, limit)


@app.get("/api/recipes/{recipe_id}", operation_id="get_recipe", response_model=RecipeResponse,
         dependencies=[Depends(authenticate)], responses=ERRORS)
def get_recipe(recipe_id: str):
    """Fetch full details before summarising, checking dietary suitability or calculating shopping quantities."""
    return invoke(recipe_tools.get_recipe, recipe_id)


@app.get("/api/meal-plans/{start_on}", operation_id="get_meal_plan", response_model=WeekResponse,
         dependencies=[Depends(authenticate)], responses=ERRORS)
def get_meal_plan(start_on: str):
    """Read a saved week without changes. A missing week returns 404."""
    return invoke(recipe_tools.get_meal_plan, start_on)


@app.post("/api/meal-plans", operation_id="create_meal_plan", response_model=WeekResponse,
          status_code=201, dependencies=[Depends(authenticate)], responses=ERRORS,
          openapi_extra={"x-openai-isConsequential": True})
def create_meal_plan(payload: WeekCreate):
    """Save a draft only when requested. Returns 409 if this week already exists; never auto-replace it."""
    return invoke(recipe_tools.create_meal_plan, payload.start_on, payload.meals, payload.notes)


@app.put("/api/meal-plans/{start_on}", operation_id="update_meal_plan", response_model=WeekResponse,
         dependencies=[Depends(authenticate)], responses=ERRORS,
         openapi_extra={"x-openai-isConsequential": True})
def update_meal_plan(start_on: str, payload: WeekUpdate):
    """Replace all meals and notes only on an explicit replacement request. Read the existing week first."""
    return invoke(recipe_tools.update_meal_plan, start_on, payload.meals, payload.notes)
