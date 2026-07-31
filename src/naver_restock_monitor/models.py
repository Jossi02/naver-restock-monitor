from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class StockState(StrEnum):
    UNKNOWN = "unknown"
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"


class FetchErrorKind(StrEnum):
    NONE = "none"
    RATE_LIMITED = "rate_limited"
    AUTHORIZATION = "authorization"
    SERVER = "server"
    TRANSPORT = "transport"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class Product:
    id: str
    name: str


@dataclass(frozen=True)
class Store:
    slug: str
    channel_id: str

    def product_url(self, product_id: str, *, mobile: bool = False) -> str:
        host = "m.brand.naver.com" if mobile else "brand.naver.com"
        return f"https://{host}/{self.slug}/products/{product_id}"


@dataclass(frozen=True)
class MonitorSettings:
    interval_min_seconds: float = 300
    interval_max_seconds: float = 600
    between_products_min_seconds: float = 2
    between_products_max_seconds: float = 5
    api_timeout_seconds: float = 15
    api_max_attempts: int = 2
    backoff_base_seconds: float = 2
    backoff_max_seconds: float = 30
    session_setup_wait_seconds: float = 4
    headless: bool = False
    chrome_binary: str | None = None
    chromedriver_path: str | None = None
    session_refresh_after_cycles: int = 100
    session_failure_threshold: int = 3
    cooldown_seconds: float = 900
    rate_limit_cooldown_seconds: float = 1800
    notify_initial_in_stock: bool = False
    min_alert_interval_seconds: float = 3600
    timezone: str = "Asia/Seoul"


@dataclass(frozen=True)
class NotificationSettings:
    discord_enabled: bool = False
    telegram_enabled: bool = False
    max_immediate_attempts: int = 3
    max_total_attempts: int = 6
    retry_base_seconds: float = 2
    retry_max_seconds: float = 60
    max_pending_alerts: int = 100


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"
    file: str = "var/monitor.log"
    max_bytes: int = 2_000_000
    backup_count: int = 3


@dataclass(frozen=True)
class AppConfig:
    store: Store
    products: tuple[Product, ...]
    monitor: MonitorSettings
    notifications: NotificationSettings
    logging: LoggingSettings
    state_file: str
    discord_webhook_url: str | None = field(default=None, repr=False)
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class FetchResult:
    state: StockState
    detail: str
    error_kind: FetchErrorKind = FetchErrorKind.NONE
    http_status: int | None = None
    retry_after_seconds: float | None = None


@dataclass
class ProductState:
    confirmed_state: StockState = StockState.UNKNOWN
    last_observed_state: StockState = StockState.UNKNOWN
    last_checked_at: str | None = None
    last_alert_at: str | None = None
    consecutive_failures: int = 0


@dataclass
class PendingAlert:
    product_id: str
    product_name: str
    product_url: str
    occurred_at: str
    channel_attempts: dict[str, int]
    next_attempt_at: str


@dataclass
class StateSnapshot:
    version: int = 1
    products: dict[str, ProductState] = field(default_factory=dict)
    pending: dict[str, PendingAlert] = field(default_factory=dict)
    blocked_until: str | None = None


@dataclass(frozen=True)
class Alert:
    product_id: str
    product_name: str
    product_url: str
    occurred_at: str
    is_test: bool = False
