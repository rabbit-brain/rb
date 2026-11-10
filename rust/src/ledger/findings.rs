//! Findings: a criterion read on axes, computed from the runs and analyses on every read and never written.

use super::helpers::*;
use super::*;
use std::rc::Rc;
use crate::assess;
use crate::pyfloat::fmt_float;
use std::collections::BTreeSet;

/// rb's own reading of the runs for one comparison (`_run_reading`'s dict).
#[derive(Debug, Clone, Default)]
pub struct RunReading {
    pub reading: Option<Reading>,
    pub observations: Vec<Observation>,
    /// each paired unit and its value
    pub unit_values: Option<Vec<(String, f64)>>,
    /// the units the reading rests on
    pub read_units: Vec<String>,
    pub notes: Vec<Caveat>,
    pub problems: Vec<Caveat>,
    pub prospective: Option<Reading>,
    pub history: Vec<Reading>,
    /// the runs the reading rests on (every counted run while there is no reading)
    pub counted: Vec<Observation>,
    /// the runs a paired reading leaves out
    pub left_out: Vec<Observation>,
    pub superseded_early: Vec<(String, String, String)>,
    /// the runs of the baseline alone (the reference arm) that came before the rule was declared: its timing is read on
    /// its subject's runs, so these leave it written before them
    pub baseline_early: Vec<String>,
    /// when each run nobody knows the production time of was recorded: it was produced by then
    pub recorded: HashMap<String, String>,
    /// where the reading pairs runs of two arms recorded apart: the settings it pairs them by, whether that is the
    /// declared unit, and the same runs read unpaired
    pub pairing: Option<Pairing>,
}

/// How a paired reading of two arms recorded in runs of their own pairs them, and what the same runs read unpaired.
#[derive(Debug, Clone)]
pub struct Pairing {
    /// the per-run settings a pair shares: the declared unit, or those whose values tell the pairs apart
    pub by: Vec<String>,
    /// the pairs are the declared unit's
    pub declared: bool,
    /// the same runs, each arm's units apart: a Welch reading, where each arm has units enough
    pub unpaired: Option<Reading>,
    /// of `by`, the settings that could only number repeats (their values whole numbers): the only ones the repeat form
    /// is offered for, since a condition (a model, an effort, an eval set) is never folded into a unit
    pub counters: Vec<String>,
    /// the unpaired reading reads runs the paired one leaves out (a unit one arm alone ran): all the arms' runs
    pub all_runs: bool,
    /// a run gives only the difference (change.<metric>), so no arm's mean is over all its runs: no unpaired figure
    pub diff_only: bool,
}

/// Values a counter could hold (a seed, an epoch, a repeat's number): whole numbers, every one. A setting whose values
/// are names (a model, an eval set) is a condition, never offered as a repeat.
pub fn counter_values<'a>(mut values: impl Iterator<Item = &'a str>) -> bool {
    let mut any = false;
    let all = values.all(|v| {
        any = true;
        let v = v.trim();
        v.parse::<i64>().is_ok() || v.parse::<f64>().map_or(false, |x| x.is_finite() && x.fract() == 0.0 && v.contains('.'))
    });
    any && all
}

pub type Aliases = Rc<IndexMap<String, String>>;

/// A criterion's normal reading and readings at its question links' declaration times. Keeping both avoids a
/// timing query evicting the numeric finding that the next surface or decision snapshot needs again.
pub(super) type CachedFindings = (Rc<Index>, Vec<(Rc<Criterion>, Rc<Finding>)>);
const FINDING_VARIANTS: usize = 16;
/// Where a pooled note offers the repeat form, the words it starts with: taken away where a value misses.
const REPEAT_FORM: &str = ". The repeat form is for a counter only (a seed, an epoch, a repeat's number), never a condition: ";
/// How many runs a caveat names before it counts the rest: the finding's observations list every one.
const RUNS_NAMED: usize = 6;

#[cfg(test)]
mod cache_tests {
    use super::*;

    struct Scratch(std::path::PathBuf);
    impl Drop for Scratch {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
    }

    fn cached(led: &Ledger, criterion: &Criterion) -> Rc<Finding> {
        led.finding_cache.borrow().get(&criterion.id).unwrap().1.iter()
            .find(|(c, _)| c.as_ref() == criterion).unwrap().1.clone()
    }

