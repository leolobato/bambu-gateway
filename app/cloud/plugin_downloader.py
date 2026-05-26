"""Downloads and validates the Bambu Lab network plugin (Linux .so).

Runs at gateway startup when ``settings.bambu_cloud_enabled`` is True.
Pulls ``libbambu_networking.so`` and ``libBambuSource.so`` from Bambu's
CDN, validating ELF magic + SHA-256 manifest + ABI version pin before
declaring the plugin "active".
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.cloud import BAMBU_NETWORK_AGENT_VERSION, BAMBU_STUDIO_USER_AGENT

logger = logging.getLogger("bambu.cloud.downloader")


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


class IntegrityError(RuntimeError):
    """Raised when a downloaded plugin file fails integrity validation."""


def validate_sha256(path: Path, expected_hex: str) -> None:
    """Confirm that ``path`` hashes to ``expected_hex`` under SHA-256.

    Raises :class:`IntegrityError` on mismatch or if the file cannot be read.
    Reads in 1 MiB chunks so the plugin (often 10+ MiB) doesn't load entirely
    into memory.
    """
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError as exc:
        raise IntegrityError(f"cannot read {path}: {exc}") from exc

    actual = h.hexdigest()
    if actual.lower() != expected_hex.lower():
        raise IntegrityError(
            f"SHA-256 mismatch for {path.name}: "
            f"expected {expected_hex}, got {actual}"
        )


def _hex_sha256_of_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of ``path`` (chunked read)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_ELF_MAGIC = b"\x7fELF"
_ELFCLASS64 = 2
_ELFDATA2LSB = 1
_EV_CURRENT = 1
_EM_X86_64 = 0x3E


def validate_elf(path: Path) -> None:
    """Sanity-check that ``path`` is a 64-bit little-endian x86_64 ELF.

    Does not parse program headers — only the first 20 bytes of the file are
    inspected. This catches "the CDN served us the wrong binary" without
    pulling in a full ELF parser. Mirrors OrcaSlicer-bambulab's
    ``validate_linux_so_binary`` (``PJarczakLinuxBridgeConfig.cpp:293+``).

    Raises :class:`IntegrityError` on any mismatch.
    """
    try:
        head = path.read_bytes()[:20]
    except OSError as exc:
        raise IntegrityError(f"cannot read {path}: {exc}") from exc

    if len(head) < 20 or head[:4] != _ELF_MAGIC:
        raise IntegrityError(f"{path.name} is not an ELF file")
    if head[4] != _ELFCLASS64:
        raise IntegrityError(f"{path.name} is not 64-bit ELF")
    if head[5] != _ELFDATA2LSB:
        raise IntegrityError(f"{path.name} is not little-endian")
    if head[6] != _EV_CURRENT:
        raise IntegrityError(f"{path.name} has unexpected ELF version")
    machine = int.from_bytes(head[18:20], "little")
    if machine != _EM_X86_64:
        raise IntegrityError(
            f"{path.name} is for machine 0x{machine:x}, expected x86_64 (0x3E)"
        )


class AbiVersionMismatch(IntegrityError):
    """Raised when the manifest's abi_version diverges from the pinned one."""


def validate_abi_version(manifest_abi: str | None, *, pinned: str) -> None:
    """Confirm that the manifest declares a compatible ABI version.

    Compatibility rule mirrors OrcaSlicer-bambulab
    (``PJarczakLinuxBridgeConfig.cpp:293-490``): the first 8 chars
    (``02.05.02``) must match. The 4th component is patch-level and may
    drift.

    Raises :class:`AbiVersionMismatch` if the value is missing or
    incompatible.
    """
    if manifest_abi is None:
        raise AbiVersionMismatch(
            f"manifest entry is missing abi_version; expected {pinned}"
        )
    if len(manifest_abi) < 8 or len(pinned) < 8:
        raise AbiVersionMismatch(
            f"abi_version {manifest_abi!r} has unexpected shape"
        )
    if manifest_abi[:8] != pinned[:8]:
        raise AbiVersionMismatch(
            f"abi_version {manifest_abi!r} incompatible with pinned {pinned!r}"
        )


