from dataclasses import replace
from datetime import date

import pytest

from batchward.compliance.recall import RECALL_CLOCKS, MatchKind, RecallClass, match_notice
from factories import at
from recall_factories import ITEMS, NOTICE, PARTIES, RECALLED


def match(notice=NOTICE, batches=(RECALLED,)):
    return match_notice(notice, batches, items=ITEMS, parties=PARTIES)


def test_a_notice_giving_manufacturer_product_and_expiry_matches_exactly():
    result = match()
    assert result.exact == (RECALLED,)
    assert result.review == ()


@pytest.mark.parametrize("printed", ["az4021", " AZ 4021 ", "Az4021"])
def test_spaces_and_letter_case_in_the_batch_number_do_not_matter(printed):
    assert match(replace(NOTICE, batch_no=printed)).exact == (RECALLED,)


@pytest.mark.parametrize(
    "product",
    ["Azinil 500 tablets", "AZITHROMYCIN TABLETS I.P. 500MG", "Azithromycin 500 mg (strip of 3)"],
)
def test_the_product_can_be_named_by_brand_or_by_molecule_with_strength(product):
    assert match(replace(NOTICE, product=product)).exact == (RECALLED,)


def test_expiry_is_compared_by_month_and_year_only():
    assert match(replace(NOTICE, expiry=date(2027, 10, 31))).exact == (RECALLED,)


def test_a_look_alike_batch_number_is_raised_for_review_never_blocked():
    look_alike = replace(RECALLED, batch_no="AZ4O21")
    result = match(batches=(look_alike,))
    assert result.exact == ()
    (candidate,) = result.review
    assert (candidate.batch, candidate.kind) == (look_alike, MatchKind.LOOK_ALIKE)


@pytest.mark.parametrize("held", ["AZ-4021", "A24021", "AZ4O2I"])
def test_punctuation_and_other_misreadings_are_look_alikes_too(held):
    result = match(batches=(replace(RECALLED, batch_no=held),))
    assert result.exact == ()
    assert [c.kind for c in result.review] == [MatchKind.LOOK_ALIKE]


def test_an_unrelated_batch_number_is_ignored():
    result = match(batches=(replace(RECALLED, batch_no="AZ4022"),))
    assert (result.exact, result.review) == ((), ())


@pytest.mark.parametrize(
    ("batch", "reason"),
    [
        (replace(RECALLED, company_id="C02", item_id="I002"), "the batch is from Godavari Biotech"),
        (replace(RECALLED, expiry=date(2026, 10, 31)), "the batch expires 10/2026"),
    ],
)
def test_a_batch_whose_details_disagree_is_raised_for_review(batch, reason):
    result = match(batches=(batch,))
    assert result.exact == ()
    (candidate,) = result.review
    assert candidate.kind is MatchKind.PARTIAL
    assert any(reason in r for r in candidate.reasons)


def test_a_different_strength_of_the_same_molecule_is_not_the_product_named():
    result = match(replace(NOTICE, product="Azithromycin Tablets IP 250 mg"))
    assert result.exact == ()
    assert "but the batch is Azinil 500" in result.review[0].reasons[0]


@pytest.mark.parametrize(
    ("missing", "reason"),
    [
        ("manufacturer", "does not name the manufacturer"),
        ("product", "does not name the product"),
        ("expiry", "does not give the expiry"),
    ],
)
def test_a_notice_missing_any_identifying_detail_cannot_block_automatically(missing, reason):
    result = match(replace(NOTICE, **{missing: None}))
    assert result.exact == ()
    assert result.review[0].reasons == (f"the notice {reason}",)


def test_a_manufacturer_name_must_appear_whole_not_as_a_fragment():
    result = match(replace(NOTICE, manufacturer="Nilgiri Biotechnologies Pvt Ltd"))
    assert result.exact == ()


def test_the_same_batch_number_from_two_manufacturers_blocks_only_the_named_one():
    rival = replace(RECALLED, company_id="C02", item_id="I002")
    result = match(batches=(rival, RECALLED))
    assert result.exact == (RECALLED,)
    assert [c.batch for c in result.review] == [rival]


def test_each_recall_class_has_its_clock():
    assert RECALL_CLOCKS[RecallClass.I].stop_sale.total_seconds() == 24 * 3600
    assert RECALL_CLOCKS[RecallClass.I].complete.total_seconds() == 72 * 3600
    assert RECALL_CLOCKS[RecallClass.II].complete.days == 10
    assert RECALL_CLOCKS[RecallClass.III].complete.days == 30
    assert NOTICE.clock is RECALL_CLOCKS[RecallClass.I]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (dict(received_at=at(12, 9, month=2).replace(tzinfo=None)), "timezone-aware"),
        (dict(batch_no="  "), "must name a batch number"),
        (dict(reference=""), "needs a reference"),
        (dict(source="  "), "who issued it"),
    ],
)
def test_refuses_an_unusable_notice(change, message):
    with pytest.raises(ValueError, match=message):
        replace(NOTICE, **change)


AMLODIPINE = replace(ITEMS["I001"], brand="Amlopin", molecule="Amlodipine", strength="5 mg")


def match_amlodipine(product, item=AMLODIPINE):
    notice = replace(NOTICE, product=product)
    return match_notice(notice, (RECALLED,), items={"I001": item}, parties=PARTIES)


@pytest.mark.parametrize(
    "product",
    [
        "Amlodipine Tablets IP 2.5 mg",
        "Amlodipine Tablets 0.5 mg",
        "Amlodipine Tablets 12.5 mg",
        "S-Amlodipine Tablets 5 mg",
        "Telmisartan 40 mg and Amlodipine 5 mg Tablets",
        "Amlopin 10 mg",
    ],
)
def test_another_strength_or_a_combination_is_never_blocked_as_this_product(product):
    result = match_amlodipine(product)
    assert result.exact == ()
    assert [c.kind for c in result.review] == [MatchKind.PARTIAL]


@pytest.mark.parametrize("product", ["AMLODIPINE TABLETS I.P. 5MG", "Amlodipine 5.0 mg", "Amlopin"])
def test_the_same_strength_written_differently_still_matches(product):
    assert match_amlodipine(product).exact == (RECALLED,)


@pytest.mark.parametrize(("molecule", "strength"), [("", ""), (None, None)])
def test_an_item_with_no_molecule_or_strength_on_record_is_only_raised_for_review(
    molecule, strength
):
    item = replace(AMLODIPINE, brand="Crocin", molecule=molecule, strength=strength)
    result = match_amlodipine("Paracetamol Tablets IP 650 mg", item)
    assert result.exact == ()
    assert [c.kind for c in result.review] == [MatchKind.PARTIAL]


@pytest.mark.parametrize(
    "printed",
    [
        "".join(chr(ord(c) + 0xFEE0) for c in "AZ4021"),  # full-width, as some keyboards type it
        chr(0x410) + "Z4021",  # a Cyrillic A
    ],
)
def test_a_batch_number_typed_in_look_alike_unicode_is_raised_for_review(printed):
    result = match(replace(NOTICE, batch_no=printed))
    assert result.exact == ()
    assert [c.kind for c in result.review] == [MatchKind.LOOK_ALIKE]


def test_a_notice_keeps_only_the_month_and_year_of_its_expiry():
    assert replace(NOTICE, expiry=date(2027, 10, 1)) == replace(NOTICE, expiry=date(2027, 10, 31))
