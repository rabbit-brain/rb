// The app harness: the shell's script driven in headless Chrome by real clicks and keys, on a page given by address.
//   node tests/app/app.mjs --url <address> [--only <check,...>] [--shots <folder>] [--timeout <s>, 240] [--idle <s>, 120]
//   node tests/app/app.mjs --generic (--url <address> | --rb <rb> --record <checkout>) [--timeout <s>]
//   node tests/app/app.mjs --places --rb <rb> --record <checkout> [--wired]
//   node tests/app/app.mjs --shown away|past --rb <rb> --record <checkout>   (rb open drawing RB_TEST_SHELL_MODEL's document)
// --url is the page to check. Without --generic it is the DOM fixture (tests/fixtures/shell/dom-fixture.html, as a
// file: address), whose every id the checks know. With --generic the checks read the page's own markup, so they run on
// rb page's render and on rb open's (--rb and --record start rb open there, with this process's environment). Each
// check runs at 390 and 1280 CSS pixels, light and dark, then print and with the script off. Exit 0 when every check
// passes, 1 when one fails, 3 when there is no Chrome. --timeout is the whole walk's deadline; --idle stops it sooner
// when no check is said for that long, naming the last one. The last line on stdout is JSON: {"ok", "checks": [{"name",
// "pass", "detail"}], ...}. With --places it starts rb open in a checkout with a second worktree and a committed
// record, and chooses places and versions by real clicks on its pickers (--copy-only: the past Copy rule alone;
// --wired: also the scope bar and a past
// version's withheld commands, which rb open's document carries once places.rs's on_the_shell is wired into it).
import { findChrome, launchChrome, parseArgs, progress, run, sleep, startRbOpen } from './cdp.mjs';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';

const a = parseArgs(process.argv.slice(2), { flags: ['generic', 'places', 'wired', 'copy-only'] });
if (!a.url && !((a.generic || a.places || a.shown) && a.rb && a.record)) { process.stderr.write('give --url, or --generic or --places with --rb and --record\n'); process.exit(2); }
// the fixture's own checks, or with --generic only the checks any shell page answers, or with --places rb open's places
const ONLY = a.shown ? ['shown'] : a.places ? ['places'] : a.generic ? ['generic', 'print', 'noscript'] : a.only ? a.only.split(',') : null;
const WIDTHS = [390, 1280];
const SCHEMES = ['light', 'dark'];

// where the app is, read from the page: the working view, the inspector's stack as its cards' ids, its crumbs, the
// address, the sheet and what has focus
const STATE = `(() => {
  const top = document.querySelector('.ibody > .card');
  const ae = document.activeElement;
  return { hash: decodeURIComponent(location.hash.slice(1)), view: (document.querySelector('main .view.on') || {}).id || null,
    views_on: document.querySelectorAll('main .view.on').length,
    top: top ? top.dataset.id : null, enter: !!(top && top.classList.contains('enter')), ids_in_insp: document.querySelectorAll('aside.insp .ibody [id]').length, root: !!document.querySelector('.ibody > .rootc'),
    crumbs: Array.from(document.querySelectorAll('.ibar .crumbs > a, .ibar .crumbs > span:not(.sep)')).map(x => x.textContent),
    sheet: document.documentElement.classList.contains('sheet'), title: document.title,
    sel: Array.from(document.querySelectorAll('main .sel')).map(x => x.dataset.for || x.dataset.go),
    inert: !!(document.querySelector('#main') || {}).inert,
    state: history.state ? { c: history.state.c, s: history.state.s } : null,
    focus: ae ? (ae.closest('.ibody') ? 'inspector:' + ae.tagName : (ae.id || ae.tagName + (ae.closest('[data-for], [data-go]') ? ':' + (ae.closest('[data-for], [data-go]').dataset.for || ae.closest('[data-for], [data-go]').dataset.go) : ''))) : null,
    back_hidden: !!(document.querySelector('.ibar [data-act="back"]') || {}).hidden };
})()`;

// an element that runs past the screen, outside a box meant to scroll sideways (tables, code, the figure, the crumbs)
const OVERFLOW = `(() => { const W = document.documentElement.clientWidth, bad = [];
  const allowed = el => el.closest('pre, svg, table, .ibar .crumbs, .sr, .skip');
  for (const el of document.querySelectorAll('body *')) {
    if (allowed(el)) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    if (r.right > W + 0.5 || r.left < -0.5) bad.push((el.id || el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ')[0] : '')) + ' ' + Math.round(r.left) + '..' + Math.round(r.right));
  }
  return { width: W, scroll: document.documentElement.scrollWidth, bad: bad.slice(0, 8), n: bad.length }; })()`;

// a line that runs past the content box of the block holding it (a card, a row): the screen's bounds cannot see it
const INNER = `(() => { const bad = [];
  const isBlock = el => { const d = getComputedStyle(el).display; return /block|grid|flex|list-item|table-cell/.test(d) && d !== 'inline-block' && d !== 'inline-flex'; };
  for (const el of document.querySelectorAll('span, a, b, code, kbd')) {
    if (el.closest('pre, svg, .ibar, .sr, .skip, .cmdbox, table')) continue;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    let a = el.parentElement; while (a && !isBlock(a)) a = a.parentElement; if (!a) continue;
    const ar = a.getBoundingClientRect(), cs = getComputedStyle(a);
    // a box that cuts its line with an ellipsis (the top bar's project id) shows where it is cut
    if (cs.textOverflow === 'ellipsis' && cs.overflowX !== 'visible') continue;
    if (r.right > ar.right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth) + 1.5 && r.right > ar.right - 0.5) bad.push((el.className || el.tagName) + ' ' + el.textContent.slice(0, 40)); }
  return bad; })()`;

// in print, each view shown and each fold's content laid out: [views shown, views, folds shown, folds]
const PRINTED = `(() => { const vs = Array.from(document.querySelectorAll('main .view')), ds = Array.from(document.querySelectorAll('main details'));
  const shown = el => { const r = el.getBoundingClientRect(); return r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const open = d => Array.from(d.children).filter(c => c.tagName !== 'SUMMARY').every(shown);
  return [vs.filter(shown).length, vs.length, ds.filter(open).length, ds.length]; })()`;

// the pointer guide (script.js's last block): forgotten, as in a browser that never saw it; and where it stands
const GUIDE_FORGET = `(() => { try { localStorage.removeItem('rb.guide.1'); } catch (e) {} return true; })()`;
const GUIDE = `(() => {
  const g = document.querySelector('.guide'), s = document.querySelector('.guide-spot');
  const stored = (() => { try { return localStorage.getItem('rb.guide.1'); } catch (e) { return 'throws'; } })();
  if (!g) return { made: false, shown: false, stored };
  const shown = !g.hidden && getComputedStyle(g).display !== 'none';
  const r = g.getBoundingClientRect(), sr = s.getBoundingClientRect(), W = document.documentElement.clientWidth, Hh = innerHeight;
  const ae = document.activeElement, title = (g.querySelector('h2') || {}).textContent || '';
  // the part each step names, and the part its tooltip sits beside
  const vis = el => el && !el.closest('[hidden]') && el.getBoundingClientRect().width > 0 && getComputedStyle(el).visibility !== 'hidden';
  const insp = document.querySelector('aside.insp');
  const part = { 'Questions and experiments': '#nav', 'What the record says': '#main', 'Your call': '#nav a[href="#your-call"]' }[title];
  const kindOf = r => { const c = document.getElementById(r.dataset.for || r.dataset.go || (r.getAttribute('href') || '#').slice(1)); return c && c.classList.contains('card') ? c.dataset.kind : null; };
  let spotEl = part ? document.querySelector(part) : vis(insp) ? insp : Array.from(document.querySelectorAll('#main .view.on [data-for], #main .view.on [data-go], #main .view.on a[href^="#"]')).find(r => vis(r) && /^(criterion|reading|run|analysis)$/.test(kindOf(r) || ''));
  const atEl = title === 'What the record says' ? document.querySelector('#main .view.on h1') : spotEl;
  const clip = q => { const b = q.getBoundingClientRect(); return { l: Math.max(b.left, 0), t: Math.max(b.top, 0), r: Math.min(b.right, W), b: Math.min(b.bottom, Hh) }; };
  const want = spotEl ? clip(spotEl) : null, at = atEl ? clip(atEl) : null;
  const near = (x, y) => Math.abs(x - y) < 1.5;
  const rgb = c => { const m = c.match(/[\\d.]+/g).map(Number); return m.slice(0, 3); };
  const lum = c => { const v = rgb(c).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const gs = getComputedStyle(g), nx = g.querySelector('.gnext'), ns = nx && getComputedStyle(nx), ps = getComputedStyle(g.querySelector('p'));
  return { made: true, shown, title, role: g.getAttribute('role'), label: !!(g.getAttribute('aria-labelledby') && document.getElementById(g.getAttribute('aria-labelledby')).textContent),
    focus_in: g.contains(ae), focus: ae ? (ae.id || ae.tagName + (ae.className ? '.' + String(ae.className).split(' ')[0] : '')) : null,
    whole: r.left >= 0 && r.top >= 0 && r.right <= W + 0.5 && r.bottom <= Hh + 0.5, box: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)],
    beside: at ? (r.top >= at.b - 0.5 || r.bottom <= at.t + 0.5) : null,
    lit: !!want && near(sr.left, want.l) && near(sr.top, want.t) && near(sr.right, want.r) && near(sr.bottom, want.b),
    dots: g.querySelectorAll('.gdots li').length, at_dot: Array.from(g.querySelectorAll('.gdots li')).findIndex(d => d.getAttribute('aria-current') === 'step'),
    back_hidden: g.querySelector('.gback').hidden, next: nx ? nx.textContent : null,
    fixed: gs.position === 'fixed' && getComputedStyle(s).position === 'fixed' && getComputedStyle(s).pointerEvents === 'none',
    contrast: [ratio(ps.color, gs.backgroundColor), ns ? ratio(ns.color, ns.backgroundColor) : 0],
    motion: [getComputedStyle(s).transitionDuration, gs.transitionDuration, gs.animationName, getComputedStyle(s).animationName],
    stored };
})()`;
// the guide's step as it stands: the part its words name at this width, whether the spotlight is on it, and whether the
// tooltip covers it or the working view's title and summary (Polish 1: never); the inspector's top card; the dimming
const GUIDE_STEP = `(() => {
  const g = document.querySelector('.guide'), s = document.querySelector('.guide-spot');
  if (!g || g.hidden) return { shown: false, hash: location.hash, top: (document.querySelector('.ibody > .card') || { dataset: {} }).dataset.id || null };
  const W = document.documentElement.clientWidth, Hh = innerHeight, wide = matchMedia('(min-width: 960px)').matches;
  const title = g.querySelector('h2').textContent, card = document.querySelector('.ibody > .card');
  const v = document.querySelector('#main .view.on'), h1 = v && v.querySelector('h1');
  const rectOf = e => { const b = e.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom }; };
  // the title and the summary lines under it
  let head = null;
  if (h1) { let last = h1; for (let n = h1.nextElementSibling; n && n.tagName === 'P' && n.getBoundingClientRect().height > 0; n = n.nextElementSibling) last = n; const a = rectOf(h1), b = rectOf(last); head = { l: Math.min(a.l, b.l), t: a.t, r: Math.max(a.r, b.r), b: b.b }; }
  const part = {
    'Questions and experiments': () => { const st = v.querySelector('nav.strip.ov'); return rectOf(!wide && st && st.getBoundingClientRect().height > 0 ? st : document.querySelector('#nav')); },
    'What the record says': () => wide ? rectOf(document.querySelector('#main')) : head,
    'Where it came from': () => wide ? rectOf(document.querySelector('aside.insp')) : card && rectOf(card.querySelector('.part[data-id$=":runs"]') || card),
    'Your call': () => rectOf(document.querySelector('#nav a[href="#your-call"]')),
    // the Overview v2's parts: the standing cards as one, the integrity section, the judgment queue's section
    'Where the record stands': () => { const w = v.querySelector('#standing'); if (w) return rectOf(w); const all = Array.from(v.querySelectorAll('section.stand')).filter(e => e.getBoundingClientRect().height > 0); if (!all.length) return null; const a = rectOf(all[0]), b = rectOf(all[all.length - 1]); return { l: Math.min(a.l, b.l), t: a.t, r: Math.max(a.r, b.r), b: b.b }; },
    'How far to trust it': () => { const e = v.querySelector('#integrity'); return e && rectOf(e); },
    'Needs your judgment': () => { const q = v.querySelector('#queue'); if (!q) return null; const s = q.closest('section'); return rectOf(s && s !== v ? s : q); },
  }[title];
  const clip = q => q && { l: Math.max(q.l, 0), t: Math.max(q.t, 0), r: Math.min(q.r, W), b: Math.min(q.b, Hh) };
  const want = clip(part && part()), box = rectOf(g), sp = rectOf(s);
  const over = (a, b) => !!(a && b) && Math.min(a.r, b.r) - Math.max(a.l, b.l) > 0.5 && Math.min(a.b, b.b) - Math.max(a.t, b.t) > 0.5;
  const near = (x, y) => Math.abs(x - y) < 1.5;
  const sheet = document.documentElement.classList.contains('sheet');
  const hd = sheet ? null : clip(head);
  // the dimming: how much darker it makes the page's text (its contrast against the same text undimmed)
  const sh = getComputedStyle(s).boxShadow, al = +((sh.match(/rgba\\([^)]*,\\s*([\\d.]+)\\)/) || [0, 0])[1]);
  const ink = getComputedStyle(document.body).color.match(/[\\d.]+/g).map(Number).slice(0, 3);
  const lum = c => { const x = c.map(u => { u /= 255; return u <= 0.03928 ? u / 12.92 : Math.pow((u + 0.055) / 1.055, 2.4); }); return 0.2126 * x[0] + 0.7152 * x[1] + 0.0722 * x[2]; };
  const dimmed = (lum(ink) + 0.05) / (lum(ink.map(u => u * (1 - al))) + 0.05);
  // lit: the part as it shows, or, where it is taller than the screen, its top down to the tooltip under it
  return { shown: true, title, lit: !!want && near(sp.l, want.l) && near(sp.t, want.t) && near(sp.r, want.r) && (near(sp.b, want.b) || (sp.b < want.b && sp.b - sp.t >= 48 && box.t >= sp.b)),
    covers_part: over(box, sp), covers_head: !!hd && hd.b > hd.t && over(box, hd), whole: box.l >= 0 && box.t >= 0 && box.r <= W + 0.5 && box.b <= Hh + 0.5,
    top: card ? card.dataset.id : null, kind: card ? card.dataset.kind : null, sheet, hash: location.hash, dimmed: Math.round(dimmed * 100) / 100,
    focus_in: g.contains(document.activeElement) };
})()`;
// the guide as it stands, its spotlight read by what each step names at this width
const GUIDE_ALL = `Object.assign(${GUIDE}, (s => ({ lit: s.lit, beside: s.shown ? !s.covers_part : null }))(${GUIDE_STEP}))`;
// where the working parts stand: the guide moves none of them
const LAYOUT = `(() => ['#nav', '#main', 'aside.insp', '#main .view.on h1'].map(q => { const e = document.querySelector(q); if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; }).concat([document.documentElement.scrollWidth, document.documentElement.scrollHeight]))()`;

