# Supervisor Tree-Growth Design

**Status:** Approved design
**Date:** 2026-08-22

This document supersedes the stateless, one-shot Supervisor allocation model in
`2026-08-22-supervisor-integrator-agent-runtime-design.md`. It changes only the
normal proposer-allocation path. Existing Scientist, Experiment, Gate, and
Integrator responsibilities remain separate and are not expanded here.

## 1. Goal

SimpleEvolution needs one global controller for the direction in which its
research tree grows. The controller must exploit promising lineages, give
credible new directions a chance, reject low-value expansion, and leave
capacity unused when no available investment is worthwhile.

The goal is not to maximize proposer utilization. It is to spend limited
research and evaluation budget on Nodes with the highest prospective value.

## 2. Core definition

> **The Supervisor is the research tree's growth gate. Whenever a new Node is
> created, the same Supervisor uses its continuously accumulated global
> understanding and actively investigated public evidence to decide whether
> that Node receives one more opportunity to be researched.**

A non-root Node is a new research world produced by one completed Experiment;
the seed root is the initial world presented to the same gate. The
Supervisor's irreducible scientific decision is only:

> Given all evidence accumulated across the research tree, is this Node worth
> one more proposer cost so that research may continue growing from it?

The judgment has three essential considerations, not three mandatory workflow
steps:

- does the Node show credible potential for further improvement;
- does it offer a valuable research opportunity relative to existing
  lineages;
- is investing the next proposer lease in it worth the opportunity cost?

If it is worthwhile, the Supervisor grants one proposer lease. Otherwise it
does not. A refused Node remains part of the public research history and may be
reconsidered if later evidence changes its value.

This is the scientific invariant of the role. Runtime batching may apply the
same judgment to several Nodes in one turn, and new evidence may cause the
Supervisor to reconsider a historical Node. Neither changes the underlying
decision from "does this Node receive another chance to grow?" into general
workflow or resource management.

The Supervisor is a global planner, but Node allocation is its only control
over normal tree growth. The accumulated allocation decisions determine the
shape of the tree:

- repeated investment along productive descendants creates depth;
- investment in credible independent Nodes creates breadth;
- refusing investment parks low-value branches;
- an empty allocation deliberately stops or pauses growth.

Diversity has no guaranteed quota. Novelty, eligibility, and unused capacity
are not sufficient reasons to invest. A new direction must have credible
prospective or information value.

The minimal scientific loop is:

```text
new Node is created
        ↓
the same Supervisor resumes
        ↓
it actively queries the global research history
        ↓
it decides whether the Node may continue growing
        ↓
node ID + rationale
```

Depth and breadth emerge from repeated instances of this decision; they are
not separate modes or quotas. Consecutively approving promising descendants
deepens a lineage. Approving a credible independent mechanism gives a new
direction one chance. Novelty without potential is refused, an exhausted
high-scoring direction may be refused, and the tree stops expanding when no
Node justifies another lease.

Three conditions are therefore foundational:

1. **Cognitive continuity.** Every resumption restores the same logical
   Supervisor, including what it previously inspected, funded, and judged.
2. **Active investigation.** The Harness exposes read-only access to the
   global Node, lineage, Proposal, Experiment, diff, failure, and allocation
   history. It does not prepare a ranking or make the judgment on the
   Supervisor's behalf.
3. **Exclusive growth authority.** Only the Supervisor can grant proposer
   leases. The Scheduler does not fill vacancies, Frontier does not take over,
   and capacity may remain unused.

Process lifetime, event batching, context compaction, stale decisions,
integration triggers, sibling waiting, and step budgets are runtime design
questions. They must preserve this definition and must not expand or redefine
the Supervisor's scientific responsibility.

## 3. Role boundaries

### 3.1 Supervisor

The Supervisor makes three kinds of global judgment, each on its own turn and
never bundled:

- **growth** — chooses zero, one, or several currently allocatable Nodes and
  records a natural-language reason for every allocation decision;
- **integration request** — when distinct branches hold mature, compatible,
  gate-passed results, names the target Node and donor Experiments with a
  selection rationale;
