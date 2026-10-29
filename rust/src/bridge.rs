//! `rb bridge`: how rb's Python package (the SDK, the terminal UI) asks rb for what it needs.
//!
//! One JSON request per line on standard input, one JSON answer per line on standard output:
//!
//! ```text
//! {"op": "add_run", "root": "/repo", "via": "sdk", "agent": null, "args": {...}}
//! {"ok": true, "value": {...}}
//! {"ok": true, "value": {...}, "warnings": ["LaTeX markup in statement ($\\Delta$). ..."]}
//! {"ok": false, "error": {"code": "E_OBJECT_INVALID", "message": "...", "fix": "...", "exit_code": 2}}
//! ```
//!
//! An op is one call the SDK or the screen makes: it does what the call says through the same ledger every command
//! uses, and answers with the record as `--json` gives it, and with what the command line warns of beside a write
//! (`warnings`, as `data.warnings` in --json). `via` is the surface (sdk, or cli for the user's screen),
//! `agent` fixes the actor to an agent, `words` are the user's words a call acts on. It is not a command for a
//! terminal, and it is in no help text: the Python package is its only caller.

use crate::cmds::{parse_source, parse_value};
use crate::errors::{RBError, Res, EXIT_INVALID};
use crate::ledger::{self, Ledger, NewAnalysis, NewCriterion, NewExperiment, NewResource, NewRun, SetSetting};
use crate::pyjson;
use crate::record::*;
use crate::value::{Dict, Value};
use indexmap::IndexMap;
use std::io::{BufRead, Write};
use std::path::Path;

const VIAS: &[&str] = &["sdk", "cli"];
const RECORD_KINDS: &[&str] = &["question", "experiment", "metric", "criterion", "run", "analysis", "resource", "decision"];

thread_local! {
    // A duplicate SDK write returns the existing record. Keep that fact locally without changing
    // the SDK response or passing any record fields to analytics.
    static NEW_EVIDENCE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
    /// What a write says beside its record, as the command line warns it (a run's resource whose copy here changed, an
    /// analysis's typed numbers, other numbers for an output already recorded): the answer's `warnings`.
    static SAID: std::cell::RefCell<Vec<String>> = const { std::cell::RefCell::new(Vec::new()) };
}

fn say(warning: Option<&String>) {
    if let Some(w) = warning {
        SAID.with(|s| s.borrow_mut().push(w.clone()));
    }
}

/// What a JSON value is, in the words a refusal uses.
fn word(v: Option<&Value>) -> &'static str {
    match v {
        None | Some(Value::Null) => "nothing",
        Some(Value::Bool(_)) => "true or false",
        Some(Value::Str(_)) => "text",
        Some(Value::Int(_)) | Some(Value::BigInt(_)) | Some(Value::Float(_)) => "a number",
        Some(Value::List(_)) => "a list",
        Some(Value::Dict(_)) => "a mapping",
    }
}

/// float(v) of a JSON number; a whole number past what a float holds is infinite, on its side of zero.
fn float(v: &Value) -> f64 {
    match v {
        Value::BigInt(s) => s.parse::<f64>().unwrap_or(if s.starts_with('-') { f64::NEG_INFINITY } else { f64::INFINITY }),
        other => other.as_f64().unwrap_or(f64::NAN),
    }
}

/// A request's arguments, each read as the kind of value its op takes. A value handed on as it was given (`given`) is
/// checked where the record is built, by the same rules as everywhere else.
struct Args<'a> {
    a: &'a Dict,
}

impl<'a> Args<'a> {
    fn refuse(&self, k: &str, expected: &str, got: &str) -> RBError {
        RBError::msg("E_OBJECT_INVALID", format!("{k}: {expected} is expected here (got {got})."))
    }
    /// The value as given; null when it is not given.
    fn given(&self, k: &str) -> Value {
        self.a.get(k).cloned().unwrap_or(Value::Null)
    }
    fn present(&self, k: &str) -> Option<&Value> {
        self.a.get(k).filter(|v| !v.is_null())
    }
    fn opt_text(&self, k: &str) -> Res<Option<String>> {
        match self.present(k) {
            None => Ok(None),
            Some(Value::Str(s)) => Ok(Some(s.clone())),
            v => Err(self.refuse(k, "text", word(v))),
        }
    }
    fn text(&self, k: &str) -> Res<String> {
        Ok(self.opt_text(k)?.unwrap_or_default())
    }
    fn need_text(&self, k: &str) -> Res<String> {
        match self.a.get(k) {
            Some(Value::Str(s)) => Ok(s.clone()),
            v => Err(self.refuse(k, "text", word(v))),
        }
    }
    fn one_of(&self, k: &str, choices: &[&str]) -> Res<String> {
        let v = self.need_text(k)?;
        if !choices.contains(&v.as_str()) {
            return Err(self.refuse(k, &format!("one of {}", choices.join(", ")), &pyjson::repr_str(&v)));
        }
        Ok(v)
    }
    fn number(&self, k: &str) -> Res<Option<f64>> {
        match self.present(k) {
            None => Ok(None),
            Some(v @ (Value::Int(_) | Value::BigInt(_) | Value::Float(_))) => Ok(Some(float(v))),
            v => Err(self.refuse(k, "a number", word(v))),
        }
    }
    fn whole(&self, k: &str) -> Res<Option<i64>> {
        match self.present(k) {
            None => Ok(None),
            Some(Value::Int(i)) => Ok(Some(*i)),
            // past what a count can be: as far as it goes, either way
            Some(Value::BigInt(s)) => Ok(Some(if s.starts_with('-') { i64::MIN + 1 } else { i64::MAX })),
            v => Err(self.refuse(k, "a whole number", word(v))),
        }
    }
    fn opt_flag(&self, k: &str) -> Res<Option<bool>> {
        match self.present(k) {
            None => Ok(None),
            Some(Value::Bool(b)) => Ok(Some(*b)),
            v => Err(self.refuse(k, "true or false", word(v))),
        }
    }
    fn flag(&self, k: &str) -> Res<bool> {
        Ok(self.opt_flag(k)?.unwrap_or(false))
    }
    fn texts(&self, k: &str) -> Res<Vec<String>> {
        match self.present(k) {
            None => Ok(Vec::new()),
            Some(Value::List(l)) => l
                .iter()
                .map(|x| match x {
                    Value::Str(s) => Ok(s.clone()),
                    other => Err(self.refuse(k, "a list of text", &format!("a list with {} in it", word(Some(other))))),
                })
                .collect(),
            v => Err(self.refuse(k, "a list of text", word(v))),
        }
    }
    fn pairs(&self, k: &str) -> Res<Vec<(String, String)>> {
        match self.present(k) {
            None => Ok(Vec::new()),
            Some(Value::List(l)) => l
                .iter()
                .map(|x| match x {
                    Value::List(p) if p.len() == 2 && p[0].is_str() && p[1].is_str() => Ok((p[0].as_str().unwrap().to_string(), p[1].as_str().unwrap().to_string())),
                    other => Err(self.refuse(k, "a list of pairs of text", &format!("a list with {} in it", word(Some(other))))),
                })
                .collect(),
            v => Err(self.refuse(k, "a list of pairs of text", word(v))),
        }
    }
    fn mapping(&self, k: &str) -> Res<Dict> {
        match self.present(k) {
            None => Ok(Dict::new()),
            Some(Value::Dict(d)) => Ok(d.clone()),
            v => Err(self.refuse(k, "a mapping", word(v))),
        }
    }
    fn text_map(&self, k: &str) -> Res<IndexMap<String, String>> {
        self.mapping(k)?
            .into_iter()
            .map(|(key, x)| match x {
                Value::Str(s) => Ok((key, s)),
                other => Err(self.refuse(k, "a mapping of text", &format!("a mapping with {} in it", word(Some(&other))))),
            })
            .collect()
    }
    fn number_map(&self, k: &str) -> Res<Dict> {
        self.mapping(k)?
            .into_iter()
            .map(|(name, x)| match x {
                Value::Int(_) | Value::BigInt(_) | Value::Float(_) => Ok((name, Value::Float(float(&x)))),
                other => Err(self.refuse(k, "a mapping of numbers", &format!("a mapping with {} in it", word(Some(&other))))),
            })
            .collect()
    }
    fn mappings(&self, k: &str) -> Res<Vec<Dict>> {
        match self.present(k) {
            None => Ok(Vec::new()),
            Some(Value::List(l)) => l
                .iter()
                .map(|x| match x {
                    Value::Dict(d) => Ok(d.clone()),
                    other => Err(self.refuse(k, "a list of mappings", &format!("a list with {} in it", word(Some(other))))),
                })
                .collect(),
            v => Err(self.refuse(k, "a list of mappings", word(v))),
        }
    }
}

