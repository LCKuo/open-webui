@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Install Interact Private Search

set "ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\install_searxng_ec2.ps1"
if errorlevel 1 (
    echo.
    echo [ERROR] Private search installation failed. Review the message above.
    pause
    exit /b 1
)

echo.
echo Private search is installed and ready.
pause
endlocal
