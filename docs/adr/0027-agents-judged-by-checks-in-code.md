# ADR 0027: The agent team is judged by cases whose checks are code, never by a model

- **Status:** Accepted
- **Date:** 2026-09-28

## Context

The team has grown to a desk, three specialists, about twenty read-only tools
and several pages of instructions. Every change to those instructions, and every
change of model version, can break something that no unit test covers: the desk
answering a stock question itself, a specialist forgetting to call the tool
whose figures it then states, an answer claiming a batch has been blocked when
no agent can block anything (ADR 0004), or a figure that never came from a tool
at all (ADR 0003).

Reading a few answers by hand does not scale and does not repeat. Comparing
answers word for word is worse: a model's wording moves between runs, so such a
check measures the wording and not the work. What does not vary is who answered,
what was called, which figures were quoted, and what must never be said.

## Decision

- **A case is a question plus checks, and every check is code.** The kinds are:
  which specialist answered, which tools were called, that a figure the tool
  returns appears in the answer, phrases that must appear, phrases that must
  never appear, that the Numbers Guard did not have to withhold the answer, and
  that a question no tool can answer carries no rupee figure at all.
- **No model grades another model's answer.** A check that cannot be written in
  code is not written: it would cost a second call per case, drift with the
  judge's own version, and ask the thing ADR 0003 refuses to trust to mark its
  own work.
- **No expected figure is written into a case.** `Quotes` names a tool and a
  path into what it returns, and reads the figure at the moment of judging, so
  cases do not rot as the data changes and a case cannot pass by agreeing with a
  stale number.
- **A case whose data is not there is left out, not failed.** Each case says
  what the data must hold — a recall on record, a records database, dead stock,
  something to order — and the suite works that out by asking the tools. A
  question about dead stock nobody has says nothing about the team.
- **The cases include what the team must refuse**: a figure no tool can give,
  and an instruction to block a batch.
- **The harness is checked offline; the suite is only meaningful live.** A
  scripted model stands in for Gemini in the fast tests, so that a failing case
  is proved to fail with the right reason without paying a model to misbehave.
  `batchward evals` and `pytest -m live` ask the real model, need
  `GOOGLE_API_KEY`, and are never part of the fast suite.
- `batchward evals` answers 0 only when every case passed, so a change to an
  agent's instructions can be gated on it, and `--out` keeps the run as JSON to
  compare against later. The report prints, for each failure, the question, the
  answer, who answered and what was called.
- The cases are written against the simulated stockist — batch AZ4021,
  Atorvastatin — so they are run on the demo databases, not on a real office's
  data.

## Consequences

- 13 cases and 55 checks now stand behind the instructions in `team.py`. A
  rewording that breaks routing, or drops the "not legal advice" the price
  answer must carry, is visible in one command.
- **The suite has not yet been run against Gemini.** There is no API key on the
  machine it was built on, so what it will find is unknown; the harness is
  proved against a scripted model only. The first live run is a finding in
  itself.
- A run costs money and takes a minute or two, so it is opt-in. Nothing in CI
  calls a model.
- What the suite cannot judge: whether an explanation is any good, whether the
  Hindi reads well, tone, or brevity. Those are read by a person.
- A case that fails is a finding to be fixed in the instructions or the case,
  not retried until it passes. The report gives the evidence to tell which.
- Cases tied to the simulator's own batch and product mean the suite says
  nothing about a real office's data until cases are written for it.

## Alternatives rejected

- **A model as judge.** The usual answer, and the wrong one here: a second call
  per case, a grade that moves with the judge's version, and no way to tell a
  model's mistake from its marker's. Where a check cannot be code, the case is
  left out.
- **Recorded transcripts compared to the answer.** Any rewording fails, so the
  suite would be rewritten after every model update until it was ignored.
- **Expected figures written into the cases.** They are stale as soon as the
  demo data is regenerated, and a stale figure that happens to match is worse
  than no check.
- **Running the suite in the fast tests.** It needs a key, a network and money;
  the fast tests need none of those.
- **Judging the desk's routing by reading the transfer instead of who answered.**
  Who finally answers is what the owner sees; a transfer that is undone or
  ignored would still count as routed.
