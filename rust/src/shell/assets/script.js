(() => {
  'use strict';
  // The shell's one script (SHELL-SPEC sections 2 and 3). The page is a complete document without it; this turns it into
  // the app: a working view in the centre and an inspector stack beside it. It shows, hides and clones what the page
  // holds. It computes nothing.
  const H = document.documentElement;
  // set while the head parses, so the long document never flashes before the app takes it
  H.classList.add('app');
  // the viewer's own choices, kept in this browser: the theme, and whether single keys act
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (_) {} return null; };
  // hosted: the hub frames the page with sandbox="allow-scripts" and no allow-same-origin, an opaque origin ("null")
  // where storage throws. Its own bar above names the record and the sync time, so this page's top bar folds away and
  // its time and rb's version move to the Overview's header line (HOSTED-VIEW, the hosted-mode pass)
  // (a frame on a local file has a null origin too, and keeps storage: it is not hosted)
  const HOSTED = (() => {
    try { if (window.self === window.top || String(window.origin || location.origin) !== 'null') return false; } catch (_) { return true; }
    try { localStorage.getItem('rb-theme'); return false; } catch (_) { return true; }
  })();
  // who looks: the hub says it on the page it serves, `<meta name="rb-viewer" content="owner">` on the owner's own
  // address and "reader" on a link shared with others. The owner gets the working view; a reader gets the record to
  // read, with every warning and open item and none of the actions (no call's buttons, reason or line, no rb command,
  // no Copy). With no meta (rb page, rb open, an export opened from disk) the page is as it was. Read once, here, as the
  // hub puts the meta before this script; else once more when the document is in
  const viewer = () => { const m = document.querySelector('meta[name="rb-viewer"]'), v = m && (m.getAttribute('content') || '').trim(); return v === 'owner' || v === 'reader' ? v : null; };
  // what a reader is not shown: the parts that make a call (a row's Review button where its title is a link), and the
  // boxes and chips that hold an rb command (marked `rbcmd` below), never the warnings, open items and trust lines
  // around them; a recipe stays as text to read. Until the app has marked them (and if it never starts) every command
  // box is held back too, so none shows for a moment
  const READER_CSS = 'html[data-viewer="reader"] :is(.rbcmd, .decide, [data-level="decide"], .queue li:has(a[href]:not(.qbtn)) .qbtn, .composer, .seg, input.why, .cp, .cprow, .keys):not(:is(#rerun, .rrx) *) { display: none !important; }' +
    ' html[data-viewer="reader"]:not(.ready) :is(.cmdbox, pre.cmd):not(:is(#rerun, .rrx) *) { display: none !important; }';
  function setViewer(v) {
    VIEWER = v;
    if (!v) return;
    H.dataset.viewer = v;
    if (v === 'reader' && !document.getElementById('rb-viewer')) {
      const css = document.createElement('style'); css.id = 'rb-viewer'; css.textContent = READER_CSS;
      (document.head || H).appendChild(css);
    }
  }
  let VIEWER = null;
  setViewer(viewer());
  // inside the hub (its frame, or a viewer meta, read again at init): the hub's bar names the record, so this page's folds
  const inHub = () => HOSTED || !!VIEWER || !!document.querySelector('meta[name="rb-viewer"]');
  let INHUB = inHub();
  if (INHUB) H.classList.add('hosted');
  // the export's attributes name the calls "Open calls"; its owner reads "Your call" there
  function ownWords(root) {
    if (VIEWER !== 'owner') return;
    for (const a of ['data-title', 'data-crumb', 'data-rootname', 'aria-label'])
      $$('[' + a + '^="Open calls"]', root).forEach(e => e.setAttribute(a, 'Your call' + e.getAttribute(a).slice(10)));
  }
  const t0 = store('rb-theme');
  if (t0 === 'light' || t0 === 'dark') H.dataset.theme = t0;
  let KEYS = store('rb-keys') !== 'off';
  // the address keeps the view and the cards shown; a frame whose address cannot be rewritten (a hosted page drawn from
  // srcdoc, an opaque origin) keeps them on the screen alone, and the app still works
  const hist = (how, s, url) => { try { history[how](s, '', url); } catch (_) {} };

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const byId = id => (id ? document.getElementById(id) : null);
  const esc = s => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/[^\w-]/g, '\\$&'));
  // rb's view names before the shell, and rb open's tabs, each the view it is now
  const ALIAS = { record: 'runs', findings: 'research', top: 'overview', questions: 'overview', 'found-recordings': 'overview' };
  // the width from which the inspector is a column beside the work; under it, a sheet over the work
  const WIDE = window.matchMedia('(min-width: 960px)');
  // c: the working view's id; s: the inspector's stack of card ids, newest last; f: the part opened, if any
  let st = { c: 'overview', s: [], f: null };
  let main, aside, ibody, crumbs, backBtn, linkBtn, closeBtn, live, helpBox, opener = null;
  // what took focus when the inspector last went back to its root
  let rooted = null;
  // what follows the anchor in the address (rb open's '&place=<id>&at=<commit>'), kept as it came
  let tail = '';

  document.addEventListener('DOMContentLoaded', () => {
    // a start that throws leaves the document as it is, every view shown ('ready' keys the hiding)
    try { init(); H.classList.add('ready'); if (rerunCold) openRerun(null); } catch (e) { console.error(e); }
  });

  function init() {
    main = byId('main');
    if (!VIEWER) setViewer(viewer());
    ownWords(document);
    keepStore();
    if (!INHUB && (INHUB = inHub())) H.classList.add('hosted');
    if (INHUB) foldTop();
    addControls();
    rerunInit();
    buildInspector();
    sayTheme();
    const kb = $('button[data-keys]'); if (kb) kb.setAttribute('aria-expanded', 'false');
    enhanceAll(main);
    if (rerunDlg) enhanceAll(rerunDlg);
    document.addEventListener('click', onClick);
    document.addEventListener('keydown', onKey);
    document.addEventListener('input', onInput);
    setInterval(() => sayChanged(main), 60000);
    const nav = byId('nav');
    if (nav) nav.addEventListener('keydown', onNavKey);
    // rb open's live module replaced the navigator's and the views' children with a newer drawing of the record: the
    // new parts get their copy buttons and composers, and the app stands where it stood (the view, the selected row,
    // the inspector's stack cloned again; a card gone from the record says so, as an address that names nothing
    // does), moving no focus and no scroll and telling of no card opened; the module puts back the rest
    document.addEventListener('rb:swapped', e => {
      ownWords(document); enhanceAll(byId('nav')); enhanceAll(main);
      const kept = inspectorHeld();
      const now = id => { const bare = id.replace(/^\?/, ''); return byId(bare) ? bare : '?' + bare; };
      apply({ c: st.c, s: st.s.map(now), f: st.f && byId(st.f) ? st.f : null }, { quiet: true });
      inspectorBack(kept);
      e.preventDefault();
    });
    window.addEventListener('popstate', e => {
      if (rerunHash()) return;
      if (rerunDlg && rerunDlg.open) rerunDlg.close();
      if (placeMoved()) { showPlace(e.state && e.state.c ? e.state : null); return; }
      if (e.state && e.state.c) { apply(e.state, { scroll: true, focus: true, kept: e.state.c === st.c }); return; }
      // an address typed or set by hand arrives here first, with no state: resolve it and write its state, so the
      // hashchange that follows finds it already applied
      const s = fromHash(true);
      hist('replaceState', s, '#' + hashOf(s) + tail);
      apply(s, { scroll: true, focus: true });
    });
    // a hash followed or typed outside the app's own links; Back and Forward arrive through popstate
    window.addEventListener('hashchange', () => {
      if (placeMoved()) { showPlace(null); return; }
      sayFailed('');
      // the browser's fragment scroll after Back can drop focus: the card's title takes it again, or at the root what
      // took it there (the row that opened the card, or the view's heading)
      if (decode(location.hash.slice(1)) === hashOf(st) + tail) { if (document.activeElement === document.body) { if (st.s.length) focusTop(); else if (rooted && document.contains(rooted)) rooted.focus({ preventScroll: true }); } return; }
      const s = fromHash(true);
      plainTail();
      hist('replaceState', s, '#' + hashOf(s) + tail);
      apply(s, { scroll: true });
    });
    // across 960 the open card turns from a column into a sheet, or back; a sheet takes focus as it opens
    const onWide = () => { const had = aside.contains(document.activeElement); renderInspector(false); if (had) focusTop(); };
    if (WIDE.addEventListener) WIDE.addEventListener('change', onWide); else WIDE.addListener(onWide);
    // a reload keeps the entry's state: the place and version its address names are shown again too
    if (history.state && history.state.c) { apply(history.state, { scroll: true }); if (placeMoved()) showPlace(history.state); return; }
    const s0 = fromHash();
    if (s0.s.length && s0.s[0][0] !== '?') {
      // a cold link to a card: its working object first, so the browser's Back lands there and not off the page
      hist('replaceState', { c: s0.c, s: [], f: null }, '#' + s0.c + tail);
      s0.p = [];
      hist('pushState', s0, '#' + hashOf(s0) + tail);
    } else hist('replaceState', s0, '#' + hashOf(s0) + tail);
    apply(s0, { scroll: true });
    // a link or a reload that keeps another place or a past version: rb open drew the place it started on, live
    if (placeMoved()) showPlace(null); else plainTail();
  }

  // ---------- rb open's places (BP 4b): a place or a version is chosen only on the page, by its links; rb open's live
  // module fetches it from rb open and swaps it in, and the address keeps it ('&place=<id>&at=<commit>'), so a reload,
  // Back and Forward and a bookmark show it again. On a page (no live module) nothing here acts.
  const queryOf = (place, at) => { const q = []; if (place) q.push('place=' + encodeURIComponent(place)); if (at) q.push('at=' + encodeURIComponent(at)); return q.length ? '?' + q.join('&') : ''; };
  const tailOf = q => (q ? '&' + q.slice(1) : '');
  // the place and version an address's tail names
  function tailQuery(t) {
    let place = '', at = '';
    for (const part of t.split('&')) { const i = part.indexOf('='); if (i < 0) continue; const k = part.slice(0, i), v = part.slice(i + 1); if (k === 'place') place = v; else if (k === 'at') at = v; }
    return queryOf(place, at);
  }
  // the query rb open drew the page shown for: its live module's tag says it as the page came, and each swap after
  let shown = null;
  const shownQuery = () => { if (shown === null) { const t = byId('rb-live'); shown = t ? t.dataset.query || '' : ''; } return shown; };
  document.addEventListener('rb:swapped', e => { if (e.detail && typeof e.detail.query === 'string') shown = e.detail.query; });
  // whether the address names another place or version than the one shown
  function placeMoved() {
    if (!window.rbLive) return false;
    const raw = decode(location.hash.slice(1)).replace(/^#+/, ''), i = raw.indexOf('&');
    return tailQuery(i >= 0 ? raw.slice(i) : '') !== shownQuery();
  }
  // an address whose tail names what is shown in other words (a key twice, one rb does not use) is written as rb
  // open names what it shows, so a link copied from it names the same
  function plainTail() {
    if (!window.rbLive || tail === tailOf(shownQuery())) return;
    tail = tailOf(shownQuery());
    hist('replaceState', history.state, '#' + hashOf(st) + tail);
  }
  function sayFailed(why) { const f = $('#where .failed'); if (f) f.textContent = why; }
  // the place rb open started on, as its Where picker names it
  function startLabel() { const a = $('#where a[data-place=""][data-at=""]'); return a ? a.textContent.trim() : 'the place it started on'; }
  let asking = 0, inFlight = 0;
  // the place and version the address names, fetched and shown, then the view or card it names; one rb open does not
  // list now, or cannot show, falls back to the place it started on, live, and says why
  async function showPlace(state) {
    const mine = ++asking;
    const typed = fromHash(true), s = state || typed;
    const q = tailQuery(tail);
    sayFailed('');
    inFlight++;
    let ok;
    try { ok = await window.rbLive.show(q); } finally { inFlight--; }
    if (ok) {
      if (mine !== asking) return;
      // as rb open names what it showed: the place it started on, live, is the view alone
      tail = tailOf(shownQuery());
      hist('replaceState', s, '#' + hashOf(s) + tail);
      apply(s, { scroll: true });
      return;
    }
    if (mine !== asking || !q) return;
    const why = failedWhy;
    tail = '';
    if (shownQuery() && !(await window.rbLive.show(''))) return;
    if (mine !== asking) return;
    const back = resolve(hashOf(s), false, true, true);
    hist('replaceState', back, '#' + hashOf(back));
    apply(back, { scroll: true });
    sayFailed((why || 'The place in this link is not one rb lists now.') + ' rb open shows ' + startLabel() + ', live, instead.');
  }
  // what rb open said of the last choice it refused
  let failedWhy = '';
  document.addEventListener('rb:failed', e => { failedWhy = (e.detail && e.detail.error) || ''; });
  // a choice by a link: the pickers, the scope bar's way back, Elsewhere's places, a run's "recorded in". The bar's
  // links keep the view shown; any other opens what it names there
  async function choosePlace(a) {
    const mine = ++asking;
    const q = queryOf(a.dataset.place, a.dataset.at || '');
    const pick = a.closest('details.pick'); if (pick) pick.open = false;
    const anchor = a.closest('#where, #scope, #withheld') ? hashOf(st) : decode((a.getAttribute('href') || '#').slice(1)).split('&')[0] || hashOf(st);
    failedWhy = ''; sayFailed('');
    // a choice of the place shown still asks again while an earlier choice is on its way: the live module answers the
    // latest ask alone, so the earlier one is never swapped in after it
    if (q !== shownQuery() || inFlight) {
      inFlight++;
      let ok;
      try { ok = await window.rbLive.show(q); } finally { inFlight--; }
      if (!ok) { if (mine === asking) sayFailed(failedWhy || 'rb open could not show it.'); return; }
      if (mine !== asking) return;
    }
    tail = tailOf(shownQuery());
    const s = resolve(anchor, false, true, false);
    s.p = st.c === s.c ? st.s.slice() : null;
    hist('pushState', s, '#' + hashOf(s) + tail);
    apply(s, { focus: true, scroll: true });
  }

  // the document's cards (rb draws them in one store at the end of main): in the app they open in the inspector, so
  // on the screen the store shows under no view; printed, the whole document is the page, and they print. A rule for
  // the screen alone, so a print never waits on the script to hear of it
  function keepStore() {
    if (!$(':scope > .nodes', main)) return;
    const css = document.createElement('style');
    css.media = 'screen';
    css.textContent = 'html.app.ready main > .nodes { display: none; }';
    document.head.appendChild(css);
  }

  // hosted: the top bar's time and version go quiet on the Overview's header line, and its controls (the theme, the
  // keys) into the navigator, where every view keeps them; the bar itself is hidden by html.hosted's rule
  function foldTop() {
    const top = byId('top');
    if (!top) return;
    const stamp = $('.stamp', top), ov = byId('overview');
    if (stamp && ov) {
      let line = $('.ovh', ov);
      if (!line) {
        line = document.createElement('p'); line.className = 'counts ovh';
        const h = $('h1', ov);
        if (h) h.after(line); else ov.prepend(line);
      }
      // said as when the page was made, since the line's other time is when the record changed
      const s = document.createElement('span'); s.className = 'stamp';
      s.textContent = 'made ' + stamp.textContent;
      if (line.childNodes.length) line.append(document.createTextNode(' \u00b7 '));
      line.append(s);
    }
    const ctl = $$('.end .ctl', top), nav = byId('nav');
    if (ctl.length && nav) {
      let foot = $(':scope > .navctl', nav);
      if (!foot) { foot = document.createElement('div'); foot.className = 'navctl'; nav.append(foot); }
      ctl.forEach(b => foot.append(b));
    }
  }

  // the export draws About alone in the navigator: the hub's copy of the script adds the theme and keys beside it
  function addControls() {
    const foot = $('#nav > .navctl');
    if (!foot || $('button[data-theme]')) return;
    const st = 'fill="none" stroke="currentColor" stroke-width="1.5"', sv = (c, b) => `<svg class="th th-${c}" viewBox="0 0 16 16" aria-hidden="true">${b}</svg>`;
    const t = document.createElement('button'); t.type = 'button'; t.className = 'ctl ico'; t.dataset.theme = '';
    t.setAttribute('aria-label', 'Theme: system'); t.title = 'Theme: system';
    t.innerHTML = sv('sys', `<circle cx="8" cy="8" r="6" ${st}/><path d="M8 2a6 6 0 0 1 0 12z" fill="currentColor"/>`) +
      sv('light', `<circle cx="8" cy="8" r="3" ${st}/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3" ${st} stroke-linecap="round"/>`) +
      sv('dark', `<path d="M13.5 10.2A6 6 0 0 1 5.8 2.5a6 6 0 1 0 7.7 7.7z" ${st} stroke-linejoin="round"/>`);
    const k = document.createElement('button'); k.type = 'button'; k.className = 'ctl'; k.dataset.keys = '';
    k.setAttribute('aria-label', 'Keyboard'); k.textContent = '?';
    foot.append(t, k);
  }

  // with nothing picked and nothing of the view's own, from 960 the inspector is a slim rail and the working view takes
  // the width; its sentence stays for a screen reader, fixed at the corner so it runs past nothing
  const RAIL_CSS = '@media screen and (min-width: 960px) {' +
    ' html.app.irail body { grid-template-columns: var(--navw) minmax(0, 1fr) 14px; }' +
    ' html.app.irail aside.insp { overflow: hidden; background: var(--bg); }' +
    ' html.app.irail aside.insp > .ibar { display: none; }' +
    ' html.app.irail aside.insp > .ibody { position: fixed; top: 0; left: 0; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; } }' +
    '@media screen and (min-width: 960px) and (max-width: 1279px) { html.app.irail body { grid-template-columns: minmax(0, 1fr) 14px; } }' +
    // a term's hint takes no clicks
    ' .term::after { pointer-events: none; }' +
    // closed while hovered: no hover hint until the pointer leaves
    ' .term[data-hush]::after { content: none !important; }' +
    // on a touch width a call's choices say no key letter, and the composed line wraps whole in its box
    ' @media (max-width: 639px), (hover: none) and (pointer: coarse) { html.app .seg kbd { display: none; } }' +
    ' @media (max-width: 639px) { html.app .composer .cmd { white-space: pre-wrap; overflow-wrap: anywhere; } }';
  function buildInspector() {
    aside = document.createElement('aside');
    aside.className = 'insp'; aside.id = 'insp'; aside.setAttribute('aria-label', 'Inspector');
    aside.innerHTML =
      '<div class="ibar">' +
      '<button type="button" class="ibtn back" data-act="back" aria-label="Back" title="Back">‹</button>' +
      '<nav class="crumbs" aria-label="Inspector path"></nav><span class="bact"></span>' +
      '<button type="button" class="ibtn" data-act="link" title="Copy a link to this card">Link</button>' +
      '<button type="button" class="ibtn" data-act="close" aria-label="Close" title="Close  Esc">✕</button>' +
      '</div><div class="ibody"></div>';
    main.after(aside);
    // the slim rail's rules travel with the script that sets its class, so every page the script runs on folds an
    // empty inspector alike: rb page, rb open and the export the hub serves (whose styles leave out A's own)
    const rail = document.createElement('style');
    rail.id = 'rb-rail';
    rail.textContent = RAIL_CSS;
    document.head.appendChild(rail);
    ibody = $('.ibody', aside); crumbs = $('.crumbs', aside);
    backBtn = $('[data-act="back"]', aside); linkBtn = $('[data-act="link"]', aside); closeBtn = $('[data-act="close"]', aside);
    live = document.createElement('div'); live.className = 'sr'; live.setAttribute('aria-live', 'polite'); document.body.appendChild(live);
  }

  // ---------- where you are: a view in the centre, a stack in the inspector; the address is always a document anchor
  function isView(el) { return !!(el && el.classList.contains('view')); }
  function decode(s) { try { return decodeURIComponent(s); } catch (_) { return s; } }
  // what a redraw of the inspector would lose: the call chosen in its composer, the reason typed, and where focus was
  // (by what the part is, and its place among the inspector's focusable parts), put back on the new copy as it was
  // (a card's title takes focus by tabindex -1, which its new copy has not: it is put back as the title)
  const FOCUSABLE = 'a[href], button, input, summary, [tabindex]:not([tabindex="-1"])';
  function inspectorHeld() {
    const box = $('.ibody .decide'), inp = box && $('input.why', box), ae = document.activeElement;
    return { chosen: box ? box.dataset.chosen || null : null, why: inp ? inp.value : '', inside: aside.contains(ae),
      at: aside.contains(ae) ? $$(FOCUSABLE, aside).indexOf(ae) : -1, sel: inp && ae === inp ? [inp.selectionStart, inp.selectionEnd] : null,
      what: aside.contains(ae) ? whatItIs(ae) : null };
  }
  // what a focusable part is, as its new copy says it too: its kind, where it goes or what it does, and its words
  function whatItIs(el) {
    return [el.tagName, el.getAttribute('href'), el.dataset.act, el.dataset.action, el.dataset.id, el.className, el.textContent.trim()].join('\n');
  }
  function inspectorBack(k) {
    const box = $('.ibody .decide');
    // a past version puts back no chosen call: it composes no command
    if (box && k.chosen && !noCmd() && $('.seg button[data-action="' + esc(k.chosen) + '"]', box)) {
      box.dataset.chosen = k.chosen;
      $$('.seg button', box).forEach(x => x.setAttribute('aria-pressed', String(x.dataset.action === k.chosen)));
      const comp = $('.composer', box); if (comp) comp.hidden = false;
      const inp = $('input.why', box); if (inp) inp.value = k.why;
      compose(box);
    }
    if (!k.inside) return;
    const all = $$(FOCUSABLE, aside), same = k.what ? all.filter(x => whatItIs(x) === k.what) : [];
    // the same part: the one that reads as it did, the nearest to its old place when several do; gone, the title
    const to = same.length === 1 ? same[0] : (same.length > 1 ? same.reduce((a, b) => Math.abs(all.indexOf(b) - k.at) < Math.abs(all.indexOf(a) - k.at) ? b : a) : null);
    if (to) {
      to.focus({ preventScroll: true });
      if (k.sel && to.matches('input.why')) to.setSelectionRange(k.sel[0], k.sel[1]);
    } else focusTop();
  }

  // the address: '#<anchor>', then rb open's place and version ('&place=<id>&at=<commit>'), which this keeps as they
  // came; an extra '#' is dropped ('##q-q1' opens q-q1 and is written back with one)
  // `typed`: an address typed or followed while the page is open, which stays over the working object
  function fromHash(typed) {
    const raw = decode(location.hash.slice(1)).replace(/^#+/, '');
    const i = raw.indexOf('&');
    tail = i >= 0 ? raw.slice(i) : '';
    return resolve((i >= 0 ? raw.slice(0, i) : raw) || 'overview', false, true, typed);
  }
  function hashOf(s) { return s.f || (s.s && s.s.length ? s.s[s.s.length - 1].replace(/^\?/, '') : s.c); }
  // a card already on the trail cuts it back to that card; any other is pushed on top
  function pushId(s, id) { const i = s.indexOf(id); return i >= 0 ? s.slice(0, i + 1) : s.concat(id); }
  function homeOf(card) { return byId(card.dataset.home) ? card.dataset.home : ((card.closest('.view') || {}).id || 'overview'); }

  // what an anchor opens: from the centre a card starts a new trail, from the inspector it extends it; `fresh` is an
  // address arriving cold, which opens a card over the view it belongs to
  function resolve(id, fromInsp, fresh, typed) {
    id = id.replace(/^#+/, '');
    const old = oldAnchor(id);
    if (old && old.view) id = old.view;
    else if (old) {
      if (fresh) return { c: old.home || homeOf(byId(old.card)), s: [old.card], f: null };
      return { c: st.c, s: fromInsp ? pushId(st.s, old.card) : [old.card], f: null };
    }
    id = ALIAS[id] || (id.startsWith('tab-') ? ALIAS[id.slice(4)] || id.slice(4) : id);
    const el = byId(id);
    // an address that names nothing says so in the inspector, over the working object, and keeps the address as typed
    if (!el) return { c: (fresh && !typed) || !byId(st.c) ? 'overview' : st.c, s: id ? ['?' + id] : [], f: null };
    if (el.closest('aside')) return { c: fresh || !byId(st.c) ? 'overview' : st.c, s: [], f: null };
    // a view: from the inspector beside the work the trail stays; under 960 the sheet closes onto the view
    if (isView(el)) return { c: id, s: fresh ? [] : (fromInsp && WIDE.matches ? st.s.slice() : []), f: null };
    const card = el.classList.contains('card') ? el : el.closest('.card');
    if (card) {
      // a part a reader is not shown (a call's Decide part): the card, from its top
      const sub = el === card || (VIEWER === 'reader' && el.closest(DECIDE)) ? null : id;
      if (fresh) return { c: homeOf(card), s: [card.id], f: sub };
      return { c: st.c, s: fromInsp ? pushId(st.s, card.id) : [card.id], f: sub };
    }
    const v = el.closest('.view');
    // P12: another view's fold, followed from a view or a card, opens in the inspector; a cold address lands on its view
    if (!fresh && foldOf(el) && (!v || v.id !== st.c)) return { c: st.c, s: fromInsp ? pushId(st.s, id) : [id], f: null };
    const c = v ? v.id : (fresh ? 'overview' : st.c);
    return { c, s: fresh ? [] : ((fromInsp || c === st.c) ? st.s.slice() : []), f: id };
  }

  // rb open's anchors before the shell: '#c-<cid>:<qid>' (a criterion under its question, ':unfiled' for none) and
  // '#all-c-<cid>' (its row in Findings) open the criterion's card, over its question or over All criteria;
  // '#calls-<qid>' is the question. Each only where the page holds no element of that id.
  function oldAnchor(id) {
    if (byId(id)) return null;
    let m = id.match(/^all-c-(.+)$/);
    if (m && isCard(byId('c-' + m[1]))) return { card: 'c-' + m[1], home: byId('criteria') ? 'criteria' : null };
    m = id.match(/^c-([^:]+):(.+)$/);
    if (m && isCard(byId('c-' + m[1]))) return { card: 'c-' + m[1], home: isView(byId('q-' + m[2])) ? 'q-' + m[2] : null };
    m = id.match(/^calls-(.+)$/);
    if (m && isView(byId('q-' + m[1]))) return { view: 'q-' + m[1] };
    return null;
  }
  function isCard(el) { return !!(el && el.classList.contains('card')); }
  // the fold a part outside the cards is, sits in or wraps
  function foldOf(el) {
    if (!el || el.closest('.card, aside, #nav, header.top')) return null;
    return el.closest('details.fold') || $(':scope > details.fold', el);
  }
  // a trail part's name: a card's crumb, else its fold's summary
  function crumbOf(id) {
    const el = byId(id);
    if (!el) return id.replace(/^\?/, '');
    if (el.dataset.crumb) return el.dataset.crumb;
    const f = foldOf(el), sum = f && $(':scope > summary', f);
    return sum ? sum.textContent.trim() : id;
  }

  function go(s, o) {
    sayFailed('');
    // the same place again (a row of the card already open) is no new step in history
    if (s.c === st.c && (s.f || null) === st.f && (s.s || []).join('\n') === st.s.join('\n')) { apply(s, Object.assign({ focus: true }, o || {})); return; }
    s.p = st.c === s.c ? st.s.slice() : null;
    hist('pushState', s, '#' + hashOf(s) + tail);
    apply(s, Object.assign({ focus: true }, o || {}));
  }

  function apply(s, o) {
    o = o || {};
    const prev = st.c, was = st.s[0], before = st.s[st.s.length - 1];
    st = { c: byId(s.c) ? s.c : 'overview', s: (s.s || []).filter(id => byId(id) || id[0] === '?'), f: s.f || null };
    // a new view, or a cold link, starts at its top; Back to the same view leaves it be
    showView(st.c, !o.quiet && (prev !== st.c || (o.scroll && !o.kept)));
    renderInspector(!!o.focus, was, o.quiet);
    // a card brought to the inspector's top by your act (a click, a key, Back, a cold link), never by a redraw: rb
    // open's live module counts it as a card looked at
    const top = st.s[st.s.length - 1];
    if (!o.quiet && top && top[0] !== '?' && top !== before && isCard(byId(top))) document.dispatchEvent(new CustomEvent('rb:card', { detail: { id: top } }));
    if (o.quiet) { markSelected(); return; }
    // land once the other views are hidden ('ready'); while the start runs every view still stacks in the centre
    const land = () => {
      if (st.f) reveal(st.f);
      else if (atDecide) { const d = decidePart(); if (d) d.scrollIntoView({ block: 'start' }); }
      atDecide = false;
      markSelected();
      // a cold link or Back to another view: the open card's row comes into view
      if (o.scroll && !o.kept && st.s.length) { const r = $$('main .sel').find(x => x.offsetParent !== null); if (r) r.scrollIntoView({ block: 'center' }); }
    };
    if (H.classList.contains('ready')) land(); else requestAnimationFrame(land);
  }

  // a part: in the centre its folds open and it comes into view; inside a card or a rail shown as the inspector's
  // root, the inspector's copy of it does
  function reveal(id) {
    const t = byId(id);
    if (!t) return;
    const inRail = t.closest('[data-rail]') && WIDE.matches;
    if (!t.closest('.card') && !inRail) { openFolds(t); t.scrollIntoView({ block: 'start' }); flash(t); return; }
    const k = $('[data-id="' + esc(id) + '"]', ibody);
    if (k) { openFolds(k); k.scrollIntoView({ block: 'start' }); flash(k); }
  }

  function showView(id, top) {
    const v = byId(id) || byId('overview');
    $$('main .view.on').forEach(x => x.classList.remove('on'));
    v.classList.add('on');
    const sec = v.dataset.nav || id;
    $$('#nav a').forEach(a => {
      const h = a.getAttribute('href').slice(1);
      if (h === id) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
      a.classList.toggle('in', h === sec && h !== id);
    });
    // a view the top bar names (About) is marked there as the nav marks its own
    $$('header.top a[href^="#"]').filter(a => !a.closest('.where')).forEach(a => { if (a.getAttribute('href').slice(1) === id) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    // the top bar names the view after the record's title
    const vc = $('#top .vcrumb'); if (vc) vc.textContent = v.dataset.crumb || '';
    // away from the place rb open started on, live, the title names the place shown first (the scope bar says it)
    const bar = $('#scope .bar');
    document.title = (bar && bar.dataset.title || '') + (v.dataset.title || document.title);
    if (top) main.scrollTop = 0;
  }

  // the view's own action: its rail, else the element its data-root names
  function rootSource(c) {
    const v = byId(c) || byId('overview');
    const own = $(':scope [data-rail]', v);
    if (own) return own;
    const r = byId(v.dataset.root);
    // another view's rail (the Overview's review list) is that view's: beside this one it would be stale
    if (r && r.matches('[data-rail]') && r.closest('.view') !== v) return null;
    return r;
  }

  // `was`: the card the trail started from before this render, whose row takes focus back when nothing else does
  function renderInspector(focus, was, quiet) {
    const keep = aside.scrollTop;
    const s = st.s;
    // the opener keeps its place on screen as the inspector unfolds from the rail
    const anchor = opener && main.contains(opener) && opener.offsetParent !== null ? opener.getBoundingClientRect().top : null;
    ibody.textContent = '';
    let node;
    if (!s.length) node = rootFor(st.c);
    else {
      const top = s[s.length - 1];
      const el = top[0] === '?' ? null : byId(top);
      node = !el ? missing(top.slice(1)) : (isCard(el) ? cloneCard(el) : partCard(el));
      node.classList.add('enter');
    }
    ibody.appendChild(node);
    // nothing picked and nothing of the view's own to show: the inspector folds to a slim rail beside the work, which
    // takes the width (its sentence stays for a screen reader)
    H.classList.toggle('irail', !s.length && node.dataset.empty === '1');
    if (anchor !== null && opener.offsetParent !== null) main.scrollTop += opener.getBoundingClientRect().top - anchor;
    // the root's own copy button (an experiment's Copy recipe) sits in the inspector's bar
    const bact = $('.bact', aside);
    bact.textContent = '';
    const slot = !s.length && $('.rhd [data-copybtn]', node);
    if (slot) bact.appendChild(slot);
    drawCrumbs(node);
    // a redraw keeps where the inspector was scrolled; anything else starts the card at its top
    aside.scrollTop = quiet ? keep : 0;
    const sheet = !WIDE.matches && s.length > 0;
    H.classList.toggle('sheet', sheet);
    // a sheet is modal: what lies under it takes no focus and no clicks while it is open (a dialog over it stays
    // usable)
    Array.from(document.body.children).forEach(el => { if (el !== aside && el !== live && !el.matches('[role="dialog"]')) el.inert = sheet; });
    aside.setAttribute('role', sheet ? 'dialog' : 'complementary');
    if (sheet) aside.setAttribute('aria-modal', 'true'); else aside.removeAttribute('aria-modal');
    if (quiet) return;
    if ((focus || sheet) && s.length) {
      focusTop();
      const top = s[s.length - 1];
      announce(top[0] !== '?' && byId(top) ? crumbOf(top) + ' opened in the inspector' : '');
    } else if (!s.length && focus) {
      // back at the root: focus returns to what opened the card while it is on screen, else to the row of the card just
      // closed (after the browser's Back and Forward the opener is gone), else the view's heading takes it
      // (a queue's button names its card by its link: a live reload drew it again, so the opener is a copy now)
      const row = was && $$('main [data-for="' + esc(was) + '"] > summary, main [data-for="' + esc(was) + '"], main [data-go="' + esc(was) + '"] a[href], main a[data-decide="' + esc(was) + '"], main .queue a[href="#' + esc(was) + '"]')
        .find(x => !x.closest('.card') && x.offsetParent !== null && x.matches('summary, a[href], button, [tabindex]'));
      if (opener && document.contains(opener) && opener.offsetParent !== null) rooted = opener;
      else if (row) rooted = row;
      else { rooted = $('h1', byId(st.c)); if (rooted) rooted.tabIndex = -1; }
      if (rooted) rooted.focus({ preventScroll: true });
      opener = null;
    }
  }

  // what takes focus in a card just opened: its title, or, opened at its Decide level (a queue's button), the first of
  // its actions, so a key or a click chooses one next
  function focusTarget() {
    if (!st.s.length) return null;
    const d = decidePart();
    if (d) {
      const b = $('.seg button[aria-pressed="true"]', d) || $('.seg button', d) || $('button:not(:disabled), a[href], input', d);
      if (b) return b;
    }
    return $('.ibody .tt') || $('.ibody h2');
  }

  // the inspector's card's Decide part, when it was opened at that level
  function decidePart() {
    // a reader is not shown a Decide part: the card opens from its top
    if (VIEWER === 'reader') return null;
    const named = st.f && $('[data-id="' + esc(st.f) + '"]', ibody);
    if (named && named.matches(DECIDE)) return named;
    return atDecide ? $('.ibody > .card ' + DECIDE.split(', ').join(', .ibody > .card ')) : null;
  }

  function focusTop() {
    const h = focusTarget();
    if (!h) return;
    if (!h.matches('a[href], button, input')) h.tabIndex = -1;
    h.focus({ preventScroll: true });
    // a sheet sliding in is still hidden on its first frame, and a hidden element takes no focus: once it shows,
    // unless focus went elsewhere meanwhile (a dialog over it)
    if (document.activeElement !== h) {
      const again = () => { const ae = document.activeElement; if (document.contains(h) && ae !== h && (!ae || ae === document.body || aside.contains(ae))) h.focus({ preventScroll: true }); };
      aside.addEventListener('transitionend', again, { once: true });
      setTimeout(again, 250);
    }
  }

  function rootFor(c) {
    const src = rootSource(c);
    const wrap = document.createElement('div');
    wrap.className = 'rootc';
    if (!src || src.classList.contains('inone')) wrap.dataset.empty = '1';
    if (src) {
      const k = src.cloneNode(true);
      stripIds(k); k.removeAttribute('data-rail'); k.hidden = false;
      $$('details.rc', k).forEach(d => { d.open = true; });
      const v = byId(c);
      wrap.dataset.rootname = src.dataset.rootname || (src.classList.contains('card') ? (v.dataset.crumb + ' · ' + (src.dataset.crumb || '')) : '');
      wrap.appendChild(k);
      enhanceAll(wrap);
    } else {
      // nothing of this view's own to show: rb's line for an inspector with nothing picked
      const said = byId('insp-empty');
      const p = said ? said.cloneNode(true) : document.createElement('p');
      if (said) { stripIds(p); p.hidden = false; } else { p.className = 'iempty dim'; p.textContent = 'Pick a question, a rule or a run to see it here.'; }
      wrap.appendChild(p);
    }
    return wrap;
  }

  // a card in the inspector is a copy of the document's: its ids become data-id, its receipts open
  function cloneCard(src) {
    const k = src.cloneNode(true);
    stripIds(k);
    $$('details.rc', k).forEach(d => { d.open = true; });
    enhanceAll(k);
    return k;
  }

  // another view's fold as a card: its summary the title, its content open
  function partCard(el) {
    const d = document.createElement('div');
    d.className = 'card partc';
    d.dataset.id = el.id;
    const h = document.createElement('h2');
    h.className = 'tt'; h.textContent = crumbOf(el.id);
    d.appendChild(h);
    const f = foldOf(el);
    (f ? Array.from(f.children).filter(x => x.tagName !== 'SUMMARY') : [el]).forEach(x => { const k = x.cloneNode(true); stripIds(k); d.appendChild(k); });
    $$('details', d).forEach(x => { x.open = true; });
    enhanceAll(d);
    return d;
  }

  // what an address that names nothing opens: rb's words, never a silent fall back
  function missing(id) {
    const d = document.createElement('div');
    d.className = 'card missing';
    d.innerHTML = '<p class="tt dim"></p>';
    d.firstChild.textContent = "nothing here is called '" + id + "'";
    return d;
  }

  function stripIds(root) {
    if (root.id) { root.dataset.id = root.id; root.removeAttribute('id'); }
    $$('[id]', root).forEach(e => { e.dataset.id = e.id; e.removeAttribute('id'); });
    $$('[aria-labelledby]', root).forEach(e => e.removeAttribute('aria-labelledby'));
  }

  // the path taken, in each card's crumb; past four, the first, then the last two
  function drawCrumbs(node) {
    const s = st.s;
    crumbs.textContent = '';
    backBtn.hidden = !s.length; closeBtn.hidden = !s.length; linkBtn.hidden = !s.length;
    if (!s.length) {
      const r = document.createElement('span');
      r.className = 'rootname';
      r.textContent = node.dataset.rootname || '';
      crumbs.appendChild(r);
      return;
    }
    const origin = byId(st.c);
    const items = [{ label: (origin && origin.dataset.crumb) || st.c, root: true }]
      .concat(s.map((id, i) => ({ label: crumbOf(id), id, i, last: i === s.length - 1 })));
    const shown = items.length > 4 ? [items[0], { label: '…', gap: true }].concat(items.slice(-2)) : items;
    shown.forEach((it, i) => {
      if (i) { const sep = document.createElement('span'); sep.className = 'sep'; sep.textContent = '›'; sep.setAttribute('aria-hidden', 'true'); crumbs.appendChild(sep); }
      let el;
      if (it.last) { el = document.createElement('span'); el.setAttribute('aria-current', 'location'); }
      else if (it.gap) { el = document.createElement('span'); }
      else { el = document.createElement('a'); el.href = '#' + (it.id || st.c); el.dataset.crumb = it.root ? 'root' : String(it.i); }
      el.textContent = it.label; el.title = it.label;
      crumbs.appendChild(el);
    });
  }

  // the centre's row of the card on top (or of the card the root shows) is marked selected
  function markSelected() {
    $$('main .sel').forEach(x => x.classList.remove('sel'));
    let top = st.s[st.s.length - 1];
    if (!top) { const r = rootSource(st.c); if (r && r.classList.contains('card')) top = r.id; }
    if (!top) return;
    $$('main [data-for="' + esc(top) + '"], main [data-go="' + esc(top) + '"]').forEach(x => { if (!x.closest('.card')) x.classList.add('sel'); });
  }

  function openFolds(t) { for (let n = t.parentElement; n; n = n.parentElement) if (n.tagName === 'DETAILS') n.open = true; if (t.tagName === 'DETAILS') t.open = true; }
  function flash(t) { t.classList.remove('flash'); void t.offsetWidth; t.classList.add('flash'); }
  // said aloud: under the open Rerun panel the page is inert, so the panel's own region says it
  function announce(msg) {
    const at = rerunDlg && rerunDlg.open ? $('.sr[aria-live]', rerunDlg) : live;
    if (!at || !msg) return;
    at.textContent = ''; setTimeout(() => { at.textContent = msg; }, 30);
  }

  // ---------- clicks: a link in the centre starts a trail, a link in the inspector extends it
  // a term shown as the press began (the press can move focus to a link inside it)
  let pressedShown = null;
  document.addEventListener('pointerdown', e => { const t = e.target.closest && e.target.closest('.term[data-hint]'); pressedShown = t && t.dataset.shown ? t : null; }, true);
  // a term's meaning closed, its hover hint too while hovered
  function hush(term) {
    term.blur();
    if (!term.matches(':hover')) return;
    term.dataset.hush = '1';
    term.addEventListener('pointerleave', () => { delete term.dataset.hush; }, { once: true });
  }
  function onClick(e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const t = e.target;
    // a term shows its meaning in place on a click or tap and follows nothing; a second click, Esc or a click elsewhere
    // closes it
    const term = t.closest('.term[data-hint]');
    if (term) {
      e.preventDefault();
      if (!term.matches('a[href], [tabindex]')) term.tabIndex = 0;
      if (term.dataset.shown || pressedShown === term) { pressedShown = null; hush(term); return; }
      term.dataset.shown = '1'; delete term.dataset.hush;
      term.addEventListener('blur', () => { delete term.dataset.shown; }, { once: true });
      term.focus({ preventScroll: true });
      return;
    }
    const btn = t.closest('button');
    if (btn) { onButton(btn, e); return; }
    if (t.closest('input, label')) return;
    const a = t.closest('a[href^="#"]');
    if (a && rerunDlg && a.getAttribute('href') === '#rerun') { e.preventDefault(); openRerun(a); return; }
    // a click outside the panel closes it: one pressed there too, not a selection dragged out of the prompt
    if (rerunDlg && rerunDlg.open && t === rerunDlg && rerunDownOut && !inBox(rerunDlg, e)) { rerunDlg.close(); return; }
    if (a) {
      e.preventDefault();
      if (a.dataset.place !== undefined && window.rbLive) { choosePlace(a); return; }
      if (a.dataset.crumb === 'root' && a.closest('.crumbs')) { go({ c: st.c, s: [], f: null }); return; }
      if (a.dataset.crumb !== undefined && a.closest('.crumbs')) { go({ c: st.c, s: st.s.slice(0, Number(a.dataset.crumb) + 1), f: null }); return; }
      navigate(decide(a) || decode(a.getAttribute('href').slice(1)), a);
      return;
    }
    // a folded row's summary opens its card; the row stays folded
    const sum = t.closest('details.nd > summary');
    if (sum && sum.closest('main')) {
      e.preventDefault();
      // the row's own card, or the card it names, wherever rb drew it
      const d = sum.parentElement, named = d.dataset.for && byId(d.dataset.for);
      const card = $(':scope > .card', d) || (isCard(named) ? named : null);
      if (card) navigate(card.id, sum);
      return;
    }
    // a row's own disclosure opens in place; the rest of the row follows the row's link
    const row = t.closest('[data-go]');
    if (row && !t.closest('summary, details.evm, details.ipm')) { e.preventDefault(); navigate(row.dataset.go, row); }
  }

  // a queue's button (`data-decide`, or a row of a queue) opens its card at the Decide level: the address names the
  // card's Decide part where it has an id; where it has none, the card, and the inspector lands on its Decide part all
  // the same
  let atDecide = false;
  function decide(a) {
    if (a.dataset.decide === undefined && !a.closest('[data-queue], .queue')) return null;
    const id = decode(a.getAttribute('href').slice(1)), el = byId(id);
    const card = el && (el.classList.contains('card') ? el : el.closest('.card'));
    if (!card) return null;
    const d = el !== card && el.matches(DECIDE) ? el : $(DECIDE, card);
    if (!d) return null;
    if (d.id) return d.id;
    atDecide = true;
    return card.id;
  }
  const DECIDE = '[data-level="decide"], .decide';

  function navigate(id, from) {
    const inInsp = !!(from && from.closest('aside'));
    const s = resolve(id, inInsp, false);
    // a nav item changes the view and clears the trail
    if (from && from.closest('#nav')) { s.s = []; s.f = null; }
    // what Esc and close return focus to: the centre's link that opened a card; a view link leaves the heading to take it
    if (!inInsp) opener = s.s.length ? from : null;
    go(s);
  }

  function onButton(b, e) {
    if (b.matches('[data-theme]')) { e.preventDefault(); cycleTheme(b); return; }
    if (b.matches('[data-keys]')) { e.preventDefault(); toggleHelp(); return; }
    const act = b.dataset.act;
    if (act === 'back') { e.preventDefault(); back(); }
    else if (act === 'close') { e.preventDefault(); go({ c: st.c, s: [], f: null }); }
    else if (act === 'link') { e.preventDefault(); copyText(location.href.split('#')[0] + '#' + hashOf(st) + tail, b); }
    else if (act === 'copy') {
      e.preventDefault();
      if (b.disabled || noCmdAt(b)) return;
      // its box, or the box its row sits under (the composer's)
      const row = b.closest('.cmprow'), box = b.closest('.cmdbox') || (row && row.previousElementSibling);
      const src = b.dataset.from ? findCopy(b, b.dataset.from) : (box && $('.cmd', box));
      if (src) copyText(src.textContent.replace(/\s+$/, '') + '\n', b, src);
    }
    else if (act === 'rerun-close') { e.preventDefault(); if (rerunDlg && rerunDlg.open) rerunDlg.close(); }
    else if (act === 'pick') { e.preventDefault(); choose(b.closest('.decide'), b.dataset.action); }
    else if (act === 'chip') { e.preventDefault(); b.setAttribute('aria-pressed', String(b.getAttribute('aria-pressed') !== 'true')); filter(b.closest('.view')); }
  }

  // one card back: the browser's Back when the entry before is this trail one card shorter (it records the trail it
  // came from, `p`), else a new entry with it, so Back after a crumb or a cut never goes deeper
  function back() {
    if (!st.s.length) return;
    const up = st.s.slice(0, -1), h = history.state;
    const same = (a, b) => a.map(x => x.replace(/^\?/, '')).join('\n') === b.map(x => x.replace(/^\?/, '')).join('\n');
    if (h && h.c === st.c && Array.isArray(h.p) && same(h.p, up)) history.back();
    else go({ c: st.c, s: up, f: null });
  }

  // copy targets inside the inspector's copies lost their ids: look them up by data-id there, then in the document
  function findCopy(el, id) {
    const scope = el.closest('.card, .rootc') || ibody;
    return $('[data-id="' + esc(id) + '"]', scope) || $('[data-id="' + esc(id) + '"]', ibody) || byId(id);
  }

  // the clipboard first, then the browser's copy command; when neither works the button says so and the text stays
  // selectable
  function copyText(text, btn, src) {
    const done = ok => {
      if (btn) {
        const o = btn.dataset.label || btn.textContent;
        btn.dataset.label = o;
        btn.textContent = ok ? 'Copied' : 'Select the text and copy it';
        clearTimeout(btn.rbSaid);
        btn.rbSaid = setTimeout(() => { btn.textContent = o; }, 2000);
      }
      // where nothing could be copied, the text it would have copied is selected, ready for the keys
      if (!ok && src) { try { const sel = getSelection(); sel.removeAllRanges(); const r = document.createRange(); r.selectNodeContents(src); sel.addRange(r); } catch (_) {} }
      announce(ok ? 'Copied' : 'Select the text and copy it');
    };
    const fallback = () => {
      const ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
      // under an open dialog the rest of the page is inert: the field goes in the dialog, where a copy still works
      ((btn && btn.closest('dialog[open]')) || document.body).appendChild(ta); ta.select();
      let ok = false; try { ok = document.execCommand('copy'); } catch (_) {}
      ta.remove();
      // the field took focus to copy: it goes back to the button
      if (btn && document.contains(btn)) btn.focus({ preventScroll: true });
      done(ok);
    };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(() => done(true), fallback);
    else fallback();
  }

  // ---------- the parts the script adds: copy buttons, the call composer, hints, filters
  // A command is copyable only when it is whole: never a line still holding a placeholder ('‹why›'), never a call's
  // listed lines (the composer copies the one chosen, with a reason), and in what waits for a confirmation only its
  // 'rb confirm' line.
  const CONFIRM = /^(cd \S.* && )?rb confirm( |$)/;
  // a past version offers no command to copy: a call copied there would be recorded on today's readings
  const past = () => !!$('#withheld .withheld');
  // no command is offered there, nor to a reader: a reader reads the record, and a call is made by its owner with rb
  const noCmd = () => VIEWER === 'reader' || past();
  // an rb command, as a box, a line or a chip holds it (`rb clone`, which a reader may run, is not one)
  const RBCMD = /^(cd \S.* && )?rb (?!clone\b)[a-z]/;
  // the rerun panel's parts are for owner and reader alike: only a past version withholds them. Its parts by
  // what they are (the class rr alone is a run's row too): the openers, the panel, the Overview's place, an
  // experiment's prompt
  const RERUN_IN = '#rerun, .rrx, [data-rerun]';
  const RERUN_PARTS = 'a[href="#rerun"][data-rerun], #rerun, .rrx, #reproduce.rrun, [data-rerun]';
  const noCmdAt = el => (el && el.closest(RERUN_IN) ? past() : noCmd());
  function copyable(box) {
    if (noCmdAt(box)) return false;
    if (box.classList.contains('nocp') || box.closest('.decide .acts')) return false;
    const cmd = $('.cmd', box);
    const text = cmd ? cmd.textContent.trim() : '';
    if (!text || text.includes('‹')) return false;
    if (box.closest('.waiting, [data-waiting]') && !CONFIRM.test(text)) return false;
    return true;
  }

  // rb open's live view says when the record last changed relative to now (R's <time data-changed>), its exact time
  // kept as the hint; a page and a past version keep the exact time
  function ago(ms) {
    const m = Math.floor(ms / 60000), h = Math.floor(m / 60), d = Math.floor(h / 24);
    const n = (k, one) => k + ' ' + one + (k === 1 ? '' : 's') + ' ago';
    if (m < 1) return 'just now';
    if (m < 60) return n(m, 'minute');
    if (h < 24) return n(h, 'hour');
    return d <= 30 ? n(d, 'day') : null;
  }
  function sayChanged(root) {
    if (!byId('rb-live') || past()) return;
    $$('time[data-changed][datetime]', root).forEach(t => {
      if (!t.dataset.exact) t.dataset.exact = t.textContent;
      const at = Date.parse(t.getAttribute('datetime')), said = isNaN(at) ? null : ago(Math.max(0, Date.now() - at));
      t.textContent = said || t.dataset.exact;
      t.title = t.dataset.exact;
    });
  }
  function enhanceAll(root) {
    sayChanged(root);
    $$('.term[aria-describedby]', root).forEach(t => {
      if (!t.dataset.hint) { const d = byId(t.getAttribute('aria-describedby')); if (d) t.dataset.hint = t.textContent.trim() + ' · ' + d.textContent.trim(); }
      if (t.tagName !== 'A') t.tabIndex = 0;
    });
    // a command box: its own Copy (rb draws it '.cmdbox[data-copybtn]', the prototype '.cmdbox')
    $$('.cmdbox', root).forEach(b => {
      if ($(':scope > .cp', b) || $(':scope > [data-copybtn]', b) || !copyable(b)) return;
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'cp'; btn.dataset.act = 'copy'; btn.textContent = 'Copy';
      b.appendChild(btn);
    });
    // a copy slot that names what it copies (an experiment's Copy recipe)
    $$('[data-copybtn]:not(.cmdbox)', root).forEach(slot => {
      if (!slot.dataset.copybtn || $(':scope > .cp', slot) || noCmdAt(slot)) return;
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'cp big2'; btn.dataset.act = 'copy'; btn.dataset.from = slot.dataset.copybtn;
      btn.textContent = slot.dataset.label || 'Copy';
      slot.appendChild(btn);
    });
    // the composer's line: its Copy on a row of its own under it, so the button (and its Copied) never covers the line,
    // which scrolls in its box
    $$('.composer .cmdbox > .cp', root).forEach(btn => {
      const box = btn.parentElement, row = document.createElement('p');
      row.className = 'cprow cmprow'; btn.classList.add('big2');
      box.after(row); row.appendChild(btn);
    });
    // a past version offers no command: no key says one copies
    if (noCmd()) $$('.keys', root).forEach(k => { k.hidden = true; });
    $$('.cmdbox, pre, code', root).forEach(c => { const t = ($('.cmd', c) || c).textContent.trim(); if (RBCMD.test(t)) (c.closest('.cmdbox') || c).classList.add('rbcmd'); });
    $$('.fchip', root).forEach(b => { b.dataset.act = 'chip'; });
    $$('.decide', root).forEach(box => {
      const ul = $('.acts', box); if (ul) ul.classList.add('enh');
      // a choice shows its key only where a key makes it: a, r and i; a line of work's calls (park, stop, ...) have none
      $$('.seg button kbd', box).forEach(k => { if (!KEYOF[(k.closest('button').dataset.action || '')]) k.remove(); });
    });
  }

  const KEYOF = { accept: 'a', reject: 'r', investigate: 'i' };

  // a choice in the composer: the line rb will be given, with your reason single-quoted, and what its review will read
  function choose(box, act) {
    // a past version composes no command: a call copied there would be recorded on today's readings
    if (!box || !act || noCmd()) return;
    box.dataset.chosen = act;
    $$('.seg button', box).forEach(x => x.setAttribute('aria-pressed', String(x.dataset.action === act)));
    const comp = $('.composer', box); if (comp) comp.hidden = false;
    compose(box);
    const inp = $('input.why', box); if (inp) inp.focus({ preventScroll: true });
  }

  function compose(box) {
    const act = box.dataset.chosen;
    const li = act && $('.acts > li[data-action="' + esc(act) + '"]', box);
    if (!li || noCmd()) return;
    const tpl = $('.cmd', li).textContent;
    const inp = $('input.why', box);
    const why = inp ? inp.value.trim() : '';
    const q = "'" + why.replace(/'/g, "'\\''") + "'";
    const out = $('.composer .cmd', box), cp = $('.composer .cp', box), cons = $('.composer .willread span', box);
    // the replacements are functions: a '$' typed in a reason is text, never a pattern of String.replace
    if (out) out.textContent = tpl.replace(/--why (?:'‹why›'|"‹why›"|‹why›|"\.\.\.")/, () => '--why ' + (why ? q : "'‹your reason›'"));
    // nothing to copy until there is an action and a reason
    if (cp) cp.disabled = !why;
    if (cons) cons.textContent = (li.dataset.cons || '').replace('‹why›', () => why || '‹why›');
  }

  function onInput(e) {
    const t = e.target;
    if (t.matches('input.why')) { const box = t.closest('.decide'); if (box) compose(box); return; }
    if (t.matches('.filterbar input')) filter(t.closest('.view'));
  }

  // words, the experiment chips and the retracted toggle; rows only show or hide, and no count is computed here
  function filter(view) {
    if (!view) return;
    const inp = $('.filterbar input', view);
    const q = inp ? inp.value.trim().toLowerCase() : '';
    const words = q.split(/\s+/).filter(Boolean);
    const xs = $$('.fchip[data-fx][aria-pressed="true"]', view).map(b => b.dataset.fx);
    const ret = !!$('.fchip[data-fret][aria-pressed="true"]', view);
    $$('[data-row]', view).forEach(r => {
      const s = (r.querySelector('summary') || r).textContent.toLowerCase();
      const okX = !xs.length || !r.dataset.x || xs.includes(r.dataset.x);
      r.hidden = !(words.every(w => s.includes(w)) && okX && (!ret || r.hasAttribute('data-ret')));
    });
    $$('[data-group]', view).forEach(g => { g.hidden = !$$('[data-row]', g).some(r => !r.hidden); });
  }

  // ---------- keys: Esc closes the inspector to its root, or leaves a text field; '?' lists the rest
  function onKey(e) {
    if (rerunDlg && rerunDlg.open) return;
    if (e.target.closest('input, textarea, select, [contenteditable="true"]')) {
      // Esc in the keys list closes it; in a field it leaves the field
      if (helpBox && helpBox.contains(e.target) && (e.key === 'Escape' || e.key === '?')) { e.preventDefault(); closeHelp(); return; }
      if (e.key === 'Escape') e.target.blur();
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'Escape') {
      if (helpBox && !helpBox.hidden) { closeHelp(); return; }
      // Esc closes a term's meaning, not the inspector
      if (e.target.closest && e.target.closest('.term[data-hint]')) { e.preventDefault(); hush(e.target.closest('.term')); return; }
      if (st.s.length) { e.preventDefault(); go({ c: st.c, s: [], f: null }); }
      return;
    }
    if (e.key === '?') { toggleHelp(); return; }
    // single keys act while they are on, and not while the keys list is open
    if (!KEYS || (helpBox && !helpBox.hidden)) return;
    const k = e.key;
    // [ and ] walk the browser's history of the inspector, so ] replays what [ undid; ‹ is one card back
    if (k === '[') { e.preventDefault(); if (st.s.length && history.state && Array.isArray(history.state.p)) history.back(); else back(); }
    else if (k === ']') { history.forward(); }
    else if (k === '/') { const f = $('main .view.on .filterbar input'); if (f) { e.preventDefault(); f.focus(); } }
    else if (k === 'j' || k === 'k') { e.preventDefault(); if (!moveQueue(k === 'j' ? 1 : -1)) moveRow(k === 'j' ? 1 : -1); }
    else if (k === 'a' || k === 'r' || k === 'i') {
      // only a call that shows: under 960 the root is drawn in the centre, and the inspector shows only a card opened
      const box = (WIDE.matches || st.s.length) && $('.ibody .decide');
      const act = Object.keys(KEYOF).find(x => KEYOF[x] === k);
      if (box && $('.seg button[data-action="' + act + '"]', box)) { e.preventDefault(); choose(box, act); }
    }
    else if (k === 'c') {
      // the card's primary: its Copy recipe, else the composed call line once it is live, else its first command
      // (only from an inspector that shows: under 960 with nothing open, the centre holds the same commands)
      const b = (WIDE.matches || st.s.length) && ($('.ibar .bact .cp') || $('.ibody .cprow:not(.cmprow) .cp') || $('.ibody .composer .cp:not(:disabled)') || $('.ibody .cmdbox .cp'));
      if (b) b.click();
    }
  }

  // on a view that holds a queue (the Overview's "Needs your judgment", Your call's full list), j and k move focus
  // through its items' buttons and open nothing; Enter opens the one focused, as a click does
  function moveQueue(d) {
    const v = byId(st.c);
    const q = v && $$('[data-queue], ul.queue, ol.queue', v).find(x => x.offsetParent !== null);
    if (!q) return false;
    // a sheet over the work holds the keys' focus: what lies under it is out of reach until it closes
    if (H.classList.contains('sheet')) return true;
    // each item's button, else (a reader's) its first link that shows
    const shows = x => x.offsetParent !== null;
    const acts = $$(':scope > li', q).map(li => $$('a[data-decide], a.qbtn, a.qact', li).find(shows) || $$('a[href^="#"], button', li).find(shows)).filter(Boolean);
    if (!acts.length) return true;
    const ae = document.activeElement;
    let i = acts.findIndex(x => x === ae || (ae && x.closest('li').contains(ae)));
    i = i < 0 ? (d > 0 ? 0 : acts.length - 1) : Math.max(0, Math.min(acts.length - 1, i + d));
    acts[i].focus({ preventScroll: true });
    acts[i].closest('li').scrollIntoView({ block: 'nearest' });
    return true;
  }

  // j and k move through the working object's rows, each opened in the inspector
  function moveRow(d) {
    const v = byId(st.c);
    if (!v) return;
    const rows = $$('[data-for], [data-go]', v).filter(r => !r.closest('.card') && !r.closest('[hidden]') && r.offsetParent !== null);
    const of = r => r.dataset.for || r.dataset.go;
    const ids = rows.map(of).filter((id, i, a) => a.indexOf(id) === i && isCard(byId(id)));
    if (!ids.length) return;
    let top = st.s[0];
    if (!top) { const r = rootSource(st.c); if (r && r.classList.contains('card')) top = r.id; }
    let i = ids.indexOf(top);
    i = i < 0 ? (d > 0 ? 0 : ids.length - 1) : Math.max(0, Math.min(ids.length - 1, i + d));
    const row = rows.find(r => of(r) === ids[i]);
    opener = row ? ($('summary', row) || $('a', row) || row) : null;
    // a call (Your call's list) opens at its Decide level, as the queue's buttons open it
    if ($(DECIDE, byId(ids[i]))) atDecide = true;
    go({ c: st.c, s: [ids[i]], f: null }, { focus: false });
    if (row) row.scrollIntoView({ block: 'nearest' });
  }

  function onNavKey(e) {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
    const links = $$('#nav a').filter(a => a.offsetParent !== null);
    const i = links.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const n = e.key === 'Home' ? 0 : e.key === 'End' ? links.length - 1 : (i + (e.key === 'ArrowDown' ? 1 : -1) + links.length) % links.length;
    links[n].focus();
  }

  // the theme: the system's, then light, then dark; kept in this browser
  function cycleTheme() {
    const cur = H.dataset.theme || 'system';
    const next = cur === 'system' ? 'light' : cur === 'light' ? 'dark' : 'system';
    if (next === 'system') delete H.dataset.theme; else H.dataset.theme = next;
    store('rb-theme', next === 'system' ? null : next);
    sayTheme();
  }

  function sayTheme() {
    const b = $('button[data-theme]');
    if (!b) return;
    const t = H.dataset.theme || 'system';
    b.setAttribute('aria-label', 'Theme: ' + t);
    b.title = 'Theme: ' + t;
    // the button's icon (half a circle, a sun, a moon) is chosen by its state
    b.dataset.state = t;
    const lt = $('.lt', b); if (lt) lt.textContent = t;
  }

  function toggleHelp() {
    if (!helpBox) {
      helpBox = document.createElement('div');
      helpBox.className = 'help'; helpBox.setAttribute('role', 'dialog'); helpBox.setAttribute('aria-label', 'Keyboard');
      helpBox.innerHTML = (byId('words') ? '<p class="hw"><a href="#words">What the words on this page mean →</a></p>' : '') + '<b>Keyboard</b><dl>' +
        '<dt><kbd>[</kbd> <kbd>]</kbd></dt><dd>back and forward through the inspector</dd><dt><kbd>Esc</kbd></dt><dd>close the inspector; focus goes back to where it was</dd>' +
        // a reader's keys only read: no call to make and nothing to copy
        (VIEWER === 'reader'
          ? '<dt><kbd>j</kbd> <kbd>k</kbd></dt><dd>next or previous row, opened in the inspector; on the Overview, the next or previous item still open, and <kbd>Enter</kbd> opens it</dd>'
          : '<dt><kbd>j</kbd> <kbd>k</kbd></dt><dd>next or previous row, opened in the inspector (a call at its Decide part); on the Overview, the next or previous item that needs your judgment, and <kbd>Enter</kbd> opens it</dd>' +
            '<dt><kbd>a</kbd> <kbd>r</kbd> <kbd>i</kbd></dt><dd>on a call: accept, reject, investigate</dd>' +
            '<dt><kbd>c</kbd></dt><dd>copy the card’s recipe or command</dd>') +
        '<dt><kbd>/</kbd></dt><dd>filter this list</dd>' +
        '<dt><kbd>↑</kbd> <kbd>↓</kbd></dt><dd>move in the navigation</dd>' +
        '<dt><kbd>?</kbd></dt><dd>this list</dd></dl>' +
        '<label><input type="checkbox" class="keys-on"' + (KEYS ? ' checked' : '') + '> Single-key shortcuts</label>';
      helpBox.addEventListener('change', ev => { if (ev.target.matches('.keys-on')) { KEYS = ev.target.checked; store('rb-keys', KEYS ? null : 'off'); } });
      helpBox.addEventListener('click', ev => { if (ev.target.closest('a')) closeHelp(); });
      // Tab goes round the list while it is open, as in any dialog
      helpBox.addEventListener('keydown', ev => {
        if (ev.key !== 'Tab') return;
        const f = $$('a[href], input, button', helpBox);
        if (!f.length) return;
        const i = f.indexOf(document.activeElement), n = ev.shiftKey ? (i <= 0 ? f.length - 1 : i - 1) : (i + 1) % f.length;
        ev.preventDefault(); f[n].focus();
      });
      document.body.appendChild(helpBox);
      helpBox.hidden = true;
      // what a later block of the script keeps in the list adds itself here
      document.dispatchEvent(new CustomEvent('rb:help', { detail: { box: helpBox, close: closeHelp } }));
    }
    if (!helpBox.hidden) { closeHelp(); return; }
    helpBox.hidden = false;
    const kb = $('button[data-keys]'); if (kb) kb.setAttribute('aria-expanded', 'true');
    // it opens on what the words mean, the question '?' is usually asked for
    const first = $('a, input', helpBox); if (first) first.focus({ preventScroll: true });
  }

  // ---------- the rerun panel: a page with a clone address (an export with --url, which the hub serves with its
  // rb-clone meta) offers a prompt to copy and paste into a coding tool. rb draws its parts (.rrun: the openers, data-rerun, the
  // panel #rerun, an experiment's prompt) with its own addresses in data-fill spans; the script puts the hub's addresses
  // there, and opens the panel over the page. Inside the hub with no rb-clone meta (the owner did not let readers rerun
  // it), and at a past version, none of it is there. The clipboard alone: nothing is sent.
  let rerunDlg = null, rerunFrom = null, rerunCold = false, rerunDownOut = false;
  // an address a meta gives: https (http on this machine alone), with nothing in it that could end the line or the prompt
  function metaAddr(name) {
    const m = document.querySelector('meta[name="' + name + '"]'), v = m && (m.getAttribute('content') || '').trim();
    // a bundle's address holds no query: the hub's are /s/<link>/bundle.zip and /demo/<slug>/bundle.zip
    return name === 'rb-clone' && v && v.includes('?') ? null : safeAddr(v);
  }
  // an address goes into a prompt someone pastes elsewhere, and from there into a shell: only the characters an
  // address needs, none a shell reads ($ ( ) ; | & { } * ! ' and the like)
  function safeAddr(v) {
    if (!v || !/^[A-Za-z0-9._~:/?#@%+=,-]+$/.test(v)) return null;
    try { const u = new URL(v); if (u.protocol === 'https:' || (u.protocol === 'http:' && /^(127\.0\.0\.1|localhost|\[::1\])$/.test(u.hostname))) return v; } catch (_) {}
    return null;
  }
  const RERUN_CSS = 'dialog.rerundlg { width: min(42rem, calc(100vw - 32px)); max-height: calc(100dvh - 48px); box-sizing: border-box; padding: 18px 20px 20px;' +
    ' background: var(--card, var(--bg)); color: var(--ink); border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow); overflow: auto; }' +
    ' dialog.rerundlg::backdrop { background: rgba(0, 0, 0, .45); }' +
    ' dialog.rerundlg .rerun-x { float: right; margin: -4px -6px 6px 12px; }' +
    ' dialog.rerundlg aside.rerun { display: block; position: static; inset: auto; width: auto; max-width: none; max-height: none; margin: 0; padding: 0; border: 0; box-shadow: none; background: none; overflow: visible; }' +
    ' dialog.rerundlg h2 { margin: 0 0 8px; font-size: 19px; }' +
    ' .rrun pre.rprompt, [data-rerun] pre.rerun-prompt { white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0 12px; padding: 10px 12px; max-width: 100%; box-sizing: border-box;' +
    ' background: var(--cmdbg, var(--surf)); border: 1px solid var(--line); border-radius: 8px; font: 12.5px/1.5 var(--mono, monospace); user-select: text; }' +
    ' .rrun .cmdbox { max-width: 100%; }' +
    // a command in the panel wraps rather than run past its box's edge at a phone's width: Copy copies it whole
    ' dialog.rerundlg .cmd { white-space: pre-wrap; overflow-wrap: anywhere; }' +
    ' .rpbox > [data-copybtn] { display: block; margin: 4px 0 0; }' +
    // a command's Copy clear of its box's border: 5 px under it reads as on it
    ' dialog.rerundlg .cmdbox > .cp { top: 7px; right: 7px; }' +
    ' @media screen { dialog.rerundlg.under { margin-top: var(--rr-top); max-height: calc(100dvh - var(--rr-top) - 16px); } }' +
    ' @media (max-width: 639px) { header.top .rrb { display: none; } dialog.rerundlg { width: calc(100vw - 16px); max-height: calc(100dvh - 16px); padding: 14px 14px 16px; } }' +
    ' @media print { .rrhd { display: none; } dialog.rerundlg { display: block; position: static; width: auto; max-height: none; box-shadow: none; border: 0; padding: 0; }' +
    ' dialog.rerundlg::backdrop { display: none; } dialog.rerundlg .rerun-x, .rrun .cp { display: none; } }';
  function rerunInit() {
    const parts = $$(RERUN_PARTS);
    if (!parts.length) return;
    const bundle = metaAddr('rb-clone'), page = metaAddr('rb-page') || (bundle ? safeAddr(location.href.split('#')[0]) : null);
    if (past() || (INHUB && !bundle)) { parts.forEach(p => p.remove()); return; }
    // the hub's addresses in place of rb's own: in the spans that hold them, else wherever rb's own (data-rb-bundle,
    // data-rb-page on what says them) stands in the text
    const swap = (kind, to) => {
      if (!to) return;
      const spans = $$('[data-fill="' + kind + '"]');
      spans.forEach(e => { e.textContent = to; });
      if (spans.length) return;
      $$('[data-rb-' + kind + ']').forEach(holder => {
        const from = holder.getAttribute('data-rb-' + kind);
        if (!from || from === to) return;
        const w = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT);
        for (let n = w.nextNode(); n; n = w.nextNode()) if (n.nodeValue.includes(from)) n.nodeValue = n.nodeValue.split(from).join(to);
      });
    };
    // the bundle first: the page's own address is often the bundle's folder, a part of it
    swap('bundle', bundle); swap('page', page);
    // a part still holding a placeholder ('‹bundle›') has no address to offer
    const known = !parts.some(e => e.textContent.includes('‹bundle›') || e.textContent.includes('‹page›'));
    parts.forEach(p => { p.hidden = !known; });
    if (!known) return;
    H.classList.add('rerun-on');
    const css = document.createElement('style'); css.id = 'rb-rerun'; css.textContent = RERUN_CSS;
    document.head.appendChild(css);
    // a hub page: rb's own opener again under the title, in the first screen at every width
    const ttl = bundle && $('#overview > h1'), own = $('a.rrb[data-rerun]');
    if (ttl && own) { const p = document.createElement('p'), l = own.cloneNode(true); p.className = 'rrhd'; l.className = 'rrb rrun'; p.append(l); ttl.after(p); }
    const panel = byId('rerun');
    // the panel stands outside the working view and the inspector (it is an aside of its own)
    if (!panel || (panel.parentElement && panel.parentElement.closest('main, aside.insp'))) return;
    rerunDlg = document.createElement('dialog');
    rerunDlg.className = 'rerundlg'; rerunDlg.setAttribute('aria-labelledby', 'h-rerun');
    const x = document.createElement('button');
    x.type = 'button'; x.className = 'ctl rerun-x'; x.dataset.act = 'rerun-close'; x.textContent = 'Close';
    const said = document.createElement('div'); said.className = 'sr'; said.setAttribute('aria-live', 'polite');
    panel.before(rerunDlg); rerunDlg.append(x, panel, said);
    rerunDlg.addEventListener('pointerdown', e => { rerunDownOut = e.target === rerunDlg && !inBox(rerunDlg, e); });
    rerunDlg.addEventListener('close', () => {
      const f = rerunFrom; rerunFrom = null;
      if (f && document.contains(f) && f.checkVisibility()) f.focus({ preventScroll: true });
    });
    // an address that names the panel opens it over the Overview, the address the Overview's
    if (decode(location.hash.slice(1)) === 'rerun') { rerunCold = true; H.classList.add('rerun-cold'); hist('replaceState', null, location.pathname + location.search + '#overview'); }
    // print: an open panel would print over the page from the top layer; closed for the print, it prints in its place
    let reopen = false;
    const toPrint = () => { if (rerunDlg.open) { reopen = true; rerunDlg.close(); } };
    const fromPrint = () => { if (reopen) { reopen = false; openRerun(null); } };
    const pm = window.matchMedia('print');
    if (pm.addEventListener) pm.addEventListener('change', e => (e.matches ? toPrint() : fromPrint()));
  }
  function openRerun(from) {
    if (!rerunDlg || rerunDlg.open) return;
    rerunFrom = from || document.activeElement;
    rerunDlg.showModal();
    placeRerun();
    rerunDlg.scrollTop = 0;
    const first = $('#rerun .cp', rerunDlg) || $('.rerun-x', rerunDlg);
    if (first) { first.focus({ preventScroll: true }); requestAnimationFrame(() => { if (rerunDlg.open && !rerunDlg.contains(document.activeElement)) first.focus({ preventScroll: true }); }); }
  }
  // beside a top bar (from 640) the panel opens under it and under the navigator's tabs when they make a row up there,
  // never over what they say (Open calls among it); under 640 it is a sheet over the whole screen, and print is print's
  function placeRerun() {
    if (!rerunDlg || !rerunDlg.open) return;
    let top = 0;
    if (innerWidth >= 640) $$('header.top, nav.nav').forEach(e => {
      if (!e.checkVisibility()) return;
      const r = e.getBoundingClientRect();
      if (r.top < 140 && r.height < 160 && r.bottom > 0) top = Math.max(top, Math.ceil(r.bottom) + 8);
    });
    rerunDlg.classList.toggle('under', top > 0);
    rerunDlg.style.setProperty('--rr-top', top + 'px');
  }
  window.addEventListener('resize', placeRerun);
  // an address naming the panel, followed or typed once the page is up: the panel opens and the address stays as it was
  function rerunHash() {
    if (!rerunDlg || decode(location.hash.slice(1)) !== 'rerun') return false;
    hist('replaceState', history.state, '#' + hashOf(st) + tail);
    openRerun(null);
    return true;
  }
  // a click inside the dialog's own box, not on its backdrop
  function inBox(el, e) {
    const r = el.getBoundingClientRect();
    return e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
  }

  function closeHelp() {
    if (!helpBox || helpBox.hidden) return;
    const back = helpBox.contains(document.activeElement);
    helpBox.hidden = true;
    const kb = $('button[data-keys]');
    if (kb) kb.setAttribute('aria-expanded', 'false');
    // focus goes back to the keys button, or into the card when a sheet holds the page under it
    if (back) { if (kb && !kb.closest('[inert]')) kb.focus({ preventScroll: true }); else focusTop(); }
  }
})();

// ---------- the pointer guide: a first visit's few steps in app mode, each a spotlight on one part of the screen and a
// short tooltip beside it. It shows and never asks. It is this block and guide.css alone: leave both out and nothing
// else changes. Ended or skipped, it never shows again in this browser (rb.guide.1); where the browser keeps nothing
// (a hosted page's sandboxed frame), it never shows by itself. Never in print, never with the script off (it is the script's), never on an address that
// names anything past the Overview (a view, a card, a place): whoever followed that link came for it. The keys list's
// "Show the guide again" replays it.
(() => {
  'use strict';
  const H = document.documentElement;
  // a reader (the hub's meta, read by the app above) is shown where the calls stand, not asked to make them
  const READER = () => H.dataset.viewer === 'reader';
  const KEY = 'rb.guide.1';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const seen = () => { try { return localStorage.getItem(KEY) !== null; } catch (_) { return shownHere; } };
  // whether this browser keeps what it is given here: where it keeps nothing (a hosted page's opaque origin, where
  // storage throws), the guide never shows by itself, only from the keys list, or it would come back on every load
  const keeps = () => { try { const k = 'rb.probe'; localStorage.setItem(k, '1'); localStorage.removeItem(k); return true; } catch (_) { return false; } };
  const keep = () => { try { localStorage.setItem(KEY, 'ended'); } catch (_) {} };
  const shown = el => { if (!el) return false; if (el.isHead || el.virtual) return true; if (el.closest('[hidden]')) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const yourCall = () => {
    const a = $('#nav a[href="#your-call"]');
    const n = a && $('.n', a);
    return n && parseInt(n.textContent, 10) > 0 && shown(a) ? a : null;
  };
  // from 960 the navigator is a column that lists the questions; under it a row of tabs, and the Overview lists them
  const WIDE = window.matchMedia('(min-width: 960px)');
  const narrow = () => !WIDE.matches;
  const view = () => $('#main .view.on');
  // under 960, the Overview's list of the questions, where it shows
  const strip = () => { const e = $('#main .view.on nav.strip.ov'); return shown(e) ? e : null; };
  // the finding step 3 opens, as a click would: one whose rule is met, else the first
  const finding = () => { const met = $$('.card[data-kind="criterion"].is-good'); return met.find(c => $('.part[id$=":runs"] table', c)) || met[0] || $('.card[data-kind="criterion"]'); };
  // the inspector shows a finding or a run: the card on its top
  const inInsp = () => $('.ibody > .card[data-kind="criterion"], .ibody > .card[data-kind="run"], .ibody > .card[data-kind="analysis"]');
  // each step: what it lights, and its words; a step whose part is not drawn is left out
  const STEPS = [
    { spot: () => (narrow() && strip()) || $('#nav'), title: 'Questions and experiments',
      text: () => !narrow() ? 'The navigator lists the questions this record asks and the experiments that test them. Pick one to read it.'
        : strip() ? 'This lists the questions the record asks, each with where it stands. Pick one to read it and the experiments that test it.'
        : 'These tabs lead to the questions, the experiments, the runs and the rest. Pick one to read it.' },
    // under 960 the working view is the whole screen: its title and summary are what is lit
    { spot: () => narrow() ? head(view()) : $('#main'), title: 'What the record says',
      text: 'The working view shows what the record says about the one picked, and what is still open.' },
    // a finding opened in the inspector, its provenance lit: beside the work from 960, a sheet over it under 960
    { spot: () => { const c = inInsp(); if (!c) return null; return narrow() ? ($('.part[data-id$=":runs"]', c) || c) : $('aside.insp'); },
      title: 'Where it came from', open: true,
      text: 'A rule or a run opens here: what rb measured, the runs it rests on, and how to run it again.' },
    // a reader is shown where the calls stand, not asked to make them
    { spot: yourCall, title: () => READER() ? 'Open calls' : 'Your call',
      text: () => READER() ? 'The calls still open in this record. They are made with rb in a terminal, not on this page.'
        : 'The calls that wait on you are here, each with the line to run in your own terminal.' },
  ];
  // the Overview as a decision surface (OVERVIEW-V2): where each question stands, what weakens the evidence, what
  // needs the user's judgment, then the inspector; a part rb does not draw (no integrity line) is left out
  const onOv = q => { const v = view(), e = v && v.id === 'overview' && $(q, v); return shown(e) ? e : null; };
  const queue = () => { const q = onOv('#queue') || onOv('[data-queue], ul.queue, ol.queue'); if (!q) return null; const s = q.closest('section'); return s && s !== view() && shown(s) ? s : q; };
  // the standing cards (one per top-level question), as one part
  const standing = () => {
    const w = onOv('#standing'); if (w) return w;
    const v = view(), all = v && v.id === 'overview' ? $$('section.stand', v).filter(shown) : [];
    if (all.length < 2) return all[0] || null;
    return { getBoundingClientRect: () => { const a = all[0].getBoundingClientRect(), b = all[all.length - 1].getBoundingClientRect(); return { left: Math.min(a.left, b.left), top: a.top, right: Math.max(a.right, b.right), bottom: b.bottom, width: Math.max(a.right, b.right) - Math.min(a.left, b.left), height: b.bottom - a.top }; },
      scrollIntoView: o => all[0].scrollIntoView(o), closest: () => null, virtual: true };
  };
  const STEPS_V2 = [
    { spot: standing, title: 'Where the record stands',
      text: 'Each question the record asks, where it stands now and why. Its rule counts open the rules behind them.' },
    { spot: () => onOv('#integrity'), title: 'How far to trust it',
      text: 'What limits the whole body of evidence, with its numbers. Read the result of every rule with it in mind.' },
    { spot: queue, title: () => READER() ? 'Still open' : 'Needs your judgment',
      text: () => READER() ? 'What rb cannot settle on its own. Each row opens its item in the inspector, to read.'
        : 'What rb cannot settle and you can. Each button opens it in the inspector, ready for your call.' },
    Object.assign({}, STEPS[2]),
  ];
  // a view's title and the summary lines under it, as one part
  function head(v) {
    const h = v && $('h1', v);
    if (!h || !shown(h)) return null;
    let last = h;
    for (let n = h.nextElementSibling; n && n.tagName === 'P' && shown(n); n = n.nextElementSibling) last = n;
    return { getBoundingClientRect: () => { const a = h.getBoundingClientRect(), b = last.getBoundingClientRect(); return { left: Math.min(a.left, b.left), top: a.top, right: Math.max(a.right, b.right), bottom: b.bottom, width: Math.max(a.right, b.right) - Math.min(a.left, b.left), height: b.bottom - a.top }; },
      scrollIntoView: o => h.scrollIntoView(o), closest: () => null, isHead: true };
  }
  let shownHere = false, steps = [], i = 0, box = null, spot = null, back = null, raf = 0, later = 0, paused = false, watch = null;
  // the card the guide opened for step 3, by its address, while it shows: the guide closes it when it moves on
  let mine = null;
  // whether this step's part was scrolled to make room for its tooltip
  let roomed = false;
  document.addEventListener('DOMContentLoaded', () => {
    // after the app has started (its own listener came first); the address names nothing past the Overview
    if (!H.classList.contains('ready') || !keeps() || seen() || window.matchMedia('print').matches) return;
    const hash = decodeURIComponent(location.hash.replace(/^#+/, ''));
    if ((hash && hash !== 'overview') || H.classList.contains('rerun-cold')) return;
    requestAnimationFrame(() => start());
  });
  // the replay, in the keys list, only where the guide can show
  document.addEventListener('rb:help', e => {
    if (!H.classList.contains('app') || !styled()) return;
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'guide-again'; b.textContent = 'Show the guide again';
    b.addEventListener('click', () => { const kb = $('button[data-keys]'); e.detail.close(); start(kb && shown(kb) ? kb : null); });
    e.detail.box.appendChild(b);
  });

  // whether guide.css is on the page: without it the guide would be a block in the page, so it stays unseen instead
  function styled() {
    if (!box) build();
    if (getComputedStyle(box).position === 'fixed') return true;
    box.remove(); spot.remove(); box = spot = null;
    return false;
  }

  function start(from) {
    steps = (standing() ? STEPS_V2 : STEPS).filter(s => s.open ? !!(inInsp() || finding()) : shown(s.spot()));
    if (!steps.length || !H.classList.contains('app') || !styled()) return;
    shownHere = true;
    keep();
    back = from || document.activeElement;
    box.hidden = false; spot.hidden = false; paused = false;
    i = 0;
    draw(true);
    window.addEventListener('resize', onResize);
    window.addEventListener('scroll', place, true);
    document.addEventListener('rb:swapped', place);
    document.addEventListener('keydown', onEsc, true);
    // a card opened as a sheet (under 960) covers the screen: the guide waits behind it, and comes back when it closes,
    // a step on when the user did what the step asked
    watch = new MutationObserver(onSheet);
    watch.observe(H, { attributes: true, attributeFilter: ['class'] });
    // and the inspector's card: one the guide opened and the user closed takes the guide a step on
    const ib = $('aside.insp .ibody');
    if (ib) watch.observe(ib, { childList: true });
    // and the working view: another view shown moves the part a step lights, or takes it away
    const mn = $('#main');
    if (mn) watch.observe(mn, { attributes: true, attributeFilter: ['class'], subtree: true });
  }

  function build() {
    spot = document.createElement('div');
    spot.className = 'guide-spot'; spot.setAttribute('aria-hidden', 'true');
    box = document.createElement('div');
    box.className = 'guide'; box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'false');
    box.setAttribute('aria-labelledby', 'guide-h'); box.setAttribute('aria-describedby', 'guide-p');
    box.innerHTML = '<h2 id="guide-h" tabindex="-1"></h2><p id="guide-p"></p><div class="gfoot"><ol class="gdots"></ol>' +
      '<button type="button" class="gskip">Skip</button><button type="button" class="gback">Back</button><button type="button" class="gnext">Next</button></div>';
    box.hidden = true; spot.hidden = true;
    box.addEventListener('click', e => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.matches('.gskip')) end();
      else if (b.matches('.gback')) { if (i > 0) { i--; draw(true); } }
      else if (b.matches('.gnext')) { if (i < steps.length - 1) { i++; draw(true); } else end(); }
    });
    document.body.appendChild(spot);
    document.body.appendChild(box);
  }

  function draw(focus) {
    const s = steps[i];
    // a card the guide opened stays only on its own step; one the user opened stays
    if (!s.open) closeMine();
    else if (!inInsp()) openFinding();
    $('#guide-h', box).textContent = typeof s.title === 'function' ? s.title() : s.title;
    $('#guide-p', box).textContent = typeof s.text === 'function' ? s.text() : s.text;
    $('.gdots', box).innerHTML = steps.map((_, n) => '<li' + (n === i ? ' aria-current="step"' : '') + '></li>').join('');
    $('.gdots', box).setAttribute('aria-label', 'Step ' + (i + 1) + ' of ' + steps.length);
    $('.gback', box).hidden = i === 0;
    $('.gnext', box).textContent = i === steps.length - 1 ? 'Done' : 'Next';
    const el = s.spot();
    roomed = false;
    if (el) el.scrollIntoView({ block: 'nearest' });
    place();
    // a sheet slides in: measure it again once it has
    clearTimeout(later); later = setTimeout(place, 300);
    if (focus) $('.gnext', box).focus({ preventScroll: true });
  }

  // step 3 opens a finding in the inspector, as a click on a link to it there would (the working view stays)
  function openFinding() {
    const c = finding(), insp = $('aside.insp .ibody');
    if (!c || !insp) return;
    const a = document.createElement('a');
    a.href = '#' + c.id; a.hidden = true;
    insp.appendChild(a);
    mine = c.id;
    a.click();
    a.remove();
  }
  // what the guide opened, closed: the browser's Back, while its address is still the card's
  function closeMine(then) {
    const was = mine;
    mine = null;
    if (!was || decodeURIComponent(location.hash.replace(/^#+/, '')).split('&')[0] !== was) { if (then) then(); return; }
    window.addEventListener('popstate', () => setTimeout(() => { if (then) then(); else if (box && !box.hidden) $('.gnext', box).focus({ preventScroll: true }); }, 0), { once: true });
    history.back();
  }

  function onSheet() {
    if (!box) return;
    const sheet = H.classList.contains('sheet');
    // the card the guide opened (step 3; a sheet under 960) is the step: the guide stays over it; closed by the user,
    // the guide goes a step on
    if (mine) {
      if (!box.hidden && !inInsp()) { mine = null; if (i < steps.length - 1) { i++; draw(true); } else end(); }
      else place();
      return;
    }
    if (sheet && !box.hidden) {
      paused = true; box.hidden = true; spot.hidden = true;
    } else if (!sheet && paused) {
      paused = false;
      box.hidden = false; spot.hidden = false;
      draw(false);
    } else place();
  }

  // the inspector slides in or out across 960: measure again once it has
  function onResize() { place(); clearTimeout(later); later = setTimeout(place, 300); }

  // the spotlight on the step's part as it shows on the screen; the tooltip whole on the screen, beside the part and
  // never over it, nor over the view's title and summary: below it or above it, else beside it, else the nearest place
  // that covers neither, else the one that covers least of them
  function place() {
    if (!box || box.hidden) return;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      if (!box || box.hidden) return;
      const s = steps[i], el = s.spot();
      if (!el) {
        // the card the guide opened was closed by the user: the guide goes a step on
        if (s.open && mine) { mine = null; if (i < steps.length - 1) { i++; draw(true); } else end(); return; }
        end(); return;
      }
      const W = document.documentElement.clientWidth, Hh = window.innerHeight, m = 8;
      const clip = r => { const l = Math.min(Math.max(r.left, 0), W), t = Math.min(Math.max(r.top, 0), Hh); return { l, t, r: Math.max(Math.min(r.right, W), l), b: Math.max(Math.min(r.bottom, Hh), t) }; };
      const at = clip(el.getBoundingClientRect());
      const keep = [at];
      const hd = !H.classList.contains('sheet') && !el.isHead && head(view());
      if (hd) keep.push(clip(hd.getBoundingClientRect()));
      const bw = box.offsetWidth, bh = box.offsetHeight;
      const x = v => Math.max(m, Math.min(v, W - bw - m)), y = v => Math.max(m, Math.min(v, Hh - bh - m));
      const over = (l, t) => keep.reduce((n, k) => n + Math.max(0, Math.min(l + bw, k.r) - Math.max(l, k.l)) * Math.max(0, Math.min(t + bh, k.b) - Math.max(t, k.t)), 0);
      const tries = [[x(at.l), at.b + m], [x(at.l), at.t - m - bh]];
      if (W >= 640) tries.push([at.r + m, y(at.t)], [at.l - m - bw, y(at.t)]);
      for (let t = m; t <= Hh - bh - m; t += 8) for (const l of [at.r + m, at.l - m - bw, x(at.l), m, W - bw - m]) tries.push([l, t]);
      const fits = ([l, t]) => l >= m - 0.5 && t >= m - 0.5 && l + bw <= W - m + 0.5 && t + bh <= Hh - m + 0.5;
      const ok = tries.filter(fits);
      // of the places that cover neither, the nearest the part; where none does, the part is scrolled to the top of
      // the screen once, which makes room beside it; else the part's top, lit down to where the tooltip sits under it;
      // else the place that covers least
      const gap = ([l, t]) => Math.hypot(Math.max(0, at.l - (l + bw), l - at.r), Math.max(0, at.t - (t + bh), t - at.b));
      const free = ok.filter(([l, t]) => over(l, t) === 0);
      let best = free.length ? free.reduce((a, b) => gap(b) < gap(a) ? b : a) : null;
      if (!best && !roomed) { roomed = true; el.scrollIntoView({ block: 'start' }); place(); return; }
      // a part taller than the screen has room for the tooltip under it: the spotlight lights what of it shows above
      if (!best && Hh - bh - 2 * m - at.t >= 48) {
        const was = at.b;
        at.b = Math.min(at.b, Hh - bh - 2 * m);
        if (over(x(at.l), at.b + m) === 0) best = [x(at.l), at.b + m]; else at.b = was;
      }
      if (!best) best = (ok.length ? ok : [[x(at.l + m), y(at.b - bh - m)]]).reduce((a, b) => over(b[0], b[1]) < over(a[0], a[1]) ? b : a);
      Object.assign(spot.style, { left: at.l + 'px', top: at.t + 'px', width: (at.r - at.l) + 'px', height: (at.b - at.t) + 'px' });
      box.style.left = best[0] + 'px'; box.style.top = best[1] + 'px';
    });
  }

  // Esc ends the guide, unless the keys list is open over it: then Esc is the list's
  function onEsc(e) {
    if (e.key !== 'Escape' || !box || box.hidden) return;
    const help = $('.help');
    if (help && !help.hidden && help.contains(e.target)) return;
    e.preventDefault(); e.stopImmediatePropagation();
    end();
  }

  function end() {
    if (!box || (box.hidden && !paused)) return;
    const had = box.contains(document.activeElement);
    box.hidden = true; spot.hidden = true; paused = false;
    window.removeEventListener('resize', onResize);
    window.removeEventListener('scroll', place, true);
    document.removeEventListener('rb:swapped', place);
    document.removeEventListener('keydown', onEsc, true);
    if (watch) { watch.disconnect(); watch = null; }
    const to = back;
    back = null;
    // focus goes back where it was before the guide took it, once the card the guide opened is closed
    closeMine(() => {
      if (had || document.activeElement === document.body || (document.activeElement && document.activeElement.closest('aside.insp, #main h1'))) {
        if (to && to.isConnected && to !== document.body && !to.closest('[inert]')) to.focus({ preventScroll: true });
        else if (document.activeElement && box.contains(document.activeElement)) document.activeElement.blur();
      }
    });
  }
})();
