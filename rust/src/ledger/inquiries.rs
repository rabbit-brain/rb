//! The reading of one inquiry. Filing an experiment provides navigation; only an explicit criterion link says
//! that a finding bears on this question. A descendant's reading remains the descendant's, never its parent's.

use super::*;

impl Ledger {
    /// Live criteria explicitly assigned to this question. Experiment filing is not an evidence selector.
    pub fn inquiry_criterion_ids(&self, question: &str) -> Res<HashSet<String>> {
        Ok(self.live::<Criterion>()?.into_iter().filter(|c| c.question_links.iter().any(|l| l.question == question && l.retracted.is_none())).map(|c| c.id).collect())
    }

    fn inquiry_question_integrity(&self, q: &Question) -> Res<Vec<Caveat>> {
        let mut out = Vec::new();
        if q.retracted.is_some() {
            out.push(Caveat::problem("question_retracted", &q.id, "the question was retracted"));
        }
        if let Some(why) = self.edited_outside("question", &q.id)? {
            out.push(Caveat::problem("edited_outside_rb", &q.id, format!("question {}: {why}", q.id)));
        }
        Ok(out)
    }

    /// A linked row keeps the numeric finding unchanged. Link timing uses rb's existing timing calculation with
    /// the link's declaration time; it does not inherit the criterion's earlier declaration.
    fn inquiry_row(&self, q: &Question, c: &Criterion, link: Option<&QuestionLink>, findings: &IndexMap<String, Finding>) -> Res<Dict> {
        let mut integrity = self.inquiry_question_integrity(q)?;
        if let Some(why) = self.edited_outside("criterion", &c.id)? {
            integrity.push(Caveat::problem("edited_outside_rb", &c.id, why));
        }
        let experiment = if self.kind_of(&c.experiment)? == Some("experiment") { Some(self.load::<Experiment>(&c.experiment)?) } else { None };
        // a retracted experiment is said by its criteria's findings (experiment_retracted)
        if experiment.is_none() {
            integrity.push(Caveat::problem("experiment_unavailable", &c.experiment, "the experiment is missing"));
        }
        let finding = match (findings.get(&c.id), &experiment) {
            (Some(f), _) => Some(f.clone()),
            (None, Some(_)) => Some(self.finding(c)?),
            _ => None,
        };
        // a retracted criterion is said by its finding (retracted), once
        if c.retracted.is_some() && !finding.as_ref().map_or(false, |f| f.integrity.iter().any(|p| p.code == "retracted")) {
            integrity.push(Caveat::problem("criterion_retracted", &c.id, "the criterion was retracted"));
        }
        if let Some(f) = &finding {
            for p in &f.integrity {
                if !integrity.contains(p) {
                    integrity.push(p.clone());
                }
            }
        }
        let mut qualifications = Vec::new();
        // qualifications its summary leaves out: what the finding's line says already, or the line it is listed under
        let mut said_elsewhere = Vec::new();
        let mut link_timing = "unmapped".to_string();
        if let Some(link) = link {
            if link.retracted.is_some() {
                integrity.push(Caveat::problem("question_link_retracted", &c.id, "this question link was withdrawn"));
            }
            if link.question_text != q.text {
                integrity.push(Caveat::problem("question_link_outdated", &c.id, "the question's text differs from the text this link was declared on"));
            }
            if c.question_links.iter().filter(|l| l.question == q.id && l.retracted.is_none()).count() > 1 {
                integrity.push(Caveat::problem("question_link_ambiguous", &c.id, "more than one active link assigns this criterion to the question"));
            }
            if finding.is_some() {
                let mut at_link = c.clone();
                at_link.created_at = link.declared_at.clone();
                at_link.created_by = link.declared_by.clone();
                let timed = self.finding(&at_link)?;
                link_timing = timed.timing;
                let said = timed.says.get("timing").cloned().unwrap_or_default();
                if !said.is_empty() {
                    qualifications.push(format!("question link {said}"));
                }
            } else {
                link_timing = "unknown".into();
                qualifications.push("question link timing is unknown: no finding can be read".into());
            }
        } else {
            qualifications.push("filed here through its experiment; no question link declares what this criterion answers".to_string());
            said_elsewhere.extend(qualifications.last().cloned());
        }
        if c.role == "gate" {
            qualifications.push("a gate is a policy for spending effort; its outcome is not an answer to this question".into());
        } else if c.role != "primary" {
            qualifications.push(format!("{} criterion", c.role));
        }
        if let Some(f) = &finding {
            if matches!(f.result.as_str(), "point_only" | "unresolved") {
                qualifications.push(format!("result: {}", f.says.get("result").cloned().unwrap_or_else(|| f.result.replace('_', " "))));
                said_elsewhere.extend(qualifications.last().cloned());
            }
            // what the reading rests on: across fixed design cells, how many meet and their range
            if let Some(units) = f.says.get("units").filter(|u| !u.is_empty()) {
                qualifications.push(format!("units: {units}"));
            }
            for note in &f.caveats {
                if !qualifications.contains(&note.text) {
                    qualifications.push(note.text.clone());
                }
            }
        }
        let active = interpretation_active(
            link.map_or("", |l| l.when_met.as_str()),
            link.map_or(false, |l| l.retracted.is_none()),
            &c.role,
            integrity.is_empty(),
            finding.as_ref().map_or(false, |f| f.rule_outcome == "meets" && f.comparison.is_some()),
        );
        // the declarations this row reads: every active one for the question where a merge left more than one
        let links: Vec<&QuestionLink> = match link {
            Some(l) if l.retracted.is_none() => c.question_links.iter().filter(|x| x.question == q.id && x.retracted.is_none()).collect(),
            Some(l) => vec![l],
            None => Vec::new(),
        };
        // what the user's confirmation of the experiment's design covers: a session's interpretation it listed, as it
        // reads now, is confirmed, and the reason given with it reads as its note, as a confirmed call's does
        let design = self.design(&c.experiment)?.unwrap_or_default();
        let covered = |l: &QuestionLink| design.link_writer(&c.id, l) == crate::view::CONFIRMED;
        // in rb's words, which name no session and a writer only as actor::shown_writer does
        let said = |l: &QuestionLink| {
            let who = actor::shown_writer(&l.declared_by).map(|name| format!("{name} ")).unwrap_or_default();
            let mut said = if l.when_met.is_empty() {
                format!("{who}declared no interpretation")
            } else {
                // a session's interpretation is said as one the user has not confirmed, naming no writer
                let unconfirmed = if crate::view::not_confirmed(&l.declared_by).is_empty() || covered(l) { "" } else { ", not confirmed" };
                format!("{who}declared when met{unconfirmed}: {}", pyjson::repr_str(&l.when_met))
            };
            // a session's reason is its note, never rb's words; an interpretation not confirmed already says so for it
            if !l.why.is_empty() {
                let unconfirmed = if l.when_met.is_empty() && !covered(l) { crate::view::not_confirmed(&l.declared_by) } else { "" };
                let label = if crate::view::not_confirmed(&l.declared_by).is_empty() { "why" } else { "note" };
                said.push_str(&format!("; {label}: {}{unconfirmed}", l.why));
            }
            // the user's words, as what is being built says them: never on a page anyone may be sent
            let words = if l.words.is_empty() { String::new() } else { crate::view::say_words(&pyjson::repr_str(&l.words), &l.declared_by) };
            if !words.is_empty() {
                said.push_str(&format!("; {words}"));
            }
            said
        };
        // where a merge left several, each is said, so the one kept is chosen with all of them in view
        let interpretation = if links.iter().any(|l| !l.when_met.is_empty()) {
            links.iter().map(|l| said(l)).collect::<Vec<_>>().join(", and ")
        } else {
            String::new()
        };
        let mut summary = match &finding {
            Some(f) => {
                // its problems are said once, with the inquiry's
                let mut f = f.dump(false);
                f.insert("integrity".into(), Value::List(Vec::new()));
                let f = Value::Dict(f);
                crate::view::finding_line(&f, &self.call_words(&f)).sentence
            }
            None => format!("{}: no finding can be read.", c.statement),
        };
        if !interpretation.is_empty() {
            let label = match (active, links.len() > 1) {
                (true, _) => "Interpretation: ",
                (false, false) => "Conditional interpretation (not active): ",
                (false, true) => "Conditional interpretations (none active): ",
            };
            summary.push_str(&format!(" {label}{interpretation}."));
        }
        if !integrity.is_empty() {
            summary.push_str(&format!(" Inquiry integrity: {}.", integrity.iter().map(|p| p.text.clone()).collect::<Vec<_>>().join("; ")));
        }
        let unsaid: Vec<&String> = qualifications.iter().filter(|q| !said_elsewhere.contains(q)).collect();
        if !unsaid.is_empty() {
            summary.push_str(&format!(" {}.", unsaid.iter().map(|q| q.as_str()).collect::<Vec<_>>().join("; ")));
        }
        Ok(crate::dict! {
            "criterion" => c.id.clone(), "experiment" => c.experiment.clone(), "statement" => c.statement.clone(), "role" => c.role.clone(),
            "finding" => finding.as_ref().map(|f| Value::Dict(f.dump(false))),
            "link" => link.map(|l| Value::Dict(l.dump(false))), "links" => Value::List(links.iter().map(|l| Value::Dict(l.dump(false))).collect()),
            "interpretation" => interpretation,
            "interpretation_active" => active, "link_timing" => link_timing,
            "interpretation_confirmed" => link.map_or(false, |l| !l.when_met.is_empty() && covered(l)),
            "clean" => integrity.is_empty(),
            "qualifications" => Value::list_of_str(qualifications), "integrity" => Value::List(integrity.iter().map(|p| Value::Dict(p.dump(false))).collect()),
            "summary" => summary
        })
    }

