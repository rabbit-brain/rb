//! The shell's document (SPRINT-OCT24.md section 1.3): `build` assembles `rb-shell-view/1.9` of a record for one tier
//! and place, from view::home and the per-object fields the build plan adds. It holds the fields rb computes today
//! (M0), each object in the contract's shape and nothing outside it; the later phases add theirs. It names no writer:
//! no actor, session, channel or "by <name>" enters it, and the Owner tier holds none of the Live tier's own values.

use super::tier::{Row, Tier};
use super::Surface;
use crate::errors::{RBError, Res};
use crate::graph::Graph;
use crate::ledger::Ledger;
use crate::places::{self, Chosen, Place};
use crate::pyjson::py_str;
use crate::record::{Analysis, Criterion, Decision, Experiment, Finding, Resource, Run};
use crate::value::{Dict, DictExt, Value};
use crate::view::{self, ExperimentBrief, FindingLine, Home};
use indexmap::IndexMap;

/// The contract's version, as the document's `contract` names it.
pub const CONTRACT: &str = "rb-shell-view/1.9";

/// The document of `led` for `tier`, at `place` (rb open's chosen place and version; none on a page): drawn on rb open
/// for the Live tier, on rb page for the Owner tier.
pub fn build(led: &Ledger, tier: Tier, place: Option<&Chosen>) -> Res<Value> {
    build_on(led, if tier == Tier::Live { Surface::Open } else { Surface::Page }, place)
}

/// The document of `led` drawn on `surface` (a page), served from `url` where rb export --url names the address it will
/// be served from: the tier table's rows shown only with such an address (a resource's download address, Reproduce
/// this project) are shown with it, and `reproduce_url` is it.
pub fn build_served(led: &Ledger, surface: Surface, url: Option<&str>) -> Res<Value> {
    let url = url.filter(|u| !u.is_empty());
    match Tier::of(surface) {
        Tier::Owner => view::for_a_page(|| {
            let trees = if view::page_trees_known() { Vec::new() } else { view::page_trees_of(led) };
            view::with_page_trees(trees, || document(led, surface, None, url))
        }),
        Tier::Live => build_on(led, surface, None),
    }
}

/// The document of `led` drawn on `surface`, at `place` (rb open's; the record's own, live, when none is given), with
/// every place of the clone as `led`'s record lists them.
pub fn build_on(led: &Ledger, surface: Surface, place: Option<&Chosen>) -> Res<Value> {
    match Tier::of(surface) {
        Tier::Owner => view::for_a_page(|| {
            let trees = if view::page_trees_known() { Vec::new() } else { view::page_trees_of(led) };
            view::with_page_trees(trees, || document(led, surface, None, None))
        }),
        Tier::Live => {
            // the places are listed from the record the screen started on: at a past version `led.root` is rb's checkout
            // of that version, never a place of the clone
            let listed = places::list(&place.map_or_else(|| led.root.clone(), |c| c.start_root()));
            let start;
            let chosen = match place {
                Some(c) => c,
                None => {
                    start = places::choose(&led.root, &listed, None, None, &|_: &Place| Vec::new(), &|| None).map_err(|why| RBError::msg("E_NOT_AVAILABLE", why))?;
                    &start
                }
            };
            let at = At { chosen, listed: &listed };
            // a past version offers no command to copy (guarantee 38): its lines say what they say without one
            let past = chosen.version.is_some();
            view::for_a_screen(|| view::at_place(places::shown(chosen, &listed), || view::as_past_version(past, || document(led, surface, Some(&at), None))))
        }
    }
}

/// Where rb open shows the record: the place and version chosen, and every place of the clone.
struct At<'a> {
    chosen: &'a Chosen,
    listed: &'a [Place],
}

fn document(led: &Ledger, surface: Surface, at: Option<&At>, served: Option<&str>) -> Res<Value> {
    let tier = Tier::of(surface);
    led.locked(|| {
        let home = view::home(led, None)?;
        // rb status's data, read again inside the same lock, its findings already worked out: what the home view leaves out
        let st = Value::Dict(led.status(None)?);
        // Reproduce (BP 1c, reproduce.rs): one Recipes reads git, the files and each resource's availability once a document
        // each experiment's request asks for the run rb status suggests for it (P's with_open)
        let recipes = crate::reproduce::Recipes::new(led, tier)?.with_open(list(&st, "open"));
        // what rests on what, both ways, read once
        let graph = Graph::of(led)?;
        let mut doc = Dict::new();
        doc.insert("contract".into(), CONTRACT.into());
        doc.insert("surface".into(), surface.name().into());
        doc.insert("tier".into(), tier.name().into());
        doc.insert("rb_version".into(), crate::version().into());
        doc.insert("at".into(), crate::pytime::now_dt().format("%Y-%m-%d %H:%M UTC").to_string().into());
        if let Some(at) = at {
            doc.insert("digest".into(), digest_of(led.state_digest()?, at.chosen).into());
        }
        doc.insert("escaped".into(), false.into());
        // the address the project was cloned from, as a page links it (page.rs fork_line): an address only, said
        // without its credentials or query; on a page, only where rb export --url names where it will be served from
        let parent = led.project()?.parent.map(|p| crate::http::shareable(&p.url)).filter(|p| crate::http::is_address(p)).filter(|_| tier == Tier::Live || served.is_some());
        doc.insert("project".into(), crate::obj! {"id" => home.project_id.clone(), "title" => home.title.clone(), "parent" => parent.unwrap_or_default()});
        // the log in sittings, and the newest one as the Overview's Latest changes (BP 5H, history.rs)
        let mut history = crate::history::of(led)?;
        let mut home_v = home_value(&home, &st);
        if let Value::Dict(h) = &mut home_v {
            h.insert("latest".into(), history.latest().map_or(Value::Null, |l| l.to_value()));
            // a page that leaves the machine says the quiet line in no one's voice
            if surface == Surface::Export {
                h.insert("quiet".into(), home.quiet_away.clone().unwrap_or_default().into());
                h.insert("waiting_line".into(), waiting_line(&home.pending, true).into());
            }
        }
        doc.insert("home".into(), home_v);
        doc.insert("pending".into(), pending_value(&home, at));
        let (calls, made_calls) = calls_value(led, &home, at)?;
        doc.insert("your_call".into(), crate::obj! {"n" => calls.len(), "first" => calls.first().map(|c| text(c, "subject")).unwrap_or_default()});
        let mut questions = questions_value(led, &home, &st)?;
        filed_here(&mut questions, &calls, &made_calls);
        doc.insert("open_items".into(), Value::List(home.open_items.clone()));
        let mut unfiled = home.unfiled.clone();
        if let Value::Dict(u) = &mut unfiled {
            // the experiments filed under no question, as rb status says them under "Not filed under a question"
            let ids = home.briefs.iter().filter(|b| b.id.as_deref().map_or(true, str::is_empty)).flat_map(|b| b.experiments.iter().map(|e| e.id.clone()));
            u.insert("experiments".into(), Value::list_of_str(ids));
        }
        doc.insert("unfiled".into(), unfiled);
        doc.insert("made_calls".into(), Value::List(made_calls));
        doc.insert("questions".into(), questions);
        let findings = findings_value(led, &home, &st, &graph)?;
        doc.insert("calls".into(), Value::List(calls.into_iter().map(|c| gate_reason_plain(c, &findings)).collect()));
        doc.insert("findings".into(), findings);
        doc.insert("experiments".into(), experiments_value(led, &home, &st, &recipes)?);
        doc.insert("runs".into(), runs_value(led, tier, &recipes, &graph)?);
        doc.insert("resources".into(), resources_value(led, &home, &st, &graph)?);
        doc.insert("analyses".into(), analyses_value(led, &graph)?);
        overview(led, &mut doc, &st, &home)?;
        // the sentences of findings, questions, open items and a run's retraction and receipt name what rb minted an id
        // for by what it is, never by the id (FIX7); a run's recipe is P's (reproduce.rs)
        let names = minted_names(led, &doc)?;
        // each run as every line names it (contract 1.9, M8): runs.{id}.named
        if let Some(Value::Dict(runs)) = doc.get_mut("runs") {
            for (id, run) in runs.iter_mut() {
                if let (Value::Dict(d), Some(w)) = (run, names.get(id)) {
                    let at = d.get_index_of("label").map_or(d.len(), |i| i + 1);
                    d.shift_insert(at, "named".into(), w.clone().into());
                }
            }
        }
        // rb's "replaced by <run>" names only a run that did replace one (its `replaces`), whose words rb wrote first in
        // the retraction's reason; words someone typed that read the same stay as typed (FIX8)
        let replacers = replacers_of(led)?;
        let replacing = replaced_words(led, &replacers, &names)?;
        for k in ["findings", "questions", "open_items"] {
            if let Some(v) = doc.get_mut(k) {
                name_in_prose(v, &names, &replacing);
            }
        }
        if let Some(Value::Dict(runs)) = doc.get_mut("runs") {
            for (id, run) in runs.iter_mut().filter_map(|(k, r)| if let Value::Dict(d) = r { Some((k, d)) } else { None }) {
                let by: IndexMap<String, String> = replacers.get(id).and_then(|x| names.get(x).map(|w| (x.clone(), w.clone()))).into_iter().collect();
                for k in ["retracted", "receipt_rows"] {
                    if let Some(v) = run.get_mut(k) {
                        name_replaced(v, &by);
                    }
                }
            }
        }
        let held = held_ids(&doc);
        // a line links only what the document holds, as the log rows do
        history.retain_refs(|r| held.contains(r));
        let mut history_v = history.to_value();
        if let Value::Dict(h) = &mut history_v {
            h.insert("log".into(), log_rows(led, &held, &names)?);
        }
        doc.insert("history".into(), history_v);
        match at {
            Some(at) => {
                doc.insert("places".into(), places_value(at));
            }
            // the address rb export --url names, from which Reproduce this project is served
            None => {
                doc.insert("reproduce_url".into(), served.unwrap_or("").into());
            }
        }
        let mut doc = Value::Dict(doc);
        // rb open's places on its shell (BP 4b, places.rs): the scope bar, Elsewhere at the live start place, and where
        // each run was recorded links to that place where rb open can show it
        if let Some(at) = at {
            places::on_the_shell_of(led, &mut doc, at.chosen, at.listed);
        }
        // every file:line the text cites, resolved to its lines, commit and sha256; what a shared page may quote is
        // decided there (CITE items 1 and 4)
        // a past version is read from a checkout rb takes away when it stops: nothing links into it
        let reader = crate::ledger::cite::Reader::new(&led.root, surface == Surface::Export, tier == Tier::Live && !at.map_or(false, |a| a.chosen.version.is_some()));
        let citations = crate::ledger::cite::of_document(&doc, &reader);
        if let Value::Dict(d) = &mut doc {
            d.insert("citations".into(), citations);
        }
        // what a screen would hide is drawn as its escape, as rb open and rb page draw it, and the document says so
        let escaped = led.shows_escapes()? && crate::ledger::design::shown_in(&mut doc);
        if let Value::Dict(d) = &mut doc {
            d.insert("escaped".into(), escaped.into());
        }
        blanks(&mut doc, tier, served.is_some());
        Ok(doc)
    })
}

/// rb open's fingerprint of what it shows, as its /state.json gives it: the record's own at the place it started on,
/// live; with the place and version for any other, so a switch between two places that hold the same record redraws.
pub fn digest_of(of_record: String, chosen: &Chosen) -> String {
    if chosen.start {
        return of_record;
    }
    let at = chosen.version.as_ref().map(|v| v.sha.clone()).unwrap_or_default();
    crate::sources::sha256_bytes(format!("{of_record}\n{}\n{at}", chosen.place.id).as_bytes())
}

/// What a tier does not show, of the fields this document holds, as the tier table says (shell/tier.rs): the user's
/// own words, where a run or a finding's runs were recorded, a resource's local place said in short, and its download
/// address, which a page shows only when rb export --url names the address it is served from (`served`).
fn blanks(doc: &mut Value, tier: Tier, served: bool) {
    let Value::Dict(d) = doc else { return };
    // the words given with a comparison's declaration are the user's, Live only (contract 1.5)
    if let Some(Value::List(qs)) = d.get_mut("questions").filter(|_| !tier.has(Row::YourWords)) {
        let relations = qs
            .iter_mut()
            .filter_map(|q| q.as_dict_mut()?.get_mut("overview")?.as_dict_mut()?.get_mut("relations")?.as_list_mut())
            .flatten();
        let decls = relations.filter_map(|r| r.as_dict_mut()?.get_mut("declarations")?.as_list_mut()).flatten();
        for decl in decls.filter_map(Value::as_dict_mut) {
            decl.insert("words".into(), "".into());
        }
    }
    if let Some(Value::Dict(fs)) = d.get_mut("findings") {
        for f in fs.values_mut().filter_map(Value::as_dict_mut) {
            if !tier.has(Row::YourWords) {
                f.insert("your_words".into(), "".into());
            }
            if !tier.has(Row::RecordedIn) {
                for k in ["observed", "observed_short"] {
                    f.insert(k.into(), "".into());
                }
                f.insert("observed_places".into(), Value::List(Vec::new()));
            }
        }
    }
    if let Some(Value::Dict(rs)) = d.get_mut("runs") {
        // where a run was recorded and the link to that place: a place's path and name, Live only (SPRINT 1.5), null
        // on a page, as rb open's document holds both on every run
        for r in rs.values_mut().filter_map(Value::as_dict_mut).filter(|_| !tier.has(Row::RecordedIn)) {
            r.insert("observed_in".into(), Value::Null);
            r.insert("recorded_in".into(), Value::Null);
        }
    }
    if let Some(Value::Dict(rs)) = d.get_mut("resources") {
        for r in rs.values_mut().filter_map(Value::as_dict_mut) {
            if !tier.has(Row::Places) {
                r.shift_remove("where_short");
            }
            let shown = match tier.shows(Row::DownloadAddress) {
                super::tier::Shown::Yes => true,
                super::tier::Shown::WithUrl => served,
                _ => false,
            };
            if !shown {
                r.insert("url".into(), "".into());
            }
        }
    }
}

// ---------------------------------------------------------------- the parts

/// "2026-10-12 12:20 UTC" of "2026-10-12T14:20:33+02:00": the minute in UTC, said so.
fn minute(at: &str) -> String {
    match crate::pytime::parse_iso(at) {
        Some(t) => t.with_timezone(&chrono::Utc).format("%Y-%m-%d %H:%M UTC").to_string(),
        None => at.chars().take(16).collect::<String>().replace('T', " "),
    }
}

/// "2026-10-12" of a time: its day in UTC, as a project note's "<date> UTC:" names it.
fn day(at: &str) -> String {
    match crate::pytime::parse_iso(at) {
        Some(t) => t.with_timezone(&chrono::Utc).format("%Y-%m-%d").to_string(),
        None => at.chars().take(10).collect(),
    }
}

fn text(v: &Value, k: &str) -> String {
    v.get(k).filter(|x| !x.is_null()).map(py_str).unwrap_or_default()
}

fn strs(v: &[String]) -> Value {
    Value::list_of_str(v.to_vec())
}

/// The keys of `v` the contract holds, in its order: a key `v` lacks stays out.
fn pick(v: &Value, keys: &[&str]) -> Dict {
    keys.iter().filter_map(|k| v.get(k).map(|x| (k.to_string(), x.clone()))).collect()
}

/// A list of `v`'s, or nothing.
fn list<'a>(v: &'a Value, k: &str) -> &'a [Value] {
    v.get(k).and_then(Value::as_list).map(Vec::as_slice).unwrap_or(&[])
}

fn home_value(h: &Home, st: &Value) -> Value {
    let or = |s: &Option<String>| s.clone().unwrap_or_default();
    // each project note in its parts: its id, its day in UTC, and its text with the words given with it as the reader
    // says them; who wrote it never
    let notes: Vec<Value> = st
        .get("project")
        .map(|p| list(p, "notes"))
        .unwrap_or(&[])
        .iter()
        .filter(|n| !n.get("retracted").map_or(false, Value::truthy))
        .map(|n| crate::obj! {"id" => text(n, "id"), "at" => day(&text(n, "at")), "text" => view::note_said(&text(n, "text"), &text(n, "words"), &text(n, "by"))})
        .collect();
    crate::obj! {
        "began" => or(&h.began), "scope" => or(&h.scope), "scope_short" => or(&h.scope_short), "uncited" => h.uncited,
        "uncited_line" => or(&h.uncited_line), "uncited_short" => or(&h.uncited_short),
        "found" => Value::List(h.found.iter().map(|a| crate::obj! {
            "request_id" => a.request_id.clone(), "title" => a.title.clone(), "hint" => a.hint.clone(), "at" => minute(&a.asked_at),
            // the whole command, rb first, as rb ui runs it and as every argv of the contract reads
            "argv" => Value::list_of_str(std::iter::once("rb".to_string()).chain(a.argv.iter().cloned()))
        }).collect()),
        "found_total" => h.found.len(), "unplaced" => strs(&h.unplaced), "waiting_line" => waiting_line(&h.pending, false),
        "tally" => Value::List(h.tally.iter().map(|(k, n)| crate::list![k.clone(), *n]).collect()), "muted" => strs(&h.muted),
        "open_line" => h.open_besides.clone().unwrap_or_default(), "notes" => Value::List(notes), "quiet" => or(&h.quiet),
        "counts" => h.counts.clone(), "made" => h.made.clone(), "last_write" => crate::obj! {"at" => h.last_write.clone()},
        "tree" => Value::List(h.tree.iter().map(|t| crate::obj! {
            "kind" => t.kind.clone(), "id" => t.id.clone(), "label" => t.label.clone(), "standing" => t.standing.clone(), "depth" => t.depth,
            "muted" => strs(&t.muted)
        }).collect())
    }
}

/// The line that says what waits for a confirmation, as the quiet line begins with it: "1 call and 2 designs wait for
/// your confirmation, listed apart"; nothing while nothing waits. `away` (a page that leaves the machine) says it in no
/// one's voice: "wait for a confirmation".
fn waiting_line(rows: &[view::PendingRow], away: bool) -> String {
    let whose = if away { "a" } else { "your" };
    match rows.len() {
        0 => String::new(),
        1 => format!("{} waits for {whose} confirmation, listed apart", view::waiting_said(rows)),
        _ => format!("{} wait for {whose} confirmation, listed apart", view::waiting_said(rows)),
    }
}

/// An experiment's who line, as its card says it, from its row in rb status's data: "experiment · open · locked · 3 runs".
fn who_line(e: &Value) -> String {
    let n = e.get("run_count").and_then(Value::as_i64).unwrap_or(0);
    format!(
        "experiment · {} · {} · {n} run{}",
        e.get("state").map(py_str).unwrap_or_else(|| "planned".into()),
        if e.get("frozen").map_or(false, Value::truthy) { "locked" } else { "not locked" },
        if n == 1 { "" } else { "s" }
    )
}

/// What waits for a confirmation, each with the one command that confirms it from where rb open started, and rb's line
/// for the designs, and `confirm_all`: rb's line that confirms every call listed, as listed (view::confirm_all_line), null
/// where fewer than two wait or a design does.
fn pending_value(h: &Home, at: Option<&At>) -> Value {
    let confirm = |c: &str| at.map_or_else(|| c.to_string(), |a| a.chosen.confirm(c));
    let rows: Vec<Value> = h
        .pending
        .iter()
        .map(|p| crate::obj! {
            "id" => p.id.clone(), "kind" => p.kind.clone(), "subject" => p.subject.clone(), "text" => p.text.clone(), "confirm" => confirm(&p.confirm),
            "lines" => strs(&p.lines)
        })
        .collect();
    // rb's line that confirms every call listed, as listed, where more than one waits and no design does
    let all = view::confirm_all_line(&h.pending).map_or(Value::Null, |(line, cmd)| crate::obj! {"line" => line, "confirm" => confirm(&cmd)});
    crate::obj! {"rows" => Value::List(rows), "confirm_all" => all, "designs_line" => view::designs_line(&h.pending, None).unwrap_or_default()}
}

/// The question rb status's data holds for `id`.
fn question_of<'a>(st: &'a Value, id: &str) -> Option<&'a Value> {
    list(st, "questions").iter().find(|q| text(q, "id") == id)
}

/// When an interpretation was declared, and why, as its group's first criterion's link says it: "declared <time>;
/// why: <why>", a session's reason as its note.
fn declared(q: Option<&Value>, group: &Value) -> String {
    let said = group.get("text").map(py_str).unwrap_or_default();
    let Some(cid) = list(group, "criteria").first().map(py_str) else { return String::new() };
    let row = q.and_then(|q| q.get("inquiry")).map(|i| list(i, "direct")).unwrap_or(&[]).iter().find(|r| text(r, "criterion") == cid);
    let Some(row) = row else { return String::new() };
    let links: Vec<&Value> = if list(row, "links").is_empty() { row.get("link").into_iter().collect() } else { list(row, "links").iter().collect() };
    let Some(link) = links.into_iter().find(|l| text(l, "when_met") == said) else { return String::new() };
    let when = text(link, "declared_at");
    if when.is_empty() {
        return String::new();
    }
    // a session's interpretation the user's confirmation of its design covers is still its note, no longer waiting
    let by = text(link, "declared_by");
    let confirmed = group.get("confirmed").map_or(false, Value::truthy);
    let by = if confirmed && !by.is_empty() && !view::words_are_theirs(&by) { view::CONFIRMED.to_string() } else { by };
    format!("declared {}{}", minute(&when), view::reason_with_words(&text(link, "why"), &text(link, "words"), &by, "; why: "))
}

fn questions_value(led: &Ledger, h: &Home, st: &Value) -> Res<Value> {
    Ok(Value::List(
        h.briefs
            .iter()
            .filter(|b| b.id.is_some())
            .map(|b| -> Res<Value> {
                let q = question_of(st, b.id.as_deref().unwrap_or(""));
                let mut overview = pick(&b.overview, &["headline", "tone", "interpretations", "limits", "relations", "evidence_count", "unmapped_count"]);
                let groups: Vec<Value> = list(&b.overview, "interpretations")
                    .iter()
                    .map(|g| {
                        let mut d = pick(g, &["text", "active", "confirmed", "criteria", "holds", "calls"]);
                        d.insert("declared".into(), declared(q, g).into());
                        Value::Dict(d)
                    })
                    .collect();
                overview.insert("interpretations".into(), Value::List(groups));
                let mut d = crate::dict! {
                    "id" => Value::opt_str(b.id.clone()), "text" => b.text.clone(), "status" => b.status.clone(), "depth" => b.depth,
                    "parent" => Value::opt_str(b.parent.clone()), "rollup" => b.rollup.clone(), "overview" => Value::Dict(overview),
                    "lead" => b.overview.get("lead").cloned().unwrap_or(Value::Null), "more" => b.overview.get("more").cloned().unwrap_or(Value::Int(0))
                };
                d.insert("findings".into(), Value::list_of_str(b.inquiry_findings.iter().map(|f| f.criterion_id.clone())));
                d.insert("experiments".into(), Value::list_of_str(b.experiments.iter().map(|e| e.id.clone())));
                for (k, v) in [("changed", &b.changed), ("still_open", &b.still_open), ("ruled", &b.ruled), ("summary", &b.summary)] {
                    d.insert(k.into(), strs(v));
                }
                // rb's request in words for the first run rb suggests for the question (BP 1c, next_step.rs)
                d.insert("next_step".into(), crate::next_step::of(led, b, list(st, "open"))?.into());
                Ok(Value::Dict(d))
            })
            .collect::<Res<Vec<Value>>>()?,
    ))
}

