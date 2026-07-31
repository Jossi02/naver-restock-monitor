FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-noto-cjk \
        tini \
        tzdata \
        xauth \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system monitor \
    && useradd --system --gid monitor --create-home monitor

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install .

RUN mkdir -p /app/var && chown -R monitor:monitor /app/var
USER monitor

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", "python", "-m", "naver_restock_monitor", "--config", "/app/config.yaml", "--server"]
