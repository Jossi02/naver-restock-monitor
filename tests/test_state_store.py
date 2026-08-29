from __future__ import annotations

import json
from pathlib import Path

import pytest

from naver_restock_monitor.models import (
    PendingAlert,
    ProductState,
    StateSnapshot,
    StockState,
)
from naver_restock_monitor.state_store import JsonStateStore


def test_state_is_saved_and_restored(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = JsonStateStore(path)
    snapshot = StateSnapshot(
        products={"123": ProductState(confirmed_state=StockState.OUT_OF_STOCK)}
    )
    store.save(snapshot)
    loaded = store.load()
    assert loaded.products["123"].confirmed_state is StockState.OUT_OF_STOCK
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_state_is_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("not-json", encoding="utf-8")
    store = JsonStateStore(path)
    assert store.load() == StateSnapshot()
    assert not path.exists()
    assert len(list(tmp_path.glob("state.json.corrupt-*"))) == 1


def test_rate_limit_cooldown_is_saved_and_restored(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = JsonStateStore(path)
    snapshot = StateSnapshot(blocked_until="2026-01-01T12:30:00+09:00")
    store.save(snapshot)
    assert store.load().blocked_until == "2026-01-01T12:30:00+09:00"


def test_timezone_aware_timestamps_are_saved_and_restored(tmp_path: Path) -> None:
    timestamp = "2026-01-01T12:30:00+09:00"
    store = JsonStateStore(tmp_path / "state.json")
    snapshot = StateSnapshot(
        blocked_until=timestamp,
        products={
            "123": ProductState(
                last_checked_at=timestamp,
                last_alert_at=timestamp,
            )
        },
        pending={
            "123": PendingAlert(
                "123",
                "상품",
                "https://example.invalid/123",
                timestamp,
                {"discord": 1},
                timestamp,
            )
        },
    )
    store.save(snapshot)
    assert store.load() == snapshot


@pytest.mark.parametrize(
    "field",
    [
        "blocked_until",
        "last_checked_at",
        "last_alert_at",
        "occurred_at",
        "next_attempt_at",
    ],
)
@pytest.mark.parametrize("invalid", ["not-a-time", "2026-01-01T12:30:00"])
def test_invalid_or_naive_timestamps_are_quarantined(
    tmp_path: Path, field: str, invalid: str
) -> None:
    valid = "2026-01-01T12:30:00+09:00"
    payload = {
        "version": 1,
        "blocked_until": valid,
        "products": {
            "123": {
                "confirmed_state": "out_of_stock",
                "last_observed_state": "out_of_stock",
                "last_checked_at": valid,
                "last_alert_at": valid,
                "consecutive_failures": 0,
            }
        },
        "pending": {
            "123": {
                "product_id": "123",
                "product_name": "상품",
                "product_url": "https://example.invalid/123",
                "occurred_at": valid,
                "channel_attempts": {"discord": 1},
                "next_attempt_at": valid,
            }
        },
    }
    if field == "blocked_until":
        payload[field] = invalid
    elif field in {"last_checked_at", "last_alert_at"}:
        payload["products"]["123"][field] = invalid
    else:
        payload["pending"]["123"][field] = invalid

    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = JsonStateStore(path)
    assert store.load() == StateSnapshot()
    assert not path.exists()
    assert len(list(tmp_path.glob("state.json.corrupt-*"))) == 1
