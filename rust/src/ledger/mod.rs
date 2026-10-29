//! The `.rb/` store: one project's record, as YAML files beside the code. This file holds the store itself: opening,
//! the files and their index, the lock, the log, and what was changed outside rb. Writes are in `writes.rs`, findings
//! in `findings.rs`, the views over the record in `views.rs`.

pub mod authority;
pub mod cite;
pub mod design;
pub mod findings;
pub mod helpers;
pub mod inquiries;
pub mod relations;
pub mod report_link;
pub mod resync;
pub mod updates;
pub mod support;
pub mod views;
pub mod writes;

pub use authority::{past_when, runs_after_of, runs_after_said, Authority, PENDING_CODES};
pub use design::Design;
pub use helpers::*;
pub use writes::*;

use crate::actor::{self, Actor};
use crate::errors::{RBError, Res};
use crate::model::{problems, VErr};
use crate::pyjson::{self, repr_str, Dumps};
use crate::rbyaml::{self, Document};
use crate::record::*;
use crate::sources::{self, now, sha256_bytes};
use crate::value::{Dict, DictExt, Value};
use indexmap::IndexMap;
use std::any::Any;
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::Mutex;

pub const DIR: &str = RECORD_DIR;
pub const EXT: &str = ".yaml";
pub const ROOT_FILE: &str = "project.yaml";
pub const EXPERIMENT_FILE: &str = "experiment.yaml";
pub const CRITERIA_FILE: &str = "criteria.yaml"; // every criterion of an experiment in one file: read, and moved on the next write
pub const CRITERIA_DIR: &str = "criteria";
pub const VARIANTS_DIR: &str = "variants";
pub const NOTES_DIR: &str = "notes";
pub const GITIGNORE: &str = ".lock\n*.tmp\ncache/\njournal/\n";
/// What git is told about the record's files: never rewrite a line ending in them (a git set to convert them, as it
/// is on Windows by default, would check the record out with \r\n and warn at every commit that rb's files have \n),
/// and merge the log by keeping both sides' lines.
pub const GITATTRIBUTES: &str = "* -text\nlog.jsonl merge=union\n";
/// What fetches the history a shallow clone lacks, and how GitHub Actions checks out one that has it.
pub const UNSHALLOW: &str = "git fetch --unshallow";
pub const UNSHALLOW_CI: &str = "in GitHub Actions, actions/checkout with fetch-depth: 0";

pub const AUTHORITY_WHOLE: &[&str] = &["frozen", "amendments", "retracted", "resolved", "exposures"];
/// Kept whole on a criterion only: what it selects, the questions it is filed to and its declared relations.
pub const CRITERION_WHOLE: &[&str] = &["where", "subject_where", "reference_where", "question_links", "relations"];
/// Kept whole on a setting only.
pub const SETTING_WHOLE: &[&str] = &["design_factor"];
pub const AUTHORITY_FIELDS: &[&str] = &["created_by", "created_at", "set_by", "set_at", "by", "words", "session", "produced_at", "produced_basis"];
/// Kept on a run's or an analysis's receipt: who recorded it, how rb knew and through what (session is kept everywhere,
/// above), and what rb knew of the agent, whole.
pub const RECEIPT_FIELDS: &[&str] = &["actor", "actor_via", "via"];
pub const RECEIPT_WHOLE: &[&str] = &["agent"];
/// Beside who wrote something, through what they wrote it.
pub const WRITERS: &[&str] = &["created_by", "set_by", "by"];
/// A record's keys that name who made a write: rb's words for an edit name a writer only as actor::shown_writer does.
pub const WRITER_IDS: &[&str] = &["created_by", "set_by", "by", "declared_by", "actor"];
/// Those, the keys that say how rb knew, through what, in which session or with which agent, and a branch or a
/// worktree, whose name often names an agent: rb's words for an edit give none of their values.
pub const WRITER_KEYS: &[&str] = &["created_by", "set_by", "by", "declared_by", "actor", "actor_via", "via", "session", "agent", "branch", "worktree"];

/// A value at `path` that an edit outside rb changed, as rb's words for the edit may give it: the value (None where
/// it is absent), or None where it would say who made a write, how rb knew, through what, in which session or with
/// which agent, or on which branch. A writer is named only as actor::shown_writer names one.
pub fn shown_edit(path: &str, value: Option<&Value>) -> Option<Option<Value>> {
    fn writes(v: &Value) -> bool {
        match v {
            Value::Dict(d) => d.iter().any(|(k, x)| WRITER_KEYS.contains(&k.as_str()) || writes(x)),
            Value::List(l) => l.iter().any(writes),
            _ => false,
        }
    }
    let keys: Vec<&str> = path.split('.').map(|k| k.split('[').next().unwrap_or("")).collect();
    if keys.last().map_or(false, |k| WRITER_IDS.contains(k)) {
        return match value {
            Some(Value::Str(id)) => actor::shown_writer(id).map(|name| Some(Value::Str(name))),
            _ => None,
        };
    }
    if keys.iter().any(|k| WRITER_KEYS.contains(k)) || value.map_or(false, writes) {
        return None;
    }
    Some(value.cloned())
}

/// A record as rb's text gives it whole (rb show <file>): without the keys that say who made a write, how rb knew,
/// through what, in which session, with which agent or on which branch, at any depth. A receipt's produced.existed.by
/// says what an output's time rests on, not who wrote, and stays. --json keeps the record as it is.
pub fn without_writers(v: &Value) -> Value {
    fn walk(v: &Value, parent: &str) -> Value {
        match v {
            Value::Dict(d) => Value::Dict(
                d.iter()
                    .filter(|(k, _)| (k.as_str() == "by" && parent == "existed") || !(WRITER_KEYS.contains(&k.as_str()) || k.as_str() == "ignored_rb_actor"))
                    .map(|(k, x)| (k.clone(), walk(x, k)))
                    .collect(),
            ),
            Value::List(l) => Value::List(l.iter().map(|x| walk(x, parent)).collect()),
            other => other.clone(),
        }
    }
    walk(v, "")
}

/// kind -> (directory, id prefix)
pub const KINDS: &[(&str, &str, &str)] = &[
    ("question", "questions", "q"),
    ("experiment", "experiments", "e"),
    ("metric", "metrics", "m"),
    ("criterion", "criteria", "c"),
    ("run", "runs", "run"),
    ("analysis", "analyses", "a"),
    ("resource", "resources", "res"),
    ("decision", "decisions", "d"),
];
pub const NESTED: &[&str] = &["criterion", "run", "analysis"];
pub const TOP: &[&str] = &["question", "metric", "resource", "decision"];
/// Parts of a record, a file each: an experiment's variants and notes, the project's notes. Read into their record and
/// written with it, never addressed alone. (kind, directory, id prefix)
pub const PARTS: &[(&str, &str, &str)] = &[("variant", VARIANTS_DIR, ""), ("note", NOTES_DIR, "n")];

pub fn is_part(kind: &str) -> bool {
    PARTS.iter().any(|p| p.0 == kind)
}

pub fn role_order(role: &str) -> i64 {
    match role {
        "baseline" => 0,
        "candidate" => 1,
        "control" => 2,
        "ablation" => 3,
        _ => 9,
    }
}
pub const LEGACY_KINDS: &[&str] = &["investigation", "hypothesis", "assumption", "claim", "evidence"];
pub const ROLES: &[&str] = &["baseline", "candidate", "control", "ablation"];
pub const GATES: &[&str] = &["unmet", "unresolved", "untested", "integrity", "undecided", "stale", "unknown", "late", "checks"];
pub const UNESTABLISHED: &[&str] = &["unmet", "untested", "integrity", "late"];

pub fn kind_dir(kind: &str) -> &'static str {
    KINDS.iter().find(|k| k.0 == kind).map(|k| k.1).unwrap_or("")
}
pub fn kind_prefix(kind: &str) -> &'static str {
    KINDS.iter().find(|k| k.0 == kind).map(|k| k.2).unwrap_or("")
}
pub fn is_kind(kind: &str) -> bool {
    KINDS.iter().any(|k| k.0 == kind)
}

pub fn state_of(action: &str) -> &'static str {
    match action {
        "accept" => "accepted",
        "reject" => "rejected",
        "investigate" => "investigating",
        "continue" => "open",
        "park" => "parked",
        "stop" => "stopped",
        "pivot" => "pivoted",
        "ship" => "shipped",
        _ => "open",
    }
}

/// A call as made, the way anyone reads it: accept is accepted, continue is continued.
/// What a decision line adds when its readings moved: a past call was made before any reading rb holds.
pub fn moved_since(past: bool) -> &'static str {
    if past { crate::view::EARLIER_PAST } else { " · made on readings that have moved since" }
}

pub fn made(action: &str) -> &str {
    match action {
        "accept" => "accepted",
        "reject" => "rejected",
        "investigate" => "investigating",
        "continue" => "continued",
        "park" => "parked",
        "stop" => "stopped",
        "pivot" => "pivoted",
        "ship" => "shipped",
        other => other,
    }
}

/// A call with its verb, the day and why: "parked 2026-10-07: later"; an investigation is still going,
/// "investigating since 2026-10-07: one more seed". A past call says when it was made, as given, and that it is
/// Retrospective: "parked May 7 (a past call, Retrospective): later". `by` is the writer's id, named only as
/// actor::shown_writer names it; `confirms`, whether the call confirms one a session recorded.
pub fn call_line(action: &str, by: &str, at: &str, why: &str, past: Option<&Past>, confirms: bool) -> String {
    let why = reason_said(why, by, past.is_some(), confirms);
    let day: String = at.chars().take(10).collect();
    let (day, said) = match past {
        None => (day, String::new()),
        Some(p) => {
            // the document's own words, verbatim with its file and line: what the call rests on, for the reader to judge
            let from = p.source.as_ref().map(|s| format!(" from {}, \"{}\"", crate::view::page_path(&format!("{}:{}", s.path, s.line), None), s.quote)).unwrap_or_default();
            match past_when(p).as_str() {
                "date unknown" => (String::new(), format!("(a past call{from}, date unknown, Retrospective)")),
                when => (when.to_string(), format!(" (a past call{from}, Retrospective)")),
            }
        }
    };
    if action == "investigate" {
        let who = actor::shown_writer(by).map(|name| format!(", by {name}")).unwrap_or_default();
        let since = if day.is_empty() { format!(" {}", said.trim_start()) } else { format!(" since {day}{said}") };
        format!("investigating{since}{who}{why}")
    } else {
        let when = if day.is_empty() { format!(" {}", said.trim_start()) } else { format!(" {day}{said}") };
        format!("{}{}{when}{why}", made(action), actor::by_writer(by))
    }
}

/// A call's reason after its line: ": <why>", or " · note: <why>" for a past or proposed call a session gave
/// (`as_note`), whose reason is its note on the call, never what the call rests on (the document's words, or nothing).
pub fn why_said(why: &str, by: &str, as_note: bool) -> String {
    if as_note && !crate::view::words_are_theirs(by) {
        if why.is_empty() { String::new() } else { format!(" · note: {why}") }
    } else {
        format!(": {why}")
    }
}

/// The same, for a call that may confirm one (`confirms`): its reason is the one the session gave with the call it
/// confirms, which stays a note on it. The user confirmed the call, never the words given for it.
pub fn reason_said(why: &str, by: &str, as_note: bool, confirms: bool) -> String {
    if confirms { session_note(why) } else { why_said(why, by, as_note) }
}

/// The reason a session gave with a call, as its note: " · note: <why>". A call that waits for a confirmation, or one
/// that confirms it, has no other reason, whatever its file says of who wrote it.
pub fn session_note(why: &str) -> String {
    if why.is_empty() { String::new() } else { format!(" · note: {why}") }
}

/// A past call's document, as a line says it: `<file>:<line>: "<its words>"`, verbatim.
pub fn source_said(src: &CallSource) -> String {
    format!("{}: \"{}\"", crate::view::page_path(&format!("{}:{}", src.path, src.line), None), src.quote)
}

pub fn question_state_of(action: &str) -> &'static str {
    match action {
        "accept" => "answered",
        "reject" => "dropped",
        "park" => "parked",
        "stop" => "stopped",
        "pivot" => "pivoted",
        "ship" => "shipped",
        _ => "open",
    }
}

// ---------------------------------------------------------------- canonical JSON and hashes

/// json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
pub fn canonical(v: &Value) -> String {
    pyjson::dumps_with(v, &Dumps::canonical()).unwrap_or_default()
}

/// json.dumps(node, sort_keys=True, ensure_ascii=False)
pub fn canon_json(v: &Value) -> String {
    pyjson::dumps_with(v, &Dumps { sort_keys: true, ensure_ascii: false, ..Default::default() }).unwrap_or_default()
}

pub fn sha(v: &Value) -> String {
    sha256_bytes(canonical(v).as_bytes())
}

/// A log line by what it says, which is what a look at the log remembers it by: a merge sorts another branch's lines
/// in among older ones, so where a line sits says nothing of whether it was seen.
pub fn line_digest(row: &Dict) -> String {
    sha(&Value::Dict(row.clone()))[..16].to_string()
}

/// A record's form as its leaves, each by its path, in canonical form. A list is its elements: records by their name
/// or id (else by what they hold), values by what they are, so one element changed, added or taken out is one leaf,
/// whatever order a merge left them in.
pub fn form_leaves(v: &Value, at: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    let join = |k: &str| if at.is_empty() { k.to_string() } else { format!("{at}.{k}") };
    match v {
        Value::Dict(d) if !d.is_empty() => {
            for (k, x) in d {
                out.extend(form_leaves(x, &join(k)));
            }
        }
        Value::List(items) if !items.is_empty() => {
            for x in items {
                match x {
                    Value::Dict(d) if !d.is_empty() => {
                        let name = ["name", "id", "question"].iter().find_map(|k| x.get_str(k)).map_or_else(|| format!("#{}", &sha(x)[..12]), str::to_string);
                        out.extend(form_leaves(x, &format!("{at}[{name}]")));
                    }
                    other => {
                        out.insert(format!("{at}[={}]", canonical(other)), "held".to_string());
                    }
                }
            }
        }
        other => {
            out.insert(at.to_string(), canonical(other));
        }
    }
    out
}

/// "unknown" for None, text as it is, anything else as JSON.
pub fn show(v: &Value) -> String {
    match v {
        Value::Null => "unknown".into(),
        Value::Str(s) => s.clone(),
        other => pyjson::dumps(other),
    }
}

/// Exactly the same value: same JSON type and text. 1 is not 1.0.
pub fn same_value(a: &Value, b: &Value) -> bool {
    canonical(a) == canonical(b) && a.type_name() == b.type_name()
}

/// `e1/lr` addresses setting lr of experiment e1.
pub fn parse_address(text: &str) -> (String, Option<String>) {
    match text.split_once('/') {
        Some((h, t)) => (h.to_string(), Some(t.to_string())),
        None => (text.to_string(), None),
    }
}

/// Whether this run's retraction covers a measurement.
pub fn superseded_for(r: &Run, metric: &str, arms: Option<(&str, &str)>) -> bool {
    match &r.retracted {
        None => false,
        Some(ret) => ret.scope.is_empty() || scope_covers(&ret.scope, metric, arms),
    }
}

pub fn scope_covers(scope: &[String], metric: &str, arms: Option<(&str, &str)>) -> bool {
    let (m_head, m_base) = match metric.rsplit_once('.') {
        Some((h, b)) => (h, b),
        None => ("", metric),
    };
    for s in scope {
        let (s_head, s_base) = match s.rsplit_once('.') {
            Some((h, b)) => (h, b),
            None => ("", s.as_str()),
        };
        if s == metric {
            return true;
        }
        if s_base != m_base {
            continue;
        }
        if s_head.is_empty() {
            return true;
        }
        if !m_head.is_empty() {
            continue;
        }
        match arms {
            None => return true,
            Some((a, b)) => {
                if s_head == a || s_head == b || s_head == "change" {
                    return true;
                }
            }
        }
    }
    false
}

// ---------------------------------------------------------------- finding the record

/// The nearest directory, from `start` upward, whose `.rb/` holds a record.
pub fn find_root(start: Option<&Path>) -> Option<PathBuf> {
    let here = match start {
        Some(s) => sources::resolve_path(s),
        None => sources::resolve_path(&std::env::current_dir().ok()?),
    };
    let mut d: Option<&Path> = Some(&here);
    while let Some(dir) = d {
        if dir.join(DIR).join(ROOT_FILE).is_file() {
            return Some(dir.to_path_buf());
        }
        d = dir.parent();
    }
    None
}

/// The directory a `.git` file names (`gitdir: <path>`, relative to the file's directory or absolute), or None.
fn gitdir_named(dot_git: &Path) -> Option<PathBuf> {
    let text = std::fs::read_to_string(dot_git).ok()?;
    let named = text.lines().next()?.strip_prefix("gitdir:")?.trim();
    if named.is_empty() {
        return None;
    }
    let p = Path::new(named);
    let p = if p.is_absolute() { p.to_path_buf() } else { dot_git.parent()?.join(p) };
    crate::sys::canonicalize(p).ok()
}

/// The shared git directory of a linked worktree whose `.git` file this is: its gitdir's commondir. None for a `.git`
/// file whose gitdir has no commondir (a submodule's), or one rb cannot read.
fn linked_common(dot_git: &Path) -> Option<PathBuf> {
    let gitdir = gitdir_named(dot_git)?;
    let text = std::fs::read_to_string(gitdir.join("commondir")).ok()?;
    let named = text.lines().next()?.trim();
    if named.is_empty() {
        return None;
    }
    let p = Path::new(named);
    crate::sys::canonicalize(if p.is_absolute() { p.to_path_buf() } else { gitdir.join(p) }).ok()
}

/// The shared git directory of the repository `dir` is in, from the first `.git` at or above it, read from files.
fn common_of(dir: &Path) -> Option<PathBuf> {
    for d in dir.ancestors() {
        let g = d.join(".git");
        if g.is_dir() {
            return crate::sys::canonicalize(g).ok();
        }
        if g.is_file() {
            return linked_common(&g).or_else(|| gitdir_named(&g));
        }
    }
    None
}

/// The directory in the worktree at `top` that the record's directory is in its own checkout (the record's place below
/// that checkout's top), when the worktree has it; else the worktree's top.
fn record_dir_in(top: &Path, root: &Path) -> PathBuf {
    let Some(home) = root.ancestors().find(|a| a.join(".git").symlink_metadata().is_ok()) else { return top.to_path_buf() };
    match root.strip_prefix(home) {
        Ok(rel) if !rel.as_os_str().is_empty() && top.join(rel).is_dir() => top.join(rel),
        _ => top.to_path_buf(),
    }
}

/// The checkout a command run in `start` works in, for a record at `root`: a linked worktree of root's repository
/// nested below root (the record's directory in it, as record_dir_in finds it), else root. The first `.git` strictly
/// below root decides; read from files, never git.
pub fn checkout_of(start: &Path, root: &Path) -> PathBuf {
    let root = sources::resolve_path(root);
    let start = sources::resolve_path(start);
    if start == root || !start.starts_with(&root) {
        return root;
    }
    let mut d = start.as_path();
    while d != root {
        let g = d.join(".git");
        if g.is_file() {
            return match (linked_common(&g), common_of(&root)) {
                (Some(a), Some(b)) if a == b => record_dir_in(d, &root),
                _ => root,
            };
        }
        if g.symlink_metadata().is_ok() {
            return root; // a repository of its own, or a .git rb cannot read
        }
        match d.parent() {
            Some(p) => d = p,
            None => break,
        }
    }
    root
}

/// Whether `dir` is in a linked worktree of root's repository nested below root: another checkout, whose files are its
/// own and not root's. Read from files, never git.
pub fn in_nested_checkout(dir: &Path, root: &Path) -> bool {
    checkout_of(dir, root) != sources::resolve_path(root)
}

/// Whether `dir` is the top of a linked worktree of the repository whose shared git directory is `common`: its own
/// `.git` file says so. One stat for a directory with no `.git` file.
pub fn is_worktree_top(dir: &Path, common: &Path) -> bool {
    let g = dir.join(".git");
    g.is_file() && linked_common(&g).as_deref() == Some(common)
}

/// The shared git directory of the repository `dir` is in, read from files.
pub fn common_dir_of(dir: &Path) -> Option<PathBuf> {
    common_of(&sources::resolve_path(dir))
}

/// E_NO_PROJECT for a command run in `start` (default: here). Where another worktree of this repository holds the
/// project's record, it names that place and how to bring the record here.
pub fn no_project(start: Option<&Path>) -> RBError {
    let start = match start {
        Some(s) => s.to_path_buf(),
        None => std::env::current_dir().unwrap_or_default(),
    };
    match crate::elsewhere::record_elsewhere(&start) {
        Some(there) => RBError::msg_fix("E_NO_PROJECT", there.message(), there.fix(false)),
        None => RBError::new("E_NO_PROJECT"),
    }
}

/// The top of the linked worktree `dir` is in, read from files; None in a main worktree, a submodule or outside git.
pub fn linked_top(dir: &Path) -> Option<PathBuf> {
    for d in dir.ancestors() {
        let g = d.join(".git");
        if g.is_file() {
            return linked_common(&g).map(|_| d.to_path_buf());
        }
        if g.symlink_metadata().is_ok() {
            return None;
        }
    }
    None
}

// ---------------------------------------------------------------- the words and the session of this process

static WORDS: Mutex<Option<String>> = Mutex::new(None);
static SESSION: Mutex<(Option<String>, Option<String>)> = Mutex::new((None, None));

/// The user's words, verbatim, that the writes of this command act on (--words).
pub fn set_words(words: Option<String>) {
    *WORDS.lock().unwrap() = words.map(|w| crate::rbyaml::py_strip(&w).to_string());
}

pub fn words() -> String {
    WORDS.lock().unwrap().clone().unwrap_or_default()
}

/// Every ledger opened while set records `via` as the surface, and `agent` as the actor (the MCP server).
pub fn set_session(via: Option<String>, agent: Option<String>) {
    *SESSION.lock().unwrap() = (via, agent);
}

pub fn session(via: Option<&str>, agent: Option<&str>) -> (String, Option<String>) {
    let (cv, ca) = SESSION.lock().unwrap().clone();
    (via.map(|s| s.to_string()).or(cv).unwrap_or_else(|| "cli".into()), agent.map(|s| s.to_string()).or(ca))
}

/// What an MCP client said on its connection: its version (clientInfo), and the model and provider its agent gave the
/// context tool, which hold for the connection when the runtime names no session.
#[derive(Debug, Clone, Default)]
pub struct Client {
    pub version: Option<String>,
    pub said: actor::Said,
}

static CLIENT: Mutex<Option<Client>> = Mutex::new(None);

/// Set by rb mcp around each tool call: every ledger opened while set reads its agent from this connection.
pub fn set_client(client: Option<Client>) {
    *CLIENT.lock().unwrap() = client;
}

pub fn client() -> Option<Client> {
    CLIENT.lock().unwrap().clone()
}

// ---------------------------------------------------------------- record files

#[derive(Debug, Clone)]
pub enum Unreadable {
    Bad(String),
    Newer(String),
}

impl Unreadable {
    pub fn text(&self) -> &str {
        match self {
            Unreadable::Bad(s) | Unreadable::Newer(s) => s,
        }
    }
}

fn body(data: &Value, kind: &str, rel: &str) -> Result<Dict, Unreadable> {
    let d = match data {
        Value::Dict(d) => d,
        _ => return Err(Unreadable::Bad(format!("{rel} is not a record: a record file is a mapping that starts with {FORMAT_KEY}: {RB_FORMAT}"))),
    };
    let fmt = d.get(FORMAT_KEY);
    if let Some(Value::Int(f)) = fmt {
        if *f > RB_FORMAT {
            return Err(Unreadable::Newer(format!("{rel} is format {f}; this rb reads format {RB_FORMAT}: it was written by a newer rb")));
        }
    }
    if !matches!(fmt, Some(Value::Int(f)) if *f == RB_FORMAT) {
        return Err(Unreadable::Bad(format!("{rel} does not start with {FORMAT_KEY}: {RB_FORMAT}")));
    }
    if d.get("kind").and_then(|k| k.as_str()) != Some(kind) {
        let k = d.get("kind").map(pyjson::py_repr).unwrap_or_else(|| "None".into());
        return Err(Unreadable::Bad(format!("{rel} says kind: {k}, and a file there holds a {kind}")));
    }
    Ok(d.iter().filter(|(k, _)| k.as_str() != FORMAT_KEY && k.as_str() != "kind").map(|(k, v)| (k.clone(), v.clone())).collect())
}

fn repr_opt(v: Option<&Value>) -> String {
    v.map(pyjson::py_repr).unwrap_or_else(|| "None".into())
}

/// The records a file's data holds, as (id, stored form): one, or every criterion of an experiment's criteria file
/// (the layout from before each criterion had a file of its own, which has no id in its path).
pub fn entries(kind: &str, data: &Value, rel: &str, path_id: Option<&str>, experiment: Option<&str>) -> Result<Vec<(String, Dict)>, Unreadable> {
    if kind != "criterion" || path_id.is_some() {
        let b = body(data, kind, rel)?;
        if kind == "variant" {
            let name = path_id.and_then(|p| p.split_once('/')).map(|(_, n)| n);
            if b.get("name").and_then(|v| v.as_str()) != name {
                return Err(Unreadable::Bad(format!("{rel} holds variant {}; a file there holds variant {}", repr_opt(b.get("name")), name.unwrap_or("None"))));
            }
            return Ok(vec![(path_id.unwrap_or("").to_string(), b)]);
        }
        if let Some(pid) = path_id {
            if b.get("id").and_then(|v| v.as_str()) != Some(pid) {
                return Err(Unreadable::Bad(format!("{rel} holds {kind} {}; a file there holds {kind} {pid}", repr_opt(b.get("id")))));
            }
        }
        if (kind == "run" || kind == "analysis" || kind == "criterion") && b.get("experiment").and_then(|v| v.as_str()) != experiment {
            return Err(Unreadable::Bad(format!(
                "{rel} says experiment: {}; it is in {}'s {}",
                repr_opt(b.get("experiment")),
                experiment.unwrap_or("None"),
                kind_dir(kind)
            )));
        }
        let id = match path_id {
            Some(p) => p.to_string(),
            None => b.get("id").map(pyjson::py_str).unwrap_or_else(|| "None".into()),
        };
        return Ok(vec![(id, b)]);
    }
    let b = body(data, "criteria", rel)?;
    if b.get("experiment").and_then(|v| v.as_str()) != experiment {
        return Err(Unreadable::Bad(format!("{rel} says experiment: {}; it is {}'s criteria file", repr_opt(b.get("experiment")), experiment.unwrap_or("None"))));
    }
    let mut extra: Vec<&String> = b.keys().filter(|k| k.as_str() != "experiment" && k.as_str() != "criteria").collect();
    extra.sort();
    if !extra.is_empty() {
        return Err(Unreadable::Newer(format!(
            "{rel} has {}, which a criteria file of format {RB_FORMAT} does not: it was written by a newer rb",
            extra.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
        )));
    }
    let items = match b.get("criteria") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::List(l)) => l.clone(),
        Some(_) => return Err(Unreadable::Bad(format!("{rel}: criteria is a list of criteria, each with an id"))),
    };
    if items.iter().any(|x| !matches!(x, Value::Dict(d) if matches!(d.get("id"), Some(Value::Str(_))))) {
        return Err(Unreadable::Bad(format!("{rel}: criteria is a list of criteria, each with an id")));
    }
    let ids: Vec<String> = items.iter().map(|x| x.get_str("id").unwrap().to_string()).collect();
    let mut twice: Vec<String> = ids.iter().filter(|i| ids.iter().filter(|j| j == i).count() > 1).cloned().collect();
    twice.sort();
    twice.dedup();
    if !twice.is_empty() {
        return Err(Unreadable::Bad(format!("{rel} lists {} twice", twice.join(", "))));
    }
    Ok(items.into_iter().map(|x| (x.get_str("id").unwrap().to_string(), x.as_dict().unwrap().clone())).collect())
}

