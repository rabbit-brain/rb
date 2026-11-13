//! PLAIN.md: the author's plain lines (UX3 4.2, the owner's yes to D1 (b)). A file in the repository, beside `.rb/`,
//! where the author or their agent gives each name the record uses one line in plain words, for a reader with no
//! background: the project, each question, experiment and variant, each metric and each setting. rb reads it and
//! writes nothing: no record file, no log line. It is the file's words, never rb's, so every surface says each line
//! with its place in the file (`PLAIN.md:7`), and the JSON marks it `voice: file`.
//!
//! The form is forgiving, so a hand can write it:
//! - a heading names a thing and the paragraph under it is its line (`## int8`, then one line; a line wrapped over
//!   several reads as one);
//! - or one list item names a thing and gives its line: `- int8: the smaller, faster copy` (`-` or `*`; `:`, ` - `,
//!   ` – ` or ` — ` between);
//! - a name may say its kind first (`variant int8`, `setting seed`), or its experiment (`int8/fp32`), when one name
//!   names two things; `project` names the project. Backticks, bold and a trailing colon around a name are read through,
//!   and a name matches as written, else whatever its case;
//! - a heading with no line under it is a section of the file (`# Plain words`, `## Variants`), and names nothing;
//! - code blocks, HTML comments and front matter are passed over.
//!
//! A line for a name the record does not hold is reported (`unknown`), never an error, and rb doctor names it. A name
//! given twice keeps its first line, and the second is reported.

use crate::ledger::Ledger;
use crate::value::{Dict, Value};
use std::path::Path;

/// The file's name, at the record's root.
pub const FILE: &str = "PLAIN.md";
/// The most rb reads of it.
const MAX_BYTES: u64 = 256 * 1024;
/// The most characters a line keeps; the rest is cut, with an ellipsis.
const MAX_LINE: usize = 600;
const KINDS: [&str; 6] = ["project", "question", "experiment", "variant", "metric", "setting"];

/// One plain line as the file gives it: the name as written, its kind when one was said, the line and where it is.
#[derive(Debug, Clone, PartialEq)]
pub struct Line {
    pub about: String,
    pub kind: Option<String>,
    pub name: String,
    pub text: String,
    pub line: usize,
}

/// Where PLAIN.md is at `root`: the file of that name, else one whose name is the same whatever its case.
pub fn path_at(root: &Path) -> Option<std::path::PathBuf> {
    let exact = root.join(FILE);
    if exact.is_file() {
        return Some(exact);
    }
    let mut found: Vec<std::path::PathBuf> = std::fs::read_dir(root)
        .ok()?
        .flatten()
        .filter(|e| e.file_name().to_string_lossy().eq_ignore_ascii_case(FILE))
        .map(|e| e.path())
        .filter(|p| p.is_file())
        .collect();
    found.sort();
    found.into_iter().next()
}

/// Whether `p` is inside `root` once links are followed: a PLAIN.md that is a link out of the repository is not read.
fn inside(root: &Path, p: &Path) -> bool {
    match (root.canonicalize(), p.canonicalize()) {
        (Ok(r), Ok(f)) => f.starts_with(r),
        _ => false,
    }
}

/// The file's lines at `root`, with its name as found. None where there is no such file, or rb cannot read it.
pub fn read(root: &Path) -> Option<(String, Vec<Line>)> {
    use std::io::Read;
    let p = path_at(root).filter(|p| inside(root, p))?;
    let mut bytes = Vec::new();
    std::fs::File::open(&p).ok()?.take(MAX_BYTES).read_to_end(&mut bytes).ok()?;
    let text = String::from_utf8_lossy(&bytes).into_owned();
    Some((p.file_name()?.to_string_lossy().into_owned(), parse(&text)))
}

