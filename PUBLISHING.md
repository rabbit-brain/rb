# Publishing rabbit-brain

Maintainer notes. Users need none of this.

## What is where

- PyPI: https://pypi.org/project/rabbit-brain/ (0.1.1: importer, checks, receipts).
- Source: `github.com/rabbit-brain/rb`, branch `main`, version `0.2.0.dev1` (the runner). 0.2.0 ships from `main`.
- `dist/` and `releases/` are gitignored. `releases/rabbit-brain-<version>-src.zip` is the exact source a PyPI release was built from.

## Rules for anything public (README, PyPI page, AGENTS.md, commit messages)

- No paper title and no attribution to the paper until it is public. Then add the reference in one line at the end of the README and a `Paper` URL in `pyproject.toml`.
- No links to preview or mock sites. The homepage is the repo.
- No em dashes. Commit messages: one plain line, no trailers.

## Cutting a release

1. Bump the version in both places: `pyproject.toml` (`version = "..."`) and `src/rabbit_brain/__init__.py` (`__version__`). Add a CHANGELOG entry.
2. `pytest` (34 tests with torch and a RAFT checkout at `../raft`; the RAFT tests skip without them).
3. Build and check:

```sh
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/rabbit_brain-<version>*
```

4. Upload with `publish.sh` (it also pushes `main`), or by hand:

```sh
python -m twine upload dist/rabbit_brain-<version>*      # username: __token__   password: a PyPI API token
```

5. Verify from a clean environment: `python -m venv /tmp/v && /tmp/v/bin/pip install rabbit-brain && /tmp/v/bin/rb version`.
6. Tag: `git tag v<version> && git push origin v<version>`.

PyPI never lets a filename be uploaded twice, even after the release is deleted. A bad release is followed by a new patch version, not a re-upload.

Trusted publishing (GitHub Actions to PyPI, no token) is the plan for 0.2: https://docs.pypi.org/trusted-publishers/

## Running the tests

```sh
pip install -e ".[dev]"
pytest
```

`tests/fixtures/v5-expected.json` holds the outputs of the reference TypeScript implementation (`lib/comparison.ts`), dumped once with esbuild and node. The Python is tested against it number for number and byte for byte (report, verdict, import problems). If `lib/comparison.ts` changes, bundle it with esbuild and re-dump the fixture.
