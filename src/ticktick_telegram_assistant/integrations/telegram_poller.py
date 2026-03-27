from __future__ import annotations

import asyncio

from ticktick_telegram_assistant.services.conversation_service import ConversationService, TelegramUpdate


class TelegramPoller:
    def __init__(
        self,
        *,
        telegram_client,
        conversation_service: ConversationService,
        timeout_seconds: int = 30,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self._telegram_client = telegram_client
        self._conversation_service = conversation_service
        self._timeout_seconds = timeout_seconds
        self._retry_delay_seconds = retry_delay_seconds

    async def poll_once(self, offset: int | None) -> int | None:
        try:
            raw_updates = await self._telegram_client.get_updates(offset=offset, timeout=self._timeout_seconds)
        except Exception:
            await asyncio.sleep(self._retry_delay_seconds)
            return offset

        next_offset = offset
        for raw_update in raw_updates:
            update = TelegramUpdate.model_validate(raw_update)
            await self._conversation_service.handle_update(update)
            next_offset = update.update_id + 1
        return next_offset
