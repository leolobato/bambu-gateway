"""SSDP parsing tests using a real captured Bambu A1 Mini advertisement."""
from __future__ import annotations

from app.ssdp import parse_ssdp

_NOTIFY = (
    b"NOTIFY * HTTP/1.1\r\n"
    b"HOST: 239.255.255.250:1900\r\n"
    b"Server: UPnP/1.0\r\n"
    b"Location: 10.0.1.157\r\n"
    b"NT: urn:bambulab-com:device:3dprinter:1\r\n"
    b"USN: 0309DA561103403\r\n"
    b"DevModel.bambu.com: N1\r\n"
    b"DevName.bambu.com: A1 Mini\r\n\r\n"
)


def test_parse_ssdp_extracts_serial_and_ip():
    assert parse_ssdp(_NOTIFY) == ("0309DA561103403", "10.0.1.157")


def test_parse_ssdp_ignores_non_bambu_packet():
    msearch = (
        b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
        b'MAN: "ssdp:discover"\r\nST: upnp:rootdevice\r\nMX: 5\r\n\r\n'
    )
    assert parse_ssdp(msearch) is None


def test_parse_ssdp_strips_url_style_location():
    pkt = _NOTIFY.replace(
        b"Location: 10.0.1.157",
        b"Location: http://10.0.1.157:8080/description.xml",
    )
    assert parse_ssdp(pkt) == ("0309DA561103403", "10.0.1.157")
