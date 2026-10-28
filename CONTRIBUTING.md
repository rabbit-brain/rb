# Contributing to Rabbit Brain

Use [GitHub issues](https://github.com/rabbit-brain/rb/issues) for bugs and proposals, and pull requests for changes. Include the rb version, operating system, the command that triggered the behavior and what you expected. A small synthetic record is preferable to an upload of private research. Report security concerns through [SECURITY.md](SECURITY.md).

## Development setup

You need Python 3.10 or later, Git and a stable Rust toolchain. From a checkout, create and activate a virtual environment, then install the development dependencies:

```sh
python -m pip install -e ".[dev]"
rb version
```

The install builds the Rust binary. Rust owns the record, source checks, findings and write rules. The Python SDK and terminal UI ask that binary for their data. Read [rust/ARCHITECTURE.md](rust/ARCHITECTURE.md) for the layout and generated files, and [AGENTS.md](AGENTS.md) for the behavior. If your working project has a `.rb/` record, read `rb context` before changing the work; do not edit that record by hand.

## Make and check a change

Keep changes focused and explain the behavior they change. A fix should have a regression check when it protects a rule, compatibility boundary or failure path. Update the manual with a changed command or rule; the binary embeds `AGENTS.md` and `SKILL.md`, so rebuild after changing them. Regenerate affected files with the scripts in `rust/gen/` when their declarations change; `tests/test_generated.py` checks that they agree. When `rust/Cargo.lock` changes, run `python rust/gen/gen_notices.py` (it needs `cargo install cargo-about --locked --features cli`) to write `THIRD-PARTY-NOTICES` again; `tests/test_release_setup.py` fails until you do.

Code is hand-formatted: match the surrounding style, and do not run `cargo fmt` or `ruff format`; a formatter pass rewrites whole files.

Run relevant focused checks while developing. The normal suite is:

```sh
cargo test --manifest-path rust/Cargo.toml -q
python -m pytest -q
python .github/scripts/test-public-content.py
python .github/scripts/test-attribution.py
```

On Linux, also compare the stored command and browser responses:

```sh
python rust/tests_py/golden.py --stored "$(command -v rb)" -j 4
python rust/tests_py/diff_open.py --stored "$(command -v rb)"
python rust/tests_py/diff_hub.py --stored "$(command -v rb)"
```

These snapshots include Linux paths and system messages. Review each changed response before recording an intentional update; a changed snapshot is not evidence that the new behavior is correct. `RB_BIN` can name an already built binary when running Python tests. Ensure it belongs to the revision under review, and do not replace it during a test run.

For packaging changes, run `python -m build` and `python -m twine check dist/*`. The native wheel workflow checks installed packages on Linux x86_64 and aarch64, macOS Intel and Apple Silicon, and Windows x86_64. See [RELEASING.md](RELEASING.md) for the release process.

## Submit the change

Describe the problem, the resulting behavior and the checks you ran. Mention compatibility changes and any unresolved limitation. Keep private research, credentials and local machine paths out of examples and test data. Ordinary contributions do not need release secrets or access to hosted services.

Commit under your own name. The attribution check fails a commit whose author, committer or co-author trailer is a coding agent, and a commit or pull request that carries an agent session trailer, an agent session link or a generated-with footer. `python .github/scripts/check-attribution.py origin/main..HEAD` runs the same check on your commits before you push.

The repository's license is [Apache 2.0](LICENSE).
