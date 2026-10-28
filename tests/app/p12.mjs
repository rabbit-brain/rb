// A detail opens in the inspector (design pass 1, P12): a link in a view, or in a card, to a part inside another view's
// fold (the project's notes, the open items, what the words mean) never moves the working view there. By a real click
// the view and its scroll stay as they were, the inspector shows the part under its fold's words with the address
// naming it, and Back returns: from a view the inspector is empty again, from a card the card is back on top. At 1280
// and 390, light and dark. (The link stays where it was on the screen, unless the view has no room left to scroll.)
//   node tests/app/p12.mjs --url <address> [--timeout <s>, 240]
// The last line on stdout is JSON: {"ok", "checks": [{"name", "pass", "detail"}]}; exit 0, 1 on a failed check, 3 with
// no Chrome.
import { findChrome, launchChrome, parseArgs, progress, run, sleep } from './cdp.mjs';

const a = parseArgs(process.argv.slice(2));
if (!a.url) { process.stderr.write('give --url\n'); process.exit(2); }
const results = [];
const check = (name, pass, detail = '') => { results.push({ name, pass: !!pass, detail }); progress(name); if (!pass) process.stderr.write(`FAIL ${name}: ${detail}\n`); };
const j = x => JSON.stringify(x);

// the fold a part outside the cards is, sits in or wraps, and the view it is on ('' for none)
const FOLD = `const foldOf = el => el && !el.closest('.card, aside, #nav, header.top') && !el.matches('.view') ? (el.closest('details.fold') || el.querySelector(':scope > details.fold')) : null;
  const viewOf = el => { const v = el.closest('main .view'); return v ? v.id : ''; };`;
// every link in a view's own markup (outside its cards) or in a card, to a part in a fold of another view
const LINKS = `(() => { ${FOLD}
  const out = [];
  for (const l of document.querySelectorAll('main a[href^="#"]')) {
    const id = decodeURIComponent(l.getAttribute('href').slice(1)), t = document.getElementById(id), f = foldOf(t);
    if (!f) continue;
    const card = l.closest('.card');
    const from = card ? card.id : viewOf(l);
    if (!from || (!card && viewOf(l) === viewOf(t)) || (card && viewOf(t) === (document.getElementById(card.dataset.home) ? card.dataset.home : viewOf(card)))) continue;
    out.push({ from, card: !!card, id, title: f.querySelector(':scope > summary').textContent.trim() });
  }
  return out; })()`;
const STATE = `(() => { const v = document.querySelector('main .view.on'), c = document.querySelector('.ibody > .card');
  const l = document.querySelector('main [data-p12]');
  return { at: l && l.checkVisibility() ? Math.round(l.getBoundingClientRect().top) : null, view: v && v.id, room: Math.round(document.querySelector('main').scrollHeight - document.querySelector('main').clientHeight), scroll: Math.round(document.querySelector('main').scrollTop + scrollY), hash: decodeURIComponent(location.hash.slice(1)),
    top: c ? { part: c.classList.contains('partc'), id: c.dataset.id || c.id, title: (c.querySelector('.tt') || {}).textContent || '', text: c.textContent.length } : null,
    crumbs: Array.from(document.querySelectorAll('.crumbs > a, .crumbs > span:not(.sep)')).map(x => x.textContent) }; })()`;

