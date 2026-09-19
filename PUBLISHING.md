# Publishing rabbit-brain

Maintainer notes. Users need none of this.

## What is where

- PyPI: https://pypi.org/project/rabbit-brain/ (0.1.1: importer, checks, receipts; 0.2.0: the runner).
- Source: `github.com/rabbit-brain/rb`, branch `main`. Releases are built and published by GitHub Actions from a tag.
- `dist/` and `releases/` are gitignored. `releases/rabbit-brain-<version>-src.zip` is the exact source a release was built from (0.1.x, built by hand); from 0.2.0 the tag is the source.

## Rules for anything public (README, PyPI page, AGENTS.md, commit messages)

- No paper title and no attribution to the paper until it is public. Then add the reference in one line at the end of the README and a `Paper` URL in `pyproject.toml`. The publish workflow greps the release for the title, the venue and the mock-site domains and fails if it finds one.
- No links to preview or mock sites. The homepage is the repo.
- No em dashes. Commit messages: one plain line, no trailers.

## Cutting a release

1. Bump the version in both places: `pyproject.toml` (`version = "..."`) and `src/rabbit_brain/__init__.py` (`__version__`). Date the CHANGELOG entry. Update the status line at the top of the README if what the release contains changed.
2. `pytest` (37 tests with torch and a RAFT checkout at `../raft` or `RB_RAFT_PATH`; the RAFT tests skip without them). `.github/workflows/tests.yml` runs the same suite on every push to `main`.
3. Commit, tag, push the tag:

```sh
git add -A && git commit -m "0.2.0" && git push
git tag v0.2.0 && git push origin v0.2.0
```

4. `.github/workflows/publish.yml` builds the sdist and wheel, checks that the tag matches `pyproject.toml`, runs the tests against the built wheel, runs the public-content grep, and publishes through PyPI's trusted publishing (OpenID Connect from the `pypi` environment; no token exists anywhere). Watch it at https://github.com/rabbit-brain/rb/actions.
5. Verify from a clean environment: `python -m venv /tmp/v && /tmp/v/bin/pip install rabbit-brain==0.2.0 && /tmp/v/bin/rb version`.

PyPI never lets a filename be uploaded twice, even after the release is deleted. A bad release is followed by a new patch version, not a re-upload. If the publish job fails after the tag exists, fix `main`, delete the tag (`git tag -d v0.2.0 && git push origin :refs/tags/v0.2.0`) and tag again.

### One-time setup of trusted publishing (done for 0.2.0)

On PyPI, logged in as the project owner: https://pypi.org/manage/project/rabbit-brain/settings/publishing/ > "Add a new publisher" > GitHub: owner `rabbit-brain`, repository `rb`, workflow `publish.yml`, environment `pypi`. On GitHub the `pypi` environment is created the first time the workflow references it; optionally protect it (Settings > Environments > pypi > required reviewers) so a tag push needs a click before it publishes.

`publish.sh` is the 0.1-era path (twine with an API token from a temporary venv); it still works as a fallback but is not needed.

## Running the tests

```sh
pip install -e ".[dev]" numpy pillow
pytest
```

`tests/fixtures/v5-expected.json` holds the outputs of the reference TypeScript implementation (`lib/comparison.ts`), dumped once with esbuild and node. The Python is tested against it number for number and byte for byte (report, verdict, import problems). If `lib/comparison.ts` changes, bundle it with esbuild and re-dump the fixture.
