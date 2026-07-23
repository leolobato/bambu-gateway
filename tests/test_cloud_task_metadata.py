from __future__ import annotations

import asyncio
import json

import pytest

from app.cloud.task_metadata import (
    fetch_subtask_metadata,
    normalize_subtask_metadata,
)
from app.cloud.cloud_printer import CloudPrinterClient
from app.config import PrinterConfig
from app.printer_service import PrinterService


def _task() -> dict:
    return {
        "title": "Cloud cube",
        "content": json.dumps({"info": {"plate_idx": 2}}),
        "context": {
            "plates": [
                {
                    "index": 1,
                    "filaments": [{"used_m": "99", "used_g": "99"}],
                },
                {
                    "index": 2,
                    "weight": 3.75,
                    "filaments": [
                        {
                            "type": "PLA",
                            "color": "#FFFFFF",
                            "used_m": "1.25",
                            "used_g": "3.2",
                        },
                        {
                            "type": "PETG",
                            "color": "#000000",
                            "used_m": "0.20",
                            "used_g": "0.55",
                        },
                    ],
                    "projectFile": (
                        "https://example.invalid/jobs/plate-2.3mf?sig=x"
                    ),
                    "thumbnail": {
                        "url": "https://example.invalid/plate-2.png"
                    },
                },
            ],
        },
    }


def test_normalizes_exact_plate_filament_lengths_and_3mf():
    reference, source_url = normalize_subtask_metadata(
        _task(), subtask_id="123",
    )

    assert reference == {
        "available": True,
        "source": "bambu_cloud",
        "subtask_id": "123",
        "title": "Cloud cube",
        "plate_index": 2,
        "total_weight_g": 3.75,
        "filaments": [
            {
                "filament_index": 0,
                "used_length_mm": 1250.0,
                "used_weight_g": 3.2,
                "type": "PLA",
                "color": "#FFFFFF",
            },
            {
                "filament_index": 1,
                "used_length_mm": 200.0,
                "used_weight_g": 0.55,
                "type": "PETG",
                "color": "#000000",
            },
        ],
        "length_validation_available": True,
        "reason": None,
    }
    assert source_url == "https://example.invalid/jobs/plate-2.3mf?sig=x"


def test_does_not_treat_thumbnail_or_non_3mf_download_as_print_source():
    task = _task()
    plate = task["context"]["plates"][1]
    plate.pop("projectFile")
    plate["downloadUrl"] = "https://example.invalid/model.zip?sig=x"

    _, source_url = normalize_subtask_metadata(task, subtask_id="123")

    assert source_url is None


@pytest.mark.asyncio
async def test_fetch_uses_plugin_session_without_python_token():
    class Host:
        async def call(self, method, params, timeout):
            assert method == "get_subtask_info"
            assert params == {"subtask_id": "123"}
            assert timeout == 20.0
            return {"task": _task()}

    reference, source_url = await fetch_subtask_metadata(
        host=Host(), subtask_id="123",
    )

    assert reference["filaments"][0]["used_length_mm"] == 1250.0
    assert source_url.endswith("plate-2.3mf?sig=x")


@pytest.mark.asyncio
async def test_cloud_report_is_enriched_for_the_same_active_job():
    class Host:
        async def call(self, method, params, timeout=60.0):
            assert method == "get_subtask_info"
            assert params == {"subtask_id": "123"}
            return {"task": _task()}

    service = PrinterService(
        [PrinterConfig(ip="", access_code="", serial="S1")],
        cloud_mode=True,
    )
    cloud = CloudPrinterClient(dev_id="S1")
    service.set_cloud_printers({"S1": cloud}, host=Host())

    await cloud.handle_event({
        "kind": "OnMessage",
        "dev_id": "S1",
        "payload": json.dumps({
            "print": {
                "gcode_state": "RUNNING",
                "task_id": "100",
                "subtask_id": "123",
                "subtask_name": "Cloud cube",
            },
        }),
    })
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    snapshot = service.get_print_event_broker("S1").snapshot()
    assert snapshot["job_key"] == "printer:100:123:Cloud cube"
    assert snapshot["usage_reference"]["filaments"][0][
        "used_length_mm"
    ] == 1250.0
    assert snapshot["source"]["kind"] == "cloud_http"
