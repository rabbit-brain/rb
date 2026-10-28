//! What rests on what (SHELL-SPEC section 15 P0, BP 1a): one rule for every surface, by ids. `rests_on` gives the
//! edges into a record as `Rest`s, each naming its objects by id; rb retract words them (`Rest::words`) and the shell's
//! document hands them apart, so a screen and the retraction never say two things. `Graph` reads, once per state of the
//! record, the edges a screen draws both ways: what a run counts toward, the runs that hold the same bytes, what read or
//! made a resource and what rests on it, and what reads an analysis.

use crate::errors::Res;
use crate::ledger::Ledger;
use crate::ledger::Obj;
use crate::record::{Analysis, Criterion, Experiment, Finding, Observation, Question, Resource, Run};
use indexmap::IndexMap;
use std::collections::{BTreeSet, HashSet};

/// One edge into a record: what rests on it, by id. Its words are rb retract's, one sentence each.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Rest {
    /// a call made on it
    Decision(String),
    /// a criterion counts the run
    Counts(String),
    /// a criterion reads the run in its timing, as one it could have been fitted to
    Timing(String),
    /// an analysis read it (a run or an analysis)
    ReadBy(String),
    /// a criterion reads it (an analysis, as its reading; a metric, by name)
    Reads(String),
    /// its experiment is frozen
    Frozen(String),
    /// the experiment itself is frozen
    ItIsFrozen,
    /// a variant's experiment is frozen
    VariantFrozen(String),
    /// a run of the experiment, or one that read or made the resource
    Run(String),
    /// one of the experiment's criteria is linked to a question
    Linked { criterion: String, question: String },
    /// a relation one of the experiment's criteria is a side of
    Related { criterion: String, kind: String, target: String },
    /// the criterion's role, while evidence reads on it
    Evidence(String),
    /// the criterion is linked to a question
    LinkedTo(String),
    /// the criterion relates to another
    RelatesTo { kind: String, target: String },
    /// another criterion relates to this one
    RelatedFrom { criterion: String, kind: String },
    /// an experiment filed under the question
    Experiment(String),
    /// a question under the question
    Question(String),
    /// a criterion linked to the question
    CriterionLinked(String),
}

impl Rest {
    /// The edge as rb retract says it.
    pub fn words(&self) -> String {
        match self {
            Rest::Decision(d) => format!("decision {d}"),
            Rest::Counts(c) => format!("criterion {c} counts it"),
            Rest::Timing(c) => format!("criterion {c} reads it in its timing"),
            Rest::ReadBy(a) => format!("analysis {a} read it"),
            Rest::Reads(c) => format!("criterion {c} reads it"),
            Rest::Frozen(e) => format!("experiment {e} is frozen"),
            Rest::ItIsFrozen => "it is frozen".into(),
            Rest::VariantFrozen(e) => format!("{e} is frozen"),
            Rest::Run(r) => format!("run {r}"),
            Rest::Linked { criterion, question } => format!("criterion {criterion} is linked to question {question}"),
            Rest::Related { criterion, kind, target } => format!("criterion {criterion} {kind} criterion {target}"),
            Rest::Evidence(role) => format!("it is a {role} criterion and evidence reads on it"),
            Rest::LinkedTo(q) => format!("it is linked to question {q}"),
            Rest::RelatesTo { kind, target } => format!("it {kind} criterion {target}"),
            Rest::RelatedFrom { criterion, kind } => format!("criterion {criterion} {kind} it"),
            Rest::Experiment(e) => format!("experiment {e}"),
            Rest::Question(q) => format!("question {q}"),
            Rest::CriterionLinked(c) => format!("criterion {c} is linked to it"),
        }
    }
}

