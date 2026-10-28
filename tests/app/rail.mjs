// The empty inspector's slim rail (Design pass 1, target 8) on any page the shell's script runs on, the export the hub
// serves included: at 1280 and 1100, light and dark, the Overview's inspector with nothing picked is a slim rail and the
// working view takes its width; a card opened from the Overview (else the first view with one) by a real click takes the inspector's width back, Esc
// folds it again; under 960 there is no rail and no sheet until a card opens.
//   node tests/app/rail.mjs --url <address> [--timeout <s>, 240]
// The last line on stdout is JSON: {"ok", "checks": [{"name", "pass", "detail"}]}; exit 0, 1 on a failed check, 3 with
// no Chrome.
import { findChrome, launchChrome, parseArgs, progress, run, sleep } from './cdp.mjs';

const a = parseArgs(process.argv.slice(2));
if (!a.url) { process.stderr.write('give --url\n'); process.exit(2); }
const results = [];
const check = (name, pass, detail = '') => { results.push({ name, pass: !!pass, detail }); progress(name); if (!pass) process.stderr.write(`FAIL ${name}: ${detail}\n`); };
const j = x => JSON.stringify(x);
const RAIL = `(() => { const a = document.querySelector('aside.insp'), m = document.querySelector('main'), e = document.querySelector('.ibody > .rootc');
  return { empty: !!(e && e.dataset.empty === '1'), insp: a ? Math.round(a.getBoundingClientRect().width) : null, main: Math.round(m.getBoundingClientRect().width),
    sheet: document.documentElement.classList.contains('sheet'), rail: document.documentElement.classList.contains('irail') }; })()`;
// the first link or row shown in the working view that opens a card
const CARD = `(() => { document.querySelectorAll('[data-rail-pick]').forEach(x => x.removeAttribute('data-rail-pick')); const v = document.querySelector('#main .view.on'); const l = Array.from(v.querySelectorAll('a[href^="#"], [data-for] > summary, [data-for]')).find(x => {
  const id = x.getAttribute('href') ? x.getAttribute('href').slice(1) : (x.closest('[data-for]') || {}).dataset.for; const t = id && document.getElementById(id);
  return x.checkVisibility() && t && t.closest('.card'); }); if (!l) return null; l.setAttribute('data-rail-pick', ''); l.scrollIntoView({ block: 'center' }); return l.getAttribute('href') || l.closest('[data-for]').dataset.for; })()`;

await run(async () => {
  const b = await launchChrome({ chrome: findChrome() });
  const p = await b.page();
  for (const w of [1280, 1100, 390]) for (const scheme of ['light', 'dark']) {
    const at = `${w} ${scheme}`;
    await p.blank(); await p.viewport(w, w < 600 ? 844 : 900); await p.media({ type: '', scheme });
    await p.goto(a.url + '#overview');
    if (!(await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000))) throw new Error('never ready');
    await p.ev(`(() => { try { localStorage.setItem('rb.guide.1', 'ended'); } catch (e) {} const g = document.querySelector('.guide:not([hidden]) .gskip'); if (g) g.click(); return true; })()`);
    await p.settle({ quietMs: 100 }); await sleep(250);
    const first = await p.ev(RAIL);
    if (w >= 960) check(`rail: with nothing picked the inspector is a slim rail [${at}]`, first.empty && first.rail && first.insp <= 20, j(first));
    else check(`rail: under 960 neither a rail nor a sheet with nothing picked [${at}]`, first.empty && !first.sheet && first.insp !== null, j(first));
    // a card opened from the Overview, else from the first view with an empty inspector where a link or a row opens one
    let card = await p.ev(CARD), before = first;
    const views = await p.ev(`Array.from(document.querySelectorAll('main .view')).map(v => v.id)`);
    for (const v of views) {
      if (card && before.empty) break;
      await p.ev(`location.hash = ${j('#' + v)}`); await sleep(200);
      before = await p.ev(RAIL); card = before.empty ? await p.ev(CARD) : null;
    }
    if (card) await p.clickEl(`document.querySelector('[data-rail-pick]')`);
    await sleep(300);
    const opened = await p.ev(RAIL);
    await p.key('Escape', 'Escape', 27); await sleep(300);
    const closed = await p.ev(RAIL);
    if (w >= 960) check(`rail: a card takes the inspector's width, and Esc folds it back [${at}]`,
      !!card && !opened.rail && opened.insp >= 300 && opened.main < before.main && closed.rail && closed.insp <= 20 && closed.main === before.main, j({ card, before, opened, closed }));
    else check(`rail: under 960 a card opens as the sheet, and Esc closes it [${at}]`, !!card && opened.sheet && !closed.sheet, j({ card, opened, closed }));
  }
  const failed = results.filter(r => !r.pass);
  if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
  return { checks: results };
}, { timeoutMs: (Number(a.timeout) || 240) * 1000, idleMs: (Number(a.idle) || 120) * 1000 });
