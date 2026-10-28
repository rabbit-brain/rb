"""Plain record text. rb shows the text anyone writes exactly as written, on every screen, and renders no markup. After
an agent's write whose new text holds LaTeX, rb says so in one line that names the field and what to write instead:
after the write's text, among data.warnings in --json and over MCP, and as an RBWarning from the SDK, the same line on
every surface. It never refuses or changes the text. The user's words, a quote of a file and a command as given are
kept exactly as given and never pointed at, nor is what only looks like LaTeX (a price, a Windows path, a regex, a code
span), nor anything a human writes in their own terminal.

Every name and session id here is made up."""
import json
import shutil
import subprocess
import warnings
from pathlib import Path

import pytest

import rabbit_brain
from rabbit_brain.models import Envelope
from rbtest import Mcp, as_the_user, main

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the record is kept in a git repository")

AGENT = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-plain-41c7"}
MATH = "Unicode symbols (≥, ×, Δ, σ, ∼) for math"
WORDS = "the words without their command"
CITES = "no citation keys"
REFS = "no table or figure references the record cannot follow"


def said(fields: str, *advice: str) -> str:
    """The line rb gives after a write whose new text holds LaTeX."""
    write = advice[0] if len(advice) == 1 else f"{', '.join(advice[:-1])} and {advice[-1]}"
    return f"LaTeX markup in {fields}. rb shows text exactly as written, on every screen: write plain text, with {write}."


def git(*args) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


