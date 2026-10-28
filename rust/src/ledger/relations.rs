//! Declared relationships between findings. A reversal reads both differences in an explicitly aligned order;
//! it does not turn a directional point estimate into evidence that the direction is settled.

use super::*;

/// The keys of a comparison scope this rb declares (criterion_scope). A scope a newer rb wrote with more keys reads
/// as needing that rb (state_newer); its relation compares only these.
pub const SCOPE_KEYS: &[&str] = &["experiment", "subject", "reference", "metric", "estimand", "where", "subject_where", "reference_where", "design_factors", "unit"];

impl Ledger {
    pub fn criterion_scope(&self, c: &Criterion) -> Res<Dict> {
        let exp: Experiment = self.load(&c.experiment)?;
        let (subject, reference, metric, problem) = self.arms(&exp, c)?;
        if let Some(problem) = problem { return Err(RBError::msg("E_OBJECT_INVALID", problem)); }
        let raw = c.dump(false);
        let mut out = crate::dict! {
            "experiment" => c.experiment.clone(), "subject" => subject, "reference" => reference,
            "metric" => metric, "estimand" => c.estimand.clone()
        };
        for key in ["where", "subject_where", "reference_where"] {
            out.insert(key.into(), raw.get(key).cloned().unwrap_or(Value::Dict(Dict::new())));
        }
        let mut design = Vec::new();
        for (name, setting) in exp.all_settings() {
            if setting.dump(false).get("design_factor").map_or(false, Value::truthy) { design.push(name); }
        }
        design.sort();
        out.insert("design_factors".into(), Value::list_of_str(design));
        out.insert("unit".into(), Value::from(self.unit_setting(&exp)));
        Ok(out)
    }

