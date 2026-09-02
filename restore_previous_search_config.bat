@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "ROOT=%~dp0"
if defined WEBUI_DATA_DIR (
    set "DATA_DIR=%WEBUI_DATA_DIR%"
) else (
    set "DATA_DIR=%ROOT%backend\open_webui\recovery-test-data"
)

"%ROOT%.venv\Scripts\python.exe" "%ROOT%scripts\configure_web_search.py" "%DATA_DIR%" --backup "%ROOT%.runtime\web-search-config-backup.json" --restore
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\manage_searxng_tunnel.ps1" -Action Stop
endlocal
