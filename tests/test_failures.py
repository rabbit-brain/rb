"""Every failure is said one way.

rb says a failure as `rb <command>: <what happened>` and a line that says what to do; with --json, as the envelope's
errors. That holds for a failure of the system under rb (a file it cannot write, an address that does not answer),
which rb says in a sentence naming the file or the address, with the system's own words for why; and it holds for a
fault in rb's own code. None is said as the crash of a program: no traceback, no exception's name, no error number in
brackets. Where a missing directory or an unreadable entry need not be a failure at all, it is not one. Every name
here is made up."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, ROOT, command, environment, main

# what the tests here clear besides an agent and a session: a hub address or credential, and a fault rb is told to play
NOT_HERE = ("RB_HUB_URL", "RB_HUB_TOKEN", "RB_TEST_FAULT", "RB_TEST_MCP_FAULT")
# what a crash in another language looks like: a traceback, an exception class, an error number in brackets, the words
# an interpreter has for its own types
IN_ANY_TEXT = (r"Traceback|\b[A-Z][A-Za-z]*(?:Error|Exception)\b|\[Errno|\[WinError|NoneType|object is not|object has no|__format__|codec can't|"
               r"unhashable")
CRASH_WORDS = re.compile(IN_ANY_TEXT + r"|\(os error")     # in what rb prints, the runtime's own way of saying a system error too
NOWHERE = "http://127.0.0.1:1"       # nothing listens on port 1


@pytest.fixture
def record(tmp_path, monkeypatch, capsys):
    """A small record in a directory of its own, a human at their terminal, and no account anywhere but here."""
    as_the_user(monkeypatch, *NOT_HERE)
    monkeypatch.setenv("RB_ACTOR", "human:t")
    monkeypatch.setenv("RB_CONFIG_DIR", str(tmp_path / "account"))
    root = tmp_path / "study"
    root.mkdir()
    monkeypatch.chdir(root)
    for argv in (["init", "does the wide model hold?", "--no-agents"],
                 ["experiment", "add", "wide against base", "--id", "e1", "--baseline", "base", "--candidate", "wide"]):
        assert main(argv) == 0
    capsys.readouterr()
    return root


def said(capsys, *argv):
    """rb <argv>, in text: (exit code, stdout, stderr)."""
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def envelope(capsys, *argv):
    """rb <argv> --json: (exit code, the one envelope on stdout, stderr)."""
    code, out, err = said(capsys, *argv, "--json")
    return code, Envelope.model_validate_json(out), err


def write_odd_files(root: Path) -> None:
    (root / "taken").write_text("a file where a directory is asked for\n", encoding="utf-8")
    (root / "latin.yaml").write_bytes(b"rb: 1\nkind: note\ntext: \xff\n")
    (root / "large.yaml").write_text("rb: 1\nkind: metric\nid: m\nscale: 1.0e+999\n", encoding="utf-8", newline="\n")
    (root / "seen.json").write_text('{"model": {"depth": 18}}\n', encoding="utf-8", newline="\n")


# (the command line, the command as rb names it, the code, a piece of the message, a piece of the fix)
FAILURES = [
    (["page", "--out", "taken/one.html"], "page", "E_WRITE_FAILED", "taken is a file, not a directory", "directories"),
    (["open", "--port", "70000"], "open", "E_USAGE", "70000 is not a port", "rb open --port"),
    (["open", "--port", "99999999999999999999999999"], "open", "E_USAGE", "99999999999999999999999999 is not a port", "rb open --port"),
    (["show", "latin.yaml"], "show", "E_YAML", "latin.yaml is not UTF-8 text (the byte 0xff at offset 23)", "UTF-8"),
    (["show", "large.yaml"], "show", "E_YAML", "1.0e+999 is too large to keep as a number", "quote it"),
    (["spec", "set", "e1", "--from", "missing.yaml", "--keys", "optim.*"], "spec set", "E_FILE_NOT_FOUND", "No such file: missing.yaml", "path"),
    (["run", "add", "e1", "--from", "missing.json"], "run add", "E_FILE_NOT_FOUND", "No such file: missing.json", "path"),
    (["run", "add", "e1", "wide.top1=0.7", "--config", "missing.yaml"], "run add", "E_FILE_NOT_FOUND", "No such file: missing.yaml", "path"),
    (["analysis", "add", "e1", "--from", "missing.json", "--method", "m", "--unit", "u", "--units", "3"], "analysis add", "E_FILE_NOT_FOUND",
     "No such file: missing.json", "path"),
    (["resource", "add", "r1", "--type", "dataset", "--name", "d", "--file", "missing.bin"], "resource add", "E_FILE_NOT_FOUND",
     "No such file: missing.bin", "path"),
    (["spec", "set", "e1", "depth", "--source", "run:seen.json#/model/width"], "spec set", "E_SOURCE_UNRESOLVED",
     "has no value to read (nothing at width there)", "source"),
    (["spec", "set", "e1", "depth", "--source", "run:unseen.json#/model/depth"], "spec set", "E_SOURCE_UNRESOLVED", "has no value to read (", "source"),
    (["login", "--hub", "rabbitbrain"], "login", "E_USAGE", "rabbitbrain is not a web address", "--hub"),
    (["login", "--hub", NOWHERE], "login", "E_HUB_UNREACHABLE", f"{NOWHERE}/api/hub/device/code could not be reached: ", "address"),
    (["clone", "https:///bundle.zip", "copy"], "clone", "E_USAGE", "https:///bundle.zip is not a web address", "address"),
    (["clone", "not-here", "copy"], "clone", "E_FILE_NOT_FOUND", "No such file or directory: not-here", "what rb export --with-record wrote"),
    (["clone", "not-here.zip", "copy"], "clone", "E_FILE_NOT_FOUND", "No such file or directory: not-here.zip", "what rb export --with-record wrote"),
    (["sync", "--hub", NOWHERE], "sync", "E_USAGE", f"No credential for {NOWHERE} on this machine.", f"rb login --hub {NOWHERE}"),
    (["sync"], "sync", "E_NOT_AVAILABLE", "rabbitbrain.ai's hosted service is not open yet, so there is no account to keep a copy in; rb sync sent nothing.",
     "rb page writes its page as one file"),
    (["login"], "login", "E_NOT_AVAILABLE", "rabbitbrain.ai's hosted service is not open yet, so there is no account to sign in to; rb login sent nothing.",
     "rb export --with-record writes a copy to host anywhere"),
    # rabbitbrain.ai however it is spelled: www., http, another case, a trailing slash
    (["login", "--hub", "https://www.rabbitbrain.ai"], "login", "E_NOT_AVAILABLE", "rb login sent nothing.", "rb export --with-record"),
    (["login", "--hub", "http://rabbitbrain.ai"], "login", "E_NOT_AVAILABLE", "rb login sent nothing.", "rb export --with-record"),
    (["sync", "--hub", "https://RabbitBrain.ai/"], "sync", "E_NOT_AVAILABLE", "rb sync sent nothing.", "rb page writes its page as one file"),
    (["sync", "--hub", "https://www.rabbitbrain.ai"], "sync", "E_NOT_AVAILABLE", "rb sync sent nothing.", "rb page writes its page as one file"),
    (["rerun"], "rerun", "E_NOT_AVAILABLE", "`rb rerun` is not part of rabbit-brain", "rb run add"),
]


@pytest.mark.parametrize("argv, name, code, message, fix", FAILURES, ids=[" ".join(f[0][:3]) for f in FAILURES])
def test_a_failure_is_rb_saying_what_happened_and_what_to_do(record, capsys, argv, name, code, message, fix):
    write_odd_files(record)
    exit_code, out, err = said(capsys, *argv)
    lines = err.splitlines()
    assert exit_code == 2 and out == "", (exit_code, out, err)
    assert lines[0].startswith(f"rb {name}: ") and message in lines[0], err
    assert lines[-1].startswith("  → ") and fix in lines[-1], err
    assert not CRASH_WORDS.search(err), err
    exit_code, env, err = envelope(capsys, *argv)
    assert exit_code == 2 and not env.ok and err == "", (exit_code, env, err)
    assert [e.code for e in env.errors] == [code] and message in env.errors[0].message and fix in env.errors[0].fix
    assert not CRASH_WORDS.search(env.model_dump_json())


def test_a_fault_in_rb_itself_is_said_the_same_way_and_asks_to_be_reported(record, capsys, monkeypatch):
    """What rb's own code did not expect: the same two lines, the same envelope, exit 3, and where to report it.
    RB_TEST_FAULT makes a command stop on one, as RB_TEST_NOW fixes the clock."""
    monkeypatch.setenv("RB_TEST_FAULT", "a made-up fault")
    code, out, err = said(capsys, "status")
    assert code == 3 and out == ""
    first, second = err.splitlines()
    assert re.fullmatch(r"rb status: rb itself failed: a made-up fault \((?:\S*/)?src/cli\.rs:\d+\)", first), err
    assert second.startswith("  → ") and "report" in second and not CRASH_WORDS.search(err) and "panicked" not in err
    code, env, err = envelope(capsys, "status")
    assert code == 3 and not env.ok and err == "" and env.command == "status"
    assert env.errors[0].code == "E_INTERNAL" and "a made-up fault" in env.errors[0].message and "/report?k=crash&" in env.errors[0].fix
    monkeypatch.delenv("RB_TEST_FAULT")
    assert said(capsys, "status")[0] == 0                       # the record was not touched by it


def test_a_fault_while_rb_mcp_answers_outside_a_tool_carries_the_report_link(record):
    """A fault in rb while `rb mcp` answers a request that is no tool's (here initialize) is that request's error
    (-32603), and it says how to report it as a command's fault does: its fix line and report_url, in the error's data.
    The server goes on to the next request."""
    from rbtest import Mcp
    server = Mcp(root=record, env={**environment(), "RB_TEST_MCP_FAULT": "a made-up fault"})
    try:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "ana-client", "version": "1.0"}}})
        error = answer["error"]
        assert error["code"] == -32603 and "a made-up fault" in error["message"], answer
        assert "/report?k=crash&" in error["data"]["report_url"] and error["data"]["report_url"] in error["data"]["fix"], answer
        assert not CRASH_WORDS.search(json.dumps(answer)) and "panicked" not in json.dumps(answer)
        assert server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "ana-client", "version": "1.0"}}})["error"]["code"] == -32603
    finally:
        server.close()


def test_a_page_goes_where_it_is_asked_for_when_the_directory_is_not_there_yet(record, capsys):
    code, out, err = said(capsys, "page", "--out", "shared/week-1/page.html")
    assert code == 0 and err == "" and out.startswith("shared/week-1/page.html written")
    assert (record / "shared" / "week-1" / "page.html").read_text(encoding="utf-8").lstrip().lower().startswith("<!doctype html")


def test_the_journal_lists_what_it_can_read_and_counts_what_it_cannot(record, capsys):
    """The journal is a local buffer anything may have written into. An entry rb cannot read is passed over and
    counted, never a failure; --json gives every entry as it is."""
    entries = [
        {"at": "2026-10-01T12:00:00+00:00", "op": "file", "path": "out/a.json", "change": "new", "written": "2026-10-01T12:00:00+00:00"},
        {"at": "2026-10-01T12:01:00+00:00", "op": "file", "path": "out/b.json", "change": ["new"]},
        {"at": "yesterday", "op": "file", "path": "out/c.json", "change": "new", "written": "2026-10-01T11:00:00"},
        {"at": "2026-10-01T12:02:00+00:00", "op": "git", "commit": {"sha": "abc"}},
        {"at": 20261001, "op": "run"},
        {"at": "2026-10-01T12:03:00+00:00", "op": "run", "command": "python eval.py", "exit": 0, "secs": 1.5},
    ]
    journal = record / ".rb" / "journal"
    journal.mkdir(parents=True, exist_ok=True)                  # rb init's look keeps its journal here, outside git
    (journal / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries) + "not an entry at all\n", encoding="utf-8", newline="\n")
    code, out, err = said(capsys, "journal")
    assert code == 0 and err == ""
    assert out.splitlines()[:3] == ["  12:00  new      out/a.json", "  12:03  ran      python eval.py (exit 0, 1.5s)",
                                    "  4 entries could not be read, and are not listed: rb journal --json gives every entry as it is."]
    assert not CRASH_WORDS.search(out)
    code, env, _ = envelope(capsys, "journal")
    assert code == 0 and env.ok and env.data["entry_count"] == 6 and env.data["entries"] == entries
    (journal / "journal.jsonl").write_text(json.dumps(entries[1]) + "\n", encoding="utf-8", newline="\n")
    assert said(capsys, "journal")[1].splitlines()[0] == "  1 entry could not be read, and is not listed: rb journal --json gives every entry as it is."


def test_a_record_file_of_an_odd_shape_is_shown_with_what_is_wrong_with_it(record, capsys):
    """rb show <file> on a file that reads and is not a record: its problems, each said as a field's, and exit 1."""
    shapes = {
        "kind-list.yaml": ("rb: 1\nkind:\n  - metric\n", "kind: Input should be a valid string"),
        "kind-none.yaml": ("rb: 1\nid: x\n", "kind: Field required"),
        "kind-new.yaml": ("rb: 1\nkind: verdict\n", "kind 'verdict' is not a kind format 1 has"),
        "criteria-number.yaml": ("rb: 1\nkind: criteria\nexperiment: e1\ncriteria: 5\n", "criteria: Input should be a valid list"),
        "criteria-text.yaml": ("rb: 1\nkind: criteria\nexperiment: e1\ncriteria:\n  - just a line\n", "criterion ?: Input should be a valid dictionary"),
    }
    for name, (text, problem) in shapes.items():
        (record / name).write_text(text, encoding="utf-8", newline="\n")
        code, env, _ = envelope(capsys, "show", name)
        assert code == 1 and env.ok and env.data["valid"] is False and env.data["problems"] == [problem], (name, env.data["problems"])
        code, _, err = said(capsys, "show", name)
        assert code == 1 and err.startswith(f"rb show: {name} reads, but is not a valid ") and problem in err and not CRASH_WORDS.search(err)


