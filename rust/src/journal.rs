//! What happened in the repository, whether or not anyone told rb: the journal, rb's own observation log in the clone's
//! shared git directory by worktree (`.git/rb/journal/<worktree>/`, or `.rb/journal/` outside git), and `found()`, the
//! closed-world reconciler that says which activity is in the repository and not in the record.

use crate::errors::Res;
use crate::ledger::{py_splitlines, Ledger, DIR};
use crate::pyjson::{self, Dumps};
use crate::record::*;
use crate::sources::{self, git, now, sha256_bytes};
use crate::value::{Dict, DictExt, Value};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

pub const DEFAULT_DIRS: &[&str] = &["out", "outputs", "results", "eval-out", "metrics"];
pub const SKIP_DIRS: &[&str] = &[".git", DIR, ".hg", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".idea", ".vscode"];
pub const MAX_FILES: usize = 4000;
pub const MAX_PARSE_BYTES: u64 = 4_000_000;
pub const PROGRESS_KEYS: &[&str] = &[
    "step", "steps", "global_step", "epoch", "epochs", "iter", "iters", "iteration", "iterations", "progress", "pct", "percent", "eta", "eta_s", "elapsed",
    "elapsed_s", "time", "timestamp", "ts", "heartbeat", "job", "job_id", "pid", "rank", "world_size", "gpu", "port", "attempt",
];
pub const RUNNING_WORDS: &[&str] = &["running", "pending", "started", "starting", "in_progress", "queued", "training"];
pub const TAIL_BYTES: usize = 64 * 1024;
pub const TAIL_LINES: usize = 10;

fn strip(s: &str) -> &str {
    crate::rbyaml::py_strip(s)
}

// ---------------------------------------------------------------- the journal itself

/// (the clone's shared git directory, this worktree's own, the project's place in the worktree), or None outside git.
fn git_dirs(root: &Path) -> Option<(PathBuf, PathBuf, String)> {
    let out = git(root, &["rev-parse", "--git-common-dir", "--git-dir", "--show-prefix"])?;
    let lines: Vec<&str> = py_splitlines(&out);
    if lines.len() < 2 || strip(lines[0]).is_empty() {
        return None;
    }
    let abs = |x: &str| {
        let p = PathBuf::from(strip(x));
        let p = if p.is_absolute() { p } else { root.join(p) };
        sources::resolve_path(&p)
    };
    let prefix = if lines.len() > 2 { strip(lines[2]).trim_end_matches('/').to_string() } else { String::new() };
    Some((abs(lines[0]), abs(lines[1]), prefix))
}

/// A linked worktree named "main" by git keeps its journal under this name: "~" is in no name git gives a worktree.
const LINKED_MAIN: &str = "main~linked";

/// The journal directory's name for a linked worktree git keeps under `name`: the main worktree's is "main".
fn linked_key(name: &str) -> String {
    if name == "main" {
        LINKED_MAIN.to_string()
    } else {
        name.to_string()
    }
}

/// Where this checkout's journal is: <git common dir>/rb/journal/<worktree>[/<the project's place>], the main worktree's
/// under "main" and a linked one's under its name, so it outlives the worktree; .rb/journal/ outside git.
pub fn journal_home(root: &Path) -> PathBuf {
    let Some((common, own, prefix)) = git_dirs(root) else { return root.join(DIR).join("journal") };
    let key = if own == common { "main".to_string() } else { linked_key(&own.file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default()) };
    let home = common.join("rb").join("journal").join(key);
    if prefix.is_empty() {
        home
    } else {
        home.join(prefix)
    }
}

/// A linked worktree's journal: its directory (<git common dir>/rb/journal/<its name>), the worktree's top, which its
/// place.json names, and git's entry for it (entry_id). None for the main worktree's and outside git. git gives a later
/// worktree the name of one removed; a moved one keeps its name and its entry.
struct Linked {
    dir: PathBuf,
    top: PathBuf,
    entry: Option<String>,
}

fn linked_place(root: &Path) -> Option<Linked> {
    let (common, own, _) = git_dirs(root)?;
    if own == common {
        return None;
    }
    let name = linked_key(&own.file_name()?.to_string_lossy());
    let top = sources::git_top(root)?;
    Some(Linked { dir: common.join("rb").join("journal").join(name), top, entry: entry_id(&own) })
}

/// What tells git's entry for a linked worktree (<git common dir>/worktrees/<name>) from a later one of the same name:
/// when it was made and, on unix, its inode. git worktree move and repair keep the entry; remove, then add, makes a new one.
fn entry_id(entry: &Path) -> Option<String> {
    let md = std::fs::metadata(entry).ok()?;
    let born = md.created().ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map(|d| d.as_nanos().to_string());
    #[cfg(unix)]
    let ino = Some(std::os::unix::fs::MetadataExt::ino(&md).to_string());
    #[cfg(not(unix))]
    let ino: Option<String> = None;
    if born.is_none() && ino.is_none() {
        return None;
    }
    Some(format!("{}:{}", born.unwrap_or_default(), ino.unwrap_or_default()))
}

/// What a journal directory's place.json says: the worktree it names, when it began, and git's entry for it.
struct Place {
    path: PathBuf,
    at: Option<String>,
    entry: Option<String>,
}

fn place_of(dir: &Path) -> Option<Place> {
    let text = std::fs::read_to_string(dir.join("place.json")).ok()?;
    let Ok(Value::Dict(d)) = pyjson::loads(&text) else { return None };
    let path = sources::resolve_path(Path::new(d.get_str("path")?));
    Some(Place { path, at: d.get_str("at").map(|a| a.to_string()), entry: d.get_str("entry").map(|e| e.to_string()) })
}

/// Whether a place.json is the worktree's whose top and git entry these are: by the entry when both have one (a moved
/// worktree keeps it), else by the path.
fn names_it(place: &Place, top: &Path, entry: Option<&str>) -> bool {
    match (place.entry.as_deref(), entry) {
        (Some(had), Some(now)) => had == now,
        _ => place.path == top,
    }
}

/// Whether a journal directory is this worktree's: its place.json names it, or it has none (a journal from before
/// place.json, adopted as it is).
fn is_mine(l: &Linked) -> bool {
    place_of(&l.dir).map_or(true, |p| names_it(&p, &l.top, l.entry.as_deref()))
}

/// The journal to read: its home, else the one a project kept in .rb/journal/ before it moved. A home another
/// worktree of the same name left is not this one's: nothing is read from it here.
pub fn read_dir(root: &Path) -> PathBuf {
    let home = journal_home(root);
    if let Some(l) = linked_place(root) {
        if l.dir.is_dir() && !is_mine(&l) {
            return l.dir.join("place.json").join("none"); // a path that holds nothing
        }
    }
    let old = root.join(DIR).join("journal");
    if home.is_dir() || !old.is_dir() {
        home
    } else {
        old
    }
}

/// Make a linked worktree's journal directory its own before its first use: one another worktree of the same name
/// left is moved aside to <name>@<when it began>, where orphans() reads it as a worktree removed since; one with no
/// place.json is adopted as it is. place.json names the worktree and git's entry for it, and follows a move.
fn claim(root: &Path) {
    let Some(l) = linked_place(root) else { return };
    if l.dir.is_dir() && !is_mine(&l) {
        let began = place_of(&l.dir).and_then(|p| p.at).unwrap_or_default();
        let stamp: String = began.chars().take(19).map(|c| if c.is_ascii_alphanumeric() || c == '-' { c } else { '-' }).collect();
        let name = l.dir.file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default();
        let base = format!("{name}@{}", if stamp.is_empty() { "earlier".to_string() } else { stamp });
        let mut aside = l.dir.with_file_name(&base);
        let mut n = 2;
        while aside.exists() {
            aside = l.dir.with_file_name(format!("{base}-{n}"));
            n += 1;
        }
        if std::fs::rename(&l.dir, &aside).is_err() {
            return;
        }
    }
    let had = place_of(&l.dir);
    let stale = match &had {
        None => true,
        Some(p) => p.path != l.top || (p.entry.is_none() && l.entry.is_some()),
    };
    if stale {
        let _ = std::fs::create_dir_all(&l.dir);
        let at = had.as_ref().and_then(|p| p.at.clone()).unwrap_or_else(now);
        let entry = l.entry.clone().or_else(|| had.and_then(|p| p.entry));
        let place = crate::obj! {"path" => crate::sys::slashed(&l.top), "at" => at, "entry" => Value::opt_str(entry)};
        let text = pyjson::dumps_with(&place, &Dumps { ensure_ascii: false, ..Default::default() }).unwrap_or_default() + "\n";
        let _ = std::fs::write(l.dir.join("place.json"), text);
    }
}

/// Where the journal is kept while its home in git's folder cannot be written (an agent's sandbox keeps that folder
/// read-only): the record's own .rb/journal/, which .rb/.gitignore keeps out of git, and where a project kept it before
/// it was in git. None outside git, where that is the home itself.
pub fn aside_dir(root: &Path) -> Option<PathBuf> {
    let aside = root.join(DIR).join("journal");
    (journal_home(root) != aside).then_some(aside)
}

/// Whether a journal folder holds a journal: its entries or its state.
fn holds_journal(dir: &Path) -> bool {
    dir.join("journal.jsonl").is_file() || dir.join("state.json").is_file()
}

/// Whether the journal is kept beside the record now, its home in git's folder not written since it could not be.
pub fn kept_aside(root: &Path) -> bool {
    aside_dir(root).map_or(false, |a| holds_journal(&a))
}

/// The folder a journal write goes to: its home, made on first use, else, while the home cannot be written or what was
/// kept beside the record has not moved home yet, the record's own .rb/journal/.
pub fn journal_dir(root: &Path) -> PathBuf {
    let d = journal_home(root);
    let Some(aside) = aside_dir(root) else {
        let _ = std::fs::create_dir_all(&d);
        return d;
    };
    // whether rb makes the journal's folder now, before a linked worktree's claim makes it
    let fresh = !d.is_dir();
    claim(root);
    if !d.is_dir() {
        if let Some(parent) = d.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if aside.is_dir() {
            let _ = crate::sys::replace(&aside, &d); // a journal kept beside the record moves to its home once
        }
        let _ = std::fs::create_dir_all(&d);
    }
    if crate::sys::can_write(&d) && !holds_journal(&aside) {
        if fresh {
            crate::owned::record(root, "repo", crate::dict! {"path" => crate::sys::slashed(&d), "how" => "dir"});
        }
        return d;
    }
    let _ = std::fs::create_dir_all(&aside);
    aside
}

/// Move what was kept beside the record, while the journal's home could not be written, into the home once a look can
/// write there: the entries the home does not hold yet, appended in their order after the home's, which are older; the
/// state the later look left, with the words that left found items out from both; then the same for rb's list of what
/// it wrote outside .rb/. Under the record's lock, which every journal write holds, so nothing lands in between.
fn settle(root: &Path) {
    if crate::dry::active() {
        return;
    }
    crate::owned::settle(root);
    let Some(aside) = aside_dir(root) else { return };
    if !holds_journal(&aside) {
        return;
    }
    let home = journal_home(root);
    let fresh = !home.is_dir();
    claim(root);
    let _ = std::fs::create_dir_all(&home);
    if !crate::sys::can_write(&home) {
        return;
    }
    if fresh {
        crate::owned::record(root, "repo", crate::dict! {"path" => crate::sys::slashed(&home), "how" => "dir"});
    }
    let moving = aside.join("journal.jsonl");
    if let Ok(text) = std::fs::read_to_string(&moving) {
        // a line the home holds already was moved by a look stopped before it took the line away here: a line is
        // the time it was written to the millisecond with what it says, so the same line twice is one moved twice
        let held = std::fs::read_to_string(home.join("journal.jsonl")).unwrap_or_default();
        let mut have: HashMap<&str, usize> = HashMap::new();
        for line in py_splitlines(&held).into_iter().map(strip).filter(|l| !l.is_empty()) {
            *have.entry(line).or_default() += 1;
        }
        let mut add = String::new();
        for line in py_splitlines(&text).into_iter().map(strip).filter(|l| !l.is_empty()) {
            match have.get_mut(line) {
                Some(n) if *n > 0 => *n -= 1,
                _ => {
                    add.push_str(line);
                    add.push('\n');
                }
            }
        }
        use std::io::Write;
        let wrote = add.is_empty()
            || std::fs::OpenOptions::new().create(true).append(true).open(home.join("journal.jsonl")).and_then(|mut f| f.write_all(add.as_bytes())).is_ok();
        if !wrote || std::fs::remove_file(&moving).is_err() {
            return;
        }
    }
    let state = aside.join("state.json");
    if state.is_file() {
        let read = |p: &Path| match std::fs::read_to_string(p).ok().and_then(|t| pyjson::loads(&t).ok()) {
            Some(Value::Dict(d)) => d,
            _ => Dict::new(),
        };
        if save_state(&home, &merged_state(read(&home.join("state.json")), read(&state))).is_err() || std::fs::remove_file(&state).is_err() {
            return;
        }
    }
    let _ = std::fs::remove_dir(&aside); // gone once nothing else is in it
}

/// Two journal states as one: the one the later look left, with every word that left a found item out, the user's
/// kept over a session's and otherwise the later one.
fn merged_state(home: Dict, aside: Dict) -> Dict {
    let looked = |st: &Dict| st.get("looked").and_then(|l| l.get_str("at")).unwrap_or("").to_string();
    let (mut later, earlier) = if looked(&aside) >= looked(&home) { (aside, home) } else { (home, aside) };
    let mut words = earlier.get("dismissed").and_then(|v| v.as_dict()).cloned().unwrap_or_default();
    for (k, v) in later.get("dismissed").and_then(|v| v.as_dict()).cloned().unwrap_or_default() {
        if !(words.get(&k).map_or(false, users_word) && !users_word(&v)) {
            words.insert(k, v);
        }
    }
    later.insert("dismissed".into(), Value::Dict(words));
    later
}

/// The worktree git now keeps under an admin name, from its gitdir file (a relative one is from git's entry).
fn admin_worktree(common: &Path, name: &str) -> Option<PathBuf> {
    let entry = common.join("worktrees").join(name);
    let text = std::fs::read_to_string(entry.join("gitdir")).ok()?;
    let dot_git = PathBuf::from(text.trim());
    let dot_git = if dot_git.is_absolute() { dot_git } else { entry.join(dot_git) };
    dot_git.parent().map(sources::resolve_path)
}

/// The journals of worktrees of this clone that are gone (removed or pruned): (the worktree's name, its entries).
pub fn orphans(root: &Path) -> Vec<(String, Vec<Dict>)> {
    let Some((common, _, prefix)) = git_dirs(root) else { return Vec::new() };
    let base = common.join("rb").join("journal");
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(&base).map(|rd| rd.flatten().map(|e| e.path()).collect()).unwrap_or_default();
    dirs.sort();
    let mut out = Vec::new();
    for d in dirs {
        let name = d.file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default();
        if !d.is_dir() {
            continue;
        }
        // the main checkout's, or a worktree still here, moved or not: a later worktree that took a removed one's
        // name is not it
        let admin = if name == LINKED_MAIN { "main" } else { name.as_str() };
        let entry = common.join("worktrees").join(admin);
        let here = entry.is_dir()
            && match place_of(&d) {
                Some(p) => match (p.entry.as_deref(), entry_id(&entry)) {
                    (Some(had), Some(now)) => had == now,
                    _ => admin_worktree(&common, admin).map_or(true, |now_there| now_there == p.path),
                },
                None => true,
            };
        if name == "main" || here {
            continue;
        }
        let file = if prefix.is_empty() { d.join("journal.jsonl") } else { d.join(&prefix).join("journal.jsonl") };
        let rows = rows_of(&file);
        if !rows.is_empty() {
            out.push((name, rows));
        }
    }
    out
}

/// The state the last look left: beside the record while the journal is kept there, else in the journal's home.
pub fn load_state(root: &Path) -> Dict {
    let p = aside_dir(root).map(|a| a.join("state.json")).filter(|p| p.is_file()).unwrap_or_else(|| read_dir(root).join("state.json"));
    if let Ok(text) = std::fs::read_to_string(&p) {
        if let Ok(Value::Dict(mut st)) = pyjson::loads(&text) {
            if !st.contains_key("files") {
                st.insert("files".into(), Value::empty_dict());
            }
            if !st.contains_key("git") {
                st.insert("git".into(), Value::Null);
            }
            if !st.contains_key("dismissed") {
                st.insert("dismissed".into(), Value::empty_dict());
            }
            return st;
        }
    }
    crate::dict! {"files" => Value::empty_dict(), "git" => Value::Null, "dismissed" => Value::empty_dict()}
}

fn save_state(dir: &Path, st: &Dict) -> Res<()> {
    let p = dir.join("state.json");
    let tmp = p.with_file_name("state.json.tmp");
    let text = pyjson::dumps_with(&Value::Dict(st.clone()), &Dumps { ensure_ascii: false, ..Default::default() }).unwrap_or_default();
    std::fs::write(&tmp, text).map_err(|e| crate::errors::RBError::could_not_write(&e, &tmp))?;
    crate::sys::replace(&tmp, &p).map_err(|e| crate::errors::RBError::could_not_write(&e, &p))?;
    Ok(())
}

pub fn append(root: &Path, entry: Dict) {
    append_in(&journal_dir(root), entry)
}

fn append_in(dir: &Path, entry: Dict) {
    let mut e = crate::dict! {"at" => now()};
    for (k, v) in entry {
        e.insert(k, v);
    }
    let line = pyjson::jsonl_line(&Value::Dict(e));
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("journal.jsonl")) {
        let _ = f.write_all(line.as_bytes());
    }
}

/// The journal's entries, oldest first: its home's, then those kept beside the record since the home could not be
/// written. A read never creates the journal.
pub fn entries(root: &Path, last: Option<usize>) -> Vec<Dict> {
    let home = read_dir(root);
    let mut rows = rows_of(&home.join("journal.jsonl"));
    if let Some(aside) = aside_dir(root).filter(|a| *a != home) {
        rows.extend(rows_of(&aside.join("journal.jsonl")));
    }
    match last.filter(|n| *n > 0) {
        Some(n) if rows.len() > n => rows.split_off(rows.len() - n),
        _ => rows,
    }
}

fn rows_of(p: &Path) -> Vec<Dict> {
    let Ok(text) = std::fs::read_to_string(p) else { return Vec::new() };
    let mut rows = Vec::new();
    for line in py_splitlines(&text) {
        if strip(line).is_empty() {
            continue;
        }
        if let Ok(Value::Dict(d)) = pyjson::loads(line) {
            rows.push(d);
        }
    }
    rows
}

// ---------------------------------------------------------------- watching

/// A change git reports, in words: added, modified or deleted.
fn change_word(code: u8) -> &'static str {
    match code {
        b'A' | b'C' | b'R' => "added",
        b'D' => "deleted",
        _ => "modified",
    }
}