    /// The words given with the latest call on a criterion, as its finding names that call.
    fn call_words(&self, f: &Value) -> String {
        let decision = f.get("review").and_then(|r| r.get_str("decision")).filter(|id| !id.is_empty());
        decision.and_then(|id| self.load::<Decision>(id).ok()).map(|d| d.words).unwrap_or_default()
    }

    fn inquiry_direct(&self, q: &Question, findings: &IndexMap<String, Finding>, context: bool) -> Res<Dict> {
        let filed = self.experiments_under(&q.id, false)?;
        let mut direct = Vec::new();
        let mut unmapped = Vec::new();
        let mut unassigned = Vec::new();
        let mut withdrawn = Vec::new();
        let mut criteria = self.all::<Criterion>()?.as_ref().clone();
        criteria.sort_by(|a, b| a.id.cmp(&b.id));
        for c in &criteria {
            match c.question_links.iter().rev().find(|l| l.question == q.id && l.retracted.is_none()) {
                // a retracted criterion is no evidence here: its link is context, beside the withdrawn ones
                Some(link) if c.retracted.is_none() => direct.push(Value::Dict(self.inquiry_row(q, c, Some(link), findings)?)),
                Some(link) if context => withdrawn.push(Value::Dict(self.inquiry_row(q, c, Some(link), findings)?)),
                Some(_) => (),
                None if context && unassigned_here(c, &filed) => {
                    let row = self.inquiry_row(q, c, None, findings)?;
                    // listed with its finding, apart from the evidence, and with the filing that would link it here
                    let amend = if self.load::<Experiment>(&c.experiment)?.frozen.is_some() { " --amend" } else { "" };
                    // a past version offers no command to copy (guarantee 38): the filing is not offered there
                    let link = if crate::view::at_past_version() {
                        String::new()
                    } else {
                        format!(" Link it here: rb criterion file {} {} --why \"<why it bears on this question>\"{amend}", c.id, q.id)
                    };
                    unassigned.push(format!("unassigned (filed here, no question link): {}: {}{link}", c.id, row.get_str("summary").unwrap_or("")));
                    unmapped.push(Value::Dict(row));
                }
                None => (),
            }
            if context {
                for link in c.question_links.iter().filter(|l| l.question == q.id && l.retracted.is_some()) {
                    withdrawn.push(Value::Dict(self.inquiry_row(q, c, Some(link), findings)?));
                }
            }
        }
        let mut calls: HashMap<String, String> = HashMap::new();
        for row in &direct {
            if let (Some(id), Some(f)) = (row.get_str("criterion"), row.get("finding").filter(|f| f.is_dict())) {
                if let Some(call) = crate::view::call_said(f, &self.call_words(f)) {
                    calls.insert(id.to_string(), format!("decided: {call}"));
                }
            }
        }
        // a design reading's range, as its finding says it
        let mut ranges: HashMap<String, String> = HashMap::new();
        for row in &direct {
            let comparison = row.get("finding").and_then(|f| f.get("comparison")).filter(|c| c.get("design").map_or(false, Value::truthy));
            if let (Some(id), Some(comparison)) = (row.get_str("criterion"), comparison) {
                let mut errs = Vec::new();
                let reading = Reading::validate(comparison, &mut Vec::new(), &mut errs).filter(|_| errs.is_empty());
                if let Some(range) = reading.and_then(|r| crate::assess::range_said(criteria.iter().find(|c| c.id == id), &r)) {
                    ranges.insert(id.to_string(), range);
                }
            }
        }
        let mut summary = inquiry_summary(&direct, &calls, &ranges);
        if direct.is_empty() {
            summary.push("No criterion is linked directly to this question.".into());
        }
        summary.extend(unassigned);
        let relations = self.inquiry_relations(&q.id, findings)?;
        summary.extend(relations.iter().map(super::relations::relation_line));
        Ok(crate::dict! {
            "question" => q.id.clone(), "text" => q.text.clone(), "state" => self.question_state(q)?,
            "direct" => Value::List(direct), "unmapped" => Value::List(unmapped), "withdrawn" => Value::List(withdrawn),
            "relations" => Value::List(relations),
            "integrity" => Value::List(self.inquiry_question_integrity(q)?.iter().map(|p| Value::Dict(p.dump(false))).collect()),
            "rollup" => Value::Dict(self.rollup(q, findings, &self.live::<Experiment>()?)?), "summary" => Value::list_of_str(summary)
        })
    }

