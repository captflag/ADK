"""Return terms for the simulated companies (ADR 0018).

Real terms differ by company and are agreed with its area manager; none are
published. These are made up within ranges that are plausible but unverified:
a window opening two to six months before expiry, closing on expiry or up to
three months after, at 75% to 100% of the stock's value. Each company's terms
come from its own seeded generator, so adding them changes nothing else the
simulator produces.
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

from batchward.claims.terms import ReturnTerms
from batchward.sim.catalogue import Catalogue

_OPENS = (60, 90, 120, 180)
_CLOSES = (0, 30, 60, 90)
_CREDIT = (Decimal(100), Decimal(100), Decimal(90), Decimal(75))


def return_terms(catalogue: Catalogue, *, effective_from: date) -> list[ReturnTerms]:
    """One set of terms per company, in force from ``effective_from``."""
    terms = []
    for company in catalogue.companies:
        rng = random.Random(f"return-terms:{company.id}")
        terms.append(
            ReturnTerms(
                company_id=company.id,
                opens_days_before_expiry=rng.choice(_OPENS),
                closes_days_after_expiry=rng.choice(_CLOSES),
                credit_percent=rng.choice(_CREDIT),
                effective_from=effective_from,
                reference=f"Simulated terms for {company.name}",
            )
        )
    return terms