@pytest.fixture
def rec(workdir, monkeypatch, tmp_path_factory):
    """A record started by human:writer-ana at their own terminal, with an experiment, a question and a criterion."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git("init", "-q")
    git("config", "user.email", "writer-ana@example.com")
    git("config", "user.name", "writer ana")
    Path("papers").mkdir()
    Path("papers/gain.tex").write_text("The wide layer gains $\\Delta = -2.02$ on the held-out set.\n", encoding="utf-8")
    for argv in (["init", "plain text", "--no-agents"], ["question", "add", "Is the wide layer better?", "--id", "q1"],
                 ["experiment", "add", "a wider layer", "--id", "e1", "-q", "q1", "--baseline", "base", "--candidate", "wide"],
                 ["metric", "add", "loss", "--minimize"],
                 ["criterion", "add", "wide is no worse", "-e", "e1", "-q", "q1", "--metric", "change.loss", "--at-most", "0", "--id", "c1"]):
        assert main([*argv, "--json"]) == 0
    return workdir


def write(capsys, monkeypatch, *argv, env=None) -> Envelope:
    """rb <argv> --json, as whoever `env` makes the writer; it must succeed."""
    with monkeypatch.context() as m:
        for k, v in (env or {}).items():
            m.setenv(k, v)
        capsys.readouterr()
        code = main([*argv, "--json"])
    out = Envelope.model_validate_json(capsys.readouterr().out)
    assert code == 0 and out.ok, (argv, out.errors)
    return out


def latex_lines(env: Envelope) -> list:
    return [w for w in env.data.get("warnings", []) if w.startswith("LaTeX markup")]


def test_latex_in_an_agents_new_text_gets_a_line_after_the_write(rec, capsys, monkeypatch):
    """A question, a note, a criterion's statement and interpretation, and a reason: each write whose new text holds
    LaTeX says which field holds what, and what to write instead, and keeps the text exactly as given."""
    q = write(capsys, monkeypatch, "question", "add", r"Does the gain hold at $\Delta$ 0.3?", env=AGENT)
    assert latex_lines(q) == [said(r"text ($\Delta$)", MATH)]
    assert q.data["object"]["text"] == r"Does the gain hold at $\Delta$ 0.3?"
    n = write(capsys, monkeypatch, "note", "add", r"as in \cite{writer2020}", "-e", "e1", env=AGENT)
    assert latex_lines(n) == [said(r"text (\cite{writer2020})", CITES)]
    c = write(capsys, monkeypatch, "criterion", "add", r"\emph{wide} costs at most $x^2$", "-e", "e1", "-q", "q1", "--metric", "change.loss",
              "--at-most", "0.05", "--when-met", r"it holds, see Table~\ref{tab:main}", env=AGENT)
    assert latex_lines(c) == [said(r"statement (\emph{wide}, $x^2$) and when_met (\ref{tab:main})", MATH, WORDS, REFS)]
    d = write(capsys, monkeypatch, "decide", "c1", "investigate", "--why", r"one more seed at $\sigma_1$", env=AGENT)
    assert latex_lines(d) == [said(r"why ($\sigma_1$)", MATH)]
    f = write(capsys, monkeypatch, "freeze", "e1", "-m", r"locked before $t_0$", env=AGENT)
    assert latex_lines(f) == [said(r"why ($t_0$)", MATH)]
    # in rb's text, the line comes right after the write's own lines
    with monkeypatch.context() as m:
        for k, v in AGENT.items():
            m.setenv(k, v)
        capsys.readouterr()
        assert main(["question", "add", r"Is \texttt{lr} the knob?", "--id", "q-lr"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == [r"q-lr: Is \texttt{lr} the knob?", "  " + said(r"text (\texttt{lr})", WORDS)]


def test_a_source_note_a_cited_source_and_a_comparisons_note_get_the_line_too(rec, capsys, monkeypatch):
    """A criterion's note source, the source of a cited value (on the experiment and on a variant) and a comparison's
    note given to the SDK show on every screen as written, so LaTeX in them gets the line, each named as the writer
    gave it: source, cited_source, note."""
    from rabbit_brain import RBWarning
    c = write(capsys, monkeypatch, "criterion", "add", "as the paper notes", "-e", "e1", "--metric", "change.loss", "--equals", "-2.02",
              "--tolerance", "0.01", "--source", r"note:the paper's $\eta$ table", env=AGENT)
    assert latex_lines(c) == [said(r"source ($\eta$)", MATH)]
    s = write(capsys, monkeypatch, "spec", "set", "e1", "lr", "0.2", "--no-verify", "--cited", "0.3", "--cited-source",
              r"note:the paper's $\eta_0$", env=AGENT)
    assert latex_lines(s) == [said(r"cited_source ($\eta_0$)", MATH)]
    v = write(capsys, monkeypatch, "spec", "set", "e1", "lr", "0.4", "--variant", "wide", "--no-verify", "--cited", "0.5", "--cited-source",
              r"note:wide's \emph{own} rate", env=AGENT)
    assert latex_lines(v) == [said(r"cited_source (\emph{own})", WORDS)]
    with pytest.warns(RBWarning) as caught:
        rabbit_brain.open(agent="writer-bot").experiment("e1").analysis(
            [{"subject": "wide", "reference": "base", "metric": "loss", "estimate": -0.1, "note": r"per \cite{writer2020}"}], method="paired bootstrap")
    assert said(r"note (\cite{writer2020})", CITES) in [str(w.message) for w in caught]


def test_the_users_words_quotes_and_commands_stay_as_given_and_are_never_pointed_at(rec, capsys, monkeypatch):
    """A write whose LaTeX is in the user's words, a quote of a file or a command as given says nothing of them: its
    line names only the author's own text."""
    q = write(capsys, monkeypatch, "question", "add", r"Is $x^2$ the cost?", "--words", r"is it $\Delta$ or \emph{not}", env=AGENT)
    assert latex_lines(q) == [said("text ($x^2$)", MATH)]
    c = write(capsys, monkeypatch, "criterion", "add", r"the paper's $\Delta$", "-e", "e1", "--metric", "change.loss", "--equals", "-2.02",
              "--tolerance", "0.01", "--source", "papers/gain.tex", "--quote", r"$\Delta = -2.02$", "--locator", "Table 2", env=AGENT)
    assert latex_lines(c) == [said(r"statement ($\Delta$)", MATH)]
    assert c.data["object"]["source"]["quote"] == r"$\Delta = -2.02$"
    r = write(capsys, monkeypatch, "run", "add", "e1", "base.loss=1.0", "wide.loss=0.9", "--note", r"a run at $\sigma^2$",
              "--command", r"python eval.py --tag $\Delta$ \emph{x}", env=AGENT)
    assert latex_lines(r) == [said(r"note ($\sigma^2$)", MATH)]
    words_only = write(capsys, monkeypatch, "note", "add", "kept as they said it", "-e", "e1", "--words", r"note $x_i$ \cite{k}", env=AGENT)
    assert latex_lines(words_only) == []


def test_prices_paths_regexes_and_code_spans_are_not_latex(rec, capsys, monkeypatch):
    """What only looks like LaTeX is left alone: a price, a Windows path, a regex, a shell or Make variable and a code
    span. The line names the one thing that is."""
    text = (r"it costs $5 to $10 a run; logs in C:\Users\writer\delta\run1; files match ^run_\d+$; set $RB_MODEL/$RB_SESSION; "
            r"checkpoints in ${OUTPUT_DIR}/${RUN_NAME}, ${DATA_ROOT}/train/${SPLIT}, $DATA_ROOT/${SPLIT} and ${MODEL_NAME}-${SEED}.pt; "
            r"make writes $(OUT_DIR)/$(RUN) and $(BUILD_DIR)/$@; see `$\Delta$` in the code; and \emph{this}")
    n = write(capsys, monkeypatch, "note", "add", text, "-e", "e1", env=AGENT)
    assert latex_lines(n) == [said(r"text (\emph{this})", WORDS)]
    plain = write(capsys, monkeypatch, "note", "add", r"it costs $5; the path is C:\data\alpha; the regex is \bword\b", "-e", "e1", env=AGENT)
    assert latex_lines(plain) == []


