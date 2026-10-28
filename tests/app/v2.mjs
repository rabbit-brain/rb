// The Overview as a decision surface (OVERVIEW-V2), A's half: the queue's buttons open the inspector at the Decide
// level, j and k move through the queue and Enter opens, the Source details fold stays as it was across rb open's live
// reload, and the pointer guide's steps follow the new Overview. By real clicks and keys at 390 and 1280, light and
// dark, on tests/app/overview-v2.html served here with rb open's live module (each drawing numbered, so a reload is
// seen to have swapped the page in).
//   node tests/app/v2.mjs [--shots <folder>] [--timeout <s>, 240]
// The last line on stdout is JSON: {"ok", "checks": [{"name", "pass", "detail"}]}; exit 0, 1 on a failed check, 3 with
// no Chrome.
import { createServer } from 'node:http';
import { readFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { findChrome, launchChrome, parseArgs, progress, run, sleep } from './cdp.mjs';

const a = parseArgs(process.argv.slice(2));
const HERE = dirname(fileURLToPath(import.meta.url));
const ASSETS = join(HERE, '..', '..', 'rust', 'src', 'shell', 'assets');
const results = [];
const check = (name, pass, detail = '') => { results.push({ name, pass: !!pass, detail }); progress(name); if (!pass) process.stderr.write(`FAIL ${name}: ${detail}\n`); };
const j = x => JSON.stringify(x);

// the page, numbered by drawing; its assets from the tree; rb open's two small answers
let drawn = 0;
function serve() {
  const page = readFileSync(join(HERE, 'overview-v2.html'), 'utf8');
  const srv = createServer((req, res) => {
    const u = new URL(req.url, 'http://x');
    if (u.pathname === '/') { drawn++; res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }); res.end(page.replace('{{N}}', String(drawn))); return; }
    if (u.pathname.startsWith('/assets/')) {
      const f = u.pathname.slice(8);
      if (!/^[\w.-]+$/.test(f)) { res.writeHead(404); res.end(); return; }
      try { const b = readFileSync(join(ASSETS, f)); res.writeHead(200, { 'content-type': f.endsWith('.css') ? 'text/css' : 'text/javascript' }); res.end(b); } catch { res.writeHead(404); res.end(); }
      return;
    }
    if (u.pathname === '/topbar') {
      // rb open's top bar alone, as open.rs draws it with and without a Where picker (a record outside git has none)
      const pick = u.searchParams.get('pick') === '1' ? '<details class="pick" data-pick="where"><summary><span class="dim">Where</span> <span class="lab">main</span></summary></details>' : '';
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      res.end(`<!doctype html><html class="app"><head><link rel="stylesheet" href="/assets/open.css"></head><body><header class="top"><span class="ttl">INT8 ResNet-50 on ImageNet</span><div class="where" id="where">${pick}<span class="pl">2 record files not committed</span></div></header></body></html>`);
      return;
    }
    if (u.pathname === '/digest.json') { res.writeHead(200, { 'content-type': 'application/json' }); res.end(j({ key: 'k1' })); return; }
    res.writeHead(204); res.end();
  });
  return new Promise(r => srv.listen(0, '127.0.0.1', () => r({ url: `http://127.0.0.1:${srv.address().port}/`, close: () => srv.close() })));
}

const STATE = `(() => { const top = document.querySelector('.ibody > .card'), ae = document.activeElement;
  const d = top && top.querySelector('[data-level="decide"]'), ib = document.querySelector('aside.insp');
  const inView = el => { if (!el) return false; const r = el.getBoundingClientRect(), b = ib.getBoundingClientRect(); return r.top >= b.top - 1 && r.top < Math.min(b.bottom, innerHeight) - 20; };
  return { view: (document.querySelector('main .view.on') || {}).id || null, top: top ? top.dataset.id : null, hash: decodeURIComponent(location.hash.slice(1)),
    sheet: document.documentElement.classList.contains('sheet'), decide_in_view: inView(d),
    focus: ae ? (ae.closest('.ibody') ? 'inspector:' + ae.tagName + ':' + (ae.dataset.action || ae.className || '') : ae.tagName + ':' + (ae.getAttribute('href') || ae.id || '')) : null,
    queue_in_insp: !!document.querySelector('.ibody .queue, .ibody .qbtn'), empty: (document.querySelector('.ibody .iempty') || {}).textContent || null,
    drawing: (document.querySelector('#overview .drawn') || {}).textContent || '' }; })()`;

