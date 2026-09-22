"""Time in a distributor's world.

Indian pharma distribution runs on Indian Standard Time, and billing systems
store local dates and times with no zone attached. Every timestamp in the
ledger is timezone-aware; these helpers convert between that and the local
calendar day a person would name.
"""

from datetime import date, datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def ist_datetime(day: date, clock: time) -> datetime:
    """A moment on a local calendar day in India."""
    return datetime.combine(day, clock, tzinfo=IST)


def ist_date(moment: datetime) -> date:
    """The local calendar day in India on which a moment falls."""
    return moment.astimezone(IST).date()


def end_of_day(day: date) -> datetime:
    """The last instant of a local calendar day, so everything ``ist_date`` puts on it counts."""
    return datetime.combine(day, time.max, tzinfo=IST)
