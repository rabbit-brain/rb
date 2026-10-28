// Rerun this research (the owner's Oct 26 feature, REPRODUCE-WORDS): a page with a clone address offers one prompt to
// copy into a coding agent. Without an address nothing of it shows; with the hub's rb-clone and rb-page metas the script
// fills the prompt's addresses, the bar's and the Overview's "Rerun this research" open the panel over the page, and
// Copy prompt puts the exact prompt on the clipboard ("Copied" for 2 s), sending nothing; Esc, Close and a click outside
// close it and focus goes back to the link. A reader copies it too. A copy that fails says "Select the text and copy it"
// with the text selected. At a past version none of it is there. Without a script the prompt is selectable text. Each
// page at 1440 and 390, light and dark, by real clicks and keys.
//   node tests/app/rerun.mjs --none <url> --meta <url> --expect <json file> [--reader <url>] [--past <url>]
//     [--plain <url>] [--hubnone <url>] [--bad <url>] [--query <url>] [--pagequery <url>] [--cloneonly <url>] [--spans <url>]
//     [--framed <url>] [--framednone <url>] [--shots <dir>] [--timeout <s>]
// --none: a page with no clone address; --meta: the same page with the rb-clone and rb-page metas; --reader: with them
// and the reader meta; --past: with them at a past version; --plain: the page as rb drew it, its own addresses, opened
// with its script off; --hubnone: in the hub (a viewer meta) with no rb-clone; --bad: in the hub, metas that are no
// https address; --query: in the hub, an rb-clone that holds a query; --pagequery: an rb-page that holds one; --cloneonly: rb-clone with no rb-page, opened with shell characters in its query; --framed: a host page
// framing --meta's page as the hub does (sandbox="allow-scripts"), whose probe answers the host's messages. --expect: {"prompt", "experiment", "clone", "cmds", "card", "plain", "cloneonly", "rows", "bar"}, the
// exact texts Copy gives (each without its last newline; cmds the panel's commands by hand, in order, however many), the card whose Reproduce holds the experiment's prompt, and
// how many runs' rows (class rr) --hubnone holds; bar false where the page's own bar is folded (in the hub).
// The last line on stdout is JSON: {"ok", "checks": [{"name", "pass", "detail"}]}; exit 0, 1 on a failed check, 3 with
// no Chrome.
import { readFileSync } from 'node:fs';
import { findChrome, launchChrome, parseArgs, progress, run, sleep } from './cdp.mjs';

const a = parseArgs(process.argv.slice(2));
if (!a.none || !a.meta || !a.expect) { process.stderr.write('give --none, --meta and --expect\n'); process.exit(2); }
const want = JSON.parse(readFileSync(a.expect, 'utf8'));
const results = [];
const check = (name, pass, detail = '') => { results.push({ name, pass: !!pass, detail }); progress(name); if (!pass) process.stderr.write(`FAIL ${name}: ${detail}\n`); };
const j = x => JSON.stringify(x);
const sel = s => `document.querySelector(${j(s)})`;

// every request the page makes, counted before the app starts
const NET = `(() => { window.__net = 0; const tick = () => { window.__net++; };
  const f = window.fetch; window.fetch = (...x) => { tick(); return f ? f(...x) : Promise.reject(new Error('no')); };
  const xo = XMLHttpRequest.prototype.open; XMLHttpRequest.prototype.open = function (...x) { tick(); return xo.apply(this, x); };
  if (navigator.sendBeacon) { const sb = navigator.sendBeacon.bind(navigator); navigator.sendBeacon = (...x) => { tick(); return sb(...x); }; }
  const W = window.WebSocket; window.WebSocket = function (...x) { tick(); return new W(...x); }; return true; })()`;
// what of the feature shows
const SHOWN = `(() => { const vis = e => e.checkVisibility() && e.getBoundingClientRect().width > 0;
  const d = document.querySelector('dialog.rerundlg');
  const P = 'a[href="#rerun"][data-rerun], #rerun, .rrx, #reproduce.rrun, [data-rerun]';
  return { parts: document.querySelectorAll(P).length, shown: Array.from(document.querySelectorAll(P)).filter(vis).length,
    rows: Array.from(document.querySelectorAll('.rr')).filter(e => !e.matches(P)).length,
    bar: !!Array.from(document.querySelectorAll('header a[href="#rerun"]')).find(vis), ov: !!Array.from(document.querySelectorAll('#overview a[href="#rerun"]')).find(vis),
    dialog: !!d, open: !!(d && d.open), panel: !!(document.getElementById('rerun') && vis(document.getElementById('rerun'))),
    focus: document.activeElement ? (document.activeElement.className + ' ' + document.activeElement.textContent.trim()).slice(0, 60) : null,
    fills: Array.from(document.querySelectorAll('[data-fill]')).map(e => e.textContent) }; })()`;
