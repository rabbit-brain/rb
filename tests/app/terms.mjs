// A term says its meaning in place (plain words pass 4, Oct 25 15:05 +08): a click or a tap on a word with a meaning
// (.term with its hint) shows the hint beside it and follows nothing, not the link or the row it sits in, so the working
// view, its scroll, the address and the inspector stay as they were; Esc or a second click closes it, the pointer
// still on the word, and leaves the inspector as it is, and so does a click elsewhere. Terms in the working view and in a card, at 1280 and 390, light and dark.
//   node tests/app/terms.mjs --url <address> [--timeout <s>, 240]
// The last line on stdout is JSON: {"ok", "checks": [{"name", "pass", "detail"}]}; exit 0, 1 on a failed check, 3 with
// no Chrome.
import { findChrome, launchChrome, parseArgs, progress, run, sleep } from './cdp.mjs';

const a = parseArgs(process.argv.slice(2));
if (!a.url) { process.stderr.write('give --url\n'); process.exit(2); }
const results = [];
const check = (name, pass, detail = '') => { results.push({ name, pass: !!pass, detail }); progress(name); if (!pass) process.stderr.write(`FAIL ${name}: ${detail}\n`); };
const j = x => JSON.stringify(x);

const STATE = `(() => { const v = document.querySelector('main .view.on'), c = document.querySelector('.ibody > *'), t = document.querySelector('[data-tterm]');
  const hint = t ? getComputedStyle(t, '::after').content : '';
  return { view: v && v.id, scroll: Math.round(document.querySelector('main').scrollTop + scrollY), iscroll: Math.round(document.querySelector('aside.insp').scrollTop),
    hash: location.hash, top: c ? (c.dataset.id || c.className) : null,
    shown: !!t && document.activeElement === t && hint !== 'none' && hint.includes(t.dataset.hint.slice(0, 20)) }; })()`;
// up to `n` terms that show, in the working view (or in the inspector's card), each marked in turn
const PICK = (where, k) => `(() => { document.querySelectorAll('[data-tterm]').forEach(x => x.removeAttribute('data-tterm'));
  const scope = document.querySelector(${j(where)});
  const ts = scope ? Array.from(scope.querySelectorAll('.term[data-hint]')).filter(t => { for (let d = t.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true; return t.checkVisibility() && t.getBoundingClientRect().width > 0; }) : [];
  const t = ts[${k}]; if (!t) return null;
  t.scrollIntoView({ block: 'center' }); t.setAttribute('data-tterm', ''); return { n: ts.length, word: t.textContent.trim(), inLink: !!t.closest('a[href], [data-go], [data-for]') }; })()`;

await run(async () => {
  const b = await launchChrome({ chrome: findChrome() });
  const p = await b.page();
  for (const w of [1280, 390]) for (const scheme of ['light', 'dark']) {
    const at = `${w} ${scheme}`;
    await p.blank(); await p.viewport(w, w < 600 ? 844 : 900); await p.media({ type: '', scheme });
    await p.goto(a.url + '#' + (a.view || 'overview'));
    if (!(await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000))) throw new Error('never ready');
    await p.ev(`(() => { try { localStorage.setItem('rb.guide.1', 'ended'); } catch (e) {} const g = document.querySelector('.guide:not([hidden]) .gskip'); if (g) g.click(); return true; })()`);
    await p.settle({ quietMs: 100 }); await sleep(200);
    let tried = 0, inLink = 0;
    // in the working view, then in a card opened over it (a.card: the card's id)
    for (const where of ['main .view.on', '.ibody']) {
      if (where === '.ibody') {
        if (!a.card) continue;
        await p.ev(`location.hash = ${j('#' + a.card)}`); await sleep(400);
      }
      for (let k = 0; k < 4; k++) {
        const got = await p.ev(PICK(where, k));
        if (!got) break;
        tried++; if (got.inLink) inLink++;
        await sleep(150);
        const before = await p.ev(STATE);
        const r = await p.clickEl(`document.querySelector('[data-tterm]')`); await sleep(250);
        const shown = await p.ev(STATE);
        // the pointer leaves the word: the hint stays while the word holds focus
        await p.mouse(5, w < 600 ? 830 : 890); await sleep(150);
        const held = await p.ev(STATE);
        // the pointer back on the word, then Esc: its meaning closes though the word is still hovered
        const at2 = await p.ev(`(() => { const t = document.querySelector('[data-tterm]'), q = t.getClientRects()[0]; return [q.x + Math.min(q.width / 2, 30), q.y + q.height / 2]; })()`);
        await p.mouse(at2[0], at2[1]); await sleep(150);
        await p.key('Escape', 'Escape', 27); await sleep(250);
        const esc = await p.ev(STATE.replace("document.activeElement === t && hint", "hint"));
        // and the same for a second click on it
        await p.clickAt(at2[0], at2[1]); await sleep(200);
        await p.clickAt(at2[0], at2[1]); await sleep(250);
        const again = await p.ev(STATE.replace("document.activeElement === t && hint", "hint"));
        await p.mouse(5, w < 600 ? 830 : 890); await sleep(150);
        const same = s => s.view === before.view && Math.abs(s.scroll - before.scroll) <= 2 && s.iscroll === before.iscroll && s.hash === before.hash && s.top === before.top;
        check(`term: "${got.word}" (${where === '.ibody' ? 'in a card' : 'in the view'}) shows its meaning in place and moves nothing; Esc closes it and keeps the inspector [${at}]`,
          shown.shown && held.shown && same(shown) && !esc.shown && same(esc) && !again.shown && same(again), j({ got, before, shown, held, esc, again }));
        // a click elsewhere closes it too
        await p.clickEl(`document.querySelector('[data-tterm]')`); await sleep(200);
        // elsewhere: the view's heading, or in a card (a sheet over the view under 960) the card's title
        await p.clickEl(where === '.ibody' ? `document.querySelector('.ibody .tt, .ibody h2')` : `document.querySelector('main .view.on h1')`); await sleep(250);
        const out = await p.ev(STATE);
        check(`term: "${got.word}": a click elsewhere closes its meaning [${at}]`, !out.shown && out.view === before.view && out.hash === before.hash, j(out));
      }
    }
    check(`term: the page holds terms to try, one inside a link or a row [${at}]`, tried >= 2 && (!a.inlink || inLink > 0), j({ tried, inLink }));
  }
  const failed = results.filter(r => !r.pass);
  if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
  return { checks: results };
}, { timeoutMs: (Number(a.timeout) || 240) * 1000, idleMs: (Number(a.idle) || 120) * 1000 });
