# The agent-baseline experiment: pre-registered protocol

_Question: on a public checkpoint pair, does an engineer with a coding agent plus Rabbit Brain do the release review with less effort, fewer errors and more consistency across updates than the same engineer with a coding agent and generally available tools? The trajectory signal is excluded from the primary comparison on purpose: a generic agent cannot derive it, so including it would decide the result by construction. It is reported separately as "what Rabbit Brain adds beyond parity". This file is committed before the counted runs and not edited after them; amendments made during the pilots are listed at the end._

## 1. Pre-registration (fixed before run 1)

- The prompts (section 4), operator rules (5), caps (5), rubric (6) and predictions (8) are fixed before the first counted run.
- Environment: one image used for every run: the RAFT repository at a fixed commit, the three public checkpoints, KITTI-2015 training, the Python dependencies, and in Path B only `rabbit-brain` at a fixed version with its `AGENTS.md`. The agent tool, its version and the model behind it are recorded; every session runs with the same settings.
- Reference numbers: per-case EPE computed once through RAFT's own evaluation path (`core/datasets.py` KITTI loader, `InputPadder`, `RAFT.forward(test_mode=True)`, the per-image EPE from `evaluate.py`) at the iteration count the prompt states. This is the same path `rb verify-adapter` compares against, so it is independent of Rabbit Brain's own adapter. Nobody edits the reference after the runs start.
- Working directories are snapshotted (git commit or tarball) after every round; transcripts are saved verbatim.

## 2. Materials

- Model: RAFT (public repository, fixed commit). One codebase, so both paths need exactly one adapter's worth of understanding.
- Checkpoints, all public weights: current = `raft-things`; candidate 1 = `raft-small` (an efficiency candidate, the most realistic release-review scenario, no evaluation-set contamination on KITTI); candidate 2 = `raft-sintel` for round 2 (`raft-kitti` was fine-tuned on KITTI-2015 train; contamination affects only the model-quality narrative, not the workflow measurement, and the write-up says so).
- Cases: the 200 KITTI-2015 training pairs; one pair is one case, id = frame stem. Metric: mean endpoint error over valid pixels per case; the dataset number is the mean of the per-case means; regression threshold +0.3 px; 12 refinement iterations for both models; all fixed in the prompt.
- Paths: A = engineer + coding agent + anything public it chooses to install (if it reaches for FiftyOne, that is the real competitor showing up; allow it). B = the same, plus `rabbit-brain` installed and named in the prompt.
- Agent: the one the target engineers use (Claude Code or Codex); if budget allows, both, reported separately. Fixed model and version.

## 3. Run matrix

2 paths x 3 runs x 2 rounds = 12 agent rounds. Each run starts from a clean copy of the environment. Round 2 happens in a fresh agent session with only the working directory as memory: this models "next week" and "a second engineer", which is where continuity is supposed to show. One uncounted pilot run per path first, to shake out environment problems; if a pilot reveals a Rabbit Brain defect, it is fixed, the version bumped, and the counted runs start over.

## 4. Prompts (written once; identical except for the bracketed sentence)

Round 1:

> I'm reviewing a candidate optical-flow checkpoint before release. Repo: `./raft` (RAFT). Current checkpoint: `./ckpt/raft-things.pth`. Candidate: `./ckpt/raft-small.pth`. Data: `./data/kitti2015/training` with ground truth. Run both models with 12 refinement iterations. Produce three things: (1) per-case mean endpoint error for both checkpoints on every pair, as a file I can read; (2) a ranked list of the cases where the candidate is worse than the current model by more than 0.3 px, with the evidence a reviewer needs to decide: numbers, flow visualisations and error maps for the worst cases; (3) a single command that re-runs this same review against a different candidate checkpoint later and reports which of today's flagged cases are still failing. Work in this directory. Ask me only if you are blocked. [Path B: Use Rabbit Brain for the review; it is installed; see `AGENTS.md`.]

Round 2 (fresh session, same directory):

> A new candidate checkpoint arrived: `./ckpt/raft-sintel.pth`. Re-run the release review against the current model and tell me which of the previously flagged cases are fixed, which are still failing, and which cases are newly worse. [Path B: Use Rabbit Brain; see `AGENTS.md`.]

## 5. Operator rules

The operator plays the engineer and is not blind to the path, so the rules do the blinding:

- Say nothing the prompt does not say. Answer only questions the agent asks, and only with facts about paths, environment or permissions.
- Allowed interventions, each logged with its category: (a) a factual answer; (b) a permission or tool approval; (c) if the agent declares done with a deliverable missing, say once "deliverable (n) is missing" and nothing else; (d) if the agent asks a design question ("which threshold?", "per-case or pooled?"), answer "as in the task"; logged, because a run that needs design answers is a finding.
- Never fix code, never paste snippets, never name a library.
- Caps: 90 minutes wall-clock per round and a token cap set before run 1 (the pilots used 200k to 210k agent tokens per round; the cap is 600k). A round that hits a cap is scored as-is.
- Logged per round: start and stop times, interventions by category, agent turns, tool calls, tokens, and the minute at which the first regression case with evidence was on disk.

## 6. Rubric (mechanical; graded from the snapshot, not from the transcript)

Completion, per deliverable, 0 or 1: (1) per-case errors for both checkpoints; (2) a ranked regression list with evidence for the worst cases; (3) a re-run command that works when the grader executes it; round 2: fixed / still failing / newly worse reported by case id.

Correctness against the reference: per-case EPE within 0.01 px for at least 95% of cases; precision and recall of the flagged set against the reference flagged set; threshold applied as stated; per-case mean, not pooled pixel mean; valid-pixel mask applied; cases paired correctly (same id, same pair, both models); the stated iteration count used. Each failure named: these are the quiet errors the product claims to prevent.

Continuity, round 2: round-1 artefacts reused or everything re-derived; definition drift between rounds (threshold, metric, averaging, mask, iterations; any change is drift); case-id consistency (round-2 statuses correctly matched to round-1 ids); whether the fresh session found and used the round-1 re-run command without being told.

Effort: wall-clock per round; interventions by category; time to the first useful finding; tool calls and tokens; one line on whether the operator had to read code to trust the numbers.

Beyond parity, Path B only, reported separately: cases flagged as improved on error but not settled; whether any coincide with reference regressions on the other checkpoint; saved checks carried into round 2; whether the receipt (`record.json`) lets the grader reproduce the numbers without reading the agent's code.

## 7. Analysis

Every run in one table (path x run x round x the rubric), then per-path medians. No significance tests at n = 3: this is an existence proof and a magnitude estimate, and the write-up says so. The prompt, the rules, the rubric, the reference numbers, the transcripts and the snapshots are published so anyone can rerun it; the landing page gets one sentence with the numbers and a link.

## 8. Pre-registered predictions and decision rule

- Round 1: Path A completes deliverables (1) and (2) within the cap with at most 2 interventions in at least 2 of 3 runs. A tie on producing the numbers is expected and is not a failure of the thesis.
- Correctness: at least 1 of 3 Path A runs has a definitional error (pooled mean, mask, pairing, threshold or iteration count). If none does, the "verified arithmetic" argument is weak and is dropped from the pitch.
- Round 2: Path A re-derives in at least 2 of 3 runs and drifts in at least 1; Path B reuses via `rb check` in 3 of 3 with zero drift.
- Decision rule: if Path A matches Path B on completion, correctness and round-2 continuity, the runner thesis is not supported on this workflow; the product's value then rests on the trajectory signal and the fitted scorer alone, and the landing page and the plan say so. If Path B wins only on continuity, the pitch is continuity, not convenience. If Path B fails its own agent-operability (the agent cannot drive the CLI from `AGENTS.md`), that is a defect to fix before any outreach.

## 9. Pilot results (uncounted), 2026-09-19

Run on a CPU fixture (six KITTI-style noise pairs with constant ground-truth flow, random-weight checkpoints) to shake out the environment, not to measure anything about the models.

