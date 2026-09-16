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
  lakh", need an explicit tolerance rule in the check.
