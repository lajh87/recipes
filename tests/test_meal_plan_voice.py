import json
from datetime import UTC, date, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from app.config import Settings, get_settings
from app.home_assistant import read_meal_plan_payload
from app.main import app
from app.meal_plan_voice import summarize_meal_plan

URL = "/api/integrations/home-assistant/meal-plan"
HEADERS = {"Authorization": "Bearer dinner-test-key"}
NOW = datetime(2026, 11, 1, 18, tzinfo=UTC)  # Sunday


def meal(title="Pasta", weekday="Sunday", meal="Dinner", completed=False, **extra):
    return dict(title=title, weekday=weekday, meal=meal, completed=completed, **extra)


def week(start="2026-11-01", entries=None, **extra):
    return dict(id=start, start_on=start, entries=entries or [], **extra)


def summary(*weeks, now=NOW):
    return summarize_meal_plan(json.dumps({"weeks": weeks}), now=now)


def test_tonight_includes_all_dishes_and_done_flags_but_week_excludes_done():
    result = summary(week(entries=[
        meal("Pasta", completed=True), meal("Salad"),
        meal("Soup", weekday="Monday", meal="Lunch"),
        meal("Toast", meal="Breakfast"), meal("   "),
    ]))
    assert [item.title for item in result.tonight] == ["Pasta", "Salad"]
    assert [item.completed for item in result.tonight] == [True, False]
    assert "Pasta, already marked done" in result.tonight_speech
    assert "Salad" in result.tonight_speech
    assert [item.title for item in result.remaining] == ["Salad"]
    assert "Pasta" not in result.remaining_speech
    assert "Lunch" not in result.remaining_speech


def test_blank_types_unscheduled_and_unknown_weekdays_are_weekly_only():
    result = summary(week(entries=[
        meal("Leftovers", meal=""), meal("Curry", weekday=""),
        meal("Tacos", weekday="Any day", meal=""),
        meal("Rice", weekday="monday", meal="dinner"),
    ]))
    assert [item.title for item in result.tonight] == ["Leftovers"]
    assert [item.title for item in result.remaining] == ["Leftovers", "Rice", "Curry", "Tacos"]
    assert result.remaining[1].date == date(2026, 11, 2)
    assert result.remaining[-1].date is None
    assert "Other dinners this week: Curry and Tacos." in result.remaining_speech


def test_current_week_ignores_archive_and_future_and_retains_past_unchecked_dinners():
    result = summary(
        week("2026-11-08", [meal("Future")]),
        week("2026-10-25", [meal("Old")]),
        week("2026-10-26", [meal("Earlier", weekday="Monday"), meal("Tonight")]),
        week("", [meal("Undated")]),
    )
    assert result.week.start_on == date(2026, 10, 26)
    assert [item.title for item in result.remaining] == ["Earlier", "Tonight"]
    assert result.tonight[0].date == date(2026, 11, 1)


def test_overlaps_choose_latest_start_and_ties_keep_stored_order():
    result = summary(
        week("2026-10-28", [meal("Earlier overlap")]),
        week("2026-11-01", [meal("First")]),
        week("2026-11-01", [meal("Second")]),
    )
    assert [item.title for item in result.tonight] == ["First"]


def test_sunday_start_orders_weekdays_relative_to_saved_week_not_monday():
    result = summary(week(entries=[
        meal("Saturday", weekday="Saturday"), meal("Monday", weekday="Monday"),
        meal("Sunday", weekday="Sunday"), meal("Friday", weekday="Friday"),
        meal("No day", weekday=""),
    ]))
    assert [item.title for item in result.remaining] == ["Sunday", "Monday", "Friday", "Saturday", "No day"]
    assert result.week.end_on == date(2026, 11, 7)
    assert result.remaining[2].date == date(2026, 11, 6)


@pytest.mark.parametrize("instant,expected", [
    ("2026-07-01T22:59:59+00:00", "2026-07-01"),
    ("2026-07-01T23:00:00+00:00", "2026-07-02"),
    ("2026-01-01T23:30:00+00:00", "2026-01-01"),
    ("2026-03-29T00:59:59+00:00", "2026-03-29"),
    ("2026-03-29T01:00:00+00:00", "2026-03-29"),
    ("2026-10-25T00:59:59+00:00", "2026-10-25"),
    ("2026-10-25T01:00:00+00:00", "2026-10-25"),
])
def test_london_local_date_including_both_dst_changes(instant, expected):
    result = summarize_meal_plan(None, now=datetime.fromisoformat(instant))
    assert result.local_date.isoformat() == expected
    assert result.timezone == "Europe/London"


def test_week_rollover_at_london_midnight_and_timezone_override():
    weeks = [week("2026-06-28", [meal("Old", weekday="Saturday")]),
             week("2026-07-05", [meal("New")])]
    before = summary(*weeks, now=datetime.fromisoformat("2026-07-04T22:59:59+00:00"))
    after = summary(*weeks, now=datetime.fromisoformat("2026-07-04T23:00:00+00:00"))
    assert before.tonight[0].title == "Old"
    assert after.tonight[0].title == "New"
    assert summarize_meal_plan(None, now=NOW, timezone="Pacific/Auckland").local_date == date(2026, 11, 2)


