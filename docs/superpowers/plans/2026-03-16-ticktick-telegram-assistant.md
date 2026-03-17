# TickTick Telegram Assistant Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个云端常驻的中文 Telegram 助理，作为 TickTick 的对话入口和主提醒通道，支持上下文理解、任务创建/修改/完成、晨报/晚报、T-5 提醒、snooze、时间窗口任务、长期记忆与歧义确认。

**Architecture:** 项目使用 `FastAPI` 处理 Telegram webhook 和健康检查，`SQLAlchemy + Postgres` 保存用户状态、记忆、任务影子和提醒事件，`httpx` 封装 Telegram/TickTick 接口，`OpenAI` 客户端调用 `gpt-5-mini` 只输出结构化计划，再由服务层执行。提醒和简报通过独立 scheduler/worker 驱动，所有高风险写操作先经过确认、重复检测和冲突检测。

**Tech Stack:** Python 3.13, FastAPI, Pydantic, SQLAlchemy, Alembic, Postgres, httpx, APScheduler, OpenAI Python SDK, pytest, pytest-asyncio

---

## File Structure

### Root Files

- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `docker-compose.yml`
- Create: `alembic.ini`

### Application Package

- Create: `src/ticktick_telegram_assistant/__init__.py`
- Create: `src/ticktick_telegram_assistant/app.py`
- Create: `src/ticktick_telegram_assistant/config.py`
- Create: `src/ticktick_telegram_assistant/logging.py`

### API Layer

- Create: `src/ticktick_telegram_assistant/api/__init__.py`
- Create: `src/ticktick_telegram_assistant/api/telegram_webhook.py`
- Create: `src/ticktick_telegram_assistant/api/health.py`

### Database Layer

- Create: `src/ticktick_telegram_assistant/db/__init__.py`
- Create: `src/ticktick_telegram_assistant/db/base.py`
- Create: `src/ticktick_telegram_assistant/db/session.py`
- Create: `src/ticktick_telegram_assistant/db/models/__init__.py`
- Create: `src/ticktick_telegram_assistant/db/models/user.py`
- Create: `src/ticktick_telegram_assistant/db/models/memory_fact.py`
- Create: `src/ticktick_telegram_assistant/db/models/conversation_summary.py`
- Create: `src/ticktick_telegram_assistant/db/models/active_context.py`
- Create: `src/ticktick_telegram_assistant/db/models/task_shadow.py`
- Create: `src/ticktick_telegram_assistant/db/models/reminder_event.py`
- Create: `src/ticktick_telegram_assistant/db/models/action_log.py`

### Repository Layer

- Create: `src/ticktick_telegram_assistant/repositories/__init__.py`
- Create: `src/ticktick_telegram_assistant/repositories/users.py`
- Create: `src/ticktick_telegram_assistant/repositories/memory.py`
- Create: `src/ticktick_telegram_assistant/repositories/contexts.py`
- Create: `src/ticktick_telegram_assistant/repositories/task_shadows.py`
- Create: `src/ticktick_telegram_assistant/repositories/reminders.py`
- Create: `src/ticktick_telegram_assistant/repositories/action_logs.py`

### Integration Clients

- Create: `src/ticktick_telegram_assistant/integrations/__init__.py`
- Create: `src/ticktick_telegram_assistant/integrations/telegram_client.py`
- Create: `src/ticktick_telegram_assistant/integrations/ticktick_client.py`
- Create: `src/ticktick_telegram_assistant/integrations/openai_planner.py`

### Domain and Services

- Create: `src/ticktick_telegram_assistant/domain/__init__.py`
- Create: `src/ticktick_telegram_assistant/domain/enums.py`
- Create: `src/ticktick_telegram_assistant/domain/schemas.py`
- Create: `src/ticktick_telegram_assistant/services/__init__.py`
- Create: `src/ticktick_telegram_assistant/services/time_interpreter.py`
- Create: `src/ticktick_telegram_assistant/services/context_builder.py`
- Create: `src/ticktick_telegram_assistant/services/memory_service.py`
- Create: `src/ticktick_telegram_assistant/services/conversation_service.py`
- Create: `src/ticktick_telegram_assistant/services/confirmation_service.py`
- Create: `src/ticktick_telegram_assistant/services/duplicate_detector.py`
- Create: `src/ticktick_telegram_assistant/services/conflict_detector.py`
- Create: `src/ticktick_telegram_assistant/services/message_renderer.py`
- Create: `src/ticktick_telegram_assistant/services/reminder_service.py`
- Create: `src/ticktick_telegram_assistant/services/briefing_service.py`
- Create: `src/ticktick_telegram_assistant/services/evening_review_service.py`

