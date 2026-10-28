// Chrome over the DevTools protocol, for the app harness (app.mjs) and the smoke and screenshot runners.
// Node 22 or later: the protocol is spoken over node's global WebSocket, which node 18 lacks. Each Chrome gets its
// own free port and its own profile in a temporary folder; every process started here is stopped before node exits,
// on success, failure, timeout or a signal (run() below), and its folder is removed.
import { spawn, spawnSync } from 'node:child_process';
import { accessSync, constants, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir, userInfo } from 'node:os';
import { basename, delimiter, join } from 'node:path';

export const sleep = ms => new Promise(r => setTimeout(r, ms));

// no Chrome on this machine: run() exits 3, so a caller can tell it from a runner that failed
export class NoChrome extends Error {}

// ---------------------------------------------------------------- finding Chrome

const isFile = p => { try { return statSync(p).isFile(); } catch { return false; } };
const runnable = p => { try { accessSync(p, process.platform === 'win32' ? constants.F_OK : constants.X_OK); return isFile(p); } catch { return false; } };

// the home folders a per-user install may sit in: HOME's, and the account's own, which a runner given another HOME
// (the tests give the record's) still finds, since os.userInfo() reads it from the system, not from HOME
function homes() {
  const found = [homedir()];
  try { found.push(userInfo().homedir); } catch {}
  return [...new Set(found.filter(Boolean))];
}

// where Chrome is installed by default, on the system this runs on
export function usualPaths(platform = process.platform, env = process.env) {
  if (platform === 'darwin') {
    const apps = ['/Applications', ...homes().map(h => join(h, 'Applications'))];
    return apps.flatMap(a => [
      join(a, 'Google Chrome.app/Contents/MacOS/Google Chrome'),
      join(a, 'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'),
      join(a, 'Chromium.app/Contents/MacOS/Chromium'),
    ]);
  }
  if (platform === 'win32') {
    const roots = [env.PROGRAMFILES, env['PROGRAMFILES(X86)'], env.LOCALAPPDATA].filter(Boolean);
    return roots.map(r => join(r, 'Google', 'Chrome', 'Application', 'chrome.exe'));
  }
  return ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/opt/google/chrome/chrome', '/usr/bin/chromium',
    '/usr/bin/chromium-browser', '/snap/bin/chromium'];
}

function onPath(name, env = process.env) {
  const exts = process.platform === 'win32' ? (env.PATHEXT || '.EXE').split(';').filter(Boolean) : [''];
  for (const dir of (env.PATH || '').split(delimiter).filter(Boolean)) {
    for (const ext of exts) if (runnable(join(dir, name + ext))) return join(dir, name + ext);
  }
  return null;
}

// RB_CHROME, then the usual paths, then google-chrome on PATH (where GitHub's Ubuntu image has it)
export function findChrome(env = process.env) {
  if (env.RB_CHROME) {
    if (runnable(env.RB_CHROME)) return env.RB_CHROME;
    throw new Error(`RB_CHROME names ${env.RB_CHROME}, which is not a program here`);
  }
  for (const p of usualPaths()) if (runnable(p)) return p;
  for (const name of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome']) {
    const p = onPath(name, env);
    if (p) return p;
  }
  throw new NoChrome(`no Chrome found: RB_CHROME is not set, none is at ${usualPaths().join(', ')}, and none is on PATH`);
}

// ---------------------------------------------------------------- what this process started, stopped every time

const stoppers = [];       // newest first when stopping: the browser before the server it reads
const children = new Set();
const folders = new Set();
const trees = new WeakMap();      // a child's killTree options, where its starter gave them

function track(proc, tree) {
  children.add(proc);
  if (tree) trees.set(proc, tree);
  proc.once('exit', () => children.delete(proc));
  return proc;
}

const alive = proc => proc.exitCode === null && proc.signalCode === null;

// Windows ends only the process it is asked to end, so a process's helpers outlive it there: its whole tree is ended,
// while its root still runs, since taskkill /T finds the tree through the root. Elsewhere, false: the caller's own
// process group holds the helpers.
export function killTree(pid, { platform = process.platform, run = spawnSync } = {}) {
  if (platform !== 'win32' || !pid) return false;
  try { run('taskkill', ['/T', '/F', '/PID', String(pid)], { stdio: 'ignore', windowsHide: true }); } catch {}
  return true;
}