def test_a_humans_writes_get_no_line(rec, capsys, monkeypatch):
    """In the user's own terminal rb points at nothing: the same write an agent's session gets a line for."""
    for make in (lambda n: ["question", "add", rf"Does $\Delta$ hold, take {n}?"], lambda n: ["note", "add", rf"see \cite{{writer2020}}, take {n}", "-e", "e1"],
                 lambda n: ["decide", "c1", "investigate", "--why", rf"at $\sigma_1$, take {n}"]):
        human = write(capsys, monkeypatch, *make(1))
        assert latex_lines(human) == [], make(1)
        capsys.readouterr()
        assert main(make(2)) == 0
        assert "LaTeX" not in capsys.readouterr().out
        assert len(latex_lines(write(capsys, monkeypatch, *make(3), env=AGENT))) == 1, make(3)


def test_the_sdk_and_mcp_give_the_same_line(rec, capsys, monkeypatch):
    """The SDK warns with the line the command line gives (an RBWarning, at the line of code that wrote), and an MCP
    tool's answer carries it in data.warnings; a human's SDK writes get none."""
    from rabbit_brain import RBWarning
    text = r"see \cite{writer2020} for $\sigma^2$"
    cli = write(capsys, monkeypatch, "note", "add", text, "-e", "e1", env=AGENT)
    [line] = latex_lines(cli)
    assert line == said(r"text (\cite{writer2020}, $\sigma^2$)", MATH, CITES)
    with pytest.warns(RBWarning) as caught:
        rabbit_brain.open(agent="writer-bot").experiment("e1").note(text)
    assert [str(w.message) for w in caught] == [line] and caught[0].filename == __file__
    with pytest.warns(RBWarning) as caught:
        rabbit_brain.open(agent="writer-bot").add_question(text)
    assert [str(w.message) for w in caught] == [line]
    with warnings.catch_warnings(record=True) as human:
        warnings.simplefilter("always")
        rabbit_brain.open().experiment("e1").note(text)
    assert [w for w in human if issubclass(w.category, RBWarning)] == []
    server = Mcp()
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "writer-mcp", "version": "1.0"}}})
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "note_add", "arguments": {"text": text, "experiment": "e1"}}})["result"]
        assert not answer["isError"] and answer["structuredContent"]["data"]["warnings"] == [line]
        assert json.loads(answer["content"][0]["text"])["data"]["warnings"] == [line]
        quiet = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "note_add", "arguments": {"text": "plain words", "experiment": "e1", "words": text}}})["result"]
        assert not quiet["isError"] and "warnings" not in quiet["structuredContent"]["data"]
    finally:
        server.close()


def test_text_a_write_copies_is_not_the_writers_and_gets_no_line(rec, capsys, monkeypatch, tmp_path_factory):
    """A setting's note, source and cited source copied with --like, a comparison's note rb analysis add --from reads
    from the analysis's output file, and the title rb reconcile --apply takes from the repository's README, are not the
    writer's words: only what the writer gave in the same write is pointed at."""
    write(capsys, monkeypatch, "spec", "set", "e1", "lr", "0.1", "--source", r"note:the paper's $\eta$", "--note", r"as in \cite{writer2020}",
          "--cited", "0.3", "--cited-source", r"note:the paper's $\eta_0$")
    copy = write(capsys, monkeypatch, "experiment", "add", "a copy", "--id", "e2", "--like", "e1", "--hypothesis", r"it gains $x^2$", env=AGENT)
    assert latex_lines(copy) == [said("hypothesis ($x^2$)", MATH)]
    [lr] = [s for s in copy.data["object"]["settings"] if s["name"] == "lr"]
    assert (lr["note"], lr["source"]["note"], lr["cited"]["source"]["note"]) == (r"as in \cite{writer2020}", r"the paper's $\eta$", r"the paper's $\eta_0$")
    Path("boot.json").write_text(json.dumps({"comparisons": [{"subject": "wide", "reference": "base", "metric": "loss", "estimate": -0.1,
                                                              "lo": -0.2, "hi": -0.05, "note": r"per \cite{writer2020}"}]}), encoding="utf-8")
    read = write(capsys, monkeypatch, "analysis", "add", "e1", "--from", "boot.json", "--method", r"paired \emph{bootstrap}", env=AGENT)
    assert latex_lines(read) == [said(r"method (\emph{bootstrap})", WORDS)]
    assert read.data["object"]["comparisons"][0]["note"] == r"per \cite{writer2020}"
    fresh = tmp_path_factory.mktemp("fresh")
    monkeypatch.chdir(fresh)
    git("init", "-q")
    Path("README.md").write_text("# A sweep of $\\Delta$\n\nWhat the sweep found.\n", encoding="utf-8")
    started = write(capsys, monkeypatch, "reconcile", "--apply", env=AGENT)
    assert started.data["started"]["title"] == r"A sweep of $\Delta$" and latex_lines(started) == []
