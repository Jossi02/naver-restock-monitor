from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from naver_restock_monitor.models import (
    Alert,
    FetchErrorKind,
    FetchResult,
    NotificationSettings,
    StockState,
)
from naver_restock_monitor.monitor import CooldownActiveError, RestockMonitor
from naver_restock_monitor.notifiers import NotificationDispatcher, NotificationError
from naver_restock_monitor.state_store import JsonStateStore

from .helpers import make_config

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))


class FakeNotifier:
    name = "discord"

    def __init__(self, outcomes: list[Exception | None] | None = None) -> None:
        self.outcomes = list(outcomes or [None])
        self.sent: list[Alert] = []
        self.closed = False

    def send(self, alert: Alert) -> None:
        self.sent.append(alert)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is not None:
            raise outcome

    def close(self) -> None:
        self.closed = True


class UnusedClient:
    def start(self) -> None: ...

    def restart(self) -> None: ...

    def close(self) -> None: ...

    def fetch(self, _product_id: str) -> FetchResult:
        raise AssertionError("not used")


class SequenceClient:
    def __init__(self, results: dict[str, FetchResult]) -> None:
        self.results = results
        self.closed = False
        self.start_count = 0

    def start(self) -> None:
        self.start_count += 1

    def restart(self) -> None: ...

    def close(self) -> None:
        self.closed = True

    def fetch(self, product_id: str) -> FetchResult:
        return self.results[product_id]


def make_monitor(
    path: Path,
    notifier: FakeNotifier,
    *,
    notify_initial: bool = False,
    settings: NotificationSettings | None = None,
) -> RestockMonitor:
    config = make_config(
        path,
        notify_initial=notify_initial,
        notifications=settings,
    )
    dispatcher = NotificationDispatcher(
        [notifier], config.notifications, sleep=lambda _seconds: None
    )
    return RestockMonitor(
        config,
        UnusedClient(),
        dispatcher,
        JsonStateStore(config.state_file),
        now=lambda: NOW,
    )


def observe(monitor: RestockMonitor, state: StockState) -> bool:
    return monitor.process_observation(
        monitor.config.products[0], FetchResult(state, "test"), NOW
    )


def test_out_of_stock_to_in_stock_alerts_once(tmp_path: Path) -> None:
    notifier = FakeNotifier([None])
    monitor = make_monitor(tmp_path, notifier)
    assert observe(monitor, StockState.OUT_OF_STOCK) is False
    assert observe(monitor, StockState.IN_STOCK) is True
    assert observe(monitor, StockState.IN_STOCK) is False
    assert len(notifier.sent) == 1


