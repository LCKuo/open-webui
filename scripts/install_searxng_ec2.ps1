param(
    [string]$SshTarget = "",
    [string]$KeyPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$websiteRoot = "D:\AntigravityProj\website"
$targetFiles = @(
    @{ Path = (Join-Path $root ".runtime\searxng-target.env"); BaseDir = $root },
    @{ Path = (Join-Path $websiteRoot ".deploy\ec2-target.env"); BaseDir = $websiteRoot }
)

foreach ($target in $targetFiles) {
    if (($SshTarget -and $KeyPath) -or -not (Test-Path -LiteralPath $target.Path)) {
        continue
    }

    $config = @{}
    foreach ($line in Get-Content -LiteralPath $target.Path -Encoding UTF8) {
        if ($line -match '^\s*([^#][^=]*)=(.*)$') {
            $config[$matches[1].Trim()] = $matches[2].Trim().Trim('"').Trim("'")
        }
    }
    if (-not $SshTarget) {
        $SshTarget = $config["EC2_USER_URL"]
    }
    if (-not $KeyPath -and $config["PEM_KEY"]) {
        $configuredKey = $config["PEM_KEY"]
        $KeyPath = if ([System.IO.Path]::IsPathRooted($configuredKey)) {
            $configuredKey
        }
        else {
            Join-Path $target.BaseDir $configuredKey
        }
    }
}

if (-not $SshTarget) {
    throw "EC2 target is missing. Pass -SshTarget or configure the website EC2 target."
}
if (-not $KeyPath -or -not (Test-Path -LiteralPath $KeyPath)) {
    throw "EC2 PEM key was not found: $KeyPath"
}

$infra = Join-Path $root "infra\searxng"
$settings = Join-Path $infra "settings.yml"
$service = Join-Path $infra "interact-searxng.service"
$installer = Join-Path $infra "install-ec2.sh"
foreach ($file in @($settings, $service, $installer)) {
    if (-not (Test-Path -LiteralPath $file)) {
        throw "Missing installation file: $file"
    }
}

$sshOptions = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "-i", $KeyPath)
$scpOptions = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "-i", $KeyPath)

Write-Host "Uploading private search configuration to $SshTarget ..."
& scp.exe @scpOptions $settings "${SshTarget}:/tmp/interact-searxng-settings.yml"
if ($LASTEXITCODE -ne 0) { throw "Failed to upload SearXNG settings." }
& scp.exe @scpOptions $service "${SshTarget}:/tmp/interact-searxng.service"
if ($LASTEXITCODE -ne 0) { throw "Failed to upload SearXNG service." }
& scp.exe @scpOptions $installer "${SshTarget}:/tmp/interact-searxng-install.sh"
if ($LASTEXITCODE -ne 0) { throw "Failed to upload SearXNG installer." }

Write-Host "Installing SearXNG on EC2. The service remains bound to EC2 loopback only ..."
& ssh.exe @sshOptions $SshTarget "sudo bash /tmp/interact-searxng-install.sh"
if ($LASTEXITCODE -ne 0) { throw "EC2 SearXNG installation failed." }

$dataDir = Join-Path $root "backend\open_webui\recovery-test-data"
& (Join-Path $PSScriptRoot "manage_searxng_tunnel.ps1") -Action Start -DataDir $dataDir -SshTarget $SshTarget -KeyPath $KeyPath -ConfigureWebUI
if ($LASTEXITCODE -ne 0) { throw "SearXNG was installed, but the local tunnel setup failed." }

Write-Host "Private search installation and WebUI configuration completed."
