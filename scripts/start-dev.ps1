param(
    [switch]$NoFrontend,
    [switch]$KeepDatabase
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$composeArgs = @("compose", "-f", "docker-compose.dev.yml")

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install Docker Desktop and retry."
}

if ($NoFrontend) {
    Write-Host "Frontend startup is not configured by the Docker development stack."
}

try {
    & docker @composeArgs up -d --build
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose could not start the development stack."
    }

    Write-Host "Docker development stack started."
    Write-Host "WebSocket: ws://127.0.0.1:8765/ws"
    Write-Host "View logs with: docker compose -f docker-compose.dev.yml logs -f bot web"
    & docker @composeArgs logs -f bot web
}
finally {
    if (-not $KeepDatabase) {
        & docker @composeArgs down
    } else {
        Write-Host "Keeping the Docker development stack running. Stop it with: docker compose -f docker-compose.dev.yml down"
    }
}
