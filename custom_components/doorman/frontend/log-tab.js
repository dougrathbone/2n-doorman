/**
 * Access log tab for the Doorman panel.
 */

import { define, ws, svc, esc, formatDateTime, BASE_CSS } from "./helpers.js";

// ─── Access Log Tab ───────────────────────────────────────────────────────────

class DoormanLogTab extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._events = null;
    this._loading = true;
    this._error = null;
    this._entryId = null;
    this._unsubLive = null;
    this._resyncing = false;
    this._toastTimer = null;
  }

  set hass(h) { this._hass = h; }
  set entryId(id) { this._entryId = id; }
  connectedCallback() {
    this._load();
    this._subscribeLive();
  }

  disconnectedCallback() {
    if (this._unsubLive) { this._unsubLive(); this._unsubLive = null; }
  }

  async _subscribeLive() {
    try {
      this._unsubLive = await this._hass.connection.subscribeMessage(
        (ev) => this._onLiveEvent(ev),
        { type: "doorman/subscribe_events", ...(this._entryId ? { entry_id: this._entryId } : {}) }
      );
    } catch (e) {
      // Older backend without the subscription command — stay on manual refresh
      this._unsubLive = null;
    }
  }

  _onLiveEvent(ev) {
    if (!this._events) return;  // initial load not finished — it will include these
    this._events.unshift({ event: ev.event_type, params: ev.params || {}, utcTime: ev.utc_time });
    if (this._events.length > 100) this._events.length = 100;
    this._render();
  }

  async _load() {
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const res = await ws(this._hass, "doorman/get_access_log", {}, this._entryId);
      this._events = (res.events || []).slice().reverse();
    } catch (e) {
      this._error = e.message;
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _render() {
    const shadow = this.shadowRoot;
    shadow.innerHTML = `
      <style>
        ${BASE_CSS}
        .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
        .toolbar h2 { margin: 0; font-size: 16px; font-weight: 500; flex: 1; }
        .toolbar-actions { display: flex; gap: 8px; flex-wrap: wrap; }
        .success { color: var(--success-color, #2e7d32); font-weight: 500; }
        .fail    { color: var(--error-color, #f44336); font-weight: 500; }
        .event-type { font-family: monospace; font-size: 12px; background: var(--secondary-background-color, #f5f5f5); padding: 2px 6px; border-radius: 3px; }
        .toast {
          position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
          background: #323232; color: white; padding: 10px 20px; border-radius: 4px;
          font-size: 13px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); z-index: 100;
        }
        .toast.error { background: var(--error-color, #f44336); }
      </style>
      <div class="toolbar">
        <h2>Access Log</h2>
        <div class="toolbar-actions">
          <button class="btn btn-outlined" id="resync-btn" type="button" ${this._resyncing ? "disabled" : ""}>
            Resync history
          </button>
          <button class="btn btn-outlined" id="refresh-btn" type="button">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z"/></svg>
            Refresh
          </button>
        </div>
      </div>
      <div id="content"></div>
    `;

    shadow.getElementById("refresh-btn").addEventListener("click", () => this._load());
    shadow.getElementById("resync-btn").addEventListener("click", () => this._resync());

    const content = shadow.getElementById("content");
    if (this._loading) { content.innerHTML = `<div class="loading">Loading log…</div>`; return; }
    if (this._error)   { content.innerHTML = `<div class="error" role="alert">${esc(this._error)}</div>`; return; }
    if (!this._events?.length) { content.innerHTML = `<div class="empty">No log events found.</div>`; return; }

    const wrap = document.createElement("div");
    wrap.className = "table-scroll";
    const table = document.createElement("table");
    table.innerHTML = `
      <thead><tr><th>Time</th><th>Event</th><th>User / Card</th><th>Result</th></tr></thead>
      <tbody>
        ${this._events.slice(0, 100).map(e => {
          const params = e.params || {};
          const user = params.name || params.uid || "—";
          const valid = params.valid;
          const resultClass = valid === false ? "fail" : "success";
          const resultText = valid === false ? "✗ Denied" : "✓ OK";
          return `
            <tr>
              <td>${formatDateTime(e.utcTime)}</td>
              <td><span class="event-type">${esc(e.event || "—")}</span></td>
              <td>${esc(user)}</td>
              <td class="${resultClass}">${valid !== undefined ? resultText : "—"}</td>
            </tr>
          `;
        }).join("")}
      </tbody>
    `;
    if (this._events.length > 100) {
      const note = document.createElement("p");
      note.style.cssText = "font-size:12px;color:var(--secondary-text-color);margin:0 0 8px";
      note.textContent = `Showing 100 of ${this._events.length} events`;
      content.appendChild(note);
    }
    wrap.appendChild(table);
    content.appendChild(wrap);
  }

  _showToast(text, error = false) {
    let toast = this.shadowRoot.querySelector(".toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "toast";
      toast.setAttribute("role", "status");
      toast.setAttribute("aria-live", "polite");
      this.shadowRoot.appendChild(toast);
    }
    toast.classList.toggle("error", !!error);
    toast.textContent = text;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => toast.remove(), error ? 4000 : 2500);
  }

  async _resync() {
    if (this._resyncing) return;
    this._resyncing = true;
    const btn = this.shadowRoot.getElementById("resync-btn");
    if (btn) btn.disabled = true;
    try {
      await svc(this._hass, "resync_log_history", {}, this._entryId);
      this._showToast("History resync complete");
      await this._load();
    } catch (e) {
      this._showToast(e.message || "Resync failed", true);
    } finally {
      this._resyncing = false;
      const b = this.shadowRoot.getElementById("resync-btn");
      if (b) b.disabled = false;
    }
  }
}
define("doorman-log-tab", DoormanLogTab);
