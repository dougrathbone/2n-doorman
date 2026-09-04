/**
 * Slide-in edit drawer for the Doorman panel.
 */

import { define } from "./helpers.js";

// ─── Drawer (slide-in edit panel) ────────────────────────────────────────────

class DoormanDrawer extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._open = false;
    this._opener = null;
    this._onKeyDown = (ev) => {
      if (ev.key === "Escape" && this._open) {
        ev.stopPropagation();
        this.close();
      }
    };
  }

  connectedCallback() { this._render(); }

  open(title, content, onSave) {
    this._opener = document.activeElement;
    this._title = title;
    this._content = content;
    this._onSave = onSave;
    this._saving = false;
    this._open = true;
    this._render();
    // Focus first focusable after the open paint.
    requestAnimationFrame(() => {
      const root = this.shadowRoot;
      const focusable = root.querySelector(
        "input, select, textarea, button:not([disabled]), [tabindex]:not([tabindex='-1'])"
      );
      (focusable || root.getElementById("close-btn"))?.focus();
    });
  }

  close() {
    this._open = false;
    this._render();
    const opener = this._opener;
    this._opener = null;
    if (opener && typeof opener.focus === "function") {
      try { opener.focus(); } catch (_) { /* detached */ }
    }
  }

  _render() {
    document.removeEventListener("keydown", this._onKeyDown, true);
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
        .close-btn:focus-visible, .btn:focus-visible,
        .field input:focus-visible, .field select:focus-visible {
          outline: 2px solid var(--primary-color);
          outline-offset: 2px;
        }
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
        .field input:focus, .field select:focus { border-color: var(--primary-color); }
        .section-title { font-size: 11px; font-weight: 600; text-transform: uppercase;
          letter-spacing: 0.5px; color: var(--secondary-text-color); margin: 16px 0 8px; }
        .required { color: var(--error-color, #f44336); margin-left: 2px; }
        .optional-hint { font-weight: 400; text-transform: none; font-size: 10px; opacity: 0.7; }
        .error { padding: 10px 12px; color: var(--error-color, #f44336);
          background: color-mix(in srgb, var(--error-color, #f44336) 12%, var(--card-background-color, white));
          border-radius: 4px; font-size: 13px; }
      </style>
      <div class="overlay" ${this._open ? "" : "hidden"}>
        <div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
          <div class="drawer-header">
            <h2 id="drawer-title"></h2>
            <button class="close-btn" id="close-btn" type="button" aria-label="Close">
              <svg viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
            </button>
          </div>
          <div class="drawer-body" id="drawer-body"></div>
          <div class="drawer-footer">
            <button class="btn btn-outlined" id="cancel-btn" type="button">Cancel</button>
            <button class="btn btn-primary" id="save-btn" type="button">Save</button>
          </div>
        </div>
      </div>
    `;
    // Title comes from device-controlled strings (user name/UUID) — set via textContent
    const titleEl = this.shadowRoot.getElementById("drawer-title");
    if (titleEl) titleEl.textContent = this._title || "";
    const drawer = this.shadowRoot.querySelector(".drawer");
    if (drawer) drawer.setAttribute("aria-label", this._title || "Dialog");
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
    if (this._open) {
      document.addEventListener("keydown", this._onKeyDown, true);
    }
  }

  disconnectedCallback() {
    document.removeEventListener("keydown", this._onKeyDown, true);
  }
}
define("doorman-drawer", DoormanDrawer);
