//! The repository compiler: what a repository's own files say about its experiments, read into the record without
//! paraphrase: results folders, their reports quoted with file and line, manifests, audits and checks, and the research
//! diff `rb reconcile --apply` logs.

use crate::errors::Res;
use crate::ledger::{py_splitlines, Ledger, DIR};
use crate::pyjson;
use crate::record::*;
use crate::sources::{self, git};
use crate::value::{Dict, DictExt, Value};
use fancy_regex::Regex as FRegex;
use indexmap::IndexMap;
use regex::Regex;
use std::collections::{BTreeSet, HashSet};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// Where results land: the one list the journal walks, reconcile compiles and every read names (rb looks in ...).
pub const RESULT_ROOTS: &[&str] = crate::journal::DEFAULT_DIRS;
pub const REPORT_NAMES: &[&str] = &["readme.md", "report.md", "results.md", "summary.md", "readme.markdown", "readme.txt"];
pub const STRUCTURED: &[&str] = &[".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".jsonl"];
pub const DATA: &[&str] = &[
    ".npz", ".npy", ".pt", ".pth", ".ckpt", ".safetensors", ".h5", ".hdf5", ".bin", ".pkl", ".pickle", ".parquet", ".arrow", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
    ".gif", ".mp4", ".zip", ".gz", ".tar", ".tgz", ".xz", ".zst",
];
pub const SKIP: &[&str] = &[".git", DIR, "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ipynb_checkpoints"];
pub const MAX_HASH: u64 = 64 * 1024 * 1024;
pub const MAX_FOLDER_FILES: usize = 2000;
pub const MAX_LISTED: usize = 60;
pub const MAX_DOC_BYTES: u64 = 2 * 1024 * 1024;
pub const PASS_KEYS: &[&str] = &["passed", "pass", "ok", "success", "all_passed", "valid"];
pub const PASS_WORDS: &[(&str, bool)] =
    &[("pass", true), ("passed", true), ("ok", true), ("success", true), ("succeeded", true), ("fail", false), ("failed", false), ("error", false), ("failure", false)];

pub const SECTIONS: &[(&str, &[&str])] = &[
    ("result", &["result", "conclusion", "finding", "outcome", "verdict", "takeaway"]),
    ("limitation", &["limitation", "caveat", "known issue", "not included", "threats to validity"]),
    ("next", &["next step", "next", "future work", "open question", "follow-up", "follow up", "todo"]),
];
pub const REPRODUCTION_WORDS: &[&str] = &["reproduc", "regenerat", "rerun", "re-run", "how to run", "fresh clone", "fresh checkout"];

fn re(cell: &'static OnceLock<Regex>, pat: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pat).unwrap())
}

fn link_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r#"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)"#)
}
fn module_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"(?:python3?\s+(?:-\S+\s+)*-m\s+|\bfrom\s+|\bimport\s+)([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)")
}
fn pypath_re() -> &'static FRegex {
    static R: OnceLock<FRegex> = OnceLock::new();
    R.get_or_init(|| FRegex::new(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.py)\b").unwrap())
}
fn hex64(s: &str) -> bool {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"^[0-9a-fA-F]{64}\n?\z").is_match(s)
}
pub fn check_name(name: &str) -> bool {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"^(?:(?:audit|test|check|verify)_[\w.-]+\.py|[\w.-]+_test\.py)\n?\z").is_match(name)
}
fn audit_name(stem: &str) -> bool {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"(?i)(audit|check|verif)").is_match(stem)
}
fn heading_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"^(#{1,6})\s+(.*?)\s*#*\s*$")
}
fn heading_start_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"^#{1,6}\s")
}
fn list_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"^\s*([-*+]|\d+[.)])\s")
}

fn strip(s: &str) -> &str {
    crate::rbyaml::py_strip(s)
}

fn name_of(p: &Path) -> String {
    p.file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default()
}

/// Path.suffix, lowered.
fn suffix(name: &str) -> String {
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => name[i..].to_lowercase(),
        _ => String::new(),
    }
}

/// Path.stem
fn stem(name: &str) -> &str {
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => &name[..i],
        _ => name,
    }
}

fn parts_key(p: &Path) -> Vec<String> {
    p.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect()
}

fn rel_posix(p: &Path, base: &Path) -> String {
    p.strip_prefix(base).map(|r| r.to_string_lossy().replace('\\', "/")).unwrap_or_else(|_| p.to_string_lossy().into_owned())
}

fn file_size(p: &Path) -> u64 {
    std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
}

/// How many "\n" the first `byte` bytes of `s` hold.
fn newlines_before(s: &str, byte: usize) -> usize {
    s[..byte].matches('\n').count()
}

// ---------------------------------------------------------------- reading a markdown file, with its lines

#[derive(Debug, Clone)]
pub struct Block {
    pub kind: &'static str,
    pub start: usize,
    pub end: usize,
    pub text: String,
    pub heading: String,
    pub level: usize,
}

#[derive(Debug, Clone)]
pub struct Doc {
    pub path: String,
    pub blocks: Vec<Block>,
    pub links: Vec<(String, usize)>,
    pub code_refs: Vec<(String, usize)>,
}

impl Doc {
    pub fn title_index(&self) -> Option<usize> {
        let heads: Vec<usize> = self.blocks.iter().enumerate().filter(|(_, b)| b.kind == "heading").map(|(i, _)| i).collect();
        heads.iter().copied().find(|i| self.blocks[*i].level == 1).or_else(|| heads.first().copied())
    }
    pub fn title(&self) -> Option<&Block> {
        self.title_index().map(|i| &self.blocks[i])
    }
    /// The opening lines: the first paragraph under the title, or the first paragraph at all.
    pub fn summary(&self) -> Option<&Block> {
        let t = self.title();
        for b in &self.blocks {
            if (b.kind == "para" || b.kind == "list") && t.map_or(true, |t| b.start > t.start && b.heading == t.text) {
                return Some(b);
            }
            if let Some(t) = t {
                if b.kind == "heading" && b.start > t.start {
                    return None;
                }
            }
        }
        None
    }
    /// (kind, heading, first block under it) for every heading that says it is a result, a limitation or a next step.
    pub fn sections(&self) -> Vec<(&'static str, &Block, &Block)> {
        let mut out = Vec::new();
        let ti = self.title_index();
        for (i, b) in self.blocks.iter().enumerate() {
            if b.kind != "heading" || Some(i) == ti {
                continue;
            }
            let Some(kind) = section_kind(&b.text) else { continue };
            let body = self.blocks[i + 1..].iter().find(|x| x.kind != "heading" || x.level <= b.level);
            if let Some(body) = body {
                if body.kind == "para" || body.kind == "list" {
                    out.push((kind, b, body));
                }
            }
        }
        out
    }
    pub fn commands(&self) -> Vec<&Block> {
        self.blocks.iter().filter(|b| b.kind == "code" && looks_like_commands(b)).collect()
    }
}

fn section_kind(heading: &str) -> Option<&'static str> {
    let h = heading.to_lowercase();
    for (kind, words) in SECTIONS {
        for w in *words {
            let r = Regex::new(&format!(r"\b{}", regex::escape(w))).unwrap();
            if r.is_match(&h) {
                return Some(kind);
            }
        }
    }
    None
}

fn looks_like_commands(b: &Block) -> bool {
    let first = py_splitlines(&b.text).into_iter().map(strip).find(|l| !l.is_empty()).unwrap_or("");
    if first.is_empty() {
        return false;
    }
    !["{", "[", "<", "def ", "class ", "import ", "from ", "#!/", "//"].iter().any(|p| first.starts_with(p))
}

/// A markdown file as blocks with their lines, and the repository paths and code it names.
pub fn read_doc(root: &Path, rel: &str) -> Option<Doc> {
    let p = root.join(rel);
    let md = std::fs::metadata(&p).ok()?;
    if md.len() > MAX_DOC_BYTES {
        return None;
    }
    let bytes = std::fs::read(&p).ok()?;
    let text = String::from_utf8_lossy(&bytes).into_owned();
    let lines: Vec<&str> = py_splitlines(&text);
    let mut blocks: Vec<Block> = Vec::new();
    let mut heading = String::new();
    let n = lines.len();
    let mut i = 0;
    while i < n {
        let line = lines[i];
        let s = strip(line);
        if s.starts_with("```") || s.starts_with("~~~") {
            let fence = &s[..3];
            let body_start = i + 1;
            let mut j = body_start;
            while j < n && !strip(lines[j]).starts_with(fence) {
                j += 1;
            }
            if j > body_start {
                blocks.push(Block { kind: "code", start: body_start + 1, end: j, text: lines[body_start..j].join("\n"), heading: heading.clone(), level: 0 });
            }
            i = j + 1;
            continue;
        }
        if let Some(m) = heading_re().captures(line) {
            heading = strip(m.get(2).map(|x| x.as_str()).unwrap_or("")).to_string();
            blocks.push(Block { kind: "heading", start: i + 1, end: i + 1, text: heading.clone(), heading: heading.clone(), level: m.get(1).unwrap().as_str().len() });
            i += 1;
            continue;
        }
        if s.is_empty() {
            i += 1;
            continue;
        }
        let mut j = i;
        while j < n && !strip(lines[j]).is_empty() && !strip(lines[j]).starts_with("```") && !strip(lines[j]).starts_with("~~~") && !heading_start_re().is_match(lines[j]) {
            j += 1;
        }
        let kind = if list_re().is_match(lines[i]) { "list" } else { "para" };
        blocks.push(Block { kind, start: i + 1, end: j, text: lines[i..j].join("\n"), heading: heading.clone(), level: 0 });
        i = j;
    }
    let mut doc = Doc { path: rel.to_string(), blocks: blocks.clone(), links: Vec::new(), code_refs: Vec::new() };
    let base = root.join(rel).parent().map(|p| p.to_path_buf()).unwrap_or_else(|| root.to_path_buf());
    for b in &blocks {
        if b.kind != "code" {
            for m in link_re().captures_iter(&b.text) {
                let g = m.get(1).unwrap();
                let target = g.as_str().split('#').next().unwrap_or("");
                if target.is_empty() || target.contains("://") || target.starts_with("mailto:") || target.starts_with('#') {
                    continue;
                }
                let line_no = b.start + newlines_before(&b.text, m.get(0).unwrap().start());
                if let Some(resolved) = inside(root, &base.join(target)) {
                    doc.links.push((resolved, line_no));
                }
            }
        }
        for m in module_re().captures_iter(&b.text) {
            let cand = format!("{}.py", m.get(1).unwrap().as_str().replace('.', "/"));
            if root.join(&cand).is_file() {
                doc.code_refs.push((cand, b.start + newlines_before(&b.text, m.get(0).unwrap().start())));
            }
        }
        for m in pypath_re().captures_iter(&b.text).flatten() {
            let g = m.get(1).unwrap().as_str().to_string();
            let line = b.start + newlines_before(&b.text, m.get(0).unwrap().start());
            for cand in [Some(g.clone()), inside(root, &base.join(&g))] {
                if let Some(c) = cand.filter(|c| !c.is_empty()) {
                    if root.join(&c).is_file() {
                        doc.code_refs.push((c, line));
                        break;
                    }
                }
            }
        }
    }
    let mut seen = HashSet::new();
    doc.code_refs.retain(|x| seen.insert(x.clone()));
    Some(doc)
}

