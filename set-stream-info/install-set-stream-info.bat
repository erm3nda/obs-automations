@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

if errorlevel 1 (
    echo.
    echo La instalacion termino con errores.
) else (
    echo.
    echo Instalacion completada.
)

pause
endlocal
