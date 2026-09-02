@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "ROOT=%~dp0"
if defined WEBUI_DATA_DIR (
    set "DATA_DIR=%WEBUI_DATA_DIR%"
) else (
    set "DATA_DIR=%ROOT%backend\open_webui\recovery-test-data"
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\manage_searxng_tunnel.ps1" -Action Start -DataDir "%DATA_DIR%" -ConfigureWebUI
if errorlevel 1 exit /b 1
endlocal