/// The records one log line wrote, each as the entry its last write is read from: the record the line is about (when it
/// carries a hash, or someone adopted its deletion), then every part the same command wrote.
pub fn writes_in(row: &Dict) -> Vec<(Key, Dict)> {
    let mut out = Vec::new();
    let kind = row.get("kind").filter(|v| v.truthy());
    let id = row.get("id").filter(|v| v.truthy());
    let has = row.get("sha256").map_or(false, |v| v.truthy()) || row.get("op").and_then(|v| v.as_str()) == Some("adopt_delete");
    if let (Some(k), Some(i), true) = (kind, id, has) {
        out.push(((pyjson::py_str(k), pyjson::py_str(i)), row.clone()));
    }
    if let Some(Value::List(parts)) = row.get("parts") {
        for p in parts {
            let Value::Dict(pd) = p else { continue };
            let (Some(k), Some(i), Some(sha)) = (pd.get("kind").filter(|v| v.truthy()), pd.get("id").filter(|v| v.truthy()), pd.get("sha256").filter(|v| v.truthy())) else { continue };
            let mut w = row.clone();
            w.insert("kind".into(), k.clone());
            w.insert("id".into(), i.clone());
            w.insert("sha256".into(), sha.clone());
            w.insert("path".into(), pd.get("path").cloned().unwrap_or(Value::Null));
            out.push(((pyjson::py_str(k), pyjson::py_str(i)), w));
        }
    }
    out
}

/// Whether a log line is a session's line: what rb knew of an agent, written when a session's agent facts were first
/// known or changed. It records no write, so no reading of the log as writes counts it.
/// The ids a writer goes by, from what a log line keeps of its agent (`agent`) and its id: its name, its runtime's name
/// as people write it (Claude Code), and its model.
pub fn writer_ids_of(agent: &Value, actor: &str) -> Vec<String> {
    let mut ids: Vec<String> = Vec::new();
    let mut add = |s: &str| {
        let s = s.trim();
        if !s.is_empty() && !ids.iter().any(|i| i.eq_ignore_ascii_case(s)) {
            ids.push(s.to_string());
        }
    };
    if let Some(name) = actor.strip_prefix("agent:") {
        add(name);
    }
    for key in ["name", "model"] {
        if let Some(v) = agent.get_str(key) {
            add(v);
        }
    }
    if let Some((_, label)) = agent.get_str("name").and_then(|n| actor::RUNTIME_NAMES.iter().find(|(k, _)| *k == n)) {
        add(label);
    }
    ids
}

pub fn is_session_line(row: &Dict) -> bool {
    row.get_str("op") == Some("session")
}

type DocKey = (i64, u64, u64);

/// What one file holds as one kind: its records, (id, stored form) each, or why it cannot be read.
type Entries = Result<Rc<Vec<(String, Dict)>>, Unreadable>;

thread_local! {
    static DOCS: RefCell<HashMap<PathBuf, (DocKey, Rc<Document>)>> = RefCell::new(HashMap::new());
    /// entries() of a file as it stands (the doc's key), for a project root and a kind
    static ENTRIES: RefCell<HashMap<(PathBuf, PathBuf, String), (DocKey, Entries)>> = RefCell::new(HashMap::new());
    /// a record of a one-file kind, validated from its file as it stood (the file's key): for (root, kind, id)
    static VALID: RefCell<HashMap<(PathBuf, String, String), (PathBuf, DocKey, Rc<Obj>)>> = RefCell::new(HashMap::new());
    /// Write preflight only: unfamiliar model fields, keyed like the parsed file. Ordinary reads and recovery keep
    /// their existing validation paths; a normal write cannot discover a future shape only after it has committed.
    static NEWER_SHAPES: RefCell<HashMap<(PathBuf, PathBuf, String), (DocKey, Option<RBError>)>> = RefCell::new(HashMap::new());
}

/// A file's key as doc_keyed takes it: (time, size, inode).
fn doc_key(path: &Path) -> Option<DocKey> {
    crate::dry::stamp(path)
}

/// Path components, for sorting the way pathlib sorts paths.
fn parts_key(p: &Path) -> Vec<String> {
    p.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect()
}

fn list_dir(d: &Path) -> Vec<PathBuf> {
    crate::dry::list_dir(d)
}

fn name_of(p: &Path) -> String {
    p.file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default()
}

pub type Key = (String, String);
/// (kind, id, file, commit): a record as its file held it at a commit
type At = (String, String, String, String);

/// What git says of a checkout: whether there is one (a bundle on its own has none), the commits a shallow clone holds
/// without their parents (none in a whole clone), and MERGE_HEAD while a merge is under way.
struct Checkout {
    repo: bool,
    shallow: HashSet<String>,
    merging: Option<String>,
    /// a merge, cherry-pick, revert or rebase under way: the working tree holds what no commit does yet
    busy: bool,
}

/// Whether a record is a bundle's, where what its export kept may speak (in_bundle), a checkout's, or one a shallow
/// clone shows too little history to tell.
#[derive(Clone, Copy, PartialEq)]
enum Bundled {
    Yes,
    No,
    CannotTell,
}

/// What git says merged a record's file: the two sides rb wrote, the side merged in (by its commit: a branch's name often
/// names an agent, which rb's words never do), and whether the file is both sides kept.
#[derive(Debug, Clone)]
pub struct MergeInfo {
    pub sides: Vec<Dict>,
    pub merged_in: String,
    pub union: bool,
    /// when it is both sides kept, every merge that makes it rb's own: its own first, then those its sides rest on
    pub rests_on: Vec<Merged>,
    /// read from what an export kept (KEPT_MERGES), where git cannot show it
    pub kept: bool,
}

/// One merge a record's file rests on: the form it made, and the base and the two sides it was made from.
#[derive(Debug, Clone)]
pub struct Merged {
    pub form: Dict,
    pub base: Option<Dict>,
    pub ours: Dict,
    pub theirs: Dict,
}

/// What an export keeps of a record that reads as rb's own only through merges: how many writes of it rb had logged
/// then, and the merges it rests on, the one that made its file first.
pub struct Kept {
    pub key: Key,
    pub writes: usize,
    pub merges: Vec<Merged>,
}

/// Where rb export keeps them in a bundle's .rb/, with rb's copy of every form they name, by content hash: beside the
/// log, since the bundle's .gitignore keeps cache/ out of a repository that hosts it.
pub const KEPT_MERGES: &str = "merges.json";
pub type Index = IndexMap<Key, PathBuf>;

/// A record of any kind, as `get` and `_load` return it.
#[derive(Debug, Clone)]
pub enum Obj {
    Project(Project),
    Question(Question),
    Experiment(Experiment),
    Metric(Metric),
    Criterion(Criterion),
    Run(Run),
    Analysis(Analysis),
    Resource(Resource),
    Decision(Decision),
}

impl Obj {
    pub fn kind(&self) -> &'static str {
        match self {
            Obj::Project(_) => "project",
            Obj::Question(_) => "question",
            Obj::Experiment(_) => "experiment",
            Obj::Metric(_) => "metric",
            Obj::Criterion(_) => "criterion",
            Obj::Run(_) => "run",
            Obj::Analysis(_) => "analysis",
            Obj::Resource(_) => "resource",
            Obj::Decision(_) => "decision",
        }
    }
    pub fn id(&self) -> &str {
        match self {
            Obj::Project(x) => &x.id,
            Obj::Question(x) => &x.id,
            Obj::Experiment(x) => &x.id,
            Obj::Metric(x) => &x.id,
            Obj::Criterion(x) => &x.id,
            Obj::Run(x) => &x.id,
            Obj::Analysis(x) => &x.id,
            Obj::Resource(x) => &x.id,
            Obj::Decision(x) => &x.id,
        }
    }
    pub fn retracted(&self) -> Option<&Retraction> {
        match self {
            Obj::Project(_) => None,
            Obj::Question(x) => x.retracted.as_ref(),
            Obj::Experiment(x) => x.retracted.as_ref(),
            Obj::Metric(x) => x.retracted.as_ref(),
            Obj::Criterion(x) => x.retracted.as_ref(),
            Obj::Run(x) => x.retracted.as_ref(),
            Obj::Analysis(x) => x.retracted.as_ref(),
            Obj::Resource(x) => x.retracted.as_ref(),
            Obj::Decision(x) => x.retracted.as_ref(),
        }
    }
    pub fn retracted_mut(&mut self) -> Option<&mut Option<Retraction>> {
        match self {
            Obj::Project(_) => None,
            Obj::Question(x) => Some(&mut x.retracted),
            Obj::Experiment(x) => Some(&mut x.retracted),
            Obj::Metric(x) => Some(&mut x.retracted),
            Obj::Criterion(x) => Some(&mut x.retracted),
            Obj::Run(x) => Some(&mut x.retracted),
            Obj::Analysis(x) => Some(&mut x.retracted),
            Obj::Resource(x) => Some(&mut x.retracted),
            Obj::Decision(x) => Some(&mut x.retracted),
        }
    }
    /// The stored form, and the experiment a nested record belongs to.
    pub fn stored(&self) -> Dict {
        match self {
            Obj::Project(x) => x.stored(),
            Obj::Question(x) => x.stored(),
            Obj::Experiment(x) => x.stored(),
            Obj::Metric(x) => x.stored(),
            Obj::Criterion(x) => x.stored(),
            Obj::Run(x) => x.stored(),
            Obj::Analysis(x) => x.stored(),
            Obj::Resource(x) => x.stored(),
            Obj::Decision(x) => x.stored(),
        }
    }
    pub fn experiment(&self) -> Option<&str> {
        match self {
            Obj::Criterion(x) => Some(&x.experiment),
            Obj::Run(x) => Some(&x.experiment),
            Obj::Analysis(x) => Some(&x.experiment),
            _ => None,
        }
    }
    pub fn to_value(&self) -> Value {
        match self {
            Obj::Project(x) => x.to_value(),
            Obj::Question(x) => x.to_value(),
            Obj::Experiment(x) => x.to_value(),
            Obj::Metric(x) => x.to_value(),
            Obj::Criterion(x) => x.to_value(),
            Obj::Run(x) => x.to_value(),
            Obj::Analysis(x) => x.to_value(),
            Obj::Resource(x) => x.to_value(),
            Obj::Decision(x) => x.to_value(),
        }
    }
}

pub trait FromObj: Record + 'static {
    fn from_obj(o: Obj) -> Option<Self>;
    fn into_obj(self) -> Obj;
}

macro_rules! from_obj {
    ($t:ident) => {
        impl FromObj for $t {
            fn from_obj(o: Obj) -> Option<Self> {
                match o {
                    Obj::$t(x) => Some(x),
                    _ => None,
                }
            }
            fn into_obj(self) -> Obj {
                Obj::$t(self)
            }
        }
    };
}
from_obj!(Question);
from_obj!(Experiment);
from_obj!(Metric);
from_obj!(Criterion);
from_obj!(Run);
from_obj!(Analysis);
from_obj!(Resource);
from_obj!(Decision);

/// pydantic's errors as rb's E_STATE_NEWER or E_STATE_CORRUPT, for the data they were raised on. A declared relation
/// of a kind, or with an orientation, this rb does not know is a newer rb's, whatever else it holds.
fn looks_newer(errs: &[VErr], data: &Value) -> bool {
    use crate::model::LocStep;
    if errs.is_empty() {
        return false;
    }
    // the relations entry an error is in, when a newer rb wrote that entry
    let newer_entry = |loc: &[LocStep]| -> Option<usize> {
        let at = loc.iter().position(|s| matches!(s, LocStep::Key(k) if k == "relations"))?;
        let (Some(LocStep::Index(i)), Some(LocStep::Key(_))) = (loc.get(at + 1), loc.get(at + 2)) else { return None };
        let mut owner = data;
        for step in &loc[..at] {
            owner = match step { LocStep::Key(k) => owner.get(k)?, LocStep::Index(n) => owner.as_list()?.get(*n)? };
        }
        let model = if owner.get("comparator").is_some() { "CriterionRelation" } else { "Relation" };
        newer_relation(model, owner.get("relations")?.as_list()?.get(*i)?).map(|_| *i)
    };
    errs.iter().all(|e| {
        e.typ == "extra_forbidden"
            || ((e.typ == "literal_error" || e.typ == "enum") && matches!(e.loc.last(), Some(LocStep::Key(k)) if k == "via"))
            || newer_entry(&e.loc).is_some()
    })
}

/// A declared relation whose kind, or orientation on a criterion, this rb does not know: what it is ("kind
/// replicates"), or None. A newer rb wrote it; the rest of its record still reads.
pub fn newer_relation(model: &str, relation: &Value) -> Option<String> {
    let mut errs = Vec::new();
    match model {
        "CriterionRelation" => { CriterionRelation::validate(relation, &mut Vec::new(), &mut errs); }
        _ => { Relation::validate(relation, &mut Vec::new(), &mut errs); }
    }
    ["kind", "orientation"].into_iter().find_map(|key| {
        let unknown = errs.iter().any(|e| (e.typ == "literal_error" || e.typ == "enum") && matches!(e.loc.as_slice(), [crate::model::LocStep::Key(k)] if k == key));
        match relation.get(key) {
            Some(Value::Str(s)) if unknown && !s.is_empty() && s.chars().all(|c| c.is_ascii_lowercase() || c == '_') => Some(format!("{key} {s}")),
            _ => None,
        }
    })
}

/// A record's relations without the ones only a newer rb reads (named by newer_within), so the rest of it reads.
fn without_newer_relations(kind: &str, mut data: Dict) -> Dict {
    let model = match kind {
        "criterion" => "CriterionRelation",
        "experiment" => "Relation",
        _ => return data,
    };
    if let Some(Value::List(items)) = data.get_mut("relations") {
        items.retain(|r| newer_relation(model, r).is_none());
    }
    data
}

/// A cheap shape screen before full validation. Free-form mappings (for example a decision's saved basis) stay
/// free-form; only declared nested models are traversed. Invalid known fields remain the normal reader's concern.
fn unfamiliar_shape(meta: &crate::model::ModelMeta, data: &Dict) -> bool {
    if matches!(meta.name, "CriterionRelation" | "Relation") && newer_relation(meta.name, &Value::Dict(data.clone())).is_some() {
        return true;
    }
    data.iter().any(|(key, value)| {
        let Some(field) = meta.field(key) else { return true; };
        if key == "via" && value.as_str().map_or(false, |s| !["cli", "sdk", "mcp", "api"].contains(&s)) {
            return true;
        }
        match (field.sub, value) {
            (Some(sub), Value::Dict(d)) => unfamiliar_shape(sub, d),
            (Some(sub), Value::List(items)) => items.iter().any(|v| v.as_dict().map_or(false, |d| unfamiliar_shape(sub, d))),
            _ => false,
        }
    })
}

/// What a readable record keeps in a free-form field that only a newer rb reads: a question call's snapshot of a
/// later version or with keys this rb does not know, or a declared relation's scope with such keys.
fn newer_within(kind: &str, id: &str, form: &Dict) -> Option<String> {
    match kind {
        "decision" => {
            let snapshot = form.get("inquiry_basis").and_then(Value::as_dict)?;
            let what = inquiries::snapshot_newer(snapshot)?;
            Some(format!("decision {id} keeps a question snapshot with {what}, which this rb does not know"))
        }
        "criterion" => {
            let relations = form.get("relations").and_then(Value::as_list)?;
            if let Some(what) = relations.iter().find_map(|r| newer_relation("CriterionRelation", r)) {
                return Some(format!("criterion {id} declares a relation with {what}, which this rb does not know"));
            }
            let keys: Vec<String> = relations.iter().flat_map(relations::unknown_scope_keys).collect();
            let first = keys.first()?;
            Some(format!("criterion {id} declares a relation scope with {first}, which this rb does not know"))
        }
        "experiment" => {
            let what = form.get("relations").and_then(Value::as_list)?.iter().find_map(|r| newer_relation("Relation", r))?;
            Some(format!("experiment {id} declares a relation with {what}, which this rb does not know"))
        }
        _ => None,
    }
}

/// _build: a record from its fields, validated as pydantic validates them; E_OBJECT_INVALID otherwise.
pub fn build<T: Record>(fields: Dict) -> Res<T> {
    let mut errs = Vec::new();
    match T::validate_value(&Value::Dict(fields), &mut errs) {
        Some(m) if errs.is_empty() => Ok(m),
        _ => Err(invalid(&errs)),
    }
}

/// _build for a nested model (no file of its own).
pub fn build_with<T>(fields: Dict, validate: fn(&Value, &mut crate::model::Loc, &mut Vec<VErr>) -> Option<T>) -> Res<T> {
    let mut errs = Vec::new();
    let mut loc = Vec::new();
    match validate(&Value::Dict(fields), &mut loc, &mut errs) {
        Some(m) if errs.is_empty() => Ok(m),
        _ => Err(invalid(&errs)),
    }
}

pub fn invalid(errs: &[VErr]) -> RBError {
    let probs = problems(errs, "value");
    if probs.len() == 1 {
        RBError::msg("E_OBJECT_INVALID", probs[0].clone())
    } else {
        RBError::msg("E_OBJECT_INVALID", "The values given are not valid.").with_problems(probs)
    }
}

// ---------------------------------------------------------------- the ledger

pub struct Ledger {
    pub root: PathBuf,
    /// the checkout the command ran in: a linked worktree nested below root with no record of its own (the record's
    /// directory in it), else root
    pub here: PathBuf,
    pub dir: PathBuf,
    pub via: String,
    agent: Option<String>,
    pub words: String,
    depth: Cell<usize>,
    hashes: RefCell<Option<Rc<IndexMap<Key, Dict>>>>,
    /// the log's (size, time) when `hashes` was read from it; None once this ledger has appended to it since
    hashes_stamp: Cell<Option<(u64, i64)>>,
    newer_log: RefCell<Vec<Dict>>,
    tamper: RefCell<Option<Rc<Vec<Dict>>>>,
    tamper_keys: RefCell<Option<(Rc<Vec<Dict>>, Rc<HashSet<Key>>)>>,
    index: RefCell<Option<Rc<Index>>>,
    objs: RefCell<HashMap<&'static str, Rc<dyn Any>>>,
    objs_for: RefCell<Option<Rc<Index>>>,
    /// one record read and validated, kept for the state of the record it was read in (objs_for)
    loaded: RefCell<HashMap<Key, Rc<Obj>>>,
    /// edited_outside per record, and whether only a shallow clone's missing history makes it so, for the state of the
    /// record and the log it was worked out against
    edited: RefCell<HashMap<Key, Option<(String, bool)>>>,
    parts_by: RefCell<Rc<HashMap<(String, Option<String>), Vec<(String, PathBuf)>>>>,
    parts_for: RefCell<Option<Rc<Index>>>,
    merges: RefCell<HashMap<(String, String, String), Option<Rc<MergeInfo>>>>,
    /// KEPT_MERGES as read at its (size, time): what it holds, where a bundle's manifest names it, and whether it speaks
    /// (not where a shallow clone cannot tell the bundle from a checkout)
    kept: RefCell<Option<((u64, i64), Option<(Rc<Value>, bool)>)>>,
    /// whether this record is a bundle's, where what its export kept may speak: worked out once
    bundle: Cell<Option<Bundled>>,
    /// the hash of every write rb logged, per record, for the log as read
    logged_by: RefCell<Option<(Rc<Vec<Dict>>, Rc<HashMap<Key, Vec<String>>>)>>,
    versions: RefCell<HashMap<(String, String), Option<Value>>>,
    checkout: RefCell<Option<Rc<Checkout>>>,
    /// per (kind, id, file, commit) and content: the commit that last changed the record's content there, and its parents
    made: RefCell<HashMap<(At, String), Option<(String, Vec<String>)>>>,
    /// rb_side and cut_at per (kind, id, file, commit), for the log they were worked out against
    sides: RefCell<HashMap<At, Option<(Dict, Vec<Merged>)>>>,
    cuts: RefCell<HashMap<At, bool>>,
    /// where two commits' histories meet (git merge-base), per pair
    bases: RefCell<HashMap<(String, String), Option<String>>>,
    pub(crate) unreadable: RefCell<IndexMap<String, String>>,
    newer_files: RefCell<Vec<Dict>>,
    pub(crate) newer_index: RefCell<HashMap<Key, PathBuf>>,
    strays: RefCell<Vec<Dict>>,
    actor_cache: RefCell<Option<Actor>>,
    log_cache: RefCell<Option<((u64, i64), Rc<Vec<Dict>>)>>,
    aliases_cache: RefCell<Option<(Rc<Index>, Rc<IndexMap<String, String>>)>>,
    finding_cache: RefCell<HashMap<String, findings::CachedFindings>>,
    /// the session, writer and channel whose agent facts the log last said, as this ledger last read or wrote them
    session_noted: RefCell<Option<([String; 3], Dict)>>,
    /// the linked worktree `here` is in, worked out once
    worktree: std::cell::OnceCell<Option<PathBuf>>,
}

/// The log's (size, modification time), as log_entries keys what it read.
fn log_stamp(dir: &Path) -> Option<(u64, i64)> {
    crate::dry::size_time(&dir.join("log.jsonl"))
}

/// What a ledger worked out about one state of the record, handed over when it is dropped to the ledger rb_delta opens
/// right after the command (Ledger::open_continuing). The CLI forgets it before every command line.
struct Worked {
    key: (PathBuf, String, Option<String>),
    stamp: (u64, i64),
    hashes: Option<Rc<IndexMap<Key, Dict>>>,
    newer_log: Vec<Dict>,
    tamper: Option<Rc<Vec<Dict>>>,
    tamper_keys: Option<(Rc<Vec<Dict>>, Rc<HashSet<Key>>)>,
    index: Option<Rc<Index>>,
    objs: HashMap<&'static str, Rc<dyn Any>>,
    objs_for: Option<Rc<Index>>,
    loaded: HashMap<Key, Rc<Obj>>,
    edited: HashMap<Key, Option<(String, bool)>>,
    parts_by: Rc<HashMap<(String, Option<String>), Vec<(String, PathBuf)>>>,
    parts_for: Option<Rc<Index>>,
    merges: HashMap<(String, String, String), Option<Rc<MergeInfo>>>,
    versions: HashMap<(String, String), Option<Value>>,
    unreadable: IndexMap<String, String>,
    newer_files: Vec<Dict>,
    newer_index: HashMap<Key, PathBuf>,
    strays: Vec<Dict>,
    actor_cache: Option<Actor>,
    log_cache: Option<((u64, i64), Rc<Vec<Dict>>)>,
    aliases_cache: Option<(Rc<Index>, Rc<IndexMap<String, String>>)>,
    finding_cache: HashMap<String, findings::CachedFindings>,
}

thread_local! {
    static WORKED: RefCell<Option<Worked>> = const { RefCell::new(None) };
}

/// Forget every file read and what was worked out from it, by the files' stamps: after a preview, which read files of
/// its own.
pub fn forget_files() {
    DOCS.with(|d| d.borrow_mut().clear());
    ENTRIES.with(|e| e.borrow_mut().clear());
    VALID.with(|v| v.borrow_mut().clear());
    NEWER_SHAPES.with(|s| s.borrow_mut().clear());
}

/// Forget what the last ledger worked out: a new command line reads the record afresh.
/// What it read of the sources goes too, so a source rewritten with as many bytes inside the clock's resolution reads as
/// it is now (rb ui's bridge answers every request in one process).
pub fn forget_worked() {
    WORKED.with(|w| *w.borrow_mut() = None);
    sources::forget_git();
    sources::forget_reads();
}

impl Drop for Ledger {
    /// A ledger that read the record and holds the whole log as it stands hands what it worked out to the next ledger
    /// the same command opens for rb_delta.
    fn drop(&mut self) {
        if self.depth.get() != 0 || self.index.borrow().is_none() || crate::dry::active() {
            return;
        }
        // what it holds of the log is the log as some stamp names it; the rows themselves only when read at that stamp
        let Some(stamp) = self.hashes_stamp.get() else { return };
        if self.log_cache.borrow().as_ref().map_or(false, |(read, _)| *read != stamp) {
            *self.log_cache.borrow_mut() = None;
        }
        let worked = Worked {
            key: (self.root.clone(), self.via.clone(), self.agent.clone()),
            stamp,
            hashes: self.hashes.take(),
            newer_log: self.newer_log.take(),
            tamper: self.tamper.take(),
            tamper_keys: self.tamper_keys.take(),
            index: self.index.take(),
            objs: self.objs.take(),
            objs_for: self.objs_for.take(),
            loaded: self.loaded.take(),
            edited: self.edited.take(),
            parts_by: self.parts_by.take(),
            parts_for: self.parts_for.take(),
            merges: self.merges.take(),
            versions: self.versions.take(),
            unreadable: self.unreadable.take(),
            newer_files: self.newer_files.take(),
            newer_index: self.newer_index.take(),
            strays: self.strays.take(),
            actor_cache: self.actor_cache.take(),
            log_cache: self.log_cache.take(),
            aliases_cache: self.aliases_cache.take(),
            finding_cache: self.finding_cache.take(),
        };
        WORKED.with(|w| *w.borrow_mut() = Some(worked));
    }
}

