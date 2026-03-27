# TickTick Telegram Assistant Local Hosting Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 TickTick Telegram assistant 从云端 webhook 方案调整为可运行在 Mac Studio 上的本地常驻方案，使用 Telegram polling、本地 Postgres、`launchd` 自启动和临时 OAuth 暴露。

**Architecture:** 保留 `FastAPI + SQLAlchemy + Postgres + OpenAI planner` 的核心结构，但把 Telegram 接收改为本地 polling runner，把 reminder/scheduler 接成同一常驻进程的一部分，并增加本地部署与 `launchd` 运维文件。Webhook 路由保留但降级为非默认模式，TickTick OAuth callback 单独挂在本地 API 上，通过临时 HTTPS 隧道使用。

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, Alembic, Postgres, httpx, APScheduler, pytest, macOS launchd, Docker Compose

---

## File Structure

### Modify

- `README.md`
- `.env.example`
- `src/ticktick_telegram_assistant/app.py`
- `src/ticktick_telegram_assistant/config.py`
- `src/ticktick_telegram_assistant/integrations/telegram_client.py`
- `src/ticktick_telegram_assistant/services/conversation_service.py`
- `src/ticktick_telegram_assistant/workers/scheduler.py`
- `src/ticktick_telegram_assistant/workers/reminder_worker.py`

### Create

- `src/ticktick_telegram_assistant/runner.py`
- `src/ticktick_telegram_assistant/integrations/telegram_poller.py`
- `src/ticktick_telegram_assistant/api/ticktick_oauth.py`
- `scripts/run_local_assistant.sh`
- `deploy/macos/com.jxzhang.ticktick-assistant.plist`
- `tests/unit/test_telegram_poller.py`
- `tests/unit/test_runner.py`
- `tests/integration/test_ticktick_oauth_route.py`
- `docs/runbooks/mac-studio-deploy.md`

## Chunk 1: Telegram Polling and Runtime Wiring

### Task 1: Add failing polling client tests

**Files:**
- Create: `tests/unit/test_telegram_poller.py`
- Modify: `src/ticktick_telegram_assistant/integrations/telegram_client.py`
- Create: `src/ticktick_telegram_assistant/integrations/telegram_poller.py`

- [ ] **Step 1: Write a failing test for `getUpdates` request construction**
- [ ] **Step 2: Run `pytest tests/unit/test_telegram_poller.py -q` and verify it fails for missing poller support**
- [ ] **Step 3: Implement minimal Telegram client HTTP helpers for `deleteWebhook` and `getUpdates`**
- [ ] **Step 4: Implement `TelegramPoller` with offset tracking and backoff skeleton**
- [ ] **Step 5: Re-run `pytest tests/unit/test_telegram_poller.py -q` and verify it passes**
- [ ] **Step 6: Commit polling integration changes**

### Task 2: Add failing runtime runner tests

**Files:**
- Create: `tests/unit/test_runner.py`
- Create: `src/ticktick_telegram_assistant/runner.py`
- Modify: `src/ticktick_telegram_assistant/app.py`
- Modify: `src/ticktick_telegram_assistant/config.py`

- [ ] **Step 1: Write a failing test for boot sequence: clear webhook, start poller, start scheduler**
- [ ] **Step 2: Run `pytest tests/unit/test_runner.py -q` and verify it fails**
- [ ] **Step 3: Implement minimal runner orchestration and settings needed for local runtime**
- [ ] **Step 4: Re-run `pytest tests/unit/test_runner.py -q` and verify it passes**
- [ ] **Step 5: Commit runner wiring**

## Chunk 2: Scheduler and OAuth Callback

### Task 3: Add failing scheduler execution tests

**Files:**
- Modify: `tests/integration/test_scheduler_flow.py`
- Modify: `src/ticktick_telegram_assistant/workers/scheduler.py`
- Modify: `src/ticktick_telegram_assistant/workers/reminder_worker.py`

- [ ] **Step 1: Write a failing test showing the scheduler can execute a reminder tick locally**
- [ ] **Step 2: Run `pytest tests/integration/test_scheduler_flow.py -q` and verify it fails**
- [ ] **Step 3: Implement minimal executable scheduler/reminder loop**
- [ ] **Step 4: Re-run `pytest tests/integration/test_scheduler_flow.py -q` and verify it passes**
- [ ] **Step 5: Commit scheduler execution support**

### Task 4: Add failing TickTick OAuth callback route tests

**Files:**
- Create: `tests/integration/test_ticktick_oauth_route.py`
- Create: `src/ticktick_telegram_assistant/api/ticktick_oauth.py`
- Modify: `src/ticktick_telegram_assistant/app.py`

- [ ] **Step 1: Write a failing test for `/auth/ticktick/callback` receiving `code` and `state`**
- [ ] **Step 2: Run `pytest tests/integration/test_ticktick_oauth_route.py -q` and verify it fails**
- [ ] **Step 3: Implement the minimal callback route and response contract**
- [ ] **Step 4: Re-run `pytest tests/integration/test_ticktick_oauth_route.py -q` and verify it passes**
- [ ] **Step 5: Commit OAuth callback route**

## Chunk 3: Local Deployment and Runbooks

### Task 5: Add local deployment assets and docs

**Files:**
- Create: `scripts/run_local_assistant.sh`
- Create: `deploy/macos/com.jxzhang.ticktick-assistant.plist`
- Create: `docs/runbooks/mac-studio-deploy.md`
- Modify: `README.md`
- Modify: `.env.example`

- [ ] **Step 1: Write a failing documentation test or smoke assertion for expected commands/files**
- [ ] **Step 2: Run the targeted test and verify it fails**
- [ ] **Step 3: Add the local run script, `launchd` plist, and Mac Studio deployment guide**
- [ ] **Step 4: Update README and `.env.example` for local polling mode**
- [ ] **Step 5: Re-run the targeted docs/deployment test and verify it passes**
- [ ] **Step 6: Commit deployment/runbook changes**

## Chunk 4: Verification

### Task 6: Run focused verification

**Files:**
- No file changes required

- [ ] **Step 1: Run the new focused test set**

```bash
.venv/bin/pytest \
  tests/unit/test_telegram_poller.py \
  tests/unit/test_runner.py \
  tests/integration/test_scheduler_flow.py \
  tests/integration/test_ticktick_oauth_route.py -q
```

- [ ] **Step 2: Run the broader regression suite**

```bash
.venv/bin/pytest -q
```

- [ ] **Step 3: Record any gaps**

Expected known gap:
- 真正的 TickTick token exchange 仍需要用户实际凭据和临时 HTTPS 隧道联调

- [ ] **Step 4: Commit final local-hosting adjustment**

```bash
git add README.md .env.example docs deploy scripts src tests
git commit -m "feat: add Mac Studio local hosting runtime"
```
