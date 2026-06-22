"""Discover Bambu printers' LAN IPs via SSDP.

Bambu printers multicast an SSDP ``NOTIFY`` to ``239.255.255.250:2021`` (and
respond to ``M-SEARCH``) carrying their serial (``USN``) and LAN IP
(``Location``). The cloud bind list gives us each printer's access code but not
its IP; this fills that gap so a cloud-discovered printer can be reached over
LAN MQTT.

Example NOTIFY payload::

    NOTIFY * HTTP/1.1
    Location: 10.0.1.157
    NT: urn:bambulab-com:device:3dprinter:1
    USN: 0309DA561103403
    DevModel.bambu.com: N1
    DevName.bambu.com: A1 Mini
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import Awaitable, Callable

logger = logging.getLogger("bambu.ssdp")

_MCAST_GRP = "239.255.255.250"
_BAMBU_PORT = 2021
_DEVICE_MARKER = "bambulab-com:device:3dprinter"
_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {_MCAST_GRP}:{_BAMBU_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "ST: urn:bambulab-com:device:3dprinter:1\r\n"
    "MX: 3\r\n\r\n"
).encode()


def parse_ssdp(data: bytes) -> tuple[str, str] | None:
    """Return ``(serial, ip)`` from a Bambu SSDP payload, or None.

    Reads ``USN`` (serial) and ``Location`` (IP) from the header block; ignores
    anything that isn't a Bambu 3D-printer advertisement.
    """
    try:
        text = data.decode("utf-8", "replace")
    except Exception:
        return None
    if _DEVICE_MARKER not in text:
        return None
    headers: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    serial = headers.get("usn", "").strip()
    ip = headers.get("location", "").strip()
    # Location is sometimes a bare IP, sometimes a URL — keep just the host.
    if "://" in ip:
        ip = ip.split("://", 1)[1]
    ip = ip.split("/", 1)[0].split(":", 1)[0]
    if serial and ip:
        return serial, ip
    return None


class SSDPDiscovery:
    """Background SSDP listener mapping printer serial -> LAN IP.

    Calls ``on_found(serial, ip)`` whenever a printer's IP is first seen or
    changes, so the caller can (re)point a LAN client at it.
    """

    def __init__(
        self,
        on_found: Callable[[str, str], Awaitable[None]],
        *,
        search_interval: float = 60.0,
    ) -> None:
        self._on_found = on_found
        self._search_interval = search_interval
        self._by_serial: dict[str, str] = {}
        self._sock: socket.socket | None = None
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    def get_ip(self, serial: str) -> str | None:
        return self._by_serial.get(serial)

    async def start(self) -> None:
        try:
            self._sock = self._make_socket()
        except OSError as exc:
            logger.warning("SSDP discovery disabled (socket setup failed): %s", exc)
            return
        loop = asyncio.get_running_loop()
        self._tasks.append(loop.create_task(self._listen()))
        self._tasks.append(loop.create_task(self._search_loop()))
        logger.info("SSDP discovery started on %s:%d", _MCAST_GRP, _BAMBU_PORT)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    @staticmethod
    def _make_socket() -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", _BAMBU_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(_MCAST_GRP), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setblocking(False)
        return sock

    async def _listen(self) -> None:
        loop = asyncio.get_running_loop()
        assert self._sock is not None
        while True:
            try:
                data, _addr = await loop.sock_recvfrom(self._sock, 65535)
            except asyncio.CancelledError:
                raise
            except OSError:
                await asyncio.sleep(1.0)
                continue
            parsed = parse_ssdp(data)
            if parsed is None:
                continue
            serial, ip = parsed
            if self._by_serial.get(serial) == ip:
                continue
            self._by_serial[serial] = ip
            logger.info("SSDP: printer %s at %s", serial, ip)
            try:
                await self._on_found(serial, ip)
            except Exception:
                logger.exception("SSDP on_found handler failed")

    async def _search_loop(self) -> None:
        assert self._sock is not None
        while True:
            try:
                self._sock.sendto(_MSEARCH, (_MCAST_GRP, _BAMBU_PORT))
            except OSError as exc:
                logger.debug("SSDP M-SEARCH send failed: %s", exc)
            await asyncio.sleep(self._search_interval)
