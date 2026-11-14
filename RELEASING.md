# Releasing rabbit-brain

A version tag on a reviewed commit releases that commit, and only GitHub's publishing job uploads it. [publish.yml](.github/workflows/publish.yml) builds the wheels and the source distribution, tests them, waits in the `pypi` environment for the maintainer's approval, then publishes them to [PyPI](https://pypi.org/project/rabbit-brain/) through PyPI's trusted publisher and attaches the same files, with checksums, to the tag's [GitHub release](https://github.com/rabbit-brain/rb/releases). No file is uploaded from any other machine, and no PyPI token is used.

## Before tagging

- Set the version in `pyproject.toml`, `src/rabbit_brain/__init__.py` and `rust/src/lib.rs`, and the matching Rust version in `rust/Cargo.toml` and its package entry in `rust/Cargo.lock` (`0.6.0-dev.0` for `0.6.0.dev0`). The workflow stops when the tag and the version differ.
- Date the version's `CHANGELOG.md` entry, as `## 0.6.0 (2026-10-20)`; the workflow stops at an undated one. Check the README, the manual and the guides against what the version does.
- Run the checks in [CONTRIBUTING.md](CONTRIBUTING.md), and keep the passing release check (tests.yml's `release-check` job: Windows and macOS on Python 3.12, Linux on Python 3.10, rb remove's round trip on all three) and native wheel runs for the exact commit you tag.
- The workflows run every action at a commit, with its version in a comment beside it, and the wheel builder at a fixed maturin version. Move one on purpose: its new commit and version together, then a native wheel run.

## What the workflow needs

| Setting | What it is |
| --- | --- |
| GitHub `pypi` environment | The environment the publishing job runs in, with the maintainer as its required reviewer and `v*` tags as its only deployment branches. The job waits there until the maintainer approves it: that approval is the go to publish. |
| PyPI trusted publisher | Owner `rabbit-brain`, repository `rb`, workflow `publish.yml`, environment `pypi`, for the `rabbit-brain` project. Publishing uses OpenID Connect, not an API token. Remove any API token for the project from PyPI's account settings, so an upload can come from this job alone. |
| `RB_POSTHOG_KEY`, `RB_POSTHOG_HOST` repository variables | The public project token and ingestion host built into release wheels. A tagged release needs both. Analytics stays off until the user enables it; see [PRIVACY.md](PRIVACY.md). |
| `RB_RELEASE_DENYLIST` repository secret | The release denylist, one literal phrase per line. The content check fails when it is missing or empty. |

Where these stand for the first release through the job:

- the PyPI trusted publisher above is in place for `rabbit-brain`: to confirm;
- the maintainer is the `pypi` environment's required reviewer, and a deployment branch policy limits the environment: stated, read from GitHub's API on 2026-11-15;
- the project's PyPI API token is removed once a release has gone through the job: to confirm.

## Tag the reviewed commit

Create `v<version>` on the reviewed commit, with the exact package version (a prerelease suffix included), and push that one tag. The publish workflow then:

1. Builds five native wheels and the source distribution.
2. Checks the tag against the package and binary versions, checks the package metadata, installs the built Linux and Windows wheels and builds rb from the source distribution alone, on Python 3.10, and runs `rb version`, `rabbit-brain version` and a short set of tests that drive the installed rb end to end (`WHEEL_TESTS` in publish.yml), the page rb page writes among them; the source distribution's run takes the tests from the source distribution itself. Each check stops after 20 minutes. They prove the wheels install and run, not the whole of rb. [tests.yml](.github/workflows/tests.yml) runs its release check on every push to `main` and every pull request: rb built on Windows and macOS and the tests of what differs there, and on Linux on Python 3.10 the same tests with the wheel tests, in at most 22.1 minutes on GitHub's runs so far; the full suites run on our own machines before the tag, and in tests.yml after it, daily and by hand. The publish workflow does not wait for tests.yml, so tag a commit whose release check passed (Before tagging).
3. Checks that the build carries its analytics destination. In a job of its own, which installs nothing and is the only one to receive the release denylist, it runs the content check's own tests, then checks the committed files and every built file against the denylist. It stops after 20 minutes too.
4. Waits in the `pypi` environment once the three test jobs and the content check pass. The maintainer approves it after reading the run (Before the approval, below), and the job publishes to PyPI through the trusted publisher.
5. Attaches the same files, `SHA256SUMS` and `THIRD-PARTY-NOTICES` (the licenses of the crates built into rb) to the GitHub release. Development, alpha, beta and release-candidate versions are marked as prereleases. The release notes are the version's CHANGELOG.md entry, then the install line and the checksums line.

## Before the approval

Read the run before you approve it: every check job passed, and the files it built are the ones you checked. `gh run download <run-id> --dir <empty-directory>` gives them. If anything is wrong, reject the deployment: nothing reaches PyPI, and the files stay only as the run's artifacts, which you then delete (`gh run delete <run-id>`). Never upload the files yourself instead: a second path to PyPI is what this job replaces.

The built files are also the run's artifacts, which a public repository offers for download as soon as they upload: the content check holds back PyPI and the GitHub release, not them. The check still reads them when a build fails. Delete a run whose check did not pass, whether it failed or did not run (`gh run delete <run-id>`).

## Verify the release

In a fresh virtual environment outside the checkout, install `rabbit-brain==<version>` with `--only-binary=:all:`. Check `rb version`, `rabbit-brain version`, `rb docs` and the SDK, and compare the downloaded files with `SHA256SUMS`.

Never move a published tag or reuse an uploaded file name. To correct a release that reached PyPI, release a new version.

## The daily run

tests.yml's full matrix runs after each tag, by hand, and once a day from the default branch's copy of tests.yml. GitHub turns a public repository's schedule off after 60 days with no activity in it, without a failure: counted from the 0.8.6 push (2026-11-13), the daily run stops about 2027-01-12 unless something reaches `main` before then.

Each job in tests.yml stops at twice the slowest time GitHub measured for it, rounded up to 5 minutes. The times come from two full runs, the 2026-11-14 daily run (43586178395) and the v0.8.6 tag (43498656381): the release check 22.1 minutes, so 45; `test` 76.4 (Python 3.13), so 155; `windows` 151.7, so 305; `leaving` 94.9 (macOS), so 190. Both runs failed, so the times are read again after the first green full run, and the timeouts and their pin in tests/test_release_workflows.py move with them.

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