await run(async () => {
  const b = await launchChrome({ chrome: findChrome() });
  const p = await b.page();
  for (const w of [1280, 390]) for (const scheme of ['light', 'dark']) {
    const at = `${w} ${scheme}`;
    await p.blank(); await p.viewport(w, w < 600 ? 844 : 900); await p.media({ type: '', scheme });
    await p.goto(a.url + '#overview');
    if (!(await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000))) throw new Error('never ready');
    await p.ev(`(() => { try { localStorage.setItem('rb.guide.1', 'ended'); } catch (e) {} const g = document.querySelector('.guide:not([hidden]) .gskip'); if (g) g.click(); return true; })()`);
    await p.settle({ quietMs: 100 }); await sleep(200);
    const links = await p.ev(LINKS);
    check(`P12: the page holds a link to another view's fold [${at}]`, links.length > 0, j(links));
    for (const [k, l] of links.entries()) {
      // its view (the card's home, the card opened over it), the link brought into view by scrolling the working view
      await p.ev(`location.hash = ${j('#' + l.from)}`);
      await sleep(250);
      const mark = `document.querySelector('[data-p12]')`;
      const found = await p.ev(`(() => { document.querySelectorAll('[data-p12]').forEach(x => x.removeAttribute('data-p12'));
        // a card's link in its copy in the inspector; a view's in the view, or in its rail, which shows as the
        // inspector's root beside it from 960
        const scopes = ${l.card} ? [document.querySelector('.ibody')] : [document.querySelector('main .view.on'), document.querySelector('.ibody > .rootc')];
        const open = e => { for (let d = e.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true; return e; };
        const x = scopes.filter(Boolean).map(sc => Array.from(sc.querySelectorAll('a[href]')).find(e => decodeURIComponent(e.getAttribute('href').slice(1)) === ${j(l.id)} && open(e).checkVisibility())).find(Boolean);
        if (!x) return false;
        x.setAttribute('data-p12', ''); x.scrollIntoView({ block: 'center' });
        // under a card, the working view scrolled away from its top first: Back must not move it
        const m = document.querySelector('main');
        if (${l.card}) { m.scrollTop = Math.min(250, m.scrollHeight - m.clientHeight); scrollTo(0, Math.min(250, document.documentElement.scrollHeight - innerHeight)); }
        return true; })()`);
      if (!found) { check(`P12: ${l.from} → #${l.id} shows its link [${at}]`, false, j(l)); continue; }
      await sleep(150);
      const before = await p.ev(STATE);
      // a real click on the link, on a part of it that is not a term (a term says its meaning and follows nothing)
      const pt = await p.ev(`(() => { const l = ${mark}; for (const q of l.getClientRects()) for (let x = q.left + 2; x < q.right; x += 4) {
        const y = q.top + q.height / 2, e = document.elementFromPoint(x, y); if (e && l.contains(e) && !e.closest('.term')) return [x, y]; } return null; })()`);
      if (!pt) continue;   // the link's words are all a term's, which a click says the meaning of
      await p.clickAt(pt[0], pt[1]);
      await sleep(350);
      const opened = await p.ev(STATE);
      const tag = `${l.from} → #${l.id}`;
      check(`P12: ${tag} opens the part in the inspector under its fold's words; the working view stays, its link where it was [${at}]`,
        opened.view === before.view && (before.at === null || opened.at === null ? Math.abs(opened.scroll - before.scroll) <= 2 : (Math.abs(opened.at - before.at) <= 2 || (opened.at > before.at && opened.scroll >= opened.room - 1))) && opened.top && opened.top.part && opened.top.title.trim() === l.title && opened.top.text > l.title.length && opened.hash === l.id
          && (!l.card || opened.crumbs.length >= 3), j({ l, before, opened }));
      await p.ev(`history.back()`); await sleep(400);
      const back = await p.ev(STATE);
      check(`P12: ${tag}, then Back: the working view where it was, ${l.card ? 'the card on top again' : 'the inspector as it was'} [${at}]`,
        back.view === before.view && Math.abs(back.scroll - before.scroll) <= 2 && (l.card ? back.top && !back.top.part && back.top.id === before.top.id : !back.top), j({ before, back }));
      if (k > 12) break;
    }
  }
  const failed = results.filter(r => !r.pass);
  if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
  return { checks: results };
}, { timeoutMs: (Number(a.timeout) || 240) * 1000, idleMs: (Number(a.idle) || 120) * 1000 });