function exited(proc, ms) {
  if (!alive(proc)) return Promise.resolve(true);
  return new Promise(r => {
    const t = setTimeout(() => r(false), ms);
    proc.once('exit', () => { clearTimeout(t); r(true); });
  });
}

// each signal in turn, `ms` apart, then a kill: a signal can arrive ignored, as one sent to a background job does
async function stopProcess(proc, signals, ms) {
  for (const signal of [...signals, 'SIGKILL']) {
    if (!alive(proc)) return;
    try { proc.kill(signal); } catch {}
    if (await exited(proc, ms)) return;
  }
}

const nap = ms => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

// the command line of each process running that holds `name` in it: as ps lists them, or PowerShell on Windows; none
// when neither answers
export function holders(name, { platform = process.platform } = {}) {
  const [cmd, args] = platform === 'win32'
    ? ['powershell', ['-NoProfile', '-NonInteractive', '-Command', 'Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }']]
    : ['ps', ['-A', '-ww', '-o', 'command=']];
  const r = spawnSync(cmd, args, { encoding: 'utf8', windowsHide: true, timeout: 60000 });
  return r.status === 0 && typeof r.stdout === 'string' ? r.stdout.split(/\r?\n/).filter(l => l.includes(name)) : [];
}

// Chrome's folder, removed once no process started with its profile runs, for `ms` at most: a helper can still write into
// the profile (its cache's index) as it ends, after Chrome itself, and would leave the folder behind. Synchronous, so
// node's exit handler can use it
function removeFolder(dir, ms = 10000) {
  const until = Date.now() + ms;
  while (holders(basename(dir)).length && Date.now() < until) nap(100);
  // Windows holds a file a moment after its process ends
  try { rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }); } catch {}
  folders.delete(dir);
}

export async function stopAll() {
  while (stoppers.length) {
    const stop = stoppers.pop();
    try { await stop(); } catch {}
  }
  for (const proc of children) { try { proc.kill('SIGKILL'); } catch {} }
  for (const dir of [...folders]) removeFolder(dir);
}

// whatever ends node, nothing it started outlives it
process.once('exit', () => {
  for (const proc of children) { if (alive(proc)) killTree(proc.pid, trees.get(proc)); try { proc.kill('SIGKILL'); } catch {} }
  for (const dir of [...folders]) removeFolder(dir, 5000);
});
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP', 'SIGBREAK']) {
  try { process.once(sig, () => { stopAll().finally(() => process.exit(130)); }); } catch {}
}

// what a runner says when it ends: one JSON line on stdout, whatever happened
// (chrome_profile is Chrome's --user-data-dir, which each of its helpers is started with too)
export const started = { chrome_pid: null, chrome_profile: null, rb_open_pid: null, rb_open_url: null };

// main() under a deadline; exits 0, 1 on any failure or the deadline, 3 when there is no Chrome. With `idleMs`, a walk
// also stops when it says no progress() for that long: a slow machine that keeps checking gets to the end under the
// whole deadline, and a walk that hangs stops soon after its last check, named
let lastSaid = { at: 0, what: '' };
export function progress(what = '') { lastSaid = { at: Date.now(), what }; }
export async function run(main, { timeoutMs, idleMs }) {
  let timer, idle, code = 0, said = {};
  lastSaid = { at: Date.now(), what: '' };
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`stopped after ${Math.round(timeoutMs / 1000)} s`)), timeoutMs);
    if (idleMs) idle = setInterval(() => {
      if (Date.now() - lastSaid.at > idleMs) reject(new Error(`stopped: no progress for ${Math.round(idleMs / 1000)} s${lastSaid.what ? ` after '${lastSaid.what}'` : ''}`));
    }, Math.min(1000, idleMs));
  });
  try {
    said = (await Promise.race([main(), deadline])) || {};
  } catch (e) {
    code = e instanceof NoChrome ? 3 : 1;
    // a walk that failed still says each check, so a test can read the ones it is about
    said = { error: String(e && e.message || e), ...(e && Array.isArray(e.results) ? { checks: e.results } : {}) };
    process.stderr.write(`${said.error}\n`);
  } finally {
    clearTimeout(timer); clearInterval(idle);
    await stopAll();
  }
  // exit once the line is out: to a pipe, stdout is written later, and an exit before then cuts a long line short
  process.stdout.write(JSON.stringify({ ok: code === 0, ...said, started }) + '\n', () => process.exit(code));
}

