//! What holds a run's numbers up now (EVIDENCE.md E5). A file a run named (its numbers' file, an artifact) is
//! worked out in the checkout rb runs in: unchanged, changed or gone since the run was recorded. Changed or gone, it is
//! a problem only where this checkout held the bytes the run recorded (the run was recorded here, as rb's kept copy of
//! its writes says, or this clone's journal saw the file with those bytes) and rb holds no copy of them (neither the
//! run's kept tree nor a commit holds those bytes at its path). Anywhere else it is a note: where a copy is, or "not in
//! this clone". The finding keeps its recorded number; rb never hides one.

use super::*;
use std::cell::RefCell;

/// A named file, now.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FileNow {
    Unchanged,
    Changed,
    Gone,
}

/// One file a run named, as it stands in this checkout.
#[derive(Clone, Debug)]
pub struct NamedFile {
    pub path: String,
    pub now: FileNow,
    /// this checkout held the bytes the run recorded
    pub held: bool,
    /// where rb holds those bytes: ("tree" | "commit", the 40-hex object)
    pub copy: Option<(&'static str, String)>,
}

impl NamedFile {
    /// Changed or gone where this checkout held the bytes, and nothing holds a copy: the number can no longer be
    /// checked against its file.
    pub fn is_problem(&self) -> bool {
        self.now != FileNow::Unchanged && self.held && self.copy.is_none()
    }

    /// Changed or gone where this checkout never held the bytes: a clone, a CI checkout, a file written again here.
    pub fn not_in_clone(&self) -> bool {
        self.now != FileNow::Unchanged && !self.held && self.copy.is_none()
    }

    /// The finding's problem, or the note, in EVIDENCE_WORDS; None where the file is unchanged.
    pub fn said(&self, run: &str) -> Option<String> {
        use crate::shell::strings::evidence;
        let path = self.path.clone();
        let run = run.to_string();
        Some(match (self.now, &self.copy) {
            (FileNow::Unchanged, _) => return None,
            (_, Some(("tree", t))) => evidence("file_copy_tree", &[("path", path), ("run", run), ("what", self.what()), ("tree", t.clone())]),
            (_, Some((_, c))) => evidence("file_copy_commit", &[("path", path), ("run", run), ("what", self.what()), ("commit12", c.chars().take(12).collect()), ("commit", c.clone())]),
            _ if !self.held => evidence("file_not_in_clone", &[("path", path)]),
            _ => evidence("file_problem", &[("path", path), ("run", run), ("what", self.what())]),
        })
    }