    /// One shared question reading for CLI, context, pages, open and TUI. Descendants are context, separately named;
    /// no tally decides an answer, and no missed or undetermined rule inverts someone's conditional interpretation.
    pub fn inquiry(&self, question: &str, findings: &IndexMap<String, Finding>) -> Res<Dict> {
        let q: Question = self.load(question)?;
        let mut out = self.inquiry_direct(&q, findings, true)?;
        let mut descendants = Vec::new();
        let mut seen = HashSet::from([q.id.clone()]);
        let mut pending: std::collections::VecDeque<Question> = self.children(question)?.into();
        while let Some(child) = pending.pop_front() {
            if !seen.insert(child.id.clone()) {
                continue;
            }
            pending.extend(self.children(&child.id)?);
            descendants.push(Value::Dict(self.inquiry_direct(&child, findings, true)?));
        }
        out.insert("descendants".into(), Value::List(descendants));
        Ok(out)
    }

    /// Whether question calls keep a scoped snapshot: once any live criterion in the record has an active question
    /// link or relation. Before that a question call is recorded as earlier format-1 builds record it, so they still
    /// read the record. Decided for the whole record, never per question.
    pub fn inquiry_links_in_use(&self) -> Res<bool> {
        Ok(self.live::<Criterion>()?.iter().any(|c| c.question_links.iter().any(|l| l.retracted.is_none()) || c.relations.iter().any(|r| r.retracted.is_none())))
    }

    /// Snapshot of the direct evidence a new question decision was made on. It intentionally excludes the call
    /// itself, descendant context, and navigation-only experiment filings. Old calls have no such field and retain
    /// their original broad experiment basis.
    /// Each rewording of a question the log keeps, oldest first: when, by whom, the wording before and after.
    pub fn rewordings(&self, question: &str) -> Res<Vec<Rewording>> {
        let text = |d: &Dict, k: &str| d.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        Ok(self
            .log_entries()?
            .iter()
            .filter(|e| e.get("op").and_then(Value::as_str) == Some("reword") && e.get("kind").and_then(Value::as_str) == Some("question") && e.get("id").and_then(Value::as_str) == Some(question))
            .map(|e| {
                let detail = e.get("detail").and_then(Value::as_dict).cloned().unwrap_or_default();
                Rewording { at: text(e, "at"), by: text(e, "actor"), before: text(&detail, "before"), text: text(&detail, "text") }
            })
            .collect())
    }

