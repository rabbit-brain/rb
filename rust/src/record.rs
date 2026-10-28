//! The record: one project's experiments, as YAML files in `.rb/`. The models themselves are generated from the Python
//! ones (record_gen.rs); this module holds what they share: the constants, the hand-written validators (`hooks`), the
//! stored form (`stored`, `ordered`), global ids, and the models' own methods.

use crate::model::{ModelMeta, VErr};
use crate::pyfloat::g;
use crate::pyjson::repr_str;
use crate::value::{Dict, Value};
use std::sync::OnceLock;

pub use crate::record_gen::*;

pub const RECORD_DIR: &str = ".rb";
pub const FORMAT_KEY: &str = "rb";
pub const RB_FORMAT: i64 = 1;
pub const ID_PATTERN: &str = r"^[a-zA-Z0-9_.-]{1,80}$";
pub const UID_PATTERN: &str = r"^[a-z]{2,4}_[0-9A-HJKMNP-TV-Z]{26}$";
pub const ACTOR_PATTERN: &str = r"^(human|agent):[A-Za-z0-9_.@+-]{1,80}$";
pub const SETTING_PATTERN: &str = r"^[A-Za-z0-9_.:-]{1,80}$";
pub const VARIANT_PATTERN: &str = r"^[A-Za-z0-9_-]{1,40}$";
pub const METRIC_PATTERN: &str = r"^[A-Za-z0-9_.:/-]{1,80}$";
pub const ESTIMAND_PATTERN: &str = r"^[A-Za-z0-9_.:-]{0,60}$";
pub const ARM_PATTERN: &str = r"^[A-Za-z0-9_.:+-]{0,60}$";
pub const TEXT: usize = 1000;

pub const THRESHOLDS: &[&str] = &["at_most", "at_least", "equals"];
pub const INTERVAL_RULES: &[&str] = &["better", "noninferior", "equivalent"];
pub const RULING_ACTIONS: &[&str] = &["accept", "reject", "investigate"];
pub const DIRECTION_ACTIONS: &[&str] = &["continue", "park", "stop", "pivot", "ship"];
pub const PROVENANCE: &[&str] = &["created_by", "created_at", "via", "retracted"];

pub fn re_full(pattern: &str, s: &str) -> bool {
    crate::model::regex_for(pattern).is_match(s)
}

pub fn id_ok(s: &str) -> bool {
    re_full(ID_PATTERN, s)
}

// ---------------------------------------------------------------- global ids

const CROCKFORD: &[u8] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/// A global identity: the kind's prefix and a ULID (48-bit millisecond time, 80 random bits, Crockford base32).
pub fn new_uid(prefix: &str) -> String {
    let ms = crate::pytime::now_dt().timestamp_millis() as u128;
    let rand = crate::rng::randbits(80);
    let mut n: u128 = (ms << 80) | rand;
    let mut chars = [0u8; 26];
    for k in (0..26).rev() {
        chars[k] = CROCKFORD[(n & 31) as usize];
        n >>= 5;
    }
    format!("{prefix}_{}", String::from_utf8_lossy(&chars))
}

// ---------------------------------------------------------------- the stored form

fn prune(meta: &ModelMeta, data: &Dict) -> Dict {
    let mut out = Dict::new();
    for (k, v) in data {
        let field = meta.field(k);
        let required = field.map_or(false, |f| f.required);
        let empty = match v {
            Value::Str(s) => s.is_empty(),
            Value::List(l) => l.is_empty(),
            Value::Dict(d) => d.is_empty(),
            _ => false,
        };
        if !required && (v.is_null() || (empty && field.is_some())) {
            continue;
        }
        let sub = field.and_then(|f| f.sub);
        let v = match (sub, v) {
            (Some(s), Value::Dict(d)) => Value::Dict(prune(s, d)),
            (Some(s), Value::List(items)) => Value::List(items.iter().map(|x| if let Value::Dict(d) = x { Value::Dict(prune(s, d)) } else { x.clone() }).collect()),
            _ => v.clone(),
        };
        out.insert(k.clone(), v);
    }
    out
}

/// The form a record is written in and hashed as: what it is first, who wrote it and when last, as JSON values,
/// without the fields that carry nothing (null, empty text, an empty list or mapping).
pub fn stored_form(meta: &ModelMeta, dumped: &Dict) -> Dict {
    let form = prune(meta, dumped);
    if meta.written {
        let mut out = Dict::new();
        for (k, v) in &form {
            if !PROVENANCE.contains(&k.as_str()) {
                out.insert(k.clone(), v.clone());
            }
        }
        for k in PROVENANCE {
            if let Some(v) = form.get(*k) {
                out.insert(k.to_string(), v.clone());
            }
        }
        return out;
    }
    form
}

