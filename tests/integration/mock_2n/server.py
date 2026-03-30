"""Mock 2N IP intercom HTTP API server for integration testing.

Implements the full /api/* surface used by Doorman. Maintains in-memory
state so tests can verify side-effects (user created, relay toggled, etc.)
via the /admin/* endpoints.
"""
from __future__ import annotations

import copy
import os
import uuid as _uuid

from aiohttp import web

# ─── Device identity (configurable via env for multi-device testing) ─────────

_DEVICE_NAME = os.environ.get("DEVICE_NAME", "2N IP Verso (Test)")
_SERIAL_NUMBER = os.environ.get("SERIAL_NUMBER", "10-99999999")
_HW_VERSION = os.environ.get("HW_VERSION", "535v1")
_INITIAL_USER = os.environ.get("INITIAL_USER_NAME", "Test User")
_INITIAL_UUID = os.environ.get("INITIAL_USER_UUID", "uuid-test-01")

# ─── Mutable device state ────────────────────────────────────────────────────

def _default_state() -> dict:
    return {
        "device_info": {
            "deviceName": _DEVICE_NAME,
            "swVersion": "2.49.0.38",
            "serialNumber": _SERIAL_NUMBER,
            "hwVersion": _HW_VERSION,
        },
        "users": [
            {
                "uuid": _INITIAL_UUID,
                "name": _INITIAL_USER,
                "pin": "1234",
                "card": ["AABBCCDD"],
                "code": [],
                "validFrom": None,
                "validTo": None,
            }
        ],
        "switches": [
            {"id": 1, "name": "Main Door", "active": False},
        ],
        "call_log": [],
    }

_state: dict = _default_state()


def _log(method: str, path: str, body=None) -> None:
    _state["call_log"].append({"method": method, "path": path, "body": body})


# ─── 2N API endpoints ────────────────────────────────────────────────────────

async def get_system_info(request: web.Request) -> web.Response:
    _log("GET", "/api/system/info")
    return web.json_response({"success": True, "result": _state["device_info"]})


async def get_switch_status(request: web.Request) -> web.Response:
    _log("GET", "/api/switch/status")
    return web.json_response({"success": True, "result": {"switches": _state["switches"]}})


async def get_switch_caps(request: web.Request) -> web.Response:
    return web.json_response({"success": True, "result": {"switches": _state["switches"]}})


async def ctrl_switch(request: web.Request) -> web.Response:
    switch_id = int(request.rel_url.query.get("switch", 1))
    action = request.rel_url.query.get("action", "trigger")
    _log("GET", "/api/switch/ctrl", {"switch": switch_id, "action": action})
    for sw in _state["switches"]:
        if sw["id"] == switch_id:
            sw["active"] = action == "on"
    return web.json_response({"success": True})


async def query_dir(request: web.Request) -> web.Response:
    _log("POST", "/api/dir/query")
    return web.json_response({"success": True, "result": {"users": _state["users"]}})


async def get_dir_template(request: web.Request) -> web.Response:
    return web.json_response({"success": True, "result": {}})


async def create_dir(request: web.Request) -> web.Response:
    body = await request.json()
    # Accept both {"users": [...]} (v3) and {"user": {...}} (v2) formats
    users_in = body.get("users", [body.get("user", {})])
    created = []
    for u in users_in:
        user = copy.deepcopy(u)
        user.setdefault("uuid", str(_uuid.uuid4()))
        _state["users"].append(user)
        created.append({"uuid": user["uuid"], "timestamp": 1})
    _log("PUT", "/api/dir/create", body)
    return web.json_response({"success": True, "result": {"users": created}})


async def update_dir(request: web.Request) -> web.Response:
    body = await request.json()
    # Accept both {"users": [...]} and {"user": {...}} formats
    users_in = body.get("users", [body.get("user", {})])
    for user_data in users_in:
        target_uuid = user_data.get("uuid")
        for i, u in enumerate(_state["users"]):
            if u["uuid"] == target_uuid:
                _state["users"][i] = {**u, **{k: v for k, v in user_data.items() if k != "uuid"}}
    _log("PUT", "/api/dir/update", body)
    return web.json_response({"success": True})


async def delete_dir(request: web.Request) -> web.Response:
    body = await request.json()
    # Accept both {"users": [{"uuid": ...}]} and {"uuid": ...} formats
    users_in = body.get("users", [{"uuid": body.get("uuid")}])
    for u in users_in:
        target_uuid = u.get("uuid")
        _state["users"] = [x for x in _state["users"] if x["uuid"] != target_uuid]
    _log("PUT", "/api/dir/delete", body)
    return web.json_response({"success": True})


async def subscribe_log(request: web.Request) -> web.Response:
    return web.json_response({"success": True, "result": {"id": 1}})


async def pull_log(request: web.Request) -> web.Response:
    _log("GET", "/api/log/pull")
    return web.json_response({"success": True, "result": {"events": []}})


async def grant_access(request: web.Request) -> web.Response:
    access_point_id = request.rel_url.query.get("id", "1")
    _log("GET", "/api/accesspoint/grantaccess", {"id": access_point_id})
    return web.json_response({"success": True})


# ─── Admin endpoints (for test assertions) ──────────────────────────────────

async def admin_get_calls(request: web.Request) -> web.Response:
    """Return the log of all API calls received since last reset."""
    return web.json_response({"calls": _state["call_log"]})


async def admin_reset(request: web.Request) -> web.Response:
    """Reset call log and restore initial device state."""
    fresh = _default_state()
    _state["call_log"] = fresh["call_log"]
    _state["users"] = fresh["users"]
    _state["switches"] = fresh["switches"]
    # Keep device_info unchanged (set at startup from env vars)
    return web.json_response({"ok": True})


async def admin_get_users(request: web.Request) -> web.Response:
    """Return current in-memory user list."""
    return web.json_response({"users": _state["users"]})


async def admin_get_switches(request: web.Request) -> web.Response:
    return web.json_response({"switches": _state["switches"]})


# ─── App assembly ────────────────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/system/info", get_system_info)
    app.router.add_get("/api/switch/status", get_switch_status)
    app.router.add_get("/api/switch/caps", get_switch_caps)
    app.router.add_get("/api/switch/ctrl", ctrl_switch)
    app.router.add_get("/api/dir/template", get_dir_template)
    app.router.add_post("/api/dir/query", query_dir)
    app.router.add_put("/api/dir/create", create_dir)
    app.router.add_put("/api/dir/update", update_dir)
    app.router.add_put("/api/dir/delete", delete_dir)
    app.router.add_get("/api/log/subscribe", subscribe_log)
    app.router.add_get("/api/log/pull", pull_log)
    app.router.add_get("/api/accesspoint/grantaccess", grant_access)
    # Admin
    app.router.add_get("/admin/calls", admin_get_calls)
    app.router.add_post("/admin/reset", admin_reset)
    app.router.add_get("/admin/users", admin_get_users)
    app.router.add_get("/admin/switches", admin_get_switches)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8888)
