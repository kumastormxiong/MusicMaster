@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MusicMaster - 启动音质提升工作台
echo ============================================================
echo   MusicMaster - 音频参考母带与 Ozone 12 音质提升批量工作台
echo ============================================================
echo.
echo 正在启动图形界面，请稍候...
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" gui_app.py
) else (
    python gui_app.py
)

if errorlevel 1 (
    echo.
    echo 程序异常退出，按任意键关闭...
    pause
)

