"""Surfaces are projections, never authors: one vocabulary (rb schema ir) for every audience, one wording per
integrity problem, and a conformance check (tests/conformance.py) that refuses any rendered word that is not a stored
field, a number or the shipped vocabulary."""
import shutil
import subprocess

import pytest

import conformance
from rabbit_brain.models import Envelope
from rbtest import as_the_user, home, main
from test_sync import _Hub, hub  # noqa: F401 - the stub hub, for the re-sync after an upgrade

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the research state is played in a git repository")


@pytest.fixture
def rb(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "ana@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "ana"], check=True)

    def run(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    assert run("init", "one vocabulary")[0] == 0
    monkeypatch.setenv("CLAUDECODE", "1")     # the agent writes the criterion: it counts as declared, by the agent
    assert run("experiment", "add", "int8 vs fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")[0] == 0
    assert run("metric", "add", "val_loss", "--unit", "nats", "--minimize")[0] == 0
    assert run("criterion", "add", "int8 costs at most 0.05 nats", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c1")[0] == 0
    assert run("run", "add", "e1", "fp32.val_loss=5.61", "int8.val_loss=5.64")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    return run


def kinds(rb) -> dict:
    """The vocabulary rb ships, as rb prints it: every kind with its gloss, its write and its provenance."""
    code, env = rb("schema", "ir")
    assert code == 0
    return env.data["objects"]


def test_one_vocabulary_everywhere(rb):
    """The IR is one table with one set of names: no display synonyms, no second vocabulary. Every
    kind carries its plain gloss, its write, and its provenance; every integrity problem a finding can
    carry has its one phrase, so a reason can never have two wordings."""
    table = kinds(rb)
    assert set(table) == {"question", "experiment", "variant", "setting", "criterion", "run", "analysis", "comparison",
                          "result", "resource", "metric", "finding", "decision", "note", "relation"}   # no grant: nothing needs one
    for kind, row_ in table.items():
        assert row_["thing"] and row_["is"] and row_["write"] and row_["provenance"], kind
        # The gloss never renames the kind: the name in the table is the name on disk and on screen.
        assert kind.lower() == kind
    assert table["experiment"]["thing"] == "what is tested"
    assert set(conformance.REASONS) == {
        "conflict", "stale", "unknown", "rejected_setting", "inherited_without_parent", "confound", "drift",
        "edited_outside_rb", "shallow_clone", "state_newer", "cited_unchecked", "cited_source_changed", "not_comparable",
        "experiment_retracted", "retracted", "input_retracted", "input_superseded", "analysis_source_changed", "arms", "units"}


def test_the_row_is_a_projection_not_an_author(rb):
    f = home().briefs[0].experiments[0].findings[0]
    assert f.statement == "int8 costs at most 0.05 nats"   # verbatim
    r = f.row
    assert r.metric == "change.val_loss" and r.criterion == "≤ 0.05 nats"
    assert r.value == "+0.03 nats" and r.margin == "margin 0.02" and r.runs == "1 run"
    assert r.standing == f.standing == "estimate meets criterion · point only"      # the headline rb computed: the rule, then the result
    # An agent's criterion counts as declared, attributed to it in the record and to no one in its words: nothing is
    # wrong with what it rests on, so the row has no why; met on one run, with no interval, it is met on its estimate
    # alone and does not read good.
    assert r.why == "" and not r.good and f.says["timing"].startswith("declared before the evidence")
    # Conformance: every frame token in the composed line is a stored word, a number, or the shipped vocabulary.
    stored = [f.statement, r.metric, "nats", "int8", "fp32", "val_loss"]
    assert conformance.conforms(f.numbers, stored, list(kinds(rb))), f.numbers
    # Three runs give an interval, and it lies under 0.05 nats whole: the rule met reads good.
    assert rb("run", "add", "e1", "fp32.val_loss=5.60", "int8.val_loss=5.62")[0] == 0
    assert rb("run", "add", "e1", "fp32.val_loss=5.62", "int8.val_loss=5.645")[0] == 0
    f = home().briefs[0].experiments[0].findings[0]
    assert f.row.standing == "criterion met · loss" and f.row.why == "" and f.row.good
    # When something is wrong with what it rests on, the why is the problem's one shared phrase, and the rule met no
    # longer reads good.
    assert rb("spec", "set", "e1", "lr", "--unknown")[0] == 0
    f = home().briefs[0].experiments[0].findings[0]
    assert f.row.why == "integrity: " + conformance.REASONS["unknown"] and not f.row.good
    assert conformance.conforms(f.numbers, stored, list(kinds(rb))), f.numbers


def test_the_row_says_the_number_its_rule_reads_as_the_rule_sentence_does(rb):
    """3846.6046 is within 3846.6 ± 0.05, and 3847, its 4 digits, reads outside it: the row says the estimate with
    the digits the finding's rule sentence takes."""
    assert rb("criterion", "add", "the paper's number", "-e", "e1", "--metric", "int8.ppl", "--equals", "3846.6",
              "--tolerance", "0.05", "--id", "c2")[0] == 0
    assert rb("run", "add", "e1", "int8.ppl=3846.6046")[0] == 0
    [f] = [f for f in home().briefs[0].experiments[0].findings if f.criterion_id == "c2"]
    assert f.row.value == "3846.60" and f.says["rule"] == "estimate meets criterion: estimate 3846.60 within 3846.6 ± 0.05"


def test_the_conformance_gate_catches_narration(rb):
    assert not conformance.conforms("our elegant epistemic journey begins here", ["val_loss"], list(kinds(rb)))
    assert conformance.conforms("val_loss 0.03 nats · criterion ≤ 0.05 nats · margin 0.02 · 1/1 runs · untested", ["val_loss", "nats"], list(kinds(rb)))


# ---------------------------------------------------------------- the report link (notes/design/final/REPORT-LINK.md)

REPORT_KEYS = {
    "k": r"crash|feedback",
    "v": r"[0-9A-Za-z.+-]{1,32}",
    "os": r"[a-z0-9_-]{1,32}",
    "c": r"[a-z0-9.-]{1,48}",
    "at": r"dep|[A-Za-z0-9_./-]{1,96}:[0-9]{1,6}",
    "h": r"[0-9a-f]{8}",
    "e": r"E_[A-Z_]{1,32}",
}


def report_fields(url):
    """The report link's fields, each held to the spec's key and pattern."""
    import re
    from urllib.parse import parse_qsl, urlsplit
    parts = urlsplit(url)
    assert parts.path.endswith("/report"), url
    fields = dict(parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True))
    assert len(fields) == parts.query.count("&") + 1, f"a key twice: {url}"
    for k, v in fields.items():
        assert k in REPORT_KEYS, f"{k} is not a key the hub takes: {url}"
        assert re.fullmatch(REPORT_KEYS[k], v), f"{k}={v} is not as the spec says: {url}"
    return fields


def test_a_fault_in_rb_says_a_report_link_that_holds_nothing_of_the_fault_the_folder_or_the_arguments(tmp_path, monkeypatch, capsys):
    import json
    as_the_user(monkeypatch)
    monkeypatch.delenv("RB_HUB_URL", raising=False)
    folder = tmp_path / "ana-int8-resnet50-secretfolder"
    folder.mkdir()
    monkeypatch.chdir(folder)
    message = "imagenet-val sha256 0bd1f3 not in /home/ana/int8"
    monkeypatch.setenv("RB_TEST_FAULT", message)
    code = main(["show", "c1-secretarg", "--json"])
    assert code == 3
    err = json.loads(capsys.readouterr().out)["errors"][0]
    assert err["code"] == "E_INTERNAL"
    url = err["report_url"]
    assert url.startswith("https://rabbitbrain.ai/report?"), url
    assert err["fix"] == f"This is a fault in rb. Report it (the page shows what it sends before you send it): {url}"
    fields = report_fields(url)
    assert fields["k"] == "crash" and fields["c"] == "show", fields
    assert fields["at"].startswith("rust/src/") and fields["at"].split(":")[0].endswith(".rs"), fields
    import hashlib
    assert fields["h"] == hashlib.sha256(message.encode()).hexdigest()[:8]
    query = url.split("?", 1)[1]
    for part in ["imagenet", "0bd1f3", "home", "ana", "int8", "secretfolder", "secretarg", "c1", str(tmp_path)]:
        assert part not in query, f"{part} in {url}"
    # said in text, the fix line carries the same link
    assert main(["show", "c1-secretarg"]) == 3
    said = capsys.readouterr().err
    assert "  → This is a fault in rb. Report it (the page shows what it sends before you send it): https://rabbitbrain.ai/report?k=crash&" in said


def test_the_report_link_goes_to_the_hub_rb_is_configured_for_and_a_usage_error_has_none(tmp_path, monkeypatch, capsys):
    import json
    as_the_user(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RB_HUB_URL", "https://ana:pw@hub.example.org/rb/?token=1#x")
    monkeypatch.setenv("RB_TEST_FAULT", "boom")
    main(["status", "--json"])
    url = json.loads(capsys.readouterr().out)["errors"][0]["report_url"]
    assert url.startswith("https://hub.example.org/rb/report?k=crash&"), url
    monkeypatch.delenv("RB_TEST_FAULT")
    main(["status", "--nonsense", "--json"])
    err = json.loads(capsys.readouterr().out)["errors"][0]
    assert err["code"] == "E_USAGE" and "report_url" not in err


def _feedback_in_a_terminal(tmp_path, env):
    """rb feedback with a terminal for its standard input and output, and an xdg-open (a browser) that notes what it
    was asked to open: what rb printed, and what it opened."""
    import os
    import pty
    import time
    opened = tmp_path / "opened.txt"
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name in ("xdg-open", "open"):
        fake = bindir / name
        fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{opened}"\n', encoding="utf-8")
        fake.chmod(0o755)
    env = {**env, "PATH": f"{bindir}{os.pathsep}{env.get('PATH', '')}"}
    import rbtest
    master, slave = pty.openpty()
    try:
        r = subprocess.run([*rbtest.command(), "feedback", "--about", "run", "add"], cwd=tmp_path, env=env, stdin=slave, stdout=slave, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.close(slave)
    said = b""
    try:
        while True:
            chunk = os.read(master, 4096)
            if not chunk:
                break
            said += chunk
    except OSError:
        pass
    os.close(master)
    assert r.returncode == 0, r.stderr
    for _ in range(50):
        if opened.exists():
            break
        time.sleep(0.1)
    return said.decode("utf-8", "replace"), opened.read_text(encoding="utf-8") if opened.exists() else ""


@pytest.mark.skipif(not hasattr(__import__("os"), "openpty") or __import__("sys").platform != "linux", reason="a terminal of its own (a pty), and xdg-open")
def test_rb_feedback_opens_the_page_in_the_users_own_terminal_and_only_prints_it_in_an_agent_session(tmp_path, monkeypatch):
    import rbtest
    as_the_user(monkeypatch)
    monkeypatch.delenv("RB_HUB_URL", raising=False)
    env = {**rbtest.environment(), "DISPLAY": ":0"}
    said, opened = _feedback_in_a_terminal(tmp_path, env)
    url = "https://rabbitbrain.ai/report?k=feedback&v="
    assert url in said and opened.startswith(url), (said, opened)
    fields = report_fields(opened.strip())
    assert fields["k"] == "feedback" and fields["c"] == "run.add" and set(fields) == {"k", "v", "os", "c"}, fields
    (tmp_path / "opened.txt").unlink()
    said, opened = _feedback_in_a_terminal(tmp_path, {**env, "CLAUDECODE": "1"})
    assert url in said and "c=run.add" in said, said
    assert opened == "", "an agent session prints the link for the user to click, and opens nothing"
    said, opened = _feedback_in_a_terminal(tmp_path, {**env, "RB_ACTOR": "agent:ana-bot"})
    assert url in said and opened == "", "RB_ACTOR naming an agent opens nothing either"
    no_display = {k: v for k, v in env.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    said, opened = _feedback_in_a_terminal(tmp_path, no_display)
    assert url in said and opened == "", "with no display, xdg-open has no browser to hand it to"
    assert "Open it in your browser" in said


def test_rb_feedback_is_in_the_help_and_names_its_command_by_its_words_alone(tmp_path, monkeypatch, capsys):
    import json
    as_the_user(monkeypatch)
    monkeypatch.chdir(tmp_path)
    main(["--help"])
    help_text = capsys.readouterr().out
    assert "feedback" in help_text.split("Reference", 1)[1], help_text
    main(["feedback", "--help"])
    assert "the page shows what it would send before you send it; rb sends nothing" in " ".join(capsys.readouterr().out.split())
    assert main(["feedback", "--about", "show", "c1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["about"] == "show" and data["opened"] is False and "c1" not in data["report_url"]
    assert report_fields(data["report_url"])["c"] == "show"
    assert main(["feedback", "--about", "nonsense", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "E_USAGE"


def test_agents_md_says_an_agent_shows_the_report_link_and_never_opens_or_fetches_it():
    from pathlib import Path
    text = " ".join((Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8").split())
    assert "rb feedback" in text
    assert "show the link to the user, and never open or fetch it yourself" in text.lower()


def test_a_fault_under_the_sdk_says_the_report_link_too(tmp_path, monkeypatch):
    import rabbit_brain as rb
    from rabbit_brain.errors import RBError
    as_the_user(monkeypatch)
    monkeypatch.delenv("RB_HUB_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RB_TEST_FAULT", "imagenet-val at /home/ana/int8")
    with pytest.raises(RBError) as caught:
        rb.init("INT8 ResNet-50")
    err = caught.value
    assert err.code == "E_INTERNAL", err
    url = err.extra.get("report_url") or ""
    assert err.fix == f"This is a fault in rb. Report it (the page shows what it sends before you send it): {url}"
    fields = report_fields(url)
    assert fields["k"] == "crash" and "c" not in fields and fields["at"].startswith("rust/src/"), fields
    assert "imagenet" not in url and "ana" not in url


# ---------------------------------------------------------------- updates without the user, layer 2: one re-sync

# a CI job starts no re-sync (AGENTS.md, Updates): the re-sync tests stand in the user's terminal, so a CI run of the
# suite (CI, GITHUB_ACTIONS, GITHUB_RUN_ID set) clears those signs first; the agent-or-CI test sets one on purpose
CI_SIGNS = ("CI", "GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITLAB_CI", "BUILDKITE", "TF_BUILD", "JENKINS_URL")


def _not_in_ci(monkeypatch):
    for k in CI_SIGNS:
        monkeypatch.delenv(k, raising=False)


def _puts_reach(n, within=30.0):
    import time
    from test_sync import _Hub
    end = time.monotonic() + within
    while time.monotonic() < end:
        if len([p for p in _Hub.puts if p[1]]) >= n:
            return True
        time.sleep(0.2)
    return False


def test_after_an_upgrade_a_synced_project_re_syncs_once_in_the_background(hub, monkeypatch, workdir):
    _not_in_ci(monkeypatch)
    import json
    import time
    from test_sync import _Hub
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    monkeypatch.setenv("RB_TEST_VERSION", "0.6.0")
    assert hub("sync")[0] == 0
    state = json.loads((workdir / ".rb" / "cache" / "sync.json").read_text(encoding="utf-8"))
    assert state["version"] == "0.6.0" and state["with_record"] is False and state["hub"].startswith("http://127.0.0.1:")
    assert hub("status")[0] == 0
    time.sleep(2)
    assert len(_Hub.puts) == 1, "the same rb starts no sync"
    monkeypatch.setenv("RB_TEST_VERSION", "0.6.1")
    started = time.monotonic()
    assert hub("status")[0] == 0
    assert time.monotonic() - started < 20
    assert _puts_reach(2), "the first command after the upgrade starts one sync"
    assert hub("show", "c1")[0] == 0
    assert hub("status")[0] == 0
    time.sleep(3)
    assert len(_Hub.puts) == 2, "once: the next commands start none"
    for _ in range(50):
        if json.loads((workdir / ".rb" / "cache" / "sync.json").read_text(encoding="utf-8"))["version"] == "0.6.1":
            break
        time.sleep(0.1)
    assert json.loads((workdir / ".rb" / "cache" / "sync.json").read_text(encoding="utf-8"))["version"] == "0.6.1"


def test_an_older_rb_than_the_one_that_wrote_the_page_starts_no_sync(hub, monkeypatch):
    _not_in_ci(monkeypatch)
    import time
    from test_sync import _Hub
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    monkeypatch.setenv("RB_TEST_VERSION", "0.7.0")
    assert hub("sync")[0] == 0
    monkeypatch.setenv("RB_TEST_VERSION", "0.6.0")
    assert hub("status")[0] == 0
    time.sleep(2)
    assert len(_Hub.puts) == 1


def test_an_agent_session_or_ci_starts_no_re_sync_and_the_users_next_command_does(hub, monkeypatch):
    _not_in_ci(monkeypatch)
    import time
    from test_sync import _Hub
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    monkeypatch.setenv("RB_TEST_VERSION", "0.6.0")
    assert hub("sync")[0] == 0
    monkeypatch.setenv("RB_TEST_VERSION", "0.6.1")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert hub("status")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert hub("status")[0] == 0
    monkeypatch.delenv("GITHUB_ACTIONS")
    time.sleep(2)
    assert len(_Hub.puts) == 1
    assert hub("status")[0] == 0
    assert _puts_reach(2)


def test_commands_together_start_one_re_sync(hub, monkeypatch, workdir):
    _not_in_ci(monkeypatch)
    import concurrent.futures
    import os
    import subprocess as sp
    import time
    import rbtest
    from test_sync import _Hub
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    monkeypatch.setenv("RB_TEST_VERSION", "0.6.0")
    assert hub("sync")[0] == 0
    env = {**rbtest.environment(), "RB_TEST_VERSION": "0.6.1"}
    run = lambda _: sp.run([*rbtest.command(), "status", "--json"], cwd=workdir, env=env, capture_output=True, stdin=sp.DEVNULL).returncode
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        assert set(pool.map(run, range(12))) <= {0, 1}
    assert _puts_reach(2)
    time.sleep(3)
    assert len(_Hub.puts) == 2


def test_a_project_never_synced_starts_no_sync_after_an_upgrade(hub, monkeypatch):
    import time
    from test_sync import _Hub
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    monkeypatch.setenv("RB_TEST_VERSION", "0.6.1")
    assert hub("status")[0] == 0
    time.sleep(2)
    assert _Hub.puts == []


def test_the_re_sync_keeps_what_the_last_sync_sent(hub, monkeypatch):
    _not_in_ci(monkeypatch)
    from test_sync import _Hub
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    monkeypatch.setenv("RB_TEST_VERSION", "0.6.0")
    assert hub("sync", "--with-record")[0] == 0
    monkeypatch.setenv("RB_TEST_VERSION", "0.6.1")
    assert hub("status")[0] == 0
    assert _puts_reach(2)
    import time
    for _ in range(50):
        if any(rel.startswith("record/") for rel in _Hub.received.get("manifest", {}).get("files", [])) and len(_Hub.puts) == 2:
            break
        time.sleep(0.1)
    assert any(rel.startswith("record/") for rel in _Hub.received["manifest"]["files"]), "the record went with it again"


# ---------------------------------------------------------------- updates without the user, layer 3: rb itself

class _PyPI:
    """A stub of PyPI's public JSON for rabbit-brain: the version it lists, how often it was asked, and what it heard."""

    def __init__(self, version="0.7.0", status=200):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        stub = self
        self.version, self.status, self.asked, self.heard = version, status, 0, []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                stub.asked += 1
                stub.heard.append((self.path, dict(self.headers)))
                body = __import__("json").dumps({"info": {"version": stub.version}}).encode()
                self.send_response(stub.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/pypi/rabbit-brain/json"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def updates(tmp_path, monkeypatch):
    """The user's terminal with updates on: no CI, no agent, no DO_NOT_TRACK, a settings folder of its own, this rb
    pinned to 0.6.0, and the stub PyPI in place of pypi.org."""
    import os
    as_the_user(monkeypatch, "DO_NOT_TRACK", "RB_TELEMETRY", "RB_ACTOR", "RB_AUTO_UPDATE")
    for k in ("CI", "GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITLAB_CI", "BUILDKITE", "TF_BUILD", "JENKINS_URL"):
        monkeypatch.delenv(k, raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("RB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("RB_TEST_VERSION", "0.6.0")
    monkeypatch.setenv("RB_TEST_UPDATES", "1")
    pypi = _PyPI()
    monkeypatch.setenv("RB_TEST_PYPI", pypi.url)
    monkeypatch.chdir(tmp_path)
    yield pypi
    pypi.close()


def _state(tmp_path):
    import json
    p = tmp_path / "config" / "update.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _until(ok, within=20.0):
    import time
    end = time.monotonic() + within
    while time.monotonic() < end:
        if ok():
            return True
        time.sleep(0.1)
    return False


def _rb(exe, *argv, env=None):
    import os
    return subprocess.run([exe, *argv], capture_output=True, text=True, env=env or dict(os.environ), stdin=subprocess.DEVNULL, timeout=60)


def _exe():
    import rbtest
    return rbtest.command()[0]


def test_rb_installed_with_pip_says_a_newer_rb_is_out_once_a_day_and_touches_nothing(updates, tmp_path):
    exe = _exe()
    first = _rb(exe, "version")
    assert first.returncode == 0
    assert _until(lambda: _state(tmp_path).get("latest") == "0.7.0"), "the background check asked PyPI"
    assert updates.asked == 1
    path, headers = updates.heard[0]
    assert "rabbit-brain" in path and not any(k.lower() in ("cookie", "authorization") for k in headers)
    r = _rb(exe, "version")
    assert r.returncode == 0 and r.stdout.strip() == "rabbit-brain 0.6.0"
    # the check runs beside the first command, so the line comes with whichever ends after it found the release
    said = [x.stderr.strip() for x in (first, r) if x.stderr.strip()]
    assert said == ["rb 0.7.0 is out: pip install -U rabbit-brain"], said
    r = _rb(exe, "version")
    assert "is out" not in r.stderr, "at most once a day"
    assert updates.asked == 1, "at most one check a day"
    assert _state(tmp_path).get("installed") is None, "pip's environment is never upgraded"


def _uv_tool(tmp_path, upgrades_to):
    """rb installed as a uv tool: its environment with uv's receipt, rb in its bin/ (the copy that runs, and the real
    program beside it), and a uv on PATH that notes what it was asked and, given a version, puts in place an rb that
    says it, as an upgrade replaces the program."""
    import os
    import shutil
    tool = tmp_path / "uv" / "tools" / "rabbit-brain"
    (tool / "bin").mkdir(parents=True)
    (tool / "uv-receipt.toml").write_text("[tool]\nrequirements = [{ name = \"rabbit-brain\" }]\n", encoding="utf-8")
    (tool / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    real = tool / "bin" / "rb-real"
    shutil.copy2(_exe(), real)
    exe = tool / "bin" / "rb"
    shutil.copy2(_exe(), exe)
    called = tmp_path / "uv-called.txt"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    swap = ""
    if upgrades_to:
        swap = f'rm -f "{exe}"\nprintf \'#!/bin/sh\\nRB_TEST_VERSION={upgrades_to} exec "{real}" "$@"\\n\' > "{exe}"\nchmod 755 "{exe}"\n'
    (bindir / "uv").write_text(f'#!/bin/sh\nprintf "%s " "$@" >> "{called}"\n{swap}exit 0\n', encoding="utf-8")
    (bindir / "uv").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
    return str(exe), env, called


def test_rb_installed_as_a_uv_tool_upgrades_itself_through_uv_and_a_screen_says_restart(updates, tmp_path, monkeypatch):
    exe, env, called = _uv_tool(tmp_path, "0.7.0")
    r = _rb(exe, "version", env=env)
    assert r.returncode == 0 and "is out" not in r.stderr
    assert _until(lambda: _state(tmp_path).get("installed") == "0.7.0"), _state(tmp_path)
    assert called.read_text(encoding="utf-8").split() == ["tool", "upgrade", "rabbit-brain"]
    assert _state(tmp_path)["installed_in"].endswith("uv/tools/rabbit-brain")
    r = _rb(exe, "version", env=env)
    assert r.returncode == 0 and r.stdout.strip() == "rabbit-brain 0.7.0", "the next command runs the new rb"
    assert "is out" not in r.stderr, "a tool install updates itself and says no pip line"
    from rabbit_brain.tui import update_line
    project_rb = _exe()
    monkeypatch.setenv("RB_BIN", exe)
    assert update_line(tmp_path, "0.6.0") == ("✔ Update installed · Restart to update", "0.6.0"), "a screen opened before it"
    assert update_line(tmp_path, None) == ("", "0.7.0"), "a screen opened after it runs the new rb"
    monkeypatch.setenv("RB_BIN", project_rb)
    assert update_line(tmp_path, "0.6.0")[0] == "", "an rb of another environment (pip in a project) says nothing of it"


def test_an_upgrade_that_installs_nothing_newer_is_not_said_as_installed(updates, tmp_path):
    exe, env, called = _uv_tool(tmp_path, None)
    assert _rb(exe, "version", env=env).returncode == 0
    assert _until(lambda: called.exists())
    __import__("time").sleep(1)
    assert _state(tmp_path).get("latest") == "0.7.0" and _state(tmp_path).get("installed") is None


def test_a_background_rb_says_no_pip_line_that_no_one_would_see(updates, tmp_path):
    import os
    import json
    import time
    exe = _exe()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "update.json").write_text(json.dumps({"auto_update": True, "checked_at": int(time.time()), "latest": "0.7.0", "installed": None, "said_at": 0}), encoding="utf-8")
    r = _rb(exe, "version", env={**os.environ, "RB_BACKGROUND_SYNC": "1"})
    assert r.stderr == "" and _state(tmp_path)["said_at"] == 0
    assert _rb(exe, "version").stderr.strip() == "rb 0.7.0 is out: pip install -U rabbit-brain"


@pytest.mark.parametrize("where", ["agent", "ci", "do_not_track", "config_off"])
def test_no_check_runs_in_an_agent_session_in_ci_under_do_not_track_or_once_turned_off(updates, tmp_path, where):
    import os
    import time
    env = dict(os.environ)
    if where == "agent":
        env["CLAUDECODE"] = "1"
    elif where == "ci":
        env["GITHUB_ACTIONS"] = "true"
    elif where == "do_not_track":
        env["DO_NOT_TRACK"] = "1"
    else:
        assert _rb(_exe(), "config", "auto-update", "off").returncode == 0
        # the switch itself claims no check: one it started would run before its own off is written
        assert _state(tmp_path)["auto_update"] is False and _state(tmp_path)["checked_at"] == 0
    r = _rb(_exe(), "version", env=env)
    assert r.returncode == 0 and r.stderr == ""
    time.sleep(2)
    assert updates.asked == 0
    if where == "config_off":
        said = _rb(_exe(), "config", "auto-update", "--json")
        assert __import__("json").loads(said.stdout)["data"]["auto_update"] is False
        refused = _rb(_exe(), "config", "auto-update", "on", "--json", env={**os.environ, "CLAUDECODE": "1"})
        assert refused.returncode == 2 and __import__("json").loads(refused.stdout)["errors"][0]["code"] == "E_USAGE"
        assert _rb(_exe(), "config", "auto-update", "on").returncode == 0
    else:
        # the same command where updates run checks once: what kept it from checking above was where it ran
        assert _rb(_exe(), "version").returncode == 0
        assert _until(lambda: updates.asked == 1)


def test_a_failed_check_is_silent_and_changes_no_command(updates, tmp_path):
    updates.status = 500
    r = _rb(_exe(), "version")
    assert r.returncode == 0 and r.stderr == ""
    assert _until(lambda: updates.asked == 1)
    __import__("time").sleep(1)
    assert _state(tmp_path).get("latest") is None
    r = _rb(_exe(), "version")
    assert r.returncode == 0 and r.stderr == ""


def test_rbs_update_state_never_lands_inside_a_record(updates, tmp_path):
    import os
    import time
    for config in (tmp_path / "work" / ".RB" / "settings", tmp_path / "work" / "missing" / ".." / "settings"):
        env = {**os.environ, "RB_CONFIG_DIR": str(config)}
        assert _rb(_exe(), "version", env=env).returncode == 0
        refused = _rb(_exe(), "config", "auto-update", "off", "--json", env=env)
        assert refused.returncode == 2 and __import__("json").loads(refused.stdout)["errors"][0]["code"] == "E_WRITE_FAILED"
    time.sleep(1)
    assert not (tmp_path / "work").exists() and updates.asked == 0


def test_rb_auto_update_off_in_the_environment_keeps_every_check_away(updates, tmp_path):
    import os
    import time
    assert _rb(_exe(), "version", env={**os.environ, "RB_AUTO_UPDATE": "0"}).returncode == 0
    time.sleep(2)
    assert updates.asked == 0 and not (tmp_path / "config").exists()


def test_commands_together_claim_the_days_check_once(updates, tmp_path):
    import concurrent.futures
    exe = _exe()
    with concurrent.futures.ThreadPoolExecutor(12) as pool:
        codes = list(pool.map(lambda _: _rb(exe, "version").returncode, range(24)))
    assert set(codes) == {0}
    assert _until(lambda: updates.asked >= 1)
    __import__("time").sleep(2)
    assert updates.asked == 1


def test_updates_turned_off_while_pypi_answers_upgrade_nothing(updates, tmp_path):
    import threading
    exe, env, called = _uv_tool(tmp_path, "0.7.0")
    gate = threading.Event()
    arrived = []
    held = updates.httpd.RequestHandlerClass.do_GET

    def slow(handler):
        arrived.append(1)
        gate.wait(60)
        held(handler)
    updates.httpd.RequestHandlerClass.do_GET = slow
    assert _rb(exe, "version", env=env).returncode == 0
    # the check is asking PyPI, and PyPI has not answered: the request is here, held (a claim alone is not a request)
    assert _until(lambda: len(arrived) == 1, within=60)
    assert updates.asked == 0
    assert _rb(exe, "config", "auto-update", "off", env=env).returncode == 0
    gate.set()
    assert _until(lambda: _state(tmp_path).get("latest") == "0.7.0", within=60), "PyPI answered the check"
    __import__("time").sleep(2)
    assert not called.exists(), "the user's off holds, right up to the upgrade"
    assert _state(tmp_path).get("installed") is None


def test_rb_actor_naming_an_agent_starts_no_check(updates, tmp_path):
    import os
    import time
    assert _rb(_exe(), "version", env={**os.environ, "RB_ACTOR": "agent:ana-bot"}).returncode == 0
    time.sleep(2)
    assert updates.asked == 0


# ---------------------------------------------------------------- one wording, every view: rb question reword

def test_a_question_reworded_keeps_its_id_evidence_and_calls_and_the_log_keeps_both_wordings(rb, monkeypatch):
    import json
    code, env = rb("question", "add", "Does INT8 cost accuracy on ImageNet?", "--id", "q-acc")
    assert code == 0
    assert rb("experiment", "file", "e1", "q-acc")[0] == 0
    assert rb("criterion", "file", "c1", "q-acc", "--why", "the held-out loss is the accuracy we ship on")[0] == 0
    code, env = rb("question", "reword", "q-acc", "Does INT8 lose accuracy on the ImageNet validation set?",
                   "--why", "the held-out set is the validation set", "--words", "say the validation set, not ImageNet")
    assert code == 0, env.errors
    q = env.data["object"]
    assert (q["id"], q["text"]) == ("q-acc", "Does INT8 lose accuracy on the ImageNet validation set?")
    lines = [json.loads(l) for l in open(".rb/log.jsonl", encoding="utf-8")]
    last = lines[-1]
    assert (last["op"], last["kind"], last["id"]) == ("reword", "question", "q-acc")
    assert last["detail"]["before"] == "Does INT8 cost accuracy on ImageNet?"
    assert last["detail"]["text"] == "Does INT8 lose accuracy on the ImageNet validation set?"
    assert last["detail"]["why"] == "the held-out set is the validation set"
    assert last["words"] == "say the validation set, not ImageNet"
    shown = rb("show", "q-acc")[1].data["object"]
    assert shown["text"] == "Does INT8 lose accuracy on the ImageNet validation set?"
    exp = rb("show", "e1")[1].data["object"]
    assert "q-acc" in exp["questions"], "its experiments stay filed under it"
    status = rb("status")[1].data
    codes = {i["code"] for i in status["open"]}
    assert "question_link_outdated" in codes, "a criterion linked to the old wording is declared on it again"
    # the same wording again writes nothing
    n = len(open(".rb/log.jsonl", encoding="utf-8").read().splitlines())
    assert rb("question", "reword", "q-acc", "Does INT8 lose accuracy on the ImageNet validation set?", "--why", "again")[0] == 0
    assert len(open(".rb/log.jsonl", encoding="utf-8").read().splitlines()) == n


def test_a_reword_needs_new_words_and_a_reason_and_a_retracted_question_takes_none(rb):
    assert rb("question", "add", "Does INT8 cost accuracy?", "--id", "q1")[0] == 0
    code, env = rb("question", "reword", "q1", "  ", "--why", "x")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID"
    code, env = rb("question", "reword", "q1", "Does INT8 cost top-1?")
    assert code == 2 and env.errors[0].code == "E_USAGE", "--why is required"
    code, env = rb("question", "reword", "q1", "Does INT8 cost top-1?", "--why", "plainer", "--dry-run")
    assert code == 0 and env.data["dry_run"] is True
    assert rb("show", "q1")[1].data["object"]["text"] == "Does INT8 cost accuracy?", "a preview writes nothing"
    assert rb("retract", "q1", "--why", "folded into another")[0] == 0
    code, env = rb("question", "reword", "q1", "Does INT8 cost top-1?", "--why", "plainer")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID"
    code, env = rb("question", "reword", "q-none", "Does INT8 cost top-1?", "--why", "plainer")
    assert code == 2 and env.errors[0].code == "E_OBJECT_NOT_FOUND"


def test_the_sdk_rewords_a_question_as_the_command_does(rb, monkeypatch):
    import rabbit_brain
    assert rb("question", "add", "Does INT8 cost accuracy?", "--id", "q1")[0] == 0
    state = rabbit_brain.open()
    q = state.reword_question("q1", "Does INT8 cost top-1 on ImageNet?", why="the metric we report", words="say top-1")
    assert (q.id, q.text) == ("q1", "Does INT8 cost top-1 on ImageNet?")
    import json
    last = [json.loads(l) for l in open(".rb/log.jsonl", encoding="utf-8")][-1]
    assert (last["op"], last["via"], last["words"]) == ("reword", "sdk", "say top-1")
    assert rb("show", "q1")[1].data["object"]["text"] == "Does INT8 cost top-1 on ImageNet?"


def test_a_reword_makes_a_call_on_the_question_one_made_on_an_earlier_reading_even_with_nothing_linked(rb, monkeypatch, capsys):
    assert rb("question", "add", "Should we ship A?", "--id", "q2")[0] == 0
    assert rb("experiment", "file", "e1", "q2")[0] == 0
    assert rb("decide", "q2", "park", "--why", "later")[0] == 0
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("question", "reword", "q2", "Should we ship B instead?", "--why", "the plan changed")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    status = rb("status")[1].data
    outdated = [i for i in status["open"] if i["code"] == "decision_outdated"]
    assert outdated and "q2" in outdated[0]["subject"], status["open"]
    assert main(["status", "--fail-on", "undecided"]) == 1
    capsys.readouterr()
    main(["status"])
    said = capsys.readouterr().out
    assert 'parked: "Should we ship A?"' in said, "the call is said with the wording it was made on"
    assert 'parked: "Should we ship B instead?"' not in said


def test_a_rewordings_words_about_who_wrote_it_are_its_writers(rb, monkeypatch):
    assert rb("question", "add", "Does INT8 cost accuracy?", "--id", "q5")[0] == 0
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("question", "reword", "q5", "Does INT8 cost accuracy, written by Claude from session memory?", "--why", "x")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    texts = [t for i in rb("status")[1].data["open"] if i["code"] == "writer_words" for t in i.get("texts", [])]
    assert any("q5" in str(t) for t in texts), texts
    assert rb("question", "reword", "q5", "Does INT8 cost accuracy on ImageNet?", "--why", "plain")[0] == 0
    texts = [t for i in rb("status")[1].data["open"] if i["code"] == "writer_words" for t in i.get("texts", [])]
    assert not any("q5" in str(t) for t in texts), texts


def test_the_mcp_tool_rewords_a_question_as_the_command_does(rb, workdir):
    from rbtest import Mcp, environment
    assert rb("question", "add", "Does INT8 cost accuracy?", "--id", "q1")[0] == 0
    server = Mcp(root=workdir, env={**environment(), "RB_SESSION": "sess-reword"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "ana-client", "version": "1.0"}}})
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "question_reword", "arguments": {
            "question": "q1", "text": "Does INT8 cost top-1 on ImageNet?", "why": "the metric we report", "words": "say top-1"}}})["result"]
        assert not answer["isError"], answer
        assert answer["structuredContent"]["data"]["object"]["text"] == "Does INT8 cost top-1 on ImageNet?"
    finally:
        server.close()
    import json
    last = [json.loads(l) for l in open(".rb/log.jsonl", encoding="utf-8")][-1]
    assert (last["op"], last["via"], last["actor"], last["words"]) == ("reword", "mcp", "agent:ana-client", "say top-1")