/// The calls waiting on you (D2: the user's calls alone, never what rb found or what waits for a confirmation), one
/// per subject decided, in rb status's order: each with what it asks and on what, the question whose Your call lists
/// it, rb's words, why rb asks and the calls it offers; then the latest call made or confirmed on each subject, as its
/// line says it, with the reason a session gave apart where it is the call's note.
fn calls_value(led: &Ledger, h: &Home, at: Option<&At>) -> Res<(Vec<Value>, Vec<Value>)> {
    let calls: Vec<Value> = h
        .calls
        .iter()
        .map(|c| {
            let mut d = pick(c, &["subject", "kind", "code", "label", "title", "question", "what", "reason", "actions"]);
            // the open item's own command, as rb status gives it, from the terminal the screen started in; a past
            // version offers none, as it offers no action
            let cmd = text(c, "command");
            let cmd = match at {
                Some(a) if a.chosen.version.is_some() => String::new(),
                Some(a) => a.chosen.confirm(&cmd),
                None => cmd,
            };
            d.insert("do".into(), cmd.into());
            Value::Dict(d)
        })
        .collect();
    let mut made = Vec::new();
    let mut subjects: Vec<String> = Vec::new();
    for d in led.live::<Decision>()? {
        if !subjects.contains(&d.subject) {
            subjects.push(d.subject.clone());
        }
    }
    for subject in subjects {
        let Some(d) = led.decisions_on(&subject)?.pop() else { continue };
        let confirms = led.confirms(&d.id)?;
        let line = view::with_words(&crate::ledger::call_line(&d.action, &d.by, &d.at, &d.why, d.past.as_ref(), confirms), &d.words, &d.by);
        // the reason a session gave, which the line says as its note, never as what the call rests on
        let as_note = confirms || (d.past.is_some() && !view::words_are_theirs(&d.by));
        let at = match &d.past {
            Some(p) => crate::ledger::past_when(p),
            None => d.at.chars().take(10).collect(),
        };
        made.push(crate::obj! {
            "subject" => subject.clone(), "question" => view::decided_at(led, &h.briefs, list(&h.unfiled, "criteria").iter().map(py_str).collect::<Vec<_>>().as_slice(), &subject, None, None)?, "at" => at, "verb" => crate::ledger::made(&d.action), "line" => line,
            "note" => if as_note { d.why.clone() } else { String::new() }
        });
    }
    Ok((calls, made))
}

/// Each question's calls waiting and made: the subjects filed there, each once.
fn filed_here(questions: &mut Value, calls: &[Value], made: &[Value]) {
    let Value::List(qs) = questions else { return };
    for q in qs.iter_mut() {
        let id = text(q, "id");
        let here = |xs: &[Value]| -> Vec<String> {
            let mut out: Vec<String> = Vec::new();
            for x in xs.iter().filter(|x| text(x, "question") == id) {
                let s = text(x, "subject");
                if !out.contains(&s) {
                    out.push(s);
                }
            }
            out
        };
        let (waiting, decided) = (here(calls), here(made));
        if let Value::Dict(d) = q {
            d.insert("calls".into(), Value::list_of_str(waiting));
            d.insert("made_calls".into(), Value::list_of_str(decided));
        }
    }
}

// ---------------------------------------------------------------- Overview v2 (contract 1.6)

/// The Overview as a projection of the record onto the user's decisions (OVERVIEW-V2.md, contract 1.6), worked out from
/// the document already built, so it reads nothing twice: each question's state, tallies and compact line
/// (`questions[]`); per top-level question, rb's state word, the line on why with its reasons and the tallies
/// (`home.standing`); what weakens the whole body of evidence (`home.integrity`); and everything rb cannot settle and the
/// user can, in the fixed order (`home.queue`).
fn overview(led: &Ledger, doc: &mut Dict, st: &Value, home: &Home) -> Res<()> {
    let findings = doc.get("findings").cloned().unwrap_or(Value::empty_dict());
    let states: IndexMap<String, String> = list(st, "questions").iter().map(|q| (text(q, "id"), text(q, "state"))).collect();
    let mut standing: Vec<Value> = Vec::new();
    let pending = doc.get("pending").map(|p| list(p, "rows").to_vec()).unwrap_or_default();
    let made_calls = doc.get("made_calls").and_then(Value::as_list).cloned().unwrap_or_default();
    let experiments = doc.get("experiments").and_then(Value::as_list).cloned().unwrap_or_default();
    // a page that leaves the machine says what waits for a confirmation in no one's voice
    let away = doc.get("surface").map(py_str).as_deref() == Some(Surface::Export.name());
    if let Some(Value::List(qs)) = doc.get_mut("questions") {
        // what a question's tree holds below it, for the designs and calls that wait under it: its experiments, and its
        // sub-questions with their criteria and experiments, all the way down
        let mut below: IndexMap<String, Vec<String>> = IndexMap::new();
        for q in qs.iter().rev() {
            let mut exps: Vec<String> = list(q, "experiments").iter().map(py_str).collect();
            exps.extend(below.get(&text(q, "id")).cloned().unwrap_or_default());
            if q.get("depth").and_then(Value::as_i64) != Some(0) {
                // a sub-question is in its parent's tree, with the criteria linked to it
                let mut mine = vec![text(q, "id")];
                mine.extend(list(q, "findings").iter().map(py_str));
                let parent = text(q, "parent");
                if !parent.is_empty() {
                    below.entry(parent).or_default().extend(mine);
                }
            }
            let parent = text(q, "parent");
            if !parent.is_empty() {
                below.entry(parent).or_default().extend(exps.iter().cloned());
            }
            below.entry(text(q, "id")).or_default().extend(exps);
        }
        for q in qs.iter_mut() {
            let id = text(q, "id");
            let ids: Vec<String> = list(q, "findings").iter().map(py_str).collect();
            let tallies = tallies_of(&findings, &ids);
            let state = states.get(&id).filter(|s| !s.is_empty()).cloned().unwrap_or_else(|| "open".into());
            let labels = tallies.as_list().map(|v| v.as_slice()).unwrap_or(&[]).iter().map(|t| text(t, "label"));
            let compact = std::iter::once(state.clone()).chain(labels).collect::<Vec<_>>().join(" · ");
            let exps = below.get(&id).cloned().unwrap_or_default();
            if q.get("depth").and_then(Value::as_i64) == Some(0) {
                standing.push(standing_of(q, &state, &ids, &tallies, &findings, &pending, &made_calls, &exps, away));
            }
            // a question no rule is linked to, in the one reading its card says too; "" where a rule is linked
            let no_rule = if ids.is_empty() { no_rule_line(&id, &ids, &exps, &pending, &findings, away) } else { String::new() };
            if let Value::Dict(d) = q {
                d.insert("no_rule".into(), no_rule.into());
                d.insert("state".into(), state.into());
                d.insert("tallies".into(), tallies);
                d.insert("compact".into(), compact.into());
            }
        }
    }
    let integrity = integrity_of(doc, st, home);
    let queue = queue_of(led, doc, home, &findings, &pending, &experiments)?;
    if let Some(Value::Dict(h)) = doc.get_mut("home") {
        h.insert("standing".into(), Value::List(standing));
        h.insert("integrity".into(), integrity);
        h.insert("queue".into(), Value::List(queue));
    }
    Ok(())
}

/// The criteria `ids` by rule word, in rb's tally order, only the words some criterion has: each with its count, its
/// label ("4 met") and the criteria it counts, for the link to that filtered list.
fn tallies_of(findings: &Value, ids: &[String]) -> Value {
    let mut by: IndexMap<String, Vec<String>> = IndexMap::new();
    for id in ids {
        let state = findings.get(id).map(|f| text(f, "state")).unwrap_or_default();
        if !state.is_empty() {
            by.entry(state).or_default().push(id.clone());
        }
    }
    let mut rows: Vec<(String, Vec<String>)> = by.into_iter().collect();
    rows.sort_by_key(|(k, _)| crate::cmds::tally_order(k));
    Value::List(
        rows.into_iter()
            .map(|(state, ids)| crate::obj! {"state" => state.clone(), "n" => ids.len(), "label" => format!("{} {}", ids.len(), state_label(&state)), "criteria" => Value::list_of_str(ids)})
            .collect(),
    )
}

fn plural_is(n: usize) -> &'static str {
    if n == 1 { "is" } else { "are" }
}

/// "1 of 13 rules", "1 of 1 rule".
fn of_criteria(k: usize, n: usize) -> String {
    format!("{k} of {n} {}", if n == 1 { "rule" } else { "rules" })
}

/// One top-level question's standing card: rb's state word, one computed line on why with the two strongest reasons rb
/// can name (chosen in the queue's fixed order: a baseline that does not reproduce, criteria not met, criteria
/// undetermined or untested, designs and calls waiting for a confirmation), the tallies, and the active recorded
/// interpretation in one line.
#[allow(clippy::too_many_arguments)]
fn standing_of(q: &Value, state: &str, ids: &[String], tallies: &Value, findings: &Value, pending: &[Value], made_calls: &[Value], exps: &[String], away: bool) -> Value {
    let finding = |id: &str| finding_in(findings, id);
    let id = text(q, "id");
    let n = ids.len();
    let mut reasons: Vec<Value> = Vec::new();
    let reason = |kind: &str, text: String, criteria: Vec<String>| crate::obj! {"kind" => kind, "text" => text, "criteria" => Value::list_of_str(criteria)};
    let line;
    if state == "open" {
        let of = |pred: &dyn Fn(&Value) -> bool| -> Vec<String> { ids.iter().filter(|c| pred(finding(c))).cloned().collect() };
        let missed = |f: &Value| matches!(text(f, "state").as_str(), "misses" | "not reproduced" | "gate missed");
        let cited = |f: &Value| text(f, "reference_label").starts_with("cited ");
        let base = of(&|f| missed(f) && f.get("reads_baseline").map_or(false, Value::truthy));
        // each baseline or control once, however many of its criteria miss: by its experiment and its label
        let mut arms: Vec<(String, String)> = Vec::new();
        for c in &base {
            let f = finding(c);
            let arm = (text(f, "experiment"), text(f, "subject_label"));
            if !arms.contains(&arm) {
                arms.push(arm);
            }
        }
        if let [(_, label)] = arms.as_slice() {
            let how = match base.as_slice() {
                // a cited number it does not match; an inherited rule (rb clone) has none, and is not reproduced
                [one] if text(finding(one), "state") == "not reproduced" && cited(finding(one)) => "doesn't match its cited number".to_string(),
                [one] if text(finding(one), "state") == "not reproduced" => "is not reproduced".to_string(),
                _ if base.iter().all(|c| text(finding(c), "state") == "not reproduced" && cited(finding(c))) => format!("doesn't match {} cited numbers", base.len()),
                _ if base.iter().all(|c| text(finding(c), "state") == "not reproduced") => format!("does not reproduce {} rules", base.len()),
                [_] => "doesn't meet its rule".to_string(),
                _ => format!("doesn't meet {} rules", base.len()),
            };
            reasons.push(reason("baseline", format!("the {label} {how}"), base.clone()));
        } else if !arms.is_empty() {
            let controls = arms.iter().filter(|(_, l)| l.ends_with(" control")).count();
            let what = match controls {
                0 => "baselines",
                n if n == arms.len() => "controls",
                _ => "baselines and controls",
            };
            reasons.push(reason("baseline", format!("{} {what} don't meet their rules", arms.len()), base.clone()));
        }
        let not_met = of(&|f| missed(f)).into_iter().filter(|c| !base.contains(c)).collect::<Vec<_>>();
        if !not_met.is_empty() {
            reasons.push(reason("not_met", format!("{} {} not met", of_criteria(not_met.len(), n), plural_is(not_met.len())), not_met));
        }
        let und = of(&|f| matches!(text(f, "state").as_str(), "undetermined" | "gate undetermined"));
        let unt = of(&|f| matches!(text(f, "state").as_str(), "untested" | "gate untested"));
        // one plain word for both where both are there: not settled; rules with no results at all are not tested
        let word = if und.is_empty() { "not tested" } else { "not settled" };
        let unsettled: Vec<String> = ids.iter().filter(|c| und.contains(c) || unt.contains(c)).cloned().collect();
        if !unsettled.is_empty() {
            reasons.push(reason("unsettled", format!("{} {} {word}", of_criteria(unsettled.len(), n), plural_is(unsettled.len())), unsettled));
        }
        for (kind, said) in waits_of(&id, ids, exps, pending, away) {
            reasons.push(reason(kind, said, Vec::new()));
        }
        if n == 0 && reasons.is_empty() {
            reasons.push(reason("no_criteria", "no rule is linked to it".into(), Vec::new()));
        }
        reasons.truncate(2);
        let why: Vec<String> = reasons.iter().map(|r| text(r, "text")).collect();
        // the latest call on it that keeps it open (continue, investigate), else none is recorded
        let call = made_calls.iter().rev().find(|m| text(m, "subject") == id).map(|m| text(m, "line"));
        line = match why.as_slice() {
            // a question no rule is linked to reads one way on its card and in its own view (FIX2 item 3)
            _ if n == 0 => no_rule_line(&id, ids, exps, pending, findings, away),
            [] => match call {
                Some(call) => format!("No answer yet. The latest call on it: {call}"),
                None => "No answer yet: no call on it is recorded.".to_string(),
            },
            [one] => format!("No answer yet: {one}."),
            [a, b, ..] => format!("No answer yet: {a}, and {b}."),
        };
    } else {
        // a question decided says the call that decided it, as its line says it
        line = made_calls.iter().rev().find(|m| text(m, "subject") == id).map(|m| text(m, "line")).unwrap_or_default();
    }
    let lead = q.get("lead").cloned().unwrap_or(Value::Null);
    let interpretation = if text(&lead, "kind") == "interpretation" && lead.get("active").map_or(false, Value::truthy) {
        let index = lead.get("index").and_then(Value::as_i64).unwrap_or(0) as usize;
        let holds = q.get("overview").map(|o| list(o, "interpretations")).and_then(|gs| gs.get(index)).map(|g| text(g, "holds")).unwrap_or_default();
        let words = text(&lead, "text");
        let confirmed = lead.get("confirmed").map_or(false, Value::truthy);
        // below rb's own line, a recorded interpretation is a note, never rb's verdict, its state said in the line
        // (PLAIN-WORDS-PASS-3): "A note, not confirmed: <its words>." or "Note: <its words>."; then its rules' state as a
        // sentence of its own ("Its rule is met."). It names no one, so a page that leaves the machine says it the same.
        let label = interpretation_label(confirmed);
        let ends = words.trim_end().ends_with(['.', '!', '?']);
        let stop = if ends || words.is_empty() { "" } else { "." };
        let said = if holds.is_empty() { format!("{label}: {words}{stop}") } else { format!("{label}: {words}{stop} {holds}") };
        crate::obj! {"line" => said, "text" => words, "holds" => holds, "confirmed" => confirmed}
    } else {
        crate::obj! {"line" => "", "text" => "", "holds" => "", "confirmed" => false}
    };
    // the state names its subject (PLAIN-WORDS-PASS-3): "Open question", "Answered question"
    let mut word = format!("{state} question");
    if let Some(c) = word.get_mut(0..1) {
        c.make_ascii_uppercase();
    }
    crate::obj! {
        "question" => id, "text" => text(q, "text"), "state" => state, "state_word" => word, "line" => line, "reasons" => Value::List(reasons),
        "tallies" => tallies.clone(), "interpretation" => interpretation
    }
}

/// What waits for a confirmation in question `id`'s tree (its criteria `ids`, what lies below it `exps`): the designs,
/// then the calls, each with its count, in the user's voice on their own screens and in no one's on a page that leaves
/// the machine ("1 experiment design waits for your confirmation", "... for a confirmation").
fn waits_of(id: &str, ids: &[String], exps: &[String], pending: &[Value], away: bool) -> Vec<(&'static str, String)> {
    let whose = if away { "a" } else { "your" };
    let mut said = Vec::new();
    let designs = pending.iter().filter(|p| text(p, "kind") == "design" && exps.contains(&design_of(p))).count();
    if designs > 0 {
        said.push(("design", if designs == 1 { format!("1 experiment design waits for {whose} confirmation") } else { format!("{designs} experiment designs wait for {whose} confirmation") }));
    }
    let calls = pending.iter().filter(|p| matches!(text(p, "kind").as_str(), "proposed" | "past") && { let s = text(p, "subject"); s == id || ids.contains(&s) || exps.contains(&s) }).count();
    if calls > 0 {
        said.push(("pending_call", if calls == 1 { format!("1 call waits for {whose} confirmation") } else { format!("{calls} calls wait for {whose} confirmation") }));
    }
    said
}

/// A question no rule is linked to, in one reading its card and its own view share (FIX2 item 3): "No rule tests this
/// question yet.", or, where rules are linked to its sub-questions, "No rule is linked to this question itself; 6 rules
/// are linked to its sub-questions."; then what waits in its tree, if anything ("1 experiment design waits for your
/// confirmation.").
fn no_rule_line(id: &str, ids: &[String], exps: &[String], pending: &[Value], findings: &Value, away: bool) -> String {
    // the rules linked below it, each once
    let mut below: Vec<&String> = Vec::new();
    for x in exps.iter().filter(|x| findings.get(x.as_str()).is_some()) {
        if !below.contains(&x) {
            below.push(x);
        }
    }
    let below = below.len();
    let head = match below {
        0 => super::strings::NO_RULE_TESTS.to_string(),
        1 => "No rule is linked to this question itself; 1 rule is linked to its sub-questions.".to_string(),
        n => format!("No rule is linked to this question itself; {n} rules are linked to its sub-questions."),
    };
    let waits: Vec<String> = waits_of(id, ids, exps, pending, away).into_iter().map(|(_, s)| s).collect();
    match waits.as_slice() {
        [] => head,
        [one] => format!("{head} {}.", capital(one)),
        [a, b, ..] => format!("{head} {}, and {b}.", capital(a)),
    }
}

/// A missed gate's reason in the sentence its reading says (DESIGN-PASS-1 P8): each missed gate of the call's
/// experiment by its reading ("INT8 is 0.97 ms lower than the FP32 baseline (-1.25 to -0.68)"), "; " apart, never rb's
/// comparison text; the reason as rb gives it where the document holds no such gate.
fn gate_reason_plain(mut call: Value, findings: &Value) -> Value {
    if text(&call, "code") != "gate_missed" {
        return call;
    }
    let exp = text(&call, "subject");
    let gates: Vec<&Value> = findings.as_dict().map(|fs| fs.values().filter(|f| text(f, "experiment") == exp && text(f, "state") == "gate missed").collect()).unwrap_or_default();
    // one gate by its reading alone; several each by its statement first, so no two read alike
    let said: Vec<String> = gates
        .iter()
        .filter(|f| !text(f, "reading_short").is_empty())
        .map(|f| if gates.len() == 1 { text(f, "reading_short") } else { format!("{}: {}", text(f, "statement"), text(f, "reading_short")) })
        .collect();
    if let (false, Value::Dict(c)) = (said.is_empty(), &mut call) {
        if let Some(Value::Dict(r)) = c.get_mut("reason") {
            r.insert("detail".into(), said.join("; ").into());
        }
    }
    call
}

/// A gate a session set, waiting apart from its design, as a queue row says it: by its experiment, never its hash.
fn gate_row(p: &Value, called: &dyn Fn(&str) -> String) -> (String, String) {
    let exp = design_of(p);
    // the gate's own words, as its row's text says them after "<exp> gates: "
    let said = text(p, "text");
    let said = said.split_once(" gates: ").map(|(_, g)| g.to_string()).unwrap_or_default();
    (format!("Confirm the gate on {}", called(&exp)), said)
}

/// The gates of experiment `exp` that miss, in one line, each by its statement: "gate missed: INT8 answers in 7 ms or
/// less", "2 gates missed: ... · ..."; None where the document holds none. Ids live in the inspector (Look 3).
fn gates_missed(findings: &Value, exp: &str) -> Option<String> {
    let said: Vec<String> = findings.as_dict()?.iter().filter(|(_, f)| text(f, "experiment") == exp && text(f, "state") == "gate missed").map(|(id, f)| Some(text(f, "statement")).filter(|s| !s.is_empty()).unwrap_or_else(|| id.clone())).collect();
    match said.len() {
        0 => None,
        1 => Some(format!("Gate missed: {}", said[0])),
        n => Some(format!("{n} gates missed: {}", first_two(&said))),
    }
}

/// The finding `id` of the document's findings, or null.
fn finding_in<'a>(findings: &'a Value, id: &str) -> &'a Value {
    static NULL: Value = Value::Null;
    findings.get(id).unwrap_or(&NULL)
}

/// The experiment a design (or a gate) waiting for its confirmation is of: its row's id before the hash.
fn design_of(p: &Value) -> String {
    text(p, "id").split('@').next().unwrap_or("").to_string()
}

/// What a standing card calls the active recorded interpretation (PLAIN-WORDS-PASS-3): a note, its state in the label;
/// "Note" where the user owns it (declared in their terminal, or in a design they confirmed), "A note, not confirmed"
/// where it waits for their confirmation, on every surface, since neither names whose it is.
fn interpretation_label(confirmed: bool) -> &'static str {
    if confirmed { "Note" } else { "A note, not confirmed" }
}

/// The integrity strip's line for the `k` of `r` runs with no command from when they ran: none recorded, or one given
/// later and not observed (Given::made_it). Such a run may record a command, so the line never says it records none.
fn without_command_line(k: usize, r: usize) -> String {
    match (k, r) {
        (1, 1) => "The one run has no command from when it ran.".to_string(),
        (k, r) if k == r => format!("None of the {r} runs has a command from when it ran."),
        (1, r) => format!("1 of {r} runs has no command from when it ran."),
        (k, r) => format!("{k} of {r} runs have no command from when they ran."),
    }
}

/// What weakens the whole body of evidence, each part one plain line with its numbers and the ids it counts, for the
/// link to that filtered list: criteria declared after their evidence, runs with no command as given, runs retracted,
/// records edited outside rb; then one line on what they affect. No part, no line.
fn integrity_of(doc: &Dict, st: &Value, home: &Home) -> Value {
    let mut parts: Vec<Value> = Vec::new();
    let part = |kind: &str, n: usize, of: usize, line: String, ids: Vec<String>| crate::obj! {"kind" => kind, "n" => n, "of" => of, "line" => line, "ids" => Value::list_of_str(ids)};
    let made = |k: &str| home.made.get(k).and_then(Value::as_i64).unwrap_or(0) as usize;
    let late = home.weakened.late.clone();
    let (a, n) = (late.len(), made("criteria"));
    if a > 0 {
        let line = match (a, n) {
            (1, 1) => "The one rule was written after its results came in.".to_string(),
            (a, n) if a == n => format!("All {n} rules were written after their results came in."),
            (1, n) => format!("1 of {n} rules was written after its results came in."),
            (a, n) => format!("{a} of {n} rules were written after their results came in."),
        };
        parts.push(part("after_evidence", a, n, line, late));
    }
    let none = home.weakened.no_command.clone();
    let (k, r) = (none.len(), made("runs"));
    if k > 0 {
        parts.push(part("without_command", k, r, without_command_line(k, r), none));
    }
    let runs = doc.get("runs").and_then(Value::as_dict).cloned().unwrap_or_default();
    let retracted: Vec<String> = runs.iter().filter(|(_, v)| v.get("retracted").map_or(false, |x| !x.is_null())).map(|(id, _)| id.clone()).collect();
    if !retracted.is_empty() {
        let k = retracted.len();
        let line = if k == 1 { "1 run was retracted.".to_string() } else { format!("{k} runs were retracted.") };
        parts.push(part("retracted", k, runs.len(), line, retracted));
    }
    let mut edited: Vec<String> = Vec::new();
    for i in list(st, "open").iter().filter(|i| text(i, "code") == "edited_outside_rb") {
        let s = text(i, "subject");
        if !edited.contains(&s) {
            edited.push(s);
        }
    }
    if !edited.is_empty() {
        let k = edited.len();
        let line = if k == 1 { "1 record was edited outside rb.".to_string() } else { format!("{k} records were edited outside rb.") };
        parts.push(part("edited", k, 0, line, edited));
    }
    let has = |k: &str| parts.iter().any(|p| text(p, "kind") == k);
    let affects = if has("after_evidence") || has("without_command") {
        "Treat them as a record of what happened, not as a test planned in advance."
    } else if has("edited") {
        "A finding that reads an edited record lists it as an integrity problem."
    } else if has("retracted") {
        "A retracted run stays on record and counts toward nothing."
    } else {
        ""
    };
    crate::obj! {"parts" => Value::List(parts), "affects" => affects}
}

