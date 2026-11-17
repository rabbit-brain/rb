// Chrome over the DevTools protocol, for the app harness (app.mjs) and the smoke and screenshot runners.
// Node 22 or later: the protocol is spoken over node's global WebSocket, which node 18 lacks. Each Chrome gets its
// own free port and its own profile in a temporary folder; every process started here is stopped before node exits,
// on success, failure, timeout or a signal (run() below), and its folder is removed.
import { spawn, spawnSync } from 'node:child_process';
import { accessSync, constants, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir, userInfo } from 'node:os';
import { basename, delimiter, join } from 'node:path';

export const sleep = ms => new Promise(r => setTimeout(r, ms));

// the half of a walk this run takes (RB_APP_VIEWPORT, tests/app/test_app.py's viewport switch, so two Chrome slots each
// walk one): 390 takes the widths under 960, where the app is one column and a card a sheet; 1280 those from 960 up,
// where the card stands beside the work. Unset, every width. A block a runner walks at one width, or that starts at
// one and resizes across 960, is that width's
export const HALF = process.env.RB_APP_VIEWPORT || '';
if (!['', '390', '1280'].includes(HALF)) { process.stderr.write(`RB_APP_VIEWPORT is 390 or 1280, or unset; not ${JSON.stringify(HALF)}\n`); process.exit(2); }
export const inHalf = w => !HALF || (HALF === '390' ? w < 960 : w >= 960);
export const widths = ws => ws.filter(inHalf);

// how many times slower than this machine every page runs (Emulation.setCPUThrottlingRate): RB_APP_CPU_THROTTLE, a
// number from 1, or unset
export const CPU_THROTTLE = Number(process.env.RB_APP_CPU_THROTTLE || 1);
if (!(CPU_THROTTLE >= 1)) { process.stderr.write(`RB_APP_CPU_THROTTLE is a number from 1, or unset; not ${JSON.stringify(process.env.RB_APP_CPU_THROTTLE)}\n`); process.exit(2); }
// how long each animation or transition a page starts holds at its first frame, in ms (RB_APP_FRAME_STALL, round
// mac-tabs): the page's animation clock is held (Animation.setPlaybackRate 0) from when Chrome starts one, and runs
// again that long after. GitHub's macOS runner drew a sheet at its start for over 300 ms while the walk read its box
// (a tab pressed at 499 px in a 390 px sheet); a slowed CPU alone never holds it so. Unset, or 0, no hold
export const FRAME_STALL = Number(process.env.RB_APP_FRAME_STALL || 0);
if (!(FRAME_STALL >= 0)) { process.stderr.write(`RB_APP_FRAME_STALL is a number of ms from 0, or unset; not ${JSON.stringify(process.env.RB_APP_FRAME_STALL)}\n`); process.exit(2); }
// how long after a document starts its first animation frames come, in ms (RB_APP_FIRST_FRAME, round r61r): each
// requestAnimationFrame the page asks for before then runs then, as on GitHub's macOS runner, whose first page in a new
// Chrome drew its first frame after the walk had read the page as ready (the guide, started on that frame, opened after
// the walk had put it away, and took the walk's Esc). Unset, or 0, frames come as the machine draws them
export const FIRST_FRAME = Number(process.env.RB_APP_FIRST_FRAME || 0);
if (!(FIRST_FRAME >= 0)) { process.stderr.write(`RB_APP_FIRST_FRAME is a number of ms from 0, or unset; not ${JSON.stringify(process.env.RB_APP_FIRST_FRAME)}\n`); process.exit(2); }
// the machine's own display settings, set on Chrome (RB_APP_MACHINE, round media-1): a comma list of reduced-motion,
// dark and high-contrast, each a Chrome switch that stands for that setting on the system, as GitHub's macOS image has
// reduced motion and transparency turned on. A walk then reads what the machine says wherever a page's emulated media
// leaves a feature to it; media() below sends every one, so none should
export const MACHINE_SWITCHES = { 'reduced-motion': '--force-prefers-reduced-motion', dark: '--force-dark-mode', 'high-contrast': '--force-high-contrast' };
export const MACHINE = (process.env.RB_APP_MACHINE || '').split(',').map(x => x.trim()).filter(Boolean);
for (const m of MACHINE) if (!MACHINE_SWITCHES[m]) { process.stderr.write(`RB_APP_MACHINE is a comma list of ${Object.keys(MACHINE_SWITCHES).join(', ')}; not ${JSON.stringify(m)}\n`); process.exit(2); }
// the media features a machine can set on its own, and what each reads where nothing asks otherwise: a page's emulated
// media sends every one, so no walk reads the machine's settings (round media-1: GitHub's macOS runners turn motion
// down, and a guide that should glide read as still there)
export const FEATURE_DEFAULTS = { motion: ['prefers-reduced-motion', 'no-preference'], contrast: ['prefers-contrast', 'no-preference'],
  forcedColors: ['forced-colors', 'none'], transparency: ['prefers-reduced-transparency', 'no-preference'] };
