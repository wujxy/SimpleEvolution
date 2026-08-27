"""The research ledger: the scientist's records, as world files.

One design decision, per the constitution: there is NO live channel to
any host. The ledger is always local files under the world's
``.scientist/`` directory — standalone and simpleevo modes are the SAME
shape. The harness (when there is one) reads these files after the world
closes; it never sits in the loop. Every post-hoc question — what the
Scientist believed, which role engagements it opened, what it spent — is answered
from here.

Files (all line-JSON, append-only where the scientist writes):
- ``research_state.jsonl``  — append-only Current Research Judgment
                              revisions (rows seeded by the supervisor
                              generation may carry the older
                              ``working_model`` shape; reads tolerate it)
- ``experiments.jsonl``     — the seeded experiment archive (read-only
                              for the scientist; the harness writes it
                              when it opens the world, from the lineage)
- ``assistant_calls.jsonl`` — one row per role engagement (the name is
                              historical; simpleevo's db readers know it)
- ``usage.jsonl``           — one row per model call (the token ledger)

The search/inspect logic over the seeded archive is a lean port of the
old L2 memory service's — same row shapes, same bucket semantics — so
the behavior the seat-v2 prompts were tuned against carries over
unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path


def _query_terms(query: str) -> list[str]:
    """Split a free-text query into lowercased, non-empty substring terms."""
    return [term for term in (query or "").lower().split() if term]


def _read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _append_row_text(path: Path, text: str) -> None:
    """Append plain text (the notes log is markdown lines, not JSON)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


