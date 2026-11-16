// A viewport change that a walk measures after (round f19d): Emulation.setDeviceMetricsOverride answers before the page
// has laid out at the new width, so a probe that only waits a fixed time can read the layout of the width before it.
// GitHub's macOS runner did, on public main 50548e4: at 1024, right after 1440, the inspector's bar, crumbs and body
// stood with their right edges at 1297 after 250 ms. resize() sets the viewport and returns once the page has laid out
// at it: innerWidth and innerHeight are the new size, nothing that can move a box is running, and the layout (the
// window's sizes, the document's scroll size and the box of every landmark and its children) is the same across two
// animation frames. Where Chrome draws no frame in 2 s, reads 50 ms apart stand in for the frames, as in cdp.mjs's
// stopped(). A read that throws counts as not yet. At `timeoutMs` it throws, naming the size and what the page last
// read. It evaluates in the page, so it is for a page whose scripts are on.
import { sleep } from './cdp.mjs';

const SIZE = (w, h) => `(innerWidth === ${w} && innerHeight === ${h})`;
// the layout, as one string: the window, the document and the landmarks with their children, the inspector's first
// (a record with many cards has more navs in its main than the 60 kept)
const LAYOUT = `(() => { const d = document.documentElement, r = e => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.width, b.height].map(Math.round).join(','); };
  const first = [...document.querySelectorAll('aside, #insp, #insp > *, .ibar, .ibody')];
  const marks = [...new Set([...first, ...document.querySelectorAll('header, nav, main, footer')])].slice(0, 60);
  return [innerWidth, innerHeight, d.clientWidth, d.scrollWidth, d.scrollHeight, ...marks.map(r)].join('|'); })()`;
// an animation or a transition running anywhere that can move a box (one of colour or opacity alone cannot, and one
// that never ends, a spinner, is not waited for)
const MOVING = `(() => { const moves = k => /^(transform|translate|rotate|scale|left|top|right|bottom|inset|margin|padding|width|height|min|max|flex|grid|fontSize|lineHeight|zoom|visibility|display)/.test(k);
  return document.getAnimations().filter(a => { const t = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming() : {};
    const keys = a.effect && a.effect.getKeyframes ? a.effect.getKeyframes().flatMap(f => Object.keys(f)).filter(k => !['offset', 'computedOffset', 'easing', 'composite'].includes(k)) : [];
    return (a.playState === 'running' || a.pending) && t.endTime !== Infinity && (!keys.length || keys.some(moves)); })
    .map(a => (a.effect && a.effect.target ? a.effect.target.tagName.toLowerCase() + (a.effect.target.id ? '#' + a.effect.target.id : '') : '') + ' ' + (a.animationName || a.transitionProperty || a.constructor.name)); })()`;

// waits until the page shown has laid out at `width` x `height` (see above); says how long it waited and whether
// frames were drawn
export async function laidOut(page, width, height = 900, { timeoutMs = 10000 } = {}) {
  const t0 = Date.now(), until = t0 + timeoutMs;
  let frames = true;
  const frame = async () => {
    if (!frames) { await sleep(50); return; }
    const r = await Promise.race([page.ev('new Promise(r => requestAnimationFrame(() => r(true)))'), sleep(2000).then(() => 'late')]);
    if (r === 'late' || (r && r.__error)) frames = false;
  };
  const late = async what => {
    const seen = await page.ev(`({ size: [innerWidth, innerHeight], moving: ${MOVING}, layout: ${LAYOUT}.slice(0, 300) })`);
    throw new Error(`the page did not lay out at ${width}x${height} in ${timeoutMs / 1000} s (${what}): ${JSON.stringify(seen)}`);
  };
  const sized = async () => (await page.ev(SIZE(width, height))) === true;
  const still = v => Array.isArray(v) && !v.length;
  await frame();
  while (!(await sized())) { if (Date.now() > until) await late('its size'); await frame(); }
  for (;;) {
    let moving = await page.ev(MOVING);
    while (!still(moving)) { if (Date.now() > until) await late('a box still moving'); await frame(); moving = await page.ev(MOVING); }
    // the same layout across two frames: three reads, each after a frame is drawn (four 50 ms apart where none is)
    let was = await page.ev(LAYOUT), same = 0;
    while (same < (frames ? 2 : 3)) {
      if (Date.now() > until) await late('its layout still changing');
      await frame();
      const now = await page.ev(LAYOUT);
      same = now === was ? same + 1 : 0; was = now;
    }
    // a box that started moving during those frames is waited for, then the layout read again
    const after = await page.ev(MOVING);
    if (still(after) && await sized()) return { ms: Date.now() - t0, frames };
  }
}

// sets the viewport and waits until the page has laid out at it (laidOut above)
export async function resize(page, width, height = 900, opts = {}) {
  await page.viewport(width, height);
  return laidOut(page, width, height, opts);
}
