#!/usr/bin/env node
// The agent beat's record states (take 7): while a take runs, every state the copy passes through is kept, so the page
// can be walked again on a later build from the states the take recorded, with the take's casts kept as they are.
//
//   node tools/demo/agent_states.mjs --folder <the take's copy> --out <states dir> --stop <file> [--max 3000]
//
// - Once a second it reads the whole copy, each file by path, size, times and inode, but rb's own local cache and rb's
//   past-version checkouts (.git/rb/views), which no state holds. Whenever that differs from the last state kept, the
//   whole copy is kept again as <out>/<n>/: at once for a change in .rb/ or .git/, else at most every 5 s. rsync
//   compares by content: a file whose bytes are those of the state before is linked to it, so a state costs only what
//   changed, and a file the copy changes in place afterwards never changes a state kept.
// - <out>/states.jsonl has one line per state: {"n", "wall" (epoch seconds when its copy began), "took" (seconds),
//   "moving" (true when the copy moved while it was kept, so the state may hold one write and not the next; the next
//   look keeps it again)}.
// - A file rsync cannot read leaves it out of that state, marked "partial" with rsync's words, said once; a copy that
//   fails otherwise is said once and tried again on each look, the failures counted. It ends once --stop exists or the
//   process that started it is gone, keeping the copy once more if it moved, and exits 0 when every state was kept, 1
//   when a copy failed and never came right, 3 when it reached --max states (a "max" line says so; the last of them is
//   kept for the end). Virtual environments, node_modules and __pycache__ are in no state.

import { spawnSync } from "node:child_process";
import { mkdirSync, existsSync, readdirSync, lstatSync, appendFileSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1]]);
  return acc;
}, []));
for (const k of ["folder", "out", "stop"]) if (typeof args[k] !== "string") { console.error(`agent_states: give --${k}`); process.exit(2); }
const folder = resolve(args.folder), out = resolve(args.out), stop = resolve(args.stop);
if (!existsSync(join(folder, ".rb"))) { console.error(`agent_states: ${folder} holds no .rb/`); process.exit(2); }
const MAX = Number(args.max || 3000);
if (!(MAX >= 2)) { console.error("agent_states: --max is 2 or more (the last state is kept for the end)"); process.exit(2); }
if (spawnSync("rsync", ["--version"]).status !== 0) { console.error("agent_states: no rsync on the PATH"); process.exit(2); }
// rb's own local cache and past-version checkouts, and anywhere a virtual environment, node's modules or Python's
// caches (which nothing the page reads, and which can weigh gigabytes)
const SKIP = [".rb/cache", ".git/rb/views"], SKIP_NAMES = new Set([".venv", "node_modules", "__pycache__"]);
const skip = new Set(SKIP.map(p => join(folder, p)));
const parent = process.ppid;
const unread = new Set();