impl Ledger {
    /// Ledger.open for rb_delta, right after its command: it picks up what the command's ledger worked out about the
    /// same record as the same actor, when the log is just as that ledger last read it (nothing written since).
    /// Otherwise, a ledger reading afresh.
    pub fn open_continuing(start: Option<&Path>) -> Res<Ledger> {
        let (via, agent) = session(None, None);
        let root = find_root(start).ok_or_else(|| no_project(start))?;
        let led = Ledger::new(&root, &via, agent).at(start);
        if let Some(w) = WORKED.with(|w| w.borrow_mut().take()) {
            if w.key == (led.root.clone(), led.via.clone(), led.agent.clone()) && log_stamp(&led.dir) == Some(w.stamp) {
                *led.hashes.borrow_mut() = w.hashes;
                led.hashes_stamp.set(Some(w.stamp));
                *led.newer_log.borrow_mut() = w.newer_log;
                *led.tamper.borrow_mut() = w.tamper;
                *led.tamper_keys.borrow_mut() = w.tamper_keys;
                *led.index.borrow_mut() = w.index;
                *led.objs.borrow_mut() = w.objs;
                *led.objs_for.borrow_mut() = w.objs_for;
                *led.loaded.borrow_mut() = w.loaded;
                *led.edited.borrow_mut() = w.edited;
                *led.parts_by.borrow_mut() = w.parts_by;
                *led.parts_for.borrow_mut() = w.parts_for;
                *led.merges.borrow_mut() = w.merges;
                *led.versions.borrow_mut() = w.versions;
                *led.unreadable.borrow_mut() = w.unreadable;
                *led.newer_files.borrow_mut() = w.newer_files;
                *led.newer_index.borrow_mut() = w.newer_index;
                *led.strays.borrow_mut() = w.strays;
                *led.actor_cache.borrow_mut() = w.actor_cache;
                *led.log_cache.borrow_mut() = w.log_cache;
                *led.aliases_cache.borrow_mut() = w.aliases_cache;
                *led.finding_cache.borrow_mut() = w.finding_cache;
            }
        }
        led.actor()?;
        led.project()?;
        Ok(led)
    }

    /// Whether this ledger worked out a reading it keeps (`design`, `authorities`): what a test asks of a command that
    /// should not need it.
    #[cfg(test)]
    pub(crate) fn worked_out(&self, what: &str) -> bool {
        self.objs.borrow().contains_key(what)
    }

    pub fn new(root: &Path, via: &str, agent: Option<String>) -> Ledger {
        let root = sources::resolve_path(root);
        Ledger {
            dir: root.join(DIR),
            here: root.clone(),
            root,
            via: via.to_string(),
            agent,
            words: words(),
            depth: Cell::new(0),
            hashes: RefCell::new(None),
            hashes_stamp: Cell::new(None),
            newer_log: RefCell::new(Vec::new()),
            tamper: RefCell::new(None),
            tamper_keys: RefCell::new(None),
            index: RefCell::new(None),
            objs: RefCell::new(HashMap::new()),
            objs_for: RefCell::new(None),
            loaded: RefCell::new(HashMap::new()),
            edited: RefCell::new(HashMap::new()),
            parts_by: RefCell::new(Rc::new(HashMap::new())),
            parts_for: RefCell::new(None),
            merges: RefCell::new(HashMap::new()),
            kept: RefCell::new(None),
            bundle: Cell::new(None),
            logged_by: RefCell::new(None),
            versions: RefCell::new(HashMap::new()),
            checkout: RefCell::new(None),
            made: RefCell::new(HashMap::new()),
            sides: RefCell::new(HashMap::new()),
            cuts: RefCell::new(HashMap::new()),
            bases: RefCell::new(HashMap::new()),
            unreadable: RefCell::new(IndexMap::new()),
            newer_files: RefCell::new(Vec::new()),
            newer_index: RefCell::new(HashMap::new()),
            strays: RefCell::new(Vec::new()),
            actor_cache: RefCell::new(None),
            log_cache: RefCell::new(None),
            aliases_cache: RefCell::new(None),
            finding_cache: RefCell::new(HashMap::new()),
            session_noted: RefCell::new(None),
            worktree: std::cell::OnceCell::new(),
        }
    }

    /// This ledger, its checkout the one a command run in `start` works in (checkout_of).
    pub fn at(mut self, start: Option<&Path>) -> Ledger {
        let start = match start {
            Some(s) => s.to_path_buf(),
            None => std::env::current_dir().unwrap_or_else(|_| self.root.clone()),
        };
        self.here = checkout_of(&start, &self.root);
        self.worktree = std::cell::OnceCell::new();
        self
    }

    /// The linked worktree the command ran in, by its top, when it ran in one; None in a main worktree or outside git.
    pub fn worktree(&self) -> Option<&Path> {
        self.worktree.get_or_init(|| linked_top(&self.here)).as_deref()
    }

    /// What a write from a linked worktree keeps of it ({path}), on its log line, its receipt and its journal line.
    pub fn worktree_value(&self) -> Option<Value> {
        self.worktree().map(|w| crate::obj! {"path" => crate::sys::slashed(w)})
    }

    /// A run receipt's repository state, asked in the checkout the command ran in, with the exact code (with_code).
    pub fn attached_code(&self) -> Option<Dict> {
        self.attached_state(with_code(&self.here, sources::git_state(&self.here, DIR)))
    }

    /// A receipt's repository state with the linked worktree the command ran in (`worktree`), when it ran in one.
    pub fn attached_state(&self, attached: Option<Dict>) -> Option<Dict> {
        let mut attached = attached?;
        if let Some(w) = self.worktree_value() {
            attached.insert("worktree".into(), w);
        }
        Some(attached)
    }

    /// Ledger.open: the record here or above, its root file read now.
    pub fn open(start: Option<&Path>) -> Res<Ledger> {
        Self::open_with(start, None, None)
    }

    pub fn open_with(start: Option<&Path>, via: Option<&str>, agent: Option<&str>) -> Res<Ledger> {
        let (via, agent) = session(via, agent);
        let root = find_root(start).ok_or_else(|| no_project(start))?;
        let led = Ledger::new(&root, &via, agent).at(start);
        led.actor()?;
        led.project()?;
        Ok(led)
    }

    /// Ledger.init: start the record here. Where another worktree of this repository holds the project's record, a
    /// second project is started only when asked for (`new_project`).
    pub fn init(root: &Path, title: &str, id: Option<&str>, via: Option<&str>, agent: Option<&str>, new_project: bool) -> Res<Ledger> {
        let (via, agent) = session(via, agent);
        if let Some(existing) = find_root(Some(root)) {
            return Err(RBError::msg("E_PROJECT_EXISTS", format!("{} already holds a project.", crate::sys::slashed(&existing.join(DIR)))));
        }
        if !new_project {
            if let Some(there) = crate::elsewhere::record_elsewhere(root) {
                return Err(RBError::msg_fix("E_PROJECT_EXISTS", there.message(), there.fix(true)));
            }
        }
        let led = Ledger::new(root, &via, agent);
        let who = led.actor()?;
        let slug = match id {
            Some(i) => i.to_string(),
            None => {
                let low = title.to_lowercase();
                let mut s = String::new();
                let mut dash = false;
                for c in low.chars() {
                    if c.is_ascii_lowercase() || c.is_ascii_digit() {
                        s.push(c);
                        dash = false;
                    } else if !dash {
                        s.push('-');
                        dash = true;
                    }
                }
                let s: String = s.trim_matches('-').chars().take(60).collect();
                if s.is_empty() {
                    "project".into()
                } else {
                    s
                }
            }
        };
        let proj: Project = build_project(crate::dict! {
            "id" => slug, "uid" => new_uid("prj"), "title" => title, "created_by" => who.id.clone(), "created_at" => now(), "via" => via.clone()
        })?;
        if !crate::dry::active() {
            std::fs::create_dir_all(&led.dir).map_err(|e| RBError::could_not_write(&e, &led.dir))?;
        }
        led.locked(|| {
            led.write(&led.dir.join(".gitignore"), GITIGNORE)?;
            led.write(&led.dir.join(".gitattributes"), GITATTRIBUTES)?;
            // the commit the record began on ("" for none): a document older than the record is in its history
            let head = sources::git(&led.root, &["rev-parse", "--verify", "-q", "HEAD^{commit}"]).map(|h| h.trim().to_string()).unwrap_or_default();
            led.commit(&who, "init", Obj::Project(proj.clone()), crate::dict! {"title" => title, "head" => head})
        })?;
        Ok(led)
    }

    pub fn actor(&self) -> Res<Actor> {
        if let Some(a) = &self.agent {
            return Ok(Actor { id: format!("agent:{a}"), via: format!("session:{}", self.via), runtime: Some(a.clone()), ignored: None });
        }
        if let Some(a) = self.actor_cache.borrow().clone() {
            return Ok(a);
        }
        let a = actor::current(Some(&self.root))?;
        *self.actor_cache.borrow_mut() = Some(a.clone());
        Ok(a)
    }

    /// What a session said of its agent: from its store when the runtime names the session, else what this MCP
    /// connection was told. Nothing for a session with no id outside MCP: rb keeps nothing it cannot tie to one.
    pub fn said(&self, session: Option<&str>) -> actor::Said {
        match session {
            Some(s) => crate::delta::said_in(self, s),
            None => client().map(|c| c.said).unwrap_or_default(),
        }
    }

    /// agent{} for a write by `who` (None for a human's): with this command's RB_MODEL and RB_PROVIDER when
    /// `this_command`, and without them for what the session itself says, its line in the log.
    /// The ids an agent writer goes by, as the session line keeps them: its name, its runtime's name as people write
    /// it, and its model. None for a human.
    pub fn writer_ids(&self, who: &Actor) -> Vec<String> {
        let Some(facts) = self.agent_facts(who, true) else { return Vec::new() };
        writer_ids_of(&Value::Dict(facts), &who.id)
    }

    pub fn agent_facts(&self, who: &Actor, this_command: bool) -> Option<Dict> {
        if !who.id.starts_with("agent:") {
            return None;
        }
        let said = self.said(actor::session_for(who).as_deref());
        let version = if self.agent.is_some() { client().and_then(|c| c.version) } else { None };
        actor::agent_facts(who, version.as_deref(), &said, this_command)
    }

