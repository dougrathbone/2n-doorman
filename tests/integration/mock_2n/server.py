"""Mock 2N IP intercom HTTP API server for integration testing.

Implements the full /api/* surface used by Doorman. Maintains in-memory
state so tests can verify side-effects (user created, relay toggled, etc.)
via the /admin/* endpoints.
"""
from __future__ import annotations

import copy
import time
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
    "call_sessions": [
        {"session": 1, "state": "connected", "peer": "sip:test@example.local"},
    ],
    "io": [
        {"port": "input1", "type": "input", "state": 0},
        {"port": "relay1", "type": "output", "state": 0},
    ],
    "call_log": [],  # {"method", "path", "body"}
    # On-box event history, as returned by log/subscribe?include=all
    "log_history": [],
    "log_subscriptions": {},  # {subscription_id: [queued events]}
    "log_subscription_seq": 0,
}


def _initial_log_history() -> list[dict]:
    """Two recent access events, as a real device would have recorded."""
    now = int(time.time())
    return [
        {
            "id": 1,
            "utcTime": now - 3600,
            "upTime": 100,
            "event": "UserAuthenticated",
            "params": {"ap": 0, "session": 1, "name": "Test User", "uuid": "uuid-test-01"},
        },
        {
            "id": 2,
            "utcTime": now - 1800,
            "upTime": 1900,
            "event": "CardEntered",
            "params": {"ap": 0, "uid": "AABBCCDD", "valid": True},
        },
    ]


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
    return web.json_response({"success": True, "result": {
        "users": [{"access": {"accessPoints": [{"enabled": True}, {"enabled": True}]}}]
    }})


async def create_dir(request: web.Request) -> web.Response:
    body = await request.json()
    user = copy.deepcopy((body.get("users") or [{}])[0])
    user.setdefault("uuid", str(_uuid.uuid4()))
    _state["users"].append(user)
    _log("PUT", "/api/dir/create", body)
    return web.json_response({"success": True, "result": {"users": [{"uuid": user["uuid"]}]}})


async def update_dir(request: web.Request) -> web.Response:
    body = await request.json()
    user_data = (body.get("users") or [{}])[0]
    target_uuid = user_data.get("uuid")
    for i, u in enumerate(_state["users"]):
        if u["uuid"] == target_uuid:
            _state["users"][i] = {**u, **{k: v for k, v in user_data.items() if k != "uuid"}}
    _log("PUT", "/api/dir/update", body)
    return web.json_response({"success": True, "result": {"users": [{"uuid": target_uuid}]}})


async def delete_dir(request: web.Request) -> web.Response:
    body = await request.json()
    target_uuid = (body.get("users") or [{}])[0].get("uuid")
    _state["users"] = [u for u in _state["users"] if u["uuid"] != target_uuid]
    _log("PUT", "/api/dir/delete", body)
    return web.json_response({"success": True, "result": {"users": [{"uuid": target_uuid}]}})


async def subscribe_log(request: web.Request) -> web.Response:
    """Create a subscription channel, honouring the ``include`` parameter.

    Mirrors the real device: ``new`` (default) starts with an empty queue,
    ``all`` pre-fills it with the recorded history, and ``-N`` pre-fills it
    with the events from the last N seconds.
    """
    include = request.rel_url.query.get("include", "new")
    _log("GET", "/api/log/subscribe", {"include": include})

    if include == "all":
        queue = list(_state["log_history"])
    elif include.startswith("-") and include[1:].isdigit():
        cutoff = int(time.time()) - int(include[1:])
        queue = [e for e in _state["log_history"] if e.get("utcTime", 0) >= cutoff]
    else:
        queue = []

    _state["log_subscription_seq"] += 1
    sub_id = _state["log_subscription_seq"]
    _state["log_subscriptions"][sub_id] = queue
    return web.json_response({"success": True, "result": {"id": sub_id}})


async def pull_log(request: web.Request) -> web.Response:
    """Drain the subscription's queue (unknown ids get error 12, as on device)."""
    _log("GET", "/api/log/pull")
    sub_id = int(request.rel_url.query.get("id", 0))
    if sub_id not in _state["log_subscriptions"]:
        return web.json_response(
            {"success": False, "error": {"code": 12, "description": "invalid subscription id"}}
        )
    queue = _state["log_subscriptions"][sub_id]
    _state["log_subscriptions"][sub_id] = []
    return web.json_response({"success": True, "result": {"events": queue}})


async def unsubscribe_log(request: web.Request) -> web.Response:
    sub_id = int(request.rel_url.query.get("id", 0))
    _state["log_subscriptions"].pop(sub_id, None)
    _log("GET", "/api/log/unsubscribe", {"id": sub_id})
    return web.json_response({"success": True})


async def grant_access(request: web.Request) -> web.Response:
    access_point_id = request.rel_url.query.get("id", "1")
    _log("GET", "/api/accesspoint/grantaccess", {"id": access_point_id})
    return web.json_response({"success": True})


async def get_call_status(request: web.Request) -> web.Response:
    _log("GET", "/api/call/status")
    return web.json_response({"success": True, "result": {"sessions": _state["call_sessions"]}})


async def hangup_call(request: web.Request) -> web.Response:
    session = int(request.rel_url.query.get("session", 0))
    _log("GET", "/api/call/hangup", {"session": session})
    _state["call_sessions"] = [
        s for s in _state["call_sessions"] if s.get("session") != session
    ]
    return web.json_response({"success": True})