// Emulation.setEmulatedMedia's features for a colour scheme and, each given or its default, the machine's features
export function mediaFeatures({ scheme = 'light', ...asked } = {}) {
  for (const k of Object.keys(asked)) if (!FEATURE_DEFAULTS[k] && asked[k] !== undefined) throw new Error(`media(): no feature ${k}; one of scheme, ${Object.keys(FEATURE_DEFAULTS).join(', ')}`);
  return [{ name: 'prefers-color-scheme', value: scheme },
    ...Object.entries(FEATURE_DEFAULTS).map(([k, [name, value]]) => ({ name, value: asked[k] ?? value }))];
}

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

// the process ids of each process running that holds `name` in its command line, this one aside
export function holderPids(name, { platform = process.platform } = {}) {
  const [cmd, args] = platform === 'win32'
    ? ['powershell', ['-NoProfile', '-NonInteractive', '-Command', 'Get-CimInstance Win32_Process | ForEach-Object { "$($_.ProcessId) $($_.CommandLine)" }']]
    : ['ps', ['-A', '-ww', '-o', 'pid=,command=']];
  const r = spawnSync(cmd, args, { encoding: 'utf8', windowsHide: true, timeout: 60000 });
  if (r.status !== 0 || typeof r.stdout !== 'string') return [];
  return r.stdout.split(/\r?\n/).filter(l => l.includes(name)).map(l => Number(l.trim().split(/\s+/)[0]))
    .filter(pid => Number.isInteger(pid) && pid > 0 && pid !== process.pid);
}

