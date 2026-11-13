//! The Overview's view model (UX5 1.1): one value, built once from rb status's data, that every surface draws: rb
//! status's dashboard, rb ui's home, rb open's and a page's Overview, and `overview` in the JSON. It holds what the
//! research asked (the question), what came out so far (the strip of its checks by state and one chart of each result
//! against what its check accepts), what that rests on (four marks) and the decisions that wait. Every element carries
//! its `said`: the words it replaces, for screen readers, `--words` and rb context.

use crate::errors::Res;
use crate::ledger::Ledger;
use crate::pyjson::py_str;
use crate::value::{Dict, Value};

/// A check's state, in rb's order: its square, its label and its key.
pub const STATES: [(&str, &str, &str); 4] = [("meets", "■", "passed"), ("misses", "▲", "failed"), ("undetermined", "◧", "can't tell yet"), ("untested", "□", "no results yet")];

/// The square of a check's state.
pub fn mark_of(state: &str) -> &'static str {
    STATES.iter().find(|s| s.0 == state).map_or("□", |s| s.1)
}

/// The label of a check's state.
pub fn label_of(state: &str) -> &'static str {
    STATES.iter().find(|s| s.0 == state).map_or("no results yet", |s| s.2)
}

/// Rows of the chart a surface shows before it folds the rest into `+n ›`.
pub const CHART_ROWS: usize = 10;
/// Decision rows the Overview shows before `+n ›`.
pub const DECISION_ROWS: usize = 3;

fn s(v: &Value, k: &str) -> String {
    v.get(k).filter(|x| !x.is_null()).map(py_str).unwrap_or_default()
}

fn l<'a>(v: &'a Value, k: &str) -> &'a [Value] {
    match v.get(k) {
        Some(Value::List(x)) => x,
        _ => &[],
    }
}

/// What git took out of the record that a check's reading rests on, in its finding's own words; "" where nothing was.
fn took(f: &Value) -> String {
    l(f, "integrity").iter().find(|p| s(p, "code") == "taken_out_by_git").map(|p| s(p, "text")).unwrap_or_default()
}

fn late(f: &Value) -> bool {
    matches!(s(f, "timing").as_str(), "after_evidence" | "after_exposure")
}

/// The decision codes a person makes: the items rb status lists under Needs judgment.
fn decides(o: &Value) -> bool {
    s(o, "who") == "human" && !matches!(s(o, "code").as_str(), "call_proposed" | "past_call_unconfirmed")
}

/// The strip of `ids`: one square per check, grouped by state, each group with its count and label.
fn strip(ids: &[String], findings: &Dict) -> Value {
    let mut groups = Vec::new();
    let mut said = Vec::new();
    let mut n_late = 0;
    for (state, mark, label) in STATES {
        let checks: Vec<Value> = ids
            .iter()
            .filter_map(|i| findings.get(i).map(|f| (i, f)))
            .filter(|(_, f)| s(f, "rule_outcome") == state)
            .map(|(i, f)| {
                n_late += late(f) as usize;
                crate::obj! {"id" => i.clone(), "late" => late(f), "taken_out" => !took(f).is_empty()}
            })
            .collect();
        if checks.is_empty() {
            continue;
        }
        said.push(format!("{} {label}", checks.len()));
        groups.push(crate::obj! {"state" => state, "mark" => mark, "label" => label, "n" => checks.len(), "checks" => Value::List(checks)});
    }
    let mut said = said.join(", ");
    if n_late > 0 {
        said += &format!("; {n_late} of {} written after their results", ids.len());
    }
    if said.is_empty() {
        said = "no checks yet".into();
    }
    crate::obj! {"groups" => Value::List(groups), "said" => said}
}

/// The checks of question `q` and of every question under it: its own direct links, then each sub-question's, depth
/// first in the record's order, once each. A top-level question's strip counts them all, so every check linked to a
/// question counts in the strip of its top-level question (R21).
fn tree_ids(q: &Value, questions: &[&Value], findings: &Dict) -> Vec<String> {
    fn walk(q: &Value, questions: &[&Value], findings: &Dict, seen: &mut std::collections::BTreeSet<String>, ids: &mut Vec<String>) {
        if !seen.insert(s(q, "id")) {
            return; // a question met twice (a merge can put one under another) is walked once
        }
        for id in direct_ids(q, findings) {
            if !ids.contains(&id) {
                ids.push(id);
            }
        }
        for k in questions.iter().copied().filter(|x| s(x, "parent") == s(q, "id")) {
            walk(k, questions, findings, seen, ids);
        }
    }
    let mut ids = Vec::new();
    walk(q, questions, findings, &mut std::collections::BTreeSet::new(), &mut ids);
    ids
}

/// The checks linked directly to question `q`, in the record's order, once each.
fn direct_ids(q: &Value, findings: &Dict) -> Vec<String> {
    let mut ids: Vec<String> = Vec::new();
    for x in l(q.get("inquiry").unwrap_or(&Value::Null), "direct") {
        let id = s(x, "criterion");
        if findings.contains_key(&id) && !ids.contains(&id) {
            ids.push(id);
        }
    }
    ids
}

/// Where a check's shaded zone lies: its edge, the band's half width, and its side (`ge`, `le`, `gt`, `lt`, `eq`).
pub(crate) fn zone(c: &crate::record::Criterion, higher_better: Option<bool>) -> (Option<f64>, f64, &'static str) {
    match c.comparator.as_str() {
        "at_least" => (c.target, 0.0, "ge"),
        "at_most" => (c.target, 0.0, "le"),
        "equals" => (c.target, c.tolerance.unwrap_or(0.0), "eq"),
        "equivalent" => (Some(0.0), c.margin.unwrap_or(0.0).abs(), "eq"),
        "noninferior" => match higher_better {
            Some(false) => (c.margin.map(f64::abs), 0.0, "le"),
            _ => (c.margin.map(|m| -m.abs()), 0.0, "ge"),
        },
        "better" => (Some(0.0), 0.0, if higher_better == Some(false) { "lt" } else { "gt" }),
        _ => (None, 0.0, ""),
    }
}

