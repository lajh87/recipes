
import json
import os
import urllib.request
import urllib.error


def lambda_handler(event, context):
    directive = event.get("directive", {})

    # Token for device discovery
    token = (
        directive.get("payload", {})
        .get("scope", {})
        .get("token")
    )

    # Token for device control (on/off, brightness, etc.)
    if not token:
        token = (
            directive.get("endpoint", {})
            .get("scope", {})
            .get("token")
        )

    if not token:
        print("Alexa access token missing")
        raise ValueError("Missing Alexa access token")

    # Home Assistant Alexa Smart Home endpoint
    url = (
        os.environ["BASE_URL"].rstrip("/")
        + "/api/alexa/smart_home"
    )

    # Forward Alexa directive with OAuth and Cloudflare credentials
    request = urllib.request.Request(
        url,
        data=json.dumps(event).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "CF-Access-Client-Id":
                os.environ["CF_ACCESS_CLIENT_ID"],
            "CF-Access-Client-Secret":
                os.environ["CF_ACCESS_CLIENT_SECRET"],
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request, timeout=15
        ) as response:
            result = response.read().decode("utf-8")
            return json.loads(result)

    except urllib.error.HTTPError as error:
        print("Home Assistant HTTP status:", error.code)
        raise

    except urllib.error.URLError as error:
        print("Connection to Home Assistant failed")
        raise
