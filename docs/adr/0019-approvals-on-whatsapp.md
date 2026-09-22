# ADR 0019: Approvals are answered on WhatsApp by approvers known by phone number

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

The owner who approves a delivery or a claim is rarely at the terminal. They
are on WhatsApp all day, and the blueprint's promise is one tap, not one login.
ADR 0005 built approvals as a workflow that pauses for an answer; the answer can
come from any process that can resume the run.

WhatsApp for business runs through Meta's Cloud API. A business may send
free-form messages, such as a message with reply buttons, only within 24 hours
of the person last writing to it; outside that window it may send only a
template Meta has approved. Replies reach the business as webhook deliveries,
signed with the app's secret, and Meta may deliver the same message more than
once.

## Decision

- A request waiting for approval is sent to each approver with two buttons,
  Approve and Reject, whose payloads name the request (`approve:A-0012`). With a
  template configured, the template is sent instead, its body carrying the
  request number and summary and its two quick-reply buttons the same payloads.
  Sending is always a command a person runs (`whatsapp notify`, or `--notify` when
  putting something up for approval), never automatic.
- Approvers are a list of phone numbers with names, kept in a file outside git.
  A reply from any other number is ignored and not answered. The approver's name
  from the list is the name recorded on the decision.
- Typed replies work as well as taps: `approve A-0012`, or `reject A-0012` and the
  reason. A rejection needs its reason, so the Reject button asks for one rather
  than rejecting on its own.
- The webhook refuses any delivery whose `X-Hub-Signature-256` is not the app
  secret's HMAC-SHA256 of the exact body, before reading it. It answers Meta at
  once and handles the replies after, so matching a delivery again never makes
  Meta give up and deliver the message again.
- Every message from an approver is recorded once in the records database
  (ADR 0010), in a seventh migration, keyed by channel and message id. Meta
  retries a delivery it thinks failed for up to seven days, so a message
  delivered twice is recognised and answered once, and the record shows what each
  approver sent and when.
- Answering goes through the same shared workflow as the console (ADR 0005), so
  what an approval carries out, and every check before it, is the same whichever
  way it was given.
- Messages are sent to numbers with their plus sign and country code: Meta
  reads a number without a plus as being in the business number's own country.
  The Graph API version defaults to the one Meta's documentation shows, v25.0,
  and can be set in `.env`.
- Tokens and secrets come from the environment, usually `.env`, which git
  ignores. Nothing is sent or received without them.

## Consequences

- An owner approves or rejects from their phone, and the outcome comes back as a
  message: what was written, why nothing was, or who answered first.
- The webhook must be reachable from the internet over HTTPS for Meta to deliver
  to it, through a tunnel on a desktop or a hosted service. `whatsapp serve` only
  listens locally.
- Reaching an approver outside the 24-hour window needs a template approved by
  Meta, which the stockist creates in WhatsApp Manager; its wording is theirs.
- Meta's free test number reaches only a handful of registered recipients, and
  business messaging is billed by Meta; neither is checked here.
- Nothing here was tried against Meta's live service; the message formats follow
  Meta's documentation, and the tests use recorded shapes of those messages.
- Only approvals are answered on WhatsApp. The morning brief, alerts and invoice
  photos on WhatsApp are not built.

## Alternatives rejected

- **Approving on a tap alone for rejections too.** A rejection without a reason
  leaves the clerk not knowing what to fix.
- **Trusting the sender's number without the signature.** Anyone could post a
  forged delivery to the webhook naming an approver's number.
- **Sending requests automatically whenever one is made.** Messages go to real
  people and cost money; a person decides to send them.
- **Carrying the ADK invocation id in the button.** The request number is what a
  person reads and types, and it finds the paused run (ADR 0005).