- Path B, three variants: an agent importing an evaluator's CSV of a real review, an agent running the RAFT adapter on the fixture, and an agent with a custom model and no adapter. All completed the review from the one-line prompt and `rb docs`; 9 to 16 minutes and 39 to 69 tool calls each; every friction they logged (wording, defaults, hints, docs) was fixed the same day and is in the changelog. None asked a design question: the definitions came from the tool.
- Path A, one run on the RAFT fixture: completed all three deliverables in 21 minutes and 60 tool calls with no intervention (the operator was absent by design; the agent wrote down nine questions it would have asked and the answers it assumed). Its per-case EPE matched RAFT's own evaluation path to 4e-6 px, it built evidence panels, a manifest with checkpoint hashes, a re-run script with a stored flagged set, and a per-iteration error trace of its own. Its definitional choices matched the reference on pairing, mask, averaging and threshold; it chose 24 iterations because the prompt did not say (amendment 1). Everything it built needs ground truth; it produced no label-free signal.
- What the pilots changed in this protocol (amendments made before any counted run): (1) the prompt now fixes the iteration count, since a correct Path A run can otherwise differ from a correct Path B run on every number; (2) tool calls and tokens are logged as effort, since wall-clock on an agent is mostly waiting; (3) the fixture is for pilots only, the counted runs use the real checkpoints and the 200 pairs on a GPU; (4) "the stated iteration count used" joins the correctness list; (5) the reference path is named explicitly (RAFT's own) so it is independent of Rabbit Brain's adapter.
- What the pilots say about the thesis before the counted runs: on a one-off review with ground truth, a strong agent reaches parity with the tool in twenty minutes and builds its own continuity script; the experiment's weight sits on round 2 (does a fresh session find and trust that script, and do definitions drift), on correctness across three independent Path A runs, and on the label-free findings only Path B produces.

## 10. Amendments before the counted runs (second set, 2026-09-19)

Committed before any counted run, like the first set. Each names what it supersedes.

1. **Both agent tools, asymmetrically.** (Superseded by amendment 6 below; kept because a pre-registration that quietly rewrites itself is worth nothing.) Supersedes "if budget allows, both, reported separately" in section 2 and the matrix in section 3. The primary tool carries the full design (2 paths x 3 runs x 2 rounds = 12 rounds); the second tool runs a round-1 replication only (2 paths x 3 runs = 6 rounds). Eighteen rounds in total, about a day of wall-clock. The reason for spending it: a result produced only in one vendor's agent is discountable by exactly the engineers we want to convince, so the agent must be a reported factor and not a confound. The replication asks one question, whether the direction of the round-1 result survives a different agent. Round 2 is measured once, on the primary tool, because that is where three runs buy the most.

2. **Configuration is inside the experiment, not before it.** Supersedes the environment bullet in section 1. Previously both paths started from an environment with the model repository, checkpoints, data and dependencies installed, and for Path B with `rabbit-brain` already installed and configured. That measured the review and hid the integration, which is where a stranger's time actually goes and where a tool either earns its place or does not. The counted environment now carries the model repository, the checkpoints and the data, which any team already has, and nothing else: no `rb.toml`, no chosen adapter, no installed package. Path B's bracketed sentence becomes "Use Rabbit Brain for the review; install it from PyPI and read `AGENTS.md`." Two timestamps are logged per round instead of one: the first correct per-case number on disk, and the review complete. The gap between them is the configuration cost, measured the same way for both paths.

3. **A setup dimension in the rubric.** Adds to section 6, graded from the snapshot like the rest. Minutes and tool calls to the first correct per-case number. Whether the metric, valid mask, pairing and iteration count were right at that first number or only after rework. For Path B, whether `rb doctor`, `rb verify-hook` and `rb verify-adapter` were run before any result was reported, and whether a failure or disagreement was surfaced to the operator rather than worked around. A Path B run that reports numbers without the adapter check having passed is a product defect to fix, not a score to record.

4. **What this experiment does not answer.** Adds to sections 7 and 8. It measures whether the free tool earns its installation and its second use on one public workflow. It does not measure whether accumulated release history is worth paying for: that needs a real team, their own checkpoints, and a third and fourth round over months, and we cannot manufacture it. No claim about subscription value may cite this experiment, and the write-up says so in the same breath as the result.

5. **The onboarding path is a later arm, not a blocker.** When the onboarding step that turns a described setup into a configuration and an adapter ships, a Path B2 is added: round 1 again, with the agent starting from the generated package instead of a bare directory. B2 measures the configuration cost that package is meant to remove, against B as the baseline. It is not part of the counted runs and does not delay them.

6. **Correction to amendment 1, before any counted run: the matrix is symmetric.** Amendment 1 gave one agent tool the full design and the other a round-1 replication, to save six rounds. That cut in the wrong place. The claim this experiment exists to test is round-2 continuity, whether a fresh session finds and trusts what the previous one left behind, and that is precisely the behaviour most likely to differ between agent tools: context handling and the propensity to read a working directory before acting are not the same across them. Measuring continuity on one tool only would have left the experiment silent exactly where the agent is most likely to be a factor. So: both tools, both paths, three runs, two rounds. 2 x 2 x 3 x 2 = 24 rounds. There is no primary tool and no decision about which comes first; the tool is a reported factor in every cell, and the analysis table gains one column. The extra six rounds are the cheapest insurance in the design.
