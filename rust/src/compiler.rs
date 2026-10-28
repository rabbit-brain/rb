//! `rb compile`: reports and results that already exist, read against the record. It inventories what is there,
//! proposes the runs it can record outright, and grounds the prose's anchors against the record. It reads; it never
//! writes.

use crate::cli::Out;
use crate::errors::Res;
use crate::ledger::{superseded_for, Ledger, DIR};
use crate::pyjson::py_str;
use crate::record::*;
use crate::value::{Dict, Value};
use fancy_regex::Regex as FRegex;
use indexmap::IndexMap;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

const PROSE: &[&str] = &[".md", ".markdown", ".qmd", ".rmd", ".mdx", ".txt", ".rst"];
const STRUCTURED: &[&str] = &[".json", ".yaml", ".yml", ".toml"];
const TABLES: &[&str] = &[".csv", ".tsv"];
const CODE: &[&str] = &[".py", ".sh", ".bash", ".js", ".mjs", ".ts", ".r", ".jl", ".ipynb"];
const SKIP_DIRS: &[&str] = &[DIR, ".git", "__pycache__", "node_modules", ".venv", "venv"];
const MAX_SOURCES: usize = 200;

pub const CONTRACT: &[&str] = &[
    "Propose structure through the ordinary commands; rb compile writes nothing itself.",
    "Every value carries its source. Structured: --source file#key.path (rb reads the file and checks it). Prose: --quote \"exact text\" --locator \"section\" (the line must state the value; rb reads and checks the span).",
    "Statements travel verbatim: a question, hypothesis or criterion lifted from a report keeps its words, never reworded, as plain text with the markup dropped (Unicode symbols for math, no citation keys). A --quote stays exact: rb checks it against the file. A summary is a new artifact with an author, not a replacement for what was said.",
    "Numbers in structured files are runs (rb run add <exp> --from <file>); so is a row of a SQLite database, read from the database itself (--from <database> --table <table> --where <column>=<value>), never from an export of it. Give --command only for a command you saw run or a log line records (--command-source <file>:<line>); a command from a README, a paper or a script is a recipe, a note with its file and line. A result that exists only in prose becomes a cited criterion (rb criterion add \"...\" -e <exp> -q <question> --metric <m> --source <file> --quote \"...\" --locator \"...\"), which runs made later reproduce or not; a threshold a plan sets is a criterion in the plan's words with --why \"<file>:<line>\", and rb checks that the line states it.",
    "Declare the comparison as it was: experiment with its --hypothesis, --baseline and --candidate, control and ablation variants, --varies for the intended differences. A direction nobody ran yet is an experiment with no runs. Declare the counting structure as population.* settings; rb says it beside every count. Datasets and checkpoints the runs read are resources (rb resource add).",
    "Unknown stays unknown (rb spec set <exp> <name> --unknown); ambiguity is listed, never guessed.",
    "Everything you write is recorded as yours. Calls on results and on lines of work (rb decide) are the user's: record one when they ask, with their words verbatim in --words; it waits for their confirmation (rb confirm <id>, in their own terminal). A call made before the record is --past, and it waits for the same one confirmation whatever document it cites: --source <file>[:<line>] --quote \"...\" gives the document, and rb says beside the call what git shows of it.",
];

/// What a no-record read says of the work already done.
pub const START_POINTER: &str = "To record the work already done: rb docs --start.";

/// Path.suffix, lowered.
fn suffix(p: &Path) -> String {
    let name = p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => name[i..].to_lowercase(),
        _ => String::new(),
    }
}

fn kind_of(p: &Path) -> &'static str {
    let ext = suffix(p);
    if PROSE.contains(&ext.as_str()) {
        "prose"
    } else if STRUCTURED.contains(&ext.as_str()) {
        "structured"
    } else if TABLES.contains(&ext.as_str()) {
        "table"
    } else if CODE.contains(&ext.as_str()) {
        "code"
    } else {
        "other"
    }
}

/// A text file read as Python's text mode reads it: undecodable bytes replaced, every line ending a "\n".
fn text_of(p: &Path) -> Option<String> {
    let bytes = std::fs::read(p).ok()?;
    Some(String::from_utf8_lossy(&bytes).replace("\r\n", "\n").replace('\r', "\n"))
}

