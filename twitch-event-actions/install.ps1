# install.ps1
# Copia Twitch Event Actions al directorio de scripts de OBS Studio.

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $env:OBS_AUTO_INSTALL_RUNNING) {
    Write-Host "Solicitando permisos de Administrador..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$scriptName = "twitch-event-actions.py"
$sourceFile = Join-Path $PSScriptRoot $scriptName
$obsScriptsDir = Join-Path $env:ProgramFiles "obs-studio\data\obs-plugins\frontend-tools\scripts"
$destination = Join-Path $obsScriptsDir $scriptName

if (-not (Test-Path $sourceFile)) {
    Write-Error "No se encontro el archivo fuente: $sourceFile"
    exit 1
}

if (-not (Test-Path $obsScriptsDir)) {
    Write-Error "No se encontro el directorio de scripts de OBS:`n  $obsScriptsDir"
    exit 1
}

try {
    # Retirar las copias antiguas que pueden seguir cargadas por OBS.
    @(
        "set-text-on-twitch-chat.py",
        "set-animation-on-twitch-subscribe.py"
    ) | ForEach-Object {
        $legacyFile = Join-Path $obsScriptsDir $_
        if (Test-Path $legacyFile) {
            Remove-Item $legacyFile -Force
            Write-Host "Script antiguo eliminado: $_" -ForegroundColor DarkYellow
        }
    }

    Copy-Item -Path $sourceFile -Destination $destination -Force
    
    # Copiar todos los archivos de la carpeta helpers a la carpeta de scripts de OBS
    $helpersDir = Join-Path $PSScriptRoot "..\helpers"
    
    if (Test-Path $helpersDir) {
        Get-ChildItem $helpersDir | Copy-Item -Destination $obsScriptsDir -Force
        Write-Host "Helpers copiados a: $obsScriptsDir" -ForegroundColor Green
    }
    
    Write-Host "Twitch Event Actions e instaladores instalados correctamente en:" -ForegroundColor Green
    Write-Host "  $destination" -ForegroundColor Green
    Write-Host "En OBS: Herramientas > Scripts > [+] > selecciona '$scriptName'" -ForegroundColor Cyan
} catch {
    Write-Error "Error al copiar Twitch Event Actions: $_"
    exit 1
}
