/**
 * Access log tab for the Doorman panel.
 */

import { define, ws, svc, esc, formatDateTime, BASE_CSS } from "./helpers.js";

// Default filter for a device the user has never filtered before: pick the
// first "interesting" event type that actually appears in the buffer. Order
// is roughly by how directly the event answers "who came to the door".
// If none of these are present, fall back to the most recent event's type.
const LOG_FILTER_DEFAULT_PREFERENCE = [
  "UserAuthenticated",
  "UserRejected",
  "CardEntered",
  "CodeEntered",
  "MobKeyEntered",
  "FingerEntered",
  "DoorbellPressed",
];

// Matches backend MAX_STORED_LOG_EVENTS — no point buffering more than the
// store will return. Filtering narrows the rendered view.
const LOG_CLIENT_BUFFER_LIMIT = 1000;

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
    // null = uninitialised; a Set once _events lands.
    this._selectedTypes = null;
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
      this._unsubLive = null;
    }
  }

  _onLiveEvent(ev) {
    if (!this._events) return;
    this._events.unshift({ event: ev.event_type, params: ev.params || {}, utcTime: ev.utc_time });
    if (this._events.length > LOG_CLIENT_BUFFER_LIMIT) {
      this._events.length = LOG_CLIENT_BUFFER_LIMIT;
    }
    this._render();
  }

  async _load() {
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const res = await ws(this._hass, "doorman/get_access_log", {}, this._entryId);
      this._events = (res.events || []).slice().reverse();
      // Re-seed filter defaults against the fresh buffer when nothing was
      // persisted yet; keep an existing sessionStorage selection.
      if (this._selectedTypes === null) {
        this._selectedTypes = this._loadSelectedTypes();
      }
    } catch (e) {
      this._error = e.message;
    } finally {
      this._loading = false;
      this._render();
    }
  }

  // ── Filter state (per-entry, session-scoped) ─────────────────────────

  _filterStorageKey() {
    return `doorman_log_filter.${this._entryId || "default"}`;
  }

  _loadSelectedTypes() {
    try {
      const raw = sessionStorage.getItem(this._filterStorageKey());
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) return new Set(arr.filter((t) => typeof t === "string"));
      }
    } catch (e) { /* fall through to default */ }
    return this._defaultSelectedTypes();
  }

  _defaultSelectedTypes() {
    const available = this._typeCounts();
    for (const t of LOG_FILTER_DEFAULT_PREFERENCE) {
      if (available.has(t)) return new Set([t]);
    }
    const newest = (this._events || [])[0];
    if (newest?.event) return new Set([newest.event]);
    return new Set();
  }

  _saveSelectedTypes() {
    try {
      sessionStorage.setItem(
        this._filterStorageKey(),
        JSON.stringify([...this._selectedTypes]),
      );
    } catch (e) { /* private mode / quota — keep in-memory */ }
  }

  _ensureFilterInitialised() {
    if (this._selectedTypes === null) this._selectedTypes = this._loadSelectedTypes();
  }

  _typeCounts() {
    const counts = new Map();
    for (const e of this._events || []) {
      const t = e.event;
      if (!t) continue;
      counts.set(t, (counts.get(t) || 0) + 1);
    }
    return counts;
  }

  _setAllTypesSelected() {
    this._selectedTypes = new Set(this._typeCounts().keys());
    this._saveSelectedTypes();
    this._render();
  }

  _renderFilterPanel() {
    const panel = this.shadowRoot.getElementById("filter-panel");
    if (!panel) return;
    if (!this._events?.length) {
      panel.innerHTML = `<div class="filter-empty">No events yet.</div>`;
      return;
    }
    const counts = this._typeCounts();
    const types = [...counts.keys()].sort();
    panel.innerHTML = `
      <div class="filter-actions">
        <button class="link" id="filter-all" type="button">All</button>
        <button class="link" id="filter-none" type="button">None</button>
      </div>
      ${types.map((t) => `
        <label class="filter-item">
          <input type="checkbox" data-type="${esc(t)}" ${this._selectedTypes.has(t) ? "checked" : ""}>
          <span class="type-name">${esc(t)}</span>
          <span class="type-count">${counts.get(t)}</span>
        </label>
      `).join("")}
    `;
    panel.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.addEventListener("change", () => {
        const t = cb.dataset.type;
        if (cb.checked) this._selectedTypes.add(t);
        else this._selectedTypes.delete(t);
        this._saveSelectedTypes();
        this._render();
      });
    });
    panel.querySelector("#filter-all")?.addEventListener("click", (e) => {
      e.preventDefault();
      this._setAllTypesSelected();
    });
    panel.querySelector("#filter-none")?.addEventListener("click", (e) => {
      e.preventDefault();
      this._selectedTypes = new Set();
      this._saveSelectedTypes();
      this._render();
    });
  }

  _render() {
    const wasFilterOpen = this.shadowRoot?.getElementById("filter-menu")?.open === true;
    if (this._events) this._ensureFilterInitialised();
    const selectedCount = this._selectedTypes ? this._selectedTypes.size : 0;
    const filterDisabled = !this._events?.length;

    const shadow = this.shadowRoot;
    shadow.innerHTML = `
      <style>
        ${BASE_CSS}
        .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
        .toolbar h2 { margin: 0; font-size: 16px; font-weight: 500; flex: 1; }
        .toolbar-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .success { color: var(--success-color, #2e7d32); font-weight: 500; }
        .fail    { color: var(--error-color, #f44336); font-weight: 500; }
        .event-type { font-family: monospace; font-size: 12px; background: var(--secondary-background-color, #f5f5f5); padding: 2px 6px; border-radius: 3px; }
        .toast {
          position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
          background: #323232; color: white; padding: 10px 20px; border-radius: 4px;
          font-size: 13px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); z-index: 100;
        }
        .toast.error { background: var(--error-color, #f44336); }
        .filter { position: relative; }
        .filter-summary { list-style: none; user-select: none; }
        .filter-summary::-webkit-details-marker { display: none; }
        .filter[open] .filter-panel { display: block; }
        .filter-panel {
          display: none;
          position: absolute;
          right: 0;
          top: calc(100% + 4px);
          z-index: 10;
          background: var(--card-background-color, white);
          color: var(--primary-text-color);
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          min-width: 240px;
          max-height: 340px;
          overflow-y: auto;
          padding: 4px 0;
          box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        .filter-actions {
          display: flex;
          gap: 4px;
          padding: 6px 12px;
          border-bottom: 1px solid var(--divider-color);
          font-size: 12px;
          color: var(--secondary-text-color);
        }
        .filter-actions .link {
          background: none; border: none; padding: 2px 4px;
          color: var(--primary-color); cursor: pointer;
          font: inherit; font-size: 12px;
        }
        .filter-actions .link:hover { text-decoration: underline; }
        .filter-item {
          display: flex; align-items: center; gap: 8px;
          padding: 6px 12px; cursor: pointer; font-size: 13px;
        }
        .filter-item:hover { background: var(--secondary-background-color); }
        .filter-item input { margin: 0; }
        .filter-item .type-name { flex: 1; font-family: monospace; font-size: 12px; }
        .filter-item .type-count { color: var(--secondary-text-color); font-size: 11px; }
        .filter-empty { padding: 12px; font-size: 12px; color: var(--secondary-text-color); font-style: italic; }
        .toolbar-note { font-size: 12px; color: var(--secondary-text-color); margin: 0 0 8px; }
      </style>
      <div class="toolbar">
        <h2>Access Log</h2>
        <div class="toolbar-actions">
          <details class="filter" id="filter-menu" ${wasFilterOpen ? "open" : ""}>
            <summary class="btn btn-outlined filter-summary" ${filterDisabled ? 'style="opacity:0.5;pointer-events:none"' : ""}>
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M6,13H18V11H6M3,6V8H21V6M10,18H14V16H10V18Z"/></svg>
              Filter (${selectedCount})
            </summary>
            <div class="filter-panel" id="filter-panel"></div>
          </details>
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
    this._renderFilterPanel();

    const content = shadow.getElementById("content");
    if (this._loading) { content.innerHTML = `<div class="loading">Loading log…</div>`; return; }
    if (this._error)   { content.innerHTML = `<div class="error" role="alert">${esc(this._error)}</div>`; return; }
    if (!this._events?.length) { content.innerHTML = `<div class="empty">No log events found.</div>`; return; }

    const totalCount = this._events.length;
    const filtered = this._events.filter((e) => e.event && this._selectedTypes.has(e.event));

    if (!filtered.length) {
      content.innerHTML = `
        <div class="empty">
          No events match the current filter.
          <div style="margin-top:8px">
            <button class="link" id="clear-filter" type="button"
              style="background:none;border:none;color:var(--primary-color);cursor:pointer;font:inherit">
              Clear filter
            </button>
          </div>
        </div>
      `;
      shadow.getElementById("clear-filter")?.addEventListener("click", () => this._setAllTypesSelected());
      return;
    }

    if (filtered.length !== totalCount) {
      const note = document.createElement("p");
      note.className = "toolbar-note";
      note.textContent = `Showing ${filtered.length} of ${totalCount} events`;
      content.appendChild(note);
    }

    const wrap = document.createElement("div");
    wrap.className = "table-scroll";
    const table = document.createElement("table");
    table.innerHTML = `
      <thead><tr><th>Time</th><th>Event</th><th>User / Card</th><th>Result</th></tr></thead>
      <tbody>
        ${filtered.map((e) => {
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