    /// The wording a question had at `at` (a call's time): the wording before the first rewording after it, else the
    /// wording it has now. A call is said with the question it was made on, never with words given later.
    pub fn question_text_at(&self, question: &Question, at: &str) -> Res<String> {
        Ok(self.rewordings(&question.id)?.into_iter().find(|r| r.at.as_str() > at).map_or_else(|| question.text.clone(), |r| r.before))
    }

    pub fn inquiry_basis(&self, question: &str) -> Res<Dict> {
        let q: Question = self.load(question)?;
        let direct = self.inquiry_direct(&q, &IndexMap::new(), false)?;
        let mut criteria = Dict::new();
        let mut experiments = std::collections::BTreeSet::new();
        if let Some(Value::List(rows)) = direct.get("direct") {
            for row in rows {
                let Some(id) = row.get("criterion").and_then(Value::as_str) else {
                    continue;
                };
                if let Some(eid) = row.get("experiment").and_then(Value::as_str) {
                    experiments.insert(eid.to_string());
                }
                let mut basis = Dict::new();
                for key in ["experiment", "statement", "role", "link", "link_timing", "integrity", "interpretation_active"] {
                    basis.insert(key.into(), row.get(key).cloned().unwrap_or(Value::Null));
                }
                if self.kind_of(id)? == Some("criterion") {
                    // the rule alone: this question's link and the relations are kept apart, so another
                    // inquiry's link never moves this call
                    let mut rule = self.load::<Criterion>(id)?;
                    rule.question_links.clear();
                    rule.relations.clear();
                    basis.insert("rule_key".into(), super::helpers::rule_key(&rule));
                }
                if let Some(Value::Dict(f)) = row.get("finding") {
                    basis.insert("finding".into(), Value::Dict(finding_basis(f)));
                }
                criteria.insert(id.into(), Value::Dict(basis));
            }
        }
        if let Some(Value::List(relations)) = direct.get("relations") {
            for relation in relations {
                for side in ["source_finding", "target_finding"] {
                    if let Some(experiment) = relation.get(side).and_then(|f| f.get("experiment")).and_then(Value::as_str) {
                        experiments.insert(experiment.to_string());
                    }
                }
            }
        }
        let mut basis = crate::dict! {
            "version" => 1, "question" => q.id.clone(), "text" => q.text.clone(), "parent" => q.parent.clone(),
            "retracted" => q.retracted.as_ref().map(|r| Value::Dict(r.dump(false))),
            "integrity" => direct.get("integrity").cloned().unwrap_or(Value::Null), "criteria" => Value::Dict(criteria),
            "repository" => Value::Dict(self.repository_basis(&experiments.into_iter().collect::<Vec<_>>())?)
        };
        if let Some(Value::List(relations)) = direct.get("relations").filter(|x| x.truthy()) {
            let mut kept = Vec::new();
            for relation in relations {
                let Some(mut row) = relation.as_dict().cloned() else { continue; };
                // the reading a call is made on; who declared the pair, from either side, stays on the criteria
                row.shift_remove("declarations");
                for side in ["source_finding", "target_finding"] {
                    if let Some(Value::Dict(finding)) = row.get_mut(side) {
                        *finding = finding_basis(finding);
                    }
                }
                kept.push(Value::Dict(row));
            }
            basis.insert("relations".into(), Value::List(kept));
        }
        Ok(basis)
    }
}

/// Whether a criterion is unassigned under a question: live, filed there through its experiment (`filed`, the live
/// experiments filed directly under it), and linked to no question at all.
pub fn unassigned_here(c: &Criterion, filed: &HashSet<String>) -> bool {
    c.retracted.is_none() && filed.contains(&c.experiment) && !c.question_links.iter().any(|l| l.retracted.is_none())
}

/// Whether a linked criterion's interpretation is active: a declared meaning on a live link, a criterion that is not a
/// gate, no integrity problem, and a rule met on a comparison rb reads.
fn interpretation_active(when_met: &str, live: bool, role: &str, clean: bool, met: bool) -> bool {
    !when_met.is_empty() && live && role != "gate" && clean && met
}

/// Optional computed fields were absent in earlier snapshots. Their empty defaults do not change a call's basis.
fn finding_basis(finding: &Dict) -> Dict {
    let mut out = Dict::new();
    for key in ["comparison", "result", "rule_outcome", "rule", "origin", "timing", "integrity", "caveats"] {
        out.insert(key.into(), finding.get(key).cloned().unwrap_or(Value::Null));
    }
    if let Some(Value::Dict(comparison)) = out.get_mut("comparison") {
        if comparison.get("design").map_or(false, |v| !v.truthy()) { comparison.shift_remove("design"); }
    }
    out
}

// ---------------------------------------------------------------- what a question call's currency compares

