//! The places a researcher's own screens can show (rb open, rb ui): each worktree git lists for the clone, with what
//! its record is to the record the screen started on, and the versions of a record (the commits on its branch that
//! changed `.rb/`). Computed from git and files on every ask, never stored. Another place is only read; a past
//! version is read from a checkout of its commit that rb keeps in its own folder of the clone
//! (`<git common dir>/rb/views/`) only while a screen that holds it is open.

use crate::elsewhere::{self, Worktree};
use crate::ledger::{DIR, ROOT_FILE};
use crate::pyjson;
use crate::sources::{self, git, git_top, rel_to, resolve_path};
use crate::value::Value;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// How many versions of a record are listed, newest first.
pub const MAX_VERSIONS: usize = 20;
/// How many past versions one screen keeps checked out at a time.
pub const MAX_VIEWS: usize = 3;
/// A past version with more tracked bytes than this is listed, never checked out.
pub const MAX_VIEW_BYTES: u64 = 500 * 1000 * 1000;

/// A place: a worktree as git lists it, and what its record is to the record shown first.
#[derive(Debug, Clone)]
pub struct Place {
    pub id: String,
    /// its path as git lists it
    pub listed: String,
    /// its path resolved
    pub path: PathBuf,
    pub branch: Option<String>,
    pub head: Option<String>,
    /// uncommitted changes outside the record
    pub dirty: bool,
    /// this (the record shown first), own (a record of its own, of the same project), shared (no record of its own:
    /// it writes to this record), separate (a separate project's record) or none
    pub record: &'static str,
    /// the record's root there, for this, own and separate
    pub root: Option<PathBuf>,
    /// the record's directory there as git lists the place (its path, then the record's place below it)
    pub record_dir: String,
    /// record files there not committed
    pub uncommitted: usize,
    /// when the last write there was made (the log's `at`), or, for shared, the last write from it to this record
    pub last_write: Option<String>,
    /// runs recorded from it in this record (shared)
    pub runs: usize,
    pub gone: bool,
    /// its path as the user's screens name it (short_path), told apart from every other place's
    pub short: String,
    /// whether the clone lists other places beside it: a single checkout says its counts as it always has
    pub many: bool,
}

/// A version of a record: a commit that changed `.rb/`, with the time it was committed. Its subject and author are
/// never read: either can name a writer.
#[derive(Debug, Clone, PartialEq)]
pub struct Version {
    pub sha: String,
    pub committed: String,
}

/// A place's id: p_ and the first 10 hex of the sha256 of its resolved path.
pub fn id_of(path: &Path) -> String {
    format!("p_{}", &sources::sha256_bytes(crate::sys::slashed(path).as_bytes())[..10])
}

/// "2026-10-12 12:20 UTC" of "2026-10-12T14:20:33+02:00": the time to the minute in UTC, as rb's log keeps its
/// times, said so, since a screen beside it may say local time.
fn minute(at: &str) -> String {
    match crate::pytime::parse_iso(at) {
        Some(t) => t.with_timezone(&chrono::Utc).format("%Y-%m-%d %H:%M UTC").to_string(),
        None => at.chars().take(16).collect::<String>().replace('T', " "),
    }
}

fn plural(n: usize, one: &str, many: &str) -> String {
    format!("{n} {}", if n == 1 { one } else { many })
}

impl Place {
    /// The place as rb names it: its branch and its path, as git gives them (a detached one by its commit).
    pub fn label(&self) -> String {
        elsewhere::label(self.branch.as_deref(), &self.listed, self.head.as_deref())
    }

    /// The place as the user's screens name it: its branch, then its path in short (short_path).
    pub fn label_short(&self) -> String {
        elsewhere::label(self.branch.as_deref(), &self.short, self.head.as_deref())
    }

    /// Whether a screen can show it: it holds a record (this, its own or a separate project's) and is still there.
    pub fn readable(&self) -> bool {
        self.root.is_some() && !self.gone
    }

    /// What the place is, in rb's words, with its counts: "its own record · 2 record files not committed · last write
    /// ..." ("2 not committed" in a single checkout, as it always was). A place
    /// removed since says only that, and what this record keeps of it: what it held is not there to read.
    pub fn said(&self) -> String {
        let mut parts: Vec<String> = vec![match (self.gone, self.record) {
            (true, _) => "removed since",
            (_, "this") => "this record",
            (_, "own") => "its own record",
            (_, "shared") => "no record of its own: it writes to this record",
            (_, "separate") => "a separate project, with its own record",
            _ => "no record",
        }
        .to_string()];
        if self.uncommitted > 0 {
            parts.push(if self.many { format!("{} not committed", plural(self.uncommitted, "record file", "record files")) } else { format!("{} not committed", self.uncommitted) });
        }
        if self.runs > 0 {
            parts.push(format!("{} recorded from it", plural(self.runs, "run", "runs")));
        }
        if let Some(at) = &self.last_write {
            parts.push(format!("last write {}", minute(at)));
        }
        parts.join(" · ")
    }

    /// Why a screen cannot show it, the place named in short as the screen names it.
    pub fn not_shown(&self) -> String {
        Self::not_shown_as(self.gone, self.record, &self.label_short())
    }

    fn not_shown_as(gone: bool, record: &str, label: &str) -> String {
        match (gone, record) {
            (true, _) => format!("{label} is removed since: there is nothing there to show."),
            (false, "shared") => format!("{label} has no record of its own to show: it writes to this record."),
            _ => format!("{label} has no record to show."),
        }
    }

    pub fn to_value(&self) -> Value {
        let opt = |s: &Option<String>| s.clone().map(Value::Str).unwrap_or(Value::Null);
        crate::obj! {
            "id" => self.id.clone(), "label" => self.label(), "said" => self.said(), "path" => self.listed.clone(), "branch" => opt(&self.branch),
            "head" => opt(&self.head), "dirty" => self.dirty, "record" => self.record, "root" => self.root.as_ref().map(crate::sys::slashed).map(Value::Str).unwrap_or(Value::Null),
            "record_dir" => self.record_dir.clone(), "uncommitted" => self.uncommitted as i64, "last_write" => opt(&self.last_write),
            "runs" => self.runs as i64, "gone" => self.gone, "readable" => self.readable(), "label_short" => self.label_short(),
            "why_not" => if self.readable() { String::new() } else { Self::not_shown_as(self.gone, self.record, &self.label_short()) }
        }
    }
}

/// A place's path as the user's screens name it: from the project's main checkout (`main`) when it is inside it (from
/// ./ where it starts with ~, which would read as a home folder), from ~ when it is under the home folder, else as git
/// lists it.
pub fn short_path(listed: &str, path: &Path, main: &Path) -> String {
    short_from(listed, path, main, &resolve_path(&crate::sys::home()))
}

fn short_from(listed: &str, path: &Path, main: &Path, home: &Path) -> String {
    if path != main {
        if let Some(rel) = rel_to(path, main).filter(|r| !r.is_empty()) {
            return if rel.starts_with('~') { format!("./{rel}") } else { rel };
        }
    }
    // a home folder at the top of the file system names nothing apart
    if home.components().count() > 1 {
        if let Some(rel) = rel_to(path, home) {
            return if rel.is_empty() { "~".to_string() } else { format!("~/{rel}") };
        }
    }
    listed.to_string()
}