fn first_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// The first heading, or the first line that says anything: the source's own name for itself.
fn heading(p: &Path) -> String {
    let Some(text) = text_of(p) else { return String::new() };
    let mut first = String::new();
    for line in text.split_inclusive('\n') {
        let s = crate::rbyaml::py_strip(line);
        if s.is_empty() {
            continue;
        }
        if s.starts_with('#') {
            let t = s.trim_start_matches(|c| c == '#' || c == ' ');
            return first_chars(crate::rbyaml::py_strip(t), 120);
        }
        if first.is_empty() {
            first = first_chars(s, 120);
        }
    }
    first
}

fn columns(p: &Path) -> Vec<String> {
    let Some(text) = text_of(p) else { return Vec::new() };
    let head = crate::rbyaml::py_strip(text.split_inclusive('\n').next().unwrap_or("")).to_string();
    let sep = if head.contains(',') { "," } else { "\t" };
    head.split(sep).map(|c| crate::rbyaml::py_strip(c).to_string()).take(12).collect()
}

fn parts_of(p: &Path) -> Vec<String> {
    p.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect()
}

/// Every file below `dir`, but in a worktree of the repository (`common`, its shared git directory) found below it:
/// another checkout, whose files are its own.
fn walk_files(dir: &Path, out: &mut Vec<PathBuf>, common: Option<&Path>) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let p = e.path();
        let link = e.file_type().map(|t| t.is_symlink()).unwrap_or(false);
        if p.is_dir() {
            if !link && !common.map_or(false, |c| crate::ledger::is_worktree_top(&p, c)) {
                walk_files(&p, out, common);
            }
        } else if p.is_file() {
            out.push(p);
        }
    }
}

/// The files the caller pointed at, and the paths that fall outside the project.
fn files(root: &Path, given: &[String]) -> (Vec<PathBuf>, Vec<String>) {
    let mut inside: Vec<PathBuf> = Vec::new();
    let mut outside: Vec<String> = Vec::new();
    let home = if cfg!(windows) { crate::sys::slashed(&crate::sys::home()) } else { std::env::var("HOME").unwrap_or_default() };
    for raw in given {
        let raw = &crate::sys::typed(raw);
        let expanded = if raw == "~" {
            home.clone()
        } else if let Some(rest) = raw.strip_prefix("~/") {
            format!("{home}/{rest}")
        } else {
            raw.clone()
        };
        let p0 = PathBuf::from(&expanded);
        let p = crate::sources::resolve_path(&if p0.is_absolute() { p0 } else { std::env::current_dir().unwrap_or_default().join(p0) });
        let mut candidates = vec![p.clone()];
        if p.is_dir() {
            let mut found = Vec::new();
            walk_files(&p, &mut found, crate::ledger::common_dir_of(&p).as_deref());
            found.retain(|x| {
                let rel = x.strip_prefix(&p).map(|r| r.to_path_buf()).unwrap_or_default();
                !parts_of(&rel).iter().any(|part| SKIP_DIRS.contains(&part.as_str()) || part.starts_with('.'))
            });
            found.sort_by(|a, b| parts_of(a).cmp(&parts_of(b)));
            candidates = found;
        }
        for f in candidates {
            if !f.exists() {
                outside.push(format!("{raw}: no such file"));
                continue;
            }
            if !f.starts_with(root) {
                outside.push(crate::sys::slashed(&f));
                continue;
            }
            inside.push(f);
            if inside.len() >= MAX_SOURCES {
                return (inside, outside);
            }
        }
    }
    (inside, outside)
}

/// A SQLite database's tables, read and never written: each with its columns, its row count, and the columns that
/// name a metric the project declares (`names`, every declared name to its metric). Where it cannot be read without
/// writing, or at all, `note` says why.
fn database_entry(f: &Path, rel: &str, names: &IndexMap<String, String>, entry: &mut Dict) {
    match crate::sqlite::tables(f, rel) {
        Ok(tables) => {
            let listed: Vec<Value> = tables
                .into_iter()
                .map(|t| {
                    let metrics: Dict = t.columns.iter().filter_map(|c| crate::journal::canon_of(c, names).map(|m| (c.clone(), Value::Str(m)))).collect();
                    crate::dict! {"name" => t.name, "rows" => t.rows, "columns" => Value::list_of_str(t.columns), "metrics" => Value::Dict(metrics)}
                })
                .map(Value::Dict)
                .collect();
            entry.insert("tables".into(), Value::List(listed));
        }
        Err(e) => {
            entry.insert("note".into(), Value::Str(e.message));
        }
    }
}

