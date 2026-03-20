from pathlib import Path


def test_deployment_docs_cover_env_and_local_postgres() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml")

    assert "TELEGRAM_BOT_TOKEN=" in env_example
    assert "OPENAI_API_KEY=" in env_example
    assert "TICKTICK_CLIENT_ID=" in env_example
    assert compose.exists()
    assert "docker compose up -d postgres" in readme