fn dicts(rows: Vec<Dict>) -> Value {
    Value::List(rows.into_iter().map(Value::Dict).collect())
}

// ---------------------------------------------------------------- opening: these answer with where the record is, and which rb says so

fn opened(led: &Ledger) -> Value {
    crate::obj! {"root" => crate::sys::slashed(&led.root), "rb" => crate::version(), "here" => crate::sys::slashed(&led.here)}
}

fn op_open(a: &Args, via: &str, agent: Option<&str>) -> Res<Value> {
    let path = a.opt_text("path")?;
    let led = Ledger::open_with(path.as_deref().map(Path::new), Some(via), agent)?;
    // a path that names no worktree of its own picks the record; the code is the caller's, where it works in one
    let led = if path.is_some() && led.here == led.root { led.at(None) } else { led };
    Ok(opened(&led))
}

fn op_init(a: &Args, via: &str, agent: Option<&str>) -> Res<Value> {
    let (title, path, id) = (a.need_text("title")?, a.opt_text("path")?, a.opt_text("id")?);
    let root = match &path {
        Some(p) => Path::new(p).to_path_buf(),
        None => std::env::current_dir().unwrap_or_default(),
    };
    let led = Ledger::init(&root, &title, id.as_deref(), Some(via), agent, a.flag("new_project")?)?;
    crate::journal::look(&led); // as rb init: the record begins at rb's first look here
    Ok(opened(&led))
}

fn op_clone(a: &Args, via: &str, agent: Option<&str>) -> Res<Value> {
    let (source, dest) = (a.need_text("source")?, a.opt_text("dest")?);
    // an address is fetched as it is; anything else is a path, as pathlib writes it
    let source = if source.starts_with("http://") || source.starts_with("https://") { source } else { crate::cmds::pathlib_str(&source) };
    let code = a.opt_text("code")?;
    let res = crate::clone::clone(&source, dest.as_deref().map(Path::new), code.as_deref())?;
    let path = res.get("path").and_then(|v| v.as_str()).unwrap_or("").to_string();
    Ok(opened(&Ledger::open_with(Some(Path::new(&path)), Some(via), agent)?))
}

// ---------------------------------------------------------------- the record

fn op_actor(led: &Ledger, _a: &Args) -> Res<Value> {
    Ok(Value::Str(led.actor()?.id))
}

/// Who rb takes the caller for, and how it knows.
fn op_who(led: &Ledger, _a: &Args) -> Res<Value> {
    let who = led.actor()?;
    // agent_session: what makes rb ui refuse here, read as widely as rb confirm reads it
    Ok(crate::obj! {"id" => who.id, "via" => who.via, "runtime" => who.runtime, "ignored" => who.ignored,
                    "agent_session" => crate::actor::agent_session_sign().is_some()})
}

/// The quiet line a screen that stays open says once an upgrade installed a newer rb (layer 3 of updates without the
/// user), or null.
/// `started` is the version the screen read as it opened ("running" here, the first time): the program on disk is the
/// new one once an upgrade replaced it, so only the screen knows what it runs.
fn op_update_notice(_led: &Ledger, a: &Args) -> Res<Value> {
    let started = a.opt_text("started")?.filter(|v| !v.is_empty()).unwrap_or_else(|| crate::version().to_string());
    Ok(crate::obj! {"running" => crate::version(), "line" => crate::ledger::updates::installed_line(&started).map_or(Value::Null, Value::Str)})
}

fn op_reword_question(led: &Ledger, a: &Args) -> Res<Value> {
    Ok(Value::Dict(led.reword_question(&a.need_text("question")?, &a.need_text("text")?, &a.need_text("why")?)?.dump(false)))
}

fn op_project(led: &Ledger, _a: &Args) -> Res<Value> {
    Ok(Value::Dict(led.project()?.dump(false)))
}

fn op_set_project_title(led: &Ledger, a: &Args) -> Res<Value> {
    Ok(Value::Dict(led.set_project_title(&a.need_text("title")?, &a.need_text("why")?)?.dump(false)))
}