/// Each place's short path, told apart: two places whose labels would read the same in short are named by their paths
/// as git lists them.
fn tell_apart(places: &mut [Place]) {
    let labels: Vec<String> = places.iter().map(Place::label_short).collect();
    for (i, p) in places.iter_mut().enumerate() {
        if labels.iter().enumerate().any(|(j, l)| j != i && *l == labels[i]) {
            p.short = p.listed.clone();
        }
    }
}

impl Version {
    /// As a list of versions names it: "f44a1430abcd · 2026-10-12 14:20 UTC".
    pub fn label(&self) -> String {
        format!("{} · {}", self.sha.chars().take(12).collect::<String>(), minute(&self.committed))
    }

    /// As a screen that shows it says it: "main · /p at f44a1430abcd, 2026-10-12 14:20 UTC: a past version, read only".
    pub fn shown(&self, place: &Place) -> String {
        format!("{} at {}, {}: a past version, read only", place.label(), self.sha.chars().take(12).collect::<String>(), minute(&self.committed))
    }

    /// As the user's screens say it while it is shown: "main · ~/p as of f44a1430abcd · 2026-10-12 14:20 UTC · read only".
    pub fn shown_short(&self, place: &Place) -> String {
        format!("{} as of {} · {} · read only", place.label_short(), self.sha.chars().take(12).collect::<String>(), minute(&self.committed))
    }

    /// As a screen's data carries a version of `place`'s record: `label` in a list, `shown` while it is shown (and
    /// `shown_short`, as the user's screens say it), and `note`, why it is listed and not read, once rb has found it
    /// tracks more than it checks out.
    pub fn to_value(&self, place: &Place) -> Value {
        crate::obj! {
            "sha" => self.sha.clone(), "committed" => self.committed.clone(), "label" => self.label(), "shown" => self.shown(place), "note" => note_of(&self.sha),
            "shown_short" => self.shown_short(place)
        }
    }
}

/// What this process found of each version it would not check out: by commit.
static NOTES: std::sync::Mutex<Option<std::collections::HashMap<String, String>>> = std::sync::Mutex::new(None);

fn note_of(sha: &str) -> String {
    NOTES.lock().unwrap_or_else(|e| e.into_inner()).as_ref().and_then(|m| m.get(sha).cloned()).unwrap_or_default()
}

/// The uid of the project whose record is at `root`, read from its project.yaml.
fn uid_of(root: &Path) -> Option<String> {
    let text = std::fs::read_to_string(root.join(DIR).join(ROOT_FILE)).ok()?;
    match crate::rbyaml::loads(&text, ROOT_FILE).ok()? {
        Value::Dict(d) => d.get("uid").and_then(|v| v.as_str()).map(|u| u.to_string()).filter(|u| !u.is_empty()),
        _ => None,
    }
}

/// The `at` of the last line of a log, read from its end.
fn last_at(log: &Path) -> Option<String> {
    use std::io::{Read, Seek, SeekFrom};
    let mut f = std::fs::File::open(log).ok()?;
    let len = f.metadata().ok()?.len();
    let from = len.saturating_sub(64 * 1024);
    f.seek(SeekFrom::Start(from)).ok()?;
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf);
    text.lines().rev().filter(|l| !l.trim().is_empty()).find_map(|l| match pyjson::loads(l.trim()) {
        Ok(Value::Dict(d)) => d.get("at").and_then(|a| a.as_str()).map(|a| a.to_string()),
        _ => None,
    })
}

/// What this record's log says of each linked worktree it was written from: (its resolved path, runs recorded from
/// it, the last write from it).
fn written_from(root: &Path) -> Vec<(PathBuf, usize, Option<String>)> {
    let Ok(text) = std::fs::read_to_string(root.join(DIR).join("log.jsonl")) else { return Vec::new() };
    let mut out: Vec<(PathBuf, usize, Option<String>)> = Vec::new();
    for line in text.lines().filter(|l| l.contains("\"worktree\"")) {
        let Ok(Value::Dict(d)) = pyjson::loads(line.trim()) else { continue };
        let Some(path) = d.get("worktree").and_then(|w| w.get("path")).and_then(|p| p.as_str()) else { continue };
        let path = resolve_path(Path::new(path));
        let run = d.get("op").and_then(|v| v.as_str()) == Some("record") && d.get("kind").and_then(|v| v.as_str()) == Some("run");
        let at = d.get("at").and_then(|v| v.as_str()).map(|a| a.to_string());
        match out.iter_mut().find(|(p, _, _)| *p == path) {
            Some(entry) => {
                entry.1 += run as usize;
                if at > entry.2 {
                    entry.2 = at;
                }
            }
            None => out.push((path, run as usize, at)),
        }
    }
    out
}

/// The pathspec of a worktree's code: all of it but the record's directory and the worktrees nested in it.
fn code_spec(tree: &Path, rb_dir: &str, trees: &[Worktree]) -> Vec<String> {
    let mut spec = vec!["--".to_string(), ":(top)".to_string(), format!(":(top,exclude){rb_dir}")];
    for t in trees.iter().filter(|t| t.path != tree) {
        if let Some(rel) = rel_to(&t.path, tree).filter(|r| !r.is_empty()) {
            spec.push(format!(":(top,exclude,literal){rel}"));
        }
    }
    spec
}

/// How many files git lists as changed or new under a worktree's record directory.
fn uncommitted_in(tree: &Path, rb_dir: &str) -> usize {
    let spec = format!(":(top){rb_dir}");
    git(tree, &["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", &spec]).map_or(0, |s| s.split('\0').filter(|e| e.len() > 3).count())
}

/// The record's directory in a place, as git lists the place.
fn record_dir_of(listed: &str, rel: &str) -> String {
    if rel.is_empty() {
        listed.to_string()
    } else {
        format!("{}/{rel}", listed.trim_end_matches('/'))
    }
}

