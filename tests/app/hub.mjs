// One bar on the hub (plain words pass 4, Oct 25 13:35 +08): the hub's own bar names the record above the page, so a
// page the hub names a viewer of (<meta name="rb-viewer">) draws no top bar of its own. At 1440 nothing outside the
// working view repeats the record's title, About is in the navigator and opens its view, and the guide's first step
// points at something on the screen; at 390 the navigator still opens a view. A page with no meta keeps its bar.
//   node tests/app/hub.mjs --url <the page with the meta> --plain <the same page with none> [--guide 1] [--about 1] [--timeout <s>]
// The last line on stdout is JSON: {"ok", "checks": [{"name", "pass", "detail"}]}; exit 0, 1 on a failed check, 3 with
// no Chrome.
import { findChrome, launchChrome, parseArgs, progress, run, sleep } from './cdp.mjs';

const a = parseArgs(process.argv.slice(2));
if (!a.url || !a.plain) { process.stderr.write('give --url and --plain\n'); process.exit(2); }
const results = [];
const check = (name, pass, detail = '') => { results.push({ name, pass: !!pass, detail }); progress(name); if (!pass) process.stderr.write(`FAIL ${name}: ${detail}\n`); };
const j = x => JSON.stringify(x);

// the record's title as the page's <title> says it, and the visible elements outside the working view and the
// inspector that say it
const TWICE = `(() => {
  const t = (document.querySelector('#overview h1') || {}).textContent || '';
  const vis = e => e.checkVisibility() && e.getBoundingClientRect().height > 0;
  const said = Array.from(document.querySelectorAll('body *')).filter(e => !e.closest('main, aside, script, style, title') && e.children.length === 0 && vis(e) && t && e.textContent.trim() && t.includes(e.textContent.trim()) && e.textContent.trim().length > 12);
  const bar = document.querySelector('header.top');
  return { title: t.slice(0, 60), said: said.map(e => e.tagName + '.' + e.className + ': ' + e.textContent.trim().slice(0, 40)), bar: !!bar && vis(bar) };
})()`;

async function open(p, url, w, hash = 'overview') {
  await p.blank(); await p.viewport(w, w < 600 ? 844 : 900); await p.media({ type: '', scheme: 'light' });
  await p.goto(url + '#' + hash);
  if (!(await p.waitFor(`document.documentElement.classList.contains('ready')`, 10000))) throw new Error('never ready: ' + url);
}

await run(async () => {
  const b = await launchChrome({ chrome: findChrome() });
  const p = await b.page();
  // the guide on a first visit, where its styles are on the page (--guide): its first step's spot shows
  if (a.guide) {
  await open(p, a.url, 1440);
  await p.ev(`localStorage.removeItem('rb.guide.1')`); await open(p, a.url, 1440);
  await p.waitFor(`!!document.querySelector('.guide:not([hidden]) #guide-h')`, 10000);
  await sleep(400);
  const first = await p.ev(`(() => { const s = document.querySelector('.guide-spot'); const r = s && !s.hidden ? s.getBoundingClientRect() : null;
    return { title: (document.querySelector('#guide-h') || {}).textContent || '', spot: r && [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      shows: !!r && r.width > 20 && r.height > 20 && r.top >= 0 && r.left >= 0 && r.top < innerHeight && r.left < innerWidth }; })()`);
  check(`hub: the guide's first step points at something on the screen [1440]`, first.title && first.shows, j(first));
  }
  await p.ev(`(() => { try { localStorage.setItem('rb.guide.1', 'ended'); } catch (e) {} const g = document.querySelector('.guide:not([hidden]) .gskip'); if (g) g.click(); return true; })()`);
  await open(p, a.url, 1440); await sleep(200);
  const hub = await p.ev(TWICE);
  check(`hub: no top bar of the page's own, nothing outside the working view says the record's title [1440]`, hub.title && !hub.bar && !hub.said.length, j(hub));
  // where the page's bar holds About (--about): it is in the navigator
  if (a.about) {
  const about = await p.clickEl(`Array.from(document.querySelectorAll('#nav a[href="#about"]')).find(e => e.checkVisibility())`); await sleep(300);
  const onAbout = await p.ev(`(document.querySelector('main .view.on') || {}).id`);
  check(`hub: About is in the navigator and opens its view [1440]`, !!about && onAbout === 'about', j({ about: about && about.text, onAbout }));
  }
  await open(p, a.url, 390); await sleep(200);
  const nav = await p.clickEl(`Array.from(document.querySelectorAll('#nav a[href^="#"], #nav summary, #nav button')).find(e => e.checkVisibility() && e.getAttribute('href') !== '#overview')`); await sleep(300);
  // under 1280 the hub's slim bar says the view's name alone (R's one bar): the page's own bar shows nothing else
  const after = await p.ev(`(() => { const t = document.querySelector('header.top'), v = document.querySelector('main .view.on') || {};
    return { view: v.id, crumb: (v.dataset && v.dataset.crumb) || '', bar: t && t.checkVisibility() ? t.innerText.replace(/\\s+/g, ' ').trim() : '' }; })()`);
  check(`hub: at 390 the navigator still opens a view, under no bar of the page's own but the view's name [390]`, !!nav && after.view !== 'overview' && (!after.bar || after.bar === after.crumb), j({ nav: nav && nav.text, after }));
  await open(p, a.plain, 1440); await sleep(200);
  const plain = await p.ev(TWICE);
  check(`no meta: the page keeps its own bar [1440]`, plain.bar, j(plain));
  const failed = results.filter(r => !r.pass);
  if (failed.length) throw Object.assign(new Error(`${failed.length} of ${results.length} checks failed: ${failed.map(f => f.name).join('; ')}`), { results });
  return { checks: results };
}, { timeoutMs: (Number(a.timeout) || 180) * 1000, idleMs: (Number(a.idle) || 120) * 1000 });
