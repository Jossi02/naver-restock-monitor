from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from . import __version__
from .config import ConfigError, load_config
from .doctor import format_diagnostics, has_errors, run_diagnostics
from .instance_lock import AlreadyRunningError, SingleInstanceLock, lock_path_for_state
from .logging_utils import configure_logging
from .models import Alert
from .monitor import CooldownActiveError, RestockMonitor
from .notifiers import NotificationDispatcher
from .runtime import build_dispatcher
from .state_store import JsonStateStore, StateStoreError
from .stock_client import SeleniumStockClient, StockClientError

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="naver_restock_monitor",
        description="네이버 브랜드스토어 상품의 재입고 상태를 보수적으로 확인합니다.",
    )
    parser.add_argument("--config", default="config.yaml", help="YAML 설정 파일 경로")
    parser.add_argument("--version", action="version", version=__version__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-config", action="store_true", help="설정만 검증하고 종료"
    )
    mode.add_argument(
        "--test-notifications",
        action="store_true",
        help="활성화된 알림 채널에 실제 테스트 메시지를 보내고 종료",
    )
    mode.add_argument(
        "--once", action="store_true", help="상품을 한 번만 확인하고 종료"
    )
    mode.add_argument(
        "--ui", action="store_true", help="로컬 데스크톱 설정 화면을 열기"
    )
    mode.add_argument(
        "--server",
        action="store_true",
        help="화면 없는 서버용 검사를 수행한 뒤 계속 모니터링",
    )
    mode.add_argument(
        "--doctor",
        action="store_true",
        help="브라우저·드라이버·경로·서버 환경을 진단하고 종료",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.ui:
        try:
            from .ui import launch_ui
        except ImportError as exc:
            print(
                "Tkinter UI를 불러올 수 없습니다. "
                f"Python의 Tk 지원을 확인하세요: {exc}",
                file=sys.stderr,
            )
            return 1
        return launch_ui(args.config)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    if args.doctor:
        diagnostics = run_diagnostics(config, server_mode=False)
        print(format_diagnostics(diagnostics))
        return 1 if has_errors(diagnostics) else 0

    if args.server:
        diagnostics = run_diagnostics(config, server_mode=True)
        print(format_diagnostics(diagnostics))
        if has_errors(diagnostics):
            print("서버 환경 오류를 수정한 뒤 다시 실행하세요.", file=sys.stderr)
            return 2

    if args.check_config:
        channels = []
        if config.notifications.discord_enabled:
            channels.append("Discord")
        if config.notifications.telegram_enabled:
            channels.append("Telegram")
        product_count = len(config.products)
        channel_text = ", ".join(channels)
        print(f"설정이 올바릅니다. 상품 {product_count}개, 알림 채널: {channel_text}")
        if config.monitor.interval_min_seconds < 60:
            print(
                "경고: 확인 간격이 60초 미만입니다. 서비스 요청 제한 가능성을 "
                "확인하고 책임 있게 사용하세요."
            )
        return 0

    configure_logging(config)
    dispatcher: NotificationDispatcher | None = None
    if config.monitor.interval_min_seconds < 60:
        LOGGER.warning(
            "확인 간격이 60초 미만입니다. 요청 제한 신호가 나오면 즉시 중단합니다."
        )
    if args.test_notifications:
        dispatcher = build_dispatcher(config)
        now = datetime.now(ZoneInfo(config.monitor.timezone))
        alert = Alert(
            product_id="test",
            product_name="알림 설정 테스트",
            product_url="https://brand.naver.com/",
            occurred_at=now.isoformat(),
            is_test=True,
        )
        result = dispatcher.send_with_retry(alert)
        dispatcher.close()
        if result.failed_attempts:
            for channel, error in result.errors.items():
                LOGGER.error("%s 테스트 실패: %s", channel, error)
            return 1
        LOGGER.info("알림 채널 테스트가 모두 성공했습니다: %s", result.successes)
        return 0

    stop_event = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("종료 신호(%s)를 받았습니다. 안전하게 종료합니다.", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    lock = SingleInstanceLock(lock_path_for_state(config.state_file))
    client = None
    try:
        lock.acquire()
        dispatcher = build_dispatcher(config)
        client = SeleniumStockClient(
            config.store,
            config.monitor,
            config.products[0],
        )
        monitor = RestockMonitor(
            config,
            client,
            dispatcher,
            JsonStateStore(config.state_file),
        )
        monitor.run(once=args.once, stop_event=stop_event)
    except (
        AlreadyRunningError,
        CooldownActiveError,
        StockClientError,
        StateStoreError,
    ) as exc:
        LOGGER.error("실행을 계속할 수 없습니다: %s", exc)
        return 1
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        if client is not None:
            client.close()
        if dispatcher is not None:
            dispatcher.close()
        lock.release()
    return 0
