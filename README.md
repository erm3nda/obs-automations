# OBS Automations

Collection of Python scripts and plugins for **OBS Studio** organized into independent modules:

## Included Plugins

1. **`record-paths-by-scene`**: Automatically changes the recording and Replay Buffer output path based on the active scene (includes automatic empty folder cleanup and support for clean naming).
2. **`twitch-stream-info`**: Configure and automatically update the stream title, category, and credentials in Twitch per scene, featuring an icon selector and settings import/export.
3. **`twitch-event-actions`**: Unified engine for Twitch events in OBS, supporting chat actions (Text and Multiline Text) and automated authentication.

## Installation

Each plugin has its own PowerShell installation script (`install.ps1`), or you can use the global scripts in the root directory (`install-all.bat` or `install-all.ps1`).
Check the specific documentation within each folder for further information regarding requirements and configuration.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