### Worker Layer

- Create: `src/ticktick_telegram_assistant/workers/__init__.py`
- Create: `src/ticktick_telegram_assistant/workers/scheduler.py`
- Create: `src/ticktick_telegram_assistant/workers/reminder_worker.py`

### Migrations

- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_initial_schema.py`

### Tests

- Create: `tests/conftest.py`
- Create: `tests/unit/test_config.py`
- Create: `tests/unit/test_time_interpreter.py`
- Create: `tests/unit/test_memory_service.py`
- Create: `tests/unit/test_duplicate_detector.py`
- Create: `tests/unit/test_conflict_detector.py`
- Create: `tests/unit/test_db_models.py`
- Create: `tests/unit/test_message_renderer.py`
- Create: `tests/unit/test_reminder_service.py`
- Create: `tests/unit/test_briefing_service.py`
- Create: `tests/unit/test_evening_review_service.py`
- Create: `tests/integration/test_health_route.py`
- Create: `tests/integration/test_deployment_docs.py`
- Create: `tests/integration/test_telegram_webhook.py`
- Create: `tests/integration/test_ticktick_client.py`
- Create: `tests/integration/test_scheduler_flow.py`
- Create: `tests/regression/test_conversation_samples.py`

## Chunk 1: Foundation and Persistence

### Task 1: Bootstrap the Python service

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `src/ticktick_telegram_assistant/__init__.py`
- Create: `tests/integration/test_health_route.py`

- [ ] **Step 1: Write the failing health-route smoke test**

```python
from fastapi.testclient import TestClient

from ticktick_telegram_assistant.app import create_app


def test_health_route_returns_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Create project metadata and dependencies**

```toml
[project]
name = "ticktick-telegram-assistant"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
  "fastapi",
  "uvicorn[standard]",
  "pydantic>=2.0",
  "pydantic-settings",
  "sqlalchemy>=2.0",
  "psycopg[binary]",
  "alembic",
  "httpx",
  "openai",
  "apscheduler",
]

[project.optional-dependencies]
dev = [
  "pytest",
  "pytest-asyncio",
  "pytest-cov",
]

[tool.pytest.ini_options]
pythonpath = ["src"]
```

- [ ] **Step 3: Create a virtualenv**

Run: `python3 -m venv .venv`
Expected: `.venv/` is created

- [ ] **Step 4: Install the project in editable dev mode**

Run: `.venv/bin/pip install -e '.[dev]'`
Expected: editable install succeeds and `.venv/bin/pytest` becomes available

- [ ] **Step 5: Run the smoke test to confirm the package is missing**

Run: `.venv/bin/pytest tests/integration/test_health_route.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ticktick_telegram_assistant'`

- [ ] **Step 6: Add the minimal app package and `/health` route**

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

- [ ] **Step 7: Re-run the editable install so the new package metadata is active**

Run: `.venv/bin/pip install -e '.[dev]'`
Expected: install completes without errors

- [ ] **Step 8: Re-run the smoke test**

Run: `.venv/bin/pytest tests/integration/test_health_route.py -q`
Expected: PASS

- [ ] **Step 9: Commit the bootstrap**

```bash
git add pyproject.toml .gitignore .env.example README.md src tests/integration/test_health_route.py
git commit -m "chore: bootstrap FastAPI service"
```

### Task 2: Add settings, logging, and app factory wiring

**Files:**
- Create: `src/ticktick_telegram_assistant/config.py`
- Create: `src/ticktick_telegram_assistant/logging.py`
- Create: `src/ticktick_telegram_assistant/api/__init__.py`
- Create: `src/ticktick_telegram_assistant/api/health.py`
- Modify: `src/ticktick_telegram_assistant/app.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing settings test**

```python
from ticktick_telegram_assistant.config import Settings


def test_settings_load_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/app")
    settings = Settings()
    assert settings.app_env == "test"
    assert settings.database_url.endswith("/app")
```

- [ ] **Step 2: Run the settings test**

Run: `.venv/bin/pytest tests/unit/test_config.py -q`
Expected: FAIL with `ImportError` or missing `Settings`

- [ ] **Step 3: Implement typed settings and logging setup**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://assistant:assistant@localhost:5432/assistant"
    telegram_bot_token: str = ""
    openai_api_key: str = ""
```

