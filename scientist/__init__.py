"""The scientist: a standalone, in-world research agent (one world, one agent).

The package proper is the oneworld path — ``python -m scientist.cli
--spec spec.json --world DIR`` drops a scientist into a directory-world
with its claude assistant beside it (one container, one filesystem, equal
capabilities) and walks itself to an exit (deliver_world / abstain),
leaving session, research state, assistant transcripts, usage, and
conclusion.json under the world's ``.scientist/``. Zero simpleevo
imports; the model travels over a pure-stdlib transport.

``scientist/host/`` holds the host-side workers (the old JSON-protocol
machinery: frozen proposer path + live supervisor/integrator) — the
deletion boundary for the oneworld migration.
"""

# A self-declared version string, carried over from the proposer era.
# Informational only, emitted for observability; nothing enforces it.
CONTRACT_VERSION = "proposer-cli-v0"
