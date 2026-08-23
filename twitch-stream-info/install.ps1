# install.ps1
# Copia twitch-stream-info.py y twitch-stream-info.json al directorio de scripts de OBS Studio.

# Evitar elevación si ya se ejecuta como Admin o se invoca desde el maestro
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $env:OBS_AUTO_INSTALL_RUNNING) {
    Write-Host "Solicitando permisos de Administrador..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}


$obsScriptsDir = Join-Path $env:ProgramFiles "obs-studio\data\obs-plugins\frontend-tools\scripts"

if (-not (Test-Path $obsScriptsDir)) {
    Write-Error "No se encontro el directorio de scripts de OBS:`n  $obsScriptsDir`nAsegurate de que OBS Studio esta instalado."
    exit 1
}

try {
    # Copiar script Python
    Copy-Item -Path (Join-Path $PSScriptRoot "twitch-stream-info.py") -Destination (Join-Path $obsScriptsDir "twitch-stream-info.py") -Force
    
    # Copiar archivo twitch-stream-info.json si existe
    $jsonFile = Join-Path $PSScriptRoot "twitch-stream-info.json"
    if (Test-Path $jsonFile) {
        Copy-Item -Path $jsonFile -Destination (Join-Path $obsScriptsDir "twitch-stream-info.json") -Force
    }



    Write-Host "twitch-stream-info instalado correctamente en:" -ForegroundColor Green
    Write-Host "  $obsScriptsDir" -ForegroundColor Green
} catch {
    Write-Error "Error al copiar twitch-stream-info: $_"
    exit 1
}
