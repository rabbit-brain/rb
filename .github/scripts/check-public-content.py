#!/usr/bin/env python3
"""Check committed files and unpacked distributions against the release denylist.

The denylist comes from RB_RELEASE_DENYLIST, one literal phrase per line. Nothing
from that list, matching content, or matching filenames is printed. Missing
configuration and unreadable inputs are failures, not successful scans.

--source names the top level of a git checkout; the files committed at HEAD are
checked as git stores them, whatever the working tree holds.

--history also checks what a push or a pull request adds: every commit message,
tag message, file and file name in its history, and a pull request's title and
body. With no revisions it reads the GitHub Actions event from the step's
environment (GITHUB_EVENT_NAME, GITHUB_REF and the SCAN_* variables the
workflow sets); with revisions, as git rev-list takes them, it checks those:

    python .github/scripts/check-public-content.py --source . --history origin/main..HEAD
"""

import argparse
import codecs
import gzip
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import zipfile
import zlib

try:
    import lzma
    LZMA_ERRORS = (lzma.LZMAError,)
except ImportError:  # a Python built without lzma: zipfile refuses an LZMA member with RuntimeError instead
    LZMA_ERRORS = ()

OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
NO_OBJECT = re.compile(r"0+")      # the id an event gives a ref that did not exist before, or no longer does


class ScanError(Exception):
    pass


class ContentMatch(Exception):
    pass


def git(root: Path, *args: str) -> bytes:
    """What git prints for these arguments in the checkout at root; a failure is a scan error that names nothing."""
    result = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ScanError("git could not read the checkout")
    return result.stdout


class Exactly:
    """The next `size` bytes of a stream, read as a file of their own."""

    def __init__(self, stream, size: int):
        self.stream, self.left = stream, size

    def read(self, n: int = -1) -> bytes:
        n = self.left if n < 0 else min(n, self.left)
        data = self.stream.read(n) if n else b""
        if len(data) != n:
            raise ScanError("git stopped before an object was read whole")
        self.left -= n
        return data


