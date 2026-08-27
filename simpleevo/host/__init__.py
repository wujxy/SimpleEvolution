"""The host-side workers: the old JSON-protocol machinery.

Everything here runs OUTSIDE the world container, on the simpleevo host:
the frozen proposer path (orchestrator + scientist + wake, now served via
``simpleevo.jobs.proposer_worker``), the live supervisor/integrator
personas, and the container-command runtime they share.

This directory is the deletion boundary for the oneworld migration: the
package proper (``scientist/`` top level — cli/agent/world/ledger/
assistant_tools/model_stdlib) replaces the proposer with a standalone
in-world agent. Once that path passes its container smoke and the
supervisor switches over, the frozen proposer files here go in one cut;
the shared loop machinery stays until the supervisor and integrator
migrate too.
"""
