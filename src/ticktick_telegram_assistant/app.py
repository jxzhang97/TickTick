from fastapi import FastAPI

from ticktick_telegram_assistant.api.health import router as health_router
from ticktick_telegram_assistant.api.ticktick_oauth import router as ticktick_oauth_router
from ticktick_telegram_assistant.api.telegram_webhook import router as telegram_router
from ticktick_telegram_assistant.config import Settings
from ticktick_telegram_assistant.integrations.telegram_client import TelegramClient
from ticktick_telegram_assistant.logging import configure_logging
from ticktick_telegram_assistant.services.conversation_service import ConversationService


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    app_settings = settings or Settings()
    app = FastAPI(title="TickTick Telegram Assistant")
    app.state.settings = app_settings
    app.state.telegram_client = TelegramClient(token=app_settings.telegram_bot_token)
    app.state.conversation_service = ConversationService()
    app.include_router(health_router)
    app.include_router(ticktick_oauth_router)
    app.include_router(telegram_router)
    return app
