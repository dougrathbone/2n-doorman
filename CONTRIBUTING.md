# Contributing to Doorman

Thanks for your interest. This guide covers the development workflow, code conventions, and how to test your changes.

---

## Getting started

```bash
git clone https://github.com/dougrathbone/2n-doorman
cd 2n-doorman
pip install -r requirements_test.txt
```

No build step is required. The frontend panel (`panel.js`) is vanilla JS — edit it directly.

---

## Project layout

```
custom_components/doorman/
  __init__.py          # Integration setup, service registration
  api_client.py        # HTTP + Digest-auth client for the 2N REST API
  coordinator.py       # DataUpdateCoordinator + background log-poll task
  config_flow.py       # Setup wizard and options flow
  websocket.py         # HA WebSocket API handlers (called by the panel)
  notifications.py     # Push notification dispatch on access events
  storage.py           # Persistent storage (user links, last-access times)
  diagnostics.py       # HA diagnostics support
  sensor.py            # User count sensor entity
  switch.py            # Relay switch entities
  event.py             # Access event entity
  frontend/
    panel.js           # Sidebar panel (vanilla JS custom element, no build)
  translations/
    en.json            # UI strings
tests/
  conftest.py          # Shared fixtures and mock data
  test_coordinator.py
  test_config_flow.py
  test_init.py
  test_websocket.py
  test_api_client.py
  test_diagnostics.py
```

---

## Running tests

```bash
# All unit tests
pytest tests/ --ignore=tests/integration -v

# With coverage report
pytest tests/ --ignore=tests/integration \
  --cov=custom_components/doorman \
  --cov-report=term-missing \
  --cov-fail-under=60

# Lint
ruff check custom_components/ tests/
```

The test suite uses `pytest-homeassistant-custom-component` which spins up a real (in-process) HA instance. Tests should run in under 5 seconds.

---

## Code conventions

- **Python**: follow existing style; ruff is the linter (config in `pyproject.toml` if present, otherwise defaults)
- **Async**: use `async_create_background_task` for long-running tasks that shouldn't block `async_block_till_done`
- **No secrets**: never commit real credentials, IPs, or device serials
- **Translations**: any new UI string goes in both `strings.json` and `translations/en.json`
- **Services**: service schemas live in `services.yaml`; human-readable labels in `translations/en.json`
- **Frontend**: all dynamic HTML must use `createElement`/`textContent` — no string interpolation into `innerHTML`

---

## Adding a new feature

1. Write the backend change (Python)
2. Add or update WebSocket handlers in `websocket.py` if the panel needs new data
3. Update the panel in `frontend/panel.js`
4. Add translations in `strings.json` and `translations/en.json`
5. Write tests — aim to keep overall coverage above 60%
6. Bump `manifest.json` version if this will be released

---

## Submitting a pull request

- Target the `main` branch
- CI runs lint, all unit tests, a 60% coverage gate, and HACS validation automatically
- Fill in the PR template — especially the testing section
- One logical change per PR where practical

---

## Working with a real device

The integration talks to the 2N HTTP API directly. To test against a real device:

1. Enable the HTTP API under **Services → HTTP API** on the device
2. Create an API user with **Directory**, **System (Control)**, and **Access Log** permissions
3. Set the host/username/password in HA and load the integration

The mock server in `tests/integration/` can simulate device responses without hardware — see that directory's README for setup.
