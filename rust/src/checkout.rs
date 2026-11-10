//! How the checkout that holds a record stands in git, in one phrase every surface prints as made: "all record files
//! committed", "2 record files not committed", "12 record files ignored by git", "no commit yet", "not in git", "git
//! not found" or "rb could not read git". The commit sits one step down ("at commit 9fa4f89a1b2c"). This is the only
//! place these words are made: rb open, rb ui, rb status, rb context, the page and the bridge print them as given.
//! Computed from git on every ask, never stored.

use crate::ledger::DIR;
use crate::sources::{self, git_full, rel_to, resolve_path, GitFail};
use crate::value::Value;
use std::path::{Path, PathBuf};

/// The first state that holds, in this order.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    NotInGit,
    GitNotFound,
    Unreadable,
    Ignored,
    NoCommit,
    NotCommitted,
    Committed,
}

impl State {
    pub fn key(self) -> &'static str {
        match self {
            State::NotInGit => "not_in_git",
            State::GitNotFound => "git_not_found",
            State::Unreadable => "unreadable",
            State::Ignored => "ignored",
            State::NoCommit => "no_commit",
            State::NotCommitted => "not_committed",
            State::Committed => "committed",
        }
    }
}

/// A checkout as git sees it now: the checkout at `root` (a record's root, or a worktree's top).
#[derive(Debug, Clone, PartialEq)]
pub struct Checkout {
    pub state: State,
    /// HEAD's commit, all 40 hex
    pub commit: Option<String>,
    /// HEAD's branch (one with no commit yet too); None on a detached HEAD or outside git
    pub branch: Option<String>,
    pub on_branch: bool,
    /// the folder's name, which names the place where git cannot
    pub folder: String,
    pub record_files_not_committed: usize,
    pub record_files_ignored: usize,
    /// the first rule that ignores a record file, as git check-ignore -v gives it ("<file>:<line>:<pattern>")
    pub ignored_by: Option<String>,
    /// how many other rules ignore record files
    pub more_rules: usize,
    /// whether the commit checked out holds the record (None where it was not asked: no commit, or nothing to commit)
    pub record_held: Option<bool>,
    /// files outside the record changed or new and not ignored, by a receipt's own rule (sources::code_pathspec)
    pub changes_outside_record: bool,
    /// git's own first line when it failed, or None
    pub why: Option<String>,
    pub timed_out: bool,
    /// the record's folder (the one that holds `.rb/`), as rb resolved it: what the check command names
    pub root: PathBuf,
    /// the repository's top and the record's folder in it (`.rb`, `sub/.rb`), where git answered
    pub top: Option<PathBuf>,
    pub rb_dir: String,
    /// the first record files not committed, in git's order, each with rb's word for its change (PARITY 2.5)
    pub not_committed_names: Vec<Named>,
    /// the first record files git ignores, sorted
    pub ignored_names: Vec<String>,
    /// each path git's status lists of the record (a rename by the path HEAD holds), from the top: M counts them with
    /// HEAD's files
    pub listed: Vec<String>,
}

/// A record file not committed, named from the record's folder as `git -C <that folder> status --short` names it:
/// its change is new, changed, deleted, renamed (by its new path, `from` the old) or unmerged.
#[derive(Debug, Clone, PartialEq)]
pub struct Named {
    pub path: String,
    pub change: &'static str,
    pub from: Option<String>,
}

/// How many names a checkout keeps of each list (the JSON's), and how many a line names before "and N more".
pub const NAMES_KEPT: usize = 10;
pub const NAMES_SAID: usize = 3;

pub fn plural(n: usize, one: &str, many: &str) -> String {
    format!("{n} {}", if n == 1 { one } else { many })
}

/// "9fa4f89a1b2c": a commit as rb's prose says it, 12 hex.
pub fn short(sha: &str) -> String {
    sha.chars().take(12).collect()
}

/// Whether a path below the record's folder is one of rb's own ignores (.rb/.gitignore: cache/, journal/, .lock, *.tmp).
fn rb_ignores(rel: &str) -> bool {
    let parts: Vec<&str> = rel.trim_end_matches('/').split('/').collect();
    let dir = rel.ends_with('/');
    parts.iter().enumerate().any(|(i, p)| (*p == "cache" || *p == "journal") && (i + 1 < parts.len() || dir))
        || parts.last().map_or(false, |l| !dir && (*l == ".lock" || l.ends_with(".tmp")))
}

/// The files below a folder (`top`/`rel`, rel ending in /), relative to `top`.
fn walk(top: &Path, rel: &str, out: &mut Vec<String>) {
    let Ok(rd) = std::fs::read_dir(top.join(rel.trim_end_matches('/'))) else { return };
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        let sub = format!("{rel}{name}");
        let is_dir = e.file_type().map_or(false, |t| t.is_dir());
        if is_dir {
            walk(top, &format!("{sub}/"), out);
        } else {
            out.push(sub);
        }
    }
}

/// Whether a `.git` is at `dir` or above it: the record is in a repository, whether or not git can be run.
fn git_above(dir: &Path) -> bool {
    let mut d = Some(dir);
    while let Some(x) = d {
        if x.join(".git").symlink_metadata().is_ok() {
            return true;
        }
        d = x.parent();
    }
    false
}

/// Whether git_view's lists (taken at the repository's top `top`, where the record is) hold a change outside the
/// record by the receipt's own rule (sources::code_pathspec_in): changed against HEAD or new and not ignored (with no
/// commit yet, every file git tracks), the record's folder, the files rb wrote as rb wrote them and the clone's other
/// worktrees nested here left out. The ledger and the worktree list are read only while a change is left to judge.
fn changes_in_view(root: &Path, top: &Path, gv: &crate::repo::GitView) -> bool {
    let under = |p: &str, rel: &str| p == rel || p.trim_end_matches('/') == rel || p.starts_with(&format!("{}/", rel.trim_end_matches('/')));
    let first = if gv.commit.is_some() { &gv.modified } else { &gv.tracked };
    let mut left: Vec<&String> = first.iter().chain(gv.untracked.iter()).filter(|p| !under(p, DIR)).collect();
    if left.is_empty() {
        return false;
    }
    let ours = sources::rb_written(root, top);
    left.retain(|p| !ours.iter().any(|o| under(p, o)));
    if left.is_empty() {
        return false;
    }
    let trees: Vec<String> = sources::git(top, &["worktree", "list", "--porcelain"])
        .unwrap_or_default()
        .lines()
        .filter_map(|l| l.strip_prefix("worktree "))
        .map(|listed| resolve_path(Path::new(listed)))
        .filter(|p| p.as_path() != top)
        .filter_map(|p| rel_to(&p, top).filter(|r| !r.is_empty()))
        .collect();
    left.iter().any(|p| !trees.iter().any(|t| under(p, t)))
}

/// HEAD's commit and its branch, telling what git answers apart from git failing: `rev-parse --verify -q HEAD` exits 1
/// with nothing said where HEAD has no commit yet, and `symbolic-ref -q HEAD` exits 1 on a detached HEAD; any other
/// answer is a failure, said as git said it (Codex S5 should 2: a failed read read as "no commit yet" or "no branch").
fn head_and_branch(root: &Path) -> Result<(Option<String>, Option<String>), Result<String, GitFail>> {
    let ask = |args: &[&str]| git_full(root, args, None).map_err(Err);
    let said = |r: &sources::GitRan| if r.first_err.is_empty() { format!("git exited {}", r.code) } else { r.first_err.clone() };
    let h = ask(&["rev-parse", "--verify", "-q", "HEAD"])?;
    let head = match h.code {
        0 => Some(String::from_utf8_lossy(&h.stdout).trim().to_string()).filter(|h| !h.is_empty()),
        1 if h.first_err.is_empty() => None,
        _ => return Err(Ok(said(&h))),
    };
    let b = ask(&["symbolic-ref", "--short", "-q", "HEAD"])?;
    let branch = match b.code {
        0 => Some(String::from_utf8_lossy(&b.stdout).trim().to_string()).filter(|b| !b.is_empty()),
        1 if b.first_err.is_empty() => None,
        _ => return Err(Ok(said(&b))),
    };
    Ok((head, branch))
}

fn folder_of(p: &Path) -> String {
    p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| crate::sys::slashed(p))
}

impl Checkout {
    fn bare(state: State, folder: String) -> Checkout {
        Checkout {
            state, commit: None, branch: None, on_branch: false, folder, record_files_not_committed: 0, record_files_ignored: 0, ignored_by: None,
            more_rules: 0, record_held: None, changes_outside_record: false, why: None, timed_out: false, root: PathBuf::new(), top: None,
            rb_dir: DIR.to_string(), not_committed_names: Vec::new(), ignored_names: Vec::new(), listed: Vec::new(),
        }
    }

    fn failed(folder: String, f: Result<String, GitFail>) -> Checkout {
        match f {
            Err(GitFail::NotFound) => Self::bare(State::GitNotFound, folder),
            Err(GitFail::TimedOut) => Checkout { timed_out: true, ..Self::bare(State::Unreadable, folder) },
            Ok(line) => Checkout { why: Some(line), ..Self::bare(State::Unreadable, folder) },
        }
    }

    /// The checkout that holds the record at `root` (its folder holds `.rb/`), now.
    pub fn of(root: &Path) -> Checkout {
        let root = resolve_path(root);
        Checkout { root: root.clone(), ..Self::of_resolved(&root) }
    }

    fn of_resolved(root: &Path) -> Checkout {
        let root = root.to_path_buf();
        let folder = folder_of(&root);
        if !git_above(&root) {
            return Self::bare(State::NotInGit, folder);
        }
        let ask = |args: &[&str]| git_full(&root, args, None);
        let top = match ask(&["rev-parse", "--show-toplevel"]) {
            Err(e) => return Self::failed(folder, Err(e)),
            Ok(r) if r.code != 0 => {
                let line = if r.first_err.is_empty() { format!("git exited {}", r.code) } else { r.first_err };
                return Self::failed(folder, Ok(line));
            }
            Ok(r) => {
                let t = String::from_utf8_lossy(&r.stdout).trim().to_string();
                crate::sys::canonicalize(&t).unwrap_or_else(|_| PathBuf::from(t))
            }
        };
        let (head, branch) = match head_and_branch(&root) {
            Ok(hb) => hb,
            Err(f) => return Self::failed(folder, f),
        };
        Self::scan(&root, &top, folder, head, branch, || sources::code_changed(&root, DIR).unwrap_or(false))
    }

