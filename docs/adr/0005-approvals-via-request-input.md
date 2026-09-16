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
