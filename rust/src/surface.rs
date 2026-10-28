//! Projections from the record onto the user's surfaces: every string it emits is a stored field verbatim, a computed
//! number with its unit, or a word from the one shipped vocabulary.

use crate::pyfloat::fmt_float;
use crate::pyjson::py_str;
use crate::record::Reading;
use crate::value::Value;

/// An integrity problem, each code as one short phrase, shared by every surface.
pub const REASONS: &[(&str, &str)] = &[
    ("unknown", "unknown settings"), ("conflict", "a source contradicts a setting"), ("stale", "a source changed"),
    ("confound", "an undeclared difference"), ("drift", "the frozen spec changed"), ("edited_outside_rb", "edited outside rb"),
    ("shallow_clone", "the clone is shallow"),
    ("cited_unchecked", "the cited number was not checked"), ("cited_source_changed", "the cited source changed"),
    ("inherited_without_parent", "inherited without a parent"), ("rejected_setting", "a setting someone rejected"),
    ("not_comparable", "settings differ from the source's"), ("state_newer", "part of the record needs a newer rb"),
    ("experiment_retracted", "its experiment was retracted"), ("retracted", "it was retracted"),
    ("analysis_source_changed", "the document stating it changed"), ("input_retracted", "what it read was retracted"),
    ("input_superseded", "what it read was superseded"), ("arms", "its arms are ambiguous"),
    ("units", "more units than the runs it read form"),
];

pub fn reason(code: &str) -> Option<&'static str> {
    REASONS.iter().find(|(c, _)| *c == code).map(|(_, p)| *p)
}

/// `d.get(k)` as Python would put it in an f-string, with a default for a missing key.
fn pget(d: &Value, k: &str, default: &str) -> String {
    match d.get(k) {
        Some(v) => py_str(v),
        None => default.to_string(),
    }
}

/// A finding's headline, exactly as every surface says it: the rule and the result, 'criterion met · loss'.
pub fn standing_words(f: &Value) -> String {
    match f.get("says").filter(|s| s.truthy()).and_then(|s| s.get("headline")).filter(|h| h.truthy()) {
        Some(h) => py_str(h),
        None => pget(f, "rule_outcome", "untested"),
    }
}

/// Whether a finding's met rule is drawn green: nothing wrong under it, and met across its whole interval, not only on
/// its estimate; a reading with no interval (a point only) is met on its estimate alone; a gate met is policy, never
/// green (D4).
pub fn green(f: &Value) -> bool {
    fn listed<'a>(f: &'a Value, k: &str) -> &'a [Value] {
        match f.get(k) {
            Some(Value::List(l)) => l,
            _ => &[],
        }
    }
    let bound = |k: &str| f.get("comparison").and_then(|c| c.get(k)).map_or(false, |b| b.as_f64().is_some());
    pget(f, "rule_outcome", "untested") == "meets"
        && pget(f, "role", "") != "gate"
        && bound("lo")
        && bound("hi")
        && pget(f, "result", "") != "point_only"
        && listed(f, "integrity").is_empty()
        && !listed(f, "caveats").iter().any(|c| pget(c, "code", "") == "straddles")
}

/// The first integrity problem as its one shared phrase, with the count of the rest.
pub fn why_not(problems: &[Value]) -> String {
    if problems.is_empty() {
        return String::new();
    }
    let code = pget(&problems[0], "code", "");
    let first = reason(&code).map(|s| s.to_string()).unwrap_or_else(|| code.replace('_', " "));
    let more = if problems.len() > 1 { format!(" and {} more", problems.len() - 1) } else { String::new() };
    format!("integrity: {first}{more}")
}

/// The criterion as the knob it is: '≤ 0.05 nats', '≥ 4 R/wk', '= 0.517 ± 0.005', 'better', 'no worse than 0.05 nats', its
/// numbers as declared.
pub fn criterion_text(comparator: &str, target: Option<f64>, tolerance: Option<f64>, unit: &str, margin: Option<f64>, over: &str) -> String {
    let g = |x: Option<f64>| x.map_or_else(|| "None".into(), crate::assess::declared);
    let u = if unit.is_empty() { String::new() } else { format!(" {unit}") };
    match comparator {
        "better" => return "better".into(),
        "noninferior" => return format!("no worse than {}{u}", g(margin)),
        "equivalent" => return format!("within ±{}{u}", g(margin)),
        _ => {}
    }
    let lead = match over {
        "mean" => "",
        "each" => "each unit ",
        "bound" => "bound ",
        _ => "",
    };
    if comparator == "equals" {
        let tol = match tolerance {
            Some(t) => format!(" ± {}", g(Some(t))),
            None => String::new(),
        };
        return format!("{lead}= {}{tol}{u}", g(target));
    }
    format!("{lead}{} {}{u}", if comparator == "at_most" { "≤" } else { "≥" }, g(target))
}