    /// The checkout a page states (About's line 2, and "The page says" after rb page, rb export and rb sync): the one
    /// that holds the record the page is made of, at `root`, never a bundle's copy being built elsewhere ($TMPDIR,
    /// <out>.tmp-<pid>), whose folder is no checkout of the work. A page never says changes outside the record, so they
    /// are not asked (no whole-tree status).
    pub fn of_page(root: &Path) -> Checkout {
        let root = resolve_path(root);
        Checkout { root: root.clone(), ..Self::of_page_resolved(&root) }
    }

    fn of_page_resolved(root: &Path) -> Checkout {
        let root = root.to_path_buf();
        if !git_above(&root) {
            return Self::of(&root);
        }
        let Some(top) = sources::git_top(&root) else { return Self::of(&root) };
        let (head, branch) = match head_and_branch(&root) {
            Ok(hb) => hb,
            Err(f) => return Self::failed(folder_of(&root), f),
        };
        Self::scan(&root, &top, folder_of(&root), head, branch, || false)
    }

    /// What rb page, rb export and rb sync say after what they wrote: "The page says: <About's line 2>."
    pub fn page_says(&self) -> String {
        format!("The page says: {}.", self.page_line())
    }

    /// The checkout that holds the record at `root`, from what git_view already asked of it (HEAD, the branch, the
    /// tracked, modified and untracked files): rb status and rb context, which list those anyway. Changes outside the
    /// record are read from those lists by the receipt's own rule, which is exact where the record is at the
    /// repository's top; a record below the top asks the receipt's own status. Where git_view could not ask git, git is
    /// asked again here so the words say why.
    pub fn from_view(root: &Path, gv: &crate::repo::GitView) -> Checkout {
        let root = resolve_path(root);
        Checkout { root: root.clone(), ..Self::from_view_resolved(&root, gv) }
    }

    fn from_view_resolved(root: &Path, gv: &crate::repo::GitView) -> Checkout {
        let root = root.to_path_buf();
        if !gv.present || !git_above(&root) {
            return Self::of(&root);
        }
        // where the record is in the repository, as the journal's own place asked it (git keeps the answer): no question
        // of the repository's top
        let Some((_, _, prefix)) = crate::journal::git_dirs(&root) else { return Self::of(&root) };
        let at_top = prefix.is_empty();
        let top = if at_top { root.clone() } else { root.ancestors().nth(prefix.split('/').count()).map(Path::to_path_buf).unwrap_or_else(|| root.clone()) };
        let folder = folder_of(&root);
        // what git_view could not say (no commit, no branch) is asked again, so a failed read is never read as a state
        let (head, branch) = if gv.commit.is_none() || gv.branch.is_none() {
            match head_and_branch(&root) {
                Ok(hb) => hb,
                Err(f) => return Self::failed(folder, f),
            }
        } else {
            (gv.commit.clone(), gv.branch.clone())
        };
        // a submodule holding only new files is in no list git_view keeps, as it is in the receipt's status: a
        // repository with submodules asks the receipt's own status (Codex S5 should 1)
        let submodules = top.join(".gitmodules").is_file();
        Self::scan(&root, &top, folder, head, branch, || {
            if at_top && !submodules {
                changes_in_view(&root, &top, gv)
            } else {
                sources::code_changed(&root, DIR).unwrap_or(false)
            }
        })
    }

    /// The checkout that holds the record at `root`, in the worktree whose top is `top`, where the caller already knows
    /// its HEAD (`head`, None with no commit yet), its branch and whether it has changes outside the record (by a
    /// receipt's own rule): only the record's own files are asked of git. rb's screens list each place this way, from
    /// what git worktree list and their own status call already said.
    pub fn in_tree(root: &Path, top: &Path, head: Option<String>, branch: Option<String>, changes_outside_record: bool) -> Checkout {
        let root = resolve_path(root);
        let folder = folder_of(&root);
        Checkout { root: root.clone(), ..Self::scan(&root, top, folder, head, branch, || changes_outside_record) }
    }

    fn scan(root: &Path, top: &Path, folder: String, head: Option<String>, branch: Option<String>, changes: impl FnOnce() -> bool) -> Checkout {
        let ask = |args: &[&str]| git_full(top, args, None);
        let rel = rel_to(root, top).unwrap_or_default();
        let rb_dir = if rel.is_empty() { DIR.to_string() } else { format!("{rel}/{DIR}") };
        let spec = format!(":(top){rb_dir}");
        let st = match ask(&["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching", "--", &spec]) {
            Err(e) => return Self::failed(folder, Err(e)),
            Ok(r) if r.code != 0 => return Self::failed(folder, Ok(r.first_err)),
            Ok(r) => r.stdout,
        };
        let text = String::from_utf8_lossy(&st).into_owned();
        let mut fields = text.split('\0').filter(|f| !f.is_empty());
        let (mut not_committed, mut ignored): (usize, Vec<String>) = (0, Vec::new());
        let (mut names, mut listed): (Vec<Named>, Vec<String>) = (Vec::new(), Vec::new());
        let below = format!("{rb_dir}/");
        // a path from the top, as `git -C <the record's folder> status --short` names it
        let from_root = |p: &str| if rel.is_empty() { p.to_string() } else { p.strip_prefix(&format!("{rel}/")).unwrap_or(p).to_string() };
        while let Some(e) = fields.next() {
            if e.len() < 4 {
                continue;
            }
            let (xy, path) = (&e[..2], &e[3..]);
            let from = if xy.contains('R') || xy.contains('C') { fields.next() } else { None }; // counted once
            if xy == "!!" {
                let inside = path.strip_prefix(below.as_str()).unwrap_or("");
                if path.ends_with('/') {
                    let mut files = Vec::new();
                    walk(top, path, &mut files);
                    ignored.extend(files.into_iter().filter(|f| !rb_ignores(f.strip_prefix(below.as_str()).unwrap_or(f))));
                } else if !rb_ignores(inside) {
                    ignored.push(path.to_string());
                }
            } else {
                not_committed += 1;
                let (x, y) = (xy.as_bytes()[0], xy.as_bytes()[1]);
                let change = if xy == "??" {
                    "new"
                } else if x == b'U' || y == b'U' || xy == "AA" || xy == "DD" {
                    "unmerged"
                } else if x == b'R' || y == b'R' {
                    "renamed"
                } else if x == b'C' || x == b'A' || y == b'A' {
                    "new"
                } else if x == b'D' || y == b'D' {
                    "deleted"
                } else {
                    "changed"
                };
                listed.push(if change == "renamed" { from.unwrap_or(path).to_string() } else { path.to_string() });
                if names.len() < NAMES_KEPT {
                    names.push(Named { path: from_root(path), change, from: from.filter(|_| change == "renamed").map(from_root) });
                }
            }
        }
        let record_held = match (&head, not_committed > 0 || !ignored.is_empty()) {
            (Some(_), true) => Some(ask(&["cat-file", "-e", &format!("HEAD:{rb_dir}/{}", crate::ledger::ROOT_FILE)]).map_or(true, |r| r.code == 0)),
            _ => None,
        };
        let (ignored_by, more_rules) = if ignored.is_empty() {
            (None, 0)
        } else {
            let input = ignored.iter().map(|p| format!("{p}\0")).collect::<String>();
            let out = git_full(top, &["check-ignore", "-v", "-z", "--stdin"], Some(input.as_bytes())).map(|r| r.stdout).unwrap_or_default();
            let out = String::from_utf8_lossy(&out).into_owned();
            let f: Vec<&str> = out.split('\0').collect();
            let mut rules: Vec<String> = Vec::new();
            for c in f.chunks(4).filter(|c| c.len() == 4 && !c[0].is_empty()) {
                let r = format!("{}:{}:{}", c[0], c[1], c[2]);
                if !rules.contains(&r) {
                    rules.push(r);
                }
            }
            let n = rules.len().saturating_sub(1);
            (rules.into_iter().next(), n)
        };
        let changes_outside_record = changes();
        let state = if !ignored.is_empty() {
            State::Ignored
        } else if head.is_none() {
            State::NoCommit
        } else if not_committed > 0 {
            State::NotCommitted
        } else {
            State::Committed
        };
        Checkout {
            state, on_branch: branch.is_some(), commit: head, branch, folder, record_files_not_committed: not_committed,
            record_files_ignored: ignored.len(), ignored_by, more_rules, record_held, changes_outside_record, why: None, timed_out: false,
            root: root.to_path_buf(), top: Some(top.to_path_buf()), rb_dir, not_committed_names: names,
            ignored_names: { let mut i: Vec<String> = ignored.iter().map(|p| from_root(p)).collect(); i.sort(); i.truncate(NAMES_KEPT); i },
            listed,
        }
    }

    /// Whether git could say how the checkout stands.
    pub fn in_git(&self) -> bool {
        !matches!(self.state, State::NotInGit | State::GitNotFound | State::Unreadable)
    }

    /// The phrase: what every face says, as made here.
    pub fn said(&self) -> String {
        match self.state {
            State::NotInGit => "not in git".into(),
            State::GitNotFound => "git not found".into(),
            State::Unreadable => "rb could not read git".into(),
            State::Ignored => format!("{} ignored by git", plural(self.record_files_ignored, "record file", "record files")),
            State::NoCommit => "no commit yet".into(),
            State::NotCommitted => format!("{} not committed", plural(self.record_files_not_committed, "record file", "record files")),
            State::Committed => "all record files committed".into(),
        }
    }

    /// How many record files no commit keeps, as the words count them: those git ignores where any is (that count wins),
    /// else those not committed. Every count rb gives of a checkout's record files is this one: a place's, Elsewhere's
    /// and rb remove's.
    pub fn count(&self) -> usize {
        if self.state == State::Ignored {
            self.record_files_ignored
        } else {
            self.record_files_not_committed
        }
    }

    /// The count in the shared words ("2 record files not committed", "12 record files ignored by git"), with no
    /// commit yet too; None where git cannot say or nothing is to commit.
    pub fn count_said(&self) -> Option<String> {
        match self.state {
            State::Ignored => Some(self.said()),
            _ if !self.in_git() || self.record_files_not_committed == 0 => None,
            _ => Some(format!("{} not committed", plural(self.record_files_not_committed, "record file", "record files"))),
        }
    }

