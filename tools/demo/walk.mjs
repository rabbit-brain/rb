#!/usr/bin/env node
// The demo's capture (SPRINT-OCT24 6.3): drives Chrome over CDP through a script of beats and keeps every frame
// Page.startScreencast sends, with its time. It clicks for real (Input.dispatchMouseEvent at an element's centre)
// and types for real (Input.insertText). The screencast has no cursor: the walk keeps where its own mouse events put
// one (cursor.jsonl), and compose.py draws it on the frames, so the cursor is in the capture only, never in rb.
//
//   node tools/demo/walk.mjs --script beats.json --out <dir> [--serve <dir>] [--chrome <path>] [--headful]
//
// beats.json: {"width": 1440, "height": 900, "scale": 2, "beats": [{"name": "...", "steps": [step, ...]}, ...]}
// A step is one of:
//   {"goto": url}            {"wait": ms}             {"waitFor": "<js expression>", "timeout": ms}
//   {"click": "<css>"}       {"click": {"text": "<the element's own words>", "within": "<css>"}}
//   {"move": "<css>"}        {"type": "<text>", "cps": 14}            {"key": "Enter"}
//   {"scroll": "<css>"}      {"scrollBy": px, "ms": 600}              {"eval": "<js>"}
//   {"wheelTo": "<css>", "top": px, "ms": 300}  (wheel turns over the cursor until that element's top is at `top`)
//   {"play": true}           (term.html: starts the cast and waits until it has played)
//   {"mark": "<label>"}      (a timing marker; every beat's start is one too)
//   {"mark": "<label>", "rect": "<css>" or {"text": ...}}  (a marker that keeps where that element is, [x, y, w, h])
//   {"print": "<js>"}        (prints the expression's value: for writing a beat against a page's markup)
//   {"print": "<js>", "keep": "<name>"}   (and keeps it in walk.json's `kept`, under that name, for whoever made the script)
//   {"waitFile": "<path>", "timeout": ms}                 (until another process makes that file)
//   {"reloadUntil": "<js expression>", "live": ms, "timeout": ms}   (until the page shows it, reloaded once if not by `live`)
//   {"pointAt": "<css>" or {"text": ...}}   (the pointer put there at once, hovering; as a lead step, where a beat starts)
//   {"watch": "<css of the parts>", "until": "<path>", "ignore": "<css>", "timeout": ms}   (each rb:swapped a "change"
//                            marker with the words it newly shows and their boxes, and every line of text on screen
//                            then, as at the start and whenever the page's text moves with no swap (a "text" marker),
//                            until another process makes that file)
// "frame": true (or a css selector, "iframe" by default) on a waitFor, reloadUntil, print or eval step reads the
// expression inside the page's one visible frame it names (a page that shows another page in an <iframe>, as the
// hosted service shows rb's page: a sandboxed frame whose words the page around it cannot read), in the frame's own
// process when Chrome gives it one, else in a world of the walk's own in it; a frame hidden, off screen, clipped away or
// covered is never read, two visible ones are an error, and a page with no visible frame is read itself. walk.json's
// read_in says where the reads such steps went by were made ("frame process", "frame world", "page").
// Urls may start with /demo/ (this folder) or /files/ (the --serve folder), served on 127.0.0.1 by the walk itself.
// Output: frames/NNNNNN.jpg, frames.jsonl ({file, t}), cursor.jsonl ({t, x, y, down}), marks.jsonl ({t, label}),
// all on one clock: seconds since the screencast's first frame was asked for (CDP's frame timestamps, wall seconds).

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdirSync, writeFileSync, appendFileSync, readFileSync, statSync, mkdtempSync, rmSync, existsSync } from "node:fs";
import { join, resolve, extname, dirname, sep } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : true]);
  return acc;
}, []));
if (!args.script || !args.out) { console.error("usage: walk.mjs --script beats.json --out <dir> [--serve <dir>]"); process.exit(2); }
const script = JSON.parse(readFileSync(args.script, "utf8"));
const W = script.width || 1440, H = script.height || 900, SCALE = script.scale || 2;
const out = resolve(args.out);
mkdirSync(join(out, "frames"), { recursive: true });
for (const f of ["frames.jsonl", "cursor.jsonl", "marks.jsonl"]) writeFileSync(join(out, f), "");

