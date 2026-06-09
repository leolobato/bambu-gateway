"""Cloud-direct printing subsystem.

Only loaded when ``settings.bambu_cloud_enabled`` is True. Pinned to a
specific Bambu network-plugin release; bumping requires updating both
constants below in lockstep with the bundled ``.so`` files.
"""
from __future__ import annotations

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
