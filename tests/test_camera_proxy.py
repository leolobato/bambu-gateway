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


from app.camera_proxy import FrameParser


def _frame(jpeg: bytes) -> bytes:
    """Wrap a JPEG payload in the printer's 16-byte frame header."""
    n = len(jpeg)
    header = bytes([n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, (n >> 24) & 0xFF])
    header += bytes(12)  # unused
    return header + jpeg


def test_frameParser_singleChunkSingleFrame_emitsJpeg():
    parser = FrameParser()
    jpeg = b"\xff\xd8" + b"A" * 100 + b"\xff\xd9"
    out = parser.feed(_frame(jpeg))
    assert out == [jpeg]


def test_frameParser_splitAcrossChunks_reassembles():
    parser = FrameParser()
    jpeg = b"\xff\xd8" + b"B" * 50 + b"\xff\xd9"
    payload = _frame(jpeg)
    # Feed 5 bytes at a time.
    out: list[bytes] = []
    for i in range(0, len(payload), 5):
        out.extend(parser.feed(payload[i:i + 5]))
    assert out == [jpeg]


def test_frameParser_multipleFramesInOneChunk_emitsAll():
    parser = FrameParser()
    a = b"\xff\xd8" + b"A" * 10 + b"\xff\xd9"
    b_ = b"\xff\xd8" + b"B" * 20 + b"\xff\xd9"
    out = parser.feed(_frame(a) + _frame(b_))
    assert out == [a, b_]


def test_frameParser_partialThenComplete_emitsOnceComplete():
    parser = FrameParser()
    jpeg = b"\xff\xd8" + b"C" * 30 + b"\xff\xd9"
    payload = _frame(jpeg)
    # Feed everything except the last byte first.
    assert parser.feed(payload[:-1]) == []
    # Feed the last byte; now it should emit.
    assert parser.feed(payload[-1:]) == [jpeg]
