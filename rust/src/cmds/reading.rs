//! The commands that read the record (rb status, show, compare, log and context): what each says.

use super::*;
use crate::actor::{by_writer, shown_writer};
use crate::ledger::GATES as LEDGER_GATES;
use crate::view;
use crate::ledger::{Obj, UNSHALLOW};
use indexmap::IndexMap;

type R = Res<i32>;

pub const EXIT_CHECK_FAILED: i32 = 1;

/// The value's text as an f-string puts it, or "" when the key is missing.
fn gs(v: &Value, k: &str) -> String {
    v.get(k).map(pyjson::py_str).unwrap_or_default()
}

fn items<'a>(v: &'a Value, k: &str) -> &'a [Value] {
    match v.get(k) {
        Some(Value::List(l)) => l,
        _ => &[],
    }
}

fn truthy(v: &Value, k: &str) -> bool {
    v.get(k).map_or(false, |x| x.truthy())
}

/// A finding's headline: the rule and the result, 'criterion met · loss', 'gate missed · gain', 'untested'.
pub fn standing(f: &Value) -> String {
    f.get("says")
        .and_then(|s| s.get("headline"))
        .filter(|h| h.truthy())
        .map(pyjson::py_str)
        .unwrap_or_else(|| f.get("rule_outcome").map(pyjson::py_str).unwrap_or_else(|| "untested".into()))
}

pub const AXES: &[(&str, &str)] = &[
    ("comparison", "reads"),
    ("result", "result"),
    ("rule", "rule"),
    ("timing", "timing"),
    ("units", "units"),
    ("integrity", "integrity"),
    ("review", "review"),
    ("history", "history"),
];

/// The notes that say a setting a finding reads is not verified: provisional, unknown, or read from a source that
/// postdates a run it reads.
const UNVERIFIED: &[&str] = &["provisional", "unknown", "unknown_optional", "source_postdates_run"];

/// One criterion on its axes, as rb status, rb show and rb context print it.
pub fn finding_block(f: &Value, limit: Option<usize>) -> Vec<String> {
    finding_block_without(f, limit, &[])
}

/// Each finding's block, and before an experiment's first the notes every one of its criteria carries, said once: a
/// caveat nine rules share is one line, not nine. --json keeps every finding's own.
pub fn finding_blocks(findings: &[Value], limit: Option<usize>) -> Vec<Vec<String>> {
    let notes = |f: &Value| -> Vec<String> {
        items(f, "caveats").iter().filter(|c| c.get("code").and_then(|x| x.as_str()) != Some("single_unit")).map(|c| gs(c, "text")).collect()
    };
    let mut by: IndexMap<String, Vec<&Value>> = IndexMap::new();
    for f in findings {
        by.entry(gs(f, "experiment")).or_default().push(f);
    }
    let mut shared: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();
    for (e, fs) in &by {
        if fs.len() < 2 {
            continue;
        }
        let mut common: Vec<String> = Vec::new();
        for t in notes(fs[0]) {
            if !common.contains(&t) && fs[1..].iter().all(|f| notes(f).contains(&t)) {
                common.push(t);
            }
        }
        if !common.is_empty() {
            shared.insert(e.clone(), common);
        }
    }
    let mut out: Vec<Vec<String>> = Vec::new();
    let mut said: Vec<String> = Vec::new();
    for f in findings {
        let e = gs(f, "experiment");
        let common = shared.get(&e).map(Vec::as_slice).unwrap_or(&[]);
        if !common.is_empty() && !said.contains(&e) {
            said.push(e.clone());
            out.push(std::iter::once(format!("  On every {e} criterion:")).chain(common.iter().map(|t| format!("          · {t}"))).collect());
        }
        out.push(finding_block_without(f, limit, common));
    }
    out
}

/// A finding's block, leaving out the notes in `said`, which its experiment's line says for every criterion.
fn finding_block_without(f: &Value, limit: Option<usize>, said: &[String]) -> Vec<String> {
    let says = f.get("says").filter(|s| s.truthy()).cloned().unwrap_or(Value::empty_dict());
    let role = f.get("role");
    let role_note = match role {
        None | Some(Value::Null) => String::new(),
        Some(r) if r.as_str() == Some("primary") => String::new(),
        Some(r) => format!(" · {}", pyjson::py_str(r)),
    };
    let mut lines = vec![
        format!("  {:<7} {}", gs(f, "criterion"), view::finding_statement(f).0),
        format!("          {} · {}{role_note}", standing(f), gs(f, "rule")),
    ];
    // a setting it reads that is not verified, or runs no config was compared for, is no integrity problem, and the
    // line never reads ok beside it; rb status, which hides ok, leaves the config note alone as it did ok
    let noted = |codes: &[&str]| -> Vec<bool> {
        items(f, "caveats").iter().filter(|c| codes.contains(&gs(c, "code").as_str())).map(|c| said.contains(&gs(c, "text"))).collect()
    };
    let (unverified, unchecked) = (noted(UNVERIFIED), noted(&["config_unchecked"]));
    let mut quiet = false;
    for (key, label) in AXES {
        let Some(mut text) = says.get(key).filter(|t| t.truthy()).map(pyjson::py_str) else { continue };
        if *key == "integrity" && text == "ok" && !(unverified.is_empty() && unchecked.is_empty()) {
            let at: Vec<bool> = unverified.iter().chain(&unchecked).copied().collect();
            let (above, below) = (at.contains(&true), at.contains(&false));
            let what = match (unverified.is_empty(), unchecked.is_empty()) {
                (false, true) => "settings it reads are not verified",
                (true, false) => "no run config was compared",
                _ => "settings it reads are not verified and no run config was compared",
            };
            text = format!("{what}, as said {}", if above && below { "above and below" } else if above { "above" } else { "below" });
            quiet = unverified.is_empty();
        }
        if (*key == "integrity" && (text == "ok" || quiet) && limit.is_some()) || ((*key == "review" || *key == "history") && (text == "no decision" || text.is_empty())) {
            continue;
        }
        lines.push(format!("          {:<11}{text}", format!("{label}:")));
    }
    let problems = items(f, "integrity");
    let end = match limit {
        None => problems.len(),
        Some(l) => l.min(problems.len()),
    };
    if end > 1 {
        for c in &problems[1..end] {
            lines.push(format!("          ✗ {}", gs(c, "text")));
        }
    }
    let notes: Vec<&Value> =
        items(f, "caveats").iter().filter(|c| c.get("code").and_then(|x| x.as_str()) != Some("single_unit") && !said.contains(&gs(c, "text"))).collect();
    let shown = match limit {
        None => notes.len(),
        Some(l) => l.min(notes.len()),
    };
    for c in &notes[..shown] {
        lines.push(format!("          · {}", gs(c, "text")));
    }
    if shown < notes.len() {
        lines.push(format!("          and {} more: rb show {}", notes.len() - shown, gs(f, "criterion")));
    }
    // where its counted runs were recorded, when another place (rb status): one quiet line that asks nothing
    if let Some(o) = f.get("observed").map(pyjson::py_str).filter(|o| !o.is_empty()) {
        lines.push(format!("          · {o}"));
    }
    if let Some(o) = f.get("other_code").map(pyjson::py_str).filter(|o| !o.is_empty()) {
        lines.push(format!("          · {o}"));
    }
    lines
}

/// An experiment's settings, counted by status, and a verified one whose source no longer states it as conflict or stale.
pub fn settings_counts(e: &Value) -> String {
    let k = e.get("settings_count").cloned().unwrap_or(Value::empty_dict());
    let mut s = format!("settings {} verified · {} provisional · {} unknown", gs(&k, "verified"), gs(&k, "provisional"), gs(&k, "unknown"));
    for (key, word) in [("conflict", "conflict"), ("stale", "stale"), ("inherited", "inherited"), ("per_run", "per-run")] {
        if truthy(&k, key) {
            s += &format!(" · {} {word}", gs(&k, key));
        }
    }
    s
}

