# ADR 0003: The language model never produces numbers; tools do

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

The product's advice is financial and regulatory: rupees at risk of expiry,
overcharge exposure against a ceiling price, quantities supplied from a recalled
batch. A language model will occasionally produce a plausible wrong number, and
in this domain a plausible wrong number is worse than none.

## Decision

Every figure an agent states — quantities, amounts, dates, batch numbers — comes
from a deterministic Python tool called in the same turn. The model chooses
which tools to call and explains their results; it does not calculate. A plugin
compares numbers in each response against the turn's tool outputs, treating
batch numbers and dates as exact strings, and rejects responses that introduce
a figure no tool returned.

## Consequences

- The domain logic is ordinary, unit-tested Python that anyone can review.
- Agents need well-designed tools rather than long prompts full of formulas.
- Some natural-sounding summaries, such as rounding "₹1,84,210" to "about ₹1.8
  lakh", need an explicit tolerance rule in the check. A figure may round a
  traced one to the precision written, and may never be more than 10% away from
  it, so "₹2 lakh" can stand for ₹1,84,210 but "₹1 crore" never for ₹50 lakh.
  Numbers written in English words are checked like digits.
- A response that fails the check is replaced by a notice that names no figure,
  because a figure named even as unverified still reaches the reader. What was
  held back is logged on the server for investigation, not sent to the client.
