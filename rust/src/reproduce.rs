//! Reproduce (BP 1c): the recipe of an experiment, or of one of its runs, from what the record holds and nothing else.
//! What it takes to run it again is rb's rerunnability, rows that say the same thing about inputs in one role read as
//! one; the steps come from each input's recorded recipe, a made input bringing what its maker read first; an input
//! with no recipe is a sentence, never a command. The code is a command only where a run's receipt holds the code
//! that made it (view::code_of's rule), and never `git checkout`; a commit the record names only for when a run was
//! recorded is not printed.
//! The command as given stays apart, as history. What a run made, or what a run with the same bytes made, ends the
//! recipe, named for that run alone. The recording step is a request in words, never `rb run add`. Nothing is written.
//! Track P; shell/model.rs puts `Recipes::experiment` on experiments[].reproduce and `Recipes::run` on runs{}.reproduce.

use crate::acquisition::{self, role_words};
use crate::errors::Res;
use crate::ledger::support::{self, Given};
use crate::ledger::{made_before_recorded, Ledger};
use crate::next_step;
use crate::pyjson;
use crate::record::{Experiment, Resource, Run, Setting, Source};
use crate::shell::tier::Tier;
use crate::sources;
use crate::value::{Dict, DictExt, Value};
use indexmap::IndexMap;
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::Path;
use std::rc::Rc;

/// The first line of every copied recipe after its title.
pub const FROM_THE_RECORD: &str = "# Recipe, from what the record holds. rb did not run these commands.";

/// The readiness states in the order the ready line counts them, each with its glyph.
const GLYPHS: [(&str, &str); 5] = [("ok", "✓"), ("partial", "◐"), ("changed", "◐"), ("missing", "✗"), ("unknown", "○")];

/// How deep the made-by edge is followed: a made input brings what its maker read, and so on, this many makers back.
const MAKERS_BACK: usize = 4;

/// The recipe of an experiment, as experiments[].reproduce holds it.
pub fn experiment(led: &Ledger, exp: &Experiment, tier: Tier) -> Res<Value> {
    Recipes::new(led, tier)?.experiment(exp)
}

/// The recipe of one run (Reproduce this run), as runs{}.reproduce holds it.
pub fn run(led: &Ledger, run: &Run, tier: Tier) -> Res<Value> {
    Recipes::new(led, tier)?.run(run)
}

/// What a rerun of `runs` would need and where each piece stands now, row for row as `Ledger::rerunnability` gives
/// an experiment's: its rule, over any set of runs.
pub fn rerunnability(led: &Ledger, runs: &[&Run]) -> Res<Vec<Dict>> {
    Ok(Recipes::new(led, Tier::Live)?.readiness(runs)?.iter().map(Ready::dict).collect())
}

/// Whether two runs recorded the same bytes: a file of one, not empty, is a file of the other. The database a row is
/// read from is shared by every row of it, so it is no run's own bytes; the other files of a run read from a row (a
/// checkpoint it kept with --artifact) are.
pub fn same_bytes(a: &Run, b: &Run) -> bool {
    if a.id == b.id {
        return false;
    }
    let own = |r: &Run| -> Vec<String> {
        let row = r.receipt.row.as_ref();
        r.files
            .iter()
            .filter(|f| f.bytes > 0 && !f.sha256.is_empty())
            .filter(|f| row.map_or(true, |w| f.path != w.path && (w.sha256.is_empty() || f.sha256 != w.sha256)))
            .map(|f| f.sha256.clone())
            .collect()
    };
    let theirs = own(b);
    own(a).iter().any(|h| theirs.contains(h))
}

/// A run named by its arms and its per-run values ("fp32 and int8 · seed 1 · one cycle 1"), or the row it was read
/// from when it has none; never by its experiment's title.
pub fn run_label(led: &Ledger, exp: Option<&Experiment>, r: &Run) -> String {
    let mut parts: Vec<String> = Vec::new();
    let arms = arms_of(exp, r);
    if !arms.is_empty() {
        parts.push(next_step::and_list(&arms));
    }
    let keys: Vec<&str> = r.per_run.keys().map(String::as_str).collect();
    parts.extend(r.per_run.iter().map(|(k, v)| format!("{} {}", one_line(&key_words(k, &keys)), one_line(&crate::ledger::show(v)))));
    if r.per_run.is_empty() {
        if let Some(row) = &r.receipt.row {
            let at = row.rowid.map(|n| format!(" row {n}")).unwrap_or_default();
            parts.push(format!("{}{at}", crate::view::named_file(led, &row.path, Some(r))));
        }
    }
    parts.join(" · ")
}

/// What tells each run apart from the others of its experiment whose label reads the same: when rb recorded it, to the
/// minute in UTC, and where that is the same too, its place among them in record order ("recorded 2026-10-26 14:51 UTC,
/// the 2nd of 3 that minute"). A run whose label is its own gets none: two runs never read alike, and no minted id says it.
fn apart(led: &Ledger, experiments: &IndexMap<String, Experiment>, runs: &[Run]) -> HashMap<String, String> {
    let mut alike: IndexMap<(String, String), Vec<&Run>> = IndexMap::new();
    for r in runs {
        alike.entry((r.experiment.clone(), run_label(led, experiments.get(&r.experiment), r))).or_default().push(r);
    }
    let minute = |r: &Run| {
        let at = if r.receipt.at.is_empty() { &r.created_at } else { &r.receipt.at };
        match crate::pytime::parse_iso(at) {
            Some(t) => t.with_timezone(&chrono::Utc).format("%Y-%m-%d %H:%M UTC").to_string(),
            None => at.chars().take(16).collect::<String>().replace('T', " "),
        }
    };
    let mut out = HashMap::new();
    for (_, mut rs) in alike.into_iter().filter(|(_, rs)| rs.len() > 1) {
        rs.sort_by(|a, b| (&a.created_at, &a.id).cmp(&(&b.created_at, &b.id)));
        let mut by_minute: IndexMap<String, Vec<&Run>> = IndexMap::new();
        for r in rs {
            by_minute.entry(minute(r)).or_default().push(r);
        }
        for (m, same) in by_minute {
            let n = same.len();
            for (i, r) in same.iter().enumerate() {
                let place = if n > 1 { format!(", the {} of {n} that minute", ordinal(i + 1)) } else { String::new() };
                out.insert(r.id.clone(), format!(" · recorded {m}{place}"));
            }
        }
    }
    out
}

/// "1st", "2nd", "3rd", "4th", "11th", "21st".
fn ordinal(n: usize) -> String {
    let suffix = match (n % 10, n % 100) {
        (_, 11..=13) => "th",
        (1, _) => "st",
        (2, _) => "nd",
        (3, _) => "rd",
        _ => "th",
    };
    format!("{n}{suffix}")
}

/// A per-run setting's name in words, as Runs says it ("one cycle" for one_cycle): its last name with its underscores
/// as spaces, or its whole name with its dots as spaces too where another of `keys` has that last name.
fn key_words(k: &str, keys: &[&str]) -> String {
    let last = |x: &str| x.rsplit('.').next().filter(|t| !t.is_empty()).unwrap_or(x).to_string();
    let words = if keys.iter().any(|o| *o != k && last(o) == last(k)) { k.to_string() } else { last(k) };
    words.replace(['.', '_'], " ").to_lowercase()
}

/// The arms a run measured: the experiment's variants its numbers name (`int8.top1`), in the experiment's order.
fn arms_of(exp: Option<&Experiment>, r: &Run) -> Vec<String> {
    let Some(exp) = exp else { return Vec::new() };
    let named: HashSet<&str> = r.metrics.keys().filter_map(|k| k.split_once('.').map(|(h, _)| h)).collect();
    let mut out: Vec<String> = Vec::new();
    for v in &exp.variants {
        if named.contains(v.name.as_str()) && !out.contains(&v.name) {
            out.push(v.name.clone());
        }
    }
    out
}

/// Text on one line with every character a screen would hide or act on written as its escape: a comment in a copied
/// script can never start a line of its own.
fn one_line(text: &str) -> String {
    crate::ledger::design::visible(text)
}

/// A setting's source in short: a file with its key or line, a run's file and pointer, an address, or a note.
fn cite(s: &Source) -> String {
    match s.kind.as_str() {
        "file" => {
            let path = s.path.clone().unwrap_or_default();
            match (s.key.as_deref().filter(|k| !k.is_empty()), s.line.filter(|l| *l != 0)) {
                (Some(k), _) => format!("{path}#{k}"),
                (None, Some(l)) => format!("{path}:{l}"),
                _ => path,
            }
        }
        "run" => format!("{}#{}", s.path.as_deref().unwrap_or(""), s.pointer.as_deref().unwrap_or("")),
        "url" => s.url.clone().unwrap_or_default(),
        _ => "note".into(),
    }
}

fn standing(r: &Run) -> bool {
    r.retracted.as_ref().map_or(true, |x| !x.scope.is_empty())
}

/// A text field of a receipt's part, when it holds one.
fn field(d: &Dict, k: &str) -> Option<String> {
    d.get(k).and_then(Value::as_str).filter(|v| !v.is_empty()).map(str::to_string)
}

/// The code a run ran: the commit, and the git tree of its files as they were then (the commit itself where it had no
/// uncommitted changes). view::code_of's RunCode.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Code {
    commit: String,
    tree: String,
}

/// The checkout a run was recorded in, as its receipt keeps it. None where it names no commit, or had uncommitted
/// changes it kept no tree of, or left a file out of that tree.
fn recorded_code(r: &Run) -> Option<Code> {
    let at = r.receipt.attached.as_ref()?;
    let commit = field(at, "commit")?;
    if !at.get("dirty").map_or(false, Value::truthy) {
        return Some(Code { tree: commit.clone(), commit });
    }
    if at.get("left_out").map_or(false, Value::truthy) {
        return None;
    }
    Some(Code { tree: field(at, "tree")?, commit })
}

/// The code a run says it was made at (--commit, and --tree for its uncommitted changes, as rb journal run's proposal
/// gives them). None where it names no commit.
fn made_code(r: &Run) -> Option<Code> {
    let p = &r.receipt.produced;
    field(p, "commit").map(|c| Code { tree: field(p, "tree").unwrap_or_else(|| c.clone()), commit: c })
}

/// One row of what it takes to run it again, as rerunnability gives it (`what`, `state`, `detail`, `resource`), with a
/// resource row's parts apart: its availability word and what else it says, its recipe left to the steps.
#[derive(Debug, Clone)]
struct Ready {
    what: String,
    state: String,
    detail: String,
    resource: Option<String>,
    avail: String,
    also: Vec<String>,
}

impl Ready {
    fn plain(what: &str, state: &str, detail: String) -> Ready {
        Ready { what: what.into(), state: state.into(), detail, resource: None, avail: String::new(), also: Vec::new() }
    }

    /// As `Ledger::rerunnability` gives the row.
    fn dict(&self) -> Dict {
        let mut d = crate::dict! {"what" => self.what.clone(), "state" => self.state.clone()};
        if let Some(r) = &self.resource {
            d.insert("resource".into(), Value::Str(r.clone()));
        }
        d.insert("detail".into(), Value::Str(self.detail.clone()));
        d
    }
}

/// A step of the recipe before it is numbered.
#[derive(Debug, Clone, Default)]
struct Step {
    what: String,
    label: String,
    refs: Vec<String>,
    command: String,
    from: String,
    runnable: bool,
}

impl Step {
    fn value(&self, n: usize) -> Value {
        crate::obj! {
            "n" => n, "what" => self.what.clone(), "label" => self.label.clone(), "refs" => Value::list_of_str(self.refs.clone()),
            "command" => self.command.clone(), "from" => self.from.clone(), "runnable" => self.runnable
        }
    }

    /// The step as the copied script gives it: a comment saying what it is and where it comes from, then its command,
    /// when it has one to run.
    fn script(&self) -> Vec<String> {
        let mut out = vec![format!("# {}", one_line(&if self.from.is_empty() { self.label.clone() } else { format!("{} · {}", self.label, self.from) }))];
        if self.runnable {
            out.push(self.command.clone());
        }
        out
    }
}

/// What a run made, or what a run with the same bytes made: the recipe of that resource, named for the run.
#[derive(Debug, Clone)]
struct Made {
    run: String,
    maker: String,
    resource: String,
    command: String,
    from: String,
    line: String,
    head: String,
}

/// The recipes of one record for one tier. What every recipe reads (the record's runs, its resources and where each
/// stands now, the settings' standing) is read once, whichever experiments and runs are asked for. A recipe reads the
/// same on both tiers: what only the user's own screens say (a place's path) comes through the view's reader.
pub struct Recipes<'a> {
    led: &'a Ledger,
    runs: Rc<Vec<Run>>,
    by_id: HashMap<String, usize>,
    experiments: IndexMap<String, Experiment>,
    resources: IndexMap<String, Resource>,
    avail: RefCell<HashMap<String, Dict>>,
    statuses: RefCell<HashMap<(String, String), String>>,
    /// each resource's makers: the standing runs that recorded it as an output, in record order
    makers: IndexMap<String, Vec<String>>,
    /// what git and the files answer, asked once per document (rb open builds one on every request): a code row by
    /// the code it is asked of, a run's produced code, a commit in full, a file's state, a copy's hash
    code_rows: RefCell<HashMap<String, Dict>>,
    produced: RefCell<HashMap<String, Dict>>,
    commits: RefCell<HashMap<String, Option<String>>>,
    file_states: RefCell<HashMap<(String, String), &'static str>>,
    hashes: RefCell<HashMap<std::path::PathBuf, Option<String>>>,
    /// a commit's or a tree's tree in full, and each file a commit or tree holds
    trees: RefCell<HashMap<String, Option<String>>>,
    listings: RefCell<HashMap<String, Option<Rc<BTreeMap<String, String>>>>>,
    /// rb status's open items, when the caller hands them (`with_open`): the request asks for the run rb suggests
    open: Vec<Value>,
    /// what tells a run apart from another of its experiment whose label reads the same (" · recorded <minute>", with
    /// its place in record order where the minute is the same too), by run id; none for a run whose label is its own
    apart: HashMap<String, String>,
}

impl<'a> Recipes<'a> {
    pub fn new(led: &'a Ledger, _tier: Tier) -> Res<Recipes<'a>> {
        let runs = led.all::<Run>()?;
        let mut makers: IndexMap<String, Vec<String>> = IndexMap::new();
        for r in runs.iter().filter(|r| standing(r)) {
            for u in &r.outputs {
                makers.entry(u.resource.clone()).or_default().push(r.id.clone());
            }
        }
        let experiments: IndexMap<String, Experiment> = led.all::<Experiment>()?.iter().map(|e| (e.id.clone(), e.clone())).collect();
        let apart = apart(led, &experiments, &runs);
        Ok(Recipes {
            led,
            experiments,
            resources: led.all::<Resource>()?.iter().map(|r| (r.id.clone(), r.clone())).collect(),
            by_id: runs.iter().enumerate().map(|(i, r)| (r.id.clone(), i)).collect(),
            runs,
            avail: RefCell::new(HashMap::new()),
            statuses: RefCell::new(HashMap::new()),
            makers,
            code_rows: RefCell::new(HashMap::new()),
            produced: RefCell::new(HashMap::new()),
            commits: RefCell::new(HashMap::new()),
            file_states: RefCell::new(HashMap::new()),
            hashes: RefCell::new(HashMap::new()),
            trees: RefCell::new(HashMap::new()),
            listings: RefCell::new(HashMap::new()),
            open: Vec::new(),
            apart,
        })
    }

    /// rb status's open items (its `open`), so each experiment's request asks for the run rb suggests for it, as the
    /// question's Next step does.
    pub fn with_open(mut self, open: &[Value]) -> Self {
        self.open = open.to_vec();
        self
    }

    /// experiments[].reproduce of the experiment `id`.
    pub fn experiment_of(&self, id: &str) -> Res<Value> {
        match self.experiments.get(id) {
            Some(e) => self.experiment(e),
            None => self.experiment(&self.led.load::<Experiment>(id)?),
        }
    }

    /// runs{}.reproduce of the run `id`.
    pub fn run_of(&self, id: &str) -> Res<Value> {
        match self.run_by(id) {
            Some(r) => self.run(r),
            None => self.run(&self.led.load::<Run>(id)?),
        }
    }

    fn code_row(&self, code: &[Dict]) -> Dict {
        let key = pyjson::dumps(&Value::List(code.iter().cloned().map(Value::Dict).collect()));
        if let Some(d) = self.code_rows.borrow().get(&key) {
            return d.clone();
        }
        let d = self.led.code_row(code);
        self.code_rows.borrow_mut().insert(key, d.clone());
        d
    }

    /// A commit as the repository names it in full, when the repository has it.
    fn full_commit(&self, c: &str) -> Option<String> {
        if let Some(hit) = self.commits.borrow().get(c) {
            return hit.clone();
        }
        let full = sources::git(&self.led.root, &["rev-parse", "--verify", "-q", &format!("{c}^{{commit}}")]).map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        self.commits.borrow_mut().insert(c.to_string(), full.clone());
        full
    }

    fn file_state(&self, f: &crate::record::FileRef) -> &'static str {
        let key = (f.path.clone(), f.sha256.clone());
        if let Some(s) = self.file_states.borrow().get(&key) {
            return s;
        }
        let s = self.led.file_state(f);
        self.file_states.borrow_mut().insert(key, s);
        s
    }

    fn hash(&self, p: &std::path::Path) -> Option<String> {
        if let Some(h) = self.hashes.borrow().get(p) {
            return h.clone();
        }
        let h = self.led.hash(p);
        self.hashes.borrow_mut().insert(p.to_path_buf(), h.clone());
        h
    }

    /// The tree a commit or a tree names, in full, when this repository has it.
    fn tree_id(&self, x: &str) -> Option<String> {
        if let Some(hit) = self.trees.borrow().get(x) {
            return hit.clone();
        }
        let t = sources::git(&self.led.root, &["rev-parse", "--verify", "-q", &format!("{x}^{{tree}}")]).map(|s| s.trim().to_lowercase()).filter(|s| !s.is_empty());
        self.trees.borrow_mut().insert(x.to_string(), t.clone());
        t
    }

    /// Each file a commit or a tree holds, by its path from the top, with its mode and object. None where this clone
    /// does not have it.
    fn tree_files(&self, top: &Path, treeish: &str) -> Option<Rc<BTreeMap<String, String>>> {
        if let Some(hit) = self.listings.borrow().get(treeish) {
            return hit.clone();
        }
        let listed = sources::git_bytes(top, &["ls-tree", "-r", "-z", "--full-tree", &format!("{treeish}^{{tree}}")]).and_then(|bytes| {
            let mut files = BTreeMap::new();
            for entry in bytes.split(|b| *b == 0).filter(|e| !e.is_empty()) {
                let tab = entry.iter().position(|b| *b == b'\t')?;
                files.insert(String::from_utf8_lossy(&entry[tab + 1..]).into_owned(), String::from_utf8_lossy(&entry[..tab]).into_owned());
            }
            Some(Rc::new(files))
        });
        self.listings.borrow_mut().insert(treeish.to_string(), listed.clone());
        listed
    }

    /// The paths, from the top of a checkout, of the files a run names (what its numbers were read from, its
    /// artifacts, a row's database), as its own checkout holds them: what it read or wrote, not code.
    fn named_paths(&self, r: &Run) -> HashSet<String> {
        let root = &self.led.root;
        let top = sources::git_top(root);
        let own = r.receipt.attached.as_ref().and_then(|a| a.get("worktree")).and_then(|w| w.get("path")).and_then(Value::as_str).map(|p| sources::resolve_path(Path::new(p)));
        r.files
            .iter()
            .flat_map(|f| {
                let full = sources::resolve_path(&root.join(&f.path));
                [top.as_ref(), own.as_ref()].into_iter().flatten().filter_map(|base| sources::rel_to(&full, base)).collect::<Vec<_>>()
            })
            .collect()
    }

    /// What a run's code is, as a key that is the same for the same code: each file its commit holds, as it was when
    /// the run ran. The record's own folder and the files `named` are left out, and so is a file its commit does not
    /// hold, which may be one a run wrote. None where this clone does not have the commit or the tree.
    fn code_key(&self, c: &Code, named: &HashSet<String>) -> Option<String> {
        use sha2::{Digest, Sha256};
        let root = &self.led.root;
        let top = sources::git_top(root)?;
        let record = sources::rel_to(&sources::resolve_path(&root.join(crate::ledger::DIR)), &top)?;
        let base = self.tree_files(&top, &c.commit)?;
        let then = if c.tree == c.commit { base.clone() } else { self.tree_files(&top, &c.tree)? };
        let inside = format!("{record}/");
        let mut h = Sha256::new();
        for path in base.keys() {
            if *path == record || path.starts_with(&inside) || named.contains(path) {
                continue;
            }
            h.update(path.as_bytes());
            h.update(b"\0");
            h.update(then.get(path).map_or(&b"-"[..], |e| e.as_bytes()));
            h.update(b"\0");
        }
        Some(crate::rbyaml::hex(&h.finalize()))
    }

    /// Whether what a run says it was made at agrees with the checkout it was recorded in: it says nothing, or names
    /// that commit with the same code, the files the run names aside. A tree named with no commit is other code.
    fn agrees(&self, r: &Run, rec: &Code) -> bool {
        match made_code(r) {
            None => field(&r.receipt.produced, "tree").is_none(),
            Some(m) if m.commit != rec.commit => false,
            Some(m) => {
                let own = self.named_paths(r);
                matches!((self.code_key(&m, &own), self.code_key(rec, &own)), (Some(a), Some(b)) if a == b)
            }
        }
    }

    /// The code a run ran, where its receipt holds the code that made it (view::code_of's rule): what it says it was
    /// made at, where rb journal run watched its command make the numbers; otherwise the checkout it was recorded in,
    /// which a run made before it was recorded does not hold (it keeps the recording checkout's code), nor does a
    /// receipt that says it was made at other code than that checkout.
    fn held_code(&self, r: &Run) -> Option<Code> {
        if let Some(m) = made_code(r).filter(|_| support::run_command(r).map_or(false, |g| g.given == "observed")) {
            return Some(m);
        }
        if made_before_recorded(r) {
            return None;
        }
        let rec = recorded_code(r)?;
        self.agrees(r, &rec).then_some(rec)
    }

    /// Whether a run recorded as it was made names other code than the checkout it was recorded in as the code that
    /// made it (--commit, --tree): what a rerun needs is then that code, not the checkout's.
    fn names_other_code(&self, r: &Run) -> bool {
        let p = &r.receipt.produced;
        if field(p, "commit").is_none() && field(p, "tree").is_none() {
            return false;
        }
        match recorded_code(r) {
            Some(rec) => !self.agrees(r, &rec),
            // a checkout whose changes the receipt did not keep: other code only where it names another commit
            None => made_code(r).map(|m| m.commit) != r.receipt.attached.as_ref().and_then(|a| field(a, "commit")),
        }
    }

    fn run_by(&self, id: &str) -> Option<&Run> {
        self.by_id.get(id).map(|i| &self.runs[*i])
    }

