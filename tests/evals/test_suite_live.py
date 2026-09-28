"""The whole eval suite against the real Gemini API.

Marked live and skipped without GOOGLE_API_KEY, because every case is a real
call: it costs money, needs a network, and takes a minute or two. A failure here
is a finding about the team or its instructions, to be read in the report and
fixed, not retried until it passes.

Run with: uv run --env-file .env pytest -m live
"""

from __future__ import annotations

import os

import pytest

from batchward.evals.cases import chosen
from batchward.evals.run import run_cases

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("GOOGLE_API_KEY"), reason="needs GOOGLE_API_KEY"),
]


def test_every_case_the_data_can_be_asked_passes(stock):
    asked, left_out = chosen()
    assert asked, f"no case could be asked: {[why for _, why in left_out]}"
    report = run_cases(asked)
    assert not report.failed, "\n" + report.text()
