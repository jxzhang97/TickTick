from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.integrations.ticktick_client import (
    TickTickProject,
    TickTickProjectData,
    TickTickTask,
)


class FakeTickTickClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_tasks(self, *, access_token: str, since=None) -> list[TickTickTask]:
        self.calls.append({"method": "list_tasks", "access_token": access_token, "since": since})
        return [
            TickTickTask(
                id="t1",
                projectId="p1",
                title="11 点前发邮件",
                desc="回导师",
                dueDate="2026-03-27T18:00:00+0000",
                status=0,
            ),
            TickTickTask(
                id="t2",
                projectId="p1",
                title="下周再说",
                dueDate="2026-03-29T18:00:00+0000",
                status=0,
            ),
        ]


def make_session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@pytest.mark.asyncio
async def test_build_today_brief_renders_today_items_in_user_timezone() -> None:
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(
            User(
                telegram_user_id="99",
                display_name="Jiaxin",
                current_timezone="America/Los_Angeles",
                ticktick_access_token="access-token",
            )
        )
        session.commit()

    service = TodayBriefService(
        session_factory=session_factory,
        ticktick_client=FakeTickTickClient(),
    )

    message = await service.build_today_brief(
        telegram_user_id="99",
        now=datetime.fromisoformat("2026-03-27T09:00:00-07:00"),
    )

    assert "今天我先帮你抓重点" in message
    assert "周五" in message
    assert "11:00" in message
    assert "回导师" in message
    assert "下周再说" not in message
