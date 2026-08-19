"""Bounded executor queue with mechanical backpressure."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class QueueConfig:
    max_size: int = 32


class ExecutorQueue:
    """In-memory bounded FIFO queue backed by the proposals table.

    The queue is intentionally mechanical:
      - FIFO dequeue.
      - Overflow proposals become ``overflowed_dormant``.
      - Proposals whose parent node is no longer in the frontier become
        ``dormant``.
      - Executor idle is a real signal, not a bug.
    """

    def __init__(self, store, frontier: set[str], config: QueueConfig):
        self._store = store
        self._frontier = frontier
        self._config = config

    def enqueue(self, proposal_id: str) -> str:
        """Return the final status: queued or overflowed_dormant."""
        proposal = self._store.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"unknown proposal: {proposal_id}")
        if proposal.node_id not in self._frontier:
            self._store.transition_proposal_status(proposal_id, "dormant")
            return "dormant"
        if self.size() >= self._config.max_size:
            self._store.transition_proposal_status(proposal_id, "overflowed_dormant")
            return "overflowed_dormant"
        self._store.transition_proposal_status(proposal_id, "queued")
        return "queued"

    def dequeue(self, n: int = 1) -> list[str]:
        """Return up to ``n`` queued proposal ids in FIFO order."""
        proposals = self._store.queued_proposals(limit=n)
        return [p.proposal_id for p in proposals]

    def size(self) -> int:
        return len(self._store.queued_proposals())

    def cleanup(self) -> int:
        """Mark queued proposals whose parent left the frontier as dormant.

        Returns the number of proposals cleaned up.
        """
        cleaned = 0
        for proposal in self._store.queued_proposals():
            if proposal.node_id not in self._frontier:
                self._store.transition_proposal_status(proposal.proposal_id, "dormant")
                cleaned += 1
        return cleaned