class LocalLedger:
    """L1 judgment, pull-only history, and archive files for one world."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.research_state_path = self.root / "research_state.jsonl"
        self.experiments_path = self.root / "experiments.jsonl"
        self.assistant_calls_path = self.root / "assistant_calls.jsonl"
        self.usage_path = self.root / "usage.jsonl"
        self.notes_path = self.root / "notes.md"

    def append_note(self, text: str) -> dict:
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "error": "note.text must be non-empty"}
        _append_row_text(
            self.notes_path,
            f"- {text.strip()}\n",
        )
        return {"ok": True}

    # -- research state (the evolving understanding) ------------------------

    def current_state(self) -> dict | None:
        rows = _read_rows(self.research_state_path)
        return rows[-1] if rows else None

    @staticmethod
    def _as_judgment(row: dict) -> dict:
        """Tolerant READ shape: rows a supervisor generation seeded with
        ``working_model`` normalize on the way out. The only in-run
        writer is revise_research_judgment."""
        normalized = dict(row)
        if "judgment" not in normalized and normalized.get("working_model"):
            normalized["judgment"] = normalized["working_model"]
            normalized["judgment_id"] = normalized.get("research_state_id")
            normalized.setdefault(
                "revision_reason", "seeded row; reason unavailable"
            )
        return normalized

    def current_judgment(self) -> dict | None:
        """Return the latest L1 judgment, normalizing legacy state rows."""
        row = self.current_state()
        return self._as_judgment(row) if row else None

    def revise_research_judgment(self, action: dict) -> dict:
        judgment = action.get("judgment")
        reason = action.get("revision_reason")
        refs = action.get("evidence_refs", [])
        if not isinstance(judgment, str) or not judgment.strip():
            return {"ok": False, "error": "judgment must be a non-empty string"}
        if not isinstance(reason, str) or not reason.strip():
            return {
                "ok": False,
                "error": "revision_reason must be a non-empty string",
            }
        if not isinstance(refs, list) or not all(
            isinstance(ref, str) and ref.strip() for ref in refs
        ):
            return {"ok": False, "error": "evidence_refs must be a list of strings"}
        revision = len(_read_rows(self.research_state_path)) + 1
        row = {
            "judgment_id": f"rj-{revision:04d}",
            "revision": revision,
            "judgment": judgment.strip(),
            "revision_reason": reason.strip(),
            "evidence_refs": [ref.strip() for ref in refs],
        }
        _append_row(self.research_state_path, row)
        return {
            "ok": True,
            "judgment_id": row["judgment_id"],
            "revision": revision,
        }

    def list_research_judgments(self, action: dict) -> dict:
        try:
            limit = max(1, min(int(action.get("limit") or 20), 100))
        except (TypeError, ValueError):
            return {"ok": False, "error": "limit must be an integer"}
        results = []
        for raw in reversed(_read_rows(self.research_state_path)):
            row = self._as_judgment(raw)
            results.append({
                "judgment_id": row.get("judgment_id"),
                "revision": row.get("revision"),
                "revision_reason": row.get("revision_reason"),
                "evidence_refs": list(row.get("evidence_refs") or []),
            })
            if len(results) >= limit:
                break
        return {"ok": True, "results": results}

    def inspect_research_judgment(self, action: dict) -> dict:
        judgment_id = str(action.get("judgment_id") or "").strip()
        if not judgment_id:
            return {"ok": False, "error": "judgment_id must be non-empty"}
        for raw in _read_rows(self.research_state_path):
            row = self._as_judgment(raw)
            if judgment_id in {
                str(row.get("judgment_id") or ""),
                str(row.get("research_state_id") or ""),
            }:
                row["ok"] = True
                row["kind"] = "SUBJECTIVE_RESEARCH_JUDGMENT"
                return row
        return {"ok": False, "error": f"judgment not found: {judgment_id}"}

    def state_on_file(self) -> bool:
        return bool(_read_rows(self.research_state_path))

    # -- the seeded experiment archive --------------------------------------

    def _experiments(self) -> list[dict]:
        return _read_rows(self.experiments_path)

    def search_experiments(self, action: dict) -> dict:
        query = action.get("query") or ""
        filters = action.get("filters") or {}
        limit = int(action.get("limit") or 10)
        buckets = action.get("buckets", True)
        terms = _query_terms(query)
        rows = []
        for experiment in self._experiments():
            if "gate_passed" in filters and bool(
                    experiment.get("gate_passed")) != bool(
                    filters["gate_passed"]):
                continue
            if "status" in filters and experiment.get(
                    "status") != filters["status"]:
                continue
            if "changed_path" in filters:
                prefix = filters["changed_path"]
                if not any(
                    path.startswith(prefix)
                    for path in experiment.get("changed_paths") or ()
                ):
                    continue
            haystack = " ".join([
                str(experiment.get("instruction") or ""),
                *(experiment.get("changed_paths") or ()),
            ]).lower()
            if terms and not all(term in haystack for term in terms):
                continue
            rows.append(self._search_row(experiment))
        rows.sort(key=lambda row: str(row.get("experiment_id") or ""))
        relevant = rows[:limit]
        if not buckets:
            return {"ok": True, "results": relevant,
                    "total_matches": len(rows)}
        anchor_gate = relevant[0].get("gate_passed") if relevant else None
        contrasting = [
            row for row in rows
            if anchor_gate is not None
            and row.get("gate_passed") != anchor_gate
        ][:limit]
        diverse = []
        seen: set[tuple[str, ...]] = set()
        for row in rows:
            signature = tuple(row.get("changed_paths") or ())
            if signature in seen:
                continue
            seen.add(signature)
            diverse.append(row)
            if len(diverse) >= limit:
                break
        return {
            "ok": True,
            "relevant": relevant,
            "contrasting": contrasting,
            "diverse": diverse,
            "total_matches": len(rows),
        }

    @staticmethod
    def _search_row(experiment: dict) -> dict:
        return {
            "experiment_id": experiment.get("experiment_id"),
            "source_world": {
                "node_id": experiment.get("parent_node_id"),
                "sha": experiment.get("parent_sha"),
            },
            "child_node_id": experiment.get("child_node_id"),
            "status": experiment.get("status"),
            "gate_passed": bool(experiment.get("gate_passed")),
            "metrics": dict(experiment.get("metrics") or {}),
            "changed_paths": list(experiment.get("changed_paths") or ()),
        }

    def neutral_experiment_index(self) -> list[dict]:
        """Return every experiment as a sorted, narrative-free fact row."""
        rows = [self._search_row(experiment) for experiment in self._experiments()]
        rows.sort(key=lambda row: str(row.get("experiment_id") or ""))
        return rows

    def _find_experiment(self, experiment_id: str) -> dict | None:
        for row in self._experiments():
            if row.get("experiment_id") == experiment_id:
                return row
        return None

    def inspect_experiment(self, action: dict) -> dict:
        experiment_id = str(action.get("experiment_id") or "")
        experiment = self._find_experiment(experiment_id)
        if experiment is None:
            return {"ok": False,
                    "error": f"experiment not found: {experiment_id}"}
        gate_results = experiment.get("gate_results") or {}
        return {
            "ok": True,
            "experiment_id": experiment_id,
            "source_world": {
                "node_id": experiment.get("parent_node_id"),
                "sha": experiment.get("parent_sha"),
                "metrics": dict(experiment.get("parent_metrics") or {}),
            },
            "intervention": {
                "instruction": experiment.get("instruction"),
                "changed_paths": list(
                    experiment.get("changed_paths") or ()),
            },
            "condition": {
                "recorded_gates": sorted(gate_results),
            },
            "observation": {
                "result_sha": experiment.get("child_sha"),
                "child_node_id": experiment.get("child_node_id"),
                "child_sha": experiment.get("child_sha"),
                "metrics": dict(experiment.get("metrics") or {}),
                "gate": {
                    "passed": bool(experiment.get("gate_passed")),
                    "results": {
                        name: {
                            "passed": bool(result.get("passed"))
                            if isinstance(result, dict) else False,
                            "detail": (
                                result.get("detail")
                                if isinstance(result, dict) else None),
                        }
                        for name, result in gate_results.items()
                    },
                },
                "status": experiment.get("status"),
            },
        }

    def inspect_originating_research_state(self, action: dict) -> dict:
        experiment_id = str(action.get("experiment_id") or "")
        experiment = self._find_experiment(experiment_id)
        if experiment is None:
            return {"ok": False,
                    "error": f"experiment not found: {experiment_id}"}
        state = experiment.get("originating_research_state")
        if not isinstance(state, dict):
            return {
                "ok": False,
                "error": (
                    f"research memo unavailable for experiment: "
                    f"{experiment_id}"
                ),
            }
        return {
            "ok": True,
            "kind": "SUBJECTIVE_RESEARCH_MEMO",
            "experiment_id": experiment_id,
            **state,
        }

    # -- assistant calls and usage (world files, post-hoc readable) ---------

    def note_assistant_call(self, record: dict) -> None:
        _append_row(self.assistant_calls_path, record)

    def note_usage(self, usage: dict) -> None:
        if isinstance(usage, dict) and usage:
            _append_row(self.usage_path, usage)