    fn availability(&self, res: &Resource) -> Dict {
        if let Some(a) = self.avail.borrow().get(&res.id) {
            return a.clone();
        }
        let a = self.led.availability(res);
        self.avail.borrow_mut().insert(res.id.clone(), a.clone());
        a
    }

    fn label_of(&self, r: &Run) -> String {
        run_label(self.led, self.experiments.get(&r.experiment), r)
    }

    /// A run as a recipe names it: by its label, never by its id, which stays in `refs` and the data; "a run of e1"
    /// where it has none. `exp` adds its experiment after a label ("seed 1 (e1)"), for a run of another experiment.
    fn said(&self, r: &Run, exp: bool) -> String {
        let apart = self.apart.get(&r.id).map_or("", String::as_str);
        match self.label_of(r) {
            l if l.is_empty() => format!("a run of {}{apart}", r.experiment),
            l if exp => format!("{l}{apart} ({})", r.experiment),
            l => format!("{l}{apart}"),
        }
    }

    /// A run named by its id, as `said` names it, with its experiment where none of `runs` (the recipe's) is of it;
    /// the id itself where the record holds no such run.
    fn said_id(&self, id: &str, runs: &[&Run]) -> String {
        self.run_by(id).map_or_else(|| id.to_string(), |r| self.said(r, !runs.iter().any(|x| x.experiment == r.experiment)))
    }

    /// experiments[].reproduce: the recipe of the experiment's standing runs.
    pub fn experiment(&self, exp: &Experiment) -> Res<Value> {
        let runs: Vec<&Run> = self.runs.iter().filter(|r| r.experiment == exp.id && standing(r)).collect();
        self.recipe(exp, &runs, None)
    }

    /// runs{}.reproduce: the experiment's recipe narrowed to one run, a retracted one too, its same-bytes recipe last.
    pub fn run(&self, r: &Run) -> Res<Value> {
        match self.experiments.get(&r.experiment) {
            Some(e) => self.recipe(e, &[r], Some(r)),
            None => self.recipe(&self.led.load::<Experiment>(&r.experiment)?, &[r], Some(r)),
        }
    }

    fn recipe(&self, exp: &Experiment, runs: &[&Run], one: Option<&Run>) -> Res<Value> {
        let rows = self.readiness(runs)?;
        let ready = merged(&rows);
        let mut steps: Vec<Step> = Vec::new();
        if !runs.is_empty() {
            steps.extend(self.code_steps(exp, runs, &rows));
        }
        steps.extend(self.input_steps(runs));
        let given: Vec<(&Run, Given)> = runs.iter().filter_map(|r| support::run_command(r).map(|g| (*r, g))).collect();
        let as_given = as_given_line(runs, &given, one);
        if !runs.is_empty() {
            steps.push(self.command_step(runs, &given, one));
        }
        let (per_run, per_run_lines) = self.per_run(exp, runs, one);
        let (settings, settings_lines) = self.settings(exp, one)?;
        let made = self.made(runs, one);
        let commands: Vec<Value> = given.iter().map(|(r, g)| crate::obj! {"run" => r.id.clone(), "command" => g.text.clone(), "observed" => g.given == "observed"}).collect();

        let mut script: Vec<String> = Vec::new();
        script.push(match one {
            Some(r) => match self.label_of(r).is_empty() {
                true => format!("# Reproduce {}", one_line(&self.said(r, false))),
                false => format!("# Reproduce a run of {} · {}", one_line(&exp.id), one_line(&self.said(r, false))),
            },
            None => format!("# Reproduce {}: {}", one_line(&exp.id), one_line(&exp.title)),
        });
        script.push(FROM_THE_RECORD.into());
        for (label, text, _, _) in &ready {
            script.push(format!("# {}: {}", one_line(label), one_line(text)));
        }
        let last = steps.len().saturating_sub(if runs.is_empty() { 0 } else { 1 });
        // a code step with nothing to run says what its ready row above says
        for s in steps[..last].iter().filter(|s| s.runnable || s.what != "code") {
            script.push(String::new());
            script.extend(s.script());
        }
        script.push(String::new());
        script.extend(per_run_lines);
        script.extend(settings_lines);
        if let Some(s) = steps.get(last) {
            script.extend(s.script());
        }
        // each run's command as given, by kind: history, never a line to run
        if one.is_none() {
            for (r, g) in &given {
                script.push(format!("#   {}: {}", one_line(&self.said(r, false)), one_line(&g.line())));
            }
        }
        for m in &made {
            script.push(String::new());
            script.push(format!("# {}", one_line(&m.head)));
            if m.command.is_empty() {
                script.push("# how to get it again: no recipe recorded".into());
                continue;
            }
            script.push(format!("# how to get it again{}", if m.from.is_empty() { String::new() } else { format!(" · {}", one_line(&m.from)) }));
            script.push(m.command.clone());
        }
        while script.last().map_or(false, |l| l.is_empty()) {
            script.pop();
        }
        let mut text = script.join("\n");
        text.push('\n');

        let ready_values: Vec<Value> = ready
            .iter()
            .map(|(label, text, state, refs)| crate::obj! {"state" => state.clone(), "label" => label.clone(), "text" => text.clone(), "refs" => Value::list_of_str(refs.clone())})
            .collect();
        Ok(crate::obj! {
            "ready_line" => ready_line(&ready),
            "ready" => Value::List(ready_values),
            "steps" => Value::List(steps.iter().enumerate().map(|(i, s)| s.value(i + 1)).collect()),
            "per_run" => per_run,
            "settings" => settings,
            "command_as_given" => crate::obj! {"recorded" => given.len(), "runs" => runs.len(), "line" => as_given},
            "made" => Value::List(made.iter().map(|m| crate::obj! {
                "run" => m.run.clone(), "maker" => m.maker.clone(), "resource" => m.resource.clone(), "command" => m.command.clone(),
                "from" => m.from.clone(), "line" => m.line.clone()
            }).collect()),
            "script" => text,
            "commands" => Value::List(commands),
            "prompt" => if one.is_some() { String::new() } else { self.prompt(exp) }
        })
    }

    /// The code a run made before it was recorded names (--commit, --tree), `dirty` when its tree is not its commit's:
    /// it holds the uncommitted changes the run was made with. A tree named in full is asked after as code_row asks
    /// after it, so git answers once.
    fn produced_code(&self, r: &Run) -> Dict {
        if let Some(d) = self.produced.borrow().get(&r.id) {
            return d.clone();
        }
        let d = self.produced_code_now(r);
        self.produced.borrow_mut().insert(r.id.clone(), d.clone());
        d
    }

    fn produced_code_now(&self, r: &Run) -> Dict {
        let root = &self.led.root;
        let tree_of = |x: &str| sources::git(root, &["rev-parse", "--verify", "-q", &format!("{x}^{{tree}}")]).map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        let mut d: Dict = ["commit", "tree"].iter().filter_map(|k| r.receipt.produced.get(*k).filter(|v| v.truthy()).map(|v| (k.to_string(), v.clone()))).collect();
        let here = |t: String| -> Option<String> {
            if (t.len() == 40 || t.len() == 64) && t.bytes().all(|b| b.is_ascii_hexdigit()) {
                sources::git(root, &["cat-file", "-e", &format!("{t}^{{tree}}")]).map(|_| t.to_lowercase())
            } else {
                tree_of(&t)
            }
        };
        if let Some(tree) = d.get("tree").map(pyjson::py_str).and_then(here) {
            let commit = d.get("commit").map(pyjson::py_str);
            if commit.as_deref() != Some(tree.as_str()) && commit.and_then(|c| tree_of(&c)).as_deref() != Some(tree.as_str()) {
                d.insert("dirty".into(), Value::Bool(true));
            }
        }
        d
    }