// the panel's box and what runs past it or past the screen
const FIT = `(() => { const d = document.querySelector('dialog.rerundlg'), r = d.getBoundingClientRect(), out = [];
  d.querySelectorAll('*').forEach(e => { if (!e.checkVisibility() || e.matches('.sr')) return; const b = e.getBoundingClientRect(); if (b.width && (b.right > r.right + 0.5 || b.left < r.left - 0.5)) out.push(e.tagName + '.' + e.className + ' ' + Math.round(b.right)); if (e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'auto' && getComputedStyle(e).overflowX !== 'scroll' && e.tagName !== 'BUTTON') out.push('scrolls ' + e.tagName + '.' + e.className); });
  return { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), w: innerWidth, h: innerHeight, out: out.slice(0, 6),
    page: document.documentElement.scrollWidth }; })()`;
// what of the top bar the open panel's box lies over, the dialog's top, and the bottom of the bar's Open calls
const OVER = `(() => { const d = document.querySelector('dialog.rerundlg').getBoundingClientRect(), hit = [];
  const over = b => b.width && b.height && b.bottom > d.top && b.top < d.bottom && b.right > d.left && b.left < d.right;
  // the bar's leaves, and the navigator's links that sit up in the bar's band (its tabs, under 960 or in the hub's slim bar)
  document.querySelectorAll('header.top *, nav.nav a, nav.nav a *').forEach(e => { if (!e.checkVisibility() || e.children.length) return; const b = e.getBoundingClientRect();
    if ((e.closest('header.top') || b.top < 140) && over(b)) hit.push(e.textContent.trim()); });
  const oc = Array.from(document.querySelectorAll('header.top a, header.top span, nav.nav a')).find(e => /Open calls|Your call/.test(e.textContent) && e.checkVisibility() && e.getBoundingClientRect().top < 140);
  return { hit: hit.slice(0, 6), top: Math.round(d.top), calls: oc ? Math.round(oc.getBoundingClientRect().bottom) : null }; })()`;
// each command box's Copy against the box drawn round the command (the box or the first thing in it with a border): wholly
// inside it, 6 px or more clear of its border on every side (5 px under a top border reads as sitting on it), the
// command's text clear of it; or wholly outside it
const CPS = `Array.from(document.querySelectorAll('dialog.rerundlg .cmdbox > .cp')).filter(b => b.checkVisibility()).map(btn => {
  const cb = btn.parentElement, drawn = [cb, ...cb.querySelectorAll('*')].find(e => e !== btn && parseFloat(getComputedStyle(e).borderTopWidth) > 0) || cb;
  const r = drawn.getBoundingClientRect(), cs = getComputedStyle(drawn), bt = parseFloat(cs.borderTopWidth), br = parseFloat(cs.borderRightWidth), b = btn.getBoundingClientRect();
  const pre = cb.querySelector('.cmd'), rg = document.createRange(); rg.selectNodeContents(pre);
  const meets = (x, y) => x.right > y.left && x.left < y.right && x.bottom > y.top && x.top < y.bottom;
  const text = Array.from(rg.getClientRects()).some(t => t.width && meets(t, b));
  const bb = parseFloat(cs.borderBottomWidth), inside = b.top >= r.top + bt + 6 && b.right <= r.right - br - 6 && b.left >= r.left && b.bottom <= r.bottom - bb - 6;
  const outside = !meets(b, r);
  return { ok: (inside && !text) || outside, btn: [b.top, b.right, b.bottom].map(x => +x.toFixed(1)), box: [r.top, r.right, r.bottom].map(x => +x.toFixed(1)), bt, text }; })`;
const LABEL = s => `(() => { const b = ${sel(s)}; return b ? b.textContent.trim() : null; })()`;