/// The edges into a record of `kind` (`obj`, or `subject` for a setting), as rb retract names them. A variant has its
/// own: `variant_rests_on`.
pub fn rests_on(led: &Ledger, kind: &str, obj: Option<&Obj>, subject: &str) -> Res<Vec<Rest>> {
    let mut deps: Vec<Rest> = led.decisions_on(subject)?.into_iter().map(|d| Rest::Decision(d.id)).collect();
    match (kind, obj) {
        ("run", Some(Obj::Run(r))) => {
            for (c, obs) in observations_of(led, r)? {
                if obs.iter().any(|o| o.counted) {
                    deps.push(Rest::Counts(c.id));
                } else if obs.iter().any(crate::ledger::findings::fitted_to) {
                    deps.push(Rest::Timing(c.id));
                }
            }
            deps.extend(analyses_reading(led, &r.id)?.into_iter().map(Rest::ReadBy));
            let exp: Experiment = led.load(&r.experiment)?;
            if exp.frozen.is_some() {
                deps.push(Rest::Frozen(exp.id));
            }
        }
        ("analysis", Some(Obj::Analysis(an))) => {
            deps.extend(criteria_reading(led, an)?.into_iter().map(Rest::Reads));
            deps.extend(analyses_reading(led, &an.id)?.into_iter().map(Rest::ReadBy));
        }
        ("experiment", Some(Obj::Experiment(e))) => {
            if e.frozen.is_some() {
                deps.push(Rest::ItIsFrozen);
            }
            // every run that still says something, one superseded for a measurement too, as a resource's are
            for r in led.standing_runs()?.iter() {
                if r.experiment == e.id {
                    deps.push(Rest::Run(r.id.clone()));
                }
            }
            // its criteria's question links, and the relations read through them from either side
            let live = led.live::<Criterion>()?;
            let mine: HashSet<&str> = live.iter().filter(|c| c.experiment == e.id).map(|c| c.id.as_str()).collect();
            let mut said: Vec<Rest> = Vec::new();
            for c in live.iter().filter(|c| mine.contains(c.id.as_str())) {
                said.extend(c.active_links().into_iter().map(|link| Rest::Linked { criterion: c.id.clone(), question: link.question.clone() }));
            }
            for c in live.iter() {
                for relation in c.active_relations().into_iter().filter(|r| mine.contains(c.id.as_str()) || mine.contains(r.target.as_str())) {
                    said.push(Rest::Related { criterion: c.id.clone(), kind: relation.kind.clone(), target: relation.target.clone() });
                }
            }
            for s in said {
                if !deps.contains(&s) {
                    deps.push(s);
                }
            }
        }
        ("criterion", Some(Obj::Criterion(c))) => {
            let exp: Experiment = led.load(&c.experiment)?;
            if exp.frozen.is_some() {
                deps.push(Rest::Frozen(exp.id));
            }
            if (c.role == "primary" || c.role == "gate") && led.finding(c)?.comparison.is_some() {
                deps.push(Rest::Evidence(c.role.clone()));
            }
            deps.extend(criterion_edges(led, c)?);
        }
        ("setting", _) => {
            let exp_id = subject.split('/').next().unwrap_or("");
            if led.load::<Experiment>(exp_id)?.frozen.is_some() {
                deps.push(Rest::Frozen(exp_id.to_string()));
            }
        }
        ("metric", Some(Obj::Metric(m))) => {
            let mut names: HashSet<&str> = HashSet::from([m.id.as_str()]);
            names.extend(m.aliases.iter().map(|s| s.as_str()));
            for c in led.live::<Criterion>()? {
                if names.contains(crate::ledger::last_part(&c.metric)) {
                    deps.push(Rest::Reads(c.id));
                }
            }
        }
        ("question", Some(Obj::Question(q))) => {
            for e in led.live::<Experiment>()? {
                if e.questions.contains(&q.id) {
                    deps.push(Rest::Experiment(e.id));
                }
            }
            for q2 in led.live::<Question>()? {
                if q2.parent.as_deref() == Some(q.id.as_str()) {
                    deps.push(Rest::Question(q2.id));
                }
            }
            let mut linked: Vec<String> = led.inquiry_criterion_ids(&q.id)?.into_iter().collect();
            linked.sort();
            deps.extend(linked.into_iter().map(Rest::CriterionLinked));
        }
        ("resource", Some(Obj::Resource(res))) => {
            let (read, made) = runs_using(led, res)?;
            let mut seen = HashSet::new();
            deps.extend(read.into_iter().chain(made).map(|(r, _)| r).filter(|r| seen.insert(r.clone())).map(Rest::Run));
        }
        _ => {}
    }
    Ok(deps)
}

/// What rests on a variant: its experiment's freeze.
pub fn variant_rests_on(exp: &Experiment) -> Vec<Rest> {
    if exp.frozen.is_some() { vec![Rest::VariantFrozen(exp.id.clone())] } else { Vec::new() }
}

