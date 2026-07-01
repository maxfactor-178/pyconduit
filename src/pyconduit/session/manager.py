"""AccountManager: one Account per bare JID, shared across all tabs and users.

This is the single entry point the web layer uses. It maps a resolved identity to
a shared Account, creating the XMPP connection on demand and tearing it down when
an account has been idle (no tabs) past the configured timeout.
"""

from __future__ import annotations

import asyncio

from ..audit import AuditLog
from ..config import Config
from ..xmpp import interface as ifc
from ..xmpp.interface import XmppClient
from ..xmpp.slixmpp_client import SlixmppClient
from .account import Account, ClientFactory
from .hub import Subscriber


def _default_client_factory(
    jid: str, password: str, config: Config, on_event: ifc.EventHandler
) -> XmppClient:
    return SlixmppClient(jid, password, config.xmpp, on_event)


class AccountManager:
    def __init__(
        self,
        config: Config,
        audit: AuditLog,
        client_factory: ClientFactory | None = None,
    ):
        self._cfg = config
        self._audit = audit
        self._factory = client_factory or _default_client_factory
        self._accounts: dict[str, Account] = {}
        self._lock = asyncio.Lock()

    async def attach(self, *, jid: str, password: str, sub: Subscriber) -> Account:
        """Attach a tab to the shared account for ``jid``, creating it if needed."""
        async with self._lock:
            account = self._accounts.get(jid)
            if account is None:
                account = Account(
                    jid=jid,
                    password=password,
                    config=self._cfg,
                    audit=self._audit,
                    client_factory=self._factory,
                    on_idle_expired=self._on_idle_expired,
                )
                self._accounts[jid] = account
        await account.attach(sub)
        return account

    async def detach(self, account: Account, sub: Subscriber) -> None:
        await account.detach(sub)

    async def _on_idle_expired(self, account: Account) -> None:
        async with self._lock:
            if account.tab_count == 0 and self._accounts.get(account.jid) is account:
                del self._accounts[account.jid]

    async def shutdown(self) -> None:
        async with self._lock:
            accounts = list(self._accounts.values())
            self._accounts.clear()
        for account in accounts:
            await account.close()

    @property
    def account_count(self) -> int:
        return len(self._accounts)
