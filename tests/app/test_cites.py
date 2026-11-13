"""Every citation opens (DESIGN-UX5 section 4, round 2), by real clicks in headless Chrome (tests/app/cites.mjs): on rb
page and in rb open, over the INT8 demo, the scale record and, where RB_SAMPLE_RECORDS names them, the sample records,
the walk opens every field card the page shows and follows every link each holds. None leads nowhere."""
import os
import subprocess
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, command, environment
from test_app import _failed, harness


def samples() -> list:
    return [p for p in os.environ.get("RB_SAMPLE_RECORDS", "").split(os.pathsep) if p]


def _env() -> dict:
    env = {k: v for k, v in environment().items() if k not in AGENT_VARS and not k.startswith("RB_TEST_SHELL")}
    env.update(RB_TELEMETRY="0", DO_NOT_TRACK="1")
    return env


def _root(request, which: str, tmp_path: Path) -> Path:
    if which.startswith("sample:"):
        dest = tmp_path / "sample"
        subprocess.run(["cp", "-a", which.split(":", 1)[1], str(dest)], check=True)
        return dest
    return request.getfixturevalue(which).root


@pytest.mark.parametrize("which", ["int8_demo", "scale_record", *[f"sample:{p}" for p in samples()]])
def test_every_cite_a_page_shows_opens(which, request, tmp_path):
    root = _root(request, which, tmp_path)
    page = tmp_path / "page.html"
    r = subprocess.run([*command(), "page", "--out", str(page)], cwd=root, env=_env(), capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    said = harness(page.as_uri(), timeout=900, runner="cites.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    assert said["opened"] > 0 and said["followed"] > 0
    kinds = {c["kind"] for c in said["cards"].values() if c}
    assert {"check", "experiment"} <= kinds, kinds


@pytest.mark.parametrize("which", ["int8_demo", "scale_record", *[f"sample:{p}" for p in samples()]])
def test_every_cite_rb_open_shows_opens(which, request, tmp_path):
    root = _root(request, which, tmp_path)
    said = harness(None, "--rb", command()[0], "--record", str(root), "--most", "40", timeout=900, runner="cites.mjs", env=_env())
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    assert said["opened"] > 0 and said["started"]["rb_open_url"]


@pytest.mark.parametrize("which", ["int8_demo", "scale_record", *[f"sample:{p}" for p in samples()]])
def test_a_readers_page_shows_no_command_in_any_card(which, request, tmp_path):
    """B2 (MP-1112-1030): a field's command (rb confirm <id>) is drawn as a command, which a share link's reader's view
    holds back: on a page served with the reader meta, no field card shows one, and the card that gives one, opened,
    shows none."""
    root = _root(request, which, tmp_path)
    page = tmp_path / "page.html"
    r = subprocess.run([*command(), "page", "--out", str(page)], cwd=root, env=_env(), capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    reader = tmp_path / "reader.html"
    reader.write_text(page.read_text(encoding="utf-8").replace("<head>", '<head><meta name="rb-viewer" content="reader">', 1), encoding="utf-8")
    said = harness(None, "--cards", reader.as_uri(), timeout=180, runner="viewer.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