/// What git says of the checkout now: HEAD, its branch, the tracked files the working tree changes (staged or not) as
/// (path, change), and what it lists as untracked, a folder wholly untracked as itself with its slash.
#[derive(Debug, Default)]
struct GitNow {
    head: Option<String>,
    branch: Option<String>,
    tree: Vec<(String, &'static str)>,
    untracked: Vec<String>,
}

/// What `git status --porcelain=v2 --branch -z` says. One question to git where three were asked before.
fn status_v2(out: &[u8]) -> GitNow {
    let mut g = GitNow::default();
    let mut records = out.split(|b| *b == 0).filter(|p| !p.is_empty());
    while let Some(r) = records.next() {
        let r = String::from_utf8_lossy(r);
        if let Some(oid) = r.strip_prefix("# branch.oid ") {
            g.head = Some(oid.to_string()).filter(|o| o != "(initial)");
        } else if let Some(name) = r.strip_prefix("# branch.head ") {
            g.branch = Some(name.to_string()).filter(|n| n != "(detached)");
        } else if let Some(path) = r.strip_prefix("? ") {
            g.untracked.push(path.to_string());
        } else if r.starts_with("1 ") || r.starts_with("2 ") || r.starts_with("u ") {
            // "1 XY sub mH mI mW hH hI path", "2 ... Xscore path" then the path it came from, "u XY sub m1 m2 m3 mW h1 h2 h3 path"
            let fields = match &r[..1] {
                "1" => 9,
                "2" => 10,
                _ => 11,
            };
            let parts: Vec<&str> = r.splitn(fields, ' ').collect();
            if r.starts_with('2') {
                records.next();
            }
            let (Some(xy), Some(path)) = (parts.get(1).map(|x| x.as_bytes()), parts.get(fields - 1)) else { continue };
            let (x, y) = (xy.first().copied().unwrap_or(b'.'), xy.get(1).copied().unwrap_or(b'.'));
            let code = if x == b'D' || y == b'D' { b'D' } else if matches!(x, b'A' | b'R' | b'C') { b'A' } else { b'M' };
            g.tree.push((path.to_string(), change_word(code)));
        }
    }
    g.tree.sort();
    g.untracked.sort();
    g
}

/// `git diff --name-status -z` pairs as (path, change), by path.
fn name_status(out: &[u8]) -> Vec<(String, &'static str)> {
    let parts: Vec<&[u8]> = out.split(|b| *b == 0).filter(|p| !p.is_empty()).collect();
    let mut paths: Vec<(String, &'static str)> = parts.chunks(2).filter(|c| c.len() == 2).map(|c| (String::from_utf8_lossy(c[1]).into_owned(), change_word(c[0][0]))).collect();
    paths.sort();
    paths
}

fn as_values(paths: Vec<(String, &'static str)>) -> Vec<Value> {
    paths.into_iter().map(|(path, change)| crate::obj! {"path" => path, "change" => change}).collect()
}

/// A git command in `root` given `input` on its standard input: what it printed, or None when it failed.
fn git_fed(root: &Path, args: &[&str], input: &[u8]) -> Option<Vec<u8>> {
    let cwd = root.to_string_lossy().into_owned();
    let mut full: Vec<&str> = vec!["-C", &cwd];
    full.extend_from_slice(args);
    sources::run("git", &full, None, &[], Some(input), std::time::Duration::from_secs(30)).filter(|r| r.code == 0).map(|r| r.stdout)
}

/// Which of these files, each holding bytes this look saw first, hold bytes git already kept that no commit made here
/// explains (`made_here`: the move's from, to, and the paths it changed): a stash, a restore or a checkout of one file
/// put them back. Bytes added to the index and not committed are new, unless a commit held them before; bytes a commit
/// made here brought are new, unless another commit held them before. A file git cannot answer for is said to hold
/// them: rb never reads bytes it cannot place as new.
fn kept_by_git(root: &Path, paths: &[String], made_here: Option<&(String, String, HashSet<String>)>) -> Vec<String> {
    let paths: Vec<&String> = paths.iter().filter(|p| !p.contains('\n')).collect();
    // the bytes as they are, and with \n line ends where they have \r\n, as git keeps a text file it converts
    let tmp = std::env::temp_dir().join(format!("rb-blobs-{}-{}", std::process::id(), crate::rng::os_randbits(32)));
    let mut asked: Vec<(usize, String)> = paths.iter().enumerate().map(|(i, p)| (i, p.to_string())).collect();
    for (i, p) in paths.iter().enumerate() {
        let Ok(b) = std::fs::read(root.join(p.as_str())) else { continue };
        if b.windows(2).any(|w| w == b"\r\n") && std::fs::create_dir_all(&tmp).is_ok() {
            let f = tmp.join(i.to_string());
            let lf: Vec<u8> = b.iter().enumerate().filter(|(j, c)| !(**c == b'\r' && b.get(j + 1) == Some(&b'\n'))).map(|(_, c)| *c).collect();
            if std::fs::write(&f, lf).is_ok() {
                asked.push((i, f.to_string_lossy().into_owned()));
            }
        }
    }
    let input: String = asked.iter().map(|(_, p)| format!("{p}\n")).collect();
    let ids = git_fed(root, &["hash-object", "--no-filters", "--stdin-paths"], input.as_bytes());
    let _ = std::fs::remove_dir_all(&tmp);
    let Some(ids) = ids.map(|b| String::from_utf8_lossy(&b).into_owned()) else { return paths.into_iter().cloned().collect() };
    let ids: Vec<&str> = ids.lines().map(str::trim).collect();
    if ids.len() != asked.len() {
        return paths.into_iter().cloned().collect();
    }
    let check: String = ids.iter().map(|id| format!("{id}\n")).collect();
    let Some(said) = git_fed(root, &["cat-file", "--batch-check=%(objectname) %(objecttype)"], check.as_bytes()) else { return paths.into_iter().cloned().collect() };
    let said = String::from_utf8_lossy(&said).into_owned();
    let kept: HashSet<&str> = said.lines().filter_map(|l| l.split_once(' ')).filter(|(_, t)| t.trim() == "blob").map(|(id, _)| id).collect();
    // each file's ids git keeps
    let mut held: Vec<(usize, &str)> = asked.iter().zip(&ids).filter(|(_, id)| kept.contains(**id)).map(|((i, _), id)| (*i, *id)).collect();
    held.sort();
    held.dedup_by_key(|h| h.0);
    if held.is_empty() {
        return Vec::new();
    }
    let specs: Vec<String> = held.iter().map(|(i, _)| format!(":(literal){}", paths[*i])).collect();
    let mut args: Vec<&str> = vec!["ls-files", "-s", "-z", "--"];
    args.extend(specs.iter().map(String::as_str));
    // what the index holds at each path: "<mode> <id> <stage>\t<path>"
    let index: HashMap<String, String> = sources::git_bytes(root, &args)
        .map(|b| {
            b.split(|c| *c == 0)
                .filter_map(|r| {
                    let r = String::from_utf8_lossy(r).into_owned();
                    let (head, path) = r.split_once('\t')?;
                    Some((path.to_string(), head.split(' ').nth(1)?.to_string()))
                })
                .collect()
        })
        .unwrap_or_default();
    let new_here: HashSet<String> = made_here
        .and_then(|(a, b, _)| sources::git(root, &["rev-list", &format!("{a}..{b}")]))
        .map(|t| t.lines().map(|l| l.trim().to_string()).filter(|l| !l.is_empty()).collect())
        .unwrap_or_default();
    let mut out: Vec<String> = Vec::new();
    // the files whose bytes git keeps because they were added here or committed here: new, unless a commit held them
    let mut asked_history: Vec<(&String, &str, bool)> = Vec::new();
    for (i, id) in held {
        let p = paths[i];
        let staged = index.get(p.as_str()).map_or(false, |x| asked.iter().zip(&ids).any(|((j, _), y)| *j == i && y == x));
        let committed = made_here.map_or(false, |(_, _, moved)| moved.contains(p.as_str()));
        if staged || committed {
            asked_history.push((p, id, committed));
        } else {
            out.push(p.clone());
        }
    }
    if asked_history.is_empty() {
        return out;
    }
    // the commits that held each id, merges and stashes too, asked of git a few hundred ids at a time
    let mut held_by: HashMap<String, HashSet<String>> = HashMap::new();
    let mut answered = true;
    let cwd = root.to_string_lossy().into_owned();
    for chunk in asked_history.chunks(300) {
        let finds: Vec<String> = chunk.iter().map(|(_, id, _)| format!("--find-object={id}")).collect();
        let mut args: Vec<&str> = vec!["-C", &cwd, "log", "--all", "--reflog", "-m", "--format=commit %H", "--raw", "--no-abbrev"];
        args.extend(finds.iter().map(String::as_str));
        let Some(text) = sources::run("git", &args, None, &[], None, std::time::Duration::from_secs(120)).filter(|r| r.code == 0).map(|r| String::from_utf8_lossy(&r.stdout).into_owned()) else {
            answered = false;
            break;
        };
        let mut at = String::new();
        for line in text.lines() {
            if let Some(c) = line.strip_prefix("commit ") {
                at = c.trim().to_string();
            } else if let Some(raw) = line.strip_prefix(':') {
                // ":<mode> <mode> <old id> <new id> <status>\t<path>"
                for oid in raw.split('\t').next().unwrap_or("").split(' ').skip(2).take(2) {
                    held_by.entry(oid.to_string()).or_default().insert(at.clone());
                }
            }
        }
    }
    // no commit yet: no commit held them
    let none_yet = !answered && sources::git(root, &["rev-parse", "--verify", "-q", "HEAD"]).is_none();
    for (p, id, committed) in asked_history {
        let new = none_yet || (answered && held_by.get(id).map_or(true, |cs| cs.iter().all(|c| committed && new_here.contains(c))));
        if !new {
            out.push(p.clone());
        }
    }
    out
}

/// The paths a move of HEAD changed, from the commit seen before to the one now (`git diff --name-status`), as
/// {path, change} by path; None when git cannot say (a commit gone since).
/// Whether HEAD went from `from` to `to` by commits made in this checkout alone (`git commit`, an amend), as its
/// reflog says: their files were written here. A checkout, pull, merge, reset or rebase among them, or a reflog that
/// does not reach `from`, is a move that can bring work done elsewhere.
fn committed_here(root: &Path, from: &str, to: &str) -> bool {
    let Some(log) = git(root, &["log", "-g", "-n", "200", "--format=%H %gs", "HEAD", "--"]) else { return false };
    let mut first = true;
    for line in log.lines() {
        let (hash, says) = line.split_once(' ').unwrap_or((line, ""));
        if first && hash != to {
            return false;
        }
        first = false;
        if hash == from {
            return true;
        }
        if !["commit: ", "commit (amend): ", "commit (initial): "].iter().any(|p| says.starts_with(p)) {
            return false;
        }
    }
    false
}

fn moved_paths(root: &Path, from: &str, to: &str) -> Option<Vec<Value>> {
    let excl = format!(":(top,exclude){DIR}");
    let out = sources::git_bytes(root, &["diff", "--name-status", "-z", "--no-renames", from, to, "--", ":(top)", &excl])?;
    Some(as_values(name_status(&out)))
}

/// HEAD, its branch and what the working tree changes and holds untracked, by one `git status`; None outside git.
fn git_now(root: &Path) -> Option<GitNow> {
    // in git, as the journal's own question (asked once) says: one git process fewer on every read
    git_dirs(root)?;
    let excl = format!(":(top,exclude){DIR}");
    // untracked as git lists them by default: a folder wholly untracked is one line, so a big one costs nothing here
    let args = ["status", "--porcelain=v2", "--branch", "--no-ahead-behind", "-z", "--untracked-files=normal", "--", ":(top)", &excl];
    Some(match sources::git_bytes(root, &args) {
        Some(status) => status_v2(&status),
        // a git that cannot say: HEAD and its branch, asked apart
        None => GitNow {
            head: git(root, &["rev-parse", "--verify", "-q", "HEAD"]).map(|h| strip(&h).to_string()).filter(|h| !h.is_empty()),
            branch: git(root, &["symbolic-ref", "--short", "-q", "HEAD"]).map(|h| strip(&h).to_string()).filter(|h| !h.is_empty()),
            ..Default::default()
        },
    })
}

/// The state rb compares from one look to the next: HEAD, its branch and whether the tree has uncommitted changes to
/// tracked files, in the shape every rb writes, so a look by an earlier rb reads no move.
fn git_state(g: Option<&GitNow>) -> Value {
    match g {
        Some(g) => crate::obj! {"commit" => g.head.clone(), "branch" => g.branch.clone(), "dirty" => !g.tree.is_empty()},
        None => Value::Null,
    }
}

fn is_parent(p: &Path, of: &Path) -> bool {
    of.parent().map_or(false, |_| of.ancestors().skip(1).any(|a| a == p))
}

/// Where results land: the usual output directories that exist, and every directory a run has named a file in, but in
/// a worktree nested in the checkout: another checkout, whose files are not this one's.
pub fn watch_roots(led: &Ledger) -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();
    for name in DEFAULT_DIRS {
        let p = led.root.join(name);
        if p.is_dir() {
            roots.push(p);
        }
    }
    if let Ok(runs) = led.live::<Run>() {
        for ev in runs {
            for fr in &ev.files {
                let full = led.root.join(&fr.path);
                let Some(d) = full.parent().map(|d| d.to_path_buf()) else { continue };
                if d.strip_prefix(&led.root).is_err() {
                    continue;
                }
                let has_dir = d.components().any(|c| c.as_os_str() == DIR);
                if d.is_dir() && !has_dir && d != led.root {
                    roots.push(d);
                }
            }
        }
    }
    roots.retain(|r| !crate::ledger::in_nested_checkout(r, &led.root));
    let mut out: Vec<PathBuf> = Vec::new();
    for r in roots {
        if !out.iter().any(|kept| r == *kept || is_parent(kept, &r)) {
            out.retain(|kept| !is_parent(&r, kept));
            out.push(r);
        }
    }
    out
}

fn mtime_ns(md: &std::fs::Metadata) -> i64 {
    crate::sys::mtime_ns(md) as i64
}

/// The look of the journal's state in which rb first saw a file's bytes (the first is 1).
const SEEN_IN: &str = "l";
/// A file's bytes came with a move of HEAD (a checkout, a pull or a merge) in the look that first saw them.
const BY_GIT: &str = "g";
/// Where rb saw a file's bytes before, at a path that still holds them: the file is a copy.
const COPY_OF: &str = "c";
/// Where rb first saw a file's bytes, at a path that no longer holds them: the file was moved, and its bytes are first
/// seen when they were there.
const MOVED_FROM: &str = "m";
/// A file's bytes were in git already when rb first saw them here, and no commit made here since brought them: a
/// stash, a restore or a checkout of one file put them back.
const STORED: &str = "s";
/// What a file's state carries from the look that first saw its bytes.
const FIRST_SEEN: &[&str] = &[SEEN_IN, BY_GIT, COPY_OF, MOVED_FROM, STORED];
/// The state's bytes rb saw that no watched file held at its last look, by their hash: what the file that held them
/// carried from the look that first saw them, and its path (`p`).
const AWAY: &str = "away";

/// Give `meta`, a file at `rel` whose bytes rb saw before at `from`, what that file carried from the look that first
/// saw them: first seen when they were, and moved from where they were first seen, unless that is `rel` itself.
fn first_seen_as(meta: &mut Dict, was: &Dict, from: &str, rel: &str) {
    for k in FIRST_SEEN {
        meta.shift_remove(*k);
        if let Some(v) = was.get(*k) {
            meta.insert(k.to_string(), v.clone());
        }
    }
    // a look by an earlier rb, which kept no count, is the first
    meta.entry(SEEN_IN.to_string()).or_insert(Value::Int(1));
    meta.entry(MOVED_FROM.to_string()).or_insert(Value::Str(from.to_string()));
    if meta.get_str(MOVED_FROM) == Some(rel) {
        meta.shift_remove(MOVED_FROM);
    }
}

/// How much earlier than a command's start a file it wrote may say it was last written: a filesystem keeps times to a
/// second or two.
const WRITE_SKEW_NS: i64 = 2_000_000_000;

/// The wall clock, in nanoseconds since the epoch, as a file's time is kept.
fn wall_ns() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_nanos() as i64)
}

/// Every watched file as {relative path: {mt, sz}}, plus whether the walk hit the cap.
fn walk(led: &Ledger) -> (Dict, bool) {
    walk_in(&led.root, &led.root, watch_roots(led))
}

/// What a command run in a checkout that is not the record's root (a nested worktree) wrote where results land
/// there: its loose files and its usual output directories, as {path from the record's root: {mt, sz}}, plus whether
/// the walk hit the cap.
fn walk_here(led: &Ledger) -> (Dict, bool) {
    let roots: Vec<PathBuf> = DEFAULT_DIRS.iter().map(|d| led.here.join(d)).filter(|p| p.is_dir()).collect();
    walk_in(&led.root, &led.here, roots)
}

/// The files of `top` itself and below `roots`, each by its path from `root`, plus whether the walk hit the cap. A
/// worktree of root's repository below a root is another checkout, and is not walked.
fn walk_in(root: &Path, top: &Path, roots: Vec<PathBuf>) -> (Dict, bool) {
    let mut seen = Dict::new();
    let mut truncated = false;
    let common = crate::ledger::common_dir_of(root);
    let take = |p: &Path, seen: &mut Dict| {
        let Ok(md) = std::fs::metadata(p) else { return };
        let rel = p.strip_prefix(root).map(|r| r.to_string_lossy().replace('\\', "/")).unwrap_or_default();
        seen.insert(rel, crate::obj! {"mt" => mtime_ns(&md), "sz" => md.len()});
    };
    // By name, so what a look journals comes in the same order on every filesystem (a directory lists in its own).
    if let Ok(rd) = std::fs::read_dir(top) {
        let mut children: Vec<(String, PathBuf)> = rd.flatten().map(|c| (c.file_name().to_string_lossy().into_owned(), c.path())).collect();
        children.sort();
        for (name, p) in children {
            if p.is_file() && !name.starts_with('.') {
                take(&p, &mut seen);
            }
        }
    }
    // os.walk, top-down: each directory's files by name, then its subdirectories by name, never through a symlink
    fn walk_dir(d: &Path, seen: &mut Dict, truncated: &mut bool, take: &dyn Fn(&Path, &mut Dict), common: Option<&Path>) {
        let Ok(rd) = std::fs::read_dir(d) else { return };
        let mut dirs: Vec<String> = Vec::new();
        let mut files: Vec<String> = Vec::new();
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if e.path().is_dir() {
                dirs.push(name);
            } else {
                files.push(name);
            }
        }
        dirs.retain(|x| !SKIP_DIRS.contains(&x.as_str()) && !x.starts_with('.'));
        dirs.sort();
        files.sort();
        for name in &files {
            if name.starts_with('.') || name.ends_with(".tmp") || name.ends_with(".lock") || name.ends_with(".pyc") {
                continue;
            }
            if seen.len() >= MAX_FILES {
                *truncated = true;
                return;
            }
            take(&d.join(name), seen);
        }
        for name in dirs {
            let p = d.join(&name);
            if p.symlink_metadata().map(|m| m.file_type().is_symlink()).unwrap_or(false) {
                continue;
            }
            if common.map_or(false, |c| crate::ledger::is_worktree_top(&p, c)) {
                continue;
            }
            walk_dir(&p, seen, truncated, take, common);
            if *truncated {
                return;
            }
        }
    }
    for dir in roots {
        walk_dir(&dir, &mut seen, &mut truncated, &take, common.as_deref());
        if truncated {
            break;
        }
    }
    (seen, truncated)
}

/// Look once and write down what changed.
pub fn scan(led: &Ledger) -> Res<Dict> {
    scan_noting(led, &mut Vec::new(), &mut HashSet::new())
}