/// The latest of the minutes a call's subject was written at, from the document: a criterion's declaration and its
/// observations' runs, an experiment's runs. "" where none is known.
fn recency(doc: &Dict, subject: &str, findings: &Value) -> String {
    let runs = doc.get("runs").and_then(Value::as_dict);
    let run_at = |id: &str| runs.and_then(|r| r.get(id)).map(|r| text(r, "at")).unwrap_or_default();
    let f = finding_in(findings, subject);
    let mut ats: Vec<String> = Vec::new();
    if !f.is_null() {
        ats.extend(list(f, "receipt_rows").iter().filter(|r| r.as_list().and_then(|l| l.first()).map(py_str).as_deref() == Some("declared")).filter_map(|r| r.as_list().and_then(|l| l.get(1)).map(py_str)));
        ats.extend(list(f, "observations").iter().map(|o| run_at(&text(o, "run"))));
    } else if let Some(Value::List(es)) = doc.get("experiments") {
        if let Some(e) = es.iter().find(|e| text(e, "id") == subject) {
            ats.extend(list(e, "run_ids").iter().map(|r| run_at(&py_str(r))));
        }
    }
    ats.into_iter().max().unwrap_or_default()
}

/// The first two of `names` and how many more: "e1 · e2 and 3 more".
/// `said` with each record it names by id named by its words instead (`words`: a criterion's statement, an
/// experiment's title, a question's text): a queue row names no id, which lives in the inspector (Look 3 item 2).
fn in_words(said: &str, words: &IndexMap<String, String>) -> String {
    let mut ids: Vec<(&String, &String)> = words.iter().filter(|(id, w)| !id.is_empty() && !w.is_empty()).collect();
    ids.sort_by(|a, b| b.0.len().cmp(&a.0.len()));
    // what joins an id to a word or a path around it: a full stop only where more of the word follows it, so an id that
    // ends a sentence is still the id, and a folder in a path ("out/e1/audit.py") is never one
    let part = |c: char| c.is_alphanumeric() || "_-@/\\".contains(c);
    let joins = |s: &str| {
        let mut cs = s.chars();
        match cs.next() {
            Some('.') => cs.next().map_or(false, char::is_alphanumeric),
            Some(c) => part(c),
            None => false,
        }
    };
    let mut out = String::new();
    let mut rest = said;
    'scan: while !rest.is_empty() {
        let before = out.chars().last();
        if before.map_or(true, |c| !part(c) && c != '.') {
            for (id, w) in &ids {
                if let Some(after) = rest.strip_prefix(id.as_str()) {
                    if !joins(after) {
                        out.push_str(w);
                        rest = after;
                        continue 'scan;
                    }
                }
            }
        }
        let c = rest.chars().next().unwrap_or_default();
        out.push(c);
        rest = &rest[c.len_utf8()..];
    }
    out
}

/// Up to two names joined by " · ", then how many more.
fn first_two(names: &[String]) -> String {
    let mut said = names.iter().take(2).cloned().collect::<Vec<_>>().join(" · ");
    if names.len() > 2 {
        said += &format!(" and {} more", names.len() - 2);
    }
    said
}

/// Needs your judgment: everything rb cannot settle and the user can, one row each with a verb phrase, one line of
/// numbers or names, the thing it opens and its button, and no command, hash or path; ranked by the fixed order
/// (OVERVIEW-V2.md): 1 a baseline or control that does not reproduce or meet its criterion, 2 every other call waiting
/// on the user, 3 the designs waiting for a confirmation (one row), 4 a proposed call or a past call mentioned, 5 what
/// rb found in the repository and the changed files nothing cites (a row each); ties by recency, newest first.
#[allow(clippy::too_many_arguments)]
fn queue_of(led: &Ledger, doc: &Dict, home: &Home, findings: &Value, pending: &[Value], experiments: &[Value]) -> Res<Vec<Value>> {
    let mut rows: Vec<(i64, String, Value)> = Vec::new();
    // each row also says what stands, for a reader, who is asked to do nothing: `reader_title` names no verb to act on
    let row = |kind: &str, (title, reader): (String, String), detail: String, target: String, targets: Vec<String>, action: &str, rank: i64| {
        crate::obj! {"kind" => kind, "title" => title, "reader_title" => reader, "detail" => detail, "target" => target, "targets" => Value::list_of_str(targets), "action" => action, "rank" => rank}
    };
    // a rule's state after what it is on: "...: rule not met", "...: not reproduced"
    let stands = |what: &str, f: &Value| -> String {
        let what = what.trim_end_matches('.');
        let state = rule_state(f);
        if state.is_empty() {
            return format!("{what}: still open");
        }
        let mut c = state.chars();
        let state = c.next().map(|x| x.to_lowercase().collect::<String>() + c.as_str()).unwrap_or_default();
        format!("{what}: {state}")
    };
    // what each subject is called on a screen: a criterion's statement, an experiment's title, a question's text
    let mut words: IndexMap<String, String> = IndexMap::new();
    if let Some(Value::Dict(fs)) = doc.get("findings") {
        for (id, f) in fs {
            words.insert(id.clone(), text(f, "statement"));
        }
    }
    for e in experiments {
        words.insert(text(e, "id"), text(e, "title"));
    }
    if let Some(Value::List(qs)) = doc.get("questions") {
        for q in qs {
            words.insert(text(q, "id"), text(q, "text"));
        }
    }
    let called = |s: &str| words.get(s).cloned().unwrap_or_else(|| s.to_string());
    for c in doc.get("calls").and_then(Value::as_list).map(|v| v.as_slice()).unwrap_or(&[]) {
        let subject = text(c, "subject");
        let f = finding_in(findings, &subject);
        let at = recency(doc, &subject, findings);
        let reason = c.get("reason").map(|r| text(r, "detail")).unwrap_or_default();
        let missed = matches!(text(f, "state").as_str(), "misses" | "not reproduced");
        if text(c, "kind") == "criterion" && missed && f.get("reads_baseline").map_or(false, Value::truthy) {
            // the arm by its plain name, as the grid and the cards say it (FIX4)
            let label = plain(&text(f, "subject_label"));
            let cited = text(f, "reference_label").starts_with("cited ");
            let short = text(f, "reading_short");
            let (title, detail) = if cited {
                // its number against the cited one as the reading's sentence says them (FIX4): the reading in the
                // sentence's digits, the cited number in the rule's own, each with its unit ("13.07% against 49.6%")
                let num = |k: &str| f.get("reading").and_then(|r| r.get(k)).and_then(Value::as_f64);
                let band = |k: &str| f.get("check_band").and_then(|b| b.get(k)).and_then(Value::as_f64);
                let against = match (num("estimate"), band("target")) {
                    (Some(e), Some(t)) => {
                        let mut xs = vec![e];
                        xs.extend(num("lo").zip(num("hi")).map(|(lo, hi)| [lo, hi]).into_iter().flatten());
                        let edges = match band("tolerance") {
                            Some(tol) if tol != 0.0 && f.get("check_band").map(|b| text(b, "comparator")).as_deref() == Some("within") => vec![t - tol, t + tol],
                            _ => vec![t],
                        };
                        let unit = f.get("reading").map(|r| text(r, "unit_text")).unwrap_or_default();
                        let with_unit = |x: String| if is_percent(&unit) { format!("{x}%") } else if unit.is_empty() { x } else { format!("{x} {unit}") };
                        format!(" ({} against {})", with_unit(decp(e, said_places(&xs, Some(t), &edges))), with_unit(crate::assess::declared(t)))
                    }
                    _ => String::new(),
                };
                ((format!("Review the {label} mismatch"), format!("The {label} doesn't match its cited number{against}")), short)
            } else {
                ((format!("Review the {label} result"), stands(&format!("The {label} result"), f)), format!("{} · {short}", rule_state(f)))
            };
            rows.push((1, at, row("baseline", title, detail, subject.clone(), vec![subject], "Review", 1)));
            continue;
        }
        let title = match (text(c, "code").as_str(), text(c, "kind").as_str()) {
            ("gate_missed", _) => (format!("Choose whether {} continues", called(&subject)), format!("Whether {} continues is not decided", called(&subject))),
            ("decision_outdated", _) => (format!("Revisit the earlier call: {}", called(&subject)), format!("The earlier call on {} was made on an earlier reading", called(&subject))),
            ("investigating", _) => (format!("Review the investigation: {}", called(&subject)), format!("{} is under investigation", called(&subject))),
            // a rule taken back leaves the document's findings: it is said by its own statement, never its id
            ("retracted_by_agent", _) => {
                let said = led.load::<Criterion>(&subject).ok().map(|c| c.statement).filter(|s| !s.is_empty()).unwrap_or_else(|| called(&subject));
                let said = said.trim_end_matches('.');
                (format!("Review the rule taken back: {said}"), format!("The rule \"{said}\" was taken back, and no call is made on that yet"))
            }
            ("call_on_failed_check", _) => (format!("{}: {}", text(c, "label"), called(&subject)), format!("A call on {} was made while its own check fails", called(&subject))),
            (_, "criterion") => (format!("Review: {}", called(&subject)), stands(&called(&subject), f)),
            _ => (format!("{}: {}", text(c, "label"), called(&subject)), stands(&called(&subject), f)),
        };
        // the earlier call by its line, as made_calls says it ("continued 2026-10-21: one more seed")
        let earlier = || doc.get("made_calls").and_then(Value::as_list).and_then(|m| m.iter().rev().find(|m| text(m, "subject") == subject)).map(|m| text(m, "line")).filter(|l| !l.is_empty());
        let detail = match () {
            _ if !f.is_null() => format!("{} · {}", rule_state(f), text(f, "reading_short")),
            _ if text(c, "code") == "gate_missed" => gates_missed(findings, &subject).unwrap_or_else(|| in_words(&reason, &words)),
            _ if text(c, "code") == "decision_outdated" => earlier().unwrap_or_else(|| in_words(&reason, &words)),
            _ => in_words(&reason, &words),
        };
        rows.push((2, at, row("call", title, detail, subject.clone(), vec![subject], "Review", 2)));
    }
    let designs: Vec<String> = pending.iter().filter(|p| text(p, "kind") == "design").map(design_of).collect();
    if !designs.is_empty() {
        let names: Vec<String> = designs.iter().map(|d| experiments.iter().find(|e| text(e, "id") == *d).map(|e| text(e, "short_title")).filter(|t| !t.is_empty()).unwrap_or_else(|| called(d))).collect();
        // one design is named in its title by its words, never its id, and not said again below it
        let (title, detail) = if designs.len() == 1 {
            ((format!("Confirm the design of {}", names[0]), format!("The design of {} is not confirmed", names[0])), String::new())
        } else {
            ((format!("Confirm {} experiment designs", designs.len()), format!("The designs of {} experiments are not confirmed", designs.len())), first_two(&names))
        };
        rows.push((3, String::new(), row("design", title, detail, designs[0].clone(), designs.clone(), "Review", 3)));
    }
    for p in pending.iter().filter(|p| matches!(text(p, "kind").as_str(), "proposed" | "past" | "gate")) {
        let id = text(p, "id");
        let subject = text(p, "subject");
        let (title, detail, at) = match text(p, "kind").as_str() {
            "gate" => {
                let (title, detail) = gate_row(p, &called);
                ((title, format!("The gate on {} is not confirmed", called(&design_of(p)))), detail, String::new())
            }
            kind => {
                let d = led.load::<Decision>(&id).ok();
                let action = d.as_ref().map(|d| d.action.clone()).unwrap_or_default();
                let at = d.as_ref().map(|d| minute(&d.at)).unwrap_or_default();
                // the subject by its words in the title, never its id, and not said again below it
                if kind == "past" {
                    let when = d.as_ref().and_then(|d| d.past.as_ref()).map(crate::ledger::past_when).unwrap_or_default();
                    ((format!("Confirm the past call to {action}: {}", called(&subject)), format!("The past call to {action} is not confirmed: {}", called(&subject))), when, at)
                } else {
                    ((format!("Confirm the proposed call to {action}: {}", called(&subject)), format!("The proposed call to {action} is not confirmed: {}", called(&subject))), String::new(), at)
                }
            }
        };
        rows.push((4, at, row("pending_call", title, detail, id.clone(), vec![id], "Review", 4)));
    }
    if !home.found.is_empty() {
        let n = home.found.len();
        let title = if n == 1 {
            ("Record a result found in the repository".to_string(), "A result found in the repository is not recorded".to_string())
        } else {
            (format!("Record {n} results found in the repository"), format!("{n} results found in the repository are not recorded"))
        };
        let mut exps: Vec<String> = Vec::new();
        for a in &home.found {
            if let [run, add, e, ..] = a.argv.as_slice() {
                if run == "run" && add == "add" && !exps.contains(e) {
                    exps.push(e.clone());
                }
            }
        }
        let names: Vec<String> = exps.iter().map(|e| experiments.iter().find(|x| text(x, "id") == *e).map(|x| text(x, "short_title")).filter(|t| !t.is_empty()).unwrap_or_else(|| called(e))).collect();
        let detail = if names.is_empty() { String::new() } else { format!("for {}", first_two(&names)) };
        let at = home.found.iter().map(|a| minute(&a.asked_at)).max().unwrap_or_default();
        let ids: Vec<String> = home.found.iter().map(|a| a.request_id.clone()).collect();
        rows.push((5, at, row("found", title, detail, ids[0].clone(), ids, "Inspect", 5)));
    }
    if home.uncited > 0 {
        let n = home.uncited;
        // each file by its name alone: the Overview holds no folder path, commit or value
        let names: Vec<String> = home.uncited_paths.iter().map(|(p, _)| p.rsplit('/').next().unwrap_or("").to_string()).filter(|x| !x.is_empty()).collect();
        let title = match (n, names.as_slice()) {
            (1, [name]) => ("Inspect a changed file nothing in the record cites".to_string(), format!("A changed file nothing in the record cites: {name}")),
            (1, _) => ("Inspect a changed file nothing in the record cites".to_string(), "A changed file nothing in the record cites".to_string()),
            _ => (format!("Inspect {n} changed files nothing in the record cites"), format!("{n} changed files nothing in the record cites")),
        };
        let at = home.uncited_paths.iter().map(|(_, at)| minute(at)).max().unwrap_or_default();
        rows.push((5, at, row("uncited", title, first_two(&names), String::new(), Vec::new(), "Inspect", 5)));
    }
    // the fixed order, then the newest first within it; a stable sort keeps rb status's order where both tie
    rows.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| b.1.cmp(&a.1)));
    Ok(rows.into_iter().map(|(_, _, v)| v).collect())
}

/// Every experiment of the record once, as the briefs hold it, a question's and work filed under none alike.
fn experiments_of(h: &Home) -> Vec<&ExperimentBrief> {
    let mut seen = std::collections::HashSet::new();
    h.briefs.iter().flat_map(|b| &b.experiments).filter(|e| seen.insert(e.id.clone())).collect()
}

/// Every criterion's line once: its experiment's, which holds the finding whole; a question's own (read in that
/// inquiry's context) only for one no experiment's brief holds.
fn findings_value(led: &Ledger, h: &Home, st: &Value, g: &Graph) -> Res<Value> {
    let mut lines: IndexMap<String, &FindingLine> = IndexMap::new();
    for f in experiments_of(h).into_iter().flat_map(|e| &e.findings).chain(h.briefs.iter().flat_map(|b| &b.inquiry_findings)) {
        lines.entry(f.criterion_id.clone()).or_insert(f);
    }
    let of: IndexMap<String, &Value> = list(st, "findings").iter().map(|f| (text(f, "criterion"), f)).collect();
    let mut out = Dict::new();
    for (id, f) in lines {
        let graphed = match g.finding(&id) {
            Some((c, found)) => finding_graph(led, h, c, found)?,
            None => Dict::new(),
        };
        let found = of.get(&id).copied().unwrap_or(&Value::Null);
        let units = found.get("units").and_then(|u| u.get("n")).and_then(Value::as_i64).unwrap_or(0);
        let row = f.row.clone().unwrap_or_default();
        let row = crate::obj! {
            "metric" => row.metric, "value" => row.value, "criterion" => row.criterion, "margin" => row.margin, "runs" => units,
            "standing" => row.standing, "why" => row.why, "good" => row.good, "negative" => row.negative
        };
        let fields = match g.finding(&id) {
            Some((c, found)) => finding_fields(led, c, found)?,
            None => Dict::new(),
        };
        let mut v = crate::obj! {
            "criterion_id" => id.clone(), "experiment" => text(found, "experiment"), "statement" => f.statement.clone(), "role" => f.role.clone(),
            "standing" => f.standing.clone(), "good" => f.good, "rule" => f.rule.clone(), "sentence" => f.sentence.clone(),
            "numbers" => f.numbers.clone(), "row" => row, "says" => f.says.clone(), "your_words" => f.your_words.clone(),
            "observed" => f.observed.clone(), "observed_short" => f.observed_short.clone(),
            "observed_places" => Value::List(f.observed_places.iter().map(|(id, label)| crate::obj! {"id" => id.clone(), "label" => label.clone()}).collect()),
            "other_code" => f.other_code.clone()
        };
        if let Value::Dict(d) = &mut v {
            d.extend(fields);
            d.extend(graphed);
            // the row's rule and margin as the card says them (FIX4), in place of rb status's knob words
            if let Some(Value::Dict(words)) = d.shift_remove("row_words") {
                if let Some(Value::Dict(row)) = d.get_mut("row") {
                    row.extend(words);
                }
            }
        }
        out.insert(id, v);
    }
    Ok(Value::Dict(out))
}