/// A path inside the project as a repository path, when it exists.
fn inside(root: &Path, p: &Path) -> Option<String> {
    let rp = crate::sys::canonicalize(p).ok().or_else(|| Some(sources::resolve_path(p)))?;
    let rr = sources::resolve_path(root);
    let rel = rp.strip_prefix(&rr).ok()?;
    let s = rel.to_string_lossy().replace('\\', "/");
    let s = if s.is_empty() { ".".to_string() } else { s };
    if root.join(rel).exists() {
        Some(s)
    } else {
        None
    }
}

fn quote(kind: &str, doc: &Doc, b: &Block, heading: &str) -> Option<Quote> {
    let text = b.text.trim_matches('\n');
    if strip(text).is_empty() {
        return None;
    }
    let cut = text.chars().count() > TEXT;
    let lines = if b.start == b.end { b.start.to_string() } else { format!("{}-{}", b.start, b.end) };
    Some(Quote {
        kind: kind.into(),
        text: text.chars().take(TEXT).collect(),
        path: doc.path.clone(),
        lines,
        heading: heading.chars().take(200).collect(),
        cut,
    })
}

/// A command block's commands: lines joined where they end in a backslash, each with the line it starts on.
pub fn logical_lines(b: &Block) -> Vec<(usize, String)> {
    let mut out = Vec::new();
    let mut buf: Vec<&str> = Vec::new();
    let mut start = 0;
    for (k, ln) in py_splitlines(&b.text).into_iter().enumerate() {
        if buf.is_empty() {
            start = b.start + k;
        }
        buf.push(ln);
        if !ln.trim_end_matches(|c: char| crate::rbyaml::py_isspace(c)).ends_with('\\') {
            let joined = strip(&buf.join("\n")).to_string();
            if !joined.is_empty() {
                out.push((start, joined));
            }
            buf.clear();
        }
    }
    if !buf.is_empty() {
        out.push((start, strip(&buf.join("\n")).to_string()));
    }
    out
}

// ---------------------------------------------------------------- git, as it stands

#[derive(Debug, Clone, Default)]
pub struct GitView {
    pub commit: Option<String>,
    pub branch: Option<String>,
    pub tracked: HashSet<String>,
    pub untracked: HashSet<String>,
    pub ignored: HashSet<String>,
    pub modified: HashSet<String>,
    pub present: bool,
}

impl GitView {
    pub fn held(&self, rel: &str) -> &'static str {
        if !self.present {
            return "unknown";
        }
        if self.tracked.contains(rel) {
            return "tracked";
        }
        if self.ignored.contains(rel) || self.ignored.iter().any(|d| d.ends_with('/') && rel.starts_with(d.as_str())) {
            return "ignored";
        }
        "untracked"
    }
}

fn zsplit(s: Option<String>) -> HashSet<String> {
    s.unwrap_or_default().split('\0').filter(|x| !x.is_empty()).map(|x| x.to_string()).collect()
}

/// What git holds under the project root, paths relative to it.
pub fn git_view(root: &Path) -> GitView {
    let Some(listed) = git(root, &["ls-files", "-z", "--cached"]) else { return GitView::default() };
    let head = git(root, &["rev-parse", "--verify", "-q", "HEAD"]);
    let branch = git(root, &["rev-parse", "--abbrev-ref", "HEAD"]);
    let mut gv = GitView {
        commit: head.map(|h| strip(&h).to_string()).filter(|h| !h.is_empty()),
        branch: branch.map(|h| strip(&h).to_string()).filter(|h| !h.is_empty()),
        present: true,
        ..Default::default()
    };
    gv.tracked = zsplit(Some(listed));
    gv.untracked = zsplit(git(root, &["ls-files", "-z", "--others", "--exclude-standard"]));
    gv.ignored = zsplit(git(root, &["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"]));
    gv.modified = if gv.commit.is_some() {
        zsplit(crate::sources::git_diff_bytes(root, &["--name-only", "--relative", "-z", "HEAD"]).map(|b| String::from_utf8_lossy(&b).into_owned()))
    } else {
        HashSet::new()
    };
    gv
}

// ---------------------------------------------------------------- results folders, and what each holds

fn sorted_children(d: &Path) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = match std::fs::read_dir(d) {
        Ok(rd) => rd.flatten().map(|e| e.path()).collect(),
        Err(_) => Vec::new(),
    };
    v.sort_by_key(|p| name_of(p));
    v
}

/// Whether `d` is the top of a worktree of the repository whose shared git directory is `common`: another checkout,
/// whose files are not this one's.
fn other_checkout(d: &Path, common: Option<&Path>) -> bool {
    common.map_or(false, |c| crate::ledger::is_worktree_top(d, c))
}

/// Every folder that holds an experiment's results.
pub fn result_folders(root: &Path) -> Vec<String> {
    let mut out = Vec::new();
    let common = crate::ledger::common_dir_of(root);
    for name in RESULT_ROOTS {
        let base = root.join(name);
        if !base.is_dir() || other_checkout(&base, common.as_deref()) {
            continue;
        }
        for d in sorted_children(&base) {
            let dn = name_of(&d);
            if !d.is_dir() || dn.starts_with('.') || SKIP.contains(&dn.as_str()) || other_checkout(&d, common.as_deref()) {
                continue;
            }
            if holds_results(&d) {
                out.push(format!("{}/", rel_posix(&d, root)));
                continue;
            }
            for dd in sorted_children(&d) {
                let ddn = name_of(&dd);
                if dd.is_dir() && !ddn.starts_with('.') && !SKIP.contains(&ddn.as_str()) && !other_checkout(&dd, common.as_deref()) && holds_results(&dd) {
                    out.push(format!("{}/", rel_posix(&dd, root)));
                }
            }
        }
    }
    out
}

/// What a repository holds of work already done: its commits that touch anything outside the record, its results
/// folders, and the databases in its root or one folder down.
pub struct History {
    pub commits: usize,
    pub folders: Vec<String>,
    pub stores: Vec<String>,
}

impl History {
    /// In rb's words: "45 commits, 2 results folders and a database (results.db)".
    pub fn said(&self) -> String {
        let n = |k: usize, one: &str, many: &str| if k == 1 { format!("1 {one}") } else { format!("{k} {many}") };
        let mut parts = Vec::new();
        if self.commits > 0 {
            parts.push(n(self.commits, "commit", "commits"));
        }
        if !self.folders.is_empty() {
            parts.push(n(self.folders.len(), "results folder", "results folders"));
        }
        if !self.stores.is_empty() {
            let named = self.stores.iter().take(3).cloned().collect::<Vec<_>>().join(", ") + if self.stores.len() > 3 { ", ..." } else { "" };
            parts.push(if self.stores.len() == 1 { format!("a database ({named})") } else { format!("{} databases ({named})", self.stores.len()) });
        }
        match parts.len() {
            0 => String::new(),
            1 => parts.remove(0),
            _ => {
                let last = parts.pop().unwrap_or_default();
                format!("{} and {last}", parts.join(", "))
            }
        }
    }
}

/// The history of the repository at `root`, or None when it holds no work yet.
pub fn history(root: &Path) -> Option<History> {
    // what rb init and rb skill add write is no work: the record, the skill, the MCP entry, the instruction files, the
    // agents' hook files; nor is a new repository's scaffold, its README, license and ignore files, as a hosting
    // service's first commit holds
    let mut spec = vec!["rev-list".to_string(), "--count".into(), "HEAD".into(), "--".into(), ".".into()];
    spec.extend([DIR, ".claude/skills/rabbit-brain", ".cursor/rules/rabbit-brain.mdc", ".mcp.json", "AGENTS.md", ".claude/settings.json", ".codex/hooks.json", ".cursor/hooks.json"].iter().map(|p| format!(":(exclude){p}")));
    spec.extend(["README*", "LICENSE*", "LICENCE*", "COPYING*", ".gitignore", ".gitattributes"].iter().map(|p| format!(":(exclude,glob){p}")));
    let spec: Vec<&str> = spec.iter().map(String::as_str).collect();
    let commits = crate::sources::git(root, &spec)
        .and_then(|c| strip(&c).parse::<usize>().ok())
        .unwrap_or(0);
    let folders = result_folders(root);
    let stores = stores(root);
    (commits > 0 || !folders.is_empty() || !stores.is_empty()).then_some(History { commits, folders, stores })
}

/// The SQLite databases (told by their header) in the root or one folder down, relative to `root`.
pub fn stores(root: &Path) -> Vec<String> {
    let mut stores = Vec::new();
    let mut dirs = vec![root.to_path_buf()];
    let common = crate::ledger::common_dir_of(root);
    dirs.extend(sorted_children(root).into_iter().filter(|d| d.is_dir() && !name_of(d).starts_with('.') && !SKIP.contains(&name_of(d).as_str()) && !other_checkout(d, common.as_deref())));
    for d in dirs {
        for f in sorted_children(&d) {
            let ext = suffix(&name_of(&f).to_lowercase());
            if f.is_file() && [".db", ".sqlite", ".sqlite3", ".db3"].contains(&ext.as_str()) && crate::sqlite::is_database(&f) {
                stores.push(rel_posix(&f, root));
            }
        }
    }
    stores
}