/// The keys a format-1 question snapshot has at each level. A stored snapshot with any other key, or a version above
/// 1, was written by a newer rb: it reads as needing that rb, never as a call made on an earlier reading.
const SNAPSHOT_KEYS: &[&str] = &["version", "question", "text", "parent", "retracted", "integrity", "criteria", "repository", "relations"];
const SNAPSHOT_CRITERION_KEYS: &[&str] = &["experiment", "statement", "role", "rule_key", "link", "link_timing", "integrity", "interpretation_active", "finding"];
const SNAPSHOT_FINDING_KEYS: &[&str] = &["comparison", "result", "rule_outcome", "rule", "origin", "timing", "integrity", "caveats"];
const SNAPSHOT_RELATION_KEYS: &[&str] = &[
    "kind", "source", "target", "declaration", "orientation", "source_finding", "target_finding", "source_direction", "target_direction",
    "source_certainty", "target_certainty", "outcome", "summary", "integrity",
];
const SNAPSHOT_DESIGN_KEYS: &[&str] = &["factors", "cells", "count", "min", "max", "incomplete", "meets"];
const SNAPSHOT_CELL_KEYS: &[&str] = &["key", "estimate", "n", "unit", "lo", "hi", "runs"];
const SNAPSHOT_REPOSITORY_KEYS: &[&str] = &["result", "audits"];

fn unknown_keys(d: &Dict, known: &dyn Fn(&str) -> bool, at: &str, out: &mut Vec<String>) {
    for key in d.keys() {
        if !known(key) {
            out.push(if at.is_empty() { key.clone() } else { format!("{at}.{key}") });
        }
    }
}

fn unknown_in(value: Option<&Value>, known: &dyn Fn(&str) -> bool, at: &str, out: &mut Vec<String>) {
    if let Some(Value::Dict(d)) = value {
        unknown_keys(d, known, at, out);
    }
}

fn unknown_in_each(value: Option<&Value>, known: &dyn Fn(&str) -> bool, at: &str, out: &mut Vec<String>) {
    if let Some(Value::List(items)) = value {
        for (i, item) in items.iter().enumerate() {
            unknown_in(Some(item), known, &format!("{at}.{i}"), out);
        }
    }
}

fn among(keys: &'static [&'static str]) -> impl Fn(&str) -> bool {
    move |k| keys.contains(&k)
}

fn fields_of(meta: &'static crate::model::ModelMeta) -> impl Fn(&str) -> bool {
    move |k| meta.field(k).is_some()
}

fn unknown_in_finding(f: Option<&Value>, at: &str, out: &mut Vec<String>) {
    unknown_in(f, &among(SNAPSHOT_FINDING_KEYS), at, out);
    let comparison = f.and_then(|f| f.get("comparison"));
    unknown_in(comparison, &fields_of(&Reading::META), &format!("{at}.comparison"), out);
    let design = comparison.and_then(|c| c.get("design"));
    unknown_in(design, &among(SNAPSHOT_DESIGN_KEYS), &format!("{at}.comparison.design"), out);
    unknown_in_each(design.and_then(|d| d.get("cells")), &among(SNAPSHOT_CELL_KEYS), &format!("{at}.comparison.design.cells"), out);
    for key in ["integrity", "caveats"] {
        unknown_in_each(f.and_then(|f| f.get(key)), &fields_of(&Caveat::META), &format!("{at}.{key}"), out);
    }
}

/// What in a stored question snapshot only a newer rb reads, or None when this rb reads all of it.
pub fn snapshot_newer(snapshot: &Dict) -> Option<String> {
    match snapshot.get("version") {
        None | Some(Value::Int(1)) => (),
        Some(other) => return Some(format!("version {}", pyjson::py_str(other))),
    }
    let mut out = Vec::new();
    unknown_keys(snapshot, &among(SNAPSHOT_KEYS), "", &mut out);
    unknown_in(snapshot.get("retracted"), &fields_of(&Retraction::META), "retracted", &mut out);
    unknown_in_each(snapshot.get("integrity"), &fields_of(&Caveat::META), "integrity", &mut out);
    if let Some(Value::Dict(criteria)) = snapshot.get("criteria") {
        for (id, row) in criteria {
            let at = format!("criteria.{id}");
            unknown_in(Some(row), &among(SNAPSHOT_CRITERION_KEYS), &at, &mut out);
            let link = row.get("link");
            unknown_in(link, &fields_of(&QuestionLink::META), &format!("{at}.link"), &mut out);
            unknown_in(link.and_then(|l| l.get("retracted")), &fields_of(&Retraction::META), &format!("{at}.link.retracted"), &mut out);
            unknown_in_each(row.get("integrity"), &fields_of(&Caveat::META), &format!("{at}.integrity"), &mut out);
            unknown_in_finding(row.get("finding"), &format!("{at}.finding"), &mut out);
        }
    }
    if let Some(Value::Dict(repository)) = snapshot.get("repository") {
        for (key, entry) in repository {
            unknown_in(Some(entry), &among(SNAPSHOT_REPOSITORY_KEYS), &format!("repository.{key}"), &mut out);
        }
    }
    if let Some(Value::List(relations)) = snapshot.get("relations") {
        for (i, relation) in relations.iter().enumerate() {
            let at = format!("relations.{i}");
            unknown_in(Some(relation), &among(SNAPSHOT_RELATION_KEYS), &at, &mut out);
            let declaration = relation.get("declaration");
            unknown_in(declaration, &fields_of(&CriterionRelation::META), &format!("{at}.declaration"), &mut out);
            unknown_in(declaration.and_then(|d| d.get("retracted")), &fields_of(&Retraction::META), &format!("{at}.declaration.retracted"), &mut out);
            for side in ["source_scope", "target_scope"] {
                unknown_in(declaration.and_then(|d| d.get(side)), &among(super::relations::SCOPE_KEYS), &format!("{at}.declaration.{side}"), &mut out);
            }
            for side in ["source_finding", "target_finding"] {
                unknown_in_finding(relation.get(side), &format!("{at}.{side}"), &mut out);
            }
            unknown_in_each(relation.get("integrity"), &fields_of(&Caveat::META), &format!("{at}.integrity"), &mut out);
        }
    }
    out.first().cloned()
}

