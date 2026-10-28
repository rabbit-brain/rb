"""Environment receipts and rb env check: every run fingerprints the machine, the check
compares without blocking, and declared env.* settings ride the ordinary settings machinery."""
import json
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main


@pytest.fixture
def inv(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "t"], check=True)

    def rb(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    assert rb("init", "env receipts")[0] == 0
    assert rb("experiment", "add", "e", "--id", "e1", "--baseline", "a", "--candidate", "b")[0] == 0
    assert rb("criterion", "add", "c", "-e", "e1", "--metric", "m", "--at-least", "0", "--id", "c1")[0] == 0
    return rb


def test_the_fingerprint_names_what_is_installed(inv):
    code, env = inv("env", "check")
    here = env.data["here"]
    assert code == 0 and set(here) >= {"python", "platform", "rb"}
    assert here["pydantic"]                   # installed beside the Python doing the work, read from its metadata


def test_every_run_carries_the_environment(inv):
    code, env = inv("run", "add", "e1", "a.m=1", "b.m=2")
    assert code == 0
    recorded = env.data["object"]["receipt"]["environment"]
    assert recorded["python"] and recorded["rb"] and recorded["pydantic"]


def test_a_pinned_machine_is_what_the_receipt_and_the_check_say(inv, monkeypatch):
    """RB_TEST_ENV names the machine for the tests, as RB_TEST_NOW names the time: a receipt carries it in rb's own
    order, with no interpreter asked, and rb env check compares against it."""
    monkeypatch.setenv("RB_TEST_ENV", json.dumps({"torch": "2.4.1", "platform": "test-box", "python": "3.12.1"}))
    code, env = inv("run", "add", "e1", "a.m=1", "b.m=2")
    recorded = env.data["object"]["receipt"]["environment"]
    assert code == 0 and list(recorded) == ["python", "platform", "rb", "torch"]
    assert (recorded["python"], recorded["platform"], recorded["torch"]) == ("3.12.1", "test-box", "2.4.1")
    monkeypatch.setenv("RB_TEST_ENV", json.dumps({"python": "3.12.1", "platform": "other-box", "torch": "2.5.0", "cuda": "12.1"}))
    code, env = inv("env", "check")
    assert code == 0 and env.data["differs"] == 2 and env.data["here"]["cuda"] == "12.1"
    assert set(env.data["seen"]["torch"]) == {"2.4.1"} and set(env.data["seen"]["platform"]) == {"test-box"}


def test_a_run_made_before_it_was_recorded_is_left_out_of_what_the_check_compares(inv, capsys, monkeypatch):
    """A run made before it was recorded keeps the recorder's environment on its receipt, not the one it ran in: rb env
    check leaves it out and says so, and its receipt card says where its environment was taken."""
    monkeypatch.setenv("RB_TEST_ENV", json.dumps({"python": "3.12.1", "platform": "recorder"}))
    code, env = inv("run", "add", "e1", "a.m=1", "b.m=2", "--produced-at", "2026-05-01T10:00:00Z", "--host", "gpu=H100")
    run = env.data["object"]["id"]
    monkeypatch.setenv("RB_TEST_ENV", json.dumps({"python": "3.13.5", "platform": "another"}))
    code, env = inv("env", "check")
    assert code == 0 and env.data["seen"] == {} and env.data["made_before"] == [run] and env.data["differs"] == 0
    assert main(["env", "check"]) == 0
    said = capsys.readouterr().out
    assert f"Left out: 1 run made before it was recorded ({run}): its receipt keeps the environment that recorded it" in said
    assert "Nothing differs" not in said
    from rbtest import object_card
    assert any(f.startswith("environment: python 3.12.1 · platform recorder") and f.endswith(
        " (where it was recorded: the run was made before it was recorded)") for f in object_card(run).facts)


def test_env_check_compares_and_never_blocks(inv, capsys):
    assert inv("run", "add", "e1", "a.m=1", "b.m=2")[0] == 0
    code, env = inv("env", "check")
    assert code == 0
    assert env.data["differs"] == 0           # recorded on this same machine
    assert env.data["here"]["python"]
    # a declared expectation that disagrees is a difference, said, still exit 0
    assert inv("spec", "set", "e1", "env.python", "0.0.0", "--no-verify")[0] == 0
    code, env = inv("env", "check")
    assert code == 0
    assert env.data["differs"] >= 1
    assert any(d["name"] == "env.python" for d in env.data["declared"])


def test_env_check_says_what_this_read_finds_of_a_declared_settings_source(inv, capsys, monkeypatch):
    """A declared env.* setting's source is re-read like any other: a verified one whose file now states another value
    is said as CONFLICT beside the status the record gives it, in the JSON as its caveat, and still is once re-verifying
    puts the conflict on record."""
    monkeypatch.setenv("RB_TEST_ENV", json.dumps({"python": "3.12.1", "platform": "test-box", "torch": "2.4.1"}))
    Path("env.json").write_text('{"torch": "2.4.1"}', encoding="utf-8")
    assert inv("spec", "set", "e1", "env.torch", "--source", "env.json#torch")[0] == 0
    assert inv("env", "check")[1].data["declared"] == [{"experiment": "e1", "name": "env.torch", "value": "2.4.1", "status": "verified"}]
    assert main(["env", "check"]) == 0
    assert "  = e1/env.torch = 2.4.1 (verified)\n" in capsys.readouterr().out
    Path("env.json").write_text('{"torch": "2.5.0"}', encoding="utf-8")
    code, env = inv("env", "check")
    [d] = env.data["declared"]
    assert code == 0 and d["status"] == "verified"                                         # the record as rb wrote it
    assert [(c["code"], c["subject"], c["problem"]) for c in d["caveats"]] == [("conflict", "e1/env.torch", True)]
    assert "env.json#torch now says 2.5.0" in d["caveats"][0]["text"]
    assert main(["env", "check"]) == 0
    assert "  = e1/env.torch = 2.4.1 (verified · CONFLICT)\n" in capsys.readouterr().out
    assert inv("spec", "verify", "e1", "env.torch")[0] == 1
    assert [c["code"] for c in inv("env", "check")[1].data["declared"][0]["caveats"]] == ["conflict"]
    assert main(["env", "check"]) == 0
    assert "  = e1/env.torch = 2.4.1 (provisional · CONFLICT)\n" in capsys.readouterr().out