// ---------------------------------------------------------------- rb open

// `rb open --port 0` in `cwd`: the address it prints, once it has printed all it prints on starting
export async function startRbOpen({ rb, cwd, env = process.env, timeoutMs = 60000 }) {
  const proc = track(spawn(rb, ['open', '--port', '0'], { cwd, env, stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true }));
  stoppers.push(() => stopProcess(proc, ['SIGINT', 'SIGTERM'], 5000));     // Ctrl-C, as rb open says; on Windows any signal ends it
  started.rb_open_pid = proc.pid ?? null;
  let said = '', failed = null;
  proc.on('error', e => { failed = e; });
  proc.stderr.setEncoding('utf8');
  proc.stderr.on('data', d => { said = (said + d).slice(-100000); });
  const until = Date.now() + timeoutMs;
  for (;;) {
    const link = said.match(/^rb open \u00b7 (http:\/\/127\.0\.0\.1:\d+)\/\r?$/m);
    if (link && said.trimEnd().endsWith('stops the server.')) {
      started.rb_open_url = link[1];
      return { url: link[1], proc, said: () => said };
    }
    if (failed) throw new Error(`rb open could not be started (${rb}): ${failed.message}`);
    if (!alive(proc)) throw new Error(`rb open did not start (exit ${proc.exitCode}): ${said.trim()}`);
    if (Date.now() > until) throw new Error(`rb open did not start in ${timeoutMs / 1000} s: ${said.trim()}`);
    await sleep(20);
  }
}

// `rb <argv> --json` in `cwd`, which must answer ok
export async function rbJson({ rb, cwd, env = process.env, argv, timeoutMs = 120000 }) {
  const proc = track(spawn(rb, [...argv, '--json'], { cwd, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true }));
  stoppers.push(() => stopProcess(proc, ['SIGTERM'], 5000));
  let out = '', err = '', failed = null;
  const spawnFailed = new Promise(r => proc.once('error', e => { failed = e; r(true); }));     // then no exit follows
  proc.stdout.setEncoding('utf8'); proc.stdout.on('data', d => { out += d; });
  proc.stderr.setEncoding('utf8'); proc.stderr.on('data', d => { err = (err + d).slice(-20000); });
  const ended = await Promise.race([exited(proc, timeoutMs), spawnFailed]);
  if (failed) throw new Error(`rb could not be started (${rb}): ${failed.message}`);
  if (!ended) throw new Error(`rb ${argv.join(' ')} did not end in ${timeoutMs / 1000} s`);
  let answer;
  try { answer = JSON.parse(out); } catch { throw new Error(`rb ${argv.join(' ')} answered no JSON (exit ${proc.exitCode}): ${err.trim()}`); }
  if (!answer.ok) throw new Error(`rb ${argv.join(' ')} failed: ${JSON.stringify(answer.errors)}`);
  return answer.data;
}

// ---------------------------------------------------------------- Chrome

class Connection {
  constructor(sock) {
    this.sock = sock; this.id = 0; this.pending = new Map(); this.listeners = new Set(); this.closed = false;
    this.sent = new Set();      // each method asked for, once
    sock.onmessage = ev => {
      const m = JSON.parse(typeof ev.data === 'string' ? ev.data : Buffer.from(ev.data).toString('utf8'));
      if (m.id !== undefined) {
        const p = this.pending.get(m.id);
        if (!p) return;
        this.pending.delete(m.id);
        if (m.error) p.reject(new Error(`${p.method}: ${m.error.message}`)); else p.resolve(m.result || {});
        return;
      }
      for (const l of [...this.listeners]) l(m);
    };
    sock.onclose = () => {
      this.closed = true;
      for (const p of this.pending.values()) p.reject(new Error(`${p.method}: the browser closed its connection`));
      this.pending.clear();
    };
  }

