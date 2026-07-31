# Naver Restock Monitor

네이버 브랜드스토어 상품을 주기적으로 확인하고, **품절에서 판매 가능 상태로 바뀌었을 때만** Discord 또는 Telegram으로 알림을 보내는 개인용 프로그램입니다.

상품 링크를 화면에서 관리하는 GUI와 화면 없는 Linux 서버용 CLI를 모두 지원합니다.

> 비공식 프로젝트이며 네이버, Discord, Telegram과 제휴하거나 승인받지 않았습니다. 네이버의 내부 API나 페이지 구조가 바뀌면 작동하지 않을 수 있습니다.

## 주요 기능

- GUI에서 상품 링크 추가·삭제 및 알림 설정
- 여러 상품 순차 모니터링
- Discord와 Telegram을 각각 또는 함께 사용
- 재시작 후에도 재고 상태와 알림 기록 유지
- `UNKNOWN` 발생 시 마지막 확정 상태 보존
- 중복 재입고 알림 방지
- 알림 실패 재시도와 중복 없는 보류 큐
- HTTP 429 쿨다운, 로그 로테이션, 안전한 종료
- Windows, macOS, Linux 및 Docker 지원

현재 버전은 `0.3.0` Alpha이며 Python 3.11 이상이 필요합니다.

## 설치

Chrome 또는 Chromium을 먼저 설치하세요.

### Windows PowerShell

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
```

Python 3.14가 아니라면 설치된 3.11 이상 버전으로 `py -3.14`를 바꾸세요. 가상환경 활성화가 막히면 다음처럼 Python을 직접 실행할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m naver_restock_monitor --config config.yaml --ui
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
cp config.example.yaml config.yaml
```

## GUI 사용법

```bash
python -m naver_restock_monitor --config config.yaml --ui
```

1. **상품** 탭에 `channel_id`, 상품 링크와 표시 이름을 입력합니다.
2. **알림** 탭에서 Discord 또는 Telegram을 하나 이상 설정합니다.
3. **실행** 탭에서 확인 간격과 Chrome 표시 여부를 선택합니다.
4. **설정 저장 → 설정 확인 → 한 번 확인** 순서로 시험합니다.
5. 정상이라면 **모니터 시작**을 누릅니다.

`알림 테스트`는 실제 메시지를 보내므로 필요할 때만 누르세요. 비밀값은 `config.yaml`이 아닌 `.env`에 저장됩니다.

로컬에서 처음 시험할 때는 **Chrome 창 표시**를 켜는 것을 권장합니다. 일부 환경에서는 headless 실행만 HTTP 429를 받을 수 있습니다.

## CLI 상품 설정

CLI는 실행 중 URL을 묻지 않습니다. 실행 전에 `config.yaml`을 편집합니다.

URL이 다음과 같다면:

```text
https://brand.naver.com/example-store/products/1234567890
```

다음처럼 등록합니다.

```yaml
store:
  slug: example-store
  channel_id: replace-with-channel-id

products:
  - id: "1234567890"
    name: "예시 상품"

monitor:
  interval_min_seconds: 300
  interval_max_seconds: 600
  headless: false

notifications:
  discord_enabled: true
  telegram_enabled: false
```

상품을 변경할 때는 모니터를 종료하고 설정을 수정한 뒤 다시 시작하세요. 기존 상품 상태는 `var/state.json`에 유지됩니다.

### `channel_id` 확인

`channel_id`는 상품 번호와 다릅니다. Chrome 개발자 도구의 **Network** 탭에서 상품 페이지를 새로고침하고 다음 형태의 요청을 찾으세요.

```text
/n/v2/channels/ABC/products/1234567890
```

위 예시에서는 `ABC`가 `channel_id`입니다. 확인되지 않으면 값을 추측하지 마세요.

## 알림 설정

실제 비밀값은 `.env`에만 입력합니다.

```dotenv
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/실제_ID/실제_토큰
TELEGRAM_BOT_TOKEN=실제_봇_토큰
TELEGRAM_CHAT_ID=실제_채팅_ID
```

- Discord만 사용: `discord_enabled: true`
- Telegram만 사용: `telegram_enabled: true`
- 둘 다 사용: 두 설정을 모두 `true`

Telegram에는 Bot Token과 Chat ID가 모두 필요합니다.

## 실행 명령

