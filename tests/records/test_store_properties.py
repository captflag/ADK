"""Whatever a person types into a notice or a ceiling price comes back unchanged."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from batchward.compliance.prices import CeilingPrice
from batchward.compliance.recall import RecallClass, RecallNotice
from batchward.records.store import RecordStore

text = st.text(min_size=1, max_size=40).filter(lambda s: s.strip())
maybe_text = st.none() | text
offsets = st.integers(-12 * 60, 14 * 60).map(lambda minutes: timezone(timedelta(minutes=minutes)))
moments = st.datetimes(
    min_value=datetime(2000, 1, 1), max_value=datetime(2090, 12, 31), timezones=offsets
)
notices = st.builds(
    RecallNotice,
    reference=text,
    source=text,
    recall_class=st.sampled_from(RecallClass),
    received_at=moments,
    batch_no=text,
    manufacturer=maybe_text,
    product=maybe_text,
    expiry=st.none() | st.dates(min_value=date(2000, 1, 1), max_value=date(2090, 12, 31)),
)
ceilings = st.builds(
    CeilingPrice,
    molecule=text,
    strength=text,
    unit=text,
    ceiling=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("100000"), places=4),
    effective_from=st.dates(min_value=date(2000, 1, 1), max_value=date(2090, 12, 31)),
    reference=text,
)

SETTINGS = settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


@SETTINGS
@given(notice=notices)
def test_a_notice_reads_back_equal_and_at_the_same_instant(tmp_path, notice):
    path = tmp_path / f"n{abs(hash(notice.reference))}.sqlite"
    path.unlink(missing_ok=True)
    with RecordStore(path) as store:
        assert store.save_notice(notice) is True
    with RecordStore(path) as store:
        back = store.notice(notice.reference)
        assert back == notice
        assert back.received_at.astimezone(UTC) == notice.received_at.astimezone(UTC)
        assert back.received_at.utcoffset() == notice.received_at.utcoffset()
        assert store.save_notice(notice) is False


@SETTINGS
@given(price=ceilings)
def test_a_ceiling_price_reads_back_equal(tmp_path, price):
    path = tmp_path / f"c{abs(hash(price))}.sqlite"
    path.unlink(missing_ok=True)
    with RecordStore(path) as store:
        assert store.save_ceiling(price) is True
    with RecordStore(path) as store:
        (back,) = store.ceiling_prices()
        assert back == price
        assert store.save_ceiling(price) is False