/// Every place of the clone the record at `root` is in, as git lists its worktrees (the main one first), each with
/// what its record is to this one. A record outside git is one place, this one.
pub fn list(root: &Path) -> Vec<Place> {
    let root = resolve_path(root);
    let lone = || {
        let listed = crate::sys::slashed(&root);
        vec![Place {
            id: id_of(&root), record_dir: listed.clone(), short: short_path(&listed, &root, &root), listed, path: root.clone(), branch: None, head: None,
            dirty: false, record: "this", root: Some(root.clone()), uncommitted: 0, last_write: last_at(&root.join(DIR).join("log.jsonl")), runs: 0,
            gone: false, many: false,
        }]
    };
    let Some(top) = git_top(&root) else { return lone() };
    let rel = rel_to(&root, &top).unwrap_or_default();
    let rb_dir = if rel.is_empty() { DIR.to_string() } else { format!("{rel}/{DIR}") };
    let trees = elsewhere::worktrees(&top);
    if !trees.iter().any(|t| t.path == top) {
        return lone();
    }
    let mine = uid_of(&root);
    let from = written_from(&root);
    let main = trees.first().map(|t| t.path.clone()).unwrap_or_else(|| top.clone());
    let mut out = Vec::new();
    for w in trees.iter() {
        let (count, last) = from.iter().find(|(p, _, _)| *p == w.path).map(|(_, n, at)| (*n, at.clone())).unwrap_or((0, None));
        let there = w.path.join(&rel);
        let (record, record_root) = if w.path == top {
            ("this", Some(root.clone()))
        } else if !w.gone && there.join(DIR).join(ROOT_FILE).is_file() {
            let same = match (&mine, uid_of(&there)) {
                (Some(m), Some(u)) => *m == u,
                _ => true,
            };
            (if same { "own" } else { "separate" }, Some(resolve_path(&there)))
        } else if (!w.gone && crate::ledger::find_root(Some(&there)).as_deref() == Some(root.as_path())) || (w.gone && count + last.iter().count() > 0) {
            ("shared", None)
        } else {
            ("none", None)
        };
        let dirty = !w.gone && {
            let spec = code_spec(&w.path, &rb_dir, &trees);
            let mut args: Vec<&str> = vec!["status", "--porcelain", "--untracked-files=normal"];
            args.extend(spec.iter().map(|s| s.as_str()));
            git(&w.path, &args).map_or(false, |s| !s.trim().is_empty())
        };
        let holds = record_root.is_some() && !w.gone;
        out.push(Place {
            id: id_of(&w.path),
            listed: w.listed.clone(),
            path: w.path.clone(),
            branch: w.branch.clone(),
            head: w.head.clone().filter(|h| !h.chars().all(|c| c == '0')),
            dirty,
            record,
            record_dir: record_dir_of(&w.listed, &rel),
            uncommitted: if holds { uncommitted_in(&w.path, &rb_dir) } else { 0 },
            last_write: match &record_root {
                Some(r) if !w.gone => last_at(&r.join(DIR).join("log.jsonl")),
                _ => last,
            },
            runs: if record == "shared" { count } else { 0 },
            gone: w.gone,
            root: record_root,
            short: short_path(&w.listed, &w.path, &main),
            many: trees.len() > 1,
        });
    }
    // this place first, then the rest as git lists them, at most as many as Elsewhere names
    out.sort_by_key(|p| p.record != "this");
    out.truncate(elsewhere::MAX_PLACES);
    tell_apart(&mut out);
    out
}

/// The versions of the record at `root`: the commits on its checkout's own line that changed its `.rb/` (a merge
/// among them, never a merged branch's own commits, which are no state the record was in here), newest first, each
/// with the time it was committed.
pub fn versions(root: &Path) -> Vec<Version> {
    let root = resolve_path(root);
    let Some(top) = git_top(&root) else { return Vec::new() };
    let rel = rel_to(&root, &top).unwrap_or_default();
    let spec = format!(":(top){}", if rel.is_empty() { DIR.to_string() } else { format!("{rel}/{DIR}") });
    let n = MAX_VERSIONS.to_string();
    let out = git(&top, &["log", "--first-parent", "-n", &n, "--format=%H %cI", "HEAD", "--", &spec]).unwrap_or_default();
    out.lines()
        .filter_map(|l| l.trim().split_once(' '))
        .filter(|(sha, _)| sha.len() >= 40 && sha.chars().all(|c| c.is_ascii_hexdigit()))
        .map(|(sha, at)| Version { sha: sha.to_string(), committed: at.to_string() })
        .collect()
}

// ---------------------------------------------------------------- past versions, checked out while a screen shows them

/// rb's folder for past versions in the clone `root` is in: <git common dir>/rb/views. None outside git.
pub fn views_home(root: &Path) -> Option<PathBuf> {
    let out = git(root, &["rev-parse", "--git-common-dir"])?;
    let common = PathBuf::from(out.trim());
    if out.trim().is_empty() {
        return None;
    }
    let common = if common.is_absolute() { common } else { root.join(common) };
    Some(resolve_path(&common).join("rb").join("views"))
}

/// Whether a screen holds a views folder: its lock is held by a process that is open.
fn held(dir: &Path) -> bool {
    let Ok(f) = std::fs::OpenOptions::new().read(true).write(true).open(dir.join(".lock")) else {
        // a folder whose lock is not made yet is one being made: kept a minute
        return std::fs::metadata(dir).and_then(|m| m.modified()).ok().and_then(|t| t.elapsed().ok()).map_or(true, |age| age < Duration::from_secs(60));
    };
    match f.try_lock() {
        Ok(()) => {
            let _ = f.unlock();
            false
        }
        Err(std::fs::TryLockError::WouldBlock) => true,
        Err(_) => true, // the lock cannot be asked: kept
    }
}

/// Take away every views folder no open screen holds: one a screen left when it was stopped without the chance to.
pub fn sweep(home: &Path) {
    let Ok(rd) = std::fs::read_dir(home) else { return };
    for e in rd.flatten() {
        let p = e.path();
        if p.is_dir() && !held(&p) {
            let _ = std::fs::remove_dir_all(&p);
        }
    }
    let _ = std::fs::remove_dir(home); // gone when nothing is left in it
}

/// A screen's own views folder, held for as long as the screen is open and taken away with what it holds when it ends.
pub struct Owner {
    pub dir: PathBuf,
    lock: Option<std::fs::File>,
}

impl Owner {
    /// Hold a views folder for the clone `root` is in, after taking away those no open screen holds. None outside git.
    pub fn start(root: &Path) -> Option<Owner> {
        let home = views_home(root)?;
        sweep(&home);
        let token = format!("{}-{:08x}", std::process::id(), crate::rng::os_randbits(32));
        let dir = home.join(token);
        let first = home.parent().map_or(false, |rb| !rb.exists());
        std::fs::create_dir_all(&dir).ok()?;
        if first {
            // rb's own folder in the clone begins here: the ledger keeps it, and rb remove takes it away
            crate::owned::record(root, "repo", crate::dict! {"path" => crate::sys::slashed(&home), "how" => "dir"});
        }
        let lock = crate::sys::lock(&dir.join(".lock"));
        if lock.is_none() {
            let _ = std::fs::remove_dir_all(&dir);
            return None;
        }
        Some(Owner { dir, lock })
    }

    /// The folder's name, which a screen's own processes pass on (RB_VIEWS).
    pub fn token(&self) -> String {
        self.dir.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
    }
}

impl Drop for Owner {
    fn drop(&mut self) {
        drop(self.lock.take()); // the lock goes before its folder: a file held open cannot be removed on Windows
        let _ = std::fs::remove_dir_all(&self.dir);
        if let Some(home) = self.dir.parent() {
            let _ = std::fs::remove_dir(home);
        }
    }
}

