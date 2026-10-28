//! The ledger's helpers: names of metrics, rule keys, a run's variants, config values, differences between snapshots,
//! and the caveat lists findings say.

use super::*;
use crate::pyfloat;

pub const ROLE_PREFIXES: &[&str] = &["baseline", "candidate"];

/// str.partition(sep): (head, found, tail).
pub fn partition<'a>(s: &'a str, sep: &str) -> (&'a str, bool, &'a str) {
    match s.split_once(sep) {
        Some((h, t)) => (h, true, t),
        None => (s, false, ""),
    }
}

/// str.rpartition(sep)
pub fn rpartition<'a>(s: &'a str, sep: &str) -> (&'a str, bool, &'a str) {
    match s.rsplit_once(sep) {
        Some((h, t)) => (h, true, t),
        None => ("", false, s),
    }
}

/// How many a line counts, then the first of them: "1 record ...: why", "3 records ..., first why".
pub fn count_first(n: usize, one: &str, many: &str, first: &str) -> String {
    if n == 1 {
        format!("1 {one}: {first}")
    } else {
        format!("{n} {many}, first {first}")
    }
}

/// What only a newer rb can read here, counted, then the first of it. A part of a record this rb reads (`part`: a
/// question call's snapshot, a relation's scope) is said as a part, not as a record it cannot read.
pub fn newer_said(unread: &[Dict]) -> String {
    let first = unread.first().and_then(|u| u.get("why")).map(pyjson::py_str).unwrap_or_default();
    let parts = unread.iter().filter(|u| u.get("part").map_or(false, Value::truthy)).count();
    let (one, many) = if parts == 0 {
        ("record here was written by a newer rb and this one cannot read it", "records here were written by a newer rb and this one cannot read them")
    } else if parts == unread.len() {
        ("record here has a part only a newer rb reads", "records here have parts only a newer rb reads")
    } else {
        ("record here needs a newer rb", "records here need a newer rb")
    };
    count_first(unread.len(), one, many, &first)
}

/// The known name closest to `name` within a misspelling's reach (one edit for a name of up to five characters, two
/// for a longer one; a swap of two neighbours is one), or None. Names whose digits differ (top1, top5) are distinct
/// metrics, never a misspelling of each other; among equally close names, the first in order.
pub fn near_name<'a>(name: &str, known: impl IntoIterator<Item = &'a str>) -> Option<&'a str> {
    let digits = |s: &str| s.chars().filter(|c| c.is_ascii_digit()).collect::<String>();
    let a: Vec<char> = name.to_lowercase().chars().collect();
    let reach = if a.len() <= 5 { 1 } else { 2 };
    let mut best: Option<(usize, &str)> = None;
    for k in known {
        if k == name || digits(k) != digits(name) {
            continue;
        }
        let b: Vec<char> = k.to_lowercase().chars().collect();
        let d = edits(&a, &b);
        if d <= reach && best.map_or(true, |(bd, bk)| d < bd || (d == bd && k < bk)) {
            best = Some((d, k));
        }
    }
    best.map(|(_, k)| k)
}

/// The edits (insert, delete, change, or a swap of two neighbours) that turn one name into the other.
fn edits(a: &[char], b: &[char]) -> usize {
    let (n, m) = (a.len(), b.len());
    let mut d = vec![vec![0usize; m + 1]; n + 1];
    for (i, row) in d.iter_mut().enumerate() {
        row[0] = i;
    }
    for (j, cell) in d[0].iter_mut().enumerate() {
        *cell = j;
    }
    for i in 1..=n {
        for j in 1..=m {
            let cost = usize::from(a[i - 1] != b[j - 1]);
            d[i][j] = (d[i - 1][j] + 1).min(d[i][j - 1] + 1).min(d[i - 1][j - 1] + cost);
            if i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1] {
                d[i][j] = d[i][j].min(d[i - 2][j - 2] + 1);
            }
        }
    }
    d[n][m]
}

/// name.split(".")[-1]
pub fn last_part(s: &str) -> &str {
    s.rsplit('.').next().unwrap_or(s)
}