/// The measure a check reads, as its panel's head: "int8 − fp32, top1", "fp32 top1", "val_loss, change".
pub(crate) fn measure(c: &crate::record::Criterion) -> String {
    let m = c.metric.as_str();
    if let Some(rest) = m.strip_prefix("change.") {
        format!("{rest}, change")
    } else if !c.reference.is_empty() {
        format!("{} \u{2212} {}, {m}", if c.subject.is_empty() { "candidate" } else { &c.subject }, c.reference)
    } else if !c.subject.is_empty() {
        format!("{} {m}", c.subject)
    } else {
        m.replace('.', " ")
    }
}

fn selectors(c: &crate::record::Criterion) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for d in [&c.r#where, &c.subject_where] {
        for (k, v) in d.iter() {
            if !out.iter().any(|(x, _)| x == k) {
                out.push((k.clone(), py_str(v)));
            }
        }
    }
    out
}

/// The view model. `st` is rb status's data, as `led.status()` gives it.
/// A check's statement as the overview names it: where the words a session gave are that statement, those words said
/// once, as given, as every line says them (`view::statement_said`), the same for every reader, so no surface says a
/// session's words bare; the user's own words that are the statement are the statement.
fn statement_of(f: &Value) -> String {
    let statement = s(f, "statement");
    let declared = f.get("declared").filter(|d| d.truthy());
    let words = declared.map(|d| s(d, "words")).unwrap_or_default();
    let by = declared.map(|d| s(d, "by")).unwrap_or_default();
    if crate::view::words_are_theirs(&by) {
        return statement;
    }
    match crate::view::statement_said(&statement, &words, &by) {
        Some(_) => format!("words (as given): \"{}\"", crate::view::on_one_line(&words)),
        None => statement,
    }
}

pub fn of(led: &Ledger, st: &Value) -> Res<Value> {
    of_story(led, st, None)
}

/// The view model with question `q` as its story (`rb status <exp>`: the question the experiment is filed under), the
/// first open top-level question where `q` is None or not in the record.
pub fn of_question(led: &Ledger, st: &Value, q: Option<&str>) -> Res<Value> {
    of_story(led, st, q)
}

