from __future__ import annotations

from pathlib import Path

from naver_restock_monitor.doctor import has_errors, run_diagnostics
from naver_restock_monitor.models import MonitorSettings

from .helpers import make_config


def test_doctor_accepts_explicit_browser_and_driver(
    tmp_path: Path, monkeypatch
) -> None:
    browser = tmp_path / "chromium"
    driver = tmp_path / "chromedriver"
    browser.write_text("fake", encoding="utf-8")
    driver.write_text("fake", encoding="utf-8")
    config = make_config(tmp_path)
    config = type(config)(
        store=config.store,
        products=config.products,
        monitor=MonitorSettings(
            interval_min_seconds=60,
            interval_max_seconds=120,
            chrome_binary=str(browser),
            chromedriver_path=str(driver),
        ),
        notifications=config.notifications,
        logging=config.logging,
        state_file=config.state_file,
        discord_webhook_url=config.discord_webhook_url,
    )
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("platform.machine", lambda: "aarch64")
    monkeypatch.setenv("DISPLAY", ":99")
    results = run_diagnostics(config, server_mode=True)
    assert has_errors(results) is False


def test_doctor_requires_display_for_visible_server(
    tmp_path: Path, monkeypatch
) -> None:
    browser = tmp_path / "chromium"
    driver = tmp_path / "chromedriver"
    browser.write_text("fake", encoding="utf-8")
    driver.write_text("fake", encoding="utf-8")
    config = make_config(tmp_path)
    config = type(config)(
        store=config.store,
        products=config.products,
        monitor=MonitorSettings(
            chrome_binary=str(browser),
            chromedriver_path=str(driver),
        ),
        notifications=config.notifications,
        logging=config.logging,
        state_file=config.state_file,
        discord_webhook_url=config.discord_webhook_url,
    )
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.delenv("DISPLAY", raising=False)
    results = run_diagnostics(config, server_mode=True)
    assert has_errors(results) is True
    assert any(item.label == "가상 화면" and item.level == "error" for item in results)
