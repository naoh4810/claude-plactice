FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=ja_JP.UTF-8 \
    LC_ALL=ja_JP.UTF-8 \
    DISPLAY=:1

RUN apt-get update && apt-get install -y --no-install-recommends \
        locales ca-certificates curl \
        xvfb x11vnc novnc hicolor-icon-theme adwaita-icon-theme python3-websockify openbox tint2 xdotool xclip imagemagick ffmpeg x11-utils dbus-x11 \
        firefox-esr firefox-esr-l10n-ja \
        libreoffice-calc libreoffice-l10n-ja libreoffice-gtk3 \
        fonts-noto-cjk \
        python3 python3-venv python3-yaml python3-reportlab \
    && sed -i 's/# ja_JP.UTF-8/ja_JP.UTF-8/' /etc/locale.gen && locale-gen \
    && rm -rf /var/lib/apt/lists/*

# Firefox の初回起動画面などを出さない
COPY config/firefox-policies.json /usr/lib/firefox-esr/distribution/policies.json

RUN useradd -m -s /bin/bash demo
WORKDIR /app

RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir "anthropic>=1.11"
ENV PATH=/opt/venv/bin:$PATH

COPY --chown=demo:demo . /app
RUN mkdir -p /app/recordings && chown demo:demo /app/recordings
RUN python3 site/invoices/make_invoices.py && chown -R demo:demo /app/site

# デスクトップ（openbox + 下部パネル）と LibreOffice の初期設定
COPY --chown=demo:demo config/tint2rc /home/demo/.config/tint2/tint2rc
COPY --chown=demo:demo config/registrymodifications.xcu /home/demo/.config/libreoffice/4/user/registrymodifications.xcu

USER demo
EXPOSE 8080 6080
CMD ["/app/entrypoint.sh"]
