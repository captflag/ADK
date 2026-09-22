# ADR 0012: The Registrar checks Rule 65 wholesale records, not retail registers

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

When a Drugs Inspector visits a stockist, the records examined are the ones
Rule 65 of the Drugs Rules, 1945 requires of a wholesale licensee. Rule 65(5)
requires:

- a cash or credit memo for every sale, showing the date; the name, address
  and sale licence number of the licensee sold to; the name, quantity and batch
  number of the drug; the manufacturer; and the signature of the competent
  person who supervised the sale;
- a record of every purchase with the supplier's name, address and licence
  number, and the drug's name, quantity, batch number and manufacturer;
- purchase bills and memos serially numbered and kept in date order;
- memo copies kept for three years from the date of sale.

The original blueprint also planned Schedule H1 and X registers of prescriber
and patient for the stockist. Reading Rule 65 again, the H1 register belongs to
retail supply on prescription (Rule 65(3)), not to a wholesaler's sales to
other licensees.

Two particulars had no place in the data: party addresses, and a supplier on
purchases.

## Decision

- The Registrar checks the Rule 65(5) particulars from the ledger and the party
  and item records, and reports each gap once per bill, however many of its
  lines show it: a missing buyer or supplier, a party not on record, a missing
  licence number or address, a bill naming more than one party, a manufacturer
  not on record, and a bill numbered out of date order within its series.
  Cancelled bills are skipped.
- A bill is its number within the financial year (1 April to 31 March) it was
  made in, because numbering commonly starts again each year.
- It reports the date from which memos must still be kept.
- It says plainly what data cannot show: the competent person's signature.
- Schedule H1 and X patient registers are out of scope for a stockist.
- Parties gain an address, the assumed Marg party table gains an `ADDRESS`
  column (ADR 0006's layout remains an assumption until a real installation is
  read), and simulated purchases name the company they came from.

## Consequences

- An inspection-readiness check runs over a year of simulated bills, about
  240,000 memos, in about a second and a half, and finds the gaps an inspector
  would.
- A Marg database written before the `ADDRESS` column existed fails the layout
  check and must be regenerated; for real installations the column mapping is
  still to be confirmed.
- Numbering is checked within a series and a financial year. The series is the
  bill number with its serial set aside, the serial being the number the bill
  ends with, or the number before a closing year (`S/0042/2026`) or financial
  year (`S/0042/25-26`). Series whose number carries the date, as the
  simulator's do, are only checked within a day, and numbering that runs on
  past 1 April is not checked across it.
- The bills reported out of order are the fewest whose removal leaves the rest
  in order, so one mistyped number is reported itself rather than every correct
  bill after it. Where the evidence is even, the later bill is reported.
- Batchward keeps no retention clock of its own yet; it reports the date and
  leaves disposal to people.