/// A stored form's keys in the order its record writes them, values untouched: for writing back a form that came from
/// canonical JSON, whose keys are sorted.
pub fn ordered(meta: &ModelMeta, data: &Dict) -> Dict {
    let mut fields: Vec<&str> = meta.fields.iter().map(|f| f.name).collect();
    if meta.written {
        let mut f2: Vec<&str> = fields.iter().copied().filter(|f| !PROVENANCE.contains(f)).collect();
        f2.extend(PROVENANCE.iter().copied().filter(|p| fields.contains(p)));
        fields = f2;
    }
    let rank = |k: &str| fields.iter().position(|f| *f == k).unwrap_or(fields.len());
    let mut keys: Vec<&String> = data.keys().collect();
    keys.sort_by(|a, b| (rank(a), a.as_str()).cmp(&(rank(b), b.as_str())));
    let mut out = Dict::new();
    for k in keys {
        let v = &data[k];
        let sub = meta.field(k).and_then(|f| f.sub);
        let v = match (sub, v) {
            (Some(s), Value::Dict(d)) => Value::Dict(ordered(s, d)),
            (Some(s), Value::List(items)) => Value::List(items.iter().map(|x| if let Value::Dict(d) = x { Value::Dict(ordered(s, d)) } else { x.clone() }).collect()),
            _ => v.clone(),
        };
        out.insert(k.clone(), v);
    }
    out
}

// ---------------------------------------------------------------- the records as kinds

/// A record that has a file of its own (or a place in its experiment's criteria file).
pub trait Record: Clone + std::fmt::Debug {
    const KIND: &'static str;
    fn meta() -> &'static ModelMeta;
    fn validate_value(v: &Value, errs: &mut Vec<VErr>) -> Option<Self>;
    fn dump_all(&self, exclude_none: bool) -> Dict;
    fn rec_id(&self) -> &str;
    /// created_at, or `at` for a decision: what all() sorts by, before the id
    fn sort_time(&self) -> &str;
    fn retraction(&self) -> Option<&Retraction>;
    fn stored(&self) -> Dict {
        stored_form(Self::meta(), &self.dump_all(true))
    }
}

macro_rules! record_kind {
    ($t:ident, $kind:expr, $time:ident) => {
        impl Record for $t {
            const KIND: &'static str = $kind;
            fn meta() -> &'static ModelMeta {
                &$t::META
            }
            fn validate_value(v: &Value, errs: &mut Vec<VErr>) -> Option<Self> {
                let mut loc = Vec::new();
                $t::validate(v, &mut loc, errs)
            }
            fn dump_all(&self, exclude_none: bool) -> Dict {
                self.dump(exclude_none)
            }
            fn rec_id(&self) -> &str {
                &self.id
            }
            fn sort_time(&self) -> &str {
                &self.$time
            }
            fn retraction(&self) -> Option<&Retraction> {
                self.retracted.as_ref()
            }
        }
    };
}

record_kind!(Question, "question", created_at);
record_kind!(Experiment, "experiment", created_at);
record_kind!(Metric, "metric", created_at);
record_kind!(Criterion, "criterion", created_at);
record_kind!(Run, "run", created_at);
record_kind!(Analysis, "analysis", created_at);
record_kind!(Resource, "resource", created_at);
record_kind!(Decision, "decision", at);

impl Project {
    pub fn stored(&self) -> Dict {
        stored_form(&Project::META, &self.dump(true))
    }
}

// ---------------------------------------------------------------- the validators that are not types

fn finite(v: f64, name: &str) -> Result<f64, String> {
    if v.is_finite() {
        Ok(v)
    } else {
        Err(format!("{name} must be a finite number"))
    }
}

fn py_strip_empty(s: &str) -> bool {
    crate::rbyaml::py_strip(s).is_empty()
}

pub mod hooks {
    use super::*;

    pub fn retraction_scope(v: &mut Vec<String>) -> Result<(), String> {
        for s in v.iter() {
            if !re_full(METRIC_PATTERN, s) {
                return Err(format!("scope entries name measurements: letters, digits and _.:/- (got {})", repr_str(s)));
            }
        }
        Ok(())
    }

    pub fn setting_value(v: &mut Value) -> Result<(), String> {
        if let Value::List(l) = v {
            if l.is_empty() {
                return Err("an empty list is not a value; record a setting nobody has found as unknown".into());
            }
        }
        let items: Vec<&Value> = match v {
            Value::List(l) => l.iter().collect(),
            other => vec![other],
        };
        for x in items {
            if let Value::Str(s) = x {
                if py_strip_empty(s) {
                    return Err("empty text is not a value; record a setting nobody has found as unknown".into());
                }
            }
            if let Value::Float(f) = x {
                if !f.is_finite() {
                    return Err("a value must be a finite number".into());
                }
            }
        }
        Ok(())
    }

    pub fn setting_after(s: &mut Setting) -> Result<(), String> {
        // false is no designation: an earlier build wrote it for --sample-factor, and the next write drops it
        if s.design_factor == Some(false) {
            s.design_factor = None;
        }
        if s.design_factor == Some(true) && !s.per_run {
            return Err("a design factor must be a per-run setting".into());
        }
        if s.per_run {
            if !s.value.is_null() || s.status != "unknown" {
                return Err(format!("setting {}: a per-run setting has no single value; each run gives its own", s.name));
            }
            return Ok(());
        }
        if s.status == "unknown" && !s.value.is_null() {
            return Err(format!("setting {}: an unknown setting has no value", s.name));
        }
        if s.status != "unknown" && s.value.is_null() {
            return Err(format!("setting {}: {} without a value; a setting nobody has found is unknown", s.name, s.status));
        }
        if s.status == "verified" && s.source.as_ref().map_or(true, |src| src.resolved.is_none()) {
            return Err(format!("setting {}: verified without what rb read. Only `rb spec verify` sets verified", s.name));
        }
        Ok(())
    }

