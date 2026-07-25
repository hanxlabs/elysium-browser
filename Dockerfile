ARG DOCKER_PROXY=docker.m.daocloud.io
FROM ${DOCKER_PROXY}/library/python:3.12-slim-bookworm
ARG APT_MIRROR=https://mirrors.aliyun.com/debian
ARG APT_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CLOAKBROWSER_AUTO_UPDATE=false \
    CLOAKBROWSER_WIDEVINE=0 \
    HOME=/data

WORKDIR /app

# Keep Python dependency downloads reusable even when a later system package or
# application source layer changes. BuildKit owns this cache, so it is not
# included in the final image.
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --requirement requirements.txt

# Runtime dependencies and representative fonts required by Chromium in a slim image.
RUN APT_MIRROR_CLEAN="$(echo "${APT_MIRROR}" | sed 's#[[:space:]]##g; s#/*$##')" \
    && APT_SECURITY_MIRROR_CLEAN="$(echo "${APT_SECURITY_MIRROR}" | sed 's#[[:space:]]##g; s#/*$##')" \
    && if [ -n "${APT_MIRROR_CLEAN}" ] && [ -f /etc/apt/sources.list ]; then \
        sed -i "s|http://deb.debian.org/debian|${APT_MIRROR_CLEAN}|g" /etc/apt/sources.list; \
    fi \
    && if [ -n "${APT_MIRROR_CLEAN}" ] && [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|http://deb.debian.org/debian|${APT_MIRROR_CLEAN}|g; s|http://deb.debian.org/debian-security|${APT_SECURITY_MIRROR_CLEAN}|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
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
        libatspi2.0-0 \
        libcairo2 \
        libcairo-gobject2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libfontconfig1 \
        libgdk-pixbuf-2.0-0 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
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
        libxshmfence1 \
        libxss1 \
        libxtst6 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --home-dir /data --shell /usr/sbin/nologin gateway \
    && mkdir -p /app /data/.cloakbrowser \
    && chown -R gateway:gateway /app /data

USER gateway

# Pre-download with the library API used by CloakBrowser's official Dockerfile.
# The verified archive/extracted binary is also retained in a BuildKit cache, so
# dependency-layer changes do not force another 200+ MB network download.
RUN --mount=type=cache,id=elysium-cloakbrowser-0.5.1,target=/data/.cloakbrowser-download,uid=1000,gid=1000,sharing=locked \
    CLOAKBROWSER_CACHE_DIR=/data/.cloakbrowser-download \
    python -c "from cloakbrowser import ensure_binary; ensure_binary()" \
    && cp -a /data/.cloakbrowser-download/. /data/.cloakbrowser/

# Application-only changes now invalidate this small final source layer, without
# forcing Python packages or the 200+ MB CloakBrowser binary to download again.
COPY --chown=gateway:gateway app ./app

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3)"]

# CloakBrowser documents asyncio as the safe loop when hosted by uvicorn.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--loop", "asyncio"]
