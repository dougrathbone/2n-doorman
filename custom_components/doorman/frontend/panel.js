/**
 * Doorman — 2N intercom access control panel for Home Assistant.
 * Vanilla JS ES modules — no build step required.
 */

import { PANEL_VERSION, define, ws, esc } from "./helpers.js";
import "./drawer.js";
import "./users-tab.js";
import "./log-tab.js";
import "./device-tab.js";
import "./notifications-tab.js";

// ─── Main Panel ───────────────────────────────────────────────────────────────

class DoormanPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "users";
    this._devices = [];
    this._selectedEntryId = null;
    // Set from panel.config.version; false here so the common case (versions
    // match) is a no-op in `set panel` rather than an extra shell render.
    this._staleVersion = false;
    this._backendVersion = "";

    // Cross-tab jump event from within a tab (e.g. "go to Users tab" link).
    // Bound here, not in _renderShell(): the listener target is the host
    // element, which is never recreated, while _renderShell() runs on every
    // tab click — so wiring it there added a duplicate handler each time.
    // Every other listener in _renderShell attaches to freshly built shadow
    // nodes and is discarded with them.
    this.addEventListener("doorman-switch-tab", (ev) => {
      const target = ev.detail?.tab;
      if (target && this._tab !== target) {
        this._tab = target;
        this._renderShell();
      }
    });
  }

  set hass(h) {
    const firstSet = !this._hass;
    this._hass = h;
    if (firstSet) this._loadDevices();
    // Pass hass down to whichever tab is mounted
    const tab = this.shadowRoot.querySelector("#tab-content > *");
    if (tab) tab.hass = h;
  }

  set panel(p) {
    this._panel = p;
    // The backend passes its own version in the panel config. If it differs
    // from the version this file was built with, the browser is running
    // frontend code from before a HACS update — the module URL changed, but
    // the already-defined custom elements (see define() above) win, so only a
    // page reload picks up the new code.
    const backendVersion = p?.config?.version || "";
    const stale = Boolean(backendVersion) && backendVersion !== PANEL_VERSION;
    this._backendVersion = backendVersion;
    if (stale === this._staleVersion) return;
    this._staleVersion = stale;
    if (this.isConnected) this._renderShell();
  }
  set narrow(n) {
    // HA re-sets narrow on every viewport change; skip the re-render (and the
    // tab remount + WS refetch + drawer state loss it causes) when unchanged.
    if (n === this._narrow) return;
    this._narrow = n;
    this._renderShell();
  }

  async _loadDevices() {
    try {
      const res = await ws(this._hass, "doorman/list_devices");
      this._devices = res.devices || [];
      this._loadError = null;
      if (this._devices.length > 0) {
        // Restore last selection from sessionStorage, falling back to the first device
        const saved = sessionStorage.getItem("doorman_selected_entry_id");
        if (saved && this._devices.some(d => d.entry_id === saved)) {
          this._selectedEntryId = saved;
        } else {
          this._selectedEntryId = this._devices[0].entry_id;
        }
      } else {
        this._selectedEntryId = null;
      }
      this._renderShell();
    } catch (e) {
      this._devices = [];
      this._selectedEntryId = null;
      this._loadError = e.message || "Could not connect to Doorman";
      this._renderShell();
    }
  }

  connectedCallback() { this._renderShell(); }

  _selectTab(id, { focus = false } = {}) {
    if (this._tab === id && !focus) return;
    this._tab = id;
    this._renderShell();
    if (focus) {
      requestAnimationFrame(() => {
        this.shadowRoot.querySelector(`[role="tab"][data-tab="${id}"]`)?.focus();
      });
    }
  }

  _renderShell() {
    const tabs = [
      { id: "users",         label: "Users" },
      { id: "log",           label: "Access Log" },
      { id: "notifications", label: "Notifications" },
      { id: "device",        label: "Device" },
    ];
    const hasDevice = Boolean(this._selectedEntryId);
    const showTabs = hasDevice && !this._loadError;
    // _loadError is null only after a successful list_devices; undefined before first load.
    const noDevices = this._loadError === null && this._devices.length === 0;

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; height: 100%; background: var(--primary-background-color); }
        .header {
          background: var(--app-header-background-color, var(--primary-color));
          color: var(--app-header-text-color, white);
          height: 56px;
          display: flex;
          align-items: center;
          padding: 0 16px;
          gap: 12px;
          box-shadow: 0 2px 4px rgba(0,0,0,0.18);
          position: sticky;
          top: 0;
          z-index: 10;
        }
        .header h1 { margin: 0; font-size: 20px; font-weight: 400; flex: 1; }
        .device-select {
          padding: 4px 8px;
          border: 1px solid rgba(255,255,255,0.3);
          border-radius: 4px;
          background: transparent;
          color: inherit;
          font-size: 13px;
          font-family: inherit;
          cursor: pointer;
        }
        .device-select option { color: var(--primary-text-color); background: var(--card-background-color); }
        .menu-btn { background: none; border: none; cursor: pointer; color: inherit; line-height: 0; padding: 4px; border-radius: 50%; }
        .menu-btn:focus-visible, .tab:focus-visible, .device-select:focus-visible {
          outline: 2px solid var(--app-header-text-color, white);
          outline-offset: 2px;
        }
        .tab:focus-visible { outline-color: var(--primary-color); }
        .menu-btn svg { width: 24px; height: 24px; fill: currentColor; display: block; }
        .tabs {
          display: ${showTabs ? "flex" : "none"};
          border-bottom: 1px solid var(--divider-color);
          background: var(--primary-background-color);
          padding: 0 16px;
          position: sticky;
          top: 56px;
          z-index: 9;
        }
        .tab {
          padding: 14px 16px;
          cursor: pointer;
          font-size: 14px;
          font-weight: 500;
          color: var(--secondary-text-color);
          border: none;
          background: transparent;
          border-bottom: 2px solid transparent;
          margin-bottom: -1px;
          user-select: none;
          letter-spacing: 0.25px;
          transition: color 0.15s;
          font-family: inherit;
        }
        .tab.active { color: var(--primary-color); border-bottom-color: var(--primary-color); }
        .tab:hover:not(.active) { color: var(--primary-text-color); }
        .content { padding: 20px; max-width: 960px; margin: 0 auto; }
        .update-banner {
          display: flex;
          align-items: flex-start;
          gap: 10px;
          padding: 12px 16px;
          margin-bottom: 16px;
          border: 1px solid var(--divider-color, #e0e0e0);
          border-left: 4px solid var(--primary-color);
          border-radius: 6px;
          background: var(--card-background-color, white);
          color: var(--primary-text-color);
          font-size: 13px;
          line-height: 1.5;
        }
        .update-banner svg { width: 18px; height: 18px; flex-shrink: 0; margin-top: 1px; fill: var(--primary-color); }
        .update-banner button {
          background: none;
          border: none;
          padding: 0;
          font: inherit;
          color: var(--primary-color);
          cursor: pointer;
          text-decoration: underline;
        }
        .unavailable-banner {
          display: flex;
          align-items: flex-start;
          gap: 10px;
          padding: 12px 16px;
          background: color-mix(in srgb, var(--error-color, #f44336) 12%, var(--card-background-color, white));
          border: 1px solid color-mix(in srgb, var(--error-color, #f44336) 35%, transparent);
          border-radius: 6px;
          color: var(--error-color, #c62828);
          font-size: 13px;
          margin-bottom: 16px;
          line-height: 1.5;
        }
        .unavailable-banner svg { flex-shrink: 0; margin-top: 1px; fill: var(--error-color, #c62828); }
        .empty-state {
          text-align: center;
          padding: 48px 24px;
          color: var(--primary-text-color);
        }
        .empty-state h2 {
          margin: 0 0 8px;
          font-size: 20px;
          font-weight: 500;
        }
        .empty-state p {
          margin: 0;
          color: var(--secondary-text-color);
          font-size: 14px;
          line-height: 1.5;
        }
      </style>
      <div class="header">
        ${this._narrow ? `
          <button class="menu-btn" id="menu-btn" type="button" aria-label="Menu">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z"/></svg>
          </button>
        ` : ""}
        <svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" style="opacity:0.9" aria-hidden="true">
          <path d="M18,8H17V6A5,5 0 0,0 12,1A5,5 0 0,0 7,6V8H6A2,2 0 0,0 4,10V20A2,2 0 0,0 6,22H18A2,2 0 0,0 20,20V10A2,2 0 0,0 18,8M12,17A2,2 0 0,1 10,15A2,2 0 0,1 12,13A2,2 0 0,1 14,15A2,2 0 0,1 12,17M15.1,8H8.9V6A3.1,3.1 0 0,1 12,2.9A3.1,3.1 0 0,1 15.1,6V8Z"/>
        </svg>
        <h1>Doorman</h1>
        ${this._devices.length > 1 ? `<select class="device-select" id="device-select" aria-label="Device"></select>` : ""}
      </div>
      <div class="tabs" role="tablist" aria-label="Doorman sections">
        ${tabs.map(t => {
          const selected = this._tab === t.id;
          return `<button type="button" class="tab${selected ? " active" : ""}" role="tab"
            id="tab-${t.id}" data-tab="${t.id}"
            aria-selected="${selected ? "true" : "false"}"
            aria-controls="tab-content"
            tabindex="${selected ? "0" : "-1"}">${t.label}</button>`;
        }).join("")}
      </div>
      <div class="content">
        ${this._staleVersion ? `
          <div class="update-banner" role="status">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4V1L8 5l4 4V6a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/></svg>
            <span>Doorman was updated to ${esc(this._backendVersion)} — this page is still
            running ${esc(PANEL_VERSION)}. <button id="reload-page" type="button">Reload this page</button>
            to finish updating.</span>
          </div>` : ""}
        ${this._loadError ? `
          <div class="unavailable-banner" role="alert">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            <span><strong>Doorman is unavailable</strong> — ${esc(this._loadError)}</span>
          </div>` : ""}
        ${noDevices ? `
          <div class="empty-state" role="status">
            <h2>No devices configured</h2>
            <p>Add a Doorman integration via Settings → Integrations to manage users, access logs, and notifications.</p>
          </div>` : ""}
        <div id="tab-content" role="tabpanel" aria-labelledby="tab-${esc(this._tab)}"></div>
      </div>
    `;

    this.shadowRoot.getElementById("reload-page")?.addEventListener("click", () => {
      location.reload();
    });
    this.shadowRoot.getElementById("menu-btn")?.addEventListener("click", () => {
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    });
    this.shadowRoot.querySelectorAll('[role="tab"]').forEach(el => {
      el.addEventListener("click", () => this._selectTab(el.dataset.tab));
      el.addEventListener("keydown", (ev) => {
        if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
        ev.preventDefault();
        const list = [...this.shadowRoot.querySelectorAll('[role="tab"]')];
        const i = list.indexOf(ev.currentTarget);
        if (i < 0) return;
        const next = ev.key === "ArrowRight"
          ? list[(i + 1) % list.length]
          : list[(i - 1 + list.length) % list.length];
        this._selectTab(next.dataset.tab, { focus: true });
      });
    });

    // Populate device selector safely (device names/serials are untrusted)
    const deviceSelect = this.shadowRoot.getElementById("device-select");
    if (deviceSelect) {
      for (const d of this._devices) {
        const opt = document.createElement("option");
        opt.value = d.entry_id;
        opt.textContent = d.device_name || d.serial_number;
        if (d.entry_id === this._selectedEntryId) opt.selected = true;
        deviceSelect.appendChild(opt);
      }
      deviceSelect.addEventListener("change", (e) => {
        this._selectedEntryId = e.target.value;
        sessionStorage.setItem("doorman_selected_entry_id", this._selectedEntryId);
        this._mountTab();
      });
    }

    this._mountTab();
  }

  _mountTab() {
    const container = this.shadowRoot.getElementById("tab-content");
    if (!container) return;
    container.innerHTML = "";
    // Tabs need an entryId; do not mount when none is selected (zero devices / load error).
    if (!this._selectedEntryId) return;
    const tagMap = {
      users:         "doorman-users-tab",
      log:           "doorman-log-tab",
      notifications: "doorman-notifications-tab",
      device:        "doorman-device-tab",
    };
    const el = document.createElement(tagMap[this._tab]);
    if (this._hass) el.hass = this._hass;
    el.entryId = this._selectedEntryId;
    container.appendChild(el);
  }
}
define("doorman-panel", DoormanPanel);
