//! The ownership ledger: one JSON line for each write rb makes outside `.rb/` on purpose (an agent's instruction file,
//! a section or an entry it added to a file that was there, a page or an export, a ref that keeps code, a folder of
//! its own), in `<git common dir>/rb/owned.jsonl`, outside git `.rb/cache/owned.jsonl`. It lives outside the record,
//! so no format-1 file changes. rb remove reads it to take back exactly what rb made, and `--out` to know rb's files.
//! A line: {at, path, scope (repo or home), how (created, replaced, section, mcp_entry, hook_entry, output, ref or dir), sha256_after,
//! sha256_before (where rb changed a file that was there), command}, and what a kind of write needs besides. A path in
//! the checkout the ledger sits in is kept from the ledger's own folder (`../../AGENTS.md`), so the lines still name
//! the checkout's files once its folder moves; any other path is kept whole.

use crate::pyjson::{self, Dumps};
use crate::value::{Dict, Value};
use std::path::{Path, PathBuf};

pub const FILE: &str = "owned.jsonl";

/// The clone's shared git directory, resolved, or None outside git.
pub fn common_dir(root: &Path) -> Option<PathBuf> {
    let out = crate::sources::git(root, &["rev-parse", "--git-common-dir"])?;
    let t = out.trim();
    if t.is_empty() {
        return None;
    }
    let p = PathBuf::from(t);
    let p = if p.is_absolute() { p } else { root.join(p) };
    Some(crate::sources::resolve_path(&p))
}

/// rb's own folder in the clone `root` is in, `<git common dir>/rb`, or None outside git.
pub fn rb_home(root: &Path) -> Option<PathBuf> {
    common_dir(root).map(|c| c.join("rb"))
}

/// Where the ledger of the checkout at `root` is: in rb's folder of its clone, or outside git in the record's cache
/// (None when there is no record there either: nothing rb wrote there can be taken back by it).
pub fn ledger_path(root: &Path) -> Option<PathBuf> {
    if let Some(home) = rb_home(root) {
        return Some(home.join(FILE));
    }
    let record = crate::ledger::find_root(Some(root))?;
    Some(record.join(crate::ledger::DIR).join("cache").join(FILE))
}

/// Where the ledger of a checkout in git is kept while rb's folder in git's cannot be written (an agent's sandbox keeps
/// git's folder read-only): the record's own cache, `.rb/cache/owned.jsonl`, which .rb/.gitignore keeps out of git and
/// where the ledger is outside git. None outside git, or with no .rb/ (a removal stopped midway leaves one whose
/// project file is gone: the lines kept there still say what rb wrote).
pub fn aside_path(root: &Path) -> Option<PathBuf> {
    rb_home(root)?;
    let record = crate::ledger::find_root(Some(root)).unwrap_or_else(|| root.to_path_buf()).join(crate::ledger::DIR);
    record.is_dir().then(|| record.join("cache").join(FILE))
}

/// Whether the ledger at `ledger` can be written: its folder is there, or can be made, and takes a file.
fn writable(ledger: &Path) -> bool {
    let Some(dir) = ledger.parent() else { return false };
    (dir.is_dir() || std::fs::create_dir_all(dir).is_ok()) && crate::sys::can_write(dir)
}

/// rb's own folder in git's, which only rb writes (its journal may have made it first), is a new ledger's first line.
fn begin(ledger: &Path) {
    let home = ledger.parent().map(Path::to_path_buf).unwrap_or_default();
    if !ledger.exists() && home.file_name().map_or(false, |n| n == "rb") {
        let mut made = crate::dict! {"at" => crate::pytime::now(), "path" => crate::sys::slashed(&home), "scope" => "repo", "how" => "dir", "command" => command()};
        if let Some(v) = made.get_mut("path") {
            kept_from(ledger, v);
        }
        append(ledger, &made);
    }
}

