@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [yuketang-auto] 检查依赖...
python -c "import playwright,yaml,flask" 2>nul
if errorlevel 1 (
  echo 安装 requirements...
  python -m pip install -r requirements.txt
  python -m playwright install chromium
)
echo 启动 Web 控制台 http://127.0.0.1:8765
python webapp.py
pause
