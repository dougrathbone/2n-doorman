/**
 * Doorman — 2N intercom access control panel for Home Assistant.
 * Vanilla JS custom element — no build step required.
 */

// ─── Helpers ────────────────────────────────────────────────────────────────

const ws = (hass, type, params = {}, entryId = null) => {
  const msg = { type, ...params };
  if (entryId) msg.entry_id = entryId;
  return hass.callWS(msg);
};
const svc = (hass, service, data = {}, entryId = null) => {
  const d = { ...data };
  if (entryId) d.device = entryId;
  return hass.callService("doorman", service, d);
};

// Escape untrusted text before interpolating into innerHTML. All strings from
// the 2N device (names, UUIDs, log fields, device info) are device-controlled.
function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
    // Every interpolation in this file uses double-quoted attributes, so
    // single quotes are not currently an escape, but escaping them keeps
    // esc() safe if a single-quoted attribute is ever introduced.
    .replace(/'/g, "&#39;");
}

function formatDate(ts) {
  if (!ts) return "Always";
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}

function formatDateTime(str) {
  if (!str) return "—";
  // 2N log events report utcTime as epoch seconds (uint32); stored
  // last_access values may be legacy ISO strings.
  const d = (typeof str === "number" || /^\d+$/.test(String(str)))
    ? new Date(Number(str) * 1000)
    : new Date(str);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function toDateTimeLocalValue(unixTs) {
  if (!unixTs) return "";
  const d = new Date(unixTs * 1000);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function localDateTimeWithOffset(localISO) {
  if (!localISO) return localISO;
  const offset = -new Date().getTimezoneOffset(); // minutes ahead of UTC
  const sign = offset >= 0 ? "+" : "-";
  const h = String(Math.floor(Math.abs(offset) / 60)).padStart(2, "0");
  const m = String(Math.abs(offset) % 60).padStart(2, "0");
  return `${localISO}:00${sign}${h}:${m}`;
}

// ─── Shared styles ───────────────────────────────────────────────────────────

const BASE_CSS = `
  :host {
    display: block;
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
    color: var(--primary-text-color);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
  }
  th {
    text-align: left;
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--secondary-text-color);
    border-bottom: 2px solid var(--divider-color);
  }
  td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--divider-color, #e0e0e0);
    vertical-align: middle;
  }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--secondary-background-color, #f5f5f5); }
  .badge {
    display: inline-block;
    padding: 2px 7px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
  }
  .badge-yes    { background: #e8f5e9; color: #2e7d32; }
  .badge-no     { background: #f5f5f5; color: #9e9e9e; }
  .badge-active   { background: #e8f5e9; color: #2e7d32; }
  .badge-inactive { background: #fce4e4; color: #c62828; }
  .badge-future   { background: #fff8e1; color: #f57f17; }
  .icon-btn {
    background: none;
    border: none;
    cursor: pointer;
    padding: 4px;
    border-radius: 50%;
    color: var(--secondary-text-color);
    line-height: 0;
    transition: background 0.15s;
  }
  .icon-btn:hover { background: var(--secondary-background-color); color: var(--primary-text-color); }
  .icon-btn svg { width: 18px; height: 18px; fill: currentColor; display: block; }
  .btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border: none;
    border-radius: 4px;
    font-size: 14px;
    cursor: pointer;
    font-family: inherit;
    transition: background 0.15s;
  }
  .btn-primary { background: var(--primary-color); color: white; }
  .btn-primary:hover { opacity: 0.9; }
  .btn-outlined { background: transparent; border: 1px solid var(--divider-color); color: var(--primary-text-color); }
  .btn-outlined:hover { background: var(--secondary-background-color); }
  .btn-danger { background: var(--error-color, #f44336); color: white; }
  .loading { padding: 32px; text-align: center; color: var(--secondary-text-color); }
  .empty   { padding: 32px; text-align: center; color: var(--secondary-text-color); font-style: italic; }
  .error   { padding: 12px 16px; color: var(--error-color, #f44336); background: #fff3f3; border-radius: 4px; margin: 8px 0; }
  .field-group { display: flex; flex-direction: column; gap: 12px; }
  .field { display: flex; flex-direction: column; gap: 4px; }
  .field label { font-size: 12px; font-weight: 500; color: var(--secondary-text-color); }
  .field input, .field select {
    padding: 8px 10px;
    border: 1px solid var(--divider-color, #ccc);
    border-radius: 4px;
    font-size: 14px;
    font-family: inherit;
    background: var(--card-background-color, white);
    color: var(--primary-text-color);
  }
  .field input:focus, .field select:focus {
    outline: none;
    border-color: var(--primary-color);
  }
  .ha-link { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--secondary-text-color); }
  .actions { display: flex; gap: 4px; justify-content: flex-end; }
`;

// ─── Drawer (slide-in edit panel) ────────────────────────────────────────────

class DoormanDrawer extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  connectedCallback() { this._render(); }

  open(title, content, onSave) {
    this._title = title;
    this._content = content;
    this._onSave = onSave;
    this._saving = false;
    this._open = true;
    this._render();
  }

  close() {
    this._open = false;
    this._render();
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        .overlay {
          position: fixed; inset: 0; z-index: 100;
          background: rgba(0,0,0,0.32);
          display: ${this._open ? "flex" : "none"};
          justify-content: flex-end;
        }
        .drawer {
          width: min(420px, 100vw);
          height: 100%;
          background: var(--card-background-color, white);
          box-shadow: -4px 0 24px rgba(0,0,0,0.15);
          display: flex;
          flex-direction: column;
        }
        .drawer-header {
          display: flex;
          align-items: center;
          padding: 16px 20px;
          border-bottom: 1px solid var(--divider-color);
          gap: 12px;
        }
        .drawer-header h2 { margin: 0; font-size: 18px; font-weight: 500; flex: 1; }
        .close-btn {
          background: none; border: none; cursor: pointer; padding: 4px;
          color: var(--secondary-text-color); line-height: 0; border-radius: 50%;
        }
        .close-btn:hover { background: var(--secondary-background-color); }
        .close-btn svg { width: 20px; height: 20px; fill: currentColor; display: block; }
        .drawer-body { flex: 1; overflow-y: auto; padding: 20px; }
        .drawer-footer {
          padding: 16px 20px;
          border-top: 1px solid var(--divider-color);
          display: flex;
          gap: 8px;
          justify-content: flex-end;
        }
        .btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px;
          border: none; border-radius: 4px; font-size: 14px; cursor: pointer; font-family: inherit; }
        .btn:disabled { opacity: 0.6; cursor: default; }
        .btn-primary { background: var(--primary-color); color: white; }
        .btn-outlined { background: transparent; border: 1px solid var(--divider-color); color: var(--primary-text-color); }
        .field-group { display: flex; flex-direction: column; gap: 12px; }
        .field { display: flex; flex-direction: column; gap: 4px; }
        .field label { font-size: 12px; font-weight: 500; color: var(--secondary-text-color); }
        .field input, .field select { padding: 8px 10px; border: 1px solid var(--divider-color, #ccc);
          border-radius: 4px; font-size: 14px; font-family: inherit;
          background: var(--card-background-color, white); color: var(--primary-text-color); }
        .field input:focus, .field select:focus { outline: none; border-color: var(--primary-color); }
        .section-title { font-size: 11px; font-weight: 600; text-transform: uppercase;
          letter-spacing: 0.5px; color: var(--secondary-text-color); margin: 16px 0 8px; }
        .required { color: var(--error-color, #f44336); margin-left: 2px; }
        .optional-hint { font-weight: 400; text-transform: none; font-size: 10px; opacity: 0.7; }
        .error { padding: 10px 12px; color: var(--error-color, #f44336);
          background: #fff3f3; border-radius: 4px; font-size: 13px; }
      </style>
      <div class="overlay">
        <div class="drawer">
          <div class="drawer-header">
            <h2 id="drawer-title"></h2>
            <button class="close-btn" id="close-btn">
              <svg viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
            </button>
          </div>
          <div class="drawer-body" id="drawer-body"></div>
          <div class="drawer-footer">
            <button class="btn btn-outlined" id="cancel-btn">Cancel</button>
            <button class="btn btn-primary" id="save-btn">Save</button>
          </div>
        </div>
      </div>
    `;
    // Title comes from device-controlled strings (user name/UUID) — set via textContent
    this.shadowRoot.getElementById("drawer-title").textContent = this._title || "";
    if (this._content && this._open) {
      const body = this.shadowRoot.getElementById("drawer-body");
      body.innerHTML = "";
      body.appendChild(this._content);
    }
    this.shadowRoot.getElementById("close-btn")?.addEventListener("click", () => this.close());
    this.shadowRoot.getElementById("cancel-btn")?.addEventListener("click", () => this.close());
    this.shadowRoot.getElementById("save-btn")?.addEventListener("click", async (ev) => {
      // Disable while the async onSave is in flight to prevent double submits
      if (this._saving) return;
      this._saving = true;
      ev.currentTarget.disabled = true;
      try {
        if (this._onSave) await this._onSave();
      } finally {
        this._saving = false;
        ev.currentTarget.disabled = false;
      }
    });
  }
}
customElements.define("doorman-drawer", DoormanDrawer);


// ─── Users Tab ───────────────────────────────────────────────────────────────

class DoormanUsersTab extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._users = null;
    this._haUsers = [];
    this._notifyServices = [];
    this._writePermission = true;
    this._loading = true;
    this._error = null;
    this._drawer = null;
    this._filter = "";
    this._sortKey = "name";
    this._sortAsc = true;
    this._entryId = null;
  }

  set hass(h) {
    this._hass = h;
    if (!this._users && !this._loading) this._load();
  }

  set entryId(id) { this._entryId = id; }

  connectedCallback() { this._load(); }

  async _load() {
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const eid = this._entryId;
      const [usersRes, haUsersRes, notifyRes] = await Promise.all([
        ws(this._hass, "doorman/list_users", {}, eid),
        ws(this._hass, "doorman/list_ha_users").catch(() => ({ users: [] })),
        ws(this._hass, "doorman/list_notify_services").catch(() => ({ services: [] })),
      ]);
      this._users = usersRes.users || [];
      this._writePermission = usersRes.write_permission !== false;
      this._haUsers = haUsersRes.users || [];
      this._notifyServices = notifyRes.services || [];
    } catch (e) {
      this._error = e.message || "Failed to load users";
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _haUserName(id) {
    return this._haUsers.find(u => u.id === id)?.name || id;
  }

  _accessStatus(u) {
    if (u.enabled === false) return { label: "Disabled", cls: "badge-inactive" };
    const hasCredentials = u.pin || (u.card || []).filter(Boolean).length || (u.code || []).filter(Boolean).length;
    if (!hasCredentials) return { label: "No credentials", cls: "badge-inactive" };
    const now = Date.now() / 1000;
    if (u.validTo && u.validTo < now) return { label: "Expired", cls: "badge-inactive" };
    if (u.validFrom && u.validFrom > now) return { label: "Not yet active", cls: "badge-future" };
    return { label: "Active", cls: "badge-active" };
  }

  _sortedFilteredUsers() {
    const q = this._filter.toLowerCase();
    let users = q
      ? (this._users || []).filter(u => (u.name || "").toLowerCase().includes(q))
      : (this._users || []).slice();
    users.sort((a, b) => {
      let va, vb;
      if (this._sortKey === "name") {
        va = (a.name || "").toLowerCase(); vb = (b.name || "").toLowerCase();
      } else if (this._sortKey === "last_access") {
        va = a.last_access || ""; vb = b.last_access || "";
      } else if (this._sortKey === "access") {
        va = this._accessStatus(a).label; vb = this._accessStatus(b).label;
      } else {
        va = ""; vb = "";
      }
      if (va < vb) return this._sortAsc ? -1 : 1;
      if (va > vb) return this._sortAsc ? 1 : -1;
      return 0;
    });
    return users;
  }

  _sortHeader(key, label) {
    const active = this._sortKey === key;
    const arrow = active ? (this._sortAsc ? " ▲" : " ▼") : "";
    return `<th class="sortable${active ? " sort-active" : ""}" data-sort="${key}" style="cursor:pointer;user-select:none">${label}${arrow}</th>`;
  }

  _render() {
    // Wipe shadow DOM — any previously appended drawer is now detached, so clear the reference
    // to ensure it's recreated fresh on the next open call.
    this._drawer = null;
    const shadow = this.shadowRoot;
    shadow.innerHTML = `
      <style>
        ${BASE_CSS}
        .toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
        .toolbar h2 { margin: 0; font-size: 16px; font-weight: 500; }
        .search-row { display: flex; gap: 8px; margin-bottom: 16px; }
        .search-input { flex: 1; padding: 7px 10px; border: 1px solid var(--divider-color);
          border-radius: 4px; background: var(--card-background-color, white);
          color: var(--primary-text-color); font-size: 13px; }
        .perm-warning { display: flex; align-items: flex-start; gap: 10px; padding: 12px 16px;
          background: #fff8e1; border: 1px solid #ffe082; border-radius: 6px;
          color: #5d4037; font-size: 13px; margin-bottom: 16px; line-height: 1.5; }
        .perm-warning svg { flex-shrink: 0; margin-top: 1px; }
        th.sortable:hover { color: var(--primary-color); }
        th.sort-active { color: var(--primary-color); }
      </style>
      <div class="toolbar">
        <h2>Directory Users</h2>
        ${this._writePermission ? `
          <button class="btn btn-primary" id="add-btn">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M19 13H13V19H11V13H5V11H11V5H13V11H19V13Z"/></svg>
            Add User
          </button>` : ``}
      </div>
      ${!this._writePermission ? `
        <div class="perm-warning">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="#f57f17"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
          <span><strong>Read-only mode</strong> — the API user lacks Directory write permissions on the 2N device.
          Create, edit and delete are disabled. To fix this, enable Directory write access for the API user in the 2N web interface:
          <em>Settings → Services → HTTP API → Users</em>.</span>
        </div>` : ``}
      <div id="content"></div>
    `;

    shadow.getElementById("add-btn")?.addEventListener("click", () => this._openAddDrawer());

    const content = shadow.getElementById("content");
    if (this._loading) {
      content.innerHTML = `<div class="loading">Loading users…</div>`;
      return;
    }
    if (this._error) {
      content.innerHTML = `<div class="error">${esc(this._error)}</div>`;
      return;
    }
    if (!this._users?.length) {
      content.innerHTML = `<div class="empty">No users configured on this device.</div>`;
      return;
    }

    // Search box — preserved across re-renders via _filter state
    const searchRow = document.createElement("div");
    searchRow.className = "search-row";
    const searchInput = document.createElement("input");
    searchInput.className = "search-input";
    searchInput.id = "search";
    searchInput.type = "search";
    searchInput.placeholder = "Filter by name\u2026";
    searchInput.value = this._filter;
    searchRow.appendChild(searchInput);
    content.appendChild(searchRow);
    searchInput.addEventListener("input", e => {
      this._filter = e.target.value;
      this._rebuildTable(content);
    });
    searchInput.setSelectionRange(this._filter.length, this._filter.length);

    this._rebuildTable(content);
  }

  _rebuildTable(content) {
    content.querySelector("table")?.remove();
    const users = this._sortedFilteredUsers();

    if (!users.length) {
      let empty = content.querySelector(".filter-empty");
      if (!empty) {
        empty = document.createElement("div");
        empty.className = "filter-empty empty";
        content.appendChild(empty);
      }
      empty.textContent = `No users match "${this._filter}".`;
      return;
    }
    content.querySelector(".filter-empty")?.remove();

    const table = document.createElement("table");
    table.innerHTML = `
      <thead>
        <tr>
          ${this._sortHeader("name", "Name")}
          ${this._sortHeader("access", "Access")}
          <th>PIN</th>
          <th>Cards</th>
          <th>Codes</th>
          <th>Valid Until</th>
          ${this._sortHeader("last_access", "Last Used")}
          <th>HA User</th>
          <th title="Notifications" style="text-align:center">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" style="vertical-align:middle">
              <path d="M21,19V20H3V19L5,17V11C5,7.9 7.03,5.17 10,4.29C10,4.19 10,4.1 10,4A2,2 0 0,1 12,2A2,2 0 0,1 14,4C14,4.1 14,4.19 14,4.29C16.97,5.17 19,7.9 19,11V17L21,19M14,21A2,2 0 0,1 12,23A2,2 0 0,1 10,21"/>
            </svg>
          </th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${users.map(u => {
          const hasTargets = (u.notification_targets || []).length > 0;
          const access = this._accessStatus(u);
          return `
          <tr data-uuid="${esc(u.uuid)}">
            <td><strong>${esc(u.name || "—")}</strong></td>
            <td><span class="badge ${access.cls}">${access.label}</span></td>
            <td><span class="badge ${u.pin ? "badge-yes" : "badge-no"}">${u.pin ? "Set" : "None"}</span></td>
            <td>${(u.card || []).filter(Boolean).length}</td>
            <td>${(u.code || []).filter(Boolean).length}</td>
            <td>${formatDate(u.validTo)}</td>
            <td style="color:var(--secondary-text-color);font-size:13px">${u.last_access ? formatDateTime(u.last_access) : "—"}</td>
            <td>
              ${u.ha_user_id
                ? `<span class="ha-link"><svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M10,20V14H14V20H19V12H22L12,3L2,12H5V20H10Z"/></svg>${esc(this._haUserName(u.ha_user_id))}</span>`
                : `<span style="color:var(--disabled-color,#bbb)">—</span>`}
            </td>
            <td style="text-align:center">
              <svg viewBox="0 0 24 24" width="16" height="16"
                fill="${hasTargets ? "var(--primary-color)" : "var(--disabled-color,#ccc)"}"
                title="${hasTargets ? esc((u.notification_targets || []).join(", ")) : "No notifications"}">
                <path d="M21,19V20H3V19L5,17V11C5,7.9 7.03,5.17 10,4.29C10,4.19 10,4.1 10,4A2,2 0 0,1 12,2A2,2 0 0,1 14,4C14,4.1 14,4.19 14,4.29C16.97,5.17 19,7.9 19,11V17L21,19M14,21A2,2 0 0,1 12,23A2,2 0 0,1 10,21"/>
              </svg>
            </td>
            <td class="actions">
              ${this._writePermission ? `
              <button class="icon-btn edit-btn" data-uuid="${esc(u.uuid)}" title="Edit">
                <svg viewBox="0 0 24 24"><path d="M20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18,2.9 17.35,2.9 16.96,3.29L15.12,5.12L18.87,8.87M3,17.25V21H6.75L17.81,9.93L14.06,6.18L3,17.25Z"/></svg>
              </button>
              <button class="icon-btn del-btn" data-uuid="${esc(u.uuid)}" title="Delete">
                <svg viewBox="0 0 24 24"><path d="M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z"/></svg>
              </button>` : ``}
            </td>
          </tr>
          `;
        }).join("")}
      </tbody>
    `;
    content.appendChild(table);

    table.querySelectorAll(".sortable").forEach(th => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (this._sortKey === key) {
          this._sortAsc = !this._sortAsc;
        } else {
          this._sortKey = key;
          this._sortAsc = true;
        }
        this._rebuildTable(content);
      });
    });
    table.querySelectorAll(".edit-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const user = this._users.find(u => u.uuid === btn.dataset.uuid);
        if (user) this._openEditDrawer(user);
      });
    });
    table.querySelectorAll(".del-btn").forEach(btn => {
      btn.addEventListener("click", () => this._deleteUser(btn.dataset.uuid));
    });
  }

  _buildUserForm(user = {}) {
    const enabled = user.enabled !== false;
    const form = document.createElement("div");
    form.innerHTML = `
      <div class="field-group">
        <div class="field">
          <label>Name <span class="required">*</span></label>
          <input id="f-name" type="text" value="" placeholder="Jane Doe" required />
        </div>
        <div class="field" style="flex-direction:row;align-items:center;gap:10px">
          <input id="f-enabled" type="checkbox" ${enabled ? "checked" : ""} style="width:16px;height:16px;cursor:pointer" />
          <label for="f-enabled" style="font-size:13px;font-weight:normal;color:var(--primary-text-color);cursor:pointer;margin:0">Account enabled</label>
        </div>
        <div class="section-title">Credentials <span class="optional-hint">(all optional)</span></div>
        <div class="field">
          <label>PIN code</label>
          <input id="f-pin" type="text" value="" placeholder="2–15 digits" autocomplete="off" />
        </div>
        <div class="field">
          <label>RFID card UID (hex)</label>
          <input id="f-card" type="text" value="" placeholder="e.g. 1A2B3C4D" />
        </div>
        <div class="field">
          <label>Switch code</label>
          <input id="f-code" type="text" value="" placeholder="2–15 digits" />
        </div>
        <div class="section-title">Validity</div>
        <div class="field">
          <label>Valid from</label>
          <input id="f-valid-from" type="datetime-local" value="${toDateTimeLocalValue(user.validFrom)}" />
        </div>
        <div class="field">
          <label>Valid until</label>
          <input id="f-valid-to" type="datetime-local" value="${toDateTimeLocalValue(user.validTo)}" />
        </div>
        ${this._haUsers.length ? `
          <div class="section-title">Home Assistant</div>
          <div class="field">
            <label>Link to HA user</label>
            <select id="f-ha-user"></select>
          </div>
        ` : ""}
        ${this._notifyServices.length ? `
          <div class="section-title">Notifications</div>
          <div class="field">
            <label>Notify when this user opens the intercom</label>
            <div id="f-notify-targets" style="display:flex;flex-direction:column;gap:6px;margin-top:4px">
            </div>
          </div>
        ` : ""}
        <div id="form-error"></div>
      </div>
    `;
    // Set credential input values safely via DOM properties (avoids XSS in attribute interpolation)
    form.querySelector("#f-name").value = user.name || "";
    form.querySelector("#f-pin").value = user.pin || "";
    form.querySelector("#f-card").value = (user.card || [])[0] || "";
    form.querySelector("#f-code").value = (user.code || [])[0] || "";
    // Populate HA user select safely (HA usernames are untrusted text)
    const haUserSel = form.querySelector("#f-ha-user");
    if (haUserSel) {
      const none = document.createElement("option");
      none.value = "";
      none.textContent = "\u2014 Not linked \u2014";
      haUserSel.appendChild(none);
      for (const u of this._haUsers) {
        const opt = document.createElement("option");
        opt.value = u.id;
        opt.textContent = u.name;
        if (user.ha_user_id === u.id) opt.selected = true;
        haUserSel.appendChild(opt);
      }
    }
    // Populate notification checkboxes safely
    const notifyContainer = form.querySelector("#f-notify-targets");
    if (notifyContainer) {
      for (const svcName of this._notifyServices) {
        const lbl = document.createElement("label");
        lbl.style.cssText = "display:flex;align-items:center;gap:8px;font-size:13px;font-weight:normal;color:var(--primary-text-color);cursor:pointer";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = svcName;
        cb.style.cssText = "width:16px;height:16px;cursor:pointer";
        if ((user.notification_targets || []).includes(svcName)) cb.checked = true;
        const span = document.createElement("span");
        span.textContent = svcName.replace(/^notify\./, "");
        lbl.appendChild(cb);
        lbl.appendChild(span);
        notifyContainer.appendChild(lbl);
      }
    }
    return form;
  }

  _openAddDrawer() {
    if (!this._drawer) {
      this._drawer = document.createElement("doorman-drawer");
      this.shadowRoot.appendChild(this._drawer);
    }
    const form = this._buildUserForm();
    this._drawer.open("Add User", form, async () => {
      const name = form.querySelector("#f-name").value.trim();
      if (!name) { const errEl = form.querySelector("#form-error"); errEl.textContent = ""; const errDiv = document.createElement("div"); errDiv.className = "error"; errDiv.textContent = "Name is required."; errEl.appendChild(errDiv); return; }
      const data = { name, enabled: form.querySelector("#f-enabled").checked };
      const pin = form.querySelector("#f-pin").value.trim();
      if (pin) data.pin = pin;
      const card = form.querySelector("#f-card").value.trim();
      if (card) data.card = card;
      const code = form.querySelector("#f-code").value.trim();
      if (code) data.code = code;
      const vf = form.querySelector("#f-valid-from")?.value;
      if (vf) data.valid_from = localDateTimeWithOffset(vf);
      const vt = form.querySelector("#f-valid-to")?.value;
      if (vt) data.valid_to = localDateTimeWithOffset(vt);
      try {
        await svc(this._hass, "create_user", data, this._entryId);
        this._drawer.close();
        this._load();
      } catch (e) {
        const errEl = form.querySelector("#form-error"); errEl.textContent = ""; const errDiv = document.createElement("div"); errDiv.className = "error"; errDiv.textContent = e.message; errEl.appendChild(errDiv);
      }
    });
  }

  _openEditDrawer(user) {
    if (!this._drawer) {
      this._drawer = document.createElement("doorman-drawer");
      this.shadowRoot.appendChild(this._drawer);
    }
    const form = this._buildUserForm(user);
    this._drawer.open(`Edit: ${user.name || user.uuid}`, form, async () => {
      const data = { uuid: user.uuid };
      const name = form.querySelector("#f-name").value.trim();
      if (!name) { const errEl = form.querySelector("#form-error"); errEl.textContent = ""; const errDiv = document.createElement("div"); errDiv.className = "error"; errDiv.textContent = "Name is required."; errEl.appendChild(errDiv); return; }
      data.name = name; // always required by 2N API
      data.enabled = form.querySelector("#f-enabled").checked;
      const pin = form.querySelector("#f-pin").value.trim();
      // Mirror card/code: send "" to clear a previously set PIN
      if (pin !== (user.pin || "")) data.pin = pin;
      const card = form.querySelector("#f-card").value.trim();
      if (card !== ((user.card || [])[0] || "")) data.card = card;
      const code = form.querySelector("#f-code").value.trim();
      if (code !== ((user.code || [])[0] || "")) data.code = code;
      const vf = form.querySelector("#f-valid-from")?.value;
      const vfCurrent = toDateTimeLocalValue(user.validFrom);
      // 0 clears the validity restriction (undefined would be dropped by JSON)
      if (vf !== vfCurrent) data.valid_from = vf ? localDateTimeWithOffset(vf) : 0;
      const vt = form.querySelector("#f-valid-to")?.value;
      const vtCurrent = toDateTimeLocalValue(user.validTo);
      if (vt !== vtCurrent) data.valid_to = vt ? localDateTimeWithOffset(vt) : 0;
      let updated = false;
      try {
        await svc(this._hass, "update_user", data, this._entryId);
        updated = true;
        // Handle HA user link change
        const haSelect = form.querySelector("#f-ha-user");
        if (haSelect) {
          const newHaId = haSelect.value;
          if (newHaId !== (user.ha_user_id || "")) {
            if (newHaId) {
              await ws(this._hass, "doorman/link_user", { two_n_uuid: user.uuid, ha_user_id: newHaId });
            } else {
              await ws(this._hass, "doorman/unlink_user", { two_n_uuid: user.uuid });
            }
          }
        }
        // Handle notification targets change
        const notifyContainer = form.querySelector("#f-notify-targets");
        if (notifyContainer) {
          const selected = Array.from(notifyContainer.querySelectorAll("input[type=checkbox]:checked"))
            .map(cb => cb.value);
          const current = user.notification_targets || [];
          const changed = selected.length !== current.length || selected.some(s => !current.includes(s));
          if (changed) {
            await ws(this._hass, "doorman/set_notification_targets", { two_n_uuid: user.uuid, targets: selected });
          }
        }
        this._drawer.close();
        this._load();
      } catch (e) {
        if (updated) {
          // Partial failure: the device state already changed, so refresh the
          // table to match. The reload wipes the drawer, so report the
          // follow-up error as a toast instead of in the form.
          this._drawer.close();
          await this._load();
          this._showError(`User saved, but a follow-up update failed: ${e.message}`);
        } else {
          const errEl = form.querySelector("#form-error"); errEl.textContent = ""; const errDiv = document.createElement("div"); errDiv.className = "error"; errDiv.textContent = e.message; errEl.appendChild(errDiv);
        }
      }
    });
  }

  _showError(message) {
    const msg = document.createElement("div");
    msg.className = "error";
    msg.style.cssText = "position:fixed;top:16px;right:16px;z-index:200;padding:12px 16px;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,0.15)";
    msg.textContent = message;
    this.shadowRoot.appendChild(msg);
    setTimeout(() => msg.remove(), 5000);
  }

  async _deleteUser(uuid) {
    const user = this._users.find(u => u.uuid === uuid);
    if (!confirm(`Delete user "${user?.name || uuid}"? This cannot be undone.`)) return;
    try {
      await svc(this._hass, "delete_user", { uuid }, this._entryId);
      this._load();
    } catch (e) {
      this._showError(`Delete failed: ${e.message}`);
    }
  }
}
customElements.define("doorman-users-tab", DoormanUsersTab);


// ─── Access Log Tab ───────────────────────────────────────────────────────────

class DoormanLogTab extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._events = null;
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
        .toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
        .toolbar h2 { margin: 0; font-size: 16px; font-weight: 500; }
        .success { color: #2e7d32; font-weight: 500; }
        .fail    { color: var(--error-color, #f44336); font-weight: 500; }
        .event-type { font-family: monospace; font-size: 12px; background: var(--secondary-background-color, #f5f5f5); padding: 2px 6px; border-radius: 3px; }
      </style>
      <div class="toolbar">
        <h2>Access Log</h2>
        <button class="btn btn-outlined" id="refresh-btn">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z"/></svg>
          Refresh
        </button>
      </div>
      <div id="content"></div>
    `;

    shadow.getElementById("refresh-btn").addEventListener("click", () => this._load());

    const content = shadow.getElementById("content");
    if (this._loading) { content.innerHTML = `<div class="loading">Loading log…</div>`; return; }
    if (this._error)   { content.innerHTML = `<div class="error">${esc(this._error)}</div>`; return; }
    if (!this._events?.length) { content.innerHTML = `<div class="empty">No log events found.</div>`; return; }

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
      content.insertBefore(note, table);
    }
    content.appendChild(table);
  }
}
customElements.define("doorman-log-tab", DoormanLogTab);


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
customElements.define("doorman-device-tab", DoormanDeviceTab);


// ─── Notifications Tab ───────────────────────────────────────────────────────
//
// Per-device push-notification configuration. Two independent flows —
// access events (someone opened the door) and the doorbell — each with
// their own iOS sound / Android channel / (doorbell only) targets.
//
// Sentinel values matching custom_components/doorman/ios_sounds.py:
//   ""            → "use Companion default sound", omit push.sound
//   "__custom__"  → panel-only sentinel meaning "show a text field so
//                   the user can enter a side-loaded filename"

const CUSTOM_SOUND_SENTINEL = "__custom__";

class DoormanNotificationsTab extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._loading = true;
    this._error = null;
    this._entryId = null;
    this._deviceName = "";
    this._settings = null;
    this._catalog = [];
    this._notifyServices = [];
    // Dirty-tracking so the "Save" button reflects unsaved edits without
    // demanding the whole form on every keystroke. _dirtyGen is bumped by
    // every edit; _save() captures it at send time and only clears the dirty
    // flag if no further edit landed while the request was in flight.
    this._dirty = false;
    this._dirtyGen = 0;
    this._saving = false;
    // Auto-dismiss handle for the floating toast so back-to-back toasts
    // don't leave stale text on-screen after the first one's timer fires.
    this._toastTimer = null;
  }

  set hass(h) { this._hass = h; }
  set entryId(id) { this._entryId = id; }
  connectedCallback() { this._load(); }

  async _load() {
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const res = await ws(
        this._hass, "doorman/get_notification_settings", {}, this._entryId,
      );
      // Kept unescaped on purpose: the only consumers are the notify
      // `message` strings in _sendPreview, which are plain text on the
      // phone — esc()-ing here would push "Bob&#39;s Door" to the user.
      // It is device-controlled, so esc() it at any future HTML sink.
      this._deviceName = res.device_name || "";
      this._settings = res.settings || {};
      this._catalog = res.ios_sound_catalog || [];
      this._notifyServices = res.notify_services || [];
      this._dirty = false;
      this._dirtyGen = 0;
    } catch (e) {
      this._error = e.message || "Failed to load notification settings";
    } finally {
      this._loading = false;
      this._render();
    }
  }

  // ── Building blocks ────────────────────────────────────────────────

  _iOSSoundSelect(id, currentValue) {
    // Renders once via innerHTML; populated safely below via DOM APIs.
    // The current value may be a filename that's not in the catalog (e.g.
    // a custom side-loaded sound). We treat that as "Custom".
    const knownFilenames = new Set();
    for (const group of this._catalog) {
      for (const s of group.sounds) knownFilenames.add(s.value);
    }
    const isCustom = currentValue && !knownFilenames.has(currentValue);
    return `
      <select class="ns-select" id="${id}">
        <option value="">Default (Companion app)</option>
        ${this._catalog.map(g => `
          <optgroup label="${esc(g.group)}">
            ${g.sounds.map(s => {
              const selected = s.value === currentValue ? " selected" : "";
              return `<option value="${esc(s.value)}"${selected}>${esc(s.label)}</option>`;
            }).join("")}
          </optgroup>
        `).join("")}
        <option value="${CUSTOM_SOUND_SENTINEL}"${isCustom ? " selected" : ""}>Custom filename…</option>
      </select>
      <input type="text" class="ns-custom" id="${id}-custom"
        placeholder="e.g. my-custom-sound.wav"
        value="${isCustom ? esc(currentValue) : ""}"
        style="${isCustom ? "" : "display:none"}" />
    `;
  }

  _androidChannelSelect(id, currentValue) {
    const isCustom = !!currentValue;
    return `
      <select class="ns-select" id="${id}">
        <option value=""${!isCustom ? " selected" : ""}>Default channel</option>
        <option value="${CUSTOM_SOUND_SENTINEL}"${isCustom ? " selected" : ""}>Other (name a channel)…</option>
      </select>
      <input type="text" class="ns-custom" id="${id}-custom"
        placeholder="e.g. doorbell"
        value="${isCustom ? esc(currentValue) : ""}"
        style="${isCustom ? "" : "display:none"}" />
      <p class="ns-help">
        Android sounds are picked by the user in
        Android&nbsp;Settings → Apps → Home&nbsp;Assistant → Notifications
        after this channel first appears.
      </p>
    `;
  }

  // ── Render ─────────────────────────────────────────────────────────

  _render() {
    if (this._loading) {
      this.shadowRoot.innerHTML = `<style>${BASE_CSS}</style><div class="loading">Loading notification settings…</div>`;
      return;
    }
    if (this._error) {
      this.shadowRoot.innerHTML = `<style>${BASE_CSS}</style><div class="error">${esc(this._error)}</div>`;
      return;
    }

    const s = this._settings;
    this.shadowRoot.innerHTML = `
      <style>
        ${BASE_CSS}
        .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        @media (max-width: 720px) { .cards { grid-template-columns: 1fr; } }
        .card {
          background: var(--card-background-color, white);
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          padding: 20px;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .card h3 {
          margin: 0;
          font-size: 13px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: var(--secondary-text-color);
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .card h3 svg { width: 16px; height: 16px; fill: currentColor; }
        .row { display: flex; flex-direction: column; gap: 6px; }
        .row > label {
          font-size: 12px;
          font-weight: 500;
          color: var(--secondary-text-color);
        }
        .row input[type="text"],
        .ns-select {
          padding: 8px 10px;
          border: 1px solid var(--divider-color, #ccc);
          border-radius: 4px;
          font-size: 14px;
          font-family: inherit;
          background: var(--card-background-color, white);
          color: var(--primary-text-color);
        }
        .ns-custom { margin-top: 6px; }
        .ns-help {
          margin: 4px 0 0;
          font-size: 12px;
          color: var(--secondary-text-color);
          line-height: 1.4;
        }
        .preview-row { display: flex; gap: 8px; align-items: center; }
        .preview-row .btn { padding: 6px 12px; font-size: 13px; }
        .targets {
          display: flex;
          flex-direction: column;
          gap: 4px;
          max-height: 200px;
          overflow-y: auto;
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          padding: 8px;
        }
        .targets label {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 13px;
          padding: 4px 6px;
          border-radius: 4px;
          cursor: pointer;
        }
        .targets label:hover { background: var(--secondary-background-color); }
        .targets .empty {
          font-size: 12px;
          color: var(--secondary-text-color);
          font-style: italic;
          padding: 4px;
        }
        .cross-link {
          font-size: 12px;
          color: var(--secondary-text-color);
          background: var(--secondary-background-color);
          border-left: 3px solid var(--primary-color);
          padding: 10px 12px;
          border-radius: 0 4px 4px 0;
          line-height: 1.5;
        }
        .cross-link a { color: var(--primary-color); cursor: pointer; text-decoration: underline; }
        .save-bar {
          margin-top: 20px;
          display: flex;
          justify-content: flex-end;
          gap: 8px;
          align-items: center;
        }
        .save-bar .status {
          font-size: 12px;
          color: var(--secondary-text-color);
          margin-right: auto;
        }
        .save-bar .status.dirty { color: var(--warning-color, #f57f17); font-weight: 500; }
        .preview-popover {
          margin-top: 8px;
          padding: 10px;
          background: var(--secondary-background-color);
          border-radius: 4px;
          display: flex;
          gap: 6px;
          align-items: center;
          flex-wrap: wrap;
        }
        .preview-popover select { flex: 1; min-width: 180px; padding: 6px 8px; font-size: 13px;
          border: 1px solid var(--divider-color); border-radius: 4px;
          background: var(--card-background-color); color: var(--primary-text-color); }
        .toast {
          position: fixed;
          bottom: 20px;
          left: 50%;
          transform: translateX(-50%);
          background: #323232;
          color: white;
          padding: 10px 20px;
          border-radius: 4px;
          font-size: 13px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.3);
          z-index: 100;
        }
        .toast.error { background: var(--error-color, #f44336); }
      </style>

      <div class="cards">

        <div class="card" data-flow="doorbell">
          <h3>
            <svg viewBox="0 0 24 24"><path d="M12,4A6,6 0 0,0 6,10V15L4,17V18H20V17L18,15V10A6,6 0 0,0 12,4M12,2A1,1 0 0,1 13,3V4.5A1,1 0 0,1 12,5.5A1,1 0 0,1 11,4.5V3A1,1 0 0,1 12,2M10.5,20H13.5A1.5,1.5 0 0,1 12,21.5A1.5,1.5 0 0,1 10.5,20Z"/></svg>
            Doorbell Notifications
          </h3>

          <div class="row">
            <label for="db-key">Doorbell key code</label>
            <input type="text" id="db-key" value="${esc(s.doorbell_key_code || "%1")}" />
            <p class="ns-help">
              2N reports this value in <code>KeyPressed.params.key</code>
              when the quick-dial / call button is pressed. Defaults to
              <code>%1</code> for the Verso; use <code>%2</code>, <code>%3</code>,
              &hellip; for additional buttons. Leave it empty if this device has
              no doorbell button — plain keypad digits are rejected, because
              accepting one would ring the doorbell on every PIN keystroke.
            </p>
          </div>

          <div class="row">
            <label>Send doorbell notifications to</label>
            <div class="targets" id="db-targets">
              ${this._notifyServices.length === 0 ? `
                <span class="empty">No notify.* services are registered. Install the Home Assistant Companion app on a phone to add one.</span>
              ` : this._notifyServices.map(svcName => {
                const checked = (s.doorbell_targets || []).includes(svcName) ? " checked" : "";
                return `<label><input type="checkbox" value="${esc(svcName)}"${checked}> ${esc(svcName)}</label>`;
              }).join("")}
            </div>
          </div>

          <div class="row">
            <label for="db-ios">iOS sound</label>
            ${this._iOSSoundSelect("db-ios", s.doorbell_sound_ios || "")}
          </div>

          <div class="row">
            <label for="db-android">Android channel</label>
            ${this._androidChannelSelect("db-android", s.doorbell_channel_android || "")}
          </div>

          <div class="row">
            <div class="preview-row">
              <button class="btn btn-outlined" data-preview="doorbell">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M14,3.23V5.29C16.89,6.15 19,8.83 19,12C19,15.17 16.89,17.84 14,18.7V20.77C18,19.86 21,16.28 21,12C21,7.72 18,4.14 14,3.23M16.5,12C16.5,10.23 15.5,8.71 14,7.97V16C15.5,15.29 16.5,13.76 16.5,12M3,9V15H7L12,20V4L7,9H3Z"/></svg>
                Preview
              </button>
            </div>
            <div id="db-preview"></div>
          </div>
        </div>

        <div class="card" data-flow="access">
          <h3>
            <svg viewBox="0 0 24 24"><path d="M18,8H17V6A5,5 0 0,0 12,1A5,5 0 0,0 7,6V8H6A2,2 0 0,0 4,10V20A2,2 0 0,0 6,22H18A2,2 0 0,0 20,20V10A2,2 0 0,0 18,8M12,17A2,2 0 0,1 10,15A2,2 0 0,1 12,13A2,2 0 0,1 14,15A2,2 0 0,1 12,17M15.1,8H8.9V6A3.1,3.1 0 0,1 12,2.9A3.1,3.1 0 0,1 15.1,6V8Z"/></svg>
            Access notifications
          </h3>

          <div class="cross-link">
            <strong>Per-user targets</strong> are configured on the
            <a data-jump="users">Users tab</a> — pick which phones get pinged
            for each 2N directory user.
          </div>

          <div class="row">
            <label for="ac-ios">iOS sound</label>
            ${this._iOSSoundSelect("ac-ios", s.access_sound_ios || "")}
          </div>

          <div class="row">
            <label for="ac-android">Android channel</label>
            ${this._androidChannelSelect("ac-android", s.access_channel_android || "")}
          </div>

          <div class="row">
            <div class="preview-row">
              <button class="btn btn-outlined" data-preview="access">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M14,3.23V5.29C16.89,6.15 19,8.83 19,12C19,15.17 16.89,17.84 14,18.7V20.77C18,19.86 21,16.28 21,12C21,7.72 18,4.14 14,3.23M16.5,12C16.5,10.23 15.5,8.71 14,7.97V16C15.5,15.29 16.5,13.76 16.5,12M3,9V15H7L12,20V4L7,9H3Z"/></svg>
                Preview
              </button>
            </div>
            <div id="ac-preview"></div>
          </div>
        </div>

      </div>

      <div class="save-bar">
        <span class="status${this._dirty ? " dirty" : ""}">
          ${this._dirty ? "Unsaved changes" : "All changes saved"}
        </span>
        <button class="btn btn-primary" id="save-btn" ${this._saving ? "disabled" : ""}>
          ${this._saving ? "Saving…" : "Save changes"}
        </button>
      </div>

    `;

    this._wireEvents();
  }

  // Update the "Save" button + status text in place without touching the
  // form. Re-rendering the whole tab (as _render does) would wipe unsaved
  // edits like "user just picked a sound and pressed Preview".
  _refreshSaveBar() {
    const status = this.shadowRoot.querySelector(".status");
    const btn = this.shadowRoot.getElementById("save-btn");
    if (status) {
      status.textContent = this._saving
        ? "Saving…"
        : this._dirty ? "Unsaved changes" : "All changes saved";
      status.classList.toggle("dirty", this._dirty && !this._saving);
    }
    if (btn) {
      btn.textContent = this._saving ? "Saving…" : "Save changes";
      btn.disabled = !!this._saving;
    }
  }

  // ── Event wiring ───────────────────────────────────────────────────

  _wireEvents() {
    const root = this.shadowRoot;
    // Any input change marks dirty
    root.querySelectorAll("input, select").forEach(el => {
      el.addEventListener("input", () => this._markDirty());
      el.addEventListener("change", () => this._markDirty());
    });

    // Show/hide custom-filename text field when the sound select changes
    for (const id of ["db-ios", "ac-ios", "db-android", "ac-android"]) {
      const sel = root.getElementById(id);
      const custom = root.getElementById(`${id}-custom`);
      if (!sel || !custom) continue;
      sel.addEventListener("change", () => {
        custom.style.display = sel.value === CUSTOM_SOUND_SENTINEL ? "" : "none";
        if (sel.value === CUSTOM_SOUND_SENTINEL) custom.focus();
      });
    }

    // Preview buttons
    root.querySelectorAll("[data-preview]").forEach(btn => {
      btn.addEventListener("click", () => this._showPreview(btn.dataset.preview));
    });

    // Save
    root.getElementById("save-btn")?.addEventListener("click", () => this._save());

    // Jump to Users tab
    root.querySelectorAll("[data-jump]").forEach(a => {
      a.addEventListener("click", () => {
        this.dispatchEvent(new CustomEvent("doorman-switch-tab", {
          detail: { tab: a.dataset.jump },
          bubbles: true, composed: true,
        }));
      });
    });
  }

  _markDirty() {
    // Bump the generation on *every* edit, even when already dirty: _save()
    // compares against it to tell "the user edited during the round-trip"
    // from "nothing changed since we sent".
    this._dirtyGen++;
    if (this._dirty) return;
    this._dirty = true;
    const status = this.shadowRoot.querySelector(".status");
    if (status) {
      status.textContent = "Unsaved changes";
      status.classList.add("dirty");
    }
  }

  // ── Preview popover ────────────────────────────────────────────────

  _showPreview(flow) {
    const root = this.shadowRoot;
    const container = root.getElementById(`${flow === "doorbell" ? "db" : "ac"}-preview`);
    if (!container) return;
    if (container.dataset.open === "1") {
      container.innerHTML = "";
      container.dataset.open = "0";
      return;
    }
    container.dataset.open = "1";

    const targets = this._targetsForPreview(flow);
    const defaultTarget = targets[0] || "";
    container.innerHTML = `
      <div class="preview-popover">
        <select id="preview-target">
          ${targets.length === 0 ? `<option value="">(no notify targets available)</option>` :
            targets.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join("")}
        </select>
        <button class="btn btn-primary" id="preview-send" ${targets.length === 0 ? "disabled" : ""}>Send</button>
        <button class="btn btn-outlined" id="preview-cancel">Cancel</button>
      </div>
    `;
    container.querySelector("#preview-cancel").addEventListener("click", () => {
      container.innerHTML = "";
      container.dataset.open = "0";
    });
    container.querySelector("#preview-send").addEventListener("click", async () => {
      const target = container.querySelector("#preview-target").value;
      if (!target) return;
      // Stay open after Send so the user can hear the result and quickly
      // pick a different target / try again. Only Cancel closes it.
      await this._sendPreview(flow, target);
    });
  }

  _targetsForPreview(flow) {
    if (flow === "doorbell") {
      const checked = this._readCheckedTargets();
      return checked.length ? checked : this._notifyServices;
    }
    // access preview: no per-user context here, so allow any service
    return this._notifyServices;
  }

  _readCheckedTargets() {
    return Array.from(
      this.shadowRoot.querySelectorAll("#db-targets input[type=checkbox]:checked")
    ).map(c => c.value);
  }

  _readSoundValue(prefix) {
    const sel = this.shadowRoot.getElementById(`${prefix}-ios`);
    const custom = this.shadowRoot.getElementById(`${prefix}-ios-custom`);
    if (!sel) return "";
    if (sel.value === CUSTOM_SOUND_SENTINEL) return custom?.value.trim() || "";
    return sel.value;
  }

  _readChannelValue(prefix) {
    const sel = this.shadowRoot.getElementById(`${prefix}-android`);
    const custom = this.shadowRoot.getElementById(`${prefix}-android-custom`);
    if (!sel) return "";
    if (sel.value === CUSTOM_SOUND_SENTINEL) return custom?.value.trim() || "";
    return "";
  }

  async _sendPreview(flow, target) {
    const soundPrefix = flow === "doorbell" ? "db" : "ac";
    const ios = this._readSoundValue(soundPrefix);
    const channel = this._readChannelValue(soundPrefix);
    const title = flow === "doorbell" ? "Doorbell" : "Doorman";
    const message = flow === "doorbell"
      ? `${this._deviceName || "Test"}: someone rang the doorbell`
      : `Test — someone opened ${this._deviceName || "the door"}`;
    try {
      await this._hass.callWS({
        type: "doorman/send_test_notification",
        target, title, message,
        ios_sound: ios,
        android_channel: channel,
      });
      this._showToast("Test notification sent");
    } catch (e) {
      this._showToast(`Preview failed: ${e.message || e}`, true);
    }
  }

  // ── Save ───────────────────────────────────────────────────────────

  async _save() {
    if (this._saving) return;
    this._saving = true;
    this._refreshSaveBar();
    // Snapshot the edit generation that this request covers. The form inputs
    // stay enabled during the round-trip (disabling them mid-save would be
    // worse UX), so the user can change a value after we've read it — those
    // edits are NOT in this payload and must stay marked unsaved.
    const sentGen = this._dirtyGen;
    try {
      const settings = {
        // Sent verbatim: "" is a meaningful value (no doorbell button on this
        // device), so don't silently substitute the default for an empty field.
        doorbell_key_code: this.shadowRoot.getElementById("db-key")?.value.trim() ?? "",
        doorbell_targets: this._readCheckedTargets(),
        doorbell_sound_ios: this._readSoundValue("db"),
        doorbell_channel_android: this._readChannelValue("db"),
        access_sound_ios: this._readSoundValue("ac"),
        access_channel_android: this._readChannelValue("ac"),
      };
      const res = await ws(
        this._hass, "doorman/set_notification_settings",
        { settings }, this._entryId,
      );
      // Keep internal state in sync with the persisted settings — but do
      // NOT re-render the form. The DOM already reflects what the user
      // chose; rebuilding it from _settings would wipe any subsequent
      // edits and (per the reported bug) revert selects if the response
      // happened to be missing a field.
      this._settings = res.settings || settings;
      // Only the edits we actually sent are clean. If the user changed
      // something while the request was in flight, stay dirty — otherwise the
      // bar would claim "All changes saved" for a value that was never
      // transmitted.
      if (this._dirtyGen === sentGen) this._dirty = false;
      this._showToast(
        this._dirty ? "Saved — you have newer unsaved changes" : "Saved",
      );
    } catch (e) {
      this._showToast(`Save failed: ${e.message || e}`, true);
    } finally {
      this._saving = false;
      this._refreshSaveBar();
    }
  }

  // Floating toast — an appended element that lives outside the form
  // cards, so showing/dismissing it never touches user inputs. The old
  // implementation put the toast inside the main render and called
  // _render() on show/hide, which wiped unsaved edits every time.
  _showToast(text, error = false) {
    if (this._toastTimer) {
      clearTimeout(this._toastTimer);
      this._toastTimer = null;
    }
    let toast = this.shadowRoot.querySelector(".toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "toast";
      this.shadowRoot.appendChild(toast);
    }
    toast.classList.toggle("error", !!error);
    toast.textContent = text;
    this._toastTimer = setTimeout(() => {
      toast.remove();
      this._toastTimer = null;
    }, error ? 4000 : 2000);
  }
}
customElements.define("doorman-notifications-tab", DoormanNotificationsTab);


// ─── Main Panel ───────────────────────────────────────────────────────────────

class DoormanPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "users";
    this._devices = [];
    this._selectedEntryId = null;

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

  set panel(p) { this._panel = p; }
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
      }
      this._renderShell();
    } catch (e) {
      this._devices = [];
      this._loadError = e.message || "Could not connect to Doorman";
      this._renderShell();
    }
  }

  connectedCallback() { this._renderShell(); }

  _renderShell() {
    const tabs = [
      { id: "users",         label: "Users" },
      { id: "log",           label: "Access Log" },
      { id: "notifications", label: "Notifications" },
      { id: "device",        label: "Device" },
    ];

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
        .menu-btn svg { width: 24px; height: 24px; fill: currentColor; display: block; }
        .tabs {
          display: flex;
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
          border-bottom: 2px solid transparent;
          margin-bottom: -1px;
          user-select: none;
          letter-spacing: 0.25px;
          transition: color 0.15s;
        }
        .tab.active { color: var(--primary-color); border-bottom-color: var(--primary-color); }
        .tab:hover:not(.active) { color: var(--primary-text-color); }
        .content { padding: 20px; max-width: 960px; margin: 0 auto; }
      </style>
      <div class="header">
        ${this._narrow ? `
          <button class="menu-btn" id="menu-btn">
            <svg viewBox="0 0 24 24"><path d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z"/></svg>
          </button>
        ` : ""}
        <svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" style="opacity:0.9">
          <path d="M18,8H17V6A5,5 0 0,0 12,1A5,5 0 0,0 7,6V8H6A2,2 0 0,0 4,10V20A2,2 0 0,0 6,22H18A2,2 0 0,0 20,20V10A2,2 0 0,0 18,8M12,17A2,2 0 0,1 10,15A2,2 0 0,1 12,13A2,2 0 0,1 14,15A2,2 0 0,1 12,17M15.1,8H8.9V6A3.1,3.1 0 0,1 12,2.9A3.1,3.1 0 0,1 15.1,6V8Z"/>
        </svg>
        <h1>Doorman</h1>
        ${this._devices.length > 1 ? `<select class="device-select" id="device-select"></select>` : ""}
      </div>
      <div class="tabs">
        ${tabs.map(t => `<div class="tab${this._tab === t.id ? " active" : ""}" data-tab="${t.id}">${t.label}</div>`).join("")}
      </div>
      <div class="content">
        ${this._loadError ? `
          <div style="display:flex;align-items:flex-start;gap:10px;padding:12px 16px;background:#fff3f3;border:1px solid #ffcdd2;border-radius:6px;color:#c62828;font-size:13px;margin-bottom:16px;line-height:1.5">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="#c62828" style="flex-shrink:0;margin-top:1px"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            <span><strong>Doorman is unavailable</strong> — ${esc(this._loadError)}</span>
          </div>` : ""}
        <div id="tab-content"></div>
      </div>
    `;

    this.shadowRoot.getElementById("menu-btn")?.addEventListener("click", () => {
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    });
    this.shadowRoot.querySelectorAll(".tab").forEach(el => {
      el.addEventListener("click", () => {
        this._tab = el.dataset.tab;
        this._renderShell();
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
    container.innerHTML = "";
    const tagMap = {
      users:         "doorman-users-tab",
      log:           "doorman-log-tab",
      notifications: "doorman-notifications-tab",
      device:        "doorman-device-tab",
    };
    const el = document.createElement(tagMap[this._tab]);
    if (this._hass) el.hass = this._hass;
    if (this._selectedEntryId) el.entryId = this._selectedEntryId;
    container.appendChild(el);
  }
}
customElements.define("doorman-panel", DoormanPanel);