/// The views folder a screen's own process names (RB_VIEWS) in the clone `root` is in, while that screen holds it.
pub fn named_owner(root: &Path) -> Option<PathBuf> {
    let token = std::env::var("RB_VIEWS").ok().filter(|t| !t.is_empty() && t.len() <= 64 && t.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'))?;
    let dir = views_home(root)?.join(token);
    (dir.is_dir() && held(&dir)).then_some(dir)
}

static MAKING: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// How many bytes the files a commit tracks hold.
fn tracked_bytes(top: &Path, sha: &str) -> Option<u64> {
    let out = git(top, &["ls-tree", "-r", "-l", "--full-tree", sha])?;
    Some(out.lines().filter_map(|l| l.split('\t').next()?.split_whitespace().nth(3)?.parse::<u64>().ok()).sum())
}

/// The most tracked bytes a past version is checked out with: MAX_VIEW_BYTES, or RB_TEST_VIEW_BYTES where a test sets it.
fn max_view_bytes() -> u64 {
    std::env::var("RB_TEST_VIEW_BYTES").ok().and_then(|v| v.parse().ok()).unwrap_or(MAX_VIEW_BYTES)
}

/// Why a version is not checked out: it tracks more than rb reads for a past version.
pub fn too_large(top: &Path, sha: &str) -> Option<String> {
    let (bytes, most) = (tracked_bytes(top, sha)?, max_view_bytes());
    let mb = |b: u64| if b >= 1_000_000 { format!("{} MB", b / 1_000_000) } else { format!("{b} bytes") };
    (bytes > most).then(|| {
        format!("{} tracks {} of files, more than rb checks out to read a past version ({}).", sha.chars().take(12).collect::<String>(), mb(bytes), mb(most))
    })
}

/// The record at version `sha` of the place whose worktree is `top`, as a checkout of that commit in `owner` (a
/// screen's views folder), made once and kept while the screen holds at most MAX_VIEWS of them: its root (the
/// record's place below the checkout's top, `rel`). Err says why it cannot be read.
pub fn view_root(owner: &Path, top: &Path, sha: &str, rel: &str) -> Result<PathBuf, String> {
    let _making = MAKING.lock().unwrap_or_else(|e| e.into_inner());
    let name: String = sha.chars().take(12).collect();
    let dir = owner.join(&name);
    let at = |d: &Path| if rel.is_empty() { d.to_path_buf() } else { d.join(rel) };
    if !dir.is_dir() {
        if let Some(why) = too_large(top, sha) {
            NOTES.lock().unwrap_or_else(|e| e.into_inner()).get_or_insert_with(Default::default).insert(sha.to_string(), why.clone());
            return Err(why);
        }
        let tmp = owner.join(format!(".{name}-{:08x}", crate::rng::os_randbits(32)));
        let made = (|| -> Option<()> {
            let (src, dst) = (top.to_string_lossy().into_owned(), tmp.to_string_lossy().into_owned());
            let env: &[(&str, &str)] = &[("GIT_LFS_SKIP_SMUDGE", "1"), ("GIT_TERMINAL_PROMPT", "0")];
            // no hook or monitor of the user's runs while rb checks a version out, and git works in rb's own folder
            let none = owner.join(".no-hooks");
            std::fs::create_dir_all(&none).ok()?;
            let hooks = format!("core.hooksPath={}", none.to_string_lossy());
            let quiet = |args: &[&str]| {
                let all: Vec<&str> = ["-c", hooks.as_str(), "-c", "core.fsmonitor=false"].into_iter().chain(args.iter().copied()).collect();
                sources::run("git", &all, Some(owner), env, None, Duration::from_secs(600)).filter(|r| r.code == 0).map(|_| ())
            };
            quiet(&["clone", "--shared", "--no-checkout", "--quiet", "--", &src, &dst])?;
            quiet(&["-C", &dst, "checkout", "--quiet", "--detach", sha])
        })();
        if made.is_none() {
            let _ = std::fs::remove_dir_all(&tmp);
            return Err(format!("git could not check out {name} to read it."));
        }
        if std::fs::rename(&tmp, &dir).is_err() {
            let _ = std::fs::remove_dir_all(&tmp);
        }
    }
    // the order they were last used in, the newest last: the oldest beyond MAX_VIEWS is taken away
    let order = owner.join(".order");
    let mut used: Vec<String> = std::fs::read_to_string(&order).unwrap_or_default().lines().map(|l| l.trim().to_string()).filter(|l| !l.is_empty() && *l != name).collect();
    used.push(name.clone());
    for d in std::fs::read_dir(owner).map(|rd| rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect::<Vec<_>>()).unwrap_or_default() {
        if !d.starts_with('.') && !used.contains(&d) {
            used.insert(0, d); // one this order never named: the oldest
        }
    }
    while used.len() > MAX_VIEWS {
        let _ = std::fs::remove_dir_all(owner.join(used.remove(0)));
    }
    let _ = std::fs::write(&order, used.join("\n") + "\n");
    let root = at(&dir);
    if !root.join(DIR).join(ROOT_FILE).is_file() {
        return Err(format!("{name} holds no record at {}.", if rel.is_empty() { "the top of the repository" } else { rel }));
    }
    Ok(root)
}

// ---------------------------------------------------------------- what a screen chooses

/// What a screen shows when a place or a version is chosen: the record's root to read, the place, and the version.
pub struct Chosen {
    pub root: PathBuf,
    pub place: Place,
    pub version: Option<Version>,
    /// the place the screen started on, shown live
    pub start: bool,
}

/// The root of the record the screen started on, as the last choice of a place was made against it (a screen serves
/// one record, so its choices name one): the places are listed from it, whatever place is shown.
static STARTED: std::sync::Mutex<Option<PathBuf>> = std::sync::Mutex::new(None);

impl Chosen {
    /// The root of the record the screen started on: this one's at the start place, live; else the one the screen's
    /// choices were made against.
    pub fn start_root(&self) -> PathBuf {
        if self.start {
            return self.root.clone();
        }
        STARTED.lock().unwrap_or_else(|e| e.into_inner()).clone().unwrap_or_else(|| self.root.clone())
    }

    /// The place shown, as a screen's data carries it: the place, the version shown (`version`), and the versions of
    /// its record (`versions`).
    pub fn value(&self, versions: &[Version]) -> Value {
        let mut v = self.place.to_value();
        if let Value::Dict(d) = &mut v {
            d.insert("version".into(), self.version.as_ref().map_or(Value::Null, |at| at.to_value(&self.place)));
            d.insert("versions".into(), Value::List(versions.iter().map(|v| v.to_value(&self.place)).collect()));
        }
        v
    }

    /// The command that confirms a call this place's record holds, from the terminal the screen started in: rb confirm
    /// <id> where it started, else with the cd to the place's record first (a past version's call is confirmed where it
    /// lives now).
    pub fn confirm(&self, command: &str) -> String {
        let cd = self.cd();
        if cd.is_empty() || command.is_empty() || command.starts_with("cd ") { command.to_string() } else { format!("{cd}{command}") }
    }

    /// What a command to this record starts with from the terminal the screen started in: `cd <path> && `, or nothing.
    pub fn cd(&self) -> String {
        if self.start || self.place.record == "this" { String::new() } else { format!("cd {} && ", shell_word(&self.place.record_dir)) }
    }
}

/// The place a chosen record is shown as, against which each run says where it was recorded: its place's, a past
/// version's included.
pub fn shown(chosen: &Chosen, places: &[Place]) -> crate::view::PlaceShown {
    // the main checkout: the one place that is no linked worktree (read from .git files, no git call)
    let main = places.iter().find(|p| !p.gone && crate::ledger::linked_top(&p.path).is_none()).map(|p| p.path.clone()).unwrap_or_else(|| chosen.place.path.clone());
    crate::view::PlaceShown {
        top: chosen.place.path.clone(),
        branch: chosen.place.branch.clone(),
        linked: crate::ledger::linked_top(&chosen.place.path).is_some(),
        trees: places.iter().map(|p| (p.path.clone(), p.listed.clone(), p.gone)).collect(),
        main,
        short: places.iter().map(|p| (p.path.clone(), p.short.clone())).collect(),
        labels: places.iter().map(|p| (p.path.clone(), p.label_short())).collect(),
        shows: places.iter().filter(|p| p.readable()).map(|p| (p.path.clone(), p.id.clone(), p.branch.clone())).collect(),
        root: chosen.place.root.clone().unwrap_or_else(|| chosen.place.path.clone()),
        cd: chosen.cd(),
    }
}

/// Each call in a home view that waits for a confirmation, with the command that confirms it from the terminal the
/// screen started in (Chosen::confirm), and so the line that says where the calls that wait on the user are listed.
pub fn confirm_from_here(home: &mut Value, chosen: &Chosen) {
    if let Value::Dict(h) = home {
        if let Some(Value::List(rows)) = h.get_mut("pending") {
            for row in rows.iter_mut() {
                if let Value::Dict(r) = row {
                    let command = r.get("confirm").and_then(|c| c.as_str()).unwrap_or("").to_string();
                    r.insert("confirm".into(), Value::Str(chosen.confirm(&command)));
                }
            }
        }
        // the calls that wait on the user there are listed by rb status there
        if let Some(Value::Str(quiet)) = h.get_mut("quiet") {
            *quiet = quiet.replacen("rb status lists", &format!("{} lists", chosen.confirm("rb status")), 1);
        }
    }
}

/// A path as a shell takes it: as it is when it holds nothing a shell reads, else in single quotes.
pub fn shell_word(s: &str) -> String {
    if !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || "/._-+,:@%~=".contains(c)) {
        s.to_string()
    } else {
        format!("'{}'", s.replace('\'', "'\\''"))
    }
}