fn source_entry(root: &Path, f: &Path, names: &IndexMap<String, String>) -> Dict {
    let rel = f.strip_prefix(root).map(crate::sys::slashed).unwrap_or_default();
    // a database is told by its first bytes, whatever its name says; a file rb cannot read is said so, never taken
    // for one that is not a database
    let (kind, unread) = match crate::sqlite::header(f) {
        Ok(true) => ("database", None),
        Ok(false) => (kind_of(f), None),
        Err(e) => (kind_of(f), Some(crate::errors::RBError::could_not_read(&e, &rel).message)),
    };
    let bytes = std::fs::metadata(f).map(|m| m.len() as i64).unwrap_or(0);
    let mut entry = crate::dict! {"path" => rel.clone(), "kind" => kind, "bytes" => bytes};
    if let Some(note) = unread {
        entry.insert("note".into(), Value::Str(note));
        return entry;
    }
    match kind {
        "database" => database_entry(f, &rel, names, &mut entry),
        "prose" => {
            let head = heading(f);
            if !head.is_empty() {
                entry.insert("heading".into(), Value::Str(head));
            }
        }
        "structured" => {
            let nums = crate::journal::numbers_in(root, &rel);
            if !nums.is_empty() {
                let mut keys: Vec<String> = nums.keys().cloned().collect();
                keys.sort();
                entry.insert("numbers".into(), Value::list_of_str(keys.into_iter().take(12)));
                entry.insert("number_count".into(), Value::Int(nums.len() as i64));
            }
        }
        "table" => {
            let cols = columns(f);
            if !cols.is_empty() {
                entry.insert("columns".into(), Value::list_of_str(cols));
            }
        }
        _ => {}
    }
    entry
}

// ---------------------------------------------------------------- anchors: reconcile prose against the record

fn number_re() -> &'static FRegex {
    static R: OnceLock<FRegex> = OnceLock::new();
    R.get_or_init(|| FRegex::new(r"(?<![\w.])[+\-−]?\d+\.\d+\s*%?|(?<![\w.])[+\-−]?\d+\s*%").unwrap())
}

/// The record's own vocabulary: ids, metric names and aliases, variant and setting names.
fn authority(led: &Ledger) -> Res<IndexMap<String, String>> {
    let mut table: IndexMap<String, String> = IndexMap::new();
    for id in led.live::<Question>()?.iter().map(|o| o.id.clone()) {
        table.insert(id.to_lowercase(), "question".into());
    }
    for id in led.live::<Experiment>()?.iter().map(|o| o.id.clone()) {
        table.insert(id.to_lowercase(), "experiment".into());
    }
    for id in led.live::<Criterion>()?.iter().map(|o| o.id.clone()) {
        table.insert(id.to_lowercase(), "criterion".into());
    }
    for id in led.live::<Run>()?.iter().map(|o| o.id.clone()) {
        table.insert(id.to_lowercase(), "run".into());
    }
    for id in led.live::<Analysis>()?.iter().map(|o| o.id.clone()) {
        table.insert(id.to_lowercase(), "analysis".into());
    }
    for id in led.live::<Resource>()?.iter().map(|o| o.id.clone()) {
        table.insert(id.to_lowercase(), "resource".into());
    }
    for m in led.live::<Metric>()? {
        table.insert(m.id.to_lowercase(), "metric".into());
        for a in &m.aliases {
            table.insert(a.to_lowercase(), format!("metric (alias of {})", m.id));
        }
    }
    for exp in led.live::<Experiment>()? {
        for v in exp.live_variants() {
            table.insert(v.name.to_lowercase(), format!("variant of {}", exp.id));
        }
        for (name, s) in exp.all_settings() {
            if s.retracted.is_none() {
                table.insert(name.rsplit('.').next().unwrap_or("").to_lowercase(), format!("setting of {}", exp.id));
            }
        }
    }
    Ok(table.into_iter().filter(|(t, _)| t.chars().count() >= 2).collect())
}

