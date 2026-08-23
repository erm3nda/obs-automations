# Record Paths by Scene

OBS Studio script that automatically manages **recording** and **replay buffer** output paths when switching scenes.

## Purpose

This plugin complements OBS native scene switching by handling automations that aren't natively covered. It ensures a truly hands-free setup: once configured, you simply open OBS, launch your game, and hit "Start Streaming" when ready. The plugin takes care of starting replay buffers, recording paths, and scene-based configurations automatically.

## Features

- Folder name derived from the active scene name (strips invalid characters and trailing `escene` suffix).
- **Auto-start** recording and/or replay buffer on scene switch.
- **Keep recording** mode — updates path silently without interrupting active recordings.
- Stream settings are never touched (controlled manually via OBS).

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

> **Note for Aitum Vertical users:** This plugin is compatible with Aitum Vertical, providing additional integration options for managing vertical recording paths automatically.

## Scene naming

Name your scenes after the game. The script strips `™ ® ©`, Windows-invalid characters, and a trailing `escene` suffix.

Example: `BFV escene` → folder `BFV`

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
