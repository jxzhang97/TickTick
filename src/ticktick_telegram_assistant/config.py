from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://assistant:assistant@localhost:5432/assistant"
    public_base_url: str = ""
    telegram_bot_token: str = ""
    telegram_poll_timeout_seconds: int = 30
    openai_api_key: str = ""
    ticktick_base_url: str = "https://api.ticktick.com"
    ticktick_authorize_url: str = "https://ticktick.com/oauth/authorize"
    ticktick_token_url: str = "https://ticktick.com/oauth/token"
    ticktick_scope: str = ""
    ticktick_client_id: str = ""
    ticktick_client_secret: str = ""