def test_rb_fmt_names_each_file_it_cannot_read_and_why(record, capsys):
    loose = record / "loose"
    (loose / "dir.yaml").mkdir(parents=True)
    (loose / "bytes.yaml").write_bytes(b"a: \xff\n")
    code, out, err = said(capsys, "fmt", "loose", "loose/missing.yaml")
    assert code == 1 and err == ""
    listed = out.splitlines()
    assert listed[0] == "loose/bytes.yaml: not UTF-8 text (the byte 0xff at offset 3)" and listed[1].startswith("loose/dir.yaml: ")
    assert listed[2].startswith("loose/missing.yaml: ") and listed[3] == "3 files: 0 rewritten, 3 not Rabbit Brain YAML."
    assert not CRASH_WORDS.search(out)
    code, out, _ = said(capsys, "fmt", "loose/missing.yaml")
    assert code == 1 and out.splitlines()[-1] == "1 file: 0 rewritten, 1 not Rabbit Brain YAML."


def test_a_credentials_file_that_is_not_one_holds_no_credential_and_is_not_called_empty(record, capsys):
    """Whatever is wrong with the file rb login keeps (not text, not JSON, not the shape rb writes), no command stops
    on a fault over it: rb sync finds no credential in it (rb login writes it again), and rb logout says it cannot
    look in it, because a file rb cannot read may still hold a token. The file here is a stand-in, never a real one."""
    account = Path(os.environ["RB_CONFIG_DIR"])
    account.mkdir()
    for junk in (b"\xff\xfe not text", b"{ not json", b"[1, 2]", b'"text"', b'{"' + NOWHERE.encode() + b'": {"token": "rb_stub_made_up"},}'):
        (account / "credentials.json").write_bytes(junk)
        code, out, err = said(capsys, "sync", "--hub", NOWHERE)
        assert code == 2 and err.startswith(f"rb sync: No credential for {NOWHERE} on this machine.") and not CRASH_WORDS.search(err), err
        code, out, err = said(capsys, "logout", "--hub", NOWHERE)
        assert code == 2 and out == "" and "credentials.json is not as rb login writes it" in err and "Delete the file" in err, err
        assert "rb_stub_made_up" not in err and not CRASH_WORDS.search(err)
        assert (account / "credentials.json").read_bytes() == junk            # rb changes nothing it cannot read
    (account / "credentials.json").unlink()
    code, out, err = said(capsys, "logout", "--hub", NOWHERE)
    assert code == 0 and err == "" and out.strip() == f"Nothing stored for {NOWHERE}; nothing to do."


