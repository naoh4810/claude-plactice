#!/bin/bash
# 仮想デスクトップを起動し、noVNC（画面中継）とコントロールパネルを立ち上げる
set -e

Xvfb :1 -screen 0 1280x800x24 -nolisten tcp &
for _ in $(seq 1 50); do xdpyinfo -display :1 >/dev/null 2>&1 && break; sleep 0.1; done

xsetroot -solid "#2b3a4a" 2>/dev/null || true
openbox &
tint2 &
x11vnc -display :1 -forever -shared -nopw -rfbport 5900 -quiet &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &

if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "⚠️  ANTHROPIC_API_KEY が未設定です。.env に記入してから起動し直してください。"
fi

exec python3 /app/panel/server.py
