# Working with your assistant (claude_use)

Your assistant is part of how this lab works, not an optional tool: you
think, direct, and judge; it builds, measures, and argues back. You keep
your own hands — reading, small probes, independent verification — for
contact that forms your judgment. What you do not do is production by
hand: implementing, refactoring, and measurement campaigns are what the
assistant is for; a researcher who hand-rolls them spends the lease's
scarcest resource, their attention, on the cheapest thing to replace.

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
  measuring. Brief it like a capable junior: mechanism, files,
  constraints, what to self-measure. Its numbers are its own report; your
  verification remains yours.
- **审 review** — before EVERY delivery, and after any change you did not
  yourself type line-by-line: consult with read=lab — "attack this diff
  and my verification" — correctness holes, self-confirmation in testing,
  changes outside editable paths, anything the gate would reject. Weigh
  what it finds; verify the important hits yourself.

The ledger records what you asked and whether you adopted it. Attribute
what came from the assistant when you register evidence (`source:
"assistant:<call_id>"`); adopted-but-unverified claims are belief, not
verified evidence.
