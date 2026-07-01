"""Entrypoint: ``python -m pyconduit [config.yaml]``.

Loads config, builds the FastAPI app, and runs uvicorn. slixmpp and FastAPI both
use asyncio; uvicorn owns the event loop.
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from .config import load_config
from .web.app import create_app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("PYCONDUIT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "PYCONDUIT_CONFIG", "config.dev.yaml"
    )
    cfg = load_config(config_path)
    app = create_app(config=cfg)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="info")


if __name__ == "__main__":
    main()