/// `scan`, adding to `wrote` each path this look journals as new or changed, and to `checked` each path it read the
/// times of: a walk that stopped at its cap checked none past it.
fn scan_noting(led: &Ledger, wrote: &mut Vec<String>, checked: &mut HashSet<String>) -> Res<Dict> {
    let who = led.actor().map(|a| a.id).unwrap_or_default();
    led.locked(|| {
        settle(&led.root);
        let dir = journal_dir(&led.root);
        let mut st = load_state(&led.root);
        let (mut current, truncated) = walk(led);
        checked.extend(current.keys().cloned());
        let old: Dict = st.get("files").and_then(|v| v.as_dict()).cloned().unwrap_or_default();
        // which look this is: an earlier rb's looks count as the first, so bytes it saw are never read as new
        let look = st.get("looks").and_then(|v| v.as_i64()).unwrap_or(if old.is_empty() { 0 } else { 1 }) + 1;
        let mut summary = crate::dict! {"new" => 0i64, "changed" => 0i64, "gone" => 0i64, "git_moved" => false, "truncated" => truncated};
        let keys: Vec<String> = current.keys().cloned().collect();
        // what this look journals of the walk: the rest of what it sees change is journaled with git's word for it
        let mut journaled: HashSet<String> = HashSet::new();
        // the bytes rb saw before, by their hash, each at the path it saw them first: a file that lands with them was
        // moved or copied there, never newly written
        let mut seen_bytes: HashMap<String, (i64, String)> = HashMap::new();
        for (rel, w) in old.iter() {
            let Some(s) = w.get_str("sha").filter(|s| !s.is_empty()) else { continue };
            let l = w.get(SEEN_IN).and_then(|v| v.as_i64()).unwrap_or(1);
            let e = seen_bytes.entry(s.to_string()).or_insert((l, rel.clone()));
            if (l, rel) < (e.0, &e.1) {
                *e = (l, rel.clone());
            }
        }
        // bytes rb saw that no file held at the last look (a file stashed, a branch checked out, a file overwritten):
        // a file that comes back with them is first seen when they were
        let away: Dict = st.get(AWAY).and_then(|v| v.as_dict()).cloned().unwrap_or_default();
        // the paths whose new bytes this look saw first, which git may already hold
        let mut landed: Vec<String> = Vec::new();
        for rel in keys {
            let mut meta = current[&rel].as_dict().cloned().unwrap_or_default();
            let was = old.get(&rel).cloned();
            let sz = meta.get("sz").and_then(|v| v.as_i64()).unwrap_or(0) as u64;
            let readable = sources::structured_kind(&rel).is_some() && sz <= MAX_PARSE_BYTES;
            if let Some(w) = &was {
                if w.get("mt") == meta.get("mt") && w.get("sz") == meta.get("sz") {
                    let mut kept = w.as_dict().cloned().unwrap_or_default();
                    // a file an earlier rb hashed and kept no numbers of: read once, kept from then on
                    if readable && kept.get("sha").map_or(false, |s| s.truthy()) && !kept.contains_key("n") {
                        if let Ok(b) = std::fs::read(led.root.join(&rel)) {
                            keep_numbers(&mut kept, &rel, &b);
                        }
                    }
                    current.insert(rel.clone(), Value::Dict(kept));
                    continue;
                }
            }
            let mut sha: Option<String> = None;
            let mut bytes: Option<Vec<u8>> = None;
            if readable {
                if let Ok(b) = std::fs::read(led.root.join(&rel)) {
                    sha = Some(sha256_bytes(&b));
                    bytes = Some(b);
                }
            }
            if let (Some(w), Some(s)) = (&was, &sha) {
                if w.get_str("sha") == Some(s.as_str()) {
                    let at = w.get("at").cloned().unwrap_or_else(|| Value::Str(now()));
                    let mut m2 = meta.clone();
                    m2.insert("sha".into(), Value::Str(s.clone()));
                    m2.insert("at".into(), at);
                    // the same bytes, touched: first seen when they were
                    for k in FIRST_SEEN {
                        if let Some(v) = w.get(*k) {
                            m2.insert(k.to_string(), v.clone());
                        }
                    }
                    if let Some(b) = &bytes {
                        keep_numbers(&mut m2, &rel, b);
                    }
                    current.insert(rel.clone(), Value::Dict(m2));
                    continue;
                }
            }
            let change = if was.is_none() { "new" } else { "changed" };
            meta.insert("sha".into(), Value::opt_str(sha.clone()));
            meta.insert("at".into(), Value::Str(now()));
            meta.insert(SEEN_IN.into(), Value::Int(look));
            // bytes rb saw at another path: one that still holds them makes this a copy; one that no longer does, a
            // move, whose bytes are first seen when they were there
            match sha.as_ref().and_then(|s| seen_bytes.get(s)).map(|(_, from)| from).filter(|from| **from != rel) {
                Some(from) => {
                    let w = old.get(from).and_then(|v| v.as_dict()).cloned().unwrap_or_default();
                    let held = std::fs::metadata(led.root.join(from)).map_or(false, |md| Some(mtime_ns(&md)) == w.get("mt").and_then(|v| v.as_i64()) && Some(md.len() as i64) == w.get("sz").and_then(|v| v.as_i64()));
                    if held {
                        meta.insert(COPY_OF.into(), Value::Str(from.clone()));
                    } else {
                        first_seen_as(&mut meta, &w, from, &rel);
                    }
                }
                None if look > 1 && sha.is_some() => match sha.as_ref().and_then(|s| away.get(s)).and_then(|v| v.as_dict()) {
                    Some(w) => first_seen_as(&mut meta, w, w.get_str("p").unwrap_or(&rel), &rel),
                    None => landed.push(rel.clone()),
                },
                None => {}
            }
            if let Some(b) = &bytes {
                keep_numbers(&mut meta, &rel, b);
            }
            let n = summary.get(change).and_then(|v| v.as_i64()).unwrap_or(0);
            summary.insert(change.into(), Value::Int(n + 1));
            let mt = meta.get("mt").and_then(|v| v.as_i64()).unwrap_or(0);
            let written = crate::pytime::from_float(mt as f64 / 1e9).map(|d| crate::pytime::iso_ms(&d)).unwrap_or_default();
            let mut entry = crate::dict! {"op" => "file", "change" => change, "path" => rel.clone(), "bytes" => sz, "written" => written};
            if let Some(s) = sha.as_ref().filter(|s| !s.is_empty()) {
                entry.insert("sha256".into(), Value::Str(s.clone()));
            }
            entry.insert("by".into(), Value::Str(who.clone()));
            append_in(&dir, entry);
            journaled.insert(rel.clone());
            wrote.push(rel.clone());
            current.insert(rel.clone(), Value::Dict(meta));
        }
        for (rel, was) in old.iter() {
            // a file of a worktree nested here, which an earlier rb watched, is that checkout's: not gone, and no more this one's
            if current.contains_key(rel) || led.root.join(rel).parent().map_or(false, |d| crate::ledger::in_nested_checkout(d, &led.root)) {
                continue;
            }
            // a walk that stopped at its cap never reached the files past it: one still there is unseen, not gone, and
            // is kept as rb last saw it, so a later look that reaches it reads it as it was, never as new
            if truncated && led.root.join(rel).is_file() {
                current.insert(rel.clone(), was.clone());
                continue;
            }
            let n = summary.get("gone").and_then(|v| v.as_i64()).unwrap_or(0);
            summary.insert("gone".into(), Value::Int(n + 1));
            append_in(&dir, crate::dict! {"op" => "file", "change" => "gone", "path" => rel.clone(), "by" => who.clone()});
            journaled.insert(rel.clone());
        }
        let gn = git_now(&led.root);
        let g = git_state(gn.as_ref());
        let mut tree: Vec<(String, &'static str)> = gn.as_ref().map(|g| g.tree.clone()).unwrap_or_default();
        let mut cut = tree.len() > MAX_FILES;
        tree.truncate(MAX_FILES);
        let head_moved = st.get("git").map_or(true, |w| w.get("commit") != g.get("commit") || w.get("branch") != g.get("branch"));
        // a move of HEAD by commits made here: from, to, and the paths it changed, from where the record is
        let mut made_here: Option<(String, String, HashSet<String>)> = None;
        if Some(&g) != st.get("git") {
            summary.insert("git_moved".into(), Value::Bool(true));
            let mut entry = crate::dict! {"op" => "git"};
            match &g {
                Value::Dict(d) => {
                    for (k, v) in d {
                        entry.insert(k.clone(), v.clone());
                    }
                }
                _ => {
                    entry.insert("commit".into(), Value::Null);
                    entry.insert("branch".into(), Value::Null);
                    entry.insert("dirty".into(), Value::Bool(false));
                }
            }
            // what the move changed, path by path, from the commit seen before, and what the working tree changes
            let was = st.get("git").and_then(|w| w.get("commit")).and_then(|c| c.as_str()).map(str::to_string);
            let now_at = g.get("commit").and_then(|c| c.as_str()).map(str::to_string);
            if let (Some(a), Some(b)) = (was.as_deref(), now_at.as_deref()) {
                if a != b {
                    if let Some(mut paths) = moved_paths(&led.root, a, b) {
                        if paths.len() > MAX_FILES {
                            cut = true;
                            paths.truncate(MAX_FILES);
                        }
                        // a file whose new bytes this look saw first, which the move changed, came with it: a checkout or a
                        // pull brings work done elsewhere or before, never a result written here; a commit made here does
                        // not, and bytes rb saw before keep the look that first saw them
                        let brought = !committed_here(&led.root, a, b);
                        for p in paths.iter().filter_map(|v| v.get_str("path")).filter(|_| brought) {
                            if let Some(Value::Dict(m)) = current.get_mut(p).filter(|_| journaled.contains(p)) {
                                if m.get(SEEN_IN).and_then(|v| v.as_i64()) == Some(look) {
                                    m.insert(BY_GIT.into(), Value::Bool(true));
                                }
                            }
                        }
                        if !brought {
                            let prefix = git_dirs(&led.root).map(|d| d.2).filter(|p| !p.is_empty()).map(|p| format!("{p}/")).unwrap_or_default();
                            let here: HashSet<String> = paths.iter().filter_map(|v| v.get_str("path")).filter_map(|p| p.strip_prefix(prefix.as_str())).map(str::to_string).collect();
                            made_here = Some((a.to_string(), b.to_string(), here));
                        }
                        entry.insert("from".into(), Value::Str(a.to_string()));
                        entry.insert("changed".into(), Value::List(paths));
                    }
                }
            }
            if !tree.is_empty() {
                entry.insert("tree".into(), Value::List(as_values(tree.clone())));
            }
            if cut {
                entry.insert("cut".into(), Value::Bool(true));
            }
            entry.insert("by".into(), Value::Str(who.clone()));
            append_in(&dir, entry);
        }
        summary.insert("head_moved".into(), Value::Bool(head_moved && gn.is_some()));
        // new bytes git already kept, which no commit made here brought: a stash, a restore or a checkout of one file
        landed.retain(|p| current.get(p).map_or(false, |m| m.get(BY_GIT).is_none()));
        if gn.is_some() && !landed.is_empty() {
            for p in kept_by_git(&led.root, &landed, made_here.as_ref()) {
                if let Some(Value::Dict(m)) = current.get_mut(&p) {
                    m.insert(STORED.into(), Value::Bool(true));
                }
            }
        }
        // the working tree's own changes, kept apart from the git state an earlier rb compares
        let tree_value = Value::List(as_values(tree));
        let tree_changed = gn.is_some() && st.get("tree").map_or(false, |t| t.is_dict()) && st.get("tree").and_then(|t| t.get("paths")) != Some(&tree_value);
        if let Some(gn) = &gn {
            let prev = st.get("changes").and_then(|v| v.as_dict()).cloned();
            let (changes, more) = changes_look(led, gn, prev.as_ref(), &journaled, &who, &mut summary, wrote)?;
            cut |= more;
            if let Some(Value::Dict(f)) = changes.get("files") {
                checked.extend(f.keys().cloned());
            }
            st.insert("changes".into(), Value::Dict(changes));
            st.insert("tree".into(), crate::obj! {"paths" => tree_value, "cut" => cut});
        } else {
            st.shift_remove("changes");
            st.shift_remove("tree");
        }
        summary.insert("tree_changed".into(), Value::Bool(tree_changed));
        if cut {
            summary.insert("truncated".into(), Value::Bool(true));
        }
        // the bytes no watched file holds now, which a file held at the last look, each with the look that first saw
        // them, the earliest where two files held them; the latest MAX_FILES are kept
        let held: HashSet<&str> = current.values().filter_map(|m| m.get_str("sha")).filter(|s| !s.is_empty()).collect();
        let mut away_now: Dict = away.into_iter().filter(|(s, _)| !held.contains(s.as_str())).collect();
        for (rel, w) in old.iter() {
            let Some(s) = w.get_str("sha").filter(|s| !s.is_empty() && !held.contains(s)) else { continue };
            // a file of a worktree nested here is that checkout's
            if !current.contains_key(rel) && led.root.join(rel).parent().map_or(false, |d| crate::ledger::in_nested_checkout(d, &led.root)) {
                continue;
            }
            let first = |m: &Value| (m.get(SEEN_IN).and_then(|v| v.as_i64()).unwrap_or(1), m.get_str("p").unwrap_or("").to_string());
            let mut kept = crate::dict! {"p" => rel.clone()};
            for k in FIRST_SEEN {
                if let Some(v) = w.get(*k) {
                    kept.insert(k.to_string(), v.clone());
                }
            }
            let kept = Value::Dict(kept);
            if away_now.get(s).map_or(true, |had| first(&kept) < first(had)) {
                away_now.shift_remove(s);
                away_now.insert(s.to_string(), kept);
            }
        }
        let over = away_now.len().saturating_sub(MAX_FILES);
        away_now.drain(..over);
        if away_now.is_empty() {
            st.shift_remove(AWAY);
        } else {
            st.insert(AWAY.into(), Value::Dict(away_now));
        }
        let at = now();
        summary.insert("at".into(), Value::Str(at.clone()));
        st.insert("files".into(), Value::Dict(current));
        st.insert("git".into(), g);
        // the looks so far, when the first that counted them ended, and whether any stopped at the cap: what tells a
        // file that landed after rb began to look from one that was there
        st.insert("looks".into(), Value::Int(look));
        if !st.contains_key("first_ns") {
            st.insert("first_ns".into(), Value::Int(wall_ns()));
        }
        let ever = st.get("cut_ever").map_or(false, |c| c.truthy()) || truncated || cut;
        st.insert("cut_ever".into(), Value::Bool(ever));
        // when rb last looked, and whether that look stopped at the cap: what a read says it looked at
        st.insert("looked".into(), crate::obj! {"at" => at, "cut" => truncated || cut});
        save_state(&dir, &st)?;
        Ok(summary)
    })
}

fn ansi() -> &'static regex::Regex {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    R.get_or_init(|| regex::Regex::new(r"\x1b\[[0-9;?]*[ -/]*[@-~]").unwrap())
}

/// What a chunk of a command's output is written as on a Windows console, which takes text and not bytes: what is
/// not UTF-8 shows as the replacement character, and a character cut by the end of a chunk waits for its other half.
#[cfg(windows)]
fn console_text(held: &mut Vec<u8>, chunk: &[u8]) -> String {
    held.extend_from_slice(chunk);
    let upto = match std::str::from_utf8(held) {
        Err(e) if e.error_len().is_none() => e.valid_up_to(),
        _ => held.len(),
    };
    let text = String::from_utf8_lossy(&held[..upto]).into_owned();
    held.drain(..upto);
    text
}

/// A command's exit code as rb journals it: its own, or minus the signal that ended it.
#[cfg(unix)]
fn exit_code(status: &std::process::ExitStatus) -> i32 {
    use std::os::unix::process::ExitStatusExt;
    match status.code() {
        Some(c) => c,
        None => -status.signal().unwrap_or(0),
    }
}

#[cfg(not(unix))]
fn exit_code(status: &std::process::ExitStatus) -> i32 {
    status.code().unwrap_or(1)
}

/// Run a command with its standard output passed through as it comes (to `echo_fd`: 1, or 2 when standard output
/// carries a JSON envelope), keeping the last of it. On Linux and macOS, when rb's output is a terminal the command
/// gets a terminal of its own, so it prints what it would print alone; on Windows, and wherever rb's output is not a
/// terminal, it writes to a pipe.
pub fn tee(command: &[String], cwd: &Path, echo_fd: i32) -> (i32, Vec<u8>) {
    use std::io::{Read, Write};
    let mut tail: Vec<u8> = Vec::new();
    #[cfg(windows)]
    let (to_console, mut held) = (crate::cli::isatty(echo_fd), Vec::<u8>::new());
    #[allow(unused_mut)]
    let mut keep = |chunk: &[u8], tail: &mut Vec<u8>| {
        #[cfg(windows)]
        let shown: Vec<u8> = if to_console { console_text(&mut held, chunk).into_bytes() } else { chunk.to_vec() };
        #[cfg(not(windows))]
        let shown: &[u8] = chunk;
        if echo_fd == 2 {
            let mut o = std::io::stderr().lock();
            let _ = o.write_all(&shown);
            let _ = o.flush();
        } else {
            let mut o = std::io::stdout().lock();
            let _ = o.write_all(&shown);
            let _ = o.flush();
        }
        tail.extend_from_slice(chunk);
        if tail.len() > TAIL_BYTES {
            let cut = tail.len() - TAIL_BYTES;
            tail.drain(..cut);
        }
    };
    if command.is_empty() {
        return (127, Vec::new());
    }
    let mut cmd = std::process::Command::new(crate::sys::find_program(&command[0]));
    cmd.args(&command[1..]).current_dir(cwd);
    #[cfg(unix)]
    let (mut master, mut slave): (libc::c_int, libc::c_int) = (-1, -1);
    #[cfg(unix)]
    if crate::cli::isatty(echo_fd) {
        let ok = unsafe { libc::openpty(&mut master, &mut slave, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut()) } == 0;
        if ok {
            // close-on-exec: the terminal reaches the command as its standard output alone, so nothing the command leaves
            // running (rb's own analytics delivery among them) holds it open and keeps rb reading
            unsafe {
                libc::fcntl(master, libc::F_SETFD, libc::FD_CLOEXEC);
                libc::fcntl(slave, libc::F_SETFD, libc::FD_CLOEXEC);
            }
        } else {
            master = -1;
            slave = -1;
        }
    }
    #[cfg(unix)]
    let piped = master < 0;
    #[cfg(not(unix))]
    let piped = true;
    if piped {
        if std::env::var_os("PYTHONUNBUFFERED").is_none() {
            cmd.env("PYTHONUNBUFFERED", "1");
        }
        // a Python writing to a pipe on Windows encodes for the system's code page, and stops at the first character
        // that page lacks: asked for UTF-8, which is what rb reads, unless the user already chose
        if cfg!(windows) && std::env::var_os("PYTHONIOENCODING").is_none() && std::env::var_os("PYTHONUTF8").is_none() {
            cmd.env("PYTHONIOENCODING", "utf-8");
        }
        cmd.stdout(std::process::Stdio::piped());
    } else {
        #[cfg(unix)]
        {
            use std::os::unix::io::FromRawFd;
            let dup = unsafe { libc::fcntl(slave, libc::F_DUPFD_CLOEXEC, 0) }; // dup2 makes it the command's fd 1, which stays open
            cmd.stdout(unsafe { std::process::Stdio::from_raw_fd(dup) });
        }
    }
    let ctrl_c = crate::sys::catch_interrupts();
    let child = cmd.spawn();
    // the command keeps its own copy of the terminal's other end: while rb held one too, the terminal never closed
    // and rb waited for output that could not come
    drop(cmd);
    let mut child = match child {
        Ok(c) => c,
        Err(_) => {
            drop(ctrl_c);
            #[cfg(unix)]
            unsafe {
                if master >= 0 {
                    libc::close(master);
                }
                if slave >= 0 {
                    libc::close(slave);
                }
            }
            return (127, Vec::new());
        }
    };
    if !piped {
        #[cfg(unix)]
        {
            unsafe {
                libc::close(slave);
            }
            let mut buf = vec![0u8; 65536];
            loop {
                let n = unsafe { libc::read(master, buf.as_mut_ptr() as *mut libc::c_void, buf.len()) };
                if n < 0 {
                    if std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {
                        continue;
                    }
                    break;
                }
                if n == 0 {
                    break;
                }
                keep(&buf[..n as usize], &mut tail);
            }
            unsafe {
                libc::close(master);
            }
        }
    } else if let Some(mut out) = child.stdout.take() {
        let mut buf = vec![0u8; 65536];
        loop {
            match out.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => keep(&buf[..n], &mut tail),
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
    }
    let status = loop {
        match child.wait() {
            Ok(s) => break Some(s),
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => break None,
        }
    };
    let interrupted = crate::sys::interrupted();
    drop(ctrl_c);
    if interrupted {
        return (130, tail);
    }
    let rc = match status {
        Some(s) => exit_code(&s),
        None => 1,
    };
    (rc, tail)
}

/// What a command reported of itself: the numbers of a last line {"metrics": {...}}, and its last lines.
pub fn reported(output: &[u8]) -> (crate::value::Dict, Vec<String>) {
    let text = String::from_utf8_lossy(output);
    let text = ansi().replace_all(&text, "").replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<String> = text.split('\n').filter(|l| !strip(l).is_empty()).map(|l| l.trim_end_matches(|c: char| crate::rbyaml::py_isspace(c)).to_string()).collect();
    let mut metrics = Dict::new();
    if let Some(last) = lines.last() {
        if last.trim_start_matches(|c: char| crate::rbyaml::py_isspace(c)).starts_with('{') {
            if let Ok(Value::Dict(doc)) = pyjson::loads(last) {
                if let Some(m @ Value::Dict(_)) = doc.get("metrics") {
                    for (k, v) in sources::flatten(m, "") {
                        let x = match v {
                            Value::Int(i) => Some(i as f64),
                            Value::BigInt(s) => s.parse::<f64>().ok(),
                            Value::Float(f) => Some(f),
                            _ => None,
                        };
                        if let Some(x) = x.filter(|x| x.is_finite()) {
                            metrics.insert(k, Value::Float(x));
                        }
                    }
                }
            }
        }
    }
    let start = lines.len().saturating_sub(TAIL_LINES);
    (metrics, lines[start..].iter().map(|l| l.chars().take(300).collect()).collect())
}

/// `run`, returning (exit code, the metrics the command printed, its last lines when it failed).
pub fn run_reporting(led: &Ledger, command: &[String], echo_fd: i32) -> Res<(i32, Dict, Vec<String>)> {
    // what the look before the command checked: one that reached everything checked every path where rb looks, there
    // or not; past a walk's cap it checked nothing, so a file the look after it reaches there for the first time is the
    // command's only when the command's run is when it was last written
    let mut checked: HashSet<String> = HashSet::new();
    let whole = !scan_noting(led, &mut Vec::new(), &mut checked)?.get("truncated").map_or(false, |t| t.truthy());
    // the command runs, and its code is kept, in the checkout it was given in: a linked worktree nested in the
    // record's runs its own code, and its line goes in the record's journal
    let head = git(&led.here, &["rev-parse", "--verify", "-q", "HEAD"]).map(|h| strip(&h).to_string()).filter(|h| !h.is_empty());
    let snap = sources::code_snapshot(&led.here, DIR);
    let mut code = crate::dict! {"commit" => head.clone()};
    if let Some(t) = snap.as_ref().and_then(|s| s.get("tree")).filter(|v| v.truthy()) {
        code.insert("tree".into(), t.clone());
    }
    // a nested worktree's results land in its own folders, which the record's scan does not watch
    let away = led.here != led.root;
    let (before, before_cut) = if away { walk_here(led) } else { (Dict::new(), false) };
    let t0 = std::time::Instant::now();
    let started = wall_ns() - WRITE_SKEW_NS;
    let (rc, output) = tee(command, &led.here, echo_fd);
    sources::forget_git(); // the command may have committed
    let (metrics, tail) = reported(&output);
    let fixed = std::env::var("RB_TEST_NOW").map_or(false, |v| !v.is_empty());
    let secs = if fixed { 0.0 } else { crate::pyfloat::round_to(t0.elapsed().as_secs_f64(), 3) }; // a fixed clock does not move
    // what the command wrote is what the look after it journals: never what the look before it, or an earlier one, saw
    let mut changed_now: Vec<String> = Vec::new();
    scan_noting(led, &mut changed_now, &mut HashSet::new())?;
    let written_since = |p: &Path| std::fs::metadata(p).map_or(false, |md| mtime_ns(&md) >= started);
    let mut set: BTreeSet<String> = changed_now.into_iter().filter(|rel| whole || checked.contains(rel) || written_since(&led.root.join(rel))).collect();
    if away {
        set.extend(
            walk_here(led)
                .0
                .into_iter()
                .filter(|(rel, meta)| before.get(rel) != Some(meta) && (!before_cut || before.contains_key(rel) || meta.get("mt").and_then(|m| m.as_i64()).map_or(false, |m| m >= started)))
                .map(|(rel, _)| rel),
        );
    }
    let wrote: Vec<String> = set.into_iter().take(50).collect();
    let unnamed = unnamed_in(led, &wrote, &crate::cli::shlex_join(command), &output, &metrics);
    let who = led.actor().map(|a| a.id).unwrap_or_default();
    let mut entry = crate::dict! {
        "op" => "run", "command" => crate::cli::shlex_join(command), "exit" => rc, "secs" => secs,
        "wrote" => Value::list_of_str(wrote), "by" => who, WROTE_CHECKED => true
    };
    if !unnamed.is_empty() {
        entry.insert("unnamed".into(), Value::list_of_str(unnamed));
    }
    if !metrics.is_empty() {
        entry.insert("metrics".into(), Value::Dict(metrics.clone()));
    }
    if rc != 0 && !tail.is_empty() {
        entry.insert("tail".into(), Value::list_of_str(tail.clone()));
    }
    if head.is_some() || snap.as_ref().map_or(false, |s| !s.is_empty()) {
        entry.insert("code".into(), Value::Dict(code));
    }
    if let Some(w) = led.worktree_value() {
        entry.insert("worktree".into(), w);
    }
    // under the record's lock, as every journal write is, so a look moving the journal home misses none
    led.locked(|| {
        append(&led.root, entry);
        Ok(())
    })?;
    Ok((rc, metrics, if rc != 0 { tail } else { Vec::new() }))
}

/// On a watched run's entry: its list of what it wrote is what changed between the looks before and after it, as this
/// rb keeps it. An rb before this one could list files the command never wrote.
const WROTE_CHECKED: &str = "wrote_checked";

/// How long before a run's entry was written its look after the command may have begun: an entry is written once that
/// look ends.
const LOOK_AFTER_NS: i64 = 60_000_000_000;

/// The files a watched run's entry says the command wrote. Of the list an rb before this one kept, which could name
/// files the command never wrote, only a file last written while the command ran (`mtime_ns` of its path) is its.
pub fn wrote_of(e: &Dict, mtime_ns: &dyn Fn(&str) -> Option<i64>) -> Vec<String> {
    let Some(Value::List(w)) = e.get("wrote") else { return Vec::new() };
    let all = w.iter().map(pyjson::py_str);
    if e.get(WROTE_CHECKED).map_or(false, |v| v.truthy()) {
        return all.collect();
    }
    let Some(end) = e.get_str("at").and_then(crate::pytime::parse_iso).map(|d| d.timestamp_millis() as i64 * 1_000_000) else { return Vec::new() };
    let secs = e.get("secs").and_then(|v| v.as_f64()).filter(|s| s.is_finite() && *s >= 0.0).unwrap_or(0.0);
    let start = end - (secs * 1e9) as i64 - LOOK_AFTER_NS - WRITE_SKEW_NS;
    all.filter(|p| mtime_ns(p).map_or(false, |m| m >= start && m <= end + WRITE_SKEW_NS)).collect()
}

/// What a watched command's list holds that nothing ties to the command: whatever changed where rb looks while it ran is
/// on the list, another process's writes among it. A file is the command's when its line or its output names its path
/// or its folder, when its folder's name is a value its line gives under that name (out/seed2102/ for `--seeds 2102`),
/// when it holds the numbers the command printed, when it holds numbers and every file of numbers on the
/// list is in one folder, or when it is in a folder with one of those; any other is said as written while the command
/// ran, and never proposed as its run. What the project declares is not asked: the entry is kept as the command ran,
/// and a command run before anything was declared wrote its folder all the same.
fn unnamed_in(led: &Ledger, wrote: &[String], line: &str, output: &[u8], printed: &Dict) -> Vec<String> {
    let said = format!("{line}\n{}", ansi().replace_all(&String::from_utf8_lossy(output), "")).replace('\\', "/");
    let words = shell_words(line);
    let folder = |p: &str| p.rsplit_once('/').map(|(d, _)| d.to_string()).unwrap_or_default();
    let numbers: HashMap<&str, Dict> = wrote.iter().map(|p| (p.as_str(), numbers_in(&led.root, p))).collect();
    let results: Vec<&str> = wrote.iter().map(String::as_str).filter(|p| !numbers[p].is_empty()).collect();
    // files of numbers in one folder alone are one run's output, the command's
    let one_folder = results.iter().map(|p| folder(p)).collect::<HashSet<String>>().len() == 1;
    let named = |p: &str| {
        let dir = folder(p);
        said.contains(p)
            || (dir.contains('/') && said.contains(dir.as_str()))
            || named_by_value(&dir, &words)
            || (!printed.is_empty() && same_values(&numbers[p], printed))
            || (one_folder && results.contains(&p))
    };
    let theirs: HashSet<String> = wrote.iter().map(String::as_str).filter(|p| named(p)).map(folder).collect();
    wrote.iter().filter(|p| !named(p.as_str()) && !theirs.contains(&folder(p.as_str()))).cloned().collect()
}

fn name_value() -> &'static regex::Regex {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    R.get_or_init(|| regex::Regex::new(r"^([A-Za-z][A-Za-z_-]*?)[-_=]?([0-9]+(?:\.[0-9]+)?)$").unwrap())
}

