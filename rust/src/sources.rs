//! Resolving a source: the one way a setting becomes `verified`.
//! "Verified" means: this key has this value at this commit. Every rule errs toward "not stated".

use crate::pyfloat;
use crate::pyjson::{self, repr_str};
use crate::pytime;
use crate::rbyaml::{py_strip, sha256_hex};
use crate::record::{Resolution, Setting, Source, RECORD_DIR};
use crate::value::{Dict, Value};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::time::{Duration, Instant};

pub const UNTRACKED_HASH_LIMIT: u64 = 16 * 1024 * 1024;
pub const SNAPSHOT_FILE_LIMIT: u64 = 10 * 1024 * 1024;
pub const SNAPSHOT_MAX_FILES: usize = 2000;
pub const SNAPSHOT_REFS: &str = "refs/rb/snapshots";
pub const FUNCTION_WORDS: &[&str] = &["the", "and", "for", "with", "our", "this", "that", "are", "was", "were", "from", "into", "all", "use", "used", "using", "set", "value"];
const PROSE: &[&str] = &[".txt", ".md", ".markdown", ".qmd", ".rmd", ".mdx", ".rst", ".tex", ".html", ".htm", ""];

/// The source was read and does not state the value, or could not be read.
#[derive(Debug, Clone)]
pub struct Unresolved {
    pub code: String,
    pub reason: String,
    pub found: Value,
    pub differs: bool,
    pub candidates: Vec<Value>,
    /// Where a line or a quote was read (file:line) and what it read, when the reason quotes it.
    pub at: String,
    pub reads: String,
    /// The source could not be read (missing, unreadable, not at its commit, outside the project, not parsed): what it
    /// states is not known.
    pub unread: bool,
}

impl Unresolved {
    pub fn new(code: &str, reason: impl Into<String>) -> Unresolved {
        Unresolved { code: code.into(), reason: reason.into(), found: Value::Null, differs: false, candidates: Vec::new(), at: String::new(), reads: String::new(), unread: false }
    }
    fn unread(mut self) -> Unresolved {
        self.unread = true;
        self
    }
    fn found(mut self, found: Value) -> Unresolved {
        self.found = found;
        self.differs = true;
        self
    }
    fn reads(mut self, at: &str, text: String) -> Unresolved {
        self.at = at.to_string();
        self.reads = text;
        self
    }
    fn with_candidates(mut self, c: Vec<Value>) -> Unresolved {
        self.candidates = c;
        self
    }
    /// The conflict this is, as every surface says one: the record's value first, and the source's as what it now says
    /// only when rb `saw` it state the record's value before.
    pub fn conflict(&self, source: &Source, value: &Value, saw: bool) -> String {
        let at = if self.reads.is_empty() { source.label() } else { self.at.clone() };
        conflict_says(value, &self.found, &at, &self.reads, saw)
    }
}

type R<T> = Result<T, Unresolved>;

// ---------------------------------------------------------------- times and hashes

pub fn now() -> String {
    pytime::now()
}

pub fn sha256_bytes(data: &[u8]) -> String {
    sha256_hex(data)
}

/// The same text with the other line ending, when these bytes are text whose lines all end one way: with `\n` where
/// every line ends `\r\n`, with `\r\n` where none does. None for anything else: bytes with a NUL in them, text with
/// no line end at all, text that mixes the two.
pub fn other_line_ending(data: &[u8]) -> Option<Vec<u8>> {
    if data.contains(&0) {
        return None;
    }
    let ends = data.iter().filter(|b| **b == b'\n').count();
    let returns = data.iter().filter(|b| **b == b'\r').count();
    let crlf = data.windows(2).filter(|w| w == b"\r\n").count();
    if ends == 0 || returns != crlf || (crlf != 0 && crlf != ends) {
        return None;
    }
    let mut out = Vec::with_capacity(data.len() + ends);
    if crlf == 0 {
        for b in data {
            if *b == b'\n' {
                out.push(b'\r');
            }
            out.push(*b);
        }
    } else {
        out.extend(data.iter().filter(|b| **b != b'\r'));
    }
    Some(out)
}

/// Whether these bytes are the file a digest was taken of: the same bytes, or the same text with the other line
/// ending. A git set to convert line endings (as it is on Windows by default) checks a text file out with `\r\n`
/// where every other machine has `\n`: the same file to whoever reads it, and so to rb. The digest rb records is
/// always of the bytes as they were where it was recorded.
pub fn is_file_of(data: &[u8], sha: &str) -> bool {
    sha256_bytes(data) == sha || other_line_ending(data).map_or(false, |twin| sha256_bytes(&twin) == sha)
}

/// The same for a file on disk whose own digest is already known not to be `sha`: read again only when it is small
/// enough to be text someone keeps in git.
pub fn file_is_twin_of(path: &Path, sha: &str) -> bool {
    match std::fs::metadata(path) {
        Ok(md) if md.len() <= UNTRACKED_HASH_LIMIT => {}
        _ => return false,
    }
    std::fs::read(path).ok().and_then(|data| other_line_ending(&data)).map_or(false, |twin| sha256_bytes(&twin) == sha)
}

/// A file's sha256, read in chunks; with `cache` (a JSON file under .rb/cache/), remembered by path, size and mtime.
pub fn sha256_file(path: &Path, cache: Option<&Path>) -> std::io::Result<String> {
    use sha2::{Digest, Sha256};
    let md = std::fs::metadata(path)?;
    let key = crate::sys::canonicalize(path).unwrap_or_else(|_| path.to_path_buf()).to_string_lossy().into_owned();
    let mtime_ns: i128 = crate::sys::mtime_ns(&md);
    let stamp = (md.len() as i64, mtime_ns);
    let mut table = Dict::new();
    if let Some(c) = cache {
        if let Ok(text) = std::fs::read_to_string(c) {
            if let Ok(Value::Dict(t)) = pyjson::loads(&text) {
                if let Some(Value::List(hit)) = t.get(&key) {
                    if hit.len() >= 3 {
                        let size_ok = hit[0].as_i64() == Some(stamp.0);
                        let mt_ok = match &hit[1] {
                            Value::Int(i) => *i as i128 == stamp.1,
                            Value::BigInt(s) => s.parse::<i128>().ok() == Some(stamp.1),
                            _ => false,
                        };
                        if size_ok && mt_ok {
                            if let Value::Str(d) = &hit[2] {
                                return Ok(d.clone());
                            }
                        }
                    }
                }
                table = t;
            }
        }
    }
    let mut f = std::fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    let digest = crate::rbyaml::hex(&h.finalize());
    if let Some(c) = cache.filter(|_| !crate::dry::active()) {
        let mt = if stamp.1 >= i64::MIN as i128 && stamp.1 <= i64::MAX as i128 { Value::Int(stamp.1 as i64) } else { Value::BigInt(stamp.1.to_string()) };
        table.insert(key, Value::List(vec![Value::Int(stamp.0), mt, Value::Str(digest.clone())]));
        if let Some(parent) = c.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let tmp = c.with_file_name(format!("{}.{}.tmp", c.file_name().unwrap().to_string_lossy(), std::process::id()));
        if std::fs::write(&tmp, pyjson::dumps(&Value::Dict(table))).is_ok() {
            let _ = crate::sys::replace(&tmp, c);
        }
    }
    Ok(digest)
}

// ---------------------------------------------------------------- running git

pub struct Ran {
    pub code: i32,
    pub stdout: Vec<u8>,
}

/// A command with a timeout; None when it could not start or ran out of time.
pub fn run(program: &str, args: &[&str], cwd: Option<&Path>, env: &[(&str, &str)], input: Option<&[u8]>, timeout: Duration) -> Option<Ran> {
    let mut c = Command::new(program);
    c.args(args).stdout(Stdio::piped()).stderr(Stdio::null());
    c.stdin(if input.is_some() { Stdio::piped() } else { Stdio::null() });
    if let Some(d) = cwd {
        c.current_dir(d);
    }
    for (k, v) in env {
        c.env(k, v);
    }
    // git refreshes its index on a read when it may take the index's lock: rb leaves the index as it was
    if program == "git" {
        c.env("GIT_OPTIONAL_LOCKS", "0");
    }
    let mut child = c.spawn().ok()?;
    if let Some(data) = input {
        let mut stdin = child.stdin.take()?;
        let data = data.to_vec();
        std::thread::spawn(move || {
            let _ = stdin.write_all(&data);
        });
    }
    let mut out = child.stdout.take()?;
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = out.read_to_end(&mut buf);
        buf
    });
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let stdout = reader.join().unwrap_or_default();
                return Some(Ran { code: status.code().unwrap_or(-1), stdout });
            }
            Ok(None) => {
                if start.elapsed() > timeout {
                    let _ = child.kill();
                    let _ = child.wait();
                    return None;
                }
                std::thread::sleep(Duration::from_millis(2));
            }
            Err(_) => return None,
        }
    }
}

thread_local! {
    /// What git answered while this command line ran, and what it would not answer: a question asked again is not put
    /// to git again.
    static ANSWERS: std::cell::RefCell<std::collections::HashMap<(PathBuf, Vec<String>), Option<Vec<u8>>>> = std::cell::RefCell::new(std::collections::HashMap::new());
}

/// Forget git's answers: a new command line asks afresh, and so does rb after anything that may have moved the
/// repository (a command it watched, a repository it made).
pub fn forget_git() {
    ANSWERS.with(|a| a.borrow_mut().clear());
}

/// How long git's answer to a question holds while one command line runs. rb changes none of these, one command asks
/// them many times over, and starting git costs (a few milliseconds on Linux and macOS, tens on Windows).
#[derive(PartialEq)]
enum Holds {
    Never,
    /// Where the repository is, what HEAD is, whether an object is here, which tree a commit or a tree is: kept once git
    /// answers, since "no repository here", "no commit yet" or a tree not written yet is what rb init, rb clone, a first
    /// commit and a snapshot change.
    Answered,
    /// Which commit a name is, and what HEAD or a commit holds at a path: kept whatever git says, since rb fetches
    /// nothing and commits only where it forgets every answer, and a path HEAD lacks (a source git does not track) or
    /// a commit this clone lacks is otherwise asked again for every setting that reads it.
    Always,
}

fn holds(args: &[&str]) -> Holds {
    match args {
        ["rev-parse", "--verify", "-q", what] if what.ends_with("^{commit}") => Holds::Always,
        ["rev-parse", "--verify", "-q", what] if what.ends_with("^{tree}") => Holds::Answered,
        ["show", what] if what.contains(':') => Holds::Always,
        // when a commit was made: every setting that cites it asks
        ["show", "-s", "--format=%cI", _] => Holds::Always,
        ["rev-parse", "--show-toplevel"] | ["rev-parse", "--verify", "-q", "HEAD"] | ["rev-parse", "--abbrev-ref", "HEAD"] => Holds::Answered,
        ["rev-parse", "--git-common-dir", ..] | ["symbolic-ref", "--short", "-q", "HEAD"] | ["cat-file", "-e", _] => Holds::Answered,
        ["worktree", "list", "--porcelain"] | ["rev-parse", "--absolute-git-dir"] => Holds::Answered,
        ["log", "--format=%cI", "--reverse", "--", _] => Holds::Answered,
        // who last changed a line of a source: every finding that reads the setting asks
        ["blame", "--porcelain", "-L", _, "--", _] => Holds::Answered,
        _ => Holds::Never,
    }
}

/// Output of a git command run in `cwd`, or None when git is missing, `cwd` is not in a repository, or it fails.
pub fn git_bytes(cwd: &Path, args: &[&str]) -> Option<Vec<u8>> {
    let holds = holds(args);
    let key = (holds != Holds::Never).then(|| (cwd.to_path_buf(), args.iter().map(|a| a.to_string()).collect::<Vec<String>>()));
    if let Some(k) = &key {
        if let Some(kept) = ANSWERS.with(|a| a.borrow().get(k).cloned()) {
            return kept;
        }
    }
    let cwd_s = cwd.to_string_lossy().into_owned();
    let mut full: Vec<&str> = vec!["-C", &cwd_s];
    full.extend_from_slice(args);
    let answer = run("git", &full, None, &[], None, Duration::from_secs(30)).filter(|r| r.code == 0).map(|r| r.stdout);
    if let Some(k) = key.filter(|_| answer.is_some() || holds == Holds::Always) {
        ANSWERS.with(|a| a.borrow_mut().insert(k, answer.clone()));
    }
    answer
}

pub fn git(cwd: &Path, args: &[&str]) -> Option<String> {
    git_bytes(cwd, args).map(|b| String::from_utf8_lossy(&b).into_owned())
}

/// `git diff` of the working tree, asked of a copy of this checkout's index: git diff refreshes the index it reads and
/// writes it back whatever GIT_OPTIONAL_LOCKS says, and a read of rb's leaves the user's index as it was.
pub fn git_diff_bytes(cwd: &Path, args: &[&str]) -> Option<Vec<u8>> {
    let index = match std::env::var_os("GIT_INDEX_FILE").filter(|f| !f.is_empty()) {
        Some(f) => {
            let p = PathBuf::from(f);
            if p.is_absolute() {
                p
            } else {
                cwd.join(p)
            }
        }
        None => PathBuf::from(git(cwd, &["rev-parse", "--absolute-git-dir"])?.trim()).join("index"),
    };
    let tmp = std::env::temp_dir().join(format!("rb-index-{}-{}", std::process::id(), crate::rng::os_randbits(32)));
    std::fs::create_dir_all(&tmp).ok()?;
    let copy = tmp.join("index");
    if index.is_file() {
        if std::fs::copy(&index, &copy).is_err() {
            let _ = std::fs::remove_dir_all(&tmp);
            return None;
        }
        // the copy keeps the index's time, by which git tells a file changed in the second the index was written
        if let Ok(t) = std::fs::metadata(&index).and_then(|m| m.modified()) {
            let _ = std::fs::OpenOptions::new().write(true).open(&copy).and_then(|f| f.set_modified(t));
        }
    }
    let (cwd_s, copy_s) = (cwd.to_string_lossy().into_owned(), copy.to_string_lossy().into_owned());
    let mut full: Vec<&str> = vec!["-C", &cwd_s, "diff"];
    full.extend_from_slice(args);
    let answer = run("git", &full, None, &[("GIT_INDEX_FILE", copy_s.as_str())], None, Duration::from_secs(30)).filter(|r| r.code == 0).map(|r| r.stdout);
    let _ = std::fs::remove_dir_all(&tmp);
    answer
}

pub fn git_top(path: &Path) -> Option<PathBuf> {
    let dir = if path.is_dir() { path.to_path_buf() } else { path.parent().map(|p| p.to_path_buf()).unwrap_or_default() };
    let dir = if dir.as_os_str().is_empty() { PathBuf::from(".") } else { dir };
    let top = git(&dir, &["rev-parse", "--show-toplevel"])?;
    let t = top.trim();
    if t.is_empty() {
        return None;
    }
    Some(crate::sys::canonicalize(t).unwrap_or_else(|_| PathBuf::from(t)))
}

/// The posix relative path of `path` under `base`, or None: a backslash is a separator on Windows alone, and part of
/// a name elsewhere.
pub fn rel_to(path: &Path, base: &Path) -> Option<String> {
    path.strip_prefix(base).ok().map(crate::sys::slashed)
}