/// A name as written, read through its marks: `**int8**:`, `` `top1` ``, `"seed"`.
fn bare(s: &str) -> String {
    let mut t = s.trim().trim_end_matches(':').trim();
    loop {
        let before = t;
        for (a, b) in [("**", "**"), ("`", "`"), ("*", "*"), ("\"", "\""), ("'", "'")] {
            if t.len() > a.len() + b.len() && t.starts_with(a) && t.ends_with(b) {
                t = t[a.len()..t.len() - b.len()].trim();
            }
        }
        t = t.trim_end_matches(':').trim();
        if t == before {
            return t.to_string();
        }
    }
}

/// The kind said before a name, and the name: `variant int8` gives (variant, int8). None where the words are no name
/// (a sentence, or nothing).
fn named(about: &str) -> Option<(Option<String>, String)> {
    let b = bare(about);
    let mut words = b.split_whitespace();
    let first = words.next()?;
    let rest: Vec<&str> = words.collect();
    let (kind, name) = match rest.as_slice() {
        [] => (None, first.to_string()),
        [one] if KINDS.iter().any(|k| k.eq_ignore_ascii_case(first)) => (Some(first.to_ascii_lowercase()), bare(one)),
        _ => return None,
    };
    if name.is_empty() || name.chars().count() > 120 {
        return None;
    }
    Some((kind, name))
}

/// A list item that names a thing and gives its line: `- int8: the smaller copy`.
fn item(line: &str) -> Option<(String, String)> {
    let t = line.trim_start();
    let body = marker(t)?;
    // the first separator after the name: ":" ends a name only where a space or the end follows (not `http://`)
    let mut best: Option<(usize, usize)> = None;
    for sep in [": ", " - ", " – ", " — "] {
        if let Some(i) = body.find(sep) {
            if best.map_or(true, |(b, _)| i < b) {
                best = Some((i, sep.len()));
            }
        }
    }
    let (i, n) = best?;
    let (about, text) = (body[..i].trim(), body[i + n..].trim());
    if about.is_empty() || text.is_empty() || about.split_whitespace().count() > 2 {
        return None;
    }
    Some((about.to_string(), text.to_string()))
}

/// A list item's words, its marker (`-`, `*` or `+`, then a space or a tab) taken off.
fn marker(t: &str) -> Option<&str> {
    let mut c = t.chars();
    match (c.next(), c.next()) {
        (Some('-' | '*' | '+'), Some(' ' | '\t')) => Some(t[2..].trim_start()),
        _ => None,
    }
}

/// A line's text as a screen can show it: control characters dropped (a tab is a space), and cut when long.
fn clean(t: &str) -> String {
    let t: String = t.chars().map(|c| if c == '\t' { ' ' } else { c }).filter(|c| !c.is_control()).collect();
    let t = t.trim();
    if t.chars().count() > MAX_LINE {
        t.chars().take(MAX_LINE - 1).collect::<String>().trim_end().to_string() + "…"
    } else {
        t.to_string()
    }
}

/// Whether a line is a thematic break (`---`, `***`, `___`), which ends a paragraph.
fn rule_line(t: &str) -> bool {
    let t: String = t.chars().filter(|c| !c.is_whitespace()).collect();
    t.len() >= 3 && (t.chars().all(|c| c == '-') || t.chars().all(|c| c == '*') || t.chars().all(|c| c == '_'))
}

/// The heading a line is, and its words: `## int8` gives "int8". Setext headings are not read.
fn heading(line: &str) -> Option<String> {
    let t = line.trim_start();
    if line.len() - t.len() > 3 {
        return None;
    }
    let hashes = t.chars().take_while(|c| *c == '#').count();
    if hashes == 0 || hashes > 6 {
        return None;
    }
    let rest = &t[hashes..];
    if !(rest.is_empty() || rest.starts_with(' ') || rest.starts_with('\t')) {
        return None;
    }
    // a closing run of #s is one only where a space comes before it (`## C#` keeps its #)
    let rest = rest.trim();
    let rest = match rest.trim_end_matches('#') {
        r if r.len() < rest.len() && (r.is_empty() || r.ends_with(' ') || r.ends_with('\t')) => r.trim(),
        _ => rest,
    };
    Some(rest.to_string())
}

