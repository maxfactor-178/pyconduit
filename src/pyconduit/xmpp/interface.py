"""The abstract XMPP boundary.

The rest of the app depends on THIS module, never on slixmpp. Events are plain
dataclasses; the client is an abstract async interface. A fake implementation of
``XmppClient`` is enough to unit-test the session layer without a live server.
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

# Presence "show" values normalized to a small closed set the UI understands.
Show = Literal["online", "away", "dnd", "xa", "offline"]


# --- Events emitted by the client toward the session layer ---------------------


@dataclass
class Connected:
    jid: str


@dataclass
class Disconnected:
    reason: str = ""


@dataclass
class IncomingMessage:
    from_jid: str      # bare JID of the peer
    to_jid: str        # our bare JID
    body: str
    msg_id: str
    timestamp: str     # ISO-8601 UTC


@dataclass
class DeliveryFailure:
    to_jid: str
    error: str
    msg_id: str | None = None


@dataclass
class PresenceUpdate:
    jid: str           # bare JID of the contact
    show: Show
    status: str | None = None


@dataclass
class RosterItem:
    jid: str
    name: str
    subscription: str  # none | to | from | both


@dataclass
class RosterSnapshot:
    items: list[RosterItem] = field(default_factory=list)


@dataclass
class SubscriptionRequest:
    from_jid: str


@dataclass
class ArchivedMessage:
    from_jid: str
    to_jid: str
    body: str
    msg_id: str
    timestamp: str


# --- MUC events ----------------------------------------------------------------


@dataclass
class MucJoined:
    room: str
    nick: str
    subject: str | None = None


@dataclass
class MucMessage:
    room: str
    nick: str
    body: str
    msg_id: str
    timestamp: str
    is_self: bool


@dataclass
class MucOccupants:
    room: str
    occupants: list[dict]  # [{nick, role, affiliation, show}]


@dataclass
class MucPresence:
    room: str
    nick: str
    joined: bool


@dataclass
class DiscoveredRooms:
    server: str
    rooms: list[dict]  # [{jid, name}]


# Any of the above dataclasses may be delivered to the event handler.
EventHandler = Callable[[object], Awaitable[None]]


class XmppClient(abc.ABC):
    """One shared XMPP connection for a single bare JID.

    Implementations translate slixmpp (or any library) into the events above and
    accept the imperative calls below. All methods are async and must be safe to
    call from the asyncio event loop that owns the connection.
    """

    def __init__(self, on_event: EventHandler):
        self._on_event = on_event

    @property
    @abc.abstractmethod
    def is_connected(self) -> bool: ...

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def disconnect(self) -> None: ...

    @abc.abstractmethod
    async def send_message(self, to_jid: str, body: str) -> str:
        """Send a 1:1 message; return the message id we assigned."""

    @abc.abstractmethod
    async def set_presence(self, show: Show, status: str | None = None) -> None: ...

    @abc.abstractmethod
    async def fetch_history(
        self, peer_jid: str, before: str | None, limit: int
    ) -> tuple[list[ArchivedMessage], bool]:
        """Return (messages oldest-first, complete) for a conversation.

        ``before`` is an opaque archive id; None means "most recent page".
        ``complete`` is True when there are no older messages beyond this page.
        """

    # Roster / subscriptions
    @abc.abstractmethod
    async def add_contact(self, jid: str, name: str | None = None) -> None: ...

    @abc.abstractmethod
    async def remove_contact(self, jid: str) -> None: ...

    @abc.abstractmethod
    async def respond_subscription(self, jid: str, accept: bool) -> None: ...

    # MUC
    @abc.abstractmethod
    async def join_room(self, room: str, nick: str) -> None: ...

    @abc.abstractmethod
    async def leave_room(self, room: str) -> None: ...

    @abc.abstractmethod
    async def send_muc(self, room: str, body: str) -> str: ...

    @abc.abstractmethod
    async def disco_rooms(self, server: str) -> DiscoveredRooms: ...