/// Move the lines kept in the record's cache, while rb's folder in git's could not be written, into the ledger there
/// once it can be: each line the ledger does not hold yet, in its order, its paths kept from the ledger's folder.
pub fn settle(root: &Path) {
    // with no line kept in the record's cache there is nothing to move, and nothing to ask git
    let cached = crate::ledger::find_root(Some(root)).unwrap_or_else(|| root.to_path_buf()).join(crate::ledger::DIR).join("cache").join(FILE);
    if !cached.is_file() {
        return;
    }
    let Some(aside) = aside_path(root).filter(|a| a.is_file()) else { return };
    let Some(home) = ledger_path(root).filter(|h| writable(h)) else { return };
    let dump = |d: &Dict| pyjson::dumps_with(&Value::Dict(d.clone()), &Dumps { ensure_ascii: false, ..Default::default() }).unwrap_or_default();
    begin(&home);
    // a line the ledger holds already was moved by a write stopped before it took the line away here
    let mut have: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    for l in lines_of(&home) {
        *have.entry(dump(&l)).or_default() += 1;
    }
    for mut l in lines_of(&aside) {
        if l.get("how").and_then(Value::as_str) != Some("ref") {
            for key in ["path", "made"] {
                if let Some(v) = l.get_mut(key) {
                    whole(&aside, v);
                    kept_from(&home, v);
                }
            }
        }
        match have.get_mut(&dump(&l)) {
            Some(n) if *n > 0 => *n -= 1,
            _ => append(&home, &l),
        }
    }
    let _ = std::fs::remove_file(&aside);
}

/// The lines of the ledger file at `ledger`, as kept (one that does not read is passed over).
fn lines_of(ledger: &Path) -> Vec<Dict> {
    let Ok(bytes) = crate::dry::read(ledger) else { return Vec::new() };
    String::from_utf8_lossy(&bytes)
        .lines()
        .filter(|l| !l.trim().is_empty())
        .filter_map(|l| match pyjson::loads(l) {
            Ok(Value::Dict(d)) => Some(d),
            _ => None,
        })
        .collect()
}

/// A path (or a list of them) the ledger at `ledger` keeps, made whole.
fn whole(ledger: &Path, v: &mut Value) {
    match v {
        Value::Str(p) => *p = read_from(ledger, p),
        Value::List(items) => {
            for x in items.iter_mut() {
                if let Value::Str(p) = x {
                    *p = read_from(ledger, p);
                }
            }
        }
        _ => {}
    }
}

pub fn sha256_of(path: &Path) -> Option<String> {
    crate::dry::read(path).ok().map(|b| crate::rbyaml::sha256_hex(&b))
}

thread_local! {
    /// The bridge op under way, for a write rb's Python package asked for (the SDK's export, rb ui's scan).
    static DURING: std::cell::RefCell<Option<String>> = const { std::cell::RefCell::new(None) };
}

/// What the bridge is answering now, or None once it has answered.
pub fn during(op: Option<&str>) {
    DURING.with(|d| *d.borrow_mut() = op.map(str::to_string));
}

/// The command this write belongs to, as its words ("init", "skill add"), or the bridge op that made it.
fn command() -> String {
    crate::cli::command_ran().or_else(|| DURING.with(|d| d.borrow().clone())).unwrap_or_default()
}

/// Keep one line for a write rb made outside the record, for the checkout at `root`. `fields` holds `path`, `how` and
/// whatever the kind needs. A preview keeps none (rb init --dry-run names the ledger among what init would write); a
/// line that cannot be kept is no write's failure: rb remove then names what it cannot vouch for and keeps it.
pub fn record(root: &Path, scope: &str, fields: Dict) {
    if crate::dry::active() {
        return;
    }
    let Some(home) = ledger_path(root) else { return };
    // while rb's folder in git's cannot be written, the line is kept in the record's cache, and moves there after
    let ledger = match aside_path(root) {
        Some(aside) if !writable(&home) => aside,
        _ => {
            settle(root);
            home
        }
    };
    let mut line = crate::dict! {"at" => crate::pytime::now()};
    line.insert("path".into(), fields.get("path").cloned().unwrap_or(Value::Null));
    line.insert("scope".into(), Value::str(scope));
    for (k, v) in fields {
        if k != "path" {
            line.insert(k, v);
        }
    }
    line.insert("command".into(), Value::Str(command()));
    if line.get("how").and_then(Value::as_str) != Some("ref") {
        for key in ["path", "made"] {
            if let Some(v) = line.get_mut(key) {
                kept_from(&ledger, v);
            }
        }
    }
    begin(&ledger);
    append(&ledger, &line);
}

