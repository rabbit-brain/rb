//! The History view: sittings and the Latest changes line (BP 5H). Track H.
//!
//! Drawn from the document alone (`history.summary`, `history.sittings`, `home.latest`), as every view is: it reads no
//! ledger. A sitting is the model's (history.rs splits the log on time only); a line's sentence is rb's, drawn plain,
//! with each record it names linked in place. Its reason is drawn by its voice, by form: the user's own as their why,
//! verbatim (`why: ...`); a session's as rb's note on it (`note: ... · not confirmed`), never in the user's voice, and
//! on a call the user confirmed as `note: ...` alone, as rb status says it: the call is confirmed, its note never is.
//! Nothing here names who wrote: the document holds no writer, and a sitting is split on time, not on writer.
//!
//! Lines and the Overview's newest sitting are drawn newest first, as the sittings are. Before the document holds
//! sittings (5H not merged), each part draws nothing and History keeps its log rows.

use crate::value::Value;

/// History's body under its heading and lede: the summary ("47 changes recorded"), then each sitting, newest first,
/// with its head and its lines, and under the head of each day's newest sitting the one link to that day's rows of the
/// raw log. `held` says whether the page holds an element of that id, so a line links only where a link lands.
pub fn sittings(doc: &Value, held: &dyn Fn(&str) -> bool) -> String {
    let sits: Vec<&Value> = list(doc, "history.sittings").iter().filter(|s| s.is_dict()).collect();
    if sits.is_empty() {
        return String::new();
    }
    let mut days: Vec<&str> = Vec::new();
    let body: String = sits
        .iter()
        .map(|s| {
            let day = day_of(text(s, "first"));
            let raw = if days.contains(&day) {
                String::new()
            } else {
                days.push(day);
                raw_log(doc, day, held)
            };
            format!("<li class=\"sit\">{}{raw}{}</li>", head(s), lines(doc, s, usize::MAX, held))
        })
        .collect();
    // where one sitting holds every change, its head already counts them: the summary would say it twice
    let summary = text(doc, "history.summary");
    let summary = if summary.is_empty() || sits.len() == 1 { String::new() } else { format!("<p class=\"dim sm hsum\">{}</p>", esc(summary)) };
    format!("{summary}<ol class=\"sits\">{body}</ol>")
}

/// The one link of a day to its rows of the raw log: the newest row the page holds of that day; nothing where it holds
/// none (a page with no log rows, a time rb cannot read).
fn raw_log(doc: &Value, day: &str, held: &dyn Fn(&str) -> bool) -> String {
    if day.is_empty() {
        return String::new();
    }
    let newest = list(doc, "history.log")
        .iter()
        .filter(|e| day_of(text(e, "at")) == day)
        .filter_map(|e| count(e, "log_index"))
        .filter(|i| held(&format!("log-{i}")))
        .max();
    match newest {
        Some(i) => format!("<p class=\"sm hraw\"><a class=\"rawl\" href=\"#log-{i}\" aria-label=\"{RAW_LOG} of {}\">{RAW_LOG}</a></p>", esc(day)),
        None => String::new(),
    }
}

/// The Overview's "Latest changes" line, linked to History: rb's line for the newest sitting (`home.latest.line`,
/// which counts made and confirmed calls only); before History's sittings exist, "last write <time>"; nothing where
/// the document holds neither.
pub fn latest(doc: &Value) -> String {
    let line = text(doc, "home.latest.line");
    let said = if line.is_empty() {
        let last = text(doc, "home.last_write.at");
        if last.is_empty() {
            return String::new();
        }
        format!("{LAST_WRITE}{last}")
    } else {
        line.to_string()
    };
    format!("<p class=\"counts latest\"><a href=\"#history\">{}</a></p>", esc(&said))
}

/// The Overview's "Recent calls and changes": the newest sitting's head, its newest lines (up to four, each with its
/// verb and its reason), then the link to every change. Nothing before the document holds sittings.
pub fn recent(doc: &Value, held: &dyn Fn(&str) -> bool) -> String {
    let Some(newest) = list(doc, "history.sittings").iter().find(|s| s.is_dict()) else {
        return String::new();
    };
    format!(
        "<h2>{RECENT}</h2><div class=\"recent\">{}{}</div><p class=\"sm\"><a href=\"#history\">{EVERY_CHANGE}</a></p>",
        head(newest),
        lines(doc, newest, RECENT_LINES, held)
    )
}

/// History's body as the frame draws it without its page's ids at hand (R's `view`): a line links to the views the
/// frame always draws (the lists, each question's view and each experiment's), never to a card or a log row.
pub fn view(doc: &Value) -> String {
    sittings(doc, &|id| framed(doc, id))
}

/// The Overview's newest sitting as the frame draws it without its page's ids at hand (R's `overview_sitting`), linked
/// as `view` links.
pub fn overview_sitting(doc: &Value) -> String {
    recent(doc, &|id| framed(doc, id))
}

/// Whether the frame draws an element of this id whatever else the page holds: a list view, or the view of a question
/// or an experiment the document holds.
fn framed(doc: &Value, id: &str) -> bool {
    let holds = |path: &str, want: &str| list(doc, path).iter().any(|x| text(x, "id") == want);
    FRAMED.contains(&id) || id.strip_prefix("q-").is_some_and(|q| holds("questions", q)) || id.strip_prefix("x-").is_some_and(|x| holds("experiments", x))
}

/// The list views the frame draws on every page.
const FRAMED: [&str; 7] = ["overview", "research", "criteria", "experiments", "runs", "resources", "history"];