fn alias<'a>(aliases: &'a IndexMap<String, String>, name: &'a str) -> &'a str {
    aliases.get(name).map(|s| s.as_str()).unwrap_or(name)
}

/// A metric name with its role prefix turned into the variant's name and its base name through the catalogue's aliases:
/// candidate.cross_entropy -> int8.val_loss.
pub fn canon(name: &str, exp: &Experiment, aliases: &IndexMap<String, String>) -> String {
    let (head0, sep, rest) = partition(name, ".");
    let mut head = head0.to_string();
    if sep && ROLE_PREFIXES.contains(&head0) {
        let vs = exp.by_role(head0);
        if vs.len() == 1 {
            head = vs[0].name.clone();
        }
    }
    if sep && (head == "change" || exp.variant(&head).is_some() || ROLES.contains(&head.as_str())) {
        let (inner_head, isep, inner_rest) = partition(rest, ".");
        if head == "change" && isep && exp.variant(inner_head).is_some() {
            return format!("change.{inner_head}.{}", alias(aliases, inner_rest));
        }
        return format!("{head}.{}", alias(aliases, rest));
    }
    alias(aliases, name).to_string()
}

/// A criterion's rule, as a freeze hashes it.
pub fn rule_key(c: &Criterion) -> Value {
    let mut key = vec![
        Value::Str(c.id.clone()),
        Value::Str(c.metric.clone()),
        Value::Str(c.comparator.clone()),
        Value::opt_f64(c.target),
        Value::opt_f64(c.tolerance),
        Value::Str(c.over.clone()),
        Value::Int(c.min_n),
        Value::opt_f64(c.noise),
    ];
    let mut extra = Dict::new();
    for (k, v) in [("subject", Value::Str(c.subject.clone())), ("reference", Value::Str(c.reference.clone())), ("estimand", Value::Str(c.estimand.clone())), ("margin", Value::opt_f64(c.margin))] {
        if !(v.is_null() || v == Value::Str(String::new())) {
            extra.insert(k.into(), v);
        }
    }
    if c.role != "primary" {
        extra.insert("role".into(), Value::Str(c.role.clone()));
    }
    for (name, selector) in [("where", &c.r#where), ("subject_where", &c.subject_where), ("reference_where", &c.reference_where)] {
        if !selector.is_empty() { extra.insert(name.into(), Value::Dict(selector.clone())); }
    }
    let mut links: Vec<&QuestionLink> = c.question_links.iter().filter(|l| l.retracted.is_none()).collect();
    links.sort_by(|a, b| a.question.cmp(&b.question));
    if !links.is_empty() {
        extra.insert("question_links".into(), Value::List(links.iter().map(|l| crate::obj! {
            "question" => l.question.clone(), "question_text" => l.question_text.clone(), "when_met" => l.when_met.clone()
        }).collect()));
    }
    let mut relations: Vec<&CriterionRelation> = c.relations.iter().filter(|r| r.retracted.is_none()).collect();
    relations.sort_by(|a, b| (&a.kind, &a.target).cmp(&(&b.kind, &b.target)));
    if !relations.is_empty() {
        extra.insert("relations".into(), Value::List(relations.iter().map(|r| crate::obj! {
            "kind" => r.kind.clone(), "target" => r.target.clone(), "orientation" => r.orientation.clone(),
            "source_scope" => Value::Dict(r.source_scope.clone()), "target_scope" => Value::Dict(r.target_scope.clone())
        }).collect()));
    }
    if !extra.is_empty() {
        key.push(Value::Str(canonical(&Value::Dict(extra))));
    }
    Value::List(key)
}

/// The variants a run reports numbers for: the heads of its dotted metric names.
pub fn variants_of(r: &Run) -> std::collections::BTreeSet<String> {
    r.metrics.keys().filter(|k| k.contains('.')).map(|k| k.split('.').next().unwrap_or("").to_string()).collect()
}

/// A setting's value in a run's flattened config: by its full name, else by its last part when only one key ends so.
pub fn config_value(flat: &Dict, name: &str) -> (Value, bool) {
    if let Some(v) = flat.get(name) {
        return (v.clone(), true);
    }
    let leaf = last_part(name);
    let hits: Vec<&String> = flat.keys().filter(|k| last_part(k) == leaf).collect();
    if hits.len() == 1 {
        return (flat[hits[0]].clone(), true);
    }
    (Value::Null, false)
}

/// A list item's identity for a merge: its id, else its name, when that is text.
fn key_of(x: &Value) -> Option<String> {
    let Value::Dict(d) = x else { return None };
    let pick = match d.get("id") {
        Some(v) if v.truthy() => Some(v),
        _ => d.get("name"),
    };
    match pick {
        Some(Value::Str(s)) => Some(s.clone()),
        _ => None,
    }
}

/// Two sides' versions of one record merged against what they share, the way someone resolving the merge by hand would
/// keep both: a field changed on one side only takes that change; mappings merge key by key; lists of named or
/// identified things merge item by item, and other lists keep every item either side has (less what either removed).
/// Err when both sides changed the same value differently; None stands for a field a version does not have.
pub fn merge3(base: Option<&Value>, ours: Option<&Value>, theirs: Option<&Value>) -> Result<Option<Value>, ()> {
    match (ours, theirs) {
        (Some(o), Some(t)) if canon_json(o) == canon_json(t) => return Ok(Some(o.clone())),
        (None, None) => return Ok(None),
        _ => {}
    }
    if let (Some(b), Some(o)) = (base, ours) {
        if canon_json(b) == canon_json(o) {
            return Ok(theirs.cloned());
        }
    }
    if let (Some(b), Some(t)) = (base, theirs) {
        if canon_json(b) == canon_json(t) {
            return Ok(ours.cloned());
        }
    }
    if base.is_none() && (ours.is_none() || theirs.is_none()) {
        return Ok(if ours.is_none() { theirs.cloned() } else { ours.cloned() });
    }
    match (ours, theirs) {
        (Some(Value::Dict(o)), Some(Value::Dict(t))) => {
            let empty = Dict::new();
            let b = match base {
                Some(Value::Dict(b)) => b,
                _ => &empty,
            };
            let mut out = Dict::new();
            let keys: Vec<&String> = o.keys().chain(t.keys().filter(|k| !o.contains_key(*k))).collect();
            for k in keys {
                if let Some(v) = merge3(b.get(k), o.get(k), t.get(k))? {
                    out.insert(k.clone(), v);
                }
            }
            Ok(Some(Value::Dict(out)))
        }
        (Some(Value::List(o)), Some(Value::List(t))) => {
            let empty: Vec<Value> = Vec::new();
            let b = match base {
                Some(Value::List(b)) => b,
                _ => &empty,
            };
            if b.iter().chain(o).chain(t).all(|x| key_of(x).is_some()) {
                let index = |side: &Vec<Value>| -> IndexMap<String, Value> { side.iter().map(|x| (key_of(x).unwrap(), x.clone())).collect() };
                let (bk, ok, tk) = (index(b), index(o), index(t));
                let keys: Vec<String> = ok.keys().cloned().chain(tk.keys().filter(|k| !ok.contains_key(*k)).cloned()).collect();
                let mut out = Vec::new();
                for k in keys {
                    if let Some(v) = merge3(bk.get(&k), ok.get(&k), tk.get(&k))? {
                        out.push(v);
                    }
                }
                return Ok(Some(Value::List(out)));
            }
            let set = |side: &Vec<Value>| -> std::collections::HashSet<String> { side.iter().map(canon_json).collect() };
            let (bs, os, ts) = (set(b), set(o), set(t));
            let gone: std::collections::HashSet<String> = bs.into_iter().filter(|c| !(os.contains(c) && ts.contains(c))).collect();
            let mut out = Vec::new();
            let mut seen = std::collections::HashSet::new();
            for x in o.iter().chain(t.iter()) {
                let c = canon_json(x);
                if !gone.contains(&c) && seen.insert(c) {
                    out.push(x.clone());
                }
            }
            Ok(Some(Value::List(out)))
        }
        _ => Err(()),
    }
}

/// A form with every list in a fixed order: a merge resolved by hand may keep both sides' items in either order.
pub fn normal(x: &Value) -> Value {
    match x {
        Value::Dict(d) => Value::Dict(d.iter().map(|(k, v)| (k.clone(), normal(v))).collect()),
        Value::List(l) => {
            let mut items: Vec<Value> = l.iter().map(normal).collect();
            items.sort_by_key(canon_json);
            Value::List(items)
        }
        other => other.clone(),
    }
}

/// Whether a form is two sides merged over their base with both kept, in whatever order someone kept list items.
pub fn both_kept(base: Option<&Dict>, ours: &Dict, theirs: &Dict, form: &Dict) -> bool {
    let base = base.map(|b| Value::Dict(b.clone()));
    match merge3(base.as_ref(), Some(&Value::Dict(ours.clone())), Some(&Value::Dict(theirs.clone()))) {
        Ok(Some(m)) => canon_json(&normal(&m)) == canon_json(&normal(&Value::Dict(form.clone()))),
        _ => false,
    }
}

/// "lr 0.001 -> 0.002, seed 1 -> 2" for the first four settings that differ.
pub fn diff(then: &Dict, now_: &Dict) -> String {
    diff_arms(then, now_, &[], &[])
}

/// The settings that changed; for a reading of named arms, only theirs and the shared ones (a setting of another
/// variant is not what moved this reading).
pub fn diff_arms(then: &Dict, now_: &Dict, arms: &[String], owned: &[String]) -> String {
    let read = |k: &str| -> bool {
        arms.is_empty() || arms.iter().any(|a| k.starts_with(&format!("{a}."))) || !owned.iter().any(|v| k.starts_with(&format!("{v}.")))
    };
    let mut keys: Vec<&String> = then.keys().chain(now_.keys()).collect();
    keys.sort();
    keys.dedup();
    let null = Value::Null;
    let changed: Vec<String> = keys
        .into_iter()
        .filter(|k| read(k))
        .filter(|k| canonical(then.get(*k).unwrap_or(&null)) != canonical(now_.get(*k).unwrap_or(&null)))
        .map(|k| format!("{k} {} -> {}", show(then.get(k).unwrap_or(&null)), show(now_.get(k).unwrap_or(&null))))
        .collect();
    let mut s = changed.iter().take(4).cloned().collect::<Vec<_>>().join(", ");
    if changed.len() > 4 {
        s.push_str(&format!(" and {} more", changed.len() - 4));
    }
    s
}

pub fn clamp(x: f64) -> f64 {
    if x.is_finite() {
        x
    } else {
        f64::MAX.copysign(x)
    }
}

/// a - b without the float noise of the subtraction itself, rounded to 12 significant digits of the larger operand.
pub fn minus(a: f64, b: f64) -> f64 {
    let scale = a.abs().max(b.abs());
    if scale == 0.0 || !scale.is_finite() {
        return a - b;
    }
    pyfloat::round_to(a - b, 11 - scale.log10().floor() as i32)
}

/// math.isclose
pub fn isclose(a: f64, b: f64, rel_tol: f64, abs_tol: f64) -> bool {
    if a == b {
        return true;
    }
    if a.is_infinite() || b.is_infinite() {
        return false;
    }
    let diff = (b - a).abs();
    (diff <= (rel_tol * b).abs()) || (diff <= (rel_tol * a).abs()) || (diff <= abs_tol)
}

/// A receipt's repository state, with the exact code when the tree had uncommitted changes, and the repositories of
/// their own inside it that the code leaves out (`nested`).
pub fn with_code(root: &Path, attached: Option<Dict>) -> Option<Dict> {
    let mut attached = attached?;
    if attached.get("dirty").map_or(false, |v| v.truthy()) {
        let snap = sources::code_snapshot(root, DIR);
        if let Some(snap) = snap {
            let left_out = snap.get("left_out").cloned().unwrap_or(Value::Null);
            if snap.get("tree").map_or(false, |v| v.truthy()) {
                attached.insert("tree".into(), snap.get("tree").cloned().unwrap());
                if left_out.truthy() {
                    attached.insert("left_out".into(), left_out);
                }
            } else if !snap.is_empty() {
                attached.insert("tree".into(), Value::Null);
                attached.insert("left_out".into(), left_out);
            }
            if let Some(n) = snap.get("nested") {
                attached.insert("nested".into(), n.clone());
            }
        }
    } else if let Some(commit) = attached.get("commit").filter(|v| v.truthy()).map(pyjson::py_str) {
        if let Some(tree) = sources::keep_tree(root, &commit) {
            attached.insert("tree".into(), Value::Str(tree));
        }
    }
    Some(attached)
}

pub fn same_source(a: Option<&Source>, b: Option<&Source>) -> bool {
    match (a, b) {
        (None, None) => true,
        (Some(x), Some(y)) => {
            let mut dx = x.dump(false);
            let mut dy = y.dump(false);
            dx.shift_remove("resolved");
            dy.shift_remove("resolved");
            Value::Dict(dx) == Value::Dict(dy)
        }
        _ => false,
    }
}

pub fn describe_change(name: &str, old: Option<&Setting>, new: &Setting) -> String {
    let Some(old) = old else {
        let extra = if new.per_run {
            " (per-run)"
        } else if new.required {
            ""
        } else {
            " (optional)"
        };
        return format!("setting {name}: new, {}{extra}", show(&new.value));
    };
    let mut parts: Vec<String> = Vec::new();
    if !same_value(&old.value, &new.value) || (old.status != new.status && (old.status == "unknown" || new.status == "unknown")) {
        let was = if !old.value.is_null() && !new.value.is_null() && old.value.type_name() != new.value.type_name() {
            format!(" (was {})", old.value.type_name())
        } else {
            String::new()
        };
        parts.push(format!("{} -> {}{was}", show(&old.value), show(&new.value)));
    }
    if old.required != new.required {
        parts.push(if old.required { "required -> optional".into() } else { "optional -> required".into() });
    }
    if old.per_run != new.per_run {
        parts.push(if new.per_run { "now per-run".into() } else { "no longer per-run".into() });
    }
    let (was_factor, is_factor) = (old.design_factor == Some(true), new.design_factor == Some(true));
    if was_factor != is_factor {
        parts.push(if is_factor { "now a fixed design factor".into() } else { "no longer a fixed design factor".into() });
    }
    if !same_source(old.source.as_ref(), new.source.as_ref()) {
        parts.push(format!(
            "source {} -> {}",
            old.source.as_ref().map(|s| s.label()).unwrap_or_else(|| "none".into()),
            new.source.as_ref().map(|s| s.label()).unwrap_or_else(|| "none".into())
        ));
    }
    if old.note != new.note {
        parts.push("note changed".into());
    }
    let (was, now_) = (old.cited.as_ref(), new.cited.as_ref());
    let differ = match (was, now_) {
        (None, None) => false,
        (Some(a), Some(b)) => Value::Dict(a.dump(false)) != Value::Dict(b.dump(false)),
        _ => true,
    };
    if differ {
        parts.push(format!(
            "cited {} -> {}",
            was.map(|c| show(&c.value)).unwrap_or_else(|| "none".into()),
            now_.map(|c| show(&c.value)).unwrap_or_else(|| "none".into())
        ));
    }
    format!("setting {name}: {}", if parts.is_empty() { "unchanged".to_string() } else { parts.join("; ") })
}

fn group_key(c: &Caveat) -> Option<(String, String)> {
    if !c.subject.is_empty() && c.text.starts_with(&format!("{} ", c.subject)) {
        Some((c.code.clone(), c.text[c.subject.len() + 1..].to_string()))
    } else {
        None
    }
}

/// Notes that say the same thing about several runs, said once with the runs listed.
pub fn grouped(caveats: Vec<Caveat>) -> Vec<Caveat> {
    let mut out: Vec<Caveat> = Vec::new();
    let mut groups: IndexMap<(String, String), Vec<Caveat>> = IndexMap::new();
    for c in caveats {
        if let Some(key) = group_key(&c) {
            if let Some(g) = groups.get_mut(&key) {
                g.push(c);
                continue;
            }
            groups.insert(key, vec![c.clone()]);
        }
        out.push(c);
    }
    for c in out.iter_mut() {
        let Some(key) = group_key(c) else { continue };
        if let Some(many) = groups.get(&key) {
            if many.len() > 1 {
                let ids: Vec<&str> = many.iter().map(|x| x.subject.as_str()).collect();
                let shown = crate::assess::first_few(&ids.iter().map(|i| i.to_string()).collect::<Vec<_>>(), 4, ", ");
                *c = Caveat::new(
                    &c.code,
                    &ids.join(", "),
                    c.problem,
                    format!("{} runs {} ({shown})", ids.len(), key.1),
                );
            }
        }
    }
    out
}

pub fn unique(caveats: Vec<Caveat>) -> Vec<Caveat> {
    let mut seen: HashSet<(String, String, String)> = HashSet::new();
    let mut out = Vec::new();
    for c in caveats {
        let key = (c.code.clone(), c.subject.clone(), c.text.clone());
        if seen.insert(key) {
            out.push(c);
        }
    }
    out
}

/// The conflict or stale caveat `setting_caveats` gave a setting: its source states another value, or no longer states
/// it (a verified one as this read finds it, or a conflict on record).
pub fn source_problem<'a>(caveats: &'a [Caveat], exp: &Experiment, name: &str) -> Option<&'a Caveat> {
    let subject = format!("{}/{name}", exp.id);
    caveats.iter().find(|c| c.subject == subject && (c.code == "conflict" || c.code == "stale"))
}

