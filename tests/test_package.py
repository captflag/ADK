import batchward


def test_package_exposes_version():
    assert batchward.__version__ == "0.1.0"
