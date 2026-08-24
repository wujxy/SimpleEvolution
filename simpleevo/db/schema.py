"""SQLite schema for SimpleEvolution L2 Research State.

This module owns the DDL.  All writes go through ResearchStore with one
narrow exception: a lease's scientist worker upserts its own research-state
head row through ``db.lease_writer`` (scientist-owned research — the seat
is the sole author of its understanding, and the write must survive a
mid-lease crash).  HTCondor jobs never open the database directly.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResearchDBSchema:
    """Immutable DDL bundle.  ``apply`` executes CREATE statements idempotently."""

    @staticmethod
    def apply(conn: sqlite3.Connection) -> None:
        """Create tables and indexes if they do not exist."""
        conn.executescript(_DDL)
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(proposals)").fetchall()
        }
        if "research_state_id" not in columns:
            conn.execute(
                "ALTER TABLE proposals ADD COLUMN research_state_id TEXT"
            )
        if "research_operation" not in columns:
            conn.execute(
                "ALTER TABLE proposals ADD COLUMN research_operation TEXT"
            )
        if "donor_experiment_ids" not in columns:
            conn.execute(
                "ALTER TABLE proposals ADD COLUMN donor_experiment_ids TEXT "
                "NOT NULL DEFAULT '[]'"
            )
        # Scientist-owned research (科学家完整研究制 §8.1): research states
        # grow the six-block structure and become one evolving row per lease
        # (revision+1 per work cycle, written incrementally by the worker).
        state_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(research_states)"
            ).fetchall()
        }
        for name, ddl in (
            ("evidence", "ALTER TABLE research_states ADD COLUMN evidence TEXT"),
            ("experiment_log",
             "ALTER TABLE research_states ADD COLUMN experiment_log TEXT"),
            ("deliverables",
             "ALTER TABLE research_states ADD COLUMN deliverables TEXT"),
            ("conclusion",
             "ALTER TABLE research_states ADD COLUMN conclusion TEXT"),
            ("revision",
             "ALTER TABLE research_states ADD COLUMN revision INTEGER"),
            ("lease_id",
             "ALTER TABLE research_states ADD COLUMN lease_id TEXT"),
        ):
            if name not in state_columns:
                conn.execute(ddl)
        episode_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(episodes)"
            ).fetchall()
        }
        for name, ddl in (
            ("conclusion_type",
             "ALTER TABLE episodes ADD COLUMN conclusion_type TEXT"),
            ("concluded_at",
             "ALTER TABLE episodes ADD COLUMN concluded_at REAL"),
        ):
            if name not in episode_columns:
                conn.execute(ddl)
        allocation_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(proposer_allocations)"
            ).fetchall()
        }
        if "decision_id" not in allocation_columns:
            conn.execute(
                "ALTER TABLE proposer_allocations ADD COLUMN decision_id TEXT"
            )
        # Lease state machine: researching -> awaiting_adjudication ->
        # (reopen -> researching | concluded_*).  NULL (pre-migration rows)
        # reads as 'researching' everywhere via COALESCE.
        if "state" not in allocation_columns:
            conn.execute(
                "ALTER TABLE proposer_allocations ADD COLUMN state TEXT"
            )
        if "reopen_count" not in allocation_columns:
            conn.execute(
                "ALTER TABLE proposer_allocations ADD COLUMN reopen_count "
                "INTEGER NOT NULL DEFAULT 0"
            )
        # Indexes over the new columns live here (after the ALTERs), not in
        # _DDL: executescript runs before the guards, and a legacy database
        # does not have the columns yet.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_research_states_lease "
            "ON research_states(lease_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_allocations_state "
            "ON proposer_allocations(state)"
        )


_DDL = """
-- Research Tree nodes: one world = one git SHA.
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    parent_node_id TEXT REFERENCES nodes(node_id),
    experiment_id TEXT,
    sha TEXT UNIQUE NOT NULL,
    metrics TEXT NOT NULL DEFAULT '{}',
    gate_result TEXT NOT NULL DEFAULT '{}',
    depth INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'dormant', 'dead')),
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_node_id);
CREATE INDEX IF NOT EXISTS idx_nodes_experiment ON nodes(experiment_id);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);

-- Scientist episodes: one Scientist's one complete research act on one Node.
-- Default 1 Node -> 1 Episode; the 1:N relationship is retained so a future
-- variation operator (re-framing / mutation) can add further episodes to a
-- Node without a migration.  ``variation_operator`` names the research
-- condition that produced this episode (nullable in the MVP).
CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    inherited_from_episode_id TEXT REFERENCES episodes(episode_id),
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    variation_operator TEXT,
    created_at REAL NOT NULL,
    last_active_at REAL NOT NULL,
    conclusion_type TEXT,
    concluded_at REAL
);

