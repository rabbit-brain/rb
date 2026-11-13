//! Files (UX5 1.4): the project's files as the left pane's Files tab, `rb files` and `files[]` in the JSON show them,
//! each with its marks: cited by the record (❝ n), the source of settings (⚙ n), a run's output (▣), changed since the
//! run that named it (△), not committed (±), ignored by git (⊘), not in this clone (∅). On rb open and `rb files` the
//! whole project; on a page only the files the record names. Three sections: Cited (what the research quotes or reads,
//! by how often), Changed (since a run read it, or not committed) and the folder tree.

use crate::errors::Res;
use crate::ledger::Ledger;
use crate::pyjson::py_str;
use crate::value::Value;
use indexmap::IndexMap;
use std::collections::BTreeSet;

/// One mark: its glyph, its ASCII form, and its words (the key and the `said`).
pub const MARKS: [(&str, &str, &str, &str); 7] = [
    ("cited", "❝", "\"", "cited by the record"),
    ("setting", "⚙", "s", "the source of a setting"),
    ("output", "▣", "o", "a run's output"),
    ("changed", "△", "^", "changed since the run that named it"),
    ("uncommitted", "±", "+", "not committed"),
    ("ignored", "⊘", "-", "ignored by git"),
    ("absent", "∅", "0", "not in this clone"),
];

#[derive(Default, Clone)]
struct Entry {
    cited: usize,
    settings: usize,
    output: bool,
    changed: bool,
    uncommitted: String,
    ignored: bool,
    absent: bool,
    cited_by: Vec<(String, String)>,
    lines: BTreeSet<usize>,
    named: bool,
}

fn git_lines(root: &std::path::Path, args: &[&str]) -> Vec<String> {
    crate::sources::git(root, args).map(|s| s.split('\0').filter(|x| !x.is_empty()).map(str::to_string).collect()).unwrap_or_default()
}

/// The path part of a source (`configs/train.yaml#optim.lr`, `results.db#t[...].c`, `paper.md:12`), where it names a
/// file of the project.
fn source_path(src: &crate::record::Source) -> Option<String> {
    let p = src.path.clone()?;
    let p = p.trim_start_matches("./").to_string();
    (!p.is_empty() && !p.starts_with('/') && !p.contains("://") && !p.split('/').any(|x| x == "..")).then_some(p)
}

