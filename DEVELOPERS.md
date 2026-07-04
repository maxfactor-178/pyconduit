# PyConduit — Developer Guide

How PyConduit is put together and how to extend it. If you only want to *run* it,
see the [README](README.md). This document is for people changing the code.

## Table of contents

- [Design philosophy](#design-philosophy)
- [The layers](#the-layers)
- [How a message flows](#how-a-message-flows)
- [The WebSocket protocol](#the-websocket-protocol)
- [Adding a feature: the recipe](#adding-a-feature-the-recipe)
- [Worked example: notification sounds for DMs and MUC mentions](#worked-example-notification-sounds-for-dms-and-muc-mentions)
- [Testing](#testing)
- [Security model](#security-model)
- [slixmpp gotchas we already hit](#slixmpp-gotchas-we-already-hit)

## Design philosophy

Two rules explain almost every structural decision:

1. **slixmpp is quarantined.** It is imported in exactly one file,
   [`xmpp/slixmpp_client.py`](src/pyconduit/xmpp/slixmpp_client.py). Everything else
   talks to the abstract [`xmpp/interface.py`](src/pyconduit/xmpp/interface.py):
   an abstract `XmppClient` plus plain dataclasses (`IncomingMessage`,
   `PresenceUpdate`, `MucMessage`, …). The web and session layers never see a
   slixmpp object. This is what makes the session layer unit-testable with a fake
   client and would let us swap XMPP libraries without touching the app.

2. **The wire protocol is pure and centralized.**
   [`protocol.py`](src/pyconduit/protocol.py) has no framework imports. It validates
   inbound browser frames and builds outbound frames. Every JSON shape the browser
   sees is a `server_*()` builder there — greppable in one place.

Dependencies point downward and never back up:

```
web (FastAPI/WebSocket)  →  session (Account/Manager)  →  xmpp.interface (abstract)
                                                              ↑ implemented by
                                                         xmpp.slixmpp_client
protocol / config / auth / audit  =  pure leaf modules, no framework imports
```

## The layers

| Module | Responsibility | Imports slixmpp? | Imports FastAPI? |
|--------|----------------|:---:|:---:|
| [`config.py`](src/pyconduit/config.py) | pydantic-settings schema + YAML/env loader | no | no |
| [`auth.py`](src/pyconduit/auth.py) | username → JID → password mapping; dev `?user=` | no | no |
| [`protocol.py`](src/pyconduit/protocol.py) | browser↔server JSON contract (validate + build) | no | no |
| [`audit.py`](src/pyconduit/audit.py) | auth/session event log (never message content) | no | no |
| [`xmpp/interface.py`](src/pyconduit/xmpp/interface.py) | abstract `XmppClient` + event dataclasses | no | no |
| [`xmpp/slixmpp_client.py`](src/pyconduit/xmpp/slixmpp_client.py) | the concrete slixmpp implementation | **yes** | no |
| [`session/account.py`](src/pyconduit/session/account.py) | one shared connection per JID; fan-out; echo; idle/backoff | no | no |
| [`session/manager.py`](src/pyconduit/session/manager.py) | JID → `Account` registry, idle reaper | no | no |
| [`session/hub.py`](src/pyconduit/session/hub.py) | `Subscriber` = one browser tab | no | no |
| [`web/app.py`](src/pyconduit/web/app.py) | FastAPI factory, headers, wiring | no | **yes** |
| [`web/ws.py`](src/pyconduit/web/ws.py) | `/ws` handler: auth, origin, pump | no | **yes** |
| [`web/security.py`](src/pyconduit/web/security.py) | origin check, client IP, CSP headers | no | no |
| [`static/`](src/pyconduit/static/) | vanilla-JS dark-theme frontend, no build step | — | — |

### Key objects

- **`Subscriber`** — one browser tab: an id, the human's username/IP (for audit),
  and an async `send(frame)` that pushes JSON to that tab.
- **`Account`** — owns one `XmppClient` for a bare JID and a set of `Subscriber`s.
  It converts inbound XMPP events into JSON frames and broadcasts them to every
  tab, routes tab commands to the client, echoes sent messages to all tabs, caches
  replayable state (roster/presence/joined rooms/occupants) for late-joining tabs,
  and manages idle shutdown + exponential-backoff reconnect.
- **`AccountManager`** — maps bare JID → `Account`, shared across all tabs and even
  multiple users of the same JID ("helpdesk mode").

## How a message flows

**Browser → XMPP (sending):**

```
tab types → WS JSON {type:"send_message",...}
  → ws.py receive_json → protocol.parse_client_message (validate)
  → Account.handle_command → _cmd_send_message
  → XmppClient.send_message (slixmpp emits the stanza)
  → Account broadcasts a {type:"message",direction:"outgoing"} echo to ALL tabs
```

**XMPP → Browser (receiving):**

```
slixmpp 'message' event → SlixmppClient._on_message
  → emits interface.IncomingMessage (a plain dataclass)
  → Account._on_event → protocol.server_message(...)
  → Account._broadcast → every Subscriber.send → WS → tab renders
```

The echo-to-all-tabs step is why multiple tabs (and multiple helpdesk users) of one
account stay in sync.

## The WebSocket protocol

All frames are JSON objects with a `type` discriminator.

- **Inbound (browser → server):** allowed types are the `CLIENT_TYPES` set in
  `protocol.py`. Each is validated and dispatched to an `Account._cmd_<type>`
  method by name. Unknown types are rejected loudly.
- **Outbound (server → browser):** built by the `server_*()` functions in
  `protocol.py` and handled by the `switch (f.type)` in
  [`static/js/app.js`](src/pyconduit/static/js/app.js) `handleFrame()`.

## Adding a feature: the recipe

### A) A new command the browser sends (browser → server)

1. **`protocol.py`** — add the type string to `CLIENT_TYPES`.
2. **`session/account.py`** — add an `async def _cmd_<type>(self, sub, msg)` method.
   Dispatch is automatic (by method name). Use `msg.require("field", …)` to validate.
3. **`xmpp/interface.py`** — if it needs a new XMPP action, add an abstract method to
   `XmppClient`.
4. **`xmpp/slixmpp_client.py`** — implement that method with slixmpp.
5. **`static/js/app.js`** — call `send({ type: "<type>", … })` from the UI.
6. **Tests** — add a `test_session.py` case with the fake client; if it touches
   real XMPP, add a `test_live_xmpp.py` case.

### B) A new event the server pushes (server → browser)

1. **`xmpp/interface.py`** — add a dataclass for the event (if it originates in XMPP).
2. **`xmpp/slixmpp_client.py`** — emit it from the relevant slixmpp handler via
   `await self._on_event(...)`.
3. **`protocol.py`** — add a `server_<name>()` builder.
4. **`session/account.py`** — handle the dataclass in `_on_event` and `_broadcast`
   the built frame. Cache it in the replay state if a late tab needs it.
5. **`static/js/app.js`** — add a `case "<name>":` in `handleFrame()`.
6. **Tests** — as above.

### C) A pure frontend change

Just edit `static/`. There is **no build step** — files are served as-is, so a
browser hard-refresh (Ctrl+F5) picks up changes. Backend changes need a server
restart; static changes do not.

> **Always render untrusted text with `textContent` / `createElement`, never
> `innerHTML`.** Message bodies, nicks, JIDs and statuses come from other XMPP
> users. See [Security model](#security-model).

## Worked example: notification sounds for DMs and MUC mentions

Goal: play a distinct notification sound when **a direct message arrives** and when
**someone mentions my nick in a room**. This touches both backend and frontend and
is a good tour of the recipe above.

The DM case needs **no backend change** — the browser already receives incoming 1:1
`message` frames. The mention case is best detected **server-side** (the server
knows our per-room nick), so we add a `mentions_me` flag end-to-end.

### Step 1 — carry the flag on the event dataclass (`xmpp/interface.py`)

```python
@dataclass
class MucMessage:
    room: str
    nick: str
    body: str
    msg_id: str
    timestamp: str
    is_self: bool
    mentions_me: bool = False   # NEW: our nick appears in the body
```

### Step 2 — compute it where we already know our nick (`xmpp/slixmpp_client.py`)

In `_on_groupchat_message` we already look up `our_nick = self._joined_rooms.get(room_bare)`:

```python
mentions_me = bool(our_nick) and our_nick.lower() in body.lower() and nick != our_nick
await self._on_event(
    MucMessage(
        room=room_bare, nick=nick, body=body,
        msg_id=msg["id"] or self._xmpp.new_id(),
        timestamp=_now_iso(), is_self=(nick == our_nick),
        mentions_me=mentions_me,
    )
)
```

### Step 3 — put it on the wire (`protocol.py`)

```python
def server_muc_message(*, room, nick, body, msg_id, timestamp, is_self, mentions_me):
    return {
        "type": "muc_message", "room": room, "nick": nick, "body": body,
        "id": msg_id, "timestamp": timestamp, "is_self": is_self,
        "mentions_me": mentions_me,
    }
```

### Step 4 — pass it through the account (`session/account.py`)

In the `isinstance(event, ifc.MucMessage)` branch of `_on_event`, forward the field:

```python
protocol.server_muc_message(
    room=event.room, nick=event.nick, body=event.body,
    msg_id=event.msg_id, timestamp=event.timestamp,
    is_self=event.is_self, mentions_me=event.mentions_me,
)
```

### Step 5 — play the sounds (`static/`)

Two options for the audio itself:

- **Bundled files (what most people want):** drop `mention.mp3` / `dm.mp3` into
  `static/sounds/` and play them with `new Audio("/sounds/mention.mp3")`. The strict
  CSP already allows same-origin media (`default-src 'self'`), so no config change
  is needed — just don't reference an external CDN.
- **No asset files:** synthesize distinct tones with the Web Audio API, like the
  existing [`static/js/sound.js`](src/pyconduit/static/js/sound.js) `blip()`.

Using bundled files, extend `sound.js`:

```javascript
window.Sound = (function () {
  const files = {
    dm: new Audio("/sounds/dm.mp3"),
    mention: new Audio("/sounds/mention.mp3"),
  };
  function play(name) {
    const a = files[name];
    if (a) { a.currentTime = 0; a.play().catch(() => {}); }
  }
  return { blip, play };   // keep blip for the generic case
})();
```

Then trigger them in `app.js` `handleFrame()`:

```javascript
case "message":
  // … existing handling …
  if (f.direction === "incoming" && state.soundEnabled) Sound.play("dm");
  break;

case "muc_message":
  // … existing handling …
  if (f.mentions_me && state.soundEnabled) Sound.play("mention");
  break;
```

> Respect the existing `state.soundEnabled` toggle (the Settings modal). If you want
> separate on/off switches for DM vs mention sounds, add checkboxes in
> `index.html`, persist them in `localStorage` like the sound toggle already is, and
> gate `Sound.play(...)` on them.

### Step 6 — tests

- **`test_session.py`** (fake client): emit an `ifc.MucMessage(..., mentions_me=True)`
  and assert the broadcast frame has `mentions_me: True`.
- **`test_live_xmpp.py`** (real server): have Bob send `"hey alice look"` to a room
  Alice joined as `alice`, and assert Alice's `MucMessage` has `mentions_me is True`.

That's the whole feature: one dataclass field, one computation, one protocol field,
one account passthrough, a few lines of frontend, and two tests.

## Testing

```bash
make test         # everything; live tests auto-skip if ejabberd is down
```

Three tiers:

- **Pure unit** — `test_config.py`, `test_protocol.py`, `test_auth.py`,
  `test_security.py`. No network, no server.
- **Session** — `test_session.py`. Drives `Account`/`AccountManager` with a **fake
  `XmppClient`** (see the `FakeClient` in that file). This is the model for testing
  new commands/events without a live server.
- **Live** — `test_live_xmpp.py`. Real slixmpp against the dev ejabberd; auto-skips
  unless `localhost:5222` is reachable (`make ejabberd-up && make register`).

Browser smoke tests (Playwright) live outside the repo but follow the same pattern:
open two contexts (alice/bob) and assert on the rendered DOM.

## Security model

- **Auth is delegated to a trusted reverse proxy** that sets `auth.header`
  (default `X-Remote-User`). The app trusts that header, so **the app must never be
  directly reachable** — bind to `127.0.0.1` (the default) and only expose it via the
  proxy, or anyone can spoof the header. `auth.mode: dev` (`?user=`) is for local dev
  only and logs a loud warning at startup.
- **XSS:** all remote-controlled text is rendered with `textContent`. A strict CSP
  (`default-src 'self'`) is sent on every response as defense in depth. Keep both
  invariants when adding UI.
- **Cross-site WebSocket hijacking:** set `server.allowed_origins` in production so
  `/ws` rejects handshakes from other sites.
- **Real client IP:** set `server.client_ip_header` (e.g. `X-Forwarded-For`) so audit
  logs record the user's IP, not the proxy's. Only trust it behind a proxy that sets
  it.
- **No message content is ever logged** — the audit log records auth/session events
  only (`audit.py`). Keep it that way.
- **Message size** is capped by `server.max_message_chars` (server-enforced).

## slixmpp gotchas we already hit

Recorded so you don't rediscover them (all against slixmpp 1.16 / ejabberd 26):

- `ClientXMPP.connect(host=, port=)` only — no `address=`/`force_starttls=` kwargs.
- Plaintext dev needs `xmpp["feature_mechanisms"].unencrypted_plain = True`.
- **MAM:** use the awaitable `retrieve()` and read `iq["mam"]["results"]` +
  `iq["mam_fin"]["complete"]`. Request the newest page with an **empty** `before`
  (`""`, not `True` — slixmpp stringifies `True` to `"True"`, a bogus id → 0 rows).
- **Presence is per-resource:** a contact is online if *any* resource is. Track
  `{bare: {resource: show}}` and only report offline when the last one leaves.
- **MUC presence** shares the `presence_available/unavailable` events but carries the
  `{http://jabber.org/protocol/muc#user}x` element — filter on that so room presence
  never leaks into the 1:1 roster (including your own unavailable when leaving).
- STARTTLS needs a cert: the dev setup mounts a self-signed one (`make certs`).