async def answer_call(request: web.Request) -> web.Response:
    session = int(request.rel_url.query.get("session", 0))
    _log("GET", "/api/call/answer", {"session": session})
    for s in _state["call_sessions"]:
        if s.get("session") == session:
            s["state"] = "connected"
    return web.json_response({"success": True})


async def dial_call(request: web.Request) -> web.Response:
    number = request.rel_url.query.get("number")
    users = request.rel_url.query.get("users")
    if number and users:
        return web.json_response({"success": False, "error": {
            "code": 10, "param": "number", "description": "mutually exclusive parameters",
        }})
    session = _state.get("call_session_seq", 100) + 1
    _state["call_session_seq"] = session
    _state["call_sessions"].append({
        "session": session,
        "state": "connecting",
        "direction": "outgoing",
        "peer": number or users,
    })
    _log("GET", "/api/call/dial", {"number": number, "users": users})
    return web.json_response({"success": True, "result": {"session": session}})


# Minimal JPEG payload (SOI/EOI markers) — enough for HA's camera proxy.
_MOCK_JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


async def get_camera_caps(request: web.Request) -> web.Response:
    return web.json_response({"success": True, "result": {
        "jpegResolution": [{"width": 320, "height": 240}, {"width": 640, "height": 480}],
    }})


async def get_camera_snapshot(request: web.Request) -> web.Response:
    _log("GET", "/api/camera/snapshot", dict(request.rel_url.query))
    return web.Response(body=_MOCK_JPEG, content_type="image/jpeg")


async def get_io_caps(request: web.Request) -> web.Response:
    return web.json_response({"success": True, "result": {"ports": [
        {"port": p["port"], "type": p["type"]} for p in _state["io"]
    ]}})


async def get_io_status(request: web.Request) -> web.Response:
    return web.json_response({"success": True, "result": {"ports": [
        {"port": p["port"], "state": p["state"]} for p in _state["io"]
    ]}})


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
    _state["call_sessions"] = [
        {"session": 1, "state": "connected", "peer": "sip:test@example.local"},
    ]
    _state["io"] = [
        {"port": "input1", "type": "input", "state": 0},
        {"port": "relay1", "type": "output", "state": 0},
    ]
    _state["log_history"] = _initial_log_history()
    _state["log_subscriptions"] = {}
    _state["log_subscription_seq"] = 0
    return web.json_response({"ok": True})


async def admin_inject_event(request: web.Request) -> web.Response:
    """Append an event to every active log subscription queue.

    Lets integration tests drive the whole event pipeline (listener → bus →
    entities) as if something happened on the device. The event is also
    applied to mock state so event-driven changes survive polling.
    """
    import time as _time

    body = await request.json()
    event = {
        "id": _state.get("event_seq", 0) + 1,
        "utcTime": int(_time.time()),
        "event": body["event"],
        "params": body.get("params", {}),
    }
    _state["event_seq"] = event["id"]
    for queue in _state["log_subscriptions"].values():
        queue.append(event)

    # Reflect state events in the mock's own state (like a real device)
    if event["event"] == "SwitchStateChanged":
        for sw in _state["switches"]:
            if sw["id"] == event["params"].get("switch"):
                sw["active"] = bool(event["params"].get("state"))
    elif event["event"] in ("InputChanged", "OutputChanged"):
        for p in _state["io"]:
            if p["port"] == event["params"].get("port"):
                p["state"] = int(bool(event["params"].get("state")))
    return web.json_response({"ok": True, "event": event})


async def admin_set_call_sessions(request: web.Request) -> web.Response:
    """Replace the active call sessions (e.g. seed a ringing incoming call)."""
    body = await request.json()
    _state["call_sessions"] = body.get("sessions", [])
    return web.json_response({"ok": True})


async def admin_get_users(request: web.Request) -> web.Response:
    """Return current in-memory user list."""
    return web.json_response({"users": _state["users"]})


async def admin_get_switches(request: web.Request) -> web.Response:
    return web.json_response({"switches": _state["switches"]})


# ─── App assembly ────────────────────────────────────────────────────────────

def create_app() -> web.Application:
    _state["log_history"] = _initial_log_history()
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
    app.router.add_get("/api/log/unsubscribe", unsubscribe_log)
    app.router.add_get("/api/accesspoint/grantaccess", grant_access)
    app.router.add_get("/api/call/status", get_call_status)
    app.router.add_get("/api/call/hangup", hangup_call)
    app.router.add_get("/api/call/answer", answer_call)
    app.router.add_get("/api/call/dial", dial_call)
    app.router.add_get("/api/camera/caps", get_camera_caps)
    app.router.add_get("/api/camera/snapshot", get_camera_snapshot)
    app.router.add_get("/api/io/caps", get_io_caps)
    app.router.add_get("/api/io/status", get_io_status)
    # Admin
    app.router.add_get("/admin/calls", admin_get_calls)
    app.router.add_post("/admin/reset", admin_reset)
    app.router.add_post("/admin/inject_event", admin_inject_event)
    app.router.add_post("/admin/set_call_sessions", admin_set_call_sessions)
    app.router.add_get("/admin/users", admin_get_users)
    app.router.add_get("/admin/switches", admin_get_switches)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8888)
