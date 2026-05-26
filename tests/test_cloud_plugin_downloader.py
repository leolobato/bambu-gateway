"""Tests for the cloud plugin downloader."""
from __future__ import annotations

from app.cloud import BAMBU_NETWORK_AGENT_VERSION
from app.cloud.plugin_downloader import bambu_studio_headers


def test_headers_brand_as_bambu_studio_linux():
    headers = bambu_studio_headers()
    assert headers["User-Agent"] == f"BambuStudio/{BAMBU_NETWORK_AGENT_VERSION}"
    assert headers["X-BBL-Client-Type"] == "slicer"
    assert headers["X-BBL-Client-Name"] == "BambuStudio"
    assert headers["X-BBL-Client-Version"] == BAMBU_NETWORK_AGENT_VERSION
    assert headers["X-BBL-OS-Type"] == "linux"


def test_headers_returns_a_fresh_dict_each_call():
    a = bambu_studio_headers()
    a["User-Agent"] = "mutated"
    b = bambu_studio_headers()
    assert b["User-Agent"] == f"BambuStudio/{BAMBU_NETWORK_AGENT_VERSION}"


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------
import json

import pytest

from app.cloud.plugin_downloader import (
    ManifestEntry,
    ManifestParseError,
    parse_manifest,
)


SAMPLE_MANIFEST = {
    "files": [
        {
            "name": "libbambu_networking.so",
            "sha256": "a" * 64,
            "abi_version": "02.05.02.58",
        },
        {
            "name": "libBambuSource.so",
            "sha256": "b" * 64,
        },
    ],
}


def test_parse_manifest_extracts_entries():
    manifest = parse_manifest(json.dumps(SAMPLE_MANIFEST).encode("utf-8"))
    assert len(manifest.files) == 2
    networking = manifest.find("libbambu_networking.so")
    assert networking == ManifestEntry(
        name="libbambu_networking.so",
        sha256="a" * 64,
        abi_version="02.05.02.58",
    )
    source = manifest.find("libBambuSource.so")
    assert source.abi_version is None  # libBambuSource doesn't carry one


def test_parse_manifest_rejects_invalid_json():
    with pytest.raises(ManifestParseError):
        parse_manifest(b"not json")


def test_parse_manifest_rejects_missing_files_key():
    with pytest.raises(ManifestParseError):
        parse_manifest(json.dumps({"other": "value"}).encode("utf-8"))


def test_parse_manifest_rejects_entry_missing_sha256():
    bad = {"files": [{"name": "x.so"}]}
    with pytest.raises(ManifestParseError):
        parse_manifest(json.dumps(bad).encode("utf-8"))


def test_parse_manifest_rejects_entry_missing_name():
    bad = {"files": [{"sha256": "x" * 64}]}
    with pytest.raises(ManifestParseError):
        parse_manifest(json.dumps(bad).encode("utf-8"))


def test_parse_manifest_ignores_extra_files_not_in_manifest():
    # The ZIP can contain additional .so files (liblive555.so, libagora_*.so)
    # that the manifest does NOT list. parse_manifest just returns what's in
    # the manifest; ignoring extras is the caller's responsibility.
    manifest = parse_manifest(json.dumps(SAMPLE_MANIFEST).encode("utf-8"))
    assert manifest.find("liblive555.so") is None


def test_find_returns_none_for_unknown_name():
    manifest = parse_manifest(json.dumps(SAMPLE_MANIFEST).encode("utf-8"))
    assert manifest.find("does-not-exist.so") is None


# ---------------------------------------------------------------------------
# SHA-256 validation
# ---------------------------------------------------------------------------

from app.cloud.plugin_downloader import IntegrityError, validate_sha256


def test_validate_sha256_accepts_matching_digest(tmp_path):
    p = tmp_path / "x.so"
    p.write_bytes(b"hello world")
    # sha256("hello world") =
    # b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9
    validate_sha256(p, "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9")


def test_validate_sha256_rejects_mismatch(tmp_path):
    p = tmp_path / "x.so"
    p.write_bytes(b"hello world")
    with pytest.raises(IntegrityError):
        validate_sha256(p, "0" * 64)


