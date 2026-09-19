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
    "E_FILE_NOT_FOUND": ("A file given on the command line does not exist.", "Check the path; it is relative to the current directory."),
    "E_RUN_NOT_FOUND": ("No such run.", "`rb runs` lists runs (add --runs-dir if the runs live elsewhere); ids look like 20260925-1412-raft-small. A path to bundle.json or a v1 comparison JSON also works."),
    "E_CASE_NOT_FOUND": ("That case id is not in this run.", "`rb findings <run> --filter all` lists every case id."),
    "E_CHECKS_INVALID": ("The checks file is not valid.", "Expected version 1 or 2; see `rb schema checks`."),
    "E_CHECKS_PROJECT_MISMATCH": ("The checks file belongs to another project.", "Use the same project name when importing, or point --checks at that project's file."),
    "E_LIMITS_INVALID": ("A limit is out of range.", "--max-regression ≥ 0, 0 ≤ --max-late-share ≤ 1, 0 ≤ --max-reversals ≤ 64."),
    "E_NOT_AVAILABLE": ("This command is not part of this version of rabbit-brain.", "`rb rerun`, `rb open` and `rb mcp` are planned. Use `rb run` (or `rb import` for results you already have) and `rb findings`."),
    "E_CONFIG_MISSING": ("No rb.toml in the current directory.", "Run `rb init --project <name> --adapter raft --model-code ./raft --dataset <path>` in the project root, or `rb init --demo` for a synthetic project."),
    "E_CONFIG_INVALID": ("rb.toml could not be read.", "Fix the field named in the message; `rb init --force` rewrites a fresh file."),
    "E_ADAPTER_IMPORT": ("The adapter could not be imported.", "Run `rb doctor`; install the model's own requirements (torch, opencv, scipy for RAFT) in this environment; check [adapter] id or module in rb.toml."),
    "E_MODEL_CODE_MISSING": ("The model code directory is missing or not a RAFT checkout.", "Point [adapter] model_code at the repository checkout (it must contain core/raft.py)."),
    "E_CHECKPOINT_NOT_FOUND": ("A checkpoint is missing or does not match the configured architecture.", "Check the path; for raft-small weights set [adapter] small = true. Do not download weights without asking the human."),
    "E_DATASET_EMPTY": ("No cases were found.", "Check [dataset] path and kind in rb.toml (KITTI: image_2/*_10.png and *_11.png; flow_occ/ optional) or the --cases file."),
    "E_HOOK_NOT_REACHABLE": ("The update loop is not instrumented; no trajectory was recorded.", "For RAFT-family models the built-in adapter hooks model.update_block automatically; for a custom adapter call rec.step(delta) once per iteration inside infer(). Or run with --no-trajectories (stability is then 'not assessed')."),
    "E_DOCTOR": ("rb doctor found a problem with the environment or the project.", "Read data.checks: every failed check has a detail and a fix."),
    "E_ADAPTER_DISAGREES": ("The adapter's per-case error disagrees with the model repository's own evaluation on the same cases, so its results are not evidence about the model.", "Run `rb verify-adapter --checkpoint <path>` and compare the two columns: the adapter's loading, preprocessing (input range, padding, colour order), forward call or metric formula differs from the reference path. Fix the adapter; `rb run --skip-reference` runs anyway and says so in the receipt."),
    "E_HOOK_LENGTH": ("The recorder fired a different number of times than the configured iterations.", "Check that the hook fires exactly once per refinement iteration, and that [adapter] iterations matches what infer() runs."),
    "E_DEVICE": ("The requested device is not available.", "Set [adapter] device = \"cpu\" (slow) or pass --device cpu, or run on a machine with CUDA."),
    "E_INFERENCE_FAILED": ("Inference failed on every case.", "Run `rb verify-hook --checkpoint <path>` to see the first error; check checkpoint/architecture and the dataset."),
    "E_WRITE_FAILED": ("A file could not be written.", "Check permissions on the current directory or pass --runs-dir."),
    "E_EVIDENCE_DEPS": ("Evidence rendering needs numpy and pillow.", "pip install numpy pillow (both come with pip install 'rabbit-brain[raft]')."),
    "E_INTERNAL": ("Unexpected failure.", "Re-run with --json and report the output at https://github.com/rabbit-brain/rb/issues."),
}


ENVIRONMENT_CODES = {"E_DOCTOR", "E_ADAPTER_IMPORT", "E_MODEL_CODE_MISSING", "E_DEVICE", "E_HOOK_NOT_REACHABLE", "E_HOOK_LENGTH", "E_INFERENCE_FAILED", "E_ADAPTER_DISAGREES", "E_EVIDENCE_DEPS"}  # exit 3


@dataclass
class RBError(Exception):
    code: str
    message: str | None = None
    fix: str | None = None
    exit_code: int = EXIT_INVALID
    problems: list[str] = field(default_factory=list)

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
        return d
