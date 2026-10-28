//! A write rb previews (`--dry-run`, `dry_run`): every check the write makes runs as it would, and the files it would
//! write are kept here instead of on disk, so the reads that follow within the write see them. Nothing is written: no
//! record file, no log line, no cache, no lock file, no git object or ref, no time of any file changed.

use crate::pyjson::{self, Dumps};
use crate::value::Value;
use indexmap::IndexMap;
use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};

/// What a previewed write would write.
#[derive(Default)]
pub struct Preview {
    /// each file it would write, in the order it first wrote it: its content, or None where it would remove it
    files: IndexMap<PathBuf, Option<Vec<u8>>>,
    /// what it would add at the end of a file that is there (the log)
    appended: IndexMap<PathBuf, Vec<u8>>,
    /// each change counts one, so a file's stamp moves whenever what it holds here does
    generation: u64,
}

thread_local! {
    static PREVIEW: RefCell<Option<Preview>> = const { RefCell::new(None) };
    static DEPTH: Cell<u32> = const { Cell::new(0) };
}

/// Whether the write under way is previewed.
pub fn active() -> bool {
    DEPTH.with(|d| d.get() > 0)
}

/// Preview what follows: a command line rb runs inside a previewed one is previewed with it.
pub fn begin() {
    let depth = DEPTH.with(|d| {
        d.set(d.get() + 1);
        d.get()
    });
    if depth == 1 {
        PREVIEW.with(|p| *p.borrow_mut() = Some(Preview::default()));
        crate::ledger::forget_files();
    }
}

/// The end of a preview: what it would have written, once the outermost one ends.
pub fn end() -> Option<Preview> {
    let depth = DEPTH.with(|d| {
        d.set(d.get().saturating_sub(1));
        d.get()
    });
    if depth > 0 {
        return None;
    }
    let preview = PREVIEW.with(|p| p.borrow_mut().take());
    // what the preview read of its own files is no record's: nothing after it reads from that
    crate::ledger::forget_files();
    crate::ledger::forget_worked();
    preview
}

/// What `f` reads of a record a screen only looks at (another place, a past version), inside a preview: nothing it
/// does is written there, a lock file, a cache or a journal line included.
pub fn reading<T>(f: impl FnOnce() -> T) -> T {
    struct End;
    impl Drop for End {
        fn drop(&mut self) {
            let _ = end();
        }
    }
    begin();
    let _end = End;
    f()
}

fn with<T>(f: impl FnOnce(&mut Preview) -> T) -> Option<T> {
    PREVIEW.with(|p| p.borrow_mut().as_mut().map(f))
}

/// Why the system would refuse the write at `path` before it changed anything there, as the write itself would find
/// it: a name Windows keeps for a device, a file where a directory has to be, or a directory or file this process may
/// not write (a read-only checkout or mount).
fn refused(path: &Path, append: bool) -> Option<std::io::Error> {
    if let Some(e) = crate::ledger::unwritable_name(path) {
        return Some(e);
    }
    let dir = path.ancestors().skip(1).find(|d| d.exists())?;
    if !dir.is_dir() {
        return Some(std::io::ErrorKind::NotADirectory.into());
    }
    crate::sys::write_refused(dir, path, append)
}

/// Write a file whole: in a preview, kept here.
pub fn write(path: &Path, data: &[u8]) -> std::io::Result<()> {
    if !active() {
        return crate::ledger::write_atomic(path, data);
    }
    if let Some(e) = refused(path, false) {
        return Err(e);
    }
    with(|p| {
        p.appended.shift_remove(path);
        p.files.insert(path.to_path_buf(), Some(data.to_vec()));
        p.generation += 1;
    });
    Ok(())
}

