import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment, StrictUndefined
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("cookbook_alexa_bridge", ROOT / "integrations/alexa/lambda_function.py")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)
SKILL_ID = "amzn1.ask.skill.test"


def event(intent="TonightDinnerIntent", *, token="oauth-test-token", request_type="IntentRequest"):
    user = {"accessToken": token} if token is not None else {}
    return {
        "version": "1.0",
        "session": {"application": {"applicationId": SKILL_ID}},
        "context": {"System": {"application": {"applicationId": SKILL_ID}, "user": user}},
        "request": {"type": request_type, "intent": {"name": intent, "slots": {}}},
    }


@pytest.fixture(autouse=True)
def lambda_settings(monkeypatch):
    monkeypatch.setenv("ALEXA_SKILL_ID", SKILL_ID)
    monkeypatch.setenv("BASE_URL", "https://ha.example.test")
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)


@pytest.fixture
def https():
    with patch.object(bridge, "HTTPSConnection") as factory:
        connection = factory.return_value
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps({
            "version": "1.0", "response": {
                "outputSpeech": {"type": "PlainText", "text": "Tonight's dinner is Pasta."},
                "shouldEndSession": True,
            },
        }).encode()
        yield factory, connection, response


@pytest.mark.parametrize("intent", ["TonightDinnerIntent", "RemainingDinnersIntent"])
def test_forwards_only_allowed_intent_using_oauth_header_and_returns_plain_speech(https, intent):
    factory, connection, _ = https
    request = event(intent)
    request["request"]["intent"]["slots"] = {"Unexpected": {"value": "do something else"}}
    result = bridge.lambda_handler(request, None)
    assert result["response"]["outputSpeech"] == {"type": "PlainText", "text": "Tonight's dinner is Pasta."}
    assert result["response"]["shouldEndSession"] is True
    factory.assert_called_once_with("ha.example.test", port=443, timeout=5)
    args, kwargs = connection.request.call_args
    assert args == ("POST", "/api/alexa")
    assert kwargs["headers"]["Authorization"] == "Bearer oauth-test-token"
    payload = json.loads(kwargs["body"])
    assert payload["request"]["intent"] == {"name": intent, "slots": {}}
    assert "oauth-test-token" not in kwargs["body"].decode()
    connection.close.assert_called_once()