/// Whether a folder's own name is a name and a value the command line gives under that name: out/seed2102/ for
/// `--seeds 2102 2103`, `--seed=2102`, `--seeds 2102,2103` or `seed=2102`, '-' and '_' alike and either name a plural.
fn named_by_value(dir: &str, words: &[String]) -> bool {
    let last = dir.rsplit('/').next().unwrap_or(dir);
    let Some(c) = name_value().captures(last) else { return false };
    let norm = |w: &str| w.trim_start_matches('-').to_lowercase().replace('_', "-");
    let (name, value) = (norm(&c[1]), c[2].to_string());
    let same_name = |k: &str| {
        let k = norm(k);
        !k.is_empty() && (k == name || k == format!("{name}s") || name == format!("{k}s"))
    };
    let holds = |v: &str| v.split(',').any(|x| same_word(x.trim(), &value));
    for (i, w) in words.iter().enumerate() {
        let (key, val) = match w.split_once('=') {
            Some((k, v)) => (k, Some(v)),
            None => (w.as_str(), None),
        };
        if !same_name(key) {
            continue;
        }
        let given = match val {
            Some(v) => holds(v),
            // the values that follow an option, up to the next one
            None if key.starts_with('-') => words[i + 1..].iter().take_while(|v| !v.starts_with('-') || v.parse::<f64>().is_ok()).any(|v| holds(v)),
            None => false,
        };
        if given {
            return true;
        }
    }
    false
}

pub fn run(led: &Ledger, command: &[String]) -> Res<i32> {
    Ok(run_reporting(led, command, 1)?.0)
}

/// A human or an agent says a found item is not a run. A session's word leaves it out of that session's own briefings
/// alone: the user's screens count it until the user leaves it out too, or it is recorded. A word rb cannot tell the
/// writer of is refused, as any write is.
pub fn dismiss(led: &Ledger, key: &str, why: &str) -> Res<()> {
    let actor = led.actor()?;
    let who = actor.id.clone();
    let session = crate::actor::session_for(&actor);
    led.locked(|| {
        settle(&led.root);
        let mut st = load_state(&led.root);
        let mut d = st.get("dismissed").and_then(|v| v.as_dict()).cloned().unwrap_or_default();
        let mut said = crate::dict! {"at" => now(), "by" => who.clone(), "why" => why};
        if let Some(s) = &session {
            said.insert("session".into(), Value::Str(s.clone()));
        }
        // a session's word never takes the place of the user's
        let users = d.get(key).map_or(false, users_word);
        if !(users && !actor.is_human()) {
            d.insert(key.into(), Value::Dict(said));
        }
        st.insert("dismissed".into(), Value::Dict(d));
        let dir = journal_dir(&led.root);
        save_state(&dir, &st)?;
        let mut entry = crate::dict! {"op" => "dismiss", "key" => key, "why" => why, "by" => who.clone()};
        if let Some(s) = &session {
            entry.insert("session".into(), Value::Str(s.clone()));
        }
        append_in(&dir, entry);
        Ok(())
    })
}

/// Whether a word that leaves a found item out is the user's: one written in a human's name, or by an rb from before
/// writers were kept (no `by` at all). A word whose writer rb could not tell is no one's.
pub fn users_word(said: &Value) -> bool {
    match said.get("by") {
        None => true,
        Some(by) => by.as_str().map_or(false, |b| b.starts_with("human:")),
    }
}

// ---------------------------------------------------------------- the closed-world reconciler

/// One piece of activity that is in the repository and not in the record.
#[derive(Debug, Clone, Default)]
pub struct Found {
    pub id: String,
    pub path: String,
    pub at: String,
    pub metrics: Dict,
    pub experiment: Option<String>,
    pub key: String,
    pub options: Vec<String>,
    pub argv: Vec<String>,
    pub facts: Vec<String>,
    /// the checkout `argv` runs in, as typed: the record's for a file found there (its path is from there), and for what
    /// a command printed, the worktree nested in it that the command ran in, while it is there
    pub here: PathBuf,
    /// what a watched command printed, not a file: `path` is the command as given
    pub printed: bool,
}

pub fn numbers_in(root: &Path, rel: &str) -> Dict {
    let p = root.join(rel);
    let Ok(md) = std::fs::metadata(&p) else { return Dict::new() };
    if md.len() > MAX_PARSE_BYTES {
        return Dict::new();
    }
    let Ok(doc) = sources::load_config(&p) else { return Dict::new() };
    finite_numbers(doc)
}

/// The numbers a .json, .yaml or .toml file's bytes hold, as numbers_in reads them from the file.
fn numbers_from(rel: &str, bytes: &[u8]) -> Dict {
    let Some(kind) = sources::structured_kind(rel) else { return Dict::new() };
    match sources::parse_structured(&String::from_utf8_lossy(bytes), kind, rel) {
        Ok(doc) => finite_numbers(sources::unwrap_wandb(doc)),
        Err(_) => Dict::new(),
    }
}

/// The most numbers a look keeps beside a file's mark: a file with more is read again when a read needs them.
const KEPT_NUMBERS: usize = 256;

/// What a look keeps of a file it read, beside its mark, so a read never parses an unchanged file again: its numbers
/// (`n`, null for more than the look keeps), and whether it says of itself that its run is still going (`live`).
fn keep_numbers(meta: &mut Dict, rel: &str, bytes: &[u8]) {
    let n = numbers_from(rel, bytes);
    if n.len() > KEPT_NUMBERS {
        meta.insert("n".into(), Value::Null);
        return;
    }
    meta.insert("n".into(), Value::Dict(n));
    if running_in(rel, bytes) {
        meta.insert("live".into(), Value::Bool(true));
    }
}

/// A file's numbers, and whether it says its run is still going (None where the look kept nothing: ask the file), as
/// the last look read them.
fn numbers_seen(root: &Path, rel: &str, meta: Option<&Dict>) -> (Dict, Option<bool>) {
    match meta.and_then(|m| m.get("n")) {
        Some(Value::Dict(n)) => (n.clone(), Some(meta.and_then(|m| m.get("live")).map_or(false, |l| l.truthy()))),
        _ => (numbers_in(root, rel), None),
    }
}

fn finite_numbers(doc: Value) -> Dict {
    let flat = if doc.is_dict() { sources::flatten(&sources::unwrap_wandb(doc), "") } else { Dict::new() };
    let mut out = Dict::new();
    for (k, v) in flat {
        let x = match v {
            Value::Int(i) => i as f64,
            Value::BigInt(s) => s.parse::<f64>().unwrap_or(f64::INFINITY),
            Value::Float(f) => f,
            _ => continue,
        };
        if x.is_finite() {
            out.insert(k, Value::Float(x));
        }
    }
    out
}

pub fn canon_of(name: &str, aliases: &indexmap::IndexMap<String, String>) -> Option<String> {
    aliases.get(name.rsplit('.').next().unwrap_or(name)).cloned()
}

/// Every metric name the project declares, to its canonical form.
pub fn names(led: &Ledger) -> Res<indexmap::IndexMap<String, String>> {
    let mut names: indexmap::IndexMap<String, String> = led.metric_aliases()?.as_ref().clone();
    for c in led.live::<Criterion>()? {
        let leaf = c.metric.rsplit('.').next().unwrap_or("").to_string();
        names.entry(leaf.clone()).or_insert(leaf);
    }
    Ok(names)
}

/// The experiment these metric names point at; ambiguity is returned as options, never resolved by guessing.
pub fn target(led: &Ledger, canon_names: &HashSet<String>, names: &indexmap::IndexMap<String, String>) -> Res<(Option<String>, Vec<String>)> {
    let exps = led.live::<Experiment>()?;
    if exps.is_empty() {
        return Ok((None, Vec::new()));
    }
    let crits = led.live::<Criterion>()?;
    let mut scores: indexmap::IndexMap<String, usize> = indexmap::IndexMap::new();
    for e in &exps {
        let read: HashSet<Option<String>> = crits.iter().filter(|c| c.experiment == e.id).map(|c| canon_of(&c.metric, names)).collect();
        let n = read.iter().filter(|r| r.as_ref().map_or(false, |x| canon_names.contains(x))).count();
        scores.insert(e.id.clone(), n);
    }
    let best = scores.values().copied().max().unwrap_or(0);
    let by_criteria: Vec<String> = scores.iter().filter(|(_, s)| **s == best && best > 0).map(|(k, _)| k.clone()).collect();
    if by_criteria.len() == 1 {
        return Ok((Some(by_criteria[0].clone()), Vec::new()));
    }
    if !by_criteria.is_empty() {
        return Ok((None, by_criteria));
    }
    if exps.len() == 1 {
        return Ok((Some(exps[0].id.clone()), Vec::new()));
    }
    Ok((None, exps.iter().map(|e| e.id.clone()).collect()))
}

/// What rb reconcile does with the numbers a watched command just printed, said after them: the run it proposes, and
/// where it proposes none, why.
pub fn printed_said(led: &Ledger, command: &str, metrics: &Dict) -> Res<String> {
    let wrote: Vec<String> = entries(&led.root, Some(64))
        .iter()
        .rev()
        .find(|e| e.get_str("op") == Some("run") && e.get_str("command") == Some(command))
        .map(|e| wrote_of(e, &|p: &str| std::fs::metadata(led.root.join(p)).ok().map(|md| mtime_ns(&md))))
        .unwrap_or_default();
    let all = found_all(led, Seen::Briefing)?;
    let aliases = names(led)?;
    let canon: HashSet<String> = metrics.keys().filter_map(|k| canon_of(k, &aliases)).collect();
    let ours = |what: &str| what == command || wrote.iter().any(|w| w == what);
    if let Some(it) = all.placed.iter().find(|it| ours(&it.path)) {
        let exp = it.experiment.clone().unwrap_or_default();
        let read = led.live::<Criterion>()?.iter().any(|c| c.experiment == exp && canon_of(&c.metric, &aliases).map_or(false, |m| canon.contains(&m)));
        return Ok(format!("rb reconcile proposes the run of {exp} that records them{}.", if read { String::new() } else { format!(", though no criterion of {exp} reads them") }));
    }
    if let Some(n) = all.kinds.needs.iter().find(|n| ours(&n.what)) {
        return Ok(format!(
            "rb reconcile lists the run of {} that records them, which needs {}: rb run add takes {}.",
            n.experiment,
            crate::assess::first_three(&n.names, ", "),
            n.names.iter().map(|x| format!("--set {x}=<value>")).collect::<Vec<_>>().join(" ")
        ));
    }
    if canon.is_empty() {
        return Ok("Nothing in the record declares them yet, so rb reconcile proposes no run of them: a run counts toward a criterion that reads its numbers (rb criterion add ... --metric <name>).".into());
    }
    Ok(match target(led, &canon, &aliases)? {
        (Some(t), _) if led.live::<Run>()?.iter().any(|r| r.experiment == t && same_values(&metrics_dict(r), metrics)) => {
            format!("rb reconcile proposes no new run: a recorded run of {t} holds these numbers.")
        }
        (Some(_), _) => "rb reconcile proposes no run of them; it says why.".to_string(),
        (None, opts) if opts.is_empty() => "No experiment is declared yet, so rb reconcile proposes no run of them.".to_string(),
        (None, opts) => format!("{} each read them, so rb reconcile lists them as unplaced: rb run add <one of them> records them.", crate::assess::first_three(&opts, ", ")),
    })
}

/// A command line as a POSIX shell splits it: '...' and "..." quote, a backslash escapes the next character.
fn shell_words(line: &str) -> Vec<String> {
    let mut words: Vec<String> = Vec::new();
    let (mut cur, mut any, mut quote) = (String::new(), false, None::<char>);
    let mut chars = line.chars();
    while let Some(c) = chars.next() {
        match (quote, c) {
            (Some(q), c) if c == q => quote = None,
            (Some('"'), '\\') => cur.extend(chars.next()),
            (Some(_), c) => cur.push(c),
            (None, '\'' | '"') => {
                quote = Some(c);
                any = true;
            }
            (None, '\\') => {
                cur.extend(chars.next());
                any = true;
            }
            (None, c) if c.is_whitespace() => {
                if any || !cur.is_empty() {
                    words.push(std::mem::take(&mut cur));
                }
                any = false;
            }
            (None, c) => cur.push(c),
        }
    }
    if any || !cur.is_empty() {
        words.push(cur);
    }
    words
}

/// Whether a command as someone gives it (`--command`) is the one a watched run's entry keeps, as rb quotes it: the
/// same words however they are quoted, as a POSIX shell splits them or, on Windows, as a command line does.
pub fn same_command(kept: &str, given: &str) -> bool {
    let (kept, given) = (kept.trim(), given.trim());
    if kept == given {
        return true;
    }
    let words = shell_words(kept);
    !words.is_empty() && (shell_words(given) == words || (cfg!(windows) && windows_words(given) == words))
}

/// A command line as Windows splits it into arguments (CommandLineToArgvW): "..." quotes, a backslash is itself
/// unless backslashes come before a quote, where each pair is one and an odd one makes the quote a character.
fn windows_words(line: &str) -> Vec<String> {
    let chars: Vec<char> = line.chars().collect();
    let (mut words, mut cur, mut any, mut quoted, mut i) = (Vec::new(), String::new(), false, false, 0);
    while i < chars.len() {
        let c = chars[i];
        if c == '\\' {
            let n = chars[i..].iter().take_while(|x| **x == '\\').count();
            i += n;
            if chars.get(i) == Some(&'"') {
                cur.extend(std::iter::repeat('\\').take(n / 2));
                if n % 2 == 1 {
                    cur.push('"');
                    i += 1;
                }
            } else {
                cur.extend(std::iter::repeat('\\').take(n));
            }
            any = true;
        } else if c == '"' {
            if quoted && chars.get(i + 1) == Some(&'"') {
                cur.push('"');
                i += 1;
            } else {
                quoted = !quoted;
            }
            any = true;
            i += 1;
        } else if c.is_whitespace() && !quoted {
            if any || !cur.is_empty() {
                words.push(std::mem::take(&mut cur));
            }
            any = false;
            i += 1;
        } else {
            cur.push(c);
            any = true;
            i += 1;
        }
    }
    if any || !cur.is_empty() {
        words.push(cur);
    }
    words
}

/// A per-run setting's value as a watched command gave it: `--seed 2`, `--seed=2` or `seed=2`, by the setting's own
/// name or its last part, '-' and '_' alike.
fn value_in_command(words: &[String], name: &str) -> Option<String> {
    let norm = |w: &str| w.replace('_', "-");
    let names = [norm(name), norm(crate::ledger::last_part(name))];
    for (i, w) in words.iter().enumerate().skip(1) {
        let (key, val) = match w.split_once('=') {
            Some((k, v)) => (k, Some(v)),
            None => (w.as_str(), None),
        };
        if !names.iter().any(|n| *n == norm(key.strip_prefix("--").unwrap_or(key))) {
            continue;
        }
        match val {
            Some(v) if !v.is_empty() => return Some(v.to_string()),
            None if key.starts_with("--") => return words.get(i + 1).filter(|v| !v.starts_with("--") && !v.is_empty()).cloned(),
            _ => {}
        }
    }
    None
}

/// The run's own config beside a results file: the name the experiment's recorded runs gave theirs, else config.yaml,
/// config.yml, config.json or config.toml in the same folder.
fn config_beside(root: &Path, exp: &str, recorded: &[Run], rel: &str) -> Option<String> {
    let dir = rel.rsplit_once('/').map(|(d, _)| format!("{d}/")).unwrap_or_default();
    let mut names: Vec<String> = recorded
        .iter()
        .filter(|r| r.experiment == exp)
        .filter_map(|r| r.receipt.config.as_ref())
        .map(|c| c.path.rsplit(['/', '\\']).next().unwrap_or("").to_string())
        .filter(|n| !n.is_empty())
        .collect();
    names.extend(["config.yaml", "config.yml", "config.json", "config.toml"].map(String::from));
    names.into_iter().map(|n| format!("{dir}{n}")).find(|p| p != rel && sources::structured_kind(p).is_some() && root.join(p).is_file())
}

/// A scalar as `--set name=<it>` gives it back.
fn set_word(v: &Value) -> Option<String> {
    match v {
        Value::Str(s) if !s.trim().is_empty() => Some(s.clone()),
        Value::Int(_) | Value::BigInt(_) | Value::Bool(_) => Some(pyjson::dumps(v)),
        Value::Float(f) if f.is_finite() => Some(pyjson::dumps(v)),
        _ => None,
    }
}

/// The watched run that wrote a file, while that is still the file's last change: its command line, and every file it
/// wrote.
pub struct Writer<'a> {
    pub command: &'a str,
    pub wrote: &'a [String],
    /// how many folders it wrote results files in: a command that wrote several runs' output gives none of them its
    /// line's values
    pub results: usize,
    /// how many results files it wrote: its line names one run's arm only where it wrote one
    pub files: usize,
}

/// What a proposed rb run add needs beyond its numbers: each per-run value the experiment declares, from the run's own
/// config beside its file (passed as --config) or else from the watched command's line, and what neither gives.
pub struct Fill {
    pub argv: Vec<String>,
    pub missing: Vec<String>,
    /// a per-run value the config beside the file and the command's line give apart: (name, the config, its value, the
    /// line's)
    pub apart: Vec<(String, String, String, String)>,
    /// the folders the command that wrote the file wrote results files in, where more than one: its line is none of
    /// theirs alone
    pub several: usize,
    /// the one variant whose own settings the run's config beside the file, or the watched command's line, states, and
    /// no other variant's: the arm a file of one arm's flat numbers is for
    pub arm: Option<String>,
}

impl Fill {
    /// The per-run values a line leaves to whoever records it.
    fn left(&self) -> Vec<String> {
        self.apart.iter().map(|(n, ..)| n.clone()).chain(self.missing.iter().cloned()).collect()
    }

    /// The unplaced line for a file's run this fill cannot finish, or None when it is whole.
    fn unfinished(&self, rel: &str, exp: &str, argv: &[String], by_command: bool) -> Option<String> {
        if !self.apart.is_empty() {
            let said: Vec<String> = self.apart.iter().map(|(n, c, cv, lv)| format!("{n} {cv} in {c}, {lv} on its command's line")).collect();
            let mut line = format!("rb {}", crate::cli::shlex_join(argv));
            for n in self.left() {
                line.push_str(&format!(" --set {n}=<value>"));
            }
            return Some(format!("{rel} is a run of {exp}, and the config beside it and the command that wrote it disagree ({}): {line}", said.join("; ")));
        }
        if self.missing.is_empty() {
            return None;
        }
        let gave = match (by_command, self.several) {
            (false, _) => Gave::Nothing,
            (true, n) => Gave::Command(n),
        };
        Some(needs_line(rel, exp, argv, &self.missing, gave))
    }
}

/// Two values as given, read as one: equal numbers (`1` and `1.0`), or the same text.
fn same_word(a: &str, b: &str) -> bool {
    match (a.parse::<f64>(), b.parse::<f64>()) {
        (Ok(x), Ok(y)) => x == y,
        _ => a == b,
    }
}

/// The recorded runs of the experiment that read `config`, as these bytes, each with whether every per-run value it
/// gave is what the config states: a base config several runs shared, with a value none of them ran with, is no one
/// run's.
fn read_by_runs(root: &Path, exp: &Experiment, recorded: &[Run], config: &str, flat: &Dict, per_run: &[String]) -> Vec<bool> {
    let norm = |p: &str| p.trim_start_matches("./").replace('\\', "/");
    let now = std::fs::read(root.join(config)).ok().map(|b| sha256_bytes(&b));
    recorded
        .iter()
        .filter(|r| r.experiment == exp.id && r.retracted.is_none())
        .filter(|r| r.receipt.config.as_ref().map_or(false, |c| norm(&c.path) == norm(config) && c.sha256.as_ref().map_or(true, |s| Some(s) == now.as_ref())))
        .map(|r| {
            per_run.iter().all(|n| {
                let (v, found) = crate::ledger::config_value(flat, n);
                match (found.then(|| set_word(&v)).flatten(), r.per_run.get(n).and_then(set_word)) {
                    (Some(c), Some(given)) => same_word(&c, &given),
                    _ => true,
                }
            })
        })
        .collect()
}

