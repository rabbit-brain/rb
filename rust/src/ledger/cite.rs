//! Citations: every `path:LINE` and `path:LINE-END` a note, a source or a call names, resolved to the lines it cites,
//! the commit they were read at and the file's sha256, so a screen can open what a citation names (CITE, item 1). What
//! a page that leaves the machine may quote is decided here too (item 4): the export reads the commit, never the working
//! copy, and quotes no file git does not track there, no log and no session or agent transcript.

use crate::value::{Dict, Value};
use indexmap::IndexMap;
use std::path::Path;

/// At most this many lines a citation shows, its context included.
pub const MOST_LINES: usize = 8;
/// A quoted line longer than this is cut, then ` ...`.
const MOST_CHARS: usize = 400;
/// What a shared page says in place of lines it may not quote.
pub const NOT_QUOTED: &str = "Not quoted on a shared page";

/// One citation in a text: its byte span and its key (`README.md:3`, `README.md:42-43`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Span {
    pub start: usize,
    pub end: usize,
    pub key: String,
    pub path: String,
    pub line: usize,
    pub last: usize,
}

fn path_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '/' | '-')
}

/// The last parts of a host name, which a bare `name.com:443` ends with.
const HOSTS: &[&str] = &["com", "org", "net", "io", "ai", "dev", "edu", "gov", "co", "uk", "de", "app", "cloud", "internal", "local", "localhost"];

/// Whether `path` names a file in the project the way a citation does: relative, no `..`, a last part with an
/// extension holding a letter (`README.md`, `agent.log`; never `1.5`, `step`).
fn citable(path: &str) -> bool {
    if path.is_empty() || path.starts_with('/') || path.split('/').any(|p| p == ".." || p.is_empty()) {
        return false;
    }
    let last = path.rsplit('/').next().unwrap_or(path);
    match last.rsplit_once('.') {
        // a number with a unit (`12.5s`) and an address's host (`example.com:8787`) are no files
        Some((stem, ext)) => {
            let stem = stem.trim_start_matches('.');
            !stem.is_empty()
                && !stem.chars().all(|c| c.is_ascii_digit() || c == '.')
                && !ext.is_empty()
                && ext.chars().any(|c| c.is_ascii_alphabetic())
                && ext.chars().all(|c| c.is_ascii_alphanumeric())
                && !(path == last && HOSTS.contains(&ext.to_ascii_lowercase().as_str()))
        }
        None => false,
    }
}

/// Every citation in `text`, in order.
pub fn spans(text: &str) -> Vec<Span> {
    let b = text.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] != b':' {
            i += 1;
            continue;
        }
        // the path runs back from the colon over path characters
        let mut s = i;
        while s > 0 && path_char(b[s - 1] as char) {
            s -= 1;
        }
        // an address (`https://host:80`, `s3://b/x.json:3`) or a word run on (`a:b.md:3`) is none
        let before_ok = s == 0 || !matches!(b[s - 1], b':' | b'@' | b'#' | b'~' | b'\\' | b'$' | b'%' | b'{' | b'=');
        let raw_path = &text[s..i];
        let path = raw_path.strip_prefix("./").unwrap_or(raw_path).trim_end_matches('.');
        let path_end = s + (raw_path.len() - raw_path.trim_start_matches("./").len()) + path.len();
        let mut j = i + 1;
        while j < b.len() && b[j].is_ascii_digit() {
            j += 1;
        }
        if j == i + 1 || !before_ok || path_end != i || !citable(path) {
            i += 1;
            continue;
        }
        let line: usize = match text[i + 1..j].parse() {
            Ok(n) if n > 0 => n,
            _ => {
                i = j;
                continue;
            }
        };
        let mut last = line;
        let mut end = j;
        if j + 1 < b.len() && b[j] == b'-' && b[j + 1].is_ascii_digit() {
            let mut k = j + 1;
            while k < b.len() && b[k].is_ascii_digit() {
                k += 1;
            }
            if let Ok(n) = text[j + 1..k].parse::<usize>() {
                if n >= line {
                    last = n;
                    end = k;
                }
            }
        }
        // a time or a run of colons (`a.md:3:4`), or a number running into a word (`x.md:3rd`), is none
        if end < b.len() && (b[end] == b':' && end + 1 < b.len() && b[end + 1].is_ascii_digit() || b[end].is_ascii_alphabetic() || b[end] == b'_') {
            i = end;
            continue;
        }
        let key = if last == line { format!("{path}:{line}") } else { format!("{path}:{line}-{last}") };
        let start = s + (raw_path.len() - raw_path.trim_start_matches("./").len());
        out.push(Span { start, end, key, path: path.to_string(), line, last });
        i = end;
    }
    out
}