    /// What a place's line says of its checkout: the count where git can count, nothing where all is committed, and
    /// the words where git cannot say ("not in git", "git not found", "rb could not read git").
    pub fn place_said(&self) -> Option<String> {
        if self.in_git() {
            self.count_said()
        } else {
            Some(self.said())
        }
    }

    /// The place's name where a screen names it by its checkout: its branch, "no branch" on a detached HEAD, its
    /// folder's name where git cannot say.
    pub fn name(&self) -> String {
        match (&self.branch, self.in_git()) {
            (Some(b), _) => b.clone(),
            (None, true) => "no branch".into(),
            (None, false) => self.folder.clone(),
        }
    }

    /// The words with "no branch · " before them on a detached HEAD: rb status's line under its title.
    pub fn status_line(&self) -> String {
        if self.in_git() && !self.on_branch {
            format!("no branch \u{b7} {}", self.said())
        } else {
            self.said()
        }
    }

    /// rb status's line under its title from data.repository as Ledger::status gave it (its state, said and on_branch):
    /// the same words status_line() makes.
    pub fn status_line_of(repository: &Value) -> Option<String> {
        let said = repository.get("said").and_then(Value::as_str)?;
        let state = repository.get("state").and_then(Value::as_str).unwrap_or("");
        let in_git = !["not_in_git", "git_not_found", "unreadable"].contains(&state);
        let on_branch = repository.get("on_branch").and_then(Value::as_bool).unwrap_or(true);
        Some(if in_git && !on_branch { format!("no branch \u{b7} {said}") } else { said.to_string() })
    }

    fn commit_part(&self) -> String {
        let mut s = format!("at commit {}", short(self.commit.as_deref().unwrap_or("")));
        if self.changes_outside_record {
            s.push_str(", with changes outside the record not committed");
        }
        s
    }

    fn ignore_line(&self) -> String {
        let rule = self.ignored_by.clone().unwrap_or_default();
        let more = match self.more_rules {
            0 => String::new(),
            n => format!(" and {}", plural(n, "more rule", "more rules")),
        };
        format!("git ignores {} ({rule}{more}), so a commit leaves them out", plural(self.record_files_ignored, "record file", "record files"))
    }

    fn git_line(&self) -> String {
        if self.timed_out {
            "git did not answer, so rb cannot say what is committed.".into()
        } else {
            self.why.clone().unwrap_or_default()
        }
    }

    /// The whole line rb context's first repository bullet and data.repository.line say: the words, the commit, and
    /// what one step down adds.
    pub fn line(&self) -> String {
        let mut s = self.status_line();
        let shown = self.commit.is_some() && self.in_git();
        if shown {
            s.push_str(&format!(" \u{b7} at commit {}", short(self.commit.as_deref().unwrap_or(""))));
        }
        // the commit's own words first, so "which" can only mean the commit (MP-1109-0930 later 4), then the changes
        if shown && self.state != State::Ignored && self.record_held == Some(false) {
            s.push_str(", which does not hold the record");
        }
        if shown && self.changes_outside_record {
            s.push_str(", with changes outside the record not committed");
        }
        match self.state {
            State::Ignored => s.push_str(&format!(": {}", self.ignore_line())),
            State::Unreadable => s.push_str(&format!(": {}", self.git_line())),
            _ => {}
        }
        s
    }

    /// About's line 2 on a page: the words, then the commit where there is one. Never a branch or a path.
    pub fn page_line(&self) -> String {
        match &self.commit {
            Some(c) if self.in_git() => format!("{} \u{b7} at commit {}", self.said(), short(c)),
            _ => self.said(),
        }
    }

    /// rb doctor's problem line for the checkout, where it has one: (its check, the detail, the fix). Record files git
    /// ignores (with the rule), git not found where a .git is, and rb could not read git (with git's first line): each
    /// keeps a commit from holding the record, or rb from telling a merge from an edit outside rb.
    pub fn doctor_problem(&self) -> Option<(&'static str, String, &'static str)> {
        match self.state {
            State::Ignored => Some((
                "git_ignored",
                format!("{}: {}", self.said(), self.ignore_line()),
                "change the rule so git keeps the record's files (rb's own .rb/.gitignore already leaves out its cache and journal)",
            )),
            State::GitNotFound => Some(("git_not_found", "git not found: rb cannot find git, so it cannot say what is committed".into(), "install git, or put it on PATH")),
            State::Unreadable if self.timed_out => Some(("git_unreadable", format!("rb could not read git: {}", self.git_line()), "rb doctor again once git answers")),
            State::Unreadable => Some(("git_unreadable", format!("rb could not read git: {}", self.git_line()), "do what git's message asks, then rb doctor again")),
            _ => None,
        }
    }

    /// The fold's lines, one step down: each {text, kind} with kind commit (Copy gives `sha`), note, or git (git's own
    /// words, in monospace).
    pub fn fold(&self) -> Vec<Value> {
        let line = |text: String, kind: &str| crate::obj! {"text" => text, "kind" => kind, "sha" => if kind == "commit" { self.commit.clone().unwrap_or_default() } else { String::new() }};
        let mut out = Vec::new();
        match self.state {
            State::NotInGit => out.push(line("This folder is not in a git repository, so no commit holds the record.".into(), "note")),
            State::GitNotFound => out.push(line("rb cannot find git, so it cannot say what is committed.".into(), "note")),
            State::Unreadable => out.push(line(self.git_line(), if self.timed_out { "note" } else { "git" })),
            State::NoCommit => out.push(line("This repository has no commit yet, so no commit holds the record.".into(), "note")),
            _ => {
                match &self.commit {
                    Some(_) => out.push(line(self.commit_part(), "commit")),
                    None => out.push(line("This repository has no commit yet.".into(), "note")),
                }
                if self.state == State::Ignored {
                    out.push(line(self.ignore_line(), "note"));
                } else if self.record_held == Some(false) {
                    out.push(line("this commit does not hold the record".into(), "note"));
                }
            }
        }
        out
    }

    /// The fold's lines as text, one a line: rb ui's w detail.
    pub fn fold_text(&self) -> Vec<String> {
        self.fold().iter().filter_map(|v| v.get("text").and_then(Value::as_str).map(str::to_string)).collect()
    }

    /// Every key of the JSON that carries it (data.repository, the shell's checkout, a place's git).
    pub fn to_value(&self) -> Value {
        let opt = |s: &Option<String>| s.clone().map(Value::Str).unwrap_or(Value::Null);
        crate::obj! {
            "state" => self.state.key(), "said" => self.said(), "line" => self.line(), "commit" => opt(&self.commit), "branch" => opt(&self.branch),
            "on_branch" => self.on_branch, "name" => self.name(), "record_files_not_committed" => self.record_files_not_committed as i64,
            "record_files_ignored" => self.record_files_ignored as i64, "ignored_by" => opt(&self.ignored_by),
            // as 0.8.4 asked it (files not committed at a commit); the ignored state's answer is for page_evidence alone
            "record_held" => self.record_held.filter(|_| self.record_files_not_committed > 0).map(Value::Bool).unwrap_or(Value::Null), "changes_outside_record" => self.changes_outside_record,
            "why" => if self.timed_out { Value::Str(self.git_line()) } else { opt(&self.why) }, "page_line" => self.page_line(),
            "fold" => Value::List(self.fold())
        }
    }

    /// The three keys a page carries (the Owner tier's checkout): no branch, no path, no rule.
    pub fn page_value(&self) -> Value {
        crate::obj! {"state" => self.state.key(), "said" => self.said(), "commit" => self.commit.clone().filter(|_| self.in_git()).map(Value::Str).unwrap_or(Value::Null), "line" => self.page_line()}
    }

    /// page_value, with what About's block under line 2 says of the checkout (PARITY 9): `record_files` (M) and
    /// `page_evidence`, its lines in their page forms, at the page's own minute (line 1's). Still no branch, no path,
    /// none of git's words and no rule from outside the repository's files.
    pub fn page_value_at(&self, minute: &str) -> Value {
        let mut v = self.page_value();
        if let Some(d) = v.as_dict_mut() {
            self.add_page_facts(minute, d);
        }
        v
    }

    /// The keys page_value_at adds, into `d`: rb open's document carries them too, so a page and the screen it is
    /// drawn from say the same.
    pub fn add_page_facts(&self, minute: &str, d: &mut crate::value::Dict) {
        d.insert("record_files".into(), self.record_files().map(|m| Value::Int(m as i64)).unwrap_or(Value::Null));
        let when = When::Page(minute.to_string());
        d.insert("page_evidence".into(), Value::List(self.page_evidence(minute).iter().map(|l| l.to_value(Some(&when))).collect()));
    }

    /// What a page published with its code's address adds (PARITY 9.5): `origin_holds`, whether origin's branches, as
    /// git last fetched them, hold HEAD (local refs only, no network; null where git had fetched no branch of origin),
    /// and `record_dir`, the record's folder in the repository (`.rb`, `sub/.rb`).
    pub fn add_origin_facts(&self, d: &mut crate::value::Dict) {
        let mut origin = Value::Null;
        if let (Some(commit), Some(top)) = (self.commit.as_deref().filter(|_| self.in_git()), self.top.as_deref()) {
            let refs = |args: &[&str]| git_full(top, args, None).ok().filter(|r| r.code == 0).map_or(false, |r| !r.stdout.is_empty());
            if refs(&["for-each-ref", "--count=1", "--contains", commit, "--format=x", "refs/remotes/origin/"]) {
                origin = Value::Bool(true);
            } else if refs(&["for-each-ref", "--count=1", "--format=x", "refs/remotes/origin/"]) {
                origin = Value::Bool(false);
            }
        }
        d.insert("origin_holds".into(), origin);
        d.insert("record_dir".into(), self.rb_dir.clone().into());
    }
}

// ---------------------------------------------------------------- the evidence, one step down (PARITY 1-3)

/// When rb asked git, as each surface says it (PARITY 2.1): rb open's "just now" (a time rb open's live script ages
/// once rb open stops answering), "at 2026-11-09 09:41 UTC" on rb ui, rb status, rb context and in JSON, and a
/// page's "when this page was made, 2026-11-08 09:41 UTC" (line 1's minute).
#[derive(Debug, Clone, PartialEq)]
pub enum When {
    Live(String),
    At(String),
    Page(String),
}

impl When {
    /// rb's clock now, for rb ui, rb status, rb context and JSON.
    pub fn now() -> When {
        When::At(crate::pytime::now())
    }