/// A finding's own fields (BP 1b-1): its reading in parts, each as rb's comparison text says it, with rb's result word
/// (`reading`); its rule's band, for the figure (`check_band`); the units it rests on in plain words (`units_line`);
/// on a miss, what rb's rule sentence says after its rule word (`rule_detail`); the unit amended after the lock, where
/// that changes how its runs pair (`unit_amended`); its receipt as rows of field, words and the ids they name, with no
/// writer (`receipt_rows`); and a cited number's source (`cited`).
fn finding_fields(led: &Ledger, c: &Criterion, f: &Finding) -> Res<Dict> {
    let exp: Experiment = led.load(&c.experiment)?;
    let (subject, reference, metric, _) = led.arms(&exp, c)?;
    let entry = led.catalogue_entry(&metric)?;
    let unit = entry.as_ref().map(|m| m.unit.clone()).unwrap_or_default();
    let direction = entry.as_ref().map(|m| m.direction.clone()).unwrap_or_else(|| "none".into());
    let parts = f.comparison.as_ref().and_then(|r| crate::assess::reading_parts(Some(r), &unit, &crate::assess::rule_numbers(c, r, &direction)));
    let num = |x: Option<f64>| x.map_or(Value::Null, Value::Float);
    let r = f.comparison.as_ref();
    let p = parts.clone().unwrap_or_default();
    let reading = crate::obj! {
        "estimate" => num(r.and_then(|r| r.estimate)), "lo" => num(r.and_then(|r| r.lo)), "hi" => num(r.and_then(|r| r.hi)),
        "level" => num(r.and_then(|r| r.level)),
        "method" => r.map(|r| r.method.clone()).unwrap_or_default(), "n" => r.and_then(|r| r.n).unwrap_or(0), "unit" => r.map(|r| r.unit.clone()).unwrap_or_default(),
        "estimate_text" => p.estimate, "interval_text" => p.interval, "unit_text" => if parts.is_some() { unit.clone() } else { String::new() },
        "result" => crate::assess::result_word(&f.result),
        // the unit as a sentence says it (PLAIN-WORDS-PASS-4): a difference of two percentages is in points, a
        // percentage on its own is "%"; unit_text stays rb's own
        "unit_said" => if parts.is_none() || c.metric.starts_with("ratio.") { String::new() } else if is_percent(&unit) { if reference.is_empty() { "%".into() } else { "points".into() } } else { unit.clone() }
    };
    let (comparator, target, tolerance) = match c.comparator.as_str() {
        "at_least" => ("at least", c.target.unwrap_or(0.0), None),
        "at_most" => ("at most", c.target.unwrap_or(0.0), None),
        "equals" => ("within", c.target.unwrap_or(0.0), c.tolerance),
        "equivalent" => ("within", 0.0, c.margin),
        // the worse bound inside the margin, and the interval clear of 0, on the side the metric's direction makes worse
        "noninferior" | "better" => {
            let edge = if c.comparator == "better" { 0.0 } else { c.margin.unwrap_or(0.0) };
            match direction.as_str() {
                "maximize" => ("at least", -edge, None),
                "minimize" => ("at most", edge, None),
                _ => (c.comparator.as_str(), edge, None),
            }
        }
        other => (other, 0.0, None),
    };
    let check_band = crate::obj! {"comparator" => comparator, "target" => target, "tolerance" => num(tolerance)};
    let unit_amended = match unit_amendment(led, &exp, c)? {
        // the readings it changes: those that read runs
        Some(a) if !f.observations.is_empty() || f.comparison.as_ref().map_or(false, |r| r.source == "runs") => {
            crate::obj! {"at" => a.at.clone(), "setting" => "population.unit", "was" => a.was.clone(), "now" => a.now.clone(), "amendment" => a.id.clone(),
                "line" => format!("{}, {}", a.line, a.at), "why" => a.why.clone()}
        }
        _ => Value::Null,
    };
    let miss = matches!(f.rule_outcome.as_str(), "misses" | "undetermined");
    let rule_detail = if miss { crate::ledger::views::rule_why(f) } else { String::new() };
    let amendments = amendments_of(led, &exp)?;
    let mut rows: Vec<Value> = Vec::new();
    let row = |field: &str, words: String, refs: Vec<String>| Value::List(vec![field.into(), words.into(), Value::list_of_str(refs)]);
    rows.push(row("declared", minute(&c.created_at), Vec::new()));
    if let Some(fz) = &exp.frozen {
        let added = amendments.iter().find(|a| a.changes.iter().any(|(k, was, now)| k == "criteria" && was.is_empty() && *now == c.id));
        match added {
            Some(a) => rows.push(row("locked", format!("{}, with amendment {}", a.at, a.id), vec![a.id.clone()])),
            None if c.created_at <= fz.at => rows.push(row("locked", format!("{}, with {}", minute(&fz.at), exp.id), vec![exp.id.clone()])),
            None => {}
        }
    }
    if let Value::Dict(u) = &unit_amended {
        let said = |k: &str| u.get(k).map(py_str).unwrap_or_default();
        rows.push(row("amended", format!("{}: unit {} -> {}", said("at"), said("was"), said("now")), vec![said("amendment")]));
    }
    // a cited or inherited criterion says so; one declared here does not
    let origin = if matches!(c.origin.as_str(), "cited" | "inherited") { format!(" · {}", c.origin) } else { String::new() };
    rows.push(row("experiment", format!("{} · {}{origin}", exp.id, c.role), vec![exp.id.clone()]));
    rows.push(row("metric", if reference.is_empty() { c.reads() } else { format!("{}: {subject} against {reference}", c.reads()) }, Vec::new()));
    let source = c.source.as_ref().filter(|s| s.path.is_some());
    if source.is_some() {
        if let Some(t) = c.target {
            let tol = c.tolerance.map(|x| format!(" ± {}", crate::assess::declared(x))).unwrap_or_default();
            rows.push(row("reference", format!("{}{tol}", crate::assess::declared(t)), Vec::new()));
        }
    }
    if let Some(r) = r {
        let how = if r.lo.is_some() { format!("{}% interval", crate::assess::g(crate::pyfloat::round0(r.level.unwrap_or(0.95) * 100.0))) } else { "no interval".into() };
        let refs = if led.kind_of(&r.source)? == Some("analysis") { vec![r.source.clone()] } else { Vec::new() };
        let method = if r.method.is_empty() { how } else { format!("{} · {how}", r.method) };
        rows.push(row("method", method, refs));
    }
    let cited = match source {
        Some(s) => {
            let res = s.resolved.as_ref();
            let path = s.path.clone().unwrap_or_default();
            let line = res.and_then(|x| x.line).or(s.line);
            let commit = res.and_then(|x| x.commit.clone()).or_else(|| s.commit.clone()).unwrap_or_default();
            let sha = res.map(|x| x.sha256.clone()).unwrap_or_default();
            let quote = s.quote.clone().unwrap_or_default();
            let mut said = match line {
                Some(l) => format!("{path}:{l}"),
                None => path.clone(),
            };
            if !commit.is_empty() {
                said += &format!(" · read at commit {}", commit.chars().take(7).collect::<String>());
            }
            if !sha.is_empty() {
                said += &format!(" · sha256 {}", sha.chars().take(12).collect::<String>());
            }
            rows.push(row("source", said, Vec::new()));
            crate::obj! {"path" => path, "line" => line.unwrap_or(0), "locator" => s.locator.clone().unwrap_or_default(), "quote" => quote, "commit" => commit, "sha256" => sha}
        }
        None => Value::Null,
    };
    for l in c.question_links.iter().filter(|l| l.retracted.is_none()) {
        rows.push(row("question link", format!("{} · {}", l.question, minute(&l.declared_at)), vec![l.question.clone()]));
    }
    // the compact row's short fields and the arms' labels (contract 1.6): a baseline or control is labelled as one
    let state = crate::assess::rule_word(&f.rule_outcome, &c.role, &c.origin);
    let subject_label = arm_label(&exp, &subject);
    let cited_target = c.target.filter(|_| c.origin == "cited" && reference.is_empty());
    let with_unit = |x: &str| if unit.is_empty() { x.to_string() } else { format!("{x} {unit}") };
    let reference_label = match cited_target {
        _ if !reference.is_empty() => arm_label(&exp, &reference),
        Some(t) => format!("cited {}", with_unit(&crate::assess::declared(t))),
        None => String::new(),
    };
    // one plain sentence (DESIGN-PASS-1 P5, P8): 2 decimals, an interval as "lo to hi", a difference's direction in
    // words, once; rb's own numbers stay in the reading's parts and the receipt
    // a variant's name in a sentence says its underscores as spaces ("step_by_step" reads "step by step"); the label
    // fields keep it as written
    // a unit that is the metric's own name ("top1" for top1) is none in a sentence, as the grid and the cards say it
    let unit = sentence_unit(&unit, &metric);
    let with_unit = |x: &str| if unit.is_empty() { x.to_string() } else { format!("{x} {unit}") };
    let level_unit = |x: &str| if is_percent(&unit) { format!("{x}%") } else { with_unit(x) };
    let cells = r.map_or(false, |r| r.method.contains("design cells"));
    // a level with no arm named is said by its metric's own name, never as an empty label (Look 3, also seen)
    let said_plain = if subject_label.is_empty() { metric_said(&metric) } else { plain(&subject_label) };
    let edges = rule_edges(&c.comparator, c.target, c.tolerance, c.margin);
    let reading_short = match r.and_then(|r| r.estimate.map(|e| (e, r.lo.zip(r.hi)))) {
        Some((e, iv)) => {
            // 2 decimals, more where 2 would round a number onto or past the rule's own (the rule's digits)
            let mut xs = vec![e];
            xs.extend(iv.map(|(lo, hi)| [lo, hi]).into_iter().flatten());
            // and apart from each other and from the rule's number, as the verdict says them
            let p = said_places(&xs, c.target, &edges);
            // the interval in brackets after its number, as every view says it (FIX4): a difference's bounds in the
            // points or units just said, a level's each with its unit
            match cited_target {
                _ if !reference.is_empty() => {
                    let between = iv.map(|(lo, hi)| format!(" ({} to {})", signedp(lo, p), signedp(hi, p))).unwrap_or_default();
                    // a mean across fixed design cells says so, since one cell can miss a rule the mean meets
                    let across = if cells { " on average across its design cells" } else { "" };
                    format!("{}{across}{between}", differs_by(&plain(&subject_label), &plain(&the(&exp, &reference)), e, &unit, p))
                }
                _ => {
                    let between = iv.map(|(lo, hi)| format!(" ({} to {})", level_unit(&decp(lo, p)), level_unit(&decp(hi, p)))).unwrap_or_default();
                    // a cited level says its interval too, as the list's sentence does, then the cited number in the
                    // rule's own digits
                    let cited = cited_target.map(|t| format!("; the cited number is {}", level_unit(&crate::assess::declared(t)))).unwrap_or_default();
                    format!("{} is {}{between}{cited}", said_plain, level_unit(&decp(e, p)))
                }
            }
        }
        None => "no reading yet".to_string(),
    };
    let problem = f.integrity.first().map(|p| {
        let first = crate::surface::reason(&p.code).map(str::to_string).unwrap_or_else(|| p.code.replace('_', " "));
        if f.integrity.len() > 1 { format!("{first} and {} more", f.integrity.len() - 1) } else { first }
    });
    let verdict = verdict(&Verdict {
        state: &state,
        rule_said: f.says.get("rule").map(String::as_str).unwrap_or(""),
        why: &crate::ledger::views::rule_why(f),
        result: &f.result,
        subject: &if subject.is_empty() { metric_said(&metric) } else { plain(&subject) },
        reference: &plain(&reference),
        estimate: r.and_then(|r| r.estimate),
        interval: r.and_then(|r| r.lo.zip(r.hi)),
        unit: &unit,
        cited: c.origin == "cited",
        comparator: &c.comparator,
        over: &c.over,
        target: c.target,
        tolerance: c.tolerance,
        margin: c.margin,
        direction: &direction,
        ratio: c.metric.starts_with("ratio.") || metric.starts_with("ratio."),
        cells,
        missed: f.missed_units.len(),
        n: f.units.n,
        problem: problem.as_deref().unwrap_or(""),
    });
    // the compact row's rule and margin in the card's words and digits (FIX4): "≥ 5 points" and "margin -1.40 points"
    // beside a reading of 3.60 points, "≥ 75.5%" for a level's
    let ratio = c.metric.starts_with("ratio.") || metric.starts_with("ratio.");
    let pct = !ratio && is_percent(&unit);
    let level = reference.is_empty();
    let unit_after = |n: &str| match () {
        _ if ratio => String::new(),
        _ if pct && level => "%".to_string(),
        _ if pct => format!(" {}", if n.trim_start_matches(['-', '+', '\u{b1}']) == "1" { "point" } else { "points" }),
        _ if unit.is_empty() => String::new(),
        _ => format!(" {unit}"),
    };
    let rule_said = crate::surface::criterion_text(&c.comparator, c.target, c.tolerance, "", c.margin, &c.over);
    let rule_said = match (c.comparator.as_str(), c.target, c.tolerance) {
        _ if rule_said == "better" => rule_said,
        // a band around a level's percentage: the % on its number, the band in points, as the verdict says it
        // ("= 76.1% ± 0.05 points"), never a tolerance that reads as relative ("= 76.1 ± 0.05%")
        ("equals", Some(t), Some(tol)) if pct && level => {
            let lead = rule_said.split("= ").next().unwrap_or("");
            let band = crate::assess::declared(tol);
            format!("{lead}= {}% \u{b1} {band} {}", crate::assess::declared(t), if band == "1" { "point" } else { "points" })
        }
        _ => format!("{rule_said}{}", unit_after(rule_said.rsplit(' ').next().unwrap_or(""))),
    };
    let margin_said = match (r.and_then(|r| r.estimate), c.target) {
        (Some(e), Some(t)) if matches!(c.comparator.as_str(), "at_most" | "at_least" | "equals") && c.over == "mean" => {
            let m = match c.comparator.as_str() {
                "at_most" => t - e,
                "at_least" => e - t,
                _ => c.tolerance.unwrap_or(0.0) - (e - t).abs(),
            };
            let m = if m.abs() <= crate::assess::close(e, t) { 0.0 } else { m };
            // in the digits the verdict says the reading in, and the unit it says a distance in (points for a percentage)
            let mut xs = vec![e];
            xs.extend(r.and_then(|r| r.lo.zip(r.hi)).map(|(lo, hi)| [lo, hi]).into_iter().flatten());
            let said = decp(m, said_places(&xs, Some(t), &edges));
            let u = match () {
                _ if ratio => String::new(),
                _ if pct => format!(" {}", if said.trim_start_matches('-') == "1.00" { "point" } else { "points" }),
                _ if unit.is_empty() => String::new(),
                _ => format!(" {unit}"),
            };
            format!("margin {said}{u}")
        }
        _ => String::new(),
    };
    let mut tags: Vec<&str> = Vec::new();
    if f.rule_outcome == "untested" {
        tags.push("criterion_untested");
    }
    if matches!(f.timing.as_str(), "after_evidence" | "after_exposure") {
        tags.push("retrospective");
    }
    if matches!(f.result.as_str(), "unresolved" | "point_only") {
        tags.push("uncertainty_remains");
    }
    tags.truncate(2);
    let role_of = |arm: &str| exp.variant(arm).map(|v| v.role.clone()).unwrap_or_default();
    Ok(crate::dict! {
        "reading" => reading, "check_band" => check_band, "units_line" => units_line(led, f)?, "rule_detail" => rule_detail,
        "unit_amended" => unit_amended, "receipt_rows" => Value::List(rows), "cited" => cited,
        "state" => state.clone(), "state_label" => state_label(&state), "subject_label" => subject_label, "reference_label" => reference_label,
        "reading_short" => reading_short, "verdict" => verdict, "tags" => Value::list_of_str(tags.into_iter().map(str::to_string)),
        "reads_baseline" => reference.is_empty() && matches!(role_of(&subject).as_str(), "baseline" | "control"),
        "row_words" => crate::obj! {"criterion" => rule_said, "margin" => margin_said}
    })
}

/// An arm as a screen names it, a baseline, control or ablation labelled as one: "fp32 baseline", "int8".
fn arm_label(exp: &Experiment, arm: &str) -> String {
    match exp.variant(arm).map(|v| v.role.as_str()) {
        Some(role @ ("baseline" | "control" | "ablation")) => format!("{arm} {role}"),
        _ => arm.to_string(),
    }
}

/// A threshold rule over a mean as a page says it, its metric by its title and its number with its unit, as the card's
/// figure says the rule (Look 8): "ONNX top-1 75.5% or more", "Top-1, INT8 against the FP32 baseline: -1 point or more".
/// None for any other rule, or one with a scope or an estimand, which keeps rb's words.
fn rule_on_page(led: &Ledger, c: &Criterion) -> Res<Option<String>> {
    // a rule with a scope or an estimand (--where, --subject-where, --reference-where, pooled) keeps rb's words, which say it
    let scoped = !c.estimand.is_empty() || !c.r#where.is_empty() || !c.subject_where.is_empty() || !c.reference_where.is_empty();
    let Some(t) = c.target.filter(|_| !scoped && c.over == "mean" && matches!(c.comparator.as_str(), "at_least" | "at_most" | "equals")) else { return Ok(None) };
    let exp: Experiment = led.load(&c.experiment)?;
    let (subject, reference, metric, _) = led.arms(&exp, c)?;
    let unit = sentence_unit(&led.catalogue_entry(&metric)?.map(|m| m.unit).unwrap_or_default(), &metric);
    let level = reference.is_empty();
    let said = |x: f64| {
        let n = crate::assess::declared(x);
        match () {
            _ if is_percent(&unit) && level => format!("{n}%"),
            _ if is_percent(&unit) => format!("{n} {}", if n.trim_start_matches(['-', '+']) == "1" { "point" } else { "points" }),
            _ if unit.is_empty() => n,
            _ => format!("{n} {unit}"),
        }
    };
    let bound = match c.comparator.as_str() {
        "at_least" => format!("{} or more", said(t)),
        "at_most" => format!("{} or less", said(t)),
        _ => match c.tolerance {
            Some(tol) => format!("{} \u{b1} {}", said(t), if level && is_percent(&unit) { said(tol).trim_end_matches('%').to_string() + " points" } else { said(tol) }),
            None => return Ok(None),
        },
    };
    let title = metric_said(&metric);
    let subject_label = arm_label(&exp, &subject);
    Ok(Some(match () {
        _ if !level => format!("{title}, {} against {}: {bound}", plain(&subject_label), plain(&the(&exp, &reference))),
        _ if subject_label.is_empty() || metric == c.reads() => format!("{title} {bound}"),
        _ => format!("{title}, {} {bound}", plain(&subject_label)),
    }))
}

/// An arm named after a word such as "against": "the fp32 baseline", "int8".
fn the(exp: &Experiment, arm: &str) -> String {
    let label = arm_label(exp, arm);
    if label == arm { label } else { format!("the {label}") }
}

/// A number as a sentence says it (DESIGN-PASS-1 P5): 2 decimals; one smaller than 0.01 by its first 2 significant
/// figures, so it never reads as 0; never "-0".
fn dec2(x: f64) -> String {
    let a = x.abs();
    let places = if a == 0.0 || a >= 0.01 { 2 } else { (2 - a.log10().floor() as i64 - 1).clamp(2, 12) as usize };
    let said = format!("{x:.places$}");
    if said.trim_start_matches('-').chars().all(|c| c == '0' || c == '.') { format!("{:.places$}", 0.0) } else { said }
}

/// `x` as dec2 says it, with `p` decimals at least.
fn decp(x: f64, p: usize) -> String {
    let a = x.abs();
    let own = if a == 0.0 || a >= 0.01 { 2 } else { (2 - a.log10().floor() as i64 - 1).clamp(2, 12) as usize };
    let places = own.max(p);
    let said = format!("{x:.places$}");
    if said.trim_start_matches('-').chars().all(|c| c == '0' || c == '.') { format!("{:.places$}", 0.0) } else { said }
}

/// The fewest decimals, 2 to 6, at which numbers that differ read apart.
fn apart(xs: &[f64]) -> usize {
    (2..=6)
        .find(|&p| {
            xs.iter().enumerate().all(|(i, a)| xs[i + 1..].iter().all(|b| (a - b).abs() <= crate::assess::close(*a, *b) || decp(*a, p) != decp(*b, p)))
        })
        .unwrap_or(6)
}

/// The edges a rule's numbers make, where a reading's number turns from one side of the rule to the other: a
/// threshold, a band's two ends, a margin either side of 0, 0 for a rule that asks for a side.
fn rule_edges(comparator: &str, target: Option<f64>, tolerance: Option<f64>, margin: Option<f64>) -> Vec<f64> {
    match comparator {
        "equals" => target.map(|t| { let tol = tolerance.unwrap_or(0.0); if tol == 0.0 { vec![t] } else { vec![t - tol, t + tol] } }).unwrap_or_default(),
        "at_least" | "at_most" => target.into_iter().collect(),
        "noninferior" | "equivalent" => margin.map(|m| vec![-m.abs(), m.abs()]).unwrap_or_default(),
        "better" => vec![0.0],
        _ => Vec::new(),
    }
}

/// The decimals, `from` at least, at which each of `xs` is said on its own side of every edge of its rule (FIX2, R's
/// request 126): never rounded onto or past it ("0.05000004" over a rule of at most 0.05, never "0.05"); a number on an
/// edge (within `close`) is said as it is.
fn rule_places(xs: &[f64], edges: &[f64], from: usize) -> usize {
    let keeps = |p: usize| {
        xs.iter().all(|&x| {
            let said: f64 = decp(x, p).parse().unwrap_or(x);
            edges.iter().all(|&m| (x - m).abs() <= crate::assess::close(x, m) || (said - m).signum() == (x - m).signum() && said != m)
        })
    };
    (from..=17).find(|&p| keeps(p)).unwrap_or(17)
}

/// `x` with `p` decimals, its sign always said: "+0.05000004".
fn signedp(x: f64, p: usize) -> String {
    let said = decp(x, p);
    if said.starts_with('-') || said.trim_start_matches(['0', '.']).is_empty() { said } else { format!("+{said}") }
}


/// A difference `e` of `subject` against `reference` in words, its direction once (P5): "int8 is 0.08 points lower than
/// the fp32 baseline"; a difference in % is in points.
/// Its size with `p` decimals at least.
fn differs_by(subject: &str, reference: &str, e: f64, unit: &str, p: usize) -> String {
    let amount = decp(e.abs(), p);
    if amount.trim_start_matches(['0', '.']).is_empty() {
        // an estimate of 0 says its size and no direction, never that the two match
        return format!("{subject} is 0.00{} away from {reference}", if is_percent(unit) { " points".to_string() } else if unit.is_empty() { String::new() } else { format!(" {unit}") });
    }
    let unit = match unit {
        u if is_percent(u) => " points".to_string(),
        "" => String::new(),
        u => format!(" {u}"),
    };
    format!("{subject} is {amount}{unit} {} than {reference}", if e < 0.0 { "lower" } else { "higher" })
}

/// Whether a metric's unit is a percentage, so a difference of two of its numbers is in points (PLAIN-WORDS-PASS-4).
fn is_percent(unit: &str) -> bool {
    matches!(unit.trim().to_ascii_lowercase().as_str(), "%" | "percent" | "per cent" | "pct" | "percentage" | "percentages")
}

/// A variant's name as a sentence says it, as the grid and the cards do (FIX4): a numeric format (fp32, int8, bf16)
/// in capitals, its underscores as spaces ("step_by_step" reads "step by step", "fp32 baseline" "FP32 baseline").
fn plain(arm: &str) -> String {
    let format = |w: &str| {
        let (head, digits) = w.split_at(w.find(|c: char| c.is_ascii_digit()).unwrap_or(w.len()));
        if ["fp", "int", "uint", "bf", "tf"].contains(&head) && !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit()) { w.to_uppercase() } else { w.to_string() }
    };
    arm.split(' ').map(format).collect::<Vec<_>>().join(" ").replace('_', " ")
}

/// A metric as a sentence names it where no arm is given (FIX4): by its title, as the grid and the cards say it, never
/// its key: "top1" reads "Top-1"; an acronym keeps its case (FIX6), by nodes.rs's rule and word list, so every view
/// says one title: "onnx_top1" reads "ONNX top-1", "int8_top1" "INT8 top-1".
fn metric_said(name: &str) -> String {
    let topn = |t: &str| t.strip_prefix("top").filter(|d| !d.is_empty() && d.chars().all(|c| c.is_ascii_digit())).map(|d| format!("top-{d}"));
    let word = |w: &str| {
        let low = w.to_lowercase();
        if let Some(t) = topn(&low) {
            return t;
        }
        // a format code (letters, then digits: fp32, int8, bf16) in capitals, as a variant's name reads
        let letters = low.trim_end_matches(|c: char| c.is_ascii_digit());
        let format = !letters.is_empty() && letters.len() < low.len() && letters.chars().all(|c| c.is_ascii_lowercase());
        if format || ACRONYMS.contains(&low.as_str()) { low.to_uppercase() } else { w.to_string() }
    };
    let words = name.replace(['.', '_'], " ").split(' ').filter(|w| !w.is_empty()).map(word).collect::<Vec<_>>().join(" ");
    let mut c = words.chars();
    c.next().map_or(String::new(), |h| h.to_uppercase().chain(c).collect())
}

/// The words a metric's name holds that prose writes in capitals (FIX6): nodes.rs's list (C, FIX5), word for word.
const ACRONYMS: &[&str] = &[
    "onnx", "gpu", "cpu", "tpu", "auc", "roc", "mse", "rmse", "mae", "iou", "bleu", "rouge", "wer", "cer", "ece", "fid", "psnr", "ssim", "nll", "kl", "ema",
    "mfu", "flops", "f1", "ndcg", "mrr", "llm", "ttft", "qps", "rps",
];

/// A metric's unit as a sentence says it (FIX4): none where the catalogue gives the metric's own name as its unit
/// ("top1" for top1), as the grid and the cards drop it; else the unit as recorded.
fn sentence_unit(unit: &str, metric: &str) -> String {
    let name = metric.rsplit('.').next().unwrap_or(metric);
    if unit == name || unit == metric { String::new() } else { unit.to_string() }
}

/// The decimals a reading's numbers are said with beside its rule's number (FIX4): 2 at least, apart from each other
/// and from the rule's number, and never rounded onto an edge of the rule; the reading's short sentence and the
/// queue's row say them alike.
fn said_places(xs: &[f64], target: Option<f64>, edges: &[f64]) -> usize {
    let mut ys = xs.to_vec();
    ys.extend(target);
    rule_places(xs, edges, apart(&ys))
}

/// What a finding's verdict sentence is made of (`verdict`).
struct Verdict<'a> {
    /// rb's rule word (assess::rule_word): meets, misses, undetermined, untested, reproduced, gate missed, ...
    state: &'a str,
    /// the finding's says.rule, which says when a met rule rests on its estimate alone
    rule_said: &'a str,
    /// on a rule rb can't judge, why, as rb's rule sentence says it after its word
    why: &'a str,
    result: &'a str,
    subject: &'a str,
    /// "" for a level
    reference: &'a str,
    estimate: Option<f64>,
    interval: Option<(f64, f64)>,
    unit: &'a str,
    cited: bool,
    comparator: &'a str,
    over: &'a str,
    target: Option<f64>,
    tolerance: Option<f64>,
    margin: Option<f64>,
    direction: &'a str,
    ratio: bool,
    cells: bool,
    missed: usize,
    n: i64,
    /// the first problem with what it rests on, as its shared phrase; "" for none
    problem: &'a str,
}

