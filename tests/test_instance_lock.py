from __future__ import annotations

from pathlib import Path

import pytest

from naver_restock_monitor.instance_lock import (
    AlreadyRunningError,
    SingleInstanceLock,
    lock_path_for_state,
)


def test_single_instance_lock_blocks_duplicate_process(tmp_path: Path) -> None:
    path = lock_path_for_state(tmp_path / "state.json")
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_lock_path_is_next_to_state_file(tmp_path: Path) -> None:
    assert lock_path_for_state(tmp_path / "state.json") == tmp_path / "state.json.lock"
