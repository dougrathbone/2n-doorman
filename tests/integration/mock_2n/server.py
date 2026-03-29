"""Mock 2N IP intercom HTTP API server for integration testing.

Implements the full /api/* surface used by Doorman. Maintains in-memory
state so tests can verify side-effects (user created, relay toggled, etc.)
via the /admin/* endpoints.
"""
from __future__ import annotations

import copy
import uuid as _uuid

from aiohttp import web

# ─── Mutable device state ────────────────────────────────────────────────────

_state: dict = {
    "device_info": {
        "deviceName": "2N IP Verso (Test)",
        "swVersion": "2.49.0.38",
        "serialNumber": "10-99999999",
        "hwVersion": "535v1",
    },
    "users": [
        {
            "uuid": "uuid-test-01",
            "name": "Test User",
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
    "call_log": [],  # {"method", "path", "body"}
}


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
    user = copy.deepcopy(body.get("user", {}))
    user.setdefault("uuid", str(_uuid.uuid4()))
    _state["users"].append(user)
    _log("PUT", "/api/dir/create", body)
    return web.json_response({"success": True, "result": user})


async def update_dir(request: web.Request) -> web.Response:
    body = await request.json()
    user_data = body.get("user", {})
    target_uuid = user_data.get("uuid")
    for i, u in enumerate(_state["users"]):
        if u["uuid"] == target_uuid:
            _state["users"][i] = {**u, **{k: v for k, v in user_data.items() if k != "uuid"}}
    _log("PUT", "/api/dir/update", body)
    return web.json_response({"success": True})


async def delete_dir(request: web.Request) -> web.Response:
    body = await request.json()
    target_uuid = body.get("uuid")
    _state["users"] = [u for u in _state["users"] if u["uuid"] != target_uuid]
    _log("PUT", "/api/dir/delete", body)
    return web.json_response({"success": True})


async def pull_log(request: web.Request) -> web.Response:
    _log("GET", "/api/log/pull")
    return web.json_response({"success": True, "result": {"events": []}})


async def grant_access(request: web.Request) -> web.Response:
    access_point_id = request.rel_url.query.get("accessPointId", "1")
    _log("GET", "/api/accesspoint/grantaccess", {"accessPointId": access_point_id})
    return web.json_response({"success": True})


# ─── Admin endpoints (for test assertions) ──────────────────────────────────

async def admin_get_calls(request: web.Request) -> web.Response:
    """Return the log of all API calls received since last reset."""
    return web.json_response({"calls": _state["call_log"]})


async def admin_reset(request: web.Request) -> web.Response:
    """Reset call log and restore initial device state."""
    _state["call_log"].clear()
    _state["users"] = [
        {
            "uuid": "uuid-test-01",
            "name": "Test User",
            "pin": "1234",
            "card": ["AABBCCDD"],
            "code": [],
            "validFrom": None,
            "validTo": None,
        }
    ]
    _state["switches"] = [{"id": 1, "name": "Main Door", "active": False}]
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