await run(async () => {
  const srv = await serve();
  const browser = await launchChrome({ chrome: findChrome() });
  const p = await browser.page();
  const st = () => p.ev(STATE);
  async function open(w, scheme, hash = '', { guide = false } = {}) {
    await p.blank();
    await p.viewport(w, w < 600 ? 844 : 900);
    await p.media({ type: '', scheme });
    await p.goto(srv.url + (hash ? '#' + hash : ''));
    await p.ev(guide ? `localStorage.removeItem('rb.guide.1')` : `localStorage.setItem('rb.guide.1', 'ended')`);
    await p.goto(srv.url + '#overview'); await p.goto(srv.url + (hash ? '#' + hash : ''));
    if (!(await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000))) throw new Error('never ready');
    await p.settle({ quietMs: 100 }); await sleep(350);
  }
  const shots = a.shots; if (shots) mkdirSync(shots, { recursive: true });

  for (const w of [390, 1280]) for (const scheme of ['light', 'dark']) {
    const at = `${w} ${scheme}`;
    // the Overview's inspector: rb's empty line, never a second copy of the queue
    await open(w, scheme);
    let s = await st();
    // (Design pass 1, target 8: a slim rail beside the work, its line kept for a screen reader)
    const rail = Math.round(await p.ev(`document.querySelector('aside.insp').getBoundingClientRect().width`));
    if (w >= 960) check(`v2: the Overview's inspector shows its empty line and no copy of the queue, as a slim rail [${at}]`, s.view === 'overview' && !s.top && !s.queue_in_insp && /^Pick a question/.test(s.empty || '') && rail <= 20, j({ s, rail }));
    if (shots) await p.shot(join(shots, `overview-${w}-${scheme}.png`));

    // a queue's button: its card in the inspector, at its Decide level, focus on its first action; Esc gives focus back
    await p.clickEl(`document.querySelector('#queue a.qbtn[href="#call-c2"]')`); await sleep(400);
    s = await st();
    check(`v2: a queue's button opens its call in the inspector at the Decide level, focus on its first action [${at}]`,
      s.top === 'call-c2' && s.decide_in_view && s.focus === 'inspector:BUTTON:accept' && (w >= 960 ? !s.sheet : s.sheet), j(s));
    if (shots) await p.shot(join(shots, `decide-${w}-${scheme}.png`));
    // the a key acts on that call at once
    await p.key('a', 'KeyA', 65, 'a');
    const chosen = await p.ev(`(document.querySelector('.ibody .decide') || {dataset: {}}).dataset.chosen || null`);
    check(`v2: opened at Decide, a key chooses the action at once [${at}]`, chosen === 'accept', j(chosen));
    // (the a key put focus in the reason's field: the first Esc leaves it, the second closes the card)
    await p.key('Escape', 'Escape', 27); await p.key('Escape', 'Escape', 27); await sleep(300);
    s = await st();
    check(`v2: Esc closes it and the queue's button takes focus again [${at}]`, !s.top && s.focus === 'A:#call-c2', j(s));

    // keys: j and k move through the queue's buttons and open nothing; Enter opens the one focused
    await open(w, scheme);
    await p.ev(`document.activeElement && document.activeElement.blur()`);
    await p.key('j', 'KeyJ', 74, 'j'); const k1 = await st();
    await p.key('j', 'KeyJ', 74, 'j'); const k2 = await st();
    await p.key('k', 'KeyK', 75, 'k'); const k3 = await st();
    await p.key('k', 'KeyK', 75, 'k'); const k4 = await st();
    check(`v2: j and k move focus through the queue's buttons and open nothing [${at}]`,
      k1.focus === 'A:#call-c2' && k2.focus === 'A:#call-e2' && k3.focus === 'A:#call-c2' && k4.focus === 'A:#call-c2' && [k1, k2, k3, k4].every(x => !x.top), j([k1, k2, k3, k4]));
    await p.key('j', 'KeyJ', 74, 'j');
    await p.key('Enter', 'Enter', 13, '\r'); await sleep(400);
    s = await st();
    check(`v2: Enter opens the focused item at its Decide level [${at}]`, s.top === 'call-e2' && s.decide_in_view && s.focus === 'inspector:BUTTON:continue', j(s));

    // the Source details fold, opened in the inspector, stays open across rb open's live reload; closed, stays closed
    await open(w, scheme);
    await p.clickEl(`document.querySelector('#queue a.qbtn[href="#call-c2"]')`); await sleep(400);
    await p.clickEl(`document.querySelector('.ibody details[data-fold="call-c2:source"] > summary')`); await sleep(200);
    const before = await p.ev(`[(document.querySelector('.ibody details[data-fold="call-c2:source"]') || {}).open, (document.querySelector('#overview .drawn') || {}).textContent]`);
    const swapped = await p.ev(`window.rbLive.show('')`); await sleep(400);
    const after = await p.ev(`[(document.querySelector('.ibody details[data-fold="call-c2:source"]') || {}).open, (document.querySelector('#overview .drawn') || {}).textContent, (document.querySelector('.ibody > .card') || {dataset: {}}).dataset.id]`);
    check(`v2: Source details, opened, stays open across a live reload [${at}]`, swapped === true && before[0] === true && after[0] === true && after[1] !== before[1] && after[2] === 'call-c2', j({ before, after, swapped }));
    await p.clickEl(`document.querySelector('.ibody details[data-fold="call-c2:source"] > summary')`); await sleep(200);
    await p.ev(`window.rbLive.show('')`); await sleep(400);
    const shut = await p.ev(`[(document.querySelector('.ibody details[data-fold="call-c2:source"]') || {}).open, (document.querySelector('#overview .drawn') || {}).textContent]`);
    check(`v2: Source details, closed again, stays closed across a live reload [${at}]`, shut[0] === false && shut[1] !== after[1], j(shut));

    // a live reload while the card is open draws the queue's button again: Esc still gives focus back to it
    await open(w, scheme);
    await p.clickEl(`document.querySelector('#queue a.qbtn[href="#call-c2"]')`); await sleep(400);
    await p.ev(`window.rbLive.show('')`); await sleep(400);
    await p.key('Escape', 'Escape', 27); await sleep(300);
    s = await st();
    check(`v2: after a live reload, Esc still gives focus back to the queue's button [${at}]`, !s.top && s.focus === 'A:#call-c2', j(s));

    // Your call: j opens the next call in the inspector at its Decide level, as its buttons do
    await open(w, scheme, 'your-call');
    await p.ev(`document.activeElement && document.activeElement.blur()`);
    await p.key('j', 'KeyJ', 74, 'j'); await sleep(400);
    s = await st();
    check(`v2: on Your call, j opens the next call at its Decide level [${at}]`, s.top === 'call-e2' && s.decide_in_view, j(s));

    // the guide: the standing, the integrity, the queue, the inspector, each lit and not covered by its tooltip
    await open(w, scheme, '', { guide: true });
    const steps = [];
    for (let k = 0; k < 6; k++) {
      const g = await p.ev(`(() => { const g = document.querySelector('.guide'), s = document.querySelector('.guide-spot');
        if (!g || g.hidden) return { shown: false };
        const t = g.querySelector('h2').textContent, W = document.documentElement.clientWidth, H = innerHeight;
        const sel = { 'Where the record stands': '#st-q1', 'How far to trust it': '#integrity', 'Needs your judgment': '#queue' }[t];
        const card = document.querySelector('.ibody > .card');
        const el = sel ? document.querySelector(sel) : matchMedia('(min-width: 960px)').matches ? document.querySelector('aside.insp') : card && (card.querySelector('.part[data-id$=":runs"]') || card);
        const r = el && el.getBoundingClientRect(), b = g.getBoundingClientRect(), sp = s.getBoundingClientRect();
        const c = r && { l: Math.max(r.left, 0), t: Math.max(r.top, 0), r: Math.min(r.right, W), b: Math.min(r.bottom, H) };
        const near = (x, y) => Math.abs(x - y) < 1.5;
        const lit = !!c && near(sp.left, c.l) && near(sp.top, c.t) && near(sp.right, c.r) && (near(sp.bottom, c.b) || (sp.bottom < c.b && b.top >= sp.bottom));
        const over = Math.min(b.right, sp.right) - Math.max(b.left, sp.left) > 0.5 && Math.min(b.bottom, sp.bottom) - Math.max(b.top, sp.top) > 0.5;
        const h = sel === '#integrity' && el && el.querySelector('h2');
        const heading = h ? Array.from(h.childNodes).filter(n => !(n.nodeType === 1 && n.getAttribute('aria-hidden') === 'true')).map(n => n.textContent).join('').trim() : null;
        return { shown: true, title: t, heading, lit, over, whole: b.left >= 0 && b.top >= 0 && b.right <= W + 0.5 && b.bottom <= H + 0.5, kind: card ? card.dataset.kind : null, next: g.querySelector('.gnext').textContent }; })()`);
      steps.push(g);
      if (!g.shown) break;
      if (shots) await p.shot(join(shots, `guide-${k + 1}-${w}-${scheme}.png`));
      if (g.next === 'Done') break;
      await p.clickEl(`document.querySelector('.guide .gnext')`); await sleep(700);
    }
    check(`v2: the guide's steps are the standing, the integrity, the queue and the inspector, in that order [${at}]`,
      j(steps.filter(g => g.shown).map(g => g.title)) === j(['Where the record stands', 'How far to trust it', 'Needs your judgment', 'Where it came from']), j(steps));
    check(`v2: each guide step lights its part, whole on the screen and not covered by its tooltip [${at}]`,
      steps.filter(g => g.shown).length === 4 && steps.filter(g => g.shown).every(g => g.lit && !g.over && g.whole), j(steps));
    check(`v2: the guide's integrity step is titled as the section's own heading [${at}]`,
      steps.some(g => g.heading) && steps.filter(g => g.heading).every(g => g.title === g.heading), j(steps.map(g => [g.title, g.heading])));
    check(`v2: the guide's inspector step shows a finding [${at}]`, (steps[3] || {}).kind === 'criterion', j(steps[3]));
  }

  // rb open's top bar from 1280: the place line gives way to the Where picker, and shows where there is none
  const bar = {};
  for (const pick of ['0', '1']) {
    await p.blank(); await p.viewport(1280, 900); await p.goto(srv.url + 'topbar?pick=' + pick); await sleep(200);
    bar[pick] = await p.ev(`getComputedStyle(document.querySelector('.where .pl')).display`);
  }
  check(`v2: from 1280 the top bar's place line gives way to a Where picker, and shows where there is none`, bar['0'] !== 'none' && bar['1'] === 'none', j(bar));

  // without the script, the queue's button jumps to the Decide part in the document
  await p.blank(); await p.viewport(1280, 900); await p.noScript(true);
  await p.goto(srv.url); await p.settle({ script: false });
  await p.clickEl(`document.querySelector('#queue a.qbtn[href="#call-c2"]')`);
  const jumped = await p.ev(`location.hash`);
  await p.noScript(false);
  check(`v2: without the script the queue's button jumps to the Decide part`, jumped === '#call-c2', j(jumped));

  srv.close();
  const failed = results.filter(r => !r.pass);
  if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
  return { checks: results };
}, { timeoutMs: (Number(a.timeout) || 240) * 1000, idleMs: (Number(a.idle) || 120) * 1000 });
