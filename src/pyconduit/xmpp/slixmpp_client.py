"""Concrete slixmpp implementation of the XmppClient interface.

This is the ONLY module that imports slixmpp. It translates slixmpp events into
the plain dataclasses from ``interface`` and exposes imperative async methods.
Connection retry/backoff is intentionally NOT handled here — the session layer
owns that so it can emit its own lifecycle events; slixmpp auto-reconnect is off.
"""

from __future__ import annotations

import asyncio
import ssl
from datetime import UTC, datetime

import slixmpp
from slixmpp import JID
from slixmpp.exceptions import IqError, IqTimeout

from ..config import XmppConfig
from . import interface as ifc
from .interface import (
    ArchivedMessage,
    Connected,
    Disconnected,
    DiscoveredRooms,
    EventHandler,
    IncomingMessage,
    MucJoined,
    MucMessage,
    MucOccupants,
    MucPresence,
    PresenceUpdate,
    RosterItem,
    RosterSnapshot,
    Show,
    SubscriptionRequest,
    XmppClient,
)

# slixmpp presence 'show' -> our normalized Show
_SHOW_MAP: dict[str, Show] = {
    "": "online",
    "chat": "online",
    "away": "away",
    "xa": "xa",
    "dnd": "dnd",
}

# When a contact is logged in from several devices, pick the "most available" one.
_SHOW_PRIORITY = {"online": 0, "away": 1, "xa": 2, "dnd": 3}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _stamp_to_iso(stamp) -> str:
    if isinstance(stamp, datetime):
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp.astimezone(UTC).isoformat()
    return str(stamp) if stamp else _now_iso()