    /// What a rerun of `runs` would need and where each piece stands now: `Ledger::rerunnability`'s rule over any set
    /// of runs (one run's, for Reproduce this run), row for row as it gives an experiment's.
    fn readiness(&self, runs: &[&Run]) -> Res<Vec<Ready>> {
        let led = self.led;
        let mut rows: Vec<Ready> = Vec::new();
        if runs.is_empty() {
            return Ok(rows);
        }
        // a run made before it was recorded has the recorder's code and machine on its receipt: its code is the
        // commit it names (--commit, --tree), unknown without one, and its environment is unknown; so is the code of a
        // run whose receipt names other code than the checkout it was recorded in
        let named = |r: &Run| self.produced_code(r);
        let before: Vec<bool> = runs.iter().map(|r| made_before_recorded(r)).collect();
        let code: Vec<Dict> = runs
            .iter()
            .zip(&before)
            .filter_map(|(r, b)| {
                if *b || self.names_other_code(r) {
                    Some(named(r)).filter(|c| !c.is_empty())
                } else {
                    Some(r.receipt.attached.clone().unwrap_or_default())
                }
            })
            .collect();
        let envs: Vec<&Dict> = runs.iter().zip(&before).filter(|(r, b)| !**b && !r.receipt.environment.is_empty()).map(|(r, _)| &r.receipt.environment).collect();
        let n = runs.len();
        // one run is the run, two are both, more are all of them (Ledger::rerunnability's words)
        let said = |k: usize, one: &str, all: &str, both: &str, some_one: &str, some: &str| match (k == n, k == 1) {
            (true, true) => one.to_string(),
            (true, false) if n == 2 => both.to_string(),
            (true, false) => all.to_string(),
            (false, true) if n == 2 => format!("one of the two runs {some_one}"),
            (false, true) => format!("1 of {n} runs {some_one}"),
            (false, false) => format!("{k} of {n} runs {some}"),
        };
        let unnamed = n - code.len();
        let code_why = said(
            unnamed,
            "the run was made before it was recorded, and names no commit",
            "the runs were made before they were recorded, and none names its commit",
            "both runs were made before they were recorded, and neither names its commit",
            "was made before it was recorded and names no commit",
            "were made before they were recorded and name no commit",
        );
        let earlier = before.iter().filter(|b| **b).count();
        let env_why = said(
            earlier,
            "the run was made before it was recorded, in an environment rb did not see",
            "the runs were made before they were recorded, in environments rb did not see",
            "both runs were made before they were recorded, in environments rb did not see",
            "was made before it was recorded, in an environment rb did not see",
            "were made before they were recorded, in environments rb did not see",
        );
        let partly = |mut row: Ready, k: usize, why: &str| {
            if k > 0 {
                if row.state == "ok" {
                    row.state = "partial".into();
                }
                row.detail = format!("{} · {why}", row.detail);
            }
            row
        };
        let as_ready = |d: Dict| Ready::plain(d.get_str("what").unwrap_or(""), d.get_str("state").unwrap_or(""), d.get_str("detail").unwrap_or("").to_string());
        if code.is_empty() {
            rows.push(Ready::plain("code", "unknown", code_why.clone()));
        } else {
            rows.push(partly(as_ready(self.code_row(&code)), unnamed, &code_why));
        }
        if let Some(last) = envs.last() {
            let keys: Vec<&str> = ["python", "torch", "numpy", "jax", "tensorflow", "cuda"].into_iter().filter(|k| last.contains_key(*k)).collect();
            let d = keys.iter().map(|k| format!("{k} {}", pyjson::py_str(&last[*k]))).collect::<Vec<_>>().join(" · ");
            rows.push(partly(Ready::plain("environment", "ok", if d.is_empty() { "fingerprint recorded".to_string() } else { d }), earlier, &env_why));
        } else if earlier > 0 {
            rows.push(Ready::plain("environment", "unknown", env_why.clone()));
        }
        let mut hosts: Vec<String> = runs
            .iter()
            .filter(|r| !r.host.is_empty())
            .map(|r| {
                let mut kv: Vec<(&String, &String)> = r.host.iter().collect();
                kv.sort();
                kv.iter().map(|(k, v)| format!("{k}={v}")).collect::<Vec<_>>().join(", ")
            })
            .collect();
        hosts.sort();
        hosts.dedup();
        if !hosts.is_empty() {
            rows.push(Ready::plain("machine", "ok", crate::assess::first_three(&hosts, "; ")));
        }
        let mut seen: IndexMap<String, Vec<&crate::record::ResourceUse>> = IndexMap::new();
        for r in runs {
            for u in &r.inputs {
                seen.entry(u.resource.clone()).or_default().push(u);
            }
        }
        for (rid, uses) in &seen {
            let role = uses.iter().find(|u| !u.role.is_empty()).map(|u| u.role.clone()).unwrap_or_default();
            let Some(res) = self.resources.get(rid) else {
                let mut row = Ready::plain(if role.is_empty() { "resource" } else { &role }, "missing", format!("{rid}: no such resource record"));
                row.resource = Some(rid.clone());
                row.avail = "no such resource record".into();
                rows.push(row);
                continue;
            };
            let av = self.availability(res);
            let avs = av.get_str("state").unwrap_or("").to_string();
            let mut state = match avs.as_str() {
                "present" => "ok",
                "changed" => "partial",
                "remote" => "ok",
                "reconstructible" => "partial",
                _ => "missing",
            };
            let label = format!("{}{}", res.name, if res.version.is_empty() { String::new() } else { format!(" {}", res.version) });
            let how = av.get_str("how").unwrap_or("");
            let mut detail = format!("{label}: {avs}{}", if how.is_empty() { String::new() } else { format!(" ({how})") });
            let mut word = avs.clone();
            let mut also: Vec<String> = Vec::new();
            if let Some(rt) = &res.retracted {
                state = "missing";
                detail = format!("{label}: retracted ({})", rt.why);
                word = format!("retracted ({})", rt.why);
            }
            let read: BTreeSet<String> = uses.iter().filter_map(|u| u.sha256.clone().filter(|s| !s.is_empty())).collect();
            let here = av.get("locations").and_then(|l| l.as_list()).and_then(|l| {
                l.iter().find(|x| x.get_str("provider") == Some("local") && x.get_str("state") != Some("gone")).map(|x| led.local(x.get_str("ref").unwrap_or("")))
            });
            if let Some((copy, ns)) = here.as_ref().and_then(|p| self.hash(p).map(|h| (p, h))) {
                if !read.is_empty() && !read.contains(&ns) && !read.iter().any(|r| sources::file_is_twin_of(copy, r)) {
                    state = "partial";
                    let mut shorts: Vec<String> = read.iter().map(|x| x.chars().take(12).collect()).collect();
                    shorts.sort();
                    let not = format!("the copy here is not the bytes the runs read ({})", shorts.join(", "));
                    detail.push_str(&format!(" · {not}"));
                    also.push(not);
                }
            }
            if av.get("identity").and_then(|i| i.get_str("level")) == Some("name") {
                detail.push_str(" · identity: name only");
                also.push("identity: name only".into());
            }
            rows.push(Ready {
                what: if role.is_empty() { res.r#type.clone() } else { role },
                state: state.into(),
                detail,
                resource: Some(res.id.clone()),
                avail: word,
                also,
            });
        }
        let mut files: IndexMap<String, &crate::record::FileRef> = IndexMap::new();
        let mut named_by: HashMap<String, &Run> = HashMap::new();
        for r in runs {
            for f in &r.files {
                files.entry(f.path.clone()).or_insert(f);
                named_by.entry(f.path.clone()).or_insert(r);
            }
        }
        if !files.is_empty() {
            let states: Vec<(&String, &str)> = files.iter().map(|(p, f)| (p, self.file_state(f))).collect();
            // a file that is here with other bytes is changed, never missing
            let of = |want: &dyn Fn(&str) -> bool| -> Vec<&String> { states.iter().filter(|(_, s)| want(s)).map(|(p, _)| *p).collect() };
            let (gone, changed) = (of(&|s| s == "gone"), of(&|s| s != "gone" && s != "here"));
            let here = states.len() - gone.len() - changed.len();
            let state = if gone.is_empty() && changed.is_empty() {
                "ok"
            } else if here > 0 {
                "partial"
            } else if gone.is_empty() {
                "changed"
            } else {
                "missing"
            };
            let mut detail = format!("{} the runs named {} here unchanged", crate::assess::k_of_n(here, states.len(), "file", "files"), if here <= 1 { "is" } else { "are" });
            let named = |ps: &[&String]| -> Vec<String> { ps.iter().map(|s| crate::view::named_file(led, s, named_by.get(*s).copied())).collect() };
            if !changed.is_empty() {
                detail.push_str(&format!("; changed since recorded: {}", crate::assess::first_three(&named(&changed), ", ")));
            }
            if !gone.is_empty() {
                detail.push_str(&format!("; not here: {}", crate::assess::first_three(&named(&gone), ", ")));
            }
            rows.push(Ready::plain("files", state, detail));
        }
        Ok(rows)
    }

    /// The code a rerun starts from. A worktree at the commit is a command only when every run's receipt holds the
    /// code that made it (view::code_of's rule), in this repository; where that code had uncommitted changes rb kept
    /// as a git tree, the worktree takes that tree. Otherwise the step says what the record knows, in rb's words, with
    /// nothing to run.
    fn code_steps(&self, exp: &Experiment, runs: &[&Run], rows: &[Ready]) -> Vec<Step> {
        // (the commit in full, the tree of its uncommitted changes where it had any)
        let held = |r: &Run| -> Option<(String, Option<String>)> {
            let code = self.held_code(r)?;
            let commit = self.full_commit(&code.commit)?;
            if code.tree == code.commit {
                return Some((commit, None));
            }
            let tree = self.tree_id(&code.tree)?;
            Some((commit.clone(), Some(tree).filter(|t| self.tree_id(&commit).as_ref() != Some(t))))
        };
        let mut codes: IndexMap<(String, Option<String>), Vec<String>> = IndexMap::new();
        let mut all = true;
        for r in runs {
            match held(r) {
                Some(c) => codes.entry(c).or_default().push(r.id.clone()),
                None => all = false,
            }
        }
        if all && (1..=3).contains(&codes.len()) {
            return codes
                .into_iter()
                .map(|((c, tree), ids)| {
                    let short = |x: &str, n: usize| x.chars().take(n).collect::<String>();
                    // the runs by their labels, never their ids (which stay in refs); several by count
                    let who = match ids.len() {
                        1 => self.said_id(&ids[0], runs),
                        n if n == runs.len() => format!("all {n} runs"),
                        n => format!("{n} of the runs"),
                    };
                    let (label, command) = match &tree {
                        None => {
                            let dir = format!("../{}-{}", exp.id, short(&c, 7));
                            (format!("code · commit {}, which {who} ran at", short(&c, 10)), format!("git worktree add --detach {dir} {c}"))
                        }
                        Some(t) => {
                            let dir = format!("../{}-{}-{}", exp.id, short(&c, 7), short(t, 7));
                            (
                                format!("code · commit {} with the uncommitted changes rb kept as git tree {}, which {who} ran at", short(&c, 10), short(t, 10)),
                                format!("git worktree add --detach {dir} {c}\ngit -C {dir} read-tree -u --reset {t}"),
                            )
                        }
                    };
                    Step { what: "code".into(), label, refs: ids, command, from: String::new(), runnable: true }
                })
                .collect();
        }
        let row = rows.iter().find(|r| r.what == "code" && r.resource.is_none());
        let label = row.map_or_else(|| "code: unknown".to_string(), |r| format!("code · {}: {}", r.state, r.detail));
        vec![Step { what: "code".into(), label, ..Default::default() }]
    }

    /// The inputs the runs read, in the made-by order: an input a run recorded as its output brings what that run read
    /// to make it first (all it read but its evaluation data). Inputs in one role got by one recorded command read as
    /// one step naming each.
    fn input_steps(&self, runs: &[&Run]) -> Vec<Step> {
        // (role, resource, the run that made it when it brings what it read, why it is here, whether the walk stopped
        // at it with its maker's inputs not followed)
        type Item = (String, String, Option<String>, Option<(String, String)>, bool);
        let mut items: Vec<Item> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        let ours: HashSet<&str> = runs.iter().map(|r| r.id.as_str()).collect();
        fn walk(me: &Recipes<'_>, role: &str, rid: &str, read_for: Option<(String, String)>, depth: usize, ours: &HashSet<&str>, seen: &mut HashSet<String>, items: &mut Vec<Item>) {
            if !seen.insert(rid.to_string()) {
                return;
            }
            let maker = me.makers.get(rid).and_then(|ms| ms.iter().find(|m| !ours.contains(m.as_str())).cloned());
            let mut cut = false;
            if let Some(mr) = maker.as_ref().and_then(|m| me.run_by(m)) {
                // what it read to evaluate is not what made it
                let made_of: Vec<_> = mr.inputs.iter().filter(|u| role_words(&u.role) != "evaluation data").collect();
                if depth >= MAKERS_BACK {
                    cut = !made_of.is_empty();
                } else {
                    for u in made_of {
                        walk(me, &u.role, &u.resource, Some((mr.id.clone(), rid.to_string())), depth + 1, ours, seen, items);
                    }
                }
            }
            items.push((role.to_string(), rid.to_string(), maker, read_for, cut));
        }
        for r in runs {
            for u in &r.inputs {
                walk(self, &u.role, &u.resource, None, 0, &ours, &mut seen, &mut items);
            }
        }
        let mut steps: Vec<Step> = Vec::new();
        let mut at: HashMap<(String, Option<(String, String)>, String, String), usize> = HashMap::new();
        for (role, rid, maker, read_for, cut) in items {
            let res = self.resources.get(&rid);
            let words = role_words(if role.is_empty() { res.map_or("resource", |r| r.r#type.as_str()) } else { &role });
            let got = res.filter(|r| r.retracted.is_none()).and_then(acquisition::of);
            // the inputs it names, then whose they are, then what the record lacks of them
            let said = |ids: &str, lacks: &str| -> String {
                let (read_for, maker) = (read_for.as_ref().map(|(m, made)| (self.said_id(m, runs), made.clone())), maker.as_ref().map(|m| self.said_id(m, runs)));
                let whose = match (&read_for, &maker) {
                    // where the walk stops, it says so
                    (Some((m, made)), Some(by)) if cut => format!(", which {m} read to make {made} · made by {by}; the recipe stops here, {MAKERS_BACK} makers back"),
                    (Some((m, made)), _) => format!(", which {m} read to make {made}"),
                    (None, Some(m)) => format!(" · made by {m}"),
                    _ => String::new(),
                };
                format!("{words} · {ids}{whose}{lacks}")
            };
            let mut refs = vec![rid.clone()];
            refs.extend(read_for.iter().map(|(m, _)| m.clone()).chain(maker.iter().cloned()));
            let step = match (&got, res) {
                (Some(g), _) => {
                    let key = (words.clone(), read_for.clone(), g.command.clone(), g.from.clone());
                    if maker.is_none() {
                        if let Some(&i) = at.get(&key) {
                            let after = steps[i].refs.iter().take_while(|x| self.resources.contains_key(*x)).count();
                            steps[i].refs.insert(after, rid.clone());
                            let ids: Vec<String> = steps[i].refs.iter().filter(|x| self.resources.contains_key(*x)).cloned().collect();
                            steps[i].label = said(&ids.join(", "), "");
                            continue;
                        }
                        at.insert(key, steps.len());
                    }
                    Step { what: words.clone(), label: said(&rid, ""), refs, command: g.command.clone(), from: g.from.clone(), runnable: true }
                }
                (None, Some(r)) if r.retracted.is_some() => Step { what: words.clone(), label: said(&rid, ": retracted"), refs, ..Default::default() },
                (None, Some(r)) => {
                    // words saying where it comes from, on both tiers; its address is the resource's own (resources{}.url)
                    let how = r.acquisition.as_ref().map(|a| a.source.trim().to_string()).filter(|s| !s.is_empty());
                    Step { what: words.clone(), label: said(&rid, &format!(": no recipe recorded{}", how.map(|h| format!(" · {h}")).unwrap_or_default())), refs, ..Default::default() }
                }
                (None, None) => Step { what: words.clone(), label: said(&rid, ": no such resource record"), refs, ..Default::default() },
            };
            steps.push(step);
        }
        steps
    }

    /// The last step: the command as given, apart, as history. A run's own is a line to run only when rb knows it made
    /// the numbers (given as it was made, watched by rb journal run, or launched by a log line written then); one given
    /// later is said, never offered to run.
    fn command_step(&self, runs: &[&Run], given: &[(&Run, Given)], one: Option<&Run>) -> Step {
        let refs: Vec<String> = given.iter().map(|(r, _)| r.id.clone()).collect();
        let what = "command (as given)".to_string();
        if one.is_some() {
            let Some((_, g)) = given.first() else {
                return Step { what, label: "command (as given) · not recorded".into(), refs, ..Default::default() };
            };
            // how rb knows it made the numbers, where rb saw more than the words given: the journal watched it, or a log
            // line written then records its launch (its file and line in rb's words)
            let label = match g.given {
                "observed" | "logged" => format!("{} · {}", g.label(), g.how),
                _ if g.made_it() => g.label().to_string(),
                _ => format!("{}: {}", g.label(), g.text),
            };
            return Step { what, label, refs, command: g.text.clone(), from: String::new(), runnable: g.made_it() };
        }
        let (k, n) = (given.len(), runs.len());
        let later = given.iter().filter(|(_, g)| !g.made_it()).count();
        let mut label = match (k, n) {
            (0, 1) => "command (as given) · not recorded".to_string(),
            (0, n) => format!("command (as given) · not recorded for any of the {n} runs"),
            (k, n) if k == n => format!("command (as given) · recorded for {}", if n == 1 { "the run".to_string() } else { format!("all {n} runs") }),
            (k, n) => format!("command (as given) · recorded for {k} of {n} runs"),
        };
        if later > 0 {
            label.push_str(&format!(" · {later} given later, not observed"));
        }
        Step { what, label, refs, ..Default::default() }
    }

    /// The per-run values, by arm: every run under each arm its numbers name ("" when they name none).
    fn per_run(&self, exp: &Experiment, runs: &[&Run], one: Option<&Run>) -> (Value, Vec<String>) {
        let mut keys: Vec<String> = Vec::new();
        if one.is_none() {
            for (name, s) in exp.all_settings() {
                if s.per_run && !keys.contains(&name) {
                    keys.push(name);
                }
            }
        }
        for r in runs {
            for k in r.per_run.keys() {
                if !keys.contains(k) {
                    keys.push(k.clone());
                }
            }
        }
        let mut by_arm: IndexMap<String, Vec<Value>> = IndexMap::new();
        for r in runs {
            let mut d = crate::dict! {"run" => r.id.clone()};
            for k in &keys {
                if let Some(v) = r.per_run.get(k) {
                    d.insert(k.clone(), Value::Str(crate::ledger::show(v)));
                }
            }
            let arms = arms_of(Some(exp), r);
            for arm in if arms.is_empty() { vec![String::new()] } else { arms } {
                by_arm.entry(arm).or_default().push(Value::Dict(d.clone()));
            }
        }
        let mut lines: Vec<String> = Vec::new();
        if let Some(r) = one {
            if !r.per_run.is_empty() {
                let names: Vec<&str> = r.per_run.keys().map(String::as_str).collect();
                lines.push(format!("# per run: {}", one_line(&r.per_run.iter().map(|(k, v)| format!("{} {}", key_words(k, &names), crate::ledger::show(v))).collect::<Vec<_>>().join(" · "))));
            }
        } else if !keys.is_empty() && !runs.is_empty() {
            // each name in words, as Runs says it
            let names: Vec<&str> = keys.iter().map(String::as_str).collect();
            lines.push(format!("# per run ({}): {}", runs.len(), one_line(&keys.iter().map(|k| key_words(k, &names)).collect::<Vec<_>>().join(" · "))));
            for (arm, rs) in &by_arm {
                let each: Vec<String> = rs.iter().map(|d| keys.iter().map(|k| d.get_str(k).unwrap_or("unknown").to_string()).collect::<Vec<_>>().join(" ")).collect();
                lines.push(format!("#   {}{}", if arm.is_empty() { String::new() } else { format!("{}: ", one_line(arm)) }, one_line(&each.join(" · "))));
            }
        }
        let value = crate::obj! {
            "keys" => Value::list_of_str(keys),
            "by_arm" => Value::Dict(by_arm.into_iter().map(|(k, v)| (k, Value::List(v))).collect())
        };
        (value, lines)
    }

    /// A setting's standing as this read finds it (a verified one whose line came after a run reads provisional),
    /// worked out once per setting.
    fn status(&self, exp: &Experiment, name: &str, s: &Setting) -> String {
        let key = (exp.id.clone(), name.to_string());
        if let Some(st) = self.statuses.borrow().get(&key) {
            return st.clone();
        }
        let st = self.led.status_read(exp, name, s).unwrap_or_else(|_| s.status.clone());
        self.statuses.borrow_mut().insert(key, st.clone());
        st
    }

    /// The settings a run is given, shared and by arm, each with its source and standing, and those whose value is
    /// unknown: the experiment's as it declares them, or for one run the values it was recorded with.
    fn settings(&self, exp: &Experiment, one: Option<&Run>) -> Res<(Value, Vec<String>)> {
        let mut shared: Vec<(String, String, String, String)> = Vec::new();
        let mut by_arm: IndexMap<String, Vec<(String, String, String, String)>> = IndexMap::new();
        let mut unknown: Vec<String> = Vec::new();
        let mut put = |name: &str, value: &Value, s: Option<&Setting>| {
            if name.starts_with("population.") {
                return;
            }
            if value.is_null() {
                unknown.push(name.to_string());
                return;
            }
            let (source, status) = match s {
                Some(s) if crate::ledger::same_value(&s.value, value) => (s.source.as_ref().map(cite).unwrap_or_default(), self.status(exp, name, s)),
                _ => (String::new(), String::new()),
            };
            let (arm, bare) = exp.split(name);
            let row = (bare.to_string(), crate::ledger::show(value), source, status);
            match arm {
                Some(v) => by_arm.entry(v.name.clone()).or_default().push(row),
                None => shared.push(row),
            }
        };
        match one {
            Some(r) => {
                for (name, v) in &r.settings {
                    put(name, v, exp.lookup(name));
                }
            }
            None => {
                for (name, s) in exp.all_settings() {
                    if !s.per_run {
                        put(&name, &s.value, Some(s));
                    }
                }
            }
        }
        let verified = shared.iter().chain(by_arm.values().flatten()).filter(|r| r.3 == "verified").count();
        let row = |r: &(String, String, String, String)| crate::obj! {"name" => r.0.clone(), "value" => r.1.clone(), "source" => r.2.clone(), "status" => r.3.clone()};
        let kv = |rs: &[(String, String, String, String)]| rs.iter().map(|r| format!("{}={}", r.0, r.1)).collect::<Vec<_>>().join(" ");
        let mut lines: Vec<String> = Vec::new();
        if !shared.is_empty() || !by_arm.is_empty() || !unknown.is_empty() {
            lines.push(format!("# settings: {}", one_line(&kv(&shared))).trim_end().to_string());
            for (arm, rs) in &by_arm {
                lines.push(format!("#   {}: {}", one_line(arm), one_line(&kv(rs))));
            }
            if !unknown.is_empty() {
                lines.push(format!("# unknown: {}", one_line(&unknown.join(", "))));
            }
        }
        let value = crate::obj! {
            "shared" => Value::List(shared.iter().map(row).collect()),
            "by_arm" => Value::Dict(by_arm.iter().map(|(k, rs)| (k.clone(), Value::List(rs.iter().map(row).collect()))).collect()),
            "unknown" => Value::list_of_str(unknown.clone()),
            "counts" => crate::obj! {"verified" => verified, "unknown" => unknown.len()}
        };
        Ok((value, lines))
    }

    /// What each run made, or, for a run that made nothing, what a standing run with the same bytes made: that
    /// resource's recipe, named for this run and its config alone.
    fn made(&self, runs: &[&Run], one: Option<&Run>) -> Vec<Made> {
        let mut out: Vec<Made> = Vec::new();
        for r in runs {
            let named = self.said(r, false);
            let (maker, outputs): (&Run, Vec<String>) = if !r.outputs.is_empty() {
                (*r, r.outputs.iter().map(|u| u.resource.clone()).collect())
            } else {
                let twin = self.runs.iter().filter(|m| standing(m) && !m.outputs.is_empty()).find(|m| same_bytes(r, m));
                match twin {
                    Some(m) => (m, m.outputs.iter().map(|u| u.resource.clone()).collect()),
                    None => continue,
                }
            };
            for rid in outputs {
                let Some(res) = self.resources.get(&rid).filter(|x| x.retracted.is_none()) else { continue };
                let got = acquisition::of(res).unwrap_or_default();
                let what = if maker.id == r.id {
                    format!("made {rid}")
                } else {
                    format!("the same bytes are recorded by {}, which made {rid}", self.said(maker, true))
                };
                let recipe = if got.command.is_empty() { " No recipe is recorded for it." } else { "" };
                let line = match one {
                    Some(_) => format!("{}{}.{recipe}", what[..1].to_uppercase(), &what[1..]),
                    None => format!("{named}: {what}.{recipe}"),
                };
                let head = format!("{named}: {what}");
                out.push(Made { run: r.id.clone(), maker: maker.id.clone(), resource: rid, command: got.command, from: got.from, line, head });
            }
        }
        out
    }

    /// The recording step, in words (T10), in SHELL-SPEC section 12's form: one more measurement of what the
    /// experiment's criterion reads, of the arms it compares ("<subject> vs <reference>"). The criterion is the first
    /// rb status suggests a run for (`with_open`), else the first primary one, else the first that is not a gate. ""
    /// when no criterion names a metric.
    fn prompt(&self, exp: &Experiment) -> String {
        let criteria = match self.led.live::<crate::record::Criterion>() {
            Ok(c) => c,
            Err(_) => return String::new(),
        };
        let mine: Vec<&crate::record::Criterion> = criteria.iter().filter(|c| c.experiment == exp.id && !c.metric.is_empty()).collect();
        let suggested = self.open.iter().filter(|i| next_step::adds_a_run(i)).flat_map(next_step::criteria_of).find_map(|id| mine.iter().find(|c| c.id == id));
        let Some(c) = suggested.or_else(|| mine.iter().find(|c| c.role == "primary")).or_else(|| mine.iter().find(|c| c.role != "gate")).or_else(|| mine.first()) else {
            return String::new();
        };
        // the arms, the metric and the runs it selects as rb reads the criterion, as the Next step says them
        next_step::for_criterion(self.led, exp, c, next_step::Arms::Versus).unwrap_or_default()
    }
}

/// The ready rows as Reproduce shows them: (label, text, state, refs). Rows that say the same thing about inputs in
/// one role read as one row naming each input; every other row stands alone.
fn merged(rows: &[Ready]) -> Vec<(String, String, String, Vec<String>)> {
    let mut out: Vec<(String, String, String, Vec<String>)> = Vec::new();
    let mut at: HashMap<(String, String, String, Vec<String>), usize> = HashMap::new();
    for r in rows {
        let label = role_words(&r.what);
        match &r.resource {
            Some(id) => {
                let key = (label.clone(), r.state.clone(), r.avail.clone(), r.also.clone());
                let rest: String = std::iter::once(r.avail.clone()).chain(r.also.iter().cloned()).collect::<Vec<_>>().join(" · ");
                if let Some(&i) = at.get(&key) {
                    out[i].3.push(id.clone());
                    out[i].1 = format!("{} · {}: {rest}", r.state, out[i].3.join(", "));
                    continue;
                }
                at.insert(key, out.len());
                out.push((label, format!("{} · {id}: {rest}", r.state), r.state.clone(), vec![id.clone()]));
            }
            None => out.push((label, format!("{} · {}", r.state, r.detail), r.state.clone(), Vec::new())),
        }
    }
    out
}

/// "to run again: ✓ 2 ok · ◐ 1 partial · ○ 1 unknown"; "" with nothing to say.
fn ready_line(ready: &[(String, String, String, Vec<String>)]) -> String {
    let parts: Vec<String> = GLYPHS
        .iter()
        .filter_map(|(state, glyph)| {
            let n = ready.iter().filter(|r| r.2 == *state).count();
            (n > 0).then(|| format!("{glyph} {n} {state}"))
        })
        .collect();
    if parts.is_empty() {
        String::new()
    } else {
        format!("to run again: {}", parts.join(" · "))
    }
}

/// How many of the runs record their command as given, and how many of those were given later, not observed.
fn as_given_line(runs: &[&Run], given: &[(&Run, Given)], one: Option<&Run>) -> String {
    let (k, n) = (given.len(), runs.len());
    let later = given.iter().filter(|(_, g)| !g.made_it()).count();
    if one.is_some() {
        return match given.first() {
            None => "the run records no command (as given)".to_string(),
            Some((_, g)) if !g.made_it() => "the run records a command given later, not observed".to_string(),
            Some(_) => "the run records its command (as given)".to_string(),
        };
    }
    let head = match (k, n) {
        (_, 0) => return String::new(),
        (0, _) => "no run records its command (as given)".to_string(),
        (k, n) if k == n && n == 1 => "the run records its command (as given)".to_string(),
        (k, n) if k == n => "every run records its command (as given)".to_string(),
        (1, n) => format!("1 of {n} runs records its command (as given)"),
        (k, n) => format!("{k} of {n} runs record their command (as given)"),
    };
    match later {
        0 => head,
        1 if k == 1 => format!("{head}, given later, not observed"),
        m => format!("{head} · {m} given later, not observed"),
    }
}

// ---------------------------------------------------------------- Rerun: what a clone needs to run its parent again

/// Each live experiment's way back to its parent's runs, worked out from the parent's record while rb clone has it:
/// the experiment's first rule (the first primary one, else the first that is not a gate, else the first), and for
/// each arm it compares, reference first, the command one of the parent's runs it reads was recorded with. rb clone
/// keeps each in its experiment's clone line (`detail.rerun`), since the parent's runs do not travel; `rerun` reads it
/// back. Only what the parent recorded: a run's command as given, how rb knew it, its per-run values, and the code it
/// ran where its receipt holds it (or the commit it says it was made at, as reported).
pub fn rerun_plan(parent: &Ledger) -> Res<IndexMap<String, Value>> {
    let recipes = Recipes::new(parent, Tier::Live)?;
    let criteria = parent.live::<crate::record::Criterion>()?;
    let mut out = IndexMap::new();
    for exp in parent.live::<Experiment>()? {
        let mine: Vec<&crate::record::Criterion> = criteria.iter().filter(|c| c.experiment == exp.id && !c.metric.is_empty()).collect();
        let Some(c) = mine.iter().find(|c| c.role == "primary").or_else(|| mine.iter().find(|c| c.role != "gate")).or_else(|| mine.first()) else {
            out.insert(exp.id.clone(), crate::obj! {"criterion" => Value::Null, "commands" => Value::List(Vec::new())});
            continue;
        };
        let (subject, reference, _, _) = parent.arms(&exp, c)?;
        let mut arms: Vec<(String, &Dict)> = Vec::new();
        if !reference.is_empty() && reference != subject {
            arms.push((reference.clone(), &c.reference_where));
        }
        if !subject.is_empty() || arms.is_empty() {
            arms.push((subject.clone(), &c.subject_where));
        }
        // the parent's standing runs in record order: ids are drawn at random
        let mut runs: Vec<&Run> = recipes.runs.iter().filter(|r| r.experiment == exp.id && standing(r)).collect();
        runs.sort_by(|a, b| (&a.created_at, &a.id).cmp(&(&b.created_at, &b.id)));
        let holds = |r: &Run, scope: &Dict| scope.iter().all(|(k, v)| r.per_run.get(k).map_or(false, |x| crate::ledger::same_value(x, v)));
        let mut commands: Vec<Value> = Vec::new();
        let mut said: Vec<String> = Vec::new();
        for (arm, own) in &arms {
            // a rule that names no arm (a level of the experiment's only numbers) reads every run in its scope
            let reads: Vec<&Run> = runs.iter().copied().filter(|r| (arm.is_empty() || arms_of(Some(&exp), r).contains(arm)) && holds(r, &c.r#where) && holds(r, own)).collect();
            // a command rb knows made the numbers comes before one given later
            let pick = reads.iter().find(|r| support::run_command(r).map_or(false, |g| g.made_it())).or_else(|| reads.iter().find(|r| support::run_command(r).is_some()));
            let Some(r) = pick else { continue };
            if said.contains(&r.id) {
                continue;
            }
            said.push(r.id.clone());
            let g = support::run_command(r).unwrap_or_else(|| unreachable!("picked for its command"));
            // the code it ran: its commit, and the git tree of its uncommitted changes where it had any
            let (code, reported) = match recipes.held_code(r) {
                Some(code) => (Some(code), false),
                None => {
                    let made = made_code(r);
                    let said = made.is_some();
                    (made, said)
                }
            };
            let tree = code.as_ref().filter(|c| c.tree != c.commit).map(|c| c.tree.clone()).unwrap_or_default();
            let code = code.map(|c| c.commit).unwrap_or_default();
            let per_run: Dict = r.per_run.iter().map(|(k, v)| (k.clone(), Value::Str(crate::ledger::show(v)))).collect();
            commands.push(crate::obj! {
                "arms" => Value::list_of_str(arms_of(Some(&exp), r)), "run" => r.id.clone(), "command" => g.text.clone(), "given" => g.given,
                "per_run" => Value::Dict(per_run), "code" => code, "tree" => tree, "code_reported" => reported
            });
        }
        out.insert(exp.id.clone(), crate::obj! {"criterion" => c.id.clone(), "commands" => Value::List(commands)});
    }
    Ok(out)
}

/// The code of a clone in a sentence, and the command that gets it where it is not here: (state, words, command,
/// whether the code is in this folder). `here` while this checkout holds the commit and its code outside the record
/// (and the files its runs name) is that commit's; `changed` where it holds the commit and files differ; `elsewhere`
/// where it lacks the commit, or where git failed at the address when rb clone tried it (`failed` on its line: that
/// address is not offered again); `unknown` where the bundle named no code, the code here only where the checkout holds files beside
/// the record.
fn code_here(led: &Ledger, code: Option<(&str, &str)>, failed: bool, differs: &dyn Fn(&str) -> Option<Vec<(String, String)>>, bundle: &str, taken: bool) -> (&'static str, String, String, bool) {
    // what the record keeps is quoted as a shell reads it; the placeholders are the reader's to fill
    let again = |address: &str| format!("rb clone {} <new folder> --code {address}", crate::cli::shlex_quote(bundle));
    let Some((url, commit)) = code else {
        return ("unknown", "the runs need the code; this bundle doesn't say where it is".into(), again("<address of the code>"), holds_files(led));
    };
    let short: String = commit.chars().take(12).collect();
    let at = if commit.is_empty() { url.to_string() } else { format!("{url} at {short}") };
    if failed {
        return ("elsewhere", format!("{at}, which rb clone could not get from that address"), again("<address of the code>"), false);
    }
    let root = &led.root;
    let full = (!commit.is_empty()).then(|| sources::git(root, &["rev-parse", "--verify", "-q", &format!("{commit}^{{commit}}")])).flatten().map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    let Some(full) = full else {
        return ("elsewhere", format!("{at}, which this checkout does not hold"), again(&crate::cli::shlex_quote(url)), false);
    };
    let Some(differ) = differs(&full) else {
        return ("elsewhere", format!("{at}, which this checkout does not hold"), again(&crate::cli::shlex_quote(url)), false);
    };
    let differ: Vec<String> = differ.into_iter().map(|(_, p)| p).collect();
    if differ.is_empty() {
        let aside = if !taken { "the record aside" } else { "the record and the parent's results aside" };
        return ("here", format!("{at}, here: this checkout's code is commit {short}'s, {aside}"), String::new(), true);
    }
    let n = differ.len();
    let files = if n == 1 { "1 file differs".to_string() } else { format!("{n} files differ") };
    ("changed", format!("{at}, here with changes: {files} from it ({})", crate::assess::first_three(&differ, ", ")), String::new(), true)
}

/// Whether the record's folder holds a file beside the record, tracked or untracked and not ignored: a folder holding
/// the record alone holds no code to run.
fn holds_files(led: &Ledger) -> bool {
    if sources::git_top(&led.root).is_none() {
        return true;
    }
    // the record's own folder alone: a file elsewhere in a repository it sits inside is no code of this folder's
    let listed = sources::git_bytes(&led.root, &["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".", &format!(":(exclude){}", crate::ledger::DIR)]);
    listed.map_or(true, |b| b.split(|c| *c == 0).any(|p| !p.is_empty()))
}

/// How this checkout's code differs from a commit's, file by file, as git says it (`M` changed, `D` the commit holds
/// it and this checkout does not, `A` this checkout holds it and the commit does not, `U` an untracked file git does
/// not ignore, which a run's code holds too): the record, what rb clone took out of the branch (`taken`,
/// the parent's) and the files the record's runs name (what they wrote) aside. None where this is no checkout.
fn code_differs(led: &Ledger, commit: &str, taken: &[String]) -> Option<Vec<(String, String)>> {
    #[cfg(test)]
    DIFFS.with(|n| n.set(n.get() + 1));
    let root = &led.root;
    let top = sources::git_top(root)?;
    let record = sources::rel_to(&sources::resolve_path(&root.join(crate::ledger::DIR)), &top).unwrap_or_else(|| crate::ledger::DIR.to_string());
    let named: HashSet<String> = led.all::<Run>().map(|rs| rs.iter().flat_map(|r| r.files.iter().map(|f| f.path.clone())).collect()).unwrap_or_default();
    // each path as git keeps it, never quoted: a status, then its path, each ended by a NUL
    let listed = sources::git_bytes(&top, &["diff", "-z", "--name-status", "--no-renames", commit, "--", ".", &format!(":(exclude){record}")])?;
    let theirs = |p: &str| taken.iter().any(|t| t == p || (t.ends_with('/') && p.starts_with(t.as_str())));
    let fields: Vec<String> = listed.split(|b| *b == 0).map(|f| String::from_utf8_lossy(f).into_owned()).collect();
    let mut out: Vec<(String, String)> = fields.chunks(2).filter(|c| c.len() == 2).map(|c| (c[0].chars().take(1).collect::<String>(), c[1].clone())).collect();
    let untracked = sources::git_bytes(&top, &["ls-files", "-z", "--others", "--exclude-standard", "--", ".", &format!(":(exclude){record}")])?;
    for p in untracked.split(|b| *b == 0).map(|f| String::from_utf8_lossy(f).into_owned()) {
        if !out.iter().any(|(_, q)| *q == p) {
            out.push(("U".to_string(), p));
        }
    }
    out.retain(|(_, p)| !p.is_empty() && !named.contains(p) && !theirs(p));
    out.sort_by(|a, b| a.1.cmp(&b.1));
    Some(out)
}

#[cfg(test)]
thread_local! {
    /// How many times this thread compared a checkout's code with a commit's.
    static DIFFS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

/// What a clone needs to run its parent again, read from the record on every read: None for a record that is no
/// clone. `parent` (its address and when it was cloned), `code` (what the bundle named, whether it is here, and the
/// command that gets it where it is not), `resources` (each with how to get it again, from what the record keeps of
/// it), and `experiments` (each with its parent's commands, the first as `rb journal run -- <command>`, and how many
/// runs were recorded here).
pub fn rerun(led: &Ledger) -> Res<Option<Value>> {
    let proj = led.project()?;
    let Some(parent) = proj.parent.as_ref() else { return Ok(None) };
    let log = led.log_entries()?;
    let detail_of = |kind: &str, id: &str| -> Option<Dict> {
        log.iter()
            .filter(|e| e.get_str("op") == Some("clone") && e.get_str("kind") == Some(kind) && e.get_str("id") == Some(id))
            .last()
            .and_then(|e| e.get("detail").and_then(Value::as_dict).cloned())
    };
    let code = detail_of("project", &proj.id).and_then(|d| d.get("code").and_then(Value::as_dict).cloned());
    let url = code.as_ref().and_then(|c| field(c, "url"));
    let commit = code.as_ref().and_then(|c| field(c, "commit")).unwrap_or_default();
    let bundle = crate::http::shareable(&parent.url);
    let taken: Vec<String> = code.as_ref().and_then(|c| c.get("taken_out")).and_then(Value::as_list).map(|l| l.iter().map(pyjson::py_str).collect()).unwrap_or_default();
    let failed = code.as_ref().and_then(|c| c.get("failed")).map_or(false, |f| matches!(f, Value::Bool(true)));
    // one diff per commit or tree, however many of the parent's runs and the bundle name it, on each read
    let diffs: std::cell::RefCell<HashMap<String, Option<Vec<(String, String)>>>> = Default::default();
    let differs = |full: &str| -> Option<Vec<(String, String)>> {
        if let Some(d) = diffs.borrow().get(full) {
            return d.clone();
        }
        let d = code_differs(led, full, &taken);
        diffs.borrow_mut().insert(full.to_string(), d.clone());
        d
    };
    let (state, words, get, in_folder) = code_here(led, url.as_deref().map(|u| (u, commit.as_str())), failed, &differs, &bundle, !taken.is_empty());
    let code_value = crate::obj! {
        "url" => url.clone().map(Value::Str).unwrap_or(Value::Null), "commit" => if commit.is_empty() { Value::Null } else { Value::Str(commit.clone()) },
        "state" => state, "text" => words, "get" => get, "here" => in_folder
    };
    let mut resources: Vec<Value> = Vec::new();
    for res in led.live::<Resource>()? {
        let av = led.availability(&res);
        let mut how: Vec<String> = Vec::new();
        if let Some(a) = acquisition::of(&res) {
            how.push(if a.from.is_empty() { a.command } else { format!("{} ({})", a.command, a.from) });
        }
        for l in res.locations.iter().filter(|l| l.provider != "local") {
            how.push(format!("{}: {}", l.provider, l.r#ref));
        }
        if let Some(a) = &res.acquisition {
            how.extend(a.url.iter().filter(|u| !u.trim().is_empty()).map(|u| u.trim().to_string()));
            if !a.source.trim().is_empty() {
                how.push(a.source.trim().to_string());
            }
        }
        how.dedup();
        // where the record expects its bytes here, and whether they are there
        let mut expects: Vec<String> = Vec::new();
        for l in res.locations.iter().filter(|l| l.provider == "local") {
            let here = led.local(&l.r#ref).exists();
            let said = format!("{} ({})", l.r#ref, if here { "here" } else { "not here" });
            if !expects.contains(&said) {
                expects.push(said);
            }
        }
        let label = format!("{}{}", res.name, if res.version.is_empty() { String::new() } else { format!(" {}", res.version) });
        resources.push(crate::obj! {
            "id" => res.id.clone(), "type" => res.r#type.clone(), "name" => label, "state" => av.get_str("state").unwrap_or("").to_string(),
            "how" => Value::list_of_str(how), "expects" => Value::list_of_str(expects)
        });
    }
    let runs = led.live::<Run>()?;
    // the code a parent's run ran, against this checkout: the same, other code with the command that puts its files
    // here, or a commit this checkout does not hold; nothing where the run names none
    let ran = |c: &str, tree: &str| -> Dict {
        if c.is_empty() {
            return Dict::new();
        }
        // with uncommitted changes, its code is the tree rb kept of them, which a clone of the code does not carry
        let (kind, at) = if tree.is_empty() { ("commit", c) } else { ("tree", tree) };
        let full = sources::git(&led.root, &["rev-parse", "--verify", "-q", &format!("{at}^{{{kind}}}")]).map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        let Some(differ) = full.as_deref().and_then(differs) else { return crate::dict! {"state" => "absent"} };
        if differ.is_empty() {
            return crate::dict! {"state" => "same"};
        }
        let of = |f: &dyn Fn(&str) -> bool| differ.iter().filter(|(st, _)| f(st)).map(|(_, p)| p.clone()).collect::<Vec<String>>();
        let (held, extra, untracked) = (of(&|st| st != "A" && st != "U"), of(&|st| st == "A"), of(&|st| st == "U"));
        let short: String = at.chars().take(12).collect();
        // restored from the commit (or tree): its files as it holds them, and a tracked file it does not hold taken
        // away; git restore knows no untracked file, which stays as it is
        let paths: Vec<String> = differ.iter().filter(|(st, _)| st != "U").map(|(_, p)| p.clone()).collect();
        let get = if paths.is_empty() { String::new() } else { format!("git restore --source={short} --worktree -- {}", crate::cli::shlex_join(&paths)) };
        crate::dict! {"state" => "differs", "differs" => Value::list_of_str(held), "extra" => Value::list_of_str(extra), "untracked" => Value::list_of_str(untracked), "get" => get}
    };
    let mut experiments: Vec<Value> = Vec::new();
    for exp in led.live::<Experiment>()? {
        let plan = detail_of("experiment", &exp.id).and_then(|d| d.get("rerun").cloned()).unwrap_or(Value::Null);
        let mut commands: Vec<Value> = plan.get("commands").and_then(Value::as_list).cloned().unwrap_or_default();
        for c in commands.iter_mut() {
            let here = ran(c.get_str("code").unwrap_or(""), c.get_str("tree").unwrap_or(""));
            if let Value::Dict(d) = c {
                d.insert("code_here".into(), Value::Dict(here));
            }
        }
        let first = commands.first().and_then(|c| c.get_str("command")).map(|c| format!("rb journal run -- {c}")).unwrap_or_default();
        experiments.push(crate::obj! {
            "id" => exp.id.clone(), "title" => exp.title.clone(), "criterion" => plan.get("criterion").cloned().unwrap_or(Value::Null),
            "commands" => Value::List(commands), "first" => first, "runs_here" => runs.iter().filter(|r| r.experiment == exp.id).count()
        });
    }
    Ok(Some(crate::obj! {
        "parent" => crate::obj! {"url" => bundle, "cloned_at" => parent.cloned_at.clone(), "title" => proj.title.clone()},
        "code" => code_value, "resources" => Value::List(resources), "experiments" => Value::List(experiments)
    }))
}

/// A parent's run's command in a line: the command, then what rb knew of it ("its command given later, not
/// observed"), its per-run values and the code it ran.
fn command_said(c: &Value) -> String {
    let per_run: Vec<String> = c.get("per_run").and_then(Value::as_dict).map(|d| d.iter().map(|(k, v)| format!("{k}={}", pyjson::py_str(v))).collect()).unwrap_or_default();
    let how = if c.get_str("given") == Some("later") { "its command given later, not observed" } else { "its command as given" };
    let arms = c.get("arms").and_then(Value::as_list).map(|l| l.iter().map(pyjson::py_str).collect::<Vec<_>>()).unwrap_or_default();
    let mut parts = vec![format!("the parent's run {}{}, {how}", c.get_str("run").unwrap_or(""), if arms.is_empty() { String::new() } else { format!(" of {}", next_step::and_list(&arms)) })];
    if !per_run.is_empty() {
        parts.push(format!("per run {}", per_run.join(" ")));
    }
    if let Some(code) = c.get_str("code").filter(|x| !x.is_empty()) {
        let short: String = code.chars().take(12).collect();
        let tree = c.get_str("tree").filter(|t| !t.is_empty()).map(|t| format!(" with the uncommitted changes rb kept as git tree {}", t.chars().take(12).collect::<String>())).unwrap_or_default();
        parts.push(if c.get("code_reported").map_or(false, Value::truthy) { format!("made at {short}{tree}, as reported") } else { format!("ran at {short}{tree}") });
        let here = c.get("code_here").cloned().unwrap_or(Value::empty_dict());
        // the commit or tree by its name, never "that commit": the briefing names more than one
        let that = if tree.is_empty() { format!("commit {short}") } else { format!("tree {}", c.get_str("tree").unwrap_or("").chars().take(12).collect::<String>()) };
        let files = |k: &str| here.get(k).and_then(Value::as_list).map(|l| l.iter().map(|x| one_line(&pyjson::py_str(x))).collect::<Vec<_>>()).unwrap_or_default();
        match here.get_str("state") {
            Some("same") => parts.push(format!("this checkout's code is {that}'s")),
            Some("absent") => parts.push(format!("{} this checkout does not hold", if tree.is_empty() { "a commit" } else { "a tree" })),
            Some("differs") => {
                let (held, extra, untracked) = (files("differs"), files("extra"), files("untracked"));
                let mut said = format!("this checkout's code is not {that}'s");
                // a few files are named, more are counted after the first three
                let named = |fs: &[String]| if fs.len() <= 3 { next_step::and_list(fs) } else { crate::assess::first_three(fs, ", ") };
                if !held.is_empty() {
                    said.push_str(&format!(": it differs in {}", named(&held)));
                }
                let both: Vec<String> = extra.iter().cloned().chain(untracked.iter().map(|u| format!("{u} (untracked)"))).collect();
                if !both.is_empty() {
                    said.push_str(&format!("{} holds {} {} does not", if held.is_empty() { ":" } else { ", and" }, named(&both), if tree.is_empty() { "the commit" } else { "the tree" }));
                }
                // the command writes over this checkout's files and deletes the ones it holds and that does not,
                // keeping no copy: said here with what it overwrites, never offered as a next step
                if let Some(get) = here.get_str("get").filter(|g| !g.is_empty()) {
                    let mut over = Vec::new();
                    if !held.is_empty() {
                        over.push(format!("writes over {}", named(&held)));
                    }
                    if !extra.is_empty() {
                        over.push(format!("deletes {}", named(&extra)));
                    }
                    let makes = if untracked.is_empty() { format!("makes it {that}'s") } else { format!("puts {that}'s files here") };
                    said.push_str(&format!("; `{}` {makes}: it {} here and keeps no copy", one_line(get), next_step::and_list(&over)));
                }
                if !untracked.is_empty() {
                    said.push_str(&format!("; git restore leaves {} as {}", named(&untracked), if untracked.len() == 1 { "it is" } else { "they are" }));
                }
                parts.push(said);
            }
            _ => {}
        }
    }
    format!("`rb journal run -- {}` ({})", one_line(c.get_str("command").unwrap_or("")), parts.join("; "))
}

/// The rerun as lines of a briefing (rb context, rb status): the parent, the code, each resource, and each experiment's
/// commands, the first of them the command to run first.
pub fn rerun_lines(v: &Value) -> Vec<String> {
    let p = v.get("parent").cloned().unwrap_or(Value::empty_dict());
    let mut l = vec![format!("- parent: {} (cloned {} from {})", one_line(p.get_str("title").unwrap_or("")), p.get_str("cloned_at").unwrap_or("").chars().take(10).collect::<String>(), one_line(p.get_str("url").unwrap_or("")))];
    let code = v.get("code").cloned().unwrap_or(Value::empty_dict());
    let get = code.get_str("get").unwrap_or("");
    l.push(format!("- code: {}{}", one_line(code.get_str("text").unwrap_or("")), if get.is_empty() { String::new() } else { format!(": `{}`", one_line(get)) }));
    for r in v.get("resources").and_then(Value::as_list).map(Vec::as_slice).unwrap_or(&[]) {
        let list = |k: &str| r.get(k).and_then(Value::as_list).map(|l| l.iter().map(|x| one_line(&pyjson::py_str(x))).collect::<Vec<_>>()).unwrap_or_default();
        let (how, expects) = (list("how"), list("expects"));
        let mut parts = vec![format!("{} · {}", r.get_str("type").unwrap_or(""), r.get_str("state").unwrap_or(""))];
        if !expects.is_empty() {
            parts.push(format!("the record expects it at {}", expects.join(", ")));
        }
        parts.push(if how.is_empty() { "the record says no way to get it again".to_string() } else { format!("how to get it again: {}", how.join(" · ")) });
        l.push(format!("- resource {} ({}): {}", one_line(r.get_str("id").unwrap_or("")), one_line(r.get_str("name").unwrap_or("")), parts.join("; ")));
    }
    for e in v.get("experiments").and_then(Value::as_list).map(Vec::as_slice).unwrap_or(&[]) {
        let id = one_line(e.get_str("id").unwrap_or(""));
        let commands = e.get("commands").and_then(Value::as_list).map(Vec::as_slice).unwrap_or(&[]);
        let here = e.get("runs_here").and_then(Value::as_i64).unwrap_or(0);
        let runs = match here {
            0 => "no run recorded here yet".to_string(),
            1 => "1 run recorded here".to_string(),
            n => format!("{n} runs recorded here"),
        };
        let rule = e.get_str("criterion").map(|c| format!(", for {}", one_line(c))).unwrap_or_default();
        match commands {
            [] => l.push(format!("- {id}: the parent's record names no command for its runs{rule}; {runs}")),
            [one] => l.push(format!("- {id}, first{rule}: {}; {runs}", command_said(one))),
            [first, rest @ ..] => {
                l.push(format!("- {id}, first{rule}: {}; {runs}", command_said(first)));
                l.extend(rest.iter().map(|c| format!("  then: {}", command_said(c))));
            }
        }
    }
    l
}

/// The commands a rerun asks for next, in order: the code where it is not here, then, where this folder holds the code,
/// each experiment's first command while no run of it is recorded here. Where the code is got into a new folder, its
/// commands run there, and that clone's own Next names them. Never a command that writes over this checkout's files
/// (a run's `git restore`), which the block says with what it overwrites.
pub fn rerun_next(v: &Value) -> Vec<String> {
    let mut next: Vec<String> = Vec::new();
    let code = v.get("code");
    if let Some(get) = code.and_then(|c| c.get_str("get")).filter(|g| !g.is_empty()) {
        next.push(get.to_string());
    }
    if code.and_then(|c| c.get("here")).map_or(false, |h| matches!(h, Value::Bool(false))) {
        return next;
    }
    for e in v.get("experiments").and_then(Value::as_list).map(Vec::as_slice).unwrap_or(&[]) {
        if e.get("runs_here").and_then(Value::as_i64).unwrap_or(0) == 0 {
            if let Some(first) = e.get_str("first").filter(|f| !f.is_empty() && !next.iter().any(|n| n == f)) {
                next.push(first.to_string());
            }
        }
    }
    next
}

#[cfg(test)]
mod tests {
    //! Each record here is written through the ledger's own writes, the ones rb's commands make, in a git repository
    //! of its own: the INT8 ResNet-50 on ImageNet example, written by human:ana.
    use super::*;
    use crate::ledger::{NewAnalysis, NewCriterion, NewExperiment, NewResource, NewRun, SetSetting};
    use crate::record::Criterion;
    use std::path::{Path, PathBuf};
    use std::process::Command;

    fn git(root: &Path, args: &[&str]) -> String {
        let out = Command::new("git")
            .args(["-c", "user.email=ana@example.org", "-c", "user.name=ana", "-c", "commit.gpgsign=false"])
            .args(args)
            .current_dir(root)
            .output()
            .expect("git runs");
        assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
        String::from_utf8_lossy(&out.stdout).trim().to_string()
    }

    /// A record in a repository of its own, removed when the test ends.
    struct Rec {
        root: PathBuf,
        led: Ledger,
        /// the run ids, by the name the test gives each
        ids: HashMap<&'static str, String>,
    }

    impl Drop for Rec {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    impl Rec {
        fn write(&self, path: &str, text: &str) {
            let p = self.root.join(path);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(p, text).unwrap();
        }

        fn run(&mut self, name: &'static str, exp: &str, a: NewRun) -> String {
            let id = self.led.add_run(exp, a).unwrap().record.id;
            self.ids.insert(name, id.clone());
            id
        }

        fn id(&self, name: &str) -> String {
            self.ids[name].clone()
        }

        fn load_run(&self, name: &str) -> Run {
            self.led.load::<Run>(&self.id(name)).unwrap()
        }

        fn exp(&self, id: &str) -> Experiment {
            self.led.load::<Experiment>(id).unwrap()
        }
    }

    fn metrics(pairs: &[(&str, f64)]) -> IndexMap<String, Value> {
        pairs.iter().map(|(k, v)| (k.to_string(), Value::Float(*v))).collect()
    }

    fn uses(pairs: &[(&str, &str)]) -> Vec<(String, String)> {
        pairs.iter().map(|(r, role)| (r.to_string(), role.to_string())).collect()
    }

    /// e1, INT8 against FP32 with a per-run seed: its seed-1 run made the int8 checkpoint as it was recorded, at a
    /// committed clean tree; its seed-2 run was made days before it was recorded and names no commit. e2, calibration
    /// set size: its 512-image run recorded the bytes the seed-1 run wrote, its 1024-image run other bytes. e3 times
    /// the checkpoint seed 1 made. The FP32 weights have an address and no recipe.
    fn int8_record(tag: &str) -> Rec {
        let root = std::env::temp_dir().join(format!("rb-reproduce-{tag}-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(&root).unwrap();
        git(&root, &["init", "-q"]);
        git(&root, &["config", "user.email", "ana@example.org"]);
        git(&root, &["config", "user.name", "ana"]);
        let led = Ledger::init(&root, "INT8 ResNet-50 on ImageNet", Some("int8-resnet-50"), Some("cli"), None, false).unwrap();
        let mut rec = Rec { root, led, ids: HashMap::new() };
        let led = &rec.led;
        led.add_question("Is INT8 ResNet-50 as accurate as FP32 on ImageNet?", Some("q1"), None).unwrap();
        led.add_experiment(&NewExperiment {
            title: "INT8 against FP32: top-1 on the validation set".into(),
            id: Some("e1".into()),
            question: Some("q1".into()),
            hypothesis: "INT8 keeps top-1 within a point of FP32".into(),
            baseline: Some("fp32".into()),
            candidates: vec!["int8".into()],
            ..Default::default()
        })
        .unwrap();
        led.set_setting("e1", "seed", SetSetting { per_run: Some(true), why: "one seed per run".into(), ..Default::default() }).unwrap();
        led.set_setting("e1", "batch_size", SetSetting { value: Value::Int(256), ..Default::default() }).unwrap();
        led.set_setting("e1", "int8.quant", SetSetting { value: Value::Str("int8".into()), ..Default::default() }).unwrap();
        led.set_setting("e1", "quant_backend", SetSetting { unknown: true, ..Default::default() }).unwrap();
        led.add_criterion(&NewCriterion {
            statement: "INT8 loses at most a point of top-1".into(),
            experiment: "e1".into(),
            metric: "change.top1".into(),
            comparator: "at_least".into(),
            target: Some(-0.01),
            id: Some("c1".into()),
            ..Default::default()
        })
        .unwrap();
        led.add_experiment(&NewExperiment {
            title: "Calibration set size".into(),
            id: Some("e2".into()),
            question: Some("q1".into()),
            purpose: "how many calibration images INT8 needs".into(),
            candidates: vec!["int8".into()],
            ..Default::default()
        })
        .unwrap();
        led.set_setting("e2", "calib_images", SetSetting { per_run: Some(true), ..Default::default() }).unwrap();
        led.add_experiment(&NewExperiment {
            title: "INT8 latency on one GPU".into(),
            id: Some("e3".into()),
            question: Some("q1".into()),
            purpose: "how fast the INT8 model answers".into(),
            candidates: vec!["int8".into()],
            ..Default::default()
        })
        .unwrap();
        for (id, ty, recipe, url) in [
            ("imagenet-val", "dataset", Some("python get_data.py --split val (scripts/README.md:5)"), None),
            ("imagenet-calib", "dataset", Some("python get_data.py --calib 512 (scripts/README.md:6)"), None),
            ("resnet50-fp32", "checkpoint", None, Some("https://example.org/resnet50_fp32.pt")),
            ("ckpt-int8", "checkpoint", Some("python quantize.py --seed 1 --calib 512 (scripts/README.md:8)"), None),
        ] {
            led.add_resource(&NewResource {
                id: id.into(),
                r#type: ty.into(),
                name: id.into(),
                recipe: recipe.map(String::from),
                url: url.map(String::from),
                ..Default::default()
            })
            .unwrap();
        }
        rec.write("results/seed1.json", "{\"fp32\": {\"top1\": 0.7613}, \"int8\": {\"top1\": 0.7581}}\n");
        rec.write("results/seed2.json", "{\"fp32\": {\"top1\": 0.7609}, \"int8\": {\"top1\": 0.7574}}\n");
        rec.write("results/calib1024.json", "{\"int8\": {\"top1\": 0.7585}}\n");
        git(&rec.root, &["add", "-A"]);
        git(&rec.root, &["commit", "-qm", "the record and its results"]);
        let p = |k: &str, v: i64| -> Dict { crate::dict! {k => v} };
        rec.run(
            "seed1",
            "e1",
            NewRun {
                metrics: metrics(&[("fp32.top1", 0.7613), ("int8.top1", 0.7581)]),
                files: vec!["results/seed1.json".into()],
                per_run: p("seed", 1),
                inputs: uses(&[("imagenet-val", "eval"), ("imagenet-calib", "train"), ("resnet50-fp32", "weights")]),
                outputs: uses(&[("ckpt-int8", "checkpoint")]),
                command: Some("python eval.py --seed 1".into()),
                ..Default::default()
            },
        );
        rec.run(
            "seed2",
            "e1",
            NewRun {
                metrics: metrics(&[("fp32.top1", 0.7609), ("int8.top1", 0.7574)]),
                files: vec!["results/seed2.json".into()],
                per_run: p("seed", 2),
                inputs: uses(&[("imagenet-val", "eval")]),
                command: Some("python eval.py --seed 2".into()),
                produced_at: Some("2026-10-01T09:00:00Z".into()),
                ..Default::default()
            },
        );
        rec.run(
            "c512",
            "e2",
            NewRun {
                metrics: metrics(&[("int8.top1", 0.7581)]),
                files: vec!["results/seed1.json".into()],
                per_run: p("calib_images", 512),
                inputs: uses(&[("imagenet-val", "eval"), ("imagenet-calib", "train")]),
                ..Default::default()
            },
        );
        rec.run(
            "c1024",
            "e2",
            NewRun {
                metrics: metrics(&[("int8.top1", 0.7585)]),
                files: vec!["results/calib1024.json".into()],
                per_run: p("calib_images", 1024),
                inputs: uses(&[("imagenet-val", "eval"), ("imagenet-calib", "train")]),
                ..Default::default()
            },
        );
        rec.run(
            "t4",
            "e3",
            NewRun { metrics: metrics(&[("int8.latency_ms", 3.9)]), inputs: uses(&[("ckpt-int8", "model")]), host: [("gpu".to_string(), "T4".to_string())].into_iter().collect(), ..Default::default() },
        );
        rec
    }

    fn get<'v>(v: &'v Value, path: &str) -> &'v Value {
        path.split('.').fold(v, |at, k| at.get(k).unwrap_or_else(|| panic!("no {k} in {path}")))
    }

    fn list<'v>(v: &'v Value, path: &str) -> &'v [Value] {
        get(v, path).as_list().map(|l| l.as_slice()).unwrap_or(&[])
    }

    fn text<'v>(v: &'v Value, path: &str) -> &'v str {
        get(v, path).as_str().unwrap_or("")
    }

    fn labels(v: &Value) -> Vec<String> {
        list(v, "steps").iter().map(|s| text(s, "label").to_string()).collect()
    }

    #[test]
    fn reproduce_is_recorded_facts() {
        let rec = int8_record("facts");
        let r = Recipes::new(&rec.led, Tier::Live).unwrap();
        let seed1 = rec.id("seed1");
        let e1 = r.experiment(&rec.exp("e1")).unwrap();
        let e2 = r.experiment(&rec.exp("e2")).unwrap();
        let e3 = r.experiment(&rec.exp("e3")).unwrap();
        // made-by order: e3 times the checkpoint seed 1 made, so what seed 1 read to make it comes first, then the model;
        // what seed 1 read to evaluate did not make it
        let l3 = labels(&e3);
        // a run of another experiment by its label and its experiment, never its id
        let calib = l3.iter().position(|l| l == "training data · imagenet-calib, which fp32 and int8 · seed 1 (e1) read to make ckpt-int8").expect("the maker's training data");
        let weights = l3.iter().position(|l| l == "weights · resnet50-fp32, which fp32 and int8 · seed 1 (e1) read to make ckpt-int8: no recipe recorded").expect("the maker's weights");
        let model = l3.iter().position(|l| l == "model · ckpt-int8 · made by fp32 and int8 · seed 1 (e1)").expect("the model, made by its run");
        assert!(!l3.iter().any(|l| l.contains(&seed1)), "{l3:?}");
        assert!(calib < model && weights < model, "{l3:?}");
        assert!(!l3.iter().any(|l| l.contains("imagenet-val")), "{l3:?}");
        let model_step = &list(&e3, "steps")[model];
        assert_eq!((text(model_step, "command"), text(model_step, "from")), ("python quantize.py --seed 1 --calib 512", "scripts/README.md:8"));
        assert_eq!(get(model_step, "refs"), &Value::list_of_str(["ckpt-int8".to_string(), seed1.clone()]));
        assert_eq!(get(&list(&e3, "steps")[calib], "refs"), &Value::list_of_str(["imagenet-calib".to_string(), seed1.clone()]));
        // unknown lines: every row of what it takes is a comment in the copied script, and an unknown setting is listed
        for v in [&e1, &e2, &e3] {
            let script = text(v, "script");
            assert!(script.starts_with("# Reproduce e") && script.lines().nth(1) == Some(FROM_THE_RECORD), "{script}");
            for row in list(v, "ready") {
                assert!(script.lines().any(|l| l == format!("# {}: {}", text(row, "label"), text(row, "text"))), "{row:?} in {script}");
            }
        }
        assert_eq!(get(&e1, "settings.unknown"), &Value::list_of_str(["quant_backend".to_string()]));
        assert!(text(&e1, "script").lines().any(|l| l == "# unknown: quant_backend"));
        // seed 2 was made before it was recorded and names no commit: the code is a sentence, nothing to check out
        let code = &list(&e1, "steps")[0];
        assert_eq!(text(code, "what"), "code");
        assert!(!get(code, "runnable").truthy() && text(code, "command").is_empty(), "{code:?}");
        assert!(text(code, "label").contains("one of the two runs was made before it was recorded and names no commit"), "{code:?}");
        // a code step with nothing to run is said once in the copied script, by its ready row
        let e1_script = text(&e1, "script");
        assert!(e1_script.lines().any(|l| l.starts_with("# code: partial · ")) && !e1_script.lines().any(|l| l.starts_with("# code · ")), "{e1_script}");
        // seed 1's receipt holds the code that made it: a worktree at its commit, never a checkout of this one
        let head = git(&rec.root, &["rev-parse", "HEAD"]);
        let s1 = r.run(&rec.load_run("seed1")).unwrap();
        let wt = &list(&s1, "steps")[0];
        assert_eq!(text(wt, "command"), format!("git worktree add --detach ../e1-{} {head}", &head[..7]));
        assert!(get(wt, "runnable").truthy() && text(&s1, "script").contains(&format!("\ngit worktree add --detach ../e1-{} {head}\n", &head[..7])));
        // seed 2's receipt names only the commit it was recorded at: no worktree, and that commit is in no line of its recipe
        let s2 = r.run(&rec.load_run("seed2")).unwrap();
        let attached = pyjson::py_str(rec.load_run("seed2").receipt.attached.as_ref().unwrap().get("commit").unwrap());
        assert_eq!(attached, head);
        let all = |v: &Value| pyjson::dumps(v);
        assert!(!all(&s2).contains(&head[..7]), "{}", all(&s2));
        assert!(!all(&s2).contains("git worktree"));
        for v in [&e1, &e2, &e3, &s1, &s2, &r.run(&rec.load_run("c512")).unwrap(), &r.run(&rec.load_run("c1024")).unwrap(), &r.run(&rec.load_run("t4")).unwrap()] {
            let whole = all(v);
            assert!(!whole.contains("git checkout") && !whole.contains("rb run add"), "{whole}");
        }
        // the command as given stays apart, as history: counted, listed, and a line to run only for a run's own
        assert_eq!(text(&e1, "command_as_given.line"), "every run records its command (as given) · 1 given later, not observed");
        let last = list(&e1, "steps").last().unwrap();
        assert_eq!(text(last, "label"), "command (as given) · recorded for all 2 runs · 1 given later, not observed");
        assert!(!get(last, "runnable").truthy() && text(last, "command").is_empty());
        assert_eq!(list(&e1, "commands").len(), 2);
        assert!(list(&e1, "commands").iter().all(|c| !get(c, "observed").truthy()), "only rb journal run observes a command");
        // each run's command is in the copied script by its kind, as a comment
        let seed2 = rec.id("seed2");
        for line in [
            "#   fp32 and int8 · seed 1: command (as given): python eval.py --seed 1",
            "#   fp32 and int8 · seed 2: command given later, not observed: python eval.py --seed 2",
        ] {
            assert!(e1_script.lines().any(|l| l == line), "{line} in {e1_script}");
        }
        assert!(!e1_script.lines().any(|l| l.starts_with("python eval.py")), "{e1_script}");
        // runs by their labels, never their ids; per-run settings by name in words
        assert!(!e1_script.contains(&seed1) && !e1_script.contains(&seed2), "{e1_script}");
        let own = list(&s1, "steps").last().unwrap();
        assert_eq!((text(own, "label"), text(own, "command"), get(own, "runnable").truthy()), ("command (as given)", "python eval.py --seed 1", true));
        let later = list(&s2, "steps").last().unwrap();
        assert_eq!(text(later, "label"), "command given later, not observed: python eval.py --seed 2");
        assert!(!get(later, "runnable").truthy());
        assert!(!text(&s2, "script").lines().any(|l| l == "python eval.py --seed 2"), "a command given later is never a line to run");
        // the recording step is a request in words
        assert_eq!(text(&e1, "prompt"), "run one more top1 measurement on INT8 against FP32 and record it in rb");
        assert_eq!(text(&s1, "prompt"), "");
        assert!(text(&e1, "ready_line").starts_with("to run again: "), "{}", text(&e1, "ready_line"));
    }

    #[test]
    fn made_recipe_names_its_one_config() {
        let rec = int8_record("made");
        let r = Recipes::new(&rec.led, Tier::Live).unwrap();
        let (seed1, c512, c1024) = (rec.id("seed1"), rec.id("c512"), rec.id("c1024"));
        // e1: the seed-1 run made the checkpoint, named with its arms and seed; seed 2 made nothing and gets no recipe
        let e1 = r.experiment(&rec.exp("e1")).unwrap();
        let made = list(&e1, "made");
        assert_eq!(made.len(), 1, "{made:?}");
        assert_eq!(text(&made[0], "line"), "fp32 and int8 · seed 1: made ckpt-int8.");
        assert_eq!((text(&made[0], "run"), text(&made[0], "maker"), text(&made[0], "resource")), (seed1.as_str(), seed1.as_str(), "ckpt-int8"));
        assert_eq!((text(&made[0], "command"), text(&made[0], "from")), ("python quantize.py --seed 1 --calib 512", "scripts/README.md:8"));
        assert!(text(&e1, "script").ends_with(&format!(
            "# fp32 and int8 · seed 1: made ckpt-int8\n# how to get it again · scripts/README.md:8\npython quantize.py --seed 1 --calib 512\n"
        )));
        // e2: the 512-image run recorded the bytes seed 1 wrote; the 1024-image run did not, and the recipe never reaches it
        let e2 = r.experiment(&rec.exp("e2")).unwrap();
        let made = list(&e2, "made");
        assert_eq!(made.len(), 1, "{made:?}");
        assert_eq!(text(&made[0], "line"), "int8 · calib images 512: the same bytes are recorded by fp32 and int8 · seed 1 (e1), which made ckpt-int8.");
        assert_eq!(text(&made[0], "run"), c512);
        assert!(!text(&e2, "script").contains("# int8 · calib images 1024:"), "{}", text(&e2, "script"));
        assert!(!text(&e2, "script").contains(&c512) && !text(&e2, "script").contains(&c1024), "{}", text(&e2, "script"));
        // a per-run setting by its name in words, as Runs says it
        assert!(text(&e2, "script").lines().any(|l| l.starts_with("# per run (") && l.contains("calib images") && !l.contains("calib_images")), "{}", text(&e2, "script"));
        let one = r.run(&rec.load_run("c512")).unwrap();
        assert_eq!(text(&list(&one, "made")[0], "line"), "The same bytes are recorded by fp32 and int8 · seed 1 (e1), which made ckpt-int8.");
        assert!(text(&one, "script").ends_with("# how to get it again · scripts/README.md:8\npython quantize.py --seed 1 --calib 512\n"), "{}", text(&one, "script"));
        let other = r.run(&rec.load_run("c1024")).unwrap();
        assert!(list(&other, "made").is_empty());
        assert!(!text(&other, "script").lines().any(|l| l == "python quantize.py --seed 1 --calib 512"));
        // a database's rows share its bytes, an empty file is anyone's: neither is the same bytes
        let (mut a, mut b) = (rec.load_run("seed1"), rec.load_run("c1024"));
        b.files = a.files.clone();
        assert!(same_bytes(&a, &b));
        let mut row = b.clone();
        row.receipt.row = Some(crate::record::RowRead {
            store: "sqlite".into(),
            path: "results/runs.db".into(),
            sha256: String::new(),
            table: "runs".into(),
            r#where: Dict::new(),
            rowid: Some(7),
            columns: IndexMap::new(),
            per_run: IndexMap::new(),
            produced_at: None,
            extra: Dict::new(),
        });
        assert!(same_bytes(&a, &row) && same_bytes(&row, &a), "a row-read run's own files are its bytes");
        // the database the row is read from is every row's: by its path, or by its bytes under another path
        let db = crate::record::FileRef { path: "results/runs.db".into(), sha256: "d".repeat(64), bytes: 4096 };
        let (mut typed, mut rowed) = (a.clone(), row.clone());
        rowed.files = vec![db.clone()];
        typed.files = vec![db.clone()];
        assert!(!same_bytes(&typed, &rowed) && !same_bytes(&rowed, &typed));
        rowed.receipt.row.as_mut().unwrap().sha256 = db.sha256.clone();
        typed.files[0].path = "copies/runs.db".into();
        assert!(!same_bytes(&typed, &rowed) && !same_bytes(&rowed, &typed));
        // two rows of the same database share no bytes by it
        let mut other_row = rowed.clone();
        other_row.id = "run-other-row".into();
        assert!(!same_bytes(&rowed, &other_row));
        // a row-read run that kept a checkpoint has the same bytes as a typed run holding that checkpoint
        rowed.files.push(a.files.iter().find(|f| f.bytes > 0).unwrap().clone());
        assert!(same_bytes(&a, &rowed) && same_bytes(&rowed, &a));
        // a run read from a row with no per-run value is named by the row
        row.per_run = Dict::new();
        assert_eq!(run_label(&rec.led, Some(&rec.exp("e2")), &row), "int8 · results/runs.db row 7");
        // a per-run setting by its name in words, as Runs says it, never its key; two last names alike say each whole
        row.per_run = [("seed".to_string(), Value::Int(1)), ("one_cycle".to_string(), Value::Int(1))].into_iter().collect();
        assert_eq!(run_label(&rec.led, Some(&rec.exp("e2")), &row), "int8 · seed 1 · one cycle 1");
        row.per_run = [("model.lr".to_string(), Value::Int(1)), ("optim.lr".to_string(), Value::Int(2))].into_iter().collect();
        assert_eq!(run_label(&rec.led, Some(&rec.exp("e2")), &row), "int8 · model lr 1 · optim lr 2");
        row.per_run = Dict::new();
        for f in a.files.iter_mut().chain(b.files.iter_mut()) {
            f.bytes = 0;
        }
        assert!(!same_bytes(&a, &b));
    }

    #[test]
    fn what_a_run_made_with_no_recipe_says_so_in_the_script() {
        let mut rec = int8_record("made-no-recipe");
        rec.led.add_resource(&NewResource { id: "trace-t4".into(), r#type: "artifact".into(), name: "trace-t4".into(), ..Default::default() }).unwrap();
        let t = rec.run(
            "trace",
            "e3",
            NewRun { metrics: metrics(&[("int8.latency_ms", 4.1)]), inputs: uses(&[("ckpt-int8", "model")]), outputs: uses(&[("trace-t4", "profile")]), ..Default::default() },
        );
        let r = Recipes::new(&rec.led, Tier::Live).unwrap();
        let apart = r.apart[&t].clone();
        for v in [r.experiment(&rec.exp("e3")).unwrap(), r.run(&rec.load_run("trace")).unwrap()] {
            let made = list(&v, "made");
            let [m] = made else { panic!("{made:?}") };
            assert!(text(m, "line").to_lowercase().ends_with("made trace-t4. no recipe is recorded for it."), "{m:?}");
            let script = text(&v, "script");
            assert!(script.ends_with(&format!("# int8{apart}: made trace-t4\n# how to get it again: no recipe recorded\n")), "{script}");
            assert!(!script.contains(&t), "{script}");
        }
    }

    #[test]
    fn an_input_without_a_recipe_is_a_sentence() {
        let rec = int8_record("sentence");
        let seed1 = rec.load_run("seed1");
        for tier in [Tier::Live, Tier::Owner] {
            let r = Recipes::new(&rec.led, tier).unwrap();
            let v = r.run(&seed1).unwrap();
            let step = list(&v, "steps").iter().find(|s| get(s, "refs").as_list().map_or(false, |l| l.first().and_then(Value::as_str) == Some("resnet50-fp32"))).expect("the weights' step");
            assert!(!get(step, "runnable").truthy() && text(step, "command").is_empty(), "{step:?}");
            // its address is the resource's own, on the tier that shows it: a recipe reads the same on both
            let said = "weights · resnet50-fp32: no recipe recorded";
            assert_eq!(text(step, "label"), said);
            let script = text(&v, "script");
            let at = script.lines().position(|l| l == format!("# {said}")).unwrap_or_else(|| panic!("{script}"));
            assert!(script.lines().nth(at + 1).map_or(true, |l| l.is_empty() || l.starts_with('#')), "nothing to run follows it: {script}");
            assert!(!pyjson::dumps(&v).contains("https://"), "a recipe holds no address of a resource");
            // the inputs that have one are commands, with where each was taken from
            let val = list(&v, "steps").iter().find(|s| text(s, "label") == "evaluation data · imagenet-val").expect("the evaluation data");
            assert_eq!((text(val, "command"), text(val, "from"), get(val, "runnable").truthy()), ("python get_data.py --split val", "scripts/README.md:5", true));
            assert!(script.contains("\n# evaluation data · imagenet-val · scripts/README.md:5\npython get_data.py --split val\n"));
        }
    }

    #[test]
    fn the_record_format_is_unchanged() {
        // building every recipe reads the record and writes nothing: its files are byte for byte as they were
        let rec = int8_record("format");
        // the record is what .rb/.gitignore leaves to git: rb's read cache, its lock and its journal are not
        let files = |root: &Path| -> Vec<(PathBuf, Vec<u8>)> {
            let mut out = Vec::new();
            let mut todo = vec![root.join(crate::ledger::DIR)];
            while let Some(d) = todo.pop() {
                for e in std::fs::read_dir(&d).unwrap().flatten() {
                    let p = e.path();
                    let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
                    if d == root.join(crate::ledger::DIR) && ["cache", "journal", ".lock"].contains(&name) || name.ends_with(".tmp") {
                        continue;
                    }
                    if p.is_dir() {
                        todo.push(p);
                    } else {
                        out.push((p.clone(), std::fs::read(&p).unwrap()));
                    }
                }
            }
            out.sort();
            out
        };
        let before = files(&rec.root);
        for tier in [Tier::Live, Tier::Owner] {
            let r = Recipes::new(&rec.led, tier).unwrap();
            for e in ["e1", "e2", "e3"] {
                r.experiment(&rec.exp(e)).unwrap();
            }
            for name in ["seed1", "seed2", "c512", "c1024", "t4"] {
                r.run(&rec.load_run(name)).unwrap();
            }
        }
        assert_eq!(files(&rec.root), before);
        // a recipe is computed from what a resource keeps: its source, address and recipe as given, nothing more
        let kept: Vec<&str> = crate::record::Acquisition::META.fields.iter().map(|f| f.name).collect();
        assert_eq!(kept, ["source", "url", "recipe"]);
    }

    #[test]
    fn runs_that_read_alike_are_told_apart_by_when_rb_recorded_them() {
        let rec = int8_record("apart");
        let seed1 = rec.load_run("seed1");
        let experiments: IndexMap<String, Experiment> = rec.led.all::<Experiment>().unwrap().iter().map(|e| (e.id.clone(), e.clone())).collect();
        let at = |id: &str, when: &str| {
            let mut r = seed1.clone();
            r.id = id.into();
            r.created_at = when.into();
            r.receipt.at = when.into();
            r
        };
        // three runs of e1 that read "fp32 and int8 · seed 1": two recorded in one minute, one in another
        let runs = vec![at("rb", "2026-10-26T14:51:40+00:00"), at("ra", "2026-10-26T14:51:10+00:00"), at("rc", "2026-10-26T16:02:00+02:00"), rec.load_run("seed2")];
        let a = apart(&rec.led, &experiments, &runs);
        assert_eq!(a["ra"], " · recorded 2026-10-26 14:51 UTC, the 1st of 2 that minute");
        assert_eq!(a["rb"], " · recorded 2026-10-26 14:51 UTC, the 2nd of 2 that minute");
        assert_eq!(a["rc"], " · recorded 2026-10-26 14:02 UTC");
        // a run whose label is its own is said by its label alone
        assert!(!a.contains_key(&rec.id("seed2")), "{a:?}");
        assert_eq!([1, 2, 3, 4, 11, 12, 13, 21, 22, 112].map(ordinal), ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "112th"]);
    }

    #[test]
    fn a_run_s_recipe_is_its_own() {
        let rec = int8_record("narrow");
        let r = Recipes::new(&rec.led, Tier::Live).unwrap();
        let seed2 = rec.id("seed2");
        let v = r.run(&rec.load_run("seed2")).unwrap();
        let code = list(&v, "ready").iter().find(|x| text(x, "label") == "code").unwrap();
        assert_eq!(text(code, "text"), "unknown · the run was made before it was recorded, and names no commit");
        assert_eq!(get(&v, "per_run.keys"), &Value::list_of_str(["seed".to_string()]));
        for arm in ["fp32", "int8"] {
            assert_eq!(get(&v, &format!("per_run.by_arm.{arm}")), &Value::List(vec![crate::obj! {"run" => seed2.clone(), "seed" => "2"}]));
        }
        assert_eq!(list(&v, "commands").len(), 1);
        assert_eq!(text(&v, "command_as_given.line"), "the run records a command given later, not observed");
        assert_eq!((get(&v, "command_as_given.recorded"), get(&v, "command_as_given.runs")), (&Value::Int(1), &Value::Int(1)));
        // its settings are the ones it was recorded with
        let shared: Vec<&str> = list(&v, "settings.shared").iter().map(|s| text(s, "name")).collect();
        assert_eq!(shared, ["batch_size"]);
        assert_eq!(text(&list(&v, "settings.by_arm.int8")[0], "value"), "int8");
        assert!(text(&v, "script").starts_with(&format!("# Reproduce a run of e1 · fp32 and int8 · seed 2\n{FROM_THE_RECORD}\n")));
        // its per-run values in words, as Runs says them; its id nowhere in what the page shows
        assert!(text(&v, "script").contains("\n# per run: seed 2\n"), "{}", text(&v, "script"));
        assert!(!text(&v, "script").contains(&seed2), "{}", text(&v, "script"));
        // only its own input: the evaluation data, no calibration images and no model
        let refs: Vec<String> = list(&v, "steps").iter().flat_map(|s| list(s, "refs").iter().filter_map(Value::as_str).map(String::from).collect::<Vec<_>>()).collect();
        assert!(refs.contains(&"imagenet-val".to_string()) && !refs.iter().any(|x| x == "imagenet-calib" || x == "ckpt-int8"), "{refs:?}");
    }

    /// The record at a later commit `later`, with `results/<name>.json` for each name committed in it.
    fn at_a_later_commit(rec: &Rec, names: &[&str]) -> String {
        rec.write("eval.py", "print('top-1 on the validation set')\n");
        // each file its own bytes: a file with another run's bytes is that run again
        for (i, n) in names.iter().enumerate() {
            rec.write(&format!("results/{n}.json"), &format!("{{\"fp32\": {{\"top1\": 0.761{i}}}, \"int8\": {{\"top1\": 0.757{i}}}}}\n"));
        }
        git(&rec.root, &["add", "-A"]);
        git(&rec.root, &["commit", "-qm", "the evaluation script"]);
        // rb asks git once per command; this test's process goes on writing after a commit, as two commands would
        sources::forget_git();
        git(&rec.root, &["rev-parse", "HEAD"])
    }

    fn e1_run(file: &str, seed: i64, commit: Option<&str>) -> NewRun {
        NewRun {
            metrics: metrics(&[("fp32.top1", 0.7611), ("int8.top1", 0.7578)]),
            files: vec![file.into()],
            per_run: crate::dict! {"seed" => seed},
            inputs: uses(&[("imagenet-val", "eval")]),
            commit: commit.map(String::from),
            ..Default::default()
        }
    }

    #[test]
    fn a_checkout_is_only_of_the_code_a_receipt_holds() {
        let mut rec = int8_record("checkout");
        let earlier = git(&rec.root, &["rev-parse", "HEAD"]);
        let later = at_a_later_commit(&rec, &["seed9", "seed10"]);
        // recorded as it was made at a clean tree at the later commit, its receipt naming the earlier one as the code
        // that made its numbers: the checkout it was recorded in did not make them, and rb never saw the earlier run
        rec.run("seed9", "e1", e1_run("results/seed9.json", 9, Some(&earlier)));
        // the same, naming the checkout it was recorded in: its receipt holds that code
        rec.run("seed10", "e1", e1_run("results/seed10.json", 10, Some(&later)));
        let r = Recipes::new(&rec.led, Tier::Live).unwrap();
        let v9 = r.run(&rec.load_run("seed9")).unwrap();
        let whole = pyjson::dumps(&v9);
        assert!(!whole.contains("git worktree") && !whole.contains(&later[..7]), "{whole}");
        let code = &list(&v9, "steps")[0];
        assert!(text(code, "what") == "code" && !get(code, "runnable").truthy(), "{code:?}");
        // what a rerun needs is the code it names, as rb status and rb show will say it
        let row = list(&v9, "ready").iter().find(|x| text(x, "label") == "code").cloned().unwrap();
        assert_eq!(text(&row, "text"), format!("ok · commit {}", &earlier[..10]));
        let v10 = r.run(&rec.load_run("seed10")).unwrap();
        assert_eq!(text(&list(&v10, "steps")[0], "command"), format!("git worktree add --detach ../e1-{} {later}", &later[..7]));
        assert_eq!(text(&list(&v10, "steps")[0], "label"), format!("code · commit {}, which fp32 and int8 · seed 10 ran at", &later[..10]));
    }

    #[test]
    fn a_command_rb_journal_run_watched_holds_the_code_it_names() {
        let mut rec = int8_record("watched");
        let earlier = git(&rec.root, &["rev-parse", "HEAD"]);
        let later = at_a_later_commit(&rec, &[]);
        // rb journal run watched the command write its file; its proposal names the commit it ran at
        rec.write("out/bench.json", "{\"int8\": {\"latency_ms\": 3.8}}\n");
        let journal = crate::journal::read_dir(&rec.led.root);
        std::fs::create_dir_all(&journal).unwrap();
        let entry = crate::obj! {"op" => "run", "command" => "python bench.py", "wrote" => Value::list_of_str(["out/bench.json".to_string()]), "at" => crate::pytime::now()};
        std::fs::write(journal.join("journal.jsonl"), pyjson::dumps(&entry) + "\n").unwrap();
        let id = rec.run(
            "bench",
            "e3",
            NewRun {
                metrics: metrics(&[("int8.latency_ms", 3.8)]),
                files: vec!["out/bench.json".into()],
                command: Some("python bench.py".into()),
                commit: Some(earlier.clone()),
                ..Default::default()
            },
        );
        let run = rec.load_run("bench");
        assert_eq!(support::run_command(&run).map(|g| g.given), Some("observed"));
        let v = Recipes::new(&rec.led, Tier::Live).unwrap().run(&run).unwrap();
        let code = &list(&v, "steps")[0];
        assert_eq!(text(code, "command"), format!("git worktree add --detach ../e3-{} {earlier}", &earlier[..7]));
        assert!(!pyjson::dumps(&v).contains(&later[..7]), "{}", pyjson::dumps(&v));
        // its command step says how rb knows the command made the numbers
        let last = list(&v, "steps").last().unwrap();
        assert!(text(last, "label").starts_with("command (as given) · rb journal run watched it make these numbers, "), "{last:?}");
        assert!(get(last, "runnable").truthy() && text(last, "command") == "python bench.py");
        assert!(text(&v, "script").contains("\n# command (as given) · rb journal run watched it make these numbers, "), "{}", text(&v, "script"));
        let r3 = Recipes::new(&rec.led, Tier::Live).unwrap();
        let e3 = r3.experiment(&rec.exp("e3")).unwrap();
        // another run of e3 reads "int8" too: when rb recorded each tells them apart
        let apart = &r3.apart[&id];
        assert!(apart.starts_with(" · recorded "), "{apart}");
        assert!(text(&e3, "script").contains(&format!("\n#   int8{apart}: command (as given): python bench.py (rb journal run watched it make these numbers, ")), "{}", text(&e3, "script"));
        assert!(!text(&e3, "script").contains(&id), "{}", text(&e3, "script"));
        assert_eq!(list(&e3, "commands").iter().filter(|c| get(c, "observed").truthy()).count(), 1);
    }

    #[test]
    fn a_worktree_takes_the_uncommitted_changes_rb_kept() {
        let mut rec = int8_record("kept");
        rec.write("quantize.py", "CALIB = 512\n");
        git(&rec.root, &["add", "-A"]);
        git(&rec.root, &["commit", "-qm", "the quantizer"]);
        let head = git(&rec.root, &["rev-parse", "HEAD"]);
        // run with a change nobody committed: its receipt keeps the tree it ran with
        rec.write("quantize.py", "CALIB = 1024\n");
        sources::forget_git();
        rec.run("dirty", "e3", NewRun { metrics: metrics(&[("int8.latency_ms", 4.4)]), inputs: uses(&[("ckpt-int8", "model")]), ..Default::default() });
        let run = rec.load_run("dirty");
        let at = run.receipt.attached.clone().unwrap();
        assert!(at.get("dirty").map_or(false, Value::truthy), "{at:?}");
        let tree = pyjson::py_str(at.get("tree").unwrap());
        let r = Recipes::new(&rec.led, Tier::Live).unwrap();
        let apart = r.apart[&run.id].clone();
        let v = r.run(&run).unwrap();
        let code = &list(&v, "steps")[0];
        let dir = format!("../e3-{}-{}", &head[..7], &tree[..7]);
        assert_eq!(text(code, "command"), format!("git worktree add --detach {dir} {head}\ngit -C {dir} read-tree -u --reset {tree}"));
        assert_eq!(text(code, "label"), format!("code · commit {} with the uncommitted changes rb kept as git tree {}, which int8{apart} ran at", &head[..10], &tree[..10]));
        // run as given, the recipe gives back the code the run ran with
        let made = rec.root.join(&dir);
        for line in text(code, "command").lines() {
            let argv: Vec<&str> = line.split(' ').collect();
            assert_eq!(argv[0], "git");
            git(&rec.root, &argv[1..]);
        }
        let back = std::fs::read_to_string(made.join("quantize.py"));
        let tree_back = git(&made, &["write-tree"]);
        let clean = Command::new("git").args(["diff", "--quiet"]).current_dir(&made).status().map(|s| s.success());
        let _ = std::fs::remove_dir_all(&made);
        assert_eq!(back.unwrap(), "CALIB = 1024\n");
        assert_eq!(tree_back, tree);
        assert_eq!(clean.unwrap(), true);
    }

    #[test]
    fn readiness_is_computed_on_each_read() {
        // guarantee 20: what a rerun needs is where things stand at this read, never at an earlier one's
        let mut rec = int8_record("per-read");
        rec.write("data/calib.txt", "512 calibration images\n");
        rec.led
            .add_resource(&NewResource { id: "calib-local".into(), r#type: "dataset".into(), name: "calib-local".into(), locations: vec![("local".into(), rec.root.join("data/calib.txt").to_string_lossy().into_owned())], ..Default::default() })
            .unwrap();
        rec.run("local", "e2", NewRun { metrics: metrics(&[("int8.top1", 0.7583)]), per_run: crate::dict! {"calib_images" => 256}, inputs: uses(&[("calib-local", "train")]), ..Default::default() });
        let rows = |name: &str| -> Vec<(String, String)> {
            let v = Recipes::new(&rec.led, Tier::Live).unwrap().run(&rec.load_run(name)).unwrap();
            list(&v, "ready").iter().map(|x| (text(x, "label").to_string(), text(x, "text").to_string())).collect()
        };
        let row = |rows: &[(String, String)], label: &str| rows.iter().find(|(l, _)| l == label).map(|(_, t)| t.clone()).unwrap_or_default();
        let (s1, local) = (rows("seed1"), rows("local"));
        assert_eq!(row(&s1, "files"), "ok · the only file the runs named is here unchanged");
        assert!(row(&local, "training data").starts_with("ok · calib-local: present"), "{local:?}");
        std::fs::remove_file(rec.root.join("results/seed1.json")).unwrap();
        std::fs::remove_file(rec.root.join("data/calib.txt")).unwrap();
        assert_eq!(row(&rows("seed1"), "files"), "missing · no file the runs named is here unchanged; not here: results/seed1.json");
        assert!(row(&rows("local"), "training data").starts_with("missing · calib-local: lost"), "{:?}", rows("local"));
    }

    #[test]
    fn the_made_by_walk_says_where_it_stops() {
        // x0 makes x1 makes ... x5, each by a run of its own; a run reads x5: the recipe follows its makers 4 back
        let mut rec = int8_record("walk");
        for i in 0..=5 {
            let id = format!("x{i}");
            rec.led.add_resource(&NewResource { id: id.clone(), r#type: "checkpoint".into(), name: id, ..Default::default() }).unwrap();
        }
        let names = ["m1", "m2", "m3", "m4", "m5"];
        for (i, name) in names.iter().enumerate() {
            let (read, made) = (format!("x{i}"), format!("x{}", i + 1));
            rec.run(name, "e3", NewRun { metrics: metrics(&[("int8.latency_ms", 5.0 + i as f64)]), inputs: uses(&[(read.as_str(), "train")]), outputs: uses(&[(made.as_str(), "checkpoint")]), ..Default::default() });
        }
        rec.run("reader", "e3", NewRun { metrics: metrics(&[("int8.latency_ms", 3.7)]), inputs: uses(&[("x5", "model")]), ..Default::default() });
        let r = Recipes::new(&rec.led, Tier::Live).unwrap();
        let v = r.run(&rec.load_run("reader")).unwrap();
        let steps = labels(&v);
        let (m1, m2) = (rec.id("m1"), rec.id("m2"));
        // the runs by their labels in the words, each "int8" told apart by when rb recorded it; their ids in refs,
        // where a link needs them
        let (a1, a2) = (&r.apart[&m1], &r.apart[&m2]);
        assert!(a1 != a2 && a1.starts_with(" · recorded ") && a2.starts_with(" · recorded "), "{a1} {a2}");
        let line = format!("training data · x1, which int8{a2} read to make x2 · made by int8{a1}; the recipe stops here, 4 makers back: no recipe recorded");
        let at = steps.iter().position(|l| *l == line).unwrap_or_else(|| panic!("{line} in {steps:?}"));
        assert_eq!(list(&list(&v, "steps")[at], "refs"), &[Value::str("x1"), Value::str(&m2), Value::str(&m1)]);
        assert!(!steps.iter().any(|s| s.contains("x0")), "{steps:?}");
        assert!(text(&v, "script").contains("; the recipe stops here, 4 makers back"));
    }

    #[test]
    fn readiness_is_rerunnability_s_rule() {
        // Ledger::rerunnability gives rb status and rb show an experiment's rows; Reproduce gives a run's by the same
        // rule, so the two must agree row for row on every experiment
        let rec = int8_record("parity");
        let r = Recipes::new(&rec.led, Tier::Live).unwrap();
        for e in ["e1", "e2", "e3"] {
            let exp = rec.exp(e);
            let runs: Vec<&Run> = r.runs.iter().filter(|x| x.experiment == e && standing(x)).collect();
            let ours = rerunnability(&rec.led, &runs).unwrap();
            assert_eq!(ours, rec.led.rerunnability(&exp).unwrap(), "{e}");
            assert!(!ours.is_empty());
        }
    }

    #[test]
    fn rows_that_say_the_same_thing_read_as_one() {
        let row = |what: &str, res: &str| Ready { what: what.into(), state: "partial".into(), detail: String::new(), resource: Some(res.into()), avail: "reconstructible".into(), also: vec!["identity: name only".into()] };
        let rows = vec![Ready::plain("code", "unknown", "no run names its commit".into()), row("eval", "val-a"), row("eval", "val-b"), row("train", "train-a")];
        let m = merged(&rows);
        assert_eq!(m.iter().map(|x| (x.0.as_str(), x.1.as_str())).collect::<Vec<_>>(), [
            ("code", "unknown · no run names its commit"),
            ("evaluation data", "partial · val-a, val-b: reconstructible · identity: name only"),
            ("training data", "partial · train-a: reconstructible · identity: name only"),
        ]);
        assert_eq!(m[1].3, ["val-a", "val-b"]);
        assert_eq!(ready_line(&m), "to run again: ◐ 2 partial · ○ 1 unknown");
        assert_eq!(ready_line(&[]), "");
    }

    #[test]
    fn a_comment_in_a_recipe_never_starts_a_line_of_its_own() {
        assert_eq!(one_line("ImageNet\nrm -rf data"), "ImageNet\\nrm -rf data");
        assert_eq!(one_line("val\u{202e}set"), "val\\u202eset");
        let rec = int8_record("lines");
        rec.led
            .add_resource(&NewResource { id: "calib-extra".into(), r#type: "dataset".into(), name: "calib-extra".into(), source: "the lab share\npython steal.py".into(), ..Default::default() })
            .unwrap();
        let mut run = rec.load_run("seed2");
        run.inputs.push(crate::record::ResourceUse { resource: "calib-extra".into(), role: "eval".into(), availability: None, sha256: None });
        let v = Recipes::new(&rec.led, Tier::Live).unwrap().run(&run).unwrap();
        assert!(!text(&v, "script").lines().any(|l| l == "python steal.py"), "{}", text(&v, "script"));
        assert!(text(&v, "script").contains("# evaluation data · calib-extra: no recipe recorded · the lab share\\npython steal.py\n"));
    }

    /// The contract's shape (tests/fixtures/shell/view-1.shape.tsv): each path a recipe holds, with its JSON type.
    fn shape() -> HashMap<String, BTreeSet<String>> {
        include_str!("../../tests/fixtures/shell/view-1.shape.tsv")
            .lines()
            .skip(1)
            .filter_map(|l| {
                let mut f = l.split('\t');
                Some((f.next()?.to_string(), f.next()?.split('|').map(String::from).collect()))
            })
            .collect()
    }

    fn kind(v: &Value) -> &'static str {
        match v {
            Value::Dict(_) => "object",
            Value::List(_) => "list",
            Value::Str(_) => "string",
            Value::Int(_) | Value::Float(_) => "number",
            Value::Bool(_) => "bool",
            Value::Null => "null",
            _ => "other",
        }
    }

    /// Every path of `v` as the shape names it: a map of arms is `{key}`, and so is a per-run value but the run's id.
    fn paths(v: &Value, at: &str, out: &mut Vec<(String, &'static str)>) {
        out.push((at.to_string(), kind(v)));
        match v {
            Value::Dict(d) => {
                for (k, x) in d {
                    let p = if at.ends_with(".by_arm") || (at.ends_with(".per_run.by_arm.{key}[]") && k != "run") { format!("{at}.{{key}}") } else { format!("{at}.{k}") };
                    paths(x, &p, out);
                }
            }
            Value::List(l) => {
                for x in l {
                    paths(x, &format!("{at}[]"), out);
                }
            }
            _ => {}
        }
    }

    #[test]
    fn a_recipe_meets_the_contract_s_shape() {
        let rec = int8_record("shape");
        let shape = shape();
        for tier in [Tier::Live, Tier::Owner] {
            let r = Recipes::new(&rec.led, tier).unwrap();
            let mut seen: Vec<(String, &'static str)> = Vec::new();
            for e in ["e1", "e2", "e3"] {
                paths(&r.experiment(&rec.exp(e)).unwrap(), "experiments[].reproduce", &mut seen);
            }
            for name in ["seed1", "seed2", "c512", "c1024", "t4"] {
                paths(&r.run(&rec.load_run(name)).unwrap(), "runs.{id}.reproduce", &mut seen);
            }
            for (p, k) in &seen {
                let types = shape.get(p).unwrap_or_else(|| panic!("{p} is outside the contract's shape"));
                assert!(types.contains(*k), "{p} is a {k}; the shape says {types:?}");
            }
            // every field the shape gives a recipe is there
            for p in shape.keys().filter(|p| p.starts_with("experiments[].reproduce.") && p.matches('.').count() == 2) {
                assert!(seen.iter().any(|(s, _)| s == p), "{p} missing");
            }
        }
    }

    /// The resources' view on the INT8 record: what each was read as, and its recipe handed apart.
    fn resource_fields(rec: &Rec) -> IndexMap<String, (String, Value)> {
        acquisition::fields(&rec.led).unwrap().into_iter().map(|(id, d)| (id, (pyjson::py_str(d.get("role").unwrap()), d.get("acquisition").unwrap().clone()))).collect()
    }

    #[test]
    fn a_resource_says_what_it_was_read_as_and_how_to_get_it_again() {
        let mut rec = int8_record("acquired");
        let log = rec.led.log_entries().unwrap().len();
        let got = |c: &str, f: &str| crate::obj! {"command" => c, "from" => f};
        let f = resource_fields(&rec);
        assert_eq!(f["imagenet-val"], ("evaluation data".to_string(), got("python get_data.py --split val", "scripts/README.md:5")));
        assert_eq!(f["imagenet-calib"], ("training data".to_string(), got("python get_data.py --calib 512", "scripts/README.md:6")));
        // an address is no recipe: nothing to run; a role rb has no words for reads as recorded
        assert_eq!(f["resnet50-fp32"], ("weights".to_string(), Value::Null));
        assert_eq!(f["ckpt-int8"], ("model".to_string(), got("python quantize.py --seed 1 --calib 512", "scripts/README.md:8")));
        // computed on read: a run taken back reads nothing, and a second role joins the first in rb's order
        rec.led.retract(&rec.id("t4"), "timed on the wrong GPU", None).unwrap();
        rec.run("val-train", "e2", NewRun { metrics: metrics(&[("int8.top1", 0.7590)]), per_run: crate::dict! {"calib_images" => 2048}, inputs: uses(&[("imagenet-val", "train")]), ..Default::default() });
        let f = resource_fields(&rec);
        assert_eq!(f["ckpt-int8"].0, "", "only a run taken back read it; seed 1 made it");
        assert_eq!(f["imagenet-val"].0, "training data and evaluation data");
        // never stored: reading the fields wrote nothing (the two writes above are the only new lines)
        assert_eq!(rec.led.log_entries().unwrap().len(), log + 2);
        // what an analysis reads counts, one taken back does not, nor does a run of an experiment taken back; a role in
        // another case is the same role, and a run taken back for one measurement still read what it read
        for (id, source, recipe) in [
            ("imagenet-v2", "", None),
            ("imagenet-r", "", None),
            ("imagenet-a", "", None),
            ("calib-extra", "the lab share", Some("python fetch.py --calib extra")),
        ] {
            rec.led.add_resource(&NewResource { id: id.into(), r#type: "dataset".into(), name: id.into(), source: source.into(), recipe: recipe.map(String::from), ..Default::default() }).unwrap();
        }
        let cmp = |e: f64| vec![crate::dict! {"subject" => "int8", "reference" => "fp32", "metric" => "top1", "estimate" => e}];
        rec.led.add_analysis("e1", NewAnalysis { comparisons: cmp(-0.003), method: "paired bootstrap over images".into(), inputs: uses(&[("imagenet-v2", "eval"), ("calib-extra", "eval")]), ..Default::default() }).unwrap();
        let gone = rec.led.add_analysis("e1", NewAnalysis { comparisons: cmp(-0.004), method: "a pooled estimate".into(), inputs: uses(&[("imagenet-r", "eval")]), ..Default::default() }).unwrap().record.id;
        rec.led.retract(&gone, "read the wrong split", None).unwrap();
        rec.led.add_experiment(&NewExperiment { title: "INT8 on adversarial images".into(), id: Some("e5".into()), purpose: "top-1 on ImageNet-A".into(), candidates: vec!["int8".into()], ..Default::default() }).unwrap();
        rec.run("adv", "e5", NewRun { metrics: metrics(&[("int8.top1", 0.031)]), inputs: uses(&[("imagenet-a", "eval")]), ..Default::default() });
        rec.led.retract("e5", "ImageNet-A belongs to another study", None).unwrap();
        rec.run("calib-case", "e2", NewRun { metrics: metrics(&[("int8.top1", 0.7588)]), per_run: crate::dict! {"calib_images" => 4096}, inputs: uses(&[("imagenet-calib", "Train"), ("calib-extra", " ")]), ..Default::default() });
        rec.led.retract(&rec.id("seed1"), "top-1 read with the wrong crop", Some(&["top1".to_string()])).unwrap();
        let f = resource_fields(&rec);
        assert_eq!(f["imagenet-v2"].0, "evaluation data");
        assert_eq!((f["imagenet-r"].0.as_str(), f["imagenet-a"].0.as_str()), ("", ""));
        assert_eq!(f["imagenet-calib"].0, "training data");
        assert_eq!(f["calib-extra"], ("evaluation data".to_string(), got("python fetch.py --calib extra", "the lab share")), "a blank role adds nothing; a recipe with no citation is from its source words");
        assert_eq!(f["resnet50-fp32"].0, "weights", "seed 1 taken back for top-1 alone still read the weights");
        // roles rb has no words for read in the order they were recorded, whatever order their random ids fall in
        rec.led.add_resource(&NewResource { id: "probe".into(), r#type: "checkpoint".into(), name: "probe".into(), ..Default::default() }).unwrap();
        let first = rec.run("probe-a", "e3", NewRun { metrics: metrics(&[("int8.latency_ms", 4.1)]), inputs: uses(&[("probe", "weights")]), ..Default::default() });
        for n in 0..40 {
            let later = rec.led.add_run("e3", NewRun { metrics: metrics(&[("int8.latency_ms", 4.2 + n as f64 / 100.0)]), inputs: uses(&[("probe", "model")]), ..Default::default() }).unwrap().record;
            assert!(later.created_at > rec.load_run("probe-a").created_at, "recorded later reads later");
            if later.id < first {
                break;
            }
        }
        assert_eq!(resource_fields(&rec)["probe"].0, "weights and model");
        // the contract's shape holds them
        let shape = shape();
        for (_, (_, acq)) in resource_fields(&rec) {
            assert!(shape["resources.{id}.acquisition"].contains(kind(&acq)), "{acq:?}");
            if let Value::Dict(d) = acq {
                for k in d.keys() {
                    assert!(shape.contains_key(&format!("resources.{{id}}.acquisition.{k}")), "{k}");
                }
            }
        }
        assert!(shape["resources.{id}.role"].contains("string"));
    }

    /// The brief of a question, and rb status's open items, as the shell's model reads them.
    fn next_of(rec: &Rec, q: &str) -> String {
        let home = crate::view::home(&rec.led, None).unwrap();
        let b = home.briefs.iter().find(|b| b.id.as_deref() == Some(q)).expect("the question's brief");
        let st = Value::Dict(rec.led.status(None).unwrap());
        next_step::of(&rec.led, b, st.get("open").and_then(Value::as_list).map(|l| l.as_slice()).unwrap_or(&[])).unwrap()
    }

    /// The INT8 record with a rule that needs three seeds of e1, which has two: rb suggests a third run of e1 for q1.
    fn wants_a_third_seed(tag: &str) -> Rec {
        let rec = int8_record(tag);
        rec.led
            .add_criterion(&NewCriterion {
                statement: "INT8 loses at most a point of top-1 over three seeds".into(),
                experiment: "e1".into(),
                metric: "change.top1".into(),
                comparator: "at_least".into(),
                target: Some(-0.01),
                min_n: Value::Int(3),
                questions: vec!["q1".into()],
                id: Some("c3".into()),
                ..Default::default()
            })
            .unwrap();
        rec
    }

    #[test]
    fn next_step_is_a_request_in_words() {
        let rec = wants_a_third_seed("next");
        let said = next_of(&rec, "q1");
        // past the brief's three commands (settings to check come first), rb suggests a run for q1, in the brief's scope:
        // every run the brief lists is one of them, in the same order
        let home = crate::view::home(&rec.led, None).unwrap();
        let q1 = home.briefs.iter().find(|b| b.id.as_deref() == Some("q1")).unwrap();
        let st = Value::Dict(rec.led.status(None).unwrap());
        let open = st.get("open").and_then(Value::as_list).unwrap();
        let runs: Vec<String> = next_step::runs_suggested(q1, open).iter().map(|i| pyjson::py_str(i.get("do").unwrap())).collect();
        let listed: Vec<&String> = q1.next.iter().filter(|d| d.starts_with("rb run add ")).collect();
        assert!(!runs.is_empty() && runs.iter().all(|d| d.starts_with("rb run add ")), "{runs:?}");
        assert!(runs.iter().take(listed.len()).eq(listed.iter().copied()), "{runs:?} against {listed:?}");
        assert!(runs[0].starts_with("rb run add e1 "), "{runs:?}");
        // the words of rb's first suggested run: its metric, the short title, the arms as the suggestion names them
        assert_eq!(said, "run one more top1 measurement on INT8 against FP32 and record it in rb");
        for no in ["rb run add", "--", "<value>", "seed"] {
            assert!(!said.contains(no), "{no} in {said}");
        }
        // a question with nothing to run says none, and a question decided otherwise than open says none either
        rec.led.add_question("Does INT8 change latency on a CPU?", Some("q2"), None).unwrap();
        assert_eq!(next_of(&rec, "q2"), "");
        // a call is made where rb's user records it; an agent's runtime (GitHub Actions' CI too) records it proposed, which
        // changes nothing until it is confirmed, so the question keeps its Next step there
        let asks = "run one more top1 measurement on INT8 against FP32 and record it in rb";
        let made = |d: &crate::record::Decision| rec.led.authority(&d.id).unwrap().unwrap().is_call();
        let d = rec.led.decide("q1", "park", "the GPUs are taken this week", "", None).unwrap();
        assert_eq!(next_of(&rec, "q1"), if made(&d) { "" } else { asks });
        // nor does an open question whose experiment is parked: rb does not ask for a run of a line set aside; one under
        // investigation still asks
        let rec = wants_a_third_seed("next-parked");
        rec.led.decide("e1", "investigate", "one more seed first", "", None).unwrap();
        assert_eq!(next_of(&rec, "q1"), asks);
        let d = rec.led.decide("e1", "park", "the GPUs are taken this week", "", None).unwrap();
        let made = rec.led.authority(&d.id).unwrap().unwrap().is_call();
        assert_eq!(next_of(&rec, "q1"), if made { "" } else { asks });
    }

    /// The states a question's Next step reads, whoever's runtime records the calls: the question's and its
    /// experiments' as the brief says them, which only a call made sets.
    #[test]
    fn a_next_step_asks_only_while_its_line_moves() {
        let rec = wants_a_third_seed("next-states");
        let asks = "run one more top1 measurement on INT8 against FP32 and record it in rb";
        let home = crate::view::home(&rec.led, None).unwrap();
        let st = Value::Dict(rec.led.status(None).unwrap());
        let open = st.get("open").and_then(Value::as_list).unwrap();
        let q1 = home.briefs.iter().find(|b| b.id.as_deref() == Some("q1")).unwrap();
        let said = |q: &crate::view::QuestionBrief| next_step::of(&rec.led, q, open).unwrap();
        assert_eq!(said(q1), asks);
        for (question, experiment, then) in [
            ("parked", "open", ""),
            ("answered", "open", ""),
            ("stopped · since 2026-10-10", "open", ""),
            ("open", "parked", ""),
            ("open", "shipped", ""),
            ("open", "investigating", asks),
            ("open", "planned", asks),
            ("investigating", "open", asks),
        ] {
            let mut q = q1.clone();
            q.status = question.into();
            q.experiments.iter_mut().filter(|e| e.id == "e1").for_each(|e| e.state = experiment.into());
            assert_eq!(said(&q), then, "question {question}, e1 {experiment}");
        }
    }

    #[test]
    fn a_next_step_names_its_metric_whole_and_reads_a_folded_item() {
        let mut rec = int8_record("dotted");
        // a metric whose own name holds a dot, as a nested key gives it: named whole
        rec.led
            .add_criterion(&NewCriterion {
                statement: "INT8 keeps the summary's top-1 within a point over three seeds".into(),
                experiment: "e1".into(),
                metric: "change.summary.top1".into(),
                comparator: "at_least".into(),
                target: Some(-0.01),
                min_n: Value::Int(3),
                questions: vec!["q1".into()],
                id: Some("c5".into()),
                ..Default::default()
            })
            .unwrap();
        rec.run("summary", "e1", NewRun { metrics: metrics(&[("fp32.summary.top1", 0.7613), ("int8.summary.top1", 0.7581)]), per_run: crate::dict! {"seed" => 3}, ..Default::default() });
        assert_eq!(next_of(&rec, "q1"), "run one more summary.top1 measurement on INT8 against FP32 and record it in rb");
        // an item that folds an experiment's criteria (a late one) names the criterion by its `criteria`
        let home = crate::view::home(&rec.led, None).unwrap();
        let q1 = home.briefs.iter().find(|b| b.id.as_deref() == Some("q1")).unwrap();
        let late = vec![crate::obj! {"code" => "late", "subject" => "e1", "do" => "rb run add e1 fp32.summary.top1=<value> int8.summary.top1=<value>   (both arms, run after the declaration)", "criteria" => Value::list_of_str(["c5".to_string()])}];
        assert_eq!(next_step::of(&rec.led, q1, &late).unwrap(), "run one more summary.top1 measurement on INT8 against FP32 and record it in rb");
        // Reproduce's request names it whole too, once it is the rule the experiment's prompt reads
        rec.led.retract("c1", "top-1 is read from the summary now", None).unwrap();
        let r = Recipes::new(&rec.led, Tier::Owner).unwrap();
        assert_eq!(text(&r.experiment(&rec.exp("e1")).unwrap(), "prompt"), "run one more summary.top1 measurement on INT8 against FP32 and record it in rb");
    }

    #[test]
    fn a_next_step_names_the_settings_its_criterion_selects() {
        let rec = int8_record("scope");
        rec.led.add_question("Does a larger calibration set keep more top-1?", Some("q2"), None).unwrap();
        rec.led.add_experiment(&NewExperiment {
            title: "Calibration images at batch 256: a sweep".into(),
            id: Some("e4".into()),
            question: Some("q2".into()),
            purpose: "top-1 against calibration images".into(),
            candidates: vec!["int8".into()],
            ..Default::default()
        })
        .unwrap();
        for name in ["calib_images", "batch_size"] {
            rec.led.set_setting("e4", name, SetSetting { per_run: Some(true), ..Default::default() }).unwrap();
        }
        rec.led.add_criterion(&NewCriterion {
            statement: "256 calibration images keep top-1 at least as high as 128".into(),
            experiment: "e4".into(),
            metric: "top1".into(),
            subject: "int8".into(),
            reference: "int8".into(),
            comparator: "at_least".into(),
            target: Some(0.0),
            min_n: Value::Int(3),
            r#where: crate::dict! {"batch_size" => 256},
            subject_where: crate::dict! {"calib_images" => 256},
            reference_where: crate::dict! {"calib_images" => 128},
            questions: vec!["q2".into()],
            id: Some("c4".into()),
            ..Default::default()
        })
        .unwrap();
        assert_eq!(next_of(&rec, "q2"), "run one more top1 measurement on Calibration images at batch 256, at batch_size=256, calib_images 128 and 256, and record it in rb");
        // Reproduce's request reads the same runs: a variant against itself is named by its settings
        let r = Recipes::new(&rec.led, Tier::Owner).unwrap();
        assert_eq!(text(&r.experiment(&rec.exp("e4")).unwrap(), "prompt"), "run one more top1 measurement on Calibration images at batch 256, at batch_size=256, calib_images 128 and 256, and record it in rb");
        // two arms: the request names them as the criterion compares them, with the runs it selects
        rec.led
            .add_criterion(&NewCriterion { statement: "INT8 loses at most a point on seed 1".into(), experiment: "e1".into(), metric: "change.top1".into(), comparator: "at_least".into(), target: Some(-0.01), r#where: crate::dict! {"seed" => 1}, id: Some("c7".into()), ..Default::default() })
            .unwrap();
        let c7 = rec.led.load::<Criterion>("c7").unwrap();
        assert_eq!(next_step::for_criterion(&rec.led, &rec.exp("e1"), &c7, next_step::Arms::Versus).unwrap(), "run one more top1 measurement on INT8 against FP32, at seed=1, and record it in rb");
        assert_eq!(next_step::for_criterion(&rec.led, &rec.exp("e1"), &c7, next_step::Arms::Suggested).unwrap(), "run one more top1 measurement on INT8 against FP32, at seed=1, and record it in rb");
    }

    #[test]
    fn reproduce_s_request_asks_for_the_run_rb_suggests() {
        let rec = wants_a_third_seed("suggests");
        rec.led
            .add_criterion(&NewCriterion { statement: "INT8 adds at most 2 ms".into(), experiment: "e1".into(), metric: "change.latency_ms".into(), comparator: "at_most".into(), target: Some(2.0), id: Some("c9".into()), ..Default::default() })
            .unwrap();
        let prompt = |open: &[Value]| text(&Recipes::new(&rec.led, Tier::Owner).unwrap().with_open(open).experiment(&rec.exp("e1")).unwrap(), "prompt").to_string();
        let (top1, latency) = ("run one more top1 measurement on INT8 against FP32 and record it in rb", "run one more latency_ms measurement on INT8 against FP32 and record it in rb");
        // no open items handed: the first primary criterion
        assert_eq!(prompt(&[]), top1);
        // the criterion of the first item that adds a run, named as its subject or folded under its experiment
        let item = |pairs: Vec<(&str, Value)>| Value::Dict(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect());
        let run = |subject: &str| item(vec![("do", "rb run add e1 --set seed=4".into()), ("subject", subject.into())]);
        assert_eq!(prompt(&[run("c9"), run("c1")]), latency);
        let folded = item(vec![("do", "rb run add e1 --set seed=4".into()), ("subject", "e1".into()), ("criteria", Value::List(vec!["c9".into()]))]);
        assert_eq!(prompt(&[folded]), latency);
        // an item that adds no run, or names another experiment's criterion, leaves the request as it was
        let check = item(vec![("do", "rb spec verify e1".into()), ("subject", "c9".into())]);
        assert_eq!(prompt(&[check, run("c-elsewhere")]), top1);
        // rb status's own items: the request asks for the run its first suggestion would add, as the Next step does
        let st = Value::Dict(rec.led.status(None).unwrap());
        let open = st.get("open").and_then(Value::as_list).unwrap().clone();
        let first = open.iter().filter(|i| next_step::adds_a_run(i)).flat_map(next_step::criteria_of).find(|id| rec.led.load::<Criterion>(id).map_or(false, |c| c.experiment == "e1")).unwrap();
        let c = rec.led.load::<Criterion>(&first).unwrap();
        assert_eq!(prompt(&open), next_step::for_criterion(&rec.led, &rec.exp("e1"), &c, next_step::Arms::Versus).unwrap());
    }

    #[test]
    fn a_request_says_no_arm_its_title_already_names() {
        let rec = int8_record("titled");
        let both = |exp: &str, c: &Criterion| {
            let e = rec.exp(exp);
            [next_step::Arms::Versus, next_step::Arms::Suggested].map(|f| next_step::for_criterion(&rec.led, &e, c, f).unwrap())
        };
        // one arm of two that the title names: that arm of the experiment, never "on INT8 against FP32, fp32"
        rec.led
            .add_criterion(&NewCriterion { statement: "FP32 keeps its top-1".into(), experiment: "e1".into(), metric: "fp32.top1".into(), comparator: "at_least".into(), target: Some(0.7), id: Some("c8".into()), ..Default::default() })
            .unwrap();
        let c8 = rec.led.load::<Criterion>("c8").unwrap();
        assert_eq!(both("e1", &c8), ["run one more top1 measurement on the fp32 arm of INT8 against FP32 and record it in rb"; 2]);
        let add = |id: &str, title: &str, baseline: &str, candidate: &str| {
            rec.led
                .add_experiment(&NewExperiment {
                    title: title.into(),
                    id: Some(id.into()),
                    question: Some("q1".into()),
                    purpose: "top-1".into(),
                    baseline: (!baseline.is_empty()).then(|| baseline.into()),
                    candidates: vec![candidate.into()],
                    ..Default::default()
                })
                .unwrap();
            let cid = format!("c-{id}");
            let metric = if baseline.is_empty() { format!("{candidate}.top1") } else { "change.top1".into() };
            rec.led
                .add_criterion(&NewCriterion { statement: "Top-1 holds".into(), experiment: id.into(), metric, comparator: "at_least".into(), target: Some(-0.01), id: Some(cid.clone()), ..Default::default() })
                .unwrap();
            rec.led.load::<Criterion>(&cid).unwrap()
        };
        // a title that names neither arm, or one of the two it compares, keeps the arms in the criterion's order or rb's
        let c = add("e7", "Quantizer study", "fp32", "int8");
        assert_eq!(
            both("e7", &c),
            ["run one more top1 measurement on Quantizer study, int8 vs fp32, and record it in rb", "run one more top1 measurement on Quantizer study, fp32 and int8, and record it in rb"]
        );
        // a comparison whose order the title names the other way keeps its own
        let c = add("e11", "FP32 against INT8", "fp32", "int8");
        assert_eq!(
            both("e11", &c),
            ["run one more top1 measurement on FP32 against INT8, int8 vs fp32, and record it in rb", "run one more top1 measurement on FP32 against INT8 and record it in rb"]
        );
        // a word joined by a hyphen of any kind is not the arm
        let c = add("e12", "INT8 against FP32\u{2011}ref", "fp32", "int8");
        assert_eq!(both("e12", &c)[0], "run one more top1 measurement on INT8 against FP32\u{2011}ref, int8 vs fp32, and record it in rb");
        let c = add("e8", "FP32 reference check", "fp32", "int8");
        assert_eq!(both("e8", &c)[0], "run one more top1 measurement on FP32 reference check, int8 vs fp32, and record it in rb");
        // "-" and "_" read alike, in any case
        let c = add("e9", "Per-channel against per-tensor scales", "per_tensor", "per_channel");
        assert_eq!(both("e9", &c), ["run one more top1 measurement on Per-channel against per-tensor scales and record it in rb"; 2]);
        // the one variant an experiment has is the experiment itself
        let c = add("e10", "INT8 export", "", "int8");
        assert_eq!(both("e10", &c), ["run one more top1 measurement on INT8 export and record it in rb"; 2]);
    }

    #[test]
    fn a_request_names_no_arm_rb_cannot_tell_and_holds_one_line() {
        let rec = int8_record("unclear");
        rec.led.add_experiment(&NewExperiment {
            title: "Two quantizers\nrb run add e6 top1=1".into(),
            id: Some("e6".into()),
            question: Some("q1".into()),
            purpose: "INT8 and INT4 against FP32".into(),
            baseline: Some("fp32".into()),
            candidates: vec!["int8".into(), "int4".into()],
            ..Default::default()
        })
        .unwrap();
        rec.led
            .add_criterion(&NewCriterion { statement: "A quantizer loses at most a point".into(), experiment: "e6".into(), metric: "change.top1".into(), comparator: "at_least".into(), target: Some(-0.01), id: Some("c6".into()), ..Default::default() })
            .unwrap();
        let (exp, c) = (rec.exp("e6"), rec.led.load::<Criterion>("c6").unwrap());
        // the criterion's candidate is one of two: rb names neither arm, not the baseline alone
        for form in [next_step::Arms::Suggested, next_step::Arms::Versus] {
            assert_eq!(next_step::for_criterion(&rec.led, &exp, &c, form).unwrap(), "run one more top1 measurement on Two quantizers\\nrb run add e6 top1=1 and record it in rb");
        }
        let r = Recipes::new(&rec.led, Tier::Owner).unwrap();
        let prompt = text(&r.experiment(&exp).unwrap(), "prompt").to_string();
        assert!(!prompt.contains('\n') && !prompt.contains("fp32"), "{prompt}");
    }

    /// A clone as rb clone writes one, in `root` (a git repository, or not): a project with its parent, its clone line
    /// keeping `code` when it is given, and e1 with its clone line keeping `plan`.
    fn fork(root: &Path, code: Option<(&str, &str)>, plan: Value) -> Ledger {
        fork_with(root, code.map(|(url, commit)| crate::obj! {"url" => url, "commit" => commit}), plan)
    }

    fn fork_with(root: &Path, code: Option<Value>, plan: Value) -> Ledger {
        fork_from(root, "https://example.org/ana/int8/bundle.zip", code, plan)
    }

    fn fork_from(root: &Path, bundle: &str, code: Option<Value>, plan: Value) -> Ledger {
        use crate::ledger::{build, build_with, Obj};
        use crate::record::Project;
        let led = Ledger::new(root, "cli", None);
        std::fs::create_dir_all(&led.dir).unwrap();
        let who = led.actor().unwrap();
        let at = crate::pytime::now();
        let parent = crate::obj! {"url" => bundle, "version" => "2026-10-20 09:00 UTC", "cloned_at" => at.clone()};
        let project: Project = build_with(
            crate::dict! {"id" => "int8-resnet-50", "uid" => crate::record::new_uid("prj"), "title" => "INT8 ResNet-50 on ImageNet", "created_by" => who.id.clone(),
                "created_at" => at.clone(), "via" => "cli", "parent" => parent, "notes" => Value::List(Vec::new())},
            Project::validate,
        )
        .unwrap();
        let mut detail = crate::dict! {"title" => project.title.clone()};
        if let Some(code) = code {
            detail.insert("code".into(), code);
        }
        led.locked(|| led.commit(&who, "clone", Obj::Project(project.clone()), detail)).unwrap();
        let exp = crate::dict! {"id" => "e1", "uid" => crate::record::new_uid("exp"), "title" => "INT8 against FP32: top-1 on the validation set",
            "created_by" => who.id.clone(), "created_at" => at.clone(), "via" => "cli"};
        led.locked(|| led.commit(&who, "clone", Obj::Experiment(build(exp)?), crate::dict! {"rerun" => plan.clone()})).unwrap();
        led
    }

    fn scratch(tag: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!("rb-rerun-{tag}-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn a_rerun_plan_names_the_parents_commands_for_its_first_rule() {
        let rec = int8_record("plan");
        let plans = rerun_plan(&rec.led).unwrap();
        // e1's first rule is c1 (change.top1, int8 against fp32): seed 1's run holds both arms, and rb knows its command
        // made the numbers; seed 2's was given later, so it comes second and is not needed, one run covering both arms
        let e1 = &plans["e1"];
        assert_eq!(text(e1, "criterion"), "c1");
        let cmds = list(e1, "commands");
        assert_eq!(cmds.len(), 1, "{e1:?}");
        assert_eq!((text(&cmds[0], "run"), text(&cmds[0], "command"), text(&cmds[0], "given")), (rec.id("seed1").as_str(), "python eval.py --seed 1", "at_the_time"));
        assert_eq!(get(&cmds[0], "arms"), &Value::list_of_str(["fp32".to_string(), "int8".to_string()]));
        assert_eq!(get(&cmds[0], "per_run"), &crate::obj! {"seed" => "1"});
        // its receipt holds the code that made it: the commit it was recorded at, not as reported
        assert_eq!(text(&cmds[0], "code"), git(&rec.root, &["rev-parse", "HEAD"]));
        assert!(!get(&cmds[0], "code_reported").truthy());
        // an experiment with no rule names no command
        assert_eq!(get(&plans["e2"], "criterion"), &Value::Null);
        assert!(list(&plans["e2"], "commands").is_empty());
    }

    #[test]
    fn a_rerun_plan_reads_the_runs_its_rule_selects_and_says_a_later_command() {
        let rec = int8_record("plan-where");
        rec.led
            .add_criterion(&NewCriterion {
                statement: "INT8 keeps top-1 at seed 2".into(),
                experiment: "e1".into(),
                metric: "change.top1".into(),
                comparator: "at_least".into(),
                target: Some(-0.01),
                role: "primary".into(),
                id: Some("c0".into()),
                r#where: crate::dict! {"seed" => 2},
                ..Default::default()
            })
            .unwrap();
        let plans = rerun_plan(&rec.led).unwrap();
        // c1 is primary too and was declared first: the first primary rule is c1's, and with c1 retracted, c0's
        assert_eq!(text(&plans["e1"], "criterion"), "c1");
        rec.led.retract("c1", "declared on the wrong metric", None).unwrap();
        let plans = rerun_plan(&rec.led).unwrap();
        let e1 = &plans["e1"];
        assert_eq!(text(e1, "criterion"), "c0");
        let cmds = list(e1, "commands");
        assert_eq!(cmds.len(), 1, "{e1:?}");
        // the one run at seed 2, its command given later, not observed, and made before it was recorded
        assert_eq!((text(&cmds[0], "run"), text(&cmds[0], "given")), (rec.id("seed2").as_str(), "later"));
        let line = command_said(&cmds[0]);
        assert_eq!(line, format!("`rb journal run -- python eval.py --seed 2` (the parent's run {} of fp32 and int8, its command given later, not observed; per run seed=2)", rec.id("seed2")));
        // a later run at seed 2 whose command rb knows made its numbers comes before the one given later
        let mut rec = rec;
        let again = rec.run(
            "seed2b",
            "e1",
            NewRun {
                metrics: metrics(&[("fp32.top1", 0.7610), ("int8.top1", 0.7577)]),
                per_run: crate::dict! {"seed" => 2},
                command: Some("python eval.py --seed 2 --again".into()),
                again: Some("a second execution at seed 2".into()),
                ..Default::default()
            },
        );
        let plans = rerun_plan(&rec.led).unwrap();
        let cmds = list(&plans["e1"], "commands");
        assert_eq!((text(&cmds[0], "run"), text(&cmds[0], "given")), (again.as_str(), "at_the_time"), "{cmds:?}");
    }

    #[test]
    fn a_record_that_is_no_clone_has_no_rerun() {
        let rec = int8_record("no-rerun");
        assert_eq!(rerun(&rec.led).unwrap(), None);
    }

    #[test]
    fn a_clone_says_where_its_code_is() {
        // the code's repository, two commits: the second changes eval.py
        let code = scratch("code");
        git(&code, &["init", "-q"]);
        std::fs::write(code.join("eval.py"), "print('top1')\n").unwrap();
        git(&code, &["add", "-A"]);
        git(&code, &["commit", "-qm", "eval"]);
        let first = git(&code, &["rev-parse", "HEAD"]);
        std::fs::write(code.join("eval.py"), "print('top1', 'top5')\n").unwrap();
        git(&code, &["commit", "-qam", "top5 too"]);
        let second = git(&code, &["rev-parse", "HEAD"]);
        let plan = crate::obj! {"criterion" => "c1", "commands" => Value::List(vec![crate::obj! {
            "arms" => Value::list_of_str(["fp32".to_string(), "int8".to_string()]), "run" => "run1abcde", "command" => "python eval.py --seed 1",
            "given" => "at_the_time", "per_run" => crate::obj! {"seed" => "1"}, "code" => first.clone(), "code_reported" => false
        }])};
        let url = "https://example.org/ana/int8.git";
        // the record in a checkout of the code at its first commit: here, the record aside
        git(&code, &["checkout", "-q", "-b", "rerun", &first]);
        let led = fork(&code, Some((url, &first)), plan.clone());
        let v = rerun(&led).unwrap().expect("a clone");
        let short: String = first.chars().take(12).collect();
        assert_eq!(text(&v, "code.state"), "here", "{v:?}");
        assert_eq!(text(&v, "code.text"), format!("{url} at {short}, here: this checkout's code is commit {short}'s, the record aside"));
        assert_eq!(text(&v, "code.get"), "");
        let e1 = &list(&v, "experiments")[0];
        assert_eq!(text(e1, "first"), "rb journal run -- python eval.py --seed 1");
        assert_eq!(rerun_next(&v), vec!["rb journal run -- python eval.py --seed 1".to_string()]);
        let lines = rerun_lines(&v);
        assert_eq!(lines[0], format!("- parent: INT8 ResNet-50 on ImageNet (cloned {} from https://example.org/ana/int8/bundle.zip)", &crate::pytime::now()[..10]));
        assert_eq!(lines[1], format!("- code: {url} at {short}, here: this checkout's code is commit {short}'s, the record aside"));
        assert_eq!(
            lines.last().unwrap(),
            &format!("- e1, first, for c1: `rb journal run -- python eval.py --seed 1` (the parent's run run1abcde of fp32 and int8, its command as given; per run seed=1; ran at {short}; this checkout's code is commit {short}'s); no run recorded here yet")
        );
        // a changed file is said with its name
        std::fs::write(code.join("eval.py"), "print('top1 at last')\n").unwrap();
        let v = rerun(&led).unwrap().unwrap();
        assert_eq!(text(&v, "code.state"), "changed");
        assert_eq!(text(&v, "code.text"), format!("{url} at {short}, here with changes: 1 file differs from it (eval.py)"));
        git(&code, &["checkout", "-q", "--", "eval.py"]);
        assert_eq!(text(&rerun(&led).unwrap().unwrap(), "code.state"), "here");
        // what the parent's runs wrote, taken out of the branch, and a file a run here names are no difference in code
        std::fs::create_dir_all(code.join("results/s1")).unwrap();
        std::fs::write(code.join("results/s1/metrics.json"), "{\"int8\": {\"top1\": 0.7581}}\n").unwrap();
        std::fs::write(code.join("results/s2.json"), "{\"int8\": {\"top1\": 0.7574}}\n").unwrap();
        git(&code, &["add", "-A", "results"]);
        git(&code, &["commit", "-qm", "the parent's results"]);
        let at = git(&code, &["rev-parse", "HEAD"]);
        let short: String = at.chars().take(12).collect();
        let checkout = scratch("taken");
        git(&checkout, &["clone", "-q", code.to_str().unwrap(), "."]);
        git(&checkout, &["rm", "-q", "-r", "results/s1"]);
        // here the parent's run ran at the second commit, the one that made eval.py say top5 too
        let mut plan2 = plan.clone();
        if let Some(Value::List(cs)) = plan2.as_dict_mut().and_then(|d| d.get_mut("commands")) {
            if let Some(Value::Dict(c)) = cs.first_mut() {
                c.insert("code".into(), Value::Str(second.clone()));
            }
        }
        let taken = fork_with(&checkout, Some(crate::obj! {"url" => url, "commit" => at.clone(), "taken_out" => Value::list_of_str(["results/s1/".to_string()])}), plan2);
        let v = rerun(&taken).unwrap().unwrap();
        assert_eq!(text(&v, "code.text"), format!("{url} at {short}, here: this checkout's code is commit {short}'s, the record and the parent's results aside"));
        // the parent's run ran at the second commit: this checkout's eval.py is not that commit's, and it holds a file
        // that commit does not; the command that puts that commit's files here comes before the run's own
        let ran_short: String = second.chars().take(12).collect();
        let e1 = &list(&v, "experiments")[0];
        let ran = get(&list(e1, "commands")[0], "code_here");
        assert_eq!(text(ran, "state"), "differs");
        assert_eq!(get(ran, "differs"), &Value::list_of_str(["eval.py".to_string()]));
        assert_eq!(get(ran, "extra"), &Value::list_of_str(["results/s2.json".to_string()]));
        let restore = format!("git restore --source={ran_short} --worktree -- eval.py results/s2.json");
        assert_eq!(text(ran, "get"), restore);
        // the command writes over eval.py and deletes results/s2.json, keeping no copy: the block says so, and Next
        // never offers it
        assert_eq!(rerun_next(&v), vec!["rb journal run -- python eval.py --seed 1".to_string()]);
        assert!(rerun_lines(&v).last().unwrap().contains(&format!(
            "ran at {ran_short}; this checkout's code is not commit {ran_short}'s: it differs in eval.py, and holds results/s2.json the commit does not; `{restore}` makes it commit {ran_short}'s: it writes over eval.py and deletes results/s2.json here and keeps no copy"
        )), "{:?}", rerun_lines(&v));
        // one diff per commit on each read, however many of the parent's runs ran it
        let mut twice = plan.clone();
        if let Some(Value::List(cs)) = twice.as_dict_mut().and_then(|d| d.get_mut("commands")) {
            let mut c = cs[0].clone();
            if let Value::Dict(d) = &mut c {
                d.insert("code".into(), Value::Str(second.clone()));
                d.insert("command".into(), Value::Str("python eval.py --seed 2".into()));
            }
            cs[0] = c.clone();
            cs.push(c);
        }
        let dir = scratch("twice");
        git(&dir, &["clone", "-q", code.to_str().unwrap(), "."]);
        let both = fork_with(&dir, Some(crate::obj! {"url" => url, "commit" => at.clone()}), twice);
        let count = |led: &Ledger| {
            let before = DIFFS.with(|n| n.get());
            let v = rerun(led).unwrap().unwrap();
            (DIFFS.with(|n| n.get()) - before, list(&list(&v, "experiments")[0], "commands").len())
        };
        let (one, two) = (count(&taken), count(&both));
        assert_eq!((one.1, two.1), (1, 2));
        assert_eq!(one.0, 2, "the bundle's commit and the run's");
        assert_eq!(two.0, one.0, "a second command at the same commit compares nothing again");
        let _ = std::fs::remove_dir_all(&both.root);
        // the command does what it says: run, it leaves this checkout's code that commit's
        let parts: Vec<String> = restore.split(' ').skip(1).map(str::to_string).collect();
        git(&checkout, &parts.iter().map(String::as_str).collect::<Vec<_>>());
        let after = rerun(&taken).unwrap().unwrap();
        assert_eq!(text(get(&list(&list(&after, "experiments")[0], "commands")[0], "code_here"), "state"), "same");
        git(&checkout, &["checkout", "-q", "HEAD", "--", "."]);
        std::fs::write(checkout.join("results/s2.json"), "{\"int8\": {\"top1\": 0.7580}}\n").unwrap();
        assert_eq!(text(&rerun(&taken).unwrap().unwrap(), "code.state"), "changed");
        taken.add_run("e1", NewRun { metrics: metrics(&[("int8.top1", 0.7580)]), files: vec!["results/s2.json".into()], ..Default::default() }).unwrap();
        assert_eq!(text(&rerun(&taken).unwrap().unwrap(), "code.state"), "here");
        // an untracked file git does not ignore is code too: a run's code holds it
        std::fs::write(checkout.join("newmod.py"), "SCALE = 2\n").unwrap();
        let v = rerun(&taken).unwrap().unwrap();
        assert_eq!(text(&v, "code.state"), "changed");
        assert_eq!(text(&v, "code.text"), format!("{url} at {short}, here with changes: 1 file differs from it (newmod.py)"));
        let ran = get(&list(&list(&v, "experiments")[0], "commands")[0], "code_here");
        assert_eq!(get(ran, "untracked"), &Value::list_of_str(["newmod.py".to_string()]), "{ran:?}");
        // git restore knows no untracked file: the command leaves it out, and the block says it stays
        // (results/s2.json is a run's here now, so no difference in code)
        assert_eq!(text(ran, "get"), format!("git restore --source={ran_short} --worktree -- eval.py"));
        assert!(rerun_lines(&v).last().unwrap().contains(&format!(
            "it differs in eval.py, and holds newmod.py (untracked) the commit does not; `git restore --source={ran_short} --worktree -- eval.py` puts commit {ran_short}'s files here: it writes over eval.py here and keeps no copy; git restore leaves newmod.py as it is"
        )), "{:?}", rerun_lines(&v));
        std::fs::write(checkout.join(".gitignore"), "newmod.py\n.gitignore\n").unwrap();
        assert_eq!(text(&rerun(&taken).unwrap().unwrap(), "code.state"), "here");
        // a commit this checkout does not hold: the clone again, with the code's address
        let other = fork(&scratch("elsewhere"), Some((url, "0123456789abcdef0123456789abcdef01234567")), plan.clone());
        let v = rerun(&other).unwrap().unwrap();
        assert_eq!(text(&v, "code.state"), "elsewhere");
        assert_eq!(text(&v, "code.text"), format!("{url} at 0123456789ab, which this checkout does not hold"));
        assert_eq!(text(&v, "code.get"), format!("rb clone https://example.org/ana/int8/bundle.zip <new folder> --code {url}"));
        // the runs' commands run in the folder the code is got into, whose own Next names them: none here
        assert_eq!(rerun_next(&v), vec![text(&v, "code.get")]);
        assert_eq!(get(get(&v, "code"), "here"), &Value::Bool(false));
        // an address rb clone could not get is not offered again
        let failed = fork_with(&scratch("failed"), Some(crate::obj! {"url" => url, "commit" => Value::Null, "got" => false, "failed" => true}), plan.clone());
        let v = rerun(&failed).unwrap().unwrap();
        assert_eq!(text(&v, "code.text"), format!("{url}, which rb clone could not get from that address"));
        assert_eq!(text(&v, "code.get"), "rb clone https://example.org/ana/int8/bundle.zip <new folder> --code <address of the code>");
        assert_eq!(rerun_next(&v), vec![text(&v, "code.get")]);
        // not got for want of a git identity: the address stands
        let unsaid = fork_with(&scratch("no-identity"), Some(crate::obj! {"url" => url, "commit" => Value::Null, "got" => false, "failed" => false}), plan.clone());
        assert_eq!(text(&rerun(&unsaid).unwrap().unwrap(), "code.get"), format!("rb clone https://example.org/ana/int8/bundle.zip <new folder> --code {url}"));
        let _ = std::fs::remove_dir_all(&unsaid.root);
        let _ = std::fs::remove_dir_all(&failed.root);
        // a bundle that named no code
        let none = fork(&scratch("unknown"), None, plan);
        let v = rerun(&none).unwrap().unwrap();
        assert_eq!(text(&v, "code.state"), "unknown");
        assert_eq!(text(&v, "code.text"), "the runs need the code; this bundle doesn't say where it is");
        assert_eq!(text(&v, "code.get"), "rb clone https://example.org/ana/int8/bundle.zip <new folder> --code <address of the code>");
        // a folder holding the record alone holds no code to run; one holding files beside it may
        git(&none.root, &["init", "-q"]);
        let v = rerun(&none).unwrap().unwrap();
        assert_eq!(rerun_next(&v), vec![text(&v, "code.get")]);
        // a file of the repository the record sits inside, outside the record's folder, is no code of this folder's
        let inner = none.root.join("clone");
        std::fs::create_dir_all(&inner).unwrap();
        let nested = fork(&inner, None, crate::obj! {"criterion" => "c1", "commands" => Value::List(vec![crate::obj! {"run" => "run1abcde", "command" => "python eval.py --seed 1", "given" => "later"}])});
        std::fs::write(none.root.join("unrelated.py"), "print('other work')\n").unwrap();
        let w = rerun(&nested).unwrap().unwrap();
        assert_eq!(get(get(&w, "code"), "here"), &Value::Bool(false), "{w:?}");
        std::fs::remove_file(none.root.join("unrelated.py")).unwrap();
        std::fs::remove_dir_all(&inner).unwrap();
        std::fs::write(none.root.join("eval.py"), "print('top1')\n").unwrap();
        let v = rerun(&none).unwrap().unwrap();
        assert_eq!(rerun_next(&v), vec![text(&v, "code.get"), "rb journal run -- python eval.py --seed 1"]);
        for root in [&code, &checkout, &other.root, &none.root] {
            let _ = std::fs::remove_dir_all(root);
        }
    }

    #[test]
    fn a_clone_says_how_to_get_each_resource_again_and_stops_asking_once_a_run_is_here() {
        let root = scratch("resources");
        git(&root, &["init", "-q"]);
        // the code beside the record, as in a folder the user cloned it into
        std::fs::write(root.join("eval.py"), "print('top1')\n").unwrap();
        let led = fork(&root, None, crate::obj! {"criterion" => "c1", "commands" => Value::List(vec![crate::obj! {"run" => "run1abcde", "command" => "python eval.py --seed 1", "given" => "later"}])});
        led.add_resource(&NewResource {
            id: "imagenet-val".into(),
            r#type: "dataset".into(),
            name: "ImageNet-1k validation".into(),
            recipe: Some("python get_data.py --split val (scripts/README.md:5)".into()),
            locations: vec![("local".into(), crate::sys::slashed(&root.join("data/val"))), ("s3".into(), "s3://ana-lab/imagenet-val".into())],
            ..Default::default()
        })
        .unwrap();
        let v = rerun(&led).unwrap().unwrap();
        let lines = rerun_lines(&v);
        let line = lines.iter().find(|l| l.starts_with("- resource imagenet-val")).expect("the resource");
        assert_eq!(
            line,
            "- resource imagenet-val (ImageNet-1k validation): dataset · remote; the record expects it at data/val (not here); how to get it again: python get_data.py --split val (scripts/README.md:5) · s3: s3://ana-lab/imagenet-val"
        );
        std::fs::create_dir_all(root.join("data/val")).unwrap();
        assert!(rerun_lines(&rerun(&led).unwrap().unwrap()).iter().any(|l| l.contains("the record expects it at data/val (here)")));
        // a command given later is said so; once a run of e1 is recorded here, its first command is no longer asked for
        assert!(lines.iter().any(|l| l.contains("its command given later, not observed")), "{lines:?}");
        assert_eq!(rerun_next(&v).last().map(String::as_str), Some("rb journal run -- python eval.py --seed 1"));
        led.add_run("e1", NewRun { metrics: metrics(&[("int8.top1", 0.7581)]), command: Some("python eval.py --seed 1".into()), ..Default::default() }).unwrap();
        let v = rerun(&led).unwrap().unwrap();
        assert!(rerun_next(&v).iter().all(|n| !n.contains("python eval.py")), "{v:?}");
        assert!(rerun_lines(&v).last().unwrap().ends_with("; 1 run recorded here"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_rerun_plan_keeps_the_uncommitted_changes_a_run_ran_and_a_rule_with_no_arm() {
        let mut rec = int8_record("plan-dirty");
        // e4: no variants, a level rule on its runs' only numbers; its run recorded with eval.py changed and uncommitted
        rec.led.add_experiment(&NewExperiment { title: "FP32 top-1 alone".into(), id: Some("e4".into()), question: Some("q1".into()), purpose: "the FP32 baseline's top-1".into(), ..Default::default() }).unwrap();
        rec.led
            .add_criterion(&NewCriterion { statement: "FP32 reaches 76% top-1".into(), experiment: "e4".into(), metric: "top1".into(), comparator: "at_least".into(), target: Some(0.76), id: Some("c4".into()), ..Default::default() })
            .unwrap();
        rec.write("eval.py", "print('top1')\n");
        git(&rec.root, &["add", "eval.py"]);
        git(&rec.root, &["commit", "-qm", "eval.py"]);
        rec.write("eval.py", "print('top1 on 50000 images')\n");
        let id = rec.run("level", "e4", NewRun { metrics: metrics(&[("top1", 0.7613)]), command: Some("python eval.py --fp32".into()), ..Default::default() });
        let plans = rerun_plan(&rec.led).unwrap();
        let cmds = list(&plans["e4"], "commands");
        assert_eq!(cmds.len(), 1, "{:?}", plans["e4"]);
        assert_eq!((text(&cmds[0], "run"), text(&cmds[0], "command")), (id.as_str(), "python eval.py --fp32"));
        let receipt = rec.load_run("level").receipt.attached.unwrap();
        let (commit, tree) = (receipt.get_str("commit").unwrap().to_string(), receipt.get_str("tree").unwrap().to_string());
        assert_eq!((text(&cmds[0], "code"), text(&cmds[0], "tree")), (commit.as_str(), tree.as_str()));
        // a clone of the code carries no tree rb kept (one fetched from an address, as --no-local fetches): said so,
        // never the commit's code in its place
        let root = scratch("dirty");
        git(&root, &["clone", "-q", "--no-local", rec.root.to_str().unwrap(), "."]);
        git(&root, &["rm", "-r", "-q", "--", crate::ledger::DIR]);
        let fork = fork(&root, Some(("https://example.org/ana/int8.git", &commit)), plans["e4"].clone());
        let v = rerun(&fork).unwrap().unwrap();
        let c = &list(&list(&v, "experiments")[0], "commands")[0];
        assert_eq!(text(get(c, "code_here"), "state"), "absent", "{c:?}");
        let line = rerun_lines(&v).last().unwrap().clone();
        let (cs, ts): (String, String) = (commit.chars().take(12).collect(), tree.chars().take(12).collect());
        assert!(line.contains(&format!("ran at {cs} with the uncommitted changes rb kept as git tree {ts}; a tree this checkout does not hold")), "{line}");
        // where the tree is here, the code is compared with it
        let here = fork_root_in(&rec.root, &commit, plans["e4"].clone());
        let v = rerun(&here).unwrap().unwrap();
        let c = &list(&list(&v, "experiments")[0], "commands")[0];
        assert_eq!(text(get(c, "code_here"), "state"), "same", "{c:?}");
        let _ = std::fs::remove_dir_all(&root);
    }

    /// A clone written into the parent's own checkout, which holds every tree rb kept there; the parent's record set
    /// aside, as rb clone takes it out of the branch.
    fn fork_root_in(root: &Path, commit: &str, plan: Value) -> Ledger {
        std::fs::rename(root.join(crate::ledger::DIR), root.join(".rb-parent")).unwrap();
        std::fs::write(root.join(".git/info/exclude"), ".rb-parent/\n").unwrap();
        fork(root, Some(("https://example.org/ana/int8.git", commit)), plan)
    }

    #[test]
    fn a_path_git_would_quote_is_named_as_it_is() {
        let code = scratch("unicode");
        git(&code, &["init", "-q"]);
        std::fs::write(code.join("café.py"), "print('top1')\n").unwrap();
        git(&code, &["add", "-A"]);
        git(&code, &["commit", "-qm", "café"]);
        let at = git(&code, &["rev-parse", "HEAD"]);
        std::fs::write(code.join("café.py"), "print('top1', 'top5')\n").unwrap();
        let plan = crate::obj! {"criterion" => "c1", "commands" => Value::List(vec![crate::obj! {"run" => "run1abcde", "command" => "python café.py", "given" => "at_the_time", "code" => at.clone()}])};
        let led = fork(&code, Some(("https://example.org/ana/int8.git", &at)), plan);
        let v = rerun(&led).unwrap().unwrap();
        let ran = get(&list(&list(&v, "experiments")[0], "commands")[0], "code_here");
        assert_eq!(get(ran, "differs"), &Value::list_of_str(["café.py".to_string()]));
        assert_eq!(text(ran, "get"), format!("git restore --source={} --worktree -- 'café.py'", &at[..12]));
        let _ = std::fs::remove_dir_all(&code);
    }

    #[test]
    fn a_clone_quotes_the_addresses_it_names_in_a_command() {
        let root = scratch("quoted");
        let led = fork(&root, Some(("/srv/ana/int8 code", "0123456789abcdef0123456789abcdef01234567")), Value::Null);
        let v = rerun(&led).unwrap().unwrap();
        assert_eq!(text(&v, "code.get"), "rb clone https://example.org/ana/int8/bundle.zip <new folder> --code '/srv/ana/int8 code'");
        let _ = std::fs::remove_dir_all(&root);
        let root = scratch("quoted-bundle");
        let led = fork_from(&root, "/srv/ana/shared bundles/int8.zip", None, Value::Null);
        assert_eq!(text(&rerun(&led).unwrap().unwrap(), "code.get"), "rb clone '/srv/ana/shared bundles/int8.zip' <new folder> --code <address of the code>");
        let _ = std::fs::remove_dir_all(&root);
    }
}