/// The lines PLAIN.md's text gives, in the file's order.
pub fn parse(text: &str) -> Vec<Line> {
    // a byte order mark, and a file whose lines end in a carriage return alone, read as any other
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let text = if text.contains('\r') && !text.contains('\n') { text.replace('\r', "\n") } else { text.to_string() };
    let lines: Vec<&str> = text.lines().collect();
    let mut out: Vec<Line> = Vec::new();
    // the name a heading gave, its line, and the paragraph under it so far
    let mut open: Option<(String, Option<String>, String, usize)> = None;
    let mut para: Vec<String> = Vec::new();
    let mut fence: Option<String> = None;
    let mut comment = false;
    let mut i = 0;
    // front matter: a first line of --- up to the next
    if lines.first().map(|l| l.trim_end()) == Some("---") {
        if let Some(end) = lines.iter().skip(1).position(|l| matches!(l.trim_end(), "---" | "...")) {
            i = end + 2;
        }
    }
    let close = |open: &mut Option<(String, Option<String>, String, usize)>, para: &mut Vec<String>, out: &mut Vec<Line>| {
        if let Some((about, kind, name, line)) = open.take() {
            let text = clean(&para.join(" "));
            if !text.is_empty() {
                out.push(Line { about, kind, name, text, line });
            }
        }
        para.clear();
    };
    while i < lines.len() {
        let raw = lines[i];
        let n = i + 1;
        i += 1;
        // a code block, passed over
        let t = raw.trim_start();
        if let Some(f) = &fence {
            if t.starts_with(f.as_str()) {
                fence = None;
            }
            continue;
        }
        if t.starts_with("```") || t.starts_with("~~~") {
            fence = Some(t[..3].to_string());
            if !para.is_empty() {
                close(&mut open, &mut para, &mut out);
            }
            continue;
        }
        // an HTML comment, passed over (the words around it on its line are kept)
        let mut line = String::new();
        let mut rest = raw;
        loop {
            if comment {
                match rest.find("-->") {
                    Some(e) => {
                        comment = false;
                        rest = &rest[e + 3..];
                    }
                    None => break,
                }
            } else {
                match rest.find("<!--") {
                    Some(s) => {
                        line.push_str(&rest[..s]);
                        comment = true;
                        rest = &rest[s + 4..];
                    }
                    None => {
                        line.push_str(rest);
                        break;
                    }
                }
            }
        }
        let had_comment = line.len() != raw.len();
        if line.trim().is_empty() || rule_line(&line) {
            // a blank line ends a paragraph; a line that was only a comment does not
            if !had_comment && !para.is_empty() {
                close(&mut open, &mut para, &mut out);
            }
            continue;
        }
        if let Some(h) = heading(&line) {
            close(&mut open, &mut para, &mut out);
            open = named(&h).map(|(kind, name)| (h.clone(), kind, name, n));
            continue;
        }
        // a list item that names a thing makes the heading above it a section of such items (`## Variants`), and the
        // words between them an introduction, not a line
        if open.is_some() {
            if let Some((about, text)) = item(&line).filter(|(a, _)| named(a).is_some()) {
                open = None;
                para.clear();
                if let Some((kind, name)) = named(&about) {
                    out.push(Line { about, kind, name, text: clean(&text), line: n });
                }
                continue;
            }
        }
        if open.is_some() {
            if para.is_empty() {
                // the first line under a heading starts its paragraph; a list item's marker is read through
                if let Some((_, _, _, at)) = open.as_mut() {
                    *at = n;
                }
            }
            // a list item's or a quote's marker is read through
            let t = line.trim();
            let t = marker(t).or_else(|| t.strip_prefix("> ")).unwrap_or(t);
            para.push(t.trim().to_string());
            continue;
        }
        // outside a named heading: a list item that names a thing
        if let Some((about, text)) = item(&line) {
            if let Some((kind, name)) = named(&about) {
                out.push(Line { about, kind, name, text: clean(&text), line: n });
            }
        }
    }
    close(&mut open, &mut para, &mut out);
    out
}

