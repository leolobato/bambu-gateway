"""Cloud auth flow: sign-in URL, paste-callback parsing, canonical payload."""
from __future__ import annotations

import json as _json
import logging
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.cloud import bambu_studio_headers, region_api_base
from app.cloud.plugin_host import PluginHost, PluginHostError

logger = logging.getLogger("bambu.cloud.auth")

# These values mirror `pjarczak_browser_login_url()` in
# WebUserLoginDialog.cpp:64-76 (see Phase A discovery notes §A.1).
# The redirect_url is fixed because the plugin hardcodes its loopback
# callback URL — we cannot change it.
_LOOPBACK_REDIRECT = "http://localhost:13618"

_REGION_SIGNIN_BASE: dict[str, str] = {
    "US": "https://bambulab.com/sign-in",
    "CN": "https://bambulab.cn/sign-in",
}


def build_signin_url(*, region: str, locale: str = "en") -> str:
    """Build the Bambu hosted sign-in URL the user opens in their browser.

    Two-level URL: the outer is the public sign-in page, the ``to=`` query
    param is the inner callback URL that carries the ticket-flow markers.
    Mirrors OrcaSlicer's ``pjarczak_browser_login_url`` (Phase A §A.1).

    After the user signs in, Bambu redirects to
    ``http://localhost:13618/?ticket=<val>``. Because nothing is listening
    on that port from the user's browser, the browser shows a "connection
    refused" page — the user then copies the URL from their address bar
    and pastes it into the gateway's /api/cloud/auth/paste endpoint.
    """
    base = _REGION_SIGNIN_BASE.get(region)
    if base is None:
        raise ValueError(f"unsupported region: {region!r}")

    # Inner callback URL — carries the ticket-flow markers.
    inner_qs = urlencode({
        "source": "portal",
        "locale": locale,
        "redirect_url": _LOOPBACK_REDIRECT,
        "openBy": "suite",
        "from": "studio",
        "slicerLoginType": "ticket",
    })
    inner = f"{base}/callback?{inner_qs}"

    # Outer URL — carries `to=<inner-encoded>`.
    outer_qs = urlencode({
        "from": "studio",
        "source": "portal",
        "to": inner,
    })
    return f"{base}?{outer_qs}"


class PasteParseError(ValueError):
    """Raised when the user-pasted URL doesn't carry a usable ticket."""


def parse_paste_url(pasted: str) -> str:
    """Extract the ``ticket`` value from the URL the user pasted back.

    Bambu's sign-in page may put the ticket in either the query string (``?``)
    or the URL fragment (``#``); we try both.
    """
    try:
        parsed = urlparse(pasted)
    except Exception as exc:
        raise PasteParseError(f"not a URL: {pasted!r}") from exc

    # Real OAuth callbacks land on the loopback host; lock that down to catch
    # obviously-wrong pastes (a Google search result, say).
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise PasteParseError(
            f"expected a localhost redirect URL, got {parsed.hostname!r}"
        )

    qs: dict[str, list[str]] = {}
    if parsed.query:
        qs.update(parse_qs(parsed.query))
    if parsed.fragment:
        qs.update(parse_qs(parsed.fragment))

    ticket = qs.get("ticket")
    if not ticket:
        raise PasteParseError("pasted URL missing 'ticket' parameter")
    return ticket[0]


class ProfileFetchError(RuntimeError):
    """Raised when the Bambu profile API rejects the access token."""


# Bambu's user-service profile endpoint. The older `/v1/user-service/u/info`
# path 404s ("404 page not found"); `/my/profile` is the live route (returns
# 401 unauthenticated, i.e. exists) and carries uid/name/account/avatar.
_PROFILE_PATH = "/v1/user-service/my/profile"


