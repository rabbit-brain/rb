/**
 * The TypeScript answer for the conformance test.
 *
 *   node answer.mjs <comparison.ts> <payloads.json>
 *
 * Reads the payloads rb would inline into a generated report, computes everything the viewer shows
 * with the viewer's own comparison.ts, and writes the answers to stdout. Node runs the TypeScript
 * directly, so the file under test is the source that ships in the viewer rather than a transpiled
 * stand-in.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [libPath, payloadPath] = process.argv.slice(2);
if (!libPath || !payloadPath) {
  console.error("usage: answer.mjs <comparison.ts> <payloads.json>");
  process.exit(2);
}
const lib = await import(pathToFileURL(resolve(libPath)).href);
const { comparisonSchema, limitsSchema, checkSchema, summary, verdict, rank, outcome, stabilityOutcome, isFlagged, priority, checkResult } = lib;

const answers = [];
for (const item of JSON.parse(readFileSync(payloadPath, "utf8"))) {
  // Parse rather than trust: the viewer parses too, and a payload it would reject must fail here
  // rather than quietly conform.
  const run = comparisonSchema.parse(item.payload.run);
  const limits = limitsSchema.parse(item.payload.limits);
  const checks = checkSchema.array().parse(item.payload.checks ?? []);
  const s = summary(run, limits);
  const v = verdict(run, limits, checks);
  // The one line rb writes at the top of report.md, assembled the way the workspace shows it:
  // the verdict text, then the case to start with.
  const line = v.ready ? v.text : `${v.text} Start with ${v.start}.`;
  answers.push({
    name: item.name,
    verdict: { ready: v.ready, status: v.status, line, start: v.start },
    summary: {
      baseline: s.baseline, candidate: s.candidate, change: s.change,
      regressions: s.regressions, improved: s.improved, unstable: s.unstable,
      unstablePassing: s.unstablePassing, withTrajectories: s.withTrajectories, flagged: s.flagged,
    },
    cases: Object.fromEntries(run.cases.map(c => [c.id, {
      error: outcome(c, limits.threshold),
      stability: stabilityOutcome(c, limits),
      flagged: isFlagged(c, limits),
      priority: priority(c, limits),
    }])),
    order: rank(run.cases, limits).map(c => c.id),
    checks: checks.map(c => ({ id: c.id, ...checkResult(c, run) })),
  });
}
process.stdout.write(JSON.stringify(answers));
