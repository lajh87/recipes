"""Side-effect-free dinner summaries of the saved meal plan."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.meal_plan import MealPlanRow, WEEKDAY_LABELS, meal_plan_from_dict


class VoiceMeal(BaseModel):
    id: str
    title: str
    weekday: str | None
    date: date | None
    completed: bool


class VoiceWeek(BaseModel):
    id: str
    start_on: date
    end_on: date


class MealPlanVoiceResponse(BaseModel):
    status: Literal["ok", "no_current_plan"]
    local_date: date
    timezone: str
    week: VoiceWeek | None
    tonight: list[VoiceMeal]
    remaining: list[VoiceMeal]
    tonight_speech: str
    remaining_speech: str


def _validate_rows(rows: object, *, legacy: bool = False) -> None:
    if not isinstance(rows, list):
        raise ValueError("Invalid saved meal-plan rows.")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid saved meal-plan row.")
        fields = ("title", "meal_label", "meal_slug") if legacy else ("title", "weekday", "meal")
        if any(key in row and not isinstance(row[key], str) for key in fields):
            raise ValueError("Invalid saved meal-plan text.")
        if "completed" in row and not isinstance(row["completed"], bool):
            raise ValueError("Invalid saved meal-plan completion flag.")
        if "recipe_id" in row and row["recipe_id"] is not None and not isinstance(row["recipe_id"], str):
            raise ValueError("Invalid saved recipe reference.")


def _decode_payload(payload: str) -> dict:
    """Reject corrupt data rather than quietly announcing an empty plan."""
    decoded = json.loads(payload)
    if not isinstance(decoded, dict) or not isinstance(decoded.get("weeks"), list):
        raise ValueError("Invalid saved meal-plan document.")
    for week in decoded["weeks"]:
        if not isinstance(week, dict):
            raise ValueError("Invalid saved meal-plan week.")
        start = week.get("start_on", "")
        if not isinstance(start, str) or (start and date.fromisoformat(start).isoformat() != start):
            raise ValueError("Invalid saved meal-plan start date.")
        if "entries" in week:
            _validate_rows(week["entries"])
        elif "days" in week:
            if not isinstance(week["days"], list):
                raise ValueError("Invalid saved meal-plan days.")
            for day in week["days"]:
                if not isinstance(day, dict):
                    raise ValueError("Invalid saved meal-plan day.")
                _validate_rows(day.get("meals", []), legacy=True)
    return decoded


def _voice_meal(row: MealPlanRow, start: date) -> VoiceMeal:
    weekday = row.weekday if row.weekday in WEEKDAY_LABELS else None
    scheduled = (
        start + timedelta(days=(WEEKDAY_LABELS.index(weekday) - start.weekday()) % 7)
        if weekday else None
    )
    return VoiceMeal(
        id=row.id, title=row.title, weekday=weekday, date=scheduled,
        completed=row.completed,
    )


def _join_titles(titles: list[str]) -> str:
    # Plain text throughout: recipe titles are never interpreted as SSML or templates.
    if len(titles) < 2:
        return "".join(titles)
    return ", ".join(titles[:-1]) + " and " + titles[-1]


def summarize_meal_plan(
    payload: str | None, *, now: datetime, timezone: str = "Europe/London",
) -> MealPlanVoiceResponse:
    if now.utcoffset() is None:
        raise ValueError("An aware datetime is required.")
    today = now.astimezone(ZoneInfo(timezone)).date()
    result = MealPlanVoiceResponse(
        status="no_current_plan", local_date=today, timezone=timezone,
        week=None, tonight=[], remaining=[],
        tonight_speech="There is no saved meal plan covering today.",
        remaining_speech="There is no saved meal plan covering today.",
    )
    if payload is None:
        return result
    document = meal_plan_from_dict(_decode_payload(payload), Path(__file__).resolve().parent.parent)
    candidates = [
        week for week in document.weeks
        if week.start_on and 0 <= (today - date.fromisoformat(week.start_on)).days < 7
    ]
    if not candidates:
        return result
    # max preserves stored order for equal start dates.
    week = max(candidates, key=lambda item: item.start_on)
    start = date.fromisoformat(week.start_on)
    dinners = [
        _voice_meal(row, start) for row in week.entries
        if row.title.strip() and row.meal in ("", "Dinner")
    ]
    result.status = "ok"
    result.week = VoiceWeek(id=week.id, start_on=start, end_on=start + timedelta(days=6))
    result.tonight = [meal for meal in dinners if meal.date == today]
    result.remaining = [meal for meal in dinners if not meal.completed]
    result.tonight_speech = "There is no dinner assigned to tonight on your meal plan."
    if result.tonight:
        titles = [meal.title + (", already marked done" if meal.completed else "") for meal in result.tonight]
        result.tonight_speech = f"Tonight's dinner is {_join_titles(titles)}."
    result.remaining_speech = "There are no dinners left on this week's meal plan."
    if result.remaining:
        parts = ["The dinners left on this week's meal plan are:"]
        for weekday in WEEKDAY_LABELS[start.weekday():] + WEEKDAY_LABELS[:start.weekday()]:
            titles = [meal.title for meal in result.remaining if meal.weekday == weekday]
            if titles:
                parts.append(f"{weekday}: {_join_titles(titles)}.")
        unassigned = [meal.title for meal in result.remaining if meal.weekday is None]
        if unassigned:
            parts.append(f"Other dinners this week: {_join_titles(unassigned)}.")
        result.remaining_speech = " ".join(parts)
    elif not dinners:
        result.remaining_speech = "There are no dinners planned for this week."
    return result