    pub fn relate_criterion(&self, source: &str, kind: &str, target: &str, orientation: Option<&str>, remove: bool, why: &str, amend: Option<&str>) -> Res<Criterion> {
        if kind != "reverses" || source == target {
            return Err(RBError::msg("E_OBJECT_INVALID", "A reversal relates two different criteria: rb criterion relate <criterion> reverses <other>."));
        }
        if why.trim().is_empty() || why.chars().count() > TEXT {
            return Err(RBError::msg("E_OBJECT_INVALID", "A finding relation needs a nonblank reason: --why \"...\"."));
        }
        if !remove && !matches!(orientation, Some("same" | "opposite")) {
            return Err(RBError::msg("E_OBJECT_INVALID", "Declare how the arms align with --orientation same or --orientation opposite."));
        }
        if remove && orientation.is_some() {
            return Err(RBError::msg("E_OBJECT_INVALID", "--remove takes no new orientation."));
        }
        let who = self.actor()?;
        self.locked(|| {
            self.check_write("criterion", source)?;
            let mut c: Criterion = self.load(source)?;
            if c.retracted.is_some() { return Err(RBError::msg("E_OBJECT_INVALID", format!("{source} is retracted."))); }
            let mut exp: Experiment = self.load(&c.experiment)?;
            writable(&exp)?;
            self.check_write("experiment", &exp.id)?;
            // every active declaration of this pair: one, or two where a merge of two branches left them
            let active: Vec<usize> = (0..c.relations.len()).filter(|i| c.relations[*i].kind == kind && c.relations[*i].target == target && c.relations[*i].retracted.is_none()).collect();
            let new = if remove {
                if active.is_empty() { return Err(RBError::msg("E_OBJECT_INVALID", format!("{source} has no active {kind} relation to {target}."))); }
                None
            } else {
                self.check_write("criterion", target)?;
                let other: Criterion = self.load(target)?;
                if other.retracted.is_some() { return Err(RBError::msg("E_OBJECT_INVALID", format!("{target} is retracted."))); }
                let other_exp: Experiment = self.load(&other.experiment)?;
                writable(&other_exp)?;
                self.check_write("experiment", &other_exp.id)?;
                // an alignment reads the same from either side, so a declaration back with the other one contradicts it
                if let Some(back) = other.relations.iter().rev().find(|r| r.kind == kind && r.target == source && r.retracted.is_none() && Some(r.orientation.as_str()) != orientation) {
                    let amend = if other_exp.frozen.is_some() { " --amend" } else { "" };
                    return Err(RBError::msg_fix(
                        "E_OBJECT_INVALID",
                        format!("{target} already declares that it {kind} {source} with orientation {}, declared{} at {}; an alignment reads the same from either side, so orientation {} contradicts it.",
                            back.orientation, actor::by_writer(&back.declared_by), back.declared_at, orientation.unwrap_or("")),
                        format!("Withdraw that declaration first, if this one is right: rb criterion relate {target} {kind} {source} --remove --why \"...\"{amend}."),
                    ));
                }
                let left = self.criterion_scope(&c)?;
                let right = self.criterion_scope(&other)?;
                if left.get_str("reference").unwrap_or("").is_empty() || right.get_str("reference").unwrap_or("").is_empty() {
                    return Err(RBError::msg("E_OBJECT_INVALID", "A reversal needs two differences, each with a subject and reference; a level has no direction to reverse."));
                }
                if left.get("metric") != right.get("metric") || left.get("estimand") != right.get("estimand") {
                    return Err(RBError::msg("E_OBJECT_INVALID", "A reversal needs the same metric and estimand on both sides."));
                }
                Some(build_with(crate::dict! {
                    "kind" => kind, "target" => target, "orientation" => orientation.unwrap(),
                    "source_scope" => Value::Dict(left), "target_scope" => Value::Dict(right),
                    "declared_by" => who.id.clone(), "declared_at" => now(), "via" => self.via.clone(),
                    "why" => why, "words" => self.words.clone(), "session" => actor::session_for(&who)
                }, CriterionRelation::validate)?)
            };
            if let ([i], Some(new)) = (active.as_slice(), &new) {
                let old = &c.relations[*i];
                if old.orientation == new.orientation && same_scope(&old.source_scope, &new.source_scope) && same_scope(&old.target_scope, &new.target_scope) { return Ok(c); }
            }
            let before = self.freeze_sha(&exp, None)?;
            for i in &active {
                c.relations[*i].retracted = Some(build_with(crate::dict! {
                    "at" => now(), "by" => who.id.clone(), "why" => why, "words" => self.words.clone()
                }, Retraction::validate)?);
            }
            if let Some(new) = new { c.relations.push(new); }
            c = build_with(c.dump(false), Criterion::validate)?;
            super::writes::declared_once(&c, None, Some((kind, target)), false)?;
            let mut criteria = self.criteria_of(&exp.id)?;
            for current in &mut criteria { if current.id == c.id { *current = c.clone(); } }
            let after = self.freeze_sha(&exp, Some(&criteria))?;
            let amended = self.guard(&mut exp, &before, &after, amend, &who, &format!("criterion {source}: {} {kind} {target}", if remove { "withdraw" } else { "relate" }))?;
            if !amended.is_empty() { self.commit(&who, "amend", Obj::Experiment(exp), amended.clone())?; }
            let mut detail = crate::dict! {"kind" => kind, "target" => target, "orientation" => orientation, "why" => why};
            detail.extend(amended);
            self.commit(&who, if remove { "unrelate" } else { "relate" }, Obj::Criterion(c.clone()), detail)?;
            Ok(c)
        })
    }

