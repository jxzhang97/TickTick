from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from urllib.parse import urlencode

from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.config import Settings
from ticktick_telegram_assistant.db.models.active_context import ActiveContext
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.domain.schemas import TickTickOAuthConnectionResult
from ticktick_telegram_assistant.integrations.ticktick_oauth_client import (
    TickTickOAuthClient,
    TickTickTokenResponse,
)
from ticktick_telegram_assistant.repositories.contexts import ContextRepository
from ticktick_telegram_assistant.repositories.users import UserRepository


class TickTickOAuthService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: sessionmaker[Session],
        oauth_client: TickTickOAuthClient,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._oauth_client = oauth_client

    async def has_connection(self, *, telegram_user_id: str) -> bool:
        with self._session_factory() as session:
            user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
            return self._has_durable_connection(user=user)

    async def create_authorization_url(
        self,
        *,
        telegram_user_id: str,
        display_name: str | None,
    ) -> str | None:
        if not self._settings.ticktick_client_id or not self._settings.public_base_url:
            return None

        with self._session_factory() as session:
            user_repo = UserRepository(session)
            context_repo = ContextRepository(session)
            user = user_repo.get_or_create(
                telegram_user_id=telegram_user_id,
                display_name=display_name,
            )

            state = secrets.token_urlsafe(24)
            for context in context_repo.list_by_type(context_type="ticktick_oauth_state"):
                if context.user_id == user.id:
                    context_repo.delete(context)

            context_repo.add(
                ActiveContext(
                    user_id=user.id,
                    context_type="ticktick_oauth_state",
                    payload_json={"state": state, "telegram_user_id": telegram_user_id},
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                )
            )
            session.commit()

        query = {
            "client_id": self._settings.ticktick_client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "state": state,
        }
        if self._settings.ticktick_scope:
            query["scope"] = self._settings.ticktick_scope
        return f"{self._settings.ticktick_authorize_url}?{urlencode(query)}"

    async def connect_with_code(self, *, code: str, state: str) -> TickTickOAuthConnectionResult:
        with self._session_factory() as session:
            context_repo = ContextRepository(session)
            state_context = self._find_valid_state_context(session, state=state)
            if state_context is None:
                return TickTickOAuthConnectionResult(
                    connected=False,
                    message="这个 TickTick 授权链接已经失效了。回 Telegram 再让我发你一个新的就行。",
                )

            token = await self._oauth_client.exchange_code(
                code=code,
                redirect_uri=self._redirect_uri,
                scope=self._settings.ticktick_scope or None,
            )
            user = session.get(User, state_context.user_id)
            if user is None:
                return TickTickOAuthConnectionResult(
                    connected=False,
                    message="我拿到了回调，但没找到对应用户。回 Telegram 再让我重新发一次授权链接吧。",
                )

            self._store_token(user=user, token=token)
            context_repo.delete(state_context)
            session.commit()

        return TickTickOAuthConnectionResult(
            connected=True,
            message="TickTick 已连接好啦。回 Telegram 继续跟我说就行，这个页面可以关掉了。",
        )

    @property
    def _redirect_uri(self) -> str:
        return f"{self._settings.public_base_url.rstrip('/')}/auth/ticktick/callback"

    def _find_valid_state_context(self, session: Session, *, state: str) -> ActiveContext | None:
        now = datetime.now(timezone.utc)
        for context in ContextRepository(session).list_by_type(context_type="ticktick_oauth_state"):
            expires_at = context.expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at is not None and expires_at < now:
                continue
            if context.payload_json.get("state") == state:
                return context
        return None

    def _has_durable_connection(self, *, user: User | None) -> bool:
        if user is None:
            return False
        if user.ticktick_refresh_token:
            return True

        expires_at = user.ticktick_token_expires_at
        if expires_at is None:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)

    def _store_token(self, *, user: User, token: TickTickTokenResponse) -> None:
        expires_at = None
        if token.expires_in is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=token.expires_in)
        user.ticktick_access_token = token.access_token
        user.ticktick_refresh_token = token.refresh_token
        user.ticktick_token_type = token.token_type
        user.ticktick_token_scope = token.scope
        user.ticktick_token_expires_at = expires_at
        user.ticktick_connected_at = datetime.now(timezone.utc)
