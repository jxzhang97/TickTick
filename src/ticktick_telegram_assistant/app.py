from fastapi import FastAPI
from openai import AsyncOpenAI

from ticktick_telegram_assistant.api.health import router as health_router
from ticktick_telegram_assistant.api.ticktick_oauth import router as ticktick_oauth_router
from ticktick_telegram_assistant.api.telegram_webhook import router as telegram_router
from ticktick_telegram_assistant.config import Settings
from ticktick_telegram_assistant.db.session import create_session_factory
from ticktick_telegram_assistant.integrations.telegram_client import TelegramClient
from ticktick_telegram_assistant.integrations.openai_planner import OpenAIPlanner
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickClient
from ticktick_telegram_assistant.integrations.ticktick_oauth_client import TickTickOAuthClient
from ticktick_telegram_assistant.logging import configure_logging
from ticktick_telegram_assistant.services.conversation_service import ConversationService
from ticktick_telegram_assistant.services.memory_service import MemoryService
from ticktick_telegram_assistant.services.task_command_service import TaskCommandService
from ticktick_telegram_assistant.services.ticktick_oauth_service import TickTickOAuthService
from ticktick_telegram_assistant.services.timezone_resolver import TimezoneResolver
from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    app_settings = settings or Settings()
    app = FastAPI(title="TickTick Telegram Assistant")
    app.state.settings = app_settings
    app.state.session_factory = create_session_factory(app_settings)
    app.state.telegram_client = TelegramClient(token=app_settings.telegram_bot_token)
    app.state.ticktick_client = TickTickClient(base_url=app_settings.ticktick_base_url)
    app.state.openai_planner = OpenAIPlanner(
        client=AsyncOpenAI(api_key=app_settings.openai_api_key) if app_settings.openai_api_key else None
    )
    app.state.ticktick_oauth_service = TickTickOAuthService(
        settings=app_settings,
        session_factory=app.state.session_factory,
        oauth_client=TickTickOAuthClient(
            client_id=app_settings.ticktick_client_id,
            client_secret=app_settings.ticktick_client_secret,
            token_url=app_settings.ticktick_token_url,
        ),
    )
    app.state.memory_service = MemoryService(session_factory=app.state.session_factory)
    app.state.timezone_resolver = TimezoneResolver()
    app.state.conversation_service = ConversationService(
        planner=app.state.openai_planner,
        ticktick_oauth_service=app.state.ticktick_oauth_service,
        today_brief_service=TodayBriefService(
            session_factory=app.state.session_factory,
            ticktick_client=app.state.ticktick_client,
        ),
        task_command_service=TaskCommandService(
            session_factory=app.state.session_factory,
            ticktick_client=app.state.ticktick_client,
        ),
        session_factory=app.state.session_factory,
        timezone_resolver=app.state.timezone_resolver,
        memory_service=app.state.memory_service,
        ticktick_client=app.state.ticktick_client,
    )
    app.include_router(health_router)
    app.include_router(ticktick_oauth_router)
    app.include_router(telegram_router)
    return app