fn holds_results(d: &Path) -> bool {
    let Ok(rd) = std::fs::read_dir(d) else { return false };
    let names: Vec<String> = rd.flatten().map(|e| e.path()).filter(|p| p.is_file()).map(|p| name_of(&p).to_lowercase()).collect();
    names.iter().any(|n| REPORT_NAMES.contains(&n.as_str())) || names.iter().any(|n| STRUCTURED.contains(&suffix(n).as_str()))
}

/// The experiment id a folder gets: its own name, made a valid id.
pub fn folder_id(folder: &str) -> String {
    let name = folder.trim_end_matches('/').rsplit('/').next().unwrap_or("");
    static R: OnceLock<Regex> = OnceLock::new();
    let s = re(&R, r"[^A-Za-z0-9_.-]+").replace_all(name, "-").into_owned();
    let s: String = s.trim_matches(|c| c == '-' || c == '.').chars().take(80).collect();
    let ident = if s.is_empty() { "experiment".to_string() } else { s };
    if id_ok(&ident) {
        ident
    } else {
        "experiment".into()
    }
}

fn walk(root: &Path, folder: &str) -> (Vec<PathBuf>, bool) {
    let base = root.join(folder);
    let mut all: Vec<PathBuf> = Vec::new();
    fn rec(d: &Path, out: &mut Vec<PathBuf>, common: Option<&Path>) {
        let Ok(rd) = std::fs::read_dir(d) else { return };
        for e in rd.flatten() {
            let p = e.path();
            let is_link = e.file_type().map(|t| t.is_symlink()).unwrap_or(false);
            if !is_link && p.is_dir() && other_checkout(&p, common) {
                continue;
            }
            out.push(p.clone());
            if !is_link && p.is_dir() {
                rec(&p, out, common);
            }
        }
    }
    rec(&base, &mut all, crate::ledger::common_dir_of(root).as_deref());
    all.sort_by_cached_key(|p| parts_key(p));
    let mut out = Vec::new();
    for p in all {
        let rel = p.strip_prefix(&base).unwrap_or(&p).to_path_buf();
        let parts: Vec<String> = rel.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect();
        let parents = &parts[..parts.len().saturating_sub(1)];
        let name = name_of(&p);
        if parents.iter().any(|x| SKIP.contains(&x.as_str()) || x.starts_with('.')) || (name.starts_with('.') && name != ".gitignore") {
            continue;
        }
        if p.is_file() {
            out.push(p);
            if out.len() >= MAX_FOLDER_FILES {
                return (out, true);
            }
        }
    }
    (out, false)
}

pub(crate) fn json_file(p: &Path) -> Option<Value> {
    let md = std::fs::metadata(p).ok()?;
    if md.len() > MAX_DOC_BYTES {
        return None;
    }
    let bytes = std::fs::read(p).ok()?;
    let text = String::from_utf8(bytes).ok()?;
    pyjson::loads(&text).ok()
}

/// A manifest's source hashes: the first mapping in it of paths to sha256 digests.
pub fn manifest_sources(data: Option<&Value>) -> Option<IndexMap<String, String>> {
    let Some(Value::Dict(d)) = data else { return None };
    for key in ["sources", "source_hashes", "files", "hashes", "inputs", "code"] {
        if let Some(Value::Dict(m)) = d.get(key) {
            if !m.is_empty() && m.values().all(|v| matches!(v, Value::Str(s) if hex64(s))) {
                return Some(m.iter().map(|(k, v)| (k.clone(), v.as_str().unwrap().to_lowercase())).collect());
            }
        }
    }
    None
}

pub fn manifest_frozen(data: Option<&Value>) -> Option<String> {
    let Some(Value::Dict(d)) = data else { return None };
    for (key, value) in d {
        if let Value::Str(v) = value {
            let k = key.to_lowercase();
            if ["frozen", "created", "timestamp", "written_at"].iter().any(|w| k.contains(w)) {
                return Some(v.clone());
            }
        }
    }
    None
}

fn role_of(p: &Path) -> &'static str {
    let fname = name_of(p);
    let name = fname.to_lowercase();
    let ext = suffix(&fname);
    let size = file_size(p);
    if DATA.contains(&ext.as_str()) || size > MAX_HASH {
        return "data";
    }
    if REPORT_NAMES.contains(&name.as_str()) || matches!(ext.as_str(), ".md" | ".markdown" | ".rst") {
        return "report";
    }
    if ext == ".py" {
        return if check_name(&fname) { "check" } else { "code" };
    }
    if STRUCTURED.contains(&ext.as_str()) {
        if ext == ".json" && manifest_sources(json_file(p).as_ref()).is_some() {
            return "manifest";
        }
        if audit_name(stem(&fname)) {
            return "audit";
        }
        return "results";
    }
    "other"
}

/// What an audit file says about itself: (passed, the key and value that say so), from a top-level field.
pub fn audit_verdict(p: &Path) -> Option<(bool, String)> {
    let data = json_file(p)?;
    let Value::Dict(d) = &data else { return None };
    for key in PASS_KEYS {
        if let Some(Value::Bool(b)) = d.get(*key) {
            return Some((*b, format!("{key}: {}", if *b { "true" } else { "false" })));
        }
    }
    for key in ["status", "result", "verdict", "outcome"] {
        if let Some(Value::Str(v)) = d.get(key) {
            let w = strip(v).to_lowercase();
            if let Some((_, ok)) = PASS_WORDS.iter().find(|(x, _)| *x == w) {
                return Some((*ok, format!("{key}: {v}")));
            }
        }
    }
    None
}

// ---------------------------------------------------------------- what reads a repository: the ledger, or nothing yet

/// Enough of a record for the compiler: a Ledger, or `Unrecorded` for a repository nobody has started a record in.
pub trait Records {
    fn root(&self) -> &Path;
    fn live_experiments(&self) -> Res<Vec<Experiment>>;
    fn all_experiments(&self) -> Res<Vec<Experiment>>;
    fn kind_of_id(&self, id: &str) -> Res<Option<&'static str>>;
    fn hash_file(&self, p: &Path) -> Option<String>;
    /// Every file a recorded run of a live experiment named (--from, --artifact, --config, the SDK's config), with the
    /// experiment: where a results folder is read by that experiment's runs.
    fn run_files(&self) -> Res<Vec<RunFile>> {
        Ok(Vec::new())
    }
    /// Whether the file at `p` is the one `sha` was taken of: the same bytes, or the same text as another system's git
    /// checks it out (sources::is_file_of).
    fn file_is(&self, p: &Path, sha: &str) -> bool {
        match self.hash_file(p) {
            Some(h) => h == sha || sources::file_is_twin_of(p, sha),
            None => false,
        }
    }
}

impl Records for Ledger {
    fn root(&self) -> &Path {
        &self.root
    }
    fn live_experiments(&self) -> Res<Vec<Experiment>> {
        self.live::<Experiment>()
    }
    fn all_experiments(&self) -> Res<Vec<Experiment>> {
        Ok(self.all::<Experiment>()?.as_ref().clone())
    }
    fn kind_of_id(&self, id: &str) -> Res<Option<&'static str>> {
        self.kind_of(id)
    }
    fn hash_file(&self, p: &Path) -> Option<String> {
        self.hash(p)
    }
    fn run_files(&self) -> Res<Vec<RunFile>> {
        let live: HashSet<String> = self.live::<Experiment>()?.into_iter().map(|e| e.id).collect();
        let mut out = Vec::new();
        for r in self.all::<Run>()?.iter().filter(|r| live.contains(&r.experiment)) {
            let files = r.files.iter().map(|f| (f.path.as_str(), Some(f.sha256.clone()), Some(f.bytes)));
            let config = r.receipt.config.as_ref().map(|c| (c.path.as_str(), c.sha256.clone(), None));
            for (path, sha256, bytes) in files.chain(config).filter(|(p, ..)| !p.is_empty()) {
                let path = path.replace('\\', "/").trim_start_matches("./").to_string();
                out.push(RunFile { path, experiment: r.experiment.clone(), sha256: sha256.filter(|s| !s.is_empty()), bytes });
            }
        }
        Ok(out)
    }
}

/// A file a recorded run named, with its experiment and, where the run kept them, its digest and size.
#[derive(Debug, Clone)]
pub struct RunFile {
    pub path: String,
    pub experiment: String,
    pub sha256: Option<String>,
    pub bytes: Option<i64>,
}

/// The experiments whose recorded runs named a file in `folder`, in the order they first did.
pub fn readers_of(folder: &str, run_files: &[RunFile]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for f in run_files {
        if f.path.starts_with(folder) && !out.contains(&f.experiment) {
            out.push(f.experiment.clone());
        }
    }
    out
}

/// A file a run named reads as the same file on a system whose git checks text out with the other line ending: it
/// keeps the digest and size the run recorded, as an indexed folder's files do (keep_indexed).
fn keep_read(root: &Path, named: &[RunFile], fp: &mut FolderPlan) {
    for f in fp.files.iter_mut() {
        let Some(now) = f.sha256.clone() else { continue };
        let Some(run) = named.iter().find(|r| r.path == f.path && r.sha256.as_deref().map_or(false, |was| was != now)) else { continue };
        let was = run.sha256.clone().unwrap_or_default();
        let full = root.join(&f.path);
        if sources::file_is_twin_of(&full, &was) {
            // a config's receipt keeps no size: the twin's is the one the run read
            let twin = std::fs::read(&full).ok().and_then(|d| sources::other_line_ending(&d)).map(|t| t.len() as i64);
            f.sha256 = Some(was);
            f.bytes = run.bytes.or(twin).unwrap_or(f.bytes);
        }
    }
}

