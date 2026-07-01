"""HTTP + WebSocket routes.

  * GET /healthz — liveness, no auth.
  * GET /readyz  — readiness, no auth.
  * WS  /ws      — authenticated chat socket.

The static frontend at ``/`` is mounted by the app factory. In production the
reverse proxy authenticates ``/`` and ``/ws`` and overwrites the auth header;
in dev mode ``?user=alice`` selects the identity.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, WebSocket

from .ws import handle_ws

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> dict:
    # Ready once the auth mapping loaded and the manager exists.
    _ = request.app.state.mapper
    _ = request.app.state.manager
    return {"status": "ready"}


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await handle_ws(websocket)