/// A finding as a call's currency reads it: its numbers, design cells, result and rule outcome. Its notes, method,
/// rule sentence and why-no-interval text are left out, so a rewording never moves a call.
fn basis_projection(f: &Value) -> Value {
    let comparison = f.get("comparison").and_then(Value::as_dict);
    let at = |key: &str| comparison.and_then(|c| c.get(key)).cloned().unwrap_or(Value::Null);
    let mut out = crate::dict! {
        "source" => at("source"), "estimate" => at("estimate"), "lo" => at("lo"), "hi" => at("hi"), "n" => at("n"),
        "result" => f.get("result").cloned().unwrap_or(Value::Null), "rule" => f.get("rule_outcome").cloned().unwrap_or(Value::Null)
    };
    if let Some(Value::List(cells)) = comparison.and_then(|c| c.get("design")).and_then(|d| d.get("cells")) {
        let cells = cells.iter().map(|cell| {
            let of = |key: &str| cell.get(key).cloned().unwrap_or(Value::Null);
            crate::obj! {"key" => of("key"), "estimate" => of("estimate"), "lo" => of("lo"), "hi" => of("hi"), "n" => of("n")}
        });
        out.insert("design".into(), Value::List(cells.collect()));
    }
    Value::Dict(out)
}

/// Problems by what and where, without their text, and whether state only a newer rb reads was among them. That one is
/// left out: it fails every gate under its own item (state_newer) and is never a move of the reading a call was made on.
fn problem_codes(items: Option<&Value>) -> (Value, bool) {
    let mut newer = false;
    let mut codes: Vec<(String, String)> = Vec::new();
    for p in items.and_then(Value::as_list).map(|l| l.as_slice()).unwrap_or_default() {
        match p.get_str("code") {
            Some("state_newer") => newer = true,
            Some(code) => codes.push((code.to_string(), p.get_str("subject").unwrap_or("").to_string())),
            None => (),
        }
    }
    codes.sort();
    codes.dedup();
    (Value::List(codes.into_iter().map(|(code, subject)| crate::list![code, subject]).collect()), newer)
}

/// A relation's outcome as the projection compares it: read again from its sides' directions when state only a newer
/// rb reads is all that stopped it being read.
fn relation_outcome(relation: &Value) -> Value {
    let (rest, newer) = problem_codes(relation.get("integrity"));
    if newer && rest.as_list().map_or(false, |l| l.is_empty()) {
        let at = |key: &str| relation.get_str(key).unwrap_or("missing");
        let (outcome, _) = super::relations::reversal_outcome(true, (at("source_direction"), at("source_certainty")), (at("target_direction"), at("target_certainty")));
        return Value::Str(outcome.into());
    }
    relation.get("outcome").cloned().unwrap_or(Value::Null)
}

/// One normalized projection of a question snapshot, stored by any format-1 rb or made now: the question's id, text,
/// retraction and problem codes; each linked criterion's reading, rule, link, timing, interpretation state and problem
/// codes; each relation's outcome and sides; and what the repositories said. The question's parent is left out: a
/// reframing is not a fresh start. State only a newer rb reads is left out too, and what it stopped from being read (an
/// interpretation, a reversal) is read as it would be without it.
fn inquiry_projection(snapshot: &Dict) -> Dict {
    let mut criteria = Dict::new();
    if let Some(Value::Dict(rows)) = snapshot.get("criteria") {
        for (id, row) in rows {
            let declared = row.get("link").filter(|l| l.is_dict());
            let link = declared.map(|l| {
                let of = |key: &str| l.get(key).cloned().unwrap_or(Value::Null);
                crate::obj! {
                    "question" => of("question"), "question_text" => of("question_text"), "when_met" => of("when_met"),
                    "declared_by" => of("declared_by"), "declared_at" => of("declared_at"), "retracted" => l.get_nn("retracted").is_some()
                }
            });
            let finding = row.get("finding").filter(|f| f.is_dict());
            let of_finding = |key: &str| finding.and_then(|f| f.get(key)).cloned().unwrap_or(Value::Null);
            let (integrity, newer) = problem_codes(row.get("integrity"));
            let active = if newer {
                Value::Bool(interpretation_active(
                    declared.and_then(|l| l.get_str("when_met")).unwrap_or(""),
                    declared.map_or(false, |l| l.get_nn("retracted").is_none()),
                    row.get_str("role").unwrap_or(""),
                    integrity.as_list().map_or(true, |l| l.is_empty()),
                    finding.map_or(false, |f| f.get_str("rule_outcome") == Some("meets") && f.get_nn("comparison").is_some()),
                ))
            } else {
                row.get("interpretation_active").cloned().unwrap_or(Value::Null)
            };
            let mut kept = crate::dict! {
                "basis" => finding.map(basis_projection), "origin" => of_finding("origin"), "timing" => of_finding("timing"),
                "link" => link, "link_timing" => row.get("link_timing").cloned().unwrap_or(Value::Null),
                "interpretation_active" => active, "integrity" => integrity
            };
            if let Some(rule) = row.get("rule_key") {
                kept.insert("rule_key".into(), rule.clone());
            }
            criteria.insert(id.clone(), Value::Dict(kept));
        }
    }
    // a pair is said in one order, so a pair declared from both sides, read once or once from each side, is one
    let mut relations: Vec<Value> = snapshot
        .get("relations")
        .and_then(Value::as_list)
        .map(|l| {
            l.iter()
                .map(|r| {
                    let of = |key: &str| r.get(key).cloned().unwrap_or(Value::Null);
                    let side = |key: &str| r.get(key).filter(|f| f.is_dict()).map(basis_projection).unwrap_or(Value::Null);
                    let (mut a, mut b) = ((of("source"), side("source_finding")), (of("target"), side("target_finding")));
                    if canonical(&a.0) > canonical(&b.0) {
                        std::mem::swap(&mut a, &mut b);
                    }
                    crate::obj! {"kind" => of("kind"), "source" => a.0, "target" => b.0, "outcome" => relation_outcome(r),
                        "source_finding" => a.1, "target_finding" => b.1}
                })
                .collect()
        })
        .unwrap_or_default();
    relations.sort_by_key(canonical);
    relations.dedup();
    crate::dict! {
        "question" => snapshot.get("question").cloned().unwrap_or(Value::Null), "text" => snapshot.get("text").cloned().unwrap_or(Value::Null),
        "retracted" => snapshot.get("retracted").map_or(false, |r| !r.is_null()), "integrity" => problem_codes(snapshot.get("integrity")).0,
        "criteria" => Value::Dict(criteria), "relations" => Value::List(relations),
        "repository" => snapshot.get("repository").cloned().unwrap_or(Value::Dict(Dict::new()))
    }
}

