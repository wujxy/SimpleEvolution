from __future__ import annotations

import json

from scientist.collaboration import build_collaboration_prompt
from scientist.ledger import LocalLedger
from scientist.assistant_tools import AssistantConfig, InWorldAssistant
from scientist.world import LocalWorld


JUDGMENT = {
    "current_judgment": "The cache path is still the only worthwhile direction.",
    "revision_reason": "three local wins",
    "evidence_refs": ["experiment:E3"],
}
EVIDENCE_INDEX = [
    {
        "experiment_id": "E3",
        "status": "COMPLETED",
        "gate_passed": True,
        "metrics": {"runtime_ms": 810.0},
        "changed_paths": ["src/cache.cc"],
    },
    {
        "experiment_id": "E4",
        "status": "COMPLETED",
        "gate_passed": True,
        "metrics": {"runtime_ms": 790.0},
        "changed_paths": ["src/memory.cc"],
    },
]


def _prompt(role: str, action: dict) -> str:
    return build_collaboration_prompt(
        role,
        action,
        goal="minimize runtime with correctness gates green",
        gate_block="correctness gates must pass",
        current_judgment=JUDGMENT,
        evidence_index=EVIDENCE_INDEX,
        selected_experiments=[],
    )


def test_open_proposer_gets_neutral_index_not_scientist_judgment():
    text = _prompt(
        "proposer", {"brief": "find the next direction", "scope": "open"}
    )
    assert "cache path is still the only worthwhile direction" not in text
    assert "three local wins" not in text
    assert "E3" in text and "E4" in text and "memory.cc" in text


def test_directed_proposer_receives_region_not_autobiography():
    text = _prompt(
        "proposer",
        {
            "brief": "find a structural optimization",
            "scope": "directed",
            "region": "cache evaluation",
        },
    )
    assert "cache evaluation" in text
    assert "three local wins" not in text


def test_searcher_does_not_receive_expected_answer():
    text = _prompt("searcher", {"brief": "locate the dominant allocation path"})
    assert "cache path is still the only worthwhile direction" not in text


def test_challenger_receives_judgment_to_attack():
    text = _prompt("challenger", {"brief": "find the strongest failure mode"})
    assert "cache path is still the only worthwhile direction" in text
    assert "three local wins" in text


def test_executor_receives_intent_constraints_and_definition_of_done():
    text = _prompt(
        "executor",
        {
            "brief": "implement a TOF-aware cache",
            "definition_of_done": "correctness passes; report runtime_ms",
            "workspace": "current",
        },
    )
    assert "TOF-aware cache" in text
    assert "correctness passes" in text
    assert "minimize runtime" in text


def test_every_seat_prompt_carries_station_and_source_order():
    # the mission is an engagement, not an identity claim; the goal and
    # gates are the authority; the brief is the Scientist's account
    actions = {
        "searcher": {"brief": "locate the dominant allocation path"},
        "proposer": {"brief": "find the next direction", "scope": "open"},
        "challenger": {"brief": "find the strongest failure mode"},
        "reviewer": {"brief": "look back over the run"},
        "executor": {
            "brief": "implement a TOF-aware cache",
            "definition_of_done": "correctness passes",
            "workspace": "current",
        },
    }
    for role, action in actions.items():
        text = _prompt(role, action)
        assert "You are engaged as the team's" in text, role
        assert "what your work answers to" in text, role
        assert "the Scientist's account and request" in text, role
        # primary text precedes the account that characterizes it
        assert text.index("Research goal:") < text.index(
            "Engagement brief"), role


def test_brief_characterization_arrives_as_reading_not_law():
    # r6's failure as a unit test: a brief that retells a tolerance as
    # "must remain bit-exact" still arrives verbatim, but framed as one
    # colleague's reading of the text above — which the seat holds,
    # with the authority restated after the brief (the sandwich)
    text = _prompt("challenger", {
        "brief": "HARD CONSTRAINT: the FCN must remain bit-exact, any "
                 "approximation breaks it.",
    })
    assert "must remain bit-exact" in text
    assert "one colleague's reading" in text
    assert "yours to check" in text
    # the sandwich: authority text before the brief AND restated after
    assert text.index("Research goal:") < text.index("Engagement brief")
    assert text.index("Engagement brief") < text.index(
        "remain the authority")