/// Every record of a kind, oldest first; with `live`, the ones not retracted.
fn op_records(led: &Ledger, a: &Args) -> Res<Value> {
    let (kind, live) = (a.one_of("kind", RECORD_KINDS)?, a.flag("live")?);
    fn of<T: ledger::FromObj>(led: &Ledger, live: bool, dump: fn(&T) -> Dict) -> Res<Value> {
        let all = led.all::<T>()?;
        Ok(Value::List(all.iter().filter(|r| !live || r.retraction().is_none()).map(|r| Value::Dict(dump(r))).collect()))
    }
    match kind.as_str() {
        "question" => of::<Question>(led, live, |r| r.dump(false)),
        "experiment" => of::<Experiment>(led, live, |r| r.dump(false)),
        "metric" => of::<Metric>(led, live, |r| r.dump(false)),
        "criterion" => of::<Criterion>(led, live, |r| r.dump(false)),
        "run" => of::<Run>(led, live, |r| r.dump(false)),
        "analysis" => of::<Analysis>(led, live, |r| r.dump(false)),
        "resource" => of::<Resource>(led, live, |r| r.dump(false)),
        "decision" => of::<Decision>(led, live, |r| r.dump(false)),
        _ => Ok(Value::List(Vec::new())), // never reached: one_of let only the eight through
    }
}

fn op_add_question(led: &Ledger, a: &Args) -> Res<Value> {
    Ok(Value::Str(led.add_question(&a.need_text("text")?, a.opt_text("id")?.as_deref(), a.opt_text("parent")?.as_deref())?.id))
}

fn op_expose(led: &Ledger, a: &Args) -> Res<Value> {
    let (id, how, why, at) = (a.need_text("resource_id")?, a.need_text("how")?, a.text("why")?, a.opt_text("at")?);
    Ok(Value::Dict(led.expose_resource(&id, &how, &why, at.as_deref())?.dump(false)))
}

fn op_add_metric(led: &Ledger, a: &Args) -> Res<Value> {
    let (name, unit) = (a.need_text("name")?, a.text("unit")?);
    let direction = a.opt_text("direction")?.filter(|d| !d.is_empty()).unwrap_or_else(|| "none".into());
    let (aliases, description) = (a.texts("aliases")?, a.text("description")?);
    Ok(Value::Str(led.add_metric(&name, &unit, &direction, &aliases, &description)?.id))
}

fn op_add_experiment(led: &Ledger, a: &Args) -> Res<Value> {
    let note = a.text("note")?;
    let new = NewExperiment {
        title: a.need_text("title")?,
        id: a.opt_text("id")?,
        question: a.opt_text("question")?,
        hypothesis: a.text("hypothesis")?,
        why: a.text("why")?,
        purpose: a.text("purpose")?,
        baseline: a.opt_text("baseline")?,
        candidates: a.texts("candidates")?,
        varies: a.texts("varies")?,
        like: a.opt_text("like")?,
        questions: a.texts("questions")?,
        if_holds: a.text("if_holds")?,
        if_not: a.text("if_not")?,
    };
    let e = led.add_experiment(&new)?;
    if !note.is_empty() {
        led.add_note(Some(&e.id), &note)?;
    }
    for w in crate::cmds::record::varies_same(led, &e.id, None)? {
        say(Some(&w));
    }
    Ok(Value::Str(e.id))
}

fn op_add_note(led: &Ledger, a: &Args) -> Res<Value> {
    let (experiment, text) = (a.opt_text("experiment")?, a.need_text("text")?);
    Ok(Value::Dict(led.add_note(experiment.as_deref(), &text)?.dump(false)))
}

fn op_add_resource(led: &Ledger, a: &Args) -> Res<Value> {
    let new = NewResource {
        id: a.need_text("id")?,
        r#type: a.need_text("type")?,
        name: a.need_text("name")?,
        version: a.text("version")?,
        subset: a.text("subset")?,
        sha256: a.opt_text("sha256")?,
        digest: a.text("digest")?,
        source: a.text("source")?,
        url: a.opt_text("url")?,
        recipe: a.opt_text("recipe")?,
        locations: a.pairs("locations")?,
        from_path: a.opt_text("file")?,
        note: a.text("note")?,
    };
    Ok(Value::Dict(led.add_resource(&new)?.dump(false)))
}

fn op_locate_resource(led: &Ledger, a: &Args) -> Res<Value> {
    let (id, provider, r, note) = (a.need_text("id")?, a.need_text("provider")?, a.need_text("ref")?, a.text("note")?);
    Ok(Value::Dict(led.locate_resource(&id, &provider, &r, &note)?.dump(false)))
}

fn op_availability(led: &Ledger, a: &Args) -> Res<Value> {
    let res: Resource = led.load(&a.need_text("resource_id")?)?;
    Ok(Value::Dict(led.availability(&res)))
}

fn op_load(led: &Ledger, a: &Args) -> Res<Value> {
    let (kind, id) = (a.need_text("kind")?, a.need_text("id")?);
    Ok(led.load_kind(&kind, &id)?.to_value())
}

fn op_get(led: &Ledger, a: &Args) -> Res<Value> {
    let (kind, obj) = led.get(&a.need_text("id")?)?;
    Ok(crate::obj! {"kind" => kind, "object" => obj.to_value()})
}

fn op_finding(led: &Ledger, a: &Args) -> Res<Value> {
    let crit: Criterion = led.load(&a.need_text("criterion_id")?)?;
    Ok(Value::Dict(led.finding(&crit)?.dump(false)))
}

fn op_status(led: &Ledger, a: &Args) -> Res<Value> {
    let mut v = Value::Dict(led.status(a.opt_text("experiment")?.as_deref())?);
    // rb ui's list of what is open draws it, as every screen draws text (`shown`); the data keeps it as written
    if a.flag("shown")? {
        drawn(led, &mut v)?;
    }
    Ok(v)
}

/// What a screen draws, as it draws text where something a session wrote can wait for the user there.
fn drawn(led: &Ledger, v: &mut Value) -> Res<()> {
    if led.shows_escapes()? {
        crate::ledger::design::shown_in(v);
    }
    Ok(())
}

fn op_context(led: &Ledger, _a: &Args) -> Res<Value> {
    crate::journal::look(led);
    let st = led.status(None)?;
    Ok(Value::Str(crate::cmds::reading::context_markdown(led, &st)?))
}

fn op_log(led: &Ledger, a: &Args) -> Res<Value> {
    let (last, about) = (a.whole("last")?, a.opt_text("about")?);
    let rows = led.log_about(None, about.as_deref().filter(|x| !x.is_empty()))?;
    Ok(dicts(crate::cmds::reading::py_last(rows, last.unwrap_or(0))))
}

/// Look once, with what rb then says of where it looks and when (the scope line), so a screen that looks on its own
/// keeps that line's time true.
fn op_scan(led: &Ledger, _a: &Args) -> Res<Value> {
    let mut s = crate::journal::scan(led)?;
    let scope = crate::journal::Scope::of(led);
    s.insert("scope".into(), Value::Str(scope.said()));
    s.insert("scope_short".into(), Value::Str(scope.said_short()));
    Ok(Value::Dict(s))
}