- **epoch review** — judges a completed integration candidate (promote or
  retain) by naming the request under review.

It also:

- maintains the global research view across the whole run;
- investigates public Node, Proposal, Experiment, ResearchState, allocation,
  and budget facts;
- compares new and historical Nodes as investment opportunities.

The Supervisor does not invent optimization proposals, direct a Scientist's
implementation, modify source, execute experiments, or keep capacity busy for
its own sake.

### 3.2 Scientist

A Scientist receives one finite proposer lease on one Node and independently
investigates what technical work, if any, is worth proposing. Its private
session is not visible to the Supervisor. Public Proposal, ResearchState, and
Experiment records are visible through the normal evidence boundary.

### 3.3 Scheduler

The Scheduler provides durable events, enforces budgets and eligibility,
validates Supervisor selections, creates proposer leases, and runs work. It
does not rank Nodes, fill unused capacity, or replace a valid Supervisor policy
with Frontier selection.

### 3.4 Frontier and other roles

Frontier remains a telemetry or explicit baseline view, not an automatic
allocation fallback in a Supervisor run. Integration, when used, remains a
separate protocol and is not bundled into the normal Node-allocation decision.
No additional planning Agent is introduced.

## 4. Supervisor lifetime and wake-up

One run has one logical Supervisor identity. Its session begins with the run
and continues until the run ends. A process or model request may stop between
turns; identity, memory, and the event cursor persist and are restored on the
next turn.

The Supervisor is resumed only when research evidence relevant to future Node
allocation changes:

1. the root is ready for the first proposer investment;
2. an Experiment reaches a terminal outcome, including a new Node,
   gate rejection, or no-change;
3. a proposer lease terminates without creating any Experiment, including
   abstention, failure, or timeout;
4. the user changes the research goal or budget.

Budget changes have a reliable source: the driver's limits (terminal-eval cap,
USD budget) are durable in the run's database and rebuilt from the same run
configuration on restart, so constructing or restarting a run with unchanged
limits emits nothing, while a resumed run with different limits emits a
durable `budget_changed` event — written in the same transaction as the new
limit values, so a crash can never strand an applied change without its
wake event.

Scheduler ticks, telemetry updates, free capacity by itself, and work starting
do not resume the Supervisor.

Events are written durably before notification. Several events that arrive
before the Supervisor runs are delivered together as one incremental batch.
Every event remains individually identifiable and ordered; batching avoids
forcing several partial judgments over sibling results that completed close
together.

## 5. Persistent cognition and context

The Supervisor reuses the existing Scientist continuity pattern rather than a
new memory subsystem:

```text
session.jsonl  append-only complete conversation and tool audit
notebook.md    Supervisor-authored, revisable global research understanding
meta.json      stable identity, prompt version, and consumed event cursor
```

The full archive is never rewritten. The notebook is the Supervisor's own
lossy account of prior allocations, promising or exhausted lineages, open
questions, and judgments worth carrying forward. It is not an authoritative
fact source; the public research ledger wins whenever they disagree.

On resume, the active model context contains:

- the standing Supervisor role and tool contract;
- the Supervisor's current notebook;
- public events since its last consumed cursor.

Old raw tool observations are not replayed merely because they exist. The
Supervisor can query their authoritative records again when a new event makes
them relevant.

The Scientist live-context compaction machinery is generalized and reused.
Normal bounded runs keep their live context intact. When the configured context
threshold is crossed, the runtime retains recent complete tool turns and the
Supervisor rewrites its notebook before suspension. The append-only archive
still preserves everything for audit.

## 6. Public research environment

The Harness provides facts and bounded read-only tools, not a prepared ranking,
potential score, explore/exploit label, or recommended allocation.

The wake-up message contains only the new event batch and stable object IDs.
The Supervisor decides what else to inspect.

The public tool environment reuses the existing L2 evidence tools:

