"""A deterministic catalogue for a simulated pharma distributor.

Company and brand names are generated and fictional; any resemblance to a real
business or brand is coincidental. Molecule attributes — prices, schedules,
price control, demand — are illustrative simulation inputs, not a regulatory or
commercial reference.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from batchward.core.models import Item, Party, PartyKind, Schedule
from batchward.intake.gstin import make_gstin


class Therapy(StrEnum):
    ACUTE = "acute"
    ANTI_INFECTIVE = "anti_infective"
    CHRONIC = "chronic"
    CONTROLLED = "controlled"
    GASTRO = "gastro"
    RESPIRATORY = "respiratory"
    SUPPLEMENT = "supplement"
    VACCINE = "vaccine"


@dataclass(frozen=True, slots=True)
class Molecule:
    name: str
    strength: str
    unit: str
    therapy: Therapy
    mrp: Decimal
    """Typical MRP per unit sold."""
    daily_demand: float
    """Mean units a single brand sells per day at a mid-sized stockist."""
    shelf_life_months: int
    schedules: frozenset[Schedule] = frozenset()
    dpco_scheduled: bool = False
    cold_chain: bool = False


_H = frozenset({Schedule.H})
_H1 = frozenset({Schedule.H1})

MOLECULES: tuple[Molecule, ...] = (
    Molecule("Paracetamol", "500 mg", "strip of 15 tablets", Therapy.ACUTE, Decimal(30), 25, 36, dpco_scheduled=True),
    Molecule("Azithromycin", "500 mg", "strip of 3 tablets", Therapy.ANTI_INFECTIVE, Decimal(72), 6, 24, _H),
    Molecule("Amoxicillin + Clavulanic acid", "625 mg", "strip of 6 tablets", Therapy.ANTI_INFECTIVE, Decimal(200), 5, 24, _H, dpco_scheduled=True),
    Molecule("Cefixime", "200 mg", "strip of 10 tablets", Therapy.ANTI_INFECTIVE, Decimal(110), 4, 24, _H1),
    Molecule("Ceftriaxone", "1 g", "vial", Therapy.ANTI_INFECTIVE, Decimal(60), 3, 24, _H1, dpco_scheduled=True),
    Molecule("Levofloxacin", "500 mg", "strip of 10 tablets", Therapy.ANTI_INFECTIVE, Decimal(95), 3, 36, _H1),
    Molecule("Doxycycline", "100 mg", "strip of 10 capsules", Therapy.ANTI_INFECTIVE, Decimal(45), 2, 24, _H),
    Molecule("Fluconazole", "150 mg", "strip of 1 tablet", Therapy.ANTI_INFECTIVE, Decimal(20), 2, 36, _H),
    Molecule("Metformin", "500 mg", "strip of 20 tablets", Therapy.CHRONIC, Decimal(35), 12, 36, _H, dpco_scheduled=True),
    Molecule("Glimepiride", "2 mg", "strip of 10 tablets", Therapy.CHRONIC, Decimal(90), 6, 36, _H),
    Molecule("Atorvastatin", "10 mg", "strip of 10 tablets", Therapy.CHRONIC, Decimal(60), 8, 24, _H, dpco_scheduled=True),
    Molecule("Amlodipine", "5 mg", "strip of 15 tablets", Therapy.CHRONIC, Decimal(35), 10, 36, _H, dpco_scheduled=True),
    Molecule("Telmisartan", "40 mg", "strip of 15 tablets", Therapy.CHRONIC, Decimal(110), 7, 36, _H),
    Molecule("Levothyroxine", "50 mcg", "bottle of 100 tablets", Therapy.CHRONIC, Decimal(150), 2, 24, _H, dpco_scheduled=True),
    Molecule("Clopidogrel", "75 mg", "strip of 15 tablets", Therapy.CHRONIC, Decimal(120), 4, 24, _H, dpco_scheduled=True),
    Molecule("Aspirin", "75 mg", "strip of 14 tablets", Therapy.CHRONIC, Decimal(5), 6, 24, dpco_scheduled=True),
    Molecule("Insulin glargine", "100 IU/ml", "prefilled pen", Therapy.CHRONIC, Decimal(800), 0.6, 24, _H, dpco_scheduled=True, cold_chain=True),
    Molecule("Human insulin", "40 IU/ml", "10 ml vial", Therapy.CHRONIC, Decimal(160), 1, 24, _H, dpco_scheduled=True, cold_chain=True),
    Molecule("Pantoprazole", "40 mg", "strip of 15 tablets", Therapy.GASTRO, Decimal(150), 9, 24, _H, dpco_scheduled=True),
    Molecule("Ondansetron", "4 mg", "strip of 10 tablets", Therapy.GASTRO, Decimal(60), 3, 36, _H),
    Molecule("Domperidone", "10 mg", "strip of 10 tablets", Therapy.GASTRO, Decimal(35), 3, 36, _H),
    Molecule("Oral rehydration salts", "21.8 g", "sachet", Therapy.ACUTE, Decimal(21), 8, 24),
    Molecule("Diclofenac", "50 mg", "strip of 10 tablets", Therapy.ACUTE, Decimal(20), 5, 36, _H),
    Molecule("Cetirizine", "10 mg", "strip of 10 tablets", Therapy.RESPIRATORY, Decimal(20), 6, 36),
    Molecule("Montelukast + Levocetirizine", "10 mg + 5 mg", "strip of 10 tablets", Therapy.RESPIRATORY, Decimal(180), 4, 24, _H),
    Molecule("Salbutamol", "100 mcg", "inhaler of 200 doses", Therapy.RESPIRATORY, Decimal(150), 1.5, 24, _H, dpco_scheduled=True),
    Molecule("Rabies vaccine", "2.5 IU", "vial", Therapy.VACCINE, Decimal(350), 0.3, 24, _H, cold_chain=True),
    Molecule("Alprazolam", "0.25 mg", "strip of 15 tablets", Therapy.CONTROLLED, Decimal(40), 1, 36, _H1),
    Molecule("Tramadol", "50 mg", "strip of 10 capsules", Therapy.CONTROLLED, Decimal(60), 0.8, 36, _H1),
    Molecule("Multivitamin", "—", "strip of 15 capsules", Therapy.SUPPLEMENT, Decimal(120), 3, 24),
    Molecule("Calcium + Vitamin D3", "500 mg + 250 IU", "strip of 15 tablets", Therapy.SUPPLEMENT, Decimal(110), 4, 24),
    Molecule("Ferrous ascorbate + Folic acid", "100 mg + 1.5 mg", "strip of 10 tablets", Therapy.SUPPLEMENT, Decimal(120), 3, 24),
)  # fmt: skip

_COMPANY_WORDS = (
    "Nilgiri", "Vaigai", "Satpura", "Godavari", "Kesar", "Aravalli", "Narmada", "Konkan",
    "Chambal", "Sahyadri", "Mahanadi", "Tapti", "Shivalik", "Kaveri", "Vindhya", "Palar",
    "Sabarmati", "Pench", "Wainganga", "Betwa", "Tungabhadra", "Periyar", "Barak", "Luni",
    "Teesta", "Sone", "Indravati", "Malaprabha", "Bhima", "Koyna", "Girna", "Purna",
    "Damodar", "Mandovi", "Zuari",
)  # fmt: skip
_TOWNS = (
    "Baddi, Himachal Pradesh", "Pithampur, Madhya Pradesh", "Sanand, Gujarat",
    "Jeedimetla, Telangana", "Verna, Goa",
)  # fmt: skip
"""Pharma manufacturing towns; the companies, plots and licence numbers are fictional."""
_STATE_CODES = {
    "Himachal Pradesh": 2,
    "Madhya Pradesh": 23,
    "Gujarat": 24,
    "Goa": 30,
    "Telangana": 36,
}
"""GST state codes of the towns above. The GSTINs built from them are fictional but well formed."""
_COMPANY_SUFFIXES = (
    "Pharma", "Labs", "Lifesciences", "Remedies", "Healthcare", "Biotech", "Formulations",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Catalogue:
    companies: tuple[Party, ...]
    items: tuple[Item, ...]
    molecules: dict[str, Molecule]
    """The molecule behind each item, by item id."""


def build_catalogue(
    seed: int = 7,
    *,
    n_companies: int = 35,
    brands_per_company: tuple[int, int] = (8, 20),
) -> Catalogue:
    """Generate companies and their brands. The same seed always gives the same catalogue."""
    low, high = brands_per_company
    if not 1 <= low <= high <= len(MOLECULES):
        raise ValueError(f"brands_per_company must lie within 1..{len(MOLECULES)}")
    if not 1 <= n_companies <= len(_COMPANY_WORDS):
        raise ValueError(f"n_companies must lie within 1..{len(_COMPANY_WORDS)}")

    rng = random.Random(seed)
    companies: list[Party] = []
    items: list[Item] = []
    molecules: dict[str, Molecule] = {}

    for index, word in enumerate(_COMPANY_WORDS[:n_companies], start=1):
        company = Party(
            id=f"C{index:02d}",
            kind=PartyKind.COMPANY,
            name=f"{word} {rng.choice(_COMPANY_SUFFIXES)}",
            drug_licence_no=f"SIM/MFG/{index:03d}",
            address=f"Plot {index}, Industrial Area, {_TOWNS[index % len(_TOWNS)]}",
            gstin=make_gstin(
                _STATE_CODES[_TOWNS[index % len(_TOWNS)].split(", ")[1]],
                f"{word[:3].upper()}C{word[0].upper()}{index:04d}{chr(ord('A') + index % 26)}",
            ),
        )
        companies.append(company)
        for number, molecule in enumerate(rng.sample(MOLECULES, rng.randint(low, high)), start=1):
            item = Item(
                id=f"{company.id}-{number:03d}",
                company_id=company.id,
                brand=_brand_name(molecule, word),
                molecule=molecule.name,
                strength=molecule.strength,
                unit=molecule.unit,
                hsn="3002" if molecule.therapy is Therapy.VACCINE else "3004",
                gst_rate=Decimal("0.05"),
                mrp=(molecule.mrp * Decimal(str(rng.uniform(0.8, 1.25)))).quantize(Decimal("0.01")),
                schedules=molecule.schedules,
                dpco_scheduled=molecule.dpco_scheduled,
                cold_chain=molecule.cold_chain,
            )
            items.append(item)
            molecules[item.id] = molecule

    return Catalogue(companies=tuple(companies), items=tuple(items), molecules=molecules)


def _brand_name(molecule: Molecule, company_word: str) -> str:
    """A pharma-style brand: molecule stem + company syllable + strength, e.g. "Azinil 500"."""
    stem = "".join(ch for ch in molecule.name if ch.isalpha())[:3].title()
    syllable = company_word[:3].lower()
    number = molecule.strength.split()[0]
    return f"{stem}{syllable} {number}" if number[0].isdigit() else f"{stem}{syllable}"
