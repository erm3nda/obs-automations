# install.ps1
# Copia set-escene-path.py al directorio de scripts de OBS Studio.
# Compatible con cualquier usuario y cualquier instalacion estandar de OBS.

$scriptName  = "set-escene-path.py"
$sourceFile  = Join-Path $PSScriptRoot $scriptName
$obsScriptsDir = Join-Path $env:ProgramFiles "obs-studio\data\obs-plugins\frontend-tools\scripts"
$destination = Join-Path $obsScriptsDir $scriptName

# Verificar que el archivo fuente existe
if (-not (Test-Path $sourceFile)) {
    Write-Error "No se encontro el archivo fuente: $sourceFile"
    exit 1
}

# Verificar que el directorio de OBS existe
if (-not (Test-Path $obsScriptsDir)) {
    Write-Error "No se encontro el directorio de scripts de OBS:`n  $obsScriptsDir`nAsegurate de que OBS Studio esta instalado."
    exit 1
}

# Copiar el script
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
