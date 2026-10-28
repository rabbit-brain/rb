"""The Python SDK and `rb mcp` are the same record and the same rules as the CLI, reached another way.

Pinned here: the SDK asks rb for every read and write (rabbit_brain._rb), so it reads the record as it stands now;
a run block records what it logged only when it ends cleanly, with the script's command and `via: sdk`;
the calls (freeze, decide, retract) are recorded as whoever makes them, an agent's with the user's words it was given
(`words=`); over MCP every call is recorded as the connecting agent whatever RB_ACTOR says, a freeze and a decision
included, with their `words`, and there is no grant tool."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import rabbit_brain as rb
from rabbit_brain import RBError
from rbtest import as_the_user, cli_parser, command, environment, fake_rb, main, Mcp, registered_commands

HAS_GIT = shutil.which("git") is not None


@pytest.fixture
def project(workdir, monkeypatch):
    as_the_user(monkeypatch)
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 1e-4\ndata:\n  batch_size: 6\n", encoding="utf-8")
    if HAS_GIT:
        for cmd in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"], ["add", "."], ["commit", "-qm", "init"]):
            subprocess.run(["git", *cmd], check=True, capture_output=True)
    else:
        monkeypatch.setenv("USER", "t")
    return workdir


def started():
    state = rb.init("INT8 quality")
    exp = state.add_experiment("INT8 vs FP32", id="int8", baseline="fp32", candidates=["int8"], varies=["precision"])
    exp.spec.set("precision", "fp32", variant="fp32", source="note:default")
    exp.spec.set("precision", "int8", variant="int8", source="note:quantised")
    return state, exp


def test_sdk_reads_a_value_from_its_key_and_verifies_it(project):
    state, exp = started()
    s = exp.spec.set("optim.lr", source="configs/train.yaml#optim.lr")
    assert s.value == pytest.approx(1e-4) and s.status == "verified"
    assert "optim.lr" in exp.spec and exp.spec["optim.lr"].status == "verified"


def test_run_block_records_what_it_logged_with_the_scripts_command(project, monkeypatch):
    monkeypatch.setattr("sys.argv", ["eval.py", "--seed", "1"])
    state, exp = started()
    exp.spec.set("seed", per_run=True)
    with exp.run(seed=1) as run:
        run.log({"fp32.val_loss": 5.61})
        run.log(**{"int8.val_loss": 5.63})
    rec = run.record
    assert rec is not None and rec.metrics == {"fp32.val_loss": 5.61, "int8.val_loss": 5.63} and rec.per_run == {"seed": 1}
    assert rec.basis == "logged" and rec.receipt.command == "python eval.py --seed 1" and rec.receipt.via == "sdk"
    last = [json.loads(line) for line in Path(".rb/log.jsonl").read_text().splitlines()][-1]
    assert last["via"] == "sdk" and last["op"] == "record" and last["kind"] == "run"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_run_block_is_recorded_where_and_when_it_ran(project, monkeypatch):
    """A run block, and exp.record, are recorded by the code that ran them, so their receipt's code and environment are
    their own, even when they keep a file git committed long before, as rb run add --artifact keeps one; numbers
    recorded with a time months earlier are not."""
    Path("configs/train.yaml").write_text("optim:\n  lr: 2e-4\n", encoding="utf-8")
    Path("results.json").write_text('{"int8": {"val_loss": 5.66}}', encoding="utf-8")
    subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "lr"], check=True, capture_output=True, env={**os.environ, "GIT_COMMITTER_DATE": "2026-05-01T10:00:00Z"})
    state, exp = started()
    with exp.run() as run:
        run.artifact("configs/train.yaml")
        run.log({"fp32.val_loss": 5.61, "int8.val_loss": 5.63})
    assert "existed" not in run.record.receipt.produced
    assert {r["what"]: r["state"] for r in exp.rerunnability()}["environment"] == "ok"
    got = exp.record({"int8.val_loss": 5.66}, files=["results.json"])
    assert "existed" not in got.receipt.produced
    assert {r["what"]: r["state"] for r in exp.rerunnability()}["environment"] == "ok"
    exp.record({"int8.val_loss": 5.7}, produced_at="2026-05-02")
    assert {r["what"]: r["state"] for r in exp.rerunnability()}["environment"] == "partial"


def test_run_block_that_raises_records_nothing(project):
    state, exp = started()
    with pytest.raises(ValueError):
        with exp.run() as run:
            run.log(val_loss=1.0)
            raise ValueError("the eval crashed")
    assert run.record is None and not state.records("run")


def test_run_block_that_logs_nothing_is_an_error_not_silence(project):
    state, exp = started()
    with pytest.raises(RBError) as e:
        with exp.run():
            pass
    assert e.value.code == "E_OBJECT_INVALID"


def test_run_block_refuses_a_value_that_is_not_a_number(project):
    state, exp = started()
    with pytest.raises(RBError):
        with exp.run() as run:
            run.log(val_loss="5.6")


def test_an_identical_run_is_not_recorded_twice(project):
    state, exp = started()
    assert exp.record({"int8.val_loss": 5.6}) is not None
    assert exp.record({"int8.val_loss": 5.6}) is None
    assert exp.record({"int8.val_loss": 5.6}, again="a deliberate repeat") is not None


def test_sdk_finding_matches_the_rules(project):
    state, exp = started()
    crit = exp.criterion("INT8 costs at most 0.05", metric="change.val_loss", at_most=0.05)
    with exp.run() as run:
        run.log({"fp32.val_loss": 5.60, "int8.val_loss": 5.62})
    f = crit.finding()
    # The same axes the CLI reads: the rule met on one run, nothing wrong under it, the unchecked settings said as notes.
    assert f.rule_outcome == "meets" and f.says["headline"] == "estimate meets criterion · point only" and f.integrity == []
    assert "provisional" in {c.code for c in f.caveats}
    assert f.observations[0].value == pytest.approx(0.02) and f.observations[0].value == 0.02   # no float noise from the subtraction
    assert state.finding(crit.id) == state.criterion(crit.id).finding() == f


def test_a_rule_worked_out_in_python_is_said_as_it_was_meant(project, capsys):
    """A threshold worked out in Python carries the residue of the arithmetic (5.4 * 0.01, 0.7 - 0.05): the rule, its
    sentence, the reading beside it and a run's own value are said as they were meant, never with that residue."""
    state, exp = started()
    exp.spec.set("seed", per_run=True)
    c1 = exp.criterion("within 1% of the paper", metric="int8.val_loss", equals=5.4, tolerance=5.4 * 0.01)
    c2 = exp.criterion("at most 0.05 below 0.7", metric="int8.acc", at_least=0.7 - 0.05)
    c3 = exp.criterion("every seed at most 0.05 below 0.7", metric="int8.acc", at_least=0.7 - 0.05, over="each")
    for seed, val_loss, acc in ((1, 5.38, 0.65), (2, 5.41, 0.66), (3, 5.43, 0.64)):
        with exp.run(seed=seed) as run:
            run.log({"int8.val_loss": val_loss, "int8.acc": acc})
    f = c1.finding()
    assert f.rule == c1.data.rule() == "int8.val_loss = 5.4 ± 0.054" and f.says["rule"] == "estimate meets criterion: estimate 5.407 within 5.4 ± 0.054"
    f = c2.finding()
    assert f.rule == c2.data.rule() == "int8.acc ≥ 0.65" and f.says["rule"] == "estimate meets criterion: estimate 0.65 ≥ 0.65"
    assert f.says["comparison"].startswith("int8.acc: 0.65 [0.6252, 0.6748] · ")
    assert main(["show", c3.id]) == 0
    shown = capsys.readouterr().out
    assert "0.64999" not in shown and " 0.65 (logged)" in shown


