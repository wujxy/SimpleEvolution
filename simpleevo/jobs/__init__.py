"""Job envelope, worker entry points, and scheduler adapters.

Both launch backends implement the same ``BaseSubmitter`` interface:
Worker submission methods return the result path the
Scheduler polls; ``probe_job`` / ``remove_job`` let the Reconciler reconcile
against the live job queue.  Swap backends by constructing the right submitter
in the CLI (``jobs.backend: local | condor``).
"""
from __future__ import annotations

from .base import BaseSubmitter, JobSpec
from .condor import HTCondorSubmitter
from .local import LocalSubmitter

__all__ = [
    "BaseSubmitter",
    "HTCondorSubmitter",
    "JobSpec",
    "LocalSubmitter",
]