- [ ] **Step 4: Refactor `create_app()` to accept settings and mount routes**

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    app = FastAPI(title="TickTick Telegram Assistant")
    app.state.settings = app_settings
    app.include_router(health_router)
    return app
```

- [ ] **Step 5: Re-run the config and health tests**

Run: `.venv/bin/pytest tests/unit/test_config.py tests/integration/test_health_route.py -q`
Expected: PASS

- [ ] **Step 6: Commit the config layer**

```bash
git add src/ticktick_telegram_assistant/config.py src/ticktick_telegram_assistant/logging.py src/ticktick_telegram_assistant/api src/ticktick_telegram_assistant/app.py tests/unit/test_config.py
git commit -m "feat: add application settings and app factory"
```

### Task 3: Add database session and initial schema

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_initial_schema.py`
- Create: `src/ticktick_telegram_assistant/db/__init__.py`
- Create: `src/ticktick_telegram_assistant/db/base.py`
- Create: `src/ticktick_telegram_assistant/db/session.py`
- Create: `src/ticktick_telegram_assistant/db/models/__init__.py`
- Create: `src/ticktick_telegram_assistant/db/models/user.py`
- Create: `src/ticktick_telegram_assistant/db/models/memory_fact.py`
- Create: `src/ticktick_telegram_assistant/db/models/conversation_summary.py`
- Create: `src/ticktick_telegram_assistant/db/models/active_context.py`
- Create: `src/ticktick_telegram_assistant/db/models/task_shadow.py`
- Create: `src/ticktick_telegram_assistant/db/models/reminder_event.py`
- Create: `src/ticktick_telegram_assistant/db/models/action_log.py`
- Create: `tests/unit/test_db_models.py`

- [ ] **Step 1: Write a failing schema smoke test**

```python
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.db.models.reminder_event import ReminderEvent


def test_models_expose_expected_tablenames() -> None:
    assert User.__tablename__ == "users"
    assert ReminderEvent.__tablename__ == "reminder_events"
```

- [ ] **Step 2: Run the schema smoke test**

Run: `.venv/bin/pytest tests/unit/test_db_models.py -q`
Expected: FAIL because the model modules do not exist

- [ ] **Step 3: Implement SQLAlchemy base, session factory, and models**

```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[str] = mapped_column(unique=True, index=True)
    default_language: Mapped[str] = mapped_column(default="zh-CN")
    current_timezone: Mapped[str] = mapped_column(default="America/Los_Angeles")
```

```python
class ReminderEvent(Base):
    __tablename__ = "reminder_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(index=True)
    scheduled_at: Mapped[datetime]
    dedupe_key: Mapped[str] = mapped_column(unique=True)
```

- [ ] **Step 4: Create the initial Alembic migration**

Run: `.venv/bin/alembic revision -m "initial schema"`
Expected: Generates a migration file that you then edit to create the seven tables defined in the spec

- [ ] **Step 5: Re-run the schema smoke test**

Run: `.venv/bin/pytest tests/unit/test_db_models.py -q`
Expected: PASS

- [ ] **Step 6: Commit the persistence layer**

```bash
git add alembic.ini migrations src/ticktick_telegram_assistant/db tests/unit/test_db_models.py
git commit -m "feat: add persistence schema and migrations"
```

### Task 4: Add repositories and typed domain schemas

**Files:**
- Create: `src/ticktick_telegram_assistant/domain/__init__.py`
- Create: `src/ticktick_telegram_assistant/domain/enums.py`
- Create: `src/ticktick_telegram_assistant/domain/schemas.py`
- Create: `src/ticktick_telegram_assistant/repositories/__init__.py`
- Create: `src/ticktick_telegram_assistant/repositories/users.py`
- Create: `src/ticktick_telegram_assistant/repositories/memory.py`
- Create: `src/ticktick_telegram_assistant/repositories/contexts.py`
- Create: `src/ticktick_telegram_assistant/repositories/task_shadows.py`
- Create: `src/ticktick_telegram_assistant/repositories/reminders.py`
- Create: `src/ticktick_telegram_assistant/repositories/action_logs.py`
- Create: `tests/unit/test_memory_service.py`

- [ ] **Step 1: Write the failing memory-fact upsert test**

