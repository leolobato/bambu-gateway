"""Downloads and validates the Bambu Lab network plugin (Linux .so).

Runs at gateway startup when ``settings.bambu_cloud_enabled`` is True.
Pulls ``libbambu_networking.so`` and ``libBambuSource.so`` from Bambu's
CDN, validating ELF magic + SHA-256 manifest + ABI version pin before
declaring the plugin "active".
"""
from __future__ import annotations

from app.cloud import BAMBU_NETWORK_AGENT_VERSION, BAMBU_STUDIO_USER_AGENT


def bambu_studio_headers() -> dict[str, str]:
    """Headers that brand the request as a Linux build of Bambu Studio.

    Required by Bambu's CDN to serve the proprietary plugin payload.
    Identity must match :data:`BAMBU_NETWORK_AGENT_VERSION`; ``X-BBL-OS-Type:
    linux`` is load-bearing on every host (even when called from a Linux
    container) because it selects which prebuilt binary the CDN serves.
    """
    return {
        "User-Agent": BAMBU_STUDIO_USER_AGENT,
        "X-BBL-Client-Type": "slicer",
        "X-BBL-Client-Name": "BambuStudio",
        "X-BBL-Client-Version": BAMBU_NETWORK_AGENT_VERSION,
        "X-BBL-OS-Type": "linux",
    }
