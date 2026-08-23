# install.ps1
# Copia Set-Stream-Info.py y stream_info.json al directorio de scripts de OBS Studio.

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
    Copy-Item -Path (Join-Path $PSScriptRoot "Set-Stream-Info.py") -Destination (Join-Path $obsScriptsDir "Set-Stream-Info.py") -Force
    
    # Copiar archivo stream_info.json si existe
    $jsonFile = Join-Path $PSScriptRoot "stream_info.json"
    if (Test-Path $jsonFile) {
        Copy-Item -Path $jsonFile -Destination (Join-Path $obsScriptsDir "stream_info.json") -Force
    }



    Write-Host "Set-Stream-Info instalado correctamente en:" -ForegroundColor Green
    Write-Host "  $obsScriptsDir" -ForegroundColor Green
} catch {
    Write-Error "Error al copiar Set-Stream-Info: $_"
    exit 1
}