```bash
# 설정만 검사 — 외부 요청 없음
python -m naver_restock_monitor --config config.yaml --check-config

# 브라우저와 서버 환경 진단 — 상품 요청 없음
python -m naver_restock_monitor --config config.yaml --doctor

# 실제 알림 테스트
python -m naver_restock_monitor --config config.yaml --test-notifications

# 상품을 한 번만 실제 확인
python -m naver_restock_monitor --config config.yaml --once

# 계속 모니터링
python -m naver_restock_monitor --config config.yaml
```

종료할 때는 `Ctrl+C`를 누르세요.

## Linux 서버와 Docker

서버에서도 URL을 `config.yaml`에 미리 등록합니다. 화면 없는 환경에서는 탐지 회피 기능 대신 Xvfb 가상 화면에서 일반 Chromium을 실행합니다.

```bash
xvfb-run -a -s "-screen 0 1280x800x24" \
  python -m naver_restock_monitor --config config.yaml --server
```

Docker Compose 사용:

```bash
mkdir -p var
chmod 600 .env
docker compose -f docker-compose.server.yml build
docker compose -f docker-compose.server.yml up -d
docker compose -f docker-compose.server.yml logs -f --tail=100
```

Oracle Cloud와 systemd를 포함한 내용은 [서버 배포 안내](docs/SERVER.md)를 참고하세요. Linux ARM64에서는 Selenium Manager 대신 배포판의 Chromium·ChromeDriver 또는 제공된 Docker 구성을 권장합니다.

## 재고 판정과 요청 간격

다음 두 필드가 함께 일치할 때만 재고 상태를 확정합니다.

| 응답 | 판정 |
|---|---|
| `soldout: true` + `productStatusType: OUTOFSTOCK` | `OUT_OF_STOCK` |
| `soldout: false` + `productStatusType: SALE` | `IN_STOCK` |
| 누락, 자료형 오류, 충돌 또는 예상 밖의 값 | `UNKNOWN` |

`UNKNOWN`은 마지막 확정 상태를 지우지 않습니다. 기본 확인 간격은 5~10분이며, 20초 미만은 허용하지 않고 60초 미만은 경고합니다.

HTTP 429가 발생하면 설정된 시간 동안 요청을 중단하고 해제 시각을 상태 파일에 저장합니다. 재시작이나 상태 파일 삭제로 쿨다운을 우회하지 마세요.

## 문제 해결

- **Chrome 실행 실패:** `--doctor`를 실행하고 Chrome·ChromeDriver 경로와 버전을 확인합니다.
- **계속 `UNKNOWN`:** 네트워크 오류, 접근 제한 또는 내부 API 변경 가능성이 있습니다.
- **HTTP 429:** 실행을 반복하지 말고 충분히 기다린 뒤 확인 간격을 늘립니다.
- **UI 실행 실패:** Tkinter 설치 여부를 확인합니다. 화면 없는 서버에서는 CLI를 사용합니다.
- **다른 스토어 상품 추가:** 스토어마다 별도 설정 파일과 상태 파일로 실행합니다.

CAPTCHA, 로그인 제한 또는 접근 제한을 우회하는 기능은 제공하지 않습니다.

## 개발 검사

```bash
python -m pip install -e ".[dev]"
ruff format --check src tests
ruff check src tests
mypy src
pytest -q
```

자동 테스트와 GitHub Actions는 실제 네이버·Discord·Telegram에 접속하지 않고 mock/fake만 사용합니다.

## 보안과 책임 있는 사용

- `.env`, `config.yaml`, `var/`, 로그와 상태 파일을 Git에 올리지 마세요.
- Webhook이나 Bot Token이 노출됐다면 즉시 폐기하고 재발급하세요.
- 소수의 개인 관심 상품만 보수적인 간격으로 확인하세요.
- 대량 수집, 구매 자동화, CAPTCHA·접근 제한 우회 또는 탐지 회피에 사용하지 마세요.
- 내부 API 변경, 알림 지연, 재고 오판, 구매 실패 또는 서비스 제한 가능성이 있습니다.

취약점 제보는 [SECURITY.md](SECURITY.md)를 참고하세요.

## 라이선스

현재 [MIT License](LICENSE) 후보가 포함되어 있습니다. GitHub 게시 전 저장소 소유자가 최종 라이선스를 확인해야 하며, 별도 승인 전에는 저장소 생성·커밋·푸시를 진행하지 않습니다.
