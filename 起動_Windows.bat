@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    set PY_CMD=py -3
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set PY_CMD=python
    ) else (
        echo.
        echo ========================================
        echo [エラー] Python が見つかりません
        echo ========================================
        echo.
        echo Python をインストールしてください:
        echo   https://www.python.org/downloads/
        echo   インストール時に "Add python.exe to PATH" にチェック
        echo.
        pause
        exit /b 1
    )
)
echo obc_to_freee GUI を起動します...
%PY_CMD% obc_to_freee_gui.py
echo.
echo サーバーが停止しました。このウィンドウを閉じてください。
pause