/// A finding's verdict, one sentence said once on its card (PLAIN-WORDS-PASS-4): the status word first ("Met", "Not
/// met", "Not reproduced", "Can't tell yet", "Not tested"), then the reading in plain words, then rb's result class in
/// words and, where the rule has a threshold, how far the reading is from it. "Not met: step by step is 3.60 points
/// above plain (+0.62 to +6.58). A real gain, but 1.40 points short of the 5 points the rule asks for."
fn verdict(v: &Verdict) -> String {
    let status = match v.state {
        "meets" if v.rule_said.starts_with("estimate meets") => "Met on its estimate",
        "meets" if v.rule_said.starts_with("recorded values meet") => "Met on the recorded values",
        "meets" => "Met",
        "misses" => "Not met",
        "undetermined" => "Can't tell yet",
        "untested" => "Not tested",
        "reproduced" => "Reproduced",
        "not reproduced" => "Not reproduced",
        "gate met" => "Gate met",
        "gate missed" => "Gate missed",
        "gate undetermined" => "Gate, can't tell yet",
        _ => "Gate, not tested",
    };
    let trouble = if v.problem.is_empty() { String::new() } else { format!(" What it rests on has a problem: {}.", v.problem) };
    let Some(e) = v.estimate else {
        let why = if v.why.is_empty() { "no runs are read yet".to_string() } else { rule_words(v.why) };
        return format!("{status}: {why}.{trouble}");
    };
    // a ratio has no unit of its own, and is never in points
    let pct = !v.ratio && is_percent(v.unit);
    let unit = if v.ratio { "" } else { v.unit };
    // the reading's numbers with the decimals that tell them apart from each other and from the rule's number
    // (0.7597 within 0.7591 to 0.7603, 0.994 against 0.99), 2 at least
    let mut xs = vec![e];
    xs.extend(v.interval.map(|(lo, hi)| [lo, hi]).into_iter().flatten());
    xs.extend(v.target);
    let p = apart(&xs);
    // and never rounded onto or past an edge of the rule (the rule's digits)
    let p = rule_places(&xs, &rule_edges(v.comparator, v.target, v.tolerance, v.margin), p);
    let n = |x: f64| decp(x, p);
    let sn = |x: f64| {
        let said = n(x);
        if said.starts_with('-') || said.trim_start_matches(['0', '.']).is_empty() { said } else { format!("+{said}") }
    };
    // a distance between two numbers of the metric: points for a percentage
    // a band's distance never rounds onto its tolerance ("0.05002 from", never "0.05 from" outside ± 0.05)
    let tolerance_edge: Vec<f64> = if v.comparator == "equals" { v.tolerance.into_iter().collect() } else { Vec::new() };
    let by = |x: f64| -> String {
        let said = decp(x, rule_places(&[x], &tolerance_edge, 2));
        match unit {
            _ if pct => format!("{said} {}", if said == "1.00" { "point" } else { "points" }),
            "" => said,
            u => format!("{said} {u}"),
        }
    };
    // a number the rule declares, with its unit: "the 5 points", "the 49.6%"
    let declared = |x: f64, level: bool| -> String {
        let said = crate::assess::declared(x);
        match unit {
            _ if pct && level => format!("{said}%"),
            _ if pct => format!("{said} {}", if said.trim_start_matches('-') == "1" { "point" } else { "points" }),
            "" => said,
            u => format!("{said} {u}"),
        }
    };
    let level = v.reference.is_empty();
    let reading = if v.ratio && !level {
        format!("the ratio of {} to {} is {}", v.subject, v.reference, n(e))
    } else if level {
        let at = if pct { format!("{}%", n(e)) } else if unit.is_empty() { n(e) } else { format!("{} {unit}", n(e)) };
        format!("{} is {at}", v.subject)
    } else if n(e.abs()).trim_start_matches(['0', '.']).is_empty() {
        // an estimate of 0 says its size and no direction, never that the two match
        format!("{} is {} away from {}", v.subject, match unit { _ if pct => "0.00 points".to_string(), "" => "0.00".to_string(), u => format!("0.00 {u}") }, v.reference)
    } else {
        let size = n(e.abs());
        let size = match unit {
            _ if pct => format!("{size} {}", if size == "1.00" { "point" } else { "points" }),
            "" => size,
            u => format!("{size} {u}"),
        };
        format!("{} is {size} {} {}", v.subject, if e < 0.0 { "below" } else { "above" }, v.reference)
    };
    let across = if v.cells { " on average across its design cells" } else { "" };
    let between = match v.interval {
        Some((lo, hi)) if v.ratio => format!(" ({} to {})", n(lo), n(hi)),
        // a level's bounds each with its unit, as the reading's short sentence and the list say them (FIX4)
        Some((lo, hi)) if level => {
            let at = |x: f64| if pct { format!("{}%", n(x)) } else if unit.is_empty() { n(x) } else { format!("{} {unit}", n(x)) };
            format!(" ({} to {})", at(lo), at(hi))
        }
        Some((lo, hi)) => format!(" ({} to {})", sn(lo), sn(hi)),
        None => String::new(),
    };
    let head = format!("{status}: {reading}{across}{between}.");
    if matches!(v.state, "undetermined" | "gate undetermined" | "untested" | "gate untested") {
        let why = if v.why.is_empty() { String::new() } else { format!(" {}.", capital(&rule_words(v.why))) };
        return format!("{head}{why}{trouble}");
    }
    let met = matches!(v.state, "meets" | "reproduced" | "gate met");
    let near = |a: f64, b: f64| (a - b).abs() <= crate::assess::close(a, b) || dec2((a - b).abs()).trim_start_matches(['0', '.']).is_empty();
    // how far it is from the rule's number, where the rule has one and it reads the estimate or a bound
    let ask = |t: f64| if v.cited { format!("the cited {}", declared(t, level)) } else { format!("the {} the rule asks for", declared(t, level)) };
    let gap: Option<String> = match (v.comparator, v.over) {
        _ if v.cells => None,
        ("at_least" | "at_most", "each") => v.target.map(|t| {
            let (word, verb, side) = if v.comparator == "at_least" { ("below", "asks for", "at or above") } else { ("above", "allows", "at or below") };
            if v.missed == 0 {
                format!("every unit is {side} {}, as the rule asks", declared(t, level))
            } else {
                format!("{} of {} {} {} {word} the {} the rule {verb}", v.missed, v.n, if v.n == 1 { "unit" } else { "units" }, if v.missed == 1 { "is" } else { "are" }, declared(t, level))
            }
        }),
        ("at_least" | "at_most", _) => v.target.map(|t| {
            let (x, end) = match (v.over, v.comparator, v.interval) {
                ("bound", "at_least", Some((lo, _))) => (lo, "its lower end is "),
                ("bound", _, Some((_, hi))) => (hi, "its upper end is "),
                _ => (e, ""),
            };
            let (allows, past, short) = if v.comparator == "at_least" { ("asks for", "past", "short of") } else { ("allows", "under", "over") };
            let rule = format!("the {} the rule {allows}", declared(t, level));
            if near(x, t) {
                format!("{end}at {rule}")
            } else {
                let ahead = (x > t) == (v.comparator == "at_least");
                format!("{end}{} {} {rule}", by((x - t).abs()), if ahead { past } else { short })
            }
        }),
        ("equals", _) => v.target.map(|t| {
            // the tolerance is a distance, so in points for a percentage
            let tol = v.tolerance.map(|x| format!(" ± {}", declared(x, false))).unwrap_or_default();
            let side = if met { "inside" } else { "outside" };
            let band = format!("{}{tol}", declared(t, level));
            match (v.over, v.interval) {
                // every unit, or the interval's two ends, are what the rule reads: never the estimate's distance
                ("each", _) if v.missed == 0 => format!("every unit is inside {band}, as the rule asks"),
                ("each", _) => format!("{} of {} {} {} outside {band}", v.missed, v.n, if v.n == 1 { "unit" } else { "units" }, if v.missed == 1 { "is" } else { "are" }),
                ("bound", Some(_)) if met => format!("its interval is inside {band}, as the rule asks"),
                ("bound", Some(_)) => format!("its interval reaches outside {band}"),
                _ if near(e, t) => format!("at {}, inside its{tol}", ask(t)),
                _ => format!("{} from {}, {side} its{tol}", by((e - t).abs()), ask(t)),
            }
        }),
        ("better", _) if !met => Some("the rule asks for an interval clear of 0 on the better side".into()),
        ("noninferior", _) => v.margin.zip(v.interval).map(|(m, (lo, hi))| {
            let worse = if v.direction == "minimize" { hi } else { lo };
            let edge = declared(m, level);
            if met { format!("its worse end, {}, is inside the {edge} the rule allows", sn(worse)) } else { format!("its worse end, {}, reaches past the {edge} the rule allows", sn(worse)) }
        }),
        ("equivalent", _) => v.margin.map(|m| {
            let edge = declared(m, level);
            if met { format!("its interval is inside ± {edge}, as the rule asks") } else { format!("its interval reaches past the ± {edge} the rule allows") }
        }),
        _ => None,
    };
    // rb computes no interval across fixed design cells, so none is to come
    let class = if v.cells && v.result == "point_only" { "No interval is computed across design cells" } else { crate::assess::result_phrase(v.result) };
    let good = matches!(v.result, "gain" | "equivalent");
    let bad = v.result == "loss";
    let tail = match (class, gap) {
        ("", None) => String::new(),
        ("", Some(g)) => format!(" It is {g}."),
        (c, None) => format!(" {}.", capital(c)),
        (c, Some(g)) if (good && !met) || (bad && met) => format!(" {}, but {g}.", capital(c)),
        (c, Some(g)) => format!(" {}. It is {g}.", capital(c)),
    };
    // "It is its lower end is ..." reads as one clause
    let tail = tail.replace("It is its ", "Its ").replace("It is every ", "Every ").replace(&format!("It is {} of ", v.missed), &format!("{} of ", v.missed));
    let tail = tail.replace("It is the rule asks", "The rule asks");
    format!("{head}{tail}{trouble}")
}

/// rb's rule sentence after its word, in the rule's words: "2 units; the rule needs 3".
fn rule_words(why: &str) -> String {
    why.replace("the criterion", "the rule").replace("criterion", "rule")
}

fn capital(s: &str) -> String {
    let mut c = s.chars();
    c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
}

/// A finding's rule state with its subject (PLAIN-WORDS-PASS-3), as a queue row leads with it: "Rule met", "Rule not
/// met", "Rule: can't tell yet", "Rule: not tested", "Not reproduced", "Gate missed".
fn rule_state(f: &Value) -> String {
    let label = text(f, "state_label");
    let said = match label.as_str() {
        "met" | "not met" => format!("rule {label}"),
        "can't tell yet" | "not tested" => format!("rule: {label}"),
        _ => label,
    };
    let mut c = said.chars();
    c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
}

/// A rule word as a tally says it after its count: "4 met", "1 not met", "1 not reproduced".
fn state_label(state: &str) -> String {
    match state {
        "meets" => "met".into(),
        "misses" => "not met".into(),
        // PLAIN-WORDS-PASS-1 (D1): the two stay apart; the glossary's hover says how
        "undetermined" => "can't tell yet".into(),
        "untested" => "not tested".into(),
        "gate undetermined" => "gate: can't tell yet".into(),
        "gate untested" => "gate: not tested".into(),
        other => other.into(),
    }
}

/// What an experiment compares, its arms labelled: "int8 against the fp32 baseline", "int8 and int4 against the fp32
/// baseline"; its variants alone where it has no baseline.
fn compares(exp: &Experiment) -> String {
    let vs = exp.live_variants();
    // each variant by its plain name, as the grid and the runs table say it (FIX4)
    let others: Vec<String> = vs.iter().filter(|v| v.role != "baseline").map(|v| plain(&arm_label(exp, &v.name))).collect();
    let and = |xs: &[String]| match xs {
        [] => String::new(),
        [one] => one.clone(),
        [init @ .., last] => format!("{} and {last}", init.join(", ")),
    };
    match exp.by_role("baseline").first() {
        Some(b) if !others.is_empty() => format!("{} against {}", and(&others), plain(&the(exp, &b.name))),
        Some(b) => plain(&arm_label(exp, &b.name)),
        None => and(&others),
    }
}

/// The units a finding's reading rests on, in plain words (SHELL-SPEC 12): "N runs" when each unit holds one run,
/// else the unit's plain noun and the runs it holds ("1 configuration from 2 runs"); then how the arms split, where
/// each arm's runs are recorded apart and the head counts something other than seeds ("3 seeds per arm"); then the runs
/// not counted. A reading from an analysis rests on it and the runs it reads: "analysis a1 · reads 3 runs".
fn units_line(led: &Ledger, f: &Finding) -> Res<String> {
    let plural = |n: usize, w: &str| format!("{n} {}", crate::assess::plural(w, n as i64));
    if let Some(r) = f.comparison.as_ref().filter(|r| f.observations.is_empty() && !r.source.is_empty() && r.source != "runs") {
        if let Ok(a) = led.load::<Analysis>(&r.source) {
            let mut runs = 0;
            for id in &a.reads {
                if led.kind_of(id)? == Some("run") {
                    runs += 1;
                }
            }
            return Ok(if runs == 0 { format!("analysis {}", a.id) } else { format!("analysis {} · reads {}", a.id, plural(runs, "run")) });
        }
    }
    let u = &f.units;
    let mut parts: Vec<String> = Vec::new();
    if u.n != 0 {
        parts.push(if u.runs == u.n || u.runs == 0 {
            plural(u.n as usize, "run")
        } else {
            format!("{} {} from {}", u.n, crate::ledger::views::unit_noun(&u.unit, u.n), plural(u.runs as usize, "run"))
        });
    }
    let counted: Vec<&crate::record::Observation> = f.observations.iter().filter(|o| o.counted).collect();
    let mut arms: IndexMap<&str, Vec<&crate::record::Observation>> = IndexMap::new();
    for o in &counted {
        arms.entry(o.arm.as_str()).or_default().push(o);
    }
    let design = f.comparison.as_ref().map_or(false, |r| !r.design.is_empty());
    let sizes: std::collections::BTreeSet<usize> = arms.values().map(Vec::len).collect();
    if !design && u.unit != "seed" && arms.len() >= 2 && !arms.contains_key("") && sizes.len() == 1 && sizes.first().map_or(false, |k| *k > 1) && !parts.is_empty() {
        let k = *sizes.first().unwrap_or(&0);
        let seeds = arms.values().all(|os| os.iter().filter_map(|o| o.per_run.get("seed").map(crate::ledger::show)).collect::<std::collections::BTreeSet<_>>().len() == k);
        parts.push(format!("{k} {} per arm", if seeds { "seeds" } else { "runs" }));
    }
    let left = f.observations.len() - counted.len();
    if left > 0 {
        parts.push(format!("{} not counted", plural(left, "run")));
    }
    Ok(parts.join(" · "))
}

/// One amendment of an experiment after its lock, as a screen lists it: its anchor id ("e1:am:2"), when, rb's line
/// for it, each change it made to a setting or the criteria (setting, was, now), the reason as rb says it (a session's
/// as its note), and the ids it names.
struct Amended {
    id: String,
    at: String,
    /// when, as the record keeps it
    when: String,
    line: String,
    changes: Vec<(String, String, String)>,
    why: String,
    refs: Vec<String>,
}

/// The changes an amendment's line names, from rb's own change words: a setting's old and new value ("setting
/// population.unit: run -> photo", "setting lr: new, 0.1"), a criterion added or taken back. The rest its line says.
fn change_parts(change: &str) -> Vec<(String, String, String)> {
    let clean = |v: &str| v.split(" (").next().unwrap_or("").to_string();
    if let Some(rest) = change.strip_prefix("setting ") {
        if let Some((name, what)) = rest.split_once(": ") {
            let first = what.split("; ").next().unwrap_or("");
            // what a write changed besides the value, in rb's own words: no value to hand apart
            let other = ["required -> optional", "optional -> required", "now per-run", "no longer per-run", "now a fixed design factor", "no longer a fixed design factor", "note changed", "unchanged"];
            if other.contains(&first) || first.starts_with("source ") || first.starts_with("cited ") {
                return Vec::new();
            }
            if let Some(v) = first.strip_prefix("new, ") {
                return vec![(name.to_string(), String::new(), clean(v))];
            }
            if let Some((was, now)) = first.split_once(" -> ") {
                return vec![(name.to_string(), was.to_string(), clean(now))];
            }
        }
        return Vec::new();
    }
    if let Some(rest) = change.strip_prefix("new criterion ") {
        return vec![("criteria".into(), String::new(), rest.split(':').next().unwrap_or("").to_string())];
    }
    if let Some(id) = change.strip_prefix("criterion ").and_then(|r| r.strip_suffix(" retracted")) {
        return vec![("criteria".into(), id.to_string(), String::new())];
    }
    Vec::new()
}

/// The setting each amendment of `exp` changed, in the amendments' order: the setting, the value it had before (none
/// where it was new or taken back) and the value it has, as rb shows a value, handed apart from the log rather than
/// read back out of rb's change words. Each amendment is paired with its own log line, the lines that made one in
/// order; a line whose setting and values the amendment's own words do not say gives none, and its change is then
/// read from those words.
fn amended_settings(led: &Ledger, exp: &Experiment) -> Res<Vec<Option<Vec<(String, String, String)>>>> {
    let said = |v: Option<&Value>| match v {
        None => String::new(),
        Some(Value::Null) => "unknown".to_string(),
        Some(v) => crate::ledger::show(v),
    };
    // each log line of this experiment that made an amendment, with the setting's values where it set one
    let mut values: IndexMap<String, String> = IndexMap::new();
    let mut lines: Vec<Option<(String, String, String)>> = Vec::new();
    for row in led.log_entries()?.iter() {
        if row.get_str("kind") != Some("experiment") || row.get_str("id") != Some(exp.id.as_str()) {
            continue;
        }
        let Some(Value::Dict(d)) = row.get("detail") else { continue };
        let name = d.get("setting").and_then(Value::as_str).map(str::to_string);
        let set = match (row.get_str("op"), &name) {
            (Some("spec_set"), Some(name)) => {
                let now = said(d.get("value"));
                let was = values.insert(name.clone(), now.clone()).unwrap_or_default();
                Some((name.clone(), was, now))
            }
            _ => None,
        };
        if d.get("amendment").map_or(false, Value::truthy) {
            lines.push(set);
        }
    }
    let fits = |a: &crate::record::Amendment, (name, was, now): &(String, String, String)| {
        let words = a.change.strip_prefix(&format!("setting {name}: ")).unwrap_or("");
        let now_said = now == "unknown" || words.contains(&format!("new, {now}")) || words.contains(&format!("-> {now}"));
        let was_said = was.is_empty() || words.starts_with(&format!("{was} -> "));
        now_said && was_said
    };
    Ok(exp
        .amendments
        .iter()
        .enumerate()
        .map(|(i, a)| match lines.get(i) {
            // a setting each run gives its own holds no value: each run's, as rb shows one
            Some(Some((name, was, now))) if now == "unknown" && a.change.starts_with(&format!("setting {name}: new, ")) && a.change.contains("(per-run)") => Some(vec![(name.clone(), was.clone(), "each run".into())]),
            Some(Some(set)) if fits(a, set) => Some(vec![set.clone()]),
            _ => None,
        })
        .collect())
}

/// Each amendment of `exp` made after its lock, in order (`Amended`).
fn amendments_of(led: &Ledger, exp: &Experiment) -> Res<Vec<Amended>> {
    let Some(fz) = &exp.frozen else { return Ok(Vec::new()) };
    let design = led.design(&exp.id)?.unwrap_or_default();
    // every criterion it may name, one taken back since included; when each was declared
    let criteria: Vec<(String, String)> = led.all::<Criterion>()?.iter().filter(|c| c.experiment == exp.id).map(|c| (c.id.clone(), c.created_at.clone())).collect();
    let live: Vec<String> = led.all::<Criterion>()?.iter().filter(|c| c.experiment == exp.id && c.retracted.is_none()).map(|c| c.id.clone()).collect();
    let settings = amended_settings(led, exp)?;
    let mut out = Vec::new();
    for (i, a) in exp.amendments.iter().enumerate().filter(|(_, a)| a.at >= fz.at) {
        let changes = settings.get(i).cloned().flatten().unwrap_or_else(|| change_parts(&a.change));
        let unit = changes.iter().find(|(k, _, _)| k == "population.unit");
        let line = match unit {
            Some((_, was, now)) => format!("unit amended after the lock: {} -> {now}", if was.is_empty() { "run" } else { was }),
            None => format!("amended after the lock: {}", a.change),
        };
        // the criteria it names; a unit amended changes how each criterion declared before it pairs its runs
        let refs: Vec<String> = if unit.is_some() {
            criteria.iter().filter(|(id, at)| live.contains(id) && *at <= a.at).map(|(id, _)| id.clone()).collect()
        } else {
            criteria.iter().filter(|(id, _)| a.change.split(|ch: char| ch == ' ' || ch == ':').any(|w| w == id.as_str())).map(|(id, _)| id.clone()).collect()
        };
        out.push(Amended {
            id: format!("{}:am:{}", exp.id, i + 1),
            at: minute(&a.at),
            when: a.at.clone(),
            line,
            changes,
            why: view::reason_said(&a.why, &design.writer(&a.by, &format!("amendment_why:{}", i + 1))),
            refs,
        });
    }
    Ok(out)
}

/// The unit amended after the lock, as it changes how `c` pairs its runs: from what it was when `c` was declared (or
/// the experiment locked, whichever came later; each run its own unit where none was declared) to what it is, by
/// the amendments of `population.unit` made after that, the latest's anchor, time and reason; none for a criterion
/// declared after them.
struct UnitAmended {
    id: String,
    at: String,
    was: String,
    now: String,
    line: String,
    why: String,
}

fn unit_amendment(led: &Ledger, exp: &Experiment, c: &Criterion) -> Res<Option<UnitAmended>> {
    let units: Vec<(Amended, String, String)> = amendments_of(led, exp)?
        .into_iter()
        .filter(|a| a.when >= c.created_at)
        .filter_map(|a| {
            let (_, was, now) = a.changes.iter().find(|(k, _, _)| k == "population.unit")?.clone();
            Some((a, was, now))
        })
        .collect();
    let (Some((_, first, _)), Some((last, _, now))) = (units.first(), units.last()) else { return Ok(None) };
    let was = if first.is_empty() { "run".to_string() } else { first.clone() };
    if was == *now {
        return Ok(None);
    }
    Ok(Some(UnitAmended {
        id: last.id.clone(),
        at: last.at.clone(),
        line: format!("unit amended after the lock: {was} -> {now}"),
        was,
        now: now.clone(),
        why: last.why.clone(),
    }))
}

