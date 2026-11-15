"""Polls bridge.poll_events and dispatches events to registered handlers."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Mapping

from app.cloud.plugin_host import PluginHost, PluginHostError

logger = logging.getLogger("bambu.cloud.event_pump")

Handler = Callable[[dict], Awaitable[None]]


class EventPump:
    """Background task that polls the host for events and dispatches them.

    Cadence: ``active_interval`` (default 80ms) while events are flowing,
    ``idle_interval`` (default 250ms) when the queue is empty. The default
    cadence matches OrcaSlicer-bambulab's PJarczak bridge.
    """

    def __init__(
        self,
        *,
        host: PluginHost,
        handlers: Mapping[str, Handler],
        active_interval: float = 0.08,
        idle_interval: float = 0.25,
    ) -> None:
        self._host = host
        self._handlers = dict(handlers)
        self._active = active_interval
        self._idle = idle_interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="bambu-cloud-event-pump"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        failures = 0
        last_logged = 0.0
        while not self._stop.is_set():
            try:
                result = await self._host.call("bridge.poll_events", {})
                events: list[dict] = result.get("events", []) or []
            except PluginHostError as exc:
                # Log the start of an outage once, not 4x/s: the per-poll
                # warning flood rotated the original crash out of the journal.
                # Recovering a dead host is the cloud supervisor's job.
                # (Production runs at DEBUG, so demoting isn't enough — only
                # a once-a-minute reminder while the outage lasts.)
                now = time.monotonic()
                if failures == 0 or now - last_logged >= 60.0:
                    logger.warning(
                        "poll_events failed (%d so far): %s; will retry",
                        failures + 1, exc,
                    )
                    last_logged = now
                failures += 1
                await asyncio.sleep(self._idle)
                continue
            if failures:
                logger.info(
                    "poll_events recovered after %d failed polls", failures
                )
                failures = 0
            for ev in events:
                kind = ev.get("kind")
                handler = self._handlers.get(kind)
                if handler is None:
                    logger.debug("no handler for event kind=%r", kind)
                    continue
                try:
                    await handler(ev)
                except Exception:
                    logger.exception("handler for %r raised", kind)
            # Cadence: active when we drained at least one event.
            await asyncio.sleep(self._active if events else self._idle)
