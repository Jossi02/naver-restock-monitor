# Naver Restock Monitor

네이버 브랜드스토어 상품을 주기적으로 확인하고, 확정된 품절 상태가 판매 가능 상태로 바뀌었을 때 Discord 또는 Telegram으로 알림을 보내는 비공식 개인용 모니터입니다. GUI와 Linux 서버용 CLI를 같은 모니터 코어 위에서 제공합니다.

> 네이버·Discord·Telegram의 공식 또는 제휴 프로젝트가 아닙니다. 네이버 내부 endpoint나 schema가 바뀌면 동작하지 않을 수 있으며, CAPTCHA·로그인·접근 제한 우회와 자동 구매 기능은 제공하지 않습니다.

## Overview

- `soldout`과 `productStatusType`을 함께 확인하는 보수적인 재고 판정
- 각각의 `OUT_OF_STOCK` → `IN_STOCK` 전환에 대한 재입고 알림
- Discord·Telegram의 독립적인 전송과 재시도
- 확정 상태, 보류 알림, HTTP 429 cooldown의 JSON persistence
- GUI 설정·실행과 CLI 일회/지속 모니터링
- 무작위 polling 간격과 상품 사이 요청 지연
- Xvfb, Docker Compose, systemd를 이용한 Linux 서버 실행 구성

현재 버전은 `0.3.0` Alpha이며 Python 3.11 이상이 필요합니다.

## How It Works

```text
config.yaml + .env
        ↓
Chromium/Selenium session
        ↓
상품 페이지와 같은 origin에서 browser fetch
        ↓
재고 상태 분류
        ↓
이전 confirmed state와 비교
        ↓
Discord/Telegram 전송 또는 보류
        ↓
state 저장 → 다음 polling cycle
```

Naver endpoint를 Python `requests`로 직접 호출하지 않습니다. Selenium이 Chromium에서 상품 페이지를 먼저 연 뒤, 그 browser session 안에서 `credentials: include`인 same-origin fetch를 실행합니다. 따라서 현재 browser session의 cookie는 요청에 사용되지만, 애플리케이션이 cookie나 browser profile을 별도 persistent storage에 저장하거나 다음 실행에서 재사용하지는 않습니다.

## Stock State and Notification Rules

두 응답 필드가 함께 일치할 때만 재고 상태를 확정합니다.

| `soldout` | `productStatusType` | 판정 |
|---|---|---|
| `true` | `OUTOFSTOCK` | `OUT_OF_STOCK` |
| `false` | `SALE` | `IN_STOCK` |
| 누락·자료형 오류·충돌·기타 값 |  | `UNKNOWN` |

- `UNKNOWN`은 마지막 confirmed state를 덮어쓰지 않습니다.
- 지속적인 `IN_STOCK`에서는 반복 알림을 보내지 않습니다.
- 각각의 genuine `OUT_OF_STOCK` → `IN_STOCK` 전환은 새로운 restock event입니다.
- 기본값에서는 최초 관찰이 `IN_STOCK`인 상품을 알리지 않습니다.
- `notify_initial_in_stock: true`로 최초 `IN_STOCK` 알림을 선택할 수 있습니다.

## Reliability and Operations

| 영역 | 구현 |
|---|---|
| State | 임시 파일, `fsync`, 교체를 이용한 atomic JSON 저장; 손상 파일 격리; timezone-aware timestamp 검증 |
| Notification | retryable/permanent 오류 구분, provider `Retry-After` 반영, channel별 보류 재시도 |
| Pending policy | 상품별 최신 restock event로 coalesce하며 이미 성공한 channel은 보류 대상에서 제외 |
| Request control | 기본 5~10분 무작위 polling, 상품 사이 jitter, 재시작 후에도 유지되는 HTTP 429 cooldown |
| Lifecycle | single-instance lock, SIGINT/SIGTERM graceful shutdown, 종료 신호를 확인하는 retry와 state 저장 |
| Logging | rotating file log와 설정된 secret 값 redaction |
| Server | non-root Docker 사용자, systemd sandboxing, inbound application port가 필요 없는 outbound-only 구조 |

외부 알림 서비스와 process crash 사이의 모든 timing을 통제할 수 없으므로 exactly-once delivery를 보장하지는 않습니다.

## Quick Start

Chrome 또는 Chromium과 Python 3.11 이상이 필요합니다.

```bash
python -m venv .venv
```

가상환경을 활성화합니다.

```bash
# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
python -m pip install -e .
```

example 파일을 복사하고 placeholder를 실제 로컬 설정으로 바꿉니다.

```bash
# macOS / Linux
cp .env.example .env
cp config.example.yaml config.yaml

# Windows PowerShell
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
```

```bash
# 설정 확인: 외부 요청 없음
python -m naver_restock_monitor --config config.yaml --check-config

# 상품을 한 번 확인
python -m naver_restock_monitor --config config.yaml --once

# 지속 모니터링
python -m naver_restock_monitor --config config.yaml

# GUI
python -m naver_restock_monitor --config config.yaml --ui
```

