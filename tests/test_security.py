import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pyconduit.audit import _clean
from pyconduit.config import load_config
from pyconduit.web.app import create_app
from pyconduit.web.security import client_ip, origin_allowed

# --- pure helpers -------------------------------------------------------------

def test_origin_allowed_disabled_when_empty():
    assert origin_allowed("http://evil.example", []) is True
    assert origin_allowed(None, []) is True


def test_origin_allowed_enforces_allowlist():
    allowed = ["https://chat.example.com"]
    assert origin_allowed("https://chat.example.com", allowed) is True
    assert origin_allowed("https://evil.example", allowed) is False
    assert origin_allowed(None, allowed) is False  # missing Origin rejected when enforcing


def test_client_ip_prefers_forwarded_header_first_hop():
    ip = client_ip(
        headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1, 10.0.0.2"},
        peer="10.0.0.1", ip_header="X-Forwarded-For",
    )
    assert ip == "203.0.113.7"


def test_client_ip_header_case_insensitive():
    ip = client_ip(
        headers={"x-forwarded-for": "203.0.113.9"}, peer="10.0.0.1",
        ip_header="X-Forwarded-For",
    )
    assert ip == "203.0.113.9"


def test_client_ip_falls_back_to_peer():
    assert client_ip(headers={}, peer="192.0.2.5", ip_header="X-Forwarded-For") == "192.0.2.5"
    assert client_ip(headers={}, peer="192.0.2.5", ip_header=None) == "192.0.2.5"


def test_audit_clean_strips_newlines():
    assert _clean("alice\nfake audit line") == "alice fake audit line"
    assert _clean("ok\r\ntab\there") == "ok  tab here"


# --- integration: response headers -------------------------------------------

def _app():
    cfg = load_config(None)
    cfg.audit.destinations = []
    return create_app(config=cfg)


def test_security_headers_present():
    with TestClient(_app()) as client:
        r = client.get("/healthz")
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"


def test_websocket_rejects_bad_origin():
    cfg = load_config(None)
    cfg.audit.destinations = []
    cfg.server.allowed_origins = ["https://good.example"]
    app = create_app(config=cfg)
    with TestClient(app) as client:
        # A disallowed Origin must be rejected at the handshake (before any XMPP work).
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws?user=alice", headers={"origin": "https://evil.example"}
            ):
                pass
