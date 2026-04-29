@echo off
REM === HIV 取號 DEBUG EXE 編譯流程 ===
REM 自動：搬舊 EXE → PyInstaller build → 直接輸出到 D:\Backup\Desktop\CODE\number\
chcp 65001 >NUL
cd /d %~dp0

set "OUT_DIR=D:\Backup\Desktop\CODE\number"
set "OLD_DIR=%OUT_DIR%\old"

echo [1/4] 確保 %OUT_DIR%\old 存在
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
if not exist "%OLD_DIR%" mkdir "%OLD_DIR%"

echo [2/4] 將 %OUT_DIR% 內舊 EXE 移到 old\（保留歷史）
for %%F in ("%OUT_DIR%\*.exe") do (
  echo    搬移 %%~nxF
  move /Y "%%F" "%OLD_DIR%\" >NUL
)

echo [3/4] PyInstaller 編譯（DEBUG 版） → 輸出到 %OUT_DIR%
"C:\Users\MIHC\AppData\Local\Programs\Python\Python311\python.exe" -m PyInstaller hiv_code_DEBUG.spec --noconfirm --distpath "%OUT_DIR%" --workpath "build"
if errorlevel 1 (
  echo.
  echo X 編譯失敗，請看上方錯誤訊息
  pause
  exit /b 1
)

echo.
echo [4/4] 完成！產物：
dir /B "%OUT_DIR%\*.exe"
echo.
echo 舊版歷史：%OLD_DIR%
echo Excel 輸出也在：%OUT_DIR%
echo.
pause