def test_sdk_calls_are_recorded_as_the_agent_with_the_users_words(project):
    """freeze, decide and retract go through for an agent: each record names it and keeps the user's words given
    with words=, verbatim, as each call's log line does."""
    started()
    state = rb.open(agent="my-agent")
    assert state.actor == "agent:my-agent"
    exp = state.experiment("int8")
    exp.criterion("INT8 costs at most 0.05", metric="change.val_loss", at_most=0.05, id="c1")
    exp.freeze(why="before the runs", words="lock it before the runs")
    frozen = exp.data.frozen
    assert (frozen.by, frozen.why, frozen.words) == ("agent:my-agent", "before the runs", "lock it before the runs")
    run = exp.record({"fp32.val_loss": 5.60, "int8.val_loss": 5.62})
    d = state.get(state.decide("c1", "accept", why="within budget", words="good enough, accept it"), "decision")
    assert (d.by, d.why, d.words, d.via) == ("agent:my-agent", "within budget", "good enough, accept it", "sdk")
    state.retract(run.id, why="the export was stale", words="that run used the old export, drop it")
    retracted = state.get(run.id, "run").retracted
    assert (retracted.by, retracted.why, retracted.words) == ("agent:my-agent", "the export was stale", "that run used the old export, drop it")
    rows = [json.loads(line) for line in Path(".rb/log.jsonl").read_text().splitlines()]
    assert [(r["op"], r["actor"], r.get("words")) for r in rows[-4:]] == [
        ("freeze", "agent:my-agent", "lock it before the runs"), ("record", "agent:my-agent", None),
        ("decide", "agent:my-agent", "good enough, accept it"), ("retract", "agent:my-agent", "that run used the old export, drop it")]


