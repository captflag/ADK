"""Property-based checks of the ledger against a deliberately naive reference model."""

from contextlib import suppress
from datetime import timedelta

from hypothesis import given
from hypothesis import strategies as st

from batchward.core.ledger import InsufficientStockError, Ledger
from batchward.core.models import MovementType
from factories import at, batch_key, movement

START = at(1, 0)

# (minutes after START, signed quantity). Offsets repeat on purpose, so
# same-instant movements are exercised too.
movements_strategy = st.lists(
    st.tuples(st.integers(0, 3_000), st.integers(-50, 50).filter(bool)),
    max_size=60,
)


def build(offset, qty, index):
    kind = MovementType.PURCHASE if qty > 0 else MovementType.SALE
    return movement(kind, qty, when=START + timedelta(minutes=offset), movement_id=f"P{index}")


def would_go_negative(accepted, candidate):
    """Reference model: replay everything in time order, ties in append order."""
    running = 0
    for m in sorted([*accepted, candidate], key=lambda m: m.at):
        running += m.qty
        if running < 0:
            return True
    return False


@given(movements_strategy)
def test_refuses_exactly_the_movements_the_reference_model_refuses(steps):
    ledger, accepted = Ledger(), []
    for index, (offset, qty) in enumerate(steps):
        candidate = build(offset, qty, index)
        expected_refusal = would_go_negative(accepted, candidate)
        try:
            ledger.append(candidate)
        except InsufficientStockError:
            assert expected_refusal, f"refused {candidate} but the reference model accepts it"
        else:
            assert not expected_refusal, f"accepted {candidate} but it goes negative"
            accepted.append(candidate)


@given(movements_strategy)
def test_balances_agree_with_summing_accepted_movements(steps):
    ledger, accepted = Ledger(), []
    for index, (offset, qty) in enumerate(steps):
        candidate = build(offset, qty, index)
        try:
            ledger.append(candidate)
            accepted.append(candidate)
        except InsufficientStockError:
            pass

    key = batch_key()
    assert ledger.balance(key, "GODOWN") == sum(m.qty for m in accepted)
    for m in accepted:
        assert ledger.balance(key, "GODOWN", as_of=m.at) == sum(
            n.qty for n in accepted if n.at <= m.at
        )
        assert ledger.balance(key, "GODOWN", as_of=m.at) >= 0


@given(movements_strategy)
def test_an_accepted_history_can_always_be_rebuilt(steps):
    ledger = Ledger()
    for index, (offset, qty) in enumerate(steps):
        with suppress(InsufficientStockError):
            ledger.append(build(offset, qty, index))

    rebuilt = Ledger(list(ledger))
    assert rebuilt.balances() == ledger.balances()