    /// Read one edge only. Even reciprocal declarations never recurse through the relationship graph.
    pub fn criterion_relation(&self, source: &Criterion, relation: &CriterionRelation, findings: &IndexMap<String, Finding>) -> Res<Dict> {
        let mut problems = Vec::new();
        let other = if self.kind_of(&relation.target)? == Some("criterion") { Some(self.load::<Criterion>(&relation.target)?) } else { None };
        let mut left = None;
        let mut right = None;
        for (c, slot, expected) in [(Some(source), &mut left, &relation.source_scope), (other.as_ref(), &mut right, &relation.target_scope)] {
            let Some(c) = c else { problems.push(Caveat::problem("relation_target_missing", &relation.target, "the other criterion is missing")); continue };
            if let Some(why) = self.edited_outside("criterion", &c.id)? { problems.push(Caveat::problem("edited_outside_rb", &c.id, why)); }
            if self.kind_of(&c.experiment)? != Some("experiment") {
                problems.push(Caveat::problem("experiment_unavailable", &c.experiment, "a related experiment is missing"));
                continue;
            }
            match self.criterion_scope(c) {
                Ok(scope) if !same_scope(&scope, expected) => problems.push(Caveat::problem("relation_outdated", &c.id, "the comparison scope changed after its alignment was declared; declare the relation again")),
                Ok(_) => (),
                Err(error) if error.code == "E_OBJECT_INVALID" => problems.push(Caveat::problem("relation_scope_invalid", &c.id, error.message)),
                Err(error) => return Err(error),
            }
            let finding = match findings.get(&c.id) { Some(f) => f.clone(), None => self.finding(c)? };
            // a retracted criterion's finding says its retraction (retracted), once
            if c.retracted.is_some() && !finding.integrity.iter().any(|p| p.code == "retracted") {
                problems.push(Caveat::problem("criterion_retracted", &c.id, "a criterion in this relation is retracted"));
            }
            for problem in &finding.integrity { if !problems.contains(problem) { problems.push(problem.clone()); } }
            *slot = Some(finding);
        }
        if relation.retracted.is_some() { problems.push(Caveat::problem("relation_retracted", &source.id, "this relationship was withdrawn")); }
        if relation.retracted.is_none() && source.relations_to(&relation.kind, &relation.target) > 1 {
            problems.push(Caveat::problem("relation_ambiguous", &source.id, "more than one active declaration relates these criteria; none is read"));
        }
        if let Some(back) = other.as_ref().filter(|o| o.retracted.is_none() && relation.retracted.is_none()).and_then(|o| contradicting(o, relation, &source.id)) {
            problems.push(Caveat::problem("relation_conflict", &source.id, format!("{} declares the {} alignment back to {}; neither declaration is read", relation.target, back.orientation, source.id)));
        }
        let l = direction(left.as_ref(), false);
        let r = direction(right.as_ref(), relation.orientation == "opposite");
        let (outcome, summary) = reversal_outcome(problems.is_empty(), l, r);
        Ok(crate::dict! {
            "kind" => relation.kind.clone(), "source" => source.id.clone(), "target" => relation.target.clone(),
            "declaration" => Value::Dict(relation.dump(false)), "orientation" => relation.orientation.clone(),
            "source_finding" => left.as_ref().map(|f| Value::Dict(f.dump(false))),
            "target_finding" => right.as_ref().map(|f| Value::Dict(f.dump(false))),
            "source_direction" => l.0, "target_direction" => r.0, "source_certainty" => l.1, "target_certainty" => r.1,
            "outcome" => outcome, "summary" => summary,
            "integrity" => Value::List(problems.iter().map(|p| Value::Dict(p.dump(false))).collect()),
            "declarations" => Value::List(vec![declared(source, relation)])
        })
    }

    /// What withdrawing a relation answers: the declaration as it stands, with no reading of what it no longer declares.
    pub fn withdrawn_relation(source: &Criterion, relation: &CriterionRelation) -> Dict {
        crate::dict! {
            "kind" => relation.kind.clone(), "source" => source.id.clone(), "target" => relation.target.clone(),
            "declaration" => Value::Dict(relation.dump(false)), "orientation" => relation.orientation.clone(),
            "source_finding" => Value::Null, "target_finding" => Value::Null,
            "source_direction" => Value::Null, "target_direction" => Value::Null, "source_certainty" => Value::Null, "target_certainty" => Value::Null,
            "outcome" => "withdrawn", "summary" => "The relation was withdrawn; its declaration stays on record with who withdrew it and why.",
            "integrity" => Value::List(Vec::new()), "declarations" => Value::List(vec![declared(source, relation)])
        }
    }

    pub fn inquiry_relations(&self, question: &str, findings: &IndexMap<String, Finding>) -> Res<Vec<Value>> {
        let ids = self.inquiry_criterion_ids(question)?;
        self.relations_touching(&ids, findings)
    }

