"""Session layer: shared XMPP connections per account, fan-out to browser tabs.

Depends only on the abstract xmpp.interface — never on slixmpp or FastAPI.
"""
