# Twitch Event Actions

Unified OBS plugin to automate actions based on real-time Twitch events.

## Features

- **Event Engine**: Powered by an IRC listener connected to your Twitch channel.
- **Configurable Actions**:
  - **Chat and EventSub Events**: Configure each event independently through the same compact event selector, without a separate settings block per event. Chat is the first event in the list:
    - Follows, new subscriptions, resubscriptions, gifted subscriptions, and subscription endings.
    - Incoming raids, Cheers/Bits, and custom Channel Points redemptions.
    - Stream online/offline and title/category changes.
    - Guest Star session/guest updates and moderator add/remove or ban/unban notifications.
  - Every event has its own enable switch, OBS action, target, and duration. Chat-only multiline options and EventSub message templates appear only when relevant. Templates support `{user}`, `{message}`, `{amount}`, `{reward}`, `{title}`, `{category}`, `{reason}`, and `{action}`.
  - Twitch's retired legacy **Host** event is not available; use **Raid recibido** for current incoming raids.

- **Direct Token Authorization**: Opens the official Twitch OAuth2 URL directly in your browser, with scopes pre-filled from your configuration. Approve and paste the token — no external tools or automation needed.
- **Credential storage**: The Client ID, OAuth/access token, and refresh token are stored in Windows Credential Manager on Windows. Other systems use a local fallback file and show a warning.
- **Settings Management**: Allows full configuration import/export between OBS profiles.

## Installation

**Option A (Recommended):** In OBS, go to **Tools → Scripts → [+]**, navigate to this folder and select `twitch-event-actions.py` directly.

**Option B (Optional):** Run `install.ps1` as Administrator or use `install-all.bat` from the root directory to copy the script into the OBS scripts folder automatically. This is a convenience and not required — Option A works without it.

**Note:** No external Python dependencies are required. No `helpers/` folder or extra scripts needed. Token authorization opens directly in your system browser.

## Initial Configuration

1. Enable the events you need, then click "🌐 Abrir URL de Autorización Twitch". The URL includes the scopes needed by enabled events. Approve the request, copy the returned token, and paste the access and refresh tokens into their fields.
2. Use the "🔍 Detectar ID y Canal" button to auto-fill your Broadcaster ID and chat channel.
3. Click "🔒 Guardar credenciales" to move credentials out of OBS settings. On Windows they are stored in Credential Manager; if that is unavailable, or on other systems, they are stored as plain text in `~/.obs-twitch-event-actions-credentials.json` and a warning is logged. Leave credential fields blank to keep saved credentials. Use "🗑 Borrar credenciales guardadas" to remove them.
4. In the single "⚡ Eventos Twitch" panel, select Chat or another event, enable it, and configure its action and target. Chat's multiline options and EventSub message templates are shown only when applicable.
5. Use "▶ Probar" to verify the selected action.

EventSub events require a valid Twitch Client ID, user OAuth token, and Broadcaster ID. Some events (follows, subscriptions, Bits, redemptions, Guest Star, and moderation) require the corresponding Twitch authorization scopes; Twitch reports any rejected subscription in the OBS script log. Guest Star availability depends on Twitch's beta access. The discontinued legacy Host event is not supported by Twitch EventSub.

The Client ID, access token, and refresh token are excluded from new exports. Existing OBS settings and older JSON exports may contain credentials: they are migrated on load or import, but protect or delete old exports because they may still contain credentials.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
