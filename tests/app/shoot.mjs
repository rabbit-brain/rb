// The screenshot runner: every view of rb open and rb page on a record, at each width and colour scheme, plus rb page
// printed and with its script off, into a folder of its own with a manifest of every shot.
//   node tests/app/shoot.mjs --rb <rb> --record <checkout> --out <new or empty folder>
//        [--widths 390,1280] [--schemes light,dark] [--surfaces open,page] [--html-app] [--expect <text>] [--timeout <s>]
// --html-app adds rb page's no-script shots again with class "app" set on <html>, the shell's three-pane layout forced
// on its document. --expect is text the record shows, waited for before each rb open shot. A view the runner plans
// and cannot reach, or a page that throws, fails the run (exit 1) after the manifest is written; 3 is no Chrome.
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { findChrome, launchChrome, parseArgs, rbJson, run, sleep, startRbOpen } from './cdp.mjs';

// rb open's old tab page: the panes its rows open, each with the tab it opens from, the clicks and what shows it is open
const OPEN_PANES = [
  { view: 'experiment-pane', tab: 'experiments', clicks: ["document.querySelector('#cat [data-eid]')"] },
  { view: 'receipt-pane', tab: 'runs', clicks: ["document.querySelector('#cat [data-rcpt]')"] },
  { view: 'compare-pane', tab: 'runs', clicks: ["document.querySelectorAll('#cat [data-runsel]')[0]", "document.querySelectorAll('#cat [data-runsel]')[1]",
    "document.querySelector('#cat [data-cmp]')"] },
].map(p => ({ ...p, shows: "(e => !!e && !e.hidden)(document.getElementById('pane'))" }));
// a view that is part of a tab, opened by its address, on both surfaces
const ANCHORS = ['your-call'];
const TABS = `[...document.querySelectorAll('[role="tab"][href^="#"]')].map(a => a.getAttribute('href').slice(1))`;
// rb page's views (the shell's, since the switch): each a section.view of the document, the one shown marked .on
const PAGE_VIEWS = `[...document.querySelectorAll('main section.view')].map(v => v.id)`;
// the shell's cards rb open opens in the inspector for its shots: the first finding and the first run the views link
const SHELL_PANES = [{ view: 'finding-card', kind: 'criterion' }, { view: 'run-card', kind: 'run' }];
// (rb open serves a card when it is opened: the views' links name them)
const SHELL_CARDS = `(() => { const id = (sel, attr) => { const e = document.querySelector(sel); return e ? e.getAttribute(attr).replace(/^#/, '') : null; };
  return { criterion: id('main a[href^="#c-"]', 'href'), run: id('#runs [data-for^="run-"]', 'data-for') || id('main a[href^="#run-"]', 'href') }; })()`;
const CARD_SHOWN = id => `!!document.querySelector('.ibody > .card[data-id=' + JSON.stringify(${JSON.stringify(id)}) + ']')`;
const PAGE_SHOWN = v => `(e => !!e && e.classList.contains('on'))(document.getElementById(${JSON.stringify(v)}))`;
// a view is shown: its tab is the one selected, or, for a view that is no tab, an element has its id
const SHOWN = v => `(t => t ? t.getAttribute('aria-selected') === 'true' : !!document.getElementById(${JSON.stringify(v)}))(document.querySelector('[role="tab"][href="#' + ${JSON.stringify(v)} + '"]'))`;

const a = parseArgs(process.argv.slice(2), { flags: ['html-app'] });
if (!a.rb || !a.record || !a.out) {
  process.stderr.write('give --rb, --record and --out\n');
  process.exit(2);
}
const out = resolve(a.out);
if (existsSync(out) && readdirSync(out).length) {
  process.stderr.write(`${out} is not empty: give a new or empty folder\n`);
  process.exit(2);
}
mkdirSync(out, { recursive: true });
const list = (v, d) => (v || d).split(',').map(s => s.trim()).filter(Boolean);
const widths = list(a.widths, '390,1280').map(Number);
const schemes = list(a.schemes, 'light,dark');
const surfaces = list(a.surfaces, 'open,page');
const height = w => (w < 600 ? 844 : 900);

