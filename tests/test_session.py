"""Session-layer tests using a fake XmppClient (no slixmpp, no network).

These exercise the heart of the app — shared connection per account, echo of sent
messages to every tab, incoming broadcast, and delivery-failure surfacing — which
is exactly what the abstract xmpp.interface was designed to make testable.
"""

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

    # Tests may set FakeClient.disco_map = {server: DiscoveredRooms} to shape results.
    disco_map: dict = {}

    async def disco_rooms(self, server):
        return self.disco_map.get(server, ifc.DiscoveredRooms(server=server, rooms=[]))


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


async def test_message_too_long_is_rejected():
    mgr = make_manager()
    f1, s1 = collector()
    sub1 = Subscriber(username="alice", ip="1.1.1.1", send=s1)
    account = await mgr.attach(jid="alice@example.com", password="pw", sub=sub1)

    long_body = "x" * (account._cfg.server.max_message_chars + 1)
    cmd = parse_client_message(
        {"type": "send_message", "to": "bob@example.com", "body": long_body}
    )
    await account.handle_command(sub1, cmd)

    # Nothing was sent to XMPP, and the tab got a delivery error instead of an echo.
    client = created["alice@example.com"]
    assert client.sent == []
    errors = [fr for fr in f1 if fr["type"] == "error"]
    assert errors and "too long" in errors[0]["message"].lower()
    assert not any(fr["type"] == "message" for fr in f1)


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


async def test_disco_servers_uses_only_configured_servers_sorted():
    mgr = make_manager()
    # Configure two MUC domains; one online with rooms, one offline.
    mgr._cfg.muc.discovery_servers = ["z.example.com", "a.example.com"]
    FakeClient.disco_map = {
        "a.example.com": ifc.DiscoveredRooms(
            server="a.example.com", rooms=[{"jid": "r@a.example.com", "name": "R"}],
            online=True,
        ),
        "z.example.com": ifc.DiscoveredRooms(server="z.example.com", rooms=[], online=False),
    }
    try:
        f1, s1 = collector()
        sub1 = Subscriber(username="alice", ip="1.1.1.1", send=s1)
        account = await mgr.attach(jid="alice@example.com", password="pw", sub=sub1)
        await account.handle_command(sub1, parse_client_message({"type": "disco_servers"}))
    finally:
        FakeClient.disco_map = {}

    frame = [fr for fr in f1 if fr["type"] == "disco_servers"][-1]
    names = [s["server"] for s in frame["servers"]]
    assert names == ["a.example.com", "z.example.com"]  # sorted by server name
    by = {s["server"]: s for s in frame["servers"]}
    assert by["a.example.com"]["online"] is True and by["a.example.com"]["rooms"]
    assert by["z.example.com"]["online"] is False and by["z.example.com"]["rooms"] == []


async def test_join_room_rejects_disallowed_domain():
    mgr = make_manager()
    mgr._cfg.muc.discovery_servers = ["conference.example.com"]
    f1, s1 = collector()
    sub1 = Subscriber(username="alice", ip="1.1.1.1", send=s1)
    account = await mgr.attach(jid="alice@example.com", password="pw", sub=sub1)

    await account.handle_command(
        sub1, parse_client_message({"type": "join_room", "room": "x@evil.example.com"})
    )
    errors = [fr for fr in f1 if fr["type"] == "error"]
    assert errors and "not allowed" in errors[0]["message"].lower()
    # An allowed-domain room passes validation (fake join is a no-op).
    await account.handle_command(
        sub1,
        parse_client_message({"type": "join_room", "room": "team@conference.example.com"}),
    )
    assert "team@conference.example.com" in account._joined_rooms


async def test_idle_close_after_last_tab():
    mgr = make_manager()
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