/// The text of a time rb writes: an ISO date or time as UTC to the millisecond, or None for anything else.
pub fn norm_time(text: &str) -> Option<String> {
    crate::pytime::norm_time(text)
}

#[cfg(test)]
mod tests {
    use super::{count_first, near_name, newer_said};

    /// A misspelling names the close name; a far one, a name whose digits differ, or the name itself names none.
    #[test]
    fn a_close_name_is_named_and_a_distinct_one_is_not() {
        let known = ["val_loss", "top1", "latency_ms"];
        assert_eq!(near_name("val_los", known), Some("val_loss"));
        assert_eq!(near_name("vla_loss", known), Some("val_loss"));
        assert_eq!(near_name("Val_Loss", known), Some("val_loss"));
        assert_eq!(near_name("top5", known), None);
        assert_eq!(near_name("throughput", known), None);
        assert_eq!(near_name("val_loss", known), None);
        assert_eq!(near_name("tpo1", known), Some("top1"));
    }

    /// One record is said in the singular with what is wrong with it; many are counted, with the first of them.
    #[test]
    fn one_record_and_many_are_said_as_they_are() {
        let why = "metric top1 differs from what rb wrote: it was edited outside rb";
        let said = |n| count_first(n, "record in .rb/ changed outside rb", "records in .rb/ changed outside rb", why);
        assert_eq!(said(1), format!("1 record in .rb/ changed outside rb: {why}"));
        assert_eq!(said(3), format!("3 records in .rb/ changed outside rb, first {why}"));
        let newer = |why: &str| crate::dict! {"kind" => "run", "id" => "r1", "why" => why};
        assert_eq!(newer_said(&[newer("a")]), "1 record here was written by a newer rb and this one cannot read it: a");
        assert_eq!(newer_said(&[newer("a"), newer("b")]), "2 records here were written by a newer rb and this one cannot read them, first a");
        let part = |why: &str| crate::dict! {"kind" => "decision", "id" => "d1", "why" => why, "part" => true};
        assert_eq!(newer_said(&[part("a")]), "1 record here has a part only a newer rb reads: a");
        assert_eq!(newer_said(&[part("a"), part("b")]), "2 records here have parts only a newer rb reads, first a");
        assert_eq!(newer_said(&[newer("a"), part("b")]), "2 records here need a newer rb, first a");
    }
}
