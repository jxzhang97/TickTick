from ticktick_telegram_assistant.integrations.ticktick_client import TickTickClient


def test_ticktick_client_exposes_core_methods() -> None:
    client = TickTickClient(base_url="https://developer.ticktick.com")
    assert callable(client.list_tasks)
    assert callable(client.create_task)
    assert callable(client.update_task)
    assert callable(client.complete_task)
