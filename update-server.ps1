param(
    [string]$User = "rhys",
    [string]$KeyFile = "$env:USERPROFILE\Documents\ssh keys\id_rsa"
)

$ErrorActionPreference = "Stop"
$server = "192.168.0.187"

if (-not (Test-Path -LiteralPath $KeyFile -PathType Leaf)) {
    Write-Error "SSH key not found: $KeyFile"
    exit 1
}

Write-Host "Connecting to $User@$server..."
Write-Host "Running ~/update-chudite.sh"
Write-Host "--- remote output ---"

& ssh.exe `
    -i $KeyFile `
    -o BatchMode=yes `
    -o ConnectTimeout=10 `
    -o ServerAliveInterval=15 `
    -o ServerAliveCountMax=3 `
    -o StrictHostKeyChecking=accept-new `
    "$User@$server" `
    'bash ~/update-chudite.sh'

$status = $LASTEXITCODE
Write-Host "--- remote command exited with status $status ---"
exit $status
