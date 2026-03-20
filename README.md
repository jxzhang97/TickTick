# TickTick Telegram Assistant

Telegram-first TickTick assistant service.

## Local Setup

1. Create a Python 3.13 virtual environment.
2. Install dependencies with `.venv/bin/python -m pip install -e '.[dev]'`.
3. Start local Postgres:

```bash
docker compose up -d postgres
```

4. Copy values from `.env.example` into `.env`.
5. Run the app:

```bash
.venv/bin/uvicorn ticktick_telegram_assistant.app:create_app --factory --reload
```

## Required Secrets

- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `TICKTICK_CLIENT_ID`
- `TICKTICK_CLIENT_SECRET`
- `DATABASE_URL`

## Verification

```bash
.venv/bin/pytest -q
```
