from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from selenium.common.exceptions import WebDriverException

from naver_restock_monitor.models import (
    FetchErrorKind,
    MonitorSettings,
    Product,
    StockState,
    Store,
)
from naver_restock_monitor.stock_client import (
    SeleniumStockClient,
    StockClientError,
    build_chrome_options,
    parse_product_payload,
)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"soldout": True, "productStatusType": "OUTOFSTOCK"}, StockState.OUT_OF_STOCK),
        ({"soldout": False, "productStatusType": "SALE"}, StockState.IN_STOCK),
        ({"soldout": False}, StockState.UNKNOWN),
        ({"productStatusType": "SALE"}, StockState.UNKNOWN),
        ({"soldout": "false", "productStatusType": "SALE"}, StockState.UNKNOWN),
        ({"soldout": False, "productStatusType": 1}, StockState.UNKNOWN),
        ({"soldout": False, "productStatusType": "WAIT"}, StockState.UNKNOWN),
        ({"soldout": True, "productStatusType": "SALE"}, StockState.UNKNOWN),
        ([], StockState.UNKNOWN),
    ],
)
def test_parse_product_payload_is_conservative(
    payload: object, expected: StockState
) -> None:
    assert parse_product_payload(payload).state is expected


class FakeDriver:
    def __init__(self, responses: Iterator[object], *, fail_get: bool = False) -> None:
        self.responses = responses
        self.fail_get = fail_get
        self.quit_called = False

    def set_page_load_timeout(self, _value: float) -> None: ...

    def set_script_timeout(self, _value: float) -> None: ...

    def get(self, _url: str) -> None:
        if self.fail_get:
            raise WebDriverException("setup failed")

    def execute_async_script(self, _script: str, *_args: Any) -> object:
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value

    def quit(self) -> None:
        self.quit_called = True


def make_client(driver: FakeDriver, *, attempts: int = 1) -> SeleniumStockClient:
    return SeleniumStockClient(
        Store("example", "channel"),
        MonitorSettings(
            interval_min_seconds=60,
            interval_max_seconds=60,
            api_max_attempts=attempts,
            session_setup_wait_seconds=0,
        ),
        Product("123", "상품"),
        driver_factory=lambda: driver,
        sleep=lambda _seconds: None,
    )


@pytest.mark.parametrize(
    ("raw", "kind", "status"),
    [
        (
            {"ok": False, "status": 429, "retryAfter": "120"},
            FetchErrorKind.RATE_LIMITED,
            429,
        ),
        ({"ok": False, "status": 401}, FetchErrorKind.AUTHORIZATION, 401),
        ({"ok": False, "status": 403}, FetchErrorKind.AUTHORIZATION, 403),
        ({"ok": False, "status": 500}, FetchErrorKind.SERVER, 500),
        ({"ok": False, "status": 503}, FetchErrorKind.SERVER, 503),
    ],
)
def test_http_statuses_are_classified(
    raw: dict[str, object], kind: FetchErrorKind, status: int
) -> None:
    driver = FakeDriver(iter([raw]))
    client = make_client(driver)
    client.start()
    result = client.fetch("123")
    assert result.error_kind is kind
    assert result.http_status == status
    client.close()


def test_transient_server_error_is_retried() -> None:
    driver = FakeDriver(
        iter(
            [
                {"ok": False, "status": 500},
                {"ok": True, "data": {"soldout": False, "productStatusType": "SALE"}},
            ]
        )
    )
    client = make_client(driver, attempts=2)
    client.start()
    assert client.fetch("123").state is StockState.IN_STOCK
    client.close()


def test_driver_is_closed_when_session_setup_fails() -> None:
    driver = FakeDriver(iter(()), fail_get=True)
    client = make_client(driver)
    with pytest.raises(StockClientError):
        client.start()
    assert driver.quit_called is True


def test_driver_context_closes_after_exception() -> None:
    driver = FakeDriver(iter(()))
    client = make_client(driver)
    with pytest.raises(RuntimeError), client:
        raise RuntimeError("test")
    assert driver.quit_called is True


def test_visible_chrome_is_available_without_stealth_flags() -> None:
    visible = build_chrome_options(headless=False).arguments
    headless = build_chrome_options(headless=True).arguments
    assert "--headless=new" not in visible
    assert "--headless=new" in headless
    assert not any(
        "AutomationControlled" in argument for argument in visible + headless
    )


def test_chrome_binary_path_is_applied() -> None:
    options = build_chrome_options(headless=False, chrome_binary="/usr/bin/chromium")
    assert options.binary_location == "/usr/bin/chromium"


def test_fetch_restores_normal_korean_language_header() -> None:
    assert "'Accept-Language': 'ko-KR,ko;q=0.9'" in SeleniumStockClient._FETCH_SCRIPT
