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
