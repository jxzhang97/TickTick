from fastapi.testclient import TestClient

from ticktick_telegram_assistant.app import create_app


class FakeTickTickOAuthService:
    async def connect_with_code(self, *, code: str, state: str):
        class Result:
            connected = True
            message = f"TickTick 已连接，code={code}, state={state}"

        return Result()


def test_ticktick_oauth_callback_returns_connection_message() -> None:
    app = create_app()
    app.state.ticktick_oauth_service = FakeTickTickOAuthService()
    client = TestClient(app)

    response = client.get("/auth/ticktick/callback", params={"code": "abc123", "state": "xyz"})

    assert response.status_code == 200
    assert "TickTick 已连接" in response.text
