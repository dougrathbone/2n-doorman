# Doorman

[![CI](https://github.com/dougrathbone/2n-doorman/actions/workflows/ci.yml/badge.svg)](https://github.com/dougrathbone/2n-doorman/actions/workflows/ci.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![GitHub release](https://img.shields.io/github/v/release/dougrathbone/2n-doorman?display_name=tag)](https://github.com/dougrathbone/2n-doorman/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A Home Assistant integration for managing users and access credentials on **2N IP intercoms** — directly from your Home Assistant sidebar.

![Doorman panel screenshot](brand/logo.svg)

---

## Features

- **Sidebar panel** — dedicated Doorman view pinned to the HA left navigation
- **User management** — create, edit, and delete directory users on your 2N device
- **Credential control** — set and update PINs, RFID cards, and switch codes per user
- **Validity windows** — configure time-limited access (valid from / valid to)
- **Access log** — browse the most recent access events (authentications, denials, card taps)
- **HA user linking** — associate 2N directory users with Home Assistant accounts
- **Access events** — fires HA events on every authentication, usable in automations
- **Relay switches** — control door relays directly as HA switch entities
- **Local only** — communicates directly with the device; no cloud dependency

---

## Requirements

- Home Assistant 2024.1.0 or later
- A 2N IP intercom with the HTTP API enabled (Services → HTTP API)
- An API user on the device with **Directory** permission

---

## Installation

### Via HACS (recommended)

1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/dougrathbone/2n-doorman` with category **Integration**
3. Install **Doorman** and restart Home Assistant

### Manual

1. Copy `custom_components/doorman/` into your HA `config/custom_components/` directory
2. Restart Home Assistant

---

## Configuration

1. Go to **Settings → Integrations → Add integration** and search for **Doorman**
2. Enter your 2N device's IP address, API username, and password
3. The **Doorman** panel appears in the left sidebar

### 2N device setup

Enable the HTTP API on your 2N device:

1. Log in to the 2N web interface
2. Navigate to **Services → HTTP API**
3. Enable **HTTP API**
4. Create a user account with at minimum the **Directory** service permission
5. Note the username and password for HA setup

---

## Usage

### Sidebar panel

The Doorman panel has three tabs:

| Tab | Description |
|-----|-------------|
| **Users** | View all directory entries; add, edit, or delete users and their credentials |
| **Access Log** | Browse recent access events from the device |
| **Device** | View device information and trigger immediate access |

### Linking 2N users to HA accounts

In the Users tab, open any user and use the **Link to HA User** dropdown to associate them with a Home Assistant account. Once linked:

- The HA user's name appears alongside their 2N entry
- Access events include the linked HA user ID, enabling presence automations

### Automations

The `doorman_access` event fires whenever an access attempt is detected:

```yaml
trigger:
  - platform: event
    event_type: doorman_access
    event_data:
      event_type: authenticated
action:
  - service: notify.mobile_app_phone
    data:
      message: "{{ trigger.event.data.params.user.name }} entered"
```

### Services

| Service | Description |
|---------|-------------|
| `doorman.create_user` | Add a new user to the 2N directory |
| `doorman.update_user` | Update an existing user's credentials |
| `doorman.delete_user` | Remove a user from the 2N directory |
| `doorman.grant_access` | Open an access point immediately |

---

## Development

```bash
# Install test dependencies
pip install -r requirements_test.txt

# Run tests
pytest tests/ -v

# Lint
ruff check custom_components/ tests/
```

---

## License

MIT