    pub fn experiment_questions(v: &mut Vec<String>) -> Result<(), String> {
        for q in v.iter() {
            if !re_full(ID_PATTERN, q) {
                return Err(format!("questions are ids: letters, digits, '_', '.' and '-' (got {})", repr_str(q)));
            }
        }
        let set: std::collections::HashSet<&String> = v.iter().collect();
        if set.len() != v.len() {
            return Err("a question is listed twice".into());
        }
        Ok(())
    }

    fn dups(names: Vec<&str>) -> Vec<String> {
        let mut d: Vec<String> = names.iter().filter(|n| names.iter().filter(|m| m == n).count() > 1).map(|s| s.to_string()).collect();
        d.sort();
        d.dedup();
        d
    }

    pub fn experiment_variants(v: &mut Vec<Variant>) -> Result<(), String> {
        let d = dups(v.iter().map(|x| x.name.as_str()).collect());
        if !d.is_empty() {
            return Err(format!("duplicate variant names: {}", d.join(", ")));
        }
        Ok(())
    }

    pub fn experiment_settings(v: &mut Vec<Setting>) -> Result<(), String> {
        // a setting taken back stays on record beside the one set again under its name
        let d = dups(v.iter().filter(|x| x.retracted.is_none()).map(|x| x.name.as_str()).collect());
        if !d.is_empty() {
            return Err(format!("duplicate setting names: {}", d.join(", ")));
        }
        Ok(())
    }

    pub fn metric_aliases(v: &mut Vec<String>) -> Result<(), String> {
        for a in v.iter() {
            if !re_full(METRIC_PATTERN, a) {
                return Err(format!("alias {}: letters, digits and _.:/- only", repr_str(a)));
            }
        }
        Ok(())
    }

    fn opt_finite(v: &mut Option<f64>) -> Result<(), String> {
        if let Some(x) = v {
            finite(*x, "value")?;
        }
        Ok(())
    }
    pub fn criterion_target(v: &mut Option<f64>) -> Result<(), String> {
        opt_finite(v)
    }
    pub fn criterion_tolerance(v: &mut Option<f64>) -> Result<(), String> {
        opt_finite(v)
    }
    pub fn criterion_noise(v: &mut Option<f64>) -> Result<(), String> {
        opt_finite(v)
    }
    pub fn criterion_margin(v: &mut Option<f64>) -> Result<(), String> {
        opt_finite(v)
    }

    pub fn criterion_after(c: &mut Criterion) -> Result<(), String> {
        // one active declaration per question, and per kind and target, is the writes' rule (a merge may leave two)
        if c.relations.iter().any(|relation| relation.target == c.id) { return Err("a criterion cannot relate to itself".into()); }
        for selector in [&c.r#where, &c.subject_where, &c.reference_where] {
            for (name, value) in selector {
                if !re_full(SETTING_PATTERN, name) {
                    return Err("run selectors name per-run settings".into());
                }
                let valid = match value {
                    Value::Str(s) => !crate::rbyaml::py_strip(s).is_empty(),
                    Value::Int(_) | Value::Bool(_) => true,
                    Value::Float(f) => f.is_finite(),
                    _ => false,
                };
                if !valid { return Err("run selector values must be nonblank text, booleans or finite numbers".into()); }
            }
        }
        if !c.reference_where.is_empty() && !c.compares() {
            return Err("reference_where needs a comparison with a reference".into());
        }
        if !c.estimand.is_empty() && c.scoped() {
            return Err("run selectors read runs, not an analysis's custom estimand".into());
        }
        for selector in [&c.subject_where, &c.reference_where] {
            if selector.iter().any(|(k, v)| c.r#where.get(k).map_or(false, |shared| !crate::ledger::same_value(shared, v))) {
                return Err("an arm selector contradicts the shared run selector".into());
            }
        }
        let cmp = c.comparator.as_str();
        if THRESHOLDS.contains(&cmp) && c.target.is_none() {
            return Err(format!("an '{cmp}' criterion needs a target"));
        }
        if cmp == "equals" && c.tolerance.is_none() {
            return Err("an 'equals' criterion needs a tolerance".into());
        }
        if cmp != "equals" && c.tolerance.is_some() {
            return Err("a tolerance applies only to an 'equals' criterion".into());
        }
        if INTERVAL_RULES.contains(&cmp) {
            if c.target.is_some() {
                return Err(format!("a '{cmp}' rule reads the interval against 0; it takes no target"));
            }
            if !c.compares() {
                return Err(format!("a '{cmp}' rule compares two arms: give a reference (or read change.<metric>)"));
            }
            if c.over != "mean" {
                return Err(format!("a '{cmp}' rule already reads the interval; --each and --bound are for thresholds"));
            }
        }
        if (cmp == "noninferior" || cmp == "equivalent") && c.margin.is_none() {
            return Err(format!("a '{cmp}' rule needs its margin"));
        }
        if !(cmp == "noninferior" || cmp == "equivalent") && c.margin.is_some() {
            return Err("a margin applies only to a noninferior or equivalent rule".into());
        }
        if !c.reference.is_empty() && c.metric.starts_with("change.") {
            return Err("change.<metric> already compares with the baseline; give the bare metric with a reference".into());
        }
        if (!c.subject.is_empty() || !c.reference.is_empty()) && c.metric.contains('.') {
            let head = c.metric.split('.').next().unwrap_or("");
            if ["change", "candidate", "baseline"].contains(&head) {
                return Err("with a subject or reference, the metric is the bare name (val_loss), not a role or change form".into());
            }
        }
        if c.origin == "cited" && c.source.is_none() {
            return Err("a cited criterion needs its source".into());
        }
        Ok(())
    }