    /// Every relation read through these criteria, from either side. A declaration back that reads the same (its
    /// kind and orientation, outcome and problems) joins the reading of the side listed first, so one pair reads once
    /// with both declarations and both attributions.
    pub fn relations_touching(&self, ids: &HashSet<String>, findings: &IndexMap<String, Finding>) -> Res<Vec<Value>> {
        let mut out: Vec<Dict> = Vec::new();
        let mut criteria = self.all::<Criterion>()?.as_ref().clone();
        criteria.sort_by(|a, b| a.id.cmp(&b.id));
        for c in &criteria {
            if c.retracted.is_some() { continue; }
            for relation in c.active_relations() {
                if !ids.contains(&c.id) && !ids.contains(&relation.target) { continue; }
                let reading = self.criterion_relation(c, relation, findings)?;
                let reciprocal = |first: &Dict| {
                    let at = |d: &Dict, key: &str| d.get(key).cloned();
                    first.get_str("source") == Some(relation.target.as_str()) && first.get_str("target") == Some(c.id.as_str())
                        && ["kind", "orientation", "outcome"].iter().all(|key| at(first, key) == at(&reading, key))
                        && problems_of(first) == problems_of(&reading)
                };
                match out.iter_mut().find(|first| reciprocal(first)) {
                    Some(first) => {
                        if let Some(Value::List(both)) = first.get_mut("declarations") {
                            both.push(declared(c, relation));
                        }
                    }
                    None => out.push(reading),
                }
            }
        }
        Ok(out.into_iter().map(Value::Dict).collect())
    }
}

/// One declaration of a relation as a reading lists it: the declaration, with the criterion that holds it.
fn declared(holder: &Criterion, relation: &CriterionRelation) -> Value {
    let mut d = relation.dump(false);
    d.insert("criterion".into(), Value::Str(holder.id.clone()));
    Value::Dict(d)
}

/// A reading's problems by what and where, to tell whether two readings of one pair agree.
fn problems_of(reading: &Dict) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = reading
        .get("integrity")
        .and_then(Value::as_list)
        .map(|l| l.iter().map(|p| (p.get_str("code").unwrap_or("").to_string(), p.get_str("subject").unwrap_or("").to_string())).collect())
        .unwrap_or_default();
    out.sort();
    out
}

/// A relation's reading as one line: 'c2 reverses c1: <its summary>', both declarations where the pair was declared
/// from both sides, and what stops it being read when anything does. A writer is named only as actor::shown_writer
/// names one.
pub fn relation_line(reading: &Value) -> String {
    let at = |key: &str| reading.get_str(key).unwrap_or("");
    let declarations = reading.get("declarations").and_then(Value::as_list).map(|l| l.as_slice()).unwrap_or_default();
    let mut line = if declarations.len() > 1 {
        let by: Vec<String> = declarations
            .iter()
            .filter_map(|d| actor::shown_writer(d.get_str("declared_by").unwrap_or("")).map(|name| format!("{} by {name}", d.get_str("criterion").unwrap_or(""))))
            .collect();
        let by = if by.is_empty() { String::new() } else { format!(": {}", by.join("; ")) };
        format!("{} {} {}, and {} {} {} (declared from both sides{by}): {}", at("source"), at("kind"), at("target"), at("target"), at("kind"), at("source"), at("summary"))
    } else {
        format!("{} {} {}: {}", at("source"), at("kind"), at("target"), at("summary"))
    };
    if let Some(said) = reading.as_dict().map(relation_problems).filter(|s| !s.is_empty()) {
        line.push_str(&format!(" Integrity: {said}."));
    }
    line
}

/// What stops a relation being read, as its integrity says it: 'a criterion in this relation is retracted (c1)'.
pub fn relation_problems(reading: &Dict) -> String {
    let problems: Vec<String> = reading
        .get("integrity")
        .and_then(Value::as_list)
        .map(|l| l.iter().map(|p| format!("{} ({})", p.get_str("text").unwrap_or(""), p.get_str("subject").unwrap_or(""))).collect())
        .unwrap_or_default();
    problems.join("; ")
}

