# PyConduit

A web-based XMPP chat client. PyConduit is a Python server that bridges browser
WebSocket connections to a standard XMPP server: a user opens a browser tab and
chats, while the server manages the real XMPP session on their behalf. It aims to
be a simple, solid chat client that interoperates with standard XMPP clients —
not a feature-complete one.

## Features

- **1:1 chat** with server-side message history (MAM) and a *load older messages* button.
- **Group chat (MUC):** join/leave, live occupant list, *joined/left* notices, room
  discovery across multiple servers, and join-by-address.
- **Contacts/roster:** add/remove contacts and an accept/decline subscription flow.
- **Presence:** online / away / do-not-disturb / extended-away / offline.
- **Delivery feedback:** a clear warning in the conversation when a message can't be sent.
- **Unread indicators:** per-conversation badges and an unread count in the tab title.
- **Notification sounds** with a settings modal, plus a configurable brand/page title.
- **Multi-tab & multi-user sync** for a shared account, in real time.
- **Audit log** of auth/session events with configurable destinations and formats.

Deliberately out of scope: typing indicators, delivery/read receipts, browser push,
file sharing, and OMEMO.

## Architecture

```
browser (vanilla JS)  ⇄  WebSocket JSON  ⇄  FastAPI /ws
                                              │
                                     session layer (AccountManager)
                                        one shared XMPP connection
                                        per bare JID, reused across
                                        all tabs and users of that JID
                                              │
                                     xmpp.interface  (abstract)
                                              │  implemented by
                                     xmpp.slixmpp_client  (slixmpp)
```

- **Auth is delegated to a trusted reverse proxy** that sets a username header.
  The server maps that username → XMPP JID (`users.json`) → password
  (`credentials.json`). A **dev mode** accepts `?user=alice` in the URL instead.
- **One shared connection per account** (bare JID), reused across all of that
  account's tabs — and across multiple users mapped to the same JID ("helpdesk
  mode"). When the last tab closes, the connection lingers for a configurable idle
  period, then closes. Dropped connections reconnect with exponential backoff.
- **slixmpp is quarantined** behind `xmpp/interface.py`; the web and session
  layers never import it.

Package layout:

```
src/pyconduit/
  config.py          # pydantic-settings schema + YAML loader
  auth.py            # username → JID → password mapping; dev mode
  protocol.py        # pure browser↔server JSON contract
  audit.py           # auth/session event log
  xmpp/interface.py  # abstract XmppClient + plain event dataclasses
  xmpp/slixmpp_client.py  # the only slixmpp import
  session/           # Account, AccountManager, Subscriber (fan-out, idle, backoff)
  web/               # FastAPI app, routes, /ws handler
  static/            # vanilla-JS dark-theme frontend (no build step)
```

## Quickstart

Prerequisites: Python 3.11+ and Docker.

### 1. Start ejabberd (development XMPP server)

```bash
docker compose up -d          # or: make ejabberd-up
```

This runs **only** ejabberd (the PyConduit app runs on the host). It uses the
`example.com` virtual host and enables MAM (history) and MUC (group chat).

### 2. Register two test accounts

```bash
make register
# equivalently:
docker exec pyconduit-ejabberd ejabberdctl register alice example.com alicepass
docker exec pyconduit-ejabberd ejabberdctl register bob   example.com bobpass
```

These match the sample `users.json` / `credentials.json` (`alice@example.com`,
`bob@example.com`).

### 3. Install and run the app

```bash
make install                  # creates .venv and installs the package
make run                      # runs on http://127.0.0.1:8080
```

Without `make` (e.g. Windows PowerShell):

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pyconduit config.dev.yaml
```

### 4. Chat from two browser tabs

Dev mode selects the user from the URL query string:

- Tab 1 (Alice): <http://127.0.0.1:8080/?user=alice>
- Tab 2 (Bob):   <http://127.0.0.1:8080/?user=bob>

In Alice's tab, click **＋** next to *Direct messages* and add
`bob@example.com`; accept the request in Bob's tab, and chat. Open a second
Alice tab to see multi-tab sync and unread badges.

## Configuration

`config.dev.yaml` is the dev config. Any field can be overridden by an environment
variable prefixed `PYCONDUIT_` with `__` as the nesting delimiter, e.g.:

```bash
PYCONDUIT_SERVER__PORT=9000 PYCONDUIT_AUTH__MODE=proxy make run
```

Key sections: `server` (host/port/brand_title), `auth` (mode `proxy`/`dev`,
header name, mapping files), `xmpp` (host/port/TLS, idle timeout, reconnect
backoff), `muc` (discovery servers), `history` (page size), `ui`
(sound default), `audit` (destinations + text/json format).

### Production auth

Set `auth.mode: proxy`. Put PyConduit behind a reverse proxy that authenticates
every request to `/` and `/ws` and **overwrites** the configured header
(default `X-Remote-User`) with the verified username. `/healthz` and `/readyz`
need no auth.

## Testing & linting

```bash
make test      # pytest — pure logic (config, protocol, auth, session)
make lint      # ruff
```

The suite also includes **live XMPP integration tests** (`tests/test_live_xmpp.py`)
that exercise the real slixmpp round-trips — connect/auth, 1:1 messaging, MAM
history, and MUC. They **skip automatically** unless ejabberd is reachable on
`localhost:5222`, so `make test` stays green offline. To run them, start ejabberd
and register the accounts first (`make ejabberd-up && make register`).

## Health endpoints

- `GET /healthz` — liveness (no auth)
- `GET /readyz`  — readiness (no auth)
```