def test_in_stock_to_in_stock_has_no_duplicate(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    monitor = make_monitor(tmp_path, notifier)
    observe(monitor, StockState.IN_STOCK)
    observe(monitor, StockState.IN_STOCK)
    assert notifier.sent == []


def test_unknown_preserves_confirmed_out_of_stock(tmp_path: Path) -> None:
    notifier = FakeNotifier([None])
    monitor = make_monitor(tmp_path, notifier)
    observe(monitor, StockState.OUT_OF_STOCK)
    observe(monitor, StockState.UNKNOWN)
    state = monitor.snapshot.products["123"]
    assert state.confirmed_state is StockState.OUT_OF_STOCK
    assert observe(monitor, StockState.IN_STOCK) is True
    assert len(notifier.sent) == 1


def test_initial_in_stock_is_configurable(tmp_path: Path) -> None:
    silent = FakeNotifier()
    assert observe(make_monitor(tmp_path / "off", silent), StockState.IN_STOCK) is False
    enabled = FakeNotifier([None])
    assert (
        observe(
            make_monitor(tmp_path / "on", enabled, notify_initial=True),
            StockState.IN_STOCK,
        )
        is True
    )
    assert len(enabled.sent) == 1


def test_notification_retries_then_succeeds(tmp_path: Path) -> None:
    settings = NotificationSettings(
        discord_enabled=True,
        max_immediate_attempts=3,
        max_total_attempts=5,
    )
    notifier = FakeNotifier([NotificationError("temporary"), None])
    monitor = make_monitor(tmp_path, notifier, settings=settings)
    observe(monitor, StockState.OUT_OF_STOCK)
    observe(monitor, StockState.IN_STOCK)
    assert len(notifier.sent) == 2
    assert monitor.snapshot.pending == {}


def test_pending_alert_is_deduplicated_and_bounded(tmp_path: Path) -> None:
    settings = NotificationSettings(
        discord_enabled=True,
        max_immediate_attempts=1,
        max_total_attempts=3,
        max_pending_alerts=1,
    )
    notifier = FakeNotifier([NotificationError("fail"), NotificationError("fail")])
    monitor = make_monitor(tmp_path, notifier, settings=settings)
    observe(monitor, StockState.OUT_OF_STOCK)
    observe(monitor, StockState.IN_STOCK)
    observe(monitor, StockState.OUT_OF_STOCK)
    observe(monitor, StockState.IN_STOCK)
    assert list(monitor.snapshot.pending) == ["123"]


def test_pending_alert_stops_after_max_total_attempts(tmp_path: Path) -> None:
    settings = NotificationSettings(
        discord_enabled=True,
        max_immediate_attempts=1,
        max_total_attempts=2,
    )
    notifier = FakeNotifier([NotificationError("first"), NotificationError("final")])
    monitor = make_monitor(tmp_path, notifier, settings=settings)
    observe(monitor, StockState.OUT_OF_STOCK)
    observe(monitor, StockState.IN_STOCK)
    pending = monitor.snapshot.pending["123"]
    pending.next_attempt_at = NOW.isoformat()
    monitor.retry_pending(NOW)
    assert monitor.snapshot.pending == {}


def test_one_product_failure_is_not_cleared_by_another_success(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    second = type(config.products[0])("456", "정상 상품")
    config = type(config)(
        store=config.store,
        products=(config.products[0], second),
        monitor=config.monitor,
        notifications=config.notifications,
        logging=config.logging,
        state_file=config.state_file,
        discord_webhook_url=config.discord_webhook_url,
    )
    client = SequenceClient(
        {
            "123": FetchResult(StockState.UNKNOWN, "failed"),
            "456": FetchResult(StockState.OUT_OF_STOCK, "ok"),
        }
    )
    notifier = FakeNotifier()
    monitor = RestockMonitor(
        config,
        client,
        NotificationDispatcher([notifier], config.notifications),
        JsonStateStore(config.state_file),
        sleep=lambda _seconds: None,
        now=lambda: NOW,
    )
    summary = monitor.run_cycle()
    assert summary.confirmed == 1
    assert monitor.snapshot.products["123"].consecutive_failures == 1
    assert monitor.snapshot.products["456"].consecutive_failures == 0


def test_run_closes_client_after_unexpected_fetch_exception(tmp_path: Path) -> None:
    class ExplodingClient(SequenceClient):
        def fetch(self, product_id: str) -> FetchResult:
            raise RuntimeError(product_id)

    config = make_config(tmp_path)
    client = ExplodingClient({})
    notifier = FakeNotifier()
    monitor = RestockMonitor(
        config,
        client,
        NotificationDispatcher([notifier], config.notifications),
        JsonStateStore(config.state_file),
    )
    import pytest

    with pytest.raises(RuntimeError):
        monitor.run(once=True)
    assert client.closed is True
    assert notifier.closed is True


def test_once_persists_rate_limit_and_reports_cooldown(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = SequenceClient(
        {
            "123": FetchResult(
                StockState.UNKNOWN,
                "rate limited",
                FetchErrorKind.RATE_LIMITED,
                429,
            )
        }
    )
    notifier = FakeNotifier()
    monitor = RestockMonitor(
        config,
        client,
        NotificationDispatcher([notifier], config.notifications),
        JsonStateStore(config.state_file),
        now=lambda: NOW,
    )
    import pytest

    with pytest.raises(CooldownActiveError):
        monitor.run(once=True)
    assert monitor.snapshot.blocked_until == "2026-01-01T12:30:00+09:00"


def test_saved_rate_limit_prevents_new_browser_session(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = JsonStateStore(config.state_file)
    snapshot = store.load()
    snapshot.blocked_until = "2026-01-01T12:30:00+09:00"
    store.save(snapshot)
    client = SequenceClient({})
    notifier = FakeNotifier()
    monitor = RestockMonitor(
        config,
        client,
        NotificationDispatcher([notifier], config.notifications),
        store,
        now=lambda: NOW,
    )
    import pytest

    with pytest.raises(CooldownActiveError):
        monitor.run(once=True)
    assert client.start_count == 0