    pub fn said(&self) -> String {
        match self {
            When::Live(_) => "just now".into(),
            When::At(iso) => format!("at {}", crate::ledger::support::minute(iso)),
            When::Page(minute) => format!("when this page was made, {minute}"),
        }
    }

    pub fn iso(&self) -> Option<&str> {
        match self {
            When::Live(iso) | When::At(iso) => Some(iso),
            When::Page(_) => None,
        }
    }

    fn page(&self) -> bool {
        matches!(self, When::Page(_))
    }
}

/// One line of the evidence: its kind (found, git, files, code, check, define; a page's own: page), its text, and
/// what a surface draws apart: `code`, the parts drawn in monospace; `sha`, all 40 hex where a found line names a
/// commit; a check line's `cmd` and `says`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Line {
    pub kind: &'static str,
    pub text: String,
    pub code: Vec<String>,
    pub sha: Option<String>,
    pub cmd: Option<String>,
    pub says: Option<String>,
}

impl Line {
    fn of(kind: &'static str, text: String) -> Line {
        Line { kind, text, ..Line::default() }
    }

    /// Every key on every line, null where it does not hold (a list's objects keep one shape): `when` and `at` on a
    /// found line, `sha` where it names a commit, `cmd` and `says` on a check line.
    pub fn to_value(&self, when: Option<&When>) -> Value {
        let opt = |s: Option<String>| s.map(Value::Str).unwrap_or(Value::Null);
        let found = self.kind == "found";
        crate::obj! {
            "kind" => self.kind, "text" => self.text.clone(), "code" => Value::list_of_str(self.code.clone()),
            "sha" => opt(self.sha.clone()), "when" => opt(when.filter(|_| found).map(When::said)),
            "at" => opt(when.filter(|_| found).and_then(When::iso).map(str::to_string)), "cmd" => opt(self.cmd.clone()), "says" => opt(self.says.clone())
        }
    }
}

/// `text` cut where each of `code` stands (in order), each piece with whether it is one of them: what a surface draws
/// in monospace.
pub fn segments<'a>(text: &'a str, code: &[String]) -> Vec<(bool, &'a str)> {
    let mut out = Vec::new();
    let mut rest = text;
    for c in code.iter().filter(|c| !c.is_empty()) {
        if let Some(i) = rest.find(c.as_str()) {
            if i > 0 {
                out.push((false, &rest[..i]));
            }
            out.push((true, &rest[i..i + c.len()]));
            rest = &rest[i + c.len()..];
        }
    }
    if !rest.is_empty() {
        out.push((false, rest));
    }
    out
}

/// What record files are, wherever a line above says "record file" (PARITY 2.9).
pub const RECORD_FILES_ARE: &str = "Record files are the files rb keeps for this project next to its code, such as its questions, rules, runs, notes and log.";
pub const CHECK_LEAD: &str = "To check it yourself, in your terminal:";
pub const ASK_LEAD: &str = "To ask git yourself, in your terminal:";
pub const STATUS_SAYS: &str = "It lists each record file not committed, and prints nothing when all are.";
pub const IGNORE_SAYS: &str = "It prints the rule that makes git ignore that file.";
pub const CODE_SAYS: &str = "This checkout has changes outside the record not committed.";
const OUTSIDE_RULE: &str = "by a rule outside the repository's files";

/// The file of a rule as git check-ignore -v gives it, `<source>:<line>:<pattern>`: everything before the first
/// `:<digits>:`, so a Windows path's drive letter (`C:\Users\x\.gitignore:1:.rb/`) stays in the file.
fn rule_file(rule: &str) -> &str {
    let b = rule.as_bytes();
    for (i, _) in rule.match_indices(':') {
        let digits = b[i + 1..].iter().take_while(|c| c.is_ascii_digit()).count();
        if digits > 0 && b.get(i + 1 + digits) == Some(&b':') {
            return &rule[..i];
        }
    }
    rule.split(':').next().unwrap_or("")
}

/// Whether a rule's file is inside the repository: a relative path that does not climb out of it. A drive letter, a
/// UNC path, an absolute path on any OS and `..` are outside.
fn rule_file_inside(rule: &str) -> bool {
    let file = rule_file(rule);
    let b = file.as_bytes();
    !(file.is_empty()
        || Path::new(file).is_absolute()
        || file.starts_with('/')
        || file.starts_with('\\')
        || (b.len() >= 2 && b[1] == b':' && b[0].is_ascii_alphabetic())
        || file.split(['/', '\\']).any(|p| p == ".."))
}

/// HEAD's files below the record's folder, per (top, commit, folder): a commit's tree never changes.
static TREES: std::sync::Mutex<Option<std::collections::HashMap<String, Option<std::collections::HashSet<String>>>>> = std::sync::Mutex::new(None);

fn head_files(top: &Path, commit: &str, rb_dir: &str) -> Option<std::collections::HashSet<String>> {
    let key = format!("{}\0{commit}\0{rb_dir}", top.display());
    if let Some(hit) = TREES.lock().ok().and_then(|g| g.as_ref().and_then(|m| m.get(&key).cloned())) {
        return hit;
    }
    let below = format!("{rb_dir}/");
    let got = git_full(top, &["ls-tree", "-r", "-z", "--name-only", commit, "--", rb_dir], None).ok().filter(|r| r.code == 0).map(|r| {
        String::from_utf8_lossy(&r.stdout).split('\0').filter(|p| !p.is_empty() && !rb_ignores(p.strip_prefix(below.as_str()).unwrap_or(p))).map(str::to_string).collect()
    });
    if let Ok(mut g) = TREES.lock() {
        let m = g.get_or_insert_with(Default::default);
        if m.len() >= 64 {
            m.clear();
        }
        m.insert(key, got.clone());
    }
    got
}

/// "a, b and c", or the first three and "and N more" of `total`.
fn names_said(names: &[String], total: usize) -> String {
    let shown: Vec<String> = names.iter().take(NAMES_SAID).cloned().collect();
    if total > shown.len() {
        format!("{} and {} more", shown.join(", "), total - shown.len())
    } else {
        crate::next_step::and_list(&shown)
    }
}

fn files(n: usize) -> String {
    plural(n, "record file", "record files")
}

impl Checkout {
    /// M: the record files, those HEAD holds and each the status lists that HEAD does not (a rename once, as HEAD holds
    /// it), rb's own ignores left out; with no commit yet, those the status lists. None where git cannot say. Counted
    /// only where the evidence is drawn (one ls-tree, kept per commit), never in a place's line.
    pub fn record_files(&self) -> Option<usize> {
        if !self.in_git() {
            return None;
        }
        let Some(commit) = &self.commit else { return Some(self.record_files_not_committed) };
        let mut held = head_files(self.top.as_deref()?, commit, &self.rb_dir)?;
        held.extend(self.listed.iter().cloned());
        Some(held.len())
    }

    /// The command that checks it, run from anywhere (PARITY 2.8): `git -C <the record's folder> status …`, or
    /// check-ignore where git ignores record files. None where git is not there to ask.
    pub fn check(&self) -> Option<String> {
        let folder = crate::places::shell_word(&crate::sys::slashed(&self.root));
        match self.state {
            State::NotInGit | State::GitNotFound => None,
            State::Ignored => {
                let file = format!("{DIR}/{}", crate::ledger::ROOT_FILE);
                let one = if self.ignored_names.iter().any(|n| n == &file) { file } else { self.ignored_names.first().cloned().unwrap_or(file) };
                Some(format!("git -C {folder} check-ignore -v {}", crate::places::shell_word(&one)))
            }
            _ => Some(format!("git -C {folder} status --short --untracked-files=all -- {DIR}")),
        }
    }

    /// The rule as `<rule>` says it: on your own screens git's first rule and how many more; on a page only a rule
    /// whose file git names inside the repository, else None (the words say "by a rule outside the repository's files").
    fn rule_said(&self, page: bool) -> Option<String> {
        let r = self.ignored_by.clone()?;
        if page && !rule_file_inside(&r) {
            return None;
        }
        Some(r)
    }

    /// Part 1, what rb found, with the parts drawn in monospace (the rule) and the commit it names (PARITY 2.2).
    fn found(&self, when: &When) -> Line {
        let w = when.said();
        let c12 = self.commit.as_deref().map(short).unwrap_or_default();
        let held = if self.record_held == Some(false) { ", which does not hold the record" } else { "" };
        let mut line = Line::of("found", String::new());
        let since = |n: usize, m: Option<usize>| -> String {
            match m {
                Some(m) if n == m && m == 1 => format!("its 1 record file is new, changed or deleted since commit {c12}{held}"),
                Some(m) if n == m => format!("all {m} record files are new, changed or deleted since commit {c12}{held}"),
                Some(m) => format!("{n} of its {m} record files {} new, changed or deleted since commit {c12}{held}", if n == 1 { "is" } else { "are" }),
                None => format!("{} {} new, changed or deleted since commit {c12}{held}", files(n), if n == 1 { "is" } else { "are" }),
            }
        };
        line.text = match self.state {
            State::NotInGit => format!("rb looked for a git repository {w}: the project's folder is not in one, so no commit holds the record."),
            State::GitNotFound => format!("rb could not find git {w}, so it could not say what is committed."),
            State::Unreadable if self.timed_out => format!("rb asked git {w}, and git did not answer, so rb could not say what is committed."),
            State::Unreadable => format!("rb asked git {w}, and git failed, so rb could not say what is committed."),
            State::NoCommit => format!("rb checked the record with git {w}: the repository has no commit yet, so no commit holds the record."),
            State::Committed => match self.record_files() {
                Some(1) => format!("rb checked the record with git {w}: its 1 record file is in commit {c12}."),
                Some(m) => format!("rb checked the record with git {w}: all {m} record files are in commit {c12}."),
                None => format!("rb checked the record with git {w}: all record files are in commit {c12}."),
            },
            State::NotCommitted => format!("rb checked the record with git {w}: {}.", since(self.record_files_not_committed, self.record_files())),
            State::Ignored => {
                let k = self.record_files_ignored;
                let (them, all) = if k == 1 { ("it", false) } else { ("them", true) };
                let rule = self.rule_said(when.page());
                let more = match self.more_rules {
                    0 => String::new(),
                    n => format!(" and {}", plural(n, "more rule", "more rules")),
                };
                let by = match &rule {
                    Some(r) => {
                        line.code.push(r.clone());
                        format!(" ({r}{more})")
                    }
                    None if self.ignored_by.is_some() => format!(" {OUTSIDE_RULE}"),
                    None => String::new(),
                };
                if self.commit.is_none() {
                    format!("rb checked the record with git {w}: the repository has no commit yet, and git ignores {}{by}, so a commit leaves {them} out.", files(k))
                } else {
                    let Some(others) = self.record_files() else {
                        // git could not count the others: say only what it said of the ignored ones
                        return Line { text: format!("rb checked the record with git {w}: git ignores {}{by}, so a commit leaves {them} out.", files(k)), ..line };
                    };
                    let n = self.record_files_not_committed;
                    let ignores = if others == 0 && all { format!("all {}", files(k)) } else if others == 0 { "its 1 record file".to_string() } else { files(k) };
                    let rest = if others == 0 {
                        String::new()
                    } else if n == 0 && others == 1 {
                        format!("; the other one is in commit {c12}{held}")
                    } else if n == 0 {
                        format!("; the other {others} are in commit {c12}{held}")
                    } else if others == 1 {
                        format!("; the other one is new, changed or deleted since commit {c12}{held}")
                    } else if n == others {
                        format!("; all {others} others are new, changed or deleted since commit {c12}{held}")
                    } else {
                        format!("; {n} of the other {others} {} new, changed or deleted since commit {c12}{held}", if n == 1 { "is" } else { "are" })
                    };
                    format!("rb checked the record with git {w}: git ignores {ignores}{by}, so a commit leaves {them} out{rest}.")
                }
            }
        };
        // the commit whole, only where part 1 names it (PARITY 2.6)
        if self.in_git() && !c12.is_empty() && line.text.contains(&format!("commit {c12}")) {
            line.sha = self.commit.clone();
        }
        line
    }

