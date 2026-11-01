# Ask Alexa about your meal plan

This integration answers two questions from the same Redis-backed meal plan used
by the cookbook website:

- **“Alexa, what's for dinner tonight?”** reads today's assigned dinner. If it
  is checked off, Alexa still names it and says it is already marked done.
- **“Alexa, what's on my meal plan?”** reads unchecked dinners in the saved
  seven-day plan covering today, including unchecked dinners earlier in the week.

A custom Alexa skill provides the answers: reuse an existing Home Assistant
custom skill, or create the private **Family Cookbook** skill described below.
Two Alexa routines provide the short phrases. Responses return through Alexa's skill
request, so they play on the Echo you asked; no last-used-speaker detection,
Alexa Media Player, or fixed notification speaker is needed.

```text
Echo → Alexa custom skill → AWS Lambda → Home Assistant /api/alexa
     ← spoken response    ←            ← intent_script
                                           ↓ fresh REST GET
                                      Cookbook app → Redis
```

The repository provides the code and configuration. You must complete the account
setup below before the phrases work on your devices. If your existing skill has
an Interaction Model with custom intents, add these dinner intents to it. If
you only have a Smart Home model for device discovery, use a separate Custom
model for these spoken answers. Keep the existing smart-home connection.

## 1. Enable the cookbook's read-only API

Generate a long random secret in your password manager and add it to the
cookbook's git-ignored `.env`:

```dotenv
HOME_ASSISTANT_API_KEY=your-new-random-secret
MEAL_PLAN_TIMEZONE=Europe/London
```

Use a different secret from `GPT_API_KEY`. Rebuild the web app to load the new
code and settings:

```sh
docker compose up -d --build --no-deps app
```

The new endpoint is served by **app**, not the restricted **gpt-api** service:

```text
GET http://COOKBOOK_LAN_HOST:8080/api/integrations/home-assistant/meal-plan
Authorization: Bearer <HOME_ASSISTANT_API_KEY>
```

Home Assistant must be able to reach that address. Use the cookbook server's LAN
hostname/IP or a reachable Docker service name. `localhost` inside Home Assistant
means Home Assistant itself. If using a reverse proxy with a path prefix, include
the prefix in the endpoint URL. Use HTTPS when the request crosses an untrusted
network; certificate verification stays enabled. The cookbook endpoint does not
need a public hostname, a new tunnel, or an exception to its existing browser
login policy when Home Assistant accesses it on the LAN.

The endpoint returns `local_date`, `timezone`, `status`, `week`, `tonight`,
`remaining`, `tonight_speech`, and `remaining_speech`. A selected week contains
`id`, `start_on`, and inclusive `end_on`. Each meal contains `id`, `title`,
`weekday`, `date`, and `completed`; unscheduled dates/weekdays are null.

- `status: ok` means a saved week covers today. Empty results still return 200
  with an appropriate spoken explanation.
- `status: no_current_plan` means no dated plan covers today; `week` is null.
- Missing/invalid bearer credentials return 401. An unset server key or an
  unavailable/malformed datastore returns 503.
- Successful data responses have `Cache-Control: no-store`. Each request reads
  Redis afresh. Reads never import notes, initialise a week, or save changes.
- Only GET is provided. The key authenticates this new route; it grants no GPT
  API access. Other web-app routes retain their existing access controls.

## 2. Install the Home Assistant package

Use a current Home Assistant release supporting REST command response data and
intent action responses. The examples follow the current documented configuration;
an older installation may need upgrading first.

1. Enable packages in `/config/configuration.yaml`, merging this into your existing
   `homeassistant:` section rather than creating a second section:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Copy `integrations/home-assistant/family_cookbook.yaml` from this repository
   to `/config/packages/family_cookbook.yaml`. If you already use a packages
   directory, place it there. The package's empty `alexa: {}` merges with your
   existing Alexa configuration; retain the existing `smart_home:` settings.
3. Merge the entries from `integrations/home-assistant/secrets.example.yaml` into
   `/config/secrets.yaml`, replacing the URL and key:

   ```yaml
   family_cookbook_meal_plan_url: "http://COOKBOOK_LAN_HOST:8080/api/integrations/home-assistant/meal-plan"
   family_cookbook_authorization: "Bearer your-new-random-secret"
   ```

4. Run Home Assistant's configuration check (Developer Tools → YAML → Check
   configuration), then restart Home Assistant. Do not proceed with an invalid
   configuration. This is a manual runtime check; the repository tests do not
   start a Home Assistant instance.
5. In Developer Tools → Actions, run `rest_command.family_cookbook_meal_plan`.
   Check that its response has `status: 200` and the expected spoken answers in
   `content`. This tests networking and authentication without changing any meals.

