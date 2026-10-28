//! rb_delta: what changed in the research state since this agent session last looked. Every rb command run in an agent
//! session carries, once, what changed that the agent must not silently forget, and the session's seen-store
//! (`.rb/cache/sessions/`, local and gitignored) keeps what it was told. What a session said of its agent is kept
//! beside it, in `sessions/said/`.

use crate::errors::{RBError, Res};
use crate::ledger::Ledger;
use crate::pyjson::{self, Dumps};
use crate::record::{Criterion, Decision, Experiment, Question};
use crate::value::{Dict, DictExt, Value};
use std::collections::HashSet;
use std::path::PathBuf;

/// The status items that are a change in the state of the research, rather than work still to do.
pub const ATTENTION: &[&str] = &[
    "unindexed_folder", "reindex", "check_failed", "audit_failed", "manifest_changed", "call_on_failed_check", "decision_outdated", "undecided",
    "gate_missed", "edited_outside_rb", "id_collision", "merged_outside_rb", "shallow_clone", "drift", "state_newer", "stale", "conflict", "source_changed", "cited_source_changed", "analysis_source_changed",
    "input_retracted", "input_superseded", "retracted_by_agent",
];
pub const QUIET: &[&str] = &["status", "context"];
/// The fact that asks an agent with no model recorded for its writes to say it.
pub const MODEL_UNSAID: &str = "model_unsaid";
pub const MAX_TOLD: usize = 12;
/// Of what rb found, and of the changed files nothing cites, how many one command names: the rest it counts on one line.
pub const NAMED: usize = 5;

fn store_path(led: &Ledger, key: &str) -> PathBuf {
    led.dir.join("cache").join("sessions").join(format!("{}.json", safe_key(key)))
}

fn safe_key(key: &str) -> String {
    key.chars().map(|c| if c.is_ascii_alphanumeric() || "_.@+-".contains(c) { c } else { '_' }).take(120).collect()
}

/// Where a session's said model and provider are kept: a file of their own, which an rb from before them, rewriting
/// the session's store with its own keys, never touches.
fn said_path(led: &Ledger, session: &str) -> PathBuf {
    led.dir.join("cache").join("sessions").join("said").join(format!("{}.json", safe_key(session)))
}

fn load_store(p: &PathBuf) -> Option<Dict> {
    let text = std::fs::read(p).ok()?;
    let text = String::from_utf8(text).ok()?;
    match pyjson::loads(&text).ok()? {
        Value::Dict(d) => Some(d),
        _ => None,
    }
}

/// What a session said of its agent (rb context --model, --provider, the context tool).
pub fn said_in(led: &Ledger, session: &str) -> crate::actor::Said {
    let said = load_store(&said_path(led, session));
    let text = |k: &str| said.as_ref().and_then(|s| s.get_str(k)).and_then(crate::actor::said_id);
    crate::actor::Said { model: text("model"), provider: text("provider") }
}

/// Keep what a session says of its agent for its later writes: a model or a provider said again replaces the one said
/// before.
pub fn remember_said(led: &Ledger, session: &str, model: Option<&str>, provider: Option<&str>) -> Res<()> {
    let path = said_path(led, session);
    let mut said = load_store(&path).unwrap_or_default();
    for (k, v) in [("model", model), ("provider", provider)] {
        if let Some(v) = v {
            said.insert(k.into(), Value::str(v));
        }
    }
    let text = pyjson::dumps_with(&Value::Dict(said), &Dumps::indent(1)).unwrap_or_default();
    crate::ledger::write_atomic(&path, text.as_bytes()).map_err(|e| RBError::could_not_write(&e, &path))
}

