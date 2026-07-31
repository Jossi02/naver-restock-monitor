from __future__ import annotations

from .models import AppConfig
from .notifiers import DiscordNotifier, NotificationDispatcher, TelegramNotifier
from .notifiers.base import Notifier


def build_dispatcher(config: AppConfig) -> NotificationDispatcher:
    notifiers: list[Notifier] = []
    if config.notifications.discord_enabled:
        assert config.discord_webhook_url is not None
        notifiers.append(DiscordNotifier(config.discord_webhook_url))
    if config.notifications.telegram_enabled:
        assert config.telegram_bot_token is not None
        assert config.telegram_chat_id is not None
        notifiers.append(
            TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
        )
    return NotificationDispatcher(notifiers, config.notifications)
