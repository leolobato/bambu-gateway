"""Tests for app/camera_proxy.py — TCP-JPEG proxy."""

from __future__ import annotations

from app.camera_proxy import build_auth_packet


def test_buildAuthPacket_layoutMatchesIOS():
    """Auth packet must be byte-identical to the iOS BambuTCPJPEGFeed.swift handshake."""
    packet = build_auth_packet(access_code="12345678")

    assert len(packet) == 80
    # Magic header (bytes 0-3)
    assert packet[0:4] == bytes([0x40, 0x00, 0x00, 0x00])
    # Length marker, little-endian 0x3000 (bytes 4-7)
    assert packet[4:8] == bytes([0x00, 0x30, 0x00, 0x00])
    # Bytes 8..15 are zero
    assert packet[8:16] == bytes(8)
    # Username "bblp" (bytes 16-19)
    assert packet[16:20] == b"bblp"
    # Bytes 20..47 are zero
    assert packet[20:48] == bytes(28)
    # Access code (bytes 48..55, then zero-padded to 80)
    assert packet[48:56] == b"12345678"
    assert packet[56:80] == bytes(24)


def test_buildAuthPacket_truncatesLongAccessCodeTo32Bytes():
    long_code = "x" * 50
    packet = build_auth_packet(access_code=long_code)
    assert len(packet) == 80
    assert packet[48:80] == b"x" * 32  # truncated to 32 bytes
