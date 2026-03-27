import pytest


class FakeTelegramClient:
    def __init__(self) -> None:
        self.deleted = False

    async def delete_webhook(self) -> bool:
        self.deleted = True
        return True


class FakePoller:
    def __init__(self) -> None:
        self.offsets: list[int | None] = []

    async def poll_once(self, offset: int | None) -> int | None:
        self.offsets.append(offset)
        return 55


class FakeReminderWorker:
    def __init__(self) -> None:
        self.runs = 0

    async def run_once(self) -> None:
        self.runs += 1


@pytest.mark.asyncio
async def test_local_runner_bootstraps_polling_and_scheduler() -> None:
    from ticktick_telegram_assistant.runner import LocalAssistantRunner

    telegram_client = FakeTelegramClient()
    poller = FakePoller()
    reminder_worker = FakeReminderWorker()
    runner = LocalAssistantRunner(
        telegram_client=telegram_client,
        poller=poller,
        reminder_worker=reminder_worker,
    )

    await runner.bootstrap()
    await runner.run_once()

    assert telegram_client.deleted is True
    assert poller.offsets == [None]
    assert reminder_worker.runs == 1
    assert runner.last_update_offset == 55


@pytest.mark.asyncio
async def test_local_runner_run_forever_executes_requested_iterations() -> None:
    from ticktick_telegram_assistant.runner import LocalAssistantRunner

    telegram_client = FakeTelegramClient()
    poller = FakePoller()
    reminder_worker = FakeReminderWorker()
    runner = LocalAssistantRunner(
        telegram_client=telegram_client,
        poller=poller,
        reminder_worker=reminder_worker,
    )

    await runner.run_forever(iterations=2, sleep_seconds=0)

    assert telegram_client.deleted is True
    assert poller.offsets == [None, 55]
    assert reminder_worker.runs == 2
