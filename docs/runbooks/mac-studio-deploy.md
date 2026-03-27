# Mac Studio Deployment

This runbook documents the zero-cost local deployment flow for the TickTick Telegram assistant on a long-lived Mac Studio.

## Target directory

Clone or sync the repository into:

```bash
/Users/jiaxin/doc_unsyn/TickTick_Codex
```

## First-time setup

```bash
cd /Users/jiaxin/doc_unsyn/TickTick_Codex
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
.venv/bin/alembic upgrade head
```

Fill `.env` with your real secrets before starting the service.

## Database choice

### Option A: Docker + Postgres

Keep the default:

```env
DATABASE_URL=postgresql+psycopg://assistant:assistant@localhost:5432/assistant
```

Then start Postgres with:

```bash
docker compose up -d postgres
```

### Option B: Docker-free SQLite fallback

If the machine does not have Docker, change `.env` to:

```env
DATABASE_URL=sqlite:///./assistant.db
```

Then run:

```bash
PYTHONPATH=src .venv/bin/alembic upgrade head
```

SQLite is acceptable for this single-user local deployment path.

## Manual start

```bash
cd /Users/jiaxin/doc_unsyn/TickTick_Codex
./scripts/run_local_assistant.sh
```

## Safe code sync

Use the bundled sync script instead of a raw `rsync --delete` command:

```bash
cd /Users/jiaxin/doc_unsyn/TickTick_Codex
```

From the local machine:

```bash
/Users/jiaxinzhang/Documents/AI/plan2/.worktrees/ticktick-telegram-assistant/scripts/sync_to_studio.sh
```

This preserves remote state that must never be touched during deploys:

- `.env`
- `.venv/`
- `assistant.db`
- `logs/`

Do not run a bare `rsync --delete` against the Studio checkout. For this local-hosted setup, preserving Studio state is more important than deleting stale files, because wiping `assistant.db` will erase the local OAuth/database state and force a new TickTick authorization.

## Install launchd service

```bash
mkdir -p ~/Library/LaunchAgents
cp deploy/macos/com.jxzhang.ticktick-assistant.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.jxzhang.ticktick-assistant.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.jxzhang.ticktick-assistant.plist
launchctl start com.jxzhang.ticktick-assistant
```

## Check status

```bash
launchctl list | rg ticktick-assistant
tail -f logs/launchd.stdout.log logs/launchd.stderr.log
tail -f logs/api.stdout.log logs/api.stderr.log
```

## Stop or restart

```bash
launchctl stop com.jxzhang.ticktick-assistant
launchctl start com.jxzhang.ticktick-assistant
```

## TickTick OAuth callback

The local API exposes:

```text
http://127.0.0.1:8000/auth/ticktick/callback
```

When you need to complete TickTick OAuth, temporarily expose port `8000` with an HTTPS tunnel, update the TickTick developer console `OAuth redirect URL`, finish the authorization, then shut the tunnel down.

## Operating assumptions

- Keep the Mac Studio awake and online.
- Telegram polling is the default receive mode.
- Postgres runs locally via Docker Compose.