/// The criterion's own edges, beside its calls, freeze and role: the questions it bears on, the relations it declares
/// and those other criteria declare to it.
fn criterion_edges(led: &Ledger, c: &Criterion) -> Res<Vec<Rest>> {
    let mut deps: Vec<Rest> = c.active_links().into_iter().map(|link| Rest::LinkedTo(link.question.clone())).collect();
    deps.extend(c.active_relations().into_iter().map(|r| Rest::RelatesTo { kind: r.kind.clone(), target: r.target.clone() }));
    for other in led.live::<Criterion>()? {
        for relation in other.active_relations().into_iter().filter(|r| r.target == c.id) {
            deps.push(Rest::RelatedFrom { criterion: other.id.clone(), kind: relation.kind.clone() });
        }
    }
    Ok(deps)
}

/// The observations of `r` in the finding of each criterion of its experiment that observes it, with its criterion,
/// in the criteria's order.
fn observations_of(led: &Ledger, r: &Run) -> Res<Vec<(Criterion, Vec<Observation>)>> {
    let mut out = Vec::new();
    for c in led.live::<Criterion>()? {
        if c.experiment != r.experiment {
            continue;
        }
        let obs: Vec<Observation> = led.finding(&c)?.observations.into_iter().filter(|o| o.run == r.id).collect();
        if !obs.is_empty() {
            out.push((c, obs));
        }
    }
    Ok(out)
}

/// The analyses not taken back that read `id` (a run or an analysis) themselves.
fn analyses_reading(led: &Ledger, id: &str) -> Res<Vec<String>> {
    Ok(reading(&read_lists(led, &led.live::<Analysis>()?)?, id))
}

/// What each analysis read: as rb last wrote it for one changed outside rb since, which rb does not read as it stands
/// (nothing, where rb holds no copy of its last write).
fn read_lists(led: &Ledger, analyses: &[Analysis]) -> Res<IndexMap<String, Vec<String>>> {
    let mut out = IndexMap::new();
    for a in analyses {
        let reads = if led.edited_outside("analysis", &a.id)?.is_some() {
            match led.as_written("analysis", &a.id)? {
                Some(Obj::Analysis(w)) => w.reads,
                _ => Vec::new(),
            }
        } else {
            a.reads.clone()
        };
        out.insert(a.id.clone(), reads);
    }
    Ok(out)
}

/// Of the analyses (each with what it read), those that read `id` themselves.
fn reading(reads: &IndexMap<String, Vec<String>>, id: &str) -> Vec<String> {
    reads.iter().filter(|(_, r)| r.iter().any(|x| x == id)).map(|(a, _)| a.clone()).collect()
}

/// The criteria of an analysis's experiment whose current reading is the analysis's.
fn criteria_reading(led: &Ledger, an: &Analysis) -> Res<Vec<String>> {
    let mut out = Vec::new();
    for c in led.live::<Criterion>()? {
        let f = led.finding(&c)?;
        if reads_analysis(&c, &f, an) {
            out.push(c.id);
        }
    }
    Ok(out)
}

/// Whether a criterion's current reading is an analysis's.
fn reads_analysis(c: &Criterion, f: &Finding, an: &Analysis) -> bool {
    c.experiment == an.experiment && f.comparison.as_ref().map_or(false, |x| x.source == an.id)
}

/// Who used a resource: the runs still standing that read it or made it, each with its experiment, and the analyses
/// that read it, themselves or through what they read (rb show's split, one rule with rb retract's).
pub struct Users {
    pub read: Vec<(String, String)>,
    pub made: Vec<(String, String)>,
    pub analyses: Vec<String>,
}

pub fn users_of(led: &Ledger, res: &Resource) -> Res<Users> {
    let (read, made) = runs_using(led, res)?;
    let mut analyses = Vec::new();
    for a in led.live::<Analysis>()? {
        if led.upstream(&a)?.iter().any(|(k, rid, _)| k == "resource" && *rid == res.id) {
            analyses.push(a.id);
        }
    }
    Ok(Users { read, made, analyses })
}