pub fn resolve_path(p: &Path) -> PathBuf {
    // Path.resolve() (strict=False): the canonical form of what exists, the rest appended
    if let Ok(c) = crate::sys::canonicalize(p) {
        return c;
    }
    let abs = if p.is_absolute() { p.to_path_buf() } else { std::env::current_dir().unwrap_or_default().join(p) };
    let mut parts: Vec<std::ffi::OsString> = Vec::new();
    let mut cur = abs.clone();
    loop {
        if let Ok(c) = crate::sys::canonicalize(&cur) {
            let mut out = c;
            for part in parts.iter().rev() {
                if part == ".." {
                    out.pop();
                } else if part != "." {
                    out.push(part);
                }
            }
            return out;
        }
        match (cur.file_name().map(|f| f.to_os_string()), cur.parent().map(|p| p.to_path_buf())) {
            (Some(f), Some(parent)) => {
                parts.push(f);
                cur = parent;
            }
            _ => return abs,
        }
    }
}

/// The repository at this moment: the commit, the branch, whether the tree had uncommitted changes, and a hash of the
/// tracked diff and the untracked files. The record's own directory is left out.
pub fn git_state(root: &Path, exclude: &str) -> Option<Dict> {
    let top = git_top(root)?;
    let head = git(root, &["rev-parse", "--verify", "-q", "HEAD"]);
    let spec = code_pathspec(root, exclude, &top);
    let pathspec: Vec<&str> = spec.iter().map(|s| s.as_str()).collect();
    let mut st_args = vec!["status", "--porcelain", "--untracked-files=all"];
    st_args.extend_from_slice(&pathspec);
    let status = git(root, &st_args).unwrap_or_default();
    let branch = git(root, &["symbolic-ref", "--short", "-q", "HEAD"]).unwrap_or_default().trim().to_string();
    let mut state = Dict::new();
    state.insert("commit".into(), match &head {
        Some(h) => Value::Str(h.trim().to_string()),
        None => Value::Null,
    });
    state.insert("branch".into(), if branch.is_empty() { Value::Null } else { Value::Str(branch) });
    let dirty = !status.trim().is_empty();
    state.insert("dirty".into(), Value::Bool(dirty));
    if dirty {
        use sha2::{Digest, Sha256};
        let mut h = Sha256::new();
        if head.is_some() {
            let mut d = vec!["HEAD"];
            d.extend_from_slice(&pathspec);
            h.update(git_diff_bytes(root, &d).unwrap_or_default());
        }
        let mut loose: Vec<String> = status
            .lines()
            .filter(|l| head.is_none() || l.starts_with("??"))
            .map(|l| l.chars().skip(3).collect::<String>().trim_matches('"').to_string())
            .collect();
        loose.sort();
        let mut by_size = 0;
        for rel in &loose {
            let p = top.join(rel);
            h.update(rel.as_bytes());
            h.update(b"\0");
            match std::fs::metadata(&p) {
                Ok(md) => {
                    if md.len() <= UNTRACKED_HASH_LIMIT {
                        match std::fs::read(&p) {
                            Ok(b) => h.update(&b),
                            Err(_) => h.update(b"unreadable"),
                        }
                    } else {
                        h.update(format!("size={}", md.len()).as_bytes());
                        by_size += 1;
                    }
                }
                Err(_) => h.update(b"unreadable"),
            }
        }
        state.insert("diff_sha256".into(), Value::Str(crate::rbyaml::hex(&h.finalize())));
        state.insert("untracked".into(), Value::Int(status.lines().filter(|l| l.starts_with("??")).count() as i64));
        if by_size > 0 {
            state.insert("untracked_hashed_by_size".into(), Value::Int(by_size));
        }
    }
    Some(state)
}

/// What a receipt's code is: the whole worktree from its top, but the record's own directory and the other worktrees of
/// this clone nested in it, which are checkouts of their own, not this one's code.
fn code_pathspec(root: &Path, exclude: &str, top: &Path) -> Vec<String> {
    let skip = rel_to(&resolve_path(&root.join(exclude)), top).unwrap_or_else(|| exclude.to_string());
    let mut spec = vec!["--".to_string(), ":(top)".to_string(), format!(":(top,exclude){skip}")];
    for line in git(top, &["worktree", "list", "--porcelain"]).unwrap_or_default().lines() {
        let Some(listed) = line.strip_prefix("worktree ") else { continue };
        let path = resolve_path(Path::new(listed));
        if path != top {
            if let Some(rel) = rel_to(&path, top).filter(|r| !r.is_empty()) {
                spec.push(format!(":(top,exclude,literal){rel}"));
            }
        }
    }
    spec
}

/// The exact code of a working tree with uncommitted changes, as one git tree written through a temporary index.
/// The tree of a commit a receipt names, kept under refs/rb/snapshots/ beside the snapshots: a branch deleted, a squash
/// merge or a worktree thrown away no longer takes the code a run was recorded at with it.
pub fn keep_tree(root: &Path, commit: &str) -> Option<String> {
    let top = git_top(root)?;
    let tree = git(&top, &["rev-parse", "--verify", "-q", &format!("{commit}^{{tree}}")]).map(|t| t.trim().to_string()).unwrap_or_default();
    if tree.is_empty() {
        return None;
    }
    keep_ref(&top, &tree);
    Some(tree)
}

/// The words a write says once, when it makes the ref that keeps a tree: where it is and the command that removes it.
pub fn snapshot_ref_told(tree: &str) -> String {
    let name = format!("{SNAPSHOT_REFS}/{tree}");
    format!("rb put the code in git at {name}, so it outlives a deleted branch or worktree; git update-ref -d {name} removes it.")
}

/// Whether a line a write told is the one `snapshot_ref_told` says.
pub fn is_snapshot_ref_told(line: &str) -> bool {
    line.starts_with(&format!("rb put the code in git at {SNAPSHOT_REFS}/"))
}

/// Keep `tree` under refs/rb/snapshots/, and say so at the write that makes the ref, never again; a preview makes no
/// ref and says what the write will.
fn keep_ref(top: &Path, tree: &str) {
    let name = format!("{SNAPSHOT_REFS}/{tree}");
    if git(top, &["rev-parse", "--verify", "-q", &name]).map_or(false, |s| !s.trim().is_empty()) {
        return;
    }
    if crate::dry::active() {
        crate::plain::tell(snapshot_ref_told(tree));
    } else if git(top, &["update-ref", &name, tree]).is_some() {
        crate::owned::record(top, "repo", crate::dict! {"path" => name, "how" => "ref", "target" => tree});
        crate::plain::tell(snapshot_ref_told(tree));
    }
}

pub fn code_snapshot(root: &Path, exclude: &str) -> Option<Dict> {
    let top = git_top(root)?;
    let head = git(&top, &["rev-parse", "--verify", "-q", "HEAD"]).map(|h| h.trim().to_string()).filter(|h| !h.is_empty());
    let pathspec = code_pathspec(root, exclude, &top);
    let spec: Vec<&str> = pathspec.iter().map(|s| s.as_str()).collect();
    let split0 = |b: Vec<u8>| -> Vec<Vec<u8>> { b.split(|c| *c == 0).filter(|x| !x.is_empty()).map(|x| x.to_vec()).collect() };
    let changed: Vec<Vec<u8>> = if head.is_some() {
        let mut a = vec!["--name-only", "-z", "HEAD"];
        a.extend_from_slice(&spec);
        split0(git_diff_bytes(&top, &a).unwrap_or_default())
    } else {
        Vec::new()
    };
    let mut a = vec!["ls-files", "-z", "--others", "--exclude-standard"];
    a.extend_from_slice(&spec);
    let others = split0(git_bytes(&top, &a).unwrap_or_default());
    if changed.is_empty() && others.is_empty() {
        return None;
    }
    if changed.len() + others.len() > SNAPSHOT_MAX_FILES {
        return Some(crate::dict! {"tree" => Value::Null, "left_out" => (changed.len() + others.len()) as i64});
    }
    let mut add: Vec<Vec<u8>> = Vec::new();
    let mut remove: Vec<Vec<u8>> = Vec::new();
    let (mut left_out, mut nested) = (0i64, 0i64);
    for rel in changed.iter().chain(others.iter()) {
        let p = top.join(String::from_utf8_lossy(rel).as_ref());
        if !p.exists() && changed.contains(rel) {
            remove.push(rel.clone());
            continue;
        }
        // a directory git lists whole is a repository of its own: an untracked one, or a submodule moved on
        if std::fs::symlink_metadata(&p).map_or(false, |m| m.is_dir()) {
            nested += 1;
            continue;
        }
        match std::fs::metadata(&p) {
            Ok(md) if md.is_file() && md.len() <= SNAPSHOT_FILE_LIMIT => add.push(rel.clone()),
            _ => left_out += 1,
        }
    }
    // a preview writes the tree's objects into a directory of its own, reading the repository's beside it: the same
    // tree, and nothing written in the repository
    let dry = crate::dry::active();
    let objects = if dry {
        let common = git(&top, &["rev-parse", "--git-common-dir"]).map(|d| PathBuf::from(d.trim()))?;
        Some(if common.is_absolute() { common } else { top.join(common) }.join("objects"))
    } else {
        None
    };
    let tmp = std::env::temp_dir().join(format!("rb-snap-{}-{}", std::process::id(), crate::rng::os_randbits(32)));
    let _ = std::fs::create_dir_all(tmp.join("objects"));
    let index = tmp.join("index");
    let index_s = index.to_string_lossy().into_owned();
    let top_s = top.to_string_lossy().into_owned();
    let mut env: Vec<(&str, String)> = vec![("GIT_INDEX_FILE", index_s.clone())];
    if let Some(objects) = &objects {
        // named in info/alternates, which git splits only at a newline (its variable splits at ':', on Windows ';')
        let o = objects.to_string_lossy();
        let line = if o.contains(['\n', '\r']) {
            format!("\"{}\"\n", o.replace('\\', "\\\\").replace('"', "\\\"").replace('\n', "\\n").replace('\r', "\\r"))
        } else {
            format!("{o}\n")
        };
        let info = tmp.join("objects").join("info");
        if std::fs::create_dir_all(&info).and_then(|_| std::fs::write(info.join("alternates"), line)).is_err() {
            let _ = std::fs::remove_dir_all(&tmp);
            return None;
        }
        env.push(("GIT_OBJECT_DIRECTORY", tmp.join("objects").to_string_lossy().into_owned()));
    }
    let env: Vec<(&str, &str)> = env.iter().map(|(k, v)| (*k, v.as_str())).collect();
    let gitrun = |args: &[&str], data: Option<&[u8]>| -> Option<String> {
        let mut full: Vec<&str> = vec!["-C", &top_s];
        full.extend_from_slice(args);
        let r = run("git", &full, None, &env, data, Duration::from_secs(300))?;
        if r.code == 0 {
            Some(String::from_utf8_lossy(&r.stdout).trim().to_string())
        } else {
            None
        }
    };
    let result = (|| {
        if let Some(h) = &head {
            gitrun(&["read-tree", h], None)?;
        }
        if !remove.is_empty() {
            let mut data: Vec<u8> = remove.join(&0u8);
            data.push(0);
            gitrun(&["update-index", "--force-remove", "-z", "--stdin"], Some(&data))?;
        }
        if !add.is_empty() {
            let data: Vec<u8> = add.join(&0u8);
            gitrun(&["add", "--force", "--pathspec-from-file=-", "--pathspec-file-nul"], Some(&data))?;
        }
        let tree = gitrun(&["write-tree"], None)?;
        if tree.is_empty() {
            return None;
        }
        keep_ref(&top, &tree);
        Some(tree)
    })();
    let _ = std::fs::remove_dir_all(&tmp);
    let tree = result?;
    let mut snap = crate::dict! {"tree" => tree, "left_out" => left_out};
    if nested > 0 {
        snap.insert("nested".into(), Value::Int(nested));
    }
    Some(snap)
}

// ---------------------------------------------------------------- reading files

/// A key path into a SQLite database: one column of a stored row, a measurement a run reads, never a number someone
/// else stated.
pub fn is_store_row(source: &Source, root: &Path) -> bool {
    source.kind == "file" && source.key.is_some() && source.path.as_deref().map_or(false, |p| crate::sqlite::is_database(&full_path(root, p)))
}


pub fn full_path(root: &Path, path: &str) -> PathBuf {
    let p = Path::new(path);
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        root.join(p)
    }
}

pub fn in_state(root: &Path, path: &str) -> bool {
    let rec = resolve_path(&root.join(RECORD_DIR));
    let p = resolve_path(&root.join(path));
    p.starts_with(&rec)
}

/// A source must live in the project: under the directory holding .rb/, or in the git repository that holds it.
pub fn inside_project(root: &Path, path: &str) -> bool {
    let real = crate::sys::canonicalize(full_path(root, path)).unwrap_or_else(|_| resolve_path(&full_path(root, path)));
    let mut bases = vec![resolve_path(root)];
    if let Some(top) = git_top(root) {
        bases.push(top);
    }
    bases.iter().any(|b| real == *b || real.starts_with(b))
}

fn repo_and_rel(file: &Path) -> Option<(PathBuf, String)> {
    let real = resolve_path(file);
    let parent = real.parent().map(|p| p.to_path_buf()).unwrap_or_default();
    let top = git_top(if parent.exists() { &parent } else { file.parent().unwrap_or(Path::new(".")) })?;
    Some((top.clone(), rel_to(&real, &top)?))
}

/// Where rb export keeps a source's file as it stood at a commit, beside a record that travels without its git (a
/// bundle): rb's own copy in the record's cache, by commit and path. None for a commit or path that would lead elsewhere.
pub fn kept_path(root: &Path, commit: &str, path: &str) -> Option<PathBuf> {
    let rel = Path::new(path);
    let plain = !commit.is_empty()
        && commit.bytes().all(|b| b.is_ascii_hexdigit())
        && !path.is_empty()
        && rel.components().all(|c| matches!(c, std::path::Component::Normal(_)));
    plain.then(|| root.join(RECORD_DIR).join("cache").join("commits").join(commit).join(rel))
}

/// A source's file as it stood at its commit, as rb export kept it: only when it is the file rb verified there, byte
/// for byte, by the digest it recorded (`sha`).
pub fn kept_file(root: &Path, source: &Source, sha: &str) -> Option<Vec<u8>> {
    let p = kept_path(root, source.commit.as_deref()?, source.path.as_deref()?)?;
    std::fs::read(p).ok().filter(|data| sha256_bytes(data) == sha)
}

pub fn at_commit(file: &Path, commit: &str) -> R<(Vec<u8>, String)> {
    let (top, rel) = repo_and_rel(file)
        .ok_or_else(|| Unresolved::new("E_SOURCE_UNRESOLVED", format!("{} is not in a git repository, so it cannot be read at commit {commit}", crate::sys::slashed(&file))).unread())?;
    let full = git(&top, &["rev-parse", "--verify", "-q", &format!("{commit}^{{commit}}")])
        .ok_or_else(|| Unresolved::new("E_SOURCE_UNRESOLVED", format!("commit {commit} is not in the repository at {}", crate::sys::slashed(&top))).unread())?;
    let full = full.trim().to_string();
    let data = git_bytes(&top, &["show", &format!("{full}:{rel}")]).ok_or_else(|| {
        Unresolved::new("E_SOURCE_UNRESOLVED", format!("{rel} does not exist at commit {} in {}", commit.chars().take(12).collect::<String>(), crate::sys::slashed(&top)))
            .unread()
    })?;
    Ok((data, full))
}

