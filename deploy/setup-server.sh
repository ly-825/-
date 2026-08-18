#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/srv/tenaishi/app"
ENV_DIR="/etc/tenaishi"
ENV_FILE="$ENV_DIR/tenaishi.env"

export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y git python3 python3-venv python3-pip nginx rsync util-linux curl gettext-base sqlite3

id -u tenaishi >/dev/null 2>&1 || useradd --system --create-home --home-dir /srv/tenaishi --shell /usr/sbin/nologin tenaishi
install -d -o tenaishi -g tenaishi -m 0750 /srv/tenaishi/app /srv/tenaishi/data /srv/tenaishi/data/uploads /srv/tenaishi/data/previews /srv/tenaishi/data/qrcodes /srv/tenaishi/backups
install -d -o root -g tenaishi -m 0750 "$ENV_DIR"

if [ ! -d "$APP_DIR/.git" ]; then
  : "${TENAISHI_REPO_URL:?首次安装必须设置 TENAISHI_REPO_URL}"
  git clone "$TENAISHI_REPO_URL" "$APP_DIR"
  chown -R tenaishi:tenaishi "$APP_DIR"
fi

cd "$APP_DIR"
runuser -u tenaishi -- python3 -m venv .venv
runuser -u tenaishi -- .venv/bin/python -m pip install -r requirements.txt

if [ ! -f "$ENV_FILE" ]; then
  install -o root -g tenaishi -m 0640 /dev/null "$ENV_FILE"
  cat >"$ENV_FILE" <<'EOF'
PRODUCTION=true
DATABASE_URL=sqlite:////srv/tenaishi/data/app.db
UPLOAD_DIR=/srv/tenaishi/data/uploads
DRAWING_PREVIEW_DIR=/srv/tenaishi/data/previews
QRCODE_DIR=/srv/tenaishi/data/qrcodes
BACKUP_ROOT=/srv/tenaishi/backups
DATABASE_PATH=/srv/tenaishi/data/app.db
BACKUP_RETENTION_DAYS=7
PYTHON_BIN=/srv/tenaishi/app/.venv/bin/python
AUTH_PEPPER=
OWNER_TOTP_SECRET=
WECHAT_APP_ID=
WECHAT_APP_SECRET=
EOF
fi

install -o root -g root -m 0644 deploy/tenaishi.service /etc/systemd/system/tenaishi.service
install -o root -g root -m 0644 deploy/tenaishi-backup.service /etc/systemd/system/tenaishi-backup.service
install -o root -g root -m 0644 deploy/tenaishi-backup.timer /etc/systemd/system/tenaishi-backup.timer
systemctl daemon-reload
systemctl enable tenaishi.service
systemctl enable --now tenaishi-backup.timer

echo "基础目录、Python 环境和备份定时器已准备完成。"
echo "填写 $ENV_FILE 中的服务器密钥后，再启动 tenaishi.service。"
