import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def expected() -> dict:
    """Outputs of the v5 workspace's TypeScript (lib/comparison.ts), dumped once with esbuild + node. The Python must agree."""
    return json.loads((FIXTURES / "v5-expected.json").read_text(encoding="utf-8"))


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RB_RUNS_DIR", raising=False)
    return tmp_path
