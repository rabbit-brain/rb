// Your view and the reader's view (the orchestrator's Oct 25 07:30 +08 brief): the hub says who looks with
// <meta name="rb-viewer" content="owner|reader"> before the shell's script, and the script sets html[data-viewer]. The
// owner gets the working view: a queue's row opens its call at Record a call, the composer composes and copies. A reader
// gets the record to read: the same row opens its card with no buttons, no reason field and no rb command anywhere, the
// keys make and copy nothing, and every warning, open item and trust line still shows. With no meta the page is as it
// was. Each page at 1440 and 390, light and dark, by real clicks and keys.
//   node tests/app/viewer.mjs --none <url> --owner <url> --reader <url> [--export <url>] [--framed <json>] [--timeout <s>]
// --none, --owner and --reader: one page (rb page) without the meta and with each; --export: rb export's page as the hub
// serves a share link (its script and the reader meta beside it); --framed: [[name, url, viewer]], host pages that frame
// a page with sandbox="allow-scripts" as the hub does and take its probe's report (posted to them) on <body data-probe>.
// The last line on stdout is JSON: {"ok", "checks": [{"name", "pass", "detail"}]}; exit 0, 1 on a failed check, 3 with
// no Chrome.
import { findChrome, launchChrome, parseArgs, progress, run, sleep } from './cdp.mjs';

const a = parseArgs(process.argv.slice(2));
if (!a.prose && !a.framed && !a.yours && !a.rows && !a.ownerexp && (!a.none || !a.owner || !a.reader)) { process.stderr.write('give --none, --owner and --reader, or --prose, or --framed\n'); process.exit(2); }
const results = [];
const check = (name, pass, detail = '') => { results.push({ name, pass: !!pass, detail }); progress(name); if (!pass) process.stderr.write(`FAIL ${name}: ${detail}\n`); };
const j = x => JSON.stringify(x);
const sel = s => `document.querySelector(${j(s)})`;

// what shows of the actions: rb commands (a box, a line or a chip whose text is one, `rb clone` aside), Copy buttons,
// a call's choices, its reason field and its composer, and the Record a call part
const ACTIONS = `(() => {
  const vis = e => e.checkVisibility() && e.getBoundingClientRect().width > 0;
  const rb = /^(cd \\S.* && )?rb (?!clone\\b)[a-z]/;
  const cmds = Array.from(document.querySelectorAll('.cmdbox, pre, code')).filter(e => vis(e) && rb.test(((e.querySelector('.cmd') || e).textContent || '').trim())).map(e => e.textContent.trim().slice(0, 60));
  const n = s => Array.from(document.querySelectorAll(s)).filter(vis).length;
  return { cmds, copy: n('.cp, [data-act="copy"]'), choices: n('.seg button, .decide button'), why: n('input.why'), composer: n('.composer'), decide: n('[data-level="decide"], .decide'), qbtn: n('.queue li:has(a[href]:not(.qbtn, [data-decide])) :is(.qbtn, a[data-decide])') };
})()`;
// every fold opened, as a reader may open each and print opens them all
const OPEN_ALL = `(() => { document.querySelectorAll('details').forEach(d => { d.open = true; }); return true; })()`;
// an rb command said in a sentence (any visible text), `rb clone` aside
const PROSE = `(() => {
  const rb = /(^|[\\s:(·])(cd \\S+ && )?rb (?!clone\\b)(confirm|decide|reconcile|status|show|retract|freeze|spec|run|criterion|experiment|question|note|doctor|context|journal|init|open|page|export)\\b/;
  const out = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) { const e = n.parentElement; if (e && rb.test(n.textContent) && e.checkVisibility() && e.getBoundingClientRect().width > 0) out.push(n.textContent.trim().slice(0, 90)); }
  return Array.from(new Set(out));
})()`;
const NONE = o => !o.cmds.length && !o.copy && !o.choices && !o.why && !o.composer && !o.decide && !o.qbtn;
// the words a reader is shown on the working view, the navigator and the top bar that speak to whoever reads as the
// one who decides ("Your call", "Needs your judgment", "Waiting for your confirmation")
const YOURS = `(() => {
  const out = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) { const e = n.parentElement; if (e && !e.closest('aside, .guide, .help, script, style') && /\\byours?\\b/i.test(n.textContent) && e.checkVisibility() && e.getBoundingClientRect().width > 0) out.push(n.textContent.trim().slice(0, 80)); }
  return Array.from(new Set(out));
})()`;
// what must stay for every viewer: the queue's rows, the trust line, the Overview's headings and the open items
const KEPT = `(() => {
  const vis = e => e.checkVisibility() && e.getBoundingClientRect().height > 0;
  const v = document.querySelector('#main .view.on');
  return { rows: Array.from(document.querySelectorAll('#overview .queue > li')).filter(vis).length, integ: !!(document.querySelector('#integrity') && vis(document.querySelector('#integrity'))),
    heads: Array.from(v.querySelectorAll('h1, h2, h3, summary')).filter(vis).map(h => h.textContent.trim().slice(0, 40)) };
})()`;
const WIDTH = `[document.documentElement.scrollWidth, innerWidth]`;