/// Each per-run value the experiment declares, for a proposed run of `rel` (None for what a command printed): from the
/// config beside the file when it is the run's own (one the command that wrote the file wrote too, one a recorded run
/// read with the values it states, or, for a file no watched command wrote, one rb saw land after its first look, as
/// the file), or else from the line of the command that wrote it, when that command wrote no other results file. A
/// config that states none of the settings the spec declares (a model's own config.json) is no run's config. Where the
/// two give a value apart, neither is taken.
fn fill_per_run(led: &Ledger, exp: Option<&Experiment>, recorded: &[Run], rel: Option<&str>, writer: Option<&Writer>, fresh: &dyn Fn(&str) -> bool) -> Fill {
    let mut fill = Fill { argv: Vec::new(), missing: Vec::new(), apart: Vec::new(), several: 0, arm: None };
    let Some(exp) = exp else { return fill };
    let settings = exp.all_settings();
    let mut per_run: Vec<String> = settings.iter().filter(|(_, s)| s.per_run).map(|(n, _)| n.clone()).collect();
    per_run.sort();
    per_run.dedup();
    let beside = rel.and_then(|r| config_beside(&led.root, &exp.id, recorded, r));
    let flat = beside.as_ref().and_then(|c| sources::load_config(&led.root.join(c)).ok()).map(|d| sources::flatten(&d, "")).unwrap_or_default();
    let beside = beside.filter(|_| settings.iter().any(|(n, _)| crate::ledger::config_value(&flat, n).1));
    let own = beside.as_deref().map_or(false, |c| {
        let read = read_by_runs(&led.root, exp, recorded, c, &flat, &per_run);
        // a run that read these bytes with another value is what tells a shared base config from a run's own
        !read.contains(&false) && (writer.map_or(false, |w| w.wrote.iter().any(|x| x == c)) || read.contains(&true) || (writer.is_none() && fresh(c)))
    });
    let words = match writer {
        Some(w) if w.results <= 1 => shell_words(w.command),
        Some(w) => {
            fill.several = w.results;
            Vec::new()
        }
        None => Vec::new(),
    };
    for name in per_run {
        let (v, found) = crate::ledger::config_value(&flat, &name);
        let in_config = if found && beside.is_some() { set_word(&v) } else { None };
        match (in_config, value_in_command(&words, &name)) {
            (Some(c), Some(l)) if !same_word(&c, &l) => fill.apart.push((name, beside.clone().unwrap_or_default(), c, l)),
            (Some(c), _) if own => fill.argv.extend(["--set".to_string(), format!("{name}={c}")]),
            (_, Some(l)) => fill.argv.extend(["--set".to_string(), format!("{name}={l}")]),
            _ => fill.missing.push(name),
        }
    }
    // a line names the arm of the one results file it wrote; of several, it says nothing of which is whose
    let line: &[String] = if writer.map_or(false, |w| w.files == 1) { &words } else { &[] };
    fill.arm = arm_of(exp, own.then_some(&flat), line);
    if let Some(c) = beside.filter(|_| own) {
        fill.argv.extend(["--config".to_string(), c]);
    }
    fill
}

/// The one variant whose own settings a run's config (flattened) or its command's words state: a variant is stated when
/// at least one of its own settings with a value is given, by the config or else the line, and each one given is its
/// value. None where no variant, or more than one, is stated so.
fn arm_of(exp: &Experiment, config: Option<&Dict>, words: &[String]) -> Option<String> {
    let given = |name: &str| -> Option<String> {
        if let Some(flat) = config {
            let (v, found) = crate::ledger::config_value(flat, name);
            if found {
                return set_word(&v);
            }
        }
        value_in_command(words, name)
    };
    let mut stated: Vec<&str> = Vec::new();
    for v in exp.live_variants() {
        let own: Vec<(&str, String)> = v.settings.iter().filter(|s| s.retracted.is_none() && !s.per_run).filter_map(|s| set_word(&s.value).map(|w| (s.name.as_str(), w))).collect();
        let mut any = false;
        let all = own.iter().all(|(name, value)| match given(name) {
            Some(g) => {
                any = true;
                same_word(&g, value)
            }
            None => true,
        });
        if any && all {
            stated.push(v.name.as_str());
        }
    }
    match stated.as_slice() {
        [one] => Some(one.to_string()),
        _ => None,
    }
}

/// Whether a file's numbers are one arm's, flat: the experiment has variants and no number names one, or a role, or a
/// change, as its first part.
fn flat_numbers(exp: &Experiment, nums: &Dict) -> bool {
    let variants = exp.live_variants();
    !variants.is_empty()
        && !nums.is_empty()
        && nums.keys().all(|k| {
            k.split_once('.').map_or(true, |(head, _)| {
                !variants.iter().any(|v| v.name == head) && !["baseline", "candidate", "control", "ablation", "change", "subject", "reference"].contains(&head)
            })
        })
}

/// The --config a proposed rb run add passes.
fn config_of(argv: &[String]) -> Option<String> {
    argv.iter().position(|a| a == "--config").and_then(|i| argv.get(i + 1)).cloned()
}

/// What tells a results file that landed after rb began to look here from one that was there: the look that first saw
/// its bytes, whether they came with a move of HEAD, were in git already, or are bytes rb saw at another path, and when
/// it was last written, against rb's first look.
struct Fresh {
    first_ns: Option<i64>,
    cut_ever: bool,
    /// the clock is fixed (RB_TEST_NOW, as the golden harness has it), and file times are dated against it, not the wall
    fixed: bool,
}

/// Why a file is not one rb saw land after its first look here.
#[derive(Clone, Debug, PartialEq)]
pub enum Older {
    /// its bytes were there at rb's first look (or an earlier rb's), at the path given when they were moved since
    There(Option<String>),
    /// they came with a checkout, a pull or a merge, at the path given when they were moved since
    Git(Option<String>),
    /// it was last written before rb's first look
    Before,
    /// git kept its bytes already, and no commit made here brought them: a stash, a restore or a checkout of one file
    Stored,
    /// it holds the bytes of a file rb saw first, which still holds them
    Copy(String),
}

impl Fresh {
    fn of(st: &Dict) -> Fresh {
        Fresh {
            first_ns: st.get("first_ns").and_then(|v| v.as_i64()),
            cut_ever: st.get("cut_ever").map_or(false, |c| c.truthy()),
            fixed: std::env::var("RB_TEST_NOW").map_or(false, |v| !v.is_empty()),
        }
    }

    /// None for a file rb saw land after its first look here, else why not.
    fn not(&self, meta: &Dict) -> Option<Older> {
        if let Some(of) = meta.get_str(COPY_OF) {
            return Some(Older::Copy(of.to_string()));
        }
        let moved = meta.get_str(MOVED_FROM).map(str::to_string);
        if !meta.get(SEEN_IN).and_then(|v| v.as_i64()).map_or(false, |l| l > 1) {
            return Some(Older::There(moved));
        }
        if meta.get(BY_GIT).map_or(false, |g| g.truthy()) {
            return Some(Older::Git(moved));
        }
        if meta.get(STORED).map_or(false, |g| g.truthy()) {
            return Some(Older::Stored);
        }
        // a file written before rb began, moved or copied in with its time (mv, cp -p, rsync -t, an archive); a fixed
        // clock does not move, so there only past the cap is a file's time read against rb's first look
        let mt = meta.get("mt").and_then(|v| v.as_i64()).unwrap_or(0);
        match self.first_ns {
            Some(first) if (self.cut_ever || !self.fixed) && mt < first - WRITE_SKEW_NS => Some(Older::Before),
            None if self.cut_ever => Some(Older::Before),
            _ => None,
        }
    }
}

/// Whether a watched command runs git (any of its words names git, by path or not, a shell's -c line included): one that
/// checks out, restores or pops bytes git kept puts back what was there, and makes nothing anew.
fn is_git_command(command: &str) -> bool {
    shell_words(command).iter().flat_map(|w| w.split_whitespace().map(str::to_string).collect::<Vec<_>>()).any(|t| {
        let t = t.trim_matches(|c: char| c == '(' || c == ')' || c == ';' || c == '&' || c == '|' || c == '\'' || c == '"');
        let name = t.rsplit(['/', '\\']).next().unwrap_or(t).to_lowercase();
        name == "git" || name == "git.exe"
    })
}

/// The line for a results file rb cannot tell from the work before the record: why, and the rb run add that records it
/// for whoever knows it is a run, never proposed.
fn older_line(rel: &str, why: &Older, place: &str, argv: &[String], missing: &[String]) -> String {
    let mut line = format!("rb {}", crate::cli::shlex_join(argv));
    for n in missing {
        line.push_str(&format!(" --set {n}=<value>"));
    }
    let was = |m: &Option<String>| m.as_ref().map(|m| format!(", as {m}")).unwrap_or_default();
    let when = match why {
        Older::There(m) => format!("was there when rb first looked here{}", was(m)),
        Older::Git(m) => format!("came with a checkout, pull or merge{}", was(m)),
        Older::Before => "was last written before rb first looked here".to_string(),
        Older::Stored => "holds bytes git already kept".to_string(),
        Older::Copy(of) => {
            return format!("{rel} holds the same bytes as {of}{place}, so rb reads it as a copy and proposes no run of it: {line} records it, if it is a run of its own")
        }
    };
    format!("{rel} {when}{place}, so rb cannot tell it from the work before the record and proposes no run of it: {line} records it, if it is one")
}

/// Where a run's per-run values were looked for: a watched command's printed numbers (its line, where it wrote results
/// in this many folders), a file a watched command wrote (its own config, and the line of that command, which wrote
/// results in this many folders), or a file no watched command wrote (its own config alone).
pub enum Gave {
    Printed(usize),
    Command(usize),
    Nothing,
}

/// The line for a results file that changed while a watched command ran, which nothing ties to that command: it may be
/// another process's, so it is never proposed as the command's run, and the rb run add that records it is said.
fn while_line(rel: &str, command: &str, argv: &[String], left: &[String]) -> String {
    let mut line = format!("rb {}", crate::cli::shlex_join(argv));
    for n in left {
        line.push_str(&format!(" --set {n}=<value>"));
    }
    format!("{rel} was written while `{command}` ran, and nothing that command was given or printed names it, so rb proposes no run of it: {line} records it, if it is one")
}

/// The line a per-run value it cannot read leaves to whoever records it: rb run add with --set <name>=<value>, never
/// run for them.
fn needs_line(what: &str, exp: &str, argv: &[String], missing: &[String], gave: Gave) -> String {
    let mut line = format!("rb {}", crate::cli::shlex_join(argv));
    for n in missing {
        line.push_str(&format!(" --set {n}=<value>"));
    }
    let it = if missing.len() == 1 { "it" } else { "them" };
    let why = match gave {
        Gave::Printed(n) if n > 1 => format!("its command wrote results in {n} folders, so its line is no one run's"),
        Gave::Printed(_) => format!("its command line does not give {it}"),
        Gave::Command(n) if n > 1 => format!("no config of its own beside it gives {it}, and the command that wrote it wrote results in {n} folders, so its line is no one run's"),
        Gave::Command(_) => format!("neither a config of its own beside it nor the command that wrote it gives {it}"),
        Gave::Nothing => format!("no config of its own beside it gives {it}, and no watched command wrote it"),
    };
    format!("{what} is a run of {exp}, which declares {} per-run, and {why}: {line}", missing.join(", "))
}

/// Whether a small JSON file is one whose bytes might say its run is still going.
fn may_say_running(path: &Path) -> bool {
    let name = path.file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default();
    let suffix = match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => name[i..].to_lowercase(),
        _ => String::new(),
    };
    suffix == ".json"
}

/// Whether a small JSON file says of itself that its run is still going.
fn says_running(path: &Path) -> bool {
    if !may_say_running(path) {
        return false;
    }
    let Ok(md) = std::fs::metadata(path) else { return false };
    if md.len() > 64_000 {
        return false;
    }
    let Ok(bytes) = std::fs::read(path) else { return false };
    running_in(&path.to_string_lossy(), &bytes)
}

/// The same, of a file's bytes.
fn running_in(rel: &str, bytes: &[u8]) -> bool {
    if !may_say_running(Path::new(rel)) || bytes.len() > 64_000 {
        return false;
    }
    let Ok(text) = std::str::from_utf8(bytes) else { return false };
    let Ok(Value::Dict(d)) = pyjson::loads(text) else { return false };
    ["state", "status", "phase"].iter().any(|k| {
        let s = d.get(*k).map(pyjson::py_str).unwrap_or_default().to_lowercase();
        RUNNING_WORDS.contains(&s.as_str())
    })
}

fn same_values(a: &Dict, b: &Dict) -> bool {
    if a.is_empty() || b.is_empty() || a.len() != b.len() {
        return false;
    }
    let num = |v: &Value| -> Option<f64> {
        match v {
            Value::Int(i) => Some(*i as f64),
            Value::Bool(x) => Some(if *x { 1.0 } else { 0.0 }),
            Value::Float(f) => Some(*f),
            Value::BigInt(s) => s.parse().ok(),
            _ => None,
        }
    };
    let mut xs: Vec<f64> = a.values().filter_map(num).collect();
    let mut ys: Vec<f64> = b.values().filter_map(num).collect();
    xs.sort_by(|p, q| p.partial_cmp(q).unwrap_or(std::cmp::Ordering::Equal));
    ys.sort_by(|p, q| p.partial_cmp(q).unwrap_or(std::cmp::Ordering::Equal));
    xs.len() == ys.len() && xs.iter().zip(&ys).all(|(x, y)| crate::ledger::isclose(*x, *y, 1e-9, 1e-12))
}

fn metrics_dict(r: &Run) -> Dict {
    r.metrics.iter().map(|(k, v)| (k.clone(), Value::Float(*v))).collect()
}

fn at16(s: &str) -> String {
    s.chars().take(16).collect::<String>().replace('T', " ")
}

/// The numbers a watched command printed, as numbers, and the key what they stand for is left out under.
fn printed(e: &Dict) -> (Dict, String) {
    let nums: Dict = match e.get("metrics") {
        Some(Value::Dict(m)) => m
            .iter()
            .filter_map(|(k, v)| {
                let x = match v {
                    Value::Int(i) => *i as f64,
                    Value::Bool(b) => {
                        if *b {
                            1.0
                        } else {
                            0.0
                        }
                    }
                    Value::BigInt(s) => s.parse::<f64>().ok()?,
                    Value::Float(f) => *f,
                    _ => return None,
                };
                Some((k.clone(), Value::Float(x)))
            })
            .collect(),
        _ => Dict::new(),
    };
    let cmd = e.get("command").map(pyjson::py_str).unwrap_or_default();
    let keyv = crate::list![e.get("at").cloned().unwrap_or(Value::Null), cmd, Value::Dict(nums.clone())];
    let key = format!("cmd:{}", &sha256_bytes(pyjson::dumps_with(&keyv, &Dumps { sort_keys: true, ..Default::default() }).unwrap_or_default().as_bytes())[..16]);
    (nums, key)
}

/// What a key that leaves a found item out names, in plain words: a file's path, or the command whose printed numbers
/// it stands for.
pub fn left_out_what(root: &Path, key: &str) -> Option<String> {
    if let Some((path, _)) = key.strip_prefix("changed:").and_then(|k| k.rsplit_once('@')) {
        return Some(path.to_string());
    }
    if key.starts_with("cmd:") {
        return entries(root, None).iter().filter(|e| e.get_str("op") == Some("run")).find(|e| printed(e).1 == key).map(|e| format!("the numbers `{}` printed", e.get("command").map(pyjson::py_str).unwrap_or_default()));
    }
    let st = load_state(root);
    let files = st.get("files").and_then(|f| f.as_dict())?;
    files.iter().find(|(_, m)| m.get_str("sha") == Some(key)).map(|(p, _)| p.clone())
}

/// Whose view of what rb found: the user's screens leave out only what the user left out or recorded; a session's
/// briefing (rb context, rb_delta, and rb reconcile run in it) leaves out what that session left out or recorded too.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Seen {
    User,
    Briefing,
}

/// The session a briefing is for, by its writer's id and its session: None for the user's screens, and for the user.
fn briefed(led: &Ledger, seen: Seen) -> Option<(String, Option<String>)> {
    let me = if seen == Seen::Briefing { led.actor().ok().filter(|a| !a.is_human()) } else { None }?;
    let session = crate::actor::session_for(&me);
    Some((me.id, session))
}

/// The keys of the found items this view leaves out: the user's word, and in a briefing the session's own.
fn left_out(led: &Ledger, st: &Dict, seen: Seen) -> HashSet<String> {
    let Some(d) = st.get("dismissed").and_then(|v| v.as_dict()) else { return HashSet::new() };
    let me = briefed(led, seen);
    d.iter()
        .filter(|(_, v)| users_word(v) || me.as_ref().map_or(false, |(id, session)| v.get_str("by") == Some(id.as_str()) && v.get_str("session").map(str::to_string) == *session))
        .map(|(k, _)| k.clone())
        .collect()
}

/// Whether a line of the record is the user's own write: one in a human's name, or one an rb wrote before it kept who
/// wrote.
fn users_line(e: &Dict) -> bool {
    match e.get("actor") {
        None => true,
        Some(a) => a.as_str().map_or(false, |a| a.starts_with("human:")),
    }
}

/// (what can be recorded now, each with the exact rb run add; what exists but nothing declares), as the user's screens
/// count them.
pub fn found(led: &Ledger) -> Res<(Vec<Found>, Vec<String>)> {
    found_as(led, Seen::User)
}

/// The same, as `seen` counts them.
pub fn found_as(led: &Ledger, seen: Seen) -> Res<(Vec<Found>, Vec<String>)> {
    let st = load_state(&led.root);
    let all_entries = entries(&led.root, None);
    let (placed, unplaced, _, _) = found_in(led, &st, &all_entries, &left_out(led, &st, seen))?;
    Ok((placed, unplaced))
}

/// Everything rb found and nobody recorded: the runs it can place, what it cannot, and the files that changed that
/// nothing in the record cites.
pub struct FoundAll {
    pub placed: Vec<Found>,
    pub unplaced: Vec<String>,
    pub uncited: Vec<Uncited>,
    /// git listed more changed files than rb keeps: the list holds the first
    pub uncited_cut: bool,
    /// how many of the changed files are also among what rb cannot place
    pub uncited_also_found: usize,
    /// (HEAD the count starts at, the start this clone does not hold), where no commit here stands in for it
    pub uncited_from_head: Option<(String, String)>,
    /// what among the unplaced is a run that needs a per-run value, a file there before rb's first look, or one
    /// written while a watched command ran
    pub kinds: Kinds,
}

/// A run rb reconcile gives the line of, which needs a per-run value nothing gives: the file (or the command, for what
/// it printed), the experiment, and the values.
#[derive(Debug, Clone)]
pub struct Needs {
    pub what: String,
    pub experiment: String,
    pub names: Vec<String>,
}

impl Needs {
    pub fn to_value(&self) -> Value {
        crate::obj! {"what" => self.what.clone(), "experiment" => self.experiment.clone(), "names" => Value::list_of_str(self.names.clone())}
    }
}

/// How many of the unplaced are of each kind rb says apart.
#[derive(Debug, Clone, Default)]
pub struct Kinds {
    pub needs: Vec<Needs>,
    /// why each file rb cannot tell from the work before the record is not a new run
    pub older: Vec<Older>,
    pub while_ran: usize,
}

pub fn found_all(led: &Ledger, seen: Seen) -> Res<FoundAll> {
    let st = load_state(&led.root);
    let all_entries = entries(&led.root, None);
    let left = left_out(led, &st, seen);
    let (placed, unplaced, unplaced_paths, kinds) = found_in(led, &st, &all_entries, &left)?;
    let uncited = uncited_in(led, &st, &all_entries, &left, seen, &placed, &unplaced_paths)?;
    let uncited_cut = st.get("changes").and_then(|c| c.get("cut")).map_or(false, |c| c.truthy());
    let uncited_also_found = uncited.iter().filter(|u| unplaced_paths.contains(&u.path)).count();
    let changes = st.get("changes");
    let uncited_from_head = match (changes.and_then(|c| c.get_str("base")), changes.and_then(|c| c.get_str("base_for"))) {
        (Some(h), Some(gone)) if changes.and_then(|c| c.get("base_from_head")).map_or(false, |b| b.truthy()) => Some((h.to_string(), gone.to_string())),
        _ => None,
    };
    Ok(FoundAll { placed, unplaced, uncited, uncited_cut, uncited_also_found, uncited_from_head, kinds })
}

