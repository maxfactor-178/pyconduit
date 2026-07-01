"""Authentication mapping: proxy username -> XMPP identity.

Authentication itself is delegated to a trusted reverse proxy that sets a header
(default ``X-Remote-User``). This module only maps that trusted username onto an
XMPP identity:

    username --(users.json)--> bare JID --(credentials.json)--> password

Many usernames may map to the same bare JID ("helpdesk mode"). The mapping files
are loaded once and can be reloaded; lookups are pure and side-effect free.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class AuthError(Exception):
    """Raised when a username cannot be resolved to a usable XMPP identity."""


@dataclass(frozen=True)
class Identity:
    """The resolved XMPP identity for an authenticated web user."""

    username: str  # the trusted proxy username (who is at the keyboard)
    jid: str       # bare XMPP JID the session runs as
    password: str  # XMPP password for that JID


def _load_json_map(path: Path) -> dict[str, str]:
    if not path.exists():
        raise AuthError(f"mapping file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise AuthError(f"mapping file must be a JSON object: {path}")
    return {str(k): str(v) for k, v in data.items()}


class AuthMapper:
    """Resolves trusted usernames to XMPP identities via two JSON files."""

    def __init__(self, users: Mapping[str, str], credentials: Mapping[str, str]):
        self._users = dict(users)
        self._credentials = dict(credentials)

    @classmethod
    def from_files(cls, users_file: str | Path, credentials_file: str | Path) -> AuthMapper:
        return cls(
            users=_load_json_map(Path(users_file)),
            credentials=_load_json_map(Path(credentials_file)),
        )

    def resolve(self, username: str) -> Identity:
        """Map a trusted username to a full XMPP identity.

        Raises AuthError if the username is unknown or has no stored password.
        """
        if not username:
            raise AuthError("empty username")
        jid = self._users.get(username)
        if jid is None:
            raise AuthError(f"unknown user: {username!r}")
        password = self._credentials.get(jid)
        if password is None:
            raise AuthError(f"no credentials for jid: {jid!r}")
        return Identity(username=username, jid=jid, password=password)


def extract_username(
    *,
    mode: str,
    header_name: str,
    headers: Mapping[str, str],
    query_user: str | None,
    dev_default_user: str,
) -> str:
    """Determine the trusted username for a request.

    - proxy mode: read it from the configured header (the proxy overwrites it).
    - dev mode: accept ?user=alice, falling back to the configured default.

    Header lookup is case-insensitive to match HTTP semantics.
    """
    if mode == "dev":
        return query_user or dev_default_user

    lowered = {k.lower(): v for k, v in headers.items()}
    value = lowered.get(header_name.lower())
    if not value:
        raise AuthError(f"missing auth header: {header_name}")
    return value
