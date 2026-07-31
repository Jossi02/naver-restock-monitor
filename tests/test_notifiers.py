from __future__ import annotations

from typing import Any

import pytest
import requests

from naver_restock_monitor.models import Alert, NotificationSettings
from naver_restock_monitor.notifiers import (
    DiscordNotifier,
    NotificationDispatcher,
    NotificationError,
    TelegramNotifier,
)

ALERT = Alert("1", "<상품>", "https://example.invalid/1", "2026-01-01T00:00:00+09:00")


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
        body: object | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def json(self) -> object:
        return self._body


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_discord_http_errors_are_safely_reported(status: int) -> None:
    session = FakeSession([FakeResponse(status)])
    notifier = DiscordNotifier(
        "https://discord.com/api/webhooks/1/secret",
        session=session,  # type: ignore[arg-type]
    )
    with pytest.raises(NotificationError) as caught:
        notifier.send(ALERT)
    assert str(status) in str(caught.value)
    assert "secret" not in str(caught.value)


def test_telegram_429_captures_retry_after_without_token_leak() -> None:
    session = FakeSession([FakeResponse(429, body={"parameters": {"retry_after": 30}})])
    notifier = TelegramNotifier(
        "token-secret",
        "123",
        session=session,  # type: ignore[arg-type]
    )
    with pytest.raises(NotificationError) as caught:
        notifier.send(ALERT)
    assert caught.value.retry_after_seconds == 30
    assert "token-secret" not in str(caught.value)


def test_request_exception_is_sanitized() -> None:
    session = FakeSession([requests.ConnectionError("contains token-secret")])
    notifier = TelegramNotifier(
        "token-secret",
        "123",
        session=session,  # type: ignore[arg-type]
    )
    with pytest.raises(NotificationError, match="연결 오류") as caught:
        notifier.send(ALERT)
    assert "token-secret" not in str(caught.value)


class OutcomeNotifier:
    def __init__(self, name: str, outcomes: list[Exception | None]) -> None:
        self.name = name
        self.outcomes = list(outcomes)
        self.sent = 0

    def send(self, _alert: Alert) -> None:
        self.sent += 1
        outcome = self.outcomes.pop(0)
        if outcome:
            raise outcome

    def close(self) -> None: ...


def test_channels_retry_independently() -> None:
    discord = OutcomeNotifier("discord", [None])
    telegram = OutcomeNotifier("telegram", [NotificationError("fail"), None])
    settings = NotificationSettings(
        discord_enabled=True,
        telegram_enabled=True,
        max_immediate_attempts=2,
    )
    dispatcher = NotificationDispatcher(
        [discord, telegram], settings, sleep=lambda _seconds: None
    )
    result = dispatcher.send_with_retry(ALERT)
    assert result.successes == {"discord", "telegram"}
    assert discord.sent == 1
    assert telegram.sent == 2


def test_final_notification_failure_is_returned() -> None:
    notifier = OutcomeNotifier(
        "discord", [NotificationError("one"), NotificationError("two")]
    )
    dispatcher = NotificationDispatcher(
        [notifier],
        NotificationSettings(discord_enabled=True, max_immediate_attempts=2),
        sleep=lambda _seconds: None,
    )
    result = dispatcher.send_with_retry(ALERT)
    assert result.failed_attempts == {"discord": 2}
    assert result.successes == set()
