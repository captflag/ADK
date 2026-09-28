import re
from datetime import date

import pytest

from batchward.compliance.cdsco import AlertListError, alert_table, read_alert_list
from batchward.compliance.recall import RecallClass, match_notice
from batchward.core.printed import expiry_month
from batchward.documents.tables import TableError
from pdf_documents import Printed, lay_out, write_pdf
from recall_factories import ITEMS, NOTICE, PARTIES, RECALLED

HEADER = (
    "S. No.,Name of Drugs/medical device/cosmetic,Batch No.,Date of Manufacture,"
    "Date of Expiry,Manufactured By,NSQ Result,Reporting Portal"
)
AZ4021 = (
    "7,Azithromycin Tablets IP 500 mg,AZ4021,Nov-2025,Oct-2027,"
    '"M/s. Nilgiri Biotech Ltd., Baddi",Dissolution,CDSCO'
)


def read(*rows):
    return read_alert_list([HEADER, *rows])


def test_reads_each_row_of_the_list():
    (alert,) = read(AZ4021)
    assert (alert.line, alert.serial, alert.batch_no) == (2, "7", "AZ4021")
    assert alert.product == "Azithromycin Tablets IP 500 mg"
    assert alert.manufacturer == "M/s. Nilgiri Biotech Ltd., Baddi"
    assert (alert.expiry, alert.expiry_as_listed) == (date(2027, 10, 31), "Oct-2027")
    assert alert.result == "Dissolution"


def test_a_row_becomes_a_notice_that_matches_the_batch_it_names_exactly():
    (alert,) = read(AZ4021)
    notice = alert.notice(
        list_reference="CDSCO drug alert, August 2026",
        received_at=NOTICE.received_at,
        recall_class=RecallClass.II,
    )
    assert notice.reference == "CDSCO drug alert, August 2026 #7"
    assert notice.source == "CDSCO drug alert: Dissolution"
    assert notice.recall_class is RecallClass.II
    assert match_notice(notice, [RECALLED], items=ITEMS, parties=PARTIES).exact == (RECALLED,)


def test_a_row_whose_expiry_cannot_be_read_can_only_raise_a_batch_for_review():
    (alert,) = read(AZ4021.replace("Oct-2027", "N/A"))
    assert alert.expiry is None
    notice = alert.notice(
        list_reference="L", received_at=NOTICE.received_at, recall_class=RecallClass.I
    )
    result = match_notice(notice, [RECALLED], items=ITEMS, parties=PARTIES)
    assert result.exact == ()
    assert result.review[0].reasons == ("the notice does not give the expiry",)


def test_a_list_without_serial_numbers_numbers_rows_by_line():
    header = HEADER.removeprefix("S. No.,")
    (alert,) = read_alert_list([header, AZ4021.removeprefix("7,")])
    assert alert.serial == "2"


@pytest.mark.parametrize(
    ("listed", "expiry"),
    [
        ("10/2027", date(2027, 10, 31)),
        ("2/28", date(2028, 2, 29)),
        ("Oct-2027", date(2027, 10, 31)),
        ("October 2027", date(2027, 10, 31)),
        ("Sept. 27", date(2027, 9, 30)),
        ("2027-10", date(2027, 10, 31)),
        ("15/10/2027", date(2027, 10, 31)),
        ("N/A", None),
        ("13/2027", None),
        ("", None),
    ],
)
def test_reads_an_expiry_month_however_the_list_writes_it(listed, expiry):
    assert expiry_month(listed) == expiry


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([], "empty"),
        (["S. No.,Batch No."], "no column 'Name of Drugs'"),
        ([HEADER, AZ4021.replace("AZ4021", " ")], "line 2 has no 'Batch No.'"),
        ([HEADER, AZ4021, AZ4021.replace("AZ4021", "AZ4022")], "more than one row 7"),
    ],
)
def test_a_list_that_cannot_be_read_is_refused_naming_the_line(lines, message):
    with pytest.raises(AlertListError, match=message):
        read_alert_list(lines)


PRINTED_COLUMNS = [40.0, 75.0, 250.0, 320.0, 390.0, 460.0]
PRINTED_HEADING = [
    ["S. No.", "Name of Drugs", "Batch No.", "Date of", "Date of", "Manufactured By"],
    ["", "", "", "Manufacture", "Expiry", ""],
]
PRINTED_ROWS = [
    [
        ["1."],
        ["Azithromycin Tablets", "IP 500 mg"],
        ["AZ4021"],
        ["Nov-2025"],
        ["Oct-2027"],
        ["M/s. Nilgiri Biotech", "Ltd., Baddi"],
    ],
    [
        ["2."],
        ["Paracetamol Tablets", "IP 650 mg"],
        ["PCM9981"],
        ["01/2026"],
        ["12/2028"],
        ["M/s. X Pharma"],
    ],
]


class TestAnAlertListAsPublished:
    """A monthly list arrives as a PDF, whose table is read from the text it prints."""

    def printed(self):
        pieces = lay_out(PRINTED_COLUMNS, PRINTED_HEADING, PRINTED_ROWS)
        heading = Printed(150.0, 40.0, "Drugs declared as Not of Standard Quality", 11.0)
        return alert_table(write_pdf([[heading, *pieces]]))

    def test_the_table_it_prints_is_read_as_the_rows_it_shows(self):
        alerts = read_alert_list(self.printed().lines())
        assert [alert.batch_no for alert in alerts] == ["AZ4021", "PCM9981"]
        first = alerts[0]
        assert first.product == "Azithromycin Tablets IP 500 mg"
        assert first.manufacturer == "M/s. Nilgiri Biotech Ltd., Baddi"
        assert (first.expiry, first.expiry_as_listed) == (date(2027, 10, 31), "Oct-2027")

    def test_a_row_read_from_the_pdf_becomes_a_notice_like_any_other(self):
        (alert, _) = read_alert_list(self.printed().lines())
        notice = alert.notice(
            list_reference="CDSCO drug alert, August 2026",
            received_at=NOTICE.received_at,
            recall_class=RecallClass.II,
        )
        assert match_notice(notice, [RECALLED], items=ITEMS, parties=PARTIES).exact == (RECALLED,)

    def test_a_document_that_prints_no_such_table_is_refused(self):
        with pytest.raises(TableError, match=re.escape("Batch No.")):
            alert_table(write_pdf([[Printed(40.0, 40.0, "No table here")]]))
