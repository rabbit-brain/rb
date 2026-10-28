#!/usr/bin/env node
// The demo's agent beats (follow-up 14): while an agent works in a terminal, rb open's page updates itself. One take
// films both on one clock: rb open's live page in Chrome (the whole frame, the Overview at 1440 CSS px across 1920 x
// 1200) and a Claude Code session in a pty (an asciinema v2 cast), then a fresh session asked where the research stands.
// agent_cut.py makes the two segments from the take: the page full frame, the terminal floating in front.
//
//   node tools/demo/agent_beat.mjs --rb <rb binary> --record <folder holding .rb/> --out <dir> --task '<one line>'
//       [--ask '<one line>'] [--runs N] [--study-bin <dir>] [--python <a python with pexpect and pyte>]
//       [--chrome <path>] [--port 8941] [--cpus 8-15] [--limit 150] [--no-fresh | --fresh-only] [--work <dir>] [--no-skill]
//       [--start <view's address>]
//
// - The record is copied into --work (~/agent-take by default: the agent's screen shows its path, so a short one
//   reads better) and everything runs on the copy, never in place.
// - rb open runs on the copy in a clean terminal (the researcher's, as human:ana), and the page is filmed from before the
//   agent is told anything until the fresh session has answered. Each time rb open's live module swaps a new version
//   of the page in, the walk keeps a "change" marker with the words it newly shows and where they are drawn
//   (walk.mjs's watch step): the cut's time jumps and the terminal's placement read them.
// - The agent session is told --task and chooses its commands; the driver types nothing else (agent_take.py). With
//   --runs N it ends once the record holds N runs and the agent has gone quiet. The fresh session is a new one in the
//   same copy, told --ask ("Where does this research stand?").
// - The sessions run on --cpus (taskset), so the study's runs keep to their cores.
// - Before filming, rb's skill is put in the copy's .claude/ as the researcher (rb skill add --claude --project), unless
//   it is there or --no-skill: a fresh session reads the record through rb only when told to. --fresh-only films the
//   fresh session alone (a record the agent beat has already changed).
// - The copy's .claude/settings.json turns Claude Code's spinner tips off (spinnerTipsEnabled false), kept with what
//   else it holds; the box's own settings are left alone.
// - --start x-e1 films the agent session on that view (experiment e1's, its comparison grid), then goes back to the
//   Overview for the fresh session (agent_page.mjs); without it both are filmed on the Overview. A view the page links
//   nowhere to is refused before an earlier take in --out is cleared.
// - Every state the copy passes through is kept (agent_states.mjs), so a later build can walk the page again on them.
// Output: <out>/page/walk (frames, marks), <out>/agent.cast(.json), <out>/fresh.cast(.json), <out>/states (the
// copy's states, states.jsonl), <out>/take.json.

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync, readFileSync, existsSync, rmSync, realpathSync, readdirSync } from "node:fs";
import { join, resolve, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";
import { pageSteps } from "./agent_page.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] !== undefined && !all[i + 1].startsWith("--") ? all[i + 1] : true]);
  return acc;
}, []));
const need = ["rb", "record", "out", ...(args["fresh-only"] ? [] : ["task"])].filter(k => typeof args[k] !== "string");
if (need.length) { console.error(`agent_beat: give ${need.map(k => "--" + k).join(", ")}`); process.exit(2); }
const rb = resolve(args.rb), source = resolve(args.record), out = resolve(args.out);
if (!existsSync(rb)) { console.error(`agent_beat: no rb at ${rb}`); process.exit(2); }
if (!existsSync(join(source, ".rb"))) { console.error(`agent_beat: ${source} holds no .rb/`); process.exit(2); }
if ((typeof args.task === "string" && /\n/.test(args.task)) || (typeof args.ask === "string" && /\n/.test(args.ask))) { console.error("agent_beat: --task and --ask are one line each"); process.exit(2); }
const ask = typeof args.ask === "string" ? args.ask : "Where does this research stand?";
const python = args.python || "python3";
const chrome = args.chrome || (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : "google-chrome");
const port = Number(args.port || 8941);
const cpus = typeof args.cpus === "string" ? args.cpus : null;
const limitMin = Number(args.limit || 150);
// the page across the whole frame: 1440 CSS px into 1920 x 1200, so 900 CSS px high at 4/3 device pixels
const FRAME = { w: 1920, h: 1200 }, PAGE = { w: 1440 };
PAGE.h = Math.round(PAGE.w * FRAME.h / FRAME.w); PAGE.scale = FRAME.w / PAGE.w;