# Bambu CDN endpoint paths (per Phase 0 §Q11.3 discovery).
_RESOURCE_TYPE = "slicer/plugins/cloud"
_LISTING_PATH = "/v1/iot-service/api/slicer/resource"
_NETWORK_SO = "libbambu_networking.so"
_SOURCE_SO = "libBambuSource.so"
_MANIFEST_FILE = "linux_payload_manifest.json"


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


class ListingParseError(ValueError):
    """Raised when the Bambu resource-listing JSON is malformed or empty."""


@dataclass(frozen=True)
class ResourceEntry:
    """One entry from the Bambu resource-listing response.

    Matches the schema captured in Phase 0 §Q11.3.
    """

    type: str
    version: str
    url: str
    description: str = ""
    force_update: bool = False


def parse_resource_listing(blob: bytes) -> ResourceEntry:
    """Parse the listing JSON and return the cloud-plugin entry.

    Raises :class:`ListingParseError` if the body is invalid or contains no
    ``type == "slicer/plugins/cloud"`` entry.
    """
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ListingParseError(f"listing is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ListingParseError("listing must be a JSON object")
    resources = data.get("resources")
    if not isinstance(resources, list):
        raise ListingParseError("listing 'resources' must be a list")
    for raw in resources:
        if not isinstance(raw, dict):
            continue
        if raw.get("type") != _RESOURCE_TYPE:
            continue
        try:
            return ResourceEntry(
                type=raw["type"],
                version=raw["version"],
                url=raw["url"],
                description=raw.get("description", ""),
                force_update=bool(raw.get("force_update", False)),
            )
        except KeyError as exc:
            raise ListingParseError(
                f"resource entry missing required field: {exc.args[0]}"
            ) from exc
    raise ListingParseError(
        f"no resource of type {_RESOURCE_TYPE!r} in listing"
    )


def _bootstrap_query_version() -> str:
    """Build the version string to send on the listing query.

    Bambu's CDN responds with a resource entry only when the query version is
    older than the latest within the same MM.mm.pp.CC prefix. We send patch-DD
    of ``00`` so the CDN always returns the current entry — e.g., pinned
    ``02.05.02.58`` becomes ``02.05.02.00``. ``ensure_active()`` then
    validates the *returned* version against the actual pin.
    """
    parts = BAMBU_NETWORK_AGENT_VERSION.rsplit(".", 1)
    if len(parts) != 2:
        return BAMBU_NETWORK_AGENT_VERSION
    return f"{parts[0]}.00"


class PluginDownloader:
    """Downloads, validates, and persists the Bambu network plugin.

    Flow:
      1. GET the resource listing with forged BambuStudio Linux headers.
      2. Parse the listing for the cloud-plugin entry; validate its version
         against the pinned ABI version.
      3. GET the ZIP archive from the entry's URL (no special headers — the
         CDN is public).
      4. Extract the ZIP to staging. The CDN ZIP contains only ``.so``
         files; there is no bundled manifest.
      5. Validate ELF magic on both required ``.so`` files.
      6. Compute SHA-256 of each ``.so`` and write our own
         ``linux_payload_manifest.json`` to staging as a local-integrity
         record so subsequent startups can detect on-disk corruption.
      7. Atomic-ish swap: remove old ``active/``, rename ``staging/`` →
         ``active/``.

    Idempotent: ``ensure_active()`` is a no-op if ``${plugin_dir}/active/``
    already contains both ``.so`` files matching the locally-written
    manifest.
    """

    def __init__(self, *, plugin_dir: Path, client: httpx.AsyncClient) -> None:
        self._plugin_dir = Path(plugin_dir)
        self._client = client

    @property
    def _active(self) -> Path:
        return self._plugin_dir / "active"

    async def ensure_active(self) -> None:
        """Guarantee that ``active/`` contains a valid pinned plugin set.

        Raises an :class:`IntegrityError` subclass on validation failure.
        On any failure, partially-written files are removed so the next
        startup tries again from scratch.
        """
        if self._active_is_valid():
            logger.info(
                "Bambu plugin already present and valid at %s; skipping fetch",
                self._active,
            )
            return

        staging = self._plugin_dir / "staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        try:
            listing_blob = await self._get_listing()
            entry = parse_resource_listing(listing_blob)
            validate_abi_version(
                entry.version, pinned=BAMBU_NETWORK_AGENT_VERSION
            )

            zip_blob = await self._get_binary(entry.url)
            _extract_zip(zip_blob, staging)

            for name in (_NETWORK_SO, _SOURCE_SO):
                so_path = staging / name
                if not so_path.exists():
                    raise IntegrityError(
                        f"ZIP did not contain required file {name!r}"
                    )
                validate_elf(so_path)

            # The Bambu CDN ZIP does not ship a manifest — write our own,
            # mirroring the schema OrcaSlicer-bambulab's packaging script
            # produces, so the on-disk layout matches what callers (e.g.
            # subprocess hosts) expect to find.
            self_manifest = {
                "files": [
                    {
                        "name": _NETWORK_SO,
                        "sha256": _hex_sha256_of_file(staging / _NETWORK_SO),
                        "abi_version": entry.version,
                    },
                    {
                        "name": _SOURCE_SO,
                        "sha256": _hex_sha256_of_file(staging / _SOURCE_SO),
                    },
                ],
            }
            (staging / _MANIFEST_FILE).write_text(
                json.dumps(self_manifest, indent=2)
            )

            # Atomic-ish swap: remove old active/, rename staging -> active/.
            if self._active.exists():
                shutil.rmtree(self._active)
            staging.rename(self._active)
            logger.info(
                "Bambu plugin %s installed to %s",
                entry.version,
                self._active,
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(self._active, ignore_errors=True)
            raise

    async def _get_listing(self) -> bytes:
        # The CDN behaves as a patch-DD update server: it returns a resource
        # entry only when the query version is older than the latest in the
        # same MM.mm.pp.CC prefix. Querying with our pinned version returns
        # empty resources[] (we're "already up to date"), which breaks the
        # bootstrap case where active/ doesn't exist yet. Send patch-DD=00
        # so the CDN always replies with the current latest entry; we then
        # validate that entry's version against the pin in ensure_active().
        # Literal-slash query string matches the OrcaSlicer client exactly;
        # httpx would percent-encode `/` in a `params=` dict.
        path_with_query = (
            f"{_LISTING_PATH}?{_RESOURCE_TYPE}={_bootstrap_query_version()}"
        )
        response = await self._client.get(
            path_with_query, headers=bambu_studio_headers()
        )
        response.raise_for_status()
        return response.content

    async def _get_binary(self, url: str) -> bytes:
        # The ZIP CDN is public (CloudFront); no forged headers needed.
        response = await self._client.get(url)
        response.raise_for_status()
        return response.content

    def _active_is_valid(self) -> bool:
        """Cheap pre-flight: do the files exist and match the manifest?

        Returns False on any discrepancy (caller will then re-fetch).
        """
        manifest_path = self._active / _MANIFEST_FILE
        if not manifest_path.exists():
            return False
        try:
            manifest = parse_manifest(manifest_path.read_bytes())
            net_entry = manifest.find(_NETWORK_SO)
            src_entry = manifest.find(_SOURCE_SO)
            if net_entry is None or src_entry is None:
                return False
            validate_abi_version(
                net_entry.abi_version, pinned=BAMBU_NETWORK_AGENT_VERSION
            )
            validate_sha256(self._active / _NETWORK_SO, net_entry.sha256)
            validate_sha256(self._active / _SOURCE_SO, src_entry.sha256)
        except (IntegrityError, ManifestParseError):
            return False
        return True


def _extract_zip(blob: bytes, dest: Path) -> None:
    """Extract ``blob`` (a ZIP archive) into ``dest`` (must already exist).

    Refuses any entry whose normalised path escapes ``dest`` (zip-slip
    guard). All extracted files land directly in ``dest`` — archive
    subdirectories are flattened.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Flatten: take just the basename so the manifest + .so files
            # land at dest/<name> regardless of ZIP internal layout.
            target = dest / Path(info.filename).name
            if not target.resolve().is_relative_to(dest.resolve()):
                raise IntegrityError(
                    f"ZIP entry {info.filename!r} escapes target directory"
                )
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
