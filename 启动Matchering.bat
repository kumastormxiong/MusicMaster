@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Matchering 2.0 & Ozone 12
echo ============================================================
echo   Matchering 2.0 - 音频参考母带与 Ozone 12 (KS) 批量工作台
echo ============================================================
echo.
echo 正在启动图形界面，请稍候...
echo.
".venv\Scripts\python.exe" gui_app.py
if errorlevel 1 (
    echo.
    echo 程序异常退出，按任意键关闭...
    pause
)