def test_continuation_carries_station_and_authority():
    from scientist.collaboration import build_continuation_prompt
    text = build_continuation_prompt({
        "brief": "X landed; continue with Y",
        "definition_of_done": "report the delta",
    })
    assert "engaged as the team's Executor" in text
    assert "the goal and constraints as written still govern" in text


def test_neutral_index_is_complete_thin_and_sorted(tmp_path):
    ledger = LocalLedger(tmp_path)
    rows = [
        {
            "experiment_id": "E2",
            "status": "COMPLETED",
            "gate_passed": True,
            "metrics": {"runtime_ms": 2},
            "changed_paths": ["b.cc"],
            "instruction": "secret direction text",
            "originating_research_state": {"working_model": "secret model"},
        },
        {
            "experiment_id": "E1",
            "status": "FAILED",
            "gate_passed": False,
            "metrics": {},
            "changed_paths": ["a.cc"],
            "instruction": "another secret",
        },
    ]
    ledger.experiments_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.experiments_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    index = ledger.neutral_experiment_index()
    assert [row["experiment_id"] for row in index] == ["E1", "E2"]
    assert all("instruction" not in row for row in index)
    assert all("originating_research_state" not in row for row in index)


def _runtime(tmp_path) -> InWorldAssistant:
    work = tmp_path / "work"
    scratch = tmp_path / "scratch"
    work.mkdir()
    scratch.mkdir()
    script = tmp_path / "claude.sh"
    event = {
        "type": "result",
        "result": "```json\n"
        '{"report_digest":"independent result","evidence":["observed x"],'
        '"artifacts":[],"uncertainty":"y","recommended_follow_up":"z"}'
        "\n```",
        "usage": {},
    }
    script.write_text(
        "#!/bin/sh\ncat >/dev/null\nsleep 0.3\ncat <<'JSONLINE'\n"
        + json.dumps(event)
        + "\nJSONLINE\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    world = LocalWorld(
        work=work, repo=tmp_path, scratch=scratch,
        timeout_seconds=10, cap_chars=1000,
    )
    ledger = LocalLedger(work / ".scientist")
    return InWorldAssistant(
        world=world,
        config=AssistantConfig(
            model="deepseek-v4-flash", effort="medium",
            command=str(script), goal="improve the system", gate_block="tests pass"
        ),
        ledger=ledger,
        episode_id="test",
    )


def test_all_roles_run_to_completion_and_reports_are_attributed(tmp_path):
    """Synchronous seats (round 4): engage blocks through the whole
    engagement and returns the attributed report itself."""
    runtime = _runtime(tmp_path)
    actions = {
        "searcher": {"brief": "find prior art"},
        "proposer": {"brief": "find a direction", "scope": "open"},
        "executor": {
            "brief": "implement it", "definition_of_done": "tests pass"
        },
        "challenger": {"brief": "attack the current explanation"},
    }
    reports = [runtime.engage(role, action)
               for role, action in actions.items()]
    assert {report["role"] for report in reports} == set(actions)
    assert all(report["collaborator_id"].startswith(report["role"] + "-")
               for report in reports)
    assert all(report["report_digest"] == "independent result"
               for report in reports)


def test_each_engagement_gets_a_fresh_instance(tmp_path):
    runtime = _runtime(tmp_path)
    first = runtime.engage("proposer", {"brief": "scan", "scope": "open"})
    second = runtime.engage("proposer", {"brief": "scan again", "scope": "open"})
    assert first["collaborator_id"] != second["collaborator_id"]


# --- interview probe fixtures (functional: contexts render as designed) ----

