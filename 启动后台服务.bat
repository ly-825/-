@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"
title 杭州特耐时 - 后台服务

echo.
echo ========================================
echo  杭州特耐时库存系统 - 启动后台服务
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo 错误：未找到 .venv\Scripts\python.exe。
    echo 请先运行 一键更新程序.bat，或按部署说明创建 Python 虚拟环境。
    echo.
    pause
    exit /b 1
)

echo 后台地址：http://127.0.0.1:8000/admin
echo 健康检查：http://127.0.0.1:8000/health
echo.
echo 服务运行期间请不要关闭本窗口。
echo 按 Ctrl + C 可以停止服务。
echo.

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000

echo.
echo 后台服务已停止。
pause