/// One thing the record names: its kind, its id or name, and the experiment a variant or a setting is of.
#[derive(Debug, Clone, PartialEq)]
pub struct Thing {
    pub kind: &'static str,
    pub id: String,
    pub experiment: String,
    /// the names it answers to besides its id: a metric's aliases, the project's title
    pub also: Vec<String>,
}

/// Every name the record holds that a plain line can be for, standing (not retracted). Where a kind cannot be read,
/// its names are left out.
pub fn things(led: &Ledger) -> Vec<Thing> {
    let mut out: Vec<Thing> = Vec::new();
    fn add(out: &mut Vec<Thing>, kind: &'static str, id: String, experiment: String, also: Vec<String>) {
        if !id.is_empty() && !out.iter().any(|t| t.kind == kind && t.id == id && t.experiment == experiment) {
            out.push(Thing { kind, id, experiment, also });
        }
    }
    if let Ok(p) = led.project() {
        add(&mut out, "project", "project".into(), String::new(), vec![p.id.clone(), p.title.clone()]);
    }
    if let Ok(qs) = led.all::<crate::record::Question>() {
        for q in qs.iter().filter(|q| q.retracted.is_none()) {
            add(&mut out, "question", q.id.clone(), String::new(), Vec::new());
        }
    }
    if let Ok(ms) = led.all::<crate::record::Metric>() {
        for m in ms.iter().filter(|m| m.retracted.is_none()) {
            add(&mut out, "metric", m.id.clone(), String::new(), m.aliases.clone());
        }
    }
    if let Ok(es) = led.all::<crate::record::Experiment>() {
        for e in es.iter().filter(|e| e.retracted.is_none()) {
            add(&mut out, "experiment", e.id.clone(), String::new(), Vec::new());
            for v in e.variants.iter().filter(|v| v.retracted.is_none()) {
                add(&mut out, "variant", v.name.clone(), e.id.clone(), Vec::new());
                for s in v.settings.iter().filter(|s| s.retracted.is_none()) {
                    add(&mut out, "setting", s.name.clone(), e.id.clone(), Vec::new());
                }
            }
            for s in e.settings.iter().filter(|s| s.retracted.is_none()) {
                add(&mut out, "setting", s.name.clone(), e.id.clone(), Vec::new());
            }
            for v in &e.varies {
                add(&mut out, "setting", v.clone(), e.id.clone(), Vec::new());
            }
        }
    }
    // a metric a criterion names by use: `change.top1`, `int8.top1` and `top1` all read top1
    if let Ok(cs) = led.all::<crate::record::Criterion>() {
        let variants: Vec<String> = out.iter().filter(|t| t.kind == "variant").map(|t| t.id.clone()).collect();
        for c in cs.iter().filter(|c| c.retracted.is_none()) {
            let mut m = c.metric.as_str();
            for p in ["change.", "candidate.", "baseline.", "control.", "ablation."] {
                m = m.strip_prefix(p).unwrap_or(m);
            }
            if let Some((head, tail)) = m.split_once('.') {
                if variants.iter().any(|v| v == head) {
                    m = tail;
                }
            }
            if !out.iter().any(|t| t.kind == "metric" && (t.id == m || t.also.iter().any(|a| a == m))) {
                add(&mut out, "metric", m.to_string(), String::new(), Vec::new());
            }
        }
    }
    out
}

/// How nearly `name`, as a line gives it (with the kind it said), names `t`: 0 not at all, else 1, with 2 more where
/// it said the kind and 2 more where it said the experiment (`int8/fp32`); `fold` matches whatever the case.
fn names(t: &Thing, kind: Option<&str>, name: &str, fold: bool) -> u8 {
    if kind.map_or(false, |k| k != t.kind) {
        return 0;
    }
    let said = if kind.is_some() { 2 } else { 0 };
    let eq = |a: &str, b: &str| if fold { a.to_lowercase() == b.to_lowercase() } else { a == b };
    if matches!(t.kind, "variant" | "setting") {
        for sep in ['/', '.', ':'] {
            if let Some((e, n)) = name.split_once(sep) {
                if eq(e, &t.experiment) && eq(n, &t.id) {
                    return 3 + said;
                }
            }
        }
    }
    if eq(name, &t.id) || t.also.iter().any(|a| !a.is_empty() && eq(name, a)) {
        1 + said
    } else {
        0
    }
}