/// A finding's own observations, the source of its reading, its design cells, and what rests on it (BP 1a): each run
/// it observes with its arm, unit, per-run values, value in rb's digits and whether it counts, with rb's reason; the
/// runs or the analysis its reading comes from; each design cell with its estimate, its runs and whether it falls
/// outside the rule; the interpretations, question links and relations that rest on it, and the call made on it.
fn finding_graph(led: &Ledger, h: &Home, c: &Criterion, f: &Finding) -> Res<Dict> {
    let observations: Vec<Value> = f
        .observations
        .iter()
        .map(|o| crate::obj! {
            "run" => o.run.clone(), "arm" => o.arm.clone(), "unit" => o.unit.clone(),
            "per_run" => Value::Dict(o.per_run.iter().map(|(k, v)| (k.clone(), Value::Str(crate::ledger::show(v)))).collect()),
            "value_text" => crate::assess::observation_said(c, o), "counted" => o.counted, "reason" => o.reason.clone()
        })
        .collect();
    let analyses: std::collections::HashSet<String> = led.all::<Analysis>()?.iter().map(|a| a.id.clone()).collect();
    let source = match &f.comparison {
        Some(r) if analyses.contains(&r.source) => crate::obj! {"kind" => "analysis", "id" => r.source.clone()},
        Some(_) => crate::obj! {"kind" => "runs", "id" => ""},
        None => crate::obj! {"kind" => "", "id" => ""},
    };
    // each design cell as the rule judges it, its own estimate said as rb says a cell's
    let mut cells: Vec<Value> = Vec::new();
    if let Some(cur) = f.comparison.as_ref().filter(|r| !r.design.is_empty()) {
        let read = led.design_cells(c)?;
        // each cell's interval, units and what a unit is, as the reading's own cell holds them
        let of_cell = |key: &str| cur.design.get("cells").and_then(Value::as_list).and_then(|cs| cs.iter().find(|x| x.get_str("key") == Some(key))).cloned();
        let bounds = |key: &str| of_cell(key).and_then(|x| Some((x.get("lo")?.as_f64()?, x.get("hi")?.as_f64()?)));
        // the estimates, then the bounds, said together so every number takes the rule's digits and none reads as another
        let mut xs: Vec<f64> = read.iter().filter_map(|(_, x, _, _)| *x).collect();
        xs.extend(read.iter().filter_map(|(k, _, _, _)| bounds(k)).flat_map(|(lo, hi)| [lo, hi]));
        let mut said = crate::assess::cells_said(Some(c), cur, &xs).into_iter();
        let estimates: Vec<String> = read.iter().map(|(_, x, _, _)| if x.is_some() { said.next().unwrap_or_default() } else { String::new() }).collect();
        for ((key, _, outside, runs), estimate_text) in read.into_iter().zip(estimates) {
            let interval_text = match bounds(&key) {
                Some(_) => format!("[{}, {}]", said.next().unwrap_or_default(), said.next().unwrap_or_default()),
                None => String::new(),
            };
            let cell = of_cell(&key).unwrap_or(Value::Null);
            cells.push(crate::obj! {
                "key" => key, "estimate_text" => estimate_text, "runs" => Value::list_of_str(runs), "outside" => outside,
                "interval_text" => interval_text, "n" => cell.get("n").and_then(Value::as_i64).unwrap_or(0), "unit" => text(&cell, "unit")
            });
        }
    }
    let interpretations: Vec<Value> = h
        .briefs
        .iter()
        .filter_map(|b| b.id.as_ref().map(|q| (q, b)))
        .flat_map(|(q, b)| {
            list(&b.overview, "interpretations")
                .iter()
                .filter(|grp| list(grp, "criteria").iter().any(|x| py_str(x) == c.id))
                .map(|grp| crate::obj! {
                    "question" => q.clone(), "active" => grp.get("active").map_or(false, Value::truthy), "confirmed" => grp.get("confirmed").map_or(false, Value::truthy),
                    "text" => text(grp, "text")
                })
                .collect::<Vec<_>>()
        })
        .collect();
    let edges = crate::graph::criterion_rests(led, c)?;
    // the call made on it, as its review says it, with the words given with the call as the reader says them
    let call = match &f.review {
        Some(r) => {
            let d: Option<Decision> = led.load::<Decision>(&r.decision).ok();
            let line = f.says.get("review").cloned().unwrap_or_default();
            d.map_or(line.clone(), |d| view::with_words(&line, &d.words, &d.by))
        }
        None => String::new(),
    };
    let rests_on = crate::obj! {
        "interpretations" => Value::List(interpretations), "question_links" => strs(&edges.question_links),
        "relations_out" => Value::List(edges.relations_out.iter().map(|(kind, target)| crate::obj! {"kind" => kind.clone(), "criterion" => target.clone()}).collect()),
        "relations_in" => Value::List(edges.relations_in.iter().map(|(from, kind)| crate::obj! {"kind" => kind.clone(), "criterion" => from.clone()}).collect()),
        "call" => call
    };
    Ok(crate::dict! {"observations" => Value::List(observations), "source" => source, "design_cells" => Value::List(cells), "rests_on" => rests_on})
}

/// An experiment's comparison as the contract holds it: its metrics with their units, what varies, the conditions its
/// rows are split by, and each row's variant, role, settings, cell and means (with the criteria that read a mean
/// otherwise than the table does). A setting's value is said as rb says it. No comparison yet: no rows.
fn table_value(table: Option<&Dict>, varies: &[String]) -> Value {
    let t = table.cloned().map(Value::Dict).unwrap_or(Value::Null);
    let said = |d: Option<&Value>| -> Value {
        let d = d.and_then(Value::as_dict).cloned().unwrap_or_default();
        Value::Dict(d.into_iter().filter(|(_, v)| !v.is_null()).map(|(k, v)| (k, Value::Str(crate::ledger::findings::unit_value(&v)))).collect())
    };
    let rows: Vec<Value> = list(&t, "rows")
        .iter()
        .map(|r| {
            let metrics: Dict = r
                .get("metrics")
                .and_then(Value::as_dict)
                .map(|m| {
                    m.iter()
                        .map(|(k, c)| {
                            let read_by: Vec<Value> = list(c, "read_by").iter().map(|x| Value::Dict(pick(x, &["criterion", "reads"]))).collect();
                            let or_null = |key: &str| c.get(key).cloned().unwrap_or(Value::Null);
                            let flag = |key: &str| c.get(key).map_or(false, Value::truthy);
                            // a change against a baseline outside the cell's column is no difference within one: a
                            // screen that draws a cell's change draws none there
                            let in_column = c.get("in_column").map_or(true, Value::truthy);
                            let or_null = |key: &str| if !in_column && matches!(key, "delta" | "better") { Value::Null } else { or_null(key) };
                            // rb compare's change against the baseline and what it rests on, each always given
                            (k.clone(), crate::obj! {
                                "mean" => or_null("mean"), "n" => or_null("n"), "read_by" => Value::List(read_by),
                                "delta" => or_null("delta"), "better" => or_null("better"), "paired" => flag("paired"), "primary" => flag("primary"),
                                "said" => text(c, "said"), "basis" => text(c, "basis"), "differs_in" => Value::list_of_str(list(c, "differs_in").iter().map(py_str)),
                                "other_code" => text(c, "other_code"),
                                // a grid's cell: its runs and how many it rests on; within its column, the change's
                                // interval where the units pair, or why there is none, and the rules that read it
                                "runs" => or_null("runs"), "run_ids" => Value::list_of_str(list(c, "run_ids").iter().map(py_str)), "count_said" => text(c, "count_said"),
                                "lo" => or_null("lo"), "hi" => or_null("hi"), "level" => or_null("level"), "no_interval" => text(c, "no_interval"), "in_column" => in_column,
                                "criteria" => Value::list_of_str(list(c, "criteria").iter().map(py_str))
                            })
                        })
                        .collect()
                })
                .unwrap_or_default();
            crate::obj! {"variant" => text(r, "variant"), "role" => text(r, "role"), "varies" => said(r.get("varies")), "where" => said(r.get("where")), "unvaried" => said(r.get("unvaried")), "metrics" => Value::Dict(metrics)}
        })
        .collect();
    crate::obj! {
        "metrics" => Value::List(list(&t, "metrics").iter().map(|m| crate::obj! {"name" => text(m, "name"), "unit" => text(m, "unit")}).collect()),
        "varies" => strs(varies), "conditions" => Value::list_of_str(list(&t, "conditions").iter().map(py_str)), "unit" => text(&t, "cell_unit"), "rows" => Value::List(rows)
    }
}

fn experiments_value(led: &Ledger, h: &Home, st: &Value, recipes: &crate::reproduce::Recipes) -> Res<Value> {
    let runs = led.all::<Run>()?;
    let rows: IndexMap<String, &Value> = list(st, "experiments").iter().map(|e| (text(e, "id"), e)).collect();
    Ok(Value::List(
        experiments_of(h)
            .into_iter()
            .map(|e| -> Res<Value> {
                let v = e.to_value();
                let mut d = pick(&v, &["id", "title", "short_title", "state"]);
                d.insert("who_line".into(), rows.get(&e.id).map(|r| who_line(r)).unwrap_or_default().into());
                d.extend(pick(&v, &["arms", "varies", "counted", "runs", "hypothesis", "purpose", "why_part", "gate_parts", "gate_words_said", "notes", "note_ids", "relations", "folder", "repo_brief", "design"]));
                // a rule a design's line adds, in a page's words: its metric by its title (Look 8); a rule changed or
                // removed keeps rb's words, whose was is only text, so its was and now read alike
                if let Some(Value::List(lines)) = d.get_mut("design").and_then(|x| if let Value::Dict(x) = x { x.get_mut("lines") } else { None }) {
                    for c in led.all::<Criterion>()?.iter().filter(|c| c.experiment == e.id && c.retracted.is_none()) {
                        if let Some(words) = rule_on_page(led, c)? {
                            let rule = c.rule();
                            for l in lines.iter_mut() {
                                if let Value::Str(t) = l {
                                    if let Some(at) = t.find(&format!("criterion {} added: {rule} (", c.id)) {
                                        let from = at + format!("criterion {} added: ", c.id).len();
                                        t.replace_range(from..from + rule.len(), &words);
                                    }
                                }
                            }
                        }
                    }
                }
                d.insert("findings".into(), Value::list_of_str(e.findings.iter().map(|f| f.criterion_id.clone())));
                d.insert("run_ids".into(), Value::list_of_str(runs.iter().filter(|r| r.experiment == e.id).map(|r| r.id.clone())));
                d.insert("table".into(), table_value(e.table.as_ref(), &e.varies));
                d.insert("rerun".into(), Value::List(e.rerun.iter().map(|r| Value::Dict(pick(&Value::Dict(r.clone()), &["state", "what"]))).collect()));
                d.insert("reproduce".into(), recipes.experiment_of(&e.id)?);
                let x = led.load::<Experiment>(&e.id).ok();
                d.insert("compares".into(), x.as_ref().map(compares).unwrap_or_default().into());
                // the card's lines, one rule each (view::relation_lines, view::settings_of), and its rule counts
                d.insert("relation_lines".into(), strs(&x.as_ref().map(view::relation_lines).unwrap_or_default()));
                d.insert("settings_line".into(), x.as_ref().map(|x| view::settings_of(led, x).map(|s| s.0)).transpose()?.unwrap_or_default().into());
                let (tally, muted) = view::experiment_tally(st, &e.id);
                d.insert("tally".into(), Value::List(tally.into_iter().map(|(k, n)| crate::list![k, n]).collect()));
                d.insert("muted".into(), strs(&muted));
                let amended = match &x {
                    Some(x) => amendments_of(led, x)?,
                    None => Vec::new(),
                };
                d.insert(
                    "amendments".into(),
                    Value::List(
                        amended
                            .into_iter()
                            .map(|a| crate::obj! {
                                "id" => a.id, "at" => a.at, "line" => a.line,
                                "changes" => Value::List(a.changes.into_iter().map(|(k, was, now)| crate::obj! {"setting" => k, "was" => was, "now" => now}).collect()),
                                "why" => a.why, "refs" => strs(&a.refs)
                            })
                            .collect(),
                    ),
                );
                Ok(Value::Dict(d))
            })
            .collect::<Res<Vec<Value>>>()?,
    ))
}

/// A run's numbers as rb show and the run's card say them (ledger::show: the number as recorded, never rounded, so a
/// row never reads 1234.57 beside a rule of 1234.5678), each arm's together after its name in the experiment's order,
/// the numbers no arm names first: "latency_ms=25.1 · fp32: top1=75.69 · int8: top1=75.53".
fn metrics_text(exp: Option<&Experiment>, r: &Run) -> String {
    let said = |x: f64| crate::ledger::show(&Value::Float(x));
    let arms: Vec<String> = exp.map(|x| x.variants.iter().map(|v| v.name.clone()).collect()).unwrap_or_default();
    let mut plain: Vec<String> = Vec::new();
    let mut by: IndexMap<String, Vec<String>> = arms.iter().map(|a| (a.clone(), Vec::new())).collect();
    for (k, v) in &r.metrics {
        match k.split_once('.').filter(|(arm, _)| by.contains_key(*arm)) {
            Some((arm, name)) => by.entry(arm.to_string()).or_default().push(format!("{name}={}", said(*v))),
            None => plain.push(format!("{k}={}", said(*v))),
        }
    }
    let mut out: Vec<String> = Vec::new();
    if !plain.is_empty() {
        out.push(plain.join(" "));
    }
    out.extend(by.into_iter().filter(|(_, xs)| !xs.is_empty()).map(|(arm, xs)| format!("{arm}: {}", xs.join(" "))));
    out.join(" · ")
}

/// A run's receipt as rows of the receipt's field, rb's words for it and the ids they name (`view::run_parts`, the
/// run card's own lines), as `tier` shows them (shell/tier.rs): no writer, session or branch on any tier, and the
/// machine's platform on the Live tier alone, a row of its own apart from the environment.
fn receipt_rows(r: &Run, tier: Tier) -> Value {
    let mut card = view::receipt_card(r);
    let platform = r.receipt.environment.get("platform").map(py_str).filter(|_| tier.has(Row::Platform));
    if let Some(Value::Dict(env)) = card.get_mut("environment") {
        env.shift_remove("platform");
    }
    let mut rows: Vec<Value> = view::run_parts(r, &Value::Dict(card))
        .into_iter()
        .map(|p| Value::List(vec![p.field.into(), p.row_text().into(), Value::list_of_str(p.refs)]))
        .collect();
    if let Some(p) = platform {
        rows.push(Value::List(vec!["platform".into(), p.into(), Value::List(Vec::new())]));
    }
    Value::List(rows)
}

/// Every run of the record, retracted ones too, each once: when it was recorded and produced, its per-run values, its
/// numbers, its note, its retraction, and where it was recorded when that is another place than the one shown.
fn runs_value(led: &Ledger, tier: Tier, recipes: &crate::reproduce::Recipes, g: &Graph) -> Res<Value> {
    let rows: IndexMap<String, view::RunRow> = view::run_rows(led, usize::MAX)?.into_iter().map(|r| (r.run_id.clone(), r)).collect();
    let experiments: IndexMap<String, Experiment> = led.all::<Experiment>()?.iter().map(|x| (x.id.clone(), x.clone())).collect();
    let mut out = Dict::new();
    for r in led.all::<Run>()?.iter() {
        let row = rows.get(&r.id).cloned().unwrap_or_default();
        let exp = experiments.get(&r.experiment);
        let given = crate::ledger::support::run_command(r);
        let command = crate::obj! {
            "text" => given.as_ref().map(|c| c.text.clone()).unwrap_or_default(),
            "kind" => match given.as_ref().map(|c| c.given) {
                Some("at_the_time") => "as given",
                Some("observed") => "observed",
                Some("logged") => "logged",
                Some(_) => "given later",
                None => "not recorded",
            }
        };
        // a session's words read as its note, not confirmed, as the run's card says them
        let retracted =
            r.retracted.as_ref().map_or(Value::Null, |x| crate::obj! {"at" => day(&x.at), "line" => view::retraction_words(x), "why" => x.why.clone() + view::not_confirmed(&x.by)});
        let observed_in = row.observed_in.as_ref().map_or(Value::Null, |o| {
            let mut d = pick(o, &["label", "label_short", "commit", "dirty", "gone", "place"]);
            for k in ["commit", "place"] {
                if d.get(k).map_or(true, Value::is_null) {
                    d.insert(k.into(), "".into());
                }
            }
            Value::Dict(d)
        });
        out.insert(
            r.id.clone(),
            crate::obj! {
                "run_id" => r.id.clone(), "experiment_id" => r.experiment.clone(), "when" => row.when, "produced" => row.produced,
                "per_run_text" => r.per_run.iter().map(|(k, v)| format!("{k}={}", crate::ledger::show(v))).collect::<Vec<_>>().join(" · "),
                "results" => row.results, "note" => if r.note.is_empty() { String::new() } else { r.note.clone() + view::not_confirmed(&r.created_by) }, "retracted" => retracted, "observed_in" => observed_in,
                "counts_toward" => Value::List(g.counts_toward(r).into_iter().map(|t| crate::obj! {"criterion" => t.criterion, "counted" => t.counted, "reason" => t.reason, "via" => t.via}).collect()),
                "same_bytes" => Value::List(g.same_bytes(r).into_iter().map(|s| crate::obj! {"run" => s.run, "experiment" => s.experiment, "retracted" => s.retracted, "made" => strs(&s.made)}).collect()),
                "made" => strs(&crate::graph::made(r)), "reproduce" => recipes.run(r)?,
                "label" => crate::reproduce::run_label(led, exp, r), "at" => minute(if r.receipt.at.is_empty() { &r.created_at } else { &r.receipt.at }),
                "metrics_text" => metrics_text(exp, r), "receipt_rows" => receipt_rows(r, tier), "command" => command
            },
        );
    }
    Ok(Value::Dict(out))
}

fn resources_value(led: &Ledger, h: &Home, st: &Value, g: &Graph) -> Res<Value> {
    let urls: IndexMap<String, String> = list(st, "resources").iter().map(|r| (text(r, "id"), r.get("acquisition").map(|a| text(a, "url")).unwrap_or_default())).collect();
    let records: IndexMap<String, Resource> = led.all::<Resource>()?.iter().map(|r| (r.id.clone(), r.clone())).collect();
    // what each was read as, and how to get it again, computed on read (BP 1c, acquisition.rs)
    let acq = crate::acquisition::fields(led)?;
    let mut out = Dict::new();
    for r in &h.resources {
        let url = urls.get(&r.resource_id).cloned().unwrap_or_default();
        // the download address is `url`'s alone, which the Owner tier empties: `how` is the recipe or the source
        let how = if !url.is_empty() && r.how == url { String::new() } else { r.how.clone() };
        let mut v = crate::obj! {
            "resource_id" => r.resource_id.clone(), "name" => r.name.clone(), "type" => r.r#type.clone(), "availability" => r.availability.clone(),
            "where" => strs(&r.where_), "where_short" => strs(&r.where_short), "identity" => r.identity.clone(), "exact" => r.exact,
            "how" => how, "url" => url
        };
        if let (Some(res), Value::Dict(d)) = (records.get(&r.resource_id), &mut v) {
            // who read it and who made it, apart, and the criteria resting on what read it
            let users = crate::graph::users_of(led, res)?;
            let (criteria, by_question) = g.resting_on(&users);
            let pairs = |xs: &[(String, String)]| Value::List(xs.iter().map(|(run, e)| crate::obj! {"run" => run.clone(), "experiment" => e.clone()}).collect());
            d.insert("read_by".into(), crate::obj! {"runs" => pairs(&users.read), "analyses" => strs(&users.analyses)});
            d.insert("made_by".into(), pairs(&users.made));
            d.insert("rests_on".into(), crate::obj! {
                "criteria" => strs(&criteria), "by_question" => Value::Dict(by_question.into_iter().map(|(q, cs)| (q, strs(&cs))).collect())
            });
        }
        if let (Value::Dict(d), Some(f)) = (&mut v, acq.get(&r.resource_id)) {
            d.extend(f.clone());
        }
        out.insert(r.resource_id.clone(), v);
    }
    Ok(Value::Dict(out))
}

/// Each analysis not taken back: its title, when it was recorded and on what basis, the runs it read, and the file it
/// was read from with the start of its sha256.
fn analyses_value(led: &Ledger, g: &Graph) -> Res<Value> {
    Ok(Value::Dict(
        led.live::<Analysis>()?
            .into_iter()
            .map(|a| {
                let basis = if a.basis.is_empty() { String::new() } else { format!(" · basis {}", a.basis) };
                let file = a.files.first();
                let v = crate::obj! {
                    "id" => a.id.clone(), "title" => a.title.clone(), "recorded" => format!("analysis · recorded {}{basis}", minute(&a.created_at)),
                    "reads" => strs(&a.reads), "script" => file.map(|f| f.path.clone()).unwrap_or_default(),
                    "sha256" => file.map(|f| f.sha256.chars().take(12).collect::<String>()).unwrap_or_default(),
                    "used_by" => strs(&g.used_by(&a))
                };
                (a.id, v)
            })
            .collect(),
    ))
}

/// What a page says for each object rb minted an id for, which it never shows as text: a run by its label ("run int8 ·
/// seed 1", "a run of e1" where it has none), a call by what it is ("a call to accept c1"), an analysis by its title
/// where its id is rb's, "an analysis" where it has none (one chosen, a1, stays). A note is said by what it is on, by its row (log_rows).
fn minted_names(led: &Ledger, doc: &Dict) -> Res<IndexMap<String, String>> {
    let mut names = IndexMap::new();
    if let Some(runs) = doc.get("runs").and_then(Value::as_dict) {
        // when each was recorded, in the order the log recorded them
        let mut seq: IndexMap<String, usize> = IndexMap::new();
        for (i, row) in led.log_entries()?.iter().enumerate() {
            if row.get_str("kind") == Some("run") {
                if let Some(id) = row.get_str("id") {
                    seq.entry(id.to_string()).or_insert(i);
                }
            }
        }
        let mut times: Vec<(String, String)> =
            led.all::<Run>()?.iter().map(|r| (r.id.clone(), if r.receipt.at.is_empty() { r.created_at.clone() } else { r.receipt.at.clone() })).collect();
        times.sort_by_key(|(id, _)| seq.get(id).copied().unwrap_or(usize::MAX));
        names.extend(run_names(runs, &times.into_iter().collect()));
    }
    // a call a session recorded says so, as History's line does (history::call_said): it was never a call made
    let auth = led.authorities()?;
    for d in led.all::<Decision>()?.iter() {
        let said = match auth.get(&d.id).map(|a| a.state) {
            Some("proposed") => format!("a proposed call to {} {}, not made", d.action, d.subject),
            Some("past_unconfirmed") => format!("a past call to {} {} mentioned, not confirmed", d.action, d.subject),
            _ => format!("a call to {} {}", d.action, d.subject),
        };
        names.insert(d.id.clone(), said);
    }
    // one with no title is "an analysis", as a card says it (FIX9): its id stays for the link alone
    for a in led.all::<Analysis>()?.iter() {
        if minted(&a.id, "a") {
            names.insert(a.id.clone(), if a.title.trim().is_empty() { "an analysis".to_string() } else { format!("analysis {}", a.title.trim()) });
        }
    }
    Ok(names)
}

/// Each run rb retracted when another replaced it (`rb run add --replaces`), with the run that replaced it: rb's own
/// "replaced by <that run>" opens the retraction's reason.
fn replacers_of(led: &Ledger) -> Res<IndexMap<String, String>> {
    let runs = led.all::<Run>()?;
    let mut out = IndexMap::new();
    for x in runs.iter() {
        let Some(old) = x.replaces.as_deref().filter(|o| !o.is_empty()) else { continue };
        let ours = runs.iter().any(|r| r.id == old && r.retracted.as_ref().is_some_and(|t| t.why.starts_with(&format!("replaced by {}", x.id))));
        if ours {
            out.insert(old.to_string(), x.id.clone());
        }
    }
    Ok(out)
}

/// The words of each retraction rb made when a run replaced another, as rb says them ("retracted: replaced by
/// runabcdef: ..."), with the same words naming the run that replaced it. A line that says no run by its id (a
/// question's changes) names it only where it ends with those words whole.
fn replaced_words(led: &Ledger, replacers: &IndexMap<String, String>, names: &IndexMap<String, String>) -> Res<Vec<(String, String)>> {
    let mut out = Vec::new();
    for r in led.all::<Run>()?.iter() {
        let (Some(x), Some(ret)) = (replacers.get(&r.id), r.retracted.as_ref()) else { continue };
        let Some(w) = names.get(x) else { continue };
        let given = view::retraction_words(ret);
        let mut said = Value::str(given.clone());
        name_replaced(&mut said, &[(x.clone(), w.clone())].into_iter().collect());
        if let Value::Str(said) = said {
            if said != given {
                out.push((given, said));
            }
        }
    }
    Ok(out)
}

