# OBS Automations

Collection of Python scripts for **OBS Studio** organized into independent modules:

## Included Scripts

1. **`record-paths-by-scene`**: Automatically changes the recording and Replay Buffer output path based on the active scene, including automatic empty folder cleanup and support for clean naming. [Read more](record-paths-by-scene/README.md)
2. **`twitch-stream-info`**: Configure and automatically update the stream title, category, and credentials in Twitch per scene, featuring an icon selector and settings import/export. [Read more](twitch-stream-info/README.md)
3. **`twitch-event-actions`**: Automate OBS actions from Twitch chat and EventSub events, with per-event configuration, a compact event selector, and browser-based token authorization. [Read more](twitch-event-actions/README.md)
4. **`aitum-vertical-options`**: Gestiona la sincronización de escenas y archivos de Aitum Vertical, con auto-inicios opcionales desactivados por defecto. [Read more](aitum-vertical-options/README.md)

## How it works

These scripts are designed to work together with OBS Studio's automatic scene switching capabilities (e.g., scene switching based on the active executable/game). When a scene switch occurs, the scripts automatically update the relevant recording paths and stream information based on your pre-configured settings.

## Installation

Each plugin can be installed directly from OBS (**Tools → Scripts → [+] → select the .py file**). The PowerShell `install.ps1` scripts and the root `install-all.bat` / `install-all.ps1` are optional convenience tools that copy scripts into the OBS scripts folder — they are not required. Check the specific documentation within each folder for requirements and configuration.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*