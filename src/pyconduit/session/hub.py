"""A Subscriber is one browser tab attached to an account.

The hub abstraction is deliberately tiny: a subscriber is just an addressable
sink for JSON frames plus the identity of the human behind it (for audit and
because several distinct users may share one account in "helpdesk mode").
"""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

_ids = itertools.count(1)


@dataclass
class Subscriber:
    username: str                                  # trusted proxy username at this tab
    ip: str                                         # client IP (for audit)
    send: Callable[[dict], Awaitable[None]]         # push one JSON frame to this tab
    id: int = field(default_factory=lambda: next(_ids))

    def __hash__(self) -> int:
        return self.id
