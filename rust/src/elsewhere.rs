//! What other branches and worktrees of this clone hold for the record, and this checkout does not. rb reads those
//! places and never writes to them.

use crate::errors::Res;
use crate::ledger::Ledger;
use crate::record::Experiment;
use crate::sources::{git, git_top};
use crate::value::{Dict, Value};
use indexmap::IndexMap;
use std::collections::HashSet;
use std::path::{Path, PathBuf};

pub const MAX_PLACES: usize = 20;
const KIND_OF_DIR: &[(&str, &str)] = &[("runs", "run"), ("notes", "note"), ("variants", "variant"), ("criteria", "criterion"), ("analyses", "analysis")];
const TOP_KINDS: &[(&str, &str)] = &[("questions", "question"), ("notes", "note"), ("metrics", "metric"), ("resources", "resource"), ("decisions", "decision")];
const ORDER: &[&str] = &["experiment", "variant", "criterion", "run", "analysis", "note", "question", "metric", "resource", "decision"];

fn plural(kind: &str) -> &'static str {
    match kind {
        "run" => "runs",
        "note" => "notes",
        "variant" => "variants",
        "criterion" => "criteria",
        "analysis" => "analyses",
        "experiment" => "experiments",
        "question" => "questions",
        "metric" => "metrics",
        "resource" => "resources",
        _ => "decisions",
    }
}