/// (what can be placed, what cannot, and the paths of the files among what cannot).
fn found_in(led: &Ledger, st: &Dict, all_entries: &[Dict], dismissed: &HashSet<String>) -> Res<(Vec<Found>, Vec<String>, Vec<String>, Kinds)> {
    let mut kinds = Kinds::default();
    let aliases = names(led)?;
    let recorded: Vec<Run> = led.all::<Run>()?.as_ref().clone();
    // a run's files and its config, which the run named as read
    let attached_shas: HashSet<String> = recorded
        .iter()
        .flat_map(|ev| ev.files.iter().map(|f| f.sha256.clone()).chain(ev.receipt.config.as_ref().and_then(|c| c.sha256.clone())))
        .filter(|s| !s.is_empty())
        .collect();
    let mut git_line: Option<String> = None;
    if let Some(g) = st.get("git").filter(|v| v.truthy()) {
        let commit = g.get("commit").filter(|v| v.truthy()).map(|c| pyjson::py_str(c).chars().take(10).collect::<String>()).unwrap_or_else(|| "none".into());
        git_line = Some(format!("seen at commit {commit}{}", if g.get("dirty").map_or(false, |v| v.truthy()) { ", tree dirty" } else { ", tree clean" }));
    }
    let mut commands: HashMap<String, String> = HashMap::new();
    // everything the watched run that wrote a file last wrote, while that is the file's last change
    let mut wrote_with: HashMap<String, Vec<String>> = HashMap::new();
    // a file that changed while a watched command ran, which nothing ties to that command: the command, as given
    let mut while_ran: HashMap<String, String> = HashMap::new();
    // the files a watched command that exited 0 wrote last
    let mut wrote_ok: HashSet<String> = HashSet::new();
    // the code a watched command ran at, for each file whose last change it wrote (a change seen after it is no one's)
    let mut code_of: HashMap<String, Value> = HashMap::new();
    let state_mt = |rel: &str| st.get("files").and_then(|f| f.get(rel)).and_then(|m| m.get("mt")).and_then(|v| v.as_i64());
    for e in all_entries {
        if e.get_str("op") == Some("file") {
            if let Some(rel) = e.get("path") {
                code_of.remove(&pyjson::py_str(rel));
                wrote_ok.remove(&pyjson::py_str(rel));
                commands.remove(&pyjson::py_str(rel));
                wrote_with.remove(&pyjson::py_str(rel));
                while_ran.remove(&pyjson::py_str(rel));
            }
        }
        if e.get_str("op") == Some("run") && e.get("wrote").map_or(false, |w| w.as_list().is_some()) {
            {
                let all: Vec<String> = wrote_of(e, &state_mt);
                let w: Vec<Value> = all.iter().map(|p| Value::Str(p.clone())).collect();
                let unnamed: HashSet<String> = match e.get("unnamed") {
                    Some(Value::List(u)) => u.iter().map(pyjson::py_str).collect(),
                    _ => HashSet::new(),
                };
                for rel in &w {
                    if unnamed.contains(&pyjson::py_str(rel)) {
                        commands.remove(&pyjson::py_str(rel));
                        wrote_ok.remove(&pyjson::py_str(rel));
                        code_of.remove(&pyjson::py_str(rel));
                        wrote_with.remove(&pyjson::py_str(rel));
                        while_ran.insert(pyjson::py_str(rel), e.get("command").map(pyjson::py_str).unwrap_or_default());
                        continue;
                    }
                    while_ran.remove(&pyjson::py_str(rel));
                    wrote_with.insert(pyjson::py_str(rel), all.clone());
                    if e.get("exit") == Some(&Value::Int(0)) {
                        wrote_ok.insert(pyjson::py_str(rel));
                    } else {
                        wrote_ok.remove(&pyjson::py_str(rel));
                    }
                    commands.insert(pyjson::py_str(rel), e.get("command").map(pyjson::py_str).unwrap_or_default());
                    if let Some(code) = e.get("code").filter(|v| v.truthy()) {
                        code_of.insert(pyjson::py_str(rel), code.clone());
                    }
                }
            }
        }
    }
    let live_exps = led.live::<Experiment>()?;
    let exp_of = |id: &str| live_exps.iter().find(|e| e.id == id);
    let mut placed: Vec<Found> = Vec::new();
    let mut unplaced: Vec<String> = Vec::new();
    let mut unplaced_paths: Vec<String> = Vec::new();
    // a file of numbers no metric names: (its path, the line), said unless a proposal passes it as its run's --config
    let mut configs_said: Vec<(String, String)> = Vec::new();
    let mut configs_passed: HashSet<String> = HashSet::new();
    let declared: Vec<(String, String)> = live_exps.iter().flat_map(|e| e.all_settings().into_iter().map(|(n, _)| (e.id.clone(), n)).collect::<Vec<_>>()).collect();
    // who reads a results folder: the experiment indexed on it, and every one whose recorded runs named a file in it
    let mut owners: indexmap::IndexMap<String, Vec<String>> = indexmap::IndexMap::new();
    for e in &live_exps {
        if !e.folder.is_empty() {
            owners.entry(e.folder.clone()).or_default().push(e.id.clone());
        }
    }
    let mut folders: Vec<String> = crate::repo::result_folders(&led.root).into_iter().chain(owners.keys().cloned()).collect::<BTreeSet<_>>().into_iter().collect();
    folders.sort_by(|a, b| b.len().cmp(&a.len()));
    let named = crate::repo::Records::run_files(led)?;
    for f in &folders {
        for e in crate::repo::readers_of(f, &named) {
            let readers = owners.entry(f.clone()).or_default();
            if !readers.contains(&e) {
                readers.push(e);
            }
        }
    }
    let crits = led.live::<Criterion>()?;
    let fresh = Fresh::of(st);
    let mut files: Vec<(String, Dict)> = st.get("files").and_then(|v| v.as_dict()).map(|d| d.iter().map(|(k, v)| (k.clone(), v.as_dict().cloned().unwrap_or_default())).collect()).unwrap_or_default();
    let at_of = |m: &Dict| m.get("at").map(pyjson::py_str).unwrap_or_default();
    files.sort_by(|a, b| at_of(&b.1).cmp(&at_of(&a.1)));
    let meta_of: HashMap<String, Dict> = files.iter().cloned().collect();
    // a results file: one whose numbers name a metric the project declares
    let results_file = |rel: &str| meta_of.get(rel).map_or(false, |m| numbers_seen(&led.root, rel, Some(m)).0.keys().any(|k| canon_of(k, &aliases).is_some()));
    let fresh_file = |rel: &str| meta_of.get(rel).map_or(false, |m| fresh.not(m).is_none());
    // why a file is not a new run: a file a watched command wrote is its own, unless it holds bytes rb saw at another
    // path first, or bytes git kept already that the command, git itself, put back: a rerun that makes the bytes a
    // commit kept, bit for bit, is the run a reproduction hopes for
    let by_git = |rel: &str| commands.get(rel).map_or(false, |c| is_git_command(c));
    let older = |rel: &str, meta: &Dict| fresh.not(meta).filter(|w| !wrote_ok.contains(rel) || matches!(w, Older::Copy(_)) || (matches!(w, Older::Stored) && by_git(rel)));
    // the folders a watched run wrote results files in: one run's output is one folder's
    let result_folders = |wrote: &[String]| wrote.iter().filter(|w| results_file(w)).map(|w| w.rsplit_once('/').map_or("", |(d, _)| d)).collect::<HashSet<&str>>().len();
    // rb run add for a file, with each per-run value its own config or its command gives, and the commit and the tree
    // its command ran at, as a printed number's proposal has them; and what a value needs that neither gives
    let from_file = |exp: &str, rel: &str| -> (Vec<String>, Fill) {
        let mut argv: Vec<String> = vec!["run".into(), "add".into(), exp.to_string(), "--from".into(), rel.to_string()];
        let writer = commands.get(rel).map(|c| {
            let wrote = wrote_with.get(rel).map(Vec::as_slice).unwrap_or(&[]);
            Writer { command: c.as_str(), wrote, results: result_folders(wrote), files: wrote.iter().filter(|w| results_file(w)).count() }
        });
        let fill = fill_per_run(led, exp_of(exp), &recorded, Some(rel), writer.as_ref(), &fresh_file);
        // a file of one arm's flat numbers names its arm where the run's config or its command's line states that
        // variant's own settings and no other's: recorded without it, its numbers would count toward nothing
        if let (Some(arm), Some(e)) = (fill.arm.as_ref(), exp_of(exp)) {
            if flat_numbers(e, &meta_of.get(rel).map(|m| numbers_seen(&led.root, rel, Some(m)).0).unwrap_or_default()) {
                argv.extend(["--variant".to_string(), arm.clone()]);
            }
        }
        argv.extend(fill.argv.iter().cloned());
        if let Some(code) = code_of.get(rel) {
            for k in ["commit", "tree"] {
                if let Some(v) = code.get(k).filter(|v| v.truthy()) {
                    argv.extend([format!("--{k}"), pyjson::py_str(v)]);
                }
            }
        }
        // the command rb watched write the file and exit 0, so the run reads it as observed, and a clone of the record can
        // run it again; a command that failed made no run to name, as a printed number's proposal needs exit 0 too
        if let Some(c) = commands.get(rel).filter(|_| wrote_ok.contains(rel)) {
            argv.extend(["--command".to_string(), c.clone()]);
        }
        (argv, fill)
    };
    for (rel, meta) in &files {
        let Some(sha) = meta.get("sha").filter(|v| v.truthy()).map(pyjson::py_str) else { continue };
        // a lockfile or a tool's config holds numbers that are no result
        if dismissed.contains(&sha) || attached_shas.contains(&sha) || tool_file(rel.rsplit('/').next().unwrap_or(rel)) {
            continue;
        }
        let (nums, running) = numbers_seen(&led.root, rel, Some(meta));
        if nums.is_empty() {
            continue;
        }
        let matched: Dict = nums.iter().filter(|(k, _)| canon_of(k, &aliases).is_some()).map(|(k, v)| (k.clone(), v.clone())).collect();
        let folder = folders.iter().find(|f| rel.starts_with(f.as_str())).cloned();
        let progress = nums.keys().all(|k| PROGRESS_KEYS.contains(&k.rsplit('.').next().unwrap_or("").to_lowercase().as_str()) && !matched.contains_key(k))
            || running.unwrap_or_else(|| says_running(&led.root.join(rel)));
        let mt = meta.get("mt").filter(|v| v.truthy()).and_then(|v| v.as_i64());
        let written = mt.and_then(|m| crate::pytime::from_float(m as f64 / 1e9)).map(|d| crate::pytime::iso_minutes(&d)).unwrap_or_default();
        // a results folder's numbers go only to an experiment that reads that folder; a file in one nothing reads yet,
        // which a watched command wrote or which rb saw land after its first look here, is placed as a loose file is,
        // where exactly one experiment's criteria read its numbers: one there before is work the start protocol records
        let readers: Vec<String> = folder.as_ref().and_then(|f| owners.get(f)).cloned().unwrap_or_default();
        if folder.is_some() && readers.is_empty() && !wrote_ok.contains(rel) {
            if fresh.not(meta).is_some() || matched.is_empty() || progress {
                continue;
            }
            let canon_names: HashSet<String> = matched.keys().filter_map(|k| canon_of(k, &aliases)).collect();
            let read_by_criteria = crits.iter().any(|c| canon_of(&c.metric, &aliases).map_or(false, |m| canon_names.contains(&m)));
            if !read_by_criteria || target(led, &canon_names, &aliases)?.0.is_none() {
                continue;
            }
        }
        if let Some(folder) = folder.filter(|_| !readers.is_empty()) {
            if readers.is_empty() || matched.is_empty() || progress {
                continue;
            }
            let mine: HashSet<Option<String>> = matched.keys().filter_map(|k| canon_of(k, &aliases)).map(Some).collect();
            let reading: Vec<&String> = readers
                .iter()
                .filter(|r| crits.iter().filter(|c| c.experiment == **r).map(|c| canon_of(&c.metric, &aliases)).any(|m| mine.contains(&m)))
                .collect();
            let owner = match reading.as_slice() {
                [] => continue,
                [one] => (*one).clone(),
                several => {
                    let names: BTreeSet<String> = mine.iter().flatten().cloned().collect();
                    unplaced.push(format!(
                        "{rel} reports {} and the target is ambiguous: {folder} is read by {} (rb run add <{}> --from {rel})",
                        names.into_iter().collect::<Vec<_>>().join(", "),
                        crate::assess::first_three(&several.iter().map(|s| s.to_string()).collect::<Vec<_>>(), ", "),
                        several.iter().take(4).map(|s| s.as_str()).collect::<Vec<_>>().join("|")
                    ));
                    unplaced_paths.push(rel.clone());
                    continue;
                }
            };
            let mut facts = vec![format!("{rel} · sha {} · last written {} UTC · in {folder}, which {owner} reads", sha.chars().take(12).collect::<String>(), written.replace('T', " "))];
            if let Some(c) = commands.get(rel) {
                facts.push(format!("written by: {c}"));
            }
            if !recorded.iter().any(|ev| ev.experiment == owner && Value::Dict(metrics_dict(ev)) == Value::Dict(nums.clone())) {
                let (argv, fill) = from_file(&owner, rel);
                // a file no watched command wrote is a new run only when rb saw it land after its first look here: one
                // there before is the work the record began after, which a run named nothing of
                if let Some(why) = older(rel, meta) {
                    unplaced.push(older_line(rel, &why, &format!(", in {folder}, which {owner} reads"), &argv, &fill.left()));
                    kinds.older.push(why);
                    unplaced_paths.push(rel.clone());
                    continue;
                }
                if let Some(c) = while_ran.get(rel) {
                    unplaced.push(while_line(rel, c, &argv, &fill.left()));
                    kinds.while_ran += 1;
                    unplaced_paths.push(rel.clone());
                    continue;
                }
                if let Some(line) = fill.unfinished(rel, &owner, &argv, commands.contains_key(rel)) {
                    unplaced.push(line);
                    kinds.needs.push(Needs { what: rel.clone(), experiment: owner.clone(), names: fill.left() });
                    unplaced_paths.push(rel.clone());
                    continue;
                }
                configs_passed.extend(config_of(&argv));
                placed.push(Found {
                    id: format!("j{}", sha.chars().take(10).collect::<String>()),
                    path: rel.clone(),
                    at: at_of(meta),
                    metrics: matched,
                    experiment: Some(owner.clone()),
                    key: sha.clone(),
                    options: Vec::new(),
                    argv,
                    facts,
                    here: led.root.clone(),
                    printed: false,
                });
            }
            continue;
        }
        if progress {
            let mut ks: Vec<&String> = nums.keys().collect();
            ks.sort();
            unplaced.push(format!(
                "{rel} holds only progress ({}): a heartbeat or a run still going, not a result",
                crate::assess::first_three(&ks.iter().map(|s| s.to_string()).collect::<Vec<_>>(), ", ")
            ));
            unplaced_paths.push(rel.clone());
            continue;
        }
        if matched.is_empty() {
            let mut ks: Vec<String> = nums.keys().cloned().collect();
            ks.sort();
            // a spec's settings, matching or not, are a run's config, not numbers nothing declares
            let leaf = |k: &str| crate::ledger::last_part(k).to_string();
            let (settings, rest): (Vec<String>, Vec<String>) = ks.into_iter().partition(|k| declared.iter().any(|(_, n)| n == k || leaf(n) == leaf(k)));
            if settings.is_empty() {
                configs_said.push((rel.clone(), format!("{rel} holds numbers nothing declares ({})", crate::assess::first_three(&rest, ", "))));
            } else {
                let mut by: Vec<String> = declared.iter().filter(|(_, n)| settings.iter().any(|k| n == k || leaf(n) == leaf(k))).map(|(e, _)| e.clone()).collect();
                by.dedup();
                let spec = if by.len() == 1 { format!("{}'s spec", by[0]) } else { format!("the specs of {}", crate::assess::first_three(&by, ", ")) };
                // a number under none of the keys a declared setting is under (top1 beside optim.lr and seed) may be a
                // result: a file that holds one is not said to be settings alone
                let parent = |k: &str| k.rsplit_once('.').map(|(p, _)| p.to_string());
                let parents: HashSet<String> = declared.iter().filter_map(|(_, n)| parent(n)).collect();
                let loose: Vec<&String> = rest.iter().filter(|k| !parent(k).map_or(false, |p| parents.contains(&p))).collect();
                configs_said.push((
                    rel.clone(),
                    if loose.is_empty() {
                        format!("{rel} holds settings {spec} declares ({}), not results: rb run add <experiment> --from <its results file> --config {rel} checks them against the spec", crate::assess::first_three(&settings, ", "))
                            + &if rest.is_empty() { String::new() } else { format!("; nothing declares its other numbers ({})", crate::assess::first_three(&rest, ", ")) }
                    } else {
                        format!("{rel} holds numbers nothing declares ({}) beside settings {spec} declares ({})", crate::assess::first_three(&rest, ", "), crate::assess::first_three(&settings, ", "))
                    },
                ));
            }
            continue;
        }
        let canon_names: HashSet<String> = matched.keys().filter_map(|k| canon_of(k, &aliases)).collect();
        let (tgt, options) = target(led, &canon_names, &aliases)?;
        if let Some(t) = &tgt {
            if recorded.iter().any(|ev| ev.experiment == *t && Value::Dict(metrics_dict(ev)) == Value::Dict(nums.clone())) {
                continue;
            }
        }
        let mut facts = vec![format!(
            "{rel} · sha {} · last written {} UTC · first seen {}",
            sha.chars().take(12).collect::<String>(),
            written.replace('T', " "),
            at16(&at_of(meta))
        )];
        if let Some(c) = commands.get(rel) {
            facts.push(format!("written by: {c}"));
        }
        if let Some(g) = &git_line {
            facts.push(g.clone());
        }
        let item = Found {
            id: format!("j{}", sha.chars().take(10).collect::<String>()),
            path: rel.clone(),
            at: at_of(meta),
            metrics: matched,
            experiment: tgt.clone(),
            key: sha.clone(),
            options: options.clone(),
            argv: Vec::new(),
            facts,
            here: led.root.clone(),
            printed: false,
        };
        if let Some(t) = &tgt {
            let (argv, fill) = from_file(t, rel);
            // as in a folder a run read: a file no watched command wrote is a new run only once rb saw it land
            if let Some(why) = older(rel, meta) {
                unplaced.push(older_line(rel, &why, "", &argv, &fill.left()));
                kinds.older.push(why);
                unplaced_paths.push(rel.clone());
                continue;
            }
            if let Some(c) = while_ran.get(rel) {
                unplaced.push(while_line(rel, c, &argv, &fill.left()));
                kinds.while_ran += 1;
                unplaced_paths.push(rel.clone());
                continue;
            }
            if let Some(line) = fill.unfinished(rel, t, &argv, commands.contains_key(rel)) {
                unplaced.push(line);
                kinds.needs.push(Needs { what: rel.clone(), experiment: t.clone(), names: fill.left() });
                unplaced_paths.push(rel.clone());
                continue;
            }
            configs_passed.extend(config_of(&argv));
            placed.push(Found { argv, ..item });
        } else {
            let names: BTreeSet<&String> = canon_names.iter().collect();
            unplaced_paths.push(rel.clone());
            unplaced.push(format!(
                "{rel} reports {} and the target is ambiguous{}",
                names.into_iter().cloned().collect::<Vec<_>>().join(", "),
                if options.is_empty() { String::new() } else { format!(" (rb run add <{}> --from {rel})", options.iter().take(4).cloned().collect::<Vec<_>>().join("|")) }
            ));
        }
    }
    for (rel, line) in configs_said {
        if !configs_passed.contains(&rel) {
            unplaced.push(line);
            unplaced_paths.push(rel);
        }
    }
    let watched: HashSet<&str> = files.iter().map(|(rel, _)| rel.as_str()).collect();
    let watched_meta: HashMap<&str, &Dict> = files.iter().map(|(rel, m)| (rel.as_str(), m)).collect();
    let mut sources_: Vec<(Option<String>, Vec<Dict>)> = vec![(None, all_entries.to_vec())];
    sources_.extend(orphans(&led.root).into_iter().map(|(n, rows)| (Some(n), rows)));
    for (gone, rows) in &sources_ {
    // never the worktree's name, which can name the agent that worked there, as a branch's can
    let where_ = if gone.is_some() { " in a worktree removed since" } else { "" };
    for e in rows {
        if e.get_str("op") != Some("run") || e.get("exit").map_or(true, |v| *v != Value::Int(0)) {
            continue;
        }
        let Some(Value::Dict(m)) = e.get("metrics") else { continue };
        if m.is_empty() {
            continue;
        }
        let (nums, key) = printed(e);
        let cmd = e.get("command").map(pyjson::py_str).unwrap_or_default();
        if nums.is_empty() || dismissed.contains(&key) {
            continue;
        }
        if recorded.iter().any(|ev| same_values(&metrics_dict(ev), &nums) && ev.receipt.command.as_deref().map_or(false, |c| same_command(&cmd, c))) {
            continue;
        }
        // a file it wrote stands for it only where rb proposes that file on its own: one the scan watches
        let ran_wrote: Vec<String> = wrote_of(e, &|p: &str| watched_meta.get(p).and_then(|m| m.get("mt")).and_then(|v| v.as_i64()));
        let wrote: Vec<String> = if gone.is_none() { ran_wrote.iter().filter(|w| watched.contains(w.as_str()) && led.root.join(w).is_file()).cloned().collect() } else { Vec::new() };
        if wrote.iter().any(|w| same_values(&numbers_seen(&led.root, w, watched_meta.get(w.as_str()).copied()).0, &nums)) {
            continue;
        }
        let matched: Dict = nums.iter().filter(|(k, _)| canon_of(k, &aliases).is_some()).map(|(k, v)| (k.clone(), v.clone())).collect();
        if matched.is_empty() {
            let mut ks: Vec<&String> = nums.keys().collect();
            ks.sort();
            unplaced.push(format!("`{cmd}`{where_} printed numbers nothing declares ({})", crate::assess::first_three(&ks.iter().map(|s| s.to_string()).collect::<Vec<_>>(), ", ")));
            continue;
        }
        let canon_names: HashSet<String> = matched.keys().filter_map(|k| canon_of(k, &aliases)).collect();
        let (tgt, options) = target(led, &canon_names, &aliases)?;
        if let Some(t) = &tgt {
            if recorded.iter().any(|ev| ev.experiment == *t && same_values(&metrics_dict(ev), &nums)) {
                continue;
            }
        }
        let at = e.get("at").map(pyjson::py_str).unwrap_or_default();
        let ran = e.get("code").filter(|v| v.truthy()).cloned().unwrap_or(Value::empty_dict());
        let mut argv: Vec<String> = Vec::new();
        if let Some(t) = &tgt {
            argv = vec!["run".into(), "add".into(), t.clone()];
            let mut sorted: Vec<(&String, &Value)> = nums.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(b.0));
            for (k, v) in sorted {
                argv.push(format!("{k}={}", pyjson::py_repr(v)));
            }
            argv.extend(["--command".to_string(), cmd.clone(), "--produced-at".to_string(), at.clone()]);
            let writer = Writer { command: cmd.as_str(), wrote: &ran_wrote, results: result_folders(&ran_wrote), files: ran_wrote.iter().filter(|w| results_file(w)).count() };
            let fill = fill_per_run(led, exp_of(t), &recorded, None, Some(&writer), &|_: &str| false);
            argv.extend(fill.argv.iter().cloned());
            if let Some(c) = ran.get("commit").filter(|v| v.truthy()) {
                argv.extend(["--commit".to_string(), pyjson::py_str(c)]);
            }
            if let Some(t) = ran.get("tree").filter(|v| v.truthy()) {
                argv.extend(["--tree".to_string(), pyjson::py_str(t)]);
            }
            if !fill.missing.is_empty() {
                unplaced.push(needs_line(&format!("What `{cmd}`{where_} printed"), t, &argv, &fill.missing, Gave::Printed(fill.several)));
                kinds.needs.push(Needs { what: cmd.clone(), experiment: t.clone(), names: fill.missing.clone() });
                continue;
            }
        }
        let mut facts = vec![format!("printed by `{cmd}`{where_} · exit 0 · {}", at16(&at))];
        if let Some(g) = git_line.as_ref().filter(|_| gone.is_none()) {
            facts.push(g.clone());
        }
        // recorded where it ran: the nested worktree its line names, while that is still one of this record's
        let here = match e.get("worktree").and_then(|w| w.get("path")).and_then(|p| p.as_str()) {
            Some(p) if gone.is_none() => crate::ledger::checkout_of(Path::new(p), &led.root),
            _ => led.root.clone(),
        };
        let item = Found {
            id: format!("c{}", &key[4..14]),
            path: cmd.clone(),
            at: at.clone(),
            metrics: matched,
            experiment: tgt.clone(),
            key: key.clone(),
            options: options.clone(),
            argv,
            facts,
            here,
            printed: true,
        };
        if tgt.is_some() {
            placed.push(item);
        } else {
            let names: BTreeSet<&String> = canon_names.iter().collect();
            unplaced.push(format!(
                "`{cmd}`{where_} printed {} and the target is ambiguous{}",
                names.into_iter().cloned().collect::<Vec<_>>().join(", "),
                if options.is_empty() { String::new() } else { format!(" (record it on one of {})", options.iter().take(4).cloned().collect::<Vec<_>>().join(", ")) }
            ));
        }
    }
    }
    Ok((placed, unplaced, unplaced_paths, kinds))
}

