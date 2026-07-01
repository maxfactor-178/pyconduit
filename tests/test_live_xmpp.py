"""Live integration tests against a running dev ejabberd.

These are skipped automatically unless an XMPP server is reachable on
localhost:5222 (i.e. `make ejabberd-up && make register`). They exercise the real
slixmpp round-trips — connect/auth, 1:1 messaging, MAM history, and MUC — that the
pure-logic unit tests cannot. Written to be idempotent (unique message bodies and
room names) so they don't depend on a freshly reset server.
"""

from __future__ import annotations

import asyncio
import socket
import uuid

import pytest

from pyconduit.config import XmppConfig
from pyconduit.xmpp import interface as ifc
from pyconduit.xmpp.slixmpp_client import SlixmppClient


def _server_up(host: str = "localhost", port: int = 5222) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _server_up(), reason="no XMPP server on localhost:5222 (run `make ejabberd-up`)"
)

CFG = XmppConfig(host="localhost", port=5222, tls=False, verify_certs=False)


class Collector:
    def __init__(self):
        self.events: list[object] = []

    async def on_event(self, ev):
        self.events.append(ev)

    def of(self, cls):
        return [e for e in self.events if isinstance(e, cls)]


async def _wait_for(coll, cls, pred=None, timeout=8.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        for e in coll.of(cls):
            if pred is None or pred(e):
                return e
        await asyncio.sleep(0.1)
    return None


@pytest.fixture
async def clients():
    ac, bc = Collector(), Collector()
    alice = SlixmppClient("alice@example.com", "alicepass", CFG, ac.on_event)
    bob = SlixmppClient("bob@example.com", "bobpass", CFG, bc.on_event)
    await alice.connect()
    await bob.connect()
    await alice.set_presence("online")
    await bob.set_presence("online")
    await asyncio.sleep(0.5)
    try:
        yield alice, ac, bob, bc
    finally:
        await alice.disconnect()
        await bob.disconnect()
        await asyncio.sleep(0.2)


async def test_connect_auth(clients):
    alice, _, bob, _ = clients
    assert alice.is_connected and bob.is_connected


async def test_one_to_one_message(clients):
    alice, _ac, _bob, bc = clients
    body = f"hi-{uuid.uuid4().hex[:8]}"
    await alice.send_message("bob@example.com", body)
    got = await _wait_for(bc, ifc.IncomingMessage, pred=lambda e: e.body == body)
    assert got is not None
    assert got.from_jid == "alice@example.com"


async def test_mam_history(clients):
    alice, _ac, _bob, _bc = clients
    body = f"archive-{uuid.uuid4().hex[:8]}"
    await alice.send_message("bob@example.com", body)
    await asyncio.sleep(1.0)  # let the server archive
    messages, _complete = await alice.fetch_history("bob@example.com", None, 50)
    assert any(m.body == body for m in messages)


async def test_muc_join_send_occupants(clients):
    alice, ac, bob, bc = clients
    room = f"pyc-{uuid.uuid4().hex[:8]}@conference.example.com"
    await alice.join_room(room, "alice")
    assert await _wait_for(ac, ifc.MucJoined) is not None
    await bob.join_room(room, "bob")
    await asyncio.sleep(1.0)
    body = f"room-{uuid.uuid4().hex[:8]}"
    await alice.send_muc(room, body)
    got = await _wait_for(bc, ifc.MucMessage, pred=lambda e: e.body == body)
    assert got is not None
    occ = ac.of(ifc.MucOccupants)
    assert occ and any(o["nick"] in ("alice", "bob") for o in occ[-1].occupants)
