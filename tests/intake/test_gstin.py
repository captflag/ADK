import pytest
from hypothesis import given
from hypothesis import strategies as st

from batchward.intake.gstin import check_character, gstin_problem, make_gstin

CHARACTERS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@pytest.mark.parametrize("gstin", ["27AAPFU0939F1ZV", "29AAGCB7383J1Z4", " 27aapfu0939f1zv "])
def test_real_gstins_pass(gstin):
    assert gstin_problem(gstin) is None


@pytest.mark.parametrize(
    ("gstin", "problem"),
    [
        ("27AAPFU0939F1ZX", "fails its check character"),
        ("27AAPFU0O39F1ZV", "fifteen-character layout"),
        ("27AAPFU0939F1Z", "fifteen-character layout"),
        ("45AAPFU0939F1ZV", "not a state code"),
    ],
)
def test_misread_or_malformed_gstins_are_explained(gstin, problem):
    assert problem in gstin_problem(gstin)


pans = st.from_regex(r"[A-Z]{5}[0-9]{4}[A-Z]", fullmatch=True)


@given(
    state=st.integers(1, 38),
    pan=pans,
    position=st.integers(0, 14),
    replacement=st.sampled_from(CHARACTERS),
)
def test_any_one_misread_character_is_caught(state, pan, position, replacement):
    gstin = make_gstin(state, pan)
    assert gstin_problem(gstin) is None
    misread = gstin[:position] + replacement + gstin[position + 1 :]
    if misread != gstin:
        assert gstin_problem(misread) is not None


def test_the_check_character_needs_fourteen_digits_and_capitals():
    with pytest.raises(ValueError, match="first fourteen"):
        check_character("27aapfu0939f1z")