/// (value, is_percent, where it comes from): every stored measurement, plus the derived values a report would quote.
fn grounding_values(led: &Ledger) -> Res<Vec<(f64, bool, String)>> {
    let mut vals: Vec<(f64, bool, String)> = Vec::new();
    for c in led.live::<Criterion>()? {
        if let Some(t) = c.target {
            vals.push((t, false, format!("the rule of {}", c.id)));
        }
        if let Some(m) = c.margin {
            vals.push((m, false, format!("the margin of {}", c.id)));
        }
        if let Some(n) = c.noise {
            vals.push((n, false, format!("the declared noise of {}", c.id)));
        }
    }
    for a in led.live::<Analysis>()? {
        for comp in &a.comparisons {
            let arms = if !comp.reference.is_empty() {
                format!("{} vs {}", comp.subject, comp.reference)
            } else if !comp.subject.is_empty() {
                comp.subject.clone()
            } else {
                "the run".to_string()
            };
            for (label, x) in [("estimate", Some(comp.estimate)), ("lower bound", comp.lo), ("upper bound", comp.hi)] {
                if let Some(x) = x {
                    vals.push((x, false, format!("the {label} of {}, {arms}, in {}", comp.metric, a.id)));
                }
            }
        }
    }
    for e in led.standing_runs()?.iter() {
        for (k, v) in &e.metrics {
            if !superseded_for(&e, k, None) {
                vals.push((*v, false, format!("{k} in {}", e.id)));
            }
        }
    }
    for exp in led.live::<Experiment>()? {
        let Ok(cmp) = led.compare(&exp.id) else { continue };
        let cv = Value::Dict(cmp);
        let base = cv.get("baseline").map(py_str).unwrap_or_else(|| "None".into());
        let base_null = cv.get("baseline").map_or(true, |b| b.is_null());
        let rows: Vec<Value> = match cv.get("rows") {
            Some(Value::List(l)) => l.clone(),
            _ => Vec::new(),
        };
        // a row of a split table is one cell: its numbers say which, and its change is against the baseline's same cell
        let cell = |r: &Value| -> String {
            match r.get("where") {
                Some(Value::Dict(w)) if !w.is_empty() => format!(" [where {}]", w.iter().map(|(k, v)| format!("{k}={}", py_str(v))).collect::<Vec<_>>().join(", ")),
                _ => String::new(),
            }
        };
        let mut base_means: IndexMap<(String, String), f64> = IndexMap::new();
        for r in &rows {
            if !base_null && r.get("variant").map(py_str).as_deref() == Some(base.as_str()) {
                if let Some(Value::Dict(ms)) = r.get("metrics") {
                    for (m, c) in ms {
                        base_means.insert((cell(r), m.clone()), c.get("mean").and_then(|v| v.as_f64()).unwrap_or(0.0));
                    }
                }
            }
        }
        for r in &rows {
            let variant = r.get("variant").map(py_str).unwrap_or_default();
            let at = cell(r);
            if let Some(Value::Dict(ms)) = r.get("metrics") {
                for (m, c) in ms {
                    let mean = c.get("mean").and_then(|v| v.as_f64()).unwrap_or(0.0);
                    vals.push((mean, false, format!("mean {variant}.{m}{at} ({})", exp.id)));
                    if let Some(delta) = c.get("delta").and_then(|v| v.as_f64()) {
                        vals.push((delta, false, format!("change in {m}, {variant} vs {base}{at} ({})", exp.id)));
                        if let Some(bm) = base_means.get(&(at.clone(), m.clone())).copied().filter(|b| *b != 0.0) {
                            vals.push((delta / bm * 100.0, true, format!("% change in {m}, {variant} vs {base}{at} ({})", exp.id)));
                        }
                    }
                }
            }
        }
    }
    Ok(vals)
}

/// (value, is_percent, tolerance): the tolerance is half a unit of the last printed decimal.
fn parse_number(text: &str) -> Option<(f64, bool, f64)> {
    let mut t = text.replace('−', "-").replace(' ', "");
    let pct = t.ends_with('%');
    if pct {
        t.pop();
    }
    let decimals = match t.rsplit_once('.') {
        Some((_, tail)) => tail.chars().count(),
        None => 0,
    };
    let v = crate::pyfloat::parse_float(&t)?;
    Some((v, pct, 0.5 * 10f64.powf(-(decimals as f64))))
}