/// Each file the record names, with how; and, where `whole`, every file git tracks or lists untracked and not ignored.
/// `citations` is the shell document's map (ledger/cite.rs), where one is drawn.
pub fn of(led: &Ledger, citations: Option<&Value>, whole: bool) -> Res<Value> {
    let mut files: IndexMap<String, Entry> = IndexMap::new();
    if let Some(Value::Dict(cs)) = citations {
        for c in cs.values() {
            let path = c.get("path").map(py_str).unwrap_or_default();
            if path.is_empty() {
                continue;
            }
            let e = files.entry(path).or_default();
            e.named = true;
            e.cited += 1;
            if let (Some(a), b) = (c.get("line").and_then(Value::as_i64), c.get("end").and_then(Value::as_i64)) {
                for n in a..=b.unwrap_or(a).max(a) {
                    e.lines.insert(n as usize);
                }
            }
            if let Some(Value::List(by)) = c.get("cited_by") {
                for b in by {
                    let kv = (b.get("kind").map(py_str).unwrap_or_default(), b.get("id").map(py_str).unwrap_or_default());
                    if !e.cited_by.contains(&kv) {
                        e.cited_by.push(kv);
                    }
                }
            }
        }
    }
    for x in led.all::<crate::record::Experiment>()?.iter().filter(|x| x.retracted.is_none()) {
        for st in x.settings.iter().filter(|s| s.retracted.is_none()) {
            if let Some(p) = st.source.as_ref().and_then(source_path) {
                let e = files.entry(p).or_default();
                e.named = true;
                e.settings += 1;
                if let Some(n) = st.source.as_ref().and_then(|s| s.line) {
                    e.lines.insert(n as usize);
                }
                let by = ("setting".to_string(), format!("{}/{}", x.id, st.name));
                if !e.cited_by.contains(&by) {
                    e.cited_by.push(by);
                }
            }
        }
    }
    for c in led.all::<crate::record::Criterion>()?.iter().filter(|c| c.retracted.is_none()) {
        if let Some(p) = c.source.as_ref().and_then(source_path) {
            let e = files.entry(p).or_default();
            e.named = true;
            if let Some(n) = c.source.as_ref().and_then(|s| s.line) {
                e.lines.insert(n as usize);
            }
            let by = ("criterion".to_string(), c.id.clone());
            if !e.cited_by.contains(&by) {
                e.cited += 1;
                e.cited_by.push(by);
            }
        }
    }
    for r in led.standing_runs()?.iter() {
        // every file a run read its numbers from or kept is its output, a database it read a row of included
        for f in &r.files {
            let e = files.entry(f.path.clone()).or_default();
            e.named = true;
            e.output = true;
            let by = ("run".to_string(), r.id.clone());
            if !e.cited_by.contains(&by) {
                e.cited_by.push(by);
            }
        }
        for nf in led.named_files(r) {
            let e = files.entry(nf.path.clone()).or_default();
            e.named = true;
            e.output = true;
            if nf.now != crate::ledger::evidence::FileNow::Unchanged {
                if nf.not_in_clone() {
                    e.absent = true;
                } else {
                    e.changed = true;
                }
            }
            let by = ("run".to_string(), r.id.clone());
            if !e.cited_by.contains(&by) {
                e.cited_by.push(by);
            }
        }
    }
    let root = led.root.clone();
    let in_git = crate::sources::git(&root, &["rev-parse", "--is-inside-work-tree"]).map_or(false, |s| s.trim() == "true");
    if in_git {
        // what git says of each: not committed (new or changed), ignored; the rest of the project where whole
        let status = git_lines(&root, &["-c", "core.quotepath=off", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".", ":(exclude).rb"]);
        let mut changed: IndexMap<String, String> = IndexMap::new();
        let mut skip = false;
        for line in &status {
            if skip {
                skip = false;
                continue;
            }
            if line.len() < 4 {
                continue;
            }
            let (code, path) = (&line[..2], line[3..].to_string());
            if code.starts_with('R') || code.starts_with('C') {
                skip = true;
            }
            let how = if code == "??" { "new" } else if code.contains('D') { "deleted" } else { "changed" };
            changed.insert(path, how.to_string());
        }
        if whole {
            for p in git_lines(&root, &["-c", "core.quotepath=off", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".", ":(exclude).rb"]) {
                files.entry(p).or_default();
            }
        }
        for (p, how) in &changed {
            if let Some(e) = files.get_mut(p) {
                e.uncommitted = how.clone();
            } else if whole {
                files.insert(p.clone(), Entry { uncommitted: how.clone(), ..Default::default() });
            }
        }
        let named: Vec<String> = files.keys().cloned().collect();
        if !named.is_empty() {
            let mut args: Vec<&str> = vec!["check-ignore", "--no-index", "-z", "--"];
            args.extend(named.iter().map(String::as_str));
            for p in git_lines(&root, &args) {
                if let Some(e) = files.get_mut(&p) {
                    e.ignored = true;
                }
            }
        }
    }
    for (p, e) in files.iter_mut() {
        if !e.absent && !root.join(p).exists() && e.named && e.uncommitted != "deleted" {
            e.absent = true;
        }
    }
    let mut keys: Vec<String> = files.keys().cloned().collect();
    keys.sort();
    let out: Vec<Value> = keys
        .iter()
        .map(|p| {
            let e = &files[p];
            let mut marks: Vec<Value> = Vec::new();
            let mut add = |key: &str, n: Option<usize>| {
                let (_, glyph, ascii, words) = MARKS.iter().find(|m| m.0 == key).copied().unwrap();
                marks.push(crate::obj! {"key" => key, "glyph" => glyph, "ascii" => ascii, "n" => Value::opt_i64(n.map(|n| n as i64)), "said" => words});
            };
            if e.cited > 0 {
                add("cited", Some(e.cited));
            }
            if e.settings > 0 {
                add("setting", Some(e.settings));
            }
            if e.output {
                add("output", None);
            }
            if e.changed {
                add("changed", None);
            }
            if !e.uncommitted.is_empty() {
                add("uncommitted", None);
            }
            if e.ignored {
                add("ignored", None);
            }
            if e.absent {
                add("absent", None);
            }
            let (folder, name) = match p.rsplit_once('/') {
                Some((f, n)) => (format!("{f}/"), n.to_string()),
                None => (String::new(), p.clone()),
            };
            crate::obj! {
                "path" => p.clone(), "folder" => folder, "name" => name, "named" => e.named,
                "marks" => Value::List(marks),
                "cited_by" => Value::List(e.cited_by.iter().map(|(k, id)| crate::obj! {"kind" => k.clone(), "id" => id.clone()}).collect()),
                "lines" => Value::List(e.lines.iter().map(|n| Value::Int(*n as i64)).collect()),
                "changed_since" => if e.changed { "run" } else { "" },
                "git" => if e.ignored { "ignored".to_string() } else if !in_git { "not_in_git".to_string() } else if e.uncommitted.is_empty() { "committed".to_string() } else { e.uncommitted.clone() },
            }
        })
        .collect();
    Ok(Value::List(out))
}

