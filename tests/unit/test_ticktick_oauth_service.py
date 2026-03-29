from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.config import Settings
from ticktick_telegram_assistant.db.base import Base
from ticktick_telegram_assistant.db.models.active_context import ActiveContext
from ticktick_telegram_assistant.db.models.user import User


class FakeOAuthClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def exchange_code(self, *, code: str, redirect_uri: str, scope: str | None = None):
        self.calls.append({"code": code, "redirect_uri": redirect_uri, "scope": scope})
        from ticktick_telegram_assistant.integrations.ticktick_oauth_client import TickTickTokenResponse

        return TickTickTokenResponse(
            access_token="access-token",
            refresh_token="refresh-token",
            token_type="Bearer",
            scope="tasks:write",
            expires_in=3600,
        )


def make_session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@pytest.mark.asyncio
async def test_has_connection_requires_durable_oauth_metadata() -> None:
    from ticktick_telegram_assistant.services.ticktick_oauth_service import TickTickOAuthService

    settings = Settings()
    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(User(telegram_user_id="99", ticktick_access_token="access-only"))
        session.commit()

    service = TickTickOAuthService(
        settings=settings,
        session_factory=session_factory,
        oauth_client=FakeOAuthClient(),
    )

    assert await service.has_connection(telegram_user_id="99") is False


@pytest.mark.asyncio
async def test_has_connection_accepts_refresh_token_or_unexpired_token() -> None:
    from ticktick_telegram_assistant.services.ticktick_oauth_service import TickTickOAuthService

    settings = Settings()
    session_factory = make_session_factory()
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            User(
                telegram_user_id="99",
                ticktick_access_token="access-token",
                ticktick_refresh_token="refresh-token",
                ticktick_token_expires_at=now - timedelta(hours=1),
            )
        )
        session.add(
            User(
                telegram_user_id="100",
                ticktick_access_token="access-token",
                ticktick_token_expires_at=now + timedelta(hours=1),
            )
        )
        session.commit()

    service = TickTickOAuthService(
        settings=settings,
        session_factory=session_factory,
        oauth_client=FakeOAuthClient(),
    )

    assert await service.has_connection(telegram_user_id="99") is True
    assert await service.has_connection(telegram_user_id="100") is True


@pytest.mark.asyncio
async def test_create_authorization_url_persists_user_and_state() -> None:
    from ticktick_telegram_assistant.services.ticktick_oauth_service import TickTickOAuthService

    settings = Settings(
        ticktick_client_id="client-id",
        ticktick_client_secret="client-secret",
        public_base_url="https://assistant.example.com",
        ticktick_scope="tasks:read tasks:write",
    )
    session_factory = make_session_factory()
    service = TickTickOAuthService(
        settings=settings,
        session_factory=session_factory,
        oauth_client=FakeOAuthClient(),
    )

    url = await service.create_authorization_url(telegram_user_id="99", display_name="Jiaxin")

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "ticktick.com"
    assert parsed.path == "/oauth/authorize"
    assert params["client_id"] == ["client-id"]
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == ["https://assistant.example.com/auth/ticktick/callback"]
    assert params["scope"] == ["tasks:read tasks:write"]
    assert params["state"]

    with session_factory() as session:
        user = session.query(User).filter(User.telegram_user_id == "99").one()
        assert user.display_name == "Jiaxin"
        state = params["state"][0]
        context = (
            session.query(ActiveContext)
            .filter(ActiveContext.user_id == user.id, ActiveContext.context_type == "ticktick_oauth_state")
            .one()
        )
        assert context.payload_json["state"] == state


@pytest.mark.asyncio
async def test_connect_with_code_updates_stored_tokens() -> None:
    from ticktick_telegram_assistant.services.ticktick_oauth_service import TickTickOAuthService

    settings = Settings(
        ticktick_client_id="client-id",
        ticktick_client_secret="client-secret",
        public_base_url="https://assistant.example.com",
        ticktick_scope="tasks:read tasks:write",
    )
    session_factory = make_session_factory()
    oauth_client = FakeOAuthClient()
    service = TickTickOAuthService(
        settings=settings,
        session_factory=session_factory,
        oauth_client=oauth_client,
    )

    url = await service.create_authorization_url(telegram_user_id="99", display_name="Jiaxin")
    state = parse_qs(urlparse(url).query)["state"][0]

    result = await service.connect_with_code(code="auth-code", state=state)

    assert result.connected is True
    assert "Telegram" in result.message
    assert oauth_client.calls == [
        {
            "code": "auth-code",
            "redirect_uri": "https://assistant.example.com/auth/ticktick/callback",
            "scope": "tasks:read tasks:write",
        }
    ]
    with session_factory() as session:
        user = session.query(User).filter(User.telegram_user_id == "99").one()
        assert user.ticktick_access_token == "access-token"
        assert user.ticktick_refresh_token == "refresh-token"
        assert user.ticktick_token_type == "Bearer"
        assert user.ticktick_token_scope == "tasks:write"
        assert user.ticktick_connected_at is not None
        assert user.ticktick_token_expires_at is not None
        assert user.ticktick_token_expires_at > user.ticktick_connected_at
        assert session.query(ActiveContext).count() == 0