/// The file's lines matched against the record, as the JSON carries them (`plain`): each line with the things it is
/// for, the lines for names the record does not hold (`unknown`), and those for a name given twice (`twice`). None
/// where there is no PLAIN.md.
pub fn of(led: &Ledger) -> Option<Value> {
    let (file, lines) = read(&led.root)?;
    Some(matched(&file, &lines, &things(led)))
}

/// What `of` gives, from the lines and the things already read.
pub fn matched(file: &str, lines: &[Line], things: &[Thing]) -> Value {
    // the things each line is for: as written, else whatever the case
    // each line's things, with how nearly it names each: as written, else whatever the case
    let hits: Vec<Vec<(usize, u8)>> = lines
        .iter()
        .map(|l| {
            let by = |fold: bool| -> Vec<(usize, u8)> {
                (0..things.len()).map(|i| (i, names(&things[i], l.kind.as_deref(), &l.name, fold))).filter(|x| x.1 > 0).collect()
            };
            let exact = by(false);
            if exact.is_empty() { by(true) } else { exact }
        })
        .collect();
    // each thing's line: the one that names it most nearly (its experiment or kind said), then the first; a line it
    // loses to as nearly named is listed once under `twice`
    let mut owner: Vec<Option<(usize, u8)>> = vec![None; things.len()];
    let mut beaten: Vec<(usize, usize)> = Vec::new();
    for (li, hs) in hits.iter().enumerate() {
        for &(ti, near) in hs {
            match owner[ti] {
                None => owner[ti] = Some((li, near)),
                Some((_, was)) if near > was => owner[ti] = Some((li, near)),
                Some((prev, was)) if near == was && !beaten.contains(&(li, prev)) => beaten.push((li, prev)),
                _ => {}
            }
        }
    }
    let twice: Vec<Value> = beaten
        .iter()
        .map(|&(li, prev)| crate::obj! {"about" => lines[li].about.clone(), "line" => lines[li].line, "cite" => format!("{file}:{}", lines[li].line),
            "first" => lines[prev].line, "first_cite" => format!("{file}:{}", lines[prev].line)})
        .collect();
    let owner: Vec<Option<usize>> = owner.into_iter().map(|o| o.map(|x| x.0)).collect();
    let mut known: Vec<Value> = Vec::new();
    let mut project = Value::Null;
    for (li, l) in lines.iter().enumerate() {
        let for_: Vec<usize> = (0..things.len()).filter(|&ti| owner[ti] == Some(li)).collect();
        if for_.is_empty() {
            continue;
        }
        for ti in for_ {
            let t = &things[ti];
            let row = crate::obj! {
                "kind" => t.kind, "id" => t.id.clone(), "experiment" => t.experiment.clone(), "about" => l.about.clone(),
                "text" => l.text.clone(), "file" => file, "line" => l.line, "cite" => format!("{file}:{}", l.line), "voice" => "file",
                "holds_a_result" => holds_a_result(&l.text),
            };
            if t.kind == "project" {
                project = row.clone();
            }
            known.push(row);
        }
    }
    let unknown: Vec<Value> = lines
        .iter()
        .zip(&hits)
        .filter(|(_, h)| h.is_empty())
        .map(|(l, _)| crate::obj! {"about" => l.about.clone(), "kind" => l.kind.clone().unwrap_or_default(), "text" => l.text.clone(), "file" => file, "line" => l.line, "cite" => format!("{file}:{}", l.line)})
        .collect();
    crate::obj! {
        "file" => file, "voice" => "file", "project" => project, "lines" => Value::List(known),
        "unknown" => Value::List(unknown), "twice" => Value::List(twice),
    }
}