CREATE INDEX IF NOT EXISTS idx_episodes_inherited ON episodes(inherited_from_episode_id);
CREATE INDEX IF NOT EXISTS idx_episodes_node ON episodes(node_id);

-- Cognitive transformations: one recorded generator challenge.
CREATE TABLE IF NOT EXISTS cognitive_transformations (
    transformation_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    source_research_state_id TEXT,
    operator_id TEXT NOT NULL,
    challenge TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transformations_node
    ON cognitive_transformations(node_id);
CREATE INDEX IF NOT EXISTS idx_transformations_episode
    ON cognitive_transformations(episode_id);

-- Research states: one evolving understanding per lease.  Classic runs
-- (and the integrator lane) still insert immutable snapshot rows via
-- publish_research_batch; a complete-research lease instead keeps ONE head
-- row keyed by lease_id that the worker upserts every work cycle
-- (revision+1) through db.lease_writer.  The six-block columns (evidence /
-- experiment_log / deliverables / conclusion) carry the full-resolution
-- record that successors may only reach by pull, never by push.
CREATE TABLE IF NOT EXISTS research_states (
    research_state_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    derived_from_research_state_id TEXT,
    transformation_id TEXT,
    working_model TEXT NOT NULL,
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    evidence TEXT,
    experiment_log TEXT,
    deliverables TEXT,
    conclusion TEXT,
    revision INTEGER,
    lease_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_research_states_node
    ON research_states(node_id);
CREATE INDEX IF NOT EXISTS idx_research_states_episode
    ON research_states(episode_id);

-- Proposals: Scientist judgment waiting to be tested.
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    instruction TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '{}',
    research_operation TEXT CHECK (research_operation IN ('explore', 'synthesize')),
    donor_experiment_ids TEXT NOT NULL DEFAULT '[]',
    research_state_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN (
            'queued', 'running', 'done',
            'overflowed_dormant', 'dormant'
        )),
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proposals_node ON proposals(node_id);
CREATE INDEX IF NOT EXISTS idx_proposals_episode ON proposals(episode_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);

-- Experiments: one logical scientific verification.
--
-- status holds only SCIENTIFIC terminal states.  Infrastructure failures
-- (worker crash / network / API) never land here; they are recorded on the
-- ``attempts`` table (see §16/§17) and the experiment stays pending/running
-- until a fresh attempt succeeds.  ``executor_failed``/``eval_failed`` are
-- deliberately absent: executor crash = infra → attempt; eval-command
-- non-zero exit is captured by the EVAL_COMMANDS gate → ``gate_rejected``.
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
    parent_node_id TEXT NOT NULL REFERENCES nodes(node_id),
    result_sha TEXT,
    metrics TEXT NOT NULL DEFAULT '{}',
    gate_result TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending', 'running', 'completed',
            'gate_rejected', 'no_change'
        )),
    changed_paths TEXT NOT NULL DEFAULT '[]',
    child_node_id TEXT REFERENCES nodes(node_id),
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_experiments_proposal ON experiments(proposal_id);
CREATE INDEX IF NOT EXISTS idx_experiments_parent ON experiments(parent_node_id);
CREATE INDEX IF NOT EXISTS idx_experiments_child ON experiments(child_node_id);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);

-- Execution attempts: infrastructure-layer retries under a logical work id.
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    logical_work_id TEXT NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('proposer', 'experiment', 'supervisor', 'integrator')),
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK (status IN (
            'ready', 'pending', 'running', 'succeeded', 'failed', 'lost'
        )),
    trace_ref TEXT,
    artifact_ref TEXT,
    host TEXT,
    started_at REAL,
    finished_at REAL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_work ON attempts(logical_work_id, kind);
CREATE INDEX IF NOT EXISTS idx_attempts_status ON attempts(status);

-- Frontier axes: per-axis winner nodes.
CREATE TABLE IF NOT EXISTS frontier_axes (
    axis TEXT NOT NULL,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    value REAL NOT NULL,
    margin REAL NOT NULL,
    hysteresis_anchor REAL,
    since REAL NOT NULL,
    PRIMARY KEY (axis, node_id)
);

CREATE INDEX IF NOT EXISTS idx_frontier_axes_node ON frontier_axes(node_id);

