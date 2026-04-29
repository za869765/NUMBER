@echo off
REM === HIV 取號 正式版 EXE 編譯流程 ===
REM 與 build_debug.bat 差別：用 RELEASE.spec（console 隱藏 + 檔名無 _DEBUG）
chcp 65001 >NUL
cd /d %~dp0

set "OUT_DIR=D:\Backup\Desktop\CODE\number"
set "OLD_DIR=%OUT_DIR%\old"

echo [1/4] 確保 %OUT_DIR%\old 存在
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
if not exist "%OLD_DIR%" mkdir "%OLD_DIR%"

echo [2/4] 將舊正式版 EXE 移到 old\（保留歷史）
for %%F in ("%OUT_DIR%\HIV*.exe") do (
  echo    搬移 %%~nxF
  move /Y "%%F" "%OLD_DIR%\" >NUL
)

echo [3/4] PyInstaller 編譯（RELEASE 版） → 輸出到 %OUT_DIR%
"C:\Users\MIHC\AppData\Local\Programs\Python\Python311\python.exe" -m PyInstaller hiv_code_RELEASE.spec --noconfirm --distpath "%OUT_DIR%" --workpath "build"
if errorlevel 1 (
  echo.
  echo X 編譯失敗，請看上方錯誤訊息
  pause
  exit /b 1
)

echo.
echo [4/4] 完成！產物：
dir /B "%OUT_DIR%\HIV*.exe"
echo.
echo 正式版（無 console 黑窗）/ 舊版歷史在 %OLD_DIR%
echo.
pause