def test_logging_out_removes_the_credential_here_whatever_stops_the_revoke(record, capsys):
    account = Path(os.environ["RB_CONFIG_DIR"])
    account.mkdir()
    (account / "credentials.json").write_text(json.dumps({NOWHERE: {"token": "rb_stub_made_up", "handle": "t"}}), encoding="utf-8")
    code, out, err = said(capsys, "logout", "--hub", NOWHERE)
    assert code == 0 and err == "" and "credential removed" in out and "not reachable to revoke it" in out
    assert json.loads((account / "credentials.json").read_text(encoding="utf-8")) == {}


def test_an_address_set_to_nothing_is_said_not_guessed(record, capsys, monkeypatch):
    monkeypatch.setenv("RB_HUB_URL", "")
    for cmd in ("login", "logout", "sync"):
        code, env, _ = envelope(capsys, cmd)
        assert code == 2 and env.errors[0].code == "E_USAGE" and env.errors[0].message == "RB_HUB_URL is set to nothing.", cmd


def test_rb_init_starts_the_record_whatever_state_an_instruction_file_is_in(tmp_path, monkeypatch, capsys):
    """rb init installs rb's instructions for the agents here, beside what is there (an AGENTS.md that was there takes
    rb's section once the user agrees: --yes). A file it cannot add to is said and left as it is, and the record is
    started either way; markers left in a muddle get rb's section at the end."""
    as_the_user(monkeypatch, *NOT_HERE)
    monkeypatch.setenv("RB_ACTOR", "human:t")
    skill = (ROOT / "rust" / "src" / "skill.rs").read_text(encoding="utf-8")
    begin, end = (re.search(rf'const {name}: &str = "(.*)";', skill).group(1) for name in ("BEGIN", "END"))
    cases = {"bytes": b"\xff\xfe our own notes\n", "muddle": f"{end}\n{begin}\n".encode(), "plain": b"# Our agents\n\nBe kind.\n"}
    for name, content in cases.items():
        root = tmp_path / name
        root.mkdir()
        (root / "AGENTS.md").write_bytes(content)
        monkeypatch.chdir(root)
        code, out, err = said(capsys, "init", "does it hold?", "--yes")
        assert code == 0 and err == "" and (root / ".rb" / "project.yaml").is_file(), (name, out, err)
        assert not CRASH_WORDS.search(out)
        after = (root / "AGENTS.md").read_bytes()
        if name == "bytes":
            assert after == content and "nothing written" in out and "is not UTF-8 text" in out
        else:
            assert after.startswith(content.rstrip(b"\n")) and after.rstrip().endswith(end.encode()) and b"rb context" in after, name


