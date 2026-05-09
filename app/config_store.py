"""Persistent printer configuration stored in printers.json."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import PrinterConfig, settings

logger = logging.getLogger(__name__)

_config_path: Path = Path("data/printers.json")


def set_path(path: str | Path) -> None:
    """Override the config file location (e.g. for Docker volumes)."""
    global _config_path
    _config_path = Path(path)


def _serialize(configs: list[PrinterConfig]) -> list[dict]:
    result = []
    for c in configs:
        d = {
            "serial": c.serial,
            "ip": c.ip,
            "access_code": c.access_code,
            "name": c.name,
        }
        if c.machine_model:
            d["machine_model"] = c.machine_model
        result.append(d)
    return result


def _deserialize(items: list[dict]) -> list[PrinterConfig]:
    return [
        PrinterConfig(
            serial=item["serial"],
            ip=item["ip"],
            access_code=item["access_code"],
            name=item.get("name", ""),
            machine_model=item.get("machine_model", ""),
        )
        for item in items
    ]


def save(configs: list[PrinterConfig]) -> None:
    """Write printer configs to printers.json."""
    _config_path.parent.mkdir(parents=True, exist_ok=True)
    _config_path.write_text(
        json.dumps(_serialize(configs), indent=2) + "\n"
    )
    logger.info("Saved %d printer config(s) to %s", len(configs), _config_path)


def load() -> list[PrinterConfig]:
    """Load printer configs from disk, seeding from env vars if needed.

    On first run (no printers.json), env var configs are written to disk
    so they become the persisted source of truth going forward.
    """
    if _config_path.exists():
        data = json.loads(_config_path.read_text())
        configs = _deserialize(data)
        logger.info("Loaded %d printer config(s) from %s", len(configs), _config_path)
        return configs

    # Seed from environment variables
    configs = settings.get_printers()
    if configs:
        save(configs)
        logger.info("Seeded printer config from environment variables")
    else:
        logger.info("No printers.json and no env vars — starting with empty config")

    return configs
