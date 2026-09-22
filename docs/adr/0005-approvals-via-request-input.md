# ADR 0005: Approvals use graph `RequestInput` and an approval table

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

An owner may approve a purchase order hours after it was drafted, by tapping a
WhatsApp button. The agent run that drafted it cannot block while it waits.

ADK offers tool confirmation (`require_confirmation`), but as of ADK 2.9 it is
marked experimental and does not support `DatabaseSessionService` or
`VertexAiSessionService`. Any real deployment needs a persistent session store.

## Decision

Approvals are modelled as a graph workflow node that yields `RequestInput`, with
the run made resumable through `ResumabilityConfig`. Each pending action is also
written to an `approval` table. The WhatsApp or console response records the
decision and resumes the run by its invocation ID. Action tools are idempotent,
keyed by the recommendation they execute, because ADK may re-run a tool when a
run resumes.

## Consequences

- Approvals work with persistent sessions and survive restarts.
- The approval table doubles as the audit trail of who approved what, and when.
- Revisit when tool confirmation supports persistent session services.

## Amendment, 2026-09-21: as built

The first approval built is for receiving a delivery (ADR 0016). It follows this
decision, with what was learned building it on ADK 2.9.1:

- The workflow has three nodes. `ask_approval` records a numbered request
  (`A-0001`) and yields a `RequestInput`. On the answer, `post_receipt` runs if
  it was approved, or `record_rejection` if it was not.
- The approval table is three append-only tables in the records database
  (ADR 0010): requests, decisions (who, when, and why when rejected or not
  carried out), and the approvals of what was carried out, with its digest.
- Paused runs are kept by ADK's `SqliteSessionService` in a file beside the
  records database. `batchward approvals approve` or `reject` resumes a run by
  its session. ADK finds the invocation from the answer's interrupt id, so the
  invocation id is not stored.
- `ResumabilityConfig` is not used. In ADK 2.9.1 a workflow pauses on a
  `RequestInput` and resumes from a later process without it. It would add
  checkpoint events this workflow does not need, and it is experimental.
- The asking node runs again when the run resumes. It finds its request rather
  than making another, and checks the answer itself. An answer that does not
  read is asked for again under a new interrupt id, with no response schema:
  ADK stores an answer before checking it against a schema, and an invalid one
  would block every later answer to that interrupt.
- Before anything is written, the delivery is matched again from the same
  files, and it is posted only if what it would write has the digest the person
  approved. Posting is idempotent, keyed by the bill (ADR 0016).
- The console is the only way to answer. WhatsApp buttons are not built.
  (Since built: ADR 0019.)

