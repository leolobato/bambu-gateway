"""Generate and cache APNs provider auth JWTs (ES256)."""

from __future__ import annotations

import threading
import time

import jwt as pyjwt

_MAX_TOKEN_AGE_SECONDS = 50 * 60  # rotate before Apple's 60-minute hard cap


class ApnsJwtSigner:
    """Produces a fresh JWT for each push when the cached one ages out."""

    def __init__(self, key_path: str, key_id: str, team_id: str) -> None:
        self._key_path = key_path
        self._key_id = key_id
        self._team_id = team_id
        self._lock = threading.Lock()
        self._cached_token: str | None = None
        self._issued_at: float = 0.0

    def current_token(self) -> str:
        with self._lock:
            now = time.time()
            if (
                self._cached_token is not None
                and now - self._issued_at < _MAX_TOKEN_AGE_SECONDS
            ):
                return self._cached_token
            with open(self._key_path, "rb") as fh:
                key = fh.read()
            token = pyjwt.encode(
                {"iss": self._team_id, "iat": int(now)},
                key,
                algorithm="ES256",
                headers={"kid": self._key_id},
            )
            self._cached_token = token
            self._issued_at = now
            return token
