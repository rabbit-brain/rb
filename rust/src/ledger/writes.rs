//! Every write to the record: questions, metrics, notes, relations, experiments, variants, settings, freezes,
//! criteria, runs, resources, analyses, decisions and retractions.

use super::helpers::*;
use super::*;
use crate::model::Loc;

/// What add_run and add_analysis return: the record, the one already here it repeats, and what to say about it.
#[derive(Debug, Clone)]
pub struct Added<T> {
    pub record: T,
    pub duplicate_of: Option<String>,
    pub warning: Option<String>,
}

/// What a past call is given with (rb decide --past): when it was made, as given, and the document that states it:
/// its path from the project root, the line given, and the words quoted from it.
#[derive(Debug, Clone, Default)]
pub struct PastGiven {
    pub when: String,
    pub source: Option<(String, Option<i64>, String)>,
}

#[derive(Debug, Clone, Default)]
pub struct NewExperiment {
    pub title: String,
    pub id: Option<String>,
    pub question: Option<String>,
    pub hypothesis: String,
    pub why: String,
    pub purpose: String,
    pub baseline: Option<String>,
    pub candidates: Vec<String>,
    pub varies: Vec<String>,
    pub like: Option<String>,
    pub questions: Vec<String>,
    pub if_holds: String,
    pub if_not: String,
}

#[derive(Debug, Clone)]
pub struct SetSetting {
    pub value: Value,
    pub unknown: bool,
    pub source: Option<Source>,
    pub required: Option<bool>,
    pub per_run: Option<bool>,
    pub design_factor: Option<bool>,
    pub cited: Option<Cited>,
    pub note: Option<String>,
    pub amend: Option<String>,
    pub verify: bool,
    /// the reason as given, kept on the log line
    pub why: String,
}

impl Default for SetSetting {
    fn default() -> Self {
        SetSetting { value: Value::Null, unknown: false, source: None, required: None, per_run: None, design_factor: None, cited: None, note: None, amend: None, verify: true, why: String::new() }
    }
}

#[derive(Debug, Clone)]
pub struct NewCriterion {
    pub statement: String,
    pub experiment: String,
    pub metric: String,
    pub comparator: String,
    pub target: Option<f64>,
    pub tolerance: Option<f64>,
    pub source: Option<Source>,
    pub over: String,
    pub min_n: Value,
    pub noise: Option<f64>,
    pub note: String,
    pub amend: Option<String>,
    pub id: Option<String>,
    pub subject: String,
    pub reference: String,
    pub estimand: String,
    pub r#where: Dict,
    pub subject_where: Dict,
    pub reference_where: Dict,
    pub margin: Option<f64>,
    pub role: String,
    pub questions: Vec<String>,
    pub when_met: String,
    pub why: String,
}