// ---- the copy: never the record itself. Paths are compared as they really are (a link to the record is the record),
// and a folder this take clears is its own: --out an earlier take's or new, never one holding the record or rb, and
// the copy a folder of its own beside nothing else of theirs
const real = p => { let head = resolve(p), tail = ""; for (;;) { try { return join(realpathSync(head), tail); } catch { /* not there yet */ }
  const up = dirname(head); if (up === head) return resolve(p); tail = join(basename(head), tail); head = up; } };
const within = (a, b) => b === "/" || a === b || a.startsWith(b + "/");
const sourceReal = real(source), rbReal = real(rb), outReal = real(out);
const work = real(typeof args.work === "string" ? args.work : join(process.env.HOME || out, "agent-take")), record = join(work, basename(sourceReal));
for (const [name, dir] of [["--out", outReal], ["--work", record]]) {
  if (within(sourceReal, dir) || within(rbReal, dir) || within(dir, sourceReal)) { console.error(`agent_beat: ${name} ${dir} holds the record or rb, or sits inside the record`); process.exit(2); }
}
if (existsSync(outReal) && readdirSync(outReal).length && !existsSync(join(outReal, "take.json")) && !existsSync(join(outReal, "page"))) {
  console.error(`agent_beat: --out ${outReal} holds files that are no earlier take's: give a new folder`); process.exit(2);
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
const base = `http://127.0.0.1:${port}/`;

// ---- the page: the first view (--start's, else the Overview), its first-visit guide seen, watched until the agent
// session is done, then the Overview until the fresh session is
const flag = join(out, ".done"), agentFlag = join(out, ".agent-done");
if (args.start === true) { console.error("agent_beat: give --start a view's address (x-e1)"); process.exit(2); }
if (args.start && args["fresh-only"]) { console.error("agent_beat: --start films the agent session, which --fresh-only leaves out"); process.exit(2); }
const start = typeof args.start === "string" ? args.start : null;
let steps;
try { steps = pageSteps({ base, start, agentDone: agentFlag, done: flag, timeoutMs: (limitMin + 20) * 60000, fresh: !args["no-fresh"] }); }
catch (e) { console.error(`agent_beat: --start ${e.message.replace(/^agent_page: /, "")}`); process.exit(2); }
// a port that answers already is another rb open's: filming it would film another record
try { await fetch(base, { signal: AbortSignal.timeout(3000) }); console.error(`agent_beat: something answers at ${base} already: give another --port`); process.exit(2); }
catch (e) { if (e.name === "TimeoutError") { console.error(`agent_beat: something holds ${base} and does not answer: give another --port`); process.exit(2); } }
rmSync(record, { recursive: true, force: true });
mkdirSync(work, { recursive: true });
if (spawnSync("cp", ["-a", sourceReal, record]).status !== 0) { console.error(`agent_beat: could not copy ${sourceReal}`); process.exit(1); }
rmSync(join(record, ".rb", "cache", "sessions"), { recursive: true, force: true });
const runFiles = () => {
  const r = spawnSync("sh", ["-c", "ls .rb/experiments/*/runs/*.yaml 2>/dev/null | wc -l"], { cwd: record, encoding: "utf8" });
  return Number(r.stdout.trim());
};
const runsBefore = runFiles();

// ---- the researcher's clean terminal: rb's skill put in the project as they would put it (rb init, run in an agent
// session, installs nothing for later sessions and names this command), then rb open on the copy
const env = { HOME: process.env.HOME, USER: process.env.USER || "rb", PATH: [dirname(rb), "/usr/local/bin", "/usr/bin", "/bin"].join(":"),
  LANG: "C.UTF-8", RB_ACTOR: "human:ana", RB_TELEMETRY: "0", DO_NOT_TRACK: "1" };
if (!args["no-skill"] && !existsSync(join(record, ".claude", "skills", "rabbit-brain"))) {
  const r = spawnSync(rb, ["skill", "add", "--claude", "--project"], { cwd: record, env, encoding: "utf8" });
  if (r.status !== 0) { console.error(`agent_beat: rb skill add failed: ${(r.stdout + r.stderr).slice(-600)}`); process.exit(1); }
}
// no spinner tips in the filmed sessions ("Tip: Did you know ..."): the copy's own project settings, never the box's
const settingsFile = join(record, ".claude", "settings.json");
let settings = {};
if (existsSync(settingsFile)) {
  try { settings = JSON.parse(readFileSync(settingsFile, "utf8")); }
  catch { console.error(`agent_beat: ${settingsFile} is no JSON: the take would drop what it holds`); process.exit(1); }
}
mkdirSync(dirname(settingsFile), { recursive: true });
writeFileSync(settingsFile, JSON.stringify({ ...settings, spinnerTipsEnabled: false }, null, 2) + "\n");
// every state the copy passes through, kept beside it (agent_states.mjs) and moved into <out>/states at the end, so
// the page can be walked again on a later build from the states this take recorded
const statesDir = record + ".states", statesStop = record + ".states-stop";
rmSync(statesStop, { force: true });
rmSync(statesDir, { recursive: true, force: true });   // an earlier take's, which would read as this one's first state
// (its own process group, so a stop takes its rsync with it)
const keeper = spawn(process.execPath, [join(here, "agent_states.mjs"), "--folder", record, "--out", statesDir, "--stop", statesStop],
  { stdio: ["ignore", "inherit", "inherit"], detached: true });
const keeperDone = new Promise(r => keeper.on("exit", code => r(code)));
const stopKeeper = () => { try { process.kill(-keeper.pid, "SIGTERM"); } catch { /* gone */ } };
process.on("exit", stopKeeper);
// the copy as it was before rb open looks at it is the first state; a keeper that cannot keep it (no rsync) is said,
// and the take goes on without states
let statesFailed = null;
for (const end = Date.now() + 60000; !existsSync(join(statesDir, "states.jsonl"));) {
  if (keeper.exitCode !== null || Date.now() > end) {
    statesFailed = "the copy's first state was not kept, so the take keeps no states";
    console.error(`agent_beat: ${statesFailed}`); stopKeeper(); break;
  }
  await sleep(100);
}
// a beat stopped by a signal stops what it started too (exit handlers run only on exit)
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(sig, () => process.exit(130));
const open = spawn(rb, ["open", "--port", String(port)], { cwd: record, env, stdio: ["ignore", "pipe", "pipe"], detached: true });
let openSaid = "";
open.stdout.on("data", d => { openSaid += d; }); open.stderr.on("data", d => { openSaid += d; });
const stopOpen = () => { try { process.kill(-open.pid, "SIGINT"); } catch { /* gone */ } };
process.on("exit", stopOpen);
for (let i = 0; ; i++) {
  try { if ((await fetch(base)).ok && open.exitCode === null) break; } catch { /* not yet */ }
  if (i > 100 || open.exitCode !== null) { console.error(`agent_beat: rb open did not answer at ${base}: ${openSaid.slice(-600)}`); process.exit(1); }
  await sleep(200);
}
// a view the page links nowhere to is no view of this record: filming it would film rb's "nothing here"
if (start && !(await (await fetch(base)).text()).includes(`href="#${start}"`)) {
  console.error(`agent_beat: the page links nowhere to #${start}: give a view of this record (x-e1)`); process.exit(2);
}
// an earlier take in --out is cleared only now, once the take can start
rmSync(out, { recursive: true, force: true });

const pageBeats = { width: PAGE.w, height: PAGE.h, scale: PAGE.scale, beats: [{ name: "page", steps }] };
mkdirSync(join(out, "page"), { recursive: true });
writeFileSync(join(out, "page", "beats.json"), JSON.stringify(pageBeats, null, 1));
const walk = spawn(process.execPath, [join(here, "walk.mjs"), "--script", join(out, "page", "beats.json"), "--out", join(out, "page", "walk"),
  "--chrome", chrome], { stdio: ["ignore", "inherit", "inherit"] });
const walkDone = new Promise(r => walk.on("exit", code => r(code)));
const marksOf = dir => existsSync(join(dir, "marks.jsonl")) ? readFileSync(join(dir, "marks.jsonl"), "utf8").split("\n").filter(Boolean).map(l => JSON.parse(l)) : [];
let pageT0 = null;   // the wall time of the page walk's clock zero
for (const end = Date.now() + 120000; pageT0 === null;) {
  const m = marksOf(join(out, "page", "walk")).find(x => x.label === "ready");
  if (m) pageT0 = Date.now() / 1000 - m.t;
  else if (walk.exitCode !== null || Date.now() > end) { console.error("agent_beat: the page never got ready"); process.exit(1); }
  else await sleep(50);
}
await sleep(2000);

// ---- the sessions, one after the other
function session(name, line, more = []) {
  const argv = [join(here, "agent_take.py"), "--folder", record, "--say", line, "--cast", join(out, `${name}.cast`),
    "--rb-bin", dirname(rb), "--limit", String(limitMin * 60), ...(typeof args["study-bin"] === "string" ? ["--study-bin", args["study-bin"]] : []), ...more];
  const [cmd, all] = cpus ? ["taskset", ["-c", cpus, python, ...argv]] : [python, argv];
  return spawnSync(cmd, all, { stdio: ["ignore", "inherit", "inherit"], env: { HOME: process.env.HOME, USER: process.env.USER || "rb", PATH: process.env.PATH, LANG: "C.UTF-8" } }).status;
}
let failed = null;
const want = typeof args.runs === "string" ? runsBefore + Number(args.runs) : null;
if (!args["fresh-only"]) {
  if (session("agent", args.task, want === null ? [] : ["--until-runs", String(want)]) !== 0) failed = "the agent session did not end as asked (see agent.log)";
  await sleep(8000);   // the page's last swap after the last write
}
writeFileSync(agentFlag, "");
if (start && !failed && !args["no-fresh"]) {
  // the page goes back to the Overview before the fresh session is told anything
  for (const end = Date.now() + 120000; !marksOf(join(out, "page", "walk")).some(x => x.label === "overview");) {
    if (walk.exitCode !== null || Date.now() > end) { failed = "the page never went back to the Overview"; break; }
    await sleep(100);
  }
}
if (!failed && !args["no-fresh"]) {
  if (session("fresh", ask, ["--quiet", "20"]) !== 0) failed = "the fresh session failed (see fresh.log)";
  await sleep(3000);
}
writeFileSync(flag, "");
const pageCode = await walkDone;
stopOpen();
// the states are the take's second record: one not all kept is said in take.json and here, and fails nothing filmed
writeFileSync(statesStop, "");
const keeperCode = await Promise.race([keeperDone, new Promise(r => setTimeout(() => r("timeout"), 180000).unref())]);
const saidAtStart = statesFailed;
if (!statesFailed && keeperCode === "timeout") {
  stopKeeper(); statesFailed = "the states keeper did not end within 3 minutes of the take";
  await Promise.race([keeperDone, new Promise(r => setTimeout(r, 10000).unref())]);
} else if (!statesFailed && keeperCode === 3) statesFailed = "the states keeper reached its cap and kept only the last state after it";
else if (!statesFailed && keeperCode !== 0) statesFailed = "a state of the copy could not be kept (see the take's log)";
rmSync(join(out, "states"), { recursive: true, force: true });
let statesAt = existsSync(statesDir) ? "states" : null;
if (statesAt && spawnSync("mv", [statesDir, join(out, "states")]).status !== 0) { statesFailed = statesFailed || `the states could not be moved from ${statesDir}`; statesAt = statesDir; }
rmSync(statesStop, { force: true });
if (statesFailed && statesFailed !== saidAtStart) console.error(`agent_beat: ${statesFailed}`);
if (pageCode !== 0) failed = failed || "the page walk failed";
const changes = marksOf(join(out, "page", "walk")).filter(m => m.label === "change");
const casts = Object.fromEntries(["agent", "fresh"].filter(n => existsSync(join(out, `${n}.cast.json`)))
  .map(n => [n, JSON.parse(readFileSync(join(out, `${n}.cast.json`), "utf8"))]));
writeFileSync(join(out, "take.json"), JSON.stringify({
  record: basename(source), work: record, page: { css: [PAGE.w, PAGE.h], scale: PAGE.scale, t0_wall: pageT0, start }, casts,
  runs_before: runsBefore, runs_after: runFiles(), changes: changes.length, states: statesAt, states_failed: statesFailed, task: args.task, ask, failed,
  made: new Date().toISOString(),
}, null, 1) + "\n");
if (failed) { console.error(`agent_beat: ${failed}`); process.exit(1); }
console.log(`agent beat: ${runFiles() - runsBefore} runs recorded, ${changes.length} page changes; cut it with agent_cut.py ${out}`);
