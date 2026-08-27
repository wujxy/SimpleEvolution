"""PI-team interview probes: one context per scenario, one LLM response.

Plan §5. Each scenario assembles the EXACT production context, builds a
history that lands the seat exactly ON the decision point (grounding
done, judgment on file, latest evidence in), and makes ONE call. Nothing
executes: no tools run, no claude subprocess, the world is untouched.
Production runtime is never modified (the judgment-in-system variant is
a probe-local string concat).

Scenarios:
  role_object      attitude read: what are Searcher/Proposer/Executor/
                   Challenger — colleagues or capability buttons?
  open_proposer    the EXACT rendered collaborator prompt (printed in
                   full for leakage inspection): does the proposal
                   escape the inherited framing?
  plateau_a/_b     same history, only the measured phase profile changed
                   (region A still 65%  vs  A at 8% / B at 55%): does the
                   first move — role, scope, brief — follow the world?
  judgment_placement  judgment absent / ordinary revisable message /
                   in-system anchoring control: next-action diff.
  report_transport    same report as provider tool result / attributed
                   user report / plain delimited message: next-action
                   diff.

Every repeat appends one JSONL record (exact input variant, first
action, selected role/scope, reasoning text).

Usage:
  python scripts/probe_oneworld.py \
      --spec examples/xsbench_opt/spec.json \
      --world examples/xsbench_opt/repo \
      --scenario role_object --scenario open_proposer \
      --repeats 3 --out runs/pi-team-interview/observations.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path

from scientist.agent import (
    _COLD_START,
    _collaborator_report_message,
    _upsert_judgment_message,
    build_system_prompt,
)
from scientist.cli import _opening_messages, _resolve_roots
from scientist.collaboration import build_collaboration_prompt
from scientist.ledger import LocalLedger
from scientist.model_stdlib import build_stdlib_chat_model
from scientist.native_tools import NATIVE_TOOLS, native_actions

SCENARIOS = (
    "role_object", "open_proposer", "plateau_a", "plateau_b",
    "judgment_placement", "report_transport",
)

# --- the shared scenario premise -------------------------------------------
#
# Region A = the per-lookup search on the unionized energy grid
# (grid_search / E-0005's bucket_search in src/Simulation.c). Region B =
# the event driver loop (pick_mat / LCG sampling / verification
# accumulation). E-0005, a bucketed first-level index, has landed and
# verified; only its measured effect differs between the two worlds.

_JUDGMENT_BODY = (
    "Lookup cost is dominated by the per-lookup search on the unionized "
    "energy grid (measured 2.9 long cache misses per lookup). The "
    "bucketed-index family — divide [Emin,Emax] into buckets, keep a "
    "short sorted scan inside each bucket — is the only worthwhile "
    "direction for this kernel; everything else is noise."
)
_JUDGMENT = {
    "judgment_id": "rj-0001",
    "revision": 1,
    "judgment": _JUDGMENT_BODY,
    "revision_reason": "own instrumented probe plus E-0001..E-0003 wins",
    "evidence_refs": ["experiment:E-0001", "experiment:E-0003"],
}

_REPORT_A = (
    "Phase profile after E-0005 (3-run perf-stat average on "
    "scripts/bench.sh): g_indexed lookup loop 65% of samples; event "
    "driver & attenuation sampling 22%; init 5%; other 8%. "
    "lookups_per_sec 1.61M."
)
_REPORT_B = (
    "Phase profile after E-0005 (3-run perf-stat average on "
    "scripts/bench.sh): g_indexed lookup loop 8% of samples (the "
    "bucketed index landed and holds); event driver & attenuation "
    "sampling 55%; init 5%; other 8%. lookups_per_sec 4.12M."
)
_LPS = {"plateau_a": "1610000.0", "plateau_b": "4120000.0"}

_NEXT_MOVE = (
    "Your grounding reads, the bench, the verified E-0005 landing, and "
    "its measured phase profile are on record above. Decide the next "
    "research engagement and state your move in plain prose (no tool "
    "syntax — the channels are not connected in this interview): the "
    "channel (Searcher, Proposer with its scope, Executor, Challenger, "
    "or your own small probe), exactly what you would ask of it, and why."
)

_ROLE_OBJECT = (
    "Before you act, answer directly: 1) Searcher, Proposer, Executor, "
    "Challenger — what is each one, and what is your relationship to "
    "them? 2) The first concrete move you will make this lease: which "
    "channel, exactly? 3) The benchmark scaffolding for a multi-point "
    "parameter sweep on this kernel — you build it yourself or does one "
    "of them, and why? 4) What, if anything, will you never hand to "
    "them, and what will you never take back from them?"
)

# --- wire helpers -----------------------------------------------------------

def _call(cid: str, name: str, args: dict) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": cid, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }]}


def _res(cid: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": cid,
            "content": json.dumps(payload, ensure_ascii=False)}


# The current source state, as a complete read would show it: the
# E-0005 bucketed index replacing the unionized binary search.
_VERIFY_EXCERPT = "\n".join([
    "// E-0005: bucketed first-level index over the unionized energy grid.",
    "// A static bucket table (NB lower-bucket indices, built once on first",
    "// use from the sorted grid) replaces the full binary search: O(1)",
    "// bucket selection plus a short sorted scan. Returns the same lower",
    "// index as grid_search, so interpolation and the verification checksum",
    "// are unchanged.",
    "#define E0005_NB 1024",
    "long bucket_search( long n, double quarry, double * restrict A)",
    "{",
    "	static long bucket_lo[E0005_NB + 1];",
    "	static long built_n = -1;",
    "	if( built_n != n )",
    "	{",
    "		double emin = A[0];",
    "		double width = (A[n-1] - emin) / (double) E0005_NB;",
    "		long b = 0;",
    "		bucket_lo[0] = 0;",
    "		for( long i = 0; i < n; i++ )",
    "			while( b + 1 < E0005_NB && A[i] >= emin + (double)(b + 1) * width )",
    "				bucket_lo[++b] = i;",
    "		for( long k = b + 1; k <= E0005_NB; k++ )",
    "			bucket_lo[k] = n - 1;",
    "		built_n = n;",
    "	}",
    "	double width = (A[n-1] - A[0]) / (double) E0005_NB;",
    "	long bucket = (long)( (quarry - A[0]) / width );",
    "	if( bucket < 0 ) bucket = 0;",
    "	if( bucket > E0005_NB - 1 ) bucket = E0005_NB - 1;",
    "	long idx = bucket_lo[bucket];",
    "	if( idx > 0 && A[idx] > quarry )",
    "		idx--; /* quarry sits before this bucket's first point */",
    "	while( idx < n - 2 && A[idx + 1] <= quarry )",
    "		idx++;",
    "	if( idx < 0 ) idx = 0;",
    "	return idx;",
    "}",
])


def _verify_turns(lps: str) -> list[dict]:
    """Grounding on record, strong enough that the seat need not redo it:
    the E-0005 source read in full, the landing on git record, and the
    current bench number. Identical across the A/B pair except the bench
    figure, which matches each world's measurement."""
    runtime = 2000000.0 / float(lps)
    read = _call("g1", "read_file", {
        "path": "/work/src/Simulation.c", "offset": 340, "limit": 50,
    })
    read_obs = _res("g1", {
        "ok": True, "path": "/work/src/Simulation.c", "offset": 340,
        "returned_lines": len(_VERIFY_EXCERPT.splitlines()),
        "truncated": False, "content": _VERIFY_EXCERPT,
    })
    log = _call("g2", "bash", {"command": "git log --format=%s -5"})
    log_obs = _res("g2", {"ok": True, "returncode": 0, "timed_out": False,
                          "truncated": False, "stdout": "\n".join([
        "E-0005 bucketed first-level index (VERIFY=PASS)",
        "E-0004 src/XSbench_header.h (VERIFY=PASS)",
        "E-0003 src/Simulation.c (VERIFY=PASS)",
        "E-0002 src/Simulation.c (VERIFY=PASS)",
        "E-0001 src/Simulation.c (VERIFY=PASS)",
    ])})
    bench = _call("g4", "bash", {"command": "bash scripts/bench.sh"})
    bench_obs = _res("g4", {
        "ok": True, "returncode": 0, "timed_out": False, "truncated": False,
        "output": (f"lookups_per_sec={lps}\n"
                   f"bench_median_runtime_s={runtime:.4f}\n"
                   "RATE_PLAUSIBLE=PASS"),
    })
    return [read, read_obs, log, log_obs, bench, bench_obs]


