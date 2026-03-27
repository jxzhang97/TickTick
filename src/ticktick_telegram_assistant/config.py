from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://assistant:assistant@localhost:5432/assistant"
    telegram_bot_token: str = ""
    telegram_poll_timeout_seconds: int = 30
    openai_api_key: str = ""
    ticktick_base_url: str = "https://developer.ticktick.com"
    ticktick_client_id: str = ""
    ticktick_client_secret: str = ""