    #[test]
    fn declaration_timing_queries_reuse_findings_and_reread_changed_sources() {
        let _turn = crate::sys::turns::starting();     // the ledger starts git and Python
        let dir = Scratch(std::env::temp_dir().join(format!("rb-finding-cache-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32))));
        std::fs::create_dir_all(&dir.0).unwrap();
        let source = dir.0.join("target.json");
        std::fs::write(&source, "{\"budget\":0.7}").unwrap();
        let led = Ledger::init(&dir.0, "Finding cache fixture", Some("fixture"), Some("sdk"), Some("cache-test"), false).unwrap();
        led.add_experiment(&NewExperiment { title: "Measured budget".into(), id: Some("e1".into()), ..Default::default() }).unwrap();
        let cited = build_with(crate::dict! {"kind" => "file", "path" => "target.json", "key" => "budget"}, Source::validate).unwrap();
        let criterion = led.add_criterion(&NewCriterion {
            id: Some("c1".into()), experiment: "e1".into(), statement: "Budget".into(), metric: "score".into(),
            comparator: "at_least".into(), target: Some(0.7), source: Some(cited), ..Default::default()
        }).unwrap();
        led.add_run("e1", NewRun { metrics: crate::dict! {"score" => 0.8}, ..Default::default() }).unwrap();
        let normal = led.finding(&criterion).unwrap();
        assert!(normal.integrity.is_empty());
        let original = cached(&led, &criterion);
        let mut link = criterion.clone();
        link.created_at = "2099-01-01T00:00:00+00:00".into();
        link.created_by = "agent:link-author".into();
        let timed = led.finding(&link).unwrap();
        let linked = cached(&led, &link);
        assert_eq!(timed.timing, "after_evidence");
        for _ in 0..3 {
            assert_eq!(led.finding(&criterion).unwrap(), normal);
            assert_eq!(led.finding(&link).unwrap(), timed);
            assert!(Rc::ptr_eq(&cached(&led, &criterion), &original));
            assert!(Rc::ptr_eq(&cached(&led, &link), &linked));
        }
        for n in 0..FINDING_VARIANTS + 2 {
            link.created_by = format!("agent:link-{n}");
            led.finding(&link).unwrap();
        }
        assert_eq!(led.finding_cache.borrow()["c1"].1.len(), FINDING_VARIANTS);
        assert!(Rc::ptr_eq(&cached(&led, &criterion), &original));
        // A new read scope still re-reads external sources even though no .rb file changed.
        std::fs::write(&source, "{\"budget\":0.9}").unwrap();
        let changed = led.locked(|| led.finding(&criterion)).unwrap();
        assert!(changed.integrity.iter().any(|p| p.code == "cited_source_changed"));
        assert!(!Rc::ptr_eq(&cached(&led, &criterion), &original));
    }
}

/// x if x is not None else (y if y is not None else z)
/// The variant whose own setting a caveat's subject (`<exp>/<variant>.<name>`) names, or None for a shared setting.
fn variant_setting_of(exp: &Experiment, subject: &str) -> Option<String> {
    let name = subject.strip_prefix(&format!("{}/", exp.id))?;
    match exp.split(name) {
        (Some(v), rest) if v.setting(rest).is_some() => Some(v.name.clone()),
        _ => None,
    }
}

fn first_some(a: Option<f64>, b: Option<f64>, c: Option<f64>) -> Option<f64> {
    a.or(b).or(c)
}

fn selector_matches(selector: &Dict, run: &Run) -> bool {
    selector.iter().all(|(k, v)| run.per_run.get(k).map_or(false, |actual| selector_equal(actual, v)))
}

pub(crate) fn selector_equal(a: &Value, b: &Value) -> bool {
    // Select exactly the recorded JSON value, as setting verification does.
    match (a, b) {
        (Value::Bool(x), Value::Bool(y)) => x == y,
        (Value::Bool(_), _) | (_, Value::Bool(_)) => false,
        _ => same_value(a, b),
    }
}

fn selected(crit: &Criterion, run: &Run) -> bool {
    selector_matches(&crit.r#where, run)
        && (selector_matches(&crit.subject_where, run) || (crit.compares() && selector_matches(&crit.reference_where, run)))
}

fn separating(crit: &Criterion, key: &str) -> bool {
    crit.subject_where.get(key).zip(crit.reference_where.get(key)).map_or(false, |(a, b)| !selector_equal(a, b))
}

fn scoped_unit(run: &Run, crit: &Criterion, unit: Option<&str>, unit_of: &HashMap<String, String>) -> String {
    if unit.is_some() || crit.subject_where.is_empty() || crit.reference_where.is_empty() {
        return unit_for(run, unit, unit_of);
    }
    let mut scope = run.clone();
    scope.per_run.retain(|key, _| !separating(crit, key));
    unit_for(&scope, unit, unit_of)
}

fn reference_observation(subject: &str, reference: &str) -> String {
    if subject == reference { format!("{reference} [reference]") } else { reference.to_string() }
}

/// Whether an observation is of the rule's subject: a difference, or the subject's own number; never the reference arm's
/// alone, which a rule makes no claim about.
fn of_subject(subject: &str, reference: &str, arm: &str) -> bool {
    reference.is_empty() || arm != reference_observation(subject, reference)
}

/// What a rule's timing says when only the runs of the arm it compares against came before it: its baseline's (or
/// control's) by that name, any other arm as the runs it compares against.
fn came_first(exp: &Experiment, reference: &str) -> String {
    match exp.variant(reference).map(|v| v.role.as_str()) {
        Some(role @ ("baseline" | "control")) => format!("its {role}'s runs {CAME_FIRST}"),
        _ => format!("the runs it compares against {CAME_FIRST}"),
    }
}

const CAME_FIRST: &str = "came first";

fn truncate(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

const SHALLOW: &str = "the clone is shallow";
/// Why a reading leaves out a run changed outside rb.
const EDITED: &str = "edited outside rb";

/// Why a reading leaves out a record (a run, an analysis) changed outside rb since rb wrote it, as a run's observation
/// says it; None while it is what rb wrote.
pub(crate) fn unread(led: &Ledger, kind: &str, id: &str) -> Res<Option<String>> {
    Ok(match led.edited_outside(kind, id)? {
        Some(_) if led.shallow_cut(kind, id)? => Some(SHALLOW.into()),
        Some(_) => Some(EDITED.into()),
        None => None,
    })
}

/// Whether a run is shown and not read because its file is not what rb last wrote: edited outside rb, or made in
/// history a shallow clone lacks.
fn not_read(reason: &str) -> bool {
    reason.starts_with("edited outside") || reason == SHALLOW
}

/// What a rule met or missed on its estimate alone says of the interval that crosses it.
pub(crate) const STRADDLES: &str = "on the estimate, not across the whole interval";

/// Whether a criterion's timing reads a run its reading does not count, as one it could have been fitted to: it came
/// before the declaration, and is read (no edit made outside rb hides it, and it is no synthetic run).
pub(crate) fn fitted_to(o: &Observation) -> bool {
    !o.counted && o.before_declared && !not_read(&o.reason) && !o.reason.starts_with("synthetic")
}

/// When a record's output was produced, as a timing reads it: by the time it was recorded at the latest, whatever later
/// time it was given (a local time typed with no offset reads as UTC, hours ahead).
fn by_record(produced: &str, recorded: &str) -> String {
    if crate::pytime::before(Some(recorded), Some(produced)) { recorded.to_string() } else { produced.to_string() }
}

/// What shows how a record whose history a shallow clone lacks got there.
fn fetch_whole() -> String {
    format!("({UNSHALLOW} fetches the whole history)")
}

impl Ledger {
    pub fn metric_aliases(&self) -> Res<Aliases> {
        let idx = self.where_()?;
        if let Some((i, a)) = self.aliases_cache.borrow().as_ref() {
            if Rc::ptr_eq(i, &idx) {
                return Ok(a.clone());
            }
        }
        let mut out: IndexMap<String, String> = IndexMap::new();
        for m in self.live::<Metric>()? {
            out.insert(m.id.clone(), m.id.clone());
            for a in &m.aliases {
                out.insert(a.clone(), m.id.clone());
            }
        }
        let rc = Rc::new(out);
        *self.aliases_cache.borrow_mut() = Some((idx, rc.clone()));
        Ok(rc)
    }

    /// The problem of a run or analysis an analysis rests on that is not what rb last wrote (`how` it rests on it).
    fn rests_outside(&self, kind: &str, rid: &str, how: &str) -> Res<Caveat> {
        let why = self.edited_outside(kind, rid)?.unwrap_or_else(|| "None".into());
        Ok(if self.shallow_cut(kind, rid)? {
            Caveat::problem("shallow_clone", rid, format!("{how}: {why}: {UNSHALLOW} fetches the whole history"))
        } else {
            Caveat::problem("edited_outside_rb", rid, format!("{how}: {why}"))
        })
    }

    /// The number a criterion's metric reads in one run, or None (also when the reading is ambiguous).
    pub fn value_for(&self, exp: &Experiment, metric: &str, run: &Run, aliases: Option<&IndexMap<String, String>>) -> Res<Option<f64>> {
        Ok(self.read_metric(exp, metric, run, aliases)?.0)
    }

    /// (the number a criterion's metric reads in one run, or None; why it cannot be read, or None).
    pub fn read_metric(&self, exp: &Experiment, metric: &str, run: &Run, aliases: Option<&IndexMap<String, String>>) -> Res<(Option<f64>, Option<String>)> {
        let owned;
        let aliases: &IndexMap<String, String> = match aliases {
            Some(a) => a,
            None => {
                owned = self.metric_aliases()?;
                &owned
            }
        };
        Ok(read_metric_with(exp, metric, run, aliases))
    }

    /// What this read finds of a verified setting's source: a problem when it now states another value or no longer
    /// states it, a note when it states it on another line.
    fn source_now(&self, name: &str, subject: &str, s: &Setting) -> Option<Caveat> {
        let src = s.source.as_ref().filter(|_| s.status == "verified")?;
        let (state, why) = sources::recheck(src, &s.value, &self.root, name, true);
        match state.as_str() {
            "stale" | "conflict" => Some(Caveat::problem(&state, subject, format!("{name}: {why}"))),
            "moved" => Some(Caveat::note("source_changed", subject, format!("{name}: {why}"))),
            _ => None,
        }
    }

    /// A verified setting whose file no commit held before a past run that reads it was produced: the file postdates that
    /// run, so it may state what later runs use (a plan's line), never what that run used. A database row, and a run's
    /// own config or file, is read for that run itself and is never said so.
    /// A setting verified at a commit made after such a run postdates it the same way, whatever an earlier commit held.
    /// (the file, the variant whose setting it is, the earliest such run, the day it was produced, and the commit and its
    /// day when the setting cites one). With `read`, the arms a finding reads, only the runs that report one of them count.
    /// A setting read from the working tree with no commit postdates a run where git last changed its line after the
    /// run, or no commit holds the line yet. With `run`, only that run counts (a run add says it of the run it records).
    pub fn source_postdates(&self, exp: &Experiment, name: &str, s: &Setting, read: Option<&HashSet<String>>, run: Option<&str>) -> Res<Option<Postdates>> {
        self.source_postdates_of(exp, name, s, read, run, None)
    }

    /// The same, dated only against the runs in `counted` where it is given: the runs a reading counts.
    #[allow(clippy::too_many_arguments)]
    pub fn source_postdates_of(&self, exp: &Experiment, name: &str, s: &Setting, read: Option<&HashSet<String>>, run: Option<&str>, counted: Option<&HashSet<String>>) -> Res<Option<Postdates>> {
        let Some(src) = s.source.as_ref().filter(|x| x.kind == "file" && !sources::is_store_row(x, &self.root)) else { return Ok(None) };
        let Some(path) = src.path.as_deref().filter(|p| !p.is_empty()) else { return Ok(None) };
        let variant = match exp.split(name) {
            (Some(v), rest) if v.setting(rest).is_some() => Some(v.name.clone()),
            _ => None,
        };
        let reports = |r: &Run, v: &str| r.metrics.keys().any(|m| m.starts_with(&format!("{v}.")));
        // a variant's own setting is dated against that variant's runs: those that report its numbers, or, for a run
        // that names no variant in its numbers, one whose spec holds it
        let names_variants = |r: &Run| exp.variants.iter().any(|v| reports(r, &v.name));
        let reads = |r: &Run| {
            variant.as_ref().map_or(true, |v| reports(r, v) || (!names_variants(r) && r.arm_specs.contains_key(v)))
                && read.filter(|a| !a.is_empty()).map_or(true, |a| a.iter().any(|v| reports(r, v)))
                && counted.map_or(true, |c| c.contains(&r.id))
        };
        // a past run: made before the record began, so its settings are read back from files
        let Some(began) = self.began()? else { return Ok(None) };
        let first_run = self
            .standing_runs()?
            .iter()
            // a run's own file (its config, its output) is that run's, whenever it was committed
            .filter(|r| run.map_or(true, |id| r.id == id))
            .filter(|r| r.experiment == exp.id && reads(r) && !r.files.iter().any(|f| f.path == path) && r.receipt.config.as_ref().map_or(true, |c| c.path != path) && super::support::made_before_start(r, &began) == Some(true))
            .filter_map(|r| super::support::run_produced(r).and_then(|p| crate::pytime::parse_iso(p).map(|t| (t, r.id.clone(), p.to_string()))))
            .min_by_key(|(t, _, _)| *t);
        let Some((when, rid, at)) = first_run else { return Ok(None) };
        let day = utc_day(&at);
        if let Some(commit) = src.commit.as_deref().filter(|c| !c.is_empty()) {
            let made = sources::git(&self.root, &["show", "-s", "--format=%cI", commit]).map(|t| t.trim().to_string()).filter(|t| !t.is_empty());
            return Ok(match made.as_deref().and_then(crate::pytime::parse_iso) {
                Some(c) if c > when => {
                    let (cday, day) = days_apart(&made.unwrap_or_default(), &at);
                    Some((path.to_string(), variant, rid, day, Some((crate::checkout::short(commit), cday, None))))
                }
                _ => None,
            });
        }
        let committed = sources::path_first_committed(&self.root, path).and_then(|c| crate::pytime::parse_iso(&c));
        if !committed.map_or(false, |c| c <= when) {
            return Ok(Some((path.to_string(), variant, rid, day, None)));
        }
        // a file committed before the run, read from the working tree: its line is as old as the commit that last
        // changed it
        let line = src.resolved.as_ref().and_then(|r| r.line).or(src.line).filter(|n| *n > 0);
        Ok(match line.and_then(|n| sources::line_last_changed(&self.root, path, n).map(|b| (n, b))) {
            Some((n, (_, None))) => Some((path.to_string(), variant, rid, day, Some((String::new(), String::new(), Some(n))))),
            Some((n, (sha, Some(m)))) if crate::pytime::parse_iso(&m).map_or(false, |c| c > when) => {
                let (cday, day) = days_apart(&m, &at);
                Some((path.to_string(), variant, rid, day, Some((crate::checkout::short(&sha), cday, Some(n)))))
            }
            _ => None,
        })
    }

    /// A setting's status as this read finds it: one verified from a line no commit before a past run held reads
    /// provisional, as its note says (source_postdates_run); every other as it is kept.
    pub fn status_read(&self, exp: &Experiment, name: &str, s: &Setting) -> Res<String> {
        Ok(if s.status == "verified" && !s.per_run && self.setting_postdates(exp, name, s, None)?.is_some() { "provisional".into() } else { s.status.clone() })
    }

    /// The runs some reading of the experiment counts (a criterion's finding), and those it leaves out only for being
    /// under an earlier spec, which count again once read under this one; None where it has no rule. A setting of the
    /// experiment is dated only against those, as each reading dates it against its own: never a run left out for a
    /// config that disagrees, or outside every rule's selectors.
    pub fn counted_by_readings(&self, exp: &Experiment) -> Res<Option<HashSet<String>>> {
        let crits: Vec<Criterion> = self.criteria_of(&exp.id)?.into_iter().filter(|c| c.retracted.is_none()).collect();
        if crits.is_empty() {
            return Ok(None);
        }
        let mut out = HashSet::new();
        for c in &crits {
            // a run under an earlier spec counts again once read under this one: a setting is dated against it too
            out.extend(self.finding(c)?.observations.iter().filter(|o| o.counted || o.reason.starts_with("under an earlier spec")).map(|o| o.run.clone()));
        }
        Ok(Some(out))
    }

    /// Whether a setting of the experiment postdates a run, as the experiment's own views date it: against the runs some
    /// reading counts (and `run` alone, where one is given and counted), never a run no reading counts.
    pub fn setting_postdates(&self, exp: &Experiment, name: &str, s: &Setting, run: Option<&str>) -> Res<Option<Postdates>> {
        let counted = self.counted_by_readings(exp)?;
        if let (Some(r), Some(c)) = (run, counted.as_ref()) {
            if !c.contains(r) {
                return Ok(None);
            }
        }
        self.source_postdates_of(exp, name, s, None, run, counted.as_ref())
    }

    /// Everything about an experiment's settings a finding on it rests on.
    /// As the experiment's own views say them, a file dated only against the runs some reading of it counts.
    pub fn setting_caveats(&self, exp: &Experiment, has_origin: bool, arms: Option<&HashSet<String>>) -> Res<Vec<Caveat>> {
        let counted = self.counted_by_readings(exp)?;
        self.setting_caveats_counted(exp, has_origin, arms, None, counted.as_ref())
    }


    /// The same, a file dated only against the runs a reading counts (`counted`), where it is given.
    pub fn setting_caveats_counted(&self, exp: &Experiment, has_origin: bool, arms: Option<&HashSet<String>>, read: Option<&HashSet<String>>, counted: Option<&HashSet<String>>) -> Res<Vec<Caveat>> {
        let mut out: Vec<Caveat> = Vec::new();
        // the settings one file states for runs made before any commit of it, said once for the file
        let mut later: IndexMap<Postdates, Vec<String>> = IndexMap::new();
        for (name, s) in exp.all_settings() {
            let subject = format!("{}/{name}", exp.id);
            if s.per_run {
                continue;
            }
            if s.conflict.is_none() && (name.starts_with("population.") || name.contains(".population.")) {
                // declaring one is never work to do; a verified one is still re-read
                out.extend(self.source_now(&name, &subject, s).filter(|c| c.problem));
                continue;
            }
            if let Some(said) = sources::said_conflict(s) {
                out.push(Caveat::problem("conflict", &subject, format!("{name}: {said}")));
                continue;
            }
            if s.value.is_null() {
                out.push(Caveat::new(
                    if s.required { "unknown" } else { "unknown_optional" },
                    &subject,
                    s.required,
                    format!("{name} is unknown{}", if s.required { "" } else { " (optional)" }),
                ));
                continue;
            }
            if s.status == "inherited" {
                if has_origin {
                    out.push(Caveat::note("inherited", &subject, format!("{name} = {} is inherited from the parent project", show(&s.value))));
                } else {
                    out.push(Caveat::new("inherited_without_parent", &subject, s.required, format!("{name} is marked inherited, but nothing records where it came from")));
                }
                continue;
            }
            let vouch = self.vouch(exp, &name, s)?;
            let rejected = self.rulings_on(&subject)?.into_iter().rev().find(|d| d.action == "reject");
            if let Some(rj) = &rejected {
                if vouch.as_ref().map_or(true, |v| v.at < rj.at) && (rj.value.is_null() || same_value(&rj.value, &s.value)) {
                    out.push(Caveat::problem(
                        "rejected_setting",
                        &subject,
                        format!("{name} = {} was rejected{} ({}): {}", show(&s.value), actor::by_writer(&rj.by), rj.id, rj.why),
                    ));
                    // rejected, a verified one is still re-read
                    out.extend(self.source_now(&name, &subject, s).filter(|c| c.problem));
                    continue;
                }
            }
            if s.status == "provisional" {
                if let Some(v) = &vouch {
                    out.push(Caveat::note("vouched", &subject, format!("{name} = {} is provisional · vouched for{} ({})", show(&s.value), actor::by_writer(&v.by), v.id)));
                } else {
                    let where_ = match &s.source {
                        Some(src) if !src.checkable() => format!(" (source {}, not checkable)", src.label()),
                        _ => String::new(),
                    };
                    let text = match s.failed.as_ref().filter(|f| !f.is_empty()) {
                        Some(f) => format!(
                            "{name} = {} is provisional: rb read {} and it does not state it ({f})",
                            show(&s.value),
                            s.source.as_ref().map(|x| x.label()).unwrap_or_else(|| "its source".into())
                        ),
                        None => format!("{name} = {} is provisional: no check of it is recorded{where_}", show(&s.value)),
                    };
                    out.push(Caveat::note("provisional", &subject, text));
                }
            } else {
                out.extend(self.source_now(&name, &subject, s));
                if let Some(key) = self.source_postdates_of(exp, &name, s, read, None, counted)? {
                    // provisional as this read finds it, so a vouch for its value is said as for a stored one
                    match &vouch {
                        Some(v) => out.push(Caveat::note("vouched", &subject, format!("{name} = {} is provisional · vouched for{} ({})", show(&s.value), actor::by_writer(&v.by), v.id))),
                        None => later.entry(key).or_default().push(name.clone()),
                    }
                }
            }
            if let Some(c) = &s.cited {
                if !same_value(&c.value, &s.value) {
                    out.push(Caveat::note("cited_differs", &subject, format!("{name} {} here vs {} in the cited source", show(&s.value), show(&c.value))));
                }
            }
        }
        for ((path, _, rid, day, at), names) in later {
            out.push(Caveat::note("source_postdates_run", &format!("{}/{}", exp.id, names[0]), postdates_said(&names, &path, &rid, &day, at.as_ref())));
        }
        out.extend(self.confounds(exp, arms)?);
        Ok(out)
    }

    /// Whether inherited values here have somewhere they came from.
    pub fn has_origin(&self, exp: &Experiment) -> Res<bool> {
        Ok(self.project()?.parent.is_some() || exp.relations.iter().any(|r| r.kind == "reproduces"))
    }

    /// The numbers the runs report that no criterion reads.
    pub fn unread_metrics(&self, exp: &Experiment, runs: &[Run], criteria: &[Criterion]) -> Res<Vec<String>> {
        let aliases = self.metric_aliases()?;
        let read = self.read_metrics(exp, criteria)?;
        let reported: BTreeSet<&String> = runs.iter().flat_map(|r| r.metrics.keys()).collect();
        Ok(reported.into_iter().filter(|k| !read.contains(&canon(k, exp, &aliases))).cloned().collect())
    }

    /// The metric names, as canon gives them, that an experiment's standing criteria read of a run.
    pub fn read_metrics(&self, exp: &Experiment, criteria: &[Criterion]) -> Res<BTreeSet<String>> {
        let aliases = self.metric_aliases()?;
        let mut read: BTreeSet<String> = BTreeSet::new();
        for c in criteria {
            if c.retracted.is_some() {
                continue;
            }
            let (subject, reference, metric, _) = self.arms(exp, c)?;
            for a in [&subject, &reference] {
                if !a.is_empty() {
                    read.insert(canon(&format!("{a}.{metric}"), exp, &aliases));
                }
            }
            let cn = canon(&c.metric, exp, &aliases);
            // where two or more variants compare, a criterion that names its arms reads no number by its bare name: a
            // run giving one names no variant, which rb run add refuses (writes.rs names_no_arm)
            if subject.is_empty() && reference.is_empty() || cn.starts_with("change.") || cn.starts_with("ratio.") || exp.live_variants().len() < 2 {
                read.insert(cn.clone());
            }
            let (head, _, rest) = partition(&cn, ".");
            if head == "change" && !rest.is_empty() {
                let (v2, _, m2) = partition(rest, ".");
                let base_metric = if !m2.is_empty() && exp.variant(v2).is_some() { m2 } else { rest };
                for v in exp.live_variants() {
                    read.insert(format!("{}.{base_metric}", v.name));
                }
            }
        }
        // an experiment of one variant reads a number that names no variant as that variant's, and a rule on the
        // variant's name reads one recorded without it (findings.rs value_of): base.acc and acc are the same number
        let live = exp.live_variants();
        if live.len() == 1 {
            let only = live[0].name.clone();
            for name in read.clone() {
                let (head, sep, rest) = partition(&name, ".");
                if head == only && sep && !rest.is_empty() {
                    read.insert(rest.to_string());
                } else if head != "change" && head != "ratio" && exp.variant(head).is_none() && !ROLES.contains(&head) {
                    read.insert(format!("{only}.{name}"));
                }
            }
        }
        Ok(read)
    }

    /// The comparisons the experiment's standing analyses report that no criterion reads.
    pub fn unread_comparisons(&self, exp: &Experiment, criteria: &[Criterion]) -> Res<Vec<Dict>> {
        let aliases = self.metric_aliases()?;
        let mut read: HashSet<(String, String, String, String)> = HashSet::new();
        for c in criteria {
            if c.retracted.is_none() {
                let (subject, reference, metric, _) = self.arms(exp, c)?;
                read.insert((subject, reference, metric, c.estimand.clone()));
            }
        }
        let live = self.live::<Analysis>()?;
        let replaced: HashSet<&String> = live.iter().flat_map(|a| a.supersedes.iter()).collect();
        let mut out = Vec::new();
        let mut seen: HashSet<(String, String, String, String)> = HashSet::new();
        for a in &live {
            if a.experiment != exp.id || replaced.contains(&a.id) {
                continue;
            }
            for c in &a.comparisons {
                let key = (c.subject.clone(), c.reference.clone(), aliases.get(&c.metric).cloned().unwrap_or_else(|| c.metric.clone()), c.estimand.clone());
                if read.contains(&key) || seen.contains(&key) {
                    continue;
                }
                seen.insert(key);
                let arms_ = if !c.reference.is_empty() {
                    format!("{} vs {}", c.subject, c.reference)
                } else if !c.subject.is_empty() {
                    c.subject.clone()
                } else {
                    "whole run".into()
                };
                let signed = !c.reference.is_empty();
                let iv = match (c.lo, c.hi) {
                    (Some(lo), Some(hi)) => format!(" [{}, {}]", assess::n4(Some(lo), signed), assess::n4(Some(hi), signed)),
                    _ => String::new(),
                };
                let est = if c.estimand.is_empty() { String::new() } else { format!(" ({})", c.estimand) };
                out.push(crate::dict! {
                    "analysis" => a.id.clone(), "subject" => c.subject.clone(), "reference" => c.reference.clone(), "metric" => c.metric.clone(),
                    "estimand" => c.estimand.clone(),
                    "text" => format!("{}: {arms_}{est} {}{iv} ({})", c.metric, assess::n4(Some(c.estimate), signed), a.id)
                });
            }
        }
        Ok(out)
    }

    /// A setting that differs between the arms without being declared in `varies` is a difference to review.
    pub fn confounds(&self, exp: &Experiment, arms: Option<&HashSet<String>>) -> Res<Vec<Caveat>> {
        let mut out = Vec::new();
        let variants: Vec<&Variant> = exp.live_variants().into_iter().filter(|v| arms.map_or(true, |a| a.contains(&v.name))).collect();
        let names: BTreeSet<String> = variants.iter().flat_map(|v| v.settings.iter().filter(|s| s.retracted.is_none() && !s.per_run).map(|s| s.name.clone())).collect();
        for name in &names {
            let values: Vec<(String, Value)> = variants.iter().map(|v| (v.name.clone(), exp.value_in(v, name))).collect();
            let distinct: HashSet<String> = values.iter().map(|(_, x)| canonical(x)).collect();
            if distinct.len() > 1 && !exp.varies.contains(name) {
                let all: Dict = exp.live_variants().iter().map(|v| (v.name.clone(), exp.value_in(v, name))).collect();
                let vouch = self.accepted_difference(exp, name, &all)?;
                let listing = values.iter().map(|(k, x)| format!("{} ({k})", show(x))).collect::<Vec<_>>().join(" vs ");
                match vouch {
                    None => out.push(Caveat::problem(
                        "confound",
                        &format!("{}/{name}", exp.id),
                        format!(
                            "undeclared difference: {name} {listing}; {} varies only {}",
                            exp.id,
                            if exp.varies.is_empty() { "nothing declared".to_string() } else { exp.varies.join(", ") }
                        ),
                    )),
                    Some(v) => out.push(Caveat::note(
                        "confound_accepted",
                        &format!("{}/{name}", exp.id),
                        format!("{name} differs between variants ({listing}); accepted{} ({})", actor::by_writer(&v.by), v.id),
                    )),
                }
            }
        }
        // read over every variant: two arms sharing a varied setting are a comparison held at one value of it
        let every = exp.live_variants();
        for name in &exp.varies {
            let values: HashSet<String> = every.iter().map(|v| canonical(&exp.value_in(v, name))).collect();
            if variants.len() > 1 && values.len() == 1 {
                // none set yet is not one value shared
                let none = every.iter().all(|v| exp.value_in(v, name).is_null());
                let what = if none { "no variant has a value for it yet" } else { "every variant has the same value" };
                out.push(Caveat::note("varies_unchanged", &format!("{}/{name}", exp.id), format!("{} declares it varies {name}, but {what}", exp.id)));
            }
        }
        Ok(out)
    }

    // ------------------------------------------------------------ findings: a criterion read on axes

    /// The per-run setting whose values are the independent units (`population.unit`), or None.
    pub fn unit_setting(&self, exp: &Experiment) -> Option<String> {
        let s = exp.setting("population.unit")?;
        match &s.value {
            Value::Str(v) if !crate::rbyaml::py_strip(v).is_empty() => Some(crate::rbyaml::py_strip(v).to_string()),
            _ => None,
        }
    }

    /// The per-run settings `population.repeat` declares repeat indexes (a seed, an epoch, a repeat's number): no
    /// condition, never what a reading pools, and never what links two arms' runs.
    pub fn repeat_settings(&self, exp: &Experiment) -> Vec<String> {
        let Some(s) = exp.setting("population.repeat") else { return Vec::new() };
        if s.retracted.is_some() {
            return Vec::new();
        }
        let names: Vec<String> = match &s.value {
            Value::Str(v) => v.split(',').map(|x| crate::rbyaml::py_strip(x).to_string()).collect(),
            Value::List(l) => l.iter().filter_map(|x| x.as_str().map(|t| crate::rbyaml::py_strip(t).to_string())).collect(),
            _ => Vec::new(),
        };
        names.into_iter().filter(|n| !n.is_empty()).collect()
    }

    /// (subject, reference, metric, problem): the arms and the bare metric a criterion reads.
    pub fn arms(&self, exp: &Experiment, crit: &Criterion) -> Res<(String, String, String, Option<String>)> {
        let aliases = self.metric_aliases()?;
        Ok(arms_with(exp, crit, &aliases))
    }

    /// Every analysis reading of one comparison, oldest first.
    pub fn analysis_readings(&self, exp: &Experiment, subject: &str, reference: &str, metric: &str, estimand: &str) -> Res<Vec<(Reading, Analysis)>> {
        let aliases = self.metric_aliases()?;
        let mut out = Vec::new();
        for a in self.all::<Analysis>()?.iter() {
            if a.experiment == exp.id {
                for rd in analysis_rows(a, subject, reference, metric, estimand, &aliases) {
                    out.push((rd, a.clone()));
                }
            }
        }
        Ok(out)
    }

    pub fn analysis_rows(&self, a: &Analysis, subject: &str, reference: &str, metric: &str, estimand: &str) -> Res<Vec<Reading>> {
        let aliases = self.metric_aliases()?;
        Ok(analysis_rows(a, subject, reference, metric, estimand, &aliases))
    }

    /// An analysis's comparison's estimate, lo and hi, each as the rule of a criterion reading it says it (with the most
    /// digits when several do), else as assess::said_all does.
    pub fn comparison_said(&self, experiment_id: &str, c: &Comparison) -> Res<[String; 3]> {
        let signed = !c.reference.is_empty();
        let xs = [Some(c.estimate), c.lo, c.hi];
        let mut out: [Option<String>; 3] = [None, None, None];
        let Ok(exp) = self.load::<Experiment>(experiment_id) else { return Ok(assess::said_all(xs, &out, signed)) };
        let aliases = self.metric_aliases()?;
        let mut r = Reading::of(&c.metric);
        (r.subject, r.reference, r.estimand) = (c.subject.clone(), c.reference.clone(), c.estimand.clone());
        (r.estimate, r.lo, r.hi) = (Some(c.estimate), c.lo, c.hi);
        for k in self.live::<Criterion>()?.iter().filter(|k| k.experiment == exp.id && k.estimand == c.estimand) {
            let (s, rf, m, _) = self.arms(&exp, k)?;
            if s != c.subject || rf != c.reference || aliases.get(&c.metric).unwrap_or(&c.metric) != &m {
                continue;
            }
            let direction = self.catalogue_entry(&m)?.map(|e| e.direction).unwrap_or_else(|| "none".into());
            for (o, said) in out.iter_mut().zip(assess::rule_numbers(k, &r, &direction)) {
                if let Some(said) = said.filter(|s| o.as_ref().map_or(true, |o| s.len() > o.len())) {
                    *o = Some(said);
                }
            }
        }
        Ok(assess::said_all(xs, &out, signed))
    }

    /// A record as rb last wrote it, from rb's own copies or git history, or None when neither has it.
    pub fn as_written(&self, kind: &str, id: &str) -> Res<Option<Obj>> {
        let entry = self.last_writes()?.get(&(kind.to_string(), id.to_string())).cloned();
        let Some(entry) = entry else { return Ok(None) };
        let Some(sha) = entry.get("sha256").filter(|v| v.truthy()).map(pyjson::py_str) else { return Ok(None) };
        let path = entry.get("path").filter(|v| v.truthy()).map(pyjson::py_str);
        let Some(form) = self.stored_form(kind, id, &sha, path.as_deref())? else { return Ok(None) };
        Ok(validate_kind(kind, form))
    }

    /// What differs between a record as rb last wrote it and its file now, field by field.
    pub fn changed_outside(&self, kind: &str, id: &str) -> Res<Vec<String>> {
        let entry = self.last_writes()?.get(&(kind.to_string(), id.to_string())).cloned();
        let Some(entry) = entry else { return Ok(Vec::new()) };
        let Some(sha) = entry.get("sha256").filter(|v| v.truthy()).map(pyjson::py_str) else { return Ok(Vec::new()) };
        let path = entry.get("path").filter(|v| v.truthy()).map(pyjson::py_str);
        let then = self.stored_form(kind, id, &sha, path.as_deref())?;
        let now_ = match self.form(kind, id) {
            Ok(f) => f,
            Err(_) => return Ok(Vec::new()),
        };
        let Some(then) = then else { return Ok(Vec::new()) };
        fn flat(v: &Value, at: &str, out: &mut IndexMap<String, Value>) {
            match v {
                Value::Dict(d) => {
                    for (key, item) in d {
                        flat(item, &if at.is_empty() { key.clone() } else { format!("{at}.{key}") }, out);
                    }
                }
                Value::List(l) if !l.is_empty() => {
                    for (i, item) in l.iter().enumerate() {
                        flat(item, &if at.is_empty() { i.to_string() } else { format!("{at}.{i}") }, out);
                    }
                }
                other => {
                    out.insert(at.to_string(), other.clone());
                }
            }
        }
        let (mut a, mut b) = (IndexMap::new(), IndexMap::new());
        flat(&Value::Dict(then), "", &mut a);
        flat(&Value::Dict(now_), "", &mut b);
        let mut keys: Vec<&String> = a.keys().chain(b.keys()).collect();
        keys.sort();
        keys.dedup();
        let mut out = Vec::new();
        let shown = |k: &str, v: Option<&Value>| crate::ledger::shown_edit(k, v).map(|v| v.as_ref().map(show).unwrap_or_else(|| "(absent)".into()));
        for k in keys {
            if a.get(k) != b.get(k) {
                out.push(match (shown(k, a.get(k)), shown(k, b.get(k))) {
                    (Some(then), Some(now)) => format!("{k}: {then} -> {now}"),
                    _ => format!("{k} changed"),
                });
            }
        }
        Ok(out)
    }

    /// Everything an analysis rests on through what it read: (kind, id, the chain from the analysis to it).
    pub fn upstream(&self, a: &Analysis) -> Res<Vec<(String, String, Vec<String>)>> {
        let mut out: Vec<(String, String, Vec<String>)> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        let mut frontier: std::collections::VecDeque<(Analysis, Vec<String>)> = std::collections::VecDeque::from([(a.clone(), vec![a.id.clone()])]);
        while let Some((node, chain)) = frontier.pop_front() {
            for rid in &node.reads {
                if seen.contains(rid) {
                    continue;
                }
                seen.insert(rid.clone());
                let k = self.kind_of(rid)?;
                let mut c2 = chain.clone();
                c2.push(rid.clone());
                out.push((k.unwrap_or("missing").to_string(), rid.clone(), c2.clone()));
                if k == Some("analysis") {
                    frontier.push_back((self.load::<Analysis>(rid)?, c2));
                } else if k == Some("run") {
                    for u in self.load::<Run>(rid)?.inputs {
                        if !seen.contains(&u.resource) {
                            seen.insert(u.resource.clone());
                            let mut c3 = c2.clone();
                            c3.push(u.resource.clone());
                            out.push(("resource".into(), u.resource.clone(), c3));
                        }
                    }
                }
            }
            for u in &node.inputs {
                if !seen.contains(&u.resource) {
                    seen.insert(u.resource.clone());
                    let mut c3 = chain.clone();
                    c3.push(u.resource.clone());
                    out.push(("resource".into(), u.resource.clone(), c3));
                }
            }
        }
        Ok(out)
    }

    /// (subject's value, reference's value, the difference, why it cannot be read) in one run.
    #[allow(clippy::type_complexity)]
    fn pair(&self, exp: &Experiment, subject: &str, reference: &str, metric: &str, crit: &Criterion, r: &Run, aliases: &IndexMap<String, String>) -> (Option<f64>, Option<f64>, Option<f64>, Option<String>) {
        if !selector_matches(&crit.r#where, r) {
            return (None, None, None, None);
        }
        if reference.is_empty() {
            if !selector_matches(&crit.subject_where, r) { return (None, None, None, None); }
            let name = if subject.is_empty() { crit.metric.clone() } else { format!("{subject}.{metric}") };
            let (v, p) = read_metric_with(exp, &name, r, aliases);
            return (v, None, v, p);
        }
        let (sv, ps) = if selector_matches(&crit.subject_where, r) { read_metric_with(exp, &format!("{subject}.{metric}"), r, aliases) } else { (None, None) };
        let (rv, pr) = if selector_matches(&crit.reference_where, r) { read_metric_with(exp, &format!("{reference}.{metric}"), r, aliases) } else { (None, None) };
        if ps.is_some() || pr.is_some() {
            return (None, None, None, ps.or(pr));
        }
        let mut diff = match (sv, rv) {
            (Some(s), Some(rr)) => Some(minus(s, rr)),
            _ => None,
        };
        let base = exp.by_role("baseline");
        let change = if crit.metric.starts_with("change.") && crit.reference.is_empty() {
            Some(crit.metric.clone())
        } else if !base.is_empty() && base[0].name == reference && exp.variant(subject).is_some() {
            Some(format!("change.{subject}.{metric}"))
        } else {
            None
        };
        if let Some(ch) = change.filter(|_| crit.subject_where.is_empty() && crit.reference_where.is_empty()) {
            let (given, pg) = read_metric_with(exp, &ch, r, aliases);
            if pg.is_some() {
                return (None, None, None, pg);
            }
            if given.is_some() {
                diff = given;
            }
        }
        (sv, rv, diff, None)
    }

    /// rb's own reading of the runs for one comparison.
    fn run_reading(&self, exp: &Experiment, crit: &Criterion, subject: &str, reference: &str, metric: &str, declared_at: &str) -> Res<RunReading> {
        let aliases = self.metric_aliases()?;
        let spec_now = self.spec_sha(exp);
        let snapshot_now = self.settings_snapshot(exp);
        let arms: Vec<String> = [subject, reference].iter().filter(|a| !a.is_empty()).map(|a| a.to_string()).collect();
        let arms_now = if arms.is_empty() { IndexMap::new() } else { self.arm_specs(exp) };
        let owned: Vec<String> = exp.variants.iter().map(|v| v.name.clone()).collect();
        let unit_setting = self.unit_setting(exp);
        let repeats = self.repeat_settings(exp);
        let design_factors: Vec<String> = exp.settings.iter().filter(|s| s.retracted.is_none() && s.design_factor == Some(true) && !separating(crit, &s.name)).map(|s| s.name.clone()).collect();
        let only_design_units = match unit_setting.as_deref() {
            Some(unit) => design_factors.iter().any(|name| name == unit),
            None => !design_factors.is_empty() && exp.all_settings().iter().filter(|(_, s)| s.per_run).all(|(name, s)| s.design_factor == Some(true) || separating(crit, name)),
        };
        // format 2's rules read the same runs their own way: the best unit, a relative change, a difference of means
        // A selected cell (--where on a design factor) is one cell: the rule's own estimand is read on its replications.
        // Across several cells, each cell is read on that estimand too, and the rule says it reads none across them.
        let read = |observations: &[Observation]| {
            let across = observations.iter().map(|o| cell_key(&o.per_run, &design_factors)).collect::<HashSet<_>>().len() > 1;
            let own = |cell: &[Observation]| rule_reading(crit, reading_of(cell, subject, reference, metric, &crit.estimand, unit_setting.as_deref()), cell, subject, reference, metric, unit_setting.as_deref(), false);
            let per_cell: Option<&dyn Fn(&[Observation]) -> ReadOut> = if crit.measure.is_empty() { None } else { Some(&own) };
            let out = design_reading_of(observations, subject, reference, metric, &crit.estimand, unit_setting.as_deref(), &design_factors, only_design_units, per_cell);
            rule_reading(crit, out, observations, subject, reference, metric, unit_setting.as_deref(), across)
        };
        let in_cell = |unit: String, per_run: &Dict| unit_within(unit, per_run, unit_setting.as_deref(), &design_factors);
        let mut runs: Vec<Run> = self.all::<Run>()?.iter().filter(|r| r.experiment == exp.id).cloned().collect();
        runs.sort_by(|a, b| (&a.created_at, &a.id).cmp(&(&b.created_at, &b.id)));
        let recorded: HashMap<String, String> = runs.iter().filter(|r| super::support::run_basis(r) == "unknown").map(|r| (r.id.clone(), r.created_at.clone())).collect();
        let mut obs: Vec<Observation> = Vec::new();
        let mut notes: Vec<Caveat> = Vec::new();
        let mut problems: Vec<Caveat> = Vec::new();
        let mut unit_of: HashMap<String, String> = HashMap::new();
        // runs this rule finds none of its numbers in: said where no rule of the experiment reads them either
        let mut unread_runs: Vec<Run> = Vec::new();
        #[allow(clippy::type_complexity)]
        let mut earlier: IndexMap<String, Vec<(Run, Option<f64>, Option<f64>, Option<f64>)>> = IndexMap::new();
        let mut superseded: Vec<(Run, String)> = Vec::new();
        for r in &runs {
            if !selected(crit, r) {
                // An edit cannot hide selected evidence by moving it out of the selector.
                if crit.scoped() && self.edited_outside("run", &r.id)?.is_some() {
                    if let Some(Obj::Run(written)) = self.as_written("run", &r.id)? {
                        if selected(crit, &written) {
                            problems.push(Caveat::problem("edited_outside_rb", &r.id, format!("run {} was edited outside rb and no longer matches the selector; restore it or adopt the edit", r.id)));
                        }
                    }
                }
                continue;
            }
            if r.retracted.is_some() && superseded_for(r, metric, Some((subject, reference))) {
                let ret = r.retracted.as_ref().unwrap();
                if !ret.scope.is_empty() {
                    notes.push(Caveat::note("superseded", &r.id, format!("{} superseded for {}{}", r.id, ret.scope.join(", "), crate::view::reason_part(&ret.why, &ret.by, ": "))));
                    superseded.push((r.clone(), format!("superseded for {}{}", ret.scope.join(", "), crate::view::reason_part(&ret.why, &ret.by, ": "))));
                }
                continue;
            }
            if let Some(ret) = &r.retracted {
                notes.push(Caveat::note(
                    "superseded_elsewhere",
                    &r.id,
                    format!("{} superseded for {} ({}); its {metric} still counts", r.id, ret.scope.join(", "), crate::view::reason_said(&ret.why, &ret.by)),
                ));
            }
            let (sv, rv, diff, problem) = self.pair(exp, subject, reference, metric, crit, r, &aliases);
            if let Some(p) = problem.filter(|p| !p.is_empty()) {
                notes.push(Caveat::note("not_read", &r.id, format!("{} not read: {p}", r.id)));
                continue;
            }
            let Some(value) = first_some(diff, sv, rv) else {
                if r.retracted.is_none() {
                    unread_runs.push(r.clone());
                }
                continue;
            };
            let arm = if diff.is_some() {
                String::new()
            } else if sv.is_some() {
                subject.to_string()
            } else {
                reference_observation(subject, reference)
            };
            let mut unit = scoped_unit(r, crit, unit_setting.as_deref(), &unit_of);
            // a repeat index links no runs: with no unit declared, the runs pair by their other per-run values (a repeat
            // is a case within its unit), and where none is left that tells runs apart (none but the arms' selectors),
            // each run is a unit of its own, the arms apart
            if unit_setting.is_none() && !unit.is_empty() && repeats.iter().any(|k| r.per_run.contains_key(k)) {
                let mut scope = r.clone();
                scope.per_run.retain(|k, _| !separating(crit, k) && !repeats.contains(k));
                let selects = |k: &str| [&crit.r#where, &crit.subject_where, &crit.reference_where].iter().any(|w| w.contains_key(k));
                if scope.per_run.keys().all(|k| selects(k)) {
                    let whole = unit_for(r, None, &unit_of);
                    unit = if arm.is_empty() || subject == reference { whole } else { format!("{whole} · {}", arm.trim_end_matches(" [reference]")) };
                } else {
                    unit = unit_for(&scope, None, &unit_of);
                }
            }
            unit_of.insert(r.id.clone(), unit.clone());
            let mut produced = super::support::run_produced(r).map(str::to_string);
            if produced.is_none() && super::support::run_basis(r) != "unknown" {
                produced = Some(r.created_at.clone());
            }
            let mut reason = String::new();
            let mism: Vec<Dict> = r.receipt.config.as_ref().map(|c| c.given().mismatches).unwrap_or_default();
            if r.synthetic {
                reason = "synthetic: recorded as synthetic data, not a result about any model".into();
            } else if let Some(why) = self.edited_outside("run", &r.id)? {
                if self.shallow_cut("run", &r.id)? {
                    reason = SHALLOW.into();
                    problems.push(Caveat::problem("shallow_clone", &r.id, format!("{why}; it is not read {}", fetch_whole())));
                } else {
                    reason = EDITED.into();
                    problems.push(Caveat::problem(
                        "edited_outside_rb",
                        &r.id,
                        format!("run {} was edited outside rb: {why}; it is not read until restored or adopted", r.id),
                    ));
                }
            } else if !self.same_spec(r, &spec_now, &arms, &arms_now) {
                let d = diff_or(&r.settings, &snapshot_now, &arms, &owned);
                reason = format!("under an earlier spec: {d}");
                earlier.entry(r.spec_sha256.clone()).or_default().push((r.clone(), sv, rv, diff));
            } else if !mism.is_empty() {
                let said: Vec<String> = mism
                    .iter()
                    .map(|m| {
                        format!(
                            "{} {} (the spec says {})",
                            m.get("name").map(pyjson::py_str).unwrap_or_default(),
                            show(m.get("ran").unwrap_or(&Value::Null)),
                            show(m.get("spec").unwrap_or(&Value::Null))
                        )
                    })
                    .collect();
                reason = format!("ran with {}", assess::first_few(&said, 3, "; "));
            } else if unit.is_empty() {
                reason = format!("no {} value, so rb cannot tell which unit it is", unit_setting.clone().unwrap_or_else(|| "None".into()));
            } else if let Some(key) = design_factors.iter().find(|key| r.per_run.get(*key).map_or(true, Value::is_null)) {
                reason = format!("no {key} value, so rb cannot place this run in its declared design cell");
                problems.push(Caveat::problem("design_cell_unknown", &r.id, reason.clone()));
            }
            for u in &r.inputs {
                if self.kind_of(&u.resource)? == Some("resource") {
                    let res: Resource = self.load(&u.resource)?;
                    if let Some(rt) = &res.retracted {
                        if reason.is_empty() {
                            problems.push(Caveat::problem("input_retracted", &r.id, format!("{} read {}, which was retracted since{}", r.id, res.id, crate::view::reason_part(&rt.why, &rt.by, ": "))));
                        }
                    }
                }
            }
            // a relative change and a difference of means read each arm's own number: a run that gives both is two
            // observations, one of each arm, in its unit
            let parts: Vec<(f64, String)> = match (sv, rv) {
                (Some(s), Some(v)) if !crit.measure.is_empty() => vec![(s, subject.to_string()), (v, reference_observation(subject, reference))],
                _ => vec![(value, arm)],
            };
            // nobody knowing when it was produced, it was by the time it was recorded
            let produced = produced.map(|p| by_record(&p, &r.created_at));
            let unit = in_cell(unit, &r.per_run);
            for (value, arm) in parts {
                let mut o = Observation::of(&r.id, value, reason.is_empty(), &r.basis);
                o.unit = unit.clone();
                o.arm = arm;
                o.before_declared = crate::pytime::before(Some(produced.as_deref().unwrap_or(&r.created_at)), Some(declared_at));
                o.reason = reason.clone();
                o.per_run = r.per_run.clone();
                o.produced_at = produced.clone();
                o.produced_basis = super::support::run_basis(r).to_string();
                o.repeats = r.repeats.clone();
                obs.push(o);
            }
        }
        for (rid, entry) in self.deleted_runs(&exp.id)? {
            let sha = entry.get("sha256").filter(|v| v.truthy()).map(pyjson::py_str).unwrap_or_default();
            let path = entry.get("path").filter(|v| v.truthy()).map(pyjson::py_str);
            let form = self.stored_form("run", &rid, &sha, path.as_deref())?;
            let gone: Option<Run> = form.and_then(|f| {
                let mut errs = Vec::new();
                Run::validate_value(&Value::Dict(f), &mut errs).filter(|_| errs.is_empty())
            });
            if let Some(g) = &gone {
                if !selected(crit, g) { continue; }
                let fully = g.retracted.as_ref().map_or(false, |x| x.scope.is_empty());
                let (sv, rv, diff, problem) = self.pair(exp, subject, reference, metric, crit, g, &aliases);
                if fully || problem.map_or(false, |p| !p.is_empty()) || first_some(diff, sv, rv).is_none() {
                    continue;
                }
            }
            problems.push(Caveat::problem(
                "edited_outside_rb",
                &rid,
                format!("run {rid} was deleted outside rb{}: rb doctor --restore puts it back", if gone.is_some() { format!(" (it reported {metric})") } else { String::new() }),
            ));
        }
        if !unread_runs.is_empty() {
            let others: Vec<Criterion> = self.criteria_of(&exp.id)?.into_iter().filter(|c| c.retracted.is_none()).collect();
            let mut named: Vec<String> = Vec::new();
            for r in &unread_runs {
                let read_elsewhere = others.iter().any(|c| {
                    let (s2, r2, m2, _) = arms_with(exp, c, &aliases);
                    let (a, b, d, _) = self.pair(exp, &s2, &r2, &m2, c, r, &aliases);
                    first_some(d, a, b).is_some()
                });
                if !read_elsewhere {
                    named.push(r.id.clone());
                }
            }
            // a run that holds a number this rule reads, outside the per-run selectors of the arm that reads it, is said
            // so: its name is no mismatch
            let holds = |r: &Run| {
                let names: Vec<String> = if reference.is_empty() { vec![format!("{subject}.{metric}")] } else { vec![format!("{subject}.{metric}"), format!("{reference}.{metric}")] };
                names.iter().any(|n| read_metric_with(exp, n, r, &aliases).0.is_some())
            };
            let arm_scoped = !crit.subject_where.is_empty() || !crit.reference_where.is_empty();
            let outside: Vec<String> = unread_runs.iter().filter(|r| arm_scoped && named.contains(&r.id) && holds(r)).map(|r| r.id.clone()).collect();
            named.retain(|n| !outside.contains(n));
            if !outside.is_empty() {
                let at = |w: &Dict| w.iter().map(|(k, v)| format!("{k}={}", show(v))).collect::<Vec<_>>().join(" and ");
                let arms: Vec<String> = [(subject, &crit.subject_where), (reference, &crit.reference_where)]
                    .iter()
                    .filter(|(a, w)| !a.is_empty() && !w.is_empty())
                    .map(|(a, w)| format!("{a} at {}", at(w)))
                    .collect();
                notes.push(Caveat::note(
                    "counts_toward_nothing",
                    &exp.id,
                    format!(
                        "{} {} on {} and {} toward no rule: {} a number this rule reads, outside its per-run selectors ({})",
                        assess::first_few(&outside, 4, ", "),
                        if outside.len() == 1 { "is" } else { "are" },
                        exp.id,
                        if outside.len() == 1 { "counts" } else { "count" },
                        if outside.len() == 1 { "it holds" } else { "each holds" },
                        arms.join("; ")
                    ),
                ));
            }
            if !named.is_empty() {
                let reports: BTreeSet<String> = unread_runs.iter().filter(|r| named.contains(&r.id)).flat_map(|r| r.metrics.keys().cloned()).collect();
                let wants = match (subject.is_empty(), reference.is_empty()) {
                    (true, _) => metric.to_string(),
                    (false, true) => format!("{subject}.{metric}"),
                    _ => format!("{subject}.{metric} and {reference}.{metric}"),
                };
                notes.push(Caveat::note(
                    "counts_toward_nothing",
                    &exp.id,
                    format!(
                        "{} {} on {} and {} toward no rule: {} {}, and this rule reads {wants}",
                        assess::first_few(&named, 4, ", "),
                        if named.len() == 1 { "is" } else { "are" },
                        exp.id,
                        if named.len() == 1 { "counts" } else { "count" },
                        if named.len() == 1 { "it reports" } else { "they report" },
                        assess::first_few(&reports.into_iter().collect::<Vec<_>>(), 4, ", ")
                    ),
                ));
            }
        }
        let counted: Vec<Observation> = obs.iter().filter(|o| o.counted).cloned().collect();
        for o in &obs {
            if !o.counted && !not_read(&o.reason) {
                notes.push(Caveat::note("not_counted", &o.run, format!("{} not counted: {}", o.run, o.reason)));
            }
        }
        if unit_setting.is_none() && counted.len() >= 3 {
            // within each design cell, where a declared unit would be counted (one cell when no factor is declared)
            let mut cells: IndexMap<String, Vec<&Observation>> = IndexMap::new();
            for o in &counted {
                cells.entry(cell_key(&o.per_run, &design_factors)).or_default().push(o);
            }
            let keys: BTreeSet<&String> = counted.iter().flat_map(|o| o.per_run.keys()).collect();
            // (values, the setting, units, the first cell it takes fewer values than units in, how many more cells)
            let mut nesting: Vec<(usize, String, usize, String, usize)> = Vec::new();
            for k in keys {
                // a design factor is no unit, and neither is a setting the criterion selects or separates its arms by
                if design_factors.contains(k) || repeats.contains(k) || crit.r#where.contains_key(k) || crit.subject_where.contains_key(k) || crit.reference_where.contains_key(k) { continue; }
                let mut fewer: Vec<(usize, usize, &String)> = Vec::new();
                for (cell, observations) in &cells {
                    let n_units = observations.iter().map(|o| &o.unit).collect::<HashSet<_>>().len();
                    let vals: HashSet<String> = observations.iter().filter_map(|o| o.per_run.get(k).filter(|v| !v.is_null()).map(show)).collect();
                    if 1 < vals.len() && vals.len() < n_units {
                        fewer.push((vals.len(), n_units, cell));
                    }
                }
                if let Some(&(n_vals, n_units, cell)) = fewer.first() {
                    nesting.push((n_vals, k.clone(), n_units, cell.clone(), fewer.len() - 1));
                }
            }
            nesting.sort();
            if let Some((n_vals, k, n_units, cell, more)) = nesting.first() {
                let others = nesting[1..].iter().map(|(n2, k2, ..)| format!("{k2} ({n2})")).collect::<Vec<_>>().join(", ");
                let within = match more {
                    _ if design_factors.is_empty() => String::new(),
                    0 => format!(" in design cell {cell}"),
                    more => format!(" in design cells {cell} and {more} more"),
                };
                notes.push(Caveat::note(
                    "unit_hint",
                    &exp.id,
                    format!(
                        "{k} takes {n_vals} values across {} {n_units} units{within}{}: if runs sharing {} {k} are not independent, declare it the unit (rb spec set {} population.unit {k})",
                        if design_factors.is_empty() { "these" } else { "the" },
                        if others.is_empty() { String::new() } else { format!(" (also {others})") },
                        if k.starts_with(['a', 'e', 'i', 'o', 'u']) { "an" } else { "a" },
                        exp.id
                    ),
                ));
            }
        }
        for o in &counted {
            if let Some(rep) = o.repeats.as_ref().filter(|r| !r.is_empty()) {
                if let Some(first) = counted.iter().find(|x| x.run == *rep) {
                    if first.arm == o.arm {
                        let same = isclose(first.value, o.value, 1e-9, 1e-12);
                        notes.push(Caveat::note(
                            "repeat",
                            &o.run,
                            format!(
                                "{} repeats {rep}: {}",
                                o.run,
                                if same { "the same number, a check that holds".to_string() } else { format!("it differs by {}", fmt_float(minus(o.value, first.value), "+g")) }
                            ),
                        ));
                    }
                }
            }
        }
        let mut retracted_inputs: BTreeSet<String> = BTreeSet::new();
        for r in &runs {
            if !selected(crit, r) { continue; }
            for u in &r.inputs {
                if self.kind_of(&u.resource)? == Some("resource") && self.load::<Resource>(&u.resource)?.retracted.is_some() {
                    retracted_inputs.insert(u.resource.clone());
                }
            }
        }
        if !retracted_inputs.is_empty() {
            let mut blind: Vec<String> = Vec::new();
            for o in &counted {
                if self.load::<Run>(&o.run)?.inputs.is_empty() {
                    blind.push(o.run.clone());
                }
            }
            if !blind.is_empty() {
                notes.push(Caveat::note(
                    "inputs_unknown",
                    &exp.id,
                    format!(
                        "{} recorded no inputs: whether {} read {} (retracted since) is unknown",
                        assess::first_few(&blind, 4, ", "),
                        if blind.len() == 1 { "it" } else { "they" },
                        retracted_inputs.iter().cloned().collect::<Vec<_>>().join(", ")
                    ),
                ));
            }
        }
        let (mut reading, unit_values, read_units) = read(&counted);
        // a paired reading of two arms recorded in runs of their own says what it pairs them by, with the same runs read
        // unpaired beside it: whether runs that share those values are linked is not rb's to say
        let mut pairing: Option<Pairing> = None;
        if let Some(rd) = reading.as_mut().filter(|r| r.method.starts_with("paired difference") && r.design.is_empty()) {
            let apart: Vec<&Observation> = counted.iter().filter(|o| !o.arm.is_empty() && read_units.contains(&o.unit)).collect();
            if !apart.is_empty() {
                let mut values: IndexMap<String, BTreeSet<String>> = IndexMap::new();
                let (by, declared) = match &unit_setting {
                    Some(u) => (vec![u.clone()], true),
                    None => {
                        for u in read_units.iter().filter(|u| apart.iter().any(|o| o.unit == **u)) {
                            for part in u.split(" · ") {
                                if let Some((k, v)) = part.split_once('=') {
                                    values.entry(k.to_string()).or_default().insert(v.to_string());
                                }
                            }
                        }
                        let varying: Vec<String> = values.iter().filter(|(_, v)| v.len() > 1).map(|(k, _)| k.clone()).collect();
                        (if varying.is_empty() { values.keys().cloned().collect() } else { varying }, false)
                    }
                };
                if !by.is_empty() {
                    // every run of the two arms, those the paired reading leaves out (a unit one arm alone ran) too: the
                    // arms' own means, never a second reading of the paired runs alone
                    let all_runs = counted.iter().any(|o| !o.arm.is_empty() && !read_units.contains(&o.unit));
                    // a run that gives both arms is read as each arm's number, so the arms' means are over every run
                    // of theirs; one that gives only the difference (change.<metric>) has no arm to read apart
                    let mut split: Vec<Observation> = Vec::new();
                    let mut diff_only = false;
                    // a run read as both arms' numbers is two of the figure's values, not two runs
                    let mut both_split = false;
                    for o in counted.iter().filter(|o| read_units.contains(&o.unit) || !o.arm.is_empty()) {
                        let mut x = o.clone();
                        if !x.arm.is_empty() {
                            x.unit = format!("{}\u{0}{}", x.unit, of_subject(subject, reference, &x.arm));
                            split.push(x);
                            continue;
                        }
                        let both = runs.iter().find(|r| r.id == o.run).map(|r| self.pair(exp, subject, reference, metric, crit, r, &aliases));
                        match both {
                            Some((Some(sv), Some(rv), _, None)) if subject != reference => {
                                both_split = true;
                                for (arm, value) in [(subject, sv), (reference, rv)] {
                                    let mut y = x.clone();
                                    y.arm = arm.to_string();
                                    y.value = value;
                                    y.unit = format!("{}\u{0}{}", x.unit, of_subject(subject, reference, arm));
                                    split.push(y);
                                }
                            }
                            _ => diff_only = true,
                        }
                    }
                    // its units are runs where each holds one run, else units of each arm
                    let one_run_each = {
                        let mut per: HashMap<&str, HashSet<&str>> = HashMap::new();
                        for o in &split {
                            per.entry(o.unit.as_str()).or_default().insert(o.repeats.as_deref().filter(|r| !r.is_empty()).unwrap_or(o.run.as_str()));
                        }
                        per.values().all(|runs| runs.len() == 1)
                    };
                    let unpaired = (!diff_only).then(|| reading_of(&split, subject, reference, metric, &crit.estimand, unit_setting.as_deref()).0).flatten().map(|mut u| {
                        // an arm's unit apart from the other arm's: never counted as the declared unit's values, of which there are half as many
                        u.unit = if one_run_each && !declared && !both_split { "run".into() } else { "unit".into() };
                        u
                    });
                    // the units are counted by what pairs them, where rb has no other name for them
                    if by.len() == 1 && !declared && rd.unit == "unit" {
                        rd.unit = by[0].clone();
                    }
                    // a setting the rule selects on is a condition of that rule, never offered as a repeat
                    let selects = |k: &str| [&crit.r#where, &crit.subject_where, &crit.reference_where].iter().any(|w| w.contains_key(k));
                    let counters = by.iter().filter(|k| !selects(k) && values.get(*k).map_or(false, |v| counter_values(v.iter().map(String::as_str)))).cloned().collect();
                    pairing = Some(Pairing { by, declared, unpaired, counters, all_runs, diff_only });
                }
            }
        }
        if reading.as_ref().map_or(false, |r| !r.design.is_empty()) {
            for setting in exp.settings.iter().filter(|s| s.design_factor == Some(true) && design_factors.contains(&s.name)) {
                notes.push(Caveat::note("design_factor", &format!("{}/{}", exp.id, setting.name), format!("{} declared a fixed design factor{} at {}; cells describe the recorded design, not independent samples", setting.name, actor::by_writer(&setting.set_by), setting.set_at)));
            }
        }
        if crit.scoped() {
            // each run named, with its values of what the rule selects by, so rb show lists the runs it leaves out
            let keys: Vec<&String> = crit.r#where.keys().chain(crit.subject_where.keys()).chain(crit.reference_where.keys()).fold(Vec::new(), |mut v, k| {
                if !v.contains(&k) {
                    v.push(k);
                }
                v
            });
            let skipped: Vec<String> = runs
                .iter()
                .filter(|r| r.retracted.is_none() && !selected(crit, r))
                .map(|r| {
                    let at: Vec<String> = keys.iter().map(|k| format!("{k}={}", r.per_run.get(*k).map(show).unwrap_or_else(|| "none".into()))).collect();
                    format!("{} ({})", r.id, at.join(", "))
                })
                .collect();
            if !skipped.is_empty() {
                let head = if skipped.len() == 1 { "1 run outside this criterion's per-run selectors is not read".to_string() } else { format!("{} runs outside this criterion's per-run selectors are not read", skipped.len()) };
                notes.push(Caveat::note("run_scope", &crit.id, format!("{head}: {}", skipped.join(", "))));
            }
        }
        // a paired reading leaves out a run whose unit has one arm only, and one arm's run in a unit where a run gives
        // the difference by itself: it is shown, not counted; across design cells, as each cell's reading does
        let mut left_out: Vec<Observation> = Vec::new();
        if unit_values.is_some() || reading.as_ref().map_or(false, |r| !r.design.is_empty()) {
            let whole: HashSet<String> = counted.iter().filter(|o| o.arm.is_empty()).map(|o| o.unit.clone()).collect();
            for o in obs.iter_mut().filter(|o| o.counted && (!read_units.contains(&o.unit) || (!o.arm.is_empty() && whole.contains(&o.unit)))) {
                o.counted = false;
                o.reason = if read_units.contains(&o.unit) {
                    "its unit has a run that gives the difference by itself, so the paired reading leaves it out".to_string()
                } else {
                    format!("its unit has no {} run, so the paired reading leaves it out", if o.arm == subject { reference } else { subject })
                };
                notes.push(Caveat::note("not_counted", &o.run, format!("{} not counted: {}", o.run, o.reason)));
                left_out.push(o.clone());
            }
        }
        let in_reading: Vec<Observation> = obs.iter().filter(|o| o.counted).cloned().collect();
        if let Some(rd) = &reading {
            if rd.lo.is_some() && rd.lo == rd.hi && rd.n.unwrap_or(0) >= 3 {
                notes.push(Caveat::note(
                    "no_spread",
                    &crit.id,
                    format!(
                        "every {} gives the same value: the interval has no width, and says nothing about variation the runs never showed",
                        if rd.unit.is_empty() { "unit" } else { &rd.unit }
                    ),
                ));
            }
        }
        // with the unit declared, a per-run setting the rule neither selects nor reads by design cell, with more than one
        // value among the runs it counts: the reading pools those conditions, and says so
        for s in exp.settings.iter().filter(|s| unit_setting.is_some() && s.retracted.is_none() && s.per_run && Some(s.name.as_str()) != unit_setting.as_deref() && s.design_factor != Some(true)) {
            if [&crit.r#where, &crit.subject_where, &crit.reference_where].iter().any(|w| w.contains_key(&s.name)) || repeats.contains(&s.name) {
                continue;
            }
            // a setting the declared unit is made of (each unit of several runs holds one value of it: a config's model
            // and effort) is that unit's, never a condition the reading pools
            let mut per_unit: IndexMap<&str, (BTreeSet<String>, BTreeSet<String>)> = IndexMap::new();
            for o in &in_reading {
                let e = per_unit.entry(o.unit.as_str()).or_default();
                e.0.insert(o.per_run.get(&s.name).map(unit_value).unwrap_or_default());
                e.1.insert(o.repeats.clone().filter(|r| !r.is_empty()).unwrap_or_else(|| o.run.clone()));
            }
            let several: Vec<&(BTreeSet<String>, BTreeSet<String>)> = per_unit.values().filter(|(_, runs)| runs.len() > 1).collect();
            if !several.is_empty() && several.iter().all(|(vals, _)| vals.len() == 1) && per_unit.values().all(|(vals, _)| vals.len() == 1) {
                continue;
            }
            let mut seen: Vec<Value> = Vec::new();
            for o in &in_reading {
                let v = o.per_run.get(&s.name).cloned().unwrap_or(Value::Null);
                if !seen.iter().any(|x| unit_value(x) == unit_value(&v)) {
                    seen.push(v);
                }
            }
            // two arms each at one value of it, recorded apart, pool nothing: the arms differ in it, which that note says
            let one_each = !reference.is_empty() && in_reading.iter().all(|o| !o.arm.is_empty()) && [true, false].iter().all(|subj| {
                let mut vs: Vec<String> = in_reading.iter().filter(|o| of_subject(subject, reference, &o.arm) == *subj).map(|o| o.per_run.get(&s.name).map(unit_value).unwrap_or_default()).collect();
                vs.dedup();
                vs.len() == 1
            });
            if seen.len() > 1 && !one_each {
                seen.sort_by(|a, b| match (a.as_f64(), b.as_f64()) {
                    (Some(x), Some(y)) => x.partial_cmp(&y).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Less,
                    (None, Some(_)) => std::cmp::Ordering::Greater,
                    _ => unit_value(a).cmp(&unit_value(b)),
                });
                let mut shown: Vec<String> = seen.iter().map(|v| if v.is_null() { "none".to_string() } else { unit_value(v) }).collect();
                // the repeat form is offered only for what could be a counter: whole numbers, which every arm holds alike
                let counter = counter_values(shown.iter().map(String::as_str)) && (reference.is_empty() || {
                    let held = |subj: bool| -> BTreeSet<String> {
                        in_reading.iter().filter(|o| o.arm.is_empty() || of_subject(subject, reference, &o.arm) == subj).map(|o| o.per_run.get(&s.name).map(unit_value).unwrap_or_default()).collect()
                    };
                    held(true) == held(false)
                });
                let last = shown.pop().unwrap_or_default();
                let repeat = if counter {
                    format!(
                        "{REPEAT_FORM}where {} only numbers repeats, rb spec set {} population.repeat {} --no-verify says so",
                        s.name,
                        exp.id,
                        repeats.iter().cloned().chain(std::iter::once(s.name.clone())).collect::<Vec<_>>().join(",")
                    )
                } else {
                    String::new()
                };
                notes.push(Caveat::note(
                    "pooled_setting",
                    &format!("{}/{}", exp.id, s.name),
                    format!("the runs it reads hold {} {} and {last}, pooled in one reading: a rule with --where {}=<value> reads one{repeat}", s.name, shown.join(", "), s.name),
                ));
            }
        }
        // two arms read from runs of their own that hold no value of a per-run setting in common (500 problems against
        // 250, one grader against another): the comparison mixes that difference in, and says so, as a shared
        // setting's undeclared difference is said; the unit, a design factor and a setting both arms select alike are
        // no such difference, and one the arms select apart is one only where they are different variants too
        if !reference.is_empty() {
            let same_variant = subject == reference;
            for s in exp.settings.iter().filter(|s| s.retracted.is_none() && s.per_run && Some(s.name.as_str()) != unit_setting.as_deref() && s.design_factor != Some(true)) {
                if crit.r#where.contains_key(&s.name) || (same_variant && (crit.subject_where.contains_key(&s.name) || crit.reference_where.contains_key(&s.name))) {
                    continue;
                }
                let values = |subj: bool| -> Vec<String> {
                    let mut vs: Vec<String> = Vec::new();
                    for o in in_reading.iter().filter(|o| !o.arm.is_empty() && of_subject(subject, reference, &o.arm) == subj) {
                        let v = o.per_run.get(&s.name).map(unit_value).unwrap_or_else(|| "none".into());
                        if !vs.contains(&v) {
                            vs.push(v);
                        }
                    }
                    vs
                };
                let (sv, rv) = (values(true), values(false));
                // or a unit whose two arms hold no value in common, though the arms share values across units
                let unit_values = |unit: &str, subj: bool| -> Vec<String> {
                    in_reading
                        .iter()
                        .filter(|o| o.unit == unit && !o.arm.is_empty() && of_subject(subject, reference, &o.arm) == subj)
                        .map(|o| o.per_run.get(&s.name).map(unit_value).unwrap_or_else(|| "none".into()))
                        .collect()
                };
                let within = in_reading.iter().filter(|o| !o.arm.is_empty()).map(|o| o.unit.clone()).collect::<BTreeSet<String>>().into_iter().any(|u| {
                    let (a, b) = (unit_values(&u, true), unit_values(&u, false));
                    !a.is_empty() && !b.is_empty() && !a.iter().any(|v| b.contains(v))
                });
                let apart = !sv.is_empty() && !rv.is_empty() && !sv.iter().any(|v| rv.contains(v));
                if apart || within {
                    let few = |vs: &[String]| assess::first_few(vs, 5, ", ");
                    // a variant compared with itself is named by each arm's selection: wide at loops=8, wide at loops=6
                    let at = |w: &Dict| w.iter().map(|(k, v)| format!("{k}={}", show(v))).collect::<Vec<_>>().join(" and ");
                    let held = if apart && same_variant {
                        format!("the runs of {subject} at {} hold {}, those of {reference} at {} hold {}", at(&crit.subject_where), few(&sv), at(&crit.reference_where), few(&rv))
                    } else if apart {
                        format!("{}'s runs hold {}, {}'s {}", subject, few(&sv), reference, few(&rv))
                    } else {
                        "within a unit, the two arms' runs hold different values".to_string()
                    };
                    notes.push(Caveat::note(
                        "arms_differ",
                        &format!("{}/{}", exp.id, s.name),
                        format!("the arms differ in {}: {held}, so the comparison carries that difference too", s.name),
                    ));
                }
            }
        }
        let mut superseded_early = Vec::new();
        let mut baseline_early: Vec<String> = obs.iter().filter(|o| o.before_declared && !of_subject(subject, reference, &o.arm)).map(|o| o.run.clone()).collect();
        for (r, why_) in &superseded {
            let produced = if super::support::run_basis(r) != "unknown" { by_record(super::support::run_produced(r).unwrap_or(&r.created_at), &r.created_at) } else { r.created_at.clone() };
            if crate::pytime::before(Some(&produced), Some(declared_at)) {
                // a rule's timing is read on its subject's runs: a superseded run of its baseline alone came first as the
                // baseline's runs do
                let (sv, _, diff, _) = self.pair(exp, subject, reference, metric, crit, r, &aliases);
                if diff.is_none() && sv.is_none() {
                    baseline_early.push(r.id.clone());
                } else {
                    superseded_early.push((produced, r.id.clone(), why_.clone()));
                }
            }
        }
        let mut prospective = None;
        // any run of its subject that came before it, counted or not (under an earlier spec, run with a config that
        // disagreed, left out, superseded), is one it could have been fitted to; its baseline's runs coming first is the
        // usual order, a rule written down before the runs it makes a claim about
        let fitted = obs.iter().any(|o| o.before_declared && of_subject(subject, reference, &o.arm) && !not_read(&o.reason) && !o.reason.starts_with("synthetic")) || !superseded_early.is_empty();
        if reading.is_some() && fitted {
            // the units the reading rests on known to have come after it: no run of theirs came before it, or at a time nobody knows
            // (its subject's runs: its baseline's coming first leave a unit after it)
            // an unpaired reading counts each arm's units apart, as the timing's count does: a unit of either arm
            // produced before it is not after it
            let unpaired = reading.as_ref().map_or(false, |r| r.method.starts_with("unpaired"));
            let key = |o: &Observation| if unpaired { format!("{}\u{0}{}", of_subject(subject, reference, &o.arm), o.unit) } else { o.unit.clone() };
            let not_after: HashSet<String> = in_reading
                .iter()
                .filter(|o| (unpaired || of_subject(subject, reference, &o.arm)) && (o.before_declared || o.produced_basis == "unknown"))
                .map(key)
                .collect();
            let after: Vec<Observation> = in_reading.iter().filter(|o| !not_after.contains(&key(o))).cloned().collect();
            if !after.is_empty() {
                prospective = read(&after).0;
            }
        }
        let mut history: Vec<Reading> = Vec::new();
        if !superseded.is_empty() {
            if let Some(cur) = &reading {
                let mut then: Vec<Observation> = counted.clone();
                for (r, _) in &superseded {
                    let (sv, rv, diff, problem) = self.pair(exp, subject, reference, metric, crit, r, &aliases);
                    let value = first_some(diff, sv, rv);
                    if problem.map_or(false, |p| !p.is_empty()) || value.is_none() {
                        continue;
                    }
                    let arm = if diff.is_some() {
                        String::new()
                    } else if sv.is_some() {
                        subject.to_string()
                    } else {
                        reference_observation(subject, reference)
                    };
                    let unit = scoped_unit(r, crit, unit_setting.as_deref(), &unit_of);
                    let mut o = Observation::of(&r.id, value.unwrap(), true, &r.basis);
                    o.unit = in_cell(unit, &r.per_run);
                    o.arm = arm;
                    o.repeats = r.repeats.clone();
                    o.per_run = r.per_run.clone();
                    then.push(o);
                }
                then.retain(|o| !(o.repeats.as_ref().map_or(false, |rep| !rep.is_empty() && superseded.iter().any(|(x, _)| x.id == *rep))));
                let (old, _, _) = read(&then);
                if let Some(mut old) = old {
                    if (old.estimate, old.lo, old.hi, old.n) != (cur.estimate, cur.lo, cur.hi, cur.n) {
                        let why_ = &superseded[0].1;
                        old.superseded = format!("before {} run{} {why_}", superseded.len(), if superseded.len() == 1 { "" } else { "s" });
                        old.at = superseded.iter().map(|(r, _)| r.created_at.clone()).min().unwrap_or_default();
                        history.push(old);
                    }
                }
            }
        }
        for (_sha, group) in &earlier {
            let gobs: Vec<Observation> = group
                .iter()
                .filter_map(|(r, sv, rv, d)| {
                    let value = first_some(*d, *sv, *rv)?;
                    let mut o = Observation::of(&r.id, value, true, &r.basis);
                    o.unit = unit_of.get(&r.id).filter(|u| !u.is_empty()).map(|u| in_cell(u.clone(), &r.per_run)).unwrap_or_else(|| r.id.clone());
                    o.arm = if d.is_some() {
                        String::new()
                    } else if sv.is_some() {
                        subject.to_string()
                    } else {
                        reference_observation(subject, reference)
                    };
                    o.repeats = r.repeats.clone();
                    o.per_run = r.per_run.clone();
                    Some(o)
                })
                .collect();
            let (old, _, _) = read(&gobs);
            if let Some(mut old) = old {
                let first = &group[0].0;
                old.superseded = format!("under an earlier spec: {}", diff_or(&first.settings, &snapshot_now, &arms, &owned));
                old.at = first.created_at.clone();
                history.push(old);
            }
        }
        Ok(RunReading { reading, observations: obs, unit_values, read_units, notes, problems, prospective, history, counted: in_reading, left_out, superseded_early, baseline_early, recorded, pairing })
    }

    /// Runs rb wrote for this experiment whose files are gone: (id, the log's last write of it).
    fn deleted_runs(&self, experiment_id: &str) -> Res<Vec<(String, Dict)>> {
        let prefix = format!("{DIR}/experiments/{experiment_id}/runs/");
        let idx = self.where_()?;
        let writes = self.last_writes()?;
        let mut items: Vec<(&Key, &Dict)> = writes.iter().collect();
        items.sort_by(|a, b| a.0.cmp(b.0));
        let mut out = Vec::new();
        for ((kind, rid), entry) in items {
            if kind != "run" || entry.get_str("op") == Some("adopt_delete") || !entry.get("path").filter(|v| v.truthy()).map(pyjson::py_str).unwrap_or_default().starts_with(&prefix) {
                continue;
            }
            let key = ("run".to_string(), rid.clone());
            if !idx.contains_key(&key) && !self.newer_index.borrow().contains_key(&key) {
                out.push((rid.clone(), entry.clone()));
            }
        }
        Ok(out)
    }

    /// Every time a dataset's contents were looked at: its exposures, and every run that evaluated on it.
    pub fn exposures_of(&self, res: &Resource) -> Res<Vec<Dict>> {
        let mut out: Vec<Dict> = res
            .exposures
            .iter()
            .map(|e| crate::dict! {"how" => e.how.clone(), "at" => e.at.clone(), "by" => e.by.clone(), "why" => e.why.clone(), "run" => Value::Null, "reported" => e.reported})
            .collect();
        for r in self.standing_runs()?.iter() {
            for u in &r.inputs {
                if u.resource == res.id && assess::is_eval_role(&u.role) {
                    out.push(crate::dict! {
                        "how" => "evaluated", "at" => super::support::run_produced(r).unwrap_or(&r.created_at).to_string(), "by" => r.created_by.clone(),
                        "why" => "", "run" => r.id.clone(), "experiment" => r.experiment.clone(), "reported" => false
                    });
                }
            }
        }
        out.sort_by_key(|x| pyjson::py_str(x.get("at").unwrap_or(&Value::Null)));
        Ok(out)
    }

    /// A criterion read on axes.
    /// A criterion's finding, worked out once per state of the record for the criterion as given.
    pub fn finding(&self, crit: &Criterion) -> Res<Finding> {
        let idx = self.where_()?;
        if let Some((i, variants)) = self.finding_cache.borrow().get(&crit.id) {
            if Rc::ptr_eq(i, &idx) {
                if let Some((_, f)) = variants.iter().find(|(c, _)| c.as_ref() == crit) {
                    return Ok((**f).clone());
                }
            }
        }
        let f = self.finding_now(crit)?;
        // the state as it is now: working the finding out may have read the record afresh
        let idx = self.where_()?;
        let mut cache = self.finding_cache.borrow_mut();
        let (state, variants) = cache.entry(crit.id.clone()).or_insert_with(|| (idx.clone(), Vec::new()));
        if !Rc::ptr_eq(state, &idx) {
            *state = idx;
            variants.clear();
        }
        if variants.len() == FINDING_VARIANTS {
            // The normal finding is read first; retain it even for a criterion with many question declarations.
            variants.remove(1);
        }
        variants.push((Rc::new(crit.clone()), Rc::new(f.clone())));
        Ok(f)
    }

    /// A criterion's fixed design cells as its rule judges them, from rb's reading of its runs: each cell's key, its
    /// estimate (None where the cell has no complete comparison), whether it falls outside the rule (a mean rule on the
    /// cell's estimate; an --each rule on each unit's own value, a unit's runs averaged in it, as `judge` reads them),
    /// and the runs in it the reading reads (in a cell with no complete comparison, the ones a paired reading leaves
    /// out). Empty where the criterion reads no design cells.
    pub(crate) fn design_cells(&self, crit: &Criterion) -> Res<Vec<(String, Option<f64>, bool, Vec<String>)>> {
        if crit.retracted.is_some() || !crit.estimand.is_empty() {
            return Ok(Vec::new());
        }
        let exp: Experiment = self.load(&crit.experiment)?;
        let (subject, reference, metric, _) = self.arms(&exp, crit)?;
        let rr = self.run_reading(&exp, crit, &subject, &reference, &metric, &crit.created_at)?;
        let Some(design) = rr.reading.as_ref().map(|r| &r.design).filter(|d| !d.is_empty()) else { return Ok(Vec::new()) };
        let listed: Vec<(String, f64, Vec<String>)> = design
            .get("cells")
            .and_then(Value::as_list)
            .into_iter()
            .flatten()
            .filter_map(|c| Some((c.get_str("key")?.to_string(), c.get("estimate")?.as_f64()?, c.get("runs").and_then(Value::as_list).map(|r| r.iter().map(pyjson::py_str).collect()).unwrap_or_default())))
            .collect();
        let thresholds = THRESHOLDS.contains(&crit.comparator.as_str());
        let missed: Vec<String> = match (crit.over == "each", &rr.unit_values) {
            (false, _) => assess::missed_units(crit, &listed.iter().map(|(k, x, _)| (k.clone(), *x)).collect::<Vec<_>>()),
            (true, Some(values)) => assess::missed_units(crit, values),
            (true, None) => Vec::new(),
        };
        let mut out: Vec<(String, Option<f64>, bool, Vec<String>)> = listed
            .into_iter()
            .map(|(key, x, runs)| {
                // a count and a best judge units, not cells: no cell is said to miss them
                let outside = thresholds && crit.over != "count" && crit.over != "best" && if crit.over == "each" { missed.iter().any(|u| of_cell(u, &key)) } else { missed.contains(&key) };
                (key, Some(x), outside, runs)
            })
            .collect();
        for key in design.get("incomplete").and_then(Value::as_list).into_iter().flatten().map(pyjson::py_str) {
            let mut runs: Vec<String> = Vec::new();
            for o in rr.counted.iter().chain(rr.left_out.iter()).filter(|o| of_cell(&o.unit, &key)) {
                if !runs.contains(&o.run) {
                    runs.push(o.run.clone());
                }
            }
            out.push((key, None, false, runs));
        }
        Ok(out)
    }

    fn finding_now(&self, crit: &Criterion) -> Res<Finding> {
        let exp: Experiment = self.load(&crit.experiment)?;
        let (subject, reference, metric, arm_problem) = self.arms(&exp, crit)?;
        let catalogue = self.catalogue_entry(&metric)?;
        let metric_unit = catalogue.as_ref().map(|m| m.unit.clone()).unwrap_or_default();
        let direction = catalogue.as_ref().map(|m| m.direction.clone()).unwrap_or_else(|| "none".into());
        let declared = Declared {
            by: crit.created_by.clone(),
            at: crit.created_at.clone(),
            words: crit.words.clone(),
            frozen_by: exp.frozen.as_ref().map(|f| f.by.clone()),
            frozen_at: exp.frozen.as_ref().map(|f| f.at.clone()),
        };
        let base = || Finding::base(&crit.id, &crit.experiment, &crit.statement, crit.rule(), &crit.origin, &crit.role, declared.clone());
        if let Some(rt) = &crit.retracted {
            let problem = Caveat::problem("retracted", &crit.id, format!("retracted{}{}", actor::by_writer(&rt.by), crate::view::reason_part(&rt.why, &rt.by, ": ")));
            let mut f = base();
            let says: IndexMap<String, String> = [
                ("headline", "retracted".to_string()),
                ("integrity", problem.text.clone()),
                ("result", "untested".into()),
                ("rule", "retracted".into()),
                ("timing", String::new()),
                ("units", String::new()),
                ("comparison", String::new()),
                ("review", String::new()),
                ("history", String::new()),
            ]
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect();
            f.integrity = vec![problem];
            f.says = says;
            return Ok(f);
        }
        let mut problems: Vec<Caveat> = Vec::new();
        let mut notes: Vec<Caveat> = Vec::new();
        let unread = self.newer()?;
        if !unread.is_empty() {
            problems.push(Caveat::problem("state_newer", &crit.id, format!("{}: upgrade rabbit-brain", newer_said(&unread))));
        }
        if let Some(t) = self.tampered()?.iter().find(|t| t.get_str("kind") == Some("log")) {
            problems.push(Caveat::problem("edited_outside_rb", "log.jsonl", t.get_str("why").unwrap_or("").to_string()));
        }
        let mut checks: Vec<(String, String)> = vec![("criterion".into(), crit.id.clone()), ("experiment".into(), exp.id.clone())];
        checks.extend(self.variant_keys(&exp.id)?);
        if let Some(m) = &catalogue {
            checks.push(("metric".into(), m.id.clone()));
        }
        for (kind, rid) in checks {
            if let Some(why) = self.edited_outside(&kind, &rid)? {
                problems.push(if self.shallow_cut(&kind, &rid)? {
                    Caveat::problem("shallow_clone", &rid, format!("{why}: {UNSHALLOW} fetches the whole history"))
                } else {
                    Caveat::problem("edited_outside_rb", &rid, format!("{why}: rb doctor shows it"))
                });
            }
        }
        for d in self.decisions_outside_of(&crit.id, &exp.id)? {
            let why = self.edited_outside("decision", &d.id)?.map(|w| w).unwrap_or_else(|| "None".into());
            problems.push(if self.shallow_cut("decision", &d.id)? {
                Caveat::problem("shallow_clone", &d.id, format!("{why}: it is not read as anyone's call {}", fetch_whole()))
            } else {
                Caveat::problem("edited_outside_rb", &d.id, format!("{why}: it is not read as anyone's call until restored or adopted"))
            });
        }
        if let Some(src) = &crit.source {
            if src.resolved.is_none() {
                // a cited criterion has its number as its target; one without (a record changed outside rb) is said without it
                let cited = crit.target.map(|t| format!("the cited {}", assess::declared(t))).unwrap_or_else(|| "the cited number".into());
                problems.push(Caveat::problem("cited_unchecked", &crit.id, format!("{cited} rests on {}, which rb did not check", src.label())));
            } else {
                let (state, why) = sources::recheck(src, &crit.cited_value(), &self.root, last_part(&crit.metric), false);
                if state == "stale" || state == "conflict" {
                    let what = if crit.cited_ordering().is_some() { "the cited ordering's source" } else { "the cited number's source" };
                    problems.push(Caveat::problem("cited_source_changed", &crit.id, format!("{what}: {why}")));
                }
            }
        }
        if let Some(rt) = &exp.retracted {
            problems.push(Caveat::problem("experiment_retracted", &exp.id, format!("{} was retracted{}", exp.id, crate::view::reason_part(&rt.why, &rt.by, ": "))));
        }
        if let Some(drift) = self.spec_drift(&exp)? {
            problems.push(Caveat::problem("drift", &exp.id, drift));
        }
        if let Some(p) = arm_problem.as_ref().filter(|p| !p.is_empty()) {
            problems.push(Caveat::problem("arms", &crit.id, p.clone()));
        }
        // writes refuse a unit that separates the arms, but a merge can bring one branch's unit to another's arms
        if let Some(unit) = self.unit_setting(&exp) {
            if let (Some(a), Some(b)) = (crit.subject_where.get(&unit), crit.reference_where.get(&unit)) {
                if !same_value(a, b) {
                    problems.push(Caveat::problem("unit_separates_arms", &crit.id, format!(
                        "population.unit is {unit}, the setting that separates its two arms ({unit}={} against {unit}={}), so each arm is one unit",
                        show(a), show(b))));
                }
            }
        }
        let arms: HashSet<String> = [&subject, &reference].into_iter().filter(|x| !x.is_empty() && exp.variant(x).is_some()).cloned().collect();
        let empty = HashSet::new();
        let rr_ = if crit.estimand.is_empty() { self.run_reading(&exp, crit, &subject, &reference, &metric, &crit.created_at)? } else { RunReading::default() };
        // a file is dated only against the runs this reading counts
        let counted_runs: HashSet<String> = rr_.counted.iter().map(|o| o.run.clone()).collect();
        for c in self.setting_caveats_counted(&exp, self.has_origin(&exp)?, Some(if arms.len() == 2 { &arms } else { &empty }), Some(&arms), crit.estimand.is_empty().then_some(&counted_runs))? {
            // a variant's own setting bears only on the findings that read that variant
            if !arms.is_empty() && variant_setting_of(&exp, &c.subject).map_or(false, |v| !arms.contains(&v)) {
                continue;
            }
            if c.problem {
                problems.push(c);
            } else {
                notes.push(c);
            }
        }
        let mut cited_differs: Vec<Caveat> = notes.iter().filter(|c| c.code == "cited_differs").cloned().collect();
        let mut observations: Vec<Observation> = Vec::new();
        let mut unit_values: Option<Vec<(String, f64)>> = None;
        let mut prospective: Option<Reading> = None;
        let mut history: Vec<Reading> = Vec::new();
        let mut evidence_times: Vec<(Option<String>, String, String)> = Vec::new();
        let mut fitted: Vec<(String, String, String)> = Vec::new();
        let mut exposed_resources: BTreeSet<String> = BTreeSet::new();
        // a per-run setting with a cited value: the runs the reading counts each give their own, and one that differs
        // from the cited source's is said as a shared setting's is
        let counted_ids: HashSet<&String> = rr_.counted.iter().map(|o| &o.run).collect();
        if !counted_ids.is_empty() {
            let runs: Vec<Run> = self.live::<Run>()?.into_iter().filter(|r| counted_ids.contains(&r.id)).collect();
            for (name, st) in exp.all_settings() {
                let Some(c) = st.cited.as_ref().filter(|_| st.per_run && st.retracted.is_none()) else { continue };
                let mut differ: Vec<String> = Vec::new();
                for r in &runs {
                    if let Some(v) = r.per_run.get(&name).filter(|v| !same_value(&c.value, v)) {
                        let shown = show(v);
                        if !differ.contains(&shown) {
                            differ.push(shown);
                        }
                    }
                }
                if !differ.is_empty() {
                    let cav = Caveat::note(
                        "cited_differs",
                        &format!("{}/{name}", exp.id),
                        format!("{name} {} in the runs it reads vs {} in the cited source", differ.join(", "), show(&c.value)),
                    );
                    notes.push(cav.clone());
                    cited_differs.push(cav);
                }
            }
        }
        let mut readings = self.analysis_readings(&exp, &subject, &reference, &metric, &crit.estimand)?;
        let design_declared = crit.estimand.is_empty() && exp.settings.iter().any(|s| s.retracted.is_none() && s.design_factor == Some(true));
        if (crit.scoped() || design_declared) && !readings.is_empty() {
            notes.push(Caveat::note("analysis_scope", &crit.id, "Existing analyses do not declare these per-run selectors or fixed design cells; this criterion reads the scoped runs only."));
            readings.clear();
        }
        // an --each rule is about every unit, and an analysis that reports a difference of means does not say that: the
        // rule keeps reading the runs, and the analysis is said beside it, as a cited figure is
        // so are a count and a best of N; and a relative change and a difference of means are their own estimands,
        // which an analysis's difference is not
        let own_reading = match (crit.over.as_str(), crit.measure.as_str()) {
            ("each", _) => Some("an --each rule reads each unit"),
            ("count", _) => Some("a count rule reads each unit"),
            ("best", _) => Some("a best of N reads each unit"),
            (_, "relative") => Some("a relative change reads each arm's own numbers"),
            (_, "means") => Some("a difference of means reads each arm's own numbers"),
            _ => None,
        };
        if let Some(why) = own_reading.filter(|_| !readings.is_empty()) {
            let replaced: HashSet<String> = self.live::<Analysis>()?.iter().flat_map(|a| a.supersedes.iter().cloned()).collect();
            for (rd, a) in readings.iter().filter(|(_, a)| a.retracted.is_none() && !replaced.contains(&a.id)) {
                notes.push(Caveat::note(
                    "analysis_beside",
                    &a.id,
                    format!("{} reports {}: {}; {why}, so rb reads the runs, with the analysis beside them", a.id, crit.metric, assess::reading_for(crit, rd, &metric_unit, &direction)),
                ));
            }
            readings.clear();
        }
        // a cited difference read against rb's paired reading, which leaves out runs whose unit one arm alone ran: the
        // two are different estimands (with every unit paired, the mean of the differences is the difference of the
        // means), so the cited number is not compared with it, and rb says the difference of the arms' means it reads
        // a cited number, or one a clone brought with its criterion (`--equals` read as reproduced or not)
        let reads_number = crit.origin == "cited" || (crit.origin == "inherited" && crit.comparator == "equals");
        // only a number the reading could miss is compared (an --equals, or a threshold the source states that is not
        // 0): a direction cited by its words compares nothing, and is read as its rule says; and where the two readings
        // give its rule the same outcome, rb says the difference beside it and the rule is judged
        // a 0 the cited words state ("at least 0 points") is a number too; an ordering cited by its words states none
        let stated = |t: f64| crit.source.as_ref().and_then(|src| src.quote.as_deref()).map_or(false, |q| crate::sources::states(q, &Value::Float(t)));
        let numbered = crit.comparator == "equals" || (crate::record::THRESHOLDS.contains(&crit.comparator.as_str()) && crit.target.map_or(false, |t| t != 0.0 || stated(t)));
        // a cited difference of means is read as one (measure: means), and a relative change is no difference: neither is
        // set against rb's paired reading
        if reads_number && readings.is_empty() && numbered && crit.measure.is_empty() {
            if let Some((cav, means)) = paired_against_means(&rr_, crit, &subject, &reference) {
                notes.push(cav.clone());
                let est = rr_.reading.as_ref().and_then(|r| r.estimate);
                // a mean that meets the rule but for float noise (1.885 of 1.89 ± 0.005) meets it
                let meets = |x: f64| [x, x + x.abs() * 1e-9 + 1e-12, x - x.abs() * 1e-9 - 1e-12].iter().any(|y| assess::threshold(crit, *y).0);
                let agree = crit.over != "each" && matches!((est, means), (Some(e), Some(m)) if meets(e) == meets(m));
                // a count and a best of N read units, never a mean: the arms' means are no estimand of theirs, and the
                // units the paired reading leaves out are said in the note above, never a reason to read them otherwise
                if !agree && crit.over != "count" && crit.over != "best" {
                    cited_differs.push(cav);
                }
            }
        }
        // a cited value bears on a rule that cites a number; a rule of the work's own cites nothing to differ from
        if !reads_number {
            notes.retain(|c| c.code != "cited_differs");
        }
        if reads_number && !cited_differs.is_empty() {
            problems.push(Caveat::problem("not_comparable", &crit.id, cited_differs.iter().map(|c| c.text.clone()).collect::<Vec<_>>().join("; ")));
        }
        let mut current: Option<Reading> = None;
        let mut cur_a: Option<Analysis> = None;
        let mut run_units: Option<Vec<Observation>> = None;
        let mut lone_early: Vec<String> = Vec::new();
        let mut baseline_early: Vec<String> = Vec::new();
        let mut read_units: HashSet<String> = HashSet::new();
        let mut unreadable: Vec<String> = Vec::new();
        let mut unread_cut: Vec<String> = Vec::new();
        let mut superseded_by: IndexMap<String, Analysis> = IndexMap::new();
        if !readings.is_empty() {
            for a in self.live::<Analysis>()? {
                for old in &a.supersedes {
                    superseded_by.entry(old.clone()).or_insert_with(|| a.clone());
                }
            }
            let mut usable: Vec<(usize, Analysis)> = Vec::new();
            let mut kept: Vec<Reading> = Vec::new();
            for (rd, a) in readings {
                let mut rd = rd;
                if let Some(edited) = self.edited_outside("analysis", &a.id)? {
                    let cut = self.shallow_cut("analysis", &a.id)?;
                    problems.push(if cut {
                        Caveat::problem("shallow_clone", &a.id, format!("{edited}; it is not read {}", fetch_whole()))
                    } else {
                        Caveat::problem("edited_outside_rb", &a.id, format!("analysis {}: {edited}", a.id))
                    });
                    unreadable.push(a.id.clone());
                    if cut {
                        unread_cut.push(a.id.clone());
                    }
                    let rows = match self.as_written("analysis", &a.id)? {
                        Some(Obj::Analysis(w)) => self.analysis_rows(&w, &subject, &reference, &metric, &crit.estimand)?,
                        _ => Vec::new(),
                    };
                    let Some(first) = rows.into_iter().next() else { continue };
                    rd = first;
                    rd.superseded = if cut {
                        format!("as rb wrote it; this clone is shallow, and it is not read {}", fetch_whole())
                    } else {
                        "as rb wrote it; edited outside rb since, and not read until restored or adopted".into()
                    };
                } else if let Some(rt) = &a.retracted {
                    rd.superseded = format!("retracted{}{}", actor::by_writer(&rt.by), crate::view::reason_part(&rt.why, &rt.by, ": "));
                } else if let Some(by) = superseded_by.get(&a.id) {
                    rd.superseded = format!("superseded by {}{}", by.id, crate::view::reason_part(&by.why, &by.created_by, ": "));
                } else {
                    usable.push((kept.len(), a.clone()));
                }
                kept.push(rd);
            }
            let mut cur_idx: Option<usize> = None;
            if let Some((i, a)) = usable.last().cloned() {
                cur_idx = Some(i);
                for (j, _) in &usable[..usable.len() - 1] {
                    kept[*j].superseded = format!("replaced by {}", a.id);
                }
                cur_a = Some(a);
            }
            for rd in &kept {
                evidence_times.push((if rd.produced_basis != "unknown" { rd.produced_at.clone() } else { None }, rd.source.clone(), rd.produced_basis.clone()));
            }
            for (j, rd) in kept.into_iter().enumerate() {
                if Some(j) == cur_idx {
                    current = Some(rd);
                } else {
                    history.push(rd);
                }
            }
        }
        if !crit.estimand.is_empty() {
            if cur_a.is_none() {
                notes.push(Caveat::note(
                    "estimand",
                    &crit.id,
                    format!("only an analysis reports {metric} ({}); rb's reading of the runs has no estimand", crit.estimand),
                ));
            }
        }
        if let Some(ca) = &cur_a {
            let aliases = self.metric_aliases()?;
            let at = ca.comparisons.iter().position(|c| c.subject == subject && c.reference == reference && c.estimand == crit.estimand && aliases.get(&c.metric).unwrap_or(&c.metric) == &metric);
            let comp = at.map(|i| ca.comparisons[i].clone());
            if ca.basis == "reported" {
                if let Some(comp) = &comp {
                    match &comp.source {
                        None => {
                            // numbers rb's journal watched a command print were not typed in
                            let printed = super::support::analysis_command(ca).map_or(false, |g| g.given == "observed");
                            let how = if printed { "was printed by a command rb's journal watched" } else { "was typed in" };
                            notes.push(Caveat::note("typed", &ca.id, format!("{} {how}: no document rb checked states its numbers", ca.id)))
                        }
                        Some(src) if src.resolved.is_some() => {
                            let nums: Vec<f64> = [Some(comp.estimate), comp.lo, comp.hi].into_iter().flatten().collect();
                            let (state, why) = self.recheck_stated(src, &nums);
                            if state == "stale" {
                                problems.push(Caveat::problem("analysis_source_changed", &ca.id, format!("{}'s numbers: {why}", ca.id)));
                            }
                        }
                        // read from the lines of one section, as the receipt keeps them
                        Some(src) if at.and_then(|i| Self::stated_across_of(ca, i)).is_some() => {
                            let nums: Vec<f64> = [Some(comp.estimate), comp.lo, comp.hi].into_iter().flatten().collect();
                            let (state, why) = self.recheck_across(src, &Self::stated_across_of(ca, at.unwrap()).unwrap_or_default(), &nums);
                            if state == "stale" {
                                problems.push(Caveat::problem("analysis_source_changed", &ca.id, format!("{}'s numbers: {why}", ca.id)));
                            }
                        }
                        _ => {}
                    }
                }
            }
            let mut upstream_runs: Vec<Run> = Vec::new();
            for (k, rid, chain) in self.upstream(ca)? {
                let how = if chain.len() == 2 { format!("{} read {rid}", ca.id) } else { format!("{} read {}, which rests on {rid}", ca.id, chain[1]) };
                if k == "missing" {
                    problems.push(Caveat::problem("edited_outside_rb", &rid, format!("{how}, and {rid} is no longer in the record: rb doctor shows it")));
                } else if k == "run" {
                    let rr: Run = self.load(&rid)?;
                    // a rule's timing is read on its subject's runs: a run the analysis rests on that gives its baseline's
                    // number alone came first as the baseline's runs do
                    let (sv, rv, diff, _) = self.pair(&exp, &subject, &reference, &metric, crit, &rr, &aliases);
                    if !reference.is_empty() && sv.is_none() && diff.is_none() && rv.is_some() {
                        if crate::pytime::before(Some(super::support::run_produced(&rr).unwrap_or(&rr.created_at)), Some(&crit.created_at)) {
                            baseline_early.push(rid.clone());
                        }
                    } else {
                        evidence_times.push((
                        if super::support::run_basis(&rr) != "unknown" { Some(by_record(super::support::run_produced(&rr).unwrap_or(&rr.created_at), &rr.created_at)) } else { None },
                        format!("{rid}, which {} rests on,", ca.id),
                        super::support::run_basis(&rr).to_string(),
                        ));
                    }
                    if rr.retracted.as_ref().map_or(false, |x| x.scope.is_empty()) {
                        problems.push(Caveat::problem("input_retracted", &ca.id, format!("{how}, which was retracted since{}", rr.retracted.as_ref().map(|x| crate::view::reason_part(&x.why, &x.by, ": ")).unwrap_or_default())));
                    } else if self.outside_rb("run", &rid)? {
                        problems.push(self.rests_outside("run", &rid, &how)?);
                    }
                    for u in &rr.inputs {
                        if assess::is_eval_role(&u.role) {
                            exposed_resources.insert(u.resource.clone());
                        }
                    }
                    upstream_runs.push(rr);
                } else if k == "analysis" {
                    let ra: Analysis = self.load(&rid)?;
                    if let Some(rt) = &ra.retracted {
                        problems.push(Caveat::problem("input_retracted", &ca.id, format!("{how}, which was retracted since{}", crate::view::reason_part(&rt.why, &rt.by, ": "))));
                    } else if superseded_by.get(&rid).map_or(false, |s| s.id != ca.id && !chain.contains(&s.id)) {
                        let s = &superseded_by[&rid];
                        problems.push(Caveat::problem("input_superseded", &ca.id, format!("{how}, which {} superseded since{}", s.id, crate::view::reason_part(&s.why, &s.created_by, ": "))));
                    } else if self.outside_rb("analysis", &rid)? {
                        problems.push(self.rests_outside("analysis", &rid, &how)?);
                    }
                    for u in &ra.inputs {
                        if assess::is_eval_role(&u.role) {
                            exposed_resources.insert(u.resource.clone());
                        }
                    }
                } else if k == "resource" && self.kind_of(&rid)? == Some("resource") {
                    let res: Resource = self.load(&rid)?;
                    if let Some(rt) = &res.retracted {
                        problems.push(Caveat::problem("input_retracted", &ca.id, format!("{how}, which was retracted since{}", crate::view::reason_part(&rt.why, &rt.by, ": "))));
                    }
                }
            }
            for u in &ca.inputs {
                if assess::is_eval_role(&u.role) {
                    exposed_resources.insert(u.resource.clone());
                }
            }
            if let Some(mut mine) = rr_.reading.clone() {
                let counted_ids: HashSet<&String> = rr_.counted.iter().map(|o| &o.run).collect();
                let up: HashSet<&String> = upstream_runs.iter().map(|r| &r.id).collect();
                let again = !counted_ids.is_empty() && counted_ids.is_subset(&up);
                mine.superseded = format!("rb's reading of the runs, replaced by {}{}", ca.id, if again { ", which re-reads these runs (no new runs)" } else { "" });
                history.insert(0, mine);
            }
            if let Some(us) = self.unit_setting(&exp) {
                let cn = current.as_ref().and_then(|c| c.n).unwrap_or(0);
                if !upstream_runs.is_empty() && cn != 0 {
                    let empty_map = HashMap::new();
                    let formed: HashSet<String> = upstream_runs.iter().map(|r| unit_for(r, Some(&us), &empty_map)).filter(|u| !u.is_empty()).collect();
                    if !formed.is_empty() && cn > formed.len() as i64 {
                        let cu = current.as_ref().map(|c| c.unit.clone()).filter(|u| !u.is_empty()).unwrap_or_else(|| "unit".into());
                        problems.push(Caveat::problem(
                            "units",
                            &ca.id,
                            format!(
                                "{} counts {cn} {}, and the runs it read form {} {} (population.unit = {us})",
                                ca.id,
                                assess::plural(&cu, cn),
                                formed.len(),
                                assess::plural(&us, formed.len() as i64)
                            ),
                        ));
                    }
                }
            }
        } else {
            current = rr_.reading.clone();
            if let (Some(p), Some(cur)) = (&rr_.pairing, current.as_mut()) {
                let (said, command) = pairing_said(p, &exp, crit, &subject, &reference, &metric_unit, &direction, cur);
                notes.push(Caveat::note("paired_by", &crit.id, said.clone()));
                cur.pairing = crate::dict! {
                    "by" => Value::list_of_str(p.by.clone()), "declared_unit" => p.declared, "said" => said,
                    "unpaired" => p.unpaired.as_ref().map_or(Value::Null, |u| Value::Dict(u.dump(false))), "command" => command.map_or(Value::Null, Value::Str)
                };
            }
            observations = rr_.observations.clone();
            unit_values = rr_.unit_values.clone();
            notes.extend(rr_.notes.iter().cloned());
            problems.extend(rr_.problems.iter().cloned());
            prospective = rr_.prospective.clone();
            history.extend(rr_.history.iter().cloned());
            run_units = Some(rr_.counted.clone());
            read_units = rr_.read_units.iter().cloned().collect();
            unreadable.extend(observations.iter().filter(|o| not_read(&o.reason)).map(|o| o.run.clone()));
            unread_cut.extend(observations.iter().filter(|o| o.reason == SHALLOW).map(|o| o.run.clone()));
            // when a run is known to have existed: when it was produced, or, nobody knowing that, when it was recorded
            // before the declaration
            let known = |o: &Observation| if o.produced_basis != "unknown" { o.produced_at.clone() } else { rr_.recorded.get(&o.run).filter(|_| o.before_declared).cloned() };
            // a run the reading leaves out is evidence it could have been fitted to only when it is known to have come before
            // a rule's timing is read on its subject's runs: its baseline's alone are no evidence it was fitted to
            let of_subj = |o: &&Observation| of_subject(&subject, &reference, &o.arm);
            // (a mixed case, where its subject's runs came first too, still names its baseline's unpaired runs)
            for o in rr_.left_out.iter().filter(|o| o.before_declared) {
                if of_subj(&o) {
                    evidence_times.push((known(o), o.run.clone(), o.produced_basis.clone()));
                }
                lone_early.push(o.run.clone());
            }
            baseline_early = rr_.baseline_early.clone();
            for o in &rr_.counted {
                if of_subj(&o) {
                    evidence_times.push((known(o), o.run.clone(), o.produced_basis.clone()));
                }
                let r: Run = self.load(&o.run)?;
                for u in &r.inputs {
                    if assess::is_eval_role(&u.role) {
                        exposed_resources.insert(u.resource.clone());
                    }
                }
            }
            fitted = observations
                .iter()
                .filter(|o| fitted_to(o) && of_subj(o))
                .map(|o| (known(o).unwrap_or_default(), o.run.clone(), o.reason.clone()))
                .collect();
            fitted.extend(rr_.superseded_early.iter().cloned());
            // the runs the reading rests on, or every counted run while there is no reading
            let read = |o: &&Observation| read_units.is_empty() || read_units.contains(&o.unit);
            // numbers rb's journal watched a command print were not typed in
            let watched = |id: &String| self.load::<Run>(id).ok().and_then(|r| super::support::run_command(&r)).map_or(false, |g| g.given == "observed");
            let mut typed: Vec<String> = rr_.counted.iter().filter(read).filter(|o| o.basis == "typed").map(|o| o.run.clone()).filter(|id| !watched(id)).collect();
            // a run read as both arms' numbers (a relative change, a difference of means) is one run
            let mut seen = HashSet::new();
            typed.retain(|id| seen.insert(id.clone()));
            if !typed.is_empty() {
                notes.push(Caveat::note("typed", &crit.id, format!("typed in, not read from a file or run: {}", crate::assess::first_few(&typed, RUNS_NAMED, ", "))));
            }
            // a file written after its run is no evidence of its own that the run gave those numbers
            let (mut derived, mut undated): (Vec<String>, Vec<String>) = (Vec::new(), Vec::new());
            for o in rr_.counted.iter().filter(read).filter(|o| o.basis == "file") {
                let run: Run = self.load(&o.run)?;
                if super::support::written_after(&run).is_some() {
                    derived.push(o.run.clone());
                } else if super::support::written_when_unknown(&run).is_some() {
                    undated.push(o.run.clone());
                }
            }
            if !undated.is_empty() {
                notes.push(Caveat::note(
                    "file_run_time_unknown",
                    &crit.id,
                    format!("read from files whose runs' times are unknown, so not shown to be the runs' own output: {}", crate::assess::first_few(&undated, RUNS_NAMED, ", ")),
                ));
            }
            if !derived.is_empty() {
                notes.push(Caveat::note("file_written_after", &crit.id, format!("read from files written after their runs, not the runs' own output: {}", crate::assess::first_few(&derived, RUNS_NAMED, ", "))));
            }
            if !rr_.counted.is_empty() {
                let mut all_unchecked = true;
                for o in rr_.counted.iter().filter(read) {
                    let r: Run = self.load(&o.run)?;
                    if let Some(c) = r.receipt.config.as_ref().map(|c| c.given()) {
                        if !c.matches.is_empty() || !c.mismatches.is_empty() {
                            all_unchecked = false;
                            break;
                        }
                    }
                }
                if all_unchecked {
                    notes.push(Caveat::note("config_unchecked", &crit.id, "no run config was compared with the spec (record runs with --config)"));
                }
            }
            // a run whose own config says the code had uncommitted changes when it ran: the rule that reads it says so,
            // in the config's words
            let mut dirty: Vec<String> = Vec::new();
            for o in rr_.counted.iter().filter(read) {
                let r: Run = self.load(&o.run)?;
                if let Some(d) = r.receipt.config.as_ref().and_then(|c| crate::repo::config_dirty_line(&self.root, &c.path, c.sha256.as_deref())) {
                    let said = format!("{}: {d}", r.id);
                    if !dirty.contains(&said) {
                        dirty.push(said);
                    }
                }
            }
            if !dirty.is_empty() {
                notes.push(Caveat::note("uncommitted_code", &crit.id, crate::assess::first_few(&dirty, RUNS_NAMED, "; ")));
            }
        }
        // the axes
        let eq_margin = if crit.comparator == "equivalent" { crit.margin } else { None };
        let result = assess::result_of(current.as_ref(), &direction, !reference.is_empty(), eq_margin);
        let (mut outcome, mut why_rule, margin) = {
            let (o, w, m) = assess::judge(crit, current.as_ref(), &direction, unit_values.as_deref());
            (o.to_string(), w, m)
        };
        if let Some(cur) = current.as_mut().filter(|r| !r.design.is_empty()) {
            // a cell meets a rule on the estimate when its estimate does, and an --each rule when every unit in it does
            let cells: Vec<(String, f64)> = cur.design.get("cells").and_then(Value::as_list).map(|cells| {
                cells.iter().filter_map(|c| Some((c.get_str("key")?.to_string(), c.get("estimate")?.as_f64()?))).collect()
            }).unwrap_or_default();
            let meets = match (crit.over == "each", &unit_values) {
                // a count and a best judge units, not cells: no cell is said to meet them
                _ if crit.over == "count" || crit.over == "best" => None,
                (false, _) => Some(cells.len() - assess::missed_units(crit, &cells).len()),
                (true, Some(values)) => {
                    let missed = assess::missed_units(crit, values);
                    Some(cells.iter().filter(|(cell, _)| !missed.iter().any(|unit| of_cell(unit, cell))).count())
                }
                (true, None) => None,
            };
            if let Some(meets) = meets.filter(|_| THRESHOLDS.contains(&crit.comparator.as_str())) {
                cur.design.insert("meets".into(), Value::Int(meets as i64));
            }
            if !crit.measure.is_empty() {
                outcome = "undetermined".into();
                why_rule = format!(
                    "{} is not read across fixed design cells, which are not independent samples; select one cell (--where) to read its replications",
                    if crit.measure == "relative" { "a relative change" } else { "a difference of means" }
                );
            } else if crit.min_n > 1 {
                outcome = "undetermined".into();
                why_rule = format!("{} fixed design cells cannot satisfy n ≥ {} independent samples; select one cell to read its replications", cur.n.unwrap_or(0), crit.min_n);
            } else if cur.design.get("incomplete").map_or(false, Value::truthy) {
                outcome = "undetermined".into();
                why_rule = "some recorded design cells have no complete comparison; select the intended cells or record their missing arms".into();
            }
        }
        let (unread_shallow, unread_edited): (Vec<String>, Vec<String>) = ordered_unique_str(&unreadable).into_iter().partition(|i| unread_cut.contains(i));
        if current.is_none() && !unreadable.is_empty() {
            outcome = "undetermined".into();
            let mut said: Vec<String> = Vec::new();
            if !unread_edited.is_empty() {
                said.push(format!("its evidence ({}) was edited outside rb: rb reads it again once it is restored or adopted (rb doctor)", crate::assess::first_few(&unread_edited, RUNS_NAMED, ", ")));
            }
            if !unread_shallow.is_empty() {
                said.push(format!("its evidence ({}) is not what rb last wrote, and the clone is shallow: rb cannot see how it got there {}", crate::assess::first_few(&unread_shallow, RUNS_NAMED, ", "), fetch_whole()));
            }
            why_rule = said.join("; ");
        }
        if reads_number && !cited_differs.is_empty() && (outcome == "meets" || outcome == "misses") {
            outcome = "undetermined".into();
            why_rule = format!("not comparable: {}", cited_differs.iter().map(|c| c.text.clone()).collect::<Vec<_>>().join("; "));
        }
        if (outcome == "meets" || outcome == "misses") && margin.is_some() && crit.noise.is_some() && margin.unwrap().abs() < crit.noise.unwrap() {
            notes.push(Caveat::note(
                "borderline",
                &crit.id,
                format!("within the stated noise ({}) of the rule: another unit may land on the other side", assess::declared(crit.noise.unwrap())),
            ));
        }
        if let Some((lo, hi, crossed)) = current.as_ref().filter(|_| outcome == "meets" || outcome == "misses").and_then(|cur| assess::crossing(crit, cur)) {
            notes.push(Caveat::note(
                "straddles",
                &crit.id,
                format!(
                    "the interval [{lo}, {hi}] crosses {}: {} {STRADDLES}",
                    crossed.join(" and "),
                    // a cited number is reproduced or not, a gate met or missed, as the rule axis says them
                    match assess::rule_word(&outcome, &crit.role, &crit.origin).as_str() {
                        "meets" => "met".to_string(),
                        "misses" => "missed".to_string(),
                        word => word.to_string(),
                    }
                ),
            ));
        }
        // timing
        let mut timing = "untested".to_string();
        let mut timing_why = if !unreadable.is_empty() && current.is_none() {
            match (unread_edited.is_empty(), unread_shallow.is_empty()) {
                (false, true) => "its evidence is not read (edited outside rb)".to_string(),
                (true, false) => format!("its evidence is not read ({SHALLOW})"),
                _ => format!(
                    "its evidence is not read ({} edited outside rb: rb doctor; {} whose history this shallow clone lacks: {UNSHALLOW})",
                    unread_edited.join(", "),
                    unread_shallow.join(", ")
                ),
            }
        } else {
            "no evidence recorded yet".to_string()
        };
        if let Some(cur) = &current {
            let known: Vec<(String, String)> = evidence_times.iter().filter_map(|(t, what, _)| t.clone().map(|t| (t, what.clone()))).collect();
            let mut unknown: Vec<String> = evidence_times.iter().filter(|(t, _, _)| t.is_none()).map(|(_, what, _)| what.clone()).collect();
            let mut early: Vec<(String, String)> = known.into_iter().filter(|(t, _)| crate::pytime::before(Some(t), Some(&crit.created_at))).collect();
            early.sort();
            let fitted_early: Vec<(String, String, String)> = if early.is_empty() {
                let mut f = fitted.clone();
                f.sort();
                f
            } else {
                Vec::new()
            };
            let mut seen: Vec<(String, String, Dict)> = Vec::new();
            let run_ids: HashSet<String> = run_units.as_ref().map(|u| u.iter().chain(&rr_.left_out).map(|o| o.run.clone()).collect()).unwrap_or_default();
            for rid in &exposed_resources {
                if self.kind_of(rid)? != Some("resource") {
                    continue;
                }
                let res: Resource = self.load(rid)?;
                for x in self.exposures_of(&res)? {
                    let by = x.get("by").map(pyjson::py_str).unwrap_or_default();
                    match x.get("at").filter(|v| !v.is_null()) {
                        None => unknown.push(format!("{rid}, {}{} at a time nobody recorded,", x.get("how").map(pyjson::py_str).unwrap_or_default(), actor::by_writer(&by))),
                        Some(at) => {
                            let at = pyjson::py_str(at);
                            let run = x.get("run").filter(|v| !v.is_null()).map(pyjson::py_str);
                            if crate::pytime::before(Some(&at), Some(&crit.created_at)) && run.as_ref().map_or(true, |r| !run_ids.contains(r)) {
                                seen.push((at, rid.clone(), x.clone()));
                            }
                        }
                    }
                }
            }
            seen.sort_by(|a, b| a.0.cmp(&b.0));
            if !early.is_empty() {
                timing = "after_evidence".into();
                if let Some(ru) = &run_units {
                    // the units the reading rests on; a run the paired reading leaves out is said as a run
                    // read on its subject's runs: a unit whose baseline's run alone came first is not one of them
                    // an unpaired reading counts each arm's units apart (its n is both arms' units), so it counts the units
                    // produced before the declaration in both arms; a paired one counts a unit by its subject's run
                    let unpaired = cur.method.starts_with("unpaired");
                    let key = |o: &Observation| if unpaired { format!("{}\u{0}{}", of_subject(&subject, &reference, &o.arm), o.unit) } else { o.unit.clone() };
                    let counts = |o: &Observation| unpaired || of_subject(&subject, &reference, &o.arm);
                    let late: BTreeSet<String> = ru.iter().filter(|o| o.before_declared && counts(o)).map(key).collect();
                    let unsure: BTreeSet<String> = ru.iter().filter(|o| o.produced_basis == "unknown" && counts(o)).map(key).filter(|k| !late.contains(k)).collect();
                    let lone = &lone_early;
                    // across fixed design cells the reading's n counts cells, and the units are those within them
                    let cells = cur.design.get("count").and_then(Value::as_i64);
                    let n = if cells.is_some() { read_units.len() as i64 } else { cur.n.filter(|n| *n != 0).unwrap_or(read_units.len() as i64) };
                    // said as the rule's line says its units (assess::judge): "1 of 4 units, in 4 design cells, was ..."
                    let within = cells.map(|cells| format!(" in {cells} design cells")).unwrap_or_default();
                    let which = match (late.len() as i64, n) {
                        (1, 1) => format!("the only unit{within}"),
                        (2, 2) => format!("both units{within}"),
                        (k, n) if k == n => format!("all {n} units{within}"),
                        (k, n) if cells.is_some() => format!("{k} of {n} {},{within},", if n == 1 { "unit" } else { "units" }),
                        (k, n) => format!("{k} of {n} units"),
                    };
                    timing_why = format!("{which} {} produced before it was declared", if late.len() == 1 { "was" } else { "were" });
                    if !unsure.is_empty() {
                        timing_why.push_str(&format!(" (when {} {} produced is unknown)", unsure.len(), if unsure.len() == 1 { "was" } else { "were" }));
                    }
                    if !lone.is_empty() {
                        let (runs, were) = if lone.len() == 1 { ("1 unpaired run".to_string(), "was") } else { (format!("{} unpaired runs", lone.len()), "were") };
                        let ids = assess::first_three(lone, ", ");
                        timing_why = if late.is_empty() {
                            format!("{timing_why}, but {runs} {were} ({ids})")
                        } else {
                            format!("{timing_why}, and so {were} {runs} ({ids})")
                        };
                    }
                } else {
                    timing_why = format!("{} was produced {}, before it was declared", early[0].1, truncate(&early[0].0, 10));
                }
            } else if let Some((t, what, why)) = fitted_early.first() {
                timing = "after_evidence".into();
                let made = if rr_.recorded.contains_key(what) { "recorded" } else { "produced" };
                timing_why = format!("{what} was {made} {}, before it was declared, and is not counted ({why})", truncate(t, 10));
            } else if let Some((at, rid, x)) = seen.first() {
                timing = "after_exposure".into();
                let by = x.get("by").map(pyjson::py_str).unwrap_or_default();
                let how = x.get("how").map(pyjson::py_str).unwrap_or_default();
                timing_why = match x.get("run").filter(|v| v.truthy()) {
                    Some(run) => {
                        let who = actor::shown_writer(&by).map(|name| format!("{name}, ")).unwrap_or_default();
                        format!("{rid} was {how} by {} ({who}{})", pyjson::py_str(run), truncate(at, 10))
                    }
                    None => {
                        let why = x.get("why").filter(|v| v.truthy()).map(pyjson::py_str);
                        format!("{rid} was {how}{} on {}{}", actor::by_writer(&by), truncate(at, 10), why.map(|w| format!(": {w}")).unwrap_or_default())
                    }
                };
            } else if let Some(u0) = unknown.first() {
                timing = "unknown".into();
                timing_why = if !u0.ends_with("nobody recorded,") { format!("when {u0} was produced is unknown") } else { u0.trim_end_matches(',').to_string() };
            } else {
                timing = "before".into();
                let bases: HashSet<&String> = evidence_times.iter().map(|(_, _, b)| b).collect();
                timing_why = if bases.iter().all(|b| b.as_str() == "recorded") {
                    "by record times".into()
                } else if !bases.iter().any(|b| b.as_str() == "recorded") {
                    "by production times".into()
                } else {
                    "by production and record times".into()
                };
                if !baseline_early.is_empty() {
                    timing_why = format!("{timing_why}; {}", came_first(&exp, &reference));
                }
            }
        }
        // units
        let population = exp
            .all_settings()
            .into_iter()
            .filter(|(n, st)| n.contains("population.") && st.retracted.is_none() && !st.value.is_null())
            .map(|(n, st)| format!("{} = {}", n.split_once("population.").map(|(_, t)| t).unwrap_or(""), show(&st.value)))
            .collect::<Vec<_>>()
            .join(" · ");
        let n = current.as_ref().map(|c| c.n.unwrap_or(0)).unwrap_or(0);
        let units = Units {
            n,
            unit: current.as_ref().map(|c| c.unit.clone()).unwrap_or_default(),
            population,
            // the runs the units it rests on came from, never a run the reading leaves out
            runs: run_units.as_ref().map(|u| u.iter().filter(|o| read_units.contains(&o.unit)).map(|o| o.run.as_str()).collect::<HashSet<_>>().len() as i64).unwrap_or(0),
            repeats: run_units
                .as_ref()
                .map(|u| u.iter().filter(|o| read_units.contains(&o.unit) && o.repeats.as_ref().map_or(false, |r| !r.is_empty())).count() as i64)
                .unwrap_or(0),
        };
        if let Some(cur) = &current {
            if n == 1 {
                notes.push(Caveat::note("single_unit", &crit.id, format!("1 {}: nothing yet shows how it varies", if cur.unit.is_empty() { "unit" } else { &cur.unit })));
            }
        }
        // review
        let mut review = None;
        let decs = self.decisions_on(&crit.id)?;
        let mut partial = base();
        partial.comparison = current.clone();
        partial.result = result.to_string();
        partial.rule_outcome = outcome.clone();
        if let Some(d) = decs.last() {
            let then = d.basis.get(&crit.id).cloned();
            let now_basis = assess::basis_of_finding(&partial);
            let current_basis = then.as_ref() == Some(&now_basis);
            review = Some(Review {
                decision: d.id.clone(),
                action: d.action.clone(),
                by: d.by.clone(),
                at: d.at.clone(),
                why: d.why.clone(),
                current: current_basis,
                was: if current_basis {
                    String::new()
                } else {
                    let b = then.filter(|t| t.truthy()).unwrap_or(Value::Dict(Dict::new()));
                    let said = assess::rule_numbers(crit, &assess::basis_reading(&b, &reference), &direction);
                    assess::basis_text(&b, !reference.is_empty(), &assess::basis_rule(&b, Some(crit), &reference), &said)
                },
            });
        }
        let problems = unique(problems);
        let missed_units = match &unit_values {
            Some(v) if outcome == "misses" && (crit.over == "each" || crit.over == "count") => assess::missed_units(crit, v),
            _ => Vec::new(),
        };
        // a reading that pools several values of a per-run setting says so in its headline, and names each value whose
        // runs alone would not meet the rule
        let pooled = if current.as_ref().map_or(false, |c| c.source == "runs") { pooled_values(crit, &notes, &rr_.counted, &subject, &reference, &metric, self.unit_setting(&exp).as_deref(), &direction) } else { Vec::new() };
        let mut notes = notes;
        // never an offer that would fold away a miss rb names: where the rule misses, no note offers a command that changes
        // how its runs are counted (the repeat form, a unit, a pairing's repeat), whichever reading that would give; the
        // facts the notes say stay
        if outcome == "misses" {
            for n in notes.iter_mut() {
                match n.code.as_str() {
                    "pooled_setting" => {
                        if let Some(at) = n.text.find(REPEAT_FORM) {
                            n.text.truncate(at);
                        }
                    }
                    "unit_hint" => {
                        if let Some(at) = n.text.find(": if runs sharing ") {
                            n.text.truncate(at);
                        }
                    }
                    "paired_by" => {
                        if let Some(at) = n.text.find(" is not rb's to say") {
                            n.text.truncate(at + " is not rb's to say".len());
                        }
                    }
                    _ => {}
                }
            }
            if let Some(cur) = current.as_mut() {
                if let Some(said) = notes.iter().find(|n| n.code == "paired_by").map(|n| n.text.clone()) {
                    if cur.pairing.contains_key("said") {
                        cur.pairing.insert("said".into(), Value::Str(said));
                        cur.pairing.insert("command".into(), Value::Null);
                    }
                }
            }
        }
        for (name, _, missed) in &pooled {
            if !missed.is_empty() {
                for n in notes.iter_mut().filter(|n| n.code == "pooled_setting" && n.subject.split_once('/').map_or(false, |(_, x)| x == name)) {
                    if let Some(at) = n.text.find(REPEAT_FORM) {
                        n.text.truncate(at);
                    }
                }
            }
        }
        for (name, values, missed) in &pooled {
            if !missed.is_empty() && outcome == "meets" {
                let at = missed.iter().map(|(v, x)| format!("{name}={v} ({x})")).collect::<Vec<_>>().join(", ");
                let text = if missed.len() == values.len() {
                    format!("the runs at each {name} value alone would not meet the rule ({at}); only pooled do they")
                } else {
                    format!("the runs at {at} alone would not meet the rule; pooled with the other {name} values they do")
                };
                notes.push(Caveat::note("pooled_misses", &crit.id, text));
            }
        }
        let notes = grouped(unique(notes.into_iter().filter(|c| !c.problem).collect()));
        let mut says = self.says(crit, current.as_ref(), result, &outcome, &why_rule, &timing, &timing_why, &declared, &units, &problems, review.as_ref(), &history, &metric_unit, prospective.as_ref(), &direction);
        if let Some(h) = says.get_mut("headline") {
            for (name, values, missed) in &pooled {
                h.push_str(&format!(" · pooled over {} {name} values", values.len()));
                if !missed.is_empty() && outcome == "meets" {
                    let word = assess::rule_label(&assess::rule_word("misses", &crit.role, &crit.origin), 1);
                    h.push_str(&format!(", {word} at {}", missed.iter().map(|(v, _)| format!("{name}={v}")).collect::<Vec<_>>().join(", ")));
                }
            }
        }
        let mut f = base();
        f.comparison = current;
        f.result = result.to_string();
        f.rule_outcome = outcome;
        f.missed_units = missed_units;
        f.timing = timing;
        f.units = units;
        f.integrity = problems;
        f.review = review;
        f.history = history;
        f.prospective = prospective;
        f.observations = observations;
        f.caveats = notes;
        f.says = says;
        f.n = n;
        Ok(f)
    }

    /// One phrase per axis: the words every surface prints for this finding.
    #[allow(clippy::too_many_arguments)]
    fn says(
        &self,
        crit: &Criterion,
        current: Option<&Reading>,
        result: &str,
        outcome: &str,
        why_rule: &str,
        timing: &str,
        timing_why: &str,
        declared: &Declared,
        units: &Units,
        problems: &[Caveat],
        review: Option<&Review>,
        history: &[Reading],
        metric_unit: &str,
        prospective: Option<&Reading>,
        direction: &str,
    ) -> IndexMap<String, String> {
        let by = actor::by_writer(&declared.by);
        let rule_w = assess::rule_said(crit, outcome, current);
        let res_w = assess::result_word(result);
        let res_t = match result {
            "unresolved" => "unresolved: the interval includes 0".to_string(),
            "point_only" => format!("point only: {}", current.map(|c| c.why_no_interval.clone()).unwrap_or_default()).trim_end_matches(|c| c == ':' || c == ' ').to_string(),
            "equivalent" => format!("equivalent within ±{}", crit.margin.map(assess::declared).unwrap_or_else(|| "None".into())),
            "gain" | "loss" => format!(
                "{res_w}: the interval clear of 0 on the {} side ({} is better)",
                if result == "gain" { "better" } else { "worse" },
                if direction == "minimize" { "lower" } else { "higher" }
            ),
            "higher" | "lower" => format!("{res_w}: the catalogue gives {} no direction", last_part(&crit.metric)),
            "measured" => format!(
                "measured: {} on its own, with its interval, so neither better nor worse",
                current.map(|c| c.subject.as_str()).filter(|s| !s.is_empty()).unwrap_or("one arm")
            ),
            _ => res_w.to_string(),
        };
        let mut timing_t = match timing {
            // written down before the runs it makes a claim about, its baseline's having come first
            "before" if timing_why.ends_with(CAME_FIRST) => format!("declared{by} before its runs ({timing_why})"),
            "before" => format!("declared{by} before the evidence ({timing_why})"),
            "after_evidence" => format!("declared{by} after the evidence existed: {timing_why}"),
            "after_exposure" => format!("declared{by} after the data was seen: {timing_why}"),
            "unknown" => format!("declared{by}; order unknown: {timing_why}"),
            _ => format!("declared{by}; {timing_why}"),
        };
        if timing == "after_evidence" {
            if let Some(p) = prospective {
                timing_t.push_str(&format!("; produced after it: {}", assess::reading_for(crit, p, metric_unit, direction)));
            }
        }
        let mut unit_t = String::new();
        if units.n != 0 {
            unit_t = format!("{} {}", units.n, assess::plural(if units.unit.is_empty() { "unit" } else { &units.unit }, units.n));
            // units that are runs come from themselves and their repeats, said next
            if units.runs != 0 && units.runs != units.n && units.unit != "run" {
                unit_t.push_str(&format!(" from {} runs", units.runs));
            }
            if units.repeats != 0 {
                unit_t.push_str(&format!(", {} repeat{}", units.repeats, if units.repeats == 1 { "" } else { "s" }));
            }
        }
        if !units.population.is_empty() {
            unit_t = format!("{}declared: {}", if unit_t.is_empty() { String::new() } else { format!("{unit_t} · ") }, units.population);
        }
        if let Some(cur) = current.filter(|r| !r.design.is_empty()) {
            if let Some(range) = assess::range_said(Some(crit), cur) {
                unit_t.push_str(&format!(" · descriptive range {range}; no sampling interval across fixed cells"));
            }
            if let Some(meets) = cur.design.get("meets") {
                // every cell is the only one, both or all n, as every count rb says
                let said = match meets.as_i64() {
                    Some(k) if k == units.n && k > 0 => format!("{} {}", assess::k_of_n(k as usize, k as usize, "cell", "cells"), if k == 1 { "meets" } else { "meet" }),
                    _ => format!("{} of {} cells meet", show(meets), units.n),
                };
                unit_t.push_str(&format!(" · {said} the threshold"));
            }
        }
        let integ = if problems.is_empty() {
            "ok".to_string()
        } else {
            format!("{}{}", problems[0].text, if problems.len() > 1 { format!(" (and {} more)", problems.len() - 1) } else { String::new() })
        };
        let review_t = match review {
            None => "no decision".to_string(),
            Some(r) => {
                let past = self.load::<Decision>(&r.decision).ok().and_then(|d| d.past);
                let mut t = call_line(&r.action, &r.by, &r.at, &r.why, past.as_ref(), self.confirms(&r.decision).unwrap_or(false));
                if !r.current {
                    t.push_str(&format!("{} ({})", crate::view::earlier(past.is_some()), r.was));
                }
                t
            }
        };
        let mut hist_t = String::new();
        if let Some(h) = history.last() {
            hist_t = format!("was {}", assess::reading_for(crit, h, metric_unit, direction));
            if !h.superseded.is_empty() {
                hist_t.push_str(&format!(", {}", h.superseded));
            }
            if history.len() > 1 {
                hist_t.push_str(&format!("; {} reading{} before that", history.len() - 1, if history.len() == 2 { "" } else { "s" }));
            }
        }
        let mut out = IndexMap::new();
        out.insert("headline".into(), format!("{rule_w} · {res_w}"));
        // the numbers its rule reads, as the rule sentence says them
        let said = current.map(|c| assess::rule_numbers(crit, c, direction)).unwrap_or_default();
        out.insert("comparison".into(), format!("{}: {}", crit.reads(), assess::reading_text_as(current, metric_unit, &said)));
        out.insert("result".into(), res_t);
        out.insert("rule".into(), if outcome != "untested" { format!("{rule_w}: {why_rule}") } else { rule_w.clone() });
        out.insert("timing".into(), timing_t);
        out.insert("units".into(), unit_t);
        out.insert("integrity".into(), integ);
        out.insert("review".into(), review_t);
        out.insert("history".into(), hist_t);
        out
    }

    /// Whether the line a reported comparison was checked against still states its numbers.
    /// The lines an analysis's comparison `idx` was read from where no one line states its numbers (the receipt's
    /// `stated_lines`): each line's number and text as rb read them. None where it was read from one line.
    pub(crate) fn stated_across_of(a: &Analysis, idx: usize) -> Option<Vec<(usize, String)>> {
        let entry = a.receipt.as_ref()?.extra.get("stated_lines")?.as_list()?.iter().find(|e| e.get("comparison").and_then(Value::as_i64) == Some(idx as i64))?;
        let lines = entry.get("lines")?.as_list()?;
        let texts = entry.get("texts")?.as_list()?;
        Some(lines.iter().zip(texts).filter_map(|(l, t)| Some((l.as_i64()? as usize, t.as_str()?.to_string()))).collect())
    }

    /// stated_across_of's lines re-read, as recheck_stated re-reads one: ("ok", "") while they still state the
    /// numbers between them (a line moved is found by its text), else ("stale", why).
    pub(crate) fn recheck_across(&self, src: &Source, read: &[(usize, String)], numbers: &[f64]) -> (String, String) {
        let path = src.path.clone().unwrap_or_else(|| "None".into());
        let data = match sources::read_source(&self.root, &path, src, None) {
            Ok((d, _)) => d,
            Err(u) => return ("stale".into(), format!("{path} cannot be read: {}", u.reason)),
        };
        let lines = sources::split_lines(&String::from_utf8_lossy(&data));
        let (comments, cut) = sources::stated_lines(&path, &lines);
        let mut held: Vec<String> = Vec::new();
        for (no, text) in read {
            // as rb kept it: a line's first 400 characters
            let same = |i: usize| crate::rbyaml::py_strip(&lines[i]).chars().take(400).collect::<String>() == crate::rbyaml::py_strip(text);
            let at = if (1..=lines.len()).contains(no) && same(no - 1) {
                Some(no - 1)
            } else {
                let m: Vec<usize> = (0..lines.len()).filter(|i| same(*i)).collect();
                (m.len() == 1).then(|| m[0])
            };
            match at {
                Some(i) if !comments[i] => held.push(cut[i].clone()),
                _ => {
                    return ("stale".into(), format!("{path}:{no} no longer reads as rb read it: {}", truncate(crate::rbyaml::py_strip(text), 120)));
                }
            }
        }
        let missing: Vec<&f64> = numbers.iter().filter(|x| !held.iter().any(|h| sources::states(h, &Value::Float(**x)))).collect();
        if !missing.is_empty() {
            let at: Vec<String> = read.iter().map(|(n, _)| n.to_string()).collect();
            return ("stale".into(), format!("{path}:{} no longer state {}", at.join(" and :"), missing.iter().map(|x| fmt_float(**x, "g")).collect::<Vec<_>>().join(", ")));
        }
        ("ok".into(), String::new())
    }

    pub(crate) fn recheck_stated(&self, src: &Source, numbers: &[f64]) -> (String, String) {
        let path = src.path.clone().unwrap_or_else(|| "None".into());
        let data = match sources::read_source(&self.root, &path, src, src.resolved.as_ref()) {
            Ok((d, _)) => d,
            Err(u) => return ("stale".into(), format!("{path} cannot be read: {}", u.reason)),
        };
        let lines = sources::split_lines(&String::from_utf8_lossy(&data));
        // the bytes rb read are read as rb read them, an earlier rb's reading of them included; a file changed since is
        // read by its type's comments
        let changed = !src.resolved.as_ref().map_or(false, |r| sources::is_file_of(&data, &r.sha256));
        let (text, kept) = match sources::line_or_moved(src, &lines, src.resolved.as_ref()) {
            Ok((no, t)) if changed && (1..=lines.len()).contains(&no) => {
                let (comments, mut cut) = sources::stated_lines(&path, &lines);
                if comments[no - 1] {
                    return ("stale".into(), format!("{path}:{no} is a comment, and a comment states nothing"));
                }
                // a comment that ends the line states nothing either
                (t, cut.swap_remove(no - 1))
            }
            Ok((_, t)) => (t.clone(), t),
            Err(u) => return ("stale".into(), u.reason),
        };
        let haystack = match src.quote.as_ref().filter(|q| !q.is_empty() && sources::holds_quote(&kept, q)) {
            Some(q) => q.clone(),
            None => kept,
        };
        let missing: Vec<&f64> = numbers.iter().filter(|x| !sources::states(&haystack, &Value::Float(**x))).collect();
        if !missing.is_empty() {
            return (
                "stale".into(),
                format!(
                    "{} no longer states {}; it reads: {}",
                    src.label(),
                    missing.iter().map(|x| fmt_float(**x, "g")).collect::<Vec<_>>().join(", "),
                    truncate(crate::rbyaml::py_strip(&text), 160)
                ),
            );
        }
        ("ok".into(), String::new())
    }
}

fn ordered_unique_str(items: &[String]) -> Vec<String> {
    let mut seen = HashSet::new();
    items.iter().filter(|x| seen.insert((*x).clone())).cloned().collect()
}

fn diff_or(then: &Dict, now_: &Dict, arms: &[String], owned: &[String]) -> String {
    let d = diff_arms(then, now_, arms, owned);
    if d.is_empty() {
        "the variants or declared variables changed".into()
    } else {
        d
    }
}

/// The model of a kind, validated from a stored form; None when it does not validate.
pub fn validate_kind(kind: &str, form: Dict) -> Option<Obj> {
    let v = Value::Dict(form);
    let mut errs = Vec::new();
    let mut loc = Vec::new();
    let o = match kind {
        "project" => Project::validate(&v, &mut loc, &mut errs).map(Obj::Project),
        "question" => Question::validate(&v, &mut loc, &mut errs).map(Obj::Question),
        "experiment" => Experiment::validate(&v, &mut loc, &mut errs).map(Obj::Experiment),
        "metric" => Metric::validate(&v, &mut loc, &mut errs).map(Obj::Metric),
        "criterion" => Criterion::validate(&v, &mut loc, &mut errs).map(Obj::Criterion),
        "run" => Run::validate(&v, &mut loc, &mut errs).map(Obj::Run),
        "analysis" => Analysis::validate(&v, &mut loc, &mut errs).map(Obj::Analysis),
        "resource" => Resource::validate(&v, &mut loc, &mut errs).map(Obj::Resource),
        "decision" => Decision::validate(&v, &mut loc, &mut errs).map(Obj::Decision),
        _ => None,
    };
    if errs.is_empty() {
        o
    } else {
        None
    }
}

/// read_metric with the aliases given.
pub fn read_metric_with(exp: &Experiment, metric: &str, run: &Run, aliases: &IndexMap<String, String>) -> (Option<f64>, Option<String>) {
    let mut cn: IndexMap<String, Vec<(&String, f64)>> = IndexMap::new();
    for (k, v) in &run.metrics {
        cn.entry(canon(k, exp, aliases)).or_default().push((k, *v));
    }
    let get = |key: &str| -> (Option<f64>, Option<String>) {
        let Some(got) = cn.get(key).filter(|g| !g.is_empty()) else { return (None, None) };
        let distinct: HashSet<String> = got.iter().map(|(_, x)| canonical(&Value::Float(*x))).collect();
        if distinct.len() > 1 {
            return (None, Some(format!("{} both read as {key} and disagree", got.iter().map(|(k, _)| k.as_str()).collect::<Vec<_>>().join(" and "))));
        }
        (Some(got[0].1), None)
    };
    let want = canon(metric, exp, aliases);
    let (head, _, rest) = partition(&want, ".");
    if head == "change" && !rest.is_empty() {
        let (v2, _, m2) = partition(rest, ".");
        let mut m2 = m2.to_string();
        let mut cand = if !m2.is_empty() { exp.variant(v2) } else { None };
        let cands = exp.by_role("candidate");
        if cand.is_none() {
            cand = if cands.len() == 1 { Some(cands[0]) } else { None };
            m2 = rest.to_string();
        }
        let base = exp.by_role("baseline");
        if let Some(cand) = cand {
            if base.len() == 1 {
                let (b, pb) = get(&format!("{}.{m2}", base[0].name));
                let (c, pc) = get(&format!("{}.{m2}", cand.name));
                if pb.is_some() || pc.is_some() {
                    return (None, pb.or(pc));
                }
                if let (Some(b), Some(c)) = (b, c) {
                    let computed = minus(c, b);
                    let (given, pg) = get(&want);
                    if pg.is_some() {
                        return (None, pg);
                    }
                    if let Some(g) = given {
                        if !isclose(g, computed, 1e-9, 1e-12) {
                            return (
                                None,
                                Some(format!(
                                    "{want} is given as {}, but {}.{m2} minus {}.{m2} is {}",
                                    fmt_float(g, "g"),
                                    cand.name,
                                    base[0].name,
                                    fmt_float(computed, "g")
                                )),
                            );
                        }
                    }
                    return (Some(computed), None);
                }
            }
        }
        if cand.is_none() && cands.len() > 1 && cn.contains_key(&want) {
            return (
                None,
                Some(format!(
                    "{want} does not say which of {} it is for: name one ({}.{rest} ...)",
                    cands.iter().map(|v| v.name.as_str()).collect::<Vec<_>>().join(", "),
                    cands[0].name
                )),
            );
        }
        return get(&want);
    }
    let (v, p) = get(&want);
    if v.is_some() || p.is_some() {
        return (v, p);
    }
    let live = exp.live_variants();
    if !want.contains('.') || (exp.variant(head).is_none() && !ROLES.contains(&head)) {
        let cands = exp.by_role("candidate");
        if cands.len() == 1 {
            return get(&format!("{}.{want}", cands[0].name));
        }
        // an experiment of one variant keeps a number that names none as that variant's
        if live.len() == 1 {
            return get(&format!("{}.{want}", live[0].name));
        }
    }
    // an experiment of one variant: a number that names no variant is that variant's
    if live.len() == 1 && live[0].name == head && !rest.is_empty() && !run.metrics.keys().any(|k| k.starts_with(&format!("{head}."))) {
        return get(rest);
    }
    (None, None)
}

/// arms() with the aliases given.
pub fn arms_with(exp: &Experiment, crit: &Criterion, aliases: &IndexMap<String, String>) -> (String, String, String, Option<String>) {
    let alias = |m: &str| aliases.get(m).cloned().unwrap_or_else(|| m.to_string());
    let arm = |name: &str| -> (String, Option<String>) {
        if ROLES.contains(&name) {
            let vs = exp.by_role(name);
            if vs.len() > 1 {
                return (
                    name.to_string(),
                    Some(format!("{} has several {name}s ({}): name the one it means", exp.id, vs.iter().map(|v| v.name.as_str()).collect::<Vec<_>>().join(", "))),
                );
            }
            return (vs.first().map(|v| v.name.clone()).unwrap_or_else(|| name.to_string()), None);
        }
        (name.to_string(), None)
    };
    if !crit.subject.is_empty() || !crit.reference.is_empty() {
        let (subject, p1) = arm(if crit.subject.is_empty() { "candidate" } else { &crit.subject });
        let (reference, p2) = if !crit.reference.is_empty() { arm(&crit.reference) } else { (String::new(), None) };
        return (subject, reference, alias(&crit.metric), p1.or(p2));
    }
    let (head, sep, rest) = partition(&crit.metric, ".");
    if head == "change" && !rest.is_empty() {
        let (v2, sep2, m2) = partition(rest, ".");
        let (subject, p1, metric) = if sep2 && exp.variant(v2).is_some() {
            (v2.to_string(), None, m2.to_string())
        } else {
            let (s, p) = arm("candidate");
            (s, p, rest.to_string())
        };
        let (reference, p2) = arm("baseline");
        return (subject, reference, alias(&metric), p1.or(p2));
    }
    if sep && (exp.variant(head).is_some() || ROLES.contains(&head)) {
        let (subject, p1) = arm(head);
        return (subject, String::new(), alias(rest), p1);
    }
    (String::new(), String::new(), alias(&crit.metric), None)
}

/// Whether a criterion reads a number by the name a run gives it, naming no arm: --metric val_loss, or eval.top1 where
/// eval is no variant or role, with no --subject or --reference.
pub fn reads_as_given(exp: &Experiment, crit: &Criterion, aliases: &IndexMap<String, String>) -> bool {
    let (subject, reference, _, _) = arms_with(exp, crit, aliases);
    subject.is_empty() && reference.is_empty()
}

/// An analysis's readings of one comparison.
pub fn analysis_rows(a: &Analysis, subject: &str, reference: &str, metric: &str, estimand: &str, aliases: &IndexMap<String, String>) -> Vec<Reading> {
    let mut rows = Vec::new();
    for c in &a.comparisons {
        let m = aliases.get(&c.metric).cloned().unwrap_or_else(|| c.metric.clone());
        if !(c.subject == subject && c.reference == reference && m == metric && c.estimand == estimand) {
            continue;
        }
        let iv = c.lo.is_some() && c.hi.is_some();
        let truthy_f = |x: Option<f64>| x.filter(|v| *v != 0.0);
        let mut rd = Reading::of(&c.metric);
        rd.subject = c.subject.clone();
        rd.reference = c.reference.clone();
        rd.estimand = c.estimand.clone();
        rd.estimate = Some(c.estimate);
        rd.lo = c.lo;
        rd.hi = c.hi;
        rd.level = if iv { Some(truthy_f(c.level).or(truthy_f(a.level)).unwrap_or(0.95)) } else { None };
        rd.method = a.method.clone();
        rd.unit = if c.unit.is_empty() { a.unit.clone() } else { c.unit.clone() };
        rd.n = if c.n.map_or(false, |n| n != 0) { c.n } else { a.units };
        rd.draws = a.draws;
        rd.source = a.id.clone();
        rd.basis = a.basis.clone();
        let pa = a.produced_at.clone().filter(|p| !p.is_empty());
        rd.produced_at = if a.produced_basis == "unknown" { None } else { Some(by_record(pa.as_deref().unwrap_or(&a.created_at), &a.created_at)) };
        rd.produced_basis = if pa.is_some() || a.produced_basis == "unknown" { a.produced_basis.clone() } else { "recorded".into() };
        rd.at = a.created_at.clone();
        rd.why_no_interval = if iv { String::new() } else { format!("{} reports no interval", a.id) };
        rows.push(rd);
    }
    rows
}

/// A per-run value as units and design cells are told apart by it: a whole number by its value, so seed=2.0 (a REAL
/// column, a float in a file) is the unit seed=2, as recording a run counts it; anything else as it is shown.
pub fn unit_value(v: &Value) -> String {
    match v {
        Value::Float(x) if x.fract() == 0.0 && x.abs() < 9.0e15 => show(&Value::Int(*x as i64)),
        _ => show(v),
    }
}

/// Which independent unit a run belongs to.
pub fn unit_for(r: &Run, unit_setting: Option<&str>, unit_of: &HashMap<String, String>) -> String {
    if let Some(us) = unit_setting {
        let mut u = r.per_run.get(us).filter(|v| !v.is_null()).cloned();
        if u.is_none() {
            // next((x for k, x in per_run.items() if k.split(".")[-1] == unit_setting), None): the first such key, null or not
            u = r.per_run.iter().find(|(k, _)| last_part(k) == us).map(|(_, x)| x.clone()).filter(|x| !x.is_null());
        }
        if u.is_none() {
            if let Some(rep) = &r.repeats {
                return unit_of.get(rep).cloned().unwrap_or_default();
            }
        }
        return match u {
            Some(u) => format!("{us}={}", unit_value(&u)),
            None => String::new(),
        };
    }
    if let Some(rep) = &r.repeats {
        return unit_of.get(rep).cloned().unwrap_or_else(|| rep.clone());
    }
    if !r.per_run.is_empty() {
        let mut kv: Vec<(&String, &Value)> = r.per_run.iter().collect();
        kv.sort_by(|a, b| a.0.cmp(b.0));
        return kv.iter().map(|(k, v)| format!("{k}={}", unit_value(v))).collect::<Vec<_>>().join(" · ");
    }
    r.id.clone()
}

/// The fixed design cell a run's per-run values place it in: 'width=256', or 'width=256 · depth=4'.
fn cell_key(per_run: &Dict, factors: &[String]) -> String {
    factors.iter().map(|k| format!("{k}={}", unit_value(per_run.get(k).unwrap_or(&Value::Null)))).collect::<Vec<_>>().join(" · ")
}

/// A declared unit the design cells share, counted within each cell: seed=2 at width=256 is not seed=2 at width=512
/// ('width=256 · seed=2'), and a unit that is one of the cells' factors is the cell it is in; any other unit as it is.
pub(super) fn unit_within(unit: String, per_run: &Dict, unit_setting: Option<&str>, factors: &[String]) -> String {
    match unit_setting {
        Some(us) if !unit.is_empty() && !factors.is_empty() => {
            if factors.iter().any(|k| k == us) { cell_key(per_run, factors) } else { format!("{} · {unit}", cell_key(per_run, factors)) }
        }
        _ => unit,
    }
}

/// A unit as an `--each` rule across fixed design cells names it: as its observations do, where that names its cell
/// (seed=2 · width=256, or width=256 · seed=2 for a declared unit the cells share), else as the cell it is.
fn unit_in_cell(cell: &str, unit: &str) -> String {
    if of_cell(unit, cell) { unit.to_string() } else { cell.to_string() }
}

/// Whether a unit, as unit_in_cell names it, is one of a cell's.
pub(crate) fn of_cell(unit: &str, cell: &str) -> bool {
    let parts: Vec<&str> = unit.split(" · ").collect();
    cell.split(" · ").all(|part| parts.contains(&part))
}

/// The reading over counted observations, each paired unit with its value, and the units it rests on: the paired
/// ones when any unit pairs (a unit with one arm only is then left out), else every unit. Across fixed design cells
/// the values are every unit's within its cell, and none when a cell's arms never share a unit.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
/// A reading of a criterion's runs: the reading, each unit's value, the units read.
type ReadOut = (Option<Reading>, Option<Vec<(String, f64)>>, Vec<String>);

/// rb's reading across fixed design cells: each cell read by `per_cell` where a rule reads its own estimand (a relative
/// change, a difference of means), else as a difference or a level, and the cells' estimates said descriptively.
#[allow(clippy::too_many_arguments)]
fn design_reading_of(counted: &[Observation], subject: &str, reference: &str, metric: &str, estimand: &str, unit_setting: Option<&str>, factors: &[String], only_design_units: bool, per_cell: Option<&dyn Fn(&[Observation]) -> ReadOut>) -> ReadOut {
    if factors.is_empty() {
        return reading_of(counted, subject, reference, metric, estimand, unit_setting);
    }
    let mut cells: IndexMap<String, Vec<Observation>> = IndexMap::new();
    for o in counted {
        cells.entry(cell_key(&o.per_run, factors)).or_default().push(o.clone());
    }
    // A fixed slice can still contain independent replications. Its sampling interval is within that slice.
    if cells.len() <= 1 {
        let (mut reading, values, units) = reading_of(counted, subject, reference, metric, estimand, unit_setting);
        if let Some(reading) = reading.as_mut().filter(|r| only_design_units && r.n == Some(1) && r.lo.is_none()) {
            reading.unit = "fixed design cell".into();
            reading.why_no_interval = "one fixed design cell, with no independent replications; adding design cells does not supply a sampling interval".into();
        }
        return (reading, values, units);
    }
    let mut estimates = Vec::new();
    let mut each: Option<Vec<(String, f64)>> = Some(Vec::new());
    let mut details = Vec::new();
    let mut read_units = Vec::new();
    let mut missing = Vec::new();
    let mut own_method: Option<String> = None;
    for (key, observations) in cells {
        let (reading, values, units) = match per_cell {
            Some(read) => read(&observations),
            None => reading_of(&observations, subject, reference, metric, estimand, unit_setting),
        };
        let Some(reading) = reading.filter(|r| r.estimate.is_some()) else { missing.push(key); continue; };
        if per_cell.is_some() && own_method.is_none() {
            own_method = Some(reading.method.clone());
        }
        let estimate = reading.estimate.unwrap();
        estimates.push(estimate);
        each = each.zip(values).map(|(mut each, values)| {
            each.extend(values.into_iter().map(|(unit, value)| (unit_in_cell(&key, &unit), value)));
            each
        });
        details.push(crate::obj! {"key" => key, "estimate" => estimate, "n" => reading.n, "unit" => reading.unit,
            "lo" => reading.lo, "hi" => reading.hi, "runs" => Value::list_of_str(observations.iter().filter(|o| units.contains(&o.unit)).map(|o| o.run.clone()))});
        read_units.extend(units);
    }
    if estimates.is_empty() { return (None, None, Vec::new()); }
    let mut reading = Reading::of(metric);
    reading.subject = subject.into();
    reading.reference = reference.into();
    reading.estimand = estimand.into();
    reading.estimate = Some(assess::fmean(&estimates));
    reading.n = Some(estimates.len() as i64);
    reading.unit = "design cell".into();
    // cells read on a rule's own estimand say it first: a relative change (in percent), a difference of means
    reading.method = match own_method.as_deref() {
        Some(m) if m.starts_with("relative change") => "relative change of the means, descriptive mean across fixed design cells".into(),
        Some(m) if m.starts_with("difference of means") => "difference of means, as cited, descriptive mean across fixed design cells".into(),
        _ => "descriptive mean across fixed design cells".into(),
    };
    reading.why_no_interval = "fixed design cells are not independent samples; their range is descriptive, not a sampling interval".into();
    reading.produced_at = counted.iter().filter(|o| read_units.contains(&o.unit)).filter_map(|o| o.produced_at.clone()).min();
    reading.design = crate::dict! {"factors" => Value::list_of_str(factors.iter().cloned()), "cells" => Value::List(details), "count" => estimates.len(),
        "min" => estimates.iter().copied().fold(f64::INFINITY, f64::min), "max" => estimates.iter().copied().fold(f64::NEG_INFINITY, f64::max), "incomplete" => Value::list_of_str(missing)};
    (Some(reading), each, read_units)
}

#[allow(clippy::type_complexity)]
pub fn reading_of(counted: &[Observation], subject: &str, reference: &str, metric: &str, estimand: &str, unit_setting: Option<&str>) -> (Option<Reading>, Option<Vec<(String, f64)>>, Vec<String>) {
    if counted.is_empty() {
        return (None, None, Vec::new());
    }
    let mut case_of: HashMap<String, String> = HashMap::new();
    for o in counted {
        let c = match o.repeats.as_ref().filter(|r| !r.is_empty()) {
            Some(rep) => case_of.get(rep).cloned().unwrap_or_else(|| rep.clone()),
            None => o.run.clone(),
        };
        case_of.insert(o.run.clone(), c);
    }
    let mut cases: IndexMap<String, [IndexMap<String, Vec<f64>>; 3]> = IndexMap::new();
    let mut order: Vec<String> = Vec::new();
    for o in counted {
        if !cases.contains_key(&o.unit) {
            cases.insert(o.unit.clone(), [IndexMap::new(), IndexMap::new(), IndexMap::new()]);
            order.push(o.unit.clone());
        }
        let slot = if o.arm.is_empty() {
            0
        } else if o.arm == subject {
            1
        } else {
            2
        };
        cases.get_mut(&o.unit).unwrap()[slot].entry(case_of[&o.run].clone()).or_default().push(o.value);
    }
    let units: IndexMap<String, [Vec<f64>; 3]> = cases
        .iter()
        .map(|(k, u)| (k.clone(), [0, 1, 2].map(|s| u[s].values().map(|v| assess::fmean(v)).collect::<Vec<f64>>())))
        .collect();
    let (mut paired, mut s_only, mut r_only) = (Vec::new(), Vec::new(), Vec::new());
    let (mut paired_units, mut lone_units) = (Vec::new(), Vec::new());
    for k in &order {
        let u = &units[k];
        if !u[0].is_empty() {
            paired.push(assess::fmean(&u[0]));
            paired_units.push(k.clone());
        } else if !u[1].is_empty() && !u[2].is_empty() {
            paired.push(minus(assess::fmean(&u[1]), assess::fmean(&u[2])));
            paired_units.push(k.clone());
        } else if !u[1].is_empty() {
            s_only.push(assess::fmean(&u[1]));
            lone_units.push(k.clone());
        } else if !u[2].is_empty() {
            r_only.push(assess::fmean(&u[2]));
            lone_units.push(k.clone());
        }
    }
    let simple = |k: &str| k.contains('=') && !k.contains(" · ");
    let keys: HashSet<&str> = order.iter().filter(|k| simple(k)).map(|k| k.split('=').next().unwrap_or("")).collect();
    // a unit named by one per-run setting is that setting's; by several, or by runs with and without one, a unit
    let unit_name = match unit_setting.filter(|u| !u.is_empty()) {
        Some(u) => u.to_string(),
        None if keys.len() == 1 && order.iter().all(|k| simple(k)) => keys.into_iter().next().unwrap().to_string(),
        None if order.iter().all(|k| !k.contains('=')) => "run".into(),
        None => "unit".into(),
    };
    let mut base = Reading::of(metric);
    base.subject = subject.into();
    base.reference = reference.into();
    base.estimand = estimand.into();
    base.unit = unit_name;
    let first = |units: &[String]| counted.iter().filter(|o| units.contains(&o.unit)).filter_map(|o| o.produced_at.clone().filter(|p| !p.is_empty())).min();
    if !paired.is_empty() {
        let (est, lo, hi, why) = assess::mean_interval(&paired, 0.95);
        let mut r = base;
        r.estimate = Some(est);
        r.lo = lo;
        r.hi = hi;
        r.level = if lo.is_some() { Some(0.95) } else { None };
        r.method = format!("{}{}", if !reference.is_empty() { "paired difference" } else { "mean" }, if lo.is_some() { ", t interval" } else { "" });
        r.n = Some(paired.len() as i64);
        r.produced_at = first(&paired_units);
        r.why_no_interval = why;
        return (Some(r), Some(paired_units.iter().cloned().zip(paired).collect()), paired_units);
    }
    if !reference.is_empty() && !s_only.is_empty() && !r_only.is_empty() {
        let (est, lo, hi, why) = assess::welch_interval(&s_only, &r_only, 0.95);
        let mut r = base;
        r.estimate = Some(est);
        r.lo = lo;
        r.hi = hi;
        r.level = if lo.is_some() { Some(0.95) } else { None };
        r.method = format!("unpaired difference{}", if lo.is_some() { ", Welch interval" } else { "" });
        r.n = Some((s_only.len() + r_only.len()) as i64);
        r.produced_at = first(&lone_units);
        r.why_no_interval = why;
        return (Some(r), None, lone_units);
    }
    (None, None, Vec::new())
}

/// A file that postdates a past run: (the file, the variant whose setting it is, the earliest such run, the day it was
/// produced, and the commit the setting cites with its day, when that commit came after the run).
/// (the file, the variant, the run, the day it was produced, and the commit, its day and the line it changed: the
/// commit the setting cites, with no line, or the one that last changed the line read from the working tree, empty
/// where no commit holds it).
pub type Postdates = (String, Option<String>, String, String, Option<(String, String, Option<i64>)>);

/// What a file that postdates a past run says of the settings it states (source_postdates_run): they read as
/// provisional, since no line rb read was there when the run was made.
pub fn postdates_said(names: &[String], path: &str, run: &str, day: &str, at: Option<&(String, String, Option<i64>)>) -> String {
    let names = format!("{} {} provisional", assess::first_few(names, 3, ", "), if names.len() == 1 { "is" } else { "are" });
    match at {
        Some((commit, made, None)) => format!("{names}: {path} at {commit} was committed {made}, after run {run} was produced ({day}): the file postdates the run"),
        Some((commit, _, Some(n))) if commit.is_empty() => format!("{names}: {path}:{n} holds changes no commit has, and run {run} was produced {day}: the line postdates the run"),
        Some((commit, made, Some(n))) => format!("{names}: {path}:{n} was last changed at {commit}, committed {made}, after run {run} was produced ({day}): the line postdates the run"),
        None => format!("{names}: {path} has no commit from before run {run} was produced ({day}): the file postdates the run"),
    }
}

/// The UTC day of a time as given, or its first ten characters where it does not read as one.
fn utc_day(at: &str) -> String {
    crate::pytime::parse_iso(at).map(|t| t.with_timezone(&chrono::Utc).format("%Y-%m-%d").to_string()).unwrap_or_else(|| at.chars().take(10).collect())
}

/// Two times as a postdates line says them: their UTC days, or, on the same UTC day, their UTC times, so "after" reads
/// true on its face.
fn days_apart(later: &str, earlier: &str) -> (String, String) {
    let (a, b) = (utc_day(later), utc_day(earlier));
    if a != b {
        return (a, b);
    }
    let at = |t: &str| crate::pytime::parse_iso(t).map(|t| t.with_timezone(&chrono::Utc).format("%Y-%m-%d %H:%M UTC").to_string()).unwrap_or_else(|| t.to_string());
    (at(later), at(earlier))
}

/// For each per-run setting a reading pools (its `pooled_setting` note), the values its runs hold and each value whose
/// runs alone would not meet the rule, with what they read there: a difference where the rule compares two arms (each
/// run's own difference, or a unit's subject minus its reference, or where no unit holds both, the subject's mean
/// minus the reference's), or the subject's level. A threshold rule judges the mean over that value's units, or for an
/// --each rule each unit; an interval rule its estimate there, on the side its rule asks for.
///
/// A best of N, a relative change and a difference of means are read at each value as their rule reads them: the best
/// unit there, the relative change there (in percent), the difference of the arms' means there. A count's K is of every
/// unit it reads, so no value's units alone are held to it: a count names no value.
#[allow(clippy::too_many_arguments)]
pub fn pooled_values(crit: &Criterion, notes: &[Caveat], counted: &[Observation], subject: &str, reference: &str, metric: &str, unit_setting: Option<&str>, direction: &str) -> Vec<(String, Vec<String>, Vec<(String, String)>)> {
    let mut out = Vec::new();
    let own = crit.over == "best" || !crit.measure.is_empty();
    let pct = if crit.measure == "relative" { "%" } else { "" };
    for n in notes.iter().filter(|n| n.code == "pooled_setting") {
        let Some(name) = n.subject.split_once('/').map(|(_, s)| s.to_string()) else { continue };
        let mut groups: IndexMap<String, Vec<&Observation>> = IndexMap::new();
        for o in counted {
            groups.entry(o.per_run.get(&name).map(unit_value).unwrap_or_else(|| "none".into())).or_default().push(o);
        }
        let mut missed = Vec::new();
        for (v, obs) in &groups {
            // each unit's value there: a run's own difference or level, or a unit's subject minus its reference
            let mut mine: IndexMap<String, Vec<f64>> = IndexMap::new();
            let (mut subj, mut refr): (IndexMap<String, Vec<f64>>, IndexMap<String, Vec<f64>>) = (IndexMap::new(), IndexMap::new());
            for o in obs {
                if reference.is_empty() || o.arm.is_empty() {
                    mine.entry(o.unit.clone()).or_default().push(o.value);
                } else if of_subject(subject, reference, &o.arm) {
                    subj.entry(o.unit.clone()).or_default().push(o.value);
                } else {
                    refr.entry(o.unit.clone()).or_default().push(o.value);
                }
            }
            let mut units: Vec<f64> = mine.values().map(|xs| assess::fmean(xs)).collect();
            let paired: Vec<f64> = subj.iter().filter_map(|(u, xs)| refr.get(u).map(|ys| assess::fmean(xs) - assess::fmean(ys))).collect();
            units.extend(paired.iter().copied());
            if crit.over == "count" {
                continue;
            }
            // the rule's own reading there: the best unit, a relative change, a difference of means
            let reading_there = own.then(|| {
                let group: Vec<Observation> = obs.iter().map(|o| (*o).clone()).collect();
                rule_reading(crit, reading_of(&group, subject, reference, metric, &crit.estimand, unit_setting), &group, subject, reference, metric, unit_setting, false).0
            });
            let estimate = if let Some(r) = &reading_there {
                match r.as_ref().and_then(|r| r.estimate) {
                    Some(e) => e,
                    None => continue,
                }
            } else if !units.is_empty() {
                assess::fmean(&units)
            } else if !subj.is_empty() && !refr.is_empty() {
                let mean = |m: &IndexMap<String, Vec<f64>>| assess::fmean(&m.values().map(|xs| assess::fmean(xs)).collect::<Vec<_>>());
                mean(&subj) - mean(&refr)
            } else {
                continue;
            };
            let sign = match direction {
                "maximize" => 1.0,
                "minimize" => -1.0,
                _ => 0.0,
            };
            // what the runs at that value read: an --each rule's units outside it, with the farthest; else the estimate
            // there and how many units it rests on
            let n = units.len();
            let rests = if n > 0 { format!(", {n} {}", if n == 1 { "unit" } else { "units" }) } else { String::new() };
            let miss = if crate::record::THRESHOLDS.contains(&crit.comparator.as_str()) {
                if crit.over == "each" && !units.is_empty() && !own {
                    let out: Vec<f64> = units.iter().copied().filter(|x| !assess::threshold(crit, *x).0).collect();
                    let side = match crit.comparator.as_str() {
                        "at_least" => "below",
                        "at_most" => "above",
                        _ => "outside",
                    };
                    // the unit farthest from the rule: the lowest below it, the highest above it, the farthest from its number
                    let target = crit.target.unwrap_or(0.0);
                    let dist = |x: f64| match side {
                        "below" => target - x,
                        "above" => x - target,
                        _ => (x - target).abs(),
                    };
                    out.iter().copied().max_by(|a, b| dist(*a).partial_cmp(&dist(*b)).unwrap_or(std::cmp::Ordering::Equal)).map(|worst| {
                        let bound = match (side, crit.tolerance) {
                            ("outside", Some(t)) => format!("{} \u{b1} {}", assess::declared(target), assess::declared(t)),
                            _ => crit.target.map(assess::declared).unwrap_or_default(),
                        };
                        let word = if side == "below" { "lowest" } else if side == "above" { "highest" } else { "farthest" };
                        match (out.len(), n) {
                            (1, 1) => format!("its unit {side} {bound}: {}", assess::g(worst)),
                            (k, n) if k == n => format!("all {n} units {side} {bound}, {word} {}", assess::g(worst)),
                            (1, n) => format!("1 of {n} units {side} {bound}: {}", assess::g(worst)),
                            (k, n) => format!("{k} of {n} units {side} {bound}, {word} {}", assess::g(worst)),
                        }
                    })
                } else {
                    let best = if crit.over == "best" { "best unit " } else { "" };
                    (!assess::threshold(crit, estimate).0).then(|| format!("{best}{}{pct}{rests}", assess::g(estimate)))
                }
            } else {
                // an interval rule, judged on its estimate there: on the better side, inside its margin
                let margin = crit.margin.unwrap_or(0.0);
                let off = match crit.comparator.as_str() {
                    "better" => sign != 0.0 && estimate * sign <= 0.0,
                    "noninferior" => sign != 0.0 && estimate * sign < -margin,
                    "equivalent" => estimate.abs() > margin,
                    _ => false,
                };
                off.then(|| format!("{}{pct}{rests}", assess::g(estimate)))
            };
            // a value says a reading of its own only where it holds one: the rule's least number of units, two at the
            // least, never one unit (a repeat's number takes another value in every unit)
            let held = if n > 0 { n } else { subj.len().min(refr.len()) };
            if held < (crit.min_n.max(2) as usize) {
                continue;
            }
            if let Some(x) = miss {
                missed.push((v.clone(), x));
            }
        }
        out.push((name, groups.keys().cloned().collect(), missed));
    }
    out
}

/// What a paired reading of two arms recorded apart says of its pairs, with the same runs read unpaired beside it and,
/// where no unit is declared, the command that reads each run as its own unit: (the words, the command).
#[allow(clippy::too_many_arguments)]
fn pairing_said(p: &Pairing, exp: &Experiment, crit: &Criterion, subject: &str, reference: &str, metric_unit: &str, direction: &str, cur: &Reading) -> (String, Option<String>) {
    // the command keeps the repeats already declared: it adds to them, never puts another in their place
    let declared: Vec<String> = exp
        .setting("population.repeat")
        .filter(|s| s.retracted.is_none())
        .map(|s| match &s.value {
            Value::Str(v) => v.split(',').map(|x| crate::rbyaml::py_strip(x).to_string()).filter(|x| !x.is_empty()).collect(),
            Value::List(l) => l.iter().filter_map(|x| x.as_str().map(str::to_string)).collect(),
            _ => Vec::new(),
        })
        .unwrap_or_default();
    let with = |more: &[String]| -> String {
        let mut all = declared.clone();
        all.extend(more.iter().filter(|m| !declared.contains(m)).cloned());
        all.join(",")
    };
    let at = |w: &Dict| w.iter().map(|(k, v)| format!("{k}={}", show(v))).collect::<Vec<_>>().join(" and ");
    let (sname, rname) = if subject == reference {
        (format!("{subject} at {}", at(&crit.subject_where)), format!("{reference} at {}", at(&crit.reference_where)))
    } else {
        (subject.to_string(), reference.to_string())
    };
    let by = match p.by.len() {
        1 => p.by[0].clone(),
        _ => {
            let (last, rest) = p.by.split_last().unwrap();
            format!("{} and {last}", rest.join(", "))
        }
    };
    let pairs = if p.by.len() == 1 {
        let k = &p.by[0];
        format!("each {k}'s {sname} run against the same {k}'s {rname} run")
    } else {
        format!("each {sname} run against the {rname} run with the same {by}")
    };
    let head = if p.declared { format!("paired by {by}, the declared unit: {pairs}") } else { format!("paired by {by}: {pairs}") };
    // the same reading twice says nothing: an unpaired reading that equals the paired one, estimate and interval, is
    // left out; one that reads runs the paired reading leaves out says so
    let runs = if p.all_runs { "all the arms' runs, those it leaves out too," } else { "the same runs" };
    let unpaired = match &p.unpaired {
        Some(u) if u.estimate.is_some() && [(u.estimate, cur.estimate), (u.lo, cur.lo), (u.hi, cur.hi)].iter().all(|(a, b)| match (a, b) {
            (Some(a), Some(b)) => (a - b).abs() <= assess::close(*a, *b),
            (None, None) => true,
            _ => false,
        }) => String::new(),
        Some(u) if u.estimate.is_some() => format!("unpaired, {runs} read {}", assess::reading_for(crit, u, metric_unit, direction)),
        _ if p.diff_only => String::new(),
        _ => format!("unpaired, {runs} give no reading: each arm needs a unit of its own"),
    };
    let (tail, command) = if p.declared {
        // the declared unit is the declaration that pairs them: changing it is the way to read them otherwise
        (String::new(), None)
    } else {
        // the repeat form only for a setting that could be a counter (whole numbers); a condition is never offered
        let share = if p.by.len() == 1 {
            let a = if by.starts_with(['a', 'e', 'i', 'o', 'u']) { "an" } else { "a" };
            format!("whether runs that share {a} {by} are linked is not rb's to say")
        } else {
            format!("whether runs that share their {by} are linked is not rb's to say")
        };
        let all = p.counters.len() == p.by.len();
        // never an offer that would read away the miss rb names: where the paired reading misses its rule and the
        // unpaired one would not, the repeat form is not offered
        let misses = |r: &Reading| assess::judge(crit, Some(r), direction, None).0 == "misses";
        let away = misses(cur) && p.unpaired.as_ref().map_or(false, |u| !misses(u));
        match p.counters.as_slice() {
            [] => (share, None),
            _ if away => (format!("{share}; rb offers no repeat form here: read that way, the rule's miss would no longer show"), None),
            [c] => {
                let cmd = format!("rb spec set {} population.repeat {} --no-verify", exp.id, with(&p.counters));
                let does = if all { "reads each run as its own unit" } else { "leaves it out of what pairs them" };
                (format!("{share}. The repeat form is for a counter only (a seed, an epoch, a repeat's number), never a condition: where {c} only numbers repeats, {cmd} {does}"), Some(cmd))
            }
            cs => {
                // of several, rb cannot tell which numbers repeats: the one that does leaves what pairs them, and all of
                // them, where they are all that pairs them, read each run as its own unit
                let (last, rest) = cs.split_last().unwrap();
                let named = format!("{} or {last}", rest.join(", "));
                let one = with(&["<that setting>".to_string()]);
                let cmd = format!("rb spec set {} population.repeat {} --no-verify", exp.id, with(cs));
                let each = if all { format!(", and {cmd} reads each run as its own unit") } else { String::new() };
                (
                    format!(
                        "{share}. The repeat form is for a counter only (a seed, an epoch, a repeat's number), never a condition: where {named} only numbers repeats, rb spec set {} population.repeat {one} --no-verify leaves it out of what pairs them{each}",
                        exp.id
                    ),
                    Some(cmd),
                )
            }
        }
    };
    let parts: Vec<&str> = [head.as_str(), unpaired.as_str(), tail.as_str()].into_iter().filter(|t| !t.is_empty()).collect();
    (parts.join("; "), command)
}

/// Where rb's paired reading of two arms leaves out runs whose unit one arm alone ran, what rb says to a cited
/// difference: the units it paired, the runs it left out, and, where the arms are recorded in runs of their own and read
/// outside design cells, the difference of the arms' means over all their units (a unit's cases averaged first, a case
/// being a run with its repeats), which a cited difference of means gives. None where every unit pairs.
fn paired_against_means(rr: &RunReading, crit: &Criterion, subject: &str, reference: &str) -> Option<(Caveat, Option<f64>)> {
    let rd = rr.reading.as_ref()?;
    if subject.is_empty() || reference.is_empty() {
        return None;
    }
    let lone: Vec<&Observation> = rr.left_out.iter().filter(|o| !o.arm.is_empty() && o.reason.starts_with("its unit has no ")).collect();
    if lone.is_empty() {
        return None;
    }
    let left: Vec<String> = lone.iter().map(|o| o.run.clone()).collect();
    let left = assess::first_few(&left, 4, ", ");
    let units = assess::plural(if rd.unit.is_empty() { "unit" } else { rd.unit.as_str() }, 2);
    let note = |text: String| Some((Caveat::note("cited_differs", subject, text), None));
    let all: Vec<&Observation> = rr.counted.iter().chain(lone.iter().copied()).collect();
    // across design cells, or where some units give the difference in one run, rb has no means of its own to set beside it
    if !rd.design.is_empty() || all.iter().any(|o| o.arm.is_empty()) {
        let within = if rd.design.is_empty() { "" } else { " within each design cell" };
        return note(format!(
            "rb's paired reading reads only the units both arms ran{within} and leaves out {left}, so it is not a difference of the arms' means over all their {units}"
        ));
    }
    // each arm's mean over its units: a case is a run with its repeats, a unit's cases averaged, then the units
    let arm_mean = |subj: bool| -> Option<(f64, usize)> {
        let mut by_unit: IndexMap<&str, IndexMap<&str, Vec<f64>>> = IndexMap::new();
        for o in all.iter().filter(|o| of_subject(subject, reference, &o.arm) == subj) {
            by_unit.entry(o.unit.as_str()).or_default().entry(o.repeats.as_deref().unwrap_or(o.run.as_str())).or_default().push(o.value);
        }
        let mean = |v: &[f64]| v.iter().sum::<f64>() / v.len() as f64;
        let means: Vec<f64> = by_unit.values().map(|cases| mean(&cases.values().map(|c| mean(c)).collect::<Vec<_>>())).collect();
        (!means.is_empty()).then(|| (mean(&means), means.len()))
    };
    let ((sm, sn), (rm, rn)) = (arm_mean(true)?, arm_mean(false)?);
    // a variant compared with itself is named by each arm's selection
    let at = |w: &Dict| w.iter().map(|(k, v)| format!("{k}={}", show(v))).collect::<Vec<_>>().join(" and ");
    let (sname, rname) = if subject == reference {
        (format!("{subject} at {}", at(&crit.subject_where)), format!("{reference} at {}", at(&crit.reference_where)))
    } else {
        (subject.to_string(), reference.to_string())
    };
    let paired = rr.read_units.len();
    // the difference in the digits the rule needs to say which side of its edges it falls (+1.8848 beside 1.89 ± 0.005,
    // never +1.885, which reads as on the edge)
    let mut means = Reading::of("");
    means.reference = reference.to_string();
    means.estimate = Some(sm - rm);
    let said = assess::rule_numbers(crit, &means, "")[0].clone().unwrap_or_else(|| assess::n4(Some(sm - rm), true));
    let text = format!(
        "rb's paired reading reads only the {paired} {} both arms ran and leaves out {left}, so it is not a difference of the arms' means over all their {units} ({sname} over {sn}, {rname} over {rn}), which is {} here",
        assess::plural(if rd.unit.is_empty() { "unit" } else { rd.unit.as_str() }, paired as i64),
        said
    );
    Some((Caveat::note("cited_differs", subject, text), Some(sm - rm)))
}


/// A unit as a sentence names it: "seed 3", "width 512, seed 2".
pub fn unit_named(unit: &str) -> String {
    unit.split(" · ").map(|p| p.replacen('=', " ", 1)).collect::<Vec<_>>().join(", ")
}

/// The unit best in a threshold's direction (the highest for --at-least, the lowest for --at-most) and its value.
pub fn best_unit<'a>(c: &Criterion, values: &'a [(String, f64)]) -> Option<&'a (String, f64)> {
    let high = c.comparator == "at_least";
    values.iter().filter(|(_, v)| v.is_finite()).fold(None, |best: Option<&(String, f64)>, x| match best {
        Some(b) if (high && b.1 >= x.1) || (!high && b.1 <= x.1) => Some(b),
        _ => Some(x),
    })
}

/// A run reading as format 2's rules read it (`over: best`, `measure: relative`, `measure: means`); any other rule's
/// as rb reads it. A best of N is the best unit's own value, never a mean, and has no interval. A relative change is
/// the difference of the arms' means over the reference's mean, in percent, with a delta-method t interval from 3
/// units (paired where the units pair). A difference of means is each arm's mean over all its units, with Welch's
/// interval, the estimand a cited difference of means states.
pub fn rule_reading(
    c: &Criterion,
    out: (Option<Reading>, Option<Vec<(String, f64)>>, Vec<String>),
    counted: &[Observation],
    subject: &str,
    reference: &str,
    metric: &str,
    unit_setting: Option<&str>,
    design: bool,
) -> (Option<Reading>, Option<Vec<(String, f64)>>, Vec<String>) {
    if c.over == "best" {
        let (mut reading, values, units) = out;
        if let (Some(r), Some((label, v))) = (reading.as_mut(), values.as_deref().and_then(|vs| best_unit(c, vs)).cloned()) {
            let n = values.as_ref().map_or(0, |v| v.len()) as i64;
            r.estimate = Some(v);
            (r.lo, r.hi, r.level) = (None, None, None);
            let _ = n;
            r.method = format!("best unit {}", unit_named(&label));
            r.why_no_interval = "a best of N is one unit's own value, with no interval".into();
        }
        return (reading, values, units);
    }
    // across fixed design cells (declared, and not selected away) the cells' own reading stands, with its guards: no
    // relative change or difference of means is read across them, and the rule says so (undetermined)
    if (c.measure != "relative" && c.measure != "means") || design {
        return out;
    }
    let base = out.0.clone();

    // each unit's mean of each arm: a case is a run with its repeats, a unit's cases averaged
    let mut case_of: HashMap<String, String> = HashMap::new();
    for o in counted {
        let k = o.repeats.as_ref().filter(|r| !r.is_empty()).map(|rep| case_of.get(rep).cloned().unwrap_or_else(|| rep.clone())).unwrap_or_else(|| o.run.clone());
        case_of.insert(o.run.clone(), k);
    }
    let mut arms: IndexMap<String, [IndexMap<String, Vec<f64>>; 2]> = IndexMap::new();
    let mut diff_only = false;
    for o in counted {
        if o.arm.is_empty() {
            diff_only = true;
            continue;
        }
        let slot = if o.arm == subject { 0 } else { 1 };
        arms.entry(o.unit.clone()).or_insert_with(|| [IndexMap::new(), IndexMap::new()])[slot].entry(case_of[&o.run].clone()).or_default().push(o.value);
    }
    let mean_of = |cases: &IndexMap<String, Vec<f64>>| (!cases.is_empty()).then(|| assess::fmean(&cases.values().map(|v| assess::fmean(v)).collect::<Vec<_>>()));
    let mut r = base.clone().unwrap_or_else(|| {
        let mut r = Reading::of(metric);
        (r.subject, r.reference) = (subject.to_string(), reference.to_string());
        r.unit = unit_setting.unwrap_or("unit").to_string();
        r
    });
    (r.lo, r.hi, r.level, r.estimate) = (None, None, None, None);
    // a run that gives only the difference holds neither arm's own number, which both readings need
    if diff_only {
        return (None, None, Vec::new());
    }
    let (s_units, r_units): (Vec<(String, f64)>, Vec<(String, f64)>) = (
        arms.iter().filter_map(|(u, a)| mean_of(&a[0]).map(|m| (u.clone(), m))).collect(),
        arms.iter().filter_map(|(u, a)| mean_of(&a[1]).map(|m| (u.clone(), m))).collect(),
    );
    if s_units.is_empty() || r_units.is_empty() {
        return (None, None, Vec::new());
    }
    let read_units: Vec<String> = arms.keys().cloned().collect();
    let xs: Vec<f64> = s_units.iter().map(|(_, v)| *v).collect();
    let ys: Vec<f64> = r_units.iter().map(|(_, v)| *v).collect();
    if c.measure == "means" {
        let (est, lo, hi, why) = assess::welch_interval(&xs, &ys, 0.95);
        r.estimate = Some(est);
        (r.lo, r.hi) = (lo, hi);
        r.level = lo.map(|_| 0.95);
        r.method = format!("difference of means, as cited{}", if lo.is_some() { ", Welch interval" } else { "" });
        // its units are the units the arms were read on, each counted once
        r.n = Some(read_units.len() as i64);
        r.why_no_interval = why;
        return (Some(r), None, read_units);
    }
    // a relative change: paired where every unit holds both arms, else each arm's units apart
    let paired: Vec<(f64, f64)> = s_units.iter().filter_map(|(u, x)| r_units.iter().find(|(v, _)| v == u).map(|(_, y)| (*x, *y))).collect();
    let all_pair = paired.len() == s_units.len() && paired.len() == r_units.len();
    let (sm, rm) = if all_pair {
        (assess::fmean(&paired.iter().map(|p| p.0).collect::<Vec<_>>()), assess::fmean(&paired.iter().map(|p| p.1).collect::<Vec<_>>()))
    } else {
        (assess::fmean(&xs), assess::fmean(&ys))
    };
    if rm == 0.0 || !rm.is_finite() {
        return (None, None, Vec::new());
    }
    let q = sm / rm;
    r.estimate = Some((q - 1.0) * 100.0);
    let n = if all_pair { paired.len() } else { xs.len().min(ys.len()) };
    let (var_q, df) = if all_pair {
        // the ratio estimator's variance from each unit's residual, subject - q x reference: centred, so arms that move
        // together lose no variance to cancellation
        let k = paired.len() as f64;
        let residuals: Vec<f64> = paired.iter().map(|(x, y)| x - q * y).collect();
        let var = if paired.len() > 1 { assess::variance(&residuals) } else { 0.0 };
        (var / (k * rm * rm), k - 1.0)
    } else {
        (assess::variance(&xs) / xs.len() as f64 / (rm * rm) + q * q * assess::variance(&ys) / ys.len() as f64 / (rm * rm), (xs.len().min(ys.len()) as f64) - 1.0)
    };
    let how = if all_pair { "relative change of the means, paired" } else { "relative change of the means, unpaired" };
    if n >= assess::MIN_UNITS_FOR_INTERVAL && var_q.is_finite() && var_q >= 0.0 {
        let half = if var_q == 0.0 { 0.0 } else { assess::t_ppf(0.975, df) * var_q.sqrt() * 100.0 };
        let e = r.estimate.unwrap();
        (r.lo, r.hi, r.level) = (Some(e - half), Some(e + half), Some(0.95));
        r.method = format!("{how}, delta-method t interval");
        r.why_no_interval = String::new();
    } else {
        r.method = how.to_string();
        r.why_no_interval = format!("{n} unit{}: rb gives an interval from {}", if n == 1 { "" } else { "s" }, assess::MIN_UNITS_FOR_INTERVAL);
    }
    r.n = Some(read_units.len() as i64);
    (Some(r), None, read_units)
}