fn lookup(table: &[(&str, &'static str)], key: &str) -> Option<&'static str> {
    table.iter().find(|(k, _)| *k == key).map(|(_, v)| *v)
}

/// (kind, experiment or "", id) of a record file, by its path inside .rb/; None for what is not a record.
fn classify(rel: &str) -> Option<(&'static str, String, String)> {
    let parts: Vec<&str> = rel.split('/').collect();
    if !rel.ends_with(".yaml") || parts[0] == "cache" || parts[0] == "journal" {
        return None;
    }
    let stem = |p: &str| p[..p.len() - ".yaml".len()].to_string();
    if parts[0] == "experiments" && parts.len() == 3 && parts[2] == "experiment.yaml" {
        return Some(("experiment", parts[1].to_string(), parts[1].to_string()));
    }
    if parts[0] == "experiments" && parts.len() == 4 {
        if let Some(kind) = lookup(KIND_OF_DIR, parts[2]) {
            return Some((kind, parts[1].to_string(), stem(parts[3])));
        }
    }
    if parts.len() == 2 {
        if let Some(kind) = lookup(TOP_KINDS, parts[0]) {
            return Some((kind, String::new(), stem(parts[1])));
        }
    }
    None
}

/// What a commit has changed in the record since it and HEAD parted: path inside .rb/ -> A (added) or M.
fn committed(top: &Path, head: &str, tip: &str, prefix: &str) -> IndexMap<String, char> {
    let range = format!("{head}...{tip}");
    let out = git(top, &["diff", "--name-status", "--no-renames", "-z", &range, "--", prefix]).unwrap_or_default();
    let fields: Vec<&str> = out.split('\0').collect();
    let mut changes = IndexMap::new();
    let lead = format!("{prefix}/");
    let mut i = 0;
    while i + 1 < fields.len() {
        let (status, path) = (fields[i], fields[i + 1]);
        i += 2;
        if let Some(c) = status.chars().next() {
            if path.starts_with(&lead) && (c == 'A' || c == 'M') {
                changes.insert(path[lead.len()..].to_string(), c);
            }
        }
    }
    changes
}

/// What a worktree has in its record and has not committed: path inside .rb/ -> A or M.
fn uncommitted(tree: &Path, prefix: &str) -> IndexMap<String, char> {
    let spec = format!(":(top){prefix}");
    let out = git(tree, &["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", &spec]).unwrap_or_default();
    let mut changes = IndexMap::new();
    let lead = format!("{prefix}/");
    for entry in out.split('\0') {
        let chars: Vec<char> = entry.chars().collect();
        if chars.len() > 3 {
            let rest: String = chars[3..].iter().collect();
            if let Some(path) = rest.strip_prefix(&lead) {
                let xy: String = chars[..2].iter().collect();
                if !xy.contains('D') {
                    changes.insert(path.to_string(), if xy == "??" || xy.contains('A') { 'A' } else { 'M' });
                }
            }
        }
    }
    changes
}

/// A worktree of the clone as git lists it: its path as git gives it (`listed`) and resolved, its commit and branch,
/// and whether its directory is gone (removed without git worktree prune).
pub struct Worktree {
    pub listed: String,
    pub path: PathBuf,
    pub head: Option<String>,
    pub branch: Option<String>,
    pub gone: bool,
    /// locked with `git worktree lock`: git keeps it while its folder is away (a removable drive), and so does rb
    pub locked: bool,
}

fn resolve(p: &str) -> PathBuf {
    crate::sys::canonicalize(p).unwrap_or_else(|_| PathBuf::from(p))
}

/// Every worktree of the clone, as git lists them (the main one first).
pub fn worktrees(top: &Path) -> Vec<Worktree> {
    let out = git(top, &["worktree", "list", "--porcelain"]).unwrap_or_default();
    let mut trees = Vec::new();
    for block in out.trim().split("\n\n") {
        let (mut listed, mut head, mut branch, mut prunable, mut locked) = (None, None, None, false, false);
        for line in block.lines() {
            let (key, value) = line.split_once(' ').unwrap_or((line, ""));
            match key {
                "worktree" => listed = Some(value.to_string()),
                "HEAD" => head = Some(value.to_string()),
                "branch" => branch = Some(value.strip_prefix("refs/heads/").unwrap_or(value).to_string()),
                "prunable" => prunable = true,
                "locked" => locked = true,
                _ => {}
            }
        }
        if let Some(listed) = listed {
            let path = resolve(&listed);
            let gone = prunable || !path.is_dir();
            trees.push(Worktree { listed, path, head, branch, gone, locked });
        }
    }
    trees
}

/// A place as rb names it: its branch and its path, as git gives them; a detached one by its commit.
pub fn label(branch: Option<&str>, path: &str, head: Option<&str>) -> String {
    match branch {
        Some(b) => format!("{b} · {path}"),
        None => format!("detached at {} · {path}", head.map(|h| h.chars().take(12).collect::<String>()).unwrap_or_default()),
    }
}

/// A path as git lists it, with a directory below it (`rel`, "" for none).
fn joined(listed: &str, rel: &str) -> String {
    if rel.is_empty() {
        listed.to_string()
    } else {
        format!("{}/{rel}", listed.trim_end_matches('/'))
    }
}

/// The label of the worktree whose top is `top`, as git lists it, naming the directory `rel` below it; its path alone
/// when git does not list it.
fn label_of(trees: &[Worktree], top: &Path, rel: &str) -> String {
    match trees.iter().find(|w| w.path == top) {
        Some(w) => label(w.branch.as_deref(), &joined(&w.listed, rel), w.head.as_deref()),
        None => joined(&crate::sys::slashed(top), rel),
    }
}

/// The line rb status and rb context say once in a linked worktree with no record of its own, nested in the checkout
/// whose record its commands write to: the worktree, and the record's own directory in that checkout.
pub fn nested_line(led: &Ledger) -> Option<String> {
    if led.here == led.root {
        return None;
    }
    let top = led.worktree().map(|w| w.to_path_buf()).unwrap_or_else(|| led.here.clone());
    let trees = worktrees(&top);
    let home = git_top(&led.root).unwrap_or_else(|| led.root.clone());
    let rel = crate::sources::rel_to(&led.root, &home).unwrap_or_default();
    Some(format!(
        "This worktree ({}) has no record of its own: rb writes to the record in {}, and each run keeps this worktree's code.",
        label_of(&trees, &top, ""),
        label_of(&trees, &home, &rel)
    ))
}

/// The checkout the command ran in, where it is not the record's own: {path (the worktree's top), branch, commit, dirty}.
pub fn checkout_value(led: &Ledger) -> Option<Value> {
    if led.here == led.root {
        return None;
    }
    let st = crate::sources::git_state(&led.here, crate::ledger::DIR).unwrap_or_default();
    let get = |k: &str| st.get(k).cloned().unwrap_or(Value::Null);
    Some(crate::obj! {
        "path" => crate::sys::slashed(led.worktree().unwrap_or(&led.here)), "branch" => get("branch"), "commit" => get("commit"),
        "dirty" => st.get("dirty").cloned().unwrap_or(Value::Bool(false))
    })
}

/// Where another worktree of this repository holds the project's record, for a worktree with none: its place, this
/// one's, and how to bring the record here.
pub struct RecordThere {
    here: String,
    there: String,
    path: String,
    /// what to merge: the holder's branch, or its commit when detached; None when that commit is still to be made
    merge: Option<String>,
    uncommitted: bool,
}

impl RecordThere {
    pub fn message(&self) -> String {
        format!("No record in this worktree ({}): the project's record is in {}.", self.here, self.there)
    }

    /// What brings the record here; for rb init, also how to start a separate project instead.
    pub fn fix(&self, init: bool) -> String {
        let merge = self.merge.as_deref().unwrap_or("<that commit>");
        let bring = if self.uncommitted {
            format!("To bring it here, commit .rb/ in {}, then run git merge {merge} here.", self.path)
        } else {
            format!("To bring it here, run git merge {merge} here.")
        };
        if init {
            format!("{bring} To start a separate project here instead, run rb init --new-project.")
        } else {
            bring
        }
    }
}

/// The record the main checkout of this repository holds, at the place in it that `start` is in this linked worktree
/// (or above it), when `start` and everything above it here hold none. The main checkout itself is never pointed at
/// a linked worktree's record: it starts its own.
pub fn record_elsewhere(start: &Path) -> Option<RecordThere> {
    let start = crate::sources::resolve_path(start);
    crate::ledger::linked_top(&start)?;
    let top = git_top(&start)?;
    let rel = crate::sources::rel_to(&start, &top).unwrap_or_default();
    let trees = worktrees(&top);
    for w in trees.iter().take(1).filter(|w| w.path != top && !w.gone) {
        let mut at = PathBuf::from(&rel);
        loop {
            let dir = w.path.join(&at);
            if dir.join(crate::ledger::DIR).join(crate::ledger::ROOT_FILE).is_file() {
                let rb_dir = crate::sources::rel_to(&crate::sources::resolve_path(&dir.join(crate::ledger::DIR)), &w.path).unwrap_or_else(|| crate::ledger::DIR.to_string());
                let spec = format!(":(top){rb_dir}");
                // committed where the holder's HEAD has it, whatever git status says of an ignored .rb/
                let changed = git(&w.path, &["status", "--porcelain", "--untracked-files=all", "--", &spec]).map_or(false, |s| !s.trim().is_empty());
                let in_head = git(&w.path, &["cat-file", "-e", &format!("HEAD:{rb_dir}/{}", crate::ledger::ROOT_FILE)]).is_some();
                let uncommitted = changed || !in_head;
                // a detached holder's commit holds the record only once it is committed
                let merge = match &w.branch {
                    Some(b) => Some(b.clone()),
                    None if !uncommitted => Some(w.head.clone().unwrap_or_default().chars().take(12).collect()),
                    None => None,
                };
                let place = joined(&w.listed, &crate::sys::slashed(&at)); // the record's own directory there
                return Some(RecordThere {
                    here: label_of(&trees, &top, ""),
                    there: label(w.branch.as_deref(), &place, w.head.as_deref()),
                    path: place,
                    merge,
                    uncommitted,
                });
            }
            if !at.pop() {
                break;
            }
        }
    }
    None
}

/// The uid of the project whose record a place holds: its project.yaml on disk in a worktree, else at its commit.
fn uid_at(top: &Path, dir: Option<&Path>, head: Option<&str>, prefix: &str) -> Option<String> {
    let text = match dir.map(|d| d.join(prefix).join(crate::ledger::ROOT_FILE)).filter(|f| f.is_file()) {
        Some(f) => std::fs::read_to_string(f).ok()?,
        None => git(top, &["show", &format!("{}:{prefix}/{}", head?, crate::ledger::ROOT_FILE)])?,
    };
    match crate::rbyaml::loads(&text, crate::ledger::ROOT_FILE).ok()? {
        Value::Dict(d) => d.get("uid").and_then(|v| v.as_str()).map(|u| u.to_string()),
        _ => None,
    }
}

struct Place {
    branch: Option<String>,
    worktree: Option<String>,
    path: Option<String>,
    head: Option<String>,
    changes: IndexMap<String, char>,
    local: HashSet<String>,
    same_project: bool,
    gone: bool,
}

/// A place as rb status gives it: its value in --json, and the label rb names it by.
pub struct Elsewhere {
    pub value: Value,
    pub label: String,
}

fn opt(s: &Option<String>) -> Value {
    match s {
        Some(x) => Value::Str(x.clone()),
        None => Value::Null,
    }
}

/// Each other place of this clone holding work on the record that is not here, or a separate project's record:
/// {branch, worktree, path, head, added, changed, uncommitted, same_project, gone}, `added` by experiment ("" for the
/// project) and kind, worktrees first, then branches, most recent first. Only work on `experiment` when one is named.
pub fn elsewhere(led: &Ledger, experiment: Option<&str>) -> Res<Vec<Elsewhere>> {
    let Some(top) = git_top(&led.root) else { return Ok(Vec::new()) };
    let head = git(&led.root, &["rev-parse", "--verify", "-q", "HEAD"]).unwrap_or_default().trim().to_string();
    if head.is_empty() {
        return Ok(Vec::new());
    }
    let top = crate::sys::canonicalize(&top).unwrap_or(top);
    let dir = crate::sys::canonicalize(&led.dir).unwrap_or_else(|_| led.dir.clone());
    let Some(prefix) = crate::sources::rel_to(&dir, &top) else {
        return Err(crate::errors::RBError::msg("E_INTERNAL", "the record is not inside its repository"));
    };
    let mine = led.project().ok().map(|p| p.uid.clone()).filter(|u| !u.is_empty());
    let same = |uid: Option<String>| match (&mine, uid) {
        (Some(m), Some(u)) => *m == u,
        _ => true,
    };
    let mut places: Vec<Place> = Vec::new();
    let mut covered: HashSet<String> = HashSet::new();
    for wt in worktrees(&top) {
        if let Some(b) = &wt.branch {
            covered.insert(b.clone());
        }
        if wt.path == top {
            continue;
        }
        let mut changes = match &wt.head {
            Some(h) if !h.is_empty() => committed(&top, &head, h, &prefix),
            _ => IndexMap::new(),
        };
        // a worktree removed without git worktree prune: what it committed is still in git, and nothing else is
        let local = if wt.gone { IndexMap::new() } else { uncommitted(&wt.path, &prefix) };
        let local_keys: HashSet<String> = local.keys().cloned().collect();
        for (k, v) in local {
            changes.insert(k, v);
        }
        let same_project = same(uid_at(&top, (!wt.gone).then_some(wt.path.as_path()), wt.head.as_deref(), &prefix));
        let name = wt.path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        places.push(Place {
            branch: wt.branch.clone(), worktree: Some(name), path: Some(wt.listed.clone()), head: wt.head.clone(), changes, local: local_keys,
            same_project, gone: wt.gone,
        });
    }
    let refs = git(&top, &["for-each-ref", "--sort=-committerdate", "--format=%(refname:short) %(objectname)", "refs/heads"]).unwrap_or_default();
    for line in refs.lines() {
        let (name, tip) = line.split_once(' ').unwrap_or((line, ""));
        if tip.is_empty() || covered.contains(name) {
            continue;
        }
        if git(&top, &["merge-base", "--is-ancestor", tip, &head]).is_some() {
            continue; // merged here already
        }
        let changes = committed(&top, &head, tip, &prefix);
        if !changes.is_empty() {
            let same_project = same(uid_at(&top, None, Some(tip), &prefix));
            places.push(Place {
                branch: Some(name.to_string()), worktree: None, path: None, head: Some(tip.to_string()), changes, local: HashSet::new(), same_project,
                gone: false,
            });
        }
    }
    let mut out = Vec::new();
    for p in &places {
        let mut added: IndexMap<String, IndexMap<String, Vec<String>>> = IndexMap::new();
        let mut changed: Vec<String> = Vec::new();
        let mut n_local = 0i64;
        let mut items: Vec<(&String, &char)> = p.changes.iter().collect();
        items.sort();
        for (rel, status) in items {
            let Some((kind, exp, id)) = classify(rel) else { continue };
            if let Some(e) = experiment {
                if exp != e {
                    continue;
                }
            }
            if p.local.contains(rel) {
                n_local += 1;
            }
            if *status == 'A' {
                added.entry(exp).or_default().entry(kind.to_string()).or_default().push(id);
            } else {
                changed.push(rel.clone());
            }
        }
        // a separate project's record is listed as one, whatever it holds
        if !added.is_empty() || !changed.is_empty() || !p.same_project {
            let mut a = Dict::new();
            for (exp, kinds) in added {
                let mut k = Dict::new();
                for (kind, ids) in kinds {
                    k.insert(kind, Value::List(ids.into_iter().map(Value::Str).collect()));
                }
                a.insert(exp, Value::Dict(k));
            }
            let value = crate::obj! {
                "branch" => opt(&p.branch),
                "worktree" => opt(&p.worktree),
                "head" => opt(&p.head),
                "added" => Value::Dict(a),
                "changed" => Value::List(changed.into_iter().map(Value::Str).collect()),
                "uncommitted" => n_local,
                "path" => opt(&p.path),
                "same_project" => p.same_project,
                "gone" => p.gone,
            };
            let at = p.head.as_deref().filter(|h| !h.is_empty()).map(|h| h.chars().take(12).collect::<String>());
            let named = match (&p.path, &p.branch, &at) {
                (Some(path), None, _) => label(None, path, at.as_deref()),
                (Some(path), Some(b), Some(h)) => format!("{} at {h}", label(Some(b), path, None)),
                (Some(path), Some(b), None) => label(Some(b), path, None),
                (None, Some(b), Some(h)) => format!("{b} at {h}"),
                (None, b, _) => b.clone().unwrap_or_default(),
            } + if p.gone { " (removed since)" } else { "" };
            out.push(Elsewhere { value, label: named });
        }
    }
    out.truncate(MAX_PLACES);
    Ok(out)
}

/// The places as rb status prints them, one line each, each named by its branch and path as git gives them.
pub fn lines(led: &Ledger, places: &[Elsewhere]) -> Res<Vec<String>> {
    Ok(said(led, places)?
        .into_iter()
        .zip(places)
        .map(|((adds, n_local), Elsewhere { label, value })| match value.get("same_project") {
            Some(Value::Bool(false)) => format!("  {label}: a separate project, with its own record"),
            _ => format!("  {label}: {adds}") + &if n_local > 0 { format!(" · {n_local} record file{} not committed", if n_local == 1 { "" } else { "s" }) } else { String::new() },
        })
        .collect())
}

/// What each place holds that is not here, in rb's words ("1 run on e1 (int8 against fp32); 1 record changed"), and how
/// many of its record files are not committed there; a separate project's says nothing of what it holds.
pub fn said(led: &Ledger, places: &[Elsewhere]) -> Res<Vec<(String, i64)>> {
    said_as(led, places, plural, |k| k.to_string(), false)
}

/// What each place holds that is not here, as a screen says it: the same as `said`, but a criterion is a rule, the
/// word the screens use for it (commands still call it a criterion), and an experiment is named by its title, its id
/// after it ("1 run on int8 against fp32 (e1)").
pub fn said_on_screen(led: &Ledger, places: &[Elsewhere]) -> Res<Vec<(String, i64)>> {
    let on_screen = |k: &str| if k == "criterion" { "rule".to_string() } else { k.to_string() };
    said_as(led, places, |k| if k == "criterion" { "rules" } else { plural(k) }, on_screen, true)
}

fn said_as(led: &Ledger, places: &[Elsewhere], many: fn(&str) -> &'static str, one: impl Fn(&str) -> String, title_first: bool) -> Res<Vec<(String, i64)>> {
    let titles: IndexMap<String, String> = led.all::<Experiment>()?.iter().map(|e| (e.id.clone(), e.title.clone())).collect();
    let mut out = Vec::new();
    for Elsewhere { value: p, .. } in places {
        if p.get("same_project") == Some(&Value::Bool(false)) {
            out.push((String::new(), 0));
            continue;
        }
        let mut parts: Vec<String> = Vec::new();
        if let Some(Value::Dict(added)) = p.get("added") {
            for (exp, kinds) in added {
                let Value::Dict(kinds) = kinds else { continue };
                let count = |k: &str| match kinds.get(k) {
                    Some(Value::List(ids)) => Some(format!("{} {}", ids.len(), if ids.len() == 1 { one(k) } else { many(k).to_string() })),
                    _ => None,
                };
                // a decision's file is the project's, whatever its subject: counted, never said to be on the project
                let decisions = if exp.is_empty() { count("decision") } else { None };
                let counts: Vec<String> = ORDER.iter().filter(|k| **k != "experiment" && !(exp.is_empty() && **k == "decision")).filter_map(|k| count(k)).collect();
                let said = match counts.len() {
                    0 => String::new(),
                    1 => counts[0].clone(),
                    n => format!("{} and {}", counts[..n - 1].join(", "), counts[n - 1]),
                };
                if exp.is_empty() {
                    if !said.is_empty() {
                        parts.push(format!("{said} on the project"));
                    }
                    parts.extend(decisions);
                } else if kinds.contains_key("experiment") {
                    parts.push(format!("new experiment {exp}") + &if said.is_empty() { String::new() } else { format!(" with {said}") });
                } else {
                    parts.push(match titles.get(exp) {
                        Some(t) if title_first => format!("{said} on {t} ({exp})"),
                        Some(t) => format!("{said} on {exp} ({t})"),
                        None => format!("{said} on {exp}"),
                    });
                }
            }
        }
        let n_changed = match p.get("changed") {
            Some(Value::List(l)) => l.len(),
            _ => 0,
        };
        if n_changed > 0 {
            parts.push(format!("{n_changed} record{} changed", if n_changed == 1 { "" } else { "s" }));
        }
        out.push((parts.join("; "), p.get("uncommitted").and_then(|v| v.as_i64()).unwrap_or(0)));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A place that holds criteria not merged here: rb status says "criteria", as its commands do; a screen says
    /// "rules", its word for them, and says every other kind as rb status does.
    #[test]
    fn a_screen_says_a_criterion_not_merged_here_as_a_rule() {
        let dir = std::env::temp_dir().join(format!("rb-elsewhere-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(dir.join(".rb")).unwrap();
        let led = Ledger::new(&dir, "cli", None);
        let ids = |xs: &[&str]| Value::List(xs.iter().map(|x| Value::Str(x.to_string())).collect());
        let place = |kinds: Value| {
            let mut a = Dict::new();
            a.insert("int8".into(), kinds);
            Elsewhere { value: crate::obj! {"added" => Value::Dict(a), "changed" => Value::List(vec![]), "uncommitted" => 0i64, "same_project" => true}, label: "exp-b".into() }
        };
        let mut two = Dict::new();
        two.insert("criterion".into(), ids(&["c1", "c2"]));
        two.insert("run".into(), ids(&["r1"]));
        let mut one = Dict::new();
        one.insert("criterion".into(), ids(&["c3"]));
        let mut new = Dict::new();
        new.insert("experiment".into(), ids(&["int8"]));
        new.insert("criterion".into(), ids(&["c4"]));
        let places = [place(Value::Dict(two)), place(Value::Dict(one)), place(Value::Dict(new))];
        let status: Vec<String> = said(&led, &places).unwrap().into_iter().map(|(s, _)| s).collect();
        let screen: Vec<String> = said_on_screen(&led, &places).unwrap().into_iter().map(|(s, _)| s).collect();
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(status, ["2 criteria and 1 run on int8", "1 criterion on int8", "new experiment int8 with 1 criterion"]);
        assert_eq!(screen, ["2 rules and 1 run on int8", "1 rule on int8", "new experiment int8 with 1 rule"]);
    }

    /// Design pass 1 (P3): a screen names an experiment here by its title, its id after it; rb status keeps its words.
    #[test]
    fn a_screen_names_an_experiment_by_its_title_and_its_id_after_it() {
        let _turn = crate::sys::turns::starting();
        let dir = std::env::temp_dir().join(format!("rb-elsewhere-t-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(&dir).unwrap();
        let led = Ledger::init(&dir, "INT8 ResNet-50 on ImageNet", Some("int8"), Some("cli"), None, false).unwrap();
        let e = crate::ledger::writes::NewExperiment { title: "INT8 against FP32".into(), id: Some("e1".into()), ..Default::default() };
        led.add_experiment(&e).unwrap();
        let mut kinds = Dict::new();
        kinds.insert("run".into(), Value::List(vec![Value::Str("r1".into())]));
        let mut a = Dict::new();
        a.insert("e1".into(), Value::Dict(kinds));
        let places = [Elsewhere { value: crate::obj! {"added" => Value::Dict(a), "changed" => Value::List(vec![]), "uncommitted" => 0i64, "same_project" => true}, label: "exp-b".into() }];
        let status: Vec<String> = said(&led, &places).unwrap().into_iter().map(|(s, _)| s).collect();
        let screen: Vec<String> = said_on_screen(&led, &places).unwrap().into_iter().map(|(s, _)| s).collect();
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(status, ["1 run on e1 (INT8 against FP32)"]);
        assert_eq!(screen, ["1 run on INT8 against FP32 (e1)"]);
    }
}