const manifest = { record: resolve(a.record), widths, schemes, surfaces, html_app: !!a['html-app'], shots: [], misses: [], errors: [] };

function writeManifest(extra = {}) {
  writeFileSync(join(out, 'manifest.json'), JSON.stringify({ ...manifest, ...extra }, null, 1) + '\n');
}

await run(async () => {
  const chrome = findChrome();
  let browser, page;
  try {
    const open = surfaces.includes('open') ? await startRbOpen({ rb: a.rb, cwd: a.record }) : null;
    let pageFile = null;
    if (surfaces.includes('page')) {
      pageFile = join(out, 'page.html');
      await rbJson({ rb: a.rb, cwd: a.record, argv: ['page', '--out', pageFile] });
      manifest.page = 'page.html';
    }
    browser = await launchChrome({ chrome });
    manifest.chrome = browser.version;
    page = await browser.page();

    const shoot = async (meta, { full = true } = {}) => {
      const name = [meta.surface, meta.view, meta.width, meta.scheme, ![meta.view, 'script'].includes(meta.mode) && meta.mode, meta.html_app && 'app'].filter(Boolean).join('-') + '.png';
      const before = page.errors.length;
      const image = await page.shot(join(out, name), { full });
      const errors = page.errors.slice(before);
      manifest.shots.push({ file: name, ...meta, image, errors });
      if (errors.length) manifest.errors.push({ file: name, errors });
    };
    const load = async (url, { width, scheme, type = '', script = true }) => {
      await page.noScript(false);
      await page.blank();
      await page.viewport(width, height(width));
      await page.media({ type, scheme });
      await page.noScript(!script);
      await page.goto(url);
      // --expect is text the record shows: rb open's old tab script draws it, so it is waited for there in what is
      // shown; the shell's document (rb page's, and rb open's since 4b) holds it whatever view is shown (its app mode
      // shows one view at a time)
      const want = JSON.stringify(a.expect);
      const said = url.startsWith('http') ? `(document.querySelector('main section.view') ? document.body.textContent : document.body.innerText)` : 'document.body.textContent';
      if (script && a.expect && !(await page.waitFor(`document.body && ${said}.includes(${want})`, 30000))) {
        throw new Error(`${url} never showed ${JSON.stringify(a.expect)}`);
      }
      await page.settle({ script });
    };
    const missed = (meta, why) => manifest.misses.push({ ...meta, why });

    // rb open drawing the shell (BP 4b): its views by their addresses, as rb page's, then a finding's and a run's card
    // in the inspector, each opened by its address
    const shellOpen = async home => {
      const views = (await page.ev(PAGE_VIEWS)) || [];
      if (!views.length) return false;
      const anchors = ANCHORS.filter(v => !views.includes(v));
      const cards = await page.ev(SHELL_CARDS);
      manifest.open = { views, anchors, cards: SHELL_PANES.map(p => p.view) };
      for (const width of widths) for (const scheme of schemes) {
        for (const view of [...views, ...anchors]) {
          const meta = { surface: 'open', view, width, scheme, mode: 'script', html_app: false, address: '#' + view };
          await load(home + '#' + view, { width, scheme });
          if (!(await page.ev(PAGE_SHOWN(view)))) { missed(meta, 'it is not shown at its address'); continue; }
          await shoot(meta);
        }
        for (const pane of SHELL_PANES) {
          const id = cards[pane.kind];
          const meta = { surface: 'open', view: pane.view, width, scheme, mode: 'script', html_app: false, address: id ? '#' + id : '' };
          if (!id) { missed(meta, 'the record has no such card'); continue; }
          await load(home + '#' + id, { width, scheme });
          if (!(await page.ev(CARD_SHOWN(id)))) { missed(meta, 'the card did not open'); continue; }
          await shoot(meta, { full: false });          // the inspector sits beside the view, or over it as a sheet
        }
      }
      return true;
    };

    if (open) {
      const home = open.url + '/';
      await load(home, { width: widths[0], scheme: schemes[0] });
      if (!(await shellOpen(home))) {
        const tabs = (await page.ev(TABS)) || [];
        if (!tabs.length) throw new Error(`rb open at ${home} draws no views`);
        manifest.open = { tabs, anchors: ANCHORS, panes: OPEN_PANES.map(p => p.view) };
        for (const width of widths) for (const scheme of schemes) {
          for (const view of [...tabs, ...ANCHORS]) {
            const meta = { surface: 'open', view, width, scheme, mode: 'script', html_app: false, address: '#' + view };
            await load(home + '#' + view, { width, scheme });
            if (!(await page.ev(SHOWN(view)))) { missed(meta, 'it is not shown at its address'); continue; }
            await shoot(meta);
          }
          for (const pane of OPEN_PANES) {
            const meta = { surface: 'open', view: pane.view, width, scheme, mode: 'script', html_app: false, address: '#' + pane.tab };
            await load(home + '#' + pane.tab, { width, scheme });
            let clicked = true;
            for (const click of pane.clicks) {
              if (!(await page.clickEl(click))) { clicked = false; break; }
              await page.settle();
            }
            if (!clicked || !(await page.ev(pane.shows))) { missed(meta, clicked ? 'the pane did not open' : 'nothing to click'); continue; }
            await shoot(meta, { full: false });          // a pane sits over the screen: a whole-page shot would move it
          }
        }
      }
    }

    if (pageFile) {
      const file = pathToFileURL(pageFile).href;
      await load(file, { width: widths[0], scheme: schemes[0] });
      const tabs = (await page.ev(PAGE_VIEWS)) || [];
      if (!tabs.length) throw new Error(`rb page's ${pageFile} draws no views`);
      const anchors = ANCHORS.filter(v => !tabs.includes(v));
      const views = [...tabs, ...anchors];
      manifest.page_views = { tabs, anchors };
      const present = async (meta, view) => {
        const { root } = await page.send('DOM.getDocument', { depth: 0 });
        const { nodeId } = await page.send('DOM.querySelector', { nodeId: root.nodeId, selector: `[id="${view}"]` });
        if (!nodeId) missed(meta, 'no element has this id');
        return !!nodeId;
      };
      for (const width of widths) for (const scheme of schemes) {
        for (const view of views) {
          const meta = { surface: 'page', view, width, scheme, mode: 'script', html_app: false, address: '#' + view };
          await load(file + '#' + view, { width, scheme });
          if (!(await page.ev(PAGE_SHOWN(view)))) { missed(meta, 'it is not shown at its address'); continue; }
          await shoot(meta);
        }
        await load(file, { width, scheme, type: 'print' });
        await shoot({ surface: 'page', view: 'print', width, scheme, mode: 'print', html_app: false, address: '' });
        for (const app of a['html-app'] ? [false, true] : [false]) {
          await load(file, { width, scheme, script: false });
          const html_class = app ? await page.htmlClass('app') : await page.htmlClass();
          if (app && !html_class.split(/\s+/).includes('app')) throw new Error('class "app" did not stay on <html>');
          await shoot({ surface: 'page', view: 'document', width, scheme, mode: 'no-script', html_app: app, html_class, address: '' });
          for (const view of views) {
            const meta = { surface: 'page', view, width, scheme, mode: 'no-script', html_app: app, html_class, address: '#' + view };
            await page.goto(file + '#' + view);
            await sleep(200);
            if (await present(meta, view)) await shoot(meta, { full: false });
          }
        }
      }
      await load(file, { width: 1280, scheme: 'light', type: 'print' });
      const pdf = await page.pdf(join(out, 'page-print.pdf'));
      manifest.shots.push({ file: 'page-print.pdf', surface: 'page', view: 'print', width: null, scheme: 'light', mode: 'print', html_app: false, address: '', pdf });
    }
  } finally {
    writeManifest();
  }
  if (manifest.misses.length) throw new Error(`${manifest.misses.length} views could not be reached: ${manifest.misses.map(m => `${m.surface} ${m.view} (${m.why})`).slice(0, 5).join('; ')}`);
  if (manifest.errors.length) throw new Error(`the page threw on ${manifest.errors.length} shots: ${manifest.errors[0].errors[0]}`);
  return { out, shots: manifest.shots.length, manifest: join(out, 'manifest.json') };
}, { timeoutMs: Number(a.timeout || 1200) * 1000 });