    /// Part 4: which files, the ignored line first (PARITY 2.5). Never with no commit yet for files not committed,
    /// never where git did not answer.
    fn names_lines(&self) -> Vec<Line> {
        let mut out = Vec::new();
        if !self.in_git() {
            return out;
        }
        if self.record_files_ignored > 0 && !self.ignored_names.is_empty() {
            let shown: Vec<String> = self.ignored_names.iter().take(NAMES_SAID).cloned().collect();
            out.push(Line { code: shown.clone(), ..Line::of("files", format!("Ignored by git: {}", names_said(&shown, self.record_files_ignored))) });
        }
        if self.commit.is_some() && self.record_files_not_committed > 0 && !self.not_committed_names.is_empty() {
            let shown: Vec<&Named> = self.not_committed_names.iter().take(NAMES_SAID).collect();
            let said: Vec<String> = shown.iter().map(|n| format!("{} ({})", n.path, if n.change == "unmerged" { "in a merge conflict" } else { n.change })).collect();
            out.push(Line { code: shown.iter().map(|n| n.path.clone()).collect(), ..Line::of("files", format!("Not committed: {}", names_said(&said, self.record_files_not_committed))) });
        }
        out
    }

    /// The evidence on your own screens (rb open, rb ui, rb status, rb context, JSON): parts 1 and 3 to 7, in order
    /// (PARITY 1). Each surface leaves out only what its own rules forbid.
    pub fn evidence(&self, when: &When) -> Vec<Line> {
        let mut out = vec![self.found(when)];
        if self.state == State::Unreadable && !self.timed_out {
            if let Some(why) = &self.why {
                out.push(Line { code: vec![why.clone()], ..Line::of("git", why.clone()) });
            }
        }
        out.extend(self.names_lines());
        // beside a commit only, as data.repository.line says it
        if self.changes_outside_record && self.in_git() && self.commit.is_some() {
            out.push(Line::of("code", CODE_SAYS.into()));
        }
        if let Some(cmd) = self.check() {
            let (lead, says) = match self.state {
                State::Unreadable => (ASK_LEAD, None),
                State::Ignored => (CHECK_LEAD, Some(IGNORE_SAYS)),
                _ => (CHECK_LEAD, Some(STATUS_SAYS)),
            };
            out.push(Line { cmd: Some(cmd), says: says.map(str::to_string), ..Line::of("check", lead.into()) });
        }
        Self::define(&mut out);
        out
    }

    /// The evidence on a page (PARITY 2.2-2.5, 2.9, 9.3): part 1 in its page form, P, which files, and what record
    /// files are. Never git's words, a place's path, a branch or a rule from outside the repository's files.
    pub fn page_evidence(&self, minute: &str) -> Vec<Line> {
        let mut out = vec![self.found(&When::Page(minute.to_string()))];
        let c12 = self.commit.as_deref().map(short).unwrap_or_default();
        let unsaved = self.record_files_not_committed > 0 || self.record_files_ignored > 0;
        if self.commit.is_some() && self.in_git() && unsaved {
            match self.record_held {
                Some(false) => out.push(Line::of("page", format!("This page shows the record as it stood on disk; a clone of commit {c12} has no record."))),
                Some(true) => out.push(Line::of("page", format!("This page shows the record as it stood on disk; a clone of commit {c12} has the record as that commit holds it."))),
                None => {}
            }
        }
        out.extend(self.names_lines());
        Self::define(&mut out);
        out
    }

    fn define(out: &mut Vec<Line>) {
        if out.iter().any(|l| l.text.contains("record file") || l.says.as_deref().map_or(false, |s| s.contains("record file"))) {
            out.push(Line::of("define", RECORD_FILES_ARE.into()));
        }
    }

    /// rb context's first bullet (PARITY 7): the status line, the commit, the changes outside the record, and not what
    /// one step down adds, which its nested bullets say.
    pub fn context_head_of(repository: &Value) -> Option<String> {
        let mut s = Self::status_line_of(repository)?;
        let in_git = repository.get("state").and_then(Value::as_str).map_or(false, |st| !["not_in_git", "git_not_found", "unreadable"].contains(&st));
        if let Some(c) = repository.get("commit").and_then(Value::as_str).filter(|_| in_git) {
            s.push_str(&format!(" \u{b7} at commit {}", short(c)));
        }
        // as data.repository.line says them: beside a commit only
        if in_git && repository.get("commit").map_or(false, |c| !c.is_null()) && repository.get("changes_outside_record").and_then(Value::as_bool) == Some(true) {
            s.push_str(", with changes outside the record not committed");
        }
        Some(s)
    }

    /// data.repository's evidence keys (PARITY 10), into `d`.
    pub fn add_evidence(&self, when: &When, d: &mut crate::value::Dict) {
        d.insert("record_files".into(), self.record_files().map(|m| Value::Int(m as i64)).unwrap_or(Value::Null));
        d.insert(
            "not_committed_files".into(),
            Value::List(
                self.not_committed_names
                    .iter()
                    .map(|n| {
                        crate::obj! {"path" => n.path.clone(), "change" => n.change, "from" => n.from.clone().map(Value::Str).unwrap_or(Value::Null)}
                    })
                    .collect(),
            ),
        );
        d.insert("ignored_files".into(), Value::list_of_str(self.ignored_names.clone()));
        d.insert("checked_at".into(), when.iso().map(|s| Value::Str(s.into())).unwrap_or(Value::Null));
        d.insert("check".into(), self.check().map(Value::Str).unwrap_or(Value::Null));
        d.insert("evidence".into(), Value::List(self.evidence(when).iter().map(|l| l.to_value(Some(when))).collect()));
    }

    /// The evidence as rb ui prints it, one string a line (PARITY 5): the commit whole under part 1, and the command
    /// on a line of its own, indented two spaces, so a selection takes it alone.
    pub fn evidence_text(&self, when: &When) -> Vec<String> {
        let mut out = Vec::new();
        for l in self.evidence(when) {
            match l.kind {
                "check" => {
                    out.push(l.text.clone());
                    out.push(format!("  {}", l.cmd.clone().unwrap_or_default()));
                    out.extend(l.says.clone());
                }
                _ => {
                    out.push(l.text.clone());
                    out.extend(l.sha.clone());
                }
            }
        }
        out
    }
}

/// data.repository's evidence lines of the kinds given, as rb status prints them: one line each, a check's lead, a
/// space, then its command.
pub fn evidence_of(repository: Option<&Value>, kinds: &[&str]) -> Vec<String> {
    let lines = repository.and_then(|r| r.get("evidence")).and_then(Value::as_list).cloned().unwrap_or_default();
    lines
        .iter()
        .filter(|l| kinds.contains(&l.get("kind").and_then(Value::as_str).unwrap_or("")))
        .map(|l| {
            let text = l.get("text").and_then(Value::as_str).unwrap_or("").to_string();
            match l.get("cmd").and_then(Value::as_str) {
                Some(c) => format!("{text} {c}"),
                None => text,
            }
        })
        .collect()
}

