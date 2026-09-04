/**
 * Notification settings tab for the Doorman panel.
 */

import { define, ws, esc, BASE_CSS } from "./helpers.js";

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
      toast.setAttribute("role", "status");
      toast.setAttribute("aria-live", "polite");
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
define("doorman-notifications-tab", DoormanNotificationsTab);