def objects(root: Path, ids):
    """(id, type, contents as a file) for each object id, as git cat-file --batch gives them, one at a time."""
    with subprocess.Popen(["git", "-C", str(root), "cat-file", "--batch"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as batch:
        for oid in ids:
            batch.stdin.write(oid + b"\n")
            batch.stdin.flush()
            header = batch.stdout.readline().split()
            if len(header) != 3 or header[0] != oid or not header[2].isdigit():
                raise ScanError("git could not read a committed object")
            contents = Exactly(batch.stdout, int(header[2]))
            yield oid, header[1].decode("ascii"), contents
            while contents.read(65536):
                pass
            if batch.stdout.read(1) != b"\n":
                raise ScanError("git could not read a committed object")
        batch.stdin.close()
        if batch.wait():
            raise ScanError("git could not read a committed object")


def has_object(root: Path, rev: str) -> bool:
    return subprocess.run(["git", "-C", str(root), "cat-file", "-e", rev], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode == 0


def event_scope(root: Path, environ) -> tuple:
    """What a GitHub Actions event adds: (revisions as git rev-list takes them, text it carries, what that text is).
    A push adds its commits, with a pushed tag's own object, and its ref's name; a pull request adds its commits,
    its title and its body."""
    def object_id(key):
        value = environ.get(key, "")
        if value and not OBJECT_ID.fullmatch(value):
            raise ScanError(f"{key} is not an object id")
        return value

    name = environ.get("GITHUB_EVENT_NAME", "")
    if name == "pull_request":
        base, head = object_id("SCAN_PR_BASE"), object_id("SCAN_PR_HEAD")
        text = "\n".join(environ.get(key, "") for key in ("SCAN_PR_TITLE", "SCAN_PR_BODY"))
        said = "the pull request's title and body"
        if base and head and has_object(root, base) and has_object(root, head):
            return [head, "--not", base], text, said
        # The checkout of a pull request is its merge commit: the base tip, then the head.
        if has_object(root, "HEAD^2"):
            return ["HEAD^2", "--not", "HEAD^1"], text, said
        raise ScanError("the pull request's commits are not in the checkout")
    if name == "push":
        ref = environ.get("GITHUB_REF", "")
        before, after = object_id("SCAN_BEFORE"), object_id("SCAN_AFTER")
        if not after or NO_OBJECT.fullmatch(after):
            return [], ref, "the ref's name"                 # a deleted branch or tag adds nothing
        if not has_object(root, after):
            raise ScanError("the pushed commits are not in the checkout")
        if before and not NO_OBJECT.fullmatch(before) and has_object(root, before):
            return [after, "--not", before], ref, "the ref's name"
        # A new branch or tag, or a forced push whose old tip is gone: what the default branch does not hold. Never
        # --not --remotes: the checkout's remote branches hold the pushed commits already.
        default = environ.get("SCAN_DEFAULT_BRANCH", "")
        known = f"refs/remotes/origin/{default}"
        if default and ref != f"refs/heads/{default}" and has_object(root, known):
            return [after, "--not", known], ref, "the ref's name"
        return [after], ref, "the ref's name"
    raise ScanError(f"no rule for the {name or 'unnamed'} event")


class Scanner:
    def __init__(self, words: str):
        self.phrases = tuple(line.strip().casefold() for line in words.splitlines() if line.strip())
        if not self.phrases:
            raise ScanError("RB_RELEASE_DENYLIST is missing or empty; content was not checked")
        self.overlap = max(map(len, self.phrases)) - 1
        self.files = 0
        self.commits = self.tags = self.blobs = 0

    def text(self, value: str):
        folded = value.casefold()
        if any(phrase in folded for phrase in self.phrases):
            raise ContentMatch("blocked content found; matching text and path omitted")

    def stream(self, source, *, count=True):
        # Compiled Rust strings and the source documents are UTF-8. Incremental
        # decoding preserves text and phrases that cross a read boundary; binary
        # bytes outside UTF-8 are ignored without skipping the rest of the file.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        tail = ""
        while True:
            block = source.read(65536)
            value = tail + decoder.decode(block, final=not block).casefold()
            self.text(value)
            tail = value[-self.overlap:] if self.overlap else ""
            if not block:
                break
        if count:
            self.files += 1

    def source(self, root: Path):
        # A subdirectory would leave the rest of the checkout unchecked.
        top = os.fsdecode(git(root, "rev-parse", "--show-toplevel").strip())
        if not top or not os.path.samefile(top, root):
            raise ScanError("--source is not the top level of a git checkout; the files outside it were not checked")
        listing = git(root, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
        blobs = []
        for entry in filter(None, listing.split(b"\0")):
            info, _, name = entry.partition(b"\t")
            mode, kind, oid = info.split(b" ")
            self.text(os.fsdecode(name))
            # A link's target or a submodule's commit is not a file this check reads; it is reviewed by hand.
            if kind != b"blob" or mode not in (b"100644", b"100755"):
                raise ScanError("tracked source contains a non-regular file; review it explicitly")
            blobs.append(oid)
        if not blobs:
            raise ScanError("could not enumerate tracked source files")
        for _, _, contents in objects(root, blobs):
            self.stream(contents)

    def history(self, root: Path, revisions):
        """Every commit message, tag message, file and file name in the history the revisions add."""
        if git(root, "rev-parse", "--is-shallow-repository").strip() != b"false":
            raise ScanError("the checkout is shallow, so its history was not checked; fetch all of it (fetch-depth: 0)")
        ids = []
        for line in git(root, "rev-list", "--objects", *revisions, "--").splitlines():
            oid, _, name = line.partition(b" ")
            if not OBJECT_ID.fullmatch(oid.decode("ascii", "replace")):
                raise ScanError("git rev-list output could not be read")
            self.text(os.fsdecode(name))                  # the path a file or folder was first found at
            ids.append(oid)
        for oid, kind, contents in objects(root, ids):
            if kind == "blob":
                self.stream(contents, count=False)
                self.blobs += 1
            elif kind == "tree":
                # '<mode> <name>\0<id>' for each entry: every name, where rev-list stops a path at a line break
                tree, at = contents.read(), 0
                while at < len(tree):
                    space = tree.index(b" ", at)
                    end = tree.index(b"\0", space)
                    self.text(os.fsdecode(tree[space + 1:end]))
                    at = end + 1 + len(oid) // 2
            elif kind == "commit":
                self.text(contents.read().partition(b"\n\n")[2].decode("utf-8", "ignore"))   # its message, as %B
                self.commits += 1
            elif kind == "tag":
                headers, _, message = contents.read().partition(b"\n\n")
                named = [line for line in headers.split(b"\n") if line.startswith(b"tag ")]
                self.text(b"\n".join([*named, message]).decode("utf-8", "ignore"))      # its name and message
                self.tags += 1

    def artifacts(self, root: Path):
        paths = sorted(root.iterdir())
        if not paths:
            raise ScanError("the artifact directory is empty; distributions were not checked")
        for path in paths:
            self.text(path.name)
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ScanError("the artifact directory contains a non-regular file")
            before = self.files
            # Also check archive metadata, including ZIP comments, which is not
            # exposed as a member. Compressed payloads are checked below.
            with path.open("rb") as source:
                self.stream(source, count=False)
            if path.suffix == ".whl":
                with zipfile.ZipFile(path) as archive:
                    for member in archive.infolist():
                        self.text(member.filename)
                        if member.is_dir():
                            continue
                        if stat.S_ISLNK(member.external_attr >> 16):
                            raise ScanError("a wheel contains a symbolic link")
                        with archive.open(member) as source:
                            self.stream(source)
            elif path.name.endswith(".tar.gz"):
                # Read through the gzip trailer too: tar readers may stop at the
                # end-of-archive marker before verifying its CRC or truncation.
                with gzip.open(path, "rb") as source:
                    self.stream(source, count=False)
                with tarfile.open(path, "r:gz") as archive:
                    for member in archive:
                        self.text(member.name)
                        if member.isdir():
                            continue
                        if not member.isfile():
                            raise ScanError("a source distribution contains a non-regular file")
                        source = archive.extractfile(member)
                        if source is None:
                            raise ScanError("a source distribution member could not be read")
                        with source:
                            self.stream(source)
            else:
                raise ScanError("unexpected artifact; only wheels and .tar.gz source distributions are accepted")
            if self.files == before:
                raise ScanError("a distribution contains no files")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True,
                        help="top level of the git checkout whose committed files are checked")
    parser.add_argument("--artifacts", type=Path, help="directory containing the wheels and sdist to check")
    parser.add_argument("--history", nargs="*", metavar="REVISION",
                        help="also check the history these revisions add (default: what the GitHub Actions event adds)")
    args = parser.parse_args(argv)
    try:
        scanner = Scanner(os.environ.get("RB_RELEASE_DENYLIST", ""))
        scanner.source(args.source)
        source_count = scanner.files
        if args.artifacts is not None:
            scanner.artifacts(args.artifacts)
        checked = f"{source_count} tracked files, {scanner.files - source_count} distribution members"
        if args.history is not None:
            revisions, text, said = (args.history, "", "") if args.history else event_scope(args.source, os.environ)
            if revisions:
                scanner.history(args.source, revisions)
            scanner.text(text)
            checked += (f", and in the history {scanner.commits} commit messages, {scanner.tags} tag messages and "
                        f"{scanner.blobs} files" + (f", with {said}" if said else ""))
        print(f"Public-content check passed: {checked}.")
        return 0
    except ContentMatch as error:
        print(f"Public-content check failed: {error}.", file=sys.stderr)
        return 1
    except ScanError as error:
        print(f"Public-content check failed: {error}.", file=sys.stderr)
        return 2
    except (OSError, EOFError, ValueError, RuntimeError, tarfile.TarError, zipfile.BadZipFile, zlib.error,
            *LZMA_ERRORS):
        # Exception messages can contain a forbidden path or archive member.
        print("Public-content check failed: an input could not be read completely; "
              "no successful audit was produced.", file=sys.stderr)
        return 2
    except Exception:
        # Anything else is a failed check, never a match (exit 1) and never its message.
        print("Public-content check failed: the check stopped on an unexpected error; "
              "no successful audit was produced.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