def test_the_tour_stops_as_the_step_that_stopped_it(tmp_path, monkeypatch, capsys):
    """A tour whose step fails says the step's own failure and exits as it did: it is not a fault in rb."""
    as_the_user(monkeypatch, *NOT_HERE)
    monkeypatch.setenv("RB_ACTOR", "nobody at all")
    monkeypatch.chdir(tmp_path)
    code, out, err = said(capsys, "tour", "first")
    lines = err.splitlines()
    assert code == 2 and lines[0].startswith("rb init: RB_ACTOR is ") and lines[-2].startswith("rb tour: The tour stopped at: rb init ")
    assert lines[-1].startswith("  → ") and "report" not in err and not CRASH_WORDS.search(err)
    code, env, err = envelope(capsys, "tour", "second")
    assert code == 2 and err == "" and [e.code for e in env.errors] == ["E_ACTOR_INVALID"]
    assert env.errors[0].message.startswith("The tour stopped at `rb init ") and "RB_ACTOR is " in env.errors[0].message


@pytest.mark.skipif(sys.platform == "win32", reason="a command line on Windows is text by the time a program sees it")
def test_an_argument_that_is_not_text_is_a_usage_error(record):
    for argv, json_mode in (([b"show", b"caf\xe9.yaml"], False), ([b"status", b"--json", b"\xff"], True)):
        r = subprocess.run([*(os.fsencode(c) for c in command()), *argv], capture_output=True, env=environment(), cwd=record)
        assert r.returncode == 2, (r.returncode, r.stderr)
        if json_mode:
            env = Envelope.model_validate_json(r.stdout)
            assert not env.ok and env.errors[0].code == "E_USAGE" and env.errors[0].message.startswith("An argument is not UTF-8 text")
        else:
            err = r.stderr.decode("utf-8", "replace")
            assert err.startswith("rb: An argument is not UTF-8 text: caf") and "\n  → " in err and "panicked" not in err


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_a_screen_that_cannot_start_says_what_to_install(record):
    """rb ui in a terminal, with no Python that can draw it: the two lines, exit 3, and the envelope under --json."""
    import pty
    env = dict(environment(), RB_PYTHON=str(record / "no-such-python"), TERM="xterm")
    for argv in (["ui"], ["ui", "--json"]):
        master, slave = pty.openpty()
        proc = subprocess.Popen([*command(), *argv], stdin=slave, stdout=slave, stderr=slave, env=env, cwd=record, close_fds=True)
        os.close(slave)
        shown = b""
        try:
            while chunk := os.read(master, 4096):
                shown += chunk
        except OSError:
            pass
        os.close(master)
        text = shown.decode("utf-8", "replace").replace("\r\n", "\n")
        assert proc.wait() == 3 and "could not be started" in text and re.search(r'pip install \\?"rabbit-brain==', text) and not CRASH_WORDS.search(text), text
        if "--json" in argv:
            assert json.loads(text)["errors"][0]["code"] == "E_INTERNAL"
        else:
            assert text.startswith("rb ui: the screen is drawn by rb's Python package") and "\n  → " in text