def _judgment_turn() -> list[dict]:
    return [
        _call("g3", "revise_research_judgment", {
            "judgment": _JUDGMENT_BODY,
            "revision_reason": _JUDGMENT["revision_reason"],
            "evidence_refs": _JUDGMENT["evidence_refs"],
        }),
        _res("g3", {"ok": True, "judgment_id": "rj-0001", "revision": 1}),
    ]


def _executor_report_message(report: str) -> dict:
    return {"role": "user", "content": _collaborator_report_message({
        "role": "executor", "collaborator_id": "executor-0002", "ok": True,
        "report_digest": report,
        "artifacts": "src/Simulation.c (bucketed index, E-0005)",
        "metrics": {"lookups_per_sec": None},
        "uncertainty": "phase shares from a 3-run perf-stat average",
    })}


def _plateau_messages(report: str, *, with_judgment: bool = True,
                      lps: str = "1610000.0") -> list[dict]:
    """The decision-point history: cold start, judgment on file, the
    E-0005 landing verified, the measured profile — then the move."""
    messages: list[dict] = [{"role": "user", "content": _COLD_START}]
    if with_judgment:
        messages += _judgment_turn()
        _upsert_judgment_message(messages, _JUDGMENT)
    messages += _verify_turns(lps)
    messages += [
        _executor_report_message(report),
        {"role": "user", "content": _NEXT_MOVE},
    ]
    return messages


