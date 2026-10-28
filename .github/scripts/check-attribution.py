#!/usr/bin/env python3
"""Fail when a commit or a pull request credits a coding agent.

A commit fails when its author or committer is an agent identity, or when its message carries a co-author trailer
that names an agent, an agent session trailer, a generated-with footer or an agent session link. A pull request fails
when its title or body carries any of these. Names and addresses are matched whole: an identity is an agent's when its
name is an agent's name or its address is an agent's, a co-author trailer when its name starts with an agent's name or
its address is an agent's, and a footer only as a line of its own. A contributor whose name or address only contains
an agent's name passes. With no revisions, the GitHub Actions event names what to check: the commits a push adds, or a
pull request's commits over its base, with its title and body. Revisions name commits as git rev-list takes them, for
a check before pushing:

    python .github/scripts/check-attribution.py origin/main..HEAD
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

# Matched whole: the names agents commit under, and the addresses (Anthropic's, and an agent's GitHub app).
AGENT_NAME = r"(?:claude(?: code)?|codex|chatgpt|cursor agent)(?: ?\[bot\])?"
AGENT_EMAIL = (r"(?:noreply@anthropic\.com"
               r"|(?:\d+\+)?(?:claude|codex|chatgpt|copilot|cursor|gemini)[a-z-]*\[bot\]@users\.noreply\.github\.com)")
AGENT_IDENTITY = re.compile(rf"{AGENT_NAME}[ \t]*<[^<>]*>|[^<>]*<{AGENT_EMAIL}>", re.IGNORECASE)
AGENT_TEXT = (
    ("a co-author trailer that names an agent", re.compile(
        rf"^[ \t]*co-authored-by:[ \t]*(?:(?:claude|codex|chatgpt|copilot|cursor|gemini)\b|[^<\n]*<{AGENT_EMAIL}>)",
        re.IGNORECASE | re.MULTILINE)),
    ("an agent session trailer", re.compile(r"^[ \t]*(?:claude|codex)-session:", re.IGNORECASE | re.MULTILINE)),
    ("a generated-with footer", re.compile(
        r"^[ \t]*[^\w\n]*[ \t]*generated[ \t]+(?:with|by)[ \t]+\[?(?:claude(?: code)?|codex|chatgpt)\]?"
        r"(?:\([^)\n]*\))?\.?[ \t]*$", re.IGNORECASE | re.MULTILINE)),
    ("an agent session link", re.compile(r"claude\.ai/code/session", re.IGNORECASE)),
)
COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
DELETED = re.compile(r"0+")


class CheckError(Exception):
    pass


def has_commit(repo, rev):
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{rev}^{{commit}}"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def commits(repo, revisions):
    """(id, author, committer, message) for every commit the revisions name, with identities as recorded."""
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "log.showSignature=false", "log", "--no-use-mailmap", "-z",
         "--format=%H%x00%an <%ae>%x00%cn <%ce>%x00%B", *revisions, "--"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise CheckError("git could not list the commits to check")
    fields = result.stdout.decode("utf-8", "replace").split("\0")
    if fields[-1] == "":
        fields.pop()
    # Four fields per commit, each led by its id; anything else is output this check does not understand.
    if len(fields) % 4 or not all(COMMIT_ID.fullmatch(field) for field in fields[::4]):
        raise CheckError("git log output could not be read")
    return [tuple(fields[n:n + 4]) for n in range(0, len(fields), 4)]


def event_revisions(repo, name, event, ref):
    """The revisions of the commits the event adds, or None when it adds none."""
    if name == "pull_request":
        base, head = event["pull_request"]["base"]["sha"], event["pull_request"]["head"]["sha"]
        if has_commit(repo, base) and has_commit(repo, head):
            return [head, "--not", base]
        # The checkout of a pull request is its merge commit: the base tip, then the head.
        if has_commit(repo, "HEAD^2"):
            return ["HEAD^2", "--not", "HEAD^1"]
        raise CheckError("the pull request's commits are not in the checkout")
    if name == "push":
        before, after = event.get("before") or "", event.get("after") or ""
        if not after or DELETED.fullmatch(after):
            return None
        if before and not DELETED.fullmatch(before) and has_commit(repo, before):
            return [after, "--not", before]
        # A new branch or tag, or a forced push whose old tip is gone: what the default branch does not hold yet.
        default = (event.get("repository") or {}).get("default_branch") or ""
        known = f"refs/remotes/origin/{default}"
        if default and ref != f"refs/heads/{default}" and has_commit(repo, known):
            return [after, "--not", known]
        return [after]
    raise CheckError(f"no rule for the {name or 'unnamed'} event")


def normalized(text):
    """The text with every line break as a newline, so a line of its own reads the same from any platform."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def findings(found, text):
    """What credits an agent. Nothing from a commit or a pull request is quoted, only commit ids."""
    out = []
    for commit, author, committer, message in found:
        if AGENT_IDENTITY.fullmatch(author) or AGENT_IDENTITY.fullmatch(committer):
            out.append(f"commit {commit[:12]}: the author or committer is an agent identity")
        out += [f"commit {commit[:12]}: the message carries {what}"
                for what, rule in AGENT_TEXT if rule.search(normalized(message))]
    out += [f"the pull request's title or body carries {what}"
            for what, rule in AGENT_TEXT if rule.search(normalized(text))]
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("revisions", nargs="*",
                        help="commits to check, as git rev-list takes them (default: the event's)")
    parser.add_argument("--repo", type=Path, default=Path("."),
                        help="the git checkout (default: the current directory)")
    args = parser.parse_args(argv)
    try:
        text = None
        revisions = args.revisions
        if not revisions:
            name, path = os.environ.get("GITHUB_EVENT_NAME", ""), os.environ.get("GITHUB_EVENT_PATH", "")
            if not path:
                raise CheckError("no revisions given and no GitHub Actions event to read")
            with open(path, encoding="utf-8") as source:
                event = json.load(source)
            revisions = event_revisions(args.repo, name, event, os.environ.get("GITHUB_REF", ""))
            if name == "pull_request":
                pull = event["pull_request"]
                text = "\n".join(part for part in (pull.get("title"), pull.get("body")) if isinstance(part, str))
        found = commits(args.repo, revisions) if revisions else []
        problems = findings(found, text or "")
    except CheckError as error:
        print(f"Attribution check failed: {error}.", file=sys.stderr)
        return 2
    except (OSError, ValueError, LookupError, TypeError, AttributeError):
        print("Attribution check failed: the event or the commits could not be read.", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(f"Attribution check failed: {problem}.", file=sys.stderr)
        print("Commit under your own name, remove what is named above, and push again.", file=sys.stderr)
        return 1
    checked = f"{len(found)} commit{'' if len(found) == 1 else 's'}"
    print(f"Attribution check passed: {checked}"
          + (", and the pull request's title and body." if text is not None else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