/// HEAD of the file's own repository when the working-tree file is tracked and identical to HEAD's copy (or is that
/// copy as a git that converts line endings checks it out: the same text with `\r\n`).
pub fn committed_as(file: &Path, data: &[u8]) -> Option<String> {
    let (top, rel) = repo_and_rel(file)?;
    let head = git(&top, &["rev-parse", "--verify", "-q", "HEAD"])?;
    let kept = git_bytes(&top, &["show", &format!("HEAD:{rel}")])?;
    if kept == data || other_line_ending(data).map_or(false, |twin| twin == kept) {
        Some(head.trim().to_string())
    } else {
        None
    }
}

pub fn read_file(root: &Path, path: &str, commit: Option<&str>) -> R<(Vec<u8>, Option<String>)> {
    let p = full_path(root, path);
    if let Some(c) = commit.filter(|c| !c.is_empty()) {
        let (d, full) = at_commit(&p, c)?;
        return Ok((d, Some(full)));
    }
    if !p.is_file() {
        return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{path} does not exist")).unread());
    }
    let file = on_disk(&p).map_err(|e| Unresolved::new("E_SOURCE_UNRESOLVED", format!("{path} could not be read: {}", crate::sys::os_words(&e))).unread())?;
    let c = committed_on_disk(&p, &file.0);
    Ok((file.0.clone(), c))
}

/// These bytes, or their twin with the other line ending, when that is byte for byte the file `sha` was taken of.
fn as_file_of(data: Vec<u8>, sha: &str) -> Option<Vec<u8>> {
    if sha256_bytes(&data) == sha {
        return Some(data);
    }
    other_line_ending(&data).filter(|twin| sha256_bytes(twin) == sha)
}

/// The file rb verified at `commit`, by its recorded digest: export's kept copy first (in a bundle inside a repository,
/// git's file at that path is an earlier bundle's), then git, then the working copy (a shallow clone); else git's own.
pub fn verified_at_commit(root: &Path, path: &str, commit: &str, source: &Source, verified: &Resolution) -> R<(Vec<u8>, Option<String>)> {
    let sha = verified.sha256.as_str();
    if let Some(d) = kept_file(root, source, sha) {
        return Ok((d, verified.commit.clone()));
    }
    let file = full_path(root, path);
    let from_git = match at_commit(&file, commit) {
        Ok((d, full)) if is_file_of(&d, sha) => return Ok((d, Some(full))),
        other => other,
    };
    match std::fs::read(&file).ok().and_then(|d| as_file_of(d, sha)) {
        Some(d) => Ok((d, verified.commit.clone())),
        None => from_git.map(|(d, full)| (d, Some(full))),
    }
}

/// A source's file as a check reads it; at a commit rb verified it at, through `verified_at_commit`.
pub fn read_source(root: &Path, path: &str, source: &Source, verified: Option<&Resolution>) -> R<(Vec<u8>, Option<String>)> {
    match (source.commit.as_deref().filter(|c| !c.is_empty()), verified) {
        (Some(c), Some(v)) => verified_at_commit(root, path, c, source, v),
        _ => read_file(root, path, source.commit.as_deref()),
    }
}

/// Lines as git and editors number them: split on newline only, a trailing CR removed, no empty last line.
pub fn split_lines(text: &str) -> Vec<String> {
    let mut lines: Vec<&str> = text.split('\n').collect();
    if lines.last() == Some(&"") {
        lines.pop();
    }
    lines.iter().map(|l| l.strip_suffix('\r').unwrap_or(l).to_string()).collect()
}

// ---------------------------------------------------------------- structured files

pub fn suffix_lower(path: &str) -> String {
    let name = Path::new(path).file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default();
    // Path.suffix: from the last dot, unless the name starts with it and has no other
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => name[i..].to_lowercase(),
        _ => String::new(),
    }
}

pub fn structured_kind(path: &str) -> Option<&'static str> {
    match suffix_lower(path).as_str() {
        ".yaml" | ".yml" => Some("yaml"),
        ".json" => Some("json"),
        ".toml" => Some("toml"),
        _ => None,
    }
}

fn toml_value(v: &toml::Value) -> Value {
    match v {
        toml::Value::String(s) => Value::Str(s.clone()),
        toml::Value::Integer(i) => Value::Int(*i),
        toml::Value::Float(f) => Value::Float(*f),
        toml::Value::Boolean(b) => Value::Bool(*b),
        toml::Value::Datetime(d) => Value::Str(d.to_string()),
        toml::Value::Array(a) => Value::List(a.iter().map(toml_value).collect()),
        toml::Value::Table(t) => Value::Dict(t.iter().map(|(k, v)| (k.clone(), toml_value(v))).collect()),
    }
}

pub fn parse_structured(text: &str, kind: &str, name: &str) -> R<Value> {
    let bad = |msg: String| {
        let first = msg.lines().next().unwrap_or("").chars().take(160).collect::<String>();
        Unresolved::new("E_SOURCE_UNRESOLVED", format!("{name} does not parse as {}: {first}", kind.to_uppercase())).unread()
    };
    match kind {
        "json" => pyjson::loads(text).map_err(|e| bad(e.to_string())),
        "toml" => text.parse::<toml::Table>().map(|t| toml_value(&toml::Value::Table(t))).map_err(|e| bad(e.to_string())),
        _ => crate::pyyaml::load(text).map_err(bad),
    }
}

/// `optim.lr` or `/optim/lr` (a JSON pointer, for keys that themselves contain dots).
pub fn key_steps(key: &str) -> Vec<String> {
    if let Some(rest) = key.strip_prefix('/') {
        return rest.split('/').map(|s| s.replace("~1", "/").replace("~0", "~")).collect();
    }
    key.split('.').map(|s| s.to_string()).collect()
}

/// The value at a key path; Err(the step that does not exist).
pub fn get_key<'a>(doc: &'a Value, key: &str) -> Result<&'a Value, String> {
    let mut cur = doc;
    for step in key_steps(key) {
        cur = match cur {
            Value::List(items) => {
                let ok = !step.is_empty() && step.bytes().all(|b| b.is_ascii_digit()) && (step == "0" || !step.starts_with('0'));
                if !ok {
                    return Err(step);
                }
                match step.parse::<usize>().ok().and_then(|i| items.get(i)) {
                    Some(v) => v,
                    None => return Err(step),
                }
            }
            Value::Dict(d) => match d.get(&step) {
                Some(v) => v,
                None => return Err(step),
            },
            _ => return Err(step),
        };
    }
    Ok(cur)
}

/// A W&B config.yaml stores each value as {desc, value} and adds _wandb bookkeeping.
pub fn unwrap_wandb(doc: Value) -> Value {
    let wrapped = |v: &Value| -> bool {
        match v {
            Value::Dict(d) => d.contains_key("value") && d.keys().all(|k| k == "desc" || k == "value"),
            _ => false,
        }
    };
    if let Value::Dict(d) = &doc {
        if d.values().any(wrapped) {
            let mut out = Dict::new();
            for (k, v) in d {
                if k.starts_with("_wandb") || k == "wandb_version" {
                    continue;
                }
                out.insert(k.clone(), if wrapped(v) { v.get("value").cloned().unwrap_or(Value::Null) } else { v.clone() });
            }
            return Value::Dict(out);
        }
    }
    doc
}

/// {"optim": {"lr": 1e-4}} -> {"optim.lr": 1e-4}. Lists of scalars stay values.
pub fn flatten(doc: &Value, prefix: &str) -> Dict {
    let mut out = Dict::new();
    match doc {
        Value::Dict(d) => {
            for (k, v) in d {
                let key = if prefix.is_empty() { k.clone() } else { format!("{prefix}.{k}") };
                match v {
                    Value::Dict(inner) if !inner.is_empty() => {
                        for (a, b) in flatten(v, &key) {
                            out.insert(a, b);
                        }
                    }
                    _ => {
                        out.insert(key, v.clone());
                    }
                }
            }
        }
        _ => {
            if !prefix.is_empty() {
                out.insert(prefix.to_string(), doc.clone());
            }
        }
    }
    out
}

pub fn yaml_line_of(text: &str, key: &str) -> Option<usize> {
    crate::pyyaml::line_of(text, &key_steps(key))
}

pub fn yaml_key_at(text: &str, line: usize) -> Option<String> {
    crate::pyyaml::key_at(text, line)
}

/// Whether a config key is the setting's own: the same path, the same last segment, or the source's --term.
pub fn key_names(key: &str, name: &str, term: Option<&str>) -> bool {
    let last = name.split('.').last().unwrap_or("").to_lowercase();
    let steps: Vec<String> = key_steps(key).iter().map(|s| s.to_lowercase()).collect();
    steps.join(".") == name.to_lowercase()
        || steps.last().map(|s| *s == last).unwrap_or(false)
        || (term.map_or(false, |t| !t.is_empty()) && key_token_in(&key_steps(key).join(" "), term.unwrap()))
}

// ---------------------------------------------------------------- "stated"

// a digit separator between two digits is part of the number, as Python, Rust and TOML write one: 48_200
const NUM: &str = r"[-+]?(?:\d+(?:_\d+)*\.(?:\d+(?:_\d+)*)?|\.\d+(?:_\d+)*|\d+(?:_\d+)*)(?:[eE][-+]?\d+(?:_\d+)*)?";
/// What separates a number's groups of three digits: a comma, and LaTeX's {,} and \, (48{,}200 is 48,200).
const GROUP_MARKS: [&str; 3] = [",", "{,}", "\\,"];

fn number_token() -> &'static fancy_regex::Regex {
    static R: OnceLock<fancy_regex::Regex> = OnceLock::new();
    R.get_or_init(|| fancy_regex::Regex::new(&format!(r"(?<![\w.\-+]){NUM}(?!\d|_\d|\.\d|[-+]\d|,\d|\{{,\}}\d|\\,\d)")).unwrap())
}
fn thousands() -> &'static fancy_regex::Regex {
    static R: OnceLock<fancy_regex::Regex> = OnceLock::new();
    R.get_or_init(|| fancy_regex::Regex::new(r"(?<![\w.,\-+])(?<!\{,\})[-+]?\d{1,3}(?:(?:,|\{,\}|\\,)\d{3})+(?![\w.,])(?!\{,\}\d)").unwrap())
}

/// Whether a number found at `start` follows a digit and a group's mark: the 200 of 48,200, 48{,}200 or 48\,200,
/// which is the end of one number, never one of its own.
fn after_group(text: &str, start: usize, digit: fn(char) -> bool) -> bool {
    GROUP_MARKS.iter().any(|mark| text[..start].strip_suffix(mark).and_then(|rest| rest.chars().next_back()).map_or(false, digit))
}

/// A name as the source may write it: `seq_len`, or `seq\_len` as LaTeX and Markdown escape it.
fn name_pattern(name: &str) -> String {
    fancy_regex::escape(name).replace('_', r"\\?_")
}
fn bool_re() -> &'static fancy_regex::Regex {
    static R: OnceLock<fancy_regex::Regex> = OnceLock::new();
    R.get_or_init(|| fancy_regex::Regex::new(r"(?<![\w.\-])(true|false|True|False|TRUE|FALSE)(?![\w.\-])").unwrap())
}
fn group_re() -> &'static regex::Regex {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    R.get_or_init(|| regex::Regex::new(r"[\[\(\{]([^\[\]\(\)\{\}]*)[\]\)\}]").unwrap())
}
/// How a file of one type writes its comments.
#[derive(Clone, Copy, PartialEq, Debug)]
enum Syntax {
    /// Markdown's text, whose only comment is an HTML comment: `markdown_lines` says which lines it holds, and one inside
    /// a line ends there.
    Markdown,
    /// reStructuredText's text, whose comment is a `..` comment: `rst_lines` says which lines it holds.
    Rst,
    /// LaTeX's `%`; a verbatim, lstlisting or minted environment's lines are code (`latex_lines`).
    Latex,
    /// YAML, TOML, Python, shell, and the .txt files pip and CMake read: requirements, constraints and CMakeLists.txt.
    Hash,
    Ini,
    /// C-family, JavaScript, TypeScript, Rust, Go, Java and CSS: `//`, `/*`, and `*` leading a block comment's line.
    CLike,
    /// A type rb does not know, .conf among them (HOCON's `//`, nginx's `#`, an INI file's `;`): every mark above opens a
    /// comment line, and a `#` or `//` ends a line in one.
    Unknown,
    /// Text that names no comment of its own and may hold code rb cannot name: any other .txt file, a web page, a file
    /// with no extension, and a document's code block in a language it does not name or rb does not know. As rb read a
    /// document before: every mark above opens a comment line, and nothing ends one, so `run #3` and `#heads 12` are text.
    Prose,
}

impl Syntax {
    /// In declaration order: `self as usize` indexes it.
    const ALL: [Syntax; 8] = [Syntax::Markdown, Syntax::Rst, Syntax::Latex, Syntax::Hash, Syntax::Ini, Syntax::CLike, Syntax::Unknown, Syntax::Prose];

    fn of(path: &str) -> Syntax {
        let slashed = path.replace('\\', "/").to_lowercase();
        let (dir, name) = slashed.rsplit_once('/').unwrap_or(("", &slashed));
        match suffix_lower(path).as_str() {
            ".md" | ".markdown" | ".qmd" | ".rmd" | ".mdx" => Syntax::Markdown,
            ".rst" => Syntax::Rst,
            ".tex" | ".sty" | ".cls" | ".ltx" => Syntax::Latex,
            ".yaml" | ".yml" | ".toml" | ".py" | ".pyi" | ".sh" | ".bash" | ".zsh" => Syntax::Hash,
            ".txt" if ["requirements", "constraints"].iter().any(|r| name.contains(r) || dir.split('/').any(|d| d == *r)) || name == "cmakelists.txt" => Syntax::Hash,
            ".txt" | ".html" | ".htm" | "" => Syntax::Prose,
            ".ini" | ".cfg" | ".cnf" => Syntax::Ini,
            ".c" | ".h" | ".cc" | ".cpp" | ".cxx" | ".hpp" | ".hh" | ".hxx" | ".cu" | ".cuh" | ".js" | ".mjs" | ".cjs" | ".jsx" | ".ts" | ".tsx"
            | ".mts" | ".cts" | ".rs" | ".go" | ".java" | ".css" => Syntax::CLike,
            _ => Syntax::Unknown,
        }
    }

    /// A code block's lines by the language its fence or directive names (```ini, ```{python}, .. code-block:: c).
    fn of_language(info: &str) -> Syntax {
        let word = info.trim().trim_start_matches(['{', '.']).split(|c: char| c.is_whitespace() || c == ',' || c == '}').next().unwrap_or("");
        match word.to_lowercase().as_str() {
            "python" | "py" | "python3" | "ipython" | "ipython3" | "yaml" | "yml" | "toml" | "bash" | "sh" | "shell" | "zsh" | "r" | "ruby" | "perl" | "make"
            | "makefile" | "cmake" | "dockerfile" => Syntax::Hash,
            "ini" | "cfg" | "dosini" => Syntax::Ini,
            "c" | "h" | "cpp" | "c++" | "cc" | "cxx" | "hpp" | "cuda" | "cu" | "js" | "javascript" | "jsx" | "ts" | "typescript" | "tsx" | "rust" | "rs"
            | "go" | "java" | "css" | "scss" | "kotlin" | "swift" | "scala" | "cs" | "csharp" | "jsonc" => Syntax::CLike,
            "tex" | "latex" => Syntax::Latex,
            _ => Syntax::Prose,
        }
    }

