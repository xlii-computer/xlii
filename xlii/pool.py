"""Round-robin pool of API clients for swarm dispatch.

Primary client is always pool[0] — used for sync + main agent.
Workers call `acquire()` to get the next client; if more workers are running
than there are keys, keys are reused (gRPC + httpx clients are thread-safe).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from xlii.client import Clients, MissingCredentials
from xlii.config import JOURNAL_KEY_LABEL, GlobalConfig

# Substrings that mark an exception as auth-shaped — the key itself is bad
# (revoked, expired, wrong scope), as opposed to a transient failure (network,
# 5xx, timeout) that must NOT cost a healthy key its slot.
_AUTH_FAILURE_MARKERS = (
    "401", "403", "unauthorized", "permission", "expired", "unauthenticated",
    # xAI/OpenAI surface a rejected key as INVALID_ARGUMENT "Incorrect API key
    # provided" — there is no 401/unauthorized token, so match the message.
    # ("invalid api key" is not a substring of the unrelated INVALID_ARGUMENT.)
    "incorrect api key", "invalid api key",
)


def is_auth_failure(exc_or_msg: object) -> bool:
    """True when an exception (or message) looks like an authentication /
    authorization failure — the only kind that should quarantine a key via
    ClientPool.report_auth_failure. THE single source of truth, so a new marker
    is learned in exactly one place instead of drifting between call sites."""
    msg = str(exc_or_msg).lower()
    return any(t in msg for t in _AUTH_FAILURE_MARKERS)


@dataclass
class ClientPool:
    clients: list[Clients]
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _next: int = 0
    _failures: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_config(cls, cfg: GlobalConfig, *, require_management: bool = True) -> "ClientPool":
        try:
            pairs = cfg.key_pairs()
        except RuntimeError as e:
            # Vault-backed keys with no master key: refuse like missing creds,
            # never a traceback out of Face/REPL boot.
            if "vault" in str(e).lower():
                raise MissingCredentials(str(e)) from e
            raise
        if not pairs:
            raise MissingCredentials("no API keys configured")
        clients = [
            Clients.from_keypair(
                p, require_management=require_management, region=cfg.api_region(),
            )
            for p in pairs
        ]
        return cls(clients=clients)

    def rebuild_from_config(self, cfg: GlobalConfig) -> None:
        """Reconstruct every client from *cfg* IN PLACE — the live-flip seam for
        connectivity config (``cfg.region``). Turns already holding a Clients
        they acquired keep it until they finish (graceful drain); everything
        after the swap rides the new construction. Key presence was validated
        at launch, so the rebuild never re-gates management keys. ``_failures``
        survives the swap on purpose: quarantine records KEY-badness
        (revoked/expired), which no edge change cures — clearing it would hand
        a known-bad key back to the worker ring for three more failing turns."""
        pairs = cfg.key_pairs()
        if not pairs:
            raise MissingCredentials("no API keys configured")
        fresh = [
            Clients.from_keypair(p, require_management=False, region=cfg.api_region())
            for p in pairs
        ]
        with self._lock:
            self.clients[:] = fresh
            self._next = 0

    def primary(self) -> Clients:
        # The first NON-journal key: the orchestrator + sync must never spend the
        # dedicated journal key (its whole point is isolated, auditable cost).
        for c in self.clients:
            if c.label != JOURNAL_KEY_LABEL:
                return c
        raise MissingCredentials(
            "only the dedicated journal key is configured — add a chat key for "
            "orchestrator/sync"
        )

    def journal_client(self) -> Optional[Clients]:
        """The client for the dedicated journal key (label == JOURNAL_KEY_LABEL),
        or None when one hasn't been provisioned. Reserved exclusively for the
        journal so its LLM spend is isolated on the xAI dashboard for auditing —
        primary() and acquire() never hand it out. Provision one with
        `xlii journal key`."""
        for c in self.clients:
            if c.label == JOURNAL_KEY_LABEL:
                return c
        return None

    def acquire(self) -> Clients:
        """Hand out a worker client. The primary (index 0) is reserved for
        the orchestrator whenever there are dedicated worker keys — workers
        must not burn the orchestrator's rate budget. The dedicated journal key
        (if any) is excluded entirely — it's the journal's alone. Keys
        quarantined after repeated auth failures are skipped; if everything is
        quarantined we fall back to round-robin over all (degraded beats dead)."""
        with self._lock:
            pool = [c for c in self.clients if c.label != JOURNAL_KEY_LABEL]
            if not pool:
                raise MissingCredentials(
                    "only the dedicated journal key is configured — add worker keys "
                    "for swarm"
                )
            workers = pool[1:] if len(pool) > 1 else pool
            live = [c for c in workers if self._failures.get(c.label, 0) < self.QUARANTINE_AFTER]
            ring = live or workers
            c = ring[self._next % len(ring)]
            self._next += 1
            return c

    QUARANTINE_AFTER = 3

    def report_auth_failure(self, clients: Clients) -> None:
        """Record an auth failure (401/403/expired). At QUARANTINE_AFTER the
        key stops being handed to workers until the process restarts."""
        with self._lock:
            n = self._failures.get(clients.label, 0) + 1
            self._failures[clients.label] = n

    def report_success(self, clients: Clients) -> None:
        with self._lock:
            self._failures.pop(clients.label, None)

    def __len__(self) -> int:
        return len(self.clients)