/// The place and version a screen asks for (ids and shas rb listed, nothing else), for the record at `start_root`
/// the screen started on: `places` as listed now, and the versions of a place's record from `versions_of`. Err says
/// why nothing is shown for them.
pub fn choose(
    start_root: &Path, places: &[Place], place: Option<&str>, at: Option<&str>, versions_of: &dyn Fn(&Place) -> Vec<Version>,
    owner: &dyn Fn() -> Option<PathBuf>,
) -> Result<Chosen, String> {
    *STARTED.lock().unwrap_or_else(|e| e.into_inner()) = Some(start_root.to_path_buf());
    let this = places.iter().find(|p| p.record == "this").cloned().ok_or_else(|| "rb lists no place for this record.".to_string())?;
    let p = match place.filter(|p| !p.is_empty()) {
        None => this,
        Some(id) => places.iter().find(|p| p.id == id).cloned().ok_or_else(|| format!("{id} is not a place rb listed here."))?,
    };
    if !p.readable() {
        return Err(p.not_shown());
    }
    let root = p.root.clone().unwrap_or_else(|| start_root.to_path_buf());
    let start = p.record == "this";
    let at = at.filter(|s| !s.is_empty());
    // another place as it is now, not as a listing of a few seconds ago had it
    if let Some(why) = gone_now(&p, &root, start, at.is_some()) {
        return Err(why);
    }
    let Some(sha) = at else {
        return Ok(Chosen { root, place: p, version: None, start });
    };
    let version = versions_of(&p).into_iter().find(|v| v.sha == sha).ok_or_else(|| format!("{sha} is not a version rb listed for {}.", p.label_short()))?;
    let owner = owner().ok_or_else(|| "A past version shows only on rb open and rb ui, which keep a checkout of it while they are open.".to_string())?;
    let rel = rel_to(&root, &p.path).unwrap_or_default();
    let view = view_root(&owner, &p.path, &version.sha, &rel)?;
    Ok(Chosen { root: view, place: p, version: Some(version), start: false })
}

/// Why another place (`p`, its record at `root`) cannot be shown now, as a screen says it: removed since it was listed,
/// or its record gone (a past version is read from rb's own checkout of it). None while it is there to read.
fn gone_now(p: &Place, root: &Path, start: bool, version: bool) -> Option<String> {
    if start {
        None
    } else if !p.path.join(".git").exists() {
        Some(format!("{} is removed since: there is nothing there to show.", p.label_short()))
    } else if !version && !root.join(DIR).join(ROOT_FILE).is_file() {
        Some(format!("{} has no record of its own to show.", p.label_short()))
    } else {
        None
    }
}

impl Chosen {
    /// Why what was chosen cannot be shown now, where reading it failed because it was taken away meanwhile (a
    /// worktree removed while a screen read it): rb's words for that, never the error the read gave.
    pub fn gone_now(&self) -> Option<String> {
        gone_now(&self.place, &self.root, self.start, self.version.is_some())
    }
}

// ---------------------------------------------------------------- on the shell (BP 4b)

/// rb open's places on its shell's document (`doc`) of `led` at `chosen` (on_the_shell), Elsewhere read from each
/// other place's own record as rb open's home reads it, at the live start place only.
/// The places are listed from the record the screen started on (`_listed`, as a caller lists them from the record
/// shown, names another place this record away from it).
pub fn on_the_shell_of(led: &crate::ledger::Ledger, doc: &mut Value, chosen: &Chosen, _listed: &[Place]) {
    let listed = list(&chosen.start_root());
    let rows = if chosen.start && chosen.version.is_none() && listed.len() > 1 {
        let here = crate::webui::held_here(led);
        elsewhere_on_the_shell(&listed, &crate::webui::elsewhere_rows(led, &listed), &|p| pending_there(p, &here))
    } else {
        Vec::new()
    };
    on_the_shell(doc, chosen, &listed, rows);
}

/// What a past version withholds, in rb's words.
pub const WITHHELD: &str = "A past version offers no command: a call copied here would be recorded on today's readings.";

/// What rb open's shell adds to its document (`doc`, the Live tier's) at `chosen`, of the clone's places `listed`:
/// the scope bar away from the live start place, Elsewhere at it (`rows`, each other place as Elsewhere says it), each
/// run's `recorded_in` where it was recorded in a place the screen can show, and, on a past version, no command to
/// copy: no call's lines and no confirm, since a call copied there would be recorded on today's readings.
pub fn on_the_shell(doc: &mut Value, chosen: &Chosen, listed: &[Place], rows: Vec<Value>) {
    let start = listed.iter().find(|p| p.record == "this");
    let live_start = chosen.start && chosen.version.is_none();
    let to = |id: &str| if start.map_or(false, |s| s.id == id) { String::new() } else { format!("&place={}", url_part(id)) };
    let bar = match &chosen.version {
        Some(v) => crate::obj! {
            "line" => v.shown_short(&chosen.place), "back" => "Back to now", "href" => format!("#overview{}", to(&chosen.place.id)), "withheld" => WITHHELD
        },
        None if !live_start => crate::obj! {
            "line" => format!("Viewing {} \u{b7} read only", chosen.place.label_short()),
            "back" => format!("Back to {}", start.map(|s| s.branch.clone().unwrap_or_else(|| s.label_short())).unwrap_or_else(|| "the place rb open started on".into())),
            "href" => "#overview", "withheld" => ""
        },
        None => Value::Null,
    };
    let Value::Dict(d) = doc else { return };
    if let Some(Value::Dict(places)) = d.get_mut("places") {
        places.insert("scope_bar".into(), bar);
        places.insert("elsewhere".into(), Value::List(if live_start { rows } else { Vec::new() }));
    }
    if let Some(Value::Dict(runs)) = d.get_mut("runs") {
        for (id, run) in runs.iter_mut() {
            let Value::Dict(r) = run else { continue };
            let o = r.get("observed_in").cloned().unwrap_or(Value::Null);
            let pid = o.get("place").and_then(Value::as_str).unwrap_or("");
            let shows = !pid.is_empty() && pid != chosen.place.id && listed.iter().any(|p| p.id == pid && p.readable());
            let link = if shows {
                crate::obj! {
                    "place" => pid, "label" => o.get("label_short").and_then(Value::as_str).unwrap_or(""), "href" => format!("#run-{id}{}", to(pid))
                }
            } else {
                Value::Null
            };
            r.insert("recorded_in".into(), link);
        }
    }
    if chosen.version.is_some() {
        withhold_commands(d);
    }
}

