# Twitch Stream Info

OBS Studio plugin to manage stream information on Twitch, customizable per scene.

## Features

- Automatic update of **title**, **category**, and tags based on the active scene.
- **Settings Import/Export**: Export and import scene configurations between different setups or profiles.
- **Credential storage**: Credentials use Windows Credential Manager when available. Other systems, or Windows when the manager is unavailable, use a local fallback file and show a warning.
- **Icon Selector**: Quickly add icons to your stream titles.
- **Update Modes**: Flexible configuration (Manual, Immediate, or Delayed).
- Direct token authorization: opens the official Twitch OAuth2 URL in your browser with scopes pre-filled — approve and paste the token. Automatic token refreshing via the Helix API.

## Installation

**Option A (Recommended):** In OBS, go to **Tools → Scripts → [+]**, navigate to this folder and select `twitch-stream-info.py` directly.

**Option B (Optional):** Run `install.ps1` as Administrator to copy the script into the OBS scripts folder. This is a convenience and not required — Option A works without it.

**Note:** No external Python dependencies are required. No `helpers/` folder or extra scripts needed. Token authorization opens directly in your system browser with scopes pre-filled in the URL.

## Configuration

From the script panel in OBS, you can configure:
- Twitch credentials (Client ID, Client Secret, Access Token, Refresh Token).
- Information update modes.
- Title and category mapping for each scene.
- Global suffix for titles.

Use **Save credentials** after entering credentials to remove the Client ID, client secret, and tokens from the OBS script settings. On Windows they are stored in Credential Manager; if that is unavailable, or on other systems, they are stored as plain text in `~/.obs-twitch-stream-info-credentials.json` and a warning is logged. If both stores fail, credentials remain in OBS settings and the script logs a warning. Exported settings do not include credentials, so they must be configured separately on another computer. Delete or protect JSON exports made by older versions because they may still contain credentials.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
