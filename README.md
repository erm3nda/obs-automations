# set-escene-path

OBS Studio script that automatically changes the **recording path** and **replay buffer path** when switching scenes.

## Features

- 📁 Folder name derived from the **active OBS scene name** (strips invalid characters and trailing `escene` suffix)
- ⏺️ **Auto-start recording** when switching scenes
- 🔁 **Auto-start replay buffer** when switching scenes
- 🔒 **Keep recording** option — only updates the path config without interrupting active recordings
- 🔴 Stream is **never touched**
- ⚡ Race-condition safe: uses deferred timers to let OBS settle before stop/start operations

## How it works

1. On scene change → path updated in OBS config immediately
2. 500ms later → stop recording/replay (if needed)
3. On `RECORDING_STOPPED` event → 1500ms later → start in new path

This two-step delay avoids GUI glitches and ensures OBS has fully finalized the previous file before starting a new one.

## Scene naming

Name your OBS scenes after the game. The script strips:
- Trademark symbols (`™`, `®`, `©`)
- Windows-invalid characters (`< > : " / \ | ? *`)
- Trailing `escene` suffix (e.g. `BFV escene` → `BFV`)

## Installation

Run `install.ps1` as Administrator — it copies `set-escene-path.py` to:
```
C:\Program Files\obs-studio\data\obs-plugins\frontend-tools\scripts\
```

Then in OBS: **Tools → Scripts → [+] → set-escene-path.py**

## Configuration (in OBS Scripts panel)

| Option | Description |
|---|---|
| **Carpeta base** | Root folder where per-scene subfolders will be created |
| **Mantener grabación activa** | Don't interrupt active recording; path applies on next session |
| **Auto-start: grabación** | Automatically start recording when scene changes |
| **Auto-start: replay buffer** | Automatically start replay buffer when scene changes |

## Requirements

- OBS Studio (tested on OBS 30+)
- Windows (uses PowerShell installer)