def test_prefix_and_session_token_fallback_work_without_caching_tokens(https, monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://ha.example.test/home/")
    request = event(token=None)
    request["session"]["user"] = {"accessToken": "session-token"}
    bridge.lambda_handler(request, None)
    bridge.lambda_handler(event(token="next-user-token"), None)
    calls = https[1].request.call_args_list
    assert calls[0].args == ("POST", "/home/api/alexa")
    assert calls[0].kwargs["headers"]["Authorization"] == "Bearer session-token"
    assert calls[1].kwargs["headers"]["Authorization"] == "Bearer next-user-token"


@pytest.mark.parametrize("kind", ["missing_setting", "wrong_id", "conflicting_ids", "missing_ids"])
def test_skill_identity_is_required_before_any_network_access(https, monkeypatch, kind):
    request = event()
    if kind == "missing_setting":
        monkeypatch.delenv("ALEXA_SKILL_ID")
    elif kind == "wrong_id":
        request["context"]["System"]["application"]["applicationId"] = "other"
    elif kind == "conflicting_ids":
        request["session"]["application"]["applicationId"] = "other"
    else:
        request["session"].pop("application")
        request["context"]["System"].pop("application")
    result = bridge.lambda_handler(request, None)
    assert "not authorised" in result["response"]["outputSpeech"]["text"]
    https[0].assert_not_called()


def test_missing_account_linking_and_expired_token_return_link_account_card(https):
    result = bridge.lambda_handler(event(token=None), None)
    assert result["response"]["card"] == {"type": "LinkAccount"}
    https[0].assert_not_called()
    https[2].status = 401
    assert bridge.lambda_handler(event(), None)["response"]["card"]["type"] == "LinkAccount"
    https[1].close.assert_called_once()


@pytest.mark.parametrize("envelope", [
    event("AMAZON.HelpIntent"), event("AMAZON.FallbackIntent"), event("UnrelatedIntent"),
    event(request_type="LaunchRequest"), event(request_type="UnexpectedRequest"),
])
def test_welcome_help_fallback_and_unknown_intents_never_forward(envelope, https):
    result = bridge.lambda_handler(envelope, None)["response"]
    assert bridge.HELP in result["outputSpeech"]["text"]
    assert result["shouldEndSession"] is False
    assert "reprompt" in result
    https[0].assert_not_called()


@pytest.mark.parametrize("intent", ["AMAZON.StopIntent", "AMAZON.CancelIntent"])
def test_stop_and_cancel_end_session_without_network(intent, https):
    assert bridge.lambda_handler(event(intent), None)["response"]["shouldEndSession"] is True
    https[0].assert_not_called()


def test_session_end_has_no_speech_or_network(https):
    assert bridge.lambda_handler(event(request_type="SessionEndedRequest"), None) == {
        "version": "1.0", "response": {"shouldEndSession": True},
    }
    https[0].assert_not_called()


@pytest.mark.parametrize("url", [
    "", "http://ha.example.test", "https://user:password@ha.example.test",
    "https://ha.example.test?token=secret", "https://ha.example.test/#fragment",
])
def test_invalid_base_url_fails_safely_before_network(url, monkeypatch, https):
    monkeypatch.setenv("BASE_URL", url)
    assert bridge.lambda_handler(event(), None)["response"]["outputSpeech"]["text"] == bridge.UNAVAILABLE
    https[0].assert_not_called()


@pytest.mark.parametrize("status", [301, 302, 307, 403, 500, 503])
def test_http_errors_do_not_follow_redirects_or_speak_response_body(status, https):
    https[2].status = status
    assert bridge.lambda_handler(event(), None)["response"]["outputSpeech"]["text"] == bridge.UNAVAILABLE
    assert https[1].request.call_count == 1
    https[2].read.assert_not_called()
    https[1].close.assert_called_once()


@pytest.mark.parametrize("body", [
    b"not json", b"[]", b'{}', b'{"response": {"outputSpeech": {"type": "SSML", "ssml": "<speak>unsafe</speak>"}}}',
    b'{"response": {"outputSpeech": {"type": "PlainText", "text": null}}}',
    b'{"response": {"outputSpeech": {"type": "PlainText", "text": "   "}}}',
    b"x" * (bridge.MAX_RESPONSE_BYTES + 1),
])
def test_bad_ha_responses_use_safe_fallback(body, https):
    https[2].read.return_value = body
    result = bridge.lambda_handler(event(), None)
    assert result["response"]["outputSpeech"]["text"] == bridge.UNAVAILABLE
    https[1].close.assert_called_once()


def test_network_failure_does_not_expose_secrets_in_speech_or_logs(https, caplog):
    https[1].request.side_effect = TimeoutError("Bearer oauth-test-token https://private-host")
    result = bridge.lambda_handler(event(), None)
    assert result["response"]["outputSpeech"]["text"] == bridge.UNAVAILABLE
    assert "oauth-test-token" not in caplog.text
    assert "private-host" not in caplog.text
    https[1].close.assert_called_once()


def test_alexa_speech_limit_has_explicit_fallback(https):
    https[2].read.return_value = json.dumps({"response": {"outputSpeech": {
        "type": "PlainText", "text": "x" * 8000,
    }}}).encode()
    assert "too long to read aloud" in bridge.lambda_handler(event(), None)["response"]["outputSpeech"]["text"]


class SecretLoader(yaml.SafeLoader):
    pass


SecretLoader.add_constructor("!secret", lambda loader, node: {"secret": loader.construct_scalar(node)})


@pytest.fixture
def ha_package():
    return yaml.load((ROOT / "integrations/home-assistant/family_cookbook.yaml").read_text(), Loader=SecretLoader)


@pytest.mark.parametrize("intent,key", [("TonightDinnerIntent", "tonight_speech"),
                                        ("RemainingDinnersIntent", "remaining_speech")])
def test_ha_templates_use_correct_answer_and_tolerate_failed_or_missing_rest_results(ha_package, intent, key):
    template = Environment(undefined=StrictUndefined).from_string(ha_package["intent_script"][intent]["speech"]["text"])
    speech = "Pasta & rice {{ this is literal }} <speak>pie</speak>"
    result = template.render(action_response={"status": 200, "content": {key: speech}}).strip()
    assert result == speech
    for context in ({}, {"action_response": None}, {"action_response": {}},
                    {"action_response": {"status": 503, "content": {key: "secret"}}},
                    {"action_response": {"status": 200, "content": "not json"}},
                    {"action_response": {"status": 200, "content": {key: None}}},
                    {"action_response": {"status": 200, "content": {key: " "}}}):
        assert template.render(**context).strip() == bridge.UNAVAILABLE


def test_configuration_connects_model_intents_to_fresh_ha_requests(ha_package):
    model = json.loads((ROOT / "integrations/alexa/en-GB.json").read_text())["interactionModel"]["languageModel"]
    assert model["invocationName"] == "family cookbook"
    custom = {intent["name"] for intent in model["intents"] if not intent["name"].startswith("AMAZON.")}
    assert custom == bridge.DINNER_INTENTS == set(ha_package["intent_script"])
    command = ha_package["rest_command"]["family_cookbook_meal_plan"]
    assert command["url"] == {"secret": "family_cookbook_meal_plan_url"}
    assert command["headers"]["authorization"] == {"secret": "family_cookbook_authorization"}
    assert command["timeout"] < 5
    for intent in ha_package["intent_script"].values():
        assert intent["mode"] == "parallel"
        assert intent["action"][0]["variables"] == {"cookbook_result": {}}
        assert intent["action"][1]["continue_on_error"] is True
        assert intent["action"][1]["response_variable"] == intent["action"][-1]["response_variable"]


def test_saved_checkbox_changes_flow_through_template_and_bridge_without_cached_answers(ha_package, https):
    from datetime import UTC, datetime
    from app.meal_plan_voice import summarize_meal_plan

    stored = {"weeks": [{"id": "current", "start_on": "2026-11-01", "entries": [
        {"id": "pasta", "title": "Pasta", "weekday": "Sunday", "meal": "Dinner", "completed": False},
        {"id": "pie", "title": "Pie", "weekday": "Monday", "meal": "Dinner", "completed": False},
    ]}]}
    template = Environment(undefined=StrictUndefined).from_string(
        ha_package["intent_script"]["RemainingDinnersIntent"]["speech"]["text"])
    answers = []
    for done in (False, True):
        stored["weeks"][0]["entries"][0]["completed"] = done
        result = summarize_meal_plan(json.dumps(stored), now=datetime(2026, 11, 1, 18, tzinfo=UTC))
        speech = template.render(action_response={"status": 200, "content": result.model_dump(mode="json")}).strip()
        https[2].read.return_value = json.dumps({"response": {"outputSpeech": {"type": "PlainText", "text": speech}}}).encode()
        answers.append(bridge.lambda_handler(event("RemainingDinnersIntent"), None)["response"]["outputSpeech"]["text"])
    assert "Sunday: Pasta." in answers[0]
    assert "Pasta" not in answers[1]
    assert "Monday: Pie." in answers[1]


def smart_event(operation="control"):
    header = {"namespace": "Alexa.PowerController", "name": "TurnOn", "messageId": "request-id",
              "payloadVersion": "3", "correlationToken": "request-correlation"}
    scope = {"type": "BearerToken", "token": "smart-home-oauth"}
    directive = {
        "header": header,
        "endpoint": {"endpointId": "switch.tv_art_mode", "scope": scope, "cookie": {"keep": "this"}},
        "payload": {},
    }
    if operation == "discovery":
        header.update(namespace="Alexa.Discovery", name="Discover")
        directive.pop("endpoint")
        directive["payload"] = {"scope": scope}
    elif operation == "grant":
        header.update(namespace="Alexa.Authorization", name="AcceptGrant")
        directive.pop("endpoint")
        directive["payload"] = {"grantee": scope, "grant": {"type": "OAuth2.AuthorizationCode", "code": "grant-code"}}
    elif operation == "state":
        header.update(namespace="Alexa", name="ReportState")
    return {"directive": directive}


def smart_reply(operation="control"):
    header = {"namespace": "Alexa", "name": "Response", "messageId": "response-id",
              "payloadVersion": "3", "correlationToken": "request-correlation"}
    body = {"header": header, "endpoint": {"endpointId": "switch.tv_art_mode"}, "payload": {}}
    result = {"event": body, "context": {"properties": [
        {"namespace": "Alexa.PowerController", "name": "powerState", "value": "ON",
         "timeOfSample": "2026-11-01T20:00:00Z", "uncertaintyInMilliseconds": 0},
    ]}}
    if operation == "discovery":
        header.update(namespace="Alexa.Discovery", name="Discover.Response")
        body.pop("endpoint")
        body["payload"] = {"endpoints": [{"endpointId": "switch.tv_art_mode", "friendlyName": "TV Art Mode"}]}
    elif operation == "grant":
        header.update(namespace="Alexa.Authorization", name="AcceptGrant.Response")
        body.pop("endpoint")
    elif operation == "state":
        header["name"] = "StateReport"
    return result


@pytest.mark.parametrize("operation", ["discovery", "control", "state", "grant"])
def test_smart_home_routes_all_directives_with_original_payload_and_response(operation, https, monkeypatch):
    monkeypatch.delenv("ALEXA_SKILL_ID")  # Identity enforcement is by the Smart Home trigger.
    request = smart_event(operation)
    expected = smart_reply(operation)
    https[2].read.return_value = json.dumps(expected).encode()
    assert bridge.lambda_handler(request, None) == expected
    args, kwargs = https[1].request.call_args
    assert args == ("POST", "/api/alexa/smart_home")
    assert kwargs["headers"]["Authorization"] == "Bearer smart-home-oauth"
    assert json.loads(kwargs["body"]) == request
    https[1].close.assert_called_once()


@pytest.mark.parametrize("is_custom", [True, False])
def test_cloudflare_access_headers_are_kept_on_both_routes(is_custom, https, monkeypatch):
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "cloudflare-client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "cloudflare-secret")
    request = event() if is_custom else smart_event()
    if not is_custom:
        https[2].read.return_value = json.dumps(smart_reply()).encode()
    bridge.lambda_handler(request, None)
    headers = https[1].request.call_args.kwargs["headers"]
    assert headers["CF-Access-Client-Id"] == "cloudflare-client-id"
    assert headers["CF-Access-Client-Secret"] == "cloudflare-secret"
    assert headers["Authorization"] == "Bearer " + ("oauth-test-token" if is_custom else "smart-home-oauth")


@pytest.mark.parametrize("is_custom", [True, False])
@pytest.mark.parametrize("setting", ["CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"])
def test_partial_cloudflare_configuration_fails_before_sending_any_credentials(setting, is_custom, https, monkeypatch):
    monkeypatch.setenv(setting, "unpaired-setting")
    result = bridge.lambda_handler(event() if is_custom else smart_event(), None)
    if is_custom:
        assert result["response"]["outputSpeech"]["text"] == bridge.UNAVAILABLE
    else:
        assert result["event"]["payload"]["type"] == "INTERNAL_ERROR"
    https[0].assert_not_called()


def test_shared_function_uses_each_requests_token_and_route_without_cross_contamination(https):
    https[2].read.side_effect = [
        json.dumps({"response": {"outputSpeech": {"type": "PlainText", "text": "Dinner"}}}).encode(),
        json.dumps(smart_reply()).encode(),
        json.dumps({"response": {"outputSpeech": {"type": "PlainText", "text": "Meals"}}}).encode(),
    ]
    bridge.lambda_handler(event(token="first-custom-token"), None)
    bridge.lambda_handler(smart_event(), None)
    bridge.lambda_handler(event("RemainingDinnersIntent", token="second-custom-token"), None)
    calls = https[1].request.call_args_list
    assert [call.args[1] for call in calls] == ["/api/alexa", "/api/alexa/smart_home", "/api/alexa"]
    assert [call.kwargs["headers"]["Authorization"] for call in calls] == [
        "Bearer first-custom-token", "Bearer smart-home-oauth", "Bearer second-custom-token",
    ]


def test_missing_smart_home_token_returns_protocol_error_with_correlation_and_no_credentials(https):
    request = smart_event()
    request["directive"]["endpoint"].pop("scope")
    result = bridge.lambda_handler(request, None)
    assert result["event"]["header"]["name"] == "ErrorResponse"
    assert result["event"]["header"]["payloadVersion"] == "3"
    assert result["event"]["header"]["correlationToken"] == "request-correlation"
    assert result["event"]["endpoint"] == {"endpointId": "switch.tv_art_mode"}
    assert result["event"]["payload"]["type"] == "INVALID_AUTHORIZATION_CREDENTIAL"
    assert "cookie" not in json.dumps(result)
    https[0].assert_not_called()


@pytest.mark.parametrize("directive", [None, {}, {"header": {}},
    {"header": {"namespace": "Alexa.Discovery", "name": "Discover", "payloadVersion": "2"}}])
def test_invalid_smart_home_directives_return_protocol_errors(directive, https):
    result = bridge.lambda_handler({"directive": directive}, None)
    assert result["event"]["payload"]["type"] == "INVALID_DIRECTIVE"
    https[0].assert_not_called()


def test_ambiguous_mixed_request_is_not_forwarded(https):
    request = {**smart_event(), **event()}
    assert bridge.lambda_handler(request, None)["event"]["payload"]["type"] == "INVALID_DIRECTIVE"
    https[0].assert_not_called()


@pytest.mark.parametrize("status,error_type", [(401, "INVALID_AUTHORIZATION_CREDENTIAL"),
    (403, "INTERNAL_ERROR"), (302, "INTERNAL_ERROR"), (503, "INTERNAL_ERROR")])
def test_smart_home_http_errors_preserve_alexa_schema_and_do_not_read_error_bodies(status, error_type, https):
    https[2].status = status
    result = bridge.lambda_handler(smart_event(), None)
    assert result["event"]["payload"]["type"] == error_type
    assert result["event"]["header"]["namespace"] == "Alexa"
    https[2].read.assert_not_called()
    https[1].close.assert_called_once()


@pytest.mark.parametrize("body", [b"<html>Access login</html>", b"null", b'{}',
    b'{"response":{"outputSpeech":{"type":"PlainText","text":"wrong route"}}}',
    b'{"event":{"header":{"payloadVersion":"2"}}}'])
def test_bad_smart_home_responses_are_not_forwarded_as_success(body, https):
    https[2].read.return_value = body
    result = bridge.lambda_handler(smart_event(), None)
    assert result["event"]["payload"]["type"] == "INTERNAL_ERROR"
    https[1].close.assert_called_once()


def test_smart_home_network_failure_logs_no_tokens_cloudflare_secrets_or_request_bodies(https, caplog, monkeypatch):
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "cloudflare-client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "cloudflare-secret")
    https[1].request.side_effect = TimeoutError("smart-home-oauth cloudflare-secret private-host")
    result = bridge.lambda_handler(smart_event(), None)
    assert result["event"]["payload"]["type"] == "INTERNAL_ERROR"
    for secret in ("smart-home-oauth", "cloudflare-secret", "private-host", "switch.tv_art_mode"):
        assert secret not in caplog.text
    https[1].close.assert_called_once()


def test_new_smart_home_transport_preserves_the_supplied_original_functions_request(https, monkeypatch):
    spec = importlib.util.spec_from_file_location("original_smart_home", ROOT / "integrations/alexa/smart-home-lambda.py")
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "cloudflare-client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "cloudflare-secret")
    for operation in ("discovery", "control"):
        request = smart_event(operation)
        reply = smart_reply(operation)
        with patch.object(original.urllib.request, "urlopen") as old_http:
            old_http.return_value.__enter__.return_value.read.return_value = json.dumps(reply).encode()
            assert original.lambda_handler(request, None) == reply
            old_request = old_http.call_args.args[0]
        https[2].read.return_value = json.dumps(reply).encode()
        assert bridge.lambda_handler(request, None) == reply
        call = https[1].request.call_args
        assert "https://ha.example.test" + call.args[1] == old_request.full_url
        assert json.loads(call.kwargs["body"]) == json.loads(old_request.data)
        assert {key.lower(): value for key, value in call.kwargs["headers"].items()} == {
            key.lower(): value for key, value in old_request.header_items()
        }
