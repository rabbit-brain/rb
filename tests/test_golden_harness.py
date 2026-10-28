"""The golden harness plays every scenario under one fixed path in /tmp, where any user can make that path first. It
plays only in a directory of its own: a link there, or a directory of another user's, is refused in one line before
anything is emptied or written, and the lock files it takes go when their scenarios end."""
import importlib.util
import os
import sys
import threading
import time

import pytest

from rbtest import ROOT

HARNESS = ROOT / "rust" / "tests_py" / "golden.py"
pytestmark = [pytest.mark.skipif(not HARNESS.is_file(), reason="the golden harness stays in the repository"),
              pytest.mark.skipif(sys.platform == "win32", reason="the goldens are held on Linux and macOS")]


@pytest.fixture
def golden(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("rb_golden_harness", HARNESS)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "path", list(sys.path))          # the harness puts its own folder first
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "RUN_ROOT", str(tmp_path / "rb-golden"))
    return module


def play_main(golden, monkeypatch, capsys) -> tuple:
    """The harness's command line against a stand-in binary, which a refused run never starts."""
    monkeypatch.setattr(sys, "argv", ["golden.py", "--stored", sys.executable, "basics"])
    code = golden.main()
    return code, capsys.readouterr().out


def test_a_link_at_the_fixed_path_is_refused_in_one_line_and_where_it_points_is_left_alone(golden, tmp_path, monkeypatch, capsys):
    elsewhere = tmp_path / "someone-elses"
    (elsewhere / "basics").mkdir(parents=True)
    (elsewhere / "basics" / "keep.txt").write_text("mine\n", encoding="utf-8")
    os.symlink(elsewhere, golden.RUN_ROOT)
    code, out = play_main(golden, monkeypatch, capsys)
    assert code == 2 and out == f"{golden.RUN_ROOT} is a symbolic link, not a directory of your own: remove the link and run again\n"
    with pytest.raises(SystemExit, match="symbolic link"):
        with golden.scenario_dir("basics"):
            pass
    assert (elsewhere / "basics" / "keep.txt").read_text(encoding="utf-8") == "mine\n"
    assert sorted(p.name for p in elsewhere.iterdir()) == ["basics"]


def test_a_directory_of_another_user_at_the_fixed_path_is_refused(golden, monkeypatch, capsys):
    os.mkdir(golden.RUN_ROOT, 0o755)
    me = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: me + 1)
    code, out = play_main(golden, monkeypatch, capsys)
    assert code == 2 and out == f"{golden.RUN_ROOT} belongs to another user (uid {me}), not to you: remove it or run as its owner\n"
    with pytest.raises(SystemExit, match="another user"):
        with golden.scenario_dir("basics"):
            pass
    assert os.listdir(golden.RUN_ROOT) == []


def test_a_directory_every_user_can_write_to_is_refused(golden, monkeypatch, capsys):
    os.mkdir(golden.RUN_ROOT)
    os.chmod(golden.RUN_ROOT, 0o777)
    code, out = play_main(golden, monkeypatch, capsys)
    assert code == 2 and out.endswith("can be written by every user: remove it and run again\n") and out.count("\n") == 1


def test_a_directory_its_group_can_write_to_is_refused(golden, monkeypatch, capsys):
    """A member of the group could plant a link at a scenario's lock file, which the harness opens for writing."""
    os.mkdir(golden.RUN_ROOT)
    os.chmod(golden.RUN_ROOT, 0o770)
    code, out = play_main(golden, monkeypatch, capsys)
    assert code == 2 and out.endswith("can be written by its group: remove it and run again\n") and out.count("\n") == 1


def test_the_harness_makes_its_directory_its_own_and_takes_its_lock_files_away(golden):
    with golden.scenario_dir("basics") as base:
        assert os.path.isdir(base) and os.path.exists(os.path.join(golden.RUN_ROOT, "basics.lock"))
    assert os.stat(golden.RUN_ROOT).st_mode & 0o777 == 0o700
    assert os.listdir(golden.RUN_ROOT) == []


def test_a_harness_waiting_on_a_scenario_plays_it_alone_after_the_lock_file_is_taken_away(golden):
    inside, order = threading.Event(), []

    def first():
        with golden.scenario_dir("basics"):
            order.append("first in")
            inside.set()
            time.sleep(0.3)
            order.append("first out")

    def second():
        inside.wait()
        with golden.scenario_dir("basics"):
            order.append("second in")
            order.append("second out")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert order == ["first in", "first out", "second in", "second out"]
    assert os.listdir(golden.RUN_ROOT) == []
