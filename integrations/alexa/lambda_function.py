"""Combined Alexa Custom and Smart Home bridge; Python 3.12+, standard library only.

Environment: BASE_URL, ALEXA_SKILL_ID (custom skill ID), and optionally the paired
CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET for a Cloudflare Access service token.
Uses linked-account OAuth tokens. No Home Assistant long-lived token is needed.

Configure an Alexa Skills Kit trigger for the custom skill and an Alexa Smart Home
trigger for the Smart Home skill. Restrict each trigger to its actual skill ID.
Smart Home directives do not carry a skill ID: their trigger enforces that check.
"""
from __future__ import annotations

from http.client import HTTPSConnection
import json
import logging
import os
from urllib.parse import urlsplit
from uuid import uuid4

logger = logging.getLogger(__name__)
DINNER_INTENTS = {"TonightDinnerIntent", "RemainingDinnersIntent"}
HELP = "You can ask what's for dinner tonight, or what's on my meal plan."
UNAVAILABLE = "I can't reach your meal plan right now. Please try again shortly."
# Allow large discovery replies without unbounded reads.
MAX_RESPONSE_BYTES = 5_000_000


class HomeAssistantHTTPError(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__("Home Assistant request failed.")


def _response(text: str, *, end_session: bool = True, link_account: bool = False) -> dict:
    body = {"outputSpeech": {"type": "PlainText", "text": text}, "shouldEndSession": end_session}
    if not end_session:
        body["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": HELP}}
    if link_account:
        body["card"] = {"type": "LinkAccount"}
    return {"version": "1.0", "response": body}


def _link_account() -> dict:
    return _response("Please link this skill to Home Assistant in the Alexa app.", link_account=True)


def _post(base_url: str, path: str, token: str, payload: dict) -> dict:
    url = urlsplit(base_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment):
        raise ValueError("BASE_URL must be a public HTTPS Home Assistant URL.")
    if not isinstance(token, str) or not token.strip() or "\r" in token or "\n" in token:
        raise ValueError("Invalid access token.")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    cloudflare_id = os.environ.get("CF_ACCESS_CLIENT_ID", "")
    cloudflare_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
    if bool(cloudflare_id) != bool(cloudflare_secret):
        raise ValueError("Both Cloudflare Access service-token settings are required together.")
    if cloudflare_id:
        headers["CF-Access-Client-Id"] = cloudflare_id
        headers["CF-Access-Client-Secret"] = cloudflare_secret

    connection = HTTPSConnection(url.hostname, port=url.port or 443, timeout=5)
    try:
        connection.request(
            "POST", url.path.rstrip("/") + path,
            body=json.dumps(payload).encode("utf-8"), headers=headers,
        )
        response = connection.getresponse()
        if response.status != 200:
            # Do not follow redirects with credentials or read error bodies.
            logger.warning("Home Assistant returned HTTP %s", response.status)
            raise HomeAssistantHTTPError(response.status)
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Home Assistant response too large.")
        return json.loads(raw)
    finally:
        connection.close()


def _forward(base_url: str, token: str, skill_id: str, intent_name: str) -> dict:
    # Send only the fields Home Assistant needs. In particular, never copy the
    # access token into the JSON body, which Home Assistant may log at debug level.
    payload = {
        "version": "1.0",
        "session": {"application": {"applicationId": skill_id}},
        "request": {"type": "IntentRequest", "intent": {"name": intent_name, "slots": {}}},
    }
    try:
        data = _post(base_url, "/api/alexa", token, payload)
    except HomeAssistantHTTPError as exc:
        if exc.status == 401:
            return _link_account()
        raise
    speech = data["response"]["outputSpeech"]
    if speech.get("type") != "PlainText" or not isinstance(speech.get("text"), str) or not speech["text"].strip():
        raise ValueError("Invalid Home Assistant speech response.")
    if len(speech["text"]) >= 8000:
        return _response("Your meal plan is too long to read aloud. Please open the cookbook to see it.")
    return _response(speech["text"])


