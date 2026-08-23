"""Quick read-only duty-check snapshot for the running supervisor run."""
import json
import pathlib
import sqlite3
import sys

run = sys.argv[1] if len(sys.argv) > 1 else "runs/supervisor-tree-xsbench"
conn = sqlite3.connect(f"{run}/simpleevo.db")
conn.row_factory = sqlite3.Row

print("=== events ===")
for r in conn.execute(
    "SELECT event_id, type, payload FROM supervisor_events"
):
    print(r["event_id"], r["type"], json.loads(r["payload"]))

print("=== nodes ===")
for r in conn.execute(
    "SELECT substr(node_id,1,8) nid, substr(parent_node_id,1,8) par, depth,"
    " status, substr(metrics,0,80) m FROM nodes ORDER BY depth, created_at"
):
    print(dict(r))

print("=== experiments ===")
for r in conn.execute(
    "SELECT substr(experiment_id,1,8) e, substr(parent_node_id,1,8) par,"
    " status, substr(metrics,0,60) m FROM experiments ORDER BY created_at"
):
    print(dict(r))

print("=== allocations ===")
for r in conn.execute(
    "SELECT substr(allocation_id,1,8) a, substr(node_id,1,8) node,"
    " proposals_produced, finished_at IS NULL open FROM proposer_allocations"
):
    print(dict(r))

print("=== decisions ===")
for r in conn.execute(
    "SELECT substr(decision_id,1,8) d, decision_kind, event_cursor_to,"
    " substr(node_ids,0,50) nids, substr(rationale,1,220) why"
    " FROM supervisor_decisions ORDER BY created_at"
):
    print(dict(r))

session = pathlib.Path(f"{run}/supervisor/session/session.jsonl")
if session.exists():
    rounds = {}
    for line in session.read_text().splitlines():
        m = json.loads(line)
        if m.get("role") != "assistant":
            continue
        try:
            action = json.loads(m["content"]).get("action")
        except Exception:
            action = "(notebook)"
        rounds.setdefault(m.get("round", 0), []).append(action)
    print("=== per-turn assistant actions ===")
    for turn in sorted(rounds):
        print(f"  turn {turn}: {rounds[turn]}")

meta = pathlib.Path(f"{run}/supervisor/session/meta.json")
if meta.exists():
    info = json.loads(meta.read_text())
    print("meta:", {k: info[k] for k in info
                    if k in ("supervisor_turn", "event_cursor")})
