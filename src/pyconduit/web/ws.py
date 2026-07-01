"""The /ws WebSocket handler.

Authenticates the connection (proxy header or dev ?user=), attaches a Subscriber
to the shared Account, then pumps JSON frames both ways until the socket closes.
Errors in a single frame are reported to that tab without tearing down the socket.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .. import protocol
from ..auth import AuthError
from ..protocol import ProtocolError

log = logging.getLogger("pyconduit.ws")


async def handle_ws(websocket: WebSocket) -> None:
    app = websocket.app
    cfg = app.state.config
    mapper = app.state.mapper
    audit = app.state.audit
    manager = app.state.manager

    ip = websocket.client.host if websocket.client else "unknown"

    await websocket.accept()

    # --- authenticate ---------------------------------------------------------
    try:
        from ..auth import extract_username

        username = extract_username(
            mode=cfg.auth.mode.value,
            header_name=cfg.auth.header,
            headers=dict(websocket.headers),
            query_user=websocket.query_params.get("user"),
            dev_default_user=cfg.auth.dev_default_user,
        )
        identity = mapper.resolve(username)
    except AuthError as exc:
        audit.auth_denied(username=locals().get("username"), ip=ip, reason=str(exc))
        await websocket.send_json(
            protocol.server_error(context="auth", message=f"Not authorized: {exc}")
        )
        await websocket.close(code=1008)
        return

    audit.auth_ok(username=identity.username, jid=identity.jid, ip=ip)

    async def send(frame: dict) -> None:
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.send_json(frame)

    from ..session.hub import Subscriber

    sub = Subscriber(username=identity.username, ip=ip, send=send)
    account = await manager.attach(jid=identity.jid, password=identity.password, sub=sub)

    # --- pump -----------------------------------------------------------------
    try:
        while True:
            raw = await websocket.receive_json()
            try:
                msg = protocol.parse_client_message(raw)
                await account.handle_command(sub, msg)
            except ProtocolError as exc:
                await send(protocol.server_error(context="protocol", message=str(exc)))
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("ws error for %s", identity.jid)
    finally:
        await manager.detach(account, sub)
