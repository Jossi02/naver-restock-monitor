from __future__ import annotations

from datetime import datetime

import requests

from ..models import Alert
from .base import NotificationError


class DiscordNotifier:
    name = "discord"

    def __init__(
        self,
        webhook_url: str,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self._webhook_url = webhook_url
        self._session = session or requests.Session()
        self._owns_session = session is None
        self._timeout = timeout_seconds

    def send(self, alert: Alert) -> None:
        payload = self._payload(alert)
        try:
            response = self._session.post(
                self._webhook_url, json=payload, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise NotificationError("Discord 연결 오류") from exc
        if 200 <= response.status_code < 300:
            return
        retry_after = _retry_after(response)
        retryable = response.status_code == 429 or response.status_code >= 500
        raise NotificationError(
            f"Discord HTTP {response.status_code}",
            retryable=retryable,
            retry_after_seconds=retry_after,
        )

    @staticmethod
    def _payload(alert: Alert) -> dict[str, object]:
        title = "알림 채널 테스트" if alert.is_test else "재입고 알림"
        description = (
            "설정한 Discord 알림이 정상적으로 작동합니다."
            if alert.is_test
            else (
                f"**{alert.product_name}** 상품이 다시 판매 가능한 상태로 확인됐습니다."
            )
        )
        return {
            "username": "네이버 재입고 모니터",
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "url": alert.product_url,
                    "color": 0x00C73C,
                    "fields": [
                        {
                            "name": "확인 시각",
                            "value": _display_time(alert.occurred_at),
                            "inline": True,
                        }
                    ],
                    "footer": {"text": "비공식 개인용 모니터"},
                }
            ],
        }

    def close(self) -> None:
        if self._owns_session:
            self._session.close()


def _display_time(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S %Z")
    except ValueError:
        return value


def _retry_after(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
