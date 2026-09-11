#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$ProjectRoot = $PSScriptRoot
$ComposeFile = Join-Path $ProjectRoot 'docker-compose-online.yaml'
$BatchComposeFile = Join-Path $ProjectRoot 'docker-compose.yaml'
$PlatformComposeFile = Join-Path $ProjectRoot 'docker-compose.yml'
$EnvFile = Join-Path $ProjectRoot '.env'
$StateFile = Join-Path $ProjectRoot '.skyengine-windows-state.json'
$FrontendRoot = Join-Path $ProjectRoot 'application\frontend'

function Stop-ProcessTree {
    param([Parameter(Mandatory)][int]$ProcessId)

    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    }
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

if (Test-Path -LiteralPath $StateFile) {
    $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    foreach ($name in @('BackendPid', 'FrontendPid')) {
        $processId = [int]$state.$name
        if ($processId -gt 0) {
            Stop-ProcessTree -ProcessId $processId
        }
    }
    Remove-Item -LiteralPath $StateFile -Force
}
Stop-ProjectDevelopmentProcesses

if (Get-Command docker.exe -ErrorAction SilentlyContinue) {
    & docker.exe info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & docker.exe compose version 2>$null | Out-Null
        $useComposePlugin = $LASTEXITCODE -eq 0

        function Invoke-StopCompose {
            param([Parameter(Mandatory)][string[]]$Arguments)

            if ($useComposePlugin) {
                & docker.exe compose @Arguments 2>$null | Out-Null
            }
            elseif (Get-Command docker-compose.exe -ErrorAction SilentlyContinue) {
                & docker-compose.exe @Arguments 2>$null | Out-Null
            }
        }

        Invoke-StopCompose @('-p', 'skyengine-online', '--project-directory', $ProjectRoot, '-f', $ComposeFile, 'down', '--remove-orphans')
        if (Test-Path -LiteralPath $EnvFile) {
            Invoke-StopCompose @('--project-directory', $ProjectRoot, '-f', $PlatformComposeFile, 'down', '--remove-orphans')
            Invoke-StopCompose @('-p', 'skyengine-batch', '--project-directory', $ProjectRoot, '-f', $BatchComposeFile, 'down', '--remove-orphans')
        }
    }
}

Write-Output 'SkyEngine Windows services stopped.'
