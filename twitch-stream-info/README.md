# Twitch Stream Info

OBS Studio plugin to manage stream information on Twitch, customizable per scene.

## Features

- Automatic update of **title**, **category**, and tags based on the active scene.
- **Settings Import/Export**: Export and import scene configurations between different setups or profiles.
- The scene editor opens on the active scene; use its selector to edit other scenes.
- **Credential storage**: Credentials use Windows Credential Manager when available. A local plaintext fallback is available only after explicit consent.
- **Icon Selector**: Quickly add icons to your stream titles.
- **Update Modes**: Flexible configuration (Manual, Immediate, or Delayed).
- Direct token authorization: opens the official Twitch OAuth2 URL in your browser with scopes pre-filled — approve and paste the token. Automatic token refreshing via Twitch OAuth2.

## Installation

**Option A (Recommended):** In OBS, go to **Tools → Scripts → [+]**, navigate to this folder and select `twitch-stream-info.py` directly.

**Option B (Optional):** Run `install.ps1` as Administrator to copy the script into the OBS scripts folder. This is a convenience and not required — Option A works without it.

**Note:** No external Python dependencies are required. No `helpers/` folder or extra scripts needed. Token authorization opens directly in your system browser with scopes pre-filled in the URL.

## Configuration

From the script panel in OBS, you can configure:
- Enable or disable the plugin with the standalone **Plugin Activo** checkbox; the remaining controls are grouped under **Opciones del plugin**.
- Twitch credentials (Client ID, Client Secret, Access Token, Refresh Token).
- The credential status shows asterisks when each value is available, identifies the verified storage backend, and reports access-token validation separately; it never displays credential contents. **Comprobar Token** calls Twitch's OAuth2 `/validate` endpoint directly without refreshing the token, and reports every result both in the credential-status row and the OBS INFO log. A successful check includes the remaining token lifetime; a Client ID mismatch is reported explicitly. Missing tokens, HTTP rejection/errors, and connection failures also update both surfaces. Token values are never logged. A refresh token's presence does not prove it is valid.
- Information update modes.
- Title and category mapping for each scene.
- Global suffix for titles.

Use **Save credentials** after entering credentials to store the complete credential set, verify it by reading it back, and then remove the previous OBS-settings copy. The fields stay blank after saving or loading because saved credentials are not displayed there; leave them blank to keep the saved values. On Windows, the preferred backend is Windows Credential Manager. The local fallback is disabled by default and requires enabling **Permitir fallback local SIN CIFRAR** before saving. When used, its location is `~/.obs-twitch-stream-info-credentials.json`; it is plaintext on every OS, including Windows, and the script warns about its path. On Windows, file access depends on the user profile's ACLs; the script does not encrypt this file. Unix-like systems apply file permissions `0600`. If persistence or migration fails, the script retains the prior source and reports the pending migration rather than exposing stored credentials in the input fields.

If the Windows Credential Manager and local fallback contain different values for the same credential, automatic migration stops and both copies remain unchanged. Resolve the conflict by explicitly entering all four credentials and saving them; the destination is verified before old copies are removed.

The fallback-consent setting is local-only and is not included in imports or exports. Exported settings do not include credentials, so they must be configured separately on another computer. Older JSON exports containing flat credential fields are imported and migrated to the available backend; if persistence is unavailable or not consented to, the script keeps the source JSON and does not copy its secrets into OBS settings or the fallback file. Delete or protect JSON exports made by older versions because they may still contain credentials.

When the Client Secret is not configured, token renewal sends the refresh token to TwitchTokenGenerator.com. Configure the Client ID and Client Secret to use Twitch's token endpoint directly.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