fn of_story(led: &Ledger, st: &Value, chosen: Option<&str>) -> Res<Value> {
    let mut findings = Dict::new();
    for f in l(st, "findings") {
        findings.insert(s(f, "criterion"), f.clone());
    }
    let questions: Vec<&Value> = l(st, "questions").iter().filter(|q| !q.get("retracted").map_or(false, Value::truthy)).collect();
    let tops: Vec<&Value> = questions.iter().copied().filter(|q| s(q, "parent").is_empty()).collect();
    let story = chosen
        .and_then(|c| questions.iter().copied().find(|q| s(q, "id") == c))
        .or_else(|| tops.iter().copied().find(|q| s(q, "state") == "open"))
        .or_else(|| tops.first().copied());
    let directions: std::collections::HashMap<String, Option<bool>> = l(st, "metrics")
        .iter()
        .flat_map(|m| {
            let dir = match s(m, "direction").as_str() {
                "maximize" => Some(true),
                "minimize" => Some(false),
                _ => None,
            };
            std::iter::once(s(m, "id")).chain(l(m, "aliases").iter().map(py_str)).map(move |n| (n, dir)).collect::<Vec<_>>()
        })
        .collect();
    let exps: Dict = l(st, "experiments").iter().map(|e| (s(e, "id"), e.clone())).collect();
    let mut doc = Dict::new();
    doc.insert("project".into(), st.get("project").map(|p| s(p, "title")).unwrap_or_default().into());
    // what git took out of the record, in the one line every surface says it; "" where nothing was (S7 E1)
    doc.insert("taken_out".into(), st.get("taken_out").map(|t| s(t, "said")).unwrap_or_default().into());
    let ids = story.map(|q| tree_ids(q, &questions, &findings)).unwrap_or_default();
    // the author's plain lines (PLAIN.md), the file's words with their place in it: null where there is none
    let plain = crate::plainfile::of(led);
    let plain_of = |id: &str, k: &str| {
        plain.as_ref().and_then(|p| crate::plainfile::line_for(p, "question", id, "")).and_then(|l| l.get(k)).cloned().unwrap_or(Value::Null)
    };
    doc.insert(
        "question".into(),
        match story {
            Some(q) => crate::obj! {
                "id" => s(q, "id"), "text" => s(q, "text"), "plain" => plain_of(&s(q, "id"), "text"), "plain_cite" => plain_of(&s(q, "id"), "cite"),
                "state" => s(q, "state"),
                "subs" => questions.iter().filter(|x| s(x, "parent") == s(q, "id")).count(),
            },
            None => Value::Null,
        },
    );
    doc.insert("strip".into(), strip(&ids, &findings));
    // the chart: one row per check with a result, grouped by measure, each against what its check accepts
    let mut rows: Vec<(String, Vec<(String, String)>, Dict)> = Vec::new();
    for id in &ids {
        let f = &findings[id];
        let state = s(f, "rule_outcome");
        let cmp = f.get("comparison").cloned().unwrap_or(Value::Null);
        let est = cmp.get("estimate").and_then(Value::as_f64).filter(|v| v.is_finite());
        if state == "untested" || est.is_none() {
            continue;
        }
        let Ok(c) = led.load::<crate::record::Criterion>(id) else { continue };
        let metric = c.metric.strip_prefix("change.").unwrap_or(&c.metric).to_string();
        let (edge, tol, side) = zone(&c, directions.get(&metric).copied().flatten());
        let num = |k: &str| cmp.get(k).and_then(Value::as_f64).filter(|v| v.is_finite());
        let says = f.get("says").cloned().unwrap_or(Value::Null);
        let difference = !c.reference.is_empty() || c.metric.starts_with("change.");
        let unit = crate::card::metric_unit(led, &c.metric)?;
        let mut row = crate::dict! {
            "id" => id.clone(), "group" => measure(&c), "label" => "",
            "est" => Value::opt_f64(est), "lo" => Value::opt_f64(num("lo")), "hi" => Value::opt_f64(num("hi")),
            "n" => cmp.get("n").cloned().unwrap_or(Value::Null), "unit" => s(&cmp, "unit"),
            "threshold" => Value::opt_f64(edge), "tol" => tol, "side" => side,
            "difference" => !c.reference.is_empty() || c.metric.starts_with("change."),
            "state" => state.clone(), "late" => late(f), "taken_out" => took(f),
            "said" => format!("{}; {}", s(&says, "comparison"), s(&says, "headline")),
        };
        // a check of a claim someone stated (a cited number or ordering): its row carries the chip `claim`
        row.insert("claim".into(), (c.origin == "cited").into());
        // the unit its numbers are in, as the card says it: a difference of a percentage is in points
        row.insert("measure_unit".into(), crate::card::unit_said(&unit, difference).into());
        row.insert("statement".into(), statement_of(f).into());
        rows.push((measure(&c), selectors(&c), row));
    }
    // a row's label: the selector values that tell it from the others of its panel, else its check's statement
    let mut order: Vec<(String, Vec<String>)> = Vec::new();
    for (g, sel, _) in &rows {
        let key = (g.clone(), { let mut k: Vec<String> = sel.iter().map(|x| x.0.clone()).collect(); k.sort(); k });
        if !order.contains(&key) {
            order.push(key);
        }
    }
    // a measure's rows stay together, in one panel, where its checks select by different settings (a sub-question's
    // checks beside the question's own): its keys in the order its group first appears
    let first_of = |g: &String| order.iter().position(|k| &k.0 == g).unwrap_or(0);
    let mut order: Vec<(usize, (String, Vec<String>))> = order.iter().map(|k| (first_of(&k.0), k.clone())).collect();
    order.sort_by_key(|x| x.0);
    let order: Vec<(String, Vec<String>)> = order.into_iter().map(|x| x.1).collect();
    let mut placed: Vec<(Vec<(String, String)>, Vec<(String, String)>, Dict)> = Vec::new();
    for key in &order {
        let mut members: Vec<&(String, Vec<(String, String)>, Dict)> =
            rows.iter().filter(|(g, sel, _)| g == &key.0 && { let mut k: Vec<String> = sel.iter().map(|x| x.0.clone()).collect(); k.sort(); &k == &key.1 }).collect();
        let vary: Vec<String> = key.1.iter().filter(|k| members.iter().map(|m| m.1.iter().find(|x| &&x.0 == k).map(|x| x.1.clone())).collect::<std::collections::HashSet<_>>().len() > 1).cloned().collect();
        // numbers in order of their value where every label is one
        members.sort_by(|a, b| {
            let n = |m: &&(String, Vec<(String, String)>, Dict)| vary.iter().map(|k| m.1.iter().find(|x| &x.0 == k).and_then(|x| x.1.parse::<f64>().ok()).unwrap_or(0.0)).collect::<Vec<_>>();
            n(a).partial_cmp(&n(b)).unwrap_or(std::cmp::Ordering::Equal)
        });
        for m in members {
            let picked: Vec<(String, String)> = vary.iter().filter_map(|k| m.1.iter().find(|x| &x.0 == k).cloned()).collect();
            placed.push((picked, m.1.clone(), m.2.clone()));
        }
    }
    // a row's name never repeats another's: where two read the same, each says the selector values that tell them
    // apart, and where none does, its check's id; a name is never cut (the UX lead, Nov 11: a short name, whole)
    let id_of = |r: &Dict| s(&Value::Dict(r.clone()), "id");
    let joined = |xs: Vec<String>| xs.join(" \u{b7} ");
    let stated = |r: &Dict| s(&Value::Dict(r.clone()), "statement");
    let first: Vec<String> = placed.iter().map(|(p, _, r)| if p.is_empty() { stated(r) } else { joined(p.iter().map(|x| x.1.clone()).collect()) }).collect();
    let mut names = first.clone();
    for i in 0..placed.len() {
        let twins: Vec<usize> = (0..placed.len()).filter(|j| *j != i && first[*j] == first[i]).collect();
        if twins.is_empty() {
            continue;
        }
        let (picked, sel, _) = &placed[i];
        // the values its twins hold otherwise, after its own
        let differs = |k: &str, v: &str| twins.iter().any(|j| placed[*j].1.iter().find(|x| x.0 == k).map_or(false, |x| x.1 != v));
        let more: Vec<String> = sel.iter().filter(|(k, v)| !picked.iter().any(|p| &p.0 == k) && differs(k, v)).map(|x| x.1.clone()).collect();
        if !picked.is_empty() && !more.is_empty() {
            names[i] = joined(picked.iter().map(|x| x.1.clone()).chain(more).collect());
        }
    }
    for i in 0..placed.len() {
        if names[i].is_empty() || (0..placed.len()).any(|j| j != i && names[j] == names[i]) {
            names[i] = format!("{} ({})", names[i], id_of(&placed[i].2)).trim_start().to_string();
        }
    }
    let mut chart: Vec<Value> = Vec::new();
    for (i, (_, _, row)) in placed.iter().enumerate() {
        let mut row = row.clone();
        // `short`: a name of selector values, which fits beside its bar; else the check's statement, which a surface
        // wraps above it, never cut
        row.insert("label".into(), names[i].clone().into());
        row.insert("short".into(), (!placed[i].0.is_empty() && names[i] != stated(&placed[i].2)).into());
        row.shift_remove("statement");
        chart.push(Value::Dict(row));
    }
    doc.insert("chart".into(), Value::List(chart));
    // what it rests on: when the checks were written, the cited numbers reproduced, the runs and tries, the code kept
    let tested: Vec<&Value> = ids.iter().map(|i| &findings[i]).filter(|f| s(f, "rule_outcome") != "untested").collect();
    let n_late = tested.iter().filter(|f| late(f)).count();
    let cited: Vec<&Value> = ids.iter().map(|i| &findings[i]).filter(|f| s(f, "origin") == "cited").collect();
    let reproduced = cited.iter().filter(|f| s(f, "rule_outcome") == "meets").count();
    let mut tries: Vec<String> = Vec::new();
    for i in &ids {
        let e = s(&findings[i], "experiment");
        if !tries.contains(&e) {
            tries.push(e);
        }
    }
    let runs: i64 = tries.iter().filter_map(|e| exps.get(e)).map(|e| e.get("run_count").and_then(Value::as_i64).unwrap_or(0)).sum();
    let mut kept = 0;
    for e in &tries {
        let rerun: Vec<Value> = match exps.get(e).and_then(|x| x.get("rerun")) {
            Some(Value::List(r)) => r.clone(),
            _ => match led.load::<crate::record::Experiment>(e) {
                Ok(x) => led.rerunnability(&x)?.into_iter().map(Value::Dict).collect(),
                Err(_) => Vec::new(),
            },
        };
        if rerun.iter().any(|r| s(r, "what") == "code" && s(r, "state") == "ok") {
            kept += 1;
        }
    }
    let qid = story.map(|q| s(q, "id")).unwrap_or_default();
    // each a label, a ✓ where it holds whole, a ✗ where it holds for none, and its count (the UX lead, Nov 11)
    let ok = |n: usize, of: usize| -> Value { if of == 0 { Value::Null } else if n == of { true.into() } else if n == 0 { false.into() } else { Value::Null } };
    let first = tested.len() - n_late;
    let mut rests = vec![crate::obj! {"key" => "planned", "runs" => Value::Null, "label" => "planned first", "ok" => ok(first, tested.len()), "n" => first, "of" => tested.len(),
        "said" => format!("{first} of {} checks were written before their results", tested.len()), "opens" => qid.clone()}];
    if !cited.is_empty() {
        rests.push(crate::obj! {"key" => "reproduced", "runs" => Value::Null, "label" => "reproduced", "ok" => ok(reproduced, cited.len()), "n" => reproduced, "of" => cited.len(),
            "said" => format!("{reproduced} of {} cited numbers reproduced", cited.len()), "opens" => qid.clone()});
    }
    rests.push(crate::obj! {"key" => "tries", "label" => "tries", "ok" => Value::Null, "n" => tries.len(), "of" => Value::Null, "runs" => runs,
        "said" => format!("{runs} runs in {} experiment{}", tries.len(), if tries.len() == 1 { "" } else { "s" }), "opens" => "runs"});
    rests.push(crate::obj! {"key" => "code", "runs" => Value::Null, "label" => "code kept", "ok" => ok(kept, tries.len()), "n" => kept, "of" => tries.len(),
        "said" => format!("the code is kept whole for {kept} of {} experiments", tries.len()), "opens" => tries.first().cloned().unwrap_or_default()});
    // what they rest on reads otherwise while git has taken out a write a check rests on: each mark says so
    let taken = ids.iter().filter(|i| !took(&findings[*i]).is_empty()).count();
    let said_taken = if taken == 0 { String::new() } else { format!("{taken} of {} checks rest on a write git took out of the record", ids.len()) };
    for r in rests.iter_mut() {
        if let Value::Dict(d) = r {
            d.insert("taken_out".into(), said_taken.clone().into());
        }
    }
    doc.insert("rests".into(), Value::List(rests));
    // the decisions that wait: the calls to make, then what waits for a confirmation
    let mut title = Dict::new();
    for e in l(st, "experiments") {
        title.insert(s(e, "id"), Value::str(s(e, "title")));
    }
    for q in &questions {
        title.insert(s(q, "id"), Value::str(s(q, "text")));
    }
    for (id, f) in &findings {
        title.insert(id.clone(), Value::str(statement_of(f)));
    }
    let titled = |subject: &str| title.get(subject).map(py_str).unwrap_or_else(|| subject.to_string());
    let mut decisions: Vec<Value> = Vec::new();
    for o in l(st, "open").iter().filter(|o| decides(o)) {
        let subject = s(o, "subject");
        let mark = findings.get(&subject).map(|f| mark_of(&s(f, "rule_outcome"))).unwrap_or("");
        let mut row = crate::dict! {"kind" => "decide", "chip" => "decide", "code" => s(o, "code"), "subject" => subject.clone(), "title" => titled(&subject),
            "mark" => mark, "cmd" => "", "do" => s(o, "do"), "said" => s(o, "what")};
        // what git took out of the record that this decision rests on, "" where nothing was
        row.insert("taken_out".into(), s(o, "taken_out").into());
        decisions.push(Value::Dict(row));
    }
    for p in l(st, "pending") {
        let subject = s(p, "subject");
        let id = s(p, "id");
        let id = if id.is_empty() { s(p, "decision") } else { id };
        decisions.push(crate::obj! {"kind" => "confirm", "chip" => "confirm", "code" => if p.get("past").map_or(false, Value::truthy) { "past_call_unconfirmed" } else { "call_proposed" },
            "subject" => subject.clone(), "title" => titled(&subject), "mark" => s(p, "action"), "cmd" => format!("rb confirm {id}"), "do" => format!("rb confirm {id}"),
            "said" => format!("the call to {} {subject} waits for one confirmation", s(p, "action")), "taken_out" => ""});
    }
    let designs = l(st, "designs_pending");
    if !designs.is_empty() {
        let n = designs.len();
        let subject = designs.iter().map(|x| s(x, "experiment")).collect::<Vec<_>>().join(" · ");
        decisions.push(crate::obj! {"kind" => "confirm", "chip" => "confirm", "code" => "design_pending", "subject" => subject,
            "title" => format!("{n} plan{}", if n == 1 { "" } else { "s" }), "mark" => "", "cmd" => "rb confirm", "do" => "rb confirm",
            "said" => format!("the design of {n} experiment{} is not confirmed", if n == 1 { "" } else { "s" }), "taken_out" => ""});
    }
    // the calls made: never one a write to which git took out (its retraction, its confirmation), which reads neither
    // as made nor as not; those, and the calls git took out whole, are counted apart and said beside the count
    let calls = l(st, "decisions");
    let made = calls
        .iter()
        .filter(|d| s(d, "taken_out").is_empty())
        .filter(|d| !d.get("retracted").map_or(false, Value::truthy))
        .filter(|d| d.get("authority").map(|a| matches!(s(a, "state").as_str(), "direct" | "confirmed")).unwrap_or(false))
        .count();
    let listed: std::collections::BTreeSet<String> = calls.iter().map(|d| s(d, "id")).collect();
    let gone = st
        .get("taken_out")
        .map(|t| l(t, "records").iter().filter(|r| s(r, "kind") == "decision" && !listed.contains(&s(r, "id"))).count())
        .unwrap_or(0);
    let made_taken = calls.iter().filter(|d| !s(d, "taken_out").is_empty()).count() + gone;
    doc.insert("decisions".into(), Value::List(decisions));
    doc.insert("made".into(), made.into());
    doc.insert("made_taken_out".into(), made_taken.into());
    let others: Vec<Value> = tops
        .iter()
        .filter(|q| Some(s(q, "id")) != story.map(|x| s(x, "id")))
        .map(|q| crate::obj! {"id" => s(q, "id"), "text" => s(q, "text"), "plain" => plain_of(&s(q, "id"), "text"), "plain_cite" => plain_of(&s(q, "id"), "cite"),
            "state" => s(q, "state"), "strip" => strip(&tree_ids(q, &questions, &findings), &findings)})
        .collect();
    doc.insert("others".into(), Value::List(others));
    // every plain line, each marked as the file's words (`voice: file`), for the names, the project and the labels
    doc.insert("plain".into(), plain.clone().unwrap_or(Value::Null));
    doc.insert("counts".into(), crate::obj! {"questions" => questions.len(), "checks" => findings.len(), "runs" => l(st, "runs").len()});
    // what every surface prints of each element, worked out once, so a terminal, a screen and a page say it alike
    if let Some(Value::Dict(sd)) = doc.get_mut("strip") {
        let text = strip_text(&Value::Dict(sd.clone()));
        sd.insert("text".into(), text.into());
    }
    if let Some(Value::List(rows)) = doc.get_mut("chart") {
        for r in rows.iter_mut() {
            let (est, range) = row_numbers(r);
            let unit = s(r, "measure_unit");
            let result = with_unit(&est, &unit);
            let edge = edge_text(r);
            if let Value::Dict(d) = r {
                d.insert("est_text".into(), est.into());
                d.insert("range_text".into(), range.into());
                d.insert("result_text".into(), result.into());
                d.insert("edge_text".into(), edge.into());
            }
        }
    }
    if let Some(Value::List(rs)) = doc.get_mut("rests") {
        for r in rs.iter_mut() {
            let text = rest_said(r, false);
            if let Value::Dict(d) = r {
                d.insert("text".into(), text.into());
            }
        }
    }
    if let Some(Value::List(os)) = doc.get_mut("others") {
        for o in os.iter_mut() {
            if let Value::Dict(od) = o {
                if let Some(Value::Dict(sd)) = od.get_mut("strip") {
                    let text = strip_text(&Value::Dict(sd.clone()));
                    sd.insert("text".into(), text.into());
                }
            }
        }
    }
    Ok(Value::Dict(doc))
}

