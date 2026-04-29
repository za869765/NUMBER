@echo off
chcp 65001 >NUL
cd /d %~dp0
echo === HIV 取號 DEBUG 啟動 ===
echo Python: C:\Users\MIHC\AppData\Local\Programs\Python\Python311\python.exe
echo.
"C:\Users\MIHC\AppData\Local\Programs\Python\Python311\python.exe" hiv_code.py
pause
