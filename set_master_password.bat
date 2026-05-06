@echo off
REM === HIV 取號工具 — 主密碼設定 ===
chcp 65001 >NUL
cd /d %~dp0

set "PY=C:\Users\MIHC\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" set_master_password.py
echo.
pause
