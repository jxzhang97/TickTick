from pathlib import Path


def test_deployment_docs_cover_env_and_local_postgres() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml")
    runbook = Path("docs/runbooks/mac-studio-deploy.md")
    launchd_plist = Path("deploy/macos/com.jxzhang.ticktick-assistant.plist")
    run_script = Path("scripts/run_local_assistant.sh")

    assert "TELEGRAM_BOT_TOKEN=" in env_example
    assert "OPENAI_API_KEY=" in env_example
    assert "TICKTICK_CLIENT_ID=" in env_example
    assert compose.exists()
    assert "docker compose up -d postgres" in readme
    assert "Telegram polling" in readme
    assert "sqlite:///./assistant.db" in readme
    assert runbook.exists()
    assert "sqlite:///./assistant.db" in runbook.read_text(encoding="utf-8")
    assert "launchctl" in runbook.read_text(encoding="utf-8")
    assert "/Users/jiaxin/doc_unsyn/TickTick_Codex" in runbook.read_text(encoding="utf-8")
    assert launchd_plist.exists()
    assert "/Users/jiaxin/doc_unsyn/TickTick_Codex" in launchd_plist.read_text(encoding="utf-8")
    assert run_script.exists()