async function open(p, url, w, scheme, hash = 'overview') {
  await p.blank(); await p.viewport(w, w < 600 ? 844 : 900); await p.media({ type: '', scheme });
  await p.goto(url + '#' + hash);
  if (!(await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000))) throw new Error('never ready: ' + url);
  await p.ev(`(() => { try { localStorage.setItem('rb.guide.1', 'ended'); } catch (e) {} const g = document.querySelector('.guide:not([hidden]) .gskip'); if (g) g.click(); return true; })()`);
  await p.settle({ quietMs: 100 }); await sleep(200);
}

// --prose <url>: a reader's page, every view and card with its folds open: no sentence names an rb command (the words
// are rb's views' and model's; a reader is told the state, "The design is not confirmed yet.")
async function prose(p) {
  await open(p, a.prose, 1440, 'light');
  const said = [];
  const ids = await p.ev(`[...Array.from(document.querySelectorAll('main .view')).map(v => v.id), ...Array.from(document.querySelectorAll('main .nodes .card[id]')).map(c => c.id)]`);
  for (const id of ids) {
    await p.ev(`location.hash = ${j('#' + id)}`); await sleep(60);
    await p.ev(OPEN_ALL);
    for (const t of await p.ev(PROSE)) if (!said.includes(t)) said.push(t);
  }
  check(`reader: no sentence on any view or card names an rb command [1440 light]`, ids.length > 3 && !said.length, j(said.slice(0, 8)));
}

