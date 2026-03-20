from fastapi import FastAPI

from ticktick_telegram_assistant.api.health import router as health_router
from ticktick_telegram_assistant.config import Settings
from ticktick_telegram_assistant.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    app_settings = settings or Settings()
    app = FastAPI(title="TickTick Telegram Assistant")
    app.state.settings = app_settings
    app.include_router(health_router)
    return app