def test_sdk_criterion_needs_exactly_one_rule(project):
    state, exp = started()
    with pytest.raises(RBError):
        exp.criterion("two rules", metric="val_loss", at_most=1, at_least=0)


def test_a_state_reads_what_others_wrote_since_it_opened(project, capsys):
    """The SDK holds no copy of the record: every call asks rb. A criterion a long-running script holds reads the run
    the command line recorded meanwhile, and a second State's write is in the first one's log."""
    state, exp = started()
    crit = exp.criterion("INT8 costs at most 0.05", metric="change.val_loss", at_most=0.05)
    assert crit.finding().says["headline"] == "criterion untested · untested" and exp.state_word == "planned"
    assert main(["run", "add", "int8", "fp32.val_loss=5.60", "int8.val_loss=5.62"]) == 0
    capsys.readouterr()
    f = crit.finding()
    assert f.rule_outcome == "meets" and f.integrity == [] and exp.state_word == "open"
    rb.open(agent="another").add_note("written through another State")
    assert state.log(1)[0]["actor"] == "agent:another"
    assert all(f.integrity == [] for f in [crit.finding(), state.finding(crit.id)])


def test_the_sdk_reads_the_project_its_records_and_what_rb_found(project):
    """What a script needs to read the record without the files: the project, every record of a kind (with the
    retracted ones or without), who rb records the process as, and what rb found in the repository."""
    state, exp = started()
    state.add_metric("val_loss")
    assert state.project.title == "INT8 quality" and state.project.id == "int8-quality"
    assert [e.id for e in state.records("experiment")] == ["int8"] and state.records("run") == []
    assert state.who()["id"] == state.actor and set(state.who()) == {"id", "via", "runtime", "ignored", "agent_session"}
    first = exp.record({"fp32.val_loss": 5.61})
    second = exp.record({"fp32.val_loss": 5.71})
    state.retract(first.id, why="wrong split")
    assert [r.id for r in state.records("run")] == [first.id, second.id]            # oldest first, retracted included
    assert [r.id for r in state.records("run", live=True)] == [second.id]
    with pytest.raises(RBError) as e:
        state.records("project")                                                    # the project is state.project
    assert e.value.code == "E_OBJECT_INVALID" and "one of question, experiment" in e.value.message

    Path("out").mkdir()
    Path("out/r.json").write_text(json.dumps({"val_loss": 5.5}), encoding="utf-8")
    assert state.found() == ([], [])                                                # found() takes no new look
    state.scan()
    found, unplaced = state.found()
    assert [f.path for f in found] == ["out/r.json"] and unplaced == []
    state.dismiss(found[0].key, "a scratch file")
    assert state.found() == ([], []) and Path("out/r.json").exists()               # left out of the list; the file stays


def test_rb_clone_is_the_sdk_call_every_time(project, tmp_path_factory):
    """`rabbit_brain.clone` is the SDK's call each time it is used, whatever else of rb has been imported since."""
    state, exp = started()
    bundle = state.export(with_record=True)["path"]
    one = rb.clone(bundle, tmp_path_factory.mktemp("one") / "c")
    two = rb.clone(Path(bundle), tmp_path_factory.mktemp("two") / "c")
    assert one.root != two.root and one.status()["project"]["title"] == two.status()["project"]["title"] == "INT8 quality"
    assert one.experiment("int8").data.settings == [] and [s.status for _, s in one.experiment("int8").spec.items()] == ["inherited", "inherited"]


