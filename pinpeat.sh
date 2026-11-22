#!/bin/zsh
# Run Pinpeat on a Mac as a LaunchAgent (starts at login, restarts on crash): ./pinpeat.sh start | stop | restart | status | logs | update
cd "$(dirname "$0")"
L=${PINPEAT_LABEL:-local.pinpeat}; P=~/Library/LaunchAgents/$L.plist; D=gui/$(id -u); LOG=~/Library/Logs/pinpeat/bot.log
mkdir -p ~/Library/Logs/pinpeat
plist() { cat <<X
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$L</string>
  <key>ProgramArguments</key><array><string>$PWD/.venv/bin/python</string><string>-u</string><string>bot.py</string></array>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG</string><key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
X
}
case "$1" in
  start)
    grep -q "paste_your_bot_token_here" .env 2>/dev/null && { echo "Put the bot token in .env first (DISCORD_TOKEN=...)"; exit 1; }
    [ -x .venv/bin/python ] || { uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python "discord.py[voice]>=2.7" "davey>=0.1,<0.2" "yt-dlp[default]>=2026.10" python-dotenv; }
    plist > $P; launchctl bootstrap $D $P 2>/dev/null || launchctl kickstart -k $D/$L
    sleep 8; tail -3 $LOG ;;
  stop)    launchctl bootout $D/$L 2>/dev/null; rm -f $P; echo "stopped (won't start at login)" ;;
  restart) launchctl kickstart -k $D/$L; sleep 8; tail -3 $LOG ;;
  status)  launchctl print $D/$L 2>/dev/null | grep -E "state|pid =" || echo "not running" ;;
  logs)    tail -f $LOG ;;
  update)  uv pip install -q --python .venv/bin/python -U yt-dlp && echo "yt-dlp $(.venv/bin/yt-dlp --version)" && launchctl kickstart -k $D/$L ;;
  *) echo "usage: ./pinpeat.sh start|stop|restart|status|logs|update" ;;
esac