    /// What a comment line starts with, and what starts a comment at a line's end.
    fn marks(self) -> (Option<&'static str>, Option<&'static str>) {
        match self {
            Syntax::Markdown => (None, Some(r"\s*<!--.*?(?:-->|$)")),
            Syntax::Rst => (None, None),
            Syntax::Latex => (Some(r"^\s*%"), Some(r"\s+%.*$")),
            Syntax::Hash => (Some(r"^\s*#"), Some(r"\s+#.*$")),
            Syntax::Ini => (Some(r"^\s*[#;]"), Some(r"\s+[#;].*$")),
            Syntax::CLike => (Some(r"^\s*(?://|/\*|\*)"), Some(r"\s+//.*$")),
            Syntax::Unknown => (Some(r"^\s*(?:#|//|%|;|\*|/\*)"), Some(r"\s+(?:#|//).*$")),
            Syntax::Prose => (Some(r"^\s*(?:#|//|%|;|\*|/\*)"), None),
        }
    }

    fn regexes(self) -> &'static (Option<regex::Regex>, Option<regex::Regex>) {
        static R: OnceLock<Vec<(Option<regex::Regex>, Option<regex::Regex>)>> = OnceLock::new();
        let all = R.get_or_init(|| {
            Syntax::ALL
                .iter()
                .map(|s| {
                    let (line, end) = s.marks();
                    (line.map(|p| regex::Regex::new(p).unwrap()), end.map(|p| regex::Regex::new(p).unwrap()))
                })
                .collect()
        });
        &all[self as usize]
    }

    fn opens_comment(self, line: &str) -> bool {
        self.regexes().0.as_ref().map_or(false, |re| re.is_match(line))
    }

    fn cut_trailing(self, line: &str) -> String {
        match &self.regexes().1 {
            Some(re) => re.replace_all(line, "").into_owned(),
            None => line.to_string(),
        }
    }
}

/// Each line's syntax, and whether a comment holds it: in a document, the comments its structure makes.
fn line_syntaxes(path: &str, lines: &[String]) -> Vec<(Syntax, bool)> {
    match Syntax::of(path) {
        Syntax::Markdown => markdown_lines(lines),
        Syntax::Rst => rst_lines(lines),
        Syntax::Latex => latex_lines(lines),
        file => vec![(file, false); lines.len()],
    }
}

/// LaTeX: a line is a comment when it starts with `%`, or when it starts a comment of the code a verbatim, lstlisting
/// or minted environment holds, read by the language it names (minted's argument, lstlisting's language=) or by every
/// mark rb knows when it names none.
fn latex_lines(lines: &[String]) -> Vec<(Syntax, bool)> {
    static BEGIN: OnceLock<regex::Regex> = OnceLock::new();
    let begin = BEGIN.get_or_init(|| regex::Regex::new(r"\\begin\{(verbatim\*?|Verbatim\*?|BVerbatim|LVerbatim|lstlisting|minted)\}(?:\[([^\]]*)\])?(?:\{([^}]*)\})?").unwrap());
    let mut out = Vec::with_capacity(lines.len());
    // the environment a line sits in, and the syntax of its code
    let mut open: Option<(String, Syntax)> = None;
    for line in lines {
        if let Some((end, syntax)) = &open {
            let syntax = *syntax;
            if line.contains(end.as_str()) {
                open = None;
                out.push((Syntax::Latex, false));
            } else {
                out.push((syntax, false));
            }
            continue;
        }
        if let Some(c) = begin.captures(line).filter(|_| !Syntax::Latex.opens_comment(line)) {
            let end = format!("\\end{{{}}}", &c[1]);
            let language = match &c[1] {
                "minted" => c.get(3).map(|m| m.as_str().to_string()),
                "lstlisting" => c.get(2).and_then(|o| o.as_str().split(',').find_map(|kv| kv.trim().strip_prefix("language=").map(|l| l.trim().to_string()))),
                _ => None,
            };
            if !line[c.get(0).map_or(0, |m| m.end())..].contains(end.as_str()) {
                open = Some((end, language.map_or(Syntax::Prose, |l| Syntax::of_language(&l))));
            }
        }
        out.push((Syntax::Latex, false));
    }
    out
}

/// How far a line is indented (a tab reaching the next multiple of four), and the line after its indent.
fn indented(line: &str) -> (usize, &str) {
    let mut width = 0;
    for (i, c) in line.char_indices() {
        match c {
            ' ' => width += 1,
            '\t' => width += 4 - width % 4,
            _ => return (width, &line[i..]),
        }
    }
    (width, "")
}

/// Where a Markdown list item's text starts, when the line opens one: `-`, `+`, `*` or a number and `.` or `)`, then a
/// space, so `**bold**` opens none, nor does a thematic break.
fn list_item(indent: usize, bare: &str) -> Option<usize> {
    static ITEM: OnceLock<regex::Regex> = OnceLock::new();
    static BREAK: OnceLock<regex::Regex> = OnceLock::new();
    let item = ITEM.get_or_init(|| regex::Regex::new(r"^([-+*]|\d{1,9}[.)])( +|\t|$)").unwrap());
    if BREAK.get_or_init(|| regex::Regex::new(r"^(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$").unwrap()).is_match(bare.trim_end()) {
        return None;
    }
    let c = item.captures(bare)?;
    let gap = c[2].len();
    Some(indent + c[1].len() + if (1..=4).contains(&gap) { gap } else { 1 })
}

/// Markdown, by the smallest rule that holds rather than a parser. A line is a comment only when it lies in an HTML
/// comment that opens at the start of a line (`<!--` indented at most three spaces past the list item it sits in) and
/// runs to the line holding its `-->`, or when it starts a comment of a code block's language: a block fenced by three
/// or more backticks or tildes reads by the language its fence names, and one that names none, or an indented block
/// (four spaces past the list item it sits in, after anything but a paragraph's text), by every mark rb knows. Front
/// matter (--- or +++ at the top) reads as YAML or TOML. A heading, a list item and a bold line are text, and so is a
/// line that only mentions `<!--`. A list item holds the lines indented as far as its text, and a line indented short
/// of it that carries on a paragraph.
fn markdown_lines(lines: &[String]) -> Vec<(Syntax, bool)> {
    static HEADING: OnceLock<regex::Regex> = OnceLock::new();
    let heading_re = HEADING.get_or_init(|| regex::Regex::new(r"^#{1,6}(?:[ \t]|$)").unwrap());
    let mut out = Vec::with_capacity(lines.len());
    let top = lines.first().map(|l| l.trim_end()).filter(|l| *l == "---" || *l == "+++");
    let front = top.and_then(|mark| lines.iter().skip(1).position(|l| l.trim_end() == mark || (mark == "---" && l.trim_end() == "...")));
    let mut fence: Option<(char, usize, Syntax)> = None;
    let mut in_comment = false;
    // the text column of each list item a line may sit in, outermost first
    let mut items: Vec<usize> = Vec::new();
    // whether the line before was a paragraph's text, which the next line may carry on
    let mut para = false;
    for (i, line) in lines.iter().enumerate() {
        if let Some(end) = front.filter(|end| i <= end + 1) {
            out.push((if i == 0 || i == end + 1 { Syntax::Markdown } else { Syntax::Hash }, false));
            continue;
        }
        let (indent, bare) = indented(line);
        let run = |c: char| bare.chars().take_while(|x| *x == c).count();
        if let Some((c, n, syntax)) = fence {
            if run(c) >= n && bare[run(c)..].trim().is_empty() {
                fence = None;
            }
            out.push((syntax, false));
            continue;
        }
        if in_comment {
            in_comment = !line.contains("-->");
            out.push((Syntax::Markdown, true));
            continue;
        }
        if bare.trim().is_empty() {
            para = false;
            out.push((Syntax::Markdown, false));
            continue;
        }
        // the open items the line reaches into, and how far it is indented past the innermost of them
        let inside = items.iter().take_while(|at| indent >= **at).count();
        let rel = indent - inside.checked_sub(1).map_or(0, |k| items[k]);
        // a backtick fence's info string holds no backtick: ```x``` is inline code
        let opens = ['`', '~'].into_iter().find(|c| rel <= 3 && run(*c) >= 3 && (*c == '~' || !bare[run(*c)..].contains('`')));
        let item = if rel <= 3 { list_item(indent, bare) } else { None };
        let comment = rel <= 3 && bare.starts_with("<!--");
        let heading = rel <= 3 && heading_re.is_match(bare);
        let carries_on = para && item.is_none() && opens.is_none() && !comment && !heading;
        if !carries_on {
            items.truncate(inside);
        }
        if let Some(text_at) = item {
            items.push(text_at);
            para = true;
            out.push((Syntax::Markdown, false));
        } else if carries_on {
            out.push((Syntax::Markdown, false));
        } else if let Some(c) = opens {
            fence = Some((c, run(c), Syntax::of_language(&bare[run(c)..])));
            para = false;
            out.push((Syntax::Markdown, false));
        } else if comment {
            in_comment = !bare[4..].contains("-->");
            para = false;
            out.push((Syntax::Markdown, true));
        } else if rel >= 4 {
            para = false;
            out.push((Syntax::Prose, false));
        } else {
            para = !heading;
            out.push((Syntax::Markdown, false));
        }
    }
    out
}

/// reStructuredText, the same way. A line is a comment when it lies in a `..` comment (one that is no directive,
/// target, footnote, citation or substitution) or the lines indented under it, or when it starts a comment of a
/// literal block's language: the lines indented under a code-block, code or sourcecode directive read by the language
/// it names, and those under a line ending in `::` by every mark rb knows. A list item, a bold line, a note's text and
/// a Sphinx directive's (.. py:function::) are text.
fn rst_lines(lines: &[String]) -> Vec<(Syntax, bool)> {
    static COMMENT: OnceLock<fancy_regex::Regex> = OnceLock::new();
    static DIRECTIVE: OnceLock<regex::Regex> = OnceLock::new();
    let comment_re = COMMENT.get_or_init(|| fancy_regex::Regex::new(r"^\.\.(?:[ \t]*$|[ \t]+(?![ \t_\[|]|[\w.+:-]+::))").unwrap());
    let directive_re = DIRECTIVE.get_or_init(|| regex::Regex::new(r"^\.\.[ \t]+([\w.+:-]+)::(.*)$").unwrap());
    let mut out = Vec::with_capacity(lines.len());
    // the indent of the line that opened a comment, a literal block, or a literal block whose lines are still to come
    let mut comment: Option<usize> = None;
    let (mut literal, mut pending): (Option<(usize, Syntax)>, Option<(usize, Syntax)>) = (None, None);
    for line in lines {
        let (indent, bare) = indented(line);
        if bare.trim().is_empty() {
            out.push((literal.map_or(Syntax::Rst, |(_, s)| s), comment.is_some()));
            continue;
        }
        if comment.is_some_and(|at| indent > at) {
            out.push((Syntax::Rst, true));
            continue;
        }
        comment = None;
        if let Some((at, syntax)) = literal.or(pending).filter(|(at, _)| indent > *at) {
            (literal, pending) = (Some((at, syntax)), None);
            out.push((syntax, false));
            continue;
        }
        (literal, pending) = (None, None);
        if comment_re.is_match(bare).unwrap_or(false) {
            comment = Some(indent);
            out.push((Syntax::Rst, true));
            continue;
        }
        let code = match directive_re.captures(bare) {
            Some(d) => match &d[1] {
                "code-block" | "code" | "sourcecode" | "code-cell" => Some(Syntax::of_language(&d[2])),
                // directives whose body is Python, IPython's and Sphinx's own, unless they name another language
                "ipython" | "testcode" | "testsetup" | "testcleanup" | "doctest" | "jupyter-execute" | "plot" => {
                    Some(if d[2].trim().is_empty() { Syntax::Hash } else { Syntax::of_language(&d[2]) })
                }
                _ => None,
            },
            None => bare.trim_end().ends_with("::").then_some(Syntax::Prose),
        };
        if let Some(syntax) = code {
            pending = Some((indent, syntax));
        }
        out.push((Syntax::Rst, false));
    }
    out
}

/// Which of a file's lines are comments, by its type's own syntax: a Markdown heading, list item or bold line is text,
/// as a line of a reStructuredText note is; LaTeX's comment is `%`, YAML's and requirements.txt's `#`, C's `//`.
pub fn comment_lines(path: &str, lines: &[String]) -> Vec<bool> {
    line_syntaxes(path, lines).into_iter().zip(lines).map(|((syntax, inside), line)| inside || syntax.opens_comment(line)).collect()
}

/// Where `quote` is in `text` as the text has it: each place it neither begins nor ends inside a word or a number
/// there, so `batch size of 3` is not in "batch size of 32", nor `momentum: 0.9` in "momentum: 0.95", nor `0.1` in
/// "-0.1".
pub fn quote_places<'a>(text: &'a str, quote: &'a str) -> impl Iterator<Item = usize> + 'a {
    let mut from = 0;
    std::iter::from_fn(move || {
        while !quote.is_empty() {
            let at = from + text.get(from..)?.find(quote)?;
            from = at + text[at..].chars().next().map_or(1, |c| c.len_utf8());
            if cuts_nothing(text, at, at + quote.len()) {
                return Some(at);
            }
        }
        None
    })
}

pub fn holds_quote(text: &str, quote: &str) -> bool {
    quote_places(text, quote).next().is_some()
}

/// Whether `text[start..end]` begins and ends where each word and number of its lines does.
fn cuts_nothing(text: &str, start: usize, end: usize) -> bool {
    let from = text[..start].rfind('\n').map_or(0, |i| i + 1);
    let to = text[end..].find('\n').map_or(text.len(), |i| end + i);
    // a minus sign takes three bytes, as two spaces and '-' do, so every place stays where it was
    let lines = text[from..to].replace('\u{2212}', "  -");
    let (s, e) = (start - from, end - from);
    static WORD: OnceLock<regex::Regex> = OnceLock::new();
    let words = WORD.get_or_init(|| regex::Regex::new(r"\w+").unwrap()).find_iter(&lines).map(|m| (m.start(), m.end()));
    let numbers = matches_fancy(number_token(), &lines).into_iter().chain(matches_fancy(thousands(), &lines)).map(|(a, b, _)| (a, b));
    !words.chain(numbers).any(|(x, y)| (x < s && s < y) || (x < e && e < y))
}

/// The line a quote is on: its first place on a line that is no comment, else its first.
pub fn quoted_line(text: &str, quote: &str, comments: &[bool]) -> Option<usize> {
    let mut first = None;
    for idx in quote_places(text, quote) {
        let no = text[..idx].matches('\n').count() + 1;
        if !comments.get(no - 1).copied().unwrap_or(false) {
            return Some(no);
        }
        first = first.or(Some(no));
    }
    first
}

/// A line of the file without the comment that ends it, by the syntax that line is read in.
pub fn cut_trailing_comment(path: &str, lines: &[String], idx: usize) -> String {
    let syntax = line_syntaxes(path, lines).get(idx).map_or(Syntax::of(path), |(s, _)| *s);
    lines.get(idx).map_or_else(String::new, |line| syntax.cut_trailing(line))
}

/// Every line of the file without the comment that ends it, the file read once.
pub fn cut_trailing_comments(path: &str, lines: &[String]) -> Vec<String> {
    line_syntaxes(path, lines).into_iter().zip(lines).map(|((syntax, _), line)| syntax.cut_trailing(line)).collect()
}

