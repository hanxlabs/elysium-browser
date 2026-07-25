ARG DOCKER_PROXY=docker.m.daocloud.io
FROM ${DOCKER_PROXY}/library/python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/data

WORKDIR /app

# Runtime dependencies and representative fonts required by Chromium in a slim image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-freefont-ttf \
        fonts-noto-color-emoji \
        fonts-unifont \
        fonts-wqy-zenhei \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --home-dir /data --shell /usr/sbin/nologin gateway \
    && mkdir -p /app /data/.cloakbrowser \
    && chown -R gateway:gateway /app /data

COPY requirements.txt ./
RUN pip install --requirement requirements.txt

COPY --chown=gateway:gateway app ./app

USER gateway

# The pinned wrapper verifies the downloaded browser artifact before extraction.
# Keeping it in the image avoids a production first-request download.
RUN python -m cloakbrowser install

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3)"]

# CloakBrowser documents asyncio as the safe loop when hosted by uvicorn.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--loop", "asyncio"]
