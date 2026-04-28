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
