from fastapi.testclient import TestClient

from ticktick_telegram_assistant.app import create_app
from ticktick_telegram_assistant.services.conversation_service import TelegramReply


class FakeConversationService:
    async def handle_update(self, payload) -> list[TelegramReply]:
        return [TelegramReply(chat_id=99, text="收到，我在。")]


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def send_message(self, *, chat_id: int, text: str) -> dict:
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return {"ok": True}


def test_telegram_webhook_accepts_text_message() -> None:
    app = create_app()
    fake_telegram_client = FakeTelegramClient()
    app.state.conversation_service = FakeConversationService()
    app.state.telegram_client = fake_telegram_client
    client = TestClient(app)
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
    assert fake_telegram_client.sent_messages == [{"chat_id": 99, "text": "收到，我在。"}]
