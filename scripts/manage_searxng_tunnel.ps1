param(
    [ValidateSet("Start", "Stop", "Status")]
    [string]$Action = "Start",
    [string]$DataDir = "",
    [string]$SshTarget = "",
    [string]$KeyPath = "",
    [int]$LocalPort = 8082,
    [int]$RemotePort = 8888,
    [switch]$ConfigureWebUI
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $root ".runtime"
$pidFile = Join-Path $runtimeDir "searxng-tunnel.pid"
$backupFile = Join-Path $runtimeDir "web-search-config-backup.json"
$healthUrl = "http://127.0.0.1:$LocalPort/search"

function Test-SearxngHealth {
    try {
        $response = Invoke-RestMethod -Method Get -Uri "$healthUrl`?q=Taiwan%20industrial%20equipment&format=json" -TimeoutSec 10
        return $null -ne $response.results
    }
    catch {
        return $false
    }
}

function Resolve-ConnectionSettings {
    if (-not $script:SshTarget) {
        $script:SshTarget = $env:SEARXNG_SSH_TARGET
    }
    if (-not $script:KeyPath) {
        $script:KeyPath = $env:SEARXNG_SSH_KEY
    }

    $websiteRoot = "D:\AntigravityProj\website"
    $targetFiles = @(
        @{ Path = (Join-Path $runtimeDir "searxng-target.env"); BaseDir = $root },
        @{ Path = (Join-Path $websiteRoot ".deploy\ec2-target.env"); BaseDir = $websiteRoot }
    )

    foreach ($target in $targetFiles) {
        if (($script:SshTarget -and $script:KeyPath) -or -not (Test-Path -LiteralPath $target.Path)) {
            continue
        }

        $config = @{}
        foreach ($line in Get-Content -LiteralPath $target.Path -Encoding UTF8) {
            if ($line -match '^\s*([^#][^=]*)=(.*)$') {
                $config[$matches[1].Trim()] = $matches[2].Trim().Trim('"').Trim("'")
            }
        }
        if (-not $script:SshTarget) {
            $script:SshTarget = $config["EC2_USER_URL"]
        }
        if (-not $script:KeyPath -and $config["PEM_KEY"]) {
            $configuredKey = $config["PEM_KEY"]
            $script:KeyPath = if ([System.IO.Path]::IsPathRooted($configuredKey)) {
                $configuredKey
            }
            else {
                Join-Path $target.BaseDir $configuredKey
            }
        }
    }

    if (-not $script:SshTarget) {
        throw "SearXNG SSH target is missing. Set SEARXNG_SSH_TARGET or configure the website EC2 target."
    }
    if (-not $script:KeyPath -or -not (Test-Path -LiteralPath $script:KeyPath)) {
        throw "SearXNG SSH key was not found. Set SEARXNG_SSH_KEY to the PEM file path."
    }
}

function Stop-Tunnel {
    if (-not (Test-Path -LiteralPath $pidFile)) {
        Write-Host "SearXNG tunnel is not recorded as running."
        return
    }

    $savedPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($savedPid -match '^\d+$') {
        $process = Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $process.Id -Force
            $process.WaitForExit()
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "SearXNG SSH tunnel stopped."
}

if ($Action -eq "Stop") {
    Stop-Tunnel
    exit 0
}

if ($Action -eq "Status") {
    if (Test-SearxngHealth) {
        Write-Host "SearXNG tunnel is healthy at $healthUrl"
        exit 0
    }
    Write-Error "SearXNG tunnel is not healthy at $healthUrl"
    exit 1
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
if (-not (Test-SearxngHealth)) {
    Resolve-ConnectionSettings

    if (Test-Path -LiteralPath $pidFile) {
        Stop-Tunnel
    }

    $ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    $arguments = @(
        "-N", "-T",
        "-L", "127.0.0.1:$LocalPort`:127.0.0.1:$RemotePort",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ConnectTimeout=15",
        "-i", "`"$KeyPath`"",
        $SshTarget
    ) -join " "

    $process = Start-Process -FilePath $ssh -ArgumentList $arguments -WindowStyle Hidden -PassThru
    Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ASCII

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) {
            break
        }
        if (Test-SearxngHealth) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
        }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        throw "Private search did not become healthy. Check EC2 service interact-searxng and SSH connectivity."
    }
}

if ($ConfigureWebUI) {
    if (-not $DataDir) {
        throw "DataDir is required when ConfigureWebUI is enabled."
    }
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "WebUI Python environment was not found: $python"
    }
    & $python (Join-Path $PSScriptRoot "configure_web_search.py") $DataDir --backup $backupFile
    if ($LASTEXITCODE -ne 0) {
        throw "WebUI search configuration failed."
    }
}

Write-Host "Private SearXNG is ready at $healthUrl"