/// A past version's document with no command to copy: no call's lines, no confirm of what waits, no confirm-all.
fn withhold_commands(d: &mut crate::value::Dict) {
    if let Some(Value::List(calls)) = d.get_mut("calls") {
        for c in calls.iter_mut() {
            if let Value::Dict(c) = c {
                c.insert("actions".into(), Value::List(Vec::new()));
            }
        }
    }
    if let Some(Value::Dict(p)) = d.get_mut("pending") {
        if let Some(Value::List(rows)) = p.get_mut("rows") {
            for r in rows.iter_mut() {
                if let Value::Dict(r) = r {
                    r.insert("confirm".into(), Value::Str(String::new()));
                }
            }
        }
        p.insert("confirm_all".into(), Value::Null);
    }
    // nor the command that confirms an experiment's design
    if let Some(exps) = d.get_mut("experiments") {
        let all: Vec<&mut Value> = match exps {
            Value::List(l) => l.iter_mut().collect(),
            Value::Dict(m) => m.values_mut().collect(),
            _ => Vec::new(),
        };
        for e in all {
            if let Some(Value::Dict(design)) = e.as_dict_mut().and_then(|e| e.get_mut("design")) {
                if design.contains_key("confirm") {
                    design.insert("confirm".into(), Value::Str(String::new()));
                }
            }
        }
    }
}

/// A value as it stands in an address: what is not a letter, a digit or one of `-._~` as %XX of its bytes.
fn url_part(s: &str) -> String {
    s.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-._~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect()
}

/// What waits for a confirmation in another place's own record (`p`), read there and never written, as the shell's
/// Elsewhere lists it: each by its id, kind, subject, rb's words and the one command there that confirms it (a design,
/// the one there that lists it to confirm, which ends with the command that confirms it as listed); one this record
/// holds too (`here`) waits, or was confirmed, here.
pub fn pending_there(p: &Place, here: &std::collections::HashSet<String>) -> Vec<Value> {
    let Some(root) = p.root.clone() else { return Vec::new() };
    let read = crate::dry::reading(|| -> crate::errors::Res<Vec<Value>> {
        let led = crate::ledger::Ledger::open(Some(&root))?;
        let st = Value::Dict(led.status(None)?);
        let rows: Vec<crate::view::PendingRow> = crate::view::for_a_screen(|| crate::view::pending_rows(&st)).into_iter().map(crate::view::PendingRow::drawn).collect();
        let cd = shell_word(&p.record_dir);
        Ok(rows
            .into_iter()
            .filter(|r| !here.contains(&r.id))
            .map(|r| {
                let confirm = if r.kind == "design" { format!("cd {cd} && rb confirm {}", r.id.split('@').next().unwrap_or("")) } else { format!("cd {cd} && {}", r.confirm) };
                crate::obj! {"id" => r.id, "kind" => r.kind, "subject" => r.subject, "text" => r.text, "confirm" => confirm, "lines" => Value::list_of_str(r.lines)}
            })
            .collect())
    });
    read.unwrap_or_default()
}

