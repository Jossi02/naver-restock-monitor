from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from .models import AppConfig


@dataclass(frozen=True)
class Diagnostic:
    level: str
    label: str
    message: str


def run_diagnostics(config: AppConfig, *, server_mode: bool) -> list[Diagnostic]:
    results = [
        Diagnostic("ok", "Python", platform.python_version()),
        Diagnostic(
            "ok",
            "플랫폼",
            f"{platform.system()} {platform.machine()}",
        ),
    ]
    browser = _find_browser(config.monitor.chrome_binary)
    if browser is None:
        results.append(
            Diagnostic(
                "error",
                "Chrome/Chromium",
                "브라우저를 찾지 못했습니다. chrome_binary 또는 "
                "CHROME_BINARY를 지정하세요.",
            )
        )
    else:
        results.append(Diagnostic("ok", "Chrome/Chromium", str(browser)))

    driver = _find_driver(config.monitor.chromedriver_path)
    machine = platform.machine().lower()
    linux_arm = platform.system() == "Linux" and machine in {
        "aarch64",
        "arm64",
    }
    if driver is not None:
        results.append(Diagnostic("ok", "ChromeDriver", str(driver)))
    elif linux_arm:
        results.append(
            Diagnostic(
                "error",
                "ChromeDriver",
                "Linux ARM64에서는 Selenium Manager를 사용할 수 없습니다. "
                "chromedriver_path 또는 CHROMEDRIVER_PATH를 지정하세요.",
            )
        )
    else:
        results.append(
            Diagnostic(
                "warning",
                "ChromeDriver",
                "명시적 드라이버가 없습니다. Selenium Manager를 사용합니다.",
            )
        )

    if platform.system() == "Linux" and not config.monitor.headless:
        display = os.getenv("DISPLAY")
        if display:
            results.append(Diagnostic("ok", "가상 화면", f"DISPLAY={display}"))
        elif server_mode:
            results.append(
                Diagnostic(
                    "error",
                    "가상 화면",
                    "DISPLAY가 없습니다. xvfb-run으로 --server를 실행하세요.",
                )
            )
        else:
            results.append(
                Diagnostic(
                    "warning",
                    "가상 화면",
                    "DISPLAY가 없습니다. 서버에서는 Xvfb가 필요합니다.",
                )
            )
    elif config.monitor.headless:
        results.append(
            Diagnostic(
                "warning",
                "브라우저 모드",
                "headless 모드는 환경에 따라 HTTP 429가 발생할 수 있습니다.",
            )
        )
    else:
        results.append(Diagnostic("ok", "브라우저 모드", "일반 Chrome"))

    for label, path in (
        ("상태 경로", Path(config.state_file)),
        ("로그 경로", Path(config.logging.file)),
    ):
        if _parent_is_writable(path):
            results.append(Diagnostic("ok", label, str(path)))
        else:
            results.append(
                Diagnostic("error", label, f"상위 폴더에 쓸 수 없습니다: {path}")
            )
    if config.monitor.interval_min_seconds < 60:
        results.append(
            Diagnostic(
                "warning",
                "확인 간격",
                f"최소 {config.monitor.interval_min_seconds:.0f}초로 "
                "짧게 설정됐습니다.",
            )
        )
    else:
        results.append(
            Diagnostic(
                "ok",
                "확인 간격",
                f"{config.monitor.interval_min_seconds:.0f}~"
                f"{config.monitor.interval_max_seconds:.0f}초",
            )
        )
    return results


def has_errors(results: list[Diagnostic]) -> bool:
    return any(result.level == "error" for result in results)


def format_diagnostics(results: list[Diagnostic]) -> str:
    icons = {"ok": "OK", "warning": "WARN", "error": "ERROR"}
    return "\n".join(
        f"[{icons[result.level]}] {result.label}: {result.message}"
        for result in results
    )


def _find_browser(configured: str | None) -> Path | None:
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        found = shutil.which(name)
        if found:
            return Path(found)
    if platform.system() == "Windows":
        roots = [
            os.getenv("PROGRAMFILES"),
            os.getenv("PROGRAMFILES(X86)"),
            os.getenv("LOCALAPPDATA"),
        ]
        for root in roots:
            if not root:
                continue
            candidate = Path(root) / "Google/Chrome/Application/chrome.exe"
            if candidate.is_file():
                return candidate
    return None


def _find_driver(configured: str | None) -> Path | None:
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    found = shutil.which("chromedriver")
    return Path(found) if found else None


def _parent_is_writable(path: Path) -> bool:
    current = path.parent
    while not current.exists() and current != current.parent:
        current = current.parent
    return current.is_dir() and os.access(current, os.W_OK)
