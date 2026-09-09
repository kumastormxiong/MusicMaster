@echo off
chcp 65001 >nul
title Matchering 2.0 GUI
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" gui_app.py
) else (
    python gui_app.py
)
if %ERRORLEVEL% NEQ 0 (
    pause
)
