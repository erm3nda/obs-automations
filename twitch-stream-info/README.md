# Twitch Stream Info

OBS Studio plugin to manage stream information on Twitch, customizable per scene.

## Features

- Automatic update of **title**, **category**, and tags based on the active scene.
- **Settings Import/Export**: Export and import scene configurations between different setups or profiles.
- **Icon Selector**: Quickly add icons to your stream titles.
- **Update Modes**: Flexible configuration (Manual, Immediate, or Delayed).
- Integration with the Twitch API (Helix) for authentication and token refreshing.

## Installation

Run `install.ps1` as Administrator to copy the script to the OBS Studio scripts folder. Then, in OBS: **Tools → Scripts → [+] → twitch-stream-info.py**.

## Configuration

From the script panel in OBS, you can configure:
- Twitch credentials (Client ID, Access Token, Refresh Token).
- Information update modes.
- Title and category mapping for each scene.
- Global suffix for titles.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