브라우저와 server 실행 환경만 진단하려면 `--doctor`, 실제 알림 설정을 시험하려면 `--test-notifications`를 사용합니다. 알림 테스트는 실제 Discord 또는 Telegram 메시지를 전송합니다.

## Configuration

전체 기본값은 [`config.example.yaml`](config.example.yaml)과 [`.env.example`](.env.example)을 기준으로 합니다.

| 설정 | 용도 |
|---|---|
| `store.slug` | 브랜드스토어 URL의 store slug |
| `store.channel_id` | 상품 endpoint의 channel 식별자 |
| `products[].id`, `products[].name` | 모니터링할 상품 번호와 표시 이름 |
| `interval_min_seconds`, `interval_max_seconds` | polling 범위; 기본 300~600초 |
| `discord_enabled`, `telegram_enabled` | 사용할 알림 channel 선택 |
| `.env` | Discord webhook, Telegram bot token·chat ID |

`channel_id`는 상품 번호와 다릅니다. 상품 페이지를 연 상태에서 Chrome 개발자 도구의 Network 탭에 나타나는 `/n/v2/channels/{channel_id}/products/{product_id}` 요청에서 확인하고, 보이지 않으면 추측하지 마세요.

기본 polling 간격은 5~10분입니다. 20초 미만은 설정 단계에서 거부하고 60초 미만은 경고합니다. HTTP 429가 발생하면 cooldown 종료 시각을 state에 저장하므로 재시작이나 state 삭제로 우회하지 마세요.

## Server Deployment

화면 없는 Linux 서버에서는 Xvfb 안에서 일반 Chromium을 실행합니다. Docker 이미지는 Chromium·ChromeDriver·Xvfb를 포함하고 non-root 사용자로 실행하며, Compose는 `config.yaml`과 `.env`를 read-only로 연결하고 `var/`를 영속화합니다.

```bash
docker compose -f docker-compose.server.yml build
docker compose -f docker-compose.server.yml up -d
```

직접 설치할 때는 [`deploy/systemd/naver-restock-monitor.service.example`](deploy/systemd/naver-restock-monitor.service.example)을 사용할 수 있습니다. Xvfb, Docker Compose, systemd, ARM64 참고사항과 `var/` 권한은 [서버 배포 안내](docs/SERVER.md)에 정리되어 있습니다.

## Validation

| 범위 | 환경 | 결과 |
|---|---|---|
| GitHub Actions | Ubuntu, Python 3.11·3.12·3.13·3.14 | Ruff format, Ruff lint, mypy, pytest 통과 |
| Test suite | fake/mock 기반 unit·integration test | 100 tests |
| Local hardening pass | Windows, Python 3.12 | 100 passed |
| Docker build/runtime | 현재 hardening pass | 검증하지 않음 |
| macOS runtime | 현재 CI | 검증 대상 아님 |

자동 테스트와 CI는 실제 Naver·Discord·Telegram에 접속하지 않습니다. Windows, macOS, Linux, Docker는 실행 구성을 제공하지만, 위 표의 CI 검증 범위와는 구분됩니다.

## Known Limitations

- Naver 내부 endpoint나 response schema 변경 시 상태 확인이 중단될 수 있습니다.
- 알림 서비스가 요청을 수신한 직후 timeout이나 process crash가 발생하면 중복 전송 가능성이 있으며, 외부 알림의 exactly-once delivery는 보장하지 않습니다.
- pending state는 historical event database가 아니라 상품별 latest-event coalescing 정책입니다.
- bounded pending queue가 가득 차면 새로운 상품의 실패 알림을 저장하지 못하고 로그만 남길 수 있습니다.
- Docker build/runtime와 macOS 실행은 현재 CI에서 검증하지 않습니다.

## Responsible Use

- 소수의 개인 관심 상품만 보수적인 polling 간격으로 확인하세요.
- 대량 수집이나 자동 구매를 위한 프로그램이 아닙니다.
- CAPTCHA, 로그인·접근 제한, automation detection 우회 기능은 제공하지 않습니다.
- stealth browser, fingerprint 변조, proxy rotation을 사용하지 않습니다.
- HTTP 429 cooldown을 준수하고 state나 lock 파일 삭제로 우회하지 마세요.
- `.env`, `config.yaml`, `var/`, log와 state 파일을 Git에 커밋하지 마세요.

Webhook이나 token이 노출됐다면 즉시 폐기하고 재발급하세요. 취약점 제보는 [SECURITY.md](SECURITY.md)를 참고하세요.

## Development

```bash
python -m pip install -e ".[dev]"
ruff format --check src tests
ruff check src tests
mypy src
pytest -q
```

## License

이 프로젝트에는 [MIT License](LICENSE)가 적용됩니다.
