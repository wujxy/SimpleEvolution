"""SQLite schema for SimpleEvolution L2 Research State.

This module owns the DDL.  All writes go through ResearchStore; HTCondor jobs
never open the database directly.
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
    last_active_at REAL NOT NULL
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

-- Research states: immutable Scientist working models.
CREATE TABLE IF NOT EXISTS research_states (
    research_state_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    derived_from_research_state_id TEXT,
    transformation_id TEXT,
    working_model TEXT NOT NULL,
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
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

-- Proposer allocations: telemetry for "who actually got proposer capacity".
CREATE TABLE IF NOT EXISTS proposer_allocations (
    allocation_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    reserved_proposal_ids TEXT NOT NULL DEFAULT '[]',
    started_at REAL NOT NULL,
    finished_at REAL,
    proposals_produced INTEGER NOT NULL DEFAULT 0
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
"""