    pub fn run_metrics(v: &mut indexmap::IndexMap<String, f64>) -> Result<(), String> {
        for (k, x) in v.iter() {
            if !re_full(METRIC_PATTERN, k) {
                return Err(format!("metric names are letters, digits and _.:/- (got {})", repr_str(k)));
            }
            finite(*x, &format!("metric {k}"))?;
        }
        Ok(())
    }

    pub fn comparison_estimate(v: &mut f64) -> Result<(), String> {
        finite(*v, "a comparison's number").map(|_| ())
    }
    pub fn comparison_lo(v: &mut Option<f64>) -> Result<(), String> {
        if let Some(x) = v {
            finite(*x, "a comparison's number")?;
        }
        Ok(())
    }
    pub fn comparison_hi(v: &mut Option<f64>) -> Result<(), String> {
        comparison_lo(v)
    }

    pub fn comparison_after(c: &mut Comparison) -> Result<(), String> {
        if c.lo.is_none() != c.hi.is_none() {
            return Err("an interval has both bounds: give lo and hi, or neither".into());
        }
        if let (Some(lo), Some(hi)) = (c.lo, c.hi) {
            if lo > hi {
                return Err(format!("the interval's lower bound {} is above its upper bound {}", g(lo), g(hi)));
            }
            let a = lo - 1e-12 * 1f64.max(lo.abs());
            let b = hi + 1e-12 * 1f64.max(hi.abs());
            if !(a <= c.estimate && c.estimate <= b) {
                return Err(format!("the estimate {} lies outside its interval [{}, {}]", g(c.estimate), g(lo), g(hi)));
            }
        }
        Ok(())
    }

    pub fn analysis_after(a: &mut Analysis) -> Result<(), String> {
        let keys: Vec<(String, String, String, String)> = a.comparisons.iter().map(|c| c.key()).collect();
        let mut twice: Vec<&(String, String, String, String)> = keys.iter().filter(|k| keys.iter().filter(|x| x == k).count() > 1).collect();
        twice.sort();
        twice.dedup();
        if let Some((s, r, m, e)) = twice.first() {
            return Err(format!(
                "the analysis reports {} for {}{}{} twice",
                m,
                if s.is_empty() { "the run" } else { s },
                if r.is_empty() { String::new() } else { format!(" vs {r}") },
                if e.is_empty() { String::new() } else { format!(" ({e})") }
            ));
        }
        if !a.supersedes.is_empty() && a.why.is_empty() {
            return Err("superseding an analysis needs the reason".into());
        }
        Ok(())
    }

    pub fn source_after(s: &mut Source) -> Result<(), String> {
        if s.kind == "file" && s.path.as_deref().map_or(true, |p| p.is_empty()) {
            return Err("a file source needs a path".into());
        }
        if s.kind == "run" {
            if s.path.as_deref().map_or(true, |p| p.is_empty()) || s.pointer.is_none() {
                return Err("a run source needs a path and a JSON pointer, e.g. run:out/seed1/record.json#/seeds/torch".into());
            }
            if let Some(p) = &s.pointer {
                if !p.is_empty() && !p.starts_with('/') {
                    return Err("a JSON pointer starts with '/', e.g. /seeds/torch".into());
                }
            }
        }
        if s.kind == "url" && s.url.as_deref().map_or(true, |u| u.is_empty()) {
            return Err("a url source needs the url".into());
        }
        if s.kind == "note" && s.note.is_empty() {
            return Err("a note source needs the note".into());
        }
        if s.resolved.is_some() && !s.checkable() {
            return Err("only a file source with a key, line or quote, or a run source, can be resolved".into());
        }
        Ok(())
    }
}

// ---------------------------------------------------------------- the models' own methods

impl Source {
    pub fn checkable(&self) -> bool {
        self.kind == "run" || (self.kind == "file" && (self.key.is_some() || self.line.is_some() || self.quote.as_deref().map_or(false, |q| !q.is_empty())))
    }

