from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .models import AppConfig


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, *, secrets: list[str]) -> None:
        super().__init__(fmt)
        self._secrets = [secret for secret in secrets if secret]

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in self._secrets:
            rendered = rendered.replace(secret, "<REDACTED>")
        return rendered


def configure_logging(config: AppConfig) -> None:
    level = getattr(logging, config.logging.level)
    log_path = Path(config.logging.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    secrets = [
        value
        for value in (
            config.discord_webhook_url,
            config.telegram_bot_token,
            config.telegram_chat_id,
        )
        if value
    ]
    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        secrets=secrets,
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    rotating = RotatingFileHandler(
        log_path,
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
        encoding="utf-8",
    )
    rotating.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(rotating)
