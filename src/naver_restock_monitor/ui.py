from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from zoneinfo import ZoneInfo

from .config import ConfigError, load_config
from .instance_lock import AlreadyRunningError, SingleInstanceLock, lock_path_for_state
from .logging_utils import configure_logging
from .models import Alert
from .monitor import CooldownActiveError, RestockMonitor
from .runtime import build_dispatcher
from .state_store import JsonStateStore, StateStoreError
from .stock_client import SeleniumStockClient, StockClientError
from .ui_config import (
    UiProduct,
    UiSettings,
    load_ui_settings,
    parse_product_url,
    save_ui_settings,
    state_path_from_config,
)

LOGGER = logging.getLogger(__name__)


class MonitorApp:
    def __init__(self, root: tk.Tk, config_path: str | Path) -> None:
        self.root = root
        self.config_path = Path(config_path).expanduser().resolve()
        self.products: dict[str, UiProduct] = {}
        self.worker: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.events: queue.Queue[tuple[bool, str]] = queue.Queue()
        self.closing = False

        self.store_slug = tk.StringVar()
        self.channel_id = tk.StringVar()
        self.product_url = tk.StringVar()
        self.product_name = tk.StringVar()
        self.interval_min = tk.StringVar(value="300")
        self.interval_max = tk.StringVar(value="600")
        self.timezone = tk.StringVar(value="Asia/Seoul")
        self.show_browser = tk.BooleanVar(value=True)
        self.chrome_binary = tk.StringVar()
        self.chromedriver_path = tk.StringVar()
        self.discord_enabled = tk.BooleanVar()
        self.discord_webhook = tk.StringVar()
        self.telegram_enabled = tk.BooleanVar()
        self.telegram_token = tk.StringVar()
        self.telegram_chat_id = tk.StringVar()
        self.reveal_secrets = tk.BooleanVar()
        self.status_text = tk.StringVar(value="설정을 불러오는 중입니다.")
        self.confirmed_short_interval: tuple[int, int] | None = None

        self._configure_window()
        self._build_layout()
        self._load()
        self.root.protocol("WM_DELETE_WINDOW", self._request_close)
        self.root.after(200, self._poll_worker)
        self.root.after(1_000, self._refresh_product_states)

    def _configure_window(self) -> None:
        self.root.title("Naver Restock Monitor")
        self.root.geometry("940x680")
        self.root.minsize(820, 600)
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("TkDefaultFont", 15, "bold"))
        style.configure("Muted.TLabel", foreground="#555555")

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Naver Restock Monitor", style="Title.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            outer,
            text=(
                "상품 링크와 알림 채널을 로컬에서 관리합니다. "
                "비밀값은 .env에만 저장됩니다."
            ),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        product_tab = ttk.Frame(notebook, padding=12)
        notification_tab = ttk.Frame(notebook, padding=12)
        operation_tab = ttk.Frame(notebook, padding=12)
        notebook.add(product_tab, text="상품")
        notebook.add(notification_tab, text="알림")
        notebook.add(operation_tab, text="실행")
        self._build_product_tab(product_tab)
        self._build_notification_tab(notification_tab)
        self._build_operation_tab(operation_tab)

        status = ttk.Frame(outer, padding=(0, 12, 0, 0))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_text).pack(side="left", fill="x")
        self.save_button = ttk.Button(status, text="설정 저장", command=self._save)
        self.save_button.pack(side="right")

    def _build_product_tab(self, parent: ttk.Frame) -> None:
        store_box = ttk.LabelFrame(parent, text="브랜드스토어", padding=10)
        store_box.pack(fill="x")
        store_box.columnconfigure(1, weight=1)
        ttk.Label(store_box, text="스토어").grid(row=0, column=0, sticky="w")
        ttk.Entry(store_box, textvariable=self.store_slug, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=(8, 18)
        )
        ttk.Label(store_box, text="channel_id").grid(row=0, column=2, sticky="w")
        ttk.Entry(store_box, textvariable=self.channel_id, width=28).grid(
            row=0, column=3, sticky="ew", padx=(8, 0)
        )
        ttk.Label(
            store_box,
            text=(
                "상품 링크에서 스토어와 상품 ID는 자동 추출됩니다. "
                "channel_id는 스토어마다 한 번 입력해야 합니다."
            ),
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        add_box = ttk.LabelFrame(parent, text="상품 링크 추가", padding=10)
        add_box.pack(fill="x", pady=(12, 8))
        add_box.columnconfigure(1, weight=1)
        ttk.Label(add_box, text="상품 링크").grid(row=0, column=0, sticky="w")
        ttk.Entry(add_box, textvariable=self.product_url).grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Label(add_box, text="표시 이름").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(add_box, textvariable=self.product_name).grid(
            row=1, column=1, sticky="ew", padx=8, pady=(8, 0)
        )
        ttk.Button(add_box, text="추가", command=self._add_product).grid(
            row=0, column=2, rowspan=2, sticky="ns", padx=(4, 0)
        )

        columns = ("name", "id", "state", "checked", "url")
        self.product_tree = ttk.Treeview(
            parent, columns=columns, show="headings", selectmode="extended", height=12
        )
        headings = {
            "name": "상품 이름",
            "id": "상품 ID",
            "state": "확정 상태",
            "checked": "마지막 확인",
            "url": "상품 링크",
        }
        widths = {"name": 180, "id": 110, "state": 100, "checked": 150, "url": 310}
        for column in columns:
            self.product_tree.heading(column, text=headings[column])
            self.product_tree.column(column, width=widths[column], anchor="w")
        self.product_tree.pack(fill="both", expand=True)
        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="선택 삭제", command=self._remove_products).pack(
            side="right"
        )

    def _build_notification_tab(self, parent: ttk.Frame) -> None:
        discord = ttk.LabelFrame(parent, text="Discord", padding=12)
        discord.pack(fill="x")
        discord.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            discord,
            text="Discord 알림 사용",
            variable=self.discord_enabled,
            command=self._update_channel_states,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(discord, text="Webhook URL").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        self.discord_entry = ttk.Entry(
            discord, textvariable=self.discord_webhook, show="*"
        )
        self.discord_entry.grid(
            row=1, column=1, sticky="ew", padx=(10, 0), pady=(10, 0)
        )

        telegram = ttk.LabelFrame(parent, text="Telegram", padding=12)
        telegram.pack(fill="x", pady=(12, 0))
        telegram.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            telegram,
            text="Telegram 알림 사용",
            variable=self.telegram_enabled,
            command=self._update_channel_states,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(telegram, text="Bot Token").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        self.telegram_token_entry = ttk.Entry(
            telegram, textvariable=self.telegram_token, show="*"
        )
        self.telegram_token_entry.grid(
            row=1, column=1, sticky="ew", padx=(10, 0), pady=(10, 0)
        )
        ttk.Label(telegram, text="Chat ID").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        self.telegram_chat_entry = ttk.Entry(
            telegram, textvariable=self.telegram_chat_id, show="*"
        )
        self.telegram_chat_entry.grid(
            row=2, column=1, sticky="ew", padx=(10, 0), pady=(10, 0)
        )
        ttk.Label(
            telegram,
            text="Telegram은 링크 하나가 아니라 Bot Token과 Chat ID가 모두 필요합니다.",
            style="Muted.TLabel",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Checkbutton(
            parent,
            text="비밀값 표시",
            variable=self.reveal_secrets,
            command=self._toggle_secret_visibility,
        ).pack(anchor="w", pady=(12, 0))
        ttk.Label(
            parent,
            text=(
                "설정 저장 시 Webhook과 Token은 config.yaml이 아닌 같은 폴더의 .env에 "
                "원자적으로 저장됩니다."
            ),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(8, 0))

    def _build_operation_tab(self, parent: ttk.Frame) -> None:
        settings = ttk.LabelFrame(parent, text="확인 간격", padding=12)
        settings.pack(fill="x")
        ttk.Label(settings, text="최소(초)").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.interval_min, width=12).grid(
            row=0, column=1, padx=(8, 22)
        )
        ttk.Label(settings, text="최대(초)").grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, textvariable=self.interval_max, width=12).grid(
            row=0, column=3, padx=(8, 22)
        )
        ttk.Label(settings, text="시간대").grid(row=0, column=4, sticky="w")
        ttk.Entry(settings, textvariable=self.timezone, width=20).grid(
            row=0, column=5, padx=(8, 0)
        )
        ttk.Checkbutton(
            settings,
            text="Chrome 창 표시",
            variable=self.show_browser,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(
            settings,
            text=(
                "20~59초 간격은 저장 시 경고 확인이 필요합니다. "
                "429가 나오면 저장된 쿨다운 동안 다시 요청하지 않습니다."
            ),
            style="Muted.TLabel",
        ).grid(row=2, column=0, columnspan=6, sticky="w", pady=(10, 0))
        ttk.Label(settings, text="Chrome/Chromium 경로").grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Entry(settings, textvariable=self.chrome_binary).grid(
            row=3, column=1, columnspan=5, sticky="ew", padx=(8, 0), pady=(10, 0)
        )
        ttk.Label(settings, text="ChromeDriver 경로").grid(
            row=4, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Entry(settings, textvariable=self.chromedriver_path).grid(
            row=4, column=1, columnspan=5, sticky="ew", padx=(8, 0), pady=(10, 0)
        )
        ttk.Label(
            settings,
            text="경로는 서버나 자동 탐색이 실패하는 환경에서만 지정하세요.",
            style="Muted.TLabel",
        ).grid(row=5, column=0, columnspan=6, sticky="w", pady=(8, 0))

        actions = ttk.LabelFrame(parent, text="모니터 제어", padding=12)
        actions.pack(fill="x", pady=(12, 0))
        self.check_button = ttk.Button(actions, text="설정 확인", command=self._check)
        self.check_button.pack(side="left")
        self.test_button = ttk.Button(
            actions, text="알림 테스트", command=self._test_notifications
        )
        self.test_button.pack(side="left", padx=(8, 0))
        self.once_button = ttk.Button(
            actions, text="한 번 확인", command=lambda: self._start_monitor(once=True)
        )
        self.once_button.pack(side="left", padx=(8, 0))
        self.start_button = ttk.Button(
            actions, text="모니터 시작", command=lambda: self._start_monitor(once=False)
        )
        self.start_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(
            actions, text="중지", command=self._stop_monitor, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Label(
            parent,
            text=(
                "알림 테스트는 확인 대화상자에서 승인한 경우에만 "
                "실제 메시지를 보냅니다. "
                "'한 번 확인'과 '모니터 시작'은 실제 네이버 상품을 조회합니다."
            ),
            style="Muted.TLabel",
            wraplength=820,
        ).pack(anchor="w", pady=(16, 0))

    def _load(self) -> None:
        try:
            settings = load_ui_settings(self.config_path)
        except ConfigError as exc:
            messagebox.showerror("설정 읽기 오류", str(exc))
            self.status_text.set("설정 파일을 읽지 못했습니다.")
            return
        self.store_slug.set(settings.store_slug)
        self.channel_id.set(settings.channel_id)
        self.interval_min.set(str(settings.interval_min_seconds))
        self.interval_max.set(str(settings.interval_max_seconds))
        self.timezone.set(settings.timezone)
        self.show_browser.set(settings.show_browser)
        self.chrome_binary.set(settings.chrome_binary)
        self.chromedriver_path.set(settings.chromedriver_path)
        self.discord_enabled.set(settings.discord_enabled)
        self.discord_webhook.set(settings.discord_webhook_url)
        self.telegram_enabled.set(settings.telegram_enabled)
        self.telegram_token.set(settings.telegram_bot_token)
        self.telegram_chat_id.set(settings.telegram_chat_id)
        for product in settings.products:
            self.products[product.product_id] = product
            self._insert_product(product)
        self._update_channel_states()
        if self.config_path.exists():
            self.status_text.set(f"설정을 불러왔습니다: {self.config_path.name}")
        else:
            self.status_text.set("새 설정입니다. 상품과 알림 채널을 입력하세요.")

    def _add_product(self) -> None:
        try:
            parsed = parse_product_url(self.product_url.get())
        except ConfigError as exc:
            messagebox.showerror("상품 링크 오류", str(exc))
            return
        current_slug = self.store_slug.get()
        if current_slug and current_slug != parsed.slug:
            messagebox.showerror(
                "다른 스토어",
                "한 설정 파일에는 같은 브랜드스토어 상품만 추가할 수 있습니다.",
            )
            return
        if parsed.product_id in self.products:
            messagebox.showwarning("중복 상품", "이미 추가된 상품입니다.")
            return
        name = self.product_name.get().strip() or f"상품 {parsed.product_id}"
        product = UiProduct(name, parsed.url, parsed.product_id)
        self.products[product.product_id] = product
        self.store_slug.set(parsed.slug)
        self._insert_product(product)
        self.product_url.set("")
        self.product_name.set("")
        self.status_text.set(
            f"{name} 상품을 추가했습니다. 설정 저장을 눌러 확정하세요."
        )

    def _insert_product(self, product: UiProduct) -> None:
        self.product_tree.insert(
            "",
            "end",
            iid=product.product_id,
            values=(product.name, product.product_id, "-", "-", product.url),
        )

    def _remove_products(self) -> None:
        selected = self.product_tree.selection()
        if not selected:
            return
        for product_id in selected:
            self.products.pop(product_id, None)
            self.product_tree.delete(product_id)
        if not self.products:
            self.store_slug.set("")
        self.status_text.set("선택한 상품을 삭제했습니다. 설정 저장을 눌러 확정하세요.")

    def _collect_settings(self) -> UiSettings:
        try:
            interval_min = int(self.interval_min.get())
            interval_max = int(self.interval_max.get())
        except ValueError as exc:
            raise ConfigError("확인 간격은 정수로 입력하세요.") from exc
        return UiSettings(
            store_slug=self.store_slug.get().strip(),
            channel_id=self.channel_id.get().strip(),
            products=list(self.products.values()),
            interval_min_seconds=interval_min,
            interval_max_seconds=interval_max,
            timezone=self.timezone.get().strip(),
            show_browser=self.show_browser.get(),
            chrome_binary=self.chrome_binary.get().strip(),
            chromedriver_path=self.chromedriver_path.get().strip(),
            discord_enabled=self.discord_enabled.get(),
            discord_webhook_url=self.discord_webhook.get().strip(),
            telegram_enabled=self.telegram_enabled.get(),
            telegram_bot_token=self.telegram_token.get().strip(),
            telegram_chat_id=self.telegram_chat_id.get().strip(),
        )

    def _save(self, *, show_success: bool = True) -> bool:
        try:
            settings = self._collect_settings()
            short_pair = (
                settings.interval_min_seconds,
                settings.interval_max_seconds,
            )
            if (
                settings.interval_min_seconds < 60
                and self.confirmed_short_interval != short_pair
            ):
                confirmed = messagebox.askyesno(
                    "짧은 확인 간격",
                    "60초 미만의 확인 간격은 네이버의 요청 제한 가능성을 높일 수 "
                    "있습니다. 429가 발생하면 즉시 요청을 중단합니다. 이 간격을 "
                    "저장하시겠습니까?",
                )
                if not confirmed:
                    self.status_text.set("짧은 확인 간격 저장을 취소했습니다.")
                    return False
                self.confirmed_short_interval = short_pair
            save_ui_settings(self.config_path, settings)
            load_config(self.config_path)
        except ConfigError as exc:
            messagebox.showerror("설정 오류", str(exc))
            self.status_text.set("설정을 저장하지 못했습니다.")
            return False
        self.status_text.set("config.yaml과 .env를 안전하게 저장했습니다.")
        if show_success:
            messagebox.showinfo("저장 완료", "설정과 비밀값을 안전하게 저장했습니다.")
        return True

    def _check(self) -> None:
        if not self._save(show_success=False):
            return
        config = load_config(self.config_path)
        channels = []
        if config.notifications.discord_enabled:
            channels.append("Discord")
        if config.notifications.telegram_enabled:
            channels.append("Telegram")
        messagebox.showinfo(
            "설정 확인",
            f"상품 {len(config.products)}개와 {', '.join(channels)} 설정이 올바릅니다.",
        )

    def _test_notifications(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        if not self._save(show_success=False):
            return
        if not messagebox.askyesno(
            "실제 알림 전송",
            "활성화된 알림 채널에 실제 테스트 메시지를 보내시겠습니까?",
        ):
            self.status_text.set("알림 테스트를 취소했습니다.")
            return
        self._set_busy(True, stoppable=False)
        self.status_text.set("알림 테스트 메시지를 전송하고 있습니다.")
        self.worker = threading.Thread(target=self._notification_worker, daemon=True)
        self.worker.start()

    def _notification_worker(self) -> None:
        dispatcher = None
        try:
            config = load_config(self.config_path)
            configure_logging(config)
            dispatcher = build_dispatcher(config)
            now = datetime.now(ZoneInfo(config.monitor.timezone))
            result = dispatcher.send_with_retry(
                Alert(
                    product_id="test",
                    product_name="알림 설정 테스트",
                    product_url="https://brand.naver.com/",
                    occurred_at=now.isoformat(),
                    is_test=True,
                )
            )
            if result.failed_attempts:
                failed = ", ".join(sorted(result.failed_attempts))
                self.events.put((False, f"알림 테스트 실패: {failed}"))
            else:
                self.events.put((True, "모든 알림 채널의 테스트가 성공했습니다."))
        except (
            ConfigError,
            CooldownActiveError,
            StateStoreError,
            StockClientError,
        ) as exc:
            self.events.put((False, str(exc)))
        except Exception:
            LOGGER.exception("UI 알림 테스트 중 예상하지 못한 오류")
            self.events.put(
                (False, "예상하지 못한 오류가 발생했습니다. 로그를 확인하세요.")
            )
        finally:
            if dispatcher is not None:
                dispatcher.close()

    def _start_monitor(self, *, once: bool) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        if not self._save(show_success=False):
            return
        action = "상품을 한 번 확인" if once else "모니터링을 시작"
        if not messagebox.askyesno(
            "실제 상품 조회",
            f"실제 네이버 상품에 접속해 {action}하시겠습니까?",
        ):
            return
        self.stop_event = threading.Event()
        self._set_busy(True, stoppable=True)
        self.status_text.set(
            "한 번 확인을 시작합니다." if once else "모니터링 중입니다."
        )
        self.worker = threading.Thread(
            target=self._monitor_worker, args=(once,), daemon=True
        )
        self.worker.start()

    def _monitor_worker(self, once: bool) -> None:
        dispatcher = None
        client = None
        monitor_started = False
        lock = None
        try:
            config = load_config(self.config_path)
            configure_logging(config)
            lock = SingleInstanceLock(lock_path_for_state(config.state_file))
            lock.acquire()
            dispatcher = build_dispatcher(config)
            client = SeleniumStockClient(
                config.store, config.monitor, config.products[0]
            )
            monitor = RestockMonitor(
                config,
                client,
                dispatcher,
                JsonStateStore(config.state_file),
            )
            monitor_started = True
            assert self.stop_event is not None
            monitor.run(once=once, stop_event=self.stop_event)
            message = (
                "한 번 확인을 완료했습니다." if once else "모니터링을 중지했습니다."
            )
            self.events.put((True, message))
        except (
            ConfigError,
            AlreadyRunningError,
            CooldownActiveError,
            StateStoreError,
            StockClientError,
        ) as exc:
            self.events.put((False, str(exc)))
        except Exception:
            LOGGER.exception("UI 모니터 실행 중 예상하지 못한 오류")
            self.events.put(
                (False, "예상하지 못한 오류가 발생했습니다. 로그를 확인하세요.")
            )
        finally:
            if not monitor_started:
                if client is not None:
                    client.close()
                if dispatcher is not None:
                    dispatcher.close()
            if lock is not None:
                lock.release()

    def _stop_monitor(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
            self.status_text.set("안전하게 중지하는 중입니다.")
            self.stop_button.configure(state="disabled")

    def _set_busy(self, busy: bool, *, stoppable: bool = False) -> None:
        state = "disabled" if busy else "normal"
        for button in (
            self.save_button,
            self.check_button,
            self.test_button,
            self.once_button,
            self.start_button,
        ):
            button.configure(state=state)
        self.stop_button.configure(state="normal" if busy and stoppable else "disabled")

    def _poll_worker(self) -> None:
        try:
            while True:
                success, message = self.events.get_nowait()
                self.status_text.set(message)
                self._set_busy(False)
                if not self.closing:
                    if success:
                        messagebox.showinfo("완료", message)
                    else:
                        messagebox.showerror("실행 오류", message)
        except queue.Empty:
            pass
        if self.closing and (self.worker is None or not self.worker.is_alive()):
            self.root.destroy()
            return
        self.root.after(200, self._poll_worker)

    def _refresh_product_states(self) -> None:
        if self.closing:
            return
        try:
            state_path = state_path_from_config(self.config_path)
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            states = raw.get("products", {}) if isinstance(raw, dict) else {}
            if isinstance(states, dict):
                for product_id, value in states.items():
                    if product_id not in self.products or not isinstance(value, dict):
                        continue
                    current = list(self.product_tree.item(product_id, "values"))
                    if len(current) == 5:
                        current[2] = str(value.get("confirmed_state", "unknown"))
                        current[3] = str(value.get("last_checked_at") or "-")
                        self.product_tree.item(product_id, values=current)
        except (ConfigError, OSError, UnicodeError, json.JSONDecodeError):
            pass
        self.root.after(1_000, self._refresh_product_states)

    def _update_channel_states(self) -> None:
        self.discord_entry.configure(
            state="normal" if self.discord_enabled.get() else "disabled"
        )
        telegram_state = "normal" if self.telegram_enabled.get() else "disabled"
        self.telegram_token_entry.configure(state=telegram_state)
        self.telegram_chat_entry.configure(state=telegram_state)

    def _toggle_secret_visibility(self) -> None:
        mask = "" if self.reveal_secrets.get() else "*"
        self.discord_entry.configure(show=mask)
        self.telegram_token_entry.configure(show=mask)
        self.telegram_chat_entry.configure(show=mask)

    def _request_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno(
                "종료 확인", "실행 중인 작업을 중지하고 UI를 닫으시겠습니까?"
            ):
                return
            self.closing = True
            if self.stop_event is not None:
                self.stop_event.set()
            self.status_text.set("실행을 안전하게 중지한 뒤 닫습니다.")
            return
        self.root.destroy()


def launch_ui(config_path: str | Path) -> int:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"UI를 시작할 수 없습니다: {exc}", file=sys.stderr)
        return 1
    MonitorApp(root, config_path)
    root.mainloop()
    return 0