/// How an agent with no model recorded for its writes says it, once: (rb_delta's text, what to run, the briefing's
/// sentence). None for a human, and once a model is recorded for this command (said, or RB_MODEL).
pub fn model_hint(led: &Ledger) -> Res<Option<(String, String, String)>> {
    let who = led.actor()?;
    let Some(facts) = led.agent_facts(&who, true) else { return Ok(None) };
    if facts.contains_key("model") {
        return Ok(None);
    }
    if crate::actor::session_for(&who).is_some() || crate::ledger::client().is_some() {
        let how = if led.via == "mcp" { "give the context tool your model id as `model`" } else { "`rb context --model <your model id>`" };
        let text = "No model is recorded for your writes yet";
        return Ok(Some((text.into(), "rb context --model <your model id>".into(), format!("{text}. Say it once: {how}."))));
    }
    let text = "No model is recorded for your writes, and this session has no id to keep one under";
    Ok(Some((text.into(), "RB_MODEL=<your model id> on each command".into(), format!("{text}: give `RB_MODEL=<your model id>` on each command."))))
}

/// The log lines a store or a marker remembers seeing, by digest; None where an earlier rb kept only a count.
pub fn remembered(v: Option<&Value>) -> Option<Vec<String>> {
    match v {
        Some(Value::List(l)) => Some(l.iter().map(pyjson::py_str).collect()),
        _ => None,
    }
}

/// What a look remembers of the log: the lines remembered before, then each of the lines it saw that they lack, so what
/// one look remembered stays the start of what the next does. With none remembered (an earlier rb's count, or no look
/// yet), the lines it saw in the log's order, so a count's first lines are the ones it counted.
pub fn remember(before: Option<Vec<String>>, digests: &[String]) -> Vec<String> {
    let Some(mut seen) = before else { return digests.to_vec() };
    let mut known: HashSet<String> = seen.iter().cloned().collect();
    seen.extend(digests.iter().filter(|d| known.insert(d.to_string())).cloned());
    seen
}

/// str() of a log row's field, or the default when the row has none.
fn field(row: &Dict, key: &str, default: &str) -> String {
    match row.get(key) {
        Some(v) => pyjson::py_str(v),
        None => default.to_string(),
    }
}

