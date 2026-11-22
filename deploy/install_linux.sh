#!/bin/bash
# Run ON the Linux server (Debian/Ubuntu), inside the folder that holds bot.py, .env and (optional) cookies.txt.
# Installs FFmpeg (apt), uv + deno (in your home), a Python venv, and the pinpeat systemd service.
set -e
cd "$(dirname "$0")/.." 2>/dev/null || true
[ -f bot.py ] || cd ~/pinpeat
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg libopus0 >/dev/null
command -v uv >/dev/null || [ -x ~/.local/bin/uv ] || curl -LsSf https://astral.sh/uv/install.sh | sh
[ -x ~/.deno/bin/deno ] || curl -fsSL https://deno.land/install.sh | sh -s -- -y --no-modify-path
UV=$(command -v uv || echo ~/.local/bin/uv)
$UV venv -q --python 3.12 .venv
$UV pip install -q --python .venv/bin/python "discord.py[voice]>=2.7" "davey>=0.1,<0.2" "yt-dlp[default]>=2026.10" python-dotenv
chmod 600 .env cookies.txt 2>/dev/null || true
sed -e "s|@USER@|$USER|g" -e "s|@DIR@|$PWD|g" -e "s|@HOME@|$HOME|g" deploy/pinpeat.service | sudo tee /etc/systemd/system/pinpeat.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now pinpeat
sleep 5; systemctl is-active pinpeat && journalctl -u pinpeat -n 3 --no-pager -o cat
