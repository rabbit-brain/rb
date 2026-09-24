"""Error codes: one table feeds `errors[]` in the JSON envelope, `rb docs`, and the tests.

Exit codes: 0 ok · 1 a check failed (rb check run) · 2 invalid input or config · 3 environment failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_INVALID = 2
EXIT_ENVIRONMENT = 3

# code -> (meaning, what to do)
ERRORS: dict[str, tuple[str, str]] = {
    "E_IMPORT_INVALID": (
        "The results file is not a valid comparison.",
        "Fix the listed problems, or start from the minimal example: `rb schema example`.",
    ),
    "E_IMPORT_NOT_JSON": (
        "The file is not valid JSON.",
        "Check for a missing comma or bracket, or start from `rb schema example`.",
    ),
    "E_IMPORT_TOO_LARGE": (
        "The results file is over 2 MB (the limit is 500 cases).",
        "Split the case set or drop per-frame series.",
    ),
    "E_IMPORT_CONFIG": (
        "CSV import needs the project, model names, dataset, metric and unit.",
        "Pass --project, --baseline-name, --candidate-name, --dataset, --metric and --unit.",
    ),
    "E_CSV_INVALID": (
        "The CSV could not be converted.",
        "Required columns: case_id, baseline_error, candidate_error. Optional: name, tags, baseline_trajectory, candidate_trajectory, baseline_frames, candidate_frames (semicolon-separated numbers).",
    ),
    "E_WORKSPACE_NO_TOKEN": (
        "No workspace token. The workspace is the paid feature; the tool itself needs no account.",
        "Set RB_WORKSPACE_TOKEN to a token from your workspace settings. `rb plans` needs no token and shows what a workspace costs.",
    ),
    "E_WORKSPACE_AUTH": (
        "The workspace rejected this token.",
        "It may have been revoked, or it may belong to a different deployment. Create a new one in your workspace settings and set RB_WORKSPACE_TOKEN.",
    ),
    "E_WORKSPACE_PAYMENT_REQUIRED": (
        "This workspace has no active subscription.",
        "Comparing, saving checks and failing CI stay free and local. Collecting results into a shared workspace is the paid part: `rb workspace checkout` returns a link for someone to approve.",
    ),
    "E_WORKSPACE_NOT_SELLABLE": (
        "That plan is published but not yet sellable.",
        "It is not finished, and we do not take payment for unfinished work. `rb plans` shows which plans can be bought today.",
    ),
    "E_WORKSPACE_UNREACHABLE": (
        "Could not reach the workspace.",
        "Check network access to the host, or set RB_WORKSPACE_URL if you run your own deployment. Nothing local was affected; the run is still on disk.",
    ),
    "E_WORKSPACE_REJECTED": (
        "The workspace refused the request.",
        "The message says why. If it mentions the bundle's shape, push the bundle.json that `rb review run` or `rb review import` wrote, unmodified.",
    ),
    "E_FILE_NOT_FOUND": ("A file given on the command line does not exist.", "Check the path; it is relative to the current directory."),
    "E_RUN_NOT_FOUND": ("No such run.", "`rb review runs` lists runs (add --runs-dir if the runs live elsewhere); ids look like 20260925-1412-raft-small. A path to bundle.json or a v1 comparison JSON also works."),
    "E_CASE_NOT_FOUND": ("That case id is not in this run.", "`rb review findings <run> --filter all` lists every case id."),
    "E_CHECKS_INVALID": ("The checks file is not valid.", "Expected version 1 or 2; see `rb schema checks`."),
    "E_CHECKS_PROJECT_MISMATCH": ("The checks file belongs to another project.", "Use the same project name when importing, or point --checks at that project's file."),
    "E_LIMITS_INVALID": ("A limit is out of range.", "--max-regression ≥ 0, 0 ≤ --max-late-share ≤ 1, 0 ≤ --max-reversals ≤ 64."),
    "E_NOT_AVAILABLE": ("This command is not part of this version of rabbit-brain.", "The message says what to use instead. `rb --help` lists every command in this version."),
    "E_CONFIG_MISSING": ("No rb.toml in the current directory.", "Run `rb review init --project <name> --adapter raft --model-code ./raft --dataset <path>` in the project root, or `rb review init --demo` for a synthetic project."),
    "E_CONFIG_INVALID": ("rb.toml could not be read.", "Fix the field named in the message; `rb review init --force` rewrites a fresh file."),
    "E_ADAPTER_IMPORT": ("The adapter could not be imported.", "Run `rb review doctor`; install the model's own requirements (torch, opencv, scipy for RAFT) in this environment; check [adapter] id or module in rb.toml."),
    "E_MODEL_CODE_MISSING": ("The model code directory is missing or not a RAFT checkout.", "Point [adapter] model_code at the repository checkout (it must contain core/raft.py)."),
    "E_CHECKPOINT_NOT_FOUND": ("A checkpoint is missing or does not match the configured architecture.", "Check the path; for raft-small weights set [adapter] small = true. Do not download weights without asking the human."),
    "E_DATASET_EMPTY": ("No cases were found.", "Check [dataset] path and kind in rb.toml (KITTI: image_2/*_10.png and *_11.png; flow_occ/ optional) or the --cases file."),
    "E_HOOK_NOT_REACHABLE": ("The update loop is not instrumented; no trajectory was recorded.", "For RAFT-family models the built-in adapter hooks model.update_block automatically; for a custom adapter call rec.step(delta) once per iteration inside infer(). Or run with --no-trajectories (stability is then 'not assessed')."),
    "E_DOCTOR": ("rb doctor found a problem with the environment or the project.", "Read data.checks: every failed check has a detail and a fix."),
    "E_ADAPTER_DISAGREES": ("The adapter's per-case error disagrees with the model repository's own evaluation on the same cases, so its results are not evidence about the model.", "Run `rb review verify-adapter --checkpoint <path>` and compare the two columns: the adapter's loading, preprocessing (input range, padding, colour order), forward call or metric formula differs from the reference path. Fix the adapter; `rb review run --skip-reference` runs anyway and says so in the receipt."),
    "E_HOOK_LENGTH": ("The recorder fired a different number of times than the configured iterations.", "Check that the hook fires exactly once per refinement iteration, and that [adapter] iterations matches what infer() runs."),
    "E_DEVICE": ("The requested device is not available.", "Set [adapter] device = \"cpu\" (slow) or pass --device cpu, or run on a machine with CUDA."),
    "E_INFERENCE_FAILED": ("Inference failed on every case.", "Run `rb review verify-hook --checkpoint <path>` to see the first error; check checkpoint/architecture and the dataset."),
    "E_WRITE_FAILED": ("A file could not be written.", "Check permissions on the current directory or pass --runs-dir."),
    "E_EVIDENCE_DEPS": ("Evidence rendering needs numpy and pillow.", "pip install numpy pillow (both come with pip install 'rabbit-brain[raft]')."),
    "E_NO_INVESTIGATION": ("No research state here: no .rb/ in this directory or any directory above it.", "Run `rb init \"<what you are trying to establish>\"` in the project root (it creates .rb/), or cd into a project that has one."),
    "E_INVESTIGATION_EXISTS": ("An investigation already exists here or in a directory above.", "Use it: `rb status`. One .rb/ per line of work; start another in a different directory."),
    "E_OBJECT_NOT_FOUND": ("No object with that id in this investigation.", "`rb status --json` lists every object; `rb show <experiment>` lists its variants, settings, claims and evidence; a setting is addressed as <experiment>/<name>."),
    "E_OBJECT_INVALID": ("The values given do not make a valid object.", "Do what the message says; `rb <command> --help` lists the arguments and `rb schema <kind>` prints an object's shape (claim, experiment, evidence, ...)."),
    "E_FROZEN": ("The experiment is frozen, and this would change what it locked: a setting, a variant, what it varies, or a claim's criterion.", "A person runs the same command with --amend --why \"<reason>\"; the change, the reason and the hash before and after are kept."),
    "E_USAGE": ("The command line could not be parsed: an unknown option, a missing argument, or a value that is not allowed.", "`rb <command> --help` lists the arguments. A negative number is written as it is (--at-most -0.001); text that starts with '-' goes after --."),
    "E_HUMAN_ONLY": ("Freezing, deciding, amending, and retracting what something rests on are a person's calls, and rb records this call as an agent's.", "Hand errors[0].handoff.command to the person, to run in their own terminal. Do not set or unset RB_ACTOR."),
    "E_ACTOR_INVALID": ("RB_ACTOR is not in the form human:<name> or agent:<name>.", "Set it to human:<name> or agent:<name> (letters, digits and _.@+-), or unset it: rb then detects the agent runtime, or names the person from git."),
    "E_SOURCE_UNRESOLVED": ("The source was read and does not state the value, or could not be read.", "The setting stays provisional. Check the path, key, line, quote or commit, or set the value the source actually states: `rb spec set <exp> <name> <value> --source <file#key>`."),
    "E_SOURCE_OUTSIDE": ("The source is outside the project, so nobody else can check it.", "Copy it into the repository, commit it, and point --source at the copy."),
    "E_SOURCE_UNVERIFIABLE": ("This source cannot be checked by reading a file: a url, a note, or a file source without a line or a quote.", "Point at a config key (file#key), a file line, a quote in a saved copy of the page, or a run's JSON (run:PATH#/pointer) that states the value; or a person vouches for it: `rb decide <exp>/<name> accept --why \"...\"`."),
    "E_STATE_EDITED": ("An object under .rb/ was changed outside rb (edited, deleted, or written by hand), and rb will not write over it.", "`rb doctor` lists every such file. `rb doctor --restore` puts back what rb last wrote; if the change is right, a person runs `rb doctor --adopt --why \"...\"`."),
    "E_STATE_CORRUPT": ("A file under .rb/ is not valid.", "It was probably edited by hand or left mid-merge. `rb doctor` lists every such file; `rb doctor --restore` puts back what rb last wrote."),
    "E_INTERNAL": ("Unexpected failure.", "Re-run with --json and report the output at https://github.com/rabbit-brain/rb/issues."),
}


ENVIRONMENT_CODES = {"E_DOCTOR", "E_ADAPTER_IMPORT", "E_MODEL_CODE_MISSING", "E_DEVICE", "E_HOOK_NOT_REACHABLE", "E_HOOK_LENGTH", "E_INFERENCE_FAILED", "E_ADAPTER_DISAGREES", "E_EVIDENCE_DEPS", "E_WORKSPACE_UNREACHABLE"}  # exit 3


@dataclass
class RBError(Exception):
    code: str
    message: str | None = None
    fix: str | None = None
    exit_code: int = EXIT_INVALID
    problems: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)          # structured detail for agents, e.g. the handoff for a person's call

    def __post_init__(self) -> None:
        if self.code in ENVIRONMENT_CODES and self.exit_code == EXIT_INVALID:
            self.exit_code = EXIT_ENVIRONMENT
        meaning, default_fix = ERRORS.get(self.code, ("Unexpected failure.", ERRORS["E_INTERNAL"][1]))
        if self.message is None:
            self.message = meaning
        if self.fix is None:
            self.fix = default_fix
        super().__init__(self.message)

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message, "fix": self.fix}
        if self.problems:
            d["problems"] = list(self.problems)
        d.update(self.extra)
        return d