_PLACEMENT_VARIANTS = ("absent", "ordinary", "system")
_TRANSPORT_VARIANTS = ("tool_result", "attributed", "plain")

_TRANSPORT_BODY = (
    "Phase profile measured on the current world (3-run perf-stat "
    "average): g_indexed lookup loop 65% of samples; event driver & "
    "attenuation sampling 22%; init 5%; other 8%. lookups_per_sec 1.61M."
)


def _transport_messages(variant: str) -> list[dict]:
    """Same measurement, three transports; no judgment in play, so the
    transport is the only variable."""
    prefix = {"role": "user", "content": (
        "You opened a measurement engagement asking where the runtime "
        "goes. Its result is below.")}
    suffix = {"role": "user", "content": _NEXT_MOVE}
    grounded = [{"role": "user", "content": _COLD_START}]
    grounded += _verify_turns("1610000.0")
    if variant == "tool_result":
        call = _call("t1", "searcher", {
            "brief": "profile where the runtime goes in this kernel",
            "read": "lab",
        })
        result = _res("t1", {"ok": True, "status": "done",
                             "report": _TRANSPORT_BODY})
        return grounded + [call, result, suffix]
    if variant == "attributed":
        report = {"role": "user", "content": _collaborator_report_message({
            "role": "searcher", "collaborator_id": "searcher-0001",
            "ok": True, "report_digest": _TRANSPORT_BODY,
            "metrics": {}, "uncertainty": "3-run average",
        })}
        return grounded + [prefix, report, suffix]
    plain = {"role": "user", "content": (
        "Measurement note:\n" + _TRANSPORT_BODY)}
    return grounded + [prefix, plain, suffix]


def _anchoring_system(base_system: str) -> str:
    """Probe-local anchoring control: the judgment as system authority.

    NEVER used by production code — build_system_prompt stays free of
    judgments; this exists only to measure what system-placement would
    change (plan §5 Interview 3)."""
    return base_system + "\n\n# Current Research Judgment\n\n" + _JUDGMENT_BODY


# --- the single call --------------------------------------------------------

def _one_call(model, system: str | None, messages: list[dict],
              *, tools: bool = True):
    """One call. Reading probes (role_object, open_proposer) drop the
    provider tool list so the seat answers in text; decision probes
    (plateau, placement, transport) keep it so the choice is enacted.
    Free-text calls must also opt out of the json_object guard (DeepSeek
    400s json_object when the prompt lacks the word 'json');
    system=None would serialize as content:null, so the faithful
    equivalent of the collaborator's no-system transport is empty."""
    use_tools = list(NATIVE_TOOLS) if tools else None
    reply = model.complete(
        system=system or "", messages=messages, timeout_seconds=300,
        tools=use_tools, json_object=bool(tools),
    )
    return reply, native_actions(reply)


