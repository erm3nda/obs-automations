# Record Paths by Scene

OBS Studio script that automatically sets the **recording** and **replay buffer** output path when switching scenes.

## Features

- Folder name derived from the active scene name (strips invalid characters and trailing `escene` suffix).
- **Auto-start** recording and/or replay buffer on scene switch.
- **Keep recording** mode — updates path silently without interrupting active recordings.
- Stream is never touched.

## Installation

Run `install.ps1` as Administrator. It copies the script to:
```
%ProgramFiles%\obs-studio\data\obs-plugins\frontend-tools\scripts\
```
Then in OBS: **Tools → Scripts → [+] → record-paths-by-scene.py**

## Options

| Option | Description |
|---|---|
| Base folder | Root folder where per-scene subfolders are created |
| Keep recording active | Don't stop on scene switch; new path applies on next session |
| Auto-start recording | Start recording automatically on scene switch |
| Auto-start replay buffer | Start replay buffer automatically on scene switch |

## Scene naming

Name your scenes after the game. The script strips `™ ® ©`, Windows-invalid characters, and a trailing `escene` suffix.

Example: `BFV escene` → folder `BFV`

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