/// Add to the end of a file, made if need be: in a preview, kept here.
pub fn append(path: &Path, data: &[u8]) -> std::io::Result<()> {
    if !active() {
        use std::io::Write;
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(path)?;
        return f.write_all(data);
    }
    if let Some(e) = refused(path, true) {
        return Err(e);
    }
    with(|p| {
        match p.files.get_mut(path) {
            Some(Some(content)) => content.extend_from_slice(data),
            _ => p.appended.entry(path.to_path_buf()).or_default().extend_from_slice(data),
        }
        p.generation += 1;
    });
    Ok(())
}

/// Remove a file: in a preview, kept here as removed.
pub fn remove(path: &Path) -> std::io::Result<()> {
    if !active() {
        return std::fs::remove_file(path);
    }
    if let Some(e) = refused(path, false) {
        return Err(e);
    }
    with(|p| {
        p.appended.shift_remove(path);
        p.files.insert(path.to_path_buf(), None);
        p.generation += 1;
    });
    Ok(())
}

/// A file's bytes as the write under way has left them.
pub fn read(path: &Path) -> std::io::Result<Vec<u8>> {
    if !active() {
        return std::fs::read(path);
    }
    match with(|p| (p.files.get(path).cloned(), p.appended.get(path).cloned())).unwrap_or((None, None)) {
        (Some(Some(content)), _) => Ok(content),
        (Some(None), _) => Err(std::io::Error::new(std::io::ErrorKind::NotFound, "No such file or directory")),
        (None, Some(more)) => {
            let mut data = match std::fs::read(path) {
                Ok(d) => d,
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
                Err(e) => return Err(e),
            };
            data.extend_from_slice(&more);
            Ok(data)
        }
        (None, None) => std::fs::read(path),
    }
}

