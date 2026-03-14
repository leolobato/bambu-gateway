"""Application configuration loaded from environment variables."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_settings import BaseSettings


@dataclass
class PrinterConfig:
    """Connection details for a single Bambu Lab printer."""

    ip: str
    access_code: str
    serial: str
    name: str = ""
    machine_model: str = ""


class Settings(BaseSettings):
    """Application settings parsed from environment variables.

    Printer lists are provided as comma-separated strings and split into
    individual ``PrinterConfig`` entries via ``get_printers()``.
    """

    # Printer config (comma-separated for multi-printer support)
    bambu_printer_ip: str = ""
    bambu_printer_access_code: str = ""
    bambu_printer_serial: str = ""

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 4844

    # Logging
    log_level: str = "INFO"

    # OrcaSlicer CLI API
    orcaslicer_api_url: str = ""

    # Upload limits
    max_file_size_mb: int = 200

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def get_printers(self) -> list[PrinterConfig]:
        """Parse comma-separated printer env vars into a list of configs."""
        ips = [s.strip() for s in self.bambu_printer_ip.split(",") if s.strip()]
        codes = [s.strip() for s in self.bambu_printer_access_code.split(",") if s.strip()]
        serials = [s.strip() for s in self.bambu_printer_serial.split(",") if s.strip()]

        if not ips:
            return []

        count = len(ips)
        if len(codes) != count or len(serials) != count:
            raise ValueError(
                f"Printer config mismatch: {count} IPs, {len(codes)} access codes, "
                f"{len(serials)} serials. All lists must have the same length."
            )

        return [
            PrinterConfig(ip=ip, access_code=code, serial=serial)
            for ip, code, serial in zip(ips, codes, serials)
        ]


settings = Settings()
