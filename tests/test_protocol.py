import pytest

from pyconduit import protocol
from pyconduit.protocol import ProtocolError, parse_client_message


def test_parse_valid():
    msg = parse_client_message({"type": "send_message", "to": "bob@example.com", "body": "hi"})
    assert msg.type == "send_message"
    assert msg.data == {"to": "bob@example.com", "body": "hi"}


def test_parse_rejects_non_object():
    with pytest.raises(ProtocolError):
        parse_client_message(["not", "an", "object"])


def test_parse_rejects_missing_type():
    with pytest.raises(ProtocolError):
        parse_client_message({"to": "bob@example.com"})


def test_parse_rejects_unknown_type():
    with pytest.raises(ProtocolError):
        parse_client_message({"type": "explode"})


def test_require_missing_field():
    msg = parse_client_message({"type": "send_message", "to": "bob@example.com"})
    with pytest.raises(ProtocolError):
        msg.require("to", "body")


def test_require_empty_string_is_missing():
    msg = parse_client_message({"type": "send_message", "to": "", "body": "x"})
    with pytest.raises(ProtocolError):
        msg.require("to")


def test_server_message_shape():
    frame = protocol.server_message(
        conversation="bob@example.com", from_jid="alice@example.com",
        to_jid="bob@example.com", body="hi", msg_id="1",
        timestamp="2026-01-01T00:00:00Z", direction="outgoing",
    )
    assert frame["type"] == "message"
    assert frame["conversation"] == "bob@example.com"
    assert frame["direction"] == "outgoing"


def test_server_error_carries_conversation():
    frame = protocol.server_error(
        context="delivery", message="nope", conversation="bob@example.com"
    )
    assert frame["type"] == "error"
    assert frame["conversation"] == "bob@example.com"


def test_server_ready_shape():
    frame = protocol.server_ready(
        jid="alice@example.com", username="alice", brand_title="X",
        sound_default=True, muc_servers=["conference.example.com"],
    )
    assert frame["type"] == "ready"
    assert frame["jid"] == "alice@example.com"
    assert frame["sound_enabled_default"] is True
    assert frame["muc_servers"] == ["conference.example.com"]
