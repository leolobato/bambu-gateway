"""FTPS client for uploading 3MF files to Bambu Lab printers."""

from __future__ import annotations

import ftplib
import logging
import socket
import ssl
from collections.abc import Callable
from io import BytesIO

logger = logging.getLogger(__name__)

FTPS_PORT = 990
FTP_USERNAME = "bblp"


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTP_TLS subclass that connects with implicit TLS (port 990).

    ftplib.FTP_TLS only supports explicit FTPS (STARTTLS after plaintext
    connect). Bambu printers require implicit FTPS where the socket is
    wrapped in TLS before the server sends its welcome banner.
    """

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address

        self.sock = socket.create_connection(
            (self.host, self.port),
            self.timeout,
            source_address=self.source_address,
        )
        self.af = self.sock.family

        # Wrap with TLS immediately (implicit FTPS)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.sock = ctx.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)

        # Now read the welcome banner
        self.welcome = self.getresp()
        return self.welcome

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        """Store a file, skipping SSL unwrap to avoid timeout.

        The Bambu printer's FTPS server doesn't respond to close_notify,
        causing conn.unwrap() in the stdlib to hang indefinitely.
        """
        self.voidcmd("TYPE I")
        with self.transfercmd(cmd, rest) as conn:
            while True:
                buf = fp.read(blocksize)
                if not buf:
                    break
                conn.sendall(buf)
                if callback:
                    callback(buf)
        return self.voidresp()


def upload_file(
    ip: str,
    access_code: str,
    file_data: bytes,
    filename: str,
    progress_callback: Callable[[int], None] | None = None,
) -> str:
    """Upload a file to the printer via implicit FTPS.

    Args:
        progress_callback: Called with the number of bytes in each chunk sent.
    Returns the remote path of the uploaded file.
    """
    remote_path = f"/cache/{filename}"

    logger.info("Uploading %s (%d bytes) to %s:%d",
                filename, len(file_data), ip, FTPS_PORT)

    def _on_chunk(buf: bytes) -> None:
        if progress_callback is not None:
            progress_callback(len(buf))

    ftp = ImplicitFTPS()
    try:
        ftp.connect(ip, FTPS_PORT, timeout=30)
        ftp.login(FTP_USERNAME, access_code)
        ftp.prot_p()

        try:
            ftp.cwd("/cache")
        except ftplib.error_perm:
            ftp.mkd("/cache")
            ftp.cwd("/cache")

        ftp.storbinary(
            f"STOR {filename}", BytesIO(file_data),
            blocksize=65536, callback=_on_chunk,
        )
        logger.info("Upload complete: %s", remote_path)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()

    return remote_path


def download_file(*, host: str, access_code: str, remote_path: str, port: int = 990) -> bytes:
    """Download a single file from the printer's FTPS server, returning bytes.

    Mirrors `upload_file`'s connection setup but uses RETR. Like upload, we
    do NOT call `quit()`/`close()` cleanly because Bambu's FTPS daemon does
    not respond to TLS close_notify, which makes graceful shutdown hang.
    """
    chunks: list[bytes] = []

    def _on_chunk(b: bytes) -> None:
        chunks.append(b)

    ftps = ImplicitFTPS()
    ftps.connect(host=host, port=port, timeout=30)
    ftps.login("bblp", access_code)
    ftps.prot_p()
    try:
        ftps.retrbinary(f"RETR {remote_path}", _on_chunk, blocksize=64 * 1024)
    finally:
        try:
            ftps.sock.close()  # avoid the hang; intentional ungraceful close
        except Exception:
            pass

    return b"".join(chunks)