/// The three sections of the Files tab, by path: Cited (most cited first), Changed (changed since a run read it, or
/// not committed), and every file (the tree).
pub fn sections(files: &[Value]) -> (Vec<&Value>, Vec<&Value>) {
    let has = |f: &Value, k: &str| matches!(f.get("marks"), Some(Value::List(m)) if m.iter().any(|x| x.get("key").and_then(Value::as_str) == Some(k)));
    let n_of = |f: &Value| match f.get("marks") {
        Some(Value::List(m)) => m.iter().filter(|x| matches!(x.get("key").and_then(Value::as_str), Some("cited" | "setting"))).filter_map(|x| x.get("n").and_then(Value::as_i64)).sum::<i64>(),
        _ => 0,
    };
    let mut cited: Vec<&Value> = files.iter().filter(|f| has(f, "cited") || has(f, "setting")).collect();
    cited.sort_by_key(|f| -n_of(f));
    let changed: Vec<&Value> = files.iter().filter(|f| has(f, "changed") || has(f, "uncommitted")).collect();
    (cited, changed)
}

/// A file's marks as one string of glyphs and counts: "❝ 2 ⚙ 7 ±" (`ascii`: the fallback forms).
pub fn marks_said(f: &Value, ascii: bool) -> String {
    match f.get("marks") {
        Some(Value::List(m)) => m
            .iter()
            .map(|x| {
                let g = x.get(if ascii { "ascii" } else { "glyph" }).map(py_str).unwrap_or_default();
                match x.get("n").and_then(Value::as_i64) {
                    Some(n) => format!("{g} {n}"),
                    None => g,
                }
            })
            .collect::<Vec<_>>()
            .join(" "),
        _ => String::new(),
    }
}

/// At most this many lines of a file its card shows whole, and this many bytes read for it.
pub const WHOLE_LINES: usize = 2000;
pub const WHOLE_BYTES: u64 = 2 * 1024 * 1024;

/// A file's lines, as its card shows it whole (owner, Nov 13 2026): on rb open the file in the checkout shown as it is
/// there, and on a page that carries the record, the copy the bundle carries (`root` its record's folder). `lines` are
/// the text's lines, at most WHOLE_LINES (`more`: how many past them); `why` says why there are none (not a text file,
/// too large, not there); `commit` the commit that holds these bytes, where git says the file is committed and the
/// working copy is as that commit holds it. Never a file outside the project, in git's folder or rb's, or past a link.
pub fn whole(root: &std::path::Path, path: &str, commit: &str) -> Value {
    let said = |why: String| crate::obj! {"lines" => Value::List(Vec::new()), "more" => 0, "why" => why, "commit" => ""};
    let first = path.split('/').next().unwrap_or("");
    if path.is_empty() || first == ".git" || first == ".rb" || path.split('/').any(|p| p == ".." || p.is_empty()) || !crate::sources::inside_project(root, path) {
        return said(format!("{path} is not a file of this project"));
    }
    let full = root.join(path);
    let meta = match std::fs::symlink_metadata(&full) {
        Ok(m) => m,
        Err(_) => return said(format!("{path} is not here")),
    };
    // a link is never followed, a folder, a pipe or a device never read
    if !meta.is_file() {
        return said(format!("{path} is not a file"));
    }
    if meta.len() > WHOLE_BYTES {
        return said(format!("{path} is larger than 2 MB"));
    }
    let Ok(bytes) = std::fs::read(&full) else { return said(format!("{path} could not be read")) };
    if bytes.contains(&0) {
        return said(format!("{path} is not a text file"));
    }
    let text = String::from_utf8_lossy(&bytes);
    let mut all: Vec<&str> = text.split('\n').map(|l| l.strip_suffix('\r').unwrap_or(l)).collect();
    if text.ends_with('\n') {
        all.pop();
    }
    let more = all.len().saturating_sub(WHOLE_LINES);
    let lines: Vec<Value> = all.iter().take(WHOLE_LINES).map(|l| Value::Str(l.to_string())).collect();
    crate::obj! {"lines" => Value::List(lines), "more" => more as i64, "why" => "", "commit" => commit}
}