  send(method, params = {}, sessionId) {
    this.sent.add(method);
    if (this.closed) return Promise.reject(new Error(`${method}: the browser closed its connection`));
    return new Promise((resolve, reject) => {
      const id = ++this.id;
      this.pending.set(id, { resolve, reject, method });
      this.sock.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
}

// a headless Chrome on a free port with a profile of its own; `.page()` opens a tab. `tree` is killTree's options for
// its process tree, which a test gives to record the call on any system
export async function launchChrome({ chrome = findChrome(), timeoutMs = 30000, tree } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'rb-chrome-'));
  folders.add(dir);
  const profile = join(dir, 'profile'), home = join(dir, 'home');
  mkdirSync(profile); mkdirSync(home);
  const args = ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run',
    '--no-default-browser-check', '--disable-background-networking', '--disable-component-update', '--disable-default-apps',
    '--disable-extensions', '--disable-sync', '--metrics-recording-only', '--mute-audio', '--hide-scrollbars',
    '--password-store=basic', '--use-mock-keychain', '--force-color-profile=srgb'];
  // Ubuntu's runners refuse Chrome the user namespaces its sandbox needs; the pages it opens are rb's own, on this machine
  if (process.platform === 'linux') args.push('--no-sandbox');
  args.push('about:blank');
  // its own home on Linux and macOS, so nothing it writes lands outside its folder
  const env = process.platform === 'win32' ? process.env : { ...process.env, HOME: home, XDG_CONFIG_HOME: join(home, '.config'), XDG_CACHE_HOME: join(home, '.cache') };
  const proc = track(spawn(chrome, args, { env, stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true }), tree);
  started.chrome_pid = proc.pid ?? null;
  started.chrome_profile = profile;
  let said = '', failed = null, conn = null;
  proc.on('error', e => { failed = e; });
  proc.stderr.setEncoding('utf8');
  proc.stderr.on('data', d => { said = (said + d).slice(-20000); });
  stoppers.push(async () => {
    // on Windows its tree first, while Chrome runs: closed first, it would leave its helpers behind
    if (alive(proc) && killTree(proc.pid, tree)) await exited(proc, 5000);
    if (conn && !conn.closed) { try { await Promise.race([conn.send('Browser.close'), sleep(3000)]); } catch {} }
    await stopProcess(proc, ['SIGTERM'], 5000);
    removeFolder(dir);
  });
  // Chrome writes the port it took, and its browser's path, into the profile
  const until = Date.now() + timeoutMs;
  let port, path;
  for (;;) {
    try { [port, path] = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split(/\r?\n/); } catch {}
    if (port && path) break;
    if (failed) throw new Error(`Chrome could not be started (${chrome}): ${failed.message}`);
    if (!alive(proc)) throw new Error(`Chrome ended on starting (exit ${proc.exitCode}): ${said.trim().slice(-2000)}`);
    if (Date.now() > until) throw new Error(`Chrome did not start in ${timeoutMs / 1000} s: ${said.trim().slice(-2000)}`);
    await sleep(50);
  }
  const sock = new WebSocket(`ws://127.0.0.1:${port}${path}`);
  await new Promise((resolve, reject) => {
    sock.onopen = resolve;
    sock.onerror = () => reject(new Error(`no answer from Chrome's debugging port ${port}`));
  });
  conn = new Connection(sock);
  const version = await conn.send('Browser.getVersion');
  return { chrome, version: version.product, proc, page: () => openPage(conn), asked: method => conn.sent.has(method) };
}

