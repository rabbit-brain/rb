import os

import pytest

# rb checks PyPI for a newer rb at most once a day (AGENTS.md, Updates): never from the tests, whatever a test's own
# environment clears to look like the user's; the tests of it turn it back on
os.environ["RB_AUTO_UPDATE"] = "0"

# Which rb the tests drive (tests/rbtest.py): RB_BIN=<path to an rb binary> names it, for every command, every SDK call
# and the screen's reads; without it, the rb installed beside this Python.


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(scope="session")
def int8_demo(tmp_path_factory):
    """The INT8 demo record (rbtest.int8_demo), made once for the test process: read it, never write to it."""
    from rbtest import int8_demo
    return int8_demo(tmp_path_factory.mktemp("int8-demo"))


@pytest.fixture(scope="session")
def scale_record(tmp_path_factory):
    """The scale record (rbtest.scale_record), made once for the test process: read it, never write to it."""
    from rbtest import scale_record
    return scale_record(tmp_path_factory.mktemp("scale"))