/// The top of the checkout the ledger at `ledger` sits in: `<top>/.git/rb/owned.jsonl`, or `<top>/.rb/cache/owned.jsonl`.
fn ledger_top(ledger: &Path) -> Option<PathBuf> {
    ledger.parent()?.parent()?.parent().map(Path::to_path_buf)
}

/// A path (or a list of them) as the ledger keeps it: from the ledger's own folder when it lies in the ledger's
/// checkout, else whole.
fn kept_from(ledger: &Path, v: &mut Value) {
    let Some(top) = ledger_top(ledger).map(|t| crate::sources::resolve_path(&t)) else { return };
    let one = |s: &str| -> String {
        let p = crate::sources::resolve_path(Path::new(s));
        match p.strip_prefix(&top) {
            Ok(rel) => format!("../../{}", crate::sys::slashed(rel)).trim_end_matches('/').to_string(),
            Err(_) => s.to_string(),
        }
    };
    match v {
        Value::Str(s) if Path::new(s.as_str()).is_absolute() => *s = one(s),
        Value::List(items) => {
            for x in items.iter_mut() {
                if let Value::Str(s) = x {
                    if Path::new(s.as_str()).is_absolute() {
                        *s = one(s);
                    }
                }
            }
        }
        _ => {}
    }
}

/// A path the ledger keeps, whole: one kept from the ledger's folder is read from where that folder is now.
fn read_from(ledger: &Path, s: &str) -> String {
    if Path::new(s).is_absolute() {
        return s.to_string();
    }
    let mut out = ledger.parent().map(Path::to_path_buf).unwrap_or_default();
    for part in s.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                out.pop();
            }
            p => out.push(p),
        }
    }
    crate::sys::slashed(out)
}

fn append(ledger: &Path, line: &Dict) {
    use std::io::Write;
    let text = pyjson::dumps_with(&Value::Dict(line.clone()), &Dumps { ensure_ascii: false, ..Default::default() }).unwrap_or_default() + "\n";
    if let Some(parent) = ledger.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(ledger) {
        let _ = f.write_all(text.as_bytes());
    }
}

/// A file rb wrote at `path` for the checkout at `root`: created, or, with `before`, the sha256 of the file that was
/// there. `made` names the folders the write made for it, which go with it. `more` holds what the kind needs besides.
pub fn wrote(root: &Path, scope: &str, path: &Path, how: &str, before: Option<&str>, made: &[PathBuf], more: Dict) {
    let mut fields = crate::dict! {"path" => crate::sys::slashed(path), "how" => how};
    if let Some(sha) = sha256_of(path) {
        fields.insert("sha256_after".into(), Value::Str(sha));
    }
    if let Some(b) = before {
        fields.insert("sha256_before".into(), Value::str(b));
    }
    if !made.is_empty() {
        fields.insert("made".into(), Value::list_of_str(made.iter().map(crate::sys::slashed)));
    }
    for (k, v) in more {
        fields.insert(k, v);
    }
    record(root, scope, fields);
}

/// Where a path rb writes lies: in the checkout at `root` ("repo"), under the home directory ("home"), or elsewhere.
pub fn scope_of(root: &Path, path: &Path) -> &'static str {
    let p = crate::sources::resolve_path(path);
    let top = crate::sources::git_top(root).unwrap_or_else(|| crate::sources::resolve_path(root));
    if p.starts_with(&top) {
        "repo"
    } else if p.starts_with(crate::sources::resolve_path(&crate::sys::home())) {
        "home"
    } else {
        "other"
    }
}

/// Every file under `dir` (its .git too), by its path from `dir`, with its sha256: what a folder rb made holds.
pub fn tree_sums(dir: &Path) -> Dict {
    let mut out = Dict::new();
    let mut todo = vec![dir.to_path_buf()];
    let mut files: Vec<PathBuf> = Vec::new();
    while let Some(d) = todo.pop() {
        for e in std::fs::read_dir(&d).into_iter().flatten().flatten() {
            if e.file_type().map_or(false, |t| t.is_dir()) {
                todo.push(e.path());
            } else {
                files.push(e.path());
            }
        }
    }
    files.sort();
    for f in files {
        let rel = crate::sources::rel_to(&f, dir).unwrap_or_default();
        out.insert(rel, Value::Str(std::fs::read(&f).map(|b| crate::rbyaml::sha256_hex(&b)).unwrap_or_default()));
    }
    out
}