    pub fn label(&self) -> String {
        let s = match self.kind.as_str() {
            "file" => {
                let mut s = self.path.clone().map(|p| p).unwrap_or_else(|| "None".into());
                if let Some(k) = self.key.as_deref().filter(|k| !k.is_empty()) {
                    s.push_str(&format!("#{k}"));
                } else if let Some(l) = self.line.filter(|l| *l != 0) {
                    s.push_str(&format!(":{l}"));
                }
                if let Some(q) = self.quote.as_deref().filter(|q| !q.is_empty()) {
                    let n = q.chars().count();
                    let shown = if n <= 60 { q.to_string() } else { format!("{}...", q.chars().take(57).collect::<String>()) };
                    s.push_str(&format!(" \"{shown}\""));
                }
                if let Some(c) = self.commit.as_deref().filter(|c| !c.is_empty()) {
                    s.push_str(&format!(" @{}", c.chars().take(7).collect::<String>()));
                }
                s
            }
            "run" => format!("{}#{}", self.path.clone().unwrap_or_else(|| "None".into()), self.pointer.clone().unwrap_or_else(|| "None".into())),
            "url" => self.url.clone().unwrap_or_else(|| "None".into()),
            _ => format!("note: {}", self.note),
        };
        match self.locator.as_deref().filter(|l| !l.is_empty()) {
            Some(l) => format!("{s} ({l})"),
            None => s,
        }
    }

    /// The options that give this source to rb spec set or rb criterion add as it is kept, each value quoted for a
    /// POSIX shell: --source as it was given, its line where rb last read it, then each --quote, --term, --locator and
    /// --commit it holds. Of a source gone `stale`, what held the value where rb no longer finds it is for the user to
    /// give again: its quote is a placeholder and its commit is left out.
    pub fn args(&self, stale: bool) -> String {
        let spec = match self.kind.as_str() {
            "file" => {
                let path = self.path.clone().unwrap_or_default();
                let line = self.line.filter(|l| *l != 0).map(|l| self.resolved.as_ref().and_then(|r| r.line).unwrap_or(l));
                match (self.key.as_deref().filter(|k| !k.is_empty()), line) {
                    (Some(k), _) => format!("{path}#{k}"),
                    (None, Some(l)) => format!("{path}:{l}"),
                    _ => path,
                }
            }
            "run" => format!("run:{}#{}", self.path.as_deref().unwrap_or(""), self.pointer.as_deref().unwrap_or("")),
            "url" => self.url.clone().unwrap_or_default(),
            _ => format!("note:{}", self.note),
        };
        // a value that starts with '-' is joined to its option, or it would read as an option of its own
        let opt = |flag: &str, v: &str| format!("--{flag}{}{}", if v.starts_with('-') { "=" } else { " " }, crate::cli::shlex_quote(v));
        let quote = self.quote.clone().filter(|q| !q.is_empty()).map(|q| if stale { "<the text that states it now>".to_string() } else { q });
        let commit = self.commit.clone().filter(|_| !stale);
        let mut out = opt("source", &spec);
        for (flag, given) in [("quote", &quote), ("term", &self.term), ("locator", &self.locator), ("commit", &commit)] {
            if let Some(v) = given.as_deref().filter(|v| !v.is_empty()) {
                out.push_str(&format!(" {}", opt(flag, v)));
            }
        }
        out
    }
}

impl Variant {
    pub fn setting(&self, name: &str) -> Option<&Setting> {
        self.settings.iter().find(|s| s.name == name && s.retracted.is_none())
    }
}

impl Experiment {
    pub fn question(&self) -> Option<&str> {
        self.questions.first().map(|s| s.as_str())
    }
    pub fn variant(&self, name: &str) -> Option<&Variant> {
        self.variants.iter().find(|v| v.name == name && v.retracted.is_none())
    }
    pub fn live_variants(&self) -> Vec<&Variant> {
        self.variants.iter().filter(|v| v.retracted.is_none()).collect()
    }
    pub fn by_role(&self, role: &str) -> Vec<&Variant> {
        self.live_variants().into_iter().filter(|v| v.role == role).collect()
    }
    pub fn setting(&self, name: &str) -> Option<&Setting> {
        self.settings.iter().find(|s| s.name == name && s.retracted.is_none())
    }
    /// `int8.lr` is variant int8's own lr when int8 is a variant of this experiment; anything else is shared.
    pub fn split<'a>(&'a self, name: &'a str) -> (Option<&'a Variant>, &'a str) {
        if let Some((head, tail)) = name.split_once('.') {
            if !tail.is_empty() {
                if let Some(v) = self.variant(head) {
                    return (Some(v), tail);
                }
            }
        }
        (None, name)
    }
    pub fn lookup(&self, name: &str) -> Option<&Setting> {
        if let Some((head, tail)) = name.split_once('.') {
            if !tail.is_empty() {
                if let Some(v) = self.variant(head) {
                    return v.setting(tail);
                }
            }
        }
        self.setting(name)
    }
    /// Every live setting with its full name: shared ones as themselves, a variant's as <variant>.<name>.
    pub fn all_settings(&self) -> Vec<(String, &Setting)> {
        let mut out: Vec<(String, &Setting)> = self.settings.iter().filter(|s| s.retracted.is_none()).map(|s| (s.name.clone(), s)).collect();
        for v in self.live_variants() {
            for s in v.settings.iter().filter(|s| s.retracted.is_none()) {
                out.push((format!("{}.{}", v.name, s.name), s));
            }
        }
        out
    }
    pub fn value_in(&self, variant: &Variant, name: &str) -> Value {
        if let Some(own) = variant.setting(name) {
            return own.value.clone();
        }
        self.setting(name).map(|s| s.value.clone()).unwrap_or(Value::Null)
    }
}

