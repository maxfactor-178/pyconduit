"""FastAPI application factory and wiring.

Holds shared singletons (config, auth mapper, audit log, account manager) on
``app.state`` and mounts the routes and the static frontend. Everything XMPP is
reached through the AccountManager, so this module stays framework-only.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..audit import AuditLog
from ..auth import AuthMapper
from ..config import Config, load_config
from ..session.manager import AccountManager
from .routes import router

log = logging.getLogger("pyconduit")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(config: Config | None = None, config_path: str | None = None) -> FastAPI:
    cfg = config or load_config(config_path)
    audit = AuditLog(cfg.audit)
    mapper = AuthMapper.from_files(cfg.auth.users_file, cfg.auth.credentials_file)
    manager = AccountManager(cfg, audit)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("PyConduit starting on %s:%s", cfg.server.host, cfg.server.port)
        yield
        await manager.shutdown()
        audit.close()

    app = FastAPI(title=cfg.server.brand_title, lifespan=lifespan)
    app.state.config = cfg
    app.state.audit = audit
    app.state.mapper = mapper
    app.state.manager = manager

    app.include_router(router)
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app
