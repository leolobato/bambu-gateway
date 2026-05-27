"""Map plugin BAMBU_NETWORK_ERR_* numeric codes to user-facing messages.

Codes per Phase 0 §7.4. Discovery (Phase A.5) may extend this list.
"""

_ERROR_MESSAGES: dict[int, str] = {
    -2040: "File too large for Bambu cloud upload",
    -2110: "Bambu cloud OSS upload failed",
    -2120: "Bambu cloud rejected the print job",
    -2060: "Timed out waiting for printer to acknowledge cloud job",
    -4020: "LAN FTP upload failed (cloud fallback should engage)",
    -16: "Uploaded file checksum mismatch",
}


def error_message(code: int) -> str:
    """Return a human-readable message for a plugin error code."""
    return _ERROR_MESSAGES.get(code, f"Bambu plugin error {code}")