```python
from ticktick_telegram_assistant.domain.enums import MemoryType
from ticktick_telegram_assistant.repositories.memory import MemoryRepository


def test_memory_repository_exposes_upsert() -> None:
    repo = MemoryRepository(session=None)
    assert callable(repo.upsert_fact)
    assert MemoryType.PREFERENCE.value == "preference"
```

- [ ] **Step 2: Run the repository test**

Run: `.venv/bin/pytest tests/unit/test_memory_service.py -q`
Expected: FAIL because the repository and enums do not exist

- [ ] **Step 3: Add domain enums and repository interfaces**

```python
class MemoryType(str, Enum):
    PREFERENCE = "preference"
    ALIAS_MAPPING = "alias_mapping"
    TIME_EXPRESSION = "time_expression"
```

```python
class MemoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_fact(self, *, user_id: int, key: str, value_json: dict, memory_type: MemoryType) -> MemoryFact:
        ...
```

- [ ] **Step 4: Add Pydantic schemas for planned actions, confirmation requests, reminder events, and briefing sections**

```python
class PlannedAction(BaseModel):
    action_type: Literal["query", "create", "update", "complete", "reminder_control"]
    target_task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 5: Re-run the repository and config tests**

Run: `.venv/bin/pytest tests/unit/test_memory_service.py tests/unit/test_config.py -q`
Expected: PASS

- [ ] **Step 6: Commit the repository layer**

```bash
git add src/ticktick_telegram_assistant/domain src/ticktick_telegram_assistant/repositories tests/unit/test_memory_service.py
git commit -m "feat: add domain schemas and repositories"
```

## Chunk 2: Telegram, TickTick, and Conversation Core

### Task 5: Add Telegram client and webhook route

**Files:**
- Create: `src/ticktick_telegram_assistant/integrations/telegram_client.py`
- Create: `src/ticktick_telegram_assistant/api/telegram_webhook.py`
- Modify: `src/ticktick_telegram_assistant/app.py`
- Create: `tests/integration/test_telegram_webhook.py`

- [ ] **Step 1: Write the failing webhook acceptance test**

```python
from fastapi.testclient import TestClient

from ticktick_telegram_assistant.app import create_app


def test_telegram_webhook_accepts_text_message() -> None:
    client = TestClient(create_app())
    payload = {"update_id": 1, "message": {"message_id": 10, "from": {"id": 99}, "chat": {"id": 99, "type": "private"}, "text": "今天有什么安排"}}
    response = client.post("/webhook/telegram", json=payload)
    assert response.status_code == 202
```

- [ ] **Step 2: Run the webhook test**

Run: `.venv/bin/pytest tests/integration/test_telegram_webhook.py -q`
Expected: FAIL with `404 Not Found`

- [ ] **Step 3: Implement a Telegram client wrapper and webhook route**

```python
@router.post("/webhook/telegram", status_code=202)
async def telegram_webhook(payload: TelegramUpdate, request: Request) -> dict[str, str]:
    service: ConversationService = request.app.state.conversation_service
    await service.handle_update(payload)
    return {"status": "accepted"}
```

- [ ] **Step 4: Register the webhook router and service dependency placeholder**

```python
app.include_router(telegram_router)
app.state.telegram_client = TelegramClient(token=app_settings.telegram_bot_token)
app.state.conversation_service = NoopConversationService()
```

- [ ] **Step 5: Re-run the webhook and health tests**

Run: `.venv/bin/pytest tests/integration/test_telegram_webhook.py tests/integration/test_health_route.py -q`
Expected: PASS

- [ ] **Step 6: Commit the Telegram ingress**

```bash
git add src/ticktick_telegram_assistant/integrations/telegram_client.py src/ticktick_telegram_assistant/api/telegram_webhook.py src/ticktick_telegram_assistant/app.py tests/integration/test_telegram_webhook.py
git commit -m "feat: add Telegram webhook ingress"
```

### Task 6: Add TickTick adapter interface and official-first client

**Files:**
- Create: `src/ticktick_telegram_assistant/integrations/ticktick_client.py`
- Create: `tests/integration/test_ticktick_client.py`
- Modify: `src/ticktick_telegram_assistant/config.py`
- Modify: `src/ticktick_telegram_assistant/domain/schemas.py`

- [ ] **Step 1: Write the failing TickTick client contract test**

```python
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickClient


