"""FastAPI routes for /api/cloud/auth/*."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.cloud import auth
from app.cloud.plugin_host import PluginHostError
from app.config import settings

logger = logging.getLogger("bambu.cloud.auth.routes")

router = APIRouter(prefix="/api/cloud/auth", tags=["cloud-auth"])


def _make_http_client() -> httpx.AsyncClient:
    """Module-level factory so tests can monkeypatch it."""
    return httpx.AsyncClient(timeout=30.0)


def _require_host(request: Request):
    host = getattr(request.app.state, "cloud_host", None)
    if host is None:
        raise HTTPException(
            status_code=503,
            detail="cloud mode not active (BAMBU_CLOUD_ENABLED=false)",
        )
    return host


@router.get("/url")
async def get_signin_url() -> dict:
    return {"url": auth.build_signin_url(region=settings.bambu_cloud_region)}


@router.get("/status")
async def get_status(request: Request) -> dict:
    host = _require_host(request)
    try:
        signed_in = await auth.is_signed_in(host=host)
    except PluginHostError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"signed_in": signed_in}


class PasteBody(BaseModel):
    pasted_url: str


@router.post("/paste")
async def post_paste(request: Request, body: PasteBody) -> dict:
    host = _require_host(request)
    async with _make_http_client() as http:
        try:
            profile = await auth.complete_login(
                host=host,
                http=http,
                region=settings.bambu_cloud_region,
                pasted_url=body.pasted_url,
            )
        except auth.PasteParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except auth.ProfileFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (auth.LoginFailed, PluginHostError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Login succeeded — connect the cloud MQTT relay and subscribe printers so
    # status starts flowing. A connect failure doesn't invalidate the login;
    # surface it in the response instead of failing the request.
    connected = False
    connect = getattr(request.app.state, "cloud_connect", None)
    if connect is not None:
        try:
            connected = await connect()
        except PluginHostError as exc:
            logger.warning("cloud connect after login failed: %s", exc)
    return {"profile": profile, "connected": connected}


@router.post("/logout")
async def post_logout(request: Request) -> dict:
    host = _require_host(request)
    try:
        await auth.logout(host=host)
    except PluginHostError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True}
