"""Lifespan-level robustness: degraded startup, CRUD re-subscribe, light route."""
from __future__ import annotations

import json


def _requests(record_file) -> list[dict]:
    if not record_file.exists():
        return []
    return [json.loads(line) for line in record_file.read_text().splitlines()]


def test_cloud_startup_failure_degrades_to_lan(cloud_app_factory):
    """A CDN outage on first boot must not take down the whole gateway —
    LAN printing and the web UI keep working, cloud endpoints return 503."""
    with cloud_app_factory(
        printers=[{"serial": "DEVA", "ip": "10.0.0.5"}],
        download_error=RuntimeError("CDN unreachable"),
    ) as client:
        assert client.get("/api/health").status_code == 200
        # Cloud-specific endpoints degrade cleanly.
        assert client.get("/api/cloud/auth/status").status_code == 503
        # The printer is still served — via the LAN fallback.
        resp = client.get("/api/printers")
        assert resp.status_code == 200
        assert [p["id"] for p in resp.json()["printers"]] == ["DEVA"]


def test_printer_crud_resubscribes_cloud(cloud_app_factory):
    """A printer added via the settings API must start reporting without a
    gateway restart: registered as a cloud client AND subscribed."""
    with cloud_app_factory(
        printers=[{"serial": "DEVA", "ip": "10.0.0.5"}],
        fake_env={"FAKE_HOST_USER_LOGGED_IN": "1"},
    ) as client:
        before = len(_requests(client.rpc_record_file))
        resp = client.post("/api/settings/printers", json={
            "serial": "DEVB", "ip": "10.0.0.6", "access_code": "ac2",
            "name": "Second", "machine_model": "",
        })
        assert resp.status_code == 201, resp.text

        # Registered in the cloud registry and listed exactly once.
        listed = client.get("/api/printers").json()["printers"]
        assert sorted(p["id"] for p in listed) == ["DEVA", "DEVB"]

        # And subscribed with the plugin so its reports start flowing.
        new_reqs = _requests(client.rpc_record_file)[before:]
        subs = [r for r in new_reqs if r["method"] == "add_subscribe"]
        assert subs, "no add_subscribe issued after printer CRUD"
        assert "DEVB" in subs[-1]["params"]["dev_ids"]


def test_light_route_dispatches_to_cloud(cloud_app_factory):
    """/light was the one control endpoint left behind on the LAN-only
    helper — it 404'd for cloud printers while pause/resume/etc. worked."""
    with cloud_app_factory(
        printers=[{"serial": "DEV1", "ip": "10.0.0.9"}],
    ) as client:
        resp = client.post(
            "/api/printers/DEV1/light", json={"on": True},
        )
        assert resp.status_code == 200, resp.text

        sends = [
            r for r in _requests(client.rpc_record_file)
            if r["method"] == "send_message"
        ]
        assert sends, "light command never reached the cloud relay"
        payload = json.loads(sends[-1]["params"]["payload"])
        assert payload["system"]["command"] == "ledctrl"
        assert payload["system"]["led_mode"] == "on"
