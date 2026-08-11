#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/srv/tenaishi/app"
ENV_FILE="/etc/tenaishi/tenaishi.env"

cd "$APP_DIR"

if [ -n "$(runuser -u tenaishi -- git status --porcelain)" ]; then
  echo "应用目录存在未提交修改，拒绝自动更新" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

runuser -u tenaishi --preserve-environment -- scripts/backup.sh
runuser -u tenaishi -- git pull --ff-only
runuser -u tenaishi -- .venv/bin/python -m pip install -r requirements.txt

systemctl restart tenaishi.service
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health; then
    echo
    echo "Updated commit: $(runuser -u tenaishi -- git rev-parse HEAD)"
    exit 0
  fi
  sleep 1
done

echo "服务重启后未通过本地健康检查" >&2
exit 1