/// The folders `path`'s parent chain lacks now, outermost first: what writing it makes.
pub fn missing_dirs(path: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = path.ancestors().skip(1).take_while(|d| !d.as_os_str().is_empty() && !crate::dry::is_dir(d)).map(Path::to_path_buf).collect();
    out.reverse();
    out
}

/// Every line of the ledger of the checkout at `root`, its paths whole (one that does not read is passed over): the
/// ledger's, then those kept in the record's cache since rb's folder in git's could not be written, which are later.
pub fn entries(root: &Path) -> Vec<Dict> {
    let Some(home) = ledger_path(root) else { return Vec::new() };
    let mut out = Vec::new();
    for ledger in std::iter::once(home).chain(aside_path(root)) {
        for mut d in lines_of(&ledger) {
            if d.get("how").and_then(Value::as_str) != Some("ref") {
                for key in ["path", "made"] {
                    if let Some(v) = d.get_mut(key) {
                        whole(&ledger, v);
                    }
                }
            }
            out.push(d);
        }
    }
    out
}

/// The last line the ledger keeps for `path`, of one of the kinds `hows`.
pub fn last_for(lines: &[Dict], path: &Path, hows: &[&str]) -> Option<Dict> {
    all_for(lines, path, hows).pop()
}

/// Every line the ledger keeps for `path`, of one of the kinds `hows`, oldest first.
pub fn all_for(lines: &[Dict], path: &Path, hows: &[&str]) -> Vec<Dict> {
    let want = crate::sys::slashed(crate::sources::resolve_path(path));
    let plain = crate::sys::slashed(path);
    lines
        .iter()
        .filter(|l| {
            let p = l.get("path").and_then(Value::as_str).unwrap_or("");
            (p == want || p == plain) && hows.contains(&l.get("how").and_then(Value::as_str).unwrap_or(""))
        })
        .cloned()
        .collect()
}

/// A file rb added to, as it was before: rb's bytes taken out where the line says they went, and the bytes they took
/// the place of put back, when what that gives has the sha256 the file had before.
pub fn as_before(now: &str, line: &Dict) -> Option<String> {
    let (offset, length) = (line.get("offset")?.as_i64()? as usize, line.get("length")?.as_i64()? as usize);
    let before = line.get("sha256_before").and_then(Value::as_str)?;
    let replaced = line.get("replaced").and_then(Value::as_str).unwrap_or("");
    let (head, tail) = (now.get(..offset)?, now.get(offset.checked_add(length)?..)?);
    let back = format!("{head}{replaced}{tail}");
    (crate::rbyaml::sha256_hex(back.as_bytes()) == before).then_some(back)
}

/// Whether a file at `path` is rb's to write over as an output: none is there, the ledger keeps its bytes as rb wrote
/// them, or it is an rb output by its own marks (`marked`). Otherwise it is refused unless `replace` says to write over it.
pub fn may_write_over(root: &Path, path: &Path, replace: bool, marked: &dyn Fn(&Path) -> bool) -> crate::errors::Res<()> {
    // a file the ledger keeps is rb's only with the bytes rb wrote; rb's marks say so only of one it has no line for
    let kept = last_for(&entries(root), path, &["output"]).is_some();
    if replace || !path.exists() || path.is_dir() || as_rb_wrote(root, path) || (!kept && marked(path)) {
        return Ok(());
    }
    let shown = crate::cmds::share::shown_path(path);
    let said = if kept { "changed since rb wrote it" } else { "is there and rb did not write it" };
    Err(crate::errors::RBError::msg_fix(
        "E_WRITE_FAILED",
        format!("{shown} {said}, so rb leaves it as it is."),
        "Give --out a path that is not taken, or add --replace to write over that file.",
    ))
}

/// Whether the file at `path` is one rb wrote and nobody changed since: the ledger keeps its bytes' sha256.
pub fn as_rb_wrote(root: &Path, path: &Path) -> bool {
    let Some(now) = sha256_of(path) else { return false };
    entries(root).iter().any(|l| {
        let p = l.get("path").and_then(Value::as_str).unwrap_or("");
        (p == crate::sys::slashed(crate::sources::resolve_path(path)) || p == crate::sys::slashed(path))
            && l.get("sha256_after").and_then(Value::as_str) == Some(now.as_str())
    })
}