/// Which of a document's lines are comments, and each line without the comment that ends it, as an analysis's numbers
/// are read: by a syntax with comments of its own. A log, a table, a text file and output in a block that names no
/// language have none, so their lines are read whole, as rb read every analysis's source before (` * Acc@1 81.800` is
/// what an eval printed).
pub fn stated_lines(path: &str, lines: &[String]) -> (Vec<bool>, Vec<String>) {
    line_syntaxes(path, lines)
        .into_iter()
        .zip(lines)
        .map(|((syntax, inside), line)| match syntax {
            Syntax::Unknown | Syntax::Prose => (false, line.clone()),
            _ => (inside || syntax.opens_comment(line), syntax.cut_trailing(line)),
        })
        .unzip()
}

fn num_full() -> &'static regex::Regex {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    R.get_or_init(|| regex::Regex::new(&format!("^(?:{NUM})$")).unwrap())
}

fn matches_fancy(re: &fancy_regex::Regex, text: &str) -> Vec<(usize, usize, String)> {
    let mut out = Vec::new();
    for m in re.find_iter(text).flatten() {
        out.push((m.start(), m.end(), m.as_str().to_string()));
    }
    out
}

/// A count's suffix right after a number, as `999M tokens`, `1.5B` and `30K steps` write one: the power of ten it
/// stands for. A letter that runs on into a word (`512MB`, `3Mbps`) is no suffix.
fn count_suffix(rest: &str) -> Option<i32> {
    let mut chars = rest.chars();
    let power = match chars.next()? {
        'K' | 'k' => 3,
        'M' => 6,
        'B' => 9,
        _ => return None,
    };
    if chars.next().map_or(false, |c| c.is_alphanumeric() || c == '_') {
        return None;
    }
    Some(power)
}

/// A value written as a count with its suffix and nothing else (`999M`, `1.5B`, `30K`): the count, else None.
pub fn count_value(text: &str) -> Option<Value> {
    static COUNT: OnceLock<regex::Regex> = OnceLock::new();
    let c = COUNT.get_or_init(|| regex::Regex::new(r"^([-+]?\d+(?:\.\d+)?)([KkMB])$").unwrap()).captures(text.trim())?;
    number_at(c.get(1)?.as_str(), c.get(2)?.as_str())
}

/// The number a token reads as, with the count suffix that follows it in `rest`: 999 before `M` is 999000000.
fn number_at(token: &str, rest: &str) -> Option<Value> {
    let Some(power) = count_suffix(rest).filter(|_| !token.contains(['e', 'E'])) else { return parse_number(token) };
    let t = token.replace("{,}", "").replace("\\,", "").replace([',', '_'], "");
    let x = pyfloat::parse_float(&format!("{t}e{power}"))?;
    if x.fract() == 0.0 && x.abs() < 9007199254740992.0 {
        return Some(Value::Int(x as i64));
    }
    Some(Value::Float(x))
}

pub fn parse_number(token: &str) -> Option<Value> {
    let t = token.replace("{,}", "").replace("\\,", "").replace([',', '_'], "");
    static INT: OnceLock<regex::Regex> = OnceLock::new();
    if INT.get_or_init(|| regex::Regex::new(r"^[-+]?\d+$").unwrap()).is_match(&t) {
        return pyfloat::parse_int(&t);
    }
    pyfloat::parse_float(&t).map(Value::Float)
}

/// An integer equals only an integral number of the same value; a float equals the same double.
pub fn num_equal(value: &Value, found: &Value) -> bool {
    if value.is_bool() || found.is_bool() {
        return false;
    }
    match value {
        Value::Int(_) | Value::BigInt(_) => match found {
            Value::Int(_) | Value::BigInt(_) => found == value,
            Value::Float(f) => f.fract() == 0.0 && f.is_finite() && f.abs() < 9007199254740992.0 && Value::Int(*f as i64) == *value,
            _ => false,
        },
        Value::Float(v) => match found.as_f64() {
            Some(x) if found.is_number() => x == *v,
            _ => false,
        },
        _ => false,
    }
}

/// Every standalone number in `text`.
pub fn numbers(text: &str) -> Vec<Value> {
    let text = text.replace('\u{2212}', "-");
    let mut out = Vec::new();
    for (start, end, tok) in matches_fancy(number_token(), &text) {
        if after_group(&text, start, |c| c.is_numeric() && c.to_digit(10).is_some()) {
            continue;
        }
        if let Some(v) = number_at(&tok, &text[end..]) {
            out.push(v);
        }
    }
    for (_s, end, tok) in matches_fancy(thousands(), &text) {
        if let Some(v) = number_at(&tok, &text[end..]) {
            out.push(v);
        }
    }
    out
}

fn py_isdigit(c: char) -> bool {
    c.to_digit(10).is_some() || (c.is_numeric() && !c.is_ascii())
}

/// The number closest to where the line names the setting; a number after the name wins a tie.
pub fn nearest_number(text: &str, token: &str) -> Option<Value> {
    let text = text.replace('\u{2212}', "-");
    let pat = format!(r"(?i)(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])", name_pattern(token));
    let re = fancy_regex::Regex::new(&pat).ok()?;
    let names: Vec<(usize, usize)> = re.find_iter(&text).flatten().map(|m| (cidx(&text, m.start()), cidx(&text, m.end()))).collect();
    let mut spans: Vec<((usize, usize), Value)> = Vec::new();
    for (start, end, tok) in matches_fancy(number_token(), &text) {
        if after_group(&text, start, py_isdigit) {
            continue;
        }
        if let Some(v) = number_at(&tok, &text[end..]) {
            spans.push(((cidx(&text, start), cidx(&text, end)), v));
        }
    }
    for (start, end, tok) in matches_fancy(thousands(), &text) {
        if let Some(v) = number_at(&tok, &text[end..]) {
            spans.push(((cidx(&text, start), cidx(&text, end)), v));
        }
    }
    let mut best: Option<((i64, i64), Value)> = None;
    for ((a, b), n) in &spans {
        for (x, y) in &names {
            let (a, b, x, y) = (*a as i64, *b as i64, *x as i64, *y as i64);
            let gap = if a >= y { a - y } else { x - b };
            let key = (gap, if a >= y { 0 } else { 1 });
            if gap >= 0 && best.as_ref().map_or(true, |(k, _)| key < *k) {
                best = Some((key, n.clone()));
            }
        }
    }
    best.map(|(_, n)| n)
}

fn cidx(text: &str, byte: usize) -> usize {
    text[..byte].chars().count()
}

fn text_token(value: &str, text: &str) -> bool {
    if py_strip(value).is_empty() {
        return false;
    }
    let pat = format!(r"(?<![\w.\-]){}(?![\w.\-])", name_pattern(value));
    fancy_regex::Regex::new(&pat).ok().map_or(false, |re| re.is_match(text).unwrap_or(false))
}

fn item_is(item: &str, value: &Value) -> bool {
    let s = py_strip(py_strip(item).trim_matches(|c| c == '\'' || c == '"')).to_string();
    match value {
        Value::Bool(b) => s.to_lowercase() == if *b { "true" } else { "false" },
        Value::Int(_) | Value::BigInt(_) | Value::Float(_) => num_full().is_match(&s) && parse_number(&s).map_or(false, |n| num_equal(value, &n)),
        Value::Str(v) => !py_strip(v).is_empty() && (s == *v || s.replace("\\_", "_") == *v),
        _ => false,
    }
}

fn list_stated(text: &str, value: &[Value]) -> bool {
    let mut candidates: Vec<String> = group_re().captures_iter(text).map(|c| c.get(1).map_or("", |m| m.as_str()).to_string()).collect();
    static SPLIT: OnceLock<regex::Regex> = OnceLock::new();
    let parts: Vec<&str> = SPLIT.get_or_init(|| regex::Regex::new(r"[:=]").unwrap()).split(text).collect();
    candidates.push(parts.last().copied().unwrap_or("").to_string());
    for cand in candidates {
        let mut items: Vec<&str> = cand.split(',').collect();
        if !items.is_empty() && py_strip(items.last().unwrap()).is_empty() {
            items.pop();
        }
        if items.len() == value.len() && items.iter().zip(value.iter()).all(|(i, v)| item_is(i, v)) {
            return true;
        }
    }
    false
}

/// Whether `text` states `value`.
pub fn states(text: &str, value: &Value) -> bool {
    match value {
        Value::List(l) => !l.is_empty() && list_stated(text, l),
        Value::Bool(b) => {
            let want = if *b { "true" } else { "false" };
            bool_re().find_iter(text).flatten().any(|m| m.as_str().to_lowercase() == want)
        }
        Value::Int(_) | Value::BigInt(_) | Value::Float(_) => numbers(text).iter().any(|n| num_equal(value, n)),
        Value::Str(s) => text_token(s, text),
        _ => false,
    }
}

/// A setting's value against a value read from a file or run.
pub fn equal(a: &Value, b: &Value) -> bool {
    if a.is_list() || b.is_list() {
        return match (a, b) {
            (Value::List(x), Value::List(y)) => x.len() == y.len() && x.iter().zip(y.iter()).all(|(p, q)| equal(p, q)),
            _ => false,
        };
    }
    if a.is_bool() || b.is_bool() {
        return matches!((a, b), (Value::Bool(x), Value::Bool(y)) if x == y);
    }
    if a.is_number() && b.is_number() {
        return num_equal(a, b);
    }
    a == b
}

/// The setting is named on the line: `lr` in `lr: 0.001`; a term such as "learning rate" anywhere, ignoring case.
pub fn key_token_in(line: &str, token: &str) -> bool {
    let pat = format!(r"(?i)(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])", name_pattern(token));
    fancy_regex::Regex::new(&pat).ok().map_or(false, |re| re.is_match(line).unwrap_or(false))
}

/// RFC 6901. Err(the step that does not exist).
pub fn pointer_get<'a>(doc: &'a Value, pointer: &str) -> Result<&'a Value, String> {
    if pointer.is_empty() {
        return Ok(doc);
    }
    if !pointer.starts_with('/') {
        return Err(pointer.to_string());
    }
    get_key(doc, pointer)
}

pub fn run_json_path(root: &Path, path: &str) -> PathBuf {
    let p = full_path(root, path);
    if p.is_dir() {
        p.join("record.json")
    } else {
        p
    }
}

/// json.dumps(value) for anything but text, which is itself.
pub fn render(value: &Value) -> String {
    match value {
        Value::Str(s) => s.clone(),
        other => pyjson::dumps(other),
    }
}

/// What a source states and the value given, side by side: text in quotes when the two would read alike ("400", not 400).
fn beside(found: &Value, value: &Value) -> (String, String) {
    let (a, b) = (render(found), render(value));
    if a == b {
        return (pyjson::dumps(found), pyjson::dumps(value));
    }
    (a, b)
}

/// The two values a conflict says, as rb says them: what the source states (something else, when rb read no value in
/// it) and what the record says.
fn conflict_values(value: &Value, found: &Value) -> (String, String) {
    if found.is_null() {
        ("something else".to_string(), render(value))
    } else {
        beside(found, value)
    }
}

fn conflict_says(value: &Value, found: &Value, at: &str, reads: &str, saw: bool) -> String {
    let (says, was) = conflict_values(value, found);
    let reads = if reads.is_empty() { String::new() } else { format!("; it reads: {reads}") };
    let now = if saw { "now " } else { "" };
    format!("the record says {was}, but {at} {now}says {says}{reads}")
}

/// A setting's kept conflict says its source changed: rb had seen the source state the record's value. Read in rb's
/// own words, found by the two values as rb says them: the first "says" followed by the source's value and then the
/// end of the text or the line rb quotes, so nothing the source states, the line rb quotes or a place's --locator reads
/// as rb's. One kept cut to its first 400 characters is read as far as it goes.
pub fn said_changed(s: &Setting) -> bool {
    let Some(c) = &s.conflict else { return false };
    let (says, was) = conflict_values(&s.value, &c.read);
    let Some(rest) = c.text.strip_prefix(&format!("the record says {was}, but ")) else { return false };
    let cut = c.text.chars().count() >= 400;
    let rb_says = |tail: &str| match tail.strip_prefix(says.as_str()) {
        Some(after) => after.is_empty() || after.starts_with("; it reads: ") || (cut && "; it reads: ".starts_with(after)),
        None => cut && says.starts_with(tail),
    };
    rest.match_indices(" says ").map(|(i, _)| i).find(|i| rb_says(&rest[i + " says ".len()..])).map_or(false, |i| rest[..i].ends_with(" now"))
}

/// A setting's stored conflict, said the same way. One an earlier rb stored said the source's value first ("f#lr is
/// 0.3, not 0.1", "f:1 gives lr 0.3, not 0.1; it reads: ..."), so it is said again from what was read, never as a
/// change, which it did not say.
pub fn said_conflict(s: &Setting) -> Option<String> {
    let c = s.conflict.as_ref()?;
    match &s.source {
        Some(src) if !c.text.starts_with("the record says ") => {
            let (head, reads) = c.text.split_once("; it reads: ").unwrap_or((&c.text, ""));
            let line = [" gives ", " does not state "].iter().find_map(|w| head.split_once(w)).filter(|_| !reads.is_empty());
            Some(conflict_says(&s.value, &c.read, &line.map_or_else(|| src.label(), |(at, _)| at.to_string()), reads, false))
        }
        _ => Some(c.text.clone()),
    }
}

// ---------------------------------------------------------------- resolving

/// Read the source and confirm it states `value` for the setting `name`.
pub fn resolve(source: &Source, value: &Value, root: &Path, name: &str, previous: Option<&Resolution>, named: bool) -> R<Resolution> {
    resolve_inner(source, value, root, name, previous, named, false)
}

fn resolution(at: String, commit: Option<String>, sha: String, line: Option<i64>, text: String, read: Value) -> Resolution {
    Resolution { at, commit, sha256: sha, line, text, read }
}