/// Whether a plain line says what a thing found rather than what it is: a number, or a word of comparison. rb counts it
/// and refuses nothing.
pub fn holds_a_result(text: &str) -> bool {
    // a number standing alone (`75.4`, `0.5%`, `-1`), never one inside a name (`INT8`, `ResNet-20`, `8-bit`)
    let number = |w: &str| {
        let w = w.trim_matches(|c: char| matches!(c, '(' | ')' | '[' | ']' | ',' | ';' | ':' | '.' | '!' | '?' | '"' | '\''));
        let w = w.trim_start_matches(['+', '-', '\u{2212}', '~', '\u{2248}']).trim_end_matches(['%', 'x', '\u{d7}']);
        !w.is_empty() && w.starts_with(|c: char| c.is_ascii_digit()) && w.chars().all(|c| c.is_ascii_digit() || c == '.' || c == ',')
    };
    if text.split_whitespace().any(number) {
        return true;
    }
    let lower = text.to_lowercase();
    let words: Vec<&str> = lower.split(|c: char| !c.is_alphanumeric()).filter(|w| !w.is_empty()).collect();
    words.windows(2).any(|w| matches!(w[0], "better" | "worse" | "higher" | "lower" | "faster" | "slower" | "more" | "less") && w[1] == "than")
        || words.iter().any(|w| matches!(*w, "beats" | "outperforms" | "outperform" | "outperformed" | "improves" | "improved" | "regresses" | "regressed"))
}

/// The line for thing `kind` `id` (of `experiment` for a variant or a setting) in `plain` as `of` gives it.
pub fn line_for<'a>(plain: &'a Value, kind: &str, id: &str, experiment: &str) -> Option<&'a Value> {
    plain.get("lines")?.as_list()?.iter().find(|l| {
        l.get_str("kind") == Some(kind) && l.get_str("id") == Some(id) && (experiment.is_empty() || l.get_str("experiment") == Some(experiment))
    })
}

