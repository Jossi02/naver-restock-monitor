from __future__ import annotations

from pathlib import Path

from naver_restock_monitor.models import (
    AppConfig,
    LoggingSettings,
    MonitorSettings,
    NotificationSettings,
    Product,
    Store,
)


def make_config(
    directory: Path,
    *,
    notify_initial: bool = False,
    notifications: NotificationSettings | None = None,
) -> AppConfig:
    return AppConfig(
        store=Store("example-store", "channel-id"),
        products=(Product("123", "테스트 상품"),),
        monitor=MonitorSettings(
            interval_min_seconds=60,
            interval_max_seconds=60,
            between_products_min_seconds=0,
            between_products_max_seconds=0,
            session_setup_wait_seconds=0,
            notify_initial_in_stock=notify_initial,
            min_alert_interval_seconds=0,
        ),
        notifications=notifications
        or NotificationSettings(discord_enabled=True, max_immediate_attempts=1),
        logging=LoggingSettings(file=str(directory / "monitor.log")),
        state_file=str(directory / "state.json"),
        discord_webhook_url="https://discord.com/api/webhooks/1/fake",
    )