// the link under the page's title on a hub page: right after the title, inside the first screen, and it opens the panel,
// Esc closing it with focus back on the link
async function titleLink(p, w, scheme) {
  const at = `${w} ${scheme}`;
  await open(p, a.meta, w, scheme);
  const t = await p.ev(`(() => { const l = document.querySelector('#overview > p.rrhd > a[href="#rerun"]'); if (!l) return null; const r = l.getBoundingClientRect();
    return { vis: l.checkVisibility() && r.width > 0, after: l.parentElement.previousElementSibling && l.parentElement.previousElementSibling.matches('h1'), top: Math.round(r.top), bottom: Math.round(r.bottom), h: innerHeight, y: scrollY,
      text: l.textContent, main: document.getElementById('main').scrollTop }; })()`);
  await p.clickEl(`document.querySelector('#overview > p.rrhd > a[href="#rerun"]')`);
  const o = await p.ev(SHOWN);
  await p.key('Escape', 'Escape', 27);
  const c = await p.ev(SHOWN);
  check(`rerun: on a hub page a link under the title, inside the first screen, opens the panel [${at}]`, t && t.vis && t.after && t.text === 'Rerun this research' && t.top >= 0 && t.bottom <= t.h && o.open && o.panel && !c.open && /\brrb\b.*Rerun this research$/.test(c.focus || ''), j({ t, o: o.open, c: c.open, focus: c.focus }));
}

async function open(p, url, w, scheme, hash = 'overview') {
  await p.blank(); await p.viewport(w, w < 600 ? 844 : 900); await p.media({ type: '', scheme });
  await p.goto(url + '#' + hash);
  if (!(await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000))) throw new Error('never ready: ' + url);
  await p.ev(`(() => { try { localStorage.setItem('rb.guide.1', 'ended'); } catch (e) {} const g = document.querySelector('.guide:not([hidden]) .gskip'); if (g) g.click(); return true; })()`);
  await p.settle({ quietMs: 100 }); await sleep(200);
  await p.hookClipboard();
}