/// A file a shared page never quotes, tracked or not: a log (any folder named `logs`), a file under a dot folder (an
/// agent's or editor's own), and one whose name says it is a session or agent transcript.
pub fn never_shared(path: &str) -> bool {
    let parts: Vec<&str> = path.split('/').collect();
    let (folders, name) = parts.split_at(parts.len() - 1);
    // a dot file (`.env`) or anything under a dot folder (an agent's or editor's own) too
    if folders.iter().any(|f| f.eq_ignore_ascii_case("logs") || f.starts_with('.')) || name[0].starts_with('.') {
        return true;
    }
    // by its words, never a word inside another (`management.md` is no agent's)
    let name = name[0].to_ascii_lowercase();
    name.split(|c: char| !c.is_ascii_alphabetic()).any(|w| {
        ["transcript", "transcripts", "session", "sessions", "chat", "chats", "conversation", "conversations", "agent", "agents"].contains(&w)
    })
}

/// Where the document's text was found: the kind and id of the note, rule, call, experiment, question or run note that
/// says it, or None (history, the log, a recipe, a resource's acquisition).
pub type CitedBy = Option<(String, String)>;

/// How the citations of one document are read.
pub struct Reader<'a> {
    pub root: &'a Path,
    /// a page that leaves the machine: the commit only, and item 4's rule
    pub shared: bool,
    /// rb open: a link that opens the file
    pub live: bool,
    /// each file read once a document
    cache: std::cell::RefCell<std::collections::HashMap<String, Read>>,
}

impl<'a> Reader<'a> {
    pub fn new(root: &'a Path, shared: bool, live: bool) -> Reader<'a> {
        Reader { root, shared, live, cache: Default::default() }
    }
}

/// A file larger than this is not read for a citation.
pub const MOST_BYTES: u64 = 16 * 1024 * 1024;

#[derive(Clone)]
struct Read {
    tracked: bool,
    bytes: Option<Vec<u8>>,
    /// read from the commit (a shared page, or a file gone from the working copy), else from the file as it is now
    at_commit: bool,
    changed: bool,
    /// why no bytes were read where the file is there: too large, not a file, not readable, a link on a shared page
    why_not: String,
}

fn git_head(root: &Path) -> String {
    crate::sources::git(root, &["rev-parse", "--verify", "-q", "HEAD"]).map(|s| s.trim().to_string()).unwrap_or_default()
}

/// The path of `path` (from the project root) inside its git repository.
fn in_repo(root: &Path, path: &str) -> Option<String> {
    let top = crate::sources::git_top(root)?;
    crate::sources::rel_to(&crate::sources::resolve_path(root).join(path), &top)
}

impl Reader<'_> {
    fn read(&self, path: &str, commit: &str) -> Read {
        let none = |tracked: bool, at_commit: bool, why: String| Read { tracked, bytes: None, at_commit, changed: false, why_not: why };
        // what git holds at the commit: its mode (a link is 120000) and size, asked before its bytes
        let rel = (!commit.is_empty()).then(|| in_repo(self.root, path)).flatten();
        let entry = rel.as_ref().and_then(|rel| crate::sources::git(self.root, &["ls-tree", "-l", commit, "--", rel])).and_then(|l| {
            let (meta, _) = l.trim_end().split_once('\t')?;
            let f: Vec<&str> = meta.split_whitespace().collect();
            (f.len() == 4 && f[1] == "blob").then(|| (f[0].to_string(), f[3].parse::<u64>().unwrap_or(u64::MAX)))
        });
        let tracked = entry.is_some();
        let blob = || -> Read {
            match &entry {
                None => none(false, true, String::new()),
                // a link is never followed on a shared page: what it names is not what git holds there
                Some((mode, _)) if mode == "120000" => none(false, true, String::new()),
                Some((_, size)) if *size > MOST_BYTES => none(true, true, format!("{path} is larger than 16 MB, and rb quotes no line of it")),
                Some(_) => match rel.as_ref().and_then(|rel| crate::sources::git_bytes(self.root, &["cat-file", "blob", &format!("{commit}:{rel}")])) {
                    Some(b) => Read { tracked: true, bytes: Some(b), at_commit: true, changed: false, why_not: String::new() },
                    None => none(true, true, format!("{path} could not be read at commit {}", commit.chars().take(7).collect::<String>())),
                },
            }
        };
        if self.shared {
            return blob();
        }
        let full = self.root.join(path);
        if !crate::sources::inside_project(self.root, path) {
            return none(tracked, false, String::new());
        }
        let meta = match std::fs::metadata(&full) {
            Ok(m) => m,
            // a file git holds and the working copy has lost is read as the commit holds it
            Err(_) if tracked => return Read { changed: true, ..blob() },
            Err(_) => return none(false, false, String::new()),
        };
        // a pipe, a device or a folder is never read: reading one could wait forever
        if !meta.is_file() {
            return none(tracked, false, format!("{path} is not a file"));
        }
        if meta.len() > MOST_BYTES {
            return none(tracked, false, format!("{path} is larger than 16 MB, and rb quotes no line of it"));
        }
        let now = match std::fs::read(&full) {
            Ok(b) => b,
            Err(_) => return none(tracked, false, format!("{path} could not be read")),
        };
        let changed = match blob().bytes {
            Some(b) => b != now && crate::sources::other_line_ending(&now).as_deref() != Some(b.as_slice()),
            None => false,
        };
        Read { tracked, bytes: Some(now), at_commit: false, changed, why_not: String::new() }
    }