/// The other criterion's active declaration back to `source`, of the same kind, with the other alignment: an alignment
/// reads the same from either side, so the two cannot both hold.
pub fn contradicting<'a>(other: &'a Criterion, relation: &CriterionRelation, source: &str) -> Option<&'a CriterionRelation> {
    other.relations.iter().rev().find(|r| r.kind == relation.kind && r.target == source && r.retracted.is_none() && r.orientation != relation.orientation)
}

/// A reversal's outcome and summary from each side's (direction, certainty), once nothing in its evidence or declared
/// alignment needs repair (`clean`).
pub fn reversal_outcome(clean: bool, l: (&str, &str), r: (&str, &str)) -> (&'static str, &'static str) {
    let both = l.0 != "missing" && r.0 != "missing";
    let opposed = (l.0 == "positive" && r.0 == "negative") || (l.0 == "negative" && r.0 == "positive");
    let settled = l.1 == "interval_clear" && r.1 == "interval_clear";
    if !clean {
        ("integrity", "The reversal cannot be read until its evidence or declared alignment is repaired.")
    } else if !both {
        ("incomplete", "Both comparisons are needed to read the reversal.")
    } else if l.0 == "mixed" || r.0 == "mixed" {
        ("mixed", "The recorded design cells do not all point in one direction.")
    } else if l.0 == "zero" || r.0 == "zero" {
        ("no_direction", "At least one comparison has no directional difference.")
    } else if opposed && settled {
        ("resolved_reversal", "The direction reverses, with both intervals clear of zero.")
    } else if opposed {
        ("observed_reversal", "The measured direction reverses; repeatability remains uncertain.")
    } else if settled {
        ("same_direction", "Both comparisons point in the same direction, with intervals clear of zero.")
    } else {
        ("same_direction_uncertain", "The measured directions agree; uncertainty remains.")
    }
}

/// Signs are aligned only by the declared orientation. An interval is clear only when it excludes zero;
/// a fixed set of design cells may show a uniform direction, but supplies no sampling interval.
fn direction(finding: Option<&Finding>, flip: bool) -> (&'static str, &'static str) {
    let Some(c) = finding.and_then(|f| f.comparison.as_ref()) else { return ("missing", "missing") };
    let Some(estimate) = c.estimate else { return ("missing", "missing") };
    if c.reference.is_empty() { return ("missing", "missing"); }
    let sign = |x: f64| if x == 0.0 { "zero" } else if (x > 0.0) != flip { "positive" } else { "negative" };
    let data = c.dump(false);
    if data.get("design").and_then(|d| d.get("incomplete")).map_or(false, Value::truthy) {
        return ("missing", "incomplete_design");
    }
    if let Some(design) = data.get("design").filter(|d| d.get("count").and_then(Value::as_i64).unwrap_or(0) > 1) {
        if let (Some(lo), Some(hi)) = (design.get("min").and_then(Value::as_f64), design.get("max").and_then(Value::as_f64)) {
            if sign(lo) != sign(hi) { return ("mixed", "design_cells"); }
        }
        return (sign(estimate), "design_cells");
    }
    let certainty = match (c.lo, c.hi) {
        (Some(lo), Some(hi)) if lo > 0.0 || hi < 0.0 => "interval_clear",
        (Some(_), Some(_)) => "interval_crosses_zero",
        _ => "point_only",
    };
    (sign(estimate), certainty)
}

fn same_scope(a: &Dict, b: &Dict) -> bool {
    let known = |d: &Dict| Value::Dict(d.iter().filter(|(k, _)| SCOPE_KEYS.contains(&k.as_str())).map(|(k, v)| (k.clone(), v.clone())).collect());
    canonical(&known(a)) == canonical(&known(b))
}

/// Keys of a declared relation's scopes that this rb does not know: a newer rb wrote them.
pub fn unknown_scope_keys(relation: &Value) -> Vec<String> {
    let mut out = Vec::new();
    for side in ["source_scope", "target_scope"] {
        if let Some(Value::Dict(scope)) = relation.get(side) {
            out.extend(scope.keys().filter(|k| !SCOPE_KEYS.contains(&k.as_str())).map(|k| format!("{side}.{k}")));
        }
    }
    out
}
