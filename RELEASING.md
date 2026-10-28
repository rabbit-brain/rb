# Releasing rabbit-brain

A version tag on a reviewed commit releases that commit. [publish.yml](.github/workflows/publish.yml) builds the wheels and the source distribution, publishes them to [PyPI](https://pypi.org/project/rabbit-brain/), and attaches the same files, with checksums, to the tag's [GitHub release](https://github.com/rabbit-brain/rb/releases). Pushing a release tag publishes.

## Before tagging

- Set the version in `pyproject.toml`, `src/rabbit_brain/__init__.py` and `rust/src/lib.rs`, and the matching Rust version in `rust/Cargo.toml` and its package entry in `rust/Cargo.lock` (`0.6.0-dev.0` for `0.6.0.dev0`). The workflow stops when the tag and the version differ.
- Date the version's `CHANGELOG.md` entry, as `## 0.6.0 (2026-10-20)`; the workflow stops at an undated one. Check the README, the manual and the guides against what the version does.
- Run the checks in [CONTRIBUTING.md](CONTRIBUTING.md), and keep the passing CI and native wheel runs for the exact commit you tag.
- The workflows run every action at a commit, with its version in a comment beside it, and the wheel builder at a fixed maturin version. Move one on purpose: its new commit and version together, then a native wheel run.

## What the workflow needs

| Setting | What it is |
| --- | --- |
| GitHub `pypi` environment | The environment the publishing job runs in. Add reviewer protection there if you want it. |
| PyPI trusted publisher | Owner `rabbit-brain`, repository `rb`, workflow `publish.yml`, environment `pypi`, for the `rabbit-brain` project. Publishing uses OpenID Connect, not an API token. |
| `RB_POSTHOG_KEY`, `RB_POSTHOG_HOST` repository variables | The public project token and ingestion host built into release wheels. A tagged release needs both. Analytics stays off until the user enables it; see [PRIVACY.md](PRIVACY.md). |
| `RB_RELEASE_DENYLIST` repository secret | The release denylist, one literal phrase per line. The content check fails when it is missing or empty. |

## Tag the reviewed commit

Create `v<version>` on the reviewed commit, with the exact package version (a prerelease suffix included), and push that one tag. The publish workflow then:

1. Builds five native wheels and the source distribution.
2. Checks the tag against the package and binary versions, checks the package metadata, and runs the suite against the installed Linux and Windows wheels.
3. Checks that the build carries its analytics destination. In a job of its own, which installs nothing and is the only one to receive the release denylist, it runs the content check's own tests, then checks the committed files and every built file against the denylist.
4. Publishes to PyPI through the trusted publisher once both test jobs and the content check pass.
5. Attaches the same files, `SHA256SUMS` and `THIRD-PARTY-NOTICES` (the licenses of the crates built into rb) to the GitHub release. Development, alpha, beta and release-candidate versions are marked as prereleases.

The built files are also the run's artifacts, which a public repository offers for download as soon as they upload: the content check holds back PyPI and the GitHub release, not them. The check still reads them when a build fails. Delete a run whose check did not pass, whether it failed or did not run (`gh run delete <run-id>`).

## Verify the release

In a fresh virtual environment outside the checkout, install `rabbit-brain==<version>` with `--only-binary=:all:`. Check `rb version`, `rabbit-brain version`, `rb docs` and the SDK, and compare the downloaded files with `SHA256SUMS`.

Never move a published tag or reuse an uploaded file name. To correct a release that reached PyPI, release a new version.

## Trial packages

The **build wheels** workflow builds the same five wheels and source distribution for testers, with no tag and no release:

```sh
gh workflow run build-wheels.yml --ref <branch>
gh run download <run-id> -n wheel-<platform> --dir <empty-directory>
```

`<platform>` is `linux-x86_64`, `linux-aarch64`, `macos-aarch64`, `macos-x86_64` or `windows-x86_64`. Install the wheel with `python -m pip install --only-binary=:all: <wheel>`.

After the builds, a job of its own checks the committed files and every built file against the release denylist, as a release does, and still runs when a build fails. The files upload before that check, so on a public repository anyone signed in to GitHub can download them at once, whatever it finds. Build trial packages only from a commit the content check has passed, and delete any run whose check did not pass, whether it failed or did not run (`gh run delete <run-id>`).

## The content check

[check-public-content.py](.github/scripts/check-public-content.py) checks text against the release denylist. It prints counts and generic messages only, never a phrase or where it was found. It exits 1 on a match, and 2 when it could not check everything, which is never a pass.

On every push and same-repository pull request, the **public content** workflow checks the files committed at the pushed commit and everything the push or pull request adds: each commit message, tag message, file and file name in its history, and a pull request's title and body. This is detection, not prevention: it runs after the push, so on a public repository what it finds is already public. Fork pull requests run only the check's own tests; their merge is checked when it is pushed.

Before you push, run the same check over your commits, with the denylist in `RB_RELEASE_DENYLIST`:

```sh
python .github/scripts/check-public-content.py --source . --history origin/main..HEAD
```

`--source` is the top level of the checkout, and the check reads the files committed there, not the working tree.