/// What waits for a confirmation, in sections of their own, apart from the calls: each call proposed, then each past
/// call mentioned, with its subject, action, reason as given and the one command that confirms it; then one line for
/// every experiment whose design waits, however many. Nothing when nothing waits; with more than one, while no design
/// waits, the line that confirms them all. `drawn`: each row as a screen draws it (`PendingRow::drawn`), else as written.
pub fn pending_sections(stv: &Value, scope: Option<&str>, drawn: bool) -> (Vec<(&'static str, Vec<String>)>, Option<String>, Option<String>) {
    let rows: Vec<crate::view::PendingRow> = crate::view::pending_rows(stv).into_iter().map(|r| if drawn { r.drawn() } else { r }).collect();
    let mut out = Vec::new();
    for (kind, title) in [("proposed", "Proposed, not made"), ("past", "Past calls mentioned, not confirmed"), ("gate", "Gates set, not confirmed")] {
        // a gate is said in full on its experiment's line; here it is named, with its command
        let said = |r: &crate::view::PendingRow| if r.kind == "gate" { format!("{}'s gate", r.id.split('@').next().unwrap_or("")) } else { r.text.clone() };
        let lines: Vec<String> = rows.iter().filter(|r| r.kind == kind).map(|r| format!("{} · confirm: {}", said(r), r.confirm)).collect();
        if !lines.is_empty() {
            out.push((title, lines));
        }
    }
    // a design is read before it is confirmed: while one waits, its one line says rb confirm lists it, and that listing
    // ends with the command that confirms everything it lists, as listed
    let all = crate::view::confirm_all_line(&rows).map(|(head, cmd)| format!("{head}: {cmd}"));
    (out, crate::view::designs_line(&rows, scope), all)
}

/// The next step the user takes first while anything waits for their confirmation, in their own terminal: the one call's
/// rb confirm <id>, or rb confirm, which lists what waits (a design is read before it is confirmed). None in an agent
/// session, where no one but the user confirms.
fn confirm_first(led: &Ledger, stv: &Value) -> Res<Option<String>> {
    if led.actor()?.id.starts_with("agent:") {
        return Ok(None);
    }
    Ok(match crate::view::pending_rows(stv).as_slice() {
        [] => None,
        [one] if one.kind != "design" => Some(one.confirm.clone()),
        _ => Some("rb confirm".into()),
    })
}

/// The open items rb status lists apart, in sections of their own: the calls that wait for a confirmation.
fn pending_item(i: &&Value) -> bool {
    crate::ledger::PENDING_CODES.contains(&gs(i, "code").as_str())
}

/// Whether an item's command is a next step: never one that waits on a confirmation, nor older work that can wait.
fn next_step(i: &&Value) -> bool {
    !pending_item(i) && !truthy(i, "later")
}

/// Open items, with many of one kind collapsed into one line and the command under each.
pub fn items_text(its: &[&Value]) -> Vec<String> {
    let mut out = Vec::new();
    let mut groups: IndexMap<(String, String), Vec<&Value>> = IndexMap::new();
    for it in its {
        groups.entry((gs(it, "who"), gs(it, "code"))).or_default().push(it);
    }
    for ((who, code), its) in groups {
        // an item that names what is unread says it in its own words, never in its command alone
        if its.len() > 3 && who != "human" && !code.starts_with("unread_") {
            out.push(format!("  - {} × {}:", its.len(), code.replace('_', " ")));
            // an item that asks for nothing says what it is
            for it in its.iter().take(6) {
                let do_ = gs(it, "do");
                out.push(format!("      {}", if do_.is_empty() { gs(it, "what") } else { do_ }));
            }
            if its.len() > 6 {
                out.push(format!("      ... and {} more: rb status --json lists every one", its.len() - 6));
            }
        } else {
            for it in its {
                out.push(format!("  - {}", gs(it, "what")));
                let last = items(it, "last_lines");
                if !last.is_empty() {
                    out.push("      its last lines:".into());
                    let from = last.len().saturating_sub(5);
                    for line in &last[from..] {
                        out.push(format!("        {}", pyjson::py_str(line)));
                    }
                }
                let do_ = gs(it, "do");
                if !do_.is_empty() {
                    out.push(format!("      {do_}"));
                }
            }
        }
    }
    out
}

/// The open items items_text names, by the key rb_delta tells them under: a kind of more than three collapses to its
/// first six.
fn shown_keys(its: &[&Value]) -> Vec<String> {
    let mut groups: IndexMap<(String, String), Vec<&Value>> = IndexMap::new();
    for it in its {
        groups.entry((gs(it, "who"), gs(it, "code"))).or_default().push(it);
    }
    let mut out = Vec::new();
    for ((who, code), its) in groups {
        let many = its.len() > 3 && who != "human" && !code.starts_with("unread_");
        out.extend(its.iter().take(if many { 6 } else { its.len() }).map(|it| crate::delta::state_key(&code, &gs(it, "subject"))));
    }
    out
}

/// What rb status and rb context print of the open items, by the key rb_delta tells each under: what a session has
/// been told by reading them.
fn told_keys(stv: &Value) -> Vec<String> {
    let open = items(stv, "open");
    // the items for a human beyond the first SHOWN are counted, not printed
    let human: Vec<&Value> = open.iter().filter(|i| gs(i, "who") == "human" && !pending_item(i)).take(SHOWN).collect();
    let (agent, quiet): (Vec<&Value>, Vec<&Value>) = open.iter().filter(|i| gs(i, "who") != "human").partition(|i| !gs(i, "do").is_empty());
    [human, agent, quiet].iter().flat_map(|its| shown_keys(its)).collect()
}

/// The count line of the .json, .yaml and .toml files that changed and nothing in the record cites, with the command
/// that lists them: "or more" where git listed more than rb keeps, and how many are also among what rb cannot place.
/// The same count, where a screen has one short line for it.
pub(crate) fn uncited_short(f: &crate::journal::FoundAll) -> String {
    format!("Changed .json/.yaml/.toml files the record does not cite ({}{}): rb reconcile", f.uncited.len(), if f.uncited_cut { " or more" } else { "" })
}

pub(crate) fn uncited_words(f: &crate::journal::FoundAll) -> String {
    let n = f.uncited.len();
    let count = if f.uncited_cut { format!("{n} or more") } else { n.to_string() };
    let also = if f.uncited_also_found > 0 { format!("; {} also counted above", f.uncited_also_found) } else { String::new() };
    format!("Changed .json, .yaml or .toml files that nothing in the record cites ({count}{also}): rb reconcile lists {} by path", if n == 1 { "it" } else { "each" })
}

/// How many of one list rb status and rb context print before they count the rest, so a large repository or record
/// keeps the first screen readable: --json keeps every item.
pub(crate) const SHOWN: usize = 10;
/// Of the rows a reader scans down (criteria, experiments, runs), how many.
pub(crate) const SHOWN_ROWS: usize = 20;
/// Of a question's criteria, how many its reading names at the top of rb status and rb context: the first screen.
pub(crate) const SHOWN_READINGS: usize = 5;
/// Of an experiment's settings, how many the briefing's table holds.
pub(crate) const SHOWN_SETTINGS: usize = 40;

/// The first `n` of a list, and the line that counts the rest ("and 12 more experiments: <where>"), if any are left.
pub(crate) fn first_n<T>(all: &[T], n: usize, indent: &str, what: &str, rest: &str) -> (usize, Option<String>) {
    if all.len() <= n {
        return (all.len(), None);
    }
    (n, Some(format!("{indent}... and {} more {what}: {rest}", all.len() - n)))
}

fn cut240(text: &str) -> String {
    if text.chars().count() <= 240 {
        return text.to_string();
    }
    let head: String = text.chars().take(240).collect();
    format!("{} ...", crate::rbyaml::py_rstrip(&head))
}

/// What rb status and rb compile say of what rb found and nobody recorded: the runs rb reconcile gives the rb run add
/// that records each, the runs whose line needs a per-run value nothing gives, the files rb cannot tell from the work
/// before the record (there when rb first looked, brought by git, copied), those written while a watched command ran that nothing ties to it, and the numbers it cannot place in one
/// experiment (several may read them, or none).
pub(crate) fn found_words(f: &crate::journal::FoundAll) -> String {
    let k = &f.kinds;
    let runs = f.placed.len();
    let total = runs + f.unplaced.len();
    let loose = f.unplaced.len().saturating_sub(k.needs.len() + k.older.len() + k.while_ran);
    let one = |n: usize| if n == 1 { "one".to_string() } else { n.to_string() };
    let mut said: Vec<String> = Vec::new();
    match runs {
        0 => {}
        1 if total == 1 => said.push("rb reconcile lists it, with the rb run add that records it".into()),
        r if r == total => said.push("rb reconcile lists them, with the rb run add for each".into()),
        1 => said.push("rb reconcile lists them, with the rb run add for the one run".into()),
        r => said.push(format!("rb reconcile lists them, with the rb run add for each of the {r} runs")),
    }
    if !k.needs.is_empty() {
        let n = k.needs.len();
        let exps: std::collections::BTreeSet<&String> = k.needs.iter().map(|x| &x.experiment).collect();
        let names: std::collections::BTreeSet<&Vec<String>> = k.needs.iter().map(|x| &x.names).collect();
        let who = match exps.iter().next() {
            Some(e) if exps.len() == 1 => format!("{n} {} of {e}", if n == 1 { "run" } else { "runs" }),
            _ => format!("{n} runs"),
        };
        let what = match names.iter().next() {
            Some(v) if names.len() == 1 => format!("{} {}", if n == 1 { "its" } else { "their" }, crate::assess::first_three(v, ", ")),
            _ => "a per-run value".to_string(),
        };
        said.push(format!("{who} {} {what}, which rb reconcile gives the {} for", if n == 1 { "needs" } else { "need" }, if n == 1 { "line" } else { "lines" }));
    }
    if !k.older.is_empty() {
        use crate::journal::Older;
        let n = k.older.len();
        let (file, one) = (if n == 1 { "file" } else { "files" }, n == 1);
        // each kind in its own words where all are of one kind; else what they share
        let word = |o: &Older| match o {
            Older::There(_) => if one { "was there when rb first looked here" } else { "were there when rb first looked here" },
            Older::Git(_) => "came with a checkout, pull or merge",
            Older::Before => if one { "was last written before rb first looked here" } else { "were last written before rb first looked here" },
            Older::Stored => if one { "holds bytes git already kept" } else { "hold bytes git already kept" },
            Older::Copy(_) => if one { "holds the same bytes as a file rb saw first" } else { "hold the same bytes as files rb saw first" },
        };
        let words: std::collections::BTreeSet<&str> = k.older.iter().map(word).collect();
        let what = match words.iter().next() {
            Some(w) if words.len() == 1 => format!("{n} results {file} {w}"),
            _ => format!("{n} results {file} rb cannot tell from the work before the record"),
        };
        said.push(format!("{what}, which rb reconcile lists and proposes no run of"));
    }
    if k.while_ran > 0 {
        said.push(format!(
            "{} {} while a watched command ran, which nothing ties to it",
            k.while_ran,
            if k.while_ran == 1 { "file was written" } else { "files were written" }
        ));
    }
    if loose > 0 {
        said.push(if said.is_empty() {
            "numbers rb cannot place in one experiment, which rb reconcile lists".to_string()
        } else {
            format!("rb cannot place the numbers of the other {} in one experiment", one(loose))
        });
    }
    said.join("; ")
}

pub fn cmd_status(ns: &Namespace, out: &mut Out) -> R {
    let mut gates: Vec<&str> = LEDGER_GATES.to_vec();
    gates.push("unestablished");
    let mut fail_on: Vec<String> = Vec::new();
    for item in list(ns, "fail_on") {
        for g in item.split(',') {
            let g = crate::rbyaml::py_strip(g);
            if !g.is_empty() {
                fail_on.push(g.to_string());
            }
        }
    }
    let bad: Vec<&String> = fail_on.iter().filter(|g| !gates.contains(&g.as_str())).collect();
    if !bad.is_empty() {
        return Err(RBError::msg(
            "E_OBJECT_INVALID",
            format!("--fail-on takes {} (got {}).", gates.join(", "), bad.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")),
        ));
    }
    let led = Ledger::open(None)?;
    let r = status_of(&led, ns, &fail_on, out);
    out.shows_for(&led);
    r
}

fn status_of(led: &Ledger, ns: &Namespace, fail_on: &[String], out: &mut Out) -> R {
    // look at the repository before reading it, as rb open does
    crate::journal::look(led);
    let experiment = s(ns, "experiment");
    let experiment = experiment.as_deref();
    let mut st = led.status(experiment)?;
    // where each finding's counted runs were recorded, when another place: said once under it (`observed`)
    let findings: &[Value] = match st.get("findings") {
        Some(Value::List(l)) => l,
        _ => &[],
    };
    let notes = view::observed_notes(&led, findings)?;
    // and, where they ran other code, which (`other_code`)
    let code = view::code_notes(&led, findings)?;
    if let Some(Value::List(fs)) = st.get_mut("findings") {
        for f in fs.iter_mut() {
            if let Value::Dict(d) = f {
                let id = d.get("criterion").map(pyjson::py_str).unwrap_or_default();
                d.insert("observed".into(), Value::Str(notes.get(&id).cloned().unwrap_or_default()));
                d.insert("other_code".into(), Value::Str(code.get(&id).cloned().unwrap_or_default()));
            }
        }
    }
    let stv = Value::Dict(st.clone());
    let proj = stv.get("project").cloned().unwrap_or(Value::empty_dict());
    let mut tally: Vec<(String, String)> = match stv.get("tally") {
        Some(Value::Dict(d)) => d.iter().map(|(k, n)| (k.clone(), pyjson::py_str(n))).collect(),
        _ => Vec::new(),
    };
    tally.sort_by_key(|(k, _)| tally_order(k));
    let tally = tally.iter().map(|(k, n)| format!("{n} {}", crate::assess::rule_label(k, n.parse().unwrap_or(0)))).collect::<Vec<_>>().join(" · ");
    let retracted = items(&stv, "retracted");
    // a title that is its id is said once
    let (title, pid) = (gs(&proj, "title"), gs(&proj, "id"));
    let head = if title == pid { title } else { format!("{title} · {pid}") };
    out.say(head + &experiment.filter(|e| !e.is_empty()).map(|e| format!(" · experiment {e}")).unwrap_or_default());
    if let Some(start) = led.record_start()? {
        out.say(start.words());
    }
    if let Some(line) = crate::elsewhere::nested_line(&led) {
        out.say(line);
    }
    // a clone: what it takes to run the parent again, said under the title
    let rerun = crate::reproduce::rerun(led)?;
    if let Some(v) = &rerun {
        out.say("");
        out.say("Rerun the parent");
        for line in crate::reproduce::rerun_lines(v) {
            out.say(format!("  {line}"));
        }
    }
    let tally_line = if !tally.is_empty() {
        format!("Criteria: {tally}")
    } else if !retracted.is_empty() {
        format!("Criteria: none standing ({} retracted)", retracted.len())
    } else {
        "Criteria: none yet".to_string()
    };
    if let Some(last) = stv.get("repository").filter(|r| r.truthy()).and_then(|r| r.get("last_reconcile")).filter(|l| l.truthy()) {
        let at: String = gs(last, "at").chars().take(16).collect::<String>().replace('T', " ");
        out.say(format!("Last reconcile, {at}{}: {}", by_writer(&gs(last, "by")), gs(last, "line")));
        // the one write rb status prints, which it tells a session
        let row = led.log_entries()?.iter().rev().find(|r| r.get("op").and_then(|o| o.as_str()) == Some("reconciled")).map(crate::ledger::line_digest);
        out.told_lines = Some(row.into_iter().collect());
    }
    let open = items(&stv, "open");
    let human: Vec<&Value> = open.iter().filter(|i| gs(i, "who") == "human" && !pending_item(i)).collect();
    // an item that asks for nothing is said apart from the work
    let (agent, quiet): (Vec<&Value>, Vec<&Value>) = open.iter().filter(|i| gs(i, "who") != "human").partition(|i| !gs(i, "do").is_empty());
    let (exps, findings, questions) = (items(&stv, "experiments"), items(&stv, "findings"), items(&stv, "questions"));
    if exps.is_empty() && findings.is_empty() && questions.is_empty() {
        out.say("");
        out.say("Nothing recorded yet. Start: rb question add \"<what are you trying to find out?>\", then rb experiment add \"<what you are testing>\" -q <question> --hypothesis \"<what you expect>\" --baseline <name> --candidate <name>");
    }
    // The state leads: each question, and where it stands, read from the record. Who acts next follows.
    for b in view::briefs(&led, &st)? {
        let has_id = b.id.as_deref().map_or(false, |i| !i.is_empty());
        let indent = "  ".repeat(b.depth);
        let head = if has_id { format!("{} ({})", b.text, b.status) } else { "Not filed under a question".to_string() };
        let mut lines: Vec<String> = if has_id || !b.experiments.is_empty() { vec![format!("{indent}{head}")] } else { Vec::new() };
        if !b.rollup.is_empty() {
            lines.push(format!("{indent}  {}", b.rollup));
        }
        let summary = view::summary_lines(&b.inquiry);
        let (k, more) = first_n(&summary, SHOWN_READINGS, &format!("{indent}  "), "lines of its reading", &format!("rb show {} reads it whole", b.id.as_deref().unwrap_or("<question>")));
        lines.extend(summary[..k].iter().map(|s| format!("{indent}  {s}")));
        lines.extend(more);
        // each experiment that says something here, as a block of lines: the first ones, and a count of the rest
        let mut blocks: Vec<Vec<String>> = Vec::new();
        for eb in &b.experiments {
            let mut lines: Vec<String> = Vec::new();
            if has_id && !eb.findings.is_empty() && (eb.scope.is_some() || !eb.said.is_empty() || !eb.notes.is_empty() || !eb.repo_brief.is_empty() || !eb.if_holds.is_empty() || !eb.if_not.is_empty()) {
                lines.push(format!("{indent}  experiment context: {} · {}", eb.id, eb.title));
            }
            if eb.findings.is_empty() {
                lines.push(format!("  {} · {}", eb.title, eb.state) + &if eb.folder.is_empty() { String::new() } else { format!(" · {}", eb.folder) });
            }
            if !has_id {
                for f in &eb.findings {
                    lines.push(format!("  {:<7} {}", f.criterion_id, f.sentence));
                }
            }
            if let Some(scope) = eb.scope.as_ref().filter(|s| !s.is_empty()) {
                lines.push(format!("          scope, declared: {scope}"));
            }
            if eb.findings.is_empty() && !eb.hypothesis.is_empty() {
                lines.push(format!("          hypothesis: {}", eb.hypothesis));
            }
            let gate = eb.gate_said();
            if !gate.is_empty() {
                lines.push(format!("          {gate}"));
            }
            for q in &eb.said {
                let kind = q.get("kind").map(pyjson::py_str).unwrap_or_default();
                if kind == "result" || kind == "next" {
                    let text = cut240(&q.get("text").map(pyjson::py_str).unwrap_or_default());
                    lines.push(format!("          {kind}: \"{text}\" ({})", q.get("where").map(pyjson::py_str).unwrap_or_default()));
                }
            }
            if let Some(last) = eb.notes.last() {
                let label = view::note_label(eb.note_ids.last().map(|s| s.as_str()));
                lines.push(format!("          {label}: {last}") + &if eb.notes.len() > 1 { format!(" · {} more: rb show {}", eb.notes.len() - 1, eb.id) } else { String::new() });
            }
            if !eb.repo_brief.is_empty() {
                lines.push(format!("          {}", eb.repo_brief));
            }
            if !lines.is_empty() {
                blocks.push(lines);
            }
        }
        let (k, more_exps) = first_n(&blocks, SHOWN, "  ", "experiments", "rb status --json lists every one");
        lines.extend(blocks[..k].iter().flatten().cloned());
        lines.extend(more_exps);
        for d in &b.ruled {
            lines.push(format!("{indent}  call: {d}"));
        }
        if lines.len() > if has_id { 1 } else { 0 } {
            out.say("");
            out.say_all(lines);
        }
    }
    out.say("");
    out.say(tally_line);
    if !human.is_empty() {
        out.say("");
        out.say(format!("Needs a human ({})", human.len()));
        let (k, more) = first_n(&human, SHOWN, "  ", "items", "rb status --json lists every one");
        out.say_all(items_text(&human[..k]));
        out.say_all(more);
    }
    let (sections, designs, all) = pending_sections(&stv, experiment.filter(|x| !x.is_empty()), true);
    for (title, lines) in &sections {
        out.say("");
        out.say(format!("{title} ({})", lines.len()));
        let (k, more) = first_n(lines, SHOWN, "  ", "waiting", "rb confirm lists every one");
        out.say_all(lines[..k].iter().map(|x| format!("  - {x}")));
        out.say_all(more);
    }
    if let Some(designs) = designs {
        out.say("");
        out.say(designs);
    }
    if let Some(all) = all {
        out.say(all);
    }
    if !agent.is_empty() {
        out.say("");
        out.say(format!("Can be done now ({})", agent.len()));
        out.say_all(items_text(&agent));
    }
    if !quiet.is_empty() {
        out.say("");
        out.say(format!("Nothing to do ({})", quiet.len()));
        out.say_all(items_text(&quiet));
    }
    let all = crate::journal::found_all(&led, crate::journal::Seen::User).ok();
    let (found, placed) = all.as_ref().map_or((0, 0), |f| ((f.placed.len() + f.unplaced.len()) as i64, f.placed.len()));
    st.insert("found_unrecorded".into(), Value::Int(found));
    st.insert("found_needs".into(), Value::List(all.as_ref().map(|f| f.kinds.needs.iter().map(|n| n.to_value()).collect()).unwrap_or_default()));
    if let Some(f) = all.as_ref().filter(|_| found > 0) {
        out.say("");
        out.say(format!("Found in the repository, not in the record ({found}): {}", found_words(f)));
    }
    if let Some(f) = all.as_ref().filter(|f| !f.uncited.is_empty()) {
        out.say("");
        out.say(uncited_words(f));
    }
    st.insert("uncited_changes".into(), Value::List(all.as_ref().map(|f| f.uncited.iter().map(|u| u.to_value()).collect()).unwrap_or_default()));
    // what rb looked at, and when: absence is said as that, never as nothing having happened
    let scope = crate::journal::Scope::of(&led);
    out.say("");
    out.say(scope.said());
    st.insert("scope".into(), scope.to_value());
    let places = crate::elsewhere::elsewhere(&led, experiment).unwrap_or_default();
    if !places.is_empty() {
        out.say("");
        let separate = places.iter().filter(|p| p.value.get("same_project") == Some(&Value::Bool(false))).count();
        let said = match (places.len() - separate, separate) {
            (_, 0) => "work on this record in branches and worktrees not merged here",
            (0, 1) => "a separate project's record in a branch or worktree of this repository",
            (0, _) => "separate projects' records in branches and worktrees of this repository",
            (_, 1) => "work on this record in branches and worktrees not merged here, and a separate project's record",
            _ => "work on this record in branches and worktrees not merged here, and separate projects' records",
        };
        out.say(format!("Elsewhere ({}): {said}", places.len()));
        out.say_all(crate::elsewhere::lines(&led, &places).unwrap_or_default());
    }
    st.insert("elsewhere".into(), Value::List(places.into_iter().map(|p| p.value).collect()));
    if !findings.is_empty() {
        out.say("");
        out.say("Criteria");
        let (k, more) = first_n(findings, SHOWN_ROWS, "  ", "criteria", "rb show <id> reads one, and rb status --json lists every one");
        for block in finding_blocks(&findings[..k], Some(STATUS_CAVEATS)) {
            out.say_all(block);
        }
        out.say_all(more);
    }
    if !retracted.is_empty() {
        out.say("");
        out.say("Retracted criteria");
        let (k, more) = first_n(&retracted, SHOWN, "  ", "retracted criteria", "rb status --json lists every one");
        for r in &retracted[..k] {
            out.say(format!(
                "  {:<7} {} · {} · retracted{}{}",
                gs(r, "id"),
                gs(r, "role"),
                gs(r, "statement"),
                by_writer(&gs(r, "by")),
                view::reason_with_words(&gs(r, "why"), &r.get("words").filter(|w| w.truthy()).map(pyjson::py_str).unwrap_or_default(), &gs(r, "by"), ": ")
            ));
        }
        out.say_all(more);
    }
    if !exps.is_empty() {
        out.say("");
        out.say("Experiments");
        let mut with_rerun: Vec<Value> = Vec::new();
        // every experiment's row in --json; in text, the first ones and a count
        let (shown, more) = first_n(exps, SHOWN_ROWS, "  ", "experiments", "rb show <id> reads one, and rb status --json lists every one");
        for (i, e) in exps.iter().enumerate() {
            let said = i < shown;
            let arms = items(e, "variants").iter().map(|v| format!("{} ({})", gs(v, "name"), gs(v, "role"))).collect::<Vec<_>>().join(" vs ");
            let arms = if arms.is_empty() { "no variants".to_string() } else { arms };
            let mut frozen = match e.get("frozen").filter(|f| f.truthy()) {
                Some(fz) => {
                    let sha: String = gs(fz, "sha256").chars().take(12).collect();
                    format!("frozen {sha}") + &if truthy(e, "amendment_count") { format!(", amended {}x", gs(e, "amendment_count")) } else { String::new() }
                }
                None => "not frozen".to_string(),
            };
            if truthy(e, "spec_drift") {
                frozen += ", SPEC CHANGED WITHOUT AN AMENDMENT";
            }
            let varies: Vec<String> = items(e, "varies").iter().map(pyjson::py_str).collect();
            let mut lines: Vec<String> = Vec::new();
            lines.push(
                format!("  {:<7} {} · {} · {arms}", gs(e, "id"), gs(e, "title"), gs(e, "state"))
                    + &if varies.is_empty() { String::new() } else { format!(" · varies {}", varies.join(", ")) }
                    + &format!(" · {frozen} · {} · runs {}", settings_counts(e), gs(e, "run_count"))
                    + &if truthy(e, "analysis_count") { format!(" · analyses {}", gs(e, "analysis_count")) } else { String::new() },
            );
            let mut e2 = e.clone();
            if truthy(e, "run_count") {
                let rerun = led.rerunnability(&led.load::<Experiment>(&gs(e, "id"))?)?;
                lines.push(
                    "          to run again: ".to_string()
                        + &rerun
                            .iter()
                            .map(|r| {
                                let what = get_s(r, "what");
                                let res = r.get("resource").filter(|x| x.truthy()).map(pyjson::py_str);
                                let paren = match res {
                                    Some(res) if res != what => format!(" ({res})"),
                                    _ => String::new(),
                                };
                                format!("{what}{paren} {}", get_s(r, "state"))
                            })
                            .collect::<Vec<_>>()
                            .join(" · "),
                );
                if let Value::Dict(d) = &mut e2 {
                    d.insert("rerun".into(), Value::List(rerun.into_iter().map(Value::Dict).collect()));
                }
            }
            with_rerun.push(e2);
            if said {
                out.say_all(lines);
            }
        }
        out.say_all(more);
        st.insert("experiments".into(), Value::List(with_rerun));
    }
    let runs = items(&stv, "runs");
    if !runs.is_empty() {
        let tam = led.tampered()?;
        let runs_where = |cut: bool| -> Vec<String> {
            let mut ids: Vec<String> =
                tam.iter().filter(|t| get_s(t, "kind") == "run" && t.get("shallow").map_or(false, |v| v.truthy()) == cut).map(|t| get_s(t, "id")).collect();
            ids.sort();
            ids.dedup();
            ids
        };
        let (changed, unseen) = (runs_where(false), runs_where(true));
        let n_exp = runs.iter().map(|r| gs(r, "experiment_id")).collect::<std::collections::HashSet<_>>().len();
        let first = |ids: &[String]| ids.iter().take(4).cloned().collect::<Vec<_>>().join(", ");
        let mut said: Vec<String> = Vec::new();
        if !changed.is_empty() {
            said.push(format!("{} changed outside rb ({}): rb doctor", changed.len(), first(&changed)));
        }
        if !unseen.is_empty() {
            said.push(format!("{} whose history this shallow clone lacks ({}): {UNSHALLOW}", unseen.len(), first(&unseen)));
        }
        out.say("");
        out.say(
            format!("Runs: {} recorded on {n_exp} experiment{} · ", runs.len(), if n_exp == 1 { "" } else { "s" })
                + &if said.is_empty() { "every receipt is what rb wrote".to_string() } else { said.join(" · ") },
        );
    }
    let live_analyses: Vec<&Value> = items(&stv, "analyses").iter().filter(|a| !truthy(a, "retracted")).collect();
    if !live_analyses.is_empty() {
        out.say("");
        out.say("Analyses");
        let (k, more) = first_n(&live_analyses, SHOWN_ROWS, "  ", "analyses", "rb show <id> reads one, and rb status --json lists every one");
        for a in &live_analyses[..k] {
            let n = items(a, "comparisons").len();
            let supersedes: Vec<String> = items(a, "supersedes").iter().map(pyjson::py_str).collect();
            out.say(
                format!("  {:<7} on {} · {n} comparison{} ({})", gs(a, "id"), gs(a, "experiment_id"), if n == 1 { "" } else { "s" }, gs(a, "basis"))
                    + &if truthy(a, "method") { format!(" · {}", gs(a, "method")) } else { String::new() }
                    + &if truthy(a, "superseded_by") { format!(" · superseded by {}", gs(a, "superseded_by")) } else { String::new() }
                    + &if supersedes.is_empty() { String::new() } else { format!(" · supersedes {}", supersedes.join(", ")) },
            );
        }
        out.say_all(more);
    }
    let proj_notes: Vec<&Value> = items(&proj, "notes").into_iter().filter(|n| !truthy(n, "retracted")).collect();
    if !proj_notes.is_empty() {
        out.say("");
        out.say("Notes on the project");
        let from = proj_notes.len().saturating_sub(6);
        for n in &proj_notes[from..] {
            let day: String = gs(n, "at").chars().take(10).collect();
            let who = shown_writer(&gs(n, "by")).map(|name| format!("{name}, ")).unwrap_or_default();
            let label = view::note_label(Some(gs(n, "id").as_str()).filter(|_| truthy(n, "id")));
            out.say(format!("  {label} ({who}{day}): {}", gs(n, "text")) + &words(&gs(n, "words"), &gs(n, "by")) + view::not_confirmed(&gs(n, "by")));
        }
    }
    let resources = items(&stv, "resources");
    if !resources.is_empty() {
        out.say("");
        out.say("Resources");
        let (k, more) = first_n(resources, SHOWN_ROWS, "  ", "resources", "rb show <id> reads one, and rb status --json lists every one");
        for r in &resources[..k] {
            let av = r.get("availability").cloned().unwrap_or(Value::empty_dict());
            let label = gs(r, "name") + &if truthy(r, "version") { format!(" {}", gs(r, "version")) } else { String::new() };
            let ident = av.get("identity").map(|i| gs(i, "text")).unwrap_or_default();
            out.say(
                format!("  {:<12} {} · {label} · {} · {ident}", gs(r, "id"), gs(r, "type"), gs(&av, "state"))
                    + &if truthy(&av, "how") { format!(" · how to get it again: {}", gs(&av, "how")) } else { String::new() },
            );
        }
        out.say_all(more);
    }
    // a run rb found and nobody recorded is open too, as the screens count it under Your call; numbers nothing
    // declares are not
    if open.is_empty() && placed == 0 && (!findings.is_empty() || !exps.is_empty()) {
        out.say("");
        out.say("Nothing open.");
    }
    let gate = stv.get("gate").cloned().unwrap_or(Value::empty_dict());
    let mut tripped: Vec<String> = fail_on.iter().filter(|g| truthy(&gate, g)).cloned().collect();
    let outside = |code: &str| findings.iter().any(|f| items(f, "integrity").iter().any(|c| gs(c, "code") == code));
    if !fail_on.is_empty() && tripped.is_empty() {
        if let Some(code) = ["edited_outside_rb", "shallow_clone"].into_iter().find(|c| outside(c)) {
            tripped = vec![code.to_string()];
        }
    }
    st.insert("fail_on".into(), Value::List(fail_on.iter().cloned().map(Value::Str).collect()));
    st.insert(
        "outcome".into(),
        crate::obj! {
            "passed" => tripped.is_empty(),
            "failures" => Value::List(tripped.iter().map(|g| crate::obj! {"code" => g.clone(), "reason" => format!("--fail-on {g}")}).collect()),
        },
    );
    if !tripped.is_empty() {
        out.say("");
        out.say(format!("GATE FAILED on: {}.", tripped.join(", ")));
    }
    for (k, v) in st {
        out.data.insert(k, v);
    }
    // a confirmation is the user's, in their own terminal: no next step anyone else could take, and the user's first
    // an item that asks for nothing offers no next step
    // two kinds of item may ask for the same command: it is said once
    let mut next: Vec<String> = confirm_first(led, &stv)?.into_iter().collect();
    if let Some(v) = rerun {
        rerun_steps(&v, experiment, &mut next);
        out.data.insert("rerun".into(), v);
    }
    for d in open.iter().filter(next_step).map(|i| gs(i, "do")).filter(|d| !d.is_empty()) {
        if !next.contains(&d) && next.len() < 3 {
            next.push(d);
        }
    }
    out.next = next;
    out.told = Some(told_keys(&stv));
    Ok(if tripped.is_empty() { 0 } else { EXIT_CHECK_FAILED })
}

// ---------------------------------------------------------------- rb show

fn dump_obj(o: &Obj) -> Dict {
    match o {
        Obj::Project(x) => x.dump(false),
        Obj::Question(x) => x.dump(false),
        Obj::Experiment(x) => x.dump(false),
        Obj::Metric(x) => x.dump(false),
        Obj::Criterion(x) => x.dump(false),
        Obj::Run(x) => x.dump(false),
        Obj::Analysis(x) => x.dump(false),
        Obj::Resource(x) => x.dump(false),
        Obj::Decision(x) => x.dump(false),
    }
}

fn cut(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// What a record file's form fails on as its kind (a part's too), one line each as rb words them everywhere; None when
/// the kind is not one a file holds.
fn file_problems(kind: &str, form: Value) -> Option<Vec<String>> {
    let mut errs = Vec::new();
    let mut loc = Vec::new();
    let v = &form;
    match kind {
        "project" => drop(Project::validate(v, &mut loc, &mut errs)),
        "question" => drop(Question::validate(v, &mut loc, &mut errs)),
        "experiment" => drop(Experiment::validate(v, &mut loc, &mut errs)),
        "variant" => drop(Variant::validate(v, &mut loc, &mut errs)),
        "note" => drop(Note::validate(v, &mut loc, &mut errs)),
        "criterion" => drop(Criterion::validate(v, &mut loc, &mut errs)),
        "run" => drop(Run::validate(v, &mut loc, &mut errs)),
        "analysis" => drop(Analysis::validate(v, &mut loc, &mut errs)),
        "metric" => drop(Metric::validate(v, &mut loc, &mut errs)),
        "resource" => drop(Resource::validate(v, &mut loc, &mut errs)),
        "decision" => drop(Decision::validate(v, &mut loc, &mut errs)),
        _ => return None,
    }
    Some(crate::model::problems(&errs, "value"))
}

/// A record file anywhere (here, in an export, sent on its own): read as Rabbit Brain YAML, checked against the format,
/// and given back as canonical JSON. One that does not validate is still shown, with what it fails on.
fn show_file(subject: &str, out: &mut Out) -> R {
    let path = pathlib_str(subject);
    let text = match std::fs::read(subject) {
        Ok(bytes) => match String::from_utf8(bytes) {
            Ok(t) => t,
            Err(e) => {
                let said = crate::rbyaml::not_utf8(e.as_bytes(), &e.utf8_error());
                return Err(RBError::msg_fix("E_YAML", format!("{path} is {said}; Rabbit Brain YAML is UTF-8."), "Save the file as UTF-8."));
            }
        },
        Err(e) => return Err(RBError::could_not_read(&e, &path)),
    };
    let data = match crate::rbyaml::loads(&text, &path) {
        Ok(d) => d,
        Err(e) => {
            return Err(if e.fix.is_empty() { RBError::msg("E_YAML", e.to_string()) } else { RBError::msg_fix("E_YAML", e.to_string(), e.fix.clone()) });
        }
    };
    let kind: Value = match &data {
        Value::Dict(d) => d.get("kind").cloned().unwrap_or(Value::Null),
        _ => Value::Null,
    };
    let mut problems: Vec<String> = Vec::new();
    let is_format_1 = matches!(&data, Value::Dict(d) if d.get(FORMAT_KEY).map_or(false, |v| *v == Value::Int(1)));
    if !is_format_1 {
        problems.push(format!("not a record of Rabbit Brain format 1: it does not start with {FORMAT_KEY}: 1"));
    } else if kind.as_str() == Some("criteria") {
        let d = data.as_dict().cloned().unwrap_or_default();
        if d.get("criteria").map_or(false, |v| v.truthy() && !matches!(v, Value::List(_))) {
            problems.push("criteria: Input should be a valid list".into());
        }
        for c in criteria_of(&d) {
            let Value::Dict(cd) = &c else {
                problems.push("criterion ?: Input should be a valid dictionary".into());
                continue;
            };
            let mut form = cd.clone();
            form.insert("experiment".into(), d.get("experiment").cloned().unwrap_or(Value::Null));
            let id = cd.get("id").map(pyjson::py_str).unwrap_or_else(|| "None".into());
            for p in file_problems("criterion", Value::Dict(form)).unwrap_or_default() {
                problems.push(format!("criterion {id}: {p}"));
            }
        }
    } else {
        let body: Dict = data.as_dict().map(|d| d.iter().filter(|(k, _)| k.as_str() != FORMAT_KEY && k.as_str() != "kind").map(|(k, v)| (k.clone(), v.clone())).collect()).unwrap_or_default();
        // the kind is said as any field is: missing, not text, or not one of the format's
        match &kind {
            Value::Str(k) => match file_problems(k, Value::Dict(body)) {
                Some(found) => problems.extend(found),
                None => problems.push(format!("kind {} is not a kind format 1 has", pyjson::repr_str(k))),
            },
            Value::Null => problems.push("kind: Field required".into()),
            _ => problems.push("kind: Input should be a valid string".into()),
        }
    }
    // what the reader took, the writer can write: a failure here is rb's own
    let unwritable = |e: crate::rbyaml::DumpError| RBError::fault(format!("{path} read, and could not be written back: {}", e.0));
    let canonical = crate::rbyaml::canonical_json(&data).map_err(unwritable)?;
    let pretty = pyjson::Dumps { indent: Some(2), item_sep: ",", ensure_ascii: false, ..Default::default() };
    out.say(pyjson::dumps_with(&crate::ledger::without_writers(&data), &pretty).unwrap_or_default());
    if !problems.is_empty() {
        let shown_kind = kind.as_str().filter(|k| !k.is_empty()).unwrap_or("record").to_string();
        let first: Vec<String> = problems.iter().map(|p| p.split('\n').next().unwrap_or("").to_string()).collect();
        out.warn(&format!("{path} reads, but is not a valid {shown_kind}: {}", crate::assess::first_few(&first, 3, "; ")));
    }
    // the hashes log.jsonl records: a record's content without the format/kind header, each criterion's entry on its own
    let mut hashes = Dict::new();
    match (&data, kind.as_str()) {
        (Value::Dict(d), Some("criteria")) => {
            let mut shas = Dict::new();
            for c in criteria_of(d) {
                if let Value::Dict(cd) = &c {
                    let sha = crate::rbyaml::content_sha(&c).map_err(unwritable)?;
                    shas.insert(cd.get("id").map(pyjson::py_str).unwrap_or_else(|| "None".into()), Value::Str(sha));
                }
            }
            hashes.insert("criteria".into(), Value::Dict(shas));
        }
        _ => {
            let body = match &data {
                Value::Dict(d) => Value::Dict(d.iter().filter(|(k, _)| k.as_str() != FORMAT_KEY && k.as_str() != "kind").map(|(k, v)| (k.clone(), v.clone())).collect()),
                other => other.clone(),
            };
            hashes.insert("content_sha256".into(), Value::Str(crate::rbyaml::content_sha(&body).map_err(unwritable)?));
        }
    }
    let valid = problems.is_empty();
    out.data.insert("path".into(), Value::Str(path));
    out.data.insert("kind".into(), kind);
    out.data.insert("record".into(), data.clone());
    out.data.insert("canonical".into(), Value::Str(canonical));
    for (k, v) in hashes {
        out.data.insert(k, v);
    }
    out.data.insert("valid".into(), Value::Bool(valid));
    out.data.insert("problems".into(), Value::list_of_str(problems));
    Ok(if valid { 0 } else { crate::errors::EXIT_CHECK_FAILED })
}

/// The entries of a criteria file: its `criteria` list, or nothing when it holds no list there.
fn criteria_of(d: &Dict) -> Vec<Value> {
    match d.get("criteria") {
        Some(Value::List(l)) => l.clone(),
        _ => Vec::new(),
    }
}

/// How a run's numbers came, as a screen says it: a database row's are a row's, though the record keeps `file`.
fn basis_said(r: &Run) -> &str {
    if r.basis == "file" && r.receipt.row.is_some() { "row" } else { r.basis.as_str() }
}

/// rb show <note id>: the note as its experiment's (or the project's) screen lists it, and whether it was taken back.
fn show_note(led: &Ledger, id: &str, holder: &Obj, out: &mut Out) -> R {
    let (on, notes) = match holder {
        Obj::Experiment(e) => (e.id.clone(), &e.notes),
        Obj::Project(p) => ("the project".to_string(), &p.notes),
        _ => return Ok(0),
    };
    let Some(n) = notes.iter().find(|n| n.id.as_deref() == Some(id)) else { return Ok(0) };
    let w = led.note_words()?.remove(id).unwrap_or_default();
    out.say(format!("Note {id} on {on} ({}): {}", cut(&n.at, 10), n.text) + &words(&w, &n.by) + view::not_confirmed(&n.by));
    match &n.retracted {
        Some(r) => out.say(format!("  {}", view::retraction_words(r))),
        None => out.say(format!("  rb retract {id} --why \"...\" takes it back.")),
    }
    let mut d = crate::dict! {"kind" => "note", "experiment_id" => if matches!(holder, Obj::Experiment(_)) { Value::Str(on.clone()) } else { Value::Null }};
    for (k, v) in n.dump(false) {
        d.insert(k, v);
    }
    out.data.insert("object".into(), Value::Dict(d));
    Ok(0)
}

pub fn cmd_show(ns: &Namespace, out: &mut Out) -> R {
    let subject = s_or(ns, "id");
    if (subject.ends_with(".yaml") || subject.ends_with(".yml") || subject.ends_with(".json")) && Path::new(&subject).is_file() {
        return show_file(&subject, out);
    }
    let led = Ledger::open(None)?;
    let r = show_of(&led, subject, out);
    out.shows_for(&led);
    r
}

fn show_of(led: &Ledger, subject: String, out: &mut Out) -> R {
    let (_exp_id, name) = crate::ledger::parse_address(&subject);
    let dotted = subject.contains('.') && led.kind_of(&subject)?.is_none() && led.kind_of(subject.split('.').next().unwrap_or(""))? == Some("experiment");
    if name.is_some() || dotted {
        return show_setting(&led, &subject, out);
    }
    if led.kind_of(&subject)?.is_none() {
        if let Some(holder) = led.note_holder(&subject)? {
            return show_note(&led, &subject, &holder, out);
        }
    }
    let (kind, o) = led.get(&subject)?;
    let mut data = Dict::new();
    data.insert("object".into(), obj(kind, dump_obj(&o)));
    match &o {
        Obj::Criterion(c) => {
            let f = led.finding(c)?;
            let fv = Value::Dict(f.dump(false));
            data.insert("finding".into(), fv.clone());
            out.say(format!(
                "Criterion {} on {} ({}, {}{}): {}",
                c.id,
                c.experiment,
                c.role,
                c.origin,
                shown_writer(&c.created_by).map(|name| format!(", declared by {name}")).unwrap_or_default(),
                view::statement_said(&c.statement, &c.words, &c.created_by).unwrap_or_else(|| c.statement.clone() + &words(&c.words, &c.created_by))
            ));
            if let Some(said) = c.cited_ordering_said() {
                out.say(format!("  cited: {said}"));
            } else if let Some(src) = &c.source {
                out.say(
                    format!("  cited: {}", src.label())
                        + &match &src.resolved {
                            Some(r) => format!(" · states {}, read {}", crate::assess::declared(c.target.unwrap_or(f64::NAN)), pyjson::repr_str(&r.text)),
                            None => " · not checked".to_string(),
                        },
                );
            }
            out.say_all(finding_block(&fv, None));
            let relations = led.relations_touching(&std::collections::HashSet::from([c.id.clone()]), &indexmap::IndexMap::new())?;
            if !relations.is_empty() {
                for row in &relations {
                    // with what stops it being read, so the repair rb status gives can be checked here
                    out.say(format!("  {}", crate::ledger::relations::relation_line(row)));
                }
                data.insert("relations".into(), Value::List(relations));
            }
            let exp: Experiment = led.load(&c.experiment)?;
            let base = led.arms(&exp, c)?.2;
            let entry = led.catalogue_entry(&base)?;
            let unit = entry.as_ref().map(|m| m.unit.clone()).unwrap_or_default();
            let direction = entry.map(|m| m.direction).unwrap_or_else(|| "none".into());
            if let Some(cmp) = &f.comparison {
                if cmp.source != "runs" && !cmp.source.is_empty() && led.kind_of(&cmp.source)? == Some("analysis") {
                    let a: Analysis = led.load(&cmp.source)?;
                    let said: Vec<&String> = a
                        .comparisons
                        .iter()
                        .filter(|x| !x.note.is_empty() && x.subject == cmp.subject && x.reference == cmp.reference && x.estimand == cmp.estimand && x.metric == cmp.metric)
                        .map(|x| &x.note)
                        .collect();
                    if let Some(first) = said.first() {
                        out.say(format!("  as {} states it: {first}", a.id));
                    }
                }
            }
            if let Some(p) = &f.prospective {
                out.say(format!("  produced after it was declared: {}", crate::assess::reading_for(c, p, &unit, &direction)));
            }
            for h in &f.history {
                out.say(
                    format!("  earlier: {}", crate::assess::reading_for(c, h, &unit, &direction))
                        + &if h.superseded.is_empty() { String::new() } else { format!(" · {}", h.superseded) }
                        + &if h.at.is_empty() { String::new() } else { format!(" · recorded {}", cut(&h.at, 10)) },
                );
            }
            let last_part = c.metric.rsplit('.').next().unwrap_or("").to_string();
            let rows: std::collections::HashSet<String> = led.all::<Run>()?.iter().filter(|r| basis_said(r) == "row").map(|r| r.id.clone()).collect();
            for ob in &f.observations {
                let what = if !ob.arm.is_empty() {
                    format!("{}.{last_part}", ob.arm)
                } else if f.comparison.as_ref().map_or(false, |x| !x.reference.is_empty()) {
                    "difference".to_string()
                } else {
                    c.metric.clone()
                };
                out.say(
                    format!("  {}: {what} {} ({}) · unit {}", ob.run, crate::assess::observation_said(c, ob), if rows.contains(&ob.run) { "row" } else { ob.basis.as_str() }, ob.unit)
                        + &if ob.counted { String::new() } else { format!(" · not counted: {}", ob.reason) }
                        + &ob.repeats.as_ref().filter(|r| !r.is_empty()).map(|r| format!(" · repeats {r}")).unwrap_or_default()
                        + if ob.before_declared { " · produced before the declaration" } else { "" }
                        + &if ob.per_run.is_empty() {
                            String::new()
                        } else {
                            format!(" · {}", ob.per_run.iter().map(|(k, x)| format!("{k}={}", show(x))).collect::<Vec<_>>().join(", "))
                        },
                );
            }
        }
        Obj::Experiment(e) => show_experiment(&led, e, out, &mut data)?,
        Obj::Run(r) => {
            out.say(format!("Run {} on {} ({}{}){}", r.id, r.experiment, basis_said(r), if r.synthetic { ", synthetic" } else { "" }, by_writer(&r.created_by)));
            out.say(format!("  {}", crate::ledger::support::produced_line(r)));
            let made_before = crate::ledger::made_before_recorded(r);
            let given = crate::ledger::support::run_command(r);
            let watched = given.as_ref().map_or(false, |g| g.given == "observed");
            let after = crate::ledger::support::written_after(r);
            // how rb knows the command made the numbers, and when the file they were read from was written
            data.insert("command".into(), given.as_ref().map(|g| g.to_value()).unwrap_or(Value::Null));
            data.insert("made_before_recorded".into(), Value::Bool(made_before));
            data.insert("file_written_after".into(), after.clone().map(Value::Str).unwrap_or(Value::Null));
            data.insert("file_written".into(), crate::ledger::support::file_written(r).map(Value::Str).unwrap_or(Value::Null));
            let mut metrics: Vec<(&String, &f64)> = r.metrics.iter().collect();
            metrics.sort_by(|a, b| a.0.cmp(b.0));
            // a number read from a row under another name than its column's (a variant's) says the column
            let column = |k: &str| r.receipt.row.as_ref().and_then(|w| w.columns.get(k)).filter(|c| *c != k).map(|c| format!(" · column {c}")).unwrap_or_default();
            out.say_all(metrics.iter().map(|(k, x)| format!("  {k} = {}{}", g(**x), column(k))));
            if !r.per_run.is_empty() {
                // a per-run value read from a column of another name says the column
                let column = |k: &str| r.receipt.row.as_ref().and_then(|w| w.per_run.get(k)).filter(|c| *c != k).map(|c| format!(" (column {c})")).unwrap_or_default();
                out.say("  per-run: ".to_string() + &r.per_run.iter().map(|(k, x)| format!("{k}={}{}", show(x), column(k))).collect::<Vec<_>>().join(", "));
            }
            if let Some(row) = &r.receipt.row {
                out.say(format!("  {}", crate::cmds::record::row_words(row)));
            }
            if let Some(rep) = r.replaces.as_ref().filter(|x| !x.is_empty()) {
                out.say(format!("  replaces {rep} (retracted)"));
            }
            let later: Vec<String> = led.all::<Run>()?.iter().filter(|x| x.replaces.as_deref() == Some(r.id.as_str())).map(|x| x.id.clone()).collect();
            if !later.is_empty() {
                out.say(format!("  replaced by {}", later.join(", ")));
            }
            for u in &r.inputs {
                out.say(format!("  read {}", u.resource) + &if u.role.is_empty() { String::new() } else { format!(" ({})", u.role) });
            }
            for u in &r.outputs {
                out.say(format!("  made {}", u.resource) + &if u.role.is_empty() { String::new() } else { format!(" ({})", u.role) });
            }
            if !r.host.is_empty() {
                out.say("  ran on: ".to_string() + &r.host.iter().map(|(k, v)| format!("{k}={v}")).collect::<Vec<_>>().join(", "));
            }
            if let Some(rr) = r.review_run.as_ref().filter(|x| !x.is_empty()) {
                out.say(format!("  review run: {rr}"));
            }
            let rc = &r.receipt;
            if let Some(g) = &given {
                out.say(format!("  {}", g.line()));
            }
            let pcommit = rc.produced.get("commit").filter(|v| v.truthy()).map(pyjson::py_str).unwrap_or_default();
            let p12 = cut(&pcommit, 12);
            let ptree = rc.produced.get("tree").filter(|v| v.truthy()).map(pyjson::py_str);
            let from = rc.produced.get("from").map(pyjson::py_str).unwrap_or_default();
            out.say(
                format!(
                    "  code that made it: {}",
                    if p12.is_empty() { "unknown".to_string() } else { format!("{p12} ({})", if from == "flag" { "as given".to_string() } else { from }) }
                ) + &match &ptree {
                    // the exact code only where rb's journal watched the command make these numbers
                    Some(t) if watched => format!(", the exact code git tree {} (git diff {p12} {})", cut(t, 12), cut(t, 12)),
                    Some(t) => format!(", git tree {} (git diff {p12} {})", cut(t, 12), cut(t, 12)),
                    None => String::new(),
                },
            );
            if let Some(att) = rc.attached.as_ref().filter(|a| !a.is_empty()) {
                let commit = att.get("commit").filter(|v| v.truthy()).map(|c| cut(&pyjson::py_str(c), 12)).unwrap_or_else(|| "no commit yet".into());
                let dirty = att.get("dirty").map_or(false, |v| v.truthy());
                out.say(format!(
                    "  repository when recorded{}: {commit}{}",
                    if made_before { ", not when run" } else { "" },
                    if dirty { " with uncommitted changes" } else { "" }
                ));
                if dirty {
                    out.say_all(crate::cmds::exact_code_at(&led.root, att, made_before, watched));
                }
            }
            if let Some(cfg) = rc.config.as_ref().map(|c| c.given()) {
                let (m, d) = (cfg.matches.len(), cfg.mismatches.len());
                let verb = |n: usize, one: &'static str, many: &'static str| if n == 1 { one } else { many };
                out.say(format!("  config {}: {m} {}, {d} {}, {} absent", cfg.path, verb(m, "matches", "match"), verb(d, "differs", "differ"), cfg.absent.len()));
                // the check as rb reads it now, beside the receipt that keeps what an earlier rb stored
                data.insert("config".into(), cfg.to_value());
            }
            for fr in &r.files {
                out.say(format!("  file {} sha256 {} ({} {}) · {}", fr.path, cut(&fr.sha256, 12), fr.bytes, if fr.bytes == 1 { "byte" } else { "bytes" }, led.file_state(fr)));
            }
            if let Some(said) = crate::ledger::support::file_sentence(r) {
                out.say(format!("  {said}"));
            }
            for (k, url) in &r.links {
                out.say(format!("  {k}: {url}"));
            }
            if !r.note.is_empty() {
                out.say(format!("  note: {}{}", r.note, view::not_confirmed(&r.created_by)));
            }
        }
        Obj::Resource(res) => {
            let av = led.availability(res);
            data.insert("availability".into(), Value::Dict(av.clone()));
            let avv = Value::Dict(av.clone());
            out.say(
                format!("Resource {}: {}", res.id, res.name)
                    + &if res.version.is_empty() { String::new() } else { format!(" {}", res.version) }
                    + &format!(" ({}) · {}", res.r#type, gs(&avv, "state")),
            );
            if !res.subset.is_empty() {
                out.say(format!("  subset: {}", res.subset));
            }
            let ids: Vec<String> = [
                res.sha256.as_ref().filter(|s| !s.is_empty()).map(|s| format!("sha256 {s}")).unwrap_or_default(),
                if res.digest.is_empty() { String::new() } else { format!("digest {}", res.digest) },
                if res.version.is_empty() { String::new() } else { format!("version {}", res.version) },
            ]
            .into_iter()
            .filter(|x| !x.is_empty())
            .collect();
            let ident = if ids.is_empty() { avv.get("identity").map(|i| gs(i, "text")).unwrap_or_default() } else { ids.join(" · ") };
            out.say(format!("  identity: {ident}"));
            for loc in items(&avv, "locations") {
                out.say(format!("  {}:{} · {}", gs(loc, "provider"), gs(loc, "ref"), gs(loc, "state")));
            }
            if let Some(a) = &res.acquisition {
                let parts: Vec<String> = [a.source.clone(), a.url.clone().unwrap_or_default(), a.recipe.clone().unwrap_or_default()].into_iter().filter(|x| !x.is_empty()).collect();
                out.say(format!("  how to get it again: {}", parts.join(" · ")));
            }
            if !res.note.is_empty() {
                out.say(format!("  note: {}", res.note));
            }
            for e in &res.exposures {
                out.say(
                    format!("  {}{} ", e.how, by_writer(&e.by))
                        + &match e.at.as_ref().filter(|a| !a.is_empty()) {
                            Some(at) => format!("at {}", cut(at, 10)),
                            None => "at a time nobody recorded".to_string(),
                        }
                        + &if e.why.is_empty() { String::new() } else { format!(": {}", e.why) },
                );
            }
            // the runs that read it, then those that made it: one rule with rb retract's and the shell's, each use said
            let standing = led.standing_runs()?;
            let (read, made) = crate::graph::runs_using(&led, res)?;
            let mut users: Vec<(&Run, &ResourceUse, &str)> = Vec::new();
            for (ids, word) in [(&read, "read"), (&made, "made")] {
                for r in ids.iter().filter_map(|(id, _)| standing.iter().find(|r| r.id == *id)) {
                    let uses = if word == "read" { &r.inputs } else { &r.outputs };
                    users.extend(uses.iter().filter(|u| u.resource == res.id).map(|u| (r, u, word)));
                }
            }
            for (r, u, word) in users.iter().take(12) {
                out.say(
                    format!("  {word} by run {} ({}, {})", r.id, r.experiment, cut(&r.created_at, 10))
                        + &u.availability.as_ref().filter(|x| !x.is_empty()).map(|x| format!(" · {x} then")).unwrap_or_default()
                        + &u.sha256.as_ref().filter(|x| !x.is_empty()).map(|x| format!(" · sha256 {}", cut(x, 12))).unwrap_or_default(),
                );
            }
            if users.len() > 12 {
                out.say(format!("  and {} more {}", users.len() - 12, if users.len() == 13 { "run" } else { "runs" }));
            }
            // the analyses that read it, themselves or through what they read: one rule with rb retract's and the shell's
            let reading: Vec<String> = crate::graph::users_of(&led, res)?.analyses;
            let readers: Vec<Analysis> = led.live::<Analysis>()?.into_iter().filter(|a| reading.contains(&a.id)).collect();
            for a in readers.iter().take(12) {
                let direct = a.inputs.iter().any(|u| u.resource == res.id);
                out.say(format!("  read by analysis {} ({})", a.id, a.experiment) + if direct { "" } else { ", through what it read" });
            }
            if readers.len() > 12 {
                out.say(format!("  and {} more {}", readers.len() - 12, if readers.len() == 13 { "analysis" } else { "analyses" }));
            }
            data.insert(
                "read_by".into(),
                crate::obj! {
                    "runs" => Value::List(users.iter().filter(|(_, _, w)| *w == "read").map(|(r, _, _)| Value::Str(r.id.clone())).collect()),
                    "analyses" => Value::List(readers.iter().map(|a| Value::Str(a.id.clone())).collect()),
                },
            );
        }
        Obj::Analysis(a) => {
            out.say(
                format!("Analysis {} on {} ({}) · recorded {}{}", a.id, a.experiment, a.basis, cut(&a.created_at, 19).replace('T', " "), by_writer(&a.created_by))
                    + &if a.title.is_empty() { String::new() } else { format!(": {}", a.title) },
            );
            if !a.method.is_empty() || !a.unit.is_empty() {
                let level = crate::pyfloat::round0(a.level.filter(|l| *l != 0.0).unwrap_or(0.95) * 100.0);
                let parts: Vec<String> = [
                    a.method.clone(),
                    match a.units.filter(|n| *n != 0) {
                        Some(n) => format!("{n} {}", crate::assess::plural(&a.unit, n)),
                        None => a.unit.clone(),
                    },
                    a.draws.filter(|n| *n != 0).map(|n| format!("{n} {}", crate::assess::plural("draw", n))).unwrap_or_default(),
                    format!("{}%", fmt(level, "g")),
                ]
                .into_iter()
                .filter(|x| !x.is_empty())
                .collect();
                out.say(format!("  method: {}", parts.join(" · ")));
            }
            let mut now: Vec<Caveat> = Vec::new();
            for c in &a.comparisons {
                let [est, lo, hi] = led.comparison_said(&a.experiment, c)?;
                let iv = match (c.lo, c.hi) {
                    // an interval is its two bounds: one bound alone is not one
                    (Some(_), Some(_)) => format!(" [{lo}, {hi}]"),
                    _ => " (no interval)".to_string(),
                };
                let arms = if !c.reference.is_empty() {
                    format!("{} vs {}", c.subject, c.reference)
                } else if !c.subject.is_empty() {
                    c.subject.clone()
                } else {
                    "whole run".to_string()
                };
                out.say(
                    format!(
                        "  {}: {arms}{}: {}{iv}",
                        c.metric,
                        if c.estimand.is_empty() { String::new() } else { format!(" ({})", c.estimand) },
                        est
                    ) + &match c.n.filter(|n| *n != 0) {
                        Some(n) if !c.unit.is_empty() => format!(" · {n} {}", crate::assess::plural(&c.unit, n)),
                        _ => String::new(),
                    } + &match &c.source {
                        Some(src) => {
                            format!(" · stated in {}", src.label())
                                + &src.resolved.as_ref().map(|r| format!(", read {}", pyjson::repr_str(&cut(&r.text, 80)))).unwrap_or_default()
                        }
                        None => String::new(),
                    },
                );
                if !c.note.is_empty() {
                    out.say(format!("    note: {}", c.note));
                }
                // the document is re-read on every read, as each finding that reads this analysis does
                if let Some(src) = c.source.as_ref().filter(|s| a.basis == "reported" && s.resolved.is_some()) {
                    let nums: Vec<f64> = [Some(c.estimate), c.lo, c.hi].into_iter().flatten().collect();
                    let (state, why) = led.recheck_stated(src, &nums);
                    if state == "stale" {
                        out.say(format!("    ✗ {why}"));
                        now.push(Caveat::problem("analysis_source_changed", &a.id, format!("{}'s numbers: {why}", a.id)));
                    }
                }
            }
            data.insert("caveats".into(), Value::List(now.iter().map(|c| Value::Dict(c.dump(false))).collect()));
            if !a.reads.is_empty() {
                out.say(format!("  reads: {}", a.reads.join(", ")));
            }
            for u in &a.inputs {
                out.say(
                    format!("  read {}", u.resource)
                        + &if u.role.is_empty() { String::new() } else { format!(" ({})", u.role) }
                        + &u.sha256.as_ref().filter(|x| !x.is_empty()).map(|x| format!(" · sha256 {}", cut(x, 12))).unwrap_or_default(),
                );
            }
            for fr in &a.files {
                out.say(format!("  file {} sha256 {} ({} {}) · {}", fr.path, cut(&fr.sha256, 12), fr.bytes, if fr.bytes == 1 { "byte" } else { "bytes" }, led.file_state(fr)));
            }
            if !a.supersedes.is_empty() {
                out.say(format!("  supersedes {}", a.supersedes.join(", ")) + &view::reason_part(&a.why, &a.created_by, ": "));
            }
            let later: Vec<String> = led.live::<Analysis>()?.iter().filter(|x| x.supersedes.contains(&a.id)).map(|x| x.id.clone()).collect();
            if !later.is_empty() {
                out.say(format!("  superseded by {}", later.join(", ")));
            }
            let given = crate::ledger::support::analysis_command(a);
            data.insert("command".into(), given.as_ref().map(|g| g.to_value()).unwrap_or(Value::Null));
            if let Some(g) = &given {
                out.say(format!("  {}", g.line()));
            }
            out.say(
                "  produced: ".to_string()
                    + &match a.produced_at.as_ref().filter(|p| !p.is_empty()) {
                        Some(p) => format!("{} ({})", cut(p, 19).replace('T', " "), a.produced_basis),
                        None if a.produced_basis == "unknown" => "unknown".to_string(),
                        None => "when it was recorded".to_string(),
                    },
            );
        }
        Obj::Decision(d) => {
            let a = led.authority(&d.id)?;
            match a.as_ref().filter(|a| !a.is_call()) {
                // what waits for a confirmation, or waited, is said as proposed or mentioned, never as made
                Some(a) => out.say(format!("Decision {}: {} · recorded {}", d.id, crate::cmds::record::decision_said(d, a, crate::ledger::made(&d.action)), d.at)),
                // a past call is dated as given; when it was recorded comes after
                None if d.past.is_some() => out.say(
                    format!("Decision {}: {} {}{}{}{}", d.id, crate::ledger::made(&d.action), d.subject, by_writer(&d.by), crate::view::past_phrase(&Value::Dict(d.dump(false))), crate::ledger::reason_said(&d.why, &d.by, true, led.confirms(&d.id)?))
                        + &words(&d.words, &d.by)
                        + &format!(" · recorded {}", d.at),
                ),
                None => out.say(
                    format!("Decision {}: {} {}{} at {}", d.id, crate::ledger::made(&d.action), d.subject, by_writer(&d.by), d.at)
                        + &crate::ledger::reason_said(&d.why, &d.by, false, led.confirms(&d.id)?)
                        + &words(&d.words, &d.by),
                ),
            }
            if let Some(a) = &a {
                out.say_all(crate::cmds::record::authority_lines(d, a));
                data.insert("authority".into(), a.to_value());
            }
            let after = led.runs_after_past_call(d)?;
            if !after.is_empty() {
                let when = d.past.as_ref().map(|p| p.when.clone()).unwrap_or_default();
                out.say(format!("  {}", crate::ledger::runs_after_said(&d.subject, &when, &after).trim_start_matches(" · ")));
                data.insert("runs_after".into(), Value::list_of_str(after));
            }
            if !d.revisit.is_empty() && a.as_ref().map_or(true, |a| a.is_call()) {
                out.say(format!("  revisit: {}", d.revisit));
            }
            let (current, moved) = led.decision_current(d)?;
            // what a call was made on: a call that waits for a confirmation was made on nothing yet
            let made_on = if a.as_ref().map_or(true, |a| a.is_call()) { d.basis.iter().collect::<Vec<_>>() } else { Vec::new() };
            for (cid, b) in made_on {
                // a settled gate's reading is not part of the call's own currency; it moves on its own
                let since = if crate::ledger::settled_gate(d, cid) { led.reading_moved(cid, b)? } else { moved.contains(cid) };
                let mark = if led.newer_only("criterion", cid) {
                    "  · only a newer rb reads it now"
                } else if since {
                    "  · moved since"
                } else {
                    ""
                };
                out.say(format!("  {}", led.basis_line(d, cid, b, false)) + mark);
            }
            data.insert("current".into(), Value::Bool(current));
        }
        Obj::Question(q) => {
            let state = led.question_state(q)?;
            out.say(format!("Question {} ({state}): {}", q.id, q.text));
            if let Some(p) = q.parent.as_ref().filter(|p| !p.is_empty()) {
                out.say(format!("  under: {p}"));
            }
            let kids = led.children(&q.id)?;
            if !kids.is_empty() {
                let mut parts = Vec::new();
                for k in &kids {
                    parts.push(format!("{} ({})", k.id, led.question_state(k)?));
                }
                out.say(format!("  sub-questions: {}", parts.join(", ")));
            }
            let filed: Vec<String> = led.live::<Experiment>()?.iter().filter(|e| e.questions.contains(&q.id)).map(|e| e.id.clone()).collect();
            out.say(format!("  experiments: {}", if filed.is_empty() { "none filed under it yet".to_string() } else { filed.join(", ") }));
            let st = Value::Dict(led.status(None)?);
            let inquiry = if let Some(qd) = items(&st, "questions").iter().find(|x| gs(x, "id") == q.id) {
                qd.get("inquiry").cloned().unwrap_or(Value::empty_dict())
            } else {
                Value::Dict(led.inquiry(&q.id, &indexmap::IndexMap::new())?)
            };
            {
                let r = inquiry.get("rollup").cloned().unwrap_or(Value::empty_dict());
                for line in view::summary_lines(&inquiry) {
                    out.say(format!("  {line}"));
                }
                data.insert("inquiry".into(), inquiry);
                // the counts in rb's words, as rb status says them: 2 criteria met, 1 point only
                let mut rules: Vec<(String, i64)> = match r.get("rules") {
                    Some(Value::Dict(d)) => d.iter().map(|(k, n)| (k.clone(), n.as_i64().unwrap_or(0))).collect(),
                    _ => Vec::new(),
                };
                rules.sort_by_key(|(k, _)| tally_order(k));
                let rules: Vec<String> = rules.iter().map(|(k, n)| format!("{n} {}", crate::assess::rule_label(k, *n))).collect();
                let results: Vec<String> = match r.get("results") {
                    Some(Value::Dict(d)) => d.iter().map(|(k, n)| format!("{} {}", pyjson::py_str(n), k.replace('_', " "))).collect(),
                    _ => Vec::new(),
                };
                out.say(
                    "  linked here, by rule: ".to_string()
                        + &if rules.is_empty() { "no criteria yet".to_string() } else { rules.join(" · ") }
                        + " · by result: "
                        + &if results.is_empty() { "none".to_string() } else { results.join(" · ") },
                );
                let untested = items(&r, "untested");
                for u in untested.iter().take(6) {
                    out.say(format!("  untested: {}", pyjson::py_str(u)));
                }
                if untested.len() > 6 {
                    out.say(format!("  untested: ... and {} more: rb show {} --json lists every one", untested.len() - 6, q.id));
                }
                data.insert("rollup".into(), r);
            }
        }
        Obj::Metric(m) => {
            let text = if m.description.is_empty() { String::new() } else { m.description.clone() };
            out.say(format!("Metric {} ({}): {text}", m.id, m.direction));
        }
        Obj::Project(p) => {
            out.say(format!("Project {} (): ", p.id));
        }
    }
    if let Some(ret) = o.retracted() {
        if !ret.scope.is_empty() {
            out.say(format!("  SUPERSEDED for {} {}{}{} (its other numbers still count)", ret.scope.join(", "), ret.at, by_writer(&ret.by), view::reason_part(&ret.why, &ret.by, ": ")));
        } else {
            out.say(format!("  RETRACTED {}{}{}", ret.at, by_writer(&ret.by), view::reason_part(&ret.why, &ret.by, ": ")));
        }
    }
    if let Obj::Question(q) = &o {
        data.insert("state".into(), Value::Str(led.question_state(q)?));
    }
    let decisions = if kind == "decision" { Vec::new() } else { led.decisions_on(o.id())? };
    if !decisions.is_empty() {
        let mut list = Vec::new();
        for d in &decisions {
            let (current, moved) = if !d.basis.is_empty() || kind == "experiment" || kind == "question" { led.decision_current(d)? } else { (true, Vec::new()) };
            // a missed gate the call settles is not what it was made on: once it misses on another reading, rb status
            // asks for a call on it again, so it is named beside the call too
            let mut gates: Vec<String> = Vec::new();
            for (key, then) in d.basis.iter().filter(|(key, _)| crate::ledger::settled_gate(d, key)) {
                if led.missed_again(key, then)? {
                    gates.push(key.clone());
                }
            }
            let mut dd = d.dump(false);
            dd.insert("current".into(), Value::Bool(current));
            dd.insert("settled_gates_moved".into(), Value::list_of_str(gates.iter().cloned()));
            let after = led.runs_after_past_call(d)?;
            if !after.is_empty() {
                dd.insert("runs_after".into(), Value::list_of_str(after.iter().cloned()));
            }
            list.push(Value::Dict(dd));
            let when = d.past.as_ref().map(|p| p.when.clone()).unwrap_or_default();
            out.say(
                format!("  decision {}: {}{}{} at {}{}{}", d.id, crate::ledger::made(&d.action), by_writer(&d.by), crate::view::past_phrase(&Value::Dict(d.dump(false))), d.at, crate::ledger::reason_said(&d.why, &d.by, d.past.is_some(), led.confirms(&d.id)?), words(&d.words, &d.by))
                    + &crate::ledger::runs_after_said(&d.subject, &when, &after)
                    + &if d.revisit.is_empty() { String::new() } else { format!(" · revisit: {}", d.revisit) }
                    + &if current { String::new() } else { format!("{} ({})", crate::ledger::moved_since(d.past.is_some()), crate::assess::first_few(&moved, 4, ", ")) }
                    + &match gates.as_slice() {
                        [] => String::new(),
                        [gate] => format!(" · settled the missed gate {gate} on a reading that has moved since"),
                        _ => format!(" · settled the missed gates {} on readings that have moved since", gates.join(", ")),
                    },
            );
        }
        data.insert("decisions".into(), Value::List(list));
    }
    // a call on it that waits for a confirmation changes nothing: said apart, with the command that confirms it
    let pending = if kind == "decision" { Vec::new() } else { led.pending_on(o.id())? };
    if !pending.is_empty() {
        let mut list = Vec::new();
        for (d, a) in &pending {
            let after = led.runs_after_past_call(d)?;
            let when = d.past.as_ref().map(|p| p.when.clone()).unwrap_or_default();
            // what git shows of a past call's document, beside it, as rb status says it
            let found = if d.past.as_ref().map_or(false, |p| p.source.is_some()) { format!(" · {}", a.basis) } else { String::new() };
            out.say(format!(
                "  {}: {}{found}{} · confirm: rb confirm {}",
                d.id,
                crate::cmds::record::decision_said(d, a, crate::ledger::made(&d.action)),
                crate::ledger::runs_after_said(&d.subject, &when, &after),
                d.id
            ));
            let mut dd = d.dump(false);
            dd.insert("authority".into(), a.to_value());
            if !after.is_empty() {
                dd.insert("runs_after".into(), Value::list_of_str(after));
            }
            list.push(Value::Dict(dd));
        }
        data.insert("pending".into(), Value::List(list));
    }
    if crate::ledger::KINDS.iter().any(|k| k.0 == kind) {
        if let Some(why_out) = led.edited_outside(kind, o.id())? {
            let changes = led.changed_outside(kind, o.id())?;
            let cut = led.shallow_cut(kind, o.id())?;
            out.warn_of(
                led,
                &(format!("{why_out}: rb does not read it as it stands")
                    + &if changes.is_empty() { String::new() } else { format!(": {}", crate::assess::first_few(&changes, 6, "; ")) }
                    + &if cut { format!(" ({UNSHALLOW} fetches the whole history)") } else { " (rb doctor --restore puts back what rb wrote)".to_string() }),
            );
            data.insert(
                if cut { "shallow_clone" } else { "edited_outside_rb" }.into(),
                crate::obj! {"why" => why_out, "changes" => Value::List(changes.into_iter().map(Value::Str).collect())},
            );
        }
    }
    for (k, v) in data {
        out.data.insert(k, v);
    }
    Ok(0)
}

fn show_setting(led: &Ledger, subject: &str, out: &mut Out) -> R {
    let (exp, name, st) = led.resolve_setting(subject)?;
    let address = format!("{}/{name}", exp.id);
    // what this read finds of it, the same re-check rb status and rb show <experiment> make
    let now: Vec<Caveat> = led.setting_caveats(&exp, led.has_origin(&exp)?, None)?.into_iter().filter(|c| c.subject == address).collect();
    let flag = now.iter().find(|c| c.code == "stale" || c.code == "conflict").map(|c| format!(" · {}", c.code.to_uppercase())).unwrap_or_default();
    out.say(if st.per_run {
        format!("{address} · per-run: each run gives its own")
    } else {
        format!("{address} = {} · {}{}{flag}", show(&st.value), led.status_read(&exp, &name, &st)?, if st.required { "" } else { " (optional)" })
    });
    if let Some(src) = &st.source {
        out.say(
            format!("  source: {}", src.label())
                + &match &src.resolved {
                    Some(r) => {
                        format!(" · read {}", pyjson::repr_str(&r.text))
                            + &r.commit.as_ref().filter(|c| !c.is_empty()).map(|c| format!(" @{}", cut(c, 7))).unwrap_or_default()
                    }
                    None => String::new(),
                },
        );
    }
    if let Some(said) = crate::sources::said_conflict(&st) {
        out.say(format!("  CONFLICT: {said}"));
    }
    for c in now.iter().filter(|c| !(c.code == "conflict" && st.conflict.is_some())) {
        out.say(format!("  {} {}", if c.problem { "✗" } else { "·" }, c.text));
    }
    if let Some(c) = &st.cited {
        out.say(format!("  cited source used {}", show(&c.value)) + &c.source.as_ref().map(|s| format!(" ({})", s.label())).unwrap_or_default());
    }
    let history: Vec<Dict> = led
        .log_about(None, Some(&address))?
        .into_iter()
        .filter(|r| matches!(r.get("op").and_then(|v| v.as_str()), Some("spec_set") | Some("spec_verify") | Some("retract")))
        .collect();
    if !history.is_empty() {
        out.say("  history:");
        for r in &history {
            let rv = Value::Dict(r.clone());
            // a check of several settings (rb spec verify) says this one's outcome
            let d = crate::ledger::checked(r, &name).map(Value::Dict).unwrap_or(Value::empty_dict());
            let what = first_truthy(&d, &["change", "outcome", "after", "why"]);
            out.say(format!(
                "    {}{} {}: {what}",
                cut(&rv.get("at").map(pyjson::py_str).unwrap_or_default(), 19),
                shown_writer(&gs(&rv, "actor")).map(|name| format!(" {name}")).unwrap_or_default(),
                rv.get("op").map(pyjson::py_str).unwrap_or_else(|| "None".into())
            ));
        }
    }
    for d in led.rulings_on(&address)? {
        out.say(format!("  decision {}: {}{}: {}", d.id, crate::ledger::made(&d.action), by_writer(&d.by), d.why));
    }
    let mut object = crate::dict! {"kind" => "setting", "id" => address.clone(), "address" => address.clone(), "experiment_id" => exp.id.clone()};
    for (k, v) in st.dump(false) {
        object.insert(k, v);
    }
    out.data.insert("object".into(), Value::Dict(object));
    out.data.insert("history".into(), Value::List(history.into_iter().map(Value::Dict).collect()));
    out.data.insert("caveats".into(), Value::List(now.iter().map(|c| Value::Dict(c.dump(false))).collect()));
    Ok(0)
}

fn show_experiment(led: &Ledger, exp: &Experiment, out: &mut Out, data: &mut Dict) -> Res<()> {
    let state = led.experiment_state(exp, None)?;
    out.say(format!("Experiment {}: {} · {state}", exp.id, exp.title) + &if exp.questions.is_empty() { String::new() } else { format!(" · under {}", exp.questions.join(", ")) });
    // its design's standing, once, with the command while it waits, and what changed since the user confirmed it
    let design = led.design(&exp.id)?.unwrap_or_default();
    out.say_all(design.lines().into_iter().map(|l| format!("  {l}")).collect::<Vec<_>>());
    for (label, text) in [("purpose", &exp.purpose), ("hypothesis", &exp.hypothesis)] {
        if !text.is_empty() {
            out.say(format!("  {label}: {text}"));
        }
    }
    if design.not_recorded.contains(&"hypothesis or purpose") {
        out.say("  hypothesis or purpose: not recorded");
    }
    match view::why_part(&exp.why, &led.why_by(exp, &design)) {
        Some((label, text)) => out.say(format!("  {label}: {text}")),
        None if design.not_recorded.contains(&"why") => out.say("  why: not recorded"),
        None => (),
    }
    let (gate_words, gate_by) = led.gate_words()?.remove(&exp.id).unwrap_or_default();
    let command = view::gate_command(&exp.id, &exp.if_holds, &exp.if_not, &gate_words, &design.waiting_command());
    for (label, text) in view::gate_parts(&exp.if_holds, &exp.if_not, &gate_words, &design.writer(&gate_by, "gate"), &command, design.gate_first()) {
        out.say(format!("  {label}: {text}"));
    }
    if !exp.variants.is_empty() {
        out.say(
            "  variants: ".to_string()
                + &exp
                    .variants
                    .iter()
                    .map(|v| format!("{} ({})", v.name, v.role) + if v.retracted.is_some() { " RETRACTED" } else { "" })
                    .collect::<Vec<_>>()
                    .join(" · "),
        );
    }
    if !exp.varies.is_empty() {
        out.say(format!("  varies: {}", exp.varies.join(", ")));
    }
    let freeze_sha = led.freeze_sha(exp, None)?;
    out.say(
        format!("  spec {} · ", cut(&freeze_sha, 12))
            + &match &exp.frozen {
                Some(fz) => {
                    let by = design.writer(&fz.by, "freeze_why");
                    format!("frozen {}{} ({})", fz.at, by_writer(&fz.by), cut(&fz.sha256, 12)) + &view::reason_part(&fz.why, &by, ": ") + &words(&fz.words, &fz.by)
                }
                None => "not frozen".to_string(),
            },
    );
    let drift = led.spec_drift(exp)?;
    if let Some(d) = drift.as_ref().filter(|d| !d.is_empty()) {
        out.say(format!("  {d}"));
    }
    if !exp.folder.is_empty() {
        let read = crate::repo::read_folder(led, exp, None)?;
        data.insert("repository".into(), Value::Dict(read.clone()));
        out.say(
            format!("  in the repository: {}", exp.folder)
                + &match &exp.indexed {
                    Some(idx) => {
                        format!(" · indexed {}{}", cut(&idx.at, 16).replace('T', " "), by_writer(&idx.by))
                            + &idx.commit.as_ref().filter(|c| !c.is_empty()).map(|c| format!(" at {}", cut(c, 7))).unwrap_or_default()
                    }
                    None => String::new(),
                },
        );
        for q in &exp.quotes {
            if q.kind == "command" {
                continue;
            }
            out.say(format!("    {}: \"{}\" ({})", q.kind, q.text, crate::repo::where_of(q, &exp.folder)) + if q.cut { " (continues in the file)" } else { "" });
        }
        for q in exp.quotes.iter().filter(|q| q.kind == "command") {
            out.say(format!("    command ({}:{}):", q.path, q.lines));
            out.say_all(crate::ledger::py_splitlines(&q.text).into_iter().map(|ln| format!("      {ln}")));
        }
        out.say_all(crate::repo::facts(&read).into_iter().map(|line| format!("    {line}")));
        if let Some(Value::List(files)) = read.get("files") {
            for f in files {
                if gs(f, "state") != "present" {
                    out.say(format!("    {}: {} since indexed", gs(f, "path"), gs(f, "state")));
                }
            }
        }
    }
    for (i, a) in exp.amendments.iter().enumerate() {
        let by = design.writer(&a.by, &format!("amendment_why:{}", i + 1));
        out.say(format!("  amended {}{}: {}{}{}", a.at, by_writer(&a.by), a.change, view::reason_part(&a.why, &by, " · because: "), words(&a.words, &a.by)));
    }
    let caveats = led.setting_caveats(exp, led.has_origin(exp)?, None)?;
    let mut flags: IndexMap<String, String> = IndexMap::new();
    for c in &caveats {
        if ["stale", "conflict", "provisional", "unknown", "vouched"].contains(&c.code.as_str()) {
            let key = c.subject.split_once('/').map(|(_, t)| t.to_string()).unwrap_or_default();
            flags.insert(key, c.code.clone());
        }
    }
    let settings = exp.all_settings();
    if !settings.is_empty() {
        out.say("  settings:");
        for (full, s) in &settings {
            let place = s.source.as_ref().map(|x| x.label()).unwrap_or_else(|| "no source".into());
            let st = if s.per_run { "per-run".to_string() } else { led.status_read(exp, full, s)? + if s.required { "" } else { " (optional)" } };
            let value = if s.per_run { "(each run)".to_string() } else { show(&s.value) };
            let flag = flags.get(full).map(|x| x.as_str());
            out.say(
                format!("    {full:<24} {value:<14} {st} · {place}")
                    + &match flag {
                        Some(f) if f == "stale" || f == "conflict" => format!(" · {}", f.to_uppercase()),
                        _ => String::new(),
                    }
                    + if flag == Some("vouched") { " · vouched" } else { "" },
            );
        }
    }
    for c in caveats.iter().filter(|c| ["confound", "confound_accepted", "varies_unchanged", "source_postdates_run"].contains(&c.code.as_str())) {
        out.say(format!("  {} {}", if c.problem { "✗" } else { "·" }, c.text));
    }
    let said = led.note_words()?;
    for n in exp.notes.iter().filter(|n| n.retracted.is_none()) {
        let w = n.id.as_ref().and_then(|id| said.get(id)).cloned().unwrap_or_default();
        out.say(format!("  {} ({}{}): {}", view::note_label(n.id.as_deref()), shown_writer(&n.by).map(|name| format!("{name}, ")).unwrap_or_default(), cut(&n.at, 10), n.text) + &words(&w, &n.by) + view::not_confirmed(&n.by));
    }
    for r in &exp.relations {
        out.say(format!("  {} {}", r.kind.replace('_', " "), r.target) + &relation_note(&r.note, &r.by));
    }
    let mut incoming: Vec<(String, Relation)> = Vec::new();
    for e in led.live::<Experiment>()? {
        if e.id == exp.id {
            continue;
        }
        for r in &e.relations {
            if r.target == exp.id || r.target == exp.uid {
                incoming.push((e.id.clone(), r.clone()));
            }
        }
    }
    for (eid, r) in &incoming {
        out.say(format!("  {eid} {} it", r.kind.replace('_', " ")) + &relation_note(&r.note, &r.by));
    }
    data.insert(
        "related_from".into(),
        Value::List(incoming.iter().map(|(eid, r)| crate::obj! {"experiment" => eid.clone(), "kind" => r.kind.clone(), "note" => r.note.clone()}).collect()),
    );
    let mut findings = Vec::new();
    for c in led.criteria_of(&exp.id)? {
        if c.retracted.is_none() {
            findings.push(led.finding(&c)?);
        }
    }
    let fvals: Vec<Value> = findings.iter().map(|f| Value::Dict(f.dump(false))).collect();
    if !fvals.is_empty() {
        out.say("  criteria:");
        for block in finding_blocks(&fvals, None) {
            out.say_all(block);
        }
    }
    let runs: Vec<Run> = led.all::<Run>()?.iter().filter(|r| r.experiment == exp.id).cloned().collect();
    if !runs.is_empty() {
        out.say(
            "  runs: ".to_string()
                + &runs
                    .iter()
                    .map(|r| {
                        format!(
                            "{}{}{}",
                            r.id,
                            if r.retracted.is_some() { " (retracted)" } else { "" },
                            r.repeats.as_ref().filter(|x| !x.is_empty()).map(|x| format!(" (repeats {x})")).unwrap_or_default()
                        )
                    })
                    .collect::<Vec<_>>()
                    .join(", "),
        );
    }
    let analyses: Vec<Analysis> = led.all::<Analysis>()?.iter().filter(|a| a.experiment == exp.id).cloned().collect();
    if !analyses.is_empty() {
        out.say("  analyses: ".to_string() + &analyses.iter().map(|a| format!("{}{}", a.id, if a.retracted.is_some() { " (retracted)" } else { "" })).collect::<Vec<_>>().join(", "));
    }
    let rerun = led.rerunnability(exp)?;
    if !rerun.is_empty() {
        out.say("  to run it again:");
        for row in &rerun {
            out.say(format!("    {:<12} {:<8} {}", get_s(row, "what"), get_s(row, "state"), get_s(row, "detail")));
        }
    }
    data.insert("design".into(), design.to_value());
    data.insert("freeze_sha256".into(), Value::Str(freeze_sha));
    data.insert("spec_sha256".into(), Value::Str(led.spec_sha(exp)));
    data.insert("spec_drift".into(), drift.map(Value::Str).unwrap_or(Value::Null));
    data.insert("state".into(), Value::Str(state));
    data.insert("caveats".into(), Value::List(caveats.iter().map(|c| Value::Dict(c.dump(false))).collect()));
    data.insert("findings".into(), Value::List(fvals));
    data.insert("run_ids".into(), Value::List(runs.iter().map(|r| Value::Str(r.id.clone())).collect()));
    data.insert("analysis_ids".into(), Value::List(analyses.iter().map(|a| Value::Str(a.id.clone())).collect()));
    data.insert("rerunnability".into(), Value::List(rerun.into_iter().map(Value::Dict).collect()));
    Ok(())
}
/// The rules, words and reporting example `rb context` opens with, after the title and who you are.
pub const CONTEXT_RULES: &[&str] = &[
    "",
    "## Rules",
    "",
    "- You and the user both record; rb checks sources and computes findings; every write records who made it and in which chat. Record questions (a tree: a top-level question for each inquiry, a sub-question under its parent with rb question add --parent), experiments (with their hypothesis), variants, settings with their sources, criteria, runs, analyses and resources.",
    "- Declare a criterion before the evidence it reads: its role (--role primary, secondary, exploratory or gate), what it compares (--subject, --reference, --metric, --estimand) and its rule. Evidence that already existed, or data already seen, is labelled so on the finding's timing, never hidden and never counted as prospective.",
    "- Link each criterion to the question its finding bears on: -q <question> on rb criterion add, optionally with --when-met \"<what meeting the rule would mean for the question>\"; rb criterion file <criterion> <question> --why \"...\" links one already recorded. A question's reading counts only the criteria linked to it.",
    "- Record every run with `rb run add`: the command you saw run (a recipe from a README or a paper goes in a note), --set for per-run values, --input for what it read, --produced-at when the output existed before you record it, --repeats when it re-checks a run. A population.unit setting names the independent unit (the photograph, the seed).",
    "- Record a computation over saved outputs with `rb analysis add` (--compare, --method, --unit, --units, and --source for the document that states the numbers). A reanalysis is an analysis, never a run; --supersedes replaces an earlier one with the reason.",
    "- Only `rb spec verify` (or `rb spec set` with a checkable source) makes a setting verified. Only rb computes a finding. Never edit `.rb/` by hand: rb catches it, and the edit has to be restored or adopted (rb doctor).",
    "- rb refuses nothing for who you are. Calls on results and on lines of work (accept, reject, investigate; continue, park, stop, pivot, ship) are the user's: record one when the user asks, with the reason as given, and pass their words verbatim with --words \"<what they wrote>\". It is proposed, not made, and changes nothing until the user confirms it in their own terminal (rb confirm <id>): tell them it waits. A call made before the record takes --past --when \"...\"; never record one as made now. Do not set or unset RB_ACTOR.",
    "- A gate is a priority policy: a missed gate parks a line (rb decide <experiment> park --revisit \"...\"); it never refutes it.",
    "- Pass on every integrity problem and every unknown below with any result you report.",
    "",
    "## Words",
    "",
    "- result: gain or loss (the interval clear of 0, read in the metric's direction), unresolved (the interval includes 0), equivalent (inside a declared margin), point only (no interval yet), measured (a level).",
    "- rule: criterion met, criterion not met, criterion undetermined (it needs an interval, more units, or the metric's direction), criterion untested; estimate meets criterion (recorded values meet criterion for an --each rule) when a reading with no interval, or one that crosses the threshold, meets it. A gate reads gate met or gate missed; a cited number, reproduced or not reproduced. In the JSON, rule_outcome is meets, misses, undetermined or untested.",
    "- timing: declared before the evidence (before its runs, when only its baseline's runs came first: timing is read on the subject's runs); after the evidence existed; after the data was seen; order unknown.",
    "- units: the independent units a reading rests on. A repeat checks a run and adds no unit; a reanalysis adds none.",
    "- integrity: problems with what a finding depends on (a stale source, an undeclared difference, a record edited outside rb). ok when there are none.",
    "- verified: the named file or run states this setting's value at that commit. It does not mean a run used it; `--config` on rb run add checks that. provisional: given, not checked.",
    "- planned: an experiment nothing in the record tests yet. open: evidence and no call. parked, stopped, pivoted, shipped: a direction call.",
    "",
    "## Report it like this",
    "",
    "`c1 (primary) change.val_loss: +0.020 [+0.015, +0.025] nats, 3 seeds (rb): a loss that meets its rule (≤ 0.05); declared before the evidence; integrity ok.` Quote the comparison with its interval and units, then the rule, the timing, and every integrity problem. There is no single word for a finding; never say supported or established.",
    "",
];

// ---------------------------------------------------------------- rb compare, rb log, rb context

fn ljust(s: &str, w: usize) -> String {
    let n = s.chars().count();
    if n >= w {
        s.to_string()
    } else {
        format!("{s}{}", " ".repeat(w - n))
    }
}

pub fn cmd_compare(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let r = compare_of(&led, ns, out);
    out.shows_for(&led);
    r
}

fn compare_of(led: &Ledger, ns: &Namespace, out: &mut Out) -> R {
    let exp_id = match s(ns, "experiment") {
        Some(e) => e,
        None => {
            let exps = led.live::<Experiment>()?;
            if exps.len() != 1 {
                let named = if exps.is_empty() { "no experiments yet".to_string() } else { exps.iter().map(|e| e.id.clone()).collect::<Vec<_>>().join(", ") };
                return Err(RBError::msg("E_USAGE", format!("rb compare <experiment>: {named}")));
            }
            exps[0].id.clone()
        }
    };
    let t = led.compare(&exp_id)?;
    let tv = Value::Dict(t.clone());
    let varies: Vec<String> = items(&tv, "varies").iter().map(pyjson::py_str).collect();
    let conditions: Vec<String> = items(&tv, "conditions").iter().map(pyjson::py_str).collect();
    let unit = tv.get("unit").map(pyjson::py_str).unwrap_or_else(|| "run".into());
    out.say(
        format!("{}  {}", gs(&tv, "experiment_id"), gs(&tv, "title"))
            + &if varies.is_empty() { String::new() } else { format!(" · varies {}", varies.join(", ")) }
            + &format!(" · {} {} under the current spec", gs(&tv, "run_count"), if gs(&tv, "run_count") == "1" { "run" } else { "runs" })
            + &if unit == "run" { String::new() } else { format!(" · means over {unit}s") },
    );
    let metrics = items(&tv, "metrics");
    let mut heads: Vec<String> = vec!["variant".into(), "role".into()];
    heads.extend(varies.iter().cloned());
    heads.extend(conditions.iter().cloned());
    for m in metrics {
        let u = gs(m, "unit");
        heads.push(gs(m, "name") + &if u.is_empty() { String::new() } else { format!(" ({u})") } + if truthy(m, "declared") { "" } else { "*" });
    }
    let mut rows: Vec<Vec<String>> = Vec::new();
    for r in items(&tv, "rows") {
        let mut cells = vec![gs(r, "variant"), gs(r, "role")];
        let rv = r.get("varies").cloned().unwrap_or(Value::empty_dict());
        for n in &varies {
            cells.push(short(&show(rv.get(n).unwrap_or(&Value::Null)), 16));
        }
        let rw = r.get("where").cloned().unwrap_or(Value::empty_dict());
        for n in &conditions {
            cells.push(short(&show(rw.get(n).unwrap_or(&Value::Null)), 16));
        }
        let rm = r.get("metrics").cloned().unwrap_or(Value::empty_dict());
        for m in metrics {
            let Some(c) = rm.get(&gs(m, "name")) else {
                cells.push("-".into());
                continue;
            };
            let mut txt = crate::pyfloat::fmt_num(c.get("mean").unwrap_or(&Value::Null), ".4g");
            if let Some(Value::List(readers)) = c.get("read_by").filter(|r| r.truthy()) {
                // a criterion reads this comparison otherwise than the unpaired means: named, its number below
                txt += &format!(" (read by {})", readers.iter().map(|r| gs(r, "criterion")).collect::<Vec<_>>().join(", "));
            } else if let Some(delta) = c.get("delta") {
                let mixed = truthy(c, "differs_in");
                let mark = match c.get("better") {
                    _ if mixed => "",
                    None | Some(Value::Null) => "",
                    Some(b) if b.truthy() => " better",
                    Some(_) => " worse",
                };
                // a change a primary criterion reads is said as its rule says it
                let said = c.get("said").map(pyjson::py_str).unwrap_or_else(|| crate::pyfloat::fmt_num(delta, "+.3g"));
                txt += &format!(" {said}{mark}");
                if truthy(c, "primary") {
                    txt += " [primary]";
                }
            }
            // what a change rests on, as the page says it: units the baseline does not share, or another factor too
            let unpaired = if c.get("paired").map_or(false, |p| !p.truthy()) && c.get("read_by").map_or(true, |r| !r.truthy()) { ", unpaired" } else { "" };
            let mixed = items(c, "differs_in").iter().map(pyjson::py_str).collect::<Vec<_>>();
            let mixed = match mixed.split_last() {
                Some((last, rest)) if !rest.is_empty() => format!("; differs from the baseline in {} and {last}", rest.join(", ")),
                _ => String::new(),
            };
            cells.push(format!("{txt} (n={}{unpaired}{mixed})", gs(c, "n")));
        }
        rows.push(cells);
    }
    let widths: Vec<usize> = (0..heads.len())
        .map(|i| std::iter::once(&heads[i]).chain(rows.iter().filter_map(|r| r.get(i))).map(|x| x.chars().count()).max().unwrap_or(0))
        .collect();
    // zip(heads, *rows) stops at the shortest; every row has a cell per head
    out.say("  ".to_string() + &heads.iter().zip(&widths).map(|(h, w)| ljust(h, *w)).collect::<Vec<_>>().join("  "));
    for cells in &rows {
        out.say("  ".to_string() + &cells.iter().zip(&widths).map(|(c, w)| ljust(c, *w)).collect::<Vec<_>>().join("  "));
    }
    let mut read_by: Vec<String> = Vec::new();
    for r in items(&tv, "rows") {
        for (_, c) in r.get("metrics").and_then(|m| m.as_dict()).into_iter().flatten() {
            for x in c.get("read_by").and_then(Value::as_list).into_iter().flatten() {
                let said = format!("{} reads {}", gs(x, "criterion"), gs(x, "reads"));
                if !read_by.contains(&said) {
                    read_by.push(said);
                }
            }
        }
    }
    if !read_by.is_empty() {
        out.say(format!("  read by a criterion, in the place of the unpaired means: {}", read_by.join("; ")));
    }
    if metrics.iter().any(|m| !truthy(m, "declared")) {
        out.say("  * not in the catalogue: rb metric add <name> --unit <unit> --minimize|--maximize says which way is better");
    }
    if let Some(Value::Dict(ov)) = tv.get("overall") {
        if !ov.is_empty() {
            out.say(
                "  whole-comparison numbers: ".to_string()
                    + &ov.iter().map(|(k, v)| format!("{k}={}", crate::pyfloat::fmt_num(v.get("mean").unwrap_or(&Value::Null), ".4g"))).collect::<Vec<_>>().join(", "),
            );
        }
    }
    let primary = items(&tv, "primary");
    if !primary.is_empty() {
        out.say(
            "  the primary criteria read: ".to_string()
                + &primary
                    .iter()
                    .map(|p| {
                        if truthy(p, "reads") && truthy(p, "reference") {
                            format!("{} {} vs {} ({} reads {})", gs(p, "metric"), gs(p, "subject"), gs(p, "reference"), gs(p, "criterion"), gs(p, "reads"))
                        } else if truthy(p, "reads") {
                            let subj = gs(p, "subject");
                            format!("{}{} ({} reads {})", if subj.is_empty() { String::new() } else { format!("{subj}.") }, gs(p, "metric"), gs(p, "criterion"), gs(p, "reads"))
                        } else if truthy(p, "reference") {
                            format!("{} {} vs {} ({})", gs(p, "metric"), gs(p, "subject"), gs(p, "reference"), gs(p, "criterion"))
                        } else {
                            let subj = gs(p, "subject");
                            format!("{}{} ({})", if subj.is_empty() { String::new() } else { format!("{subj}.") }, gs(p, "metric"), gs(p, "criterion"))
                        }
                    })
                    .collect::<Vec<_>>()
                    .join(", ")
                + "; deltas are means, the findings carry the intervals (rb show <criterion>)",
        );
    }
    for (k, v) in t {
        out.data.insert(k, v);
    }
    Ok(0)
}

/// rows[-last:] if last else rows
pub(crate) fn py_last(rows: Vec<Dict>, last: i64) -> Vec<Dict> {
    if last == 0 {
        return rows;
    }
    let n = rows.len() as i64;
    let start = if last > 0 { (n - last).max(0) } else { (-last).min(n) };
    rows.into_iter().skip(start as usize).collect()
}

/// A relation's note after its line: ": <note>", with "· not confirmed" when a session wrote it, as any note.
fn relation_note(note: &str, by: &str) -> String {
    if note.is_empty() { String::new() } else { format!(": {}{}", note, view::not_confirmed(by)) }
}

/// A write's reason as a log line says it: a session's as its note, not confirmed, the user's as given.
fn session_reason(why: &str, actor: &str) -> String {
    if crate::view::words_are_theirs(actor) || actor.is_empty() { why.to_string() } else { format!("note: {why} · not confirmed") }
}

fn first_truthy(d: &Value, keys: &[&str]) -> String {
    keys.iter().find_map(|k| d.get(k).filter(|v| v.truthy()).map(pyjson::py_str)).unwrap_or_default()
}

fn opt_str(v: &Value, k: &str, missing: &str) -> String {
    v.get(k).map(pyjson::py_str).unwrap_or_else(|| missing.to_string())
}

pub fn cmd_log(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let r = log_of(&led, ns, out);
    out.shows_for(&led);
    r
}

fn log_of(led: &Ledger, ns: &Namespace, out: &mut Out) -> R {
    let n = arg(ns, "n").as_i64().unwrap_or(20);
    let about = s(ns, "id").filter(|x| !x.is_empty());
    let setting = about.as_deref().and_then(|a| crate::ledger::parse_address(a).1);
    let lines = led.log_about(None, about.as_deref())?;
    // a session's line records no write: --json gives the last n lines, the text the last n writes
    let writes = py_last(lines.iter().filter(|r| !crate::ledger::is_session_line(r)).cloned().collect(), n);
    let rows = py_last(lines, n);
    for r in &writes {
        let rv = Value::Dict(r.clone());
        // who, how rb knew and through what are in --json; the text says what changed
        out.say(format!(
            "{}{} {}",
            cut(&opt_str(&rv, "at", ""), 16).replace('T', " "),
            shown_writer(&gs(&rv, "actor")).map(|name| format!(" {name}")).unwrap_or_default(),
            log_said(r, setting.as_deref())
        ));
    }
    if writes.is_empty() {
        out.say("Nothing logged.");
    }
    out.data.insert("entries".into(), Value::List(rows.into_iter().map(Value::Dict).collect()));
    Ok(0)
}

/// One write as rb log's text says it after its time, with no writer: what it did and what it wrote to, then what
/// changed (`setting`: what it did to that one setting of an experiment). The shell's History log rows say the same.
pub(crate) fn log_said(r: &Dict, setting: Option<&str>) -> String {
    log_said_as(r, setting, &|_| None)
}

/// log_said's line with each object rb minted an id for said by what it is, as a page names it (`named`: an id's words,
/// with its kind, such as "run int8 · seed 1"; None keeps the id): what the line wrote to, and each such id in what it
/// says after. A note taken back is "a note on e1"; rb log's own text keeps every id.
pub(crate) fn log_said_as(r: &Dict, setting: Option<&str>, named: &dyn Fn(&str) -> Option<String>) -> String {
    let rv = Value::Dict(r.clone());
    let d = rv.get("detail").filter(|x| x.truthy()).cloned().unwrap_or(Value::empty_dict());
    let mut what = first_truthy(&d, &["change", "outcome", "why", "rule", "from"]);
    // rb's own words name what rb minted an id for (`named`); a reason someone gave keeps its words, but for rb's
    // "replaced by <run>" before a replaced run's reason
    let rbs = !first_truthy(&d, &["change", "outcome"]).is_empty();
    if rbs {
        what = name_ids(&what, named);
    } else if let Some(by) = d.get("replaced_by").and_then(Value::as_str).and_then(|by| Some(by).zip(named(by))) {
        if let Some(rest) = what.strip_prefix(&format!("replaced by {}", by.0)) {
            what = format!("replaced by {}{rest}", by.1);
        }
    }
    // a session's reason is its note, never rb's words
    if !rbs && !what.is_empty() && what_is_why(&d, &what, named) {
        what = session_reason(&what, &gs(&rv, "actor"));
    }
    let verify = rv.get("op").and_then(|x| x.as_str()) == Some("spec_verify");
    if verify && truthy(&d, "results") {
        let res = items(&d, "results");
        let ok = res.iter().filter(|x| truthy(x, "ok")).count();
        what = format!("{} as stated", crate::assess::k_of_n(ok, res.len(), "setting checked", "settings checked"));
    } else if verify && !first_truthy(&d, &["outcome"]).is_empty() {
        // one setting's check, said as what rb found in its source
        what = format!("{}: {}", first_truthy(&d, &["setting"]), check_said(&first_truthy(&d, &["outcome"])));
    }
    // the log of one setting says what each line did to it
    if let Some(c) = setting.and_then(|name| crate::ledger::checked(r, name)) {
        let c = Value::Dict(c);
        what = first_truthy(&c, &["change", "outcome", "after", "why"]);
        if verify && first_truthy(&c, &["change"]).is_empty() {
            what = check_said(&what);
        }
    }
    line_said_as(&rv, named) + &if what.is_empty() { String::new() } else { format!(": {what}") }
}

/// Whether `what` is the line's reason (its why), as given or with rb's "replaced by" named.
fn what_is_why(d: &Value, what: &str, named: &dyn Fn(&str) -> Option<String>) -> bool {
    let why = first_truthy(d, &["why"]);
    if what == why {
        return true;
    }
    let Some(by) = d.get("replaced_by").and_then(Value::as_str) else { return false };
    named(by).is_some_and(|w| why.strip_prefix(&format!("replaced by {by}")).is_some_and(|rest| what == format!("replaced by {w}{rest}")))
}

/// `text` with each id `named` gives words for in their place, in rb's own words only: an id is a whole word of
/// letters, digits, '_' and '-'; one inside a path, a file name or an address ('/', '.', '@', '#', '=' join a word),
/// inside quotation marks, or in a command (from "rb " on) keeps its id.
pub(crate) fn name_ids(text: &str, named: &dyn Fn(&str) -> Option<String>) -> String {
    let (prose, command) = match text.match_indices("rb ").find(|(i, _)| *i == 0 || text[..*i].ends_with([' ', '(', ':', '`'])) {
        Some((i, _)) => text.split_at(i),
        None => (text, ""),
    };
    let chars: Vec<char> = prose.chars().collect();
    let mut out = String::new();
    let mut word = String::new();
    let mut quoted: Option<char> = None;
    let joins = |c: char| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '/' | '.' | '@' | '#' | '=');
    let flush = |word: &mut String, out: &mut String, quoted: bool| {
        // a sentence's full stop is not part of its last word
        let stem = word.trim_end_matches('.');
        match named(stem).filter(|_| !quoted) {
            // "run runj53tal" reads "run int8 · seed 1", never "run run int8 · seed 1"; and "the analysis a6pqyge" reads
            // "an analysis" where it has no title, never "the analysis an analysis" (FIX9)
            Some(words) => {
                let mut said = &words[..];
                let mut parts = words.splitn(3, ' ');
                let first = parts.next().unwrap_or_default();
                let said_with_article = matches!(first, "a" | "an");
                let kind = if said_with_article { parts.next().unwrap_or_default() } else { first };
                if !kind.is_empty() && ends_with_word(&out, &format!("{kind} ")) {
                    if said_with_article {
                        out.truncate(out.len() - kind.len() - 1);
                        if ends_with_word(&out, "the ") {
                            out.truncate(out.len() - 4);
                        }
                    } else {
                        said = &words[kind.len() + 1..];
                    }
                }
                out.push_str(said);
                out.push_str(&word[stem.len()..]);
            }
            None => out.push_str(word),
        }
        word.clear();
    };
    for (i, &ch) in chars.iter().enumerate() {
        if joins(ch) {
            word.push(ch);
            continue;
        }
        flush(&mut word, &mut out, quoted.is_some());
        let (before, after) = (i.checked_sub(1).map(|j| chars[j]), chars.get(i + 1).copied());
        quoted = match (quoted, ch) {
            (None, '"') => Some('"'),
            (Some('"'), '"') => None,
            (None, '\u{201c}') => Some('\u{201d}'),
            (Some('\u{201d}'), '\u{201d}') => None,
            // a single quotation mark opens after a space or a mark and closes before one; an apostrophe (unit's) does neither
            (None, '\'') if !before.is_some_and(|b| b.is_alphanumeric()) && after.is_some_and(|a| !a.is_whitespace()) => Some('\''),
            (Some('\''), '\'') if !after.is_some_and(|a| a.is_alphanumeric()) => None,
            (q, _) => q,
        };
        out.push(ch);
    }
    flush(&mut word, &mut out, quoted.is_some());
    out + command
}

/// Whether `out` ends with `w` as whole words: what comes before it, if anything, is no letter or digit.
fn ends_with_word(out: &str, w: &str) -> bool {
    out.strip_suffix(w).is_some_and(|head| !head.ends_with(|c: char| c.is_alphanumeric()))
}

/// What a check of a setting's source found, in rb's words for what it computed: the source states the value
/// ("checked as stated"), states another, or no longer states it; any other status as rb names it.
fn check_said(outcome: &str) -> String {
    match outcome {
        "verified" => "checked as stated".into(),
        "conflict" => "its source states another value".into(),
        "stale" => "its source no longer states it".into(),
        other => other.to_string(),
    }
}

/// What a log line did, as its text says it: its op and what it wrote to, and a confirmation of a design as that, with
/// its gate only where it has one.
fn line_said(rv: &Value) -> String {
    line_said_as(rv, &|_| None)
}

fn line_said_as(rv: &Value, named: &dyn Fn(&str) -> Option<String>) -> String {
    let part = |k: &str| rv.get("detail").and_then(|d| d.get_str(k)).filter(|x| !x.is_empty()).map(|x| x.to_string());
    if rv.get("op").and_then(|x| x.as_str()) == Some("gate") && part("design").is_some() {
        let gate = if part("if_holds").is_some() || part("if_not").is_some() { ", with its gate" } else { "" };
        return format!("design {} {} confirmed{gate}", opt_str(rv, "kind", "None"), opt_str(rv, "id", "None"));
    }
    format!("{} {}", opt_str(rv, "op", "None"), line_target(rv, named))
}

/// What a log line wrote to, as its text names it: a retraction of a note, a variant or a setting names that part,
/// never only the experiment or the project that holds it.
fn line_target(rv: &Value, named: &dyn Fn(&str) -> Option<String>) -> String {
    let (kind, id) = (opt_str(rv, "kind", "None"), opt_str(rv, "id", "None"));
    let part = |k: &str| rv.get("detail").and_then(|d| d.get_str(k)).filter(|x| !x.is_empty()).map(|x| x.to_string());
    let target = named(&id).unwrap_or_else(|| format!("{kind} {id}"));
    if rv.get("op").and_then(|x| x.as_str()) == Some("gate") && part("confirms").as_deref() == Some("gate") {
        return format!("{target}, confirmed");
    }
    if rv.get("op").and_then(|x| x.as_str()) == Some("retract") {
        if let Some(n) = part("note") {
            if named(&n).is_some() {
                return if kind == "project" { "a note on the project".to_string() } else { format!("a note on {id}") };
            }
            return format!("note {n} on {kind} {id}");
        }
        if let Some((p, name)) = ["variant", "setting"].into_iter().find_map(|p| part(p).map(|n| (p, n))) {
            return format!("{p} {id}/{name}");
        }
    }
    target
}

fn collapse_log(rows: &[Dict]) -> Vec<String> {
    collapse_log_rows(rows).into_iter().map(|(line, _)| line).collect()
}

/// collapse_log's lines, each with the rows it says.
fn collapse_log_rows(rows: &[Dict]) -> Vec<(String, Vec<usize>)> {
    let mut out: Vec<(String, Vec<usize>)> = Vec::new();
    let mut prev: Option<[String; 2]> = None;
    let mut count = 0;
    for (i, r) in rows.iter().enumerate() {
        let rv = Value::Dict(r.clone());
        // writes in a row that read the same are one line
        let who = shown_writer(&gs(&rv, "actor")).map(|name| format!(" {name}")).unwrap_or_default();
        let key = [who.clone(), ["op", "kind", "id"].map(|k| rv.get(k).map(pyjson::py_repr).unwrap_or_else(|| "None".into())).join(" ")];
        let head = format!("- {}{who} {}", cut(&opt_str(&rv, "at", ""), 16).replace('T', " "), line_said(&rv));
        if prev.as_ref() == Some(&key) {
            count += 1;
            if let Some(last) = out.last_mut() {
                last.0 = format!("{head} (x{count})");
                last.1.push(i);
            }
            continue;
        }
        prev = Some(key);
        count = 1;
        let d = rv.get("detail").filter(|x| x.truthy()).cloned().unwrap_or(Value::empty_dict());
        let mut what = first_truthy(&d, &["change", "outcome", "why"]);
        if first_truthy(&d, &["change", "outcome"]).is_empty() && !what.is_empty() {
            what = session_reason(&what, &gs(&rv, "actor"));
        }
        out.push((head + &if what.is_empty() { String::new() } else { format!(": {what}") }, vec![i]));
    }
    out
}

/// The log lines the briefing's Recent activity prints, and so tells a session, by digest.
fn recent_lines(led: &Ledger) -> Res<Vec<String>> {
    let recent = py_last(led.log_about(None, None)?.into_iter().filter(|r| !crate::ledger::is_session_line(r)).collect(), 40);
    let lines = collapse_log_rows(&recent);
    let from = lines.len().saturating_sub(12);
    Ok(lines[from..].iter().flat_map(|(_, rows)| rows.iter().map(|i| crate::ledger::line_digest(&recent[*i]))).collect())
}

/// What a briefing opens with on a record that holds no question and no experiment, in a project with work already
/// done (commits, results where rb looks, a database): record where the work stands now, from the files, without asking,
/// then the start protocol. rb status's no_question item carries what rb found of that work.
fn start_now(led: &Ledger, stv: &Value) -> Res<Option<String>> {
    if stv.get("experiment_id").map_or(false, |x| x.truthy()) || !items(stv, "questions").is_empty() || !led.live::<Experiment>()?.is_empty() {
        return Ok(None);
    }
    let Some(item) = items(stv, "open").iter().find(|i| gs(i, "code") == "no_question") else { return Ok(None) };
    let strs = |k: &str| item.get(k).and_then(|v| v.as_list()).map(|l| l.iter().map(pyjson::py_str).collect()).unwrap_or_default();
    let h = crate::repo::History { commits: item.get("commits").and_then(|v| v.as_i64()).unwrap_or(0) as usize, folders: strs("folders"), stores: strs("stores") };
    Ok(Some(format!(
        "Nothing is recorded yet, and this project holds work already done: {}. Record where the work stands now, in this turn, from the files, and ask the researcher nothing first. How: rb docs --start.",
        h.said()
    )))
}

/// The briefing: what a fresh agent session (or a colleague) reads first instead of anyone's summary.
pub fn context_markdown(led: &Ledger, st: &Dict) -> Res<String> {
    let found = crate::journal::found_all(led, crate::journal::Seen::Briefing).ok();
    context_markdown_with(led, st, found.as_ref(), &crate::journal::Scope::of(led), false)
}

/// Each write the log holds to an experiment, as (the experiment, when): a write to a file of its own (a run, a
/// criterion, a note, a variant, its spec or freeze), and a call or a confirmation on it, on one of its criteria or on
/// one of its settings.
fn latest_writes(led: &Ledger) -> Res<Vec<(String, String)>> {
    use crate::value::DictExt;
    let crits: std::collections::HashMap<String, String> = led.all::<Criterion>()?.iter().map(|c| (c.id.clone(), c.experiment.clone())).collect();
    let subjects: std::collections::HashMap<String, String> = led.all::<crate::record::Decision>()?.iter().map(|d| (d.id.clone(), d.subject.clone())).collect();
    let of_subject = |s: &str| -> String {
        let s = s.split('/').next().unwrap_or(s);
        crits.get(s).cloned().unwrap_or_else(|| s.to_string())
    };
    let folder = format!("{}/experiments/", crate::ledger::DIR);
    let mut out = Vec::new();
    for e in led.log_entries()?.iter() {
        let Some(at) = e.get_str("at") else { continue };
        // the files the line wrote, its own and its parts' (a note is a part of its experiment's line)
        let mut paths: Vec<&str> = e.get_str("path").into_iter().collect();
        if let Some(Value::List(parts)) = e.get("parts") {
            paths.extend(parts.iter().filter_map(|p| p.get_str("path")));
        }
        let mut exps: Vec<String> = paths.iter().filter_map(|p| p.strip_prefix(folder.as_str())).filter_map(|rest| rest.split('/').next()).map(str::to_string).collect();
        match (e.get_str("kind"), e.get_str("id")) {
            (Some("experiment"), Some(id)) => exps.push(id.to_string()),
            (Some("decision"), Some(id)) => exps.extend(subjects.get(id).map(|s| of_subject(s))),
            (Some("criterion"), Some(id)) => exps.extend(crits.get(id).cloned()),
            _ => {}
        }
        exps.sort();
        exps.dedup();
        out.extend(exps.into_iter().map(|x| (x, at.to_string())));
    }
    Ok(out)
}

/// The briefing, with what rb found and where it looks worked out once by the caller. `drawn`: what waits for a
/// confirmation as rb context's text draws it; the briefing's data (--json, MCP, the SDK) keeps the text as written.
pub fn context_markdown_with(led: &Ledger, st: &Dict, found: Option<&crate::journal::FoundAll>, scope: &crate::journal::Scope, drawn: bool) -> Res<String> {
    let rerun = crate::reproduce::rerun(led)?;
    briefing(led, st, found, scope, drawn, rerun.as_ref())
}

/// A clone's rerun steps for Next, after what is there: the code where it is not here, then each experiment's first
/// command (only the named experiment's, for rb status <exp>), at most two of them, none said twice.
fn rerun_steps(v: &Value, experiment: Option<&str>, next: &mut Vec<String>) {
    let mut v = v.clone();
    if let (Some(e), Value::Dict(d)) = (experiment, &mut v) {
        if let Some(Value::List(l)) = d.get_mut("experiments") {
            l.retain(|x| x.get_str("id") == Some(e));
        }
    }
    let mut added = 0;
    for step in crate::reproduce::rerun_next(&v) {
        if added < 2 && !next.contains(&step) {
            next.push(step);
            added += 1;
        }
    }
}

/// The briefing, with a clone's rerun (None for a record that is no clone) worked out once by the caller.
fn briefing(led: &Ledger, st: &Dict, found: Option<&crate::journal::FoundAll>, scope: &crate::journal::Scope, drawn: bool, rerun: Option<&Value>) -> Res<String> {
    let stv = Value::Dict(st.clone());
    let proj = stv.get("project").cloned().unwrap_or(Value::empty_dict());
    let mut l: Vec<String> = vec![format!("# The record: {}", gs(&proj, "title"))];
    // nothing recorded yet in a project with work already done: what to do now comes before anything else
    if let Some(said) = start_now(led, &stv)? {
        l.extend([String::new(), "## First, record where the work stands".to_string(), String::new(), said]);
    }
    if let Some(start) = led.record_start()? {
        l.push(String::new());
        l.push(start.words());
    }
    if let Some(line) = crate::elsewhere::nested_line(led) {
        l.push(String::new());
        l.push(line);
    }
    l.extend([
        String::new(),
        "Generated by `rb context` from `.rb/`; read the state from here or `rb status --json`, never from memory or a summary.".to_string(),
    ]);
    if let Some((_, _, said)) = crate::delta::model_hint(led)? {
        l.push(String::new());
        l.push(said);
    }
    // a clone: what it takes to run the parent again, before anything else is done here
    if let Some(rerun) = rerun {
        l.extend([String::new(), "## Rerun the parent".to_string(), String::new()]);
        l.extend(crate::reproduce::rerun_lines(rerun));
    }
    l.extend(CONTEXT_RULES.iter().map(|x| x.to_string()));
    for q in items(&stv, "questions") {
        l.push(format!("## Question {}: {} ({})", gs(q, "id"), gs(q, "text"), gs(q, "state")));
        l.push(String::new());
        if let Some(inquiry) = q.get("inquiry") {
            let summary = view::summary_lines(inquiry);
            let (k, more) = first_n(&summary, SHOWN_READINGS, "- ", "lines of its reading", &format!("rb show {} reads it whole", gs(q, "id")));
            l.extend(summary[..k].iter().map(|line| format!("- {line}")));
            l.extend(more);
        }
        l.push(String::new());
    }
    l.push("## Criteria".into());
    l.push(String::new());
    let findings = items(&stv, "findings");
    if findings.is_empty() {
        l.push("- None yet.".into());
    } else {
        let (k, more) = first_n(findings, SHOWN_ROWS, "- ", "criteria", "rb show <id> reads one, and rb context --json lists every one");
        for block in finding_blocks(&findings[..k], None) {
            l.push(block.join("\n"));
        }
        l.extend(more);
    }
    l.push(String::new());
    let open = items(&stv, "open");
    let human: Vec<&Value> = open.iter().filter(|i| gs(i, "who") == "human" && !pending_item(i)).collect();
    let (agent, quiet): (Vec<&Value>, Vec<&Value>) = open.iter().filter(|i| gs(i, "who") != "human").partition(|i| !gs(i, "do").is_empty());
    for (title, its) in [("## Needs a human", &human), ("## Can be done now", &agent), ("## Nothing to do", &quiet)] {
        if title == "## Nothing to do" && its.is_empty() {
            continue;
        }
        l.push(title.into());
        l.push(String::new());
        let (k, more) = first_n(its, if title == "## Needs a human" { SHOWN } else { its.len() }, "- ", "items", "rb status --json lists every one");
        let text = items_text(&its[..k]);
        if text.is_empty() {
            l.push("- Nothing.".into());
        } else {
            l.extend(text);
            l.extend(more);
        }
        l.push(String::new());
        if title == "## Needs a human" {
            // what waits for the user's confirmation, in its own place: it changes nothing until then
            let (sections, designs, all) = pending_sections(&stv, None, drawn);
            for (title, lines) in sections {
                l.push(format!("## {title}"));
                l.push(String::new());
                let (k, more) = first_n(&lines, SHOWN, "- ", "waiting", "rb confirm lists every one");
                l.extend(lines[..k].iter().map(|x| format!("- {x}")));
                l.extend(more);
                l.push(String::new());
            }
            if let Some(designs) = designs {
                l.push(designs);
                l.push(String::new());
            }
            if let Some(all) = all {
                l.push(all);
                l.push(String::new());
            }
        }
    }
    let only = stv.get("experiment_id").filter(|x| x.truthy()).map(pyjson::py_str);
    let note_said = led.note_words()?;
    let gate_said = led.gate_words()?;
    let live_runs = led.live::<Run>()?;
    let live_analyses = led.live::<Analysis>()?;
    let mut exps: Vec<Experiment> = led.live::<Experiment>()?.into_iter().filter(|e| only.as_ref().map_or(true, |o| *o == e.id)).collect();
    // of many experiments, the ones most recently written to, in the record's order; the rest are counted by id
    let mut left: Vec<String> = Vec::new();
    if exps.len() > SHOWN_ROWS {
        let mut last: IndexMap<String, String> = exps.iter().map(|e| (e.id.clone(), e.created_at.clone())).collect();
        for (exp, at) in live_runs.iter().map(|r| (&r.experiment, &r.created_at)).chain(live_analyses.iter().map(|a| (&a.experiment, &a.created_at))) {
            if let Some(t) = last.get_mut(exp) {
                if at > t {
                    *t = at.clone();
                }
            }
        }
        // any write to an experiment or its parts (a note, a criterion, the spec, a freeze, a variant), and a call on it
        // or on one of its criteria, is a write to it
        for (exp, at) in latest_writes(led)? {
            if let Some(t) = last.get_mut(&exp) {
                if at > *t {
                    *t = at;
                }
            }
        }
        let mut recent: Vec<(&String, &String)> = last.iter().collect();
        recent.sort_by(|a, b| b.1.cmp(a.1).then_with(|| a.0.cmp(b.0)));
        let kept: std::collections::HashSet<String> = recent.iter().take(SHOWN_ROWS).map(|(id, _)| (*id).clone()).collect();
        left = exps.iter().filter(|e| !kept.contains(&e.id)).map(|e| e.id.clone()).collect();
        exps.retain(|e| kept.contains(&e.id));
    }
    for e in exps {
        let arms = e.live_variants().iter().map(|v| format!("{} ({})", v.name, v.role)).collect::<Vec<_>>().join(" · ");
        l.push(format!("## Experiment {}: {} ({})", e.id, e.title, led.experiment_state(&e, None)?));
        l.push(String::new());
        let questions = e.questions.join(", ");
        for (label, text) in [("questions", &questions), ("purpose", &e.purpose), ("hypothesis", &e.hypothesis)] {
            if !text.is_empty() {
                l.push(format!("- {label}: {text}"));
            }
        }
        // the briefing says no design's standing of its own: one line above counts the designs that wait
        let design = led.design(&e.id)?.unwrap_or_default();
        if let Some((label, text)) = view::why_part(&e.why, &led.why_by(&e, &design)) {
            l.push(format!("- {label}: {text}"));
        }
        let (gate_words, gate_by) = gate_said.get(&e.id).cloned().unwrap_or_default();
        let command = view::gate_command(&e.id, &e.if_holds, &e.if_not, &gate_words, &design.waiting_command());
        for (label, text) in view::gate_parts(&e.if_holds, &e.if_not, &gate_words, &design.writer(&gate_by, "gate"), &command, design.gate_first()) {
            l.push(format!("- {label}: {text}"));
        }
        l.push(
            format!("- {}", if arms.is_empty() { "no variants".to_string() } else { arms.clone() })
                + &if e.varies.is_empty() { String::new() } else { format!(" · varies {}", e.varies.join(", ")) }
                + &match &e.frozen {
                    Some(fz) => format!(" · frozen {}{}", cut(&fz.at, 10), by_writer(&fz.by)) + &view::reason_part(&fz.why, &design.writer(&fz.by, "freeze_why"), ": "),
                    None => " · not frozen".to_string(),
                },
        );
        let notes: Vec<_> = e.notes.iter().filter(|n| n.retracted.is_none()).collect();
        if notes.len() > SHOWN {
            l.push(format!("- {} earlier notes are not listed here: rb show {} lists every one", notes.len() - SHOWN, e.id));
        }
        for n in &notes[notes.len().saturating_sub(SHOWN)..] {
            let w = n.id.as_ref().and_then(|id| note_said.get(id)).cloned().unwrap_or_default();
            l.push(format!("- {}{}: {}", view::note_label(n.id.as_deref()), shown_writer(&n.by).map(|name| format!(" ({name})")).unwrap_or_default(), n.text) + &words(&w, &n.by) + view::not_confirmed(&n.by));
        }
        for r in &e.relations {
            l.push(format!("- {} {}", r.kind.replace('_', " "), r.target) + &relation_note(&r.note, &r.by));
        }
        if !e.folder.is_empty() {
            let known = stv.get("repository").filter(|r| r.truthy()).and_then(|r| r.get("experiments")).and_then(|x| x.get(&e.id)).filter(|x| x.truthy()).cloned();
            let read = match known {
                Some(Value::Dict(d)) => d,
                _ => crate::repo::read_folder(led, &e, None)?,
            };
            l.push(format!("- in the repository: {} (what its files say is quoted verbatim; the files are the source, not this)", e.folder));
            for q in &e.quotes {
                if ["summary", "result", "limitation", "next"].contains(&q.kind.as_str()) {
                    l.push(format!("  - {}: \"{}\" ({}:{})", q.kind, q.text, q.path, q.lines));
                }
            }
            l.extend(crate::repo::facts(&read).into_iter().map(|line| format!("  - {line}")));
        }
        let settings = e.all_settings();
        if !settings.is_empty() {
            l.push(String::new());
            l.push("| setting | value | status | checked in |".into());
            l.push("|---|---|---|---|".into());
            let mut caveats: IndexMap<String, String> = IndexMap::new();
            for c in led.setting_caveats(&e, led.has_origin(&e)?, None)? {
                if ["stale", "conflict", "confound"].contains(&c.code.as_str()) {
                    caveats.insert(c.subject.split_once('/').map(|(_, t)| t.to_string()).unwrap_or_default(), c.code.clone());
                }
            }
            let (k, more) = first_n(&settings, SHOWN_SETTINGS, "- ", "settings", &format!("rb show {} lists every one", e.id));
            for (full, s) in &settings[..k] {
                let status = led.status_read(&e, full, s)?;
                let place = match &s.source {
                    Some(src) if status == "verified" => src.label(),
                    Some(src) => format!("(given: {})", src.label()),
                    None => String::new(),
                };
                let flag = caveats.get(full).map(|c| format!(" · {}", c.to_uppercase())).unwrap_or_default();
                l.push(format!(
                    "| {full} | {} | {}{flag} | {place} |",
                    if s.per_run { "(each run)".to_string() } else { show(&s.value) },
                    if s.per_run { "per-run".to_string() } else { status }
                ));
            }
            if let Some(more) = more {
                l.push(String::new());
                l.push(more);
            }
        }
        for (i, am) in e.amendments.iter().enumerate() {
            let because = match view::why_part(&am.why, &design.writer(&am.by, &format!("amendment_why:{}", i + 1))) {
                Some((label, text)) if label != "why" => format!(" · {label}: {text}"),
                _ => format!(", because \"{}\"", am.why),
            };
            l.push(format!("- amended {}{}: {}{because}", cut(&am.at, 10), by_writer(&am.by), am.change));
        }
        // of many runs, the latest, in the record's order; rb show <experiment> lists every one
        let mut runs: Vec<&Run> = live_runs.iter().filter(|r| r.experiment == e.id).collect();
        if runs.len() > SHOWN {
            let mut by_time: Vec<&Run> = runs.clone();
            by_time.sort_by(|a, b| b.created_at.cmp(&a.created_at).then_with(|| a.id.cmp(&b.id)));
            let latest: std::collections::HashSet<&str> = by_time.iter().take(SHOWN).map(|r| r.id.as_str()).collect();
            l.push(format!("- {} earlier runs are not listed here: rb show {} lists every one", runs.len() - SHOWN, e.id));
            runs.retain(|r| latest.contains(r.id.as_str()));
        }
        for run in runs {
            let ran = match &run.receipt.config.as_ref().map(|c| c.given()) {
                None => "config unchecked",
                Some(c) if c.matches.is_empty() && c.mismatches.is_empty() => "config unchecked",
                Some(c) if c.mismatches.is_empty() => "ran with the spec",
                Some(_) => "ran with a different spec",
            };
            let mut metrics: Vec<(&String, &f64)> = run.metrics.iter().collect();
            metrics.sort_by(|a, b| a.0.cmp(b.0));
            let shown = metrics.iter().take(12).map(|(k, x)| format!("{k}={}", g(**x))).collect::<Vec<_>>().join(", ");
            let commit = run.receipt.produced.get("commit").filter(|c| c.truthy()).map(pyjson::py_str);
            l.push(
                format!("- {} ({}{}): {shown}", run.id, basis_said(&run), if run.synthetic { ", synthetic" } else { "" })
                    + &if run.metrics.len() > 12 { format!(" and {} more (rb show {})", run.metrics.len() - 12, run.id) } else { String::new() }
                    + &if run.per_run.is_empty() {
                        String::new()
                    } else {
                        format!(" · {}", run.per_run.iter().map(|(k, x)| format!("{k}={}", show(x))).collect::<Vec<_>>().join(", "))
                    }
                    + &format!(" · {ran}")
                    + &crate::ledger::support::run_command(&run).map(|g| format!(" · {}", g.line())).unwrap_or_default()
                    + &commit.map(|c| format!(" · produced at {}", cut(&c, 12))).unwrap_or_default()
                    + &crate::ledger::support::file_words(&run).map(|w| format!(" · {w}")).unwrap_or_default()
                    + &if run.note.is_empty() { String::new() } else { format!(" · note: {}{}", run.note, view::not_confirmed(&run.created_by)) }
                    + &run.repeats.as_ref().filter(|r| !r.is_empty()).map(|r| format!(" · repeats {r}")).unwrap_or_default(),
            );
        }
        let mut analyses: Vec<&Analysis> = live_analyses.iter().filter(|a| a.experiment == e.id).collect();
        if analyses.len() > SHOWN {
            let mut by_time: Vec<&Analysis> = analyses.clone();
            by_time.sort_by(|a, b| b.created_at.cmp(&a.created_at).then_with(|| a.id.cmp(&b.id)));
            let latest: std::collections::HashSet<&str> = by_time.iter().take(SHOWN).map(|a| a.id.as_str()).collect();
            l.push(format!("- {} earlier analyses are not listed here: rb show {} lists every one", analyses.len() - SHOWN, e.id));
            analyses.retain(|a| latest.contains(a.id.as_str()));
        }
        for an in analyses {
            let mut comps: Vec<String> = Vec::new();
            for c in &an.comparisons {
                let [est, lo, hi] = led.comparison_said(&e.id, c)?;
                let iv = match (c.lo, c.hi) {
                    (Some(_), Some(_)) => format!(" [{lo}, {hi}]"),
                    _ => String::new(),
                };
                comps.push(format!(
                    "{} {}{}{} {}{iv}",
                    c.metric,
                    c.subject,
                    if c.reference.is_empty() { String::new() } else { format!(" vs {}", c.reference) },
                    if c.estimand.is_empty() { String::new() } else { format!(" ({})", c.estimand) },
                    est
                ));
            }
            l.push(
                format!("- analysis {} ({}{}): {}", an.id, an.basis, if an.method.is_empty() { String::new() } else { format!(", {}", an.method) }, crate::assess::first_few(&comps, 6, "; "))
                    + &if an.supersedes.is_empty() { String::new() } else { format!(" · supersedes {}", an.supersedes.join(", ")) + &view::reason_part(&an.why, &an.created_by, ": ") },
            );
        }
        l.push(String::new());
    }
    if !left.is_empty() {
        l.push(format!(
            "{} more experiments, less recently written to, are not shown here: {} (rb show <id> reads one, and rb context --json lists every one)",
            left.len(),
            crate::assess::first_few(&left, SHOWN, ", ")
        ));
        l.push(String::new());
    }
    let questions = items(&stv, "questions");
    if !questions.is_empty() {
        l.push("## Questions".into());
        l.push(String::new());
        let (k, more) = first_n(questions, SHOWN_ROWS, "- ", "questions", "rb show <id> reads one, and rb context --json lists every one");
        for q in &questions[..k] {
            l.push(
                format!("- {} ({})", gs(q, "id"), gs(q, "state"))
                    + &if truthy(q, "parent") { format!(" under {}", gs(q, "parent")) } else { String::new() }
                    + &format!(": {}", gs(q, "text")),
            );
        }
        l.extend(more);
        l.push(String::new());
    }
    let rp = stv.get("repository").filter(|r| r.truthy()).cloned().unwrap_or(Value::empty_dict());
    {
        l.push("## The repository".into());
        l.push(String::new());
        if let Some(last) = rp.get("last_reconcile").filter(|x| x.truthy()) {
            l.push(format!(
                "- last rb reconcile --apply, {}{}: {}",
                cut(&opt_str(last, "at", ""), 16).replace('T', " "),
                by_writer(&opt_str(last, "by", "")),
                opt_str(last, "line", "")
            ));
        }
        // the folders no experiment reads, as status's item counts them: --json lists every one
        for it in open.iter().filter(|i| gs(i, "code") == "unindexed_folder") {
            l.push(format!("- {} · {}", gs(it, "what"), gs(it, "do")));
        }
        let checks = items(&rp, "project_checks");
        let (k, more) = first_n(checks, SHOWN, "- ", "checks", "rb reconcile lists every one");
        for c in &checks[..k] {
            let ok_none = matches!(c.get("ok"), None | Some(Value::Null));
            l.push(
                format!("- check {}: {}", gs(c, "path"), gs(c, "state"))
                    + &if ok_none && truthy(c, "command") { format!(" · run it: rb journal run -- {}", gs(c, "command")) } else { String::new() },
            );
        }
        l.extend(more);
        // what rb found and nobody recorded, counted as rb status counts it, less what this session left out
        if let Some(f) = found {
            let n = f.placed.len() + f.unplaced.len();
            if n > 0 {
                l.push(format!("- Found in the repository, not in the record ({n}): {}", found_words(f)));
            }
            if !f.uncited.is_empty() {
                l.push(format!("- {}", uncited_words(f)));
            }
        }
        l.push(format!("- {}", scope.said()));
        l.push(String::new());
    }
    let resources = items(&stv, "resources");
    if !resources.is_empty() {
        l.push("## Resources".into());
        l.push(String::new());
        let (k, more) = first_n(resources, SHOWN_ROWS, "- ", "resources", "rb show <id> reads one, and rb context --json lists every one");
        for r in &resources[..k] {
            let av = r.get("availability").cloned().unwrap_or(Value::empty_dict());
            l.push(
                format!("- {} ({}): {}", gs(r, "id"), gs(r, "type"), gs(r, "name"))
                    + &if truthy(r, "version") { format!(" {}", gs(r, "version")) } else { String::new() }
                    + &format!(" · {}", gs(&av, "state"))
                    + &if truthy(&av, "how") { format!(" · how to get it again: {}", gs(&av, "how")) } else { String::new() },
            );
        }
        l.extend(more);
        l.push(String::new());
    }
    let notes: Vec<&Value> = items(&proj, "notes").into_iter().filter(|n| !truthy(n, "retracted")).collect();
    if !notes.is_empty() {
        l.push("## Notes on the project".into());
        l.push(String::new());
        if notes.len() > SHOWN {
            l.push(format!("- {} earlier notes are not listed here: rb status --json lists every one", notes.len() - SHOWN));
        }
        for n in &notes[notes.len().saturating_sub(SHOWN)..] {
            let label = view::note_label(Some(gs(n, "id").as_str()).filter(|_| truthy(n, "id")));
            l.push(format!("- {label}{}: {}", shown_writer(&gs(n, "by")).map(|name| format!(" ({name})")).unwrap_or_default(), gs(n, "text")) + &words(&gs(n, "words"), &gs(n, "by")) + view::not_confirmed(&gs(n, "by")));
        }
        l.push(String::new());
    }
    let decisions = items(&stv, "decisions");
    if !decisions.is_empty() {
        l.push("## Decisions".into());
        l.push(String::new());
        if decisions.len() > SHOWN_ROWS {
            l.push(format!("- {} earlier decisions are not listed here: rb log lists them, and rb context --json lists every one", decisions.len() - SHOWN_ROWS));
        }
        for d in &decisions[decisions.len().saturating_sub(SHOWN_ROWS)..] {
            let current = d.get("current").map_or(true, |c| c.truthy());
            l.push(
                format!("- {}: {} {}{}{}{}", gs(d, "id"), crate::ledger::made(&gs(d, "action")), gs(d, "subject"), by_writer(&gs(d, "by")), crate::view::past_phrase(d), crate::ledger::reason_said(&gs(d, "why"), &gs(d, "by"), truthy(d, "past"), crate::view::confirms_one(d)))
                    + &words(&d.get("words").filter(|w| w.truthy()).map(pyjson::py_str).unwrap_or_default(), &gs(d, "by"))
                    + &crate::ledger::runs_after_of(d)
                    + &if truthy(d, "revisit") { format!(" · revisit: {}", gs(d, "revisit")) } else { String::new() }
                    + if current { "" } else { crate::ledger::moved_since(truthy(d, "past")) },
            );
        }
        l.push(String::new());
    }
    let recent = py_last(led.log_about(None, None)?.into_iter().filter(|r| !crate::ledger::is_session_line(r)).collect(), 40);
    if !recent.is_empty() {
        l.push("## Recent activity".into());
        l.push(String::new());
        let lines = collapse_log(&recent);
        let from = lines.len().saturating_sub(12);
        l.extend(lines[from..].iter().cloned());
        l.push(String::new());
    }
    Ok(l.join("\n"))
}

/// What an agent says of itself with rb context: --model and --provider, each kept as said_id keeps it. E_USAGE for
/// one rb cannot keep as given.
fn said_flag(ns: &Namespace, key: &str) -> Res<Option<String>> {
    let Some(given) = s(ns, key) else { return Ok(None) };
    crate::actor::said_id(&given).map(Some).ok_or_else(|| {
        let trimmed = given.trim();
        let why = if trimmed.is_empty() {
            "is empty".to_string()
        } else if trimmed.chars().count() > crate::actor::SAID_MAX {
            format!("is longer than {} characters", crate::actor::SAID_MAX)
        } else {
            "holds a control character or a line break".to_string()
        };
        RBError::msg_fix("E_USAGE", format!("--{key} {why}."), format!("Give the id your runtime calls it by, on one line: rb context --{key} <your {key} id>."))
    })
}

/// Keep what an agent says of itself for its session's writes, and say what was kept, or why nothing was.
fn say_agent(led: &Ledger, model: Option<&str>, provider: Option<&str>, out: &mut Out) -> Res<()> {
    let who = led.actor()?;
    if !who.id.starts_with("agent:") {
        out.warn("--model and --provider record nothing here: rb keeps them only for an agent's writes, and this is no agent session.");
        return Ok(());
    }
    match crate::actor::session_for(&who) {
        Some(session) => {
            // a checkout rb cannot write still gets its briefing
            if let Err(e) = crate::delta::remember_said(led, &session, model, provider) {
                out.warn(&format!(
                    "what you gave is not kept for this session's writes: {}. Give RB_MODEL=<your model id> on each command instead (RB_PROVIDER for a provider).",
                    e.message
                ));
                return Ok(());
            }
            if let Err(e) = led.log_session(&who, &session) {
                out.warn(&format!("kept for this session's writes; the log takes its line with the session's next write: {}", e.message));
            }
        }
        // rb mcp keeps it for the connection
        None if crate::ledger::client().is_some() => {}
        None => {
            out.warn("this session has no id, so rb keeps no model or provider past this command: give RB_MODEL=<your model id> on each command, or set RB_SESSION=<an id of your own> and say it again.");
            return Ok(());
        }
    }
    let kept: Vec<String> = [("model", model), ("provider", provider)].iter().filter_map(|(k, v)| v.map(|v| format!("{k} {v}"))).collect();
    out.say(format!("Kept for your writes in this session: {}.", kept.join(", ")));
    out.say("");
    Ok(())
}

pub fn cmd_context(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let r = context_of(&led, ns, out);
    out.shows_for(&led);
    r
}

fn context_of(led: &Ledger, ns: &Namespace, out: &mut Out) -> R {
    let (model, provider) = (said_flag(ns, "model")?, said_flag(ns, "provider")?);
    if model.is_some() || provider.is_some() {
        say_agent(&led, model.as_deref(), provider.as_deref(), out)?;
    }
    // look at the repository before the briefing reads it, as rb status does
    crate::journal::look(&led);
    let st = led.status(None)?;
    // what rb found and where it looks, worked out once for the briefing and its data
    let found = crate::journal::found_all(&led, crate::journal::Seen::Briefing).ok();
    let scope = crate::journal::Scope::of(&led);
    let rerun = crate::reproduce::rerun(led)?;
    let md = briefing(&led, &st, found.as_ref(), &scope, !out.json_mode, rerun.as_ref())?;
    out.say(md.trim_end_matches('\n').to_string());
    out.told = Some(told_keys(&Value::Dict(st.clone())));
    out.told_lines = Some(recent_lines(&led)?);
    if let Some(f) = &found {
        out.data.insert("found_unrecorded".into(), Value::Int((f.placed.len() + f.unplaced.len()) as i64));
        out.data.insert("found_needs".into(), Value::List(f.kinds.needs.iter().map(|n| n.to_value()).collect()));
        out.data.insert("uncited_changes".into(), Value::List(f.uncited.iter().map(|u| u.to_value()).collect()));
    }
    out.data.insert("scope".into(), scope.to_value());
    let who = led.actor()?;
    out.data.insert("actor".into(), Value::Dict(actor_value(&led, &who)));
    // as rb status's: a confirmation first in the user's own terminal, and never a session's to run
    let mut open: Vec<String> = confirm_first(led, &Value::Dict(st.clone()))?.into_iter().collect();
    // a clone's rerun first: the code where it is not here, then each experiment's first command
    if let Some(rerun) = rerun {
        rerun_steps(&rerun, None, &mut open);
        out.data.insert("rerun".into(), rerun);
    }
    if let Some(Value::List(l)) = st.get("open") {
        for d in l.iter().filter(next_step).map(|i| gs(i, "do")).filter(|d| !d.is_empty()) {
            if !open.contains(&d) && open.len() < 3 {
                open.push(d);
            }
        }
    }
    out.data.insert("markdown".into(), Value::Str(md));
    out.data.insert("state".into(), Value::Dict(st));
    out.next = open;
    Ok(0)
}

/// rb feedback: the report link for a word of feedback, about a command when `--about` names one. It prints the link,
/// and opens the page in the user's own terminal only (interactive, no agent session, no --json); an agent session
/// shows it for the user to click. rb sends nothing: the page shows what it would send before the user sends it.
pub fn cmd_feedback(ns: &Namespace, out: &mut Out) -> R {
    use crate::ledger::report_link as link;
    let words = crate::cli::ns_list(ns, "about").join(" ");
    let about = Some(words.trim()).filter(|w| !w.is_empty());
    let command = about.map(|w| (w, link::command_path(w)));
    if let Some((w, None)) = command {
        return Err(RBError::msg_fix(
            "E_USAGE",
            format!("--about names no rb command: {w}"),
            "Give the command's words, as rb --help lists them (rb feedback --about run add), or leave --about out.",
        ));
    }
    let url = link::feedback_link(about);
    let terminal = crate::sys::isatty(0) && crate::sys::isatty(1) && link::has_display(|k| std::env::var(k).ok());
    // an agent session, or RB_ACTOR naming an agent, as rb confirm reads them
    let agent = crate::actor::agent_session_sign().is_some() || std::env::var("RB_ACTOR").map_or(false, |a| a.trim().starts_with("agent:"));
    let opens = link::opens_browser(terminal, agent, out.json_mode);
    let opened = opens && link::open_in_browser(&url);
    out.data.insert("report_url".into(), Value::Str(url.clone()));
    out.data.insert("about".into(), command.and_then(|(_, c)| c).map_or(Value::Null, Value::Str));
    out.data.insert("opened".into(), Value::Bool(opened));
    out.say(format!("Send feedback on rb (the page shows what it sends before you send it): {url}"));
    if opened {
        out.say("It is open in your browser. Nothing is sent until you press Send.");
    } else {
        out.say("Open it in your browser. Nothing is sent until you press Send there.");
    }
    Ok(crate::errors::EXIT_OK)
}
