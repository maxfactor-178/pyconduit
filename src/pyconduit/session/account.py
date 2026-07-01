"""An Account owns one shared XMPP connection for a bare JID.

Multiple browser tabs (Subscribers) — possibly belonging to different humans
sharing the account — attach to it. The account:

  * lazily connects on first attach and reconnects with exponential backoff,
  * translates inbound XMPP events into JSON frames broadcast to every tab,
  * routes tab commands to the XMPP client and echoes sent messages to all tabs,
  * keeps a small cache (roster/presence/joined rooms) to replay to a late tab,
  * stays alive for a configurable idle period after the last tab leaves.

It speaks only the xmpp.interface dataclasses, so it is unit-testable with a fake
client and has no knowledge of slixmpp or WebSockets.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC

from .. import protocol
from ..audit import AuditLog
from ..config import Config
from ..xmpp import interface as ifc
from ..xmpp.interface import XmppClient
from .hub import Subscriber

log = logging.getLogger("pyconduit.account")

# Factory so tests can inject a fake client: (jid, password, cfg, on_event) -> XmppClient
ClientFactory = Callable[[str, str, "Config", ifc.EventHandler], XmppClient]


class Account:
    def __init__(
        self,
        *,
        jid: str,
        password: str,
        config: Config,
        audit: AuditLog,
        client_factory: ClientFactory,
        on_idle_expired: Callable[[Account], Awaitable[None]] | None = None,
    ):
        self.jid = jid
        self._password = password
        self._cfg = config
        self._audit = audit
        self._on_idle_expired = on_idle_expired

        self._client: XmppClient = client_factory(jid, password, config, self._on_event)
        self._subscribers: set[Subscriber] = set()

        # Replayable state for late-joining tabs.
        self._roster: list[dict] = []
        self._presence: dict[str, dict] = {}
        self._joined_rooms: dict[str, str] = {}  # room -> nick

        self._idle_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._closing = False

    # --- subscriber lifecycle --------------------------------------------------

    async def attach(self, sub: Subscriber) -> None:
        self._cancel_idle()
        self._subscribers.add(sub)
        await self.ensure_connected()
        await self._send_snapshot(sub)
        self._audit.session_open(
            username=sub.username, jid=self.jid, ip=sub.ip, tabs=len(self._subscribers)
        )

    async def detach(self, sub: Subscriber) -> None:
        self._subscribers.discard(sub)
        self._audit.session_close(
            username=sub.username, jid=self.jid, ip=sub.ip, tabs=len(self._subscribers)
        )
        if not self._subscribers:
            self._start_idle_timer()

    @property
    def tab_count(self) -> int:
        return len(self._subscribers)

    # --- connection ------------------------------------------------------------

    async def ensure_connected(self) -> None:
        async with self._connect_lock:
            if self._client.is_connected:
                return
            try:
                await self._client.connect()
            except Exception as exc:  # noqa: BLE001 — surface to tabs, then back off
                self._audit.xmpp_disconnect(jid=self.jid, reason=f"connect failed: {exc}")
                await self._broadcast(
                    protocol.server_error(
                        context="connection", message=f"Cannot reach chat server: {exc}"
                    )
                )
                self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            return
        if self._closing or not self._subscribers:
            return
        self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        rc = self._cfg.xmpp.reconnect
        delay = rc.initial_seconds
        while not self._closing and self._subscribers and not self._client.is_connected:
            await asyncio.sleep(delay)
            if self._closing or not self._subscribers:
                return
            try:
                await self._client.connect()
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("reconnect to %s failed: %s", self.jid, exc)
                delay = min(delay * rc.factor, rc.max_seconds)

    # --- inbound XMPP events -> JSON frames ------------------------------------

    async def _on_event(self, event: object) -> None:
        if isinstance(event, ifc.Connected):
            self._audit.xmpp_connect(jid=self.jid)
            await self._client.set_presence("online")
            # Re-join rooms after a reconnect.
            for room, nick in list(self._joined_rooms.items()):
                try:
                    await self._client.join_room(room, nick)
                except Exception:  # noqa: BLE001
                    pass

        elif isinstance(event, ifc.Disconnected):
            self._audit.xmpp_disconnect(jid=self.jid, reason=event.reason)
            await self._broadcast(
                protocol.server_error(
                    context="connection", message="Chat server connection lost; reconnecting…"
                )
            )
            self._schedule_reconnect()

        elif isinstance(event, ifc.IncomingMessage):
            peer = event.from_jid
            await self._broadcast(
                protocol.server_message(
                    conversation=peer,
                    from_jid=event.from_jid,
                    to_jid=event.to_jid,
                    body=event.body,
                    msg_id=event.msg_id,
                    timestamp=event.timestamp,
                    direction="incoming",
                )
            )

        elif isinstance(event, ifc.DeliveryFailure):
            await self._broadcast(
                protocol.server_error(
                    context="delivery",
                    conversation=event.to_jid,
                    message=f"Message to {event.to_jid} could not be delivered: {event.error}",
                )
            )

        elif isinstance(event, ifc.PresenceUpdate):
            frame = protocol.server_presence(
                jid=event.jid, show=event.show, status=event.status
            )
            self._presence[event.jid] = frame
            await self._broadcast(frame)

        elif isinstance(event, ifc.RosterSnapshot):
            self._roster = [
                {"jid": i.jid, "name": i.name, "subscription": i.subscription}
                for i in event.items
            ]
            await self._broadcast(protocol.server_roster(contacts=self._roster))

        elif isinstance(event, ifc.SubscriptionRequest):
            await self._broadcast(protocol.server_subscription_request(jid=event.from_jid))

        elif isinstance(event, ifc.MucJoined):
            await self._broadcast(
                protocol.server_muc_joined(
                    room=event.room, nick=event.nick, subject=event.subject
                )
            )

        elif isinstance(event, ifc.MucMessage):
            await self._broadcast(
                protocol.server_muc_message(
                    room=event.room,
                    nick=event.nick,
                    body=event.body,
                    msg_id=event.msg_id,
                    timestamp=event.timestamp,
                    is_self=event.is_self,
                )
            )

        elif isinstance(event, ifc.MucOccupants):
            await self._broadcast(
                protocol.server_muc_occupants(room=event.room, occupants=event.occupants)
            )

        elif isinstance(event, ifc.MucPresence):
            await self._broadcast(
                protocol.server_muc_presence(
                    room=event.room, nick=event.nick, joined=event.joined
                )
            )

    # --- inbound tab commands --------------------------------------------------

    async def handle_command(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        handler = getattr(self, f"_cmd_{msg.type}", None)
        if handler is None:
            await sub.send(
                protocol.server_error(context="protocol", message=f"unknown command: {msg.type}")
            )
            return
        await handler(sub, msg)

    async def _cmd_send_message(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        msg.require("to", "body")
        to_jid = msg.data["to"]
        body = msg.data["body"]
        try:
            msg_id = await self._client.send_message(to_jid, body)
        except Exception as exc:  # noqa: BLE001
            await sub.send(
                protocol.server_error(
                    context="delivery", conversation=to_jid,
                    message=f"Could not send message: {exc}",
                )
            )
            return
        # Echo to ALL tabs so multiple tabs of this account stay in sync.
        await self._broadcast(
            protocol.server_message(
                conversation=to_jid,
                from_jid=self.jid,
                to_jid=to_jid,
                body=body,
                msg_id=msg_id,
                timestamp=_now_iso(),
                direction="outgoing",
            )
        )

    async def _cmd_load_history(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        msg.require("jid")
        peer = msg.data["jid"]
        before = msg.data.get("before")
        messages, complete = await self._client.fetch_history(
            peer, before, self._cfg.history.page_size
        )
        payload = [
            {
                "conversation": peer,
                "from": m.from_jid,
                "to": m.to_jid,
                "body": m.body,
                "id": m.msg_id,
                "timestamp": m.timestamp,
                "direction": "outgoing" if m.from_jid == self.jid else "incoming",
            }
            for m in messages
        ]
        await sub.send(
            protocol.server_history(conversation=peer, messages=payload, complete=complete)
        )

    async def _cmd_set_presence(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        show = msg.data.get("show", "online")
        await self._client.set_presence(show, msg.data.get("status"))

    async def _cmd_add_contact(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        msg.require("jid")
        await self._client.add_contact(msg.data["jid"], msg.data.get("name"))

    async def _cmd_remove_contact(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        msg.require("jid")
        await self._client.remove_contact(msg.data["jid"])

    async def _cmd_subscription(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        msg.require("jid", "action")
        await self._client.respond_subscription(
            msg.data["jid"], accept=msg.data["action"] == "accept"
        )

    async def _cmd_join_room(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        msg.require("room")
        room = msg.data["room"]
        nick = msg.data.get("nick") or self.jid.split("@", 1)[0]
        self._joined_rooms[room] = nick
        await self._client.join_room(room, nick)

    async def _cmd_leave_room(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        msg.require("room")
        room = msg.data["room"]
        self._joined_rooms.pop(room, None)
        await self._client.leave_room(room)

    async def _cmd_send_muc(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        msg.require("room", "body")
        try:
            await self._client.send_muc(msg.data["room"], msg.data["body"])
        except Exception as exc:  # noqa: BLE001
            await sub.send(
                protocol.server_error(
                    context="delivery", conversation=msg.data["room"],
                    message=f"Could not send to room: {exc}",
                )
            )

    async def _cmd_disco_rooms(self, sub: Subscriber, msg: protocol.ClientMessage) -> None:
        msg.require("server")
        result = await self._client.disco_rooms(msg.data["server"])
        await sub.send(
            protocol.server_disco_rooms(server=result.server, rooms=result.rooms)
        )

    # --- helpers ---------------------------------------------------------------

    async def _send_snapshot(self, sub: Subscriber) -> None:
        await sub.send(
            protocol.server_ready(
                jid=self.jid,
                username=sub.username,
                brand_title=self._cfg.server.brand_title,
                sound_default=self._cfg.ui.sound_enabled_default,
            )
        )
        if self._roster:
            await sub.send(protocol.server_roster(contacts=self._roster))
        for frame in self._presence.values():
            await sub.send(frame)
        for room, nick in self._joined_rooms.items():
            await sub.send(protocol.server_muc_joined(room=room, nick=nick))

    async def _broadcast(self, frame: dict) -> None:
        for sub in list(self._subscribers):
            try:
                await sub.send(frame)
            except Exception:  # noqa: BLE001 — a dead tab shouldn't block others
                self._subscribers.discard(sub)

    def _start_idle_timer(self) -> None:
        self._cancel_idle()
        self._idle_task = asyncio.ensure_future(self._idle_countdown())

    def _cancel_idle(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    async def _idle_countdown(self) -> None:
        try:
            await asyncio.sleep(self._cfg.xmpp.idle_timeout_seconds)
        except asyncio.CancelledError:
            return
        if self._subscribers:  # someone re-attached during the wait
            return
        await self.close()
        if self._on_idle_expired:
            await self._on_idle_expired(self)

    async def close(self) -> None:
        self._closing = True
        self._cancel_idle()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        try:
            await self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()
