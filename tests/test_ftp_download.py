"""Tests for ftp_client.download_file."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

from app.ftp_client import download_file


def test_download_file_invokes_retrbinary_and_returns_bytes():
    fake_ftps = MagicMock()
    sample = b"PK\x03\x04" + b"\x00" * 32  # zip-like

    def _retrbinary(cmd, callback, blocksize):
        assert cmd == "RETR /cache/model.3mf"
        callback(sample)

    fake_ftps.retrbinary.side_effect = _retrbinary

    with patch("app.ftp_client.ImplicitFTPS", return_value=fake_ftps):
        out = download_file(
            host="10.0.0.5",
            access_code="x",
            remote_path="/cache/model.3mf",
        )
    assert out == sample
    fake_ftps.login.assert_called_once_with("bblp", "x")
    fake_ftps.prot_p.assert_called_once()


def test_download_file_concatenates_multiple_chunks():
    fake_ftps = MagicMock()
    chunks_to_send = [b"AAAA", b"BBBB", b"CCCC"]

    def _retrbinary(cmd, callback, blocksize):
        for c in chunks_to_send:
            callback(c)

    fake_ftps.retrbinary.side_effect = _retrbinary

    with patch("app.ftp_client.ImplicitFTPS", return_value=fake_ftps):
        out = download_file(
            host="10.0.0.5",
            access_code="x",
            remote_path="/cache/big.3mf",
        )

    assert out == b"AAAABBBBCCCC"


def test_download_file_closes_socket_when_retrbinary_raises():
    fake_ftps = MagicMock()
    fake_ftps.retrbinary.side_effect = RuntimeError("simulated transfer failure")

    with patch("app.ftp_client.ImplicitFTPS", return_value=fake_ftps):
        try:
            download_file(host="10.0.0.5", access_code="x", remote_path="/x")
        except RuntimeError:
            pass

    fake_ftps.sock.close.assert_called_once()