    pub fn project(&self) -> Res<Project> {
        let p = self.dir.join(ROOT_FILE);
        if !crate::dry::is_file(&p) {
            return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("{} does not exist.", self.rel(&p))));
        }
        let doc = self.doc(&p).map_err(|e| match e {
            Unreadable::Newer(m) => RBError::msg("E_STATE_NEWER", format!("{m}.")),
            Unreadable::Bad(m) => RBError::msg("E_STATE_CORRUPT", format!("{m}.")),
        })?;
        let b = body(&doc.data, "project", &self.rel(&p)).map_err(|e| match e {
            Unreadable::Newer(m) => RBError::msg("E_STATE_NEWER", format!("{m}.")),
            Unreadable::Bad(m) => RBError::msg("E_STATE_CORRUPT", format!("{m}.")),
        })?;
        let b = self.with_parts(&b, None)?;
        let rel = self.rel(&p);
        validate_as(&Value::Dict(b), &rel, "project", |v, l, e| Project::validate(v, l, e)).map_err(|e| self.edited_or_newer("project", "", &rel, e))
    }

    /// The part files of one experiment (or, with no owner, the project): (key, path), as the index has them.
    pub fn parts_of(&self, kind: &str, owner: Option<&str>) -> Res<Vec<(String, PathBuf)>> {
        let idx = self.where_()?;
        let fresh = match self.parts_for.borrow().as_ref() {
            Some(i) => !Rc::ptr_eq(i, &idx),
            None => true,
        };
        if fresh {
            let mut by: HashMap<(String, Option<String>), Vec<(String, PathBuf)>> = HashMap::new();
            for ((k, i), path) in idx.iter() {
                if is_part(k) {
                    by.entry((k.clone(), Self::path_experiment(k, path))).or_default().push((i.clone(), path.clone()));
                }
            }
            *self.parts_by.borrow_mut() = Rc::new(by);
            *self.parts_for.borrow_mut() = Some(idx.clone());
        }
        Ok(self.parts_by.borrow().get(&(kind.to_string(), owner.map(|s| s.to_string()))).cloned().unwrap_or_default())
    }

    /// A record's form with its parts read in: the variants (an experiment's) and notes kept in files of their own, after
    /// any its own file still holds from before. Variants in the order they were added (those added together, baseline
    /// first, then by name); notes by when they were written.
    pub fn with_parts(&self, form: &Dict, owner: Option<&str>) -> Res<Dict> {
        let mut out = form.clone();
        let s = |d: &Dict, k: &str| d.get(k).map(pyjson::py_str).unwrap_or_default();
        if owner.is_some() {
            let mut variants: Vec<Dict> = Vec::new();
            for (key, _) in self.parts_of("variant", owner)? {
                variants.push(self.form("variant", &key)?);
            }
            variants.sort_by(|a, b| {
                (s(a, "created_at"), role_order(&s(a, "role")), s(a, "name")).cmp(&(s(b, "created_at"), role_order(&s(b, "role")), s(b, "name")))
            });
            let mut all: Vec<Value> = match form.get("variants") {
                Some(Value::List(l)) => l.clone(),
                _ => Vec::new(),
            };
            all.extend(variants.into_iter().map(Value::Dict));
            out.insert("variants".into(), Value::List(all));
        }
        let mut notes: Vec<Dict> = Vec::new();
        for (key, _) in self.parts_of("note", owner)? {
            notes.push(self.form("note", &key)?);
        }
        notes.sort_by(|a, b| (s(a, "at"), s(a, "id")).cmp(&(s(b, "at"), s(b, "id"))));
        let mut all: Vec<Value> = match form.get("notes") {
            Some(Value::List(l)) => l.clone(),
            _ => Vec::new(),
        };
        all.extend(notes.into_iter().map(Value::Dict));
        out.insert("notes".into(), Value::List(all));
        Ok(out)
    }

    /// Every variant file of an experiment, there now or written by rb: its spec, so a finding rests on each.
    pub fn variant_keys(&self, experiment: &str) -> Res<Vec<Key>> {
        let mut keys: Vec<Key> = self.parts_of("variant", Some(experiment))?.into_iter().map(|(k, _)| ("variant".to_string(), k)).collect();
        for (k, i) in self.last_writes()?.keys() {
            if k == "variant" && i.split('/').next() == Some(experiment) {
                keys.push((k.clone(), i.clone()));
            }
        }
        keys.sort();
        keys.dedup();
        Ok(keys)
    }

    // ------------------------------------------------------------ files, the lock, the log

    /// One writer at a time across processes. Re-entrant within a Ledger.
    pub fn locked<T>(&self, f: impl FnOnce() -> Res<T>) -> Res<T> {
        if self.depth.get() > 0 {
            self.depth.set(self.depth.get() + 1);
            let r = f();
            self.depth.set(self.depth.get() - 1);
            return r;
        }
        let held = if crate::dry::active() {
            crate::sys::lock_existing(&self.dir.join(".lock"))
        } else {
            std::fs::create_dir_all(&self.dir).map_err(|e| RBError::could_not_write(&e, &self.dir))?;
            crate::sys::lock(&self.dir.join(".lock"))
        };
        self.depth.set(1);
        *self.hashes.borrow_mut() = None;
        self.hashes_stamp.set(None);
        *self.tamper.borrow_mut() = None;
        self.edited.borrow_mut().clear();
        self.forget_sides();
        self.finding_cache.borrow_mut().clear();
        *self.index.borrow_mut() = None;
        *self.log_cache.borrow_mut() = None;
        *self.session_noted.borrow_mut() = None;
        let r = f();
        self.depth.set(0);
        drop(held); // the lock goes with the file
        r
    }

    pub fn rel(&self, path: &Path) -> String {
        match path.strip_prefix(&self.root) {
            Ok(p) => p.to_string_lossy().replace('\\', "/"),
            Err(_) => crate::sys::slashed(path),
        }
    }

    /// Write a file atomically, readable as the rest of the checkout is.
    pub fn write(&self, path: &Path, text: &str) -> Res<()> {
        crate::dry::write(path, text.as_bytes()).map_err(|e| RBError::could_not_write(&e, path))?;
        *self.index.borrow_mut() = None;
        Ok(())
    }

    /// A record file, parsed; kept until the file changes.
    pub fn doc(&self, path: &Path) -> Result<Rc<Document>, Unreadable> {
        self.doc_keyed(path).map(|(_, d)| d)
    }

    /// The file's parse, and the key (time, size, inode) it was parsed at.
    fn doc_keyed(&self, path: &Path) -> Result<(DocKey, Rc<Document>), Unreadable> {
        let key: DocKey = match doc_key(path) {
            Some(k) => k,
            None => {
                let why = std::fs::metadata(path).err().map(|e| crate::sys::os_words(&e)).unwrap_or_else(|| "it is not a file rb can read".into());
                return Err(Unreadable::Bad(format!("{} could not be read: {why}", self.rel(path))));
            }
        };
        if let Some(hit) = DOCS.with(|d| d.borrow().get(path).filter(|(k, _)| *k == key).map(|(_, doc)| doc.clone())) {
            return Ok((key, hit));
        }
        let bytes = crate::dry::read(path).map_err(|e| Unreadable::Bad(format!("{} could not be read: {}", self.rel(path), crate::sys::os_words(&e))))?;
        let text = match String::from_utf8(bytes) {
            Ok(t) => t,
            Err(e) => return Err(Unreadable::Bad(format!("{} is {}", self.rel(path), rbyaml::not_utf8(e.as_bytes(), &e.utf8_error())))),
        };
        let doc = rbyaml::load(&text, &self.rel(path)).map_err(|e| Unreadable::Bad(e.to_string()))?;
        let doc = Rc::new(doc);
        DOCS.with(|d| {
            let mut d = d.borrow_mut();
            if d.len() > 8192 {
                d.clear();
            }
            d.insert(path.to_path_buf(), (key, doc.clone()));
        });
        Ok((key, doc))
    }

    pub fn record_files(&self) -> Vec<(&'static str, PathBuf)> {
        let mut out: Vec<(&'static str, PathBuf)> = Vec::new();
        if crate::dry::is_file(&self.dir.join(ROOT_FILE)) {
            out.push(("project", self.dir.join(ROOT_FILE)));
        }
        let exps = self.dir.join("experiments");
        let mut exp_dirs: Vec<PathBuf> = list_dir(&exps);
        exp_dirs.sort_by_cached_key(|p| parts_key(p));
        let mut sorted_glob = |inner: &dyn Fn(&Path) -> Vec<PathBuf>, kind: &'static str| {
            let mut found: Vec<PathBuf> = exp_dirs.iter().flat_map(|d| inner(d)).collect();
            found.sort_by_cached_key(|p| parts_key(p));
            for p in found {
                out.push((kind, p));
            }
        };
        let yamls = |d: PathBuf| -> Vec<PathBuf> { list_dir(&d).into_iter().filter(|p| name_of(p).ends_with(EXT) && crate::dry::is_file(p)).collect() };
        sorted_glob(&|d: &Path| vec![d.join(EXPERIMENT_FILE)].into_iter().filter(|p| crate::dry::exists(p)).collect(), "experiment");
        sorted_glob(&|d: &Path| yamls(d.join(VARIANTS_DIR)), "variant");
        sorted_glob(&|d: &Path| yamls(d.join(NOTES_DIR)), "note");
        sorted_glob(&|d: &Path| vec![d.join(CRITERIA_FILE)].into_iter().filter(|p| crate::dry::exists(p)).collect(), "criterion");
        sorted_glob(&|d: &Path| yamls(d.join(CRITERIA_DIR)), "criterion");
        sorted_glob(&|d: &Path| list_dir(&d.join("runs")).into_iter().filter(|p| name_of(p).ends_with(EXT)).collect(), "run");
        sorted_glob(&|d: &Path| list_dir(&d.join("analyses")).into_iter().filter(|p| name_of(p).ends_with(EXT)).collect(), "analysis");
        let mut project_notes = yamls(self.dir.join(NOTES_DIR));
        project_notes.sort_by_cached_key(|p| parts_key(p));
        for p in project_notes {
            out.push(("note", p));
        }
        for kind in TOP {
            let d = self.dir.join(kind_dir(kind));
            if crate::dry::is_dir(&d) {
                let mut files: Vec<PathBuf> = list_dir(&d).into_iter().filter(|p| name_of(p).ends_with(EXT)).collect();
                files.sort_by_cached_key(|p| parts_key(p));
                let k: &'static str = KINDS.iter().find(|x| x.0 == *kind).unwrap().0;
                for f in files {
                    out.push((k, f));
                }
            }
        }
        out
    }

    pub fn path_id(kind: &str, path: &Path) -> Option<String> {
        let stem = || {
            let n = name_of(path);
            n[..n.len().saturating_sub(EXT.len())].to_string()
        };
        match kind {
            "project" => None,
            "criterion" if name_of(path) == CRITERIA_FILE => None,
            "experiment" => path.parent().map(name_of),
            "variant" => Some(format!("{}/{}", path.parent().and_then(|p| p.parent()).map(name_of).unwrap_or_default(), stem())),
            _ => Some(stem()),
        }
    }

    pub fn path_experiment(kind: &str, path: &Path) -> Option<String> {
        let up = |n: usize| -> Option<&Path> {
            let mut p = Some(path);
            for _ in 0..n {
                p = p.and_then(|x| x.parent());
            }
            p
        };
        match kind {
            "criterion" if name_of(path) == CRITERIA_FILE => up(1).map(name_of),
            "criterion" | "run" | "analysis" | "variant" => up(2).map(name_of),
            "note" if up(3).map(name_of).as_deref() == Some("experiments") && up(4).map(name_of).as_deref() == Some(DIR) => up(2).map(name_of),
            _ => None,
        }
    }

    /// The records a file holds as a kind, read once for the file as it stands.
    pub fn in_(&self, kind: &str, path: &Path) -> Entries {
        let (key, doc) = self.doc_keyed(path)?;
        let ck = (self.root.clone(), path.to_path_buf(), kind.to_string());
        if let Some(hit) = ENTRIES.with(|e| e.borrow().get(&ck).filter(|(k, _)| *k == key).map(|(_, v)| v.clone())) {
            return hit;
        }
        let got: Entries = entries(kind, &doc.data, &self.rel(path), Self::path_id(kind, path).as_deref(), Self::path_experiment(kind, path).as_deref()).map(Rc::new);
        ENTRIES.with(|e| {
            let mut e = e.borrow_mut();
            if e.len() > 16384 {
                e.clear();
            }
            e.insert(ck, (key, got.clone()));
        });
        got
    }

    /// Every record on disk: (kind, id) -> its file.
    pub fn where_(&self) -> Res<Rc<Index>> {
        if let Some(i) = self.index.borrow().as_ref() {
            return Ok(i.clone());
        }
        self.edited.borrow_mut().clear();
        let mut found: IndexMap<Key, Vec<PathBuf>> = IndexMap::new();
        let mut bad: IndexMap<String, String> = IndexMap::new();
        let mut newer: Vec<Dict> = Vec::new();
        let mut newer_idx: HashMap<Key, PathBuf> = HashMap::new();
        for (kind, path) in self.record_files() {
            let ids = match self.in_(kind, &path) {
                Ok(e) => {
                    // read, with a part only a newer rb reads: named, so gates fail closed and nothing is written
                    for (id, form) in e.iter() {
                        if let Some(why) = newer_within(kind, id, form) {
                            newer.push(crate::dict! {"kind" => kind, "id" => id.clone(), "at" => "", "why" => format!("{}: {why}", self.rel(&path)), "part" => true});
                        }
                    }
                    e.iter().map(|(id, _)| id.clone()).collect::<Vec<_>>()
                }
                Err(Unreadable::Newer(why)) => {
                    newer.push(crate::dict! {"kind" => kind, "id" => Self::path_id(kind, &path).unwrap_or_else(|| self.rel(&path)), "at" => "", "why" => why});
                    for id in self.ids_at(kind, &path)? {
                        newer_idx.entry((kind.to_string(), id)).or_insert_with(|| path.clone());
                    }
                    continue;
                }
                Err(Unreadable::Bad(why)) => {
                    bad.insert(self.rel(&path), why);
                    self.ids_at(kind, &path)?
                }
            };
            for id in ids {
                found.entry((kind.to_string(), id)).or_default().push(path.clone());
            }
        }
        let mut idx: Index = IndexMap::new();
        let mut strays: Vec<Dict> = Vec::new();
        let writes = self.last_writes()?;
        for (key, paths) in found {
            let logged = writes.get(&key).and_then(|e| e.get("path")).and_then(|p| p.as_str()).map(|s| s.to_string());
            let keep = paths.iter().find(|p| Some(self.rel(p)) == logged).cloned().unwrap_or_else(|| paths[0].clone());
            for p in &paths {
                if *p != keep {
                    strays.push(crate::dict! {"kind" => key.0.clone(), "id" => key.1.clone(), "path" => self.rel(p), "kept" => self.rel(&keep)});
                }
            }
            idx.insert(key, keep);
        }
        let idx = Rc::new(idx);
        *self.index.borrow_mut() = Some(idx.clone());
        *self.unreadable.borrow_mut() = bad;
        *self.newer_files.borrow_mut() = newer;
        *self.newer_index.borrow_mut() = newer_idx;
        *self.strays.borrow_mut() = strays;
        Ok(idx)
    }

    /// The records a file that cannot be read holds: the id its place gives it, or the criteria rb last wrote to it.
    fn ids_at(&self, kind: &str, path: &Path) -> Res<Vec<String>> {
        if let Some(pid) = Self::path_id(kind, path) {
            return Ok(vec![pid]);
        }
        if kind == "criterion" {
            let rel = self.rel(path);
            let mut ids: Vec<String> =
                self.last_writes()?.iter().filter(|((k, _), e)| k == "criterion" && e.get("path").and_then(|p| p.as_str()) == Some(&rel)).map(|((_, i), _)| i.clone()).collect();
            ids.sort();
            return Ok(ids);
        }
        Ok(Vec::new())
    }

    pub fn path(&self, kind: &str, id: &str, experiment: Option<&str>) -> Res<PathBuf> {
        if kind == "project" {
            return Ok(self.dir.join(ROOT_FILE));
        }
        if kind == "variant" {
            let (exp, name) = id.split_once('/').unwrap_or((id, ""));
            return Ok(self.dir.join("experiments").join(exp).join(VARIANTS_DIR).join(format!("{name}{EXT}")));
        }
        if kind == "note" {
            if experiment.is_none() {
                if let Some(p) = self.where_()?.get(&(kind.to_string(), id.to_string())) {
                    return Ok(p.clone());
                }
            }
            return Ok(match experiment {
                Some(e) => self.dir.join("experiments").join(e).join(NOTES_DIR),
                None => self.dir.join(NOTES_DIR),
            }
            .join(format!("{id}{EXT}")));
        }
        if !is_kind(kind) {
            return Err(RBError::msg("E_STATE_NEWER", format!("This project holds a {} record, which this rb does not know: it was written by a newer rb.", repr_str(kind))));
        }
        if kind == "experiment" {
            return Ok(self.dir.join("experiments").join(id).join(EXPERIMENT_FILE));
        }
        if NESTED.contains(&kind) {
            let inner = PathBuf::from(kind_dir(kind)).join(format!("{id}{EXT}"));
            if let Some(e) = experiment {
                return Ok(self.dir.join("experiments").join(e).join(inner));
            }
            if let Some(p) = self.where_()?.get(&(kind.to_string(), id.to_string())) {
                return Ok(p.clone());
            }
            if let Some(logged) = self.last_writes()?.get(&(kind.to_string(), id.to_string())).and_then(|e| e.get_str("path")) {
                if !logged.is_empty() {
                    return Ok(self.root.join(logged));
                }
            }
            return Ok(self.dir.join("experiments").join("_").join(inner));
        }
        Ok(self.dir.join(kind_dir(kind)).join(format!("{id}{EXT}")))
    }

    /// A record's stored form as its file holds it now.
    pub fn form(&self, kind: &str, id: &str) -> Res<Dict> {
        let key = (kind.to_string(), id.to_string());
        let idx = self.where_()?;
        if kind != "project" && !idx.contains_key(&key) {
            if let Some(p) = self.newer_index.borrow().get(&key) {
                return Err(RBError::msg("E_STATE_NEWER", format!("{kind} {id} is in {}, which only a newer rb reads.", self.rel(p))));
            }
        }
        let path = if kind == "project" { Some(self.dir.join(ROOT_FILE)) } else { idx.get(&key).cloned() };
        let path = match path {
            Some(p) if crate::dry::exists(&p) => p,
            _ => return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("No {kind} {} in this project.", repr_str(id)))),
        };
        match self.in_(kind, &path) {
            Ok(items) => {
                for (i, form) in items.iter() {
                    if i == id || kind == "project" {
                        return Ok(form.clone());
                    }
                }
            }
            Err(Unreadable::Newer(m)) => return Err(RBError::msg("E_STATE_NEWER", format!("{m}."))),
            Err(Unreadable::Bad(m)) => {
                return Err(RBError::msg_fix("E_STATE_CORRUPT", format!("{m}."), "rb doctor lists every such file; rb doctor --restore puts back what rb last wrote."))
            }
        }
        Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("No {kind} {} in this project.", repr_str(id))))
    }

    /// _load: a record, validated.
    pub fn load_obj(&self, kind: &str, id: &str) -> Res<Obj> {
        let form = self.form(kind, id)?;
        let path = self.where_()?.get(&(kind.to_string(), id.to_string())).cloned().unwrap_or_else(|| self.dir.join(ROOT_FILE));
        let rel = self.rel(&path);
        let data = if kind == "criterion" && name_of(&path) == CRITERIA_FILE {
            let mut f = form;
            f.insert("experiment".into(), Value::Str(name_of(path.parent().unwrap_or(Path::new("")))));
            f
        } else if kind == "experiment" {
            self.with_parts(&form, Some(id))?
        } else {
            form
        };
        let v = Value::Dict(without_newer_relations(kind, data));
        load_valid(kind, &v, &rel).map_err(|e| self.edited_or_newer(kind, id, &rel, e))
    }

    /// A shape this rb does not know, in a file that is not what rb last wrote. Format 1 gains no field (a new field
    /// means a new format), so such a file was most likely edited by hand: said so, with `rb doctor --restore` where rb
    /// holds its last write. A newer rb's own logged write still says only to upgrade.
    fn edited_or_newer(&self, kind: &str, id: &str, rel: &str, e: RBError) -> RBError {
        thread_local! { static ASKING: std::cell::Cell<bool> = const { std::cell::Cell::new(false) }; }
        // working out what was edited reads records too: one of those failing here says only what it says
        if e.code != "E_STATE_NEWER" || ASKING.with(|a| a.replace(true)) {
            return e;
        }
        let said = self.edited_said(kind, id, rel);
        ASKING.with(|a| a.set(false));
        match said {
            Some((message, fix)) => RBError::msg_fix("E_STATE_NEWER", message, fix).with_problems(e.problems),
            None => e,
        }
    }

    fn edited_said(&self, kind: &str, id: &str, rel: &str) -> Option<(String, &'static str)> {
        let tampered = self.tampered().ok()?;
        // a record's parts are read with it: an experiment's variants and notes, the project's own notes
        let parts = match kind {
            "experiment" => Some(format!("{}/", self.rel(&self.dir.join("experiments").join(id)))),
            "project" => Some(format!("{}/", self.rel(&self.dir.join(NOTES_DIR)))),
            _ => None,
        };
        let flagged = |t: &Dict, f: &str| t.get(f).map_or(false, |v| v.truthy());
        let t = tampered.iter().find(|t| {
            let (k, p) = (t.get_str("kind").unwrap_or(""), t.get_str("path").unwrap_or(""));
            !flagged(t, "stray") && !flagged(t, "shallow") && ((k == kind && t.get_str("id") == Some(id)) || p == rel || (is_part(k) && parts.as_deref().is_some_and(|d| p.starts_with(d))))
        })?;
        let (k, i, path) = (t.get_str("kind").unwrap_or(""), t.get_str("id").unwrap_or(""), t.get_str("path").unwrap_or(rel));
        let restorable = self.last_writes().ok().and_then(|w| w.get(&(k.to_string(), i.to_string())).cloned()).is_some_and(|w| {
            w.get_str("sha256").is_some_and(|sha| matches!(self.stored_form(k, i, sha, w.get_str("path")), Ok(Some(_))))
        });
        let fix = if restorable {
            "If it was edited by hand, `rb doctor --restore` puts back what rb last wrote; if a newer rb wrote it, upgrade: `pip install --upgrade rabbit-brain`."
        } else {
            "If it was edited by hand, take the edit out of the file (rb holds no copy of its last write to put back); if a newer rb wrote it, upgrade: `pip install --upgrade rabbit-brain`."
        };
        Some((format!("{path} uses a shape this rb does not know, and it is not what rb last wrote: it was edited by hand, or written by a newer rb."), fix))
    }

    /// _load for any kind a file holds, a part's too: what rb doctor reads every file with.
    pub fn load_any(&self, kind: &str, id: &str) -> Res<()> {
        if is_part(kind) {
            self.check_part(kind, id)
        } else {
            self.load_obj(kind, id).map(|_| ())
        }
    }

    /// Forget which file holds which record, so the next read walks the files again (rb doctor, after a restore).
    pub fn forget_index(&self) {
        *self.index.borrow_mut() = None;
    }

    /// A part's file read as its model: E_STATE_CORRUPT (or E_STATE_NEWER) when it is not one.
    fn check_part(&self, kind: &str, id: &str) -> Res<()> {
        let form = self.form(kind, id)?;
        let path = self.where_()?.get(&(kind.to_string(), id.to_string())).cloned().unwrap_or_default();
        let (v, rel) = (Value::Dict(form), self.rel(&path));
        let valid = if kind == "variant" {
            validate_as(&v, &rel, "variant", |v, l, e| Variant::validate(v, l, e)).map(|_| ())
        } else {
            validate_as(&v, &rel, "note", |v, l, e| Note::validate(v, l, e)).map(|_| ())
        };
        valid.map_err(|e| self.edited_or_newer(kind, id, &rel, e))
    }

    fn load_t<T: FromObj>(&self, id: &str) -> Res<T> {
        Ok(T::from_obj(self.load_cached(T::KIND, id)?).unwrap())
    }

    /// load_obj, read once per state of the record: the same file in the same state validates the same way, and a
    /// pass over a thousand runs asks for each of them many times. Any write starts a new state (the index is dropped),
    /// so a record changed by this rb is read again.
    fn load_cached(&self, kind: &str, id: &str) -> Res<Obj> {
        let idx = self.where_()?;
        let fresh = match self.objs_for.borrow().as_ref() {
            Some(i) => !Rc::ptr_eq(i, &idx),
            None => true,
        };
        if fresh {
            self.objs.borrow_mut().clear();
            self.loaded.borrow_mut().clear();
            *self.objs_for.borrow_mut() = Some(idx.clone());
            self.finding_cache.borrow_mut().clear();
        }
        let key: Key = (kind.to_string(), id.to_string());
        if let Some(hit) = self.loaded.borrow().get(&key) {
            return Ok((**hit).clone());
        }
        // across states of the record: a record that is one file validates the same way while that file is unchanged
        // (an experiment is its file and its parts' files, the project its own reading: those are read again)
        let at = if kind != "experiment" && kind != "project" { idx.get(&key).and_then(|p| doc_key(p).map(|k| (p.clone(), k))) } else { None };
        let vk = (self.root.clone(), kind.to_string(), id.to_string());
        if let Some((path, dk)) = &at {
            let hit = VALID.with(|v| v.borrow().get(&vk).filter(|(p, k, _)| p == path && k == dk).map(|(_, _, o)| o.clone()));
            if let Some(o) = hit {
                self.loaded.borrow_mut().insert(key, o.clone());
                return Ok((*o).clone());
            }
        }
        let obj = Rc::new(self.load_obj(kind, id)?);
        if let Some((path, dk)) = at {
            // the file as it was read: the same key after the read as before it
            if doc_key(&path) == Some(dk) {
                VALID.with(|v| v.borrow_mut().insert(vk, (path, dk, obj.clone())));
            }
        }
        self.loaded.borrow_mut().insert(key, obj.clone());
        Ok((*obj).clone())
    }

    /// Write one record's stored form into its file, keeping the comments already there.
    pub fn put(&self, kind: &str, id: &str, path: &Path, form: Dict) -> Res<()> {
        let data: Value;
        let comments: Option<rbyaml::Comments>;
        if kind == "criterion" && name_of(path) == CRITERIA_FILE {
            let mut items: Vec<Value> = Vec::new();
            let mut cm = None;
            if crate::dry::exists(path) {
                let exp = path.parent().map(name_of);
                match self.doc(path).and_then(|doc| entries("criterion", &doc.data, &self.rel(path), None, exp.as_deref()).map(|e| (e, doc))) {
                    Ok((es, doc)) => {
                        items = es.into_iter().map(|(_, f)| Value::Dict(f)).collect();
                        cm = Some(doc.comments.clone());
                    }
                    Err(Unreadable::Newer(m)) => return Err(RBError::msg("E_STATE_NEWER", format!("{m}; this rb will not write over it."))),
                    Err(Unreadable::Bad(m)) => return Err(RBError::msg("E_STATE_EDITED", format!("{m}; rb will not write over it."))),
                }
            }
            match items.iter().position(|x| x.get_str("id") == Some(id)) {
                None => items.push(Value::Dict(form)),
                Some(at) => items[at] = Value::Dict(form),
            }
            data = crate::obj! {FORMAT_KEY => RB_FORMAT, "kind" => "criteria", "experiment" => name_of(path.parent().unwrap()), "criteria" => Value::List(items)};
            comments = cm;
        } else {
            comments = if crate::dry::exists(path) { self.doc(path).ok().map(|d| d.comments.clone()) } else { None };
            let mut d = crate::dict! {FORMAT_KEY => RB_FORMAT, "kind" => kind};
            for (k, v) in form {
                d.insert(k, v);
            }
            data = Value::Dict(d);
        }
        let text = rbyaml::dumps(&data, comments.as_ref()).map_err(|e| RBError::msg("E_OBJECT_INVALID", e.0))?;
        self.write(path, &text)
    }

    /// Check the state a write rests on, including a request that would leave it unchanged.
    fn check_write(&self, kind: &str, id: &str) -> Res<()> {
        let unread = self.newer()?;
        if let Some(first) = unread.first() {
            return Err(RBError::msg(
                "E_STATE_NEWER",
                format!(
                    "Part of this record needs a newer rb ({}); this rb reads the rest but writes nothing until it is upgraded, so no write is made against state it cannot see.",
                    first.get("why").map(pyjson::py_str).unwrap_or_default()
                ),
            ));
        }
        self.check_newer_shapes()?;
        if let Some(why) = self.edited_outside(kind, id)? {
            return Err(self.will_not(kind, id, format!("{why}; rb will not write over it."), None)?);
        }
        Ok(())
    }

    /// Find future model shapes before the first mutation. The index has already parsed these files, but only
    /// checks their headers and locations. Cache this screen by file stamp; validate only unfamiliar shapes, so a
    /// missing required field beside an unknown field still follows the existing corruption/recovery behavior.
    fn check_newer_shapes(&self) -> Res<()> {
        let mut seen = HashSet::new();
        for ((kind, _), path) in self.where_()?.iter() {
            if !seen.insert(path.clone()) { continue; }
            let Some(stamp) = doc_key(path) else { continue; };
            let key = (self.root.clone(), path.clone(), kind.clone());
            let cached = NEWER_SHAPES.with(|s| s.borrow().get(&key).filter(|(at, _)| *at == stamp).map(|(_, error)| error.clone()));
            let error = match cached {
                Some(error) => error,
                None => {
                    let mut error = None;
                    if let Ok(forms) = self.in_(kind, path) {
                        for (id, form) in forms.iter() {
                            if unfamiliar_shape(Self::meta_of(kind), form) {
                                if let Err(e) = self.load_any(kind, id) {
                                    if e.code == "E_STATE_NEWER" { error = Some(e); break; }
                                }
                            }
                        }
                    }
                    NEWER_SHAPES.with(|s| {
                        let mut s = s.borrow_mut();
                        if s.len() > 16384 { s.clear(); }
                        s.insert(key, (stamp, error.clone()));
                    });
                    error
                }
            };
            if let Some(error) = error { return Err(error); }
        }
        Ok(())
    }

    /// Write a record and log it with its path, content hash and detail.
    pub fn commit(&self, who: &Actor, op: &str, obj: Obj, detail: Dict) -> Res<()> {
        self.commit_stating(who, op, obj, detail, None)
    }

    /// As commit, for a write that states parts of an experiment's design (`stated`: the experiment, and each part with
    /// what it reads after): the user's line names them with the parts it changed, even where they read as before, as an
    /// earlier rb reads them; a session's names only what it changed.
    pub(crate) fn commit_stating(&self, who: &Actor, op: &str, obj: Obj, mut detail: Dict, stated: Option<(&str, Dict)>) -> Res<()> {
        let kind = obj.kind();
        let id = obj.id().to_string();
        self.check_write(kind, &id)?;
        let owner: Option<String> = match &obj {
            Obj::Experiment(e) => Some(e.id.clone()),
            Obj::Criterion(c) => Some(c.experiment.clone()),
            _ => None,
        };
        let (mut plan, legacy) = match &owner {
            Some(o) => self.criteria_to_move(o)?,
            None => (IndexMap::new(), None),
        };
        let moving: HashSet<Key> = plan.keys().cloned().collect();
        let mut obj = obj;
        // a note from before notes had files: its file is named now
        let notes = match &mut obj {
            Obj::Experiment(e) => Some(&mut e.notes),
            Obj::Project(p) => Some(&mut p.notes),
            _ => None,
        };
        if let Some(notes) = notes {
            for n in notes.iter_mut() {
                if n.id.as_deref().map_or(true, |x| x.is_empty()) {
                    n.id = Some(self.new_id("note", None)?);
                }
            }
        }
        let mut form = obj.stored();
        if kind == "experiment" || kind == "project" {
            if kind == "experiment" {
                if let Some(Value::List(vs)) = form.shift_remove("variants") {
                    for v in vs {
                        let Value::Dict(vd) = v else { continue };
                        let key = format!("{id}/{}", vd.get("name").map(pyjson::py_str).unwrap_or_default());
                        let path = self.path("variant", &key, None)?;
                        plan.insert(("variant".to_string(), key), (path, vd));
                    }
                }
            }
            if let Some(Value::List(ns)) = form.shift_remove("notes") {
                for n in ns {
                    let Value::Dict(nd) = n else { continue };
                    let nid = nd.get("id").map(pyjson::py_str).unwrap_or_default();
                    let path = self.path("note", &nid, if kind == "experiment" { Some(id.as_str()) } else { None })?;
                    plan.insert(("note".to_string(), nid), (path, nd));
                }
            }
        }
        let main_path = self.path(kind, &id, if NESTED.contains(&kind) { obj.experiment() } else { None })?;
        plan.insert((kind.to_string(), id.clone()), (main_path, form));
        let main_key: Key = (kind.to_string(), id.clone());
        let mut writes: Vec<(Key, PathBuf, Dict, String)> = Vec::new();
        for (key, (path, f)) in plan.iter() {
            let sha = rbyaml::content_sha(&Value::Dict(f.clone())).map_err(|e| RBError::msg("E_OBJECT_INVALID", e.0))?;
            if !moving.contains(key) && self.unchanged(&key.0, &key.1, path, &sha)? {
                continue; // never claim a file this write did not change
            }
            if *key != main_key && !moving.contains(key) {
                if let Some(why) = self.edited_outside(&key.0, &key.1)? {
                    return Err(self.will_not(&key.0, &key.1, format!("{why}; rb will not write over it."), None)?);
                }
            }
            writes.push((key.clone(), path.clone(), f.clone(), sha));
        }
        if who.id.starts_with("agent:") {
            // the author text this write adds, read before its files change: a note moved to its own file adds none
            let (mut before, mut after) = (Vec::new(), Vec::new());
            for (key, _, f, _) in &writes {
                if let Ok(old) = self.form(&key.0, &key.1) {
                    before.extend(crate::plain::prose(&key.0, &old));
                }
                after.extend(crate::plain::prose(&key.0, f));
            }
            crate::plain::added(true, op, &before, &after, &self.writer_ids(who));
        }
        // what each file of a design it writes held before it, and of which experiment, read before its files change, so
        // its line can say which parts of which design the write changed: the standing never needs that form again
        let mut design_files: Vec<(usize, String, Option<(String, design::FileParts)>)> = Vec::new();
        for (n, (key, _, f, _)) in writes.iter().enumerate() {
            let Some(exp) = design::file_experiment(&key.0, &key.1, f).or_else(|| owner.clone().filter(|_| key.0 == "criterion")) else { continue };
            let before = if self.where_()?.contains_key(key) {
                self.form(&key.0, &key.1).ok().and_then(|old| {
                    let was = design::file_experiment(&key.0, &key.1, &old).unwrap_or_else(|| exp.clone());
                    design::file_parts(&key.0, &was, &old).map(|parts| (was, parts))
                })
            } else {
                Some((exp.clone(), HashMap::new()))
            };
            design_files.push((n, exp, before));
        }
        // the designs a session wrote some of that are the user's own before this write of theirs: its line keeps the
        // design the write makes of each (`detail.owned`), worked out now, never later
        let mut touching: Vec<String> = Vec::new();
        for e in design_files.iter().map(|(_, e, _)| e.clone()).chain((op == "gate" && kind == "experiment").then(|| id.clone())) {
            if !touching.contains(&e) {
                touching.push(e);
            }
        }
        let owned = self.owned_before(who, &touching)?;
        for (key, path, f, sha) in &writes {
            self.put(&key.0, &key.1, path, f.clone())?;
            self.stash(f, sha);
        }
        let mut changed = Dict::new();
        let mut unread: HashSet<String> = HashSet::new();
        for (n, exp, before) in design_files {
            let (key, _, f, _) = &writes[n];
            match (before, design::file_parts(&key.0, &exp, f)) {
                (Some((was, b)), Some(a)) => {
                    // a file that moves to another experiment's design takes its parts from one and gives them to the other
                    let each = if was == exp { vec![(exp, vec![b], a)] } else { vec![(was, vec![b], HashMap::new()), (exp, Vec::new(), a)] };
                    for (x, b, a) in each {
                        if let Value::Dict(into) = changed.entry(x).or_insert_with(Value::empty_dict) {
                            design::parts_changed(&b, &a, into, who.is_human());
                        }
                    }
                }
                _ => {
                    unread.insert(exp);
                }
            }
        }
        if let Some((exp, parts)) = stated.filter(|(exp, _)| who.is_human() && changed.contains_key(*exp)) {
            if let Some(Value::Dict(into)) = changed.get_mut(exp) {
                for (k, v) in parts {
                    into.entry(k).or_insert(v);
                }
            }
        }
        for exp in &unread {
            changed.shift_remove(exp);
        }
        if !changed.is_empty() {
            design::keep_changed(&mut detail, changed, who);
        }
        let words = (op == "gate" && kind == "experiment").then(|| (id.as_str(), self.words.as_str()));
        if let Some(kept) = self.owned_after(&owned, words)? {
            detail.insert("owned".into(), kept);
        }
        if let Some(l) = legacy {
            let _ = crate::dry::remove(&l); // every criterion it held now has a file of its own
            *self.index.borrow_mut() = None;
        }
        let main = writes.iter().find(|w| w.0 == main_key).map(|w| (w.3.clone(), self.rel(&w.1)));
        let parts: Vec<Value> = writes
            .iter()
            .filter(|w| w.0 != main_key)
            .map(|w| crate::obj! {"kind" => w.0 .0.clone(), "id" => w.0 .1.clone(), "path" => self.rel(&w.1), "sha256" => w.3.clone()})
            .collect();
        self.log_parts(who, op, kind, &id, main.as_ref().map(|m| m.0.as_str()), main.as_ref().map(|m| m.1.as_str()), parts, detail)
    }

    /// Whether the record's file already holds exactly this content, where it is to be written.
    fn unchanged(&self, kind: &str, id: &str, path: &Path, sha: &str) -> Res<bool> {
        if self.where_()?.get(&(kind.to_string(), id.to_string())).map(|p| p.as_path()) != Some(path) {
            return Ok(false);
        }
        Ok(match self.form(kind, id) {
            Ok(f) => rbyaml::content_sha(&Value::Dict(f)).map(|x| x == sha).unwrap_or(false),
            Err(_) => false,
        })
    }

    /// Which sides of a merge are rb's is read from the log: a new log is read again.
    fn forget_sides(&self) {
        self.sides.borrow_mut().clear();
        self.cuts.borrow_mut().clear();
    }

    /// rb will not write over a record that is not what it last wrote: in a shallow clone the fix is the history that
    /// shows how it got there, never a restore that could put back one side of a merge.
    fn will_not(&self, kind: &str, id: &str, message: String, fix: Option<&str>) -> Res<RBError> {
        Ok(if self.shallow_cut(kind, id)? {
            RBError::msg_fix("E_STATE_EDITED", message, format!("{UNSHALLOW} fetches the whole history, so rb can see how it got there; {UNSHALLOW_CI}."))
        } else {
            match fix {
                Some(f) => RBError::msg_fix("E_STATE_EDITED", message, f),
                None => RBError::msg("E_STATE_EDITED", message),
            }
        })
    }

    /// An experiment's criteria still in one criteria file, each planned into a file of its own, and that file, which goes
    /// once they are written. None is moved while any of them is not what rb wrote.
    #[allow(clippy::type_complexity)]
    fn criteria_to_move(&self, experiment: &str) -> Res<(IndexMap<Key, (PathBuf, Dict)>, Option<PathBuf>)> {
        let legacy = self.dir.join("experiments").join(experiment).join(CRITERIA_FILE);
        if !crate::dry::is_file(&legacy) {
            return Ok((IndexMap::new(), None));
        }
        let held: Vec<Key> = self.where_()?.iter().filter(|((k, _), p)| k == "criterion" && **p == legacy).map(|(k, _)| k.clone()).collect();
        for (k, i) in &held {
            if let Some(why) = self.edited_outside(k, i)? {
                return Err(self.will_not(
                    k,
                    i,
                    format!("{why}; rb will not move it to a file of its own."),
                    Some("rb doctor --restore puts back what rb last wrote; rb doctor --adopt --why \"...\" keeps it as it stands."),
                )?);
            }
        }
        let mut plan = IndexMap::new();
        for (k, i) in held {
            let c: Criterion = self.load(&i)?;
            let path = self.path("criterion", &i, Some(experiment))?;
            plan.insert((k, i), (path, c.stored()));
        }
        Ok((plan, Some(legacy)))
    }

    /// Keep a copy of what rb wrote, by its content hash, so rb doctor --restore can put it back exactly.
    pub fn stash(&self, form: &Dict, sha: &str) {
        let p = self.dir.join("cache").join("objects").join(&sha[..2]).join(format!("{sha}.json.gz"));
        if p.exists() || crate::dry::active() {
            return;
        }
        let Ok(text) = rbyaml::canonical_json(&Value::Dict(form.clone())) else { return };
        use flate2::write::GzEncoder;
        use std::io::Write;
        let mut enc = GzEncoder::new(Vec::new(), flate2::Compression::best());
        if enc.write_all(text.as_bytes()).is_err() {
            return;
        }
        if let Ok(bytes) = enc.finish() {
            let _ = write_atomic(&p, &bytes);
        }
    }

    /// rb's copy of a form it wrote or adopted in this checkout, by its hash (`.rb/cache`, which git does not keep).
    pub fn cached_form(&self, sha: &str) -> Option<Dict> {
        if sha.len() < 2 {
            return None;
        }
        let p = self.dir.join("cache").join("objects").join(&sha[..2]).join(format!("{sha}.json.gz"));
        let bytes = std::fs::read(&p).ok()?;
        use std::io::Read;
        let mut d = flate2::read::GzDecoder::new(&bytes[..]);
        let mut text = String::new();
        d.read_to_string(&mut text).ok()?;
        match pyjson::loads(&text) {
            Ok(Value::Dict(form)) if rbyaml::content_sha(&Value::Dict(form.clone())).ok().as_deref() == Some(sha) => Some(form),
            _ => None,
        }
    }

    /// What rb wrote with this content hash: from rb's own copies, else from the file's git history.
    pub fn stored_form(&self, kind: &str, id: &str, sha: &str, rel: Option<&str>) -> Res<Option<Dict>> {
        if let Some(form) = self.cached_form(sha) {
            return Ok(Some(form));
        }
        let rel = match rel {
            Some(r) => r.to_string(),
            None => match self.last_writes()?.get(&(kind.to_string(), id.to_string())).and_then(|e| e.get_str("path")).filter(|s| !s.is_empty()) {
                Some(p) => p.to_string(),
                None => self.rel(&self.path(kind, id, None)?),
            },
        };
        let path = self.root.join(&rel);
        // --full-history: a merge that kept one side's file hides the other side's writes from the simplified history
        let commits = sources::run("git", &["log", "--full-history", "--format=%H", "--", &rel], Some(&self.root), &[], None, std::time::Duration::from_secs(30));
        if let Some(c) = commits {
            for commit in String::from_utf8_lossy(&c.stdout).split_whitespace() {
                let Some(blob) = sources::git_bytes(&self.root, &["show", &format!("{commit}:./{rel}")]) else { continue };
                if blob.is_empty() {
                    continue;
                }
                let Ok(text) = String::from_utf8(blob) else { continue };
                let Ok(data) = rbyaml::loads(&text, &rel) else { continue };
                let Ok(es) = entries(kind, &data, &rel, Self::path_id(kind, &path).as_deref(), Self::path_experiment(kind, &path).as_deref()) else { continue };
                for (i, form) in es {
                    if (i == id || kind == "project") && rbyaml::content_sha(&Value::Dict(form.clone())).ok().as_deref() == Some(sha) {
                        return Ok(Some(form));
                    }
                }
            }
        }
        Ok(None)
    }

    /// Every write, attributed: who, the chat session when the runtime says, what rb knows of an agent, and the user's
    /// words an agent acted on.
    pub fn log(&self, who: &Actor, op: &str, kind: &str, id: &str, sha256: Option<&str>, path: Option<&str>, detail: Dict) -> Res<()> {
        self.log_parts(who, op, kind, id, sha256, path, Vec::new(), detail)
    }

    /// One line per command: the record it is about (with its hash, when its own file was written) and `parts`, every
    /// other file the command wrote, each with its kind, id, path and hash.
    #[allow(clippy::too_many_arguments)]
    pub fn log_parts(&self, who: &Actor, op: &str, kind: &str, id: &str, sha256: Option<&str>, path: Option<&str>, parts: Vec<Value>, detail: Dict) -> Res<()> {
        // Recovery must remain able to repair a record it cannot currently read. Normal log-only writes (such as
        // reconcile's summary) obey the same future-shape preflight as a commit.
        if !["restore", "reid", "adopt", "adopt_delete"].contains(&op) {
            self.check_newer_shapes()?;
        }
        let mut entry = crate::dict! {"at" => now(), "actor" => who.id.clone(), "actor_via" => who.via.clone(), "via" => self.via.clone(), "op" => op, "kind" => kind, "id" => id};
        let session = actor::session_for(who);
        if let Some(s) = &session {
            entry.insert("session".into(), Value::Str(s.clone()));
        }
        let agent = self.agent_facts(who, true);
        if let Some(a) = &agent {
            entry.insert("agent".into(), Value::Dict(a.clone()));
        }
        if !self.words.is_empty() {
            entry.insert("words".into(), Value::Str(self.words.clone()));
        }
        if let Some(ig) = &who.ignored {
            entry.insert("ignored_rb_actor".into(), Value::Str(ig.clone()));
        }
        // where the write was made, from a linked worktree: who wrote where is worked out from the files
        if let Some(w) = self.worktree_value() {
            entry.insert("worktree".into(), w);
        }
        if let Some(s) = sha256.filter(|s| !s.is_empty()) {
            entry.insert("sha256".into(), Value::Str(s.to_string()));
        }
        if let Some(p) = path.filter(|p| !p.is_empty()) {
            entry.insert("path".into(), Value::Str(p.to_string()));
        }
        if !parts.is_empty() {
            entry.insert("parts".into(), Value::List(parts));
        }
        // a reason only the log keeps (a question moved, a project retitled, an adoption)
        if let Some(why) = detail.get_str("why").filter(|w| !w.trim().is_empty()) {
            crate::plain::added(who.id.starts_with("agent:"), op, &[], &[("why", why.to_string())], &self.writer_ids(who));
        }
        if !detail.is_empty() {
            entry.insert("detail".into(), Value::Dict(detail));
        }
        if let (Some(s), Some(_)) = (&session, &agent) {
            self.note_session(who, s, entry.get_str("at").unwrap_or_default())?;
        }
        self.append_log(entry)
    }

    /// The line that says the agent facts of a writer in a session, written before its next line there (at that
    /// line's time, so the log sorts it first) when they are first known or have changed (a model newly said): op
    /// "session", with no hash and no path, so every rb reads past it. Each writer and channel in a session has its
    /// own (an agent and the script it runs through the SDK), so writes that alternate between them add none. Returns
    /// whether it wrote one.
    pub fn note_session(&self, who: &Actor, session: &str, at: &str) -> Res<bool> {
        let Some(facts) = self.agent_facts(who, false) else { return Ok(false) };
        let key = [session.to_string(), who.id.clone(), self.via.clone()];
        if self.session_noted.borrow().as_ref().map_or(false, |(k, f)| *k == key && Value::Dict(f.clone()) == Value::Dict(facts.clone())) {
            return Ok(false);
        }
        let theirs = |r: &&Dict| is_session_line(r) && [r.get_str("session"), r.get_str("actor"), r.get_str("via")] == key.each_ref().map(|k| Some(k.as_str()));
        let said = self.log_entries()?.iter().rev().find(theirs).and_then(|r| r.get("agent").cloned());
        let new = said != Some(Value::Dict(facts.clone()));
        if new {
            let entry = crate::dict! {
                "at" => at, "actor" => who.id.clone(), "actor_via" => who.via.clone(), "via" => self.via.clone(), "op" => "session", "kind" => "session",
                "id" => session, "session" => session, "agent" => Value::Dict(facts.clone())
            };
            self.append_log(entry)?;
        }
        *self.session_noted.borrow_mut() = Some((key, facts));
        Ok(new)
    }

    /// The session line for what an agent has just said of itself (rb context --model), under the lock, when this rb
    /// may write the record. Returns whether it wrote one.
    pub fn log_session(&self, who: &Actor, session: &str) -> Res<bool> {
        self.locked(|| {
            self.check_newer_shapes()?;
            self.note_session(who, session, &now())
        })
    }

    /// Append one line to the log, keeping what this ledger holds of the log in step.
    fn append_log(&self, mut entry: Dict) -> Res<()> {
        // the log is read in the order of its times (log_entries), so a write is never dated before the last write of a
        // record it writes: a wall clock stepped back (an NTP or a virtual machine's correction) would otherwise put it
        // first, and the record would read as not what rb last wrote. Only those records' lines set the floor, so a line
        // another machine dated ahead moves no other record's times.
        if let (Some(at), Ok(last)) = (entry.get_str("at").map(str::to_string), self.last_writes()) {
            let floor = writes_in(&entry).iter().filter_map(|(key, _)| last.get(key)).map(at_text).max();
            if let Some(floor) = floor.filter(|floor| *floor > at) {
                entry.insert("at".into(), Value::Str(floor));
            }
        }
        let line = pyjson::jsonl_line(&Value::Dict(entry.clone()));
        let logp = self.dir.join("log.jsonl");
        // what this ledger holds of the log stays the whole log when the log was just as it read it before this line
        let whole = self.hashes.borrow().is_some() && self.hashes_stamp.get().is_some() && self.hashes_stamp.get() == log_stamp(&self.dir);
        if let Err(e) = crate::dry::append(&logp, line.as_bytes()) {
            return Err(RBError::could_not_write(&e, &logp));
        }
        *self.log_cache.borrow_mut() = None;
        self.hashes_stamp.set(if whole { log_stamp(&self.dir) } else { None });
        {
            let mut h = self.hashes.borrow_mut();
            if let Some(rc) = h.as_mut() {
                let m = Rc::make_mut(rc);
                for (key, w) in writes_in(&entry) {
                    m.insert(key, w);
                }
            }
        }
        *self.tamper.borrow_mut() = None;
        self.edited.borrow_mut().clear();
        self.forget_sides();
        self.finding_cache.borrow_mut().clear(); // a finding reads the log too (what rb wrote, and when)
        Ok(())
    }

    pub fn log_entries(&self) -> Res<Rc<Vec<Dict>>> {
        let p = self.dir.join("log.jsonl");
        let Some(stamp) = crate::dry::size_time(&p) else { return Ok(Rc::new(Vec::new())) };
        if let Some((s, rows)) = self.log_cache.borrow().as_ref() {
            if *s == stamp {
                return Ok(rows.clone());
            }
        }
        let text = crate::dry::read(&p)
            .and_then(|b| String::from_utf8(b).map_err(|_| std::io::Error::new(std::io::ErrorKind::InvalidData, "stream did not contain valid UTF-8")))
            .map_err(|e| RBError::msg("E_STATE_CORRUPT", format!("{DIR}/log.jsonl cannot be read: {}", crate::sys::os_words(&e))))?;
        let mut rows: Vec<Dict> = Vec::new();
        for (n, line) in py_splitlines(&text).iter().enumerate() {
            if crate::rbyaml::py_strip(line).is_empty() {
                continue;
            }
            match pyjson::loads(line) {
                Ok(Value::Dict(d)) => rows.push(d),
                _ => {
                    return Err(RBError::msg("E_STATE_CORRUPT", format!("{DIR}/log.jsonl line {} is not a log entry (a merge conflict?).", n + 1))
                        .with_problems(vec![line.chars().take(200).collect()]))
                }
            }
        }
        rows.sort_by(|a, b| at_text(a).cmp(&at_text(b)));
        let rows = Rc::new(rows);
        *self.log_cache.borrow_mut() = Some((stamp, rows.clone()));
        Ok(rows)
    }

    /// The log entries about one record, or one setting of an experiment (`e1/lr`), the last `last` of them.
    pub fn log_about(&self, last: Option<usize>, about: Option<&str>) -> Res<Vec<Dict>> {
        let rows = self.log_entries()?;
        let mut out: Vec<Dict> = match about {
            Some(a) => {
                let (exp_id, setting) = parse_address(a);
                rows.iter()
                    .filter(|r| r.get("id").and_then(|v| v.as_str()) == Some(exp_id.as_str()) && setting.as_ref().map_or(true, |s| checked(r, s).is_some()))
                    .cloned()
                    .collect()
            }
            None => rows.as_ref().clone(),
        };
        if let Some(n) = last.filter(|n| *n > 0) {
            if out.len() > n {
                out = out.split_off(out.len() - n);
            }
        }
        Ok(out)
    }

    /// The last log entry that wrote each record (or recorded someone adopting its deletion).
    pub fn last_writes(&self) -> Res<Rc<IndexMap<Key, Dict>>> {
        if let Some(h) = self.hashes.borrow().as_ref() {
            return Ok(h.clone());
        }
        let mut last: IndexMap<Key, Dict> = IndexMap::new();
        let mut unknown: IndexMap<Key, Dict> = IndexMap::new();
        for r in self.log_entries()?.iter() {
            for ((k, i), w) in writes_in(r) {
                if LEGACY_KINDS.contains(&k.as_str()) {
                    continue;
                }
                if k != "project" && !is_kind(&k) && !is_part(&k) {
                    unknown.insert((k, i), w);
                    continue;
                }
                last.insert((k, i), w);
            }
        }
        let mut unk: Vec<(&Key, &Dict)> = unknown.iter().collect();
        unk.sort_by(|a, b| a.0.cmp(b.0));
        *self.newer_log.borrow_mut() = unk
            .iter()
            .map(|((k, i), r)| {
                crate::dict! {"kind" => k.clone(), "id" => i.clone(), "at" => r.get("at").cloned().unwrap_or(Value::Str(String::new())),
                "why" => format!("log.jsonl records a {} record, which this rb does not know", repr_str(k))}
            })
            .collect();
        let rc = Rc::new(last);
        *self.hashes.borrow_mut() = Some(rc.clone());
        self.hashes_stamp.set(self.log_cache.borrow().as_ref().map(|(st, _)| *st));
        Ok(rc)
    }

    /// Everything in this .rb/ that only a newer rb can read.
    pub fn newer(&self) -> Res<Vec<Dict>> {
        self.last_writes()?;
        self.where_()?;
        let mut out = self.newer_log.borrow().clone();
        out.extend(self.newer_files.borrow().iter().cloned());
        Ok(out)
    }

    /// A fingerprint of the record as it stands.
    pub fn state_digest(&self) -> Res<String> {
        let mut writes: Vec<(String, String, String)> = self
            .last_writes()?
            .iter()
            .map(|((k, i), e)| {
                let s = match e.get("sha256") {
                    Some(v) if v.truthy() => pyjson::py_str(v),
                    _ => e.get("op").map(pyjson::py_str).unwrap_or_default(),
                };
                (k.clone(), i.clone(), s)
            })
            .collect();
        writes.sort();
        let mut tampered: Vec<(String, String, String)> =
            self.tampered()?.iter().map(|t| (t.get_str("kind").unwrap_or("").into(), t.get_str("id").unwrap_or("").into(), t.get_str("why").unwrap_or("").into())).collect();
        tampered.sort();
        let tl = |v: Vec<(String, String, String)>| Value::List(v.into_iter().map(|(a, b, c)| crate::list![a, b, c]).collect());
        let body = crate::obj! {"v" => RB_FORMAT, "writes" => tl(writes), "tampered" => tl(tampered), "newer" => Value::List(self.newer()?.into_iter().map(Value::Dict).collect())};
        let text = pyjson::dumps_with(&body, &Dumps { sort_keys: true, ..Default::default() }).unwrap_or_default();
        Ok(sha256_bytes(text.as_bytes()))
    }

    /// Why a record is not what rb last wrote, or None.
    pub fn edited_outside(&self, kind: &str, id: &str) -> Res<Option<String>> {
        Ok(self.outside(kind, id)?.map(|(why, _)| why))
    }

    /// Whether a record is not what rb last wrote only as far as a shallow clone shows: git lacks the history that made
    /// its file, so rb cannot tell a merge of its own writes from an edit until the whole history is fetched.
    pub fn shallow_cut(&self, kind: &str, id: &str) -> Res<bool> {
        Ok(self.outside(kind, id)?.map_or(false, |(_, cut)| cut))
    }

    fn outside(&self, kind: &str, id: &str) -> Res<Option<(String, bool)>> {
        let key = (kind.to_string(), id.to_string());
        self.where_()?; // the state it is worked out for: a new index clears what was worked out before
        if let Some(hit) = self.edited.borrow().get(&key) {
            return Ok(hit.clone());
        }
        let said = self.edited_outside_now(kind, id)?;
        self.edited.borrow_mut().insert(key, said.clone());
        Ok(said)
    }

    fn edited_outside_now(&self, kind: &str, id: &str) -> Res<Option<(String, bool)>> {
        let key = (kind.to_string(), id.to_string());
        let writes = self.last_writes()?;
        let entry = writes.get(&key);
        let idx = self.where_()?;
        let mut found = idx.get(&key).cloned();
        if found.is_none() && self.newer_index.borrow().contains_key(&key) {
            return Ok(None);
        }
        if kind == "project" {
            found = if crate::dry::exists(&self.dir.join(ROOT_FILE)) { Some(self.dir.join(ROOT_FILE)) } else { None };
        }
        let said = |why: String| Ok(Some((why, false)));
        let Some(entry) = entry else {
            return if found.is_some() { said(format!("{kind} {id} was not written by rb: log.jsonl has no record of it")) } else { Ok(None) };
        };
        if entry.get_str("op") == Some("adopt_delete") {
            return if found.is_some() { said(format!("{kind} {id} was deleted (someone adopted that) and it is back")) } else { Ok(None) };
        }
        let logged = entry.get("path").filter(|v| v.truthy()).map(pyjson::py_str);
        let unreadable = self.unreadable.borrow().clone();
        let Some(found) = found else {
            if let Some(l) = &logged {
                if let Some(why) = unreadable.get(l) {
                    return said(format!("{l} cannot be read: {why}"));
                }
            }
            return said(format!("{kind} {id} was deleted outside rb"));
        };
        let rel = self.rel(&found);
        if let Some(l) = &logged {
            if rel != *l {
                return said(format!("{kind} {id} was moved outside rb: rb wrote it at {l}, it is now at {rel}"));
            }
        }
        if let Some(why) = unreadable.get(&rel) {
            return said(format!("{rel} cannot be read: {why}"));
        }
        let form = match self.form(kind, id) {
            Ok(f) => f,
            Err(e) => return said(format!("{rel}: {}", e.message)),
        };
        let sha = rbyaml::content_sha(&Value::Dict(form.clone())).unwrap_or_default();
        if Some(sha.as_str()) == entry.get_str("sha256") {
            return Ok(None);
        }
        let merge = self.merge_of(kind, id, &rel, &form)?;
        if merge.as_ref().map_or(false, |m| m.union) {
            return Ok(None); // git merged what rb wrote on two branches and kept both sides: rb's own record
        }
        if self.cut_short(kind, id, &rel, &form)? || self.kept_unseen(kind, id, &sha)? {
            return Ok(Some((
                format!("{kind} {id} is not what rb last wrote, and this clone is shallow: git lacks the history that made {rel}, so rb cannot see how it got there (a merge may have made it)"),
                true,
            )));
        }
        match merge {
            // a conflict resolved by keeping one side whole: rb wrote that side, and the other side's change is gone
            Some(m) if m.sides.iter().any(|s| rbyaml::content_sha(&Value::Dict(s.clone())).ok().as_deref() == Some(sha.as_str())) => {
                said(format!("{kind} {id} keeps one side of the merge of {}: it is what rb wrote there, without the other side's change", m.merged_in))
            }
            Some(m) => said(format!("{kind} {id} was changed in the merge of {} and is not what rb wrote on either side (nor both sides kept)", m.merged_in)),
            // what rb wrote in a later line of the log that is dated before the line the times put last: a clock stepped
            // back under an earlier rb, or a merge or rebase that put the lines out of order. rb cannot tell which, so it
            // says so, and how each way out leaves it: an adoption is still checked against the write dated last, since
            // the file's write may be a stale side that undoes a later freeze or call
            None if self.last_in_file_order(kind, id).as_ref().and_then(|w| w.get_str("sha256")) == Some(sha.as_str()) => said(format!(
                "{kind} {id} is what rb wrote in a later line of log.jsonl that is dated before the line rb reads as its last (a clock stepped back, or a merge or rebase put the lines out of order): rb doctor --adopt keeps it as it stands where that changes no one's authorship, and rb doctor --restore puts back the write dated last, after which the later change is made again through rb"
            )),
            None => said(format!("{kind} {id} differs from what rb wrote: it was edited outside rb")),
        }
    }

    /// The last line of the log, in the order its lines stand in the file, that wrote a record.
    fn last_in_file_order(&self, kind: &str, id: &str) -> Option<Dict> {
        let text = crate::dry::read(&self.dir.join("log.jsonl")).ok().and_then(|b| String::from_utf8(b).ok())?;
        let key = (kind.to_string(), id.to_string());
        let mut last = None;
        for line in py_splitlines(&text) {
            let Ok(Value::Dict(row)) = pyjson::loads(line) else { continue };
            for (k, w) in writes_in(&row) {
                if k == key {
                    last = Some(w);
                }
            }
        }
        last
    }

    /// Whether what made a record's content lies past a shallow clone's boundary, where rb cannot see it: the content is
    /// as last committed (a change since is one git shows), or as a side of a merge under way holds it, and its making
    /// reaches back only to a commit the clone holds without its parents (made_past).
    fn cut_short(&self, kind: &str, id: &str, rel: &str, form: &Dict) -> Res<bool> {
        let checkout = self.checkout();
        if checkout.shallow.is_empty() {
            return Ok(false);
        }
        let parents: Vec<String> = std::iter::once("HEAD".to_string()).chain(checkout.merging.clone()).collect();
        self.made_past(kind, id, rel, form, &parents, 0)
    }

    /// Whether the making of a record's content as of a commit stops at the shallow boundary: the commit that last
    /// changed it is one the clone holds without its parents, or a merge whose content was made past it (made_past).
    fn cut_at(&self, kind: &str, id: &str, rel: &str, rev: &str, depth: usize) -> Res<bool> {
        let key = (kind.to_string(), id.to_string(), rel.to_string(), rev.to_string());
        if let Some(hit) = self.cuts.borrow().get(&key) {
            return Ok(*hit);
        }
        let Some(held) = self.version_at(kind, id, rel, rev) else { return Ok(false) };
        let content = rbyaml::content_sha(&Value::Dict(held.clone())).unwrap_or_default();
        let cut = match self.made_by(kind, id, rel, rev, &content) {
            Some((commit, parents)) if parents.is_empty() => self.checkout().shallow.contains(&commit),
            Some((_, parents)) if parents.len() > 1 && depth < 32 => self.made_past(kind, id, rel, &held, &parents, depth + 1)?,
            _ => false,
        };
        self.cuts.borrow_mut().insert(key, cut);
        Ok(cut)
    }

    /// Whether a record as a commit (or the working tree) holds it over these parents was made past the shallow
    /// boundary. A parent holding the same content made it, not the merge: its history is followed, and the record is
    /// cut only when every such parent's making is, or when another side holds the record otherwise and meets that
    /// parent only past the boundary, where rb cannot see whether the merge left out a change that side made. A merge
    /// that made it from its sides (it is what merging them gives) is cut when a side that is not rb's was made past
    /// the boundary; one whose sides meet only past it, so that rb cannot merge them, is cut unless a side is an edit
    /// git shows. Any other change is the merge's own, which git shows.
    fn made_past(&self, kind: &str, id: &str, rel: &str, held: &Dict, parents: &[String], depth: usize) -> Res<bool> {
        let sha = |f: &Option<Dict>| f.as_ref().map(|f| rbyaml::content_sha(&Value::Dict(f.clone())).unwrap_or_default());
        let content = Some(rbyaml::content_sha(&Value::Dict(held.clone())).unwrap_or_default());
        let sides: Vec<Option<Dict>> = parents.iter().map(|p| self.version_at(kind, id, rel, p)).collect();
        let same: Vec<&String> = parents.iter().zip(&sides).filter(|(_, f)| sha(f) == content).map(|(p, _)| p).collect();
        if !same.is_empty() {
            if parents.iter().zip(&sides).any(|(q, f)| f.is_some() && sha(f) != content && same.iter().any(|p| self.merge_base(p, q).is_none())) {
                return Ok(true);
            }
            for p in same {
                if !self.cut_at(kind, id, rel, p, depth)? {
                    return Ok(false);
                }
            }
            return Ok(true);
        }
        if parents.len() < 2 || sides.iter().all(|f| sha(f) == sha(&sides[0])) {
            return Ok(false); // changed from what it was made of, where git shows it
        }
        let base_rev = match parents {
            [a, b] => self.merge_base(a, b).unwrap_or_default(),
            _ => String::new(),
        };
        let explained = parents.len() == 2 && {
            let base = self.version_at(kind, id, rel, &base_rev).map(Value::Dict);
            let (a, b) = (sides[0].clone().map(Value::Dict), sides[1].clone().map(Value::Dict));
            matches!(merge3(base.as_ref(), a.as_ref(), b.as_ref()), Ok(Some(m)) if canon_json(&normal(&m)) == canon_json(&normal(&Value::Dict(held.clone()))))
        };
        if !explained && !base_rev.is_empty() {
            return Ok(false);
        }
        let mut cut = false;
        for p in parents {
            if self.rb_side(kind, id, rel, p, 0)?.is_some() {
                continue;
            }
            if !self.cut_at(kind, id, rel, p, depth)? {
                return Ok(false); // a side git shows was edited outside rb
            }
            cut = true;
        }
        Ok(cut || !explained)
    }

    /// What git says of the checkout (whether there is one, its shallow file, MERGE_HEAD, and whether another operation
    /// is under way), in one call, once.
    fn checkout(&self) -> Rc<Checkout> {
        if let Some(c) = self.checkout.borrow().as_ref() {
            return c.clone();
        }
        let busy = ["CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply"];
        let mut argv = vec!["rev-parse", "--is-shallow-repository", "--git-path", "shallow", "--git-path", "MERGE_HEAD"];
        for b in busy {
            argv.extend(["--git-path", b]);
        }
        let said = sources::git(&self.root, &argv);
        let at = |path: &str| {
            let p = PathBuf::from(path);
            if p.is_absolute() { p } else { self.root.join(p) }
        };
        let read = |path: &str| std::fs::read_to_string(at(path)).unwrap_or_default();
        let c = Rc::new(match said.as_deref().unwrap_or_default().lines().map(|l| l.trim()).collect::<Vec<_>>().as_slice() {
            [shallow, held, merging, rest @ ..] => {
                let merging = read(merging).split_whitespace().next().map(|s| s.to_string());
                Checkout {
                    repo: true,
                    shallow: if *shallow == "true" { read(held).split_whitespace().map(|s| s.to_string()).collect() } else { HashSet::new() },
                    busy: merging.is_some() || rest.iter().any(|p| at(p).exists()),
                    merging,
                }
            }
            _ => Checkout { repo: said.is_some(), shallow: HashSet::new(), merging: None, busy: false },
        });
        *self.checkout.borrow_mut() = Some(c.clone());
        c
    }

    /// What git says made a record's file what it is, when that was a merge of two sides that are rb's (a version rb
    /// wrote, or a merge of such that kept both): the sides, the side merged in, and whether the file is both sides kept.
    /// Where git cannot tell (a bundle's file), what its export kept of the merges the file rests on.
    fn merge_of(&self, kind: &str, id: &str, rel: &str, form: &Dict) -> Res<Option<Rc<MergeInfo>>> {
        let key = (kind.to_string(), id.to_string(), rbyaml::content_sha(&Value::Dict(form.clone())).unwrap_or_default());
        if let Some(m) = self.merges.borrow().get(&key) {
            return Ok(m.clone());
        }
        let mut found: Option<Rc<MergeInfo>> = None;
        let checkout = self.checkout();
        let merging = checkout.merging.clone();
        let parents = match &merging {
            Some(m) => vec!["HEAD".to_string(), m.clone()],
            None if checkout.repo => self.merge_parents(kind, id, rel, "HEAD", &key.2),
            None => Vec::new(), // no repository, as in a bundle on its own: git has nothing to show
        };
        if parents.len() >= 2 {
            let ours = self.rb_side(kind, id, rel, &parents[0], 0)?;
            let theirs = self.rb_side(kind, id, rel, &parents[1], 0)?;
            if let (Some((ours, a)), Some((theirs, b))) = (ours, theirs) {
                let base_rev = self.merge_base(&parents[0], &parents[1]).unwrap_or_default();
                let base = self.version_at(kind, id, rel, &base_rev);
                let union = both_kept(base.as_ref(), &ours, &theirs, form);
                // a change to the working tree since the merge was committed is one git shows, never the merge's
                let since = !union && merging.is_none() && self.content_at(kind, id, rel, "HEAD").as_deref() != Some(key.2.as_str());
                if !since {
                    let rests_on = if union { [vec![Merged { form: form.clone(), base, ours: ours.clone(), theirs: theirs.clone() }], a, b].concat() } else { Vec::new() };
                    let merged_in = format!("commit {}", parents[1].chars().take(12).collect::<String>());
                    found = Some(Rc::new(MergeInfo { sides: vec![ours, theirs], merged_in, union, rests_on, kept: false }));
                }
            }
        }
        if !found.as_ref().is_some_and(|m| m.union) {
            if let Some(kept) = self.kept_merge(kind, id, &key.2)? {
                found = Some(kept);
            }
        }
        self.merges.borrow_mut().insert(key, found.clone());
        Ok(found)
    }

    /// Whether this record is a bundle's, where what its export kept may speak: a bundle's record/ beside the bundle's
    /// manifest, with none of its log in git (a bundle on its own), or with every commit that changed its log changing
    /// that manifest too, as each export does (a bundle a repository hosts). A checkout's commits write its log alone,
    /// and what made its files is git's to tell. A shallow clone whose commits so far changed the log only with that
    /// manifest shows too little history to tell the two apart, however deep it is.
    fn in_bundle(&self) -> Bundled {
        if let Some(b) = self.bundle.get() {
            return b;
        }
        let commits = |p: &str| -> Option<HashSet<String>> {
            let out = sources::git(&self.root, &["log", "--full-history", "--format=%H", "--", p])?;
            Some(out.split_whitespace().map(str::to_string).collect())
        };
        let beside = self.root.file_name().is_some_and(|n| n == crate::export::RECORD) && self.root.parent().is_some_and(|p| p.join("manifest.json").is_file());
        let b = if !beside {
            Bundled::No
        } else {
            match commits(&format!("{DIR}/log.jsonl")).filter(|c| !c.is_empty()) {
                None => Bundled::Yes,
                Some(log) if !commits("../manifest.json").is_some_and(|m| log.is_subset(&m)) => Bundled::No,
                Some(_) => match sources::git(&self.root, &["rev-parse", "--is-shallow-repository"]).as_deref().map(str::trim) {
                    Some("false") => Bundled::Yes,
                    Some("true") => Bundled::CannotTell,
                    _ => Bundled::No,
                },
            }
        };
        self.bundle.set(Some(b));
        b
    }

    /// KEPT_MERGES as it may speak here: parsed, in a bundle whose manifest names the file as it is, or as this ledger
    /// kept it for the bundle it builds, with whether it speaks: not where a shallow clone shows too little history to
    /// tell the bundle a repository hosts from a checkout. None otherwise.
    fn kept(&self) -> Option<(Rc<Value>, bool)> {
        let p = self.dir.join(KEPT_MERGES);
        let md = std::fs::metadata(&p).ok()?;
        let stamp = (md.len(), crate::sys::mtime_ns(&md) as i64);
        if let Some((read, kept)) = self.kept.borrow().as_ref() {
            if *read == stamp {
                return kept.clone();
            }
        }
        let bytes = std::fs::read(&p).ok()?;
        let named = || -> Option<bool> {
            let man = pyjson::loads(&std::fs::read_to_string(self.root.parent()?.join("manifest.json")).ok()?).ok()?;
            let listed = man.get("files")?.get(&format!("{}/{DIR}/{KEPT_MERGES}", crate::export::RECORD))?.as_str()? == sha256_bytes(&bytes);
            Some(listed && man.get("format").and_then(Value::as_str) == Some(crate::export::FORMAT))
        };
        let bundle = self.in_bundle();
        let kept = if bundle != Bundled::No && named() == Some(true) {
            std::str::from_utf8(&bytes).ok().and_then(|t| pyjson::loads(t).ok()).map(|k| (Rc::new(k), bundle == Bundled::Yes))
        } else {
            None
        };
        *self.kept.borrow_mut() = Some((stamp, kept.clone()));
        kept
    }

    /// The hash of every write rb logged, per record, in the log's order ("" for one that wrote no file).
    fn logged_writes(&self) -> Res<Rc<HashMap<Key, Vec<String>>>> {
        let rows = self.log_entries()?;
        if let Some((read, by)) = self.logged_by.borrow().as_ref() {
            if Rc::ptr_eq(read, &rows) {
                return Ok(by.clone());
            }
        }
        let mut by: HashMap<Key, Vec<String>> = HashMap::new();
        for r in rows.iter() {
            for (key, w) in writes_in(r) {
                by.entry(key).or_default().push(w.get_str("sha256").unwrap_or_default().to_string());
            }
        }
        let by = Rc::new(by);
        *self.logged_by.borrow_mut() = Some((rows, by.clone()));
        Ok(by)
    }

    /// The merge that made this form of a record, read again from what an export kept (KEPT_MERGES), without git, where
    /// that may speak (kept): only for the form the record had when exported, while rb has written it no more since, and
    /// only once each merge it rests on has both sides rb's (a version rb wrote, or a kept merge that counts) and makes
    /// its form again over its base, every form read from rb's copy that its hash names. None unless such a merge made it.
    fn kept_merge(&self, kind: &str, id: &str, sha: &str) -> Res<Option<Rc<MergeInfo>>> {
        match self.kept() {
            Some((kept, true)) => self.kept_merge_in(&kept, kind, id, sha),
            _ => Ok(None),
        }
    }

    /// Whether what an export kept would make this form of a record rb's own (kept_merge), but the clone is too shallow
    /// to tell the bundle a repository hosts from a checkout, whose files git alone accounts for.
    fn kept_unseen(&self, kind: &str, id: &str, sha: &str) -> Res<bool> {
        match self.kept() {
            Some((kept, false)) => Ok(self.kept_merge_in(&kept, kind, id, sha)?.is_some()),
            _ => Ok(false),
        }
    }

    fn kept_merge_in(&self, kept: &Value, kind: &str, id: &str, sha: &str) -> Res<Option<Rc<MergeInfo>>> {
        let (Some(Value::List(records)), Some(Value::Dict(forms))) = (kept.get("records"), kept.get("forms")) else { return Ok(None) };
        let str_of = |v: &Value, k: &str| v.get(k).and_then(Value::as_str).map(str::to_string);
        let named: Vec<&Value> = records.iter().filter(|r| [("kind", kind), ("id", id), ("sha256", sha)].iter().all(|(k, want)| str_of(r, k).as_deref() == Some(*want))).collect();
        if named.is_empty() {
            return Ok(None);
        }
        let logged_writes = self.logged_writes()?;
        let logged: &[String] = logged_writes.get(&(kind.to_string(), id.to_string())).map_or(&[], Vec::as_slice);
        let at_export = named.into_iter().find(|r| r.get("writes").and_then(Value::as_i64) == i64::try_from(logged.len()).ok());
        let Some(Value::List(merges)) = at_export.and_then(|r| r.get("merges")) else { return Ok(None) };
        let copy_of = |s: &str| match forms.get(s) {
            Some(Value::Dict(f)) if rbyaml::content_sha(&Value::Dict(f.clone())).ok().as_deref() == Some(s) => Some(f.clone()),
            _ => None,
        };
        let made_by = |m: &Value, rbs: &HashSet<String>| -> Option<(String, Merged)> {
            let (made, ours, theirs) = (str_of(m, "sha256")?, str_of(m, "ours")?, str_of(m, "theirs")?);
            if !rbs.contains(&ours) || !rbs.contains(&theirs) {
                return None;
            }
            let base = match str_of(m, "base") {
                Some(b) => Some(copy_of(&b)?),
                None => None,
            };
            let (form, ours, theirs) = (copy_of(&made)?, copy_of(&ours)?, copy_of(&theirs)?);
            both_kept(base.as_ref(), &ours, &theirs, &form).then_some((made, Merged { form, base, ours, theirs }))
        };
        let mut rbs: HashSet<String> = logged.iter().filter(|s| !s.is_empty()).cloned().collect();
        let mut left: Vec<&Value> = merges.iter().collect();
        let mut counted: Vec<(String, Merged)> = Vec::new();
        loop {
            let next = left.iter().enumerate().find_map(|(n, m)| made_by(m, &rbs).map(|made| (n, made)));
            let Some((n, (made, m))) = next else { break };
            left.remove(n);
            rbs.insert(made.clone());
            counted.push((made, m));
        }
        let Some(at) = counted.iter().position(|(made, _)| made == sha) else { return Ok(None) };
        let this = counted.remove(at).1;
        let sides = vec![this.ours.clone(), this.theirs.clone()];
        let rests_on = std::iter::once(this).chain(counted.into_iter().map(|(_, m)| m)).collect();
        Ok(Some(Rc::new(MergeInfo { sides, merged_in: String::new(), union: true, rests_on, kept: true })))
    }

    /// Every record here that reads as rb's own only through merges (a file git made by keeping both sides of what rb
    /// wrote), as an export keeps it for a bundle, which has no git to ask.
    pub fn merges_rested_on(&self) -> Res<Vec<Kept>> {
        let writes = self.last_writes()?;
        let mut out = Vec::new();
        for (key, path) in self.where_()?.iter() {
            let (kind, id) = key;
            if self.edited_outside(kind, id)?.is_some() {
                continue;
            }
            let Ok(form) = self.form(kind, id) else { continue };
            let sha = rbyaml::content_sha(&Value::Dict(form.clone())).unwrap_or_default();
            if writes.get(key).and_then(|e| e.get_str("sha256")) == Some(sha.as_str()) {
                continue;
            }
            let rel = if kind == "project" { self.rel(&self.dir.join(ROOT_FILE)) } else { self.rel(path) };
            if let Some(m) = self.merge_of(kind, id, &rel, &form)?.filter(|m| m.union && !m.rests_on.is_empty()) {
                out.push(Kept { key: key.clone(), writes: self.logged_writes()?.get(key).map_or(0, Vec::len), merges: m.rests_on.clone() });
            }
        }
        Ok(out)
    }

    /// Keep `kept` beside this record (KEPT_MERGES): for each record, the form it has, how many writes of it rb logged,
    /// and which form each merge it rests on made from which, by content hash; and rb's copy of every form. Nothing for none.
    pub fn keep_merges(&self, kept: &[Kept]) -> Res<()> {
        if kept.is_empty() {
            return Ok(());
        }
        let mut forms = Dict::new();
        let mut copy = |f: &Dict| -> Res<Value> {
            let sha = rbyaml::content_sha(&Value::Dict(f.clone())).map_err(|e| RBError::msg("E_OBJECT_INVALID", e.0))?;
            forms.insert(sha.clone(), Value::Dict(f.clone()));
            Ok(Value::Str(sha))
        };
        let mut records: Vec<Value> = Vec::new();
        for k in kept {
            let mut merges: Vec<Value> = Vec::new();
            for m in &k.merges {
                let base = match &m.base {
                    Some(b) => copy(b)?,
                    None => Value::Null,
                };
                let made = crate::obj! {"sha256" => copy(&m.form)?, "base" => base, "ours" => copy(&m.ours)?, "theirs" => copy(&m.theirs)?};
                if !merges.contains(&made) {
                    merges.push(made);
                }
            }
            let form = merges.first().and_then(|m| m.get("sha256")).cloned().unwrap_or(Value::Null);
            records.push(crate::obj! {"kind" => k.key.0.clone(), "id" => k.key.1.clone(), "sha256" => form, "writes" => k.writes, "merges" => Value::List(merges)});
        }
        let path = self.dir.join(KEPT_MERGES);
        let text = pyjson::dumps_with(&crate::obj! {"records" => Value::List(records), "forms" => Value::Dict(forms)}, &Dumps::indent(1)).unwrap_or_default() + "\n";
        write_atomic(&path, text.as_bytes()).map_err(|e| RBError::could_not_write(&e, &path))?;
        // what git showed rb at home speaks for the bundle being built, whose manifest is written last
        if let Ok(md) = std::fs::metadata(&path) {
            *self.kept.borrow_mut() = Some(((md.len(), crate::sys::mtime_ns(&md) as i64), pyjson::loads(&text).ok().map(|k| (Rc::new(k), true))));
        }
        Ok(())
    }

    /// The records that read as rb's own by what an export kept (KEPT_MERGES), where git cannot show what merged them.
    pub fn read_by_kept_merges(&self) -> Res<Vec<Key>> {
        self.tampered()?;
        let mut keys: Vec<Key> = self.merges.borrow().iter().filter(|(_, m)| m.as_ref().is_some_and(|m| m.kept)).map(|((k, i, _), _)| (k.clone(), i.clone())).collect();
        keys.sort();
        keys.dedup();
        Ok(keys)
    }

    /// The commit that last changed a record's content (`content`, its hash as of a commit, or as the working tree
    /// holds it for HEAD), with its parents (none for a first commit, or one the clone holds without them); None when
    /// no commit holds that content (a change to the working tree). A commit that took the content as it stands from a
    /// parent did not make it and is walked past (made_from): rb compares content, never text. --full-history: a merge
    /// resolved to one side's version is a change, though git would simplify it away.
    fn made_by(&self, kind: &str, id: &str, rel: &str, rev: &str, content: &str) -> Option<(String, Vec<String>)> {
        let key = ((kind.to_string(), id.to_string(), rel.to_string(), rev.to_string()), content.to_string());
        if let Some(hit) = self.made.borrow().get(&key) {
            return hit.clone();
        }
        let (mut at, mut found) = (rev.to_string(), None);
        for _ in 0..32 {
            let last: Vec<String> =
                sources::git(&self.root, &["log", "-1", "--full-history", "--format=%H %P", &at, "--", rel]).unwrap_or_default().split_whitespace().map(|s| s.to_string()).collect();
            let Some((commit, parents)) = last.split_first() else { break };
            found = Some((commit.clone(), parents.to_vec()));
            let Some(from) = self.made_from(kind, id, rel, parents, content) else { break };
            if at == rev && self.content_at(kind, id, rel, rev).as_deref() != Some(content) {
                found = None;
                break;
            }
            at = from;
        }
        self.made.borrow_mut().insert(key, found.clone());
        found
    }

    /// The parents of the merge that last changed a record's content (see made_by), or none when that was no merge.
    fn merge_parents(&self, kind: &str, id: &str, rel: &str, rev: &str, content: &str) -> Vec<String> {
        match self.made_by(kind, id, rel, rev, content) {
            Some((_, parents)) if parents.len() > 1 => parents,
            _ => Vec::new(),
        }
    }

    /// The parent a commit took a record's content from as it stands, when the commit did not make it: the only parent
    /// of a commit that changed only the file's text (a comment, a reformat), or a merge's parent holding that content
    /// whose every other parent left the record as it was where their histories meet (or, meeting nowhere this clone
    /// holds, never had it). A merge that kept one side's content over the other side's change made it: no parent.
    fn made_from(&self, kind: &str, id: &str, rel: &str, parents: &[String], content: &str) -> Option<String> {
        let p = parents.iter().find(|p| self.content_at(kind, id, rel, p).as_deref() == Some(content))?;
        let untouched = parents.iter().filter(|q| *q != p).all(|q| {
            let base = self.merge_base(p, q).unwrap_or_default();
            self.content_at(kind, id, rel, q) == self.content_at(kind, id, rel, &base)
        });
        untouched.then(|| p.clone())
    }

    /// Where two commits' histories meet (git merge-base), or None when the clone holds no commit they share.
    fn merge_base(&self, a: &str, b: &str) -> Option<String> {
        let key = if a <= b { (a.to_string(), b.to_string()) } else { (b.to_string(), a.to_string()) };
        if let Some(hit) = self.bases.borrow().get(&key) {
            return hit.clone();
        }
        let base = sources::git(&self.root, &["merge-base", a, b]).map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        self.bases.borrow_mut().insert(key, base.clone());
        base
    }

    /// The hash of the record's content as its file held it at a commit; None when it did not.
    fn content_at(&self, kind: &str, id: &str, rel: &str, rev: &str) -> Option<String> {
        self.version_at(kind, id, rel, rev).map(|f| rbyaml::content_sha(&Value::Dict(f)).unwrap_or_default())
    }

    /// The record as its file held it at a commit; None when it did not.
    fn version_at(&self, kind: &str, id: &str, rel: &str, rev: &str) -> Option<Dict> {
        if rev.is_empty() {
            return None;
        }
        let vkey = (rel.to_string(), rev.to_string());
        if !self.versions.borrow().contains_key(&vkey) {
            let data = sources::git(&self.root, &["show", &format!("{rev}:./{rel}")]).and_then(|blob| rbyaml::loads(&blob, rel).ok());
            self.versions.borrow_mut().insert(vkey.clone(), data);
        }
        let data = self.versions.borrow().get(&vkey).cloned().flatten()?;
        let path = self.root.join(rel);
        entries(kind, &data, rel, Self::path_id(kind, &path).as_deref(), Self::path_experiment(kind, &path).as_deref())
            .ok()?
            .into_iter()
            .find(|(i, _)| i == id || kind == "project")
            .map(|(_, f)| f)
    }

    /// The record as it stood at a commit, when that is rb's: a version rb wrote (on any branch: the log merges line by
    /// line), or the file a merge of two such made by keeping both sides; with every merge it rests on.
    fn rb_side(&self, kind: &str, id: &str, rel: &str, rev: &str, depth: usize) -> Res<Option<(Dict, Vec<Merged>)>> {
        let key = (kind.to_string(), id.to_string(), rel.to_string(), rev.to_string());
        if let Some(hit) = self.sides.borrow().get(&key) {
            return Ok(hit.clone());
        }
        let side = self.rb_side_now(kind, id, rel, rev, depth)?;
        self.sides.borrow_mut().insert(key, side.clone());
        Ok(side)
    }

    fn rb_side_now(&self, kind: &str, id: &str, rel: &str, rev: &str, depth: usize) -> Res<Option<(Dict, Vec<Merged>)>> {
        let Some(form) = self.version_at(kind, id, rel, rev) else { return Ok(None) };
        let sha = rbyaml::content_sha(&Value::Dict(form.clone())).unwrap_or_default();
        if !sha.is_empty() && self.logged_writes()?.get(&(kind.to_string(), id.to_string())).is_some_and(|w| w.contains(&sha)) {
            return Ok(Some((form, Vec::new())));
        }
        let parents = if depth < 32 { self.merge_parents(kind, id, rel, rev, &sha) } else { Vec::new() };
        if parents.len() < 2 {
            return Ok(None);
        }
        let a = self.rb_side(kind, id, rel, &parents[0], depth + 1)?;
        let b = self.rb_side(kind, id, rel, &parents[1], depth + 1)?;
        let (Some((a, mut rests_on)), Some((b, more))) = (a, b) else { return Ok(None) };
        let base_rev = self.merge_base(&parents[0], &parents[1]).unwrap_or_default();
        let base = self.version_at(kind, id, rel, &base_rev);
        if !both_kept(base.as_ref(), &a, &b, &form) {
            return Ok(None);
        }
        rests_on.extend(more);
        rests_on.insert(0, Merged { form: form.clone(), base, ours: a, theirs: b });
        Ok(Some((form, rests_on)))
    }

    fn merge_for(&self, kind: &str, id: &str) -> Option<Rc<MergeInfo>> {
        self.merges.borrow().iter().find(|((k, i, _), m)| k == kind && i == id && m.is_some()).and_then(|(_, m)| m.clone())
    }

    /// Whether a record is not what rb last wrote.
    pub fn outside_rb(&self, kind: &str, id: &str) -> Res<bool> {
        let tam = self.tampered()?;
        let stale = match self.tamper_keys.borrow().as_ref() {
            Some((t, _)) => !Rc::ptr_eq(t, &tam),
            None => true,
        };
        if stale {
            let keys: HashSet<Key> = tam.iter().map(|t| (t.get_str("kind").unwrap_or("").to_string(), t.get_str("id").unwrap_or("").to_string())).collect();
            *self.tamper_keys.borrow_mut() = Some((tam.clone(), Rc::new(keys)));
        }
        let keys = self.tamper_keys.borrow().as_ref().unwrap().1.clone();
        Ok(keys.contains(&(kind.to_string(), id.to_string())))
    }

    /// Every record whose file is not what rb last wrote.
    pub fn tampered(&self) -> Res<Rc<Vec<Dict>>> {
        if let Some(t) = self.tamper.borrow().as_ref() {
            return Ok(t.clone());
        }
        let writes = self.last_writes()?;
        let idx = self.where_()?;
        let mut seen: Vec<Key> = writes.keys().cloned().chain(idx.keys().cloned()).collect();
        seen.sort();
        seen.dedup();
        let mut out: Vec<Dict> = Vec::new();
        for (kind, id) in seen {
            if let Some(why) = self.edited_outside(&kind, &id)? {
                let found = if kind == "project" { Some(self.dir.join(ROOT_FILE)) } else { idx.get(&(kind.clone(), id.clone())).cloned() };
                let entry = writes.get(&(kind.clone(), id.clone()));
                let path = match found.filter(|f| crate::dry::exists(f)) {
                    Some(f) => self.rel(&f),
                    None => match entry.and_then(|e| e.get("path")).filter(|v| v.truthy()) {
                        Some(p) => pyjson::py_str(p),
                        None => self.rel(&self.path(&kind, &id, None)?),
                    },
                };
                let shallow = self.shallow_cut(&kind, &id)?;
                let merged = !shallow && self.merge_for(&kind, &id).is_some();
                let mut t = crate::dict! {"kind" => kind, "id" => id, "path" => path, "why" => why};
                if shallow {
                    t.insert("shallow".into(), Value::Bool(true));
                } else if merged {
                    t.insert("merge".into(), Value::Bool(true));
                }
                out.push(t);
            }
        }
        let strays = self.strays.borrow().clone();
        for st in strays.iter() {
            let p = st.get_str("path").unwrap_or("").to_string();
            let (k, i, kept) = (st.get_str("kind").unwrap_or(""), st.get_str("id").unwrap_or(""), st.get_str("kept").unwrap_or(""));
            if self.rb_wrote(k, i, &p)? {
                out.push(crate::dict! {
                    "kind" => k, "id" => i, "path" => p.clone(), "stray" => true, "collision" => true, "kept" => kept,
                    "why" => format!("{p} and {kept} both hold {k} {i}, each as rb wrote it: two branches gave the same id to two records")
                });
                continue;
            }
            out.push(crate::dict! {
                "kind" => st.get("kind").cloned().unwrap_or(Value::Null), "id" => st.get("id").cloned().unwrap_or(Value::Null),
                "path" => p.clone(), "stray" => true,
                "why" => format!("{p} holds a second copy of {} {}, which rb keeps in {}: it was written outside rb",
                    st.get_str("kind").unwrap_or(""), st.get_str("id").unwrap_or(""), st.get_str("kept").unwrap_or(""))
            });
        }
        if let Some(rewritten) = self.log_rewritten() {
            out.push(crate::dict! {"kind" => "log", "id" => "log.jsonl", "path" => self.rel(&self.dir.join("log.jsonl")), "why" => rewritten});
        }
        let rc = Rc::new(out);
        *self.tamper.borrow_mut() = Some(rc.clone());
        Ok(rc)
    }

    /// Whether rb wrote this record into this file as it stands: some line of the log (on either side of a merge) wrote
    /// it there with this content.
    fn rb_wrote(&self, kind: &str, id: &str, rel: &str) -> Res<bool> {
        let form = match self.in_(kind, &self.root.join(rel)) {
            Ok(items) => items.iter().find(|(i, _)| i == id).map(|(_, f)| f.clone()),
            Err(_) => None,
        };
        let Some(form) = form else { return Ok(false) };
        let sha = rbyaml::content_sha(&Value::Dict(form)).unwrap_or_default();
        for r in self.log_entries()?.iter() {
            for ((k, i), w) in writes_in(r) {
                if k == kind && i == id && w.get_str("path") == Some(rel) && w.get_str("sha256") == Some(sha.as_str()) {
                    return Ok(true);
                }
            }
        }
        Ok(false)
    }

    /// Give the second of two records that branches minted with one id an id of its own: its file is written again under
    /// the new id beside where it was, and the log says which id it had.
    fn reid(&self, who: &Actor, kind: &str, id: &str, rel: &str) -> Res<String> {
        let path = self.root.join(rel);
        let mut form = self
            .in_(kind, &path)
            .map_err(|e| RBError::msg("E_STATE_CORRUPT", e.text().to_string()))?
            .iter()
            .find(|(i, _)| i == id)
            .map(|(_, f)| f.clone())
            .ok_or_else(|| RBError::msg("E_OBJECT_NOT_FOUND", format!("No {kind} {} in {rel}.", repr_str(id))))?;
        let new = self.new_id(kind, None)?;
        form.insert("id".into(), Value::Str(new.clone()));
        let owner = if NESTED.contains(&kind) { form.get_str("experiment").map(|s| s.to_string()) } else { Self::path_experiment(kind, &path) };
        let target = self.path(kind, &new, owner.as_deref())?;
        let form = ordered(Self::meta_of(kind), &form);
        let sha = rbyaml::content_sha(&Value::Dict(form.clone())).unwrap_or_default();
        self.put(kind, &new, &target, form.clone())?;
        self.stash(&form, &sha);
        let _ = std::fs::remove_file(&path);
        *self.index.borrow_mut() = None;
        self.log(who, "reid", kind, &new, Some(&sha), Some(&self.rel(&target)), crate::dict! {"was" => id, "moved_from" => rel})?;
        Ok(new)
    }

    /// The log only grows: every line of it as last committed must still be in it.
    fn log_rewritten(&self) -> Option<String> {
        let shown = sources::git_bytes(&self.root, &["show", &format!("HEAD:./{DIR}/log.jsonl")])?;
        let committed_text = String::from_utf8_lossy(&shown).into_owned();
        let committed: HashSet<&str> = py_splitlines(&committed_text).into_iter().filter(|l| !crate::rbyaml::py_strip(l).is_empty()).collect();
        let p = self.dir.join("log.jsonl");
        let current_text = crate::dry::read(&p).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default();
        let current: HashSet<&str> = py_splitlines(&current_text).into_iter().collect();
        let missing = committed.iter().filter(|l| !current.contains(*l)).count();
        if missing > 0 {
            let (lines, are) = if missing == 1 { ("line", "is") } else { ("lines", "are") };
            Some(format!("{missing} {lines} of log.jsonl as last committed {are} gone or changed: the log was rewritten"))
        } else {
            None
        }
    }

    /// Remove one criterion from a criteria file, keeping it beside the file in <file>.outside-rb.
    fn take_out(&self, path: &Path, id: &str) -> Res<String> {
        let doc = self.doc(path).map_err(|e| RBError::msg("E_STATE_CORRUPT", e.text().to_string()))?;
        let exp = path.parent().map(name_of);
        let items = entries("criterion", &doc.data, &self.rel(path), None, exp.as_deref()).map_err(|e| RBError::msg("E_STATE_CORRUPT", e.text().to_string()))?;
        let out: Vec<Value> = items.iter().filter(|(i, _)| i != id).map(|(_, f)| Value::Dict(f.clone())).collect();
        let taken: Vec<Value> = items.iter().filter(|(i, _)| i == id).map(|(_, f)| Value::Dict(f.clone())).collect();
        let aside = path.with_file_name(format!("{}.outside-rb", name_of(path)));
        {
            use std::io::Write;
            let text = rbyaml::dumps(&crate::obj! {"taken_out" => now(), "criteria" => Value::List(taken)}, None).unwrap_or_default();
            if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&aside) {
                let _ = f.write_all(text.as_bytes());
            }
        }
        let data = crate::obj! {FORMAT_KEY => RB_FORMAT, "kind" => "criteria", "experiment" => exp.unwrap_or_default(), "criteria" => Value::List(out)};
        self.write(path, &rbyaml::dumps(&data, Some(&doc.comments)).unwrap_or_default())?;
        Ok(self.rel(&aside))
    }

    /// Write an unreadable criteria file again from rb's copies of every criterion rb last wrote there.
    fn rebuild_criteria(&self, rel: &str) -> Res<Vec<String>> {
        let mut forms: Vec<Dict> = Vec::new();
        let mut missing: Vec<String> = Vec::new();
        let writes = self.last_writes()?;
        for ((k, i), e) in writes.iter() {
            if k == "criterion" && e.get_str("path") == Some(rel) && e.get("sha256").map_or(false, |v| v.truthy()) {
                match self.stored_form(k, i, e.get_str("sha256").unwrap_or(""), Some(rel))? {
                    Some(f) => forms.push(f),
                    None => missing.push(i.clone()),
                }
            }
        }
        let path = self.root.join(rel);
        if path.exists() {
            let _ = crate::sys::replace(&path, path.with_file_name(format!("{}.outside-rb", name_of(&path))));
        }
        forms.sort_by(|a, b| {
            (a.get("created_at").map(pyjson::py_str).unwrap_or_default(), a.get("id").map(pyjson::py_str).unwrap_or_default())
                .cmp(&(b.get("created_at").map(pyjson::py_str).unwrap_or_default(), b.get("id").map(pyjson::py_str).unwrap_or_default()))
        });
        let forms: Vec<Value> = forms.iter().map(|f| Value::Dict(ordered(&Criterion::META, f))).collect();
        let data = crate::obj! {FORMAT_KEY => RB_FORMAT, "kind" => "criteria", "experiment" => name_of(path.parent().unwrap()), "criteria" => Value::List(forms)};
        self.write(&path, &rbyaml::dumps(&data, None).unwrap_or_default())?;
        Ok(missing)
    }

    fn meta_of(kind: &str) -> &'static crate::model::ModelMeta {
        match kind {
            "project" => &Project::META,
            "question" => &Question::META,
            "experiment" => &Experiment::META,
            "metric" => &Metric::META,
            "criterion" => &Criterion::META,
            "run" => &Run::META,
            "analysis" => &Analysis::META,
            "resource" => &Resource::META,
            "variant" => &Variant::META,
            "note" => &Note::META,
            _ => &Decision::META,
        }
    }

    fn set_aside(&self, path: &Path) -> String {
        let aside = path.with_file_name(format!("{}.outside-rb", name_of(path)));
        let _ = crate::sys::replace(path, &aside);
        self.rel(&aside)
    }

    /// Put back what rb last wrote for every record changed outside rb.
    pub fn restore(&self) -> Res<Vec<Dict>> {
        let who = self.actor()?;
        self.locked(|| {
            let mut done: Vec<Dict> = Vec::new();
            let mut rebuilt: HashSet<String> = HashSet::new();
            let tampered = self.tampered()?;
            for t in tampered.iter() {
                let kind = t.get_str("kind").unwrap_or("").to_string();
                let id = t.get_str("id").unwrap_or("").to_string();
                let with_done = |msg: String| -> Dict {
                    let mut d = t.clone();
                    d.insert("done".into(), Value::Str(msg));
                    d
                };
                if kind == "log" {
                    done.push(with_done("left as it is: git diff shows what was rewritten, and git checkout puts back the committed log (entries written since the last commit are then lost)".into()));
                    continue;
                }
                if t.get("shallow").map_or(false, |v| v.truthy()) {
                    done.push(with_done(format!("left as it is: this clone is shallow, so rb cannot see whether a merge made it, and putting back rb's last write could drop the other side's; {UNSHALLOW} fetches the whole history")));
                    continue;
                }
                if t.get("collision").map_or(false, |v| v.truthy()) {
                    let new = self.reid(&who, &kind, &id, t.get_str("path").unwrap_or(""))?;
                    done.push(with_done(format!(
                        "given an id of its own, {new}; what named {id} on its branch now reads the record in {}",
                        t.get_str("kept").unwrap_or("")
                    )));
                    continue;
                }
                if t.get("stray").map_or(false, |v| v.truthy()) {
                    let tp = t.get_str("path").unwrap_or("").to_string();
                    let stray = self.root.join(&tp);
                    let aside = if kind == "criterion" && name_of(&stray) == CRITERIA_FILE && !self.unreadable.borrow().contains_key(&tp) {
                        self.take_out(&stray, &id)?
                    } else {
                        let _ = crate::sys::replace(&stray, stray.with_file_name(format!("{}.outside-rb", name_of(&stray))));
                        format!("{tp}.outside-rb")
                    };
                    *self.index.borrow_mut() = None;
                    self.log(&who, "restore", &kind, &id, None, None, crate::dict! {"moved_aside" => aside.clone()})?;
                    done.push(with_done(format!("the second copy set aside in {aside}")));
                    continue;
                }
                let entry = self.last_writes()?.get(&(kind.clone(), id.clone())).cloned();
                let current = if kind == "project" { Some(self.dir.join(ROOT_FILE)) } else { self.where_()?.get(&(kind.clone(), id.clone())).cloned() };
                if entry.is_none() || entry.as_ref().and_then(|e| e.get_str("op")) == Some("adopt_delete") {
                    let aside = if kind == "criterion" && current.as_ref().map_or(false, |c| name_of(c) == CRITERIA_FILE && !self.unreadable.borrow().contains_key(&self.rel(c))) {
                        self.take_out(current.as_ref().unwrap(), &id)?
                    } else {
                        let path = current.clone().unwrap_or_else(|| self.root.join(t.get_str("path").unwrap_or("")));
                        self.set_aside(&path)
                    };
                    *self.index.borrow_mut() = None;
                    self.log(&who, "restore", &kind, &id, None, None, crate::dict! {"moved_aside" => aside.clone()})?;
                    done.push(with_done(format!("set aside in {aside}")));
                    continue;
                }
                let entry = entry.unwrap();
                let logged = match entry.get("path").filter(|v| v.truthy()) {
                    Some(p) => pyjson::py_str(p),
                    None => self.rel(&self.path(&kind, &id, None)?),
                };
                let sha = entry.get_str("sha256").unwrap_or("").to_string();
                if kind == "criterion" && logged.ends_with(&format!("/{CRITERIA_FILE}")) && (rebuilt.contains(&logged) || self.unreadable.borrow().contains_key(&logged)) {
                    if !rebuilt.contains(&logged) {
                        let missing = self.rebuild_criteria(&logged)?;
                        rebuilt.insert(logged.clone());
                        *self.index.borrow_mut() = None;
                        if !missing.is_empty() {
                            done.push(with_done(format!("rebuilt {logged}; no copy of {} here or in git history", missing.join(", "))));
                        }
                    }
                    self.log(&who, "restore", &kind, &id, Some(&sha), Some(&logged), Dict::new())?;
                    done.push(with_done(format!("restored: {logged} rebuilt from what rb last wrote")));
                    continue;
                }
                let Some(form) = self.stored_form(&kind, &id, &sha, Some(&logged))? else {
                    done.push(with_done(if self.checkout().shallow.is_empty() {
                        "left as it is: there is no copy of rb's last write here or in git history".into()
                    } else {
                        format!("left as it is: there is no copy of rb's last write here or in the history this shallow clone holds ({UNSHALLOW} fetches the rest)")
                    }));
                    continue;
                };
                let target = self.root.join(&logged);
                if let Some(c) = &current {
                    if *c != target && c.exists() {
                        if kind == "criterion" && name_of(c) == CRITERIA_FILE && !self.unreadable.borrow().contains_key(&self.rel(c)) {
                            self.take_out(c, &id)?;
                        } else {
                            self.set_aside(c);
                        }
                    }
                }
                if (kind != "criterion" || name_of(&target) != CRITERIA_FILE) && self.unreadable.borrow().contains_key(&self.rel(&target)) && target.exists() {
                    self.set_aside(&target);
                }
                *self.index.borrow_mut() = None;
                self.put(&kind, &id, &target, ordered(Self::meta_of(&kind), &form))?;
                self.log(&who, "restore", &kind, &id, Some(&sha), Some(&logged), Dict::new())?;
                done.push(with_done("restored to what rb last wrote".into()));
            }
            Ok(done)
        })
    }

    /// Accept every change made outside rb as it stands, attributed to whoever adopts them and why.
    pub fn adopt(&self, why: &str, seen: Option<&Dict>) -> Res<Vec<Dict>> {
        let who = self.actor()?;
        self.locked(|| {
            let mut done: Vec<Dict> = Vec::new();
            let tampered = self.tampered()?;
            if let Some(seen) = seen {
                let cut = |t: &&Dict| t.get("shallow").map_or(false, |v| v.truthy());
                let left: HashSet<&str> = tampered.iter().filter(cut).filter_map(|t| t.get_str("path")).collect();
                let mut current: IndexMap<String, String> = IndexMap::new();
                for t in tampered.iter().filter(|t| t.get_str("kind") != Some("log") && !cut(t)) {
                    let p = t.get_str("path").unwrap_or("").to_string();
                    let f = self.root.join(&p);
                    let sha = if f.exists() { std::fs::read(&f).map(|b| sha256_bytes(&b)).unwrap_or_default() } else { "deleted".into() };
                    current.insert(p, sha);
                }
                let mut drifted: Vec<String> = Vec::new();
                for (path, sha) in &current {
                    if let Some(v) = seen.get(path) {
                        if !v.is_null() && v.as_str() != Some(sha.as_str()) {
                            drifted.push(format!("{path} changed after the review"));
                        }
                    }
                }
                for path in current.keys() {
                    if !seen.contains_key(path) {
                        drifted.push(format!("{path} appeared after the review"));
                    }
                }
                for path in seen.keys() {
                    if !current.contains_key(path) && !left.contains(path.as_str()) {
                        drifted.push(format!("{path} is no longer changed outside rb"));
                    }
                }
                if !drifted.is_empty() {
                    drifted.sort();
                    return Err(RBError::msg("E_STALE_REVIEW", "The files are not what was reviewed.").with_problems(drifted));
                }
            }
            let strays: Vec<&Dict> = tampered.iter().filter(|t| t.get("stray").map_or(false, |v| v.truthy())).collect();
            if let Some(first) = strays.first() {
                return Err(RBError::msg_fix(
                    "E_ADOPT_AUTHORITY",
                    format!("{}; a second copy of a record cannot be adopted.", first.get_str("why").unwrap_or("")),
                    "rb doctor --restore sets it aside; then adopt what remains, if anything.",
                )
                .with_problems(strays.iter().map(|t| t.get_str("why").unwrap_or("").to_string()).collect()));
            }
            let mut unreadable: Vec<String> = tampered.iter().filter_map(|t| t.get_str("path")).filter(|p| self.unreadable.borrow().contains_key(*p)).map(|s| s.to_string()).collect();
            unreadable.sort();
            unreadable.dedup();
            if let Some(u) = unreadable.first() {
                let why_u = self.unreadable.borrow().get(u).cloned().unwrap_or_default();
                return Err(RBError::msg_fix(
                    "E_STATE_CORRUPT",
                    format!("{u} cannot be read, so it cannot be adopted: {why_u}"),
                    "Fix the file so it reads (rb fmt shows where it breaks), or rb doctor --restore puts back what rb last wrote.",
                ));
            }
            // each design whose files this adopts, file by file as it was before the edits, where rb is certain of that, and
            // its gate's words before: the line that adopts the last of them keeps the design the adoption makes, where the
            // user owned the one before
            let words_before = self.gate_words()?;
            let mut adopting: IndexMap<String, Vec<design::Before>> = IndexMap::new();
            for t in tampered.iter().filter(|t| who.is_human() && !t.get("shallow").map_or(false, |v| v.truthy())) {
                let (kind, id) = (t.get_str("kind").unwrap_or(""), t.get_str("id").unwrap_or(""));
                if !matches!(kind, "experiment" | "variant" | "criterion") {
                    continue;
                }
                let before = self.form_before_adopting(kind, id)?;
                let key = (kind.to_string(), id.to_string());
                let now = match self.where_()?.get(&key).filter(|f| f.exists()).cloned() {
                    Some(f) => self.form(kind, id).ok().and_then(|form| design::file_experiment(kind, id, &form)).or_else(|| Self::path_experiment(kind, &f)),
                    None => None,
                };
                let was = self.forms_before(kind, id)?.iter().filter_map(|f| design::file_experiment(kind, id, f)).collect::<Vec<_>>();
                // a file is of the design it was of and of the one it is of now: each reads it as it was there
                for x in was.into_iter().chain(now) {
                    let files = adopting.entry(x.clone()).or_default();
                    if !files.iter().any(|(k, i, _)| *k == kind && *i == id) {
                        let there = before.clone().map(|f| f.filter(|f| design::file_experiment(kind, id, f).map_or(true, |e| e == x)));
                        files.push((kind.to_string(), id.to_string(), there));
                    }
                }
            }
            let kept_by = |kind: &str, id: &str, gate_set: bool| -> Res<Option<Value>> {
                let mut out = Dict::new();
                for (x, files) in adopting.iter().filter(|(_, files)| files.last().map_or(false, |(k, i, _)| k == kind && i == id)) {
                    if let Some(Value::Dict(kept)) = self.owned_by_adopting(&who, x, files, words_before.get(x).map_or("", |(w, _)| w.as_str()), gate_set)? {
                        out.extend(kept);
                    }
                }
                Ok((!out.is_empty()).then(|| Value::Dict(out)))
            };
            for t in tampered.iter() {
                let kind = t.get_str("kind").unwrap_or("").to_string();
                let id = t.get_str("id").unwrap_or("").to_string();
                let with_done = |msg: &str| -> Dict {
                    let mut d = t.clone();
                    d.insert("done".into(), Value::Str(msg.into()));
                    d
                };
                if kind == "log" {
                    done.push(with_done("left as it is: a rewritten log is kept by committing it, where git keeps what it replaced"));
                    continue;
                }
                if t.get("shallow").map_or(false, |v| v.truthy()) {
                    done.push(with_done(&format!("left as it is: this clone is shallow, so rb cannot see how it got there; {UNSHALLOW} fetches the whole history")));
                    continue;
                }
                let found = if kind == "project" { Some(self.dir.join(ROOT_FILE)) } else { self.where_()?.get(&(kind.clone(), id.clone())).cloned() };
                if found.as_ref().map_or(true, |f| !f.exists()) {
                    let mut detail = crate::dict! {"why" => why};
                    // what the deletion took of an experiment's design, as its line keeps it
                    let before = self.forms_before(&kind, &id)?;
                    if matches!(kind.as_str(), "experiment" | "variant" | "criterion") && !before.is_empty() {
                        if let Some(taken) = design::adoption_changes(&kind, &id, &before, None, found.as_ref().and_then(|f| Self::path_experiment(&kind, f)).as_deref(), who.is_human()).filter(|t| !t.is_empty()) {
                            design::keep_changed(&mut detail, taken, &who);
                        }
                    }
                    if let Some(kept) = kept_by(&kind, &id, false)? {
                        detail.insert("owned".into(), kept);
                    }
                    self.log(&who, "adopt_delete", &kind, &id, None, None, detail)?;
                    done.push(with_done("deletion adopted"));
                    continue;
                }
                let found = found.unwrap();
                if kind == "project" {
                    self.project()?;
                } else if is_part(&kind) {
                    self.check_part(&kind, &id)?;
                } else {
                    self.load_obj(&kind, &id)?;
                }
                let form = self.form(&kind, &id)?;
                let changes = self.authority_changes(&kind, &id, &form)?;
                if !changes.is_empty() {
                    return Err(RBError::msg(
                        "E_ADOPT_AUTHORITY",
                        format!("{} changes who wrote or decided something; an adoption cannot make that true.", t.get_str("path").unwrap_or("")),
                    )
                    .with_problems(changes));
                }
                let before = self.forms_before(&kind, &id)?;
                let retold = voice_changes(&before, &form, &who.id, &id);
                if !retold.is_empty() {
                    return Err(RBError::msg_fix(
                        "E_ADOPT_AUTHORITY",
                        format!("{} changes the words of a note someone else wrote; an adoption cannot make them that writer's.", t.get_str("path").unwrap_or("")),
                        "rb doctor --restore puts back what rb last wrote; add your own note with rb note add.",
                    )
                    .with_problems(retold));
                }
                let sha = rbyaml::content_sha(&Value::Dict(form.clone())).unwrap_or_default();
                self.stash(&form, &sha);
                let mut detail = crate::dict! {"why" => why};
                // a gate the adoption changed is the adopter's, set with no words, as rb experiment gate sets one
                if kind == "experiment" && before.iter().all(|b| gate_of(b) != gate_of(&form)) && (!before.is_empty() || gate_of(&form) != (String::new(), String::new())) {
                    let (if_holds, if_not) = gate_of(&form);
                    detail.insert("if_holds".into(), Value::Str(if_holds));
                    detail.insert("if_not".into(), Value::Str(if_not));
                }
                // what the adoption changed of an experiment's design, as its line keeps it: never guessed where rb wrote the
                // record and cannot read what it wrote
                let wrote = self.last_writes()?.get(&(kind.clone(), id.clone())).map_or(false, |w| w.get("sha256").map_or(false, |v| v.truthy()));
                let exp = design::file_experiment(&kind, &id, &form).or_else(|| Self::path_experiment(&kind, &found));
                if let (Some(exp), false) = (exp, before.is_empty() && wrote) {
                    if let Some(ch) = design::adoption_changes(&kind, &id, &before, Some((&exp, &form)), None, who.is_human()) {
                        design::keep_changed(&mut detail, ch, &who);
                    }
                }
                if let Some(kept) = kept_by(&kind, &id, detail.contains_key("if_holds"))? {
                    detail.insert("owned".into(), kept);
                }
                self.log(&who, "adopt", &kind, &id, Some(&sha), Some(&self.rel(&found)), detail)?;
                done.push(with_done("adopted as it stands"));
            }
            Ok(done)
        })
    }

    /// The record as rb last wrote it: each side of a merge git made of two rb wrote, or none for a record rb never wrote.
    fn forms_before(&self, kind: &str, id: &str) -> Res<Vec<Dict>> {
        if let Some(m) = self.merge_for(kind, id) {
            return Ok(m.sides.clone());
        }
        let entry = self.last_writes()?.get(&(kind.to_string(), id.to_string())).cloned();
        Ok(match &entry {
            Some(e) if e.get("sha256").map_or(false, |v| v.truthy()) => self.stored_form(kind, id, e.get_str("sha256").unwrap_or(""), None)?.into_iter().collect(),
            _ => Vec::new(),
        })
    }

    /// The form a record's file held before the edits an adoption takes, where rb is certain of it: what rb last wrote of
    /// it, where the file came to stand from that form through commits of no other form rb wrote and no merge, or rb wrote
    /// it here since the last commit; no file, where no line wrote it or the line adopted its deletion and the file came
    /// from none that way. None where rb cannot be certain: a merge or another git operation under way, a form no copy
    /// of rb's holds, a file git merged, or a write the times of the lines put last that the file did not come from.
    pub(crate) fn form_before_adopting(&self, kind: &str, id: &str) -> Res<Option<Option<Dict>>> {
        if self.checkout().busy || self.merge_for(kind, id).is_some() {
            return Ok(None);
        }
        let key = (kind.to_string(), id.to_string());
        let entry = self.last_writes()?.get(&key).cloned();
        let sha = entry.as_ref().and_then(|e| e.get_str("sha256")).filter(|s| !s.is_empty()).map(str::to_string);
        let form = match &sha {
            Some(s) => match self.stored_form(kind, id, s, None)? {
                Some(f) => Some(f),
                None => return Ok(None),
            },
            None => None,
        };
        let rel = match entry.as_ref().and_then(|e| e.get_str("path")).filter(|p| !p.is_empty()) {
            Some(p) => p.to_string(),
            None => match self.where_()?.get(&key) {
                Some(p) => self.rel(p),
                None => self.rel(&self.path(kind, id, None)?),
            },
        };
        let logged = self.logged_writes()?.get(&key).cloned().unwrap_or_default();
        let mut rev = "HEAD".to_string();
        for _ in 0..32 {
            let held = self.content_at(kind, id, &rel, &rev);
            if held == sha {
                return self.nothing_brought_in(kind, id, &rel, &rev, sha.as_deref(), form);
            }
            match held {
                Some(h) if !logged.contains(&h) => {
                    // a form no line wrote, committed: it came from what the commit that made it was made on
                    let last: Vec<String> = sources::git(&self.root, &["log", "-1", "--full-history", "--format=%H %P", &rev, "--", &rel]).unwrap_or_default().split_whitespace().map(str::to_string).collect();
                    match last.as_slice() {
                        [_, parent] => rev = parent.clone(),
                        _ => break,
                    }
                }
                _ => break,
            }
        }
        // rb wrote it here since the last commit: no commit holds that form
        match &sha {
            Some(s) if !self.committed_form(kind, id, &rel, s) => self.nothing_brought_in(kind, id, &rel, "HEAD", sha.as_deref(), form),
            _ => Ok(None),
        }
    }

    /// The form before an adoption's edits (`form`, rb's last write of the record, `last`), where no session's line
    /// brought in beside it says otherwise. The lines the log held where the file held that form (`base`, the commit
    /// the file came from it at, or the last commit where rb wrote it here since) came before it; a session's line not
    /// among them that wrote another form of the record came beside it or later, by a merge, a cherry-pick, a squash, a
    /// stash or a patch, committed or not. Where the file now holds a value that line's form holds and the last write
    /// does not, or a form rb cannot find, rb cannot say what the edit was: none.
    fn nothing_brought_in(&self, kind: &str, id: &str, rel: &str, base: &str, last: Option<&str>, form: Option<Dict>) -> Res<Option<Option<Dict>>> {
        let before: HashSet<String> = sources::git(&self.root, &["show", &format!("{base}:./{DIR}/log.jsonl")])
            .unwrap_or_default()
            .lines()
            .filter_map(|l| match pyjson::loads(l) {
                Ok(Value::Dict(row)) => Some(line_digest(&row)),
                _ => None,
            })
            .collect();
        let now = self.where_()?.contains_key(&(kind.to_string(), id.to_string())).then(|| self.form(kind, id).ok()).flatten();
        let leaves = |f: Option<&Dict>| f.map(|f| form_leaves(&Value::Dict(f.clone()), "")).unwrap_or_default();
        let (now, was) = (leaves(now.as_ref()), leaves(form.as_ref()));
        let mut moved: Vec<&String> = now.keys().chain(was.keys()).filter(|p| now.get(*p) != was.get(*p)).collect();
        moved.sort();
        moved.dedup();
        let rows = self.log_entries()?;
        let session = |r: &Dict| r.get_str("actor").map_or(false, |a| !a.is_empty() && !crate::view::words_are_theirs(a));
        let beside = rows.iter().filter(|r| session(r) && !before.contains(&line_digest(r)));
        for row in beside {
            for (_, w) in writes_in(row).into_iter().filter(|((k, i), _)| k == kind && i == id) {
                let sha = w.get_str("sha256").unwrap_or("");
                if sha.is_empty() || Some(sha) == last {
                    continue;
                }
                let Some(other) = self.stored_form(kind, id, sha, Some(rel))? else { return Ok(None) };
                let other = leaves(Some(&other));
                if moved.iter().any(|p| other.get(*p) == now.get(*p)) {
                    return Ok(None);
                }
            }
        }
        Ok(Some(form))
    }

    /// Whether a commit this checkout's history holds has this form of a record.
    fn committed_form(&self, kind: &str, id: &str, rel: &str, sha: &str) -> bool {
        let commits = sources::git(&self.root, &["log", "--full-history", "--format=%H", "--", rel]).unwrap_or_default();
        commits.split_whitespace().any(|c| self.content_at(kind, id, rel, c).as_deref() == Some(sha))
    }

    /// How a record, as it stands, differs from rb's record in who wrote or decided what.
    fn authority_changes(&self, kind: &str, id: &str, new: &Dict) -> Res<Vec<String>> {
        if let Some(m) = self.merge_for(kind, id) {
            return Ok(self.authority_changes_in_merge(kind, id, new, &m.sides));
        }
        self.authority_changes_from(kind, id, new)
    }

    /// A record git merged from two sides rb wrote: each authority value as it stands must be one rb wrote on a side.
    fn authority_changes_in_merge(&self, kind: &str, id: &str, new: &Dict, sides: &[Dict]) -> Vec<String> {
        let newv = Value::Dict(new.clone());
        if kind == "decision" {
            return if sides.iter().any(|s| canon_json(&Value::Dict(s.clone())) == canon_json(&newv)) {
                Vec::new()
            } else {
                vec![format!("{id}: a decision cannot be edited outside rb and adopted; every field of it is who decided what")]
            };
        }
        let before: Vec<IndexMap<String, String>> = sides.iter().map(|s| authority_values(&Value::Dict(s.clone()), "")).collect();
        let after = authority_values(&newv, "");
        let mut paths: Vec<&String> = after.keys().chain(before.iter().flat_map(|b| b.keys())).collect();
        paths.sort();
        paths.dedup();
        let mut out = Vec::new();
        for p in paths {
            let b = after.get(p);
            if before.iter().all(|side| side.get(p) != b) {
                let sides: Option<Vec<String>> = before.iter().map(|side| shown_authority(p, side.get(p))).collect();
                let Some((sides, now)) = sides.zip(shown_authority(p, b)) else {
                    out.push(format!("{id}.{p}: not as rb wrote it on either side"));
                    continue;
                };
                let mut alts: Vec<String> = Vec::new();
                for r in sides {
                    if !alts.contains(&r) {
                        alts.push(r);
                    }
                }
                out.push(format!("{id}.{p}: {} as rb wrote it on the two sides, {now} as it stands", alts.join(" or ")));
            }
        }
        out
    }

    fn authority_changes_from(&self, kind: &str, id: &str, new: &Dict) -> Res<Vec<String>> {
        let entry = self.last_writes()?.get(&(kind.to_string(), id.to_string())).cloned();
        let old = match &entry {
            Some(e) if e.get("sha256").map_or(false, |v| v.truthy()) => self.stored_form(kind, id, e.get_str("sha256").unwrap_or(""), None)?,
            _ => None,
        };
        let newv = Value::Dict(new.clone());
        let Some(old) = old else {
            if kind == "decision" {
                return Ok(vec![format!("{id} is a decision rb never wrote: a decision is recorded by whoever makes it, not adopted")]);
            }
            let mut claimed: Vec<String> = authority_values(&newv, "")
                .into_iter()
                .filter(|(p, v)| claims_authority(p, v))
                .map(|(p, v)| match shown_authority(&p, Some(&v)) {
                    Some(r) => format!("{p} = {r}"),
                    None => p,
                })
                .collect();
            claimed.sort();
            claimed.dedup();
            return Ok(claimed.into_iter().map(|c| format!("{id} was not written by rb, and as it stands it asserts: {c}")).collect());
        };
        let oldv = Value::Dict(old);
        if kind == "decision" {
            return Ok(if canon_json(&oldv) == canon_json(&newv) {
                Vec::new()
            } else {
                vec![format!("{id}: a decision cannot be edited outside rb and adopted; every field of it is who decided what")]
            });
        }
        let before = authority_values(&oldv, "");
        let after = authority_values(&newv, "");
        let mut paths: Vec<&String> = before.keys().chain(after.keys()).collect();
        paths.sort();
        paths.dedup();
        let mut out = Vec::new();
        for p in paths {
            let a = before.get(p);
            let b = after.get(p);
            if a != b {
                out.push(match (shown_authority(p, a), shown_authority(p, b)) {
                    (Some(ra), Some(rb_)) => format!("{id}.{p}: {ra} as rb wrote it, {rb_} as it stands"),
                    _ => format!("{id}.{p}: changed since rb wrote it"),
                });
            }
        }
        Ok(out)
    }

    // ------------------------------------------------------------ reading records

    /// Every record of a kind, oldest first, read once per state of the record.
    pub fn all<T: FromObj>(&self) -> Res<Rc<Vec<T>>> {
        let idx = self.where_()?;
        let fresh = match self.objs_for.borrow().as_ref() {
            Some(i) => !Rc::ptr_eq(i, &idx),
            None => true,
        };
        if fresh {
            self.objs.borrow_mut().clear();
            self.loaded.borrow_mut().clear();
            *self.objs_for.borrow_mut() = Some(idx.clone());
            self.finding_cache.borrow_mut().clear();
        }
        if let Some(hit) = self.objs.borrow().get(T::KIND) {
            if let Ok(v) = hit.clone().downcast::<Vec<T>>() {
                return Ok(v);
            }
        }
        let mut items: Vec<T> = Vec::new();
        for (k, id) in idx.keys() {
            if k == T::KIND {
                items.push(self.load_t::<T>(id)?);
            }
        }
        items.sort_by(|a, b| (a.sort_time(), a.rec_id()).cmp(&(b.sort_time(), b.rec_id())));
        let rc = Rc::new(items);
        self.objs.borrow_mut().insert(T::KIND, rc.clone() as Rc<dyn Any>);
        Ok(rc)
    }

    pub fn live<T: FromObj>(&self) -> Res<Vec<T>> {
        Ok(self.all::<T>()?.iter().filter(|o| o.retraction().is_none()).cloned().collect())
    }

    /// Runs that still say something: everything not retracted whole. Read once per state of the record.
    pub fn standing_runs(&self) -> Res<Rc<Vec<Run>>> {
        let all = self.all::<Run>()?;
        if let Some(hit) = self.objs.borrow().get("standing runs") {
            if let Ok(v) = hit.clone().downcast::<Vec<Run>>() {
                return Ok(v);
            }
        }
        let v: Rc<Vec<Run>> = Rc::new(all.iter().filter(|r| r.retracted.as_ref().map_or(true, |x| !x.scope.is_empty())).cloned().collect());
        self.objs.borrow_mut().insert("standing runs", v.clone() as Rc<dyn Any>);
        Ok(v)
    }

    pub fn load<T: FromObj>(&self, id: &str) -> Res<T> {
        let kind = T::KIND;
        if !id_ok(id) {
            return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("No {kind} {} in this project.", repr_str(id))));
        }
        let key = (kind.to_string(), id.to_string());
        if !self.where_()?.contains_key(&key) {
            if self.newer_index.borrow().contains_key(&key) {
                self.form(kind, id)?;
            }
            return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("No {kind} {} in this project.", repr_str(id))));
        }
        self.load_t::<T>(id)
    }

    pub fn load_kind(&self, kind: &str, id: &str) -> Res<Obj> {
        if !id_ok(id) {
            return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("No {kind} {} in this project.", repr_str(id))));
        }
        let key = (kind.to_string(), id.to_string());
        if !self.where_()?.contains_key(&key) {
            if self.newer_index.borrow().contains_key(&key) {
                self.form(kind, id)?;
            }
            return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("No {kind} {} in this project.", repr_str(id))));
        }
        self.load_cached(kind, id)
    }

    pub fn kind_of(&self, id: &str) -> Res<Option<&'static str>> {
        if !id_ok(id) {
            return Ok(None);
        }
        let idx = self.where_()?;
        let newer = self.newer_index.borrow();
        for (kind, _, _) in KINDS {
            let key = (kind.to_string(), id.to_string());
            if idx.contains_key(&key) || newer.contains_key(&key) {
                return Ok(Some(kind));
            }
        }
        Ok(None)
    }

    /// Whether a record is here in a form only a newer rb reads (a later format, or a field this rb does not know):
    /// `rb status` names it as state_newer and fails every gate closed, so a reading leaves it out rather than stop.
    pub fn newer_only(&self, kind: &str, id: &str) -> bool {
        matches!(self.load_kind(kind, id), Err(e) if e.code == "E_STATE_NEWER")
    }

    pub fn get(&self, id: &str) -> Res<(&'static str, Obj)> {
        let kind = self.kind_of(id)?.ok_or_else(|| RBError::msg("E_OBJECT_NOT_FOUND", format!("Nothing called {} in this project.", repr_str(id))))?;
        Ok((kind, self.load_kind(kind, id)?))
    }

    /// A record by its global uid (an experiment, a run, an analysis, a resource), or None.
    pub fn find_uid(&self, uid: &str) -> Res<Option<Obj>> {
        for e in self.all::<Experiment>()?.iter() {
            if e.uid == uid {
                return Ok(Some(Obj::Experiment(e.clone())));
            }
        }
        for r in self.all::<Run>()?.iter() {
            if r.uid == uid {
                return Ok(Some(Obj::Run(r.clone())));
            }
        }
        for a in self.all::<Analysis>()?.iter() {
            if a.uid == uid {
                return Ok(Some(Obj::Analysis(a.clone())));
            }
        }
        for r in self.all::<Resource>()?.iter() {
            if r.uid == uid {
                return Ok(Some(Obj::Resource(r.clone())));
            }
        }
        Ok(None)
    }

    /// `e1/lr`, `e1/int8.lr` or `e1.lr` -> (experiment, full setting name, setting).
    pub fn resolve_setting(&self, address: &str) -> Res<(Experiment, String, Setting)> {
        let (mut exp_id, mut name) = parse_address(address);
        if name.is_none() && address.contains('.') {
            let (head, tail) = address.split_once('.').unwrap();
            if self.kind_of(head)? == Some("experiment") {
                exp_id = head.to_string();
                name = Some(tail.to_string());
            }
        }
        let Some(name) = name else {
            return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("{} is not a setting address; write <experiment>/<setting>, e.g. e1/lr.", repr_str(address))));
        };
        let exp: Experiment = self.load(&exp_id)?;
        let mut s = exp.lookup(&name).cloned();
        if s.is_none() {
            s = exp.live_variants().iter().find_map(|v| v.setting(&name).filter(|x| x.retracted.is_none()).cloned());
        }
        let s = s.ok_or_else(|| RBError::msg("E_OBJECT_NOT_FOUND", format!("Experiment {} has no setting {}.", exp.id, repr_str(&name))))?;
        Ok((exp, name, s))
    }

    /// Called under the lock: a chosen id is kept; otherwise the kind's prefix and six random characters.
    pub fn new_id(&self, kind: &str, wanted: Option<&str>) -> Res<String> {
        if let Some(w) = wanted.filter(|w| !w.is_empty()) {
            if !id_ok(w) {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("Ids are letters, digits, '_', '.' and '-', up to 80 characters (got {}).", repr_str(w))));
            }
            if self.kind_of(w)?.is_some() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{} is already taken in this project.", repr_str(w))));
            }
            return Ok(w.to_string());
        }
        let prefix = match PARTS.iter().find(|p| p.0 == kind) {
            Some(p) => p.2,
            None => kind_prefix(kind),
        };
        loop {
            // base64.b32encode(secrets.token_bytes(5)).lower()[:6]: the first 30 of 40 random bits
            let bits = crate::rng::randbits(40) as u64;
            const B32: &[u8] = b"abcdefghijklmnopqrstuvwxyz234567";
            let tail: String = (0..6).map(|k| B32[((bits >> (35 - 5 * k)) & 31) as usize] as char).collect();
            let candidate = format!("{prefix}{tail}");
            let taken = if is_part(kind) { self.where_()?.contains_key(&(kind.to_string(), candidate.clone())) } else { self.kind_of(&candidate)?.is_some() };
            if !tail.chars().all(|c| c.is_ascii_digit()) && !taken {
                return Ok(candidate);
            }
        }
    }

    pub fn require(&self, kind: &str, id: Option<&str>) -> Res<()> {
        if let Some(i) = id {
            if !id_ok(i) || !self.where_()?.contains_key(&(kind.to_string(), i.to_string())) {
                return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("No {kind} {} in this project.", repr_str(i))));
            }
        }
        Ok(())
    }
}

