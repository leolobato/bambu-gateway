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