/// Whether a results folder says what it is: a report of its own (README.md, report.md, results.md, summary.md). Only
/// such a folder becomes an experiment of its own; one of results files alone is a run's output.
pub fn holds_report(root: &Path, folder: &str) -> bool {
    let Ok(rd) = std::fs::read_dir(root.join(folder)) else { return false };
    rd.flatten().map(|e| e.path()).any(|p| p.is_file() && REPORT_NAMES.contains(&name_of(&p).to_lowercase().as_str()))
}

/// A repository nobody has started a record in: it holds nothing, so every results folder is new, and it writes nothing.
pub struct Unrecorded {
    pub root: PathBuf,
}

impl Records for Unrecorded {
    fn root(&self) -> &Path {
        &self.root
    }
    fn live_experiments(&self) -> Res<Vec<Experiment>> {
        Ok(Vec::new())
    }
    fn all_experiments(&self) -> Res<Vec<Experiment>> {
        Ok(Vec::new())
    }
    fn kind_of_id(&self, _id: &str) -> Res<Option<&'static str>> {
        Ok(None)
    }
    fn hash_file(&self, p: &Path) -> Option<String> {
        if p.is_file() {
            sources::sha256_file(p, None).ok()
        } else {
            None
        }
    }
}

// ---------------------------------------------------------------- one folder, compiled

#[derive(Debug, Clone)]
pub struct FolderPlan {
    pub folder: String,
    pub id: String,
    pub experiment: Option<String>,
    pub title: String,
    pub title_from: String,
    pub files: Vec<RepoFile>,
    pub quotes: Vec<Quote>,
    pub checks: Vec<Check>,
    pub truncated: bool,
    pub changes: Vec<String>,
    /// the experiments whose recorded runs named a file in it
    pub read_by: Vec<String>,
    /// whether it holds a report of its own: only then is it an experiment of its own
    pub report: bool,
}

impl FolderPlan {
    /// Whether rb reconcile --apply adds it as an experiment: a folder with a report that no experiment reads. One of
    /// results files alone, which no recorded run named, is listed as unplaced and never made an experiment.
    pub fn adds(&self) -> bool {
        self.experiment.is_none() && self.read_by.is_empty() && self.report
    }
    /// Whether it is listed as unplaced: results no experiment reads, in a folder with no report.
    pub fn unplaced(&self) -> bool {
        self.experiment.is_none() && self.read_by.is_empty() && !self.report
    }
}

pub type Docs = IndexMap<String, Option<Doc>>;

fn doc_of<'a>(docs: &'a mut Docs, root: &Path, rel: &str) -> Option<&'a Doc> {
    if !docs.contains_key(rel) {
        let d = read_doc(root, rel);
        docs.insert(rel.to_string(), d);
    }
    docs.get(rel).and_then(|d| d.as_ref())
}

fn repo_file(path: &str, role: &str, sha256: Option<String>, bytes: u64, files: i64, git: &str) -> RepoFile {
    RepoFile { path: path.into(), role: role.into(), sha256, bytes: bytes as i64, files, git: git.into() }
}

pub fn compile_folder(led: &dyn Records, folder: &str, gv: &GitView, docs: &mut Docs, all_folders: &[String]) -> FolderPlan {
    let root = led.root().to_path_buf();
    let (paths, truncated) = walk(&root, folder);
    let mut listed: Vec<RepoFile> = Vec::new();
    let mut counted: IndexMap<String, (i64, u64, BTreeSet<String>)> = IndexMap::new();
    let mut reports: Vec<String> = Vec::new();
    for p in &paths {
        let rel = rel_posix(p, &root);
        let role = role_of(p);
        let size = file_size(p);
        let held = gv.held(&rel);
        if role == "report" {
            reports.push(rel.clone());
        }
        if role == "data" || listed.len() >= MAX_LISTED {
            let c = counted.entry(role.to_string()).or_insert((0, 0, BTreeSet::new()));
            c.0 += 1;
            c.1 += size;
            c.2.insert(held.to_string());
            continue;
        }
        listed.push(repo_file(&rel, role, if size <= MAX_HASH { led.hash_file(p) } else { None }, size, 1, held));
    }
    let mut cs: Vec<(&String, &(i64, u64, BTreeSet<String>))> = counted.iter().collect();
    cs.sort_by(|a, b| a.0.cmp(b.0));
    for (role, c) in cs {
        let held = if c.2.len() == 1 { c.2.iter().next().unwrap().clone() } else { "mixed".to_string() };
        listed.push(repo_file(folder, role, None, c.1, c.0, &held));
    }
    reports.sort_by_key(|r| {
        let n = Path::new(r).file_name().map(|f| f.to_string_lossy().to_lowercase()).unwrap_or_default();
        (r.matches('/').count(), if n == "readme.md" || n == "readme.markdown" { 0 } else { 1 }, r.clone())
    });
    let mut quotes: Vec<Quote> = Vec::new();
    let mut title = folder.trim_end_matches('/').rsplit('/').next().unwrap_or("").to_string();
    let mut title_from = String::new();
    let mut linked: IndexMap<String, usize> = IndexMap::new();
    let mut code_refs: Vec<(String, String, usize)> = Vec::new();
    for (i, rel) in reports.iter().take(4).enumerate() {
        let Some(doc) = doc_of(docs, &root, rel).cloned() else { continue };
        let t = doc.title().cloned();
        if i == 0 {
            if let Some(t) = &t {
                title = t.text.chars().take(200).collect();
                title_from = format!("{rel}:{}", t.start);
                if let Some(q) = quote("title", &doc, t, "") {
                    quotes.push(q);
                }
            }
        }
        if i == 0 {
            if let Some(s) = doc.summary() {
                if let Some(q) = quote("summary", &doc, s, t.as_ref().map(|t| t.text.as_str()).unwrap_or("")) {
                    quotes.push(q);
                }
            }
        }
        for (kind, head, body) in doc.sections() {
            if let Some(q) = quote(kind, &doc, body, &head.text) {
                quotes.push(q);
            }
        }
        for (target, line) in &doc.links {
            linked.entry(target.clone()).or_insert(*line);
        }
        for (script, line) in &doc.code_refs {
            code_refs.push((script.clone(), rel.clone(), *line));
        }
    }
    for target in linked.keys() {
        if target.starts_with(folder) || listed.iter().any(|f| f.path == *target) {
            continue;
        }
        let p = root.join(target);
        if p.is_dir() {
            continue;
        }
        let mut role = role_of(&p);
        if role == "report" {
            let doc = doc_of(docs, &root, target).cloned();
            role = if is_guide(target, doc.as_ref()) { "reproduction" } else { "protocol" };
        }
        let size = std::fs::metadata(&p).map(|m| m.len()).unwrap_or(0);
        listed.push(repo_file(target, role, led.hash_file(&p), size, 1, gv.held(target)));
    }
    let here = folder.trim_end_matches('/').to_string();
    let others: Vec<String> = all_folders.iter().filter(|x| x.as_str() != folder).map(|x| x.trim_end_matches('/').to_string()).collect();
    let about_another = |text: &str| !text.contains(&here) && others.iter().any(|o| text.contains(o.as_str()));
    let guides: Vec<String> = listed.iter().filter(|f| f.role == "reproduction").map(|f| f.path.clone()).collect();
    for gpath in &guides {
        let Some(doc) = doc_of(docs, &root, gpath).cloned() else { continue };
        for b in doc.commands() {
            if about_another(&b.text) {
                continue;
            }
            if let Some(q) = quote("command", &doc, b, &b.heading) {
                quotes.push(q);
            }
        }
        for (script, line) in &doc.code_refs {
            code_refs.push((script.clone(), gpath.clone(), *line));
        }
    }
    let script_name = |s: &str| Path::new(s).file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default();
    let own: BTreeSet<String> = code_refs.iter().filter(|(s, d, _)| reports.contains(d) && check_name(&script_name(s))).map(|(s, _, _)| s.clone()).collect();
    let mut candidates: IndexMap<String, ()> = IndexMap::new();
    for s in &own {
        candidates.insert(s.clone(), ());
    }
    for (s, _, _) in &code_refs {
        if check_name(&script_name(s)) {
            candidates.entry(s.clone()).or_insert(());
        }
    }
    for f in &listed {
        if f.role == "manifest" {
            if let Some(srcs) = manifest_sources(json_file(&root.join(&f.path)).as_ref()) {
                for src in srcs.keys() {
                    if check_name(&script_name(src)) && root.join(src).is_file() {
                        candidates.entry(src.clone()).or_insert(());
                    }
                }
            }
        }
    }
    let where_: Vec<String> = listed.iter().filter(|f| f.role == "reproduction" || f.role == "report").map(|f| f.path.clone()).collect();
    let mut checks = Vec::new();
    for script in candidates.keys() {
        let (command, said_at) = check_command(&root, script, docs, &where_, folder);
        if own.contains(script) || (!command.is_empty() && command.contains(&here)) {
            checks.push(Check { path: script.clone(), command: command.chars().take(TEXT).collect(), said_at });
        }
    }
    let report = holds_report(&root, folder);
    FolderPlan { folder: folder.into(), id: folder_id(folder), experiment: None, title, title_from, files: listed, quotes, checks, truncated, changes: Vec::new(), read_by: Vec::new(), report }
}

fn is_guide(rel: &str, doc: Option<&Doc>) -> bool {
    if Path::new(rel).file_name().map(|f| f.to_string_lossy().to_lowercase()).unwrap_or_default().contains("reproduc") {
        return true;
    }
    let Some(doc) = doc else { return false };
    doc.blocks.iter().any(|b| b.kind == "heading" && b.level <= 2 && REPRODUCTION_WORDS.iter().any(|w| b.text.to_lowercase().contains(w)))
}