impl Default for NewCriterion {
    fn default() -> Self {
        NewCriterion {
            statement: String::new(),
            experiment: String::new(),
            metric: String::new(),
            comparator: String::new(),
            target: None,
            tolerance: None,
            source: None,
            over: "mean".into(),
            min_n: Value::Int(1),
            noise: None,
            note: String::new(),
            amend: None,
            id: None,
            subject: String::new(),
            reference: String::new(),
            estimand: String::new(),
            r#where: Dict::new(),
            subject_where: Dict::new(),
            reference_where: Dict::new(),
            margin: None,
            role: "primary".into(),
            questions: Vec::new(),
            when_met: String::new(),
            why: String::new(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct NewRun {
    pub metrics: IndexMap<String, Value>,
    pub variant: Option<String>,
    pub files: Vec<String>,
    pub links: IndexMap<String, String>,
    pub command: Option<String>,
    /// the log line that records the command's launch (--command-source <file>:<line>)
    pub command_source: Option<String>,
    pub per_run: Dict,
    pub config: Option<String>,
    pub commit: Option<String>,
    pub basis: String,
    pub again: Option<String>,
    pub note: String,
    pub inputs: Vec<(String, String)>,
    pub outputs: Vec<(String, String)>,
    pub host: IndexMap<String, String>,
    pub repeats: Option<String>,
    pub produced_at: Option<String>,
    pub replaces: Option<String>,
    pub why: String,
    pub tree: Option<String>,
    /// the files among `files` its numbers were read from, whose sightings say when it was made
    pub numbers_from: Vec<String>,
    /// the row of a SQLite database among `files` its numbers were read from
    pub row: Option<FromRow>,
}

/// The row of a SQLite database a run is read from (`rb run add --from <database> --table --where`): what the receipt
/// keeps of it, and what tells a second read of the same row from another row.
#[derive(Debug, Clone)]
pub struct FromRow {
    /// the database, as the run's files name it
    pub path: String,
    pub table: String,
    /// the columns the row was picked by, with the row's own values
    pub key: Dict,
    pub rowid: Option<i64>,
    /// each number's name as given (before a variant makes it <variant>.<name>), and the column it was read from
    pub columns: Vec<(String, String)>,
    /// each per-run setting read from a column, and the column
    pub per_run: Vec<(String, String)>,
    /// the column the produced time was read from
    pub produced_at: Option<String>,
    /// the row: its table, its rowid and every value it holds
    pub identity: Value,
    /// the database's size and time when it was read; a database that changed since is not recorded
    pub stamp: crate::sqlite::Stamp,
}

impl FromRow {
    /// Whether `run` was read from this row: the same database and table, and either the same key, or the same rowid
    /// with the key that run was read by still in it (a key it no longer has is another row, renumbered).
    pub fn is_read_by(&self, run: &Run) -> bool {
        let Some(was) = &run.receipt.row else { return false };
        if was.path != self.path || was.table != self.table {
            return false;
        }
        if Value::Dict(was.r#where.clone()) == Value::Dict(self.key.clone()) {
            return true;
        }
        let values = self.identity.get("values");
        let holds = |k: &String, v: &Value| match values.and_then(|vs| vs.get(k)) {
            Some(now) => now == v,
            None => ["rowid", "_rowid_", "oid"].iter().any(|r| r.eq_ignore_ascii_case(k)) && self.rowid.map(Value::Int).as_ref() == Some(v),
        };
        was.rowid.is_some() && was.rowid == self.rowid && was.r#where.iter().all(|(k, v)| holds(k, v))
    }
}

/// A row read again whose values differ from what its run recorded: refused, with what differs and the two ways on.
fn changed_row(run: &Run, row: &FromRow, values: &IndexMap<String, Value>, given: &Dict) -> RBError {
    let g = |x: f64| crate::pyfloat::g(x);
    let mut differs: Vec<String> = Vec::new();
    for (k, v) in values {
        match (run.metrics.get(k), v.as_f64()) {
            (Some(had), Some(now)) if *had == now => {}
            (Some(had), Some(now)) => differs.push(format!("{k} {} recorded, {} now", g(*had), g(now))),
            (None, Some(now)) => differs.push(format!("{k} not recorded, {} now", g(now))),
            _ => {}
        }
    }
    differs.extend(run.metrics.iter().filter(|(k, _)| !values.contains_key(*k)).map(|(k, had)| format!("{k} {} recorded, not read now", g(*had))));
    for k in run.per_run.keys().chain(given.keys().filter(|k| !run.per_run.contains_key(*k))) {
        let (was, now) = (run.per_run.get(k), given.get(k));
        if was != now {
            let said = |v: Option<&Value>| v.map(show).unwrap_or_else(|| "none".into());
            differs.push(format!("{k}={} recorded, {k}={} now", said(was), said(now)));
        }
    }
    let place = format!("{}, table {}{}", row.path, row.table, row.rowid.map(|r| format!(", rowid {r}")).unwrap_or_default());
    RBError::msg_fix(
        "E_OBJECT_INVALID",
        format!("{} was read from {place}, and the row gives other values now: {}. One row is one run.", run.id, differs.join("; ")),
        format!(
            "When {} was read before the row was final, replace it: --replaces {} --why \"...\". When another execution wrote the row again, --again --why \"...\".",
            run.id, run.id
        ),
    )
}

impl Default for NewRun {
    fn default() -> Self {
        NewRun {
            metrics: IndexMap::new(),
            variant: None,
            files: Vec::new(),
            links: IndexMap::new(),
            command: None,
            command_source: None,
            per_run: Dict::new(),
            config: None,
            commit: None,
            basis: "typed".into(),
            again: None,
            note: String::new(),
            inputs: Vec::new(),
            outputs: Vec::new(),
            host: IndexMap::new(),
            repeats: None,
            produced_at: None,
            replaces: None,
            why: String::new(),
            tree: None,
            numbers_from: Vec::new(),
            row: None,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct NewResource {
    pub id: String,
    pub r#type: String,
    pub name: String,
    pub version: String,
    pub subset: String,
    pub sha256: Option<String>,
    pub digest: String,
    pub source: String,
    pub url: Option<String>,
    pub recipe: Option<String>,
    pub locations: Vec<(String, String)>,
    pub from_path: Option<String>,
    pub note: String,
}

#[derive(Debug, Clone, Default)]
pub struct NewAnalysis {
    pub comparisons: Vec<Dict>,
    pub title: String,
    pub reads: Vec<String>,
    pub inputs: Vec<(String, String)>,
    pub files: Vec<String>,
    pub command: Option<String>,
    pub method: String,
    pub unit: String,
    pub units: Option<Value>,
    pub draws: Option<Value>,
    pub level: Option<Value>,
    pub source: Option<Source>,
    pub computed: bool,
    pub supersedes: Vec<String>,
    pub why: String,
    pub produced_at: Option<String>,
    pub note: String,
    pub id: Option<String>,
}

/// A row of `verify_settings`: what rb read, or why it could not.
pub type VerifyRow = Dict;

fn nested<T>(fields: Dict, validate: fn(&Value, &mut Loc, &mut Vec<VErr>) -> Option<T>) -> Res<T> {
    build_with(fields, validate)
}

fn ordered_unique(items: Vec<String>) -> Vec<String> {
    let mut seen = HashSet::new();
    items.into_iter().filter(|x| seen.insert(x.clone())).collect()
}

fn amend_given(amend: Option<&str>) -> bool {
    amend.map_or(false, |a| !a.is_empty())
}

fn truncate(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Whether a run was made before rb recorded it, by a sign of its own output: nobody knows when it was produced, or the
/// time given for it, or the earliest of the journal's first sighting, git's first commit and the last write of the
/// bytes its numbers were read from (produced.existed), is more than an hour before it was recorded (a file is written a
/// moment before rb reads it). Another file it names is no such sign, as a run may keep one it did not make.
pub fn made_before_recorded(r: &Run) -> bool {
    if r.produced_basis == "unknown" {
        return true;
    }
    let at = if r.receipt.at.is_empty() { &r.created_at } else { &r.receipt.at };
    let earlier = |made: Option<&str>| match (made.and_then(crate::pytime::parse_iso), crate::pytime::parse_iso(at)) {
        (Some(made), Some(recorded)) => recorded.signed_duration_since(made) > chrono::Duration::hours(1),
        _ => false,
    };
    // an earlier rb kept only the earliest sighting of any file a run named, which says nothing of its numbers
    (r.produced_basis == "reported" && earlier(r.produced_at.as_deref())) || earlier(r.receipt.produced.get("existed").and_then(|e| e.get_str("at")))
}

/// Whether an entry of a call's basis is a missed gate the call settles, kept by a question call beside its snapshot,
/// rather than evidence the call was made on.
pub fn settled_gate(d: &Decision, key: &str) -> bool {
    let Some(snapshot) = &d.inquiry_basis else { return false };
    !key.ends_with("#repository") && !snapshot.get("criteria").and_then(Value::as_dict).map_or(false, |c| c.contains_key(key))
}

fn merge(mut a: Dict, b: Dict) -> Dict {
    for (k, v) in b {
        a.insert(k, v);
    }
    a
}

fn str_list(v: &[String]) -> Value {
    Value::list_of_str(v.iter().cloned())
}

pub fn sha_of(v: &Value) -> String {
    sha(v)
}

impl Ledger {
    /// Retitle the work without changing its identity, evidence or calls.
    pub fn set_project_title(&self, title: &str, why: &str) -> Res<Project> {
        if title.trim().is_empty() {
            return Err(RBError::msg("E_OBJECT_INVALID", "The project's title must not be blank."));
        }
        if why.trim().is_empty() {
            return Err(RBError::msg("E_OBJECT_INVALID", "Retitling a project needs the reason: --why \"...\"."));
        }
        if why.chars().count() > TEXT {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("The reason must be at most {TEXT} characters.")));
        }
        let who = self.actor()?;
        self.locked(|| {
            let mut proj = self.project()?;
            self.check_write("project", &proj.id)?;
            if proj.title == title {
                return Ok(proj);
            }
            let before = proj.title.clone();
            proj.title = title.to_string();
            proj = nested(proj.dump(false), Project::validate)?;
            self.commit(&who, "title", Obj::Project(proj.clone()), crate::dict! {"before" => before, "title" => title, "why" => why})?;
            Ok(proj)
        })
    }

    // ------------------------------------------------------------ questions, metrics, notes, relations

    /// A question, at the top or under a parent question: the tree the work's scope is read on.
    pub fn add_question(&self, text: &str, id: Option<&str>, parent: Option<&str>) -> Res<Question> {
        let who = self.actor()?;
        self.locked(|| {
            self.require("question", parent)?;
            let q: Question = build(crate::dict! {
                "id" => self.new_id("question", id)?, "text" => text, "parent" => parent, "created_by" => who.id.clone(),
                "created_at" => now(), "via" => self.via.clone()
            })?;
            self.commit(&who, "add", Obj::Question(q.clone()), crate::dict! {"parent" => parent})?;
            Ok(q)
        })
    }

/// Reword a question: a revision, never an edit. The log keeps the wording before and after, who changed it and why
    /// (with the user's words when given); its id, its place in the tree, its experiments, evidence and calls stay. The
    /// same wording again writes nothing.
    pub fn reword_question(&self, question_id: &str, text: &str, why: &str) -> Res<Question> {
        if text.trim().is_empty() {
            return Err(RBError::msg("E_OBJECT_INVALID", "A question's wording must not be blank."));
        }
        if why.trim().is_empty() {
            return Err(RBError::msg("E_OBJECT_INVALID", "Rewording a question needs the reason: --why \"...\"."));
        }
        if why.chars().count() > TEXT {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("The reason must be at most {TEXT} characters.")));
        }
        let who = self.actor()?;
        self.locked(|| {
            let mut q: Question = self.load(question_id)?;
            self.check_write("question", &q.id)?;
            if q.retracted.is_some() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{} was retracted: a question taken back takes no new wording.", q.id)));
            }
            if q.text == text {
                return Ok(q);
            }
            let before = q.text.clone();
            q.text = text.to_string();
            q = nested(q.dump(false), Question::validate)?;
            self.commit(&who, "reword", Obj::Question(q.clone()), crate::dict! {"before" => before, "text" => text, "why" => why})?;
            Ok(q)
        })
    }

    /// Put a question under another one (or at the top).
    pub fn move_question(&self, question_id: &str, parent: Option<&str>, why: &str) -> Res<Question> {
        if why.chars().count() > TEXT {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("The reason must be at most {TEXT} characters.")));
        }
        let who = self.actor()?;
        self.locked(|| {
            let mut q: Question = self.load(question_id)?;
            self.require("question", parent)?;
            if let Some(p) = parent {
                let mut seen: HashSet<String> = HashSet::from([q.id.clone()]);
                let mut at: Option<String> = Some(p.to_string());
                while let Some(a) = at {
                    if seen.contains(&a) {
                        return Err(RBError::msg("E_OBJECT_INVALID", format!("{p} is under {}: a question cannot sit under its own sub-question.", q.id)));
                    }
                    seen.insert(a.clone());
                    at = self.load::<Question>(&a)?.parent;
                }
            }
            if q.parent.as_deref() == parent {
                let where_ = match parent.filter(|p| !p.is_empty()) {
                    Some(p) => format!("under {p}"),
                    None => "at the top".into(),
                };
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{} is already {where_}.", q.id)));
            }
            q.parent = parent.map(|s| s.to_string());
            let mut detail = crate::dict! {"parent" => parent};
            if !why.is_empty() { detail.insert("why".into(), Value::Str(why.into())); }
            self.commit(&who, "move", Obj::Question(q.clone()), detail)?;
            Ok(q)
        })
    }

    /// File an experiment under one more question (or take it out of one).
    pub fn file_under(&self, experiment_id: &str, question_id: &str, remove: bool) -> Res<Experiment> {
        let who = self.actor()?;
        self.locked(|| {
            let mut exp: Experiment = self.load(experiment_id)?;
            writable(&exp)?;
            self.require("question", Some(question_id))?;
            if remove {
                if !exp.questions.iter().any(|q| q == question_id) {
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("{} is not filed under {question_id}.", exp.id)));
                }
                exp.questions.retain(|q| q != question_id);
            } else {
                if exp.questions.iter().any(|q| q == question_id) {
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("{} is already filed under {question_id}.", exp.id)));
                }
                exp.questions.push(question_id.to_string());
            }
            self.commit(&who, if remove { "unfile" } else { "file" }, Obj::Experiment(exp.clone()), crate::dict! {"question" => question_id})?;
            Ok(exp)
        })
    }

    pub fn add_metric(&self, name: &str, unit: &str, direction: &str, aliases: &[String], description: &str) -> Res<Metric> {
        let who = self.actor()?;
        self.locked(|| {
            if self.kind_of(name)? == Some("metric") {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("Metric {} is already in the catalogue.", repr_str(name))));
            }
            let mut taken: IndexMap<String, String> = IndexMap::new();
            for m in self.live::<Metric>()? {
                taken.insert(m.id.clone(), m.id.clone());
                for a in &m.aliases {
                    taken.insert(a.clone(), m.id.clone());
                }
            }
            let mut all: Vec<&str> = vec![name];
            all.extend(aliases.iter().map(|s| s.as_str()));
            let clash: Vec<&str> = all.into_iter().filter(|a| taken.contains_key(*a)).collect();
            if !clash.is_empty() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{} already names metric {}.", clash.join(", "), taken[clash[0]])));
            }
            let m: Metric = build(crate::dict! {
                "id" => self.new_id("metric", Some(name))?, "unit" => unit, "direction" => direction, "aliases" => str_list(aliases),
                "description" => description, "created_by" => who.id.clone(), "created_at" => now(), "via" => self.via.clone()
            })?;
            self.commit(&who, "add", Obj::Metric(m.clone()), Dict::new())?;
            Ok(m)
        })
    }

    /// The project or the experiment that holds the note with this id, or None.
    pub fn note_holder(&self, id: &str) -> Res<Option<Obj>> {
        let has = |notes: &[Note]| notes.iter().any(|n| n.id.as_deref() == Some(id));
        let proj = self.project()?;
        if has(&proj.notes) {
            return Ok(Some(Obj::Project(proj)));
        }
        Ok(self.all::<Experiment>()?.iter().find(|e| has(&e.notes)).map(|e| Obj::Experiment(e.clone())))
    }

    /// Words about an experiment (or, with no experiment, the whole project).
    pub fn add_note(&self, experiment_id: Option<&str>, text: &str) -> Res<Note> {
        let who = self.actor()?;
        self.locked(|| {
            let note: Note = nested(crate::dict! {"id" => self.new_id("note", None)?, "text" => text, "by" => who.id.clone(), "at" => now(), "via" => self.via.clone()}, Note::validate)?;
            match experiment_id {
                None => {
                    let mut proj = self.project()?;
                    proj.notes.push(note.clone());
                    self.commit(&who, "note", Obj::Project(proj), crate::dict! {"text" => text})?;
                }
                Some(eid) => {
                    let mut exp: Experiment = self.load(eid)?;
                    writable(&exp)?;
                    exp.notes.push(note.clone());
                    self.commit(&who, "note", Obj::Experiment(exp), crate::dict! {"text" => text})?;
                }
            }
            Ok(note)
        })
    }

    /// Record how an experiment stands to another: it reproduces, extends, follows from, or compares with it.
    pub fn relate(&self, experiment_id: &str, kind: &str, target: &str, note: &str) -> Res<Relation> {
        let who = self.actor()?;
        self.locked(|| {
            let mut exp: Experiment = self.load(experiment_id)?;
            writable(&exp)?;
            if target == exp.id || target == exp.uid {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{} cannot {} itself.", exp.id, kind.replace('_', " "))));
            }
            if id_ok(target) && !target.starts_with("exp_") && !target.starts_with("http") {
                if let Some(k) = self.kind_of(target)? {
                    if k != "experiment" {
                        return Err(RBError::msg("E_OBJECT_INVALID", format!("{} is a {k}, not an experiment; relations are between experiments.", repr_str(target))));
                    }
                }
            }
            if exp.relations.iter().any(|r| r.kind == kind && r.target == target) {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{} already records that it {} {target}.", exp.id, kind.replace('_', " "))));
            }
            let rel: Relation = nested(crate::dict! {"kind" => kind, "target" => target, "note" => note, "by" => who.id.clone(), "at" => now()}, Relation::validate)?;
            exp.relations.push(rel.clone());
            self.commit(&who, "relate", Obj::Experiment(exp), crate::dict! {"relation" => kind, "target" => target})?;
            Ok(rel)
        })
    }

    // ------------------------------------------------------------ experiments, variants, settings

    pub fn add_experiment(&self, a: &NewExperiment) -> Res<Experiment> {
        let who = self.actor()?;
        self.tell_gate_is_words(Some(&a.if_holds), Some(&a.if_not));
        let cands = a.candidates.clone();
        let mut vary = a.varies.clone();
        let mut qs0: Vec<String> = Vec::new();
        if let Some(q) = a.question.as_ref().filter(|q| !q.is_empty()) {
            qs0.push(q.clone());
        }
        qs0.extend(a.questions.iter().cloned());
        let mut qs = ordered_unique(qs0);
        self.locked(|| {
            for q in &qs {
                self.require("question", Some(q))?;
            }
            for v in &vary {
                if !re_full(SETTING_PATTERN, v) {
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("--varies takes setting names (got {}).", repr_str(v))));
                }
            }
            let at = now();
            let mut variants: Vec<Variant> = Vec::new();
            let mut pairs: Vec<(String, &str)> = Vec::new();
            if let Some(b) = a.baseline.as_ref().filter(|b| !b.is_empty()) {
                pairs.push((b.clone(), "baseline"));
            }
            for c in &cands {
                pairs.push((c.clone(), "candidate"));
            }
            for (name, role) in pairs {
                variants.push(nested(
                    crate::dict! {"name" => name, "role" => role, "created_by" => who.id.clone(), "created_at" => at.clone(), "via" => self.via.clone()},
                    Variant::validate,
                )?);
            }
            let mut settings: Vec<Setting> = Vec::new();
            let mut copied: Vec<(&'static str, String)> = Vec::new();
            if let Some(like) = a.like.as_ref().filter(|l| !l.is_empty()) {
                let src: Experiment = self.load(like)?;
                copied = crate::plain::prose("experiment", &src.dump(false));
                for s in src.settings.iter().filter(|s| s.retracted.is_none()) {
                    let mut c = s.clone();
                    c.status = if s.per_run { "unknown".into() } else if !s.value.is_null() { "provisional".into() } else { "unknown".into() };
                    c.source = s.source.as_ref().map(|x| {
                        let mut y = x.clone();
                        y.resolved = None;
                        y
                    });
                    c.conflict = None;
                    c.set_by = who.id.clone();
                    c.set_at = at.clone();
                    c.via = self.via.clone();
                    settings.push(c);
                }
                if vary.is_empty() {
                    vary = src.varies.clone();
                }
                if qs.is_empty() {
                    qs = src.questions.clone();
                }
            }
            let e: Experiment = build(crate::dict! {
                "id" => self.new_id("experiment", a.id.as_deref())?, "uid" => new_uid("exp"), "title" => a.title.clone(), "questions" => str_list(&qs),
                "purpose" => a.purpose.clone(), "hypothesis" => a.hypothesis.clone(), "why" => a.why.clone(), "if_holds" => a.if_holds.clone(),
                "if_not" => a.if_not.clone(), "varies" => str_list(&vary),
                "variants" => Value::List(variants.iter().map(|v| v.to_value()).collect()),
                "settings" => Value::List(settings.iter().map(|s| s.to_value()).collect()),
                "created_by" => who.id.clone(), "created_at" => at.clone(), "via" => self.via.clone()
            })?;
            self.commit(
                &who,
                "add",
                Obj::Experiment(e.clone()),
                crate::dict! {"variants" => Value::list_of_str(variants.iter().map(|v| v.name.clone())), "like" => a.like.clone(), "questions" => str_list(&qs)},
            )?;
            crate::plain::copied(&copied);
            Ok(e)
        })
    }

    /// Keep what rb reconcile read from a results folder in the experiment that reads it.
    #[allow(clippy::too_many_arguments)]
    pub fn index_folder(
        &self,
        experiment_id: Option<&str>,
        id: &str,
        folder: &str,
        title: &str,
        files: Vec<RepoFile>,
        quotes: Vec<Quote>,
        checks: Vec<Check>,
        commit: Option<&str>,
    ) -> Res<Experiment> {
        let who = self.actor()?;
        self.locked(|| {
            let (mut exp, op) = match experiment_id {
                None => {
                    let t: String = title.chars().take(200).collect();
                    let e: Experiment = build(crate::dict! {
                        "id" => self.new_id("experiment", Some(id))?, "uid" => new_uid("exp"), "title" => if t.is_empty() { id.to_string() } else { t },
                        "created_by" => who.id.clone(), "created_at" => now(), "via" => self.via.clone()
                    })?;
                    (e, "reconcile")
                }
                Some(eid) => {
                    let e: Experiment = self.load(eid)?;
                    writable(&e)?;
                    (e, "reindex")
                }
            };
            let (nf, nq, nc) = (files.len(), quotes.len(), checks.len());
            exp.folder = folder.to_string();
            exp.files = files.clone();
            exp.quotes = quotes.clone();
            exp.checks = checks.clone();
            exp.indexed = Some(nested(crate::dict! {"by" => who.id.clone(), "at" => now(), "commit" => commit}, Indexed::validate)?);
            self.commit(&who, op, Obj::Experiment(exp.clone()), crate::dict! {"folder" => folder, "files" => nf, "quotes" => nq, "checks" => nc})?;
            Ok(exp)
        })
    }

    /// One log line for one rb reconcile --apply. It writes no record.
    pub fn note_reconcile(&self, summary: Dict) -> Res<()> {
        let who = self.actor()?;
        self.locked(|| {
            let pid = self.project()?.id;
            self.log(&who, "reconciled", "project", &pid, None, None, summary)
        })
    }

    /// A gate's branch is the action taken, never the sentence it was said in: a branch that holds a condition ("if ...")
    /// is told, in the names of the surface that wrote it.
    fn tell_gate_is_words(&self, if_holds: Option<&str>, if_not: Option<&str>) {
        static CONDITION: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
        let re = CONDITION.get_or_init(|| regex::Regex::new(r"(?i)\b(if|unless)\b").unwrap());
        let cli = self.via == "cli";
        let words = if cli { "--words" } else { "words" };
        for (flag, field, when, text) in [("--if-holds", "if_holds", "when it holds", if_holds), ("--if-not", "if_not", "when it does not", if_not)] {
            let name = if cli { flag } else { field };
            if text.map_or(false, |t| re.is_match(t)) {
                crate::plain::tell(format!(
                    "{name} holds a condition (\"if ...\"). {name} is the action, what is done {when}; the whole sentence it was said in goes in {words}."
                ));
            }
        }
    }

    /// The decision an experiment gates, set or revised in its author's words.
    pub fn set_gate(&self, experiment_id: &str, if_holds: Option<&str>, if_not: Option<&str>, why: &str) -> Res<Experiment> {
        if if_holds.is_none() && if_not.is_none() {
            return Err(RBError::msg("E_USAGE", "Say what happens either way: --if-holds \"...\", --if-not \"...\", or both."));
        }
        let who = self.actor()?;
        self.tell_gate_is_words(if_holds, if_not);
        self.locked(|| {
            let mut exp: Experiment = self.load(experiment_id)?;
            writable(&exp)?;
            let before = crate::dict! {"if_holds" => exp.if_holds.clone(), "if_not" => exp.if_not.clone()};
            if let Some(h) = if_holds {
                exp.if_holds = h.to_string();
            }
            if let Some(n) = if_not {
                exp.if_not = n.to_string();
            }
            if before.get_str("if_holds").unwrap_or("") != exp.if_holds || before.get_str("if_not").unwrap_or("") != exp.if_not {
                exp = build(exp.dump(false))?;
                self.commit(&who, "gate", Obj::Experiment(exp.clone()), crate::dict! {"before" => before, "why" => why})?;
            }
            Ok(exp)
        })
    }

    /// The repository's checks no single experiment names, kept on the project.
    pub fn index_project_checks(&self, checks: Vec<Check>, commit: Option<&str>) -> Res<Project> {
        let who = self.actor()?;
        self.locked(|| {
            let mut proj = self.project()?;
            let n = checks.len();
            proj.checks = checks.clone();
            proj.indexed = Some(nested(crate::dict! {"by" => who.id.clone(), "at" => now(), "commit" => commit}, Indexed::validate)?);
            self.commit(&who, "reindex", Obj::Project(proj.clone()), crate::dict! {"checks" => n})?;
            Ok(proj)
        })
    }

    pub fn add_variant(&self, experiment_id: &str, name: &str, role: &str, note: &str, amend: Option<&str>, why: &str) -> Res<Variant> {
        let who = self.actor()?;
        self.locked(|| {
            let mut exp: Experiment = self.load(experiment_id)?;
            writable(&exp)?;
            if !re_full(VARIANT_PATTERN, name) {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("Variant names are letters, digits, '_' and '-' (no '.'), up to 40 (got {}).", repr_str(name))));
            }
            if exp.variants.iter().any(|v| v.name == name) {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{} already has a variant {}.", exp.id, repr_str(name))));
            }
            if role == "baseline" && !exp.by_role("baseline").is_empty() {
                return Err(RBError::msg(
                    "E_OBJECT_INVALID",
                    format!("{} already has a baseline ({}); an experiment compares against one.", exp.id, exp.by_role("baseline")[0].name),
                ));
            }
            let before = self.freeze_sha(&exp, None)?;
            let v: Variant = nested(
                crate::dict! {"name" => name, "role" => role, "note" => note, "created_by" => who.id.clone(), "created_at" => now(), "via" => self.via.clone()},
                Variant::validate,
            )?;
            exp.variants.push(v.clone());
            let after = self.freeze_sha(&exp, None)?;
            let amended = self.guard(&mut exp, &before, &after, amend, &who, &format!("new variant {name} ({role})"))?;
            let mut detail = crate::dict! {"variant" => name, "role" => role};
            if !why.trim().is_empty() { detail.insert("why".into(), Value::Str(why.into())); }
            self.commit(&who, "add_variant", Obj::Experiment(exp), merge(detail, amended))?;
            Ok(v)
        })
    }

    /// Declare settings the variants differ in on purpose.
    pub fn declare_varies(&self, experiment_id: &str, names: &[String], amend: Option<&str>, why: &str) -> Res<(Experiment, Vec<String>)> {
        let who = self.actor()?;
        let names = ordered_unique(names.to_vec());
        for n in &names {
            if !re_full(SETTING_PATTERN, n) {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("rb spec vary takes setting names (got {}).", repr_str(n))));
            }
        }
        self.locked(|| {
            let mut exp: Experiment = self.load(experiment_id)?;
            writable(&exp)?;
            let new: Vec<String> = names.iter().filter(|n| !exp.varies.contains(n)).cloned().collect();
            if new.is_empty() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{} already varies {}.", exp.id, names.join(", "))));
            }
            let before = self.freeze_sha(&exp, None)?;
            exp.varies.extend(new.iter().cloned());
            let after = self.freeze_sha(&exp, None)?;
            let amended = self.guard(&mut exp, &before, &after, amend, &who, &format!("varies {} on purpose", new.join(", ")))?;
            let mut detail = crate::dict! {"varies" => str_list(&new)};
            if !why.trim().is_empty() { detail.insert("why".into(), Value::Str(why.into())); }
            self.commit(&who, "spec_vary", Obj::Experiment(exp.clone()), merge(detail, amended))?;
            Ok((exp, new))
        })
    }

    /// The settings runs are made under: every variant and its role, every setting's value and whether it is required.
    pub fn spec_sha(&self, exp: &Experiment) -> String {
        let mut variants: Vec<(String, String)> = exp.live_variants().iter().map(|v| (v.name.clone(), v.role.clone())).collect();
        variants.sort();
        let mut varies = exp.varies.clone();
        varies.sort();
        let mut settings: Vec<(String, String, bool, bool)> =
            exp.all_settings().into_iter().filter(|(n, _)| !n.starts_with("population.")).map(|(n, s)| (n, canonical(&s.value), s.required, s.per_run)).collect();
        settings.sort();
        sha(&crate::obj! {
            "variants" => Value::List(variants.into_iter().map(|(a, b)| crate::list![a, b]).collect()),
            "varies" => str_list(&varies),
            "settings" => Value::List(settings.into_iter().map(|(a, b, c, d)| crate::list![a, b, c, d]).collect())
        })
    }

    /// What freezing locks: the spec, where each setting's value comes from and what a cited source used, every
    /// criterion's rule, and the catalogue entries the criteria read.
    pub fn freeze_sha(&self, exp: &Experiment, criteria: Option<&[Criterion]>) -> Res<String> {
        let owned;
        let criteria: &[Criterion] = match criteria {
            Some(c) => c,
            None => {
                owned = self.criteria_of(&exp.id)?;
                &owned
            }
        };
        let live: Vec<&Criterion> = criteria.iter().filter(|c| c.retracted.is_none()).collect();
        let mut population: Vec<(String, String)> =
            exp.all_settings().into_iter().filter(|(n, _)| n.starts_with("population.")).map(|(n, s)| (n, canonical(&s.value))).collect();
        population.sort();
        let mut claims: Vec<(String, Value)> = live.iter().map(|c| (c.id.clone(), rule_key(c))).collect();
        claims.sort_by(|a, b| a.0.cmp(&b.0));
        let mut srcs: Vec<(String, Option<String>, Option<String>, Option<String>)> = exp
            .all_settings()
            .into_iter()
            .map(|(n, s)| {
                (
                    n,
                    s.source.as_ref().map(|x| x.label()),
                    s.cited.as_ref().map(|c| canonical(&c.value)),
                    s.cited.as_ref().and_then(|c| c.source.as_ref()).map(|x| x.label()),
                )
            })
            .collect();
        srcs.sort();
        let mut body = Dict::new();
        body.insert("spec".into(), Value::Str(self.spec_sha(exp)));
        let mut factors: Vec<String> = exp.all_settings().into_iter().filter(|(_, s)| s.design_factor == Some(true)).map(|(n, _)| n).collect();
        factors.sort();
        if !factors.is_empty() { body.insert("design_factors".into(), str_list(&factors)); }
        if !population.is_empty() {
            body.insert("population".into(), Value::List(population.into_iter().map(|(a, b)| crate::list![a, b]).collect()));
        }
        body.insert("claims".into(), Value::List(claims.into_iter().map(|(_, k)| k).collect()));
        body.insert(
            "sources".into(),
            Value::List(srcs.into_iter().map(|(a, b, c, d)| Value::List(vec![Value::Str(a), Value::from(b), Value::from(c), Value::from(d)])).collect()),
        );
        body.insert("catalogue".into(), self.catalogue_read_by(&live)?);
        Ok(sha(&Value::Dict(body)))
    }

    /// The catalogue entries whose names a criterion's metric could read, retracted ones included.
    fn catalogue_read_by(&self, criteria: &[&Criterion]) -> Res<Value> {
        let names: HashSet<&str> = criteria.iter().map(|c| last_part(&c.metric)).collect();
        let mut rows: Vec<(String, Value)> = Vec::new();
        for m in self.all::<Metric>()?.iter() {
            if names.contains(m.id.as_str()) || m.aliases.iter().any(|a| names.contains(a.as_str())) {
                let mut al = m.aliases.clone();
                al.sort();
                rows.push((m.id.clone(), crate::list![m.id.clone(), str_list(&al), m.direction.clone(), m.retracted.is_some()]));
            }
        }
        rows.sort_by(|a, b| a.0.cmp(&b.0));
        Ok(Value::List(rows.into_iter().map(|(_, v)| v).collect()))
    }

    /// The hash the freeze record says the experiment has: the last amendment's, else the freeze's.
    pub fn recorded_sha(&self, exp: &Experiment) -> Option<String> {
        let f = exp.frozen.as_ref()?;
        Some(match exp.amendments.last() {
            Some(a) => a.after.clone(),
            None => f.sha256.clone(),
        })
    }

    pub fn spec_drift(&self, exp: &Experiment) -> Res<Option<String>> {
        if exp.frozen.is_none() {
            return Ok(None);
        }
        let expected = self.recorded_sha(exp).unwrap_or_default();
        if self.freeze_sha(exp, None)? != expected {
            let short: String = expected.chars().take(12).collect();
            return Ok(Some(format!(
                "{} no longer matches its freeze record ({short}): its spec, a source, a cited value or a metric its criteria read changed without an amendment",
                exp.id
            )));
        }
        Ok(None)
    }

    pub fn settings_snapshot(&self, exp: &Experiment) -> Dict {
        let mut out = Dict::new();
        for (name, s) in exp.all_settings() {
            if !s.per_run && !name.starts_with("population.") {
                out.insert(name, s.value.clone());
            }
        }
        out
    }

    /// Each arm's own spec: its role, the settings it runs under (the shared ones, its own over them) with whether each
    /// is required or per run, and what the experiment varies on purpose. A comparison counts a run while the arms it
    /// compares are as they were when the run was recorded.
    pub fn arm_specs(&self, exp: &Experiment) -> IndexMap<String, String> {
        let mut shared: IndexMap<String, &Setting> = IndexMap::new();
        for s in exp.settings.iter().filter(|s| s.retracted.is_none() && !s.name.starts_with("population.")) {
            shared.insert(s.name.clone(), s);
        }
        let mut varies = exp.varies.clone();
        varies.sort();
        let spec = |role: &str, own: &[&Setting]| -> String {
            let mut all = shared.clone();
            for s in own {
                all.insert(s.name.clone(), s);
            }
            let mut settings: Vec<(String, String, bool, bool)> = all.iter().map(|(n, s)| (n.clone(), canonical(&s.value), s.required, s.per_run)).collect();
            settings.sort();
            sha(&crate::obj! {
                "role" => role, "varies" => str_list(&varies),
                "settings" => Value::List(settings.into_iter().map(|(a, b, c, d)| crate::list![a, b, c, d]).collect())
            })
        };
        let mut out = IndexMap::new();
        for v in exp.live_variants() {
            let own: Vec<&Setting> = v.settings.iter().filter(|s| s.retracted.is_none() && !s.name.starts_with("population.")).collect();
            out.insert(v.name.clone(), spec(&v.role, &own));
        }
        out
    }

    /// Whether a run was made under the spec a reading of these arms reads now: the whole spec unchanged, or, for a
    /// reading of named arms, each of them as it was (a run recorded before runs kept their arms' specs needs the whole
    /// spec).
    /// What a run already recorded under an earlier spec says when the same row, file or values are given again: how
    /// they are read again under the spec as it is now. None for a run under the spec as it is.
    pub fn earlier_spec(&self, r: &Run, exp: &Experiment) -> Option<String> {
        (self.spec_sha(exp) != r.spec_sha256).then(|| {
            format!("{} was recorded under an earlier spec: the same run add with --replaces {} --why \"...\" reads it again under the spec as it is now", r.id, r.id)
        })
    }

    pub fn same_spec(&self, r: &Run, spec_now: &str, arms: &[String], arms_now: &IndexMap<String, String>) -> bool {
        if r.spec_sha256 == spec_now {
            return true;
        }
        if arms.is_empty() || r.arm_specs.is_empty() {
            return false;
        }
        arms.iter().all(|a| r.arm_specs.get(a).is_some() && r.arm_specs.get(a) == arms_now.get(a))
    }

    /// After a change is applied in memory, before it is saved. Returns log detail for an amendment it recorded.
    pub fn guard(&self, exp: &mut Experiment, before: &str, after: &str, amend: Option<&str>, who: &Actor, change: &str) -> Res<Dict> {
        let changes_frozen = exp.frozen.is_some() && before != after;
        if !changes_frozen {
            if amend_given(amend) {
                let why = if exp.frozen.is_none() { "the experiment is not frozen" } else { "this does not change the frozen spec" };
                return Err(RBError::msg("E_OBJECT_INVALID", format!("--amend has nothing to amend: {why}. Run it without --amend.")));
            }
            return Ok(Dict::new());
        }
        let frozen = exp.frozen.as_ref().unwrap();
        if !amend_given(amend) {
            return Err(RBError::msg_fix(
                "E_FROZEN",
                format!("Experiment {} was frozen at {} ({}); this changes it ({change}).", exp.id, frozen.at, truncate(&frozen.sha256, 12)),
                "Give the change its reason: the same command with --amend --why \"<reason>\". The amendment is recorded, by whoever makes it.",
            ));
        }
        let amend = amend.unwrap();
        let recorded = self.recorded_sha(exp).filter(|s| !s.is_empty()).unwrap_or_else(|| before.to_string());
        let mut change = change.to_string();
        if before != recorded {
            change.push_str("; also adopts changes made without an amendment since the last record");
        }
        let a: Amendment = nested(
            crate::dict! {"at" => now(), "by" => who.id.clone(), "why" => amend, "change" => truncate(&change, 400), "before" => recorded.clone(),
            "after" => after, "words" => self.words.clone()},
            Amendment::validate,
        )?;
        exp.amendments.push(a);
        Ok(crate::dict! {"amendment" => crate::obj! {"why" => amend, "change" => change, "before" => recorded, "after" => after}})
    }

    /// Propose a setting's value (`provisional`), record it as `unknown`, or declare it per-run; then verify it against
    /// its source when the source can be checked. Returns the setting and the verification row.
    pub fn set_setting(&self, experiment_id: &str, name: &str, args: SetSetting) -> Res<(Setting, Option<VerifyRow>)> {
        let out = self.write_setting(experiment_id, name, args)?;
        self.tell_unit_is_shared(experiment_id, name)?;
        Ok(out)
    }

    /// The unit is a per-run setting: one that names a setting with one value for every run gives no run a unit of its
    /// own, so every run goes uncounted; the write that makes it so is told.
    fn tell_unit_is_shared(&self, experiment_id: &str, name: &str) -> Res<()> {
        let exp: Experiment = self.load(experiment_id)?;
        let Some(unit) = self.unit_setting(&exp) else { return Ok(()) };
        if name != "population.unit" && name != unit {
            return Ok(());
        }
        if let Some(s) = exp.settings.iter().find(|s| s.name == unit && s.retracted.is_none() && !s.per_run) {
            let has = if s.value.is_null() { "not yet known".to_string() } else { show(&s.value) };
            let amend = if exp.frozen.is_some() { " --amend --why \"...\"" } else { "" };
            crate::plain::tell(format!(
                "population.unit is {unit}, which has one value for every run ({has}): no run has a {unit} of its own, so none is counted. Make it per-run, with each run's value: rb spec set {} {unit} --per-run{amend}.",
                exp.id
            ));
        }
        Ok(())
    }

    fn write_setting(&self, experiment_id: &str, name: &str, args: SetSetting) -> Res<(Setting, Option<VerifyRow>)> {
        let who = self.actor()?;
        let SetSetting { mut value, mut unknown, mut source, required, per_run, design_factor, cited, note, amend, verify, why } = args;
        // the parts of the setting this write states, which its line names even where they read as before
        let mut states: Vec<&str> = Vec::new();
        for (given, part) in [(!value.is_null() || unknown || per_run.is_some(), "setting"), (source.is_some(), "source"), (required.is_some(), "optional"), (design_factor.is_some(), "factor"), (cited.is_some(), "cited")] {
            if given {
                states.push(part);
            }
        }
        not_a_store_row(cited.as_ref().and_then(|c| c.source.as_ref()), &self.root, "A cited value's source")?;
        if unknown && !value.is_null() {
            return Err(RBError::msg("E_OBJECT_INVALID", "A setting is either unknown or has a value, not both."));
        }
        if let Some(s) = source.as_mut() {
            s.resolved = None;
        }
        self.locked(|| {
            let mut exp: Experiment = self.load(experiment_id)?;
            writable(&exp)?;
            let (vat, bare) = exp.split_index(name);
            let bare = bare.to_string();
            if !re_full(SETTING_PATTERN, &bare) {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("Setting names are letters, digits and _.:- (got {}).", repr_str(&bare))));
            }
            let old_at = exp.holder(vat).iter().position(|s| s.name == bare && s.retracted.is_none());
            let old: Option<Setting> = old_at.map(|i| exp.holder(vat)[i].clone());
            // as criterion add refuses it: one unit per arm would leave nothing to pair or replicate
            if let (None, "population.unit", Value::Str(unit)) = (vat, bare.as_str(), &value) {
                let unit = crate::rbyaml::py_strip(unit);
                for c in self.criteria_of(&exp.id)?.iter().filter(|c| c.retracted.is_none()) {
                    if let (Some(a), Some(b)) = (c.subject_where.get(unit), c.reference_where.get(unit)) {
                        if !same_value(a, b) {
                            let amend = if exp.frozen.is_some() { " --amend --why \"...\"" } else { "" };
                            return Err(RBError::msg_fix(
                                "E_OBJECT_INVALID",
                                format!("The independent unit cannot also be the setting that separates the two selected arms: criterion {} reads {unit}={} against {unit}={}.", c.id, show(a), show(b)),
                                format!("Name the setting whose values are the units within each arm (a seed, a photograph): rb spec set {} population.unit <setting>{amend}.", exp.id),
                            ));
                        }
                    }
                }
            }
            if design_factor == Some(true) && vat.is_some() {
                return Err(RBError::msg("E_OBJECT_INVALID", "Declare a design factor as a shared per-run setting, not on one variant."));
            }
            // a designation changes no spec: it makes only a new setting per-run
            if let Some(o) = old.as_ref().filter(|o| design_factor == Some(true) && per_run.is_none() && !o.per_run) {
                let (has, drops) = if o.value.is_null() { ("not yet known".to_string(), "") } else { (show(&o.value), ", which drops its value and its source") };
                let amend = if exp.frozen.is_some() { " --amend --why \"...\"" } else { "" };
                return Err(RBError::msg_fix(
                    "E_OBJECT_INVALID",
                    format!("{name} has one value for every run ({has}); a fixed design factor is a per-run setting, and declaring one does not change the spec."),
                    format!("Make it per-run first{drops}; the runs recorded so far then read as under an earlier spec: rb spec set {experiment_id} {name} --per-run{amend}, then rb spec set {experiment_id} {name} --design-factor{amend}."),
                ));
            }
            let per_run = if design_factor == Some(true) && old.is_none() { Some(true) } else { per_run };
            let before = self.freeze_sha(&exp, None)?;
            let is_per_run = per_run.unwrap_or_else(|| old.as_ref().map_or(false, |o| o.per_run));
            if is_per_run {
                if !value.is_null() {
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("{bare} is per-run: each run gives its own value with --set {bare}=<value>.")));
                }
                unknown = true;
            } else if !unknown && value.is_null() {
                let Some(o) = old.as_ref() else {
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("Give {name} a value, record it as unknown, or declare it --per-run.")));
                };
                value = o.value.clone();
                unknown = o.status == "unknown";
            }
            let mut source = source.clone();
            if let Some(s) = source.as_ref() {
                let path = s.path.clone().unwrap_or_else(|| "None".into());
                if s.kind == "file" && s.line.map_or(false, |l| l != 0) && s.key.as_deref().map_or(true, |k| k.is_empty()) && sources::structured_kind(&path) == Some("yaml") {
                    source = Some(self.yaml_line_to_key(s, &bare));
                }
            }
            let same_value = old.as_ref().map_or(false, |o| !unknown && same_value(&o.value, &value));
            let mut new_source = match &source {
                Some(s) => Some(s.clone()),
                None => {
                    if same_value {
                        old.as_ref().unwrap().source.clone()
                    } else {
                        None
                    }
                }
            };
            // rb saw this source state this value: verified from it, or a conflict kept on it says the source changed
            let saw = same_value && {
                let o = old.as_ref().unwrap();
                (o.status == "verified" || sources::said_changed(o))
                    && (source.is_none() || same_source(o.source.as_ref(), source.as_ref()))
            };
            let keep = same_value && {
                let o = old.as_ref().unwrap();
                o.status == "verified"
                    && (source.is_none() || same_source(o.source.as_ref(), source.as_ref()))
                    && o.conflict.is_none()
                    // kept verified while its source still states it as a read finds it; otherwise checked on the spot
                    && o.source.as_ref().map_or(true, |src| !matches!(sources::recheck(src, &o.value, &self.root, name, true).0.as_str(), "stale" | "conflict"))
            };
            let status;
            if keep {
                status = "verified";
                new_source = old.as_ref().unwrap().source.clone();
            } else {
                status = if unknown { "unknown" } else { "provisional" };
                if let Some(ns) = new_source.as_mut() {
                    ns.resolved = None;
                }
            }
            let default_required = match required {
                Some(r) => r,
                None => match &old {
                    Some(o) => o.required,
                    None => !bare.starts_with("population."),
                },
            };
            let cited_v = match &cited {
                Some(c) => Some(c.clone()),
                None => old.as_ref().and_then(|o| o.cited.clone()),
            };
            let note_v = match (&note, &old) {
                (None, Some(o)) => o.note.clone(),
                _ => note.clone().unwrap_or_default(),
            };
            let mut new: Setting = nested(
                crate::dict! {
                    "name" => bare.clone(), "value" => if unknown { Value::Null } else { value.clone() }, "status" => status,
                    "required" => default_required, "per_run" => is_per_run,
                    "design_factor" => design_factor.or_else(|| old.as_ref().and_then(|o| o.design_factor)).filter(|d| *d),
                    "source" => new_source.as_ref().map(|s| s.to_value()), "cited" => cited_v.as_ref().map(|c| c.to_value()),
                    "note" => note_v, "set_by" => who.id.clone(), "set_at" => now(), "via" => self.via.clone()
                },
                Setting::validate,
            )?;
            // the same value from the same source keeps the conflict found there, until a check reads the source again
            if let Some(o) = old.as_ref().filter(|o| same_value && same_source(o.source.as_ref(), new_source.as_ref())) {
                new.conflict = o.conflict.clone();
            }
            let at: SettingAt = match old_at {
                Some(i) => {
                    exp.holder_mut(vat)[i] = new.clone();
                    (vat, i)
                }
                None => {
                    exp.holder_mut(vat).push(new.clone());
                    (vat, exp.holder(vat).len() - 1)
                }
            };
            let change = describe_change(name, old.as_ref(), &new);
            let after = self.freeze_sha(&exp, None)?;
            let amended = self.guard(&mut exp, &before, &after, amend.as_deref(), &who, &change)?;
            let mut detail = crate::dict! {
                "setting" => name, "change" => change.clone(), "value" => new.value.clone(), "status" => new.status.clone(),
                "required" => new.required, "source" => new_source.as_ref().map(|s| s.label())
            };
            if !why.trim().is_empty() { detail.insert("why".into(), Value::Str(why.clone())); }
            let part = match vat { Some(i) => format!("{}/{bare}", exp.variants[i].name), None => bare.clone() };
            let stated = design::stated_parts(&part, &new, &states);
            self.commit_stating(&who, "spec_set", Obj::Experiment(exp.clone()), merge(detail, amended), Some((exp.id.as_str(), stated)))?;
            let mut row = None;
            let mut result = new.clone();
            if verify && new_source.as_ref().map_or(false, |s| s.checkable()) && new.status == "provisional" {
                let r = self.verify_one(&mut exp, name, at, &who, saw);
                let mut detail = crate::dict! {"setting" => name, "outcome" => r.get("after").cloned().unwrap_or(Value::Null)};
                if !r.get("ok").map_or(false, |v| v.truthy()) {
                    detail.insert("reason".into(), r.get("reason").cloned().unwrap_or(Value::Null));
                }
                self.commit(&who, "spec_verify", Obj::Experiment(exp.clone()), detail)?;
                result = exp.lookup(name).cloned().unwrap_or(result);
                row = Some(r);
            }
            Ok((result, row))
        })
    }

    /// A line of a YAML file is turned into the key path on that line, when that key is the setting's own.
    fn yaml_line_to_key(&self, source: &Source, name: &str) -> Source {
        let path = source.path.clone().unwrap_or_else(|| "None".into());
        let Ok((data, _)) = sources::read_file(&self.root, &path, source.commit.as_deref()) else {
            return source.clone();
        };
        let text = String::from_utf8_lossy(&data).into_owned();
        let key = sources::yaml_key_at(&text, source.line.unwrap_or(0).max(0) as usize);
        match key {
            Some(k) if sources::key_names(&k, name, source.term.as_deref()) => {
                let mut s = source.clone();
                s.key = Some(k);
                s.line = None;
                s
            }
            _ => source.clone(),
        }
    }

    /// Check one setting's source, changing the setting in place: verified, or why not. `saw`: rb saw the source state
    /// the setting's value before this check, so a conflict is a change of the source.
    pub fn verify_one(&self, exp: &mut Experiment, name: &str, at: SettingAt, _who: &Actor, saw: bool) -> VerifyRow {
        let exp_id = exp.id.clone();
        let s = exp.setting_at_mut(at);
        let mut row = crate::dict! {"setting" => name, "value" => s.value.clone(), "before" => s.status.clone()};
        let upd = |row: &mut Dict, items: Vec<(&str, Value)>| {
            for (k, v) in items {
                row.insert(k.into(), v);
            }
        };
        if s.per_run {
            upd(&mut row, vec![("after", Value::Str(s.status.clone())), ("ok", true.into()), ("skipped", true.into()), ("reason", "per-run: each run gives its own value".into())]);
            return row;
        }
        if s.value.is_null() {
            upd(
                &mut row,
                vec![("after", Value::Str(s.status.clone())), ("ok", false.into()), ("code", "E_SOURCE_UNRESOLVED".into()), ("reason", "the setting is unknown; propose a value with its source first".into())],
            );
            return row;
        }
        if s.status == "inherited" && s.source.as_ref().map_or(true, |x| !x.checkable()) {
            upd(
                &mut row,
                vec![
                    ("after", Value::Str(s.status.clone())),
                    ("ok", false.into()),
                    ("code", "E_SOURCE_UNRESOLVED".into()),
                    ("reason", format!("an inherited setting with nothing here to read is verified by setting it with its source: rb spec set {exp_id} {name} <value> --source <file#key>").into()),
                ],
            );
            return row;
        }
        let Some(src) = s.source.clone() else {
            upd(&mut row, vec![("after", Value::Str(s.status.clone())), ("ok", true.into()), ("skipped", true.into()), ("code", "E_SOURCE_UNVERIFIABLE".into()), ("reason", "no source to check".into())]);
            return row;
        };
        if !src.checkable() {
            upd(
                &mut row,
                vec![
                    ("after", Value::Str(s.status.clone())),
                    ("ok", true.into()),
                    ("skipped", true.into()),
                    ("code", "E_SOURCE_UNVERIFIABLE".into()),
                    ("reason", format!("not checkable ({}); it stays provisional. Point --source at the file or run that sets it", src.kind).into()),
                ],
            );
            return row;
        }
        match sources::resolve(&src, &s.value, &self.root, name, src.resolved.as_ref(), true) {
            Ok(res) => {
                let mut ns = src.clone();
                ns.resolved = Some(res.clone());
                let label = ns.label();
                s.source = Some(ns);
                s.status = "verified".into();
                s.conflict = None;
                s.failed = None;
                upd(
                    &mut row,
                    vec![
                        ("after", "verified".into()),
                        ("ok", true.into()),
                        ("read", Value::Str(res.text.clone())),
                        ("line", Value::opt_i64(res.line)),
                        ("commit", Value::opt_str(res.commit.clone())),
                        ("sha256", Value::Str(res.sha256.clone())),
                        ("where", Value::Str(label)),
                    ],
                );
            }
            Err(u) => {
                let saw = saw || src.resolved.is_some() || sources::said_changed(s);
                let reason = if u.differs { u.conflict(&src, &s.value, saw) } else { u.reason.clone() };
                // a check that read the source replaces the last one's finding; one that could not read it keeps a conflict
                s.conflict = if u.differs {
                    Some(Conflict { at: now(), read: u.found.clone(), text: truncate(&reason, 400) })
                } else if u.unread {
                    s.conflict.take()
                } else {
                    None
                };
                if u.differs || s.status == "verified" {
                    s.status = "provisional".into();
                    let mut ns = src.clone();
                    ns.resolved = None;
                    s.source = Some(ns);
                }
                s.failed = Some(truncate(&reason, 400));
                upd(
                    &mut row,
                    vec![
                        ("after", Value::Str(s.status.clone())),
                        ("ok", false.into()),
                        ("code", Value::Str(u.code.clone())),
                        ("reason", Value::Str(reason)),
                        ("candidates", Value::List(u.candidates.clone())),
                        ("conflict", s.conflict.is_some().into()),
                    ],
                );
            }
        }
        row
    }

    /// Check each named setting's source (every setting with a source when none are named).
    pub fn verify_settings(&self, experiment_id: &str, names: &[String]) -> Res<Vec<VerifyRow>> {
        let who = self.actor()?;
        self.locked(|| {
            let mut exp: Experiment = self.load(experiment_id)?;
            writable(&exp)?;
            for n in names {
                if exp.lookup(n).is_none() {
                    return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("Experiment {} has no setting {}.", exp.id, repr_str(n))));
                }
            }
            let targets: Vec<(String, SettingAt)> = if !names.is_empty() {
                names.iter().filter_map(|n| exp.lookup_at(n).map(|a| (n.clone(), a))).collect()
            } else {
                exp.all_settings_at().into_iter().filter(|(_, a)| exp.setting_at(*a).source.is_some()).collect()
            };
            let mut rows = Vec::new();
            for (n, a) in targets {
                rows.push(self.verify_one(&mut exp, &n, a, &who, false));
            }
            if !rows.is_empty() {
                let results: Vec<Value> = rows
                    .iter()
                    .map(|r| crate::obj! {"setting" => r.get("setting").cloned().unwrap_or(Value::Null), "after" => r.get("after").cloned().unwrap_or(Value::Null), "ok" => r.get("ok").cloned().unwrap_or(Value::Null)})
                    .collect();
                self.commit(&who, "spec_verify", Obj::Experiment(exp), crate::dict! {"results" => Value::List(results)})?;
            }
            Ok(rows)
        })
    }

    /// Lock the spec and the criteria, attributed to whoever locks them.
    pub fn freeze(&self, experiment_id: &str, why: &str, amend: bool) -> Res<Freeze> {
        let who = self.actor()?;
        self.locked(|| {
            let mut exp: Experiment = self.load(experiment_id)?;
            writable(&exp)?;
            if amend {
                if exp.frozen.is_none() {
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("{} is not frozen; freeze it without --amend.", exp.id)));
                }
                let recorded = self.recorded_sha(&exp);
                let current = self.freeze_sha(&exp, None)?;
                if recorded.as_deref() == Some(current.as_str()) {
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("{} matches its freeze record; there is nothing to adopt.", exp.id)));
                }
                let a: Amendment = nested(
                    crate::dict! {"at" => now(), "by" => who.id.clone(), "why" => why, "change" => "adopted the experiment as it is now",
                    "before" => recorded.clone().unwrap_or_default(), "after" => current.clone(), "words" => self.words.clone()},
                    Amendment::validate,
                )?;
                exp.amendments.push(a);
                self.commit(&who, "amend", Obj::Experiment(exp.clone()), crate::dict! {"why" => why, "before" => recorded, "after" => current})?;
                return Ok(exp.frozen.clone().unwrap());
            }
            if let Some(f) = &exp.frozen {
                return Err(RBError::msg_fix(
                    "E_FROZEN",
                    format!("Experiment {} is already frozen ({}).", exp.id, f.at),
                    "Change it with --amend --why \"<reason>\" on the command that changes it; the amendment is recorded.",
                ));
            }
            let before: Vec<String> = self.live::<Run>()?.iter().filter(|r| r.experiment == exp.id).map(|r| r.id.clone()).collect();
            let f: Freeze = nested(
                crate::dict! {"sha256" => self.freeze_sha(&exp, None)?, "at" => now(), "by" => who.id.clone(), "why" => why,
                "after_runs" => str_list(&before), "words" => self.words.clone()},
                Freeze::validate,
            )?;
            exp.frozen = Some(f.clone());
            self.commit(&who, "freeze", Obj::Experiment(exp), crate::dict! {"sha256_spec" => f.sha256.clone(), "why" => why, "after_runs" => str_list(&before)})?;
            Ok(f)
        })
    }

    // ------------------------------------------------------------ criteria

    pub fn criteria_of(&self, experiment_id: &str) -> Res<Vec<Criterion>> {
        Ok(self.all::<Criterion>()?.iter().filter(|c| c.experiment == experiment_id).cloned().collect())
    }

    /// Declare a criterion: a rule over one comparison, fixed once written, attributed to whoever wrote it.
    pub fn add_criterion(&self, a: &NewCriterion) -> Res<Criterion> {
        let who = self.actor()?;
        if !a.when_met.is_empty() && a.questions.is_empty() {
            return Err(RBError::msg("E_OBJECT_INVALID", "--when-met needs a question: give -q <question>."));
        }
        let mut source = a.source.clone();
        if let Some(s) = source.as_mut() {
            s.resolved = None;
        }
        not_a_store_row(source.as_ref(), &self.root, "A cited criterion's source")?;
        self.locked(|| {
            let exp: Experiment = self.load(&a.experiment)?;
            writable(&exp)?;
            self.check_write("experiment", &exp.id)?;
            let mut links = Vec::new();
            let mut linked = HashSet::new();
            for qid in &a.questions {
                if linked.insert(qid) {
                    links.push(self.question_link(qid, &a.when_met, &a.why, &who)?.to_value());
                }
            }
            let mut crit: Criterion = build(crate::dict! {
                "id" => self.new_id("criterion", a.id.as_deref())?, "experiment" => exp.id.clone(), "statement" => a.statement.clone(),
                "metric" => a.metric.clone(), "subject" => a.subject.clone(), "reference" => a.reference.clone(), "estimand" => a.estimand.clone(),
                "where" => Value::Dict(a.r#where.clone()), "subject_where" => Value::Dict(a.subject_where.clone()), "reference_where" => Value::Dict(a.reference_where.clone()),
                "comparator" => a.comparator.clone(), "target" => a.target, "tolerance" => a.tolerance, "margin" => a.margin, "over" => a.over.clone(),
                "role" => a.role.clone(), "min_n" => a.min_n.clone(), "noise" => a.noise,
                "origin" => if source.is_some() { "cited" } else { "own" }, "source" => source.as_ref().map(|s| s.to_value()),
                "words" => self.words.clone(), "note" => a.note.clone(), "created_by" => who.id.clone(), "created_at" => now(), "via" => self.via.clone(),
                "question_links" => Value::List(links)
            })?;
            declared_once(&crit, None, None, true)?;
            for key in crit.r#where.keys().chain(crit.subject_where.keys()).chain(crit.reference_where.keys()) {
                if !exp.settings.iter().any(|s| &s.name == key && s.per_run && s.retracted.is_none()) {
                    let amend = if exp.frozen.is_some() { " --amend --why \"...\"" } else { "" };
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("Selector {key} needs a shared per-run setting: rb spec set {} {key} --per-run{amend}.", exp.id)));
                }
            }
            if crit.scoped() {
                let (subject, reference, _, _) = self.arms(&exp, &crit)?;
                if !reference.is_empty() && subject == reference && !crit.subject_where.iter().any(|(k, v)| crit.reference_where.get(k).map_or(false, |other| !same_value(other, v))) {
                    return Err(RBError::msg("E_OBJECT_INVALID", "Comparing one variant with itself needs disjoint subject and reference selectors (different values of the same setting)."));
                }
                if let Some(unit) = self.unit_setting(&exp) {
                    if crit.subject_where.iter().any(|(k, v)| k == &unit && crit.reference_where.get(k).map_or(false, |other| !same_value(other, v))) {
                        return Err(RBError::msg("E_OBJECT_INVALID", "The independent unit cannot also be the setting that separates the two selected arms."));
                    }
                }
            }
            if INTERVAL_RULES.contains(&crit.comparator.as_str()) {
                let base = last_part(&crit.metric).to_string();
                let m = self.catalogue_entry(&base)?;
                if m.as_ref().map_or(true, |m| m.direction == "none") && crit.comparator != "equivalent" {
                    return Err(RBError::msg_fix(
                        "E_OBJECT_INVALID",
                        format!("A {} rule reads which side of 0 is better, and the catalogue does not say it for {base}.", crit.comparator),
                        format!("rb metric add {base} --minimize (or --maximize), then declare the criterion."),
                    ));
                }
            }
            if let Some(src) = source.as_ref().filter(|s| s.checkable()) {
                let Some(target) = crit.target else {
                    return Err(RBError::msg("E_OBJECT_INVALID", "A cited criterion states the number a source gives: use a threshold rule with its target."));
                };
                match sources::resolve(src, &crit.cited_value(), &self.root, last_part(&a.metric), None, false) {
                    Ok(res) => {
                        let mut s2 = src.clone();
                        s2.resolved = Some(res);
                        crit.source = Some(s2);
                    }
                    Err(u) if crit.cited_ordering().is_some() => {
                        return Err(RBError::msg_fix(
                            &u.code,
                            format!("The criterion's source does not hold the ordering it quotes: {}", u.reason),
                            "Quote the file's own words, as they are on that line: --source <file> --quote \"...\".",
                        ))
                    }
                    Err(u) => {
                        let fix = if target == 0.0 && matches!(crit.comparator.as_str(), "at_least" | "at_most") {
                            "An ordering a file states (one arm beats another) is a threshold of 0, cited by its words: --source <file> --quote \"<the line's words>\"."
                        } else {
                            "Point --source/--quote at the text that states the number, or correct the target."
                        };
                        return Err(RBError::msg_fix(
                            &u.code,
                            format!("The criterion's source does not state its target {}: {}", crate::pyfloat::fmt_float(target, "g"), u.reason),
                            fix,
                        ))
                    }
                }
            }
            self.why_states_rule(&crit, &a.why)?;
            let before = self.freeze_sha(&exp, None)?;
            let mut with_new = self.criteria_of(&exp.id)?;
            with_new.push(crit.clone());
            let after = self.freeze_sha(&exp, Some(&with_new))?;
            let mut exp = exp;
            let amended = self.guard(&mut exp, &before, &after, a.amend.as_deref(), &who, &format!("new criterion {}: {}", crit.id, crit.rule()))?;
            if !amended.is_empty() {
                self.commit(&who, "amend", Obj::Experiment(exp.clone()), amended.clone())?;
            }
            let mut detail = crate::dict! {"rule" => crit.rule(), "experiment" => exp.id.clone(), "role" => crit.role.clone()};
            if !a.why.is_empty() { detail.insert("why".into(), Value::Str(a.why.clone())); }
            self.commit(
                &who,
                "add",
                Obj::Criterion(crit.clone()),
                merge(detail, amended),
            )?;
            Ok(crit)
        })
    }

    /// A reason given as a document's line alone (--why "docs/plan.md:3") says the rule comes from that line: the line
    /// must be there, not a comment, and state the rule's number, or the criterion is refused.
    fn why_states_rule(&self, crit: &Criterion, why: &str) -> Res<()> {
        static POINTER: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
        let re = POINTER.get_or_init(|| regex::Regex::new(r"^\s*([^\s:]*[./][^\s:]*):(\d+)\s*$").unwrap());
        let Some(m) = re.captures(why) else { return Ok(()) };
        let (path, line) = (m[1].to_string(), m[2].parse::<usize>().unwrap_or(0));
        if path.contains("://") {
            return Ok(());
        }
        let refuse = |what: String| {
            RBError::msg_fix(
                "E_SOURCE_UNRESOLVED",
                format!("The criterion's reason is {path}:{line}, and {what}."),
                "Give the threshold that line states, or the reason in words. A threshold no document states stays unknown for the runs already made.",
            )
        };
        if !sources::inside_project(&self.root, &path) {
            return Err(refuse(format!("{path} is outside the project")));
        }
        let (data, _) = sources::read_file(&self.root, &path, None).map_err(|u| refuse(u.reason))?;
        let lines = sources::split_lines(&String::from_utf8_lossy(&data));
        if line == 0 || line > lines.len() {
            let n = lines.len();
            return Err(refuse(format!("{path} has {n} line{}", if n == 1 { "" } else { "s" })));
        }
        let (comments, texts) = sources::stated_lines(&path, &lines);
        if comments[line - 1] {
            return Err(refuse("that line is a comment, which states nothing".into()));
        }
        let text = &texts[line - 1];
        let percent = text.contains('%');
        let found: Vec<f64> = sources::numbers(text).iter().filter_map(|v| v.as_f64()).map(f64::abs).collect();
        for want in [crit.target, crit.margin].into_iter().flatten().map(f64::abs) {
            if want == 0.0 && !found.contains(&0.0) {
                // an ordering a line states is a sign, which no number on it says: the reason says it in words
                return Err(RBError::msg_fix(
                    "E_SOURCE_UNRESOLVED",
                    format!("The criterion's reason is {path}:{line}, and that line does not state its threshold 0."),
                    format!("An ordering a line states (one arm beats another) is a threshold of 0, cited by its words: --source {path} --quote \"<the line's words>\"."),
                ));
            }
            if !found.iter().any(|n| *n == want || (percent && *n / 100.0 == want)) {
                return Err(refuse(format!("that line does not state its threshold {}", crate::pyfloat::fmt_float(want, "g"))));
            }
        }
        Ok(())
    }

    /// Declare how a criterion bears on a question, keeping every earlier declaration and its attribution.
    pub fn file_criterion(&self, criterion_id: &str, question_id: &str, when_met: Option<&str>, remove: bool, why: &str, amend: Option<&str>) -> Res<Criterion> {
        if why.trim().is_empty() || why.chars().count() > TEXT {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("Filing a criterion needs a nonblank reason of at most {TEXT} characters: --why \"...\".")));
        }
        if remove && when_met.is_some() {
            return Err(RBError::msg("E_OBJECT_INVALID", "--remove cannot be combined with --when-met."));
        }
        let who = self.actor()?;
        self.locked(|| {
            self.check_write("criterion", criterion_id)?;
            let mut crit: Criterion = self.load(criterion_id)?;
            if crit.retracted.is_some() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{} is retracted.", crit.id)));
            }
            let mut exp: Experiment = self.load(&crit.experiment)?;
            writable(&exp)?;
            self.check_write("experiment", &exp.id)?;
            // every active declaration for this question: one, or two where a merge of two branches left them
            let active: Vec<usize> = (0..crit.question_links.len()).filter(|i| crit.question_links[*i].question == question_id && crit.question_links[*i].retracted.is_none()).collect();
            let mut kept: Vec<&str> = active.iter().map(|i| crit.question_links[*i].when_met.as_str()).collect();
            kept.dedup();
            let text = when_met.map(str::to_string).unwrap_or_else(|| kept.first().map(|t| t.to_string()).unwrap_or_default());
            // Removing an existing declaration remains possible after its question was retracted or deleted.
            let link = if remove { None } else { Some(self.question_link(question_id, &text, why, &who)?) };
            // Words are carried into a new declaration only when the filer states them: decided by content, never by who files.
            if let (None, Some(new)) = (when_met, &link) {
                let amend = if exp.frozen.is_some() { " --amend" } else { "" };
                let restate = format!("Restate the interpretation for the question as it reads now: rb criterion file {} {question_id} --when-met \"...\" --why \"...\"{amend} (the same words keep it), or clear it with --when-met \"\".", crit.id);
                if kept.len() > 1 {
                    return Err(RBError::msg_fix(
                        "E_OBJECT_INVALID",
                        format!("{} has {} active declarations for {question_id} with different interpretations, so there is no one to keep.", crit.id, active.len()),
                        restate,
                    ));
                }
                let reworded = active.iter().any(|i| crit.question_links[*i].question_text != new.question_text);
                if !text.is_empty() && (reworded || active.len() > 1) {
                    let what = if reworded { format!("question {question_id} was reworded after this interpretation was declared on it") } else { format!("it has {} active declarations for {question_id}", active.len()) };
                    return Err(RBError::msg_fix(
                        "E_OBJECT_INVALID",
                        format!("{}: {what}, and a filing without --when-met would carry its words into a new declaration, attributed to whoever files it.", crit.id),
                        restate,
                    ));
                }
            }
            match (active.as_slice(), &link) {
                ([i], Some(new)) => {
                    let old = &crit.question_links[*i];
                    if old.question_text == new.question_text && old.when_met == new.when_met {
                        return Ok(crit);
                    }
                }
                ([], None) => return Err(RBError::msg("E_OBJECT_INVALID", format!("{} has no active declaration for {question_id}.", crit.id))),
                _ => (),
            }
            let before = self.freeze_sha(&exp, None)?;
            for i in &active {
                crit.question_links[*i].retracted = Some(nested(crate::dict! {
                    "at" => now(), "by" => who.id.clone(), "why" => why, "words" => self.words.clone()
                }, Retraction::validate)?);
            }
            if let Some(new) = link {
                crit.question_links.push(new);
            }
            crit = nested(crit.dump(false), Criterion::validate)?;
            declared_once(&crit, Some(question_id), None, false)?;
            let mut criteria = self.criteria_of(&exp.id)?;
            for current in &mut criteria {
                if current.id == crit.id { *current = crit.clone(); }
            }
            let after = self.freeze_sha(&exp, Some(&criteria))?;
            let amended = self.guard(&mut exp, &before, &after, amend, &who, &format!("criterion {}: {} question {question_id}", crit.id, if remove { "unfile" } else { "file under" }))?;
            if !amended.is_empty() {
                self.commit(&who, "amend", Obj::Experiment(exp), amended.clone())?;
            }
            self.commit(&who, if remove { "unfile" } else { "file" }, Obj::Criterion(crit.clone()),
                merge(crate::dict! {"question" => question_id, "when_met" => when_met, "why" => why}, amended))?;
            Ok(crit)
        })
    }

    fn question_link(&self, question_id: &str, when_met: &str, why: &str, who: &Actor) -> Res<QuestionLink> {
        self.check_write("question", question_id)?;
        let q: Question = self.load(question_id)?;
        if q.retracted.is_some() {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("Question {} is retracted.", q.id)));
        }
        nested(crate::dict! {
            "question" => q.id, "question_text" => q.text, "when_met" => when_met,
            "declared_by" => who.id.clone(), "declared_at" => now(), "via" => self.via.clone(),
            "why" => why, "words" => self.words.clone(), "session" => actor::session_for(&who)
        }, QuestionLink::validate)
    }

    /// A criterion's metric that no run or analysis reports and the catalogue does not hold, close to a name one of
    /// them does (a misspelling): one line naming that name, or None. The criterion stands, since its metric declares
    /// the name by use.
    pub fn metric_near(&self, crit: &Criterion) -> Res<Option<String>> {
        let base = last_part(&crit.metric);
        if self.catalogue_entry(base)?.is_some() {
            return Ok(None);
        }
        let mut reported: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
        for r in self.live::<Run>()? {
            reported.extend(r.metrics.keys().map(|k| last_part(k).to_string()));
        }
        for a in self.live::<Analysis>()? {
            reported.extend(a.comparisons.iter().map(|c| last_part(&c.metric).to_string()));
        }
        if reported.contains(base) {
            return Ok(None);
        }
        let held: std::collections::BTreeSet<String> = self.live::<Metric>()?.into_iter().flat_map(|m| std::iter::once(m.id).chain(m.aliases)).collect();
        let near = near_name(base, reported.iter().chain(held.iter()).map(|s| s.as_str()));
        Ok(near.map(|near| {
            let has = if reported.contains(near) { "runs report" } else { "the catalogue holds" };
            format!("no run reports {base} and the catalogue does not hold it; {has} {near}, a close name. The criterion reads {} as given.", crit.metric)
        }))
    }

    /// A criterion's metric that no run of its experiment reports and the catalogue does not hold, close to a name one
    /// of them reports (a misspelling, most likely): (the name it reads, the name the runs report), or None.
    pub fn reported_near(&self, crit: &Criterion) -> Res<Option<(String, String)>> {
        let base = last_part(&crit.metric);
        let mut reported: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
        for r in self.live::<Run>()?.iter().filter(|r| r.experiment == crit.experiment) {
            reported.extend(r.metrics.keys().map(|k| last_part(k).to_string()));
        }
        if reported.contains(base) || self.catalogue_entry(base)?.is_some() {
            return Ok(None);
        }
        Ok(near_name(base, reported.iter().map(String::as_str)).map(|near| (base.to_string(), near.to_string())))
    }

    /// The catalogue's metric for a name or one of its aliases, or None.
    pub fn catalogue_entry(&self, name: &str) -> Res<Option<Metric>> {
        for m in self.live::<Metric>()? {
            if name == m.id || m.aliases.iter().any(|a| a == name) {
                return Ok(Some(m));
            }
        }
        Ok(None)
    }

    // ------------------------------------------------------------ runs

    /// Record a run on an experiment.
    pub fn add_run(&self, experiment_id: &str, a: NewRun) -> Res<Added<Run>> {
        // @<column> is a value of the row read from a database; with no row, it is a placeholder, never a value
        if a.row.is_none() {
            let read = "a row is read with --from <database> --table <table> --where <column>=<value>";
            if let Some((k, v)) = a.per_run.iter().find_map(|(k, v)| v.as_str().filter(|t| t.len() > 1 && t.starts_with('@')).map(|t| (k, t))) {
                return Err(RBError::msg_fix(
                    "E_OBJECT_INVALID",
                    format!("--set {k}={v}: {v} is the value of a column of the database row --from reads, and this run reads no row."),
                    format!("Give the value itself, --set {k}=<value>; {read}."),
                ));
            }
            if let Some(v) = a.produced_at.as_deref().filter(|t| t.len() > 1 && t.starts_with('@')) {
                return Err(RBError::msg_fix(
                    "E_OBJECT_INVALID",
                    format!("--produced-at {v}: {v} is the time in a column of the database row --from reads, and this run reads no row."),
                    format!("Give the time itself, --produced-at <ISO date or time>, or unknown; {read}."),
                ));
            }
        }
        let who = self.actor()?;
        let mut values: IndexMap<String, Value> = IndexMap::new();
        let files = a.files.clone();
        let basis = a.basis.clone();
        let (exp, run, mut notices) = {
            let r = self.locked(|| -> Res<Result<(Experiment, Run, Vec<String>), Added<Run>>> {
                let exp: Experiment = self.load(experiment_id)?;
                writable(&exp)?;
                if let Some(v) = &a.variant {
                    if exp.variant(v).is_none() {
                        let names: Vec<String> = exp.live_variants().iter().map(|v| v.name.clone()).collect();
                        return Err(RBError::msg(
                            "E_OBJECT_NOT_FOUND",
                            format!("{} has no variant {} (it has: {}).", exp.id, repr_str(v), if names.is_empty() { "none".into() } else { names.join(", ") }),
                        ));
                    }
                }
                for (k, x) in &a.metrics {
                    values.insert(self.metric_key_for(&exp, k, a.variant.as_deref()), x.clone());
                }
                if values.is_empty() {
                    return Err(RBError::msg("E_OBJECT_INVALID", "A run needs at least one number: NAME=VALUE, or --from file.json."));
                }
                let given = a.per_run.clone();
                for k in given.keys() {
                    match exp.lookup(k) {
                        Some(s) if s.per_run => {}
                        _ => {
                            let amend = if exp.frozen.is_some() { " --amend --why \"...\"" } else { "" };
                            return Err(RBError::msg(
                                "E_OBJECT_INVALID",
                                format!("--set {k}: {k} is not a per-run setting of {}. Declare it: rb spec set {} {k} --per-run{amend}.", exp.id, exp.id),
                            ));
                        }
                    }
                }
                let mut missing: Vec<String> = exp
                    .all_settings()
                    .into_iter()
                    .filter(|(n, s)| s.per_run && s.retracted.is_none() && !given.contains_key(n) && !given.contains_key(last_part(n)))
                    .map(|(n, _)| n)
                    .collect();
                missing.sort();
                missing.dedup();
                if !missing.is_empty() {
                    return Err(RBError::msg(
                        "E_OBJECT_INVALID",
                        format!(
                            "{} declares {} per-run: give each run's own with {}. Without it, rb cannot tell which unit a run belongs to.",
                            exp.id,
                            missing.join(", "),
                            missing.iter().map(|n| format!("--set {n}=<value>")).collect::<Vec<_>>().join(" ")
                        ),
                    ));
                }
                if let Some(rep) = &a.repeats {
                    if self.kind_of(rep)? != Some("run") || self.load::<Run>(rep)?.experiment != exp.id {
                        return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("--repeats {rep}: no run {} on {}.", repr_str(rep), exp.id)));
                    }
                }
                let mut replaced: Option<Run> = None;
                if let Some(rp) = &a.replaces {
                    if self.kind_of(rp)? != Some("run") || self.load::<Run>(rp)?.experiment != exp.id {
                        return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("--replaces {rp}: no run {} on {}.", repr_str(rp), exp.id)));
                    }
                    if crate::rbyaml::py_strip(&a.why).is_empty() {
                        return Err(RBError::msg(
                            "E_USAGE",
                            format!("--replaces {rp}: say why it is invalid, with --why \"...\"; the reason is kept with its retraction."),
                        ));
                    }
                    replaced = Some(self.load::<Run>(rp)?);
                }
                let mut notices: Vec<String> = Vec::new();
                let uses_in = self.uses(&a.inputs, "--input", &mut notices)?;
                let uses_out = self.uses(&a.outputs, "--output", &mut notices)?;
                let mut refs: Vec<FileRef> = Vec::new();
                for f in &files {
                    refs.push(self.file_ref_of(f)?);
                }
                // a row's database: the bytes hashed are the bytes its row was read from
                let row_sha = match &a.row {
                    Some(row) => {
                        let sha = refs.iter().find(|r| r.path == crate::sys::typed(&row.path)).map(|r| r.sha256.clone());
                        if sha.is_none() || crate::sqlite::stamp(&self.local(&row.path)) != Some(row.stamp) {
                            return Err(RBError::msg_fix("E_OBJECT_INVALID", format!("{} changed while rb read it.", row.path), "Read it again."));
                        }
                        sha
                    }
                    None => None,
                };
                let check = self.config_check(&exp, a.variant.as_deref(), a.config.as_deref(), Some(&given))?;
                let fingerprint = if let Some(row) = &a.row {
                    // one row is one run, whatever else its database holds: the database grows, the row stays
                    let mut fs: Vec<String> = refs.iter().filter(|r| r.path != crate::sys::typed(&row.path)).map(|r| r.sha256.clone()).collect();
                    fs.sort();
                    sha(&crate::obj! {"files" => str_list(&fs), "record" => Value::Null, "row" => row.identity.clone()})
                } else if !refs.is_empty() {
                    let mut fs: Vec<String> = refs.iter().map(|r| r.sha256.clone()).collect();
                    fs.sort();
                    sha(&crate::obj! {"files" => str_list(&fs), "record" => Value::Null})
                } else {
                    let mut typed = crate::dict! {"typed" => Value::Dict(values.clone().into_iter().collect()), "per_run" => Value::Dict(given.clone()),
                    "command" => a.command.clone().unwrap_or_default()};
                    if let Some(rep) = a.repeats.as_ref().filter(|r| !r.is_empty()) {
                        typed.insert("repeats".into(), Value::Str(rep.clone()));
                    }
                    // numbers given in another checkout are a run of their own: that checkout's code made them
                    if let Some(w) = self.worktree_value() {
                        typed.insert("worktree".into(), w);
                    }
                    sha(&Value::Dict(typed))
                };
                // the run it replaces is no duplicate: the same row or file read again under a newer spec replaces it
                let dup = self.live::<Run>()?.into_iter().find(|r| r.experiment == exp.id && r.fingerprint == fingerprint && a.replaces.as_deref() != Some(r.id.as_str()));
                let again = a.again.as_ref().filter(|x| !x.is_empty());
                if let Some(d) = &dup {
                    if again.is_none() {
                        // the same output files with other numbers: the run keeps what it recorded, and the writer is told
                        let mut differs: Vec<(&String, f64, f64)> = values
                            .iter()
                            .filter_map(|(k, v)| Some((k, *d.metrics.get(k)?, v.as_f64()?)).filter(|(_, had, given)| had != given))
                            .collect();
                        differs.sort_by(|x, y| x.0.cmp(y.0));
                        let warning = (!differs.is_empty()).then(|| {
                            format!(
                                "the numbers given now differ from what it recorded: {}",
                                differs.iter().map(|(k, had, given)| format!("{k} {} recorded, {} given", crate::pyfloat::g(*had), crate::pyfloat::g(*given))).collect::<Vec<_>>().join(", ")
                            )
                        });
                        let warning = [warning, self.earlier_spec(d, &exp)].into_iter().flatten().reduce(|a, b| format!("{a}; {b}"));
                        return Ok(Err(Added { record: d.clone(), duplicate_of: Some(d.id.clone()), warning }));
                    }
                }
                // one row is one run: read again, a row giving the values its run recorded is that run, and one giving others is refused
                let same_row: Option<Run> = match (&a.row, &dup) {
                    (Some(row), None) => {
                        let mut same: Vec<Run> =
                            self.live::<Run>()?.into_iter().filter(|r| r.experiment == exp.id && a.replaces.as_deref() != Some(r.id.as_str()) && row.is_read_by(r)).collect();
                        same.sort_by(|x, y| x.created_at.cmp(&y.created_at));
                        let unchanged = |r: &Run| {
                            r.metrics.len() == values.len()
                                && values.iter().all(|(k, v)| r.metrics.get(k).map_or(false, |had| Some(*had) == v.as_f64()))
                                && Value::Dict(r.per_run.clone()) == Value::Dict(given.clone())
                        };
                        if let (Some(d), None) = (same.iter().find(|r| unchanged(r)), again) {
                            return Ok(Err(Added { record: d.clone(), duplicate_of: Some(d.id.clone()), warning: self.earlier_spec(d, &exp) }));
                        }
                        match (same.pop(), again) {
                            (Some(d), None) => return Err(changed_row(&d, row, &values, &given)),
                            (d, _) => d,
                        }
                    }
                    _ => None,
                };
                let from: Vec<String> = refs.iter().zip(&files).filter(|(_, f)| a.numbers_from.contains(f)).map(|(r, _)| r.path.clone()).collect();
                // a second execution (--again, --repeats) that wrote the bytes a run here already recorded: the journal
                // and git saw them for that run, not for this one
                let earlier: HashSet<(String, String)> = if again.is_some() || a.repeats.as_ref().map_or(false, |r| !r.is_empty()) {
                    self.live::<Run>()?.iter().filter(|r| r.experiment == exp.id).flat_map(|r| r.files.iter().map(|f| (f.path.clone(), f.sha256.clone()))).collect()
                } else {
                    HashSet::new()
                };
                let database = a.row.as_ref().map(|row| crate::sys::typed(&row.path));
                let (produced, produced_basis, existed) = self.produced(a.produced_at.as_deref(), &refs, &from, &earlier, database.as_deref())?;
                let mut produced_v = if a.commit.as_ref().map_or(false, |c| !c.is_empty()) || a.tree.as_ref().map_or(false, |t| !t.is_empty()) {
                    let mut p = crate::dict! {"commit" => a.commit.clone()};
                    if let Some(t) = a.tree.as_ref().filter(|t| !t.is_empty()) {
                        p.insert("tree".into(), Value::Str(t.clone()));
                    }
                    p.insert("from".into(), Value::Str("flag".into()));
                    p
                } else {
                    crate::dict! {"commit" => Value::Null, "from" => "unknown"}
                };
                if let Some((at, by)) = existed {
                    produced_v.insert("existed".into(), crate::obj! {"at" => at, "by" => by});
                }
                let row_v = match (&a.row, &row_sha) {
                    (Some(row), Some(sha)) => crate::obj! {
                        "store" => "sqlite", "path" => row.path.clone(), "sha256" => sha.clone(), "table" => row.table.clone(),
                        "where" => Value::Dict(row.key.clone()), "rowid" => row.rowid.map(Value::Int).unwrap_or(Value::Null),
                        "columns" => Value::Dict(row.columns.iter().map(|(k, c)| (self.metric_key_for(&exp, k, a.variant.as_deref()), Value::Str(c.clone()))).collect()),
                        "per_run" => Value::Dict(row.per_run.iter().map(|(k, c)| (k.clone(), Value::Str(c.clone()))).collect()),
                        "produced_at" => row.produced_at.clone().map(Value::Str).unwrap_or(Value::Null)
                    },
                    _ => Value::Null,
                };
                let mut receipt = crate::dict! {
                    "at" => now(), "actor" => who.id.clone(), "actor_via" => who.via.clone(), "via" => self.via.clone(), "rb_version" => crate::version(),
                    "command" => a.command.clone(), "session" => actor::session_for(&who), "agent" => self.agent_facts(&who, true).map(Value::Dict),
                    "attached" => self.attached_code().map(Value::Dict),
                    "produced" => Value::Dict(produced_v), "environment" => Value::Dict(crate::environment::fingerprint(Some(&self.dir.join("cache")))),
                    "run_record" => Value::Null, "config" => check.as_ref().map(|c| c.to_value()), "row" => row_v
                };
                // what rb saw of the command now: a run of it the journal watched, or the log line given for its launch
                if let Some(seen) = self.command_seen(a.command.as_deref(), a.command_source.as_deref(), &refs, &values.clone().into_iter().collect())? {
                    receipt.insert("command_seen".into(), seen);
                }
                let receipt = Value::Dict(receipt);
                let read_before = dup.is_some() || same_row.is_some();
                let note = format!("{}{}", if again.is_some() && read_before { format!("recorded again: {}. ", again.unwrap()) } else { String::new() }, a.note);
                let run: Run = build(crate::dict! {
                    "id" => self.new_id("run", None)?, "uid" => new_uid("run"), "experiment" => exp.id.clone(),
                    "metrics" => Value::Dict(values.clone().into_iter().collect()), "per_run" => Value::Dict(given.clone()),
                    "repeats" => a.repeats.clone(), "replaces" => a.replaces.clone(), "produced_at" => produced, "produced_basis" => produced_basis,
                    "files" => Value::List(refs.iter().map(|r| r.to_value()).collect()),
                    "inputs" => Value::List(uses_in.iter().map(|u| u.to_value()).collect()),
                    "outputs" => Value::List(uses_out.iter().map(|u| u.to_value()).collect()),
                    "host" => Value::Dict(a.host.iter().map(|(k, v)| (k.clone(), Value::Str(v.clone()))).collect()),
                    "links" => Value::Dict(a.links.iter().map(|(k, v)| (k.clone(), Value::Str(v.clone()))).collect()),
                    "basis" => basis.clone(), "spec_sha256" => self.spec_sha(&exp), "settings" => Value::Dict(self.settings_snapshot(&exp)),
                    "arm_specs" => Value::Dict(self.arm_specs(&exp).into_iter().map(|(k, v)| (k, Value::Str(v))).collect()),
                    "spec_frozen" => exp.frozen.is_some(), "fingerprint" => fingerprint.clone(), "receipt" => receipt,
                    "note" => note, "created_by" => who.id.clone(), "created_at" => now(), "via" => self.via.clone()
                })?;
                let mut detail = crate::dict! {"experiment" => exp.id.clone(), "metrics" => Value::Dict(values.clone().into_iter().collect()),
                "per_run" => Value::Dict(given.clone()), "again" => again.is_some() && read_before};
                if let Some(rep) = a.repeats.as_ref().filter(|r| !r.is_empty()) {
                    detail.insert("repeats".into(), Value::Str(rep.clone()));
                }
                if let Some(rp) = a.replaces.as_ref().filter(|r| !r.is_empty()) {
                    detail.insert("replaces".into(), Value::Str(rp.clone()));
                }
                if !a.why.trim().is_empty() {
                    detail.insert("why".into(), Value::Str(a.why.clone()));
                }
                self.commit(&who, "record", Obj::Run(run.clone()), detail)?;
                if let Some(mut rep) = replaced {
                    if rep.retracted.is_none() {
                        let why_t = format!("replaced by {}: {}", run.id, crate::rbyaml::py_strip(&a.why));
                        rep.retracted = Some(nested(crate::dict! {"at" => now(), "by" => who.id.clone(), "why" => why_t.clone(), "words" => self.words.clone()}, Retraction::validate)?);
                        self.commit(&who, "retract", Obj::Run(rep), crate::dict! {"why" => why_t, "replaced_by" => run.id.clone()})?;
                    }
                }
                Ok(Ok((exp, run, notices)))
            })?;
            match r {
                Ok(t) => t,
                Err(dup) => return Ok(dup),
            }
        };
        if a.repeats.is_none() && !a.per_run.is_empty() {
            let given = Value::Dict(a.per_run.clone());
            let same = self.live::<Run>()?.into_iter().find(|r| {
                r.experiment == exp.id && r.id != run.id && Value::Dict(r.per_run.clone()) == given && r.spec_sha256 == run.spec_sha256 && variants_of(r) == variants_of(&run)
            });
            if let Some(same) = same {
                if self.unit_setting(&exp).is_none() {
                    let pairs: Vec<String> = a.per_run.iter().map(|(k, v)| format!("{k}={}", show(v))).collect();
                    notices.insert(
                        0,
                        format!("{} has the same {} as {}: the two are one unit, and their numbers are averaged in it", run.id, pairs.join(", "), same.id),
                    );
                }
            }
        }
        // one row is one run: the same row read in another experiment counts there too
        if let Some(row) = &a.row {
            for other in self.live::<Run>()?.iter().filter(|r| r.experiment != exp.id && r.retracted.is_none() && row.is_read_by(r)) {
                let place = format!("{}, table {}{}", row.path, row.table, row.rowid.map(|r| format!(", rowid {r}")).unwrap_or_default());
                notices.push(format!("{place} is also recorded as {} in {}: one row is one run, and it counts in both {} and {}", other.id, other.experiment, other.experiment, exp.id));
            }
        }
        for line in super::support::told_after_add(&run, a.produced_at.is_some(), &a.numbers_from) {
            crate::plain::tell(line);
        }
        let warning = if notices.is_empty() { None } else { Some(notices.join("; ")) };
        Ok(Added { record: run, duplicate_of: None, warning })
    }

    /// A file a run or an analysis names, as a FileRef.
    fn file_ref_of(&self, f: &str) -> Res<FileRef> {
        let f = &crate::sys::typed(f);
        if !sources::full_path(&self.root, f).is_file() {
            return Err(RBError::msg("E_FILE_NOT_FOUND", format!("No such file: {f}")));
        }
        match sources::file_ref(&self.root, f) {
            Ok(d) => build_with(d, FileRef::validate),
            Err(e) => Err(RBError::could_not_read(&e, f)),
        }
    }

    /// When the output existed: what the recorder says, else the earliest time rb can vouch for, with what it rests on;
    /// and when the files its numbers were read from (`numbers_from`) existed: each one's earliest sign of its bytes, the
    /// journal's first sighting, git's first commit or its own last write, the latest of them, none while one has none.
    /// The journal and git saw bytes a run already recorded (`earlier`: path and sha256) for that run: they say when the
    /// output existed, not when this one was made. A database a row was read from (`database`) says when its bytes
    /// existed and never when the run was made: its times are its last write's, not its row's, and with nothing else
    /// to date it, when the run was made is unknown.
    fn produced(&self, given: Option<&str>, refs: &[FileRef], numbers_from: &[String], earlier: &HashSet<(String, String)>, database: Option<&str>) -> Res<(Option<String>, String, Option<(String, &'static str)>)> {
        let given = match given {
            Some(g) if crate::rbyaml::py_strip(g).to_lowercase() == "unknown" => Some(None),
            Some(g) => match norm_time(g) {
                Some(t) => Some(Some(t)),
                None => return Err(RBError::msg("E_OBJECT_INVALID", format!("--produced-at takes an ISO date or time, or unknown (got {}).", repr_str(g)))),
            },
            None => None,
        };
        let mut found: Vec<(String, &'static str)> = Vec::new();
        let mut own: Option<Option<(String, &'static str)>> = None;
        let seen = self.journal_first_seen();
        for f in refs {
            let mut signs: Vec<(String, &'static str)> = Vec::new();
            if let Some(hit) = seen.get(&f.path) {
                if hit.truthy() && hit.get("sha").and_then(|v| v.as_str()) == Some(f.sha256.as_str()) && hit.get("at").map_or(false, |v| v.truthy()) {
                    if let Some(t) = norm_time(&pyjson::py_str(hit.get("at").unwrap())) {
                        signs.push((t, "journal"));
                    }
                }
            }
            if let Some(c) = sources::first_committed(&self.root, &f.path) {
                signs.push((c, "git"));
            }
            // a second execution (--again, --repeats) that wrote those bytes again was made when its file was written
            let own_from = if earlier.contains(&(f.path.clone(), f.sha256.clone())) { signs.len() } else { 0 };
            if let Some(m) = crate::pytime::file_time(&self.local(&f.path)) {
                signs.push((m, "file"));
            }
            if numbers_from.contains(&f.path) {
                let first = signs[own_from..].iter().min_by(|a, b| a.0.cmp(&b.0)).cloned();
                own = Some(match (own, first) {
                    (Some(Some(a)), Some(b)) => Some(if b.0 > a.0 { b } else { a }),
                    (None, b) => b,
                    _ => None,
                });
            }
            if database != Some(f.path.as_str()) {
                found.extend(signs);
            }
        }
        // a time given says when the run was made; the file its numbers were read from still says when it was written,
        // which tells a file written after the run from the run's own output
        match given {
            Some(None) => return Ok((None, "unknown".into(), own.flatten())),
            Some(Some(t)) => return Ok((Some(t), "reported".into(), own.flatten())),
            None => {}
        }
        if found.is_empty() {
            return Ok((None, if database.is_some() { "unknown" } else { "recorded" }.into(), own.flatten().filter(|_| database.is_some())));
        }
        found.sort_by(|a, b| a.0.cmp(&b.0));
        Ok((Some(found[0].0.clone()), found[0].1.to_string(), own.flatten()))
    }

    /// The journal's first sighting of each watched file with its current bytes: {path: {sha, at}}.
    fn journal_first_seen(&self) -> Dict {
        match crate::journal::load_state(&self.root).get("files") {
            Some(Value::Dict(f)) => f.clone(),
            _ => Dict::new(),
        }
    }

    /// Each resource a run names, resolved as it stands at this moment.
    fn uses(&self, pairs: &[(String, String)], flag: &str, warnings: &mut Vec<String>) -> Res<Vec<ResourceUse>> {
        let mut out = Vec::new();
        for (rid, role) in pairs {
            if self.kind_of(rid)? != Some("resource") {
                return Err(RBError::msg(
                    "E_OBJECT_NOT_FOUND",
                    format!("{flag} {rid}: no resource {} here. Record it first: rb resource add {rid} --type dataset --name \"...\"", repr_str(rid)),
                ));
            }
            let res: Resource = self.load(rid)?;
            if let Some(r) = &res.retracted {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{flag} {rid}: resource {rid} was retracted ({}).", r.why)));
            }
            let av = self.availability(&res);
            let here = av.get("locations").and_then(|l| l.as_list()).and_then(|l| {
                l.iter().find(|x| x.get_str("provider") == Some("local") && x.get_str("state") != Some("gone")).map(|x| self.local(x.get_str("ref").unwrap_or("")))
            });
            let sha256 = here.as_ref().and_then(|h| self.hash(h));
            if av.get_str("state") == Some("changed") && flag == "--input" {
                warnings.push(format!(
                    "{rid}: the copy here is not the bytes {rid} names ({}); the run records what rb found",
                    av.get("identity").and_then(|i| i.get_str("text")).unwrap_or("")
                ));
            }
            out.push(nested(
                crate::dict! {"resource" => rid.clone(), "role" => role.clone(), "availability" => av.get("state").cloned().unwrap_or(Value::Null), "sha256" => sha256},
                ResourceUse::validate,
            )?);
        }
        Ok(out)
    }

    fn metric_key_for(&self, exp: &Experiment, name: &str, variant: Option<&str>) -> String {
        let Some(variant) = variant else { return name.to_string() };
        if name.starts_with("change.") {
            return name.to_string();
        }
        let head = name.split('.').next().unwrap_or("");
        if exp.variant(head).is_some() || ROLES.contains(&head) {
            return name.to_string();
        }
        format!("{variant}.{name}")
    }

    /// Compare the spec with the run's own resolved config: did the run use what the spec says?
    fn config_check(&self, exp: &Experiment, variant: Option<&str>, config: Option<&str>, given: Option<&Dict>) -> Res<Option<ConfigCheck>> {
        let Some(cfg) = config else {
            return Ok(None);
        };
        let cfg = &crate::sys::typed(cfg);
        let (flat, label, sha256) = {
            let p = if Path::new(cfg).is_absolute() { PathBuf::from(cfg) } else { self.root.join(cfg) };
            let ps = crate::sys::slashed(&p);
            let Some(kind) = sources::structured_kind(&ps) else {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("--config {cfg}: {ps} is not YAML, JSON or TOML")));
            };
            let bytes = match std::fs::read(&p) {
                Ok(b) => b,
                Err(e) => return Err(RBError::could_not_read(&e, cfg)),
            };
            let doc = sources::parse_structured(&String::from_utf8_lossy(&bytes), kind, &ps).map_err(|u| RBError::msg("E_OBJECT_INVALID", format!("--config {cfg}: {}", u.reason)))?;
            let doc = sources::unwrap_wandb(doc);
            (sources::flatten(&doc, ""), cfg.to_string(), Some(sha256_bytes(&bytes)))
        };
        let v = variant.filter(|v| !v.is_empty()).and_then(|v| exp.variant(v));
        // population.* says how the runs are counted, not what a run was given
        let run_setting = |x: &&Setting| x.retracted.is_none() && !x.per_run && crate::record::config_compares(&x.name);
        // an env.* setting is a run's only under its own key (an RL config's env.name), never a key that shares its last name
        let ran_with = |name: &str| if crate::record::by_full_key(name) { flat.get(name).map_or((Value::Null, false), |x| (x.clone(), true)) } else { config_value(&flat, name) };
        let mut names: std::collections::BTreeSet<String> = exp.settings.iter().filter(run_setting).map(|x| x.name.clone()).collect();
        if let Some(v) = v {
            names.extend(v.settings.iter().filter(run_setting).map(|x| x.name.clone()));
        }
        let mut check = ConfigCheck { path: label, sha256, matches: Vec::new(), mismatches: Vec::new(), absent: Vec::new() };
        for name in &names {
            let spec_value = match v {
                Some(v) => exp.value_in(v, name),
                None => exp.setting(name).map(|s| s.value.clone()).unwrap_or(Value::Null),
            };
            if spec_value.is_null() {
                continue;
            }
            let (ran, found) = ran_with(name);
            if !found {
                if !crate::record::by_full_key(name) {
                    check.absent.push(name.clone());
                }
                continue;
            }
            if sources::equal(&spec_value, &ran) {
                check.matches.push(name.clone());
            } else {
                check.mismatches.push(crate::dict! {"name" => name.clone(), "spec" => spec_value, "ran" => ran});
            }
        }
        let mut said: Vec<(&String, &Value)> = given.map(|g| g.iter().collect()).unwrap_or_default();
        said.sort_by(|a, b| a.0.cmp(b.0));
        for (name, said) in said.into_iter().filter(|(n, _)| crate::record::config_compares(n)) {
            let (ran, found) = ran_with(name);
            if found {
                if sources::equal(said, &ran) {
                    check.matches.push(name.clone());
                } else {
                    check.mismatches.push(crate::dict! {"name" => name.clone(), "spec" => said.clone(), "ran" => ran});
                }
            }
        }
        Ok(Some(check))
    }

    // ------------------------------------------------------------ resources: identity, location, availability

    /// Record what a resource is: its identity, where its bytes are, and how to get it again.
    pub fn add_resource(&self, a: &NewResource) -> Res<Resource> {
        let who = self.actor()?;
        self.locked(|| {
            let at = now();
            let mut locs: Vec<Location> = Vec::new();
            for (p, r) in &a.locations {
                locs.push(self.location(p, r, &who, &at, "")?);
            }
            let mut sha256 = a.sha256.clone();
            if let Some(fp) = &a.from_path {
                let fp = &crate::sys::typed(fp);
                // the file named from where the command runs, as its location is (a subdirectory, a nested worktree)
                let full = sources::full_path(&std::env::current_dir().unwrap_or_else(|_| self.root.clone()), fp);
                if !full.is_file() {
                    return Err(RBError::msg("E_FILE_NOT_FOUND", format!("No such file: {fp}")));
                }
                let r = sources::file_ref(&self.root, &crate::sys::slashed(&full)).map_err(|e| RBError::could_not_read(&e, fp))?;
                let rsha = r.get_str("sha256").unwrap_or("").to_string();
                if let Some(s) = sha256.as_ref().filter(|s| !s.is_empty()) {
                    if *s != rsha {
                        return Err(RBError::msg("E_OBJECT_INVALID", format!("--sha256 says {}, but {fp} hashes to {}.", truncate(s, 12), truncate(&rsha, 12))));
                    }
                }
                sha256 = Some(rsha);
                locs.insert(0, self.location("local", &crate::sys::slashed(&full), &who, &at, "")?);
            }
            let acq = if !a.source.is_empty() || a.url.as_ref().map_or(false, |u| !u.is_empty()) || a.recipe.as_ref().map_or(false, |r| !r.is_empty()) {
                Some(nested(crate::dict! {"source" => a.source.clone(), "url" => a.url.clone(), "recipe" => a.recipe.clone()}, Acquisition::validate)?)
            } else {
                None
            };
            let res: Resource = build(crate::dict! {
                "id" => self.new_id("resource", Some(&a.id))?, "uid" => new_uid("res"), "type" => a.r#type.clone(), "name" => a.name.clone(),
                "version" => a.version.clone(), "subset" => a.subset.clone(), "sha256" => sha256, "digest" => a.digest.clone(),
                "acquisition" => acq.as_ref().map(|x| x.to_value()), "locations" => Value::List(locs.iter().map(|l| l.to_value()).collect()),
                "note" => a.note.clone(), "created_by" => who.id.clone(), "created_at" => at.clone(), "via" => self.via.clone()
            })?;
            self.commit(&who, "add", Obj::Resource(res.clone()), crate::dict! {"type" => a.r#type.clone(), "name" => a.name.clone()})?;
            Ok(res)
        })
    }

    /// Add a place a resource's bytes are (or were).
    pub fn locate_resource(&self, id: &str, provider: &str, r#ref: &str, note: &str) -> Res<Resource> {
        let who = self.actor()?;
        self.locked(|| {
            let mut res: Resource = self.load(id)?;
            if let Some(r) = &res.retracted {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("Resource {} was retracted ({}).", res.id, r.why)));
            }
            let loc = self.location(provider, r#ref, &who, &now(), note)?;
            if res.locations.iter().any(|x| x.provider == loc.provider && x.r#ref == loc.r#ref) {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{} already records {provider}:{}.", res.id, loc.r#ref)));
            }
            let lref = loc.r#ref.clone();
            res.locations.push(loc);
            self.commit(&who, "locate", Obj::Resource(res.clone()), crate::dict! {"provider" => provider, "ref" => lref})?;
            Ok(res)
        })
    }

    fn location(&self, provider: &str, r#ref: &str, who: &Actor, at: &str, note: &str) -> Res<Location> {
        let mut r = r#ref.to_string();
        if provider == "local" {
            let mut p = PathBuf::from(r#ref);
            if !p.is_absolute() {
                p = std::env::current_dir().unwrap_or_default().join(p);
            }
            let resolved = sources::resolve_path(&p);
            r = match resolved.strip_prefix(&self.root) {
                Ok(rel) => rel.to_string_lossy().replace('\\', "/"),
                Err(_) => crate::sys::slashed(&resolved),
            };
            if r.is_empty() {
                r = ".".into();
            }
        }
        nested(crate::dict! {"provider" => provider, "ref" => r, "note" => note, "by" => who.id.clone(), "at" => at}, Location::validate)
    }

    /// A local file's sha256, remembered by size and modification time in cache/; None for a directory or a file that
    /// cannot be read.
    pub fn hash(&self, p: &Path) -> Option<String> {
        if !p.is_file() {
            return None;
        }
        sources::sha256_file(p, Some(&self.dir.join("cache").join("hashes.json"))).ok()
    }

    /// Whether the file at `p` is the one `sha` was taken of: the same bytes, or the same text as another system's
    /// git checks it out (sources::is_file_of).
    pub fn holds(&self, p: &Path, sha: &str) -> bool {
        match self.hash(p) {
            Some(h) => h == sha || sources::file_is_twin_of(p, sha),
            None => false,
        }
    }

    pub fn local(&self, r#ref: &str) -> PathBuf {
        let p = Path::new(r#ref);
        if p.is_absolute() {
            p.to_path_buf()
        } else {
            self.root.join(r#ref)
        }
    }

    /// How exactly a resource is named, as recorded.
    pub fn identity(res: &Resource) -> Dict {
        if let Some(s) = res.sha256.as_ref().filter(|s| !s.is_empty()) {
            return crate::dict! {"level" => "exact", "text" => format!("sha256 {}", truncate(s, 12))};
        }
        if !res.digest.is_empty() {
            return crate::dict! {"level" => "exact", "text" => format!("digest {}", res.digest)};
        }
        if !res.version.is_empty() {
            return crate::dict! {"level" => "version", "text" => format!("version {}", res.version)};
        }
        crate::dict! {"level" => "name", "text" => "name only: no hash, digest or version recorded"}
    }

    /// Where a resource stands now, computed, never stored.
    pub fn availability(&self, res: &Resource) -> Dict {
        let mut checked: Vec<Value> = Vec::new();
        let (mut present, mut changed) = (false, false);
        for loc in &res.locations {
            if loc.provider != "local" {
                checked.push(crate::obj! {"provider" => loc.provider.clone(), "ref" => loc.r#ref.clone(), "state" => "recorded"});
                continue;
            }
            let p = self.local(&loc.r#ref);
            if !p.exists() {
                checked.push(crate::obj! {"provider" => "local", "ref" => loc.r#ref.clone(), "state" => "gone"});
            } else if res.sha256.as_ref().map_or(false, |s| !s.is_empty() && p.is_file() && !self.holds(&p, s)) {
                checked.push(crate::obj! {"provider" => "local", "ref" => loc.r#ref.clone(), "state" => "different bytes"});
                changed = true;
            } else {
                checked.push(crate::obj! {"provider" => "local", "ref" => loc.r#ref.clone(), "state" => "here"});
                present = true;
            }
        }
        let remote = checked.iter().any(|c| c.get_str("state") == Some("recorded"));
        let acq = res.acquisition.as_ref();
        let nonempty = |o: &Option<String>| o.as_ref().map_or(false, |s| !s.is_empty());
        let recipe = acq.map_or(false, |a| nonempty(&a.url) || nonempty(&a.recipe) || !a.source.is_empty());
        let state = if present {
            "present"
        } else if changed {
            "changed"
        } else if remote {
            "remote"
        } else if recipe {
            "reconstructible"
        } else {
            "lost"
        };
        let how = match acq {
            Some(a) if state != "present" && recipe => {
                if nonempty(&a.recipe) {
                    a.recipe.clone().unwrap()
                } else if nonempty(&a.url) {
                    a.url.clone().unwrap()
                } else {
                    a.source.clone()
                }
            }
            _ => String::new(),
        };
        crate::dict! {"state" => state, "locations" => Value::List(checked), "identity" => Value::Dict(Self::identity(res)), "how" => how}
    }

    /// A file a run named: `here` (same bytes), `different bytes`, or `gone`.
    pub fn file_state(&self, r: &FileRef) -> &'static str {
        let p = self.local(&r.path);
        if !p.exists() {
            return "gone";
        }
        match self.hash(&p) {
            None => "gone",
            Some(h) if h == r.sha256 || sources::file_is_twin_of(&p, &r.sha256) => "here",
            Some(_) => "different bytes",
        }
    }

    /// What a rerun of this experiment would need, and where each piece stands now.
    /// Where the code of a set of runs stands: each run's exact code is here when its tree is (a snapshot of uncommitted
    /// changes, or the tree of its commit, both kept under refs/rb/snapshots/) or, for a clean tree, when its commit is.
    pub fn code_row(&self, attached: &[Dict]) -> Dict {
        let get = |a: &Dict, k: &str| -> Value { a.get(k).cloned().unwrap_or(Value::Null) };
        let mut commits: Vec<String> = attached.iter().map(|a| get(a, "commit")).filter(|v| v.truthy()).map(|v| pyjson::py_str(&v)).collect();
        commits.sort();
        commits.dedup();
        let trees: Vec<String> = attached.iter().map(|a| get(a, "tree")).filter(|v| v.truthy()).map(|v| pyjson::py_str(&v)).collect();
        if commits.is_empty() && trees.is_empty() {
            return crate::dict! {"what" => "code", "state" => "missing", "detail" => "no commit recorded: the runs were recorded outside git or before the first commit"};
        }
        let has = |obj: &str, kind: &str| sources::git(&self.root, &["cat-file", "-e", &format!("{obj}^{{{kind}}}")]).is_some();
        let present: HashMap<String, bool> = commits.iter().map(|c| (c.clone(), has(c, "commit"))).collect();
        let mut kept: HashMap<String, bool> = HashMap::new();
        for t in &trees {
            if !kept.contains_key(t) {
                kept.insert(t.clone(), has(t, "tree"));
            }
        }
        let tree_kept = |a: &Dict| get(a, "tree").truthy() && kept.get(&pyjson::py_str(&get(a, "tree"))).copied().unwrap_or(false);
        let here = |a: &Dict| -> bool {
            if get(a, "tree").truthy() {
                return tree_kept(a) && !get(a, "left_out").truthy();
            }
            let c = get(a, "commit");
            c.truthy() && !get(a, "dirty").truthy() && present.get(&pyjson::py_str(&c)).copied().unwrap_or(false)
        };
        let n = attached.iter().filter(|a| here(a)).count();
        let absent: Vec<&String> = commits
            .iter()
            .filter(|c| !present[*c] && !attached.iter().filter(|a| get(a, "commit").as_str() == Some(c.as_str())).all(|a| here(a)))
            .collect();
        let snapshots = attached.iter().any(|a| get(a, "dirty").truthy() && tree_kept(a));
        let lost = attached.iter().any(|a| get(a, "dirty").truthy() && !tree_kept(a));
        let left_out = attached.iter().any(|a| get(a, "left_out").truthy());
        let short = |c: &str| truncate(c, 10);
        let mut detail = if commits.is_empty() {
            "no commit".to_string()
        } else {
            format!("commit {}", crate::assess::first_three(&commits.iter().map(|c| short(c)).collect::<Vec<_>>(), ", "))
        };
        if !absent.is_empty() {
            detail.push_str(&format!(" · not in this repository: {} (get the code repository to rerun)", crate::assess::first_three(&absent.iter().map(|c| short(c)).collect::<Vec<_>>(), ", ")));
        }
        if snapshots {
            detail.push_str(" · uncommitted changes kept as git trees");
        }
        if lost {
            detail.push_str(" · the tree had uncommitted changes when a run was recorded");
        }
        if left_out {
            detail.push_str(" · files left out of a snapshot (over 10 MB, or too many)");
        }
        let state = if n == attached.len() { "ok" } else if n == 0 { "missing" } else { "partial" };
        crate::dict! {"what" => "code", "state" => state, "detail" => detail}
    }

    pub fn rerunnability(&self, exp: &Experiment) -> Res<Vec<Dict>> {
        let runs: Vec<Run> = self.standing_runs()?.iter().filter(|r| r.experiment == exp.id).cloned().collect();
        let mut rows: Vec<Dict> = Vec::new();
        if runs.is_empty() {
            return Ok(rows);
        }
        // a run made before it was recorded has the recorder's code and machine on its receipt: its code is the
        // commit it names (--commit, --tree), unknown without one, and its environment is unknown
        let tree_of = |x: &str| sources::git(&self.root, &["rev-parse", "--verify", "-q", &format!("{x}^{{tree}}")]).map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        let named = |r: &Run| -> Dict {
            let mut d: Dict = ["commit", "tree"].iter().filter_map(|k| r.receipt.produced.get(*k).filter(|v| v.truthy()).map(|v| (k.to_string(), v.clone()))).collect();
            // a tree that is not its commit's holds the uncommitted changes the run was made with; one named in full is
            // asked after as code_row asks after it, so git answers once
            let here = |t: String| -> Option<String> {
                if (t.len() == 40 || t.len() == 64) && t.bytes().all(|b| b.is_ascii_hexdigit()) {
                    sources::git(&self.root, &["cat-file", "-e", &format!("{t}^{{tree}}")]).map(|_| t.to_lowercase())
                } else {
                    tree_of(&t)
                }
            };
            if let Some(tree) = d.get("tree").map(pyjson::py_str).and_then(here) {
                let commit = d.get("commit").map(pyjson::py_str);
                if commit.as_deref() != Some(tree.as_str()) && commit.and_then(|c| tree_of(&c)).as_deref() != Some(tree.as_str()) {
                    d.insert("dirty".into(), Value::Bool(true));
                }
            }
            d
        };
        let before: Vec<bool> = runs.iter().map(made_before_recorded).collect();
        let code: Vec<Dict> = runs
            .iter()
            .zip(&before)
            .filter_map(|(r, b)| if *b { Some(named(r)).filter(|c| !c.is_empty()) } else { Some(r.receipt.attached.clone().unwrap_or_default()) })
            .collect();
        let envs: Vec<&Dict> = runs.iter().zip(&before).filter(|(r, b)| !**b && !r.receipt.environment.is_empty()).map(|(r, _)| &r.receipt.environment).collect();
        let n = runs.len();
        // one run is the run, two are both, more are all of them
        let said = |k: usize, one: &str, all: &str, both: &str, some_one: &str, some: &str| match (k == n, k == 1) {
            (true, true) => one.to_string(),
            (true, false) if n == 2 => both.to_string(),
            (true, false) => all.to_string(),
            (false, true) if n == 2 => format!("one of the two runs {some_one}"),
            (false, true) => format!("1 of {n} runs {some_one}"),
            (false, false) => format!("{k} of {n} runs {some}"),
        };
        let unnamed = n - code.len();
        let code_why = said(
            unnamed,
            "the run was made before it was recorded, and names no commit",
            "the runs were made before they were recorded, and none names its commit",
            "both runs were made before they were recorded, and neither names its commit",
            "was made before it was recorded and names no commit",
            "were made before they were recorded and name no commit",
        );
        let earlier = before.iter().filter(|b| **b).count();
        let env_why = said(
            earlier,
            "the run was made before it was recorded, in an environment rb did not see",
            "the runs were made before they were recorded, in environments rb did not see",
            "both runs were made before they were recorded, in environments rb did not see",
            "was made before it was recorded, in an environment rb did not see",
            "were made before they were recorded, in environments rb did not see",
        );
        let partly = |mut row: Dict, k: usize, why: &str| {
            if k > 0 {
                if row.get_str("state") == Some("ok") {
                    row.insert("state".into(), Value::Str("partial".into()));
                }
                let detail = format!("{} · {why}", row.get_str("detail").unwrap_or(""));
                row.insert("detail".into(), Value::Str(detail));
            }
            row
        };
        if code.is_empty() {
            rows.push(crate::dict! {"what" => "code", "state" => "unknown", "detail" => code_why.clone()});
        } else {
            rows.push(partly(self.code_row(&code), unnamed, &code_why));
        }
        if let Some(last) = envs.last() {
            let keys: Vec<&str> = ["python", "torch", "numpy", "jax", "tensorflow", "cuda"].into_iter().filter(|k| last.contains_key(*k)).collect();
            let d = keys.iter().map(|k| format!("{k} {}", pyjson::py_str(&last[*k]))).collect::<Vec<_>>().join(" · ");
            let row = crate::dict! {"what" => "environment", "state" => "ok", "detail" => if d.is_empty() { "fingerprint recorded".to_string() } else { d }};
            rows.push(partly(row, earlier, &env_why));
        } else if earlier > 0 {
            rows.push(crate::dict! {"what" => "environment", "state" => "unknown", "detail" => env_why.clone()});
        }
        let mut hosts: Vec<String> = runs
            .iter()
            .filter(|r| !r.host.is_empty())
            .map(|r| {
                let mut kv: Vec<(&String, &String)> = r.host.iter().collect();
                kv.sort();
                kv.iter().map(|(k, v)| format!("{k}={v}")).collect::<Vec<_>>().join(", ")
            })
            .collect();
        hosts.sort();
        hosts.dedup();
        if !hosts.is_empty() {
            rows.push(crate::dict! {"what" => "machine", "state" => "ok", "detail" => crate::assess::first_three(&hosts, "; ")});
        }
        let mut seen: IndexMap<String, Vec<&ResourceUse>> = IndexMap::new();
        for r in &runs {
            for u in &r.inputs {
                seen.entry(u.resource.clone()).or_default().push(u);
            }
        }
        for (rid, uses) in &seen {
            let role = uses.iter().find(|u| !u.role.is_empty()).map(|u| u.role.clone()).unwrap_or_default();
            let res: Resource = match self.load(rid) {
                Ok(r) => r,
                Err(_) => {
                    rows.push(crate::dict! {"what" => if role.is_empty() { "resource".to_string() } else { role.clone() }, "state" => "missing", "resource" => rid.clone(),
                    "detail" => format!("{rid}: no such resource record")});
                    continue;
                }
            };
            let av = self.availability(&res);
            let avs = av.get_str("state").unwrap_or("").to_string();
            let mut state = match avs.as_str() {
                "present" => "ok",
                "changed" => "partial",
                "remote" => "ok",
                "reconstructible" => "partial",
                _ => "missing",
            };
            let label = format!("{}{}", res.name, if res.version.is_empty() { String::new() } else { format!(" {}", res.version) });
            let how = av.get_str("how").unwrap_or("");
            let mut detail = format!("{label}: {avs}{}", if how.is_empty() { String::new() } else { format!(" ({how})") });
            if let Some(rt) = &res.retracted {
                state = "missing";
                detail = format!("{label}: retracted ({})", rt.why);
            }
            let read: std::collections::BTreeSet<String> = uses.iter().filter_map(|u| u.sha256.clone().filter(|s| !s.is_empty())).collect();
            let here = av.get("locations").and_then(|l| l.as_list()).and_then(|l| {
                l.iter().find(|x| x.get_str("provider") == Some("local") && x.get_str("state") != Some("gone")).map(|x| self.local(x.get_str("ref").unwrap_or("")))
            });
            if let Some((copy, ns)) = here.as_ref().and_then(|p| self.hash(p).map(|h| (p, h))) {
                if !read.is_empty() && !read.contains(&ns) && !read.iter().any(|r| sources::file_is_twin_of(copy, r)) {
                    state = "partial";
                    let mut shorts: Vec<String> = read.iter().map(|x| truncate(x, 12)).collect();
                    shorts.sort();
                    detail.push_str(&format!(" · the copy here is not the bytes the runs read ({})", shorts.join(", ")));
                }
            }
            if av.get("identity").and_then(|i| i.get_str("level")) == Some("name") {
                detail.push_str(" · identity: name only");
            }
            rows.push(crate::dict! {"what" => if role.is_empty() { res.r#type.clone() } else { role }, "state" => state, "resource" => res.id.clone(), "detail" => detail});
        }
        let mut files: IndexMap<String, &FileRef> = IndexMap::new();
        let mut named_by: HashMap<String, &Run> = HashMap::new();
        for r in &runs {
            for f in &r.files {
                files.entry(f.path.clone()).or_insert(f);
                named_by.entry(f.path.clone()).or_insert(r);
            }
        }
        if !files.is_empty() {
            let states: Vec<(&String, &str)> = files.iter().map(|(p, f)| (p, self.file_state(f))).collect();
            // a file that is here with other bytes is changed, never missing
            let of = |want: &dyn Fn(&str) -> bool| -> Vec<&String> { states.iter().filter(|(_, s)| want(s)).map(|(p, _)| *p).collect() };
            let (gone, changed) = (of(&|s| s == "gone"), of(&|s| s != "gone" && s != "here"));
            let here = states.len() - gone.len() - changed.len();
            let state = if gone.is_empty() && changed.is_empty() {
                "ok"
            } else if here > 0 {
                "partial"
            } else if gone.is_empty() {
                "changed"
            } else {
                "missing"
            };
            let mut detail = format!("{} the runs named {} here unchanged", crate::assess::k_of_n(here, states.len(), "file", "files"), if here <= 1 { "is" } else { "are" });
            let named = |ps: &[&String]| -> Vec<String> { ps.iter().map(|s| crate::view::named_file(self, s, named_by.get(*s).copied())).collect() };
            if !changed.is_empty() {
                detail.push_str(&format!("; changed since recorded: {}", crate::assess::first_three(&named(&changed), ", ")));
            }
            if !gone.is_empty() {
                detail.push_str(&format!("; not here: {}", crate::assess::first_three(&named(&gone), ", ")));
            }
            rows.push(crate::dict! {"what" => "files", "state" => state, "detail" => detail});
        }
        Ok(rows)
    }

    /// Record that someone looked at a dataset in a way that can shape what they declare next.
    pub fn expose_resource(&self, resource_id: &str, how: &str, why: &str, at: Option<&str>) -> Res<Resource> {
        let who = self.actor()?;
        self.locked(|| {
            let mut res: Resource = self.load(resource_id)?;
            if let Some(r) = &res.retracted {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("Resource {} was retracted ({}).", res.id, r.why)));
            }
            let mut when: Option<String> = Some(now());
            if let Some(a) = at {
                if crate::rbyaml::py_strip(a).to_lowercase() == "unknown" {
                    when = None;
                } else {
                    match norm_time(a) {
                        Some(t) => when = Some(t),
                        None => return Err(RBError::msg("E_OBJECT_INVALID", format!("--at takes an ISO date or time, or unknown (got {}).", repr_str(a)))),
                    }
                }
            }
            res.exposures.push(nested(
                crate::dict! {"how" => how, "at" => when.clone(), "by" => who.id.clone(), "recorded_at" => now(), "reported" => at.is_some(), "why" => why},
                Exposure::validate,
            )?);
            self.commit(&who, "expose", Obj::Resource(res.clone()), crate::dict! {"how" => how, "at" => when})?;
            Ok(res)
        })
    }

    // ------------------------------------------------------------ analyses: computations over saved outputs

    /// Record an analysis and the comparisons it produced.
    pub fn add_analysis(&self, experiment_id: &str, a: NewAnalysis) -> Res<Added<Analysis>> {
        let who = self.actor()?;
        let mut source = a.source.clone();
        let (analysis, mut notices) = {
            let r = self.locked(|| -> Res<Result<(Analysis, Vec<String>), Added<Analysis>>> {
                let exp: Experiment = self.load(experiment_id)?;
                writable(&exp)?;
                let read_ids = ordered_unique(a.reads.clone());
                for rid in &read_ids {
                    if !matches!(self.kind_of(rid)?, Some("run") | Some("analysis")) {
                        return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("--reads {rid}: no run or analysis {} here.", repr_str(rid))));
                    }
                }
                let old = ordered_unique(a.supersedes.clone());
                for aid in &old {
                    if self.kind_of(aid)? != Some("analysis") {
                        return Err(RBError::msg("E_OBJECT_NOT_FOUND", format!("--supersedes {aid}: no analysis {} here.", repr_str(aid))));
                    }
                    if self.load::<Analysis>(aid)?.retracted.is_some() {
                        return Err(RBError::msg(
                            "E_OBJECT_INVALID",
                            format!("--supersedes {aid}: it was retracted; a retracted analysis is not read, so there is nothing to supersede."),
                        ));
                    }
                }
                if !old.is_empty() && crate::rbyaml::py_strip(&a.why).is_empty() {
                    return Err(RBError::msg("E_OBJECT_INVALID", "Superseding an analysis needs the reason: --why \"...\"."));
                }
                if a.comparisons.is_empty() {
                    return Err(RBError::msg("E_OBJECT_INVALID", "An analysis records the comparisons it produced: --compare, or --from a file that lists them."));
                }
                let mut refs: Vec<FileRef> = Vec::new();
                for f in &a.files {
                    refs.push(self.file_ref_of(f)?);
                }
                if a.computed && refs.is_empty() {
                    return Err(RBError::msg("E_OBJECT_INVALID", "A computed analysis names the output file rb read its numbers from."));
                }
                if let Some(s) = source.as_mut() {
                    s.resolved = None;
                }
                if let Some(s) = &source {
                    if s.kind != "file" {
                        return Err(RBError::msg("E_OBJECT_INVALID", "A reported analysis's source is a file in the repository: the document that states the numbers."));
                    }
                }
                not_a_store_row(source.as_ref(), &self.root, "A reported analysis's source")?;
                let mut comps: Vec<Comparison> = Vec::new();
                for raw in &a.comparisons {
                    let mut c = raw.clone();
                    if !c.contains_key("unit") {
                        c.insert("unit".into(), Value::Str(a.unit.clone()));
                    }
                    if c.get("n").map_or(true, |v| v.is_null()) {
                        if let Some(u) = &a.units {
                            c.insert("n".into(), u.clone());
                        }
                    }
                    if c.get("lo").map_or(false, |v| !v.is_null()) && c.get("level").map_or(true, |v| v.is_null()) {
                        c.insert("level".into(), a.level.clone().unwrap_or(Value::Float(0.95)));
                    }
                    c.shift_remove("source");
                    let fields: Dict = c.into_iter().filter(|(_, v)| !v.is_null()).collect();
                    let mut comp: Comparison = nested(fields, Comparison::validate)?;
                    if exp.variant(&comp.subject).is_none() && ROLES.contains(&comp.subject.as_str()) && exp.by_role(&comp.subject).len() == 1 {
                        comp.subject = exp.by_role(&comp.subject)[0].name.clone();
                    }
                    if exp.variant(&comp.reference).is_none() && ROLES.contains(&comp.reference.as_str()) && exp.by_role(&comp.reference).len() == 1 {
                        comp.reference = exp.by_role(&comp.reference)[0].name.clone();
                    }
                    if let Some(s) = &source {
                        if !a.computed {
                            comp.source = Some(self.stated(s, &comp)?);
                        }
                    }
                    comps.push(comp);
                }
                let no_source = |c: &Comparison| {
                    let mut d = c.dump(false);
                    d.shift_remove("source");
                    Value::Dict(d)
                };
                let mine: Vec<Value> = comps.iter().map(no_source).collect();
                let mut ref_shas: Vec<String> = refs.iter().map(|r| r.sha256.clone()).collect();
                ref_shas.sort();
                let dup = self.live::<Analysis>()?.into_iter().find(|x| {
                    let mut fs: Vec<String> = x.files.iter().map(|f| f.sha256.clone()).collect();
                    fs.sort();
                    x.experiment == exp.id && x.comparisons.iter().map(no_source).collect::<Vec<_>>() == mine && fs == ref_shas && x.reads == read_ids
                });
                if let Some(d) = dup {
                    return Ok(Err(Added { duplicate_of: Some(d.id.clone()), record: d, warning: None }));
                }
                let mut notices: Vec<String> = Vec::new();
                let uses = self.uses(&a.inputs, "--input", &mut notices)?;
                let (produced, produced_basis, _) = self.produced(a.produced_at.as_deref(), &refs, &[], &HashSet::new(), None)?;
                let receipt = crate::obj! {
                    "at" => now(), "actor" => who.id.clone(), "actor_via" => who.via.clone(), "via" => self.via.clone(), "rb_version" => crate::version(),
                    "command" => a.command.clone(), "session" => actor::session_for(&who), "agent" => self.agent_facts(&who, true).map(Value::Dict),
                    "attached" => self.attached_state(sources::git_state(&self.here, DIR)).map(Value::Dict),
                    "environment" => Value::Dict(crate::environment::fingerprint(Some(&self.dir.join("cache")))),
                    "run_record" => Value::Null, "config" => Value::Null
                };
                let an: Analysis = build(crate::dict! {
                    "id" => self.new_id("analysis", a.id.as_deref())?, "uid" => new_uid("ana"), "experiment" => exp.id.clone(), "title" => a.title.clone(),
                    "reads" => str_list(&read_ids), "inputs" => Value::List(uses.iter().map(|u| u.to_value()).collect()), "method" => a.method.clone(),
                    "unit" => a.unit.clone(), "units" => a.units.clone(), "draws" => a.draws.clone(), "level" => a.level.clone(),
                    "basis" => if a.computed { "computed" } else { "reported" }, "files" => Value::List(refs.iter().map(|r| r.to_value()).collect()),
                    "source" => source.as_ref().map(|s| s.to_value()), "comparisons" => Value::List(comps.iter().map(|c| c.to_value()).collect()),
                    "supersedes" => str_list(&old), "why" => a.why.clone(), "produced_at" => produced, "produced_basis" => produced_basis,
                    "receipt" => receipt, "note" => a.note.clone(), "created_by" => who.id.clone(), "created_at" => now(), "via" => self.via.clone()
                })?;
                self.commit(&who, "analyze", Obj::Analysis(an.clone()), crate::dict! {"experiment" => exp.id.clone(), "comparisons" => comps.len(), "supersedes" => str_list(&old)})?;
                Ok(Ok((an, notices)))
            })?;
            match r {
                Ok(t) => t,
                Err(d) => return Ok(d),
            }
        };
        if source.is_none() && !a.computed {
            notices.insert(0, format!("{}'s numbers were typed with no document to check them against; every reading of them says so", analysis.id));
        }
        if let Some(g) = super::support::analysis_command(&analysis).filter(|g| !g.made_it()) {
            crate::plain::tell(format!(
                "--command is kept as given and reads as a command given later, not observed: {}. A command from a README, a paper or a script is a recipe: put it in a note with its file and line.",
                g.how
            ));
        }
        let warning = if notices.is_empty() { None } else { Some(notices.join("; ")) };
        Ok(Added { record: analysis, duplicate_of: None, warning })
    }

    /// The source, pinned to the line that states a reported comparison's numbers, with what rb read there.
    fn stated(&self, src: &Source, comp: &Comparison) -> Res<Source> {
        let numbers: Vec<f64> = [Some(comp.estimate), comp.lo, comp.hi].into_iter().flatten().collect();
        let path = src.path.clone().unwrap_or_else(|| "None".into());
        let (data, commit) = sources::read_file(&self.root, &path, src.commit.as_deref()).map_err(|u| RBError::msg(&u.code, format!("{}: {}", src.label(), u.reason)))?;
        let text = String::from_utf8_lossy(&data).into_owned();
        let lines = sources::split_lines(&text);
        let candidates: Vec<(usize, String)> = if let Some(l) = src.line {
            if l >= 1 && (l as usize) <= lines.len() {
                vec![(l as usize, lines[l as usize - 1].clone())]
            } else if l < 1 && !lines.is_empty() && (l as i64) >= -(lines.len() as i64) && l != 0 {
                // lines[src.line - 1] with a negative index counts from the end, as Python's does
                let idx = (lines.len() as i64 + l - 1) as usize;
                vec![(l as usize, lines[idx].clone())]
            } else {
                Vec::new()
            }
        } else if let Some(q) = src.quote.as_ref().filter(|q| !q.is_empty()) {
            sources::quote_places(&text, q).map(|idx| (text[..idx].matches('\n').count() + 1, q.clone())).collect()
        } else {
            lines.iter().enumerate().map(|(i, l)| (i + 1, l.clone())).collect()
        };
        let g = |x: f64| crate::pyfloat::fmt_float(x, if comp.reference.is_empty() { "g" } else { "+g" });
        let mut what = format!("{} {}", comp.metric, g(comp.estimate));
        if let Some(lo) = comp.lo {
            what.push_str(&format!(" [{}, {}]", g(lo), comp.hi.map(g).unwrap_or_else(|| "None".into())));
        }
        // a comment states nothing, as it sets no setting, where the document's type has comments
        let (comments, cut) = sources::stated_lines(&path, &lines);
        let mut commented = None;
        for (no, line) in candidates {
            let states = |text: &str| numbers.iter().all(|x| sources::states(text, &Value::Float(*x)));
            if no >= 1 && comments.get(no - 1).copied().unwrap_or(false) {
                if states(&line) {
                    commented = commented.or(Some(no));
                }
                continue;
            }
            // a comment that ends the line states nothing either
            let kept = if (1..=lines.len()).contains(&no) { cut[no - 1].clone() } else { line.clone() };
            if states(if sources::holds_quote(&kept, &line) { &line } else { &kept }) {
                let full = if no >= 1 && no <= lines.len() { lines[no - 1].clone() } else { line.clone() };
                let stripped: String = crate::rbyaml::py_strip(&full).chars().take(400).collect();
                let res = Resolution {
                    at: now(),
                    commit,
                    sha256: sha256_bytes(&data),
                    line: Some(no as i64),
                    text: if stripped.is_empty() { line.chars().take(400).collect() } else { stripped },
                    read: Value::Null,
                };
                let mut s = src.clone();
                s.line = Some(no as i64);
                s.resolved = Some(res);
                return Ok(s);
            }
        }
        let mut also = commented.map_or_else(String::new, |no| format!(" {path}:{no} is a comment, and a comment states nothing."));
        if src.line.is_none() && src.quote.as_ref().map_or(false, |q| !q.is_empty() && text.contains(q.as_str()) && !sources::holds_quote(&text, q)) {
            also.push_str(&format!(" The quote is in {path} only as part of a longer word or number."));
        }
        Err(RBError::msg_fix(
            "E_SOURCE_UNRESOLVED",
            format!("{} has no line that states {what}.{also}", src.label()),
            "Point --source at the document that states the numbers (a README, a report saved as text), with --quote for the exact row when it helps.",
        ))
    }

    // ------------------------------------------------------------ calls: decide, confirm, retract

    /// Record a call on a criterion (its finding), an experiment, a question, or a setting (e1/lr). `past` records a
    /// call made before it was recorded, with when it was made and the document that states it, which is checked here.
    /// In an agent session the call is proposed, not made, until it is confirmed (authority.rs).
    pub fn decide(&self, subject: &str, action: &str, why: &str, revisit: &str, past: Option<&PastGiven>) -> Res<Decision> {
        let past = match past {
            None => None,
            Some(p) => {
                let source = match &p.source {
                    None => None,
                    Some((path, line, quote)) => Some(self.call_source(path, *line, quote)?),
                };
                let when = crate::rbyaml::py_strip(&p.when).to_string();
                Some(Past { when: if when.is_empty() { "unknown".into() } else { when }, source })
            }
        };
        let words = self.words.clone();
        self.record_call(subject, action, why, revisit, &words, past, None)
    }

    /// Confirm a call proposed, or a past call mentioned, in an agent session: the same call (its subject, action,
    /// reason, revisit, words and when it was made) recorded as whoever confirms it, its log line naming what it
    /// confirms. Whether whoever confirms may is the command's to say: an agent session's confirmation establishes
    /// nothing, and is refused there.
    pub fn confirm(&self, id: &str) -> Res<Decision> {
        self.locked(|| {
            let d: Decision = self.load(id)?;
            if d.retracted.is_some() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{id} was retracted: there is nothing to confirm.")));
            }
            if self.outside_rb("decision", id)? {
                return Err(RBError::msg_fix("E_STATE_EDITED", format!("{id} was changed outside rb, and a decision rb did not write is nobody's call."), "rb doctor"));
            }
            let a = self.authority(id)?.ok_or_else(|| RBError::msg("E_OBJECT_NOT_FOUND", format!("No decision {} in this project.", repr_str(id))))?;
            if let Some(by) = &a.confirmed_by {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{id} was confirmed already, as {by}.")));
            }
            if a.is_call() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{id} is a call already ({}): nothing waits for a confirmation.", a.state)));
            }
            self.record_call(&d.subject, &d.action, &d.why, &d.revisit, &d.words, d.past.clone(), Some(&d))
        })
    }

    /// The live experiments whose gate waits for the user, as their design reads it: a branch or the words given with
    /// it that a session wrote, which waits with the design for its one confirmation. A gate the user's confirmation
    /// covers as it reads now, or one a session put back as the user had it, waits nowhere.
    pub fn pending_gates(&self) -> Res<Vec<Experiment>> {
        // the design says whether a part of the gate waits: a session's gate that puts back the user's waits nowhere
        let designs = self.designs()?;
        Ok(self.live::<Experiment>()?.into_iter().filter(|e| !(e.if_holds.is_empty() && e.if_not.is_empty()) && designs.get(&e.id).map_or(false, |d| d.gate_waits())).collect())
    }

    /// The user's confirmation of the gate a session set on an experiment: one log line, the gate and its words as they
    /// stand, which makes the gate theirs. The experiment's file is unchanged.
    pub fn confirm_gate(&self, id: &str, key: Option<&str>) -> Res<Experiment> {
        let who = self.actor()?;
        if !who.is_human() {
            return Err(RBError::msg("E_USAGE", "rb confirm makes a gate the user's own: it runs only in their own terminal."));
        }
        self.locked(|| {
            let exp: Experiment = self.load(id)?;
            if exp.retracted.is_some() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{id} was retracted: there is nothing to confirm.")));
            }
            if exp.if_holds.is_empty() && exp.if_not.is_empty() {
                return Err(RBError::msg_fix("E_OBJECT_INVALID", format!("{id}'s design waits for no confirmation."), "rb confirm lists what waits."));
            }
            if !self.pending_gates()?.iter().any(|e| e.id == exp.id) {
                // the calls on it that still wait are named with their commands
                let on = |s: &str| s == exp.id || s.strip_prefix(exp.id.as_str()).map_or(false, |r| r.starts_with('/'));
                let calls: Vec<String> = self.pending_calls()?.iter().filter(|(d, _)| on(&d.subject)).map(|(d, _)| format!("rb confirm {}", d.id)).collect();
                let rest = if calls.is_empty() { ".".to_string() } else { format!("; the calls on it that wait: {}.", calls.join(", ")) };
                return Err(RBError::msg_fix("E_OBJECT_INVALID", format!("{id}'s design waits for no confirmation{rest}"), "rb confirm lists what waits."));
            }
            let (words, by) = self.gate_words()?.remove(&exp.id).unwrap_or_default();
            let now = crate::view::gate_key(&exp.if_holds, &exp.if_not, &words);
            if key.map_or(false, |k| k != now) {
                return Err(RBError::msg_fix(
                    "E_OBJECT_INVALID",
                    format!(
                        "{id}'s gate does not match {}: it changed after it was listed, or rb never listed that key. It reads now {}.",
                        key.unwrap_or_default(),
                        crate::view::gate_said(&exp.if_holds, &exp.if_not, &words, &by, "")
                    ),
                    format!("To confirm it as it reads now: rb confirm {id}@{now}"),
                ));
            }
            if self.outside_rb("experiment", id)? {
                return Err(RBError::msg_fix("E_STATE_EDITED", format!("{id} was changed outside rb, and a gate rb did not write is nobody's."), "rb doctor"));
            }
            self.log(&who, "gate", "experiment", id, None, None, crate::dict! {"confirms" => "gate", "if_holds" => exp.if_holds.clone(), "if_not" => exp.if_not.clone()})?;
            Ok(exp)
        })
    }

    #[allow(clippy::too_many_arguments)]
    fn record_call(&self, subject: &str, action: &str, why: &str, revisit: &str, words: &str, past: Option<Past>, confirms: Option<&Decision>) -> Res<Decision> {
        let who = self.actor()?;
        if !RULING_ACTIONS.contains(&action) && !DIRECTION_ACTIONS.contains(&action) {
            return Err(RBError::msg(
                "E_OBJECT_INVALID",
                format!("{} is not a decision: accept, reject, investigate, continue, park, stop, pivot or ship.", repr_str(action)),
            ));
        }
        self.locked(|| {
            let (_exp_id, setting) = parse_address(subject);
            let mut subject = subject.to_string();
            let mut value = Value::Null;
            let mut across: Option<Dict> = None;
            let kind: String;
            let mut basis = Dict::new();
            let mut inquiry_basis: Option<Dict> = None;
            if setting.is_some() || (subject.contains('.') && self.kind_of(&subject)?.is_none()) {
                let (exp, name, s) = self.resolve_setting(&subject)?;
                subject = format!("{}/{name}", exp.id);
                kind = "setting".into();
                if action != "accept" && action != "reject" {
                    return Err(RBError::msg("E_OBJECT_INVALID", "A setting is accepted (vouched for) or rejected; to change it, rb spec set it."));
                }
                let own_variant = name.contains('.') && exp.variant(name.split('.').next().unwrap_or("")).is_some();
                value = if own_variant || exp.setting(&name).is_some() { s.value.clone() } else { Value::Null };
                if !own_variant {
                    let values: Dict = exp.live_variants().iter().map(|v| (v.name.clone(), exp.value_in(v, &name))).collect();
                    let distinct: HashSet<String> = values.values().map(canonical).collect();
                    across = if distinct.len() > 1 { Some(values) } else { None };
                }
                if action == "accept" && s.per_run {
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("{subject} is per-run: each run gives its own value, so there is none to vouch for.")));
                }
                if action == "accept" && value.is_null() && across.is_none() {
                    return Err(RBError::msg(
                        "E_OBJECT_INVALID",
                        format!("{subject} has no value to vouch for. Set it first (rb spec set), then accept the value it has."),
                    ));
                }
            } else {
                let (k, obj) = self.get(&subject)?;
                kind = k.to_string();
                if !matches!(k, "criterion" | "experiment" | "question") {
                    return Err(RBError::msg(
                        "E_OBJECT_INVALID",
                        format!(
                            "Decisions are made on criteria (their findings), experiments, questions and settings, not on a {k}.{}",
                            if k == "run" { " To stop a run counting, retract it." } else { "" }
                        ),
                    ));
                }
                if k == "criterion" && DIRECTION_ACTIONS.contains(&action) {
                    let e = obj.experiment().unwrap_or("");
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("{action} is a call on a line of work: make it on {e} (rb decide {e} {action}).")));
                }
                match &obj {
                    Obj::Criterion(c) => {
                        basis.insert(c.id.clone(), crate::assess::basis_of_finding(&self.finding(c)?));
                    }
                    Obj::Experiment(e) => {
                        for c in self.live::<Criterion>()? {
                            if c.experiment == e.id {
                                basis.insert(c.id.clone(), crate::assess::basis_of_finding(&self.finding(&c)?));
                            }
                        }
                        for (k2, v2) in self.repository_basis(&[e.id.clone()])? {
                            basis.insert(k2, v2);
                        }
                    }
                    Obj::Question(q) if self.inquiry_links_in_use()? => {
                        let selected = self.inquiry_criterion_ids(&q.id)?;
                        let mut experiment_ids = std::collections::BTreeSet::new();
                        for c in self.live::<Criterion>()? {
                            if selected.contains(&c.id) {
                                basis.insert(c.id.clone(), crate::assess::basis_of_finding(&self.finding(&c)?));
                                experiment_ids.insert(c.experiment.clone());
                            } else if c.role == "gate" && self.gate_questions(&c)?.contains(&q.id) {
                                // a missed gate this call settles, on the reading it settles it on
                                let f = self.finding(&c)?;
                                if f.rule_outcome == "misses" {
                                    basis.insert(c.id.clone(), crate::assess::basis_of_finding(&f));
                                }
                            }
                        }
                        for (key, value) in self.repository_basis(&experiment_ids.into_iter().collect::<Vec<_>>())? {
                            basis.insert(key, value);
                        }
                        inquiry_basis = Some(self.inquiry_basis(&q.id)?);
                    }
                    Obj::Question(q) => {
                        // no criterion is linked or related yet: recorded as earlier format-1 builds record it
                        let eids = self.experiments_under(&q.id, true)?;
                        for c in self.live::<Criterion>()? {
                            if eids.contains(&c.experiment) {
                                basis.insert(c.id.clone(), crate::assess::basis_of_finding(&self.finding(&c)?));
                            }
                        }
                        let mut sorted: Vec<String> = eids.into_iter().collect();
                        sorted.sort();
                        for (key, value) in self.repository_basis(&sorted)? {
                            basis.insert(key, value);
                        }
                    }
                    _ => {}
                }
            }
            // a vouch is for the value it was proposed on: once the value has changed, confirming it would vouch for another
            if let Some(proposed) = confirms.filter(|p| kind == "setting" && (canonical(&p.value) != canonical(&value) || p.across != across)) {
                let was = if proposed.across.is_some() { "values" } else { "value" };
                return Err(RBError::msg_fix(
                    "E_OBJECT_INVALID",
                    format!("{} was proposed on the {was} {subject} had then, which {subject} no longer has.", proposed.id),
                    format!("rb decide {subject} {action} --why \"...\" on the {was} it has now, or rb retract {} --why \"...\"", proposed.id),
                ));
            }
            // the same call recorded again writes nothing: the last call on the subject, or in an agent session the last
            // decision on it, proposed or made; a confirmation is a call of its own
            let last = match confirms {
                Some(_) => None,
                None if who.is_human() => self.decisions_on(&subject)?.into_iter().last(),
                None => self.live::<Decision>()?.into_iter().filter(|d| d.subject == subject).last(),
            };
            if let Some(l) = &last {
                if l.action == action && Value::Dict(l.basis.clone()) == Value::Dict(basis.clone()) && super::inquiries::same_inquiry(l.inquiry_basis.as_ref(), inquiry_basis.as_ref()) && l.why == why && l.past == past {
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("{} already records this decision on {subject}.", l.id)));
                }
            }
            let d: Decision = build(crate::dict! {
                "id" => self.new_id("decision", None)?, "subject" => subject.clone(), "action" => action, "why" => why, "revisit" => revisit,
                "by" => who.id.clone(), "at" => now(), "via" => self.via.clone(), "session" => actor::session_for(&who), "words" => words,
                "basis" => Value::Dict(basis), "inquiry_basis" => inquiry_basis.map(Value::Dict), "value" => value.clone(), "across" => if kind == "setting" { across.map(Value::Dict).unwrap_or(Value::Null) } else { Value::Null },
                "past" => past.as_ref().map(|p| Value::Dict(p.dump(true))).unwrap_or(Value::Null)
            })?;
            let mut detail = crate::dict! {"subject" => subject.clone(), "action" => action, "value" => value};
            if let Some(c) = confirms {
                detail.insert("confirms".into(), Value::Str(c.id.clone()));
            }
            self.commit(&who, "decide", Obj::Decision(d.clone()), detail)?;
            Ok(d)
        })
    }

    /// Whether what a decision was made on under this key compares two arms, so its numbers are signed.
    pub fn compares(&self, key: &str) -> bool {
        self.load::<Criterion>(key).map_or(true, |c| c.compares())
    }

    /// Whether a criterion no longer reads as a call kept it: gone, or its reading moved. One only a newer rb reads
    /// has not moved for this rb, as in a question call's snapshot: state_newer names it, and every gate fails closed.
    pub fn reading_moved(&self, key: &str, then: &Value) -> Res<bool> {
        if self.kind_of(key)? != Some("criterion") {
            return Ok(true);
        }
        if self.newer_only("criterion", key) {
            return Ok(false);
        }
        Ok(crate::assess::basis_of_finding(&self.finding(&self.load::<Criterion>(key)?)?) != *then)
    }

    /// Whether a missed gate a call settled misses now on another reading, so that `rb status` asks for a call on it
    /// again (gate_missed); not a gate that meets now, or one taken back or only a newer rb reads.
    pub fn missed_again(&self, key: &str, then: &Value) -> Res<bool> {
        if self.kind_of(key)? != Some("criterion") || self.newer_only("criterion", key) {
            return Ok(false);
        }
        let c: Criterion = self.load(key)?;
        if c.retracted.is_some() || c.role != "gate" {
            return Ok(false);
        }
        let f = self.finding(&c)?;
        Ok(f.rule_outcome == "misses" && crate::assess::basis_of_finding(&f) != *then)
    }

    /// One entry of what a call keeps, as decide and show say it: a reading it was made on, or a missed gate a scoped
    /// question call settles. A past call was made before any of these readings, so they are said as what the
    /// criterion reads now (`now`, as decide and confirm say it) or read when the call was recorded.
    pub fn basis_line(&self, d: &Decision, key: &str, b: &Value, now: bool) -> String {
        if settled_gate(d, key) {
            return format!("settles the missed gate {key}: {}", self.basis_said(key, b));
        }
        match (d.past.is_some(), now) {
            (false, _) => format!("made on {key}: {}", self.basis_said(key, b)),
            (true, true) => format!("{key} reads now: {}", self.basis_said(key, b)),
            (true, false) => format!("{key} read when it was recorded: {}", self.basis_said(key, b)),
        }
    }

    /// What a call was made on, for one criterion: signed when it compares two arms, its rule as its finding words it.
    pub fn basis_said(&self, key: &str, b: &Value) -> String {
        let Ok(c) = self.load::<Criterion>(key) else { return crate::assess::basis_text(b, true, &crate::assess::basis_rule(b, None, ""), &Default::default()) };
        // its numbers as the criterion's rule sentence says them
        let said = (|| -> Res<_> {
            let (_, reference, metric, _) = self.arms(&self.load(&c.experiment)?, &c)?;
            let direction = self.catalogue_entry(&metric)?.map(|m| m.direction).unwrap_or_else(|| "none".into());
            Ok(crate::assess::rule_numbers(&c, &crate::assess::basis_reading(b, &reference), &direction))
        })()
        .unwrap_or_default();
        crate::assess::basis_text(b, c.compares(), &self.basis_rule(key, b), &said)
    }

    /// The rule a call's basis records for one criterion, as its finding words it: criterion not met, gate missed,
    /// estimate meets criterion.
    pub fn basis_rule(&self, key: &str, b: &Value) -> String {
        let c = self.load::<Criterion>(key).ok();
        let reference = c.as_ref().and_then(|c| self.load::<Experiment>(&c.experiment).ok().and_then(|e| self.arms(&e, c).ok())).map(|a| a.1).unwrap_or_default();
        crate::assess::basis_rule(b, c.as_ref(), &reference)
    }

    /// The calls on a subject, in order, leaving out decision files rb did not write or that were edited since, and
    /// a call that waits for a confirmation, which changes nothing. A call recorded as made earlier (--past) comes
    /// before every call recorded as made when it was recorded.
    pub fn decisions_on(&self, subject: &str) -> Res<Vec<Decision>> {
        let auth = self.authorities()?;
        let mut out = Vec::new();
        for d in self.all::<Decision>()?.iter() {
            if d.subject == subject && d.retracted.is_none() && auth.get(&d.id).map_or(false, Authority::is_call) && !self.outside_rb("decision", &d.id)? {
                out.push(d.clone());
            }
        }
        out.sort_by_key(|d| d.past.is_none());
        Ok(out)
    }

    /// The calls waiting for a confirmation on a subject, oldest first.
    pub fn pending_on(&self, subject: &str) -> Res<Vec<(Decision, Authority)>> {
        Ok(self.pending_calls()?.into_iter().filter(|(d, _)| d.subject == subject).collect())
    }

    /// Decisions a finding reads that are not what rb wrote.
    pub fn decisions_outside_of(&self, criterion_id: &str, experiment_id: &str) -> Res<Vec<Decision>> {
        let prefix = format!("{experiment_id}/");
        let mut out = Vec::new();
        for d in self.all::<Decision>()?.iter() {
            if (d.subject == criterion_id || d.subject.starts_with(&prefix)) && self.outside_rb("decision", &d.id)? {
                out.push(d.clone());
            }
        }
        Ok(out)
    }

    /// The rulings on a subject: its accept, reject and investigate decisions.
    pub fn rulings_on(&self, subject: &str) -> Res<Vec<Decision>> {
        Ok(self.decisions_on(subject)?.into_iter().filter(|d| RULING_ACTIONS.contains(&d.action.as_str())).collect())
    }

    /// An accept on e1/lr (a vouch for its value), still standing for the value the setting has now.
    pub fn vouch(&self, exp: &Experiment, name: &str, s: &Setting) -> Res<Option<Decision>> {
        for d in self.rulings_on(&format!("{}/{name}", exp.id))?.into_iter().rev() {
            if d.action == "accept" {
                return Ok(if !d.value.is_null() && !s.value.is_null() && same_value(&d.value, &s.value) { Some(d) } else { None });
            }
            if d.action == "reject" {
                return Ok(None);
            }
        }
        Ok(None)
    }

    /// An accept of a setting differing between variants, still standing for the values they have now.
    pub fn accepted_difference(&self, exp: &Experiment, name: &str, values: &Dict) -> Res<Option<Decision>> {
        for d in self.rulings_on(&format!("{}/{name}", exp.id))?.into_iter().rev() {
            if d.action == "accept" {
                let same = match &d.across {
                    Some(ac) => {
                        let a: HashSet<&String> = ac.keys().collect();
                        let b: HashSet<&String> = values.keys().collect();
                        a == b && values.iter().all(|(k, v)| canonical(&ac[k]) == canonical(v))
                    }
                    None => false,
                };
                return Ok(if same { Some(d) } else { None });
            }
            if d.action == "reject" {
                return Ok(None);
            }
        }
        Ok(None)
    }

    /// What the repository said of each indexed experiment when a call was made.
    pub fn repository_basis(&self, experiment_ids: &[String]) -> Res<Dict> {
        let mut out = Dict::new();
        for eid in experiment_ids {
            if self.kind_of(eid)? != Some("experiment") {
                continue;
            }
            let e: Experiment = self.load(eid)?;
            if e.folder.is_empty() {
                continue;
            }
            let mut audits = Dict::new();
            for f in &e.files {
                if f.role == "audit" && self.root.join(&f.path).is_file() {
                    if let Some(v) = crate::repo::audit_verdict(&self.root.join(&f.path)) {
                        audits.insert(f.path.clone(), Value::Str(v.1));
                    }
                }
            }
            let mut result: Vec<String> = e.quotes.iter().filter(|q| q.kind == "result").map(|q| q.text.clone()).collect();
            result.sort();
            out.insert(format!("{eid}#repository"), crate::obj! {"result" => str_list(&result), "audits" => Value::Dict(audits)});
        }
        Ok(out)
    }

    /// Whether the readings a decision was made on are still the current ones, and what moved.
    pub fn decision_current(&self, d: &Decision) -> Res<(bool, Vec<String>)> {
        if let Some(then) = &d.inquiry_basis {
            // only this snapshot, or its question, being newer skips the check; newer state elsewhere is left out in
            // inquiry_moved
            if super::inquiries::snapshot_newer(then).is_some() {
                return Ok((true, Vec::new()));
            }
            if self.kind_of(&d.subject)? != Some("question") {
                return Ok((false, vec![d.subject.clone()]));
            }
            if self.newer_only("question", &d.subject) {
                return Ok((true, Vec::new()));
            }
            let now = self.inquiry_basis(&d.subject)?;
            // a criterion only a newer rb reads is missing from the reading now without having moved
            let unread: HashSet<String> = self.newer_index.borrow().keys().map(|(_, id)| id.clone()).collect();
            let moved: Vec<String> = super::inquiries::inquiry_moved(&d.subject, then, &now).into_iter().filter(|id| !unread.contains(id)).collect();
            return Ok((moved.is_empty(), moved));
        }
        let mut moved: Vec<String> = Vec::new();
        for (cid, then) in &d.basis {
            if let Some(eid) = cid.strip_suffix("#repository") {
                let now_ = self.repository_basis(&[eid.to_string()])?;
                if now_.get(cid).unwrap_or(&Value::Null) != then {
                    moved.push(format!("{eid} (its repository says something else now)"));
                }
                continue;
            }
            if self.reading_moved(cid, then)? {
                moved.push(cid.clone());
            }
        }
        let kind = self.kind_of(&d.subject)?;
        if matches!(kind, Some("experiment") | Some("question")) {
            let eids: HashSet<String> = if kind == Some("experiment") { HashSet::from([d.subject.clone()]) } else { self.experiments_under(&d.subject, true)? };
            for c in self.live::<Criterion>()? {
                if eids.contains(&c.experiment) && !d.basis.contains_key(&c.id) && self.finding(&c)?.comparison.is_some() {
                    moved.push(c.id.clone());
                }
            }
        }
        if kind == Some("question") {
            // a call recorded with no snapshot of the question keeps no wording: a rewording since it says the call was
            // made on the question as it read then
            if self.rewordings(&d.subject)?.iter().any(|r| r.at.as_str() > d.at.as_str()) {
                moved.push(format!("{} (question text changed)", d.subject));
            }
            // once criteria are linked to the question, it reads through them: a call on the broader reading is earlier
            let mut linked: Vec<String> = self.inquiry_criterion_ids(&d.subject)?.into_iter().filter(|id| !moved.contains(id)).collect();
            linked.sort();
            moved.extend(linked);
        }
        Ok((moved.is_empty(), moved))
    }

    /// What depends on a record: a retraction says what it touches, in words, one per edge graph::rests_on gives.
    pub fn rests_on(&self, kind: &str, obj: Option<&Obj>, subject: &str) -> Res<Vec<String>> {
        Ok(crate::graph::rests_on(self, kind, obj, subject)?.iter().map(crate::graph::Rest::words).collect())
    }

    /// Retract anything: it stays on record, attributed to whoever took it back and why, and stops counting.
    pub fn retract(&self, subject: &str, why: &str, scope: Option<&[String]>) -> Res<(String, String, Vec<String>)> {
        let who = self.actor()?;
        let scope: Vec<String> = scope.map(|s| s.to_vec()).unwrap_or_default();
        self.locked(|| {
            let (exp_id, name) = parse_address(subject);
            if !scope.is_empty() && name.is_some() {
                return Err(RBError::msg(
                    "E_OBJECT_INVALID",
                    "--scope names measurements a retraction supersedes, which only a run has; retract the setting or variant without it.",
                ));
            }
            if let Some(name) = name {
                let mut exp: Experiment = self.load(&exp_id)?;
                let vi = if exp.lookup(&name).is_none() { exp.variant_index(&name) } else { None };
                if let Some(vi) = vi {
                    if let Some(r) = &exp.variants[vi].retracted {
                        return Err(RBError::msg("E_OBJECT_INVALID", format!("Variant {}/{name} was already retracted at {}.", exp.id, r.at)));
                    }
                    let deps: Vec<String> = crate::graph::variant_rests_on(&exp).iter().map(crate::graph::Rest::words).collect();
                    let before = self.freeze_sha(&exp, None)?;
                    exp.variants[vi].retracted = Some(nested(crate::dict! {"at" => now(), "by" => who.id.clone(), "why" => why, "words" => self.words.clone()}, Retraction::validate)?);
                    let after = self.freeze_sha(&exp, None)?;
                    let amend = if exp.frozen.is_some() { Some(why) } else { None };
                    let amended = self.guard(&mut exp, &before, &after, amend, &who, &format!("variant {name} retracted"))?;
                    let eid = exp.id.clone();
                    self.commit(&who, "retract", Obj::Experiment(exp), merge(crate::dict! {"variant" => name.clone(), "why" => why}, amended))?;
                    return Ok(("variant".to_string(), format!("{eid}/{name}"), deps));
                }
                let (mut exp, name, _s) = self.resolve_setting(subject)?;
                let deps = self.rests_on("setting", None, &format!("{}/{name}", exp.id))?;
                let before = self.freeze_sha(&exp, None)?;
                let at = exp.resolve_at(&name);
                let r: Retraction = nested(crate::dict! {"at" => now(), "by" => who.id.clone(), "why" => why, "words" => self.words.clone()}, Retraction::validate)?;
                if let Some(at) = at {
                    exp.setting_at_mut(at).retracted = Some(r);
                }
                let after = self.freeze_sha(&exp, None)?;
                let amend = if exp.frozen.is_some() { Some(why) } else { None };
                let amended = self.guard(&mut exp, &before, &after, amend, &who, &format!("setting {name} retracted"))?;
                let eid = exp.id.clone();
                self.commit(&who, "retract", Obj::Experiment(exp), merge(crate::dict! {"setting" => name.clone(), "why" => why}, amended))?;
                return Ok(("setting".to_string(), format!("{eid}/{name}"), deps));
            }
            if self.kind_of(subject)?.is_none() {
                if let Some(mut holder) = self.note_holder(subject)? {
                    if !scope.is_empty() {
                        return Err(RBError::msg("E_OBJECT_INVALID", format!("--scope names measurements a retraction supersedes, which only a run has; {subject} is a note.")));
                    }
                    let notes = match &mut holder {
                        Obj::Experiment(e) => &mut e.notes,
                        Obj::Project(p) => &mut p.notes,
                        _ => unreachable!(),
                    };
                    let note = notes.iter_mut().find(|n| n.id.as_deref() == Some(subject)).unwrap();
                    if let Some(r) = &note.retracted {
                        return Err(RBError::msg("E_OBJECT_INVALID", format!("{subject} was already retracted at {}: {}", r.at, r.why)));
                    }
                    if !who.is_human() && note.by.starts_with("human:") {
                        return Err(RBError::msg_fix(
                            "E_USAGE",
                            format!("{subject} is a note the user wrote, and this is an agent session: a session takes back only a note a session wrote."),
                            "add a note that says what changed (rb note add), or the user retracts it in their own terminal",
                        ));
                    }
                    note.retracted = Some(nested(crate::dict! {"at" => now(), "by" => who.id.clone(), "why" => why, "words" => self.words.clone()}, Retraction::validate)?);
                    self.commit(&who, "retract", holder, crate::dict! {"note" => subject, "why" => why})?;
                    return Ok(("note".to_string(), subject.to_string(), Vec::new()));
                }
            }
            let (kind, mut obj) = self.get(subject)?;
            if !scope.is_empty() && kind != "run" {
                return Err(RBError::msg(
                    "E_OBJECT_INVALID",
                    format!("--scope names measurements a retraction supersedes, which only a run has; {subject} is a {kind}."),
                ));
            }
            if let Some(r) = obj.retracted() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{subject} was already retracted at {}: {}", r.at, r.why)));
            }
            // taking back the user's call changes what only they may change: a session proposes another call instead
            if let Obj::Decision(d) = &obj {
                let theirs = self.authority(&d.id)?.map_or(false, |a| a.is_call() || a.confirmed_by.is_some());
                if !who.is_human() && theirs {
                    return Err(RBError::msg_fix(
                        "E_USAGE",
                        format!("{subject} is the user's call, and this is an agent session: a session cannot take back a call that is theirs."),
                        format!("propose another call on {}: rb decide {} <action> --why \"...\", which waits for one rb confirm; or the user retracts it in their own terminal", d.subject, d.subject),
                    ));
                }
            }
            if !scope.is_empty() {
                if let Obj::Run(run) = &obj {
                    let named: Vec<&String> = scope.iter().filter(|x| !run.metrics.keys().any(|m| scope_covers(&[(*x).clone()], m, None))).collect();
                    if !named.is_empty() {
                        let mut ms: Vec<&String> = run.metrics.keys().collect();
                        ms.sort();
                        return Err(RBError::msg_fix(
                            "E_OBJECT_INVALID",
                            format!(
                                "Run {} reports no measurement {}; it reports {}.",
                                run.id,
                                named.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", "),
                                if ms.is_empty() { "none".to_string() } else { ms.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ") }
                            ),
                            "Name a measurement the run reports, by its full name or its last part.",
                        ));
                    }
                }
            }
            let deps = if kind == "decision" { Vec::new() } else { self.rests_on(kind, Some(&obj), subject)? };
            let r: Retraction = nested(
                crate::dict! {"at" => now(), "by" => who.id.clone(), "why" => why, "scope" => str_list(&scope), "words" => self.words.clone()},
                Retraction::validate,
            )?;
            if let Some(slot) = obj.retracted_mut() {
                *slot = Some(r);
            }
            if let Obj::Criterion(c) = &obj {
                let mut exp: Experiment = self.load(&c.experiment)?;
                if exp.frozen.is_some() {
                    let before = self.freeze_sha(&exp, None)?;
                    let crits: Vec<Criterion> = self.criteria_of(&exp.id)?.into_iter().map(|x| if x.id == c.id { c.clone() } else { x }).collect();
                    let after = self.freeze_sha(&exp, Some(&crits))?;
                    let amended = self.guard(&mut exp, &before, &after, Some(why), &who, &format!("criterion {} retracted", c.id))?;
                    self.commit(&who, "retract", obj.clone(), crate::dict! {"why" => why})?;
                    self.commit(&who, "amend", Obj::Experiment(exp), amended)?;
                    return Ok((kind.to_string(), subject.to_string(), deps));
                }
            }
            let mut detail = crate::dict! {"why" => why};
            if !scope.is_empty() {
                detail.insert("scope".into(), str_list(&scope));
            }
            self.commit(&who, "retract", obj, detail)?;
            Ok((kind.to_string(), subject.to_string(), deps))
        })
    }
}

/// What a write declares stays one active declaration: one link per question (every question, or the one it filed),
/// one relation per kind and target (every pair, or the one it declared). A merge may still leave two, which reads as
/// an integrity problem until the write that names them replaces both.
pub fn declared_once(c: &Criterion, question: Option<&str>, pair: Option<(&str, &str)>, every: bool) -> Res<()> {
    let twice_q = c.active_links().into_iter().map(|l| l.question.as_str()).find(|q| (every || question == Some(*q)) && c.links_to(q) > 1);
    if let Some(q) = twice_q {
        return Err(RBError::msg("E_OBJECT_INVALID", format!("{} would have more than one active declaration for question {q}.", c.id)));
    }
    let twice_r = c.active_relations().into_iter().find(|r| (every || pair == Some((r.kind.as_str(), r.target.as_str()))) && c.relations_to(&r.kind, &r.target) > 1);
    if let Some(r) = twice_r {
        return Err(RBError::msg("E_OBJECT_INVALID", format!("{} would declare more than one active {} relation to {}.", c.id, r.kind, r.target)));
    }
    Ok(())
}

/// An experiment retracted takes no more settings, criteria or runs.
pub fn writable(exp: &Experiment) -> Res<()> {
    if let Some(r) = &exp.retracted {
        return Err(RBError::msg(
            "E_OBJECT_INVALID",
            format!("Experiment {} was retracted ({}); it takes no more settings, criteria or runs.", exp.id, r.why),
        ));
    }
    Ok(())
}

/// A cited number's source is the text that states it; a database row is a measurement, to read as a run.
fn not_a_store_row(src: Option<&Source>, root: &std::path::Path, what: &str) -> Res<()> {
    match src.filter(|s| sources::is_store_row(s, root)) {
        Some(s) => Err(RBError::msg_fix(
            "E_OBJECT_INVALID",
            format!("{what} is the text that states a number someone else gave; {} is a row of a database, a measurement.", s.label()),
            "Read the row as a run (rb run add <experiment> --from <database> --table <table> --where <column>=<value>), or cite the document that states the number.",
        )),
        None => Ok(()),
    }
}
