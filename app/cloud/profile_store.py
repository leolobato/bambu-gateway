"""Persist the signed-in Bambu account profile to disk.

The profile is only obtainable during login (it needs the access token, which
the plugin does not re-expose). Caching it here lets the UI show which account
is linked after a gateway restart.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("bambu.cloud.profile")


@dataclass(frozen=True)
class CloudProfile:
    name: str = ""
    account: str = ""
    avatar: str = ""
    uid: str = ""

    @classmethod
    def from_api(cls, profile: dict) -> "CloudProfile":
        uid = profile.get("uidStr") or profile.get("uid") or profile.get("id") or ""
        return cls(
            name=str(profile.get("name", "") or ""),
            account=str(profile.get("account", "") or ""),
            avatar=str(profile.get("avatar", "") or ""),
            uid=str(uid),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def save_profile(path: Path, profile: CloudProfile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile.to_dict()))


def load_profile(path: Path) -> CloudProfile | None:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
    except OSError as exc:
        logger.warning("could not read cloud profile cache: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    return CloudProfile(
        name=str(data.get("name", "")),
        account=str(data.get("account", "")),
        avatar=str(data.get("avatar", "")),
        uid=str(data.get("uid", "")),
    )


def clear_profile(path: Path) -> None:
    path.unlink(missing_ok=True)
