@echo off
REM === HIV 取號 正式版 EXE 編譯流程 ===
chcp 65001 >NUL
cd /d %~dp0

set "OUT_DIR=D:\Backup\Desktop\CODE\number"
set "OLD_DIR=%OUT_DIR%\old"

REM v1.0.39：主密碼改為雲端授權，不再需要 _secret.py
REM 如果還在編 EXE 前，請先：
REM   1. cd cloud_auth ^&^& npm install ^&^& npx wrangler login
REM   2. 部署 worker 並設管理員密碼（見 cloud_auth\README.md）
REM   3. 確認 hiv_code.py 內 CLOUD_AUTH_URL 是你的 worker 網址

echo [1/4] 確保 %OUT_DIR%\old 存在
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
if not exist "%OLD_DIR%" mkdir "%OLD_DIR%"

echo [2/4] 將舊正式版 EXE 移到 old\（保留歷史）
for %%F in ("%OUT_DIR%\HIV*.exe") do (
  echo    搬移 %%~nxF
  move /Y "%%F" "%OLD_DIR%\" >NUL
)

echo [3/4] PyInstaller 編譯 → 輸出到 %OUT_DIR%
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
