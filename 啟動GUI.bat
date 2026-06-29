@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" ".venv312\Scripts\pythonw.exe" gui.py
