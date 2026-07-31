from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any, Protocol

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from .models import (
    FetchErrorKind,
    FetchResult,
    MonitorSettings,
    Product,
    StockState,
    Store,
)

LOGGER = logging.getLogger(__name__)


class Driver(Protocol):
    def set_page_load_timeout(self, time_to_wait: float) -> None: ...

    def set_script_timeout(self, time_to_wait: float) -> None: ...

    def get(self, url: str) -> None: ...

    def execute_async_script(self, script: str, *args: Any) -> Any: ...

    def quit(self) -> None: ...


class StockClientError(RuntimeError):
    pass


def parse_product_payload(data: object) -> FetchResult:
    """Parse only field combinations confirmed by the original implementation."""
    if not isinstance(data, dict):
        return FetchResult(
            StockState.UNKNOWN,
            "API 응답이 객체 형식이 아닙니다.",
            FetchErrorKind.INVALID_RESPONSE,
        )

    if "soldout" not in data or "productStatusType" not in data:
        return FetchResult(
            StockState.UNKNOWN,
            "필수 재고 필드가 누락됐습니다.",
            FetchErrorKind.INVALID_RESPONSE,
        )
    soldout = data["soldout"]
    status = data["productStatusType"]
    if type(soldout) is not bool or not isinstance(status, str):
        return FetchResult(
            StockState.UNKNOWN,
            "재고 필드의 자료형이 예상과 다릅니다.",
            FetchErrorKind.INVALID_RESPONSE,
        )

    if soldout is True and status == "OUTOFSTOCK":
        return FetchResult(StockState.OUT_OF_STOCK, "품절 상태가 두 필드에서 확인됨")
    if soldout is False and status == "SALE":
        return FetchResult(StockState.IN_STOCK, "판매 가능 상태가 두 필드에서 확인됨")
    if (soldout is True and status == "SALE") or (
        soldout is False and status == "OUTOFSTOCK"
    ):
        return FetchResult(
            StockState.UNKNOWN,
            "두 재고 필드가 서로 모순됩니다.",
            FetchErrorKind.INVALID_RESPONSE,
        )
    return FetchResult(
        StockState.UNKNOWN,
        f"지원하지 않는 상품 상태입니다: {status}",
        FetchErrorKind.INVALID_RESPONSE,
    )


def build_chrome_options(
    *, headless: bool, chrome_binary: str | None = None
) -> Options:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1280,800")
    if chrome_binary is not None:
        options.binary_location = chrome_binary
    return options


def default_driver_factory(settings: MonitorSettings) -> Driver:
    options = build_chrome_options(
        headless=settings.headless,
        chrome_binary=settings.chrome_binary,
    )
    if settings.chromedriver_path is not None:
        service = Service(executable_path=settings.chromedriver_path)
        return webdriver.Chrome(service=service, options=options)
    return webdriver.Chrome(options=options)