def test_mcp_answers_a_call_it_cannot_take_in_the_protocols_own_words(record):
    """A request whose params are not what the method takes is -32602 with a sentence; a tool call whose arguments
    are not an object is the tool's own usage error. Neither is an internal error with an exception's name."""
    asked = [
        ("tools/call", [1, 2]),
        ("tools/call", {"name": ["status"]}),
        ("tools/call", {}),
        ("tools/call", {"name": "status", "arguments": [["experiment", "e1"]]}),
        ("tools/call", {"name": "status", "arguments": 5}),
        ("tools/call", {"name": "run_add", "arguments": {"experiment": "e1", "metrics": [1]}}),
        ("resources/read", {"uri": ["rb://status"]}),
        ("initialize", {"clientInfo": "not an object"}),
        ("initialize", {"clientInfo": {"name": 5}}),
    ]
    sent = "".join(json.dumps({"jsonrpc": "2.0", "id": n, "method": m, "params": p}) + "\n" for n, (m, p) in enumerate(asked, 1))
    r = subprocess.run([*command(), "mcp"], input=sent.encode("utf-8"), capture_output=True, env=environment(), cwd=record)
    replies = [json.loads(line) for line in r.stdout.decode("utf-8").splitlines()]
    assert r.returncode == 0 and [x["id"] for x in replies] == list(range(1, len(asked) + 1))
    assert not CRASH_WORDS.search(r.stdout.decode("utf-8"))
    errors = {x["id"]: x["error"] for x in replies if "error" in x}
    assert set(errors) == {1, 2, 3, 7, 8, 9} and {e["code"] for e in errors.values()} == {-32602}
    assert errors[1]["message"] == "invalid params: params must be an object"
    assert errors[8]["message"] == "invalid params: clientInfo must be an object"
    assert errors[9]["message"] == "invalid params: clientInfo.name must be text"
    for n, message in ((4, "the arguments for status must be an object of named values"), (5, "the arguments for status must be an object of named values"),
                       (6, "metrics must be an object of names and numbers")):
        result = replies[n - 1]["result"]
        assert result["isError"] and result["structuredContent"]["errors"] == [{"code": "E_USAGE", "message": message}]


