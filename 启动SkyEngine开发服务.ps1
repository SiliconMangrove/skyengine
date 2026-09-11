#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
$FrontendRoot = Join-Path $ProjectRoot 'application\frontend'
$ComposeFile = Join-Path $ProjectRoot 'docker-compose-online.yaml'
$BatchComposeFile = Join-Path $ProjectRoot 'docker-compose.yaml'
$EnvFile = Join-Path $ProjectRoot '.env'
$StateFile = Join-Path $ProjectRoot '.skyengine-windows-state.json'
$LogRoot = Join-Path $ProjectRoot 'logs'
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

function Get-EnvValue {
    param([Parameter(Mandatory)][string]$Key)

    $line = Get-Content -LiteralPath $EnvFile -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^$([regex]::Escape($Key))=" } |
        Select-Object -Last 1
    if ($null -eq $line) {
        return ''
    }
    return $line.Substring($Key.Length + 1)
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][string]$Value
    )

    $lines = @(Get-Content -LiteralPath $EnvFile -ErrorAction SilentlyContinue)
    $pattern = "^$([regex]::Escape($Key))="
    $found = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match $pattern) {
            $lines[$index] = "$Key=$Value"
            $found = $true
        }
    }
    if (-not $found) {
        $lines += "$Key=$Value"
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($EnvFile, [string[]]$lines, $encoding)
}

function Stop-ProcessTree {
    param([Parameter(Mandatory)][int]$ProcessId)

    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    }
}

function Stop-TrackedProcesses {
    if (-not (Test-Path -LiteralPath $StateFile)) {
        return
    }

    $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    foreach ($name in @('BackendPid', 'FrontendPid')) {
        $processId = [int]$state.$name
        if ($processId -gt 0) {
            Stop-ProcessTree -ProcessId $processId
        }
    }
    Remove-Item -LiteralPath $StateFile -Force
}

function Stop-ProjectDevelopmentProcesses {
    $projectPattern = [regex]::Escape($ProjectRoot)
    $frontendPattern = [regex]::Escape($FrontendRoot)
    $processes = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue
    foreach ($process in $processes) {
        $commandLine = [string]$process.CommandLine
        $isBackend = $commandLine -match 'application[\\/]backend\.server:app' -and $commandLine -match $projectPattern
        $isFrontend = $commandLine -match '\bvite\b' -and $commandLine -match $frontendPattern
        if (($isBackend -or $isFrontend) -and [int]$process.ProcessId -ne $PID) {
            Stop-ProcessTree -ProcessId ([int]$process.ProcessId)
        }
    }
}

function Test-DockerReady {
    & docker.exe info --format '{{.ServerVersion}}' 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Wait-DockerReady {
    if (Test-DockerReady) {
        return
    }

    $dockerDesktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path -LiteralPath $dockerDesktop)) {
        throw "Docker Desktop was not found: $dockerDesktop"
    }

    Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(90)
    do {
        Start-Sleep -Seconds 2
        if (Test-DockerReady) {
            return
        }
    } while ((Get-Date) -lt $deadline)

    throw 'Docker daemon did not become ready within 90 seconds'
}

$script:UseComposePlugin = $false
function Initialize-Compose {
    if (-not (Get-Command docker.exe -ErrorAction SilentlyContinue)) {
        throw 'docker.exe was not found. Install Docker Desktop first.'
    }

    & docker.exe compose version 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $script:UseComposePlugin = $true
        return
    }
    if (-not (Get-Command docker-compose.exe -ErrorAction SilentlyContinue)) {
        throw 'Docker Compose was not found.'
    }
}

function Invoke-Compose {
    param([Parameter(Mandatory)][string[]]$Arguments)

    if ($script:UseComposePlugin) {
        & docker.exe compose @Arguments
    }
    else {
        & docker-compose.exe @Arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed: $($Arguments -join ' ')"
    }
}

function Test-PortInUse {
    param([Parameter(Mandatory)][int]$Port)

    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Get-FreePort {
    param([Parameter(Mandatory)][int]$PreferredPort)

    $port = $PreferredPort
    while ((Test-PortInUse -Port $port) -or ($script:ReservedPorts -contains $port)) {
        $port++
        if ($port -gt 65535) {
            throw "No free TCP port is available after $PreferredPort"
        }
    }
    $script:ReservedPorts += $port
    return $port
}

function Wait-ServiceEndpoint {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Uri,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                return
            }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)

    throw "$Name did not return HTTP 200 within $TimeoutSeconds seconds: $Uri"
}

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination $EnvFile
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python environment was not found: $PythonPath"
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw 'npm.cmd was not found. Install Node.js 20 or newer first.'
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot 'package.json'))) {
    throw "Frontend project was not found: $FrontendRoot"
}

Initialize-Compose
Wait-DockerReady
Stop-TrackedProcesses
Stop-ProjectDevelopmentProcesses

