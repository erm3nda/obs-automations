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
3. Click "🔒 Guardar credenciales" to move credentials out of OBS settings. The fields stay blank after saving or loading; the status row shows asterisks for values present, the actual storage backend, and whether Twitch validation has succeeded. Values are removed from OBS settings only after a write-and-read-back verification succeeds.
   - Windows uses Credential Manager. Existing plaintext fallback files are migrated there on load; the file is removed only after the stored values are read back and match.
   - On systems without an encrypted credential store, or if Credential Manager fails, saving to `~/.obs-twitch-event-actions-credentials.json` is opt-in: enable "Permitir fallback local SIN CIFRAR si no hay almacén seguro". The UI and log identify the plaintext backend and path. If it is disabled or storage verification fails, the original OBS settings are retained and the UI reports that migration is pending.
   - If Credential Manager and an old fallback file contain different credentials, migration stops and preserves both. To explicitly resolve this conflict, enter all three current credentials before saving; the script will verify the selected values before removing the old fallback.
   - Leave credential fields blank to keep saved values. Use "🗑 Borrar credenciales guardadas" to remove stored credentials and the fallback file.
4. Use "🔍 Comprobar Token" to validate the access token directly with Twitch. The result appears in the credential status row and in the OBS script log at INFO level, including when no token is available or Twitch rejects it. "🔄 Forzar Refresco" rotates the refresh token through TwitchTokenGenerator.com; it does not validate the access token itself. "🔍 Detectar ID y Canal" also checks the access token while retrieving broadcaster details.
5. In the single "⚡ Eventos Twitch" panel, select Chat or another event, enable it, and configure its action and target. Chat's multiline options and EventSub message templates are shown only when applicable.
6. Use "▶ Probar" to verify the selected action.

EventSub events require a valid Twitch Client ID, user OAuth token, and Broadcaster ID. Some events (follows, subscriptions, Bits, redemptions, Guest Star, and moderation) require the corresponding Twitch authorization scopes; Twitch reports any rejected subscription in the OBS script log. Guest Star availability depends on Twitch's beta access. The discontinued legacy Host event is not supported by Twitch EventSub.

The Client ID, access token, and refresh token are excluded from new exports. Existing OBS settings and older JSON exports containing flat `client_id`, `oauth_token`, and `refresh_token` fields are migrated on load or import. Protect or delete old exports because they may still contain credentials.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
