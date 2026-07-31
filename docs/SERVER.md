# 서버 배포 안내

이 문서는 화면 없는 Linux 서버, 특히 Oracle Cloud Always Free Compute에서 CLI 버전을
운영하는 방법을 설명합니다. 서버에서는 Tkinter UI를 실행하지 않습니다. 로컬 UI로 만든
`config.yaml`과 `.env`를 서버로 안전하게 복사한 뒤 동일한 모니터 코어를 CLI로 실행합니다.

## 권장 구성

Oracle Always Free에는 AMD 기반 `VM.Standard.E2.1.Micro`와 ARM 기반
`VM.Standard.A1.Flex`가 있습니다. Chrome/Chromium의 메모리 사용량을 고려하면 가능한 경우
Ampere A1을 권장합니다. Oracle의 현재 무료 한도와 홈 리전 제공 여부는 생성 전에 공식
문서에서 다시 확인하세요.

Linux ARM64에서는 Selenium Manager가 ChromeDriver를 자동 설치하지 못합니다. 이 프로젝트의
Docker 이미지는 배포판의 `chromium`과 `chromium-driver`를 함께 설치하고 경로를 명시해 이
문제를 피합니다.

서버 권장 실행 구조:

```text
systemd 또는 Docker Compose
        ↓
CLI --server
        ↓
Xvfb 가상 화면
        ↓
일반 Chromium(headless 아님)
```

Xvfb는 화면만 가상으로 제공하며 자동화 탐지 정보를 숨기지 않습니다. 데이터센터 IP나
서비스 정책에 따라 HTTP 429가 발생할 수 있으며 동작을 보장하지 않습니다.

## 배포 전 로컬 준비

로컬 UI에서 상품과 알림을 설정하고 다음 파일을 준비합니다.

- `config.yaml`
- `.env`

서버 설정은 기본적으로 다음과 같이 둡니다.

```yaml
monitor:
  headless: false
  chrome_binary: null
  chromedriver_path: null
  interval_min_seconds: 300
  interval_max_seconds: 600
```

Docker에서는 환경변수가 컨테이너 경로를 자동으로 지정합니다. 직접 설치할 때만 실제 경로를
YAML 또는 다음 환경변수로 설정하세요.

```text
CHROME_BINARY=/usr/bin/chromium
CHROMEDRIVER_PATH=/usr/bin/chromedriver
```

## Docker Compose 배포 — 권장

Docker와 Compose 플러그인이 설치된 Ubuntu 또는 Oracle Linux 서버를 준비합니다. 저장소
파일을 서버로 복사한 뒤 프로젝트 폴더에서 실행합니다.

```bash
mkdir -p var
chmod 700 var
chmod 600 .env
docker compose -f docker-compose.server.yml build
docker compose -f docker-compose.server.yml run --rm monitor \
  python -m naver_restock_monitor --config /app/config.yaml --doctor
docker compose -f docker-compose.server.yml up -d
```

로그 확인:

```bash
docker compose -f docker-compose.server.yml logs -f --tail=100
```

정상 종료와 재시작:

```bash
docker compose -f docker-compose.server.yml stop
docker compose -f docker-compose.server.yml start
```

설정 변경 후에는 다음 명령으로 재시작합니다.

```bash
docker compose -f docker-compose.server.yml restart
```

컨테이너는 외부 포트를 열지 않습니다. SSH 이외에 이 프로그램을 위한 인바운드 포트는
필요하지 않습니다.

## 직접 설치와 systemd

배포판 패키지 관리자로 다음 구성요소를 설치합니다. 정확한 패키지 이름은 OS와 아키텍처에
따라 다릅니다.

- Python 3.11 이상
- Chromium 또는 Google Chrome
- 같은 주 버전의 ChromeDriver
- Xvfb 및 xauth
- 한글 폰트

전용 사용자와 폴더를 만들고 코드를 `/opt/naver-restock-monitor`에 설치했다고 가정합니다.

```bash
python3 -m venv /opt/naver-restock-monitor/.venv
/opt/naver-restock-monitor/.venv/bin/python -m pip install \
  /opt/naver-restock-monitor
chmod 600 /opt/naver-restock-monitor/.env
mkdir -p /opt/naver-restock-monitor/var
```

먼저 Xvfb 안에서 진단합니다.

```bash
xvfb-run -a -s "-screen 0 1280x800x24" \
  .venv/bin/python -m naver_restock_monitor \
  --config config.yaml --doctor
```

`deploy/systemd/naver-restock-monitor.service.example`의 사용자, 경로, 브라우저 경로를 서버에
맞게 수정한 뒤 설치합니다.

```bash
sudo cp deploy/systemd/naver-restock-monitor.service.example \
  /etc/systemd/system/naver-restock-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now naver-restock-monitor
sudo systemctl status naver-restock-monitor
sudo journalctl -u naver-restock-monitor -f
```

## CLI 명령

환경 진단만 수행하며 외부 상품 요청은 하지 않습니다.

```bash
python -m naver_restock_monitor --config config.yaml --doctor
```

서버 환경을 검사한 뒤 계속 모니터링합니다.

```bash
xvfb-run -a -s "-screen 0 1280x800x24" \
  python -m naver_restock_monitor --config config.yaml --server
```

`--server`는 일반 CLI 모니터와 같은 기능을 사용하지만 브라우저, 드라이버, DISPLAY와 쓰기
경로를 먼저 확인합니다.

## 운영 안전

- 하나의 `state_file`에는 한 프로세스만 실행할 수 있습니다. 두 번째 프로세스는 잠금 오류로
  종료됩니다.
- `var/state.json`, 보류 알림, 429 쿨다운은 재시작 후에도 유지됩니다.
- 429가 발생하면 상태 파일이나 잠금 파일을 삭제해 우회하지 마세요.
- Docker와 systemd를 동시에 실행하지 마세요.
- `.env`를 이미지에 복사하거나 Git에 커밋하지 마세요.
- Oracle 보안 목록에서 불필요한 인바운드 포트를 열지 마세요.
- 유휴 Always Free 인스턴스는 Oracle 정책에 따라 회수될 수 있습니다.

## 공식 참고 자료

- Oracle Cloud Free Tier: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm
- Oracle Always Free Resources: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- Selenium Manager: https://www.selenium.dev/documentation/selenium_manager/
