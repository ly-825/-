#!/usr/bin/env bash
set -euo pipefail

TARGET_FILE="${TENAISHI_TARGET_FILE:-$HOME/.config/tenaishi/deploy-target.env}"
# shellcheck disable=SC1090
source "$TARGET_FILE"

: "${TENAISHI_PUBLIC_IP:?个人 ECS 公网 IP 未配置}"
: "${TENAISHI_API_DOMAIN:?个人 API 域名未配置}"

curl -fsS http://127.0.0.1:8000/health
curl -fsS "https://${TENAISHI_API_DOMAIN}/health"
login_page=$(curl -fsS "https://${TENAISHI_API_DOMAIN}/auth/login")
if ! grep -q '使用小程序扫码登录' <<<"$login_page"; then
  echo "登录页检查失败：没有发现小程序扫码登录入口" >&2
  exit 1
fi

if curl -fsS --connect-timeout 3 "http://${TENAISHI_PUBLIC_IP}:8000/health"; then
  echo "安全检查失败：公网 8000 端口可以访问" >&2
  exit 1
fi

echo "本地健康、HTTPS 健康、扫码登录页和公网 8000 关闭检查通过"
