# Twitch Stream Info

Plugin de OBS Studio para gestionar la información de la transmisión en Twitch de forma individual por escena.

## Características

- Actualización automática de **título**, **categoría** y etiquetas según la escena activa.
- **Guardado/Carga de ajustes**: Permite exportar e importar la configuración de escenas entre equipos o perfiles.
- **Selector de iconos**: Añade iconos rápidamente a los títulos de tus directos.
- **Modos de actualización**: Configuración flexible (Manual, inmediato o retardado).
- Integración con la API de Twitch (Helix) para autenticación y refresco de tokens.

## Instalación

Ejecuta `install.ps1` como Administrador para copiar el script a la carpeta de scripts de OBS Studio. Luego, en OBS: **Herramientas → Scripts → [+] → twitch-stream-info.py**.

## Configuración

Desde el panel del script en OBS, puedes configurar:
- Credenciales de Twitch (Client ID, Access Token, Refresh Token).
- Modos de actualización de la información.
- Mapeo de título y categoría para cada escena.
- Coletilla global para los títulos.
