@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"
title 杭州特耐时 - 一键更新程序

echo.
echo ========================================
echo  杭州特耐时库存系统 - 一键更新
echo ========================================
echo.
echo 请先关闭正在运行的后台服务窗口。
echo 本脚本会先备份 data\app.db 和 data\uploads，再拉取最新代码。
echo.
pause

if not exist ".git" (
    echo.
    echo 错误：当前文件夹不是通过 git clone 下载的项目，不能自动更新。
    echo 请确认你双击的是项目根目录里的 一键更新程序.bat。
    echo.
    pause
    exit /b 1
)

git --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo 错误：未检测到 Git。请先安装 Git 后再更新。
    echo.
    pause
    exit /b 1
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo 错误：未检测到 Python。请先安装 Python 3 后再更新。
        echo.
        pause
        exit /b 1
    )
    set "PY_CMD=python"
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "BACKUP_TIME=%%i"
set "BACKUP_DIR=backups\%BACKUP_TIME%"
mkdir "%BACKUP_DIR%" >nul 2>nul

if exist "data\app.db" copy /Y "data\app.db" "%BACKUP_DIR%\app.db" >nul
if exist "data\uploads" xcopy "data\uploads" "%BACKUP_DIR%\uploads\" /E /I /Y >nul

echo.
echo 备份完成：%BACKUP_DIR%
echo.

for /f %%i in ('git status --porcelain') do (
    echo 检测到程序文件有本地改动，为避免覆盖，已停止更新。
    echo 业务数据 data\ 不会被覆盖；请联系管理员处理这些程序文件改动。
    echo.
    git status --short
    echo.
    pause
    exit /b 1
)

echo 正在拉取最新代码...
git pull --ff-only
if errorlevel 1 (
    echo.
    echo 更新失败：拉取代码失败。请检查网络，或联系管理员处理。
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo 正在创建 Python 虚拟环境...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo.
        echo 创建 Python 虚拟环境失败。
        echo.
        pause
        exit /b 1
    )
)

if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
    )
)

echo.
echo 正在安装/更新依赖...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo 依赖安装失败。请检查网络，或联系管理员处理。
    echo.
    pause
    exit /b 1
)

echo.
echo 更新完成。
echo.
choice /C YN /M "是否现在启动后台服务"
if errorlevel 2 goto end

echo.
echo 正在启动后台服务...
start "杭州特耐时后台服务" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
echo 后台服务已在新窗口启动。
echo 浏览器地址：http://127.0.0.1:8000/admin

:end
echo.
pause