/// What a log line says of one setting it wrote or checked: its own detail, or, for a check of several (rb spec verify),
/// that setting's row of its results. None when the line is not about the setting.
pub fn checked(row: &Dict, setting: &str) -> Option<Dict> {
    let Some(Value::Dict(d)) = row.get("detail") else { return None };
    if d.get_str("setting") == Some(setting) {
        return Some(d.clone());
    }
    d.get("results")?.as_list()?.iter().find_map(|x| x.as_dict().filter(|x| x.get_str("setting") == Some(setting)).cloned())
}

fn at_text(r: &Dict) -> String {
    r.get("at").map(pyjson::py_str).unwrap_or_default()
}

/// str.splitlines() for the line breaks a JSONL file can hold.
pub fn py_splitlines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let b = text.as_bytes();
    let mut i = 0;
    while i < text.len() {
        let c = text[i..].chars().next().unwrap();
        let w = c.len_utf8();
        let brk = matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}');
        if brk {
            out.push(&text[start..i]);
            if c == '\r' && i + 1 < b.len() && b[i + 1] == b'\n' {
                i += 1;
            }
            start = i + w;
        }
        i += w;
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

/// A record's stored form validated as its kind's model.
fn load_valid(kind: &str, v: &Value, rel: &str) -> Res<Obj> {
    Ok(match kind {
        "project" => Obj::Project(validate_as(v, rel, "project", |v, l, e| Project::validate(v, l, e))?),
        "question" => Obj::Question(validate_as(v, rel, "question", |v, l, e| Question::validate(v, l, e))?),
        "experiment" => Obj::Experiment(validate_as(v, rel, "experiment", |v, l, e| Experiment::validate(v, l, e))?),
        "metric" => Obj::Metric(validate_as(v, rel, "metric", |v, l, e| Metric::validate(v, l, e))?),
        "criterion" => Obj::Criterion(validate_as(v, rel, "criterion", |v, l, e| Criterion::validate(v, l, e))?),
        "run" => Obj::Run(validate_as(v, rel, "run", |v, l, e| Run::validate(v, l, e))?),
        "analysis" => Obj::Analysis(validate_as(v, rel, "analysis", |v, l, e| Analysis::validate(v, l, e))?),
        "resource" => Obj::Resource(validate_as(v, rel, "resource", |v, l, e| Resource::validate(v, l, e))?),
        "decision" => Obj::Decision(validate_as(v, rel, "decision", |v, l, e| Decision::validate(v, l, e))?),
        _ => return Err(RBError::msg("E_STATE_NEWER", format!("This project holds a {} record, which this rb does not know: it was written by a newer rb.", repr_str(kind)))),
    })
}