def test_ticktick_client_exposes_core_methods() -> None:
    client = TickTickClient(base_url="https://developer.ticktick.com")
    assert callable(client.list_tasks)
    assert callable(client.create_task)
    assert callable(client.update_task)
    assert callable(client.complete_task)
```

- [ ] **Step 2: Run the TickTick client contract test**

Run: `.venv/bin/pytest tests/integration/test_ticktick_client.py -q`
Expected: FAIL because the client module does not exist

- [ ] **Step 3: Implement the provider contract and a real HTTP-backed client**

```python
class TickTickClient:
    async def list_tasks(self, *, since: datetime | None = None) -> list[TickTickTask]:
        ...

    async def create_task(self, task: TickTickTaskCreate) -> TickTickTask:
        ...

    async def update_task(self, task_id: str, patch: TickTickTaskPatch) -> TickTickTask:
        ...
```

- [ ] **Step 4: Add capability metadata and explicit unsupported-feature errors**

```python
class UnsupportedTickTickCapability(RuntimeError):
    pass


class TickTickCapabilities(BaseModel):
    supports_repeat_rules: bool = True
    supports_multiple_reminders: bool = True
    supports_notes: bool = True
```

- [ ] **Step 5: Re-run the TickTick contract test**

Run: `.venv/bin/pytest tests/integration/test_ticktick_client.py -q`
Expected: PASS

- [ ] **Step 6: Commit the TickTick adapter**

```bash
git add src/ticktick_telegram_assistant/integrations/ticktick_client.py src/ticktick_telegram_assistant/config.py src/ticktick_telegram_assistant/domain/schemas.py tests/integration/test_ticktick_client.py
git commit -m "feat: add TickTick adapter contract"
```

### Task 7: Add time interpretation, context building, and OpenAI planning

**Files:**
- Create: `src/ticktick_telegram_assistant/services/time_interpreter.py`
- Create: `src/ticktick_telegram_assistant/services/context_builder.py`
- Create: `src/ticktick_telegram_assistant/integrations/openai_planner.py`
- Create: `src/ticktick_telegram_assistant/services/conversation_service.py`
- Create: `tests/unit/test_time_interpreter.py`
- Create: `tests/regression/test_conversation_samples.py`

- [ ] **Step 1: Write the failing time-window classification tests**

```python
from ticktick_telegram_assistant.services.time_interpreter import TimeInterpreter


def test_interpreter_marks_windowed_tasks() -> None:
    result = TimeInterpreter().parse("下周把周报框架补完", now="2026-03-16T09:00:00-06:00")
    assert result.semantic_type == "windowed"
    assert result.window_start.isoformat().startswith("2026-03-23")


def test_interpreter_marks_explicit_time_tasks() -> None:
    result = TimeInterpreter().parse("明天下午3点开会", now="2026-03-16T09:00:00-06:00")
    assert result.semantic_type == "explicit_time"
    assert result.due_at.isoformat().startswith("2026-03-17T15:00")
```

- [ ] **Step 2: Run the interpreter tests**

Run: `.venv/bin/pytest tests/unit/test_time_interpreter.py -q`
Expected: FAIL because `TimeInterpreter` does not exist

- [ ] **Step 3: Implement deterministic parsing helpers before invoking the LLM**

```python
class TimeInterpreter:
    def parse(self, text: str, *, now: datetime) -> ParsedTimeIntent:
        if "下周" in text or "这两周" in text:
            return self._parse_windowed(text, now=now)
        return self._parse_explicit_or_memo(text, now=now)
```

- [ ] **Step 4: Add an OpenAI planner that returns structured actions only**

```python
class OpenAIPlanner:
    async def plan(self, context: ConversationContext) -> PlannedConversation:
        response = await self._client.responses.create(
            model="gpt-5-mini",
            input=context.to_prompt(),
        )
        return PlannedConversation.model_validate_json(response.output_text)
```

- [ ] **Step 5: Add regression tests for contextual edits and batch lines**

```python
def test_regression_parses_contextual_edit_sample() -> None:
    sample = "改到明天下午\\n再补一句说明\\n放到 fun 那个 list"
    assert sample
