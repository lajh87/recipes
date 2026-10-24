# Deploy and configure the private GPT

## Service

The dedicated `gpt-api` Docker Compose service runs `app.gpt_api:app`. It has no host port binding and exposes only the five GPT operations, `/openapi.json`, and `/healthz`. It does not mount the cookbook UI or its administration/import routes.

Set `GPT_API_KEY` in the existing, git-ignored `.env` to a long random secret. During initial setup a dedicated key was generated locally without displaying it. Keep it out of GPT instructions, schema files, URLs and source control. The service fails closed when the setting is empty. To rotate it, replace the value, recreate only `gpt-api`, and update the GPT's authentication setting.

```sh
docker compose --profile gpt up -d --build --no-deps gpt-api
```

The existing app, worker and main hostname do not need to be restarted.

## Cloudflare

In the existing remotely managed Cloudflare Tunnel, add a public hostname:

- Hostname: `recipes-open.heley.uk`
- Service type: HTTP
- Service URL: `gpt-api:8080`

Create the corresponding tunnel DNS record through Cloudflare's hostname setup. Do not route this hostname to `app:8080`, which serves the full cookbook. Keep the existing main hostname's Access policy intact. The GPT hostname must accept machine requests without an interactive Cloudflare Access login; its data routes are protected by the dedicated bearer key. If a wildcard Access application includes the new hostname, configure an exact-host exception only for this API hostname.

Verify unauthenticated `/api/search?query=pasta` returns 401, `/openapi.json` is reachable, and `/`, `/docs`, `/api/cookbooks`, and import endpoints return 404. Then verify authenticated search and recipe details from outside the local network. Never paste the key into a URL.

## ChatGPT

1. Create a custom GPT in your personal ChatGPT account.
2. Use the name, description, instructions and conversation starters in `instructions.md`. Disable web search and keep sharing set to **Only me**.
3. Add an Action and import `https://recipes-open.heley.uk/openapi.json`, or paste `openapi.json` from this folder.
4. Set authentication to **API Key**, **Bearer**. Enter the local `GPT_API_KEY` value in the authentication field only.
5. Test search, recipe retrieval and meal-plan reading with the Action test controls. Both write operations are explicitly marked consequential.
6. Test a normal recipe summary, a restricted diet, a seven-dinner plan, missing recipe quantities, an unavailable recipe and a service failure. Verify drafts do not write data.
7. For live save/replace testing, choose an unused dated week and mark notes `GPT integration test`. Read it before creating; verify recipe IDs and notes after saving. Test explicit replacement and confirm other weeks remain intact. Remove the test week through the main cookbook UI after testing. Do not overwrite a real plan merely to test the connection.

The GPT stores meals and notes, not its scaled shopping-list calculations. Planner reads do not import legacy files or initialise a week. Existing MCP clients continue using `python -m app.mcp_server`, now with `get_recipe` and `get_meal_plan` in addition to the original three tools.

## Regenerate the schema

After API changes:

```sh
docker compose --profile gpt run --rm --no-deps -T gpt-api python -c 'import json; from app.gpt_api import app; print(json.dumps(app.openapi(), indent=2))' > docs/gpt/openapi.json
```

## Automated verification

Run `python -m pytest tests/test_gpt_api.py tests/test_mcp_server.py tests/test_meal_plan.py` in an environment with `requirements.txt` and pytest installed. Use isolated mocks/fixtures for writes; tests do not need to mutate the real library.