fn validate_as<T>(v: &Value, rel: &str, kind: &str, validate: impl Fn(&Value, &mut crate::model::Loc, &mut Vec<VErr>) -> Option<T>) -> Res<T> {
    let mut errs = Vec::new();
    let mut loc = Vec::new();
    match validate(v, &mut loc, &mut errs) {
        Some(m) if errs.is_empty() => Ok(m),
        _ => {
            let probs = problems(&errs, "");
            if looks_newer(&errs, v) {
                Err(RBError::msg("E_STATE_NEWER", format!("{rel} uses a shape this rb does not know: it was written by a newer rb.")).with_problems(probs))
            } else {
                Err(RBError::msg("E_STATE_CORRUPT", format!("{rel} is not a valid {kind}.")).with_problems(probs))
            }
        }
    }
}

pub fn build_project(fields: Dict) -> Res<Project> {
    build_with(fields, Project::validate)
}

/// Every authority-bearing value in a record, by a stable path.
pub fn authority_values(node: &Value, path: &str) -> IndexMap<String, String> {
    let mut out = IndexMap::new();
    match node {
        Value::Dict(d) => {
            for (k, v) in d {
                let here = if path.is_empty() { k.clone() } else { format!("{path}.{k}") };
                // Criterion alignments carry a declaration as a whole. Experiment relations retain their
                // ordinary editable notes, while their existing author fields remain protected recursively; the same
                // names anywhere else (a per-run setting named where) are ordinary values.
                let criterion = d.contains_key("comparator") && d.contains_key("metric");
                let setting = d.contains_key("set_by") && d.contains_key("name");
                let receipt = path == "receipt";
                let written = WRITERS.iter().any(|w| matches!(d.get(*w), Some(Value::Str(_))));
                if AUTHORITY_WHOLE.contains(&k.as_str())
                    || (criterion && CRITERION_WHOLE.contains(&k.as_str()))
                    || (setting && SETTING_WHOLE.contains(&k.as_str()))
                    || (receipt && RECEIPT_WHOLE.contains(&k.as_str()))
                {
                    // design_factor: false declares nothing, as an absent one does
                    let declares_nothing = k == "design_factor" && matches!(v, Value::Bool(false));
                    if !(declares_nothing || v.is_null() || matches!(v, Value::List(l) if l.is_empty()) || matches!(v, Value::Dict(d) if d.is_empty())) {
                        out.insert(here, canon_json(v));
                    }
                } else if AUTHORITY_FIELDS.contains(&k.as_str()) || (receipt && RECEIPT_FIELDS.contains(&k.as_str())) || (written && k == "via") {
                    out.insert(here, canon_json(v));
                } else if k == "status" && matches!(d.get("set_by"), Some(Value::Str(_))) {
                    out.insert(here, canon_json(v));
                } else {
                    for (a, b) in authority_values(v, &here) {
                        out.insert(a, b);
                    }
                }
            }
        }
        Value::List(items) => {
            let names: Vec<Option<Value>> = items
                .iter()
                .map(|x| match x {
                    Value::Dict(d) => {
                        let n = d.get("name").filter(|v| v.truthy()).or_else(|| d.get("id")).cloned();
                        n.filter(|v| !v.is_null())
                    }
                    _ => None,
                })
                .collect();
            let unique = names.iter().all(|n| n.is_some()) && {
                let set: HashSet<String> = names.iter().map(|n| canon_json(n.as_ref().unwrap())).collect();
                set.len() == names.len()
            };
            for (i, x) in items.iter().enumerate() {
                let label = if unique { pyjson::py_str(names[i].as_ref().unwrap()) } else { i.to_string() };
                for (a, b) in authority_values(x, &format!("{path}[{label}]")) {
                    out.insert(a, b);
                }
            }
        }
        _ => {}
    }
    out
}