// ---- the walk's own static server: /demo/ is this folder, /files/ the --serve folder
const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json", ".cast": "text/plain; charset=utf-8", ".png": "image/png" };
const roots = { "/demo/": here, "/files/": args.serve ? resolve(args.serve) : null };
const server = createServer((req, res) => {
  let path;
  try { path = decodeURIComponent(new URL(req.url, "http://x").pathname); } catch { res.writeHead(400); return res.end(); }
  for (const [prefix, root] of Object.entries(roots)) {
    if (root && path.startsWith(prefix)) {
      const file = resolve(root, "." + path.slice(prefix.length - 1));
      // inside its root only: a sibling folder that shares the root's name as a prefix is outside
      if (file.startsWith(root + sep) && existsSync(file) && statSync(file).isFile()) {
        res.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream" });
        let body;
        try { body = readFileSync(file); } catch { res.writeHead(403); return res.end(); }
        return res.end(body);
      }
    }
  }
  res.writeHead(404); res.end();
});
await new Promise(r => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;
const url = u => (u.startsWith("/demo/") || u.startsWith("/files/") ? base + u : u);

// ---- Chrome
const profile = mkdtempSync(join(tmpdir(), "walk-"));
const chrome = spawn(args.chrome || "google-chrome", [
  ...(args.headful ? [] : ["--headless=new"]), "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  `--force-device-scale-factor=${SCALE}`, "--no-first-run", "--no-default-browser-check", "--disable-crash-reporter", "--disable-breakpad", "--hide-scrollbars", "--disable-gpu", "--force-color-profile=srgb",
  `--window-size=${W},${H}`,
  // as tests/browser/cdp.mjs does: a container (CI, the box's rb-ci images) has no namespace for Chrome's sandbox
  ...(process.platform === "linux" ? ["--no-sandbox"] : []), "about:blank"], { stdio: ["ignore", "ignore", "pipe"], detached: true });
// Chrome runs in a process group of its own, and whatever way the walk ends, the whole group goes with it
let chromeExited = false;
chrome.on("exit", () => { chromeExited = true; });
const killChrome = sig => { try { process.kill(-chrome.pid, sig); } catch { /* gone already */ } };
process.on("exit", () => {
  if (!chromeExited) killChrome("SIGKILL");
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* left for the system's temp cleaning */ }
});
// a walk that cannot start its browser ends only once that browser has gone: killed, and its exit seen (reaped), so
// nothing of it outlives the walk, however busy the machine
const startOrEnd = promise => promise.catch(async e => {
  console.error(`walk: ${e.message || e}`);
  if (!chromeExited) killChrome("SIGKILL");
  for (let i = 0; i < 100 && !chromeExited; i++) await new Promise(r => setTimeout(r, 100));
  process.exit(1);
});
const within = (promise, ms, what) => Promise.race([promise, new Promise((_, rej) => setTimeout(() => rej(new Error(`${what} within ${ms / 1000} s`)), ms))]);
const wsUrl = await startOrEnd(within(new Promise((res, rej) => {
  let err = "";
  chrome.stderr.on("data", d => { err += d; const m = err.match(/DevTools listening on (ws:\/\/\S+)/); if (m) res(m[1]); });
  chrome.on("exit", c => rej(new Error(`chrome exited ${c}: ${err}`)));
}), 30000, "Chrome did not start"));
const ws = new WebSocket(wsUrl);
await startOrEnd(within(new Promise(r => ws.addEventListener("open", r, { once: true })), 15000, "Chrome did not open its DevTools socket"));
let nextId = 1;
const pending = new Map(), handlers = [];
ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
  else for (const h of handlers) h(m);
});
// every command answers within its time, and none waits on a browser that has gone: a walk ends, it never hangs
const TIMEOUT = Number(args.timeout || 30) * 1000;
const send = (method, params = {}, sessionId) => new Promise((res, rej) => {
  if (ws.readyState !== WebSocket.OPEN || chromeExited) return rej(new Error(`${method}: the browser is gone`));
  const id = nextId++;
  const timer = setTimeout(() => { pending.delete(id); rej(new Error(`${method} did not answer within ${TIMEOUT / 1000} s`)); }, TIMEOUT);
  pending.set(id, { res: v => { clearTimeout(timer); res(v); }, rej: e => { clearTimeout(timer); rej(e); } });
  ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});