async function openPage(conn) {
  const { targetId } = await conn.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await conn.send('Target.attachToTarget', { targetId, flatten: true });
  const send = (method, params = {}) => conn.send(method, params, sessionId);
  const errors = [], consoleMsgs = [], inflight = new Map();      // request id: its address
  let lastNet = Date.now();
  conn.on(m => {
    if (m.sessionId !== sessionId) return;
    const p = m.params || {};
    if (m.method === 'Runtime.exceptionThrown') errors.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text);
    if (m.method === 'Runtime.consoleAPICalled' && (p.type === 'error' || p.type === 'warning')) consoleMsgs.push(p.args.map(a => a.value ?? a.description).join(' '));
    if (m.method === 'Network.requestWillBeSent') { inflight.set(p.requestId, p.request.url); lastNet = Date.now(); }
    // answered is enough: a body the page never reads (a fetch whose answer it drops) never finishes loading
    if (['Network.responseReceived', 'Network.loadingFinished', 'Network.loadingFailed'].includes(m.method)) { inflight.delete(p.requestId); lastNet = Date.now(); }
  });
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable'); await send('DOM.enable');
  let media = { type: '', scheme: 'light' };

  const page = {
    send, errors, consoleMsgs,
    loading: () => [...inflight.values()],
    async viewport(width, height = 900) {
      await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 600 });
    },
    // the media type ('' for screen, 'print') and the colour scheme, set together: each call replaces both
    async media({ type = media.type, scheme = media.scheme } = {}) {
      media = { type, scheme };
      await send('Emulation.setEmulatedMedia', { media: type, features: [{ name: 'prefers-color-scheme', value: scheme }] });
    },
    async noScript(v) { await send('Emulation.setScriptExecutionDisabled', { value: v }); },
    // loads `url`; a page that cannot be loaded, or does not finish loading in `timeoutMs`, is an error
    async goto(url, { timeoutMs = 30000 } = {}) {
      const responses = [];
      let loaded = false;
      const off = conn.on(m => {
        if (m.sessionId !== sessionId) return;
        if (m.method === 'Network.responseReceived' && m.params.type === 'Document') responses.push(m.params);
        if (m.method === 'Page.loadEventFired') loaded = true;
      });
      const until = Date.now() + timeoutMs, late = () => new Error(`${url} did not finish loading in ${timeoutMs / 1000} s`);
      try {
        // Chrome answers a navigation only once the server does: a server that never answers holds it
        let timer;
        const r = await Promise.race([send('Page.navigate', { url }), new Promise((_, reject) => { timer = setTimeout(() => reject(late()), timeoutMs); })])
          .finally(() => clearTimeout(timer));
        if (r.errorText) throw new Error(`cannot load ${url}: ${r.errorText}`);
        if (!r.loaderId) return { status: null, sameDocument: true };      // a fragment of the page already shown
        while (!loaded) {
          if (Date.now() > until) throw late();
          await sleep(25);
        }
        const doc = responses.find(x => x.loaderId === r.loaderId);
        return { status: doc ? doc.response.status : null, sameDocument: false };
      } finally { off(); }
    },
    async blank() { await page.goto('about:blank', { timeoutMs: 10000 }); },
    async ev(expr) {
      const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true, userGesture: true });
      if (r.exceptionDetails) return { __error: r.exceptionDetails.exception?.description || r.exceptionDetails.text };
      return r.result?.value;
    },
    async waitFor(expr, timeoutMs = 30000) {
      const until = Date.now() + timeoutMs;
      for (;;) {
        const v = await page.ev(expr);
        if (v && !v.__error) return v;
        if (Date.now() > until) return null;
        await sleep(100);
      }
    },
    // nothing loading for `quietMs`, then fonts in and two frames drawn: the page as a reader would see it; with
    // the page's script off nothing is evaluated in it, and a short wait stands in for the frames
    async settle({ quietMs = 500, timeoutMs = 20000, script = true } = {}) {
      const until = Date.now() + timeoutMs;
      while ((inflight.size || Date.now() - lastNet < quietMs) && Date.now() < until) await sleep(50);
      if (!script) { await sleep(250); return; }
      const within = p => Promise.race([p, sleep(5000)]);
      await within(page.ev('document.fonts ? document.fonts.ready.then(() => true) : true'));
      await within(page.ev('new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))'));
    },
    // a class on <html>, through the DOM itself, so it works with the page's script off
    async htmlClass(name) {
      const { root } = await send('DOM.getDocument', { depth: 0 });
      const { nodeId } = await send('DOM.querySelector', { nodeId: root.nodeId, selector: 'html' });
      const has = async () => {
        const { attributes } = await send('DOM.getAttributes', { nodeId });
        for (let i = 0; i < attributes.length; i += 2) if (attributes[i] === 'class') return attributes[i + 1];
        return '';
      };
      if (name) {
        const now = (await has()).split(/\s+/).filter(Boolean);
        if (!now.includes(name)) await send('DOM.setAttributeValue', { nodeId, name: 'class', value: [...now, name].join(' ') });
      }
      return has();
    },
    async rect(selectorExpr) {
      return page.ev(`(() => { const e = (${selectorExpr}); if (!e) return null; e.scrollIntoView({block:'center', inline:'nearest'}); const r = e.getBoundingClientRect(), f = e.getClientRects()[0] || r; return {x: f.x + Math.min(f.width/2, 30), y: f.y + f.height/2, w: r.width, h: r.height, text: (e.innerText||e.textContent||'').trim().slice(0,120), tag: e.tagName, href: e.getAttribute('href')}; })()`);
    },
    // a real mouse click on the element `selectorExpr` evaluates to; null when there is none
    async clickEl(selectorExpr) {
      const r = await page.rect(selectorExpr);
      if (!r || r.__error) return null;
      await sleep(150);
      // a link that wraps is clicked on its first line, where its box begins (its whole box's left edge may be another
      // element's)
      const r2 = await page.ev(`(() => { const e = (${selectorExpr}); const r = e.getClientRects()[0] || e.getBoundingClientRect(); return {x: r.x + Math.min(r.width/2, 30), y: r.y + r.height/2}; })()`);
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x: r2.x, y: r2.y, button: 'left', clickCount: 1 });
      await sleep(300);
      return r;
    },
    // a real mouse click at (x, y)
    async clickAt(x, y) {
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 });
      await sleep(300);
    },
    // the pointer moved to (x, y), pressing nothing
    async mouse(x, y) { await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }); await sleep(50); },
    async key(key, code, keyCode, text) {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: keyCode, text });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: keyCode });
      await sleep(200);
    },
    async type(text) { for (const ch of text) await send('Input.dispatchKeyEvent', { type: 'char', text: ch }); await sleep(100); },
    // a PNG of the viewport, or of the whole page down to `maxHeight` px
    async shot(out, { full = false, maxHeight = 12000 } = {}) {
      let clip;
      if (full) {
        const m = await send('Page.getLayoutMetrics');
        const cs = m.cssContentSize || m.contentSize;
        clip = { x: 0, y: 0, width: Math.ceil(cs.width), height: Math.min(Math.ceil(cs.height), maxHeight), scale: 1 };
      }
      const r = await send('Page.captureScreenshot', clip ? { format: 'png', clip, captureBeyondViewport: true } : { format: 'png' });
      const bytes = Buffer.from(r.data, 'base64');
      writeFileSync(out, bytes);
      return { bytes: bytes.length, width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
    },
    async pdf(out) {
      const r = await send('Page.printToPDF', { printBackground: true, preferCSSPageSize: true });
      const bytes = Buffer.from(r.data, 'base64');
      writeFileSync(out, bytes);
      return { bytes: bytes.length };
    },
    async hookClipboard() {
      await page.ev(`(() => { window.__copied = []; try { const o = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText.bind(navigator.clipboard) : null; if (navigator.clipboard) navigator.clipboard.writeText = (t) => { window.__copied.push(String(t)); return o ? o(t).catch(()=>{}) : Promise.resolve(); }; } catch (e) {} document.addEventListener('copy', (e) => { const s = String(window.getSelection()); window.__copied.push('[copy-event] ' + s); }, true); return true; })()`);
    },
    async copied() { return page.ev('window.__copied'); },
    async close() { try { await conn.send('Target.closeTarget', { targetId }); } catch {} },
  };
  try { await conn.send('Browser.grantPermissions', { permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'] }); } catch {}
  return page;
}

// ---------------------------------------------------------------- the command line

// --name value pairs and bare --flags
export function parseArgs(argv, { flags = [] } = {}) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) throw new Error(`unexpected argument ${a}`);
    const k = a.slice(2);
    if (flags.includes(k)) { out[k] = true; continue; }
    if (i + 1 >= argv.length) throw new Error(`${a} needs a value`);
    out[k] = argv[++i];
  }
  return out;
}