fn trunc(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// `as_before`: read comments as an earlier rb read them in every file, a line opening with any mark rb knows, and a #
/// or // ending a line outside prose.
fn resolve_inner(source: &Source, value: &Value, root: &Path, name: &str, previous: Option<&Resolution>, named: bool, as_before: bool) -> R<Resolution> {
    if !source.checkable() {
        if source.kind == "file" {
            return Err(Unresolved::new("E_SOURCE_UNVERIFIABLE", "a file source needs a key path (file#key), a line (file:LINE) or a quote for rb to check it"));
        }
        return Err(Unresolved::new(
            "E_SOURCE_UNVERIFIABLE",
            format!("a {} source records where the value came from but rb cannot check it; save the page in the repository and point at it", source.kind),
        ));
    }
    let path = source.path.clone().unwrap_or_default();
    if source.path.as_deref().map_or(false, |p| !p.is_empty()) && !inside_project(root, &path) {
        return Err(Unresolved::new(
            "E_SOURCE_OUTSIDE",
            format!("{path} is outside the project, so nobody else can check it: copy it into the repository and commit it"),
        )
        .unread());
    }
    if source.path.as_deref().map_or(false, |p| !p.is_empty()) && in_state(root, &path) {
        return Err(Unresolved::new("E_SOURCE_OUTSIDE", format!("{path} is rb's own record in .rb/, which cannot be where a value comes from")).unread());
    }
    let pointer = source.pointer.clone().unwrap_or_else(|| "None".into());
    if source.kind == "run" {
        let step = key_steps(&pointer).last().cloned().unwrap_or_default();
        if named && !step.is_empty() && !key_names(&pointer, name, source.term.as_deref()) {
            return Err(Unresolved::new(
                "E_SOURCE_UNRESOLVED",
                format!(
                    "{path}#{pointer} is {}, not {name}: point at {name}'s own entry, or give --term with the name the file uses",
                    repr_str(&step)
                ),
            ));
        }
        let p = run_json_path(root, &path);
        if !p.is_file() {
            return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{path} does not exist")).unread());
        }
        let data = std::fs::read(&p).map_err(|e| io_unresolved(source, e))?;
        let doc = parse_structured(&String::from_utf8_lossy(&data), "json", &path)?;
        let got = pointer_get(&doc, &pointer)
            .map_err(|step| Unresolved::new("E_SOURCE_UNRESOLVED", format!("{} has nothing at {pointer} (no '{step}')", crate::sys::slashed(&p))))?
            .clone();
        if !equal(value, &got) {
            let (is, not) = beside(&got, value);
            return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{path}{pointer} is {is}, not {not}")).found(got));
        }
        return Ok(resolution(now(), committed_as(&p, &data), sha256_bytes(&data), None, trunc(&pyjson::dumps(&got), 400), got));
    }
    if let Some(key) = source.key.as_deref().filter(|_| crate::sqlite::is_database(&full_path(root, &path))) {
        return resolve_row(source, value, root, &path, key, name, named);
    }
    let (data, commit) = read_source(root, &path, source, previous)?;
    let text = String::from_utf8_lossy(&data).into_owned();
    let kind = structured_kind(&path);
    if let Some(key) = &source.key {
        let kind = kind.ok_or_else(|| {
            Unresolved::new(
                "E_SOURCE_UNRESOLVED",
                format!("a key path needs a YAML, JSON or TOML file; {path} is none of those. Use {path}:LINE or --quote"),
            )
        })?;
        if named && !key_names(key, name, source.term.as_deref()) {
            return Err(Unresolved::new(
                "E_SOURCE_UNRESOLVED",
                format!("{path}#{key} is not {name}: point at {name}'s own key, or give --term with the name the file uses"),
            ));
        }
        let doc = parse_structured(&text, kind, &path)?;
        let got = get_key(&doc, key).map_err(|step| Unresolved::new("E_SOURCE_UNRESOLVED", format!("{path} has no key {key} (no '{step}')")))?.clone();
        if (got.is_dict() || got.is_list()) && !value.is_list() {
            return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{path}#{key} is a section, not a value")));
        }
        if !equal(value, &got) {
            let (is, not) = beside(&got, value);
            return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{path}#{key} is {is}, not {not}")).found(got));
        }
        let line = if kind == "yaml" { yaml_line_of(&text, key).map(|l| l as i64) } else { None };
        return Ok(resolution(now(), commit, sha256_bytes(&data), line, trunc(&format!("{key}: {}", render(&got)), 400), got));
    }
    let lines = split_lines(&text);
    let comments = if as_before { lines.iter().map(|l| Syntax::Unknown.opens_comment(l)).collect() } else { comment_lines(&path, &lines) };
    if let Some(term) = &source.term {
        static LETTER: OnceLock<regex::Regex> = OnceLock::new();
        let has_letter = LETTER.get_or_init(|| regex::Regex::new(r"[A-Za-z]").unwrap()).is_match(term);
        if !has_letter || FUNCTION_WORDS.contains(&py_strip(term).to_lowercase().as_str()) {
            return Err(Unresolved::new(
                "E_SOURCE_UNRESOLVED",
                format!("--term {} names nothing in particular: give the words the text uses for this setting, e.g. \"weight decay\"", repr_str(term)),
            ));
        }
    }
    let token = source.term.clone().unwrap_or_else(|| name.split('.').last().unwrap_or("").to_string());
    let (found_line, found_text) = if source.line.is_some() {
        line_or_moved(source, &lines, previous)?
    } else {
        let quote = source.quote.clone().unwrap_or_default();
        let found_line = match quoted_line(&text, &quote, &comments) {
            Some(no) => no,
            None => {
                let why = match text.find(&quote).map(|at| text[..at].matches('\n').count() + 1) {
                    Some(no) => format!(
                        "the quote is in {path} only as part of a longer word or number; {path}:{no} reads: {}",
                        trunc(py_strip(lines.get(no - 1).map_or("", |l| l.as_str())), 200)
                    ),
                    None => format!("the quote is not in {path}"),
                };
                return Err(Unresolved::new("E_SOURCE_UNRESOLVED", why));
            }
        };
        let found_text = if found_line <= lines.len() { lines[found_line - 1].clone() } else { quote.clone() };
        (found_line, found_text)
    };
    let where_ = format!("{path}:{found_line}");
    let commented = match found_line.checked_sub(1).and_then(|i| comments.get(i)) {
        Some(c) => *c,
        None if as_before => Syntax::Unknown.opens_comment(&found_text),
        None => comment_lines(&path, std::slice::from_ref(&found_text))[0],
    };
    if commented {
        return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{where_} is a comment; a comment does not set a value")).with_candidates(candidates(&lines, &comments, value, &token)));
    }
    let code = !PROSE.contains(&suffix_lower(&path).as_str());
    // a comment that ends the line says nothing, by the syntax the line is read in
    let setting_text = if as_before {
        if code { Syntax::Unknown.cut_trailing(&found_text) } else { found_text.clone() }
    } else if (1..=lines.len()).contains(&found_line) {
        cut_trailing_comment(&path, &lines, found_line - 1)
    } else {
        found_text.clone()
    };
    let cut = setting_text != found_text;
    let haystack: String;
    let mut found_text = found_text;
    if let Some(quote) = source.quote.as_deref().filter(|q| !q.is_empty()) {
        if !holds_quote(&found_text, quote) && source.line.is_some() {
            return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{where_} does not contain the quote; it reads: {}", trunc(py_strip(&found_text), 200))));
        }
        if named && !key_token_in(&setting_text, &token) {
            return Err(Unresolved::new(
                "E_SOURCE_UNRESOLVED",
                format!("{where_} does not name {}: a quote has to be about this setting. Give --term with the word the text uses for it", repr_str(&token)),
            )
            .with_candidates(candidates(&lines, &comments, value, &token)));
        }
        haystack = if (!code && !cut) || holds_quote(&setting_text, quote) { quote.to_string() } else { setting_text.clone() };
    } else {
        found_text = setting_text.clone();
        if !key_token_in(&found_text, &token) {
            return Err(Unresolved::new(
                "E_SOURCE_UNRESOLVED",
                format!(
                    "{where_} does not name {}; it reads: {}. Point at the line that sets it, give --term with the word the file uses, or --quote the exact text",
                    repr_str(&token),
                    trunc(py_strip(&found_text), 200)
                ),
            )
            .with_candidates(candidates(&lines, &comments, value, &token)));
        }
        haystack = found_text.clone();
    }
    if !states(&haystack, value) {
        let read = trunc(py_strip(&haystack), 200);
        let mut u = Unresolved::new("E_SOURCE_UNRESOLVED", format!("{where_} does not state {}; it reads: {read}", render(value))).reads(&where_, read);
        u.differs = key_token_in(&found_text, &token);
        // what the line now gives the setting, when it names it beside a number
        if let Some(near) = nearest_number(&haystack, &token).filter(|n| u.differs && value.is_number() && !num_equal(value, n)) {
            u = u.found(near);
        }
        // a value written 999M is text; the count it writes is the number a quote is read for
        if let Some(n) = value.as_str().and_then(count_value).filter(|n| states(&haystack, n)) {
            u.reason = format!("{where_} states {} as the count {}: give the value as that number", render(value), render(&n));
            u.differs = true;
            u = u.found(n);
        }
        return Err(u.with_candidates(candidates(&lines, &comments, value, &token)));
    }
    if source.quote.as_deref().map_or(true, |q| q.is_empty()) && value.is_number() {
        if let Some(near) = nearest_number(&found_text, &token) {
            if !num_equal(value, &near) {
                let read = trunc(py_strip(&found_text), 200);
                return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{where_} gives {token} {}, not {}; it reads: {read}", render(&near), render(value)))
                    .found(near)
                    .reads(&where_, read)
                    .with_candidates(candidates(&lines, &comments, value, &token)));
            }
        }
    }
    let t = py_strip(&found_text);
    let shown = if t.is_empty() { trunc(py_strip(&haystack), 400) } else { trunc(t, 400) };
    Ok(resolution(now(), commit, sha256_bytes(&data), Some(found_line as i64), shown, Value::Null))
}

/// A column of one row of a SQLite database, as a setting's source names it: `<table>[<column>=<value>,...].<column>`
/// (the brackets pick the row as `--where` does; with none, the table has one row). Split into the table, the row's
/// key and the column; None when the key is not of that form.
pub fn row_key_of(key: &str) -> Option<(String, Vec<String>, String)> {
    let (table, rest) = match key.find('[') {
        Some(at) => {
            let close = key.rfind(']')?;
            if close < at {
                return None;
            }
            let col = key[close + 1..].strip_prefix('.')?;
            return (!key[..at].is_empty() && !col.is_empty()).then(|| (key[..at].to_string(), vec![key[at + 1..close].to_string()], col.to_string()));
        }
        None => key.split_once('.')?,
    };
    (!table.is_empty() && !rest.is_empty()).then(|| (table.to_string(), Vec::new(), rest.to_string()))
}

/// The value one column of one row of a SQLite database holds, read with rb's read-only reader: (the value, what the
/// row and column read, as a line says it). Every refusal says why, as a source that cannot be read does.
pub fn row_cell(root: &Path, path: &str, key: &str) -> R<(Value, String)> {
    let Some((table, given, column)) = row_key_of(key) else {
        return Err(Unresolved::new(
            "E_SOURCE_UNRESOLVED",
            format!("{path} is a SQLite database: name a column of one row, {path}#<table>[<column>=<value>].<column> (got #{key})"),
        ));
    };
    let pairs = crate::cmds::record::where_items(&given)
        .and_then(|items| crate::cmds::parse_pairs(&items, "the row's key", false))
        .map_err(|e| Unresolved::new("E_SOURCE_UNRESOLVED", e.message.clone()))?;
    let pairs: Vec<(String, Value)> = pairs.into_iter().collect();
    let (row, _) = crate::sqlite::read_row(&full_path(root, path), path, &table, &pairs).map_err(|e| Unresolved::new("E_SOURCE_UNRESOLVED", e.message.clone()))?;
    let Some((col, cell)) = row.column(&column) else {
        let columns: Vec<&str> = row.cells.iter().map(|(c, _)| c.as_str()).collect();
        return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("table {} in {path} has no column {column}; its columns are {}", row.table, columns.join(", "))));
    };
    let by = crate::sqlite::pairs_said(&row.key_values());
    let at = format!("{path}, {}{}", row.place(), if by.is_empty() { String::new() } else { format!(" ({by})") });
    match cell.value() {
        Some(v) if !matches!(cell, crate::sqlite::Cell::Blob(..)) => Ok((v, format!("{at}: {col} {}", cell.said()))),
        _ => Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{at} holds {} in {col}, which states no value", cell.said()))),
    }
}

/// A setting's source that is one column of one row of a SQLite database, read where it is and never at a commit.
fn resolve_row(source: &Source, value: &Value, root: &Path, path: &str, key: &str, name: &str, named: bool) -> R<Resolution> {
    if source.commit.as_deref().map_or(false, |c| !c.is_empty()) {
        return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{path} is a SQLite database, which rb reads where it is, never at a commit")));
    }
    let (rows, column) = row_key_of(key).map(|k| (k.1, k.2)).unwrap_or_default();
    // a key/value table names the setting in the row it picks: [key=calib].value, or the row's own word given as --term
    let last = name.split('.').last().unwrap_or("");
    let row_names = rows.iter().flat_map(|r| r.split(',')).filter_map(|kv| kv.split_once('=')).any(|(_, v)| {
        let v = py_strip(v).trim_matches(|c| c == '\'' || c == '"');
        v.eq_ignore_ascii_case(last) || source.term.as_deref().map_or(false, |t| !t.is_empty() && key_token_in(v, t))
    });
    if named && !column.is_empty() && !row_names && !key_names(&column, name, source.term.as_deref()) {
        return Err(Unresolved::new(
            "E_SOURCE_UNRESOLVED",
            format!("{path}#{key} is the column {column}, not {name}: point at {name}'s own column, or give --term with the name the database uses"),
        ));
    }
    let full = full_path(root, path);
    let file = on_disk(&full).map_err(|e| io_unresolved(source, e))?;
    let (got, text) = row_cell(root, path, key)?;
    if !equal(value, &got) {
        let (is, not) = beside(&got, value);
        let mut u = Unresolved::new("E_SOURCE_UNRESOLVED", format!("{path}#{key} is {is}, not {not}")).found(got);
        u.differs = true;
        return Err(u);
    }
    Ok(resolution(now(), committed_on_disk(&full, &file.0), file.1.clone(), None, trunc(&text, 400), got))
}

fn io_unresolved(source: &Source, e: std::io::Error) -> Unresolved {
    Unresolved::new("E_SOURCE_UNRESOLVED", format!("{} could not be read: {}", source.label(), crate::sys::os_words(&e))).unread()
}

/// The stated line, or, when a line was inserted above it since it was verified, the same text wherever it now is: the
/// text rb kept, a line's without the comment that ends it, or whole.
pub fn line_or_moved(source: &Source, lines: &[String], previous: Option<&Resolution>) -> R<(usize, String)> {
    let line = source.line.unwrap_or(0) as usize;
    let cut = std::cell::OnceCell::new();
    let same = |i: usize, kept: &str| {
        py_strip(&lines[i]) == py_strip(kept) || py_strip(&cut.get_or_init(|| cut_trailing_comments(source.path.as_deref().unwrap_or(""), lines))[i]) == py_strip(kept)
    };
    if line >= 1 && line <= lines.len() {
        let text = &lines[line - 1];
        if previous.map_or(true, |p| p.text.is_empty() || same(line - 1, &p.text)) {
            return Ok((line, text.clone()));
        }
    }
    if let Some(p) = previous {
        if !p.text.is_empty() {
            let matches: Vec<usize> = (0..lines.len()).filter(|i| same(*i, &p.text)).map(|i| i + 1).collect();
            if matches.len() == 1 {
                return Ok((matches[0], lines[matches[0] - 1].clone()));
            }
        }
    }
    if line > lines.len() {
        let n = lines.len();
        return Err(Unresolved::new("E_SOURCE_UNRESOLVED", format!("{} has {n} {}, not {line}", source.path.clone().unwrap_or_default(), crate::assess::plural("line", n as i64))));
    }
    Ok((line, lines[line - 1].clone()))
}

fn candidates(lines: &[String], comments: &[bool], value: &Value, token: &str) -> Vec<Value> {
    let mut out = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        if comments.get(i).copied().unwrap_or(false) {
            continue;
        }
        if key_token_in(line, token) || (!value.is_str() && states(line, value)) {
            out.push(crate::obj! {"line" => (i + 1) as i64, "text" => trunc(py_strip(line), 160)});
        }
        if out.len() >= 5 {
            break;
        }
    }
    out
}

/// A file's size and modification time, which a cache within one command keys on beside its path.
fn signature(p: &Path) -> Option<(u64, Option<std::time::SystemTime>)> {
    std::fs::metadata(p).ok().map(|m| (m.len(), m.modified().ok()))
}

type RecheckKey = (PathBuf, String, Option<(u64, Option<std::time::SystemTime>)>);

static RECHECKED: std::sync::Mutex<Option<std::collections::HashMap<RecheckKey, (String, String)>>> = std::sync::Mutex::new(None);

type FileKey = (PathBuf, Option<(u64, Option<std::time::SystemTime>)>);

