# Combined Home Assistant Alexa Lambda

`lambda_function.py` handles both the custom dinner skill and the Smart Home
skill in one AWS Lambda. It uses Python's standard library; no dependency layer
or pip installation is needed. The user-supplied `smart-home-lambda.py` is kept
unchanged as the original reference, not as the deployment entry point.

| Incoming request | OAuth token source | Home Assistant route |
| --- | --- | --- |
| Custom dinner intent | `context.System.user.accessToken`, with `session.user.accessToken` fallback | `/api/alexa` |
| Smart Home discovery | `directive.payload.scope.token` | `/api/alexa/smart_home` |
| Smart Home device control / state | `directive.endpoint.scope.token` | `/api/alexa/smart_home` |
| Smart Home account grant | `directive.payload.grantee.token` | `/api/alexa/smart_home` |

The custom intents are `TonightDinnerIntent` and `RemainingDinnersIntent`. Launch,
help, stop, cancel and fallback have local spoken responses. Smart Home requests
and successful replies are forwarded intact, preserving discovery results,
endpoint IDs, correlation tokens, cookies, state properties and account grants.
Failures return the appropriate custom-skill or Smart Home response format.

## Create the new AWS function

1. In AWS Lambda, select **Europe (Ireland), eu-west-1**.
2. Choose **Create function → Author from scratch**. Suggested name:
   **HomeAssistantAlexaCombined**. Choose **Python 3.12 or newer**, 128 MB memory,
   and an execution role with **AWSLambdaBasicExecutionRole** for logs. The function
   needs outbound HTTPS to your public Home Assistant URL.
3. Paste `lambda_function.py` into the code editor, replacing the starter file,
   or upload a ZIP with `lambda_function.py` at the ZIP's root. Set the handler to
   **`lambda_function.lambda_handler`** and the function timeout to **7 seconds**.
4. Under **Configuration → Environment variables**, set the following values.
   Copy the first, third and fourth from your working Smart Home function without
   changing them. Use your *custom* skill's actual ID for the second.

   | Variable | Value |
   | --- | --- |
   | `BASE_URL` | Existing public HTTPS Home Assistant URL, including any path prefix |
   | `ALEXA_SKILL_ID` | Custom skill ID, beginning `amzn1.ask.skill.`; not its name or Lambda ARN |
   | `CF_ACCESS_CLIENT_ID` | Existing Cloudflare Access service-token client ID |
   | `CF_ACCESS_CLIENT_SECRET` | Existing Cloudflare Access service-token secret |

   Both Cloudflare variables must be supplied together. Both routes send them
   alongside the linked-account bearer token. For deployments without Cloudflare
   Access, omit both variables. No cookbook API key or long-lived Home Assistant
   token belongs in this Lambda.
5. Click **Deploy**.

## Add both triggers and connect the skills

Add **two triggers** to this new function:

- **Alexa Skills Kit**: enable Skill ID verification and enter the custom skill's
  ID. This is the ID used in `ALEXA_SKILL_ID` above.
- **Alexa Smart Home**: enter the existing Smart Home skill's ID.

If one skill contains both models, use that skill's ID for both trigger types.
If they are separate skills, use each one's own ID even if their display names
match. Smart Home requests do not contain an application ID that the function
can check; their restricted **Alexa Smart Home trigger** provides invocation
authorization. Keep both triggers restricted to your actual skill IDs.

Copy the **new function ARN**. First put it into the custom skill's **Build →
Custom → Endpoint → AWS Lambda ARN → Default Region** (and any configured Europe
override). Save, keep Development testing enabled, and run:

```text
open home assistant
ask home assistant what is for dinner tonight
ask home assistant what is on my meal plan
```

Substitute the actual invocation name if it differs from `home assistant`. Opening
the skill should produce “Home Assistant is ready” followed by usage guidance.
The dinner questions require the custom skill to be linked to your Home Assistant
account and the Home Assistant package to be installed.

After custom requests work, put the same new ARN into the existing Smart Home
skill's **Smart Home service endpoint**, including any configured Europe override.
Test TV art mode, adaptive lighting, a state query and device discovery. Keep the
old function and its ARN available until these checks pass, so you can restore
that endpoint if needed. Account-linking URLs and existing Home Assistant device
exposure settings stay the same when only the Lambda ARN changes.

## Troubleshooting

- **“Missing Alexa access token” from the old function**: check the custom endpoint
  and any regional overrides point at the *new* ARN, and that the uploaded file is
  the combined handler. This handler reads both custom and Smart Home token paths.
- **Link-account card**: enable/link the exact development skill in the Alexa app.
  A manual Lambda Test event does not automatically include a linked-account token;
  test dinner requests through Alexa rather than copying tokens into test fixtures.
- **HTTP 403**: confirm both Cloudflare settings were copied and that the service
  token's Access policy covers `/api/alexa` as well as `/api/alexa/smart_home`.
  Keep the existing Access protection enabled.
- **HTTP 401**: the linked-account OAuth token was rejected. The custom skill
  returns an account-link card; Smart Home returns `INVALID_AUTHORIZATION_CREDENTIAL`.
- **Intent not configured**: check Home Assistant loaded `TonightDinnerIntent` and
  `RemainingDinnersIntent` from `integrations/home-assistant/family_cookbook.yaml`.
- **Function unavailable**: check the new function's triggers, environment settings,
  handler, deployed version/alias, and **Monitor → View CloudWatch logs** in Ireland.
  Appending an alias/version to an ARN requires permissions on that target too.

HTTPS certificate checks remain enabled, redirects are rejected, the HTTP timeout
is 5 seconds, and no automatic retry is performed. Logs contain exception types
and HTTP status codes rather than request bodies, URLs or credentials. Custom
request bodies omit the OAuth token; Smart Home directives retain the original
payload required by Home Assistant. Keep Home Assistant HTTP/debug logs off when
sharing diagnostics, and do not share environment-variable values or raw Alexa
request JSON.

## Local verification

From the repository root, with requirements and pytest installed:

```sh
python -m pytest tests/test_alexa_integration.py tests/test_meal_plan_voice.py -q
```

The tests include compatibility checks against the supplied original function.
They use mocked connections and do not deploy or change AWS resources. Live checks
above are still required before switching the Smart Home endpoint permanently.

See [the full setup guide](../../docs/alexa-home-assistant.md),
[Amazon's custom-skill trigger setup](https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-an-aws-lambda-function.html),
and [Home Assistant's Smart Home setup](https://www.home-assistant.io/integrations/alexa.smart_home/).