/// What moved between the snapshot a question call stored and the one this question has now, compared through one
/// projection. A rule key a call was recorded without is not compared.
pub fn inquiry_moved(subject: &str, then: &Dict, now: &Dict) -> Vec<String> {
    let (before, after) = (inquiry_projection(then), inquiry_projection(now));
    let empty = Dict::new();
    let mut moved = Vec::new();
    let was = before.get("criteria").and_then(Value::as_dict).unwrap_or(&empty);
    let is = after.get("criteria").and_then(Value::as_dict).unwrap_or(&empty);
    let ids: std::collections::BTreeSet<&String> = was.keys().chain(is.keys()).collect();
    for id in ids {
        let mut current = is.get(id).cloned();
        if let (Some(Value::Dict(old)), Some(Value::Dict(new))) = (was.get(id), current.as_mut()) {
            if !old.contains_key("rule_key") {
                new.shift_remove("rule_key");
            }
        }
        if was.get(id) != current.as_ref() {
            moved.push(id.clone());
        }
    }
    for (key, reason) in [
        ("question", "question identity changed"), ("text", "question text changed"), ("retracted", "question retraction changed"),
        ("integrity", "question integrity changed"), ("relations", "related comparisons changed"),
    ] {
        if before.get(key) != after.get(key) {
            moved.push(format!("{subject} ({reason})"));
        }
    }
    let was = before.get("repository").and_then(Value::as_dict).unwrap_or(&empty);
    let is = after.get("repository").and_then(Value::as_dict).unwrap_or(&empty);
    let ids: std::collections::BTreeSet<&String> = was.keys().chain(is.keys()).collect();
    for id in ids {
        if was.get(id) != is.get(id) {
            moved.push(format!("{} (its repository says something else now)", id.strip_suffix("#repository").unwrap_or(id)));
        }
    }
    moved
}

/// Whether two question snapshots read the same through the projection, for the check that a call repeats another.
pub fn same_inquiry(then: Option<&Dict>, now: Option<&Dict>) -> bool {
    match (then, now) {
        (None, None) => true,
        (Some(a), Some(b)) => snapshot_newer(a).is_none() && snapshot_newer(b).is_none() && inquiry_moved("", a, b).is_empty(),
        _ => false,
    }
}