The two intent scripts request fresh data, return the matching answer as plain
text, and handle failed requests with “I can't reach your meal plan right now.
Please try again shortly.” Parallel requests use separate action responses.
No polling sensors or cached copies of your meal plan are required.

## 3. Connect your Alexa custom skill

### Reuse an existing Home Assistant custom skill

If you already have a custom skill handling commands such as adaptive lighting
and TV art mode, extend it rather than creating another skill.

1. Open that skill in the [Alexa developer console](https://developer.amazon.com/alexa/console/ask).
   Check its invocation name under Interaction Model → Invocation. The display
   name and invocation name can differ; use the invocation name in commands.
2. Under Build → Interaction Model → Intents, add these two custom intents with
   exactly these names and sample utterances:

   | Custom intent | Sample utterances (one per line) |
   | --- | --- |
   | `TonightDinnerIntent` | `what is for dinner tonight` and `what's for dinner tonight` |
   | `RemainingDinnersIntent` | `what is on my meal plan` and `what's on my meal plan` |

   Alternatively, copy **only those two intent objects** from the `intents` array
   in `integrations/alexa/en-GB.json` into your existing model's `intents` array.
   Retain the existing invocation name, other intents, built-ins and slot types.
   Do not replace the whole model with the supplied Family Cookbook model.
3. Save and build the model, and ensure Development testing is enabled. The HA
   package from step 2 already supplies the matching `intent_script` handlers.
4. Retain account linking. If the existing Lambda already forwards custom intents
   to Home Assistant's `/api/alexa`, it can be reused. If it only reads Smart Home
   tokens and posts to `/api/alexa/smart_home`, deploy the supplied combined
   `integrations/alexa/lambda_function.py` as a new function. It handles both
   request types and preserves the Cloudflare Access headers in the user-supplied
   `smart-home-lambda.py`. Follow [the combined Lambda deployment instructions](../integrations/alexa/README.md)
   to add both triggers, configure the custom endpoint first, and then switch the
   Smart Home endpoint after testing. Keep the original function for rollback.
5. If the invocation name is **home assistant**, test:

   - “Alexa, ask Home Assistant what is for dinner tonight.”
   - “Alexa, ask Home Assistant what is on my meal plan.”

If both answer correctly, skip the new-skill, Lambda deployment, and account
linking instructions below and proceed directly to step 4 (routines). If HA's
REST command works but these intents are rejected or not forwarded, inspect the
existing skill's intent names and Lambda routing before changing authentication.

### Create a new skill only if needed

You need an Amazon Developer account using the same Amazon account as your Echo,
an AWS account, and an internet-accessible **Home Assistant** HTTPS URL on port
443 with a publicly trusted certificate. Your existing manual smart-home skill
may already use this URL. The cookbook app can remain reachable only from your
LAN. Do not disable TLS certificate verification.

In the [Alexa developer console](https://developer.amazon.com/alexa/console/ask):

1. Create **Family Cookbook** with language **English (UK)**, model **Custom**,
   and hosting **Provision your own**.
2. Under Interaction Model → JSON Editor, paste
   `integrations/alexa/en-GB.json`. Save and build the model. The invocation name
   is **family cookbook**.
3. Copy the skill ID (`amzn1.ask.skill.…`) for the Lambda setup below.

### Deploy the combined Lambda bridge

In AWS Lambda, select **Europe (Ireland), eu-west-1**, which supports English (UK)
Alexa skills. Create a Python **3.12 or newer** function with a basic Lambda
execution role for logging. It needs outbound HTTPS access to Home Assistant;
the default networking configuration is sufficient for a public HA URL.

- Copy `integrations/alexa/lambda_function.py` into the Lambda code editor as
  `lambda_function.py`. No dependency bundle is needed.
- Set the handler to `lambda_function.lambda_handler`, memory to 128 MB, and
  timeout to **7 seconds**. The HTTPS bridge timeout is 5 seconds; Home Assistant's
  cookbook request timeout is 3 seconds, and Redis has one-second connect/read
  timeouts without retries. There are no automatic network retries.
- Add these environment variables:

  | Name | Value |
  | --- | --- |
  | `BASE_URL` | Your public Home Assistant HTTPS URL, for example `https://ha.example.com` |
  | `ALEXA_SKILL_ID` | The exact custom skill ID copied above |
  | `CF_ACCESS_CLIENT_ID` | Existing Cloudflare Access service-token client ID, if used |
  | `CF_ACCESS_CLIENT_SECRET` | Existing Cloudflare Access service-token secret, if used |

  Preserve both Cloudflare values from the working Smart Home Lambda when Access
  protects your HA hostname. Supply both or neither; the combined function sends
  them on both routes. Keep actual credential values out of this repository.

- Add an **Alexa Skills Kit** trigger, enable Skill ID verification, and restrict
  it to that exact skill ID. Do not create a public function URL or API Gateway
  endpoint for this bridge.
- Deploy the function. Copy its ARN into the custom skill's Endpoint settings
  (default endpoint and Europe endpoint if shown).

To use the same function for the existing Smart Home skill, also add an **Alexa
Smart Home** trigger restricted to that skill's own ID. After the custom questions
work, point the Smart Home service endpoint at the new ARN and verify your device
controls and discovery. Keep the old function and ARN until verification passes.
See [the combined bridge guide](../integrations/alexa/README.md) for the complete
steps, including the case where both models share a skill ID.

The custom route forwards the two dinner intents to `/api/alexa`. Help, launch,
fallback, stop, and cancel are handled locally; opening the skill produces a
welcome response. Smart Home discovery, controls, state queries, and account
grants use `/api/alexa/smart_home`, forwarding their complete payloads and replies.
The bridge uses verified HTTPS and refuses redirects. It logs no events, tokens,
URLs, or response bodies. Custom requests omit OAuth tokens from their JSON body;
Smart Home requests retain the original directive. Custom responses are plain
text; speech of 8,000 or more characters gets an explicit “too long to read aloud”
message rather than a silently truncated list.

### Link the skill to Home Assistant

Enable Account Linking for the custom skill using **Authorization Code Grant**:

| Setting | Value for a UK/EU account |
| --- | --- |
| Authorization URI | `https://YOUR_HA_HOST/auth/authorize` |
| Access Token URI | `https://YOUR_HA_HOST/auth/token` |
| Client ID | `https://layla.amazon.com/` — include the trailing slash |
| Client Secret | A generated value; HA's documented flow does not check this field |
| Client Authentication Scheme | Credentials in request body |
| Scope | `intent` |

Use your actual HA URL, including any required path prefix, consistently with
`BASE_URL`. Home Assistant does not currently enforce the `intent` OAuth scope
as a permission restriction; the bridge's intent allowlist limits what this
function forwards. Do not put a Home Assistant long-lived access token or the
cookbook API key in Lambda's environment.

The HA authorization/token endpoints must support Alexa's account-linking flow.
The two backend API routes can remain protected by Cloudflare Access: this bridge
supports the service-token headers from the existing Smart Home function. Ensure
the service token's policy covers `/api/alexa` as well as `/api/alexa/smart_home`.
Home Assistant's own bearer authentication still applies; no blanket tunnel bypass
is needed.

Save the skill configuration and enable **Development** testing in its Test tab.
In the Alexa mobile app, find it under Your Skills → Dev Skills, enable it, and
complete the Home Assistant login/account-linking flow. Keep the skill private;
public certification/publication is unnecessary for this setup. Development
access is tied to the developer's Amazon account, so Echos on another account
need separate access setup.

Test these direct commands on an Echo configured for English (UK):

- “Alexa, ask Family Cookbook what's for dinner tonight.”
- “Alexa, ask Family Cookbook what's on my meal plan.”

## 4. Add the short phrases as routines

Create these routines in the Alexa mobile app. The trigger text and Custom action
text omit the wake word **Alexa**:

| Routine | Voice trigger | Existing `home assistant` skill action | New `family cookbook` skill action |
| --- | --- | --- | --- |
| Tonight's dinner | `what's for dinner tonight` | `ask home assistant what is for dinner tonight` | `ask family cookbook what is for dinner tonight` |
| Remaining dinners | `what's on my meal plan` | `ask home assistant what is on my meal plan` | `ask family cookbook what is on my meal plan` |

Use the action column matching your skill's invocation name. Only one Custom
action is needed per routine.

Add `what is for dinner tonight` and `what is on my meal plan` as additional
voice triggers if your app offers additional phrases; otherwise create equivalent
routines for those variants. Select **The device you speak to** as the routine's
response/playback device rather than a named Echo. Leave the custom action as
the only action; the skill supplies the dynamic spoken answer.

Use the Custom action preview, then test the short phrases by voice on two Echos.
The [Home Assistant Alexa guide](https://www.home-assistant.io/integrations/alexa.intent/)
documents routines as the workaround for omitting a custom skill's invocation
name. App labels can vary; if a short phrase does not invoke the routine, test
the direct skill phrase and inspect Alexa's Voice History for what it heard.

## Meal-selection rules

- “Today” uses `MEAL_PLAN_TIMEZONE`, default `Europe/London`, including daylight
  saving. It does not depend on the container's timezone.
- A saved week covers its start date through six days later. Sunday and other
  start weekdays are supported. A newer future plan does not replace today's
  plan. If weeks overlap, the latest containing start date wins; equal dates
  retain stored order. Undated weeks cannot establish what is current.
- Meal type **Dinner** or blank counts as dinner. Breakfast, lunch, other custom
  meal types, and rows with empty titles are excluded. Built-in labels are
  matched case-insensitively by the existing planner parser.
- Tonight requires a matching weekday. The weekly answer orders scheduled
  dinners from the saved week's start, grouping multiple dishes for each day.
  Blank or unrecognised weekdays are listed last as “Other dinners this week.”
- The weekly answer excludes checked rows, including checked unscheduled meals.
  An unchecked earlier dinner stays on the list until it is checked off. Notes
  and the shopping-list checkboxes do not affect dinner completion.
- A missing plan, no assigned dinner tonight, an empty week, all dinners done,
  and service failure have different spoken responses. No fallback guesses a
  dinner from another week or assigns an unscheduled meal to tonight.

## Verify the complete installation

1. Compare the REST action's `tonight_speech`/`remaining_speech` with the website.
2. Ask both direct skill questions, then both short routine phrases.
3. Repeat on a second Echo and confirm each answer plays on the Echo you asked.
4. Check off an existing dinner you have actually finished, wait for the website's
   saved indicator, and repeat the weekly question. The dinner should disappear
   immediately. Tonight's question should still name it and mention it is done.
5. Confirm an unscheduled dinner appears only in the weekly answer and a future
   plan does not override this week's plan. Use your normal planner data; do not
   overwrite a real week just to test the connection.

For repository tests (with requirements and pytest installed):

```sh
python -m pytest tests/test_meal_plan_voice.py tests/test_alexa_integration.py -q
python -m pytest tests
```

Tests cover the API, Redis read-only behaviour, meal/date selection, YAML parsing,
speech-template rendering, the interaction model, and Lambda forwarding/failures.
They use isolated fixtures and mocked connections; they do not deploy AWS
resources, validate against a running HA instance, or prove Echo routing.

## Troubleshooting and maintenance

| Symptom | Check |
| --- | --- |
| No current plan | Today's local date must fall within a saved dated week's seven-day window. |
| No dinner tonight | Assign a weekday to dinner; a title alone is intentionally not enough. |
| REST action returns 401 | The HA secrets header must contain `Bearer ` followed by the cookbook key. Restart/reload after changing secrets. |
| REST action returns 503 | Set the server key, check Redis connectivity and stored payload shape, and recreate the app after changing `.env`. |
| Meal-plan unavailable | Test the REST action first, then HA's public HTTPS endpoint. Check normal app/Lambda/HA warning logs. |
| Account-link card | Link or relink the custom skill in the Alexa app; a missing or rejected OAuth token requires account linking. |
| Skill not found | Enable Development testing, use the owning Amazon account, and match English (UK) on the skill and Echo. |
| Unknown intent | Confirm the package loaded and model intent names match `TonightDinnerIntent` and `RemainingDinnersIntent`. |
| Short phrase fails | Test the direct skill phrase, routine Custom action preview, response-device setting, and Alexa Voice History. |

Home Assistant's REST command debug logs can include request headers, and script
traces can include your meal answers. Leave debug logging off for `rest_command`,
`alexa`, and HTTP clients, and do not share unredacted traces or credential files.
The supplied application/Lambda warning logs show exception types only.

To rotate the cookbook key, update the git-ignored `.env`, recreate `app`, update
`family_cookbook_authorization` in HA's secrets, and restart HA. Recheck the REST
action. Keep all actual credentials out of source control, URLs and chat.

To remove the integration, disable/delete the two routines, remove the HA package
and its secrets, and unset `HOME_ASSISTANT_API_KEY` before recreating the app.
For an existing shared custom skill, remove only the two dinner intents and any
dinner-specific routing; keep its Lambda, account linking and other intents.
If you created a dedicated Family Cookbook skill and Lambda, delete those too.
If the combined Lambda now serves Smart Home controls, keep it and its Smart Home
trigger or restore the original Smart Home endpoint before removing it.
Existing meal plans and your separate Alexa smart-home connection do not need changes.

References: [Home Assistant custom Alexa skill](https://www.home-assistant.io/integrations/alexa.intent/),
[intent action responses](https://www.home-assistant.io/integrations/intent_script/),
[REST commands](https://www.home-assistant.io/integrations/rest_command/), and
[Alexa response limits](https://developer.amazon.com/en-US/docs/alexa/custom-skills/request-and-response-json-reference.html).