// Chrome's folder, removed once no process started with its profile runs, for `ms` at most: a helper can still write into
// the profile (its cache's index) as it ends, after Chrome itself, and would leave the folder behind. One still running
// then is stopped: a Chrome its first process handed its start to (Windows' chrome.exe does) is no child of this one,
// and nothing else would end it. Synchronous, so node's exit handler can use it
function removeFolder(dir, ms = 10000) {
  const until = Date.now() + ms;
  while (holders(basename(dir)).length && Date.now() < until) nap(100);
  for (const pid of holderPids(basename(dir))) { try { process.kill(pid, 'SIGKILL'); } catch {} }
  if (holderPids(basename(dir)).length) nap(500);
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
    this.sent = new Map();      // each method asked for: the params it was last sent with
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
    this.sent.set(method, params);
    if (this.closed) return Promise.reject(new Error(`${method}: the browser closed its connection`));
    return new Promise((resolve, reject) => {
      const id = ++this.id;
      this.pending.set(id, { resolve, reject, method });
      this.sock.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
}

// Chrome's environment. On Linux and macOS a home of its own, so nothing it writes lands outside its folder. On Windows
// the account's own profile folder as USERPROFILE, whatever the caller's says: a runner given a stand-in home (the
// tests give rb one beside the record) leaves Chrome no AppData there, so it cannot find its default data folder, and
// Chrome refuses remote debugging when it cannot tell that folder from its own ("DevTools remote debugging requires a
// non-default data directory"), whatever --user-data-dir says. os.userInfo() reads the account's folder from the
// system, not from the environment. `account` and `platform` are given by a test, to check the rule on any system.
export function chromeEnv({ home, platform = process.platform, env = process.env, account } = {}) {
  if (platform !== 'win32') return { ...env, HOME: home, XDG_CONFIG_HOME: join(home, '.config'), XDG_CACHE_HOME: join(home, '.cache') };
  if (account === undefined) { try { account = userInfo().homedir; } catch { account = null; } }
  // Windows reads its variables whatever their case: one USERPROFILE, in the case it was given
  const name = Object.keys(env).find(k => k.toUpperCase() === 'USERPROFILE') || 'USERPROFILE';
  return account ? { ...env, [name]: account } : { ...env };
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
  for (const m of MACHINE) args.push(MACHINE_SWITCHES[m]);
  args.push('about:blank');
  const env = chromeEnv({ home });
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
    // an exit 0 hands the start to another Chrome on this profile (Windows' chrome.exe does), which writes the port
    if (!alive(proc) && proc.exitCode !== 0) throw new Error(`Chrome ended on starting (exit ${proc.exitCode ?? proc.signalCode}): ${said.trim().slice(-2000)}`);
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
  return { chrome, version: version.product, proc, page: () => openPage(conn), asked: method => conn.sent.has(method),
    lastSent: method => conn.sent.get(method) };
}

// what moves an element: each animation or transition still running, on it or on an element it sits in, that can
// move it (one of colour or opacity alone cannot; a keyframe's own offset, easing and composite say when and how, not
// what changes), and that ends (a spinner is not waited for); evaluated in the page, the element as its argument
const MOVING = `e => { const on = [];
  const moves = k => /^(transform|translate|rotate|scale|left|top|right|bottom|inset|margin|padding|width|height|min|max|flex|grid|fontSize|lineHeight|zoom|visibility|display)/.test(k);
  for (let x = e; x; x = x.parentElement) for (const a of x.getAnimations()) {
    const t = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming() : {};
    const keys = a.effect && a.effect.getKeyframes ? a.effect.getKeyframes().flatMap(f => Object.keys(f)).filter(k => !['offset', 'computedOffset', 'easing', 'composite'].includes(k)) : [];
    if ((a.playState === 'running' || a.pending) && t.endTime !== Infinity && (!keys.length || keys.some(moves)))
      on.push(x.tagName.toLowerCase() + (x.id ? '#' + x.id : '') + ' ' + (a.animationName || a.transitionProperty || a.constructor.name));
  } return on; }`;

// where a press on an element lands: on its first line box (a link that wraps is clicked where its box begins; its
// whole box's left edge may be another element's), and only on the part of it the viewport and every box that clips it
// show, so a target half out of view is pressed where it shows (round r19: one half off the viewport was pressed
// outside it); `shown` false where none of it is in view, and the point then its box's own, which is not pressed.
// What clips it is asked of the browser, never worked out from CSS: an IntersectionObserver's intersectionRect is the
// box clipped by the viewport and by each box that clips it for layout, so an overflow box it escapes (placed
// absolutely by a box above it, or fixed) does not (round r19b: r-19 clipped by every overflow box above it, and the
// Files sheet at 390, placed by the nav under an overflow-hidden list, was never pressed). elementFromPoint would ask
// too much: a cover over the target is a press that lands on the cover and says so, not a target out of view. Read
// at the next frame; a page that draws no frame in 2 s, or whose scripts are off (no observer's callback nor timer
// runs there: the second argument), is clipped by the viewport alone. Evaluated in the page, the element as its first
// argument; it gives a promise
const PRESS_POINT = `async (e, off) => { const r = e.getClientRects()[0];
  let l = Math.max(r.left, 0), t = Math.max(r.top, 0), rt = Math.min(r.right, innerWidth), b = Math.min(r.bottom, innerHeight);
  const seen = off ? null : await new Promise(res => { const o = new IntersectionObserver(es => { o.disconnect(); res(es[es.length - 1]); });
    o.observe(e); setTimeout(() => { o.disconnect(); res(null); }, 2000); });
  if (seen) { const q = seen.intersectionRect, f = e.getClientRects()[0] || r;
    l = Math.max(f.left, q.left); t = Math.max(f.top, q.top); rt = Math.min(f.right, q.right); b = Math.min(f.bottom, q.bottom);
    if (!seen.isIntersecting) rt = l; }
  const shown = rt > l && b > t, s = shown ? null : e.getClientRects()[0] || r;
  return shown ? { x: l + Math.min((rt - l) / 2, 30), y: (t + b) / 2, shown } : { x: s.x + Math.min(s.width / 2, 30), y: s.y + s.height / 2, shown }; }`;

// an element as a failure names it: its tag, id and first words
const NAMED = `e => e.tagName + (e.id ? '#' + e.id : '') + ' ' + (e.textContent || '').trim().slice(0, 40)`;

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
  // a slow runner, emulated (RB_APP_CPU_THROTTLE, round cdp-1): every page's CPU slowed by that factor, so a walk reads
  // a transition as GitHub's macOS and Windows runners do, earlier in its course. Unset, or 1, the machine's own speed
  if (CPU_THROTTLE > 1) await send('Emulation.setCPUThrottlingRate', { rate: CPU_THROTTLE });
  if (FRAME_STALL > 0) {
    // the page's animation clock held (Animation.setPlaybackRate 0) from when Chrome starts an animation, its start
    // time then known, and run again that long after: each still reads as running, drawn where the clock stopped, and
    // goes on from there. Held from when one is made, while its start was pending, a transition got a start far in the
    // future once the clock ran again, so it never moved; paused one by one (Animation.setPaused) it reads as paused,
    // which the runner's did not; sought back to its start every 16 ms its box moved a frame's worth between reads
    let held = false;
    conn.on(m => {
      if (m.sessionId !== sessionId || m.method !== 'Animation.animationStarted' || held) return;
      held = true;
      send('Animation.setPlaybackRate', { playbackRate: 0 }).catch(() => {});
      setTimeout(() => { send('Animation.setPlaybackRate', { playbackRate: 1 }).catch(() => {}).finally(() => { held = false; }); }, FRAME_STALL);
    });
    await send('Animation.enable');
  }
  if (FIRST_FRAME > 0) {
    // each document's frames held until FIRST_FRAME ms after it starts: a frame asked for before then is asked for
    // again then, and cancelling it cancels both
    await send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => { const t0 = performance.now(), raf = window.requestAnimationFrame.bind(window),
      caf = window.cancelAnimationFrame.bind(window), held = new Map(); let n = 0;
      window.requestAnimationFrame = cb => { const wait = t0 + ${FIRST_FRAME} - performance.now(); if (wait <= 0) return raf(cb);
        const id = 'held' + (++n), h = { raf: 0 }; h.timer = setTimeout(() => { h.raf = raf(t => { held.delete(id); cb(t); }); }, wait); held.set(id, h); return id; };
      window.cancelAnimationFrame = id => { const h = held.get(id); if (!h) return caf(id); clearTimeout(h.timer); if (h.raf) caf(h.raf); held.delete(id); }; })()` });
  }
  let media = { type: '', scheme: 'light' }, scriptOff = false;
  // the presses since the page was last loaded that landed on something other than their target, or were not made
  // since their target was out of view, each as a failure names it: a step that fails later says them (round r19: a
  // tab's press that hit another tab was pressed again and passed, and the walk failed two steps on, naming that step)
  const misses = [];

  // every page starts with its media said whole: a page no walk sets media on reads no machine setting either
  await send('Emulation.setEmulatedMedia', { media: '', features: mediaFeatures() });

  const page = {
    send, errors, consoleMsgs,
    loading: () => [...inflight.values()],
    async viewport(width, height = 900) {
      await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 600 });
    },
    // the media type ('' for screen, 'print') and the colour scheme, kept from the call before where not given, and
    // every feature a machine can set on its own (FEATURE_DEFAULTS), each as asked in this call or else its default:
    // `motion: 'reduce'` holds for that call alone, and a call without it sends no-preference, never the machine's
    async media({ type = media.type, scheme = media.scheme, ...features } = {}) {
      const sent = mediaFeatures({ scheme, ...features });
      media = { type, scheme };
      await send('Emulation.setEmulatedMedia', { media: type, features: sent });
    },
    // the guide marked as seen (rb.guide.1 ended) in every document this page loads from now, before the page's own
    // DOMContentLoaded listeners run: the guide decides at DOMContentLoaded whether to start and starts on the next frame, so marking it
    // once the page reads ready leaves a window where it starts after the walk put it away (round r61r, GitHub's
    // macOS: its first frame came late, the guide opened over the walk and took its Esc). It gives back a function
    // that stops it for the documents loaded after, for a walk that then shows the guide on purpose
    async guideSeen() {
      // (marked at DOMContentLoaded, by a listener added before the page's own, and only where it is not set: storage
      // written as the document starts lost what the document before it had just stored, the theme a walk set and read
      // back after a reload, 10 of 30 times in round r61s)
      const { identifier } = await send('Page.addScriptToEvaluateOnNewDocument', { source: "document.addEventListener('DOMContentLoaded', () => { try { if (localStorage.getItem('rb.guide.1') === null) localStorage.setItem('rb.guide.1', 'ended'); } catch (e) {} }, { capture: true, once: true });" });
      let on = true;   // (stopped once: a second call does nothing)
      return async () => { if (on) { on = false; await send('Page.removeScriptToEvaluateOnNewDocument', { identifier }); } };
    },
    async noScript(v) { await send('Emulation.setScriptExecutionDisabled', { value: v }); scriptOff = !!v; },
    // loads `url`; a page that cannot be loaded, or does not finish loading in `timeoutMs`, is an error
    async goto(url, { timeoutMs = 30000 } = {}) {
      misses.length = 0;
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
    // `expr`'s value once it is truthy; at `timeoutMs`, null, or, given the `step` it waits in, an error that names the
    // step, what `expr` last gave and, given `state`, what that expression reads of the page then
    async waitFor(expr, timeoutMs = 30000, step = null, state = null) {
      const until = Date.now() + timeoutMs;
      for (;;) {
        const v = await page.ev(expr);
        if (v && !v.__error) return v;
        if (Date.now() > until) {
          if (!step) return null;
          const seen = state ? await page.ev(state) : undefined;
          throw new Error(`${step}: not so after ${timeoutMs / 1000} s (last ${JSON.stringify(v)})` + (state ? `; the page then: ${JSON.stringify(seen)}` : '')
            + (misses.length ? `; before it, ${misses.join('; ')}` : ''));
        }
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
    // until the element `selectorExpr` evaluates to has stopped: the first frame drawn after the act that moves it,
    // then no animation or transition running on it or on any element it sits in that can move it (one of colour or
    // opacity alone cannot, and one that never ends, a spinner, is not waited for), then its box the same across two
    // frames (round mac-tabs: GitHub's macOS runner drew a sheet at its start for over 300 ms, so three equal reads
    // 50 ms apart took a sheet that had not started moving for one that had stopped, and the press went to 499 px in a
    // 390 px sheet). Where no frame is drawn (the page's scripts off, round cdp-1, or a page Chrome draws no frame for)
    // equal reads 50 ms apart stand in for the frames. `timeoutMs` at most. It says whether the target stopped, whether
    // it is still there, what still ran at the end, whether frames were drawn, and how long it waited
    async stopped(selectorExpr, { timeoutMs = 10000 } = {}) {
      const t0 = Date.now(), until = t0 + timeoutMs;
      let frames = !scriptOff;
      const frame = async () => {
        if (!frames) { await sleep(50); return; }
        const r = await Promise.race([page.ev('new Promise(r => requestAnimationFrame(() => r(true)))'), sleep(2000).then(() => 'late')]);
        if (r === 'late') frames = false;
      };
      const running = `(() => { const e = (${selectorExpr}); if (!e) return null; return (${MOVING})(e); })()`;
      const box = `(() => { const e = (${selectorExpr}); if (!e) return null; const r = e.getClientRects()[0] || e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].join(); })()`;
      const said = (stopped, on) => ({ stopped, gone: on === null, running: on || [], frames, ms: Date.now() - t0 });
      await frame();
      let on = await page.ev(running);
      while (on && on.length && Date.now() < until) { await frame(); on = await page.ev(running); }
      if (on === null) return said(false, null);
      // the same box across two frames: three reads, each after a frame is drawn (four 50 ms apart where none is)
      let was = await page.ev(box), same = 0;
      while (same < (frames ? 2 : 3) && Date.now() < until) {
        await frame();
        const now = await page.ev(box);
        if (now === null) return said(false, null);
        same = now === was ? same + 1 : 0; was = now;
      }
      return said(same >= (frames ? 2 : 3), on);
    },
    // a real mouse click on the element `selectorExpr` evaluates to, once it has stopped (stopped() above), at where it
    // is then; null when there is none, or when it went before it could be pressed. Given `until`, an expression for
    // the state the click brings: when that has not come `againMs` after the press, the target is still shown, and its
    // box has moved since, it is pressed once more where it stopped this time, unless the state comes while it settles
    // or in half a second after. Only for a click that a second one cannot undo (a tab, a link): a toggle's slow state
    // would be flipped back. It says each press (`presses`: where, what was under the point, how its wait ended) and in
    // `said` where each was and any wait that did not end in a stop; the last press's point as `x`, `y` and `at`
    async clickEl(selectorExpr, { until = null, againMs = 3000 } = {}) {
      const r = await page.rect(selectorExpr);
      if (!r || r.__error) return null;
      const presses = [];
      const point = `(async () => { const e = (${selectorExpr}); if (!e || !e.getClientRects().length || !e.checkVisibility()) return null;
        const p = await (${PRESS_POINT})(e, ${scriptOff}); return {x: p.x, y: p.y}; })()`;
      const pressOnce = async () => {
        const t0 = Date.now(), settled = await page.stopped(selectorExpr);
        // where it shows (PRESS_POINT), read now, after it stopped, never before
        let at = settled.gone ? null : await page.ev(point);
        // what lies over the point and is not the target (a sheet sliding away over a button that never moved) is
        // waited for too, while it still moves, within the same 10 s
        const over = p => `(() => { const e = (${selectorExpr}), h = document.elementFromPoint(${p.x}, ${p.y}); if (!e || !h || e.contains(h) || h.contains(e)) return [];
          return (${MOVING})(h); })()`;
        for (const until = Date.now() + Math.max(0, 10000 - settled.ms); at && !at.__error && Date.now() < until;) {
          const moving = await page.ev(over(at));
          if (!moving || moving.__error || !moving.length) break;
          settled.covered = moving;
          await sleep(50);
          at = await page.ev(point);
        }
        // read once more as the button goes down, in one evaluation: where the point is now, whether the viewport and
        // every box the target scrolls in still show any of it, and what is under the point. A target that moved since its
        // point was read (the page scrolled it between the read and the press), or that a scroll took out of its
        // scroller's view (clipped there, still drawn on the page, so a press lands on what is beside it), is brought into
        // view, waited for again and read again, within the same 10 s (round l1: a scroll between the read and the press
        // beat the read, and a target scrolled out of its scroller was pressed at its clipped place, y 0); one still out
        // of view then is not pressed (round r19: it was, outside the viewport)
        const last = `(async () => { const e = (${selectorExpr}); if (!e || !e.getClientRects().length || !e.checkVisibility()) return null;
          const { x, y, shown } = await (${PRESS_POINT})(e, ${scriptOff}), h = shown ? document.elementFromPoint(x, y) : null;
          return { x, y, shown, me: (${NAMED})(e), hit: h ? (${NAMED})(h) : null, on: !!h && (h === e || e.contains(h)) }; })()`;
        // the read and the press are two steps, and the page runs between them: a scroll that comes there (round r67a:
        // at a load of 26, a scroll set 150 ms after the click was asked for came after the last read and before the
        // press, which landed where the target had been). So the last read also notes, in the page, what the next
        // mousedown lands on; a press that landed on something else while the target moved away from its point is
        // said, and the target is read and pressed again where it is, within the same 10 s. A page whose scripts are
        // off runs no listener, notes nothing, and is pressed once, as before
        const lastNoting = `(async () => { const r = await ${last}; if (r && !r.__error) { const e = (${selectorExpr});
          if (window.__rbDownH) removeEventListener('mousedown', window.__rbDownH, true);
          window.__rbDown = null; window.__rbDownH = ev => { window.__rbDown = { on: !!e && (ev.target === e || e.contains(ev.target)), hit: (${NAMED})(ev.target) }; };
          addEventListener('mousedown', window.__rbDownH, { capture: true, once: true }); } return r; })()`;
        const landed = `(() => { const d = window.__rbDown || null; if (window.__rbDownH) removeEventListener('mousedown', window.__rbDownH, true);
          window.__rbDownH = null; window.__rbDown = null; return d; })()`;
        for (;;) {
          let now = at && !at.__error ? await page.ev(lastNoting) : null;
          for (const until = t0 + 10000; now && !now.__error && (!now.shown || Math.abs(now.x - at.x) > 1 || Math.abs(now.y - at.y) > 1) && Date.now() < until;) {
            settled.again = (settled.again || 0) + 1;
            if (!now.shown) await page.ev(`(() => { const e = (${selectorExpr}); if (e) e.scrollIntoView({block:'center', inline:'nearest'}); return true; })()`);
            const s2 = await page.stopped(selectorExpr, { timeoutMs: Math.max(0, until - Date.now()) });
            if (s2.gone) { now = null; break; }
            at = await page.ev(point);
            now = at && !at.__error ? await page.ev(lastNoting) : null;
          }
          if (!at || at.__error || !now || now.__error) { await page.ev(landed); presses.push({ gone: true, settled }); return null; }
          const where = `${Math.round(now.x)}, ${Math.round(now.y)}`;
          if (!now.shown) {
            await page.ev(landed);
            presses.push({ x: now.x, y: now.y, unshown: true, settled });
            misses.push(`a click on ${now.me} was not pressed: ${where} was out of view for 10 s`);
            return null;
          }
          at = { x: now.x, y: now.y };
          let hit = now.hit, off = !now.on;
          // what the press lands on, when it is not the target: said in the press's own words and in a later failure's
          if (off) misses.push(`a click on ${now.me} pressed ${where} on ${hit || 'nothing'}`);
          for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x: at.x, y: at.y, button: 'left', clickCount: 1 });
          const down = await page.ev(landed), raced = !off && !!down && !down.__error && !down.on;
          if (raced) {
            off = true; hit = down.hit;
            misses.push(`a click on ${now.me} pressed ${where} on ${hit || 'nothing'}: the target moved as it was pressed`);
          }
          presses.push({ x: at.x, y: at.y, hit, off, settled });
          if (!raced || Date.now() >= t0 + 10000) return at;
          const moved = await page.ev(point);
          if (!moved || moved.__error || (Math.abs(moved.x - at.x) <= 1 && Math.abs(moved.y - at.y) <= 1)) return at;
          // moved since the press: read and pressed again where it is
          settled.again = (settled.again || 0) + 1;
          at = moved;
        }
      };
      const at = await pressOnce();
      if (!at) return null;
      if (until && !(await page.waitFor(until, againMs))) {
        // still shown and somewhere else: pressed there, unless the state came while it settled
        const now = await page.ev(point);
        if (now && !now.__error && (Math.abs(now.x - at.x) > 1 || Math.abs(now.y - at.y) > 1)) {
          // brought into view first where none of it shows (PRESS_POINT); one partly shown is pressed where it shows
          await page.ev(`(async () => { const e = (${selectorExpr}); if (!e || !e.getClientRects().length) return false;
            if (!(await (${PRESS_POINT})(e, ${scriptOff})).shown) e.scrollIntoView({block:'center', inline:'nearest'}); return true; })()`);
          const settled = await page.stopped(selectorExpr);
          if (!settled.gone && !(await page.waitFor(until, 500))) await pressOnce();
        }
      }
      await sleep(300);
      const last = presses.filter(p => !p.gone && !p.unshown).pop();
      r.first = { x: r.x, y: r.y };
      r.x = last.x; r.y = last.y; r.at = { x: last.x, y: last.y }; r.hit = last.hit;
      r.presses = presses;
      // each press, and what it landed on where that was not the target (round r19: a second press that passed hid a
      // first that hit another tab)
      r.said = presses.map(p => (p.gone ? 'nowhere: it went before the press'
          : p.unshown ? `nowhere: ${Math.round(p.x)}, ${Math.round(p.y)} was out of view for 10 s` : `${Math.round(p.x)}, ${Math.round(p.y)}`)
        + (p.off ? ` on ${p.hit || 'nothing'}, not the target` : '')
        + (!p.settled.stopped && !p.settled.gone ? ` (not still after ${Math.round(p.settled.ms / 1000)} s${p.settled.running.length ? ': ' + p.settled.running.join(', ') + ' running' : ''})` : '')).join(', then at ');
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

// what the system's clipboard holds once a page writes `text` there: on Windows Chrome writes each line ending as \r\n,
// as Windows' own text does (Blink's SystemClipboard::WritePlainText), and reads it back so; elsewhere the text as written
export function onTheClipboard(text, platform = process.platform) {
  return platform === 'win32' ? String(text).replace(/\r?\n/g, '\r\n') : String(text);
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
