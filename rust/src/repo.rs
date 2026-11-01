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
/// A path to a Python file, where no path character comes before it. Matched by the linear engine, with the character
/// before it consumed rather than looked behind at: a backtracking engine takes time that grows with the cube of a long
/// run of path characters, and a report's table of numbers is one.
fn pypath_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"(?:^|[^\w/.-])((?:[\w.-]+/)*[\w.-]+\.py)\b")
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
        for m in pypath_re().captures_iter(&b.text) {
            let g = m.get(1).unwrap().as_str().to_string();
            let line = b.start + newlines_before(&b.text, m.get(1).unwrap().start());
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

/// The names a folder of results goes by: below the top, a folder with one of these names is where results land, as
/// the usual folders at the top are.
pub const RESULT_WORDS: &[&str] = &[
    "out", "outs", "output", "outputs", "result", "results", "eval", "evals", "evaluation", "evaluations", "eval-out", "eval_out", "metrics",
    "report", "reports", "artifact", "artifacts", "benchmark", "benchmarks", "multirun",
];
/// How far down from the repository's top rb looks for a folder named for results: `a/b/c/results/` is as deep as it goes.
pub const MAX_ROOT_DEPTH: usize = 4;
/// Folders of code, tests and configs, and a website's static files: rb looks for results in none of them.
pub const CODE_DIRS: &[&str] = &["src", "lib", "test", "tests", "config", "configs", "conf", "scripts", "_static", "static", "public"];
/// How many folders one search for results folders opens at most.
pub const MAX_DIRS_LOOKED: usize = 5000;
/// A folder with more subfolders than this (a dataset's class folders) is not searched below.
pub const MAX_CHILD_DIRS: usize = 300;
/// How far below a folder where results land rb looks for the folders that hold them.
pub const RESULTS_DEPTH: usize = 4;
/// The files whose being in a folder rb does not read is worth a word: what results are usually written as.
const RESULTISH: &[&str] = &[".json", ".jsonl", ".csv", ".tsv"];

/// A folder that holds results-like files and that rb does not read, and why.
#[derive(Debug, Clone)]
pub struct NotRead {
    /// from the repository's top, with a slash
    pub folder: String,
    /// its own .json, .jsonl, .csv and .tsv files
    pub files: usize,
    /// its subfolders, where that is why rb looks no further down
    pub folders: usize,
    pub why: &'static str,
}

/// Where a repository keeps its results, as one search finds them.
#[derive(Debug, Clone, Default)]
pub struct Discovery {
    /// the folders where results land, from the top, each with a slash: the usual ones at the top that are there, then
    /// each folder below named for results, by path
    pub roots: Vec<String>,
    /// the folders the search passed that hold results-like files and that rb does not read
    pub not_read: Vec<NotRead>,
    /// the search stopped at MAX_DIRS_LOOKED folders
    pub cut: bool,
}

thread_local! {
    /// The search for results folders, by repository, while one command line runs: every read asks it, and the
    /// answer cannot change until a command rb watches runs.
    static FOUND_ROOTS: std::cell::RefCell<std::collections::HashMap<PathBuf, Discovery>> = std::cell::RefCell::new(std::collections::HashMap::new());
}

/// Forget the search for results folders: a new command line, or a command rb watched, looks again.
pub fn forget_roots() {
    FOUND_ROOTS.with(|f| f.borrow_mut().clear());
}

/// Whether `d` is a repository of its own (a submodule, a nested clone, the folder rb tour makes): its files are not
/// this repository's.
fn own_repository(d: &Path) -> bool {
    d.join(".git").exists()
}

/// Whether `d` is code or an environment, never a folder of results: a Python package, a virtualenv, a conda
/// environment.
fn code_or_env(d: &Path) -> bool {
    d.join("__init__.py").is_file() || d.join("pyvenv.cfg").is_file() || d.join("conda-meta").is_dir()
}

/// A folder the search for results goes into: a real folder (no link), named with no dot, in no dependency folder, no
/// repository or worktree of its own, and no code or environment.
fn searchable(c: &Path, common: Option<&Path>) -> bool {
    let n = name_of(c).to_lowercase();
    let is_link = std::fs::symlink_metadata(c).map(|m| m.file_type().is_symlink()).unwrap_or(true);
    !is_link
        && c.is_dir()
        && !n.starts_with('.')
        && !SKIP.contains(&n.as_str())
        && !crate::journal::DEPENDENCY_DIRS.contains(&n.as_str())
        && n != "rb-tour"
        && !own_repository(c)
        && !other_checkout(c, common)
        && !code_or_env(c)
}

/// Whether a folder holds a results file or a report somewhere in it, looking at most a few levels down.
fn holds_results_below(d: &Path, depth: usize) -> bool {
    if holds_results(d) {
        return true;
    }
    if depth == 0 {
        return false;
    }
    sorted_children(d).iter().any(|c| searchable(c, None) && holds_results_below(c, depth - 1))
}

/// Where the repository at `root` keeps its results: the usual folders at the top (read whether or not git ignores
/// them), and every folder named for results (RESULT_WORDS) down to MAX_ROOT_DEPTH levels that holds a results file or
/// a report. The search never goes into a dot-folder, a dependency folder or a virtualenv, a build folder at the top,
/// a folder of code, tests or configs, a repository or worktree of its own, or a folder git ignores (a folder named
/// for results is read even so, as results/ is).
pub fn discover(root: &Path) -> Discovery {
    let key = root.to_path_buf();
    if let Some(d) = FOUND_ROOTS.with(|f| f.borrow().get(&key).cloned()) {
        return d;
    }
    let d = discover_now(root);
    FOUND_ROOTS.with(|f| f.borrow_mut().insert(key, d.clone()));
    d
}

/// The usual results folders a repository's top holds, each by its own spelling (`listed`, the top's entries): on a
/// file system that ignores case, a Results/ answers to results/ too, and is found as itself, never named twice.
fn usual_roots<'a>(listed: &HashSet<String>, is_dir: &dyn Fn(&str) -> bool) -> Vec<&'a str> {
    RESULT_ROOTS.iter().copied().filter(|n| listed.contains(*n) && is_dir(n)).collect()
}

fn discover_now(root: &Path) -> Discovery {
    let common = crate::ledger::common_dir_of(root);
    let ignored: HashSet<String> = zsplit(git(root, &["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"]))
        .into_iter()
        .filter_map(|p| p.strip_suffix('/').map(str::to_string))
        .collect();
    let mut out = Discovery::default();
    let listed: HashSet<String> = std::fs::read_dir(root).map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().into_owned()).collect()).unwrap_or_default();
    for name in usual_roots(&listed, &|n: &str| root.join(n).is_dir()) {
        if !other_checkout(&root.join(name), common.as_deref()) {
            out.roots.push(format!("{name}/"));
        }
    }
    let mut found: Vec<String> = Vec::new();
    let mut looked = 0usize;
    // (folder, how deep, whether git ignores it or a folder above it)
    let mut queue: std::collections::VecDeque<(PathBuf, usize, bool)> = std::collections::VecDeque::new();
    queue.push_back((root.to_path_buf(), 0, false));
    while let Some((d, depth, under_ignored)) = queue.pop_front() {
        let children: Vec<PathBuf> = sorted_children(&d).into_iter().filter(|c| searchable(c, common.as_deref())).collect();
        if depth > 0 && children.len() > MAX_CHILD_DIRS {
            // a dataset's class folders, a cache: no results folder hides among hundreds of siblings
            if !under_ignored {
                out.not_read.push(NotRead { folder: format!("{}/", rel_posix(&d, root)), files: 0, folders: children.len(), why: "it holds too many folders to look in" });
            }
            continue;
        }
        for c in children {
            let name = name_of(&c);
            let n = name.to_lowercase();
            let level = depth + 1;
            if (level == 1 && (crate::journal::BUILD_DIRS.contains(&n.as_str()) || RESULT_ROOTS.contains(&name.as_str()))) || CODE_DIRS.contains(&n.as_str()) {
                continue;
            }
            looked += 1;
            if looked > MAX_DIRS_LOOKED {
                out.cut = true;
                break;
            }
            let rel = rel_posix(&c, root);
            if RESULT_WORDS.contains(&n.as_str()) {
                if holds_results_below(&c, RESULTS_DEPTH) {
                    found.push(format!("{rel}/"));
                }
                continue;
            }
            // a folder git ignores is searched for folders named for results, which are read even so, and nothing
            // in it is named as unread: what git ignores is a cache, a dataset or an environment as often as not
            let ignored = under_ignored || ignored.contains(&rel);
            if !ignored {
                let own = sorted_children(&c)
                    .iter()
                    .filter(|f| f.is_file() && RESULTISH.contains(&suffix(&name_of(f).to_lowercase()).as_str()) && !crate::journal::tool_file(&name_of(f)))
                    .count();
                if own > 0 {
                    out.not_read.push(NotRead { folder: format!("{rel}/"), files: own, folders: 0, why: "its name does not say it holds results" });
                }
            }
            if level < MAX_ROOT_DEPTH {
                queue.push_back((c, level, ignored));
            }
        }
        if out.cut {
            break;
        }
    }
    found.sort();
    out.roots.extend(found);
    out
}

