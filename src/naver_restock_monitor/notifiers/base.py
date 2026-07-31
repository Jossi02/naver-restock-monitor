from __future__ import annotations

import random
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
    failed_attempts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


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
    ) -> DeliveryResult:
        selected = set(channels) if channels is not None else self.channel_names
        result = DeliveryResult()
        for channel in sorted(selected):
            notifier = self.notifiers.get(channel)
            if notifier is None:
                result.failed_attempts[channel] = 0
                result.errors[channel] = "활성화되지 않은 알림 채널"
                continue
            attempts = 0
            for attempt in range(1, self.settings.max_immediate_attempts + 1):
                attempts = attempt
                try:
                    notifier.send(alert)
                except NotificationError as exc:
                    result.errors[channel] = str(exc)
                    if (
                        not exc.retryable
                        or exc.retry_after_seconds is not None
                        or attempt == self.settings.max_immediate_attempts
                    ):
                        break
                    self._sleep(self._retry_delay(attempt))
                else:
                    result.successes.add(channel)
                    break
            if channel not in result.successes:
                result.failed_attempts[channel] = attempts
        return result

    def send_once(self, alert: Alert, channel: str) -> str | None:
        notifier = self.notifiers.get(channel)
        if notifier is None:
            return "활성화되지 않은 알림 채널"
        try:
            notifier.send(alert)
        except NotificationError as exc:
            return str(exc)
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
