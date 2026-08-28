#
# Copyright (C) 2026 GNS3 Technologies Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Short-lived console tickets.

A console ticket is a short random string ("gns3t_" + 16 urlsafe chars) that
replaces the long JWT previously embedded in console WebSocket URLs returned
to LLM clients, which reliably corrupted the ~200-char JWT when retyping it
into a shell command. The ticket carries no information itself — the server
remembers what it maps to, which is what makes a 96-bit random string a
sufficient credential.

Tickets are bound to a single node's console endpoints (console/ws and
console/vnc share the same binding) and are multi-use within their TTL, so
console clients that reconnect after a drop keep working. Redeeming also
re-checks the minting user's token_version, so logging out invalidates
outstanding tickets just like it invalidates JWTs.
"""

import logging
import secrets
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Distinguishes tickets from JWTs (which contain dots) and API keys ("gns3_")
TICKET_PREFIX = "gns3t_"
DEFAULT_TICKET_TTL = 600  # seconds


@dataclass
class ConsoleTicket:
    username: str
    token_version: int
    project_id: str
    node_id: str
    expires_at: float  # time.monotonic() based


class ConsoleTicketService:
    """
    In-memory store for console tickets.

    Single-process asyncio app: minting (MCP tool handlers, via worker
    threads) and redemption (WS auth dependency, event loop) share one dict.
    dict get/set/del are atomic under the GIL and keys are random, so no
    locking is needed. Tickets vanish on restart — clients just request a
    new one.
    """

    def __init__(self) -> None:
        self._tickets: dict[str, ConsoleTicket] = {}

    def mint(
        self,
        username: str,
        token_version: int,
        project_id: str,
        node_id: str,
        ttl: int = DEFAULT_TICKET_TTL,
    ) -> str:
        # 12 random bytes → 16 urlsafe chars (~96 bits); comfortably
        # unguessable within the 10-minute window and short enough that
        # LLM clients copy it without corruption.
        ticket = TICKET_PREFIX + secrets.token_urlsafe(12)
        self._sweep_expired()
        self._tickets[ticket] = ConsoleTicket(
            username=username,
            token_version=token_version,
            project_id=project_id,
            node_id=node_id,
            expires_at=time.monotonic() + ttl,
        )
        return ticket

    def redeem(self, ticket: str, path_params: dict) -> Optional[ConsoleTicket]:
        """
        Validate a ticket against the route it is being used on.

        Returns the ticket record if valid, None otherwise. The route's path
        parameters must match the ticket's binding, which confines a ticket
        to the node's console endpoints — routes without a node_id path
        parameter (notifications, web wireshark, …) always fail the binding.
        """

        record = self._tickets.get(ticket)
        if record is None:
            return None
        if time.monotonic() >= record.expires_at:
            self._tickets.pop(ticket, None)
            return None
        if path_params.get("project_id") != record.project_id or path_params.get("node_id") != record.node_id:
            return None
        return record

    def _sweep_expired(self) -> None:
        now = time.monotonic()
        for ticket in [t for t, record in self._tickets.items() if now >= record.expires_at]:
            del self._tickets[ticket]
