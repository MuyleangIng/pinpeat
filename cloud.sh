#!/bin/zsh
# Manage Pinpeat on a Linux server from your computer: ./cloud.sh install | deploy | status | logs | restart | update | stop
# Settings in deploy.env (not in git), e.g.:
#   SSH="gcloud compute ssh my-vm --zone asia-southeast1-b --quiet --command"   SCP="gcloud compute scp --zone asia-southeast1-b --quiet"   HOST=my-vm
#   or plain SSH:  SSH="ssh me@1.2.3.4"   SCP="scp"   HOST=me@1.2.3.4
cd "$(dirname "$0")"
[ -f deploy.env ] && source ./deploy.env
[ -z "$SSH" ] && { echo "Set SSH, SCP and HOST in deploy.env (see the top of cloud.sh)"; exit 1; }
DIR=${REMOTE_DIR:-pinpeat}
run() { eval "$SSH \"\$1\"" 2>&1 | grep -v -E "^(Warning|Updating|External IP|Existing host|Writing|Did you)"; }
copy() { eval "$SCP $* $HOST:$DIR/"; }
case "$1" in
  install) run "mkdir -p $DIR/deploy" && copy bot.py .env $( [ -f cookies.txt ] && echo cookies.txt ) && eval "$SCP deploy/pinpeat.service deploy/install_linux.sh $HOST:$DIR/deploy/" && run "bash $DIR/deploy/install_linux.sh" ;;
  deploy)  copy bot.py .env $( [ -f cookies.txt ] && echo cookies.txt ) && run "chmod 600 $DIR/.env $DIR/cookies.txt 2>/dev/null; sudo systemctl restart pinpeat; sleep 5; systemctl is-active pinpeat" ;;
  status)  run "systemctl is-active pinpeat; journalctl -u pinpeat -n 5 --no-pager -o cat" ;;
  logs)    run "journalctl -u pinpeat -f -o cat" ;;
  restart) run "sudo systemctl restart pinpeat; sleep 5; systemctl is-active pinpeat" ;;
  update)  run "~/.local/bin/uv pip install -q --python $DIR/.venv/bin/python -U 'yt-dlp[default]' && sudo systemctl restart pinpeat && echo updated" ;;
  stop)    run "sudo systemctl disable --now pinpeat; echo stopped" ;;
  *) echo "usage: ./cloud.sh install|deploy|status|logs|restart|update|stop" ;;
esac
