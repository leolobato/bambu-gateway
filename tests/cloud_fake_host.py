#!/usr/bin/env python3
"""Fake bambu_cloud_host that speaks the same JSONL RPC as the C++ binary.

Used by tests so we can exercise PluginHost without needing the real .so or
a built C++ binary. Returns canned successes for `echo`, `init_plugin`, and
`change_user`. Each request can be observed via the FAKE_HOST_RECORD_FILE
env var (a JSONL file the fake writes one line per request to).
"""
from __future__ import annotations

import json
import os
import sys
from collections import deque

_PENDING_EVENTS: deque = deque()


def _record(request: dict) -> None:
    path = os.environ.get("FAKE_HOST_RECORD_FILE")
    if not path:
        return
    with open(path, "a") as fh:
        fh.write(json.dumps(request) + "\n")


def _dispatch(method: str, params: dict) -> dict:
    if method == "echo":
        return params
    if method == "init_plugin":
        return {"bootstrap_rc": 0}
    if method == "change_user":
        if "canonical_login" not in params:
            raise ValueError("change_user requires canonical_login")
        return {"rc": 0}
    if method == "is_user_login":
        return {"is_login": os.environ.get("FAKE_HOST_USER_LOGGED_IN") == "1"}
    if method == "user_logout":
        return {"rc": 0}
    if method == "get_my_token":
        # Pretends to exchange the ticket for canned tokens. Tests can assert
        # against these exact values.
        if "ticket" not in params:
            raise ValueError("get_my_token requires 'ticket'")
        return {
            "access_token": f"at_for_{params['ticket']}",
            "refresh_token": "rt_canned",
            "expires_in": "3600",
            "refresh_expires_in": "86400",
        }
    if method == "get_user_print_info":
        # Pretends to return the account's bound device list. Tests can seed
        # a JSON array via FAKE_HOST_USER_DEVICES; defaults to none.
        return {
            "devices": json.loads(
                os.environ.get("FAKE_HOST_USER_DEVICES", "[]")
            ),
            "http_code": 200,
        }
    if method == "get_subtask_info":
        if "subtask_id" not in params:
            raise ValueError("get_subtask_info requires 'subtask_id'")
        return {
            "subtask_id": params["subtask_id"],
            "task": json.loads(
                os.environ.get("FAKE_HOST_SUBTASK_INFO", "{}")
            ),
            "http_code": 200,
        }
    if method == "set_user_selected_machine":
        if "dev_id" not in params:
            raise ValueError("set_user_selected_machine requires 'dev_id'")
        return {"rc": int(os.environ.get("FAKE_HOST_SELECT_MACHINE_RC", "0"))}
    if method == "connect_server":
        return {"rc": int(os.environ.get("FAKE_HOST_CONNECT_SERVER_RC", "0"))}
    if method == "is_server_connected":
        return {
            "connected": os.environ.get("FAKE_HOST_SERVER_CONNECTED", "1") == "1",
            "available": True,
        }
    if method == "start_subscribe":
        return {"rc": 0}
    if method == "add_subscribe":
        return {"rc": 0}
    if method == "bridge.poll_events":
        events = list(_PENDING_EVENTS)
        _PENDING_EVENTS.clear()
        return {"events": events}
    if method == "_test_push_event":
        _PENDING_EVENTS.append(params)
        return {"queued": True}
    if method == "start_print":
        rc = int(os.environ.get("FAKE_HOST_START_PRINT_RC", "0"))
        if rc != 0:
            return {"rc": rc, "in_flight": False}
        # By default, push a happy-path event sequence so the orchestrator's
        # `await` for terminal events completes promptly.
        if os.environ.get("FAKE_HOST_PRINT_SCRIPT") == "happy":
            for stage in (0, 1, 2, 3, 6):  # Create, Upload, Waiting, Sending, Finished
                _PENDING_EVENTS.append({
                    "kind": "OnUpdateStatus",
                    "dev_id": params.get("dev_id", ""),
                    "stage": stage, "code": 0, "msg": "",
                })
        return {"rc": 0, "in_flight": True}
    if method == "send_message":
        if not all(k in params for k in ("dev_id", "payload")):
            raise ValueError("send_message requires dev_id + payload")
        rc = int(os.environ.get("FAKE_HOST_SEND_MESSAGE_RC", "0"))
        return {"rc": rc}
    if method == "connect_printer":
        if not all(k in params for k in ("dev_id", "dev_ip", "password")):
            raise ValueError("connect_printer requires dev_id + dev_ip + password")
        rc = int(os.environ.get("FAKE_HOST_CONNECT_PRINTER_RC", "0"))
        # Mirror the real plugin's async handshake: on acceptance, emit the
        # OnLocalConnected(status=0) event the EventPump waits on.
        if rc == 0:
            _PENDING_EVENTS.append({
                "kind": "OnLocalConnected",
                "dev_id": params["dev_id"],
                "status": int(os.environ.get("FAKE_HOST_LOCAL_CONNECT_STATUS", "0")),
                "msg": "",
            })
        return {"rc": rc}
    if method == "send_message_to_printer":
        if not all(k in params for k in ("dev_id", "payload")):
            raise ValueError("send_message_to_printer requires dev_id + payload")
        rc = int(os.environ.get("FAKE_HOST_SEND_MESSAGE_RC", "0"))
        return {"rc": rc}
    if method == "send_burst":
        if not all(k in params for k in ("dev_id", "payloads")):
            raise ValueError("send_burst requires dev_id + payloads")
        rc = int(os.environ.get("FAKE_HOST_SEND_MESSAGE_RC", "0"))
        return {"rcs": [rc for _ in params["payloads"]]}
    if method == "disconnect_printer":
        return {"rc": 0}
    if method == "install_device_cert":
        if "dev_id" not in params:
            raise ValueError("install_device_cert requires dev_id")
        return {"dispatched": True}
    if method == "set_extra_http_header":
        return {"rc": int(os.environ.get("FAKE_HOST_SET_HEADER_RC", "0"))}
    if method == "start_discovery":
        return {"ok": True}
    raise ValueError(f"unknown method: {method}")


def main() -> int:
    print("fake bambu_cloud_host: started", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            _record(req)
            rid = req.get("id", -1)
            method = req["method"]
            params = req.get("params", {})
            if method == os.environ.get("FAKE_HOST_HANG_METHOD"):
                continue  # swallow the request — simulates a wedged host
            result = _dispatch(method, params)
            print(json.dumps({"id": rid, "result": result}), flush=True)
        except Exception as exc:
            print(
                json.dumps(
                    {"id": rid if "rid" in locals() else -1,
                     "error": {"code": -1, "message": str(exc)}}
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