fn found_value(f: &crate::journal::Found) -> Value {
    crate::obj! {
        "id" => f.id.clone(), "path" => f.path.clone(), "at" => f.at.clone(), "metrics" => Value::Dict(f.metrics.clone()),
        "experiment" => f.experiment.clone(), "key" => f.key.clone(), "options" => Value::list_of_str(f.options.clone()),
        "argv" => Value::list_of_str(f.argv.clone()), "facts" => Value::list_of_str(f.facts.clone()), "run_in" => crate::sys::slashed(&f.here)
    }
}

/// What is in the repository and not in the record, by what rb has already seen.
fn op_found(led: &Ledger, _a: &Args) -> Res<Value> {
    let f = crate::journal::found_all(led, crate::journal::Seen::User)?;
    Ok(crate::obj! {
        "found" => Value::List(f.placed.iter().map(found_value).collect()), "unplaced" => Value::list_of_str(f.unplaced),
        "uncited_changes" => Value::List(f.uncited.iter().map(|u| u.to_value()).collect())
    })
}

/// The same, after looking once.
fn op_reconcile(led: &Ledger, a: &Args) -> Res<Value> {
    crate::journal::scan(led)?;
    op_found(led, a)
}

fn op_journal(led: &Ledger, a: &Args) -> Res<Value> {
    let last = a.whole("last")?;
    Ok(dicts(crate::cmds::reading::py_last(crate::journal::entries(&led.root, None), last.unwrap_or(0))))
}

fn op_export(led: &Ledger, a: &Args) -> Res<Value> {
    let out = a.opt_text("out")?.filter(|o| !o.is_empty());
    let (zip, url, no_repo, with_record) = (a.flag("zip")?, a.opt_text("url")?, a.flag("no_repo")?, a.flag("with_record")?);
    Ok(Value::Dict(crate::export::export(led, out.as_deref().map(Path::new), zip, url.as_deref(), no_repo, with_record, a.flag("replace")?)?))
}

fn op_decide(led: &Ledger, a: &Args) -> Res<Value> {
    let (subject, action, why, revisit) = (a.need_text("subject")?, a.need_text("action")?, a.text("why")?, a.text("revisit")?);
    let text = |k: &str| -> Res<Option<String>> { Ok(a.opt_text(k)?.filter(|s| !s.is_empty())) };
    let past = crate::cmds::record::past_from(a.flag("past")?, text("when")?, text("source")?, text("quote")?, &led.root)?;
    let d = led.decide(&subject, &action, &why, &revisit, past.as_ref())?;
    // a call that waits for a confirmation says so beside its record, as the command line does in data.warnings
    if led.authority(&d.id)?.map_or(false, |x| x.pending) {
        say(Some(&crate::cmds::record::waits_said(&d)));
    }
    Ok(Value::Str(d.id))
}

fn op_retract(led: &Ledger, a: &Args) -> Res<Value> {
    let (subject, why) = (a.need_text("subject")?, a.text("why")?);
    led.retract(&subject, &why, None)?;
    Ok(Value::Null)
}

fn op_experiment_state(led: &Ledger, a: &Args) -> Res<Value> {
    let exp: Experiment = led.load(&a.need_text("experiment")?)?;
    Ok(Value::Str(led.experiment_state(&exp, None)?))
}

fn op_add_variant(led: &Ledger, a: &Args) -> Res<Value> {
    let (experiment, name, role, note, amend) = (a.need_text("experiment")?, a.need_text("name")?, a.need_text("role")?, a.text("note")?, a.opt_text("amend")?);
    led.add_variant(&experiment, &name, &role, &note, amend.as_deref(), &a.text("why")?)?;
    Ok(Value::Null)
}

fn op_set_gate(led: &Ledger, a: &Args) -> Res<Value> {
    let (experiment, if_holds, if_not, why) = (a.need_text("experiment")?, a.opt_text("if_holds")?, a.opt_text("if_not")?, a.text("why")?);
    led.set_gate(&experiment, if_holds.as_deref(), if_not.as_deref(), &why)?;
    Ok(Value::Null)
}

fn op_relate(led: &Ledger, a: &Args) -> Res<Value> {
    let (experiment, kind, target, note) = (a.need_text("experiment")?, a.need_text("kind")?, a.need_text("target")?, a.text("note")?);
    led.relate(&experiment, &kind, &target, &note)?;
    Ok(Value::Null)
}

fn op_add_criterion(led: &Ledger, a: &Args) -> Res<Value> {
    let source = match a.opt_text("source")?.filter(|s| !s.is_empty()) {
        Some(s) => parse_source(Some(&s), a.opt_text("quote")?.as_deref(), None, a.opt_text("locator")?.as_deref(), None, &led.root)?,
        None => None,
    };
    let min_n = a.given("min_n");
    let new = NewCriterion {
        statement: a.need_text("statement")?,
        experiment: a.need_text("experiment")?,
        metric: a.need_text("metric")?,
        comparator: a.need_text("comparator")?,
        target: a.number("target")?,
        tolerance: a.number("tolerance")?,
        source,
        over: a.opt_text("over")?.filter(|o| !o.is_empty()).unwrap_or_else(|| "mean".into()),
        min_n: if min_n.is_null() { Value::Int(1) } else { min_n },
        noise: a.number("noise")?,
        note: a.text("note")?,
        amend: a.opt_text("amend")?,
        id: a.opt_text("id")?,
        subject: a.text("subject")?,
        reference: a.text("reference")?,
        estimand: a.text("estimand")?,
        r#where: a.mapping("where")?,
        subject_where: a.mapping("subject_where")?,
        reference_where: a.mapping("reference_where")?,
        margin: a.number("margin")?,
        role: a.opt_text("role")?.filter(|r| !r.is_empty()).unwrap_or_else(|| "primary".into()),
        questions: a.texts("questions")?,
        when_met: a.text("when_met")?,
        why: a.text("why")?,
    };
    let crit = led.add_criterion(&new)?;
    // as rb criterion add says it: a metric close to a name the runs report is most likely misspelt
    say(led.metric_near(&crit)?.as_ref());
    Ok(Value::Str(crit.id))
}

fn op_file_criterion(led: &Ledger, a: &Args) -> Res<Value> {
    let crit = led.file_criterion(&a.need_text("criterion")?, &a.need_text("question")?, a.opt_text("when_met")?.as_deref(), a.flag("remove")?, &a.need_text("why")?, a.opt_text("amend")?.as_deref())?;
    Ok(Value::Dict(crit.dump(false)))
}

fn op_relate_criterion(led: &Ledger, a: &Args) -> Res<Value> {
    let c = led.relate_criterion(&a.need_text("criterion")?, &a.need_text("relation")?, &a.need_text("target")?, a.opt_text("orientation")?.as_deref(), a.flag("remove")?, &a.need_text("why")?, a.opt_text("amend")?.as_deref())?;
    Ok(Value::Dict(c.dump(false)))
}

