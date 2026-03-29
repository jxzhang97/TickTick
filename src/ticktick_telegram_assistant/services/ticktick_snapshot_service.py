from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Generic, TypeVar

import httpx
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.domain.enums import MemoryType
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickProject, TickTickTask
from ticktick_telegram_assistant.repositories.memory import MemoryRepository


T = TypeVar("T")


@dataclass
class SnapshotReadResult(Generic[T]):
    items: list[T]
    is_stale: bool
    snapshot_synced_at: datetime | None = None


class TickTickSnapshotService:
    _TASKS_KEY = "ticktick_tasks"
    _PROJECTS_KEY = "ticktick_projects"
    _DEFAULT_MAX_STALENESS = timedelta(hours=24)

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        ticktick_client=None,
    ) -> None:
        self._session_factory = session_factory
        self._ticktick_client = ticktick_client

    async def list_tasks_for_user(
        self,
        *,
        user,
        now: datetime | None = None,
        max_staleness: timedelta | None = None,
    ) -> SnapshotReadResult[TickTickTask]:
        if self._ticktick_client is None:
            return SnapshotReadResult(items=self.load_tasks(user_id=user.id), is_stale=True, snapshot_synced_at=None)
        try:
            tasks = await self._ticktick_client.list_tasks(access_token=user.ticktick_access_token, since=None)
        except Exception as exc:
            if self._is_retryable_ticktick_error(exc):
                cached_tasks, cached_at = self._load_snapshot_items(
                    user_id=user.id,
                    key=self._TASKS_KEY,
                    model_cls=TickTickTask,
                    now=now,
                    max_staleness=max_staleness,
                )
                if cached_tasks:
                    return SnapshotReadResult(items=cached_tasks, is_stale=True, snapshot_synced_at=cached_at)
            raise

        fetched_at = now or datetime.now(timezone.utc)
        self.save_tasks(user_id=user.id, tasks=tasks, fetched_at=fetched_at)
        return SnapshotReadResult(items=list(tasks), is_stale=False, snapshot_synced_at=fetched_at)

    async def list_projects_for_user(
        self,
        *,
        user,
        now: datetime | None = None,
        max_staleness: timedelta | None = None,
    ) -> SnapshotReadResult[TickTickProject]:
        if self._ticktick_client is None:
            return SnapshotReadResult(items=self.load_projects(user_id=user.id), is_stale=True, snapshot_synced_at=None)
        try:
            projects = await self._ticktick_client.list_projects(access_token=user.ticktick_access_token)
        except Exception as exc:
            if self._is_retryable_ticktick_error(exc):
                cached_projects, cached_at = self._load_snapshot_items(
                    user_id=user.id,
                    key=self._PROJECTS_KEY,
                    model_cls=TickTickProject,
                    now=now,
                    max_staleness=max_staleness,
                )
                if cached_projects:
                    return SnapshotReadResult(items=cached_projects, is_stale=True, snapshot_synced_at=cached_at)
            raise

        fetched_at = now or datetime.now(timezone.utc)
        self.save_projects(user_id=user.id, projects=projects, fetched_at=fetched_at)
        return SnapshotReadResult(items=list(projects), is_stale=False, snapshot_synced_at=fetched_at)

    def save_tasks(self, *, user_id: int, tasks: list[TickTickTask], fetched_at: datetime | None = None) -> None:
        self._save_snapshot(
            user_id=user_id,
            key=self._TASKS_KEY,
            items=[task.model_dump(mode="json") for task in tasks],
            fetched_at=fetched_at,
        )

    def save_projects(self, *, user_id: int, projects: list[TickTickProject], fetched_at: datetime | None = None) -> None:
        self._save_snapshot(
            user_id=user_id,
            key=self._PROJECTS_KEY,
            items=[project.model_dump(mode="json") for project in projects],
            fetched_at=fetched_at,
        )

    def load_tasks(self, *, user_id: int) -> list[TickTickTask]:
        items, _ = self._load_snapshot_items(user_id=user_id, key=self._TASKS_KEY, model_cls=TickTickTask)
        return items

    def load_projects(self, *, user_id: int) -> list[TickTickProject]:
        items, _ = self._load_snapshot_items(user_id=user_id, key=self._PROJECTS_KEY, model_cls=TickTickProject)
        return items

    def _save_snapshot(
        self,
        *,
        user_id: int,
        key: str,
        items: list[dict],
        fetched_at: datetime | None,
    ) -> None:
        synced_at = fetched_at or datetime.now(timezone.utc)
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=timezone.utc)
        with self._session_factory() as session:
            MemoryRepository(session).upsert_fact(
                user_id=user_id,
                key=key,
                value_json={"items": items, "fetched_at": synced_at.isoformat()},
                memory_type=MemoryType.TICKTICK_SNAPSHOT,
                confidence=1.0,
                source_type="ticktick_sync",
                last_confirmed_at=synced_at,
            )
            session.commit()

    def _load_snapshot_items(
        self,
        *,
        user_id: int,
        key: str,
        model_cls,
        now: datetime | None = None,
        max_staleness: timedelta | None = None,
    ) -> tuple[list, datetime | None]:
        with self._session_factory() as session:
            fact = MemoryRepository(session).get_fact(
                user_id=user_id,
                key=key,
                memory_type=MemoryType.TICKTICK_SNAPSHOT,
            )
        if fact is None:
            return [], None
        value_json = fact.value_json or {}
        fetched_at = self._parse_datetime(value_json.get("fetched_at"))
        if fetched_at is not None:
            reference_now = now or datetime.now(timezone.utc)
            if reference_now.tzinfo is None:
                reference_now = reference_now.replace(tzinfo=timezone.utc)
            max_age = max_staleness or self._DEFAULT_MAX_STALENESS
            if reference_now - fetched_at > max_age:
                return [], fetched_at
        raw_items = value_json.get("items") or []
        return [model_cls.model_validate(item) for item in raw_items], fetched_at

    def _is_retryable_ticktick_error(self, error: Exception) -> bool:
        if isinstance(error, httpx.RequestError):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            return 500 <= error.response.status_code < 600
        return False

    def _parse_datetime(self, value: object) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