    /// The citation `span` names, resolved.
    fn resolve(&self, sp: &Span, commit: &str) -> Dict {
        let (folder, name) = match sp.path.rsplit_once('/') {
            Some((f, n)) => (format!("{f}/"), n.to_string()),
            None => (String::new(), sp.path.clone()),
        };
        let label = if sp.last == sp.line { format!("{} · line {}", sp.path, sp.line) } else { format!("{} · lines {}-{}", sp.path, sp.line, sp.last) };
        let short: String = commit.chars().take(7).collect();
        let mut d = Dict::new();
        d.insert("key".into(), sp.key.clone().into());
        d.insert("raw".into(), Value::List(Vec::new()));
        d.insert("path".into(), sp.path.clone().into());
        d.insert("name".into(), name.into());
        d.insert("folder".into(), folder.into());
        d.insert("line".into(), sp.line.into());
        d.insert("end".into(), sp.last.into());
        d.insert("label".into(), label.into());
        d.insert("commit".into(), commit.into());
        d.insert("commit_short".into(), short.clone().into());
        let open_url = if self.live {
            let abs = crate::sys::slashed(&crate::sources::resolve_path(&self.root.join(&sp.path)));
            // a POSIX path keeps its leading slash; a Windows one (C:/...) takes one
            if abs.starts_with('/') { format!("vscode://file{abs}:{}", sp.line) } else { format!("vscode://file/{abs}:{}", sp.line) }
        } else {
            String::new()
        };
        let r = self.cache.borrow_mut().entry(sp.path.clone()).or_insert_with(|| self.read(&sp.path, commit)).clone();
        let withheld = self.shared && (!r.tracked || never_shared(&sp.path));
        let (mut read, mut sha, mut quoted, mut lines, mut cut, mut missing) = (String::new(), String::new(), false, Vec::new(), 0usize, String::new());
        let at = if r.at_commit || (r.tracked && !r.changed) { if short.is_empty() { String::new() } else { format!(" at commit {short}") } } else { String::new() };
        if !withheld {
            match &r.bytes {
                None if !r.why_not.is_empty() => missing = r.why_not.clone(),
                None => missing = format!("{} is not in the repository{at}", sp.path),
                Some(bytes) => {
                    read = if r.at_commit && r.changed {
                        format!("read at commit {short}; the file is gone from the working copy")
                    } else if r.at_commit || (r.tracked && !r.changed) {
                        format!("read at commit {short}")
                    } else if r.tracked {
                        format!("read from the file as it is now; it differs from commit {short}")
                    } else if !commit.is_empty() {
                        "read from the file as it is now; git does not track it".into()
                    } else {
                        "read from the file as it is now".into()
                    };
                    sha = crate::sources::sha256_bytes(bytes);
                    match Some(String::from_utf8_lossy(bytes)).filter(|t| !t.contains('\0')) {
                        None => missing = format!("{} is not a text file", sp.path),
                        Some(t) => {
                            let all: Vec<&str> = t.split('\n').map(|l| l.strip_suffix('\r').unwrap_or(l)).collect();
                            // a file ending in a newline has no line after it
                            let count = if t.is_empty() { 0 } else if t.ends_with('\n') { all.len() - 1 } else { all.len() };
                            if sp.line > count {
                                missing = format!("{} has no line {}{at}", sp.path, sp.line);
                            } else {
                                let last = sp.last.min(count);
                                if last < sp.last {
                                    missing = format!("{} has no line {}{at}", sp.path, last + 1);
                                }
                                let from = sp.line.saturating_sub(1).max(1);
                                let to = (last + 1).min(count);
                                let shown_to = to.min(from + MOST_LINES - 1);
                                cut = last.saturating_sub(shown_to);
                                for n in from..=shown_to {
                                    let full = all[n - 1];
                                    let text = if full.chars().count() > MOST_CHARS { format!("{} ...", full.chars().take(MOST_CHARS).collect::<String>()) } else { full.to_string() };
                                    lines.push(crate::obj! {"n" => n, "text" => text, "cited" => n >= sp.line && n <= last});
                                }
                                quoted = true;
                            }
                        }
                    }
                }
            }
        }
        d.insert("read".into(), read.into());
        d.insert("sha256".into(), sha.into());
        d.insert("tracked".into(), r.tracked.into());
        d.insert("quoted".into(), quoted.into());
        d.insert("lines".into(), Value::List(lines));
        d.insert("cut".into(), cut.into());
        d.insert("missing".into(), missing.into());
        d.insert("not_quoted".into(), if withheld { NOT_QUOTED } else { "" }.into());
        d.insert("open_url".into(), open_url.into());
        d.insert("cited_by".into(), Value::List(Vec::new()));
        d
    }
}

