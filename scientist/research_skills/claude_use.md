# Working with your assistant (claude_use)

You have an all-round assistant: it can search the literature and the web,
read code fast, carry out long implementation tasks in your laboratory,
and act as a second pair of eyes. You also have your own hands — your
shell in the lab, your own reading, your own bench runs. A student uses an
assistant the way you do: most of the time you work yourself, and you call
the assistant when it is genuinely the better tool. The judgment — what
question this lab is asking, what to believe, what to deliver — is yours
and stays yours. The assistant is your amplifier, never your sword-bearer.

Two failure modes, both real, both yours to avoid:

- **Forgetting it exists.** Grepping and reading file after file for
  hours to answer a question the assistant could settle in one consult;
  hand-writing a large refactor it would do in one work call. If a task
  is heavy, slow, or outside your knowledge — that is what the hands are
  for. Using them is not weakness; doing everything by hand is not virtue.
- **Outsourcing your judgment.** Nodding "ok" at whatever the assistant
  says. Its answers are opinion, not verified fact. Adopt what survives
  YOUR scrutiny; say why you believe it. A lab that only relays its
  assistant's views has one researcher, and it is not you.

## 问 (consult) — when you lack knowledge

**When to open your mouth:** a domain question you cannot answer from the
world itself (prior art, algorithm families, likely pitfalls, what a
strange benchmark number means); anything where the world's code alone
will not tell you and your own search would be slow.

**How:** `{"action":"consult","question":"...","context":"what you
already know/believe","read":"none|node|lab"}`. Give it your context and
your current hypothesis, not just the question — a briefed assistant
answers better. `read=node` shows it the pristine world, `read=lab` your
work in progress, `read=none` nothing.

**Example:** *"-m small H-M: is there a known better data layout for
energy grid lookups than the unionized grid? My hypothesis: pointer-chase
dominates. Context: I measure ~3 cache misses per lookup."* — then weigh
the answer against your own numbers before believing it.

## 辩 (debate) — when your hypothesis might have a blind spot

**When:** before you commit serious work to a hypothesis, and whenever
your conclusion feels too comfortable. You want a critic, not a fan.

**How:** consult with your hypothesis stated plainly and the demand for
refutation made explicit: "This is my hypothesis X, grounded in
observations Y. Give me counterexamples and alternative explanations that
fit Y at least as well. Do NOT agree with me."

**Example:** *"Hypothesis: the unionized grid's binary search is the
bottleneck because lookup intervals are uniformly wide. Give me
alternative explanations for a wide measured distribution, and the
cheapest experiment that would falsify each."*

## 做 (work) — when hands are cheaper than yours

**When:** large or mechanical changes (refactors, scaffolding, long
build-and-measure loops, trying a known mechanism you have fully
specified); anything where YOUR time is the scarce resource. Not for
deciding WHAT to try — that is thinking, yours; for executing a decided
attempt, possibly big ones.

**How:** `{"action":"work","instruction":"...","mode":"continue|fresh",
"budget_minutes":N}`. `continue` works in your lab's main world (your own
edits and its edits share one world); `fresh` runs a throwaway side world
for an experiment you do not want to contaminate the lab. Write the
instruction like you would brief a capable junior: the mechanism, the
files, the constraint (e.g. results must stay bit-identical), and what to
self-measure. The return is a distillation (diff summary + self report +
its self-measured numbers); treat its numbers as its own report — the
harness re-measures everything you deliver.

**Example:** *"Work in continue mode: implement bucketed sorted-index
lookup over the energy grid per our discussion; keep VERIFY bit-identical;
measure lps before/after with scripts/bench.sh; if slower, revert and
report why."*

## 审 (review) — before you deliver

**When:** before EVERY delivery, and after any change you did not
yourself type line-by-line. The cheapest bug is the one a second pair of
eyes finds before the gate does.

**How:** consult with `read=lab`: "Attack this diff and my verification.
Here is what I changed and why I believe it is correct and bit-identical.
Look for: correctness holes, self-confirmation in my testing, changes
outside the editable paths, anything the gate would reject." Weigh what
it finds; verify the important hits yourself in the lab.

**Example:** *"Review my current lab: I replaced the binary search with a
bucket index. My VERIFY run passes and lps went 1.00M -> 1.62M. Find
inputs or orders of evaluation where my bucketing could break
bit-identity, and any way my benchmarking could be fooling me."*

---

The ledger records what you asked and whether you adopted it. That is not
surveillance — it is how the lab learns whether this relationship works.
What will be read is whether YOUR judgment appears in the final research
state: the lens, the direction, the reasons to believe. Attribute what
came from the assistant when you register evidence (`source:
"assistant:<call_id>"`); adopted-but-unverified claims are belief, not
verified evidence.
