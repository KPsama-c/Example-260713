@echo off
chcp 65001 >nul
cd /d "%~dp0"
title yuketang-auto Web
echo ========================================
echo  雨课堂 Web 控制台 · 仅本机 127.0.0.1
echo  见 DISCLAIMER.md · 风险自负
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.10+ 并勾选 PATH
  pause
  exit /b 1
)

echo [1/2] 检查依赖...
python -c "import playwright,yaml,flask" 2>nul
if errorlevel 1 (
  echo 安装 requirements...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [错误] pip 安装失败
    pause
    exit /b 1
  )
  echo 安装 Playwright Chromium...
  python -m playwright install chromium
)

echo [2/2] 启动 http://127.0.0.1:8765
echo 关闭本窗口即停止服务。请勿改绑定到 0.0.0.0
echo.
python webapp.py
echo.
pause
