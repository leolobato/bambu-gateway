"""TCP-JPEG camera proxy for Bambu A1/P1-family printers.

Holds one upstream TCP+TLS connection per printer and fans the decoded JPEG
frames out to N HTTP `multipart/x-mixed-replace` subscribers. The printer's
camera service only accepts one concurrent viewer, so multiplexing in the
gateway is required when more than one browser tab is watching.
"""

from __future__ import annotations


def build_auth_packet(access_code: str) -> bytes:
    """Build the 80-byte LAN binary auth packet.

    Layout (verified against panda-be-free / iOS `BambuTCPJPEGFeed.swift`):
      [0..3]   = 0x40 0x00 0x00 0x00     magic / packet type
      [4..7]   = 0x00 0x30 0x00 0x00     length marker (LE 0x3000)
      [8..15]  = 0
      [16..19] = "bblp"                  username
      [20..47] = 0
      [48..79] = access code (UTF-8, ≤32 bytes, zero-padded)
    """
    packet = bytearray(80)
    packet[0] = 0x40
    packet[5] = 0x30
    packet[16:20] = b"bblp"
    code_bytes = access_code.encode("utf-8")[:32]
    packet[48:48 + len(code_bytes)] = code_bytes
    return bytes(packet)


HEADER_SIZE = 16
"""Bambu TCP-JPEG frame header is fixed-size: 4 bytes LE length + 12 unused."""


class FrameParser:
    """Streaming parser for Bambu's `[16-byte header][JPEG]` framing.

    Accepts arbitrary byte chunks (TCP doesn't preserve message boundaries)
    and returns the list of complete JPEG payloads decoded so far.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._expected_len: int | None = None

    def feed(self, data: bytes) -> list[bytes]:
        """Append `data` and return any whole JPEG payloads now available."""
        self._buffer += data
        out: list[bytes] = []
        while True:
            if self._expected_len is None:
                if len(self._buffer) < HEADER_SIZE:
                    break
                length = (
                    self._buffer[0]
                    | (self._buffer[1] << 8)
                    | (self._buffer[2] << 16)
                    | (self._buffer[3] << 24)
                )
                self._expected_len = length
                del self._buffer[:HEADER_SIZE]
            assert self._expected_len is not None
            if len(self._buffer) < self._expected_len:
                break
            jpeg = bytes(self._buffer[:self._expected_len])
            del self._buffer[:self._expected_len]
            self._expected_len = None
            out.append(jpeg)
        return out