fn f(row: &Value, k: &str) -> Option<f64> {
    row.get(k).and_then(Value::as_f64).filter(|v| v.is_finite())
}

/// The decimals a chart row's numbers are said in: two at least, more where two would put a number on or past an
/// edge of its check (its number, its band's sides, 0 for a difference) it does not reach, six at most.
pub fn places(row: &Value) -> usize {
    let mut edges: Vec<f64> = Vec::new();
    if let Some(t) = f(row, "threshold") {
        let tol = f(row, "tol").unwrap_or(0.0);
        edges.extend([t, t - tol, t + tol]);
    }
    if row.get("difference").and_then(Value::as_bool).unwrap_or(false) {
        edges.push(0.0);
    }
    let xs: Vec<f64> = ["est", "lo", "hi"].iter().filter_map(|k| f(row, k)).collect();
    (2..=6)
        .find(|p| {
            let m = 10f64.powi(*p as i32);
            xs.iter().all(|x| edges.iter().all(|e| {
                let r = (x * m).round() / m;
                (x - e).abs() < 1e-12 || (r - e).signum() == (x - e).signum() && (r - e).abs() > 1e-12
            }))
        })
        .unwrap_or(6)
}

/// A chart number in `p` decimals, a difference signed, with a true minus.
pub fn num(v: f64, p: usize, signed: bool) -> String {
    let t = format!("{v:.p$}");
    let t = if t.trim_start_matches('-').chars().all(|c| c == '0' || c == '.') { t.trim_start_matches('-').to_string() } else { t };
    let t = if signed && !t.starts_with('-') && v != 0.0 { format!("+{t}") } else { t };
    t.replace('-', "\u{2212}")
}