/// An authority value (as authority_values gives it, None where absent) as rb's words for an edit give it, or None
/// where they give no value (shown_edit).
fn shown_authority(path: &str, canon: Option<&String>) -> Option<String> {
    let value = canon.and_then(|c| pyjson::loads(c).ok());
    Some(match shown_edit(path, value.as_ref())? {
        None => "None".into(),
        Some(v) if Some(&v) == value.as_ref() => repr_str(canon.map_or("", String::as_str)),
        Some(v) => repr_str(&canon_json(&v)),
    })
}

/// An experiment's gate in a record's form: (if it holds, if not).
fn gate_of(form: &Dict) -> (String, String) {
    let text = |k: &str| form.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    (text("if_holds"), text("if_not"))
}

/// The texts in a record that read in their writer's voice, by where they are: a note's text beside its `by`, and a
/// run's note beside its `created_by`.
fn voiced_texts(node: &Value, path: &str, out: &mut IndexMap<String, (String, String)>) {
    match node {
        Value::Dict(d) => {
            let text = |k: &str| d.get(k).and_then(Value::as_str).map(str::to_string);
            if let (Some(t), Some(by)) = (text("text"), text("by")) {
                out.insert(if path.is_empty() { "text".into() } else { format!("{path}.text") }, (by, t));
            } else if let (Some(t), Some(by), true) = (text("note"), text("created_by"), path.is_empty()) {
                out.insert("note".into(), (by, t));
            }
            if let Some(notes) = d.get("notes") {
                voiced_texts(notes, &format!("{path}notes"), out);
            }
        }
        Value::List(items) => {
            for (i, x) in items.iter().enumerate() {
                let label = x.get("id").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| i.to_string());
                voiced_texts(x, &format!("{path}[{label}]"), out);
            }
        }
        _ => {}
    }
}

