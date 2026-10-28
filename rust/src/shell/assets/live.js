(() => {
  'use strict';
  // rb open's live module (BP 4a). Every 5 s it asks rb open for the key of what the page shows; when the key moves, it
  // fetches the page again and swaps in its nav and views, keeping the folds open, the inspector's cards, where each
  // part was scrolled and a reason being typed. It computes nothing. It counts an inspection when the page first shows
  // and when a card is opened (the script's rb:card), never on a poll or a swap.
  const me = document.currentScript;
  if (!me || !window.fetch || !window.DOMParser) return;
  const EVERY = 5000, MOST = 60000, LINES = ['scope', 'scope_short'];
  let key = me.dataset.key || '', query = me.dataset.query || '', wait = EVERY, timer = 0, busy = false, swapping = false, asked = 0;
  const byId = id => document.getElementById(id);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = s => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/[^\w-]/g, '\\$&'));
  const inspected = () => { fetch('/telemetry/inspected', { cache: 'no-store', headers: { 'X-RB-Usage': '1' } }).catch(() => {}); };
  const side = el => (el.closest('aside') ? 'a' : 'd');
  const foldSel = k => 'details[data-fold="' + esc(k) + '"]';

  // an element by where it sits: in the inspector or not; the nearest element at or above it with an id (a card cloned
  // into the inspector keeps its id as data-id) or a fold key; and its place there among the elements `sel` matches
  function locate(el, sel) {
    const host = el.closest('[id], [data-id], details[data-fold]');
    const by = !host ? null : host.id ? ['id', host.id] : host.dataset.id ? ['id', host.dataset.id] : ['fold', host.dataset.fold];
    return { side: side(el), by, sel, n: host === el ? -1 : $$(sel, host || document.body).indexOf(el) };
  }
  function find(at) {
    const hosts = !at.by ? [document.body]
      : at.by[0] === 'fold' ? $$(foldSel(at.by[1])) : $$('[id="' + esc(at.by[1]) + '"], [data-id="' + esc(at.by[1]) + '"]');
    for (const h of hosts) {
      const el = at.n < 0 ? h : $$(at.sel, h)[at.n];
      if (el && side(el) === at.side) return el;
    }
    return null;
  }
  const kind = el => (el.dataset && el.dataset.fold !== undefined ? foldSel(el.dataset.fold) : el.tagName.toLowerCase());

  // each fold by its key and its place among folds of that key, the inspector's apart
  function folds() {
    const seen = new Map(), out = [];
    for (const d of $$('details[data-fold]')) {
      const k = side(d) + '\n' + d.dataset.fold, n = seen.get(k) || 0;
      seen.set(k, n + 1);
      out.push([k + '\n' + n, d]);
    }
    return out;
  }

  // the first element with an id or a fold key that starts in view of a scrolled part, and how far down it starts
  function anchor(box) {
    const top = box ? box.getBoundingClientRect().top : 0;
    for (const el of $$('[id], [data-id], [data-fold]', box || document.body)) {
      const r = el.getBoundingClientRect();
      if ((r.width || r.height) && r.top >= top - 0.5) return { at: locate(el, kind(el)), off: r.top - top };
    }
    return null;
  }

  function keep() {
    const k = { folds: new Map(folds().map(([f, d]) => [f, d.open])), scroll: [], why: [], focus: null, on: $$('#main .view.on[id]').map(v => v.id) };
    for (const box of [null, ...$$('#main, #nav, aside')]) {
      const x = box ? box.scrollLeft : window.scrollX, y = box ? box.scrollTop : window.scrollY;
      k.scroll.push({ box: box && locate(box, 'aside'), x, y, anchor: y ? anchor(box) : null });
    }
    for (const box of $$('.decide')) {
      const inp = box.querySelector('input.why');
      if ((inp && inp.value) || box.dataset.chosen) k.why.push({ at: locate(box, '.decide'), value: inp ? inp.value : '', chosen: box.dataset.chosen || '' });
    }
    const a = document.activeElement;
    if (a && a !== document.body && a.closest('#main, #nav, aside')) {
      k.focus = { at: locate(a, kind(a)), sel: typeof a.selectionStart === 'number' ? [a.selectionStart, a.selectionEnd] : null };
    }
    return k;
  }

  function restore(k) {
    for (const [f, d] of folds()) if (k.folds.has(f) && d.open !== k.folds.get(f)) d.open = k.folds.get(f);
    for (const w of k.why) {
      const box = find(w.at);
      if (!box) continue;
      if (w.chosen && box.dataset.chosen !== w.chosen) {
        const pick = box.querySelector('[data-act="pick"][data-action="' + esc(w.chosen) + '"]');
        if (pick) pick.click();
      }
      const inp = box.querySelector('input.why');
      if (inp && inp.value !== w.value) { inp.value = w.value; inp.dispatchEvent(new Event('input', { bubbles: true })); }
    }
    for (const s of k.scroll) {
      const box = s.box ? find(s.box) : null;
      if (s.box && !box) continue;
      if (box) { box.scrollLeft = s.x; box.scrollTop = s.y; } else window.scrollTo(s.x, s.y);
      const el = s.anchor && find(s.anchor.at);
      if (!el) continue;
      const by = el.getBoundingClientRect().top - (box ? box.getBoundingClientRect().top : 0) - s.anchor.off;
      if (Math.abs(by) >= 1) { if (box) box.scrollTop += by; else window.scrollBy(0, by); }
    }
    // focus goes back where it was, and nowhere a restored choice put it
    const el = k.focus && find(k.focus.at), now = document.activeElement;
    if (el) {
      if (el !== now) el.focus({ preventScroll: true });
      if (k.focus.sel && typeof el.setSelectionRange === 'function') try { el.setSelectionRange(k.focus.sel[0], k.focus.sel[1]); } catch (_) {}
    } else if (now && now !== document.body && now.closest('.decide')) now.blur();
  }

  // what rb open could not show: said by whoever draws it (the places' script), never in words of this module's own
  const failed = (error, q) => document.dispatchEvent(new CustomEvent('rb:failed', { detail: { error: error || '', query: q } }));

  // the page of `q` (a place and version rb open listed, or none for the place it started on, live), fetched and swapped in
  async function show(q, want) {
    const mine = ++asked;
    q = q || '';
    const r = await fetch('/' + q, { cache: 'no-store' });
    if (!r.ok) {
      let why = '';
      try { why = (await r.json()).error || ''; } catch (_) {}
      if (mine === asked) failed(why, q);
      return false;
    }
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    if (mine !== asked || !doc.getElementById('main')) return false;   // a later choice, or no page
    const tag = doc.getElementById('rb-live'), kept = keep();
    swapping = true;
    try {
      for (const a of [byId('nav'), byId('main'), ...$$('[data-swap][id]').filter(e => !e.closest('#main, #nav'))]) {
        const b = a && doc.getElementById(a.id);
        if (b) a.replaceChildren(...Array.from(b.childNodes));
      }
      if (doc.title) document.title = doc.title;
      key = tag ? tag.dataset.key || '' : want || key;
      query = tag ? tag.dataset.query || '' : q;
      // the script wires the new parts and draws its inspector again from its own state; without it the view stays
      if (document.dispatchEvent(new CustomEvent('rb:swapped', { cancelable: true, detail: { query } }))) {
        for (const id of kept.on) { const v = byId(id); if (v) v.classList.add('on'); }
      }
      restore(kept);
    } finally { swapping = false; }
    return true;
  }

  function said(j) {
    for (const f of LINES) {
      if (typeof j[f] !== 'string') continue;
      for (const el of $$('[data-live="' + f + '"]')) if (el.textContent !== j[f]) el.textContent = j[f];
    }
  }

  async function poll() {
    if (busy) return;
    busy = true;
    let ok = false;
    const q = query, mine = asked;
    try {
      const r = await fetch('/digest.json' + q, { cache: 'no-store' });
      const j = await r.json();
      if (r.ok && typeof j.key === 'string') {
        ok = true;
        if (q === query && mine === asked) { said(j); if (j.key !== key) ok = await show(q, j.key); }
      } else failed(j && j.error, q);
    } catch (_) {}
    busy = false;
    // every 5 s while the page is in view; less often while it is hidden or rb open does not answer
    wait = ok && !document.hidden ? EVERY : Math.min(Math.max(wait, EVERY) * 2, MOST);
    clearTimeout(timer);
    timer = setTimeout(poll, wait);
  }

  function start() {
    inspected();
    document.addEventListener('rb:card', () => { if (!swapping) inspected(); });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden || busy) return;
      wait = EVERY; clearTimeout(timer); timer = setTimeout(poll, 0);
    });
    timer = setTimeout(poll, EVERY);
  }

  window.rbLive = { show: q => show(q).catch(() => false) };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
