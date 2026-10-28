"""Regression tests for the attribution check; no project build or credentials needed."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("check-attribution.py")
TEMPLATE = Path(__file__).parent.parent / "pull_request_template.md"
spec = importlib.util.spec_from_file_location("attribution", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

HUMAN = ("Ana Example", "ana@example.com")
AGENT = ("Claude", "noreply@anthropic.com")
NONE = "0" * 40
MISSING = "1" * 40


def identity(author, committer=HUMAN):
    return {"GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
            "GIT_COMMITTER_NAME": committer[0], "GIT_COMMITTER_EMAIL": committer[1]}


class AttributionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        config = self.root / "gitconfig"
        config.write_text("[init]\n\tdefaultBranch = main\n", encoding="utf-8")
        environment = patch.dict(os.environ,
                                 {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1", **identity(HUMAN)})
        environment.start()
        self.addCleanup(environment.stop)
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.start = self.commit("Start the record")

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], check=True, stdout=subprocess.PIPE, text=True)
        return result.stdout.strip()

    def commit(self, message, author=HUMAN, committer=HUMAN):
        with patch.dict(os.environ, identity(author, committer)):
            self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def run_check(self, name=None, event=None, ref="refs/heads/main", revisions=()):
        output = io.StringIO()
        with patch.dict(os.environ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            for key in ("GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH", "GITHUB_REF"):
                os.environ.pop(key, None)
            if event is not None:
                path = self.root / "event.json"
                path.write_text(event if isinstance(event, str) else json.dumps(event), encoding="utf-8")
                os.environ.update(GITHUB_EVENT_NAME=name, GITHUB_EVENT_PATH=str(path), GITHUB_REF=ref)
            status = check.main(["--repo", str(self.repo), *revisions])
        return status, output.getvalue()

    def push(self, before, after, ref="refs/heads/main"):
        return self.run_check("push", {"before": before, "after": after, "repository": {"default_branch": "main"}}, ref)

    def pull_request(self, base, head, title="Add a criterion", body="What changed and the checks run."):
        event = {"pull_request": {"title": title, "body": body, "base": {"sha": base}, "head": {"sha": head}}}
        return self.run_check("pull_request", event, "refs/pull/1/merge")

    def branch(self, name="topic"):
        self.git("update-ref", "refs/remotes/origin/main", "main")
        self.git("checkout", "-q", "-b", name)

    def test_human_commits_pass(self):
        after = self.commit("Add a criterion\n\nCo-authored-by: Ben Example <ben@example.com>")
        status, output = self.push(self.start, after)
        self.assertEqual(status, 0, output)
        self.assertIn("passed: 1 commit.", output)

    def test_agent_identities_fail(self):
        for author, committer in [(AGENT, HUMAN), (HUMAN, ("Codex", "codex@example.com")),
                                  (("ChatGPT", "chat@example.com"), HUMAN),
                                  (("Claude Code", "code@example.com"), HUMAN),
                                  (("Cursor Agent", "cursoragent@cursor.com"), HUMAN),
                                  (("claude[bot]", "1+claude[bot]@users.noreply.github.com"), HUMAN),
                                  (("Ana Example", "2+chatgpt-codex-connector[bot]@users.noreply.github.com"), HUMAN),
                                  (("Ana Example", "noreply@anthropic.com"), HUMAN)]:
            with self.subTest(author=author, committer=committer):
                self.git("reset", "-q", "--hard", self.start)
                after = self.commit("Update the manual", author, committer)
                status, output = self.push(self.start, after)
                self.assertEqual(status, 1, output)
                self.assertIn(f"commit {after[:12]}: the author or committer is an agent identity", output)

    def test_a_contributor_whose_name_or_address_contains_an_agent_name_passes(self):
        for author, committer in [(("Claudette Codexia", "claudette@example.com"), HUMAN),
                                  (("Claude Dupont", "claude.dupont@example.fr"), HUMAN),
                                  (HUMAN, ("Jean-Claude Van", "jc@example.com")),
                                  (("Ana", "ana@codex-labs.io"), HUMAN)]:
            with self.subTest(author=author, committer=committer):
                self.git("reset", "-q", "--hard", self.start)
                after = self.commit("Update the manual", author, committer)
                status, output = self.push(self.start, after)
                self.assertEqual(status, 0, output)

    def test_attribution_in_a_message_fails(self):
        for footer, what in [("Co-Authored-By: Claude <noreply@anthropic.com>", "co-author trailer"),
                             ("  co-authored-by: Cursor Agent <agent@example.com>", "co-author trailer"),
                             ("Co-authored-by: Ana Example <noreply@anthropic.com>", "co-author trailer"),
                             ("Co-authored-by: Ana Example <3+copilot-swe-agent[bot]@users.noreply.github.com>",
                              "co-author trailer"),
                             ("Claude-Session: 0123", "session trailer"), ("Codex-Session: 0123", "session trailer"),
                             ("Generated with [Claude Code](https://example.org)", "generated-with footer"),
                             ("\N{ROBOT FACE} Generated with [Claude Code](https://example.org)",
                              "generated-with footer"),
                             ("generated by Codex", "generated-with footer"),
                             ("https://claude.ai/code/session_0123", "session link")]:
            with self.subTest(footer=footer):
                self.git("reset", "-q", "--hard", self.start)
                after = self.commit(f"Update the manual\n\n{footer}")
                status, output = self.push(self.start, after)
                self.assertEqual(status, 1, output)
                self.assertIn(f"commit {after[:12]}: the message carries", output)
                self.assertIn(what, output)

    def test_a_message_that_names_an_agent_integration_passes(self):
        for message in ["Install the skill for Claude Code and Codex\n\n"
                        "The schema is generated with rust/gen/gen_schemas.py.",
                        "Keep the AGENTS.md section generated by Codex",
                        "Add a criterion\n\nCo-authored-by: Jane Doe <jane@precursor.io>",
                        "Add a criterion\n\nCo-authored-by: Sam Lee <sam@openai.com>"]:
            with self.subTest(message=message):
                self.git("reset", "-q", "--hard", self.start)
                after = self.commit(message)
                status, output = self.push(self.start, after)
                self.assertEqual(status, 0, output)

    def test_only_the_pushed_commits_are_checked(self):
        earlier = self.commit("Update the manual\n\nGenerated with [Claude Code](https://example.org)")
        after = self.commit("Fix a typo")
        self.assertEqual(self.push(earlier, after)[0], 0)
        self.assertEqual(self.push(self.start, after)[0], 1)

    def test_a_new_branch_is_checked_over_the_default_branch(self):
        self.commit("Update the manual", AGENT)
        self.branch()
        after = self.commit("Add a criterion")
        self.assertEqual(self.push(NONE, after, "refs/heads/topic")[0], 0)
        after = self.commit("Add a run", HUMAN, ("Codex", "codex@example.com"))
        status, output = self.push(NONE, after, "refs/heads/topic")
        self.assertEqual(status, 1, output)
        self.assertEqual(output.count("agent identity"), 1)

    def test_the_first_push_of_the_default_branch_checks_its_whole_history(self):
        after = self.commit("Update the manual")
        self.git("update-ref", "refs/remotes/origin/main", "main")
        status, output = self.push(NONE, after)
        self.assertEqual(status, 0, output)
        self.assertIn("passed: 2 commits.", output)
        self.git("reset", "-q", "--hard", self.start)
        self.git("commit", "-q", "--amend", "--allow-empty", "-m", "Start the record\n\nClaude-Session: 0123")
        after = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", "main")
        self.assertEqual(self.push(NONE, after)[0], 1)

    def test_a_forced_push_whose_old_tip_is_gone_is_checked_over_the_default_branch(self):
        self.branch()
        after = self.commit("Update the manual\n\nCodex-Session: 0123")
        status, output = self.push(MISSING, after, "refs/heads/topic")
        self.assertEqual(status, 1, output)
        self.assertIn(f"commit {after[:12]}", output)

    def test_a_deleted_branch_has_nothing_to_check(self):
        status, output = self.push(self.start, NONE, "refs/heads/topic")
        self.assertEqual(status, 0, output)
        self.assertIn("passed: 0 commits.", output)

    def test_a_pull_request_checks_its_commits_title_and_body(self):
        base = self.commit("Update the manual", AGENT)
        self.branch()
        head = self.commit("Add a criterion")
        status, output = self.pull_request(base, head)
        self.assertEqual(status, 0, output)
        self.assertIn("passed: 1 commit, and the pull request's title and body.", output)
        self.assertEqual(self.pull_request(base, head, body=None)[0], 0)
        self.assertEqual(self.pull_request(base, head, body="Done.\r\n\r\n.mcp.json generated by Claude Code")[0], 0)
        for title, body in [("Add a criterion", "Done.\n\nGenerated with [Claude Code](https://example.org)"),
                            ("Add a criterion", "Done.\r\n\r\nGenerated with [Claude Code](https://example.org)\r\n"),
                            ("Add a criterion", "Done.\r\n\r\nhttps://claude.ai/code/session_0123"),
                            ("Add a criterion", "Done.\r\n\r\nCo-Authored-By: Claude <noreply@anthropic.com>"),
                            ("Generated by Codex", None)]:
            with self.subTest(title=title, body=body):
                status, output = self.pull_request(base, head, title, body)
                self.assertEqual(status, 1, output)
                self.assertIn("the pull request's title or body carries", output)
        head = self.commit("Add a run\n\nClaude-Session: 0123")
        status, output = self.pull_request(base, head)
        self.assertEqual(status, 1, output)
        self.assertIn(f"commit {head[:12]}: the message carries an agent session trailer", output)

    @unittest.skipUnless(TEMPLATE.is_file(), "no pull request template beside the scripts")
    def test_the_pull_request_template_passes(self):
        after = self.commit("Add a criterion")
        body = TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(self.pull_request(self.start, after, body=body)[0], 0)

    def test_a_pull_request_whose_base_is_gone_is_read_from_its_merge_commit(self):
        self.branch()
        head = self.commit("Add a criterion", ("ChatGPT", "chat@example.com"))
        self.git("checkout", "-q", "--detach", "main")
        self.git("merge", "-q", "--no-ff", "--no-edit", "-m", "Merge topic", "topic")
        status, output = self.pull_request(MISSING, head)
        self.assertEqual(status, 1, output)
        self.assertIn(f"commit {head[:12]}: the author or committer is an agent identity", output)
        self.git("checkout", "-q", "main")
        self.assertEqual(self.pull_request(MISSING, head)[0], 2)

    def test_revisions_check_a_local_range(self):
        self.branch()
        self.commit("Add a criterion\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        self.assertEqual(self.run_check(revisions=["main..topic"])[0], 1)
        self.assertEqual(self.run_check(revisions=["main"])[0], 0)

    def test_what_cannot_be_read_fails(self):
        for name, event in [(None, None), ("workflow_dispatch", {}), ("push", "{not json"), ("push", []),
                            ("pull_request", {"pull_request": None}),
                            ("push", {"before": self.start, "after": MISSING})]:
            with self.subTest(name=name, event=event):
                self.assertEqual(self.run_check(name, event)[0], 2)

    def test_only_commit_ids_are_printed(self):
        after = self.commit("Private subject\n\nGenerated with [Claude Code](https://example.org/private)",
                            ("Claude", "private@example.com"))
        body = "Private body, https://claude.ai/code/session_0123"
        status, output = self.pull_request(self.start, after, "Private title", body)
        self.assertEqual(status, 1, output)
        self.assertNotIn("private", output.casefold())
        self.assertNotIn("example.org", output)


if __name__ == "__main__":
    unittest.main()