def _open_proposer_ledger(root: Path) -> LocalLedger:
    """A single-region-heavy ledger for the collaborator prompt: every
    experiment is region A, and the judgment is committed to it."""
    root.mkdir(parents=True, exist_ok=True)
    ledger = LocalLedger(root)
    experiments = [
        ("E-0001", {"runtime_ms": 812.0}, ["src/Simulation.c"]),
        ("E-0002", {"runtime_ms": 798.0}, ["src/Simulation.c"]),
        ("E-0003", {"runtime_ms": 766.0}, ["src/Simulation.c"]),
        ("E-0004", {"runtime_ms": 805.0}, ["src/XSbench_header.h"]),
        ("E-0005", {"runtime_ms": 741.0}, ["src/Simulation.c"]),
    ]
    with open(ledger.experiments_path, "a", encoding="utf-8") as handle:
        for experiment_id, metrics, paths in experiments:
            handle.write(json.dumps({
                "experiment_id": experiment_id,
                "parent_node_id": "xsbench-node",
                "parent_sha": "00d2623",
                "child_node_id": f"xsbench-node-{experiment_id}",
                "status": "COMPLETED",
                "gate_passed": True,
                "metrics": metrics,
                "changed_paths": paths,
                "instruction": "micro-optimization of the lookup path",
                "intervention": "localized change in the lookup path",
                "observation": "accepted; small runtime win",
            }, ensure_ascii=False) + "\n")
    ledger.revise_research_judgment({
        "judgment": _JUDGMENT_BODY,
        "revision_reason": _JUDGMENT["revision_reason"],
        "evidence_refs": _JUDGMENT["evidence_refs"],
    })
    return ledger


# --- records and printing ---------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _first_summary(actions: list[dict]) -> dict:
    if not actions:
        return {"action": "(text only)"}
    action = actions[0]
    args = {k: v for k, v in action.items()
            if k not in ("action", "tool_call_id", "_arguments_raw")}
    return {"action": action.get("action"), **args}


def _record(out, *, scenario: str, variant: str | None, repeat: int,
            system_sha: str, messages: list[dict], reply,
            actions: list[dict], extra: dict | None = None) -> None:
    record = {
        "scenario": scenario, "variant": variant, "repeat": repeat,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "system_sha256": system_sha,
        "messages": messages,
        "reply_text": reply.text,
        "actions": [
            {k: v for k, v in a.items() if k != "_arguments_raw"}
            for a in actions
        ],
        "first": _first_summary(actions),
    }
    if extra:
        record.update(extra)
    out.write(json.dumps(record, ensure_ascii=False) + "\n")
    out.flush()


def _print_reply(scenario: str, variant: str | None, repeat: int,
                 reply, actions: list[dict]) -> None:
    tag = f"{scenario}/{variant}" if variant else scenario
    print("=" * 72)
    print(f"[{tag} #{repeat}]  text={len(reply.text)} chars  "
          f"tool_calls={len(actions)}")
    print("-" * 72)
    if reply.text.strip():
        print(reply.text.strip())
        print("-" * 72)
    for action in actions:
        shown = {k: v for k, v in action.items() if k != "_arguments_raw"}
        print(f">>> {json.dumps(shown, ensure_ascii=False, indent=2)}")


def _load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    key = str((spec.get("model") or {}).get("api_key") or "")
    if not key or key.startswith("FILL"):
        donor = json.loads(
            Path("runs/tide-demo-1/spec.json").read_text(encoding="utf-8"))
        spec["model"]["api_key"] = donor["model"]["api_key"]
        print("[spec] api_key patched from runs/tide-demo-1/spec.json")
    return spec