/// Where an adoption by `adopter` changes the words of a text another writer's voice says: a note is its writer's.
fn voice_changes(before: &[Dict], new: &Dict, adopter: &str, id: &str) -> Vec<String> {
    let mut after = IndexMap::new();
    voiced_texts(&Value::Dict(new.clone()), "", &mut after);
    let mut out = Vec::new();
    for old in before {
        let mut was = IndexMap::new();
        voiced_texts(&Value::Dict(old.clone()), "", &mut was);
        for (path, (by, text)) in &was {
            match after.get(path) {
                Some((_, now)) if now != text && by != adopter => {
                    let said = format!("{id}.{path}: another writer's words, changed outside rb");
                    if !out.contains(&said) {
                        out.push(said);
                    }
                }
                _ => {}
            }
        }
    }
    out
}

/// Whether this authority value, in a record rb never wrote, asserts something only rb should record.
pub fn claims_authority(path: &str, value: &str) -> bool {
    let leaf = path.rsplit('.').next().unwrap_or("").split('[').next().unwrap_or("");
    match leaf {
        "created_by" | "set_by" | "by" | "actor" => value.starts_with("\"human:"),
        "created_at" | "set_at" | "at" | "produced_at" | "produced_basis" | "session" | "via" | "actor_via" | "agent" => false,
        "words" => value != "\"\"" && value != "null",
        "status" => value != "\"unknown\"" && value != "\"provisional\"",
        _ => true,
    }
}

/// Why no file can be written at `path` on this system (a name Windows keeps for a device), or None.
pub fn unwritable_name(path: &Path) -> Option<std::io::Error> {
    let name = crate::sys::device_name(path)?;
    let said = format!("{name} is a name Windows keeps for a device, so no file or folder can have it here: give the record another id or name");
    Some(std::io::Error::new(std::io::ErrorKind::InvalidInput, said))
}

/// Write bytes atomically: a temporary file beside the target, made readable as the checkout is, then renamed.
pub fn write_atomic(path: &Path, data: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    if let Some(e) = unwritable_name(path) {
        return Err(e);
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut tmp = None;
    for _ in 0..100 {
        let name = format!("{}.{:08x}.tmp", name_of(path), crate::rng::os_randbits(32) as u64);
        let p = path.with_file_name(name);
        match std::fs::OpenOptions::new().write(true).create_new(true).open(&p) {
            Ok(mut f) => {
                f.write_all(data)?;
                tmp = Some(p);
                break;
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    let tmp = tmp.ok_or_else(|| std::io::Error::new(std::io::ErrorKind::Other, "no temporary name"))?;
    crate::sys::readable_as_checkout(&tmp)?;
    crate::sys::replace(&tmp, path)
}

#[cfg(test)]
mod tests {
    use super::{looks_newer, unfamiliar_shape};
    use crate::record::Criterion;
    use crate::value::Value;

    fn criterion(kind: Value, orientation: Value) -> Value {
        let scope = crate::dict! {"experiment" => "e1"};
        crate::obj! {
            "id" => "c1", "experiment" => "e1", "statement" => "s", "metric" => "change.top1", "comparator" => "at_least", "target" => 0.0,
            "relations" => crate::list![crate::obj! {
                "kind" => kind, "target" => "c2", "orientation" => orientation, "source_scope" => Value::Dict(scope.clone()),
                "target_scope" => Value::Dict(scope), "declared_by" => "human:ana", "declared_at" => "2026-10-10T00:00:00+00:00", "why" => "aligned"
            }],
            "created_by" => "human:ana", "created_at" => "2026-10-10T00:00:00+00:00"
        }
    }

    fn reads_as(c: &Value) -> (bool, bool) {
        let mut errs = Vec::new();
        Criterion::validate(c, &mut Vec::new(), &mut errs);
        (unfamiliar_shape(&Criterion::META, c.as_dict().unwrap()), !errs.is_empty() && looks_newer(&errs, c))
    }

    /// A relation kind or orientation a newer rb may add reads as needing that rb, never as corruption; a value no rb
    /// writes (a number) stays corruption, and a relation this rb knows is neither.
    #[test]
    fn a_relation_literal_this_rb_does_not_know_is_newer_and_garbage_is_not() {
        assert_eq!(reads_as(&criterion(Value::from("replicates"), Value::from("same"))), (true, true));
        assert_eq!(reads_as(&criterion(Value::from("reverses"), Value::from("crossed"))), (true, true));
        assert_eq!(reads_as(&criterion(Value::from(5), Value::from("same"))), (false, false));
        assert_eq!(reads_as(&criterion(Value::from("reverses"), Value::from("same"))), (false, false));
    }

    /// A list is read element by element, whatever order a merge left it in: one element a session's form brought in is
    /// one leaf the file holds, never hidden in a list that reads otherwise as a whole.
    #[test]
    fn a_lists_elements_are_leaves_of_their_own() {
        use super::form_leaves;
        let form = |questions: &[&str], amendments: &[&str]| {
            crate::obj! {
                "questions" => Value::list_of_str(questions.iter().map(|q| q.to_string())),
                "settings" => crate::list![crate::obj! {"name" => "optim.lr", "value" => 0.001}],
                "amendments" => Value::List(amendments.iter().map(|a| crate::obj! {"change" => *a}).collect())
            }
        };
        let session = form_leaves(&form(&["q-acc", "q-cal"], &["a1", "a2"]), "");
        let merged = form_leaves(&form(&["q-acc", "q-lat", "q-cal"], &["a1", "a3", "a2"]), "");
        for leaf in ["questions[=\"q-cal\"]", "settings[optim.lr].value"] {
            assert_eq!((merged.get(leaf), session.get(leaf).is_some()), (session.get(leaf), true), "{leaf}");
        }
        let a2: Vec<&String> = session.keys().filter(|k| k.starts_with("amendments[#") && session[*k] == "\"a2\"").collect();
        assert_eq!(a2.len(), 1);
        assert_eq!(merged.get(a2[0]), session.get(a2[0]));
        assert_eq!(form_leaves(&form(&["q-cal", "q-acc"], &["a1", "a2"]), ""), session);
    }
}

#[cfg(test)]
mod checkout_tests {
    use super::{checkout_of, linked_top};
    use std::path::{Path, PathBuf};

    struct Scratch(PathBuf);
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn scratch(name: &str) -> Scratch {
        let d = std::env::temp_dir().join(format!("rb-checkout-{name}-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(&d).unwrap();
        Scratch(crate::sys::canonicalize(&d).unwrap())
    }

    fn write(p: &Path, text: &str) {
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, text).unwrap();
    }

    /// A linked worktree of the repository whose git dir is `git`, at `top`, the way git worktree add lays it out.
    fn linked(git: &Path, name: &str, top: &Path, gitdir_line: Option<&str>) {
        let admin = git.join("worktrees").join(name);
        write(&admin.join("commondir"), "../..\n");
        write(&admin.join("gitdir"), &format!("{}\n", top.join(".git").display()));
        write(&top.join(".git"), &format!("gitdir: {}\n", gitdir_line.map(|s| s.to_string()).unwrap_or_else(|| admin.display().to_string())));
    }

    #[test]
    fn a_single_checkout_works_in_its_own_root() {
        let s = scratch("single");
        let root = s.0.join("p");
        std::fs::create_dir_all(root.join(".git")).unwrap();
        std::fs::create_dir_all(root.join("src/deep")).unwrap();
        assert_eq!(checkout_of(&root, &root), root);
        assert_eq!(checkout_of(&root.join("src/deep"), &root), root);
        assert_eq!(checkout_of(&s.0, &root), root); // a start outside the record's root
        assert_eq!(linked_top(&root.join("src")), None);
    }

    #[test]
    fn a_nested_linked_worktree_is_its_own_checkout() {
        let s = scratch("nested");
        let root = s.0.join("p");
        let wt = root.join(".worktrees").join("b");
        linked(&root.join(".git"), "b", &wt, None);
        std::fs::create_dir_all(wt.join("src")).unwrap();
        assert_eq!(checkout_of(&wt, &root), wt);
        assert_eq!(checkout_of(&wt.join("src"), &root), wt);
        assert_eq!(linked_top(&wt.join("src")), Some(wt.clone()));
        assert_eq!(linked_top(&root), None);
    }

    #[test]
    fn a_relative_gitdir_is_read_from_the_file_that_names_it() {
        let s = scratch("relative");
        let root = s.0.join("p");
        let wt = root.join(".worktrees").join("c");
        linked(&root.join(".git"), "c", &wt, Some("../../.git/worktrees/c"));
        assert_eq!(checkout_of(&wt, &root), wt);
    }

    #[test]
    fn a_record_below_the_top_of_its_repository_finds_a_worktree_nested_in_it() {
        let s = scratch("below");
        let repo = s.0.join("repo");
        let root = repo.join("sub");
        let wt = root.join(".wt").join("x");
        linked(&repo.join(".git"), "x", &wt, None);
        assert_eq!(checkout_of(&wt, &root), wt);
        // where the worktree has the record's directory (sub), that is where its commands work
        std::fs::create_dir_all(wt.join("sub").join("src")).unwrap();
        assert_eq!(checkout_of(&wt, &root), wt.join("sub"));
        assert_eq!(checkout_of(&wt.join("sub").join("src"), &root), wt.join("sub"));
        assert_eq!(linked_top(&wt.join("sub")), Some(wt.clone()));
    }

    #[test]
    fn a_nested_repository_a_submodule_or_a_worktree_of_another_repository_leaves_the_root() {
        let s = scratch("others");
        let root = s.0.join("p");
        std::fs::create_dir_all(root.join(".git")).unwrap();
        // an independent repository nested in the tree
        std::fs::create_dir_all(root.join("vendor/lib/.git")).unwrap();
        std::fs::create_dir_all(root.join("vendor/lib/src")).unwrap();
        assert_eq!(checkout_of(&root.join("vendor/lib/src"), &root), root);
        // a submodule: its gitdir has no commondir
        std::fs::create_dir_all(root.join(".git/modules/sub")).unwrap();
        write(&root.join("sub/.git"), "gitdir: ../.git/modules/sub\n");
        assert_eq!(checkout_of(&root.join("sub"), &root), root);
        assert_eq!(linked_top(&root.join("sub")), None);
        // a worktree of another repository, nested here
        let other = s.0.join("other");
        let wt = root.join("their-wt");
        linked(&other.join(".git"), "t", &wt, None);
        assert_eq!(checkout_of(&wt, &root), root);
    }

    #[test]
    fn a_dot_git_file_rb_cannot_read_leaves_the_root() {
        let s = scratch("unreadable");
        let root = s.0.join("p");
        std::fs::create_dir_all(root.join(".git")).unwrap();
        write(&root.join("w/.git"), "not a gitdir line\n");
        assert_eq!(checkout_of(&root.join("w"), &root), root);
        write(&root.join("v/.git"), "gitdir: /nowhere/at/all\n");
        assert_eq!(checkout_of(&root.join("v"), &root), root);
        assert_eq!(linked_top(&root.join("v")), None);
    }
}

#[cfg(test)]
mod forget_tests {
    /// A request after forget_worked reads a source as it is now, though it was rewritten with as many bytes and the
    /// same modified time (inside the clock's resolution): rb ui's bridge answers every request in one process.
    #[test]
    fn a_new_request_rereads_a_source_rewritten_with_as_many_bytes() {
        let d = std::env::temp_dir().join(format!("rb-forget-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(&d).unwrap();
        let p = d.join("train.yaml");
        std::fs::write(&p, "lr: 0.1\n").unwrap();
        let at = std::fs::metadata(&p).unwrap().modified().unwrap();
        assert_eq!(crate::sources::read_file(&d, "train.yaml", None).unwrap().0, b"lr: 0.1\n");
        std::fs::write(&p, "lr: 0.3\n").unwrap();
        std::fs::File::options().write(true).open(&p).unwrap().set_modified(at).unwrap();
        super::forget_worked();
        let now = crate::sources::read_file(&d, "train.yaml", None).unwrap().0;
        let _ = std::fs::remove_dir_all(&d);
        assert_eq!(now, b"lr: 0.3\n");
    }
}
