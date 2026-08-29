"""The research-memory layer: persistent items, append-only events, the
close-scope convention, retrieval, the view+memory milestone act, the
fork shipping contract, and the pointer-not-feed seat interface.

Design anchor (docs/design/scientist研究记忆层设计.md): an item is "an ID
plus a passage in the Scientist's own words"; the ONLY field-level hard
convention is close-with-scope — absence must be visible at the door, so
a variant's death is never remembered as its parent direction's death.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scientist.agent import dispatch_action
from scientist.assistant_tools import AssistantConfig, InWorldAssistant
from scientist.collaboration import build_collaboration_prompt
from scientist.ledger import LocalLedger
from scientist.native_tools import (
    NATIVE_FORWARDED_ACTIONS,
    NATIVE_TOOL_NAMES,
    REMEMBER_TOOL,
    REVISE_RESEARCH_STATE_TOOL,
)
from scientist.world import LocalWorld


@pytest.fixture()
def ledger(tmp_path: Path) -> LocalLedger:
    return LocalLedger(tmp_path)


# --- group 1: item lifecycle and the field-level conventions -----------------


def test_create_assigns_sequential_ids_and_defaults_active(ledger):
    first = ledger.remember({
        "content": "Timing may contain vertex information beyond radial "
                   "correction.",
        "evidence_refs": ["wire:rec30"],
        "kind": "finding",
    })
    second = ledger.remember({"content": "another recognition"})
    assert first == {"ok": True, "item_id": "R1", "status": "active"}
    assert second["item_id"] == "R2"


def test_close_without_scope_is_rejected_at_the_door(ledger):
    """The one field-level hard convention: a closed door must say what
    exactly died. R7 lost the vertex axis because 'radial-only died' was
    remembered as 'timing died'."""
    ledger.remember({"content": "timing triangulation for vertex"})
    reply = ledger.remember({"item_id": "R1", "status": "closed"})
    assert not reply["ok"]
    assert "close_scope" in reply["error"]
    # nothing half-applied: the item is still active
    assert ledger.inspect_research_item({"item_id": "R1"})["status"] == "active"


def test_close_with_scope_lands_and_scope_survives_in_history(ledger):
    ledger.remember({"content": "timing triangulation for vertex"})
    reply = ledger.remember({
        "item_id": "R1", "status": "closed",
        "close_scope": "radial formulation only, charge-only centroid "
                       "methods; the direction channel was never tested",
    })
    assert reply["ok"] and reply["status"] == "closed"
    detail = ledger.inspect_research_item({"item_id": "R1"})
    assert detail["status"] == "closed"
    closes = [e for e in detail["history"] if e["event"] == "close"]
    assert len(closes) == 1 and "radial formulation" in closes[0]["close_scope"]


def test_park_requires_reason_and_reopen_is_free(ledger):
    ledger.remember({"content": "a direction"})
    assert not ledger.remember({"item_id": "R1", "status": "parked"})["ok"]
    assert ledger.remember({
        "item_id": "R1", "status": "parked",
        "park_reason": "centroid route is usable now; revisit after the "
                       "energy axis closes",
    })["ok"]
    assert ledger.remember({"item_id": "R1", "status": "active"})["ok"]
    assert ledger.inspect_research_item({"item_id": "R1"})["status"] == "active"


def test_remember_validates_shapes(ledger):
    assert not ledger.remember({})["ok"]                      # no content
    assert not ledger.remember({"content": "x", "status": "done"})["ok"]
    assert not ledger.remember({"content": "x", "evidence_refs": [1]})["ok"]
    assert not ledger.remember({"item_id": "R9", "content": "y"})["ok"]
    assert not ledger.remember({"item_id": "R1"})["ok"]       # nothing to do
    ledger.remember({"content": "x"})
    assert not ledger.remember({"item_id": "R1", "note": "  "})["ok"]


def test_create_can_be_born_parked_or_closed(ledger):
    """One cheap call covers 'record what already happened': a result may
    be worth remembering already-dead — with its scope."""
    assert ledger.remember({
        "content": "matched filter", "status": "closed",
        "close_scope": "at 1000x downsample; full-rate cost untested",
    })["status"] == "closed"
    assert ledger.remember({
        "content": "per-PMT calibration", "status": "parked",
        "park_reason": "box too small for the campaign",
    })["status"] == "parked"


# --- group 2: append-only projection ----------------------------------------


def test_events_are_never_rewritten_and_projection_replays_them(ledger):
    ledger.remember({"content": "original wording", "kind": "question"})
    ledger.remember({"item_id": "R1",
                     "content": "revised wording, qualifier kept"})
    detail = ledger.inspect_research_item({"item_id": "R1"})
    assert detail["content"] == "revised wording, qualifier kept"
    creates = [e for e in detail["history"] if e["event"] == "create"]
    assert creates[0]["content"] == "original wording"   # history intact


def test_evidence_refs_accumulate_without_duplicates(ledger):
    ledger.remember({"content": "x", "evidence_refs": ["executor-001"]})
    ledger.remember({"item_id": "R1", "evidence_refs": [
        "executor-001", "reviewer-002"]})
    refs = ledger.inspect_research_item({"item_id": "R1"})["evidence_refs"]
    assert refs == ["executor-001", "reviewer-002"]


def test_items_are_never_deleted_only_statused(ledger, tmp_path):
    ledger.remember({"content": "x"})
    ledger.remember({"item_id": "R1", "status": "closed",
                     "close_scope": "s"})
    rows = [json.loads(line) for line
            in (tmp_path / "research_memory.jsonl").read_text().splitlines()]
    assert all(row["event"] in ("create", "revise", "park", "close", "reopen")
               for row in rows)
    assert len(rows) == 2  # eviction is zero: rows only ever grow


# --- group 3: retrieval trio -------------------------------------------------


def test_search_matches_all_terms_over_content_note_and_refs(ledger):
    ledger.remember({"content": "Timing carries vertex information",
                     "evidence_refs": ["executor-001"]})
    ledger.remember({"content": "energy at floor",
                     "note": "radial map is the lever"})
    hit = ledger.search_research_memory({"query": "vertex timing"})
    miss = ledger.search_research_memory({"query": "vertex nothing-matches"})
    assert [row["item_id"] for row in hit["results"]] == ["R1"]
    assert hit["total_matches"] == 1 and miss["total_matches"] == 0
    by_ref = ledger.search_research_memory({"query": "executor-001"})
    assert by_ref["total_matches"] == 1
    by_note = ledger.search_research_memory({"query": "radial lever"})
    assert by_note["total_matches"] == 1


def test_list_orders_recently_touched_first_and_filters_status(ledger):
    ledger.remember({"content": "first"})
    ledger.remember({"content": "second"})
    ledger.remember({"content": "third"})
    ledger.remember({"item_id": "R1", "status": "parked",
                     "park_reason": "r"})
    listed = ledger.list_research_memory({})
    assert [row["item_id"] for row in listed["results"]] == [
        "R1", "R3", "R2"]          # R1 touched last, floats to the top
    parked = ledger.list_research_memory({"status": "parked"})
    assert [row["item_id"] for row in parked["results"]] == ["R1"]


def test_inspect_unknown_item_errors(ledger):
    assert not ledger.inspect_research_item({"item_id": "R404"})["ok"]
    assert not ledger.inspect_research_item({})["ok"]


# --- group 4: the milestone act (view + memory in one breath) ----------------


def test_revise_research_state_writes_view_and_applies_updates(ledger):
    out = ledger.revise_research_state({
        "view": "Energy is at floor; vertex is the only open axis.",
        "revision_reason": "val 0.0185 milestone",
        "evidence_refs": ["executor-005"],
        "memory_updates": [
            {"content": "joint TOF+PE-count likelihood untested"},
            {"item_id": "R1", "status": "parked",
             "park_reason": "energy axis first"},
        ],
    })
    assert out["ok"] and out["revision"] == 1
    assert all(u["ok"] for u in out["memory_updates"])
    # the view row is the same shape the judgment channel always wrote
    row = ledger.current_judgment()
    assert row["judgment"] == "Energy is at floor; vertex is the only open axis."
    assert row["judgment_id"] == "rj-0001"
    assert ledger.list_research_memory({})["total"] == 1


def test_bad_memory_update_does_not_cost_the_view(ledger):
    out = ledger.revise_research_state({
        "view": "v", "revision_reason": "r",
        "memory_updates": [
            {"item_id": "R9", "status": "closed", "close_scope": "s"},
            {"content": "good item"},
            "not-an-object",
        ],
    })
    assert out["ok"] and out["revision"] == 1
    assert [u["ok"] for u in out["memory_updates"]] == [False, True, False]
    assert ledger.state_on_file()          # the view landed regardless
    assert ledger.list_research_memory({})["total"] == 1


def test_revise_research_state_validates_view(ledger):
    assert not ledger.revise_research_state({"revision_reason": "r"})["ok"]
    assert not ledger.revise_research_state({"view": " "})["ok"]
    assert not ledger.revise_research_state({
        "view": "v", "revision_reason": " "})["ok"]
    assert not ledger.revise_research_state({
        "view": "v", "revision_reason": "r", "evidence_refs": [2]})["ok"]
    assert not ledger.state_on_file()


def test_revision_ids_continue_the_judgment_sequence(ledger):
    ledger.revise_research_judgment({
        "judgment": "first", "revision_reason": "seed"})
    out = ledger.revise_research_state({
        "view": "second", "revision_reason": "milestone"})
    assert out["judgment_id"] == "rj-0002"


# --- group 5: the tool surface and its wording discipline --------------------


def test_memory_tools_are_registered_and_forwarded():
    assert {
        "revise_research_state", "remember", "search_research_memory",
        "list_research_memory", "inspect_research_item",
    } <= set(NATIVE_TOOL_NAMES) & set(NATIVE_FORWARDED_ACTIONS)


def test_memory_tool_descriptions_carry_no_obligation_verbs():
    """KILL_KNOCK precedent: obligation text made behavior worse. The
    memory tools state what the thing IS (persistent, searchable), never
    what the Scientist must do with it."""
    for tool in (REMEMBER_TOOL, REVISE_RESEARCH_STATE_TOOL):
        text = tool["function"]["description"].lower()
        for phrase in ("you must", "you should", "always record",
                       "before you", "do not forget", "make sure"):
            assert phrase not in text, (tool["function"]["name"], phrase)


# --- group 6: dispatch routes the new channels -------------------------------


def test_dispatch_routes_memory_and_view_actions(tmp_path):
    ledger = LocalLedger(tmp_path)
    world = assistant = object()  # unused by these branches
    assert dispatch_action(
        {"action": "remember", "content": "x"},
        world=world, assistant=assistant, ledger=ledger)["ok"]
    assert dispatch_action(
        {"action": "revise_research_state", "view": "v",
         "revision_reason": "r"},
        world=world, assistant=assistant, ledger=ledger)["ok"]
    assert dispatch_action(
        {"action": "search_research_memory", "query": "x"},
        world=world, assistant=assistant, ledger=ledger)["ok"]
    assert dispatch_action(
        {"action": "list_research_memory"},
        world=world, assistant=assistant, ledger=ledger)["ok"]
    assert dispatch_action(
        {"action": "inspect_research_item", "item_id": "R1"},
        world=world, assistant=assistant, ledger=ledger)["ok"]
    # legacy view channel still dispatches (old wires must not break)
    assert dispatch_action(
        {"action": "revise_research_judgment", "judgment": "j",
         "revision_reason": "r"},
        world=world, assistant=assistant, ledger=ledger)["ok"]


# --- group 7: the seat interface — pointer, not feed --------------------------


def _fake_claude(tmp_path: Path) -> Path:
    event = {"type": "result", "result": "```json\n"
             '{"report_digest":"done","evidence":[],"artifacts":[],'
             '"uncertainty":"u","recommended_follow_up":"f"}' "\n```",
             "usage": {}}
    script = tmp_path / "claude.sh"
    script.write_text(
        "#!/bin/sh\ncat >/dev/null\ncat <<'JSONLINE'\n"
        + json.dumps(event) + "\nJSONLINE\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _assistant(tmp_path: Path, *, with_memory: bool) -> InWorldAssistant:
    work = tmp_path / "work"
    scratch = tmp_path / "scratch"
    work.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    ledger = LocalLedger(work / ".scientist")
    if with_memory:
        ledger.remember({"content": "timing carries vertex information"})
    world = LocalWorld(
        work=work, repo=tmp_path, scratch=scratch,
        timeout_seconds=10, cap_chars=1000,
    )
    return InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(_fake_claude(tmp_path))),
        ledger=ledger, episode_id="t",
    )


def test_seat_prompt_gets_pointer_only_when_memory_exists(tmp_path):
    with_mem = _assistant(tmp_path / "a", with_memory=True)
    report = with_mem.engage("challenger", {"brief": "attack the view"})
    assert report["ok"]
    prompt = (tmp_path / "a" / "work" / ".scientist" / "assistant"
              / report["collaborator_id"] / "prompt.txt").read_text()
    # the pointer: existence and location, factually stated
    assert "research memory is at" in prompt
    assert "research_memory.jsonl" in prompt
    # not fed: the seat's prompt carries no memory content and no duty
    assert "timing carries vertex information" not in prompt
    assert "you must read" not in prompt.lower()

    without_mem = _assistant(tmp_path / "b", with_memory=False)
    report = without_mem.engage("challenger", {"brief": "attack the view"})
    assert report["ok"]
    prompt = (tmp_path / "b" / "work" / ".scientist" / "assistant"
              / report["collaborator_id"] / "prompt.txt").read_text()
    assert "research memory is at" not in prompt


def test_fork_ships_memory_file_and_keeps_wire_home(tmp_path):
    assistant = _assistant(tmp_path, with_memory=True)
    report = assistant.engage("proposer",
                              {"brief": "find the next direction",
                               "scope": "open"})
    assert report["ok"]
    fork = tmp_path / "scratch" / f"fresh-{report['collaborator_id']}"
    assert (fork / ".scientist" / "research_memory.jsonl").is_file()
    session = (tmp_path / "work" / ".scientist").rglob("wire.jsonl")
    for wire in session:
        assert not (fork / ".scientist" / wire.parent.name / wire.name).exists()


def test_challenger_view_contract_unchanged():
    """The Challenger attacks the view — its object is the same judgment
    object the view channel writes, unchanged by this layer."""
    text = build_collaboration_prompt(
        "challenger", {"brief": "attack"},
        goal="g", gate_block="b",
        current_judgment={"judgment": "the claim", "revision_reason": "r",
                          "evidence_refs": []},
        evidence_index=[], selected_experiments=[],
    )
    assert "Judgment to attack" in text and "the claim" in text