def test_validate_sha256_rejects_missing_file(tmp_path):
    with pytest.raises(IntegrityError):
        validate_sha256(tmp_path / "absent.so", "0" * 64)


# ---------------------------------------------------------------------------
# ELF magic + arch validation
# ---------------------------------------------------------------------------

from app.cloud.plugin_downloader import validate_elf


# A minimal valid ELF64 little-endian x86_64 header (52 bytes is enough; we
# only inspect the first 20). Bytes 0-3: magic. Byte 4: class (2 = ELF64).
# Byte 5: data encoding (1 = LE). Byte 6: version (1 = EV_CURRENT).
# Bytes 18-19: e_machine (0x3E = x86_64, little-endian).
_ELF_X86_64_LE = (
    b"\x7fELF"            # magic
    + b"\x02"             # EI_CLASS = ELFCLASS64
    + b"\x01"             # EI_DATA  = ELFDATA2LSB
    + b"\x01"             # EI_VERSION
    + b"\x00" * 9         # EI_OSABI..EI_PAD
    + b"\x03\x00"         # e_type   = ET_DYN
    + b"\x3e\x00"         # e_machine = EM_X86_64 (0x3E little-endian)
)


def test_validate_elf_accepts_x86_64_le(tmp_path):
    p = tmp_path / "x.so"
    p.write_bytes(_ELF_X86_64_LE + b"\x00" * 128)
    validate_elf(p)  # does not raise


def test_validate_elf_rejects_non_elf(tmp_path):
    p = tmp_path / "x.so"
    p.write_bytes(b"not an ELF file at all")
    with pytest.raises(IntegrityError):
        validate_elf(p)


def test_validate_elf_rejects_32bit(tmp_path):
    p = tmp_path / "x.so"
    blob = bytearray(_ELF_X86_64_LE)
    blob[4] = 1  # ELFCLASS32
    p.write_bytes(bytes(blob) + b"\x00" * 128)
    with pytest.raises(IntegrityError):
        validate_elf(p)


def test_validate_elf_rejects_big_endian(tmp_path):
    p = tmp_path / "x.so"
    blob = bytearray(_ELF_X86_64_LE)
    blob[5] = 2  # ELFDATA2MSB
    p.write_bytes(bytes(blob) + b"\x00" * 128)
    with pytest.raises(IntegrityError):
        validate_elf(p)


def test_validate_elf_rejects_non_x86_64_machine(tmp_path):
    p = tmp_path / "x.so"
    blob = bytearray(_ELF_X86_64_LE)
    blob[18] = 0xB7  # EM_AARCH64
    p.write_bytes(bytes(blob) + b"\x00" * 128)
    with pytest.raises(IntegrityError):
        validate_elf(p)


# ---------------------------------------------------------------------------
# ABI version pin check
# ---------------------------------------------------------------------------

from app.cloud.plugin_downloader import (
    AbiVersionMismatch,
    validate_abi_version,
)


def test_abi_version_accepts_exact_match():
    validate_abi_version("02.05.02.51", pinned="02.05.02.51")


def test_abi_version_accepts_patch_level_drift():
    # First 8 chars (02.05.02) must match; the 4th component is patch.
    validate_abi_version("02.05.02.99", pinned="02.05.02.51")
    validate_abi_version("02.05.02.00", pinned="02.05.02.51")


def test_abi_version_rejects_minor_drift():
    with pytest.raises(AbiVersionMismatch):
        validate_abi_version("02.05.03.51", pinned="02.05.02.51")


def test_abi_version_rejects_major_drift():
    with pytest.raises(AbiVersionMismatch):
        validate_abi_version("03.05.02.51", pinned="02.05.02.51")


def test_abi_version_rejects_missing():
    with pytest.raises(AbiVersionMismatch):
        validate_abi_version(None, pinned="02.05.02.51")


def test_abi_version_rejects_garbage():
    with pytest.raises(AbiVersionMismatch):
        validate_abi_version("xyz", pinned="02.05.02.51")


# ---------------------------------------------------------------------------
# Resource listing parsing + PluginDownloader driver
# ---------------------------------------------------------------------------