/// Every relation a criterion is a side of, declared from either side, read as rb show reads it.
fn op_relations(led: &Ledger, a: &Args) -> Res<Value> {
    let crit: Criterion = led.load(&a.need_text("criterion")?)?;
    Ok(Value::List(led.relations_touching(&std::collections::HashSet::from([crit.id]), &IndexMap::new())?))
}

fn op_move_question(led: &Ledger, a: &Args) -> Res<Value> {
    Ok(Value::Dict(led.move_question(&a.need_text("question")?, a.opt_text("parent")?.as_deref(), &a.text("why")?)?.dump(false)))
}

fn op_add_analysis(led: &Ledger, a: &Args) -> Res<Value> {
    let source = match a.opt_text("source")?.filter(|s| !s.is_empty()) {
        Some(s) => parse_source(Some(&s), a.opt_text("quote")?.as_deref(), None, None, None, &led.root)?,
        None => None,
    };
    let some = |v: Value| if v.is_null() { None } else { Some(v) };
    let new = NewAnalysis {
        comparisons: a.mappings("comparisons")?,
        title: a.text("title")?,
        reads: a.texts("reads")?,
        inputs: a.pairs("inputs")?,
        files: a.texts("files")?,
        command: a.opt_text("command")?,
        method: a.text("method")?,
        unit: a.text("unit")?,
        units: some(a.given("units")),
        draws: some(a.given("draws")),
        level: some(a.given("level")),
        source,
        computed: a.flag("computed")?,
        supersedes: a.texts("supersedes")?,
        why: a.text("why")?,
        produced_at: a.opt_text("produced_at")?,
        note: a.text("note")?,
        id: a.opt_text("id")?,
    };
    let got = led.add_analysis(&a.need_text("experiment")?, new)?;
    NEW_EVIDENCE.with(|flag| flag.set(got.duplicate_of.is_none()));
    say(got.warning.as_ref());
    Ok(Value::Dict(got.record.dump(false)))
}

fn op_criteria(led: &Ledger, a: &Args) -> Res<Value> {
    let all = led.criteria_of(&a.need_text("experiment")?)?;
    Ok(Value::list_of_str(all.into_iter().filter(|c| c.retracted.is_none()).map(|c| c.id)))
}

fn op_add_run(led: &Ledger, a: &Args) -> Res<Value> {
    let mut metrics = a.number_map("metrics")?;
    let mut files = a.texts("files")?;
    let (mut per_run, mut produced_at) = (a.mapping("per_run")?, a.opt_text("produced_at")?);
    // the numbers were logged by the code that made them: the files exp.record and a run block keep, as --artifact
    // keeps one, never date the run
    let (mut basis, mut numbers_from, mut row) = ("logged".to_string(), Vec::new(), None);
    // exp.record_from: the numbers of a file, or of one row of a SQLite database, read as rb run add --from reads them
    if let Some(path) = a.opt_text("from_file")? {
        let got = crate::cmds::record::read_from(led, &path, &a.texts("keys")?, a.opt_text("table")?.as_deref(), &a.mapping("where")?, &a.texts("select")?, per_run, produced_at)?;
        if let Some(both) = got.metrics.keys().find(|k| metrics.contains_key(*k)) {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("metrics and the file both give {both}.")));
        }
        if !got.skipped.is_empty() {
            say(Some(&format!("{}: not numbers, so not metrics: {}", got.shown, got.skipped.join(", "))));
        }
        metrics.extend(got.metrics);
        (per_run, produced_at, row) = (got.per_run, got.produced_at, got.row);
        let rel = crate::cmds::root_relative(&path, &led.root);
        files.push(rel.clone());
        numbers_from.push(rel);
        basis = "file".into();
    }
    let new = NewRun {
        metrics,
        variant: a.opt_text("variant")?,
        files,
        links: a.text_map("links")?,
        command: a.opt_text("command")?,
        command_source: a.opt_text("command_source")?,
        per_run,
        config: a.opt_text("config")?,
        commit: a.opt_text("commit")?,
        basis,
        again: a.opt_text("again")?,
        note: a.text("note")?,
        inputs: a.pairs("inputs")?,
        outputs: a.pairs("outputs")?,
        host: a.text_map("host")?,
        repeats: a.opt_text("repeats")?,
        produced_at,
        replaces: a.opt_text("replaces")?,
        why: a.text("why")?,
        tree: None,
        numbers_from,
        row,
    };
    let shape = if new.basis == "file" { crate::cmds::record::Shape::File } else { crate::cmds::record::Shape::Sdk };
    let got = led.add_run(&a.need_text("experiment")?, new)?;
    NEW_EVIDENCE.with(|flag| flag.set(got.duplicate_of.is_none()));
    say(got.warning.as_ref());
    if got.duplicate_of.is_none() {
        for w in crate::cmds::record::postdates_run(led, &got.record.experiment, &got.record.id)? {
            say(Some(&w));
        }
        // as rb run add says it: a run none of whose numbers a criterion reads counts toward nothing
        say(crate::cmds::record::nothing_counts(led, &got.record, shape)?.as_ref());
    }
    Ok(crate::obj! {"run" => Value::Dict(got.record.dump(false)), "duplicate" => got.duplicate_of.is_some()})
}

fn op_compare(led: &Ledger, a: &Args) -> Res<Value> {
    Ok(Value::Dict(led.compare(&a.need_text("experiment")?)?))
}

fn op_rerunnability(led: &Ledger, a: &Args) -> Res<Value> {
    let exp: Experiment = led.load(&a.need_text("experiment")?)?;
    Ok(dicts(led.rerunnability(&exp)?))
}

fn op_freeze(led: &Ledger, a: &Args) -> Res<Value> {
    let (experiment, why, amend) = (a.need_text("experiment")?, a.text("why")?, a.flag("amend")?);
    led.freeze(&experiment, &why, amend)?;
    Ok(Value::Null)
}

