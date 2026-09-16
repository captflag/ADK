# ADR 0006: Marg ERP is read over ODBC and written only through its import path

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Marg ERP is the billing system on most pharma distributors' desks and has no
public developer API. Its data is reachable over ODBC (SQL Server or Access
editions) and through its Excel, DBF and text import and export. Its table
layout is documented only informally, and upgrades can change it.

## Decision

Marg remains the system of record. A small bridge service on the billing PC
reads Marg over ODBC, read-only, and pushes changes out over HTTPS without
opening an inbound port. Batchward writes to Marg only by producing files for
Marg's own import, and only for entries a person has approved. Every sync runs
contract tests against the expected table shapes; a failure stops the sync and
raises an alert rather than importing data of unknown shape.

## Consequences

- The customer never has to trust Batchward with their billing system's
  integrity.
- Write-back is slower and coarser than a direct API would be.
- Schema drift after a Marg upgrade becomes a visible, fixable alert instead of
  silent corruption.