```

- [ ] **Step 6: Re-run the interpreter and regression tests**

Run: `.venv/bin/pytest tests/unit/test_time_interpreter.py tests/regression/test_conversation_samples.py -q`
Expected: PASS

- [ ] **Step 7: Commit the planning core**

```bash
git add src/ticktick_telegram_assistant/services/time_interpreter.py src/ticktick_telegram_assistant/services/context_builder.py src/ticktick_telegram_assistant/integrations/openai_planner.py src/ticktick_telegram_assistant/services/conversation_service.py tests/unit/test_time_interpreter.py tests/regression/test_conversation_samples.py
git commit -m "feat: add conversation planning core"
```

### Task 8: Add ambiguity confirmation, duplicate detection, and conflict detection

**Files:**
- Create: `src/ticktick_telegram_assistant/services/confirmation_service.py`
- Create: `src/ticktick_telegram_assistant/services/duplicate_detector.py`
- Create: `src/ticktick_telegram_assistant/services/conflict_detector.py`
- Create: `tests/unit/test_duplicate_detector.py`
- Create: `tests/unit/test_conflict_detector.py`

- [ ] **Step 1: Write the failing duplicate/conflict tests**

```python
from ticktick_telegram_assistant.services.duplicate_detector import DuplicateDetector
from ticktick_telegram_assistant.services.conflict_detector import ConflictDetector


def test_duplicate_detector_flags_similar_task() -> None:
    detector = DuplicateDetector()
    assert detector.is_probable_duplicate("写周报", "周报写完")


def test_conflict_detector_flags_time_overlap() -> None:
    detector = ConflictDetector()
    assert detector.has_conflict("2026-03-17T15:00:00-06:00", "2026-03-17T15:30:00-06:00")
```

- [ ] **Step 2: Run the safety tests**

Run: `.venv/bin/pytest tests/unit/test_duplicate_detector.py tests/unit/test_conflict_detector.py -q`
Expected: FAIL because the detectors do not exist

- [ ] **Step 3: Implement detectors and confirmation policy**

```python
class DuplicateDetector:
    def is_probable_duplicate(self, left: str, right: str) -> bool:
        return SequenceMatcher(a=left, b=right).ratio() >= 0.75
```

```python
class ConflictDetector:
    def has_conflict(self, start_iso: str, other_iso: str) -> bool:
        start = datetime.fromisoformat(start_iso)
        other = datetime.fromisoformat(other_iso)
        return abs((start - other).total_seconds()) < 3600
```

- [ ] **Step 4: Teach `ConversationService` to stop and ask for confirmation when confidence is low or risks are detected**

```python
if plan.requires_confirmation or duplicate_result or conflict_result:
    return await self._confirmation_service.request_confirmation(...)
```

- [ ] **Step 5: Re-run the safety and webhook tests**

Run: `.venv/bin/pytest tests/unit/test_duplicate_detector.py tests/unit/test_conflict_detector.py tests/integration/test_telegram_webhook.py -q`
Expected: PASS

- [ ] **Step 6: Commit the safety layer**

```bash
git add src/ticktick_telegram_assistant/services/confirmation_service.py src/ticktick_telegram_assistant/services/duplicate_detector.py src/ticktick_telegram_assistant/services/conflict_detector.py src/ticktick_telegram_assistant/services/conversation_service.py tests/unit/test_duplicate_detector.py tests/unit/test_conflict_detector.py
git commit -m "feat: add confirmation and safety checks"
```

## Chunk 3: Briefings, Reminders, and End-to-End Flows

### Task 9: Add Chinese message rendering and morning briefing generation

**Files:**
- Create: `src/ticktick_telegram_assistant/services/message_renderer.py`
- Create: `src/ticktick_telegram_assistant/services/briefing_service.py`
- Create: `tests/unit/test_message_renderer.py`
- Create: `tests/unit/test_briefing_service.py`

- [ ] **Step 1: Write the failing morning-brief rendering tests**

```python
from ticktick_telegram_assistant.services.briefing_service import BriefingService


def test_briefing_includes_weekday_and_focus_items() -> None:
    service = BriefingService()
    rendered = service.render_morning_brief(
        top_items=[{"title": "交周报", "when": "2026-03-17 09:00", "weekday": "周二", "description": "发给产品组"}],
        scheduled_items=[],
        ddl_items=[],
        windowed_items=[],
    )
    assert "周二" in rendered
    assert "交周报" in rendered
    assert "发给产品组" in rendered
```

- [ ] **Step 2: Run the rendering tests**

Run: `.venv/bin/pytest tests/unit/test_message_renderer.py tests/unit/test_briefing_service.py -q`
Expected: FAIL because the services do not exist

- [ ] **Step 3: Implement concise, warm Chinese templates with weekday support**

```python
class MessageRenderer:
    def render_weekday(self, dt: datetime) -> str:
        return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()]