/// What the preview holds now, path by path: "new" for a file it would make, "change" for one it would write over,
/// "remove" for one it would take away, and "add to" for one it would add lines at the end of.
pub fn held_now() -> Vec<(PathBuf, &'static str)> {
    with(|p| {
        let mut out: Vec<(PathBuf, &'static str)> = Vec::new();
        for (path, content) in &p.files {
            match content {
                Some(_) => out.push((path.clone(), if path.exists() { "change" } else { "new" })),
                None if path.exists() => out.push((path.clone(), "remove")),
                None => {}
            }
        }
        out.extend(p.appended.keys().map(|path| (path.clone(), if path.exists() { "add to" } else { "new" })));
        out
    })
    .unwrap_or_default()
}

/// What the preview holds of a path: written (Some(true)), removed (Some(false)), or nothing (None).
fn held(path: &Path) -> Option<bool> {
    with(|p| match p.files.get(path) {
        Some(c) => Some(c.is_some()),
        None => p.appended.contains_key(path).then_some(true),
    })
    .flatten()
}

pub fn is_file(path: &Path) -> bool {
    if !active() {
        return path.is_file();
    }
    held(path).unwrap_or_else(|| path.is_file())
}

pub fn exists(path: &Path) -> bool {
    if !active() {
        return path.exists();
    }
    held(path).unwrap_or_else(|| path.exists()) || is_dir(path)
}

/// A directory, on disk or as one the preview's files would make.
pub fn is_dir(path: &Path) -> bool {
    path.is_dir() || with(|p| p.files.iter().any(|(f, c)| c.is_some() && f.starts_with(path) && f != path)).unwrap_or(false)
}

/// What a directory holds, its own files and directories, as the write under way has left it.
pub fn list_dir(dir: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = match std::fs::read_dir(dir) {
        Ok(rd) => rd.flatten().map(|e| e.path()).collect(),
        Err(_) => Vec::new(),
    };
    if !active() {
        return out;
    }
    with(|p| {
        for (f, content) in &p.files {
            match (content, f.strip_prefix(dir).ok().and_then(|rest| rest.components().next())) {
                (None, _) => out.retain(|x| x != f),
                (Some(_), Some(first)) => {
                    let child = dir.join(first);
                    if !out.contains(&child) {
                        out.push(child);
                    }
                }
                _ => {}
            }
        }
    });
    out
}

/// A file's stamp as the ledger keys what it read: (time, size, inode). One the preview holds has one of its own,
/// which no file on disk has.
pub fn stamp(path: &Path) -> Option<(i64, u64, u64)> {
    if !active() {
        return crate::sys::file_stamp(path);
    }
    let ours = with(|p| match (p.files.get(path), p.appended.get(path)) {
        (Some(Some(c)), _) => Some(Some((i64::MIN + p.generation as i64, c.len() as u64, u64::MAX))),
        (Some(None), _) => Some(None),
        (None, Some(more)) => {
            let on_disk = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
            Some(Some((i64::MIN + p.generation as i64, on_disk + more.len() as u64, u64::MAX)))
        }
        (None, None) => None,
    })
    .flatten();
    match ours {
        Some(s) => s,
        None => crate::sys::file_stamp(path),
    }
}

/// A file's (size, time) as the log is read by: what the preview would add makes it longer.
pub fn size_time(path: &Path) -> Option<(u64, i64)> {
    let on_disk = std::fs::metadata(path).ok().map(|md| (md.len(), crate::sys::mtime_ns(&md) as i64));
    match with(|p| (p.files.get(path).map(|c| c.as_ref().map(|c| c.len() as u64)), p.appended.get(path).map(|m| m.len() as u64), p.generation)) {
        Some((Some(Some(len)), _, generation)) => Some((len, i64::MIN + generation as i64)),
        Some((Some(None), _, _)) => None,
        Some((None, Some(more), _)) => Some(on_disk.map_or((more, 0), |(len, time)| (len + more, time))),
        _ => on_disk,
    }
}

// ---------------------------------------------------------------- what is said of it

/// A path as the record writes it: from the project's root, with `/`.
fn shown(path: &Path, root: Option<&Path>) -> String {
    match root.and_then(|r| path.strip_prefix(r).ok()) {
        Some(rel) => rel.to_string_lossy().replace('\\', "/"),
        None => crate::sys::slashed(path),
    }
}

fn text(data: &[u8]) -> String {
    String::from_utf8_lossy(data).into_owned()
}

/// What the preview holds, as --json, MCP and the SDK give it: `files`, each file it would write with its content and
/// whether it is new; `removed`, each file it would remove; `log`, each line it would add to the log, whole.
pub fn would_write(p: &Preview, root: Option<&Path>) -> Value {
    let mut files: Vec<Value> = Vec::new();
    let mut removed: Vec<Value> = Vec::new();
    for (path, content) in &p.files {
        match content {
            Some(c) => files.push(crate::obj! {"path" => shown(path, root), "new" => !path.exists(), "content" => text(c)}),
            None if !path.exists() => {}
            None => removed.push(Value::Str(shown(path, root))),
        }
    }
    let mut log: Vec<Value> = Vec::new();
    for more in p.appended.values() {
        for line in text(more).lines().filter(|l| !l.trim().is_empty()) {
            log.push(pyjson::loads(line).unwrap_or_else(|_| Value::Str(line.to_string())));
        }
    }
    crate::obj! {"files" => Value::List(files), "removed" => Value::List(removed), "log" => Value::List(log)}
}

/// A record file as rb's text gives it: without the keys that say who made a write (ledger::without_writers).
fn readable(data: &str) -> String {
    match crate::rbyaml::loads(data, "") {
        Ok(v) => crate::rbyaml::dumps(&crate::ledger::without_writers(&v), None).unwrap_or_else(|_| data.to_string()),
        Err(_) => data.to_string(),
    }
}

/// A log line as rb's text gives it: no key that says who made the write, and a session's line without its id.
fn readable_line(entry: &Value) -> String {
    let mut shown = crate::ledger::without_writers(entry);
    if let Value::Dict(d) = &mut shown {
        if d.get("op").and_then(Value::as_str) == Some("session") {
            d.shift_remove("id");
        }
    }
    pyjson::dumps_with(&shown, &Dumps { ensure_ascii: false, ..Default::default() }).unwrap_or_default()
}

/// What the preview holds, in rb's text: each file with its content (a file that is there, as the lines that change),
/// each file removed, each line added to the log. `refused`: the write would stop on its refusal.
pub fn said(p: &Preview, root: Option<&Path>, refused: bool) -> Vec<String> {
    if p.files.iter().all(|(path, c)| c.is_none() && !path.exists()) && p.appended.values().all(|m| m.is_empty()) {
        return vec![if refused { "The write would be refused before it changed anything." } else { "The write would change nothing." }.to_string()];
    }
    let mut out = vec![if refused { "The write would make these changes, then be refused:" } else { "The write would make these changes:" }.to_string()];
    for (path, content) in &p.files {
        let at = shown(path, root);
        match content {
            None if !path.exists() => {}
            None => out.push(format!("  remove {at}")),
            Some(c) => {
                let after = readable(&text(c));
                match std::fs::read(path) {
                    Ok(before) => {
                        out.push(format!("  change {at}:"));
                        out.extend(changes(&readable(&text(&before)), &after).into_iter().map(|l| format!("    {l}")));
                    }
                    Err(_) => {
                        out.push(format!("  add {at}:"));
                        out.extend(after.lines().map(|l| format!("    {l}")));
                    }
                }
            }
        }
    }
    for (path, more) in &p.appended {
        let entries: Vec<Value> = text(more).lines().filter(|l| !l.trim().is_empty()).map(|l| pyjson::loads(l).unwrap_or_else(|_| Value::Str(l.to_string()))).collect();
        let n = entries.len();
        out.push(format!("  add {} to {}:", if n == 1 { "one line".to_string() } else { format!("{n} lines") }, shown(path, root)));
        out.extend(entries.iter().map(|e| format!("    {}", readable_line(e))));
    }
    out
}

/// The lines that change from `before` to `after`, as a unified diff's hunks with two lines around each change.
fn changes(before: &str, after: &str) -> Vec<String> {
    let a: Vec<&str> = before.lines().collect();
    let b: Vec<&str> = after.lines().collect();
    // the lines both share at the start and the end, then the fewest changes between (a longest common subsequence)
    let head = a.iter().zip(&b).take_while(|(x, y)| x == y).count();
    let tail = a[head..].iter().rev().zip(b[head..].iter().rev()).take_while(|(x, y)| x == y).count();
    let (am, bm) = (&a[head..a.len() - tail], &b[head..b.len() - tail]);
    let mut ops: Vec<(char, usize, usize)> = (0..head).map(|i| (' ', i, i)).collect();
    if am.len() * bm.len() <= 4_000_000 {
        let (n, m) = (am.len(), bm.len());
        let mut lcs = vec![0u32; (n + 1) * (m + 1)];
        for i in (0..n).rev() {
            for j in (0..m).rev() {
                lcs[i * (m + 1) + j] = if am[i] == bm[j] { lcs[(i + 1) * (m + 1) + j + 1] + 1 } else { lcs[(i + 1) * (m + 1) + j].max(lcs[i * (m + 1) + j + 1]) };
            }
        }
        let (mut i, mut j) = (0, 0);
        while i < n || j < m {
            if i < n && j < m && am[i] == bm[j] {
                ops.push((' ', head + i, head + j));
                i += 1;
                j += 1;
            } else if i < n && (j == m || lcs[(i + 1) * (m + 1) + j] >= lcs[i * (m + 1) + j + 1]) {
                ops.push(('-', head + i, head + j));
                i += 1;
            } else {
                ops.push(('+', head + i, head + j));
                j += 1;
            }
        }
    } else {
        ops.extend((0..am.len()).map(|i| ('-', head + i, head)));
        ops.extend((0..bm.len()).map(|j| ('+', head + am.len(), head + j)));
    }
    ops.extend((0..tail).map(|k| (' ', a.len() - tail + k, b.len() - tail + k)));
    // hunks: each change with two lines of context around it, near ones joined
    let changed: Vec<usize> = ops.iter().enumerate().filter(|(_, o)| o.0 != ' ').map(|(k, _)| k).collect();
    let mut out = Vec::new();
    let mut k = 0;
    while k < changed.len() {
        let start = changed[k].saturating_sub(2);
        let mut end = (changed[k] + 3).min(ops.len());
        while k + 1 < changed.len() && changed[k + 1] <= end + 2 {
            k += 1;
            end = (changed[k] + 3).min(ops.len());
        }
        let hunk = &ops[start..end];
        let (old_n, new_n) = (hunk.iter().filter(|o| o.0 != '+').count(), hunk.iter().filter(|o| o.0 != '-').count());
        let (old_at, new_at) = (hunk[0].1 + 1, hunk[0].2 + 1);
        out.push(format!("@@ -{old_at},{old_n} +{new_at},{new_n} @@"));
        for (op, i, j) in hunk {
            out.push(match op {
                '-' => format!("-{}", a[*i]),
                '+' => format!("+{}", b[*j]),
                _ => format!(" {}", a[*i]),
            });
        }
        k += 1;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A changed file is said as the lines that change, each with two lines around it, as a unified diff gives them.
    #[test]
    fn a_change_is_said_as_its_hunks() {
        let before = "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n";
        let after = "a\nb\nc\nD\ne\nf\ng\nh\ni\nj\nk\n";
        assert_eq!(changes(before, after), vec!["@@ -2,5 +2,5 @@", " b", " c", "-d", "+D", " e", " f", "@@ -9,2 +9,3 @@", " i", " j", "+k"]);
        assert_eq!(changes("x\n", "x\n"), Vec::<String>::new());
        assert_eq!(changes("", "a\n"), vec!["@@ -1,0 +1,1 @@", "+a"]);
    }

    /// A preview keeps what it writes, appends and removes; reads see it; the disk does not.
    #[test]
    fn a_preview_holds_what_it_writes_and_the_disk_does_not() {
        let dir = std::env::temp_dir().join(format!("rb-dry-{}-{}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(dir.join("old")).unwrap();
        std::fs::write(dir.join("old").join("a.yaml"), "a: 1\n").unwrap();
        std::fs::write(dir.join("log.jsonl"), "{\"op\": \"init\"}\n").unwrap();
        let was = std::fs::metadata(dir.join("log.jsonl")).unwrap().modified().unwrap();
        begin();
        write(&dir.join("new").join("b.yaml"), b"b: 2\n").unwrap();
        remove(&dir.join("old").join("a.yaml")).unwrap();
        append(&dir.join("log.jsonl"), b"{\"op\": \"add\"}\n").unwrap();
        assert!(is_file(&dir.join("new").join("b.yaml")) && is_dir(&dir.join("new")) && !exists(&dir.join("old").join("a.yaml")));
        assert_eq!(read(&dir.join("log.jsonl")).unwrap(), b"{\"op\": \"init\"}\n{\"op\": \"add\"}\n");
        assert!(list_dir(&dir).contains(&dir.join("new")) && list_dir(&dir.join("old")).is_empty());
        assert_ne!(stamp(&dir.join("log.jsonl")), crate::sys::file_stamp(&dir.join("log.jsonl")));
        let p = end().unwrap();
        assert!(!active() && !dir.join("new").exists() && dir.join("old").join("a.yaml").is_file());
        assert_eq!(std::fs::read(dir.join("log.jsonl")).unwrap(), b"{\"op\": \"init\"}\n");
        assert_eq!(std::fs::metadata(dir.join("log.jsonl")).unwrap().modified().unwrap(), was);
        let w = would_write(&p, Some(&dir));
        assert_eq!(w.get("files").and_then(Value::as_list).map(|l| l.len()), Some(1));
        assert_eq!(pyjson::dumps(w.get("removed").unwrap()), "[\"old/a.yaml\"]");
        assert_eq!(pyjson::dumps(w.get("log").unwrap()), "[{\"op\": \"add\"}]");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
