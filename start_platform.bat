@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo  AIC Factory Studio - OIP 场景编辑 + Python 仿真
echo ============================================================
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 未找到 Python，请先安装 Python 3.12 或使用安装包。
  pause
  exit /b 1
)
python run_platform.py
pause