/// rb's words here (SHELL-SPEC section 12).
const RECENT: &str = "Recent calls and changes";
const EVERY_CHANGE: &str = "Every change, newest first \u{2192}";
const LAST_WRITE: &str = "last write ";
const RAW_LOG: &str = "Raw log";
/// How many of the newest sitting's lines the Overview shows.
const RECENT_LINES: usize = 4;

fn head(s: &Value) -> String {
    format!("<p class=\"sith\">{}</p>", esc(text(s, "head")))
}

/// A sitting's lines, newest first, at most `max`. A line leads with what happened, rb's sentence; how many writes of
/// the log it tells is not said on it: the day's one Raw log link leads there.
fn lines(doc: &Value, s: &Value, max: usize, held: &dyn Fn(&str) -> bool) -> String {
    let day = day_of(text(s, "first"));
    let items: String = list(s, "lines").iter().filter(|l| l.is_dict()).rev().take(max).map(|l| line(doc, l, day, held)).collect();
    format!("<ol class=\"hlist\">{items}</ol>")
}

fn line(doc: &Value, l: &Value, day: &str, held: &dyn Fn(&str) -> bool) -> String {
    let refs: Vec<&str> = list(l, "refs").iter().filter_map(Value::as_str).collect();
    // the time is a number, in the screen's own type; only an id is set in monospace
    let mut out = format!(
        "<li class=\"hev\"><span class=\"ht\">{}</span><div class=\"hs\"><span class=\"hsen\">{}</span>",
        esc(&span(text(l, "first"), text(l, "last"), day)),
        linked(doc, text(l, "sentence"), &refs, held)
    );
    let why = text(l, "why");
    if !why.is_empty() {
        out.push_str(&format!("<span class=\"why\"><span class=\"sr\"> \u{b7} </span>why: <span class=\"au\">{}</span></span>", esc(why)));
    }
    let note = text(l, "note");
    if !note.is_empty() {
        // a confirmation's note stays the session's: the user confirmed the call, not the reason given for it, which
        // reads in rb's note form alone, as rb status says it (ledger::session_note), never "confirmed" then "not confirmed"
        let said = if confirmation(text(l, "sentence")) {
            format!("note: <span class=\"au\">{}</span>", esc(note))
        } else {
            format!("note: <span class=\"au\">{}</span> \u{b7} not confirmed", esc(note))
        };
        out.push_str(&format!("<span class=\"why note\"><span class=\"sr\"> \u{b7} </span>{said}</span>"));
    }
    out.push_str("</div></li>");
    out
}

/// Whether rb's sentence is a confirmation's (history::CONFIRMED), whatever the case of its first letter: a document an
/// earlier rb wrote opens it in lower case.
fn confirmation(sentence: &str) -> bool {
    let n = crate::history::CONFIRMED.len();
    sentence.get(..n).is_some_and(|s| s.eq_ignore_ascii_case(crate::history::CONFIRMED))
}

/// A line's time: "09:25", or "09:25 to 09:40", on the sitting's own day; with the day where it is another one (a
/// sitting across midnight); rb's text as it stands where it is not a time rb reads.
fn span(first: &str, last: &str, day: &str) -> String {
    let at = |t: &str| match (day_of(t), clock(t)) {
        (d, Some(c)) if d == day => c.to_string(),
        (d, Some(c)) => format!("{d} {c}"),
        _ => t.to_string(),
    };
    let (a, b) = (at(first), at(last));
    if a.trim().is_empty() {
        b
    } else if b.is_empty() || a == b {
        a
    } else if clock(first).is_none() || clock(last).is_none() {
        // a time rb cannot read stands alone
        a
    } else {
        format!("{a} to {b}")
    }
}

/// "2026-10-19" of "2026-10-19 09:25 UTC".
fn day_of(t: &str) -> &str {
    match t.split_once(' ') {
        Some((d, _)) if is_day(d) => d,
        _ => "",
    }
}

/// "09:25" of "2026-10-19 09:25 UTC".
fn clock(t: &str) -> Option<&str> {
    let mut parts = t.split(' ');
    let (d, c, z) = (parts.next()?, parts.next()?, parts.next()?);
    let ok = is_day(d) && z == "UTC" && parts.next().is_none() && c.len() == 5 && c.as_bytes()[2] == b':' && c.bytes().enumerate().all(|(i, b)| i == 2 || b.is_ascii_digit());
    ok.then_some(c)
}

fn is_day(d: &str) -> bool {
    d.len() == 10 && d.bytes().enumerate().all(|(i, b)| if i == 4 || i == 7 { b == b'-' } else { b.is_ascii_digit() })
}