def test_missing_plan_empty_plan_nothing_tonight_and_all_done_have_distinct_answers():
    for payload in (None, '{"weeks": []}'):
        result = summarize_meal_plan(payload, now=NOW)
        assert result.status == "no_current_plan"
        assert result.week is None
        assert result.tonight_speech == "There is no saved meal plan covering today."
    result = summary(week("2026-10-25"))
    assert result.status == "no_current_plan"  # Yesterday was the last day.
    result = summary(week(entries=[meal("Unscheduled", weekday="")]))
    assert result.tonight_speech == "There is no dinner assigned to tonight on your meal plan."
    result = summary(week(entries=[meal(completed=True)]))
    assert result.remaining_speech == "There are no dinners left on this week's meal plan."
    assert summary(week()).remaining_speech == "There are no dinners planned for this week."


def test_legacy_stored_days_load_but_stale_days_do_not_override_empty_entries():
    legacy = {"id": "legacy", "start_on": "2026-11-01", "days": [
        {"label": "Sunday", "meals": [{"meal_label": "Dinner", "title": "Pie", "completed": True}]},
    ]}
    assert summary(legacy).tonight[0].title == "Pie"
    assert not summary(legacy).remaining
    legacy["entries"] = []
    assert not summary(legacy).tonight


@pytest.mark.parametrize("payload", [
    "", "not json", "null", "[]", "{}", '{"weeks": {}}', '{"weeks": [null]}',
    '{"weeks": [{"start_on": "2026-02-30"}]}',
    '{"weeks": [{"entries": null}]}', '{"weeks": [{"entries": [null]}]}',
    '{"weeks": [{"entries": [{"title": null}]}]}',
    '{"weeks": [{"entries": [{"completed": "false"}]}]}',
])
def test_corrupt_storage_is_not_reported_as_an_empty_plan(payload):
    with pytest.raises(ValueError):
        summarize_meal_plan(payload, now=NOW)


def test_titles_remain_literal_and_reading_does_not_import_seed_or_save():
    title = "Fish & chips {{ secrets.key }} <speak>pie</speak>"
    with patch("app.meal_plan.save_meal_plan") as save, patch("app.meal_plan.load_or_import_meal_plan") as load:
        result = summary(week(entries=[meal(title)]))
    assert title in result.tonight_speech
    save.assert_not_called()
    load.assert_not_called()


def test_invalid_timezone_and_naive_clock_are_rejected():
    with pytest.raises(ValueError, match="IANA timezone"):
        Settings(_env_file=None, meal_plan_timezone="Not/AZone")
    with pytest.raises(ValueError, match="aware datetime"):
        summarize_meal_plan(None, now=datetime(2026, 11, 1))


@pytest.fixture
def api():
    settings = Settings(_env_file=None, home_assistant_api_key="dinner-test-key")
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app), settings
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_authentication_fails_before_storage_and_gpt_key_is_not_accepted(api):
    client, settings = api
    settings.gpt_api_key = "different-gpt-key"
    with patch("app.home_assistant.read_meal_plan_payload") as read:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"},
                        {"Authorization": "Bearer different-gpt-key"}):
            response = client.get(URL, headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
        for key in ("", "   "):
            settings.home_assistant_api_key = key
            assert client.get(URL, headers=HEADERS).status_code == 503
        read.assert_not_called()


def test_authenticated_api_is_fresh_uncached_and_has_read_only_method(api):
    client, _ = api
    payload = json.dumps({"weeks": [week(entries=[meal("Pasta")])]})
    with patch("app.home_assistant.read_meal_plan_payload", side_effect=[payload, None]) as read, \
            patch("app.home_assistant.datetime") as clock:
        clock.now.return_value = NOW
        response = client.get(URL, headers=HEADERS)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["tonight"][0]["date"] == "2026-11-01"
        assert response.json()["tonight_speech"] == "Tonight's dinner is Pasta."
        assert client.get(URL, headers=HEADERS).json()["status"] == "no_current_plan"
        assert read.call_count == 2
        assert client.post(URL, headers=HEADERS).status_code == 405


@pytest.mark.parametrize("failure", [RuntimeError("redis://secret-password"), ValueError("bad payload")])
def test_api_failure_is_generic_in_response_and_logs(api, failure, caplog):
    with patch("app.home_assistant.read_meal_plan_payload", side_effect=failure):
        response = api[0].get(URL, headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "Meal-plan service temporarily unavailable."}
    assert response.headers["cache-control"] == "no-store"
    assert str(failure) not in caplog.text


def test_corrupt_payload_is_503_through_api(api):
    with patch("app.home_assistant.read_meal_plan_payload", return_value="{}"):
        assert api[0].get(URL, headers=HEADERS).status_code == 503


def test_redis_reader_only_gets_existing_key_has_timeouts_and_closes():
    settings = Settings(_env_file=None, redis_url="redis://test.invalid/5", redis_key_prefix="test")
    with patch("app.home_assistant.Redis") as redis:
        connection = redis.from_url.return_value
        client = connection.__enter__.return_value
        client.get.return_value = '{"weeks": []}'
        assert read_meal_plan_payload(settings) == '{"weeks": []}'
        assert client.method_calls == [("get", ("test:meal-plan",), {})]
        assert redis.from_url.call_args.kwargs["socket_connect_timeout"] == 1
        assert redis.from_url.call_args.kwargs["socket_timeout"] == 1
        assert redis.from_url.call_args.kwargs["retry"].get_retries() == 0
        connection.__exit__.assert_called_once()


def test_new_endpoint_is_in_web_app_schema_not_restricted_gpt_schema(api):
    from app.gpt_api import app as gpt_app

    operation = api[0].get("/openapi.json").json()["paths"][URL]["get"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert URL not in gpt_app.openapi()["paths"]
