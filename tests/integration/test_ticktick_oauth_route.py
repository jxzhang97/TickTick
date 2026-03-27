from fastapi.testclient import TestClient

from ticktick_telegram_assistant.app import create_app


def test_ticktick_oauth_callback_returns_received_payload() -> None:
    client = TestClient(create_app())

    response = client.get("/auth/ticktick/callback", params={"code": "abc123", "state": "xyz"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "received",
        "code": "abc123",
        "state": "xyz",
    }