/// Keys whose strings are commands, ids, digests, commits or paths, never text that cites.
const NOT_TEXT: &[&str] = &["command", "commands", "confirm", "confirm_all", "do", "list", "digest", "sha256", "commit", "head", "tree", "path", "paths", "id", "ids", "key", "url", "open_url", "copy", "recipe", "run_in", "subject"];

/// Every citation in the document's text, with the object each sits on.
fn collect(v: &Value, at: &[String], by: &dyn Fn(&[String]) -> CitedBy, out: &mut IndexMap<String, (Span, Vec<String>, Vec<(String, String)>)>) {
    match v {
        Value::Dict(d) => {
            for (k, x) in d {
                if NOT_TEXT.contains(&k.as_str()) {
                    continue;
                }
                let mut p = at.to_vec();
                p.push(k.clone());
                collect(x, &p, by, out);
            }
        }
        Value::List(l) => {
            for (i, x) in l.iter().enumerate() {
                let mut p = at.to_vec();
                p.push(i.to_string());
                collect(x, &p, by, out);
            }
        }
        Value::Str(s) => {
            for sp in spans(s) {
                let raw = s[sp.start..sp.end].to_string();
                let e = out.entry(sp.key.clone()).or_insert_with(|| (sp.clone(), Vec::new(), Vec::new()));
                if !e.1.contains(&raw) {
                    e.1.push(raw);
                }
                if let Some(who) = by(at) {
                    if !e.2.contains(&who) {
                        e.2.push(who);
                    }
                }
            }
        }
        _ => {}
    }
}