/// One line for someone else's write, in rb's words over the record's own: what it changed, its reason and the
/// words given with it, said as the user's only when the user gave them in their own terminal. It never says who wrote
/// it; the fact carries that as `by`.
pub(crate) fn wrote(led: &Ledger, row: &Dict) -> String {
    let op = row.get("op").cloned().unwrap_or(Value::Null);
    let kind = row.get("kind").cloned().unwrap_or(Value::Null);
    let ident = row.get("id").cloned().unwrap_or(Value::Null);
    let words = match row.get("words").filter(|v| v.truthy()) {
        Some(w) if crate::view::words_are_theirs(row.get_str("actor").unwrap_or("")) => format!(" · their words: \"{}\"", crate::view::on_one_line(&pyjson::py_str(w))),
        Some(w) => format!(" · words (as given): \"{}\"", crate::view::on_one_line(&pyjson::py_str(w))),
        None => String::new(),
    };
    let detail: Option<Dict> = match row.get("detail") {
        None | Some(Value::Null) => Some(Dict::new()),
        Some(v) if !v.truthy() => Some(Dict::new()),
        Some(Value::Dict(d)) => Some(d.clone()),
        Some(_) => None,
    };
    // a change a frozen experiment took as an amendment says so; an amend line says it itself
    let amending = match detail.as_ref().and_then(|d| d.get("amendment")) {
        Some(Value::Dict(_)) if op.as_str() != Some("amend") => format!(", amending frozen {}", pyjson::py_str(&ident)),
        _ => String::new(),
    };
    // a write no arm below describes is still told with the user's words
    let fallback = format!("{} {} {}{amending}{words}", pyjson::py_str(&op), pyjson::py_str(&kind), pyjson::py_str(&ident));
    let d_get = |k: &str| -> Option<String> { detail.as_ref().map(|d| d.get(k).map(pyjson::py_str).unwrap_or_default()) };
    // a text the write's detail holds, when it holds one
    let text = |k: &str| -> Option<String> { detail.as_ref()?.get(k)?.as_str().filter(|s| !s.is_empty()).map(str::to_string) };
    // a session's reason is its note, not confirmed, never rb's words; the user's as given
    let actor = row.get_str("actor").unwrap_or("").to_string();
    let reason = |w: &str| crate::view::reason_part(w, &actor, ": ");
    let because = || text("why").map(|w| reason(&w)).unwrap_or_default();
    // the gate a confirmation confirmed, as its line keeps it, never as the gate reads now
    let confirmed_gate = || -> Option<String> {
        let d = detail.as_ref()?;
        Some(crate::view::gate_said(d.get_str("if_holds")?, d.get_str("if_not")?, "", "", ""))
    };
    let op_s = op.as_str();
    let kind_s = kind.as_str();
    let id_s = ident.as_str().map(|s| s.to_string());
    let attempt = || -> Option<String> {
        let id = id_s.clone().unwrap_or_default();
        match (op_s, kind_s) {
            (Some("decide"), _) => {
                let x: Decision = led.load(id_s.as_deref()?).ok()?;
                // a call that waits for a confirmation is told as one, never as made
                let a = led.authority(&x.id).ok()??;
                if !a.is_call() {
                    // a past call reads in its file's voice; the reason the session gave is only its note
                    let why = crate::ledger::session_note(&x.why);
                    return Some(match &x.past {
                        Some(p) => {
                            let src = p.source.as_ref().map(|s| format!(": {}", crate::ledger::source_said(s))).unwrap_or_default();
                            format!("a past call mentioned, not confirmed: {} {} ({}){src}{why}{words}", x.action, x.subject, crate::ledger::past_when(p))
                        }
                        None => format!("proposed, not made: {} {}{why}{words}", x.action, x.subject),
                    });
                }
                let made = if x.action == "investigate" { "put under investigation" } else { crate::ledger::made(&x.action) };
                let confirmed = a.confirms.as_ref().map(|c| format!(" (confirming {c})")).unwrap_or_default();
                let why = crate::ledger::reason_said(&x.why, &x.by, false, a.confirms.is_some());
                Some(format!("{} {made}{confirmed}{why}{words}", x.subject))
            }
            (Some("add"), Some("experiment")) => {
                let e: Experiment = led.load(id_s.as_deref()?).ok()?;
                Some(format!("experiment {id} added: {}{words}", e.title))
            }
            (Some("add"), Some("question")) => {
                let q: Question = led.load(id_s.as_deref()?).ok()?;
                Some(format!("question {id} added: {}{words}", q.text))
            }
            (Some("add"), Some("criterion")) => {
                let c: Criterion = led.load(id_s.as_deref()?).ok()?;
                Some(format!("criterion {id} added on {} ({}): {}{words}", c.experiment, c.rule(), c.statement))
            }
            (Some("add_variant"), Some("experiment")) => {
                Some(format!("variant {}/{} ({}) added{amending}{}{words}", id, text("variant")?, text("role")?, because()))
            }
            (Some("title"), Some("project")) => {
                Some(format!("project retitled from \"{}\" to \"{}\"{}{words}", text("before")?, text("title")?, because()))
            }
            (Some("reword"), Some("question")) => {
                Some(format!("question {id} reworded from \"{}\" to \"{}\"{}{words}", text("before")?, text("text")?, because()))
            }
            (Some("move"), Some("question")) => {
                let to = text("parent").map(|p| format!("under {p}")).unwrap_or_else(|| "to the top".into());
                Some(format!("question {id} moved {to}{}{words}", because()))
            }
            (Some("file"), Some("criterion")) => {
                // what meeting the rule would mean: as given, cleared, or kept from the earlier declaration (none given)
                let meaning = match detail.as_ref()?.get("when_met") {
                    Some(Value::Str(w)) if !w.is_empty() => format!(" (when met: \"{w}\")"),
                    Some(Value::Str(_)) => " (with no interpretation)".into(),
                    _ => String::new(),
                };
                Some(format!("{id} linked to question {}{meaning}{}{words}", text("question")?, because()))
            }
            (Some("unfile"), Some("criterion")) => Some(format!("{id}'s link to question {} withdrawn{}{words}", text("question")?, because())),
            (Some("file"), Some("experiment")) => Some(format!("experiment {id} filed under question {}{words}", text("question")?)),
            (Some("unfile"), Some("experiment")) => Some(format!("experiment {id} taken out of question {}{words}", text("question")?)),
            (Some("relate"), Some("criterion")) => {
                let orientation = text("orientation").map(|o| format!(" (orientation {o})")).unwrap_or_default();
                Some(format!("relation declared: {id} {} {}{orientation}{}{words}", text("kind")?, text("target")?, because()))
            }
            (Some("unrelate"), Some("criterion")) => Some(format!("relation withdrawn: {id} {} {}{}{words}", text("kind")?, text("target")?, because())),
            (Some("relate"), Some("experiment")) => {
                Some(format!("relation declared: experiment {id} {} {}{words}", text("relation")?.replace('_', " "), text("target")?))
            }
            (Some("spec_set"), Some("experiment")) => {
                let name = text("setting")?;
                let change = text("change").map(|c| c.strip_prefix(&format!("setting {name}: ")).unwrap_or(&c).to_string());
                let change = change.map(|c| format!(" ({c})")).unwrap_or_default();
                Some(format!("{id}/{name} set{change}{amending}{}{words}", because()))
            }
            (Some("spec_vary"), Some("experiment")) => {
                let names: Vec<String> = detail.as_ref()?.get("varies")?.as_list()?.iter().map(pyjson::py_str).collect();
                Some(format!("{id} varies {} on purpose{amending}{}{words}", names.join(", "), because()))
            }
            (Some("amend"), _) => match detail.as_ref()?.get("amendment") {
                Some(Value::Dict(a)) => Some(format!("{id} amended ({}){}{words}", a.get_str("change")?, reason(a.get_str("why")?))),
                _ => Some(format!("{id} amended to its spec as it stands{}{words}", because())),
            },
            (Some("record"), Some("run")) => Some(format!("run {id} recorded on {}{words}", d_get("experiment")?)),
            (Some("analyze"), Some("analysis")) => Some(format!("analysis {id} recorded on {}{words}", text("experiment")?)),
            (Some("retract"), _) => {
                // a variant or a setting is taken back on its experiment's line
                let part = ["variant", "setting"].into_iter().find_map(|p| text(p).map(|name| format!("{p} {id}/{name}")));
                let part = part.or_else(|| text("note").map(|n| format!("note {n} on {id}")));
                let what = part.unwrap_or_else(|| format!("{} {id}", pyjson::py_str(&kind)));
                Some(format!("{what} retracted{amending}{}{words}", reason(&d_get("why")?)))
            }
            (Some("note"), _) => Some(format!("note on {id}: {}{words}", d_get("text")?)),
            // the user's confirmation of an experiment's design, with the gate it confirmed, as its line keeps it
            (Some("gate"), Some("experiment")) if text("design").is_some() => {
                let gate = confirmed_gate()?;
                Some(if gate.is_empty() { format!("{id}'s design confirmed") } else { format!("{id}'s design confirmed, with its gate: {gate}") })
            }
            // the user's confirmation of a gate a session set, as its line keeps it
            (Some("gate"), Some("experiment")) if text("confirms").as_deref() == Some("gate") => Some(format!("{id}'s gate confirmed: {}", confirmed_gate()?)),
            (Some("freeze"), _) => Some(format!("{id} frozen{}{words}", because())),
            (Some("reconciled"), _) => Some(format!("rb reconcile --apply: {}{words}", d_get("line")?)),
            _ => None,
        }
    };
    attempt().unwrap_or(fallback)
}