impl Criterion {
    /// Each question's active declaration, the latest where a merge of two branches left more than one.
    pub fn active_links(&self) -> Vec<&QuestionLink> {
        let mut out: Vec<&QuestionLink> = Vec::new();
        for link in self.question_links.iter().rev().filter(|l| l.retracted.is_none()) {
            if !out.iter().any(|kept| kept.question == link.question) { out.push(link); }
        }
        out.reverse();
        out
    }

    /// How many active declarations file it to this question: one, or more where a merge left them.
    pub fn links_to(&self, question: &str) -> usize {
        self.question_links.iter().filter(|l| l.question == question && l.retracted.is_none()).count()
    }

    /// Each active relation, by kind and target, the latest where a merge of two branches left more than one.
    pub fn active_relations(&self) -> Vec<&CriterionRelation> {
        let mut out: Vec<&CriterionRelation> = Vec::new();
        for relation in self.relations.iter().rev().filter(|r| r.retracted.is_none()) {
            if !out.iter().any(|kept| kept.kind == relation.kind && kept.target == relation.target) { out.push(relation); }
        }
        out.reverse();
        out
    }

    /// How many active relations of this kind it declares to this target: one, or more where a merge left them.
    pub fn relations_to(&self, kind: &str, target: &str) -> usize {
        self.relations.iter().filter(|r| r.kind == kind && r.target == target && r.retracted.is_none()).count()
    }

    /// Whether it compares two arms (a reference given, or change.<metric>), rather than reading one level.
    pub fn compares(&self) -> bool {
        !self.reference.is_empty() || self.metric.starts_with("change.")
    }

