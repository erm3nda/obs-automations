# install.ps1
# Copia set-escene-path.py al directorio de scripts de OBS Studio

$scriptName = "set-escene-path.py"
$sourceFile = Join-Path $PSScriptRoot $scriptName
$obsScriptsDir = "C:\Program Files\obs-studio\data\obs-plugins\frontend-tools\scripts"
$destination = Join-Path $obsScriptsDir $scriptName

# Verificar que el archivo fuente existe
if (-not (Test-Path $sourceFile)) {
    Write-Error "No se encontro el archivo fuente: $sourceFile"
    exit 1
}

# Verificar que el directorio de OBS existe
if (-not (Test-Path $obsScriptsDir)) {
    Write-Error "No se encontro el directorio de scripts de OBS: $obsScriptsDir"
    Write-Host "Asegurate de que OBS Studio esta instalado correctamente."
    exit 1
}

# Copiar el script
try {
    Copy-Item -Path $sourceFile -Destination $destination -Force
    Write-Host "Script copiado correctamente a: $destination" -ForegroundColor Green
    Write-Host ""
    Write-Host "Ahora abre OBS Studio y ve a:" -ForegroundColor Cyan
    Write-Host "  Herramientas > Scripts > [+] > selecciona '$scriptName'" -ForegroundColor Cyan
} catch {
    Write-Error "Error al copiar el script: $_"
    Write-Host "Intenta ejecutar este script como Administrador." -ForegroundColor Yellow
    exit 1
}