/// Elsewhere, as the shell's document carries it: each other place of `listed` (`elsewhere`, rb open's own rows) by
/// its id, its label in short and its whole path, what rb says of it, what it holds not merged here, whether it can be
/// shown and why not, how many calls wait on the user there with the command there that lists them, and what waits
/// there for a confirmation, each with the one command there that confirms it (a design, the one that lists it to
/// confirm, from `pending_of`). Nothing is added up across places.
pub fn elsewhere_on_the_shell(listed: &[Place], elsewhere: &Value, pending_of: &dyn Fn(&Place) -> Vec<Value>) -> Vec<Value> {
    let rows = elsewhere.as_list().map(Vec::as_slice).unwrap_or(&[]);
    rows.iter()
        .filter_map(|r| {
            let id = r.get("id").and_then(Value::as_str)?;
            let p = listed.iter().find(|p| p.id == id)?;
            let s = |k: &str| r.get(k).and_then(Value::as_str).unwrap_or("").to_string();
            let pending = if p.readable() { pending_of(p) } else { Vec::new() };
            let n = r.get("calls").and_then(Value::as_i64).unwrap_or(0);
            Some(crate::obj! {
                "place" => id, "label" => p.label_short(), "path" => p.listed.clone(), "said" => p.said(), "adds" => s("adds"),
                "can_show" => p.readable(), "why_not" => if p.readable() { String::new() } else { p.not_shown() },
                "waiting" => crate::obj! {"n" => n, "list" => s("calls_command")}, "pending" => Value::List(pending)
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A place's id is its resolved path's, the same every time and apart from another's.
    #[test]
    fn a_place_s_id_is_its_path_s() {
        let a = id_of(Path::new("/srv/bei"));
        assert!(a.starts_with("p_") && a.len() == 12 && a[2..].chars().all(|c| c.is_ascii_hexdigit()));
        assert_eq!(a, id_of(Path::new("/srv/bei")));
        assert_ne!(a, id_of(Path::new("/srv/wt/a")));
    }

    /// A path is said to a shell as it is when it can be, else in single quotes, a quote in it closed and reopened.
    #[test]
    fn a_path_is_said_as_a_shell_takes_it() {
        assert_eq!(shell_word("/srv/wt/a"), "/srv/wt/a");
        assert_eq!(shell_word("/srv/my work"), "'/srv/my work'");
        assert_eq!(shell_word("/srv/ana's"), "'/srv/ana'\\''s'");
    }

    fn place(listed: &str, branch: Option<&str>, head: Option<&str>, short: &str) -> Place {
        Place {
            id: id_of(Path::new(listed)), listed: listed.into(), path: listed.into(), branch: branch.map(Into::into), head: head.map(Into::into), dirty: false,
            record: "own", root: Some(listed.into()), record_dir: listed.into(), uncommitted: 0, last_write: None, runs: 0, gone: false, short: short.into(), many: true,
        }
    }

    /// A version is named by its commit and the minute it was committed; shown, with its place and that it is past.
    #[test]
    fn a_version_says_its_commit_and_time() {
        let v = Version { sha: "f44a1430abcdef0123456789f44a1430abcdef01".into(), committed: "2026-10-12T16:20:33+02:00".into() };
        assert_eq!(v.label(), "f44a1430abcd · 2026-10-12 14:20 UTC");
        let p = place("/p", Some("main"), None, "~/p");
        assert_eq!(v.shown(&p), "main · /p at f44a1430abcd, 2026-10-12 14:20 UTC: a past version, read only");
        assert_eq!(v.shown_short(&p), "main · ~/p as of f44a1430abcd · 2026-10-12 14:20 UTC · read only");
    }

    /// A place's path in short: from the main checkout when inside it, from ~ under the home folder, else as git lists
    /// it; the main checkout itself is never said from itself, and a home folder at the top names nothing apart.
    #[test]
    fn a_place_s_path_in_short_is_said_from_the_checkout_or_the_home_folder() {
        let (main, home) = (Path::new("/home/ana/p"), Path::new("/home/ana"));
        assert_eq!(short_from("/home/ana/p/.worktrees/b", Path::new("/home/ana/p/.worktrees/b"), main, home), ".worktrees/b");
        assert_eq!(short_from("/home/ana/p", main, main, home), "~/p");
        assert_eq!(short_from("/home/ana/wt/my work", Path::new("/home/ana/wt/my work"), main, home), "~/wt/my work");
        assert_eq!(short_from("/home/ana", home, main, home), "~");
        assert_eq!(short_from("/srv/wt/a", Path::new("/srv/wt/a"), main, home), "/srv/wt/a");
        assert_eq!(short_from("/tmp/x", Path::new("/private/tmp/x"), main, home), "/tmp/x");     // as git lists it
        assert_eq!(short_from("/srv/wt/a", Path::new("/srv/wt/a"), main, Path::new("/")), "/srv/wt/a");
        // a folder named ~ in the checkout, or ~ana, is not a home folder
        assert_eq!(short_from("/home/ana/p/~/wt", Path::new("/home/ana/p/~/wt"), main, home), "./~/wt");
        assert_eq!(short_from("/home/ana/p/~ana/wt", Path::new("/home/ana/p/~ana/wt"), main, home), "./~ana/wt");
        assert_eq!(short_from("/home/ana/~/wt", Path::new("/home/ana/~/wt"), main, home), "~/~/wt");
    }

    /// A place taken away while a screen reads it is said gone in rb's words, by its label in short.
    #[test]
    fn a_place_taken_away_meanwhile_is_said_gone() {
        let dir = std::env::temp_dir().join(format!("rb-gone-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join(DIR)).unwrap();
        std::fs::write(dir.join(".git"), "gitdir: elsewhere\n").unwrap();
        std::fs::write(dir.join(DIR).join(ROOT_FILE), "").unwrap();
        let mut p = place(&dir.to_string_lossy(), Some("exp-c"), None, "~/wt-c");
        p.path = dir.clone();
        let chosen = Chosen { root: dir.clone(), place: p, version: None, start: false };
        assert_eq!(chosen.gone_now(), None);
        std::fs::remove_file(dir.join(DIR).join(ROOT_FILE)).unwrap();
        assert_eq!(chosen.gone_now().as_deref(), Some("exp-c · ~/wt-c has no record of its own to show."));
        std::fs::remove_file(dir.join(".git")).unwrap();
        assert_eq!(chosen.gone_now().as_deref(), Some("exp-c · ~/wt-c is removed since: there is nothing there to show."));
        assert_eq!(Chosen { start: true, ..chosen }.gone_now(), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Another place's home names the commands it gives from the terminal the screen started in: with the cd there.
    #[test]
    fn another_place_s_home_says_its_commands_with_the_cd_there() {
        let mut there = place("/home/ana/wt-c", Some("exp-c"), None, "~/wt-c");
        there.record_dir = "/home/ana/my wt".into();
        let chosen = Chosen { root: "/home/ana/my wt".into(), place: there, version: None, start: false };
        let mut home = crate::obj! {"quiet" => "1 call waits on you: rb status lists it with its command.",
            "pending" => Value::List(vec![crate::obj! {"confirm" => "rb confirm d1"}])};
        confirm_from_here(&mut home, &chosen);
        assert_eq!(home.get("quiet").and_then(Value::as_str), Some("1 call waits on you: cd '/home/ana/my wt' && rb status lists it with its command."));
        assert_eq!(home.get("pending").and_then(|p| p.as_list()).and_then(|p| p[0].get("confirm")).and_then(Value::as_str), Some("cd '/home/ana/my wt' && rb confirm d1"));
        let mut here = home.clone();
        let start = Chosen { start: true, ..chosen };
        here.as_dict_mut().unwrap().insert("quiet".into(), Value::Str("1 call waits on you: rb status lists it with its command.".into()));
        confirm_from_here(&mut here, &start);
        assert_eq!(here.get("quiet").and_then(Value::as_str), Some("1 call waits on you: rb status lists it with its command."));
    }

    /// Two places never read the same: where their labels in short would, both are named by their whole paths.
    #[test]
    fn two_places_that_would_read_the_same_in_short_are_told_apart() {
        let sha = Some("a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d0");
        let mut places = vec![
            place("/home/ana/p", Some("main"), sha, "~/p"),
            place("/home/ana/p/~/wt", None, sha, "~/wt"),
            place("/home/ana/wt", None, sha, "~/wt"),
            place("/home/ana/wt2", None, sha, "~/wt2"),
        ];
        tell_apart(&mut places);
        let labels: Vec<String> = places.iter().map(Place::label_short).collect();
        assert_eq!(labels, ["main · ~/p", "detached at a1b2c3d4e5f6 · /home/ana/p/~/wt", "detached at a1b2c3d4e5f6 · /home/ana/wt", "detached at a1b2c3d4e5f6 · ~/wt2"]);
    }
    /// The shell's document of the INT8 example at `chosen`: a call with its lines, what waits with its confirm, and
    /// two runs, one recorded here and one in the other place.
    fn shell_doc(other: &str) -> Value {
        crate::pyjson::loads(&format!(
            r#"{{"places": {{"place": {{}}, "scope_bar": null, "elsewhere": []}},
               "calls": [{{"subject": "c1", "actions": [{{"action": "accept", "line": "rb decide c1 accept --why '‹why›'"}}]}}],
               "pending": {{"rows": [{{"id": "dec9", "confirm": "rb confirm dec9"}}], "confirm_all": {{"line": "", "confirm": "rb confirm dec9"}}}},
               "experiments": [{{"id": "int8", "design": {{"standing": "not_confirmed", "confirm": "rb confirm int8@0a1b"}}}}],
               "runs": {{"runs1": {{"observed_in": null}},
                        "runs2": {{"observed_in": {{"place": "{other}", "label_short": "recorded in exp-b · .worktrees/b at 4364dc8c1a2b"}}}}}}}}"#
        ))
        .unwrap()
    }

    fn two_places() -> Vec<Place> {
        let mut main = place("/home/ana/int8-resnet50", Some("main"), None, "~/int8-resnet50");
        main.record = "this";
        vec![main, place("/home/ana/int8-resnet50/.worktrees/b", Some("exp-b"), None, ".worktrees/b")]
    }

    fn get<'a>(v: &'a Value, path: &str) -> &'a Value {
        path.split('.').fold(v, |v, k| v.get(k).unwrap_or_else(|| panic!("no {k} in {v:?}")))
    }

    /// At the live start place: no scope bar, Elsewhere as given, and a run recorded in a place the screen can show
    /// links to it there, by that place's id; one recorded here links nowhere.
    #[test]
    fn the_live_start_place_draws_elsewhere_and_links_a_run_recorded_away() {
        let listed = two_places();
        let chosen = Chosen { root: listed[0].path.clone(), place: listed[0].clone(), version: None, start: true };
        let mut doc = shell_doc(&listed[1].id);
        on_the_shell(&mut doc, &chosen, &listed, vec![crate::obj! {"place" => listed[1].id.clone()}]);
        assert!(get(&doc, "places.scope_bar").is_null());
        assert_eq!(get(&doc, "places.elsewhere").as_list().map(Vec::len), Some(1));
        assert!(get(&doc, "runs.runs1.recorded_in").is_null());
        let r = get(&doc, "runs.runs2.recorded_in");
        assert_eq!(r.get("href").and_then(Value::as_str), Some(format!("#run-runs2&place={}", listed[1].id).as_str()));
        assert_eq!(r.get("label").and_then(Value::as_str), Some("recorded in exp-b · .worktrees/b at 4364dc8c1a2b"));
        // what waits keeps its command at the place it started on, live
        assert_eq!(get(&doc, "pending.rows").as_list().unwrap()[0].get("confirm").and_then(Value::as_str), Some("rb confirm dec9"));
        assert_eq!(get(&doc, "calls").as_list().unwrap()[0].get("actions").and_then(Value::as_list).map(Vec::len), Some(1));
        assert_eq!(get(&doc, "experiments").as_list().unwrap()[0].get("design").and_then(|d| d.get("confirm")).and_then(Value::as_str), Some("rb confirm int8@0a1b"));
    }

    /// Away: the bar says where, read only, with the way back to the start place's branch; no Elsewhere; a run
    /// recorded at the start place links back to it with no place in the address; one that cannot be shown links nowhere.
    #[test]
    fn another_place_says_where_it_is_and_links_back_to_the_start() {
        let mut listed = two_places();
        let chosen = Chosen { root: listed[1].path.clone(), place: listed[1].clone(), version: None, start: false };
        let mut doc = shell_doc(&listed[0].id);
        on_the_shell(&mut doc, &chosen, &listed, vec![crate::obj! {"place" => "x"}]);
        let bar = get(&doc, "places.scope_bar");
        assert_eq!(bar.get("line").and_then(Value::as_str), Some("Viewing exp-b · .worktrees/b · read only"));
        assert_eq!((bar.get("back").and_then(Value::as_str), bar.get("href").and_then(Value::as_str)), (Some("Back to main"), Some("#overview")));
        assert_eq!(get(&doc, "places.elsewhere").as_list().map(Vec::len), Some(0), "Elsewhere is the live start place's");
        assert_eq!(get(&doc, "runs.runs2.recorded_in.href").as_str(), Some("#run-runs2"));
        listed[0].gone = true;
        let mut doc = shell_doc(&listed[0].id);
        on_the_shell(&mut doc, &chosen, &listed, Vec::new());
        assert!(get(&doc, "runs.runs2.recorded_in").is_null(), "a place the screen cannot show is plain words");
    }

    /// A past version: the bar says it as the screens do, and the document holds no command to copy: no call's lines,
    /// no confirm, no confirm-all.
    #[test]
    fn a_past_version_holds_no_command_to_copy() {
        let listed = two_places();
        let v = Version { sha: "3f2a9c1".into(), committed: "2026-10-13T15:02:00+00:00".into() };
        let chosen = Chosen { root: "/tmp/view".into(), place: listed[0].clone(), version: Some(v), start: false };
        let mut doc = shell_doc(&listed[1].id);
        on_the_shell(&mut doc, &chosen, &listed, vec![crate::obj! {"place" => "x"}]);
        let bar = get(&doc, "places.scope_bar");
        assert_eq!(bar.get("line").and_then(Value::as_str), Some("main · ~/int8-resnet50 as of 3f2a9c1 · 2026-10-13 15:02 UTC · read only"));
        assert_eq!((bar.get("back").and_then(Value::as_str), bar.get("href").and_then(Value::as_str)), (Some("Back to now"), Some("#overview")));
        assert_eq!(bar.get("withheld").and_then(Value::as_str), Some(WITHHELD));
        assert_eq!(get(&doc, "calls").as_list().unwrap()[0].get("actions").and_then(Value::as_list).map(Vec::len), Some(0));
        assert_eq!(get(&doc, "pending.rows").as_list().unwrap()[0].get("confirm").and_then(Value::as_str), Some(""));
        assert!(get(&doc, "pending.confirm_all").is_null());
        assert_eq!(get(&doc, "experiments").as_list().unwrap()[0].get("design").and_then(|d| d.get("confirm")).and_then(Value::as_str), Some(""));
        assert_eq!(get(&doc, "places.elsewhere").as_list().map(Vec::len), Some(0));
        // a past version of another place goes back to that place, now
        let chosen = Chosen { place: listed[1].clone(), ..chosen };
        let mut doc = shell_doc(&listed[1].id);
        on_the_shell(&mut doc, &chosen, &listed, Vec::new());
        assert_eq!(get(&doc, "places.scope_bar.href").as_str(), Some(format!("#overview&place={}", listed[1].id).as_str()));
    }

    /// Elsewhere on the shell: each other place by its id, in short with its whole path, its own counts, what it holds
    /// not merged here, the calls that wait there with the command there that lists them, and what waits for a
    /// confirmation there as `pending_of` reads it; one that cannot be shown says why and reads nothing there.
    #[test]
    fn elsewhere_on_the_shell_says_each_place_with_its_own_counts() {
        let mut listed = two_places();
        listed[1].runs = 1;
        let rows = Value::List(vec![crate::obj! {
            "id" => listed[1].id.clone(), "adds" => "1 run not merged here", "calls" => 1i64,
            "calls_command" => "cd /home/ana/int8-resnet50/.worktrees/b && rb status"
        }]);
        let pending = |_: &Place| vec![crate::obj! {"id" => "dec9", "confirm" => "cd /home/ana/int8-resnet50/.worktrees/b && rb confirm dec9"}];
        let got = elsewhere_on_the_shell(&listed, &rows, &pending);
        assert_eq!(got.len(), 1);
        let e = &got[0];
        let s = |k: &str| e.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        assert_eq!((s("label"), s("path"), s("said"), s("adds")), ("exp-b · .worktrees/b".into(), "/home/ana/int8-resnet50/.worktrees/b".into(), "its own record · 1 run recorded from it".into(), "1 run not merged here".into()));
        assert_eq!(get(e, "waiting.n").as_i64(), Some(1));
        assert_eq!(get(e, "waiting.list").as_str(), Some("cd /home/ana/int8-resnet50/.worktrees/b && rb status"));
        assert_eq!(get(e, "pending").as_list().map(Vec::len), Some(1));
        listed[1].gone = true;
        let got = elsewhere_on_the_shell(&listed, &rows, &pending);
        assert_eq!(get(&got[0], "can_show").as_bool(), Some(false));
        assert_eq!(get(&got[0], "why_not").as_str(), Some("exp-b · .worktrees/b is removed since: there is nothing there to show."));
        assert_eq!(get(&got[0], "pending").as_list().map(Vec::len), Some(0));
    }
    /// Away from the start place a screen's choice still names the record it started on: its places are listed from it.
    #[test]
    fn a_choice_keeps_the_root_of_the_record_the_screen_started_on() {
        let mut listed = two_places();
        listed[0].root = Some("/home/ana/int8-resnet50".into());
        let at_start = choose(Path::new("/home/ana/int8-resnet50"), &listed, None, None, &|_: &Place| Vec::new(), &|| None).unwrap();
        assert!(at_start.start);
        assert_eq!(at_start.start_root(), PathBuf::from("/home/ana/int8-resnet50"));
        let away = Chosen { root: "/home/ana/int8-resnet50/.worktrees/b".into(), place: listed[1].clone(), version: None, start: false };
        assert_eq!(away.start_root(), PathBuf::from("/home/ana/int8-resnet50"));
    }
}
