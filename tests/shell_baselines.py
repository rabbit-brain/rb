"""What the shell's budgets start from: the time and size of the bridge's `home` op (what rb ui reads on every change of
the log, tui.home_view) and of rb open's /state.json, on the scale record (rbtest.scale_record), before the shell adds
anything to them. Phase 1a's budget test holds both within 20% of BASELINES, in time and in size, measured by `measure`
on a scale record made the same way.

Each figure is five timed reads after one untimed one, as the median and the max: seconds from asking to the whole
answer read (for `home`, a fresh `rb bridge` process, as rb ui starts one; for /state.json, one GET to a running rb
open, which computes every answer afresh), and the bytes of that answer. `status` is rb status --json on the same
record, read the same way, so a machine slower or faster than the one these were taken on can hold the ratio of a
time to it instead of the time. Taken on the debug build, under the heavy-job lock, on the machine `machine` names.

    python tests/shell_baselines.py <the scale record's checkout>    prints the figures as JSON
"""
from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# phase 0's figures: measure() on rbtest.scale_record (700 writes), rb built at d81262f, 2026-10-19
BASELINES = {
    "home": {"seconds_median": 0.7573, "seconds_max": 0.7766, "bytes_median": 1621413, "bytes_max": 1621413},
    "status": {"seconds_median": 0.7815, "seconds_max": 0.982, "bytes_median": 2727846, "bytes_max": 2727846},
    "state_json": {"seconds_median": 0.6513, "seconds_max": 0.7303, "bytes_median": 1623663, "bytes_max": 1623663},
    "machine": "Darwin arm64, 8 cpus",
}
# each machine's figures, by its `machine` line. The Ubuntu box's: measure() on rbtest.scale_record, rb built at
# e3068fd (int before phase 1a), 2026-10-20, load under 2 (MP-1020-21 proposal 2, decided Oct 21; MP-1021-03)
BY_MACHINE = {
    BASELINES["machine"]: BASELINES,
    "Linux x86_64, 16 cpus": {
        "home": {"seconds_median": 0.6353, "seconds_max": 0.6711, "bytes_median": 1624066, "bytes_max": 1624066},
        "status": {"seconds_median": 0.6656, "seconds_max": 0.6865, "bytes_median": 2727672, "bytes_max": 2727672},
        "state_json": {"seconds_median": 0.5924, "seconds_max": 0.6259, "bytes_median": 1625685, "bytes_max": 1625685},
        "machine": "Linux x86_64, 16 cpus",
    },
}


def for_this_machine() -> dict:
    """The figures taken on a machine of this system (Darwin, Linux), or BASELINES where none was: a budget's ratio of a
    time to rb status depends on the system more than on the machine's speed."""
    return next((b for m, b in BY_MACHINE.items() if m.split(" ")[0] == platform.system()), BASELINES)


def _five(read) -> dict:
    """One untimed read, then five timed: the median and max of their seconds, and of their bytes."""
    read()
    took, sizes = [], []
    for _ in range(5):
        start = time.perf_counter()
        size = read()
        took.append(time.perf_counter() - start)
        sizes.append(size)
    return {"seconds_median": round(statistics.median(took), 4), "seconds_max": round(max(took), 4),
            "bytes_median": int(statistics.median(sizes)), "bytes_max": max(sizes)}


def home_op(root: Path) -> int:
    """The bridge's `home` op, as rb ui asks it: one `rb bridge` process, one request; the answer's bytes."""
    from rabbit_brain import _rb
    line = json.dumps({"op": "home", "root": os.fspath(root), "via": "cli", "agent": None, "args": {"since_baseline": None}})
    r = subprocess.run([_rb.found(), "bridge"], input=(line + "\n").encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=_rb.environment(), check=True)
    assert json.loads(r.stdout.decode("utf-8").splitlines()[-1])["ok"], r.stdout[-500:]
    return len(r.stdout)


def status_json(root: Path) -> int:
    """rb status --json, the reference a ratio is taken to; the answer's bytes."""
    from rabbit_brain import _rb
    r = subprocess.run([_rb.found(), "status", "--json"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_rb.environment())
    assert r.returncode in (0, 1) and json.loads(r.stdout)["ok"], r.stdout[-500:]
    return len(r.stdout)


def measure(root: Path) -> dict:
    """The figures BASELINES holds, read now on the record at `root`."""
    from rbtest import Open
    root = Path(root).resolve()
    out = {"home": _five(lambda: home_op(root)), "status": _five(lambda: status_json(root))}
    server = Open(root)
    try:
        def state() -> int:
            with urllib.request.urlopen(server.url + "/state.json", timeout=120) as r:
                body = r.read()
            assert json.loads(body)["home"], body[:300]
            return len(body)
        out["state_json"] = _five(state)
    finally:
        server.close()
    out["machine"] = f"{platform.system()} {platform.machine()}, {os.cpu_count()} cpus"
    return out


if __name__ == "__main__":
    print(json.dumps(measure(Path(sys.argv[1])), indent=1))
