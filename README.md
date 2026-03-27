# TickTick Telegram Assistant

Telegram-first TickTick assistant service.

## Local Runtime

This branch is configured for local hosting on a long-lived Mac, using Telegram polling instead of a public webhook.

### Local setup

1. Create a Python 3.13 virtual environment.
2. Install dependencies with `.venv/bin/python -m pip install -e '.[dev]'`.
3. Start local Postgres:

```bash
docker compose up -d postgres
```

If the host machine does not have Docker, you can use a local SQLite file instead:

```env
DATABASE_URL=sqlite:///./assistant.db
```

4. Copy values from `.env.example` into `.env`.
5. Apply migrations:

```bash
.venv/bin/alembic upgrade head
```

6. Run the local API in one terminal:

```bash
.venv/bin/uvicorn ticktick_telegram_assistant.app:create_app --factory --host 127.0.0.1 --port 8000
```

7. Run Telegram polling in another terminal:

```bash
.venv/bin/python -m ticktick_telegram_assistant.runner
```

### One-command local start

Use the helper script:

```bash
./scripts/run_local_assistant.sh
```

## Required Secrets

- `PUBLIC_BASE_URL`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_POLL_TIMEOUT_SECONDS`
- `OPENAI_API_KEY`
- `TICKTICK_CLIENT_ID`
- `TICKTICK_CLIENT_SECRET`
- `TICKTICK_SCOPE` (optional if your TickTick app uses default scopes)
- `DATABASE_URL`

## Deployment Notes

- Telegram polling is the default receive mode for local hosting.
- TickTick OAuth callback is exposed locally at `/auth/ticktick/callback`.
- To complete TickTick OAuth on a self-hosted Mac, `PUBLIC_BASE_URL` must point to a temporary or permanent HTTPS address that can reach the local API.
- If Docker is unavailable, set `DATABASE_URL=sqlite:///./assistant.db` and the helper script will skip Postgres startup.
- For Mac Studio deployment and `launchd` setup, see `docs/runbooks/mac-studio-deploy.md`.

## Verification

```bash
.venv/bin/pytest -q
```