/// A file as this command read it from disk (its bytes and their digest), and the commit git holds it at.
static ON_DISK: std::sync::Mutex<Option<std::collections::HashMap<FileKey, std::sync::Arc<(Vec<u8>, String)>>>> = std::sync::Mutex::new(None);
static COMMITTED: std::sync::Mutex<Option<std::collections::HashMap<FileKey, Option<String>>>> = std::sync::Mutex::new(None);

/// Forget what this command read of its sources: each command reads its files afresh.
pub fn forget_reads() {
    if let Ok(mut m) = RECHECKED.lock() {
        *m = None;
    }
    if let Ok(mut m) = ON_DISK.lock() {
        *m = None;
    }
    if let Ok(mut m) = COMMITTED.lock() {
        *m = None;
    }
}

#[cfg(test)]
static READS: std::sync::Mutex<Vec<PathBuf>> = std::sync::Mutex::new(Vec::new());

/// A working-copy file's bytes and digest, read and hashed once per command while it is unchanged.
fn on_disk(p: &Path) -> std::io::Result<std::sync::Arc<(Vec<u8>, String)>> {
    let key: FileKey = (p.to_path_buf(), signature(p));
    if let Some(hit) = ON_DISK.lock().ok().and_then(|m| m.as_ref().and_then(|m| m.get(&key).cloned())) {
        return Ok(hit);
    }
    let data = std::fs::read(p)?;
    #[cfg(test)]
    READS.lock().unwrap().push(p.to_path_buf());
    let sha = sha256_bytes(&data);
    let got = std::sync::Arc::new((data, sha));
    if let Ok(mut m) = ON_DISK.lock() {
        m.get_or_insert_with(Default::default).insert(key, got.clone());
    }
    Ok(got)
}

/// committed_as for a working-copy file read through on_disk, asked of git once per command.
fn committed_on_disk(p: &Path, data: &[u8]) -> Option<String> {
    let key: FileKey = (p.to_path_buf(), signature(p));
    if let Some(hit) = COMMITTED.lock().ok().and_then(|m| m.as_ref().and_then(|m| m.get(&key).cloned())) {
        return hit;
    }
    let got = committed_as(p, data);
    if let Ok(mut m) = COMMITTED.lock() {
        m.get_or_insert_with(Default::default).insert(key, got.clone());
    }
    got
}

/// How a verified source reads now: ("ok", ""), ("moved", why), ("stale", why) or ("conflict", why). Read once per
/// command for each source and value, while its file is unchanged: every finding that reads a setting rechecks it, and
/// a database is megabytes rb would otherwise hash for each.
pub fn recheck(source: &Source, value: &Value, root: &Path, name: &str, named: bool) -> (String, String) {
    let path = source.path.clone().unwrap_or_default();
    let file = if source.kind == "run" { run_json_path(root, &path) } else { full_path(root, &path) };
    let key: RecheckKey = (root.to_path_buf(), format!("{source:?}\u{0}{value:?}\u{0}{name}\u{0}{named}"), signature(&file));
    if let Some(hit) = RECHECKED.lock().ok().and_then(|m| m.as_ref().and_then(|m| m.get(&key).cloned())) {
        return hit;
    }
    let got = recheck_now(source, value, root, name, named);
    if let Ok(mut m) = RECHECKED.lock() {
        m.get_or_insert_with(Default::default).insert(key, got.clone());
    }
    got
}

fn recheck_now(source: &Source, value: &Value, root: &Path, name: &str, named: bool) -> (String, String) {
    let res = match &source.resolved {
        Some(r) => r,
        None => return ("stale".into(), "never verified".into()),
    };
    if source.kind == "run" || source.key.is_some() {
        if !equal(value, &res.read) {
            return ("stale".into(), format!("the value {} is not what was read from {} ({})", render(value), source.label(), res.text));
        }
    } else if !states(source.quote.as_deref().filter(|q| !q.is_empty()).unwrap_or(&res.text), value) && !states(&res.text, value) {
        // neither states it: a quote in the comment that ends a line of code was read for that code, which the line holds
        return ("stale".into(), format!("the value {} is not stated by what was read from {} ({})", render(value), source.label(), repr_str(&res.text)));
    }
    let path = source.path.clone().unwrap_or_default();
    let data: Vec<u8> = if source.kind == "run" {
        match std::fs::read(run_json_path(root, &path)) {
            Ok(d) => d,
            Err(_) => return ("stale".into(), format!("{path} is gone")),
        }
    } else if let Some(c) = source.commit.as_deref().filter(|c| !c.is_empty()) {
        match verified_at_commit(root, &path, c, source, res) {
            Ok((d, _)) => d,
            Err(u) => return ("stale".into(), u.reason),
        }
    } else {
        match on_disk(&full_path(root, &path)) {
            Ok(f) if f.1 == res.sha256 => {
                return recheck_read(source, value, root, name, named, res, &f.0, true);
            }
            Ok(f) => f.0.clone(),
            Err(_) => return ("stale".into(), format!("{path} is gone")),
        }
    };
    let same_bytes = is_file_of(&data, &res.sha256);
    recheck_read(source, value, root, name, named, res, &data, same_bytes)
}

/// The rest of recheck, on the bytes it read and whether they are the ones rb verified.
#[allow(clippy::too_many_arguments)]
fn recheck_read(source: &Source, value: &Value, root: &Path, name: &str, named: bool, res: &Resolution, data: &[u8], same_bytes: bool) -> (String, String) {
    let path = source.path.clone().unwrap_or_default();
    if let Some(c) = source.commit.as_deref().filter(|c| !c.is_empty()) {
        if !same_bytes {
            return ("stale".into(), format!("{path} at {} does not match what was verified", trunc(c, 12)));
        }
    }
    if let Err(u) = resolve(source, value, root, name, Some(res), named) {
        if same_bytes {
            // the bytes rb verified: what an earlier rb read in them by the comments it knew then still stands, as
            // nothing in them changed, and this rb says how it reads them now; a reading no rb would make of them was
            // not rb's
            if source.key.is_none() && source.kind != "run" && resolve_inner(source, value, root, name, Some(res), named, true).is_ok() {
                return (
                    "moved".into(),
                    format!("{} is unchanged since an earlier rb verified it, and this rb reads comments otherwise: {}; rb spec verify reads it again", source.label(), u.reason),
                );
            }
            // an earlier rb read a quote at a place it cuts a word or a number short: its reading, not a forged one
            let text = String::from_utf8_lossy(&data);
            if source.quote.as_deref().map_or(false, |q| !q.is_empty() && text.find(q).map_or(false, |at| quote_places(&text, q).next() != Some(at))) {
                return ("stale".into(), format!("{} is unchanged since an earlier rb verified it, and this rb reads the quote otherwise: {}", source.label(), u.reason));
            }
            return (
                "stale".into(),
                format!("{} is the file rb verified, yet it does not state {}: the record of what was read was not made by rb", source.label(), render(value)),
            );
        }
        if u.differs {
            return ("conflict".into(), u.conflict(source, value, true));     // verified: rb saw it state the value
        }
        return ("stale".into(), u.reason);
    }
    if same_bytes {
        return ("ok".into(), String::new());
    }
    ("moved".into(), format!("{path} changed since it was verified and still states {}; rb spec verify refreshes it", render(value)))
}

/// A file a run names: its path, hash and size.
pub fn file_ref(root: &Path, path: &str) -> std::io::Result<Dict> {
    let p = full_path(root, path);
    if !p.is_file() {
        return Err(std::io::Error::new(std::io::ErrorKind::NotFound, path.to_string()));
    }
    let sha = sha256_file(&p, None)?;
    let size = std::fs::metadata(&p)?.len();
    Ok(crate::dict! {"path" => crate::sys::typed(path), "sha256" => sha, "bytes" => size as i64})
}

/// A run's resolved config as a dict.
pub fn load_config(path: &Path) -> R<Value> {
    let p = crate::sys::slashed(path);
    let kind = structured_kind(&p).ok_or_else(|| Unresolved::new("E_SOURCE_UNRESOLVED", format!("{p} is not YAML, JSON or TOML")))?;
    let bytes = std::fs::read(path).map_err(|e| Unresolved::new("E_SOURCE_UNRESOLVED", format!("{p} could not be read: {}", crate::sys::os_words(&e))))?;
    let doc = parse_structured(&String::from_utf8_lossy(&bytes), kind, &p)?;
    Ok(unwrap_wandb(doc))
}

/// When git first committed anything at this path, in any form, or None when no commit holds it.
pub fn path_first_committed(root: &Path, rel: &str) -> Option<String> {
    let out = git(root, &["log", "--format=%cI", "--reverse", "--", rel])?;
    out.lines().map(str::trim).find(|l| !l.is_empty()).and_then(pytime::norm_time)
}

/// The commit that last changed a line of a file as the working tree holds it, and when that commit was made; an
/// empty commit where no commit holds the line as it is now. None where git cannot say (no repository, a file it does
/// not track, no such line).
pub fn line_last_changed(root: &Path, rel: &str, line: i64) -> Option<(String, Option<String>)> {
    let range = format!("{line},{line}");
    let out = git(root, &["blame", "--porcelain", "-L", &range, "--", rel])?;
    let sha = out.split_whitespace().next()?.to_string();
    if sha.chars().all(|c| c == '0') {
        return Some((String::new(), None));
    }
    let at = out.lines().find_map(|l| l.strip_prefix("committer-time "))?.trim().parse::<i64>().ok()?;
    let when = chrono::DateTime::<chrono::Utc>::from_timestamp(at, 0)?;
    Some((sha, Some(pytime::iso_ms(&when))))
}

