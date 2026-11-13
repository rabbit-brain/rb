// Every citation opens (DESIGN-UX5 section 4, round 2): the walk opens every field card a page or rb open shows, from
// the Overview's own links (a strip square, a chart row, a rests-on mark, a Decisions row) and from each card's links,
// and follows every link each card holds. Each must lead somewhere real: a card in the inspector (never "nothing here
// is called ..."), a view shown, or Files at a folder. At 1280 and 390.
//   node tests/app/cites.mjs (--url <page> | --rb <rb> --record <checkout>) [--most <cards>] [--timeout <s>, 600]
// The last line on stdout is JSON: {"ok", "checks": [{"name", "pass", "detail"}], "opened": n, "followed": n, "cards":
// {id: {kind, fields: [label...]}}}; exit 0, 1 on a failed check, 3 with no Chrome.
import { findChrome, launchChrome, parseArgs, progress, run, startRbOpen } from './cdp.mjs';

const a = parseArgs(process.argv.slice(2));
if (!a.url && !(a.rb && a.record)) { process.stderr.write('give --url, or --rb and --record\n'); process.exit(2); }
const results = [];
const check = (name, pass, detail = '') => { results.push({ name, pass: !!pass, detail }); progress(name); if (!pass) process.stderr.write(`FAIL ${name}: ${detail}\n`); };
const MOST = +(a.most || 100000);

// in the page: open an address and say what it shows (the inspector's top card, or the view shown), waiting while rb
// open serves a field card
const OPEN = `async (to, wait) => {
  const until = Date.now() + wait;
  // followed as a link in the working view is: a click, which the script takes
  const l = document.createElement('a');
  l.href = '#' + to; l.textContent = 'walk';
  (document.querySelector('main .view.on') || document.querySelector('main')).appendChild(l);
  l.click(); l.remove();
  for (;;) {
    await new Promise(r => requestAnimationFrame(() => setTimeout(r, 20)));
    const top = document.querySelector('.ibody > .card');
    const view = document.querySelector('main .view.on');
    const said = { card: top ? (top.dataset.id || '') : '', missing: !!(top && top.classList.contains('missing')), empty: !!(top && top.classList.contains('missing') && !top.textContent.trim()),
      text: top ? top.textContent.trim().slice(0, 120) : '', view: view ? view.id : '' };
    const shown = said.card === to || (!said.card && said.view === to) || (said.missing && !said.empty);
    if (shown || Date.now() > until) return said;
  }
}`;
// the links a card in the inspector holds: each once, with what it names and any folder it opens Files at
const LINKS = `(() => Array.from(document.querySelectorAll('.ibody > .card a[href^="#"]')).map(l => ({ to: decodeURIComponent(l.getAttribute('href').slice(1)), folder: l.dataset.folder || '', text: l.textContent.trim() })))()`;
const FIELDS = `(() => { const c = document.querySelector('.ibody > .card'); return c ? { kind: c.dataset.kind || '', head: (c.querySelector('h2') || {}).textContent || '', fields: Array.from(c.querySelectorAll(':scope > dl.fd > dt')).map(d => d.textContent.trim()) } : null; })()`;

