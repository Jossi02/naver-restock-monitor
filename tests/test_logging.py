from __future__ import annotations

import logging

from naver_restock_monitor.logging_utils import RedactingFormatter


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
