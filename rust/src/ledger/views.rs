//! Views over the whole record: what a run would need, the experiment's own table, states, the question tree, and
//! `status`, the record read the way anyone starts their day.

use super::helpers::*;
use super::*;
use crate::assess;
use std::collections::BTreeSet;

/// How work the record began after is recorded: the start protocol, the next step for it on every surface.
pub(crate) const START: &str = "rb docs --start   (how to record the work already done)";
/// Of many results folders, how many an item's words name: the rest it counts, and --json lists every one.
pub(crate) const SHOWN_FOLDERS: usize = 3;

/// One open item of `status`: who usually acts, a code, the subject, what is open, and the exact command.
pub(crate) fn item(items: &mut Vec<Dict>, who: &str, code: &str, subject: &str, what: &str, do_: &str, extra: Vec<(&str, Value)>) {
    let mut d = crate::dict! {"who" => who, "code" => code, "subject" => subject, "what" => what, "do" => do_};
    for (k, v) in extra {
        if v.truthy() {
            d.insert(k.into(), v);
        }
    }
    items.push(d);
}

/// Where a run was read from a row, the row as a key: its database and table, and its rowid, or the key it was read by.
pub fn row_key(r: &Run) -> Option<(String, String)> {
    let row = r.receipt.row.as_ref()?;
    let id = match row.rowid {
        Some(n) => format!("rowid {n}"),
        None => row.r#where.iter().map(|(k, v)| format!("{k}={}", show(v))).collect::<Vec<_>>().join(", "),
    };
    Some((format!("{}/{}/{id}", row.path, row.table), format!("{}, table {}, {id}", row.path, row.table)))
}

/// The rows read as runs in more than one experiment: where each is, and its runs, oldest first.
pub fn rows_in_several(runs: &[Run]) -> Vec<(String, Vec<Run>)> {
    let mut by: IndexMap<String, (String, Vec<Run>)> = IndexMap::new();
    for r in runs {
        if let Some((key, place)) = row_key(r) {
            by.entry(key).or_insert_with(|| (place, Vec::new())).1.push(r.clone());
        }
    }
    by.into_values()
        .filter(|(_, rs)| rs.iter().any(|r| r.experiment != rs[0].experiment))
        .map(|(place, mut rs)| {
            rs.sort_by(|x, y| x.created_at.cmp(&y.created_at));
            (place, rs)
        })
        .collect()
}