fn anchor_pass(led: &Ledger, root: &Path, entry: &mut Dict) -> Res<()> {
    let table = authority(led)?;
    let vals = grounding_values(led)?;
    let mut terms: IndexMap<String, (String, i64, usize)> = IndexMap::new();
    let mut grounded: Vec<Value> = Vec::new();
    let mut numbers: i64 = 0;
    let path = root.join(entry.get("path").map(py_str).unwrap_or_default());
    let Ok(bytes) = std::fs::read(&path) else { return Ok(()) };
    let text = String::from_utf8_lossy(&bytes).replace("\r\n", "\n").replace('\r', "\n");
    let term_res: Vec<(String, String, FRegex)> = table
        .iter()
        .filter_map(|(t, k)| FRegex::new(&format!(r"(?<![\w.]){}(?![\w.])", fancy_regex::escape(t))).ok().map(|r| (t.clone(), k.clone(), r)))
        .collect();
    for (i, line) in crate::ledger::py_splitlines(&text).into_iter().take(4000).enumerate() {
        let i = i + 1;
        // a name as LaTeX and Markdown escape it (seq\_len) is the name
        let low = line.to_lowercase().replace("\\_", "_");
        for (term, kind, re) in &term_res {
            if low.contains(term.as_str()) && re.is_match(&low).unwrap_or(false) {
                let hit = terms.entry(term.clone()).or_insert((kind.clone(), 0, i));
                hit.1 += 1;
            }
        }
        for m in number_re().find_iter(line).flatten() {
            numbers += 1;
            if numbers > 300 {
                break;
            }
            let Some((value, pct, tol)) = parse_number(m.as_str()) else { continue };
            let hits: Vec<String> = vals.iter().filter(|(v, p, _)| *p == pct && (v - value).abs() <= tol).map(|(_, _, w)| w.clone()).collect();
            if !hits.is_empty() && hits.len() <= 2 {
                grounded.push(crate::obj! {"text" => crate::rbyaml::py_strip(m.as_str()).to_string(), "line" => i, "matches" => Value::list_of_str(hits)});
            }
        }
    }
    let mut ts: Vec<(String, (String, i64, usize))> = terms.into_iter().collect();
    ts.sort_by(|a, b| b.1 .1.cmp(&a.1 .1));
    let terms_v: Vec<Value> = ts.into_iter().take(10).map(|(t, (k, n, line))| crate::obj! {"term" => t, "kind" => k, "count" => n, "line" => line}).collect();
    grounded.truncate(12);
    entry.insert("anchors".into(), crate::obj! {"terms" => Value::List(terms_v), "grounded" => Value::List(grounded), "numbers" => numbers});
    Ok(())
}

fn same_metrics(a: &IndexMap<String, f64>, b: &Dict) -> bool {
    a.len() == b.len() && a.iter().all(|(k, v)| b.get(k).and_then(|x| x.as_f64()).map_or(false, |x| x == *v))
}

/// The deterministic residue: a structured file whose numbers exactly one experiment's criteria read.
fn proposals(led: &Ledger, sources: &mut [Dict]) -> Res<Vec<Dict>> {
    let aliases = crate::journal::names(led)?;
    let recorded = led.standing_runs()?;
    let mut out = Vec::new();
    for s in sources.iter_mut() {
        if s.get("kind").and_then(|k| k.as_str()) != Some("structured") || !s.get("numbers").map_or(false, |n| n.truthy()) {
            continue;
        }
        let path = s.get("path").map(py_str).unwrap_or_default();
        let nums = crate::journal::numbers_in(&led.root, &path);
        let canon: HashSet<String> = nums.keys().filter_map(|k| crate::journal::canon_of(k, &aliases)).collect();
        if canon.is_empty() {
            s.insert("note".into(), Value::Str("holds numbers nothing declares yet".into()));
            continue;
        }
        if recorded.iter().any(|ev| same_metrics(&ev.metrics, &nums)) {
            s.insert("note".into(), Value::Str("already in the record".into()));
            continue;
        }
        let (target, options) = crate::journal::target(led, &canon, &aliases)?;
        if let Some(t) = target {
            let written = crate::pytime::file_time(&led.root.join(&path));
            let made = led.load::<Experiment>(&t)?.created_at;
            let before = written.as_ref().map_or(false, |w| !w.is_empty() && crate::pytime::before(Some(w), Some(&made)));
            out.push(crate::dict! {
                "path" => path.clone(), "experiment" => t.clone(), "written" => written.clone(), "before_experiment" => before,
                "argv" => Value::list_of_str(vec!["run".into(), "add".into(), t.clone(), "--from".into(), path.clone()])
            });
        } else if !options.is_empty() {
            s.insert("note".into(), Value::Str(format!("could belong to {}: say which", options.iter().take(4).cloned().collect::<Vec<_>>().join(" or "))));
        }
    }
    Ok(out)
}

