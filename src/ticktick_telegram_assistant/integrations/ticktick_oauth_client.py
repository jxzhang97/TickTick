from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel


class TickTickTokenResponse(BaseModel):
    access_token: str
    token_type: str | None = None
    refresh_token: str | None = None
    scope: str | None = None
    expires_in: int | None = None


class TickTickOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        token_url: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._http_client = http_client

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        scope: str | None = None,
    ) -> TickTickTokenResponse:
        data: dict[str, Any] = {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if scope:
            data["scope"] = scope

        if self._http_client is not None:
            response = await self._http_client.post(
                self._token_url,
                data=data,
                auth=(self._client_id, self._client_secret),
            )
            response.raise_for_status()
            return TickTickTokenResponse.model_validate(response.json())

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                self._token_url,
                data=data,
                auth=(self._client_id, self._client_secret),
            )
            response.raise_for_status()
            return TickTickTokenResponse.model_validate(response.json())