const gone = why => { for (const [id, { rej }] of pending) { pending.delete(id); rej(new Error(why)); } };
ws.addEventListener("close", () => gone("the browser closed its connection"));
chrome.on("exit", c => gone(`chrome exited ${c}`));
const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
const page = (m, p) => send(m, p, sessionId);
await page("Page.enable");
await page("Runtime.enable");
await page("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: SCALE, mobile: false });

// ---- the page's frames: one Chrome puts in a process of its own (a sandboxed frame with an opaque origin, under site
// isolation) is a target of its own, attached as the page opens it, with a session of its own; one in the page's own
// process is read in an isolated world the walk makes in it
const frameSessions = new Map();   // the frame's target id (its frame id) -> its session
handlers.push(m => {
  if (m.sessionId !== sessionId) return;
  if (m.method === "Target.attachedToTarget" && m.params.targetInfo.type === "iframe") frameSessions.set(m.params.targetInfo.targetId, m.params.sessionId);
  if (m.method === "Target.detachedFromTarget") for (const [k, v] of frameSessions) if (v === m.params.sessionId) frameSessions.delete(k);
});
await page("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: false, flatten: true });

// ---- the screencast: every frame, with its own timestamp
let t0 = null, frameNo = 0;
const clock = () => Date.now() / 1000 - t0;
handlers.push(async m => {
  if (m.method === "Page.screencastFrame" && m.sessionId === sessionId) {
    const { data, metadata, sessionId: fs } = m.params;
    page("Page.screencastFrameAck", { sessionId: fs }).catch(() => {});
    const file = `frames/${String(++frameNo).padStart(6, "0")}.jpg`;
    writeFileSync(join(out, file), Buffer.from(data, "base64"));
    appendFileSync(join(out, "frames.jsonl"), JSON.stringify({ file, t: metadata.timestamp - t0 }) + "\n");
  }
});
const mark = (label, rect, more, at = clock()) => { appendFileSync(join(out, "marks.jsonl"), JSON.stringify({ t: at, label, ...(rect ? { rect } : {}), ...(more || {}) }) + "\n"); console.log(`${at.toFixed(2)} ${label}`); };

// ---- the cursor, which only the capture draws
let cx = W / 2, cy = H / 2, down = false, pointed = false, filming = false;
const cur = () => appendFileSync(join(out, "cursor.jsonl"), JSON.stringify({ t: clock(), x: cx, y: cy, down }) + "\n");
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function moveTo(x, y, ms = 450) {
  const sx = cx, sy = cy, n = Math.max(1, Math.round(ms / 16));
  for (let i = 1; i <= n; i++) {
    const k = i / n, e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;   // ease in and out
    cx = sx + (x - sx) * e; cy = sy + (y - sy) * e;
    await page("Input.dispatchMouseEvent", { type: "mouseMoved", x: cx, y: cy });
    cur();
    await sleep(16);
  }
}
// where frame steps read (the read a step went by: a wait's read that met it, a print's or an eval's), where the latest
// read was, and a frame's isolated world, by frame id
const readIn = new Set(), worlds = new Map();
const kept = {};   // what print steps with `keep` read, by name (walk.json)
let readFrom = null;
// the frame id of the one visible <iframe> the selector names in the top page (its frame is a target of its own when
// Chrome gives it a process, with that id), or null when none is visible
async function visibleFrame(selector) {
  const r = await page("Runtime.evaluate", { expression: `(() => {
      // on screen, drawn, and what is there at the middle of its part on screen: never clipped away or covered
      const shown = e => { const b = e.getBoundingClientRect();
        const l = Math.max(b.left, 0), t = Math.max(b.top, 0), r = Math.min(b.right, innerWidth), btm = Math.min(b.bottom, innerHeight);
        if (!(r - l >= 1 && btm - t >= 1)) return false;
        for (let a = e; a; a = a.parentElement) { const st = getComputedStyle(a); if (st.display === "none" || st.visibility !== "visible" || Number(st.opacity) === 0) return false; }
        return document.elementFromPoint((l + r) / 2, (t + btm) / 2) === e; };
      const v = [...document.querySelectorAll(${JSON.stringify(selector)})].filter(e => e.tagName === "IFRAME" && shown(e));
      if (v.length > 1) throw new Error(v.length + " visible frames match " + ${JSON.stringify(selector)} + ": name one");
      return v[0] || null; })()` });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  if (!r.result.objectId) return null;
  try { return (await page("DOM.describeNode", { objectId: r.result.objectId })).node.frameId || null; }
  finally { page("Runtime.releaseObject", { objectId: r.result.objectId }).catch(() => {}); }
}
async function evaluate(expression, inFrame = false) {
  let params = { expression, awaitPromise: true, returnByValue: true }, session = sessionId, where = "page";
  const frameId = inFrame ? await visibleFrame(inFrame === true ? "iframe" : inFrame) : null;
  if (frameId && frameSessions.has(frameId)) { session = frameSessions.get(frameId); where = "frame process"; }
  else if (frameId) {
    if (!worlds.has(frameId)) worlds.set(frameId, (await page("Page.createIsolatedWorld", { frameId, worldName: "walk" })).executionContextId);
    params.contextId = worlds.get(frameId); where = "frame world";
  }
  let r;
  try { r = await send("Runtime.evaluate", params, session); }
  catch (e) { if (params.contextId) for (const [k, v] of worlds) if (v === params.contextId) worlds.delete(k); throw e; }   // a frame loaded again: a new world next time
  readFrom = where;
  if (r.exceptionDetails) throw new Error(`${expression}: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}`);
  return r.result.value;
}
// the page expression that finds a target: by css, or by its own words
function finder(target) {
  return typeof target === "string"
    ? `document.querySelector(${JSON.stringify(target)})`
    : `(() => { const root = ${target.within ? `document.querySelector(${JSON.stringify(target.within)})` : "document"};
        const want = ${JSON.stringify(target.text)}.replace(/\\s+/g, " ").trim();
        const all = [...root.querySelectorAll("*")];
        const norm = e => e.textContent.replace(/\\s+/g, " ").trim();
        const vis = e => { const r = e.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) return false;
          if (getComputedStyle(e).visibility !== "visible") return false;
          for (let a = e; a; a = a.parentElement) { const st = getComputedStyle(a); if (st.display === "none" || Number(st.opacity) === 0) return false; }
          return true; };
        // the innermost visible elements whose own words are these (a link, not the card around it), else leaves that start so
        const inner = list => { const v = list.filter(vis); return v.filter(e => !v.some(o => o !== e && e.contains(o))); };
        let found = inner(all.filter(e => norm(e) === want));
        if (!found.length) found = inner(all.filter(e => e.children.length === 0 && norm(e).startsWith(want)));
        if (found.length > 1) throw new Error(found.length + " elements hold these words: name one with within or css");
        return found[0] || null; })()`;
}
// an element's centre on screen, scrolled into view first; `still` leaves the page where it is (a beat whose zoom frames
// a part already on screen, so each move does not scroll it again), and an element off the screen then is an error
async function centre(target, still = false) {
  const box = await evaluate(`(() => { const e = ${finder(target)}; if (!e) return null;
      if (${still ? "true" : "false"}) { const b = e.getBoundingClientRect();
        if (b.top < 0 || b.left < 0 || b.bottom > innerHeight || b.right > innerWidth) return { offscreen: true }; }
      else e.scrollIntoView({ block: "center", inline: "nearest" });
      // aim at the words themselves (a wide row's own text, not the empty middle of the row), else the element's centre
      const range = document.createRange(); range.selectNodeContents(e);
      const words = [...range.getClientRects()].find(b => b.width > 0 && b.height > 0);
      const r = words || e.getBoundingClientRect();
      const x = r.left + Math.min(r.width / 2, 120), y = r.top + r.height / 2, at = document.elementFromPoint(x, y);
      // the click lands on what was named, not on something drawn over it
      if (!at || !(e === at || e.contains(at) || at.contains(e))) return { covered: at ? at.tagName + "." + at.className : "nothing" };
      return { x, y }; })()`);
  if (!box) throw new Error(`no element for ${JSON.stringify(target)}`);
  if (box.offscreen) throw new Error(`${JSON.stringify(target)} is not wholly on screen, and the step keeps the page still`);
  if (box.covered) throw new Error(`${JSON.stringify(target)} is covered by ${box.covered} where it would be clicked`);
  return box;
}

const stepRun = {
  async goto(s) { await page("Page.navigate", { url: url(s.goto) }); await waitFor("document.readyState === 'complete'", 20000); await sleep(s.settle ?? 600); },
  async wait(s) { await sleep(s.wait); },
  async waitFor(s) { await waitFor(s.waitFor, s.timeout ?? 20000, s.frame); },
  async move(s) { const b = await centre(s.move, s.still); await moveTo(b.x, b.y, s.ms); },
  async click(s) {
    const b = await centre(s.click, s.still);
    await moveTo(b.x, b.y, s.ms);
    await sleep(120);
    down = true; cur();
    await page("Input.dispatchMouseEvent", { type: "mousePressed", x: cx, y: cy, button: "left", clickCount: 1 });
    await sleep(70);
    await page("Input.dispatchMouseEvent", { type: "mouseReleased", x: cx, y: cy, button: "left", clickCount: 1 });
    down = false; cur();
    await sleep(s.settle ?? 500);
  },
  async type(s) { for (const ch of s.type) { await page("Input.insertText", { text: ch }); await sleep(1000 / (s.cps || 14)); } },
  async key(s) {
    const codes = { Enter: [13, "\r"], Tab: [9, "\t"], Escape: [27, ""] };
    const [kc, text] = codes[s.key] || [0, ""];
    await page("Input.dispatchKeyEvent", { type: "keyDown", key: s.key, code: s.key, windowsVirtualKeyCode: kc, text });
    await page("Input.dispatchKeyEvent", { type: "keyUp", key: s.key, code: s.key, windowsVirtualKeyCode: kc });
    await sleep(300);
  },
  async scroll(s) { const b = await centre(s.scroll); await moveTo(b.x, b.y, s.ms); },
  async scrollBy(s) {
    const n = Math.max(1, Math.round((s.ms || 600) / 16));
    for (let i = 0; i < n; i++) { await page("Input.dispatchMouseEvent", { type: "mouseWheel", x: cx, y: cy, deltaX: 0, deltaY: s.scrollBy / n }); await sleep(16); }
    await sleep(250);
  },
  // real wheel turns over the cursor until the named element's top is `top` px from the window's (the content it
  // scrolls may change height between builds, so no fixed distance is right)
  async wheelTo(s) {
    const at = `(() => { const e = document.querySelector(${JSON.stringify(s.wheelTo)}); return e ? e.getBoundingClientRect().top : null; })()`;
    for (let round = 0; round < 3; round++) {
      const top = await evaluate(at);
      if (top === null) throw new Error(`no element for ${JSON.stringify(s.wheelTo)}`);
      const delta = top - (s.top ?? 120);
      if (Math.abs(delta) < 4) break;
      const n = Math.max(1, Math.round((round ? 120 : s.ms || 300) / 40));
      for (let i = 0; i < n; i++) { await page("Input.dispatchMouseEvent", { type: "mouseWheel", x: cx, y: cy, deltaX: 0, deltaY: delta / n }); await sleep(16); }
      await sleep(200);
    }
  },
  // another process says when: the walk waits until that file exists (the share beat's terminal, once the watch
  // has sent the new run)
  async waitFile(s) {
    const end = Date.now() + (s.timeout ?? 300000);
    while (!existsSync(s.waitFile)) { if (Date.now() > end) throw new Error(`no ${s.waitFile} within ${(s.timeout ?? 300000) / 1000} s`); await sleep(100); }
  },
  // a page that may show a change by itself: it is given `live` ms to, then reloaded as a reader would, and given
  // `timeout` ms more; a marker says which ("shown live" or "shown after a reload")
  async reloadUntil(s) {
    try { await waitFor(s.reloadUntil, s.live ?? 20000, s.frame); mark("shown live"); return; } catch { /* reloaded below */ }
    // the whole page, as a reader reloads it (a frame in it loads again with it)
    await page("Page.reload", { ignoreCache: true });
    await waitFor("document.readyState === 'complete'", 20000);
    await waitFor(s.reloadUntil, s.timeout ?? 20000, s.frame);
    mark("shown after a reload");
  },
  async eval(s) { await evaluate(s.eval, s.frame); if (s.frame) readIn.add(readFrom); },
  // the pointer put on an element at once (scrolled into view as a move would), with the page's hover state: a lead step
  // that starts a beat with the pointer where the beat before it left it; the capture draws it from the first frame
  async pointAt(s) {
    const b = await centre(s.pointAt, s.still);
    cx = b.x; cy = b.y; pointed = true;
    await page("Input.dispatchMouseEvent", { type: "mouseMoved", x: cx, y: cy });
    if (filming) cur();  // filmed, the pointer jumps there in the capture too
  },
  async print(s) {
    const v = await evaluate(s.print, s.frame);
    console.log(JSON.stringify(v));
    if (s.frame) readIn.add(readFrom);
    if (s.keep) kept[s.keep] = v;
  },
  // the cast plays in the page's own time: started without waiting on it (one CDP call answers within 30 s, and a
  // playback can last longer), then the walk waits until the page says it has played
  async play() {
    await waitFor("window.__ready === true", 20000);
    await evaluate("window.__start(), true");
    await waitFor("window.__done === true", (await evaluate("window.__length")) * 1000 + 30000);
  },
  // a page that changes by itself (rb open's live module swaps a new version of its parts in, and says so with its
  // rb:swapped event): watched until another process makes the `until` file, each swap a "change" marker at the moment
  // it happened, keeping the words it newly shows (`words`), the words it no longer shows (`gone`), and where both are
  // drawn (`boxes`, [x, y, w, h] in CSS pixels of the window, off it too: the new words where they are now, the gone
  // ones where they were; `near`, the header row of each table a changed word sits in, what a viewer reads the cell
  // by), read once the page has drawn its parts again. Words are compared by their part (the nearest
  // element with an id) as a multiset, and words that show more than once in a part by their row too, so words only
  // moved by another part are no change, and a new row's button is the new one; words inside `ignore` (rb open's own
  // clock lines) are left out.
  async watch(s) {
    await evaluate(watcher(s.watch, s.ignore || null, s.settle ?? 400));
    mark("text", null, { text: await evaluate("window.__walkText()") });
    const end = Date.now() + (s.timeout ?? 3600000);
    let seen = 0, seenText = 0;
    const take = async () => {
      for (const c of await evaluate(`window.__walkChanges.slice(${seen})`)) {
        seen++;
        mark("change", null, { words: c.words, gone: c.gone, boxes: c.boxes, near: c.near, text: c.text }, clock() - (Date.now() - c.at) / 1000);
      }
      // text the page changed with no swap (rb open redraws its "changed 2 minutes ago" lines in place)
      for (const c of await evaluate(`window.__walkTexts.slice(${seenText})`)) {
        seenText++;
        mark("text", null, { text: c.text }, clock() - (Date.now() - c.at) / 1000);
      }
    };
    while (!existsSync(s.until)) {
      if (Date.now() > end) throw new Error(`no ${s.until} within ${(s.timeout ?? 3600000) / 1000} s`);
      await take(); await sleep(250);
    }
    await sleep((s.settle ?? 400) + 200); await take();
  },
  // with `rect`, the marker keeps where that element is drawn now, in CSS pixels, without scrolling to it (a cut
  // checks that the part its voice speaks of is inside the segment's zoom)
  async mark(s) {
    if (!s.rect) return mark(s.mark);
    const r = await evaluate(`(() => { const e = ${finder(s.rect)}; if (!e) return null; const b = e.getBoundingClientRect();
        return [b.left, b.top, b.width, b.height].map(v => Math.round(v * 10) / 10); })()`);
    if (!r) throw new Error(`no element for ${JSON.stringify(s.rect)}`);
    mark(s.mark, r);
  },
};
// the page script behind a watch step: the words drawn now in the watched parts, each with its boxes, and after each
// rb:swapped (once the page has drawn its parts again, `settle` ms on) the words that were not there before
function watcher(roots, ignore, settle) {
  return `(() => {
    if (window.__walkChanges) return true;
    const roots = ${JSON.stringify(roots)}, ignore = ${JSON.stringify(ignore)};
    const drawn = e => { for (let a = e; a; a = a.parentElement) { const st = getComputedStyle(a);
      if (st.display === "none" || st.visibility !== "visible" || Number(st.opacity) === 0) return false; } return true; };
    // every line of text the window shows, anywhere on the page (its clock lines too): a cut keeps its own marks off them
    window.__walkText = () => { const out = [], W = innerWidth, H = innerHeight;
      const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n; (n = w.nextNode());) {
        const el = n.parentElement;
        if (!n.textContent.trim() || !el || !drawn(el)) continue;
        const r = document.createRange(); r.selectNodeContents(n);
        for (const b of r.getClientRects()) {
          const l = Math.max(b.left, 0), t = Math.max(b.top, 0), rr = Math.min(b.right, W), bb = Math.min(b.bottom, H);
          if (rr > l && bb > t) out.push([l, t, rr - l, bb - t].map(v => Math.round(v * 10) / 10));
        } }
      return out; };
    const words = () => { const out = [];
      for (const root of document.querySelectorAll(roots)) {
        const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let n; (n = w.nextNode());) {
          const t = n.textContent.replace(/\\s+/g, " ").trim(), el = n.parentElement;
          if (!t || !el || (ignore && el.closest(ignore)) || !drawn(el)) continue;
          const r = document.createRange(); r.selectNodeContents(n);
          const b = [...r.getClientRects()].filter(b => b.width > 0 && b.height > 0).map(b => [b.left, b.top, b.width, b.height].map(v => Math.round(v * 10) / 10));
          // a word's part: the nearest element with an id (a count in the nav is not the same count in the pane)
          const host = el.closest("[id]");
          // and its row: the nearest element around it with more words than its own (two "Inspect" buttons are
          // two rows' buttons), read only where a word shows more than once in its part
          let row = el;
          while (row.parentElement && row !== root && row.textContent.replace(/\\s+/g, " ").trim() === t) row = row.parentElement;
          const k = (host ? host.id : "") + "\\n" + t;
          // a word in a table's cell: the table's header row is where a viewer reads what the cell is (kept clear too)
          const cell = el.closest("td,th"), head = cell && cell.closest("table") && cell.closest("table").tHead;
          const hr = head && head.getBoundingClientRect();
          const h = hr && hr.width > 0 && hr.height > 0 ? [hr.left, hr.top, hr.width, hr.height].map(v => Math.round(v * 10) / 10) : null;
          if (b.length) out.push({ t, k, kr: k + "\\n" + row.textContent.replace(/\\s+/g, " ").trim().slice(0, 400), b, h });
        } }
      return out; };
    let before = words();
    window.__walkChanges = [];
    // and every other time the page's text moves (a clock line redrawn in place, with no swap): the lines again, kept
    // from the first mutation that moved them, when they differ from the last kept
    window.__walkTexts = [];
    let lastText = JSON.stringify(window.__walkText()), first = null;
    new MutationObserver(() => {
      if (first !== null) return;
      first = Date.now();
      setTimeout(() => requestAnimationFrame(() => {
        const t = window.__walkText(), j = JSON.stringify(t), at = first;
        first = null;
        if (j !== lastText) { lastText = j; window.__walkTexts.push({ at, text: t }); }
      }), ${settle});
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true });
    document.addEventListener("rb:swapped", () => {
      const at = Date.now();
      setTimeout(() => requestAnimationFrame(() => {
        const now = words(), fresh = [], gone = [];
        // each side's words matched with the other's by part and words: what is left is new, or gone
        const twice = new Set();
        for (const list of [before, now]) { const n = new Map(); for (const w of list) { n.set(w.k, (n.get(w.k) || 0) + 1); if (n.get(w.k) > 1) twice.add(w.k); } }
        const key = w => (twice.has(w.k) ? w.kr : w.k);
        const diff = (a, b, left) => { const n = new Map();
          for (const w of b) n.set(key(w), (n.get(key(w)) || 0) + 1);
          for (const w of a) { const c = n.get(key(w)) || 0; if (c) n.set(key(w), c - 1); else left.push(w); } };
        diff(now, before, fresh); diff(before, now, gone);
        before = now;
        window.__walkChanges.push({ at, words: fresh.map(w => w.t), gone: gone.map(w => w.t),
          boxes: fresh.flatMap(w => w.b).concat(gone.flatMap(w => w.b)),
          near: [...new Set(fresh.concat(gone).filter(w => w.h).map(w => JSON.stringify(w.h)))].map(j => JSON.parse(j)),
          text: window.__walkText() });
      }), ${settle});
    });
    return true; })()`;
}
async function waitFor(expr, timeout, inFrame = false) {
  const end = Date.now() + timeout;
  let last = null;
  while (Date.now() < end) { try { if (await evaluate(`!!(${expr})`, inFrame)) { if (inFrame) readIn.add(readFrom); return; } last = null; } catch (e) { last = e; /* the page is still loading */ } await sleep(100); }
  throw new Error(`timed out waiting for ${expr}${last ? ` (last: ${String(last.message || last).split("\n")[0]})` : ""}`);
}

let failed = null;
try {
  // the first page is loaded before the screencast starts, so a capture never opens on Chrome's blank page
  // (a goto, and the waits for its content that follow it), so a capture opens on the page as it stands loaded
  const lead = script.beats[0]?.steps || [];
  if (lead[0]?.goto) {
    t0 = Date.now() / 1000;
    let n = 0;
    // and the steps marked `lead` (a page's first-visit state set and the page loaded again), so they are not filmed
    while (n < lead.length && (n === 0 ? lead[n].goto : lead[n].waitFor || lead[n].lead)) {
      const kind = n === 0 ? "goto" : Object.keys(lead[n]).find(k => stepRun[k]);
      if (n && lead[n].lead && !["goto", "waitFor", "eval", "wait", "pointAt"].includes(kind)) throw new Error(`a lead step only loads or sets up: ${JSON.stringify(lead[n])}`);
      await stepRun[kind](lead[n]); n++;
    }
    script.beats[0].steps = lead.slice(n);
  }
  // a lead step anywhere else would be filmed: it is refused before anything is
  for (const beat of script.beats) for (const s of beat.steps) if (s.lead) throw new Error(`a lead step comes only after the first goto and its lead steps: ${JSON.stringify(s)}`);
  t0 = Date.now() / 1000;
  await page("Page.startScreencast", { format: "jpeg", quality: 90, maxWidth: Math.round(W * SCALE), maxHeight: Math.round(H * SCALE), everyNthFrame: 1 });
  // no cursor until the walk first moves it: a beat nobody points in shows none (one a lead step put somewhere shows from the start)
  filming = true;
  if (pointed) cur();
  for (const beat of script.beats) {
    mark(`beat ${beat.name}`);
    for (const s of beat.steps) {
      const kind = Object.keys(s).find(k => stepRun[k]);
      if (!kind) throw new Error(`unknown step ${JSON.stringify(s)}`);
      await stepRun[kind](s);
    }
  }
  mark("end");
  await sleep(500);
  await page("Page.stopScreencast");
} catch (e) { failed = e; }
writeFileSync(join(out, "walk.json"), JSON.stringify({ width: W, height: H, scale: SCALE, frames: frameNo, read_in: [...readIn].sort(), kept, error: failed ? String(failed.message || failed) : null }, null, 2));
// Chrome closes itself (and its own temporary files) when asked; signals are for one that does not
if (!chromeExited && ws.readyState === WebSocket.OPEN) {
  ws.send(JSON.stringify({ id: nextId++, method: "Browser.close" }));
  for (let i = 0; i < 50 && !chromeExited; i++) await sleep(100);
}
ws.close(); server.close();
if (!chromeExited) killChrome("SIGTERM");
for (let i = 0; i < 50 && !chromeExited; i++) await sleep(100);
killChrome("SIGKILL");  // the group: any helper still running
// a helper may still be writing in the profile as it ends: remove it until it stays removed
for (let i = 0; i < 20; i++) {
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* still being written */ }
  await sleep(250);
  if (!existsSync(profile)) break;
}
if (failed) { console.error(`walk: ${failed.message || failed}`); process.exit(1); }
console.log(`walk: ${frameNo} frames in ${out}`);