def _smart_home_error(event: dict, error_type: str, message: str) -> dict:
    directive = event.get("directive")
    directive = directive if isinstance(directive, dict) else {}
    request_header = directive.get("header", {})
    request_header = request_header if isinstance(request_header, dict) else {}
    header = {
        "namespace": "Alexa", "name": "ErrorResponse", "messageId": str(uuid4()),
        "payloadVersion": "3",
    }
    correlation = request_header.get("correlationToken")
    if isinstance(correlation, str) and correlation:
        header["correlationToken"] = correlation
    body = {"header": header, "payload": {"type": error_type, "message": message}}
    endpoint = directive.get("endpoint", {})
    if isinstance(endpoint, dict) and isinstance(endpoint.get("endpointId"), str):
        body["endpoint"] = {"endpointId": endpoint["endpointId"]}
    return {"event": body}


def _smart_home(event: dict) -> dict:
    """Preserve complete v3 directives and replies, including discovery and grants."""
    try:
        directive = event.get("directive")
        if not isinstance(directive, dict) or "request" in event:
            return _smart_home_error(event, "INVALID_DIRECTIVE", "Invalid Smart Home request.")
        header = directive.get("header", {})
        if (not isinstance(header, dict) or header.get("payloadVersion") != "3"
                or not isinstance(header.get("namespace"), str)
                or not isinstance(header.get("name"), str)):
            return _smart_home_error(event, "INVALID_DIRECTIVE", "A valid v3 directive is required.")

        payload = directive.get("payload", {})
        endpoint = directive.get("endpoint", {})
        scopes = [
            payload.get("scope", {}),       # Discovery
            endpoint.get("scope", {}),      # Device control / state reports
            payload.get("grantee", {}),     # AcceptGrant
        ]
        token = next((scope.get("token") for scope in scopes
                      if isinstance(scope, dict) and scope.get("type") == "BearerToken"
                      and isinstance(scope.get("token"), str) and scope["token"].strip()), None)
        if token is None:
            return _smart_home_error(event, "INVALID_AUTHORIZATION_CREDENTIAL", "Alexa account linking is required.")
        result = _post(os.environ.get("BASE_URL", ""), "/api/alexa/smart_home", token, event)
        response_header = result["event"]["header"]
        if (response_header.get("payloadVersion") != "3"
                or not isinstance(response_header.get("namespace"), str)
                or not isinstance(response_header.get("name"), str)):
            raise ValueError("Invalid Smart Home response.")
        return result
    except HomeAssistantHTTPError as exc:
        error_type = "INVALID_AUTHORIZATION_CREDENTIAL" if exc.status == 401 else "INTERNAL_ERROR"
        return _smart_home_error(event, error_type, "Home Assistant request failed.")
    except Exception as exc:
        logger.warning("Alexa Smart Home bridge failed (%s)", type(exc).__name__)
        return _smart_home_error(event, "INTERNAL_ERROR", "Home Assistant is temporarily unavailable.")


def lambda_handler(event: dict, context: object) -> dict:
    if isinstance(event, dict) and "directive" in event:
        return _smart_home(event)
    try:
        skill_id = os.environ.get("ALEXA_SKILL_ID", "").strip()
        system = event.get("context", {}).get("System", {})
        session = event.get("session", {})
        received_ids = [
            source["applicationId"] for source in (system.get("application", {}), session.get("application", {}))
            if "applicationId" in source
        ]
        if not skill_id or not received_ids or any(value != skill_id for value in received_ids):
            return _response("This request is not authorised for this custom skill.")

        request = event.get("request", {})
        request_type = request.get("type")
        if request_type == "SessionEndedRequest":
            return {"version": "1.0", "response": {"shouldEndSession": True}}
        if request_type == "LaunchRequest":
            return _response("Home Assistant is ready. " + HELP, end_session=False)
        if request_type != "IntentRequest":
            return _response(HELP, end_session=False)
        intent_name = request.get("intent", {}).get("name")
        if intent_name in {"AMAZON.StopIntent", "AMAZON.CancelIntent"}:
            return _response("Goodbye.")
        if intent_name not in DINNER_INTENTS:
            return _response(HELP, end_session=False)

        token = system.get("user", {}).get("accessToken") or session.get("user", {}).get("accessToken")
        if not isinstance(token, str) or not token.strip():
            return _link_account()
        if "\r" in token or "\n" in token:
            raise ValueError("Invalid token.")
        return _forward(os.environ.get("BASE_URL", ""), token, skill_id, intent_name)
    except Exception as exc:
        # Never log events, tokens, URLs, response bodies or exception messages.
        logger.warning("Alexa meal-plan bridge failed (%s)", type(exc).__name__)
        return _response(UNAVAILABLE)
