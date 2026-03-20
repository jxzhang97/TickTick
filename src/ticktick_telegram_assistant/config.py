from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://assistant:assistant@localhost:5432/assistant"
    telegram_bot_token: str = ""
    openai_api_key: str = ""

