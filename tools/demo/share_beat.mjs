#!/usr/bin/env node
// The demo's share beat (SPRINT-OCT24 6.2, 1:45): rb sync --watch in a terminal, a new run recorded, and the record's
// share link open in a fresh browser (a profile of its own: never signed in), which shows the new run once the watch
// has sent it. It runs where the hosted service is (the Mac, against staging or production), and writes the segment as
// cut.py keeps one: <out>/screen.mp4 (1920 x 1200, 30 fps, the terminal beside the page, as a time-lapse labelled with
// its speed), <out>/screen.marks.jsonl and <out>/segment.json. cut.py --share-segment <out> puts it in the cut.
//
//   node tools/demo/share_beat.mjs --rb <rb binary> --record <folder holding .rb/> --link <the record's share link>
//       --run '<the command that records the new run>' --out <dir> [--since '<words>'] [--until '<words>']
//       [--chrome <path>] [--python <a python with Pillow>] [--window 9] [--hub <address>] [--actor human:ana]
//
// - The commands run for real, in the record's folder, with the rb given first on PATH and no agent-session variable;
//   rb sync --watch uses the credential rb login keeps (or RB_HUB_TOKEN), and --hub (RB_HUB_URL) only for a stand-in.
//   What they print is kept with its time (an asciinema v2 cast) and played in term.html.
// - The record is synced once before filming, so the link shows it as it stands; then the watch, the run, the change.
// - The link is filmed as a reader opens it: the hosted service's page, its bar over rb's page in a sandboxed frame. Every
//   check of the page's words reads them inside that frame (walk.mjs's "frame" steps; a page with no frame is read itself).
// - The page must say "<N> recorded runs" (--since; the count as rb's page header draws it) and not "<N + 1> recorded
//   runs" (--until) before the beat, N the runs rb status counts before the new one, and the second after it. It is
//   given 20 s to show it on its own after the watch's second sync, then reloaded whole, the bar too, as a reader would;
//   segment.json says which, and where the words were read (read_in).
// - Both sides are filmed in real time on one clock and played together `speed` times as fast, so the beat lasts
//   about --window seconds; term.html's label says the speed.
// - The page is laid out as a reader's desktop browser lays it out, 1440 CSS px wide (rb's three panes, navigator |
//   working view | inspector, need 1280), and scaled into its slot beside the terminal: the terminal keeps its size,
//   the page takes the rest of the 1920 x 1200 frame. Its frame must show the inspector before the beat and after it;
//   segment.json keeps what was read (layout).

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync, appendFileSync, readFileSync, existsSync, rmSync, copyFileSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] !== undefined && !all[i + 1].startsWith("--") ? all[i + 1] : true]);
  return acc;
}, []));
const need = ["rb", "record", "link", "run", "out"].filter(k => typeof args[k] !== "string");
if (need.length) { console.error(`share_beat: give ${need.map(k => "--" + k).join(", ")}`); process.exit(2); }
const rb = resolve(args.rb), record = resolve(args.record), out = resolve(args.out);
if (!existsSync(rb)) { console.error(`share_beat: no rb at ${rb}`); process.exit(2); }
if (!existsSync(join(record, ".rb"))) { console.error(`share_beat: ${record} holds no .rb/`); process.exit(2); }
if (!/^https?:\/\//.test(args.link)) { console.error(`share_beat: --link must be a web address: ${args.link}`); process.exit(2); }
const python = args.python || "python3";
const windowS = Number(args.window || 9);
const chrome = args.chrome || (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : "google-chrome");
const OUT = { w: 1920, h: 1200 }, H = 900;
const TERM = { w: 540, cols: 58, rows: 40, font: 13 };
// the slots in the frame: the terminal at its own shape, the page the rest; the page's viewport is its slot's shape at
// 1440 CSS px wide, captured at 1.5 device pixels to the CSS pixel and scaled down into the slot
const SLOT = { term: Math.round(TERM.w * OUT.h / H) }; SLOT.page = OUT.w - SLOT.term;
const PANES = 1280;   // open.css: from 1280 CSS px rb's page draws navigator | working view | inspector
const PAGE = { w: 1440, scale: 1.5 }; PAGE.h = Math.round(PAGE.w * OUT.h / SLOT.page);
const TITLE = record.split("/").pop();
const PROMPT = `${TITLE}$ `;

for (const d of ["files", "term", "page"]) mkdirSync(join(out, d), { recursive: true });
const flag = join(out, ".synced");
rmSync(flag, { force: true });

// ---- a clean terminal, as the researcher's own: only what a login shell has, none of the variables an agent
// session or a CI job leaves (whatever started this), the given rb first on PATH, the researcher named as the demo
// record names them, telemetry off
const KEEP = ["HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "RB_HUB_TOKEN", "RB_HUB_URL", "RB_CONFIG_DIR",
  "RB_TEST_SYNC_PACE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "SSL_CERT_FILE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
  "http_proxy", "https_proxy", "no_proxy"];
const env = Object.fromEntries(KEEP.filter(k => process.env[k] !== undefined).map(k => [k, process.env[k]]));
env.PATH = [dirname(rb), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"].join(":");
Object.assign(env, { TERM: "xterm-256color", COLUMNS: String(TERM.cols), RB_ACTOR: args.actor || "human:ana", RB_TELEMETRY: "0", DO_NOT_TRACK: "1" });
if (args.hub) { env.RB_HUB_URL = args.hub; env.no_proxy = "127.0.0.1,localhost"; }

// ---- how many runs the record holds before the new one: the page shows one more once it has it
function runCount() {
  const r = spawnSync(rb, ["status", "--json"], { cwd: record, env, encoding: "utf8", maxBuffer: 64 << 20 });
  try { return JSON.parse(r.stdout).data.counts.run_count; } catch { throw new Error(`rb status --json said no run count: ${(r.stderr || "").slice(-400)}`); }
}
const before = runCount();
// what the page says before the new run and after it: the runs as rb's page header counts them (strings.rs's
// RECORDED_RUNS_N, "1 recorded run", "22 recorded runs") as a whole count, so "21 recorded runs" is not "1 recorded
// run"; --since and --until give other words. The page must say the first and not the second before the beat, and the
// second once the watch has sent the run: a page that never changes, or another record's, is no beat
const RECORDED_RUNS_N = ["recorded run", "recorded runs"];
const count = n => `${n} ${RECORDED_RUNS_N[n === 1 ? 0 : 1]}`;
const since = typeof args.since === "string" ? args.since : count(before);
const until = typeof args.until === "string" ? args.until : count(before + 1);
const esc = w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const says = words => `(!!document.body && new RegExp(${JSON.stringify("(^|[^0-9.])" + esc(words) + "(?![0-9A-Za-z])")}).test(document.body.innerText.replace(/\\s+/g, " ")))`;
const shows = says(until);
// the page's layout, read inside its frame: the viewport rb's page is laid out in, and whether its inspector is drawn
const LAYOUT = `(() => { const a = document.getElementById("insp"), b = a && a.getBoundingClientRect(), c = a && getComputedStyle(a);
  return { width: innerWidth, height: innerHeight,
    inspector: !!a && c.display !== "none" && c.visibility !== "hidden" && b.width > 0 && b.height > 0 && b.left < innerWidth && b.right > 0 }; })()`;
const panes = `(l => l.width >= ${PANES} && l.inspector)(${LAYOUT})`;
const before_ = `${says(since)} && !${says(until)}`;

// ---- the link shows the record as it is before the beat: one sync first, not filmed (the link exists only once the
// record has been synced; this makes sure it shows the runs before the new one)
{
  const r = spawnSync(rb, ["sync"], { cwd: record, env, encoding: "utf8" });
  if (r.status !== 0) { console.error(`share_beat: rb sync failed before the beat:\n${(r.stdout + r.stderr).slice(-800)}`); process.exit(1); }
}

// ---- the page: a fresh browser at the share link, filmed from its first frame to the change
const pageBeats = { width: PAGE.w, height: PAGE.h, scale: PAGE.scale, beats: [{ name: "page", steps: [
  { goto: args.link }, { waitFor: "document.readyState === 'complete'" }, { waitFor: before_, frame: true, timeout: 30000 }, { mark: "words" },
  { print: LAYOUT, frame: true, keep: "before" }, { waitFor: panes, frame: true, timeout: 5000 }, { wait: 800 }, { mark: "ready" },
  { waitFile: flag, timeout: 600000 }, { mark: "sent" },
  { reloadUntil: shows, frame: true, live: 20000, timeout: 30000 }, { print: LAYOUT, frame: true, keep: "after" },
  { waitFor: panes, frame: true, timeout: 5000 }, { wait: 2500 }] }] };
writeFileSync(join(out, "page", "beats.json"), JSON.stringify(pageBeats, null, 1));
const walk = spawn(process.execPath, [join(here, "walk.mjs"), "--script", join(out, "page", "beats.json"), "--out", join(out, "page", "walk"),
  "--chrome", chrome], { stdio: ["ignore", "inherit", "inherit"] });
const walkDone = new Promise(r => walk.on("exit", code => r(code)));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const walkJson = () => { try { return JSON.parse(readFileSync(join(out, "page", "walk", "walk.json"), "utf8")); } catch { return {}; } };
const noPanes = when => {
  const l = (walkJson().kept || {})[when];
  return `the share link's page does not show rb's three panes ${when} the beat: its frame is laid out ${l ? `${l.width} CSS px wide, the inspector ${l.inspector ? "drawn" : "not drawn"}` : "in a way the walk could not read"} (rb draws navigator | working view | inspector from ${PANES} CSS px; the page is filmed at ${PAGE.w})`;
};
const marksOf = dir => existsSync(join(dir, "marks.jsonl")) ? readFileSync(join(dir, "marks.jsonl"), "utf8").split("\n").filter(Boolean).map(l => JSON.parse(l)) : [];

let pageT0 = null;  // the wall time of the page walk's clock zero
for (const end = Date.now() + 120000; pageT0 === null;) {
  const m = marksOf(join(out, "page", "walk")).find(x => x.label === "ready");
  if (m) pageT0 = Date.now() / 1000 - m.t;
  else if (walk.exitCode !== null && marksOf(join(out, "page", "walk")).some(x => x.label === "words")) { console.error(`share_beat: ${noPanes("before")}`); process.exit(1); }
  else if (walk.exitCode !== null) { console.error(`share_beat: the share link's page never said "${since}" without "${until}" (the record before the new run, read inside the page's frame where it has one): is it this record's link?`); process.exit(1); }
  else if (Date.now() > end) { console.error("share_beat: the share link's page never got ready"); process.exit(1); }
  else await sleep(50);
}

// ---- the terminal: each command typed at the prompt and run for real, what it prints kept with its time
const castPath = join(out, "files", "share.cast");
const wall0 = Date.now() / 1000;
writeFileSync(castPath, JSON.stringify({ version: 2, width: TERM.cols, height: TERM.rows, timestamp: Math.round(wall0), title: TITLE }) + "\n");
const emit = (kind, data) => appendFileSync(castPath, JSON.stringify([Math.round((Date.now() / 1000 - wall0) * 1000) / 1000, kind, data]) + "\n");
const say = text => emit("o", text.replace(/\r?\n/g, "\r\n"));
let printed = "";
async function typeLine(cmd) {
  for (const ch of cmd) { emit("o", ch); await sleep(45); }
  emit("o", "\r\n");
}
function run(cmd, background) {
  const child = spawn("/bin/sh", ["-c", cmd], { cwd: record, env, stdio: ["ignore", "pipe", "pipe"], detached: background });
  const take = d => { const s = d.toString(); printed += s; say(s); };
  child.stdout.on("data", take); child.stderr.on("data", take);
  return child;
}
const synced = () => (printed.match(/Synced at [^\n]*privately|Synced, privately/g) || []).length;
const waitUntil = async (test, ms, what) => {
  for (const end = Date.now() + ms; !test();) { if (Date.now() > end) throw new Error(`${what} within ${ms / 1000} s:\n${printed.slice(-1500)}`); await sleep(100); }
};

let watch = null, failed = null;
try {
  say(PROMPT); await sleep(700);
  await typeLine("rb sync --watch &");
  watch = run("exec rb sync --watch", true);
  await waitUntil(() => synced() >= 1 || watch.exitCode !== null, 120000, "rb sync --watch sent nothing");
  if (watch.exitCode !== null) throw new Error(`rb sync --watch ended (${watch.exitCode}):\n${printed.slice(-1500)}`);
  await sleep(1500);
  say(PROMPT); await sleep(500);
  await typeLine(args.run);
  const recorded = run(args.run, false);
  const code = await new Promise(r => recorded.on("exit", r));
  if (code !== 0) throw new Error(`the new run was not recorded (exit ${code}):\n${printed.slice(-1500)}`);
  if (runCount() !== before + 1) throw new Error(`rb status counts ${runCount()} runs after the new one, not ${before + 1}`);
  say(PROMPT);
  // the watch sends the change at its next look, a minute at most after its first upload
  await waitUntil(() => synced() >= 2, 180000, "rb sync --watch did not send the new run");
  writeFileSync(flag, "");
} catch (e) { failed = e; writeFileSync(flag, ""); }
const pageCode = await walkDone;
emit("m", "end");
if (watch && watch.exitCode === null) { try { process.kill(-watch.pid, "SIGINT"); } catch { /* gone */ } }
await sleep(500);
if (failed) { console.error(`share_beat: ${failed.message}`); process.exit(1); }
const pageMarks = marksOf(join(out, "page", "walk"));
const how = pageMarks.find(m => m.label.startsWith("shown"));
if (pageCode !== 0 && how) { console.error(`share_beat: ${noPanes("after")}`); process.exit(1); }
if (pageCode !== 0) { console.error(`share_beat: the page never showed "${until}" (see ${join(out, "page", "walk")})`); process.exit(1); }
const { read_in: readIn = [], kept = {} } = walkJson();
const castEnd = Date.now() / 1000 - wall0;

// ---- the two sides on one clock, played `speed` times as fast
const span = (pageT0 + pageMarks.at(-1).t) - wall0;   // from the first prompt to the page's last frame
const speed = Math.max(1, Math.round(span / windowS));
const ok = (cmd, argv, opts = {}) => {
  const r = spawnSync(cmd, argv, { stdio: ["ignore", "inherit", "inherit"], ...opts });
  if (r.status !== 0) { console.error(`share_beat: ${cmd} ${argv.slice(0, 3).join(" ")} ... failed`); process.exit(1); }
};
const termBeats = { width: TERM.w, height: H, scale: 2, beats: [{ name: "term", steps: [
  { goto: `/demo/term.html?cast=/files/share.cast&speed=${speed}&font=${TERM.font}&lapse=bar&title=${encodeURIComponent(TITLE)}` },
  { wait: 300 }, { mark: "start" }, { play: true }, { wait: 300 }] }] };
writeFileSync(join(out, "term", "beats.json"), JSON.stringify(termBeats, null, 1));
ok(process.execPath, [join(here, "walk.mjs"), "--script", join(out, "term", "beats.json"), "--out", join(out, "term", "walk"), "--serve", join(out, "files"), "--chrome", chrome]);
for (const side of ["term", "page"]) ok(python, [join(here, "compose.py"), join(out, side, "walk"), join(out, side, "screen.mp4"), "--from", "0"]);
const termStart = marksOf(join(out, "term", "walk")).find(m => m.label === "start").t;   // compose --from 0: the walk's own clock
const pageFrom = wall0 - pageT0;            // the page video's time at the first prompt
const length = span / speed;
ok("ffmpeg", ["-loglevel", "error", "-y", "-ss", termStart.toFixed(3), "-i", join(out, "term", "screen.mp4"), "-ss", pageFrom.toFixed(3), "-i", join(out, "page", "screen.mp4"),
  "-filter_complex", `[0:v]fps=30,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=${length.toFixed(2)}[l];` +
  `[1:v]setpts=(PTS-STARTPTS)/${speed},fps=30,tpad=stop_mode=clone:stop_duration=1[r];` +
  `[l]scale=${SLOT.term}:${OUT.h}:flags=lanczos[ls];[r]scale=${SLOT.page}:${OUT.h}:flags=lanczos[rs];[ls][rs]hstack=inputs=2,format=yuv420p[v]`,
  "-map", "[v]", "-t", (length + 1.2).toFixed(3), "-r", "30", "-c:v", "libx264", "-crf", "18", "-movflags", "+faststart", join(out, "screen.mp4")]);
writeFileSync(join(out, "screen.marks.jsonl"), [{ t: 0, label: "beat share" }, { t: 0.4, label: "voice" }].map(m => JSON.stringify(m) + "\n").join(""));
const link = new URL(args.link);
writeFileSync(join(out, "segment.json"), JSON.stringify({
  id: "share", speed, real_seconds: Math.round(span * 10) / 10, seconds: Math.round((length + 1.2) * 100) / 100,
  runs_before: before, since, until, shown: how ? how.label : null, read_in: readIn, syncs: synced(), host: link.host,
  page: { css: [PAGE.w, PAGE.h], scale: PAGE.scale, slot: [SLOT.page, OUT.h] }, layout: { before: kept.before, after: kept.after },
  made: new Date().toISOString(), cast_seconds: Math.round(castEnd * 10) / 10,
}, null, 1) + "\n");
console.log(`share beat: ${join(out, "screen.mp4")} (${(length + 1.2).toFixed(1)} s, time-lapse ${speed}x of ${span.toFixed(0)} s; ${how ? how.label : ""})`);
