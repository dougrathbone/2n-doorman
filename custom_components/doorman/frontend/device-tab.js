/**
 * Device info / grant-access tab for the Doorman panel.
 */

import { define, ws, svc, esc, BASE_CSS } from "./helpers.js";

// ─── Device Tab ───────────────────────────────────────────────────────────────

class DoormanDeviceTab extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._info = null;
    this._users = [];
    this._accessPoints = [];
    this._loading = true;
    this._error = null;
    this._entryId = null;
  }

  set hass(h) { this._hass = h; }
  set entryId(id) { this._entryId = id; }
  connectedCallback() { this._load(); }

  async _load() {
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const [infoRes, usersRes] = await Promise.all([
        ws(this._hass, "doorman/get_device_info", {}, this._entryId),
        ws(this._hass, "doorman/list_users", {}, this._entryId),
      ]);
      this._info = infoRes.device_info || {};
      this._accessPoints = infoRes.access_points || [];
      this._users = usersRes.users || [];
    } catch (e) {
      this._error = e.message || "Failed to load device info";
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _render() {
    const info = this._info || {};
    const multiAp = this._accessPoints.length > 1;
    this.shadowRoot.innerHTML = `
      <style>
        ${BASE_CSS}
        .card { background: var(--card-background-color, white); border-radius: 8px;
          border: 1px solid var(--divider-color); padding: 20px; margin-bottom: 16px; }
        .card h3 { margin: 0 0 16px; font-size: 13px; font-weight: 600; text-transform: uppercase;
          letter-spacing: 0.5px; color: var(--secondary-text-color); }
        .info-grid { display: grid; grid-template-columns: 140px 1fr; gap: 10px 0; }
        .info-label { font-size: 13px; color: var(--secondary-text-color); }
        .info-value { font-size: 13px; font-weight: 500; }
        .btn-row { display: flex; gap: 8px; flex-wrap: wrap; }
        select { width: 100%; padding: 8px 10px; border: 1px solid var(--divider-color);
          border-radius: 4px; background: var(--card-background-color, white);
          color: var(--primary-text-color); font-size: 13px; margin-bottom: 12px; }
      </style>
      ${this._loading ? `<div class="loading">Loading device info…</div>` : this._error ? `<div class="error">${esc(this._error)}</div>` : `
        <div class="card">
          <h3>Device Information</h3>
          <div class="info-grid">
            <span class="info-label">Model</span>
            <span class="info-value">${esc(info.deviceName || "—")}</span>
            <span class="info-label">Firmware</span>
            <span class="info-value">${esc(info.swVersion || "—")}</span>
            <span class="info-label">Serial</span>
            <span class="info-value">${esc(info.serialNumber || "—")}</span>
            <span class="info-label">Hardware</span>
            <span class="info-value">${esc(info.hwVersion || "—")}</span>
          </div>
        </div>
        <div class="card">
          <h3>Quick Access</h3>
          <p style="font-size:13px;color:var(--secondary-text-color);margin:0 0 12px">
            Grant immediate access, bypassing credential checks. Use with care.
          </p>
          ${multiAp ? `<select id="grant-ap"></select>` : ``}
          ${this._users.length > 0 ? `<select id="grant-user"></select>` : ``}
          <div class="btn-row">
            <button class="btn btn-primary" id="grant-btn">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M18,8H17V6A5,5 0 0,0 12,1A5,5 0 0,0 7,6V8H6A2,2 0 0,0 4,10V20A2,2 0 0,0 6,22H18A2,2 0 0,0 20,20V10A2,2 0 0,0 18,8M12,17A2,2 0 0,1 10,15A2,2 0 0,1 12,13A2,2 0 0,1 14,15A2,2 0 0,1 12,17M15.1,8H8.9V6A3.1,3.1 0 0,1 12,2.9A3.1,3.1 0 0,1 15.1,6V8Z"/>
              </svg>
              Grant Access Now
            </button>
          </div>
        </div>
      `}
    `;
    // Populate access point selector safely (names come from device)
    const apSelect = this.shadowRoot.getElementById("grant-ap");
    if (apSelect) {
      for (const ap of this._accessPoints) {
        const opt = document.createElement("option");
        opt.value = String(ap.id);
        opt.textContent = ap.name || `Access point ${ap.id}`;
        apSelect.appendChild(opt);
      }
    }
    // Populate user select safely (names/UUIDs come from device)
    const grantUserSelect = this.shadowRoot.getElementById("grant-user");
    if (grantUserSelect) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Select user\u2026";
      grantUserSelect.appendChild(placeholder);
      for (const u of this._users) {
        const opt = document.createElement("option");
        opt.value = u.uuid;
        opt.textContent = u.name || u.uuid;
        grantUserSelect.appendChild(opt);
      }
    }
    this.shadowRoot.getElementById("grant-btn")?.addEventListener("click", async () => {
      const apId = apSelect ? parseInt(apSelect.value, 10) : 1;
      const userSelect = this.shadowRoot.getElementById("grant-user");
      const userUuid = userSelect?.value || "";
      if (userSelect && !userUuid) {
        alert("Please select a user to grant access to.");
        return;
      }
      try {
        const params = { access_point_id: apId || 1 };
        if (userUuid) params.user_uuid = userUuid;
        await svc(this._hass, "grant_access", params, this._entryId);
      } catch (e) {
        alert(`Failed: ${e.message}`);
      }
    });
  }
}
define("doorman-device-tab", DoormanDeviceTab);