class SlixmppClient(XmppClient):
    def __init__(self, jid: str, password: str, config: XmppConfig, on_event: EventHandler):
        super().__init__(on_event)
        self._bare = JID(jid).bare
        self._cfg = config
        self._connected = False
        self._joined_rooms: dict[str, str] = {}  # room bare JID -> our nick
        # Presence tracked per full JID: {bare: {resource: show}}. A contact is
        # online if ANY of their resources is; only the last one leaving = offline.
        self._presence_resources: dict[str, dict[str, Show]] = {}
        self._loop = asyncio.get_event_loop()

        xmpp = slixmpp.ClientXMPP(jid, password)
        self._xmpp = xmpp
        xmpp.auto_reconnect = False

        # Plugins: disco, ping, MUC, MAM, data forms, RSM.
        for p in ("xep_0030", "xep_0199", "xep_0045", "xep_0313", "xep_0004", "xep_0059"):
            xmpp.register_plugin(p)

        if not config.verify_certs:
            xmpp.ssl_context.check_hostname = False
            xmpp.ssl_context.verify_mode = ssl.CERT_NONE

        # In a plaintext dev setup (no TLS) slixmpp would refuse PLAIN auth over an
        # unencrypted stream; allow it explicitly. Never enabled when TLS is on.
        if not config.tls:
            xmpp["feature_mechanisms"].unencrypted_plain = True

        xmpp.add_event_handler("session_start", self._on_session_start)
        xmpp.add_event_handler("disconnected", self._on_disconnected)
        xmpp.add_event_handler("failed_auth", self._on_failed_auth)
        xmpp.add_event_handler("message", self._on_message)
        xmpp.add_event_handler("presence_available", self._on_presence)
        xmpp.add_event_handler("presence_unavailable", self._on_presence)
        xmpp.add_event_handler("changed_status", self._on_presence)
        xmpp.add_event_handler("presence_subscribe", self._on_subscribe)
        xmpp.add_event_handler("roster_update", self._on_roster_update)
        xmpp.add_event_handler("groupchat_message", self._on_groupchat_message)
        xmpp.add_event_handler("groupchat_presence", self._on_groupchat_presence)

        self._session_ready = asyncio.Event()
        self._session_failed: asyncio.Future | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    # --- lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        self._session_ready.clear()
        self._session_failed = self._loop.create_future()
        self._xmpp.connect(host=self._cfg.host, port=self._cfg.port)
        # Wait until either the session starts or auth/connection fails.
        ready = asyncio.ensure_future(self._session_ready.wait())
        done, pending = await asyncio.wait(
            {ready, self._session_failed}, return_when=asyncio.FIRST_COMPLETED, timeout=30
        )
        for t in pending:
            t.cancel()
        if self._session_failed.done() and not self._session_failed.cancelled():
            exc = self._session_failed.exception()
            if exc is not None:
                raise exc
        if not self._session_ready.is_set():
            raise ConnectionError("XMPP connection timed out")

    async def disconnect(self) -> None:
        self._connected = False
        self._xmpp.disconnect()

    async def _on_session_start(self, _event) -> None:
        self._xmpp.send_presence()
        try:
            await self._xmpp.get_roster()
        except (IqError, IqTimeout):
            pass
        self._connected = True
        self._session_ready.set()
        await self._on_event(Connected(jid=self._bare))
        await self._emit_roster()

    async def _on_disconnected(self, reason) -> None:
        was = self._connected
        self._connected = False
        if self._session_failed is not None and not self._session_failed.done():
            self._session_failed.set_exception(ConnectionError(str(reason) or "disconnected"))
        if was:
            await self._on_event(Disconnected(reason=str(reason) or ""))

    async def _on_failed_auth(self, _event) -> None:
        if self._session_failed is not None and not self._session_failed.done():
            self._session_failed.set_exception(PermissionError("XMPP authentication failed"))

    # --- 1:1 messaging ---------------------------------------------------------

    async def send_message(self, to_jid: str, body: str) -> str:
        msg_id = self._xmpp.new_id()
        msg = self._xmpp.make_message(mto=JID(to_jid).bare, mbody=body, mtype="chat")
        msg["id"] = msg_id
        msg.send()
        return msg_id

    async def _on_message(self, msg) -> None:
        mtype = msg["type"]
        if mtype == "error":
            err = msg["error"]["text"] or msg["error"]["condition"] or "delivery failed"
            await self._on_event(
                ifc.DeliveryFailure(
                    to_jid=JID(msg["from"]).bare, error=str(err), msg_id=msg["id"] or None
                )
            )
            return
        if mtype not in ("chat", "normal"):
            return
        body = msg["body"]
        if not body:
            return
        await self._on_event(
            IncomingMessage(
                from_jid=JID(msg["from"]).bare,
                to_jid=JID(msg["to"]).bare,
                body=body,
                msg_id=msg["id"] or self._xmpp.new_id(),
                timestamp=_now_iso(),
            )
        )

    # --- presence --------------------------------------------------------------

    async def set_presence(self, show: Show, status: str | None = None) -> None:
        if show == "offline":
            self._xmpp.send_presence(ptype="unavailable", pstatus=status)
            return
        slix_show = "" if show == "online" else show
        self._xmpp.send_presence(pshow=slix_show or None, pstatus=status)

    async def _on_presence(self, pres) -> None:
        # Ignore our own presence and MUC room presence: occupant presences share
        # the presence_available/unavailable events, but their bare JID is the room
        # (handled by _on_groupchat_presence) — never a 1:1 roster contact.
        frm = JID(pres["from"])
        if frm.bare == self._bare or frm.bare in self._joined_rooms:
            return

        resources = self._presence_resources.setdefault(frm.bare, {})
        if pres["type"] == "unavailable":
            resources.pop(frm.resource, None)
        else:
            resources[frm.resource] = _SHOW_MAP.get(pres["show"], "online")

        # Aggregate across the contact's devices: online if any resource is, using
        # the most-available show; offline only once every resource has left.
        if resources:
            show = min(resources.values(), key=lambda s: _SHOW_PRIORITY.get(s, 0))
            status = pres["status"] or None
        else:
            show, status = "offline", None
        await self._on_event(PresenceUpdate(jid=frm.bare, show=show, status=status))

    # --- roster / subscriptions ------------------------------------------------

    async def add_contact(self, jid: str, name: str | None = None) -> None:
        bare = JID(jid).bare
        self._xmpp.send_presence_subscription(pto=bare)
        self._xmpp.update_roster(bare, name=name or bare, subscription="to")
        await self._emit_roster()

    async def remove_contact(self, jid: str) -> None:
        bare = JID(jid).bare
        try:
            await self._xmpp.del_roster_item(bare)
        except (IqError, IqTimeout):
            pass
        await self._emit_roster()

    async def respond_subscription(self, jid: str, accept: bool) -> None:
        bare = JID(jid).bare
        self._xmpp.send_presence(pto=bare, ptype="subscribed" if accept else "unsubscribed")
        if accept:
            # Reciprocate so we also see their presence.
            self._xmpp.send_presence_subscription(pto=bare)

    async def _on_subscribe(self, pres) -> None:
        await self._on_event(SubscriptionRequest(from_jid=JID(pres["from"]).bare))

    async def _on_roster_update(self, _event) -> None:
        await self._emit_roster()

    async def _emit_roster(self) -> None:
        items: list[RosterItem] = []
        roster = self._xmpp.client_roster
        for jid in roster:
            if jid == self._bare:
                continue
            item = roster[jid]
            items.append(
                RosterItem(
                    jid=jid,
                    name=item["name"] or jid,
                    subscription=item["subscription"] or "none",
                )
            )
        await self._on_event(RosterSnapshot(items=items))

    # --- MAM history -----------------------------------------------------------

    async def fetch_history(
        self, peer_jid: str, before: str | None, limit: int
    ) -> tuple[list[ArchivedMessage], bool]:
        # Fetch one RSM page ending before ``before`` (an archive id) — i.e. paging
        # backwards. An empty <before/> requests the newest page; note we must pass
        # "" not True, since slixmpp stringifies the value (str(True) -> "True",
        # which the server treats as a bogus item id and returns nothing).
        rsm = {"max": limit, "before": before if before else ""}
        try:
            iq = await self._xmpp["xep_0313"].retrieve(
                with_jid=JID(peer_jid).bare, rsm=rsm
            )
        except (IqError, IqTimeout):
            return [], True

        collected: list[ArchivedMessage] = []
        # The server returns matching messages as a list of <message> stanzas,
        # each wrapping a MAM <result> with the forwarded original.
        for msg in iq["mam"]["results"]:
            result = msg["mam_result"]
            fwd = result["forwarded"]
            inner = fwd["stanza"]
            body = inner["body"]
            if not body:
                continue
            collected.append(
                ArchivedMessage(
                    from_jid=JID(inner["from"]).bare,
                    to_jid=JID(inner["to"]).bare,
                    body=body,
                    msg_id=result["id"] or self._xmpp.new_id(),
                    timestamp=_stamp_to_iso(fwd["delay"]["stamp"]),
                )
            )
        # RSM <fin complete='true'> means no older messages remain beyond this page.
        complete = bool(iq["mam_fin"]["complete"]) or len(collected) < limit
        return collected, complete

    # --- MUC -------------------------------------------------------------------

    async def join_room(self, room: str, nick: str) -> None:
        room_bare = JID(room).bare
        self._joined_rooms[room_bare] = nick
        muc = self._xmpp["xep_0045"]
        await muc.join_muc_wait(room_bare, nick, timeout=20)
        await self._on_event(MucJoined(room=room_bare, nick=nick))
        await self._emit_occupants(room_bare)

    async def leave_room(self, room: str) -> None:
        room_bare = JID(room).bare
        nick = self._joined_rooms.pop(room_bare, None)
        if nick is not None:
            self._xmpp["xep_0045"].leave_muc(room_bare, nick)

    async def send_muc(self, room: str, body: str) -> str:
        room_bare = JID(room).bare
        msg_id = self._xmpp.new_id()
        msg = self._xmpp.make_message(mto=room_bare, mbody=body, mtype="groupchat")
        msg["id"] = msg_id
        msg.send()
        return msg_id

    async def _on_groupchat_message(self, msg) -> None:
        room_bare = JID(msg["from"]).bare
        nick = JID(msg["from"]).resource
        our_nick = self._joined_rooms.get(room_bare)
        body = msg["body"]
        if not body:
            return
        await self._on_event(
            MucMessage(
                room=room_bare,
                nick=nick,
                body=body,
                msg_id=msg["id"] or self._xmpp.new_id(),
                timestamp=_now_iso(),
                is_self=(nick == our_nick),
            )
        )

    async def _on_groupchat_presence(self, pres) -> None:
        room_bare = JID(pres["from"]).bare
        nick = JID(pres["from"]).resource
        if room_bare not in self._joined_rooms:
            return
        joined = pres["type"] != "unavailable"
        # Don't announce our own join/leave.
        if nick != self._joined_rooms.get(room_bare):
            await self._on_event(MucPresence(room=room_bare, nick=nick, joined=joined))
        await self._emit_occupants(room_bare)

    async def _emit_occupants(self, room_bare: str) -> None:
        muc = self._xmpp["xep_0045"]
        occupants: list[dict] = []
        try:
            roster = muc.get_roster(room_bare)
        except Exception:
            roster = []
        for nick in roster:
            try:
                data = muc.get_jid_property(room_bare, nick, "role")
            except Exception:
                data = None
            occupants.append({"nick": nick, "role": data or "participant"})
        await self._on_event(MucOccupants(room=room_bare, occupants=occupants))

    async def disco_rooms(self, server: str) -> DiscoveredRooms:
        rooms: list[dict] = []
        try:
            result = await self._xmpp["xep_0030"].get_items(jid=server)
            for item in result["disco_items"]["items"]:
                jid, _node, name = item
                rooms.append({"jid": jid, "name": name or jid})
        except (IqError, IqTimeout):
            pass
        return DiscoveredRooms(server=server, rooms=rooms)
