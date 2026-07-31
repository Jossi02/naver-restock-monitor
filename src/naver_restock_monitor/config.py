from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

from .models import (
    AppConfig,
    LoggingSettings,
    MonitorSettings,
    NotificationSettings,
    Product,
    Store,
)


class ConfigError(ValueError):
    """Raised when user configuration is missing or unsafe."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"'{name}' 항목은 키-값 형식이어야 합니다.")
    return value


def _number(data: Mapping[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"'{key}' 값은 숫자여야 합니다.")
    return float(value)


def _integer(data: Mapping[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"'{key}' 값은 정수여야 합니다.")
    return int(value)


def _boolean(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"'{key}' 값은 true 또는 false여야 합니다.")
    return value


def _optional_path(
    data: Mapping[str, Any], key: str, environment_key: str
) -> str | None:
    value = os.getenv(environment_key) or data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConfigError(f"'{key}' 값은 파일 경로 문자열이어야 합니다.")
    return os.path.expanduser(value)


def _resolve(base: Path, value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else (base / path).resolve())


def is_valid_discord_webhook(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"discord.com", "discordapp.com"}
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and re.fullmatch(r"/api/webhooks/\d+/[A-Za-z0-9._-]+", parsed.path) is not None
    )


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"설정 파일을 찾을 수 없습니다: {config_path}")

    load_dotenv(config_path.parent / ".env", override=False)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"설정 파일을 읽을 수 없습니다: {exc}") from exc
    root = _mapping(raw, "root")

    store_raw = _mapping(root.get("store"), "store")
    slug = store_raw.get("slug")
    channel_id = store_raw.get("channel_id")
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        raise ConfigError(
            "'store.slug'는 영문자, 숫자, '_' 또는 '-'만 사용할 수 있습니다."
        )
    if not isinstance(channel_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", channel_id
    ):
        raise ConfigError("'store.channel_id'가 없거나 형식이 올바르지 않습니다.")

    products_raw = root.get("products")
    if not isinstance(products_raw, list) or not products_raw:
        raise ConfigError("'products'에는 상품을 하나 이상 지정해야 합니다.")
    products: list[Product] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(products_raw, 1):
        product = _mapping(item, f"products[{index}]")
        product_id = product.get("id")
        name = product.get("name")
        if not isinstance(product_id, str) or not product_id.isdigit():
            raise ConfigError(f"products[{index}].id는 숫자로 된 문자열이어야 합니다.")
        if product_id in seen_ids:
            raise ConfigError(f"중복된 상품 ID가 있습니다: {product_id}")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"products[{index}].name이 비어 있습니다.")
        seen_ids.add(product_id)
        products.append(Product(id=product_id, name=name.strip()))

    monitor_raw = _mapping(root.get("monitor", {}), "monitor")
    monitor = MonitorSettings(
        interval_min_seconds=_number(monitor_raw, "interval_min_seconds", 300),
        interval_max_seconds=_number(monitor_raw, "interval_max_seconds", 600),
        between_products_min_seconds=_number(
            monitor_raw, "between_products_min_seconds", 2
        ),
        between_products_max_seconds=_number(
            monitor_raw, "between_products_max_seconds", 5
        ),
        api_timeout_seconds=_number(monitor_raw, "api_timeout_seconds", 15),
        api_max_attempts=_integer(monitor_raw, "api_max_attempts", 2),
        backoff_base_seconds=_number(monitor_raw, "backoff_base_seconds", 2),
        backoff_max_seconds=_number(monitor_raw, "backoff_max_seconds", 30),
        session_setup_wait_seconds=_number(
            monitor_raw, "session_setup_wait_seconds", 4
        ),
        headless=_boolean(monitor_raw, "headless", False),
        chrome_binary=_optional_path(monitor_raw, "chrome_binary", "CHROME_BINARY"),
        chromedriver_path=_optional_path(
            monitor_raw, "chromedriver_path", "CHROMEDRIVER_PATH"
        ),
        session_refresh_after_cycles=_integer(
            monitor_raw, "session_refresh_after_cycles", 100
        ),
        session_failure_threshold=_integer(monitor_raw, "session_failure_threshold", 3),
        cooldown_seconds=_number(monitor_raw, "cooldown_seconds", 900),
        rate_limit_cooldown_seconds=_number(
            monitor_raw, "rate_limit_cooldown_seconds", 1800
        ),
        notify_initial_in_stock=_boolean(monitor_raw, "notify_initial_in_stock", False),
        min_alert_interval_seconds=_number(
            monitor_raw, "min_alert_interval_seconds", 3600
        ),
        timezone=str(monitor_raw.get("timezone", "Asia/Seoul")),
    )
    if monitor.interval_min_seconds < 20:
        raise ConfigError(
            "서비스 보호를 위해 interval_min_seconds는 20초 이상이어야 합니다."
        )
    pairs = (
        ("확인 간격", monitor.interval_min_seconds, monitor.interval_max_seconds),
        (
            "상품 사이 간격",
            monitor.between_products_min_seconds,
            monitor.between_products_max_seconds,
        ),
    )
    for label, minimum, maximum in pairs:
        if minimum < 0 or maximum < minimum:
            raise ConfigError(f"{label}의 최솟값과 최댓값을 확인하세요.")
    positive_values = {
        "api_timeout_seconds": monitor.api_timeout_seconds,
        "api_max_attempts": monitor.api_max_attempts,
        "session_refresh_after_cycles": monitor.session_refresh_after_cycles,
        "session_failure_threshold": monitor.session_failure_threshold,
        "cooldown_seconds": monitor.cooldown_seconds,
        "rate_limit_cooldown_seconds": monitor.rate_limit_cooldown_seconds,
    }
    if any(value <= 0 for value in positive_values.values()):
        raise ConfigError("timeout, 시도 횟수, 세션 주기와 쿨다운은 0보다 커야 합니다.")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(monitor.timezone)
    except Exception as exc:
        raise ConfigError(f"알 수 없는 시간대입니다: {monitor.timezone}") from exc

    notification_raw = _mapping(root.get("notifications", {}), "notifications")
    notifications = NotificationSettings(
        discord_enabled=_boolean(notification_raw, "discord_enabled", False),
        telegram_enabled=_boolean(notification_raw, "telegram_enabled", False),
        max_immediate_attempts=_integer(notification_raw, "max_immediate_attempts", 3),
        max_total_attempts=_integer(notification_raw, "max_total_attempts", 6),
        retry_base_seconds=_number(notification_raw, "retry_base_seconds", 2),
        retry_max_seconds=_number(notification_raw, "retry_max_seconds", 60),
        max_pending_alerts=_integer(notification_raw, "max_pending_alerts", 100),
    )
    if not (notifications.discord_enabled or notifications.telegram_enabled):
        raise ConfigError("Discord 또는 Telegram 알림을 하나 이상 활성화해야 합니다.")
    if (
        min(
            notifications.max_immediate_attempts,
            notifications.max_total_attempts,
            notifications.max_pending_alerts,
        )
        <= 0
    ):
        raise ConfigError("알림 시도 횟수와 보류 큐 크기는 0보다 커야 합니다.")
    if notifications.max_total_attempts < notifications.max_immediate_attempts:
        raise ConfigError(
            "max_total_attempts는 max_immediate_attempts 이상이어야 합니다."
        )

    discord_webhook = os.getenv("DISCORD_WEBHOOK_URL") or None
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN") or None
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID") or None
    if notifications.discord_enabled and (
        not discord_webhook or not is_valid_discord_webhook(discord_webhook)
    ):
        raise ConfigError(
            "Discord가 활성화됐지만 DISCORD_WEBHOOK_URL이 없거나 "
            "안전한 HTTPS Webhook 형식이 아닙니다."
        )
    if notifications.telegram_enabled:
        if not telegram_token or not re.fullmatch(
            r"\d{6,12}:[A-Za-z0-9_-]{20,}", telegram_token
        ):
            raise ConfigError(
                "Telegram이 활성화됐지만 TELEGRAM_BOT_TOKEN이 없거나 "
                "형식이 올바르지 않습니다."
            )
        if not telegram_chat_id or not re.fullmatch(
            r"(?:-?\d+|@[A-Za-z0-9_]{5,})", telegram_chat_id
        ):
            raise ConfigError(
                "Telegram이 활성화됐지만 TELEGRAM_CHAT_ID가 없거나 "
                "형식이 올바르지 않습니다."
            )

    logging_raw = _mapping(root.get("logging", {}), "logging")
    level = str(logging_raw.get("level", "INFO")).upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("지원하지 않는 로그 레벨입니다.")
    log_file = logging_raw.get("file", "var/monitor.log")
    if not isinstance(log_file, str) or not log_file:
        raise ConfigError("'logging.file'은 비어 있지 않은 경로여야 합니다.")
    logging_settings = LoggingSettings(
        level=level,
        file=_resolve(config_path.parent, log_file),
        max_bytes=_integer(logging_raw, "max_bytes", 2_000_000),
        backup_count=_integer(logging_raw, "backup_count", 3),
    )

    state_file = root.get("state_file", "var/state.json")
    if not isinstance(state_file, str) or not state_file:
        raise ConfigError("'state_file'은 비어 있지 않은 경로여야 합니다.")

    return AppConfig(
        store=Store(slug=slug, channel_id=channel_id),
        products=tuple(products),
        monitor=monitor,
        notifications=notifications,
        logging=logging_settings,
        state_file=_resolve(config_path.parent, state_file),
        discord_webhook_url=discord_webhook if notifications.discord_enabled else None,
        telegram_bot_token=telegram_token if notifications.telegram_enabled else None,
        telegram_chat_id=telegram_chat_id if notifications.telegram_enabled else None,
    )