/// The facts this agent session has not been told, for the command just run; nothing outside an agent session, and
/// never an error: a delta that cannot be computed is no reason for a command to fail. `printed` is what a quiet
/// command (rb status, rb context) printed of the state facts, by key, and `printed_lines` the log lines it printed,
/// by digest: only those count as told.
pub fn for_command(command: &str, printed: Option<&[String]>, printed_lines: Option<&[String]>) -> Vec<Value> {
    compute(command, printed.unwrap_or(&[]), printed_lines.unwrap_or(&[])).unwrap_or_default()
}

/// The key a state fact goes by: an open item's code and subject.
pub fn state_key(code: &str, subject: &str) -> String {
    format!("{code}:{subject}")
}

fn fact(kind: &str, code: &str, subject: &str, text: &str, extra: (&str, Value)) -> Value {
    let mut d = crate::dict! {"kind" => kind, "code" => code, "subject" => subject, "text" => text};
    d.insert(extra.0.to_string(), extra.1);
    Value::Dict(d)
}

fn compute(command: &str, printed: &[String], printed_lines: &[String]) -> Res<Vec<Value>> {
    // who is asking, before the record is read: outside an agent session there is nothing to work out
    let root = crate::ledger::find_root(None).ok_or_else(|| crate::errors::RBError::new("E_NO_PROJECT"))?;
    let (via, agent) = crate::ledger::session(None, None);
    if !Ledger::new(&root, &via, agent).actor()?.id.starts_with("agent:") {
        return Ok(Vec::new());
    }
    let led = Ledger::open_continuing(None)?;
    let who = led.actor()?;
    let session = crate::actor::session_for(&who);
    let key = session.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| who.id.clone());
    let path = store_path(&led, &key);
    let store = load_store(&path);
    // taken before the log is read, so a line written while it is read is still one to tell
    let looked = crate::pytime::now();
    let rows = led.log_entries()?;
    let before = store.as_ref().and_then(|s| remembered(s.get("seen_lines")));
    // when the session first looked: this look for its first, and for a store an earlier rb wrote, which never said
    let arrived = store.as_ref().and_then(|s| s.get("arrived")).and_then(|v| v.as_str()).map(str::to_string).unwrap_or(looked);
    // a line written before then is history, wherever a merge or another branch sorts it: only the lines since are told
    // or remembered
    let lately: Vec<(&Dict, String)> = rows.iter().filter(|r| field(r, "at", "") >= arrived).map(|r| (r, crate::ledger::line_digest(r))).collect();
    let st = led.status(None)?;
    let mut facts: indexmap::IndexMap<String, Value> = indexmap::IndexMap::new();
    if let Some(Value::List(open)) = st.get("open") {
        for it in open {
            let code = it.get("code").map(pyjson::py_str).unwrap_or_default();
            if ATTENTION.contains(&code.as_str()) {
                let subject = it.get("subject").map(pyjson::py_str).unwrap_or_default();
                facts.insert(
                    state_key(&code, &subject),
                    fact("state", &code, &subject, &it.get("what").map(pyjson::py_str).unwrap_or_default(), ("do", it.get("do").cloned().unwrap_or(Value::Null))),
                );
            }
        }
    }
    // what rb found, less what this session or the user left out or recorded
    let found = crate::journal::found_all(&led, crate::journal::Seen::Briefing)?;
    let mut found_keys: Vec<String> = Vec::new();
    for f in &found.placed {
        let k = format!("unrecorded:{}", f.key);
        facts.insert(k.clone(), fact("state", "unrecorded", &f.path, &format!("{}: results landed and are not in the record", f.path), ("do", Value::Str("rb reconcile".into()))));
        found_keys.push(k);
    }
    let mut uncited_keys: Vec<String> = Vec::new();
    for u in &found.uncited {
        let k = format!("uncited:{}", u.key);
        facts.insert(k.clone(), fact("state", "uncited_change", &u.path, &u.said(), ("do", Value::Str("rb reconcile".into()))));
        uncited_keys.push(k);
    }
    let seen: HashSet<String> = match store.as_ref().and_then(|s| s.get("seen")) {
        Some(Value::List(l)) => l.iter().map(pyjson::py_str).collect(),
        _ => Default::default(),
    };
    // what a quiet command counted and did not name: named later under its own heading
    let counted: HashSet<String> = match store.as_ref().and_then(|s| s.get("counted")) {
        Some(Value::List(l)) => l.iter().map(pyjson::py_str).collect(),
        _ => Default::default(),
    };
    let quiet = QUIET.contains(&command.split(' ').next().unwrap_or(""));
    // each fact to tell with what it goes by: a state fact's key, a write's log line, or for a line that counts many,
    // each of theirs. The open items come first, then the writes, then what rb found and the changed files nothing
    // cites, so many of those never keep a write from the session
    let mut due: Vec<(Vec<String>, Value)> = Vec::new();
    let marked = |k: &str, v: &Value| -> Value {
        let mut v = v.clone();
        if counted.contains(k) {
            if let Value::Dict(d) = &mut v {
                d.insert("counted".into(), Value::Bool(true));
            }
        }
        v
    };
    let observed: HashSet<&String> = found_keys.iter().chain(&uncited_keys).collect();
    if !quiet {
        for (k, v) in facts.iter().filter(|(k, _)| !seen.contains(*k) && !observed.contains(k)) {
            due.push((vec![k.clone()], marked(k, v)));
        }
        if let Some(s) = &store {
            let fresh: Vec<(&Dict, String)> = match &before {
                Some(lines) => {
                    let known: HashSet<&str> = lines.iter().map(String::as_str).collect();
                    lately.iter().filter(|(_, d)| !known.contains(d.as_str())).map(|(row, d)| (*row, d.clone())).collect()
                }
                // a store an earlier rb wrote keeps a count: read as it read it, this once
                None => {
                    let cursor: i64 = match s.get("cursor") {
                        None | Some(Value::Null) => 0,
                        Some(v) if !v.truthy() => 0,
                        Some(Value::Int(i)) => *i,
                        Some(Value::Bool(b)) => *b as i64,
                        Some(Value::Float(f)) => f.trunc() as i64,
                        Some(Value::Str(t)) => crate::rbyaml::py_strip(t).parse::<i64>().map_err(|_| RBError::msg("E_INTERNAL", ""))?,
                        Some(_) => return Err(RBError::msg("E_INTERNAL", "")),
                    };
                    let n = rows.len() as i64;
                    let start = if cursor < 0 { (n + cursor).max(0) } else { cursor.min(n) } as usize;
                    rows[start..].iter().map(|r| (r, crate::ledger::line_digest(r))).collect()
                }
            };
            for (row, digest) in fresh.into_iter().filter(|(r, _)| !crate::ledger::is_session_line(r)) {
                let row_session = row.get("session").filter(|v| v.truthy()).cloned();
                let same_session = match (&row_session, &session) {
                    (None, None) => true,
                    (Some(a), Some(b)) => a == &Value::Str(b.clone()),
                    _ => false,
                };
                let mine = row.get("actor") == Some(&Value::Str(who.id.clone())) && same_session;
                if !mine && row.get("op") != Some(&Value::Str("init".into())) {
                    due.push((vec![digest], fact("write", &field(row, "op", ""), &field(row, "id", ""), &wrote(&led, row), ("by", row.get("actor").cloned().unwrap_or(Value::Null)))));
                }
            }
        }
        // a few of each by name, and the rest on one line each, counted earlier or not
        for (keys, code, many) in [
            (&found_keys, "unrecorded_more", "results that landed and are not in the record"),
            (&uncited_keys, "uncited_more", "changed .json, .yaml or .toml files that nothing in the record cites"),
        ] {
            let fresh: Vec<&String> = keys.iter().filter(|k| !seen.contains(*k)).collect();
            for k in fresh.iter().take(NAMED) {
                due.push((vec![k.to_string()], marked(k, &facts[k.as_str()])));
            }
            for was_counted in [true, false] {
                let rest: Vec<String> = fresh.iter().skip(NAMED).filter(|k| counted.contains(**k) == was_counted).map(|k| k.to_string()).collect();
                if rest.is_empty() {
                    continue;
                }
                let mut v = fact("state", code, "", &format!("and {} more {many}", rest.len()), ("do", Value::Str("rb reconcile".into())));
                if let Value::Dict(d) = &mut v {
                    d.insert("count".into(), Value::Int(rest.len() as i64));
                    if was_counted {
                        d.insert("counted".into(), Value::Bool(true));
                    }
                }
                due.push((rest, v));
            }
        }
    }
    // what is not told now is told by the session's next command: only what this one told counts as told
    let rest: Vec<(Vec<String>, Value)> = if due.len() > MAX_TOLD { due.split_off(MAX_TOLD) } else { Vec::new() };
    let mut told: Vec<Value> = due.iter().map(|(_, v)| v.clone()).collect();
    if !rest.is_empty() {
        let kinds: HashSet<&str> = rest.iter().filter_map(|(_, f)| f.get_str("kind")).collect();
        let do_ = match (kinds.contains("state"), kinds.contains("write")) {
            (true, false) => "rb reconcile",
            (false, true) => "rb log",
            _ => "rb status",
        };
        let n: usize = rest.iter().map(|(ks, _)| ks.len()).sum();
        told.push(fact("more", "more", "", &format!("and {n} more, which the session's next command tells"), ("do", Value::Str(do_.into()))));
    }
    let untold: HashSet<&str> = rest.iter().flat_map(|(ks, _)| ks.iter().map(String::as_str)).collect();
    // a quiet command tells only what it printed: what it counted, or left unprinted, is still to tell
    let mut keys: Vec<String> = facts.keys().filter(|k| seen.contains(*k) || if quiet { printed.contains(*k) } else { !untold.contains(k.as_str()) }).cloned().collect();
    let still_counted: Vec<String> = facts.keys().filter(|k| !keys.contains(*k) && (counted.contains(*k) || quiet)).cloned().collect();
    // how to say the model comes last, once: rb context says it itself, rb status does not
    if let Some((text, do_, _)) = model_hint(&led)? {
        let asked = format!("{MODEL_UNSAID}:model");
        if !quiet && !seen.contains(&asked) {
            told.push(fact("state", MODEL_UNSAID, "model", &text, ("do", Value::Str(do_))));
        }
        if !quiet || command == "context" || seen.contains(&asked) {
            keys.push(asked);
        }
    }
    keys.sort();
    // the log lines told: every fresh one a command told, and of a quiet command's only those it printed
    let digests: Vec<String> = lately.into_iter().map(|(_, d)| d).filter(|d| if quiet { printed_lines.contains(d) } else { !untold.contains(d.as_str()) }).collect();
    // the count still, for an earlier rb that shares this store
    let doc = crate::obj! {
        "session" => key, "seen" => Value::list_of_str(keys), "cursor" => rows.len() as i64, "arrived" => arrived,
        "seen_lines" => Value::list_of_str(remember(before, &digests)), "counted" => Value::list_of_str(still_counted)
    };
    let text = pyjson::dumps_with(&doc, &Dumps::indent(1)).unwrap_or_default();
    crate::ledger::write_atomic(&path, text.as_bytes()).map_err(|_| RBError::msg("E_INTERNAL", ""))?;
    Ok(told)
}