pub fn find(led: &Ledger, item_id: &str) -> Res<Option<Found>> {
    Ok(found(led)?.0.into_iter().find(|it| it.id == item_id))
}

// ---------------------------------------------------------------- what changed that nothing in the record cites

/// Folders whose files are a dependency's or a build tool's, never the research's.
const DEPENDENCY_DIRS: &[&str] = &[
    "node_modules", "vendor", "vendored", "third_party", "third-party", "site-packages", "dist-packages", "venv", "virtualenv", "bower_components",
    "jspm_packages", "__pypackages__", "__pycache__", "rb-export", "_build", "htmlcov",
];
/// A build tool's output folders, at the repository's top only: further down the same names can hold research files
/// (data/target/, analysis/dist/).
const BUILD_DIRS: &[&str] = &["build", "dist", "target"];
/// Continuous integration, editor, type-checker, lint, spell, docs-site and test-runner configs outside a dot-folder,
/// and test-coverage reports.
const TOOL_CONFIGS: &[&str] = &[
    "azure-pipelines.yml", "azure-pipelines.yaml", "bitbucket-pipelines.yml", "appveyor.yml", "appveyor.yaml", "codecov.yml", "codecov.yaml",
    "bitrise.yml", "bitrise.yaml", "codemagic.yaml", "codemagic.yml", "wercker.yml", "wercker.yaml", "screwdriver.yaml", "shippable.yml",
    "codeship-services.yml", "codeship-steps.yml", "_config.yml", "_config.yaml", "coverage.json", "coverage-final.json", "coverage-summary.json",
    "cloudbuild.yaml", "cloudbuild.yml", "buildspec.yml", "renovate.json", "netlify.toml", "vercel.json", "readthedocs.yml", "readthedocs.yaml",
    "jsconfig.json", "pyrightconfig.json", "biome.json", "ruff.toml", "rustfmt.toml", "clippy.toml", "taplo.toml", "rust-toolchain.toml",
    "mypy.toml", "pytest.toml", "tox.toml", "cspell.json", "cspell.yaml", "cspell.yml", "cspell.config.yaml", "jest.config.json", "deno.json",
    "dprint.json", "typos.toml", "_typos.toml", "lefthook.yml", "lefthook.yaml", "mkdocs.yml", "mkdocs.yaml", "eslint.config.json",
    "prettier.config.json", "stylelint.config.json", "babel.config.json", "commitlint.config.json", "markdownlint.json", "lerna.json", "nx.json",
    "turbo.json", "deny.toml",
];

/// Whether a path is one rb leaves out of what changed: its own files (in .rb/ or another dot-folder, .mcp.json, and
/// the page and export it writes beside .rb/ by default), anything else in a dot-folder or named with a dot, a
/// lockfile, a CI, editor or tool config, a CI pipeline (YAML in a folder named ci), and what sits in a vendored or
/// dependency folder, or in a build folder at the top.
pub fn left_alone(path: &str) -> bool {
    let parts: Vec<&str> = path.split('/').filter(|p| !p.is_empty()).collect();
    let Some((name, dirs)) = parts.split_last() else { return true };
    if parts.iter().any(|p| p.starts_with('.')) || dirs.iter().any(|d| DEPENDENCY_DIRS.contains(&d.to_lowercase().as_str())) {
        return true;
    }
    if dirs.first().map_or(false, |d| BUILD_DIRS.contains(&d.to_lowercase().as_str())) {
        return true;
    }
    if dirs.is_empty() && ["rb-page.html", "rb-export.zip"].contains(name) {
        return true;
    }
    // a JSON file in a folder named ci can be a result (a confidence interval); a YAML one is a pipeline
    if dirs.iter().any(|d| d.eq_ignore_ascii_case("ci")) && sources::structured_kind(name) == Some("yaml") {
        return true;
    }
    tool_file(name)
}

/// Whether a file's name makes it a lockfile or a tool's config. A lockfile goes by its name's own word for it
/// (package-lock.json, pnpm-lock.yaml, conda-lock.yml, uv.lock), never by a name that only ends in the letters
/// (resblock.yaml, clock.json).
pub fn tool_file(name: &str) -> bool {
    let name = name.to_lowercase();
    let (stem, ext) = name.rsplit_once('.').unwrap_or((name.as_str(), ""));
    let lock = ext == "lock" || stem == "lock" || [".lock", "-lock", "_lock"].iter().any(|w| stem.ends_with(w)) || stem.ends_with("shrinkwrap");
    lock || TOOL_CONFIGS.contains(&name.as_str()) || (name.starts_with("tsconfig.") && name.ends_with(".json"))
}

/// A .json, .yaml or .toml file outside the results folders rb reads, which nothing in the record cites (no setting's
/// source, run, analysis, resource, criterion, call, indexed folder or manifest of one), and which differs from what it
/// was where the count starts: the record's first write, or the last rb reconcile --apply that recorded by path what
/// it saw.
#[derive(Debug, Clone, Default)]
pub struct Uncited {
    pub path: String,
    /// the commit the change counts from; empty outside git, and before the repository's first commit
    pub since: String,
    /// when some of the change is in a commit, not only in the working tree
    pub committed: bool,
    /// the file is new since then
    pub new: bool,
    /// the file is gone since then
    pub gone: bool,
    /// when rb first saw the change
    pub at: String,
    /// what leaving it out or recording it is filed under: its path and its bytes, so a later change shows again
    pub key: String,
    /// the start of its bytes' sha256 ("gone" for a file gone)
    pub mark: String,
    /// in a git repository
    pub git: bool,
    /// where the change counts from, in words, where no commit names it
    pub start: &'static str,
}

impl Uncited {
    /// In rb's words: what changed since when, and that nothing in the record cites it.
    pub fn said(&self) -> String {
        let what = if self.gone {
            "gone"
        } else if self.new {
            "new"
        } else {
            "changed"
        };
        let since = if self.since.is_empty() { self.start.to_string() } else { self.since.chars().take(7).collect() };
        format!("{}: {what} since {since}{}; nothing in the record cites it", self.path, if self.git && !self.committed { ", not committed" } else { "" })
    }

    pub fn to_value(&self) -> Value {
        crate::obj! {
            "path" => self.path.clone(), "since" => Value::opt_str(Some(self.since.clone()).filter(|s| !s.is_empty())), "committed" => self.committed,
            "new" => self.new, "gone" => self.gone, "at" => self.at.clone(), "key" => self.key.clone(), "said" => self.said()
        }
    }
}

/// Where what changed starts to count: the later of the record's first write and the last rb reconcile --apply that
/// recorded by path all it saw, with the commit that apply saw. An apply whose list was cut, or one by an rb that kept
/// no paths, leaves the start where it was.
#[derive(Debug, Clone, Default)]
pub struct Baseline {
    pub at: Option<String>,
    pub commit: Option<String>,
    pub applied: bool,
    /// at the record's start, the commit rb init saw ("" where there was none), where its line keeps one
    pub began_on: Option<String>,
}

/// Where the user's count starts: only an apply in the user's own name moves it, as only their word leaves an item out.
pub fn baseline(led: &Ledger) -> Res<Baseline> {
    for e in led.log_entries()?.iter().rev() {
        if e.get_str("op") != Some("reconciled") || !users_line(e) {
            continue;
        }
        let Some(seen) = e.get("detail").and_then(|d| d.get("uncited_seen")) else { continue };
        let whole = match seen {
            Value::Dict(d) => !d.get("cut").map_or(false, |c| c.truthy()),
            Value::List(_) => true,
            _ => false,
        };
        if whole {
            let commit = seen.get("commit").and_then(|c| c.as_str()).filter(|c| !c.is_empty()).map(str::to_string);
            return Ok(Baseline { at: e.get_str("at").map(str::to_string), commit, applied: true, began_on: None });
        }
    }
    let first = led.first_write()?;
    let detail = first.as_ref().and_then(|w| w.get("detail"));
    let began_on = match first.as_ref().and_then(|w| w.get_str("op")) {
        Some("init") => detail.and_then(|d| d.get_str("head")).map(str::to_string),
        // a clone that got the code counts from the commit that put its record in place of the parent's, which git
        // dates after the record's first write (seconds after, on a slow machine)
        Some("clone") => detail.and_then(|d| d.get("code")).filter(|c| c.get("got").map_or(false, Value::truthy)).and_then(|c| c.get_str("commit")).and_then(|c| record_commit(&led.root, c)),
        _ => None,
    };
    Ok(Baseline { at: led.began()?, commit: None, applied: false, began_on })
}

/// The first commit after `code` on HEAD's line: a clone's commit of its record, where git made it.
fn record_commit(root: &Path, code: &str) -> Option<String> {
    let range = format!("{code}..HEAD");
    git(root, &["rev-list", "--ancestry-path", "--topo-order", "--reverse", &range]).and_then(|out| out.lines().next().map(|l| l.trim().to_string())).filter(|c| !c.is_empty())
}

/// The keys of the changed files an rb reconcile --apply recorded by path, each while its bytes are those: the user's
/// applies, and in a briefing the session's own too. A session's apply keeps the paths on the record, and leaves them
/// on the user's count, as a session's dismissal does.
fn recorded_keys(led: &Ledger, seen: Seen) -> Res<HashSet<String>> {
    let me = briefed(led, seen);
    let mine = |e: &Dict| {
        me.as_ref().map_or(false, |(id, session)| e.get_str("actor") == Some(id.as_str()) && e.get_str("session").map(str::to_string) == *session)
    };
    let mut out = HashSet::new();
    for e in led.log_entries()?.iter() {
        if e.get_str("op") != Some("reconciled") || !(users_line(e) || mine(e)) {
            continue;
        }
        let files: Option<&Vec<Value>> = match e.get("detail").and_then(|d| d.get("uncited_seen")) {
            Some(Value::Dict(d)) => d.get("files").and_then(|f| f.as_list()),
            Some(Value::List(l)) => Some(l),
            _ => None,
        };
        for f in files.into_iter().flatten() {
            if let Some(k) = f.get_str("key") {
                out.insert(k.to_string());
            } else if let (Some(p), Some(m)) = (f.get_str("path"), f.get_str("sha")) {
                out.insert(format!("changed:{p}@{m}"));
            }
        }
    }
    Ok(out)
}

/// The .json, .yaml and .toml files git is asked about, wherever they are.
const STRUCTURED: &[&str] = &[":(top,glob,icase)**/*.json", ":(top,glob,icase)**/*.yaml", ":(top,glob,icase)**/*.yml", ":(top,glob,icase)**/*.toml"];
/// The tree of no files, which git knows without holding it: a change before the first commit counts from it.
const EMPTY_TREE_SHA1: &str = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";
const EMPTY_TREE_SHA256: &str = "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321";

fn structured(p: &str) -> bool {
    sources::structured_kind(p).is_some() && !left_alone(p)
}

/// A file's bytes, marked: their sha256, or for a file too big to read, its size and time.
fn mark_of(full: &Path, sz: u64, mt: i64) -> String {
    let hashed = if sz <= MAX_PARSE_BYTES { std::fs::read(full).ok().map(|b| sha256_bytes(&b)) } else { None };
    hashed.unwrap_or_else(|| format!("{sz}-{mt}"))
}

/// Where a change counts from: the commit, the one it stands in for where this clone does not hold the commit the
/// last rb reconcile --apply saw, and whether no commit at or before that apply was found here either.
#[derive(Debug, Clone, Default)]
struct Start {
    from: Option<String>,
    stands_for: Option<String>,
    from_head: bool,
}

/// The newest commit in HEAD's history at or before a time rb wrote.
fn commit_before(root: &Path, at: Option<&str>) -> Option<String> {
    let e = at.and_then(crate::pytime::parse_iso).map(|d| d.timestamp())?;
    let before = format!("--before=@{e}");
    git(root, &["rev-list", "-1", &before, "HEAD"]).map(|c| strip(&c).to_string()).filter(|c| !c.is_empty())
}

/// The commit a change counts from, and whether a later look may keep it: the one the last rb reconcile --apply saw
/// (or what stood in for it on an earlier look, where this clone does not hold it), else the newest commit at or
/// before the record's first write, else the first commit rb sees here.
fn base_commit(root: &Path, base: &Baseline, prev: Option<&Dict>, head: Option<&str>) -> (Start, bool) {
    let same = prev.filter(|p| p.get_str("base_at") == base.at.as_deref());
    if let Some(c) = &base.commit {
        if let Some(p) = same.filter(|p| p.get_str("base_for") == Some(c.as_str())) {
            let from_head = p.get("base_from_head").map_or(false, |b| b.truthy());
            return (Start { from: p.get_str("base").map(str::to_string), stands_for: Some(c.clone()), from_head }, true);
        }
        return (Start { from: Some(c.clone()), ..Default::default() }, true);
    }
    if let Some(kept) = same.and_then(|p| p.get_str("base")) {
        return (Start { from: Some(kept.to_string()), ..Default::default() }, true);
    }
    let Some(head) = head else { return (Start::default(), false) };
    // git dates a commit to the second: one made just after rb init can share its second, so the commit rb init saw
    // goes first, where HEAD's history holds it
    let from = match base.began_on.as_deref() {
        Some("") => head.to_string(),
        Some(c) if git(root, &["merge-base", "--is-ancestor", c, head]).is_some() => c.to_string(),
        _ => commit_before(root, base.at.as_deref()).unwrap_or_else(|| head.to_string()),
    };
    (Start { from: Some(from), ..Default::default() }, true)
}

/// The .json, .yaml and .toml files that differ now from the commit a change counts from, tracked or not, each with
/// its change, whether any of it is committed, and its bytes' mark: what a read filters by what the record cites. A
/// file of these that changes between two looks is journaled, as a file of the walk is.
fn changes_look(led: &Ledger, g: &GitNow, prev: Option<&Dict>, journaled: &HashSet<String>, who: &str, summary: &mut Dict, wrote: &mut Vec<String>) -> Res<(Dict, bool)> {
    let root = &led.root;
    let base = baseline(led)?;
    let (mut start, keep) = base_commit(root, &base, prev, g.head.as_deref());
    let roots: Vec<String> = watch_roots(led).iter().filter_map(|r| r.strip_prefix(root).ok().map(|p| format!("{}/", crate::sys::slashed(p)))).collect();
    let under_root = |p: &str| roots.iter().any(|r| p.starts_with(r.as_str()) || r.starts_with(p));
    let mut map: std::collections::BTreeMap<String, Dict> = std::collections::BTreeMap::new();
    // what the commits since then changed
    if let Some(head) = g.head.as_deref() {
        let empty = if head.len() == 64 { EMPTY_TREE_SHA256 } else { EMPTY_TREE_SHA1 };
        let committed = |from: &str| -> Option<Vec<(String, &'static str)>> {
            if from == head {
                return Some(Vec::new());
            }
            let mut args: Vec<&str> = vec!["diff-tree", "-r", "-z", "--name-status", "--no-renames", from, head, "--"];
            args.extend(STRUCTURED);
            sources::git_bytes(root, &args).map(|out| name_status(&out))
        };
        let changed = match committed(start.from.as_deref().unwrap_or(empty)) {
            Some(c) => c,
            // a start this clone does not hold (a squash merge, a rewrite): the newest commit here at or before when it
            // was seen stands in for it, else HEAD, which the count then says
            None => {
                let stand = commit_before(root, base.at.as_deref());
                let gone = start.from.take().unwrap_or_default();
                start = Start { from: Some(stand.clone().unwrap_or_else(|| head.to_string())), stands_for: Some(gone), from_head: stand.is_none() };
                stand.and_then(|s| committed(&s)).unwrap_or_default()
            }
        };
        for (p, c) in changed.into_iter().filter(|(p, _)| structured(p)) {
            map.insert(p, crate::dict! {"change" => c, "committed" => true});
        }
    }
    // what the working tree changes on top of HEAD, which holds the start's bytes where no commit since changed them
    for (p, c) in g.tree.iter().filter(|(p, _)| structured(p)) {
        let was = map.get(p).and_then(|m| m.get_str("change")).map(str::to_string);
        match (was.as_deref(), *c) {
            (Some("added"), "deleted") => {
                map.remove(p);
            }
            (Some(_), "deleted") => {
                map.insert(p.clone(), crate::dict! {"change" => "deleted", "committed" => true});
            }
            (Some(_), _) => {}
            (None, c) => {
                map.insert(p.clone(), crate::dict! {"change" => c, "committed" => false});
            }
        }
    }
    // what git lists untracked outside the folders the walk reads, a folder wholly untracked asked for its files
    let mut untracked: Vec<String> = Vec::new();
    let mut dirs: Vec<String> = Vec::new();
    for u in &g.untracked {
        if u.ends_with('/') {
            if !left_alone(&format!("{u}x")) && !roots.iter().any(|r| r.starts_with(u.as_str()) || u.starts_with(r.as_str())) {
                dirs.push(format!(":(top,literal){u}"));
            }
        } else {
            untracked.push(u.clone());
        }
    }
    if !dirs.is_empty() {
        let mut args: Vec<&str> = vec!["ls-files", "-z", "--others", "--exclude-standard", "--"];
        args.extend(dirs.iter().map(String::as_str));
        if let Some(out) = sources::git_bytes(root, &args) {
            untracked.extend(out.split(|b| *b == 0).filter(|p| !p.is_empty()).map(|p| String::from_utf8_lossy(p).into_owned()));
        }
    }
    for p in untracked {
        if structured(&p) && !under_root(&p) && !map.contains_key(&p) {
            map.insert(p, crate::dict! {"change" => "untracked", "committed" => false});
        }
    }
    let cut = map.len() > MAX_FILES;
    let prev_files = prev.and_then(|p| p.get("files")).and_then(|f| f.as_dict());
    // untracked files gone at an earlier look (stashed, moved out), each with what rb first saw of it
    let prev_away = prev.and_then(|p| p.get(AWAY)).and_then(|f| f.as_dict());
    // this rb's first look here: what it sees untracked was there before, as far as rb can tell
    let first_look = prev_files.is_none();
    let at = now();
    let mut files = Dict::new();
    for (p, mut m) in map.into_iter().take(MAX_FILES) {
        let full = root.join(&p);
        let change = m.get_str("change").unwrap_or("").to_string();
        let md = std::fs::metadata(&full).ok().filter(|md| md.is_file());
        if change != "deleted" && md.is_none() {
            continue;
        }
        let (mt, sz, ct) = md.as_ref().map_or((0, 0, 0), |md| (mtime_ns(md), md.len() as i64, crate::sys::ctime_ns(&full, md) as i64));
        let was = prev_files.and_then(|f| f.get(&p)).and_then(|v| v.as_dict());
        let int = |w: &Dict, k: &str| w.get(k).and_then(|v| v.as_i64());
        // the same bytes as at the last look, as far as the file's times and size tell
        let same_bytes = was.map_or(false, |w| (int(w, "mt"), int(w, "sz"), int(w, "ct")) == (Some(mt), Some(sz), Some(ct)) && (change == "deleted") == (w.get_str("sha") == Some("gone")));
        let sha = match (change.as_str(), same_bytes) {
            ("deleted", _) => "gone".to_string(),
            (_, true) => was.and_then(|w| w.get_str("sha")).unwrap_or("").to_string(),
            _ => mark_of(&full, sz as u64, mt),
        };
        let tree = change == "untracked" || g.tree.binary_search_by(|(q, _)| q.as_str().cmp(p.as_str())).is_ok();
        m.insert("mt".into(), Value::Int(mt));
        m.insert("sz".into(), Value::Int(sz));
        m.insert("ct".into(), Value::Int(ct));
        m.insert("sha".into(), Value::Str(sha.clone()));
        m.insert("tree".into(), Value::Bool(tree));
        m.insert("at".into(), Value::Str(was.and_then(|w| w.get_str("at")).unwrap_or(&at).to_string()));
        if change == "untracked" {
            // one back at a path rb saw it go from keeps what rb first saw there: its bytes then, or a change since
            let back = prev_away.and_then(|a| a.get(&p)).and_then(|v| v.as_dict());
            match was.filter(|w| w.get_str("change") == Some("untracked")).or(back) {
                Some(w) => {
                    for k in ["first", "sha0", "mt0", "early"] {
                        if let Some(v) = w.get(k) {
                            m.insert(k.into(), v.clone());
                        }
                    }
                }
                None => {
                    m.insert("first".into(), Value::Str(at.clone()));
                    m.insert("sha0".into(), Value::Str(sha.clone()));
                    m.insert("mt0".into(), Value::Int(mt));
                    m.insert("early".into(), Value::Bool(first_look));
                }
            }
        }
        // journaled when the working tree changes it between two looks (a commit is the git entry's to say), but where
        // the walk journals it itself
        let was_tree = was.map_or(false, |w| w.get("tree").map_or(false, |t| t.truthy()));
        let moved = !was_tree || was.and_then(|w| w.get_str("sha")) != Some(sha.as_str());
        if !first_look && tree && moved && !journaled.contains(&p) {
            let what = if change == "deleted" {
                "gone"
            } else if !was_tree && matches!(change.as_str(), "untracked" | "added") {
                "new"
            } else {
                "changed"
            };
            let mut entry = crate::dict! {"op" => "file", "change" => what, "path" => p.clone()};
            if change != "deleted" {
                let written = crate::pytime::from_float(mt as f64 / 1e9).map(|d| crate::pytime::iso_ms(&d)).unwrap_or_default();
                entry.insert("bytes".into(), Value::Int(sz));
                entry.insert("written".into(), Value::Str(written));
                if sha.len() == 64 {
                    entry.insert("sha256".into(), Value::Str(sha.clone()));
                }
            }
            entry.insert("git".into(), Value::Str(change.clone()));
            entry.insert("by".into(), Value::Str(who.to_string()));
            append(root, entry);
            if what != "gone" {
                wrote.push(p.clone());
            }
            let n = summary.get(what).and_then(|v| v.as_i64()).unwrap_or(0);
            summary.insert(what.into(), Value::Int(n + 1));
        }
        files.insert(p, Value::Dict(m));
    }
    // an untracked file gone since the last look, kept with what rb first saw of it; the latest MAX_FILES are kept
    let mut away: Dict = prev_away.into_iter().flatten().filter(|(p, _)| !files.contains_key(*p)).map(|(p, w)| (p.clone(), w.clone())).collect();
    for (p, w) in prev_files.into_iter().flatten() {
        if !files.contains_key(p) && w.get_str("change") == Some("untracked") && !root.join(p).exists() {
            if !journaled.contains(p) {
                append(root, crate::dict! {"op" => "file", "change" => "gone", "path" => p.clone(), "git" => "untracked", "by" => who.to_string()});
                let n = summary.get("gone").and_then(|v| v.as_i64()).unwrap_or(0);
                summary.insert("gone".into(), Value::Int(n + 1));
            }
            let mut kept = Dict::new();
            for k in ["first", "sha0", "mt0", "early"] {
                if let Some(v) = w.get(k) {
                    kept.insert(k.into(), v.clone());
                }
            }
            away.shift_remove(p);
            away.insert(p.clone(), Value::Dict(kept));
        }
    }
    let over = away.len().saturating_sub(MAX_FILES);
    away.drain(..over);
    let mut out = crate::dict! {"files" => Value::Dict(files), "cut" => cut, "head" => g.head.clone()};
    if !away.is_empty() {
        out.insert(AWAY.into(), Value::Dict(away));
    }
    if keep {
        out.insert("base".into(), Value::opt_str(start.from));
        out.insert("base_at".into(), Value::opt_str(base.at));
        if let Some(c) = start.stands_for {
            out.insert("base_for".into(), Value::Str(c));
            out.insert("base_from_head".into(), Value::Bool(start.from_head));
        }
    }
    Ok((out, cut))
}

