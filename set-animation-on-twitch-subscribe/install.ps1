# install.ps1
# Copia set-animation-on-twitch-subscribe.py al directorio de scripts de OBS Studio.

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Solicitando permisos de Administrador..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$scriptName  = "set-animation-on-twitch-subscribe.py"
$sourceFile  = Join-Path $PSScriptRoot $scriptName
$obsScriptsDir = Join-Path $env:ProgramFiles "obs-studio\data\obs-plugins\frontend-tools\scripts"
$destination = Join-Path $obsScriptsDir $scriptName

if (-not (Test-Path $sourceFile)) {
    Write-Error "No se encontro el archivo fuente: $sourceFile"
    exit 1
}

if (-not (Test-Path $obsScriptsDir)) {
    Write-Error "No se encontro el directorio de scripts de OBS:`n  $obsScriptsDir`nAsegurate de que OBS Studio esta instalado."
    exit 1
}

try {
    Copy-Item -Path $sourceFile -Destination $destination -Force
    Write-Host "Script copiado correctamente a:" -ForegroundColor Green
    Write-Host "  $destination" -ForegroundColor Green
    Write-Host ""
    Write-Host "En OBS: Herramientas > Scripts > [+] > selecciona '$scriptName'" -ForegroundColor Cyan
} catch {
    Write-Error "Error al copiar: $_`nIntenta ejecutar este script como Administrador."
    exit 1
}