/// The handles the agent compiles against: what exists, by id, so nothing is invented twice.
fn state_lines(led: &Ledger) -> Res<Vec<String>> {
    let st = Value::Dict(led.status(None)?);
    let list = |k: &str| -> Vec<Value> {
        match st.get(k) {
            Some(Value::List(l)) => l.clone(),
            _ => Vec::new(),
        }
    };
    let gs = |v: &Value, k: &str, d: &str| v.get(k).map(py_str).unwrap_or_else(|| d.to_string());
    let truthy = |v: &Value, k: &str| v.get(k).map_or(false, |x| x.truthy());
    let mut lines = Vec::new();
    for q in list("questions") {
        lines.push(format!("question {}: {} ({})", gs(&q, "id", ""), gs(&q, "text", ""), gs(&q, "state", "open")) + &if truthy(&q, "parent") { format!(" under {}", gs(&q, "parent", "")) } else { String::new() });
    }
    for e in list("experiments") {
        let arms: Vec<String> = match e.get("variants") {
            Some(Value::List(vs)) => vs.iter().map(|v| format!("{} ({})", gs(v, "name", ""), gs(v, "role", ""))).collect(),
            _ => Vec::new(),
        };
        let arms = if arms.is_empty() { "no variants".to_string() } else { arms.join(" vs ") };
        let varies: Vec<String> = match e.get("varies") {
            Some(Value::List(l)) => l.iter().map(py_str).collect(),
            _ => Vec::new(),
        };
        lines.push(
            format!("experiment {}: {} · {} · {arms}", gs(&e, "id", ""), gs(&e, "title", ""), gs(&e, "state", "planned"))
                + &if varies.is_empty() { String::new() } else { format!(" · varies {}", varies.join(", ")) }
                + &if truthy(&e, "hypothesis") { format!(" · hypothesis: {}", gs(&e, "hypothesis", "")) } else { String::new() },
        );
    }
    for a in list("analyses") {
        if truthy(&a, "retracted") {
            continue;
        }
        let n = match a.get("comparisons") {
            Some(Value::List(l)) => l.len(),
            _ => 0,
        };
        lines.push(
            format!("analysis {} on {}: {n} {} ({})", gs(&a, "id", ""), gs(&a, "experiment_id", ""), crate::assess::plural("comparison", n as i64), gs(&a, "basis", ""))
                + &if truthy(&a, "superseded_by") { format!(" · superseded by {}", gs(&a, "superseded_by", "")) } else { String::new() },
        );
    }
    for r in list("resources") {
        let state = r.get("availability").map(|a| gs(a, "state", "")).unwrap_or_default();
        lines.push(format!("resource {}: {} ({}) · {state}", gs(&r, "id", ""), gs(&r, "name", ""), gs(&r, "type", "")));
    }
    let mut metrics: Vec<String> = list("metrics").iter().map(|m| gs(m, "id", "")).collect();
    if !metrics.is_empty() {
        metrics.sort();
        lines.push(format!("metrics known: {}", metrics.join(", ")));
    }
    if let Some(Value::Dict(tally)) = st.get("tally") {
        if !tally.is_empty() {
            let mut items: Vec<(&String, &Value)> = tally.iter().collect();
            items.sort_by(|a, b| a.0.cmp(b.0));
            lines.push("criteria: ".to_string() + &items.iter().map(|(k, n)| format!("{} {k}", py_str(n))).collect::<Vec<_>>().join(" · "));
        }
    }
    Ok(lines)
}