async def fetch_profile(
    *, client: httpx.AsyncClient, region: str, access_token: str
) -> dict:
    """Fetch the user's profile from Bambu's REST API using the access token.

    Raises :class:`ProfileFetchError` on non-2xx responses.
    """
    response = await client.get(
        region_api_base(region) + _PROFILE_PATH,
        headers={
            "Authorization": f"Bearer {access_token}",
            **bambu_studio_headers(),
        },
        timeout=15.0,
    )
    if response.status_code != 200:
        raise ProfileFetchError(
            f"Bambu profile API returned {response.status_code}: "
            f"{response.text[:200]}"
        )
    return response.json()


def _first_present(d: dict, *keys: str) -> str | None:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return str(v)
    return None


def build_canonical_login(*, tokens: dict, profile: dict) -> str:
    """Assemble the canonical ``change_user`` payload from tokens + profile.

    Matches the schema in Phase 0 §Q11.4 (HttpServer.cpp:38-65). The string
    returned is what gets passed to ``plugin.change_user``. Accepts either
    snake_case or camelCase token field names (Bambu's APIs mix both).
    """
    access = _first_present(tokens, "access_token", "accessToken", "token")
    refresh = _first_present(tokens, "refresh_token", "refreshToken")
    expires = _first_present(tokens, "expires_in", "expiresIn")
    refresh_expires = _first_present(
        tokens, "refresh_expires_in", "refreshExpiresIn"
    )
    if access is None or refresh is None:
        raise ValueError("tokens must contain access_token and refresh_token")

    uid = _first_present(profile, "uidStr", "uid", "id")
    if uid is None:
        raise ValueError("profile must contain uidStr/uid/id")

    payload = {
        "command": "user_login",
        "data": {
            "token": access,
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": expires or "",
            "refresh_expires_in": refresh_expires or "",
            "user_id": uid,
            "uidStr": uid,
            "user": {
                "id": uid,
                "uid": uid,
                "uidStr": uid,
                "name": profile.get("name", ""),
                "account": profile.get("account", ""),
                "avatar": profile.get("avatar", ""),
            },
        },
    }
    return _json.dumps(payload)


class LoginFailed(RuntimeError):
    """Raised when change_user succeeded but the plugin doesn't report login."""


async def complete_login(
    *,
    host: PluginHost,
    http: httpx.AsyncClient,
    region: str,
    pasted_url: str,
) -> dict:
    """Drive the full paste-fallback login flow end-to-end.

    Returns the profile dict on success. Raises:

    - :class:`PasteParseError` if the URL doesn't carry a usable ticket
    - :class:`PluginHostError` if the plugin host RPC fails (e.g. get_my_token
      rejects the ticket)
    - :class:`ProfileFetchError` if Bambu rejects the access token
    - :class:`LoginFailed` if change_user returned 0 but is_user_login is False
    """
    ticket = parse_paste_url(pasted_url)
    tokens = await host.call("get_my_token", {"ticket": ticket})

    access = _first_present(tokens, "access_token", "accessToken", "token")
    if access is None:
        raise LoginFailed("get_my_token result missing access_token")
    profile = await fetch_profile(
        client=http, region=region, access_token=access
    )
    canonical = build_canonical_login(tokens=tokens, profile=profile)

    change_result = await host.call("change_user", {"canonical_login": canonical})
    rc = change_result.get("rc", -1)
    if rc != 0:
        raise LoginFailed(f"plugin change_user returned rc={rc}")

    status = await host.call("is_user_login", {})
    if not status.get("is_login"):
        raise LoginFailed("plugin did not register login after change_user")

    logger.info(
        "Bambu user signed in: account=%s uid=%s",
        profile.get("account"),
        profile.get("uidStr") or profile.get("uid") or profile.get("id"),
    )
    return profile


async def logout(*, host: PluginHost) -> None:
    """Sign out, telling the plugin to also notify Bambu's backend."""
    await host.call("user_logout", {"with_backend_notify": True})


async def is_signed_in(*, host: PluginHost) -> bool:
    """Return True if the plugin currently has a valid logged-in user."""
    result = await host.call("is_user_login", {})
    return bool(result.get("is_login"))