/// rb doctor's check: what PLAIN.md names that the record does not hold. Never a problem.
pub fn doctor_check(led: &Ledger) -> Option<Value> {
    let plain = of(led)?;
    let file = plain.get_str("file").unwrap_or(FILE).to_string();
    let unknown = plain.get("unknown").and_then(Value::as_list).cloned().unwrap_or_default();
    let mut at: Vec<i64> = plain.get("lines").and_then(Value::as_list).map(|l| l.iter().filter_map(|x| x.get("line").and_then(Value::as_i64)).collect()).unwrap_or_default();
    at.dedup();
    let n = at.len();
    let detail = if unknown.is_empty() {
        format!("{file}: each of its {n} {} names something in the record", if n == 1 { "line" } else { "lines" })
    } else {
        let said: Vec<String> =
            unknown.iter().map(|u| format!("{} ({})", u.get_str("about").unwrap_or(""), u.get_str("cite").unwrap_or(""))).collect();
        format!("{file} has a line for {} the record does not hold: {}", if unknown.len() == 1 { "a name" } else { "names" }, said.join(", "))
    };
    let mut d: Dict = Dict::new();
    d.insert("check".into(), "plain_lines".into());
    d.insert("ok".into(), true.into());
    d.insert("detail".into(), detail.into());
    if !unknown.is_empty() {
        let (those, it) = if unknown.len() == 1 { ("that line", "it") } else { ("those lines", "them") };
        d.insert("fix".into(), format!("give {those} the name the record uses now, or delete {it}: until then no screen shows {it}").into());
    }
    d.insert("unknown".into(), Value::List(unknown));
    Some(Value::Dict(d))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn t(kind: &'static str, id: &str, exp: &str) -> Thing {
        Thing { kind, id: id.into(), experiment: exp.into(), also: Vec::new() }
    }

    #[test]
    fn headings_lists_and_marks_are_read() {
        let text = "---\ntitle: x\n---\n# Plain words\n\n## project\nCan a smaller copy of the model\nkeep its accuracy?\n\n## `int8`:\nthe smaller copy\n\n```\n## fp32\nnot read\n```\n<!-- ## seed\nhidden -->\n## Variants\n\n- **fp32**: the original model\n- a sentence: with words in it\n* setting seed - which random start\n";
        let ls = parse(text);
        let got: Vec<(&str, Option<&str>, &str, usize)> = ls.iter().map(|l| (l.name.as_str(), l.kind.as_deref(), l.text.as_str(), l.line)).collect();
        assert_eq!(
            got,
            vec![
                ("project", None, "Can a smaller copy of the model keep its accuracy?", 7),
                ("int8", None, "the smaller copy", 11),
                ("fp32", None, "the original model", 21),
                ("seed", Some("setting"), "which random start", 23),
            ]
        );
    }

    #[test]
    fn a_line_goes_to_what_it_names_and_the_rest_is_reported() {
        let things = vec![t("experiment", "int8", ""), t("variant", "int8", "int8"), t("variant", "fp32", "int8"), t("metric", "top1", "")];
        let ls = parse("## int8\nboth\n\n## variant int8\nthe smaller copy\n\n## FP32\nthe original\n\n## gone\nnothing\n\n## top1\nfirst\n\n## top1\nsecond\n");
        let v = matched("PLAIN.md", &ls, &things);
        let line = |k: &str, id: &str, e: &str| line_for(&v, k, id, e).and_then(|l| l.get_str("text")).map(str::to_string);
        assert_eq!(line("experiment", "int8", ""), Some("both".into()));
        assert_eq!(line("variant", "int8", "int8"), Some("the smaller copy".into()));
        assert_eq!(line("variant", "fp32", "int8"), Some("the original".into()));
        assert_eq!(line("metric", "top1", ""), Some("first".into()));
        let unknown: Vec<&str> = v.get("unknown").unwrap().as_list().unwrap().iter().map(|u| u.get_str("about").unwrap()).collect();
        assert_eq!(unknown, vec!["gone"]);
        assert_eq!(v.get("twice").unwrap().as_list().unwrap().len(), 1);
        // a name its experiment qualifies wins over a bare one, with a slash or a dot
        let ls = parse("- int8: bare\n- int8.int8: dotted\n");
        let v = matched("PLAIN.md", &ls, &things);
        assert_eq!(line_for(&v, "variant", "int8", "int8").and_then(|l| l.get_str("text")), Some("dotted"));
        assert_eq!(line_for(&v, "experiment", "int8", "").and_then(|l| l.get_str("text")), Some("bare"));
        assert!(v.get("twice").unwrap().as_list().unwrap().is_empty());
        assert!(v.get("lines").unwrap().as_list().unwrap().iter().all(|l| l.get_str("voice") == Some("file")));
    }

    #[test]
    fn a_hand_slips_are_read_through() {
        // a byte order mark, an introduction before a list, a marker with a tab, control bytes, a thematic break
        let ls = parse("\u{feff}## q1\nthe first\u{1b}[31m\n---\n## Variants\nThese are the arms.\n-\tint8: small\n- fp32: big\n## C#\nkept\n");
        let got: Vec<(&str, &str)> = ls.iter().map(|l| (l.name.as_str(), l.text.as_str())).collect();
        assert_eq!(got, vec![("q1", "the first[31m"), ("int8", "small"), ("fp32", "big"), ("C#", "kept")]);
        let long = format!("## q1\n{}\n", "a ".repeat(800));
        assert!(parse(&long)[0].text.chars().count() <= MAX_LINE);
    }

    #[test]
    fn a_result_in_a_line_is_counted() {
        assert!(holds_a_result("scores 75.4"));
        assert!(holds_a_result("better than fp32"));
        assert!(!holds_a_result("can it be smaller without being worse at it?"));
        assert!(!holds_a_result("INT8 ResNet-20, an 8-bit copy, rather than the original"));
        assert!(holds_a_result("it loses 0.5% of its accuracy"));
        assert!(!holds_a_result("the smaller, faster copy of the model"));
    }
}