def test_a_fault_while_answering_one_mcp_call_is_that_calls_answer(record, monkeypatch):
    """A tool call that hits a fault in rb is answered as a failed call, and the server answers the next one."""
    monkeypatch.setenv("RB_TEST_FAULT", "a made-up fault")
    asked = [("tools/call", {"name": "status", "arguments": {}}), ("ping", {})]
    sent = "".join(json.dumps({"jsonrpc": "2.0", "id": n, "method": m, "params": p}) + "\n" for n, (m, p) in enumerate(asked, 1))
    r = subprocess.run([*command(), "mcp"], input=sent.encode("utf-8"), capture_output=True, env=environment(), cwd=record)
    first, second = (json.loads(line) for line in r.stdout.decode("utf-8").splitlines())
    failed = first["result"]["structuredContent"]["errors"][0]
    assert r.returncode == 0 and first["result"]["isError"] and failed["code"] == "E_INTERNAL" and "a made-up fault" in failed["message"]
    assert second == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_nothing_rb_says_is_worded_as_another_languages_crash():
    """The binary's own text: no sentence in its source names an exception class or carries an error number in
    brackets. (Comments may: they say where a behaviour came from.)"""
    words = re.compile(IN_ANY_TEXT)
    found = []
    for path in sorted((ROOT / "rust" / "src").rglob("*.rs")):
        if path.name == "rbdiff.rs":                # the differential tests' helper, never shipped
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//")[0] if line.lstrip().startswith("//") or " // " in line else line
            for literal in re.findall(r'"((?:[^"\\]|\\.)*)"', code):
                if words.search(literal):
                    found.append(f"{path.relative_to(ROOT).as_posix()}:{n}: {literal}")
    assert not found, "\n".join(found)