await run(async () => {
  const b = await launchChrome({ chrome: findChrome() });
  const p = await b.page();
  if (a.prose) {
    await prose(p);
    const failed = results.filter(r => !r.pass);
    if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
    return { checks: results };
  }
  // --yours <url>: a reader's page (rb page with the reader meta): no view says "your", every fold open
  if (a.yours) {
    await open(p, a.yours, 1440, 'light');
    const said = [];
    for (const v of await p.ev(`Array.from(document.querySelectorAll('main .view')).map(v => v.id)`)) {
      await p.ev(`location.hash = ${j('#' + v)}`); await sleep(100); await p.ev(OPEN_ALL);
      for (const t of await p.ev(YOURS)) if (!said.includes(t)) said.push(t);
      if (!NONE(await p.ev(ACTIONS))) said.push('an action shows on ' + v);
    }
    check(`reader: no view, navigator or top bar says "your", and none offers an action [1440 light]`, !said.length, j(said.slice(0, 8)));
    const failed = results.filter(r => !r.pass);
    if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
    return { checks: results };
  }
  // --rows <url>: a reader's page whose queue rows have their own link beside their Review button (as design pass 1
  // draws them): no Review button shows, and each row's link opens its item to read
  if (a.rows) for (const w of [1440, 390]) {
    await open(p, a.rows, w, 'light');
    const n = await p.ev(`document.querySelectorAll('#overview .queue > li').length`);
    const o = await p.ev(ACTIONS);
    const row = await p.clickEl(`Array.from(document.querySelectorAll('#overview .queue a')).find(e => e.checkVisibility())`);
    await sleep(300);
    const card = await p.ev(`(() => { const c = document.querySelector('.ibody > .card'); return c ? c.dataset.id || c.id : null })()`);
    const after = await p.ev(ACTIONS);
    check(`reader: a queue row's own link stands in for its Review button, and opens its item to read [${w} light]`, n > 0 && NONE(o) && !!row && !!card && NONE(after), j({ n, o, row: row && row.href, card, after }));
  }
  if (a.rows) {
    const failed = results.filter(r => !r.pass);
    if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
    return { checks: results };
  }
  // --ownerexp <url> --readerexp <url>: rb export's page as the hub serves it to its owner and on a share link. The
  // export names the calls "Open calls" in its attributes; the owner reads "Your call" in the tab's title, the
  // crumbs and a question's rail, a reader "Open calls"
  if (a.ownerexp) {
    for (const [who, url] of [['owner', a.ownerexp], ['reader', a.readerexp]]) {
      const name = who === 'owner' ? 'Your call' : 'Open calls', other = who === 'owner' ? 'Open calls' : 'Your call';
      await open(p, url, 1440, 'light', 'your-call');
      const title = await p.ev(`document.title`);
      const left = await p.ev(`Array.from(document.querySelectorAll('[data-title], [data-crumb], [data-rootname], [aria-label]')).flatMap(e => ['data-title', 'data-crumb', 'data-rootname', 'aria-label'].map(k => e.getAttribute(k) || '')).filter(t => t.startsWith(${j(other)}))`);
      const q = await p.ev(`(Array.from(document.querySelectorAll('main .view')).find(v => /^q-/.test(v.id)) || {}).id || null`);
      if (q) { await p.ev(`location.hash = ${j('#' + q)}`); await sleep(250); }
      const root = await p.ev(`(document.querySelector('.crumbs .rootname') || {}).textContent || ''`);
      check(`${who} export: the calls are "${name}" in the tab's title, the crumbs and a question's rail [1440 light]`,
        title.startsWith(name) && !left.length && (!q || root.startsWith(name)), j({ title, left: left.slice(0, 4), q, root }));
    }
    const failed = results.filter(r => !r.pass);
    if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
    return { checks: results };
  }
  // --failsafe <url>: a reader's page whose app never starts: no rb command box shows
  if (a.failsafe) {
    await p.blank(); await p.viewport(1440, 900); await p.goto(a.failsafe + '#overview'); await sleep(1500);
    await p.ev(OPEN_ALL);
    const o = await p.ev(ACTIONS), ready = await p.ev(`document.documentElement.classList.contains('ready')`);
    check(`reader: where the app never starts, no rb command box shows`, !ready && !o.cmds.length && !o.decide, j({ ready, o }));
  }
  if (a.none) for (const w of [1440, 390]) for (const scheme of ['light', 'dark']) {
    const at = `${w} ${scheme}`;
    const kept = {};
    for (const who of ['none', 'owner', 'reader']) {
      await open(p, a[who], w, scheme);
      const mode = await p.ev(`document.documentElement.dataset.viewer || null`);
      check(`viewer: the meta sets html[data-viewer] (${who}) [${at}]`, mode === (who === 'none' ? null : who), j(mode));
      kept[who] = await p.ev(KEPT);
      // every view of the reader's page: no action shows, and nothing runs past the screen at 390
      if (who === 'reader') {
        const views = await p.ev(`Array.from(document.querySelectorAll('main .view')).map(v => v.id)`);
        const bad = [];
        for (const v of views) {
          await p.ev(`location.hash = ${j('#' + v)}`); await sleep(120);
          if (w < 600) { const [sw, iw] = await p.ev(WIDTH); if (sw > iw) bad.push({ v, past: sw - iw }); }
          await p.ev(OPEN_ALL);
          const o = await p.ev(ACTIONS);
          if (!NONE(o)) bad.push({ v, o });
        }
        // printed, every fold opens: still no action
        await p.media({ type: 'print', scheme }); await sleep(150);
        const printed = await p.ev(ACTIONS);
        if (!NONE(printed)) bad.push({ print: printed });
        await p.media({ type: '', scheme });
        await p.ev(`location.hash = '#overview'`); await open(p, a[who], w, scheme);
        check(`reader: no view shows an rb command, a Copy, a call's choices or reason, its folds opened or printed${w < 600 ? ', and none runs past the screen' : ''} [${at}]`, views.length > 3 && !bad.length, j(bad.slice(0, 4)));
        await p.ev(`location.hash = '#overview'`); await sleep(150);
        if (w >= 960) {
          const rail = await p.ev(`document.documentElement.classList.contains('irail')`);
          check(`reader: the Overview lands with the inspector folded until something is picked [${at}]`, rail, j(rail));
        }
      }
      // a queue's row by a real click: the owner at Record a call, the reader on the card to read
      await p.hookClipboard();
      // (a reader is shown no Review button: the row's own link then)
      const row = await p.clickEl(`Array.from(document.querySelectorAll('#overview .queue a[data-decide], #overview .queue a')).find(e => e.checkVisibility())`);
      await sleep(300);
      const card = await p.ev(`(() => { const c = document.querySelector('.ibody > .card'), f = document.activeElement; return c ? { id: c.dataset.id || c.id, hash: location.hash,
        focus: !!(f && f.closest('.ibody') && f.checkVisibility() && f.getBoundingClientRect().height > 0) } : null; })()`);
      const acts = await p.ev(ACTIONS);
      if (who === 'reader') {
        // the card itself, from its top: its address names the card, never the Decide part a reader is not shown, and
        // focus is on something that shows
        check(`reader: a queue's row opens its item to read, with no command, button or reason field [${at}]`,
          !!row && !!card && NONE(acts) && !/:decide$/.test(card.hash) && card.focus, j({ row: row && row.href, card, acts }));
        // j on the calls' view opens the next call to read, focus on what shows
        await p.ev(`location.hash = '#your-call'`); await sleep(300);
        await p.key('j', 'KeyJ', 74, 'j'); await sleep(300);
        const jk = await p.ev(`(() => { const f = document.activeElement, c = document.querySelector('.ibody > .card'); return { card: c ? c.dataset.id || c.id : null, hash: location.hash,
          focus: !!(f && f !== document.body && f.checkVisibility() && f.getBoundingClientRect().height > 0), acts: ${ACTIONS} } })()`);
        check(`reader: j on the calls' view opens a call to read, focus on what shows [${at}]`, !!jk.card && jk.focus && !/:decide$/.test(jk.hash) && NONE(jk.acts), j(jk));
        // the keys that make or copy a call make nothing here
        for (const [k, code, kc] of [['i', 'KeyI', 73], ['a', 'KeyA', 65], ['c', 'KeyC', 67]]) await p.key(k, code, kc, k);
        const after = await p.ev(`(() => { const d = document.querySelector('.ibody .decide'); return { chosen: d ? d.dataset.chosen || null : null, copied: window.__copied } })()`);
        check(`reader: a, i and c choose and copy nothing [${at}]`, after.chosen === null && !(after.copied || []).length, j(after));
        // the keys list says no key that makes or copies a call, nor whose judgment
        await p.key('?', 'Slash', 191, '?');
        const keys = await p.ev(`(() => { const h = document.querySelector('.help'); return h ? h.textContent : '' })()`);
        check(`reader: the keys list offers no call and no copy [${at}]`, keys.includes('Keyboard') && !/accept, reject|copy the card|\byou\b|\byour\b/i.test(keys), keys.slice(0, 300));
        await p.key('Escape', 'Escape', 27);
      } else {
        const dec = await p.ev(`(() => { const d = document.querySelector('.ibody .decide'); return d ? d.checkVisibility() : false })()`);
        check(`${who}: a queue's row opens its call at Record a call, its commands shown [${at}]`, !!row && !!card && dec && acts.choices > 0, j({ row: row && row.href, card, acts }));
        // the composer by real input: a choice, a reason typed, its line copied
        await p.clickEl(sel('.ibody .decide .seg button'));
        await p.clickEl(sel('.ibody .decide input.why'));
        await p.type('ok for ana');
        const line = await p.ev(`(() => { const c = document.querySelector('.ibody .composer:not([hidden]) .cmd'); return c ? c.textContent : null })()`);
        await p.clickEl(sel('.ibody .composer:not([hidden]) .cp'));
        const copied = await p.copied();
        check(`${who}: the composer composes the call with the reason and copies it [${at}]`, line && /^rb decide \S+ \w+ --why 'ok for ana'$/.test(line) && (copied || []).includes(line + '\n'), j({ line, copied }));
      }
    }
    // the pointer guide, on a first visit where the browser keeps it: the owner is asked for a call, a reader told
    // where the calls stand, by no word that says whose judgment
    if (w >= 960 && scheme === 'light') for (const who of ['owner', 'reader']) {
      await p.blank(); await p.viewport(w, 900); await p.goto(a[who] + '#overview');
      await p.ev(`localStorage.removeItem('rb.guide.1')`);
      await p.blank(); await p.goto(a[who] + '#overview');
      await p.waitFor(`!!document.querySelector('.guide:not([hidden]) #guide-h')`, 10000);
      const titles = [];
      for (let k = 0; k < 6; k++) {
        const t = await p.ev(`(() => { const g = document.querySelector('.guide:not([hidden])'); return g ? g.querySelector('#guide-h').textContent + ': ' + g.querySelector('#guide-p').textContent : null })()`);
        if (!t) break;
        titles.push(t);
        await p.clickEl(`document.querySelector('.guide .gnext')`); await sleep(500);
      }
      const said = titles.join(' | ');
      check(`${who}: the guide ${who === 'reader' ? "says where the calls stand, by no word of whose judgment" : 'asks for a call where one waits'} [${at}]`,
        titles.length >= 3 && (who === 'reader' ? said.includes('Still open:') && !/your judgment|your call|Your call|ready for/.test(said) : said.includes('Needs your judgment:')), said);
    }
    // a reader is shown every warning, open item and trust line the owner is
    check(`reader: every row that is still open, the trust line and every heading still show [${at}]`,
      kept.reader.rows > 0 && kept.reader.rows === kept.owner.rows && kept.reader.integ === kept.owner.integ && j(kept.reader.heads) === j(kept.owner.heads) && j(kept.owner.heads) === j(kept.none.heads), j(kept));
    // rb export's page as the hub serves a share link
    if (a.export) {
      await open(p, a.export, w, scheme);
      const mode = await p.ev(`document.documentElement.dataset.viewer || null`);
      const views = await p.ev(`Array.from(document.querySelectorAll('main .view')).map(v => v.id)`);
      const bad = [];
      const yours = [];
      for (const v of views) {
        await p.ev(`location.hash = ${j('#' + v)}`); await sleep(120);
        if (w < 600) { const [sw, iw] = await p.ev(WIDTH); if (sw > iw) bad.push({ v, past: sw - iw }); }
        await p.ev(OPEN_ALL);
        const o = await p.ev(ACTIONS);
        if (!NONE(o)) bad.push({ v, o });
        for (const t of await p.ev(YOURS)) if (!yours.includes(t)) yours.push(t);
      }
      if (a.exportyours) check(`export, reader: no view, navigator or top bar says "your" to a reader [${at}]`, views.length > 3 && !yours.length, j(yours.slice(0, 8)));
      // every card too: a reader opens any of them, and none offers an action
      const cards = await p.ev(`Array.from(document.querySelectorAll('main .nodes .card[id]')).map(c => c.id).slice(0, 60)`);
      for (const c of cards) {
        await p.ev(`location.hash = ${j('#' + c)}`); await sleep(60);
        await p.ev(OPEN_ALL);
        const o = await p.ev(ACTIONS);
        if (!NONE(o)) bad.push({ c, o });
      }
      await p.media({ type: 'print', scheme }); await sleep(150);
      const printed = await p.ev(ACTIONS);
      if (!NONE(printed)) bad.push({ print: printed });
      await p.media({ type: '', scheme });
      check(`export, reader: no view or card shows an rb command, a Copy, a call's choices or reason [${at}]`, mode === 'reader' && views.length > 3 && cards.length > 3 && !bad.length, j({ mode, n: cards.length, bad: bad.slice(0, 4) }));
    }
  }
  // the hub's sandboxed frame: no storage, no network; the owner's composer composes there, a reader's page offers none
  for (const [name, url, who] of JSON.parse(a.framed || '[]')) {
    await p.blank(); await p.viewport(1440, 900);
    await p.goto(url);
    let got = null;
    for (let t = 0; t < 100 && !got; t++) { await sleep(100); got = await p.ev(`document.body.getAttribute('data-probe')`); }
    const g = got ? JSON.parse(got) : null;
    const ok = g && g.stored === 'throws' && g.hosted && g.viewer === who && !g.errors.length && g.net === 0 &&
      (who === 'owner' ? /^rb decide \S+ \w+ --why 'ok for ana'$/.test(g.line || '') && g.live : g.line === null && NONE(g.acts));
    check(`framed (${name}): in the hub's sandbox the ${who}'s page ${who === 'owner' ? 'composes a call' : 'offers no action'}, keeps nothing and sends nothing`, ok, j(g));
  }
  const failed = results.filter(r => !r.pass);
  if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
  return { checks: results };
}, { timeoutMs: (Number(a.timeout) || 240) * 1000, idleMs: (Number(a.idle) || 120) * 1000 });
