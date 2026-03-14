"""HTTP client for the OrcaSlicer CLI API."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SlicingError(Exception):
    """Raised when the slicer API returns a non-200 response or is unreachable."""


@dataclass
class SliceResult:
    """Result from a slice request, including content and settings transfer info."""

    content: bytes
    settings_transfer_status: str = ""
    settings_transferred: list[dict] = field(default_factory=list)


class SlicerClient:
    """Thin wrapper around the OrcaSlicer CLI API."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._has_stream: bool | None = None  # None = unknown, check on first call

    async def slice(
        self,
        file_data: bytes,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list[str] | dict[str, Any],
        plate_type: str = "",
    ) -> SliceResult:
        """Send a 3MF file to the slicer and return the sliced result."""
        url = f"{self._base_url}/slice"
        files = {"file": (filename, file_data, "application/octet-stream")}
        data = {
            "machine_profile": machine_profile,
            "process_profile": process_profile,
            "filament_profiles": json.dumps(filament_profiles),
        }
        if plate_type:
            data["plate_type"] = plate_type

        logger.info("Sending %s to slicer at %s", filename, url)
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(url, files=files, data=data)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")

        if resp.status_code != 200:
            detail = resp.text[:500]
            raise SlicingError(f"Slicer returned {resp.status_code}: {detail}")

        status = resp.headers.get("x-settings-transfer-status", "")
        transferred = []
        raw = resp.headers.get("x-settings-transferred", "")
        if raw:
            try:
                transferred = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Failed to parse X-Settings-Transferred header")

        return SliceResult(
            content=resp.content,
            settings_transfer_status=status,
            settings_transferred=transferred,
        )

    async def _check_stream_support(self) -> bool:
        """Probe the slicer to see if /slice-stream exists."""
        if self._has_stream is not None:
            return self._has_stream
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.options(f"{self._base_url}/slice-stream")
                self._has_stream = resp.status_code != 404
        except httpx.HTTPError:
            self._has_stream = False
        logger.info("Slicer stream support: %s", self._has_stream)
        return self._has_stream

    async def slice_stream(
        self,
        file_data: bytes,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list[str] | dict[str, Any],
        plate_type: str = "",
    ):
        """Stream SSE events for a slice operation.

        Uses /slice-stream if available, otherwise falls back to /slice
        and emits synthetic SSE events.
        Yields dicts with 'event' and 'data' keys.
        """
        if await self._check_stream_support():
            async for event in self._slice_stream_real(
                file_data, filename, machine_profile, process_profile, filament_profiles,
                plate_type,
            ):
                yield event
        else:
            async for event in self._slice_stream_fallback(
                file_data, filename, machine_profile, process_profile, filament_profiles,
                plate_type,
            ):
                yield event

    async def _slice_stream_real(
        self, file_data, filename, machine_profile, process_profile, filament_profiles,
        plate_type,
    ):
        url = f"{self._base_url}/slice-stream"
        files = {"file": (filename, file_data, "application/octet-stream")}
        form_data = {
            "machine_profile": machine_profile,
            "process_profile": process_profile,
            "filament_profiles": json.dumps(filament_profiles),
        }
        if plate_type:
            form_data["plate_type"] = plate_type

        logger.info("Streaming slice of %s via %s", filename, url)
        timeout = httpx.Timeout(connect=10, read=300, write=60, pool=10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, files=files, data=form_data) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise SlicingError(
                        f"Slicer returned {resp.status_code}: {body.decode()[:500]}"
                    )

                event_type = None
                data_lines: list[str] = []
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data_lines.append(line[6:])
                    elif line == "":
                        if event_type and data_lines:
                            try:
                                payload = json.loads("".join(data_lines))
                            except json.JSONDecodeError:
                                payload = {"raw": "".join(data_lines)}
                            yield {"event": event_type, "data": payload}
                        event_type = None
                        data_lines = []

    async def _slice_stream_fallback(
        self, file_data, filename, machine_profile, process_profile, filament_profiles,
        plate_type,
    ):
        """Use the non-streaming /slice endpoint and emit synthetic SSE events."""
        yield {"event": "status", "data": {"phase": "slicing", "message": "Slicing..."}}

        result = await self.slice(
            file_data, filename, machine_profile, process_profile, filament_profiles,
            plate_type,
        )

        transfer_info = {}
        if result.settings_transfer_status:
            transfer_info["status"] = result.settings_transfer_status
            if result.settings_transferred:
                transfer_info["transferred"] = result.settings_transferred

        yield {"event": "result", "data": {
            "file_base64": base64.b64encode(result.content).decode(),
            "file_size": len(result.content),
            "settings_transfer": transfer_info or None,
        }}
        yield {"event": "done", "data": {}}

    async def get_profiles(
        self,
        category: str,
        *,
        machine: str = "",
        ams_assignable: bool | None = None,
    ) -> list[dict]:
        """Fetch profiles of the given category from the slicer.

        category: "machines", "processes", "filaments", or "plate-types"
        machine: optional machine setting_id to filter by (e.g. "GM014")
        ams_assignable: optional filter for filament profiles only.
        """
        url = f"{self._base_url}/profiles/{category}"
        params = {}
        if machine:
            params["machine"] = machine
        if category == "filaments" and ams_assignable is not None:
            params["ams_assignable"] = "true" if ams_assignable else "false"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, params=params)
        except httpx.HTTPError as e:
            logger.error("Failed to fetch %s from slicer: %s", category, e)
            return []

        if resp.status_code != 200:
            logger.error("Slicer /profiles/%s returned %d", category, resp.status_code)
            return []

        return resp.json()