/// The text form, for the end of a command's output.
pub fn lines(told: &[Value]) -> Vec<String> {
    if told.is_empty() {
        return Vec::new();
    }
    let line = |t: &Value| {
        let text = t.get_str("text").unwrap_or("");
        let kind = t.get_str("kind").unwrap_or("");
        let do_ = t.get("do").filter(|v| v.truthy()).map(pyjson::py_str).unwrap_or_default();
        format!("  - {}{}", text, if !do_.is_empty() && kind == "state" { format!("   ({do_})") } else { String::new() })
    };
    // what an earlier rb status or rb context counted is named under its own heading: it is not new since then
    let counted = |t: &&Value| t.get("counted").map_or(false, |c| c.truthy());
    let more: Vec<&Value> = told.iter().filter(|t| t.get_str("kind") == Some("more")).collect();
    let since: Vec<&Value> = told.iter().filter(|t| !counted(t) && t.get_str("kind") != Some("more")).collect();
    let before: Vec<&Value> = told.iter().filter(counted).collect();
    let mut out = Vec::new();
    if !since.is_empty() {
        out.extend([String::new(), "Since this session last looked:".to_string()]);
        out.extend(since.iter().map(|t| line(t)));
    }
    if !before.is_empty() {
        out.extend([String::new(), "Counted before, named now:".to_string()]);
        out.extend(before.iter().map(|t| line(t)));
    }
    out.extend(more.iter().map(|t| line(t)));
    out
}