/// A row's numbers as its end says them: the result, and its range where it has one ("−0.15", "−0.27…−0.04").
pub fn row_numbers(row: &Value) -> (String, String) {
    let p = places(row);
    let signed = row.get("difference").and_then(Value::as_bool).unwrap_or(false);
    let est = f(row, "est").map(|v| num(v, p, signed)).unwrap_or_default();
    let range = match (f(row, "lo"), f(row, "hi")) {
        (Some(lo), Some(hi)) if hi > lo => format!("{}\u{2026}{}", num(lo, p, signed), num(hi, p, signed)),
        _ => String::new(),
    };
    (est, range)
}

/// A number with its unit, as the card says it: `90.48%`, `+3.60 points`, `7.08 ms`; the number alone where the record
/// has no unit.
pub fn with_unit(n: &str, unit: &str) -> String {
    match unit {
        "" => n.to_string(),
        _ if n.is_empty() => String::new(),
        "%" | "percent" => format!("{n}%"),
        u => format!("{n} {u}"),
    }
}

/// The check's number where its zone's solid edge is, as the card's band formatter says it (`−1`, `49.6`, `5.395 to
/// 5.405` for a band), with the words that say which side passes: `from −1`, `up to 0.05`, `above 0`, `below 0`,
/// `5.395 to 5.405`. "" where the check has no number.
pub fn edge_text(row: &Value) -> String {
    let Some(t) = f(row, "threshold") else { return String::new() };
    let signed = row.get("difference").and_then(Value::as_bool).unwrap_or(false);
    let tol = f(row, "tol").unwrap_or(0.0);
    let n = |v: f64| crate::card::band_number(v, signed && v != 0.0);
    match s(row, "side").as_str() {
        "ge" => format!("from {}", n(t)),
        "le" => format!("up to {}", n(t)),
        "gt" => format!("above {}", n(t)),
        "lt" => format!("below {}", n(t)),
        _ if tol > 0.0 => {
            let (a, b) = crate::card::band_ends(t, tol, signed);
            format!("{a} to {b}")
        }
        _ => n(t),
    }
}

