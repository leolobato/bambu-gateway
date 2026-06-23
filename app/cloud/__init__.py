"""Cloud-direct printing subsystem.

Only loaded when ``settings.bambu_cloud_enabled`` is True. Pinned to a
specific Bambu network-plugin release; bumping requires updating both
constants below in lockstep with the bundled ``.so`` files.
"""
from __future__ import annotations

import uuid
from pathlib import Path

#: Bambu network plugin ABI version that we are pinned to.
#: Used as ``X-BBL-Client-Version`` in CDN requests and as the ``abi_version``
#: that the manifest entry for ``libbambu_networking.so`` must declare.
#: Patch-level drift (first 8 chars, ``02.05.02``) is tolerated.
BAMBU_NETWORK_AGENT_VERSION = "02.05.02.58"

#: ``User-Agent`` string the slicer-side wrapper uses for every CDN call.
#: Must move together with :data:`BAMBU_NETWORK_AGENT_VERSION`.
BAMBU_STUDIO_USER_AGENT = f"BambuStudio/{BAMBU_NETWORK_AGENT_VERSION}"

_REGION_API_BASE: dict[str, str] = {
    "US": "https://api.bambulab.com",
    "CN": "https://api.bambulab.cn",
}


def region_api_base(region: str) -> str:
    """Map a Bambu region code to its REST API base URL.

    Single source of truth for the CDN/plugin download client AND the auth
    profile fetch — the two must never disagree about regional endpoints.
    """
    base = _REGION_API_BASE.get(region)
    if base is None:
        raise ValueError(f"unsupported region: {region!r}")
    return base


def bambu_studio_headers() -> dict[str, str]:
    """Headers that brand a request as a Linux build of Bambu Studio.

    Required by Bambu's CDN to serve the proprietary plugin payload and by
    the user-service API. Identity must match
    :data:`BAMBU_NETWORK_AGENT_VERSION`; ``X-BBL-OS-Type: linux`` is
    load-bearing on every host because it selects which prebuilt binary the
    CDN serves.
    """
    return {
        "User-Agent": BAMBU_STUDIO_USER_AGENT,
        "X-BBL-Client-Type": "slicer",
        "X-BBL-Client-Name": "BambuStudio",
        "X-BBL-Client-Version": BAMBU_NETWORK_AGENT_VERSION,
        "X-BBL-OS-Type": "linux",
    }


def get_or_create_device_id(state_dir: Path) -> str:
    """Return a stable per-install UUID, persisted under ``state_dir``.

    Used as ``X-BBL-Device-ID`` when branding the plugin agent. It must be
    stable across restarts (the cloud ties control/session state to it), so we
    generate it once and reuse it. Mirrors OrcaSlicer's app_config
    ``slicer_uuid``.
    """
    path = state_dir / "device_id"
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    device_id = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(device_id)
    return device_id


def plugin_agent_headers(device_id: str) -> dict[str, str]:
    """X-BBL-* identity pushed into the plugin agent via
    ``set_extra_http_header`` at bring-up.

    OrcaSlicer issues this once before ``connect_server``; without it the
    plugin's own cloud session is unbranded and the relay refuses
    ``print``-namespace writes with -2 (reads still succeed). Mirrors
    ``GUI_App::get_extra_header()`` on the Linux/bridge path, which always
    brands as a ``BambuStudio``/``slicer`` client.
    """
    return {
        "X-BBL-Client-Type": "slicer",
        "X-BBL-Client-Name": "BambuStudio",
        "X-BBL-Client-Version": BAMBU_NETWORK_AGENT_VERSION,
        "X-BBL-OS-Type": "linux",
        "X-BBL-OS-Version": "6.1.0",
        "X-BBL-Device-ID": device_id,
        "X-BBL-Language": "en-US",
    }
