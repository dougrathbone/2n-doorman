/**
 * Users directory tab for the Doorman panel.
 */

import {
  define, ws, esc, formatDate, formatDateTime,
  toDateTimeLocalValue, localDateTimeWithOffset, BASE_CSS,
} from "./helpers.js";

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
    const hasCredentials = u.has_pin || u.has_card || u.has_code
      || u.pin || (u.card || []).filter(Boolean).length || (u.code || []).filter(Boolean).length;
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
          background: color-mix(in srgb, var(--warning-color, #ff9800) 14%, var(--card-background-color, white));
          border: 1px solid color-mix(in srgb, var(--warning-color, #ff9800) 40%, transparent);
          border-radius: 6px;
          color: var(--primary-text-color); font-size: 13px; margin-bottom: 16px; line-height: 1.5; }
        .perm-warning svg { flex-shrink: 0; margin-top: 1px; fill: var(--warning-color, #f57f17); }
        th.sortable:hover { color: var(--primary-color); }
        th.sort-active { color: var(--primary-color); }
        @media (max-width: 640px) {
          th, td { padding: 8px 8px; font-size: 13px; }
        }
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
        <div class="perm-warning" role="status">
          <svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
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
      content.innerHTML = `<div class="error" role="alert">${esc(this._error)}</div>`;
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
    content.querySelector(".table-scroll")?.remove();
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

    const wrap = document.createElement("div");
    wrap.className = "table-scroll";
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
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" style="vertical-align:middle" aria-hidden="true">
              <path d="M21,19V20H3V19L5,17V11C5,7.9 7.03,5.17 10,4.29C10,4.19 10,4.1 10,0A2,2 0 0,1 12,2A2,2 0 0,1 14,4C14,4.1 14,4.19 14,4.29C16.97,5.17 19,7.9 19,11V17L21,19M14,21A2,2 0 0,1 12,23A2,2 0 0,1 10,21"/>
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
            <td><span class="badge ${u.has_pin ? "badge-yes" : "badge-no"}">${u.has_pin ? "Set" : "None"}</span></td>
            <td>${u.has_card ? (u.card_count || 1) : "None"}</td>
            <td>${u.has_code ? (u.code_count || 1) : "None"}</td>
            <td>${formatDate(u.validTo)}</td>
            <td style="color:var(--secondary-text-color);font-size:13px">${u.last_access ? formatDateTime(u.last_access) : "—"}</td>
            <td>
              ${u.ha_user_id
                ? `<span class="ha-link"><svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M10,20V14H14V20H19V12H22L12,3L2,12H5V20H10Z"/></svg>${esc(this._haUserName(u.ha_user_id))}</span>`
                : `<span style="color:var(--disabled-color,#bbb)">—</span>`}
            </td>
            <td style="text-align:center">
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"
                fill="${hasTargets ? "var(--primary-color)" : "var(--disabled-color,#ccc)"}"
                title="${hasTargets ? esc((u.notification_targets || []).join(", ")) : "No notifications"}">
                <path d="M21,19V20H3V19L5,17V11C5,7.9 7.03,5.17 10,4.29C10,4.19 10,4.1 10,0A2,2 0 0,1 12,2A2,2 0 0,1 14,4C14,4.1 14,4.19 14,4.29C16.97,5.17 19,7.9 19,11V17L21,19M14,21A2,2 0 0,1 12,23A2,2 0 0,1 10,21"/>
              </svg>
            </td>
            <td class="actions">
              ${this._writePermission ? `
              <button class="icon-btn edit-btn" type="button" data-uuid="${esc(u.uuid)}" aria-label="Edit" title="Edit">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18,2.9 17.35,2.9 16.96,3.29L15.12,5.12L18.87,8.87M3,17.25V21H6.75L17.81,9.93L14.06,6.18L3,17.25Z"/></svg>
              </button>
              <button class="icon-btn del-btn" type="button" data-uuid="${esc(u.uuid)}" aria-label="Delete" title="Delete">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z"/></svg>
              </button>` : ``}
            </td>
          </tr>
          `;
        }).join("")}
      </tbody>
    `;
    wrap.appendChild(table);
    content.appendChild(wrap);

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

  _buildUserForm(user = {}, { isEdit = false } = {}) {
    const enabled = user.enabled !== false;
    const pinPh = isEdit
      ? (user.has_pin ? "Leave blank to keep" : "Enter new value to set")
      : "2–15 digits";
    const cardPh = isEdit
      ? (user.has_card ? "Leave blank to keep" : "Enter new value to set")
      : "e.g. 1A2B3C4D";
    const codePh = isEdit
      ? (user.has_code ? "Leave blank to keep" : "Enter new value to set")
      : "2–15 digits";
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
          <input id="f-pin" type="text" value="" placeholder="${esc(pinPh)}" autocomplete="off" />
          ${isEdit && user.has_pin ? `
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;font-weight:normal;color:var(--secondary-text-color);cursor:pointer;margin-top:4px">
            <input id="f-clear-pin" type="checkbox" style="width:14px;height:14px;cursor:pointer" />
            Clear PIN
          </label>` : ""}
        </div>
        <div class="field">
          <label>RFID card UID (hex)</label>
          <input id="f-card" type="text" value="" placeholder="${esc(cardPh)}" />
          ${isEdit && (user.card_count || 0) > 1 ? `
          <div class="cred-warn" style="font-size:12px;color:var(--warning-color,#f57f17);margin-top:4px;line-height:1.4">
            This user has ${user.card_count} cards; saving a new card replaces all cards on the device.
          </div>` : ""}
          ${isEdit && user.has_card ? `
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;font-weight:normal;color:var(--secondary-text-color);cursor:pointer;margin-top:4px">
            <input id="f-clear-card" type="checkbox" style="width:14px;height:14px;cursor:pointer" />
            Clear card${(user.card_count || 0) > 1 ? "s" : ""}
          </label>` : ""}
        </div>
        <div class="field">
          <label>Switch code</label>
          <input id="f-code" type="text" value="" placeholder="${esc(codePh)}" />
          ${isEdit && (user.code_count || 0) > 1 ? `
          <div class="cred-warn" style="font-size:12px;color:var(--warning-color,#f57f17);margin-top:4px;line-height:1.4">
            This user has ${user.code_count} codes; saving a new code replaces all codes on the device.
          </div>` : ""}
          ${isEdit && user.has_code ? `
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;font-weight:normal;color:var(--secondary-text-color);cursor:pointer;margin-top:4px">
            <input id="f-clear-code" type="checkbox" style="width:14px;height:14px;cursor:pointer" />
            Clear code${(user.code_count || 0) > 1 ? "s" : ""}
          </label>` : ""}
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
    // Name only — credentials are never prefilled (secrets are not returned by list_users).
    form.querySelector("#f-name").value = user.name || "";
    // Typing a new value and "Clear" are mutually exclusive.
    const wireClear = (inputId, clearId) => {
      const input = form.querySelector(inputId);
      const clear = form.querySelector(clearId);
      if (!input || !clear) return;
      clear.addEventListener("change", () => {
        if (clear.checked) {
          input.value = "";
          input.disabled = true;
        } else {
          input.disabled = false;
        }
      });
      input.addEventListener("input", () => {
        if (input.value.trim()) clear.checked = false;
      });
    };
    wireClear("#f-pin", "#f-clear-pin");
    wireClear("#f-card", "#f-clear-card");
    wireClear("#f-code", "#f-clear-code");
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
        await ws(this._hass, "doorman/create_user", data, this._entryId);
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
    const form = this._buildUserForm(user, { isEdit: true });
    this._drawer.open(`Edit: ${user.name || user.uuid}`, form, async () => {
      const data = { uuid: user.uuid };
      const name = form.querySelector("#f-name").value.trim();
      if (!name) { const errEl = form.querySelector("#form-error"); errEl.textContent = ""; const errDiv = document.createElement("div"); errDiv.className = "error"; errDiv.textContent = "Name is required."; errEl.appendChild(errDiv); return; }
      data.name = name; // always required by 2N API
      data.enabled = form.querySelector("#f-enabled").checked;
      // Empty credential field = omit (keep existing). Clear checkbox = send "".
      const pin = form.querySelector("#f-pin").value.trim();
      const clearPin = form.querySelector("#f-clear-pin")?.checked;
      if (clearPin) data.pin = "";
      else if (pin) data.pin = pin;
      const card = form.querySelector("#f-card").value.trim();
      const clearCard = form.querySelector("#f-clear-card")?.checked;
      if (clearCard) data.card = "";
      else if (card) data.card = card;
      const code = form.querySelector("#f-code").value.trim();
      const clearCode = form.querySelector("#f-clear-code")?.checked;
      if (clearCode) data.code = "";
      else if (code) data.code = code;
      if (card && (user.card_count || 0) > 1) {
        if (!confirm(`This user has ${user.card_count} cards. Saving a new card replaces all of them on the device. Continue?`)) {
          return;
        }
      }
      if (code && (user.code_count || 0) > 1) {
        if (!confirm(`This user has ${user.code_count} codes. Saving a new code replaces all of them on the device. Continue?`)) {
          return;
        }
      }
      if (clearCard && (user.card_count || 0) > 1) {
        if (!confirm(`Clear all ${user.card_count} cards for this user?`)) {
          return;
        }
      }
      const vf = form.querySelector("#f-valid-from")?.value;
      const vfCurrent = toDateTimeLocalValue(user.validFrom);
      // 0 clears the validity restriction (undefined would be dropped by JSON)
      if (vf !== vfCurrent) data.valid_from = vf ? localDateTimeWithOffset(vf) : 0;
      const vt = form.querySelector("#f-valid-to")?.value;
      const vtCurrent = toDateTimeLocalValue(user.validTo);
      if (vt !== vtCurrent) data.valid_to = vt ? localDateTimeWithOffset(vt) : 0;
      let updated = false;
      try {
        await ws(this._hass, "doorman/update_user", data, this._entryId);
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
    msg.setAttribute("role", "status");
    msg.setAttribute("aria-live", "polite");
    msg.style.cssText = "position:fixed;top:16px;right:16px;z-index:200;padding:12px 16px;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,0.15)";
    msg.textContent = message;
    this.shadowRoot.appendChild(msg);
    setTimeout(() => msg.remove(), 5000);
  }

  async _deleteUser(uuid) {
    const user = this._users.find(u => u.uuid === uuid);
    if (!confirm(`Delete user "${user?.name || uuid}"? This cannot be undone.`)) return;
    try {
      await ws(this._hass, "doorman/delete_user", { uuid }, this._entryId);
      this._load();
    } catch (e) {
      this._showError(`Delete failed: ${e.message}`);
    }
  }
}
define("doorman-users-tab", DoormanUsersTab);
