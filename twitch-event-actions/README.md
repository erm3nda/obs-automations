# Twitch Event Actions

Unified OBS plugin to automate actions based on real-time Twitch events.

## Features

- **Event Engine**: Powered by an IRC listener connected to your Twitch channel.
- **Configurable Actions**:
  - **Chat Events**: Trigger OBS actions from incoming Twitch chat messages:
    - **Text**: Displays the message in an OBS text source.
    - **Multiline Text**: Accumulates and formats chat messages in a single panel.
    - **Show/Hide**: Activates specific sources or scenes when chat events are received.
- **Direct Token Authorization**: Opens the official Twitch OAuth2 URL directly in your browser, with scopes pre-filled from your configuration. Approve and paste the token — no external tools or automation needed.
- **Settings Management**: Allows full configuration import/export between OBS profiles.

## Installation

**Option A (Recommended):** In OBS, go to **Tools → Scripts → [+]**, navigate to this folder and select `twitch-event-actions.py` directly.

**Option B (Optional):** Run `install.ps1` as Administrator or use `install-all.bat` from the root directory to copy the script into the OBS scripts folder automatically. This is a convenience and not required — Option A works without it.

**Note:** No external Python dependencies are required. No `helpers/` folder or extra scripts needed. Token authorization opens directly in your system browser.

## Initial Configuration

1. Click "🌐 Abrir URL de Autorización Twitch" to open the Twitch OAuth2 consent page in your browser with your configured scopes pre-filled. Approve the request, copy the returned token, and paste it into the "Twitch OAuth Token" field.
2. Use the "🔍 Detectar ID y Canal" button to auto-fill your Broadcaster ID and chat channel.
3. Select the target source for actions (Chat, Subscriptions, etc.) in the respective panels.
4. Use the "▶ Probar Acción Chat" button to verify functionality.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