    /// What it reads, as someone says it.
    pub fn reads(&self) -> String {
        let text = if !self.subject.is_empty() || !self.reference.is_empty() {
            let arms = if !self.reference.is_empty() {
                format!("{} vs {}", if self.subject.is_empty() { "candidate" } else { &self.subject }, self.reference)
            } else {
                self.subject.clone()
            };
            if arms.is_empty() {
                self.metric.clone()
            } else {
                format!("{}: {}", self.metric, arms)
            }
        } else {
            self.metric.clone()
        };
        let mut text = if self.estimand.is_empty() {
            text
        } else {
            format!("{text} ({})", self.estimand)
        };
        let mut scopes = Vec::new();
        for (label, selector) in [("where", &self.r#where), ("subject where", &self.subject_where), ("reference where", &self.reference_where)] {
            if selector.is_empty() { continue; }
            let mut keys: Vec<_> = selector.keys().collect();
            keys.sort();
            scopes.push(format!("{label} {}", keys.iter().map(|k| format!("{k}={}", crate::ledger::show(&selector[*k]))).collect::<Vec<_>>().join(", ")));
        }
        if !scopes.is_empty() { text.push_str(&format!(" [{}]", scopes.join("; "))); }
        text
    }

    pub fn scoped(&self) -> bool {
        !self.r#where.is_empty() || !self.subject_where.is_empty() || !self.reference_where.is_empty()
    }

    /// The quote of an ordering a file states (one arm beats another: --at-least 0 or --at-most 0), when this rule
    /// cites one: what its source is checked for is those words at their line, as no number on it states the 0.
    pub fn cited_ordering(&self) -> Option<&str> {
        let ordering = matches!(self.comparator.as_str(), "at_least" | "at_most") && self.target == Some(0.0);
        self.source.as_ref().and_then(|s| s.quote.as_deref()).filter(|q| ordering && !crate::rbyaml::py_strip(q).is_empty())
    }

    /// A cited ordering as a screen says it: the file's words verbatim, with its file and the line rb read them at.
    pub fn cited_ordering_said(&self) -> Option<String> {
        let q = self.cited_ordering()?;
        let s = self.source.as_ref()?;
        let line = s.resolved.as_ref().and_then(|r| r.line).or(s.line).filter(|l| *l != 0);
        let at = s.path.clone().unwrap_or_default() + &line.map(|l| format!(":{l}")).unwrap_or_default();
        Some(format!("{at}: \"{}\"{}", crate::view::on_one_line(q), if s.resolved.is_none() { " · not checked" } else { "" }))
    }

    /// What a cited rule's source is checked to state: its target, or an ordering's words.
    pub fn cited_value(&self) -> Value {
        match self.cited_ordering() {
            Some(q) => Value::Str(q.to_string()),
            None => Value::opt_f64(self.target),
        }
    }

    /// The whole rule as someone reads it before freezing, its numbers as declared.
    pub fn rule(&self) -> String {
        self.rule_with(crate::assess::declared)
    }

    /// The whole rule with its numbers said by `g`.
    pub fn rule_with(&self, g: fn(f64) -> String) -> String {
        let x = self.reads();
        let m = self.margin.map(g).unwrap_or_else(|| "None".into());
        let rule = match self.comparator.as_str() {
            "better" => format!("{x} better, its interval clear of 0"),
            "noninferior" => format!("{x} no worse than {m}, its interval's worse bound inside {m}"),
            "equivalent" => format!("{x} within ±{m}, its interval inside ±{m}"),
            cmp => {
                let sign = if cmp == "equals" {
                    "="
                } else if cmp == "at_most" {
                    "≤"
                } else {
                    "≥"
                };
                let t = self.target.map(g).unwrap_or_else(|| "None".into());
                let value = if cmp == "equals" { format!("{t} ± {}", self.tolerance.map(g).unwrap_or_else(|| "None".into())) } else { t };
                let head = match self.over.as_str() {
                    "mean" => x.clone(),
                    "each" => format!("each unit's {x}"),
                    _ => format!(
                        "{x}, {}",
                        if cmp == "equals" {
                            "its interval"
                        } else if cmp == "at_most" {
                            "upper bound"
                        } else {
                            "lower bound"
                        }
                    ),
                };
                format!("{head} {sign} {value}")
            }
        };
        let mut extra: Vec<String> = Vec::new();
        if self.min_n > 1 {
            extra.push(format!("n ≥ {}", self.min_n));
        }
        if let Some(n) = self.noise {
            extra.push(format!("noise {}", g(n)));
        }
        if extra.is_empty() {
            rule
        } else {
            format!("{rule} ({})", extra.join(", "))
        }
    }
}

/// Whether a run's config is compared with a setting: a population.* setting says how runs are counted, never what a
/// run was given.
pub fn config_compares(name: &str) -> bool {
    !name.starts_with("population.")
}

/// Whether a setting is found in a run's config only under its own full name: an env.* setting names the environment
/// the work expects (rb env check compares it with the machine), which a config states as such (an RL config's
/// env.name), never under a key that only shares its last name (trainer.cuda for env.cuda).
pub fn by_full_key(name: &str) -> bool {
    name.starts_with("env.")
}

impl ConfigCheck {
    /// The check as it bears on what the run was given: what an earlier rb kept of a setting no config is compared
    /// with is left out, so a run it marked stays counted.
    pub fn given(&self) -> ConfigCheck {
        let named = |m: &Dict| m.get("name").and_then(|n| n.as_str()).map_or(true, config_compares);
        ConfigCheck {
            path: self.path.clone(),
            sha256: self.sha256.clone(),
            matches: self.matches.iter().filter(|n| config_compares(n)).cloned().collect(),
            mismatches: self.mismatches.iter().filter(|m| named(m)).cloned().collect(),
            absent: self.absent.iter().filter(|n| config_compares(n)).cloned().collect(),
        }
    }
}

impl Comparison {
    pub fn key(&self) -> (String, String, String, String) {
        (self.subject.clone(), self.reference.clone(), self.metric.clone(), self.estimand.clone())
    }
}

// ---------------------------------------------------------------- where files live, and the schema

/// kind -> where a file of that kind lives under .rb/, as the format's schema titles them: what is added to an
/// experiment is a file of its own. `criteria` is the one file an earlier rb kept an experiment's criteria in, still read
/// and moved to a file per criterion on the next write.
pub const FILE_PATHS: &[(&str, &str)] = &[
    ("project", "project.yaml"),
    ("question", "questions/<id>.yaml"),
    ("experiment", "experiments/<id>/experiment.yaml"),
    ("variant", "experiments/<experiment>/variants/<name>.yaml"),
    ("note", "experiments/<experiment>/notes/<id>.yaml or notes/<id>.yaml"),
    ("criterion", "experiments/<experiment>/criteria/<id>.yaml"),
    ("run", "experiments/<experiment>/runs/<id>.yaml"),
    ("analysis", "experiments/<experiment>/analyses/<id>.yaml"),
    ("metric", "metrics/<id>.yaml"),
    ("resource", "resources/<id>.yaml"),
    ("decision", "decisions/<id>.yaml"),
    ("criteria", "experiments/<id>/criteria.yaml"),
];

/// The JSON Schemas pydantic gave the Python models: `rb schema all` (rb-v1.schema.json) and `rb schema <kind>`.
pub fn schemas() -> &'static Value {
    static S: OnceLock<Value> = OnceLock::new();
    S.get_or_init(|| crate::pyjson::loads(include_str!("schemas.json")).expect("schemas.json"))
}

// ---------------------------------------------------------------- places in an experiment, for the writes that change them

/// Where a setting sits in an experiment: (the variant's index, or None for a shared one; its index in that list).
pub type SettingAt = (Option<usize>, usize);