/// When git first committed the file at this path as it is now: the last millisecond of that second. The first commit
/// of the path alone says nothing of the bytes it holds now, and a path no commit holds has none: git is asked that
/// first, as hashing a large file it never tracked (a checkpoint) costs seconds.
pub fn first_committed(root: &Path, rel: &str) -> Option<String> {
    git(root, &["log", "-1", "--format=%H", "--", rel]).filter(|o| !o.trim().is_empty())?;
    let blob = git(root, &["hash-object", "--", rel])?;
    let out = git(root, &["log", "--format=%cI", &format!("--find-object={}", blob.trim()), "--", rel])?;
    let dt = out.lines().filter_map(|l| pytime::norm_time(l.trim())).filter_map(|t| pytime::parse_iso(&t)).min()?;
    let dt = dt.with_timezone(&chrono::Utc);
    let truncated = chrono::TimeZone::timestamp_opt(&chrono::Utc, dt.timestamp(), 0).single()? + chrono::Duration::milliseconds(999);
    Some(pytime::iso_ms(&truncated))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn setting(source: Value, text: &str, read: Value) -> Setting {
        setting_of(Value::from(0.1), source, text, read)
    }

    fn setting_of(value: Value, source: Value, text: &str, read: Value) -> Setting {
        let at = "2026-10-07T00:00:00+00:00";
        let fields = crate::dict! {
            "name" => "lr", "value" => value, "status" => "provisional", "source" => source,
            "conflict" => crate::obj! {"at" => at, "read" => read, "text" => text}, "set_by" => "human:t", "set_at" => at
        };
        crate::ledger::build_with(fields, Setting::validate).unwrap()
    }

    /// When a commit was made is asked once: each setting verified at it, on each finding, asks it again.
    #[test]
    fn a_commits_time_is_asked_of_git_once() {
        assert!(holds(&["show", "-s", "--format=%cI", "4f1c2ab"]) == Holds::Always);
        assert!(holds(&["log", "--format=%cI", "--reverse", "--", "sweep/train.py"]) == Holds::Answered);
    }

    /// Every finding that reads a setting rechecks its source: a command reads and hashes each file once while it is
    /// unchanged, so a database of megabytes is not hashed again for each run and finding that reads it.
    #[test]
    fn a_command_reads_each_source_file_once() {
        let dir = std::env::temp_dir().join(format!("rb-reads-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("train.cfg"), "lr = 0.01\nwd = 0.1\n").unwrap();
        let at = |line: i64| -> Source { crate::ledger::build_with(crate::dict! {"kind" => "file", "path" => "train.cfg", "line" => line}, Source::validate).unwrap() };
        let (mut lr, mut wd) = (at(1), at(2));
        lr.resolved = Some(resolve(&lr, &Value::from(0.01), &dir, "lr", None, true).unwrap());
        wd.resolved = Some(resolve(&wd, &Value::from(0.1), &dir, "wd", None, true).unwrap());
        let file = full_path(&dir, "train.cfg");
        let reads = || READS.lock().unwrap().iter().filter(|p| **p == file).count();
        assert_eq!(reads(), 1);
        for _ in 0..5 {
            assert_eq!(recheck(&lr, &Value::from(0.01), &dir, "lr", true).0, "ok");
            assert_eq!(recheck(&wd, &Value::from(0.1), &dir, "wd", true).0, "ok");
        }
        assert_eq!(reads(), 1);
        // a file that changes is read again
        std::fs::write(dir.join("train.cfg"), "lr = 0.01\nwd = 0.25\n").unwrap();
        assert_eq!(recheck(&wd, &Value::from(0.1), &dir, "wd", true).0, "conflict");
        assert_eq!(reads(), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A line is a comment by its own file's syntax; in a document, only in a comment or as a code block's # or // line.
    #[test]
    fn a_comment_is_read_by_its_file_s_own_syntax() {
        let read = |path: &str, text: &str| comment_lines(path, &split_lines(text));
        let md = "## lr = 3e-4\n**lr:** 3e-4\n* lr 3e-4\n<!-- lr: 0.1 -->\n<!--\nlr: 0.1\n-->\n```yaml\n# lr: 0.1\n```\n\n    # lr: 0.1\ntext\n    * lr 3e-4\n";
        assert_eq!(read("README.md", md), vec![false, false, false, true, true, true, true, false, true, false, false, true, false, false]);
        assert_eq!(read("requirements.txt", "# torch==2.3.0\ntorch==2.4.1  # pinned\n* x\n"), vec![true, false, false]);
        assert_eq!(read("CMakeLists.txt", "project(x)\n# set(BATCH 64)\nset(BATCH 32)\n"), vec![false, true, false]);
        assert_eq!(read("app.conf", "// lr = 0.1\n; lr = 0.1\nlr = 0.2\n"), vec![true, true, false]);
        assert_eq!(read("README.md", "## Config\n    # lr: 0.1\n    lr: 0.2\n"), vec![false, true, false]);
        assert_eq!(read("NOTES.md", "Start a comment with `<!--`.\n\nTrain for 90 epochs.\n"), vec![false, false, false]);
        let lists = "- Optimizer\n\n    * **warmup:** 500\n\n1. Stage 2\n\n    **dropout:** 0.1\n    # lr: 0.1\n    <!-- lr: 0.1 -->\n\nText.\n\n    # lr: 0.1\n";
        assert_eq!(read("README.md", lists), vec![false, false, false, false, false, false, false, false, true, false, false, false, true]);
        assert_eq!(read("README.md", "- item\n    ```yaml\n    # lr: 0.1\n    ```\n**lr:** 3\n"), vec![false, false, true, false, false]);
        assert_eq!(read("cfg.yaml", "# lr: 0.1\nlr: 0.2  # was 0.1\n* x\n"), vec![true, false, false]);
        assert_eq!(read("paper.tex", "% lr = 0.1\n# lr\n  % lr\n"), vec![true, false, true]);
        assert_eq!(read("setup.cfg", "; lr = 0.1\n# lr\nlr = 3\n"), vec![true, true, false]);
        assert_eq!(read("model.cu", "// lr\n/* lr */\n * lr\n#define LR 3\n"), vec![true, true, true, false]);
        assert_eq!(read("doc.rst", ".. lr = 0.1\n   lr = 0.1\n.. note::\n   # lr: 0.1\n**lr:** 3\n.. _lr:\n.. [1] lr 3\n"), vec![true, true, false, false, false, false, false]);
        let rst = "- Optimizer:\n\n  * lr: 3e-4\n\nExample::\n\n    # lr: 0.1\n    lr: 0.2\n\n.. code-block:: yaml\n\n   # lr: 0.1\nText.\n";
        assert_eq!(read("doc.rst", rst), vec![false, false, false, false, false, false, true, false, false, false, false, true, false]);
        assert_eq!(read("Makefile", "# x\n* x\n% x\n; x\n// x\nx\n"), vec![true, true, true, true, true, false]);
        // a code block in a list item, and one after an item whose fence closed, read as code, not as the item's text
        assert_eq!(read("STEPS.md", "1. Train the model:\n\n        python train.py --lr 3e-4\n        # python train.py --lr 1e-3\n"), vec![false, false, false, true]);
        let lazy = "1. Install:\n   ```bash\n   pip install -e .\n   ```\nThen train:\n\n    # python train.py --lr 1e-3\n";
        assert_eq!(read("LAZY.md", lazy), vec![false, false, false, false, false, false, true]);
        // a fence reads by its language, and one that names none by every mark; front matter is YAML
        let fences = "```ini\n; lr = 0.1\nlr = 3e-4\n```\n\n```c\n/* float lr = 0.2; */\nfloat lr = 3e-4;\n```\n```\n% lr 0.1\n```\n";
        assert_eq!(read("FENCES.md", fences), vec![false, true, false, false, false, false, true, false, false, false, true, false]);
        assert_eq!(read("FRONT.md", "---\ntitle: x\n# lr: 0.1\nlr: 3e-4\n---\n# Heading\n"), vec![false, false, true, false, false, false]);
        assert_eq!(read("report.qmd", "## lr\n**Learning rate:** 3e-4\n```{python}\n# lr = 0.1\n```\n"), vec![false, false, false, true, false]);
        let sphinx = ".. py:function:: train(lr=3e-4, warmup=500)\n\n   Trains the model. The default warmup is 500 steps.\n.. code-block:: c\n\n   /* lr 0.1 */\nExample::\n\n    ; lr 0.1\n";
        assert_eq!(read("api.rst", sphinx), vec![false, false, false, false, false, true, false, false, true]);
        let cut = |path: &str, text: &str| cut_trailing_comment(path, &split_lines(text), 0);
        assert_eq!(cut("OPT.md", "**Optimizer:** AdamW <!-- SGD diverged -->"), "**Optimizer:** AdamW");
        assert_eq!(cut("requirements.txt", "torch==2.5.0  # was 2.4.1"), "torch==2.5.0");
        assert_eq!(cut("paper.tex", "a rate of 3e-4 % was 1e-3"), "a rate of 3e-4");
        assert_eq!(cut_trailing_comment("HP.md", &split_lines("```python\nlearning_rate = 3e-4  # lr = 1e-3\n```\n"), 1), "learning_rate = 3e-4");
        assert_eq!(cut("cfg.yaml", "lr: 0.2  # was 0.1"), "lr: 0.2");
        assert_eq!(cut("train.py", "steps = 10 // 2"), "steps = 10 // 2");
        assert_eq!(cut("train.cc", "int lr = 3;  // was 1"), "int lr = 3;");
        assert_eq!(cut("README.md", "lr 3e-4 # bold"), "lr 3e-4 # bold");
        assert_eq!(cut("app.conf", "lr: 2 // was 1"), "lr: 2");
        // prose ends no line at a mark: a .txt that is no requirements file, a web page, a file with no extension
        assert_eq!(cut("notes", "lr: 2 // was 1"), "lr: 2 // was 1");
        assert_eq!(cut("papers/vit.txt", "12 layers, #heads 12, dropout 0.1"), "12 layers, #heads 12, dropout 0.1");
        assert_eq!(cut("report.html", "<td>Run #3</td><td>lr 3e-4</td>"), "<td>Run #3</td><td>lr 3e-4</td>");
        assert_eq!(cut("requirements-dev.txt", "torch==2.5.0  # was 2.4.1"), "torch==2.5.0");
        assert_eq!(cut("requirements/base.txt", "torch==2.5.0  # was 2.4.1"), "torch==2.5.0");
        assert_eq!(read("results.txt", "Run #3: top1 0.021\n# lr 0.1\n* x\n"), vec![false, true, true]);
        let fences = "```\n[run #3] lr 3e-4\n```\n```text\n[run #4] lr 3e-4\n```\n    [run #5] lr 3e-4\n";
        let lines = split_lines(fences);
        assert_eq!([1, 4, 6].map(|i| cut_trailing_comment("LOG.md", &lines, i)), ["[run #3] lr 3e-4", "[run #4] lr 3e-4", "    [run #5] lr 3e-4"]);
        assert_eq!(cut_trailing_comments("LOG.md", &lines)[1], "[run #3] lr 3e-4");
        // LaTeX's verbatim, lstlisting and minted environments hold code, read by the language they name
        let tex = "\\begin{verbatim}\n# lr: 1e-3\n\\end{verbatim}\n# text\n\\begin{lstlisting}[language=Python]\nlr = 3e-4  # 1e-3\n\\end{lstlisting}\n\\begin{minted}{c}\n// lr 1e-3\n\\end{minted}\n%\\begin{verbatim}\n# text\n";
        assert_eq!(read("paper.tex", tex), vec![false, true, false, false, false, false, false, false, true, false, true, false]);
        assert_eq!(cut_trailing_comment("paper.tex", &split_lines(tex), 5), "lr = 3e-4");
        // reStructuredText directives whose body is Python
        let rst = ".. ipython:: python\n\n   # lr = 1e-3\n   lr = 3e-4  # tuned\n\n.. testcode::\n\n   # x\n.. note::\n\n   # text\n";
        assert_eq!(read("usage.rst", rst), vec![false, false, true, false, false, false, false, true, false, false, false]);
        assert_eq!(cut_trailing_comment("usage.rst", &split_lines(rst), 3), "   lr = 3e-4");
        assert_eq!(cut_trailing_comment("nb.rst", &split_lines(".. code-cell:: ipython3\n\n   lr = 3e-4  # was 1e-3\n"), 2), "   lr = 3e-4");
    }

    /// A number written with digit separators is one number: Python's, Rust's and TOML's 48_200, LaTeX's 48{,}200 and
    /// 48\,200, as 48,200 is. A name LaTeX or Markdown escapes (seq\_len) is that name.
    #[test]
    fn separators_and_escapes_read_as_the_number_and_the_name() {
        assert_eq!(numbers("NUM_STEPS = 48_200"), vec![Value::Int(48200)]);
        for text in ["for 48{,}200 steps", "for 48\\,200 steps", "for 48,200 steps"] {
            assert_eq!(numbers(text), vec![Value::Int(48200)], "{text}");
            assert_eq!(nearest_number(text, "steps"), Some(Value::Int(48200)), "{text}");
        }
        assert_eq!(numbers("1{,}234{,}567 tokens"), vec![Value::Int(1234567)]);
        assert_eq!(numbers("lr = 0.000_1"), vec![Value::Float(0.0001)]);
        // a decimal comma, a name's digits: no number rb reads
        assert_eq!(numbers("a rate of 0{,}5"), Vec::<Value>::new());
        assert_eq!(numbers("run_48_200"), Vec::<Value>::new());
        assert!(states("DECAY_STEPS = 2_000", &Value::Int(2000)) && !states("DECAY_STEPS = 2_000", &Value::Int(2)));
        assert_eq!(nearest_number("NUM_STEPS = 48_200", "NUM_STEPS"), Some(Value::Int(48200)));
        assert!(key_token_in("with a seq\\_len of 512", "seq_len") && !key_token_in("with a seq\\_lens of 512", "seq_len"));
        assert_eq!(nearest_number("with a seq\\_len of 512", "seq_len"), Some(Value::Int(512)));
        assert!(states("the optimizer is adamw\\_8bit", &Value::from("adamw_8bit")));
        assert!(states("optimizers: [adamw\\_8bit, sgd]", &Value::List(vec![Value::from("adamw_8bit"), Value::from("sgd")])));
        // a quote that starts or ends inside such a number cuts it short
        let at = |text: &str, quote: &str| quote_places(text, quote).collect::<Vec<_>>();
        assert_eq!(at("for 48{,}200 steps", "48"), Vec::<usize>::new());
        assert_eq!(at("for 48{,}200 steps", "200 steps"), Vec::<usize>::new());
        assert_eq!(at("for 48{,}200 steps", "48{,}200 steps"), vec![4]);
        assert_eq!(at("steps = 48_200", "48"), Vec::<usize>::new());
    }

    /// A count written with a K, M or B suffix reads as the count, never as its digits alone.
    #[test]
    fn a_count_suffix_reads_as_the_count() {
        assert_eq!(numbers("trained on 999M tokens"), vec![Value::Int(999_000_000)]);
        assert_eq!(numbers("a 1.5B model, 30K steps, 2k warmup"), vec![Value::Int(1_500_000_000), Value::Int(30_000), Value::Int(2_000)]);
        assert!(states("train_tokens: 999M", &Value::Int(999_000_000)) && !states("train_tokens: 999M", &Value::Int(999)));
        assert_eq!(nearest_number("train_tokens 999M", "train_tokens"), Some(Value::Int(999_000_000)));
        // a letter that runs on into a word is no suffix
        assert_eq!(numbers("a 512MB cache at 3Mbps"), vec![Value::Int(512), Value::Int(3)]);
    }

    /// A quote is in a text only where it cuts no word or number of it short, at either end.
    #[test]
    fn a_quote_is_where_it_cuts_no_word_or_number() {
        let at = |text: &str, quote: &str| quote_places(text, quote).collect::<Vec<_>>();
        assert_eq!(at("a batch size of 32 per GPU", "batch size of 3"), Vec::<usize>::new());
        assert_eq!(at("a batch size of 32 per GPU", "batch size of 32"), vec![2]);
        assert_eq!(at("- momentum: 0.95", "momentum: 0.9"), Vec::<usize>::new());
        assert_eq!(at("x = -0.1 or 0.1", "0.1"), vec![12]);
        assert_eq!(at("x = \u{2212}0.1", "0.1"), Vec::<usize>::new());
        assert_eq!(at("total 1,000 runs", "000 runs"), Vec::<usize>::new());
        assert_eq!(at("optimizer adamw", "optimizer adam"), Vec::<usize>::new());
        assert_eq!(at("python train.py --lr=3e-4 --epochs 10", "--lr=3e-4"), vec![16]);
        assert_eq!(at("lr 0.15 or lr 0.1.", "lr 0.1"), vec![11]);
        assert_eq!(at("1 1 1", "1 1"), vec![0, 2]);
    }

    /// A conflict an earlier rb stored said the source's value first; it reads as one rb finds now does, and never as a
    /// change of the source, which it did not say. One this rb stored is said as it was kept, as a change or not.
    #[test]
    fn a_stored_conflict_says_the_record_s_value_first() {
        let key = crate::obj! {"kind" => "file", "path" => "cfg.yaml", "key" => "lr"};
        let line = crate::obj! {"kind" => "file", "path" => "train.py", "line" => 1};
        let run = crate::obj! {"kind" => "run", "path" => "out/record.json", "pointer" => "/lr"};
        let (says, now) = ("the record says 0.1, but cfg.yaml#lr says 0.3", "the record says 0.1, but cfg.yaml#lr now says 0.3");
        for (source, text, read, said) in [
            (key.clone(), "cfg.yaml#lr is 0.3, not 0.1", Value::from(0.3), says),
            (run, "out/record.json/lr is 0.3, not 0.1", Value::from(0.3), "the record says 0.1, but out/record.json#/lr says 0.3"),
            (line.clone(), "train.py:2 gives lr 0.3, not 0.1; it reads: lr = 0.3", Value::from(0.3), "the record says 0.1, but train.py:2 says 0.3; it reads: lr = 0.3"),
            (line, "train.py:1 does not state 0.1; it reads: lr = 0.3", Value::Null, "the record says 0.1, but train.py:1 says something else; it reads: lr = 0.3"),
            (key.clone(), now, Value::from(0.3), now),
            (key, says, Value::from(0.3), says),
        ] {
            let s = setting(source, text, read);
            assert_eq!(said_conflict(&s).as_deref(), Some(said));
            assert_eq!(said_changed(&s), text == now);
        }
        // "now" in the line a conflict quotes, or in a place's --locator, says nothing of whether the source changed
        let quoted = "the record says 0.1, but notes.md:1 says 0.3; it reads: the lr now says 0.3";
        let placed = "the record says 0.1, but cfg.yaml#lr (the table that now says 0.3) says 0.3";
        for (text, changed) in [
            (quoted.to_string(), false),
            (quoted.replacen(":1 says", ":1 now says", 1), true),
            (placed.to_string(), false),
            (placed.replacen(") says", ") now says", 1), true),
        ] {
            let s = setting(crate::obj! {"kind" => "file", "path" => "notes.md", "line" => 1}, &text, Value::from(0.3));
            assert_eq!(said_changed(&s), changed, "{text}");
        }
    }

    /// Whether a kept conflict says its source changed is read by the two values as rb says them, so no words of the
    /// source's, in the value it states, the line rb quotes or a place's --locator, read as rb's, and a conflict kept
    /// cut to its first 400 characters says it as far as rb's words go.
    #[test]
    fn a_change_is_read_in_rb_s_own_words_whatever_the_source_says() {
        let key = crate::obj! {"kind" => "file", "path" => "cfg.yaml", "key" => "lr"};
        for (value, found, at, reads) in [
            (Value::from("ok"), Value::from("it now says fail"), "cfg.yaml#lr", ""),
            (Value::from("ok"), Value::from("fail; it reads: lr now says fail"), "cfg.yaml#lr", ""),
            (Value::from("0.3"), Value::from(0.3), "cfg.yaml#lr", ""),
            (Value::from(0.1), Value::from(0.3), "notes.md:1", "the lr now says 0.3"),
            (Value::from(0.1), Value::from(0.3), "notes.md:1", "lr says 0.3; it reads: lr now says 0.3"),
            (Value::from(0.1), Value::Null, "notes.md:1", "the lr now says something else"),
            (Value::from(0.1), Value::from(0.3), "cfg.yaml#lr (the table that now says 0.3)", ""),
        ] {
            for saw in [false, true] {
                let text = conflict_says(&value, &found, at, reads, saw);
                assert_eq!(said_changed(&setting_of(value.clone(), key.clone(), &text, found.clone())), saw, "{text}");
            }
        }
        for saw in [false, true] {
            for n in 300..380 {
                let at = format!("{}.md:1", "p".repeat(n));
                let text = conflict_says(&Value::from(0.1), &Value::from(12345.678), &at, "the lr is 12345.678", saw);
                let kept: String = text.chars().take(400).collect();
                // the cut falls after rb's own words up to "says": in the value, before the line rb quotes, or in it
                if kept.contains(&format!("{at} {}says ", if saw { "now " } else { "" })) {
                    assert_eq!(said_changed(&setting_of(Value::from(0.1), key.clone(), &kept, Value::from(12345.678))), saw, "{kept}");
                }
            }
        }
    }
}
