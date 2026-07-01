"""Audit log for authentication and session events.

Records who connected, when, from what IP, and lifecycle events for XMPP sessions.
Destinations and formats are configurable (stdout/file; text/json). This is a thin,
dependency-light sink so it can be pointed at a file or scraped from stdout by an
external log shipper.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from .config import AuditConfig, AuditFormat


@dataclass
class _Sink:
    stream: TextIO
    fmt: AuditFormat
    owns_stream: bool = False


def _format_text(event: str, fields: dict[str, Any]) -> str:
    ts = fields.get("ts", "")
    rest = " ".join(f"{k}={v}" for k, v in fields.items() if k != "ts")
    return f"{ts} audit {event} {rest}".rstrip()


class AuditLog:
    """Fan-out audit events to one or more configured sinks."""

    def __init__(self, config: AuditConfig):
        self._sinks: list[_Sink] = []
        for dest in config.destinations:
            if dest.type == "stdout":
                self._sinks.append(_Sink(stream=sys.stdout, fmt=dest.format))
            elif dest.type == "file":
                if dest.path is None:
                    raise ValueError("audit file destination requires 'path'")
                path = Path(dest.path)
                path.parent.mkdir(parents=True, exist_ok=True)
                fh = path.open("a", encoding="utf-8")
                self._sinks.append(_Sink(stream=fh, fmt=dest.format, owns_stream=True))
            else:
                raise ValueError(f"unknown audit destination type: {dest.type!r}")

    def emit(self, event: str, **fields: Any) -> None:
        record = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
        for sink in self._sinks:
            if sink.fmt is AuditFormat.json:
                line = json.dumps(record, default=str)
            else:
                line = _format_text(event, record)
            try:
                sink.stream.write(line + "\n")
                sink.stream.flush()
            except Exception:  # never let auditing break the request path
                pass

    # Convenience wrappers for the common events.
    def auth_ok(self, *, username: str, jid: str, ip: str) -> None:
        self.emit("auth_ok", username=username, jid=jid, ip=ip)

    def auth_denied(self, *, username: str | None, ip: str, reason: str) -> None:
        self.emit("auth_denied", username=username, ip=ip, reason=reason)

    def session_open(self, *, username: str, jid: str, ip: str, tabs: int) -> None:
        self.emit("session_open", username=username, jid=jid, ip=ip, tabs=tabs)

    def session_close(self, *, username: str, jid: str, ip: str, tabs: int) -> None:
        self.emit("session_close", username=username, jid=jid, ip=ip, tabs=tabs)

    def xmpp_connect(self, *, jid: str) -> None:
        self.emit("xmpp_connect", jid=jid)

    def xmpp_disconnect(self, *, jid: str, reason: str = "") -> None:
        self.emit("xmpp_disconnect", jid=jid, reason=reason)

    def close(self) -> None:
        for sink in self._sinks:
            if sink.owns_stream:
                try:
                    sink.stream.close()
                except Exception:
                    pass
