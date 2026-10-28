//! Next step: what to run next for a question, built from recorded facts only (SPRINT-OCT24 1.3, BP 1c). Track P.
//! A request in words, never a command (T10), in SHELL-SPEC section 12's form: "run one more <metric> measurement on
//! <short title>, <arms>, and record it in rb"; it is what Reproduce's `prompt` says too. A question's Next step
//! (`of`) says so for the first run rb suggests for it (QuestionBrief.next, rb status's open items in its scope), from
//! the criterion that suggestion would add a unit to; shell/model.rs puts it on questions[].next_step.

use crate::errors::Res;
use crate::ledger::{show, Ledger};
use crate::record::{Criterion, Experiment};
use crate::value::{Dict, Value};
use crate::view::QuestionBrief;

/// The states of a question or an experiment in which rb still suggests a run: open, planned, or investigating.
const MOVING: &[&str] = &["open", "planned", "investigating"];

/// A question's Next step: rb's request in words for the first run it suggests for the question, the first open item
/// in the question's scope (`in_scope`, the items QuestionBrief.next takes its commands from, before its cap of three)
/// whose command adds a run (`rb run add`) to a criterion of an experiment still open, planned or investigating; ""
/// when the question is decided otherwise, or rb suggests no run for it. `open` is rb status's open items. Words
/// only: the metric the criterion reads, the experiment's short title, the arms it compares and the settings it
/// selects runs by, never the command itself (T10).
pub fn of(led: &Ledger, b: &QuestionBrief, open: &[Value]) -> Res<String> {
    if !b.status.is_empty() && !MOVING.contains(&b.status.split(" · ").next().unwrap_or("").trim()) {
        return Ok(String::new());
    }
    for i in runs_suggested(b, open) {
        for id in criteria_of(i) {
            let Ok(c) = led.load::<Criterion>(&id) else { continue };
            let Ok(exp) = led.load::<Experiment>(&c.experiment) else { continue };
            let state = b.experiments.iter().find(|e| e.id == exp.id).map_or("open", |e| e.state.as_str());
            if c.retracted.is_none() && MOVING.contains(&state) {
                return for_criterion(led, &exp, &c, Arms::Suggested);
            }
        }
    }
    Ok(String::new())
}

/// The criteria an open item names: its subject, or for an experiment's item the criteria it folds (`criteria`).
pub fn criteria_of(i: &Value) -> Vec<String> {
    let folded = i.get("criteria").and_then(Value::as_list).map(|l| l.iter().filter_map(Value::as_str).map(str::to_string).collect::<Vec<_>>());
    folded.unwrap_or_else(|| vec![text(i, "subject")])
}

/// Whether an open item's command adds a run.
pub fn adds_a_run(i: &Value) -> bool {
    text(i, "do").starts_with("rb run add ")
}

fn text(i: &Value, k: &str) -> String {
    i.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

/// The open items in a question's scope whose command adds a run, in rb status's order: view::home's rule for the
/// items a brief lists (its experiments, the question, the criteria linked to it; for work filed under no question,
/// every criterion of its experiments), each item once.
pub fn runs_suggested<'v>(b: &QuestionBrief, open: &'v [Value]) -> Vec<&'v Value> {
    let eids: Vec<&str> = b.experiments.iter().map(|e| e.id.as_str()).collect();
    let mut scope: std::collections::HashSet<String> = eids.iter().map(|e| e.to_string()).collect();
    match &b.id {
        Some(q) => {
            scope.insert(q.clone());
            let direct = b.inquiry.get("direct").and_then(Value::as_list).map(|l| l.as_slice()).unwrap_or(&[]);
            scope.extend(direct.iter().map(|r| text(r, "criterion")));
        }
        None => scope.extend(b.experiments.iter().flat_map(|e| e.findings.iter()).map(|f| f.criterion_id.clone())),
    }
    open.iter()
        .filter(|i| adds_a_run(i))
        .filter(|i| {
            let subject = text(i, "subject");
            scope.contains(&subject) || eids.iter().any(|e| subject.starts_with(&format!("{e}/"))) || subject.split(", ").any(|s| scope.contains(s))
        })
        .collect()
}