- `inspect_node` for one Node and its direct relations;
- `compare_nodes` for factual side-by-side metrics;
- `lineage` for ancestry;
- `search_experiments` for coverage-oriented experiment search;
- `inspect_experiment` for one Proposal/Experiment outcome;
- `inspect_originating_research_state` for an attributed public memo after
  inspecting its Experiment.

It adds only the missing global and resource views:

- `list_nodes` to browse all Nodes, independent of Frontier or allocation
  eligibility;
- `inspect_node_allocations` to read proposer investment and resulting public
  outcomes for one Node;
- `inspect_run_status` to read available capacity, queued or in-flight work,
  quiescence-relevant facts, and — when the driver set budget policy — the
  `budget` block: terminal evals against the eval cap, USD spend against the
  budget, remaining amounts, and whether the run is already capped. Spend is
  priced from the same shared token-usage ledger the driver's cap reads, so
  the Supervisor's budget facts and the driver's policy never diverge.

The Supervisor has no source-writing or general command tool. Proposal intent,
ResearchState, changed paths, gates, metrics, and outcomes are the appropriate
evidence level for its macroscopic allocation responsibility.

All current and historical Nodes remain queryable, including parked, dormant,
in-flight, allocation-exhausted, and prior-epoch Nodes. Whether a Node can be
selected now is a mechanical fact returned by the environment, not a filter on
what the Supervisor can see.

## 7. Decision semantics

When resumed, the Supervisor may inspect any part of the public environment and
then select any currently allocatable Node, not only the Node named by the
latest event. This is necessary for global planning: new evidence may make an
older leader, a parked branch, or a different lineage the best next
investment. Selecting several Nodes in one turn only batches several instances
of the same per-Node grow/do-not-grow judgment.

No mandatory investigation workflow is prescribed. The standing objective is:

> Choose the set of Nodes for which another proposer lease is currently worth
> its opportunity cost, considering the complete research tree and remaining
> budget. Select none when waiting or stopping is more valuable.

The Supervisor may deliberately wait for other in-flight results by selecting
no Nodes. Their terminal events will resume the same Supervisor with its prior
reasoning intact.

## 8. Minimal output

Every field the model returns is semantic judgment; all mechanical identity is
harness state. Each terminal carries only its own minimal semantics:

**Growth** — selected Node IDs and one reason:

```json
{
  "node_ids": ["node-a", "node-b"],
  "rationale": "Both Nodes justify an independent proposer investment now."
}
```

Waiting or stopping uses an empty selection:

```json
{
  "node_ids": [],
  "rationale": "Relevant sibling experiments remain in flight; wait for evidence."
}
```

**Integration request** — target, donors, and one reason. The request id is
not model output: the harness assigns `ir-<work_id>` after parsing the
terminal, which keeps it stable across retries of the same batch (idempotent
redelivery) and changes exactly when a stale batch grows and the judgment is
re-made:

```json
{
  "target_node_id": "node-a",
  "donor_experiment_ids": ["exp-1", "exp-2"],
  "selection_rationale": "Independent validated wins on the same bottleneck."
}
```

**Epoch review** — the existing request under review (naming an object, not
inventing state), the verdict, and one reason:

```json
{
  "integration_request_id": "ir-supervisor-7",
  "review": "promote",
  "rationale": "The candidate covers every donor's validated result."
}
```

The Harness supplies decision identity, session identity, event cursor,
timestamps, allocation IDs, and integration request IDs. Tool traces already
record what the Supervisor inspected, so the model does not repeat evidence
references. Proposal-slot and Scientist-step limits remain standard lease
configuration rather than Supervisor output.

The Supervisor may select fewer Nodes than available proposer capacity. It is
never required to fill capacity.

## 9. Scheduler application

The Scheduler applies one decision transactionally, for every terminal kind.
One database transaction covers the event-cursor compare-and-swap, the decision
row, every side effect of the decision's kind, the cursor advance, and the
audit event:

1. reject duplicate Node IDs;
2. require every selected Node to exist and remain mechanically allocatable;
3. require the selection count not to exceed free proposer capacity;
4. growth: create one standard proposer lease for each selected Node;
   integration request: create the request row; epoch review: promote the
   epoch or close the request — via explicit transaction-level operations
   dispatched on the decision kind, never an open-ended callback;