fn truncate(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// A call that waits for a confirmation, as rb status's item says it: the call in rb's words over the record's own
/// (the action and subject, when a past call was made, and the reason as given), with why a document given for a past
/// call does not make it a call. It names no writer.
pub fn pending_said(d: &Decision, a: &Authority) -> String {
    match &d.past {
        // a call a session proposes says its reason as the session's note on it
        None => format!("{} {}, proposed, not made{}", d.action, d.subject, session_note(&d.why)),
        // the document's words are what it rests on; the reason given is a note on it
        Some(p) => match &p.source {
            Some(src) => format!("{} {} ({}), a past call mentioned, not confirmed: {}{} · {}", d.action, d.subject, past_when(p), source_said(src), session_note(&d.why), a.basis),
            None => format!("{} {} ({}), a past call mentioned, not confirmed{}", d.action, d.subject, past_when(p), session_note(&d.why)),
        }
    }
}

/// Notes as rb status gives them: each as recorded, with the words given with it (`words`, from its write's log line)
/// when there were any.
fn notes_value(notes: &[Note], said: &HashMap<String, String>) -> Value {
    Value::List(
        notes
            .iter()
            .map(|n| {
                let mut d = n.dump(false);
                if let Some(w) = n.id.as_ref().and_then(|id| said.get(id)) {
                    d.insert("words".into(), Value::Str(w.clone()));
                }
                Value::Dict(d)
            })
            .collect(),
    )
}

impl Ledger {
    /// The words given with each note, by note id: a note's file keeps its text, its log line the words.
    pub fn note_words(&self) -> Res<HashMap<String, String>> {
        let mut out = HashMap::new();
        for row in self.log_entries()?.iter() {
            let Some(w) = row.get_str("words").filter(|w| !w.is_empty() && row.get_str("op") == Some("note")) else { continue };
            for part in row.get("parts").and_then(Value::as_list).map(|l| l.as_slice()).unwrap_or(&[]) {
                if part.get_str("kind") == Some("note") {
                    if let Some(id) = part.get_str("id") {
                        out.insert(id.to_string(), w.to_string());
                    }
                }
            }
        }
        Ok(out)
    }

    /// The words given with each experiment's gate as it stands, and who set it, by experiment id: the gate is in
    /// experiment.yaml, the words on the log line of the gate write that set it (a later one with none drops them).
    pub fn gate_words(&self) -> Res<HashMap<String, (String, String)>> {
        let mut out = HashMap::new();
        for row in self.log_entries()?.iter() {
            let (Some(op @ ("gate" | "add" | "adopt")), Some("experiment"), Some(id)) = (row.get_str("op"), row.get_str("kind"), row.get_str("id")) else { continue };
            // who set the gate: its latest rb experiment gate, else rb experiment add; only a gate line gives it words, and
            // the user's confirmation of a session's gate (rb confirm <experiment>) keeps the words it was set with. An
            // adoption that changed the gate set it, with no words; any other adoption leaves it as it was
            let detail = row.get("detail");
            if op == "adopt" && !detail.map_or(false, |d| d.get("if_holds").is_some()) {
                continue;
            }
            // the user's confirmation of a design leaves who set its gate: the design says whether it covers the gate
            // (`Design::writer`), which it does only while no session changed the design since
            if op == "gate" && detail.map_or(false, |d| d.get_str("design").is_some()) {
                continue;
            }
            let confirms = detail.and_then(|d| d.get_str("confirms")) == Some("gate");
            let words = match op {
                "gate" if confirms => out.get(id).map(|(w, _): &(String, String)| w.clone()).unwrap_or_default(),
                "gate" => row.get_str("words").unwrap_or("").to_string(),
                _ => String::new(),
            };
            out.insert(id.to_string(), (words, row.get_str("actor").unwrap_or("").to_string()));
        }
        Ok(out)
    }

    /// The command that would add evidence to a criterion.
    pub fn run_hint(&self, c: &Criterion) -> Res<String> {
        // a criterion on a name close to the one its runs report reads them only once it is declared on theirs
        if let Some((base, near)) = self.reported_near(c)? {
            return Ok(format!("rb retract {} --why \"it reads {base}; the runs report {near}\"   (then declare it again on {near})", c.id));
        }
        let exp: Experiment = self.load(&c.experiment)?;
        let (subject, reference, metric, _) = self.arms(&exp, c)?;
        let arms_here: Vec<&String> = [&subject, &reference].into_iter().filter(|a| !a.is_empty()).collect();
        let reads_analysis = self.live::<Analysis>()?.iter().any(|a| a.experiment == exp.id) && !self.analysis_readings(&exp, &subject, &reference, &metric, &c.estimand)?.is_empty();
        if !c.estimand.is_empty()
            || reads_analysis
            || (!arms_here.is_empty() && arms_here.iter().any(|a| exp.variant(a).is_none()) && !arms_here.iter().all(|a| ROLES.contains(&a.as_str())))
        {
            let pair = if !reference.is_empty() { format!("{subject}/{reference}") } else { subject.clone() };
            let est = format!("{metric}{}", if c.estimand.is_empty() { String::new() } else { format!("@{}", c.estimand) });
            return Ok(format!(
                "rb analysis add {} --compare \"{pair}:{est}=<estimate>[<lo>,<hi>]\" --method \"<how the interval was made>\" --unit \"<what it resampled>\" --units <how many> --source <the file that states it>",
                exp.id
            ));
        }
        let pairs = if !reference.is_empty() {
            format!("{reference}.{metric}=<value> {subject}.{metric}=<value>")
        } else if !subject.is_empty() {
            format!("{subject}.{metric}=<value>")
        } else {
            format!("{}=<value>", c.metric)
        };
        let per_run = exp.all_settings().into_iter().filter(|(_, s)| s.per_run && s.retracted.is_none()).map(|(n, _)| format!("--set {n}=<value>")).collect::<Vec<_>>().join(" ");
        let per_run = if per_run.is_empty() { String::new() } else { format!(" {per_run}") };
        // where the work keeps its runs in a database, a run is read from its row: no number typed, no command
        let stores = crate::repo::stores(&self.root);
        if !stores.is_empty() {
            let store = if stores.len() == 1 { crate::cli::shlex_quote(&stores[0]) } else { "<database>".into() };
            let arm = if reference.is_empty() && subject.is_empty() { "" } else { " --variant <variant>" };
            return Ok(format!("rb run add {}{arm} --from {store} --table <table> --where <column>=<value>{per_run}", exp.id));
        }
        Ok(format!("rb run add {} {pairs}{per_run} --command \"...\"", exp.id))
    }

    /// The question link a criterion add on this experiment gives: the one live question it is filed under, or a
    /// placeholder when it is filed under none or several, or under one this rb cannot read. A hint never fails the
    /// command that gives it.
    pub fn question_arg(&self, exp: &Experiment) -> String {
        let mut live: Vec<&String> = Vec::new();
        let mut unread = false;
        for q in &exp.questions {
            match self.load::<Question>(q) {
                Ok(x) if x.retracted.is_none() && !live.contains(&q) => live.push(q),
                Ok(_) => (),
                Err(e) if e.code == "E_OBJECT_NOT_FOUND" => (),
                Err(_) => unread = true,
            }
        }
        match (live.as_slice(), unread) {
            ([q], false) => format!(" -q {q}"),
            _ => " -q <question>".to_string(),
        }
    }

    /// The runs that count for the experiment as a whole, whatever any criterion says.
    pub fn counted_runs(&self, exp: &Experiment) -> Res<Vec<Run>> {
        let spec_now = self.spec_sha(exp);
        let arms_now = self.arm_specs(exp);
        let all = self.standing_runs()?;
        let mut standing: Vec<&Run> = all.iter().filter(|r| r.experiment == exp.id).collect();
        standing.sort_by(|a, b| a.created_at.cmp(&b.created_at));
        let mut out = Vec::new();
        for r in standing {
            let mut reported: Vec<String> = variants_of(&r).into_iter().collect();
            reported.sort();
            if r.experiment != exp.id
                || r.synthetic
                || !self.same_spec(&r, &spec_now, &reported, &arms_now)
                || r.repeats.as_ref().map_or(false, |x| !x.is_empty())
                || self.edited_outside("run", &r.id)?.is_some()
            {
                continue;
            }
            if r.receipt.config.as_ref().map_or(false, |c| !c.given().mismatches.is_empty()) {
                continue;
            }
            out.push(r.clone());
        }
        Ok(out)
    }

    fn unit_key(&self, r: &Run, unit_setting: Option<&str>, factors: &[String]) -> String {
        if let Some(us) = unit_setting {
            return match r.per_run.get(us).filter(|v| !v.is_null()) {
                // counted within its design cell, as a finding counts it
                Some(u) => super::findings::unit_within(format!("{us}={}", super::findings::unit_value(u)), &r.per_run, unit_setting, factors),
                None => r.id.clone(),
            };
        }
        if !r.per_run.is_empty() {
            let mut kv: Vec<(&String, &Value)> = r.per_run.iter().collect();
            kv.sort_by(|a, b| a.0.cmp(b.0));
            return kv.iter().map(|(k, v)| format!("{k}={}", super::findings::unit_value(v))).collect::<Vec<_>>().join(" · ");
        }
        r.id.clone()
    }

    /// The experiment's own table.
    pub fn compare(&self, experiment_id: &str) -> Res<Dict> {
        let exp: Experiment = self.load(experiment_id)?;
        let aliases = self.metric_aliases()?;
        let catalogue: IndexMap<String, Metric> = self.live::<Metric>()?.into_iter().map(|m| (m.id.clone(), m)).collect();
        let runs = self.counted_runs(&exp)?;
        let unit_setting = self.unit_setting(&exp);
        let factors: Vec<String> = exp.settings.iter().filter(|s| s.retracted.is_none() && s.design_factor == Some(true)).map(|s| s.name.clone()).collect();
        let mut variants: Vec<&Variant> = exp.live_variants();
        variants.sort_by(|a, b| (ROLES.iter().position(|r| *r == a.role).unwrap_or(99), &a.name).cmp(&(ROLES.iter().position(|r| *r == b.role).unwrap_or(99), &b.name)));
        // a per-run setting a design factor or a criterion's selector names is a condition, never the unit: where the
        // runs take more than one cell of them, a row holds one variant in one cell, never a mean pooled across cells
        let crits: Vec<Criterion> = self.live::<Criterion>()?.into_iter().filter(|c| c.experiment == exp.id).collect();
        let mut conditions: Vec<String> = exp
            .settings
            .iter()
            .filter(|s| s.retracted.is_none() && s.per_run && Some(s.name.as_str()) != unit_setting.as_deref())
            .filter(|s| s.design_factor == Some(true) || crits.iter().any(|c| [&c.r#where, &c.subject_where, &c.reference_where].iter().any(|w| w.contains_key(&s.name))))
            .map(|s| s.name.clone())
            .collect();
        // with the unit declared, so is any other per-run setting that holds more than one value among one variant's
        // runs; with none, the per-run settings together are the unit
        let heads = |r: &Run| -> HashSet<String> { r.metrics.keys().map(|k| partition(&canon(k, &exp, &aliases), ".").0.to_string()).collect() };
        let others: Vec<&Setting> = exp.settings.iter().filter(|s| unit_setting.is_some() && s.retracted.is_none() && s.per_run && Some(s.name.as_str()) != unit_setting.as_deref() && !conditions.contains(&s.name)).collect();
        for s in others {
            let mixed = variants.iter().any(|v| {
                let mut seen: Vec<String> = Vec::new();
                for r in runs.iter().filter(|r| heads(r).contains(&v.name)) {
                    let at = super::findings::unit_value(r.per_run.get(&s.name).unwrap_or(&Value::Null));
                    if !seen.contains(&at) {
                        seen.push(at);
                    }
                }
                seen.len() > 1
            });
            if mixed {
                conditions.push(s.name.clone());
            }
        }
        conditions.sort_by_key(|n| exp.settings.iter().position(|s| &s.name == n));
        let cell_of = |r: &Run, conditions: &[String]| -> Vec<Value> { conditions.iter().map(|k| r.per_run.get(k).cloned().unwrap_or(Value::Null)).collect() };
        let label = |c: &[Value]| -> String { c.iter().map(|v| super::findings::unit_value(v)).collect::<Vec<_>>().join(" · ") };
        let mut cells_seen: Vec<Vec<Value>> = Vec::new();
        for r in &runs {
            let c = cell_of(r, &conditions);
            if !cells_seen.iter().any(|x| label(x) == label(&c)) {
                cells_seen.push(c);
            }
        }
        if cells_seen.len() < 2 {
            conditions.clear();
            cells_seen = vec![Vec::new()];
        }
        cells_seen.sort_by(|a, b| cell_order(a, b));
        let split = !conditions.is_empty();
        let mut per: IndexMap<String, IndexMap<String, IndexMap<String, IndexMap<String, Vec<f64>>>>> = variants.iter().map(|v| (v.name.clone(), IndexMap::new())).collect();
        let mut overall: IndexMap<String, IndexMap<String, Vec<f64>>> = IndexMap::new();
        // the runs each variant's cell of a metric reads, by id: what a screen's and a page's note on their code is of
        let mut cell_runs: HashMap<(String, String, String), Vec<String>> = HashMap::new();
        for r in &runs {
            let key = self.unit_key(r, unit_setting.as_deref(), &factors);
            let cell = label(&cell_of(r, &conditions));
            for (k, x) in &r.metrics {
                if r.retracted.is_some() && superseded_for(r, k, None) {
                    continue;
                }
                let cn = canon(k, &exp, &aliases);
                let (head, _, rest) = partition(&cn, ".");
                if per.contains_key(head) && !rest.is_empty() {
                    let ids = cell_runs.entry((head.to_string(), cell.clone(), rest.to_string())).or_default();
                    if !ids.contains(&r.id) {
                        ids.push(r.id.clone());
                    }
                    per.get_mut(head).unwrap().entry(cell.clone()).or_default().entry(rest.to_string()).or_default().entry(key.clone()).or_default().push(*x);
                } else {
                    overall.entry(cn.clone()).or_default().entry(key.clone()).or_default().push(*x);
                }
            }
        }
        let means = |by_unit: &IndexMap<String, Vec<f64>>| -> Vec<f64> { by_unit.values().map(|v| assess::fmean(v)).collect() };
        let keys: HashSet<String> = runs.iter().map(|r| self.unit_key(r, unit_setting.as_deref(), &factors)).collect();
        let simple = |k: &str| k.contains('=') && !k.contains(" · ");
        let names: HashSet<&str> = keys.iter().filter(|k| simple(k)).map(|k| k.split('=').next().unwrap_or("")).collect();
        // as a reading names its unit: a per-run setting's, a run's, or a cell's of several per-run settings
        let unit_name = match &unit_setting {
            Some(u) if !u.is_empty() => u.clone(),
            _ if names.len() == 1 && keys.iter().all(|k| simple(k)) => names.iter().next().unwrap().to_string(),
            _ if keys.iter().all(|k| !k.contains('=')) => "run".into(),
            _ => "unit".into(),
        };
        // as a grid's cell counts what it rests on: within its condition cell, by the one per-run setting left
        let cell_unit = match &unit_setting {
            Some(u) if !u.is_empty() => u.clone(),
            _ => {
                let left: Vec<&Setting> = exp.settings.iter().filter(|s| s.retracted.is_none() && s.per_run && !conditions.contains(&s.name) && !factors.contains(&s.name)).collect();
                match left.as_slice() {
                    [one] => one.name.clone(),
                    // the cell's runs share their per-run values: they are one unit, never each a run
                    [] if keys.iter().all(|k| !k.contains('=')) => "run".into(),
                    [] => "unit".into(),
                    _ => unit_name.clone(),
                }
            }
        };
        let mut metric_names: Vec<String> = per.values().flat_map(|d| d.values().flat_map(|e| e.keys().cloned())).collect::<BTreeSet<_>>().into_iter().collect();
        metric_names.sort_by(|a, b| (!catalogue.contains_key(a), a).cmp(&(!catalogue.contains_key(b), b)));
        let base = variants.iter().find(|v| v.role == "baseline").map(|v| v.name.clone());
        let mut primary: Vec<Dict> = Vec::new();
        // what each primary criterion reads: a change in the table is marked only when it is that number
        let mut reads: Vec<Option<Reading>> = Vec::new();
        let mut readers: Vec<Criterion> = Vec::new();
        for c in crits.iter().cloned() {
            if c.role == "primary" {
                let (s_, r_, m_, _) = self.arms(&exp, &c)?;
                primary.push(crate::dict! {"subject" => s_, "reference" => r_, "metric" => m_, "criterion" => c.id.clone()});
                // a finding that cannot be worked out reads nothing here: the table stands, unmarked
                reads.push(self.finding(&c).ok().and_then(|f| f.comparison).filter(|r| r.estimate.is_some()));
                readers.push(c);
            }
        }
        // every criterion's reading of a pair of arms, either way round: where one reads a cell's comparison otherwise
        // than the table's unpaired means do, the cell carries that reading, and a screen shows it in the means' place
        let mut pair_reads: Vec<(String, String, String, String, f64, String, &Criterion)> = Vec::new();
        // each such reading's interval, by criterion: a grid's cell gives a rule's own where the rule reads its change
        let mut pair_bounds: HashMap<String, (Option<f64>, Option<f64>)> = HashMap::new();
        for c in crits.iter() {
            let (s_, r_, m_, _) = self.arms(&exp, c)?;
            if s_.is_empty() || r_.is_empty() {
                continue;
            }
            let Some(r) = self.finding(c).ok().and_then(|f| f.comparison).filter(|r| r.estimate.is_some()) else { continue };
            let entry = catalogue.get(&m_);
            let said = assess::reading_for(c, &r, &entry.map(|e| e.unit.clone()).unwrap_or_default(), entry.map(|e| e.direction.as_str()).unwrap_or("none"));
            pair_bounds.insert(c.id.clone(), (r.lo, r.hi));
            pair_reads.push((s_, r_, m_, c.id.clone(), r.estimate.unwrap_or(f64::NAN), format!("{}: {said}", c.reads()), c));
        }
        // a criterion reads a split row's comparison only where it selects both arms in that row's cell
        let in_cell = |c: &Criterion, at: &[Value]| -> bool {
            conditions.iter().zip(at).all(|(k, v)| {
                let side = |w: &Dict| w.get(k).or_else(|| c.r#where.get(k)).map_or(false, |x| super::findings::selector_equal(x, v));
                side(&c.subject_where) && (!c.compares() || side(&c.reference_where))
            })
        };
        let none = IndexMap::new();
        // on the user's screens and on a page, where a cell's runs ran other code, it says so
        let code = match crate::view::reader() {
            crate::view::Reader::Text => None,
            _ => Some(crate::view::CodeNotes::of(self)?),
        };
        let mut rows: Vec<Value> = Vec::new();
        let mut unvaried: Vec<String> = Vec::new();
        for n in variants.iter().flat_map(|v| v.settings.iter().filter(|s| s.retracted.is_none() && !s.per_run).map(|s| s.name.clone())) {
            let values: Vec<Value> = variants.iter().map(|v| exp.value_in(v, &n)).collect();
            if !exp.varies.contains(&n) && !unvaried.contains(&n) && values.iter().any(|x| *x != values[0]) {
                unvaried.push(n);
            }
        }
        let base_variant = variants.iter().find(|x| Some(x.name.as_str()) == base.as_deref()).copied();
        for v in &variants {
            // the varied factors it holds otherwise than the baseline: more than one, and its change is no one factor's
            let differs: Vec<String> = match base_variant {
                Some(b) if b.name != v.name => exp
                    .varies
                    .iter()
                    .filter(|n| {
                        let (mine, theirs) = (exp.value_in(v, n), exp.value_in(b, n));
                        !mine.is_null() && !theirs.is_null() && mine != theirs
                    })
                    .cloned()
                    .collect(),
                _ => Vec::new(),
            };
            for at in &cells_seen {
                let here = label(at);
                let mine = per[&v.name].get(&here).unwrap_or(&none);
                // a split row is a cell the variant has runs in; a variant with none keeps one row
                if split && mine.is_empty() && (per[&v.name].values().any(|d| !d.is_empty()) || at != &cells_seen[0]) {
                    continue;
                }
                let theirs_at = per.get(base.as_deref().unwrap_or("")).and_then(|d| d.get(&here));
                let mut cells = Dict::new();
                for m in &metric_names {
                    let empty = IndexMap::new();
                    let xs = means(mine.get(m).unwrap_or(&empty));
                    if xs.is_empty() {
                        continue;
                    }
                    let mean = assess::fmean(&xs);
                    let mut cell = crate::dict! {"mean" => mean, "n" => xs.len()};
                    // the runs the cell's mean is over, and how many, said as a grid says them: "3 seeds", "1 run"
                    let own_runs = cell_runs.get(&(v.name.clone(), here.clone(), m.clone())).cloned().unwrap_or_default();
                    cell.insert("runs".into(), Value::Int(own_runs.len() as i64));
                    cell.insert("run_ids".into(), Value::list_of_str(own_runs.clone()));
                    cell.insert("count_said".into(), Value::Str(count_said(xs.len(), own_runs.len(), &cell_unit)));
                    if let Some(b) = &base {
                        if let Some(bm) = theirs_at.and_then(|d| d.get(m)).filter(|x| *b != v.name && !x.is_empty()) {
                            let delta = minus(mean, assess::fmean(&means(bm)));
                            let direction = catalogue.get(m).map(|c| c.direction.clone()).unwrap_or_else(|| "none".into());
                            cell.insert("delta".into(), Value::Float(delta));
                            cell.insert(
                                "better".into(),
                                if direction == "none" || delta == 0.0 { Value::Null } else { Value::Bool((delta < 0.0) == (direction == "minimize")) },
                            );
                            // a change of means is paired only over the same units on both sides; what it rests on is said
                            let paired = mine.get(m).map_or(false, |u| u.len() == bm.len() && u.keys().all(|k| bm.contains_key(k)));
                            cell.insert("paired".into(), Value::Bool(paired));
                            // a setting the experiment does not declare it varies, held otherwise than by the baseline:
                            // the baseline is not in this cell's column, so the change is no difference within one
                            let apart: Vec<String> = unvaried.iter().filter(|n| base_variant.map_or(false, |bv| exp.value_in(v, n) != exp.value_in(bv, n))).cloned().collect();
                            cell.insert("in_column".into(), Value::Bool(apart.is_empty()));
                            // within its column, the change's interval as rb gives one (a t interval over the units'
                            // differences where they pair, Welch's where they never share one, from 3), or why there is
                            // none; where some units pair and others do not, rb's reading pairs only those, so these
                            // means over all of them take no interval
                            let shared = mine.get(m).map_or(0, |u| u.keys().filter(|k| bm.contains_key(*k)).count());
                            let (lo, hi, why) = match (paired, mine.get(m)) {
                                _ if !apart.is_empty() => (None, None, format!("{b} was not run with the same {}, so no difference here", apart.join(" and "))),
                                (true, Some(u)) => {
                                    let diffs: Vec<f64> = u.iter().map(|(k, x)| assess::fmean(x) - assess::fmean(&bm[k])).collect();
                                    let (_, lo, hi, _) = assess::mean_interval(&diffs, 0.95);
                                    let why = match diffs.len() {
                                        1 => format!("1 {cell_unit}, so no interval"),
                                        n => format!("{n} {}; an interval needs 3", plural(&cell_unit)),
                                    };
                                    (lo, hi, why)
                                }
                                _ if shared > 0 => (None, None, format!("only {shared} of the {} match, so no interval over all of them", plural(&cell_unit))),
                                _ => {
                                    let (_, lo, hi, _) = assess::welch_interval(&xs, &means(bm), 0.95);
                                    let unmatched = if cell_unit == "run" { "the runs don't pair".to_string() } else { format!("{} don't match", plural(&cell_unit)) };
                                    (lo, hi, format!("{unmatched}, and {} against {} is too few for an interval", xs.len(), bm.len()))
                                }
                            };
                            let mut basis: Vec<String> = Vec::new();
                            if xs.len() == 1 {
                                basis.push("n=1".into());
                            }
                            if !paired {
                                basis.push("unpaired".into());
                            }
                            let mut basis = basis.join(", ");
                            if differs.len() > 1 {
                                let (last, rest) = differs.split_last().unwrap();
                                let said = format!("differs from the baseline in {} and {last}", rest.join(", "));
                                basis = if basis.is_empty() { said } else { format!("{basis}; {said}") };
                                cell.insert("differs_in".into(), Value::list_of_str(differs.clone()));
                            }
                            if !basis.is_empty() {
                                cell.insert("basis".into(), Value::Str(basis));
                            }
                            let same = |e: f64| (e - delta).abs() <= 1e-9 * e.abs().max(delta.abs()).max(1.0);
                            let prim: Vec<usize> = (0..primary.len())
                                .filter(|i| {
                                    let p = &primary[*i];
                                    p.get_str("subject") == Some(v.name.as_str()) && p.get_str("reference") == Some(b.as_str()) && p.get_str("metric") == Some(m.as_str())
                                })
                                .filter(|i| reads[*i].as_ref().and_then(|r| r.estimate).map_or(false, same))
                                .filter(|i| !split || in_cell(&readers[*i], at))
                                .collect();
                            cell.insert("primary".into(), Value::Bool(!prim.is_empty()));
                            // the change a primary criterion reads, said as its rule says it (+0.05004, never +0.05 beside ≤ 0.05)
                            let said: Option<String> = prim
                                .iter()
                                .filter_map(|i| {
                                    let r = reads[*i].as_ref()?;
                                    let said = assess::rule_numbers(&readers[*i], r, &direction);
                                    said.iter().any(|x| x.is_some()).then(|| assess::said_all([r.estimate, r.lo, r.hi], &said, true)[0].clone())
                                })
                                .max_by_key(|s| s.len());
                            if let Some(said) = said {
                                cell.insert("said".into(), Value::Str(said));
                            }
                            let theirs: Vec<&(String, String, String, String, f64, String, &Criterion)> = pair_reads
                                .iter()
                                .filter(|p| p.2 == *m && ((p.0 == v.name && p.1 == *b) || (p.0 == *b && p.1 == v.name)))
                                .filter(|p| !split || in_cell(p.6, at))
                                .collect();
                            // the rules and comparisons that read this change in this cell, which a grid's difference opens
                            // a rule read by design cell, selecting none of them, reads each cell's change too
                            let by_cell = |c: &Criterion| {
                                split && conditions.iter().all(|k| factors.contains(k) && ![&c.r#where, &c.subject_where, &c.reference_where].iter().any(|w| w.contains_key(k)))
                            };
                            let across: Vec<&(String, String, String, String, f64, String, &Criterion)> = pair_reads
                                .iter()
                                .filter(|p| p.2 == *m && ((p.0 == v.name && p.1 == *b) || (p.0 == *b && p.1 == v.name)) && by_cell(p.6))
                                .collect();
                            let mut criteria: Vec<String> = Vec::new();
                            for p in theirs.iter().chain(across.iter()) {
                                if !criteria.contains(&p.3) {
                                    criteria.push(p.3.clone());
                                }
                            }
                            cell.insert("criteria".into(), Value::list_of_str(criteria));
                            let differ = !theirs.is_empty() && !theirs.iter().any(|p| same(if p.0 == v.name { p.4 } else { -p.4 }));
                            // where a rule reads the change otherwise than these means, its reading is the cell's: no
                            // second interval beside it
                            // a rule that reads this change gives its own interval: one comparison, one interval
                            let ruled = theirs.iter().find(|p| same(if p.0 == v.name { p.4 } else { -p.4 })).and_then(|p| {
                                let (lo, hi) = pair_bounds.get(&p.3).copied()?;
                                Some(if p.0 == v.name { (lo, hi) } else { (hi.map(|x| -x), lo.map(|x| -x)) })
                            });
                            let (lo, hi, why) = match ruled {
                                Some((Some(lo), Some(hi))) if apart.is_empty() => (Some(lo), Some(hi), why),
                                Some(_) if apart.is_empty() => (None, None, "the rule that reads it gives no interval yet".to_string()),
                                _ => (lo, hi, why),
                            };
                            match (lo, hi) {
                                _ if differ => (),
                                (Some(lo), Some(hi)) => {
                                    cell.insert("lo".into(), Value::Float(lo));
                                    cell.insert("hi".into(), Value::Float(hi));
                                    cell.insert("level".into(), Value::Float(0.95));
                                }
                                _ => {
                                    cell.insert("no_interval".into(), Value::Str(why));
                                }
                            }
                            if differ {
                                let units = bm.len();
                                cell.insert("unpaired".into(), Value::Str(format!("unpaired means over {} and {units} {unit_name}{}", xs.len(), if xs.len() + units == 2 { "" } else { "s" })));
                                cell.insert(
                                    "read_by".into(),
                                    Value::List(theirs.iter().map(|p| crate::obj! {"criterion" => p.3.clone(), "reads" => p.5.clone()}).collect()),
                                );
                            }
                        }
                    }
                    if let Some(code) = &code {
                        // the runs the cell reads, and the baseline's it is compared with
                        let mut ids = cell_runs.get(&(v.name.clone(), here.clone(), m.clone())).cloned().unwrap_or_default();
                        if cell.contains_key("delta") {
                            for id in base.as_ref().and_then(|b| cell_runs.get(&(b.clone(), here.clone(), m.clone()))).into_iter().flatten() {
                                if !ids.contains(id) {
                                    ids.push(id.clone());
                                }
                            }
                        }
                        let said = code.said(&ids);
                        if !said.is_empty() {
                            cell.insert("other_code".into(), Value::Str(said));
                        }
                    }
                    cells.insert(m.clone(), Value::Dict(cell));
                }
                let varies: Dict = exp.varies.iter().map(|n| (n.clone(), exp.value_in(v, n))).collect();
                let mut row = crate::dict! {"variant" => v.name.clone(), "role" => v.role.clone(), "varies" => Value::Dict(varies), "metrics" => Value::Dict(cells)};
                // a setting the variants hold apart that the experiment does not declare it varies: what a column
                // of numbers measured under one set of settings must also hold the same
                row.insert("unvaried".into(), Value::Dict(unvaried.iter().map(|n| (n.clone(), exp.value_in(v, n))).collect()));
                if split {
                    let at: Vec<Value> = if mine.is_empty() { vec![Value::Null; conditions.len()] } else { at.clone() };
                    row.insert("where".into(), Value::Dict(conditions.iter().cloned().zip(at).collect()));
                }
                rows.push(Value::Dict(row));
            }
        }
        // what each primary criterion reads, as its rule says its numbers: the table's means and changes are said to a
        // few digits, and the number a rule reads may sit closer to its threshold than they show
        for (i, p) in primary.iter_mut().enumerate() {
            if let Some(r) = &reads[i] {
                let entry = catalogue.get(p.get_str("metric").unwrap_or(""));
                let unit = entry.map(|c| c.unit.clone()).unwrap_or_default();
                let direction = entry.map(|c| c.direction.as_str()).unwrap_or("none");
                p.insert("reads".into(), Value::Str(assess::reading_for(&readers[i], r, &unit, direction)));
            }
        }
        let mut ov: Vec<(&String, &IndexMap<String, Vec<f64>>)> = overall.iter().collect();
        ov.sort_by(|a, b| a.0.cmp(b.0));
        let overall_v: Dict = ov.into_iter().map(|(k, v)| (k.clone(), crate::obj! {"mean" => assess::fmean(&means(v)), "n" => v.len()})).collect();
        let metrics_v: Vec<Value> = metric_names
            .iter()
            .map(|m| {
                crate::obj! {
                    "name" => m.clone(), "unit" => catalogue.get(m).map(|c| c.unit.clone()).unwrap_or_default(),
                    "direction" => catalogue.get(m).map(|c| c.direction.clone()).unwrap_or_else(|| "none".into()), "declared" => catalogue.contains_key(m)
                }
            })
            .collect();
        let mut out = crate::dict! {
            "experiment_id" => exp.id.clone(), "title" => exp.title.clone(), "varies" => Value::list_of_str(exp.varies.clone()),
            "baseline" => base, "metrics" => Value::List(metrics_v), "rows" => Value::List(rows), "overall" => Value::Dict(overall_v),
            "run_count" => runs.len(), "unit" => unit_name, "cell_unit" => cell_unit, "primary" => Value::List(primary.into_iter().map(Value::Dict).collect())
        };
        if split {
            out.insert("conditions".into(), Value::list_of_str(conditions));
        }
        Ok(out)
    }

    /// planned, open, or what the latest decision on it made it.
    pub fn experiment_state(&self, exp: &Experiment, runs: Option<&[Run]>) -> Res<String> {
        let decs = self.decisions_on(&exp.id)?;
        if let Some(d) = decs.last() {
            return Ok(state_of(&d.action).to_string());
        }
        let has_runs = match runs {
            Some(r) => !r.is_empty(),
            None => self.standing_runs()?.iter().any(|r| r.experiment == exp.id),
        };
        let analyses = self.live::<Analysis>()?.iter().any(|a| a.experiment == exp.id);
        let in_repo = exp.files.iter().any(|f| matches!(f.role.as_str(), "results" | "manifest" | "audit" | "data"));
        Ok(if has_runs || analyses || in_repo { "open".into() } else { "planned".into() })
    }

    pub fn question_state(&self, q: &Question) -> Res<String> {
        let decs = self.decisions_on(&q.id)?;
        Ok(match decs.last() {
            Some(d) => question_state_of(&d.action).to_string(),
            None => "open".into(),
        })
    }

    /// The questions an experiment is filed under, and every question above them.
    pub fn questions_above(&self, exp: &Experiment) -> Res<Vec<String>> {
        self.these_and_above(&exp.questions)
    }

    /// The questions a direction call on which settles a gate's miss: those the gate is linked to and every question
    /// above them, or, with no active link, those its experiment is filed under and every question above them. The
    /// same rule holds whichever rb recorded the call.
    pub fn gate_questions(&self, c: &Criterion) -> Res<Vec<String>> {
        let linked: Vec<String> = c.question_links.iter().filter(|l| l.retracted.is_none()).map(|l| l.question.clone()).collect();
        if !linked.is_empty() {
            return self.these_and_above(&linked);
        }
        if self.kind_of(&c.experiment)? != Some("experiment") {
            return Ok(Vec::new());
        }
        self.questions_above(&self.load::<Experiment>(&c.experiment)?)
    }

    /// These questions and every question above them.
    fn these_and_above(&self, questions: &[String]) -> Res<Vec<String>> {
        let mut out: Vec<String> = Vec::new();
        let mut frontier: std::collections::VecDeque<String> = questions.iter().cloned().collect();
        while let Some(qid) = frontier.pop_front() {
            if out.contains(&qid) || self.kind_of(&qid)? != Some("question") {
                continue;
            }
            out.push(qid.clone());
            // a question only a newer rb reads keeps its place; what is above it is that rb's to read
            if self.newer_only("question", &qid) {
                continue;
            }
            if let Some(p) = self.load::<Question>(&qid)?.parent.filter(|p| !p.is_empty()) {
                frontier.push_back(p);
            }
        }
        Ok(out)
    }

    pub fn children(&self, question_id: &str) -> Res<Vec<Question>> {
        Ok(self.live::<Question>()?.into_iter().filter(|q| q.parent.as_deref() == Some(question_id)).collect())
    }

    /// The experiments filed under a question, and, with `deep`, under every question below it.
    pub fn experiments_under(&self, question_id: &str, deep: bool) -> Res<HashSet<String>> {
        let mut ids: HashSet<String> = HashSet::from([question_id.to_string()]);
        if deep {
            let mut frontier = vec![question_id.to_string()];
            while let Some(at) = frontier.pop() {
                for q in self.children(&at)? {
                    if !ids.contains(&q.id) {
                        ids.insert(q.id.clone());
                        frontier.push(q.id.clone());
                    }
                }
            }
        }
        Ok(self.live::<Experiment>()?.into_iter().filter(|e| e.questions.iter().any(|q| ids.contains(q))).map(|e| e.id).collect())
    }

    /// Findings explicitly linked to this question, by axis. Experiment and child counts remain navigation;
    /// neither filing nor a descendant's finding is evidence of an answer to this question.
    pub fn rollup(&self, q: &Question, findings: &IndexMap<String, Finding>, experiments: &[Experiment]) -> Res<Dict> {
        let under = self.experiments_under(&q.id, true)?;
        let selected = self.inquiry_criterion_ids(&q.id)?;
        let mut rules: IndexMap<String, i64> = IndexMap::new();
        let mut results: IndexMap<String, i64> = IndexMap::new();
        let mut states: IndexMap<String, i64> = IndexMap::new();
        let mut untested: Vec<String> = Vec::new();
        for e in experiments {
            if !under.contains(&e.id) {
                continue;
            }
            let st = self.experiment_state(e, None)?;
            *states.entry(st.clone()).or_insert(0) += 1;
            if st == "planned" {
                untested.push(format!("experiment {} is planned: {}", e.id, if e.hypothesis.is_empty() { &e.title } else { &e.hypothesis }));
            }
        }
        let mut selected: Vec<String> = selected.into_iter().collect();
        selected.sort();
        for cid in selected {
            let f = match findings.get(&cid) {
                Some(f) => f.clone(),
                None => self.finding(&self.load::<Criterion>(&cid)?)?,
            };
            let w = assess::rule_word(&f.rule_outcome, &f.role, &f.origin);
            *rules.entry(w).or_insert(0) += 1;
            *results.entry(f.result.clone()).or_insert(0) += 1;
            if f.rule_outcome == "untested" {
                untested.push(format!("criterion {} is untested: {}", f.criterion, f.statement));
            }
        }
        let kids = self.children(&q.id)?;
        let mut open_kids = Vec::new();
        for k in &kids {
            if self.question_state(k)? == "open" {
                open_kids.push(k.id.clone());
            }
        }
        let counts = |m: IndexMap<String, i64>| Value::Dict(m.into_iter().map(|(k, v)| (k, Value::Int(v))).collect());
        Ok(crate::dict! {
            "rules" => counts(rules), "results" => counts(results), "experiments" => counts(states),
            "children" => Value::list_of_str(kids.iter().map(|k| k.id.clone())), "open_children" => Value::list_of_str(open_kids),
            "untested" => Value::list_of_str(untested)
        })
    }

    /// What the repository holds against what the record indexed.
    fn repository_items(&self, experiments: &[Experiment], items: &mut Vec<Dict>, gate: &mut IndexMap<String, bool>, whole: bool) -> Res<Dict> {
        use crate::repo;
        let gv = repo::git_view(&self.root);
        let unread = repo::unread(self)?;
        // rb reconcile --apply adds a folder with a report as an experiment: while it would, no item offers it, and rb
        // reconcile lists what it would add. Work from before the record began is recorded as the start protocol says.
        let apply = if unread.iter().any(|u| u.report) { "rb reconcile" } else { "rb reconcile --apply" };
        let unread = if whole { unread } else { Vec::new() };
        let (old, new): (Vec<&repo::Unread>, Vec<&repo::Unread>) = unread.iter().partition(|u| u.before);
        // once a question is recorded the record has started where the work stands: the folders from before it are older
        // work, which rb reconcile lists and which can be backfilled later, never the next step
        let started = !self.live::<Question>()?.is_empty();
        let (old_do, later) = if started { ("rb reconcile", true) } else { (START, false) };
        for (group, when, do_, older) in [(new, "", "rb reconcile", false), (old, " written before the record began, which", old_do, later)] {
            let folders: Vec<String> = group.iter().map(|u| u.folder.clone()).collect();
            let what = match folders.as_slice() {
                [] => continue,
                [one] => format!("{one} holds results{when} no recorded experiment reads yet"),
                many => format!("{} results folders hold results{when} no recorded experiment reads yet: {}", many.len(), assess::first_few(many, SHOWN_FOLDERS, ", ")),
            };
            item(items, "agent", "unindexed_folder", &folders.join(", "), &what, do_, vec![("folders", Value::list_of_str(folders.clone())), ("later", Value::Bool(older))]);
        }
        let unindexed: Vec<String> = unread.into_iter().map(|u| u.folder).collect();
        let mut reads = Dict::new();
        for e in experiments {
            if e.folder.is_empty() {
                continue;
            }
            let r = repo::read_folder(self, e, Some(&gv))?;
            let files = r.get("files").and_then(|v| v.as_list()).cloned().unwrap_or_default();
            // each file by its name in the folder: a change says which file it is
            let in_state = |state: &str| -> Vec<String> {
                files
                    .iter()
                    .filter(|f| f.get_str("state") == Some(state))
                    .map(|f| f.get_str("path").unwrap_or("").to_string())
                    .map(|p| p.strip_prefix(e.folder.as_str()).map(str::to_string).unwrap_or(p))
                    .collect()
            };
            let (changed, missing) = (in_state("changed"), in_state("missing"));
            let stale = r.get("stale_quotes").and_then(|v| v.as_list()).cloned().unwrap_or_default();
            if !changed.is_empty() || !missing.is_empty() || !stale.is_empty() {
                let mut what = Vec::new();
                for (names, word) in [(&changed, "changed"), (&missing, "missing")] {
                    if !names.is_empty() {
                        let first: Vec<&str> = names.iter().take(3).map(String::as_str).collect();
                        what.push(format!("{} {word}{}", first.join(", "), if names.len() > 3 { format!(" and {} more", names.len() - 3) } else { String::new() }));
                    }
                }
                if !stale.is_empty() {
                    what.push(format!(
                        "{} quote{} no longer in {}",
                        stale.len(),
                        if stale.len() == 1 { "" } else { "s" },
                        if stale.len() == 1 { "its file" } else { "their files" }
                    ));
                }
                item(items, "agent", "reindex", &e.id, &format!("{}: {} changed since it was indexed ({})", e.id, e.folder, what.join(", ")), apply, vec![]);
            }
            for m in r.get("manifests").and_then(|v| v.as_list()).cloned().unwrap_or_default() {
                let mut bad: Vec<String> = m.get("changed").and_then(|v| v.as_list()).map(|l| l.iter().map(pyjson::py_str).collect()).unwrap_or_default();
                bad.extend(m.get("missing").and_then(|v| v.as_list()).map(|l| l.iter().map(pyjson::py_str).collect::<Vec<_>>()).unwrap_or_default());
                if !bad.is_empty() {
                    gate.insert("stale".into(), true);
                    let sources = m.get("sources").map(pyjson::py_str).unwrap_or_default();
                    item(
                        items,
                        "anyone",
                        "manifest_changed",
                        &e.id,
                        &format!(
                            "{}: {} froze {} of {sources} {}, and {} {} changed since: {}",
                            e.id,
                            m.get_str("path").unwrap_or(""),
                            if sources == "1" { "the hash" } else { "the hashes" },
                            if sources == "1" { "source" } else { "sources" },
                            bad.len(),
                            if bad.len() == 1 { "has" } else { "have" },
                            assess::first_three(&bad, ", ")
                        ),
                        &format!("git diff -- {}   (the code a frozen result was made with is not what the repository holds now)", crate::cli::shlex_quote(&bad[0])),
                        vec![],
                    );
                }
            }
            let audits = r.get("audits").and_then(|v| v.as_list()).cloned().unwrap_or_default();
            for a in &audits {
                if !a.get("passed").map_or(false, |v| v.truthy()) {
                    gate.insert("checks".into(), true);
                    let p = a.get_str("path").unwrap_or("");
                    item(items, "anyone", "audit_failed", &e.id, &format!("{}: {p} says {}", e.id, a.get_str("says").unwrap_or("")), &format!("open {p}"), vec![]);
                }
            }
            let checks = r.get("checks").and_then(|v| v.as_list()).cloned().unwrap_or_default();
            for c in &checks {
                if c.get("ok") == Some(&Value::Bool(false)) {
                    gate.insert("checks".into(), true);
                    let cmd = c.get_str("command").filter(|s| !s.is_empty()).or(c.get_str("path")).unwrap_or("");
                    item(
                        items,
                        "agent",
                        "check_failed",
                        &e.id,
                        &format!("{}: {} {}", e.id, c.get_str("path").unwrap_or(""), c.get_str("state").unwrap_or("")),
                        &format!("rb journal run -- {cmd}   (once it is fixed)"),
                        vec![("last_lines", c.get("tail").cloned().unwrap_or(Value::Null))],
                    );
                }
            }
            let mut failing: Vec<String> = audits
                .iter()
                .filter(|a| !a.get("passed").map_or(false, |v| v.truthy()))
                .map(|a| format!("{} says {}", Path::new(a.get_str("path").unwrap_or("")).file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default(), a.get_str("says").unwrap_or("")))
                .collect();
            failing.extend(checks.iter().filter(|c| c.get("ok") == Some(&Value::Bool(false))).map(|c| format!("{} {}", c.get_str("path").unwrap_or(""), c.get_str("state").unwrap_or(""))));
            let last = self.decisions_on(&e.id)?.into_iter().last();
            if let Some(l) = last {
                if !failing.is_empty() && matches!(l.action.as_str(), "accept" | "continue" | "ship") {
                    item(
                        items,
                        "human",
                        "call_on_failed_check",
                        &e.id,
                        &format!("{}: {}{} on {}, and now {}", e.id, made(&l.action), actor::by_writer(&l.by), truncate(&l.at, 10), crate::assess::first_few(&failing, 2, "; ")),
                        &format!("rb show {}   (then rb decide {} <call> --why \"...\" on what the repository says now)", e.id, e.id),
                        vec![],
                    );
                }
            }
            reads.insert(e.id.clone(), Value::Dict(r));
        }
        let mut project_checks: Vec<Value> = Vec::new();
        if whole {
            for c in self.project()?.checks {
                let o = repo::check_outcome(self, &c)?;
                let mut row = crate::dict! {"path" => c.path.clone(), "command" => c.command.clone(), "said_at" => c.said_at.clone()};
                for (k, v) in &o {
                    row.insert(k.clone(), v.clone());
                }
                project_checks.push(Value::Dict(row));
                if o.get("ok") == Some(&Value::Bool(false)) {
                    gate.insert("checks".into(), true);
                    let cmd = if c.command.is_empty() { c.path.clone() } else { c.command.clone() };
                    item(
                        items,
                        "agent",
                        "check_failed",
                        &c.path,
                        &format!("{} {}", c.path, o.get_str("state").unwrap_or("")),
                        &format!("rb journal run -- {cmd}   (once it is fixed)"),
                        vec![("last_lines", o.get("tail").cloned().unwrap_or(Value::Null))],
                    );
                }
            }
        }
        Ok(crate::dict! {
            "unindexed" => Value::list_of_str(unindexed), "experiments" => Value::Dict(reads), "project_checks" => Value::List(project_checks),
            "last_reconcile" => repo::last_reconcile(self)?.map(Value::Dict), "commit" => gv.commit.clone(), "branch" => gv.branch.clone()
        })
    }

    /// The whole record, read the way anyone starts their day.
    pub fn status(&self, experiment: Option<&str>) -> Res<Dict> {
        let proj = self.project()?;
        let note_words = self.note_words()?;
        let gate_words = self.gate_words()?;
        let who = self.actor()?;
        let mut experiments = self.live::<Experiment>()?;
        if let Some(e) = experiment {
            self.load::<Experiment>(e)?;
            experiments.retain(|x| x.id == e);
        }
        let exp_ids: HashSet<String> = experiments.iter().map(|e| e.id.clone()).collect();
        let criteria: Vec<Criterion> = self.live::<Criterion>()?.into_iter().filter(|c| experiment.map_or(true, |e| c.experiment == e)).collect();
        // a criterion of a retracted experiment takes no more writes: why its experiment was retracted, by its id
        let retracted_experiments: HashMap<String, String> = self.all::<Experiment>()?.iter().filter_map(|e| e.retracted.as_ref().map(|r| (e.id.clone(), r.why.clone()))).collect();
        let runs: Vec<Run> = self.standing_runs()?.iter().filter(|r| exp_ids.contains(&r.experiment)).cloned().collect();
        let analyses: Vec<Analysis> = self.all::<Analysis>()?.iter().filter(|a| exp_ids.contains(&a.experiment)).cloned().collect();
        let all_questions = self.live::<Question>()?;
        let questions: Vec<Question> = match experiment {
            None => all_questions,
            Some(_) => {
                let mut mine: HashSet<String> = experiments.iter().flat_map(|e| e.questions.iter().cloned()).collect();
                mine.extend(criteria.iter().flat_map(|c| c.question_links.iter().filter(|l| l.retracted.is_none()).map(|l| l.question.clone())));
                all_questions.into_iter().filter(|q| mine.contains(&q.id)).collect()
            }
        };
        // the calls, apart from what waits for a confirmation, which is listed in a place of its own and changes nothing
        let auth = self.authorities()?;
        let decisions: Vec<Decision> = self.live::<Decision>()?.into_iter().filter(|d| auth.get(&d.id).map_or(false, Authority::is_call)).collect();
        let resources = self.live::<Resource>()?;
        let mut findings: IndexMap<String, Finding> = IndexMap::new();
        for c in &criteria {
            findings.insert(c.id.clone(), self.finding(c)?);
        }
        let mut items: Vec<Dict> = Vec::new();
        let mut tally: IndexMap<String, i64> = IndexMap::new();
        let mut results: IndexMap<String, i64> = IndexMap::new();
        for f in findings.values() {
            *tally.entry(assess::rule_word(&f.rule_outcome, &f.role, &f.origin)).or_insert(0) += 1;
            *results.entry(f.result.clone()).or_insert(0) += 1;
        }
        let mut gate: IndexMap<String, bool> = GATES.iter().map(|g| (g.to_string(), false)).collect();
        // Link integrity belongs to the inquiry. It can fail the integrity gate without changing the criterion's
        // numeric finding, and an edit already covered by doctor gets no duplicate repair item here.
        for c in &criteria {
            for link in c.active_links() {
                let twice = c.links_to(&link.question);
                if self.edited_outside("question", &link.question)?.is_some() {
                    gate.insert("integrity".into(), true);
                    continue;
                }
                // a question only a newer rb reads is named as state_newer, which fails every gate closed
                if self.newer_only("question", &link.question) {
                    continue;
                }
                let q = if self.kind_of(&link.question)? == Some("question") { Some(self.load::<Question>(&link.question)?) } else { None };
                let unavailable = q.as_ref().map_or(true, |q| q.retracted.is_some());
                let outdated = q.as_ref().map_or(false, |q| q.text != link.question_text);
                if !unavailable && !outdated && twice < 2 { continue; }
                gate.insert("integrity".into(), true);
                // its repair writes to the criterion; experiment_retracted gives the one a retracted experiment takes
                if self.edited_outside("criterion", &c.id)?.is_some() || retracted_experiments.contains_key(&c.experiment) { continue; }
                let amend = if self.load::<Experiment>(&c.experiment)?.frozen.is_some() { " --amend" } else { "" };
                // words are asked for only where a declaration has them: a filing without --when-met keeps none
                let worded = c.question_links.iter().any(|l| l.question == link.question && l.retracted.is_none() && !l.when_met.is_empty());
                let when_met = |meaning: &str| if worded { format!(" --when-met \"<{meaning}>\"") } else { String::new() };
                let (code, what, command) = if unavailable {
                    ("question_link_unavailable", format!("{} still names question {}, which is missing or retracted; its interpretation is not active", c.id, link.question),
                     format!("rb criterion file {} {} --remove --why \"<why this question no longer applies>\"{amend}", c.id, link.question))
                } else if twice > 1 {
                    ("question_link_ambiguous", format!("{} has {twice} active declarations for question {}, as a merge of two branches leaves them{}", c.id, link.question,
                        if worded { "; none of their interpretations is active" } else { "" }),
                     format!("rb criterion file {} {}{} --why \"<why this declaration holds>\"{amend}", c.id, link.question, when_met("what a met rule means for this question")))
                } else {
                    ("question_link_outdated", format!("{} was linked to earlier wording of question {}; review {}", c.id, link.question,
                        if worded { "what its met rule means for the question now" } else { "whether it bears on the question now" }),
                     format!("rb criterion file {} {}{} --why \"<why this mapping fits the current question>\"{amend}", c.id, link.question, when_met("what a met rule means for this question now")))
                };
                item(&mut items, "agent", code, &c.id, &what, &command, vec![("question", Value::Str(link.question.clone()))]);
            }
            for relation in c.active_relations() {
                let reading = self.criterion_relation(c, relation, &findings)?;
                if !reading.get("integrity").map_or(false, Value::truthy) { continue; }
                gate.insert("integrity".into(), true);
                // state only a newer rb reads has its own item (state_newer), with the upgrade as its command
                let codes: Vec<&str> = reading.get("integrity").and_then(Value::as_list).map(|l| l.iter().filter_map(|p| p.get_str("code")).collect()).unwrap_or_default();
                if codes.iter().all(|code| *code == "state_newer") { continue; }
                if retracted_experiments.contains_key(&c.experiment) { continue; }
                let twice = c.relations_to(&relation.kind, &relation.target);
                let amend = if self.load::<Experiment>(&c.experiment)?.frozen.is_some() { " --amend" } else { "" };
                if codes.contains(&"relation_ambiguous") {
                    // each declaration is said, so the alignment kept is chosen with all of them in view
                    let declared: Vec<String> = c.relations.iter().filter(|r| r.kind == relation.kind && r.target == relation.target && r.retracted.is_none())
                        .map(|r| format!("orientation {}{}{}", r.orientation, actor::by_writer(&r.declared_by), if r.why.is_empty() { String::new() } else { format!(", why: {}", r.why) }))
                        .collect();
                    item(&mut items, "agent", "relation_ambiguous", &c.id,
                        &format!("{} has {twice} active declarations that it {} {} ({}), as a merge of two branches leaves them; none is read", c.id, relation.kind, relation.target, declared.join("; ")),
                        &format!("rb criterion relate {} {} {} --orientation \"<same or opposite>\" --why \"<why this alignment holds>\"{amend}", c.id, relation.kind, relation.target),
                        vec![("relation", Value::Dict(reading))]);
                    continue;
                }
                if codes.contains(&"relation_conflict") {
                    // one item for the two declarations, named from the side listed first
                    let other: Criterion = self.load(&relation.target)?;
                    if criteria.iter().any(|x| x.id == other.id) && other.id < c.id { continue; }
                    let back = super::relations::contradicting(&other, relation, &c.id).map(|b| b.orientation.clone()).unwrap_or_default();
                    let other_amend = if self.load::<Experiment>(&other.experiment)?.frozen.is_some() { " --amend" } else { "" };
                    item(&mut items, "agent", "relation_conflict", &c.id,
                        &format!("{} {} {} with orientation {}, and {} declares {back} back; an alignment reads the same from either side, so neither is read", c.id, relation.kind, relation.target, relation.orientation, relation.target),
                        &format!("rb criterion relate {} {} {} --remove --why \"<why the other alignment holds>\"{amend}   (or withdraw the other: rb criterion relate {} {} {} --remove --why \"...\"{other_amend})",
                            c.id, relation.kind, relation.target, relation.target, relation.kind, c.id),
                        vec![("relation", Value::Dict(reading))]);
                    continue;
                }
                // the repair follows from what stops the reading: a side gone (its experiment too) takes the relation
                // back, a changed scope takes the alignment again, and a problem with a side's own evidence is read there
                let said = super::relations::relation_problems(&reading);
                let side_gone = codes.iter().any(|code| matches!(*code, "relation_target_missing" | "criterion_retracted" | "retracted" | "experiment_unavailable" | "experiment_retracted"));
                let rescoped = codes.iter().any(|code| matches!(*code, "relation_outdated" | "relation_scope_invalid"));
                let command = if side_gone {
                    format!("rb criterion relate {} {} {} --remove --why \"<why the relation no longer holds>\"{amend}", c.id, relation.kind, relation.target)
                } else if rescoped {
                    format!("rb criterion relate {} {} {} --orientation \"<same or opposite>\" --why \"<why this alignment holds for the scopes as they are>\"{amend}", c.id, relation.kind, relation.target)
                } else {
                    format!("rb show {}", c.id)
                };
                item(&mut items, "agent", "finding_relation_invalid", &c.id,
                    &format!("{} {} {}: {said}", c.id, relation.kind, relation.target),
                    &command, vec![("relation", Value::Dict(reading))]);
            }
        }
        // each experiment's late criteria with no reading of their own yet: (criterion, timing, the run that reads it)
        let mut late: IndexMap<String, Vec<(String, String, String)>> = IndexMap::new();
        for c in &criteria {
            let f = &findings[&c.id];
            let start = items.len();
            let primary = c.role == "primary";
            let claim = c.role == "primary" || c.role == "secondary";
            if primary && (f.rule_outcome == "misses" || f.rule_outcome == "undetermined") {
                gate.insert("unmet".into(), true);
            }
            if primary && f.comparison.as_ref().map_or(false, |x| !x.reference.is_empty()) && (f.result == "unresolved" || f.result == "point_only") {
                gate.insert("unresolved".into(), true);
            }
            if primary && f.rule_outcome == "untested" {
                gate.insert("untested".into(), true);
            }
            if !f.integrity.is_empty() {
                gate.insert("integrity".into(), true);
            }
            if primary && matches!(f.timing.as_str(), "after_evidence" | "after_exposure" | "unknown") {
                gate.insert("late".into(), true);
            }
            let hint = self.run_hint(c)?;
            let last = self.decisions_on(&c.id)?.into_iter().last();
            // a past call was made on no reading rb holds: its readings moving asks nobody to decide again
            let outdated = f.review.as_ref().map_or(false, |r| !r.current) && last.as_ref().map_or(false, |l| l.past.is_none());
            let says = |k: &str| f.says.get(k).cloned().unwrap_or_default();
            if f.rule_outcome == "untested" {
                if c.origin == "inherited" {
                    item(&mut items, "agent", "inherited", &c.id, &format!("{} is inherited: nothing run here has tested it yet: {}", c.id, c.statement), &hint, vec![]);
                } else {
                    // runs its rule would read but for a spec changed since: each is read again with --replaces
                    let exp: Experiment = self.load(&c.experiment)?;
                    let spec = self.spec_sha(&exp);
                    let earlier: Vec<String> = self.standing_runs()?.iter().filter(|r| r.experiment == exp.id && r.spec_sha256 != spec).map(|r| r.id.clone()).collect();
                    let again = match earlier.len() {
                        0 => String::new(),
                        n => format!(
                            " · {n} run{} recorded under an earlier spec ({}): the same rb run add with --replaces <run> --why \"...\" reads one again under this spec",
                            if n == 1 { " was" } else { "s were" },
                            assess::first_few(&earlier, 4, ", ")
                        ),
                    };
                    item(&mut items, "agent", "untested", &c.id, &format!("{} is untested: {}{again}", c.id, c.statement), &hint, vec![]);
                }
            } else if f.rule_outcome == "undetermined" {
                let why = says("rule");
                // while the declared unit separates the arms, no run adds a unit: unit_separates_arms gives the repair
                let unit_first = f.integrity.iter().any(|p| p.code == "unit_separates_arms");
                if why.contains("needs") && why.contains("unit") {
                    if !unit_first {
                        item(&mut items, "agent", "too_few_units", &c.id, &format!("{}: {why}", c.id), &hint, vec![]);
                    }
                } else if why.contains("direction") {
                    let m = if c.subject.is_empty() && c.reference.is_empty() { last_part(&c.metric).to_string() } else { c.metric.clone() };
                    item(&mut items, "agent", "direction_unknown", &c.id, &format!("{}: {why}", c.id), &format!("rb metric add {m} --minimize   (or --maximize)"), vec![]);
                } else if why.contains("interval") && why.contains("fixed design cells") {
                    // more runs never give fixed cells an interval: a sign in every cell is a threshold of 0 on each unit
                    let lower = self.catalogue_entry(&c.metric)?.map_or(true, |e| e.direction != "maximize");
                    let arms = [("--subject", &c.subject), ("--reference", &c.reference)].iter().filter(|(_, a)| !a.is_empty()).map(|(k, a)| format!(" {k} {a}")).collect::<String>();
                    item(&mut items, "agent", "no_interval", &c.id, &format!("{}: {why}", c.id),
                        &format!("rb criterion add \"<what it checks>\" -e {} --metric {}{arms} {} 0 --each --why \"<the reason, in words>\"   (a sign in every cell; then rb retract {})",
                            c.experiment, c.metric, if lower { "--at-most" } else { "--at-least" }, c.id), vec![]);
                } else if why.contains("interval") {
                    if !unit_first {
                        item(&mut items, "agent", "no_interval", &c.id, &format!("{}: {why}", c.id), &format!("{hint}   (rb gives an interval from 3 units)"), vec![]);
                    }
                } else if why.contains("edited outside rb") || why.contains("the clone is shallow") {
                } else if claim && (last.is_none() || outdated) {
                    gate.insert("undecided".into(), true);
                    item(&mut items, "human", "undecided", &c.id, &format!("{}: {why}", c.id), &decide_do(&c.id, UNDECIDED_ACTIONS), vec![]);
                }
            }
            if f.rule_outcome == "misses" && c.role == "gate" {
                let mut lines = vec![c.experiment.clone()];
                lines.extend(self.gate_questions(c)?);
                let mut calls: Vec<Decision> = Vec::new();
                for subject in &lines {
                    for d in self.decisions_on(subject)? {
                        if DIRECTION_ACTIONS.contains(&d.action.as_str()) {
                            calls.push(d);
                        }
                    }
                }
                // a call recorded as made earlier (--past) comes before every call recorded as made when it was recorded
                calls.sort_by(|a, b| (a.past.is_none(), &a.at).cmp(&(b.past.is_none(), &b.at)));
                // the latest call settles the miss while it holds the gate's reading as it stands, and is current
                let current = match calls.last() {
                    Some(d) => d.basis.get(&c.id) == Some(&assess::basis_of_finding(f)) && self.decision_current(d)?.0,
                    None => false,
                };
                if !current {
                    gate.insert("undecided".into(), true);
                    item(
                        &mut items,
                        "human",
                        "gate_missed",
                        &c.id,
                        &format!("{} gate missed ({}): a priority call, not a refutation", c.id, says("comparison")),
                        &decide_do(&c.experiment, GATE_ACTIONS),
                        vec![],
                    );
                }
            } else if f.rule_outcome == "misses" && claim && last.is_none() {
                gate.insert("undecided".into(), true);
                // a call proposed on it, or a past call mentioned, is recorded and not made: never "no call"
                let waits = self.pending_calls()?.iter().any(|(d, _)| d.subject == c.id);
                item(
                    &mut items,
                    "human",
                    "undecided",
                    &c.id,
                    &format!(
                        "{} {} and {}: {}",
                        c.id,
                        if assess::rule_word("misses", &c.role, &c.origin) == "not reproduced" { "is not reproduced" } else { "misses its rule" },
                        if waits { "the call recorded on it is not made yet: it waits for a confirmation" } else { "no call on it is recorded" },
                        c.statement
                    ),
                    &decide_do(&c.id, UNDECIDED_ACTIONS),
                    vec![],
                );
            }
            if let Some(l) = &last {
                if outdated {
                    gate.insert("undecided".into(), true);
                    item(
                        &mut items,
                        "human",
                        "decision_outdated",
                        &c.id,
                        &format!("{} ({}) was made on {}; {} now reads {}", l.id, made(&l.action), f.review.as_ref().unwrap().was, c.id, says("comparison")),
                        &format!("{}   (on the reading as it stands)", decide_do(&c.id, UNDECIDED_ACTIONS)),
                        vec![],
                    );
                }
                if l.action == "investigate" {
                    item(
                        &mut items,
                        "human",
                        "investigating",
                        &c.id,
                        // the call's own line, as every screen says it: since its day, never its id
                        &format!("{} · {}", c.id, call_line(&l.action, &l.by, &l.at, &l.why, l.past.as_ref(), self.confirms(&l.id)?)),
                        &format!("{} when it is resolved", decide_do(&c.id, INVESTIGATING_ACTIONS)),
                        vec![],
                    );
                }
            }
            // every unit the reading rests on came after it: only runs the reading leaves out came before
            let fitted_only = f.timing == "after_evidence"
                && f.prospective.as_ref().zip(f.comparison.as_ref()).map_or(false, |(p, c)| (p.estimate, p.lo, p.hi, p.n) == (c.estimate, c.lo, c.hi, c.n));
            if primary && fitted_only {
                item(
                    &mut items,
                    "agent",
                    "late",
                    &c.id,
                    &format!("{} was declared after runs it could have been fitted to ({}), though every unit its reading rests on came after it", c.id, says("timing")),
                    &format!("report the reading with the runs that came before it: rb show {}   (the late gate stays: they came before it)", c.id),
                    vec![],
                );
            } else if primary && (f.timing == "after_evidence" || f.timing == "after_exposure") && f.prospective.is_some() {
                item(
                    &mut items,
                    "agent",
                    "late",
                    &c.id,
                    &format!(
                        "{} was declared after {} ({}): its reading is not prospective",
                        c.id,
                        if f.timing == "after_evidence" { "its evidence existed" } else { "the data it reads was seen" },
                        says("timing")
                    ),
                    &format!("report the prospective reading beside it: rb show {}   (the late gate stays: its first evidence came before it)", c.id),
                    vec![],
                );
            } else if primary && (f.timing == "after_evidence" || f.timing == "after_exposure") {
                // folded with the experiment's other late criteria, after this loop
                late.entry(c.experiment.clone()).or_default().push((c.id.clone(), f.timing.clone(), hint.clone()));
            } else if primary && f.timing == "unknown" {
                item(
                    &mut items,
                    "agent",
                    "timing_unknown",
                    &c.id,
                    &format!("{}: {}: whether it was declared before its evidence cannot be read", c.id, says("timing")),
                    &format!("{hint}   (evidence with a known production time reads prospectively)"),
                    vec![],
                );
            }
            for p in &f.integrity {
                match p.code.as_str() {
                    "cited_source_changed" => {
                        gate.insert("stale".into(), true);
                        item(
                            &mut items,
                            "anyone",
                            &p.code,
                            &c.id,
                            &format!("{}: {}", c.id, p.text),
                            &format!(
                                "git diff -- {}   (restore the cited text, or retract {} and write it again against the source as it is: rb retract {} --why \"...\")",
                                crate::cli::shlex_quote(&c.source.as_ref().and_then(|s| s.path.clone()).map(|p| p).unwrap_or_else(|| if c.source.is_some() { "None".into() } else { String::new() })),
                                c.id,
                                c.id
                            ),
                            vec![],
                        );
                    }
                    "cited_unchecked" => {
                        item(
                            &mut items,
                            "agent",
                            &p.code,
                            &c.id,
                            &format!("{}: {}", c.id, p.text),
                            "save the source in the repository; a cited criterion is checked when written, so write it again against the file",
                            vec![],
                        );
                    }
                    "analysis_source_changed" => {
                        gate.insert("stale".into(), true);
                        let a: Analysis = self.load(&p.subject)?;
                        let doc = a.comparisons.iter().find_map(|x| x.source.as_ref().map(|s| s.path.clone().unwrap_or_else(|| "None".into()))).unwrap_or_default();
                        let doc = crate::cli::shlex_quote(&doc);
                        item(
                            &mut items,
                            "anyone",
                            &p.code,
                            &p.subject,
                            &format!("{}: {}", p.subject, p.text),
                            &format!(
                                "git diff -- {doc}   (restore the document, or record the analysis again against it: rb analysis add ... --supersedes {} --why \"...\")",
                                p.subject
                            ),
                            vec![],
                        );
                    }
                    "input_retracted" | "input_superseded" => {
                        gate.insert("stale".into(), true);
                        if self.kind_of(&p.subject)? == Some("analysis") {
                            item(
                                &mut items,
                                "agent",
                                &p.code,
                                &p.subject,
                                &format!("{}: {}", p.subject, p.text),
                                &format!("rb analysis add {} ... --supersedes {} --why \"<what changed in what it read>\"", c.experiment, p.subject),
                                vec![],
                            );
                        } else {
                            item(
                                &mut items,
                                "agent",
                                &p.code,
                                &p.subject,
                                &format!("{}: {}", p.subject, p.text),
                                &format!("rb retract {} --why \"<what it read was retracted>\"   (then run it again on a good copy: {hint})", p.subject),
                                vec![],
                            );
                        }
                    }
                    "units" => {
                        let us = self.unit_setting(&self.load::<Experiment>(&c.experiment)?).unwrap_or_else(|| "None".into());
                        item(
                            &mut items,
                            "agent",
                            &p.code,
                            &p.subject,
                            &format!("{}: {}", p.subject, p.text),
                            &format!(
                                "rb analysis add {} ... --unit {us} --supersedes {} --why \"<resampled over the declared unit>\"   (or, if the declared unit is wrong, amend population.unit)",
                                c.experiment, p.subject
                            ),
                            vec![],
                        );
                    }
                    "arms" => {
                        item(
                            &mut items,
                            "agent",
                            &p.code,
                            &c.id,
                            &format!("{}: {}", c.id, p.text),
                            &format!("rb retract {} --why \"...\" and declare it again with --subject and --reference", c.id),
                            vec![],
                        );
                    }
                    "unit_separates_arms" => {
                        let amend = if self.load::<Experiment>(&c.experiment)?.frozen.is_some() { " --amend --why \"...\"" } else { "" };
                        item(
                            &mut items,
                            "agent",
                            "unit_separates_arms",
                            &c.id,
                            &format!("{}: {}", c.id, p.text),
                            &format!(
                                "rb spec set {} population.unit <setting>{amend}   (the setting whose values are the units within each arm; or, if the criterion is what is wrong, rb retract {} --why \"...\" and declare it again)",
                                c.experiment, c.id
                            ),
                            vec![],
                        );
                    }
                    _ => {}
                }
            }
            // a criterion of a retracted experiment takes no more writes, so the agent is given the one repair it
            // takes, retracting it, and nothing that writes to it
            if let Some(why) = retracted_experiments.get(&c.experiment) {
                let mut kept = items.split_off(start);
                kept.retain(|i| i.get_str("who") != Some("agent"));
                items.extend(kept);
                item(&mut items, "agent", "experiment_retracted", &c.id,
                    &format!("{} belongs to {}, which was retracted ({why}); it reads with that problem until it is retracted too", c.id, c.experiment),
                    &format!("rb retract {} --why \"<its experiment was retracted>\"", c.id), vec![]);
            }
        }
        self.late_items(&mut items, late, &experiments, &criteria, &runs, &retracted_experiments)?;
        let retracted: Vec<Criterion> = self.all::<Criterion>()?.iter().filter(|c| c.retracted.is_some() && experiment.map_or(true, |e| c.experiment == e)).cloned().collect();
        for c in &retracted {
            let rt = c.retracted.as_ref().unwrap();
            if (c.role == "primary" || c.role == "gate") && !rt.by.starts_with("human:") {
                let seen = self.decisions_on(&c.id)?.iter().any(|d| d.by.starts_with("human:") && !crate::pytime::before(Some(&d.at), Some(&rt.at)));
                if !seen {
                    let words_ = match crate::view::your_words(&rt.words, &rt.by) {
                        said if said.is_empty() => String::new(),
                        said => format!(" ({said})"),
                    };
                    item(
                        &mut items,
                        "human",
                        "retracted_by_agent",
                        &c.id,
                        &format!("{} ({}: {}) was retracted{}{}{words_}", c.id, c.role, c.statement, actor::by_writer(&rt.by), crate::view::reason_part(&rt.why, &rt.by, ": ")),
                        &format!(
                            "rb show {}   (it stays on record; declare it again if it should still count, or rb decide {} accept --why \"...\" to say you have seen it)",
                            c.id, c.id
                        ),
                        vec![],
                    );
                }
            }
        }
        for e in &experiments {
            let last = self.decisions_on(&e.id)?.into_iter().last();
            if let Some(l) = last.filter(|l| !l.basis.is_empty() && l.past.is_none()) {
                let (ok, moved) = self.decision_current(&l)?;
                if !ok {
                    gate.insert("undecided".into(), true);
                    item(
                        &mut items,
                        "human",
                        "decision_outdated",
                        &e.id,
                        &format!("{} ({} {}) was made on readings that have moved since: {}", l.id, made(&l.action), e.id, crate::assess::first_few(&moved, 4, ", ")),
                        &format!("rb decide {} {} --why \"...\"   (or another call, on the readings as they stand)", e.id, l.action),
                        vec![],
                    );
                }
            }
        }
        for q in &questions {
            let mut last = self.decisions_on(&q.id)?.into_iter().last();
            if last.as_ref().map_or(false, |l| l.basis.is_empty() && l.inquiry_basis.is_none()) && self.inquiry_criterion_ids(&q.id)?.is_empty() {
                last = None;
            }
            if let Some(l) = last.filter(|l| l.past.is_none()) {
                let (ok, moved) = self.decision_current(&l)?;
                if !ok {
                    gate.insert("undecided".into(), true);
                    item(
                        &mut items,
                        "human",
                        "decision_outdated",
                        &q.id,
                        &format!("{} ({} {}) was made on readings that have moved since: {}", l.id, made(&l.action), q.id, crate::assess::first_few(&moved, 4, ", ")),
                        &format!("rb decide {} {} --why \"...\"   (or another call, on the readings as they stand)", q.id, l.action),
                        vec![],
                    );
                }
            }
        }
        let all_tamper = self.tampered()?;
        let collision = |t: &&Dict| t.get("collision").map_or(false, |v| v.truthy());
        let merge = |t: &&Dict| t.get("merge").map_or(false, |v| v.truthy());
        let cut = |t: &&Dict| t.get("shallow").map_or(false, |v| v.truthy());
        let tamper: Vec<&Dict> = all_tamper.iter().filter(|t| !collision(t) && !merge(t) && !cut(t)).collect();
        let twice: Vec<&Dict> = all_tamper.iter().filter(|t| collision(t)).collect();
        let merged: Vec<&Dict> = all_tamper.iter().filter(|t| merge(t)).collect();
        let unseen: Vec<&Dict> = all_tamper.iter().filter(|t| cut(t)).collect();
        if (!tamper.is_empty() || !twice.is_empty() || !merged.is_empty() || !unseen.is_empty()) && experiment.is_none() {
            gate.insert("integrity".into(), true);
        }
        if !merged.is_empty() {
            let ids: Vec<String> = merged.iter().take(6).map(|t| t.get("id").map(pyjson::py_str).unwrap_or_default()).collect();
            item(
                &mut items,
                "anyone",
                "merged_outside_rb",
                &format!("{}{}", ids.join(", "), if merged.len() > 6 { " ..." } else { "" }),
                &count_first(merged.len(), &format!("record in {DIR}/ changed when branches were merged"), &format!("records in {DIR}/ changed when branches were merged"), merged[0].get_str("why").unwrap_or("")),
                "rb doctor --adopt --why \"...\"   (keeps the merge as it stands; or keep both sides' changes, which rb reads as its own in the merge commit itself: git commit --amend once it is made; or the later write, which rb doctor --restore puts back)",
                vec![],
            );
        }
        if !tamper.is_empty() {
            let ids: Vec<String> = tamper.iter().take(6).map(|t| t.get("id").map(pyjson::py_str).unwrap_or_default()).collect();
            item(
                &mut items,
                "anyone",
                "edited_outside_rb",
                &format!("{}{}", ids.join(", "), if tamper.len() > 6 { " ..." } else { "" }),
                &count_first(tamper.len(), &format!("record in {DIR}/ changed outside rb"), &format!("records in {DIR}/ changed outside rb"), tamper[0].get_str("why").unwrap_or("")),
                "rb doctor --restore   (puts back what rb last wrote; if the change is right, adopt it instead: rb doctor --adopt --why \"...\")",
                vec![],
            );
        }
        if !unseen.is_empty() {
            let ids: Vec<String> = unseen.iter().take(6).map(|t| t.get("id").map(pyjson::py_str).unwrap_or_default()).collect();
            item(
                &mut items,
                "anyone",
                "shallow_clone",
                &format!("{}{}", ids.join(", "), if unseen.len() > 6 { " ..." } else { "" }),
                &count_first(unseen.len(), &format!("record in {DIR}/ whose history this shallow clone lacks"), &format!("records in {DIR}/ whose history this shallow clone lacks"), unseen[0].get_str("why").unwrap_or("")),
                &format!("{UNSHALLOW}   (fetches the whole history, so rb can see how they got there; {UNSHALLOW_CI})"),
                vec![],
            );
        }
        if !twice.is_empty() {
            let ids: Vec<String> = twice.iter().take(6).map(|t| t.get("id").map(pyjson::py_str).unwrap_or_default()).collect();
            item(
                &mut items,
                "anyone",
                "id_collision",
                &format!("{}{}", ids.join(", "), if twice.len() > 6 { " ..." } else { "" }),
                &count_first(twice.len(), "id given to two records on two branches", "ids given to two records on two branches", twice[0].get_str("why").unwrap_or("")),
                "rb doctor --restore   (gives the second record an id of its own)",
                vec![],
            );
        }
        for e in &experiments {
            if let Some(drift) = self.spec_drift(e)? {
                gate.insert("stale".into(), true);
                item(&mut items, "anyone", "drift", &e.id, &drift, &format!("rb freeze {} --amend --why \"...\"   (adopts the spec as it is now)", e.id), vec![]);
            }
            let amend = if e.frozen.is_some() { " --amend --why \"...\"" } else { "" };
            let owner = "agent";
            for cav in self.setting_caveats(e, self.has_origin(e)?, None)? {
                let name = cav.subject.split_once('/').map(|(_, t)| t.to_string()).unwrap_or_default();
                let s_ = e.lookup(&name);
                // the address already names the setting
                let told = format!("{}: {}", cav.subject, cav.text.strip_prefix(&format!("{name}: ")).unwrap_or(&cav.text));
                match cav.code.as_str() {
                    "unknown" => {
                        gate.insert("unknown".into(), true);
                        item(&mut items, owner, "unknown", &cav.subject, &format!("{} is unknown and required", cav.subject), &format!("rb spec set {} {name} <value> --source <file#key>{amend}", e.id), vec![]);
                    }
                    "unknown_optional" => {
                        item(&mut items, owner, "unknown_optional", &cav.subject, &format!("{} is unknown (optional)", cav.subject), &format!("rb spec set {} {name} <value> --source <file#key>{amend}", e.id), vec![]);
                    }
                    "provisional" => {
                        if s_.map_or(false, |s| s.failed.as_ref().map_or(false, |f| !f.is_empty())) {
                            item(
                                &mut items,
                                owner,
                                "provisional",
                                &cav.subject,
                                &told,
                                &format!("rb spec set {} {name} <the value> --source <where it is stated>{amend}", e.id),
                                vec![],
                            );
                        } else if s_.map_or(false, |s| s.source.as_ref().map_or(false, |x| x.checkable())) {
                            item(&mut items, "agent", "provisional", &cav.subject, &told, &format!("rb spec verify {} {name}", e.id), vec![]);
                        } else {
                            let value = match s_ {
                                Some(s) if !s.value.is_null() => crate::cli::shlex_quote(&show(&s.value)),
                                _ => "<value>".into(),
                            };
                            item(
                                &mut items,
                                owner,
                                "provisional",
                                &cav.subject,
                                &told,
                                &format!(
                                    "rb spec set {} {name} {value} --source <file#key that states it>{amend}   (or vouch for it: rb decide {}/{name} accept --why \"...\")",
                                    e.id, e.id
                                ),
                                vec![],
                            );
                        }
                    }
                    "stale" | "conflict" => {
                        gate.insert("stale".into(), true);
                        // the source as it is kept, so the command records it again; gone stale, what held the value is the user's to give
                        let where_ = s_.and_then(|s| s.source.as_ref()).map(|src| format!(" {}", src.args(cav.code == "stale"))).unwrap_or_default();
                        let fix = if cav.code == "stale" {
                            format!("rb spec set {} {name} <the value it states now>{where_}{amend}   (or point --source at where the value is stated)", e.id)
                        } else {
                            format!("rb spec set {} {name} <the value the source states>{where_}{amend}", e.id)
                        };
                        item(&mut items, owner, &cav.code, &cav.subject, &told, &fix, vec![]);
                    }
                    "source_changed" => {
                        item(&mut items, "agent", &cav.code, &cav.subject, &told, &format!("rb spec verify {} {name}", e.id), vec![]);
                    }
                    "confound" => {
                        let fix = if e.frozen.is_some() {
                            format!("rb spec vary {} {name} --amend --why \"...\"   (it differs on purpose) or rb decide {} accept --why \"...\"   (it does not matter)", e.id, cav.subject)
                        } else {
                            format!("rb spec vary {} {name}   (it differs on purpose), or set it the same in every variant", e.id)
                        };
                        item(&mut items, owner, &cav.code, &cav.subject, &cav.text, &fix, vec![]);
                    }
                    "inherited_without_parent" | "rejected_setting" => {
                        item(&mut items, owner, &cav.code, &cav.subject, &cav.text, &format!("rb spec set {} {name} <value> --source <file#key>{amend}", e.id), vec![]);
                    }
                    _ => {}
                }
            }
            let mine: Vec<Criterion> = criteria.iter().filter(|c| c.experiment == e.id).cloned().collect();
            let my_runs: Vec<Run> = runs.iter().filter(|r| r.experiment == e.id).cloned().collect();
            // a criterion declared from these commands names the question its finding bears on
            let q_arg = self.question_arg(e);
            let unread_m = self.unread_metrics(e, &my_runs, &mine)?;
            if !unread_m.is_empty() {
                // said as unread, with no rule to declare: a threshold or a direction is a file's or the user's to state
                item(&mut items, owner, "unread_metric", &e.id, &format!("{}: runs report {}, which no recorded criterion reads", e.id, assess::first_few(&unread_m, 6, ", ")), "", vec![]);
            }
            let unread_c = self.unread_comparisons(e, &mine)?;
            if !unread_c.is_empty() {
                // as unread numbers are: said, with no rule to declare
                item(
                    &mut items,
                    owner,
                    "unread_comparison",
                    &e.id,
                    &format!(
                        "{}: {}: no recorded criterion reads {} (one declared now reads as declared after its evidence)",
                        e.id,
                        assess::first_few(&unread_c.iter().map(|x| x.get_str("text").unwrap_or("").to_string()).collect::<Vec<_>>(), 3, "; "),
                        if unread_c.len() == 1 { "it" } else { "them" }
                    ),
                    "",
                    vec![],
                );
            }
            if mine.is_empty() && !runs.iter().any(|r| r.experiment == e.id) && !analyses.iter().any(|a| a.experiment == e.id) && self.experiment_state(e, None)? == "planned" {
                let do_ = if e.live_variants().is_empty() {
                    format!("rb variant add {} <name> --role baseline{amend}   (then a candidate, then rb criterion add ... -e {}{q_arg})", e.id, e.id)
                } else {
                    format!("rb criterion add \"<what it should show>\" -e {}{q_arg} --metric <name> --at-most <x>{amend}", e.id)
                };
                item(&mut items, owner, "planned", &e.id, &format!("nothing in the record tests {} yet: {}", e.id, if e.hypothesis.is_empty() { &e.title } else { &e.hypothesis }), &do_, vec![]);
            }
        }
        for q in &questions {
            if self.question_state(q)? == "open" && self.experiments_under(&q.id, true)?.is_empty() && self.children(&q.id)?.is_empty() && self.inquiry_criterion_ids(&q.id)?.is_empty() {
                item(
                    &mut items,
                    "agent",
                    "open_question",
                    &q.id,
                    &format!("{} is open and nothing is filed under it: {}", q.id, q.text),
                    &format!("rb experiment add \"<what would test it>\" -q {} --hypothesis \"<what you expect>\"", q.id),
                    vec![],
                );
            }
        }
        // a record with no question in a repository with work in it: the start protocol says how to record that work
        if experiment.is_none() && questions.is_empty() {
            if let Some(h) = crate::repo::history(&self.root) {
                item(
                    &mut items,
                    "agent",
                    "no_question",
                    &proj.id,
                    &format!("No question is recorded, and the repository holds work already done: {}", h.said()),
                    START,
                    vec![
                        ("commits", Value::Int(h.commits as i64)),
                        ("folders", Value::list_of_str(h.folders.clone())),
                        ("stores", Value::list_of_str(h.stores.clone())),
                    ],
                );
            }
        }
        // Once links are in use, a question's reading and the calls on it leave out a criterion filed under it and
        // linked to none; a record made before links existed is not asked to link every criterion it has.
        if self.inquiry_links_in_use()? {
            for q in &questions {
                let filed = self.experiments_under(&q.id, false)?;
                for c in criteria.iter().filter(|c| super::inquiries::unassigned_here(c, &filed)) {
                    // its filing is refused while the criterion, its experiment or the question reads as edited outside rb,
                    // so edited_outside_rb gives the repair that comes first
                    if self.edited_outside("criterion", &c.id)?.is_some()
                        || self.edited_outside("question", &q.id)?.is_some()
                        || self.edited_outside("experiment", &c.experiment)?.is_some()
                    {
                        continue;
                    }
                    let amend = if self.load::<Experiment>(&c.experiment)?.frozen.is_some() { " --amend" } else { "" };
                    item(&mut items, "agent", "unassigned_criterion", &c.id,
                        &format!("{} is filed under {} through {} and linked to no question, so {}'s reading leaves it out: {}", c.id, q.id, c.experiment, q.id, c.statement),
                        &format!("rb criterion file {} {} --why \"<why it bears on this question>\"{amend}", c.id, q.id),
                        vec![("question", Value::Str(q.id.clone()))]);
                }
            }
        }
        // the gates a session set, each waiting with its design for one rb confirm <experiment>@<hash>, as rb confirm
        // lists them
        let designs = self.designs()?;
        let gates_pending: Vec<Value> = self
            .pending_gates()?
            .iter()
            .filter(|e| experiment.map_or(true, |x| x.is_empty() || x == e.id))
            .map(|e| {
                let words = gate_words.get(&e.id).map(|(w, _)| w.clone()).unwrap_or_default();
                let confirm = match designs.get(&e.id).filter(|d| d.pending()) {
                    Some(d) => d.confirm(),
                    None => crate::view::gate_confirm(&e.id, &e.if_holds, &e.if_not, &words),
                };
                crate::obj! {"experiment" => e.id.clone(), "if_holds" => e.if_holds.clone(), "if_not" => e.if_not.clone(), "words" => words, "confirm" => confirm}
            })
            .collect();
        // each design that waits for the user's one confirmation, with its gate: what rb confirm lists, and its command
        let mut designs_pending: Vec<Value> = Vec::new();
        for (e, d) in self.pending_designs()? {
            if experiment.map_or(false, |x| !x.is_empty() && x != e.id) {
                continue;
            }
            designs_pending.push(crate::obj! {
                "experiment" => e.id.clone(), "title" => e.title.clone(), "standing" => d.state, "key" => d.key.clone(), "confirm" => d.confirm(),
                "changes" => Value::List(d.changes.iter().map(|c| c.to_value()).collect()), "lines" => Value::list_of_str(self.design_said(&e, &d)?)
            });
        }
        // a call recorded in an agent session waits for one confirmation in the user's own terminal; it sets no gate
        let mut pending_rows: Vec<Value> = Vec::new();
        for (d, a) in self.pending_calls()? {
            if let Some(e) = experiment {
                let theirs = d.subject == e || d.subject.starts_with(&format!("{e}/")) || criteria.iter().any(|c| c.id == d.subject) || questions.iter().any(|q| q.id == d.subject);
                if !theirs {
                    continue;
                }
            }
            let after = self.runs_after_past_call(&d)?;
            let when = d.past.as_ref().map(|p| p.when.clone()).unwrap_or_default();
            let what = pending_said(&d, &a) + &runs_after_said(&d.subject, &when, &after);
            let (confirm, id) = (format!("rb confirm {}", d.id), vec![("decision", Value::Str(d.id.clone()))]);
            if d.past.is_some() {
                item(&mut items, "human", "past_call_unconfirmed", &d.subject, &what, &confirm, id);
            } else {
                item(&mut items, "human", "call_proposed", &d.subject, &what, &confirm, id);
            }
            let mut row = d.dump(false);
            row.insert("authority".into(), a.to_value());
            row.insert("confirm".into(), Value::Str(format!("rb confirm {}", d.id)));
            row.insert("witness".into(), super::support::decision_witness(&d).to_value());
            if !after.is_empty() {
                row.insert("runs_after".into(), Value::list_of_str(after));
            }
            pending_rows.push(Value::Dict(row));
        }
        let repository = self.repository_items(&experiments, &mut items, &mut gate, experiment.is_none())?;
        let unread = self.newer()?;
        if !unread.is_empty() {
            for v in gate.values_mut() {
                *v = true;
            }
            let kinds: BTreeSet<String> = unread.iter().map(|u| u.get("kind").map(pyjson::py_str).unwrap_or_default()).collect();
            item(
                &mut items,
                "anyone",
                "state_newer",
                &kinds.into_iter().collect::<Vec<_>>().join(", "),
                &newer_said(&unread),
                "pip install --upgrade rabbit-brain",
                vec![],
            );
        }
        let unestablished = UNESTABLISHED.iter().any(|g| gate.get(*g).copied().unwrap_or(false));
        gate.insert("unestablished".into(), unestablished);
        // items of one kind that ask for the same command are one item; each kind keeps its own code and words
        let mut merged: IndexMap<(String, String), Dict> = IndexMap::new();
        // each merged item's subject and own words, in order
        let mut said: IndexMap<(String, String), Vec<(String, String)>> = IndexMap::new();
        for (n, it) in items.into_iter().enumerate() {
            let code = it.get_str("code").unwrap_or("").to_string();
            let key = (it.get_str("who").unwrap_or("").to_string(), format!("{code}\u{0}{}", it.get_str("do").unwrap_or("")));
            let subj = it.get_str("subject").unwrap_or("").to_string();
            // an item that asks for nothing is its own
            if it.get_str("do").unwrap_or("").is_empty() {
                merged.insert((key.0, format!("{code}\u{0}{subj}")), it);
                continue;
            }
            // and so is one with fields of its own (one group's folders, a link's question), which a merge would lose
            if it.keys().any(|k| !["who", "code", "subject", "what", "do"].contains(&k.as_str())) {
                merged.insert((key.0, format!("{code}\u{1}{n}")), it);
                continue;
            }
            if let Some(first) = merged.get_mut(&key) {
                let fs = first.get_str("subject").unwrap_or("").to_string();
                if !fs.split(", ").any(|x| x == subj) {
                    first.insert("subject".into(), Value::Str(format!("{fs}, {subj}")));
                    said.entry(key.clone()).or_default().push((subj, it.get_str("what").unwrap_or("").to_string()));
                }
                continue;
            }
            said.insert(key.clone(), vec![(subj, it.get_str("what").unwrap_or("").to_string())]);
            merged.insert(key, it);
        }
        // a merged item says its subjects once where each one's words after its subject are the same, else each
        // item's own words in turn: one gate's comparison never reads as another's
        for (key, each) in said.into_iter().filter(|(_, e)| e.len() > 1) {
            let tails: Vec<Option<&str>> = each.iter().map(|(s, w)| w.strip_prefix(s.as_str())).collect();
            let what = match tails[0] {
                Some(t) if tails.iter().all(|x| *x == Some(t)) => format!("{}{t}", each.iter().map(|(s, _)| s.as_str()).collect::<Vec<_>>().join(", ")),
                _ => each.iter().map(|(_, w)| w.as_str()).collect::<Vec<_>>().join("; "),
            };
            if let Some(it) = merged.get_mut(&key) {
                it.insert("what".into(), Value::Str(what));
            }
        }
        let mut items: Vec<Dict> = merged.into_values().collect();
        let rank = |w: &str| match w {
            "human" => 0,
            "agent" => 1,
            _ => 2,
        };
        items.sort_by_key(|i| rank(i.get_str("who").unwrap_or("")));
        let mut exp_rows: Vec<Value> = Vec::new();
        for e in &experiments {
            let caveats = self.setting_caveats(e, self.has_origin(e)?, None)?;
            let mut counts: IndexMap<String, i64> = ["verified", "provisional", "unknown", "inherited", "per_run"].iter().map(|k| (k.to_string(), 0)).collect();
            for (name, s_) in e.all_settings() {
                // a setting whose source states another value or no longer states it, on record or as this read finds
                // it, counts under that word; conflict and stale are keyed only when some are
                let now = source_problem(&caveats, e, &name);
                let k = if s_.per_run {
                    "per_run".to_string()
                } else {
                    match now {
                        Some(c) => c.code.clone(),
                        None => self.status_read(e, &name, s_)?,
                    }
                };
                *counts.entry(k).or_insert(0) += 1;
            }
            let allr: Vec<Run> = self.all::<Run>()?.iter().filter(|x| x.experiment == e.id).cloned().collect();
            let standing: Vec<Run> = allr.iter().filter(|x| x.retracted.as_ref().map_or(true, |r| !r.scope.is_empty())).cloned().collect();
            let blocking: Vec<String> = caveats
                .into_iter()
                .filter(|c| c.problem && c.code == "unknown")
                .map(|c| c.subject.split_once('/').map(|(_, t)| t.to_string()).unwrap_or_default())
                .collect();
            let mut row = crate::obj! {
                "id" => e.id.clone(), "uid" => e.uid.clone(), "title" => e.title.clone(), "questions" => Value::list_of_str(e.questions.clone()),
                "question" => e.question().map(|s| s.to_string()), "hypothesis" => e.hypothesis.clone(), "why" => e.why.clone(), "purpose" => e.purpose.clone(),
                "if_holds" => e.if_holds.clone(), "if_not" => e.if_not.clone(), "varies" => Value::list_of_str(e.varies.clone()),
                "variants" => Value::List(e.live_variants().iter().map(|v| crate::obj! {"name" => v.name.clone(), "role" => v.role.clone()}).collect()),
                "frozen" => e.frozen.as_ref().map(|f| Value::Dict(f.dump(false))), "amendment_count" => e.amendments.len(),
                "spec_drift" => self.spec_drift(e)?, "settings_count" => Value::Dict(counts.into_iter().map(|(k, v)| (k, Value::Int(v))).collect()),
                "state" => self.experiment_state(e, Some(&standing))?,
                "notes" => notes_value(&e.notes, &note_words),
                "relations" => Value::List(e.relations.iter().map(|r| Value::Dict(r.dump(false))).collect()),
                "blocking" => Value::list_of_str(blocking), "run_count" => standing.len(), "retracted_count" => allr.len() - standing.len(),
                "analysis_count" => analyses.iter().filter(|a| a.experiment == e.id && a.retracted.is_none()).count()
            };
            // the words given with its gate, when there were any, as `words` is on a note
            if let (Some((w, by)), Value::Dict(d)) = (gate_words.get(&e.id).filter(|_| !e.if_holds.is_empty() || !e.if_not.is_empty()), &mut row) {
                if !w.is_empty() {
                    d.insert("gate_words".into(), Value::Str(w.clone()));
                }
                // who set it, or the user once their confirmation covers it
                d.insert("gate_by".into(), Value::Str(designs.get(&e.id).map_or_else(|| by.clone(), |design| design.writer(by, "gate"))));
            }
            // its design's standing, computed: whether it waits for the user's confirmation, and what changed since
            if let (Some(design), Value::Dict(d)) = (designs.get(&e.id), &mut row) {
                d.insert("design".into(), design.to_value());
            }
            exp_rows.push(row);
        }
        self.support_items(&mut items, &runs, &analyses)?;
        // one row is one run: a row read as a run in more than one experiment counts in each
        for (place, same) in rows_in_several(&runs) {
            let each: Vec<String> = same.iter().map(|r| format!("{} in {}", r.id, r.experiment)).collect();
            let mut exps: Vec<String> = Vec::new();
            for r in &same {
                if !exps.contains(&r.experiment) {
                    exps.push(r.experiment.clone());
                }
            }
            item(&mut items, "agent", "row_in_several_experiments", &same[same.len() - 1].id,
                &format!("{} were read from one row ({place}): one row is one run, and it counts in {}", crate::assess::first_few(&each, 4, ", "), exps.join(" and ")), "",
                vec![("runs", Value::list_of_str(same.iter().map(|r| r.id.clone()))), ("experiments", Value::list_of_str(exps))]);
        }
        let mut superseded_by: IndexMap<String, String> = IndexMap::new();
        for a in self.live::<Analysis>()? {
            for old in &a.supersedes {
                superseded_by.insert(old.clone(), a.id.clone());
            }
        }
        let counts = |m: IndexMap<String, i64>| Value::Dict(m.into_iter().map(|(k, v)| (k, Value::Int(v))).collect());
        let mut q_rows = Vec::new();
        let exps_v: Vec<Experiment> = experiments.clone();
        for q in &questions {
            let mut d = q.dump(false);
            d.insert("state".into(), Value::Str(self.question_state(q)?));
            d.insert("rollup".into(), Value::Dict(self.rollup(q, &findings, &exps_v)?));
            d.insert("inquiry".into(), Value::Dict(self.inquiry(&q.id, &findings)?));
            q_rows.push(Value::Dict(d));
        }
        let mut res_rows = Vec::new();
        for res in &resources {
            let mut d = res.dump(false);
            d.insert("availability".into(), Value::Dict(self.availability(res)));
            res_rows.push(Value::Dict(d));
        }
        let mut dec_rows = Vec::new();
        for d in &decisions {
            let mut dd = d.dump(false);
            dd.insert("current".into(), Value::Bool(self.decision_current(d)?.0));
            dd.insert("witness".into(), super::support::decision_witness(d).to_value());
            if let Some(a) = auth.get(&d.id) {
                dd.insert("authority".into(), a.to_value());
            }
            let after = self.runs_after_past_call(d)?;
            if !after.is_empty() {
                dd.insert("runs_after".into(), Value::list_of_str(after));
            }
            dec_rows.push(Value::Dict(dd));
        }
        let runs_v: Vec<Value> = runs
            .iter()
            .map(|x| {
                let mut ms: Vec<&String> = x.metrics.keys().collect();
                ms.sort();
                crate::obj! {
                    "id" => x.id.clone(), "uid" => x.uid.clone(), "experiment_id" => x.experiment.clone(), "basis" => x.basis.clone(),
                    "metrics" => Value::list_of_str(ms.into_iter().cloned()), "per_run" => Value::Dict(x.per_run.clone()), "synthetic" => x.synthetic,
                    "recorded" => x.created_at.clone(), "produced_at" => x.produced_at.clone(), "produced_basis" => x.produced_basis.clone(),
                    "repeats" => x.repeats.clone(), "by" => x.created_by.clone(), "session" => x.receipt.session.clone(),
                    "inputs" => Value::List(x.inputs.iter().map(|u| Value::Dict(u.dump(false))).collect()),
                    "outputs" => Value::List(x.outputs.iter().map(|u| Value::Dict(u.dump(false))).collect()),
                    "superseded_for" => Value::list_of_str(x.retracted.as_ref().map(|r| r.scope.clone()).unwrap_or_default()),
                    "command" => super::support::run_command(x).map(|g| g.to_value()), "made_before_recorded" => made_before_recorded(x),
                    "file_written_after" => super::support::written_after(x), "file_written" => super::support::file_written(x),
                    "witness" => super::support::run_witness(x).to_value()
                }
            })
            .collect();
        let analyses_v: Vec<Value> = analyses
            .iter()
            .map(|a| {
                crate::obj! {
                    "id" => a.id.clone(), "uid" => a.uid.clone(), "experiment_id" => a.experiment.clone(), "title" => a.title.clone(), "basis" => a.basis.clone(),
                    "method" => a.method.clone(), "unit" => a.unit.clone(), "units" => a.units, "reads" => Value::list_of_str(a.reads.clone()),
                    "supersedes" => Value::list_of_str(a.supersedes.clone()), "why" => a.why.clone(), "superseded_by" => superseded_by.get(&a.id).cloned(),
                    "retracted" => a.retracted.as_ref().map(|r| Value::Dict(r.dump(false))), "recorded" => a.created_at.clone(), "by" => a.created_by.clone(),
                    "comparisons" => Value::List(a.comparisons.iter().map(|c| { let mut d = c.dump(false); d.shift_remove("source"); Value::Dict(d) }).collect()),
                    "command" => super::support::analysis_command(a).map(|g| g.to_value()),
                    "witness" => super::support::analysis_witness(a).to_value()
                }
            })
            .collect();
        let retracted_v: Vec<Value> = retracted
            .iter()
            .map(|c| {
                let r = c.retracted.as_ref().unwrap();
                crate::obj! {"id" => c.id.clone(), "experiment_id" => c.experiment.clone(), "role" => c.role.clone(), "statement" => c.statement.clone(),
                "by" => r.by.clone(), "at" => r.at.clone(), "why" => r.why.clone(), "words" => r.words.clone()}
            })
            .collect();
        let metrics_v: Vec<Value> = self.live::<Metric>()?.iter().map(|m| Value::Dict(m.dump(false))).collect();
        let mut st = crate::dict! {
            "project" => {
                let mut d = proj.dump(false);
                d.insert("notes".into(), notes_value(&proj.notes, &note_words));
                Value::Dict(d)
            },
            "root" => crate::sys::slashed(&self.root),
            "actor" => {
                let mut a = crate::dict! {"id" => who.id.clone(), "via" => who.via.clone()};
                if let Some(agent) = self.agent_facts(&who, true) {
                    a.insert("agent".into(), Value::Dict(agent));
                }
                Value::Dict(a)
            },
            "experiment_id" => experiment,
            // the record-start line's facts, while runs it holds were made before it began
            "record_start" => self.record_start()?.map(|s| s.to_value()),
            // how rb knows of what the record holds: seen as it ran, or recorded afterwards (WITNESS)
            "witness" => self.witness()?,
            "counts" => crate::obj! {
                "question_count" => questions.len(), "experiment_count" => experiments.len(), "criterion_count" => criteria.len(), "run_count" => runs.len(),
                "analysis_count" => analyses.iter().filter(|a| a.retracted.is_none()).count(), "resource_count" => resources.len(), "decision_count" => decisions.len(),
                "pending_count" => pending_rows.len(), "design_pending_count" => designs_pending.len()
            },
            "tally" => counts(tally), "results" => counts(results),
            "findings" => {
                let mut rows = Vec::new();
                for f in findings.values() {
                    let mut d = f.dump(false);
                    d.insert("witness".into(), self.finding_witness(f)?);
                    rows.push(Value::Dict(d));
                }
                Value::List(rows)
            },
            "experiments" => Value::List(exp_rows), "runs" => Value::List(runs_v), "analyses" => Value::List(analyses_v),
            "questions" => Value::List(q_rows), "resources" => Value::List(res_rows), "decisions" => Value::List(dec_rows), "pending" => Value::List(pending_rows),
            "gates_pending" => Value::List(gates_pending), "designs_pending" => Value::List(designs_pending),
            "retracted" => Value::List(retracted_v), "metrics" => Value::List(metrics_v), "repository" => Value::Dict(repository),
            "open" => Value::List(items.into_iter().map(Value::Dict).collect()),
            "gate" => Value::Dict(gate.into_iter().map(|(k, v)| (k, Value::Bool(v))).collect())
        };
        // a linked worktree nested in the record's checkout, with no record of its own: the checkout it ran in
        if let Some(c) = crate::elsewhere::checkout_value(self) {
            st.insert("checkout".into(), c);
        }
        Ok(st)
    }
}

/// Cells in the order their values run: numbers by size, then words, an unset value last.
fn cell_order(a: &[Value], b: &[Value]) -> std::cmp::Ordering {
    for (x, y) in a.iter().zip(b) {
        let rank = |v: &Value| match v {
            Value::Null => 2,
            v if v.is_number() => 0,
            _ => 1,
        };
        let o = rank(x).cmp(&rank(y)).then_with(|| match (x.as_f64(), y.as_f64()) {
            (Some(p), Some(q)) if rank(x) == 0 => p.partial_cmp(&q).unwrap_or(std::cmp::Ordering::Equal),
            _ => show(x).cmp(&show(y)),
        });
        if o != std::cmp::Ordering::Equal {
            return o;
        }
    }
    std::cmp::Ordering::Equal
}

// ---------------------------------------------------------------- calls, as a screen offers them (BP 1a-2)

/// The calls an item of rb status's list asks the user for, by its code: what its command offers, first to last.
pub(crate) const UNDECIDED_ACTIONS: &[&str] = &["accept", "reject", "investigate"];
pub(crate) const GATE_ACTIONS: &[&str] = &["park", "continue", "stop"];
pub(crate) const INVESTIGATING_ACTIONS: &[&str] = &["accept", "reject"];
/// A call that moved a line on while its own audit or check says failed: look, or set the line again.
pub(crate) const FAILED_CHECK_ACTIONS: &[&str] = &["investigate", "park", "stop", "continue"];
/// A primary or gate criterion a session took back: say it was seen.
pub(crate) const SEEN_ACTIONS: &[&str] = &["accept"];

/// The slots a call's command leaves for the user to fill, as a screen shows them.
pub const WHY_SLOT: &str = "‹why›";
pub const REVISIT_SLOT: &str = "‹when to look again›";
pub const DAY_SLOT: &str = "‹day›";

/// rb status's command for a call on `on`: each action it offers, and for a park what it asks of the revisit.
pub(crate) fn decide_do(on: &str, actions: &[&str]) -> String {
    let revisit = if actions.first() == Some(&"park") { " --revisit \"<when to look again>\"" } else { "" };
    format!("rb decide {on} {} --why \"...\"{revisit}", actions.join("|"))
}

/// One way to make a call, as a screen offers it: the action, its label, the command as argv and as one line, and what
/// the call reads as once made (rb's call line, with the day and the reason left as slots and no writer).
#[derive(Clone, Debug, PartialEq)]
pub struct CallAction {
    pub action: String,
    pub label: String,
    pub argv: Vec<String>,
    pub line: String,
    pub consequence: String,
}

impl CallAction {
    pub fn of(on: &str, action: &str) -> CallAction {
        let mut argv: Vec<String> = ["rb", "decide", on, action, "--why", WHY_SLOT].iter().map(|s| s.to_string()).collect();
        if action == "park" {
            argv.extend(["--revisit".to_string(), REVISIT_SLOT.to_string()]);
        }
        let mut label = action.to_string();
        label[..1].make_ascii_uppercase();
        CallAction { action: action.into(), label, line: crate::cli::shlex_join(&argv), argv, consequence: call_line(action, "", DAY_SLOT, WHY_SLOT, None, false) }
    }
}

/// The calls a call of `code` on `on` (of `kind`) offers, its command's first: for a call made on an earlier reading,
/// the call it revisits first, then the others of its kind.
pub fn call_actions(code: &str, kind: &str, on: &str, last: Option<&str>) -> Vec<CallAction> {
    let family = |a: &str| if DIRECTION_ACTIONS.contains(&a) { DIRECTION_ACTIONS } else { RULING_ACTIONS };
    let actions: Vec<&str> = match code {
        "undecided" => UNDECIDED_ACTIONS.to_vec(),
        "gate_missed" => GATE_ACTIONS.to_vec(),
        "investigating" => INVESTIGATING_ACTIONS.to_vec(),
        "call_on_failed_check" => FAILED_CHECK_ACTIONS.to_vec(),
        "retracted_by_agent" => SEEN_ACTIONS.to_vec(),
        "decision_outdated" if kind == "criterion" => UNDECIDED_ACTIONS.to_vec(),
        "decision_outdated" => match last {
            Some(l) => std::iter::once(l).chain(family(l).iter().copied().filter(|a| *a != l)).collect(),
            None => RULING_ACTIONS.to_vec(),
        },
        _ => Vec::new(),
    };
    actions.into_iter().map(|a| CallAction::of(on, a)).collect()
}

/// Why rb asks for a call, as its first line says it: a label, its qualifier, and what more the finding's notes say.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct CallReason {
    pub label: String,
    pub detail: String,
    pub also: String,
}

/// A screen's label of a rule word: its first letter up ("Rule not met", "Not reproduced").
fn up_first(s: &str) -> String {
    let mut c = s.chars();
    c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
}

/// Why rb asks for a call of `code` (SHELL-SPEC 15.5, one rule, first match wins): from the item's own words (`what`),
/// the finding of the criterion it is on, when it is on one, and the day of the call it revisits.
pub fn call_reason(code: &str, what: &str, on: Option<(&Criterion, &Finding)>) -> CallReason {
    let r = |label: &str, detail: String, also: String| CallReason { label: label.into(), detail, also };
    let after_colon = |s: &str| s.split_once(": ").map(|(_, t)| t.to_string()).unwrap_or_default();
    let review = on.and_then(|(_, f)| f.review.as_ref());
    match code {
        "decision_outdated" => {
            return match review.filter(|r| !r.was.is_empty()) {
                // a call made while there was no reading is made on none
                Some(rv) if rv.was == "no reading" => r("Made on an earlier reading", "made when there was no reading".into(), String::new()),
                Some(rv) => r("Made on an earlier reading", format!("was {}", rv.was), String::new()),
                // on an experiment or a question: the readings that moved, as rb names them
                None => r("Made on an earlier reading", what.split_once("moved since: ").map(|(_, t)| format!("moved since: {t}")).unwrap_or_default(), String::new()),
            };
        }
        "investigating" => return r("Investigating", review.map(|rv| format!("since {}", rv.at.chars().take(10).collect::<String>())).unwrap_or_default(), String::new()),
        "retracted_by_agent" => return r("Retracted", on.map(|(c, _)| format!("{} criterion", c.role)).unwrap_or_default(), String::new()),
        "call_on_failed_check" => return r("Check failed after the call", what.split_once(", and now ").map(|(_, t)| t.to_string()).unwrap_or_else(|| after_colon(what)), String::new()),
        "gate_missed" => return r("Gate missed", on.and_then(|(_, f)| f.says.get("comparison").cloned()).unwrap_or_default(), String::new()),
        _ => {}
    }
    let Some((c, f)) = on else { return CallReason::default() };
    let word = assess::rule_word(&f.rule_outcome, &c.role, &c.origin);
    // the screens' rule words (PLAIN-WORDS-PASS-3): a criterion is a rule there, and a state names its subject
    let said = rule_said(&assess::rule_label(&word, 1));
    let label = up_first(&said);
    let rule_detail = || rule_why(f);
    let caveat = |code: &str| f.caveats.iter().find(|x| x.code == code);
    let straddles = || if caveat("straddles").is_some() { super::findings::STRADDLES.to_string() } else { String::new() };
    if let Some(first) = f.integrity.first() {
        let phrase = crate::surface::reason(&first.code).map(str::to_string).unwrap_or_else(|| first.text.clone());
        let more = if f.integrity.len() > 1 { format!(" and {} more", f.integrity.len() - 1) } else { String::new() };
        return r("Integrity problem", phrase + &more, String::new());
    }
    // rb asks for a call on an undetermined rule only for a reason other than its units, interval or direction
    if f.rule_outcome == "undetermined" {
        return r("Rule: can't tell yet", rule_detail(), String::new());
    }
    if caveat("single_unit").is_some() {
        let noun = if f.units.runs == f.units.n { "run".to_string() } else { unit_noun(&f.units.unit, f.units.n) };
        return r("Too little evidence", format!("{} {noun} · {said}", f.units.n), String::new());
    }
    if let Some(b) = caveat("borderline") {
        return r(&label, b.text.split_once(':').map(|(h, _)| h.to_string()).unwrap_or_else(|| b.text.clone()), String::new());
    }
    r(&label, rule_detail(), straddles())
}

/// A rule word as the screens say it with its subject: "rule met", "rule not met", "rule: can't tell yet", "rule: not
/// tested"; any other (a gate's, a cited number's) as rb says it.
fn rule_said(label: &str) -> String {
    match crate::view::screen_rule_word(label).as_str() {
        w @ ("can't tell yet" | "not tested") => format!("rule: {w}"),
        w => w.to_string(),
    }
}

/// What rb's rule sentence says after its rule word (`says.rule` is "<rule word>: <why>", the headline "<rule word> ·
/// <result word>"), handed apart so no screen cuts rb's prose; "" where the rule says nothing more.
pub fn rule_why(f: &Finding) -> String {
    let word = f.says.get("headline").and_then(|h| h.split(" · ").next()).unwrap_or("");
    let rule = f.says.get("rule").map(String::as_str).unwrap_or("");
    rule.strip_prefix(&format!("{word}: ")).unwrap_or("").to_string()
}

/// A unit's plain noun for `n` of them, from rb's string table (SHELL-SPEC 12): config_id is a configuration, every
/// other unit as rb names it; "unit" where the reading names none.
pub fn unit_noun(unit: &str, n: i64) -> String {
    match unit {
        "" => assess::plural("unit", n),
        "config_id" => assess::plural("configuration", n),
        u => assess::plural(u, n),
    }
}

/// What a group of open items in the record is, as a screen heads it, by rb status's code: its heading, and a quiet
/// line under it where the counts need one. A code the table does not hold is headed as open in the record.
pub fn open_head(code: &str) -> (&'static str, &'static str) {
    let head = match code {
        "late" => "Rules written after their results or data were seen",
        "untested" => "Rules with no runs yet",
        "inherited" => "Inherited rules with no runs here yet",
        "too_few_units" => "Rules that need more runs",
        "no_interval" => "Rules rb can't judge yet: no interval yet",
        "direction_unknown" => "Metrics with no better direction recorded",
        "timing_unknown" => "Rules whose order with their results is unknown",
        "units" => "Analyses that count more units than their runs form",
        "arms" => "Roles that name more than one variant",
        "input_retracted" => "Readings that rest on something taken back",
        "input_superseded" => "Readings that rest on a superseded analysis",
        "cited_unchecked" => "Cited numbers rb did not check",
        "cited_source_changed" => "Cited sources changed since they were read",
        "analysis_source_changed" => "Analysis documents changed since they were read",
        "provisional" => "Settings no source confirms",
        "unknown" => "Required settings with no value yet",
        "unknown_optional" => "Optional settings with no value yet",
        "source_changed" => "Sources changed since they were read",
        "stale" => "Sources that no longer state their setting",
        "conflict" => "Sources that state another value",
        "confound" => "Settings that differ between arms, not declared",
        "inherited_without_parent" => "Inherited settings with nowhere they came from",
        "rejected_setting" => "Settings whose value was rejected",
        "varies_unchanged" => "Varied settings with one value in every variant",
        "unread_metric" => "Metrics the runs report that no rule reads",
        "unread_comparison" => "Comparisons the analyses report that no rule reads",
        "planned" => "Experiments nothing tests yet",
        "open_question" => "Questions with nothing filed under them",
        "no_question" => "No question recorded yet",
        "reindex" => "Results folders changed since they were indexed",
        "unindexed_folder" => "Results not yet in any experiment",
        "check_failed" => "Checks that failed on their last run",
        "audit_failed" => "Audit files that say they failed",
        "manifest_changed" => "Manifests whose sources changed since frozen",
        "question_link_outdated" => "Question links declared before the question was reworded",
        "question_link_unavailable" => "Question links to a question taken back or missing",
        "question_link_ambiguous" => "Question links a merge left declared twice",
        "finding_relation_invalid" => "Relations between rules that cannot be read",
        "relation_ambiguous" => "Relations a merge left declared twice",
        "relation_conflict" => "Relations declared both ways that disagree",
        "unassigned_criterion" => "Rules linked to no question",
        "unit_separates_arms" => "Units that separate the two arms",
        "experiment_retracted" => "Rules of an experiment taken back",
        "command_later" => "Commands given after the runs",
        "file_written_after" => "Result files written after their run",
        "file_run_time_unknown" => "Result files from a run whose time is unknown",
        "writer_words" => "Text that says who wrote it",
        "attributed_words" => "Text that names the user or the chat as its source",
        "row_in_several_experiments" => "Database rows read in more than one experiment",
        "drift" => "Locked experiments that no longer match their lock",
        "edited_outside_rb" => "Records changed outside rb",
        "merged_outside_rb" => "Records a merge changed",
        "shallow_clone" => "Records a shallow clone cannot account for",
        "id_collision" => "Ids two branches gave to two records",
        "state_newer" => "Records only a newer rb reads",
        _ => "Open in the record",
    };
    (head, "")
}

/// A unit's name for more than one: "seeds", "runs", "classes", "batches".
fn plural(unit: &str) -> String {
    if ["s", "x", "z", "ch", "sh"].iter().any(|e| unit.ends_with(e)) {
        format!("{unit}es")
    } else {
        format!("{unit}s")
    }
}

/// How many a grid's cell rests on: "3 seeds", "1 run", or "2 runs as 1 seed" where several runs make one unit.
fn count_said(units: usize, runs: usize, unit: &str) -> String {
    match units {
        1 if runs <= 1 => "1 run".into(),
        n if runs > n => format!("{runs} runs as {n} {}", if n == 1 { unit.to_string() } else { plural(unit) }),
        n => format!("{n} {}", plural(unit)),
    }
}
