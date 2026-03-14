"""Allow running the app with `python -m app`."""

import argparse

import uvicorn

from app.config import settings
from app import config_store

parser = argparse.ArgumentParser(description="Bambu Gateway")
parser.add_argument(
    "-c", "--config",
    default="printers.json",
    help="path to printers.json config file (default: ./printers.json)",
)
args = parser.parse_args()

config_store.set_path(args.config)

uvicorn.run("app.main:app", host=settings.server_host, port=settings.server_port)
