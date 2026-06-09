"""Shared cloud print submission used by every print entry point.

One place builds the plugin's ``start_print`` params and drives the frame
stream, so /api/print, /api/print-stream and the slice-job manager cannot
drift apart (they previously carried near-verbatim copies of this block).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import aclosing
from pathlib import Path
from typing import AsyncIterator, Sequence

from app.cloud.cloud_printer import CloudPrinterClient
from app.cloud.print_params import build_print_params


async def submit_cloud_print(
    *,
    cloud_client: CloudPrinterClient,
    filename: str,
    plate_index: int,
    ams_mapping: Sequence[int] | None,
    use_ams: bool,
    file_path: str | Path | None = None,
    file_data: bytes | None = None,
) -> AsyncIterator[dict]:
    """Submit a sliced 3MF to a cloud printer, yielding submit_print frames.

    Exactly one of ``file_path`` / ``file_data`` must be given. The plugin
    uploads from disk, so a path is passed through untouched while bytes are
    written to a temp file off the event loop and removed afterwards.

    Frames are those of :meth:`CloudPrinterClient.submit_print`; consumers
    should iterate under ``contextlib.aclosing`` so breaking out releases the
    printer's in-flight slot immediately.
    """
    if (file_path is None) == (file_data is None):
        raise ValueError("provide exactly one of file_path or file_data")

    tmp_path: str | None = None
    try:
        if file_path is None:
            suffix = Path(filename).suffix or ".3mf"

            def _write_tmp() -> str:
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=suffix, prefix="bambu_cloud_",
                ) as tmp:
                    tmp.write(file_data)
                    return tmp.name

            tmp_path = await asyncio.to_thread(_write_tmp)
            gcode_path = tmp_path
        else:
            gcode_path = str(file_path)

        print_params = build_print_params(
            dev_id=cloud_client.serial,
            project_name=Path(filename).stem,
            plate_index=plate_index,
            gcode_3mf_path=gcode_path,
            ams_mapping=list(ams_mapping) if ams_mapping else None,
            use_ams=use_ams,
        )
        async with aclosing(
            cloud_client.submit_print(print_params=print_params)
        ) as frames:
            async for frame in frames:
                yield frame
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def run_cloud_print(**kwargs) -> dict | None:
    """Drive :func:`submit_cloud_print` to its terminal frame.

    Returns ``None`` when the job reached the printer, or the error frame
    (``{"event": "error", "code": N, "msg": ...}``) on failure. For callers
    that don't relay intermediate progress (non-streaming routes, the
    slice-job manager).
    """
    async with aclosing(submit_cloud_print(**kwargs)) as frames:
        async for frame in frames:
            event = frame.get("event")
            if event == "error":
                return frame
            if event == "done":
                return None
    return {
        "event": "error",
        "code": -99,
        "msg": "Cloud submission ended without a terminal frame",
    }
