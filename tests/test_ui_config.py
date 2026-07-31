from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from naver_restock_monitor.config import ConfigError, load_config
from naver_restock_monitor.ui_config import (
    UiProduct,
    UiSettings,
    load_ui_settings,
    parse_product_url,
    save_ui_settings,
    state_path_from_config,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://brand.naver.com/pokemon/products/1234567890",
        "https://m.brand.naver.com/pokemon/products/1234567890?query=ignored",
    ],
)
def test_parse_product_url(url: str) -> None:
    parsed = parse_product_url(url)
    assert parsed.slug == "pokemon"
    assert parsed.product_id == "1234567890"
    assert parsed.url == "https://brand.naver.com/pokemon/products/1234567890"


@pytest.mark.parametrize(
    "url",
    [
        "http://brand.naver.com/pokemon/products/123",
        "https://example.com/pokemon/products/123",
        "https://brand.naver.com/pokemon/not-products/123",
        "https://brand.naver.com/pokemon/products/not-a-number",
    ],
)
def test_parse_product_url_rejects_invalid_links(url: str) -> None:
    with pytest.raises(ConfigError):
        parse_product_url(url)


def make_settings() -> UiSettings:
    return UiSettings(
        store_slug="pokemon",
        channel_id="channel-id",
        products=[
            UiProduct(
                "테스트 상품",
                "https://brand.naver.com/pokemon/products/1234567890",
                "1234567890",
            )
        ],
        interval_min_seconds=300,
        interval_max_seconds=600,
        timezone="Asia/Seoul",
        show_browser=True,
        chrome_binary="",
        chromedriver_path="",
        discord_enabled=True,
        discord_webhook_url=(
            "https://discord.com/api/webhooks/000000000000000001/fake_for_test"
        ),
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_chat_id="",
    )


def test_ui_saves_secrets_only_to_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "config.yaml"
    settings = make_settings()
    settings.chrome_binary = "/usr/bin/chromium"
    settings.chromedriver_path = "/usr/bin/chromedriver"
    save_ui_settings(path, settings)

    yaml_text = path.read_text(encoding="utf-8")
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert settings.discord_webhook_url not in yaml_text
    assert settings.discord_webhook_url in env_text
    assert "DISCORD_WEBHOOK_URL" in env_text
    assert not list(tmp_path.glob("*.tmp"))

    loaded = load_config(path)
    assert loaded.products[0].id == "1234567890"
    assert loaded.discord_webhook_url == settings.discord_webhook_url
    ui_loaded = load_ui_settings(path)
    assert ui_loaded.products == settings.products
    assert ui_loaded.chrome_binary == "/usr/bin/chromium"
    assert ui_loaded.chromedriver_path == "/usr/bin/chromedriver"


def test_ui_saves_both_notification_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    settings = make_settings()
    settings.telegram_enabled = True
    settings.telegram_bot_token = "1234567890:fake_bot_token_abcdefghijk"
    settings.telegram_chat_id = "-1001234567890"
    path = tmp_path / "config.yaml"
    save_ui_settings(path, settings)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert raw["notifications"]["discord_enabled"] is True
    assert raw["notifications"]["telegram_enabled"] is True
    assert settings.telegram_bot_token in env_text
    assert settings.telegram_chat_id in env_text


def test_ui_rejects_products_from_different_store(tmp_path: Path) -> None:
    settings = make_settings()
    settings.products.append(
        UiProduct(
            "다른 스토어",
            "https://brand.naver.com/another-store/products/999",
            "999",
        )
    )
    with pytest.raises(ConfigError, match="같은 브랜드스토어"):
        save_ui_settings(tmp_path / "config.yaml", settings)


def test_ui_rejects_short_interval(tmp_path: Path) -> None:
    settings = make_settings()
    settings.interval_min_seconds = 19
    with pytest.raises(ConfigError, match="20초"):
        save_ui_settings(tmp_path / "config.yaml", settings)


def test_ui_accepts_explicit_20_second_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    settings = make_settings()
    settings.interval_min_seconds = 20
    settings.interval_max_seconds = 40
    save_ui_settings(tmp_path / "config.yaml", settings)
    loaded = load_config(tmp_path / "config.yaml")
    assert loaded.monitor.interval_min_seconds == 20
    assert loaded.monitor.interval_max_seconds == 40


def test_ui_requires_one_notification_channel(tmp_path: Path) -> None:
    settings = make_settings()
    settings.discord_enabled = False
    with pytest.raises(ConfigError, match="하나 이상"):
        save_ui_settings(tmp_path / "config.yaml", settings)


def test_ui_resolves_custom_state_path(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("state_file: data/custom-state.json\n", encoding="utf-8")
    assert (
        state_path_from_config(path) == (tmp_path / "data/custom-state.json").resolve()
    )