class SeleniumStockClient:
    """Use a normal browser session for the same-origin Brand Store API call."""

    _FETCH_SCRIPT = """
        const [path, timeoutMs, done] = arguments;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        fetch(path, {
            method: 'GET',
            headers: {
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'ko-KR,ko;q=0.9'
            },
            credentials: 'include',
            signal: controller.signal
        }).then(async response => {
            clearTimeout(timer);
            const retryAfter = response.headers.get('Retry-After');
            if (!response.ok) {
                done({ok: false, status: response.status, retryAfter});
                return;
            }
            try {
                done({ok: true, data: await response.json()});
            } catch (_) {
                done({ok: false, parseError: true});
            }
        }).catch(error => {
            clearTimeout(timer);
            done({ok: false, transportError: error.name || 'fetch failed'});
        });
    """

    def __init__(
        self,
        store: Store,
        settings: MonitorSettings,
        first_product: Product,
        *,
        driver_factory: Callable[[], Driver] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.first_product = first_product
        self._driver_factory = driver_factory or (
            lambda: default_driver_factory(settings)
        )
        self._sleep = sleep
        self._random = random_source or random.Random()
        self._driver: Driver | None = None

    def start(self) -> None:
        self.close()
        try:
            driver = self._driver_factory()
            self._driver = driver
            driver.set_page_load_timeout(self.settings.api_timeout_seconds + 15)
            driver.set_script_timeout(self.settings.api_timeout_seconds + 5)
            driver.get(self.store.product_url(self.first_product.id))
            self._sleep(self.settings.session_setup_wait_seconds)
        except WebDriverException as exc:
            self.close()
            raise StockClientError(
                "Chrome 세션을 시작하지 못했습니다. "
                "Chrome과 WebDriver 설정을 확인하세요."
            ) from exc
        except Exception:
            self.close()
            raise

    def restart(self) -> None:
        self.close()
        self.start()

    def close(self) -> None:
        driver, self._driver = self._driver, None
        if driver is None:
            return
        try:
            driver.quit()
        except WebDriverException:
            LOGGER.debug("Chrome 종료 중 오류가 발생했습니다.", exc_info=True)

    def fetch(self, product_id: str) -> FetchResult:
        if self._driver is None:
            raise StockClientError("Chrome 세션이 시작되지 않았습니다.")

        last_result = FetchResult(
            StockState.UNKNOWN,
            "상품 정보를 가져오지 못했습니다.",
            FetchErrorKind.TRANSPORT,
        )
        for attempt in range(1, self.settings.api_max_attempts + 1):
            last_result = self._fetch_once(product_id)
            retryable = last_result.error_kind in {
                FetchErrorKind.SERVER,
                FetchErrorKind.TRANSPORT,
            }
            if not retryable or attempt == self.settings.api_max_attempts:
                return last_result
            delay = min(
                self.settings.backoff_max_seconds,
                self.settings.backoff_base_seconds * (2 ** (attempt - 1)),
            )
            self._sleep(delay + self._random.uniform(0, delay * 0.25))
        return last_result

    def _fetch_once(self, product_id: str) -> FetchResult:
        assert self._driver is not None
        path = (
            f"/n/v2/channels/{self.store.channel_id}/products/{product_id}"
            "?withWindow=false"
        )
        try:
            raw = self._driver.execute_async_script(
                self._FETCH_SCRIPT,
                path,
                int(self.settings.api_timeout_seconds * 1000),
            )
        except WebDriverException:
            return FetchResult(
                StockState.UNKNOWN,
                "브라우저에서 API 요청을 완료하지 못했습니다.",
                FetchErrorKind.TRANSPORT,
            )
        if not isinstance(raw, dict):
            return FetchResult(
                StockState.UNKNOWN,
                "브라우저 API 응답 형식이 올바르지 않습니다.",
                FetchErrorKind.INVALID_RESPONSE,
            )
        if raw.get("ok") is True:
            return parse_product_payload(raw.get("data"))

        status = raw.get("status")
        retry_after = _parse_retry_after(raw.get("retryAfter"))
        if status == 429:
            return FetchResult(
                StockState.UNKNOWN,
                "요청 제한(HTTP 429)을 감지했습니다.",
                FetchErrorKind.RATE_LIMITED,
                429,
                retry_after,
            )
        if status in {401, 403}:
            return FetchResult(
                StockState.UNKNOWN,
                f"접근 또는 인증 오류(HTTP {status})가 발생했습니다.",
                FetchErrorKind.AUTHORIZATION,
                status,
            )
        if isinstance(status, int) and 500 <= status <= 599:
            return FetchResult(
                StockState.UNKNOWN,
                f"네이버 서버 오류(HTTP {status})가 발생했습니다.",
                FetchErrorKind.SERVER,
                status,
            )
        if raw.get("parseError") is True:
            kind = FetchErrorKind.INVALID_RESPONSE
            detail = "API JSON 응답을 해석하지 못했습니다."
        else:
            kind = FetchErrorKind.TRANSPORT
            detail = "브라우저 API 요청이 실패했습니다."
        return FetchResult(StockState.UNKNOWN, detail, kind, status)

    def __enter__(self) -> SeleniumStockClient:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _parse_retry_after(value: object) -> float | None:
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None