await run(async () => {
  const chrome = findChrome();
  if (!chrome) process.exit(3);
  let url = a.url;
  if (!url) url = (await startRbOpen({ rb: a.rb, cwd: a.record })).url + '/';
  const live = !a.url;
  const c = await launchChrome({ chrome });
  const p = await c.page();
  const cards = {};
  let opened = 0, followed = 0;
  for (const [w, h] of [[1280, 900], [390, 844]]) {
    await p.viewport(w, h);
    await p.goto(url);
    await p.ev("localStorage.setItem('rb.guide.1','seen'); true");
    await p.goto(url);
    await p.settle();
    const at = `[${w}]`;
    // where the walk starts: the Overview's links to field cards and to the rests-on cards, and on a page every field
    // card it holds
    const start = await p.ev(`(() => { const ov = document.getElementById('overview'); const out = new Set();
      for (const l of ov.querySelectorAll('a[href^="#k-"], a[href^="#rest-"]')) out.add(decodeURIComponent(l.getAttribute('href').slice(1)));
      for (const k of document.querySelectorAll('main > .nodes > .card.fc')) out.add(k.id);
      return Array.from(out); })()`);
    check(`the Overview opens field cards ${at}`, start.some(s => s.startsWith('k-')), JSON.stringify(start.slice(0, 5)));
    const todo = [...start], seen = new Set(), bad = [], targets = new Map();
    while (todo.length && seen.size < (w === 1280 ? MOST : Math.min(MOST, 40))) {
      const id = todo.shift();
      if (seen.has(id)) continue;
      seen.add(id);
      const said = await p.ev(`(${OPEN})(${JSON.stringify(id)}, ${live ? 30000 : 3000})`);
      opened++;
      progress(`opened ${id}`);
      if (a.verbose) process.stderr.write(`open ${id}: ${JSON.stringify(said)}\n`);
      if (said.missing || (said.card !== id && said.card !== id.split(':')[0])) { bad.push(`${id}: ${said.missing ? said.text || 'nothing came' : 'opened ' + (said.card || said.view)}`); continue; }   // `#<card>:<part>` opens the card at that part (cf-2)
      if (w === 1280) cards[id] = await p.ev(FIELDS);
      for (const l of await p.ev(LINKS)) {
        if (!targets.has(l.to + '\n' + l.folder)) targets.set(l.to + '\n' + l.folder, { ...l, from: id });
        if ((l.to.startsWith('k-') || l.to.startsWith('rest-')) && !seen.has(l.to)) todo.push(l.to);
      }
    }
    check(`every field card opens as itself, never "nothing here is called" ${at}`, !bad.length, bad.slice(0, 8).join('; '));
    // every other link a card holds: a card, a view, or Files at a folder
    const wrong = [];
    for (const l of targets.values()) {
      if (l.to.startsWith('k-') || l.to.startsWith('rest-')) continue;
      followed++;
      progress(`followed ${l.to}`);
      if (l.folder) {
        // Files at the folder: the card that holds the link, opened again, and the link clicked
        await p.ev(`(${OPEN})(${JSON.stringify(l.from)}, ${live ? 30000 : 3000})`);
        await p.clickEl(`Array.from(document.querySelectorAll('.ibody a[data-folder]')).find(x => x.dataset.folder === ${JSON.stringify(l.folder)})`);
        const ok = await p.ev(`(() => { const n = document.querySelector('nav li.fls.open'); const d = n && n.querySelector('details[data-folder="' + CSS.escape(${JSON.stringify(l.folder)}) + '"]'); return !!(n && (!d || d.open)); })()`);
        if (!ok) wrong.push(`${l.from} › ${l.text}: Files did not open at ${l.folder}`);
        await p.ev(`(() => { const b = document.querySelector('nav li.fls.open .ftog'); if (b) b.click(); return true; })()`);
        continue;
      }
      const said = await p.ev(`(${OPEN})(${JSON.stringify(l.to)}, 3000)`);
      const at = l.to.split(':')[0];   // `#<card or view>:<part>` opens it at that part (cf-2)
      const real = !said.missing && (said.card === l.to || said.view === l.to || said.card === at || said.view === at);
      if (!real) wrong.push(`${l.from} › ${l.text} (#${l.to}): ${said.text || said.view}`);
    }
    check(`every link a field card holds leads somewhere real ${at}`, !wrong.length, wrong.slice(0, 8).join('; '));
    if (w === 1280) {
      const fit = await p.ev(`(() => document.documentElement.scrollWidth <= innerWidth + 1)()`);
      check(`nothing past the screen with a card open ${at}`, fit);
    } else {
      const sheet = await p.ev(`(() => document.documentElement.classList.contains('sheet') || !!document.querySelector('aside.insp[role="dialog"]'))()`);
      check(`at 390 the card replaces the story ${at}`, sheet || !opened);
    }
  }
  const failed = results.filter(r => !r.pass);
  if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
  return { checks: results, opened, followed, cards };
}, { timeoutMs: (+(a.timeout || 600)) * 1000, idleMs: 120000 });