/// data.repository's evidence as rb context's nested bullets (PARITY 7): part 1, git's line in backticks, the names
/// each in backticks, and the check as "<lead> `<command>`. <what it prints>".
pub fn evidence_md(repository: Option<&Value>) -> Vec<String> {
    let lines = repository.and_then(|r| r.get("evidence")).and_then(Value::as_list).cloned().unwrap_or_default();
    let mut out = Vec::new();
    for l in &lines {
        let text = l.get("text").and_then(Value::as_str).unwrap_or("");
        let code: Vec<String> = l.get("code").and_then(Value::as_list).map(|c| c.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default();
        match l.get("kind").and_then(Value::as_str).unwrap_or("") {
            "found" | "files" => out.push(segments(text, &code).iter().map(|(m, p)| if *m { format!("`{p}`") } else { p.to_string() }).collect()),
            "git" => out.push(format!("`{text}`")),
            "check" => {
                let cmd = l.get("cmd").and_then(Value::as_str).unwrap_or("");
                let says = l.get("says").and_then(Value::as_str).map(|s| format!(" {s}")).unwrap_or_default();
                out.push(format!("{text} `{cmd}`.{says}"));
            }
            _ => {}
        }
    }
    out
}

// ---------------------------------------------------------------- a move of HEAD, as rb's look saw it

/// A move of HEAD between two looks, in the words rb journal and rb ui say: "new commit" or "3 new commits" (HEAD moved
/// to a descendant, counted with git rev-list, never read from the reflog's message), "another commit checked out" (any
/// other move), or "no commit yet" (HEAD names no commit now). None where HEAD did not move: a change of the branch
/// alone, or of what the working tree changes, is no move.
pub fn move_words(root: &Path, from: Option<&str>, to: Option<&str>) -> Option<String> {
    move_words_seen(root, from, to, false)
}

/// The same, where `seen` says HEAD named `to` before (rb's look saw it there): going back to it, or to any commit HEAD
/// has named, is "another commit checked out", never "new commit".
pub fn move_words_seen(root: &Path, from: Option<&str>, to: Option<&str>, seen: bool) -> Option<String> {
    if seen && to.is_some() && from != to {
        return Some("another commit checked out".into());
    }
    let count = |range: &str| sources::git(root, &["rev-list", "--count", range]).and_then(|n| n.trim().parse::<usize>().ok());
    let new = |n: usize| if n == 1 { "new commit".to_string() } else { format!("{n} new commits") };
    match (from, to) {
        (Some(_), None) => Some("no commit yet".into()),
        (None, None) => None,
        (Some(a), Some(b)) if a == b => None,
        // the first commit of a repository that had none
        (None, Some(b)) => Some(count(b).filter(|n| *n > 0).map_or_else(|| "another commit checked out".to_string(), new)),
        (Some(a), Some(b)) => {
            let descends = sources::git_full(root, &["merge-base", "--is-ancestor", a, b], None).map_or(false, |r| r.code == 0);
            Some(match count(&format!("{a}..{b}")).filter(|n| descends && *n > 0) {
                Some(n) => new(n),
                None => "another commit checked out".into(),
            })
        }
    }
}

/// The move's words, then " · at commit <12>" where HEAD names one: rb journal's line and rb ui's Activity.
pub fn move_line(root: &Path, from: Option<&str>, to: Option<&str>, seen: bool) -> Option<String> {
    let words = move_words_seen(root, from, to, seen)?;
    Some(match to {
        Some(c) => format!("{words} \u{b7} at commit {}", short(c)),
        None => words,
    })
}

/// Each journal entry's move of HEAD from the git entry before it, as (HEAD then, HEAD now), for the git entries at or
/// after `start`, whether or not the one before is: None for every other entry, for the first git entry rb saw (no
/// move), and for an entry rb cannot read, which is passed over. rb journal, rb ui's Activity and its OBSERVED panel
/// word these with move_line or move_words.
pub fn head_moves(entries: &[crate::value::Dict], start: usize) -> Vec<Option<(Option<String>, Option<String>, bool)>> {
    let mut out = vec![None; entries.len()];
    let mut before: Option<Option<String>> = None;
    // every commit HEAD named at an earlier look: going back to one is no new commit
    let mut named: std::collections::HashSet<String> = std::collections::HashSet::new();
    for (i, e) in entries.iter().enumerate() {
        if e.get("op").and_then(|v| v.as_str()) != Some("git") {
            continue;
        }
        let commit = match e.get("commit") {
            // an empty commit names none, as an earlier rb could write it
            Some(Value::Str(c)) if c.is_empty() => None,
            Some(Value::Str(c)) => Some(c.clone()),
            None | Some(Value::Null) => None,
            Some(_) => continue,
        };
        if i >= start {
            let seen = commit.as_ref().map_or(false, |c| named.contains(c));
            out[i] = before.clone().map(|was| (was, commit.clone(), seen));
        }
        if let Some(c) = &commit {
            named.insert(c.clone());
        }
        before = Some(commit);
    }
    out
}

/// Each git entry at or after `start` gains `said`: its move of HEAD in move_line's words, null where HEAD did not move
/// (rb journal --json and the bridge's journal op).
pub fn say_moves(root: &Path, entries: &mut [crate::value::Dict], start: usize) {
    let moves = head_moves(entries, start);
    for (i, (e, m)) in entries.iter_mut().zip(moves).enumerate() {
        let readable = matches!(e.get("commit"), None | Some(Value::Null) | Some(Value::Str(_)));
        if i < start || e.get("op").and_then(|v| v.as_str()) != Some("git") || !readable {
            continue;
        }
        let said = m.and_then(|(was, now, seen)| move_line(root, was.as_deref(), now.as_deref(), seen));
        e.insert("said".into(), said.map(Value::Str).unwrap_or(Value::Null));
    }
}

// ---------------------------------------------------------------- your checkout: the code the runs ran, compared

/// The checkout a screen or a command compares with the code the runs ran: the folder it started in (rb open's, or the
/// command's; for a worktree with no record of its own, that worktree). On rb open `listed` holds the paths that
/// checkout's status lists (from its top), whose sizes and times key the memo, so an edit to one is compared again;
/// None compares afresh every time (a command runs once).
#[derive(Debug, Clone, PartialEq)]
pub struct Viewer {
    pub dir: PathBuf,
    pub listed: Option<Vec<String>>,
}

impl Viewer {
    /// The checkout a command compares with: the folder it runs in, where that is a checkout of the record's clone (a
    /// worktree with no record of its own, whose code its runs keep), else the record's own checkout. Compared afresh.
    pub fn here(record_root: &Path) -> Viewer {
        let common = |d: &Path| sources::git(d, &["rev-parse", "--git-common-dir"]).map(|c| resolve_path(&d.join(c.trim())));
        let dir = std::env::current_dir().ok().filter(|here| common(here).is_some() && common(here) == common(record_root)).unwrap_or_else(|| record_root.to_path_buf());
        Viewer { dir, listed: None }
    }
}

/// The line where git failed or did not answer: never silence, which would read as the same code.
pub const COULD_NOT_COMPARE: &str = "rb could not compare your checkout";
/// The line where every file the runs' code held is as it was: said in words, never left to silence.
pub const HAS_THIS_CODE: &str = "your checkout has this code";
/// How many differing files the data names; the rest are counted (`more`).
const FILES_KEPT: usize = 10;

/// Your checkout against the code the runs ran: `same`, `differs` (the files named, at most ten, and how many more) or
/// `unknown` (git failed), the line every surface prints as made here, and on `differs` the command that shows the diff.
#[derive(Debug, Clone, PartialEq)]
pub struct Compared {
    pub state: &'static str,
    pub said: String,
    pub files: Vec<String>,
    pub more: usize,
    pub diff: String,
}

impl Compared {
    pub fn to_value(&self) -> Value {
        crate::obj! {"state" => self.state, "said" => self.said.clone(), "files" => Value::list_of_str(self.files.clone()), "more" => self.more as i64, "diff" => self.diff.clone()}
    }
}

/// "your checkout differs in 1 file: train.py", "... in 2 files: eval.py and quant/calibrate.py", "... in 14 files: a,
/// b, c and 11 more": three names at most, as git gives them, of `total` files.
pub fn differs_said(files: &[String], total: usize) -> String {
    let names = if total <= 3 || files.len() < 3 { crate::next_step::and_list(files) } else { format!("{} and {} more", files[..3].join(", "), total - 3) };
    format!("your checkout differs in {}: {names}", plural(total, "file", "files"))
}

/// The command that shows the diff: from the repository's top, whatever folder it runs in, the record's folder (`record`,
/// from the top) left out. It changes nothing.
pub fn diff_command(treeish: &str, record: &str) -> String {
    format!("git diff {treeish} -- \":(top)\" \":(top,exclude){record}\"")
}

static COMPARED: std::sync::Mutex<Option<std::collections::HashMap<String, Compared>>> = std::sync::Mutex::new(None);

/// Whether your checkout has the code the runs ran: `treeish` (the runs' commit, or the git tree rb kept of their
/// uncommitted changes) against your working tree, one `git diff` run on a copy of the index, so the index is never
/// written. Only paths the runs' commit holds count (`holds`, whatever git's letter: one the kept tree had deleted that
/// your tree has counts too), the record (`record`, its folder from the top) and the files the runs name (`named`) left
/// out: a commit of the record alone, a file added since or an untracked result is never a difference. None where
/// there is nothing to compare with: your folder is not in git, or has no commit yet.
pub fn compare(viewer: &Viewer, treeish: &str, record: &str, holds: &std::collections::BTreeMap<String, String>, named: &std::collections::HashSet<String>) -> Option<Compared> {
    let top = sources::git_top(&viewer.dir)?;
    sources::git(&top, &["rev-parse", "--verify", "-q", "HEAD"])?;
    // on rb open: the same code, HEAD, index and listed files' sizes and times compare the same
    let key = viewer.listed.as_ref().map(|listed| {
        let mut named: Vec<&String> = named.iter().collect();
        named.sort();
        let stat = |p: &Path| crate::dry::size_time(p).map_or("-".to_string(), |(n, t)| format!("{n}:{t}"));
        let git: Vec<String> = crate::places::git_files(&top).iter().map(|f| stat(f)).collect();
        let files: Vec<String> = listed.iter().map(|p| format!("{p}={}", stat(&top.join(p)))).collect();
        format!("{}\0{treeish}\0{record}\0{}\0{}\0{}", crate::sys::slashed(&top), named.iter().map(|s| s.as_str()).collect::<Vec<_>>().join("\n"), git.join(","), files.join("\n"))
    });
    if let Some(k) = &key {
        if let Some(hit) = COMPARED.lock().unwrap_or_else(|e| e.into_inner()).as_ref().and_then(|m| m.get(k)) {
            return Some(hit.clone());
        }
    }
    let exclude = format!(":(top,exclude){record}");
    let got = sources::git_diff_bytes(&top, &["-z", "--name-status", "--no-renames", treeish, "--", ":(top)", &exclude]);
    let Some(listed) = got else {
        return Some(Compared { state: "unknown", said: COULD_NOT_COMPARE.into(), files: Vec::new(), more: 0, diff: String::new() });
    };
    let inside = format!("{record}/");
    // a status, then its path, each ended by a NUL
    let fields: Vec<String> = listed.split(|b| *b == 0).map(|f| String::from_utf8_lossy(f).into_owned()).collect();
    let mut differ: Vec<String> = fields
        .chunks(2)
        .filter(|c| c.len() == 2)
        .map(|c| c[1].clone())
        .filter(|p| !p.is_empty() && holds.contains_key(p) && *p != record && !p.starts_with(&inside) && !named.contains(p))
        .collect();
    differ.sort();
    differ.dedup();
    let out = if differ.is_empty() {
        Compared { state: "same", said: HAS_THIS_CODE.into(), files: Vec::new(), more: 0, diff: String::new() }
    } else {
        let total = differ.len();
        let files: Vec<String> = differ.into_iter().take(FILES_KEPT).collect();
        Compared { state: "differs", said: differs_said(&files, total), more: total - files.len(), files, diff: diff_command(treeish, record) }
    };
    if let Some(k) = key {
        let mut held = COMPARED.lock().unwrap_or_else(|e| e.into_inner());
        let map = held.get_or_insert_with(std::collections::HashMap::new);
        if map.len() > 64 {
            map.clear();
        }
        map.insert(k, out.clone());
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rb_s_own_ignores_never_count() {
        for own in ["cache/x", "cache/", "journal/a/b", ".lock", "x.tmp", "experiments/e1/.lock"] {
            assert!(rb_ignores(own), "{own}");
        }
        for not in ["log.jsonl", "project.yaml", "experiments/cache.yaml", "cached/x", "notes/n1.yaml"] {
            assert!(!rb_ignores(not), "{not}");
        }
    }

    #[test]
    fn an_empty_commit_in_the_journal_names_none() {
        let git = |c: &str| crate::dict! {"op" => "git", "commit" => c};
        let entries = vec![git(""), git(""), git("9fa4f89a1b2c3d4e5f60718293a4b5c6d7e8f901")];
        let moves = head_moves(&entries, 0);
        assert_eq!(moves[1], Some((None, None, false)));
        assert_eq!(moves[2], Some((None, Some("9fa4f89a1b2c3d4e5f60718293a4b5c6d7e8f901".into()), false)));
    }

    #[test]
    fn the_words_say_each_state() {
        let mut c = Checkout::bare(State::Committed, "int8-resnet50".into());
        c.commit = Some("9fa4f89a1b2c3d4e5f60718293a4b5c6d7e8f901".into());
        c.branch = Some("main".into());
        c.on_branch = true;
        assert_eq!(c.said(), "all record files committed");
        assert_eq!(c.page_line(), "all record files committed \u{b7} at commit 9fa4f89a1b2c");
        c.state = State::NotCommitted;
        c.record_files_not_committed = 1;
        assert_eq!(c.said(), "1 record file not committed");
        c.record_files_not_committed = 2;
        c.changes_outside_record = true;
        assert_eq!(c.line(), "2 record files not committed \u{b7} at commit 9fa4f89a1b2c, with changes outside the record not committed");
        assert_eq!(c.page_line(), "2 record files not committed \u{b7} at commit 9fa4f89a1b2c");
        c.branch = None;
        c.on_branch = false;
        assert_eq!(c.name(), "no branch");
        assert_eq!(c.status_line(), "no branch \u{b7} 2 record files not committed");
        let n = Checkout::bare(State::NotInGit, "int8-resnet50".into());
        assert_eq!((n.said(), n.name(), n.status_line(), n.page_line()), ("not in git".into(), "int8-resnet50".into(), "not in git".into(), "not in git".into()));
    }

    #[test]
    fn your_checkout_names_three_files_at_most() {
        let f = |n: usize| (0..n.min(10)).map(|i| format!("f{i}.py")).collect::<Vec<_>>();
        assert_eq!(differs_said(&["train.py".into()], 1), "your checkout differs in 1 file: train.py");
        assert_eq!(differs_said(&["eval.py".into(), "quant/calibrate.py".into()], 2), "your checkout differs in 2 files: eval.py and quant/calibrate.py");
        assert_eq!(differs_said(&f(3), 3), "your checkout differs in 3 files: f0.py, f1.py and f2.py");
        assert_eq!(differs_said(&f(14), 14), "your checkout differs in 14 files: f0.py, f1.py, f2.py and 11 more");
        assert_eq!(diff_command("6e49d99436a1b2c3d4e5f60718293a4b5c6d7e8f", "sub/.rb"), "git diff 6e49d99436a1b2c3d4e5f60718293a4b5c6d7e8f -- \":(top)\" \":(top,exclude)sub/.rb\"");
    }

    /// A git repository in a scratch folder, with the INT8 example's record files: (its folder, run git there).
    fn repo(name: &str) -> (PathBuf, impl Fn(&[&str])) {
        let d = std::env::temp_dir().join(format!("rb-co-{name}-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(d.join(".rb/notes")).unwrap();
        let d = crate::sys::canonicalize(&d).unwrap();
        let top = d.clone();
        let run = move |args: &[&str]| {
            let ok = std::process::Command::new("git").arg("-C").arg(&top).args(["-c", "user.name=ana", "-c", "user.email=ana@example.org"]).args(args).output().unwrap();
            assert!(ok.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&ok.stderr));
        };
        run(&["init", "-q", "-b", "main"]);
        std::fs::write(d.join(".rb/.gitignore"), "cache/\njournal/\n.lock\n*.tmp\n").unwrap();
        std::fs::write(d.join(".rb/project.yaml"), "rb: 1\nkind: project\ntitle: INT8 ResNet-50\n").unwrap();
        std::fs::write(d.join(".rb/log.jsonl"), "{}\n").unwrap();
        std::fs::write(d.join(".rb/notes/n1.yaml"), "rb: 1\nkind: note\n").unwrap();
        (d, run)
    }

    /// One count, every case: a deletion counts, a staged rename counts once, rb's own ignores never count.
    #[test]
    fn the_count_takes_deletions_and_a_rename_once() {
        let (d, git) = repo("count");
        git(&["add", "-A"]);
        git(&["commit", "-q", "-m", "x"]);
        assert_eq!(Checkout::of(&d).said(), "all record files committed");
        std::fs::create_dir_all(d.join(".rb/cache")).unwrap();
        std::fs::write(d.join(".rb/cache/x.json"), "{}").unwrap();
        std::fs::write(d.join(".rb/.lock"), "").unwrap();
        assert_eq!(Checkout::of(&d).said(), "all record files committed");
        git(&["mv", ".rb/notes/n1.yaml", ".rb/notes/n2.yaml"]);
        let c = Checkout::of(&d);
        assert_eq!((c.said(), c.count(), c.count_said()), ("1 record file not committed".into(), 1, Some("1 record file not committed".into())));
        std::fs::remove_file(d.join(".rb/log.jsonl")).unwrap();
        assert_eq!(Checkout::of(&d).said(), "2 record files not committed");
        assert_eq!(Checkout::of(&d).name(), "main");
        // on no branch
        git(&["checkout", "-q", "--detach"]);
        let c = Checkout::of(&d);
        assert_eq!((c.name(), c.status_line()), ("no branch".into(), "no branch \u{b7} 2 record files not committed".into()));
        let _ = std::fs::remove_dir_all(&d);
    }

    /// A record git ignores (.rb/ in .gitignore) is said as ignored, whatever else is not committed, and a place's line
    /// says so; with no commit yet too.
    #[test]
    fn an_ignored_record_is_said_ignored() {
        let (d, git) = repo("ignored");
        std::fs::write(d.join(".gitignore"), ".rb/\n").unwrap();
        let c = Checkout::of(&d);
        assert_eq!((c.state, c.said(), c.count()), (State::Ignored, "4 record files ignored by git".into(), 4));
        assert_eq!(c.ignored_by.as_deref(), Some(".gitignore:1:.rb/"));
        assert_eq!(c.place_said().as_deref(), Some("4 record files ignored by git"));
        git(&["add", ".gitignore"]);
        git(&["commit", "-q", "-m", "x"]);
        assert_eq!(Checkout::of(&d).said(), "4 record files ignored by git");
        let _ = std::fs::remove_dir_all(&d);
    }

    fn texts(lines: &[Line]) -> Vec<String> {
        lines.iter().map(|l| match &l.cmd { Some(c) => format!("{} {c}", l.text), None => l.text.clone() }).collect()
    }

    /// PARITY 2.2-2.9: each state's lines, in each `<when>` form and in the screen and page forms.
    #[test]
    fn the_evidence_says_each_state() {
        let sha = "9fa4f89a1b2c3d4e5f60718293a4b5c6d7e8f901";
        let at = When::At("2026-11-09T09:41:07Z".into());
        let page = "2026-11-08 09:41 UTC";
        let mut c = Checkout { root: PathBuf::from("/home/ana/int8-resnet50"), commit: Some(sha.into()), ..Checkout::bare(State::NoCommit, "int8-resnet50".into()) };
        c.commit = None;
        assert_eq!(texts(&c.evidence(&at)), [
            "rb checked the record with git at 2026-11-09 09:41 UTC: the repository has no commit yet, so no commit holds the record.",
            "To check it yourself, in your terminal: git -C /home/ana/int8-resnet50 status --short --untracked-files=all -- .rb",
            "Record files are the files rb keeps for this project next to its code, such as its questions, rules, runs, notes and log.",
        ]);
        // the small page: part 1 alone
        assert_eq!(texts(&c.page_evidence(page)), ["rb checked the record with git when this page was made, 2026-11-08 09:41 UTC: the repository has no commit yet, so no commit holds the record."]);
        // with no commit, nothing is said of the code: data.repository.line says it beside a commit only
        c.changes_outside_record = true;
        assert!(c.evidence(&at).iter().all(|l| l.kind != "code"));
        c.changes_outside_record = false;
        assert_eq!(c.evidence(&When::Live("x".into()))[0].text, "rb checked the record with git just now: the repository has no commit yet, so no commit holds the record.");
        // not committed, at a commit that holds the record, with changes outside it
        c.state = State::NotCommitted;
        c.commit = Some(sha.into());
        c.record_files_not_committed = 2;
        c.record_held = Some(true);
        c.changes_outside_record = true;
        c.not_committed_names = vec![
            Named { path: ".rb/experiments/e1/runs/run-k7.yaml".into(), change: "new", from: None },
            Named { path: ".rb/log.jsonl".into(), change: "changed", from: None },
        ];
        let e = c.evidence(&at);
        assert_eq!(e[0].sha.as_deref(), Some(sha));
        assert_eq!(texts(&e)[1..], [
            "Not committed: .rb/experiments/e1/runs/run-k7.yaml (new) and .rb/log.jsonl (changed)".to_string(),
            "This checkout has changes outside the record not committed.".into(),
            "To check it yourself, in your terminal: git -C /home/ana/int8-resnet50 status --short --untracked-files=all -- .rb".into(),
            RECORD_FILES_ARE.into(),
        ]);
        assert_eq!(e[3].says.as_deref(), Some(STATUS_SAYS));
        let p = c.page_evidence(page);
        assert_eq!(p[1].text, "This page shows the record as it stood on disk; a clone of commit 9fa4f89a1b2c has the record as that commit holds it.");
        assert!(p.iter().all(|l| !matches!(l.kind, "code" | "check" | "git")));
        c.record_held = Some(false);
        assert_eq!(c.page_evidence(page)[1].text, "This page shows the record as it stood on disk; a clone of commit 9fa4f89a1b2c has no record.");
        // the found line's words, with M given (no git asked): its counts and plurals
        let found = |c: &Checkout| c.found(&at).text;
        let mut m = c.clone();
        m.top = None;
        assert_eq!(found(&m), "rb checked the record with git at 2026-11-09 09:41 UTC: 2 record files are new, changed or deleted since commit 9fa4f89a1b2c, which does not hold the record.");
        // ignored, all of them, with the rule; a page quotes it only from inside the repository
        let mut g = Checkout { state: State::Ignored, record_files_ignored: 12, record_files_not_committed: 0, ignored_by: Some(".gitignore:4:.rb/".into()), more_rules: 1, record_held: Some(true), ..c.clone() };
        g.top = None;
        g.changes_outside_record = false;
        g.ignored_names = vec![".rb/.gitattributes".into(), ".rb/.gitignore".into(), ".rb/experiments/e1/e1.yaml".into(), ".rb/project.yaml".into()];
        let e = g.evidence(&at);
        // with no count of the other record files (no ls-tree here, or one that failed), only what git said of these
        // (review S6 4); the "all" form is pinned where git counts them (tests/test_page_git_block.py)
        assert_eq!(e[0].text, "rb checked the record with git at 2026-11-09 09:41 UTC: git ignores 12 record files (.gitignore:4:.rb/ and 1 more rule), so a commit leaves them out.");
        assert_eq!(e[0].code, [".gitignore:4:.rb/"]);
        // the commit whole only where part 1 names it: here it names none (review S6 2)
        assert_eq!(e[0].sha, None);
        let v = g.to_value();
        assert_eq!(v.get("record_held"), Some(&Value::Null), "0.8.4's value: asked only where files are not committed");
        assert_eq!(e[1].text, "Ignored by git: .rb/.gitattributes, .rb/.gitignore, .rb/experiments/e1/e1.yaml and 9 more");
        assert_eq!(e[2].cmd.as_deref(), Some("git -C /home/ana/int8-resnet50 check-ignore -v .rb/project.yaml"));
        assert_eq!(e[2].says.as_deref(), Some(IGNORE_SAYS));
        g.ignored_by = Some("/home/ana/.config/git/ignore:2:*.jsonl".into());
        assert_eq!(g.page_evidence(page)[0].text, "rb checked the record with git when this page was made, 2026-11-08 09:41 UTC: git ignores 12 record files by a rule outside the repository's files, so a commit leaves them out.");
        assert!(g.page_evidence(page)[0].code.is_empty());
        assert!(g.evidence(&at)[0].text.contains("(/home/ana/.config/git/ignore:2:*.jsonl and 1 more rule)"));
        g.commit = None;
        g.more_rules = 0;
        g.ignored_by = Some(".gitignore:4:.rb/".into());
        assert_eq!(g.found(&at).text, "rb checked the record with git at 2026-11-09 09:41 UTC: the repository has no commit yet, and git ignores 12 record files (.gitignore:4:.rb/), so a commit leaves them out.");
        // where git cannot say
        let bare = |s: State| Checkout { root: PathBuf::from("/srv/int8"), ..Checkout::bare(s, "int8".into()) };
        assert_eq!(texts(&bare(State::NotInGit).evidence(&at)), ["rb looked for a git repository at 2026-11-09 09:41 UTC: the project's folder is not in one, so no commit holds the record."]);
        assert_eq!(texts(&bare(State::GitNotFound).evidence(&at)), ["rb could not find git at 2026-11-09 09:41 UTC, so it could not say what is committed."]);
        let mut u = bare(State::Unreadable);
        u.why = Some("fatal: detected dubious ownership in repository at '/srv/int8'".into());
        assert_eq!(texts(&u.evidence(&at)), [
            "rb asked git at 2026-11-09 09:41 UTC, and git failed, so rb could not say what is committed.",
            "fatal: detected dubious ownership in repository at '/srv/int8'",
            "To ask git yourself, in your terminal: git -C /srv/int8 status --short --untracked-files=all -- .rb",
        ]);
        // git's line never reaches a page
        assert_eq!(texts(&u.page_evidence(page)), ["rb asked git when this page was made, 2026-11-08 09:41 UTC, and git failed, so rb could not say what is committed."]);
        let t = Checkout { timed_out: true, ..bare(State::Unreadable) };
        assert_eq!(texts(&t.evidence(&at))[..1], ["rb asked git at 2026-11-09 09:41 UTC, and git did not answer, so rb could not say what is committed.".to_string()]);
        assert_eq!(t.doctor_problem().map(|p| p.2), Some("rb doctor again once git answers"));
    }

    /// M holds every one of N: a note adds 2 to N and 1 to M, git rm adds 1 to N and none to M, a staged rename adds 1
    /// to N and none to M, rb's own ignores never count (PARITY 3).
    #[test]
    fn m_holds_every_one_of_n() {
        let (d, git) = repo("m");
        git(&["add", "-A"]);
        git(&["commit", "-q", "-m", "x"]);
        let nm = || {
            let c = Checkout::of(&d);
            (c.record_files_not_committed, c.record_files().unwrap())
        };
        assert_eq!(nm(), (0, 4));
        std::fs::write(d.join(".rb/notes/n3.yaml"), "rb: 1\nkind: note\n").unwrap();
        std::fs::write(d.join(".rb/log.jsonl"), "{}\n{}\n").unwrap();
        assert_eq!(nm(), (2, 5));
        std::fs::create_dir_all(d.join(".rb/cache")).unwrap();
        std::fs::write(d.join(".rb/cache/x.json"), "{}").unwrap();
        std::fs::write(d.join(".rb/.lock"), "").unwrap();
        std::fs::write(d.join(".rb/w.tmp"), "").unwrap();
        assert_eq!(nm(), (2, 5));
        git(&["rm", "-q", ".rb/notes/n1.yaml"]);
        assert_eq!(nm(), (3, 5));
        git(&["mv", ".rb/project.yaml", ".rb/project2.yaml"]);
        assert_eq!(nm(), (4, 5));
        let c = Checkout::of(&d);
        let names: Vec<(String, &str)> = c.not_committed_names.iter().map(|n| (n.path.clone(), n.change)).collect();
        assert!(names.contains(&(".rb/project2.yaml".into(), "renamed")) && names.contains(&(".rb/notes/n1.yaml".into(), "deleted")), "{names:?}");
        assert_eq!(c.not_committed_names.iter().find(|n| n.change == "renamed").and_then(|n| n.from.clone()).as_deref(), Some(".rb/project.yaml"));
        // an intent-to-add file is new (review S6 1)
        std::fs::write(d.join(".rb/notes/n4.yaml"), "rb: 1\nkind: note\n").unwrap();
        git(&["add", "-N", ".rb/notes/n4.yaml"]);
        let c = Checkout::of(&d);
        assert!(c.not_committed_names.iter().any(|n| n.path == ".rb/notes/n4.yaml" && n.change == "new"), "{:?}", c.not_committed_names);
        let (n, m) = nm();
        assert!(n <= m);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// Three names, then "and N more"; a record below the repository's top names its files from its own folder.
    #[test]
    fn the_names_are_three_then_and_more() {
        let mut c = Checkout { root: PathBuf::from("/home/ana/my wt"), commit: Some("9fa4f89a1b2c3d4e5f60718293a4b5c6d7e8f901".into()), ..Checkout::bare(State::NotCommitted, "my wt".into()) };
        c.record_files_not_committed = 14;
        c.not_committed_names = (0..10).map(|i| Named { path: format!(".rb/notes/n{i}.yaml"), change: if i == 1 { "unmerged" } else { "new" }, from: None }).collect();
        let l = c.names_lines();
        assert_eq!(l[0].text, "Not committed: .rb/notes/n0.yaml (new), .rb/notes/n1.yaml (in a merge conflict), .rb/notes/n2.yaml (new) and 11 more");
        assert_eq!(l[0].code.len(), 3);
        assert_eq!(c.check().as_deref(), Some("git -C '/home/ana/my wt' status --short --untracked-files=all -- .rb"));
        let (d, git) = repo("sub");
        std::fs::create_dir_all(d.join("sub")).unwrap();
        std::fs::rename(d.join(".rb"), d.join("sub/.rb")).unwrap();
        git(&["add", "-A"]);
        git(&["commit", "-q", "-m", "x"]);
        std::fs::write(d.join("sub/.rb/notes/n9.yaml"), "rb: 1\n").unwrap();
        let c = Checkout::of(&d.join("sub"));
        assert_eq!(c.rb_dir, "sub/.rb");
        assert_eq!(c.not_committed_names[0].path, ".rb/notes/n9.yaml");
        assert_eq!(c.record_files(), Some(5));
        assert!(c.check().unwrap().starts_with(&format!("git -C {}/sub status", crate::sys::slashed(&d))));
        let _ = std::fs::remove_dir_all(&d);
    }

    /// A page never names a rule whose file is outside the repository, on any OS: git names Windows files with a drive
    /// letter or as a UNC path, whose first ':' is not the line-number field (S6d).
    #[test]
    fn a_page_names_no_rule_outside_the_repository_on_any_os() {
        let c = Checkout { state: State::Ignored, record_files_ignored: 5, ..Checkout::bare(State::Ignored, "int8".into()) };
        let with = |rule: &str| Checkout { ignored_by: Some(rule.into()), ..c.clone() };
        for rule in [
            r"C:\Users\x\.gitignore_global:1:.rb/",
            "C:/Users/x/.gitignore:1:.rb/",
            r"\\server\share\ignore:1:.rb/",
            "/home/ana/.config/git/ignore:2:.rb/",
            "../outside/.gitignore:3:.rb/",
            r"sub\..\..\.gitignore:3:.rb/",
        ] {
            assert_eq!(with(rule).rule_said(true), None, "{rule}");
            assert_eq!(with(rule).rule_said(false).as_deref(), Some(rule), "your own screens quote it: {rule}");
        }
        for rule in [".gitignore:4:.rb/", "sub/.gitignore:1:.rb/", r"sub\.gitignore:1:.rb/", ".git/info/exclude:7:.rb/", ".gitignore:2:a:b"] {
            assert_eq!(with(rule).rule_said(true).as_deref(), Some(rule), "{rule}");
        }
        assert_eq!(rule_file(r"C:\Users\x\.gitignore_global:1:.rb/"), r"C:\Users\x\.gitignore_global");
        assert_eq!(rule_file(".gitignore:12:x:1:y"), ".gitignore");
    }
}