/// Group only verbatim identical attributed interpretations. This removes repeated prose, never weighs evidence
/// or chooses between interpretations. Every comparison and rule stays named, with every qualification attached
/// to the criteria it qualifies; the complete statements and instrument rows remain in `direct`. Each criterion is
/// said with its rule as its finding words it, and the latest call on it beside its interpretation.
fn inquiry_summary(rows: &[Value], calls: &HashMap<String, String>, ranges: &HashMap<String, String>) -> Vec<String> {
    let mut groups: IndexMap<String, Vec<&Value>> = IndexMap::new();
    let mut ungrouped = Vec::new();
    for row in rows {
        let interpretation = row.get("interpretation").and_then(Value::as_str).unwrap_or("");
        if interpretation.is_empty() {
            if let Some(summary) = row.get("summary").and_then(Value::as_str) {
                ungrouped.push(summary.to_string());
            }
        } else {
            groups.entry(interpretation.into()).or_default().push(row);
        }
    }
    let mut summaries = Vec::new();
    for (interpretation, group) in groups {
        let several = group.iter().any(|row| row.get("links").and_then(Value::as_list).map_or(false, |l| l.len() > 1));
        let mut met = Vec::new();
        let mut inactive = Vec::new();
        let mut readings = Vec::new();
        let mut qualifications: IndexMap<String, Vec<String>> = IndexMap::new();
        for row in group {
            let id = row.get("criterion").and_then(Value::as_str).unwrap_or("").to_string();
            // the rule as the headline says it (estimate meets criterion, never just met), and the call made on it
            let rule = row.get("finding").and_then(|f| f.get("says")).and_then(|s| s.get_str("headline")).and_then(|h| h.split(" · ").next()).unwrap_or("no finding can be read");
            let mut said = vec![rule.to_string()];
            // across fixed design cells the rule reads their mean: how many meet, and their range, are said beside it
            let comparison = row.get("finding").and_then(|f| f.get("comparison"));
            if let Some(design) = comparison.and_then(|c| c.get("design")).filter(|d| d.truthy()) {
                if let (Some(meets), Some(count)) = (design.get("meets").and_then(Value::as_i64), design.get("count").and_then(Value::as_i64)) {
                    said.push(if meets == count && count > 0 {
                        // every cell is the only one, both or all n, as every count rb says
                        let all = crate::assess::k_of_n(count as usize, count as usize, "design cell", "design cells");
                        format!("{all} {} the threshold", if count == 1 { "meets" } else { "meet" })
                    } else {
                        format!("{meets} of {count} design cells meet the threshold")
                    });
                }
                if let Some(range) = ranges.get(&id) {
                    said.push(format!("range {range}"));
                }
            }
            said.extend(calls.get(&id).cloned());
            let said = format!("{id} ({})", said.join("; "));
            if row.get("interpretation_active").map_or(false, Value::truthy) {
                met.push(said);
            } else {
                inactive.push(said);
            }
            if let Some(f) = row.get("finding").filter(|f| !f.is_null()) {
                let says = f.get("says");
                let said = |key: &str| says.and_then(|s| s.get(key)).and_then(Value::as_str).unwrap_or("");
                let comparison = if said("comparison").is_empty() { "no reading" } else { said("comparison") };
                readings.push(format!("{id}: {comparison}; {}", said("rule")));
                let timing = said("timing");
                if !timing.is_empty() {
                    qualifications.entry(format!("criterion {timing}")).or_default().push(id.clone());
                }
            } else {
                readings.push(format!("{id}: no finding can be read"));
            }
            if let Some(Value::List(items)) = row.get("integrity") {
                for item in items {
                    if let Some(text) = item.get("text").and_then(Value::as_str) {
                        qualifications.entry(format!("INTEGRITY: {text}")).or_default().push(id.clone());
                    }
                }
            }
            if let Some(Value::List(items)) = row.get("qualifications") {
                for item in items {
                    if let Some(text) = item.as_str() {
                        qualifications.entry(text.to_string()).or_default().push(id.clone());
                    }
                }
            }
        }
        let mut parts = vec![format!("Recorded {}: {interpretation}.", if several { "interpretations" } else { "interpretation" })];
        if !met.is_empty() {
            parts.push(format!("Interpretation active for {}.", met.join(", ")));
        }
        if !inactive.is_empty() {
            parts.push(format!("Conditional interpretation not active for {}.", inactive.join(", ")));
        }
        parts.push(readings.join("; ") + ".");
        if !qualifications.is_empty() {
            let mut qualified = Vec::new();
            for (text, mut ids) in qualifications {
                ids.sort();
                ids.dedup();
                qualified.push(format!("{}: {text}", ids.join(", ")));
            }
            parts.push(qualified.join("; ") + ".");
        }
        summaries.push(parts.join(" "));
    }
    summaries.extend(ungrouped);
    summaries
}

#[cfg(test)]
mod tests {
    use super::*;

    fn relation(source: &str, target: &str, estimates: (f64, f64)) -> Value {
        let finding = |x: f64| crate::obj! {"comparison" => crate::obj! {"source" => "runs", "estimate" => x}, "result" => "point_only", "rule_outcome" => "misses"};
        crate::obj! {"kind" => "reverses", "source" => source, "target" => target, "outcome" => "observed_reversal", "integrity" => Value::List(Vec::new()),
            "source_finding" => finding(estimates.0), "target_finding" => finding(estimates.1)}
    }

    fn snapshot(relations: Vec<Value>) -> Dict {
        crate::dict! {"version" => 1, "question" => "q1", "text" => "Does it reverse?", "criteria" => Value::Dict(Dict::new()), "relations" => Value::List(relations)}
    }

    #[test]
    fn a_pair_read_from_each_side_and_read_once_compare_the_same() {
        let both = snapshot(vec![relation("c1", "c2", (1.0, -1.0)), relation("c2", "c1", (-1.0, 1.0))]);
        let once = snapshot(vec![relation("c1", "c2", (1.0, -1.0))]);
        assert!(inquiry_moved("q1", &both, &once).is_empty());
        assert!(inquiry_moved("q1", &once, &snapshot(vec![relation("c2", "c1", (-1.0, 1.0))])).is_empty());
        // a reading that moved still moves the call
        assert_eq!(inquiry_moved("q1", &both, &snapshot(vec![relation("c1", "c2", (1.0, -2.0))])), vec!["q1 (related comparisons changed)".to_string()]);
    }
}

/// A question's rewording as the log keeps it.
#[derive(Clone, Debug)]
pub struct Rewording {
    pub at: String,
    pub by: String,
    pub before: String,
    pub text: String,
}
