/**
 * Shared helpers for the Doorman frontend panel.
 * Vanilla JS ES modules — no build step required.
 */

// The version of the integration this file shipped with. Compared against the
// version the backend reports in panel.config (see __init__.py) to detect a
// browser tab still running frontend code from before a HACS update. Keep it
// in step with custom_components/doorman/manifest.json — a unit test asserts
// the two match.
export const PANEL_VERSION = "0.7.0";

// ─── Helpers ────────────────────────────────────────────────────────────────

// panel.js is served with a ?v=<version> cache-buster and HA re-imports module
// panels per URL, so after a HACS update (or an HA restart from the UI, which
// the browser survives over its WebSocket connection without a page reload)
// this module is re-executed in a document where the previous version's
// elements are already defined. An unguarded customElements.define() throws
// NotSupportedError there and the panel fails to render at all. Guarding turns
// that hard failure into a soft one: the previously defined classes keep
// running — one page reload is still needed to actually see the new code, and
// the banner in DoormanPanel says so.
export function define(name, cls) {
  if (!customElements.get(name)) customElements.define(name, cls);
}

export const ws = (hass, type, params = {}, entryId = null) => {
  const msg = { type, ...params };
  if (entryId) msg.entry_id = entryId;
  return hass.callWS(msg);
};
export const svc = (hass, service, data = {}, entryId = null) => {
  const d = { ...data };
  if (entryId) d.device = entryId;
  return hass.callService("doorman", service, d);
};

// Escape untrusted text before interpolating into innerHTML. All strings from
// the 2N device (names, UUIDs, log fields, device info) are device-controlled.
export function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
    // Every interpolation in this file uses double-quoted attributes, so
    // single quotes are not currently an escape, but escaping them keeps
    // esc() safe if a single-quoted attribute is ever introduced.
    .replace(/'/g, "&#39;");
}

export function formatDate(ts) {
  if (!ts) return "Always";
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}

export function formatDateTime(str) {
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

export function toDateTimeLocalValue(unixTs) {
  if (!unixTs) return "";
  const d = new Date(unixTs * 1000);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function localDateTimeWithOffset(localISO) {
  if (!localISO) return localISO;
  const offset = -new Date().getTimezoneOffset(); // minutes ahead of UTC
  const sign = offset >= 0 ? "+" : "-";
  const h = String(Math.floor(Math.abs(offset) / 60)).padStart(2, "0");
  const m = String(Math.abs(offset) % 60).padStart(2, "0");
  return `${localISO}:00${sign}${h}:${m}`;
}

// ─── Shared styles ───────────────────────────────────────────────────────────

export const BASE_CSS = `
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
  .badge-yes    {
    background: color-mix(in srgb, var(--success-color, #4caf50) 16%, var(--card-background-color, white));
    color: var(--success-color, #2e7d32);
  }
  .badge-no     { background: var(--secondary-background-color, #f5f5f5); color: var(--secondary-text-color, #9e9e9e); }
  .badge-active   {
    background: color-mix(in srgb, var(--success-color, #4caf50) 16%, var(--card-background-color, white));
    color: var(--success-color, #2e7d32);
  }
  .badge-inactive {
    background: color-mix(in srgb, var(--error-color, #f44336) 12%, var(--card-background-color, white));
    color: var(--error-color, #c62828);
  }
  .badge-future   {
    background: color-mix(in srgb, var(--warning-color, #ff9800) 16%, var(--card-background-color, white));
    color: var(--warning-color, #f57f17);
  }
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
  .error   {
    padding: 12px 16px;
    color: var(--error-color, #f44336);
    background: color-mix(in srgb, var(--error-color, #f44336) 12%, var(--card-background-color, white));
    border-radius: 4px;
    margin: 8px 0;
  }
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
    border-color: var(--primary-color);
  }
  .field input:focus-visible, .field select:focus-visible,
  .icon-btn:focus-visible, .btn:focus-visible, .close-btn:focus-visible, .menu-btn:focus-visible,
  .tab:focus-visible {
    outline: 2px solid var(--primary-color);
    outline-offset: 2px;
  }
  .table-scroll {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .ha-link { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--secondary-text-color); }
  .actions { display: flex; gap: 4px; justify-content: flex-end; }
`;