/// One criterion as an instrument row: the comparison it reads, with its interval, against the rule someone declared,
/// and the headline of its reading.
#[derive(Debug, Clone, Default)]
pub struct Row {
    pub metric: String,
    pub value: String,
    pub criterion: String,
    pub margin: String,
    pub runs: String,
    pub standing: String,
    pub why: String,
    pub good: bool,
    pub negative: bool,
}

/// What a criterion row needs of the stored criterion.
pub struct CriterionFields<'a> {
    pub metric: &'a str,
    pub reads: String,
    pub comparator: &'a str,
    pub target: Option<f64>,
    pub tolerance: Option<f64>,
    pub margin: Option<f64>,
    pub over: &'a str,
    /// the metric's direction in the catalogue: minimize, maximize or none
    pub direction: &'a str,
}

/// Project one criterion and its computed finding (the dump) into the instrument row.
pub fn row(c: &CriterionFields, finding: &Value, unit: &str) -> Row {
    let reading: Option<Reading> = finding.get("comparison").filter(|v| v.truthy()).and_then(|v| {
        let mut errs = Vec::new();
        let mut loc = Vec::new();
        Reading::validate(v, &mut loc, &mut errs).filter(|_| errs.is_empty())
    });
    // the estimate, its interval and its unit, in the parts rb's comparison text is made of, its rule's numbers as
    // the finding's rule sentence says them
    let value = reading
        .as_ref()
        .and_then(|r| crate::assess::reading_parts(Some(r), unit, &crate::assess::rule_numbers_of(c.comparator, c.over, c.target, c.tolerance, c.margin, c.direction, r)))
        .map_or_else(|| "no reading yet".to_string(), |p| p.head());
    let units = finding.get("units").filter(|u| u.truthy()).cloned().unwrap_or(Value::empty_dict());
    let n = units.get("n").filter(|n| n.truthy()).and_then(|n| n.as_i64()).unwrap_or(0);
    let outcome = pget(finding, "rule_outcome", "untested");
    let problems: Vec<Value> = finding.get("integrity").and_then(|v| v.as_list()).cloned().unwrap_or_default();
    let mut margin = String::new();
    if ["at_most", "at_least", "equals"].contains(&c.comparator) && c.over == "mean" {
        if let (Some(r), Some(t)) = (&reading, c.target) {
            if let Some(e) = r.estimate {
                let m = match c.comparator {
                    "at_most" => t - e,
                    "at_least" => e - t,
                    _ => c.tolerance.unwrap_or(0.0) - (e - t).abs(),
                };
                // an estimate the rule reads as on its edge is said as that edge, with no margin left
                let m = if m.abs() <= crate::assess::close(e, t) { 0.0 } else { m };
                margin = format!("margin {}", fmt_float(m, ".4g"));
            }
        }
    }
    let unit_word = units.get("unit").filter(|u| u.truthy()).map(py_str).unwrap_or_else(|| "unit".into());
    let metric = if c.reads.is_empty() { c.metric.to_string() } else { c.reads.clone() };
    Row {
        metric,
        value,
        criterion: criterion_text(c.comparator, c.target, c.tolerance, unit, c.margin, c.over),
        margin,
        runs: if n != 0 { format!("{n} {}", crate::assess::plural(&unit_word, n)) } else { String::new() },
        standing: standing_words(finding),
        why: why_not(&problems),
        good: green(finding),
        negative: outcome == "misses" && finding.get("role").map(py_str).as_deref() != Some("gate"),
    }
}

/// The row as one line, for surfaces that set a single string.
pub fn numbers_line(r: &Row) -> String {
    let mut parts = vec![format!("{} {}", r.metric, r.value), format!("criterion {}", r.criterion)];
    if !r.margin.is_empty() {
        parts.push(r.margin.clone());
    }
    if !r.runs.is_empty() {
        parts.push(r.runs.clone());
    }
    parts.push(if r.why.is_empty() { r.standing.clone() } else { format!("{}, {}", r.standing, r.why) });
    parts.join(" · ")
}

// a gate met is policy, not a finding: its words draw neutral (D4)
const GOOD_WORDS: &[&str] = &["meets", "reproduced"];
const BAD_WORDS: &[&str] = &["misses", "not reproduced"];
const WARN_WORDS: &[&str] = &["gate missed", "undetermined", "unresolved", "point only", "gate undetermined"];

/// One part of a headline without its count: '2 criteria met' is 'meets'.
fn uncounted(part: &str) -> &str {
    let word = crate::rbyaml::py_strip(part);
    let word = match word.split_once(' ') {
        Some((head, rest)) if !head.is_empty() && head.chars().all(|c| c.is_numeric()) => rest,
        _ => word,
    };
    match word {
        "criterion met" | "criteria met" => "meets",
        "criterion not met" | "criteria not met" => "misses",
        "criterion undetermined" | "criteria undetermined" => "undetermined",
        "criterion untested" | "criteria untested" => "untested",
        _ => word,
    }
}