/// rb's sentence, escaped, with each record it names set as an id (monospace) where it names it, the first time the id
/// stands as a word of its own (never inside another id, "e1" in "e12"), and linked where a link lands.
fn linked(doc: &Value, sentence: &str, refs: &[&str], held: &dyn Fn(&str) -> bool) -> String {
    // each ref once, by its id, or by its words where the sentence says it by them (a run, an analysis rb minted an id
    // for: FIX7); the longest text first, so a shorter one never takes the place of a longer one that holds it ("e1"
    // inside "a run of e1")
    let mut said: Vec<(&str, String, bool)> = Vec::new();
    for r in refs {
        if said.iter().any(|(x, _, _)| x == r) {
            continue;
        }
        let words = words_for(doc, r).filter(|w| !words_at(sentence, r).any(|_| true) && words_at(sentence, w).next().is_some());
        said.push(match words {
            Some(w) => (r, w, false),
            None => (r, r.to_string(), true),
        });
    }
    said.sort_by_key(|(_, t, _)| std::cmp::Reverse(t.len()));
    let mut marks: Vec<(usize, usize, Option<String>, bool)> = Vec::new();
    for (r, t, mono) in said {
        let free = |at: &usize| !marks.iter().any(|(s, e, _, _)| *at < *e && at + t.len() > *s);
        // a record told by its words then its id ("Added an experiment: e1 against e1 baseline (e1)") is set at the id
        // that closes the sentence, never at its own words
        let chip = sentence.ends_with(&format!("({t})")).then(|| sentence.len() - t.len() - 1).filter(|at| free(at));
        let Some(at) = chip.or_else(|| words_at(sentence, &t).find(|at| free(at))) else { continue };
        let Some(to) = target(doc, r, held) else { continue };
        marks.push((at, at + t.len(), to, mono));
    }
    marks.sort();
    let mut out = String::new();
    let mut from = 0;
    for (s, e, to, mono) in marks {
        out.push_str(&esc(&sentence[from..s]));
        // only an id is set in monospace; a record's words read in the sentence's own type
        let class = if mono { " class=\"mono\"" } else { "" };
        out.push_str(&match to {
            Some(to) => format!("<a{class} href=\"#{}\">{}</a>", esc(&to), esc(&sentence[s..e])),
            None if mono => format!("<span class=\"mono\">{}</span>", esc(&sentence[s..e])),
            None => esc(&sentence[s..e]),
        });
        from = e;
    }
    out.push_str(&esc(&sentence[from..]));
    out
}

/// What a sentence says for a record by its words, not its id: a run as the document names it (`runs.{id}.named`; "run
/// <label>", how its words open, in a document that does not hold them), an analysis rb minted an id for by its title.
fn words_for(doc: &Value, r: &str) -> Option<String> {
    let run = at(doc, "runs").and_then(|m| m.get(r)).map(|x| match text(x, "named") {
        "" if text(x, "label").is_empty() => format!("a run of {}", text(x, "experiment_id")),
        "" => format!("run {}", text(x, "label")),
        w => w.to_string(),
    });
    let analysis = || at(doc, "analyses").and_then(|m| m.get(r)).map(|x| format!("analysis {}", text(x, "title").trim())).filter(|w| w != "analysis ");
    run.filter(|w| w != "a run of ").or_else(analysis)
}

/// Each place `word` stands alone in `s`, in order: bounded by the text's ends or by a character no id holds there. An
/// id holds letters, digits, "-", "_" and "." (record.rs's ID_PATTERN), and a run of "." holds an id together only
/// between two of its other characters: "runs1.ckpt" and "a..b" are one id each, and "runs1." ends a sentence.
fn words_at<'s>(s: &'s str, word: &'s str) -> impl Iterator<Item = usize> + 's {
    let mut from = 0;
    std::iter::from_fn(move || {
        if word.is_empty() {
            return None;
        }
        while let Some(k) = s[from..].find(word) {
            let at = from + k;
            let end = at + word.len();
            from = at + s[at..].chars().next().map_or(1, char::len_utf8);
            let joined_before = joins(s[..at].chars().rev());
            let joined_after = joins(s[end..].chars());
            if !joined_before && !joined_after {
                return Some(at);
            }
        }
        None
    })
}

/// Whether the characters next to a word, read away from it, join it to more of an id: an id character, or a run of
/// "." with one after it.
fn joins(mut next: impl Iterator<Item = char>) -> bool {
    let part = |c: char| c.is_alphanumeric() || c == '-' || c == '_';
    loop {
        match next.next() {
            Some('.') => continue,
            c => return c.is_some_and(part),
        }
    }
}

/// The element a ref lands on: Some(its own (a question's view, an experiment's, a criterion's, run's, resource's or
/// analysis's card)) where the page holds it, else Some(the view that lists it), Some(None) where the page holds
/// neither; None for what the document does not hold, which is no record's id.
fn target(doc: &Value, r: &str, held: &dyn Fn(&str) -> bool) -> Option<Option<String>> {
    let in_list = |path: &str| list(doc, path).iter().any(|x| text(x, "id") == r);
    let in_map = |path: &str| at(doc, path).and_then(|m| m.get(r)).is_some_and(|v| !v.is_null());
    let (own, view) = if in_list("questions") {
        (format!("q-{r}"), "research")
    } else if in_list("experiments") {
        (format!("x-{r}"), "experiments")
    } else if in_map("findings") {
        (format!("c-{r}"), "criteria")
    } else if in_map("runs") {
        (format!("run-{r}"), "runs")
    } else if in_map("resources") {
        (format!("r-{r}"), "resources")
    } else if in_map("analyses") {
        (format!("a-{r}"), "runs")
    } else {
        return None;
    };
    if held(&own) {
        Some(Some(own))
    } else {
        Some(held(view).then(|| view.to_string()))
    }
}

// The document's parts, read as the renderer reads them: an absent optional field is drawn as nothing.

fn at<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |v, k| v.get(k)).filter(|v| !v.is_null())
}

fn text<'a>(v: &'a Value, path: &str) -> &'a str {
    at(v, path).and_then(Value::as_str).unwrap_or("")
}

fn list<'a>(v: &'a Value, path: &str) -> &'a [Value] {
    at(v, path).and_then(Value::as_list).map_or(&[], Vec::as_slice)
}

fn count(v: &Value, path: &str) -> Option<i64> {
    match at(v, path)? {
        // a float a whole number holds exactly, never one past what an i64 holds
        Value::Float(f) if f.fract() == 0.0 && f.abs() < 9_007_199_254_740_992.0 => Some(*f as i64),
        Value::Bool(_) => None,
        n => n.as_i64(),
    }
}

