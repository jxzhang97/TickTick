from ticktick_telegram_assistant.config import Settings


def test_settings_load_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/app")
    settings = Settings()
    assert settings.app_env == "test"
    assert settings.database_url.endswith("/app")
