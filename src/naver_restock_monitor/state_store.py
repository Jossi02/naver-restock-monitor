from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import PendingAlert, ProductState, StateSnapshot, StockState


class StateStoreError(RuntimeError):
    """Raised when state cannot be loaded or saved safely."""


class JsonStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.last_recovery_path: Path | None = None

    def load(self) -> StateSnapshot:
        if not self.path.exists():
            return StateSnapshot()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return self._decode(raw)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            recovered = self._quarantine_corrupt_file()
            self.last_recovery_path = recovered
            return StateSnapshot()

    def save(self, snapshot: StateSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._encode(snapshot)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise StateStoreError(
                f"상태 파일을 안전하게 저장하지 못했습니다: {self.path}"
            ) from exc

    def _quarantine_corrupt_file(self) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        try:
            os.replace(self.path, target)
        except OSError as exc:
            raise StateStoreError(
                f"손상된 상태 파일을 격리하지 못했습니다: {self.path}"
            ) from exc
        return target

    @staticmethod
    def _decode(raw: Any) -> StateSnapshot:
        if not isinstance(raw, Mapping) or raw.get("version") != 1:
            raise ValueError("unsupported state version")
        products_raw = raw.get("products", {})
        pending_raw = raw.get("pending", {})
        if not isinstance(products_raw, Mapping) or not isinstance(
            pending_raw, Mapping
        ):
            raise ValueError("invalid state shape")

        products: dict[str, ProductState] = {}
        for product_id, value in products_raw.items():
            if not isinstance(product_id, str) or not isinstance(value, Mapping):
                raise ValueError("invalid product state")
            products[product_id] = ProductState(
                confirmed_state=StockState(value.get("confirmed_state", "unknown")),
                last_observed_state=StockState(
                    value.get("last_observed_state", "unknown")
                ),
                last_checked_at=_optional_string(value.get("last_checked_at")),
                last_alert_at=_optional_string(value.get("last_alert_at")),
                consecutive_failures=_nonnegative_int(
                    value.get("consecutive_failures", 0)
                ),
            )

        pending: dict[str, PendingAlert] = {}
        for product_id, value in pending_raw.items():
            if not isinstance(product_id, str) or not isinstance(value, Mapping):
                raise ValueError("invalid pending alert")
            attempts = value.get("channel_attempts")
            if not isinstance(attempts, Mapping) or not attempts:
                raise ValueError("invalid pending channels")
            parsed_attempts: dict[str, int] = {}
            for channel, count in attempts.items():
                if not isinstance(channel, str):
                    raise ValueError("invalid pending channel")
                parsed_attempts[channel] = _nonnegative_int(count)
            pending[product_id] = PendingAlert(
                product_id=_required_string(value.get("product_id")),
                product_name=_required_string(value.get("product_name")),
                product_url=_required_string(value.get("product_url")),
                occurred_at=_required_string(value.get("occurred_at")),
                channel_attempts=parsed_attempts,
                next_attempt_at=_required_string(value.get("next_attempt_at")),
            )
        return StateSnapshot(
            version=1,
            products=products,
            pending=pending,
            blocked_until=_optional_string(raw.get("blocked_until")),
        )

    @staticmethod
    def _encode(snapshot: StateSnapshot) -> dict[str, Any]:
        return {
            "version": 1,
            "blocked_until": snapshot.blocked_until,
            "products": {
                product_id: {
                    "confirmed_state": state.confirmed_state.value,
                    "last_observed_state": state.last_observed_state.value,
                    "last_checked_at": state.last_checked_at,
                    "last_alert_at": state.last_alert_at,
                    "consecutive_failures": state.consecutive_failures,
                }
                for product_id, state in snapshot.products.items()
            },
            "pending": {
                product_id: {
                    "product_id": alert.product_id,
                    "product_name": alert.product_name,
                    "product_url": alert.product_url,
                    "occurred_at": alert.occurred_at,
                    "channel_attempts": alert.channel_attempts,
                    "next_attempt_at": alert.next_attempt_at,
                }
                for product_id, alert in snapshot.pending.items()
            },
        }


def _required_string(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("expected non-empty string")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return _required_string(value)


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected non-negative integer")
    return int(value)
