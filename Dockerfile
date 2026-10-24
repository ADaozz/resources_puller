FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json ./
RUN npm install
COPY frontend ./
RUN npm run build

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    SELF_PORTS=8080,8000 \
    DEMO_MEDIA_DIR=/app/demo-media \
    DATA_DIR=/data \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        ca-certificates \
        curl \
        xvfb \
        x11vnc \
        novnc \
        websockify \
        matchbox-window-manager \
        openbox \
        xdotool \
        x11-utils \
        ffmpeg \
        nginx \
        fonts-noto-cjk \
        procps \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

COPY scripts/gen_media.py /app/scripts/gen_media.py
RUN python3 /app/scripts/gen_media.py /app/demo-media

COPY backend/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && /opt/venv/bin/playwright install --with-deps chromium

COPY backend /app/backend
COPY scripts/verify.py /app/scripts/verify.py
COPY scripts/entrypoint.sh /app/scripts/entrypoint.sh
COPY deploy/nginx.conf /etc/nginx/conf.d/puller.conf
COPY --from=web /web/dist /app/frontend/dist
RUN chmod +x /app/scripts/entrypoint.sh /app/scripts/verify.py

EXPOSE 8080
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
