# install-all.ps1
# Script maestro de instalación para todos los plugins de OBS Studio.
# Se autoeleva UNA SOLA VEZ a Administrador y ejecuta recursivamente los install.ps1 de cada carpeta.

# Verificar si se ejecuta como Administrador, si no, autoelevarse manteniendo abierta la ventana al finalizar
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Solicitando permisos de Administrador para instalar todos los plugins..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}


Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "   Instalador Maestro de Plugins OBS (obs-automations)" -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host ""

# Buscar todos los scripts install.ps1 que esten dentro de las subcarpetas (excluyendo este propio script)
$installScripts = Get-ChildItem -Path $PSScriptRoot -Recurse -Filter "install.ps1" | Where-Object { $_.FullName -ne $PSCommandPath }

if (-not $installScripts) {
    Write-Warning "No se encontraron scripts install.ps1 en las subcarpetas."
    exit 0
}

$installScripts = @($installScripts | Sort-Object FullName)
Write-Host "Instaladores detectados: $($installScripts.Count)" -ForegroundColor Cyan
foreach ($script in $installScripts) {
    Write-Host "  - $($script.Directory.Name)" -ForegroundColor DarkCyan
}
Write-Host ""

# Establecer variable de entorno para que los scripts hijos sepan que el maestro ya está elevado
$env:OBS_AUTO_INSTALL_RUNNING = "True"

$installedCount = 0

foreach ($script in $installScripts) {
    $pluginName = $script.Directory.Name
    Write-Host "---------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "Instalando plugin: [$pluginName]..." -ForegroundColor Yellow
    
    try {
        & $script.FullName
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            throw "El instalador terminó con código $LASTEXITCODE"
        }
        $installedCount++
    } catch {
        Write-Error "Fallo al ejecutar instalador de $pluginName : $_"
    }
}

Write-Host ""
Write-Host "=========================================================" -ForegroundColor Green
Write-Host "   ¡Exito! Se han instalado $installedCount plugins en OBS." -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
Write-Host "Directorio destino: $env:ProgramFiles\obs-studio\data\obs-plugins\frontend-tools\scripts\" -ForegroundColor DarkCyan
Write-Host "En OBS Studio: Ve a Herramientas > Scripts y haz clic en Recargar / '+'" -ForegroundColor Cyan
Write-Host ""
Pause