/// A rest's mark: ✓ where it holds whole, ✗ where it holds for none, nothing between (`ascii`: + and x).
pub fn ok_mark(r: &Value, ascii: bool) -> &'static str {
    match r.get("ok").and_then(Value::as_bool) {
        Some(true) => if ascii { "+" } else { "\u{2713}" },
        Some(false) => if ascii { "x" } else { "\u{2717}" },
        None => "",
    }
}

/// A rest as every surface says it: "planned first ✗ 0 of 9", "reproduced 2 of 3", "tries 2 · 22 runs", "code kept ✗
/// 0 of 2".
pub fn rest_said(r: &Value, ascii: bool) -> String {
    let mark = ok_mark(r, ascii);
    let mark = if mark.is_empty() { String::new() } else { format!(" {mark}") };
    let n = r.get("n").and_then(Value::as_i64).unwrap_or(0);
    let label = s(r, "label");
    if s(r, "key") == "tries" {
        let runs = r.get("runs").and_then(Value::as_i64).unwrap_or(0);
        return format!("{label} {n} \u{b7} {runs} run{}", if runs == 1 { "" } else { "s" });
    }
    match r.get("of").and_then(Value::as_i64) {
        Some(of) => format!("{label}{mark} {n} of {of}"),
        None => format!("{label}{mark} {n}"),
    }
}

// ---------------------------------------------------------------- the terminal's form (rb status, rb ui)

/// How a terminal draws the Overview: its width, ASCII in place of the glyphs, each figure's `said` under it
/// (`--words`), and the most lines it may take (0: no bound).
#[derive(Clone, Copy, Debug)]
pub struct Text {
    pub width: usize,
    pub ascii: bool,
    pub words: bool,
    pub lines: usize,
}

const ASCII: [(&str, &str); 13] = [
    ("■", "#"), ("▲", "^"), ("◧", "%"), ("□", "."), ("●", "*"), ("○", "o"), ("├", "|"), ("┤", "|"), ("─", "-"), ("░", "~"), ("┆", ":"), ("‾", "_"), ("−", "-"),
];

/// `t` in ASCII where the terminal asks for it.
pub fn plain(t: &str, ascii: bool) -> String {
    if !ascii {
        return t.to_string();
    }
    let mut out = t.to_string();
    for (g, a) in ASCII {
        out = out.replace(g, a);
    }
    out.replace('\u{2713}', "+").replace('\u{2717}', "x").replace('\u{2026}', "..").replace('\u{b7}', "-")
}

fn cols(t: &str) -> usize {
    t.chars().count()
}

/// `t` broken into lines of at most `w` columns at spaces, a word longer than a line kept whole.
pub fn wrap(t: &str, w: usize) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for word in t.split_whitespace() {
        match out.last_mut() {
            Some(l) if cols(l) + 1 + cols(word) <= w => {
                l.push(' ');
                l.push_str(word);
            }
            _ => out.push(word.to_string()),
        }
    }
    out
}

/// The strip as text: each group its squares, its count and its label ("■■■■ 4 passed  ▲ 1 failed").
pub fn strip_text(s: &Value) -> String {
    l(s, "groups")
        .iter()
        .map(|g| format!("{} {} {}", s_(g, "mark").repeat(l(g, "checks").len()), l(g, "checks").len(), s_(g, "label")))
        .collect::<Vec<_>>()
        .join("  ")
}

fn s_(v: &Value, k: &str) -> String {
    s(v, k)
}

/// The domain of a panel's rows: their numbers, their checks' edges and 0 for a difference, padded.
pub fn domain(rows: &[&Value]) -> (f64, f64) {
    let mut dom: Vec<f64> = Vec::new();
    for r in rows {
        dom.extend(["est", "lo", "hi", "threshold"].iter().filter_map(|k| f(r, k)));
        if let (Some(t), Some(tol)) = (f(r, "threshold"), f(r, "tol")) {
            dom.extend([t - tol, t + tol]);
        }
        if r.get("difference").and_then(Value::as_bool).unwrap_or(false) {
            dom.push(0.0);
        }
    }
    let (lo, hi) = dom.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), v| (a.min(*v), b.max(*v)));
    let pad = if hi > lo { (hi - lo) * 0.15 } else if lo != 0.0 { lo.abs() * 0.05 } else { 0.5 };
    (lo - pad, hi + pad)
}