pub fn cmd_compile(paths: &[String], out: &mut Out) -> Res<i32> {
    let led = match Ledger::open(None) {
        Ok(l) => Some(l),
        Err(e) if e.code == "E_NO_PROJECT" => None,
        Err(e) => return Err(e),
    };
    let root = match &led {
        Some(l) => l.root.clone(),
        None => std::env::current_dir().unwrap_or_default(),
    };
    let mut sources: Vec<Dict> = Vec::new();
    let mut outside: Vec<String> = Vec::new();
    if !paths.is_empty() {
        let (fs, out_) = files(&root, paths);
        outside = out_;
        let names = match &led {
            Some(l) => crate::journal::names(l)?,
            None => IndexMap::new(),
        };
        sources = fs.iter().map(|f| source_entry(&root, f, &names)).collect();
        if let Some(l) = &led {
            for s in sources.iter_mut() {
                if s.get("kind").and_then(|k| k.as_str()) == Some("prose") {
                    anchor_pass(l, &root, s)?;
                }
            }
        }
    }
    let props = match &led {
        Some(l) => proposals(l, &mut sources)?,
        None => Vec::new(),
    };
    let gs = |d: &Dict, k: &str| d.get(k).map(py_str).unwrap_or_default();
    if !sources.is_empty() {
        out.say(format!("Sources ({}):", sources.len()));
        for s in &sources {
            let mut line = format!("  {}  ·  {}", gs(s, "path"), gs(s, "kind"));
            if let Some(h) = s.get("heading").filter(|h| h.truthy()) {
                line += &format!("  ·  \"{}\"", py_str(h));
            }
            if let Some(n) = s.get("number_count").and_then(|n| n.as_i64()).filter(|n| *n != 0) {
                let nums: Vec<String> = match s.get("numbers") {
                    Some(Value::List(l)) => l.iter().map(py_str).collect(),
                    _ => Vec::new(),
                };
                line += &format!("  ·  {n} {} ({}{})", crate::assess::plural("number", n), nums.iter().take(4).cloned().collect::<Vec<_>>().join(", "), if n > 4 { ", ..." } else { "" });
            }
            if let Some(Value::List(cols)) = s.get("columns").filter(|c| c.truthy()) {
                line += &format!("  ·  columns {}", cols.iter().take(6).map(py_str).collect::<Vec<_>>().join(", "));
            }
            let tables: Vec<Value> = match s.get("tables") {
                Some(Value::List(l)) => l.clone(),
                _ => Vec::new(),
            };
            if s.get("kind").and_then(|k| k.as_str()) == Some("database") && s.get("tables").is_some() {
                line += &format!("  ·  {} {}", tables.len(), crate::assess::plural("table", tables.len() as i64));
            }
            if let Some(note) = s.get("note").filter(|n| n.truthy()) {
                line += &format!("  ·  {}", py_str(note));
            }
            out.say(line);
            for t in &tables {
                let n = t.get("rows").and_then(|r| r.as_i64()).unwrap_or(0);
                let columns: Vec<String> = match t.get("columns") {
                    Some(Value::List(l)) => l.iter().map(py_str).collect(),
                    _ => Vec::new(),
                };
                let mut said = format!("      table {}: {n} {} · columns {}", t.get("name").map(py_str).unwrap_or_default(), crate::assess::plural("row", n), columns.join(", "));
                if let Some(Value::Dict(m)) = t.get("metrics").filter(|m| m.truthy()) {
                    let named: Vec<String> = m.iter().map(|(c, metric)| if py_str(metric) == *c { c.clone() } else { format!("{c} ({})", py_str(metric)) }).collect();
                    said += &format!(" · metric columns {}", named.join(", "));
                }
                out.say(said);
            }
            if tables.iter().any(|t| t.get("metrics").map_or(false, |m| m.truthy())) {
                out.say(format!("      a row is a run: rb run add <experiment> --from {} --table <table> --where <column>=<value>", crate::cli::shlex_quote(&gs(s, "path"))));
            }
            if let Some(a) = s.get("anchors").filter(|a| a.truthy()) {
                let terms: Vec<Value> = match a.get("terms") {
                    Some(Value::List(l)) => l.clone(),
                    _ => Vec::new(),
                };
                let grounded: Vec<Value> = match a.get("grounded") {
                    Some(Value::List(l)) => l.clone(),
                    _ => Vec::new(),
                };
                if !terms.is_empty() {
                    let shown = terms
                        .iter()
                        .take(6)
                        .map(|t| {
                            let n = t.get("count").and_then(|c| c.as_i64()).unwrap_or(0);
                            format!("{} ({})", t.get("term").map(py_str).unwrap_or_default(), t.get("kind").map(py_str).unwrap_or_default())
                                + &if n > 1 { format!(" ×{n}") } else { String::new() }
                        })
                        .collect::<Vec<_>>()
                        .join(" · ");
                    out.say(format!("      speaks the record's language: {shown}"));
                }
                for g in grounded.iter().take(6) {
                    let matches: Vec<String> = match g.get("matches") {
                        Some(Value::List(l)) => l.iter().map(py_str).collect(),
                        _ => Vec::new(),
                    };
                    out.say(format!(
                        "      line {}: {} grounds to {}",
                        g.get("line").map(py_str).unwrap_or_default(),
                        g.get("text").map(py_str).unwrap_or_default(),
                        matches.join(" or ")
                    ));
                }
                let loose = a.get("numbers").and_then(|n| n.as_i64()).unwrap_or(0) - grounded.len() as i64;
                if loose > 0 && (!terms.is_empty() || !grounded.is_empty()) {
                    out.say(format!(
                        "      {loose} number{} nowhere in the record: unplaced, not guessed at",
                        if loose == 1 { " grounds" } else { "s ground" }
                    ));
                }
            }
        }
    } else if !paths.is_empty() {
        out.say("Nothing readable at the paths given.");
    }
    for o in &outside {
        out.say(format!("  outside the project, so sources cannot cite it: {o} (copy it into the repository first)"));
    }
    let elsewhere = if led.is_none() { crate::elsewhere::record_elsewhere(&root) } else { None };
    match &led {
        // a worktree whose project's record is in another worktree: bring that record here, never start a second one
        None if elsewhere.is_some() => {
            let there = elsewhere.as_ref().unwrap();
            out.say("");
            out.say(there.message());
            out.say(there.fix(false));
        }
        None => {
            out.say("");
            out.say("No record here yet. A compiled record starts with the work's theme, then each question the work asks:");
            out.say("  rb init \"<the work's theme>\"");
            out.say("  rb question add \"<what are you trying to find out?>\"");
            out.say(START_POINTER);
        }
        Some(l) => {
            let lines = state_lines(l)?;
            if !lines.is_empty() {
                out.say("");
                out.say("Compile against what exists (ids are handles, not suggestions):");
                out.say_all(lines.iter().map(|x| format!("  {x}")));
            }
            if paths.is_empty() {
                let all = crate::journal::found_all(l, crate::journal::Seen::User)?;
                out.say("");
                if !all.placed.is_empty() || !all.unplaced.is_empty() {
                    out.say(format!("The journal has seen work nobody recorded: {}.", crate::cmds::reading::found_words(&all)));
                } else {
                    out.say("Nothing given and nothing unplaced in the journal. Point this at files or a directory: rb compile <path...>");
                }
            }
        }
    }
    if !props.is_empty() {
        out.say("");
        out.say(format!(
            "Matched by metric name ({}): only one experiment reads these names. A name is not provenance: record one only when it is that experiment's run.",
            props.len()
        ));
        for p in &props {
            let argv: Vec<String> = match p.get("argv") {
                Some(Value::List(l)) => l.iter().map(py_str).collect(),
                _ => Vec::new(),
            };
            let when = match p.get("written").filter(|w| w.truthy()).map(py_str) {
                Some(w) => {
                    format!("   (written {}", w.chars().take(10).collect::<String>())
                        + &if p.get("before_experiment").map_or(false, |b| b.truthy()) {
                            format!(", before {} existed: likely not its run)", gs(p, "experiment"))
                        } else {
                            ")".to_string()
                        }
                }
                None => String::new(),
            };
            out.say(format!("  rb {}{when}", argv.join(" ")));
        }
    }
    out.say("");
    out.say("The contract (for the agent doing the reading):");
    for (i, line) in CONTRACT.iter().enumerate() {
        out.say(format!("  {}. {line}", i + 1));
    }
    out.next = if !props.is_empty() {
        props
            .iter()
            .take(2)
            .map(|p| {
                let argv: Vec<String> = match p.get("argv") {
                    Some(Value::List(l)) => l.iter().map(py_str).collect(),
                    _ => Vec::new(),
                };
                format!("rb {}", argv.join(" "))
            })
            .collect()
    } else if let Some(l) = &led {
        // a record with no question has no standing to read yet: the work's question comes first
        if l.live::<Question>()?.is_empty() { vec!["rb question add \"<the question the work answers>\"".into()] } else { vec!["rb status".into()] }
    } else if elsewhere.is_some() {
        Vec::new()
    } else {
        vec!["rb init \"<the work's theme>\"".into(), "rb docs --start".into()]
    };
    out.data.insert("sources".into(), Value::List(sources.into_iter().map(Value::Dict).collect()));
    out.data.insert("outside".into(), Value::list_of_str(outside));
    out.data.insert("proposals".into(), Value::List(props.into_iter().map(Value::Dict).collect()));
    out.data.insert("project".into(), Value::Bool(led.is_some()));
    out.data.insert("contract".into(), Value::list_of_str(CONTRACT.iter().map(|s| s.to_string())));
    Ok(0)
}