/// How the repository says to run a check: the first command in its reports and guides that names the script.
pub fn check_command(root: &Path, script: &str, docs: &mut Docs, where_: &[String], folder: &str) -> (String, String) {
    let module = match script.strip_suffix(".py") {
        Some(s) => s.replace('/', "."),
        None => script.to_string(),
    };
    let mre = FRegex::new(&format!(r"(?<![\w.]){}(?![\w])", fancy_regex::escape(&module))).unwrap();
    let mut hits: Vec<(i32, String, String)> = Vec::new();
    let folder_t = folder.trim_end_matches('/');
    for rel in where_ {
        let Some(doc) = doc_of(docs, root, rel).cloned() else { continue };
        for b in doc.commands() {
            for (line, cmd) in logical_lines(b) {
                if cmd.contains(script) || mre.is_match(&cmd).unwrap_or(false) {
                    hits.push((if !folder.is_empty() && cmd.contains(folder_t) { 0 } else { 1 }, cmd.clone(), format!("{rel}:{line}")));
                }
            }
        }
    }
    if hits.is_empty() {
        return (String::new(), String::new());
    }
    hits.sort_by_key(|h| h.0);
    (hits[0].1.clone(), hits[0].2.clone())
}

// ---------------------------------------------------------------- the whole repository, against the record

#[derive(Debug, Clone)]
pub struct Plan {
    pub commit: Option<String>,
    pub branch: Option<String>,
    pub folders: Vec<FolderPlan>,
    pub project_checks: Vec<Check>,
    pub guides: Vec<String>,
}

/// A file indexed on one system reads as the same file on another whose git checks text out with the other line
/// ending (sources::is_file_of): it keeps the digest and the size it was indexed with, so an index made on Linux does
/// not read as changed on Windows, nor flip each time someone on the other system applies it.
fn keep_indexed(root: &Path, known: &Experiment, fp: &mut FolderPlan) {
    for f in fp.files.iter_mut() {
        let Some(old) = known.files.iter().find(|o| o.path == f.path && o.role == f.role) else { continue };
        if let (Some(was), Some(now)) = (&old.sha256, &f.sha256) {
            if was != now && sources::file_is_twin_of(&root.join(&f.path), was) {
                f.sha256 = Some(was.clone());
                f.bytes = old.bytes;
            }
        }
    }
}

pub fn compile_repo(led: &dyn Records) -> Res<Plan> {
    let root = led.root().to_path_buf();
    let gv = git_view(&root);
    let mut docs: Docs = IndexMap::new();
    let live = led.live_experiments()?;
    let mut by_folder: IndexMap<String, Experiment> = IndexMap::new();
    for e in &live {
        if !e.folder.is_empty() {
            by_folder.insert(e.folder.clone(), e.clone());
        }
    }
    let ids: IndexMap<String, Experiment> = live.iter().map(|e| (e.id.clone(), e.clone())).collect();
    let mut plans = Vec::new();
    let folders = result_folders(&root);
    let named = led.run_files()?;
    for folder in &folders {
        let mut fp = compile_folder(led, folder, &gv, &mut docs, &folders);
        fp.read_by = readers_of(folder, &named);
        let mut known = by_folder.get(folder).cloned();
        if known.is_none() {
            if let Some(e) = ids.get(&fp.id) {
                if e.folder.is_empty() {
                    known = Some(e.clone());
                }
            }
        }
        match known {
            Some(k) => {
                keep_indexed(&root, &k, &mut fp);
                fp.experiment = Some(k.id.clone());
                fp.id = k.id.clone();
                fp.changes = diff_index(&k, &fp);
            }
            // read by the runs that named its files, or results with no report: nothing for --apply to add
            None if !fp.adds() => keep_read(&root, &named, &mut fp),
            None => {
                if led.kind_of_id(&fp.id)?.is_some() {
                    let mut n = 2;
                    while led.kind_of_id(&format!("{}-{n}", fp.id))?.is_some() {
                        n += 1;
                    }
                    fp.id = format!("{}-{n}", fp.id);
                }
                fp.changes = vec!["new: not in the record".into()];
            }
        }
        plans.push(fp);
    }
    let guides: Vec<String> = plans.iter().flat_map(|fp| fp.files.iter().filter(|f| f.role == "reproduction").map(|f| f.path.clone())).collect::<BTreeSet<_>>().into_iter().collect();
    let named: HashSet<String> = plans.iter().flat_map(|fp| fp.checks.iter().map(|c| c.path.clone())).collect();
    let mut project: IndexMap<String, Check> = IndexMap::new();
    for g in &guides {
        let Some(doc) = doc_of(&mut docs, &root, g).cloned() else { continue };
        for (script, _line) in &doc.code_refs {
            let sn = Path::new(script).file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default();
            if check_name(&sn) && !named.contains(script) && !project.contains_key(script) {
                let (command, said_at) = check_command(&root, script, &mut docs, &[g.clone()], "");
                project.insert(script.clone(), Check { path: script.clone(), command: command.chars().take(TEXT).collect(), said_at });
            }
        }
    }
    Ok(Plan { commit: gv.commit, branch: gv.branch, folders: plans, project_checks: project.into_values().collect(), guides })
}

/// What --apply would change in an experiment's index, in words.
pub fn diff_index(exp: &Experiment, fp: &FolderPlan) -> Vec<String> {
    let mut out = Vec::new();
    let old_files: IndexMap<(String, String), &RepoFile> = exp.files.iter().map(|f| ((f.path.clone(), f.role.clone()), f)).collect();
    let new_files: IndexMap<(String, String), &RepoFile> = fp.files.iter().map(|f| ((f.path.clone(), f.role.clone()), f)).collect();
    // each file by its name in the folder, so a change says which file it is
    let named = |keys: Vec<&(String, String)>| -> Vec<String> {
        let mut names: Vec<String> = keys.iter().map(|(p, _)| p.strip_prefix(fp.folder.as_str()).or_else(|| p.strip_prefix(exp.folder.as_str())).unwrap_or(p).to_string()).collect();
        names.dedup();
        names
    };
    let added = named(new_files.keys().filter(|k| !old_files.contains_key(*k)).collect());
    let gone = named(old_files.keys().filter(|k| !new_files.contains_key(*k)).collect());
    let changed = named(
        new_files.iter().filter(|(k, n)| old_files.get(*k).map_or(false, |o| (&n.sha256, n.bytes, n.files) != (&o.sha256, o.bytes, o.files))).map(|(k, _)| k).collect(),
    );
    if !added.is_empty() || !gone.is_empty() || !changed.is_empty() {
        let said = |names: &[String], word: &str| -> String {
            if names.is_empty() {
                return String::new();
            }
            let first: Vec<&str> = names.iter().take(3).map(String::as_str).collect();
            format!("{} {word} ({}{})", names.len(), first.join(", "), if names.len() > 3 { ", ..." } else { "" })
        };
        let parts: Vec<String> = [said(&added, "new"), said(&changed, "changed"), said(&gone, "gone")].into_iter().filter(|x| !x.is_empty()).collect();
        out.push(format!("files: {}", parts.join(", ")));
    }
    let old_q: HashSet<(String, String)> = exp.quotes.iter().map(|q| (q.kind.clone(), q.text.clone())).collect();
    let new_q: HashSet<(String, String)> = fp.quotes.iter().map(|q| (q.kind.clone(), q.text.clone())).collect();
    for kind in ["title", "summary", "result", "limitation", "next", "command"] {
        let nk: HashSet<&String> = new_q.iter().filter(|(k, _)| k == kind).map(|(_, t)| t).collect();
        let ok: HashSet<&String> = old_q.iter().filter(|(k, _)| k == kind).map(|(_, t)| t).collect();
        let a = nk.difference(&ok).count();
        let g = ok.difference(&nk).count();
        if a > 0 && g > 0 {
            out.push(format!("{kind} {}", if a == 1 && g == 1 { "changed".to_string() } else { format!("{a} new, {g} gone") }));
        } else if a > 0 {
            out.push(format!("{kind}: {a} new"));
        } else if g > 0 {
            out.push(format!("{kind}: {g} gone"));
        }
    }
    let old_c: HashSet<&String> = exp.checks.iter().map(|c| &c.path).collect();
    let new_c: HashSet<&String> = fp.checks.iter().map(|c| &c.path).collect();
    let n = new_c.difference(&old_c).count();
    if n > 0 {
        out.push(format!("checks: {n} new"));
    }
    let g = old_c.difference(&new_c).count();
    if g > 0 {
        out.push(format!("checks: {g} gone"));
    }
    if exp.folder != fp.folder {
        out.push(format!("folder: {}", fp.folder));
    }
    out
}

/// Where a record for this repository starts: git's top level when inside one, else the directory itself.
pub fn repository_root(start: &Path) -> PathBuf {
    match git(start, &["rev-parse", "--show-toplevel"]) {
        Some(t) if !strip(&t).is_empty() => PathBuf::from(strip(&t)),
        _ => start.to_path_buf(),
    }
}

/// What the repository calls itself: its README's title, verbatim, with file:line; else the directory's name.
pub fn repository_title(root: &Path) -> (String, String) {
    for name in ["README.md", "readme.md", "README.markdown"] {
        if root.join(name).is_file() {
            if let Some(doc) = read_doc(root, name) {
                if let Some(t) = doc.title() {
                    if !strip(&t.text).is_empty() {
                        return (strip(&t.text).chars().take(200).collect(), format!("{name}:{}", t.start));
                    }
                }
            }
        }
    }
    let n = name_of(root);
    (if n.is_empty() { "project".into() } else { n }, String::new())
}

// ---------------------------------------------------------------- read against the repository as it stands now

/// A check's last outcome, never stored: the last run rb watched whose command names the script.
pub fn check_outcome(led: &Ledger, check: &Check) -> Res<Dict> {
    check_outcome_at(&led.root, check)
}

