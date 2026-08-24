"""Adjudication half of the old experiment package: bench + gate.

Truth, not labor: given a SHA, measure it and rule on it.  The hands
half (agent / worktree / pipeline worker) lives in the scientist's
assistant package and imports FROM here — never the reverse.
"""
from .evaluator import run_eval
from .gate import GateSpec, apply_gates

__all__ = ["run_eval", "GateSpec", "apply_gates"]
