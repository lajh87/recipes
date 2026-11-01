"""Read-only Home Assistant API, independent of recipe/search services."""
from __future__ import annotations

from datetime import UTC, datetime
import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.config import Settings, get_settings
from app.meal_plan_voice import MealPlanVoiceResponse, summarize_meal_plan

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Home Assistant"])
bearer = HTTPBearer(auto_error=False)


def authenticate_home_assistant(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Settings:
    expected = settings.home_assistant_api_key
    if not expected.strip():
        raise HTTPException(503, "Home Assistant API authentication is not configured.")
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(), expected.encode(),
    ):
        raise HTTPException(401, "Invalid API key.", headers={"WWW-Authenticate": "Bearer"})
    return settings


def read_meal_plan_payload(settings: Settings) -> str | None:
    with Redis.from_url(
        settings.redis_url, decode_responses=True,
        socket_connect_timeout=1, socket_timeout=1,
        retry=Retry(NoBackoff(), 0),
    ) as client:
        return client.get(settings.meal_plan_key)


@router.get(
    "/api/integrations/home-assistant/meal-plan",
    response_model=MealPlanVoiceResponse,
    responses={401: {"description": "Invalid bearer key"}, 503: {"description": "Service unavailable"}},
)
def get_voice_meal_plan(
    response: Response,
    settings: Annotated[Settings, Depends(authenticate_home_assistant)],
) -> MealPlanVoiceResponse:
    """Read current dinner answers without importing, initialising or saving plans."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return summarize_meal_plan(
            read_meal_plan_payload(settings), now=datetime.now(UTC), timezone=settings.meal_plan_timezone,
        )
    except Exception as exc:
        logger.warning("Meal-plan voice read failed (%s)", type(exc).__name__)
        raise HTTPException(
            503, "Meal-plan service temporarily unavailable.", headers={"Cache-Control": "no-store"},
        ) from exc
