import asyncio

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


class BlockingPoller:
    def __init__(self, release_event: asyncio.Event) -> None:
        self.release_event = release_event
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.offsets: list[int | None] = []

    async def poll_once(self, offset: int | None) -> int | None:
        self.offsets.append(offset)
        self.started.set()
        await self.release_event.wait()
        self.finished.set()
        return 55


class FlakyPoller:
    def __init__(self) -> None:
        self.offsets: list[int | None] = []

    async def poll_once(self, offset: int | None) -> int | None:
        self.offsets.append(offset)
        if len(self.offsets) == 1:
            raise RuntimeError("transient polling failure")
        return 55


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


@pytest.mark.asyncio
async def test_local_runner_keeps_running_reminders_while_polling_is_blocked() -> None:
    from ticktick_telegram_assistant.runner import LocalAssistantRunner

    release_event = asyncio.Event()
    telegram_client = FakeTelegramClient()
    poller = BlockingPoller(release_event)
    reminder_worker = FakeReminderWorker()
    runner = LocalAssistantRunner(
        telegram_client=telegram_client,
        poller=poller,
        reminder_worker=reminder_worker,
    )

    run_task = asyncio.create_task(runner.run_forever(iterations=1, sleep_seconds=0))
    try:
        await asyncio.wait_for(poller.started.wait(), timeout=1)
        await asyncio.wait_for(asyncio.sleep(0), timeout=1)
        assert reminder_worker.runs == 1
        assert poller.finished.is_set() is False
        release_event.set()
        await asyncio.wait_for(run_task, timeout=1)
    finally:
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)

    assert telegram_client.deleted is True
    assert poller.offsets == [None]
    assert reminder_worker.runs == 1
    assert runner.last_update_offset == 55


@pytest.mark.asyncio
async def test_local_runner_keeps_running_reminders_when_polling_fails_transiently() -> None:
    from ticktick_telegram_assistant.runner import LocalAssistantRunner

    telegram_client = FakeTelegramClient()
    poller = FlakyPoller()
    reminder_worker = FakeReminderWorker()
    runner = LocalAssistantRunner(
        telegram_client=telegram_client,
        poller=poller,
        reminder_worker=reminder_worker,
    )

    await runner.run_forever(iterations=2, sleep_seconds=0)

    assert telegram_client.deleted is True
    assert poller.offsets == [None, None]
    assert reminder_worker.runs == 2
    assert runner.last_update_offset == 55


@pytest.mark.asyncio
async def test_local_runner_bootstrap_loads_saved_offset_and_run_once_persists_it(tmp_path) -> None:
    from ticktick_telegram_assistant.runner import LocalAssistantRunner

    telegram_client = FakeTelegramClient()
    poller = FakePoller()
    reminder_worker = FakeReminderWorker()
    state_path = tmp_path / "telegram_offset.txt"
    state_path.write_text("777\n")
    runner = LocalAssistantRunner(
        telegram_client=telegram_client,
        poller=poller,
        reminder_worker=reminder_worker,
        offset_state_path=state_path,
    )

    await runner.bootstrap()
    await runner.run_once()

    assert telegram_client.deleted is True
    assert poller.offsets == [777]
    assert state_path.read_text() == "55\n"
    assert runner.last_update_offset == 55