const results = [], T0 = Date.now();
const check = (name, pass, detail = '') => { results.push({ name, pass: !!pass, detail, ms: Date.now() - T0 }); progress(name); if (!pass) process.stderr.write(`FAIL ${name}: ${detail}\n`); };
const want = name => !ONLY || ONLY.some(o => name.startsWith(o));
const j = x => JSON.stringify(x);

await run(async () => {
  if (!a.url) { const o = await startRbOpen({ rb: a.rb, cwd: a.record }); a.url = o.url + '/'; }
  const browser = await launchChrome({ chrome: findChrome() });
  const p = await browser.page();
  const st = () => p.ev(STATE);
  const sel = css => `document.querySelector(${j(css)})`;
  // `first`: as on a first visit to the page, the pointer guide not yet seen in this browser; otherwise as on a later
  // visit, so a check that is not about the guide finds the page as it starts once the guide has been seen
  async function open(w, scheme, hash = '', { first = false } = {}) {
    await p.blank();
    await p.viewport(w, w < 600 ? 844 : 900);
    await p.media({ type: '', scheme });
    const url = a.url + (hash ? '#' + hash : '');
    const ready = async () => { if (!(await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000))) throw new Error(`${url} never became ready`); };
    // (the guide starts a frame after the app: settled, it has shown or never will)
    if (first) { await p.goto(a.url + '#overview'); await ready(); await p.settle({ quietMs: 50 }); await p.ev(GUIDE_FORGET); await p.blank(); }
    await p.goto(url);
    await ready();
    await p.settle({ quietMs: 50 });
    if (!first && await p.ev(`!!document.querySelector('.guide:not([hidden])')`)) {
      await p.ev(`localStorage.setItem('rb.guide.1', 'ended')`);
      await p.blank(); await p.goto(url); await ready();
    }
    await p.settle({ quietMs: 100 });
    // a sheet slides in over 0.18 s: measure it once it is in
    await sleep(350);
  }
  // the guide's four steps by its buttons (Polish 1): each lights the part its words name, whole on the screen and over
  // neither that part nor the view's title and summary; step 3 opens a finding, which the next step closes; closed by
  // the user, it takes the guide a step on; in dark the dimming reads as dimming
  async function walkGuide(w, scheme, prefix, h) {
    const at = `${w}${h ? 'x' + h : ''} ${scheme}`;
    await open(w, scheme, '', { first: true });
    if (h) { await p.viewport(w, h); await sleep(700); }
    const steps = [];
    for (let k = 0; k < 4; k++) {
      const g = await p.ev(GUIDE_STEP);
      steps.push(g);
      if (!g.shown) break;
      // (the spotlight glides for 0.16 s once a sheet has slid in); the last step's Done is left for later
      if (await p.ev(`document.querySelector('.guide .gnext').textContent`) === 'Done') break;
      await p.clickEl(`document.querySelector('.guide .gnext')`); await sleep(700);
    }
    const shown = steps.filter(g => g.shown);
    // on the Overview v2 (a standing card drawn): where each question stands, what weakens the evidence where rb draws
    // it, what needs the user's judgment, then the inspector, which is the last step
    const v2 = await p.ev(`!!document.querySelector('#overview section.stand')`);
    if (v2) {
      const titles = shown.map(g => g.title), integ = await p.ev(`!!document.querySelector('#overview #integrity')`);
      check(`${prefix}guide: the Overview v2's steps, in order, the inspector last [${at}]`,
        j(titles) === j(['Where the record stands', ...(integ ? ['How far to trust it'] : []), 'Needs your judgment', 'Where it came from']), j(titles));
    }
    check(`${prefix}guide: each step lights its part, whole on the screen, over neither it nor the view's title and summary [${at}]`,
      shown.length >= 2 && shown.every(g => g.lit && g.whole && !g.covers_part && !g.covers_head), j(steps));
    // step 3 is there while the record holds a finding; Your call while a call waits
    const k3 = steps.findIndex(g => g.title === 'Where it came from');
    if (k3 < 0) return;
    check(`${prefix}guide: step 3 opens a finding in the inspector${w < 960 ? ', a sheet the guide stays over' : ''} [${at}]`,
      steps[k3].kind === 'criterion' && steps[k3].sheet === (w < 960) && steps[k3].focus_in, j(steps[k3]));
    // the next step, or, where step 3 is the last, Done
    if (!steps[k3 + 1]) { await p.clickEl(`document.querySelector('.guide .gnext')`); await sleep(700); steps.push(await p.ev(GUIDE_STEP)); }
    const after = steps[k3 + 1];
    check(`${prefix}guide: the finding it opened is closed on the next step [${at}]`, !!after && !after.top && /^#overview\b/.test(after.hash) && !after.sheet && (!after.shown || after.focus_in), j(after));
    if (scheme === 'dark') check(`${prefix}guide: in dark its dimming makes the page's text at least 3 times darker [${at}]`, steps[0].dimmed >= 3, j(steps[0].dimmed));
    if (!steps[k3 + 1] || !steps[k3 + 1].shown) return;
    await p.clickEl(`document.querySelector('.guide .gback')`); await sleep(700);
    const again = await p.ev(GUIDE_STEP);
    await p.clickEl(`document.querySelector('.ibar [data-act="close"]')`); await sleep(700);
    const on = await p.ev(GUIDE_STEP);
    check(`${prefix}guide: Back to step 3 opens the finding again; closed by the user, the guide goes a step on [${at}]`,
      again.title === 'Where it came from' && again.kind === 'criterion' && on.title === steps[k3 + 1].title && !on.top && !on.sheet, j({ again, on }));
    await p.key('Escape', 'Escape', 27);
  }
  const shot = async name => { if (a.shots) { mkdirSync(a.shots, { recursive: true }); await p.shot(join(a.shots, name + '.png')); } };

  for (const w of WIDTHS) for (const scheme of SCHEMES) {
    const at = `${w} ${scheme}`;
    // the start: the app's classes, one view shown, the inspector at its root
    if (want('start')) {
      await open(w, scheme);
      const cls = await p.ev('document.documentElement.className');
      const s = await st();
      check(`start: html.app and ready, one view, the root [${at}]`, /\bapp\b/.test(cls) && /\bready\b/.test(cls) && s.views_on === 1 && s.view === 'overview' && s.root && !s.top && s.back_hidden, j({ cls, s }));
      const o = await p.ev(OVERFLOW);
      check(`nothing past the screen on the Overview [${at}]`, o.n === 0 && o.scroll <= o.width, j(o));
      if (w < 960) {
        // every view at 390: nothing past the screen, and no line past the card or row that holds it
        const views = await p.ev(`Array.from(document.querySelectorAll('main .view')).map(v => v.id)`);
        const past = [], inner = [];
        for (const v of views) {
          await p.ev(`location.hash = ${j('#' + v)}`); await sleep(150);
          const ov = await p.ev(OVERFLOW); if (ov.n) past.push(v + ': ' + ov.bad.join(', '));
          const iv = await p.ev(INNER); if (iv.length) inner.push(v + ': ' + iv.slice(0, 3).join(', '));
        }
        check(`nothing past the screen in any of ${views.length} views [${at}]`, past.length === 0, j(past));
        check(`no line runs past its card or row in any view [${at}]`, inner.length === 0, j(inner));
      }
      await shot(`overview-${w}-${scheme}`);
    }
    // the pointer guide: on a first visit, a few steps lit one by one, in a dialog that takes focus and gives it back
    if (want('guide')) {
      const TITLES = ['Questions and experiments', 'What the record says', 'Where it came from', 'Your call'];
      const ready = () => p.waitFor(`document.documentElement.classList.contains('ready')`, 10000);
      await open(w, scheme, '', { first: true });
      let g = await p.ev(GUIDE_ALL);
      check(`guide: a first load shows it on the navigator, a labelled dialog with focus in it [${at}]`,
        g.shown && g.role === 'dialog' && g.label && g.focus_in && g.title === TITLES[0] && g.lit && g.whole && g.dots === 4 && g.at_dot === 0 && g.back_hidden && g.fixed && g.stored === 'ended', j(g));
      const laidWith = await p.ev(LAYOUT);
      // reduced motion: nothing moves; otherwise the spotlight glides
      await p.send('Emulation.setEmulatedMedia', { media: '', features: [{ name: 'prefers-color-scheme', value: scheme }, { name: 'prefers-reduced-motion', value: 'reduce' }] });
      const still = (await p.ev(GUIDE_ALL)).motion || ['none shown'];
      await p.media({ type: '', scheme });
      const moving = (await p.ev(GUIDE_ALL)).motion || ['0s'];
      check(`guide: no motion under prefers-reduced-motion [${at}]`, still.every(m => m === '0s' || m === 'none') && moving[0] !== '0s', j({ still, moving }));
      // the page behind it takes a click
      await p.clickEl(sel('#nav a[href="#runs"]'));
      let s = await st();
      g = await p.ev(GUIDE_ALL);
      check(`guide: the page behind it stays usable [${at}]`, s.view === 'runs' && g.shown, j({ s, g }));
      const steps = [];
      for (let k = 0; k < 4; k++) {
        g = await p.ev(GUIDE_ALL);
        const o = await p.ev(OVERFLOW);
        steps.push({ title: g.title, whole: g.whole, beside: g.beside, lit: g.lit, at_dot: g.at_dot, back: g.back_hidden, next: g.next, over: o.n, fits: o.scroll <= o.width, contrast: g.contrast, box: g.box });
        if (k < 3) { await p.clickEl(sel('.guide .gnext')); await sleep(700); }
      }
      check(`guide: four steps in order, each lit and whole on the screen [${at}]`, j(steps.map(x => x.title)) === j(TITLES) && steps.every((x, k) => x.whole && x.lit && x.at_dot === k && x.back === (k === 0)) && steps[3].next === 'Done', j(steps));
      check(`guide: nothing past the screen at any step [${at}]`, steps.every(x => x.over === 0 && x.fits), j(steps));
      if (w < 960) check(`guide: at 390 each tooltip sits below or above its part [${at}]`, steps.every(x => x.beside), j(steps));
      check(`guide: its words and its Next read at AA contrast [${at}]`, steps.every(x => x.contrast[0] >= 4.5 && x.contrast[1] >= 4.5), j(steps.map(x => x.contrast)));
      await p.clickEl(sel('.guide .gback'));
      g = await p.ev(GUIDE_ALL);
      check(`guide: Back goes one step back [${at}]`, g.title === TITLES[2] && g.at_dot === 2 && g.focus_in, j(g));
      await p.clickEl(sel('.guide .gnext'));
      await p.clickEl(sel('.guide .gnext'));
      g = await p.ev(GUIDE_ALL);
      check(`guide: Done on the last step ends it, kept as ended [${at}]`, !g.shown && !g.focus_in && g.stored === 'ended', j(g));
      // the second load: never again (loaded as it is, not through open, which would hide it)
      await p.blank(); await p.goto(a.url); await ready(); await p.settle({ quietMs: 100 });
      g = await p.ev(GUIDE_ALL);
      const laidWithout = await p.ev(LAYOUT);
      check(`guide: not on the second load [${at}]`, !g.made, j(g));
      check(`guide: it moves no layout [${at}]`, j(laidWith) === j(laidWithout), j({ laidWith, laidWithout }));
      // Esc ends it, and only it: a card opened beside it stays; under 960 a card opens as a sheet over the screen, and
      // the guide waits behind it until it closes
      await open(w, scheme, '', { first: true });
      // (by the address: a click on the row could land on the tooltip)
      await p.ev(`location.hash = '#run-runs1'`); await sleep(400);
      const before = await st();
      const behind = await p.ev(GUIDE_ALL);
      if (w >= 960) {
        await p.clickEl(sel('.guide .gnext'));
        const stepped = (await p.ev(GUIDE_ALL)).at_dot;
        await p.key('Escape', 'Escape', 27);
        g = await p.ev(GUIDE_ALL);
        s = await st();
        check(`guide: Esc ends it and leaves the inspector as it was [${at}]`, before.top === 'run-runs1' && behind.shown && stepped === 1 && !g.shown && g.stored === 'ended' && s.top === 'run-runs1', j({ before, behind, stepped, g, s }));
      } else {
        await p.key('Escape', 'Escape', 27);
        const closed = await st();
        const after = await p.ev(GUIDE_ALL);
        check(`guide: a sheet over it hides it, and Esc closes the sheet first [${at}]`, before.top === 'run-runs1' && before.sheet && !behind.shown && !closed.top && !closed.sheet && after.shown && after.at_dot === 0, j({ before, behind, closed, after }));
        await p.key('Escape', 'Escape', 27);
        g = await p.ev(GUIDE_ALL);
        s = await st();
        check(`guide: Esc ends it and leaves the inspector as it was [${at}]`, !g.shown && g.stored === 'ended' && !s.top && s.view === closed.view, j({ g, s }));
      }
      // the keys list opened over it: above it, and its Esc closes the list alone
      await open(w, scheme, '', { first: true });
      await p.key('?', 'Slash', 191, '?');
      // (stacked above the spotlight's dimming and the tooltip, which take no pointer and so hide from elementFromPoint)
      const over = await p.ev(`(() => { const h = document.querySelector('.help'), z = e => +getComputedStyle(e).zIndex || 0; return { open: !h.hidden, top: z(h) > z(document.querySelector('.guide-spot')) && z(h) > z(document.querySelector('.guide')), focus: h.contains(document.activeElement) }; })()`);
      await p.key('Escape', 'Escape', 27);
      const helpEsc = await p.ev(`document.querySelector('.help').hidden`);
      g = await p.ev(GUIDE_ALL);
      check(`guide: the keys list opens over it, and its Esc closes the list alone [${at}]`, over.open && over.top && over.focus && helpEsc && g.shown, j({ over, helpEsc, g }));
      if (w >= 960) {
        // scrolled, the tooltip stays whole on the screen
        await p.clickEl(sel('.guide .gnext'));
        await p.ev(`(document.scrollingElement.scrollTop = 600, document.querySelector('#main').scrollTop = 600, true)`); await sleep(400);
        g = await p.ev(GUIDE_ALL);
        check(`guide: scrolled, the tooltip stays whole on the screen [${at}]`, g.shown && g.whole && g.lit, j(g));
        // across 960, the spotlight follows the part as it is drawn now
        await p.clickEl(sel('.guide .gnext'));
        await p.viewport(800, 900); await sleep(700);
        const narrow = await p.ev(GUIDE_ALL);
        await p.viewport(w, 900); await sleep(700);
        const wide = await p.ev(GUIDE_ALL);
        check(`guide: resized across 960, the spotlight follows its part [${at}]`, narrow.shown && narrow.lit && narrow.whole && wide.shown && wide.lit, j({ narrow, wide }));
        await p.key('Escape', 'Escape', 27);
      } else await p.key('Escape', 'Escape', 27);
      // the replay from the keys list, and focus back on the keys button
      if (w < 960) await p.key('Escape', 'Escape', 27);
      await p.key('?', 'Slash', 191, '?');
      const again = await p.clickEl(sel('.help .guide-again'));
      g = await p.ev(GUIDE_ALL);
      const helpShut = await p.ev(`document.querySelector('.help').hidden`);
      check(`guide: "Show the guide again" in the keys list replays it [${at}]`, !!again && g.shown && g.focus_in && g.at_dot === 0 && helpShut, j({ again, g, helpShut }));
      await p.key('Escape', 'Escape', 27);
      g = await p.ev(GUIDE_ALL);
      const back = await p.ev(`document.activeElement === document.querySelector('button[data-keys]')`);
      check(`guide: focus returns where it was when it ends [${at}]`, !g.shown && back, j({ g, back }));
      // Your call is a step only while a call waits on the user
      await p.ev(`document.querySelector('#nav a[href="#your-call"] .n').textContent = '0'`);
      await p.key('?', 'Slash', 191, '?');
      await p.clickEl(sel('.help .guide-again'));
      const titles = [];
      for (let k = 0; k < 4; k++) { g = await p.ev(GUIDE_ALL); if (!g.shown) break; titles.push(g.title); await p.clickEl(sel('.guide .gnext')); }
      check(`guide: no Your call step when no call waits [${at}]`, j(titles) === j(TITLES.slice(0, 3)) && g.dots === 3, j({ titles, g }));
      await walkGuide(w, scheme, '');
      // never on an address that names a place: whoever followed it came for that place
      const deep = [];
      for (const hash of ['x-e1', 'runs', 'c-c1:reading', 'overview&place=p_1a2b3c']) {
        await open(w, scheme, hash, { first: true });
        g = await p.ev(GUIDE_ALL);
        deep.push({ hash, made: g.made, stored: g.stored });
      }
      check(`guide: never on a deep link, and still unseen after one [${at}]`, deep.every(d => !d.made && d.stored === null), j(deep));
      // never in print
      await open(w, scheme, '', { first: true });
      await p.media({ type: 'print', scheme });
      const printed = await p.ev(`[getComputedStyle(document.querySelector('.guide')).display, getComputedStyle(document.querySelector('.guide-spot')).display]`);
      await p.media({ type: '', scheme });
      check(`guide: never in print [${at}]`, j(printed) === j(['none', 'none']), j(printed));
      // never in the no-script document
      await p.blank(); await p.noScript(true);
      await p.goto(a.url); await p.settle({ quietMs: 100, script: false });
      await p.noScript(false);
      const ns = await p.ev(`[!!document.querySelector('.guide, .guide-spot'), document.documentElement.className]`);
      check(`guide: never in the no-script document [${at}]`, ns[0] === false && !/\bapp\b/.test(ns[1]), j(ns));
      if (at === '1280 light') {
        // a page drawn without guide.css: no guide, and still unseen, so it shows once the styles are there
        await open(w, scheme, '', { first: true });
        await p.ev(GUIDE_FORGET);
        await p.send('Network.setBlockedURLs', { urls: ['*guide.css'] });
        await p.blank(); await p.goto(a.url); await ready(); await p.settle({ quietMs: 100 });
        const bare = await p.ev(GUIDE_ALL);
        await p.key('?', 'Slash', 191, '?');
        const replay = await p.ev(`!!document.querySelector('.help .guide-again')`);
        await p.send('Network.setBlockedURLs', { urls: [] });
        check('guide: without its styles it does not show, stays unseen, and offers no replay', !bare.made && bare.stored === null && !replay, j({ bare, replay }));
        // where the browser keeps nothing (a hosted page's sandboxed frame) it never shows by itself, or it would come
        // back on every load; the keys list replays it, Skip ends it, and nothing is thrown (the hosted-mode pass)
        const { identifier } = await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('denied', 'SecurityError'); } });` });
        await p.blank(); await p.goto(a.url); await ready(); await p.settle({ quietMs: 100 });
        const one = await p.ev(GUIDE_ALL);
        await p.key('?', 'Slash', 191, '?');
        await p.clickEl(sel('.help .guide-again'));
        const replayed = await p.ev(GUIDE_ALL);
        await p.clickEl(sel('.guide .gskip'));
        const skipped = await p.ev(GUIDE_ALL);
        await p.blank(); await p.goto(a.url); await ready(); await p.settle({ quietMs: 100 });
        const two = await p.ev(GUIDE_ALL);
        await p.send('Page.removeScriptToEvaluateOnNewDocument', { identifier });
        check('guide: with no storage it never shows by itself, the keys list replays it, Skip ends it, nothing thrown',
          !one.shown && one.stored === 'throws' && replayed.shown && !skipped.shown && !two.shown && !p.errors.length, j({ one, replayed, skipped, two, errors: p.errors }));
      }
    }
    // the nav switches views and clears the stack; the title follows
    if (want('views')) {
      await open(w, scheme);
      const r = await p.clickEl(sel('#nav a[href="#runs"]'));
      let s = await st();
      check(`views: the nav opens Runs [${at}]`, r && s.view === 'runs' && s.hash === 'runs' && s.views_on === 1 && s.title.startsWith('Runs · '), j(s));
      // from 960 the nav lists every view; under it the nav holds the sections, and All criteria is reached from the Overview
      if (w >= 960) await p.clickEl(sel('#nav a[href="#criteria"]'));
      else { await p.clickEl(sel('#nav a[href="#overview"]')); await p.clickEl(sel('#overview .quiet a[href="#criteria"]')); }
      s = await st();
      check(`views: All criteria opens [${at}]`, s.view === 'criteria' && s.hash === 'criteria' && !s.top, j(s));
      const cur = await p.ev(`(() => { const c = document.querySelector('#nav a[aria-current="page"]'), i = document.querySelector('#nav a.in'); return [c && c.getAttribute('href'), i && i.getAttribute('href')]; })()`);
      check(`views: the nav marks where you are [${at}]`, cur[0] === '#criteria' && cur[1] === '#research', j(cur));
    }
    // Design pass 1, target 11: the top bar's About opens its view as the nav opens one, marked where you are; what
    // the words mean is there, by its own address too
    if (want('about')) {
      await open(w, scheme);
      const r = await p.clickEl(sel('header.top a[href="#about"]'));
      let s = await st();
      const cur = await p.ev(`[(document.querySelector('header.top a[href="#about"]') || {}).ariaCurrent || null, !!document.querySelector('#nav a[aria-current]')]`);
      check(`about: the top bar's About opens its view, marked there and nowhere in the nav [${at}]`, r && s.view === 'about' && s.hash === 'about' && s.views_on === 1 && s.title.startsWith('About · ') && j(cur) === j(['page', false]), j({ s, cur }));
      await p.clickEl(sel('#nav a[href="#runs"]'));
      const off = await p.ev(`(document.querySelector('header.top a[href="#about"]') || {}).ariaCurrent || null`);
      check(`about: leaving About unmarks it [${at}]`, off === null, j(off));
      await open(w, scheme, 'g-command');
      s = await st();
      check(`about: a word's address opens About at the word [${at}]`, s.view === 'about' && (await p.ev(`!!document.getElementById('g-command').checkVisibility()`)), j(s));
    }
    // the inspector stack: a row opens its card; a link inside the card stacks the next; crumbs say the path
    if (want('stack')) {
      await open(w, scheme, 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      let s = await st();
      check(`stack: a row opens its card [${at}]`, s.view === 'runs' && s.top === 'run-runs1' && s.enter && s.ids_in_insp === 0 && s.hash === 'run-runs1' && j(s.crumbs) === j(['Runs', 'runs1']) && s.sel.includes('run-runs1'), j(s));
      check(`stack: under 960 the card is a sheet, beside it from 960 [${at}]`, s.sheet === (w < 960), j(s));
      check(`stack: under a sheet the work takes no focus [${at}]`, s.inert === (w < 960), j(s));
      check(`stack: the card's title takes focus [${at}]`, s.focus === 'inspector:H3', j(s));
      const folded = await p.ev(`document.querySelector('#runs details[data-for="run-runs1"]').open`);
      check(`stack: the row stays folded in the centre [${at}]`, folded === false, j(folded));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      s = await st();
      check(`stack: a link in the card stacks the next card [${at}]`, s.view === 'runs' && s.top === 'c-c1' && j(s.crumbs) === j(['Runs', 'runs1', 'c1']), j(s));
      await p.clickEl(sel('.ibody a[href="#r-imagenet-val"]'));
      await p.clickEl(sel('.ibody a[href="#run-runs3"]'));
      await p.clickEl(sel('.ibody a[href="#c-c2"]'));
      s = await st();
      check(`stack: past four crumbs, the first, a gap, the last two [${at}]`, j(s.crumbs) === j(['Runs', '…', 'runs3', 'c2']), j(s));
      // a card already on the trail cuts the trail back to it
      await p.clickEl(sel('.ibody a[href="#run-runs1"]'));
      s = await st();
      check(`stack: a card on the trail cuts it back [${at}]`, s.top === 'run-runs1' && j(s.crumbs) === j(['Runs', 'runs1']), j(s));
      await shot(`stack-${w}-${scheme}`);
      const o = await p.ev(OVERFLOW);
      check(`nothing past the screen with a card open [${at}]`, o.n === 0 && o.scroll <= o.width, j(o));
    }
    // crumbs, Back and Esc, and focus back where you were
    if (want('back')) {
      // ‹ walks the browser's own history where it can: no new entry, and focus back on the row once at the root
      await open(w, scheme, 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      if (w >= 960) {
        // beside the work the row stays in reach: opening the card already open is no new step, so Back closes it
        await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
        await p.ev('history.back()'); await sleep(400);
        let b = await st();
        check(`back: opening the card already open adds no step to history [${at}]`, !b.top && b.hash === 'runs', j(b));
        await p.ev('history.forward()'); await sleep(400);
      }
      await p.clickEl(sel('.ibar [data-act="back"]')); await sleep(400);
      let b = await st();
      check(`back: ‹ to the root, focus on the row that opened the card [${at}]`, !b.top && b.hash === 'runs' && b.focus === 'SUMMARY:run-runs1', j(b));
      await p.ev('history.forward()'); await sleep(400);
      b = await st();
      check(`back: ‹ was the browser's Back: Forward reopens the card [${at}]`, b.top === 'run-runs1', j(b));
      await open(w, scheme, 'c-c1');
      await p.clickEl(sel('.ibar [data-act="back"]')); await sleep(400);
      b = await st();
      check(`back: ‹ from a cold card lands on its view, focus on the card's row [${at}]`, b.view === 'q-q1' && !b.top && b.focus === 'A:c-c1', j(b));
      await p.ev('history.forward()'); await sleep(400);
      b = await st();
      check(`back: ‹ from a cold card was the browser's Back: Forward reopens it [${at}]`, b.top === 'c-c1', j(b));
      await open(w, scheme, 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.clickEl(sel('.ibody a[href="#run-runs3"]'));
      await p.clickEl(sel('.ibar .crumbs a[data-crumb="0"]'));
      let s = await st();
      check(`crumbs: a crumb goes back to its card [${at}]`, s.top === 'run-runs1' && j(s.crumbs) === j(['Runs', 'runs1']), j(s));
      await p.clickEl(sel('.ibar [data-act="back"]'));
      s = await st();
      check(`back: Back after a crumb goes one card back, never deeper [${at}]`, !s.top && s.root && s.hash === 'runs', j(s));
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.clickEl(sel('.ibody a[href="#run-runs1"]'));
      await p.clickEl(sel('.ibar [data-act="back"]'));
      s = await st();
      check(`back: Back after a cut to a card on the trail goes one card back [${at}]`, !s.top && s.root, j(s));
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.clickEl(sel('.ibar [data-act="back"]'));
      s = await st();
      check(`back: the Back button goes one card back [${at}]`, s.top === 'run-runs1' && s.hash === 'run-runs1', j(s));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.key('Escape', 'Escape', 27);
      s = await st();
      check(`esc: Esc closes the inspector to its root [${at}]`, !s.top && s.root && s.view === 'runs' && s.hash === 'runs' && !s.sheet, j(s));
      check(`esc: focus returns to the row that opened the card [${at}]`, s.focus === 'SUMMARY:run-runs1' && !s.inert, j(s));
      // the browser's own Back walks the same stack
      await p.clickEl(sel('#runs details[data-for="run-runs2b"] > summary'));
      await p.clickEl(sel('.ibody a[href="#run-runs2"]'));
      await p.ev('history.back()'); await sleep(300);
      s = await st();
      check(`back: the browser's Back walks the stack [${at}]`, s.top === 'run-runs2b' && s.view === 'runs', j(s));
      check(`back: after the browser's Back the card's title has focus [${at}]`, s.focus === 'inspector:H3', j(s));
      await p.clickEl(sel('.ibar [data-act="close"]'));
      s = await st();
      check(`close: the close button returns to the root [${at}]`, !s.top && s.root && s.focus === 'SUMMARY:run-runs2b', j(s));
      await p.clickEl(sel('#nav a[href="#resources"]'));
      await p.clickEl(sel('#resources details[data-for="r-imagenet-val"] > summary'));
      if (w >= 960) {
        // beside the work, the nav stays in reach: a view link changes the view and clears the stack
        await p.clickEl(sel('#nav a[href="#history"]'));
        s = await st();
        check(`views: a view link clears the stack [${at}]`, s.view === 'history' && !s.top, j(s));
      } else {
        // under 960 the card is a modal sheet over the work, which the nav sits under
        const m = await p.ev(`[document.querySelector('aside.insp').getAttribute('role'), document.querySelector('aside.insp').getAttribute('aria-modal')]`);
        check(`stack: the sheet is a modal dialog [${at}]`, j(m) === j(['dialog', 'true']), j(m));
      }
    }
    // cold links: an address arriving from outside lands on the card over the view it belongs to; Back stays on the page
    if (want('cold')) {
      for (const [hash, view, top] of [['c-c1', 'q-q1', 'c-c1'], ['run-runs1', 'x-e1', 'run-runs1'], ['r-imagenet-val', 'resources', 'r-imagenet-val'],
        ['call-c2', 'your-call', 'call-c2'], ['q-q1', 'q-q1', null], ['x-e1', 'x-e1', null], ['f-c1:q1', 'q-q1', null]]) {
        await open(w, scheme, hash);
        const s = await st();
        check(`cold: #${hash} opens ${top || view} [${at}]`, s.view === view && s.top === top && s.hash === hash, j(s));
        if (top && w < 960) check(`cold: #${hash} as a sheet has focus in it [${at}]`, s.focus === 'inspector:H3' && s.inert, j(s));
        if (top) {
          let b;
          try { await p.ev('history.back()'); await sleep(300); b = await st(); b.protocol = await p.ev('location.protocol'); } catch (e) { b = { error: String(e.message || e) }; }
          check(`cold: Back from #${hash} lands on ${view}, on the page [${at}]`, b.view === view && !b.top && b.hash === view && ['file:', 'http:'].includes(b.protocol), j(b));
        }
      }
      // a part of a card: the card opens and the part is shown in the inspector
      await open(w, scheme, 'c-c1:reading');
      const s = await st();
      const part = await p.ev(`(() => { const k = document.querySelector('.ibody [data-id="c-c1:reading"]'); return k ? k.classList.contains('flash') : null; })()`);
      check(`cold: a part of a card opens the card and marks the part [${at}]`, s.top === 'c-c1' && s.view === 'q-q1' && part === true, j({ s, part }));
      // the view's rail is the inspector's root; Your call's root is its first call
      await open(w, scheme, 'your-call');
      const yc = await p.ev(`(() => { const r = document.querySelector('.ibody > .rootc > .card'); return r ? r.dataset.id : null; })()`);
      check(`root: Your call's root is its first call [${at}]`, yc === 'call-c2', j(yc));
      await open(w, scheme, 'x-e1');
      const rr = await p.ev(`(() => { const r = document.querySelector('.ibody > .rootc'); return r ? [r.dataset.rootname, !!r.querySelector('.repro')] : null; })()`);
      check(`root: an experiment's root is its Reproduce rail [${at}]`, j(rr) === j(['Reproduce · e1', true]), j(rr));
    }
  }

  // ---------- A2: the address, Your call and the composer, copy, filters, keys and the theme (1280 and 390, light)
  for (const w of WIDTHS) {
    const at = `${w}`;
    if (want('address')) {
      // an extra '#' opens what it names and is written back with one
      await open(w, 'light', '#q-q1');
      let s = await st();
      check(`address: '##q-q1' opens q-q1, written back with one '#' [${at}]`, s.view === 'q-q1' && (await p.ev('location.hash')) === '#q-q1', j(s));
      await p.ev(`location.hash = '##x-e1'`); await sleep(300);
      s = await st();
      check(`address: '##x-e1' typed into an open page opens x-e1 [${at}]`, s.view === 'x-e1' && (await p.ev('location.hash')) === '#x-e1', j(s));
      // an address that names nothing says so, keeps the address as typed, and never falls back in silence
      await open(w, 'light', 'c-nothing-here');
      s = await st();
      const said = await p.ev(`(document.querySelector('.ibody .missing') || {}).textContent`);
      check(`address: one that names nothing says so [${at}]`, said === "nothing here is called 'c-nothing-here'" && s.hash === 'c-nothing-here' && s.view === 'overview', j({ s, said }));
      await p.ev(`location.hash = '#x-nothing'`); await sleep(300);
      s = await st();
      const said2 = await p.ev(`(document.querySelector('.ibody .missing') || {}).textContent`);
      check(`address: one typed into an open page that names nothing says so [${at}]`, said2 === "nothing here is called 'x-nothing'" && s.hash === 'x-nothing', j({ s, said2 }));
      await open(w, 'light', 'run-runs1');
      await p.ev(`location.hash = '#zzz'`); await sleep(300);
      s = await st();
      check(`address: a name that is nowhere, typed with a card open, says so over the working object [${at}]`, s.view === 'x-e1' && s.hash === 'zzz' && (await p.ev(`(document.querySelector('.ibody .missing') || {}).textContent`)) === "nothing here is called 'zzz'", j(s));
      // rb's old view names and rb open's tabs and anchors, each to where it is now
      for (const [hash, view, top, now] of [['findings', 'research', null, 'research'], ['record', 'runs', null, 'runs'], ['tab-runs', 'runs', null, 'runs'],
        ['tab-findings', 'research', null, 'research'], ['questions', 'overview', null, 'overview'], ['top', 'overview', null, 'overview'],
        ['c-c1:q1', 'q-q1', 'c-c1', 'c-c1'], ['all-c-c2', 'criteria', 'c-c2', 'c-c2'], ['c-c6::unfiled', 'criteria', 'c-c6', 'c-c6'], ['calls-q1', 'q-q1', null, 'q-q1']]) {
        await open(w, 'light', hash);
        s = await st();
        check(`address: rb open's '#${hash}' opens ${top || view} [${at}]`, s.view === view && s.top === top && s.hash === now, j(s));
      }
      // rb open's place and version follow the anchor and stay in the address as the app moves
      await open(w, 'light', 'runs&place=p_1a2b3c&at=3f2a9c1');
      s = await st();
      check(`address: '&place=..&at=..' after the anchor is read past and kept [${at}]`, s.view === 'runs' && (await p.ev('location.hash')) === '#runs&place=p_1a2b3c&at=3f2a9c1', j(s));
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      const h = await p.ev('location.hash');
      check(`address: a card opened keeps the place and version [${at}]`, h === '#run-runs1&place=p_1a2b3c&at=3f2a9c1', j(h));
    }
    if (want('yourcall')) {
      await open(w, 'light', 'your-call');
      await p.hookClipboard();
      const root = await p.ev(`(() => { const r = document.querySelector('.ibody > .rootc > .card'); return r ? r.dataset.id : null; })()`);
      if (w >= 960) check(`your call: the view starts on the first call [${at}]`, root === 'call-c2', j(root));
      else {
        // under 960 the root is not drawn: the keys act on a call once it is opened
        await p.key('i', 'KeyI', 73, 'i');
        const none = await p.ev(`document.querySelector('.ibody .decide') ? document.querySelector('.ibody .decide').dataset.chosen || null : null`);
        check(`your call: under 960 'i' chooses nothing until a call is open [${at}]`, none === null, j(none));
        await p.clickEl(sel('#your-call details[data-for="call-c2"] > summary'));
      }
      // nothing copyable until an action and a reason exist
      const before = await p.ev(`(() => { const b = document.querySelector('.ibody .composer .cp'); return [b ? b.disabled : null, !!document.querySelector('.ibody .composer:not([hidden])')]; })()`);
      check(`composer: hidden, its Copy off, before a choice [${at}]`, j(before) === j([true, false]), j(before));
      const lines = await p.ev(`Array.from(document.querySelectorAll('.cp')).filter(b => { const c = b.closest('.cmdbox'); const t = c && c.querySelector('.cmd') ? c.querySelector('.cmd').textContent : ''; return /rb decide/.test(t) && !b.closest('.composer'); }).length`);
      check(`composer: no listed decide line is copyable anywhere [${at}]`, lines === 0, j(lines));
      await p.key('i', 'KeyI', 73, 'i');
      let c = await p.ev(`(() => { const b = document.querySelector('.ibody .decide'); return { chosen: b.dataset.chosen, line: b.querySelector('.composer .cmd').textContent, off: b.querySelector('.composer .cp').disabled, focus: document.activeElement.className }; })()`);
      check(`composer: 'i' chooses investigate and focuses the reason [${at}]`, c.chosen === 'investigate' && c.focus === 'why' && c.off && c.line === "rb decide c2 investigate --why '‹your reason›'", j(c));
      await p.type("it's 0.0016 under, one seed short");
      c = await p.ev(`(() => { const b = document.querySelector('.ibody .decide'); return { line: b.querySelector('.composer .cmd').textContent, off: b.querySelector('.composer .cp').disabled, cons: b.querySelector('.composer .willread span').textContent }; })()`);
      check(`composer: the reason goes in single-quoted, Copy is live, the review previews [${at}]`,
        c.line === "rb decide c2 investigate --why 'it'\\''s 0.0016 under, one seed short'" && !c.off && c.cons === "investigating since ‹day›: it's 0.0016 under, one seed short", j(c));
      // on a touch width the choices say no key letter, and the line wraps in its box rather than running past its edge
      const tw = await p.ev(`(() => { const b = document.querySelector('.ibody .decide'), l = b.querySelector('.composer .cmd'), kb = Array.from(b.querySelectorAll('.seg button kbd'));
        return { kbd: kb.length, shown: kb.filter(k => k.checkVisibility()).length, labels: Array.from(b.querySelectorAll('.seg button')).filter(x => x.checkVisibility()).map(x => x.innerText.trim()), whole: l.scrollWidth <= l.clientWidth + 1 }; })()`);
      if (w < 640) check(`composer: at a touch width the choices show no key letter and the line wraps whole in its box [${at}]`, tw.kbd > 0 && tw.shown === 0 && tw.whole && tw.labels.every(t => !/ [ari]$/.test(t)), j(tw));
      else check(`composer: with room the choices show their keys [${at}]`, tw.kbd > 0 && tw.shown === tw.kbd, j(tw));
      const net0 = await p.ev(`performance.getEntriesByType('resource').length`);
      await p.clickEl(sel('.ibody .composer .cp'));
      const copied = await p.copied();
      const net1 = await p.ev(`performance.getEntriesByType('resource').length`);
      check(`composer: Copy copies the line, and nothing is sent [${at}]`, copied && copied[copied.length - 1] === c.line + '\n' && net1 === net0 && (await p.ev(`document.forms.length`)) === 0, j({ copied, net0, net1 }));
      // a reason is text, whatever it holds: '$' is no pattern
      await p.ev(`(() => { const i = document.querySelector('.ibody input.why'); i.value = ''; i.dispatchEvent(new Event('input', { bubbles: true })); i.focus(); })()`);
      await p.type('costs $$5 and $& and $` x');
      const dl = await p.ev(`[document.querySelector('.ibody .composer .cmd').textContent, document.querySelector('.ibody .composer .willread span').textContent]`);
      check(`composer: a '$' in the reason stays as typed [${at}]`, j(dl) === j(["rb decide c2 investigate --why 'costs $$5 and $& and $` x'", 'investigating since ‹day›: costs $$5 and $& and $` x']), j(dl));
      // a line of work's calls show no key that does nothing
      await open(w, 'light', 'call-e2');
      const kb = await p.ev(`document.querySelectorAll('.ibody .seg kbd').length`);
      await p.key('p', 'KeyP', 80, 'p');
      const chosen = await p.ev(`document.querySelector('.ibody .decide').dataset.chosen || null`);
      check(`composer: a gate call shows no key for park, continue or stop, and 'p' chooses nothing [${at}]`, kb === 0 && chosen === null, j({ kb, chosen }));
      // a past version composes no command, by key or by click, and a call chosen live is not put back when the live
      // module swaps the past version in (rb open draws its withheld line in #withheld)
      await open(w, 'light', 'call-c2');
      await p.key('i', 'KeyI', 73, 'i');
      await p.type('one seed short');
      await p.hookClipboard();
      await p.ev(`(() => { const d = document.createElement('div'); d.id = 'withheld'; d.innerHTML = '<p class="withheld">A past version offers no command</p>'; document.body.prepend(d);
        document.dispatchEvent(new CustomEvent('rb:swapped', { cancelable: true })); })()`);
      await sleep(200);
      const swapped = await p.ev(`(() => { const b = document.querySelector('.ibody .decide'); return { chosen: b.dataset.chosen || null, shown: Array.from(b.querySelectorAll('.composer')).filter(c => !c.hidden).map(c => c.textContent) }; })()`);
      await p.ev(`document.activeElement && document.activeElement.blur && document.activeElement.blur()`);
      await p.key('c', 'KeyC', 67, 'c');
      await p.key('i', 'KeyI', 73, 'i');
      await p.clickEl(`Array.from(document.querySelectorAll('.ibody .decide .seg button[data-action]')).find(x => x.offsetParent !== null)`);
      const pastc = await p.ev(`(() => { const b = document.querySelector('.ibody .decide'); return { chosen: b.dataset.chosen || null, shown: Array.from(b.querySelectorAll('.composer')).filter(c => !c.hidden).map(c => c.textContent) }; })()`);
      const pastcp = await p.copied();
      await p.ev(`document.getElementById('withheld').remove()`);
      check(`composer: a past version puts back no call chosen live [${at}]`, swapped.chosen === null && swapped.shown.length === 0, j(swapped));
      check(`composer: a past version composes no command from a key or a click [${at}]`, pastc.chosen === null && pastc.shown.length === 0 && !(pastcp || []).some(t => /rb decide/.test(t)), j({ pastc, pastcp }));
    }
    if (want('waiting')) {
      await open(w, 'light', 'to-confirm');
      await p.hookClipboard();
      const boxes = await p.ev(`Array.from(document.querySelectorAll('#to-confirm .cp')).map(b => b.closest('.cmdbox').querySelector('.cmd').textContent)`);
      check(`waiting: each item copies only its confirm [${at}]`, boxes.length === 2 && boxes.every(t => /^rb confirm \S+$/.test(t)), j(boxes));
      for (let i = 0; i < boxes.length; i++) await p.clickEl(`document.querySelectorAll('#to-confirm .cp')[${i}]`);
      const copied = await p.copied();
      check(`waiting: Copy gives the confirm line, nothing else [${at}]`, j(copied) === j(boxes.map(b => b + '\n')), j(copied));
    }
    if (want('copy')) {
      await open(w, 'light', 'run-runs1');
      await p.hookClipboard();
      await p.clickEl(sel('.ibody .cmdbox .cp'));
      await p.clickEl(sel('.ibar [data-act="link"]'));
      const copied = await p.copied();
      const said = await p.ev(`(document.querySelector('.ibody .cmdbox .cp') || {}).textContent`);
      check(`copy: a command box copies its command, and says so [${at}]`, copied[0] === 'python eval.py --precision int8 --seed 1\n' && said === 'Copied', j({ copied, said }));
      check(`copy: Link copies the address of the card on top [${at}]`, (copied[1] || '').endsWith('dom-fixture.html#run-runs1'), j(copied));
      await open(w, 'light', 'x-e1');
      await p.hookClipboard();
      await p.key('c', 'KeyC', 67, 'c');
      const recipe = await p.copied();
      if (w >= 960) check(`copy: 'c' on an experiment copies its recipe, from the inspector's bar [${at}]`, j(recipe) === j(['git checkout 3f2a9c1\npython eval.py --precision int8 --seed 1\n']) && (await p.ev(`!!document.querySelector('.ibar .bact .cp')`)), j(recipe));
      else check(`copy: under 960 with nothing open, 'c' copies nothing from the unseen inspector [${at}]`, j(recipe) === j([]), j(recipe));
    }
    if (want('filter')) {
      await open(w, 'light', 'runs');
      const counts = `Array.from(document.querySelectorAll('#nav .n, .fchip')).map(x => x.textContent).join('|')`;
      const n0 = await p.ev(counts);
      await p.key('/', 'Slash', 191, '/');
      const foc = await p.ev(`document.activeElement === document.querySelector('#runs .filterbar input')`);
      await p.type('runs2');
      let vis = await p.ev(`Array.from(document.querySelectorAll('#runs [data-row]')).filter(r => !r.hidden).map(r => r.dataset.for)`);
      check(`filter: '/' focuses the filter, and words show only their rows [${at}]`, foc && j(vis) === j(['run-runs2', 'run-runs2b']), j({ foc, vis }));
      await p.ev(`(() => { const i = document.querySelector('#runs .filterbar input'); i.value = ''; i.dispatchEvent(new Event('input', { bubbles: true })); })()`);
      await p.clickEl(sel('#runs .fchip[data-fret]'));
      vis = await p.ev(`Array.from(document.querySelectorAll('#runs [data-row]')).filter(r => !r.hidden).map(r => r.dataset.for)`);
      check(`filter: the retracted toggle shows the retracted runs [${at}]`, j(vis) === j(['run-runs2']), j(vis));
      check(`filter: filters never count [${at}]`, (await p.ev(counts)) === n0, j(n0));
    }
    if (want('keys')) {
      await open(w, 'light', 'runs');
      await p.key('j', 'KeyJ', 74, 'j');
      let s = await st();
      check(`keys: j opens the first row's card [${at}]`, s.top === 'run-runs1', j(s));
      await p.key('j', 'KeyJ', 74, 'j');
      s = await st();
      check(`keys: j moves to the next [${at}]`, s.top === 'run-runs2', j(s));
      await p.key('k', 'KeyK', 75, 'k');
      s = await st();
      check(`keys: k moves back [${at}]`, s.top === 'run-runs1', j(s));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.key('[', 'BracketLeft', 219, '[');
      s = await st();
      check(`keys: '[' goes back through the inspector [${at}]`, s.top === 'run-runs1', j(s));
      await p.key(']', 'BracketRight', 221, ']'); await sleep(200);
      s = await st();
      check(`keys: ']' goes forward [${at}]`, s.top === 'c-c1', j(s));
      await p.key('?', 'Slash', 191, '?');
      const help = await p.ev(`(() => { const h = document.querySelector('.help'); return h ? [!h.hidden, (h.querySelector('a') || {}).getAttribute ? h.querySelector('a').getAttribute('href') : null] : null; })()`);
      check(`keys: '?' opens the keys, led by what the words mean [${at}]`, j(help) === j([true, '#words']), j(help));
      const opened = await p.ev(`[document.activeElement.getAttribute('href'), document.querySelector('button[data-keys]').getAttribute('aria-expanded')]`);
      check(`keys: the keys list opens on what the words mean, and says it is open [${at}]`, j(opened) === j(['#words', 'true']), j(opened));
      await p.key('j', 'KeyJ', 74, 'j');
      check(`keys: single keys do nothing while the keys list is open [${at}]`, (await st()).top === 'c-c1', '');
      await p.clickEl(sel('.help .keys-on'));
      await p.key('Escape', 'Escape', 27);
      const help2 = await p.ev(`document.querySelector('.help').hidden && document.querySelector('button[data-keys]').getAttribute('aria-expanded') === 'false' && (document.activeElement.matches('button[data-keys]') || !!document.activeElement.closest('.ibody'))`);
      await p.key('j', 'KeyJ', 74, 'j');
      s = await st();
      check(`keys: single-key shortcuts can be turned off, and stay off [${at}]`, help2 && s.top === 'c-c1' && (await p.ev(`localStorage.getItem('rb-keys')`)) === 'off', j(s));
      await p.ev(`localStorage.removeItem('rb-keys')`);
      // '[' is the browser's Back through the inspector: after a crumb it replays the deeper trail; ‹ never would
      await open(w, 'light', 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.clickEl(sel('.ibar .crumbs a[data-crumb="0"]'));
      await p.key('[', 'BracketLeft', 219, '['); await sleep(300);
      s = await st();
      check(`keys: '[' is the browser's Back: after a crumb it returns to the deeper card [${at}]`, s.top === 'c-c1', j(s));
      await open(w, 'light', 'zzz-nowhere');
      await p.key('[', 'BracketLeft', 219, '['); await sleep(300);
      s = await st();
      check(`keys: '[' after a cold address that names nothing stays on the page [${at}]`, s.view === 'overview' && !s.top && (await p.ev('location.protocol')) === 'file:', j(s));
      // the keys list holds Tab while open, and '?' closes it from its checkbox
      await p.key('?', 'Slash', 191, '?');
      for (let n = 0; n < 3; n++) await p.key('Tab', 'Tab', 9);
      const inHelp = await p.ev(`!!document.activeElement.closest('.help')`);
      await p.ev(`document.querySelector('.help .keys-on').focus()`);
      await p.key('?', 'Slash', 191, '?');
      check(`keys: Tab stays in the open keys list, and '?' closes it from its checkbox [${at}]`, inHelp && (await p.ev(`document.querySelector('.help').hidden`)), j(inHelp));
      // Up and Down move in the navigation
      await open(w, 'light', 'runs');
      await p.ev(`document.querySelector('#nav a[href="#overview"]').focus()`);
      await p.key('ArrowDown', 'ArrowDown', 40);
      const nf = await p.ev(`document.activeElement.getAttribute('href')`);
      check(`keys: Down moves in the navigation [${at}]`, nf === '#research', j(nf));
    }
    if (want('theme')) {
      await open(w, 'light');
      const bg = () => p.ev(`getComputedStyle(document.body).backgroundColor`);
      const sys = await bg();
      await p.clickEl(sel('button[data-theme]'));
      const l = [await p.ev(`document.documentElement.dataset.theme || null`), await bg()];
      await p.clickEl(sel('button[data-theme]'));
      const d = [await p.ev(`document.documentElement.dataset.theme || null`), await bg(), await p.ev(`localStorage.getItem('rb-theme')`)];
      await open(w, 'light');
      const kept = [await p.ev(`document.documentElement.dataset.theme || null`), await bg()];
      check(`theme: system, light, dark; dark wins over a light system and is kept [${at}]`, l[0] === 'light' && d[0] === 'dark' && d[1] !== sys && d[2] === 'dark' && j(kept) === j(d.slice(0, 2)), j({ sys, l, d, kept }));
      await p.clickEl(sel('button[data-theme]'));
      check(`theme: back to the system's [${at}]`, (await p.ev(`document.documentElement.dataset.theme || null`)) === null && (await p.ev(`localStorage.getItem('rb-theme')`)) === null, '');
    }
  }

  // what moves under the app: the window across 960, a reload, a typed address, a view link inside a card
  if (want('moves')) {
    for (const scheme of SCHEMES) {
      await open(1280, scheme, 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.viewport(390, 844); await sleep(400);
      let s = await st();
      check(`moves: narrowed across 960, the card becomes a sheet with focus in it [${scheme}]`, s.sheet && s.inert && s.top === 'c-c1' && s.focus === 'inspector:H3', j(s));
      const o = await p.ev(OVERFLOW);
      check(`moves: narrowed across 960, nothing past the screen [${scheme}]`, o.n === 0 && o.scroll <= o.width, j(o));
      await p.viewport(1280, 900); await sleep(400);
      s = await st();
      check(`moves: widened across 960, the card stands beside the work, focus kept in it [${scheme}]`, !s.sheet && !s.inert && s.top === 'c-c1' && s.focus === 'inspector:H3', j(s));
      await p.send('Page.reload'); await sleep(300);
      await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000); await p.settle({ quietMs: 100 });
      s = await st();
      check(`moves: a reload keeps the view, the stack and the crumbs [${scheme}]`, s.view === 'runs' && s.top === 'c-c1' && j(s.crumbs) === j(['Runs', 'runs1', 'c1']), j(s));
      await p.ev(`location.hash = '#resources'`); await sleep(300);
      s = await st();
      check(`moves: an address typed into the page opens it and is written in history [${scheme}]`, s.view === 'resources' && !s.top && s.state && s.state.c === 'resources', j(s));
    }
    for (const w of WIDTHS) {
      await open(w, 'light', 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#x-e1"]'));
      const s = await st();
      const ok = w < 960 ? s.view === 'x-e1' && !s.top && s.hash === 'x-e1' && !s.sheet : s.view === 'x-e1' && s.top === 'run-runs1';
      check(`moves: a view link inside a card opens the view${w < 960 ? ', closing the sheet' : ', the card beside it'} [${w}]`, ok, j(s));
    }
  }

  // rb open's live module: rb:card when a card comes to the inspector's top by your act, never on a redraw; rb:swapped
  // when it has redrawn the navigator and the views, after which the app stands where it stood
  if (want('live')) {
    await p.send('Page.addScriptToEvaluateOnNewDocument', { source: "window.__cards = []; document.addEventListener('rb:card', e => window.__cards.push(e.detail.id));" });
    // a new drawing of the views: the same markup, with nothing the script added
    const REDRAW = `((drop, lead) => { const t = document.createElement('template'); t.innerHTML = document.querySelector('#main').innerHTML;
      t.content.querySelectorAll('.cmprow').forEach(r => { const b = r.querySelector('.cp'), box = r.previousElementSibling; if (b && box) { b.classList.remove('big2'); box.appendChild(b); } r.remove(); });
      t.content.querySelectorAll('.cp').forEach(b => { if (!b.closest('.nocp')) b.remove(); });
      t.content.querySelectorAll('.composer .cp').forEach(b => { b.disabled = true; b.textContent = 'Copy'; });
      if (lead) { const c = t.content.getElementById(lead); if (c) c.insertAdjacentHTML('afterbegin', '<p><a href="#run-runs2b">runs2b</a></p>'); }
      t.content.querySelectorAll('.on, .sel, .flash, .enh').forEach(e => e.classList.remove('on', 'sel', 'flash', 'enh'));
      if (drop) { const d = t.content.querySelector('[data-for="' + drop + '"]'); if (d) d.remove(); }
      const main = document.querySelector('#main'); main.replaceChildren(t.content);
      const ev = new CustomEvent('rb:swapped', { cancelable: true }); document.dispatchEvent(ev); return ev.defaultPrevented; })`;
    for (const w of WIDTHS) {
      const at = `${w}`;
      await open(w, 'light', 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.clickEl(sel('.ibar [data-act="back"]'));
      await p.key('Escape', 'Escape', 27);
      await p.clickEl(sel('#runs details[data-for="run-runs2b"] > summary'));
      if (w >= 960) await p.clickEl(sel('#runs details[data-for="run-runs2b"] > summary'));
      await p.viewport(w < 960 ? 1280 : 390, 900); await sleep(300); await p.viewport(w, w < 600 ? 844 : 900); await sleep(300);
      let cards = await p.ev('window.__cards');
      check(`live: rb:card once for each card you bring to the top, none for a resize or the same card again [${at}]`, j(cards) === j(['run-runs1', 'c-c1', 'run-runs1', 'run-runs2b']), j(cards));
      await open(w, 'light', 'c-c1');
      cards = await p.ev('window.__cards');
      check(`live: rb:card for a cold link [${at}]`, j(cards) === j(['c-c1']), j(cards));
      await open(w, 'light', 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.ev(`document.querySelector('#main').scrollTop = 0; window.__cards = []; (document.querySelector('.ibody a[href="#run-runs1"]') || document.body).focus()`);
      const before = await st();
      const focused = await p.ev(`document.activeElement.outerHTML.slice(0, 60)`);
      const prevented = await p.ev(REDRAW + '(null)');
      let s = await st();
      const after = await p.ev(`[window.__cards.length, !!document.querySelector('#run-runs1 .cmdbox .cp'), document.activeElement.outerHTML.slice(0, 60) === ${j(focused)}, document.querySelector('#main').scrollTop]`);
      check(`live: after a redraw the app stands where it stood, says it did, and tells of no card [${at}]`,
        prevented === true && s.view === 'runs' && s.top === 'c-c1' && j(s.crumbs) === j(before.crumbs) && s.views_on === 1 && s.sel.includes('c-c1') && after[0] === 0 && after[3] === 0, j({ prevented, s, after }));
      check(`live: the redrawn parts get their copy buttons, and focus stays on the same part [${at}]`, after[1] && after[2], j(after));
      // the composer and focus survive a redraw: the chosen call, the typed reason, the caret
      await open(w, 'light', 'call-c2');
      await p.clickEl(sel('.ibody .seg button[data-action="reject"]'));
      await p.type('keep me');
      await p.ev(`document.querySelector('.ibody input.why').setSelectionRange(2, 6)`);
      await p.ev(REDRAW + '(null)');
      const comp = await p.ev(`(() => { const b = document.querySelector('.ibody .decide'); const i = b.querySelector('input.why'); return [b.dataset.chosen, i.value, document.activeElement === i, b.querySelector('.composer .cmd').textContent, i.selectionStart, i.selectionEnd, b.querySelector('.composer .cp').disabled]; })()`);
      check(`live: a redraw keeps the call chosen, the reason typed, its selection and a live Copy [${at}]`, j(comp) === j(['reject', 'keep me', true, "rb decide c2 reject --why 'keep me'", 2, 6, false]), j(comp));
      // focus on a link stays on that link when the redrawn card holds one more before it
      await open(w, 'light', 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.ev(`document.querySelector('.ibody a[href="#r-imagenet-val"]').focus()`);
      await p.ev(REDRAW + "(null, 'c-c1')");
      const fl = await p.ev(`document.activeElement.getAttribute('href')`);
      check(`live: focus stays on the same link when the redrawn card holds more [${at}]`, fl === '#r-imagenet-val', j(fl));
      await open(w, 'light', 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.ev(REDRAW + '(null)');
      const kept = await st();
      check(`live: a redraw keeps focus in the card [${at}]`, kept.focus === 'inspector:H3' && kept.inert === (w < 960), j(kept));
      // a card gone from the middle of the trail: ‹ still walks the browser's history
      await open(w, 'light', 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.clickEl(sel('.ibody a[href="#r-imagenet-val"]'));
      await p.ev(REDRAW + "('c-c1')");
      await p.clickEl(sel('.ibar [data-act="back"]')); await sleep(300);
      await p.ev('history.forward()'); await sleep(300);
      const mid = await st();
      check(`live: with a card gone from the trail, ‹ is still the browser's Back [${at}]`, mid.top === 'r-imagenet-val', j(mid));
      await open(w, 'light', 'runs');
      await p.clickEl(sel('#runs details[data-for="run-runs1"] > summary'));
      await p.clickEl(sel('.ibody a[href="#c-c1"]'));
      await p.ev(`window.__keepC1 = document.querySelector('#main details[data-for="c-c1"]').outerHTML`);
      await p.ev(REDRAW + "('c-c1')");
      s = await st();
      const said = await p.ev(`(document.querySelector('.ibody .missing') || {}).textContent`);
      check(`live: a card gone from the redrawn record says so in the inspector [${at}]`, said === "nothing here is called 'c-c1'" && s.view === 'runs', j({ s, said }));
      // the record's next drawing holds it again: so does the inspector
      await p.ev(`(() => { const main = document.querySelector('#main'); const list = main.querySelector('#criteria .list'); list.insertAdjacentHTML('afterbegin', window.__keepC1); const ev = new CustomEvent('rb:swapped', { cancelable: true }); document.dispatchEvent(ev); return ev.defaultPrevented; })()`);
      s = await st();
      check(`live: a card back in a later redraw is shown again [${at}]`, s.top === 'c-c1' && !(await p.ev(`!!document.querySelector('.ibody .missing')`)), j(s));
    }
  }

  // any shell page, by its own markup: every view, its rail as the root, nothing past the screen, the waiting list's
  // copies, the address, the theme and keys controls
  if (want('generic')) {
    for (const w of WIDTHS) for (const scheme of SCHEMES) {
      const at = `${w} ${scheme}`;
      await open(w, scheme);
      const cls = await p.ev('document.documentElement.className');
      let s = await st();
      check(`generic: html.app and ready, the Overview, one view [${at}]`, /\bapp\b/.test(cls) && /\bready\b/.test(cls) && s.view === 'overview' && s.views_on === 1, j({ cls, s }));
      // the top bar says no path at any width (the film rule): the place by its name, its whole path only in the Where
      // list and the tooltips
      const bar = await p.ev(`(() => { const t = document.querySelector('header.top'); if (!t) return ''; const c = t.cloneNode(true);
        c.querySelectorAll('ul.picks').forEach(e => e.remove()); return c.textContent.replace(/\\s+/g, ' '); })()`);
      check(`generic: the top bar says no absolute path [${at}]`, !/(^|[\s(·:'"])~?\/[\w.~-]/.test(bar) && !(a.record && bar.includes(a.record)), j(bar));
      const views = await p.ev(`Array.from(document.querySelectorAll('main .view')).map(v => [v.id, v.dataset.title || '', v.dataset.root || '', !!v.querySelector('[data-rail]')])`);
      const wrong = [], past = [], inner = [], roots = [];
      for (const [v, title, root, rail] of views) {
        await p.ev(`location.hash = ${j('#' + v)}`); await sleep(150);
        s = await st();
        if (s.view !== v || s.views_on !== 1 || (title && s.title !== title)) wrong.push(v + ': ' + j(s));
        const ov = await p.ev(OVERFLOW); if (ov.n) past.push(v + ': ' + ov.bad.join(', '));
        if (w < 960) { const iv = await p.ev(INNER); if (iv.length) inner.push(v + ': ' + iv.slice(0, 3).join(', ')); }
        // from 960 the inspector's root is the view's rail, or what its data-root names
        if (w >= 960 && (rail || root)) { const r = await p.ev(`!!document.querySelector('.ibody > .rootc > *')`); if (!r) roots.push(v); }
      }
      check(`generic: each of ${views.length} views opens by its address, alone, with its title [${at}]`, wrong.length === 0, j(wrong));
      check(`generic: nothing past the screen in any view [${at}]`, past.length === 0, j(past));
      // Design pass 1, target 9: under 960 each kind of card (by kind and class: a cited rule, one not met), opened as
      // the sheet, keeps every line on the screen
      if (w < 960) {
        const firsts = await p.ev(`(() => { const by = {}; document.querySelectorAll('main .card[id]').forEach(c => { const k = (c.dataset.kind || '') + ' ' + c.className; if (!by[k]) by[k] = c.id; }); return Object.values(by); })()`);
        const cut = [];
        for (const id of firsts) {
          await p.ev(`location.hash = ${j('#' + id)}`); await sleep(400);
          const ov = await p.ev(OVERFLOW); if (ov.n || ov.scroll > ov.width) cut.push(id + ': ' + ov.bad.join(', '));
        }
        await p.ev(`location.hash = '#overview'`); await sleep(250);
        check(`generic: each kind of card keeps every line on the screen [${at}]`, firsts.length > 0 && cut.length === 0, j({ firsts, cut }));
      }
      if (w < 960) check(`generic: no line runs past its box in any view [${at}]`, inner.length === 0, j(inner));
      if (w >= 960) check(`generic: each view with a rail shows it as the inspector's root [${at}]`, roots.length === 0, j(roots));
      // Design pass 1, target 8: from 960, with nothing picked and nothing of the view's own, the inspector is a slim
      // rail and the working view takes the width; a card opened widens it, and Esc folds it back
      if (w >= 960) {
        const RAIL = `(() => { const a = document.querySelector('aside.insp'), m = document.querySelector('main'), e = document.querySelector('.ibody > .rootc');
          return { empty: !!(e && e.dataset.empty === '1'), insp: Math.round(a.getBoundingClientRect().width), main: Math.round(m.getBoundingClientRect().width), said: (e && e.textContent.trim()) || '' }; })()`;
        const wrong = [];
        let empties = 0;
        for (const [v] of views) {
          await p.ev(`location.hash = ${j('#' + v)}`); await sleep(120);
          const r = await p.ev(RAIL);
          if (r.empty) { empties++; if (r.insp > 20 || !r.said) wrong.push(v + ': ' + j(r)); }
          else if (r.insp < 300) wrong.push(v + ' (a root of its own): ' + j(r));
        }
        check(`generic: an inspector with nothing to show is a slim rail, one with a root its full width [${at}]`, empties > 0 && wrong.length === 0, j({ empties, wrong }));
        // a card opened by a click from the Overview, else from the first view with an empty inspector where a link or a
        // row opens one
        const CARD = `(() => { document.querySelectorAll('[data-rail-pick]').forEach(x => x.removeAttribute('data-rail-pick')); const v = document.querySelector('#main .view.on'); const l = Array.from(v.querySelectorAll('a[href^="#"], [data-for] > summary, [data-for]')).find(x => {
          const id = x.getAttribute('href') ? x.getAttribute('href').slice(1) : (x.closest('[data-for]') || {}).dataset.for; const t = id && document.getElementById(id);
          return x.checkVisibility() && t && t.closest('.card'); }); if (!l) return null; l.setAttribute('data-rail-pick', ''); l.scrollIntoView({ block: 'center' }); return l.getAttribute('href') || l.closest('[data-for]').dataset.for; })()`;
        await p.ev(`location.hash = '#overview'`); await sleep(120);
        // (a record with no card any view opens has nothing to open: the rail's first check stands alone)
        let before = await p.ev(RAIL), card = before.empty ? await p.ev(CARD) : null;
        for (const [v] of views) {
          if (card) break;
          await p.ev(`location.hash = ${j('#' + v)}`); await sleep(150);
          before = await p.ev(RAIL); card = before.empty ? await p.ev(CARD) : null;
        }
        if (card) {
          if (card) await p.clickEl(`document.querySelector('[data-rail-pick]')`);
          await sleep(150);
          const opened = await p.ev(RAIL);
          await p.key('Escape', 'Escape', 27); await sleep(150);
          const closed = await p.ev(RAIL);
          check(`generic: a card opened from the rail takes the inspector's width, and Esc folds it back [${at}]`,
            !!card && opened.insp >= 300 && opened.main < before.main && closed.empty && closed.insp <= 20 && closed.main === before.main, j({ card, before, opened, closed }));
        }
      }
      // the nav: each link shown at this width opens its view
      await open(w, scheme);
      const links = await p.ev(`Array.from(document.querySelectorAll('#nav a[href^="#"]')).filter(a => a.offsetParent !== null).map(a => a.getAttribute('href').slice(1))`);
      const miss = [];
      for (const h of links) {
        await p.clickEl(`document.querySelector('#nav a[href="#${h}"]')`);
        s = await st();
        if (s.view !== h && !(await p.ev(`!!(document.getElementById(${j(h)}) || {}).closest`))) miss.push(h);
        else if (s.view !== h && (await p.ev(`(document.getElementById(${j(h)}) || {}).classList ? document.getElementById(${j(h)}).classList.contains('view') : false`))) miss.push(h + ': ' + s.view);
      }
      check(`generic: every nav link shown opens its view [${at}]`, links.length > 0 && miss.length === 0, j({ links, miss }));
    }
    // Polish 1: the inspector's state, a call's row, the composer, the guide on rb's own render
    for (const w of WIDTHS) for (const scheme of SCHEMES) {
      const at = `${w} ${scheme}`;
      await open(w, scheme);
      // a view shows its own: no card of the document's under it, and from 960 no other view's rail as its root
      const views = await p.ev(`Array.from(document.querySelectorAll('main .view')).map(v => v.id)`);
      const cards = [], stale = [];
      for (const v of views) {
        await p.ev(`location.hash = ${j('#' + v)}`); await sleep(150);
        const c = await p.ev(`Array.from(document.querySelectorAll('main .card')).filter(c => c.getBoundingClientRect().height > 0).map(c => c.id)`);
        if (c.length) cards.push(v + ': ' + c.slice(0, 3).join(' '));
        if (w >= 960) {
          const r = await p.ev(`(() => { const v = document.getElementById(${j(v)}), src = document.getElementById(v.dataset.root || ''); const other = !!src && src.matches('[data-rail]') && src.closest('.view') !== v && !v.querySelector('[data-rail]'); const root = document.querySelector('.ibody > .rootc'); return { other, empty: !!root && !root.querySelector('[data-rail], .ycl, .qrail, .repro, .card') && root.textContent.trim().length > 0, said: root ? root.textContent.trim().slice(0, 60) : null }; })()`);
          if (r.other && !r.empty) stale.push(v + ': ' + j(r));
        }
      }
      check(`generic: no view shows a card of the document's under it [${at}]`, cards.length === 0, j(cards));
      if (w >= 960) check(`generic: a view with no rail of its own shows rb's line, not another view's rail [${at}]`, stale.length === 0, j(stale));
      // a call's row in Your call opens its card
      await open(w, scheme, 'your-call');
      const row = await p.ev(`(() => { const d = Array.from(document.querySelectorAll('#your-call details[data-for] > summary')).map(s => s.parentElement).find(d => { const c = document.getElementById(d.dataset.for); return c && c.classList.contains('card'); }); return d ? d.dataset.for : null; })()`);
      if (row) {
        await p.clickEl(`document.querySelector('#your-call details[data-for=${j(row)}] > summary')`); await sleep(400);
        const s2 = await st();
        check(`generic: a call's row in Your call opens its card [${at}]`, s2.top === row && s2.hash === row, j({ row, s2 }));
        // its composer: an action, a reason, then Copy, on a row of its own under the line, which it never covers
        await p.clickEl(`document.querySelector('.ibody .decide .seg button')`);
        await p.ev(`document.querySelector('.ibody input.why').focus()`);
        await p.type('one seed is not enough');
        await p.hookClipboard();
        await p.ev(`document.querySelector('.ibody .composer').scrollIntoView({ block: 'center' })`); await sleep(200);
        await p.clickEl(`document.querySelector('.ibody .composer .cp')`);
        const cm = await p.ev(`(() => { const c = document.querySelector('.ibody .composer .cmd'), b = document.querySelector('.ibody .composer .cp'), r = c.getBoundingClientRect(), q = b.getBoundingClientRect(); return { line: c.textContent, said: b.textContent, covers: Math.min(r.right, q.right) > Math.max(r.left, q.left) && Math.min(r.bottom, q.bottom) > Math.max(r.top, q.top), scrolls: c.scrollWidth <= c.clientWidth + 1 || /auto|scroll/.test(getComputedStyle(c).overflowX) }; })()`);
        const copied = await p.copied();
        check(`generic: the composer's Copy and its Copied never cover the line, which fits or scrolls in its box [${at}]`, cm.said === 'Copied' && !cm.covers && cm.scrolls && j(copied) === j([cm.line + '\n']) && /--why 'one seed is not enough'/.test(cm.line), j({ cm, copied }));
      }
      await walkGuide(w, scheme, 'generic: ');
      // a short phone (390 x 667): no room under the Overview's list of the questions, which is lit down to the tooltip
      if (w < 960 && scheme === 'light') await walkGuide(w, scheme, 'generic: ', 667);
    }
    for (const w of WIDTHS) {
      const at = `${w}`;
      let s;
      // what waits for a confirmation copies only its 'rb confirm', and no decide line is copyable anywhere
      await open(w, 'light', 'to-confirm');
      await p.hookClipboard();
      const boxes = await p.ev(`Array.from(document.querySelectorAll('.waiting .cp')).map(b => b.closest('.cmdbox').querySelector('.cmd').textContent)`);
      for (let i = 0; i < boxes.length; i++) await p.clickEl(`document.querySelectorAll('.waiting .cp')[${i}]`);
      const copied = await p.copied();
      if (a.generic) {
        const parts = await p.ev(`Array.from(document.querySelectorAll('.waiting .wp')).map(e => [getComputedStyle(e).whiteSpace, e.textContent])`);
        const commands = await p.ev(`Array.from(document.querySelectorAll('.waiting pre.cmd')).map(e => [getComputedStyle(e).whiteSpace, e.scrollWidth, e.clientWidth])`);
        check(`generic: waiting parts preserve spaces and commands stay whole [${at}]`, parts.every(([white]) => /^(pre|pre-wrap|break-spaces)$/.test(white)) && commands.every(([white]) => /^(pre|nowrap)$/.test(white)), j({ parts, commands }));
      }
      check(`generic: each waiting item copies only its confirm [${at}]`, boxes.every(t => /^(cd \S.* && )?rb confirm \S+$/.test(t)) && j(copied) === j(boxes.map(b => b + '\n')), j({ boxes, copied }));
      const decide = await p.ev(`Array.from(document.querySelectorAll('.cp')).filter(b => !b.closest('.composer') && /rb decide|‹/.test((b.closest('.cmdbox') || {}).textContent || '')).length`);
      check(`generic: no decide line or placeholder is copyable outside the composer [${at}]`, decide === 0, j(decide));
      // the address: an extra '#', a name that is nowhere
      await open(w, 'light', '#overview');
      s = await st();
      check(`generic: '##overview' opens the Overview, written back with one '#' [${at}]`, s.view === 'overview' && (await p.ev('location.hash')) === '#overview', j(s));
      await open(w, 'light', 'nothing-here');
      const said = await p.ev(`(document.querySelector('.ibody .missing') || {}).textContent`);
      check(`generic: an address that names nothing says so [${at}]`, said === "nothing here is called 'nothing-here'", j(said));
      // the page's own theme and keys controls, and none added
      await open(w, 'light');
      const ctl = await p.ev(`[document.querySelectorAll('button[data-theme]').length, document.querySelectorAll('button[data-keys]').length]`);
      await p.clickEl(sel('button[data-theme]'));
      const th = await p.ev(`[document.documentElement.dataset.theme || null, document.querySelector('button[data-theme]').getAttribute('aria-label'), document.querySelector('button[data-theme]').dataset.state]`);
      await p.clickEl(sel('button[data-theme]')); await p.clickEl(sel('button[data-theme]'));
      await p.clickEl(sel('button[data-keys]'));
      const help = await p.ev(`[!document.querySelector('.help').hidden, document.querySelector('button[data-keys]').getAttribute('aria-expanded')]`);
      await p.key('Escape', 'Escape', 27);
      check(`generic: the page's one theme and one keys button work [${at}]`, j(ctl) === j([1, 1]) && j(th) === j(['light', 'Theme: light', 'light']) && j(help) === j([true, 'true']), j({ ctl, th, help }));
    }
  }

  // what rb open shows away from the start place, or at a past version, as its document says it: the title names the
  // place first, once, whatever view or card is open; a past version offers no command to copy, and no command is
  // selected whole; an address that names what is shown in other words is written as rb open names it; a refusal is
  // said until the next move
  if (a.shown && want('shown')) {
    const titled = `(() => { const b = document.querySelector('#scope .bar'), pre = b ? b.dataset.title : '';
      return { pre, title: document.title, once: !!pre && document.title.startsWith(pre) && document.title.split(pre).length === 2 }; })()`;
    for (const w of WIDTHS) for (const scheme of SCHEMES) {
      const at = `${w} ${scheme}`;
      await open(w, scheme);
      let t = await p.ev(titled);
      check(`shown: the title names the place first, once [${at}]`, t.once, j(t));
      await p.ev(`location.hash = '#runs'`); await sleep(200);
      t = await p.ev(titled);
      check(`shown: after a view change the title names it once [${at}]`, t.once, j(t));
      // a card, or where the page draws none yet, what the inspector says of an address that names nothing
      const card = (await p.ev(`(document.querySelector('main .card[id]') || {}).id || null`)) || 'nothing-here';
      await p.ev(`location.hash = ${j('#' + card)}`); await sleep(300);
      t = await p.ev(titled);
      const insp = await p.ev(`!!document.querySelector('.ibody > .card, .ibody .missing')`);
      check(`shown: with a card open the title names it once [${at}]`, insp && t.once, j({ card, insp, t }));
      const cmds = await p.ev(`(() => ({ cp: document.querySelectorAll('.cp').length, pre: Array.from(document.querySelectorAll('pre.cmd')).map(x => getComputedStyle(x).userSelect),
        withheld: (document.querySelector('#withheld .withheld') || {}).textContent || '' }))()`);
      if (a.shown === 'past') check(`shown: a past version offers no command to copy, nor selects one whole [${at}]`, cmds.cp === 0 && cmds.pre.every(u => u === 'text') && /^A past version offers no command/.test(cmds.withheld), j(cmds));
      else check(`shown: away, commands are offered [${at}]`, cmds.cp > 0 && !cmds.withheld, j(cmds));
      await open(w, scheme, 'runs&PLACE=x&place=');
      check(`shown: a tail that names what is shown in other words is written as rb open names it [${at}]`, (await p.ev('location.hash')) === '#runs', await p.ev('location.hash'));
      await open(w, scheme, 'overview&place=p_0000000000');
      await p.waitFor(`!!(document.querySelector('#where .failed') || {}).textContent`, 10000);
      const said = await p.ev(`document.querySelector('#where .failed').textContent`);
      await p.clickEl(`Array.from(document.querySelectorAll('#nav a[href^="#"]')).find(x => x.offsetParent !== null && x.getAttribute('href') !== '#overview')`);
      const after = await p.ev(`document.querySelector('#where .failed').textContent`);
      check(`shown: a refusal is said until the next move [${at}]`, /, live, instead\.$/.test(said) && after === '', j({ said, after }));
    }
  }
  // rb open's places, by real clicks: the Where picker shows another worktree's record, swapped in by the live module,
  // and the address keeps it; Back, a reload and a link show it again; a place rb does not list falls back to the
  // place rb open started on, live, saying why; a past version offers no command to copy
  if (a.places && a['copy-only'] && want('places')) {
    const copies = `document.querySelectorAll('.cp').length`;
    for (const w of WIDTHS) for (const scheme of SCHEMES) {
      const at = `${w} ${scheme}`;
      await open(w, scheme);
      const before = await p.ev(copies);
      await p.clickEl(sel('#where details.pick[data-pick="version"] > summary'));
      await p.clickEl(`document.querySelector('#where details.pick[data-pick="version"] a[data-at]:not([data-at=""])')`);
      await p.waitFor(`!!document.querySelector('#withheld .withheld')`, 15000);
      const after = await p.ev(copies);
      const past = await p.ev(`(() => { const e = document.querySelector('#withheld .withheld'); return { text: e?.textContent || '', commands: Array.from(document.querySelectorAll('pre.cmd')).map(e => getComputedStyle(e).userSelect), scope: document.querySelector('#scope')?.textContent || '', hash: location.hash }; })()`);
      check(`places: past commands cannot be copied [${at}]`, before > 0 && after === 0 && past.commands.every(s => s === 'text') && /^A past version offers no command/.test(past.text) && /read only Back to now$/.test(past.scope) && /&at=[0-9a-f]{40}/.test(past.hash), j({ before, after, past }));
      await p.clickEl(sel('#scope a.back'));
      await p.waitFor(`!document.querySelector('#withheld .withheld')`, 10000);
      const now = await p.ev(copies);
      check(`places: returning to now restores Copy [${at}]`, now === before && !(await p.ev(`location.hash.includes('&at=')`)), j({ before, now }));
    }
  }
  if (a.places && !a['copy-only'] && want('places')) {
    const where = `(() => { const s = document.querySelector('#where details.pick[data-pick="where"] > summary'), v = document.querySelector('#where details.pick[data-pick="version"] > summary');
      return { where: s ? s.textContent.trim() : null, version: v ? v.textContent.trim() : null, failed: (document.querySelector('#where .failed') || {}).textContent || '',
        hash: decodeURIComponent(location.hash.slice(1)), view: (document.querySelector('main .view.on') || {}).id || null, title: document.title,
        scope: (document.querySelector('#scope .bar') || {}).textContent || '', withheld: (document.querySelector('#withheld .withheld') || {}).textContent || '',
        copies: document.querySelectorAll('main .cp').length }; })()`;
    // the top bar from 960 up: its parts side by side, none over another, the project's id and the clock on one line
    // each, the pickers below the bar's first line under 1280 and cut short beside it from there
    const BAR = `(() => { const t = document.querySelector('header.top'), tr = t.getBoundingClientRect();
      const ks = Array.from(t.children).filter(c => c.getBoundingClientRect().width > 0).map(c => { const r = c.getBoundingClientRect(); return { k: c.className || c.tagName, l: r.left, r: r.right, t: r.top, b: r.bottom }; });
      const rows = {}; ks.forEach(k => { const y = Math.round((k.t + k.b) / 2 / 8); (rows[y] = rows[y] || []).push(k); });
      const over = []; Object.values(rows).forEach(row => row.sort((x, y) => x.l - y.l).forEach((k, n, a) => { if (n && k.l < a[n - 1].r - 0.5) over.push(a[n - 1].k + '/' + k.k); }));
      const inner = Array.from(t.querySelectorAll('.where > *')).filter(e => e.getBoundingClientRect().width > 0).map(e => e.getBoundingClientRect());
      const end = t.querySelector('.end').getBoundingClientRect();
      const under = inner.some(r => r.right > end.left + 0.5 && r.top < end.bottom && r.bottom > end.top);
      // (one line: the id's own span and the clock no taller than a line and a half)
      const lines = [t.querySelector('.oid') && t.querySelector('.oid').parentElement, t.querySelector('.end .stamp')].map(e => !e ? 0 : e.getBoundingClientRect().height / parseFloat(getComputedStyle(e).lineHeight === 'normal' ? 20 : getComputedStyle(e).lineHeight) > 1.5 ? 2 : 1);
      const outside = ks.filter(k => k.t < tr.top - 0.5 || k.b > tr.bottom + 0.5).map(k => k.k);
      // the space between the last picker (its ▾ included) and the first thing after it on its line, the clock
      const first = Array.from(t.querySelectorAll('.end > *')).map(e => e.getBoundingClientRect()).find(r => r.width > 0);
      const gaps = first ? inner.filter(r => r.top < first.bottom && r.bottom > first.top).map(r => Math.round(first.left - r.right)) : [];
      return { over, under, lines, outside, gap: gaps.length ? Math.min(...gaps) : null, height: Math.round(tr.height) }; })()`;
    for (const w of [960, 1000, 1050, 1279, 1280, 1440]) {
      await open(w, 'light');
      const bar = await p.ev(BAR);
      check(`places: the top bar's parts sit side by side, the id and the clock on one line each [${w}]`,
        !bar.over.length && !bar.under && bar.lines.every(n => n <= 1) && !bar.outside.length, j(bar));
      // from 1280 the pickers share the clock's line: a space after the Version picker's ▾ (D's take at 1440)
      if (w >= 1280) check(`places: a space between the Version picker and the clock [${w}]`, bar.gap !== null && bar.gap >= 6, j(bar));
      // from 1280 the bar says the place once, cut by nothing of rb open's: the Where button names it whole
      if (w >= 1280) {
        const cut = await p.ev(`Array.from(document.querySelectorAll('header.top .where *')).filter(e => e.getBoundingClientRect().width > 0 && e.scrollWidth > e.clientWidth + 1 && !e.closest('ul.picks')).map(e => e.className || e.tagName)`);
        const said = await p.ev(`[Array.from(document.querySelectorAll('header.top .where .pl')).filter(e => e.getBoundingClientRect().width > 0).length, (document.querySelector('#where details.pick[data-pick="where"] .lab') || {}).textContent]`);
        check(`places: from 1280 the bar names the place once and cuts nothing of it [${w}]`, cut.length === 0 && said[0] === 0 && !/\//.test(said[1] || '/'), j({ cut, said }));
      }
      // a long place (a deep checkout path): the Where picker's summary grows until the Version picker sits at the
      // screen's right edge, and its list, opened, stays on the screen
      await p.ev(`(() => { const s = document.querySelector('#where details.pick[data-pick="where"] > summary'), v = document.querySelector('#where details.pick[data-pick="version"] > summary');
        const W = document.documentElement.clientWidth; for (let n = 0; n < 400 && v.getBoundingClientRect().right < W - 30 && v.getBoundingClientRect().top < 80; n++) (s.querySelector('.lab') || s).append('x'); return true; })()`);
      await p.clickEl(sel('#where details.pick[data-pick="version"] > summary'));
      const list = await p.ev(`(() => { const l = document.querySelector('#where details.pick[data-pick="version"][open] > ul.picks'); if (!l) return null; const r = l.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), document.documentElement.clientWidth]; })()`);
      const ov = await p.ev(OVERFLOW);
      check(`places: the Version list of a long place stays on the screen [${w}]`, !!list && list[0] >= 0 && list[1] <= list[2] && ov.n === 0, j({ list, ov }));
    }
    for (const w of WIDTHS) for (const scheme of SCHEMES) {
      const at = `${w} ${scheme}`;
      await open(w, scheme);
      let s = await p.ev(where);
      check(`places: the Where picker names the place rb open started on [${at}]`, s.where && /^Where /.test(s.where) && !/exp-b/.test(s.where) && !s.failed, j(s));
      let o = await p.ev(OVERFLOW);
      check(`places: nothing past the screen with the pickers [${at}]`, o.n === 0 && o.scroll <= o.width, j(o));
      // open the picker by its summary, then choose the other worktree
      await p.clickEl(sel('#where details.pick[data-pick="where"] > summary'));
      o = await p.ev(OVERFLOW);
      check(`places: the open picker stays on the screen [${at}]`, o.n === 0 && o.scroll <= o.width, j(o));
      await p.clickEl(`Array.from(document.querySelectorAll('#where details.pick[data-pick="where"] a[data-place]')).find(a => /exp-b/.test(a.textContent))`);
      await p.waitFor(`/exp-b/.test((document.querySelector('#where details.pick[data-pick="where"] > summary') || {}).textContent || '')`, 10000);
      s = await p.ev(where);
      const pid = (s.hash.match(/&place=([^&]+)/) || [])[1];
      check(`places: choosing exp-b shows it, the address keeps it, the view stays [${at}]`, /exp-b/.test(s.where) && pid && s.view === 'overview' && s.hash.startsWith('overview&place=') && !s.failed, j(s));
      check(`places: the picker closes once a place is chosen [${at}]`, !(await p.ev(`!!document.querySelector('#where details.pick[open]')`)), '');
      if (a.wired) {
        check(`places: away, the scope bar says where, read only, with the way back [${at}]`, /^Viewing exp-b .* read only Back to /.test(s.scope) && /exp-b/.test(s.title), j(s));
        o = await p.ev(OVERFLOW);
        check(`places: nothing past the screen with the scope bar [${at}]`, o.n === 0 && o.scroll <= o.width, j(o));
      }
      // two quick choices: the place shown, chosen while another is on its way, is what stays
      await p.ev(`window.__fetch = window.fetch; window.__go = null; window.fetch = async (...x) => { if (String(x[0]).startsWith('/?place=')) await new Promise(r => { window.__go = r; }); return window.__fetch(...x); }`);
      await p.ev(`history.back()`);
      await p.waitFor(`!/exp-b/.test((document.querySelector('#where details.pick[data-pick="where"] > summary') || {}).textContent || 'exp-b')`, 10000);
      await p.ev(`document.querySelector('#where details.pick[data-pick="where"] a[data-place]:not([data-place=""])').click()`);
      await p.waitFor('!!window.__go', 10000);
      await p.ev(`document.querySelector('#where details.pick[data-pick="where"] a[data-place=""]').click()`);
      await sleep(300);
      await p.ev('window.__go()'); await sleep(1200);
      await p.ev('window.fetch = window.__fetch');
      s = await p.ev(where);
      check(`places: of two quick choices the last stays [${at}]`, !/exp-b/.test(s.where) && !/&place=/.test(s.hash), j(s));
      // to exp-b again, for Back below
      await p.clickEl(sel('#where details.pick[data-pick="where"] > summary'));
      await p.clickEl(`Array.from(document.querySelectorAll('#where details.pick[data-pick="where"] a[data-place]')).find(a => /exp-b/.test(a.textContent))`);
      await p.waitFor(`/exp-b/.test((document.querySelector('#where details.pick[data-pick="where"] > summary') || {}).textContent || '')`, 10000);
      // the browser's Back: the place it started on, live
      await p.ev('history.back()');
      await p.waitFor(`!/exp-b/.test((document.querySelector('#where details.pick[data-pick="where"] > summary') || {}).textContent || 'exp-b')`, 10000);
      s = await p.ev(where);
      check(`places: Back shows the place rb open started on, live [${at}]`, !/exp-b/.test(s.where) && !/&place=/.test(s.hash) && !s.scope, j(s));
      // a reload, or a link, that keeps the place shows it again
      await open(w, scheme, `overview&place=${pid}`);
      await p.waitFor(`/exp-b/.test((document.querySelector('#where details.pick[data-pick="where"] > summary') || {}).textContent || '')`, 10000);
      s = await p.ev(where);
      check(`places: a link that keeps the place shows it again [${at}]`, /exp-b/.test(s.where) && s.hash === `overview&place=${pid}`, j(s));
      // a reload keeps the entry's state: the place it names is shown again
      await p.ev('setTimeout(() => location.reload(), 50); true'); await sleep(300);
      await p.waitFor(`document.documentElement.classList.contains('ready') && /exp-b/.test((document.querySelector('#where details.pick[data-pick="where"] > summary') || {}).textContent || '')`, 10000);
      s = await p.ev(where);
      check(`places: a reload shows the place again [${at}]`, /exp-b/.test(s.where) && s.hash === `overview&place=${pid}`, j(s));
      // a place rb does not list: the place rb open started on, live, and why
      await open(w, scheme, 'overview&place=p_0000000000');
      await p.waitFor(`!!(document.querySelector('#where .failed') || {}).textContent`, 10000);
      s = await p.ev(where);
      check(`places: a place rb does not list falls back, saying why [${at}]`, /rb open shows .*, live, instead\.$/.test(s.failed) && s.hash === 'overview' && !/exp-b/.test(s.where), j(s));
      // a past version: chosen by the Version picker; no command to copy, with rb's reason and the way back
      await open(w, scheme);
      const copiesNow = (await p.ev(where)).copies;
      await p.clickEl(sel('#where details.pick[data-pick="version"] > summary'));
      o = await p.ev(OVERFLOW);
      check(`places: the open Version picker stays on the screen [${at}]`, o.n === 0 && o.scroll <= o.width, j(o));
      await p.clickEl(`document.querySelector('#where details.pick[data-pick="version"] a[data-at]:not([data-at=""])')`);
      await p.waitFor(`/[0-9a-f]{7}/.test((document.querySelector('#where details.pick[data-pick="version"] > summary') || {}).textContent || '')`, 15000);
      s = await p.ev(where);
      check(`places: a past version is shown by its commit, and the address keeps it [${at}]`, /^Version [0-9a-f]{12}$/.test(s.version) && /&at=[0-9a-f]{40}/.test(s.hash), j(s));
      const sha = (s.hash.match(/&at=([0-9a-f]{40})/) || [])[1];
      await p.ev('setTimeout(() => location.reload(), 50); true'); await sleep(300);
      await p.waitFor(`document.documentElement.classList.contains('ready') && /[0-9a-f]{12}/.test((document.querySelector('#where details.pick[data-pick="version"] > summary') || {}).textContent || '')`, 15000);
      s = await p.ev(where);
      check(`places: a reload shows the past version again [${at}]`, s.version === `Version ${sha.slice(0, 12)}` && s.hash === `overview&at=${sha}`, j(s));
      if (a.wired) {
        // the places a past version lists are the clone's, from the record rb open started on
        const labels = await p.ev(`Array.from(document.querySelectorAll('#where details.pick[data-pick="where"] li')).map(l => l.textContent)`);
        check(`places: a past version lists the clone's places, by name [${at}]`, labels.length === 2 && labels.some(l => /exp-b/.test(l)) && !labels.some(l => /\/rb\/views\//.test(l)) && /^Where \S/.test(s.where), j({ labels, s }));
        check(`places: a past version offers no command to copy [${at}]`, copiesNow > 0 && s.copies === 0 && /^A past version offers no command/.test(s.withheld) && / read only Back to now$/.test(s.scope), j({ copiesNow, s }));
        await p.clickEl(sel('#scope a.back'));
        await p.waitFor(`/^Version now$/.test((document.querySelector('#where details.pick[data-pick="version"] > summary') || {}).textContent.trim())`, 10000);
        s = await p.ev(where);
        check(`places: Back to now offers the commands again [${at}]`, s.copies === copiesNow && !s.withheld && !s.scope && !/&at=/.test(s.hash), j(s));
      }
    }
  }

  // the Overview's "changed <time>": relative on rb open's live view, its exact time kept as the hint; exact on a page
  if (want('generic')) {
    await open(1280, 'light', '');
    const ch = await p.ev(`(() => { const t = document.querySelector('#overview time[data-changed]'); return t && { text: t.textContent, title: t.title, days: (Date.now() - Date.parse(t.getAttribute('datetime'))) / 864e5, live: !!document.getElementById('rb-live'), past: !!document.querySelector('#withheld .withheld') }; })()`);
    // (past 30 days the live view says the exact time too, its hint the same)
    if (ch) check(`generic: the Overview says when the record changed, relative only on rb open's live view [1280]`,
      ch.live && !ch.past ? (ch.days > 30 ? / UTC$/.test(ch.text) && ch.title === ch.text : /^(just now|\d+ (minute|hour|day)s? ago)$/.test(ch.text) && / UTC$/.test(ch.title)) : / UTC$/.test(ch.text) && !ch.title, j(ch));
    if (ch && ch.live && !ch.past) {
      // a record last changed 40 days ago, drawn again as a live swap draws it: the exact time, as written
      const old = await p.ev(`(() => { const t = document.querySelector('#overview time[data-changed]'), at = new Date(Date.now() - 40 * 864e5).toISOString().slice(0, 16);
        t.setAttribute('datetime', at + 'Z'); t.textContent = at.replace('T', ' ') + ' UTC'; delete t.dataset.exact;
        document.dispatchEvent(new CustomEvent('rb:swapped', { cancelable: true })); return { text: t.textContent, want: at.replace('T', ' ') + ' UTC' }; })()`);
      check(`generic: past 30 days rb open's live view says the exact time it changed [1280]`, old.text === old.want, j(old));
    }
  }
  // print: every view in the document, no app chrome
  if (want('print')) {
    await open(1280, 'light', a.generic ? '' : 'run-runs1');
    await p.media({ type: 'print', scheme: 'light' });
    const folds = await p.ev(PRINTED);
    check('print, with the script: every view and every folded part', folds[0] === folds[1] && folds[2] === folds[3], j(folds));
    const pr = await p.ev(`(() => ({ views: Array.from(document.querySelectorAll('main .view')).filter(v => getComputedStyle(v).display !== 'none').length,
      all: document.querySelectorAll('main .view').length,
      insp: getComputedStyle(document.querySelector('aside.insp')).display, nav: getComputedStyle(document.querySelector('#nav')).display }))()`);
    check('print: every view prints, without the inspector or the nav', pr.views === pr.all && pr.insp === 'none' && pr.nav === 'none', j(pr));
    // the document's cards, which the app keeps out of every view on the screen, print with it
    const store = await p.ev(`(() => { const n = document.querySelector('main > .nodes'); return n ? [n.getBoundingClientRect().height > 0, n.querySelectorAll('.card').length] : null; })()`);
    if (a.generic) check('print: the document\'s cards print', !!store && store[0] && store[1] > 0, j(store));
    if (a.shots) await p.pdf(join(a.shots, 'print.pdf'));
    await p.media({ type: '', scheme: 'light' });
  }

  // no script: the whole document, each view in place, every anchor a plain link
  if (want('noscript')) {
    for (const w of WIDTHS) {
      await p.blank();
      await p.noScript(true);
      await p.viewport(w, 900);
      await p.goto(a.url);
      await p.settle({ quietMs: 100, script: false });
      const cls = await p.htmlClass();
      await p.noScript(false);
      const d = await p.ev(`(() => ({ views: Array.from(document.querySelectorAll('main .view')).filter(v => v.getBoundingClientRect().height > 0).length,
        all: document.querySelectorAll('main .view').length, insp: !!document.querySelector('aside.insp') }))()`);
      await p.media({ type: 'print', scheme: 'light' });
      const folds = await p.ev(PRINTED);
      await p.media({ type: '', scheme: 'light' });
      check(`print, no script: every view and every folded part [${w}]`, folds[0] === folds[1] && folds[2] === folds[3], j(folds));
      check(`no script: every view is in the document, no app [${w}]`, !/\bapp\b/.test(cls) && d.views === d.all && !d.insp, j({ cls, d }));
      const o = await p.ev(OVERFLOW);
      check(`no script: nothing past the screen [${w}]`, o.n === 0 && o.scroll <= o.width, j(o));
      // a deep link without the script: the browser opens the folds around it and it lands on screen
      await p.blank(); await p.noScript(true);
      // (on any page: the first id inside a fold, after its summary)
      const deep = a.generic ? await p.ev(`(() => { const e = document.querySelector('main details > :not(summary) [id], main details > [id]:not(summary)'); return e ? e.id : null; })()`) : 'c-c1:reading';
      if (deep) {
        await p.goto(a.url + '#' + encodeURIComponent(deep));
        await p.settle({ quietMs: 100, script: false });
        await p.noScript(false);
        const land = await p.ev(`(() => { const t = document.getElementById(${j(deep)}); const r = t.getBoundingClientRect(); return [t.closest('details').open, Math.round(r.top), innerHeight]; })()`);
        check(`no script: a deep link opens its folds and lands on screen [${w}]`, land[0] === true && land[1] >= 0 && land[1] < land[2], j({ deep, land }));
      } else await p.noScript(false);
    }
  }

  check('the page threw nothing and logged no error', !p.errors.length && !p.consoleMsgs.length, [...p.errors, ...p.consoleMsgs].join(' | '));
  const failed = results.filter(r => !r.pass);
  if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
  return { checks: results, chrome: browser.version };
}, { timeoutMs: Number(a.timeout || 240) * 1000, idleMs: Number(a.idle || 120) * 1000 });