/// Each text of `v` that ends with a retraction's words rb wrote (`replacing`), those words with the run named.
fn said_replaced(v: &mut Value, replacing: &[(String, String)]) {
    match v {
        Value::Str(s) => {
            if let Some((given, said)) = replacing.iter().find(|(g, _)| s.ends_with(g.as_str())) {
                *s = format!("{}{said}", &s[..s.len() - given.len()]);
            }
        }
        Value::List(xs) => xs.iter_mut().for_each(|x| said_replaced(x, replacing)),
        Value::Dict(d) => d.values_mut().for_each(|x| said_replaced(x, replacing)),
        _ => {}
    }
}

/// Each run of `runs` (the document's) by its words: "run int8 · seed 1", "a run of e1" where it has none. Two runs whose
/// words would read alike are each said with when it was recorded ("run int8 · seed 1, recorded 2026-10-14 09:25 UTC"),
/// to the second where the minute is shared, and by its place among them where that is shared too ("..., the 2nd of
/// 2", in the order of `times`, the log's): a line never names two runs as one.
fn run_names(runs: &Dict, times: &IndexMap<String, String>) -> IndexMap<String, String> {
    let base = |r: &Value| {
        let label = text(r, "label");
        if label.is_empty() { format!("a run of {}", text(r, "experiment_id")) } else { format!("run {label}") }
    };
    let said: IndexMap<String, String> = runs.iter().map(|(id, r)| (id.clone(), base(r))).collect();
    let mut out = IndexMap::new();
    for (id, words) in &said {
        let twins: Vec<&String> = said.iter().filter(|(_, w)| *w == words).map(|(k, _)| k).collect();
        if twins.len() < 2 {
            out.insert(id.clone(), words.clone());
            continue;
        }
        let at = |k: &str| times.get(k).cloned().unwrap_or_default();
        let to_minute = |k: &str| minute(&at(k));
        let to_second = |k: &str| match crate::pytime::parse_iso(&at(k)) {
            Some(t) => t.with_timezone(&chrono::Utc).format("%Y-%m-%d %H:%M:%S UTC").to_string(),
            None => at(k),
        };
        let when = if twins.iter().filter(|k| to_minute(k) == to_minute(id)).count() > 1 { to_second(id) } else { to_minute(id) };
        let mut alike: Vec<&&String> = twins.iter().filter(|k| to_second(k) == to_second(id)).collect();
        alike.sort_by_key(|k| times.get_index_of(k.as_str()).unwrap_or(usize::MAX));
        let place = if alike.len() > 1 {
            let n = alike.iter().position(|k| **k == id).unwrap_or(0) + 1;
            format!(", the {} of {}", ordinal(n), alike.len())
        } else {
            String::new()
        };
        out.insert(id.clone(), format!("{words}, recorded {when}{place}"));
    }
    out
}

/// "1st", "2nd", "3rd", "4th", "11th", "22nd".
fn ordinal(n: usize) -> String {
    let end = match (n % 10, n % 100) {
        (_, 11..=13) => "th",
        (1, _) => "st",
        (2, _) => "nd",
        (3, _) => "rd",
        _ => "th",
    };
    format!("{n}{end}")
}

/// Each text of `v` a reader reads as rb's words (one that holds a space; an id, which a link or a test reads, holds
/// none) with each id `names` holds said by what it is. A command keeps its ids: it is typed as it reads; and so does
/// what someone wrote (a statement, a question's text, a title, words given, a quote), which travels verbatim.
fn name_in_prose(v: &mut Value, names: &IndexMap<String, String>, replacing: &[(String, String)]) {
    const THEIRS: &[&str] = &["statement", "text", "title", "your_words", "words", "quote", "note", "why"];
    match v {
        // a question's changes say a retraction's reason as given, after rb's words
        Value::Dict(d) if d.contains_key("changed") => {
            for (k, x) in d.iter_mut().filter(|(k, _)| !THEIRS.contains(&k.as_str())) {
                if k == "changed" { said_replaced(x, replacing) } else { name_in_prose(x, names, replacing) }
            }
        }
        Value::Str(s) if rbs(s) => *s = crate::cmds::reading::name_ids(s, &|id| names.get(id).cloned()),
        Value::List(xs) => xs.iter_mut().for_each(|x| name_in_prose(x, names, replacing)),
        Value::Dict(d) => {
            // a line of rb's that says what someone wrote beside it (an interpretation's line, its when_met words) keeps
            // those words as they read in their own field
            let kept: Vec<String> = THEIRS.iter().filter_map(|k| d.get(*k).and_then(Value::as_str)).filter(|t| !t.trim().is_empty()).map(str::to_string).collect();
            for (_, x) in d.iter_mut().filter(|(k, _)| !THEIRS.contains(&k.as_str())) {
                match x {
                    Value::Str(s) if rbs(s) && kept.iter().any(|t| s.contains(t.as_str())) => *s = name_around(s, &kept, names),
                    x => name_in_prose(x, names, replacing),
                }
            }
        }
        _ => {}
    }
}

/// Whether a text reads as rb's words: it holds a space (an id, which a link or a test reads, holds none) and is no
/// command.
fn rbs(s: &str) -> bool {
    s.contains(' ') && !s.starts_with("rb ") && !s.starts_with("cd ")
}

/// `s` with each id `names` holds said by what it is, but inside each of the texts `kept`, which stay as written.
fn name_around(s: &str, kept: &[String], names: &IndexMap<String, String>) -> String {
    let name = |x: &str| crate::cmds::reading::name_ids(x, &|id| names.get(id).cloned());
    // the earliest kept text, the longest where two start at one place
    let hit = kept.iter().filter_map(|t| s.find(t.as_str()).map(|i| (i, t))).min_by_key(|(i, t)| (*i, std::cmp::Reverse(t.len())));
    match hit {
        Some((i, t)) => format!("{}{t}{}", name(&s[..i]), name_around(&s[i + t.len()..], kept, names)),
        None => name(s),
    }
}

/// Each text of `v` that says rb's "replaced by <run>" (at its start, or after "retracted: " or a session's "note: "), with
/// that run said by what it is; the rest of the text, a reason someone gave, as written.
fn name_replaced(v: &mut Value, names: &IndexMap<String, String>) {
    match v {
        Value::Str(s) => {
            // rb's words come first: the earliest of the places they open at
            let at = if s.starts_with("replaced by ") {
                Some(0)
            } else {
                ["retracted: ", "note: "].iter().filter_map(|p| s.find(&format!("{p}replaced by ")).map(|i| i + p.len())).min()
            };
            let Some(at) = at else { return };
            let (head, tail) = s.split_at(at);
            if let Some((id, rest)) = tail.strip_prefix("replaced by ").map(|r| r.split_at(r.find(':').unwrap_or(r.len()))) {
                if let Some(words) = names.get(id) {
                    *s = format!("{head}replaced by {words}{rest}");
                }
            }
        }
        Value::List(xs) => xs.iter_mut().for_each(|x| name_replaced(x, names)),
        Value::Dict(d) => d.values_mut().for_each(|x| name_replaced(x, names)),
        _ => {}
    }
}

/// Whether `id` has the shape of one rb minted for a kind: its prefix and six base-32 characters, not all digits.
fn minted(id: &str, prefix: &str) -> bool {
    id.strip_prefix(prefix).is_some_and(|t| t.len() == 6 && t.chars().all(|c| matches!(c, 'a'..='z' | '2'..='7')) && !t.chars().all(|c| c.is_ascii_digit()))
}

/// The kinds of the record's objects a log line can name.
const OBJECTS: &[&str] = &["question", "experiment", "criterion", "run", "resource", "analysis"];

/// The ids of the objects the document holds, which a screen can link: its questions, experiments, findings, runs,
/// resources and analyses. A criterion or an analysis taken back is not one.
fn held_ids(doc: &Dict) -> std::collections::HashSet<String> {
    let mut held = std::collections::HashSet::new();
    for k in ["questions", "experiments"] {
        held.extend(doc.get(k).and_then(Value::as_list).into_iter().flatten().map(|x| text(x, "id")));
    }
    for k in ["findings", "runs", "resources", "analyses"] {
        held.extend(doc.get(k).and_then(Value::as_dict).into_iter().flat_map(|d| d.keys().cloned()));
    }
    held.remove("");
    held
}

/// Every write of the log, newest first, in its screen form: its time to the minute in UTC, its op, what rb log's text
/// says of it after the time (never who wrote it), the ids it names that the document holds (`held`) and its line in rb
/// log --json. A session's line records no write and is not one.
fn log_rows(led: &Ledger, held: &std::collections::HashSet<String>, names: &IndexMap<String, String>) -> Res<Value> {
    let entries = led.log_entries()?;
    let mut rows: Vec<Value> = Vec::new();
    for (i, r) in entries.iter().enumerate().rev().filter(|(_, r)| !crate::ledger::is_session_line(r)) {
        let v = Value::Dict(r.clone());
        let detail = v.get("detail").cloned().unwrap_or(Value::Null);
        let mut refs: Vec<String> = Vec::new();
        if OBJECTS.contains(&text(&v, "kind").as_str()) {
            refs.push(text(&v, "id"));
        }
        for k in ["subject", "question", "parent", "experiment", "replaces", "target"] {
            if let Some(Value::Str(id)) = detail.get(k) {
                refs.push(id.clone());
            }
        }
        refs.retain(|x| held.contains(x));
        let mut seen = std::collections::HashSet::new();
        refs.retain(|x| seen.insert(x.clone()));
        // a note taken back is said by what it is on ("a note on e1"), never by its id
        let note = detail.get("note").and_then(Value::as_str).unwrap_or_default().to_string();
        let said = crate::cmds::reading::log_said_as(r, None, &|id| names.get(id).cloned().or_else(|| (!note.is_empty() && id == note).then(|| "a note".to_string())));
        rows.push(crate::obj! {
            "at" => minute(&text(&v, "at")), "op" => text(&v, "op"), "sentence" => said, "refs" => Value::list_of_str(refs),
            "log_index" => i
        });
    }
    Ok(Value::List(rows))
}

/// Where rb open shows the record (Live only): the place, in short and by its whole path, whether it is the place rb
/// open started on and whether it is a past version, read only, and the line the top bar says of it; every place of
/// the clone and whether it can be shown; the versions of the place's record. A commit's subject is never read: it can
/// name a writer.
fn places_value(at: &At) -> Value {
    let (c, listed) = (at.chosen, at.listed);
    let p = &c.place;
    let many = listed.len() > 1;
    let alone = !many && c.version.is_none();
    // a single checkout is named by its branch, else its folder, on the top bar and in the Where picker, live or at a
    // past version; its whole path is place.path, which their tooltips say. At a past version place.label keeps the
    // short form, which the scope bar's line says
    let short = if alone { alone_label(p) } else { p.label_short() };
    let line = match &c.version {
        Some(_) => String::new(),
        None if alone => short.clone(),
        None if p.record == "this" || p.said().is_empty() => short.clone(),
        None => format!("{short}: {}", p.said()),
    };
    let version = c.version.as_ref().map_or(Value::Null, |v| crate::obj! {"sha" => v.sha.clone(), "at" => minute(&v.committed), "subject" => "", "shown" => v.shown_short(p)});
    let versions = p.root.as_ref().map(|r| places::versions(r)).unwrap_or_default();
    crate::obj! {
        "place" => crate::obj! {
            "id" => p.id.clone(), "label" => short, "path" => p.listed.clone(), "start" => c.start, "read_only" => c.version.is_some(), "version" => version,
            "line" => line
        },
        "places" => Value::List(listed.iter().map(|x| crate::obj! {
            "id" => x.id.clone(), "label" => if many { x.label_short() } else { alone_label(x) }, "can_show" => x.readable(),
            "why_not" => if x.readable() { String::new() } else { x.not_shown() }
        }).collect()),
        "versions" => Value::List(versions.iter().map(|v| crate::obj! {"sha" => v.sha.clone(), "at" => minute(&v.committed), "subject" => ""}).collect()),
        // rb open's own rows of what waits in each other place come with its wiring (A's)
        "elsewhere" => Value::List(Vec::new())
    }
}

