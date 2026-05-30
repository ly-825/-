#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "==> 项目目录: $PROJECT_ROOT"

if ! command -v git >/dev/null 2>&1; then
  echo "错误：没有安装 git，请先安装 Git。"
  exit 1
fi

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "错误：没有安装 Python，请先安装 Python 3。"
  exit 1
fi

if [ ! -d ".git" ]; then
  echo "错误：当前目录不是 Git 项目。请先用 git clone 下载项目。"
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "错误：当前代码目录有未提交或未保存的改动，已停止更新。"
  echo "请先处理这些改动，再重新执行：bash scripts/update.sh"
  git status --short
  exit 1
fi

echo "==> 备份本机业务数据"
bash scripts/backup.sh

echo "==> 拉取最新代码"
git pull --ff-only

if [ ! -d ".venv" ]; then
  echo "==> 创建 Python 虚拟环境"
  "$PYTHON_BIN" -m venv .venv
fi

echo "==> 安装/更新依赖"
if [ -x ".venv/bin/python" ]; then
  .venv/bin/python -m pip install -r requirements.txt
elif [ -x ".venv/Scripts/python.exe" ]; then
  .venv/Scripts/python.exe -m pip install -r requirements.txt
else
  echo "错误：找不到虚拟环境里的 Python。"
  exit 1
fi

echo
echo "更新完成。"
echo "请重启后端服务："
echo "  macOS/Linux: .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
echo "  Windows:     .venv\\Scripts\\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