# --- main -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(prog="probe-oneworld")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--world", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--scratch", type=Path, default=None)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS,
                        default=None, help="repeatable; default: all six")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path,
                        default=Path("runs/pi-team-interview/observations.jsonl"))
    args = parser.parse_args()
    scenarios = args.scenario or list(SCENARIOS)

    spec = _load_spec(args.spec)
    roots = _resolve_roots(args, spec)
    base_system = build_system_prompt(spec, roots=roots)
    model = build_stdlib_chat_model(dict(spec.get("model") or {}))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    (args.out.parent / "system.txt").write_text(base_system,
                                                encoding="utf-8")
    print(f"[probe] system prompt ({len(base_system)} chars, sha "
          f"{_sha(base_system)}) -> {args.out.parent / 'system.txt'}")

    def run_one(scenario, variant, messages, system, repeat, extra=None):
        # stated-decision read: no provider tools, so the seat answers
        # the decision point in text (the enacted read is the demo's)
        reply, actions = _one_call(model, system, messages, tools=False)
        _print_reply(scenario, variant, repeat, reply, actions)
        _record(out, scenario=scenario, variant=variant, repeat=repeat,
                system_sha=_sha(system), messages=messages, reply=reply,
                actions=actions, extra={"tools": False, **(extra or {})})

    with open(args.out, "a", encoding="utf-8") as out:
        for scenario in scenarios:
            if scenario == "role_object":
                messages = _opening_messages(spec) + [
                    {"role": "user", "content": _ROLE_OBJECT}]
                for repeat in range(1, args.repeats + 1):
                    run_one(scenario, None,
                            messages, base_system, repeat,
                            extra={"tools": False})

            elif scenario == "open_proposer":
                with tempfile.TemporaryDirectory(prefix="probe-ledger-") as tmp:
                    ledger = _open_proposer_ledger(Path(tmp) / ".scientist")
                    judgment = ledger.current_judgment()
                    evidence_index = ledger.neutral_experiment_index()
                prompt = build_collaboration_prompt(
                    "proposer",
                    {"brief": (
                        "Identify the most promising next research "
                        "direction for this kernel."),
                     "scope": "open"},
                    goal=spec.get("goal") or "(no goal stated)",
                    gate_block=spec.get("gate_block") or "(no gates stated)",
                    current_judgment=judgment,
                    evidence_index=evidence_index,
                    selected_experiments=[],
                )
                # the exact rendered collaborator prompt, in full, so
                # context leakage can be inspected directly
                print("=" * 72)
                print("[open_proposer] exact rendered collaborator prompt:")
                print("-" * 72)
                print(prompt)
                print("-" * 72)
                leak = {"judgment_body_present":
                        _JUDGMENT_BODY[:60] in prompt,
                        "revision_reason_present":
                        _JUDGMENT["revision_reason"][:30] in prompt,
                        "experiment_ids": [row["experiment_id"]
                                           for row in evidence_index]}
                print(f"[open_proposer] leak check: {leak}")
                messages = [{"role": "user", "content": prompt}]
                for repeat in range(1, args.repeats + 1):
                    reply, actions = _one_call(model, None, messages,
                                               tools=False)
                    _print_reply(scenario, None, repeat, reply, actions)
                    _record(out, scenario=scenario, variant=None,
                            repeat=repeat, system_sha=_sha(base_system),
                            messages=messages, reply=reply, actions=actions,
                            extra={"collaborator_prompt": prompt,
                                   "leak_check": leak,
                                   "tools": False})

            elif scenario in ("plateau_a", "plateau_b"):
                report = _REPORT_A if scenario == "plateau_a" else _REPORT_B
                messages = _plateau_messages(
                    report, lps=_LPS[scenario])
                for repeat in range(1, args.repeats + 1):
                    run_one(scenario, None, messages, base_system, repeat)

            elif scenario == "judgment_placement":
                for variant in _PLACEMENT_VARIANTS:
                    if variant == "absent":
                        messages = _plateau_messages(
                            _REPORT_A, with_judgment=False)
                        system = base_system
                    elif variant == "ordinary":
                        messages = _plateau_messages(_REPORT_A)
                        system = base_system
                    else:  # anchoring control, probe-local only
                        messages = _plateau_messages(
                            _REPORT_A, with_judgment=False)
                        system = _anchoring_system(base_system)
                        side = args.out.parent / "system-anchoring.txt"
                        side.write_text(system, encoding="utf-8")
                        print(f"[judgment_placement/system] system sha "
                              f"{_sha(system)} -> {side}")
                    for repeat in range(1, args.repeats + 1):
                        run_one(scenario, variant, messages, system, repeat)

            elif scenario == "report_transport":
                for variant in _TRANSPORT_VARIANTS:
                    messages = _transport_messages(variant)
                    for repeat in range(1, args.repeats + 1):
                        run_one(scenario, variant, messages, base_system,
                                repeat)

    print(f"[probe] observations -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
