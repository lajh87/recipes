"""Shared recipe operations for MCP and the dedicated GPT API."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_settings
from app.meal_plan import (
    DEFAULT_IMPORT_WEEK_LIMIT,
    REDIS_MEAL_PLAN_SOURCE,
    create_blank_row,
    create_blank_week,
    load_or_import_meal_plan,
    save_meal_plan,
    sort_week_entries,
    meal_plan_from_dict,
)
from app.repository import LibraryRepository


BASE_DIR = Path(__file__).resolve().parent.parent


class PlannedMeal(BaseModel):
    weekday: Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    meal: Literal["Breakfast", "Lunch", "Dinner"] = "Dinner"
    recipe_id: str | None = Field(default=None, description="ID returned by search_recipes.")
    title: str = Field(default="", description="Required for a meal without a recipe_id.")


def get_recipe(recipe_id: str) -> dict:
    """Retrieve ingredients, method, source attribution and available metadata."""
    repository = LibraryRepository.from_settings(get_settings())
    try:
        recipe = repository.get_recipe(recipe_id)
        if recipe is None:
            raise ValueError(f"Recipe not found: {recipe_id}")
        metadata_keys = {
            "serves", "yield", "makes", "active_time", "prep_time", "cook_time",
            "total_time", "author", "description", "headnote", "source_url",
        }
        return {
            "id": recipe.id, "title": recipe.title,
            "cookbook": recipe.cookbook_title,
            "ingredients": [item.model_dump() for item in recipe.ingredients],
            "method_steps": recipe.method_steps,
            "metadata": {key: value for key, value in recipe.source.metadata.items()
                         if key in metadata_keys and isinstance(value, (str, int, float))},
            "source": {"chapter_title": recipe.source.chapter_title,
                       "page_start": recipe.source.page_start,
                       "page_end": recipe.source.page_end},
            "review_status": recipe.review.status,
            "warnings": recipe.extraction.needs_review_reasons,
            "recipe_path": f"/recipes/{recipe.id}",
        }
    finally:
        repository.close()


def get_meal_plan(start_on: str) -> dict:
    """Read one stored week without importing, seeding or changing any data."""
    if date.fromisoformat(start_on).isoformat() != start_on:
        raise ValueError("start_on must be a valid ISO date (YYYY-MM-DD).")
    repository = LibraryRepository.from_settings(get_settings())
    try:
        payload = repository.load_meal_plan_payload()
        document = meal_plan_from_dict(json.loads(payload or '{}'), BASE_DIR)
        week = next((week for week in document.weeks if week.start_on == start_on), None)
        if week is None:
            raise ValueError(f"No meal-plan week starts on {start_on}.")
        return {
            "week_id": week.id, "week_title": week.title, "start_on": week.start_on,
            "notes": week.notes, "meal_plan_path": "/meal-plan",
            "meals": [{"weekday": row.weekday, "meal": row.meal,
                       "title": row.title, "recipe_id": row.recipe_id}
                      for row in week.entries],
        }
    finally:
        repository.close()


def search_recipes(
    query: str = "",
    ingredients: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """Search recipes by keywords and/or required ingredients.

    All supplied ingredients must match. Keyword searches use the library's
    existing keyword and, when configured, semantic ranking. Returns concise
    recipe matches with IDs that can be opened at /recipes/{id} in the web app.
    """
    cleaned_query = query.strip()
    cleaned_ingredients = [value.strip() for value in (ingredients or []) if value.strip()]
    if not cleaned_query and not cleaned_ingredients:
        raise ValueError("Provide a query or at least one ingredient.")
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20.")

    repository = LibraryRepository.from_settings(get_settings())
    try:
        results = repository.search_recipes(
            query=cleaned_query or None,
            ingredients=cleaned_ingredients,
            limit=limit,
        )
    finally:
        repository.close()

    return {
        "query": cleaned_query,
        "ingredients": cleaned_ingredients,
        "count": len(results),
        "items": [
            {
                "id": result.recipe.id,
                "title": result.recipe.title,
                "cookbook": result.recipe.cookbook_title,
                "ingredient_names": result.recipe.ingredient_names,
                "recipe_path": f"/recipes/{result.recipe.id}",
                "matched_terms": result.matched_terms,
                "score": result.score,
            }
            for result in results
        ],
    }


def create_meal_plan(
    start_on: str,
    meals: list[PlannedMeal],
    notes: str = "",
) -> dict:
    """Create a dated week in the meal planner without replacing other weeks.

    Use an ISO date (YYYY-MM-DD) for the first day of the week. Each meal must
    include a recipe_id from search_recipes or a free-text title. A recipe_id
    links the meal to the library recipe and uses its current title. The tool
    rejects an existing week with the same start date.
    """
    try:
        parsed_start = date.fromisoformat(start_on)
    except ValueError as exc:
        raise ValueError("start_on must be a valid ISO date (YYYY-MM-DD).") from exc
    if parsed_start.isoformat() != start_on:
        raise ValueError("start_on must be a valid ISO date (YYYY-MM-DD).")
    if not 1 <= len(meals) <= 50:
        raise ValueError("Provide between 1 and 50 meals.")

    repository = LibraryRepository.from_settings(get_settings())
    try:
        references = repository.list_recipe_references()
        recipe_map = {recipe.id: recipe for recipe in references}
        for meal in meals:
            if meal.recipe_id:
                if meal.recipe_id not in recipe_map:
                    raise ValueError(f"Recipe not found: {meal.recipe_id}")
            elif not meal.title.strip():
                raise ValueError("Each meal needs a recipe_id or a title.")

        document = load_or_import_meal_plan(
            BASE_DIR,
            references,
            week_limit=DEFAULT_IMPORT_WEEK_LIMIT,
            source_path=REDIS_MEAL_PLAN_SOURCE,
            load_payload=repository.load_meal_plan_payload,
            save_payload=repository.save_meal_plan_payload,
        )
        if any(week.start_on == start_on for week in document.weeks):
            raise ValueError(f"A meal-plan week already starts on {start_on}.")

        week = create_blank_week(start_on)
        week.entries = []
        week.notes = notes.strip()
        for meal in meals:
            row = create_blank_row(weekday=meal.weekday, meal=meal.meal)
            recipe = recipe_map.get(meal.recipe_id or "")
            row.recipe_id = recipe.id if recipe else None
            row.recipe = recipe
            row.title = recipe.title if recipe else meal.title.strip()
            week.entries.append(row)
        sort_week_entries(week)

        # The initial empty week is a UI placeholder, not a saved plan.
        document.weeks = [
            existing
            for existing in document.weeks
            if existing.start_on
            or existing.legacy_title
            or existing.notes
            or existing.shopping_checked_items
            or any(
                row.title or row.recipe_id or row.weekday or row.meal or row.completed
                for row in existing.entries
            )
        ]
        document.weeks.insert(0, week)
        save_meal_plan(BASE_DIR, document, save_payload=repository.save_meal_plan_payload)
    finally:
        repository.close()

    return {
        "week_id": week.id,
        "week_title": week.title,
        "start_on": week.start_on,
        "meal_plan_path": "/meal-plan",
        "notes": week.notes,
        "meals": [
            {
                "weekday": row.weekday,
                "meal": row.meal,
                "title": row.title,
                "recipe_id": row.recipe_id,
            }
            for row in week.entries
        ],
    }


def update_meal_plan(
    start_on: str,
    meals: list[PlannedMeal],
    notes: str,
) -> dict:
    """Replace the meals and notes for an existing dated week.

    Recipe IDs must exist in the library. Other weeks are preserved, as are
    this week's ID and checked shopping items.
    """
    try:
        parsed_start = date.fromisoformat(start_on)
    except ValueError as exc:
        raise ValueError("start_on must be a valid ISO date (YYYY-MM-DD).") from exc
    if parsed_start.isoformat() != start_on:
        raise ValueError("start_on must be a valid ISO date (YYYY-MM-DD).")
    if not 1 <= len(meals) <= 50:
        raise ValueError("Provide between 1 and 50 meals.")

    repository = LibraryRepository.from_settings(get_settings())
    try:
        references = repository.list_recipe_references()
        recipe_map = {recipe.id: recipe for recipe in references}
        for meal in meals:
            if meal.recipe_id:
                if meal.recipe_id not in recipe_map:
                    raise ValueError(f"Recipe not found: {meal.recipe_id}")
            elif not meal.title.strip():
                raise ValueError("Each meal needs a recipe_id or a title.")

        document = load_or_import_meal_plan(
            BASE_DIR,
            references,
            week_limit=DEFAULT_IMPORT_WEEK_LIMIT,
            source_path=REDIS_MEAL_PLAN_SOURCE,
            load_payload=repository.load_meal_plan_payload,
            save_payload=repository.save_meal_plan_payload,
        )
        week = next((item for item in document.weeks if item.start_on == start_on), None)
        if week is None:
            raise ValueError(f"No meal-plan week starts on {start_on}.")

        week.entries = []
        week.notes = notes.strip()
        for meal in meals:
            row = create_blank_row(weekday=meal.weekday, meal=meal.meal)
            recipe = recipe_map.get(meal.recipe_id or "")
            row.recipe_id = recipe.id if recipe else None
            row.recipe = recipe
            row.title = recipe.title if recipe else meal.title.strip()
            week.entries.append(row)
        sort_week_entries(week)
        save_meal_plan(BASE_DIR, document, save_payload=repository.save_meal_plan_payload)
    finally:
        repository.close()

    return {
        "week_id": week.id,
        "week_title": week.title,
        "start_on": week.start_on,
        "meal_plan_path": "/meal-plan",
        "notes": week.notes,
        "meals": [
            {
                "weekday": row.weekday,
                "meal": row.meal,
                "title": row.title,
                "recipe_id": row.recipe_id,
            }
            for row in week.entries
        ],
    }
