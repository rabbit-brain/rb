"""Settings checked and not checked (owner, Nov 13 2026: "what is provisional? no hover over?").

On the page a setting rb checked against its source reads "checked" with a green check, one it did not "not checked"
with an amber question mark; each says rb's words for that setting on hover and on keyboard focus; its source is a chip
that opens the file's card. The JSON keeps its field names and words.
"""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, command, environment, main


@pytest.fixture
def rec(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    monkeypatch.chdir(root)
    subprocess.run(["git", "init", "-q"], check=True)
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "-c", "user.email=ana@example.org", "-c", "user.name=ana", "commit", "-qm", "config"], check=True)

    def rb(*argv):
        code = main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert env.ok, (argv, env.errors)
        return env

    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 against FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e1", "lr", "--source", "configs/train.yaml#lr")
    rb("spec", "set", "e1", "calib", "512", "--source", "note:the calibration script's default")
    rb("spec", "set", "e1", "crop", "--unknown")
    return root


def _page(root, tmp_path):
    env = {**environment(), "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(tmp_path / "doc.json")}
    got = subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=env, capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    return (tmp_path / "p.html").read_text(encoding="utf-8"), json.loads((tmp_path / "doc.json").read_text(encoding="utf-8"))


def test_each_setting_says_checked_or_not_checked_with_rb_s_words(rec, tmp_path):
    page, doc = _page(rec, tmp_path)
    fold = page[page.index('data-fold="x-e1:settings-fold"'):]
    fold = fold[:fold.index("</details>")]
    summary = fold[:fold.index("</summary>")]
    assert "checked" in summary and "verified" not in summary and "provisional" not in summary, summary
    rows = {re.search(r'<td class="mono">([^<]+)</td>', r).group(1): r for r in re.findall(r"<tr>.*?</tr>", fold)}
    assert re.search(r'<span class="sst ok" tabindex="0" data-tip="configs/train.yaml says 0.1, at commit [0-9a-f]{7}">'
                     r'<span class="sm2" aria-hidden="true">✓</span>checked</span>', rows["lr"]), rows["lr"]
    assert '<a class="fch fch-use mono" href="#file-configs/train.yaml">configs/train.yaml#lr</a>' in rows["lr"]
    assert re.search(r'<span class="sst nc" tabindex="0" data-tip="a link or a note: rb cannot read it to check the value"[^>]*>'
                     r'<span class="sm2" aria-hidden="true">\?</span>not checked</span>', rows["calib"]), rows["calib"]
    # the JSON keeps its own words beside rb's words for the page
    shared = {r["name"]: r for r in doc["experiments"][0]["reproduce"]["settings"]["shared"]}
    assert shared["lr"]["status"] == "verified" and shared["calib"]["status"] == "provisional" and shared["calib"]["said"]


def test_keyboard_focus_shows_what_hover_shows(rec, tmp_path):
    from test_shell import _cdp_script, _node, _runner
    _page(rec, tmp_path)
    page = tmp_path / "p.html"
    js = _cdp_script(tmp_path / "tip.mjs", """
let got;
await cdp.run(async () => {
  const p = await (await cdp.launchChrome()).page();
  await p.viewport(1440, 900);
  await p.goto(%s);
  await p.ev("localStorage.setItem('rb.guide.1', 'ended'); true");
  await p.goto(%s + '#x-e1');
  await p.waitFor("document.documentElement.classList.contains('ready')", 10000); await cdp.sleep(300);
  got = await p.ev(`(() => { const d = document.querySelector('details[data-fold="x-e1:settings-fold"]'); d.open = true;
    const s = d.querySelector('.sst.nc'); const before = getComputedStyle(s, '::after').content; s.focus();
    return { before, after: getComputedStyle(s, '::after').content, tip: s.dataset.tip, focused: document.activeElement === s }; })()`);
  await cdp.stopAll();
  return {};
}, { timeoutMs: 90000 });
console.log(JSON.stringify({ ok: true, got }));
""" % (json.dumps(page.as_uri()), json.dumps(page.as_uri())))
    code, said, err = _runner(_node(), js, env=dict(os.environ), timeout=120)
    assert code == 0 and said.get("ok"), err
    g = said["got"]
    assert g["focused"] and g["before"] in ("none", "normal") and g["after"] == json.dumps(g["tip"]), g
