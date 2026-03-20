from fastapi.testclient import TestClient

from ticktick_telegram_assistant.app import create_app


def test_telegram_webhook_accepts_text_message() -> None:
    client = TestClient(create_app())
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": 99},
            "chat": {"id": 99, "type": "private"},
            "text": "今天有什么安排",
        },
    }
    response = client.post("/webhook/telegram", json=payload)
    assert response.status_code == 202