/// The runs still standing (one superseded for a measurement too) that read a resource, and those that made it, each
/// with its experiment, in the record's order.
pub fn runs_using(led: &Ledger, res: &Resource) -> Res<(Vec<(String, String)>, Vec<(String, String)>)> {
    let standing = led.standing_runs()?;
    let used = |pick: fn(&Run) -> &Vec<crate::record::ResourceUse>| -> Vec<(String, String)> {
        standing.iter().filter(|r| pick(r).iter().any(|u| u.resource == res.id)).map(|r| (r.id.clone(), r.experiment.clone())).collect()
    };
    Ok((used(|r| &r.inputs), used(|r| &r.outputs)))
}

// ---------------------------------------------------------------- both ways, for a screen

/// A run's edge to a criterion it counts toward, or is read by without counting: directly (`via` empty), or through
/// the analysis whose reading the criterion reads (`via`), which read the run itself or through another analysis.
pub struct Toward {
    pub criterion: String,
    pub counted: bool,
    pub reason: String,
    pub via: String,
}

/// A run that holds an output file with the same bytes as another's, whether it was taken back whole, and the
/// resources it made.
pub struct SameBytes {
    pub run: String,
    pub experiment: String,
    pub retracted: bool,
    pub made: Vec<String>,
}

/// The edges a screen draws, read once for a state of the record.
pub struct Graph {
    findings: IndexMap<String, (Criterion, Finding)>,
    /// what each analysis not taken back read, as rb reads it
    reads: IndexMap<String, Vec<String>>,
    /// the analyses changed outside rb since rb wrote them, with why a reading leaves out what rests on each
    unread_analyses: IndexMap<String, String>,
    runs: Vec<Run>,
    /// the runs changed outside rb since rb wrote them, with why a reading leaves each out, as its observations say it
    edited: IndexMap<String, String>,
}

impl Graph {
    pub fn of(led: &Ledger) -> Res<Graph> {
        let mut findings = IndexMap::new();
        for c in led.live::<Criterion>()? {
            let f = led.finding(&c)?;
            findings.insert(c.id.clone(), (c, f));
        }
        let runs = led.all::<Run>()?.to_vec();
        let mut edited = IndexMap::new();
        for r in &runs {
            if let Some(why) = crate::ledger::findings::unread(led, "run", &r.id)? {
                edited.insert(r.id.clone(), why);
            }
        }
        let analyses = led.live::<Analysis>()?;
        let mut unread_analyses = IndexMap::new();
        for a in &analyses {
            if let Some(why) = crate::ledger::findings::unread(led, "analysis", &a.id)? {
                unread_analyses.insert(a.id.clone(), why);
            }
        }
        Ok(Graph { findings, reads: read_lists(led, &analyses)?, unread_analyses, runs, edited })
    }

    /// A criterion not taken back, with its finding.
    pub fn finding(&self, id: &str) -> Option<&(Criterion, Finding)> {
        self.findings.get(id)
    }

    /// Whether `id` (a run or an analysis) is upstream of analysis `a`, read by it or by an analysis it read, and if so
    /// why rb does not read what rests on it through that chain: an analysis on the way changed outside rb ("" where
    /// none was).
    fn upstream_of(&self, a: &str, id: &str) -> Option<String> {
        // each analysis reached, with the unread reason of the chain that reached it first
        let mut seen: HashSet<String> = HashSet::new();
        let mut todo = vec![(a.to_string(), String::new())];
        while let Some((x, why)) = todo.pop() {
            if !seen.insert(x.clone()) {
                continue;
            }
            let Some(reads) = self.reads.get(&x) else { continue };
            let why = if why.is_empty() { self.unread_analyses.get(&x).cloned().unwrap_or_default() } else { why };
            if reads.iter().any(|r| r == id) {
                return Some(why);
            }
            todo.extend(reads.iter().map(|r| (r.clone(), why.clone())));
        }
        None
    }

