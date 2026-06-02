@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Interact Ai WebUI Tunnel Launcher

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "BACKEND_DIR=%ROOT%\backend"
set "DATA_DIR=%BACKEND_DIR%\data"
set "PYTHON_EXE=%ROOT%\.venv\Scripts\python.exe"
set "WEBUI_SECRET_KEY_FILE=%ROOT%\.webui_secret_key"

set "HOST=0.0.0.0"
set "PORT=8080"
set "WEBUI_URL=http://localhost:8080"

echo.
echo ==========================================
echo   Interact Ai WebUI Tunnel Launcher
echo ==========================================
echo Project : %ROOT%
echo WebUI   : %WEBUI_URL%
echo Tunnel  : point Cloudflare to http://localhost:8080
echo ==========================================
echo.

if not exist "%ROOT%\package.json" (
    echo This script must be placed in the open-webui project root.
    echo Missing: %ROOT%\package.json
    echo.
    pause
    exit /b 1
)

if not exist "%ROOT%\.env" (
    if exist "%ROOT%\.env.example" (
        echo Creating .env from .env.example ...
        copy "%ROOT%\.env.example" "%ROOT%\.env" >nul
    )
)

if "%WEBUI_SECRET_KEY%"=="" (
    if not exist "%WEBUI_SECRET_KEY_FILE%" (
        echo Generating WEBUI_SECRET_KEY ...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$b=New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)" > "%WEBUI_SECRET_KEY_FILE%"
        if errorlevel 1 (
            echo.
            echo Failed to generate WEBUI_SECRET_KEY.
            pause
            exit /b 1
        )
    )
    set /p WEBUI_SECRET_KEY=<"%WEBUI_SECRET_KEY_FILE%"
)

if not exist "%ROOT%\build\index.html" (
    echo Frontend build not found. Building frontend for backend serving ...

    if not exist "%ROOT%\node_modules" (
        echo node_modules not found. Running npm install ...
        pushd "%ROOT%" >nul
        call npm install
        if errorlevel 1 (
            popd >nul
            echo.
            echo npm install failed.
            pause
            exit /b 1
        )
        popd >nul
    )

    pushd "%ROOT%" >nul
    call npm run build
    if errorlevel 1 (
        popd >nul
        echo.
        echo npm run build failed.
        pause
        exit /b 1
    )
    popd >nul
)

if not exist "%PYTHON_EXE%" (
    echo Python virtual environment not found. Running uv sync --group dev ...
    where uv >nul 2>nul
    if errorlevel 1 (
        echo.
        echo uv was not found. Please install uv or create .venv first.
        pause
        exit /b 1
    )

    pushd "%ROOT%" >nul
    call uv sync --group dev
    if errorlevel 1 (
        popd >nul
        echo.
        echo uv sync failed.
        pause
        exit /b 1
    )
    popd >nul
)

if not exist "%BACKEND_DIR%" (
    echo Missing backend directory: %BACKEND_DIR%
    pause
    exit /b 1
)

if not exist "%DATA_DIR%" (
    echo Creating backend data directory: %DATA_DIR%
    mkdir "%DATA_DIR%"
    if errorlevel 1 (
        echo.
        echo Failed to create backend data directory.
        pause
        exit /b 1
    )
)

echo Starting backend-only WebUI on %WEBUI_URL% ...
echo Do not start the frontend dev server for tunnel/public access.
echo.

pushd "%BACKEND_DIR%" >nul
"%PYTHON_EXE%" -m uvicorn open_webui.main:app --port %PORT% --host %HOST% --forwarded-allow-ips=127.0.0.1
popd >nul

echo.
echo WebUI stopped.
pause
endlocal
exit /b 0
