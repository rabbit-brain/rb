"""The workspace client: refusals, exit codes, and the credential that never lands anywhere.

No test here touches the network. The transport is replaced, because what needs pinning is how `rb`
behaves when a workspace says no, not whether HTTP works.
"""
import json

import pytest

from rabbit_brain import workspace
from rabbit_brain.cli import main
from rabbit_brain.errors import EXIT_ENVIRONMENT, EXIT_INVALID, RBError
from rabbit_brain.models import Envelope


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(workspace.TOKEN_ENV, raising=False)
    monkeypatch.setenv(workspace.BASE_ENV, "http://workspace.invalid")


def reply(status, body):
    return lambda method, path, body_=None, authed=True: (status, body)


# ---- the credential ------------------------------------------------------------------------

def test_no_token_is_a_named_error_not_a_crash():
    with pytest.raises(RBError) as e:
        workspace.token()
    assert e.value.code == "E_WORKSPACE_NO_TOKEN"
    assert e.value.exit_code == EXIT_INVALID


def test_token_is_read_from_the_environment_only(monkeypatch, tmp_path):
    monkeypatch.setenv(workspace.TOKEN_ENV, "rb_secret")
    assert workspace.token() == "rb_secret"
    # nothing on disk should ever hold it
    assert not list(tmp_path.rglob("*rb_secret*"))


def test_the_token_never_appears_in_an_error_message(monkeypatch):
    """An error is the most likely place for a credential to leak into a log."""
    monkeypatch.setenv(workspace.TOKEN_ENV, "rb_supersecret_value")
    monkeypatch.setattr(workspace, "_request", reply(401, {"error": "nope"}))
    with pytest.raises(RBError) as e:
        workspace.status_of()
    blob = f"{e.value.code} {e.value.message} {e.value.fix}"
    assert "rb_supersecret_value" not in blob


# ---- refusals ------------------------------------------------------------------------------

def test_payment_required_names_the_plan_and_its_price(monkeypatch):
    monkeypatch.setenv(workspace.TOKEN_ENV, "rb_t")
    monkeypatch.setattr(workspace, "_request", reply(402, {
        "error": "This workspace has no active subscription.",
        "fix": "Collecting results into a shared workspace is the paid feature.",
        "required_plan": {"price": "USD 99.00 / workspace / month"},
    }))
    with pytest.raises(RBError) as e:
        workspace.push({"version": 2})
    assert e.value.code == "E_WORKSPACE_PAYMENT_REQUIRED"
    assert "99.00" in (e.value.message or "")


def test_an_unfinished_plan_cannot_be_bought(monkeypatch):
    monkeypatch.setenv(workspace.TOKEN_ENV, "rb_t")
    monkeypatch.setattr(workspace, "_request", reply(409, {
        "error": "The Team workspace plan is published but not yet sellable.",
        "why": "It is not finished, and we do not take payment for unfinished work.",
    }))
    with pytest.raises(RBError) as e:
        workspace.checkout("workspace")
    assert e.value.code == "E_WORKSPACE_NOT_SELLABLE"


def test_an_unreachable_workspace_is_an_environment_failure():
    """Exit 3, not 2: the caller's input was fine and the run is still on disk."""
    from rabbit_brain.errors import ENVIRONMENT_CODES
    assert "E_WORKSPACE_UNREACHABLE" in ENVIRONMENT_CODES
    assert RBError("E_WORKSPACE_UNREACHABLE").exit_code == EXIT_ENVIRONMENT


# ---- the commands --------------------------------------------------------------------------

def test_plans_needs_no_token(monkeypatch, capsys):
    monkeypatch.setattr(workspace, "_request", reply(200, {
        "plans": [{"id": "workspace", "price": "USD 99.00 / workspace / month",
                   "available": False, "includes": ["shared reviews"]}],
        "note": "A human approves every purchase.",
    }))
    assert main(["plans", "--json"]) == 0
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert env.ok and env.data["plans"][0]["available"] is False


def test_push_refused_exits_two_and_names_the_code(monkeypatch, capsys, workdir):
    monkeypatch.setenv(workspace.TOKEN_ENV, "rb_t")
    main(["example", "--json"]); capsys.readouterr()
    run_id = json.loads(json.dumps(None)) or None
    from rabbit_brain.runs import runs_dir
    run_id = sorted(p.name for p in runs_dir().iterdir())[0]
    monkeypatch.setattr(workspace, "_request", reply(402, {"error": "no subscription"}))
    code = main(["workspace", "push", run_id, "--json"])
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert code == EXIT_INVALID
    assert env.ok is False
    assert env.errors[0].code == "E_WORKSPACE_PAYMENT_REQUIRED"


def test_a_refused_push_leaves_the_run_on_disk(monkeypatch, capsys, workdir):
    monkeypatch.setenv(workspace.TOKEN_ENV, "rb_t")
    main(["example", "--json"]); capsys.readouterr()
    from rabbit_brain.runs import runs_dir
    run_id = sorted(p.name for p in runs_dir().iterdir())[0]
    bundle = runs_dir() / run_id / "bundle.json"
    before = bundle.read_bytes()
    monkeypatch.setattr(workspace, "_request", reply(402, {"error": "no subscription"}))
    main(["workspace", "push", run_id, "--json"]); capsys.readouterr()
    assert bundle.read_bytes() == before
