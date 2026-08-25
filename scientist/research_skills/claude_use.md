# Working with your assistant (claude_use)

Your assistant is Claude Code — Anthropic's flagship coding agent, and
it is part of how this lab works, not an optional tool. You think,
direct, and judge; it builds, measures, and argues back. It is far
stronger than your own hands at execution: coding, instrumentation,
measurement campaigns, literature and web search through its subagents,
long-horizon tasks carried to completion. Use it hard. You keep your
own hands for contact that forms your judgment — reading that shapes
your model, small probes, independent verification. What you do not do
is production by hand: a researcher who hand-rolls what Claude Code
does better spends the lease's scarcest resource, their attention, on
the one thing most replaceable.

Two failure modes, both real, both yours to avoid:

- **Hand-grinding.** Grepping file after file to answer a question one
  consult would settle; hand-writing a refactor one work call would carry
  out. Delegation needs no justification here; doing production yourself
  does.
- **Outsourcing your judgment.** Nodding "ok" at whatever the assistant
  says. Its answers are opinion, not verified fact. Adopt what survives
  YOUR scrutiny; say why you believe it. A lab that only relays its
  assistant's views has one researcher, and it is not you.

Four uses:

- **问 consult** — knowledge you lack: prior art, algorithm families,
  likely pitfalls, what a strange number means. Brief it with your
  context and current hypothesis; a briefed assistant answers better.
- **辩 debate** — before committing serious work to a hypothesis, and
  whenever a conclusion feels too comfortable: state the hypothesis
  plainly and demand refutation — counterexamples, alternative
  explanations that fit the observations at least as well, the cheapest
  experiment that would falsify each. Do NOT agree with me; you want a
  critic, not a fan.
- **做 work** — execution of a decided attempt: implementing a mechanism
  you have specified, refactors, scaffolding, build-and-measure loops,
  parameter sweeps. You decide WHAT to try; it does the building and the
  measuring, better and faster than you would. work() returns at once —
  it takes the brief and works on its own; keep reading and thinking,
  and its report arrives as its own message when the job is done. It
  solves the implementation details on its own; what the brief states is
  all it knows of your intent — so the brief's precision is the ceiling
  of what you get back: mechanism, files, constraints, what to
  self-measure, the definition of done. Its numbers are its own report;
  your verification remains yours. A job that has not reported back is
  still running; when the next move depends on it and nothing else is
  worth doing, wait for it — that is a legitimate move, not idling.
- **审 review** — before EVERY delivery, and after any change you did not
  yourself type line-by-line: consult with read=lab — "attack this diff
  and my verification" — correctness holes, self-confirmation in testing,
  changes outside editable paths, anything the gate would reject. Weigh
  what it finds; verify the important hits yourself.

The ledger records what you asked and whether you adopted it. Attribute
what came from the assistant when you register evidence (`source:
"assistant:<call_id>"`); adopted-but-unverified claims are belief, not
verified evidence.