// rb's own record and git's state (what rb writes and looks with) are "core": a change there is kept at the next look;
// any other change at most once every 5 s (a run's log can move every second), and always at the end
const CORE = [".rb", ".git"].map(p => join(folder, p));
function print(dir, acc) {
  let names;
  try { names = readdirSync(dir).sort(); }
  catch (e) {
    if (e.code !== "ENOENT" && !unread.has(dir)) { unread.add(dir); console.error(`agent_states: cannot read ${dir}: ${e.code}`); }
    acc.push(`${dir.slice(folder.length)}\t${e.code}`);
    return;
  }
  for (const name of names) {
    const p = join(dir, name);
    if (skip.has(p) || SKIP_NAMES.has(name)) continue;
    let st;
    try { st = lstatSync(p); } catch { continue; }   // gone between the listing and the look
    if (st.isDirectory()) { acc.push(`${p.slice(folder.length)}\tdir`); print(p, acc); }
    else acc.push(`${p.slice(folder.length)}\t${st.size}\t${st.mtimeMs}\t${st.ctimeMs}\t${st.ino}`);
  }
}
const fingerprint = () => {
  const core = [], rest = [];
  print(folder, rest);
  const isCore = line => CORE.some(c => (folder + line.split("\t")[0] + "/").startsWith(c + "/"));
  return { core: rest.filter(isCore).join("\n"), rest: rest.filter(l => !isCore(l)).join("\n") };
};

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
let n = 0, failures = 0, failingFor = 0, lastWall = 0, code = 0, capped = false;
let last = { core: null, rest: null };
const said = new Set(), sayOnce = line => { if (!said.has(line)) { said.add(line); console.error(line); } };
// keeps the copy as state n; says whether it held still meanwhile, and what moved if not
const keep = before => {
  const dir = join(out, String(n)), wall = Date.now() / 1000;
  rmSync(dir, { recursive: true, force: true });
  const argv = ["-a", "--checksum", "--delete", ...SKIP.map(p => `--exclude=/${p}`), ...[...SKIP_NAMES].map(p => `--exclude=${p}/`),
    ...(n ? [`--link-dest=${join(out, String(n - 1))}`] : []), folder + "/", dir + "/"];
  const r = spawnSync("rsync", argv, { encoding: "utf8", timeout: 120000 });
  const why = (r.stderr || "").trim().split("\n").filter(l => !/^rsync (error|warning):/.test(l));
  // rsync's 24: a file vanished while it copied (a run's temporary file), marked moving; 23: a file it could not
  // read, the rest copied, marked partial with what it could not read
  if (r.status !== 0 && r.status !== 24 && r.status !== 23) {
    failures += 1; failingFor += 1;
    sayOnce(`agent_states: a state not kept (rsync ${r.status ?? r.signal}): ${why.join("; ").slice(-300)}; tried again on each look`);
    rmSync(dir, { recursive: true, force: true });
    return { kept: false };
  }
  if (r.status === 23) sayOnce(`agent_states: kept without what rsync could not read: ${why.join("; ").slice(-300)}`);
  const after = fingerprint();
  const moved = { core: after.core !== before.core, rest: after.rest !== before.rest || r.status === 24 };
  const moving = moved.core || moved.rest;
  appendFileSync(join(out, "states.jsonl"), JSON.stringify({ n, wall, took: Date.now() / 1000 - wall, ...(moving ? { moving } : {}),
    ...(r.status === 23 ? { partial: why.slice(0, 20) } : {}) }) + "\n");
  n += 1; failingFor = 0; lastWall = wall;
  return { kept: true, moved };
};
for (;;) {
  // told to stop, or the beat that started it is gone
  const stopping = existsSync(stop) || process.ppid !== parent;
  const now = fingerprint();
  const due = now.core !== last.core || (now.rest !== last.rest && (stopping || Date.now() / 1000 - lastWall >= 5));
  // the last of --max states is kept for the end
  if (due && n >= MAX - 1 && !stopping) {
    if (!capped) {
      capped = true; code = 3;
      appendFileSync(join(out, "states.jsonl"), JSON.stringify({ max: MAX, wall: Date.now() / 1000 }) + "\n");
      console.error(`agent_states: ${MAX - 1} states kept; keeping only the last one more`);
    }
  } else if (due) {
    const k = keep(now);
    // what moved while it was kept, or a copy that failed, is looked at again
    if (!k.kept) last = { core: null, rest: null };
    else last = { core: k.moved.core ? null : now.core, rest: k.moved.rest ? null : now.rest };
  }
  if (stopping && last.core === now.core && last.rest === now.rest) break;
  if (stopping && (failingFor >= 5 || (capped && n >= MAX))) break;
  await new Promise(r => setTimeout(r, 1000));
}
if (failures) console.error(`agent_states: ${failures} copies failed${failingFor ? ", the last one too" : ", each kept on a later look"}`);
process.exit(code || (failingFor ? 1 : 0));
