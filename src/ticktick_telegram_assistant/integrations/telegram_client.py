from __future__ import annotations

import httpx


class TelegramClient:
    def __init__(self, token: str, http_client: httpx.AsyncClient | None = None) -> None:
        self.token = token
        self._http_client = http_client

    @property
    def _base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    async def delete_webhook(self) -> bool:
        payload = await self._post("deleteWebhook", json={})
        return bool(payload.get("ok", False))

    async def get_updates(self, *, offset: int | None, timeout: int) -> list[dict]:
        payload = await self._post(
            "getUpdates",
            json={
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        return payload.get("result", [])

    async def send_message(self, *, chat_id: int, text: str) -> dict:
        payload = await self._post(
            "sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
            },
        )
        return payload

    async def _post(self, method: str, *, json: dict) -> dict:
        if self._http_client is not None:
            response = await self._http_client.post(f"{self._base_url}/{method}", json=json)
            response.raise_for_status()
            return response.json()

        async with httpx.AsyncClient(timeout=timeout_from_payload(json)) as client:
            response = await client.post(f"{self._base_url}/{method}", json=json)
            response.raise_for_status()
            return response.json()


def timeout_from_payload(payload: dict) -> float:
    timeout = payload.get("timeout")
    if isinstance(timeout, int) and timeout > 0:
        return float(timeout + 5)
    return 10.0