fn op_set_setting(led: &Ledger, a: &Args) -> Res<Value> {
    let (source, cited_source) = (a.opt_text("source")?.filter(|s| !s.is_empty()), a.opt_text("cited_source")?.filter(|s| !s.is_empty()));
    let src = match &source {
        Some(s) => parse_source(Some(s), a.opt_text("quote")?.as_deref(), a.opt_text("commit")?.as_deref(), a.opt_text("locator")?.as_deref(), a.opt_text("term")?.as_deref(), &led.root)?,
        None => None,
    };
    let (name, variant, mut value) = (a.need_text("name")?, a.opt_text("variant")?.filter(|v| !v.is_empty()), a.given("value"));
    let full = match &variant {
        Some(v) => format!("{v}.{name}"),
        None => name,
    };
    let (unknown, per_run) = (a.flag("unknown")?, a.flag("per_run")?);
    if let Value::Str(text) = &value {
        value = parse_value(text)?;
    }
    if value.is_null() && !unknown && !per_run && a.opt_flag("design_factor")? != Some(true) {
        if let Some(s) = &src {
            value = crate::cmds::record::read_value(led, s, &a.need_text("experiment")?, &full)?;
        }
    }
    let cited = match a.given("cited") {
        Value::Null => None,
        given => {
            let csrc = match &cited_source {
                Some(cs) => parse_source(Some(cs), None, None, None, None, &led.root)?,
                None => None,
            };
            let v = match &given {
                Value::Str(text) => parse_value(text)?,
                _ => given,
            };
            Some(Cited { value: v, source: csrc })
        }
    };
    let experiment = a.need_text("experiment")?;
    let (setting, _) = led.set_setting(
        &experiment,
        &full,
        SetSetting {
            value,
            unknown,
            source: src,
            required: a.opt_flag("required")?,
            per_run: if per_run { Some(true) } else { None },
            design_factor: a.opt_flag("design_factor")?,
            cited,
            note: a.opt_text("note")?,
            amend: a.opt_text("amend")?,
            verify: a.opt_flag("verify")?.unwrap_or(true),
            why: a.opt_text("why")?.unwrap_or_default(),
        },
    )?;
    // as the command line says it beside the setting: a line that postdates a past run, a cited value that differs, a
    // varied factor left the same
    let cited = crate::cmds::record::cited_differs(&experiment, &full, &setting);
    for w in crate::cmds::record::postdates_notes(led, &experiment, &[(full.clone(), setting.clone())])?.into_iter().chain(cited).chain(crate::cmds::record::varies_same(led, &experiment, Some(&full))?) {
        say(Some(&w));
    }
    Ok(Value::Dict(setting.dump(false)))
}

fn op_verify_settings(led: &Ledger, a: &Args) -> Res<Value> {
    let (experiment, names) = (a.need_text("experiment")?, a.texts("names")?);
    Ok(dicts(led.verify_settings(&experiment, &names)?))
}

fn op_declare_varies(led: &Ledger, a: &Args) -> Res<Value> {
    let (experiment, names, amend) = (a.need_text("experiment")?, a.texts("names")?, a.opt_text("amend")?);
    let (_, new) = led.declare_varies(&experiment, &names, amend.as_deref(), &a.text("why")?)?;
    for n in &new {
        for w in crate::cmds::record::varies_same(led, &experiment, Some(n))? {
            say(Some(&w));
        }
    }
    Ok(Value::Null)
}

// ---------------------------------------------------------------- the user's screen

fn op_home(led: &Ledger, a: &Args) -> Res<Value> {
    let since = a.whole("since_baseline")?;
    let mut v = crate::view::for_a_screen(|| Ok::<_, RBError>(crate::view::home(led, Some(since))?.to_value()))?;
    drawn(led, &mut v)?;
    Ok(v)
}

/// The places a screen can show (rb ui's w): each worktree of the clone, with what its record is to this one, and the
/// versions of the record of the place named (`place`, by default this one).
fn op_places(led: &Ledger, a: &Args) -> Res<Value> {
    let list = crate::places::list(&led.root);
    let id = a.opt_text("place")?.filter(|i| !i.is_empty());
    let p = match &id {
        Some(i) => list.iter().find(|p| p.id == *i).ok_or_else(|| RBError::msg("E_USAGE", format!("{i} is not a place rb listed here.")))?,
        None => list.iter().find(|p| p.record == "this").ok_or_else(|| RBError::msg("E_INTERNAL", "rb lists no place for this record."))?,
    };
    let versions = p.root.as_ref().filter(|_| p.readable()).map(|r| crate::places::versions(r)).unwrap_or_default();
    Ok(crate::obj! {
        "places" => Value::List(list.iter().map(|p| p.to_value()).collect()), "place" => p.id.clone(),
        "versions" => Value::List(versions.iter().map(|v| v.to_value(p)).collect())
    })
}

fn op_mark_seen(led: &Ledger, _a: &Args) -> Res<Value> {
    let (old, lines) = crate::view::mark_seen(led)?;
    Ok(crate::list![old, lines])
}

/// A found item's card, or null when nothing found goes by that id now.
fn op_card(led: &Ledger, a: &Args) -> Res<Value> {
    let r = a.need_text("ref")?;
    // with the checkout its command runs in, as the user would type it there, which rb ui's y runs it in
    let with_where = |c: crate::view::Card| {
        let mut v = c.to_value();
        if let Value::Dict(d) = &mut v {
            d.insert("run_in".into(), Value::Str(c.run_in.clone()));
        }
        v
    };
    let mut v = crate::view::for_a_screen(|| Ok::<_, RBError>(crate::view::card(led, &r)?.map(with_where).unwrap_or(Value::Null)))?;
    drawn(led, &mut v)?;
    Ok(v)
}

/// A record's card, or null when nothing on the catalogue goes by that name.
fn op_object_card(led: &Ledger, a: &Args) -> Res<Value> {
    let subject = a.need_text("subject")?;
    let mut v = crate::view::for_a_screen(|| Ok::<_, RBError>(crate::view::object_card(led, &subject)?.map(|c| c.to_value()).unwrap_or(Value::Null)))?;
    drawn(led, &mut v)?;
    Ok(v)
}

fn op_dismiss(led: &Ledger, a: &Args) -> Res<Value> {
    let (key, why) = (a.need_text("key")?, a.text("why")?);
    crate::journal::dismiss(led, &key, &why)?;
    Ok(Value::Null)
}

type Op = fn(&Ledger, &Args) -> Res<Value>;
type Opening = fn(&Args, &str, Option<&str>) -> Res<Value>;

/// The ops that write the record: the only ones a request may preview (dry_run), as the CLI's write commands are.
const RECORD_WRITES: &[&str] = &[
    "set_project_title", "add_question", "move_question", "reword_question", "expose", "add_metric", "add_experiment", "add_note", "add_resource",
    "locate_resource", "decide", "retract", "add_variant", "set_gate", "relate", "add_criterion", "file_criterion", "relate_criterion",
    "add_analysis", "add_run", "freeze", "set_setting", "verify_settings", "declare_varies",
];

/// The ops a screen reads another place or a past version with ("place", "at"): read, never written.
const ELSEWHERE_READS: &[&str] = &["home", "card", "object_card", "status", "journal"];

