# OBS Automations

Colección de scripts y plugins en Python para **OBS Studio** organizados por módulos independientes:

## Plugins incluidos

1. **`record-paths-by-scene`**: Cambia automáticamente la ruta de salida de grabación y Replay Buffer según la escena activa (con limpieza de carpetas vacías y soporte para nombramiento limpio).
2. **`twitch-stream-info`**: Permite configurar y actualizar automáticamente el título, la categoría y las credenciales de la transmisión en Twitch por escena, con selector de iconos y guardado/carga de ajustes.
3. **`twitch-event-actions`**: Motor unificado para eventos de Twitch en OBS, con soporte para acciones basadas en chat (Texto y Texto multilineal) y autenticación automatizada.

## Instalación

Cada plugin cuenta con su propio script de instalación en PowerShell (`install.ps1`), o puedes utilizar los scripts globales de la raíz (`install-all.bat` o `install-all.ps1`).  
Revisa la documentación específica dentro de cada carpeta para más información sobre requisitos y configuración.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