/// The folders where results land in the repository at `root`, from its top, each with a slash.
pub fn result_roots_of(root: &Path) -> Vec<String> {
    discover(root).roots
}

/// Every folder that holds an experiment's results: below each folder where results land, the first folder down each
/// path (to RESULTS_DEPTH levels) that holds a report or a results file; a folder found by its name (not one of the
/// usual folders at the top) that holds them itself is one.
pub fn result_folders(root: &Path) -> Vec<String> {
    fn down(d: &Path, depth: usize, root: &Path, common: Option<&Path>, out: &mut Vec<String>) {
        for c in sorted_children(d) {
            if !searchable(&c, common) {
                continue;
            }
            if holds_results(&c) {
                out.push(format!("{}/", rel_posix(&c, root)));
            } else if depth > 1 {
                down(&c, depth - 1, root, common, out);
            }
        }
    }
    let mut out = Vec::new();
    let common = crate::ledger::common_dir_of(root);
    for name in result_roots_of(root) {
        let name = name.trim_end_matches('/');
        let base = root.join(name);
        if !base.is_dir() || other_checkout(&base, common.as_deref()) {
            continue;
        }
        if !RESULT_ROOTS.contains(&name) && holds_results(&base) {
            out.push(format!("{name}/"));
            continue;
        }
        down(&base, RESULTS_DEPTH, root, common.as_deref(), &mut out);
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
            out.push(format!("git: {}folder unchanged since the last commit", if of != 0 { tracked_said() } else { String::new() }));
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

// ---------------------------------------------------------------- what a run folder's own record says of its run

/// The names a run's own record of itself goes by, in a folder of one run: the config a run writes beside its results.
pub const RUN_CONFIG_NAMES: &[&str] = &[
    "config.yaml", "config.yml", "config.json", "config.toml", ".hydra/config.yaml", "hparams.yaml", "params.yaml", "params.json", "args.json",
    "args.yaml", "run.json", "run.yaml", "meta.json", "metadata.json",
];
const COMMAND_KEYS: &[&str] = &["command", "cmd", "argv", "cli", "command_line"];
const COMMIT_KEYS: &[&str] = &["commit", "git_commit", "git_sha", "git.commit", "git.sha", "code.commit", "git.hash", "git_hash"];
const DIRTY_KEYS: &[&str] = &["dirty", "git_dirty", "git.dirty", "code.dirty", "uncommitted"];
const STARTED_KEYS: &[&str] = &["started", "started_at", "start_time", "start", "timestamp"];
const FINISHED_KEYS: &[&str] = &["finished", "finished_at", "end_time", "ended", "ended_at", "completed_at"];

/// One fact a run's own record states, with the line of its file it is on.
#[derive(Debug, Clone)]
pub struct Said<T> {
    pub value: T,
    pub key: String,
    pub line: usize,
}

/// What a run's own config says of the run: its command, the commit, whether the code had uncommitted changes, when
/// it started and finished, and its other settings. rb reads these as the file's words, never as what rb saw.
#[derive(Debug, Clone, Default)]
pub struct RunSays {
    /// from the repository's top
    pub path: String,
    pub command: Option<Said<String>>,
    /// the command is a list of words (an argv), shown joined
    pub command_list: bool,
    pub commit: Option<Said<String>>,
    pub dirty: Option<Said<bool>>,
    pub started: Option<Said<String>>,
    pub finished: Option<Said<String>>,
    /// every other value, flattened to dotted keys
    pub settings: Dict,
}

impl RunSays {
    /// When the run's output existed, as the folder says it: when it finished, else when it started.
    pub fn produced_at(&self) -> Option<&Said<String>> {
        self.finished.as_ref().or(self.started.as_ref())
    }

    /// The facts in the file's own words, with their lines: `command (line 1) · commit 9ba0f86 (line 2) · dirty: true
    /// (line 3)`.
    pub fn said(&self) -> Vec<String> {
        let mut parts = Vec::new();
        if let Some(c) = &self.command {
            parts.push(format!("{}: {} (line {})", c.key, crate::pyjson::repr_str(&c.value), c.line));
        }
        if let Some(c) = &self.commit {
            let dirty = if self.dirty.as_ref().map_or(false, |d| d.key == c.key && d.value) { "-dirty" } else { "" };
            parts.push(format!("{}: {}{dirty} (line {})", c.key, c.value.chars().take(12).collect::<String>(), c.line));
        }
        // a commit git describe --dirty wrote says its own value once, never "commit: true" beside it
        if let Some(d) = self.dirty.as_ref().filter(|d| self.commit.as_ref().map_or(true, |c| c.key != d.key)) {
            parts.push(format!("{}: {} (line {})", d.key, d.value, d.line));
        }
        if let Some(s) = &self.started {
            parts.push(format!("{}: {} (line {})", s.key, s.value, s.line));
        }
        parts
    }

    /// Where the file says the code had uncommitted changes when the run ran, in rb's words, naming the file and line.
    pub fn dirty_line(&self) -> Option<String> {
        let d = self.dirty.as_ref().filter(|d| d.value)?;
        // a commit `git describe --dirty` wrote says it with its own value, never "commit: true"
        let value = match self.commit.as_ref().filter(|c| c.key == d.key) {
            Some(c) => format!("{}-dirty", c.value),
            None => "true".to_string(),
        };
        Some(format!("{}:{} says {}: {value}: the code had uncommitted changes when it ran", self.path, d.line, d.key))
    }

    pub fn to_value(&self) -> Value {
        let said = |s: &Option<Said<String>>| match s {
            Some(s) => crate::obj! {"value" => s.value.clone(), "key" => s.key.clone(), "line" => s.line as i64},
            None => Value::Null,
        };
        crate::obj! {
            "path" => self.path.clone(), "command" => said(&self.command), "commit" => said(&self.commit),
            "dirty" => match &self.dirty { Some(d) => crate::obj! {"value" => d.value, "key" => d.key.clone(), "line" => d.line as i64}, None => Value::Null },
            "started" => said(&self.started), "finished" => said(&self.finished)
        }
    }
}

/// The line of a structured file a dotted key is on: YAML by its own reader, other files by the first line that
/// names the key's last part as a key.
fn key_line(text: &str, kind: &str, key: &str) -> usize {
    if kind == "yaml" {
        if let Some(n) = sources::yaml_line_of(text, key) {
            return n;
        }
    }
    let last = key.rsplit('.').next().unwrap_or(key);
    let pat = format!(r#"^\s*["']?{}["']?\s*[:=]"#, regex::escape(last));
    let r = Regex::new(&pat).unwrap();
    text.split('\n').position(|l| r.is_match(l)).map_or(1, |i| i + 1)
}

/// A value's text as a run's record gives it: a list of words (an argv) joined with spaces.
fn said_text(v: &Value) -> Option<String> {
    match v {
        Value::Str(s) if !s.trim().is_empty() => Some(s.trim().to_string()),
        Value::List(l) if !l.is_empty() && l.iter().all(|x| matches!(x, Value::Str(_))) => Some(l.iter().map(pyjson::py_str).collect::<Vec<_>>().join(" ")),
        Value::Int(_) | Value::Float(_) => Some(pyjson::py_str(v)),
        _ => None,
    }
}

/// What the run config at `rel` (from `root`) says of its run, or None when it cannot be read or says none of the
/// run's command, commit or uncommitted changes.
pub fn run_says(root: &Path, rel: &str) -> Option<RunSays> {
    let p = root.join(rel);
    let kind = sources::structured_kind(rel)?;
    if file_size(&p) > MAX_DOC_BYTES {
        return None;
    }
    let text = String::from_utf8_lossy(&std::fs::read(&p).ok()?).into_owned();
    let doc = sources::parse_structured(&text, kind, rel).ok()?;
    let doc = sources::unwrap_wandb(doc);
    if !doc.is_dict() {
        return None;
    }
    let flat = sources::flatten(&doc, "");
    let mut says = RunSays { path: rel.to_string(), ..Default::default() };
    let mut taken: HashSet<String> = HashSet::new();
    let find = |keys: &[&str], taken: &mut HashSet<String>| -> Option<(String, Value)> {
        // the first key the file gives, as it gives it
        for k in keys {
            if let Some(v) = doc.get(k).filter(|v| !v.is_dict()).cloned().or_else(|| flat.get(*k).cloned()) {
                taken.insert(k.to_string());
                return Some((k.to_string(), v));
            }
        }
        None
    };
    if let Some((k, v)) = find(COMMAND_KEYS, &mut taken) {
        // a command as one text; an argv list is shown joined, and never offered as the command a log line holds,
        // since joining it loses its quoting
        if let Some(t) = said_text(&v) {
            says.command_list = matches!(v, Value::List(_));
            says.command = Some(Said { line: key_line(&text, kind, &k), key: k, value: t });
        }
    }
    if let Some((k, v)) = find(COMMIT_KEYS, &mut taken) {
        // `git describe --dirty` writes the commit with -dirty after it
        let t = said_text(&v).unwrap_or_default();
        let (hash, described_dirty) = match t.strip_suffix("-dirty") {
            Some(h) => (h.to_string(), true),
            None => (t.clone(), false),
        };
        if hash.len() >= 7 && hash.chars().all(|c| c.is_ascii_hexdigit()) {
            let line = key_line(&text, kind, &k);
            if described_dirty && says.dirty.is_none() {
                says.dirty = Some(Said { line, key: k.clone(), value: true });
            }
            says.commit = Some(Said { line, key: k, value: hash.to_lowercase() });
        } else {
            // not a commit rb can name (a branch, a tag): one of the run's settings
            taken.remove(&k);
        }
    }
    if let Some((k, v)) = find(DIRTY_KEYS, &mut taken) {
        let b = match &v {
            Value::Bool(b) => Some(*b),
            Value::Int(i) if *i == 0 || *i == 1 => Some(*i == 1),
            Value::Str(t) => match t.trim().to_lowercase().as_str() {
                "true" | "yes" | "1" => Some(true),
                "false" | "no" | "0" => Some(false),
                _ => None,
            },
            _ => None,
        };
        match b {
            Some(b) => says.dirty = Some(Said { line: key_line(&text, kind, &k), key: k, value: b }),
            None => {
                taken.remove(&k);
            }
        }
    }
    for (keys, slot) in [(STARTED_KEYS, 0), (FINISHED_KEYS, 1)] {
        if let Some((k, v)) = find(keys, &mut taken) {
            if let Some(t) = said_text(&v).and_then(|t| crate::pytime::norm_time(&t).map(|_| t)) {
                let s = Some(Said { line: key_line(&text, kind, &k), key: k, value: t });
                if slot == 0 {
                    says.started = s;
                } else {
                    says.finished = s;
                }
            }
        }
    }
    if says.command.is_none() && says.commit.is_none() && says.dirty.is_none() {
        return None;
    }
    says.settings = flat.into_iter().filter(|(k, _)| !taken.contains(k)).collect();
    Some(says)
}

/// The run config in a folder of one run, as the folder holds it: the first of RUN_CONFIG_NAMES there that says
/// anything of its run.
pub fn run_config_in(root: &Path, folder: &str) -> Option<RunSays> {
    let base = folder.trim_end_matches('/');
    RUN_CONFIG_NAMES.iter().find_map(|n| {
        let rel = format!("{base}/{n}");
        root.join(&rel).is_file().then(|| run_says(root, &rel)).flatten()
    })
}

/// A folder of one run: its own record of the run, the results file its numbers are in, and the `rb run add` that
/// records it with all the folder gives.
#[derive(Debug, Clone)]
pub struct RunFolder {
    pub folder: String,
    pub says: RunSays,
    /// the results file, from the repository's top
    pub results: String,
    /// when the results file says the run finished, with its key and line
    pub results_finished: Option<Said<String>>,
    /// the experiment that reads the folder, when exactly one does
    pub experiment: Option<String>,
    /// rb run add's arguments, after `rb`
    pub argv: Vec<String>,
}

impl RunFolder {
    /// When the run's output existed, as the folder says it, with the file and line that says so.
    pub fn produced_at(&self) -> Option<(String, String)> {
        if let Some(f) = &self.results_finished {
            return Some((f.value.clone(), format!("{}:{}", self.results, f.line)));
        }
        self.says.produced_at().map(|s| (s.value.clone(), format!("{}:{}", self.says.path, s.line)))
    }

    pub fn to_value(&self) -> Value {
        crate::obj! {
            "folder" => self.folder.clone(), "config" => self.says.to_value(), "results" => self.results.clone(),
            "experiment" => Value::opt_str(self.experiment.clone()),
            "produced_at" => self.produced_at().map_or(Value::Null, |(at, from)| crate::obj! {"at" => at, "from" => from}),
            "settings" => Value::Dict(self.says.settings.clone()), "line" => format!("rb {}", crate::cli::shlex_join(&self.argv))
        }
    }
}

/// The results file of a folder of one run: a structured file that is not its run config, a .json one first, and one
/// named for metrics or results before the rest.
fn results_file_in(root: &Path, folder: &str, config: &str) -> Option<String> {
    let base = folder.trim_end_matches('/');
    let mut files: Vec<String> = sorted_children(&root.join(base))
        .into_iter()
        .filter(|p| p.is_file())
        .map(|p| format!("{base}/{}", name_of(&p)))
        .filter(|rel| {
            let name = rel.rsplit('/').next().unwrap_or("");
            rel != config && sources::structured_kind(rel).is_some() && !rel.ends_with(".jsonl") && !crate::journal::tool_file(name) && !RUN_CONFIG_NAMES.contains(&name.to_lowercase().as_str())
        })
        .collect();
    let rank = |rel: &String| {
        let n = rel.rsplit('/').next().unwrap_or("").to_lowercase();
        let named = ["metrics", "results", "result", "eval", "scores", "summary"].iter().any(|w| n.starts_with(w));
        (!named, !n.ends_with(".json"), n)
    };
    files.sort_by_key(rank);
    files.into_iter().next()
}

/// What an experiment gives the `rb run add` line of a run folder: its per-run settings, what it varies, each variant
/// with its own settings, and the names of the numbers its criteria read (and the catalogue's).
#[derive(Debug, Clone, Default)]
pub struct LineFacts {
    pub per_run: Vec<String>,
    pub varies: Vec<String>,
    pub variants: Vec<(String, Dict)>,
    pub metrics: Vec<String>,
}

/// A value a run config states for a setting: by its full key, else by its last part where exactly one key ends so
/// (teacher.precision beside student.precision names no one value, and is never guessed).
fn config_value<'a>(settings: &'a Dict, name: &str) -> Option<&'a Value> {
    let last = name.rsplit('.').next().unwrap_or(name);
    let by_last = || {
        let ends: Vec<&Value> = settings.iter().filter(|(k, _)| k.rsplit('.').next() == Some(last)).map(|(_, v)| v).collect();
        (ends.len() == 1).then(|| ends[0])
    };
    settings.get(name).or_else(by_last).filter(|v| !v.is_dict() && !v.is_list() && !v.is_null())
}

/// The one variant a run config's values of what the experiment varies name: a variant whose own value of a varied
/// setting is the config's, or, where it has none, whose name is the config's value (prompt: plain names plain).
fn variant_of(settings: &Dict, facts: &LineFacts) -> Option<String> {
    let stated: Vec<(&String, String)> = facts.varies.iter().filter_map(|n| config_value(settings, n).map(|v| (n, pyjson::py_str(v)))).collect();
    if stated.is_empty() {
        return None;
    }
    let fits = |(name, own): &(String, Dict)| {
        stated.iter().all(|(n, v)| match own.get(n.as_str()).or_else(|| own.get(n.rsplit('.').next().unwrap_or(n))) {
            Some(x) => &pyjson::py_str(x) == v,
            None => v == name,
        })
    };
    let hits: Vec<&String> = facts.variants.iter().filter(|x| fits(x)).map(|(n, _)| n).collect();
    (hits.len() == 1).then(|| hits[0].clone())
}

/// The names of the numbers a results file holds that an experiment reads, as --keys takes them.
fn keys_read(root: &Path, results: &str, facts: &LineFacts) -> Vec<String> {
    let Some(kind) = sources::structured_kind(results) else { return Vec::new() };
    let p = root.join(results);
    if file_size(&p) > MAX_DOC_BYTES {
        return Vec::new();
    }
    let Ok(bytes) = std::fs::read(&p) else { return Vec::new() };
    let Ok(doc) = sources::parse_structured(&String::from_utf8_lossy(&bytes), kind, results) else { return Vec::new() };
    sources::flatten(&doc, "")
        .into_iter()
        // a number recorded under its own name, which is the name a criterion reads: a nested one (eval.top1) is recorded
        // as eval.top1, so it is no key the line can give
        .filter(|(k, v)| v.as_f64().is_some() && !v.is_bool() && facts.metrics.iter().any(|m| m == k))
        .map(|(k, _)| k)
        .collect()
}

/// Every results folder in `folders` that is a folder of one run: it holds a run config that says something of its
/// run, and a results file beside it. `readers` gives the experiments that read a folder, and `facts` what an
/// experiment gives its line: the variant the config's values of what it varies name, the numbers it reads (--keys),
/// and its per-run settings, from the config where it says them. A folder no experiment reads takes the one experiment
/// of `experiments` whose variants the config names one of, where exactly one does.
pub fn run_folders(root: &Path, folders: &[(String, Vec<String>)], experiments: &[String], facts: &dyn Fn(&str) -> Option<LineFacts>) -> Vec<RunFolder> {
    let mut out = Vec::new();
    for (folder, readers) in folders {
        let Some(says) = run_config_in(root, folder) else { continue };
        let Some(results) = results_file_in(root, folder, &says.path) else { continue };
        let results_finished = run_says_time(root, &results);
        let experiment = (readers.len() == 1).then(|| readers[0].clone());
        // the experiment the line records it on: the one that reads the folder, else the one whose variant it names
        let line_exp = experiment.clone().or_else(|| {
            let named: Vec<&String> = experiments.iter().filter(|e| facts(e).map_or(false, |f| variant_of(&says.settings, &f).is_some())).collect();
            (readers.is_empty() && named.len() == 1).then(|| named[0].clone())
        });
        let mut argv: Vec<String> = vec!["run".into(), "add".into(), line_exp.clone().unwrap_or_else(|| "<experiment>".into()), "--from".into(), results.clone()];
        if let Some(f) = line_exp.as_deref().and_then(facts) {
            if let Some(v) = variant_of(&says.settings, &f) {
                argv.extend(["--variant".to_string(), v]);
            }
            let keys = keys_read(root, &results, &f);
            if !keys.is_empty() {
                argv.extend(["--keys".to_string(), keys.join(",")]);
            }
        }
        argv.extend(["--config".to_string(), says.path.clone()]);
        if let Some(f) = line_exp.as_deref().and_then(facts) {
            for name in &f.per_run {
                if let Some(v) = config_value(&says.settings, name) {
                    argv.extend(["--set".to_string(), format!("{name}={}", pyjson::py_str(v))]);
                }
            }
        }
        if let Some(c) = &says.commit {
            argv.extend(["--commit".to_string(), c.value.clone()]);
        }
        let rf = RunFolder { folder: folder.clone(), says: says.clone(), results, results_finished, experiment, argv: Vec::new() };
        let mut rf = rf;
        if let Some((at, _)) = rf.produced_at() {
            argv.extend(["--produced-at".to_string(), at]);
        }
        if let Some(c) = says.command.as_ref().filter(|c| !says.command_list && !c.value.contains('\n')) {
            argv.extend(["--command".to_string(), c.value.clone()]);
            // the line the value starts on holds it, as --command-source checks it
            let text = std::fs::read_to_string(root.join(&says.path)).unwrap_or_default();
            let lines: Vec<&str> = text.split('\n').collect();
            if crate::ledger::support::line_holds(&lines, c.line, &c.value) {
                argv.extend(["--command-source".to_string(), format!("{}:{}", says.path, c.line)]);
            }
        }
        rf.argv = argv;
        out.push(rf);
    }
    out
}

/// When a results file says its run finished (or started, where it gives no end), with its key and line.
fn run_says_time(root: &Path, rel: &str) -> Option<Said<String>> {
    let kind = sources::structured_kind(rel)?;
    let p = root.join(rel);
    if file_size(&p) > MAX_DOC_BYTES {
        return None;
    }
    let text = String::from_utf8_lossy(&std::fs::read(&p).ok()?).into_owned();
    let doc = sources::parse_structured(&text, kind, rel).ok()?;
    for k in FINISHED_KEYS {
        if let Some(t) = doc.get(k).and_then(said_text).filter(|t| crate::pytime::norm_time(t).is_some()) {
            return Some(Said { line: key_line(&text, kind, k), key: k.to_string(), value: t });
        }
    }
    None
}

/// The settings whose values differ between run folders that sit side by side (in the same parent folder): each key
/// with its values, each value with the folders that hold it, in folder order.
pub fn differing_settings(runs: &[RunFolder]) -> Vec<(String, Vec<(String, Vec<String>)>)> {
    if runs.len() < 2 {
        return Vec::new();
    }
    // each folder named from the folder all of them sit in: r012/ beside r011/, sweep/ga/0/ beside sweep/gb/0/
    let parts: Vec<Vec<&str>> = runs.iter().map(|r| r.folder.trim_end_matches('/').split('/').collect()).collect();
    let mut common = 0;
    while parts.iter().all(|p| p.len() > common + 1 && p[common] == parts[0][common]) {
        common += 1;
    }
    let mut keys: BTreeSet<String> = BTreeSet::new();
    for r in runs {
        keys.extend(r.says.settings.keys().cloned());
    }
    let mut out: Vec<(String, Vec<(String, Vec<String>)>)> = Vec::new();
    for k in keys {
        let mut values: IndexMap<String, Vec<String>> = IndexMap::new();
        let mut holds_path = false;
        for (r, p) in runs.iter().zip(&parts) {
            let v = r.says.settings.get(&k).map(|v| pyjson::py_str(v)).unwrap_or_else(|| "(not given)".into());
            let folder = r.folder.trim_end_matches('/');
            let last = p.last().copied().unwrap_or("");
            // a value that names its own folder (an output path) differs by construction
            if v.contains(folder) || v.ends_with(&format!("/{last}")) || v.contains(&format!("/{last}/")) {
                holds_path = true;
            }
            values.entry(v).or_default().push(p[common..].join("/"));
        }
        if values.len() > 1 && !holds_path {
            out.push((k, values.into_iter().collect()));
        }
    }
    out
}

/// One setting's values across run folders, in words: `eval.checker: answer_tag (9 folders), last_number (3:
/// r010_worked_s0, r011_step_n500_s0, r012_plain_s0)`.
pub fn differing_said(key: &str, values: &[(String, Vec<String>)]) -> String {
    let parts: Vec<String> = values
        .iter()
        .map(|(v, fs)| {
            let shown: String = v.chars().take(60).collect::<String>() + if v.chars().count() > 60 { "..." } else { "" };
            if fs.len() <= 3 {
                format!("{shown} ({})", fs.join(", "))
            } else {
                format!("{shown} ({} folders)", fs.len())
            }
        })
        .collect();
    format!("{key}: {}", parts.join(", "))
}

/// What a run's config (`rb run add --config`) says of the code it ran, where it says it had uncommitted changes:
/// read from the file as it is, while its bytes are those the run's receipt kept (`sha256`), or None.
pub fn config_dirty_line(root: &Path, path: &str, sha256: Option<&str>) -> Option<String> {
    if let Some(want) = sha256.filter(|s| !s.is_empty()) {
        let got = sources::file_ref(root, path).ok()?;
        if got.get_str("sha256") != Some(want) {
            return None;
        }
    }
    run_says(root, path)?.dirty_line()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// On a file system that ignores case (Windows, macOS), results/ answers for a Results/ folder: the usual folder is
    /// taken only by its own spelling, so Results/ is found once, as itself.
    #[test]
    fn a_usual_folder_is_taken_by_its_own_spelling() {
        let listed: HashSet<String> = ["Results".to_string(), "out".to_string()].into_iter().collect();
        let ignoring_case = |n: &str| listed.iter().any(|l| l.eq_ignore_ascii_case(n));
        assert_eq!(usual_roots(&listed, &ignoring_case), vec!["out"]);
    }
}
