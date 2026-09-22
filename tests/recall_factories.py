"""A recalled batch, a rival manufacturer's product, and the notice that names the batch."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from batchward.compliance.recall import RecallClass, RecallNotice
from batchward.core.models import Item, Party, PartyKind
from factories import at, batch_key

AZINIL = Item(
    id="I001",
    company_id="C01",
    brand="Azinil 500",
    molecule="Azithromycin",
    strength="500 mg",
    unit="strip of 3 tablets",
    hsn="3004",
    gst_rate=Decimal("0.05"),
    mrp=Decimal(72),
)
RIVAL = replace(AZINIL, id="I002", company_id="C02", brand="Azigod 500")
ITEMS = {item.id: item for item in (AZINIL, RIVAL)}
PARTIES = {
    "C01": Party(id="C01", kind=PartyKind.COMPANY, name="Nilgiri Biotech"),
    "C02": Party(id="C02", kind=PartyKind.COMPANY, name="Godavari Biotech"),
}

RECALLED = batch_key("AZ4021", company_id="C01", item_id="I001", expiry=date(2027, 10, 31))

NOTICE = RecallNotice(
    reference="RN/2026/014",
    source="Nilgiri Biotech",
    recall_class=RecallClass.I,
    received_at=at(12, 9, month=2),
    batch_no="AZ4021",
    manufacturer="M/s. Nilgiri Biotech Ltd., Baddi",
    product="Azithromycin Tablets IP 500 mg",
    expiry=date(2027, 10, 1),
)