def _probe():
    import importlib.util
    from pathlib import Path

    path = (Path(__file__).resolve().parents[2]
            / "scripts" / "probe_oneworld.py")
    spec = importlib.util.spec_from_file_location("probe_oneworld", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_open_proposer_fixture_is_neutral(tmp_path):
    probe = _probe()
    ledger = probe._open_proposer_ledger(tmp_path / ".scientist")
    assert ledger.current_judgment()["judgment_id"] == "rj-0001"
    index = ledger.neutral_experiment_index()
    assert [row["experiment_id"] for row in index] == [
        f"E-000{i}" for i in range(1, 6)]
    prompt = probe.build_collaboration_prompt(
        "proposer",
        {"brief": "Identify the most promising next research direction.",
         "scope": "open"},
        goal="minimize runtime with correctness gates green",
        gate_block="correctness gates must pass",
        current_judgment=ledger.current_judgment(),
        evidence_index=index,
        selected_experiments=[],
    )
    # the neutral index rides in; the Scientist's judgment does not
    assert all(f"E-000{i}" in prompt for i in range(1, 6))
    assert "only worthwhile direction" not in prompt
    assert "E-0001..E-0003 wins" not in prompt


def _assert_wire_adjacent(messages):
    pending = set()
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                pending.add(call["id"])
        elif message.get("role") == "tool":
            assert message["tool_call_id"] in pending
            pending.discard(message["tool_call_id"])


def test_probe_plateau_pair_differs_only_in_latest_report():
    probe = _probe()
    a = probe._plateau_messages(probe._REPORT_A, lps=probe._LPS["plateau_a"])
    b = probe._plateau_messages(probe._REPORT_B, lps=probe._LPS["plateau_b"])
    _assert_wire_adjacent(a)
    _assert_wire_adjacent(b)
    # identical shape and length; exactly one judgment preamble each
    from scientist.agent import _JUDGMENT_MARKER

    assert len(a) == len(b)
    for name, messages in (("a", a), ("b", b)):
        markers = [m for m in messages
                   if _JUDGMENT_MARKER in str(m.get("content"))]
        assert len(markers) == 1, name
    # exactly two differences: the bench observation and the report
    diffs = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert len(diffs) == 2
    contents = [(str(x.get("content")), str(y.get("content")))
                for _, x, y in diffs]
    report_diff = [pair for pair in contents
                   if "8% of samples" in pair[1]]
    assert len(report_diff) == 1
    assert "55%" in report_diff[0][1]
    bench_diff = [pair for pair in contents
                  if "lookups_per_sec=" in pair[0]]
    assert len(bench_diff) == 1
    assert "1610000.0" in bench_diff[0][0]
    assert "4120000.0" in bench_diff[0][1]


def test_probe_transport_variants_shape():
    probe = _probe()
    tool = probe._transport_messages("tool_result")
    attributed = probe._transport_messages("attributed")
    plain = probe._transport_messages("plain")
    # same shape: cold start, shared verify turns, one transport
    # segment, the move prompt
    verify = probe._verify_turns("1610000.0")
    for messages in (tool, attributed, plain):
        assert len(messages) == 10
        assert messages[0]["role"] == "user"
        assert messages[1:7] == verify
        assert "next research engagement" in messages[-1]["content"]
    # tool-result transport: an assistant role call adjacent to its result
    call, result = tool[7], tool[8]
    assert call["role"] == "assistant"
    assert "searcher" in call["tool_calls"][0]["function"]["name"]
    assert result["role"] == "tool" and result["tool_call_id"] == "t1"
    _assert_wire_adjacent(tool)
    # attributed transport: the production report header; the report
    # does NOT ride the tool wire (no t1 tool result)
    assert all(m.get("tool_call_id") != "t1" for m in attributed)
    assert any("Research collaborator report | role=searcher"
               in str(m.get("content")) for m in attributed)
    # plain transport: same body, no attribution header, no tool wire
    assert all(m.get("tool_call_id") != "t1" for m in plain)
    assert all("Research collaborator report" not in str(m.get("content"))
               for m in plain)
    assert any("g_indexed lookup loop 65%" in str(m.get("content"))
               for m in plain)
