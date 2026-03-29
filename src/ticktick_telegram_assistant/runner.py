from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from openai import AsyncOpenAI

from ticktick_telegram_assistant.config import Settings
from ticktick_telegram_assistant.db.session import create_session_factory
from ticktick_telegram_assistant.integrations.telegram_client import TelegramClient
from ticktick_telegram_assistant.integrations.openai_planner import OpenAIPlanner
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickClient
from ticktick_telegram_assistant.integrations.telegram_poller import TelegramPoller
from ticktick_telegram_assistant.integrations.ticktick_oauth_client import TickTickOAuthClient
from ticktick_telegram_assistant.services.conversation_service import ConversationService
from ticktick_telegram_assistant.services.memory_service import MemoryService
from ticktick_telegram_assistant.services.task_command_service import TaskCommandService
from ticktick_telegram_assistant.services.ticktick_oauth_service import TickTickOAuthService
from ticktick_telegram_assistant.services.timezone_resolver import TimezoneResolver
from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService
from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker


logger = logging.getLogger(__name__)


class LocalAssistantRunner:
    def __init__(
        self,
        *,
        telegram_client: TelegramClient,
        poller: TelegramPoller,
        reminder_worker: ReminderWorker,
        offset_state_path: str | Path | None = None,
    ) -> None:
        self._telegram_client = telegram_client
        self._poller = poller
        self._reminder_worker = reminder_worker
        self.last_update_offset: int | None = None
        self._offset_state_path = Path(offset_state_path) if offset_state_path is not None else None

    async def bootstrap(self) -> None:
        await self._telegram_client.delete_webhook()
        self.last_update_offset = self._load_offset_state()

    async def run_once(self) -> None:
        self.last_update_offset = await self._poller.poll_once(self.last_update_offset)
        self._save_offset_state(self.last_update_offset)
        await self._reminder_worker.run_once()

    async def run_forever(self, *, iterations: int | None = None, sleep_seconds: float = 1.0) -> None:
        await self.bootstrap()
        poll_task = asyncio.create_task(self._run_polling_loop(iterations=iterations))
        reminder_task = asyncio.create_task(
            self._run_reminder_loop(iterations=iterations, sleep_seconds=sleep_seconds)
        )
        try:
            await asyncio.gather(poll_task, reminder_task)
        finally:
            poll_task.cancel()
            reminder_task.cancel()
            await asyncio.gather(poll_task, reminder_task, return_exceptions=True)

    async def _run_polling_loop(self, *, iterations: int | None) -> None:
        run_count = 0
        while iterations is None or run_count < iterations:
            try:
                self.last_update_offset = await self._poller.poll_once(self.last_update_offset)
                self._save_offset_state(self.last_update_offset)
            except Exception:
                logger.exception("polling loop iteration failed")
            run_count += 1
            if iterations is None or run_count < iterations:
                await asyncio.sleep(0)

    async def _run_reminder_loop(self, *, iterations: int | None, sleep_seconds: float) -> None:
        run_count = 0
        while iterations is None or run_count < iterations:
            try:
                await self._reminder_worker.run_once()
            except Exception:
                logger.exception("reminder loop iteration failed")
            run_count += 1
            if iterations is None or run_count < iterations:
                await asyncio.sleep(sleep_seconds)

    def _load_offset_state(self) -> int | None:
        if self._offset_state_path is None or not self._offset_state_path.exists():
            return None
        raw_value = self._offset_state_path.read_text().strip()
        if not raw_value:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    def _save_offset_state(self, offset: int | None) -> None:
        if self._offset_state_path is None:
            return
        self._offset_state_path.parent.mkdir(parents=True, exist_ok=True)
        if offset is None:
            self._offset_state_path.write_text("")
            return
        self._offset_state_path.write_text(f"{offset}\n")


def build_local_runner(settings: Settings | None = None) -> LocalAssistantRunner:
    app_settings = settings or Settings()
    telegram_client = TelegramClient(token=app_settings.telegram_bot_token)
    session_factory = create_session_factory(app_settings)
    ticktick_client = TickTickClient(base_url=app_settings.ticktick_base_url)
    ticktick_oauth_service = TickTickOAuthService(
        settings=app_settings,
        session_factory=session_factory,
        oauth_client=TickTickOAuthClient(
            client_id=app_settings.ticktick_client_id,
            client_secret=app_settings.ticktick_client_secret,
            token_url=app_settings.ticktick_token_url,
        ),
    )
    memory_service = MemoryService(session_factory=session_factory)
    planner = OpenAIPlanner(client=AsyncOpenAI(api_key=app_settings.openai_api_key) if app_settings.openai_api_key else None)
    conversation_service = ConversationService(
        planner=planner,
        ticktick_oauth_service=ticktick_oauth_service,
        today_brief_service=TodayBriefService(
            session_factory=session_factory,
            ticktick_client=ticktick_client,
        ),
        task_command_service=TaskCommandService(
            session_factory=session_factory,
            ticktick_client=ticktick_client,
        ),
        session_factory=session_factory,
        timezone_resolver=TimezoneResolver(),
        memory_service=memory_service,
        ticktick_client=ticktick_client,
    )
    poller = TelegramPoller(
        telegram_client=telegram_client,
        conversation_service=conversation_service,
        timeout_seconds=app_settings.telegram_poll_timeout_seconds,
    )
    return LocalAssistantRunner(
        telegram_client=telegram_client,
        poller=poller,
        reminder_worker=ReminderWorker(
            session_factory=session_factory,
            ticktick_client=ticktick_client,
            telegram_client=telegram_client,
        ),
        offset_state_path=app_settings.telegram_offset_state_path,
    )


async def main() -> None:
    runner = build_local_runner()
    await runner.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