/// How a request names a criterion's arms: as rb's suggested run names them, the reference first ("fp32 and int8",
/// the Next step), or as the criterion compares them ("int8 vs fp32", Reproduce's request).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Arms {
    Suggested,
    Versus,
}

/// The request for one more run a criterion reads: its metric, the experiment's short title, then the arms it compares
/// in `form`, the settings every run it reads holds ("at batch_size=256") and those it selects one arm's runs by
/// ("calib_images 128 and 256" where both arms name one, "int8 at calib_images=256" where one does). No arm where rb
/// cannot tell which variant a role names. One line: what a screen would hide is written as its escape.
pub fn for_criterion(led: &Ledger, exp: &Experiment, c: &Criterion, form: Arms) -> Res<String> {
    let (subject, reference, metric, problem) = led.arms(exp, c)?;
    let named = |a: &str| if problem.is_none() && !a.is_empty() && exp.variant(a).is_some() { a.to_string() } else { String::new() };
    let (subject, reference) = (named(&subject), named(&reference));
    // a variant read against itself is told apart by its settings alone
    let itself = subject == reference && !c.subject_where.is_empty() && !c.reference_where.is_empty();
    let mut on = short_title(&exp.title);
    let mut arms = match form {
        _ if itself => String::new(),
        Arms::Versus => versus(&subject, &reference),
        Arms::Suggested if subject == reference => subject.clone(),
        Arms::Suggested => and_list(&[reference.clone(), subject.clone()].into_iter().filter(|a| !a.is_empty()).collect::<Vec<_>>()),
    };
    // arms the title already names are not said again ("on INT8 against FP32, fp32 and int8"): both arms go, and one
    // arm of several is said as that arm of the experiment ("on the fp32 arm of INT8 against FP32"); a comparison's
    // order goes only where the title names its subject first ("int8 vs fp32" stays on "FP32 against INT8")
    let named: Vec<&String> = if itself { Vec::new() } else { [&subject, &reference].into_iter().filter(|a| !a.is_empty()).collect() };
    let in_order = form == Arms::Suggested || named.len() < 2 || subject == reference || title_order(&on, &subject, &reference);
    if !named.is_empty() && named.iter().all(|a| in_title(&on, a)) && in_order {
        if named.len() == 1 || subject == reference {
            if exp.live_variants().len() > 1 {
                on = format!("the {} arm of {on}", named[0]);
            }
        }
        arms = String::new();
    }
    // one side's selection is said with its arm, or as "its subject" where the arm has no variant's name
    let parts: Vec<String> =
        [arms, scope(&c.r#where, (&reference, &c.reference_where), (&subject, &c.subject_where))].into_iter().filter(|p| !p.is_empty()).collect();
    Ok(crate::ledger::design::visible(&request(&metric, &on, &parts.join(", "))))
}

/// Whether a title names an arm as a word of its own, in any case and with "-" and "_" read alike: "INT8 against
/// FP32" names fp32, "Per-channel against per-tensor scales" names per_channel, and "Batches sized for fp32-ref"
/// does not name fp32 (a word ends at a space or a mark, never at a hyphen).
pub fn in_title(title: &str, arm: &str) -> bool {
    title_words(title).iter().any(|w| *w == word(arm))
}

/// Whether a title names `first` as a word before it names `then`.
fn title_order(title: &str, first: &str, then: &str) -> bool {
    let words = title_words(title);
    let at = |a: &str| words.iter().position(|w| *w == word(a));
    matches!((at(first), at(then)), (Some(f), Some(t)) if f < t)
}

/// A title's words, as `word` reads each: a hyphen of any kind ("-", "‐", "‑", "–") joins a word, never ends it.
fn title_words(title: &str) -> Vec<String> {
    word(title).split(|ch: char| !(ch.is_alphanumeric() || ch == '-' || ch == '.')).map(|w| w.trim_end_matches('.').to_string()).filter(|w| !w.is_empty()).collect()
}

/// A name in lower case with "_", the minus sign and every dash Unicode names (its Dash_Punctuation, Pd) read as "-".
fn word(t: &str) -> String {
    t.to_lowercase().chars().map(|ch| if ch == '_' || ch == '\u{2212}' || dash(ch) { '-' } else { ch }).collect()
}

/// Unicode's Dash_Punctuation (general category Pd), every character of it (Unicode 16).
fn dash(ch: char) -> bool {
    matches!(
        ch,
        '-' | '\u{058a}' | '\u{05be}' | '\u{1400}' | '\u{1806}' | '\u{2010}'..='\u{2015}' | '\u{2e17}' | '\u{2e1a}' | '\u{2e3a}' | '\u{2e3b}' | '\u{2e40}' | '\u{2e5d}'
            | '\u{301c}' | '\u{3030}' | '\u{30a0}' | '\u{fe31}' | '\u{fe32}' | '\u{fe58}' | '\u{fe63}' | '\u{ff0d}' | '\u{10d6e}' | '\u{10ead}'
    )
}

/// The settings a criterion selects runs by, in words: "at <name>=<value>" for each every run it reads holds; then, for
/// each an arm's runs are selected by, "<name> <reference's> and <subject's>" where both arms name it, else "<arm> at
/// <name>=<value>" for the one arm that does ("its reference" or "its subject" where the arm has no variant's name).
pub fn scope(every: &Dict, reference: (&str, &Dict), subject: (&str, &Dict)) -> String {
    let mut parts: Vec<String> = Vec::new();
    let shared: Vec<String> = every.iter().map(|(k, v)| format!("{k}={}", show(v))).collect();
    if !shared.is_empty() {
        parts.push(format!("at {}", shared.join(", ")));
    }
    let (rname, rw) = reference;
    let (sname, sw) = subject;
    let mut names: Vec<&String> = rw.keys().collect();
    names.extend(sw.keys().filter(|k| !rw.contains_key(*k)));
    for k in names {
        match (rw.get(k), sw.get(k)) {
            (Some(r), Some(s)) if show(r) == show(s) => parts.push(format!("{k} {}", show(r))),
            (Some(r), Some(s)) => parts.push(format!("{k} {} and {}", show(r), show(s))),
            (Some(r), None) => parts.push(format!("{} at {k}={}", if rname.is_empty() { "its reference" } else { rname }, show(r))),
            (None, Some(s)) => parts.push(format!("{} at {k}={}", if sname.is_empty() { "its subject" } else { sname }, show(s))),
            (None, None) => {}
        }
    }
    parts.join(", ")
}

/// An experiment's short name: its title up to the first ": ", when that leaves a name of its own (8 characters or more);
/// else the whole title. rb cuts it here, so no surface cuts free text.
pub fn short_title(title: &str) -> String {
    match title.split_once(": ") {
        Some((head, _)) if head.trim().chars().count() >= 8 => head.trim().to_string(),
        _ => title.trim().to_string(),
    }
}

/// rb's request in words for one more measurement of `metric` on the experiment named `on`, of `arms` as the request
/// names them ("" for the experiment's own): "int8 vs fp32" from a criterion, "fp32 and int8" or "at batch_size=256,
/// calib_images 128 and 256" from a suggested run.
pub fn request(metric: &str, on: &str, arms: &str) -> String {
    if arms.is_empty() {
        format!("run one more {metric} measurement on {on} and record it in rb")
    } else {
        format!("run one more {metric} measurement on {on}, {arms}, and record it in rb")
    }
}

/// The arms a criterion compares, as a request names them: "<subject> vs <reference>", the one it names alone, or "".
pub fn versus(subject: &str, reference: &str) -> String {
    match (subject.is_empty(), reference.is_empty()) {
        (false, false) => format!("{subject} vs {reference}"),
        (false, true) => subject.to_string(),
        (true, false) => reference.to_string(),
        (true, true) => String::new(),
    }
}

/// "a", "a and b", "a, b and c".
pub fn and_list(items: &[String]) -> String {
    match items {
        [] => String::new(),
        [one] => one.clone(),
        [init @ .., last] => format!("{} and {last}", init.join(", ")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_request_names_what_to_measure_and_never_gives_a_command() {
        // SHELL-SPEC section 12: the metric, the short title, the arms, and nothing a run's own setting would add
        assert_eq!(request("top1", "INT8 against FP32", &versus("int8", "fp32")), "run one more top1 measurement on INT8 against FP32, int8 vs fp32, and record it in rb");
        assert_eq!(request("top1", "Calibration set size", &versus("int8", "")), "run one more top1 measurement on Calibration set size, int8, and record it in rb");
        assert_eq!(request("top1", "INT8 against FP32", &versus("", "")), "run one more top1 measurement on INT8 against FP32 and record it in rb");
        let arms = and_list(&["fp32".to_string(), "int8".to_string()]);
        assert_eq!(request("latency_ms", "INT8 latency", &arms), "run one more latency_ms measurement on INT8 latency, fp32 and int8, and record it in rb");
        for r in [request("top1", "e1", ""), request("top1", "e1", "int8 vs fp32")] {
            assert!(!r.contains("rb run add") && !r.contains("fresh"), "{r}");
        }
    }

    #[test]
    fn a_title_names_an_arm_only_as_a_word_of_its_own() {
        assert!(in_title("INT8 against FP32", "fp32") && in_title("INT8 against FP32", "int8"));
        assert!(in_title("Per-channel against per-tensor scales", "per_channel"));
        assert!(in_title("Is it fp32.", "fp32") && in_title("fp32 (the reference)", "fp32"));
        assert!(!in_title("Batches sized for fp32-ref", "fp32"));
        assert!(!in_title("INT8 latency", "int"));
        assert!(!in_title("Calibration set size", "int8"));
        // a hyphen of any kind joins a word
        for title in ["INT8 against FP32\u{2011}ref", "INT8 against FP32\u{2010}ref", "INT8 against FP32\u{2013}ref", "INT8 against FP32\u{2212}ref", "INT8 against FP32\u{2e40}ref", "INT8 against FP32\u{058a}ref", "INT8 against FP32\u{301c}ref"] {
            assert!(!in_title(title, "fp32"), "{title}");
        }
        assert!(title_order("INT8 against FP32", "int8", "fp32") && !title_order("INT8 against FP32", "fp32", "int8"));
        assert!(!title_order("INT8 latency", "int8", "fp32"));
    }

    #[test]
    fn a_short_title_is_the_title_before_its_first_colon_when_that_names_it() {
        assert_eq!(short_title("INT8 against FP32: does quantisation cost accuracy"), "INT8 against FP32");
        assert_eq!(short_title("INT8: a study"), "INT8: a study");
        assert_eq!(short_title("Calibration set size"), "Calibration set size");
    }

    #[test]
    fn a_scope_reads_as_words() {
        let d = |pairs: &[(&str, Value)]| -> Dict { pairs.iter().map(|(k, v)| (k.to_string(), v.clone())).collect() };
        let none = Dict::new();
        assert_eq!(scope(&none, ("fp32", &none), ("int8", &none)), "");
        assert_eq!(scope(&d(&[("batch_size", Value::Int(256))]), ("fp32", &none), ("int8", &none)), "at batch_size=256");
        assert_eq!(
            scope(
                &d(&[("batch_size", Value::Int(256)), ("backend", Value::Str("fbgemm".into()))]),
                ("int8", &d(&[("calib_images", Value::Int(128))])),
                ("int8", &d(&[("calib_images", Value::Int(256))]))
            ),
            "at batch_size=256, backend=fbgemm, calib_images 128 and 256"
        );
        // a selection one arm's runs are read by is that arm's, never both arms'
        assert_eq!(scope(&none, ("fp32", &none), ("int8", &d(&[("calib_images", Value::Int(512))]))), "int8 at calib_images=512");
        assert_eq!(scope(&none, ("fp32", &d(&[("width", Value::Int(256))])), ("", &none)), "fp32 at width=256");
        assert_eq!(scope(&none, ("", &d(&[("width", Value::Int(256))])), ("", &d(&[("depth", Value::Int(4))]))), "its reference at width=256, its subject at depth=4");
        assert_eq!(scope(&none, ("int8", &d(&[("per_channel", Value::Bool(true))])), ("int8", &d(&[("per_channel", Value::Bool(false))]))), "per_channel true and false");
    }

    #[test]
    fn a_list_reads_as_words() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        assert_eq!((and_list(&s(&[])), and_list(&s(&["a"])), and_list(&s(&["a", "b"])), and_list(&s(&["a", "b", "c"]))), (String::new(), "a".into(), "a and b".into(), "a, b and c".into()));
    }
}