/// Text as HTML draws it, in an element or an attribute: `& < > " '` escaped, nothing else changed.
fn esc(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            _ => out.push(c),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const OWNER: &str = include_str!("../../../tests/fixtures/shell/int8-demo.owner.view.json");

    fn example() -> Value {
        crate::pyjson::loads(OWNER).unwrap()
    }

    fn doc(json: &str) -> Value {
        crate::pyjson::loads(json).unwrap()
    }

    /// The page's text as a reader sees it: tags dropped, entities read back, spaces folded.
    fn said(html: &str) -> String {
        // an inline element (a link, a span) joins the text around it; any other ends a run of text
        let mut out = String::new();
        let mut tag: Option<String> = None;
        for c in html.chars() {
            match (&mut tag, c) {
                (None, '<') => tag = Some(String::new()),
                (Some(t), '>') => {
                    let name = t.trim_start_matches('/').split([' ', '>']).next().unwrap_or("").to_string();
                    if !matches!(name.as_str(), "a" | "span") {
                        out.push(' ');
                    }
                    tag = None;
                }
                (Some(t), c) => t.push(c),
                (None, c) => out.push(c),
            }
        }
        let out = out.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&#x27;", "'").replace("&amp;", "&");
        out.split_whitespace().collect::<Vec<_>>().join(" ")
    }

    fn all(_: &str) -> bool {
        true
    }

    fn none(_: &str) -> bool {
        false
    }

    fn one_line(line: &str) -> Value {
        doc(&format!(
            "{{\"history\": {{\"summary\": \"1 write in the log\", \"sittings\": [{{\"first\": \"2026-10-19 09:35 UTC\", \"last\": \"2026-10-19 09:35 UTC\", \
             \"n\": 1, \"calls\": 0, \"head\": \"2026-10-19 \u{b7} 09:35 UTC \u{b7} 1 write \u{b7} no calls made\", \"lines\": [{line}]}}]}}, \
             \"findings\": {{\"c3\": {{\"criterion_id\": \"c3\"}}}}, \"experiments\": [{{\"id\": \"e1\"}}, {{\"id\": \"e12\"}}]}}"
        ))
    }

    #[test]
    fn a_runs_words_link_to_its_card_in_the_sentences_type() {
        // FIX9: a sentence names a run and an analysis rb minted an id for by their words, which link where the id would
        let d = doc(r#"{"runs": {"runaaaaaa": {"named": "run fp32 and int8 · seed 1, recorded 2026-10-14 09:20 UTC"}, "runbbbbbb": {"named": "run fp32 and int8 · seed 1, recorded 2026-10-14 09:25 UTC"}, "runcccccc": {"named": "a run of e1"}},
            "analyses": {"adddddd": {"title": " per-image accuracy "}}, "experiments": [{"id": "e1"}]}"#);
        let s = "Recorded a run on e1: run fp32 and int8 · seed 1, recorded 2026-10-14 09:25 UTC, replacing run fp32 and int8 · seed 1, recorded 2026-10-14 09:20 UTC";
        let html = linked(&d, s, &["runbbbbbb", "runaaaaaa", "e1"], &all);
        assert_eq!(
            html,
            "Recorded a run on <a class=\"mono\" href=\"#x-e1\">e1</a>: <a href=\"#run-runbbbbbb\">run fp32 and int8 · seed 1, recorded 2026-10-14 09:25 UTC</a>, replacing \
             <a href=\"#run-runaaaaaa\">run fp32 and int8 · seed 1, recorded 2026-10-14 09:20 UTC</a>"
        );
        // the experiment inside a run's words is the run's link, never its own
        assert_eq!(linked(&d, "Recorded a run of e1", &["runcccccc", "e1"], &all), "Recorded <a href=\"#run-runcccccc\">a run of e1</a>");
        assert_eq!(linked(&d, "Recorded an analysis on e1: analysis per-image accuracy", &["adddddd"], &all), "Recorded an analysis on e1: <a href=\"#a-adddddd\">analysis per-image accuracy</a>");
        // a document with no runs.{id}.named: the run's words open with "run <label>", which links
        let d = doc(r#"{"runs": {"runaaaaaa": {"label": "fp32 and int8 · seed 1"}, "runbbbbbb": {"label": "fp32 and int8 · seed 1"}}}"#);
        assert_eq!(
            linked(&d, s, &["runbbbbbb", "runaaaaaa"], &all),
            "Recorded a run on e1: <a href=\"#run-runbbbbbb\">run fp32 and int8 · seed 1</a>, recorded 2026-10-14 09:25 UTC, replacing \
             <a href=\"#run-runaaaaaa\">run fp32 and int8 · seed 1</a>, recorded 2026-10-14 09:20 UTC"
        );
        let d = doc(r#"{"runs": {"runcccccc": {"label": "", "experiment_id": "e1"}}}"#);
        assert_eq!(linked(&d, "Recorded a run of e1", &["runcccccc"], &all), "Recorded <a href=\"#run-runcccccc\">a run of e1</a>");
        // where nothing links, the words read as words, never set as an id
        assert_eq!(linked(&d, "Recorded a run of e1", &["runcccccc"], &none), "Recorded a run of e1");
        // where the sentence says the id (an earlier rb's document), the id is linked
        assert_eq!(linked(&d, "Recorded a run on e1 (runcccccc)", &["runcccccc"], &all), "Recorded a run on e1 (<a class=\"mono\" href=\"#run-runcccccc\">runcccccc</a>)");
    }

    #[test]
    fn every_sitting_and_line_of_the_example_shows() {
        let d = example();
        let page = said(&sittings(&d, &all));
        assert!(page.starts_with("47 writes in the log"), "{page}");
        for s in list(&d, "history.sittings") {
            assert!(page.contains(&said(text(s, "head"))), "{}", text(s, "head"));
            for l in list(s, "lines") {
                assert!(page.contains(&said(text(l, "sentence"))), "{}", text(l, "sentence"));
                let why = text(l, "why");
                if !why.is_empty() {
                    assert!(page.contains(&format!("why: {}", said(why))), "{why}");
                }
            }
        }
    }

    #[test]
    fn sittings_and_their_lines_read_newest_first() {
        let d = example();
        let page = said(&sittings(&d, &all));
        let at = |s: &str| page.find(s).unwrap_or_else(|| panic!("{s} not drawn"));
        // the newest sitting first, and inside it the newest line first
        assert!(at("2026-10-19 · 09:25 to 09:40 UTC") < at("2026-10-19 · 09:12 UTC"));
        assert!(at("2026-10-19 · 09:12 UTC") < at("2026-10-06 · 10:02 to 10:32 UTC"));
        assert!(at("mentioned a past call on int8") < at("proposed a call: accept c3, not made"));
        assert!(at("proposed a call: accept c3, not made") < at("amended e2 after locking it"));
        assert!(at("09:40 mentioned a past call") < at("09:35 proposed a call"));
    }

    #[test]
    fn a_sessions_reason_reads_as_rbs_note_and_the_users_as_their_why() {
        let d = one_line(
            "{\"first\": \"2026-10-19 09:35 UTC\", \"last\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"proposed a call: accept c3, not made\", \
             \"refs\": [\"c3\"], \"why\": \"\", \"note\": \"one seed is enough for the export check\", \"log_index\": 45}",
        );
        let html = sittings(&d, &all);
        let page = said(&html);
        assert!(page.contains("note: one seed is enough for the export check · not confirmed"), "{page}");
        assert!(!page.contains("why:"), "a session's reason is never drawn as a why: {page}");
        // the note's words stand in an element of their own, apart from rb's words around them
        assert!(html.contains("<span class=\"why note\"><span class=\"sr\"> \u{b7} </span>note: <span class=\"au\">one seed is enough for the export check</span> \u{b7} not confirmed"), "{html}");

        // a confirmation: the call is the user's and its note is not, said apart, never "confirmed" beside "not confirmed"
        let d = one_line(
            "{\"first\": \"2026-10-19 09:35 UTC\", \"last\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"confirmed a call: put c2 under investigation\", \
             \"refs\": [\"c2\"], \"why\": \"\", \"note\": \"compare the resize and crop with the paper's\", \"log_index\": 46}",
        );
        let html = sittings(&d, &all);
        let page = said(&html);
        assert!(page.contains("confirmed a call: put c2 under investigation · note: compare the resize and crop with the paper's"), "{page}");
        assert!(!page.contains("· not confirmed") && !page.contains("why:"), "{page}");
        assert!(html.contains("<span class=\"why note\"><span class=\"sr\"> \u{b7} </span>note: <span class=\"au\">compare the resize and crop with the paper&#x27;s</span></span>"), "{html}");

        let d = one_line(
            "{\"first\": \"2026-10-19 09:35 UTC\", \"last\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"made a call: accepted c3\", \
             \"refs\": [\"c3\"], \"why\": \"holds on all three seeds\", \"note\": \"\", \"log_index\": 45}",
        );
        let html = sittings(&d, &all);
        // rb's separator stands before the why for a reader that reads the line as one, and is drawn as the gap on screen
        assert!(html.contains("<span class=\"why\"><span class=\"sr\"> \u{b7} </span>why: <span class=\"au\">holds on all three seeds</span></span>"), "{html}");
        assert!(!said(&html).contains("note:") && !said(&html).contains("not confirmed"), "{html}");
    }

    #[test]
    fn no_names_in_a_sitting() {
        // whatever a document carries beside a line, History draws only its parts: no writer, session, channel or
        // agent fact, and no words of the user's
        let d = one_line(
            "{\"first\": \"2026-10-19 09:35 UTC\", \"last\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"made a call: accepted c3\", \
             \"refs\": [\"c3\"], \"why\": \"holds\", \"note\": \"\", \"log_index\": 45, \"actor\": \"human:ana\", \"by\": \"agent:claude-code\", \
             \"session\": \"sess-a\", \"via\": \"mcp\", \"agent\": {\"name\": \"claude-code\", \"model\": \"claude-opus-5-5\"}, \"words\": \"Accept it.\", \
             \"branch\": \"exp-b\"}",
        );
        for html in [sittings(&d, &all), recent(&d, &all)] {
            for name in ["ana", "claude", "sess-a", "mcp", "opus", "Accept it", "exp-b", "agent", "human", "session"] {
                assert!(!html.to_lowercase().contains(&name.to_lowercase()), "{name} in {html}");
            }
        }
    }

    #[test]
    fn a_line_links_its_records_only_where_a_link_lands() {
        let d = one_line(
            "{\"first\": \"2026-10-19 09:35 UTC\", \"last\": \"2026-10-19 09:35 UTC\", \"n\": 2, \"sentence\": \"filed c3 under e12 and e1\", \
             \"refs\": [\"e1\", \"c3\", \"n1\", \"e12\"], \"why\": \"\", \"log_index\": 45}",
        );
        // the card where the page holds it; else the view that lists it; else text
        let html = sittings(&d, &|id| id == "c-c3" || id == "experiments" || id == "log-45");
        assert!(html.contains("filed <a class=\"mono\" href=\"#c-c3\">c3</a> under <a class=\"mono\" href=\"#experiments\">e12</a> and <a class=\"mono\" href=\"#experiments\">e1</a>"), "{html}");
        assert!(!html.contains("in the log") && !html.contains("Raw log"), "a line carries no log count, and a page that holds no log row no Raw log: {html}");
        // an id is linked where it stands as a word, never inside another id
        let html = sittings(&d, &|id| id == "x-e1");
        assert!(html.contains("under <span class=\"mono\">e12</span> and <a class=\"mono\" href=\"#x-e1\">e1</a>"), "{html}");
        // nothing held, nothing linked: a link never points where no element is
        let html = sittings(&d, &none);
        assert!(!html.contains("href"), "{html}");
        assert!(html.contains("filed <span class=\"mono\">c3</span> under <span class=\"mono\">e12</span>"), "an id is set as one, linked or not: {html}");
    }

    #[test]
    fn an_id_with_a_dot_is_one_id() {
        // ids may hold a "." (record.rs's ID_PATTERN): runs1 is never linked inside runs1.ckpt, and a sentence's own "."
        // after an id ends the sentence
        let d = doc(
            "{\"history\": {\"sittings\": [{\"first\": \"2026-10-19 09:35 UTC\", \"head\": \"h\", \"lines\": [\
             {\"first\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"made runs1.ckpt from runs1\", \"refs\": [\"runs1\", \"runs1.ckpt\"]}, \
             {\"first\": \"2026-10-19 09:36 UTC\", \"n\": 1, \"sentence\": \"retracted runs1.\", \"refs\": [\"runs1\"]}, \
             {\"first\": \"2026-10-19 09:37 UTC\", \"n\": 1, \"sentence\": \"located ckpt.runs1 beside runs1\", \"refs\": [\"runs1\", \"runs1\"]}]}]}, \
             \"runs\": {\"runs1\": {\"run_id\": \"runs1\"}}, \"resources\": {\"runs1.ckpt\": {\"resource_id\": \"runs1.ckpt\"}}}",
        );
        let html = sittings(&d, &all);
        assert!(html.contains("made <a class=\"mono\" href=\"#r-runs1.ckpt\">runs1.ckpt</a> from <a class=\"mono\" href=\"#run-runs1\">runs1</a>"), "{html}");
        assert!(html.contains("retracted <a class=\"mono\" href=\"#run-runs1\">runs1</a>."), "{html}");
        assert!(html.contains("located ckpt.runs1 beside <a class=\"mono\" href=\"#run-runs1\">runs1</a>"), "{html}");
        assert_eq!(html.matches("href=\"#run-runs1\"").count(), 3, "a ref named twice in refs is linked once: {html}");
        let twice = one_line("{\"first\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"related c3 to c3\", \"refs\": [\"c3\", \"c3\"]}");
        assert!(sittings(&twice, &all).contains("related <a class=\"mono\" href=\"#c-c3\">c3</a> to c3"));
    }

    #[test]
    fn a_longer_id_keeps_its_place_and_dots_in_a_run_join() {
        // a run of "." joins an id ("a..b"), and a shorter ref never takes the place of a longer one that holds it,
        // whatever order refs lists them in
        let d = doc(
            "{\"history\": {\"sittings\": [{\"first\": \"2026-10-19 09:35 UTC\", \"head\": \"h\", \"lines\": [\
             {\"first\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"made a..b from a\", \"refs\": [\"a\", \"a..b\"]}, \
             {\"first\": \"2026-10-19 09:36 UTC\", \"n\": 1, \"sentence\": \"saw .x and x.\", \"refs\": [\"x\", \".x\"]}]}]}, \
             \"runs\": {\"a\": {}, \"x\": {}}, \"resources\": {\"a..b\": {}, \".x\": {}}}",
        );
        let html = sittings(&d, &all);
        assert!(html.contains("made <a class=\"mono\" href=\"#r-a..b\">a..b</a> from <a class=\"mono\" href=\"#run-a\">a</a>"), "{html}");
        assert!(html.contains("saw <a class=\"mono\" href=\"#r-.x\">.x</a> and <a class=\"mono\" href=\"#run-x\">x</a>."), "{html}");
    }

    #[test]
    fn the_frames_entry_points_link_only_what_the_frame_draws() {
        // R's frame draws every list view, each question's view and each experiment's; a card or a log row is another
        // group's, so view() and overview_sitting() never link one
        let d = example();
        let html = view(&d);
        assert!(html.contains("amended <a class=\"mono\" href=\"#x-e2\">e2</a> after locking it"), "{html}");
        assert!(html.contains("recorded an interpretation on <a class=\"mono\" href=\"#q-q1a\">q1a</a>"), "{html}");
        assert!(html.contains("proposed a call: accept <a class=\"mono\" href=\"#criteria\">c3</a>, not made"), "{html}");
        assert!(html.contains("<a class=\"mono\" href=\"#runs\">runs2</a>"), "{html}");
        assert!(!html.contains("href=\"#c-") && !html.contains("href=\"#run-") && !html.contains("href=\"#log-"), "{html}");
        assert!(!html.contains("href=\"#x-e9\""));
        assert_eq!(view(&d), sittings(&d, &|id| framed(&d, id)));
        let o = overview_sitting(&d);
        assert!(o.contains("<a class=\"mono\" href=\"#x-e2\">e2</a>") && o.contains("Recent calls and changes"), "{o}");
        assert!(!framed(&d, "q-nope") && !framed(&d, "x-nope") && !framed(&d, "c-c1") && framed(&d, "x-e1"));
    }

    #[test]
    fn a_part_of_another_type_draws_nothing_wrong() {
        // a sitting or a line that is no object draws nothing; a count of writes that is no whole number above zero is
        // left out rather than said as "1 write" or "-3 writes"
        let d = doc(
            "{\"history\": {\"sittings\": [5, {\"first\": \"2026-10-19 09:35 UTC\", \"head\": \"h\", \"lines\": [7, \
             {\"first\": \"2026-10-19 09:35 UTC\", \"n\": \"5\", \"sentence\": \"locked e1\"}, \
             {\"first\": \"2026-10-19 09:36 UTC\", \"n\": -3, \"sentence\": \"locked e2\"}]}]}}",
        );
        let html = sittings(&d, &all);
        assert_eq!(html.matches("<li class=\"sit\">").count(), 1, "{html}");
        assert_eq!(html.matches("<li class=\"hev\">").count(), 2, "{html}");
        assert!(!html.contains("in the log"), "{html}");
        assert!(said(&recent(&d, &all)).contains("locked e2"));
        // sittings that hold no object draw no list; a float count past what a whole number holds exactly is no count
        assert_eq!(sittings(&doc("{\"history\": {\"summary\": \"s\", \"sittings\": [5, [], \"x\"]}}"), &all), "");
        let big = doc("{\"history\": {\"sittings\": [{\"head\": \"h\", \"lines\": [{\"n\": 1e20, \"sentence\": \"locked e1\"}]}]}}");
        assert!(!sittings(&big, &all).contains("in the log"));
    }

    #[test]
    fn rbs_text_and_author_text_are_escaped() {
        let d = one_line(
            "{\"first\": \"2026-10-19 09:35 UTC\", \"last\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"added a note on e1\", \
             \"refs\": [\"e1\"], \"why\": \"<script>alert(1)</script> & \\\"x\\\" javascript:\", \"note\": \"<b>no</b>\", \"log_index\": 3}",
        );
        let html = sittings(&d, &all);
        assert!(!html.contains("<script") && !html.contains("<b>"), "{html}");
        assert!(html.contains("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;x&quot; javascript:"), "{html}");
    }

    #[test]
    fn a_lines_time_says_its_day_only_where_it_is_another() {
        assert_eq!(span("2026-10-19 09:35 UTC", "2026-10-19 09:35 UTC", "2026-10-19"), "09:35");
        assert_eq!(span("2026-10-19 23:58 UTC", "2026-10-20 00:03 UTC", "2026-10-19"), "23:58 to 2026-10-20 00:03");
        assert_eq!(span("2026-10-20 00:01 UTC", "2026-10-20 00:03 UTC", "2026-10-19"), "2026-10-20 00:01 to 2026-10-20 00:03");
        // a time rb cannot read stands alone, as the model's head says it
        assert_eq!(span("sometime", "sometime", ""), "sometime");
        assert_eq!(span("sometime", "2026-10-19 09:35 UTC", ""), "sometime");
        // a line with no first time says its last
        assert_eq!(span("", "2026-10-19 09:35 UTC", "2026-10-19"), "09:35");
        assert_eq!(span("  ", "2026-10-19 09:35 UTC", "2026-10-19"), "09:35");
    }

    #[test]
    fn the_latest_changes_line_falls_back_to_the_last_write() {
        let d = example();
        let line = latest(&d);
        assert_eq!(
            line,
            "<p class=\"counts latest\"><a href=\"#history\">Latest changes: 2026-10-19 \u{b7} 09:25 to 09:40 UTC \u{b7} 4 writes \u{b7} no calls made</a></p>"
        );
        // it holds the newest sitting's head (the Overview's history.sittings[0].head)
        assert!(said(&line).contains(&said(text(&list(&d, "history.sittings")[0], "head"))));
        assert_eq!(latest(&doc("{\"home\": {\"latest\": null, \"last_write\": {\"at\": \"2026-10-19 09:40 UTC\"}}}")), "<p class=\"counts latest\"><a href=\"#history\">last write 2026-10-19 09:40 UTC</a></p>");
        assert_eq!(latest(&doc("{\"home\": {}}")), "");
    }

    #[test]
    fn the_overview_shows_the_newest_sittings_newest_four_lines() {
        let d = example();
        let html = recent(&d, &all);
        let page = said(&html);
        assert!(page.starts_with("Recent calls and changes 2026-10-19 · 09:25 to 09:40 UTC · 4 writes · no calls made"), "{page}");
        assert!(page.ends_with("Every change, newest first \u{2192}"), "{page}");
        assert_eq!(html.matches("<li class=\"hev\">").count(), 4);
        assert!(!page.contains("in the log"), "the Overview's lines carry no log count: {page}");
        assert!(!page.contains("amended e1 after locking it: hypothesis"), "an older sitting stays in History: {page}");
        // a sitting of more lines shows its newest four
        let six = list(&d, "history.sittings").iter().find(|s| list(s, "lines").len() > RECENT_LINES).unwrap().clone();
        let mut d6 = d.clone();
        d6.as_dict_mut().unwrap().insert("history".into(), crate::obj! {"sittings" => Value::List(vec![six.clone()])});
        let html = recent(&d6, &all);
        assert_eq!(html.matches("<li class=\"hev\">").count(), RECENT_LINES);
        let newest = list(&six, "lines").last().unwrap();
        assert!(said(&html).contains(&said(text(newest, "sentence"))));
    }

    #[test]
    fn before_history_has_sittings_it_draws_nothing() {
        let d = doc("{\"history\": {\"log\": [{\"at\": \"2026-10-19 09:40 UTC\", \"op\": \"decide\", \"sentence\": \"decide decision d1\"}]}}");
        assert_eq!(sittings(&d, &all), "");
        assert_eq!(recent(&d, &all), "");
        assert_eq!(sittings(&doc("{}"), &all), "");
    }

    #[test]
    fn one_raw_log_link_a_day_leads_to_its_newest_row() {
        // three sittings on two days: the newest sitting of each day carries the day's one link, to the newest row of that
        // day the page holds; no line says how many writes of the log it tells
        let d = doc(
            "{\"history\": {\"summary\": \"5 changes recorded\", \"sittings\": [\
             {\"first\": \"2026-10-20 09:40 UTC\", \"head\": \"newest\", \"lines\": [{\"first\": \"2026-10-20 09:40 UTC\", \"n\": 2, \"sentence\": \"Locked e1\", \"log_index\": 3}]}, \
             {\"first\": \"2026-10-20 08:00 UTC\", \"head\": \"earlier that day\", \"lines\": [{\"first\": \"2026-10-20 08:00 UTC\", \"n\": 1, \"sentence\": \"Locked e2\", \"log_index\": 2}]}, \
             {\"first\": \"2026-10-19 23:58 UTC\", \"head\": \"the day before\", \"lines\": [{\"first\": \"2026-10-19 23:58 UTC\", \"n\": 2, \"sentence\": \"Locked e3\", \"log_index\": 0}]}], \
             \"log\": [{\"at\": \"2026-10-20 09:41 UTC\", \"log_index\": 4}, {\"at\": \"2026-10-20 09:40 UTC\", \"log_index\": 3}, {\"at\": \"2026-10-20 08:00 UTC\", \"log_index\": 2}, \
             {\"at\": \"2026-10-19 23:59 UTC\", \"log_index\": 1}, {\"at\": \"2026-10-19 23:58 UTC\", \"log_index\": 0}]}}",
        );
        let html = sittings(&d, &|id| id.starts_with("log-"));
        assert_eq!(html.matches(">Raw log</a>").count(), 2, "{html}");
        let at = |s: &str| html.find(s).unwrap_or_else(|| panic!("{s} not in {html}"));
        assert!(at("newest</p>") < at("href=\"#log-4\"") && at("href=\"#log-4\"") < at("earlier that day"), "{html}");
        assert!(at("the day before</p>") < at("href=\"#log-1\""), "{html}");
        assert!(html.contains("aria-label=\"Raw log of 2026-10-20\""), "{html}");
        assert!(!said(&html).contains("in the log") && !said(&html).contains("writes"), "{html}");
        assert!(said(&html).starts_with("5 changes recorded"), "several sittings: the summary counts them all: {html}");
        // the newest row the page holds; none held, no link
        let html = sittings(&d, &|id| id == "log-3" || id == "log-0");
        assert!(html.contains("href=\"#log-3\"") && html.contains("href=\"#log-0\"") && !html.contains("#log-4"), "{html}");
        assert!(!sittings(&d, &none).contains("Raw log"));
        // the Overview's newest sitting has no Raw log
        assert!(!recent(&d, &all).contains("Raw log"));
    }

    #[test]
    fn a_lone_sitting_is_not_counted_twice() {
        // R's line 56: where one sitting holds every change, its head counts them, and the summary is left out
        let d = one_line("{\"first\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"Locked e1\"}");
        let html = sittings(&d, &all);
        assert!(!html.contains("hsum") && !said(&html).contains("1 write in the log"), "{html}");
        assert!(sittings(&example(), &all).contains("<p class=\"dim sm hsum\">47 writes in the log</p>"));
    }

    #[test]
    fn a_time_is_set_in_the_screens_type_and_an_id_in_monospace() {
        let d = one_line("{\"first\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"Added a rule to e1: INT8 stays within a point (c3)\", \"refs\": [\"c3\", \"e1\"]}");
        let html = sittings(&d, &all);
        assert!(html.contains("<span class=\"ht\">09:35</span>"), "{html}");
        assert!(html.contains("Added a rule to <a class=\"mono\" href=\"#x-e1\">e1</a>: INT8 stays within a point (<a class=\"mono\" href=\"#c-c3\">c3</a>)"), "{html}");
        // a confirmation reads as one in either case of its first letter
        assert!(confirmation("Confirmed a call: accepted c3") && confirmation("confirmed a call: accepted c3") && !confirmation("Confirmed"));
    }

    #[test]
    fn a_records_own_id_is_set_where_it_closes_the_sentence() {
        let d = one_line("{\"first\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"Added an experiment: e1 against e1 baseline (e1)\", \"refs\": [\"e1\"]}");
        let html = sittings(&d, &all);
        assert!(html.contains("Added an experiment: e1 against e1 baseline (<a class=\"mono\" href=\"#x-e1\">e1</a>)"), "{html}");
        // another record the sentence names is still set where it first stands
        let d = one_line("{\"first\": \"2026-10-19 09:35 UTC\", \"n\": 1, \"sentence\": \"Added a rule to e1: c3 holds on e1 (c3)\", \"refs\": [\"c3\", \"e1\"]}");
        let html = sittings(&d, &all);
        assert!(html.contains("Added a rule to <a class=\"mono\" href=\"#x-e1\">e1</a>: c3 holds on e1 (<a class=\"mono\" href=\"#c-c3\">c3</a>)"), "{html}");
    }
}