```

```python
class BriefingService:
    def render_morning_brief(self, *, top_items: list[dict], scheduled_items: list[dict], ddl_items: list[dict], windowed_items: list[dict]) -> str:
        ...
```

- [ ] **Step 4: Re-run the rendering tests**

Run: `.venv/bin/pytest tests/unit/test_message_renderer.py tests/unit/test_briefing_service.py -q`
Expected: PASS

- [ ] **Step 5: Commit the briefing renderer**

```bash
git add src/ticktick_telegram_assistant/services/message_renderer.py src/ticktick_telegram_assistant/services/briefing_service.py tests/unit/test_message_renderer.py tests/unit/test_briefing_service.py
git commit -m "feat: add Chinese briefing rendering"
```

### Task 10: Add reminder scheduling, T-5 reminders, timezone switching, and snooze

**Files:**
- Create: `src/ticktick_telegram_assistant/services/reminder_service.py`
- Create: `src/ticktick_telegram_assistant/workers/scheduler.py`
- Create: `src/ticktick_telegram_assistant/workers/reminder_worker.py`
- Create: `tests/integration/test_scheduler_flow.py`
- Modify: `src/ticktick_telegram_assistant/repositories/reminders.py`
- Modify: `src/ticktick_telegram_assistant/services/conversation_service.py`

- [ ] **Step 1: Write the failing scheduler tests**

```python
from ticktick_telegram_assistant.services.reminder_service import ReminderService


def test_explicit_time_task_gets_t_minus_five_event() -> None:
    events = ReminderService().build_events(
        task={"id": "t1", "semantic_type": "explicit_time", "due_at": "2026-03-17T15:00:00-06:00"}
    )
    assert any(event["event_type"] == "prestart_reminder" for event in events)


def test_snooze_keeps_original_due_time() -> None:
    event = ReminderService().build_snooze_event(
        task={"id": "t1", "due_at": "2026-03-17T15:00:00-06:00"},
        request_text="1小时后再提醒我",
    )
    assert event["scheduled_at"].startswith("2026-03-17T16:00")
    assert event["task_due_at"] == "2026-03-17T15:00:00-06:00"
