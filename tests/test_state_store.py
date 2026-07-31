from __future__ import annotations

from pathlib import Path

from naver_restock_monitor.models import ProductState, StateSnapshot, StockState
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