/// How a surface colours one part of a headline: good, bad, warn, or dim.
/// A count prefix ('2 criteria met') is read past.
pub fn standing_class(part: &str) -> String {
    let word = uncounted(part);
    if GOOD_WORDS.contains(&word) {
        "good".into()
    } else if BAD_WORDS.contains(&word) {
        "bad".into()
    } else if WARN_WORDS.contains(&word) {
        "warn".into()
    } else {
        "dim".into()
    }
}

/// A part of a standing, coloured as standing_class does, but a met word that counts a finding not drawn green is dim.
pub fn part_class(part: &str, muted: &[String]) -> String {
    let cls = standing_class(part);
    if cls == "good" && muted.iter().any(|m| m == uncounted(part)) {
        "dim".into()
    } else {
        cls
    }
}

/// A screen's Next step: a request in words, assembled from stored names only.
pub fn next_step(exp_title: &str, metric: &str, per_run: &[String]) -> String {
    let extra = if per_run.is_empty() { String::new() } else { format!(" at a fresh {}", per_run.join(" and a fresh ")) };
    format!("\"run one more {metric} measurement on {exp_title}{extra} and record it in rb\"")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::value::Value;

    /// Every integrity problem a finding can carry has its one phrase here, and nowhere else: a reason never has two
    /// wordings.
    #[test]
    fn one_phrase_for_each_integrity_problem() {
        let mut codes: Vec<&str> = REASONS.iter().map(|(c, _)| *c).collect();
        codes.sort();
        let mut expected = vec![
            "conflict", "stale", "unknown", "rejected_setting", "inherited_without_parent", "confound", "drift", "edited_outside_rb",
            "shallow_clone", "state_newer", "cited_unchecked", "cited_source_changed", "not_comparable", "experiment_retracted", "retracted",
            "input_retracted", "input_superseded", "analysis_source_changed", "arms", "units",
        ];
        expected.sort();
        assert_eq!(codes, expected);
        assert!(REASONS.iter().all(|(_, phrase)| !phrase.is_empty()));
    }

    /// The user's screen colours a headline's words itself (rb's Python package draws it): its three sets of words
    /// are these three, word for word, so the screen, the page and rb open never colour one word two ways.
    #[test]
    fn the_screen_colours_the_same_words() {
        let screen = include_str!("../../src/rabbit_brain/tui.py");
        for (name, words) in [("GOOD_WORDS", GOOD_WORDS), ("BAD_WORDS", BAD_WORDS), ("WARN_WORDS", WARN_WORDS)] {
            let line = screen.lines().find(|l| l.starts_with(&format!("{name} = {{"))).unwrap_or_else(|| panic!("tui.py has no {name}"));
            let mut theirs: Vec<&str> = line.split('"').skip(1).step_by(2).collect();
            let mut ours: Vec<&str> = words.to_vec();
            theirs.sort();
            ours.sort();
            assert_eq!(theirs, ours, "{name}");
        }
    }

    /// A met rule is green only across a whole interval with nothing wrong under it: one met on a point only, with no
    /// interval to read, is met on its estimate alone, as one whose interval crosses its threshold is.
    #[test]
    fn green_is_a_rule_met_across_its_whole_interval() {
        let met = |result: &str, comparison: Value, caveat: &str, problem: bool| {
            crate::obj! {
                "rule_outcome" => "meets", "result" => result, "comparison" => comparison,
                "caveats" => if caveat.is_empty() { crate::list![] } else { crate::list![crate::obj! {"code" => caveat}] },
                "integrity" => if problem { crate::list![crate::obj! {"code" => "conflict"}] } else { crate::list![] }
            }
        };
        let interval = || crate::obj! {"estimate" => 0.8, "lo" => 0.75, "hi" => 0.85};
        assert!(green(&met("measured", interval(), "", false)));
        assert!(!green(&met("point_only", crate::obj! {"estimate" => 0.8}, "", false)));
        assert!(!green(&met("measured", crate::obj! {"estimate" => 0.8, "lo" => 0.75, "hi" => Value::Null}, "", false)));
        assert!(!green(&met("measured", interval(), "straddles", false)));
        assert!(!green(&met("measured", interval(), "", true)));
        assert!(!green(&crate::obj! {"rule_outcome" => "misses", "result" => "measured", "comparison" => interval()}));
    }

    /// When something is wrong with what a finding rests on, the row says the first problem in its shared phrase and
    /// counts the rest.
    #[test]
    fn why_not_is_the_first_problem_and_a_count() {
        let problems = vec![crate::obj! {"code" => "confound"}, crate::obj! {"code" => "stale"}];
        assert_eq!(why_not(&problems), "integrity: an undeclared difference and 1 more");
        assert_eq!(why_not(&[]), "");
        let unnamed: Vec<Value> = vec![crate::obj! {"code" => "some_new_code"}];
        assert_eq!(why_not(&unnamed), "integrity: some new code");
    }
}
