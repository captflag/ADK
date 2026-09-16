import sqlite3
from contextlib import closing

import pytest


@pytest.fixture
def connection():
    """An empty in-memory database standing in for a Marg installation."""
    with closing(sqlite3.connect(":memory:")) as conn:
        yield conn