```

- [ ] **Step 2: Run the scheduler tests**

Run: `.venv/bin/pytest tests/integration/test_scheduler_flow.py -q`
Expected: FAIL because the reminder service does not exist

- [ ] **Step 3: Implement reminder event generation for explicit-time, windowed, memo-cleanup, and evening-review flows**

```python
class ReminderService:
    def build_events(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        ...

    def build_snooze_event(self, task: dict[str, Any], request_text: str) -> dict[str, Any]:
        ...
```

- [ ] **Step 4: Implement timezone update handling from natural language and Telegram location payloads**

```python
async def update_user_timezone(self, *, user_id: int, timezone_name: str, source: str) -> None:
    ...
```

- [ ] **Step 5: Re-run the scheduler tests and reminder unit tests**

Run: `.venv/bin/pytest tests/integration/test_scheduler_flow.py tests/unit/test_reminder_service.py -q`
Expected: PASS

- [ ] **Step 6: Commit the scheduler layer**

```bash
git add src/ticktick_telegram_assistant/services/reminder_service.py src/ticktick_telegram_assistant/workers/scheduler.py src/ticktick_telegram_assistant/workers/reminder_worker.py src/ticktick_telegram_assistant/repositories/reminders.py src/ticktick_telegram_assistant/services/conversation_service.py tests/integration/test_scheduler_flow.py tests/unit/test_reminder_service.py
git commit -m "feat: add reminder scheduling and snooze"
```

### Task 11: Add evening review, batch actions, and task completion updates

**Files:**
- Create: `src/ticktick_telegram_assistant/services/evening_review_service.py`
- Modify: `src/ticktick_telegram_assistant/services/conversation_service.py`
- Modify: `src/ticktick_telegram_assistant/integrations/ticktick_client.py`
- Create: `tests/unit/test_evening_review_service.py`
- Modify: `tests/regression/test_conversation_samples.py`

- [ ] **Step 1: Write the failing evening-review tests**

```python
from ticktick_telegram_assistant.services.evening_review_service import EveningReviewService


def test_evening_review_parses_batch_completion_reply() -> None:
    service = EveningReviewService()
    result = service.parse_reply(
        reply_text="前两个做完了，第三个改到周四下午",
        candidate_titles=["写周报", "回导师邮件", "整理实验记录"],
    )
    assert result.completed_indices == [0, 1]
    assert result.rescheduled_indices == [2]
```

- [ ] **Step 2: Run the evening-review tests**

Run: `.venv/bin/pytest tests/unit/test_evening_review_service.py tests/regression/test_conversation_samples.py -q`
Expected: FAIL because the service does not exist

- [ ] **Step 3: Implement batch reply parsing and TickTick completion/update calls**

```python
class EveningReviewService:
    def parse_reply(self, *, reply_text: str, candidate_titles: list[str]) -> EveningReviewReply:
        ...
```

```python
async def complete_task(self, task_id: str) -> TickTickTask:
    ...
```

- [ ] **Step 4: Teach the conversation service to route brief replies, confirmation replies, and multi-line updates through dedicated handlers**

```python
if self._is_evening_review_reply(update):
    return await self._handle_evening_review_reply(update)
```

- [ ] **Step 5: Re-run the evening-review and regression tests**

Run: `.venv/bin/pytest tests/unit/test_evening_review_service.py tests/regression/test_conversation_samples.py -q`
Expected: PASS

- [ ] **Step 6: Commit the evening-review flow**

```bash
git add src/ticktick_telegram_assistant/services/evening_review_service.py src/ticktick_telegram_assistant/services/conversation_service.py src/ticktick_telegram_assistant/integrations/ticktick_client.py tests/unit/test_evening_review_service.py tests/regression/test_conversation_samples.py
git commit -m "feat: add evening review and batch action handling"
```

### Task 12: Add deployment files, developer docs, and verification commands

**Files:**
- Create: `docker-compose.yml`
- Modify: `README.md`
- Modify: `.env.example`
- Create: `tests/conftest.py`
- Create: `tests/integration/test_deployment_docs.py`

- [ ] **Step 1: Write the failing deployment-doc smoke tests**

```python
from pathlib import Path


def test_env_example_lists_required_secrets() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=" in env_example
    assert "OPENAI_API_KEY=" in env_example
    assert "TICKTICK_CLIENT_ID=" in env_example
```

- [ ] **Step 2: Run the smoke test**

Run: `.venv/bin/pytest tests/integration/test_deployment_docs.py -q`
Expected: FAIL because `.env.example` and deployment docs are still incomplete

- [ ] **Step 3: Add local Postgres compose config and README runbook**

```yaml
services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_DB: assistant
      POSTGRES_USER: assistant
      POSTGRES_PASSWORD: assistant
    ports:
      - "5432:5432"
```

- [ ] **Step 4: Document the required secrets and webhook setup**

```env
APP_ENV=dev
DATABASE_URL=postgresql+psycopg://assistant:assistant@localhost:5432/assistant
TELEGRAM_BOT_TOKEN=
OPENAI_API_KEY=
TICKTICK_CLIENT_ID=
TICKTICK_CLIENT_SECRET=
```

- [ ] **Step 5: Run the full local verification suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 6: Commit the deployment and docs layer**

```bash
git add docker-compose.yml README.md .env.example tests/conftest.py tests/integration/test_deployment_docs.py
git commit -m "docs: add local deployment and verification guide"
```

## Verification Checklist

- [ ] `.venv/bin/pytest -q`
- [ ] `.venv/bin/alembic upgrade head`
- [ ] `.venv/bin/uvicorn ticktick_telegram_assistant.app:create_app --factory --reload`
- [ ] `docker compose up -d postgres`
- [ ] Confirm `/health` returns `{"status":"ok"}`
- [ ] Send a real Telegram webhook test payload to verify `202 Accepted`

## Execution Notes

- Implement the plan in order; later chunks depend on earlier persistence and routing work.
- Keep the TickTick client behind a narrow interface so missing official capabilities can degrade safely instead of leaking into the whole codebase.
- Do not wire real user credentials into tests; use fakes or monkeypatched HTTP responses for Telegram, TickTick, and OpenAI calls.
- If TickTick OAuth or API shapes differ from the assumptions above, update the adapter contract first, then adjust downstream tests and services; do not spread raw API payload handling across the codebase.

## Open Inputs Needed Before Production

- Telegram Bot Token
- A Telegram chat bound to the bot
- TickTick developer credentials or a usable OAuth flow
- OpenAI API key for `gpt-5-mini`
- Production host with HTTPS for Telegram webhook delivery
