# Twitch Event Actions

Unified OBS plugin to automate actions based on real-time Twitch events.

## Features

- **Event Engine**: Powered by an IRC listener connected to your Twitch channel.
- **Configurable Actions**:
  - **Chat**: React to chat messages via:
    - **Text**: Displays the message in an OBS text source.
    - **Multiline Text**: Accumulates and formats chat messages in a single panel.
    - **Show/Hide**: Activates specific sources or scenes when events are received.
- **Simplified Authentication**: Supports smart authentication via Playwright or manual token generation.
- **Settings Management**: Allows full configuration import/export between OBS profiles.

## Installation

Run `install.ps1` as Administrator or use `install-all.bat` from the root directory.

Then, in OBS: **Tools → Scripts → [+] → twitch-event-actions.py**.

**Note:** Some advanced automation features (like Playwright integration for authentication) require Python dependencies. Ensure you have the necessary environment set up to run these background processes.

## Initial Configuration

1. Ensure your Twitch credentials are set in the configuration tab (using the "Detect ID and Channel" button after authenticating is recommended).
2. Select the target source for actions (Chat, Subscriptions, etc.) in the respective panels.
3. Use the "▶ Test Chat Action" button to verify functionality.

---
*Disclaimer: This project was primarily developed using Gemini Flash (Google AI Studio) and, to a lesser extent, Kilo free models.*
