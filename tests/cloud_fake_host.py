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
    if method == "connect_server":
        return {"rc": 0}
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
        # By default, push a happy-path event sequence so the orchestrator's
        # `await` for terminal events completes promptly.
        if os.environ.get("FAKE_HOST_PRINT_SCRIPT") == "happy":
            for stage in (0, 1, 2, 3, 6):  # Create, Upload, Waiting, Sending, Finished
                _PENDING_EVENTS.append({
                    "kind": "OnUpdateStatus", "stage": stage, "code": 0, "msg": ""
                })
        return {"rc": 0}
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