/// One row's bar in `w` cells over [lo, hi]: ░ what its check accepts, ┆ its check's number, ├─●─┤ the range and the
/// result, ○ a result with no range yet.
pub fn bar(r: &Value, lo: f64, hi: f64, w: usize) -> String {
    let w = w.max(8);
    let x = |v: f64| (((v - lo) / (hi - lo)) * (w - 1) as f64).round().clamp(0.0, (w - 1) as f64) as usize;
    let mut cells: Vec<&str> = vec![" "; w];
    if let Some(t) = f(r, "threshold") {
        let tol = f(r, "tol").unwrap_or(0.0);
        let span = match s(r, "side").as_str() {
            "ge" | "gt" => x(t)..=w - 1,
            "le" | "lt" => 0..=x(t),
            _ => x(t - tol)..=x(t + tol),
        };
        for i in span {
            cells[i] = "░";
        }
    }
    let est = f(r, "est").unwrap_or(0.0);
    match (f(r, "lo"), f(r, "hi")) {
        (Some(a), Some(b)) if b > a => {
            let (i, j) = (x(a), x(b));
            for c in cells.iter_mut().take(j + 1).skip(i) {
                *c = "─";
            }
            cells[i] = "├";
            cells[j] = "┤";
            cells[x(est)] = "●";
        }
        _ => cells[x(est)] = "○",
    }
    if let Some(t) = f(r, "threshold") {
        if matches!(cells[x(t)], " " | "░") {
            cells[x(t)] = "┆";
        }
    }
    cells.concat()
}

/// What a chart row or a decision says under it where it rests on a write git took out of the record.
pub const TAKEN_ROW: &str = "(rests on a write git took out of the record)";

/// What the Decisions heading says beside the calls made, of the calls that rest on a write git took out ("" for none).
pub fn made_taken_said(n: i64) -> String {
    if n == 0 { String::new() } else { format!("{n} call{} rest{} on a write git took out", if n == 1 { "" } else { "s" }, if n == 1 { "s" } else { "" }) }
}

/// What a decision row says on its first line, beside the subject, where it rests on a write git took out.
pub const TAKEN_DECISION: &str = "(rests on a write git took out)";

/// The chart as text lines in `width` columns, at most `most` lines (0: all), and how many rows it left out.
pub fn chart_text(chart: &[Value], width: usize, most: usize) -> (Vec<String>, usize) {
    let rows: Vec<&Value> = chart.iter().collect();
    let mut out: Vec<String> = Vec::new();
    let mut shown = 0;
    // the label column is as wide as the widest name of selector values, a third of the line at most
    let labw = rows.iter().filter(|r| r.get("short").and_then(Value::as_bool).unwrap_or(false)).map(|r| cols(&s(r, "label"))).max().unwrap_or(0).min(width / 3);
    let mut i = 0;
    'panels: while i < rows.len() {
        let head = s(rows[i], "group");
        let mut j = i;
        while j < rows.len() && s(rows[j], "group") == head {
            j += 1;
        }
        let panel = &rows[i..j];
        let (lo, hi) = domain(panel);
        if most > 0 && out.len() + 2 > most {
            break;
        }
        // the check's number: once in the panel's head where its rows share it, else on each row
        let edges: Vec<String> = panel.iter().map(|r| s(r, "edge_text")).collect();
        let shared = !edges[0].is_empty() && edges.iter().all(|e| e == &edges[0]);
        out.push(if shared { format!("  {head}  \u{2591} {}", edges[0]) } else { format!("  {head}") });
        for r in panel {
            let (est, range) = row_numbers(r);
            let est = with_unit(&est, &s(r, "measure_unit"));
            let edge = if shared || s(r, "edge_text").is_empty() { String::new() } else { format!("  \u{2591} {}", s(r, "edge_text")) };
            let claim = if r.get("claim").and_then(Value::as_bool).unwrap_or(false) { " [claim]" } else { "" };
            let mark = format!("{}{}", mark_of(&s(r, "state")), if r.get("late").and_then(Value::as_bool).unwrap_or(false) { "‾" } else { " " });
            let id = s(r, "id");
            let short = r.get("short").and_then(Value::as_bool).unwrap_or(false);
            let label = s(r, "label");
            let taken = !s(r, "taken_out").is_empty();
            // a row resting on a write git took out has its label above it, and under the label the line that says so,
            // so the row reads under what it rests on
            let above: Vec<String> = if short && cols(&label) <= labw && !taken { Vec::new() } else { wrap(&label, width.saturating_sub(4)) };
            let need = above.len() + 1 + usize::from(taken);
            if most > 0 && out.len() + need > most {
                break 'panels;
            }
            for a in &above {
                out.push(format!("    {a}"));
            }
            if taken {
                out.push(format!("    {TAKEN_ROW}"));
            }
            let lead = if above.is_empty() { format!("    {label:>labw$} ") } else { format!("    {:>labw$} ", "") };
            // the bar takes what the numbers leave; past that the range goes first, then the id
            let range = if range.is_empty() { String::new() } else { format!("  {range}") };
            let tail_full = format!(" {mark}{est}{range}{edge}  {id}{claim}");
            let tail_edge = format!(" {mark}{est}{edge}  {id}{claim}");
            let tail_mid = format!(" {mark}{est}  {id}{claim}");
            let room = |tail: &str| width.saturating_sub(cols(&lead) + cols(tail));
            let tail = [tail_full, tail_edge, tail_mid.clone()].into_iter().find(|t| room(t) >= 16).unwrap_or(tail_mid);
            let w = room(&tail).max(8);
            out.push(format!("{lead}{}{}", bar(r, lo, hi, w), tail.trim_end()));
            shown += 1;
        }
        i = j;
    }
    (out, rows.len() - shown)
}

