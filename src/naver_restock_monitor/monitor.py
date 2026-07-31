from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from .models import (
    Alert,
    AppConfig,
    FetchErrorKind,
    FetchResult,
    PendingAlert,
    Product,
    ProductState,
    StockState,
)
from .notifiers import NotificationDispatcher
from .state_store import JsonStateStore
from .stock_client import StockClientError

LOGGER = logging.getLogger(__name__)


class StockClient(Protocol):
    def start(self) -> None: ...

    def restart(self) -> None: ...

    def close(self) -> None: ...

    def fetch(self, product_id: str) -> FetchResult: ...


class CooldownActiveError(RuntimeError):
    def __init__(self, remaining_seconds: float, blocked_until: str) -> None:
        super().__init__(
            f"요청 제한 쿨다운 중입니다. 약 {remaining_seconds:.0f}초 후 "
            f"다시 시도할 수 있습니다. (해제 시각: {blocked_until})"
        )
        self.remaining_seconds = remaining_seconds
        self.blocked_until = blocked_until


@dataclass(frozen=True)
class CycleSummary:
    checked: int
    confirmed: int
    rate_limited: bool = False
    authorization_failed: bool = False
    retry_after_seconds: float | None = None


def apply_observation(
    state: ProductState,
    observed: StockState,
    now: datetime,
    *,
    notify_initial_in_stock: bool,
    min_alert_interval_seconds: float,
) -> bool:
    """Update state and return whether this observation creates a new alert."""
    state.last_observed_state = observed
    state.last_checked_at = now.isoformat()
    if observed is StockState.UNKNOWN:
        state.consecutive_failures += 1
        return False

    previous = state.confirmed_state
    state.confirmed_state = observed
    state.consecutive_failures = 0
    is_restock = previous is StockState.OUT_OF_STOCK and observed is StockState.IN_STOCK
    is_initial = (
        previous is StockState.UNKNOWN
        and observed is StockState.IN_STOCK
        and notify_initial_in_stock
    )
    if not (is_restock or is_initial):
        return False
    if state.last_alert_at is None:
        return True
    try:
        last_alert = datetime.fromisoformat(state.last_alert_at)
    except ValueError:
        return True
    return (now - last_alert).total_seconds() >= min_alert_interval_seconds


