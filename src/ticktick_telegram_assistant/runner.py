from __future__ import annotations

import asyncio

from ticktick_telegram_assistant.config import Settings
from ticktick_telegram_assistant.integrations.telegram_client import TelegramClient
from ticktick_telegram_assistant.integrations.telegram_poller import TelegramPoller
from ticktick_telegram_assistant.services.conversation_service import ConversationService
from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker


class LocalAssistantRunner:
    def __init__(
        self,
        *,
        telegram_client: TelegramClient,
        poller: TelegramPoller,
        reminder_worker: ReminderWorker,
    ) -> None:
        self._telegram_client = telegram_client
        self._poller = poller
        self._reminder_worker = reminder_worker
        self.last_update_offset: int | None = None

    async def bootstrap(self) -> None:
        await self._telegram_client.delete_webhook()

    async def run_once(self) -> None:
        self.last_update_offset = await self._poller.poll_once(self.last_update_offset)
        await self._reminder_worker.run_once()

    async def run_forever(self, *, iterations: int | None = None, sleep_seconds: float = 1.0) -> None:
        await self.bootstrap()
        run_count = 0
        while iterations is None or run_count < iterations:
            await self.run_once()
            run_count += 1
            if iterations is None or run_count < iterations:
                await asyncio.sleep(sleep_seconds)


def build_local_runner(settings: Settings | None = None) -> LocalAssistantRunner:
    app_settings = settings or Settings()
    telegram_client = TelegramClient(token=app_settings.telegram_bot_token)
    conversation_service = ConversationService()
    poller = TelegramPoller(
        telegram_client=telegram_client,
        conversation_service=conversation_service,
        timeout_seconds=app_settings.telegram_poll_timeout_seconds,
    )
    return LocalAssistantRunner(
        telegram_client=telegram_client,
        poller=poller,
        reminder_worker=ReminderWorker(),
    )


async def main() -> None:
    runner = build_local_runner()
    await runner.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