pub fn check_outcome_at(root: &Path, check: &Check) -> Res<Dict> {
    let module = match check.path.strip_suffix(".py") {
        Some(s) => s.replace('/', "."),
        None => check.path.clone(),
    };
    let mre = FRegex::new(&format!(r"(?<![\w.]){}(?![\w])", fancy_regex::escape(&module))).unwrap();
    let mut last: Option<Dict> = None;
    for e in crate::journal::entries(root, None) {
        if e.get_str("op") != Some("run") {
            continue;
        }
        let cmd = crate::sys::typed(&e.get("command").map(pyjson::py_str).unwrap_or_default());
        if cmd.contains(&check.path) || mre.is_match(&cmd).unwrap_or(false) {
            last = Some(e);
        }
    }
    let Some(last) = last else {
        return Ok(crate::dict! {"state" => "not run under rb's watch", "ok" => Value::Null});
    };
    let ok = last.get("exit").map_or(false, |v| *v == Value::Int(0));
    let at: String = last.get("at").filter(|v| !v.is_null()).map(pyjson::py_str).unwrap_or_default().chars().take(16).collect::<String>().replace('T', " ");
    let state = format!("{} at {at}", if ok { "passed".to_string() } else { format!("failed (exit {})", last.get("exit").map(pyjson::py_str).unwrap_or_else(|| "None".into())) });
    let tail: Vec<Value> = match last.get("tail") {
        Some(Value::List(l)) => l.clone(),
        _ => Vec::new(),
    };
    Ok(crate::dict! {
        "state" => state, "ok" => ok, "command" => last.get("command").cloned().unwrap_or(Value::Str(String::new())),
        "at" => last.get("at").cloned().unwrap_or(Value::Null), "tail" => Value::List(tail)
    })
}

/// What read_folder reads of an experiment: its folder, files, quotes and checks (an experiment, or a folder's plan).
pub struct FolderView<'a> {
    pub folder: &'a str,
    pub files: &'a [RepoFile],
    pub quotes: &'a [Quote],
    pub checks: &'a [Check],
}

