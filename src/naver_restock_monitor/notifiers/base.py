from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from ..models import Alert, NotificationSettings


class NotificationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class Notifier(Protocol):
    name: str

    def send(self, alert: Alert) -> None: ...

    def close(self) -> None: ...


@dataclass
class DeliveryResult:
    successes: set[str] = field(default_factory=set)
    failures: dict[str, DeliveryFailure] = field(default_factory=dict)

    @property
    def failed_attempts(self) -> dict[str, int]:
        return {channel: failure.attempts for channel, failure in self.failures.items()}

    @property
    def errors(self) -> dict[str, str]:
        return {
            channel: str(failure.error) for channel, failure in self.failures.items()
        }

    @property
    def retryable_failures(self) -> dict[str, DeliveryFailure]:
        return {
            channel: failure
            for channel, failure in self.failures.items()
            if failure.error.retryable
        }


@dataclass(frozen=True)
class DeliveryFailure:
    attempts: int
    error: NotificationError


class NotificationDispatcher:
    def __init__(
        self,
        notifiers: Iterable[Notifier],
        settings: NotificationSettings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        self.notifiers = {notifier.name: notifier for notifier in notifiers}
        self.settings = settings
        self._sleep = sleep
        self._random = random_source or random.Random()

    @property
    def channel_names(self) -> set[str]:
        return set(self.notifiers)

    def send_with_retry(
        self,
        alert: Alert,
        channels: Iterable[str] | None = None,
        stop_event: threading.Event | None = None,
    ) -> DeliveryResult:
        selected = set(channels) if channels is not None else self.channel_names
        result = DeliveryResult()
        for channel in sorted(selected):
            if stop_event is not None and stop_event.is_set():
                result.failures[channel] = DeliveryFailure(
                    0, NotificationError("종료 요청으로 알림을 보류했습니다.")
                )
                continue
            notifier = self.notifiers.get(channel)
            if notifier is None:
                result.failures[channel] = DeliveryFailure(
                    0,
                    NotificationError("활성화되지 않은 알림 채널", retryable=False),
                )
                continue
            attempts = 0
            last_error: NotificationError | None = None
            for attempt in range(1, self.settings.max_immediate_attempts + 1):
                if stop_event is not None and stop_event.is_set():
                    last_error = NotificationError("종료 요청으로 알림을 보류했습니다.")
                    break
                attempts = attempt
                try:
                    notifier.send(alert)
                except NotificationError as exc:
                    last_error = exc
                    if (
                        not exc.retryable
                        or exc.retry_after_seconds is not None
                        or attempt == self.settings.max_immediate_attempts
                    ):
                        break
                    delay = self._retry_delay(attempt)
                    if stop_event is None:
                        self._sleep(delay)
                    elif stop_event.wait(delay):
                        last_error = NotificationError(
                            "종료 요청으로 알림을 보류했습니다."
                        )
                        break
                else:
                    result.successes.add(channel)
                    break
            if channel not in result.successes:
                assert last_error is not None
                result.failures[channel] = DeliveryFailure(attempts, last_error)
        return result

    def send_once(self, alert: Alert, channel: str) -> NotificationError | None:
        notifier = self.notifiers.get(channel)
        if notifier is None:
            return NotificationError("활성화되지 않은 알림 채널", retryable=False)
        try:
            notifier.send(alert)
        except NotificationError as exc:
            return exc
        return None

    def pending_retry_delay(self, attempts: int) -> float:
        return self._retry_delay(max(1, attempts))

    def _retry_delay(self, attempts: int) -> float:
        base = min(
            self.settings.retry_max_seconds,
            self.settings.retry_base_seconds * (2 ** (attempts - 1)),
        )
        return float(base + self._random.uniform(0, base * 0.25))

    def close(self) -> None:
        for notifier in self.notifiers.values():
            notifier.close()
