"""Key statements show in full (owner, Nov 13 2026: "why do i need to click just to expand hypotheses").

An experiment's hypothesis or purpose shows whole, wrapping; past three lines it fades with a "more" that shows it
whole. A list row that cuts a question gives its whole text on hover. A design that waits reads as a "Design not
confirmed" line that opens the design and a prompt for their agent, never a command to type."""
import json
import os
import re
import subprocess

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, command, environment, main

LONG = ("INT8 post-training quantization with per-channel weight scales and 1024 calibration images drawn from the "
        "training split keeps ResNet-50 top-1 on the held-out ImageNet validation set within half a point of FP32, "
        "across three seeds, at batch size 256, with the same preprocessing, the same crop, the same resize and the "
        "same evaluation harness, and with no change to the model's weights beyond their quantization")


@pytest.fixture
def rec(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    monkeypatch.chdir(root)
    subprocess.run(["git", "init", "-q"], check=True)

    def rb(*argv):
        code = main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert env.ok, (argv, env.errors)
        return env

    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("question", "add", "Does INT8 quantization cost ResNet-50 top-1 accuracy on the held-out ImageNet validation set?", "--id", "q1")
    rb("experiment", "add", "INT8 against FP32", "--id", "e1", "-q", "q1", "--hypothesis", LONG, "--baseline", "fp32", "--candidate", "int8")
    monkeypatch.setenv("CLAUDECODE", "1")
    rb("experiment", "add", "INT8 with fewer images", "--id", "e2", "-q", "q1", "--hypothesis", "256 images are enough", "--baseline", "fp32", "--candidate", "int8")
    monkeypatch.delenv("CLAUDECODE")
    out = tmp_path / "p.html"
    got = subprocess.run([*command(), "page", "--out", str(out)], cwd=root, env=environment(), capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    return out


def test_a_hypothesis_shows_whole_and_a_waiting_design_is_a_chip(rec):
    page = rec.read_text(encoding="utf-8")
    view = page[page.index('id="x-e1"'):page.index("</article>", page.index('id="x-e1"'))]
    # the hypothesis opens the experiment's record card (closed form)
    assert f'<div class="kst"><a href="#k-e1"><span class="au">{LONG}</span></a></div>' in view.replace("&#x27;", "'") and 'class="oneline"' not in page
    e2 = page[page.index('id="x-e2"'):page.index("</article>", page.index('id="x-e2"'))]
    # owner, msg 47: one plain line that opens the design, and a prompt for their agent, never a command to type
    assert ('<div class="dstate"><a class="fch fch-warn" href="#k-e2" title="Design not confirmed">This experiment&#x27;s design is not confirmed.</a>'
            '<button type="button" class="ibtn ico cpprompt rbcmd" data-act="copy-text" data-text="In this repository, use Rabbit Brain to show me the design of '
            '&quot;INT8 with fewer images&quot; that waits for my confirmation, part by part, and how I confirm it."') in e2, e2
    assert "rb confirm" not in e2 and "<kbd>c</kbd>" not in e2
    assert "list it to confirm" not in e2
    # a row that may cut the question says it whole on hover
    assert 'href="#q-q1" title="Does INT8 quantization cost ResNet-50 top-1 accuracy on the held-out ImageNet validation set?"' in page


def test_a_long_statement_fades_past_three_lines_and_more_shows_it_whole(rec, tmp_path):
    from test_shell import _cdp_script, _node, _runner
    js = _cdp_script(tmp_path / "kst.mjs", """
const out = {};
await cdp.run(async () => {
  for (const [id, width] of [['x-e1', 390], ['x-e2', 1440]]) {
    const p = await (await cdp.launchChrome()).page();
    await p.viewport(width, 900);
    await p.goto(%s);
    await p.ev("localStorage.setItem('rb.guide.1', 'ended'); true");
    await p.goto(%s + '#' + id);
    await p.waitFor("document.documentElement.classList.contains('ready')", 10000); await cdp.sleep(400);
    const probe = `(() => { const k = document.querySelector('#${id} .kst'), b = k.nextElementSibling; const lh = parseFloat(getComputedStyle(k).lineHeight);
      return { clamped: k.classList.contains('clamp') && !k.classList.contains('open'), lines: Math.round(k.getBoundingClientRect().height / lh),
        more: !!(b && b.classList.contains('kmore')) ? b.textContent : null, whole: k.scrollHeight <= k.clientHeight + 2 }; })()`;
    const got = { before: await p.ev(probe) };
    if (got.before.more) { await p.ev(`document.querySelector('#${id} .kmore').click(); true`); await cdp.sleep(200); got.after = await p.ev(probe); }
    out[id] = got;
    await cdp.stopAll();
  }
  return {};
}, { timeoutMs: 120000 });
console.log(JSON.stringify({ ok: true, out }));
""" % (json.dumps(rec.as_uri()), json.dumps(rec.as_uri())))
    code, said, err = _runner(_node(), js, env=dict(os.environ), timeout=180)
    assert code == 0 and said.get("ok"), err
    long, short = said["out"]["x-e1"], said["out"]["x-e2"]
    assert long["before"]["clamped"] and long["before"]["lines"] == 3 and long["before"]["more"] == "more", long
    assert not long["after"]["clamped"] and long["after"]["lines"] > 3 and long["after"]["more"] == "less" and long["after"]["whole"], long
    assert not short["before"]["clamped"] and short["before"]["more"] is None, short
