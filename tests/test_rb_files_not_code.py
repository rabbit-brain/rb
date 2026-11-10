"""What rb init installed is not a run's code: a skill, a hook, an MCP entry or rb's section of AGENTS.md, unchanged
since rb wrote it, makes no receipt read the tree as changed and puts no snapshot of it under refs/rb/snapshots/; where they are
all that is uncommitted, rb run add says so. A file anyone changed since is the user's again. Every name and number is
synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import subprocess
from pathlib import Path

from rbtest import as_the_user, main

GIT = ["git", "-c", "user.name=ana", "-c", "user.email=ana@example.org", "-c", "commit.gpgsign=false"]


def test_rbs_own_files_make_no_tree_dirty(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    (root / "AGENTS.md").write_text("# Notes for agents\n", encoding="utf-8")
    (root / "eval.py").write_text("print('top1')\n", encoding="utf-8")
    (root / ".claude").mkdir()                                       # a Claude Code project: rb installs its skill and hooks
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "code"], check=True)
    monkeypatch.chdir(root)
    assert main(["init", "INT8 ResNet-50", "--yes"], typed=True) == 0
    capsys.readouterr()
    assert Path(root, ".claude", "skills").is_dir()                  # rb installed something outside .rb/
    main(["experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8"], typed=True)
    capsys.readouterr()
    assert main(["run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6"], typed=True) == 0
    out = capsys.readouterr().out
    assert "not committed when recorded" not in out
    assert "Only rb's own files are not committed" in out
    # the only ref the receipt keeps is the commit's own tree, as for any clean tree: no snapshot of rb's files
    refs = subprocess.run(["git", "-C", str(root), "for-each-ref", "--format=%(objectname)", "refs/rb/snapshots/"], capture_output=True, text=True).stdout.split()
    head_tree = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True).stdout.strip()
    assert refs == [head_tree]
    # a change of the user's own is code again
    (root / "eval.py").write_text("print('top5')\n", encoding="utf-8")
    assert main(["run", "add", "int8", "fp32.top1=76.0", "int8.top1=75.5"], typed=True) == 0
    assert "not committed when recorded" in capsys.readouterr().out


def test_rbs_section_in_a_file_git_keeps_with_other_line_endings_is_still_rbs(tmp_path, monkeypatch, capsys):
    """A file git keeps in its own form (core.autocrlf: CRLF here, LF in the commit, as Windows keeps it): rb's section
    added to it is still rb's own write alone, so the tree reads as clean."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    (root / "AGENTS.md").write_bytes(b"# Notes for agents\r\n\r\nRun eval.py.\r\n")
    (root / "eval.py").write_bytes(b"print('top1')\n")
    (root / ".claude").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "true"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "code"], check=True)
    monkeypatch.chdir(root)
    assert main(["init", "INT8 ResNet-50", "--yes"], typed=True) == 0
    capsys.readouterr()
    assert b"\r\n" in (root / "AGENTS.md").read_bytes() and b"rabbit" in (root / "AGENTS.md").read_bytes().lower()
    main(["experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8"], typed=True)
    capsys.readouterr()
    assert main(["run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6"], typed=True) == 0
    out = capsys.readouterr().out
    assert "not committed when recorded" not in out and "Only rb's own files are not committed" in out, out
    refs = subprocess.run(["git", "-C", str(root), "for-each-ref", "--format=%(objectname)", "refs/rb/snapshots/"], capture_output=True, text=True).stdout.split()
    assert refs == [subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True).stdout.strip()]


def test_rbs_own_page_and_export_make_no_tree_dirty(tmp_path, monkeypatch, capsys):
    """rb page and rb export write beside .rb/: as rb wrote them, they are no code a run ran, so a run recorded after an
    export reads the tree as its commit's, with no snapshot of them; an export changed since is the user's again."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    (root / "eval.py").write_text("print('top1')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    assert main(["init", "INT8 ResNet-50", "--no-agents"], typed=True) == 0
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "code and record"], check=True)
    main(["experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8"], typed=True)
    for argv in (["page"], ["export", "--out", "rb-export", "--no-repo"], ["export", "--zip", "--no-repo"]):
        assert main(argv, typed=True) == 0, argv
    capsys.readouterr()
    assert (root / "rb-page.html").is_file() and (root / "rb-export").is_dir() and (root / "rb-export.zip").is_file()
    assert main(["run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6"], typed=True) == 0
    out = capsys.readouterr().out
    assert "not committed when recorded" not in out, out
    refs = subprocess.run(["git", "-C", str(root), "for-each-ref", "--format=%(objectname)", "refs/rb/snapshots/"], capture_output=True, text=True).stdout.split()
    assert refs == [subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True).stdout.strip()]
    (root / "rb-export" / "page.html").write_text("my notes\n", encoding="utf-8")
    assert main(["run", "add", "int8", "fp32.top1=76.0", "int8.top1=75.5"], typed=True) == 0
    assert "not committed when recorded" in capsys.readouterr().out