/// The ops that answer where there is no record yet: who is asking, and the places a screen can show.
const RECORD_FREE: &[&str] = &["who", "actor", "places", "update_notice"];

fn opening(op: &str) -> Option<Opening> {
    Some(match op {
        "open" => op_open,
        "init" => op_init,
        "clone" => op_clone,
        _ => return None,
    })
}

fn op_named(op: &str) -> Option<Op> {
    Some(match op {
        "actor" => op_actor,
        "who" => op_who,
        "update_notice" => op_update_notice,
        "reword_question" => op_reword_question,
        "project" => op_project,
        "set_project_title" => op_set_project_title,
        "records" => op_records,
        "found" => op_found,
        "add_question" => op_add_question,
        "move_question" => op_move_question,
        "expose" => op_expose,
        "add_metric" => op_add_metric,
        "add_experiment" => op_add_experiment,
        "add_note" => op_add_note,
        "add_resource" => op_add_resource,
        "locate_resource" => op_locate_resource,
        "availability" => op_availability,
        "load" => op_load,
        "get" => op_get,
        "finding" => op_finding,
        "status" => op_status,
        "context" => op_context,
        "log" => op_log,
        "scan" => op_scan,
        "reconcile" => op_reconcile,
        "journal" => op_journal,
        "export" => op_export,
        "decide" => op_decide,
        "retract" => op_retract,
        "experiment_state" => op_experiment_state,
        "add_variant" => op_add_variant,
        "set_gate" => op_set_gate,
        "relate" => op_relate,
        "add_criterion" => op_add_criterion,
        "file_criterion" => op_file_criterion,
        "relate_criterion" => op_relate_criterion,
        "relations" => op_relations,
        "add_analysis" => op_add_analysis,
        "criteria" => op_criteria,
        "add_run" => op_add_run,
        "compare" => op_compare,
        "rerunnability" => op_rerunnability,
        "freeze" => op_freeze,
        "set_setting" => op_set_setting,
        "verify_settings" => op_verify_settings,
        "declare_varies" => op_declare_varies,
        "home" => op_home,
        "places" => op_places,
        "mark_seen" => op_mark_seen,
        "card" => op_card,
        "object_card" => op_object_card,
        "dismiss" => op_dismiss,
        _ => return None,
    })
}

// ---------------------------------------------------------------- one request, one answer

fn error(err: &RBError) -> Value {
    let mut e = match err.to_value() {
        Value::Dict(d) => d,
        _ => Dict::new(),
    };
    e.insert("exit_code".into(), Value::Int(err.exit_code as i64));
    crate::obj! {"ok" => false, "error" => Value::Dict(e)}
}

/// A request that is not one: the caller is rb's own package, so the fix is the install, never the command line.
fn refused(code: &str, message: String) -> Value {
    let fix = format!("rb's Python package sends these requests. Reinstall so the package and rb are one version: pip install --force-reinstall \"rabbit-brain=={}\".", crate::version());
    error(&RBError::msg_fix(code, message, fix))
}

