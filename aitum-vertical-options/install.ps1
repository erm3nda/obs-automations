# Copia Aitum Vertical Options al directorio de scripts de OBS Studio.

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $env:OBS_AUTO_INSTALL_RUNNING) {
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$scriptName = "aitum-vertical-options.py"
$sourceFile = Join-Path $PSScriptRoot $scriptName
$obsScriptsDir = Join-Path $env:ProgramFiles "obs-studio\data\obs-plugins\frontend-tools\scripts"
$destination = Join-Path $obsScriptsDir $scriptName

if (-not (Test-Path $sourceFile)) { Write-Error "No se encontro el archivo fuente: $sourceFile"; exit 1 }
if (-not (Test-Path $obsScriptsDir)) { Write-Error "No se encontro el directorio de scripts de OBS: $obsScriptsDir"; exit 1 }
Copy-Item -Path $sourceFile -Destination $destination -Force
Write-Host "Aitum Vertical Options instalado correctamente en: $destination" -ForegroundColor Green
Write-Host "En OBS: Herramientas > Scripts > [+] > selecciona '$scriptName'" -ForegroundColor Cyan