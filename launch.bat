@echo off
chcp 65001 >nul
rem video-editor 起動（Windows）: このファイルをダブルクリックするとサーバが立ち上がりブラウザが開く
cd /d "%~dp0"

set PY=
where py >nul 2>&1 && set PY=py -3
if "%PY%"=="" ( where python >nul 2>&1 && set PY=python )
if "%PY%"=="" (
  echo [ERROR] Python3 が見つかりません。
  echo   Microsoft Store で「Python 3」を入れてから、もう一度このファイルを開いてください。
  pause & exit /b 1
)

%PY% -c "import PIL" >nul 2>&1 || (
  echo [SETUP] Pillow を入れます（初回のみ）…
  %PY% -m pip install --quiet Pillow || ( echo [ERROR] Pillow の導入に失敗しました & pause & exit /b 1 )
)

rem 依存の状態を一覧で見せる（足りないものは入れ方つきで出る）
%PY% tools\_deps.py
where ffmpeg >nul 2>&1 || (
  echo.
  echo [WARN] ffmpeg が無いので、編集はできますが**書き出しができません**。
  echo   入れ方: winget install Gyan.FFmpeg
  echo   ^(入れたら、このウィンドウを閉じて開き直してください^)
  echo.
  pause
)

rem 既に起動していれば二重起動しない
curl -s -m 1 http://localhost:8765/api/version >nul 2>&1 && (
  echo [OK] すでに起動しています。
  start "" http://localhost:8765
  exit /b 0
)

echo [START] video-editor を起動します… ^(このウィンドウを閉じると終了します^)
rem サーバを別ウィンドウで起動 → /api/version が返るまでポーリング → ブラウザを開く
rem （順番を逆にすると、サーバ起動が遅い時にブラウザが接続エラーページのまま止まる。
rem  issue #4 で修正: 2026-07-23）
start "video-editor server" cmd /c "%PY% ui\server.py & pause"
set /a WAIT=0
:wait_server
rem 注意: 以下は必ずフラット構造（(...) ブロック外から goto :wait_server で戻る）を保つ。
rem  (...) ブロック内から外のラベルへ goto すると cmd.exe が事前解析した状態を壊し
rem  "20 was unexpected at this time." で即死する（issue #6 で修正: 2026-07-24）
if %WAIT% GEQ 20 goto wait_timeout
timeout /t 1 /nobreak >nul
set /a WAIT+=1
curl -s -m 1 http://localhost:8765/api/version >nul 2>&1
if errorlevel 1 goto wait_server

echo [OK] 起動しました。ブラウザを開きます。
start "" http://localhost:8765
exit /b 0

:wait_timeout
echo [ERROR] サーバが 20 秒以内に起動しませんでした。別ウィンドウのエラーを確認してください。
pause & exit /b 1
