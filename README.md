# OBS Automations

Collection of Python scripts and plugins for **OBS Studio** organized into independent modules:

## Included Plugins

1. **`record-paths-by-scene`**: Automatically changes the recording and Replay Buffer output path based on the active scene (includes automatic empty folder cleanup and support for clean naming).
2. **`twitch-stream-info`**: Configure and automatically update the stream title, category, and credentials in Twitch per scene, featuring an icon selector and settings import/export.
3. **`twitch-event-actions`**: Unified engine for Twitch events in OBS, supporting chat actions (Text and Multiline Text) and browser-based token authorization.

## How it works

These plugins are designed to work together with OBS Studio's automatic scene switching capabilities (e.g., scene switching based on the active executable/game). When a scene switch occurs, the plugins automatically update the relevant recording paths and stream information based on your pre-configured settings.

## Installation

Each plugin can be installed directly from OBS (**Tools → Scripts → [+] → select the .py file**). The PowerShell `install.ps1` scripts and the root `install-all.bat` / `install-all.ps1` are optional convenience tools that copy scripts into the OBS scripts folder — they are not required. Check the specific documentation within each folder for requirements and configuration.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
