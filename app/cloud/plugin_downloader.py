"""Downloads and validates the Bambu Lab network plugin (Linux .so).

Runs at gateway startup when ``settings.bambu_cloud_enabled`` is True.
Pulls ``libbambu_networking.so`` and ``libBambuSource.so`` from Bambu's
CDN, validating ELF magic + SHA-256 manifest + ABI version pin before
declaring the plugin "active".
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.cloud import BAMBU_NETWORK_AGENT_VERSION, BAMBU_STUDIO_USER_AGENT


class ManifestParseError(ValueError):
    """Raised when the plugin manifest JSON is malformed or missing fields."""


@dataclass(frozen=True)
class ManifestEntry:
    """One ``files[]`` entry from ``linux_payload_manifest.json``.

    Only ``libbambu_networking.so`` carries ``abi_version`` per the Bambu
    packaging script; other entries omit it.
    """

    name: str
    sha256: str
    abi_version: str | None = None


@dataclass(frozen=True)
class Manifest:
    """Parsed ``linux_payload_manifest.json``.

    Note: the manifest has no top-level ``version`` field — the version comes
    from the resource-listing response, not the manifest itself.
    """

    files: tuple[ManifestEntry, ...] = field(default_factory=tuple)

    def find(self, name: str) -> ManifestEntry | None:
        for entry in self.files:
            if entry.name == name:
                return entry
        return None


def parse_manifest(blob: bytes) -> Manifest:
    """Parse the raw manifest bytes into a :class:`Manifest`.

    Raises :class:`ManifestParseError` if the JSON is invalid or any required
    per-entry field is missing.
    """
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ManifestParseError(f"manifest is not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "files" not in data:
        raise ManifestParseError("manifest must be an object with a 'files' key")

    raw_files = data["files"]
    if not isinstance(raw_files, list):
        raise ManifestParseError("manifest 'files' must be a list")

    entries: list[ManifestEntry] = []
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise ManifestParseError("each entry in files[] must be an object")
        try:
            entries.append(
                ManifestEntry(
                    name=raw["name"],
                    sha256=raw["sha256"],
                    abi_version=raw.get("abi_version"),
                )
            )
        except KeyError as exc:
            raise ManifestParseError(
                f"manifest entry missing required field: {exc.args[0]}"
            ) from exc

    return Manifest(files=tuple(entries))


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
