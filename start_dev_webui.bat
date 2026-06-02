@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Interact Ai Dev Launcher

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "BACKEND_DIR=%ROOT%\backend"
set "DATA_DIR=%BACKEND_DIR%\data"
set "PYTHON_EXE=%ROOT%\.venv\Scripts\python.exe"
set "WEBUI_SECRET_KEY_FILE=%ROOT%\.webui_secret_key"

set "HOST=0.0.0.0"
set "PORT=8080"
set "BACKEND_URL=http://localhost:8080"
set "FRONTEND_URL=http://localhost:5173"
set "CORS_ALLOW_ORIGIN=http://localhost:5173;http://localhost:8080"

echo.
echo ==========================================
echo   Interact Ai Dev Launcher
echo ==========================================
echo Project : %ROOT%
echo Backend : %BACKEND_URL%
echo Frontend: %FRONTEND_URL%
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

echo Starting backend window ...
start "Interact Ai Backend :8080" /D "%BACKEND_DIR%" cmd /k ""%PYTHON_EXE%" -m uvicorn open_webui.main:app --port %PORT% --host %HOST% --forwarded-allow-ips=127.0.0.1 --reload"

timeout /t 2 /nobreak >nul

echo Starting frontend window ...
start "Interact Ai Frontend :5173" /D "%ROOT%" cmd /k "npm run dev"

timeout /t 4 /nobreak >nul

echo Opening browser: %FRONTEND_URL%
start "" "%FRONTEND_URL%"

echo.
echo Done. Keep both Backend and Frontend windows open while developing.
echo Close those two windows to stop the dev servers.
echo.
endlocal
exit /b 0
