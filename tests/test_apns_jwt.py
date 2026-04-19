"""Tests for APNs JWT generation."""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key, SECP256R1
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat,
)

from app.apns_jwt import ApnsJwtSigner


@pytest.fixture
def p8_key(tmp_path):
    """Generate a throwaway ES256 key in PEM (.p8) format."""
    key = generate_private_key(SECP256R1())
    pem = key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption(),
    )
    path = tmp_path / "AuthKey_TEST.p8"
    path.write_bytes(pem)
    return str(path)


def test_sign_produces_valid_jwt(p8_key):
    signer = ApnsJwtSigner(
        key_path=p8_key, key_id="KEYID1", team_id="TEAMXYZ",
    )
    token = signer.current_token()
    header = pyjwt.get_unverified_header(token)
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert header["alg"] == "ES256"
    assert header["kid"] == "KEYID1"
    assert payload["iss"] == "TEAMXYZ"
    assert "iat" in payload


def test_token_is_cached_within_window(p8_key):
    signer = ApnsJwtSigner(p8_key, "KEYID1", "TEAMXYZ")
    t1 = signer.current_token()
    t2 = signer.current_token()
    assert t1 == t2


def test_token_rotates_after_50_minutes(p8_key):
    signer = ApnsJwtSigner(p8_key, "KEYID1", "TEAMXYZ")
    t1 = signer.current_token()
    signer._issued_at = time.time() - (51 * 60)
    t2 = signer.current_token()
    assert t1 != t2