/// The object a string at `at` in the view document sits on (CITE-FIELDS.md's cited_by).
fn cited_by_in(doc: &Value) -> impl Fn(&[String]) -> CitedBy + '_ {
    move |at: &[String]| {
        let get = |path: &[&str]| -> Option<&Value> {
            let mut v = doc;
            for k in path {
                v = match v {
                    Value::Dict(d) => d.get(*k)?,
                    Value::List(l) => l.get(k.parse::<usize>().ok()?)?,
                    _ => return None,
                };
            }
            Some(v)
        };
        let s = |v: Option<&Value>| v.and_then(Value::as_str).map(str::to_string).filter(|x| !x.is_empty());
        let a: Vec<&str> = at.iter().map(String::as_str).collect();
        let pair = |k: &str, id: Option<String>| id.map(|id| (k.to_string(), id));
        match a.as_slice() {
            ["home", "notes", i, ..] => pair("note", s(get(&["home", "notes", i, "id"]))),
            ["experiments", i, "notes", j, ..] => pair("note", s(get(&["experiments", i, "note_ids", j]))),
            // a recipe's steps and settings cite where they came from: the Source view lists the objects that say a citation
            ["experiments", _, "reproduce", ..] => None,
            ["experiments", i, ..] => pair("experiment", s(get(&["experiments", i, "id"]))),
            ["findings", id, ..] => pair("criterion", Some(id.to_string())),
            ["questions", i, ..] => pair("question", s(get(&["questions", i, "id"]))),
            ["calls", i, ..] | ["made_calls", i, ..] => pair("call", s(get(&[a[0], i, "subject"]))),
            ["pending", "rows", i, ..] => {
                let id = s(get(&["pending", "rows", i, "id"]))?;
                if s(get(&["pending", "rows", i, "kind"])).as_deref() == Some("design") {
                    pair("experiment", id.split('@').next().map(str::to_string))
                } else {
                    pair("call", Some(id))
                }
            }
            ["runs", id, "note"] => pair("run", Some(id.to_string())),
            _ => None,
        }
    }
}

/// The document's citations, each resolved as `reader` reads them, keyed as CITE-FIELDS.md says.
pub fn of_document(doc: &Value, reader: &Reader) -> Value {
    let mut found = IndexMap::new();
    let by = cited_by_in(doc);
    collect(doc, &[], &by, &mut found);
    let commit = if found.is_empty() { String::new() } else { git_head(reader.root) };
    let mut out = Dict::new();
    for (key, (sp, raw, by)) in found {
        let mut d = reader.resolve(&sp, &commit);
        d.insert("raw".into(), Value::list_of_str(raw));
        d.insert("cited_by".into(), Value::List(by.into_iter().map(|(k, id)| crate::obj! {"kind" => k, "id" => id}).collect()));
        out.insert(key, Value::Dict(d));
    }
    Value::Dict(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys(t: &str) -> Vec<String> {
        spans(t).into_iter().map(|s| s.key).collect()
    }

    #[test]
    fn finds_file_line_citations_and_nothing_else() {
        assert_eq!(keys("A note: see README.md:3 and README.md:7."), vec!["README.md:3", "README.md:7"]);
        assert_eq!(keys("cited logs/agent.log:2809; the plan is at logs/agent.log:2793 and 2811"), vec!["logs/agent.log:2809", "logs/agent.log:2793"]);
        assert_eq!(keys("(README.md:42-43)"), vec!["README.md:42-43"]);
        assert_eq!(keys("./configs/train.yaml:3"), vec!["configs/train.yaml:3"]);
        let s = spans("see ./configs/train.yaml:3,");
        assert_eq!(&"see ./configs/train.yaml:3,"[s[0].start..s[0].end], "configs/train.yaml:3");
        for none in ["https://host.com:8787", "s3://b/x.json:3", "at 10:30", "step:3", "/abs/x.md:3", "../x.md:3", "a.md:3:4", "x.md:3rd", "v1.5:3", "x.md:0", "README.md, notes.md", "x.md:", "example.com:8787", "api.github.com:443", "12.5s:3", "$HOME/x.md:3", "${ROOT}/x.md:3"] {
            assert!(keys(none).is_empty(), "{none}: {:?}", keys(none));
        }
        // a range read backwards is one line
        assert_eq!(keys("x.md:7-3"), vec!["x.md:7"]);
    }

    #[test]
    fn a_shared_page_never_quotes_logs_or_transcripts() {
        for p in ["logs/agent.log", "out/logs/run.txt", ".claude/x.md", "session-3.jsonl", "chat.md", "notes/agent_trace.txt", "Transcript.md"] {
            assert!(never_shared(p), "{p}");
        }
        for p in [".env.local", "AgentLog.md"] {
            assert!(never_shared(p) == (p == ".env.local"), "{p}");
        }
        for p in ["README.md", "notes.md", "configs/train.yaml", "docs/plan.md", "catalog.md", "management.md", "reagents.csv", "chateau.md"] {
            assert!(!never_shared(p), "{p}");
        }
    }
}