impl<'a> FolderView<'a> {
    pub fn of(exp: &'a Experiment) -> FolderView<'a> {
        FolderView { folder: &exp.folder, files: &exp.files, quotes: &exp.quotes, checks: &exp.checks }
    }
    pub fn of_plan(fp: &'a FolderPlan) -> FolderView<'a> {
        FolderView { folder: &fp.folder, files: &fp.files, quotes: &fp.quotes, checks: &fp.checks }
    }
}

/// An indexed experiment against the repository as it stands.
pub fn read_folder(led: &Ledger, exp: &Experiment, gv: Option<&GitView>) -> Res<Dict> {
    read_folder_of(led, &FolderView::of(exp), gv)
}

pub fn read_folder_of(led: &dyn Records, exp: &FolderView, gv: Option<&GitView>) -> Res<Dict> {
    let root = led.root().to_path_buf();
    let owned;
    let gv = match gv {
        Some(g) => g,
        None => {
            owned = git_view(&root);
            &owned
        }
    };
    let mut files: Vec<Dict> = Vec::new();
    for f in exp.files {
        let p = root.join(&f.path);
        let state = match &f.sha256 {
            None => {
                if p.exists() {
                    "present"
                } else {
                    "missing"
                }
            }
            Some(s) => {
                if !p.is_file() {
                    "missing"
                } else if led.file_is(&p, s) {
                    "present"
                } else {
                    "changed"
                }
            }
        };
        files.push(crate::dict! {"path" => f.path.clone(), "role" => f.role.clone(), "state" => state, "files" => f.files, "bytes" => f.bytes, "git" => f.git.clone()});
    }
    let mut manifests: Vec<Value> = Vec::new();
    for f in exp.files {
        if f.role != "manifest" || !root.join(&f.path).is_file() {
            continue;
        }
        let data = json_file(&root.join(&f.path));
        let srcs = manifest_sources(data.as_ref()).unwrap_or_default();
        let base = root.join(&f.path).parent().map(|p| p.to_path_buf()).unwrap_or_else(|| root.clone());
        let (mut matched, mut changed, mut missing) = (0usize, Vec::new(), Vec::new());
        for (src, sha) in &srcs {
            let cand = if root.join(src).is_file() { root.join(src) } else { base.join(src) };
            if !cand.is_file() {
                missing.push(src.clone());
            } else if led.file_is(&cand, sha) {
                matched += 1;
            } else {
                changed.push(src.clone());
            }
        }
        manifests.push(crate::obj! {
            "path" => f.path.clone(), "frozen" => manifest_frozen(data.as_ref()), "sources" => srcs.len(), "matched" => matched,
            "changed" => Value::list_of_str(changed), "missing" => Value::list_of_str(missing)
        });
    }
    let mut audits: Vec<Value> = Vec::new();
    for f in exp.files {
        if f.role == "audit" && root.join(&f.path).is_file() {
            if let Some((passed, says)) = audit_verdict(&root.join(&f.path)) {
                audits.push(crate::obj! {"path" => f.path.clone(), "passed" => passed, "says" => says});
            }
        }
    }
    let mut checks: Vec<Value> = Vec::new();
    for c in exp.checks {
        let mut d = crate::dict! {"path" => c.path.clone(), "command" => c.command.clone(), "said_at" => c.said_at.clone()};
        for (k, v) in check_outcome_at(&root, c)? {
            d.insert(k, v);
        }
        checks.push(Value::Dict(d));
    }
    let mut stale: Vec<Value> = Vec::new();
    for q in exp.quotes {
        let p = root.join(&q.path);
        match std::fs::read(&p) {
            Err(_) => stale.push(crate::obj! {"kind" => q.kind.clone(), "path" => q.path.clone(), "lines" => q.lines.clone(), "why" => "the file is gone"}),
            Ok(bytes) => {
                // lines read as lines, however the file ends them: a quote was taken that way too
                let text = String::from_utf8_lossy(&bytes).replace("\r\n", "\n").replace('\r', "\n");
                let head: String = q.text.chars().take(200).collect();
                if !text.contains(&head) {
                    stale.push(crate::obj! {"kind" => q.kind.clone(), "path" => q.path.clone(), "lines" => q.lines.clone(), "why" => "the file no longer says it"});
                }
            }
        }
    }
    let guides: Vec<String> = files.iter().filter(|f| f.get_str("role") == Some("reproduction") && f.get_str("state") != Some("missing")).map(|f| f.get_str("path").unwrap_or("").to_string()).collect();
    let commands = exp.quotes.iter().filter(|q| q.kind == "command").count();
    let kept_local: i64 = files.iter().filter(|f| f.get_str("role") == Some("data") && f.get_str("git") == Some("ignored")).map(|f| f.get("files").and_then(|v| v.as_i64()).unwrap_or(0)).sum();
    let in_git: Vec<&Dict> = files
        .iter()
        .filter(|f| matches!(f.get_str("role"), Some("report") | Some("manifest") | Some("results") | Some("audit")) && f.get_str("path").unwrap_or("").starts_with(exp.folder))
        .collect();
    let mut untracked: Vec<&String> = gv.untracked.iter().filter(|x| !exp.folder.is_empty() && x.starts_with(exp.folder)).collect();
    untracked.sort();
    let mut modified: Vec<&String> = gv.modified.iter().filter(|x| !exp.folder.is_empty() && x.starts_with(exp.folder)).collect();
    modified.sort();
    let ready = gv.present && untracked.is_empty() && modified.is_empty();
    Ok(crate::dict! {
        "folder" => exp.folder.to_string(), "files" => Value::List(files.iter().cloned().map(Value::Dict).collect()), "manifests" => Value::List(manifests),
        "audits" => Value::List(audits), "checks" => Value::List(checks), "stale_quotes" => Value::List(stale),
        "reproduction" => crate::obj! {"guides" => Value::list_of_str(guides), "commands" => commands,
            "tracked" => in_git.iter().filter(|f| f.get_str("git") == Some("tracked")).count(), "of" => in_git.len(), "kept_local" => kept_local},
        "git" => crate::obj! {"present" => gv.present, "commit" => gv.commit.clone(), "untracked" => Value::list_of_str(untracked.into_iter().cloned()),
            "modified" => Value::list_of_str(modified.into_iter().cloned()), "ready" => ready}
    })
}

pub fn repo_file_value(f: &RepoFile, exclude_none: bool) -> Value {
    Value::Dict(f.dump(exclude_none))
}

pub fn plan_as_dict(plan: &Plan) -> Dict {
    crate::dict! {
        "commit" => plan.commit.clone(), "branch" => plan.branch.clone(), "guides" => Value::list_of_str(plan.guides.clone()),
        "project_checks" => Value::List(plan.project_checks.iter().map(|c| Value::Dict(c.dump(false))).collect()),
        "folders" => Value::List(plan.folders.iter().map(|fp| crate::obj! {
            "folder" => fp.folder.clone(), "id" => fp.id.clone(), "experiment" => fp.experiment.clone(), "title" => fp.title.clone(),
            "title_from" => fp.title_from.clone(), "files" => Value::List(fp.files.iter().map(|f| repo_file_value(f, true)).collect()),
            "quotes" => Value::List(fp.quotes.iter().map(|q| Value::Dict(q.dump(false))).collect()),
            "checks" => Value::List(fp.checks.iter().map(|c| Value::Dict(c.dump(false))).collect()),
            "changes" => Value::list_of_str(fp.changes.clone()), "truncated" => fp.truncated,
            "read_by" => Value::list_of_str(fp.read_by.clone()), "report" => fp.report, "unplaced" => fp.unplaced()
        }).collect())
    }
}

// ---------------------------------------------------------------- keeping it: rb reconcile --apply, and the research diff

/// The changed files one rb reconcile --apply records by path, at most: a line of the record stays small.
pub const MAX_RECORDED: usize = 1000;

/// Keep in the record what the plan reads, and the paths of `seen`, the changed files nothing in the record cites, on the
/// log line it writes: they are then recorded, by path.
pub fn apply(led: &Ledger, plan: &Plan, seen: &[crate::journal::Uncited]) -> Res<Dict> {
    let mut added: Vec<String> = Vec::new();
    let mut updated: IndexMap<String, Vec<String>> = IndexMap::new();
    for fp in &plan.folders {
        match &fp.experiment {
            None if !fp.adds() => {}
            None => {
                let exp = led.index_folder(None, &fp.id, &fp.folder, &fp.title, fp.files.clone(), fp.quotes.clone(), fp.checks.clone(), plan.commit.as_deref())?;
                added.push(exp.id);
            }
            Some(e) if !fp.changes.is_empty() => {
                led.index_folder(Some(e), &fp.id, &fp.folder, &fp.title, fp.files.clone(), fp.quotes.clone(), fp.checks.clone(), plan.commit.as_deref())?;
                updated.insert(e.clone(), fp.changes.clone());
            }
            _ => {}
        }
    }
    let proj_checks: Vec<Value> = led.project()?.checks.iter().map(|c| Value::Dict(c.dump(false))).collect();
    let plan_checks: Vec<Value> = plan.project_checks.iter().map(|c| Value::Dict(c.dump(false))).collect();
    let checks_moved = plan_checks != proj_checks;
    if checks_moved {
        led.index_project_checks(plan.project_checks.clone(), plan.commit.as_deref())?;
    }
    let mut diff = research_diff(led, plan, &added, &updated)?;
    // the changed files nothing in the record cites, by path and bytes, at most MAX_RECORDED on one line: where they
    // count from moves here only when all of them were recorded
    let kept: Vec<&crate::journal::Uncited> = seen.iter().take(MAX_RECORDED).collect();
    let cut = kept.len() < seen.len();
    if !kept.is_empty() {
        let names: Vec<&str> = kept.iter().take(4).map(|u| u.path.as_str()).collect();
        let shown = names.join(", ") + if kept.len() > 4 { ", ..." } else { "" };
        let part = if cut {
            format!("{} of {} changed files recorded by path ({shown})", kept.len(), seen.len())
        } else {
            format!("{} changed {} recorded by path ({shown})", kept.len(), if kept.len() == 1 { "file" } else { "files" })
        };
        let line = diff.get_str("line").filter(|l| *l != "nothing changed").map(|l| format!("{l} · {part}")).unwrap_or(part);
        diff.insert("line".into(), Value::Str(line));
    }
    diff.insert(
        "uncited_seen".into(),
        crate::obj! {
            "commit" => plan.commit.clone(), "count" => seen.len() as i64, "cut" => cut,
            "files" => Value::List(kept.iter().map(|u| crate::obj! {"path" => u.path.clone(), "sha" => if u.gone { "gone".to_string() } else { u.mark.clone() }}).collect())
        },
    );
    if !added.is_empty() || !updated.is_empty() || checks_moved || !seen.is_empty() {
        led.note_reconcile(diff.clone())?;
    } else {
        diff.shift_remove("uncited_seen");
        diff.insert("line".into(), Value::Str("nothing changed".into()));
    }
    Ok(diff)
}

pub fn research_diff(led: &Ledger, plan: &Plan, added: &[String], updated: &IndexMap<String, Vec<String>>) -> Res<Dict> {
    let new_folders: HashSet<&String> = plan.folders.iter().filter(|fp| fp.adds()).map(|fp| &fp.folder).collect();
    let mut results = plan.folders.iter().filter(|fp| new_folders.contains(&fp.folder)).flat_map(|fp| fp.quotes.iter()).filter(|q| q.kind == "result").count();
    results += updated.values().flat_map(|c| c.iter()).filter(|c| c.starts_with("result")).count();
    let mut nexts = plan.folders.iter().filter(|fp| new_folders.contains(&fp.folder)).flat_map(|fp| fp.quotes.iter()).filter(|q| q.kind == "next").count();
    nexts += updated.values().flat_map(|c| c.iter()).filter(|c| c.starts_with("next")).count();
    let live = led.live::<Experiment>()?;
    let known_guides: HashSet<String> = live
        .iter()
        .filter(|e| !added.contains(&e.id) && !updated.contains_key(&e.id))
        .flat_map(|e| e.files.iter().filter(|f| f.role == "reproduction").map(|f| f.path.clone()))
        .collect();
    let mut guides: Vec<String> = plan.guides.iter().filter(|g| !known_guides.contains(*g)).cloned().collect();
    guides.sort();
    let gv = git_view(&led.root);
    let (mut passed, mut failed, mut not_run) = (0i64, 0i64, 0i64);
    let mut manifests = 0usize;
    let mut moved: HashSet<String> = HashSet::new();
    let mut audits_failed = 0usize;
    for e in &live {
        if e.folder.is_empty() {
            continue;
        }
        let r = read_folder(led, e, Some(&gv))?;
        for c in r.get("checks").and_then(|v| v.as_list()).cloned().unwrap_or_default() {
            match c.get("ok") {
                Some(Value::Bool(true)) => passed += 1,
                Some(Value::Bool(false)) => failed += 1,
                _ => not_run += 1,
            }
        }
        let ms = r.get("manifests").and_then(|v| v.as_list()).cloned().unwrap_or_default();
        manifests += ms.len();
        for m in &ms {
            for k in ["changed", "missing"] {
                for x in m.get(k).and_then(|v| v.as_list()).cloned().unwrap_or_default() {
                    moved.insert(pyjson::py_str(&x));
                }
            }
        }
        audits_failed += r.get("audits").and_then(|v| v.as_list()).map(|l| l.iter().filter(|a| !a.get("passed").map_or(false, |v| v.truthy())).count()).unwrap_or(0);
    }
    let changed_sources = moved.len();
    for c in led.project()?.checks {
        let o = check_outcome(led, &c)?;
        match o.get("ok") {
            Some(Value::Bool(true)) => passed += 1,
            Some(Value::Bool(false)) => failed += 1,
            _ => not_run += 1,
        }
    }
    let plural = |n: usize, w: &str| if n == 1 { w.to_string() } else { format!("{w}s") };
    let mut parts: Vec<String> = Vec::new();
    if !added.is_empty() {
        parts.push(format!(
            "+{} {} ({}{})",
            added.len(),
            plural(added.len(), "experiment"),
            added.iter().take(4).cloned().collect::<Vec<_>>().join(", "),
            if added.len() > 4 { ", ..." } else { "" }
        ));
    }
    if !updated.is_empty() {
        let mut ks: Vec<&String> = updated.keys().collect();
        ks.sort();
        parts.push(format!("{} re-indexed ({}{})", updated.len(), ks.iter().take(4).map(|s| s.as_str()).collect::<Vec<_>>().join(", "), if ks.len() > 4 { ", ..." } else { "" }));
    }
    if results > 0 {
        parts.push(format!("{results} {} quoted", plural(results, "result")));
    }
    if nexts > 0 {
        parts.push(format!("{nexts} next {} quoted", plural(nexts, "step")));
    }
    if !guides.is_empty() {
        parts.push(format!("{} reproduction {}", guides.len(), plural(guides.len(), "guide")));
    }
    if passed != 0 || failed != 0 || not_run != 0 {
        let xs: Vec<String> = [
            if passed != 0 { format!("{passed} passed") } else { String::new() },
            if failed != 0 { format!("{failed} failed") } else { String::new() },
            if not_run != 0 { format!("{not_run} not run under rb's watch") } else { String::new() },
        ]
        .into_iter()
        .filter(|x| !x.is_empty())
        .collect();
        parts.push(format!("checks: {}", xs.join(", ")));
    }
    if audits_failed > 0 {
        parts.push(format!("{audits_failed} audit {} saying failed", if audits_failed == 1 { "file" } else { "files" }));
    }
    if manifests > 0 {
        parts.push(format!(
            "{manifests} {}, {}",
            plural(manifests, "manifest"),
            if changed_sources == 0 { "every source hash matches".to_string() } else { format!("{changed_sources} {} changed since frozen", plural(changed_sources, "source")) }
        ));
    }
    Ok(crate::dict! {
        "commit" => plan.commit.clone(), "added" => Value::list_of_str(added.iter().cloned()),
        "updated" => Value::Dict(updated.iter().map(|(k, v)| (k.clone(), Value::list_of_str(v.iter().cloned()))).collect()),
        "results" => results, "next" => nexts, "guides" => Value::list_of_str(guides),
        "checks" => crate::obj! {"passed" => passed, "failed" => failed, "not_run" => not_run}, "audits_failed" => audits_failed,
        "manifests" => manifests, "changed_sources" => changed_sources,
        "line" => if parts.is_empty() { "nothing changed".to_string() } else { parts.join(" · ") }
    })
}

/// The last rb reconcile --apply: its log line, with who ran it and when.
pub fn last_reconcile(led: &Ledger) -> Res<Option<Dict>> {
    for e in led.log_entries()?.iter().rev() {
        if e.get_str("op") == Some("reconciled") {
            let mut d = crate::dict! {"at" => e.get("at").cloned().unwrap_or(Value::Null), "by" => e.get("actor").cloned().unwrap_or(Value::Null)};
            if let Some(Value::Dict(detail)) = e.get("detail") {
                for (k, v) in detail {
                    d.insert(k.clone(), v.clone());
                }
            }
            return Ok(Some(d));
        }
    }
    Ok(None)
}

/// Results folders in the repository that no experiment reads yet: none is indexed on it, and no recorded run of a live
/// experiment named a file in it.
pub fn unindexed(led: &Ledger) -> Res<Vec<String>> {
    Ok(unread(led)?.into_iter().map(|u| u.folder).collect())
}

/// A results folder no experiment reads yet: whether it holds a report (rb reconcile --apply adds only such a one), and
/// whether its files were last written before the record began (work the record began after).
pub struct Unread {
    pub folder: String,
    pub report: bool,
    pub before: bool,
}

pub fn unread(led: &Ledger) -> Res<Vec<Unread>> {
    let known: HashSet<String> = led.all::<Experiment>()?.iter().filter(|e| !e.folder.is_empty()).map(|e| e.folder.clone()).collect();
    let named = led.run_files()?;
    let began = led.began()?;
    Ok(result_folders(&led.root)
        .into_iter()
        .filter(|f| !known.contains(f) && readers_of(f, &named).is_empty())
        .map(|folder| {
            let newest = last_written(&led.root.join(&folder));
            let before = matches!((&began, &newest), (Some(b), Some(n)) if crate::ledger::support::after_by(b, n, crate::ledger::support::MARGIN_SECS));
            Unread { report: holds_report(&led.root, &folder), before, folder }
        })
        .collect())
}

/// When the newest of a folder's own files was last written.
fn last_written(d: &Path) -> Option<String> {
    let rd = std::fs::read_dir(d).ok()?;
    rd.flatten().map(|e| e.path()).filter(|p| p.is_file()).filter_map(|p| crate::pytime::file_time(&p)).max()
}

/// file:lines, shortened to the folder's own name for a file inside the folder.
pub fn where_of(q: &Quote, folder: &str) -> String {
    let path = if !folder.is_empty() && q.path.starts_with(folder) { &q.path[folder.len()..] } else { q.path.as_str() };
    format!("{path}:{}", q.lines)
}

fn basename(p: &str) -> String {
    Path::new(p).file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default()
}

fn list_of(d: &Dict, k: &str) -> Vec<Value> {
    d.get(k).and_then(|v| v.as_list()).cloned().unwrap_or_default()
}

/// An indexed experiment's repository, as the few lines every surface prints.
pub fn facts(read: &Dict) -> Vec<String> {
    let mut out = Vec::new();
    let mut counts: IndexMap<String, i64> = IndexMap::new();
    let mut kept_local = 0i64;
    let files = list_of(read, "files");
    for f in &files {
        let role = f.get_str("role").unwrap_or("").to_string();
        if role == "data" {
            let n = f.get("files").and_then(|v| v.as_i64()).unwrap_or(0);
            if f.get_str("git") == Some("ignored") {
                kept_local += n;
            }
            *counts.entry("data".into()).or_insert(0) += n;
        } else {
            *counts.entry(role).or_insert(0) += 1;
        }
    }
    let order = ["report", "results", "manifest", "audit", "data", "protocol", "reproduction", "code", "check", "other"];
    let names = |r: &str| -> (&str, &str) {
        match r {
            "report" => ("report", "reports"),
            "results" => ("results file", "results files"),
            "manifest" => ("manifest", "manifests"),
            "audit" => ("audit file", "audit files"),
            "data" => ("data file", "data files"),
            "protocol" => ("protocol", "protocols"),
            "reproduction" => ("reproduction guide", "reproduction guides"),
            "code" => ("code file", "code files"),
            "check" => ("check script", "check scripts"),
            _ => ("other file", "other files"),
        }
    };
    let line = order
        .iter()
        .filter(|r| counts.get(**r).map_or(false, |n| *n != 0))
        .map(|r| {
            let n = counts[*r];
            let (one, many) = names(r);
            let extra = if *r == "data" && kept_local == n {
                " kept local (ignored by git)".to_string()
            } else if *r == "data" && kept_local != 0 {
                format!(" ({kept_local} kept local, ignored by git)")
            } else {
                String::new()
            };
            format!("{n} {}{extra}", if n == 1 { one } else { many })
        })
        .collect::<Vec<_>>()
        .join(" · ");
    let missing = files.iter().filter(|f| f.get_str("state") == Some("missing")).count();
    let changed = files.iter().filter(|f| f.get_str("state") == Some("changed")).count();
    if !line.is_empty() {
        out.push(format!(
            "files: {line}{}{}",
            if changed > 0 { format!(" · {changed} changed since indexed") } else { String::new() },
            if missing > 0 { format!(" · {missing} missing") } else { String::new() }
        ));
    }
    for m in list_of(read, "manifests") {
        let frozen = m.get("frozen").filter(|v| v.truthy()).map(pyjson::py_str);
        let head = format!("{}: {}", basename(m.get_str("path").unwrap_or("")), frozen.map(|f| format!("frozen {f} · ")).unwrap_or_default());
        let ch: Vec<String> = m.get("changed").and_then(|v| v.as_list()).map(|l| l.iter().map(pyjson::py_str).collect()).unwrap_or_default();
        let mi: Vec<String> = m.get("missing").and_then(|v| v.as_list()).map(|l| l.iter().map(pyjson::py_str).collect()).unwrap_or_default();
        let matched = m.get("matched").map(pyjson::py_str).unwrap_or_default();
        let sources = m.get("sources").map(pyjson::py_str).unwrap_or_default();
        // the only one, both, all of them: never 1 of 1
        let hashes = match (matched.parse::<usize>(), sources.parse::<usize>()) {
            (Ok(k), Ok(n)) => format!("{} {}", crate::assess::k_of_n(k, n, "source hash", "source hashes"), if k <= 1 { "matches" } else { "match" }),
            _ => format!("{matched} of {sources} {}", if matched == "1" { "source hashes matches" } else { "source hashes match" }),
        };
        if ch.is_empty() && mi.is_empty() {
            out.push(format!("{head}{hashes}"));
        } else {
            let mut bad: Vec<String> = ch.iter().take(3).cloned().collect();
            bad.extend(mi.iter().take(3).cloned());
            let more = ch.len() + mi.len() - bad.len();
            out.push(format!(
                "{head}{hashes}; changed or missing since frozen: {}{}",
                bad.join(", "),
                if more > 0 { format!(" and {more} more") } else { String::new() }
            ));
        }
    }
    for a in list_of(read, "audits") {
        out.push(format!("audit: {} says {}", basename(a.get_str("path").unwrap_or("")), a.get_str("says").unwrap_or("")));
    }
    for c in list_of(read, "checks") {
        let cmd = c.get_str("command").unwrap_or("");
        let idle = c.get("ok").map_or(true, |v| v.is_null());
        out.push(format!(
            "check: {} · {}{}",
            c.get_str("path").unwrap_or(""),
            c.get_str("state").unwrap_or(""),
            if idle && !cmd.is_empty() { format!(" · run it: rb journal run -- {cmd}") } else { String::new() }
        ));
    }
    let rep = read.get("reproduction").cloned().unwrap_or(Value::Null);
    let rguides: Vec<String> = rep.get("guides").and_then(|v| v.as_list()).map(|l| l.iter().map(pyjson::py_str).collect()).unwrap_or_default();
    let rcommands = rep.get("commands").and_then(|v| v.as_i64()).unwrap_or(0);
    if !rguides.is_empty() {
        out.push(format!("reproduction: {} · {rcommands} command{}", rguides.join(", "), if rcommands == 1 { "" } else { "s" }));
    }
    let g = read.get("git").cloned().unwrap_or(Value::Null);
    if g.get("present").map_or(false, |v| v.truthy()) {
        if g.get("ready").map_or(false, |v| v.truthy()) {
            let of = rep.get("of").and_then(|v| v.as_i64()).unwrap_or(0);
            let tracked = rep.get("tracked").and_then(|v| v.as_i64()).unwrap_or(0);
            let tracked_said = || {
                let said = crate::assess::k_of_n(tracked as usize, of as usize, "report or results file", "report and results files");
                format!("{said} {} tracked · ", if tracked <= 1 { "is" } else { "are" })
            };
            out.push(format!("git: {}folder clean", if of != 0 { tracked_said() } else { String::new() }));
        } else {
            let un = g.get("untracked").and_then(|v| v.as_list()).map(|l| l.len()).unwrap_or(0);
            let mo = g.get("modified").and_then(|v| v.as_list()).map(|l| l.len()).unwrap_or(0);
            let mut parts = Vec::new();
            if un > 0 {
                parts.push(format!("{un} untracked"));
            }
            if mo > 0 {
                parts.push(format!("{mo} changed since the last commit"));
            }
            out.push(format!("git: not ready to checkpoint: {}", parts.join(", ")));
        }
    }
    for s in list_of(read, "stale_quotes") {
        out.push(format!(
            "quote no longer in its file: {} at {}:{} ({}); rb reconcile --apply reads it again",
            s.get_str("kind").unwrap_or(""),
            s.get_str("path").unwrap_or(""),
            s.get_str("lines").unwrap_or(""),
            s.get_str("why").unwrap_or("")
        ));
    }
    out
}

/// One line for a list: where the checks, manifests, audits, reproduction and git stand.
pub fn brief(read: &Dict) -> String {
    let mut parts: Vec<String> = Vec::new();
    let checks = list_of(read, "checks");
    let ok = checks.iter().filter(|c| c.get("ok") == Some(&Value::Bool(true))).count();
    let bad = checks.iter().filter(|c| c.get("ok") == Some(&Value::Bool(false))).count();
    let idle = checks.iter().filter(|c| c.get("ok").map_or(true, |v| v.is_null())).count();
    if !checks.is_empty() {
        let xs: Vec<String> = [
            if ok > 0 { format!("{ok} passed") } else { String::new() },
            if bad > 0 { format!("{bad} failed") } else { String::new() },
            if idle > 0 { format!("{idle} not run under rb's watch") } else { String::new() },
        ]
        .into_iter()
        .filter(|x| !x.is_empty())
        .collect();
        parts.push(format!("checks: {}", xs.join(", ")));
    }
    for a in list_of(read, "audits") {
        parts.push(format!("{} says {}", basename(a.get_str("path").unwrap_or("")), a.get_str("says").unwrap_or("")));
    }
    for m in list_of(read, "manifests") {
        let n = m.get("changed").and_then(|v| v.as_list()).map(|l| l.len()).unwrap_or(0) + m.get("missing").and_then(|v| v.as_list()).map(|l| l.len()).unwrap_or(0);
        parts.push(format!(
            "{}: {}",
            basename(m.get_str("path").unwrap_or("")),
            if n == 0 { "sources match".to_string() } else { format!("{n} source{} changed since frozen", if n == 1 { "" } else { "s" }) }
        ));
    }
    let rep = read.get("reproduction").cloned().unwrap_or(Value::Null);
    if rep.get("guides").and_then(|v| v.as_list()).map_or(false, |l| !l.is_empty()) {
        let c = rep.get("commands").and_then(|v| v.as_i64()).unwrap_or(0);
        parts.push(format!("reproduction guide, {c} command{}", if c == 1 { "" } else { "s" }));
    }
    let g = read.get("git").cloned().unwrap_or(Value::Null);
    if g.get("present").map_or(false, |v| v.truthy()) {
        parts.push(if g.get("ready").map_or(false, |v| v.truthy()) { "git clean".into() } else { "git: not ready to checkpoint".into() });
    }
    let changed = list_of(read, "files").iter().filter(|f| f.get_str("state") != Some("present")).count();
    if changed > 0 || !list_of(read, "stale_quotes").is_empty() {
        parts.push("changed since indexed".into());
    }
    parts.join(" · ")
}