/// Seconds since the epoch of a time as rb writes times.
fn secs_of(at: &str) -> Option<f64> {
    crate::pytime::parse_iso(at).map(|d| d.timestamp_millis() as f64 / 1000.0)
}

/// The path a string of the record names, when it names one: a source's file before its `#key` or `:line`.
fn path_part(s: &str) -> &str {
    let s = s.split('#').next().unwrap_or(s);
    let s = match s.rsplit_once(':') {
        Some((head, tail)) if !tail.is_empty() && tail.chars().all(|c| c.is_ascii_digit() || c == '-') => head,
        _ => s,
    };
    s.trim_start_matches("./")
}

/// Every path the record names outside its notes (a setting's source, a run's files, rows and inputs, an analysis's,
/// a criterion's source, a call's document, a resource's location, an indexed folder's files), and every source a
/// manifest of an indexed folder hashes.
fn cited_paths(led: &Ledger) -> Res<HashSet<String>> {
    let mut out: HashSet<String> = HashSet::new();
    fn strings(v: &Value, out: &mut HashSet<String>, root: &str) {
        match v {
            Value::Str(s) => {
                let s = s.replace('\\', "/");
                let p = path_part(&s);
                let p = p.strip_prefix(root).map(|r| r.trim_start_matches('/')).unwrap_or(p);
                if !p.is_empty() && p.len() < 1024 {
                    out.insert(p.to_string());
                }
            }
            Value::List(l) => l.iter().for_each(|x| strings(x, out, root)),
            Value::Dict(d) => d.values().for_each(|x| strings(x, out, root)),
            _ => {}
        }
    }
    let root = crate::sys::slashed(&led.root);
    for (kind, path) in led.record_files() {
        if kind == "note" {
            continue;
        }
        if let Ok(doc) = led.doc(&path) {
            strings(&doc.data, &mut out, &root);
        }
    }
    for e in led.live::<Experiment>()? {
        for f in e.files.iter().filter(|f| f.role == "manifest") {
            let full = led.root.join(&f.path);
            let base = Path::new(&f.path).parent().map(|p| p.to_string_lossy().replace('\\', "/")).unwrap_or_default();
            for src in crate::repo::manifest_sources(crate::repo::json_file(&full).as_ref()).unwrap_or_default().into_keys() {
                let src = src.replace('\\', "/");
                out.insert(src.trim_start_matches("./").to_string());
                if !base.is_empty() {
                    out.insert(format!("{base}/{}", src.trim_start_matches("./")));
                }
            }
        }
    }
    Ok(out)
}

/// Whether the record cites a path: by its own name, or by a folder it is in (named with its slash, or by a path of
/// more than one part, so that a word of the record never names a folder by chance).
fn is_cited(cited: &HashSet<String>, path: &str) -> bool {
    if cited.contains(path) {
        return true;
    }
    let mut p = path;
    while let Some((head, _)) = p.rsplit_once('/') {
        if cited.contains(&format!("{head}/")) || (head.contains('/') && cited.contains(head)) {
            return true;
        }
        p = head;
    }
    false
}

/// The paths git tracks among these, asked in batches.
fn tracked(root: &Path, paths: &[String]) -> HashSet<String> {
    let mut out = HashSet::new();
    for chunk in paths.chunks(400) {
        let mut args: Vec<&str> = vec!["ls-files", "-z", "--full-name", "--"];
        let tops: Vec<String> = chunk.iter().map(|p| format!(":(top,literal){p}")).collect();
        args.extend(tops.iter().map(String::as_str));
        if let Some(b) = sources::git_bytes(root, &args) {
            out.extend(b.split(|c| *c == 0).filter(|p| !p.is_empty()).map(|p| String::from_utf8_lossy(p).into_owned()));
        }
    }
    out
}

fn uncited_in(led: &Ledger, st: &Dict, all_entries: &[Dict], left: &HashSet<String>, seen: Seen, placed: &[Found], unplaced: &[String]) -> Res<Vec<Uncited>> {
    let base = baseline(led)?;
    let tb = base.at.as_deref();
    let changes = st.get("changes").and_then(|v| v.as_dict());
    let in_git = st.get("git").map_or(false, |g| g.is_dict());
    let since = changes.and_then(|c| c.get_str("base")).unwrap_or("").to_string();
    let start = if base.applied { "the last rb reconcile --apply" } else { "the record began" };
    let mk = |path: &str, mark: &str, at: &str| Uncited {
        path: path.into(),
        since: since.clone(),
        git: in_git,
        start,
        mark: mark.chars().take(16).collect(),
        at: at.into(),
        ..Default::default()
    };
    let mut cand: indexmap::IndexMap<String, Uncited> = indexmap::IndexMap::new();
    // what git says differs from the commit the count starts at, tracked or not
    for (p, info) in changes.and_then(|c| c.get("files")).and_then(|f| f.as_dict()).into_iter().flatten() {
        let change = info.get_str("change").unwrap_or("");
        let sha = info.get_str("sha").unwrap_or("");
        let mut u = mk(p, sha, info.get_str("at").unwrap_or(""));
        if change == "untracked" {
            // there before the count starts, as rb first saw it, is no change while its bytes are those
            let written = info.get("mt0").and_then(|v| v.as_i64()).and_then(|m| crate::pytime::from_float(m as f64 / 1e9)).map(|d| crate::pytime::iso_ms(&d));
            let existed = crate::pytime::before(info.get_str("first"), tb) || (info.get("early").map_or(false, |e| e.truthy()) && crate::pytime::before(written.as_deref(), tb));
            if existed && info.get_str("sha0") == Some(sha) {
                continue;
            }
            u.new = !existed;
        } else {
            u.new = change == "added";
            u.gone = change == "deleted";
            u.committed = info.get("committed").map_or(false, |c| c.truthy());
        }
        cand.insert(p.clone(), u);
    }
    // what the walk saw where git says nothing: outside git, and a file git ignores. A file's bytes when the count
    // starts are those of the last look before then; one rb first saw on its first look here was there before when it
    // was last written before then
    let walked = st.get("files").and_then(|v| v.as_dict());
    let first_at = all_entries.iter().find_map(|e| e.get_str("at")).and_then(secs_of);
    let early = |at: &str| matches!((secs_of(at), first_at), (Some(a), Some(f)) if a - f <= 10.0);
    let mut hist: HashMap<&str, (Option<Option<String>>, Option<(String, Option<String>, String)>)> = HashMap::new();
    for e in all_entries.iter().filter(|e| e.get_str("op") == Some("file") && !e.contains_key("git")) {
        let (Some(path), Some(at)) = (e.get_str("path"), e.get_str("at")) else { continue };
        let sha = if e.get_str("change") == Some("gone") { None } else { e.get_str("sha256").map(str::to_string) };
        let h = hist.entry(path).or_default();
        if h.1.is_none() {
            h.1 = Some((at.to_string(), sha.clone(), e.get_str("written").unwrap_or("").to_string()));
        }
        if crate::pytime::before(Some(at), tb) {
            h.0 = Some(sha);
        }
    }
    let mut from_walk: Vec<String> = Vec::new();
    for (p, meta) in walked.into_iter().flatten() {
        if cand.contains_key(p) || !structured(p) {
            continue;
        }
        let Some(cur) = meta.get_str("sha").filter(|s| !s.is_empty()) else { continue };
        // no entry: there before the journal began
        let Some((at_start, first)) = hist.get(p.as_str()) else { continue };
        let then: Option<String> = match (at_start, first) {
            (Some(s), _) => s.clone(),
            (None, Some((at, sha, written))) if early(at) && crate::pytime::before(Some(written.as_str()), tb) => sha.clone(),
            _ => None,
        };
        if then.as_deref() == Some(cur) {
            continue;
        }
        let mut u = mk(p, cur, meta.get_str("at").unwrap_or(""));
        u.new = then.is_none();
        cand.insert(p.clone(), u);
        from_walk.push(p.clone());
    }
    // in git, what git tracks is git's to say: one it tracks and does not list is as it was
    if in_git && !from_walk.is_empty() {
        let known = tracked(&led.root, &from_walk);
        cand.retain(|p, _| !known.contains(p));
    }
    if cand.is_empty() {
        return Ok(Vec::new());
    }
    // a results folder no experiment reads yet has its own item, and an indexed folder's files are cited; a result rb
    // proposes to record, or one in a folder the walk reads that it lists as found, is named there already
    let folders: Vec<String> = crate::repo::unindexed(led)?;
    let roots: Vec<String> = watch_roots(led).iter().filter_map(|r| r.strip_prefix(&led.root).ok().map(|p| format!("{}/", crate::sys::slashed(p)))).collect();
    let proposed: HashSet<&str> = placed.iter().filter(|f| !f.printed).map(|f| f.path.as_str()).collect();
    let reported: HashSet<&str> = unplaced.iter().map(String::as_str).collect();
    let cited = cited_paths(led)?;
    let recorded = recorded_keys(led, seen)?;
    // a result rb found that this view left out stays out here too
    let found_left = |p: &str| walked.and_then(|f| f.get(p)).and_then(|m| m.get_str("sha")).map_or(false, |sha| left.contains(sha));
    let mut out: Vec<Uncited> = Vec::new();
    for (path, mut u) in cand {
        if !structured(&path) || folders.iter().any(|f| path.starts_with(f.as_str())) || proposed.contains(path.as_str()) {
            continue;
        }
        if reported.contains(path.as_str()) && roots.iter().any(|r| path.starts_with(r.as_str())) {
            continue;
        }
        if is_cited(&cited, &path) || in_nested(led, &path) || found_left(&path) {
            continue;
        }
        if !u.gone && !led.root.join(&path).is_file() {
            continue;
        }
        // a file where results land that this look read cut mid-write (empty, or not whole, and written in the last
        // minute) says nothing yet: the look that reads it whole says what it is, a heartbeat, a result or a changed
        // file; one that stays so longer (a run that stopped mid-write, JSON Lines) is listed as any other
        if !u.gone && roots.iter().any(|r| path.starts_with(r.as_str())) && !read_whole(&led.root, &path) && written_lately(&led.root, &path) {
            continue;
        }
        u.key = format!("changed:{path}@{}", if u.gone { "gone" } else { u.mark.as_str() });
        if left.contains(&u.key) || recorded.contains(&u.key) {
            continue;
        }
        out.push(u);
    }
    out.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(out)
}

/// Whether a .json, .yaml or .toml file reads whole: not empty, and parsed by its kind. One past what a look parses is
/// taken as whole, since rb cannot tell.
fn read_whole(root: &Path, rel: &str) -> bool {
    let Some(kind) = sources::structured_kind(rel) else { return true };
    let p = root.join(rel);
    if std::fs::metadata(&p).map_or(true, |md| md.len() > MAX_PARSE_BYTES) {
        return true;
    }
    let Ok(bytes) = std::fs::read(&p) else { return true };
    let text = String::from_utf8_lossy(&bytes);
    !text.trim().is_empty() && sources::parse_structured(&text, kind, rel).is_ok()
}

/// Whether a file was last written in the last minute: one a look reads cut mid-write is being written.
fn written_lately(root: &Path, rel: &str) -> bool {
    std::fs::metadata(root.join(rel)).and_then(|m| m.modified()).ok().and_then(|t| std::time::SystemTime::now().duration_since(t).ok()).map_or(true, |age| age.as_secs() < 60)
}

/// Whether a path is inside a worktree nested in the checkout: that checkout's, not this one's.
fn in_nested(led: &Ledger, path: &str) -> bool {
    led.root.join(path).parent().map_or(false, |d| crate::ledger::in_nested_checkout(d, &led.root))
}

// ---------------------------------------------------------------- where rb looks

/// What rb watches and when it last looked, as every read says it: the folders where results land (the usual ones,
/// whether or not they are there, and every one a run named), the files of the top folder, the .json, .yaml and .toml
/// files git shows as new or changed, and the commands run under rb journal run.
#[derive(Debug, Clone, Default)]
pub struct Scope {
    /// the folders the look walks, from the repository's top, each with a slash
    pub folders: Vec<String>,
    /// when rb last looked (rb's time), none before its first look
    pub looked_at: Option<String>,
    /// the last look stopped at the file cap, or kept the first of more changed files than the cap
    pub cap_hit: bool,
    /// in a git repository: what git shows as new or changed is watched too
    pub git: bool,
    /// the journal is kept in .rb/journal/, since its home in git's folder could not be written
    pub aside: bool,
}

/// What rb reads only where the record points to it, and what it does not see: the scope's own words for --json.
pub const NOT_WATCHED: &[&str] = &["code", "configs", "docs", "database rows", "commands not run through rb journal run"];

impl Scope {
    pub fn of(led: &Ledger) -> Scope {
        Scope::from_state(led, &load_state(&led.root))
    }

    pub fn from_state(led: &Ledger, st: &Dict) -> Scope {
        let mut folders: Vec<String> = DEFAULT_DIRS.iter().map(|d| format!("{d}/")).collect();
        for r in watch_roots(led) {
            if let Ok(p) = r.strip_prefix(&led.root) {
                let p = format!("{}/", crate::sys::slashed(p));
                if p != "/" && !folders.iter().any(|f| p.starts_with(f.as_str())) {
                    folders.push(p);
                }
            }
        }
        let looked = st.get("looked");
        let looked_at = looked.and_then(|l| l.get_str("at")).map(str::to_string);
        // after a look, the state says whether this is git; before one, git does
        let git = match &looked_at {
            Some(_) => st.get("git").map_or(false, |g| g.is_dict()),
            None => sources::git_top(&led.root).is_some(),
        };
        Scope { folders, looked_at, cap_hit: looked.and_then(|l| l.get("cut")).map_or(false, |c| c.truthy()), git, aside: kept_aside(&led.root) }
    }

    /// The one quiet line: where rb looks and when it last did, then what it reads only where the record points to it,
    /// and which commands it sees.
    pub fn said(&self) -> String {
        let and_list = |items: &[String]| match items.split_last() {
            Some((last, rest)) if !rest.is_empty() => format!("{} and {last}", rest.join(", ")),
            _ => items.join(""),
        };
        let usual = DEFAULT_DIRS.len().min(self.folders.len());
        let mut named: Vec<String> = self.folders[..usual].to_vec();
        let others = &self.folders[usual..];
        named.extend(others.iter().take(2).cloned());
        if others.len() > 2 {
            named.push(format!("{} more folders runs named", others.len() - 2));
        }
        let top = if self.git { "the repository's top folder" } else { "the project's top folder" };
        let what = if self.git {
            format!("rb looks in {}, at the files in {top}, and at .json, .yaml and .toml files git shows as new or changed.", and_list(&named))
        } else {
            format!("rb looks in {}, and at the files in {top}.", and_list(&named))
        };
        let when = match &self.looked_at {
            Some(at) => format!("It last looked at {} UTC.", at.chars().take(16).collect::<String>().replace('T', " ")),
            None => "It has not looked yet.".to_string(),
        };
        let cap = if self.cap_hit { format!(" That look stopped at {MAX_FILES} files, so some were not looked at.") } else { String::new() };
        let aside = if self.aside {
            " Git's folder cannot be written here, so rb keeps its journal in .rb/journal/, which git ignores, until a look can move it there."
        } else {
            ""
        };
        format!(
            "{what} {when}{cap}{aside} It reads what code, configs, docs and database rows say only where the record points to them, and it sees only the commands run through rb journal run."
        )
    }

    /// The same, where a screen has one short line for it: when rb last looked, and the command that says the rest.
    pub fn said_short(&self) -> String {
        match &self.looked_at {
            Some(at) => format!(
                "rb last looked at {} UTC{}; rb status says where it looks.",
                at.chars().take(16).collect::<String>().replace('T', " "),
                if self.cap_hit { format!(" and stopped at {MAX_FILES} files") } else { String::new() }
            ),
            None => "rb has not looked yet; rb status says where it looks.".to_string(),
        }
    }

    pub fn to_value(&self) -> Value {
        crate::obj! {
            "looked_at" => Value::opt_str(self.looked_at.clone()), "cap_hit" => self.cap_hit, "file_cap" => MAX_FILES as i64,
            "folders" => Value::list_of_str(self.folders.iter().cloned()), "top_level_files" => true, "git_changes" => self.git,
            "kinds" => Value::list_of_str(if self.git { vec![".json".to_string(), ".yaml".into(), ".yml".into(), ".toml".into()] } else { Vec::new() }),
            "commands" => "rb journal run", "not_watched" => Value::list_of_str(NOT_WATCHED.iter().map(|s| s.to_string())), "said" => self.said(),
            "journal_aside" => self.aside
        }
    }
}

/// Look once before a read, as rb open does on its cadence: best effort, since a journal that cannot be kept does not
/// stop a read, and never inside a preview.
pub fn look(led: &Ledger) {
    if !crate::dry::active() {
        let _ = scan(led);
    }
}

/// The look an agent's session hook makes (`rb journal scan --hook <agent>`, which rb init installs): one scan, kept as
/// that agent's (never the user's, whatever this shell says), where a record is. It waits on no one: while another rb
/// holds the record's lock, that rb is reading or writing it, and this look is left to the next one. It never fails:
/// what it finds is observation, and a hook that cannot look leaves things as they were.
pub fn hook_look(agent: &str) {
    let named = !agent.is_empty() && agent.len() <= 40 && agent.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-');
    let Some(root) = crate::ledger::find_root(None).filter(|_| named) else { return };
    let lock = root.join(DIR).join(".lock");
    if let Ok(f) = std::fs::OpenOptions::new().read(true).write(true).open(&lock) {
        if f.try_lock().is_err() {
            return;
        }
    }
    if let Ok(led) = Ledger::open_with(None, None, Some(agent)) {
        let _ = scan(&led);
    }
}

#[cfg(test)]
mod tests {
    use super::{is_git_command, same_word, shell_words, value_in_command};

    /// A watched command runs git when any of its words names git, by path or not, a shell's -c line included; a word that
    /// only starts with git is not git.
    #[test]
    fn a_command_runs_git_by_any_of_its_words() {
        assert!(is_git_command("git checkout HEAD -- results/run1/metrics.json"));
        assert!(is_git_command("/usr/bin/git restore results/run1/metrics.json"));
        assert!(is_git_command("'C:\\Program Files\\Git\\cmd\\git.exe' stash pop"));
        assert!(!is_git_command("python eval.py --precision int8"));
        assert!(is_git_command("sh -c \"git checkout HEAD~1 -- results/run1/metrics.json\""));
        assert!(is_git_command("bash -c 'cd x && git restore r.json'"));
        assert!(!is_git_command("gitlab-runner exec"));
        assert!(!is_git_command(""));
    }

    fn words(line: &str) -> Vec<String> {
        shell_words(line)
    }

    /// A watched command's line gives a per-run value as `--seed 4`, `--seed=4` or `seed=4`, by the setting's name or its
    /// last part, `-` and `_` alike, and through a shell's quoting; a flag that only starts with the name gives none.
    #[test]
    fn a_command_line_gives_a_per_run_value_in_each_form_it_is_written() {
        assert_eq!(value_in_command(&words("python eval.py --seed 4"), "seed").as_deref(), Some("4"));
        assert_eq!(value_in_command(&words("python eval.py --seed=4 --out out/seed4"), "seed").as_deref(), Some("4"));
        assert_eq!(value_in_command(&words("python printonly.py seed=7"), "seed").as_deref(), Some("7"));
        assert_eq!(value_in_command(&words("python eval.py --train-seed 5"), "train.seed").as_deref(), None);
        assert_eq!(value_in_command(&words("python eval.py --train_seed 5"), "train_seed").as_deref(), Some("5"));
        assert_eq!(value_in_command(&words("python eval.py --optim.lr=0.1"), "optim.lr").as_deref(), Some("0.1"));
        assert_eq!(value_in_command(&words("python eval.py --seeds 4"), "seed"), None);
        assert_eq!(value_in_command(&words("python eval.py --seed --fast"), "seed"), None);
        // a shell's quoting: what sh -c runs is one word, which gives no value of its own
        assert_eq!(words("sh -c \"python3 printonly.py seed=8\""), vec!["sh", "-c", "python3 printonly.py seed=8"]);
        assert_eq!(value_in_command(&words("sh -c \"python3 printonly.py seed=8\""), "seed"), None);
        assert_eq!(value_in_command(&words("python eval.py --seed '4'"), "seed").as_deref(), Some("4"));
        assert_eq!(words(r"python eval.py --name a\ b"), vec!["python", "eval.py", "--name", "a b"]);
    }

    /// A config's value and the line's read as one where they are the same number or the same text.
    #[test]
    fn two_values_read_as_one_when_they_are_the_same_number() {
        assert!(same_word("1", "1.0") && same_word("seed-a", "seed-a"));
        assert!(!same_word("1", "3") && !same_word("01a", "1a"));
    }
}
