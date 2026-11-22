# CampChef BBQ Grills for Home Assistant

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Timminater&repository=campchef&category=integration)

An unofficial Home Assistant integration for Camp Chef Wi-Fi grills. It uses the Camp Chef cloud to display grill status, temperatures and probe readings. Available entities and controls depend on the grill model.

## Installation

1. Add [Timminater/campchef](https://github.com/Timminater/campchef) to HACS or Marketplace as a custom integration repository.
2. Install **CampChef BBQ Grills** and restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → CampChef BBQ Grills**.
4. Enter your Camp Chef account email address and the PIN you receive by email.

For manual installation, copy `custom_components/campchef` to your Home Assistant configuration's `custom_components` directory and restart.

## Reauthentication

Open **Re-authenticate** on the existing CampChef integration and continue to request a fresh email PIN. Enter the newest PIN from your inbox or spam folder. Home Assistant updates the existing integration entry.
