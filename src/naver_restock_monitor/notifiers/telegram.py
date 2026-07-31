from __future__ import annotations

import html

import requests

from ..models import Alert
from .base import NotificationError


class TelegramNotifier:
    name = "telegram"

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._session = session or requests.Session()
        self._owns_session = session is None
        self._timeout = timeout_seconds

    def send(self, alert: Alert) -> None:
        if alert.is_test:
            text = (
                "<b>알림 채널 테스트</b>\n\n"
                "설정한 Telegram 알림이 정상적으로 작동합니다."
            )
        else:
            name = html.escape(alert.product_name)
            url = html.escape(alert.product_url, quote=True)
            text = (
                f"<b>재입고 알림</b>\n\n<b>{name}</b> 상품이 다시 판매 가능한 "
                f'상태로 확인됐습니다.\n\n<a href="{url}">상품 페이지 열기</a>'
            )
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        try:
            response = self._session.post(
                self._url, json=payload, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise NotificationError("Telegram 연결 오류") from exc
        if 200 <= response.status_code < 300:
            return
        retry_after = _telegram_retry_after(response)
        retryable = response.status_code == 429 or response.status_code >= 500
        raise NotificationError(
            f"Telegram HTTP {response.status_code}",
            retryable=retryable,
            retry_after_seconds=retry_after,
        )

    def close(self) -> None:
        if self._owns_session:
            self._session.close()


def _telegram_retry_after(response: requests.Response) -> float | None:
    try:
        body = response.json()
    except requests.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    parameters = body.get("parameters")
    if not isinstance(parameters, dict):
        return None
    value = parameters.get("retry_after")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None