    /// What run `r` counts toward: each criterion that observes it, counted or not with rb's reason, in the order of
    /// the criteria; then each criterion whose reading is an analysis's that rests on the run.
    pub fn counts_toward(&self, r: &Run) -> Vec<Toward> {
        let mut out = Vec::new();
        for (c, f) in self.findings.values() {
            if c.experiment == r.experiment {
                for o in f.observations.iter().filter(|o| o.run == r.id) {
                    out.push(Toward { criterion: c.id.clone(), counted: o.counted, reason: o.reason.clone(), via: String::new() });
                }
            }
            if let Some(source) = f.comparison.as_ref().map(|x| x.source.clone()).filter(|s| self.reads.contains_key(s)) {
                if let Some(chain) = self.upstream_of(&source, &r.id) {
                    // a run taken back whole no longer stands under the analysis; one superseded for a measurement still does
                    // and one changed outside rb, or reached through an analysis changed outside rb, is not read, as the
                    // criterion's own observations say
                    let reason = match whole_retraction(r) {
                        Some(x) => format!("retracted{}", crate::view::reason_part(&x.why, &x.by, ": ")),
                        None => self.edited.get(&r.id).cloned().unwrap_or(chain),
                    };
                    out.push(Toward { criterion: c.id.clone(), counted: reason.is_empty(), reason, via: source });
                }
            }
        }
        out
    }

    /// The other runs holding a file of the same bytes as one of `r`'s (reproduce's rule: never the database a row is
    /// read from), each with what it made.
    pub fn same_bytes(&self, r: &Run) -> Vec<SameBytes> {
        self.runs
            .iter()
            .filter(|o| crate::reproduce::same_bytes(r, o))
            .map(|o| SameBytes { run: o.id.clone(), experiment: o.experiment.clone(), retracted: whole_retraction(o).is_some(), made: made(o) })
            .collect()
    }

    /// The criteria that read an analysis's reading, then the analyses that read it: what rb retract names of it.
    pub fn used_by(&self, a: &Analysis) -> Vec<String> {
        let mut out: Vec<String> = self.findings.values().filter(|(c, f)| reads_analysis(c, f, a)).map(|(c, _)| c.id.clone()).collect();
        out.extend(reading(&self.reads, &a.id));
        out
    }

    /// The criteria resting on the runs and analyses that read a resource (`users`), in the record's order of
    /// criteria, and by the questions each is linked to.
    pub fn resting_on(&self, users: &Users) -> (Vec<String>, IndexMap<String, Vec<String>>) {
        let runs: HashSet<&str> = users.read.iter().map(|(r, _)| r.as_str()).collect();
        let analyses: HashSet<&str> = users.analyses.iter().map(String::as_str).collect();
        let mut criteria = Vec::new();
        let mut by_question: IndexMap<String, Vec<String>> = IndexMap::new();
        for (c, f) in self.findings.values() {
            let on_a_run = f.observations.iter().any(|o| runs.contains(o.run.as_str()) && (o.counted || crate::ledger::findings::fitted_to(o)));
            let on_an_analysis = f.comparison.as_ref().map_or(false, |x| analyses.contains(x.source.as_str()));
            if on_a_run || on_an_analysis {
                criteria.push(c.id.clone());
                let questions: BTreeSet<&str> = c.active_links().into_iter().map(|l| l.question.as_str()).collect();
                for q in questions {
                    by_question.entry(q.to_string()).or_default().push(c.id.clone());
                }
            }
        }
        (criteria, by_question)
    }
}

/// A run's retraction when it took the run back whole, not for some of its measurements.
fn whole_retraction(r: &Run) -> Option<&crate::record::Retraction> {
    r.retracted.as_ref().filter(|x| x.scope.is_empty())
}

/// The resources a run made, in its order.
pub fn made(r: &Run) -> Vec<String> {
    let mut seen = HashSet::new();
    r.outputs.iter().map(|u| u.resource.clone()).filter(|x| seen.insert(x.clone())).collect()
}

/// A criterion's edges a finding card draws: the questions it is linked to, the relations it declares (kind and
/// target) and those declared to it (criterion and kind), as rb retract names them.
pub struct CriterionEdges {
    pub question_links: Vec<String>,
    pub relations_out: Vec<(String, String)>,
    pub relations_in: Vec<(String, String)>,
}

pub fn criterion_rests(led: &Ledger, c: &Criterion) -> Res<CriterionEdges> {
    let mut e = CriterionEdges { question_links: Vec::new(), relations_out: Vec::new(), relations_in: Vec::new() };
    for r in criterion_edges(led, c)? {
        match r {
            Rest::LinkedTo(q) => e.question_links.push(q),
            Rest::RelatesTo { kind, target } => e.relations_out.push((kind, target)),
            Rest::RelatedFrom { criterion, kind } => e.relations_in.push((criterion, kind)),
            _ => {}
        }
    }
    Ok(e)
}
