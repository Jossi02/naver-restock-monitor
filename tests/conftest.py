from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("자동 테스트에서는 외부 네트워크를 사용할 수 없습니다.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