# The online compose file uses an external network. Windows host-development
# mode starts backend and Vite locally, so create that network explicitly.
& docker.exe network inspect skyengine-net 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    & docker.exe network create skyengine-net | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'The skyengine-net Docker network could not be created.'
    }
}

Invoke-Compose @('-p', 'skyengine-online', '--project-directory', $ProjectRoot, '-f', $ComposeFile, 'down', '--remove-orphans')

$script:ReservedPorts = @()
$backendValue = Get-EnvValue -Key 'BACKEND_PORT'
$frontendValue = Get-EnvValue -Key 'FRONTEND_PORT'
$engineValue = Get-EnvValue -Key 'ENGINE_PORT'
$backendPort = if ($backendValue -match '^\d+$') { [int]$backendValue } else { 8233 }
$frontendPort = if ($frontendValue -match '^\d+$') { [int]$frontendValue } else { 5180 }
$enginePort = if ($engineValue -match '^\d+$') { [int]$engineValue } else { 8080 }
$backendPort = Get-FreePort -PreferredPort $backendPort
$frontendPort = Get-FreePort -PreferredPort $frontendPort
$enginePort = Get-FreePort -PreferredPort $enginePort

Set-EnvValue -Key 'BACKEND_PORT' -Value ([string]$backendPort)
Set-EnvValue -Key 'FRONTEND_PORT' -Value ([string]$frontendPort)
Set-EnvValue -Key 'ENGINE_PORT' -Value ([string]$enginePort)
Set-EnvValue -Key 'SKYENGINE_COMPOSE_PATH' -Value $ComposeFile
Set-EnvValue -Key 'SKYENGINE_BATCH_COMPOSE_PATH' -Value $BatchComposeFile
Set-EnvValue -Key 'SKYENGINE_PROJECT_DIR' -Value $ProjectRoot
Set-EnvValue -Key 'SKYENGINE_BATCH_DATASET_HOST_DIR' -Value (Join-Path $ProjectRoot 'dataset')

$env:BACKEND_PORT = [string]$backendPort
$env:FRONTEND_PORT = [string]$frontendPort
$env:ENGINE_PORT = [string]$enginePort
$env:SKYENGINE_COMPOSE_PATH = $ComposeFile
$env:SKYENGINE_BATCH_COMPOSE_PATH = $BatchComposeFile
$env:SKYENGINE_PROJECT_DIR = $ProjectRoot
$env:SKYENGINE_BATCH_DATASET_HOST_DIR = Join-Path $ProjectRoot 'dataset'
$env:ENGINE_URL = "http://127.0.0.1:$enginePort"
$env:BACKEND_URL = "http://127.0.0.1:$backendPort"
$env:RAG_BACKEND_URL = "http://127.0.0.1:$backendPort"

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

Invoke-Compose @('-p', 'skyengine-online', '--project-directory', $ProjectRoot, '-f', $ComposeFile, 'up', '-d', '--build', 'engine')

$backendParameters = @{
    FilePath = $PythonPath
    ArgumentList = @('-m', 'uvicorn', 'application.backend.server:app', '--reload', '--host', '0.0.0.0', '--port', [string]$backendPort)
    WorkingDirectory = $ProjectRoot
    RedirectStandardOutput = Join-Path $LogRoot 'windows-backend.out.log'
    RedirectStandardError = Join-Path $LogRoot 'windows-backend.err.log'
    WindowStyle = 'Hidden'
    PassThru = $true
}
$backend = Start-Process @backendParameters

$frontendParameters = @{
    FilePath = 'npm.cmd'
    ArgumentList = @('run', 'dev', '--', '--host', '0.0.0.0', '--port', [string]$frontendPort)
    WorkingDirectory = $FrontendRoot
    RedirectStandardOutput = Join-Path $LogRoot 'windows-frontend.out.log'
    RedirectStandardError = Join-Path $LogRoot 'windows-frontend.err.log'
    WindowStyle = 'Hidden'
    PassThru = $true
}
$frontend = Start-Process @frontendParameters

@{
    BackendPid = $backend.Id
    FrontendPid = $frontend.Id
    BackendPort = $backendPort
    FrontendPort = $frontendPort
    EnginePort = $enginePort
} | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8

Wait-ServiceEndpoint -Name 'engine' -Uri "http://127.0.0.1:$enginePort/health"
Wait-ServiceEndpoint -Name 'backend' -Uri "http://127.0.0.1:$backendPort/health"
Wait-ServiceEndpoint -Name 'frontend' -Uri "http://127.0.0.1:$frontendPort/"
Wait-ServiceEndpoint -Name 'Vite backend proxy' -Uri "http://127.0.0.1:$frontendPort/api/health"

[PSCustomObject]@{
    BackendUrl = "http://localhost:$backendPort"
    FrontendUrl = "http://localhost:$frontendPort"
    EngineUrl = "http://localhost:$enginePort"
    Status = 'Ready'
}
