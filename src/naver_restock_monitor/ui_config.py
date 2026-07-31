from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import dotenv_values

from .config import ConfigError, is_valid_discord_webhook


@dataclass(frozen=True)
class ProductLink:
    url: str
    slug: str
    product_id: str


@dataclass(frozen=True)
class UiProduct:
    name: str
    url: str
    product_id: str


@dataclass
class UiSettings:
    store_slug: str
    channel_id: str
    products: list[UiProduct]
    interval_min_seconds: int
    interval_max_seconds: int
    timezone: str
    show_browser: bool
    chrome_binary: str
    chromedriver_path: str
    discord_enabled: bool
    discord_webhook_url: str
    telegram_enabled: bool
    telegram_bot_token: str
    telegram_chat_id: str


def parse_product_url(value: str) -> ProductLink:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "brand.naver.com",
        "m.brand.naver.com",
    }:
        raise ConfigError(
            "https://brand.naver.com 또는 https://m.brand.naver.com의 "
            "상품 링크를 입력하세요."
        )
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3 or parts[1] != "products":
        raise ConfigError("네이버 브랜드스토어 상품 링크 형식이 아닙니다.")
    slug, product_id = parts[0], parts[2]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", slug) or not product_id.isdigit():
        raise ConfigError(
            "상품 링크의 스토어 이름 또는 상품 ID 형식이 올바르지 않습니다."
        )
    canonical = f"https://brand.naver.com/{slug}/products/{product_id}"
    return ProductLink(canonical, slug, product_id)


def load_ui_settings(config_path: str | Path) -> UiSettings:
    path = Path(config_path).expanduser().resolve()
    raw = _load_yaml(path)
    store = _as_dict(raw.get("store"))
    monitor = _as_dict(raw.get("monitor"))
    notifications = _as_dict(raw.get("notifications"))
    slug = _string(store.get("slug"))
    products: list[UiProduct] = []
    for item in (
        raw.get("products", []) if isinstance(raw.get("products"), list) else []
    ):
        product = _as_dict(item)
        product_id = _string(product.get("id"))
        name = _string(product.get("name"))
        if slug and product_id:
            url = f"https://brand.naver.com/{slug}/products/{product_id}"
            products.append(
                UiProduct(name=name or product_id, url=url, product_id=product_id)
            )

    env = dotenv_values(path.parent / ".env")
    return UiSettings(
        store_slug=slug,
        channel_id=_string(store.get("channel_id")),
        products=products,
        interval_min_seconds=_positive_int(monitor.get("interval_min_seconds"), 300),
        interval_max_seconds=_positive_int(monitor.get("interval_max_seconds"), 600),
        timezone=_string(monitor.get("timezone")) or "Asia/Seoul",
        show_browser=not _boolean(monitor.get("headless"), False),
        chrome_binary=_string(
            os.getenv("CHROME_BINARY")
            or env.get("CHROME_BINARY")
            or monitor.get("chrome_binary")
        ),
        chromedriver_path=_string(
            os.getenv("CHROMEDRIVER_PATH")
            or env.get("CHROMEDRIVER_PATH")
            or monitor.get("chromedriver_path")
        ),
        discord_enabled=_boolean(notifications.get("discord_enabled"), False),
        discord_webhook_url=_string(env.get("DISCORD_WEBHOOK_URL")),
        telegram_enabled=_boolean(notifications.get("telegram_enabled"), False),
        telegram_bot_token=_string(env.get("TELEGRAM_BOT_TOKEN")),
        telegram_chat_id=_string(env.get("TELEGRAM_CHAT_ID")),
    )


