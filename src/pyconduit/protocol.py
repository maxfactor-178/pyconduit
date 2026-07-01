"""The JSON message contract between browser and server, over the WebSocket.

Every frame is a JSON object with a ``type`` discriminator. This module is pure:
it validates/normalizes inbound client frames and builds outbound server frames.
No framework or slixmpp imports — so the wire contract can be unit-tested alone.

Direction of travel:
  * ClientMessage  — parsed from what the browser sends us (validated).
  * server_*()      — builders for what we send to the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ProtocolError(Exception):
    """Raised when an inbound client frame is malformed or of an unknown type."""


# --- Inbound: client -> server -------------------------------------------------

# Recognized inbound frame types. Kept explicit so unknown types fail loudly.
CLIENT_TYPES = frozenset(
    {
        "send_message",   # {to, body}
        "load_history",   # {jid, before?}
        "set_presence",   # {show, status?}
        "join_room",      # {room, nick?}
        "leave_room",     # {room}
        "send_muc",       # {room, body}
        "add_contact",    # {jid, name?}
        "remove_contact", # {jid}
        "subscription",   # {jid, action: accept|decline}
        "disco_rooms",    # {server}
    }
)


@dataclass(frozen=True)
class ClientMessage:
    """A validated inbound frame. ``data`` holds the type-specific payload fields."""

    type: str
    data: dict[str, Any]

    def require(self, *fields: str) -> None:
        for f in fields:
            v = self.data.get(f)
            if v is None or (isinstance(v, str) and v == ""):
                raise ProtocolError(f"{self.type}: missing field {f!r}")


def parse_client_message(raw: Any) -> ClientMessage:
    """Validate a decoded JSON object from the browser into a ClientMessage."""
    if not isinstance(raw, dict):
        raise ProtocolError("frame must be a JSON object")
    mtype = raw.get("type")
    if not isinstance(mtype, str) or not mtype:
        raise ProtocolError("frame missing string 'type'")
    if mtype not in CLIENT_TYPES:
        raise ProtocolError(f"unknown client message type: {mtype!r}")
    data = {k: v for k, v in raw.items() if k != "type"}
    return ClientMessage(type=mtype, data=data)


# --- Outbound: server -> client ------------------------------------------------
# Plain builder functions returning JSON-serializable dicts. Centralizing them
# keeps the wire shape consistent and greppable.


def server_ready(*, jid: str, username: str, brand_title: str, sound_default: bool) -> dict:
    return {
        "type": "ready",
        "jid": jid,
        "username": username,
        "brand_title": brand_title,
        "sound_enabled_default": sound_default,
    }


def server_message(
    *,
    conversation: str,
    from_jid: str,
    to_jid: str,
    body: str,
    msg_id: str,
    timestamp: str,
    direction: str,  # "incoming" | "outgoing"
) -> dict:
    """A 1:1 chat message routed to a conversation (the bare JID of the peer)."""
    return {
        "type": "message",
        "conversation": conversation,
        "from": from_jid,
        "to": to_jid,
        "body": body,
        "id": msg_id,
        "timestamp": timestamp,
        "direction": direction,
    }


def server_history(*, conversation: str, messages: list[dict], complete: bool) -> dict:
    """A page of older messages for a conversation, oldest-first."""
    return {
        "type": "history",
        "conversation": conversation,
        "messages": messages,
        "complete": complete,
    }


def server_presence(*, jid: str, show: str, status: str | None = None) -> dict:
    """Presence for a roster contact. show in online|away|dnd|xa|offline."""
    return {"type": "presence", "jid": jid, "show": show, "status": status}


def server_roster(*, contacts: list[dict]) -> dict:
    return {"type": "roster", "contacts": contacts}


def server_subscription_request(*, jid: str) -> dict:
    return {"type": "subscription_request", "jid": jid}


def server_muc_joined(*, room: str, nick: str, subject: str | None = None) -> dict:
    return {"type": "muc_joined", "room": room, "nick": nick, "subject": subject}


def server_muc_message(
    *, room: str, nick: str, body: str, msg_id: str, timestamp: str, is_self: bool
) -> dict:
    return {
        "type": "muc_message",
        "room": room,
        "nick": nick,
        "body": body,
        "id": msg_id,
        "timestamp": timestamp,
        "is_self": is_self,
    }


def server_muc_occupants(*, room: str, occupants: list[dict]) -> dict:
    return {"type": "muc_occupants", "room": room, "occupants": occupants}


def server_muc_presence(*, room: str, nick: str, joined: bool) -> dict:
    """A 'X joined' / 'X left' notice for a room."""
    return {"type": "muc_presence", "room": room, "nick": nick, "joined": joined}


def server_disco_rooms(*, server: str, rooms: list[dict]) -> dict:
    return {"type": "disco_rooms", "server": server, "rooms": rooms}


def server_error(*, context: str, message: str, conversation: str | None = None) -> dict:
    """A user-facing warning, e.g. a message that could not be delivered."""
    return {
        "type": "error",
        "context": context,
        "message": message,
        "conversation": conversation,
    }
