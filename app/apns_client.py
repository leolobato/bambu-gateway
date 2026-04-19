"""HTTP/2 client for sending APNs pushes (alerts and Live Activity updates)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

_TOKEN_INVALID_REASONS = {
    "Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic",
}


class ApnsPushType(str, Enum):
    alert = "alert"
    liveactivity = "liveactivity"


class _Signer(Protocol):
    def current_token(self) -> str: ...


@dataclass
class ApnsResult:
    ok: bool
    status_code: int = 0
    reason: str = ""
    token_invalid: bool = False


class ApnsClient:
    """Send APNs pushes over HTTP/2. One instance per gateway."""

    def __init__(
        self,
        signer: _Signer,
        bundle_id: str,
        environment: str = "production",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._signer = signer
        self._bundle_id = bundle_id
        self._host = (
            "https://api.sandbox.push.apple.com"
            if environment == "sandbox"
            else "https://api.push.apple.com"
        )
        self._client = httpx.AsyncClient(
            http2=True, timeout=10.0, transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send_alert(
        self, *, device_token: str, title: str, body: str,
        event_type: str, printer_id: str,
    ) -> ApnsResult:
        payload: dict[str, Any] = {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
                "interruption-level": "time-sensitive",
            },
            "printer_id": printer_id,
            "event_type": event_type,
        }
        return await self._post(
            device_token=device_token,
            push_type=ApnsPushType.alert,
            topic=self._bundle_id,
            payload=payload,
            priority=10,
        )

    async def send_live_activity_update(
        self, *, activity_token: str, content_state: dict[str, Any],
        stale_after_seconds: int = 3600,
    ) -> ApnsResult:
        import time
        now = int(time.time())
        payload = {
            "aps": {
                "timestamp": now,
                "event": "update",
                "content-state": content_state,
                "stale-date": now + stale_after_seconds,
            }
        }
        return await self._post(
            device_token=activity_token,
            push_type=ApnsPushType.liveactivity,
            topic=f"{self._bundle_id}.push-type.liveactivity",
            payload=payload,
            priority=5,
        )

    async def send_live_activity_start(
        self, *, start_token: str, attributes_type: str,
        attributes: dict[str, Any], content_state: dict[str, Any],
        stale_after_seconds: int = 3600,
    ) -> ApnsResult:
        import time
        now = int(time.time())
        payload = {
            "aps": {
                "timestamp": now,
                "event": "start",
                "attributes-type": attributes_type,
                "attributes": attributes,
                "content-state": content_state,
                "stale-date": now + stale_after_seconds,
            }
        }
        return await self._post(
            device_token=start_token,
            push_type=ApnsPushType.liveactivity,
            topic=f"{self._bundle_id}.push-type.liveactivity",
            payload=payload,
            priority=10,
        )

    async def send_live_activity_end(
        self, *, activity_token: str, content_state: dict[str, Any],
        dismissal_seconds_from_now: int = 0,
    ) -> ApnsResult:
        import time
        now = int(time.time())
        payload = {
            "aps": {
                "timestamp": now,
                "event": "end",
                "content-state": content_state,
                "dismissal-date": now + dismissal_seconds_from_now,
            }
        }
        return await self._post(
            device_token=activity_token,
            push_type=ApnsPushType.liveactivity,
            topic=f"{self._bundle_id}.push-type.liveactivity",
            payload=payload,
            priority=10,
        )

    async def _post(
        self, *, device_token: str, push_type: ApnsPushType, topic: str,
        payload: dict[str, Any], priority: int,
    ) -> ApnsResult:
        headers = {
            "authorization": f"bearer {self._signer.current_token()}",
            "apns-push-type": push_type.value,
            "apns-topic": topic,
            "apns-priority": str(priority),
        }
        url = f"{self._host}/3/device/{device_token}"
        try:
            response = await self._client.post(
                url, headers=headers, content=json.dumps(payload),
            )
        except httpx.HTTPError as exc:
            logger.warning("APNs request failed: %s", exc)
            return ApnsResult(ok=False, reason=str(exc))

        if 200 <= response.status_code < 300:
            return ApnsResult(ok=True, status_code=response.status_code)

        reason = ""
        try:
            reason = response.json().get("reason", "")
        except Exception:
            reason = response.text
        token_invalid = reason in _TOKEN_INVALID_REASONS
        if response.status_code == 410:
            token_invalid = True
        return ApnsResult(
            ok=False,
            status_code=response.status_code,
            reason=reason,
            token_invalid=token_invalid,
        )
