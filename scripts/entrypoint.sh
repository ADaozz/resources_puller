#!/bin/bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export HOME=/tmp/rp-home
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/ms-playwright}"
mkdir -p "$HOME" /tmp/.X11-unix /data /var/log/nginx /var/lib/nginx
chmod 1777 /tmp/.X11-unix
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

Xvfb "$DISPLAY" -screen 0 1440x900x24 -ac +extension GLX +extension RANDR -noreset >/tmp/xvfb.log 2>&1 &
for _ in $(seq 1 80); do
  if xdpyinfo >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
xdpyinfo >/dev/null

if command -v matchbox-window-manager >/dev/null 2>&1; then
  matchbox-window-manager -use_titlebar no >/tmp/wm.log 2>&1 &
else
  openbox >/tmp/wm.log 2>&1 &
fi

x11vnc -display "$DISPLAY" -forever -shared -rfbport 5900 -localhost -nopw -noxdamage -repeat -xkb >/tmp/x11vnc.log 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 >/tmp/websockify.log 2>&1 &

for _ in $(seq 1 80); do
  if (echo >/dev/tcp/127.0.0.1/5900) >/dev/null 2>&1 && (echo >/dev/tcp/127.0.0.1/6080) >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

nginx -t
nginx
cd /app/backend
exec /opt/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info
