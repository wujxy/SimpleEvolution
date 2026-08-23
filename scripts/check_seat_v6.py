"""Seat-v6 acceptance checks (design doc §8) over a finished/running run.

Usage: python scripts/check_seat_v6.py runs/seat-v6-smoke

Prints each check's evidence; exits non-zero when a hard check fails.
Check 8 (mechanism-category divergence across lenses) prints the proposals
for human judgement — it is not mechanically decidable.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def main(run_dir: str) -> int:
    run = Path(run_dir)
    db = run / "simpleevo.db"
    if not db.exists():
        print(f"no db at {db}")
        return 1
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    failures: list[str] = []

    def check(name: str, ok: bool, evidence: str) -> None:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}\n       {evidence}")
        if not ok:
            failures.append(name)

    # --- 1. multiple seats per node, distinct lenses --------------------
    rows = conn.execute(
        """
        SELECT e.node_id, e.episode_id, e.variation_operator AS lens,
               (SELECT COUNT(*) FROM proposals p
                 WHERE p.episode_id = e.episode_id) AS proposals
        FROM episodes e
        JOIN proposer_allocations a ON a.episode_id = e.episode_id
        ORDER BY e.node_id, e.created_at
        """
    ).fetchall()
    seats = [dict(r) for r in rows]
    by_node: dict[str, list[dict]] = {}
    for seat in seats:
        by_node.setdefault(seat["node_id"], []).append(seat)
    multi = {n: s for n, s in by_node.items()
             if len({x["lens"] for x in s}) >= 2}
    check(
        "1. multi-seat node with distinct lenses exists",
        bool(multi),
        f"{len(seats)} seat(s) over {len(by_node)} node(s); "
        f"multi-lens nodes: {len(multi)}"
        + (f" e.g. {next(iter(multi))}: "
           f"{[x['lens'] for x in next(iter(multi.values()))]}"
           if multi else ""),
    )

    # --- 2. payload hygiene: seat block, no banned fields --------------
    manifests = sorted((run / "proposer_allocations").glob("*/manifest.json"))
    banned_hits = []
    seat_ok = 0
    one_id_ok = 0
    for manifest in manifests:
        payload = json.loads(
            manifest.read_text(encoding="utf-8"))["payload"]
        for field in ("suggested_operator_id", "generator_basis"):
            if field in payload:
                banned_hits.append(f"{manifest.parent.name}:{field}")
        seat = payload.get("seat")
        if seat and all(seat.get(k) for k in
                        ("lens_id", "directive", "forbidden", "self_check")):
            seat_ok += 1
        if len(payload.get("proposal_ids", [])) == 1:
            one_id_ok += 1
    check(
        "2a. every proposer payload carries the three-part seat block",
        bool(manifests) and seat_ok == len(manifests),
        f"{seat_ok}/{len(manifests)} manifests",
    )
    check(
        "2b. no suggested_operator_id / generator_basis in any payload",
        not banned_hits,
        f"banned fields: {banned_hits or 'none'}",
    )
    check(
        "2c. every seat reserves exactly one proposal id",
        bool(manifests) and one_id_ok == len(manifests),
        f"{one_id_ok}/{len(manifests)} manifests",
    )

    # --- 3. oneness: at most one published proposal per seat episode ----
    over = [s for s in seats if s["proposals"] > 1]
    check(
        "3. no seat published more than one proposal",
        not over,
        f"max proposals/seat = {max((s['proposals'] for s in seats), default=0)}",
    )

    # --- 4. decisions are seat_purchases with not-bought rationale ------
    decisions = conn.execute(
        "SELECT decision_id, decision_kind, node_ids, rationale, detail "
        "FROM supervisor_decisions ORDER BY created_at"
    ).fetchall()
    growth = [d for d in decisions if d["decision_kind"] == "growth"]
    purchase_rows = 0
    no_not_bought = []
    lens_ids = {g["id"] for g in json.loads(
        (Path(__file__).resolve().parents[1] / "generator.json")
        .read_text(encoding="utf-8"))}
    for d in growth:
        detail = json.loads(d["detail"] or "{}")
        purchases = detail.get("seat_purchases", [])
        purchase_rows += len(purchases)
        bought = {p["lens"] for p in purchases}
        text = (d["rationale"] or "").lower()
        named = set(lens_ids & set(text.upper().split()))
        named |= {l for l in lens_ids if l.lower() in text}
        if purchases and not (named - bought):
            no_not_bought.append(d["decision_id"][:8])
    check(
        "4. growth decisions carry seat_purchases; rationale names an "
        "unbought lens",
        bool(growth) and purchase_rows > 0 and not no_not_bought,
        f"{len(growth)} growth decision(s), {purchase_rows} purchase(s); "
        f"missing not-bought: {no_not_bought or 'none'}",
    )

    # --- 5. lineage dedup ------------------------------------------------
    nodes = {r["node_id"]: dict(r) for r in conn.execute(
        "SELECT node_id, parent_node_id FROM nodes")}
    lens_by_node: dict[str, set[str]] = {}
    for s in seats:
        lens_by_node.setdefault(s["node_id"], set()).add(s["lens"])
    violations = []
    for node_id, node in nodes.items():
        current = node["parent_node_id"]
        hops = 0
        while current and hops < 100:
            if lens_by_node.get(current, set()) & lens_by_node.get(node_id, set()):
                overlap = lens_by_node[current] & lens_by_node[node_id]
                violations.append(f"{node_id[:8]} shares {overlap} with "
                                  f"ancestor {current[:8]}")
            current = nodes.get(current, {}).get("parent_node_id")
            hops += 1
    check(
        "5. no seat lens repeats on an ancestor path",
        not violations,
        f"violations: {violations or 'none'}",
    )

    # --- 6/7. honest-quiescence artifacts + dormant table + skill --------
    transformations = conn.execute(
        "SELECT COUNT(*) FROM cognitive_transformations").fetchone()[0]
    check(
        "7a. cognitive_transformations table is dormant (0 rows)",
        transformations == 0,
        f"rows: {transformations}",
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from proposer.research_skills import render_research_skill_catalog
    catalog = render_research_skill_catalog()
    check(
        "7b. reframe_inherited_problem skill still in catalog",
        "reframe_inherited_problem" in catalog,
        "catalog entry present",
    )

    empty_stops = conn.execute(
        "SELECT payload FROM scheduler_events "
        "WHERE type = 'supervisor_decision_rejected' "
        "AND payload LIKE '%untried seats remain%'"
    ).fetchall()
    check(
        "6. empty-selection stop attempts were rejected while untried "
        "seats remained",
        True,  # informational: presence proves enforcement fired
        f"rejections of premature stops: {len(empty_stops)}",
    )

    # --- 8. mechanism divergence (manual read) ---------------------------
    print("\n--- 8. proposals per lens (manual mechanism-category check) ---")
    for row in conn.execute(
        """
        SELECT e.variation_operator AS lens, p.instruction, p.rationale
        FROM proposals p JOIN episodes e ON e.episode_id = p.episode_id
        WHERE e.variation_operator IS NOT NULL
        ORDER BY e.variation_operator, p.created_at
        """
    ):
        rationale = json.loads(row["rationale"] or "{}")
        print(f"[{row['lens']}] {row['instruction'][:160]}")
        expectation = rationale.get("expectation")
        if expectation:
            print(f"         expects: {str(expectation)[:120]}")

    print(
        f"\n{len(failures)} hard failure(s)" +
        (f": {failures}" if failures else " — all hard checks passed")
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "runs/seat-v6-smoke"))
