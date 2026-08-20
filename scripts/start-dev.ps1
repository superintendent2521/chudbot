param(
    [switch]$NoFrontend,
    [switch]$KeepDatabase
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$envFile = Join-Path $projectRoot ".env"
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)\s*$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

$hasWebPassword = [bool]$env:WEB_WS_PASSWORD
$hasWebPasswordHash = [bool]$env:WEB_WS_PASSWORD_HASH
if ($hasWebPassword -eq $hasWebPasswordHash) {
    throw "Configure exactly one of WEB_WS_PASSWORD or WEB_WS_PASSWORD_HASH in .env before starting the development WebSocket server."
}

$composeFile = Join-Path $projectRoot "docker-compose.dev.yml"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCommand) {
    throw "Docker CLI was not found. Install Docker Desktop, then run this script again."
}

function Test-DockerReady {
    $probe = Start-Process -FilePath $dockerCommand.Source -ArgumentList @("info") -Wait -PassThru -WindowStyle Hidden
    return $probe.ExitCode -eq 0
}

$dockerReady = $false
if (Test-DockerReady) {
    $dockerReady = $true
} else {
    $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path -LiteralPath $dockerDesktop) {
        Write-Host "Starting Docker Desktop..."
        Start-Process -FilePath $dockerDesktop | Out-Null
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            Start-Sleep -Seconds 2
            if (Test-DockerReady) {
                $dockerReady = $true
                break
            }
        }
    }
}
if (-not $dockerReady) {
    throw "Docker Desktop is not running or its Linux engine is unavailable. Start Docker Desktop and wait until it says 'Running', then retry."
}

if (-not (Test-Path -LiteralPath $python)) {
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $systemPython) {
        throw "Python was not found. Install Python 3.11+ and run this script again."
    }
    Write-Host "Creating Python virtual environment..."
    & $systemPython.Source -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python virtual environment."
    }
    Write-Host "Installing Python dependencies..."
    & $python -m pip install -r (Join-Path $projectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Python dependencies."
    }
}

docker compose -f $composeFile up -d postgres
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose could not start PostgreSQL. Check Docker Desktop and retry."
}
try {
    $healthy = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $status = docker inspect --format '{{.State.Health.Status}}' chudbot-dev-postgres 2>$null
        if ($status -eq "healthy") {
            $healthy = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $healthy) {
        throw "Development PostgreSQL did not become healthy. Check: docker compose -f docker-compose.dev.yml logs postgres"
    }

    $env:CHUDBOT_ENVIRONMENT = "dev"
    $env:DATABASE_URL = if ($env:DATABASE_URL) { $env:DATABASE_URL } else { "postgresql://postgres:postgres@127.0.0.1:5432/economy" }
    $env:WEB_WS_HOST = if ($env:WEB_WS_HOST) { $env:WEB_WS_HOST } else { "127.0.0.1" }
    $env:WEB_WS_PORT = if ($env:WEB_WS_PORT) { $env:WEB_WS_PORT } else { "8765" }
    $env:WEB_WS_ALLOW_INSECURE_DEV = if ($env:WEB_WS_ALLOW_INSECURE_DEV) { $env:WEB_WS_ALLOW_INSECURE_DEV } else { "true" }

    $logDirectory = Join-Path $projectRoot "logs"
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $processes = @()
    $processes += Start-Process -FilePath $python -ArgumentList @("index.py") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "dev-bot.out.log") -RedirectStandardError (Join-Path $logDirectory "dev-bot.err.log") -PassThru
    $processes += Start-Process -FilePath $python -ArgumentList @("-m", "chudbot.websocketserver.web_server") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "dev-web.out.log") -RedirectStandardError (Join-Path $logDirectory "dev-web.err.log") -PassThru

    if (-not $NoFrontend -and $env:DEV_FRONTEND_COMMAND) {
        $processes += Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoExit", "-NoProfile", "-Command", $env:DEV_FRONTEND_COMMAND) -WorkingDirectory $projectRoot -PassThru
    }

    Write-Host "Dev services started. Press Ctrl+C to stop local processes."
    Write-Host "Bot environment: dev"
    $webScheme = if ($env:WEB_WS_TLS_CERT -and $env:WEB_WS_TLS_KEY) { "wss" } else { "ws" }
    Write-Host "WebSocket: ${webScheme}://$($env:WEB_WS_HOST):$($env:WEB_WS_PORT)/ws"
    if ($env:DEV_FRONTEND_COMMAND -and -not $NoFrontend) {
        Write-Host "Frontend command: $($env:DEV_FRONTEND_COMMAND)"
    }
    while ($true) {
        $exited = $processes | Where-Object { $_.HasExited }
        if ($exited) {
            foreach ($process in $exited) {
                Write-Warning "Development process $($process.Id) exited with code $($process.ExitCode). Logs are in $logDirectory."
            }
            break
        }
        Start-Sleep -Seconds 2
    }
}
finally {
    if ($processes) {
        foreach ($process in $processes) {
            if (-not $process.HasExited) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
    if (-not $KeepDatabase) {
        docker compose -f $composeFile down
    } else {
        Write-Host "Keeping development PostgreSQL running. Stop it with: docker compose -f docker-compose.dev.yml down"
    }
}
