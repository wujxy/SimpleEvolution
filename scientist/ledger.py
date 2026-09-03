"""The research ledger: the scientist's records, as world files.

One design decision, per the constitution: there is NO live channel to
any host. The ledger is always local files under the world's
``.scientist/`` directory — standalone and simpleevo modes are the SAME
shape. The harness (when there is one) reads these files after the world
closes; it never sits in the loop. Every post-hoc question — what the
Scientist believed, which role engagements it opened, what it spent — is answered
from here.

Files (all line-JSON, append-only where the scientist writes):
- ``research_state.jsonl``  — append-only Current Research View
                              revisions (rows seeded by the supervisor
                              generation may carry the older
                              ``working_model`` shape; reads tolerate it)
- ``research_memory.jsonl`` — append-only research-memory events. Each
                              item is a persistent identity (R1, R2, …)
                              the Scientist writes in its own words;
                              status changes are appended events, so a
                              qualifier written at creation can never be
                              compressed away later — the projection
                              replays events, history is intrinsic
- ``experiments.jsonl``     — the seeded experiment archive (read-only
                              for the scientist; the harness writes it
                              when it opens the world, from the lineage)
- ``assistant_calls.jsonl`` — one row per role engagement (the name is
                              historical; simpleevo's db readers know it)
- ``record.jsonl``          — the lab's public record: one citable row
                              per engagement report, appended by the
                              harness at collection with its claimed
                              grade and falsifier. The evidence chain's
                              substrate — a colleague's conclusion is
                              citable (REC-001…), never just retold.
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

    # status semantics are the Scientist's alone: "do I keep attending to
    # this?" — nothing here teaches research, and no transition is illegal.
    MEMORY_STATUSES = ("active", "parked", "closed")

    def __init__(self, root: Path):
        self.root = Path(root)
        self.research_state_path = self.root / "research_state.jsonl"
        self.research_memory_path = self.root / "research_memory.jsonl"
        self.experiments_path = self.root / "experiments.jsonl"
        self.assistant_calls_path = self.root / "assistant_calls.jsonl"
        self.record_path = self.root / "record.jsonl"
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

    # -- research memory (persistent items, append-only events) --------------

    def _memory_items(self) -> dict[str, dict]:
        """Project the append-only event log into current items.

        File order is the whole truth: a create seeds the item, later
        events revise content or move status, and nothing is ever
        rewritten — so the qualifier written at creation survives every
        later compression the Scientist's context goes through. Each
        item carries ``_seq`` (its last event's position) for recency
        ordering; it never leaves this class."""
        items: dict[str, dict] = {}
        for seq, event in enumerate(_read_rows(self.research_memory_path)):
            kind = str(event.get("event") or "")
            item_id = str(event.get("item_id") or "")
            if not item_id:
                continue
            if kind == "create":
                items[item_id] = {
                    "item_id": item_id,
                    "content": str(event.get("content") or ""),
                    "status": str(event.get("status") or "active"),
                    "evidence_refs": [
                        str(ref) for ref in event.get("evidence_refs") or ()
                    ],
                    "note": str(event.get("note") or ""),
                    "kind": str(event.get("kind") or ""),
                    "history": [event],
                    "_seq": seq,
                }
                continue
            item = items.get(item_id)
            if item is None:
                continue  # an event for an unknown id: tolerated, ignored
            item["history"].append(event)
            item["_seq"] = seq
            if kind == "revise":
                if str(event.get("content") or "").strip():
                    item["content"] = str(event["content"]).strip()
                for ref in event.get("evidence_refs") or ():
                    text = str(ref).strip()
                    if text and text not in item["evidence_refs"]:
                        item["evidence_refs"].append(text)
                for field in ("note", "kind"):
                    if str(event.get(field) or "").strip():
                        item[field] = str(event[field]).strip()
            elif kind in ("park", "close", "reopen"):
                item["status"] = {
                    "park": "parked",
                    "close": "closed",
                    "reopen": "active",
                }[kind]
        return items

    def _next_memory_id(self) -> str:
        highest = 0
        for item_id in self._memory_items():
            digits = item_id[1:] if item_id[:1] == "R" else ""
            if digits.isdigit():
                highest = max(highest, int(digits))
        return f"R{highest + 1}"

    @staticmethod
    def _memory_row(item: dict) -> dict:
        return {
            "item_id": item["item_id"],
            "status": item["status"],
            "content": item["content"],
            "evidence_refs": list(item.get("evidence_refs") or ()),
        }

    def remember(self, action: dict) -> dict:
        """One cheap sidebar write to the long-term research memory:
        record a new item, or update / re-status an existing one.

        The only field-level hard convention lives here: closing an item
        carries its scope, parking carries its reason — absence is
        rejected at the door so it is visible, not silent. What the
        scope or reason SAYS is the Scientist's judgment."""
        item_id = str(action.get("item_id") or "").strip()
        status = action.get("status")
        if status is not None and status not in self.MEMORY_STATUSES:
            return {
                "ok": False,
                "error": "status must be one of active|parked|closed",
            }
        content = action.get("content")
        refs = action.get("evidence_refs", [])
        if not isinstance(refs, list) or not all(
            isinstance(ref, str) and ref.strip() for ref in refs
        ):
            return {
                "ok": False,
                "error": "evidence_refs must be a list of strings",
            }
        refs = [ref.strip() for ref in refs]
        reason = str(action.get("park_reason") or "").strip()
        scope = str(action.get("close_scope") or "").strip()

        if not item_id:
            if not isinstance(content, str) or not content.strip():
                return {
                    "ok": False,
                    "error": "content is required when recording a new "
                             "research-memory item",
                }
            if status == "parked" and not reason:
                return {
                    "ok": False,
                    "error": "park_reason is required when parking: why "
                             "this item is set aside",
                }
            if status == "closed" and not scope:
                return {
                    "ok": False,
                    "error": "close_scope is required when closing: "
                             "exactly what was tested and found dead "
                             "(which formulation, under what condition) — "
                             "not just the direction's name",
                }
            new_id = self._next_memory_id()
            event = {
                "event": "create",
                "item_id": new_id,
                "content": content.strip(),
                "status": status or "active",
            }
            if refs:
                event["evidence_refs"] = refs
            for field in ("note", "kind"):
                value = str(action.get(field) or "").strip()
                if value:
                    event[field] = value
            if status == "parked":
                event["park_reason"] = reason
            if status == "closed":
                event["close_scope"] = scope
            _append_row(self.research_memory_path, event)
            return {"ok": True, "item_id": new_id, "status": event["status"]}

        item = self._memory_items().get(item_id)
        if item is None:
            return {
                "ok": False,
                "error": f"research memory item not found: {item_id}",
            }
        # validate everything BEFORE writing: a rejected call leaves no
        # half-applied update behind
        status_event = None
        if status is not None and (
            status != item["status"]
            or (status == "parked" and reason)
            or (status == "closed" and scope)
        ):
            if status == "parked" and not reason:
                return {
                    "ok": False,
                    "error": "park_reason is required when parking: why "
                             "this item is set aside",
                }
            if status == "closed" and not scope:
                return {
                    "ok": False,
                    "error": "close_scope is required when closing: "
                             "exactly what was tested and found dead "
                             "(which formulation, under what condition) — "
                             "not just the direction's name",
                }
            status_event = {
                "parked": {"event": "park", "item_id": item_id,
                           "park_reason": reason},
                "closed": {"event": "close", "item_id": item_id,
                           "close_scope": scope},
                "active": {"event": "reopen", "item_id": item_id},
            }[status]
        revise: dict = {"event": "revise", "item_id": item_id}
        if isinstance(content, str) and content.strip():
            revise["content"] = content.strip()
        if refs:
            revise["evidence_refs"] = refs
        for field in ("note", "kind"):
            value = str(action.get(field) or "").strip()
            if value:
                revise[field] = value
        if status_event is None and len(revise) == 2:
            return {
                "ok": False,
                "error": "nothing to update: give content, status, "
                         "evidence_refs, note, or kind",
            }
        if len(revise) > 2:
            _append_row(self.research_memory_path, revise)
        if status_event is not None:
            _append_row(self.research_memory_path, status_event)
        return {
            "ok": True,
            "item_id": item_id,
            "status": status if status is not None else item["status"],
        }

    def search_research_memory(self, action: dict) -> dict:
        query = str(action.get("query") or "")
        status = action.get("status")
        try:
            limit = max(1, min(int(action.get("limit") or 10), 50))
        except (TypeError, ValueError):
            return {"ok": False, "error": "limit must be an integer"}
        terms = _query_terms(query)
        items = sorted(
            self._memory_items().values(),
            key=lambda item: item["_seq"], reverse=True)
        matches = [
            self._memory_row(item) for item in items
            if (status in (None, "") or item["status"] == status)
            and (not terms or all(
                term in " ".join([
                    item["content"], item.get("note") or "",
                    item.get("kind") or "",
                    *(item.get("evidence_refs") or ()),
                ]).lower()
                for term in terms))
        ]
        return {
            "ok": True,
            "results": matches[:limit],
            "total_matches": len(matches),
        }

    def list_research_memory(self, action: dict) -> dict:
        status = action.get("status")
        try:
            limit = max(1, min(int(action.get("limit") or 20), 100))
        except (TypeError, ValueError):
            return {"ok": False, "error": "limit must be an integer"}
        items = sorted(
            self._memory_items().values(),
            key=lambda item: item["_seq"], reverse=True)
        rows = [
            self._memory_row(item) for item in items
            if status in (None, "") or item["status"] == status
        ]
        return {"ok": True, "results": rows[:limit], "total": len(rows)}

    def inspect_research_item(self, action: dict) -> dict:
        item_id = str(action.get("item_id") or "").strip()
        if not item_id:
            return {"ok": False, "error": "item_id must be non-empty"}
        item = self._memory_items().get(item_id)
        if item is None:
            return {
                "ok": False,
                "error": f"research memory item not found: {item_id}",
            }
        return {
            "ok": True,
            "kind": "RESEARCH_MEMORY_ITEM",
            "item_id": item_id,
            "content": item["content"],
            "status": item["status"],
            "tag": item.get("kind") or None,
            "note": item.get("note") or None,
            "evidence_refs": list(item.get("evidence_refs") or ()),
            "history": [dict(event) for event in item["history"]],
        }

    def revise_research_state(self, action: dict) -> dict:
        """The milestone act in two parts: rewrite the Current Research
        View (the same append-only row the judgment channel always
        wrote), and in the same breath record research-memory updates.
        The view is all-or-nothing; each memory update lands or fails on
        its own and is reported individually — a malformed sidebar entry
        must not cost the view rewrite."""
        view = action.get("view")
        reason = action.get("revision_reason")
        refs = action.get("evidence_refs", [])
        if not isinstance(view, str) or not view.strip():
            return {"ok": False, "error": "view must be a non-empty string"}
        if not isinstance(reason, str) or not reason.strip():
            return {
                "ok": False,
                "error": "revision_reason must be a non-empty string",
            }
        if not isinstance(refs, list) or not all(
            isinstance(ref, str) and ref.strip() for ref in refs
        ):
            return {
                "ok": False,
                "error": "evidence_refs must be a list of strings",
            }
        revision = len(_read_rows(self.research_state_path)) + 1
        row = {
            "judgment_id": f"rj-{revision:04d}",
            "revision": revision,
            "judgment": view.strip(),
            "revision_reason": reason.strip(),
            "evidence_refs": [ref.strip() for ref in refs],
        }
        _append_row(self.research_state_path, row)
        out = {"ok": True, "judgment_id": row["judgment_id"],
               "revision": revision}
        updates = action.get("memory_updates")
        if updates:
            recorded = []
            if isinstance(updates, list):
                for entry in updates:
                    recorded.append(
                        self.remember(entry)
                        if isinstance(entry, dict)
                        else {
                            "ok": False,
                            "error": "memory_updates entries must be "
                                     "objects",
                        })
            else:
                recorded.append({
                    "ok": False,
                    "error": "memory_updates must be a list of "
                             "remember-shaped objects",
                })
            out["memory_updates"] = recorded
        return out

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

    def note_record(self, digest: dict) -> dict:
        """Archive one finalized engagement report as a citable record row.

        Appends to ``record.jsonl`` (the lab's public record) and
        returns the row's id. No grading, filtering, or validation
        happens here — the claimed grade and falsifier ride exactly as
        the seat wrote them; the record's whole job is that they are
        visible, attributed, and checkable, not that they are right.
        """
        rows = _read_rows(self.record_path)
        record_id = f"REC-{len(rows) + 1:03d}"
        evidence = digest.get("harness_evidence") or {}
        _append_row(self.record_path, {
            "record_id": record_id,
            "call_id": digest.get("call_id"),
            "role": digest.get("role"),
            "ts": digest.get("finished_at"),
            "status": digest.get("status"),
            "report": digest.get("report_digest"),
            "claim_grade": digest.get("claim_grade") or "",
            "falsifier": digest.get("falsifier") or "",
            "evidence": digest.get("evidence") or [],
            "artifacts": digest.get("artifacts") or [],
            "metrics": digest.get("metrics") or {},
            "uncertainty": digest.get("uncertainty") or "",
            "recommended_follow_up":
                digest.get("recommended_follow_up") or "",
            "workspace": evidence.get("workspace"),
        })
        return {"ok": True, "record_id": record_id}

    def note_assistant_call(self, record: dict) -> None:
        _append_row(self.assistant_calls_path, record)

    def note_usage(self, usage: dict) -> None:
        if isinstance(usage, dict) and usage:
            _append_row(self.usage_path, usage)
