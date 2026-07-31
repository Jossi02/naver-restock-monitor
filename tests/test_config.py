from __future__ import annotations

from pathlib import Path

import pytest

from naver_restock_monitor.config import ConfigError, load_config


def write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


BASE = """
store:
  slug: example
  channel_id: channel-id
products:
  - id: "123"
    name: test
monitor:
  interval_min_seconds: 60
  interval_max_seconds: 120
notifications:
  discord_enabled: {discord}
  telegram_enabled: {telegram}
"""


@pytest.mark.parametrize(
    ("discord", "telegram", "env"),
    [
        (
            True,
            False,
            {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/fake"},
        ),
        (
            False,
            True,
            {
                "TELEGRAM_BOT_TOKEN": "1234567890:fake_bot_token_abcdefghijk",
                "TELEGRAM_CHAT_ID": "123",
            },
        ),
        (
            True,
            True,
            {
                "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/fake",
                "TELEGRAM_BOT_TOKEN": "1234567890:fake_bot_token_abcdefghijk",
                "TELEGRAM_CHAT_ID": "123",
            },
        ),
    ],
)
def test_optional_notification_channel_combinations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    discord: bool,
    telegram: bool,
    env: dict[str, str],
) -> None:
    for key in ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path = write_config(
        tmp_path / "config.yaml",
        BASE.format(discord=str(discord).lower(), telegram=str(telegram).lower()),
    )
    config = load_config(path)
    assert config.notifications.discord_enabled is discord
    assert config.notifications.telegram_enabled is telegram


def test_requires_at_least_one_notification_channel(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "config.yaml", BASE.format(discord="false", telegram="false")
    )
    with pytest.raises(ConfigError, match="하나 이상"):
        load_config(path)


def test_rejects_missing_products(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "config.yaml",
        "store:\n  slug: example\n  channel_id: channel\nproducts: []\n",
    )
    with pytest.raises(ConfigError, match="상품을 하나 이상"):
        load_config(path)


def test_rejects_too_short_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/fake")
    body = BASE.format(discord="true", telegram="false").replace(
        "interval_min_seconds: 60", "interval_min_seconds: 10"
    )
    path = write_config(tmp_path / "config.yaml", body)
    with pytest.raises(ConfigError, match="20초 이상"):
        load_config(path)


def test_config_repr_does_not_expose_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "https://discord.com/api/webhooks/123/super-secret"
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", secret)
    path = write_config(
        tmp_path / "config.yaml", BASE.format(discord="true", telegram="false")
    )
    assert secret not in repr(load_config(path))


def test_browser_paths_can_be_set_by_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/fake")
    monkeypatch.setenv("CHROME_BINARY", "/usr/bin/chromium")
    monkeypatch.setenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
    path = write_config(
        tmp_path / "config.yaml", BASE.format(discord="true", telegram="false")
    )
    config = load_config(path)
    assert config.monitor.chrome_binary == "/usr/bin/chromium"
    assert config.monitor.chromedriver_path == "/usr/bin/chromedriver"
