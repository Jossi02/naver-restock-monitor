from __future__ import annotations

import logging

from naver_restock_monitor.logging_utils import RedactingFormatter, _close_handlers


def test_secrets_are_redacted_from_message_and_exception() -> None:
    secret = "token-secret-value"
    formatter = RedactingFormatter("%(message)s %(exc_text)s", secrets=[secret])
    try:
        raise RuntimeError(f"request failed at https://example.invalid/{secret}")
    except RuntimeError:
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            f"failed {secret}",
            (),
            exc_info=__import__("sys").exc_info(),
        )
    rendered = formatter.format(record)
    assert secret not in rendered
    assert "<REDACTED>" in rendered


def test_replacing_logging_handlers_closes_resources() -> None:
    class TrackingHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.was_closed = False

        def emit(self, _record: logging.LogRecord) -> None: ...

        def close(self) -> None:
            self.was_closed = True
            super().close()

    old = TrackingHandler()
    logger = logging.Logger("test")
    logger.addHandler(old)

    _close_handlers(logger)

    assert old.was_closed is True
    assert logger.handlers == []