    fn what(&self) -> String {
        if self.now == FileNow::Gone { "is gone".into() } else { "changed".into() }
    }
}

thread_local! {
    /// The checkout a page with its record is drawn from (rb export --with-record, rb sync): its runs' files are worked
    /// out there, never in the bundle's copy, which holds none of them.
    static FROM: RefCell<Option<PathBuf>> = const { RefCell::new(None) };
    /// Where rb holds a changed file's recorded bytes, by checkout, run, path, recorded sha and the file's size and time
    /// now: a read of many findings asks git once per file, and a file written again is asked again.
    static COPIES: RefCell<HashMap<(PathBuf, String, String, String, Option<(u64, i128)>), Option<(&'static str, String)>>> = RefCell::new(HashMap::new());
    /// Whether a checkout is in git, asked once per process.
    static IN_GIT: RefCell<HashMap<PathBuf, bool>> = RefCell::new(HashMap::new());
    /// The runs each kept copy wrote, by the copy's path and size, so a read of many findings reads each copy once.
    static KEPT_RUNS: RefCell<HashMap<PathBuf, (u64, Rc<HashSet<String>>)>> = RefCell::new(HashMap::new());
}

/// `f` with each run's files worked out in the checkout at `root` (a page drawn from a bundle's copy of the record).
pub fn drawn_from<T>(root: &Path, f: impl FnOnce() -> T) -> T {
    let was = FROM.with(|x| x.replace(Some(root.to_path_buf())));
    let out = f();
    FROM.with(|x| *x.borrow_mut() = was);
    out
}

/// The checkout a page is drawn from, where one was given.
pub fn drawn_root() -> Option<PathBuf> {
    FROM.with(|x| x.borrow().clone())
}

impl Ledger {
    /// Where a run's files are worked out: the checkout rb runs in, or the one a page is drawn from.
    pub fn evidence_root(&self) -> PathBuf {
        drawn_root().unwrap_or_else(|| self.root.clone())
    }

    /// Each file the run named, as it stands here now. A table's or a database's row is read as its row, not its whole
    /// file, which grows with every row written after it: its file is not one of these.
    pub fn named_files(&self, r: &Run) -> Vec<NamedFile> {
        let root = self.evidence_root();
        let mut rows: HashSet<String> = r.receipt.extra.get("table_row").and_then(|v| v.get_str("path")).map(str::to_string).into_iter().collect();
        rows.extend(r.receipt.row.as_ref().map(|row| row.path.clone()));
        let mut out = Vec::new();
        for f in r.files.iter().filter(|f| !rows.contains(&f.path)) {
            let p = root.join(&f.path);
            let now = match sources::file_ref(&root, &f.path).ok().and_then(|d| d.get("sha256").map(pyjson::py_str)) {
                _ if !p.is_file() => FileNow::Gone,
                Some(h) if h == f.sha256 || sources::file_is_twin_of(&p, &f.sha256) => FileNow::Unchanged,
                None => FileNow::Gone,
                Some(_) => FileNow::Changed,
            };
            if now == FileNow::Unchanged {
                out.push(NamedFile { path: f.path.clone(), now, held: true, copy: None });
                continue;
            }
            let held = self.recorded_here(&r.id) || self.journal_saw(&f.path, &f.sha256);
            let stamp = std::fs::metadata(&p).ok().map(|m| (m.len(), m.modified().ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map_or(0, |d| d.as_nanos() as i128)));
            let key = (root.clone(), r.id.clone(), f.path.clone(), f.sha256.clone(), stamp);
            let copy = match COPIES.with(|c| c.borrow().get(&key).cloned()) {
                Some(hit) => hit,
                None => {
                    let found = self.copy_of(r, &f.path, &f.sha256);
                    COPIES.with(|c| c.borrow_mut().insert(key, found.clone()));
                    found
                }
            };
            out.push(NamedFile { path: f.path.clone(), now, held, copy });
        }
        out
    }

    /// Whether this checkout recorded the run: rb's kept copy of its writes holds the run's write.
    fn recorded_here(&self, run: &str) -> bool {
        let mut ids: HashSet<String> = HashSet::new();
        for p in kept_files(&self.evidence_root()) {
            ids.extend(kept_runs(&p).iter().cloned());
        }
        ids.contains(run)
    }

    /// Whether this clone's journal saw the file with these bytes: at its path now, or among the bytes it saw go (a
    /// look keeps them), so a look that sees the change never undoes what it saw before.
    fn journal_saw(&self, path: &str, sha: &str) -> bool {
        let state = crate::journal::load_state(&self.evidence_root());
        state.get("files").and_then(Value::as_dict).and_then(|f| f.get(path)).and_then(|m| m.get("sha")).and_then(Value::as_str) == Some(sha)
            || state.get("away").and_then(Value::as_dict).map_or(false, |a| a.contains_key(sha))
    }

    /// Where rb holds the bytes the run recorded at `path`: its kept tree, then a commit that holds them there.
    fn copy_of(&self, r: &Run, path: &str, sha: &str) -> Option<(&'static str, String)> {
        let root = self.evidence_root();
        let holds = |obj: &str| -> bool {
            sources::git_bytes(&root, &["cat-file", "blob", &format!("{obj}:{path}")]).map_or(false, |b| sources::sha256_bytes(&b) == sha)
        };
        let cap = crate::capture::Capture::of_run(r);
        for t in [cap.tree.clone(), crate::capture::Capture::attached(r).tree].into_iter().flatten() {
            if holds(&t) {
                let full = sources::git(&root, &["rev-parse", "--verify", "-q", &format!("{t}^{{tree}}")]).map(|s| s.trim().to_string()).unwrap_or(t);
                return Some(("tree", full));
            }
        }
        let listed = sources::git(&root, &["rev-list", "--all", "-n", "50", "--", path]).unwrap_or_default();
        for c in listed.lines().map(str::trim).filter(|c| !c.is_empty()) {
            if holds(c) {
                return Some(("commit", c.to_string()));
            }
        }
        None
    }
}

/// Whether the checkout at `root` recorded the run: its kept copy of rb's writes holds the run's write (S-c). A tree
/// such a run kept that git lacks is gone from this clone, never kept "in the clone that recorded the run".
pub fn recorded_in(root: &Path, run: &str) -> bool {
    kept_files(root).iter().any(|p| kept_runs(p).contains(run))
}

fn in_git(root: &Path) -> bool {
    if let Some(hit) = IN_GIT.with(|g| g.borrow().get(root).copied()) {
        return hit;
    }
    let yes = sources::git(root, &["rev-parse", "--git-dir"]).is_some();
    IN_GIT.with(|g| g.borrow_mut().insert(root.to_path_buf(), yes));
    yes
}

fn kept_files(root: &Path) -> Vec<PathBuf> {
    let mut out = vec![crate::journal::read_dir(root).join(super::kept::KEPT_FILE)];
    if let Some(a) = crate::journal::aside_dir(root) {
        let p = a.join(super::kept::KEPT_FILE);
        if !out.contains(&p) {
            out.push(p);
        }
    }
    out
}

/// The run ids a kept copy's lines wrote.
fn kept_runs(path: &Path) -> Rc<HashSet<String>> {
    let size = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
    if let Some(hit) = KEPT_RUNS.with(|k| k.borrow().get(path).filter(|(s, _)| *s == size).map(|(_, ids)| ids.clone())) {
        return hit;
    }
    let mut ids = HashSet::new();
    for line in std::fs::read_to_string(path).unwrap_or_default().lines() {
        let Ok(v) = pyjson::loads(line) else { continue };
        for w in v.get("writes").and_then(Value::as_list).into_iter().flatten() {
            if w.get_str("kind") == Some("run") {
                if let Some(id) = w.get_str("id") {
                    ids.insert(id.to_string());
                }
            }
        }
    }
    let ids = Rc::new(ids);
    KEPT_RUNS.with(|k| k.borrow_mut().insert(path.to_path_buf(), (size, ids.clone())));
    ids
}

impl Ledger {
    /// A run's evidence as separate lines (E5): the files it named, its config, what it read; one quiet line where all
    /// is as recorded, and nothing where it named none of them. `page` says them as of when the page was made, led once.
    pub fn run_evidence_lines(&self, r: &Run, page: bool) -> Vec<String> {
        use crate::shell::strings::evidence;
        let mut lines: Vec<String> = Vec::new();
        let mut whole = true;
        let mut any = false;
        if !r.files.is_empty() {
            any = true;
            let named = self.named_files(r);
            let m = named.len().to_string();
            let pick = |f: &dyn Fn(&NamedFile) -> bool| -> Vec<String> { named.iter().filter(|n| f(n)).map(|n| n.path.clone()).collect() };
            let changed = pick(&|n| n.now == FileNow::Changed && !n.not_in_clone());
            let gone = pick(&|n| n.now == FileNow::Gone && !n.not_in_clone());
            let away = pick(&|n| n.not_in_clone());
            if changed.is_empty() && gone.is_empty() && away.is_empty() {
                lines.push(evidence("files_unchanged", &[]));
            } else {
                whole = false;
                let (n, names) = (|p: &[String]| p.len().to_string(), |p: &[String]| crate::next_step::and_list(p));
                if !changed.is_empty() {
                    lines.push(evidence("files_changed", &[("n", n(&changed)), ("m", m.clone()), ("names", names(&changed))]));
                }
                if !gone.is_empty() {
                    lines.push(evidence("files_gone", &[("n", n(&gone)), ("m", m.clone()), ("names", names(&gone))]));
                }
                if !away.is_empty() && page {
                    lines.push(evidence("files_not_in_page", &[("n", n(&away)), ("m", m.clone()), ("names", names(&away))]));
                } else if !away.is_empty() {
                    lines.push(evidence("files_not_in_clone", &[("n", n(&away)), ("m", m.clone()), ("names", names(&away))]));
                }
            }
        }
        if let Some(c) = r.receipt.config.as_ref().filter(|c| c.sha256.as_ref().map_or(false, |s| !s.is_empty())) {
            any = true;
            let now = sources::file_ref(&self.evidence_root(), &c.path).ok().and_then(|f| f.get("sha256").map(pyjson::py_str));
            if now.as_deref() == c.sha256.as_deref() {
                lines.push(evidence("config_unchanged", &[]));
            } else {
                whole = false;
                lines.push(evidence("config_changed", &[]));
            }
        }
        let read: Vec<&ResourceUse> = r.inputs.iter().filter(|u| u.sha256.as_ref().map_or(false, |s| !s.is_empty())).collect();
        if !read.is_empty() {
            any = true;
            let mut different: Vec<String> = Vec::new();
            let mut unseen = false;
            for u in &read {
                let state = self.load::<Resource>(&u.resource).ok().and_then(|res| {
                    self.availability(&res).get("locations").and_then(Value::as_list).and_then(|l| {
                        l.iter().find(|x| x.get_str("provider") == Some("local")).map(|x| self.local(x.get_str("ref").unwrap_or("")))
                    })
                });
                match state.and_then(|p| self.hash(&p)) {
                    Some(h) if Some(&h) == u.sha256.as_ref() => {}
                    Some(_) => different.push(u.resource.clone()),
                    None => unseen = true,
                }
            }
            if !different.is_empty() {
                whole = false;
                lines.push(evidence("read_different", &[("names", crate::next_step::and_list(&different))]));
            } else if unseen {
                lines.push(evidence("read_sha_only", &[]));
            } else {
                lines.push(evidence("read_same", &[]));
            }
        }
        if !any {
            return Vec::new();
        }
        if whole && lines.iter().all(|l| !l.starts_with("What it read: a sha256")) {
            // only what the run named is said to be as recorded (S2): a run with no --config names no config
            let named: Vec<String> = [(!r.files.is_empty(), "its files"), (r.receipt.config.as_ref().map_or(false, |c| c.sha256.as_ref().map_or(false, |s| !s.is_empty())), "its config"), (!read.is_empty(), "what it read")]
                .into_iter()
                .filter(|(n, _)| *n)
                .map(|(_, w)| w.to_string())
                .collect();
            let what = crate::next_step::and_list(&named);
            let are = if named.len() == 1 && named[0] != "its files" { "is" } else { "are" };
            let mut c = what.chars();
            let what = c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default();
            lines = vec![evidence("run_quiet", &[("what", what), ("are", are.to_string())])];
        }
        if page {
            // led once, as of when the page was made: "here" is dropped there
            let parts: Vec<String> = lines
                .iter()
                .map(|l| {
                    let l = l.trim_end_matches('.').replace(" here", "").replace("Files it named: ", "files it named ").replace("Config: ", "config ");
                    let mut c = l.chars();
                    c.next().map(|f| f.to_lowercase().collect::<String>() + c.as_str()).unwrap_or_default()
                })
                .collect();
            return vec![evidence("page_lead", &[("lines", parts.join("; ") + ".")])];
        }
        lines
    }

    /// The finding's notes on its counted runs' code and config (E5): code kept in part, not kept, not in this
    /// clone; a config changed since the run was recorded. (Code kept when it was recorded, after the run, is the
    /// finding's evidence line's to say, not a note on every finding.)
    pub fn run_caveats(&self, runs: &[String]) -> Vec<String> {
        use crate::capture::{Capture, Kept};
        use crate::shell::strings::evidence;
        let mut out = Vec::new();
        if !in_git(&self.evidence_root()) {
            return out;
        }
        for id in runs {
            let Ok(r) = self.load::<Run>(id) else { continue };
            let c = Capture::of_run(&r);
            if c.tree.as_deref().map_or(false, |t| crate::capture::tree_here(&self.evidence_root(), t) == crate::capture::Here::Absent) {
                if recorded_in(&self.root, &r.id) {
                    out.push(evidence("note_code_gone", &[("run", r.id.clone())]));
                } else {
                    out.push(evidence("note_code_away", &[("run", r.id.clone())]));
                }
            } else if c.kept == Kept::None {
                out.push(evidence("note_code_none", &[("run", r.id.clone()), ("why", c.none_reason())]));
            } else if c.kept == Kept::Part {
                out.push(evidence("note_code_part", &[("run", r.id.clone())]));
            }
            if let Some(cf) = r.receipt.config.as_ref().filter(|c| c.sha256.as_ref().map_or(false, |s| !s.is_empty())) {
                let now = sources::file_ref(&self.evidence_root(), &cf.path).ok().and_then(|f| f.get("sha256").map(pyjson::py_str));
                if now.as_deref() != cf.sha256.as_deref() {
                    out.push(evidence("note_config", &[("run", r.id.clone())]));
                }
            }
        }
        out
    }

    /// The finding's problems and notes for the files its counted runs named (E5).
    pub fn file_caveats(&self, runs: &[String]) -> (Vec<(String, String)>, Vec<(String, String)>) {
        let mut problems = Vec::new();
        let mut notes = Vec::new();
        // a record with no git (a bundle's copy, which holds no file a run named) says nothing of its runs' files: its
        // page is drawn from the checkout it was exported from
        if !in_git(&self.evidence_root()) {
            return (problems, notes);
        }
        for id in runs {
            let Ok(r) = self.load::<Run>(id) else { continue };
            for n in self.named_files(&r) {
                if let Some(said) = n.said(&r.id) {
                    if n.is_problem() { problems.push((r.id.clone(), said)) } else { notes.push((r.id.clone(), said)) }
                }
            }
        }
        (problems, notes)
    }
}

/// A finding's evidence line as a page says it (E5): led by "When this page was made:", as a run's lines are there, and
/// "not in this clone" said of the repository the page was made from.
pub fn finding_evidence_on_page(line: &str) -> String {
    let rest = line.strip_prefix("Evidence: ").unwrap_or(line).replace("not in this clone", "not in the repository this page was made from").replace("gone from this clone", "gone from the repository this page was made from").replace("not in this repository", "not in the repository this page was made from");
    crate::shell::strings::evidence("page_lead", &[("lines", rest)])
}

impl Ledger {
    /// A finding's evidence in one line (E5): how many runs, whether each one's files are unchanged and when its code
    /// was kept, or what is short of whole; and that rb has not rerun them. None where the reading counts no run.
    pub fn finding_evidence(&self, runs: &[String], taken: usize) -> Option<String> {
        use crate::capture::{Capture, Kept, When};
        use crate::shell::strings::evidence;
        if runs.is_empty() {
            return None;
        }
        let n = |k: usize, one: &str, many: &str| if k == 1 { format!("1 {one}") } else { format!("{k} {many}") };
        // a run's, or runs' (S5)
        let own = |k: usize| if k == 1 { "1 run's".to_string() } else { format!("{k} runs'") };
        let mut short: Vec<String> = Vec::new();
        let (mut start, mut after, mut given) = (0usize, 0usize, 0usize);
        let mut not_kept: Vec<String> = Vec::new();
        let (mut not_here, mut gone_here) = (0usize, 0usize);
        let (mut not_repo, mut part) = (0usize, 0usize);
        for id in runs {
            let Ok(r) = self.load::<Run>(id) else { continue };
            for f in self.named_files(&r).iter().filter(|f| f.now != FileNow::Unchanged) {
                short.push(if f.not_in_clone() { evidence("file_not_in_clone", &[("path", f.path.clone())]).trim_end_matches('.').to_string() } else { format!("{} {} since run {} was recorded", f.path, f.what(), r.id) });
            }
            let c = Capture::of_run(&r);
            let away = c.tree.as_deref().map_or(false, |t| crate::capture::tree_here(&self.evidence_root(), t) == crate::capture::Here::Absent);
            if crate::capture::given_absent(&self.evidence_root(), &r) {
                // a --commit or --tree this repository lacks is not kept here, whatever its receipt says (B2)
                not_repo += 1;
            } else if away && recorded_in(&self.root, &r.id) {
                // the clone that recorded it, where git gc removed the tree: gone, as the run's own lines say (S-c)
                gone_here += 1;
            } else if away {
                not_here += 1;
            } else if c.kept == Kept::Part {
                part += 1;
            } else if c.kept == Kept::Whole && (c.commit.is_some() || c.tree.is_some()) {
                match c.when {
                    When::Start => start += 1,
                    When::Given => given += 1,
                    When::After => after += 1,
                }
            } else if c.kept == Kept::Whole {
                // not in git, or no commit yet: there was nothing to keep the code at
                not_kept.push(evidence("why_no_commit", &[]));
            } else {
                not_kept.push(c.none_reason());
            }
        }
        if !not_kept.is_empty() {
            let mut why = not_kept.clone();
            why.sort();
            why.dedup();
            short.push(format!("{} code not kept ({})", own(not_kept.len()), why.join("; ")));
        }
        if part > 0 {
            short.push(format!("{} code kept in part", own(part)));
        }
        if not_repo > 0 {
            short.push(evidence("given_not_here", &[("runs", own(not_repo))]));
        }
        if not_here > 0 {
            short.push(format!("{} code not in this clone", own(not_here)));
        }
        if gone_here > 0 {
            short.push(evidence("gone_line", &[("runs", own(gone_here))]));
        }
        if taken > 0 {
            short.push(evidence("took_evidence", &[("runs", n(taken, "more run", "more runs"))]));
        }
        let total = n(runs.len(), "run", "runs");
        if !short.is_empty() {
            return Some(evidence("finding_short", &[("runs", total), ("short", short.join("; "))]));
        }
        let parts: Vec<(usize, &str)> = [(start, "from when {k} started"), (after, "when {k} was recorded, after the run"), (given, "as given for {k}")].into_iter().filter(|(k, _)| *k > 0).collect();
        let when = match parts.as_slice() {
            [(_, w)] => w.replace("{k} started", "it started").replace("{k} was", "it was").replace(" for {k}", ""),
            _ => parts
                .iter()
                .enumerate()
                .map(|(i, (k, w))| if i == 0 { w.replace("{k}", &k.to_string()) } else { format!("for {k}, {}", w.replace("{k} ", "it ").replace(" for {k}", "")) })
                .collect::<Vec<_>>()
                .join(" and, "),
        };
        Some(evidence("finding_whole", &[("runs", total), ("when", when)]))
    }
}