/// Answer one request. What goes wrong is the answer.
pub fn call(req: &Value) -> Value {
    let op = match req.get("op") {
        Some(Value::Str(op)) if req.is_dict() => op.clone(),
        _ => return refused("E_USAGE", "A request is a JSON object with \"op\", \"root\" and \"args\".".into()),
    };
    let empty = Dict::new();
    let a = match req.get("args") {
        None | Some(Value::Null) => &empty,
        Some(Value::Dict(d)) => d,
        Some(_) => return refused("E_USAGE", format!("{op}: \"args\" is a JSON object.")),
    };
    let via = match req.get("via") {
        Some(v) if v.truthy() => match v.as_str() {
            Some(s) if VIAS.contains(&s) => s.to_string(),
            _ => return refused("E_USAGE", format!("{op}: \"via\" is one of {}.", VIAS.join(", "))),
        },
        _ => "sdk".to_string(),
    };
    let agent = match req.get("agent") {
        None | Some(Value::Null) => None,
        Some(Value::Str(s)) => Some(s.clone()).filter(|s| !s.is_empty()),
        Some(_) => return refused("E_USAGE", format!("{op}: \"agent\" is a name.")),
    };
    // a write previewed (dry_run=True in the SDK): every check runs, and what it would write is answered, never written
    let dry = match req.get("dry_run") {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(_) => return refused("E_USAGE", format!("{op}: \"dry_run\" is true or false.")),
    };
    // starting a record, a scan or an export writes what no preview holds: refused before it runs
    if dry && !RECORD_WRITES.contains(&op.as_str()) {
        return refused("E_USAGE", format!("{op}: \"dry_run\" is taken only by a write to the record."));
    }
    let args = Args { a };
    let ok = |value: Value| crate::obj! {"ok" => true, "value" => value};
    if op == "telemetry_inspected" && via == "cli" {
        // The screen calls this once a session, when the record first shows. Polling and repainting do not.
        let _ = crate::cli::guarded(|| crate::usage::inspected(crate::telemetry::Surface::Tui));
        return ok(Value::Null);
    }
    let surface = if via == "cli" { crate::telemetry::Surface::Tui } else { crate::telemetry::Surface::Sdk };
    let actor = if agent.is_some() { crate::telemetry::Actor::Agent } else { crate::telemetry::Actor::detect() };
    let usage = crate::usage::Operation::start(&op, surface, actor, true);
    NEW_EVIDENCE.with(|flag| flag.set(false));
    SAID.with(|s| s.borrow_mut().clear());
    crate::ledger::forget_worked(); // what an earlier request worked out is not this one's
    crate::plain::set_aside();
    ledger::set_session(Some(via.clone()), agent.clone());
    crate::owned::during(Some(&op));
    if dry {
        crate::dry::begin();
    }
    let mut root_of: Option<std::path::PathBuf> = None;
    let result: Res<Value> = crate::cli::guarded(|| -> Res<Value> {
        // RB_TEST_FAULT=<text> stops a request on a fault of rb's own, as it stops a command: the tests hold how one is said
        if let Some(text) = std::env::var("RB_TEST_FAULT").ok().filter(|t| !t.is_empty()) {
            panic!("{text}");
        }
        if let Some(f) = opening(&op) {
            return f(&args, &via, agent.as_deref()).map(ok);
        }
        let Some(f) = op_named(&op) else {
            return Ok(refused("E_USAGE", format!("The bridge has no op called {}.", pyjson::repr_str(&op))));
        };
        let root = match req.get("root") {
            Some(Value::Str(r)) if !r.is_empty() => r.clone(),
            _ => return Ok(refused("E_USAGE", format!("{op}: \"root\" is the directory that holds .rb/."))),
        };
        let words = match req.get("words") {
            None => None,
            Some(Value::Null) => Some(String::new()),
            Some(Value::Str(w)) => Some(w.clone()),
            Some(_) => return Ok(refused("E_USAGE", format!("{op}: \"words\" is text."))),
        };
        // the checkout the caller works in (op open's "here"): a linked worktree nested below root, else root
        let here = match req.get("here") {
            None | Some(Value::Null) => None,
            Some(Value::Str(h)) if !h.is_empty() => Some(h.clone()),
            Some(_) => return Ok(refused("E_USAGE", format!("{op}: \"here\" is the directory the caller works in."))),
        };
        // a place or a past version the screen chose (rb ui's w): only an id or a commit rb lists, read and never written
        let mut chose: [Option<String>; 2] = [None, None];
        for (i, k) in ["place", "at"].iter().enumerate() {
            match req.get(*k) {
                None | Some(Value::Null) => {}
                Some(Value::Str(v)) if !v.is_empty() => chose[i] = Some(v.clone()),
                Some(_) => return Ok(refused("E_USAGE", format!("{op}: \"{k}\" is an id or a commit rb listed."))),
            }
        }
        let [place, at] = chose;
        if place.is_some() || at.is_some() {
            if !ELSEWHERE_READS.contains(&op.as_str()) {
                return Ok(refused("E_USAGE", format!("{op}: \"place\" and \"at\" are taken only by what a screen reads ({}).", ELSEWHERE_READS.join(", "))));
            }
            ledger::set_words(words);
            let start = crate::sources::resolve_path(Path::new(&root));
            let list = crate::places::list(&start);
            let versions_of = |p: &crate::places::Place| p.root.as_ref().map(|r| crate::places::versions(r)).unwrap_or_default();
            let chosen = crate::places::choose(&start, &list, place.as_deref(), at.as_deref(), &versions_of, &|| crate::places::named_owner(&start))
                .map_err(|why| RBError::msg("E_USAGE", why))?;
            let shown = crate::places::shown(&chosen, &list);
            let led = Ledger::new(&chosen.root, &via, agent.clone());
            root_of = Some(led.root.clone());
            // a past version offers no command to copy (guarantee 38): its lines say what they say without one
            let past = chosen.version.is_some();
            let mut value = match (crate::dry::reading(|| crate::view::at_place(shown, || crate::view::as_past_version(past, || f(&led, &args)))), chosen.gone_now()) {
                (Err(_), Some(why)) => return Err(RBError::msg("E_USAGE", why)),
                (read, _) => read?,
            };
            if op == "home" {
                crate::places::confirm_from_here(&mut value, &chosen);
                if past {
                    withhold_confirms(&mut value);
                }
            }
            return Ok(ok(value));
        }
        ledger::set_words(words); // read by the ledger opened next
        let mut led = Ledger::new(Path::new(&root), &via, agent.clone());
        if let Some(h) = &here {
            led = led.at(Some(Path::new(h)));
        }
        root_of = Some(led.root.clone());
        // no record there: refused before the op writes anything (a lock, the journal, a screen's seen marker)
        if !RECORD_FREE.contains(&op.as_str()) && !crate::dry::is_file(&led.dir.join(crate::ledger::ROOT_FILE)) {
            return Err(crate::ledger::no_project(Some(&led.root)));
        }
        f(&led, &args).map(ok)
    })
    .and_then(|r| r);
    let preview = if dry { crate::dry::end() } else { None };
    ledger::set_words(None);
    ledger::set_session(None, None);
    crate::owned::during(None);
    let mut said = SAID.with(|s| std::mem::take(&mut *s.borrow_mut()));
    said.extend(crate::plain::take_note());
    // a fault in rb carries the report link, as on the command line; a request names no command of rb's tree
    let mut answer = result.unwrap_or_else(|err| error(&crate::ledger::report_link::reported(err, "")));
    // what the write says beside its record, as the command line's --json carries it in data.warnings
    if let Value::Dict(d) = &mut answer {
        if !said.is_empty() && d.get("ok").map_or(false, Value::truthy) {
            d.insert("warnings".into(), Value::list_of_str(said));
        }
        if let Some(p) = &preview {
            d.insert("dry_run".into(), Value::Bool(true));
            d.insert("would_write".into(), crate::dry::would_write(p, root_of.as_deref()));
        }
    }
    let _ = crate::cli::guarded(|| {
        let success = answer.get("ok").map_or(false, Value::truthy);
        let code = if success { 0 } else { answer.get("error").and_then(|v| v.get("exit_code")).and_then(Value::as_i64).unwrap_or(2) as i32 };
        let ready = success && !dry && NEW_EVIDENCE.with(std::cell::Cell::get);
        usage.finish(code, success && !dry && matches!(op.as_str(), "init" | "clone"),
            ready.then_some(crate::telemetry::RecordRoute::Declared),
            // the command line's inspecting set: status, show, compare, log
            success && matches!(op.as_str(), "status" | "get" | "finding" | "relations" | "compare" | "log"),
            success && op == "context");
    });
    answer
}

/// Answer requests until standard input ends: one JSON object a line in, one a line out. It takes no arguments.
pub fn serve(argv: &[String]) -> i32 {
    if !argv.is_empty() || crate::cli::isatty(0) {
        crate::cli::eprintln_err(
            "rb bridge: rb's Python package talks to rb through this, one JSON request per line on standard input. It is not a command for a terminal.",
        );
        return EXIT_INVALID;
    }
    let stdin = std::io::stdin();
    let mut input = stdin.lock();
    let mut raw: Vec<u8> = Vec::new();
    loop {
        raw.clear();
        match input.read_until(b'\n', &mut raw) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        let text = String::from_utf8_lossy(&raw);
        let line = crate::rbyaml::py_strip(&text);
        if line.is_empty() {
            continue;
        }
        let answer = match pyjson::loads(line) {
            Ok(req) => call(&req),
            Err(e) => refused("E_USAGE", format!("Not a JSON request: {e}")),
        };
        let mut out = std::io::stdout().lock();
        if out.write_all(format!("{}\n", pyjson::dumps(&answer)).as_bytes()).and_then(|_| out.flush()).is_err() {
            return 0; // the caller left: not an error
        }
        let _ = crate::cli::guarded(crate::telemetry::flush_if_captured);
    }
    0
}

/// A home view of a past version with no command to copy: what waits for a confirmation keeps its rows, each with no
/// command that confirms it (guarantee 38).
fn withhold_confirms(home: &mut Value) {
    if let Value::Dict(h) = home {
        if let Some(Value::List(rows)) = h.get_mut("pending") {
            for row in rows.iter_mut() {
                if let Value::Dict(r) = row {
                    r.insert("confirm".into(), Value::Str(String::new()));
                }
            }
        }
    }
}