-- Proposer allocations: one research lease.  ``state`` is the lease state
-- machine (researching | awaiting_adjudication | reopen | concluded_*);
-- only 'researching' leases consume proposer capacity — a lease parked on
-- adjudication must not block a new seat purchase.  NULL (legacy rows)
-- reads as 'researching'.
CREATE TABLE IF NOT EXISTS proposer_allocations (
    allocation_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    reserved_proposal_ids TEXT NOT NULL DEFAULT '[]',
    started_at REAL NOT NULL,
    finished_at REAL,
    proposals_produced INTEGER NOT NULL DEFAULT 0,
    state TEXT,
    reopen_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_allocations_node ON proposer_allocations(node_id);
CREATE INDEX IF NOT EXISTS idx_allocations_episode ON proposer_allocations(episode_id);

-- Logical shared baselines. Nodes retain their original single-parent tree.
CREATE TABLE IF NOT EXISTS epochs (
    epoch_id TEXT PRIMARY KEY,
    root_node_id TEXT NOT NULL REFERENCES nodes(node_id),
    previous_epoch_id TEXT REFERENCES epochs(epoch_id),
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_epochs_root ON epochs(root_node_id);

-- Durable request state for a temporary integration investigation.
CREATE TABLE IF NOT EXISTS integration_requests (
    integration_request_id TEXT PRIMARY KEY,
    epoch_id TEXT NOT NULL REFERENCES epochs(epoch_id),
    target_node_id TEXT NOT NULL REFERENCES nodes(node_id),
    donor_experiment_ids TEXT NOT NULL,
    selection_rationale TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'abstained', 'submitted', 'closed', 'promoted')),
    integrator_episode_id TEXT,
    proposal_id TEXT REFERENCES proposals(proposal_id),
    experiment_id TEXT REFERENCES experiments(experiment_id),
    created_at REAL NOT NULL,
    closed_at REAL
);


-- Scheduler audit events.
CREATE TABLE IF NOT EXISTS scheduler_events (
    event_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_type ON scheduler_events(type);
CREATE INDEX IF NOT EXISTS idx_events_created ON scheduler_events(created_at);

-- Supervisor wake events: durable evidence changes that resume the growth
-- gate (tree-growth design §4).  Written before any notification; consumed
-- only through the decision-commit transaction.
CREATE TABLE IF NOT EXISTS supervisor_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN (
        'root_ready', 'experiment_terminal', 'lease_terminal',
        'goal_changed', 'budget_changed'
    )),
    payload TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

-- Authoritative consumption cursor; advanced only inside
-- commit_supervisor_decision.  Session meta.json mirrors it for audit.
CREATE TABLE IF NOT EXISTS supervisor_cursor (
    consumer TEXT PRIMARY KEY,
    last_consumed_event_id INTEGER NOT NULL DEFAULT 0
);

-- One committed Supervisor judgment per wake batch (tree-growth design §8/§9).
CREATE TABLE IF NOT EXISTS supervisor_decisions (
    decision_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    decision_kind TEXT NOT NULL DEFAULT 'growth'
        CHECK (decision_kind IN ('growth', 'integration_request', 'epoch_review')),
    event_cursor_to INTEGER NOT NULL,
    node_ids TEXT NOT NULL DEFAULT '[]',
    rationale TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_supervisor_decisions_work
    ON supervisor_decisions(work_id);

-- Durable run-level budget limits (max evals / USD), installed by the
-- driver through the scheduler and rebuilt from the same run
-- configuration on restart.  The growth gate reads them to weigh
-- remaining budget; a change to an installed limit emits a durable
-- ``budget_changed`` supervisor event.
CREATE TABLE IF NOT EXISTS run_limits (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Unified resource account: seat in-flight, assistant work occupancy and
-- adjudication eval occupancy all land here so oversubscription is
-- measurable (and rejectable) against one ledger instead of three
-- per-subsystem folk accounts (科学家完整研究制 §2.2 资源记账).
CREATE TABLE IF NOT EXISTS resource_ledger (
    ledger_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,               -- 'seat' | 'work' | 'eval'
    ref_id TEXT NOT NULL,             -- allocation_id | assistant call_id | experiment_id
    allocation_id TEXT,
    experiment_id TEXT,
    opened_at REAL NOT NULL,
    closed_at REAL,
    meta TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_resource_ledger_ref
    ON resource_ledger(kind, ref_id);
CREATE INDEX IF NOT EXISTS idx_resource_ledger_open
    ON resource_ledger(kind, closed_at);

-- Assistant call ledger (consult/work): what each lens asked, whether the
-- answer was adopted, at what token cost — the measurement surface for
-- oracle homogenization and 判断外包 (科学家完整研究制 §2.2/§8.2).
CREATE TABLE IF NOT EXISTS assistant_calls (
    call_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    lease_id TEXT,
    lens TEXT,
    kind TEXT NOT NULL,               -- 'consult' | 'work'
    question_digest TEXT NOT NULL DEFAULT '',
    adopted INTEGER,
    usage TEXT NOT NULL DEFAULT '{}',
    world_sha TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assistant_calls_episode
    ON assistant_calls(episode_id);
"""