class RestockMonitor:
    def __init__(
        self,
        config: AppConfig,
        client: StockClient,
        dispatcher: NotificationDispatcher,
        store: JsonStateStore,
        *,
        sleep: Callable[[float], None] = time.sleep,
        random_source: random.Random | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.dispatcher = dispatcher
        self.store = store
        self.snapshot = store.load()
        self._sleep = sleep
        self._random = random_source or random.Random()
        zone = ZoneInfo(config.monitor.timezone)
        self._now = now or (lambda: datetime.now(zone))
        if store.last_recovery_path is not None:
            LOGGER.warning(
                "손상된 상태 파일을 격리하고 빈 상태로 복구했습니다: %s",
                store.last_recovery_path,
            )

    def process_observation(
        self, product: Product, result: FetchResult, now: datetime | None = None
    ) -> bool:
        current_time = now or self._now()
        state = self.snapshot.products.setdefault(product.id, ProductState())
        should_alert = apply_observation(
            state,
            result.state,
            current_time,
            notify_initial_in_stock=self.config.monitor.notify_initial_in_stock,
            min_alert_interval_seconds=self.config.monitor.min_alert_interval_seconds,
        )
        if should_alert:
            self._deliver_new_alert(product, current_time)
        self.store.save(self.snapshot)
        return should_alert

    def _deliver_new_alert(self, product: Product, now: datetime) -> None:
        alert = Alert(
            product_id=product.id,
            product_name=product.name,
            product_url=self.config.store.product_url(product.id, mobile=True),
            occurred_at=now.isoformat(),
        )
        result = self.dispatcher.send_with_retry(alert)
        state = self.snapshot.products[product.id]
        if result.successes:
            state.last_alert_at = now.isoformat()
            LOGGER.info(
                "%s 재입고 알림 전송 성공: %s",
                product.name,
                ", ".join(sorted(result.successes)),
            )
        for channel, message in result.errors.items():
            LOGGER.warning("%s 알림 실패(%s): %s", product.name, channel, message)
        if result.failed_attempts:
            self._enqueue(alert, result.failed_attempts, now)

    def _enqueue(
        self, alert: Alert, failed_attempts: dict[str, int], now: datetime
    ) -> None:
        existing = self.snapshot.pending.get(alert.product_id)
        if existing is not None:
            for channel, attempts in failed_attempts.items():
                existing.channel_attempts.setdefault(channel, attempts)
            return
        if len(self.snapshot.pending) >= self.config.notifications.max_pending_alerts:
            LOGGER.error(
                "보류 알림 큐가 가득 차서 %s 알림을 저장하지 못했습니다.",
                alert.product_name,
            )
            return
        delay = self.dispatcher.pending_retry_delay(max(failed_attempts.values()))
        self.snapshot.pending[alert.product_id] = PendingAlert(
            product_id=alert.product_id,
            product_name=alert.product_name,
            product_url=alert.product_url,
            occurred_at=alert.occurred_at,
            channel_attempts=dict(failed_attempts),
            next_attempt_at=(now + timedelta(seconds=delay)).isoformat(),
        )

    def retry_pending(self, now: datetime | None = None) -> None:
        current_time = now or self._now()
        for product_id, pending in list(self.snapshot.pending.items()):
            try:
                due_at = datetime.fromisoformat(pending.next_attempt_at)
            except ValueError:
                due_at = current_time
            if due_at > current_time:
                continue
            alert = Alert(
                product_id=pending.product_id,
                product_name=pending.product_name,
                product_url=pending.product_url,
                occurred_at=pending.occurred_at,
            )
            any_success = False
            for channel, attempts in list(pending.channel_attempts.items()):
                error = self.dispatcher.send_once(alert, channel)
                if error is None:
                    del pending.channel_attempts[channel]
                    any_success = True
                    LOGGER.info(
                        "보류 알림 전송 성공: %s (%s)", pending.product_name, channel
                    )
                    continue
                attempts += 1
                if attempts >= self.config.notifications.max_total_attempts:
                    del pending.channel_attempts[channel]
                    LOGGER.error(
                        "보류 알림 최종 실패: %s (%s, 총 %s회)",
                        pending.product_name,
                        channel,
                        attempts,
                    )
                else:
                    pending.channel_attempts[channel] = attempts
                    LOGGER.warning(
                        "보류 알림 재시도 실패: %s (%s, %s회)",
                        pending.product_name,
                        channel,
                        attempts,
                    )
            if any_success:
                product_state = self.snapshot.products.setdefault(
                    product_id, ProductState()
                )
                product_state.last_alert_at = current_time.isoformat()
            if not pending.channel_attempts:
                del self.snapshot.pending[product_id]
            else:
                highest_attempt = max(pending.channel_attempts.values())
                delay = self.dispatcher.pending_retry_delay(highest_attempt)
                pending.next_attempt_at = (
                    current_time + timedelta(seconds=delay)
                ).isoformat()
        self.store.save(self.snapshot)

    def run_cycle(self, stop_event: threading.Event | None = None) -> CycleSummary:
        self.retry_pending()
        confirmed = 0
        checked = 0
        retry_after: float | None = None
        rate_limited = False
        authorization_failed = False
        for index, product in enumerate(self.config.products):
            if stop_event is not None and stop_event.is_set():
                break
            try:
                result = self.client.fetch(product.id)
            except StockClientError:
                result = FetchResult(
                    StockState.UNKNOWN,
                    "브라우저 세션 오류가 발생했습니다.",
                    FetchErrorKind.TRANSPORT,
                )
            checked += 1
            self.process_observation(product, result)
            if result.state is not StockState.UNKNOWN:
                confirmed += 1
                LOGGER.info("%s: %s", product.name, result.state.value)
            else:
                LOGGER.warning("%s: UNKNOWN - %s", product.name, result.detail)
            if result.error_kind is FetchErrorKind.RATE_LIMITED:
                rate_limited = True
                retry_after = result.retry_after_seconds
                break
            if result.error_kind is FetchErrorKind.AUTHORIZATION:
                authorization_failed = True
                break
            if index < len(self.config.products) - 1:
                delay = self._random.uniform(
                    self.config.monitor.between_products_min_seconds,
                    self.config.monitor.between_products_max_seconds,
                )
                if self._wait(delay, stop_event):
                    break
        return CycleSummary(
            checked=checked,
            confirmed=confirmed,
            rate_limited=rate_limited,
            authorization_failed=authorization_failed,
            retry_after_seconds=retry_after,
        )

    def run(
        self, *, once: bool = False, stop_event: threading.Event | None = None
    ) -> None:
        stop = stop_event or threading.Event()
        cycles = 0
        failed_cycles = 0
        started = False
        try:
            while not stop.is_set():
                remaining = self._cooldown_remaining_seconds()
                if remaining > 0:
                    blocked_until = self.snapshot.blocked_until
                    assert blocked_until is not None
                    if once:
                        raise CooldownActiveError(remaining, blocked_until)
                    LOGGER.warning(
                        "저장된 요청 제한 쿨다운에 따라 %.0f초 동안 요청하지 않습니다.",
                        remaining,
                    )
                    if self._wait(remaining, stop):
                        break
                    self.snapshot.blocked_until = None
                    self.store.save(self.snapshot)
                if not started:
                    try:
                        self.client.start()
                    except StockClientError as exc:
                        if once:
                            raise
                        LOGGER.error("%s", exc)
                        if self._wait(self.config.monitor.cooldown_seconds, stop):
                            break
                        continue
                    started = True
                summary = self.run_cycle(stop)
                cycles += 1

                if summary.rate_limited:
                    delay = max(
                        self.config.monitor.rate_limit_cooldown_seconds,
                        summary.retry_after_seconds or 0,
                    )
                    blocked_until_at = self._now() + timedelta(seconds=delay)
                    self.snapshot.blocked_until = blocked_until_at.isoformat()
                    self.store.save(self.snapshot)
                    LOGGER.warning(
                        "요청 제한 감지: %.0f초 동안 요청을 중단합니다.", delay
                    )
                    if once:
                        raise CooldownActiveError(delay, blocked_until_at.isoformat())
                    if self._wait(delay, stop):
                        break
                    self.snapshot.blocked_until = None
                    self.store.save(self.snapshot)
                    if not self._restart_client():
                        started = False
                    cycles = 0
                    failed_cycles = 0
                    continue
                if once or stop.is_set():
                    break
                if summary.authorization_failed:
                    LOGGER.warning(
                        "접근 오류 감지: %.0f초 동안 요청을 중단합니다.",
                        self.config.monitor.rate_limit_cooldown_seconds,
                    )
                    if self._wait(
                        self.config.monitor.rate_limit_cooldown_seconds, stop
                    ):
                        break
                    if not self._restart_client():
                        started = False
                    cycles = 0
                    failed_cycles = 0
                    continue

                failed_cycles = failed_cycles + 1 if summary.confirmed == 0 else 0
                if failed_cycles >= self.config.monitor.session_failure_threshold:
                    LOGGER.warning(
                        "전체 상품 확인이 %s회 연속 실패했습니다. "
                        "세션을 쉬었다가 재시작합니다.",
                        failed_cycles,
                    )
                    if self._wait(self.config.monitor.cooldown_seconds, stop):
                        break
                    if not self._restart_client():
                        started = False
                    cycles = 0
                    failed_cycles = 0
                    continue
                if cycles >= self.config.monitor.session_refresh_after_cycles:
                    LOGGER.info("정기적으로 Chrome 세션을 재시작합니다.")
                    if not self._restart_client():
                        started = False
                        cycles = 0
                        continue
                    cycles = 0

                delay = self._random.uniform(
                    self.config.monitor.interval_min_seconds,
                    self.config.monitor.interval_max_seconds,
                )
                LOGGER.info("다음 확인까지 %.0f초 대기합니다.", delay)
                if self._wait(delay, stop):
                    break
        finally:
            self.client.close()
            self.dispatcher.close()
            self.store.save(self.snapshot)

    def _wait(self, seconds: float, stop_event: threading.Event | None) -> bool:
        if stop_event is None:
            self._sleep(seconds)
            return False
        return stop_event.wait(seconds)

    def _restart_client(self) -> bool:
        try:
            self.client.restart()
        except StockClientError as exc:
            LOGGER.error("Chrome 세션 재시작 실패: %s", exc)
            return False
        return True

    def _cooldown_remaining_seconds(self) -> float:
        value = self.snapshot.blocked_until
        if value is None:
            return 0
        try:
            blocked_until = datetime.fromisoformat(value)
        except ValueError:
            self.snapshot.blocked_until = None
            self.store.save(self.snapshot)
            return 0
        remaining = (blocked_until - self._now()).total_seconds()
        if remaining <= 0:
            self.snapshot.blocked_until = None
            self.store.save(self.snapshot)
            return 0
        return remaining
