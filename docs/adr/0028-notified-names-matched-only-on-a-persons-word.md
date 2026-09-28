# ADR 0028: A notified formulation is matched to a stocked one only on a person's word

- **Status:** Accepted
- **Date:** 2026-09-28

## Context

ADR 0013 matches each notified row to the items stocked by molecule and
strength, and says outright what it costs: "A combination whose strength is
notified differently from how the item records it ("500 mg + 125 mg" against
"625 mg") matches no item and is only counted. Mapping such names needs a table
of equivalents, which is not built."

Now that a notification can be read from the PDF as published (ADR 0026), that
gap is the thing standing between a published S.O. and the ceilings it should
place. The disagreements are ordinary: the schedule writes "Amoxycillin and
Potassium Clavulanate" where the office typed "Amoxicillin + Clavulanic acid";
the schedule gives the parts of a combination where the office records the
total; one writes "1 g" and the other "1000 mg"; one spells a molecule with a y
and the other with an i. Neither is wrong.

The temptation is to match what resembles what. A ceiling price is enforced at
billing (ADR 0011): a wrong match blocks stock that is priced correctly, or
lets an overcharge through on stock that is not. Nothing in two names says
which of them the notification meant.

## Decision

- **Names are matched exactly, or by an equivalence a person recorded.** Where
  a notified formulation matches no item, Batchward looks for a record saying
  what that name means, and uses it. There is no third way: resemblance never
  matches anything by itself.
- **An equivalence is a record with a name on it**: the formulation and
  strength as notified, the molecule and strength as the item master records
  them, who recorded it, when, and why. It is kept in the records database
  (ADR 0010) in a twelfth migration, and it applies to every later notification.
- **A correction supersedes rather than overwrites.** A later record for the
  same notified name wins, and both stay on file, as a hold and its release do
  (ADR 0008). `ceilings equivalents list` shows every record, marking the ones
  that have been superseded.
- **Batchward does the looking, a person does the deciding.** For each notified
  row that matched nothing, the import prints up to three stocked formulations
  that are close, each with the reason it is close — the parts of the strength
  adding up, the same strength written in another unit, a name spelled a letter
  or two apart, or names sharing their words — and the command that would record
  it, ready to be corrected and run.
- **An equivalence that matches nothing is reported, not ignored.** An item
  master that has been rebuilt can leave a record pointing at a name nobody
  stocks; the import says so rather than passing over it.
- **The import says what it matched through.** A row priced because of someone's
  record is printed with their name, so a wrong equivalence is visible in the
  run that used it, not only in the table.
- Recording a name as itself is refused: it would mean nothing, and it hides a
  real mismatch somewhere else.

## Consequences

- A published notification can be imported in full: the names that agree price
  themselves, and each name that does not costs one command, once, for ever.
- The office carries the judgement, which is where it belongs — they know what
  they stock — but a wrong equivalence does real harm, silently, at billing.
  Three things hold that down: the record carries who made it, the import prints
  what it matched through, and a correction takes one command.
- Suggestions will miss things. A schedule name that shares no word with the
  item master's, a salt whose strength is stated on a different basis, or an
  item recorded under a brand name will offer nothing, and the person searches
  the catalogue as before.
- The suggestions read the whole item master for each unmatched row, which is
  fine for a notification of a few hundred rows against a few thousand items,
  and would want an index if either grew by an order of magnitude.
- Equivalences are not used for CDSCO alert lists: those are matched by batch
  number, manufacturer and expiry, where a product name carries much less weight
  (ADR 0004, ADR 0014).
- There is no bulk import of equivalences, and no shipped dictionary of
  synonyms. Both would be useful; neither is built.

## Alternatives rejected

- **Matching by resemblance at import time.** The obvious feature, and the
  dangerous one: a ceiling placed on the wrong formulation is enforced at
  billing, and nobody would know which of two similar names the notification
  meant. Resemblance is offered as a suggestion and never acts.
- **A dictionary of INN synonyms shipped with Batchward.** It would have to be
  kept up to date against the schedule, it would still miss the office's own
  spellings, and being wrong in a shipped file is worse than being wrong in a
  record with a name and a date on it.
- **Recording the equivalence against an item id.** Item ids are the billing
  system's, and change when a master is rebuilt; the price guard keys ceilings
  on molecule, strength and pack, so the record does too.
- **Editing the item master so the names agree.** Marg is read only (ADR 0006),
  and renaming an item breaks every bill already raised under the old name.
- **Leaving the strength arithmetic to the person.** Adding "500 mg + 125 mg" to
  625 mg is the one part of this that a computer should do; it is shown as the
  reason, not applied on its own.
