"""What a release is cut from agrees with itself: the version in every place it is written, the changelog's entry for
it, a tag that publishes only a dated entry, the third-party notices written from the Cargo.lock beside them and
shipped with every package, actions run at the commits the workflows name, and the links the package gives."""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    p = ROOT / path
    if not p.is_file():
        pytest.skip(f"{path} is not beside the tests")
    return p.read_text(encoding="utf-8")


def one(pattern: str, path: str) -> str:
    [found] = re.findall(pattern, text(path), re.M)
    return found


def cargo_form(version: str) -> str:
    """The Rust version a PEP 440 one is written as: 0.6.0, 0.6.0-dev.0 for 0.6.0.dev0, 0.6.0-rc.1 for 0.6.0rc1."""
    m = re.fullmatch(r"(\d+\.\d+\.\d+)(?:\.?(dev|a|b|rc)(\d+))?", version)
    assert m, version
    return m[1] + (f"-{m[2]}.{m[3]}" if m[2] else "")


def test_the_version_is_the_same_wherever_it_is_written_and_the_changelog_heads_with_it():
    version = one(r'^version = "([^"]+)"$', "pyproject.toml")
    assert one(r'^__version__ = "([^"]+)"$', "src/rabbit_brain/__init__.py") == version
    assert one(r'^pub const VERSION: &str = "([^"]+)";$', "rust/src/lib.rs") == version
    assert one(r'^version = "([^"]+)"$', "rust/Cargo.toml") == cargo_form(version)
    assert one(r'^name = "rabbit-brain"\nversion = "([^"]+)"$', "rust/Cargo.lock") == cargo_form(version)
    # the newest entry is this version's, dated when it is released: "## 0.6.0", then "## 0.6.0 (2026-10-20)"
    heading = re.search(r"^## (.+)$", text("CHANGELOG.md"), re.M)[1]
    assert re.fullmatch(re.escape(version) + r"( \(\d{4}-\d{2}-\d{2}\))?", heading), (heading, version)


def test_a_tag_publishes_only_a_version_whose_changelog_entry_is_dated():
    yaml = pytest.importorskip("yaml")
    flow = yaml.load(text(".github/workflows/publish.yml"), Loader=yaml.BaseLoader)
    [step] = [s for s in flow["jobs"]["check"]["steps"] if s.get("name", "").startswith("Tag matches the package version")]
    assert 'grep -qxE "## ${ver//./\\\\.} \\([0-9]{4}-[0-9]{2}-[0-9]{2}\\)" CHANGELOG.md' in step["run"]


def notices_generator():
    path = ROOT / "rust" / "gen" / "gen_notices.py"
    if not path.is_file():
        pytest.skip("the generators stay in the repository")
    spec = importlib.util.spec_from_file_location("rb_gen_notices", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_third_party_notices_are_written_from_the_cargo_lock_beside_them():
    gen = notices_generator()
    lock = text("rust/Cargo.lock")
    notices = (ROOT / "THIRD-PARTY-NOTICES").read_text(encoding="utf-8")       # never skipped: every package carries it
    assert gen.recorded_digest(notices) == gen.lock_digest(lock), \
        "THIRD-PARTY-NOTICES is stale: python rust/gen/gen_notices.py"
    # rb's own version is not a dependency; any other package's entry is
    bumped = re.sub(r'(name = "rabbit-brain"\nversion = )"[^"]+"', r'\1"9.9.9"', lock)
    assert bumped != lock and gen.lock_digest(bumped) == gen.lock_digest(lock)
    moved = re.sub(r'(name = "rusqlite"\nversion = )"[^"]+"', r'\1"0.0.1"', lock)
    assert moved != lock and gen.lock_digest(moved) != gen.lock_digest(lock)
    assert gen.lock_digest(lock.replace("\n", "\r\n")) == gen.lock_digest(lock)


def test_the_notices_go_into_every_package_and_onto_the_release():
    assert re.search(r'^license-files = \["LICENSE", "THIRD-PARTY-NOTICES"\](\s+#.*)?$', text("pyproject.toml"), re.M)
    yaml = pytest.importorskip("yaml")
    flow = yaml.load(text(".github/workflows/publish.yml"), Loader=yaml.BaseLoader)
    steps = flow["jobs"]["github-release"]["steps"]
    [checkout] = [s for s in steps if s.get("uses", "").startswith("actions/checkout@")]
    [release] = [s for s in steps if s.get("name") == "Release the tag on GitHub"]
    assert checkout["with"]["path"] == "tree" and steps.index(checkout) < steps.index(release)
    assert release["run"].index("cp tree/THIRD-PARTY-NOTICES dist/") < release["run"].index("sha256sum")


def test_every_action_a_workflow_runs_is_pinned_to_a_commit_with_its_version_beside_it():
    """A tag or branch can be moved to other code, and the release workflows publish to PyPI: each step runs the
    commit it names. The toolchain action reads its toolchain from its ref, so a pinned one names it."""
    flows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    if not flows:
        pytest.skip("the workflows are not beside the tests")
    pinned = 0
    for flow in flows:
        for line in flow.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*(?:- )?uses: (\S+)(.*)$", line)
            if not m or m[1].startswith("./"):
                continue
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", m[1]) and re.fullmatch(r"  # \S.*", m[2]), (flow.name, line)
            pinned += 1
    assert pinned > 20
    yaml = pytest.importorskip("yaml")
    for flow in flows:
        for job in yaml.load(flow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"].values():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("dtolnay/rust-toolchain@"):
                    assert step["with"]["toolchain"] == "stable", flow.name
                if step.get("uses", "").startswith("PyO3/maturin-action@"):      # the maturin it installs, too
                    assert re.fullmatch(r"v\d+\.\d+\.\d+", step["with"]["maturin-version"]), flow.name


def test_the_package_names_its_homepage_repository_issues_and_changelog():
    urls = text("pyproject.toml").split("[project.urls]\n", 1)[1].split("\n\n", 1)[0]
    assert dict(re.findall(r'^(\w+) = "([^"]+)"$', urls, re.M)) == {
        "Homepage": "https://rabbitbrain.ai",
        "Documentation": "https://github.com/rabbit-brain/rb/blob/main/AGENTS.md",
        "Repository": "https://github.com/rabbit-brain/rb",
        "Issues": "https://github.com/rabbit-brain/rb/issues",
        "Changelog": "https://github.com/rabbit-brain/rb/blob/main/CHANGELOG.md",
    }
    assert one(r'^repository = "([^"]+)"$', "rust/Cargo.toml") == "https://github.com/rabbit-brain/rb"
