"""Session-layer tests using a fake XmppClient (no slixmpp, no network).

These exercise the heart of the app — shared connection per account, echo of sent
messages to every tab, incoming broadcast, and delivery-failure surfacing — which
is exactly what the abstract xmpp.interface was designed to make testable.
"""

import pytest

from pyconduit.audit import AuditLog
from pyconduit.config import load_config
from pyconduit.protocol import parse_client_message
from pyconduit.session.hub import Subscriber
from pyconduit.session.manager import AccountManager
from pyconduit.xmpp import interface as ifc
from pyconduit.xmpp.interface import Connected, XmppClient

created: dict[str, "FakeClient"] = {}


class FakeClient(XmppClient):
    def __init__(self, jid, password, config, on_event):
        super().__init__(on_event)
        self.jid = jid
        self._connected = False
        self.sent: list[tuple[str, str]] = []
        created[jid] = self

    @property
    def is_connected(self):
        return self._connected

    async def connect(self):
        self._connected = True
        await self._on_event(Connected(jid=self.jid))

    async def disconnect(self):
        self._connected = False

    async def send_message(self, to_jid, body):
        self.sent.append((to_jid, body))
        return "mid-1"

    async def emit(self, event):
        await self._on_event(event)

    async def set_presence(self, show, status=None): ...
    async def fetch_history(self, peer_jid, before, limit): return [], True
    async def add_contact(self, jid, name=None): ...
    async def remove_contact(self, jid): ...
    async def respond_subscription(self, jid, accept): ...
    async def join_room(self, room, nick): ...
    async def leave_room(self, room): ...
    async def send_muc(self, room, body): return "mid-muc"
    async def disco_rooms(self, server): return ifc.DiscoveredRooms(server=server, rooms=[])


def make_manager():
    created.clear()
    cfg = load_config(None)
    cfg.audit.destinations = []  # silent audit in tests
    return AccountManager(cfg, AuditLog(cfg.audit), client_factory=FakeClient)


def collector():
    frames: list[dict] = []

    async def send(frame):
        frames.append(frame)

    return frames, send


async def test_two_tabs_share_one_connection():
    mgr = make_manager()
    f1, s1 = collector()
    f2, s2 = collector()
    sub1 = Subscriber(username="alice", ip="1.1.1.1", send=s1)
    sub2 = Subscriber(username="alice", ip="2.2.2.2", send=s2)

    await mgr.attach(jid="alice@example.com", password="pw", sub=sub1)
    await mgr.attach(jid="alice@example.com", password="pw", sub=sub2)

    # One shared account/connection, and each tab received a 'ready' frame.
    assert mgr.account_count == 1
    assert len(created) == 1
    assert any(fr["type"] == "ready" for fr in f1)
    assert any(fr["type"] == "ready" for fr in f2)


async def test_sent_message_echoes_to_all_tabs():
    mgr = make_manager()
    f1, s1 = collector()
    f2, s2 = collector()
    sub1 = Subscriber(username="alice", ip="1.1.1.1", send=s1)
    sub2 = Subscriber(username="alice", ip="2.2.2.2", send=s2)
    await mgr.attach(jid="alice@example.com", password="pw", sub=sub1)
    account = await mgr.attach(jid="alice@example.com", password="pw", sub=sub2)

    cmd = parse_client_message(
        {"type": "send_message", "to": "bob@example.com", "body": "hello"}
    )
    await account.handle_command(sub1, cmd)

    # The outgoing message is echoed to BOTH tabs (multi-tab sync).
    for frames in (f1, f2):
        echoes = [fr for fr in frames if fr["type"] == "message"]
        assert len(echoes) == 1
        assert echoes[0]["direction"] == "outgoing"
        assert echoes[0]["body"] == "hello"
        assert echoes[0]["conversation"] == "bob@example.com"


async def test_incoming_message_broadcasts():
    mgr = make_manager()
    f1, s1 = collector()
    sub1 = Subscriber(username="alice", ip="1.1.1.1", send=s1)
    await mgr.attach(jid="alice@example.com", password="pw", sub=sub1)

    client = created["alice@example.com"]
    await client.emit(
        ifc.IncomingMessage(
            from_jid="bob@example.com", to_jid="alice@example.com",
            body="hi there", msg_id="x", timestamp="2026-01-01T00:00:00Z",
        )
    )
    incoming = [fr for fr in f1 if fr["type"] == "message" and fr["direction"] == "incoming"]
    assert len(incoming) == 1
    assert incoming[0]["conversation"] == "bob@example.com"


async def test_delivery_failure_surfaces_error():
    mgr = make_manager()
    f1, s1 = collector()
    sub1 = Subscriber(username="alice", ip="1.1.1.1", send=s1)
    await mgr.attach(jid="alice@example.com", password="pw", sub=sub1)

    client = created["alice@example.com"]
    await client.emit(ifc.DeliveryFailure(to_jid="bob@example.com", error="not found"))
    errors = [fr for fr in f1 if fr["type"] == "error"]
    assert errors and errors[0]["conversation"] == "bob@example.com"


async def test_idle_close_after_last_tab(monkeypatch):
    mgr = make_manager()
    cfg_account = None
    f1, s1 = collector()
    sub1 = Subscriber(username="alice", ip="1.1.1.1", send=s1)
    account = await mgr.attach(jid="alice@example.com", password="pw", sub=sub1)
    account._cfg.xmpp.idle_timeout_seconds = 0  # expire immediately
    await mgr.detach(account, sub1)

    # Give the idle countdown task a chance to run.
    import asyncio
    await asyncio.sleep(0.05)
    assert mgr.account_count == 0
    assert created["alice@example.com"].is_connected is False