await run(async () => {
  const b = await launchChrome({ chrome: findChrome() });
  const p = await b.page();
  for (const w of [1440, 390]) for (const scheme of ['light', 'dark']) {
    const at = `${w} ${scheme}`;
    // no clone address: nothing of it shows, and an address naming the panel opens nothing
    await open(p, a.none, w, scheme);
    const none = await p.ev(SHOWN);
    check(`rerun: without a clone address nothing of it shows [${at}]`, none.shown === 0 && !none.dialog, j(none));
    await open(p, a.none, w, scheme, 'rerun');
    const none2 = await p.ev(SHOWN);
    check(`rerun: without a clone address #rerun opens nothing [${at}]`, none2.shown === 0 && !none2.open, j(none2));

    check(`rerun: with no rb-clone meta there is no link under the title [${at}]`, !(await p.ev(`!!document.querySelector('#overview .rrhd')`)), '');
    // a hub page (its rb-clone meta): a link right under the title, inside the first screen, opens the panel by a real click
    for (const tw of w === 1440 ? [1440, 1280] : [w]) await titleLink(p, tw, scheme);

    // the hub's metas: the bar's link and the Overview's show; the bar's opens the panel by a real click
    await p.blank(); await p.viewport(w, w < 600 ? 844 : 900);
    await open(p, a.meta, w, scheme);
    await p.ev(NET);
    const s0 = await p.ev(SHOWN);
    // at 390 the bar has no room for it, and inside the hub (want.bar false) the page's own bar is folded away: the
    // Overview's Reproduce place offers it there
    const inBar = w >= 640 && want.bar !== false;
    const OPENER = inBar ? `document.querySelector('header a[href="#rerun"]')` : `document.querySelector('#overview a.rrov[href="#rerun"]')`;
    check(`rerun: with the rb-clone meta the bar and the Overview offer it, the panel closed [${at}]`, (inBar ? s0.bar : !s0.bar) && s0.ov && s0.dialog && !s0.open && !s0.panel && s0.fills.every(f => !f.includes('‹')), j(s0));
    await p.clickEl(OPENER);
    const s1 = await p.ev(SHOWN);
    check(`rerun: the bar's link (at 390 the Overview's) opens the panel, focus on Copy prompt [${at}]`, s1.open && s1.panel && /\bcp\b.*Copy prompt$/.test(s1.focus || ''), j(s1));
    const copies = await p.ev(`Array.from(document.querySelectorAll('#rerun .cp')).filter(b => b.checkVisibility()).map(b => b.textContent.trim())`);
    check(`rerun: the panel offers Copy prompt once and a Copy for each command [${at}]`, want.cmds.length >= 2 && j(copies) === j(['Copy prompt', ...want.cmds.map(() => 'Copy')]), j(copies));
    const hdr = await p.ev(`(() => { const h = document.querySelector('header.top'); return h ? [h.scrollWidth, h.clientWidth] : null; })()`);
    check(`rerun: the top bar keeps to the screen with it [${at}]`, !hdr || hdr[0] <= hdr[1] + 1, j(hdr));
    const fit = await p.ev(FIT);
    check(`rerun: the panel fits the screen and nothing runs past it [${at}]`, fit.left >= 0 && fit.right <= fit.w && fit.top >= 0 && fit.bottom <= fit.h && !fit.out.length && fit.page <= fit.w, j(fit));
    // every command box shows its whole line, wrapped where it must: none scrolls sideways with its end out of sight
    const cmds = await p.ev(`Array.from(document.querySelectorAll('dialog.rerundlg .cmd')).filter(e => e.checkVisibility()).map(e => [e.textContent.trim(), e.scrollWidth, e.clientWidth])`);
    check(`rerun: each command box shows its whole line [${at}]`, cmds.length >= 2 && cmds.every(c => c[1] <= c[2] + 1), j(cmds));
    // each command's Copy sits wholly inside its box's top-right corner, clear of its border and of the command's text,
    // or wholly outside the box: never on its border
    const cps = await p.ev(CPS);
    check(`rerun: each command's Copy sits inside its box's corner or outside it, never on the border [${at}]`, cps.length >= 2 && cps.every(c => c.ok), j(cps));
    // on a wide screen the panel never covers the top bar: what it says ("Open calls" and its count among it) stays in sight above it
    const bar = await p.ev(OVER);
    if (w >= 960) check(`rerun: the panel covers nothing of the top bar [${at}]`, !bar.hit.length && (bar.calls === null || bar.calls <= bar.top), j(bar));
    if (a.shots) {
      await p.shot(`${a.shots}/rerun-${w}-${scheme}.png`);
      // and scrolled to its end, where the commands are
      await p.ev(`(() => { const d = document.querySelector('dialog.rerundlg'); d.scrollTop = d.scrollHeight; return true; })()`); await sleep(150);
      await p.shot(`${a.shots}/rerun-${w}-${scheme}-end.png`);
      await p.ev(`(() => { document.querySelector('dialog.rerundlg').scrollTop = 0; return true; })()`);
    }
    await p.clickEl(`document.querySelector('#rerun [data-copybtn="rerun-prompt"] .cp')`);
    const c1 = await p.copied(), l1 = await p.ev(LABEL('#rerun [data-copybtn="rerun-prompt"] .cp'));
    let real = null; try { real = await p.ev(`navigator.clipboard.readText()`); } catch {}
    const said1 = await p.ev(`(() => { const r = document.querySelector('dialog.rerundlg [aria-live]'); return r ? r.textContent : null; })()`);
    check(`rerun: Copied is said aloud from inside the open panel [${at}]`, said1 === 'Copied', j(said1));
    check(`rerun: Copy prompt puts the exact prompt on the clipboard and says Copied [${at}]`, c1.length === 1 && c1[0] === want.prompt + '\n' && l1 === 'Copied' && (real === null || typeof real !== 'string' || real === c1[0]), j({ c1, l1, real: typeof real === 'string' ? real.slice(0, 80) : real }));
    await sleep(1300);
    const l15 = await p.ev(LABEL('#rerun [data-copybtn="rerun-prompt"] .cp'));
    await sleep(900);
    const l2 = await p.ev(LABEL('#rerun [data-copybtn="rerun-prompt"] .cp'));
    check(`rerun: Copied says so for 2 s, then the button reads Copy prompt again [${at}]`, l15 === 'Copied' && l2 === 'Copy prompt', j({ l15, l2 }));
    // each command for people without an agent copies alone, whatever its place: its Copy against the command it sits on
    const BOXES = `Array.from(document.querySelectorAll('dialog.rerundlg .cmdbox')).filter(b => !b.matches('.rpbox'))`;
    const sits = await p.ev(`${BOXES}.map(b => b.querySelector(':scope > pre.cmd').textContent)`);
    const each = [];
    for (let i = 0; i < sits.length; i++) {
      await p.clickEl(`${BOXES}[${i}].querySelector(':scope > .cp')`);
      const c = await p.copied();
      each.push({ sits: sits[i], copied: c.length === 2 + i ? c[c.length - 1] : c });
    }
    check(`rerun: each command's Copy copies the command it sits on alone [${at}]`, j(sits) === j(want.cmds) && each.every(e => e.copied === e.sits + '\n'), j(each));
    const cl = each.find(e => e.sits.startsWith('rb clone '));
    check(`rerun: the rb clone line copies the clone command alone [${at}]`, !!cl && cl.copied === cl.sits + '\n' && (cl.sits === want.clone || cl.sits.startsWith(want.clone + ' ')), j(cl));
    // keys do nothing while the panel is open; Esc closes it and focus goes back to the link
    const st0 = await p.ev(`location.hash`);
    await p.key('j', 'KeyJ', 74, 'j');
    check(`rerun: single keys do nothing while the panel is open [${at}]`, (await p.ev(`location.hash`)) === st0 && (await p.ev(SHOWN)).open, st0);
    await p.key('Escape', 'Escape', 27);
    const s2 = await p.ev(SHOWN);
    check(`rerun: Esc closes the panel and focus goes back to the link [${at}]`, !s2.open && /\brrb\b/.test(s2.focus || ''), j(s2));
    // the Overview's link opens it; Close closes it; a click outside closes it
    await p.clickEl(`document.querySelector('#overview a.rrov[href="#rerun"]')`);
    const s3 = await p.ev(SHOWN);
    await p.clickEl(`document.querySelector('dialog.rerundlg .rerun-x')`);
    const s4 = await p.ev(SHOWN);
    check(`rerun: the Overview's link opens it and Close closes it, focus back on the link [${at}]`, s3.open && !s4.open && /\brrb\b/.test(s4.focus || ''), j({ s3, s4 }));
    await p.clickEl(OPENER);
    // a selection dragged from the prompt out past the panel's edge leaves it open
    const pr = await p.ev(`(() => { const r = document.querySelector('#rerun-prompt').getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + 20 }; })()`);
    for (const [type, x, y] of [['mouseMoved', pr.x, pr.y], ['mousePressed', pr.x, pr.y], ['mouseMoved', 4, pr.y + 40], ['mouseMoved', 3, (w < 600 ? 840 : 896)], ['mouseReleased', 3, (w < 600 ? 840 : 896)]])
      await p.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1, ...(type === 'mouseMoved' ? { buttons: type === 'mouseMoved' && x !== pr.x ? 1 : 0 } : {}) });
    await sleep(300);
    const drag = await p.ev(`({ open: document.querySelector('dialog.rerundlg').open, sel: String(getSelection()).length })`);
    check(`rerun: a selection dragged out of the prompt past the panel's edge leaves it open [${at}]`, drag.open && drag.sel > 0, j(drag));
    const box = await p.ev(FIT);
    await p.clickAt(Math.max(2, box.left - 4 > 2 ? box.left / 2 : 2), Math.min(box.h - 2, box.bottom + (box.h - box.bottom) / 2));
    const s5 = await p.ev(SHOWN);
    check(`rerun: a click outside the panel closes it [${at}]`, !s5.open && box.bottom < box.h - 2, j({ box, s5 }));
    // Back while the panel is open: it closes, and the view goes back as the address says
    await p.ev(`location.hash = ${j('#' + want.card)}`); await sleep(400);
    await p.ev(`location.hash = '#rerun'`); await sleep(400);
    const before = await p.ev(`({ open: document.querySelector('dialog.rerundlg').open, hash: location.hash })`);
    await p.ev(`history.back()`); await sleep(600);
    const bk = await p.ev(`({ open: document.querySelector('dialog.rerundlg').open, hash: location.hash })`);
    check(`rerun: Back while the panel is open closes it [${at}]`, before.open && before.hash === '#' + want.card && !bk.open, j({ before, bk }));
    // an experiment's own prompt, from its Reproduce place in its card
    await p.ev(`location.hash = ${j('#' + want.card)}`); await sleep(500);
    // its Reproduce place may be a fold: opened by its summary, as anyone opens it
    const fold = `(() => { const s = Array.from(document.querySelectorAll('[data-copybtn$=":prompt"]')).find(x => x.closest('.view.on, aside.insp')); const d = s && s.closest('details'); return d && !d.open ? d.querySelector(':scope > summary') : null; })()`;
    if (await p.ev(`!!${fold}`)) await p.clickEl(fold);
    const xb = `Array.from(document.querySelectorAll('.cp')).find(b => b.textContent.trim() === 'Copy prompt for this experiment' && b.checkVisibility())`;
    const has = await p.ev(`!!(${xb})`);
    if (has) await p.clickEl(xb);
    const c3 = await p.copied();
    check(`rerun: an experiment's Copy prompt for this experiment copies its own prompt [${at}]`, has && c3[c3.length - 1] === want.experiment + '\n', j({ has, last: c3[c3.length - 1] }));
    const net = await p.ev(`window.__net`);
    check(`rerun: nothing is sent [${at}]`, net === 0, String(net));

    // a copy that fails says so and selects the text
    await open(p, a.meta, w, scheme);
    await p.ev(`(() => { navigator.clipboard.writeText = () => Promise.reject(new Error('denied')); document.execCommand = () => false; return true; })()`);
    await p.clickEl(OPENER);
    await p.clickEl(`document.querySelector('#rerun [data-copybtn="rerun-prompt"] .cp')`);
    const f1 = await p.ev(`({ label: ${LABEL('#rerun [data-copybtn="rerun-prompt"] .cp')}, sel: String(getSelection()) })`);
    check(`rerun: a copy that fails says Select the text and copy it, the prompt selected [${at}]`, f1.label === 'Select the text and copy it' && f1.sel.replace(/\s+$/, '') === want.prompt, j({ label: f1.label, sel: f1.sel.slice(0, 60) }));

    // an address naming the panel opens it over the Overview, the guide not shown over it on a first visit
    await open(p, a.meta, w, scheme);
    await p.ev(`(() => { try { localStorage.removeItem('rb.guide.1'); } catch (e) {} return true; })()`);
    await p.blank(); await p.goto(a.meta + '#rerun');
    await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000); await p.settle({ quietMs: 100 }); await sleep(400);
    const s6 = await p.ev(SHOWN), g6 = await p.ev(`!!Array.from(document.querySelectorAll('.guide')).find(g => !g.hidden && g.checkVisibility())`);
    check(`rerun: an address #rerun opens the panel over the Overview, focus on Copy prompt, no guide over it [${at}]`, s6.open && !g6 && /Copy prompt$/.test(s6.focus || '') && (await p.ev(`document.querySelector('#main .view.on').id`)) === 'overview', j({ s6, g6 }));
    await p.ev(`(() => { try { localStorage.setItem('rb.guide.1', 'ended'); } catch (e) {} return true; })()`);
    await p.key('Escape', 'Escape', 27);
    // followed once the page is up: it opens, and the address stays where it was
    await p.ev(`location.hash = '#rerun'`); await sleep(400);
    const s6b = await p.ev(SHOWN), h6 = await p.ev(`location.hash`);
    check(`rerun: #rerun followed after load opens the panel and keeps the address [${at}]`, s6b.open && h6 !== '#rerun', j({ open: s6b.open, h6 }));
    // print with the panel open: closed for the print, it prints in its place, not over the page; open again after
    await p.media({ type: 'print', scheme }); await sleep(300);
    const pr6 = await p.ev(`(() => { const d = document.querySelector('dialog.rerundlg'), e = document.getElementById('rerun'); return { open: d.open, vis: e.checkVisibility() && e.getBoundingClientRect().height > 0 }; })()`);
    await p.media({ type: '', scheme }); await sleep(300);
    const pr7 = await p.ev(SHOWN);
    check(`rerun: printed with the panel open, it prints in its place and opens again after [${at}]`, !pr6.open && pr6.vis && pr7.open, j({ pr6, open: pr7.open }));
    await p.key('Escape', 'Escape', 27);
    if (a.spans) {
      // rb's own addresses each in a span that says what it is (data-fill): the hub's go in the spans
      await open(p, a.spans, w, scheme);
      await p.clickEl(`Array.from(document.querySelectorAll('a[href="#rerun"]')).find(l => l.checkVisibility())`);
      await p.clickEl(`document.querySelector('#rerun [data-copybtn="rerun-prompt"] .cp')`);
      const ns = await p.copied();
      check(`rerun: with rb's addresses in spans of their own, the hub's go in the spans [${at}]`, ns[ns.length - 1] === want.prompt + '\n', j(ns));
    }
    if (a.pagequery) {
      // a page's address may hold a query (only a bundle's may not): the hub's goes in the prompt whole
      await open(p, a.pagequery, w, scheme);
      await p.clickEl(`Array.from(document.querySelectorAll('a[href="#rerun"]')).find(l => l.checkVisibility())`);
      await p.clickEl(`document.querySelector('#rerun [data-copybtn="rerun-prompt"] .cp')`);
      const pq = await p.copied();
      check(`rerun: an rb-page meta holding a query goes in the prompt whole [${at}]`, pq[pq.length - 1] === want.pagequery + '\n', j(pq));
    }
    if (a.cloneonly) {
      // rb-clone with no rb-page: the page's address only where it is one; a query holding shell characters is not
      await open(p, a.cloneonly, w, scheme);
      await p.clickEl(`Array.from(document.querySelectorAll('a[href="#rerun"]')).find(l => l.checkVisibility())`);
      await p.clickEl(`document.querySelector('#rerun [data-copybtn="rerun-prompt"] .cp')`);
      const co = await p.copied();
      check(`rerun: an address opened with shell characters in it is not put in the prompt [${at}]`, co[co.length - 1] === want.cloneonly + '\n', j(co));
    }

    if (a.reader) {
      await open(p, a.reader, w, scheme);
      // in the hub the page's own bar is folded: the opener shown
      await p.clickEl(`Array.from(document.querySelectorAll('a[href="#rerun"]')).find(l => l.checkVisibility())`);
      const r1 = await p.ev(SHOWN), btn = `document.querySelector('#rerun [data-copybtn="rerun-prompt"] .cp')`;
      const vis = await p.ev(`!!(${btn} && ${btn}.checkVisibility())`);
      if (vis) await p.clickEl(btn);
      const rc = await p.copied();
      check(`rerun: a reader opens it and copies the prompt too [${at}]`, r1.open && vis && rc[0] === want.prompt + '\n', j({ r1, vis, rc }));
    }
    if (a.past) {
      await open(p, a.past, w, scheme);
      const s7 = await p.ev(SHOWN);
      check(`rerun: at a past version none of it is there, the metas whatever [${at}]`, s7.parts === 0 && !s7.dialog, j(s7));
    }
    if (a.hubnone) {
      await open(p, a.hubnone, w, scheme);
      const s9 = await p.ev(SHOWN);
      check(`rerun: in the hub with no rb-clone meta none of it is there, a run's row kept [${at}]`, s9.parts === 0 && s9.rows === want.rows && !s9.dialog, j({ s9, rows: want.rows }));
    }
    if (a.bad) {
      await open(p, a.bad, w, scheme);
      const s8 = await p.ev(SHOWN);
      check(`rerun: in the hub, an rb-clone meta that is no https address shows nothing [${at}]`, s8.shown === 0 && !s8.dialog, j(s8));
    }
    if (a.query) {
      // the hub's bundle addresses hold no query (/s/<link>/bundle.zip, /demo/<slug>/bundle.zip): one that does is no address of its
      await open(p, a.query, w, scheme);
      const s10 = await p.ev(SHOWN);
      check(`rerun: in the hub, an rb-clone meta holding a query shows nothing [${at}]`, s10.shown === 0 && !s10.dialog, j(s10));
    }
  }
  // between 640 and 1280 a top bar shows (in the hub, its slim bar with Open calls): the panel opens under it
  for (const w of [1024, 960, 800]) {
    const at = `${w} light`;
    await open(p, a.meta, w, 'light');
    await p.clickEl(`Array.from(document.querySelectorAll('a[href="#rerun"]')).find(l => l.checkVisibility())`);
    const o = await p.ev(OVER), fit = await p.ev(FIT);
    check(`rerun: the panel covers nothing of the top bar [${at}]`, !o.hit.length && (o.calls === null || o.calls <= o.top), j(o));
    check(`rerun: the panel fits the screen and nothing runs past it [${at}]`, fit.left >= 0 && fit.right <= fit.w && fit.top >= 0 && fit.bottom <= fit.h && !fit.out.length, j(fit));
    if (a.shots) await p.shot(`${a.shots}/rerun-${w}-light.png`);
    await p.key('Escape', 'Escape', 27);
  }
  if (a.framed) {
    // in the hub's sandboxed frame (no clipboard permission, no storage): Copy prompt in the open panel copies the prompt
    for (const w of [1440, 390]) {
      await p.blank(); await p.viewport(w, w < 600 ? 844 : 900); await p.media({ type: '', scheme: 'light' });
      await p.goto(a.framed);
      await p.ev(`navigator.clipboard.writeText('SENTINEL').then(() => true, () => false)`);
      const ask = async (what) => { await p.ev(`(() => { document.body.removeAttribute('data-probe'); document.querySelector('iframe').contentWindow.postMessage(${j(what)}, '*'); return true; })()`);
        const r = await p.waitFor(`document.body.getAttribute('data-probe')`, 8000); return r ? JSON.parse(r) : null; };
      await p.waitFor(`document.body.getAttribute('data-probe') === '"ready"'`, 15000);
      let r = await ask('opener');
      if (r && r.x) await p.clickAt(r.x, r.y);
      r = await ask('copy');
      if (r && r.x) await p.clickAt(r.x, r.y);
      await sleep(300);
      const label = await ask('label');
      const focus = await ask('focus');
      check(`rerun: in the hub's sandboxed frame focus stays on Copy prompt after it copies [${w}]`, /^cp\b.*\b(Copy prompt|Copied)$/.test(focus || ''), j(focus));
      let clip = null; try { clip = await p.ev(`navigator.clipboard.readText()`); } catch {}
      check(`rerun: in the hub's sandboxed frame Copy prompt copies the prompt itself [${w}]`, label === 'Copied' && clip === want.prompt + '\n', j({ label, clip: typeof clip === 'string' ? clip.slice(0, 80) : clip, r }));
    }
  }
  if (a.framednone) {
    // a page the hub frames with neither a viewer nor an rb-clone meta: none of it
    await p.blank(); await p.viewport(1440, 900);
    await p.goto(a.framednone);
    const ok = await p.waitFor(`document.body.getAttribute('data-probe') === '"ready"'`, 15000);
    await p.ev(`(() => { document.body.removeAttribute('data-probe'); document.querySelector('iframe').contentWindow.postMessage('parts', '*'); return true; })()`);
    const r = await p.waitFor(`document.body.getAttribute('data-probe')`, 8000);
    check(`rerun: in the hub's frame with no rb-clone meta none of it is there`, ok && r === '0', j(r));
  }
  // print: the panel is part of the page
  await open(p, a.meta, 1440, 'light');
  await p.media({ type: 'print', scheme: 'light' });
  const pr = await p.ev(`(() => { const e = document.getElementById('rerun'); return !!(e && e.checkVisibility() && e.getBoundingClientRect().height > 0); })()`);
  check(`rerun: print shows the panel as part of the page`, pr, String(pr));
  await p.media({ type: '', scheme: 'light' });
  if (a.plain) {
    await p.blank(); await p.noScript(true);
    // with no script the bar's link goes to the panel, which shows as its target
    await p.goto(a.plain + '#rerun');
    await p.settle({ quietMs: 200, script: false }); await sleep(300);
    const np = await p.ev(`(() => { const e = document.getElementById('rerun-prompt'); if (!e) return null; const r = e.getBoundingClientRect(); return { vis: e.checkVisibility() && r.height > 0, sel: getComputedStyle(e).userSelect, text: e.textContent.replace(/\\s+$/, '') }; })()`);
    check(`rerun: without a script the prompt is selectable text, whole`, np && np.vis && np.sel !== 'none' && np.text === want.plain, j(np));
    await p.noScript(false);
  }
  const failed = results.filter(r => !r.pass);
  if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
  return { checks: results };
}, { timeoutMs: (Number(a.timeout) || 300) * 1000, idleMs: (Number(a.idle) || 120) * 1000 });