/// rb status's dashboard (UX5 2.5): the project, the question in its words, its state and the strip, the chart, what
/// it rests on, the decisions that wait with the command for a confirmation, the other questions, and how to see more.
pub fn dashboard(ov: &Value, t: Text) -> Vec<String> {
    let w = t.width.max(40);
    let mut out: Vec<String> = Vec::new();
    out.push(s(ov, "project"));
    if !s(ov, "taken_out").is_empty() {
        out.extend(wrap(&s(ov, "taken_out"), w));
    }
    let q = ov.get("question").filter(|q| q.is_dict());
    if let Some(q) = q {
        for line in wrap(&s(q, "text"), w - 2) {
            out.push(format!("┃ {line}"));
        }
        let strip = ov.get("strip").cloned().unwrap_or(Value::Null);
        // its groups two spaces apart, one where two would wrap (rulings, Nov 12 07:15 +08); a question with no checks
        // says its state alone
        let text = strip_text(&strip);
        let lead = if text.is_empty() { format!("  {}", capital(&s(q, "state"))) } else { format!("  {} \u{b7} ", capital(&s(q, "state"))) };
        let lines = if cols(&lead) + cols(&text) <= w { vec![text] } else { wrap(&text, w - cols(&lead)) };
        for (k, line) in lines.into_iter().enumerate() {
            out.push(if k == 0 { format!("{lead}{line}") } else { format!("{:width$}{line}", "", width = cols(&lead)) });
        }
        if t.words {
            out.push(format!("  ({})", s(&strip, "said")));
        }
    }
    // what follows the chart, so the chart takes only the lines that are left
    let mut after: Vec<String> = Vec::new();
    if q.is_some() {
        // on one line where it fits: "0 of 9" where there is room, "0/9" where there is not
        let rests: Vec<String> = l(ov, "rests").iter().map(|r| rest_said(r, false)).collect();
        // "5 of 6" on every surface, never "5/6" (rulings, Nov 12 07:15 +08): a line too long wraps
        // a mark is never cut across two lines
        let mut line = String::new();
        for r in &rests {
            if !line.is_empty() && cols(&line) + 3 + cols(r) > w - 2 {
                after.push(format!("  {line}"));
                line.clear();
            }
            if !line.is_empty() {
                line.push_str(" \u{b7} ");
            }
            line.push_str(r);
        }
        if !line.is_empty() {
            after.push(format!("  {line}"));
        }
        if t.words {
            after.extend(l(ov, "rests").iter().map(|r| format!("  ({})", s(r, "said"))));
        }
    }
    let decisions = l(ov, "decisions");
    after.push(String::new());
    let made = ov.get("made").and_then(Value::as_i64).unwrap_or(0);
    let took_calls = made_taken_said(ov.get("made_taken_out").and_then(Value::as_i64).unwrap_or(0));
    let took_calls = if took_calls.is_empty() { String::new() } else { format!(" \u{b7} {took_calls}") };
    after.push(format!("Decisions ({}){}{took_calls}", decisions.len(), if made > 0 { format!(" \u{b7} made {made}") } else { String::new() }));
    if decisions.is_empty() {
        after.push("  Nothing waits.".into());
    }
    let shown = DECISION_ROWS.min(decisions.len());
    for x in &decisions[..shown] {
        let mark = s(x, "mark");
        let sq = if ["■", "▲", "◧", "□"].contains(&mark.as_str()) { format!("{mark} ") } else { String::new() };
        let kind = s(x, "kind");
        // a call names what `rb show` opens; a confirmation gives its command
        let taken = !s(x, "taken_out").is_empty();
        // a row resting on a write git took out says so on its first line, beside what it names
        let id = match (kind == "decide", taken) {
            (true, true) => format!("{} {TAKEN_DECISION} \u{b7} ", s(x, "subject")),
            (true, false) => format!("{} \u{b7} ", s(x, "subject")),
            (false, true) => format!("{TAKEN_DECISION} \u{b7} "),
            (false, false) => String::new(),
        };
        let head = format!("  [{}] {sq}", s(x, "chip"));
        for (k, line) in wrap(&format!("{id}{}", s(x, "title")), w - cols(&head)).into_iter().enumerate() {
            after.push(if k == 0 { format!("{head}{line}") } else { format!("{:width$}{line}", "", width = cols(&head)) });
        }
        if kind == "confirm" && !s(x, "cmd").is_empty() {
            after.push(format!("      $ {}", s(x, "cmd")));
        }
        if taken {
            // what it rests on, its lines hanging under the first, so each reads under the line that names the take-out
            for (k, line) in wrap(&s(x, "taken_out"), w - cols(&head) - 2).into_iter().enumerate() {
                after.push(format!("{:width$}{line}", "", width = cols(&head) + if k == 0 { 0 } else { 2 }));
            }
        }
        if t.words {
            after.push(format!("      ({})", s(x, "said")));
        }
    }
    if decisions.len() > shown {
        after.push(format!("  +{} more: rb status --full", decisions.len() - shown));
    }
    for o in l(ov, "others") {
        after.push(String::new());
        for line in wrap(&s(o, "text"), w - 2) {
            after.push(format!("┃ {line}"));
        }
        let text = strip_text(o.get("strip").unwrap_or(&Value::Null));
        after.push(if text.is_empty() { format!("  {}", capital(&s(o, "state"))) } else { format!("  {} \u{b7} {text}", capital(&s(o, "state"))) });
    }
    after.push(String::new());
    after.push("rb show <id> opens one \u{b7} rb status --full prints everything".into());
    if q.is_some() && !l(ov, "chart").is_empty() {
        let room = if t.lines > 0 { t.lines.saturating_sub(out.len() + after.len() + 2) } else { 0 };
        let (mut chart, left) = chart_text(l(ov, "chart"), w, if t.lines > 0 { room.max(6) } else { 0 });
        if left > 0 {
            chart.push(format!("    +{left} more: rb show {}", q.map(|q| s(q, "id")).unwrap_or_default()));
        }
        out.push(String::new());
        out.extend(chart);
        // the words of the rows it draws, each with its check's id, never of the rows "+n more" folds
        if t.words {
            let drawn = l(ov, "chart").len() - left;
            out.extend(l(ov, "chart").iter().take(drawn).map(|r| format!("    ({}: {})", s(r, "id"), s(r, "said"))));
        }
    }
    out.extend(after);
    out.into_iter().map(|line| plain(&line, t.ascii)).collect()
}

fn capital(t: &str) -> String {
    let mut c = t.chars();
    c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
}
