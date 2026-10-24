import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.gpt_api import app
from app import recipe_tools

client = TestClient(app)
HEADERS = {"Authorization": "Bearer test-secret"}


@pytest.fixture(autouse=True)
def settings():
    with patch("app.gpt_api.get_settings", return_value=SimpleNamespace(gpt_api_key="test-secret")):
        yield


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/search?query=pasta", None),
    ("GET", "/api/recipes/r1", None),
    ("GET", "/api/meal-plans/2026-10-26", None),
    ("POST", "/api/meal-plans", {"start_on": "2026-10-26", "meals": [{"weekday": "Monday", "title": "Soup"}]}),
    ("PUT", "/api/meal-plans/2026-10-26", {"meals": [{"weekday": "Monday", "title": "Soup"}], "notes": ""}),
])
def test_every_data_operation_requires_key(method, path, body):
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        assert client.request(method, path, json=body, headers=headers).status_code == 401


def test_unconfigured_key_fails_closed():
    with patch("app.gpt_api.get_settings", return_value=SimpleNamespace(gpt_api_key="")):
        assert client.get("/api/search?query=pasta", headers=HEADERS).status_code == 503


def test_schema_and_isolation():
    schema = client.get("/openapi.json").json()
    assert schema["servers"][0]["url"] == "https://recipes-open.heley.uk"
    assert len(schema["paths"]) == 4
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            assert operation["security"]
            if method in ("post", "put"):
                assert operation["x-openai-isConsequential"] is True
    for path in ("/", "/docs", "/meal-plan", "/api/cookbooks", "/api/recipe-imports/preview"):
        assert client.get(path, headers=HEADERS).status_code == 404


def test_search_validation_and_success():
    with patch("app.recipe_tools.LibraryRepository") as repo:
        assert client.get("/api/search", headers=HEADERS).status_code == 422
        assert client.get("/api/search?query=pasta&limit=21", headers=HEADERS).status_code == 422
        repo.from_settings.assert_not_called()
    with patch("app.gpt_api.recipe_tools.search_recipes", return_value={
        "query": "pasta", "ingredients": [], "count": 0, "items": []
    }):
        assert client.get("/api/search?query=pasta", headers=HEADERS).json()["count"] == 0


def test_errors_do_not_expose_backend_secrets():
    with patch("app.gpt_api.recipe_tools.get_recipe", side_effect=RuntimeError("redis://secret")):
        response = client.get("/api/recipes/r1", headers=HEADERS)
        assert response.status_code == 503
        assert "secret" not in response.text
    with patch("app.gpt_api.recipe_tools.get_recipe", side_effect=ValueError("Recipe not found: r1")):
        assert client.get("/api/recipes/r1", headers=HEADERS).status_code == 404
    with patch("app.gpt_api.recipe_tools.create_meal_plan", side_effect=ValueError("A meal-plan week already starts on 2026-10-26.")):
        assert client.post("/api/meal-plans", headers=HEADERS, json={
            "start_on": "2026-10-26", "meals": [{"weekday": "Monday", "title": "Soup"}]
        }).status_code == 409


def test_reads_never_seed_or_save():
    with patch("app.recipe_tools.LibraryRepository") as repo:
        instance = repo.from_settings.return_value
        instance.load_meal_plan_payload.return_value = None
        with pytest.raises(ValueError, match="No meal-plan week"):
            recipe_tools.get_meal_plan("2026-10-26")
        instance.save_meal_plan_payload.assert_not_called()
        instance.list_recipe_references.assert_not_called()
        instance.close.assert_called_once()
        instance.load_meal_plan_payload.return_value = json.dumps({"weeks": [{
            "id": "w1", "start_on": "2026-10-26", "notes": "Existing", "entries": []
        }]})
        assert recipe_tools.get_meal_plan("2026-10-26")["notes"] == "Existing"
        instance.save_meal_plan_payload.assert_not_called()


def test_recipe_details_preserve_missing_metadata_and_hide_storage():
    recipe = SimpleNamespace(
        id="r1", title="Soup", cookbook_title="Book", ingredients=[], method_steps=["Stir"],
        source=SimpleNamespace(chapter_title="Soups", page_start=3, page_end=4,
                               metadata={"author": "A", "object_key": "private", "serves": None}),
        review=SimpleNamespace(status="pending_review"),
        extraction=SimpleNamespace(needs_review_reasons=["Missing quantities"]),
    )
    with patch("app.recipe_tools.LibraryRepository") as repo:
        repo.from_settings.return_value.get_recipe.return_value = recipe
        response = client.get("/api/recipes/r1", headers=HEADERS)
        assert response.status_code == 200
        result = response.json()
        assert result["metadata"] == {"author": "A"}
        assert result["method_steps"] == ["Stir"]
        assert result["warnings"] == ["Missing quantities"]
        assert "object_key" not in response.text


def test_bad_dates_and_missing_notes():
    assert client.get("/api/meal-plans/not-a-date", headers=HEADERS).status_code == 422
    assert client.put("/api/meal-plans/2026-10-26", headers=HEADERS, json={
        "meals": [{"weekday": "Monday", "title": "Soup"}]
    }).status_code == 422


def test_api_save_and_replace_use_shared_storage_without_changing_other_weeks():
    stored = json.dumps({"weeks": [{"id": "old", "start_on": "2026-10-19",
                                   "notes": "Keep", "entries": []}]})
    with patch("app.recipe_tools.LibraryRepository") as repo:
        instance = repo.from_settings.return_value
        instance.list_recipe_references.return_value = []
        instance.load_meal_plan_payload.side_effect = lambda: stored

        def save(payload):
            nonlocal stored
            stored = payload

        instance.save_meal_plan_payload.side_effect = save
        response = client.post("/api/meal-plans", headers=HEADERS, json={
            "start_on": "2026-10-26",
            "meals": [{"weekday": "Monday", "title": "Leftovers"}], "notes": "Test"
        })
        assert response.status_code == 201
        assert response.json()["notes"] == "Test"
        week_id = response.json()["week_id"]
        response = client.put("/api/meal-plans/2026-10-26", headers=HEADERS, json={
            "meals": [{"weekday": "Tuesday", "title": "Leftovers"}], "notes": "Updated"
        })
        assert response.status_code == 200
        assert response.json()["week_id"] == week_id
        assert response.json()["meals"][0]["weekday"] == "Tuesday"
        assert client.get("/api/meal-plans/2026-10-19", headers=HEADERS).json()["notes"] == "Keep"
