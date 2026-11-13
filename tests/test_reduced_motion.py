"""Issue #1: under prefers-reduced-motion nothing on the page moves: no transition and no animation on any element,
with a card opened from a link (which flashes the part it lands on) and the Files tree opened (its arrows turn)."""
import json
import os
import subprocess

from rbtest import command


def test_nothing_moves_under_reduced_motion(int8_demo, tmp_path):
    from test_shell import _cdp_script, _node, _runner
    page = tmp_path / "p.html"
    got = subprocess.run([*command(), "page", "--out", str(page)], cwd=int8_demo.root, env=int8_demo.env, capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    js = _cdp_script(tmp_path / "motion.mjs", """
let moving = [];
await cdp.run(async () => {
  const p = await (await cdp.launchChrome()).page();
  await p.viewport(1440, 900);
  await p.send('Emulation.setEmulatedMedia', { media: '', features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  await p.goto(%s);
  await p.ev("localStorage.setItem('rb.guide.1', 'ended'); true");
  await p.goto(%s + '#c-c1:rule');
  await p.waitFor("document.documentElement.classList.contains('ready')", 10000); await cdp.sleep(150);
  await p.ev("document.querySelector('#nav .ftog') && document.querySelector('#nav .ftog').click(); true"); await cdp.sleep(100);
  // a part a link lands on in the inspector flashes (script.js flash): as it would, mid-flash
  await p.ev("(() => { const t = document.querySelector('#insp .ibody .part, #insp .ibody .card'); if (t) t.classList.add('flash'); return !!t; })()");
  moving = await p.ev(`[...document.querySelectorAll('body *')].map(e => [e, getComputedStyle(e), getComputedStyle(e, '::before')])
    .filter(([e, c, b]) => [c, b].some(x => (x.animationName && x.animationName !== 'none') || x.transitionDuration.split(',').some(d => parseFloat(d) > 0)))
    .slice(0, 8).map(([e, c]) => e.tagName + '.' + e.className + ' ' + c.animationName + ' ' + c.transitionDuration)`);
  await cdp.stopAll();
  return {};
}, { timeoutMs: 90000 });
console.log(JSON.stringify({ ok: true, moving }));
""" % (json.dumps(page.as_uri()), json.dumps(page.as_uri())))
    code, said, err = _runner(_node(), js, env=dict(os.environ), timeout=120)
    assert code == 0 and said.get("ok"), err
    assert said["moving"] == [], said["moving"]
