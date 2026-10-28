"""Regression tests for the release guard; no project build or credentials needed."""

import contextlib
import gzip
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

try:
    import lzma
except ImportError:
    lzma = None


SCRIPT = Path(__file__).with_name("check-public-content.py")
spec = importlib.util.spec_from_file_location("public_content", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

# What a workflow step gives the scanner of its event; a test sets its own and never inherits the runner's.
EVENT = ("GITHUB_EVENT_NAME", "GITHUB_REF", "SCAN_BEFORE", "SCAN_AFTER", "SCAN_DEFAULT_BRANCH", "SCAN_PR_BASE",
         "SCAN_PR_HEAD", "SCAN_PR_TITLE", "SCAN_PR_BODY")
NONE = "0" * 40
MISSING = "1" * 40


class PublicContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = self.root / "gitconfig"
        config.write_text("[init]\n\tdefaultBranch = main\n", encoding="utf-8")
        environment = patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        self.source = self.root / "source"
        self.source.mkdir()
        self.artifacts = self.root / "dist"
        self.artifacts.mkdir()
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        # the scratch repository's own identity, for its commits alone
        self.git("config", "user.name", "Ana Example")
        self.git("config", "user.email", "ana@example.com")
        self.track("README.md", b"public documentation")

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.source), *args], check=True, stdout=subprocess.PIPE, text=True)
        return result.stdout.strip()

    def track(self, name, value):
        """The file, written, committed and so checked."""
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        self.git("add", "--", name)
        self.git("commit", "-q", "-m", "Update the file")
        return path

    def wheel(self, content, name="pkg/data.txt"):
        with zipfile.ZipFile(self.artifacts / "package.whl", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, content)

    def sdist(self, content, name="package/README.md"):
        with tarfile.open(self.artifacts / "package.tar.gz", "w:gz") as archive:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))

    def run_guard(self, words="\n forbidden phrase \r\n", artifacts=False, source=None, history=None, event=None):
        output = io.StringIO()
        args = ["--source", str(self.source if source is None else source)]
        if artifacts:
            args += ["--artifacts", str(self.artifacts)]
        if history is not None:
            args += ["--history", *history]
        with patch.dict(os.environ, {"RB_RELEASE_DENYLIST": words}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            for key in EVENT:
                os.environ.pop(key, None)
            os.environ.update(event or {})
            status = guard.main(args)
        self.assertNotIn("forbidden phrase", output.getvalue().casefold())
        return status, output.getvalue()

    def commit(self, message):
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def push(self, before, after, ref="refs/heads/main", words="forbidden phrase"):
        event = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": ref, "SCAN_BEFORE": before, "SCAN_AFTER": after,
                 "SCAN_DEFAULT_BRANCH": "main"}
        return self.run_guard(words=words, history=[], event=event)

    def pull_request(self, base, head, title="Add a criterion", body="What changed and the checks run."):
        event = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/1/merge", "SCAN_PR_BASE": base,
                 "SCAN_PR_HEAD": head, "SCAN_PR_TITLE": title, "SCAN_PR_BODY": body}
        return self.run_guard(history=[], event=event)

    def branch(self, name="topic"):
        """A branch off main, with main as the checkout's copy of the default branch."""
        self.git("update-ref", "refs/remotes/origin/main", "main")
        self.git("checkout", "-q", "-b", name)

    def test_clean_source_and_both_distribution_formats(self):
        self.wheel(b"public binary\0documentation")
        self.sdist(b"public source")
        status, output = self.run_guard(artifacts=True)
        self.assertEqual(status, 0)
        self.assertIn("1 tracked files, 2 distribution members", output)

    def test_empty_configuration_fails(self):
        for words in ["", " \r\n\t"]:
            with self.subTest(words=words):
                self.assertEqual(self.run_guard(words)[0], 2)

    def test_source_scan_includes_hidden_workflows_and_bench(self):
        for name in [".github/workflows/check.yml", "bench/example.txt", "docs/guide.md", "schema/test.json"]:
            with self.subTest(name=name):
                self.track(name, b"FORBIDDEN PHRASE")
                self.assertEqual(self.run_guard()[0], 1)
                self.track(name, b"public")

    def test_phrase_across_binary_read_boundary(self):
        self.track("binary", b"\xff" * 65532 + b"FoRbIdDeN PhRaSe\0")
        self.assertEqual(self.run_guard()[0], 1)

    def test_unicode_casefold_across_read_boundary(self):
        self.track("unicode", b"x" * 65535 + "Straße".encode())
        self.assertEqual(self.run_guard(words="STRASSE")[0], 1)

    def test_literal_not_regular_expression(self):
        self.track("text", b"abc anything xyz")
        self.assertEqual(self.run_guard(words="abc.*xyz")[0], 0)

    def test_a_committed_file_git_cannot_read_is_scan_error(self):
        blob = self.git("rev-parse", "HEAD:README.md")
        stored = self.source / ".git" / "objects" / blob[:2] / blob[2:]
        stored.chmod(0o644)
        stored.unlink()
        self.assertEqual(self.run_guard()[0], 2)

    def test_the_committed_file_is_checked_not_the_working_tree(self):
        self.track("README.md", b"FORBIDDEN PHRASE")
        (self.source / "README.md").write_bytes(b"public documentation")
        self.assertEqual(self.run_guard()[0], 1)
        self.track("README.md", b"public documentation")
        (self.source / "README.md").write_bytes(b"FORBIDDEN PHRASE")      # not committed, so not published
        self.assertEqual(self.run_guard()[0], 0)

    def test_a_subdirectory_is_refused(self):
        self.track("rust/code.rs", b"public code")
        self.track("README.md", b"FORBIDDEN PHRASE")
        status, output = self.run_guard(source=self.source / "rust")
        self.assertEqual(status, 2)
        self.assertIn("not the top level", output)
        cwd = os.getcwd()
        os.chdir(self.source / "rust")
        try:
            self.assertEqual(self.run_guard(source=Path("."))[0], 2)
        finally:
            os.chdir(cwd)
        self.assertEqual(self.run_guard()[0], 1)

    def test_a_checkout_with_no_commit_fails(self):
        empty = self.root / "empty"
        subprocess.run(["git", "init", "-q", str(empty)], check=True)
        self.assertEqual(self.run_guard(source=empty)[0], 2)

    def test_a_submodule_is_reviewed_by_hand(self):
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.git('rev-parse', 'HEAD')},vendored")
        self.git("commit", "-q", "-m", "Add a submodule")
        self.assertEqual(self.run_guard()[0], 2)

    def test_git_failure_is_scan_error(self):
        with patch.object(guard.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"error")):
            self.assertEqual(self.run_guard()[0], 2)

    def test_unreadable_input_is_scan_error(self):
        with patch.object(guard.Scanner, "stream", side_effect=PermissionError("private filename")):
            status, output = self.run_guard()
            self.assertEqual(status, 2)
            self.assertNotIn("private filename", output)

    def test_a_committed_link_is_reviewed_by_hand(self):
        path = self.source / "link"
        try:
            path.symlink_to(self.root / "outside")
        except OSError:
            self.skipTest("symlinks unavailable")
        self.git("add", "--", "link")
        self.git("commit", "-q", "-m", "Add a link")
        self.assertEqual(self.run_guard()[0], 2)

    def test_compressed_wheel_match(self):
        self.wheel(b"FORBIDDEN PHRASE")
        self.assertEqual(self.run_guard(artifacts=True)[0], 1)

    def test_compressed_sdist_match(self):
        self.sdist(b"FORBIDDEN PHRASE")
        self.assertEqual(self.run_guard(artifacts=True)[0], 1)

    def test_filename_matches_without_printing_path(self):
        self.wheel(b"public", "forbidden phrase.txt")
        self.assertEqual(self.run_guard(artifacts=True)[0], 1)

    def test_archive_comment_is_checked(self):
        self.wheel(b"public")
        with zipfile.ZipFile(self.artifacts / "package.whl", "a") as archive:
            archive.comment = b"FORBIDDEN PHRASE"
        self.assertEqual(self.run_guard(artifacts=True)[0], 1)

    def test_empty_artifact_directory_fails(self):
        self.assertEqual(self.run_guard(artifacts=True)[0], 2)

    def test_malformed_archives_fail(self):
        for name in ["bad.whl", "bad.tar.gz"]:
            with self.subTest(name=name):
                path = self.artifacts / name
                path.write_bytes(b"invalid archive")
                self.assertEqual(self.run_guard(artifacts=True)[0], 2)
                path.unlink()

    def test_empty_distribution_fails(self):
        with zipfile.ZipFile(self.artifacts / "empty.whl", "w"):
            pass
        self.assertEqual(self.run_guard(artifacts=True)[0], 2)

    def test_truncated_gzip_trailer_fails(self):
        self.sdist(b"public source")
        path = self.artifacts / "package.tar.gz"
        path.write_bytes(path.read_bytes()[:-4])
        self.assertEqual(self.run_guard(artifacts=True)[0], 2)

    def corrupt_wheel_member(self, method, keep):
        """A wheel whose one member's compressed bytes, past the first `keep`, cannot be decompressed."""
        path = self.artifacts / "package.whl"
        with zipfile.ZipFile(path, "w", method) as archive:
            archive.writestr("pkg/data.txt", b"public documentation " * 200)
        with zipfile.ZipFile(path) as archive:
            member = archive.getinfo("pkg/data.txt")
        data = bytearray(path.read_bytes())
        start = member.header_offset + 30 + len(member.filename.encode()) + len(member.extra)
        # A deflate block of the reserved type 3, or an LZMA stream whose first byte is not 0, never decodes.
        data[start + keep:start + member.compress_size] = b"\x07" * (member.compress_size - keep)
        path.write_bytes(bytes(data))

    def test_corrupted_deflate_wheel_member_fails(self):
        self.corrupt_wheel_member(zipfile.ZIP_DEFLATED, 0)
        self.assertEqual(self.run_guard(artifacts=True)[0], 2)

    @unittest.skipIf(lzma is None, "this Python has no lzma")
    def test_corrupted_lzma_wheel_member_fails(self):
        self.corrupt_wheel_member(zipfile.ZIP_LZMA, 9)       # keep zipfile's 4-byte header and the 5 LZMA properties
        self.assertEqual(self.run_guard(artifacts=True)[0], 2)

    def test_corrupted_gzip_stream_fails(self):
        tar = io.BytesIO()
        with tarfile.open(fileobj=tar, mode="w") as archive:
            member = tarfile.TarInfo("package/README.md")
            member.size = len(b"public source")
            archive.addfile(member, io.BytesIO(b"public source"))
        data = bytearray(gzip.compress(tar.getvalue(), mtime=0))
        data[10] = 0x07                                       # past the 10-byte header: a block of the reserved type
        (self.artifacts / "package.tar.gz").write_bytes(bytes(data))
        self.assertEqual(self.run_guard(artifacts=True)[0], 2)

    def test_an_unexpected_error_is_a_failed_check_that_names_nothing(self):
        with patch.object(guard.Scanner, "stream", side_effect=KeyError("private filename")):
            status, output = self.run_guard()
        self.assertEqual(status, 2)
        self.assertNotIn("private filename", output)

    def test_unknown_artifact_is_not_silently_skipped(self):
        (self.artifacts / "other.zip").write_bytes(b"public")
        self.assertEqual(self.run_guard(artifacts=True)[0], 2)

    def test_sdist_link_fails(self):
        with tarfile.open(self.artifacts / "package.tar.gz", "w:gz") as archive:
            member = tarfile.TarInfo("link")
            member.type = tarfile.SYMTYPE
            member.linkname = "/outside"
            archive.addfile(member)
        self.assertEqual(self.run_guard(artifacts=True)[0], 2)

    def test_the_pushed_commit_messages_are_checked(self):
        start = self.git("rev-parse", "HEAD")
        earlier = self.commit("Update the manual\n\nFORBIDDEN PHRASE in the body")
        after = self.commit("Fix a typo")
        self.assertEqual(self.run_guard()[0], 0)                  # the files at HEAD hold nothing
        status, output = self.push(start, after)
        self.assertEqual(status, 1, output)
        status, output = self.push(earlier, after)                 # only what this push adds
        self.assertEqual(status, 0, output)
        self.assertIn("in the history 1 commit messages, 0 tag messages and 0 files, with the ref's name.", output)

    def test_a_file_the_push_added_and_removed_is_checked_with_its_name(self):
        for name, value in [("notes.txt", b"FORBIDDEN PHRASE"), ("forbidden phrase.txt", b"public"),
                            ("docs/forbidden phrase/guide.md", b"public")]:
            with self.subTest(name=name):
                start = self.git("rev-parse", "HEAD")
                self.track(name, value)
                self.git("rm", "-q", "--", name)
                after = self.commit("Remove the file")
                self.assertEqual(self.run_guard()[0], 0)
                self.assertEqual(self.push(start, after)[0], 1)

    def test_a_file_name_with_a_line_break_is_checked_whole(self):
        # git rev-list stops a path at a line break; the trees the push adds hold the whole name
        start = self.git("rev-parse", "HEAD")
        blob = subprocess.run(["git", "-C", str(self.source), "hash-object", "-w", "--stdin"], input=b"public",
                              check=True, stdout=subprocess.PIPE).stdout.decode("ascii").strip()
        name = "note\nforbidden phrase.txt"
        try:      # written to the index alone, so no file system has to hold the name
            self.git("-c", "core.protectNTFS=false", "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
        except subprocess.CalledProcessError:
            self.skipTest("this git refuses a line break in a path")
        self.git("commit", "-q", "-m", "Add a note")
        self.git("-c", "core.protectNTFS=false", "update-index", "--force-remove", name)
        after = self.commit("Remove the note")
        self.assertEqual(self.run_guard()[0], 0)
        self.assertEqual(self.push(start, after)[0], 1)

    def test_a_new_branch_is_checked_over_the_default_branch_and_never_its_own_remote_copy(self):
        self.commit("Update the manual\n\nFORBIDDEN PHRASE")      # on main: checked when main was pushed
        self.branch()
        after = self.commit("Add a criterion")
        self.git("update-ref", "refs/remotes/origin/topic", after)      # a CI checkout holds the pushed branch
        status, output = self.push(NONE, after, "refs/heads/topic")
        self.assertEqual(status, 0, output)
        self.assertIn("in the history 1 commit messages", output)
        after = self.commit("Add a run\n\nFORBIDDEN PHRASE")
        self.git("update-ref", "refs/remotes/origin/topic", after)
        self.assertEqual(self.push(NONE, after, "refs/heads/topic")[0], 1)

    def test_the_first_push_of_the_default_branch_checks_its_whole_history(self):
        self.commit("Start\n\nFORBIDDEN PHRASE")
        after = self.commit("Update the manual")
        self.git("update-ref", "refs/remotes/origin/main", "main")
        self.assertEqual(self.push(NONE, after)[0], 1)

    def test_a_forced_push_whose_old_tip_is_gone_is_checked_over_the_default_branch(self):
        self.branch()
        after = self.commit("Update the manual\n\nFORBIDDEN PHRASE")
        self.assertEqual(self.push(MISSING, after, "refs/heads/topic")[0], 1)

    def test_a_deleted_ref_adds_nothing(self):
        status, output = self.push(self.git("rev-parse", "HEAD"), NONE, "refs/heads/topic")
        self.assertEqual(status, 0, output)

    def test_a_pushed_tag_is_checked_by_its_message_and_name(self):
        commit = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", "main")
        self.git("tag", "-a", "v1", "-m", "Release notes\n\nFORBIDDEN PHRASE")
        tag = self.git("rev-parse", "refs/tags/v1")
        self.assertEqual(self.push(NONE, tag, "refs/tags/v1")[0], 1)
        self.git("update-ref", "refs/tags/v1", commit)            # a checkout that left the tag as its commit
        self.assertEqual(self.push(NONE, tag, "refs/tags/v1")[0], 1)
        self.git("tag", "-a", "v2", "-m", "Release notes")
        status, output = self.push(NONE, self.git("rev-parse", "refs/tags/v2"), "refs/tags/v2")
        self.assertEqual(status, 0, output)
        self.assertIn("0 commit messages, 1 tag messages and 0 files", output)
        self.git("tag", "v3")
        self.assertEqual(self.push(NONE, commit, "refs/tags/v3")[0], 0)
        self.git("tag", "-a", "release-made-up", "-m", "Release notes")
        tag = self.git("rev-parse", "refs/tags/release-made-up")
        self.assertEqual(self.push(NONE, tag, "refs/tags/release-made-up", words="made-up")[0], 1)
        self.git("tag", "release-made-up-light")
        self.assertEqual(self.push(NONE, commit, "refs/tags/release-made-up-light", words="made-up")[0], 1)

    def test_a_pull_request_is_checked_by_its_commits_title_and_body(self):
        base = self.git("rev-parse", "HEAD")
        self.branch()
        head = self.commit("Add a criterion")
        status, output = self.pull_request(base, head)
        self.assertEqual(status, 0, output)
        self.assertIn("1 commit messages, 0 tag messages and 0 files, with the pull request's title and body.", output)
        for title, body in [("FORBIDDEN PHRASE", ""), ("Add a criterion", "Done.\r\n\r\nForbidden phrase\r\n")]:
            with self.subTest(title=title, body=body):
                self.assertEqual(self.pull_request(base, head, title, body)[0], 1)
        head = self.commit("Add a run\n\nFORBIDDEN PHRASE")
        self.assertEqual(self.pull_request(base, head)[0], 1)

    def test_a_pull_request_whose_base_is_gone_is_read_from_its_merge_commit(self):
        self.branch()
        head = self.commit("Add a run\n\nFORBIDDEN PHRASE")
        self.git("checkout", "-q", "--detach", "main")
        self.git("merge", "-q", "--no-ff", "--no-edit", "-m", "Merge topic", "topic")
        self.assertEqual(self.pull_request(MISSING, head)[0], 1)
        self.git("checkout", "-q", "main")
        self.assertEqual(self.pull_request(MISSING, head)[0], 2)

    def test_revisions_check_a_local_range(self):
        self.branch()
        self.commit("Add a criterion\n\nFORBIDDEN PHRASE")
        self.assertEqual(self.run_guard(history=["main..topic"])[0], 1)
        status, output = self.run_guard(history=["main"])
        self.assertEqual(status, 0, output)
        self.assertIn("in the history 1 commit messages, 0 tag messages and 1 files.", output)

    def test_a_shallow_checkout_fails(self):
        self.commit("Update the manual")
        shallow = self.root / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", self.source.resolve().as_uri(), str(shallow)], check=True)
        self.assertEqual(self.run_guard(source=shallow)[0], 0)
        self.assertEqual(self.run_guard(source=shallow, history=["HEAD"])[0], 2)

    def test_an_event_this_check_cannot_read_fails(self):
        head = self.git("rev-parse", "HEAD")
        for event in [{}, {"GITHUB_EVENT_NAME": "workflow_dispatch"},
                      {"GITHUB_EVENT_NAME": "push", "SCAN_BEFORE": NONE, "SCAN_AFTER": MISSING},
                      {"GITHUB_EVENT_NAME": "push", "SCAN_BEFORE": NONE, "SCAN_AFTER": "--output=elsewhere"},
                      {"GITHUB_EVENT_NAME": "pull_request", "SCAN_PR_BASE": head, "SCAN_PR_HEAD": "HEAD"}]:
            with self.subTest(event=event):
                self.assertEqual(self.run_guard(history=[], event=event)[0], 2)


if __name__ == "__main__":
    unittest.main()