def save_ui_settings(config_path: str | Path, settings: UiSettings) -> None:
    path = Path(config_path).expanduser().resolve()
    _validate_ui_settings(settings)
    raw = _load_yaml(path)
    raw["store"] = {
        "slug": settings.store_slug,
        "channel_id": settings.channel_id,
    }
    raw["products"] = [
        {"id": product.product_id, "name": product.name}
        for product in settings.products
    ]
    monitor = _as_dict(raw.get("monitor"))
    monitor.setdefault("between_products_min_seconds", 2)
    monitor.setdefault("between_products_max_seconds", 5)
    monitor.setdefault("api_timeout_seconds", 15)
    monitor.setdefault("api_max_attempts", 2)
    monitor.setdefault("backoff_base_seconds", 2)
    monitor.setdefault("backoff_max_seconds", 30)
    monitor.setdefault("session_setup_wait_seconds", 4)
    monitor.setdefault("session_refresh_after_cycles", 100)
    monitor.setdefault("session_failure_threshold", 3)
    monitor.setdefault("cooldown_seconds", 900)
    monitor.setdefault("rate_limit_cooldown_seconds", 1800)
    monitor.setdefault("notify_initial_in_stock", False)
    monitor.setdefault("min_alert_interval_seconds", 3600)
    monitor.update(
        {
            "interval_min_seconds": settings.interval_min_seconds,
            "interval_max_seconds": settings.interval_max_seconds,
            "timezone": settings.timezone,
            "headless": not settings.show_browser,
            "chrome_binary": settings.chrome_binary or None,
            "chromedriver_path": settings.chromedriver_path or None,
        }
    )
    raw["monitor"] = monitor
    notifications = _as_dict(raw.get("notifications"))
    notifications.setdefault("max_immediate_attempts", 3)
    notifications.setdefault("max_total_attempts", 6)
    notifications.setdefault("retry_base_seconds", 2)
    notifications.setdefault("retry_max_seconds", 60)
    notifications.setdefault("max_pending_alerts", 100)
    notifications.update(
        {
            "discord_enabled": settings.discord_enabled,
            "telegram_enabled": settings.telegram_enabled,
        }
    )
    raw["notifications"] = notifications
    raw.setdefault("state_file", "var/state.json")
    raw.setdefault(
        "logging",
        {
            "level": "INFO",
            "file": "var/monitor.log",
            "max_bytes": 2_000_000,
            "backup_count": 3,
        },
    )

    yaml_text = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
    env_values: dict[str, str] = {}
    if settings.discord_enabled:
        env_values["DISCORD_WEBHOOK_URL"] = settings.discord_webhook_url
    if settings.telegram_enabled:
        env_values["TELEGRAM_BOT_TOKEN"] = settings.telegram_bot_token
        env_values["TELEGRAM_CHAT_ID"] = settings.telegram_chat_id
    env_text = "".join(
        f"{key}={_quote_env(value)}\n" for key, value in env_values.items()
    )

    _atomic_write(path, yaml_text, private=False)
    _atomic_write(path.parent / ".env", env_text, private=True)
    _update_process_environment(env_values)


def state_path_from_config(config_path: str | Path) -> Path:
    path = Path(config_path).expanduser().resolve()
    raw = _load_yaml(path)
    configured = raw.get("state_file", "var/state.json")
    state_path = (
        Path(configured) if isinstance(configured, str) else Path("var/state.json")
    )
    return (
        state_path if state_path.is_absolute() else (path.parent / state_path).resolve()
    )


def _validate_ui_settings(settings: UiSettings) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", settings.store_slug):
        raise ConfigError("상품 링크에서 확인한 스토어 이름이 필요합니다.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", settings.channel_id):
        raise ConfigError("스토어 channel_id가 없거나 형식이 올바르지 않습니다.")
    if not settings.products:
        raise ConfigError("추적할 상품을 하나 이상 추가하세요.")
    seen: set[str] = set()
    for product in settings.products:
        parsed = parse_product_url(product.url)
        if parsed.slug != settings.store_slug:
            raise ConfigError(
                "한 설정 파일에는 같은 브랜드스토어 상품만 추가할 수 있습니다."
            )
        if parsed.product_id != product.product_id or product.product_id in seen:
            raise ConfigError("상품 ID가 일치하지 않거나 중복됐습니다.")
        if not product.name.strip():
            raise ConfigError("상품 이름이 비어 있습니다.")
        seen.add(product.product_id)
    if settings.interval_min_seconds < 20:
        raise ConfigError("서비스 보호를 위해 최소 확인 간격은 20초 이상이어야 합니다.")
    if settings.interval_max_seconds < settings.interval_min_seconds:
        raise ConfigError("최대 확인 간격은 최소 확인 간격 이상이어야 합니다.")
    try:
        ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"알 수 없는 시간대입니다: {settings.timezone}") from exc
    if not (settings.discord_enabled or settings.telegram_enabled):
        raise ConfigError("Discord 또는 Telegram을 하나 이상 활성화하세요.")
    if settings.discord_enabled and not is_valid_discord_webhook(
        settings.discord_webhook_url
    ):
        raise ConfigError("Discord Webhook URL이 없거나 올바른 HTTPS 형식이 아닙니다.")
    if settings.telegram_enabled:
        if not re.fullmatch(
            r"\d{6,12}:[A-Za-z0-9_-]{20,}", settings.telegram_bot_token
        ):
            raise ConfigError("Telegram Bot Token 형식이 올바르지 않습니다.")
        if not re.fullmatch(r"(?:-?\d+|@[A-Za-z0-9_]{5,})", settings.telegram_chat_id):
            raise ConfigError("Telegram Chat ID 형식이 올바르지 않습니다.")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"설정 파일을 읽을 수 없습니다: {exc}") from exc
    return dict(loaded) if isinstance(loaded, dict) else {}


def _atomic_write(path: Path, content: str, *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if private:
            os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    except OSError as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise ConfigError(f"설정 파일을 안전하게 저장하지 못했습니다: {path}") from exc


def _update_process_environment(values: dict[str, str]) -> None:
    managed = {"DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
    for key in managed:
        if key in values:
            os.environ[key] = values[key]
        else:
            os.environ.pop(key, None)


def _quote_env(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _positive_int(value: object, default: int) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else default
    )


def _boolean(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default