impl Experiment {
    pub fn variant_index(&self, name: &str) -> Option<usize> {
        self.variants.iter().position(|v| v.name == name && v.retracted.is_none())
    }
    pub fn variant_mut(&mut self, name: &str) -> Option<&mut Variant> {
        self.variants.iter_mut().find(|v| v.name == name && v.retracted.is_none())
    }
    /// split(name), as an index: the variant a `<variant>.<name>` names, and the bare name.
    pub fn split_index<'a>(&self, name: &'a str) -> (Option<usize>, &'a str) {
        if let Some((head, tail)) = name.split_once('.') {
            if !tail.is_empty() {
                if let Some(i) = self.variant_index(head) {
                    return (Some(i), tail);
                }
            }
        }
        (None, name)
    }
    pub fn holder(&self, at: Option<usize>) -> &Vec<Setting> {
        match at {
            Some(i) => &self.variants[i].settings,
            None => &self.settings,
        }
    }
    pub fn holder_mut(&mut self, at: Option<usize>) -> &mut Vec<Setting> {
        match at {
            Some(i) => &mut self.variants[i].settings,
            None => &mut self.settings,
        }
    }
    /// lookup(name), as a place.
    pub fn lookup_at(&self, name: &str) -> Option<SettingAt> {
        let (v, bare) = self.split_index(name);
        self.holder(v).iter().position(|s| s.name == bare && s.retracted.is_none()).map(|i| (v, i))
    }
    pub fn setting_at(&self, at: SettingAt) -> &Setting {
        &self.holder(at.0)[at.1]
    }
    pub fn setting_at_mut(&mut self, at: SettingAt) -> &mut Setting {
        &mut self.holder_mut(at.0)[at.1]
    }
    /// Where resolve_setting finds a setting: lookup(name), else the first live variant that has its own `name`.
    pub fn resolve_at(&self, name: &str) -> Option<SettingAt> {
        if let Some(a) = self.lookup_at(name) {
            return Some(a);
        }
        for (vi, v) in self.variants.iter().enumerate() {
            if v.retracted.is_some() {
                continue;
            }
            if let Some(si) = v.settings.iter().position(|s| s.name == name && s.retracted.is_none()) {
                return Some((Some(vi), si));
            }
        }
        None
    }
    /// all_settings(), as places.
    pub fn all_settings_at(&self) -> Vec<(String, SettingAt)> {
        let mut out: Vec<(String, SettingAt)> = self.settings.iter().enumerate().filter(|(_, s)| s.retracted.is_none()).map(|(i, s)| (s.name.clone(), (None, i))).collect();
        for (vi, v) in self.variants.iter().enumerate() {
            if v.retracted.is_some() {
                continue;
            }
            for (si, s) in v.settings.iter().enumerate() {
                if s.retracted.is_none() {
                    out.push((format!("{}.{}", v.name, s.name), (Some(vi), si)));
                }
            }
        }
        out
    }
}

// ---------------------------------------------------------------- the computed models, as rb's code makes them

impl Caveat {
    pub fn new(code: &str, subject: &str, problem: bool, text: impl Into<String>) -> Caveat {
        Caveat { code: code.into(), subject: subject.into(), problem, text: text.into() }
    }
    pub fn note(code: &str, subject: &str, text: impl Into<String>) -> Caveat {
        Caveat::new(code, subject, false, text)
    }
    pub fn problem(code: &str, subject: &str, text: impl Into<String>) -> Caveat {
        Caveat::new(code, subject, true, text)
    }
}

impl Reading {
    /// Reading(metric=...): every other field at its default.
    pub fn of(metric: &str) -> Reading {
        Reading {
            subject: String::new(),
            reference: String::new(),
            metric: metric.into(),
            estimand: String::new(),
            estimate: None,
            lo: None,
            hi: None,
            level: None,
            method: String::new(),
            unit: String::new(),
            n: None,
            draws: None,
            source: "runs".into(),
            basis: "runs".into(),
            produced_at: None,
            produced_basis: String::new(),
            at: String::new(),
            superseded: String::new(),
            why_no_interval: String::new(),
            design: Dict::new(),
        }
    }
}

impl Observation {
    /// Observation(run=, value=, counted=, basis=): every other field at its default.
    pub fn of(run: &str, value: f64, counted: bool, basis: &str) -> Observation {
        Observation {
            run: run.into(),
            unit: String::new(),
            arm: String::new(),
            value,
            counted,
            reason: String::new(),
            basis: basis.into(),
            per_run: Dict::new(),
            produced_at: None,
            produced_basis: String::new(),
            before_declared: false,
            repeats: None,
        }
    }
}

impl Units {
    pub fn zero() -> Units {
        Units { n: 0, unit: String::new(), runs: 0, repeats: 0, population: String::new() }
    }
}

impl Finding {
    /// Finding(criterion=, experiment=, statement=, rule=, origin=, role=, declared=): the axes at their defaults.
    pub fn base(criterion: &str, experiment: &str, statement: &str, rule: String, origin: &str, role: &str, declared: Declared) -> Finding {
        Finding {
            criterion: criterion.into(),
            experiment: experiment.into(),
            statement: statement.into(),
            rule,
            origin: origin.into(),
            role: role.into(),
            declared: Some(declared),
            comparison: None,
            result: "untested".into(),
            rule_outcome: "untested".into(),
            missed_units: Vec::new(),
            timing: "untested".into(),
            units: Units::zero(),
            integrity: Vec::new(),
            review: None,
            history: Vec::new(),
            prospective: None,
            observations: Vec::new(),
            caveats: Vec::new(),
            says: indexmap::IndexMap::new(),
            n: 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every kind lives where the format's schema says it does, and the schema has no file kind the table lacks.
    #[test]
    fn each_kind_lives_where_the_schema_says() {
        let defs = schemas().get("all").and_then(|a| a.get("data")).and_then(|d| d.get("$defs")).and_then(Value::as_dict).unwrap();
        for (kind, path) in FILE_PATHS {
            let name = format!("{}{}File", kind[..1].to_uppercase(), &kind[1..]);
            assert_eq!(defs.get(&name).and_then(|d| d.get_str("title")), Some(format!("{kind} file (.rb/{path})").as_str()), "{kind}");
        }
        assert_eq!(defs.values().filter(|d| d.get_str("title").map_or(false, |t| t.contains(" file (.rb/"))).count(), FILE_PATHS.len());
    }
}