5. persist the decision, rationale, triggering event cursor, and links
   atomically;
6. leave all unused capacity idle.

An empty selection means:

- wait when proposer, experiment, or integration work remains in flight;
- quiesce when no work remains that can produce new evidence.

If the world changes before the transaction commits, the decision is not
partially applied — for growth, integration, and epoch review alike: no lease,
no request row, no promotion survives a stale rejection. The new evidence is
delivered back to the same Supervisor session for reconsideration. A stale
decision does not invoke Frontier.

Supervisor/model failure leaves the durable event batch unconsumed and retries
the same logical session. Existing work may finish, but the Scheduler issues no
new automatic allocation while the Supervisor is unavailable.

### 9.1 Capped runs: harvest, never derive

The scheduler derives the cap itself, before any new work starts in a step:
`allocation_disabled = stop_allocating or durable_run_limit_reached`, where
the durable condition is computed from the same eval/spend numbers the budget
view shows (terminal experiments against the eval cap, usage-ledger spend
against the USD budget). The driver's manual `stop_allocating` remains a
distinct cause and is never overwritten — a restarted already-capped run, a
plain `run()`, and a bounded driver therefore behave identically, and the
restart's very first step starts nothing.

With allocation disabled the scheduler initiates no new logical work: no new
gate turns, no new integrator jobs, no new proposer or experiment jobs. Work
already in flight is drained and harvested — including a Supervisor result
that lands after the cap, which closes its attempt and is archived unapplied
(`supervisor_decision_discarded`) because it was formed under a budget state
that no longer holds. The batch stays unconsumed for a resumed run; the
discard is neither a failure nor a retry. A capped run ends with status
`capped` once in-flight work drains, even with unconsumed evidence pending.

## 10. Required invariants and acceptance tests

The implementation must demonstrate:

1. every proposer lease is the result of the Supervisor deciding that its Node
   deserves another opportunity to grow;
2. no Scheduler, Frontier, capacity-filling rule, or other role can grant a
   proposer lease in a Supervisor run;
3. all Supervisor turns in one run use the same stable identity and notebook;
4. terminal Experiment events and proposer-without-Experiment outcomes are
   delivered durably and at least once;
5. retries cannot create duplicate decisions or proposer leases;
6. event batches contain only incremental facts, not a Harness-authored
   ranking or recommendation;
7. the Supervisor can query every historical Node, including non-allocatable
   and non-Frontier Nodes;
8. the Supervisor can select a historical Node unrelated to the newest event;
9. zero, one, and multiple Node selections are all accepted when valid;
10. unused capacity remains idle and causes no repeated allocation request by
   itself;
11. an empty selection waits while work remains and quiesces when none remains;
12. stale or failed Supervisor work never silently falls back to Frontier;
13. context compaction preserves the immutable archive and resumes from the
    Supervisor-authored notebook plus new events;
14. every field of the semantic model output is judgment: growth returns only
    `node_ids` and `rationale`; an integration request only target, donors,
    and rationale (the request id is harness-assigned); an epoch review only
    the request under review, the verdict, and rationale;
15. a stale decision leaves zero side effects for all three terminals — no
    lease, no integration request, no epoch promotion survives it;
16. a capped run harvests results of work already started but derives no new
    work from them, and terminates visibly instead of hanging or spinning.

## 11. Explicit non-goals

This design does not add:

- another planning or monitoring Agent;
- fixed explore/exploit quotas;
- automatic Node potential scores;
- a bandit or reinforcement-learning allocator;
- a required investigation workflow;
- dynamic per-lease token or step budgets;
- Supervisor-authored technical proposals;
- automatic source-level similarity or integration judgments;
- an obligation to keep proposer capacity utilized.

These omissions are deliberate. The complete tree-evolution architecture is
the existing research ledger, autonomous Scientists, mechanical experiments and
gates, and one persistent global Supervisor whose only normal control is Node
allocation.