/// A single checkout as its top bar names it: its branch, else its folder's name. Its path, whole, is said apart.
fn alone_label(p: &Place) -> String {
    match &p.branch {
        Some(b) => b.clone(),
        None => p.listed.trim_end_matches('/').rsplit('/').next().unwrap_or(&p.listed).to_string(),
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_reason_names_records_by_their_words() {
        let words: IndexMap<String, String> = [("c1", "INT8 keeps top-1 within a point"), ("c12", "INT8 answers in 7 ms or less"), ("e1", "int8 against fp32")]
            .iter().map(|(a, b)| (a.to_string(), b.to_string())).collect();
        assert_eq!(in_words("moved since: c1, c12", &words), "moved since: INT8 keeps top-1 within a point, INT8 answers in 7 ms or less");
        assert_eq!(in_words("e1's c1x and run-c1 stay", &words), "int8 against fp32's c1x and run-c1 stay");
        // an id ending a sentence is the id; a folder in a path, a file's extension or a version is not
        assert_eq!(in_words("moved since: c1.", &words), "moved since: INT8 keeps top-1 within a point.");
        assert_eq!(in_words("out/e1/audit.py and e1.json and v1.c1", &words), "out/e1/audit.py and e1.json and v1.c1");
    }

    /// FIX8: rb's "replaced by <run>" names the run after a session's "note: " too; the reason after it stays as given.
    #[test]
    fn a_replaced_run_is_named_after_a_sessions_note() {
        let names: IndexMap<String, String> = [("runabcdef".to_string(), "run int8 · seed 2".to_string())].into_iter().collect();
        for (given, want) in [
            ("retracted · note: replaced by runabcdef: the first read", "retracted · note: replaced by run int8 · seed 2: the first read"),
            ("retracted: replaced by runabcdef: x", "retracted: replaced by run int8 · seed 2: x"),
            ("replaced by runabcdef", "replaced by run int8 · seed 2"),
            ("a reason that says runabcdef", "a reason that says runabcdef"),
        ] {
            let mut v = Value::str(given);
            name_replaced(&mut v, &names);
            assert_eq!(v.as_str(), Some(want));
        }
    }

    /// FIX9: an analysis with no title is "an analysis" where a sentence names it, never "analysis an analysis" or "the
    /// analysis an analysis", and so is a run with no label ("a run of e1"); a word that only ends in the kind keeps it.
    #[test]
    fn an_untitled_analysis_and_an_unlabelled_run_are_said_with_their_article() {
        let names: IndexMap<String, String> =
            [("a6pqyge", "an analysis"), ("runk2xa9q", "a run of e1"), ("runj53tal", "run int8 · seed 1")].into_iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        let say = |t: &str| crate::cmds::reading::name_ids(t, &|id| names.get(id).cloned());
        assert_eq!(say("reads the analysis a6pqyge."), "reads an analysis.");
        assert_eq!(say("analyze analysis a6pqyge"), "analyze an analysis");
        assert_eq!(say("counted in run runk2xa9q"), "counted in a run of e1");
        assert_eq!(say("run runj53tal and rerun runj53tal"), "run int8 · seed 1 and rerun run int8 · seed 1");
    }

    /// FIX8: two runs whose words read alike are each said with when it was recorded, to the second where the minute is
    /// shared, and by their place where that is too; a run alone keeps its words.
    #[test]
    fn two_runs_with_one_label_are_said_apart() {
        let run = |label: &str| crate::obj! {"label" => label, "experiment_id" => "e1"};
        let runs: Dict = [("r1", run("int8 · seed 1")), ("r2", run("int8 · seed 1")), ("r3", run("int8 · seed 2")), ("r4", run("int8 · seed 1"))]
            .into_iter().map(|(k, v)| (k.to_string(), v)).collect();
        let times: IndexMap<String, String> = [("r1", "2026-10-14T09:25:10Z"), ("r2", "2026-10-14T09:31:00Z"), ("r3", "2026-10-14T09:25:10Z"), ("r4", "2026-10-14T09:25:40Z")]
            .into_iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        let n = run_names(&runs, &times);
        assert_eq!(n["r1"], "run int8 · seed 1, recorded 2026-10-14 09:25:10 UTC");
        assert_eq!(n["r2"], "run int8 · seed 1, recorded 2026-10-14 09:31 UTC");
        assert_eq!(n["r3"], "run int8 · seed 2");
        assert_eq!(n["r4"], "run int8 · seed 1, recorded 2026-10-14 09:25:40 UTC");
        let same: IndexMap<String, String> = ["r1", "r2", "r3", "r4"].into_iter().map(|k| (k.to_string(), "2026-10-14T09:25:10Z".to_string())).collect();
        let n = run_names(&runs, &same);
        assert_eq!((n["r1"].as_str(), n["r4"].as_str()), ("run int8 · seed 1, recorded 2026-10-14 09:25:10 UTC, the 1st of 3", "run int8 · seed 1, recorded 2026-10-14 09:25:10 UTC, the 3rd of 3"));
        assert_eq!((ordinal(2), ordinal(11), ordinal(22), ordinal(13)), ("2nd".into(), "11th".into(), "22nd".into(), "13th".into()));
    }

    /// FIX8: a line of rb's that says someone's words beside them (an interpretation's line and its text) keeps those
    /// words as written, ids and all; the rest of the line names what rb minted an id for.
    #[test]
    fn a_line_keeps_the_words_its_text_holds() {
        let names: IndexMap<String, String> = [("runabcdef".to_string(), "run int8 · seed 2".to_string())].into_iter().collect();
        let mut v = crate::obj! {"line" => "Note: INT8 ships, as runabcdef shows. runabcdef counts.", "text" => "INT8 ships, as runabcdef shows", "holds" => "runabcdef counts."};
        name_in_prose(&mut v, &names, &[]);
        assert_eq!(text(&v, "line"), "Note: INT8 ships, as runabcdef shows. run int8 · seed 2 counts.");
        assert_eq!(text(&v, "text"), "INT8 ships, as runabcdef shows");
        assert_eq!(text(&v, "holds"), "run int8 · seed 2 counts.");
    }

    #[test]
    fn the_strip_says_runs_have_no_command_from_when_they_ran() {
        assert_eq!(super::without_command_line(1, 1), "The one run has no command from when it ran.");
        assert_eq!(super::without_command_line(22, 22), "None of the 22 runs has a command from when it ran.");
        assert_eq!(super::without_command_line(1, 22), "1 of 22 runs has no command from when it ran.");
        assert_eq!(super::without_command_line(3, 22), "3 of 22 runs have no command from when they ran.");
    }

    use super::*;

    fn finding_of(experiment: &str, state: &str, label: &str, baseline: bool) -> Value {
        crate::obj! {"experiment" => experiment, "state" => state, "subject_label" => label, "reads_baseline" => baseline}
    }

    /// FIX4: a variant by its plain name and a metric by its title, as the grid, the runs table and the cards say them.
    #[test]
    fn a_variant_and_a_metric_are_said_by_their_plain_names() {
        assert_eq!(plain("step_by_step"), "step by step");
        assert_eq!(plain("fp32 baseline"), "FP32 baseline");
        assert_eq!(plain("the int8 baseline"), "the INT8 baseline");
        assert_eq!(plain("fp32_ref"), "fp32 ref");
        assert_eq!(plain("resnet50"), "resnet50");
        assert_eq!(metric_said("onnx_top1"), "ONNX top-1");
        assert_eq!(metric_said("int8_top1"), "INT8 top-1");
        assert_eq!(metric_said("gpu_mfu"), "GPU MFU");
        assert_eq!(metric_said("Calib_loss"), "Calib loss");
        assert_eq!(metric_said("top1"), "Top-1");
        assert_eq!(metric_said("latency_ms"), "Latency ms");
    }

    /// DESIGN-PASS-1 P5: a sentence's numbers have 2 decimals (a small one keeps 2 significant figures, never "0"), an
    /// interval's bounds their signs, and a difference says its direction in words, once, in points for a %.
    #[test]
    fn a_sentences_numbers_and_directions_are_plain() {
        assert_eq!((dec2(90.4833), dec2(-0.0767), dec2(7.1), dec2(0.0012), dec2(-0.0)), ("90.48".into(), "-0.08".into(), "7.10".into(), "0.0012".into(), "0.00".into()));
        assert_eq!((signedp(0.07, 2), signedp(-0.2312, 2)), ("+0.07".to_string(), "-0.23".to_string()));
        assert_eq!(differs_by("int8", "the fp32 baseline", -0.0767, "%", 2), "int8 is 0.08 points lower than the fp32 baseline");
        assert_eq!(differs_by("int8", "fp32", 0.9667, "ms", 2), "int8 is 0.97 ms higher than fp32");
        assert_eq!(differs_by("int8", "fp32", 0.001, "", 2), "int8 is 0.0010 higher than fp32");
        assert_eq!(differs_by("int8", "fp32", 0.0, "%", 2), "int8 is 0.00 points away from fp32");
        // a unit spelled out as a percentage is in points too
        assert_eq!(differs_by("step by step", "plain", 3.6, "percent", 2), "step by step is 3.60 points higher than plain");
    }

    fn said(v: Verdict) -> String {
        verdict(&v)
    }

    /// FIX2 (R's request 126): a reading is said with the rule's digits where 2 decimals would round it onto or past
    /// the rule's number: a miss at +0.05000004 over at most 0.05 never reads "0.05".
    #[test]
    fn a_reading_never_rounds_onto_its_rule() {
        assert_eq!(rule_places(&[0.05000004], &[0.05], 2), 8);
        assert_eq!(rule_places(&[0.0433, 0.03, 0.06], &[0.05], 2), 2);
        assert_eq!(rule_places(&[0.0467], &[0.05], 2), 3);
        assert_eq!(rule_places(&[0.05], &[0.05], 2), 2);
        assert_eq!(rule_places(&[4.996], &[5.0], 2), 3);
        assert_eq!(rule_places(&[0.149], &[0.05, 0.15], 2), 3);
        let miss = Verdict { state: "misses", result: "point_only", subject: "int8", reference: "fp32", unit: "", comparator: "at_most", target: Some(0.05),
            estimate: Some(0.05000004), interval: None, direction: "minimize", ..base() };
        let v = said(miss);
        assert!(v.starts_with("Not met: int8 is 0.05000004 above fp32."), "{v}");
        assert!(!v.contains(" 0.05 above"), "{v}");
        // a noninferior rule's worse end, and a band's distance, keep their own side of the rule's number (review)
        let ni = Verdict { state: "meets", result: "loss", subject: "int8", reference: "fp32", unit: "", comparator: "noninferior", target: None, margin: Some(0.3),
            estimate: Some(0.29996), interval: Some((0.29994, 0.29998)), direction: "minimize", ..base() };
        let v = said(ni);
        assert!(v.contains("its worse end, +0.29998, is inside the 0.3 the rule allows"), "{v}");
        let eq = Verdict { state: "misses", result: "gain", subject: "int8", reference: "fp32", unit: "", comparator: "equals", target: Some(0.5), tolerance: Some(0.05),
            estimate: Some(0.55002), interval: None, ..base() };
        let v = said(eq);
        assert!(v.contains("0.05002 from the 0.5 the rule asks for, outside its ± 0.05"), "{v}");
    }

    /// FIX2 item 3, review: a question whose rules are linked to its sub-questions says so, never that no rule tests it.
    #[test]
    fn a_parent_question_says_its_sub_questions_rules() {
        let fs = crate::obj! {"c1" => finding_of("e1", "misses", "int8", false), "c2" => finding_of("e1", "meets", "int8", false)};
        let below = ["q1a".to_string(), "c1".to_string(), "c2".to_string(), "c1".to_string(), "e1".to_string()];
        assert_eq!(no_rule_line("q1", &[], &below, &[], &fs, false), "No rule is linked to this question itself; 2 rules are linked to its sub-questions.");
        assert_eq!(no_rule_line("q1", &[], &["q1a".to_string()], &[], &fs, false), "No rule tests this question yet.");
    }

    fn base<'a>() -> Verdict<'a> {
        Verdict { state: "misses", rule_said: "criterion not met", why: "", result: "gain", subject: "step by step", reference: "plain",
            estimate: Some(3.6), interval: Some((0.619, 6.581)), unit: "percent", cited: false, comparator: "at_least", over: "mean",
            target: Some(5.0), tolerance: None, margin: None, direction: "maximize", ratio: false, cells: false, missed: 0, n: 3, problem: "" }
    }

    /// PLAIN-WORDS-PASS-4: a rule's verdict is one sentence: its status word first, the reading, rb's result class in
    /// words (never the class word alone), and how far it is from the rule's number.
    #[test]
    fn a_verdict_says_status_reading_class_and_distance() {
        assert_eq!(said(base()), "Not met: step by step is 3.60 points above plain (+0.62 to +6.58). A real gain, but 1.40 points short of the 5 points the rule asks for.");
        assert_eq!(said(Verdict { state: "meets", estimate: Some(6.4), interval: Some((5.5, 7.3)), ..base() }),
            "Met: step by step is 6.40 points above plain (+5.50 to +7.30). A real gain. It is 1.40 points past the 5 points the rule asks for.");
        assert_eq!(said(Verdict { state: "meets", rule_said: "estimate meets criterion", result: "point_only", estimate: Some(6.4), interval: None, ..base() }),
            "Met on its estimate: step by step is 6.40 points above plain. There is no interval yet. It is 1.40 points past the 5 points the rule asks for.");
        assert_eq!(said(Verdict { result: "unresolved", interval: Some((-1.0, 8.2)), ..base() }),
            "Not met: step by step is 3.60 points above plain (-1.00 to +8.20). The runs can't tell it from no difference. It is 1.40 points short of the 5 points the rule asks for.");
        // an upper limit on a loss, in ms
        let ms = Verdict { state: "meets", result: "loss", subject: "int8", reference: "fp32", unit: "ms", comparator: "at_most", target: Some(0.5),
            estimate: Some(0.2), interval: Some((0.1, 0.3)), direction: "minimize", ..base() };
        assert_eq!(said(ms), "Met: int8 is 0.20 ms above fp32 (+0.10 to +0.30). A real loss, but 0.30 ms under the 0.5 ms the rule allows.");
        // a level of a percentage, and a cited one
        let level = Verdict { state: "not reproduced", cited: true, result: "measured", subject: "plain", reference: "", comparator: "equals",
            target: Some(49.6), tolerance: Some(0.05), estimate: Some(13.07), interval: Some((11.0, 15.14)), ..base() };
        // FIX4: a level's bounds each with its unit, as the list and the short sentence say them
        assert_eq!(said(level), "Not reproduced: plain is 13.07% (11.00% to 15.14%). One arm measured on its own. It is 36.53 points from the cited 49.6%, outside its ± 0.05 points.");
        let ms_level = Verdict { state: "misses", result: "measured", subject: "INT8", reference: "", unit: "ms", comparator: "at_most", target: Some(7.0),
            estimate: Some(7.1), interval: Some((6.85, 7.35)), direction: "minimize", ..base() };
        let ms_said = said(ms_level);
        assert!(ms_said.starts_with("Not met: INT8 is 7.10 ms (6.85 ms to 7.35 ms)."), "{ms_said}");
        // a rule rb can't judge says why, in the rule's words, and no class
        assert_eq!(said(Verdict { state: "undetermined", why: "2 units; the criterion needs 3", result: "point_only", interval: None, ..base() }),
            "Can't tell yet: step by step is 3.60 points above plain. 2 units; the rule needs 3.");
        assert_eq!(said(Verdict { state: "untested", estimate: None, interval: None, result: "untested", ..base() }), "Not tested: no runs are read yet.");
        // a gate, an --each rule, a bound, a margin, an interval clear of 0 asked for
        assert!(said(Verdict { state: "gate missed", ..base() }).starts_with("Gate missed: "));
        assert_eq!(said(Verdict { state: "gate untested", estimate: None, interval: None, ..base() }), "Gate, not tested: no runs are read yet.");
        // an equals rule over every unit, or over its interval's ends, never says the estimate's distance
        let eq = Verdict { comparator: "equals", target: Some(0.0), tolerance: Some(0.1), unit: "", estimate: Some(0.07), interval: Some((-0.2, 0.3)), result: "unresolved", ..base() };
        assert_eq!(said(Verdict { over: "each", missed: 1, ..eq }),
            "Not met: step by step is 0.07 above plain (-0.20 to +0.30). The runs can't tell it from no difference. 1 of 3 units is outside 0 ± 0.1.");
        assert_eq!(said(Verdict { over: "bound", estimate: Some(0.0), interval: Some((-0.75, 0.75)), ..eq }),
            "Not met: step by step is 0.00 away from plain (-0.75 to +0.75). The runs can't tell it from no difference. Its interval reaches outside 0 ± 0.1.");
        assert_eq!(said(Verdict { over: "bound", state: "meets", estimate: Some(0.01), interval: Some((-0.05, 0.07)), ..eq }),
            "Met: step by step is 0.01 above plain (-0.05 to +0.07). The runs can't tell it from no difference. Its interval is inside 0 ± 0.1, as the rule asks.");
        // a tolerance on a percentage is in points; across design cells no interval is to come
        assert!(said(Verdict { comparator: "equals", tolerance: Some(0.05), ..base() }).ends_with("outside its ± 0.05 points."));
        assert_eq!(said(Verdict { cells: true, result: "point_only", interval: None, ..base() }),
            "Not met: step by step is 3.60 points above plain on average across its design cells. No interval is computed across design cells.");
        assert_eq!(said(Verdict { over: "each", missed: 1, ..base() }),
            "Not met: step by step is 3.60 points above plain (+0.62 to +6.58). A real gain, but 1 of 3 units is below the 5 points the rule asks for.");
        assert_eq!(said(Verdict { over: "bound", ..base() }),
            "Not met: step by step is 3.60 points above plain (+0.62 to +6.58). A real gain, but its lower end is 4.38 points short of the 5 points the rule asks for.");
        assert_eq!(said(Verdict { state: "meets", comparator: "noninferior", margin: Some(1.0), result: "loss", interval: Some((-0.27, -0.04)), estimate: Some(-0.15), ..base() }),
            "Met: step by step is 0.15 points below plain (-0.27 to -0.04). A real loss, but its worse end, -0.27, is inside the 1 point the rule allows.");
        assert_eq!(said(Verdict { comparator: "better", result: "unresolved", interval: Some((-1.0, 8.2)), ..base() }),
            "Not met: step by step is 3.60 points above plain (-1.00 to +8.20). The runs can't tell it from no difference. The rule asks for an interval clear of 0 on the better side.");
        // numbers that differ read apart: a level's interval and a cited number close to it
        let close = Verdict { state: "not reproduced", cited: true, result: "measured", subject: "fp32", reference: "", unit: "", comparator: "equals",
            target: Some(0.7613), tolerance: Some(0.001), estimate: Some(0.7597), interval: Some((0.7591, 0.7603)), ..base() };
        assert_eq!(said(close), "Not reproduced: fp32 is 0.7597 (0.7591 to 0.7603). One arm measured on its own. It is 0.0016 from the cited 0.7613, outside its ± 0.001.");
        // a ratio says no unit
        assert_eq!(said(Verdict { ratio: true, state: "misses", result: "point_only", unit: "ms", comparator: "at_most", target: Some(0.6), estimate: Some(0.64), interval: None,
            subject: "int8", reference: "fp32", ..base() }), "Not met: the ratio of int8 to fp32 is 0.64. There is no interval yet. It is 0.04 over the 0.6 the rule allows.");
        // a problem with what it rests on is said after
        assert!(said(Verdict { problem: "a source changed", ..base() }).ends_with(" What it rests on has a problem: a source changed."));
        // no class word stands alone as the status
        for r in ["gain", "loss", "unresolved", "equivalent", "point_only", "measured", "higher", "lower"] {
            let v = said(Verdict { result: r, ..base() });
            let status = v.split(':').next().unwrap();
            assert!(!status.to_lowercase().contains(&crate::assess::result_word(r).to_string()), "{v}");
            assert!(!crate::assess::result_phrase(r).is_empty() && v.contains(crate::assess::result_phrase(r)), "{v}");
        }
    }

    /// PLAIN-WORDS-PASS-3: a queue row's rule state names its subject.
    #[test]
    fn a_rule_state_names_the_rule() {
        let said = |l: &str| rule_state(&crate::obj! {"state_label" => l});
        assert_eq!([said("met"), said("not met"), said("can't tell yet"), said("not tested"), said("not reproduced"), said("gate missed")],
            ["Rule met", "Rule not met", "Rule: can't tell yet", "Rule: not tested", "Not reproduced", "Gate missed"]);
    }

    /// DESIGN-PASS-1 P8: a missed gate's call gives its reason as its gates' readings say them, never rb's comparison
    /// text; any other call's reason stays as rb gives it.
    #[test]
    fn a_missed_gates_reason_is_its_reading() {
        let fs = crate::obj! {"g1" => crate::obj! {"experiment" => "e2", "state" => "gate missed", "reading_short" => "INT8 is 0.97 ms lower than the FP32 baseline (-1.25 to -0.68)"},
            "c1" => crate::obj! {"experiment" => "e2", "state" => "misses", "reading_short" => "int8 is 7.10 ms"}};
        let call = |code: &str| crate::obj! {"subject" => "e2", "code" => code, "reason" => crate::obj! {"label" => "Gate missed", "detail" => "change.latency_ms: -0.9667 [-1.254, -0.6798] ms · paired difference (rb)", "also" => ""}};
        let said = |c: Value| text(c.get("reason").unwrap(), "detail");
        assert_eq!(said(gate_reason_plain(call("gate_missed"), &fs)), "INT8 is 0.97 ms lower than the FP32 baseline (-1.25 to -0.68)");
        assert!(said(gate_reason_plain(call("undecided"), &fs)).starts_with("change.latency_ms"));
        let two = crate::obj! {"g1" => crate::obj! {"experiment" => "e2", "state" => "gate missed", "statement" => "INT8 runs 20% faster", "reading_short" => "int8 is 0.97 ms lower than fp32"},
            "g2" => crate::obj! {"experiment" => "e2", "state" => "gate missed", "statement" => "INT8 fits in 30 MB", "reading_short" => "int8 is 31.00 MB"}};
        assert_eq!(said(gate_reason_plain(call("gate_missed"), &two)), "INT8 runs 20% faster: int8 is 0.97 ms lower than fp32; INT8 fits in 30 MB: int8 is 31.00 MB");
    }

    fn card(findings: &Value, ids: &[&str], lead: Value, holds: &str) -> Value {
        let ids: Vec<String> = ids.iter().map(|s| s.to_string()).collect();
        let q = crate::obj! {"id" => "q1", "text" => "Does INT8 cost ResNet-50 top-1 on ImageNet?", "lead" => lead,
            "overview" => crate::obj! {"interpretations" => Value::List(vec![crate::obj! {"holds" => holds}])}};
        standing_of(&q, "open", &ids, &tallies_of(findings, &ids), findings, &[], &[], &[], false)
    }

    /// A missed gate's row says which gates miss, by statement (Look 3: never by id), never their whole reading.
    #[test]
    fn missed_gates_are_named_in_one_line() {
        let gate = |e: &str, state: &str, said: &str| {
            let mut f = finding_of(e, state, "int8", false);
            if let Value::Dict(d) = &mut f {
                d.insert("statement".into(), said.into());
            }
            f
        };
        let fs = crate::obj! {"g1" => gate("e3", "gate missed", "INT8 answers in 7 ms or less"), "g2" => gate("e3", "gate missed", "INT8 fits in 30 MB"),
            "g3" => gate("e3", "gate met", "INT8 exports to ONNX"), "g4" => gate("e4", "gate missed", "INT8 answers in 5 ms or less")};
        assert_eq!(gates_missed(&fs, "e3").as_deref(), Some("2 gates missed: INT8 answers in 7 ms or less · INT8 fits in 30 MB"));
        assert_eq!(gates_missed(&fs, "e4").as_deref(), Some("Gate missed: INT8 answers in 5 ms or less"));
        assert_eq!(gates_missed(&fs, "e5"), None);
    }

    /// An open question whose every criterion meets its rule says the latest call that keeps it open, never that no call
    /// is recorded; with no call it says none is.
    #[test]
    fn an_open_question_says_the_call_that_keeps_it_open() {
        let fs = crate::obj! {"c1" => finding_of("e1", "meets", "int8", false)};
        let ids = vec!["c1".to_string()];
        let q = crate::obj! {"id" => "q1", "text" => "Does INT8 cost ResNet-50 top-1 on ImageNet?", "lead" => Value::Null};
        let made = [crate::obj! {"subject" => "q1", "line" => "continued 2026-10-21: one more seed"}];
        let said = |made: &[Value]| text(&standing_of(&q, "open", &ids, &tallies_of(&fs, &ids), &fs, &[], made, &[], false), "line");
        assert_eq!(said(&made), "No answer yet. The latest call on it: continued 2026-10-21: one more seed");
        assert_eq!(said(&[]), "No answer yet: no call on it is recorded.");
    }

    /// A call proposed on a sub-question waits within its top-level question's tree: its card counts it.
    #[test]
    fn a_call_waiting_on_a_sub_question_is_a_reason_on_its_top_question() {
        let fs = Value::empty_dict();
        let q = crate::obj! {"id" => "q1", "text" => "Does INT8 cost ResNet-50 top-1 on ImageNet?", "lead" => Value::Null};
        let pending = [crate::obj! {"id" => "dec1", "kind" => "proposed", "subject" => "q1a"}];
        let card = standing_of(&q, "open", &[], &tallies_of(&fs, &[]), &fs, &pending, &[], &["q1a".to_string()], false);
        // FIX2 item 3: a question no rule is linked to reads as its own view says it, then what waits
        assert_eq!(text(&card, "line"), "No rule tests this question yet. 1 call waits for your confirmation.");
        assert_eq!(text(&card, "line"), no_rule_line("q1", &[], &["q1a".to_string()], &pending, &fs, false));
        // a page that leaves the machine says it in no one's voice
        let away = standing_of(&q, "open", &[], &tallies_of(&fs, &[]), &fs, &pending, &[], &["q1a".to_string()], true);
        assert_eq!(text(&away, "line"), "No rule tests this question yet. 1 call waits for a confirmation.");
        let design = [crate::obj! {"id" => "e3@ab", "kind" => "design", "subject" => "e3"}];
        let one = standing_of(&q, "open", &[], &tallies_of(&fs, &[]), &fs, &design, &[], &["e3".to_string()], false);
        assert_eq!(text(&one, "line"), "No rule tests this question yet. 1 experiment design waits for your confirmation.");
        let none = standing_of(&q, "open", &[], &tallies_of(&fs, &[]), &fs, &[], &[], &[], false);
        assert_eq!(text(&none, "line"), "No rule tests this question yet.");
    }

    /// A gate waiting apart is named by its experiment and its title, never by the hash its confirmation names.
    #[test]
    fn a_gate_row_names_its_experiment_without_its_hash() {
        let p = crate::obj! {"id" => "e2@fd981c33ec987d5611ba865019a19aadfc47c866b7e62ae523916f9193514aed", "kind" => "gate", "text" => "e2 gates: if it holds: INT8 ships"};
        let called = |s: &str| if s == "e2" { "calibration set size".to_string() } else { s.to_string() };
        // named by its words, never its id (Look 3), with the gate's own words below
        assert_eq!(gate_row(&p, &called), ("Confirm the gate on calibration set size".to_string(), "if it holds: INT8 ships".to_string()));
    }

    /// The tallies go in rb's tally order whatever order the criteria come in, each word after its count as a tally
    /// says it ("1 not met"), only the words some criterion has.
    #[test]
    fn tallies_go_in_rbs_order_with_rbs_words() {
        let fs = crate::obj! {"c1" => finding_of("e1", "meets", "int8", false), "c2" => finding_of("e1", "untested", "int8", false),
            "c3" => finding_of("e1", "misses", "int8", false), "c4" => finding_of("e1", "misses", "int8", false)};
        let ids: Vec<String> = ["c1", "c2", "c3", "c4"].iter().map(|s| s.to_string()).collect();
        let labels: Vec<String> = tallies_of(&fs, &ids).as_list().unwrap().iter().map(|t| text(t, "label")).collect();
        assert_eq!(labels, ["1 met", "2 not met", "1 not tested"]);
    }

    /// A recorded interpretation's words keep their full stop as written, and its rules' state follows as a sentence
    /// of its own; words with no closing stop get one.
    #[test]
    fn an_interpretation_s_rules_state_is_a_sentence_of_its_own() {
        let fs = crate::obj! {"c1" => finding_of("e1", "meets", "int8", false)};
        let lead = |words: &str| crate::obj! {"kind" => "interpretation", "index" => 0, "text" => words, "active" => true, "confirmed" => true};
        let c = card(&fs, &["c1"], lead("INT8 is accurate enough to ship."), "Its rule is met.");
        assert_eq!(text(c.get("interpretation").unwrap(), "line"), "Note: INT8 is accurate enough to ship. Its rule is met.");
        let c = card(&fs, &["c1"], lead("INT8 is accurate enough to ship"), "Its rule is met.");
        assert_eq!(text(c.get("interpretation").unwrap(), "line"), "Note: INT8 is accurate enough to ship. Its rule is met.");
    }

    /// PLAIN-WORDS-PASS-3: a recorded interpretation is a note, its state in its line, never rb's verdict and never a
    /// reading: "A note, not confirmed: ..." where it waits for the user, "Note: ..." where they own it, the same on a
    /// page that leaves the machine (neither names whose it is); and the card's state word names its subject.
    #[test]
    fn a_recorded_interpretation_reads_as_a_note() {
        let fs = crate::obj! {"c1" => finding_of("e1", "meets", "int8", false)};
        let lead = |confirmed: bool| crate::obj! {"kind" => "interpretation", "index" => 0, "text" => "INT8 is accurate enough to ship", "active" => true, "confirmed" => confirmed};
        let waits = card(&fs, &["c1"], lead(false), "Its rule is met.");
        let i = waits.get("interpretation").unwrap();
        assert_eq!(text(i, "line"), "A note, not confirmed: INT8 is accurate enough to ship. Its rule is met.");
        assert_eq!(text(&waits, "state_word"), "Open question");
        assert!(text(card(&fs, &["c1"], lead(true), "Its rule is met.").get("interpretation").unwrap(), "line").starts_with("Note: "));
        for confirmed in [true, false] {
            let q = crate::obj! {"id" => "q1", "text" => "Does INT8 cost ResNet-50 top-1 on ImageNet?", "lead" => lead(confirmed),
                "overview" => crate::obj! {"interpretations" => Value::List(vec![crate::obj! {"holds" => "Its rule is met."}])}};
            let ids = vec!["c1".to_string()];
            let away = standing_of(&q, "open", &ids, &tallies_of(&fs, &ids), &fs, &[], &[], &[], true);
            let line = text(away.get("interpretation").unwrap(), "line");
            assert!(line.starts_with(if confirmed { "Note: " } else { "A note, not confirmed: " }), "{line}");
            assert!(!line.contains("reading") && !line.contains("Your"), "{line}");
        }
    }

    /// Rules rb cannot apply yet and rules with no results: "not settled" where both are, "not tested" where only the
    /// second; the tallies keep them apart.
    #[test]
    fn rules_not_settled_and_not_tested_are_said_apart_in_the_standing_and_the_tallies() {
        let none = Value::Null;
        let mixed = crate::obj! {"c1" => finding_of("e1", "undetermined", "int8", false), "c2" => finding_of("e1", "untested", "int8", false), "c3" => finding_of("e1", "meets", "int8", false)};
        let c = card(&mixed, &["c1", "c2", "c3"], none.clone(), "");
        assert_eq!(text(&list(&c, "reasons")[0], "text"), "2 of 3 rules are not settled");
        let labels: Vec<String> = list(&c, "tallies").iter().map(|t| text(t, "label")).collect();
        assert_eq!(labels, ["1 met", "1 can't tell yet", "1 not tested"]);
        let untested = crate::obj! {"c1" => finding_of("e1", "untested", "int8", false), "c2" => finding_of("e1", "meets", "int8", false)};
        assert_eq!(text(&list(&card(&untested, &["c1", "c2"], none.clone(), ""), "reasons")[0], "text"), "1 of 2 rules is not tested");
        assert_eq!(text(&card(&untested, &["c1", "c2"], none, ""), "line"), "No answer yet: 1 of 2 rules is not tested.");
    }

    /// One baseline is one reason, however many of its criteria miss: "the fp32 baseline doesn't meet 2 rules";
    /// two baselines are two, and controls are called controls.
    #[test]
    fn a_baseline_is_counted_once_however_many_of_its_criteria_miss() {
        let none = Value::Null;
        let one = crate::obj! {"c1" => finding_of("e1", "misses", "fp32 baseline", true), "c2" => finding_of("e1", "not reproduced", "fp32 baseline", true)};
        assert_eq!(text(&list(&card(&one, &["c1", "c2"], none.clone(), ""), "reasons")[0], "text"), "the fp32 baseline doesn't meet 2 rules");
        let cited_of = |c: &str| { let mut f = finding_of("e1", "not reproduced", "fp32 baseline", true); if let Value::Dict(d) = &mut f { d.insert("reference_label".into(), Value::Str(format!("cited {c}"))); } f };
        let cited = crate::obj! {"c1" => cited_of("76.13 %"), "c2" => cited_of("93 %")};
        assert_eq!(text(&list(&card(&cited, &["c1", "c2"], none.clone(), ""), "reasons")[0], "text"), "the fp32 baseline doesn't match 2 cited numbers");
        let one_cited = crate::obj! {"c1" => cited_of("76.13 %")};
        assert_eq!(text(&list(&card(&one_cited, &["c1"], none.clone(), ""), "reasons")[0], "text"), "the fp32 baseline doesn't match its cited number");
        // an inherited rule (rb clone) is not reproduced and names no cited number
        let inherited = crate::obj! {"c1" => finding_of("e1", "not reproduced", "fp32 baseline", true)};
        assert_eq!(text(&list(&card(&inherited, &["c1"], none.clone(), ""), "reasons")[0], "text"), "the fp32 baseline is not reproduced");
        let two = crate::obj! {"c1" => finding_of("e1", "misses", "fp32 control", true), "c2" => finding_of("e2", "misses", "fp32 control", true)};
        assert_eq!(text(&list(&card(&two, &["c1", "c2"], none, ""), "reasons")[0], "text"), "2 controls don't meet their rules");
    }

    /// A resource's download address is shown on rb open; a page empties it, unless rb export --url names the address
    /// the export is served from (the tier table's WithUrl row).
    #[test]
    fn a_download_address_on_a_page_only_with_the_exports_address() {
        let doc = || Value::Dict(crate::dict! {"resources" => crate::obj! {"holdout" => crate::obj! {"url" => "https://data.example.org/holdout", "where_short" => crate::list![]}}});
        let url = |d: &Value| d.get("resources").and_then(|r| r.get("holdout")).and_then(|r| r.get("url")).map(py_str).unwrap_or_default();
        for (tier, served, shown) in [(Tier::Live, false, true), (Tier::Owner, false, false), (Tier::Owner, true, true)] {
            let mut d = doc();
            blanks(&mut d, tier, served);
            assert_eq!(url(&d).is_empty(), !shown, "{tier:?} {served}");
        }
    }
}