def test_sdk_takes_the_values_a_training_script_has(project):
    """A numpy or torch scalar is its number and a path is its text, wherever a value goes; what is no value is said."""
    class Scalar:
        def __init__(self, v):
            self.v = v

        def item(self):
            return self.v

    state, exp = started()
    assert exp.spec.set("depth", Scalar(18), verify=False).value == 18
    assert exp.spec.set("optim.lr", source=Path("configs/train.yaml#optim.lr")).status == "verified"
    exp.spec.set("fold", per_run=True)
    run = exp.record({"fp32.val_loss": Scalar(5.6), "int8.val_loss": 5.62}, per_run={"fold": Scalar(2)}, files=[Path("configs/train.yaml")],
                     config=Path("configs/train.yaml"))
    assert run.metrics == {"fp32.val_loss": 5.6, "int8.val_loss": 5.62} and run.per_run == {"fold": 2} and run.files[0].path == "configs/train.yaml"
    assert run.receipt.config.path == "configs/train.yaml"
    with pytest.raises(RBError) as e:
        exp.spec.set("net", object())
    assert e.value.code == "E_OBJECT_INVALID" and "got object" in e.value.message


def test_the_sdk_warns_of_a_write_what_the_command_line_warns_of(project, capsys):
    """What rb run add and rb analysis add warn of beside the record (an input whose copy here is not the bytes it names,
    numbers typed with no document) comes from the SDK as an RBWarning at the line that wrote, as --json carries it in
    data.warnings."""
    from rabbit_brain import RBWarning
    state, exp = started()
    Path("held.txt").write_text("the held-out set, v1\n", encoding="utf-8")
    state.add_resource("held", type="dataset", name="held-out set", file="held.txt")
    Path("held.txt").write_text("the held-out set, v2\n", encoding="utf-8")
    with pytest.warns(RBWarning) as caught:
        exp.record({"fp32.val_loss": 5.61, "int8.val_loss": 5.63}, inputs=["held:eval"])
    # the first run here also puts its code under refs/rb/snapshots/ and says so once
    [changed] = [str(w.message) for w in caught if not str(w.message).startswith("rb put the code in git at refs/rb/snapshots/")]
    assert changed.startswith("held: the copy here is not the bytes held names") and all(w.filename == __file__ for w in caught)
    assert main(["run", "add", "int8", "fp32.val_loss=5.6", "int8.val_loss=5.64", "--input", "held:eval", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["warnings"] == [changed]
    with pytest.warns(RBWarning) as caught:
        typed = exp.analysis([{"subject": "int8", "reference": "fp32", "metric": "val_loss", "estimate": 0.02, "lo": 0.01, "hi": 0.03}],
                             method="paired bootstrap")
    assert [str(w.message) for w in caught] == [f"{typed.id}'s numbers were typed with no document to check them against; every reading of "
                                                "them says so"]
    assert main(["analysis", "add", "int8", "--compare", "int8/fp32:val_loss=0.03[0.02,0.04]", "--method", "paired bootstrap", "--json"]) == 0
    cli = json.loads(capsys.readouterr().out)["data"]
    assert cli["warnings"] == [f"{cli['object']['id']}'s numbers were typed with no document to check them against; every reading of them says so"]


def test_the_sdk_warns_when_the_output_it_names_is_recorded_with_other_numbers(project, capsys):
    """A run whose output files are already recorded is not written again. When the numbers given differ from what that
    run recorded, the SDK says so with the line --json carries in data.warnings, named as the run keeps them, also when
    they are given for one variant; the run stays as it was."""
    from rabbit_brain import RBWarning
    state, exp = started()
    Path("out").mkdir()
    Path("out/m.json").write_text('{"val_loss": 5.63}\n', encoding="utf-8")
    assert main(["run", "add", "int8", "fp32.val_loss=5.61", "int8.val_loss=5.63", "--file", "out/m.json", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)["data"]["object"]["id"]
    line = "the numbers given now differ from what it recorded: int8.val_loss 5.63 recorded, 5.7 given"
    with pytest.warns(RBWarning) as caught:
        assert exp.record({"fp32.val_loss": 5.61, "int8.val_loss": 5.7}, files=["out/m.json"]) is None
    assert [str(w.message) for w in caught] == [line] and caught[0].filename == __file__
    with pytest.warns(RBWarning) as caught:
        assert exp.record({"val_loss": 5.7}, variant="int8", files=["out/m.json"]) is None
    assert [str(w.message) for w in caught] == [line]
    for argv in (["fp32.val_loss=5.61", "int8.val_loss=5.7"], ["val_loss=5.7", "--variant", "int8"]):
        assert main(["run", "add", "int8", *argv, "--file", "out/m.json", "--json"]) == 0
        cli = json.loads(capsys.readouterr().out)["data"]
        assert cli["duplicate_of"] == first and cli["warnings"] == [line], argv
    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["counts"]["run_count"] == 1


def test_the_sdk_says_when_rb_does_not_answer_or_is_another_version(project, monkeypatch, tmp_path):
    """The package asks the rb installed with it. One that does not answer, or answers as another version, is a broken
    install, said with the fix, never a half-working SDK."""
    started()
    silent = fake_rb(tmp_path, None)
    monkeypatch.setenv("RB_BIN", silent)
    with pytest.raises(RBError) as e:
        rb.open()
    assert e.value.code == "E_INTERNAL" and f"rb did not answer ({silent})" in e.value.message and "reinstall" in e.value.fix.lower()
    monkeypatch.setenv("RB_BIN", fake_rb(tmp_path, '{"ok": true, "value": {"root": "/x", "rb": "0.0.1"}}'))
    with pytest.raises(RBError) as e:
        rb.open()
    assert e.value.code == "E_INTERNAL" and e.value.message.startswith("rb 0.0.1 answered, and this Python package is rabbit-brain")


def test_every_reinstall_advised_names_the_version_installed(workdir, monkeypatch):
    """Unpinned, pip reinstalls the newest final release, which can be another version than the one installed: the
    package, rb and the rabbit-brain command each name the version they are."""
    from rabbit_brain import _rb
    real = command()[0]
    said = json.loads(subprocess.run([real, "version", "--json"], env=environment(), capture_output=True, check=True).stdout)
    pinned = f'pip install --force-reinstall "rabbit-brain=={said["data"]["version"]}"'
    sdk_pinned = f'pip install --force-reinstall "rabbit-brain=={rb.__version__}"'
    with monkeypatch.context() as m:
        m.setattr(_rb, "binary", lambda: None)
        with pytest.raises(RBError) as e:
            _rb.found()
        assert sdk_pinned in e.value.fix
    for says in (None, '{"ok": true, "value": {"root": "/x", "rb": "0.0.1"}}'):
        with monkeypatch.context() as m:
            m.setenv("RB_BIN", fake_rb(workdir, says))
            with pytest.raises(RBError) as e:
                rb.open()
            assert sdk_pinned in e.value.fix, e.value.fix
    answered = subprocess.run([real, "bridge"], input=b"not a request\n", env=environment(), capture_output=True, check=True)
    assert pinned in json.loads(answered.stdout.decode("utf-8").splitlines()[-1])["error"]["fix"]
    wrapper = Path(real).with_name("rabbit-brain" + Path(real).suffix)
    if wrapper.is_file():
        alone = workdir / "alone"
        alone.mkdir()
        shutil.copy2(wrapper, alone / wrapper.name)
        r = subprocess.run([str(alone / wrapper.name), "version"], env=environment(), capture_output=True, text=True)
        assert r.returncode == 3 and pinned in r.stderr, r.stderr


# ---------------------------------------------------------------- MCP

@pytest.fixture
def mcp(project):
    """Connect to `rb mcp` here as an agent would (`mcp("claude-code")`, or `mcp()` to be named by the client)."""
    opened = []

    def connect(agent=None):
        opened.append(Mcp(agent))
        return opened[-1]

    yield connect
    for server in opened:
        server.close()


def rpc(server, method, params=None, id=1):
    return server.handle({"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})


def call(server, name, arguments=None):
    return rpc(server, "tools/call", {"name": name, "arguments": arguments or {}})["result"]


def test_every_research_command_has_an_mcp_tool_or_a_recorded_reason(mcp):
    """CLI and MCP stay in step: a new research command either becomes a tool or names, here, why not."""
    NOT_OVER_MCP = {
        "init": "the record is started in the repository by the CLI or SDK; MCP serves an existing project",
        "journal run": "command execution stays off the MCP surface; agents have their own shell",
        "clone": "like init, it creates a project where none exists; MCP serves an existing one",
        "fmt": "it rewrites the layout of Rabbit Brain YAML files and changes no content, so nothing is recorded; upkeep for the shell, like a code formatter",
        "login": "the device flow shows a code and waits until someone approves it on the site; a tool call would block on that wait, so it runs in a shell",
        "logout": "it revokes and removes this machine's credential, the other half of login, and runs in a shell like it",
        "sync": "it uploads with the credential login stored on this machine, so it runs in the same shell; anyone may run it there",
        "env check": "it reads the machine it runs on; over MCP that is the agent's machine, and the CLI is its home",
        "compile": "the packaging surface for the agent's own shell session; the writes it leads to are the ordinary tools",
        "confirm": "it makes a call the user's own, in their own terminal; every MCP call is an agent's, and a confirmation an agent passes establishes nothing",
    }
    tools = {t["name"] for t in rpc(mcp("claude-code"), "tools/list")["result"]["tools"]}     # a tool is named for its command's words
    for c in cli_parser.LEDGER_COMMANDS:
        assert c.replace(" ", "_") in tools or c in NOT_OVER_MCP, f"rb {c} has no MCP tool and no recorded reason why not"


def test_sdk_sees_what_landed_without_being_told(project):
    state, exp = started()
    exp.criterion("top1 holds", metric="top1", at_least=0.7)
    Path("out").mkdir()
    Path("out/m.json").write_text('{"top1": 0.9}', encoding="utf-8")
    assert state.scan()["new"] >= 1
    found, unplaced = state.reconcile()
    assert found and found[0].argv[:3] == ["run", "add", "int8"]
    assert any(e.get("op") == "file" for e in state.journal())


def test_mcp_tools_map_onto_real_commands(mcp):
    real = registered_commands()
    tools = rpc(mcp("claude-code"), "tools/list")["result"]["tools"]
    assert len(tools) > 25
    for t in tools:
        assert t["name"] in real or t["name"].replace("_", " ", 1) in real, t["name"]
        assert t["inputSchema"]["type"] == "object" and t["description"]


def test_mcp_arguments_are_the_command_line_they_stand_for(project, mcp):
    """A tool call is the command it is named for: a mapping of results and of a run's own settings, a list as one
    value, a direction as its switch, a second execution with its reason. An argument a tool does not take is refused,
    never passed on."""
    state, exp = started()
    exp.spec.set("seed", per_run=True)
    server = mcp("claude-code")
    first = call(server, "run_add", {"experiment": "int8", "metrics": {"int8.val_loss": 5.64, "fp32.val_loss": 5.61}, "set": {"seed": 2}})
    run = first["structuredContent"]["data"]["object"]
    assert not first["isError"] and run["metrics"] == {"int8.val_loss": 5.64, "fp32.val_loss": 5.61} and run["per_run"] == {"seed": 2}
    same = call(server, "run_add", {"experiment": "int8", "metrics": {"int8.val_loss": 5.64, "fp32.val_loss": 5.61}, "set": {"seed": 2}})
    assert same["structuredContent"]["data"]["object"]["id"] == run["id"]             # identical: the same run, not a second one
    again = call(server, "run_add", {"experiment": "int8", "metrics": {"int8.val_loss": 5.64, "fp32.val_loss": 5.61}, "set": {"seed": 2},
                                     "again": True, "why": "repeat"})
    second = again["structuredContent"]["data"]["object"]
    assert not again["isError"] and second["id"] != run["id"] and second["note"].startswith("recorded again: repeat")
    crop = call(server, "spec_set", {"experiment": "int8", "name": "crop", "value": [288, 960], "source": "note:the paper's crop"})
    assert crop["structuredContent"]["data"]["object"]["value"] == [288, 960]
    many = call(server, "spec_set", {"experiment": "int8", "from_file": "configs/train.yaml", "keys": ["optim.*", "data.batch_size"]})
    assert not many["isError"] and "optim.lr" in exp.spec and "data.batch_size" in exp.spec
    metric = call(server, "metric_add", {"name": "val_loss", "direction": "minimize", "alias": ["cross_entropy"]})
    assert (metric["structuredContent"]["data"]["object"]["direction"], metric["structuredContent"]["data"]["object"]["aliases"]) == \
        ("minimize", ["cross_entropy"])
    refused = call(server, "status", {"sneaky": 1})
    assert refused["isError"] and refused["structuredContent"]["errors"] == [
        {"code": "E_USAGE", "message": "unknown argument for status: sneaky"}]
    refused = call(server, "status", {"sneaky": 1, "also": 2})
    assert refused["isError"] and refused["structuredContent"]["errors"] == [
        {"code": "E_USAGE", "message": "unknown arguments for status: also, sneaky"}]


def test_mcp_records_every_call_as_the_agent_whatever_rb_actor_says(project, monkeypatch, mcp):
    started()
    monkeypatch.setenv("RB_ACTOR", "human:t")
    server = mcp()
    init = rpc(server, "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "Claude Desktop"}})
    assert init["result"]["serverInfo"]["name"] == "rabbit-brain" and "the user" in init["result"]["instructions"]
    r = call(server, "question_add", {"text": "Does batch size matter?"})
    assert not r["isError"] and r["structuredContent"]["data"]["actor"]["id"] == "agent:claude-desktop"
    last = [json.loads(line) for line in Path(".rb/log.jsonl").read_text().splitlines()][-1]
    assert last["actor"] == "agent:claude-desktop" and last["via"] == "mcp"


def test_mcp_freeze_and_decide_are_recorded_as_the_agent_with_its_words(project, monkeypatch, mcp):
    """Over MCP a freeze and a direction call go through as the connecting agent, whatever RB_ACTOR says, each with the
    user's words given in `words`, on the record and on its log line."""
    started()
    monkeypatch.setenv("RB_ACTOR", "human:t")
    server = mcp("claude-code")
    r = call(server, "freeze", {"experiment": "int8", "why": "before runs", "words": "freeze it now"})
    frozen = r["structuredContent"]["data"]["object"]["frozen"]
    assert not r["isError"] and (frozen["by"], frozen["words"]) == ("agent:claude-code", "freeze it now")
    r = call(server, "decide", {"subject": "int8", "action": "park", "why": "not now", "words": "park it until the next export"})
    d = r["structuredContent"]["data"]["object"]
    assert not r["isError"] and (d["by"], d["action"], d["words"], d["via"]) == ("agent:claude-code", "park", "park it until the next export", "mcp")
    rows = [json.loads(line) for line in Path(".rb/log.jsonl").read_text().splitlines()]
    assert [(x["op"], x["actor"], x["via"], x["words"]) for x in rows[-2:]] == [
        ("freeze", "agent:claude-code", "mcp", "freeze it now"), ("decide", "agent:claude-code", "mcp", "park it until the next export")]
    assert not {"grant_add", "grant_revoke"} & {t["name"] for t in rpc(server, "tools/list")["result"]["tools"]}
    assert rpc(server, "tools/call", {"name": "grant_add", "arguments": {}})["error"]["code"] == -32602


def test_mcp_context_resource_and_tool_are_markdown(project, mcp):
    started()
    server = mcp("claude-code")
    tool = call(server, "context")
    assert tool["content"][0]["text"].startswith("# The record: INT8 quality")
    res = rpc(server, "resources/read", {"uri": "rb://context"})["result"]
    assert res["contents"][0]["text"].startswith("# The record")
    docs = rpc(server, "resources/read", {"uri": "rb://docs"})["result"]
    assert docs["contents"][0]["text"].startswith("# Rabbit Brain: AGENTS.md")


def test_an_export_carries_the_record_only_when_asked_in_the_sdk_and_over_mcp(project, mcp, tmp_path_factory):
    """The SDK's export and the MCP export tool write the page alone, as rb export does, and the record only with
    with_record: the same choice under the same name wherever an export is asked for, an address to clone from taking
    the record."""
    state, _ = started()
    out = tmp_path_factory.mktemp("exports")
    page = state.export(out=str(out / "page"))
    assert page["with_record"] is False and sorted(p.name for p in (out / "page").iterdir()) == ["README.md", "manifest.json", "page.html"]
    bundle = state.export(out=str(out / "bundle"), with_record=True)
    assert bundle["with_record"] is True and (out / "bundle" / "record" / ".rb" / "log.jsonl").is_file()
    server = mcp("claude-code")
    for args, record in (({"out": str(out / "mcp-page")}, False), ({"out": str(out / "mcp-bundle"), "with_record": True}, True)):
        r = call(server, "export", args)
        assert not r["isError"] and r["structuredContent"]["data"]["with_record"] is record, r
        assert (Path(args["out"]) / "record").is_dir() is record
    r = call(server, "export", {"out": str(out / "mcp-url"), "url": "https://lab.example/x/"})
    assert r["isError"] and r["structuredContent"]["errors"][0]["code"] == "E_USAGE" and not (out / "mcp-url").exists()


def test_mcp_errors_are_answers_not_crashes(project, mcp):
    started()
    server = mcp("a")
    assert rpc(server, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert rpc(server, "no/such")["error"]["code"] == -32601
    r = call(server, "show", {"id": "zzzz"})
    assert r["isError"] and r["structuredContent"]["errors"][0]["code"] == "E_OBJECT_NOT_FOUND"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert rpc(server, "ping", id=7)["id"] == 7                         # the notification got no line of its own
    assert server.close() == 0                                          # and hanging up ends it cleanly


def test_mcp_serves_line_delimited_json_over_stdio(project):
    started()
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "x"}}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    sent = "\n".join(json.dumps(m) for m in lines) + "\nnot json\n"
    r = subprocess.run([*command(), "mcp"], input=sent.encode("utf-8"), capture_output=True, env=environment())
    replies = [json.loads(line) for line in r.stdout.decode("utf-8").splitlines()]
    assert r.returncode == 0 and [x.get("id") for x in replies] == [1, 2, None]
    assert {t["name"] for t in replies[1]["result"]["tools"]} >= {"context", "criterion_add", "run_add", "freeze", "decide"}
    assert replies[2]["error"]["code"] == -32700


# ---------------------------------------------------------------- what a freeze shows


def test_an_agents_freeze_says_what_it_locks_and_is_recorded_as_the_agents(project, capsys, monkeypatch):
    """rb freeze prints what it locks, whoever runs it: each criterion with its rule, and for a criterion an agent
    declared, a look at whether it is the test meant, in words that name no writer. The freeze is the agent's, with the
    words it gave beside its reason, said as given: never as the user's, which rb cannot know they are."""
    started()
    monkeypatch.setenv("CLAUDECODE", "1")
    assert main(["criterion", "add", "INT8 costs at most 0.05", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c1"]) == 0
    capsys.readouterr()
    assert main(["freeze", "int8", "--why", "before the runs", "--words", "lock it before the runs"]) == 0
    out = capsys.readouterr().out
    assert "int8 frozen: spec " in out and 'before the runs · words (as given): "lock it before the runs"' in out and "your words" not in out
    assert "    criterion c1 (primary): change.val_loss ≤ 0.05\n" in out
    assert "look: check c1 before locking it; freezing locks it as the test. Is it the one you mean?" in out
    assert "claude-code" not in out
    frozen = rb.open().experiment("int8").data.frozen
    assert (frozen.by, frozen.why, frozen.words) == ("agent:claude-code", "before the runs", "lock it before the runs")


def test_a_freeze_review_points_at_settings_verified_under_another_name(project, capsys, monkeypatch):
    state, exp = started()
    Path("paper.txt").write_text("The learning rate is 0.01 and weight decay 0.0001 for all runs.\n")
    exp.spec.set("wd", 0.0001, source="paper.txt:1", term="weight decay")
    exp.spec.set("optim.lr", source="configs/train.yaml#optim.lr")
    main(["freeze", "int8", "--why", "go"])
    out = capsys.readouterr().out
    assert "look: wd is verified, matched by the words 'weight decay'" in out and "look: optim.lr" not in out


def test_a_freeze_review_says_what_this_read_finds_of_each_verified_source(project, capsys):
    state, exp = started()
    exp.spec.set("optim.lr", source="configs/train.yaml#optim.lr")
    exp.spec.set("data.batch_size", source="configs/train.yaml#data.batch_size")
    Path("configs/train.yaml").write_text("optim:\n  lr: 3e-4\n", encoding="utf-8")
    main(["freeze", "int8", "--why", "go"])
    out = capsys.readouterr().out
    assert "optim.lr = 0.0001 · verified · configs/train.yaml#optim.lr · CONFLICT" in out
    assert "data.batch_size = 6 · verified · configs/train.yaml#data.batch_size · STALE" in out
    assert "look: optim.lr: the record says 0.0001, but configs/train.yaml#optim.lr now says 0.0003" in out
    assert "look: data.batch_size: configs/train.yaml has no key data.batch_size" in out


def test_a_freeze_review_says_a_conflict_on_record_as_the_record_holds_it_then_the_source(project, capsys):
    """A conflict rb spec verify kept on the setting is said in the review the way a fresh read says one: what the
    record holds first, then what the source now says."""
    state, exp = started()
    exp.spec.set("optim.lr", source="configs/train.yaml#optim.lr")
    Path("configs/train.yaml").write_text("optim:\n  lr: 3e-4\ndata:\n  batch_size: 6\n", encoding="utf-8")
    assert main(["spec", "verify", "int8", "optim.lr"]) == 1           # the conflict is on the setting now
    capsys.readouterr()
    assert main(["freeze", "int8", "--why", "go"]) == 0
    out = capsys.readouterr().out
    assert "optim.lr = 0.0001 · provisional · configs/train.yaml#optim.lr · CONFLICT" in out
    assert ("look: optim.lr conflicts with its source: the record says 0.0001, but configs/train.yaml#optim.lr now says "
            "0.0003\n") in out
