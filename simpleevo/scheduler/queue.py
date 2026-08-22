"""Bounded executor queue with mechanical backpressure."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueueConfig:
    max_size: int = 32


class ExecutorQueue:
    """Mechanically bounded FIFO over already-admitted proposals.

    The queue is intentionally mechanical (§12):
      - FIFO dequeue.
      - Overflow proposals (beyond ``max_size``, newest first) become
        ``overflowed_dormant``.
      - Executor idle is a real signal, not a bug.
    """

    def __init__(self, store, config: QueueConfig):
        self._store = store
        self._config = config

    def dequeue(self, n: int = 1) -> list[str]:
        """Return up to ``n`` queued proposal ids in FIFO order."""
        proposals = self._store.queued_proposals(limit=n)
        return [p.proposal_id for p in proposals]

    def size(self) -> int:
        return len(self._store.queued_proposals())

    def enforce_bound(self) -> int:
        """Demote queued proposals beyond ``max_size`` to ``overflowed_dormant``.

        Proposals are ordered FIFO (created_at), so the oldest ``max_size``
        stay queued and the newest overflow — mechanical backpressure, no
        judgment.  Returns the number of proposals overflowed.
        """
        queued = self._store.queued_proposals()
        overflowed = 0
        for proposal in queued[self._config.max_size:]:
            self._store.transition_proposal_status(
                proposal.proposal_id, "overflowed_dormant"
            )
            overflowed += 1
        return overflowed