import hashlib
import io
import json as _json
import zipfile

import httpx

from app.cloud.plugin_downloader import (
    ListingParseError,
    PluginDownloader,
    parse_resource_listing,
)


_API_BASE = "https://api.bambulab.com"
_LISTING_PATH = "/v1/iot-service/api/slicer/resource"
_ZIP_URL = (
    "https://public-cdn.bblmw.com/upgrade/studio/plugins/02.05.02.58/"
    "abc12345/linux_02.05.02.58.zip"
)


def _hex_sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _make_fake_so(name: str = "stub") -> bytes:
    # Minimal valid ELF64-LE-x86_64 header + padding so validate_elf passes.
    return _ELF_X86_64_LE + name.encode() + b"\x00" * 64


def _build_fake_zip(
    *, network_so: bytes, source_so: bytes, manifest_override: dict | None = None
) -> bytes:
    """Build an in-memory ZIP that mirrors Bambu's plugin payload structure.

    Contains both `.so` files and `linux_payload_manifest.json`.
    """
    manifest = manifest_override or {
        "files": [
            {
                "name": "libbambu_networking.so",
                "sha256": _hex_sha256(network_so),
                "abi_version": "02.05.02.58",
            },
            {
                "name": "libBambuSource.so",
                "sha256": _hex_sha256(source_so),
            },
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("linux_payload_manifest.json", _json.dumps(manifest))
        zf.writestr("libbambu_networking.so", network_so)
        zf.writestr("libBambuSource.so", source_so)
    return buffer.getvalue()


def _build_listing(*, version: str = "02.05.02.58", url: str = _ZIP_URL) -> bytes:
    """Build the JSON the listing endpoint returns (per Phase 0 §Q11.3)."""
    return _json.dumps(
        {
            "message": "success",
            "code": None,
            "error": None,
            "software": None,
            "guide": None,
            "resources": [
                {
                    "type": "slicer/plugins/cloud",
                    "version": version,
                    "description": "",
                    "url": url,
                    "force_update": False,
                }
            ],
        }
    ).encode("utf-8")


def _make_fake_cdn_handler(*, listing: bytes, zip_blob: bytes):
    """Return an httpx mock handler that serves the listing + the ZIP.

    Asserts the listing request carries the forged BambuStudio Linux headers.
    The ZIP request is unauthenticated (matches real CloudFront behaviour).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.path == _LISTING_PATH:
            # Brand check: the listing request must look like Linux Bambu Studio.
            assert (
                request.headers["User-Agent"] == "BambuStudio/02.05.02.58"
            ), request.headers["User-Agent"]
            assert request.headers["X-BBL-OS-Type"] == "linux"
            assert request.headers["X-BBL-Client-Name"] == "BambuStudio"
            # Confirm the query string preserves literal slashes (not %2F)
            assert "slicer/plugins/cloud=" in str(request.url), (
                f"query slashes were encoded: {request.url}"
            )
            return httpx.Response(200, content=listing)
        if url == _ZIP_URL:
            return httpx.Response(200, content=zip_blob)
        return httpx.Response(404, text=f"unexpected request: {url}")

    return handler


def test_parse_resource_listing_extracts_cloud_entry():
    entry = parse_resource_listing(_build_listing())
    assert entry.version == "02.05.02.58"
    assert entry.url == _ZIP_URL


def test_parse_resource_listing_rejects_no_cloud_entry():
    blob = _json.dumps({"message": "success", "resources": []}).encode()
    with pytest.raises(ListingParseError):
        parse_resource_listing(blob)


def test_parse_resource_listing_rejects_malformed_json():
    with pytest.raises(ListingParseError):
        parse_resource_listing(b"not json")


async def test_downloader_fetches_and_validates(tmp_path):
    network_so = _make_fake_so("network")
    source_so = _make_fake_so("source")
    listing = _build_listing()
    zip_blob = _build_fake_zip(network_so=network_so, source_so=source_so)
    handler = _make_fake_cdn_handler(listing=listing, zip_blob=zip_blob)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=_API_BASE
    ) as client:
        downloader = PluginDownloader(plugin_dir=tmp_path, client=client)
        await downloader.ensure_active()

    active = tmp_path / "active"
    assert (active / "libbambu_networking.so").read_bytes() == network_so
    assert (active / "libBambuSource.so").read_bytes() == source_so
    assert (active / "linux_payload_manifest.json").exists()


async def test_downloader_is_idempotent_when_already_valid(tmp_path):
    network_so = _make_fake_so("network")
    source_so = _make_fake_so("source")
    listing = _build_listing()
    zip_blob = _build_fake_zip(network_so=network_so, source_so=source_so)
    handler = _make_fake_cdn_handler(listing=listing, zip_blob=zip_blob)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=_API_BASE
    ) as client:
        downloader = PluginDownloader(plugin_dir=tmp_path, client=client)
        await downloader.ensure_active()

    # Second call must not hit the CDN — give the downloader a poisoned
    # client that 500s on any request.
    poisoned = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(500, text="should not be called")
        ),
        base_url=_API_BASE,
    )
    try:
        downloader = PluginDownloader(plugin_dir=tmp_path, client=poisoned)
        await downloader.ensure_active()  # must not raise
    finally:
        await poisoned.aclose()


async def test_downloader_rejects_tampered_so(tmp_path):
    network_so = _make_fake_so("network")
    source_so = _make_fake_so("source")
    listing = _build_listing()
    # Build a ZIP whose manifest claims the original SHAs but whose
    # libbambu_networking.so bytes are different content. SHA validation
    # must reject this.
    manifest = {
        "files": [
            {
                "name": "libbambu_networking.so",
                "sha256": _hex_sha256(network_so),  # SHA of the ORIGINAL
                "abi_version": "02.05.02.58",
            },
            {
                "name": "libBambuSource.so",
                "sha256": _hex_sha256(source_so),
            },
        ],
    }
    tampered_zip = _build_fake_zip(
        network_so=b"\x7fELF" + b"\x00" * 200,  # tampered
        source_so=source_so,
        manifest_override=manifest,
    )
    handler = _make_fake_cdn_handler(listing=listing, zip_blob=tampered_zip)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=_API_BASE
    ) as client:
        downloader = PluginDownloader(plugin_dir=tmp_path, client=client)
        with pytest.raises(IntegrityError):
            await downloader.ensure_active()
    # No artifacts left behind on failure.
    assert not (tmp_path / "active").exists()


async def test_downloader_rejects_listing_with_incompatible_version(tmp_path):
    # Listing reports a major-bump version; pin check must reject it.
    network_so = _make_fake_so("network")
    source_so = _make_fake_so("source")
    listing = _build_listing(version="03.00.00.00")
    zip_blob = _build_fake_zip(network_so=network_so, source_so=source_so)
    handler = _make_fake_cdn_handler(listing=listing, zip_blob=zip_blob)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=_API_BASE
    ) as client:
        downloader = PluginDownloader(plugin_dir=tmp_path, client=client)
        with pytest.raises(IntegrityError):
            await downloader.ensure_active()
    assert not (tmp_path / "active").exists()


async def test_downloader_recovers_from_corrupt_active_manifest(tmp_path):
    """If active/ exists but has a corrupt manifest, re-download cleanly."""
    network_so = _make_fake_so("network")
    source_so = _make_fake_so("source")
    listing = _build_listing()
    zip_blob = _build_fake_zip(network_so=network_so, source_so=source_so)
    handler = _make_fake_cdn_handler(listing=listing, zip_blob=zip_blob)

    # Pre-populate active/ with garbage so _active_is_valid() must
    # reject it without raising.
    active = tmp_path / "active"
    active.mkdir()
    (active / "linux_payload_manifest.json").write_bytes(b"not valid json")
    (active / "libbambu_networking.so").write_bytes(b"stale")
    (active / "libBambuSource.so").write_bytes(b"stale")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=_API_BASE
    ) as client:
        downloader = PluginDownloader(plugin_dir=tmp_path, client=client)
        await downloader.ensure_active()

    # Re-downloaded files replace the stale ones.
    assert (active / "libbambu_networking.so").read_bytes() == network_so
