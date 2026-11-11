// A page writes each term's line once (orchestrator's call, Nov 10 2026): the term's first use carries its hint, its later
// uses name the same glossary entry, and the script gives them that entry's line. With the script, every use shows
// the hint on hover; without it, the first use still shows it, and the glossary holds every line.
//   node tests/app/hints.mjs --url <address> [--timeout <s>, 120]
// The last line on stdout is JSON: {"ok", "checks": [{"name", "pass", "detail"}]}; exit 0, 1 on a failed check, 3 with
// no Chrome.
import { findChrome, launchChrome, parseArgs, progress, run, sleep } from './cdp.mjs';

const a = parseArgs(process.argv.slice(2));
if (!a.url) { process.stderr.write('give --url\n'); process.exit(2); }
const results = [];
const check = (name, pass, detail = '') => { results.push({ name, pass: !!pass, detail }); progress(name); if (!pass) process.stderr.write(`FAIL ${name}: ${detail}\n`); };
const j = x => JSON.stringify(x);

// every term by the entry it names, in document order: whether it carries a hint, and whether it is the entry's line
const TERMS = `(() => { const by = {}; document.querySelectorAll('.term[aria-describedby]').forEach(t => { const id = t.getAttribute('aria-describedby'), g = document.getElementById(id);
  if (!g) return; (by[id] = by[id] || []).push({ hint: t.hasAttribute('data-hint'), same: t.getAttribute('data-hint') === g.textContent }); }); return by; })()`;
// a use of an entry with more than one (the first, or the last), shown and marked; null where none shows
const MARK = which => `(() => { document.querySelectorAll('[data-hterm]').forEach(x => x.removeAttribute('data-hterm'));
  const by = {}; document.querySelectorAll('.term[aria-describedby]').forEach(t => { const id = t.getAttribute('aria-describedby'); if (document.getElementById(id)) (by[id] = by[id] || []).push(t); });
  const shows = t => { for (let d = t.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true; return t.checkVisibility() && t.getBoundingClientRect().width > 0; };
  for (const ts of Object.values(by)) { if (ts.length < 2) continue;
    // the first use, or the last later use the view shows (the script shows one view at a time)
    const t = ${which === 'last' ? 'ts.slice(1).reverse().find(shows)' : '(shows(ts[0]) ? ts[0] : null)'};
    if (!t) continue;
    t.scrollIntoView({ block: 'center' }); t.setAttribute('data-hterm', ''); const q = t.getClientRects()[0];
    return { word: t.textContent.trim(), id: t.getAttribute('aria-describedby'), uses: ts.length, x: q.x + Math.min(q.width / 2, 20), y: q.y + q.height / 2 }; }
  return null; })()`;
const SHOWN = `(() => { const t = document.querySelector('[data-hterm]'), g = t && document.getElementById(t.getAttribute('aria-describedby')), c = t ? getComputedStyle(t, '::after').content : 'none';
  return { content: c.slice(0, 60), shown: !!g && c !== 'none' && c.includes(g.textContent.slice(0, 20)) }; })()`;

await run(async () => {
  const b = await launchChrome({ chrome: findChrome() });
  const p = await b.page();
  for (const script of [true, false]) {
    const at = script ? 'with the script' : 'without the script';
    await p.blank(); await p.noScript(!script); await p.viewport(1280, 900);
    await p.goto(a.url + (script ? '#' + (a.view || 'overview') : ''));
    if (script && !(await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000))) throw new Error('never ready');
    if (script) await p.ev(`(() => { try { localStorage.setItem('rb.guide.1', 'ended'); } catch (e) {} const g = document.querySelector('.guide:not([hidden]) .gskip'); if (g) g.click(); return true; })()`);
    await p.settle({ quietMs: 100, script }); await sleep(200);
    const by = await p.ev(TERMS), groups = Object.values(by), many = groups.filter(g => g.length > 1);
    if (script) {
      check(`hints: every use of a term carries its entry's line [${at}]`, many.length > 0 && groups.every(g => g.every(t => t.hint && t.same)), j(by));
    } else {
      check(`hints: a term's first use carries its entry's line and its later uses none [${at}]`,
        many.length > 0 && groups.every(g => g[0].hint && g[0].same && g.slice(1).every(t => !t.hint)), j(by));
    }
    for (const which of script ? ['first', 'last'] : ['first']) {
      const got = await p.ev(MARK(which));
      if (!got) { check(`hints: a ${which} use of a repeated term shows [${at}]`, false, 'none shows'); continue; }
      await sleep(150);
      const q = await p.ev(`(() => { const r = document.querySelector('[data-hterm]').getClientRects()[0]; return [r.x + Math.min(r.width / 2, 20), r.y + r.height / 2]; })()`);
      await p.mouse(q[0], q[1]); await sleep(250);
      const s = await p.ev(SHOWN);
      check(`hints: "${got.word}", ${which === 'last' ? 'a later one' : 'the first'} of its ${got.uses} uses, shows its line on hover [${at}]`, s.shown, j({ got, s }));
      await p.mouse(2, 890); await sleep(100);
    }
  }
  await p.noScript(false);
  const failed = results.filter(r => !r.pass);
  if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
  return { checks: results };
}, { timeoutMs: (Number(a.timeout) || 120) * 1000, idleMs: (Number(a.idle) || 60) * 1000 });
