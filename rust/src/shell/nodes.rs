//! The shell's cards (SHELL-SPEC section 5), each drawn once at its anchor from the document alone: the finding, the run
//! receipt, the resource, the analysis, the call, and on a question or an experiment the qualification, reading group
//! and variant cards. The frame's cards region calls `all`; in app mode the inspector clones a card it holds. A
//! question's qualifications and reading groups live in the question's own folds, which the view draws (SHELL-SPEC
//! 2.2), so `all` leaves them out and `one` draws them whole for rb open's inspector.
//!
//! Every card keeps the waterline (SHELL-SPEC section 3.3): first the conclusion band (kind, id, role, title, rb's
//! standing words), then the evidence band (the reading, what it rests on, what rests on it, counts toward, used by),
//! never folded, every number, count, id and standing word in it a link (the drill law); then "receipts", folded in the
//! document and opened by the inspector and by print. No card names a writer, a session, a channel or a branch; a place
//! (rb open's own) is drawn on rb open only, whatever the document holds. Author text sits in an element of its own
//! (`class="au"`), verbatim.

use super::{figure, strings as w, Surface};
use crate::value::Value;
use indexmap::IndexMap;

/// Every card of the frame's cards region, in waterline order inside each and in the order a reader drills (findings,
/// runs, resources, analyses, calls, then an experiment's variants). A question's qualifications and reading groups are
/// not among them: the question's view draws them in its folds, at the same anchors.
pub fn all(doc: &Value, surface: Surface) -> String {
    let d = Doc::new(doc, surface);
    let mut out = String::new();
    for (cid, f) in entries(doc, "findings") {
        out += &d.finding(cid, f);
    }
    let short = past_the_cap(doc);
    for (rid, r) in entries(doc, "runs") {
        out += &if short.contains(&rid) { d.short_run(rid, r) } else { d.run(rid, r) };
    }
    for (res, r) in entries(doc, "resources") {
        out += &d.resource(res, r);
    }
    for (aid, a) in entries(doc, "analyses") {
        out += &d.analysis(aid, a);
    }
    // one card per subject: rb may wait on one subject for more than one reason
    let mut by_subject: Vec<(&str, Vec<&Value>)> = Vec::new();
    for c in list(doc, "calls") {
        match by_subject.iter_mut().find(|(sub, _)| *sub == s(c, "subject")) {
            Some((_, v)) => v.push(c),
            None => by_subject.push((s(c, "subject"), vec![c])),
        }
    }
    for (_, calls) in &by_subject {
        out += &d.call(calls);
    }
    for x in list(doc, "experiments") {
        out += &d.variants(x);
    }
    for (key, c) in entries(doc, "citations") {
        out += &d.source(key, c);
    }
    out
}

/// One card of the document, whole, by its anchor (`c-c1`, `run-runs1`, `r-imagenet-val`, `a-a1`, `call-c2`,
/// `q-q1:lim:0`, `q-q1:rd:criteria-met`, `x-e1:v:int8`), as `all` draws it on `surface`; a run past the cap is drawn
/// with its whole receipt, which is what rb open serves for it. None for an anchor the document has no card at.
pub fn one(doc: &Value, surface: Surface, anchor: &str) -> Option<String> {
    let d = Doc::new(doc, surface);
    let (prefix, id) = anchor.split_once('-')?;
    let found = |key: &str| at(doc, key).and_then(|m| m.get(id));
    match prefix {
        "c" => found("findings").map(|f| d.finding(id, f)),
        "run" => found("runs").map(|r| d.run(id, r)),
        "r" => found("resources").map(|r| d.resource(id, r)),
        "a" => found("analyses").map(|a| d.analysis(id, a)),
        "call" => {
            let calls: Vec<&Value> = list(doc, "calls").iter().filter(|c| s(c, "subject") == id).collect();
            (!calls.is_empty()).then(|| d.call(&calls))
        }
        "q" => {
            let qid = id.split(':').next()?;
            let q = list(doc, "questions").iter().find(|q| s(q, "id") == qid)?;
            cut(&(d.qualifications(q) + &d.reading_groups(q)), anchor)
        }
        "x" => {
            let eid = id.split(':').next()?;
            let x = list(doc, "experiments").iter().find(|x| s(x, "id") == eid)?;
            cut(&d.variants(x), anchor)
        }
        "cite" => found("citations").map(|c| d.source(id, c)),
        _ => None,
    }
}

/// One card by its kind and id: `criterion` (or `finding`), `run`, `resource`, `analysis` and `call` by the record's
/// id; `source` (or `citation`) by its citation's key (`README.md:3`); `qualification`, `reading` and `variant` by their anchor's tail (`q1:lim:0`, `q1:rd:criteria-met`, `e1:v:int8`).
pub fn card(doc: &Value, kind: &str, id: &str, surface: Surface) -> Option<String> {
    let prefix = match kind {
        "criterion" | "finding" => "c",
        "run" => "run",
        "resource" => "r",
        "analysis" => "a",
        "call" => "call",
        "qualification" | "reading" => "q",
        "variant" => "x",
        "source" | "citation" => "cite",
        _ => return None,
    };
    one(doc, surface, &format!("{prefix}-{id}"))
}

/// The card at `anchor` in `html`, from its opening tag to the end tag that closes it.
fn cut(html: &str, anchor: &str) -> Option<String> {
    let at = html.find(&format!(" id=\"{}\"", esc(anchor)))?;
    let start = html[..at].rfind('<')?;
    let tag: String = html[start + 1..].chars().take_while(|c| c.is_ascii_alphanumeric()).collect();
    let (open, close) = (format!("<{tag}"), format!("</{tag}>"));
    let (mut depth, mut i) = (0usize, start);
    loop {
        let next_close = html[i..].find(&close)? + i;
        match html[i..].find(&open).map(|k| k + i) {
            Some(o) if o < next_close => {
                depth += 1;
                i = o + open.len();
            }
            _ => {
                depth -= 1;
                i = next_close + close.len();
                if depth == 0 {
                    return Some(html[start..i].to_string());
                }
            }
        }
    }
}

/// How many run receipts a page holds in full for each experiment: its newest, by when each was recorded. The model
/// gives every run; a run past the cap keeps its anchor as a short card (SHELL-SPEC section 5.3).
pub const RECEIPT_CAP: usize = 50;

/// The runs past the cap: each experiment's runs but its newest RECEIPT_CAP, newest first by when recorded (`at`, rb's
/// UTC time, which sorts as text), then by id.
fn past_the_cap(doc: &Value) -> Vec<&str> {
    let mut by_x: Vec<(&str, Vec<(&str, &str)>)> = Vec::new();
    for (rid, r) in entries(doc, "runs") {
        let x = s(r, "experiment_id");
        match by_x.iter_mut().find(|(e, _)| *e == x) {
            Some((_, v)) => v.push((s(r, "at"), rid)),
            None => by_x.push((x, vec![(s(r, "at"), rid)])),
        }
    }
    let mut out = Vec::new();
    for (_, mut runs) in by_x {
        runs.sort_by(|a, b| b.cmp(a));
        out.extend(runs.iter().skip(RECEIPT_CAP).map(|(_, rid)| *rid));
    }
    out
}

/// Whether a part of a line names `cmd`: as a whole span, never inside a word ("eval" in "evaluation data", "sh" in
/// "sha256"); a command of one word ("python", "train") only where it is the whole part or what follows its ": ", so a
/// receipt's "python 3.11.9" is not taken for it. A full stop that ends a sentence after it ("... --seed 1.") is not
/// part of a word. pub(crate) so views.rs's said_past can call the one rule (requested of its owner).
pub(crate) fn names_command(part: &str, cmd: &str) -> bool {
    let part = part.trim();
    if !cmd.contains(char::is_whitespace) {
        let bare = if cmd.ends_with('.') { part } else { part.strip_suffix('.').unwrap_or(part) };
        return bare == cmd || bare.ends_with(&format!(": {cmd}"));
    }
    let word = |c: Option<char>| c.map_or(false, |c| c.is_alphanumeric() || "_-./".contains(c));
    part.match_indices(cmd).any(|(i, _)| {
        let after = &part[i + cmd.len()..];
        // full stops a sentence ends with ("1.", "1.)", "1...") are no part of a word; one a word goes on from ("1.5",
        // "train.py") is
        let next = after.chars().next();
        let next = if next == Some('.') { after.trim_start_matches('.').chars().next() } else { next };
        !word(part[..i].chars().next_back()) && !word(next)
    })
}

/// The document a card is drawn from, and where it is drawn.
struct Doc<'a> {
    doc: &'a Value,
    surface: Surface,
    /// On rb open at a past version, the commands the document records (a step's, a run's, a resource's way to get it
    /// again): the cards draw none of them, as a box, as code or as words (guarantee 38). None when drawn live.
    past: Option<Vec<String>>,
    /// What tells each run apart from a run whose label reads the same (run_apart); none for a run no other reads as.
    apart: IndexMap<String, String>,
}

impl<'a> Doc<'a> {
    fn new(doc: &'a Value, surface: Surface) -> Self {
        let past = (surface == Surface::Open && !s(doc, "places.place.version.sha").is_empty()).then(|| {
            fn walk(v: &Value, out: &mut Vec<String>) {
                match v {
                    Value::Dict(d) => {
                        for (k, x) in d.iter() {
                            match (k.as_str(), x) {
                                ("command", Value::Str(c)) if !c.trim().is_empty() => out.push(c.trim().to_string()),
                                ("command", x) if !s(x, "text").trim().is_empty() => out.push(s(x, "text").trim().to_string()),
                                _ => walk(x, out),
                            }
                        }
                    }
                    Value::List(l) => l.iter().for_each(|x| walk(x, out)),
                    _ => {}
                }
            }
            let mut out = Vec::new();
            walk(doc, &mut out);
            out.sort();
            out.dedup();
            out
        });
        Doc { doc, surface, past, apart: run_apart(doc) }
    }

    /// A line of rb's as this card draws it: on a past version without its parts (DOT apart) that name a command the
    /// document records; drawn live, as it is.
    fn said(&self, line: &str) -> String {
        match &self.past {
            None => line.to_string(),
            Some(cmds) => line.split(DOT).filter(|part| !cmds.iter().any(|c| names_command(part, c))).collect::<Vec<_>>().join(DOT),
        }
    }
}

// ---------------------------------------------------------------- reading the document

/// The value at `key` of `v`, if it is there and not null.
fn at<'a>(v: &'a Value, key: &str) -> Option<&'a Value> {
    key.split('.').try_fold(v, |v, k| v.get(k)).filter(|v| !v.is_null())
}

/// The text at `key`, "" where there is none: an absent optional field is drawn as nothing.
fn s<'a>(v: &'a Value, key: &str) -> &'a str {
    at(v, key).and_then(Value::as_str).unwrap_or("")
}

fn list<'a>(v: &'a Value, key: &str) -> &'a [Value] {
    at(v, key).and_then(Value::as_list).map_or(&[], Vec::as_slice)
}

fn strs<'a>(v: &'a Value, key: &str) -> Vec<&'a str> {
    list(v, key).iter().filter_map(Value::as_str).collect()
}

/// The (id, object) pairs of a map of the document, in its order.
fn entries<'a>(v: &'a Value, key: &str) -> Vec<(&'a str, &'a Value)> {
    at(v, key).and_then(Value::as_dict).map_or_else(Vec::new, |d| d.iter().map(|(k, v)| (k.as_str(), v)).collect())
}

/// A number as rb's JSON writes it, for a count ("3"), or "" where there is none.
fn num(v: &Value, key: &str) -> String {
    match at(v, key) {
        Some(Value::Int(n)) => n.to_string(),
        Some(Value::Float(f)) if f.fract() == 0.0 && f.abs() < 9_007_199_254_740_992.0 => format!("{}", *f as i64),
        Some(Value::Float(f)) => f.to_string(),
        Some(Value::Str(t)) => t.clone(),
        _ => String::new(),
    }
}

// ---------------------------------------------------------------- drawing

/// Text as HTML draws it, in an element or an attribute.
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

/// Author text (a statement, a title, a note, an interpretation, a why), verbatim, in an element of its own.
fn au(text: &str) -> String {
    format!("<span class=\"au\">{}</span>", esc(text))
}

/// A link to an anchor of the page.
fn link(anchor: &str, text: &str) -> String {
    format!("<a href=\"#{}\">{}</a>", esc(anchor), esc(text))
}

/// A link with class `cls`, none where it is "".
fn link_cls(anchor: &str, text: &str, cls: &str) -> String {
    if cls.is_empty() {
        link(anchor, text)
    } else {
        link_c(anchor, text, cls)
    }
}

fn link_c(anchor: &str, text: &str, cls: &str) -> String {
    format!("<a class=\"{cls}\" href=\"#{}\">{}</a>", esc(anchor), esc(text))
}

fn plural(n: usize, one: &str, many: &str) -> String {
    format!("{n} {}", if n == 1 { one } else { many })
}

/// A short id an anchor may hold: lower case, words joined by "-" ("criteria met" is "criteria-met").
fn slug(text: &str) -> String {
    let mut out = String::new();
    for c in text.to_lowercase().chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c);
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    out.trim_matches('-').to_string()
}

pub(crate) fn finding_anchor(cid: &str) -> String {
    format!("c-{cid}")
}
pub(crate) fn run_anchor(rid: &str) -> String {
    format!("run-{rid}")
}
pub(crate) fn resource_anchor(res: &str) -> String {
    format!("r-{res}")
}
pub(crate) fn analysis_anchor(aid: &str) -> String {
    format!("a-{aid}")
}
pub(crate) fn call_anchor(subject: &str) -> String {
    format!("call-{subject}")
}
/// A qualification of a question (its `n`th limit, from 0), a reading group (rb's rule word, plural), a variant.
pub(crate) fn qualification_anchor(qid: &str, n: usize) -> String {
    format!("q-{qid}:lim:{n}")
}
pub(crate) fn reading_anchor(qid: &str, word: &str) -> String {
    format!("q-{qid}:rd:{}", slug(word))
}
/// A citation's Source view (CITE item 3), by the key the document gives it (`README.md:3`, `logs/agent.log:581-584`):
/// the anchor views.rs's chips link to.
pub(crate) fn source_anchor(key: &str) -> String {
    format!("cite-{key}")
}

pub(crate) fn variant_anchor(eid: &str, variant: &str) -> String {
    format!("x-{eid}:v:{variant}")
}

/// `inner` as the glossary's term `key`: rb's line on hover and focus, described by its glossary entry (SHELL-SPEC 7.5);
/// a word the glossary does not hold is drawn plain.
fn term(key: &str, inner: &str) -> String {
    match w::hint(key) {
        Some(line) => format!("<span class=\"term\" data-hint=\"{}\" aria-describedby=\"{}\">{inner}</span>", esc(line), w::glossary_id(key)),
        None => inner.to_string(),
    }
}

/// The head of a finding's design cells where each says its interval and units, as rb page always said it.
const WITHIN_CELL_UNITS: &str = "Within-cell units: each cell's interval over its own units";

/// A criterion's display noun on a screen (PLAIN-WORDS-PASS-1): a card says "rule", with the glossary's line that
/// says what commands call it ("rule"). Commands, flags, kinds and anchors keep "criterion".
fn rule_term(inner: &str) -> String {
    term("rule", inner)
}

/// "what depends on this": the part of a card that lists what reads the record it is on.
const DEPENDS: &str = "What depends on this";
/// A run or an analysis named where it has no label or title, never by the id rb minted (FIX7; R's FIX6 words).
const RUN_OF: &str = "run of ";
const A_RUN: &str = "a run";
const AN_ANALYSIS: &str = "an analysis";

/// A rule card's parts, in plain headings (DESIGN-PASS-1 target 4), each the tab that leads to it.
const MEASURED: &str = "What rb measured";
const AS_WRITTEN: &str = "The rule as written";
const RESTS_ON: &str = "Runs it rests on";

/// What a call's composer says its consequence will read as.
const WILL_SAY: &str = "The record will say:";

/// What a resource's way to get it again says on a past version, where its command is not shown.
const PAST_NO_COMMAND: &str = "A past version shows no command.";

/// What a citation's Source view says on a past version, which quotes no line of a file (guarantee 38).
const PAST_NO_QUOTE: &str = "A past version quotes no line of a file, as it shows no command: the record as it is now quotes them.";

/// What a card's Decide says on a past version, which records no call and offers none to run.
const PAST_READ_ONLY: &str = "This is a past version, read only: a call is recorded on the record as it is now.";

/// rb's separator between the parts of a line.
const DOT: &str = " \u{b7} ";

/// What a card's Decide says about where a call is made, on each surface and for each viewer of the export
/// (OWNER-READER): rb open and rb page, and the owner's working view on the hub, say rb open's words; an export with no
/// viewer named says it without a role.
const HONEST_OWN: &str = "Run it in your terminal; rb records the call and your reason.";
const HONEST_EXPORT: &str = "It is run with rb in a terminal; rb records the call and the reason.";

/// The heading of what a reader of a shared page reads in place of a card's Decide part: the same reasons, as status.
const STILL_OPEN: &str = "Still open";
/// What a call card says when no call is recorded on its rule yet.
const NOTHING_RECORDED: &str = "Nothing is recorded on it yet.";

/// Where an export's finding card sends a call: its call card, where the actions are drawn once.
const ON_ITS_CARD: &str = "Its actions and their lines are on";

/// `html` drawn once for each wording the export's viewers read (OWNER-READER): with no viewer named ("none", an
/// export opened from disk), the record's owner signed in on the hub ("owner"), or a reader of a shared link
/// ("reader"). Each wording is a `tag` with `attrs`, marked with the viewers it is for (`data-vw`, which shell.css
/// shows only to them); "" draws nothing for that viewer; one wording for all three is drawn unmarked. The same marks
/// as the views' (views.rs by_viewer).
fn by_viewer(tag: &str, attrs: &str, none: &str, owner: &str, reader: &str) -> String {
    let mut groups: Vec<(&str, Vec<&str>)> = Vec::new();
    for (who, html) in [("none", none), ("owner", owner), ("reader", reader)] {
        if html.is_empty() {
            continue;
        }
        match groups.iter_mut().find(|(h, _)| *h == html) {
            Some((_, ws)) => ws.push(who),
            None => groups.push((html, vec![who])),
        }
    }
    if let [(h, ws)] = groups.as_slice() {
        if ws.len() == 3 {
            return format!("<{tag}{attrs}>{h}</{tag}>");
        }
    }
    groups.iter().map(|(h, ws)| format!("<{tag}{attrs} data-vw=\"{}\">{h}</{tag}>", ws.join(" "))).collect()
}
/// The label of how rb knows a run's command, a row of its own under the command (as given).
const HOW_RB_KNOWS: &str = "how rb knows";

/// A command to copy, whole, in its own box.
/// The mark beside a line of what it takes to run something again, as the experiment's view draws it.
fn ready_glyph(state: &str) -> &'static str {
    match state {
        "ok" => "\u{2713}",
        "partial" => "\u{25d0}",
        _ => "\u{25cb}",
    }
}

fn cmdbox(text: &str, id: &str) -> String {
    let id = if id.is_empty() { String::new() } else { format!(" id=\"{}\"", esc(id)) };
    format!("<div class=\"cmdbox\"><pre class=\"cmd\"{id}>{}</pre></div>", esc(text))
}

// The levels of a card (OVERVIEW-V2, "Levels of disclosure"): Understand (what it is, its standing in one line),
// Examine (the reading, the rule, what it rests on), Trace (the "Source details" fold: files, configs, commits,
// hashes, commands) and Decide (the composer and its command, where a call is possible). Each level is an element of
// its own with `data-level`, so the inspector, print and a link can find it; nothing is said in two of them.

/// The label of a card's Trace level, folded on every surface until someone opens it.
const SOURCE_DETAILS: &str = "Source details";

/// Understand: what the card is and its standing, the first thing it says.
fn understand(inner: &str) -> String {
    format!("<div class=\"lv\" data-level=\"understand\">{inner}</div>")
}

/// Examine: what rb read and what it rests on, never folded.
fn examine(inner: &str) -> String {
    if inner.is_empty() {
        return String::new();
    }
    format!("<div class=\"lv\" data-level=\"examine\">{inner}</div>")
}

/// Trace: the "Source details" fold, closed until someone opens it (in the document, in the inspector and in rb
/// open's live swap alike; print shows it); keyed `<id>:source` so rb open keeps it as it was across a swap. `rows`
/// are rb's receipt rows, the list keeping its `<id>:receipts` anchor; `before` is what the fold holds above them (a
/// run's command). The id is on what is inside the fold, never on the fold itself.
fn source_details(id: &str, before: &str, rows: &str) -> String {
    if before.is_empty() && rows.is_empty() {
        return String::new();
    }
    let rows = if rows.is_empty() { String::new() } else { format!("<dl class=\"rx\" id=\"{}:receipts\">{rows}</dl>", esc(id)) };
    format!(
        "<details class=\"fold lv src\" data-level=\"trace\" data-fold=\"{}:source\"><summary>{SOURCE_DETAILS}</summary>{before}{rows}</details>",
        esc(id)
    )
}

impl Doc<'_> {
    /// rb open draws what only the user's own screen shows (a place, the machine's platform); a page never does.
    fn live(&self) -> bool {
        self.surface == Surface::Open
    }

    /// Whose calls the calls view holds, as this surface says it, as markup: the record's owner reads rb open and rb
    /// page, so "Your call"; an export (rb export, what rb sync uploads) cannot know who reads it, and its readers make
    /// no calls, so "Open calls", with no "you" about a call, unless the hub names its viewer: its owner's working view
    /// says "Your call" (OWNER-READER). The address stays #your-call on every surface.
    fn calls_view(&self) -> String {
        if self.surface == Surface::Export { by_viewer("span", "", "Open calls", "Your call", "Open calls") } else { "Your call".to_string() }
    }

    /// A finding's call line as this surface draws it: on a page (rb page, rb export) without the user's own words
    /// the document hands apart (`your_words`), where the line ends with them. The line is never parsed for quotes:
    /// a page's document carries the line without them (SPRINT-OCT24.md section 1.5).
    fn call_line<'b>(&self, f: &'b Value) -> &'b str {
        let line = s(f, "rests_on.call");
        let words = s(f, "your_words");
        if self.live() || words.is_empty() {
            return line;
        }
        line.strip_suffix(words).and_then(|l| l.strip_suffix(DOT)).unwrap_or(line)
    }

    /// Which code a finding's runs ran, as this surface draws it: on a page by commits only, so a line that names a
    /// place (rb open's own "exp-b · .worktrees/b at ...") is drawn on rb open alone.
    fn other_code<'b>(&self, line: &'b str) -> &'b str {
        let by_commit = line.split_once(": ").map_or(false, |(_, rest)| {
            rest.starts_with("commit ") || rest.split("; ").all(|part| part.split_once(" at ").map_or(false, |(n, at)| n.parse::<u64>().is_ok() && at.starts_with("commit ")))
        });
        if self.live() || by_commit {
            line
        } else {
            ""
        }
    }

    /// `text` linked to question `q`'s view, drawn plain where the document holds no such question (a call may be filed
    /// under one rb's questions do not list): a card links only where the page draws an element.
    fn qlink(&self, q: &str, text: &str, cls: &str) -> String {
        if list(self.doc, "questions").iter().any(|v| s(v, "id") == q) {
            link_cls(&format!("q-{q}"), text, cls)
        } else {
            esc(text)
        }
    }

    /// Whether experiment `x`'s view draws `part` ("" the view itself; ":reproduce" where the document gives it one;
    /// ":lock" where it has rb's lock line; ":am:<n>" where it lists that amendment).
    fn xdrawn(&self, x: &str, part: &str) -> bool {
        let Some(v) = list(self.doc, "experiments").iter().find(|v| s(v, "id") == x) else { return false };
        match part {
            "" => true,
            ":reproduce" => at(v, "reproduce").map_or(false, Value::is_dict),
            ":lock" => !s(v, "who_line").is_empty(),
            p if p.starts_with(":am:") => list(v, "amendments").iter().any(|a| s(a, "id") == format!("{x}{p}")),
            _ => false,
        }
    }

    /// `text` linked to `part` of experiment `x`'s view, drawn plain where the page draws no such element.
    fn xlink(&self, x: &str, part: &str, text: &str, cls: &str) -> String {
        if self.xdrawn(x, part) {
            link_cls(&format!("x-{x}{part}"), text, cls)
        } else {
            esc(text)
        }
    }

    /// The anchor of an id the document holds, by its kind; None for an id it does not hold.
    fn anchor_of(&self, id: &str) -> Option<String> {
        let d = self.doc;
        if at(d, "findings").and_then(|m| m.get(id)).is_some() {
            Some(finding_anchor(id))
        } else if at(d, "runs").and_then(|m| m.get(id)).is_some() {
            Some(run_anchor(id))
        } else if at(d, "resources").and_then(|m| m.get(id)).is_some() {
            Some(resource_anchor(id))
        } else if at(d, "analyses").and_then(|m| m.get(id)).is_some() {
            Some(analysis_anchor(id))
        } else if list(d, "experiments").iter().any(|x| s(x, "id") == id) {
            Some(format!("x-{id}"))
        } else if list(d, "questions").iter().any(|q| s(q, "id") == id) {
            Some(format!("q-{id}"))
        } else {
            None
        }
    }

    /// An id, linked to its card or view where the document holds it, by what the page calls it (`named`): a name in
    /// words is not set as an id.
    fn id_link(&self, id: &str, cls: &str) -> String {
        let name = self.named(id);
        let cls = if name == id { cls } else { "" };
        match self.anchor_of(id) {
            Some(a) if cls.is_empty() => link(&a, &name),
            Some(a) => link_c(&a, &name, cls),
            None => esc(&name),
        }
    }

    /// A run as the page names it (FIX7: never by the id rb minted): its label in words, as its card's title says it,
    /// else the experiment it is a run of.
    /// Two runs whose labels read the same are told apart as the model's runs.{id}.named tells them (FIX9).
    fn run_named(&self, rid: &str) -> String {
        let run = at(self.doc, "runs").and_then(|m| m.get(rid)).filter(|r| r.is_dict());
        let base = match run.map_or("", |r| s(r, "label")) {
            "" => match run.map_or("", |r| s(r, "experiment_id")) {
                "" => A_RUN.to_string(),
                x => format!("{RUN_OF}{x}"),
            },
            l => run_title(l),
        };
        format!("{base}{}", self.apart.get(rid).map_or("", String::as_str))
    }

    /// An analysis as the page names it: its title, never the id rb minted.
    fn analysis_named(&self, aid: &str) -> String {
        match at(self.doc, "analyses").and_then(|m| m.get(aid)).map_or("", |a| s(a, "title")) {
            "" => AN_ANALYSIS.to_string(),
            t => t.to_string(),
        }
    }

    /// What the page calls an object it links: a run by its label, an analysis by its title, anything else (an
    /// experiment, a rule, a question, a resource: ids someone chose) by its id.
    fn named(&self, id: &str) -> String {
        if at(self.doc, "runs").and_then(|m| m.get(id)).is_some_and(Value::is_dict) {
            self.run_named(id)
        } else if at(self.doc, "analyses").and_then(|m| m.get(id)).is_some_and(Value::is_dict) {
            self.analysis_named(id)
        } else {
            id.to_string()
        }
    }

    /// A link to run `rid`'s card, by its name.
    fn run_link(&self, rid: &str) -> String {
        link(&run_anchor(rid), &self.run_named(rid))
    }

    /// A link to analysis `aid`'s card, by its title.
    fn analysis_link(&self, aid: &str) -> String {
        link(&analysis_anchor(aid), &self.analysis_named(aid))
    }

    /// An analysis in a sentence (FIX8): "the analysis <its title>", or "an analysis" where it has none, never "the
    /// analysis an analysis".
    fn the_analysis(&self, aid: &str) -> String {
        if self.analysis_named(aid) == AN_ANALYSIS {
            link(&analysis_anchor(aid), AN_ANALYSIS)
        } else {
            format!("the analysis {}", self.analysis_link(aid))
        }
    }

    /// Runs linked in a list, each by its name (FIX8): where a name holds a comma ("base and wide, seed 1") the list
    /// is said with semicolons, so it reads one way only.
    fn run_links(&self, rids: &[&str]) -> String {
        listed(&rids.iter().map(|r| self.run_named(r)).collect::<Vec<_>>(), rids.iter().map(|r| self.run_link(r)).collect())
    }

    /// rb's line with each id it names (`refs`) linked where the line says it, whole; an id the line does not say is
    /// linked after it, in a list that reads one way however its names are said (FIX8). An analysis with no title the
    /// line does not say adds no "an analysis" after it: where the line's own words link nothing, they link to it.
    fn with_refs(&self, text: &str, refs: &[&str]) -> String {
        let said = self.said(text);
        let text = said.as_str();
        let mut spans: Vec<(usize, usize, String)> = Vec::new();
        let mut after: Vec<String> = Vec::new();
        let mut names: Vec<String> = Vec::new();
        let mut seen: Vec<&str> = Vec::new();
        let mut untitled: Vec<String> = Vec::new();
        for r in refs.iter().filter(|r| !r.is_empty()) {
            // each id once: a line that names one twice (a run that made what it recorded) links it once
            if seen.contains(r) {
                continue;
            }
            seen.push(r);
            let Some(anchor) = self.anchor_of(r) else { continue };
            match whole_word(text, r) {
                Some(i) if !spans.iter().any(|(a, b, _)| i < *b && i + r.len() > *a) => spans.push((i, i + r.len(), anchor)),
                Some(_) => {}
                None if self.named(r) == AN_ANALYSIS && at(self.doc, "analyses").and_then(|m| m.get(r)).is_some_and(Value::is_dict) => untitled.push(anchor),
                None => {
                    names.push(self.named(r));
                    after.push(link(&anchor, &self.named(r)));
                }
            }
        }
        // the line's own words link to the one untitled analysis where they link nothing else; else it is named after
        let whole = match untitled.as_slice() {
            [anchor] if spans.is_empty() && !text.is_empty() => Some(anchor.clone()),
            _ => {
                names.extend(untitled.iter().map(|_| AN_ANALYSIS.to_string()));
                after.extend(untitled.iter().map(|a| link(a, AN_ANALYSIS)));
                None
            }
        };
        if let Some(anchor) = &whole {
            let lead = link(anchor, text);
            return if after.is_empty() { lead } else { format!("{lead}<span>{DOT}</span>{}", after_list(&names, after)) };
        }
        spans.sort();
        // a line with links keeps every plain piece in an element of its own, so it reads in order to anything that
        // reads elements
        let plain = |t: &str| if t.is_empty() { String::new() } else if spans.is_empty() && after.is_empty() { esc(t) } else { format!("<span>{}</span>", esc(t)) };
        let mut out = String::new();
        let mut from = 0;
        for (a, b, anchor) in &spans {
            out += &plain(&text[from..*a]);
            out += &link(anchor, &text[*a..*b]);
            from = *b;
        }
        out += &plain(&text[from..]);
        if !after.is_empty() {
            out += &format!("<span>{DOT}</span>{}", after_list(&names, after));
        }
        out
    }

    fn finding_of(&self, cid: &str) -> Option<&Value> {
        at(self.doc, "findings").and_then(|m| m.get(cid))
    }

    /// The question a criterion is filed under (the first that lists it), for its lineage and its home.
    fn question_of(&self, cid: &str) -> Option<&str> {
        list(self.doc, "questions").iter().find(|q| strs(q, "findings").contains(&cid)).map(|q| s(q, "id"))
    }

    /// A standing as rb gives it, each word in its colour, linked to `href`; a met word rb does not call good stays
    /// out of green.
    fn standing(&self, standing: &str, good: bool, href: &str) -> String {
        standing
            .split(DOT)
            .filter(|p| !p.is_empty())
            .map(|p| {
                let mut cls = crate::surface::standing_class(p);
                if cls == "good" && !good {
                    cls = "dim".into();
                }
                let cls = if cls == "dim" { "sw".to_string() } else { format!("sw {cls}") };
                // in the screens' words; the hover only on a card's own standing line, not on every list item
                let shown = if href.is_empty() { esc(&w::plain_word(p)) } else { said_plain(p) };
                if href.is_empty() {
                    format!("<span class=\"{cls}\">{shown}</span>")
                } else {
                    format!("<a class=\"{cls}\" href=\"#{}\">{shown}</a>", esc(href))
                }
            })
            .collect::<Vec<_>>()
            .join(DOT)
    }

    /// A criterion as one row (P3, PLAIN-WORDS-PASS-3): its sentence as the link, its state in the screens' words after
    /// it, the id last, small.
    fn crit_item(&self, cid: &str) -> String {
        match self.finding_of(cid) {
            Some(f) => {
                let statement = s(f, "statement");
                // one link: the sentence, its id trailing small inside it; then its state
                format!(
                    "<a class=\"ci\" href=\"#{}\">{} <span class=\"mono id\">{}</span></a>{DOT}{}",
                    esc(&finding_anchor(cid)),
                    if statement.is_empty() { esc(cid) } else { au(statement) },
                    esc(cid),
                    self.standing(s(f, "standing").split(DOT).next().unwrap_or(""), at(f, "good").and_then(Value::as_bool).unwrap_or(false), ""),
                )
            }
            None => format!("<span class=\"mono id\">{}</span>", esc(cid)),
        }
    }

    // ------------------------------------------------------------ the finding card: c-<cid>

    pub fn finding(&self, cid: &str, f: &Value) -> String {
        let id = finding_anchor(cid);
        let x = s(f, "experiment");
        let good = at(f, "good").and_then(Value::as_bool).unwrap_or(false);
        let standing = s(f, "standing");
        let bad = matches!(crate::surface::standing_class(standing.split(DOT).next().unwrap_or("")).as_str(), "bad");
        let rule_cls = if good { " is-good" } else if bad { " is-bad" } else { "" };
        let q = self.question_of(cid);
        let home = q.map_or_else(|| "criteria".to_string(), |q| format!("q-{q}"));
        let mut lineage = vec![link("research", "Research")];
        if let Some(q) = q {
            lineage.push(self.qlink(q, q, ""));
        }
        if !x.is_empty() {
            lineage.push(self.xlink(x, "", x, ""));
        }
        lineage.push(esc(cid));
        let cited = if at(f, "cited").is_some() { "<span class=\"tag\">cited</span>" } else { "" };
        // an id is the document's: escaped wherever it is written
        let ide = esc(&id);
        // a part is linked only where the card draws it: a finding with no receipt rows has no source details, and one
        // no call waits on has nothing to decide
        let rx = self.finding_receipts(f);
        let to_receipts = if rx.is_empty() { String::new() } else { format!("<a href=\"#{ide}:receipts\">{SOURCE_DETAILS}</a>") };
        let calls: Vec<&Value> = list(self.doc, "calls").iter().filter(|c| s(c, "subject") == cid).collect();
        // a reader of a shared page is shown no Decide part: its tab goes to the same reasons, as status (OWNER-READER)
        let to_decide = match (calls.is_empty(), self.surface == Surface::Export) {
            (true, _) => String::new(),
            (false, false) => format!("<a href=\"#{ide}:decide\">Decide</a>"),
            (false, true) => format!("<a href=\"#{ide}:decide\"><span data-vw=\"none owner\">Decide</span><span data-vw=\"reader\">{STILL_OPEN}</span></a>"),
        };
        // the rule's sentence first, its kind, role and experiment in one quiet line, the id last (P3); then its verdict,
        // said once (FIX2: no state words after it; checks are told in The rule as written); the parts as a row of tabs
        let top = format!(
            "<p class=\"lineage\">{}</p>\
             <h3 class=\"tt\">{}</h3>\
             <div class=\"hd\"><span class=\"kind\">{}</span><span class=\"dim sm\">{}{}</span>{cited}<span class=\"id\">{}</span></div>\
             <div class=\"stand\"><p class=\"str\">{}</p></div>\
             <nav class=\"secnav tabs\" aria-label=\"Parts of {}\"><a href=\"#{ide}:reading\">{MEASURED}</a><a href=\"#{ide}:rule\">{AS_WRITTEN}</a>\
             <a href=\"#{ide}:runs\">{RESTS_ON}</a><a href=\"#{ide}:impact\">{DEPENDS}</a>{to_receipts}{to_decide}</nav>",
            lineage.join(" \u{203a} "),
            self.au_cited(s(f, "statement")),
            rule_term("rule"),
            esc(s(f, "role")),
            if x.is_empty() { String::new() } else { format!(" in {}", self.xlink(x, "", x, "")) },
            esc(cid),
            sentence_html_to(&verdict(f), &format!("{id}:runs"), &format!("{id}:rule")),
            esc(cid),
        );
        let body = self.finding_reading(&id, f) + &self.finding_rule(cid, &id, f) + &self.finding_rests_on(&id, f) + &self.finding_impact(&id, f);
        format!(
            "<div class=\"card crit{rule_cls}\" id=\"{}\" data-kind=\"criterion\" data-home=\"{}\" data-crumb=\"{}\">{}{}{}{}</div>",
            esc(&id),
            esc(&home),
            esc(cid),
            understand(&top),
            examine(&body),
            source_details(&id, "", &rx),
            if calls.is_empty() { String::new() } else { self.decide(&calls, cid, &id) },
        )
    }

    /// What rb measured: the value large in two decimals (rb's digits on hover), the reading as one plain sentence, the
    /// figure, and on rb open where its runs were recorded; rb's comparison line in Details.
    fn finding_reading(&self, id: &str, f: &Value) -> String {
        let r = at(f, "reading");
        let est = r.map_or("", |r| s(r, "estimate_text"));
        let big = if est.is_empty() {
            // a reading across fixed design cells has no one estimate: rb's own range of them
            format!("<span class=\"e\">{}</span>", esc(s(f, "row.value")))
        } else {
            big_reading(f, true)
        };
        let mut out = format!(
            "<section class=\"part\" id=\"{id}:reading\"><h4>{MEASURED}</h4><div class=\"big\"><a href=\"#{id}:runs\">{big}</a></div>",
            id = esc(id)
        );
        // the reading's sentence is the verdict's, said once above (FIX3): here what the number is from, once
        let from = s(f, "units_line");
        if !from.is_empty() {
            out += &format!("<p class=\"reads\">{}</p>", link(&format!("{id}:runs"), from));
        }
        out += &figure::draw(f, true);
        if self.live() {
            out += &self.observed(id, f);
        }
        let other = self.other_code(s(f, "other_code"));
        if !other.is_empty() {
            out += &format!("<p class=\"sm\">{}</p>", esc(other));
        }
        out + "</section>"
    }

    /// Where the runs a finding counts were recorded (rb open only): each place it names linked to that place.
    fn observed(&self, id: &str, f: &Value) -> String {
        let line = s(f, "observed_short");
        if line.is_empty() {
            return String::new();
        }
        let mut out = esc(line);
        for p in list(f, "observed_places") {
            let (place, label) = (s(p, "id"), s(p, "label"));
            if place.is_empty() || label.is_empty() {
                continue;
            }
            let href = format!("#{id}&place={place}");
            out = out.replacen(&esc(label), &format!("<a href=\"{}\" data-place=\"{}\">{}</a>", esc(&href), esc(place), esc(label)), 1);
        }
        format!("<p class=\"sm obs-in\">{out}</p>")
    }

    /// The rule as written: what it passes on in a plain sentence, the margin, when it was written against its results
    /// (and the unit amended after the lock), the units, the review, rb's notes; rb's own rule and timing lines in
    /// Details (P8). Only a link's own words are in link colour (P7).
    fn finding_rule(&self, cid: &str, id: &str, f: &Value) -> String {
        let mut dl = String::new();
        // what the rule passes on, in words: the declared statement is the card's title; this is how rb reads it
        let passes = rule_passes(f);
        if !passes.is_empty() {
            dl += &format!("<dt>passes when</dt><dd>{}</dd>", esc(&passes));
        }
        // the word once: rb's row says "margin 0.8433", and the label beside it says margin already
        let margin = s(f, "row.margin");
        let margin = margin.strip_prefix("margin ").unwrap_or(margin);
        if !margin.is_empty() {
            // how far the reading is from the rule's number: M's margin comes in the verdict's digits and unit ("-1.40
            // points") and is drawn as M words it (FIX5); a bare number is said in the reading's digits and unit
            let said = match parsed(margin) {
                Some(v) => {
                    let shown = fixed(v, reading_digits(f), false);
                    with_unit(&shown, &unit_word(&unit_of(f), compared(f), parsed(&shown).unwrap_or(v).abs()))
                }
                None => margin.to_string(),
            };
            dl += &format!("<dt>margin</dt><dd>{}</dd>", link(&format!("{id}:reading"), &said));
        }
        // when it was written, in plain words; rb's line (which runs, how many) in Details
        let timing_raw = s(f, "says.timing");
        let mut timing = esc(timing_said(timing_raw));
        if let Some(am) = at(f, "unit_amended") {
            let x = s(f, "experiment");
            // the amendment that changed the unit, else the lock it was made after, else the experiment
            let am_part = s(am, "amendment").strip_prefix(x).unwrap_or("");
            let part = [am_part, ":lock"].into_iter().find(|p| p.starts_with(':') && self.xdrawn(x, p)).unwrap_or("");
            timing += &format!("<span class=\"s\">{}</span>", self.xlink(x, part, s(am, "line"), ""));
            let why = s(am, "why");
            if !why.is_empty() {
                timing += &format!("<span class=\"s\">why: {}</span>", self.au_cited(why));
            }
        }
        if !timing.is_empty() {
            dl += &format!("<dt>{}</dt><dd>{timing}</dd>", term("timing", "timing"));
        }
        let units = s(f, "says.units");
        let units_line = s(f, "units_line");
        if !units.is_empty() || !units_line.is_empty() {
            let shown = if units.is_empty() { units_line } else { units };
            // the count is the link; what rb says of it beside stays text
            let (count, rest) = shown.split_once(DOT).unwrap_or((shown, ""));
            let rest = if rest.is_empty() { String::new() } else { format!("{DOT}{}", esc(rest)) };
            dl += &format!("<dt>{}</dt><dd>{}{rest}</dd>", term("units", "units"), link(&format!("{id}:runs"), count));
        }
        // what rb checked under the reading, told here and not beside the verdict (FIX2): a problem in full, ok quietly
        let integrity = s(f, "says.integrity");
        if !integrity.is_empty() {
            let cls = if integrity == "ok" { " class=\"dim\"" } else { "" };
            dl += &format!("<dt>{}</dt><dd{cls}>{}</dd>", term("integrity", "checks"), esc(integrity));
        }
        let history = s(f, "says.history");
        if !history.is_empty() {
            dl += &format!("<dt>history</dt><dd>{}</dd>", esc(history));
        }
        let call = self.call_line(f);
        let mut review = if call.is_empty() { "No call made on it.".to_string() } else { esc(call) };
        // a call waiting on it is the card's own Decide, below
        if list(self.doc, "calls").iter().any(|c| s(c, "subject") == cid) {
            // a reader of a shared page is shown no Decide part: the link goes to the same reasons, said as status
            review += &format!("{DOT}<a href=\"#{}:decide\">{}</a>", esc(&id), self.calls_view());
        }
        dl += &format!("<dt>review</dt><dd>{review}</dd>");
        let mut notes = String::new();
        for c in strs(f, "says.caveats") {
            let (code, text) = c.split_once(": ").unwrap_or(("", c));
            notes += &format!("<li><b>{}</b> {}</li>", esc(&code.replace('_', " ")), esc(text));
        }
        let notes = if notes.is_empty() { notes } else { format!("<ul class=\"notes\">{notes}</ul>") };
        format!("<section class=\"part\" id=\"{}:rule\"><h4>{AS_WRITTEN}</h4><dl class=\"ax\">{dl}</dl>{notes}</section>", esc(id))
    }

    /// What the reading rests on: an analysis, the design cells, or each run's observation, counted or not and why.
    fn finding_rests_on(&self, id: &str, f: &Value) -> String {
        let units = s(f, "units_line");
        let n = if units.is_empty() { String::new() } else { format!(" <span class=\"n\">{}</span>", link(&format!("{id}:runs"), units)) };
        let mut body = String::new();
        let source = at(f, "source");
        let cells = list(f, "design_cells");
        let obs = list(f, "observations");
        if source.map_or("", |v| s(v, "kind")) == "analysis" {
            let aid = source.map_or("", |v| s(v, "id"));
            let a = at(self.doc, "analyses").and_then(|m| m.get(aid));
            body += &format!(
                "<p class=\"sm\">the reading comes from {}</p>",
                self.the_analysis(aid)
            );
            if let Some(a) = a {
                let reads = strs(a, "reads");
                if !reads.is_empty() {
                    body += &format!("<dl class=\"ax\"><dt>reads</dt><dd>{}</dd></dl>", self.run_links(&reads));
                }
            }
        } else if !cells.is_empty() {
            let mut li = String::new();
            // the cells' values in digits said alike (P5)
            let cell_d = decimals(&cells.iter().filter_map(|c| parsed(s(c, "estimate_text"))).collect::<Vec<_>>());
            for c in cells {
                let out = at(c, "outside").and_then(Value::as_bool).unwrap_or(false);
                let runs: Vec<&str> = strs(c, "runs");
                let first = runs.first().map(|r| run_anchor(r)).unwrap_or_else(|| format!("{id}:runs"));
                // its interval and its own units, where the document gives them
                let iv = s(c, "interval_text");
                let iv = if iv.is_empty() { String::new() } else { format!("<span class=\"iv\">{}</span>", esc(iv)) };
                let n = at(c, "n").and_then(Value::as_i64).map(|n| format!("<span class=\"u\">{n} {}</span>", esc(s(c, "unit")))).unwrap_or_default();
                li += &format!(
                    "<li{}><span class=\"k\">{}</span><span class=\"v\">{}</span>{iv}{n}{}<span class=\"rs\">{}</span></li>",
                    if out { " class=\"out\"" } else { "" },
                    esc(&cell_said(s(c, "key"))),
                    format!("<a href=\"#{}\">{}</a>", esc(&first), nb(s(c, "estimate_text"), Some(cell_d), compared(f))),
                    if out { "<span class=\"o\">outside the rule</span>" } else { "" },
                    self.run_links(&runs)
                );
            }
            // within each cell, its own units: the rule reads every cell apart
            let units = if cells.iter().any(|c| at(c, "n").is_some()) { format!("<p class=\"dim sm\">{}</p>", WITHIN_CELL_UNITS) } else { String::new() };
            body += &format!("{units}<ul class=\"cells\">{li}</ul>");
        } else if !obs.is_empty() {
            // each run by the per-run values that tell it apart, in the UI font (P2, P4); a reason shared by more than
            // one run it leaves out is said once above the table, and those rows carry only "not counted" (P6)
            let per = |o: &Value| -> Vec<(String, String)> {
                at(o, "per_run").and_then(Value::as_dict).map_or_else(Vec::new, |d| d.iter().map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_string())).collect())
            };
            let all: Vec<Vec<(String, String)>> = obs.iter().map(per).collect();
            let varies = |k: &str| {
                let vals: Vec<Option<&String>> = all.iter().map(|p| p.iter().find(|(kk, _)| kk == k).map(|(_, v)| v)).collect();
                vals.iter().any(|v| *v != vals[0])
            };
            let mut reasons: Vec<(&str, usize)> = Vec::new();
            for o in obs.iter().filter(|o| at(o, "counted").and_then(Value::as_bool) != Some(true)) {
                match reasons.iter_mut().find(|(r, _)| *r == s(o, "reason")) {
                    Some((_, n)) => *n += 1,
                    None => reasons.push((s(o, "reason"), 1)),
                }
            }
            let shared = |r: &str| reasons.iter().any(|(x, n)| *x == r && *n > 1);
            let values: Vec<f64> = obs.iter().filter_map(|o| parsed(s(o, "value_text"))).collect();
            let one_arm = obs.iter().all(|o| s(o, "arm") == obs.first().map_or("", |o| s(o, "arm")));
            let d = decimals(&values);
            // a run that names no arm gives the difference itself where the rule compares two arms, and the level of
            // the arm the rule reads where it reads one (FIX4: never "the difference" for a level)
            let level_of = match arms(f).0.as_str() {
                "" => String::new(),
                l => said_arm(l),
            };
            let no_arm = if compared(f) { "difference".to_string() } else { level_of.clone() };
            let mut rows = String::new();
            // the runs it counts first, then those it leaves out
            let mut order: Vec<(&Value, &Vec<(String, String)>)> = obs.iter().zip(&all).collect();
            order.sort_by_key(|(o, _)| at(o, "counted").and_then(Value::as_bool) != Some(true));
            // a run by what it was run at (its arm is the first column), else by its name; never by its id (FIX7)
            let at_said = |o: &Value, p: &Vec<(String, String)>| {
                let said: Vec<String> = p.iter().filter(|(k, _)| varies(k)).map(|(k, v)| setting_said(k, v)).collect();
                if said.is_empty() { s(o, "unit").replace('=', " ").replace('_', " ") } else { said.join(", ") }
            };
            let named: Vec<(&str, &str, String)> = order.iter().map(|(o, p)| (s(o, "arm"), s(o, "run"), at_said(o, p))).collect();
            for (o, p) in order {
                let run = s(o, "run");
                let counted = at(o, "counted").and_then(Value::as_bool).unwrap_or(false);
                let said = at_said(o, p);
                // two runs of an arm that would read alike are each said by its whole name
                let alike = named.iter().any(|(a, r, w)| *a == s(o, "arm") && *r != run && *w == said);
                let said = if said.is_empty() || alike { self.run_named(run) } else { said };
                let why = match (counted, s(o, "reason")) {
                    (true, _) => String::new(),
                    (false, r) if shared(r) || r.is_empty() => "<span class=\"why\">not counted</span>".to_string(),
                    (false, r) => format!("<span class=\"why\">not counted: {}</span>", esc(r)),
                };
                rows += &format!(
                    "<tr{}><td>{}</td><td class=\"pr\"><a href=\"#{}\">{}</a>{why}</td><td class=\"val\"><a href=\"#{}\">{}</a></td></tr>",
                    if counted { "" } else { " class=\"nc\"" },
                    // a run that gives the difference by itself names no arm
                    esc(&if one_arm { String::new() } else if s(o, "arm").is_empty() { no_arm.clone() } else { arm_name(s(o, "arm")) }),
                    esc(&run_anchor(run)),
                    esc(&said),
                    esc(&run_anchor(run)),
                    value_with_unit(&nb(s(o, "value_text"), Some(d), false), s(o, "value_text"), &unit_of(f), compared(f) && s(o, "arm").is_empty()),
                );
            }
            let common: Vec<String> = all.first().map_or_else(Vec::new, |p| p.iter().filter(|(k, _)| !varies(k)).map(|(k, v)| setting_said(k, v)).collect());
            if !common.is_empty() {
                body += &format!("<p class=\"dim sm\">Every run here: {}.</p>", esc(&common.join(", ")));
            }
            for (r, n) in reasons.iter().filter(|(r, n)| *n > 1 && !r.is_empty()) {
                body += &format!("<p class=\"dim sm\">{n} runs are not counted: {}.</p>", esc(r));
            }
            // an arm every run shares is said once above the table, and no row says it (P6)
            if one_arm {
                let line = match obs.first().map_or("", |o| s(o, "arm")) {
                    "" if compared(f) => "Each run gives the difference by itself.".to_string(),
                    "" if level_of.is_empty() => String::new(),
                    "" => format!("Every run here is {level_of}."),
                    arm => format!("Every run here is {}.", arm_name(arm)),
                };
                if !line.is_empty() {
                    body += &format!("<p class=\"dim sm\">{}</p>", esc(&line));
                }
            }
            // each value carries its unit, as the verdict says it (FIX5)
            body += &format!("<div class=\"wrap\"><table class=\"obs obsr\"><thead><tr><th>arm</th><th>run</th><th>value</th></tr></thead><tbody>{rows}</tbody></table></div>");
        } else {
            body += "<p class=\"dim sm\">No recorded run is read yet.</p>";
        }
        format!("<section class=\"part\" id=\"{}:runs\"><h4>{RESTS_ON}{n}</h4>{body}</section>", esc(id))
    }

    /// What rests on this finding: the interpretations citing it, the questions it is linked to without one, the
    /// relations both ways, the call.
    fn finding_impact(&self, id: &str, f: &Value) -> String {
        let mut li = String::new();
        let mut said: Vec<&str> = Vec::new();
        for ip in list(f, "rests_on.interpretations") {
            let q = s(ip, "question");
            said.push(q);
            let active = at(ip, "active").and_then(Value::as_bool).unwrap_or(false);
            // words not said to be the user's read as a session's, as the views read them
            let confirmed = at(ip, "confirmed").and_then(Value::as_bool).unwrap_or(false);
            // a recorded interpretation is a note (PLAIN-WORDS-PASS-3), with its standing in the line, never a tag of its
            // own: "A note, not confirmed: <its words>" until the user confirms it, then "Note: <its words>"; one
            // whose rule is not met is a note for when it is. The glossary's line stays on it. The question it is on
            // trails (P3).
            // a note that is not in force on a rule that is met (a gate, or a problem with what it rests on) is not one
            // "for when this rule is met": it says it is not in force
            let met = matches!(s(f, "state"), "meets" | "reproduced" | "gate met");
            let lead = match (active, confirmed, met) {
                (true, true, _) => "Note:",
                (true, false, _) => "A note, not confirmed:",
                (false, true, false) => "A note for when this rule is met:",
                (false, false, false) => "A note for when this rule is met, not confirmed:",
                (false, true, true) => "A note, not in force:",
                (false, false, true) => "A note, not in force, not confirmed:",
            };
            let key = if active { w::RECORDED_INTERPRETATION } else { w::CONDITIONAL_INTERPRETATION };
            li += &format!(
                "<li><div class=\"said\">{} {}</div><div class=\"by\">on question {}</div></li>",
                term(key, lead),
                self.au_cited(s(ip, "text")),
                self.qlink(q, q, "")
            );
        }
        for q in strs(f, "rests_on.question_links") {
            if said.contains(&q) {
                continue;
            }
            let text = list(self.doc, "questions").iter().find(|x| s(x, "id") == q).map_or("", |x| s(x, "text"));
            li += &format!("<li><div class=\"by\">question link{DOT}{}</div><div>{}</div></li>", self.qlink(q, q, ""), self.au_cited(text));
        }
        for (key, reverse) in [("rests_on.relations_out", false), ("rests_on.relations_in", true)] {
            for rel in list(f, key) {
                li += &self.relation(rel, reverse);
            }
        }
        // the call made on it is said once, as the declared criterion's review; one waiting on it is the card's Decide
        if li.is_empty() {
            li = "<li class=\"dim\">Nothing links to it yet.</li>".into();
        }
        format!("<section class=\"part\" id=\"{}:impact\"><h4>{DEPENDS}</h4><ul class=\"rests\">{li}</ul></section>", esc(id))
    }

    /// A declared relation between two criteria, either way: its kind and the other criterion.
    fn relation(&self, rel: &Value, reverse: bool) -> String {
        match rel {
            Value::Str(other) => format!("<li><div class=\"by\">related</div>{}</li>", self.crit_item(other)),
            _ => {
                let other = [s(rel, "criterion"), s(rel, "target"), s(rel, "other"), s(rel, "source")].into_iter().find(|t| !t.is_empty()).unwrap_or("");
                let kind = s(rel, "kind");
                let by = if reverse && !other.is_empty() { format!("{} {} this", esc(other), esc(kind)) } else { esc(kind) };
                format!("<li><div class=\"by\">{by}</div>{}</li>", self.crit_item(other))
            }
        }
    }

    /// The finding's receipts: rb's rows (declared, locked, amended, experiment, metric, method, source, question link),
    /// each id they name linked, then the cited number's source with its quote as the file reads it.
    fn finding_receipts(&self, f: &Value) -> String {
        // rb's own lines for the rule and its reading first, as rb gives them (P8): the card says each in plain words
        let says_rule = s(f, "says.rule");
        let detail = s(f, "rule_detail");
        let mut rows = raw_rows(&[
            ("rule", format!("<span class=\"mono\">{}</span>", esc(s(f, "rule")))),
            ("rb reads", esc(without_state(says_rule))),
            ("why", if detail.is_empty() || says_rule.contains(detail) { String::new() } else { esc(detail) }),
            ("timing", esc(s(f, "says.timing"))),
            ("comparison", esc(s(f, "says.comparison"))),
        ]);
        rows += &self.rows(list(f, "receipt_rows"), "");
        if let Some(c) = at(f, "cited") {
            let mut where_ = s(c, "path").to_string();
            let line = num(c, "line");
            if !line.is_empty() {
                where_ += &format!(":{line}");
            }
            let locator = s(c, "locator");
            if !locator.is_empty() {
                where_ += &format!("{DOT}{locator}");
            }
            let mut read = Vec::new();
            for (k, label) in [("commit", "read at commit "), ("sha256", "sha256 ")] {
                let v = s(c, k);
                if !v.is_empty() {
                    read.push(format!("{label}{v}"));
                }
            }
            rows += &format!(
                "<dt>cited</dt><dd>{}<q class=\"quote\">{}</q>{}</dd>",
                esc(&where_),
                au(s(c, "quote")),
                if read.is_empty() { String::new() } else { format!("<span class=\"s\">{}</span>", esc(&read.join(DOT))) }
            );
        }
        rows
    }

    /// rb's receipt rows, [field, text, refs]: the field in dim mono, rb's wording with each id it names linked. A row
    /// only the user's own screen shows (the platform) is drawn on rb open only. `id` names the rows a part links to.
    fn rows(&self, rows: &[Value], id: &str) -> String {
        let mut out = String::new();
        let mut files = false;
        for row in rows {
            let Some(cells) = row.as_list() else { continue };
            let field = cells.first().and_then(Value::as_str).unwrap_or("");
            let text = cells.get(1).and_then(Value::as_str).unwrap_or("");
            // the field once: rb's row text opens with its own name ("produced 2026-10-14 ..", "repository when
            // recorded: ..") where it stands alone, and the label beside it already says it
            let text = match text.strip_prefix(field) {
                Some(rest) if !field.is_empty() && (rest.starts_with(' ') || rest.starts_with(':')) => rest.trim_start_matches(':').trim_start(),
                _ => text,
            };
            if field == "platform" && !self.live() {
                continue;
            }
            // a past version shows no command (guarantee 38): a row that says only one is not drawn, empty
            if !text.is_empty() && self.said(text).is_empty() {
                continue;
            }
            let refs: Vec<&str> = cells.get(2).and_then(Value::as_list).map_or_else(Vec::new, |l| l.iter().filter_map(Value::as_str).collect());
            let part = match field {
                _ if id.is_empty() => "",
                "repository" => "code",
                "config" => "config",
                "results" | "file" if !files => {
                    files = true;
                    "files"
                }
                _ => "",
            };
            let dt_id = if part.is_empty() { String::new() } else { format!(" id=\"{}:{part}\"", esc(id)) };
            // the row in the UI font, hashes and paths in monospace (DESIGN-PASS-1 target 7, P4)
            out += &format!("<dt{dt_id}>{}</dt><dd>{}</dd>", esc(field), mono_tokens(&self.with_refs(text, &refs)));
        }
        out
    }

    // ------------------------------------------------------------ the run receipt: run-<rid>

    pub fn run(&self, rid: &str, r: &Value) -> String {
        let id = run_anchor(rid);
        let x = s(r, "experiment_id");
        let retracted = at(r, "retracted");
        // the title in human words (P3), the id last; when it was produced and recorded once, by day, in the quiet line
        // (the same day said once); the full times, and how rb knows them, are the receipt's rows in Source details
        let produced = s(r, "produced");
        let recorded = match s(r, "when") {
            "" => s(r, "at").get(..10).unwrap_or(""),
            t => t,
        };
        let dates = match (produced, recorded) {
            ("", "") => String::new(),
            ("", rec) => format!("recorded {rec}"),
            (p, rec) if p == rec => format!("produced and recorded {rec}"),
            (p, "") => format!("produced {p}"),
            (p, rec) => format!("produced {p}{DOT}recorded {rec}"),
        };
        let label = s(r, "label");
        let mut top = format!(
            "<h3 class=\"tt\">{}</h3><div class=\"hd\"><span class=\"kind\">run</span><span class=\"dim sm\">in {}{}</span>{}</div>",
            esc(&self.run_named(rid)),
            self.xlink(x, "", x, ""),
            if dates.is_empty() { String::new() } else { format!("{DOT}{}", esc(&dates)) },
            if retracted.is_some() { "<span class=\"tag\">retracted</span>" } else { "" },
        );
        if let Some(ret) = retracted {
            top += &format!("<div class=\"retr\"><b>retracted</b> {}: {}</div>", esc(s(ret, "at")), self.au_cited(s(ret, "why")));
        }
        let note = s(r, "note");
        if !note.is_empty() {
            top += &format!("<p class=\"note\">note: {}</p>", self.au_cited(note));
        }
        if self.live() {
            top += &self.recorded_in(r);
        }
        let body = self.counts_toward(&id, r, retracted.is_some()) + &self.results(&id, r);
        // Source details: the command (said once, with how rb knows it, so its receipt row is not drawn again), then
        // the receipt's rows, the file the results were read from among them
        let has = |field: &str| list(r, "receipt_rows").iter().any(|c| c.as_list().and_then(|c| c.first()).and_then(Value::as_str) == Some(field));
        let receipt: Vec<Value> = list(r, "receipt_rows")
            .iter()
            .filter(|c| {
                let cells = c.as_list().map_or(&[][..], |l| l.as_slice());
                let field = cells.first().and_then(Value::as_str).unwrap_or("");
                let text = cells.get(1).and_then(Value::as_str).unwrap_or("");
                // the per-run values are the title's; the command is said above the rows, once
                field != "command" && !(field == "run" && !label.is_empty() && text.contains('='))
            })
            .cloned()
            .collect();
        let mut rows = String::new();
        if !has("results") && !s(r, "results").is_empty() {
            rows += &format!("<dt id=\"{}:files\">results</dt><dd>{}</dd>", esc(&id), esc(s(r, "results")));
        }
        rows += &self.rows(&receipt, &id);
        // when it was recorded, to the minute, where no receipt row says it (the head says the day)
        let at_ = s(r, "at");
        if !at_.is_empty() && !list(r, "receipt_rows").iter().any(|c| c.as_list().and_then(|c| c.get(1)).and_then(Value::as_str).map_or(false, |t| t.contains(at_))) {
            rows += &format!("<dt>recorded</dt><dd>{}</dd>", esc(at_));
        }
        // rb's own line of its results, which the card draws as rows (P8)
        let mets = s(r, "metrics_text");
        if !mets.is_empty() {
            rows += &format!("<dt>results as rb gives them</dt><dd>{}</dd>", esc(mets));
        }
        rows += &self.same_bytes(r);
        let made: Vec<&str> = strs(r, "made");
        if !made.is_empty() && !has("outputs") {
            rows += &format!("<dt>made</dt><dd>{}</dd>", listed(&made.iter().map(|m| self.named(m)).collect::<Vec<_>>(), made.iter().map(|m| self.id_link(m, "")).collect()));
        }
        let reproduce = if retracted.is_none() { self.run_reproduce(&id, r, x) } else { String::new() };
        format!(
            "<div class=\"card run{}\" id=\"{}\" data-kind=\"run\" data-home=\"x-{}\" data-crumb=\"{}\">{}{}{}{reproduce}</div>",
            if retracted.is_some() { " gone" } else { "" },
            esc(&id),
            esc(x),
            esc(&self.run_named(rid)),
            understand(&top),
            examine(&body),
            source_details(&id, &self.command(&id, r), &rows),
        )
    }

    /// A run past the cap: enough to place it (its experiment, label, results and what it counts toward) at its own
    /// anchor, so a drill to it never dead-ends, and where its whole receipt is.
    pub fn short_run(&self, rid: &str, r: &Value) -> String {
        let x = s(r, "experiment_id");
        // what it counts toward, each once, as its whole receipt says it: a retracted run counts toward nothing
        let mut counts: Vec<&str> = Vec::new();
        if at(r, "retracted").is_none() {
            for c in list(r, "counts_toward").iter().filter(|c| at(c, "counted").and_then(Value::as_bool) == Some(true)) {
                if !s(c, "criterion").is_empty() && !counts.contains(&s(c, "criterion")) {
                    counts.push(s(c, "criterion"));
                }
            }
        }
        // its results keep their anchor, which an analysis's table and a reading drill to, as a whole card's do
        let id = run_anchor(rid);
        format!(
            "<div class=\"card run short{}\" id=\"{}\" data-kind=\"run\" data-home=\"x-{}\" data-crumb=\"{}\">\
             <div class=\"lv\" data-level=\"understand\"><h3 class=\"tt\">{}</h3><div class=\"hd\"><span class=\"kind\">run</span><span class=\"dim sm\">in {}</span>{}</div>\
             </div><div class=\"lv\" data-level=\"examine\"><p class=\"sm mets\" id=\"{}:results\">{}</p>{}\
             <p class=\"stub\">This page holds the newest {RECEIPT_CAP} receipts of {}; rb open loads this one in full.</p></div></div>",
            if at(r, "retracted").is_some() { " gone" } else { "" },
            esc(&id),
            esc(x),
            esc(&self.run_named(rid)),
            esc(&self.run_named(rid)),
            self.xlink(x, "", x, ""),
            if at(r, "retracted").is_some() { "<span class=\"tag\">retracted</span>" } else { "" },
            esc(&id),
            results_line(r),
            if counts.is_empty() {
                String::new()
            } else {
                // the words first, the ids it counts toward after them (a short card is a placeholder: its whole receipt says each)
                format!("<p class=\"sm\">Counts toward {}: {}</p>", plural(counts.len(), "rule", "rules"), counts.iter().map(|c| link_c(&finding_anchor(c), c, "mono")).collect::<Vec<_>>().join(", "))
            },
            esc(x),
        )
    }

    /// Where a run was recorded (rb open only): where the document links it (`recorded_in`, the start place included),
    /// a link rb open's live module routes to that place (`data-place`, empty for the place it started on; `data-at`,
    /// the version, empty for now), else the place in plain words (`observed_in.label_short`).
    fn recorded_in(&self, r: &Value) -> String {
        if let Some(rec) = at(r, "recorded_in").filter(|v| v.is_dict()) {
            let label = match s(rec, "label") {
                "" => s(r, "observed_in.label_short"),
                l => l,
            };
            let href = s(rec, "href");
            if !label.is_empty() && href.starts_with('#') {
                return format!(
                    "<p class=\"sm obs-in\"><a href=\"{}\" data-place=\"{}\" data-at=\"\">{}</a></p>",
                    esc(href),
                    esc(s(rec, "place")),
                    esc(label)
                );
            }
        }
        match s(r, "observed_in.label_short") {
            "" => String::new(),
            l => format!("<p class=\"sm obs-in\">{}</p>", esc(l)),
        }
    }

    /// Counts toward (DESIGN-PASS-1 target 7): rows, each a rule's sentence with its state in plain words and its id
    /// last; the rules that count the run first, then those that leave it out, the reason said once for every rule it
    /// is the reason of; a rule that reads it through an analysis says so. A retracted run counts toward nothing.
    fn counts_toward(&self, id: &str, r: &Value, retracted: bool) -> String {
        if retracted {
            return format!(
                "<section class=\"part\" id=\"{}:counts\"><h4>Counts toward <span class=\"n\">nothing</span></h4><p class=\"dim sm\">A retracted run leaves every reading.</p></section>",
                esc(id)
            );
        }
        let items = list(r, "counts_toward");
        let mut counted: Vec<&Value> = Vec::new();
        let mut left: Vec<&Value> = Vec::new();
        for c in items {
            let cid = s(c, "criterion");
            let yes = at(c, "counted").and_then(Value::as_bool) == Some(true) || !s(c, "via").is_empty();
            let into = if yes { &mut counted } else { &mut left };
            if !into.iter().any(|d| s(d, "criterion") == cid) {
                into.push(c);
            }
        }
        left.retain(|c| !counted.iter().any(|d| s(d, "criterion") == s(c, "criterion")));
        let item = |c: &Value| -> String {
            let cid = s(c, "criterion");
            let via = s(c, "via");
            let through = if via.is_empty() { String::new() } else { format!(" <span class=\"dim\">through {}</span>", self.id_link(via, "")) };
            format!("<li>{}{through}</li>", self.crit_item(cid))
        };
        let mut body = String::new();
        if !counted.is_empty() {
            // the heading counts them; a line of its own only where rules that leave it out follow
            let lead = if left.is_empty() { String::new() } else { format!("<p class=\"sm\">Counted in {}.</p>", plural(counted.len(), "rule", "rules")) };
            body += &format!("{lead}<ul class=\"ctq\">{}</ul>", counted.iter().map(|c| item(c)).collect::<String>());
        }
        let mut reasons: Vec<&str> = Vec::new();
        for c in &left {
            if !reasons.contains(&s(c, "reason")) {
                reasons.push(s(c, "reason"));
            }
        }
        for reason in reasons {
            let these: Vec<&&Value> = left.iter().filter(|c| s(c, "reason") == reason).collect();
            let why = if reason.is_empty() { String::new() } else { format!(": {}", esc(reason)) };
            body += &format!(
                "<p class=\"sm\">Not counted in {}{why}.</p><ul class=\"ctq nc\">{}</ul>",
                plural(these.len(), "rule", "rules"),
                these.iter().map(|c| item(c)).collect::<String>()
            );
        }
        let n = format!(
            "{}{}",
            plural(counted.len(), "rule", "rules"),
            if left.is_empty() { String::new() } else { format!("{DOT}not counted in {}", left.len()) }
        );
        if body.is_empty() {
            body = "<p class=\"dim sm\">No recorded rule reads it.</p>".to_string();
        }
        format!("<section class=\"part\" id=\"{}:counts\"><h4>Counts toward <span class=\"n\">{}</span></h4>{body}</section>", esc(id), link(&format!("{id}:counts"), &n))
    }

    /// The results rb rounded, a row for each number in the UI font, two decimals with rb's digits on hover (P4, P5):
    /// from rb's results line ("fp32.top1=90.64 · int8.top1=90.61"), else its metrics line ("fp32: top1=0.7598
    /// onnx_top1=0.7569 · int8: ..."). The file they were read from, with its hash, is a source detail: the receipt's row.
    fn results(&self, id: &str, r: &Value) -> String {
        let pairs = result_pairs(r);
        if pairs.is_empty() {
            return format!("<section class=\"part\" id=\"{}:results\"><h4>Results</h4><p class=\"mets\">{}</p></section>", esc(id), esc(s(r, "metrics_text")));
        }
        // in digits said alike across the run's numbers, so two that differ never read the same
        let d = decimals(&pairs.iter().filter_map(|(_, v)| parsed(v)).collect::<Vec<_>>());
        let rows: String = pairs.iter().map(|(k, v)| format!("<dt>{}</dt><dd>{}</dd>", esc(k), nb(v, Some(d), false))).collect();
        format!("<section class=\"part\" id=\"{}:results\"><h4>Results</h4><dl class=\"ax mets\">{rows}</dl></section>", esc(id))
    }

    /// command (as given): always there, apart from the recipe, with how rb knows it. The receipt's own rows give its
    /// label ("command given later, not observed" for one rb did not see made the numbers) and why rb knows it as it
    /// does; the receipts do not say either again.
    fn command(&self, id: &str, r: &Value) -> String {
        let text = s(r, "command.text");
        // a past version shows no command (guarantee 38); that none was recorded it still says
        if self.past.is_some() && !text.is_empty() {
            return String::new();
        }
        let kind = s(r, "command.kind");
        let rows: Vec<&str> = list(r, "receipt_rows")
            .iter()
            .filter_map(Value::as_list)
            .filter(|c| c.first().and_then(Value::as_str) == Some("command"))
            .filter_map(|c| c.get(1).and_then(Value::as_str))
            .collect();
        let label = rows.iter().find_map(|t| t.strip_suffix(text).and_then(|l| l.strip_suffix(": "))).filter(|l| !text.is_empty() && !l.is_empty());
        let label = label.unwrap_or(w::COMMAND_AS_GIVEN);
        let why = rows.iter().find_map(|t| t.strip_prefix(HOW_RB_KNOWS).and_then(|t| t.strip_prefix(": "))).unwrap_or(kind);
        // how rb knows it is a row of its own, under its own label: never a word glued onto the command's last flag
        let (v, how) = if text.is_empty() {
            (esc(if kind.is_empty() { "not recorded" } else { kind }), String::new())
        } else if why.is_empty() {
            (format!("<code>{}</code>", esc(text)), String::new())
        } else {
            (format!("<code>{}</code>", esc(text)), format!("<span class=\"k\">{}</span><span class=\"v s\">{}</span>", HOW_RB_KNOWS, esc(why)))
        };
        format!("<div class=\"asgiven\" id=\"{}:command\"><span class=\"k\">{}</span><span class=\"v\">{v}</span>{how}</div>", esc(id), term(label, label))
    }

    /// Reproduce this run: what it takes to run it again, the recipe narrowed to the run to copy, and the same-bytes
    /// line where another run recorded the same file and made something.
    fn run_reproduce(&self, id: &str, r: &Value, x: &str) -> String {
        let Some(rep) = at(r, "reproduce") else { return String::new() };
        // folded under its own heading, with what it takes to run it again in one line: the lines, the recipe and what
        // the run made are one click down
        // what it takes, said as one count (DESIGN-PASS-1 target 6: "2 of 4 parts ready"); each part's line is inside
        let parts = list(rep, "ready");
        let ok = parts.iter().filter(|p| s(p, "state") == "ok").count();
        let ready = if parts.is_empty() { String::new() } else { format!("{ok} of {} parts ready", parts.len()) };
        // rb's own line, as rb gives it, opens the fold's inside (P8)
        let line = s(rep, "ready_line");
        let mut out = format!(
            "<details class=\"fold lv repro\" data-level=\"trace\" data-fold=\"{id}:reproduce\"><summary>Reproduce this run{}</summary>\
             <section class=\"part\" id=\"{id}:reproduce\">{}<p class=\"sm\">{}</p>",
            if ready.is_empty() { String::new() } else { format!("<span class=\"dim\">{DOT}{}</span>", esc(&ready)) },
            if line.is_empty() { String::new() } else { format!("<p class=\"sm dim\">{}</p>", esc(line)) },
            self.xlink(x, ":reproduce", &format!("the whole recipe for {x}"), ""),
            id = esc(id)
        );
        // Each line runs the card's full width: the experiment's three-column `ul.ready` grid leaves its text a
        // column a few characters wide in a pane as narrow as the inspector.
        for item in list(rep, "ready") {
            // the state once: rb's own line already opens with it ("ok \u{b7} commit defcbf0a63")
            let (state, text) = (s(item, "state"), s(item, "text"));
            let said = text.strip_prefix(state).map_or(false, |rest| rest.is_empty() || rest.starts_with(' '));
            out += &format!(
                "<p class=\"sm rdy {}\"><span aria-hidden=\"true\">{}</span> {}{}</p>",
                esc(state),
                ready_glyph(state),
                if said || state.is_empty() { String::new() } else { format!("{}{DOT}", esc(state)) },
                self.with_refs(text, &strs(item, "refs"))
            );
        }
        let script = s(rep, "script");
        if !script.is_empty() && self.past.is_none() {
            out += &format!(
                "<div class=\"cprow\" data-copybtn=\"{id}:recipe\" data-label=\"Copy recipe\"></div>{}",
                // one Copy, in the row above: the box takes none of its own, which would cover its first line
                format!("<div class=\"cmdbox nocp\"><pre class=\"cmd\" id=\"{id}:recipe\">{}</pre></div>", esc(script)),
                id = esc(id)
            );
        }
        for m in list(rep, "made") {
            let res = s(m, "resource");
            out += &format!(
                "<p class=\"sm\">{}{DOT}{}</p>",
                self.with_refs(s(m, "line"), &[s(m, "run"), s(m, "maker"), res].into_iter().filter(|r| run_anchor(r) != id).collect::<Vec<_>>()),
                link(&format!("{}:recipe", resource_anchor(res)), "how to get it again")
            );
        }
        for sb in list(r, "same_bytes") {
            let other = s(sb, "run");
            for res in strs(sb, "made") {
                out += &format!(
                    "<p class=\"sm samebytes\">The same bytes are recorded by {} ({}), which made {}{DOT}{}</p>",
                    self.run_link(other),
                    esc(s(sb, "experiment")),
                    link_c(&resource_anchor(res), res, "mono"),
                    link(&format!("{}:recipe", resource_anchor(res)), "how to get it again")
                );
            }
        }
        out + "</section></details>"
    }

    /// The runs that recorded the same file, as a receipt row.
    fn same_bytes(&self, r: &Value) -> String {
        let same: Vec<String> = list(r, "same_bytes")
            .iter()
            .map(|sb| {
                let other = s(sb, "run");
                let retracted = at(sb, "retracted").and_then(Value::as_bool).unwrap_or(false);
                format!("{} ({}{})", self.run_link(other), esc(s(sb, "experiment")), if retracted { ", retracted" } else { "" })
            })
            .collect();
        if same.is_empty() {
            String::new()
        } else {
            format!("<dt>same bytes as</dt><dd>{}</dd>", listed(&list(r, "same_bytes").iter().map(|sb| self.run_named(s(sb, "run"))).collect::<Vec<_>>(), same))
        }
    }

    // ------------------------------------------------------------ the resource card: r-<res>

    pub fn resource(&self, res: &str, r: &Value) -> String {
        let id = resource_anchor(res);
        let runs = list(r, "read_by.runs");
        let analyses: Vec<&str> = strs(r, "read_by.analyses");
        let readers = if runs.is_empty() { "no recorded run reads it".to_string() } else { plural(runs.len(), "run reads it", "runs read it") };
        let readers = if runs.is_empty() { esc(&readers) } else { link(&format!("{id}:rests"), &readers) };
        // its availability once, on its standing line, drilling to where its bytes are
        let mut who = vec![esc(s(r, "type")), term(s(r, "availability"), &link(&format!("{id}:places"), s(r, "availability")))];
        let role = s(r, "role");
        // its role once: a checkpoint whose role is checkpoint says it once (P2)
        if !role.is_empty() && role != s(r, "type") {
            who.push(esc(role));
        }
        who.push(readers);
        // its name first, the id last (P3)
        let top = format!(
            "<h3 class=\"tt\">{}</h3><div class=\"hd\"><span class=\"kind\">resource</span><span class=\"dim sm\">{}</span><span class=\"id\">{}</span></div>",
            au(s(r, "name")),
            who.join(DOT),
            esc(res),
        );
        // Examine: whether it can be had, what made it and what rests on it; where its bytes are, its identity and
        // how to get it again are source details
        let mut out = String::new();
        let made_by = list(r, "made_by");
        if !made_by.is_empty() {
            out += &format!(
                "<p class=\"sm\">made by {}</p>",
                listed(
                    &made_by.iter().map(|m| self.run_named(s(m, "run"))).collect::<Vec<_>>(),
                    made_by.iter().map(|m| format!("{} ({})", self.run_link(s(m, "run")), esc(s(m, "experiment")))).collect()
                )
            );
        }
        if !runs.is_empty() || !analyses.is_empty() {
            out += &self.resource_rests(&id, r, runs, &analyses);
        }
        let mut rows = format!(
            "<dt>identity</dt><dd>{}{}</dd>",
            esc(s(r, "identity")),
            if at(r, "exact").and_then(Value::as_bool) == Some(false) { " (not exact)" } else { "" }
        );
        let places = if self.live() && at(r, "where_short").is_some() { strs(r, "where_short") } else { strs(r, "where") };
        rows += &format!(
            "<dt id=\"{}:places\">where</dt><dd>{}</dd>",
            esc(&id),
            if places.is_empty() { "no place recorded".to_string() } else { places.iter().map(|p| format!("<span class=\"mono\">{}</span>", esc(p))).collect::<Vec<_>>().join(DOT) }
        );
        let url = s(r, "url");
        if !url.is_empty() {
            rows += &format!("<dt>url</dt><dd class=\"mono\">{}</dd>", esc(url));
        }
        // a past version shows no command (guarantee 38): no box, and rb's words for it only where they name none
        let command = if self.past.is_some() { "" } else { s(r, "acquisition.command") };
        let how = self.said(s(r, "how"));
        let how = how.as_str();
        let from = s(r, "acquisition.from");
        // on a past version the part a run's card links to stays, with the file it comes from or that no command shows
        let recipe = if s(r, "acquisition.command").is_empty() && s(r, "how").is_empty() {
            String::new()
        } else {
            format!(
                "<section class=\"part\" id=\"{}:recipe\"><h4>how to get it again</h4>{}{}</section>",
                esc(&id),
                match (command.is_empty(), how.is_empty()) {
                    (false, _) => cmdbox(command, ""),
                    (true, false) => format!("<p class=\"sm\">{}</p>", self.au_cited(how)),
                    (true, true) => format!("<p class=\"sm\">{PAST_NO_COMMAND}</p>"),
                },
                if from.is_empty() { String::new() } else { format!("<p class=\"from\">{}</p>", esc(from)) }
            )
        };
        format!(
            "<div class=\"card res\" id=\"{}\" data-kind=\"resource\" data-home=\"resources\" data-crumb=\"{}\">{}{}{}</div>",
            esc(&id),
            esc(res),
            understand(&top),
            examine(&out),
            source_details(&id, &recipe, &rows),
        )
    }

    /// What rests on a resource: what rests on the runs and analyses that read it, by question, and its readers by
    /// experiment, each folded under its count.
    fn resource_rests(&self, id: &str, r: &Value, runs: &[Value], analyses: &[&str]) -> String {
        let criteria = strs(r, "rests_on.criteria");
        let by_q = entries(r, "rests_on.by_question");
        // each count drills to the list it counts, inside its fold
        let mut lead = format!("read by {}", link(&format!("{id}:readers"), &plural(runs.len(), "run", "runs")));
        if !analyses.is_empty() {
            lead += &format!(" and {}", link(&format!("{id}:readers"), &plural(analyses.len(), "analysis", "analyses")));
        }
        if !criteria.is_empty() {
            lead += &format!(
                "; {} them, under {}",
                link(&format!("{id}:criteria"), &plural(criteria.len(), "rule reads", "rules read")),
                by_q.iter().map(|(q, _)| self.qlink(q, q, "")).collect::<Vec<_>>().join(", ")
            );
        }
        let crit_li: String = by_q
            .iter()
            .map(|(q, cs)| {
                let cs: Vec<&str> = cs.as_list().map_or_else(Vec::new, |l| l.iter().filter_map(Value::as_str).collect());
                format!(
                    "<li><span class=\"qh\">{}{DOT}{}</span><span class=\"ids\">{}</span></li>",
                    self.qlink(q, q, ""),
                    self.qlink(q, &plural(cs.len(), "rule", "rules"), ""),
                    cs.iter().map(|c| link_c(&finding_anchor(c), c, "mono")).collect::<Vec<_>>().join(" ")
                )
            })
            .collect();
        let mut by_x: Vec<(&str, Vec<&str>)> = Vec::new();
        for run in runs {
            let x = s(run, "experiment");
            match by_x.iter_mut().find(|(e, _)| *e == x) {
                Some((_, v)) => v.push(s(run, "run")),
                None => by_x.push((x, vec![s(run, "run")])),
            }
        }
        let mut runs_li: String = by_x
            .iter()
            .map(|(x, zs)| {
                format!(
                    "<li><span class=\"qh\">{}{DOT}{}</span><span class=\"ids\">{}</span></li>",
                    self.xlink(x, "", x, ""),
                    self.xlink(x, "", &plural(zs.len(), "run", "runs"), ""),
                    self.run_links(zs)
                )
            })
            .collect();
        runs_li += &analyses.iter().map(|a| format!("<li><span class=\"qh\">{}</span></li>", self.the_analysis(a))).collect::<String>();
        let crit_fold = if crit_li.is_empty() {
            String::new()
        } else {
            format!(
                "<details class=\"fold\" data-fold=\"{}:criteria\"><summary>{}, by question</summary><ul class=\"idl\" id=\"{}:criteria\">{crit_li}</ul></details>",
                esc(id),
                plural(criteria.len(), "rule", "rules"),
                esc(id)
            )
        };
        format!(
            "<section class=\"part\" id=\"{}:rests\"><h4>{DEPENDS}</h4><p class=\"sm\">{lead}</p>{crit_fold}\
             <details class=\"fold\" data-fold=\"{}:readers\"><summary>{}, by experiment</summary><ul class=\"idl\" id=\"{}:readers\">{runs_li}</ul></details></section>",
            esc(id),
            esc(id),
            plural(runs.len(), "run", "runs"),
            esc(id)
        )
    }

    // ------------------------------------------------------------ the analysis card: a-<aid>

    pub fn analysis(&self, aid: &str, a: &Value) -> String {
        let id = analysis_anchor(aid);
        let reads: Vec<&str> = strs(a, "reads");
        let used_by: Vec<&str> = strs(a, "used_by");
        // its experiment: the one its criteria are on, else the one its runs are in
        let x = used_by
            .iter()
            .filter_map(|c| self.finding_of(c))
            .map(|f| s(f, "experiment"))
            .chain(reads.iter().filter_map(|r| at(self.doc, "runs").and_then(|m| m.get(r))).map(|r| s(r, "experiment_id")))
            .find(|x| !x.is_empty())
            .unwrap_or("");
        let (home, within) = if x.is_empty() { ("runs-analyses".to_string(), String::new()) } else { (format!("x-{x}"), format!("<span class=\"dim sm\">in {}</span>", self.xlink(x, "", x, ""))) };
        // its title first, the id last (P3)
        let top = format!(
            "<h3 class=\"tt\">{}</h3><div class=\"hd\"><span class=\"kind\">analysis</span>{within}<span class=\"dim sm\">{}</span></div>",
            match s(a, "title") {
                "" => esc(AN_ANALYSIS),
                t => au(t),
            },
            esc(s(a, "recorded")),
        );
        // what it reports: the readings of the criteria that read it
        let mut out = String::new();
        for c in &used_by {
            let Some(f) = self.finding_of(c) else { continue };
            if s(f, "source.kind") != "analysis" || s(f, "source.id") != aid {
                continue;
            }
            // the reading in plain words, rb's metric key never the text (P8); the rule that reads it trails by its id
            let sentence = reading_sentence(f);
            out += &format!(
                "<section class=\"part\"><h4>Reports</h4><div class=\"big\"><a href=\"#{}:reading\">{}</a></div><p class=\"reads\">{}{}read by {}</p></section>",
                esc(&finding_anchor(c)),
                // in the numbers and digits its sentence says them in (FIX4)
                big_reading(f, false),
                sentence_html(&sentence, &format!("{}:reading", finding_anchor(c))),
                if sentence.is_empty() { String::new() } else { " ".into() },
                link_c(&finding_anchor(c), c, "mono id")
            );
        }
        let mut rows = String::new();
        for rid in &reads {
            let run = at(self.doc, "runs").and_then(|m| m.get(rid));
            rows += &format!(
                "<tr><td>{}</td><td>{}</td></tr>",
                self.run_link(rid),
                // each run's numbers drill to its results, where its receipt reads them
                match run.map_or("", |r| s(r, "metrics_text")) {
                    "" => String::new(),
                    m => link(&format!("{}:results", run_anchor(rid)), m),
                }
            );
        }
        out += &format!(
            "<section class=\"part\"><h4>reads <span class=\"n\">{}</span></h4><div class=\"wrap\"><table class=\"obs\" id=\"{}:reads\"><thead><tr><th>run</th><th>results</th></tr></thead><tbody>{rows}</tbody></table></div></section>",
            link(&format!("{id}:reads"), &plural(reads.len(), "run", "runs")),
            esc(&id)
        );
        if !used_by.is_empty() {
            out += &format!(
                "<section class=\"part\"><h4>{DEPENDS}</h4><ul class=\"rests\">{}</ul></section>",
                used_by.iter().map(|c| format!("<li>{}</li>", self.crit_item(c))).collect::<String>()
            );
        }
        let mut rx = String::new();
        let script = s(a, "script");
        if !script.is_empty() {
            rx += &format!("<dt>script</dt><dd>{}</dd>", esc(script));
        }
        let sha = s(a, "sha256");
        if !sha.is_empty() {
            rx += &format!("<dt>sha256</dt><dd>{}</dd>", esc(sha));
        }
        format!(
            "<div class=\"card ana\" id=\"{}\" data-kind=\"analysis\" data-home=\"{}\" data-crumb=\"{}\">{}{}{}</div>",
            esc(&id),
            esc(&home),
            esc(&self.analysis_named(aid)),
            understand(&top),
            examine(&out),
            source_details(&id, "", &rx),
        )
    }

    // ------------------------------------------------------------ the call card: call-<subject>

    /// The call card of one subject, from each call rb lists on it (`calls`, at least one): the first gives its label
    /// and title, each gives its reason, and each action is offered once.
    pub fn call(&self, calls: &[&Value]) -> String {
        let c = calls[0];
        let subject = s(c, "subject");
        let id = call_anchor(subject);
        let q = s(c, "question");
        // the finding the call is on: the criterion itself, or the gate of the experiment it steers
        let (cid, f) = match self.finding_of(subject) {
            Some(f) => (subject, Some(f)),
            None => {
                let x = list(self.doc, "experiments").iter().find(|x| s(x, "id") == subject);
                let ids: Vec<&str> = x.map_or_else(Vec::new, |x| strs(x, "findings"));
                let gate = ids.iter().find(|c| self.finding_of(c).map_or(false, |f| s(f, "role") == "gate")).or(ids.first()).copied();
                match gate.and_then(|g| self.finding_of(g).map(|f| (g, f))) {
                    Some((g, f)) => (g, Some(f)),
                    None => ("", None),
                }
            }
        };
        let mut lineage = vec![format!("<a href=\"#your-call\">{}</a>", self.calls_view())];
        if !q.is_empty() && q != ":unfiled" {
            lineage.push(self.qlink(q, q, ""));
        }
        lineage.push(esc(subject));
        // the eyebrow says whose call it is and its id, once; the title; then the verdict, said once (plain words
        // pass 4): no status chips, no kind word, no "why" that says the title again
        // the rule it is about, by id, where the call is on something else (an experiment and its gate)
        let on = match f {
            Some(f) if cid != subject => format!("{DOT}{} {}", if s(f, "role") == "gate" { "gate" } else { "rule" }, link_c(&finding_anchor(cid), cid, "mono id")),
            _ => String::new(),
        };
        let mut top = format!(
            "<p class=\"lineage\">{}</p><div class=\"hd\"><span class=\"kind\">{}</span>{DOT}{}{on}</div><h3 class=\"tt\">{}</h3>",
            lineage.join(" \u{203a} "),
            self.eyebrow(),
            self.id_link(subject, "id"),
            self.au_cited(s(c, "title")),
        );
        if let Some(f) = f {
            let fa = finding_anchor(cid);
            top += &format!("<p class=\"verdict\">{}</p>", sentence_html_to(&verdict(f), &format!("{fa}:runs"), &format!("{fa}:rule")));
        }
        // what a call asks where it is not a result's review ("Choose whether this work continues"); a review's is the
        // eyebrow's already
        let asks = s(c, "label");
        if !asks.is_empty() && asks != "Review this result" {
            top += &format!("<p class=\"asks\">{}</p>", esc(asks));
        }
        // a reason the verdict does not say (a call made on an earlier reading, too little evidence) stays, once, in
        // rb's words; one that is the rule's state again is said by the verdict
        let rule = if cid.is_empty() { String::new() } else { format!("{}:rule", finding_anchor(cid)) };
        let mut why = String::new();
        for c in calls {
            let label = s(c, "reason.label");
            if f.is_some() && says_the_state(label) {
                continue;
            }
            // the parts of rb's detail the card says already (the rule's state, the count it rests on) are said once
            let d = if f.is_some() { s(c, "reason.detail").split(DOT).filter(|p| !says_the_count_or_state(p)).collect::<Vec<_>>().join(DOT) } else { s(c, "reason.detail").to_string() };
            let detail = match d.as_str() {
                "" => String::new(),
                d if rule.is_empty() => format!("<span class=\"rd2\">{DOT}{}</span>", esc(d)),
                d => format!("<span class=\"rd2\">{DOT}{}</span>", link(&rule, d)),
            };
            // "Rule: can't tell yet" is the verdict's state again: its detail, why rb can't tell, stays as the line
            if f.is_some() && says_cant_tell(label) {
                if !d.is_empty() {
                    let line = up_first(&d);
                    why += &format!("<p class=\"rsn\"><span class=\"rl\">{}</span></p>", if rule.is_empty() { esc(&line) } else { link(&rule, &line) });
                }
                continue;
            }
            let label = if label.is_empty() {
                self.with_refs(s(c, "what"), &[subject, cid])
            } else if rule.is_empty() {
                esc(label)
            } else {
                link(&rule, label)
            };
            why += &format!("<p class=\"rsn\"><span class=\"rl\">{label}</span>{detail}</p>");
        }
        top += &why;
        let mut body = String::new();
        let mut cited = String::new();
        if let Some(f) = f {
            let (read, source) = self.call_read(cid, f);
            body += &read;
            cited = source;
        }
        format!(
            "<article class=\"card call\" id=\"{}\" data-kind=\"call\" data-home=\"your-call\" data-crumb=\"call {}\">{}{}{}{}</article>",
            esc(&id),
            esc(subject),
            understand(&top),
            examine(&body),
            source_details(&id, &cited, ""),
            self.composer(calls, subject, &id, ""),
        )
    }

    /// A call card's eyebrow, as markup: "Your call" on rb open and rb page; on an export "Open call" with no viewer
    /// named, "Your call" in the owner's working view, "Still open" for a reader (OWNER-READER).
    fn eyebrow(&self) -> String {
        if self.surface == Surface::Export { by_viewer("span", "", "Open call", "Your call", STILL_OPEN) } else { "Your call".to_string() }
    }

    /// What a call rests on, under its verdict: the figure with its axis, rb's notes, what the reading is from, and
    /// what is recorded on it; the cited source goes to Source details.
    fn call_read(&self, cid: &str, f: &Value) -> (String, String) {
        let fa = finding_anchor(cid);
        let mut out = format!("<section class=\"part\">{}", figure::draw(f, false));
        let units = s(f, "units_line");
        if !units.is_empty() {
            // "3 seeds from 6 runs" reads "From 3 seeds (6 runs)"
            let said = match units.split_once(" from ") {
                Some((u, runs)) => format!("From {} ({runs})", link(&format!("{fa}:runs"), u)),
                None => format!("From {}", link(&format!("{fa}:runs"), units)),
            };
            out += &format!("<p class=\"sm\">{said}{DOT}{}</p>", link(&fa, "the whole finding"));
        }
        let notes: String = strs(f, "says.caveats")
            .iter()
            .map(|c| {
                let (code, text) = c.split_once(": ").unwrap_or(("", c));
                format!("<li><b>{}</b> {}</li>", esc(&code.replace('_', " ")), esc(text))
            })
            .collect();
        if !notes.is_empty() {
            out += &format!("<ul class=\"notes\">{notes}</ul>");
        }
        // what is recorded on it: the call line rb gives, or that nothing is yet
        let recorded = match self.call_line(f) {
            "" => NOTHING_RECORDED.to_string(),
            line => esc(&self.said(line)),
        };
        out += &format!("<p class=\"sm\">{recorded}</p>");
        // the cited file and its words are a source detail
        let mut source = String::new();
        if let Some(cited) = at(f, "cited") {
            let line = num(cited, "line");
            let locator = s(cited, "locator");
            let at_ = if line.is_empty() { s(cited, "path").to_string() } else { format!("{}:{line}", s(cited, "path")) };
            // a locator that is the file and line again is said once
            let locator = if locator == at_ { "" } else { locator };
            source = format!(
                "<p class=\"sm\">cited: <span class=\"mono\">{}</span>{} <q class=\"quote\">{}</q></p>",
                esc(&at_),
                if locator.is_empty() { String::new() } else { format!("{DOT}{}", esc(locator)) },
                au(s(cited, "quote"))
            );
        }
        (out + "</section>", source)
    }

    /// Decide on a card that is not the call's own (a finding a call waits on): why rb asks, once for each reason, its
    /// numbers drilling to the card's rule, then the composer, at `<id>:decide`. On an export the call is recorded from its
    /// call card: here the reasons, and a link there (OWNER-READER).
    fn decide(&self, calls: &[&Value], subject: &str, id: &str) -> String {
        let rule = format!("{id}:rule");
        let mut lead = String::new();
        // the card says its verdict once, under its title (FIX2): a reason that is the rule's state again ("Rule not
        // met · estimate +3.6 < 5") is not said here; one the verdict does not say stays, without the parts it does
        for c in calls {
            let label = s(c, "reason.label");
            if says_the_state(label) {
                continue;
            }
            let d = s(c, "reason.detail").split(DOT).filter(|p| !says_the_count_or_state(p)).collect::<Vec<_>>().join(DOT);
            // "Rule: can't tell yet" is the verdict's state again: its detail, why rb can't tell, stays as the line
            if says_cant_tell(label) {
                if !d.is_empty() {
                    lead += &format!("<p class=\"rsn\"><span class=\"rl\">{}</span></p>", link(&rule, &up_first(&d)));
                }
                continue;
            }
            let detail = match d.as_str() {
                "" => String::new(),
                d => format!("<span class=\"rd2\">{DOT}{}</span>", link(&rule, d)),
            };
            lead += &format!("<p class=\"rsn\"><span class=\"rl\">{}</span>{detail}</p>", link(&rule, label));
        }
        if self.surface != Surface::Export {
            // nothing to offer and no reason left to say: the part the card's tab lands on says nothing is recorded yet
            if lead.is_empty() && calls.iter().all(|c| list(c, "actions").is_empty()) {
                return format!("<section class=\"part decide lv\" data-level=\"decide\" id=\"{}:decide\"><h4>Decide</h4><p class=\"sm\">{NOTHING_RECORDED}</p></section>", esc(id));
            }
            return self.composer(calls, subject, id, &lead);
        }
        // on an export the call is recorded from its call card, said once there; here, the reasons for every viewer
        // (a reader reads them as status: OWNER-READER keeps every open item), and where its actions are
        let call = call_anchor(subject);
        let has_actions = calls.iter().any(|c| !list(c, "actions").is_empty());
        let to = if has_actions { format!("{call}:decide") } else { call };
        // a reader with no reason left to read (the verdict says the state) reads that nothing is recorded on it yet
        if lead.is_empty() {
            lead = format!("<p class=\"sm\" data-vw=\"reader\">{NOTHING_RECORDED}</p>");
        }
        format!(
            "<section class=\"part lv\" data-level=\"examine\" id=\"{}:decide\"><h4 data-vw=\"none owner\">Record a call</h4><h4 data-vw=\"reader\">{STILL_OPEN}</h4>{lead}\
             <p class=\"sm\" data-vw=\"none owner\">{ON_ITS_CARD} {}.</p></section>",
            esc(id),
            link(&to, "its call card")
        )
    }

    /// Record a call: each action rb offers with its own line and what the review will read (document mode); the
    /// segmented choice and the composer the script drives (app mode). On an export the app mode is the owner's alone,
    /// in the hub's working view (OWNER-READER): with no viewer named it draws as a document, and a reader of a shared
    /// link is shown no Decide part at all. The composer sends nothing anywhere: it composes the line to copy.
    fn composer(&self, calls: &[&Value], subject: &str, id: &str, lead: &str) -> String {
        let mut actions: Vec<&Value> = Vec::new();
        for a in calls.iter().flat_map(|c| list(c, "actions")) {
            if !actions.iter().any(|b| s(b, "action") == s(a, "action")) {
                actions.push(a);
            }
        }
        // a past version is read only and shows no command (guarantee 38): why rb asks, and nothing to run, whatever
        // actions the document holds; the anchor stays, as the finding links it
        if self.past.is_some() {
            return format!("<section class=\"part decide lv\" data-level=\"decide\" id=\"{}:decide\">{lead}<p class=\"sm\">{PAST_READ_ONLY}</p></section>", esc(id));
        }
        // nothing to offer: why rb asks still stands, where the card's tab and review link land
        if actions.is_empty() {
            if lead.is_empty() {
                return String::new();
            }
            return format!("<section class=\"part decide lv\" data-level=\"decide\" id=\"{}:decide\"><h4>Why this needs a call</h4>{lead}</section>", esc(id));
        }
        let mut lines = String::new();
        for a in &actions {
            lines += &format!(
                "<li data-action=\"{}\" data-label=\"{}\" data-cons=\"{}\"><span class=\"an\">{}</span>{}<span class=\"cons\">{WILL_SAY} {}</span></li>",
                esc(s(a, "action")),
                esc(s(a, "label")),
                esc(s(a, "consequence")),
                esc(s(a, "label")),
                format!("<div class=\"cmdbox\"><pre class=\"cmd line\">{}</pre></div>", esc(s(a, "line"))),
                esc(s(a, "consequence"))
            );
        }
        let export = self.surface == Surface::Export;
        // on an export only the owner's working view drives the composer: its parts are marked for the owner alone
        let owner = if export { " data-vw=\"owner\"" } else { "" };
        let app = {
            let buttons: String = actions
                .iter()
                .map(|a| {
                    let key = s(a, "action").chars().next().map(String::from).unwrap_or_default();
                    format!(
                        "<button type=\"button\" data-act=\"pick\" data-action=\"{}\" aria-pressed=\"false\">{}<kbd aria-hidden=\"true\">{}</kbd></button>",
                        esc(s(a, "action")),
                        esc(s(a, "label")),
                        esc(&key)
                    )
                })
                .collect();
            format!(
                "<div class=\"seg\"{owner} role=\"group\" aria-label=\"Record a call on {}\">{buttons}</div>\
                 <div class=\"composer\"{owner} hidden><label>why (rb records it with the call)<input class=\"why\" type=\"text\" autocomplete=\"off\" spellcheck=\"true\" placeholder=\"your reason\"></label>\
                 <div class=\"cmdbox nocp\"><pre class=\"cmd line\"></pre><button type=\"button\" class=\"cp\" data-act=\"copy\" disabled>Copy</button></div>\
                 <p class=\"willread\">{WILL_SAY} <span></span></p></div>",
                esc(subject)
            )
        };
        let honest = if export { by_viewer("p", " class=\"honest\"", HONEST_EXPORT, HONEST_OWN, "") } else { format!("<p class=\"honest\">{HONEST_OWN}</p>") };
        format!(
            "<section class=\"part decide lv\" data-level=\"decide\" id=\"{}:decide\"{}><h4>Record a call</h4>{lead}{app}<ul class=\"acts\" data-subject=\"{}\">{lines}</ul>{honest}</section>",
            esc(id),
            if export { " data-vw=\"none owner\"" } else { "" },
            esc(subject)
        )
    }

    // ------------------------------------------------------------ citations (CITE)

    /// A citation's Source view (CITE item 3), at `cite-<key>`, where views.rs's chips lead: the file by its name, the
    /// cited lines with their numbers (the cited ones marked) as rb read them, at which commit, what rb could not read or
    /// a shared page may not quote, which notes, rules and calls cite it, and on rb open a link that opens the file in
    /// the user's editor (`open_url`, a vscode:// link: rb open serves no file of its own); the sha256 in Source details.
    pub fn source(&self, key: &str, c: &Value) -> String {
        let id = source_anchor(key);
        let ide = esc(&id);
        let label = s(c, "label");
        let title = chip_inner(c);
        let line = num(c, "line");
        let end = num(c, "end");
        let at_lines = if end.is_empty() || end == line { format!("line {line}") } else { format!("lines {line}-{end}") };
        let top = format!(
            "<h3 class=\"tt\">{title}</h3><div class=\"hd\"><span class=\"kind\">cited file</span></div>"
        );
        let mut body = String::new();
        let (missing, not_quoted) = (s(c, "missing"), s(c, "not_quoted"));
        let lines = list(c, "lines");
        let quoted = at(c, "quoted").and_then(Value::as_bool).unwrap_or(!lines.is_empty());
        if !missing.is_empty() {
            body += &format!("<p class=\"miss\">{}</p>", esc(missing));
        } else if !not_quoted.is_empty() {
            body += &format!("<p class=\"nq\">{}<span class=\"dim\">{DOT}{NOT_QUOTED_OPEN}</span></p>", esc(not_quoted));
        } else if quoted && !lines.is_empty() && self.past.is_some() {
            // a past version quotes no line of a file (guarantee 38): a cited line may hold a recorded command however
            // the file writes it (spaced, after a prompt, over a `\`, in a sentence), so none is drawn, as words either;
            // which lines it cites the label above says
            body += &format!("<p class=\"sm dim\">{PAST_NO_QUOTE}</p>");
        } else if quoted && !lines.is_empty() {
            let rows: Vec<String> = lines
                .iter()
                .map(|l| {
                    let on = at(l, "cited").and_then(Value::as_bool).unwrap_or(false);
                    // the cited lines marked, as text too: a screen reader and print say which they are
                    format!(
                        "<span class=\"sl{}\"><span class=\"ln\">{}</span>{} {}</span>",
                        if on { " on" } else { "" },
                        esc(&num(l, "n")),
                        if on { "<span class=\"cm\" title=\"cited\">\u{25b8}</span>" } else { "<span class=\"cm\"> </span>" },
                        esc(s(l, "text"))
                    )
                })
                .collect();
            body += &format!("<pre class=\"srclines\">{}</pre>", rows.join("\n"));
            let cut = at(c, "cut").and_then(Value::as_i64).unwrap_or(0);
            if cut > 0 {
                body += &format!("<p class=\"sm dim\">{} not shown: rb quotes at most 8 lines of a citation.</p>", plural(cut as usize, "more cited line", "more cited lines"));
            }
        }
        let read = s(c, "read");
        if !read.is_empty() {
            body += &format!("<p class=\"sm dim\">{}</p>", esc(read));
        }
        let url = s(c, "open_url");
        if self.live() && !url.is_empty() && self.past.is_none() {
            body += &format!("<p class=\"sm\"><a class=\"openfile\" href=\"{}\">Open the file</a><span class=\"dim\"> in your editor, at {}</span></p>", esc(url), esc(&at_lines));
        }
        let lines_part = format!("<section class=\"part\" id=\"{ide}:lines\"><h4>{}</h4>{body}</section>", if quoted { "The cited lines" } else { "The citation" });
        let by: String = list(c, "cited_by").iter().map(|b| format!("<li>{}</li>", self.citer(s(b, "kind"), s(b, "id")))).collect();
        let by = if by.is_empty() {
            format!("<p class=\"sm dim\">{CITED_BY_RB}</p>")
        } else {
            format!("<ul class=\"plain citedby\">{by}</ul>")
        };
        let by_part = format!("<section class=\"part\" id=\"{ide}:cited-by\"><h4>Cited by</h4>{by}</section>");
        let mut rows = String::new();
        let path = s(c, "path");
        if !path.is_empty() {
            rows += &format!("<dt>file</dt><dd class=\"mono\">{}</dd>", esc(path));
        }
        if !s(c, "commit").is_empty() {
            rows += &format!("<dt>commit</dt><dd class=\"mono\">{}</dd>", esc(s(c, "commit")));
        }
        if !s(c, "sha256").is_empty() {
            rows += &format!("<dt>sha256</dt><dd class=\"mono\">{}</dd>", esc(s(c, "sha256")));
        }
        let home = list(c, "cited_by").first().map_or_else(|| "overview".to_string(), |b| self.citer_home(s(b, "kind"), s(b, "id")));
        format!(
            "<div class=\"card source\" id=\"{ide}\" data-kind=\"source\" data-home=\"{}\" data-crumb=\"{}\">{}{}{}</div>",
            esc(&home),
            esc(if label.is_empty() { key } else { label }),
            understand(&top),
            examine(&(lines_part + &by_part)),
            source_details(&id, "", &rows),
        )
    }

    /// What cites a file, as a Source view lists it: its kind in words, then what it is, linked where the page draws
    /// it, its id last.
    fn citer(&self, kind: &str, id: &str) -> String {
        // the id drills too, to the same card or view as the title (the drill law), chosen by the citer's kind: two
        // kinds of record may share an id
        let anchor = match kind {
            "criterion" => self.finding_of(id).map(|_| finding_anchor(id)),
            "experiment" => self.xdrawn(id, "").then(|| format!("x-{id}")),
            "question" => list(self.doc, "questions").iter().any(|q| s(q, "id") == id).then(|| format!("q-{id}")),
            "note" => match self.note_home(id) {
                Some(x) if x == "notes" => Some(x),
                Some(x) => self.xdrawn(&x, "").then(|| format!("x-{x}")),
                None => None,
            },
            _ => None,
        };
        let idm = match anchor {
            Some(a) => format!(" {}", link_c(&a, id, "mono id")),
            None => format!(" <span class=\"mono id\">{}</span>", esc(id)),
        };
        match kind {
            // a note by what it is on, never by the id rb minted (FIX7)
            "note" => match self.note_home(id) {
                Some(x) if x != "notes" => format!("A note on {}", self.xlink(&x, "", &x, "")),
                Some(_) => format!("A note on the project{DOT}{}", link("notes", "the project's notes")),
                None => "A note".to_string(),
            },
            "experiment" => {
                let title = list(self.doc, "experiments").iter().find(|x| s(x, "id") == id).map_or("", |x| s(x, "title"));
                format!("Experiment {}{idm}", self.xlink(id, "", if title.is_empty() { id } else { title }, ""))
            }
            "criterion" => match self.finding_of(id) {
                Some(f) => format!("Rule {}{idm}", link(&finding_anchor(id), if s(f, "statement").is_empty() { id } else { s(f, "statement") })),
                None => format!("Rule{idm}"),
            },
            "question" => {
                let text = list(self.doc, "questions").iter().find(|q| s(q, "id") == id).map_or("", |q| s(q, "text"));
                format!("Question {}{idm}", self.qlink(id, if text.is_empty() { id } else { text }, ""))
            }
            "call" => {
                if list(self.doc, "calls").iter().any(|c| s(c, "subject") == id) {
                    let lead = if self.surface == Surface::Export { by_viewer("span", "", "An open call on", "Your call on", "An open call on") } else { "Your call on".to_string() };
                    format!("{lead} {}", link(&call_anchor(id), id))
                } else if let Some(row) = list(self.doc, "pending.rows").iter().find(|r| s(r, "id") == id && matches!(s(r, "kind"), "past" | "proposed")) {
                    // a call that waits for a confirmation, by what it is, as History's rows say it (model.rs
                    // minted_names), never by the id rb minted (FIX9)
                    let action = s(row, "text").split(' ').next().unwrap_or("");
                    let subject = self.id_link(s(row, "subject"), "");
                    match s(row, "kind") {
                        "past" => format!("A past call to {} {subject} mentioned, not confirmed", esc(action)),
                        _ => format!("A proposed call to {} {subject}, not made", esc(action)),
                    }
                } else {
                    format!("A call on {}", self.id_link(id, ""))
                }
            }
            "run" => format!("Run {}", self.id_link(id, "mono")),
            _ => esc(id),
        }
    }

    /// The view a citer lives in, where a Source view opened cold is drawn over.
    fn citer_home(&self, kind: &str, id: &str) -> String {
        match kind {
            "note" => match self.note_home(id) {
                Some(x) if x != "notes" => format!("x-{x}"),
                _ => "overview".to_string(),
            },
            "experiment" if self.xdrawn(id, "") => format!("x-{id}"),
            "criterion" => self.question_of(id).map_or_else(|| "criteria".to_string(), |q| format!("q-{q}")),
            "question" => format!("q-{id}"),
            "call" => "your-call".to_string(),
            "run" => at(self.doc, "runs").and_then(|m| m.get(id)).map_or_else(|| "overview".to_string(), |r| format!("x-{}", s(r, "experiment_id"))),
            _ => "overview".to_string(),
        }
    }

    /// Where note `id` is drawn: the experiment whose notes hold it, or "notes" for the project's own.
    fn note_home(&self, id: &str) -> Option<String> {
        if let Some(x) = list(self.doc, "experiments").iter().find(|x| strs(x, "note_ids").contains(&id)) {
            return Some(s(x, "id").to_string());
        }
        list(self.doc, "home.notes").iter().any(|n| s(n, "id") == id).then(|| "notes".to_string())
    }

    /// Author text, verbatim, as `au` draws it, each citation the document resolved a chip that opens its Source view
    /// (CITE items 2 and 3), as views.rs draws it.
    fn au_cited(&self, text: &str) -> String {
        let cites = cites_in(self.doc, text);
        if cites.is_empty() {
            return au(text);
        }
        let mut out = String::new();
        let mut from = 0;
        for (i, end, c) in cites {
            out += &esc(&text[from..i]);
            out += &cite_chip(c);
            from = end;
        }
        out += &esc(&text[from..]);
        format!("<span class=\"au\">{out}</span>")
    }

    // ------------------------------------------------------------ qualification, reading group and variant cards

    /// A question's qualifications, each a card: its label, what it means, and the criteria that carry it.
    fn qualifications(&self, q: &Value) -> String {
        let qid = s(q, "id");
        let mut out = String::new();
        for (i, lim) in list(q, "overview.limits").iter().enumerate() {
            let label = s(lim, "label");
            let criteria = strs(lim, "criteria");
            let meaning = qualification_meaning(label).map_or(String::new(), |m| format!("<p class=\"sm\">{}</p>", esc(m)));
            out += &format!(
                "<div class=\"card lim\" id=\"{}\" data-kind=\"qualification\" data-home=\"q-{}\" data-crumb=\"{}\">\
                 <h3 class=\"tt\">{}</h3><div class=\"hd\"><span class=\"kind\">qualification</span>{}</div>{meaning}\
                 <p class=\"dim sm\">{} it</p><ul class=\"ctw\" id=\"{}:criteria\">{}</ul></div>",
                esc(&qualification_anchor(qid, i)),
                esc(qid),
                esc(label),
                esc(label),
                self.qlink(qid, qid, "id"),
                link(&format!("{}:criteria", qualification_anchor(qid, i)), &plural(criteria.len(), "comparison carries", "comparisons carry")),
                esc(&qualification_anchor(qid, i)),
                criteria.iter().map(|c| format!("<li>{}</li>", self.crit_item(c))).collect::<String>()
            );
        }
        out
    }

    /// A question's findings by rb's rule word, each group a card ("rules met · 3"); its anchor keeps rb's word.
    fn reading_groups(&self, q: &Value) -> String {
        let qid = s(q, "id");
        let mut groups: Vec<(String, Vec<&str>)> = Vec::new();
        for cid in strs(q, "findings") {
            let Some(f) = self.finding_of(cid) else { continue };
            let word = reading_word(s(f, "standing").split(DOT).next().unwrap_or(""));
            match groups.iter_mut().find(|(w, _)| *w == word) {
                Some((_, v)) => v.push(cid),
                None => groups.push((word, vec![cid])),
            }
        }
        groups
            .iter()
            .map(|(word, cids)| {
                format!(
                    "<div class=\"card lim\" id=\"{}\" data-kind=\"reading\" data-home=\"q-{}\" data-crumb=\"{}\">\
                     <h3 class=\"tt\">Rules: {} ({})</h3><div class=\"hd\"><span class=\"kind\">by reading</span>{}</div><ul class=\"ctw\" id=\"{}:criteria\">{}</ul></div>",
                    esc(&reading_anchor(qid, word)),
                    esc(qid),
                    esc(reading_shown(word)),
                    reading_title(word),
                    link(&format!("{}:criteria", reading_anchor(qid, word)), &cids.len().to_string()),
                    self.qlink(qid, qid, "id"),
                    esc(&reading_anchor(qid, word)),
                    cids.iter().map(|c| format!("<li>{}</li>", self.crit_item(c))).collect::<String>()
                )
            })
            .collect()
    }

    /// An experiment's variants, each a card: its role, what it varies, and the means of its comparison table with the
    /// runs behind them and the criteria that read each.
    fn variants(&self, x: &Value) -> String {
        let eid = s(x, "id");
        let rows = list(x, "table.rows");
        let metrics: Vec<&str> = list(x, "table.metrics").iter().map(|m| s(m, "name")).collect();
        let runs: Vec<&str> = strs(x, "run_ids")
            .into_iter()
            .filter(|r| at(self.doc, "runs").and_then(|m| m.get(r)).map_or(false, |r| at(r, "retracted").is_none()))
            .collect();
        let mut names: Vec<&str> = Vec::new();
        for r in rows {
            let v = s(r, "variant");
            if !v.is_empty() && !names.contains(&v) {
                names.push(v);
            }
        }
        let mut out = String::new();
        for v in names {
            let mine: Vec<&Value> = rows.iter().filter(|r| s(r, "variant") == v).collect();
            let role = mine.first().map_or("", |r| s(r, "role"));
            let varies: Vec<String> = mine
                .first()
                .and_then(|r| at(r, "varies"))
                .and_then(Value::as_dict)
                .map_or_else(Vec::new, |d| d.iter().map(|(k, v)| setting_said(k, v.as_str().unwrap_or(""))).collect());
            let cols: Vec<&str> = metrics.iter().copied().filter(|m| mine.iter().any(|r| at(r, &format!("metrics.{m}")).is_some())).collect();
            let conditioned = mine.iter().any(|r| at(r, "where").and_then(Value::as_dict).map_or(false, |d| !d.is_empty()));
            let units: Vec<String> = cols.iter().map(|m| list(x, "table.metrics").iter().find(|t| s(t, "name") == *m).map_or("", |t| s(t, "unit")).to_string()).collect();
            let labels: Vec<&str> = cols.iter().map(|m| list(x, "table.metrics").iter().find(|t| s(t, "name") == *m).map_or("", |t| s(t, "label"))).collect();
            // rb's label for a metric where it gives one, as the grid titles it
            let heads: Vec<(String, String)> =
                cols.iter().zip(&units).zip(&labels).map(|((m, u), l)| { let (title, unit) = metric_head(m, u); (if l.is_empty() { title } else { l.to_string() }, unit) }).collect();
            let head: String = heads.iter().map(|(title, _)| format!("<th>{}</th>", esc(title))).collect();
            // each column's means in the digits the page's grid says them in (FIX5)
            let places: Vec<usize> = cols.iter().zip(&heads).map(|(m, (_, u))| grid_places(rows, m, u)).collect();
            let mut body = String::new();
            for r in &mine {
                let cell: Vec<String> =
                    at(r, "where").and_then(Value::as_dict).map_or_else(Vec::new, |d| d.iter().map(|(k, v)| setting_said(k, v.as_str().unwrap_or(""))).collect());
                let label = if cell.is_empty() { "mean".to_string() } else { cell.join(", ") };
                let mut tds = String::new();
                for (i, m) in cols.iter().enumerate() {
                    let Some(cellv) = r.get("metrics").and_then(|ms| ms.get(m)) else {
                        tds += "<td></td>";
                        continue;
                    };
                    let read_by: Vec<String> = list(cellv, "read_by")
                        .iter()
                        .map(|rb| format!("{} reads {}", link_c(&finding_anchor(s(rb, "criterion")), s(rb, "criterion"), "mono"), esc(s(rb, "reads"))))
                        .collect();
                    tds += &format!(
                        "<td class=\"num\"><b>{}</b> <span class=\"dim\">{}</span>{}</td>",
                        link(
                            &format!("{}:runs", variant_anchor(eid, v)),
                            &at(cellv, "mean").and_then(Value::as_f64).filter(|v| v.is_finite()).map_or_else(|| num(cellv, "mean"), |v| with_unit(&fixed(v, places[i], false), &heads[i].1))
                        ),
                        link(&format!("{}:runs", variant_anchor(eid, v)), &format!("n={}", num(cellv, "n"))),
                        if read_by.is_empty() { String::new() } else { format!("<span class=\"s\">{}</span>", read_by.join(", ")) }
                    );
                }
                body += &format!("<tr><td class=\"dim\">{}</td>{tds}</tr>", esc(&label));
            }
            let behind: Vec<&str> = mine.iter().flat_map(|r| strs(r, "runs")).collect();
            let behind = if behind.is_empty() { runs.clone() } else { behind };
            let run_li: String = behind
                .iter()
                .map(|z| {
                    format!("<li>{}</li>", self.run_link(z))
                })
                .collect();
            out += &format!(
                "<div class=\"card variant\" id=\"{}\" data-kind=\"variant\" data-home=\"x-{}\" data-crumb=\"{}\">\
                 <h3 class=\"tt\">{}</h3><div class=\"hd\"><span class=\"kind\">variant</span><span class=\"dim sm\">{}</span>{}</div>{}\
                 <div class=\"wrap\"><table class=\"obs\"><thead><tr><th>{}</th>{head}</tr></thead><tbody>{body}</tbody></table></div>\
                 <h4 id=\"{}:runs\">the runs behind each mean <span class=\"n\"><span>{DOT}</span>{}</span></h4><ul class=\"ctw\">{run_li}</ul></div>",
                esc(&variant_anchor(eid, v)),
                esc(eid),
                esc(v),
                esc(&arm_name(v)),
                esc(role),
                self.xlink(eid, "", eid, "id"),
                if varies.is_empty() { String::new() } else { format!("<p class=\"sm\">{}</p>", esc(&varies.join(", "))) },
                if conditioned { "cell" } else { "" },
                esc(&variant_anchor(eid, v)),
                link(&format!("{}:runs", variant_anchor(eid, v)), &plural(behind.len(), "run", "runs")),
            );
        }
        out
    }
}

/// rb's phrase as the screens say it (w::plain_word: "rule met", "can't tell yet", "not tested", "no interval"),
/// escaped, with the glossary's line on the words where it holds them; a leading count stays outside the hover. "rule"
/// alone keeps no hover here: the card's kind chip carries it.
/// What a Source view says beside "Not quoted on a shared page": where the lines are read.
const NOT_QUOTED_OPEN: &str = "rb open shows these lines on the record's own machine.";

/// What a Source view says when no note, rule or call cites its file: rb's own lines do (a history line, a recipe).
const CITED_BY_RB: &str = "Only rb's own lines cite it here, such as the history or a recipe.";

/// A citation's words as its chip and its Source view's title say them: "README.md · line 3", the path's last part
/// bold, its folder before it, quiet.
fn chip_inner(c: &Value) -> String {
    let (key, name, folder, label) = (s(c, "key"), s(c, "name"), s(c, "folder"), s(c, "label"));
    match label.strip_prefix(&format!("{folder}{name}")).filter(|_| !name.is_empty()) {
        Some(rest) => {
            let folder = if folder.is_empty() { String::new() } else { format!("<span class=\"cdir\">{}</span>", esc(folder)) };
            format!("{folder}<b>{}</b>{}", esc(name), esc(rest))
        }
        None => esc(if label.is_empty() { key } else { label }),
    }
}

/// A citation as a chip that opens its Source view (views.rs cite_chip's markup): what rb could not read, or a line a
/// shared page may not quote, said beside it.
fn cite_chip(c: &Value) -> String {
    let key = s(c, "key");
    let (missing, not_quoted) = (s(c, "missing"), s(c, "not_quoted"));
    let class = format!("cite{}{}", if missing.is_empty() { "" } else { " miss" }, if not_quoted.is_empty() { "" } else { " nq" });
    let hover = [s(c, "path"), missing, not_quoted].into_iter().filter(|s| !s.is_empty()).collect::<Vec<_>>().join(DOT);
    let chip = format!("<a class=\"{class}\" href=\"#{}\" data-cite=\"{}\" title=\"{}\">{}</a>", esc(&source_anchor(key)), esc(key), esc(&hover), chip_inner(c));
    match [missing, not_quoted].into_iter().find(|s| !s.is_empty()) {
        Some(said) => format!("{chip} <span class=\"cmiss\">({})</span>", esc(said)),
        None => chip,
    }
}

/// Every citation the document resolved (`citations`) in `text`, in order and apart: its byte span and the citation,
/// found by each form the document gives it, and only where it stands as a citation: not run into a path, a word or a
/// number (views.rs cites_in's rule).
fn cites_in<'a>(doc: &'a Value, text: &str) -> Vec<(usize, usize, &'a Value)> {
    let Some(Value::Dict(all)) = at(doc, "citations") else { return Vec::new() };
    let path_char = |c: char| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '/' | '-' | ':' | '@' | '#' | '~' | '\\');
    let mut found: Vec<(usize, usize, &Value)> = Vec::new();
    for c in all.values() {
        let mut forms = strs(c, "raw");
        forms.push(s(c, "key"));
        for form in forms.into_iter().filter(|f| !f.is_empty()) {
            for (i, _) in text.match_indices(form) {
                let end = i + form.len();
                let before = text[..i].chars().next_back();
                let after: Vec<char> = text[end..].chars().take(2).collect();
                let runs_on = match after.as_slice() {
                    [c, ..] if c.is_ascii_alphanumeric() || *c == '_' => true,
                    ['-' | ':', d, ..] if d.is_ascii_digit() => true,
                    _ => false,
                };
                // as M's finder (ledger::cite::spans) reads it: not a path run into a longer one, nor one after `$`,
                // `%`, `{` or `=` (a variable, a template, a setting); a decimal or a letter beyond ASCII ends it
                let after_mark = before.is_some_and(|c| path_char(c) || matches!(c, '$' | '%' | '{' | '='));
                if after_mark || runs_on {
                    continue;
                }
                found.push((i, end, c));
            }
        }
    }
    // the longest at a place first, then each kept only where it overlaps none kept before it
    found.sort_by(|a, b| a.0.cmp(&b.0).then(b.1.cmp(&a.1)));
    let mut kept: Vec<(usize, usize, &Value)> = Vec::new();
    for f in found {
        if kept.last().map_or(true, |k| f.0 >= k.1) {
            kept.push(f);
        }
    }
    kept
}

fn said_plain(phrase: &str) -> String {
    let said = w::plain_word(phrase);
    let (n, key) = match said.split_once(' ') {
        Some((n, rest)) if n.chars().all(|c| c.is_ascii_digit()) => (format!("{n} "), rest.to_string()),
        _ => (String::new(), said.clone()),
    };
    if w::hint(&key).is_some() && key != "rule" { format!("{}{}", esc(&n), term(&key, &esc(&key))) } else { esc(&said) }
}

/// rb's rule line without the rule's state it leads with ("criterion met: estimate -0.0029 at least -0.01" reads
/// "estimate -0.0029 at least -0.01"): the card says its verdict once, under its title (FIX2), and rb's line is a
/// source detail. A line that is the state alone says nothing more.
fn without_state(line: &str) -> &str {
    let is_state = |lead: &str| {
        let l = lead.trim();
        let p = w::plain_word(l);
        l.starts_with("criterion ")
            || l.starts_with("estimate meets")
            || l.starts_with("recorded values meet")
            || l.starts_with("gate ")
            || p.starts_with("rule ")
            || p.starts_with("gate")
            || matches!(p.as_str(), "met" | "not met" | "can't tell yet" | "not tested" | "reproduced" | "not reproduced")
    };
    match line.split_once(": ") {
        Some((lead, rest)) if is_state(lead) => rest,
        _ if is_state(line) => "",
        _ => line,
    }
}

/// The reading group a rule word falls in: a met word, however rb qualifies it, is "criteria met".
fn reading_word(rule: &str) -> String {
    match rule {
        "criterion met" | "estimate meets criterion" | "recorded values meet criterion" => "criteria met".into(),
        "criterion not met" => "criteria not met".into(),
        "criterion undetermined" => "criteria undetermined".into(),
        "criterion untested" => "criteria untested".into(),
        w => w.into(),
    }
}

/// A reading group as a screen says it (PLAIN-WORDS-PASS-1, the lexicon's D1); the group's anchor keeps rb's word.
fn reading_shown(word: &str) -> &str {
    match word {
        "criteria met" => "rules met",
        "criteria not met" => "rules not met",
        "criteria undetermined" => "can't tell yet",
        "criteria untested" => "not tested",
        w => w,
    }
}

/// A reading group's title: its words, with the glossary's line that keeps "can't tell yet" and "not tested" apart.
/// After "Rules: " (PLAIN-WORDS-PASS-3), so "rules met" reads "Rules: met".
fn reading_title(word: &str) -> String {
    let shown = reading_shown(word);
    term(shown, &esc(shown.strip_prefix("rules ").unwrap_or(shown)))
}

/// What a qualification means, in rb's own words (SHELL-SPEC section 12's glossary lines).
fn qualification_meaning(label: &str) -> Option<&'static str> {
    Some(match label {
        "Retrospective" | "Rule written after the results" => "declared after the evidence existed or after the data was seen, or linked to the question after either: never read as prospective.",
        "No interval" => "one estimate with no interval, so how much it could vary is unknown. rb gives one from 3 units, and none across fixed design cells.",
        "Uncertainty remains" => "unresolved: the interval includes 0.",
        "Fixed design cells" => {
            "the reading spans fixed design choices: each cell is read on its own, with how many meet and their range, and no sampling interval across cells."
        }
        "Reversal observed; repeatability uncertain" => "the two sides of a declared reversal measured opposite directions, but not both with intervals clear of 0.",
        _ => return None,
    })
}

/// The first byte where `word` stands whole in `text` (no letter, digit, "_" or "-" either side).
fn whole_word(text: &str, word: &str) -> Option<usize> {
    let inside = |c: char| c.is_alphanumeric() || c == '_' || c == '-';
    let mut from = 0;
    while let Some(i) = text[from..].find(word).map(|i| i + from) {
        let before = text[..i].chars().next_back().map_or(false, inside);
        let after = text[i + word.len()..].chars().next().map_or(false, inside);
        if !before && !after {
            return Some(i);
        }
        from = i + word.len();
    }
    None
}


// ------------------------------------------------------------ plain words (DESIGN-PASS-1, PLAIN-WORDS-PASS-3)

/// How many decimals a set of numbers is said with (P5): two, or as many as keep two significant figures of the
/// smallest nonzero one, so a small difference never reads as 0.00; then more while two numbers that differ would read
/// alike (an estimate and the threshold it is held to, an interval's two ends). Full precision stays on hover and in
/// Source details.
pub(crate) fn decimals(vals: &[f64]) -> usize {
    // what differs only by floating-point noise (an estimate of 1e-17 rb itself writes "+0") is no difference
    let scale = vals.iter().map(|v| v.abs()).filter(|v| v.is_finite()).fold(0.0, f64::max).max(1.0);
    let noise = scale * 1e-12;
    let small = vals.iter().map(|v| v.abs()).filter(|v| *v > noise && v.is_finite()).fold(f64::INFINITY, f64::min);
    let mut d = if !small.is_finite() || small >= 0.005 { 2 } else { ((-small.log10()).floor() as usize + 2).clamp(2, 6) };
    while d < 6 && vals.iter().enumerate().any(|(i, a)| vals[i + 1..].iter().any(|b| (a - b).abs() > noise && format!("{a:.d$}") == format!("{b:.d$}"))) {
        d += 1;
    }
    d
}

/// A number with `d` decimals, signed when `signed` (a difference), with a minus that is a hyphen as rb writes it.
pub(crate) fn fixed(v: f64, d: usize, signed: bool) -> String {
    let t = format!("{v:.d$}");
    let t = if t.trim_start_matches('-').chars().all(|c| c == '0' || c == '.') { t.trim_start_matches('-').to_string() } else { t };
    if signed && !t.starts_with('-') && v != 0.0 { format!("+{t}") } else { t }
}

/// A number as rb wrote it ("-0.07667", "+0.07307", "90.48"), parsed; none where it is not one.
fn parsed(text: &str) -> Option<f64> {
    text.trim().trim_start_matches('+').replace('\u{2212}', "-").parse::<f64>().ok().filter(|v| v.is_finite())
}

/// A number said in two decimals, rb's own digits on hover: `<span class="nb" title="-0.07667">-0.08</span>`.
fn nb(text: &str, d: Option<usize>, signed: bool) -> String {
    match parsed(text) {
        Some(v) => {
            let shown = fixed(v, d.unwrap_or_else(|| decimals(&[v])), signed);
            // rb's digits on hover only where the shown number dropped some ("-0.08" for "-0.07667", never "-0.20" for "-0.2")
            if parsed(&shown) == Some(v) { esc(&shown) } else { format!("<span class=\"nb\" title=\"{}\">{}</span>", esc(text.trim()), esc(&shown)) }
        }
        None => esc(text),
    }
}

/// A run's value with its unit (FIX5: "14.00%", "0.50 nats"), as the verdict says the reading: a run that gives the
/// difference by itself in the difference's unit ("0.62 points"), an arm's own number in the level's.
fn value_with_unit(shown: &str, text: &str, unit: &str, difference: bool) -> String {
    let amount = parsed(text).map_or(0.0, f64::abs);
    let u = if difference { unit_word(unit, true, amount) } else { unit.to_string() };
    match u.as_str() {
        "" => shown.to_string(),
        "%" => format!("{shown}%"),
        u => format!("{shown} {}", esc(u)),
    }
}

/// A reading said large, as the card's verdict says it (FIX4): the estimate with its unit, then its interval in
/// brackets, a level's bounds each with its unit ("13.07% (11.00% to 15.14%)"), a difference's bare ("-0.08 points
/// (-0.23 to +0.07)"). The numbers are the ones the verdict reads, in its digits, rb's text on hover; rb's text where
/// the reading holds no number. `interval` false says the estimate alone.
fn big_reading(f: &Value, interval: bool) -> String {
    let r = at(f, "reading");
    let est = r.map_or("", |r| s(r, "estimate_text"));
    let iv = r.map_or("", |r| s(r, "interval_text"));
    let (lo, hi) = iv.trim_matches(|c| c == '[' || c == ']').split_once(", ").unwrap_or(("", ""));
    let n = |k: &str, text: &str| at(f, k).and_then(Value::as_f64).filter(|v| v.is_finite()).or_else(|| parsed(text));
    let (estv, lov, hiv) = (n("reading.estimate", est), n("reading.lo", lo), n("reading.hi", hi));
    let compared = compared(f);
    let d = reading_digits(f);
    let unit = &unit_of(f);
    // an amount's unit agrees with the number shown ("1.00 point")
    let shown = estv.map(|v| parsed(&fixed(v.abs(), d, false)).unwrap_or(v.abs())).unwrap_or(0.0);
    let unit = if compared { unit_word(unit, true, shown) } else { unit.to_string() };
    let said = |v: Option<f64>, text: &str| v.map_or_else(|| esc(text), |v| nb_of(v, text, d, compared));
    let bound = |v: Option<f64>, text: &str| if compared { said(v, text) } else { format!("{}{}", said(v, text), big_unit(&unit)) };
    let iv = if !interval || lo.is_empty() || hi.is_empty() { String::new() } else { format!("<span class=\"iv\"> ({} to {})</span>", bound(lov, lo), bound(hiv, hi)) };
    format!("<span class=\"e\">{}</span>{}{iv}", said(estv, est), big_unit(&unit))
}

/// A number said in `d` decimals with rb's text of it on hover where the shown number dropped digits; the number is
/// the one the card's verdict reads, so a big number and its sentence never round one value two ways.
fn nb_of(v: f64, text: &str, d: usize, signed: bool) -> String {
    let shown = fixed(v, d, signed);
    if parsed(&shown) == Some(v) && parsed(text).map_or(true, |t| t == v) { esc(&shown) } else { format!("<span class=\"nb\" title=\"{}\">{}</span>", esc(text.trim()), esc(&shown)) }
}

/// The unit a difference is said in: a difference of percentages is in points (P5: "0.08 points lower").
fn unit_word(unit: &str, difference: bool, amount: f64) -> String {
    match unit {
        // rb writes a percentage's unit as "%" or "percent"
        "%" | "percent" if difference => if (amount.abs() - 1.0).abs() < 1e-12 { "point".into() } else { "points".into() },
        "" => String::new(),
        u => u.into(),
    }
}

/// A large number's unit beside it: "%" against the number ("0.76%"), any other a word apart ("0.08 points").
fn big_unit(unit: &str) -> String {
    match unit {
        "" => String::new(),
        "%" => "<span class=\"u\">%</span>".into(),
        u => format!("<span class=\"u\"> {}</span>", esc(u)),
    }
}

/// A number with its unit: "0.08 points", "90.48%", "12 ms".
fn with_unit(n: &str, unit: &str) -> String {
    let unit = clean(unit);
    match unit.as_str() {
        "" => n.to_string(),
        "%" => format!("{n}%"),
        u => format!("{n} {u}"),
    }
}

/// An arm or label as a sentence names it: a precision code (letters then digits, "fp32", "int8") in capitals, the
/// rest as the record has it but for its underscores; "fp32 baseline" reads "FP32 baseline".
pub(crate) fn arm_name(label: &str) -> String {
    // a variant by its plain name, its underscores as spaces ("step_by_step" reads "step by step"), as every view
    // says it (FIX4)
    clean(label)
        .replace('_', " ")
        .split(' ')
        .map(|w| {
            let letters = w.trim_end_matches(|c: char| c.is_ascii_digit());
            if !letters.is_empty() && letters.len() < w.len() && letters.chars().all(|c| c.is_ascii_lowercase()) { w.to_ascii_uppercase() } else { w.to_string() }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// A run's results as (title, value) pairs, each by its metric's title and its arm's plain name ("Top-1, INT8"): from
/// rb's results line ("fp32.top1=90.64 · int8.top1=90.61"), else its metrics line ("fp32: top1=0.7598 onnx_top1=0.7569 ·
/// int8: ..."); the full card and the short one say them alike (FIX5).
fn result_pairs(r: &Value) -> Vec<(String, String)> {
    let mut pairs: Vec<(String, String)> = Vec::new();
    for part in s(r, "results").split(DOT).filter(|p| !p.is_empty()) {
        if let Some((k, v)) = part.split_once('=') {
            let (arm, metric) = k.split_once('.').unwrap_or(("", k));
            pairs.push((result_key(arm, metric), v.to_string()));
        }
    }
    if pairs.is_empty() {
        for part in s(r, "metrics_text").split(DOT).filter(|p| !p.is_empty()) {
            let (arm, rest) = part.split_once(": ").unwrap_or(("", part));
            for kv in rest.split(' ') {
                if let Some((k, v)) = kv.split_once('=') {
                    pairs.push((result_key(arm, k), v.to_string()));
                }
            }
        }
    }
    pairs
}

/// A short run card's results on one line, as the full card says them (FIX5): "Top-1, FP32 0.7598 · ONNX top-1, FP32
/// 0.7569"; rb's metrics line where it holds no number.
fn results_line(r: &Value) -> String {
    let pairs = result_pairs(r);
    if pairs.is_empty() {
        return esc(s(r, "metrics_text"));
    }
    let d = decimals(&pairs.iter().filter_map(|(_, v)| parsed(v)).collect::<Vec<_>>());
    pairs.iter().map(|(k, v)| format!("{} {}", esc(k), nb(v, Some(d), false))).collect::<Vec<_>>().join(DOT)
}

/// A run's result row as the page's grid heads it (FIX4): the metric by its title, then the arm by its plain name
/// ("Top-1, INT8", "Val loss, step by step"); a number no arm is named for by its metric alone.
fn result_key(arm: &str, metric: &str) -> String {
    let m = metric_said(metric);
    if arm.trim().is_empty() { m } else { format!("{m}, {}", arm_name(arm)) }
}

/// A setting and its value as a title says it (P2, P4): a number after its name ("seed 2", "calib images 128"), a
/// word alone with its underscores as hyphens ("per-tensor").
fn setting_said(name: &str, value: &str) -> String {
    let (name, value) = (clean(&name.replace('_', " ")), clean(value));
    let value = value.as_str();
    if value.is_empty() {
        name
    } else if parsed(value).is_some() {
        format!("{name} {value}")
    } else {
        value.replace('_', "-")
    }
}

/// Items of a list joined: with commas, or with semicolons where an item's name (`names`, as said) holds a comma,
/// so the list reads one way only (FIX8).
fn listed(names: &[String], items: Vec<String>) -> String {
    items.join(if names.iter().any(|n| n.contains(',')) { "; " } else { ", " })
}

/// Links said after rb's line, each plain separator in an element of its own: ", ", or "; " where a name holds a comma.
fn after_list(names: &[String], links: Vec<String>) -> String {
    let sep = if names.iter().any(|n| n.contains(',')) { "<span>; </span>" } else { "<span>, </span>" };
    links.join(sep)
}

/// A run's title in human words, from rb's label of it ("fp32 and int8 · seed 0 · scheme per_tensor · calib_images
/// 128"): "FP32 and INT8, seed 0, per-tensor, calib images 128". The label itself stays the run's crumb.
fn run_title(label: &str) -> String {
    let mut parts = label.split(DOT);
    let mut out: Vec<String> = Vec::new();
    if let Some(first) = parts.next() {
        out.push(arm_name(first));
    }
    for p in parts {
        let (k, v) = p.rsplit_once(' ').unwrap_or((p, ""));
        out.push(setting_said(k, v));
    }
    out.join(", ")
}

/// What tells each run apart from another whose label reads the same, as the model's run_names says it after the
/// label: ", recorded 2026-10-06 10:32 UTC", then ", the 2nd of 2" where that is shared too. It is what runs.{id}.named
/// says after "run <label>" ("a run of <experiment>" where it has none); where the document has no `named`, what its
/// History row that records the run says after it, which the model words the same; else the same from each run's `at`,
/// which the document keeps to the minute, so runs of one minute are told apart by their order.
fn run_apart(doc: &Value) -> IndexMap<String, String> {
    let Some(runs) = at(doc, "runs").and_then(Value::as_dict) else { return IndexMap::new() };
    let said = |r: &Value| match s(r, "label") {
        "" => format!("a run of {}", s(r, "experiment_id")),
        l => format!("run {l}"),
    };
    // History's row that records each run, which the model writes with the same words (model.rs minted_names)
    let logged: IndexMap<&str, &str> = list(doc, "history.log")
        .iter()
        .filter(|row| s(row, "op") == "record")
        .filter_map(|row| Some((list(row, "refs").first()?.as_str()?, s(row, "sentence").strip_prefix("record ")?)))
        .collect();
    let mut groups: IndexMap<String, Vec<(&String, &Value)>> = IndexMap::new();
    for (id, r) in runs.iter().filter(|(_, r)| r.is_dict()) {
        groups.entry(said(r)).or_default().push((id, r));
    }
    let mut out = IndexMap::new();
    for (words, twins) in groups.iter().filter(|(_, g)| g.len() > 1) {
        for &(id, r) in twins {
            let named = s(r, "named");
            let logged = logged.get(id.as_str()).and_then(|l| l.strip_prefix(words.as_str())).and_then(said_apart);
            let apart = match (named.strip_prefix(words.as_str()), logged) {
                (Some(rest), _) if !named.is_empty() => rest.to_string(),
                (_, Some(rest)) => rest.to_string(),
                _ => {
                    let alike: Vec<&String> = twins.iter().filter(|(_, x)| s(x, "at") == s(r, "at")).map(|(k, _)| *k).collect();
                    let place = if alike.len() > 1 {
                        format!(", the {} of {}", ordinal(alike.iter().position(|k| *k == id).unwrap_or(0) + 1), alike.len())
                    } else {
                        String::new()
                    };
                    format!(", recorded {}{place}", s(r, "at"))
                }
            };
            if !apart.is_empty() {
                out.insert(id.clone(), apart);
            }
        }
    }
    out
}

/// The words that tell a run apart at the head of `rest`, what follows its label on a History row (", recorded
/// 2026-10-13 14:12:05 UTC", then ", the 2nd of 2" where that is shared), without what the row says after them.
fn said_apart(rest: &str) -> Option<&str> {
    let when = rest.strip_prefix(", recorded ")?;
    let end = when.find(" UTC")? + 4;
    let (stamp, after) = (&when[..end - 4], &when[end..]);
    if !stamp.chars().all(|c| c.is_ascii_digit() || " -:".contains(c)) || stamp.len() < 16 {
        return None;
    }
    let place = after
        .strip_prefix(", the ")
        .and_then(|p| p.find(" of ").map(|i| (i, p)))
        .map_or(0, |(i, p)| 6 + i + 4 + p[i + 4..].chars().take_while(char::is_ascii_digit).count());
    Some(&rest[..", recorded ".len() + end + place])
}

/// "1st", "2nd", "3rd", "4th", "11th", "22nd".
fn ordinal(n: usize) -> String {
    let end = match (n % 10, n % 100) {
        (_, 11..=13) => "th",
        (1, _) => "st",
        (2, _) => "nd",
        (3, _) => "rd",
        _ => "th",
    };
    format!("{n}{end}")
}

/// Whether a call's reason is the rule's state again ("Rule not met", "Not reproduced", "Gate missed"), which the
/// call card's verdict says.
fn says_the_state(label: &str) -> bool {
    let l = label.to_lowercase();
    // an undetermined rule waits on a call for a reason other than its units, its interval or its direction, which
    // the verdict does not say: its reason stays
    ["rule not met", "criterion not met", "not reproduced", "gate missed", "rule untested", "criterion untested", "not tested"]
        .contains(&l.as_str())
}

/// Whether a call's reason label is "can't tell yet" again ("Rule: can't tell yet"), which the verdict says; its detail
/// (why rb can't tell) is a reason the verdict does not say.
fn says_cant_tell(label: &str) -> bool {
    let l = label.to_lowercase();
    ["rule: can't tell yet", "gate: can't tell yet", "gate, can't tell yet", "criterion undetermined", "gate undetermined"].contains(&l.as_str())
}

/// The first letter of a line in capitals, for a detail said as a line of its own.
fn up_first(line: &str) -> String {
    let mut c = line.chars();
    c.next().map_or_else(String::new, |f| f.to_uppercase().collect::<String>() + c.as_str())
}

/// Whether a part of a call's detail is what its card says already: the rule's state ("criterion met") or the count
/// the reading rests on ("1 run", "3 seeds").
fn says_the_count_or_state(part: &str) -> bool {
    let p = part.trim();
    let plain = w::plain_word(p);
    // a count in any unit's noun ("1 run", "3 seeds", "2 photos"): the reading under the verdict says it
    let count = p.split_once(' ').map_or(false, |(n, what)| n.parse::<u64>().is_ok() && !what.is_empty() && what.chars().all(char::is_alphabetic));
    count || plain.starts_with("rule ") || plain.starts_with("gate ") || matches!(p, "criterion met" | "criterion not met" | "reproduced" | "not reproduced")
}

/// What a rule reads, from rb's own rule text (record.rs's rule_with): the estimate, the interval's worse end, both
/// its ends, the interval against 0 (better) or inside a margin (equivalent), or every unit's value (each).
#[derive(Clone, Copy, PartialEq, Debug)]
enum Kind {
    Estimate,
    WorseEnd,
    BothEnds,
    Better,
    Equivalent,
    Each,
}

fn rule_kind(f: &Value) -> Kind {
    let rule = s(f, "rule");
    if rule.contains("its interval clear of 0") {
        Kind::Better
    } else if rule.contains("its interval inside \u{b1}") {
        Kind::Equivalent
    } else if rule.contains("each unit's") || rule.contains("in every cell") {
        Kind::Each
    } else if rule.contains("worse bound") || rule.contains(", lower bound") || rule.contains(", upper bound") {
        Kind::WorseEnd
    } else if rule.contains(", its interval") {
        Kind::BothEnds
    } else {
        Kind::Estimate
    }
}

/// Whether a finding's reading is a difference of two arms (not a level, a cited number or a ratio).
pub(crate) fn compared(f: &Value) -> bool {
    let reference = s(f, "reference_label");
    !reference.is_empty() && !reference.starts_with("cited ") && !ratio(f)
}

/// Text from the record with rb's two sentence markers taken out, so a number's link always closes.
fn clean(text: &str) -> String {
    text.replace(['\u{1}', '\u{2}', '\u{3}', '\u{4}'], "")
}

/// A finding's reading as one plain sentence (P5, P8): "INT8 is 0.08 points lower than the FP32 baseline (-0.23 to
/// +0.07), 3 seeds." A level: "FP32 baseline: 90.48% (89.92 to 91.04), 3 seeds." rb's comparison text stays in
/// Details.
fn reading_sentence(f: &Value) -> String {
    reading_parts(f).map_or(String::new(), |(said, count)| format!("{said}{count}."))
}

/// A finding's reading in two parts: what it reads with its interval ("INT8 is 0.08 points lower than the FP32
/// baseline (-0.23 to +0.07)"), and the count it rests on (", 3 seeds"); none where the reading has no estimate.
fn reading_parts(f: &Value) -> Option<(String, String)> {
    let n = |k: &str| at(f, k).and_then(Value::as_f64).filter(|v| v.is_finite());
    let est = n("reading.estimate")?;
    let (lo, hi) = (n("reading.lo"), n("reading.hi"));
    let unit = &unit_of(f);
    let (subject, reference) = arms(f);
    let reference = reference.as_str();
    let compared = !reference.is_empty() && !reference.starts_with("cited ") && !ratio(f);
    let subject = match subject.as_str() {
        "" => metric_said(metric_name(f)),
        l => said_arm(l),
    };
    let d = reading_digits(f);
    let level = reference.is_empty() || reference.starts_with("cited ");
    // a level's bounds keep its unit, as the page's lists say them ("13.07% (11.00% to 15.14%)"); a difference's are
    // the points or units its amount just said (FIX4)
    let bound = |v: f64| if level { with_unit(&mark(&fixed(v, d, false)), unit) } else { mark(&fixed(v, d, compared)) };
    let iv = match (lo, hi) {
        (Some(lo), Some(hi)) => format!(" ({} to {})", bound(lo), bound(hi)),
        // rb gives no interval across fixed design cells, ever; elsewhere one comes from 3 units
        _ if !list(f, "design_cells").is_empty() => " (no interval across design cells)".to_string(),
        _ => " (no interval yet)".to_string(),
    };
    let units = at(f, "reading.n").and_then(Value::as_i64).unwrap_or(0);
    let u = s(f, "reading.unit");
    let count = if units > 0 && !u.is_empty() { format!(", {} {}", mark(&units.to_string()), if units == 1 { u.to_string() } else { format!("{u}s") }) } else { String::new() };
    let said = if compared {
        // an estimate that reads 0 in its digits is no direction ("the same as", never "0.00 higher than")
        let zero = fixed(est.abs(), d, false).chars().all(|c| c == '0' || c == '.');
        let way = if zero { "the same as" } else if est < 0.0 { "lower than" } else { "higher than" };
        let amount = if zero { String::new() } else { format!("{} ", with_unit(&mark(&fixed(est.abs(), d, false)), &unit_word(unit, true, est.abs()))) };
        format!("{subject} is {amount}{way} {}{iv}", the_arm(reference))
    } else if !reference.is_empty() && !reference.starts_with("cited ") {
        // a ratio of two arms is no difference: said as what it is
        format!("{subject} against {}: {}{iv}", the_arm(reference), with_unit(&mark(&fixed(est, d, false)), unit))
    } else {
        format!("{subject}: {}{iv}", with_unit(&mark(&fixed(est, d, false)), unit))
    };
    Some((said, count))
}

/// A rule's number as it was declared, with the unit the card's verdict says it in ("5 points", "0.9%", "0 nats"),
/// for the figure's band and the verdict alike (FIX4).
pub(crate) fn rule_number_said(f: &Value, t: f64) -> String {
    with_unit(&fixed(t, digits(t), false), &unit_word(&unit_of(f), compared(f), t.abs()))
}

/// The digits a finding's reading is said in: two, more where its numbers would read alike, never fewer than the
/// rule's own number was written with, so the reading never rounds onto or past it.
pub(crate) fn reading_digits(f: &Value) -> usize {
    let n = |k: &str| at(f, k).and_then(Value::as_f64).filter(|v| v.is_finite());
    let target = n("check_band.target");
    let vals: Vec<f64> = [n("reading.estimate"), n("reading.lo"), n("reading.hi"), target].into_iter().flatten().collect();
    decimals(&vals).max(target.map_or(0, digits))
}

/// A call's verdict, said once (plain words pass 4): the rule's state word first ("Met", "Not met", "Reproduced",
/// "Not reproduced", "Can't tell yet", "Not tested"), then the reading, then how far it stands from the rule's
/// number. "Not met: step by step is 3.60 points higher than the plain baseline (+0.62 to +6.58), short of the 5
/// the rule asks for." What a rule reads (a gain, a level) is never said as its state.
pub(crate) fn verdict(f: &Value) -> String {
    let state = s(f, "state");
    let word = match state {
        "meets" | "gate met" => "Met",
        "misses" | "gate missed" => "Not met",
        "reproduced" => "Reproduced",
        "not reproduced" => "Not reproduced",
        "undetermined" | "gate undetermined" => "Can't tell yet",
        _ => return "Not tested: nothing is read for this rule yet.".to_string(),
    };
    let met = matches!(state, "meets" | "gate met" | "reproduced");
    let first = s(f, "standing").split(DOT).next().unwrap_or("");
    let on_estimate = first.starts_with("estimate") || first.starts_with("recorded");
    // across fixed design cells: how many fall outside the rule; rb gives no interval across them
    let cells = list(f, "design_cells");
    if !cells.is_empty() {
        let out: Vec<String> = cells.iter().filter(|c| at(c, "outside").and_then(Value::as_bool) == Some(true)).map(|c| cell_said(s(c, "key"))).collect();
        let cn = mark(&cells.len().to_string());
        return match out.len() {
            0 if met => format!("{word} in each of its {cn} design cells."),
            0 => format!("{word}."),
            // met with a cell outside it is met on its estimate alone, and says so: never "Met: 1 of 4 ... outside"
            k => format!(
                "{}: {} of {cn} design cells {} outside the rule ({}).",
                if met && on_estimate { "Met on its estimate" } else { word },
                mark(&k.to_string()),
                if k == 1 { "is" } else { "are" },
                out.join("; ")
            ),
        };
    }
    let Some((said, _)) = reading_parts(f) else { return format!("{word}.") };
    let n = |k: &str| at(f, k).and_then(Value::as_f64).filter(|v| v.is_finite());
    let (est, lo, hi) = (n("reading.estimate"), n("reading.lo"), n("reading.hi"));
    let (cmp, target, tol) = (s(f, "check_band.comparator"), n("check_band.target"), n("check_band.tolerance"));
    let unit = &unit_of(f);
    let compared = compared(f);
    let d = reading_digits(f);
    let kind = rule_kind(f);
    if matches!(state, "undetermined" | "gate undetermined") {
        let units = at(f, "reading.n").and_then(Value::as_i64).unwrap_or(0);
        let u = match s(f, "reading.unit") {
            "" => "unit".to_string(),
            u => clean(u),
        };
        let needs_interval = matches!(kind, Kind::Better | Kind::Equivalent | Kind::WorseEnd | Kind::BothEnds);
        return if needs_interval && lo.is_none() && units > 0 && units < 3 {
            format!("{word}: {said}; the rule reads the interval, which rb gives from 3 {u}s.")
        } else {
            format!("{word}: {said}.")
        };
    }
    // the rule's number as it was declared, with its unit ("the 5 points the rule asks for"); an amount from it in the
    // reading's digits, with its unit
    let the = |t: f64| with_unit(&mark_rule(&fixed(t, digits(t), false)), &unit_word(unit, compared, t.abs()));
    // an amount's unit agrees with the number shown ("1.00 point"), never with digits it dropped
    let amount = |v: f64| {
        let shown = fixed(v.abs(), d, false);
        let as_shown = parsed(&shown).unwrap_or(v.abs());
        with_unit(&mark(&shown), &unit_word(unit, compared, as_shown))
    };
    let of_it = if s(f, "reference_label").starts_with("cited ") { "the cited" } else { "the" };
    // how far a value stands from the rule's number, with whether that side meets it
    let reach = |v: f64| -> Option<(bool, String)> {
        let t = target?;
        let close = fixed(v - t, d, false).trim_start_matches('-').chars().all(|c| c == '0' || c == '.');
        Some(match (cmp, tol) {
            ("at least", _) if v >= t => (true, if close { format!("at the {} it asks for", the(t)) } else { format!("{} past the {} it asks for", amount(v - t), the(t)) }),
            ("at least", _) => (false, format!("short of the {} the rule asks for", the(t))),
            ("at most", _) if v <= t => (true, if close { format!("at the {} it allows", the(t)) } else { format!("{} under the {} it allows", amount(t - v), the(t)) }),
            ("at most", _) => (false, format!("over the {} the rule allows", the(t))),
            ("within" | "equals", Some(e)) => {
                let inside = (v - t).abs() <= e.abs() + 1e-12;
                (inside, format!("{}within {} of {of_it} {}", if inside { "" } else { "not " }, the(e.abs()), the(t)))
            }
            _ => return None,
        })
    };
    let far: Option<(bool, String)> = match kind {
        Kind::Better => lo.zip(hi).map(|_| (met, format!("; its interval is {}clear of 0 on the better side", if met { "" } else { "not " }))),
        Kind::Equivalent => lo.zip(hi).zip(tol).map(|(_, m)| (met, format!("; its interval is {}inside \u{b1}{}", if met { "" } else { "not " }, the(m.abs())))),
        Kind::Each => Some((met, if met { "; every unit's value meets it".to_string() } else { "; a unit's value falls outside it".to_string() })),
        Kind::WorseEnd => (if cmp == "at most" { hi } else { lo }).and_then(|v| reach(v)).map(|(ok, r)| (ok, format!("; its worse end is {r}"))),
        Kind::BothEnds => match (lo, hi) {
            (Some(lo), Some(hi)) => match (reach(lo), reach(hi)) {
                (Some((a, _)), Some((b, r))) if a && b => Some((true, format!("; its whole interval is {r}"))),
                (Some(_), Some(_)) => Some((false, format!("; its interval is not wholly within {} of {of_it} {}", tol.map_or(String::new(), |e| the(e.abs())), target.map_or(String::new(), the)))),
                _ => None,
            },
            _ => None,
        },
        Kind::Estimate => est.and_then(|v| reach(v)).map(|(ok, r)| (ok, format!(", {r}"))),
    };
    // a clause that would say the opposite of the rule's state is left out: the state word stands alone
    let far = far.filter(|(ok, _)| *ok == met).map_or(String::new(), |(_, r)| r);
    // met on its estimate alone; an each rule reads every unit's value, no interval
    let tail = if met && on_estimate && kind != Kind::Each { "; its interval does not show it yet" } else { "" };
    format!("{word}: {said}{far}{tail}.")
}

/// What a rule passes on, in one plain sentence from its band (DESIGN-PASS-1 target 4): "The interval's worse end is
/// at least -1 point (INT8 against the FP32 baseline)." None where the band says nothing rb can word.
fn rule_passes(f: &Value) -> String {
    let n = |k: &str| at(f, k).and_then(Value::as_f64).filter(|v| v.is_finite());
    let unit = &unit_of(f);
    let (subject, reference) = arms(f);
    let reference = reference.as_str();
    let compared = compared(f);
    let tol = n("check_band.tolerance");
    let target = n("check_band.target");
    let cmp = s(f, "check_band.comparator");
    // each number in the digits it was declared with
    let said = |v: f64| with_unit(&fixed(v, digits(v), false), &unit_word(unit, compared, v.abs()));
    let against = if !reference.is_empty() && !reference.starts_with("cited ") { format!(" ({} against {})", said_arm(&subject), the_arm(reference)) } else { String::new() };
    let bound = |t: f64| match (cmp, tol) {
        ("at least", _) | ("at most", _) => format!("{cmp} {}", said(t)),
        ("within", Some(e)) => format!("within {} of {}", said(e.abs()), said(t)),
        _ => String::new(),
    };
    // each kind of rule in the words of what it reads (record.rs's rule_with)
    let s_ = match (rule_kind(f), target) {
        (Kind::Better, _) => "The whole interval is clear of 0 on the better side".to_string(),
        (Kind::Equivalent, _) => match tol {
            Some(m) => format!("The whole interval is inside \u{b1}{}", with_unit(&fixed(m, digits(m), false), &unit_word(unit, compared, m))),
            None => String::new(),
        },
        (Kind::Each, Some(t)) => format!("Every unit's value is {}", bound(t)),
        (Kind::WorseEnd, Some(t)) => format!("The interval's worse end is {}", bound(t)),
        (Kind::BothEnds, Some(t)) => format!("The whole interval is {}", bound(t)),
        (Kind::Estimate, Some(t)) => format!("The estimate is {}", bound(t)),
        _ => String::new(),
    };
    if s_.is_empty() || s_.ends_with("is ") {
        return String::new();
    }
    format!("{s_}{against}.")
}

/// Whether a finding compares its arms as a ratio ("ratio.latency_ms"), which is no difference to say lower or higher.
fn ratio(f: &Value) -> bool {
    s(f, "row.metric").starts_with("ratio.") || s(f, "rule").starts_with("ratio.")
}

/// An arm as a sentence names it after a preposition: "the FP32 baseline", "INT8".
fn the_arm(label: &str) -> String {
    let name = said_arm(label);
    if [" baseline", " control", " ablation"].iter().any(|r| name.ends_with(r)) { format!("the {name}") } else { name }
}

/// An arm as a sentence says it: as arm_name gives it, its underscores as spaces ("step_by_step" reads "step by
/// step").
fn said_arm(label: &str) -> String {
    arm_name(label)
}

/// The two arms a reading compares, as their labels: where a variant is compared with itself, each with the settings
/// its side is selected by ("wide at loops 8", "wide at loops 6"), read from the rule rb words ("[...; subject where
/// loops=8; reference where loops=6]"), as the page's reading names them (FIX3), so the two sides never read as one.
fn arms(f: &Value) -> (String, String) {
    let (subject, reference) = (s(f, "subject_label").to_string(), s(f, "reference_label").to_string());
    if subject.is_empty() || subject != reference {
        return (subject, reference);
    }
    let rule = s(f, "rule");
    // rb's scope is the bracket after the arms, up to the rule's last "]" (a quoted value may hold one)
    let scope = rule.find(" [").and_then(|i| rule.rfind(']').filter(|j| *j > i).map(|j| &rule[i + 2..j])).unwrap_or("");
    let side = |label: &str, bare: bool| {
        scope.split("; ").find_map(|part| part.strip_prefix(label)).map(|sel| {
            sel.split(", ").map(|kv| { let kv = kv.replacen('=', " ", 1); if bare { kv.replace('"', "") } else { kv } }).collect::<Vec<_>>().join(" and ")
        })
    };
    // a text value's quotes are dropped unless they are what tells the two sides apart ("1" and 1)
    let sides = [true, false].into_iter().map(|bare| (side("subject where ", bare), side("reference where ", bare))).find(|(a, b)| a.is_some() && a != b);
    match sides {
        Some((Some(a), Some(b))) => (format!("{subject} at {a}"), format!("{reference} at {b}")),
        Some((Some(a), None)) => (format!("{subject} at {a}"), reference),
        _ => (subject, reference),
    }
}

/// The metric a finding reads, by its name: "change.top1" and "fp32.top1" read "top1"; a cited reading's "top1: ..."
/// its name alone.
fn metric_name(f: &Value) -> &str {
    let m = s(f, "row.metric").split(':').next().unwrap_or("").trim();
    m.rsplit('.').next().unwrap_or(m)
}

/// A metric as a sentence names it where no arm is given (FIX3): by its title, as the page's grid says it, never its
/// key: "top1" reads "Top-1", "share_under_2pt" "Share under 2pt"; an acronym keeps its case (FIX5): "onnx_top1" reads
/// "ONNX top-1", "int8_top1" "INT8 top-1".
fn metric_said(name: &str) -> String {
    let topn = |t: &str| t.strip_prefix("top").filter(|d| !d.is_empty() && d.chars().all(|c| c.is_ascii_digit())).map(|d| format!("top-{d}"));
    let word = |w: &str| {
        let low = w.to_lowercase();
        if let Some(t) = topn(&low) {
            return t;
        }
        let format = arm_name(&low);
        if format != low || ACRONYMS.contains(&low.as_str()) { low.to_uppercase() } else { w.to_string() }
    };
    let words = name.replace(['.', '_'], " ").split(' ').filter(|w| !w.is_empty()).map(word).collect::<Vec<_>>().join(" ");
    let mut c = words.chars();
    c.next().map_or(String::new(), |h| h.to_uppercase().chain(c).collect())
}

/// The words a metric's name holds that prose writes in capitals (FIX5).
const ACRONYMS: &[&str] = &[
    "onnx", "gpu", "cpu", "tpu", "auc", "roc", "mse", "rmse", "mae", "iou", "bleu", "rouge", "wer", "cer", "ece", "fid", "psnr", "ssim", "nll", "kl", "ema",
    "mfu", "flops", "f1", "ndcg", "mrr", "llm", "ttft", "qps", "rps",
];

/// The decimals the page's grid says a metric's means in (R's views.rs places, design pass 1, P5), so a variant's card
/// and the grid write one mean one way (FIX5): two, or two significant digits of the largest gap between a variant and
/// the baseline in the same cell where every gap is under 0.1 and the unit is no percentage (a top-1 of 0.7568 against
/// 0.7597).
fn grid_places(rows: &[Value], metric: &str, unit: &str) -> usize {
    let mean = |r: &Value| r.get("metrics").and_then(|ms| ms.get(metric)).and_then(|c| at(c, "mean")).and_then(Value::as_f64);
    let cell = |r: &Value| at(r, "where").map(|w| format!("{w:?}")).unwrap_or_default();
    let mut gaps: Vec<f64> = Vec::new();
    for base in rows.iter().filter(|r| s(r, "role") == "baseline") {
        let Some(b) = mean(base) else { continue };
        for r in rows.iter().filter(|r| s(r, "variant") != s(base, "variant") && cell(r) == cell(base)) {
            if let Some(x) = mean(r).filter(|x| *x != b) {
                gaps.push(x - b);
            }
        }
    }
    let m = gaps.iter().map(|x| x.abs()).fold(0.0_f64, f64::max);
    if unit == "%" || m >= 0.1 || m == 0.0 || !m.is_finite() {
        return 2;
    }
    ((1.0 - m.log10().floor()) as usize).clamp(2, 6)
}

/// A metric as a table heads it (FIX5): its title, and the unit its values are said in; a name that ends in its unit
/// says it once ("latency_ms" in ms heads "Latency"), a percentage by any name is "%", a unit that is the metric's own
/// name is none.
fn metric_head(name: &str, unit: &str) -> (String, String) {
    let unit = clean(unit);
    let unit = if matches!(unit.to_ascii_lowercase().as_str(), "percent" | "per cent" | "pct" | "percentage" | "percentages") { "%".to_string() } else { unit };
    let last = name.rsplit('.').next().unwrap_or(name);
    let unit = if unit == name || unit == last { String::new() } else { unit };
    let base = name.strip_suffix(&format!("_{unit}")).filter(|b| !b.is_empty() && !unit.is_empty()).unwrap_or(name);
    (metric_said(base), unit)
}

/// A finding's unit as a sentence says it (FIX3, as the page says units): a percentage by any of its names is "%"
/// ("0.81%", never "0.81 percent"); a unit that is the metric's own name ("top1" for top1), or a ratio's, is none.
fn unit_of(f: &Value) -> String {
    let u = clean(s(f, "reading.unit_text"));
    let u = u.trim();
    if matches!(u.to_ascii_lowercase().as_str(), "%" | "percent" | "per cent" | "pct" | "percentage" | "percentages") {
        return "%".into();
    }
    // a ratio of two arms has no unit of its own (model's unit_said says none)
    if u == metric_name(f) || ratio(f) { String::new() } else { u.to_string() }
}

/// The digits a declared number was written with ("0.01" has 2, "1" none), at most 6.
pub(crate) fn digits(v: f64) -> usize {
    let t = format!("{v}");
    t.split_once('.').map_or(0, |(_, d)| d.len()).min(6)
}

/// Whether a word of a receipt row is a hash, a path or a file (P4: monospace only for ids, commands, paths and
/// hashes): seven or more hex digits with a digit and a letter among them, or a word with a "/" or a file's extension.
fn machine_word(word: &str) -> bool {
    let w = word.trim_end_matches([',', ';', ':', ')', '.']).trim_start_matches('(');
    if w.len() < 3 {
        return false;
    }
    let hex = w.len() >= 7 && w.chars().all(|c| c.is_ascii_hexdigit()) && w.chars().any(|c| c.is_ascii_digit()) && w.chars().any(|c| c.is_ascii_alphabetic());
    let path = w.contains('/') && w.chars().any(|c| c.is_ascii_alphabetic()) && !w.contains("//") || w.starts_with("./");
    let file = ["json", "yaml", "yml", "toml", "py", "log", "txt", "csv", "db", "pt", "md", "sh", "ipynb", "parquet", "npz", "ckpt", "safetensors"]
        .iter()
        .any(|e| w.rsplit_once('.').map_or(false, |(stem, ext)| !stem.is_empty() && ext == *e));
    hex || path || file || w.starts_with("sha256:")
}

/// A row's markup with each machine word of its text in a monospace span; tags, and the text inside a tag, untouched.
fn mono_tokens(html: &str) -> String {
    let mut out = String::with_capacity(html.len() + 32);
    let mut rest = html;
    while !rest.is_empty() {
        if rest.starts_with('<') {
            let end = rest.find('>').map_or(rest.len(), |i| i + 1);
            out += &rest[..end];
            rest = &rest[end..];
            continue;
        }
        let end = rest.find('<').unwrap_or(rest.len());
        let text = &rest[..end];
        let mut word = String::new();
        let flush = |word: &mut String, out: &mut String| {
            if !word.is_empty() {
                // the trailing punctuation stays outside the span
                let core = word.trim_end_matches([',', ';', ')']).to_string();
                if machine_word(&core) {
                    *out += &format!("<span class=\"mono\">{core}</span>{}", &word[core.len()..]);
                } else {
                    *out += word;
                }
                word.clear();
            }
        };
        for c in text.chars() {
            if c == ' ' {
                flush(&mut word, &mut out);
                out.push(c);
            } else {
                word.push(c);
            }
        }
        flush(&mut word, &mut out);
        rest = &rest[end..];
    }
    out
}

/// A design cell's key in words ("calib_images=512 · scheme=per_channel" reads "calib images 512, per-channel").
pub(crate) fn cell_said(key: &str) -> String {
    key.split(DOT).map(|kv| kv.split_once('=').map_or(kv.to_string(), |(k, v)| setting_said(k, v))).collect::<Vec<_>>().join(", ")
}

/// Numbers rb wrote, in two decimals (P5) said alike across the set, as plain text: for the figure's words.
pub(crate) fn two_decimals(texts: &[&str], signed: bool) -> Vec<String> {
    let d = decimals(&texts.iter().filter_map(|t| parsed(t)).collect::<Vec<_>>());
    texts.iter().map(|t| parsed(t).map_or_else(|| t.to_string(), |v| fixed(v, d, signed))).collect()
}

/// A number in a sentence, marked so the sentence drills from it (the drill law) and nowhere else is in link colour
/// (P7): `sentence_html` makes it a link, `sentence_text` leaves the number.
fn mark(n: &str) -> String {
    format!("\u{1}{n}\u{2}")
}

/// The rule's own number in a sentence, marked so it drills to the rule (`sentence_html_to`'s second anchor).
fn mark_rule(n: &str) -> String {
    format!("\u{3}{n}\u{4}")
}

/// A sentence with its marked numbers as links to `href`, the rest escaped text.
fn sentence_html(text: &str, href: &str) -> String {
    sentence_html_to(text, href, href)
}

/// A sentence with its marked numbers as links: the reading's to `href`, the rule's own (mark_rule) to `rule`.
fn sentence_html_to(text: &str, href: &str, rule: &str) -> String {
    // a marker the record's own text could hold is taken out where sentences are composed (clean); a sentence whose
    // markers still do not pair is drawn with none
    let pairs = |o: char, c: char| text.split(o).skip(1).all(|p| p.matches(c).count() == 1) && text.matches(o).count() == text.matches(c).count();
    if !pairs('\u{1}', '\u{2}') || !pairs('\u{3}', '\u{4}') {
        return esc(&clean(text));
    }
    esc(text)
        .replace('\u{1}', &format!("<a href=\"#{}\">", esc(href)))
        .replace('\u{3}', &format!("<a href=\"#{}\">", esc(rule)))
        .replace(['\u{2}', '\u{4}'], "</a>")
}

/// A sentence as plain text, its numbers unmarked: for a description, an aria-label.
pub(crate) fn sentence_text(text: &str) -> String {
    text.replace(['\u{1}', '\u{2}', '\u{3}', '\u{4}'], "")
}

/// When a rule was written against its results, in plain words, from rb's timing line (its lead): the rest of rb's
/// line stays in Details.
fn timing_said(timing: &str) -> &'static str {
    if timing.starts_with("declared before") {
        "Written before its results."
    } else if timing.starts_with("declared after the data") {
        "Written after the data was seen."
    } else if timing.starts_with("declared after") {
        "Written after its results came in."
    } else if timing.is_empty() {
        ""
    } else if timing.starts_with("order unknown") {
        "rb can't tell whether it was written before its results."
    } else {
        // rb's "no evidence recorded yet": nothing to be before or after
        "No results yet."
    }
}

/// rb's own words for a reading and its rule, as rb gives them (P8): rows of the card's Source details, never primary
/// text. A row with nothing to say is not drawn.
fn raw_rows(lines: &[(&str, String)]) -> String {
    lines.iter().filter(|(_, v)| !v.is_empty()).map(|(k, v)| format!("<dt>{}</dt><dd>{v}</dd>", esc(k))).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn example(name: &str) -> Value {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("../tests/fixtures/shell/int8-demo.{name}.view.json"));
        crate::pyjson::loads(&std::fs::read_to_string(path).unwrap()).unwrap()
    }

    /// `html` as `viewer` ("none", "owner" or "reader") reads the export (OWNER-READER): every element marked for other
    /// viewers (`data-vw`) cut out whole, as shell.css hides it.
    fn as_viewer(html: &str, viewer: &str) -> String {
        let mut out = html.to_string();
        let mut from = 0;
        while let Some(k) = out[from..].find(" data-vw=\"").map(|k| k + from) {
            let names = &out[k + 10..k + 10 + out[k + 10..].find('"').unwrap()];
            if names.split(' ').any(|n| n == viewer) {
                from = k + 10;
                continue;
            }
            let start = out[..k].rfind('<').unwrap();
            let tag: String = out[start + 1..].chars().take_while(|c| c.is_ascii_alphanumeric()).collect();
            let (open, close) = (format!("<{tag}"), format!("</{tag}>"));
            let (mut depth, mut i) = (0usize, start);
            let end = loop {
                let next_close = out[i..].find(&close).unwrap() + i;
                match out[i..].find(&open).map(|o| o + i) {
                    Some(o) if o < next_close => {
                        depth += 1;
                        i = o + open.len();
                    }
                    _ => {
                        depth -= 1;
                        i = next_close + close.len();
                        if depth == 0 {
                            break i;
                        }
                    }
                }
            };
            out.replace_range(start..end, "");
            from = start;
        }
        out
    }

    /// The value at a path of dicts, to change in a test.
    fn dig<'a>(v: &'a mut Value, path: &[&str]) -> &'a mut Value {
        path.iter().fold(v, |v, k| v.as_dict_mut().unwrap().get_mut(*k).unwrap())
    }

    /// The text a reader sees of `html`: tags out, rb's five escapes read back.
    fn text_of(html: &str) -> String {
        let mut out = String::new();
        let mut inside = false;
        for c in html.chars() {
            match c {
                '<' => inside = true,
                '>' => inside = false,
                c if !inside => out.push(c),
                _ => {}
            }
        }
        out.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&#x27;", "'").replace("&amp;", "&")
    }

    /// The card at `id` in `page`, whole: from its opening tag to the end tag that closes it.
    fn card<'a>(page: &'a str, id: &str) -> &'a str {
        let at = page.find(&format!(" id=\"{id}\"")).unwrap_or_else(|| panic!("no #{id}"));
        let start = page[..at].rfind('<').unwrap();
        let tag: String = page[start + 1..].chars().take_while(|c| c.is_ascii_alphanumeric()).collect();
        let (open, close) = (format!("<{tag}"), format!("</{tag}>"));
        let (mut depth, mut i) = (0usize, start);
        loop {
            let next_open = page[i..].find(&open).map(|k| k + i);
            let next_close = page[i..].find(&close).map(|k| k + i).unwrap();
            match next_open {
                Some(o) if o < next_close => {
                    depth += 1;
                    i = o + open.len();
                }
                _ => {
                    depth -= 1;
                    i = next_close + close.len();
                    if depth == 0 {
                        return &page[start..i];
                    }
                }
            }
        }
    }

    /// The text of `html` that sits outside every link.
    fn unlinked(html: &str) -> String {
        let mut out = String::new();
        let mut depth = 0usize;
        let mut rest = html;
        while let Some(i) = rest.find('<') {
            if depth == 0 {
                out += &rest[..i];
            }
            let end = rest[i..].find('>').unwrap() + i;
            let tag = &rest[i..=end];
            if tag.starts_with("<a ") || tag == "<a>" {
                depth += 1;
            } else if tag == "</a>" {
                depth -= 1;
            }
            rest = &rest[end + 1..];
        }
        out + rest
    }

    fn anchors(doc: &Value) -> Vec<String> {
        let mut want: Vec<String> = Vec::new();
        want.extend(entries(doc, "findings").iter().map(|(c, _)| finding_anchor(c)));
        want.extend(entries(doc, "runs").iter().map(|(r, _)| run_anchor(r)));
        want.extend(entries(doc, "resources").iter().map(|(r, _)| resource_anchor(r)));
        want.extend(entries(doc, "analyses").iter().map(|(a, _)| analysis_anchor(a)));
        want.extend(list(doc, "calls").iter().map(|c| call_anchor(s(c, "subject"))));
        // each citation the document resolved has its Source view (CITE item 3)
        want.extend(entries(doc, "citations").iter().map(|(k, _)| source_anchor(k)));
        want
    }

    /// Every object of the document has its card, at its anchor, once: findings, runs, resources, analyses, calls,
    /// qualifications, the reading groups and the variants; and every card carries what the inspector reads of it.
    #[test]
    fn every_object_has_its_card_once() {
        for (name, surface) in [("owner", Surface::Page), ("owner", Surface::Export), ("live", Surface::Open)] {
            let doc = example(name);
            let page = all(&doc, surface);
            let mut want = anchors(&doc);
            want.extend(["x-e1:v:fp32", "x-e1:v:int8", "x-e2:v:fp32", "x-e2:v:int8"].map(String::from));
            for id in &want {
                assert_eq!(page.matches(&format!(" id=\"{id}\"")).count(), 1, "{name} {surface:?}: #{id}");
                let c = card(&page, id);
                assert!(c.contains(" data-home=\"") && c.contains(" data-crumb=\"") && c.contains("class=\"card"), "{id}: {}", &c[..120]);
            }
            assert_eq!(page.matches("class=\"card").count(), want.len(), "{name}: a card no object asks for");
            // a question's qualifications and reading groups live in its view's folds: the region leaves them to the
            // view, and the inspector gets each whole by its anchor
            let mut folded: Vec<String> = ["q-q1:rd:criteria-met", "q-q1:rd:not-reproduced", "q-q1a:rd:criteria-not-met"].map(String::from).to_vec();
            for q in list(&doc, "questions") {
                folded.extend((0..list(q, "overview.limits").len()).map(|i| qualification_anchor(s(q, "id"), i)));
            }
            assert!(folded.len() > 3);
            for id in &folded {
                assert!(!page.contains(&format!(" id=\"{id}\"")), "{name} {surface:?}: #{id} is the view's");
                let c = one(&doc, surface, id).unwrap_or_else(|| panic!("{id}"));
                assert!(c.starts_with("<div class=\"card lim\"") && c.contains(&format!(" id=\"{id}\"")), "{id}");
            }
            assert!(!page.contains("<details id") && !page.contains("<details class=\"rc\" id"), "an id on a fold");
            // every fold is keyed (data-fold), each key once, so rb open's live module keeps it open across a swap
            let keys: Vec<&str> = page
                .split("<details")
                .skip(1)
                .map(|t| t[..t.find('>').unwrap()].split(" data-fold=\"").nth(1).map_or("", |k| &k[..k.find('"').unwrap()]))
                .collect();
            assert!(!keys.is_empty() && keys.iter().all(|k| !k.is_empty()), "{name}: a fold with no key");
            assert_eq!(keys.iter().collect::<std::collections::HashSet<_>>().len(), keys.len(), "{name}: a key twice");
        }
    }

    /// The levels (OVERVIEW-V2): a finding card says what it is and its standing (Understand), then the reading, the
    /// declared criterion, what it rests on and what rests on it, unfolded (Examine); then "Source details", folded,
    /// the receipts' id on the list inside it; then Decide where a call waits. A run card says what it is, then what
    /// it counts toward and its results, then its command and receipts in Source details, then Reproduce, folded.
    #[test]
    fn a_card_keeps_its_levels() {
        let doc = example("owner");
        let page = all(&doc, Surface::Page);
        let c1 = card(&page, "c-c1");
        let order: Vec<usize> = [
            "data-level=\"understand\"",
            "class=\"stand\"",
            "data-level=\"examine\"",
            "id=\"c-c1:reading\"",
            "id=\"c-c1:rule\"",
            "id=\"c-c1:runs\"",
            "id=\"c-c1:impact\"",
            "<details class=\"fold lv src\" data-level=\"trace\" data-fold=\"c-c1:source\"><summary>Source details</summary>",
            "id=\"c-c1:receipts\"",
        ]
        .iter()
        .map(|p| c1.find(p).unwrap_or_else(|| panic!("{p}")))
        .collect();
        assert!(order.windows(2).all(|w| w[0] < w[1]), "{order:?}");
        assert!(!c1[..order[7]].contains("<details"), "Understand and Examine hold a fold");
        assert!(!c1.contains("data-level=\"decide\""), "no call waits on c1");
        // a fold is never drawn open: the inspector, a page and rb open's swap leave Source details shut
        assert!(!page.contains(" open>") && !page.contains(" open=\""));
        let runs1 = card(&page, "run-runs1");
        let order: Vec<usize> = [
            "class=\"tt\"",
            "data-level=\"examine\"",
            "id=\"run-runs1:counts\"",
            "id=\"run-runs1:results\"",
            "<details class=\"fold lv src\" data-level=\"trace\" data-fold=\"run-runs1:source\"><summary>Source details</summary>",
            "id=\"run-runs1:command\"",
            "id=\"run-runs1:receipts\"",
            "<details class=\"fold lv repro\" data-level=\"trace\" data-fold=\"run-runs1:reproduce\"><summary>Reproduce this run",
            "id=\"run-runs1:reproduce\"",
        ]
        .iter()
        .map(|p| runs1.find(p).unwrap_or_else(|| panic!("{p}")))
        .collect();
        assert!(order.windows(2).all(|w| w[0] < w[1]), "{order:?}");
        assert!(!runs1[..order[4]].contains("<details"));
        // the file the results were read from, and its hash, are source details: never in Examine
        let examine = &runs1[order[1]..order[4]];
        assert!(!examine.contains("results/seed1/metrics.json") && !examine.contains("sha256"), "{examine}");
        let src = order[4];
        // each receipt row rb gives is drawn in the receipts, the ids it names linked; but the command, which the card
        // says above it with how rb knows it
        for row in list(at(&doc, "runs.runs1").unwrap(), "receipt_rows") {
            let (field, text) = (row.as_list().unwrap()[0].as_str().unwrap(), row.as_list().unwrap()[1].as_str().unwrap());
            if field == "command" {
                assert!(!runs1[src..].contains("<dt>command</dt>"));
                continue;
            }
            // the per-run values are the title's: not a row again (DESIGN-PASS-1 target 7); a run row that says when it
            // was recorded is drawn
            if field == "run" && text.contains('=') {
                assert!(!runs1[src..].contains("<dt>run</dt>"), "the run's values said twice");
                continue;
            }
            assert!(text_of(&runs1[src..]).contains(text), "{text}");
        }
        assert!(runs1[src..].contains("<a href=\"#r-ckpt-int8-s1\">ckpt-int8-s1</a>"));
        for row in list(at(&doc, "findings.c1").unwrap(), "receipt_rows") {
            assert!(text_of(c1).contains(row.as_list().unwrap()[1].as_str().unwrap()));
        }
    }

    /// The drill law: in a card's bands (above its receipts) no id the document holds, and no reading's number, is
    /// drawn outside a link, but for the card's own id; the standing words link to the rule.
    #[test]
    fn numbers_and_standing_drill() {
        let doc = example("owner");
        let page = all(&doc, Surface::Page);
        let mut ids: Vec<String> = Vec::new();
        for key in ["findings", "runs", "resources", "analyses"] {
            ids.extend(entries(&doc, key).iter().map(|(k, _)| k.to_string()));
        }
        let numbers: Vec<String> = entries(&doc, "findings")
            .iter()
            .flat_map(|(_, f)| [s(f, "reading.estimate_text").to_string(), s(f, "units_line").to_string()])
            .filter(|t| !t.is_empty())
            .collect();
        let cards: Vec<String> = anchors(&doc).into_iter().filter(|a| !a.starts_with("q-")).collect();
        for anchor in &cards {
            let c = card(&page, anchor);
            let own = anchor.split_once('-').unwrap().1;
            // Understand and Examine: everything above the Source details fold
            let bands = &c[..c.find("data-level=\"trace\"").map_or(c.len(), |i| c[..i].rfind('<').unwrap())];
            // author text is the author's: its words are not rb's to link
            let bands = bands.split("<span class=\"au\">").map(|p| p.split_once("</span>").map_or(p, |(_, rest)| rest)).collect::<String>();
            // a fold's summary is its control (the count it shows is linked in the line above it)
            let bands = bands.split("<summary>").map(|p| p.split_once("</summary>").map_or(p, |(_, rest)| rest)).collect::<String>();
            // the figure is geometry: its numbers are its marks' titles, beside the linked reading
            let bands = bands.split("<svg").map(|p| p.split_once("</svg>").map_or(p, |(_, rest)| rest)).collect::<String>();
            // a command to copy is a payload, never a link
            let bands = bands.split("<pre").map(|p| p.split_once("</pre>").map_or(p, |(_, rest)| rest)).collect::<String>();
            let loose = text_of(&unlinked(&bands));
            for id in ids.iter().filter(|i| *i != own) {
                assert!(whole_word(&loose, id).is_none(), "{anchor}: {id} unlinked in {loose:?}");
            }
            // a run's own results are the leaf the readings drill to; a reading's number elsewhere drills
            for n in numbers.iter().filter(|_| !anchor.starts_with("run-")) {
                assert!(!loose.contains(n.as_str()), "{anchor}: {n} unlinked");
            }
        }
        let c1 = card(&page, "c-c1");
        // the verdict leads with the state word; its numbers drill to the runs (FIX2: no state words after it)
        assert!(c1.contains("<p class=\"str\">Met: ") && level(c1, "understand").contains("href=\"#c-c1:runs\""), "{}", &c1[..1200]);
        assert!(card(&page, "c-c2").contains("<p class=\"str\">Not reproduced: "));
    }

    /// A page (rb page, rb export) draws no place and no platform, whatever document it is handed; rb open draws both.
    #[test]
    fn live_only_stays_on_rb_open() {
        let live = example("live");
        for surface in [Surface::Page, Surface::Export] {
            let page = all(&live, surface);
            for leak in ["exp-b", "Linux-6.8.0", "data-place", ".worktrees"] {
                assert!(!page.contains(leak), "{surface:?}: {leak}");
            }
        }
        let open = all(&live, Surface::Open);
        assert!(card(&open, "run-runs3").contains("<a href=\"#run-runs3&amp;place=exp-b\" data-place=\"exp-b\" data-at=\"\">"));
        assert!(card(&open, "c-c1").contains("data-place=\"exp-b\">exp-b \u{b7} .worktrees/b</a>"));
        assert!(card(&open, "run-runs1").contains("Linux-6.8.0-x86_64"));
    }

    /// A run's place as A's live module reads it (BP 4b): a link wherever the document gives `recorded_in`, the place rb
    /// open started on included (an empty `data-place`), with an empty `data-at`; where it gives none, the place in
    /// plain words; on a page, neither.
    #[test]
    fn a_run_says_where_it_was_recorded_as_a_link_the_live_module_routes() {
        let mut live = example("live");
        let start = crate::obj! {"place" => "", "label" => "main \u{b7} ~/int8-resnet50", "href" => "#run-runs1"};
        if let Some(r) = live.as_dict_mut().and_then(|d| d.get_mut("runs")).and_then(Value::as_dict_mut).and_then(|rs| rs.get_mut("runs1")).and_then(Value::as_dict_mut) {
            r.insert("recorded_in".into(), start);
        }
        let open = all(&live, Surface::Open);
        assert!(card(&open, "run-runs1").contains("<a href=\"#run-runs1\" data-place=\"\" data-at=\"\">main \u{b7} ~/int8-resnet50</a>"));
        if let Some(r) = live.as_dict_mut().and_then(|d| d.get_mut("runs")).and_then(Value::as_dict_mut).and_then(|rs| rs.get_mut("runs3")).and_then(Value::as_dict_mut) {
            r.insert("recorded_in".into(), Value::Null);
        }
        let open = all(&live, Surface::Open);
        let runs3 = card(&open, "run-runs3");
        assert!(runs3.contains("<p class=\"sm obs-in\">recorded in exp-b \u{b7} .worktrees/b at 5d1e0a7</p>") && !runs3.contains("data-place"), "{runs3}");
        assert!(!all(&live, Surface::Page).contains("data-place"));
    }

    /// The analysis card: its title and when it was recorded, the runs it reads, what it reports and the criteria
    /// that rest on it, each linked.
    #[test]
    fn an_analysis_has_a_card() {
        let doc = example("owner");
        let a1 = card(&all(&doc, Surface::Page), "a-a1").to_string();
        for want in [
            "per-class accuracy, int8 against fp32",
            "analysis \u{b7} recorded 2026-10-13 14:56 UTC \u{b7} basis per-class top1 over 1000 classes",
            "analysis/per_class.py",
            "61be0c94d7a2",
        ] {
            assert!(text_of(&a1).contains(want), "{want}");
        }
        for run in ["runs1", "runs2b", "runs3"] {
            // linked by its name, never by the id rb minted (FIX7)
            assert!(a1.contains(&format!("<a href=\"#run-{run}\">")) && !text_of(&a1).contains(run), "{run}");
        }
        assert!(a1.contains("href=\"#c-c4\"") && a1.contains("<span class=\"e\">0.994</span>"));
        assert!(a1.contains("data-home=\"x-e1\""));
        // the finding it backs says where its reading comes from
        assert!(card(&all(&doc, Surface::Page), "c-c4").contains("the reading comes from the analysis <a href=\"#a-a1\">per-class accuracy, int8 against fp32</a>"));
    }

    /// An analysis with no title is "an analysis" where a sentence names it, never "the analysis an analysis", and a
    /// line rb gives it ("95% interval") gets no "· an analysis" after it: the line itself links to it (FIX8). A list of
    /// runs whose names hold commas ("FP32 and INT8, seed 1") is said with semicolons, so it reads one way only.
    #[test]
    fn an_untitled_analysis_and_runs_named_with_commas_read_one_way() {
        let mut doc = example("owner");
        dig(&mut doc, &["analyses", "a1"]).as_dict_mut().unwrap().insert("title".into(), Value::str(""));
        // the method row as model.rs gives it now: rb's line, the analysis in its refs and not in its words
        dig(&mut doc, &["findings", "c4", "receipt_rows"]).as_list_mut().unwrap()[4].as_list_mut().unwrap()[1] = Value::str("95% interval");
        let page = all(&doc, Surface::Page);
        let c4 = card(&page, "c-c4");
        let said = text_of(c4);
        assert!(said.contains("the reading comes from an analysis") && !said.contains("the analysis an analysis"), "{said}");
        assert!(c4.contains("the reading comes from <a href=\"#a-a1\">an analysis</a>"), "{c4}");
        assert!(!said.contains("\u{b7} an analysis") && !said.contains("interval, an analysis"), "{said}");
        assert!(c4.contains("<a href=\"#a-a1\">95% interval</a>"), "{c4}");
        // with runs in its refs too: the line links to the analysis, the runs follow, said with semicolons
        let mut doc2 = doc.clone();
        let row = &mut dig(&mut doc2, &["findings", "c4", "receipt_rows"]).as_list_mut().unwrap()[4];
        row.as_list_mut().unwrap()[2] = Value::List(vec![Value::str("a1"), Value::str("runs1"), Value::str("runs2")]);
        let c4b = card(&all(&doc2, Surface::Page), "c-c4").to_string();
        assert!(c4b.contains("<a href=\"#a-a1\">95% interval</a><span> \u{b7} </span><a href=\"#run-runs1\">FP32 and INT8, seed 1</a><span>; </span><a href=\"#run-runs2\">FP32 and INT8, seed 2, recorded 2026-10-06 10:32 UTC</a>"), "{c4b}");
        assert!(!text_of(&c4b).contains("\u{b7} an analysis") && !text_of(&c4b).contains("seed 2, an analysis"), "{}", text_of(&c4b));
        // a line that names a run it links where it says it, the untitled analysis after it as "an analysis"
        assert_eq!(Doc::new(&doc2, Surface::Page).with_refs("same as runs1", &["runs1", "a1"]), "<span>same as </span><a href=\"#run-runs1\">runs1</a><span> \u{b7} </span><a href=\"#a-a1\">an analysis</a>");
        // the analysis's reads: three runs, each a name with a comma in it
        assert!(said.contains("FP32 and INT8, seed 1; FP32 and INT8, seed 2, recorded 2026-10-13 14:12 UTC; FP32 and INT8, seed 3"), "{said}");
        // where the analysis is in a list, it is "an analysis" too
        assert!(!text_of(&page).contains("the analysis an analysis"));
        // "same bytes as" two runs whose names hold commas
        let mut doc3 = example("owner");
        let sb = dig(&mut doc3, &["runs", "runc512", "same_bytes"]).as_list_mut().unwrap();
        let mut second = sb[0].clone();
        second.as_dict_mut().unwrap().insert("run".into(), Value::str("runs2"));
        sb.push(second);
        let runc512 = card(&all(&doc3, Surface::Page), "run-runc512").to_string();
        assert!(runc512.contains("<a href=\"#run-runs1\">FP32 and INT8, seed 1</a> (e1); <a href=\"#run-runs2\">FP32 and INT8, seed 2, recorded 2026-10-06 10:32 UTC</a> (e1)"), "{runc512}");
        // names with no comma keep commas between them
        assert_eq!(listed(&["seed 1".into(), "seed 2".into()], vec!["a".into(), "b".into()]), "a, b");
        assert_eq!(listed(&["x, seed 1".into(), "seed 2".into()], vec!["a".into(), "b".into()]), "a; b");
    }

    /// The call card: why rb asks, once, what rb read, and each action with its line and what the review will read;
    /// the composer's buttons and field only where a script can drive them for the record's owner: rb page, and the
    /// hub's working view of an export (OWNER-READER), never an export with no viewer named or a reader's.
    #[test]
    fn a_call_card_says_why_once_and_offers_each_action() {
        let doc = example("owner");
        let export = all(&doc, Surface::Export);
        for (surface, page) in [(Surface::Page, all(&doc, Surface::Page)), (Surface::Export, as_viewer(&export, "none")), (Surface::Export, as_viewer(&export, "owner"))] {
            let owner = surface == Surface::Page || page != as_viewer(&export, "none");
            let c2 = card(&page, "call-c2");
            // once: rb's item line ("c2: estimate ..") is the reason's detail under the subject's id, so it is not drawn
            assert_eq!(text_of(c2).to_lowercase().matches("not reproduced").count(), 1, "the verdict said twice");
            // the verdict is said once, under the title; the reasons it says again, and the title again, are not drawn
            assert_eq!(c2.matches("<p class=\"verdict\">").count(), 1);
            assert!(!text_of(c2).contains("Why this needs a call") && !text_of(c2).contains("What rb read") && !text_of(c2).contains("Review this result"), "{c2}");
            assert!(text_of(card(&page, "call-c3")).contains("Too little evidence"), "a reason the verdict does not say stays");
            for c in list(&doc, "calls") {
                let cc = card(&page, &call_anchor(s(c, "subject")));
                for a in list(c, "actions") {
                    assert!(text_of(cc).contains(s(a, "line")) && text_of(cc).contains(s(a, "consequence")), "{}", s(a, "line"));
                    assert!(cc.contains(&format!("data-cons=\"{}\"", esc(s(a, "consequence")))));
                }
            }
            assert_eq!(page.contains("<button") || page.contains("<input"), owner, "{surface:?}");
        }
        // a reader of a shared link reads why each call is open and what rb read, and is offered no action: no button,
        // no reason field, no rb command, no Record a call
        let reader = as_viewer(&export, "reader");
        for c in list(&doc, "calls") {
            let cc = card(&reader, &call_anchor(s(c, "subject")));
            assert!(cc.contains("<p class=\"verdict\">") && text_of(cc).contains("Still open"), "{}", s(c, "subject"));
            for a in list(c, "actions") {
                assert!(!text_of(cc).contains(s(a, "line")), "{}", s(a, "line"));
            }
        }
        assert!(!reader.contains("<button") && !reader.contains("<input") && !reader.contains("Record a call") && !text_of(&reader).contains("rb decide"));
        // a call on an experiment reads its gate
        let e2 = card(&all(&doc, Surface::Page), "call-e2").to_string();
        assert!(text_of(&e2).contains("Not met: 1 of 4 design cells is outside the rule") && e2.contains("gate <a class=\"mono id\" href=\"#c-c5\">c5</a>"), "{e2}");
        assert!(e2.contains("From <a href=\"#c-c5:runs\">4 runs</a>"), "{e2}");
    }

    /// An export cannot know who reads it and its readers make no calls: its cards say Open calls and an open call,
    /// and no "you" or "your" about a call; rb page and rb open, read by the record's owner, keep Your call.
    #[test]
    fn an_export_says_open_calls_and_never_your_call() {
        let doc = example("owner");
        let html = all(&doc, Surface::Export);
        // with no viewer named, and for a reader of a shared link: Open calls, and no "you" about a call
        for viewer in ["none", "reader"] {
            let export = text_of(&as_viewer(&html, viewer)).to_lowercase();
            for said in ["your call", "waits on you", "your terminal", "your reason", "you make", "owner", "their own terminal"] {
                assert!(!export.contains(said), "{viewer}: {said}");
            }
            let c2 = text_of(card(&as_viewer(&html, viewer), "call-c2"));
            let eyebrow = if viewer == "none" { "Open call \u{b7} c2" } else { "Still open \u{b7} c2" };
            assert!(c2.starts_with("Open calls") && c2.contains(eyebrow), "{viewer}: {c2}");
            assert_eq!(c2.contains("It is run with rb in a terminal; rb records the call and the reason."), viewer == "none", "{viewer}: {c2}");
        }
        // the owner signed in on the hub reads the working view in rb open's words (OWNER-READER)
        let c2 = text_of(card(&as_viewer(&html, "owner"), "call-c2"));
        assert!(c2.starts_with("Your call") && c2.contains("Your call \u{b7} c2") && c2.contains("Run it in your terminal"), "owner: {c2}");
        for surface in [Surface::Page, Surface::Open] {
            let page = all(&doc, surface);
            let c2 = text_of(card(&page, "call-c2"));
            assert!(c2.starts_with("Your call") && c2.contains("Your call \u{b7} c2") && c2.contains("Run it in your terminal"), "{surface:?} {c2}");
        }
        // the address stays #your-call everywhere, so a link made before keeps landing
        assert!(card(&all(&doc, Surface::Export), "call-c2").contains("href=\"#your-call\""));
    }

    /// A retracted run says so and why, counts toward nothing and offers no recipe; its replacement does.
    #[test]
    fn a_retracted_run_counts_toward_nothing() {
        let page = all(&example("owner"), Surface::Page);
        let runs2 = card(&page, "run-runs2");
        assert!(runs2.contains("class=\"card run gone\"") && text_of(runs2).contains("retracted 2026-10-13: evaluated on the train split"));
        assert!(text_of(runs2).contains("A retracted run leaves every reading.") && !runs2.contains(":reproduce"));
        let runs2b = card(&page, "run-runs2b");
        assert!(runs2b.contains("id=\"run-runs2b:reproduce\"") && text_of(runs2b).contains("note: replaces runs2"));
        // runs1 recorded the same bytes as runc512, and made the int8 checkpoint
        let runc512 = card(&page, "run-runc512");
        assert!(runc512.contains("same bytes as</dt><dd><a href=\"#run-runs1\">FP32 and INT8, seed 1</a> (e1)"), "{runc512}");
        assert!(card(&page, "run-runs1").contains("<a href=\"#r-ckpt-int8-s1:recipe\">how to get it again</a>"));
    }

    /// The cap: an experiment's newest 50 receipts in full, the rest short cards that keep their anchors, their
    /// results and what they count toward; another experiment's runs are counted apart.
    #[test]
    fn the_cap_keeps_anchors() {
        let mut doc = example("owner");
        let base = at(&doc, "runs.runs1").unwrap().clone();
        let runs = doc.as_dict_mut().unwrap().get_mut("runs").unwrap().as_dict_mut().unwrap();
        for i in 0..60 {
            let mut r = base.clone();
            let d = r.as_dict_mut().unwrap();
            d.insert("run_id".into(), Value::str(format!("runx{i:02}")));
            // recorded a minute apart from 2026-10-01 00:00 UTC, so runx00 is the oldest
            d.insert("at".into(), Value::str(format!("2026-10-01 {:02}:{:02} UTC", i / 60, i % 60)));
            runs.insert(format!("runx{i:02}"), r);
        }
        let page = all(&doc, Surface::Page);
        let e1_runs = entries(&doc, "runs").iter().filter(|(_, r)| s(r, "experiment_id") == "e1").count();
        assert_eq!(e1_runs, 65);
        let short: Vec<String> = (0..60).map(|i| format!("runx{i:02}")).filter(|id| card(&page, &run_anchor(id)).contains("card run short")).collect();
        // e1's five own runs were recorded after every runx, so the 15 oldest runx are past the cap
        assert_eq!(short, (0..15).map(|i| format!("runx{i:02}")).collect::<Vec<_>>());
        assert_eq!(page.matches("class=\"card run short").count(), 15, "e2's four runs stay whole");
        for i in 0..60 {
            assert_eq!(page.matches(&format!(" id=\"run-runx{i:02}\"")).count(), 1);
        }
        let s0 = card(&page, "run-runx00");
        assert!(text_of(s0).contains("This page holds the newest 50 receipts of e1; rb open loads this one in full."));
        assert!(s0.contains("<a class=\"mono\" href=\"#c-c1\">c1</a>") && text_of(s0).contains("Top-1, FP32 0.7598"), "{s0}");
        // its results by their titles, as the full card says them (FIX5)
        assert!(!s0.contains("top1=") && !s0.contains("onnx_top1") && !s0.contains("fp32:"), "{s0}");
        assert!(!s0.contains("<details") && card(&page, "run-runx59").contains("<summary>Source details</summary>"));
        // its results keep their anchor: an analysis that read it drills there
        assert!(s0.contains(" id=\"run-runx00:results\""), "{s0}");
    }

    /// An id is the document's: a hostile one is written escaped everywhere a card names it, its section links too.
    #[test]
    fn an_id_never_breaks_the_markup() {
        let mut doc = example("owner");
        let bad = "c9\" onmouseover=\"alert(1)";
        let findings = doc.as_dict_mut().unwrap().get_mut("findings").unwrap().as_dict_mut().unwrap();
        let mut f = findings.shift_remove("c3").unwrap();
        f.as_dict_mut().unwrap().insert("criterion_id".into(), Value::str(bad));
        findings.insert(bad.into(), f);
        for surface in [Surface::Page, Surface::Export, Surface::Open] {
            let page = all(&doc, surface);
            assert!(!page.contains("\" onmouseover=\""), "{surface:?}");
            assert!(page.contains("id=\"c-c9&quot; onmouseover=&quot;alert(1)\""));
        }
    }

    /// A page draws no words of the user's and no place whatever the document says of its tier: a call's quoted
    /// words and a place in "other code" are drawn on rb open only; other code named by commits is drawn on both.
    #[test]
    fn a_page_drops_the_users_words_and_places_from_any_line() {
        let mut doc = example("live");
        let c1 = dig(&mut doc, &["findings", "c1"]).as_dict_mut().unwrap();
        c1.insert("other_code".into(), Value::str("1 of 3 runs ran other code: exp-b \u{b7} .worktrees/b at 5d1e0a7"));
        let words = "ship it once seed 3 lands";
        for surface in [Surface::Page, Surface::Export] {
            let c1 = all(&doc, surface);
            let c1 = card(&c1, "c-c1");
            assert!(!c1.contains(words) && !c1.contains("exp-b"), "{surface:?}");
            assert!(text_of(c1).contains("accepted 2026-10-14: holds on all three seeds"));
        }
        let open = all(&doc, Surface::Open);
        assert!(card(&open, "c-c1").contains(words) && card(&open, "c-c1").contains("ran other code: exp-b"));
        let c1 = dig(&mut doc, &["findings", "c1"]).as_dict_mut().unwrap();
        c1.insert("other_code".into(), Value::str("1 of 3 runs ran other code: commit 5d1e0a7, with uncommitted changes"));
        assert!(text_of(card(&all(&doc, Surface::Page), "c-c1")).contains("ran other code: commit 5d1e0a7"));
    }

    /// rb may wait on one subject for two reasons: one card, at one anchor, says each reason and offers each action
    /// once.
    #[test]
    fn one_call_card_for_each_subject() {
        let mut doc = example("owner");
        let calls = doc.as_dict_mut().unwrap().get_mut("calls").unwrap().as_list_mut().unwrap();
        let mut again = calls[0].clone();
        let d = again.as_dict_mut().unwrap();
        d.insert("code".into(), Value::str("decision_outdated"));
        d.get_mut("reason").unwrap().as_dict_mut().unwrap().insert("label".into(), Value::str("Made on an earlier reading"));
        calls.push(again);
        let page = all(&doc, Surface::Page);
        assert_eq!(page.matches(" id=\"call-c2\"").count(), 1);
        let c2 = card(&page, "call-c2");
        assert!(text_of(c2).contains("Not reproduced") && text_of(c2).contains("Made on an earlier reading"));
        assert_eq!(c2.matches("data-action=\"accept\"").count(), 2, "one pick button and one line");
    }

    /// A whole number too large for an integer is written as the float it is, never clipped.
    #[test]
    fn a_large_mean_is_written_as_it_is() {
        let mut doc = example("owner");
        let e1 = &mut doc.as_dict_mut().unwrap().get_mut("experiments").unwrap().as_list_mut().unwrap()[0];
        let row = &mut dig(e1, &["table", "rows"]).as_list_mut().unwrap()[0];
        dig(row, &["metrics", "top1"]).as_dict_mut().unwrap().insert("mean".into(), Value::Float(6.0e20));
        let page = all(&doc, Surface::Page);
        assert!(!page.contains("9223372036854775807") && card(&page, "x-e1:v:fp32").contains(">600000000000000000000.00<"), "{}", card(&page, "x-e1:v:fp32"));
    }

    /// The counts and values on the smaller cards drill too: an observation's per-run values to its run, a finding's
    /// integrity to its rule, an analysis's runs, a qualification's and a reading group's criteria, a variant's means
    /// and runs, a resource's availability; and a resource's receipts are anchored like every card's.
    #[test]
    fn cards_name_runs_and_analyses_never_by_id() {
        // FIX7 (R's FIX6 Look 7): an id rb mints is no visible text on a page; a run reads by its label, an analysis by
        // its title, and the id stays in the link's anchor
        let doc = example("owner");
        let page = all(&doc, Surface::Page);
        let runs: Vec<String> = at(&doc, "runs").and_then(Value::as_dict).unwrap().keys().cloned().collect();
        let no_id = |part: &str, what: &str| {
            let text = text_of(part);
            for r in &runs {
                assert!(!text.split(|c: char| !c.is_alphanumeric()).any(|w| w == r), "{what} names {r}: {text}");
            }
        };
        let mut seen = 0;
        for (i, _) in page.match_indices("<div class=\"card variant\"") {
            let c = &page[i..];
            let ul = &c[c.find(":runs\">").unwrap()..];
            let ul = &ul[ul.find("<ul class=\"ctw\">").unwrap()..];
            no_id(&ul[..ul.find("</ul>").unwrap()], "a variant's runs list");
            assert!(ul.contains("<li><a href=\"#run-"), "{ul}");
            seen += 1;
        }
        assert!(seen > 0);
        let r = card(&page, "r-imagenet-val");
        let rests = &r[r.find(":rests\"").unwrap()..];
        no_id(&rests[..rests.find("</section>").unwrap()], "What depends on this");
        assert!(rests.contains("<span class=\"ids\"><a href=\"#run-"), "{rests}");
        let a1 = card(&page, "a-a1");
        assert!(!a1.contains("<span class=\"id\">") && a1.contains("data-crumb=\"per-class accuracy, int8 against fp32\""), "{a1}");
        let reads = &a1[a1.find("id=\"a-a1:reads\"").unwrap()..];
        no_id(&reads[..reads.find("</table>").unwrap()], "an analysis's reads");
        assert!(reads.contains("<thead><tr><th>run</th><th>results</th></tr></thead>"), "{reads}");
        for rid in &runs {
            let head = level(card(&page, &run_anchor(rid)), "understand");
            // a note is its writer's words, verbatim ("replaces runs2"), not rb's naming
            let head = head.split("<p class=\"note\">").next().unwrap();
            no_id(head, "a run's head");
            assert!(!head.contains("<span class=\"id\">"), "{head}");
        }
    }

    /// FIX7, Codex's should: the evidence table says a run by what it was run at, and where two runs of an arm would
    /// read alike, each by its whole name, never by its id.
    #[test]
    fn runs_that_read_alike_in_the_evidence_table_are_said_by_name() {
        let mut doc = example("owner");
        let obs = dig(&mut doc, &["findings", "c1", "observations"]).as_list_mut().unwrap();
        let (arm, first) = (s(&obs[0], "arm").to_string(), s(&obs[0], "run").to_string());
        let twin = obs.iter().position(|o| s(o, "arm") == arm && s(o, "run") != first).unwrap();
        let seed = at(&obs[0], "per_run").unwrap().clone();
        let other = s(&obs[twin], "run").to_string();
        obs[twin].as_dict_mut().unwrap().insert("per_run".into(), seed);
        let page = all(&doc, Surface::Page);
        let c1 = card(&page, "c-c1");
        for rid in [&first, &other] {
            let want = format!("<a href=\"#{}\">{}</a>", run_anchor(rid), esc(&Doc::new(&doc, Surface::Page).run_named(rid)));
            assert!(c1.contains(&want), "{want} in {c1}");
        }
    }

    #[test]
    fn every_count_on_a_card_drills() {
        let doc = example("owner");
        let page = all(&doc, Surface::Page);
        assert!(card(&page, "c-c1").contains("<a href=\"#run-runs1\">seed 1</a>"), "{}", card(&page, "c-c1"));
        assert!(card(&page, "a-a1").contains("<a href=\"#a-a1:reads\">3 runs</a>") && page.contains("id=\"a-a1:reads\""));
        assert!(one(&doc, Surface::Page, "q-q1:lim:0").unwrap().contains("<a href=\"#q-q1:lim:0:criteria\">1 comparison carries</a>"));
        assert!(one(&doc, Surface::Page, "q-q1:rd:criteria-met").unwrap().contains("<a href=\"#q-q1:rd:criteria-met:criteria\">3</a>"));
        let v = card(&page, "x-e1:v:int8");
        assert!(v.contains("<a href=\"#x-e1:v:int8:runs\">0.7568</a>") && v.contains("<a href=\"#x-e1:v:int8:runs\">4 runs</a>"), "{v}");
        let r = card(&page, "r-imagenet-val");
        assert!(r.contains("<a href=\"#r-imagenet-val:places\">available</a>") && r.contains("id=\"r-imagenet-val:receipts\"") && r.contains("id=\"r-imagenet-val:places\""));
        let mut doc = example("owner");
        dig(&mut doc, &["findings", "c1", "says"]).as_dict_mut().unwrap().insert("integrity".into(), Value::str("ok"));
        assert!(card(&all(&doc, Surface::Page), "c-c1").contains("<dt><span class=\"term\" data-hint=\"problems with what a finding depends on (a stale source, an undeclared difference, a record edited outside rb). ok when there are none. Not the repository&#x27;s own checks.\" aria-describedby=\"g-checks\">checks</span></dt><dd class=\"dim\">ok</dd>"));
    }

    /// One card by its anchor is the card `all` draws there, for every card of the document; a run past the cap
    /// comes whole; an anchor with no card gives none.
    #[test]
    fn one_card_by_its_anchor() {
        let doc = example("owner");
        let page = all(&doc, Surface::Page);
        let mut want = anchors(&doc);
        want.push("x-e2:v:int8".into());
        for a in &want {
            assert_eq!(one(&doc, Surface::Page, a).as_deref(), Some(card(&page, a)), "{a}");
        }
        assert_eq!(super::card(&doc, "reading", "q1:rd:criteria-met", Surface::Page), one(&doc, Surface::Page, "q-q1:rd:criteria-met"));
        assert!(one(&doc, Surface::Page, "q-q1:rd:criteria-met").is_some() && one(&doc, Surface::Page, "q-q1:lim:0").is_some());
        assert_eq!(super::card(&doc, "criterion", "c1", Surface::Page), one(&doc, Surface::Page, "c-c1"));
        assert_eq!(super::card(&doc, "variant", "e1:v:fp32", Surface::Page).as_deref(), Some(card(&page, "x-e1:v:fp32")));
        for missing in ["c-c99", "run-nope", "q-q9:lim:0", "x-e1:v:bf16", "nothing", "zz-c1"] {
            assert_eq!(one(&doc, Surface::Page, missing), None, "{missing}");
        }
        let mut doc = doc;
        let base = at(&doc, "runs.runs1").unwrap().clone();
        let runs = dig(&mut doc, &["runs"]).as_dict_mut().unwrap();
        for i in 0..51 {
            let mut r = base.clone();
            r.as_dict_mut().unwrap().insert("at".into(), Value::str(format!("2026-10-01 00:{i:02} UTC")));
            runs.insert(format!("runy{i:02}"), r);
        }
        assert!(card(&all(&doc, Surface::Open), "run-runy00").contains("card run short"));
        assert!(one(&doc, Surface::Open, "run-runy00").unwrap().contains("<summary>Source details</summary>"));
    }

    /// A page drops the user's words by the field that holds them, never by reading quotes in a line: words that hold
    /// a quote go whole, and a line that ends in a quote of its own stays whole.
    #[test]
    fn a_call_line_loses_the_users_words_by_their_field() {
        let mut doc = example("live");
        let c1 = dig(&mut doc, &["findings", "c1"]).as_dict_mut().unwrap();
        let words = "\"ship it \u{b7} \"the int8 one\" now\"";
        c1.insert("your_words".into(), Value::str(words));
        dig(&mut doc, &["findings", "c1", "rests_on"]).as_dict_mut().unwrap().insert("call".into(), Value::str(format!("accepted 2026-10-14: holds \u{b7} {words}")));
        let page = all(&doc, Surface::Page);
        assert!(!card(&page, "c-c1").contains("ship it") && text_of(card(&page, "c-c1")).contains("accepted 2026-10-14: holds"));
        let mut doc = example("owner");
        let reason = "accepted 2026-10-14: per README \u{b7} \"the 76.13 row\"";
        dig(&mut doc, &["findings", "c1", "rests_on"]).as_dict_mut().unwrap().insert("call".into(), Value::str(reason));
        assert!(text_of(card(&all(&doc, Surface::Page), "c-c1")).contains(reason));
    }

    /// A short card says what its run counts toward as its whole receipt does: counted criteria only, each once, and
    /// nothing for a retracted run.
    #[test]
    fn a_short_card_counts_as_its_receipt_does() {
        let mut doc = example("owner");
        let base = at(&doc, "runs.runs1").unwrap().clone();
        let runs = dig(&mut doc, &["runs"]).as_dict_mut().unwrap();
        for i in 0..52 {
            let mut r = base.clone();
            let d = r.as_dict_mut().unwrap();
            d.insert("at".into(), Value::str(format!("2026-10-01 00:{i:02} UTC")));
            let ct = |c: &str, counted: bool| crate::obj! {"criterion" => c, "counted" => counted, "reason" => "", "via" => ""};
            d.insert("counts_toward".into(), Value::List(vec![ct("c1", false), ct("c2", true), ct("c3", true), ct("c2", true)]));
            if i == 0 {
                d.insert("retracted".into(), crate::obj! {"at" => "2026-10-13", "line" => "retracted 2026-10-13: wrong split", "why" => "wrong split"});
            }
            runs.insert(format!("runz{i:02}"), r);
        }
        let page = all(&doc, Surface::Page);
        let (gone, kept) = (card(&page, "run-runz00"), card(&page, "run-runz01"));
        assert!(gone.contains("card run short gone") && !gone.contains("Counts toward"), "{gone}");
        assert!(kept.contains("card run short") && kept.contains("Counts toward 2 rules: <a class=\"mono\" href=\"#c-c2\">c2</a>, <a class=\"mono\" href=\"#c-c3\">c3</a></p>"), "{kept}");
        // the whole receipt counts each criterion once
        let full = one(&doc, Surface::Page, "run-runz01").unwrap();
        assert!(full.contains(">2 rules \u{b7} not counted in 1</a>"), "{full}");
    }

    /// An id rb's line names is linked where the line says it, whole, and only there.
    #[test]
    fn a_line_links_the_ids_it_names() {
        let doc = example("owner");
        let d = Doc::new(&doc, Surface::Page);
        assert_eq!(d.with_refs("2026-10-06 10:20 UTC, with e1", &["e1"]), "<span>2026-10-06 10:20 UTC, with </span><a href=\"#x-e1\">e1</a>");
        assert_eq!(d.with_refs("e12 then e1", &["e1"]), "<span>e12 then </span><a href=\"#x-e1\">e1</a>");
        assert_eq!(d.with_refs("e1 · primary", &["e1"]), "<a href=\"#x-e1\">e1</a><span> \u{b7} primary</span>");
        assert_eq!(d.with_refs("no id here", &["c1"]), "<span>no id here</span><span> \u{b7} </span><a href=\"#c-c1\">c1</a>");
        assert_eq!(d.with_refs("a <b> & q9", &["q9"]), "a &lt;b&gt; &amp; q9");
    }

    /// A card links only to a part of a view the document gives the page: no recipe, no amendment and no question the
    /// document lacks is linked, and each is linked once the document holds it.
    #[test]
    fn a_card_links_only_to_what_the_page_draws() {
        let doc = example("owner");
        let page = all(&doc, Surface::Page);
        assert!(page.contains("href=\"#x-e1:reproduce\"") && page.contains("href=\"#x-e2:am:1\"") && page.contains("href=\"#q-q1a\""));
        let mut doc = example("owner");
        for x in dig(&mut doc, &["experiments"]).as_list_mut().unwrap() {
            let x = x.as_dict_mut().unwrap();
            x.insert("reproduce".into(), Value::Null);
            x.insert("amendments".into(), Value::List(Vec::new()));
            x.insert("who_line".into(), Value::str(""));
        }
        dig(&mut doc, &["questions"]).as_list_mut().unwrap().retain(|q| s(q, "id") != "q1a");
        let page = all(&doc, Surface::Page);
        for dead in ["#x-e1:reproduce", "#x-e2:reproduce", "#x-e2:am:1", "#x-e2:lock", "#q-q1a\""] {
            assert!(!page.contains(&format!("href=\"{dead}")), "{dead}");
        }
        // the amendment's line is still said, linked to the experiment
        assert!(card(&page, "c-c5").contains("<a href=\"#x-e2\">unit amended after the lock"));
        // a finding with no receipt rows and no cited source draws no receipts, and its parts link none
        let mut doc = example("owner");
        let c1 = dig(&mut doc, &["findings", "c1"]).as_dict_mut().unwrap();
        c1.insert("receipt_rows".into(), Value::List(Vec::new()));
        c1.shift_remove("cited");
        let c1 = card(&all(&doc, Surface::Page), "c-c1").to_string();
        // rb's own rule line is still a source detail, so the tab to it stays
        assert!(c1.contains("<a href=\"#c-c1:impact\">What depends on this</a><a href=\"#c-c1:receipts\">Source details</a></nav>"), "{c1}");
    }

    /// A card says a criterion in plain words (PLAIN-WORDS-PASS-1): "rule", "the rule as declared", "checks",
    /// "what depends on this", "The record will say:", and the reading groups "rules met", "can't tell yet" and "not
    /// tested". Kinds, anchors and commands keep rb's words.
    #[test]
    fn a_card_says_rule_in_plain_words() {
        let mut doc = example("owner");
        dig(&mut doc, &["findings", "c1", "says"]).as_dict_mut().unwrap().insert("integrity".into(), Value::str("ok"));
        let page = all(&doc, Surface::Page);
        let c1 = card(&page, "c-c1");
        let kind = &c1[c1.find("<span class=\"kind\">").unwrap()..];
        assert!(kind.starts_with(&format!("<span class=\"kind\">{}</span>", rule_term("rule"))), "{c1}");
        // the chip carries the glossary's line on "rule" (a key the glossary holds, so it is not drawn plain)
        assert!(kind.starts_with(&format!("<span class=\"kind\"><span class=\"term\" data-hint=\"{}\"", esc(w::hint("rule").unwrap()))), "{c1}");
        assert!(c1.contains("data-kind=\"criterion\""));
        assert!(c1.contains("<a href=\"#c-c1:rule\">The rule as written</a>") && c1.contains("<h4>The rule as written</h4>"), "{c1}");
        assert!(c1.contains("<a href=\"#c-c1:impact\">What depends on this</a>") && c1.contains("<h4>What depends on this</h4>"), "{c1}");
        assert!(c1.contains(">checks</span></dt><dd class=\"dim\">ok</dd>"), "{c1}");
        let r = card(&page, "r-imagenet-val");
        assert!(r.contains("<h4>What depends on this</h4>") && r.contains("rules read</a>"), "{r}");
        let c2 = card(&page, "c-c2");
        assert!(c2.contains("<span class=\"cons\">The record will say: ") && c2.contains("<p class=\"willread\">The record will say: <span>"), "{c2}");
        let text = text_of(&page);
        for old in ["declared criterion", "what rests on this", "Its review will read", "No recorded criterion", "point only: no interval yet"] {
            assert!(!text.contains(old), "{old}");
        }
        assert!(!page.contains("<span class=\"kind\">criterion</span>"));
        // each reading group says its plain words; its anchor keeps rb's
        let met = one(&doc, Surface::Page, "q-q1:rd:criteria-met").unwrap();
        assert!(met.contains("data-crumb=\"rules met\"") && text_of(&met).starts_with("Rules: met (3)"), "{met}");
        for (word, shown) in [("criterion undetermined", "can't tell yet"), ("criterion untested", "not tested"), ("criterion not met", "rules not met")] {
            let mut doc = example("owner");
            dig(&mut doc, &["findings", "c1"]).as_dict_mut().unwrap().insert("standing".into(), Value::str(word));
            let anchor = format!("q-q1:rd:{}", word.replace("criterion", "criteria").replace(' ', "-"));
            let group = one(&doc, Surface::Page, &anchor).unwrap_or_else(|| panic!("{anchor}"));
            assert!(group.contains(&format!("data-crumb=\"{}\"", esc(shown))) && group.contains(&format!("<h3 class=\"tt\">Rules: {} (", term(shown, &esc(shown.strip_prefix("rules ").unwrap_or(shown))))), "{group}");
        }
        // a run no rule reads says so
        let mut doc = example("owner");
        dig(&mut doc, &["runs", "runs1"]).as_dict_mut().unwrap().insert("counts_toward".into(), Value::List(Vec::new()));
        let run = card(&all(&doc, Surface::Page), "run-runs1").to_string();
        assert!(run.contains("<p class=\"dim sm\">No recorded rule reads it.</p>"), "{run}");
        // what a card says of no interval
        assert!(qualification_meaning("No interval").unwrap().starts_with("one estimate with no interval, so how much it could vary is unknown."));
        assert_eq!(qualification_meaning("Rule written after the results"), qualification_meaning("Retrospective"));
    }

    /// Each of the glossary's terms a card draws is described by its entry (aria-describedby), the command's too.
    #[test]
    fn a_cards_terms_name_their_glossary_entries() {
        let page = all(&example("owner"), Surface::Page);
        let terms: Vec<&str> = page.split("<span class=\"term\"").skip(1).map(|t| &t[..t.find('>').unwrap()]).collect();
        assert!(terms.len() > 10);
        for t in &terms {
            assert!(t.contains(" aria-describedby=\"g-"), "{t}");
        }
        assert!(card(&page, "run-runs1").contains("aria-describedby=\"g-command-as-given\">command (as given)</span>"));
    }

    /// DESIGN-PASS-1 target 7: a run's title is in human words, its id last in the quiet line; when it was produced and
    /// recorded is said there once each, by day, and once only where it is the same day; the times are Source details.
    #[test]
    fn a_runs_head_is_its_title_then_its_days() {
        for (produced, when, want) in [
            ("2026-10-14", "2026-10-20", "runin e1 \u{b7} produced 2026-10-14 \u{b7} recorded 2026-10-20"),
            ("2026-10-20", "2026-10-20", "runin e1 \u{b7} produced and recorded 2026-10-20"),
            ("", "2026-10-20", "runin e1 \u{b7} recorded 2026-10-20"),
        ] {
            let mut doc = example("owner");
            let r = dig(&mut doc, &["runs", "runs1"]).as_dict_mut().unwrap();
            r.insert("label".into(), Value::str("fp32 and int8 \u{b7} seed 1 \u{b7} scheme per_tensor \u{b7} calib_images 128"));
            r.insert("produced".into(), Value::str(produced));
            r.insert("when".into(), Value::str(when));
            let page = all(&doc, Surface::Page);
            let run = card(&page, "run-runs1");
            let head = level(run, "understand");
            assert!(head.starts_with("<div class=\"lv\" data-level=\"understand\"><h3 class=\"tt\">FP32 and INT8, seed 1, per-tensor, calib images 128</h3>"), "{head}");
            let hd = &head[head.find("<div class=\"hd\">").unwrap()..];
            assert_eq!(text_of(&hd[..hd.find("</div>").unwrap()]), want);
            // FIX7: the id rb minted is no visible text; the card's anchor and its crumb's words carry the run
            assert!(!text_of(&head).contains("runs1") && run.contains("data-crumb=\"FP32 and INT8, seed 1, per-tensor, calib images 128\""), "{run}");
            assert!(!head.contains("\u{b7} seed") && !head.contains("calib_images"), "rb's label is not the title: {head}");
        }
        // with no label it is titled by the experiment it is a run of, never by its id (FIX7), and so is every link
        // to it
        let mut doc = example("owner");
        dig(&mut doc, &["runs", "runs1"]).as_dict_mut().unwrap().insert("label".into(), Value::str(""));
        let page = all(&doc, Surface::Page);
        assert!(card(&page, "run-runs1").contains("<h3 class=\"tt\">run of e1</h3>"), "{}", card(&page, "run-runs1"));
        assert!(card(&page, "run-runc512").contains("same bytes as</dt><dd><a href=\"#run-runs1\">run of e1</a> (e1)"));
    }

    /// The review of design pass 1: a sentence is true on whichever side of its threshold the reading falls, says what
    /// each kind of rule reads (better, equivalent, each, both ends), says a cited difference in points, never promises
    /// an interval across design cells, signs only a difference, says "No results yet" for a rule with none, keeps every
    /// link closed whatever the record's text holds, and says each word and source once.
    #[test]
    fn a_sentence_is_true_for_every_kind_of_rule() {
        let base = at(&example("owner"), "findings.c1").unwrap().clone();
        let with = |rule: &str, band: Value, state: &str, est: f64, lo: f64, hi: f64| {
            let mut f = base.clone();
            let d = f.as_dict_mut().unwrap();
            d.insert("rule".into(), Value::str(rule));
            d.insert("check_band".into(), band);
            d.insert("state".into(), Value::str(state));
            d.insert("standing".into(), Value::str(if state == "meets" { "criterion met" } else { "criterion not met" }));
            let r = d.get_mut("reading").unwrap().as_dict_mut().unwrap();
            r.insert("estimate".into(), Value::Float(est));
            r.insert("lo".into(), Value::Float(lo));
            r.insert("hi".into(), Value::Float(hi));
            r.insert("unit_text".into(), Value::str("%"));
            f
        };
        let band = |c: &str, t: f64, tol: Option<f64>| crate::obj! {"comparator" => c, "target" => t, "tolerance" => tol.map_or(Value::Null, Value::Float)};
        // far past the margin on the better side: never "within 1 point of"
        let f = with("change.top1, lower bound ≥ -1", band("at least", -1.0, None), "meets", 3.28, 3.06, 3.5);
        assert_eq!(sentence_text(&verdict(&f)), "Met: INT8 is 3.28 points higher than the FP32 baseline (+3.06 to +3.50); its worse end is 4.06 points past the -1 point it asks for.");
        let f = with("change.top1 ≥ -1", band("at least", -1.0, None), "meets", 3.28, 3.06, 3.5);
        assert_eq!(sentence_text(&verdict(&f)), "Met: INT8 is 3.28 points higher than the FP32 baseline (+3.06 to +3.50), 4.28 points past the -1 point it asks for.");
        let f = with("change.top1 ≥ -1", band("at least", -1.0, None), "misses", -1.5, -1.9, -1.1);
        assert_eq!(sentence_text(&verdict(&f)), "Not met: INT8 is 1.50 points lower than the FP32 baseline (-1.90 to -1.10), short of the -1 point the rule asks for.");
        // each kind of rule says what it reads
        let f = with("change.top1 better, its interval clear of 0", band("at least", 0.0, None), "misses", 0.2, -0.1, 0.5);
        assert_eq!(rule_passes(&f), "The whole interval is clear of 0 on the better side (INT8 against the FP32 baseline).");
        assert_eq!(sentence_text(&verdict(&f)), "Not met: INT8 is 0.20 points higher than the FP32 baseline (-0.10 to +0.50); its interval is not clear of 0 on the better side.");
        let f = with("change.top1 within ±0.5, its interval inside ±0.5", band("within", 0.0, Some(0.5)), "meets", 0.1, -0.2, 0.4);
        assert_eq!(rule_passes(&f), "The whole interval is inside \u{b1}0.5 points (INT8 against the FP32 baseline).");
        assert_eq!(sentence_text(&verdict(&f)), "Met: INT8 is 0.10 points higher than the FP32 baseline (-0.20 to +0.40); its interval is inside \u{b1}0.5 points.");
        let f = with("each unit's change.top1 ≥ -1", band("at least", -1.0, None), "misses", -0.5, -0.9, -0.1);
        assert_eq!(rule_passes(&f), "Every unit's value is at least -1 point (INT8 against the FP32 baseline).");
        assert_eq!(sentence_text(&verdict(&f)), "Not met: INT8 is 0.50 points lower than the FP32 baseline (-0.90 to -0.10); a unit's value falls outside it.");
        let f = with("change.top1, its interval = 3 ± 0.5", band("within", 3.0, Some(0.5)), "meets", 3.1, 2.8, 3.4);
        assert_eq!(rule_passes(&f), "The whole interval is within 0.5 points of 3 points (INT8 against the FP32 baseline).");
        // a cited difference is in points, as every difference is
        let mut f = with("change.top1 = -0.5 ± 0.05", band("within", -0.5, Some(0.05)), "reproduced", -0.5, -0.6, -0.4);
        f.as_dict_mut().unwrap().insert("origin".into(), Value::str("cited"));
        assert_eq!(sentence_text(&verdict(&f)), "Reproduced: INT8 is 0.50 points lower than the FP32 baseline (-0.60 to -0.40), within 0.05 points of the -0.5 points.");
        // across design cells: rb never gives an interval, and a rule met on its estimate there says its cells
        let doc = example("owner");
        let c5 = at(&doc, "findings.c5").unwrap().clone();
        let mut f = c5.clone();
        let d = f.as_dict_mut().unwrap();
        d.insert("state".into(), Value::str("meets"));
        d.insert("standing".into(), Value::str("estimate meets criterion"));
        d.get_mut("reading").unwrap().as_dict_mut().unwrap().insert("estimate".into(), Value::Float(-0.0072));
        let said = sentence_text(&verdict(&f));
        assert!(said.starts_with("Met on its estimate: 1 of 4 design cells is outside the rule") && !said.contains("does not show it yet"), "{said}");
        assert!(sentence_text(&reading_sentence(&f)).contains("(no interval across design cells)"), "{}", sentence_text(&reading_sentence(&f)));
        // a level across cells is not signed in the figure's words
        let mut f = c5.clone();
        f.as_dict_mut().unwrap().insert("reference_label".into(), Value::str(""));
        let cells = f.as_dict_mut().unwrap().get_mut("design_cells").unwrap().as_list_mut().unwrap();
        for (c, v) in cells.iter_mut().zip(["88.2", "89.92", "89.9", "90.1"]) {
            c.as_dict_mut().unwrap().insert("estimate_text".into(), Value::str(v));
        }
        let fig = figure::draw(&f, true);
        assert!(fig.contains("calib images 128: 88.20") && fig.contains("calib images 512: 89.92") && !fig.contains(": +"), "{fig}");
        // a rule with no results says so
        assert_eq!(timing_said("no evidence recorded yet"), "No results yet.");
        assert_eq!(timing_said("order unknown"), "rb can't tell whether it was written before its results.");
        // the record's text cannot open a link that never closes
        let mut f = c5.clone();
        let cells = f.as_dict_mut().unwrap().get_mut("design_cells").unwrap().as_list_mut().unwrap();
        cells[0].as_dict_mut().unwrap().insert("key".into(), Value::str("tag=a\u{1}<i>b"));
        let html = sentence_html(&verdict(&f), "c-c5:runs");
        assert_eq!(html.matches("<a ").count(), html.matches("</a>").count(), "{html}");
        assert!(!html.contains('\u{1}') && html.contains("&lt;i&gt;") && html.contains("<a href=\"#c-c5:runs\">"), "its counts stay links: {html}");
        // and an arm's name holding one still leaves the sentence its numbers' links
        let mut f = base.clone();
        f.as_dict_mut().unwrap().insert("reference_label".into(), Value::str("fp32\u{1} baseline"));
        let html = sentence_html(&reading_sentence(&f), "c-c1:runs");
        assert!(html.contains("<a href=\"#c-c1:runs\">") && html.matches("<a ").count() == html.matches("</a>").count(), "{html}");
        // each word once on the state line, and a cited source once
        let mut doc = example("owner");
        let f = dig(&mut doc, &["findings", "c3"]).as_dict_mut().unwrap();
        f.insert("standing".into(), Value::str("criterion untested \u{b7} untested"));
        f.insert("state".into(), Value::str("untested"));
        let page = all(&doc, Surface::Page);
        let c3 = card(&page, "c-c3");
        let sti = &c3[c3.find("<p class=\"str\">").unwrap()..];
        assert_eq!(text_of(&sti[..sti.find("</p>").unwrap()]).to_lowercase().matches("not tested").count(), 1, "{sti}");
        let mut doc = example("owner");
        let cited = dig(&mut doc, &["findings", "c2", "cited"]).as_dict_mut().unwrap();
        let at_ = format!("{}:{}", cited.get("path").unwrap().as_str().unwrap(), num(&Value::Dict(cited.clone()), "line"));
        cited.insert("locator".into(), Value::str(&at_));
        let call = card(&all(&doc, Surface::Page), "call-c2").to_string();
        assert_eq!(text_of(&call).matches(&at_).count(), 1, "{call}");
    }

    /// DESIGN-PASS-1 target 7 and P6: a run's Counts toward is rows, each a rule's sentence, its id trailing inside the
    /// link and its state after it; the rules that leave it out follow under one line that gives their shared reason
    /// once; its results are rows in the UI font. On a rule card, what every run shares is said once above the table.
    #[test]
    fn a_runs_rules_are_rows_and_a_shared_reason_is_said_once() {
        let mut doc = example("owner");
        let why = "its unit has a run that gives the difference by itself, so the paired reading leaves it out";
        let ct = |c: &str, counted: bool| crate::obj! {"criterion" => c, "counted" => counted, "reason" => if counted { "" } else { why }, "via" => ""};
        let r = dig(&mut doc, &["runs", "runs1"]).as_dict_mut().unwrap();
        r.insert("counts_toward".into(), Value::List(vec![ct("c1", true), ct("c2", false), ct("c3", false)]));
        r.insert("results".into(), Value::str("fp32.top1=0.7598 \u{b7} int8.top1=0.7569"));
        let page = all(&doc, Surface::Page);
        let run = card(&page, "run-runs1");
        let counts = &run[run.find("id=\"run-runs1:counts\"").unwrap()..];
        let counts = &counts[..counts.find("</section>").unwrap()];
        assert!(counts.contains("<li><a class=\"ci\" href=\"#c-c1\"><span class=\"au\">int8 holds top1 within 1 point of fp32</span> <span class=\"mono id\">c1</span></a> \u{b7} <span class=\"sw good\">rule met</span></li>"), "{counts}");
        assert_eq!(text_of(counts).matches(why).count(), 1, "{counts}");
        assert!(text_of(counts).contains(&format!("Counted in 1 rule.")) && text_of(counts).contains(&format!("Not counted in 2 rules: {why}.")), "{}", text_of(counts));
        // two numbers that differ never read alike: 0.7598 and 0.7569 take a third decimal, rb's digits on hover
        assert!(run.contains("<dl class=\"ax mets\"><dt>Top-1, FP32</dt><dd><span class=\"nb\" title=\"0.7598\">0.760</span></dd><dt>Top-1, INT8</dt><dd><span class=\"nb\" title=\"0.7569\">0.757</span></dd></dl>"), "{run}");
        // a rule card's table: the arm all its runs share and the values every run has, once above it
        let c1 = card(&page, "c-c1");
        let runs = &c1[c1.find("id=\"c-c1:runs\"").unwrap()..];
        let runs = &runs[..runs.find("</section>").unwrap()];
        assert!(text_of(runs).contains("Every run here: one cycle 1.") && text_of(runs).contains("Each run gives the difference by itself."), "{}", text_of(runs));
        assert!(!runs.contains("one_cycle") && !runs.contains(">difference<"), "{runs}");
    }

    /// A run card's Reproduce lines say each state once: rb's own line opens with it, a line that does not gets it.
    #[test]
    fn a_reproduce_line_says_its_state_once() {
        let mut doc = example("owner");
        let ready = dig(&mut doc, &["runs", "runs1", "reproduce", "ready"]).as_list_mut().unwrap();
        // the line the example gives, whatever its words (FIX8: the contract's examples say it, not this pin)
        let code = s(&ready[3], "text").to_string();
        let first = ready[0].as_dict_mut().unwrap();
        first.insert("text".into(), Value::str("ok \u{b7} commit defcbf0a63"));
        first.insert("refs".into(), Value::List(Vec::new()));
        let page = all(&doc, Surface::Page);
        let rdy: Vec<String> = card(&page, "run-runs1").split("<p class=\"sm rdy ").skip(1).map(|p| text_of(&p[p.find('>').unwrap() + 1..p.find("</p>").unwrap()])).collect();
        assert_eq!(rdy[0], "\u{2713} ok \u{b7} commit defcbf0a63");
        assert!(code.starts_with("code: unknown"), "{code}");
        assert_eq!(rdy[3], format!("\u{25cb} unknown \u{b7} {code}"));
        assert_eq!(rdy.len(), 4);
    }

    /// A run's command is its own code, how rb knows it a row of its own under its own label (never "--seed 1as
    /// given"), and the card says it once: its receipt row is not drawn again.
    #[test]
    fn a_runs_command_stands_alone_and_once() {
        let mut doc = example("owner");
        let cmd = dig(&mut doc, &["runs", "runs1", "command"]).as_dict_mut().unwrap();
        cmd.insert("text".into(), Value::str("python train.py --precision int8 --seed 1"));
        cmd.insert("kind".into(), Value::str("as given"));
        let page = all(&doc, Surface::Page);
        let run = card(&page, "run-runs1");
        assert!(
            run.contains("<span class=\"v\"><code>python train.py --precision int8 --seed 1</code></span><span class=\"k\">how rb knows</span><span class=\"v s\">as given</span></div>"),
            "{run}"
        );
        assert!(!text_of(&run).contains("--seed 1as given"));
        assert!(!run.contains("<dt>command</dt>"), "the receipt says the command again");
        assert_eq!(text_of(&run).matches("python train.py --precision int8 --seed 1").count(), 1);
        // no command recorded: the value says so, with no row of how rb knows
        let page = all(&example("owner"), Surface::Page);
        let run = card(&page, "run-runs1");
        assert!(run.contains("<span class=\"v\">not recorded</span></div>"), "{run}");
    }

    /// The recipe has one Copy, in the row above it: its box takes none of its own (`nocp`), which would cover its
    /// first line.
    #[test]
    fn a_runs_recipe_has_one_copy() {
        let page = all(&example("owner"), Surface::Page);
        let run = card(&page, "run-runs1");
        assert!(run.contains("<div class=\"cprow\" data-copybtn=\"run-runs1:recipe\" data-label=\"Copy recipe\"></div><div class=\"cmdbox nocp\"><pre class=\"cmd\" id=\"run-runs1:recipe\">"), "{run}");
        assert_eq!(run.matches("class=\"cmdbox").count(), 1);
    }

    /// What a run made links the resource and how to get it again, never the card's own run, and no id twice.
    #[test]
    fn a_made_line_links_each_id_once_and_never_its_own_card() {
        let mut doc = example("owner");
        let made = dig(&mut doc, &["runs", "runs1", "reproduce", "made"]).as_list_mut().unwrap();
        made[0].as_dict_mut().unwrap().insert("line".into(), Value::str("Made ckpt-int8-s1."));
        let page = all(&doc, Surface::Page);
        let run = card(&page, "run-runs1");
        let line = run.split("<p class=\"sm\">").find(|p| p.starts_with("<span>Made ")).unwrap();
        let line = &line[..line.find("</p>").unwrap()];
        assert!(!line.contains("href=\"#run-runs1\""), "{line}");
        assert_eq!(line.matches("href=\"#r-ckpt-int8-s1\"").count(), 1, "{line}");
        // on another card, a line naming one id twice links it once
        let d = Doc::new(&doc, Surface::Page);
        let html = d.with_refs("Made ckpt-int8-s1.", &["runs2", "runs2", "ckpt-int8-s1"]);
        assert_eq!(html.matches("href=\"#run-runs2\"").count(), 1, "{html}");
    }

    /// A row says its label once: the declared criterion's margin is its number, and a receipt row that opens with its
    /// own field's name ("produced 2026-10-14 ..") loses it beside the label.
    #[test]
    fn a_row_says_its_label_once() {
        let page = all(&example("owner"), Surface::Page);
        let c1 = card(&page, "c-c1");
        assert!(c1.contains("<dt>margin</dt><dd><a href=\"#c-c1:reading\">0.0071</a></dd>"), "{c1}");
        let mut doc = example("owner");
        let rows = dig(&mut doc, &["runs", "runs1", "receipt_rows"]).as_list_mut().unwrap();
        rows[1] = Value::List(vec![Value::str("produced"), Value::str("produced 2026-10-14 01:54 UTC (as reported)"), Value::List(Vec::new())]);
        rows[3] = Value::List(vec![Value::str("repository"), Value::str("repository when recorded: commit 3f2a9c1 \u{b7} tree clean"), Value::List(Vec::new())]);
        let page = all(&doc, Surface::Page);
        let run = card(&page, "run-runs1");
        // produced: its day in the card's head, its time and how rb knows it once, as a row of Source details
        assert!(run.contains("<dt>produced</dt><dd>2026-10-14 01:54 UTC (as reported)</dd>") && text_of(run).matches("2026-10-14 01:54 UTC (as reported)").count() == 1, "{run}");
        // a row in the UI font, its hash in monospace (DESIGN-PASS-1 target 7, P4), a path too; a date never
        assert!(run.contains(">repository</dt><dd>when recorded: commit <span class=\"mono\">3f2a9c1</span> \u{b7} tree clean</dd>"), "{run}");
        assert!(run.contains("<dd><span class=\"mono\">results/seed1/metrics.json</span> \u{b7} sha256 <span class=\"mono\">44d9a7356c16</span></dd>"), "{run}");
        assert!(!run.contains("<span class=\"mono\">2026-10-14"), "{run}");
        assert_eq!(mono_tokens("python 3.12.15 \u{b7} rb 0.6.0 \u{b7} tree dirty, 2 files"), "python 3.12.15 \u{b7} rb 0.6.0 \u{b7} tree dirty, 2 files");
        assert_eq!(mono_tokens("<a href=\"#r-x\">ckpt/int8.pt</a>, here"), "<a href=\"#r-x\"><span class=\"mono\">ckpt/int8.pt</span></a>, here");
    }

    /// A command rb did not see make the numbers keeps its label and why rb reads it so, from the receipt's own rows,
    /// which the receipts then do not say again.
    #[test]
    fn a_later_command_keeps_its_label_and_why() {
        let mut doc = example("owner");
        let run = dig(&mut doc, &["runs", "runs1"]).as_dict_mut().unwrap();
        let cmd = run.get_mut("command").unwrap().as_dict_mut().unwrap();
        cmd.insert("text".into(), Value::str("python eval.py --seed 3"));
        cmd.insert("kind".into(), Value::str("given later"));
        let rows = dig(&mut doc, &["runs", "runs1", "receipt_rows"]).as_list_mut().unwrap();
        let why = "it was made before it was recorded, and neither rb's journal nor a log line written then shows this command made it";
        rows.retain(|c| c.as_list().unwrap()[0].as_str() != Some("command"));
        for t in ["command given later, not observed: python eval.py --seed 3".to_string(), format!("how rb knows: {why}")] {
            rows.push(Value::List(vec![Value::str("command"), Value::str(&t), Value::List(Vec::new())]));
        }
        let page = all(&doc, Surface::Page);
        let run = card(&page, "run-runs1");
        assert!(
            run.contains(&format!(
                "<span class=\"k\">command given later, not observed</span><span class=\"v\"><code>python eval.py --seed 3</code></span><span class=\"k\">how rb knows</span><span class=\"v s\">{}</span></div>",
                esc(why)
            )),
            "{run}"
        );
        assert!(!run.contains("<dt>command</dt>"));
    }

    /// The part of `card` from its level `level` to the next level (or the card's end).
    fn level<'a>(card: &'a str, level: &str) -> &'a str {
        let at = card.find(&format!("data-level=\"{level}\"")).unwrap_or_else(|| panic!("no {level}"));
        let start = card[..at].rfind('<').unwrap();
        let end = card[at..].find("data-level=").and_then(|_| {
            let rest = &card[at + 1..];
            rest.find("data-level=").map(|k| card[..at + 1 + k].rfind('<').unwrap())
        });
        &card[start..end.unwrap_or(card.len())]
    }

    /// A finding a call waits on ends in its Decide: why rb asks, the actions and their commands, at `c-<id>:decide`,
    /// linked from the card's parts; the call is said there once, not again among what rests on it.
    #[test]
    fn a_finding_a_call_waits_on_ends_in_its_decide() {
        let doc = example("owner");
        let page = all(&doc, Surface::Page);
        let c2 = card(&page, "c-c2");
        let decide = level(c2, "decide");
        assert!(decide.contains("id=\"c-c2:decide\"") && c2.ends_with(decide) && decide.ends_with("</section></div>"), "{decide}");
        // the verdict above says the state; the call box does not again (FIX2)
        assert!(!text_of(decide).contains("Not reproduced") && decide.contains("rb decide c2 accept --why"), "{decide}");
        assert!(c2.contains("<a href=\"#c-c2:decide\">Decide</a>"));
        assert!(!text_of(level(c2, "examine")).contains("Review this result"), "the call said twice");
        // the review links to the card's own Decide, and nothing on the card says a thing twice: not the call made on
        // it (the review says it, what rests on it does not), not its result (the standing line says it)
        assert!(c2.contains("<a href=\"#c-c2:decide\">Your call</a>") && !c2.contains("href=\"#call-c2\""), "{c2}");
        let call = "investigating since 2026-10-21: compare the resize and crop with the paper's";
        let mut doc = doc.clone();
        dig(&mut doc, &["findings", "c2", "rests_on"]).as_dict_mut().unwrap().insert("call".into(), Value::str(call));
        let page = all(&doc, Surface::Page);
        let c2 = card(&page, "c-c2");
        assert_eq!(text_of(c2).matches(call).count(), 1, "{}", text_of(c2));
        let f = at(&doc, "findings.c2").unwrap();
        let result = s(f, "reading.result");
        let sti = level(c2, "understand");
        let sti = &sti[sti.find("<p class=\"str\">").unwrap()..];
        // said at most once; a level's "measured" says nothing, and is not said
        assert!(text_of(&sti[..sti.find("</p>").unwrap()]).matches(result).count() <= 1);
        assert_eq!(text_of(level(c2, "examine")).replace(MEASURED, "").matches(result).count(), 0, "{}", text_of(level(c2, "examine")));
        // on a page with no call on it, the finding has no Decide
        assert!(!card(&page, "c-c1").contains("data-level=\"decide\"") && !card(&page, "c-c1").contains("#c-c1:decide"));
    }

    /// A call card: what it is on and its standing, then why it needs a call and what rb read, then the cited file in
    /// Source details, then Decide at `call-<subject>:decide`.
    #[test]
    fn a_call_card_keeps_its_levels() {
        let doc = example("owner");
        let page = all(&doc, Surface::Page);
        let c = card(&page, "call-c2");
        let order: Vec<usize> = ["data-level=\"understand\"", "data-level=\"examine\"", "<summary>Source details</summary>", "id=\"call-c2:decide\""]
            .iter()
            .map(|p| c.find(p).unwrap_or_else(|| panic!("{p}")))
            .collect();
        assert!(order.windows(2).all(|w| w[0] < w[1]), "{order:?}");
        assert!(level(c, "understand").contains("<p class=\"verdict\">Not reproduced: "), "{c}");
        assert!(!level(c, "examine").contains("README.md") && level(c, "trace").contains("README.md:14"), "the cited file is a source detail");
    }

    /// A resource and an analysis keep their paths, hashes and recipes in Source details; a resource says its
    /// availability once, on its standing line.
    #[test]
    fn paths_and_hashes_are_source_details() {
        let doc = example("owner");
        let page = all(&doc, Surface::Page);
        let r = card(&page, "r-imagenet-val");
        let up = &r[..r.find("data-level=\"trace\"").unwrap()];
        for detail in ["data/imagenet/val", "sha256 of the file list", "python get_data.py", "scripts/README.md"] {
            assert!(!up.contains(detail) && level(r, "trace").contains(detail), "{detail}");
        }
        assert_eq!(text_of(r).matches("available").count(), 1, "{}", text_of(r));
        let a = card(&page, "a-a1");
        let up = &a[..a.find("data-level=\"trace\"").unwrap()];
        for detail in ["analysis/per_class.py", "61be0c94d7a2"] {
            assert!(!up.contains(detail) && level(a, "trace").contains(detail), "{detail}");
        }
    }


    /// An analysis's table: each run's numbers drill to that run's results.
    #[test]
    fn an_analysis_drills_to_each_runs_results() {
        let doc = example("owner");
        let a1 = card(&all(&doc, Surface::Page), "a-a1").to_string();
        for rid in strs(at(&doc, "analyses.a1").unwrap(), "reads") {
            let m = s(at(&doc, &format!("runs.{rid}")).unwrap(), "metrics_text");
            assert!(a1.contains(&format!("<a href=\"#run-{rid}:results\">{}</a>", esc(m))), "{rid}");
        }
    }


    /// PLAIN-WORDS-PASS-3: a recorded interpretation is a note, its standing in the line and never a tag of its own:
    /// "Note: <its words>" once confirmed, "A note, not confirmed: <its words>" until then, the same on every surface;
    /// one whose rule is not met is "a note for when this rule is met". The glossary's line stays on the lead, the
    /// question trails, and "reading" is never its name.
    #[test]
    fn a_findings_note_says_what_it_is() {
        for surface in [Surface::Page, Surface::Open, Surface::Export] {
            let doc = example(if surface == Surface::Export { "away" } else { "owner" });
            let page = all(&doc, surface);
            for (id, lead) in [("c-c1", "Note:"), ("c-c5", "A note, not confirmed:")] {
                let impact = card(&page, &format!("{id}:impact"));
                let said = &impact[impact.find("<div class=\"said\">").unwrap()..];
                let said = text_of(&said[..said.find("</div>").unwrap()]);
                let f = at(&doc, &format!("findings.{}", &id[2..])).unwrap();
                let words = list(f, "rests_on.interpretations").iter().find(|i| at(i, "active").and_then(Value::as_bool) == Some(true)).map(|i| s(i, "text")).unwrap_or("");
                // the away fixture may hold the note unconfirmed where the owner's is confirmed: the lead follows the field
                let confirmed = list(f, "rests_on.interpretations").iter().any(|i| at(i, "active").and_then(Value::as_bool) == Some(true) && at(i, "confirmed").and_then(Value::as_bool) == Some(true));
                let lead = if confirmed { "Note:" } else if lead == "Note:" { "A note, not confirmed:" } else { lead };
                assert_eq!(said, format!("{lead} {words}"), "{surface:?} {id}");
                assert!(impact.contains(&format!("aria-describedby=\"{}\">{lead}</span>", w::glossary_id(w::RECORDED_INTERPRETATION))), "{surface:?} {id}");
                assert!(text_of(impact).contains("on question q1") || text_of(impact).contains("on question q"), "{surface:?} {id}");
                for old in ["reading", "Recorded interpretation", "not confirmed\u{b7}", "note: "] {
                    assert!(!said.contains(old), "{surface:?} {id}: {old} in {said}");
                }
            }
        }
        // a note whose rule is not met is one for when it is
        let mut doc = example("owner");
        let ip = dig(&mut doc, &["findings", "c1", "rests_on", "interpretations"]).as_list_mut().unwrap()[0].as_dict_mut().unwrap();
        ip.insert("active".into(), Value::Bool(false));
        let impact = card(&all(&doc, Surface::Page), "c-c1:impact").to_string();
        // c1 is met: a note not in force there (a problem under it, or a gate) says so, never "for when it is met"
        assert!(text_of(&impact).contains("A note, not in force: INT8"), "{impact}");
        dig(&mut doc, &["findings", "c1"]).as_dict_mut().unwrap().insert("state".into(), Value::str("misses"));
        let impact = card(&all(&doc, Surface::Page), "c-c1:impact").to_string();
        assert!(text_of(&impact).contains("A note for when this rule is met: INT8"), "{impact}");
    }

    /// One rule says whether a line names a command, for the cards and the views: a whole span, a sentence's full stop
    /// after it included, never inside a word.
    /// DESIGN-PASS-1 targets 4 and P5, P8: a rule card's status is one sentence about the rule, from the finding's own
    /// fields; its reading one plain sentence with the difference's direction in words, once, in two decimals (more only
    /// where two numbers would read alike); what it passes on, a third. The cifar demo's noninferior rule reads as the
    /// brief has it.
    #[test]
    fn a_rule_says_its_state_and_reading_in_sentences() {
        let doc = example("owner");
        let want = [
            ("c1", "Met: INT8 is 0.0029 lower than the FP32 baseline (-0.0046 to -0.0012), 0.0071 past the -0.01 it asks for.", "INT8 is 0.0029 lower than the FP32 baseline (-0.0046 to -0.0012), 3 seeds."),
            ("c2", "Not reproduced: FP32 baseline: 0.7597 (0.7591 to 0.7603), not within 0.001 of the cited 0.7613.", "FP32 baseline: 0.7597 (0.7591 to 0.7603), 3 seeds."),
            ("c4", "Met: INT8: 0.994 share (no interval yet), 0.004 share past the 0.99 share it asks for.", "INT8: 0.994 share (no interval yet), 3 runs."),
            ("c5", "Not met: 1 of 4 design cells is outside the rule (calib images 128).", ""),
            ("c6", "Not met: INT8 against the FP32 baseline: 0.64 (no interval yet), over the 0.6 the rule allows.", "INT8 against the FP32 baseline: 0.64 (no interval yet), 1 run."),
        ];
        for (cid, status, reading) in want {
            let f = at(&doc, &format!("findings.{cid}")).unwrap();
            assert_eq!(sentence_text(&verdict(f)), status, "{cid}");
            assert_eq!(sentence_text(&reading_sentence(f)), reading, "{cid}");
        }
        // the cifar demo's c-pt-512: a noninferior rule read on the interval's worse end, in percent
        let mut doc = example("owner");
        let f = dig(&mut doc, &["findings", "c1"]).as_dict_mut().unwrap();
        f.insert("rule".into(), Value::str("top1: int8 vs fp32 no worse than 1, its interval's worse bound inside 1"));
        f.insert("check_band".into(), crate::obj! {"comparator" => "at least", "target" => -1.0, "tolerance" => Value::Null});
        let r = f.get_mut("reading").unwrap().as_dict_mut().unwrap();
        for (k, v) in [("estimate", -0.07667), ("lo", -0.2264), ("hi", 0.07307)] {
            r.insert(k.into(), Value::Float(v));
        }
        r.insert("unit_text".into(), Value::str("%"));
        r.insert("estimate_text".into(), Value::str("-0.07667"));
        r.insert("interval_text".into(), Value::str("[-0.2264, +0.07307]"));
        let f = at(&doc, "findings.c1").unwrap();
        assert_eq!(sentence_text(&verdict(f)), "Met: INT8 is 0.08 points lower than the FP32 baseline (-0.23 to +0.07); its worse end is 0.77 points past the -1 point it asks for.");
        assert_eq!(sentence_text(&reading_sentence(f)), "INT8 is 0.08 points lower than the FP32 baseline (-0.23 to +0.07), 3 seeds.");
        assert_eq!(rule_passes(f), "The interval's worse end is at least -1 point (INT8 against the FP32 baseline).");
        // the card: the status sentence under the title, the big number in two decimals with rb's digits on hover, the
        // reading's numbers drilling to the runs and nothing else in link colour
        let page = all(&doc, Surface::Page);
        let c1 = card(&page, "c-c1");
        let f1 = at(&doc, "findings.c1").unwrap();
        assert!(c1.contains(&format!("<p class=\"str\">{}</p>", sentence_html_to(&verdict(f1), "c-c1:runs", "c-c1:rule"))), "{c1}");
        assert!(c1.contains("<span class=\"e\"><span class=\"nb\" title=\"-0.07667\">-0.08</span></span><span class=\"u\"> points</span><span class=\"iv\"> (<span class=\"nb\" title=\"-0.2264\">-0.23</span> to <span class=\"nb\" title=\"+0.07307\">+0.07</span>)</span>"), "{c1}");
        // What rb measured says what the number is from, never the verdict's reading again (FIX3)
        assert!(c1.contains(&format!("<p class=\"reads\"><a href=\"#c-c1:runs\">{}</a></p>", esc(s(f1, "units_line")))), "{c1}");
        assert!(!c1.contains("<p class=\"reads\">INT8 is"), "{c1}");
        // rb's machine lines are Source details, never the Understand or Examine text (P8)
        let trace = &c1[c1.find("data-level=\"trace\"").unwrap()..];
        let above = &c1[..c1.find("data-level=\"trace\"").unwrap()];
        for raw in [s(f, "rule"), s(f, "says.comparison"), s(f, "says.timing")] {
            assert!(!text_of(above).contains(raw) && text_of(trace).contains(raw), "{raw}");
        }
        for row in ["rule", "rb reads", "timing", "comparison"] {
            assert!(trace.contains(&format!("<dt>{row}</dt>")), "{row}");
        }
        assert!(text_of(above).contains("Written before its results."), "{}", text_of(above));
    }

    #[test]
    fn a_past_versions_source_view_quotes_no_line() {
        let mut doc = cited(true);
        dig(&mut doc, &["places", "place"]).as_dict_mut().unwrap().insert("version".into(), crate::pyjson::loads(r#"{"sha": "3f2a9c1"}"#).unwrap());
        let lines: Value = crate::pyjson::loads(r#"[{"n": 7, "text": "$ make. \\", "cited": true}, {"n": 8, "text": "  --calib 512 # documented", "cited": false}]"#).unwrap();
        dig(&mut doc, &["citations", "README.md:3"]).as_dict_mut().unwrap().insert("lines".into(), lines);
        let past = all(&doc, Surface::Open);
        let src = card(&past, "cite-README.md:3");
        assert!(!src.contains("srclines") && !src.contains("make") && !src.contains("calib") && text_of(src).contains(PAST_NO_QUOTE), "{src}");
        assert!(!past.contains("line 669 &lt;of") && text_of(level(src, "understand")).contains("README.md \u{b7} line 3"), "{src}");
        // drawn live, the same document quotes them
        dig(&mut doc, &["places", "place"]).as_dict_mut().unwrap().insert("version".into(), Value::Null);
        assert!(card(&all(&doc, Surface::Open), "cite-README.md:3").contains("$ make. \\"));
    }

    #[test]
    fn names_command_takes_a_sentences_full_stop() {
        let cmd = "python train.py --seed 1";
        assert!(names_command("run it again: python train.py --seed 1.", cmd));
        assert!(names_command("python train.py --seed 1. Then compare", cmd));
        assert!(names_command("python train.py --seed 1", cmd));
        assert!(!names_command("python train.py --seed 10", cmd));
        assert!(!names_command("python train.py --seed 1.5", cmd));
        assert!(names_command("python train.py --seed 1.)", cmd));
        assert!(names_command("python train.py --seed 1...", cmd));
        assert!(!names_command("python train.py --seed 1..5", cmd));
        assert!(names_command("recipe: make.", "make"));
        assert!(names_command("make.", "make"));
        assert!(!names_command("python 3.11.9", "python"));
        assert!(!names_command("evaluation data", "eval"));
    }

    /// Guarantee 38: a past version offers no command. On rb open at one, no card draws a command box, a copy slot, a
    /// recipe, a command as code or a command's words, and a call's Decide offers nothing to run; drawn live, the same
    /// document keeps them. Every part a card links to stays (a resource's recipe keeps the file it comes from).
    #[test]
    fn a_past_version_draws_no_command_on_a_card() {
        let past = crate::pyjson::loads(include_str!("../../../tests/fixtures/shell/int8-demo.past.view.json")).unwrap();
        let mut live = past.clone();
        dig(&mut live, &["places", "place"]).as_dict_mut().unwrap().insert("version".into(), Value::Null);
        // a call's actions, which the past version's document holds none of, offered live and at the past version alike
        let action = crate::pyjson::loads(r#"{"action": "accept", "label": "Accept", "consequence": "accepted", "line": "rb decide c2 accept --why \"...\""}"#).unwrap();
        let mut past_acts = past.clone();
        for doc in [&mut live, &mut past_acts] {
            // a receipt row that says only a command the record holds
            let row = Value::List(vec![Value::str("check"), Value::str("python quantize.py --seed 1 --calib 512"), Value::List(Vec::new())]);
            dig(doc, &["runs", "runs1", "receipt_rows"]).as_list_mut().unwrap().push(row);
            for c in dig(doc, &["calls"]).as_list_mut().unwrap() {
                c.as_dict_mut().unwrap().insert("actions".into(), Value::List(vec![action.clone()]));
            }
        }
        // short commands the record holds, which name no part of a line that is not one ("evaluation data", "python
        // 3.11.9", "sha256"); a resource got again by a command alone, which cites no file
        for doc in [&mut live, &mut past_acts] {
            dig(doc, &["runs", "runs2", "command"]).as_dict_mut().unwrap().insert("text".into(), Value::str("eval"));
            for c in ["python", "sh"] {
                let step = crate::pyjson::loads(&format!(r#"{{"command": "{c}"}}"#)).unwrap();
                dig(doc, &["runs", "runs3", "reproduce", "steps"]).as_list_mut().unwrap().push(step);
            }
            let res = dig(doc, &["resources", "ckpt-int8-s1"]).as_dict_mut().unwrap();
            res.get_mut("acquisition").unwrap().as_dict_mut().unwrap().insert("from".into(), Value::str(""));
        }
        let (live_html, html) = (all(&live, Surface::Open), all(&past_acts, Surface::Open));
        for kept in ["evaluation data", "python 3.11.9", "sha256", PAST_NO_COMMAND, "id=\"r-ckpt-int8-s1:recipe\""] {
            assert!(html.contains(kept), "a past version drops {kept}");
        }
        assert!(!live_html.contains(PAST_NO_COMMAND));
        // that no command was recorded is no command
        assert!(text_of(card(&html, "run-runs1")).contains("command (as given)not recorded"), "{}", text_of(card(&html, "run-runs1")));
        let marks = ["class=\"cmdbox", "class=\"cmd", "data-copybtn", "<code>", "rb decide", "python get_data.py", "python quantize.py", "how rb knows", "data-act=\"pick\""];
        for m in ["class=\"cmdbox", "data-copybtn", "rb decide", "python get_data.py", "python quantize.py"] {
            assert!(live_html.contains(m), "drawn live, the cards offer {m}");
        }
        for m in marks {
            assert!(!html.contains(m), "a past version's cards draw {m}");
        }
        assert!(live_html.contains("<dt>check</dt>") && !html.contains("<dt>check</dt>"), "a row that said only a command is drawn empty");
        // the inspector's card, as rb open serves it one by one
        for anchor in ["run-runs1", "r-imagenet-val", "r-ckpt-int8-s1", "call-c2", "c-c2"] {
            let one = one(&past_acts, Surface::Open, anchor).unwrap_or_default();
            assert!(!marks.iter().any(|m| one.contains(m)), "{anchor}: {one}");
        }
        // every part a card links to is still drawn: a recipe's file, a call's Decide, why rb asks
        for part in ["id=\"r-imagenet-val:recipe\"", "scripts/README.md:5", "id=\"c-c2:decide\"", PAST_READ_ONLY] {
            assert!(html.contains(part), "{part}");
        }
        for href in html.match_indices("href=\"#").map(|(i, _)| &html[i + 7..i + 7 + html[i + 7..].find('"').unwrap()]) {
            if href.contains(":recipe") || href.contains(":decide") {
                assert!(html.contains(&format!("id=\"{href}\"")), "a link to {href} dead-ends");
            }
        }
        // a page never draws rb open's past version, whatever the document holds
        assert!(all(&past_acts, Surface::Page).contains("rb decide"));
    }

    /// Every glossary key a card asks for is one the glossary holds: an unknown key draws its word plain, with no hover
    /// (plain words pass 2: "rule (noun)" left 8 rule chips without one).
    #[test]
    fn every_term_a_card_asks_for_is_in_the_glossary() {
        let src = include_str!("nodes.rs");
        let mut keys = Vec::new();
        for (i, _) in src.match_indices("term(\"") {
            let rest = &src[i + 6..];
            let key = &rest[..rest.find('"').unwrap()];
            keys.push(key);
        }
        assert!(keys.len() >= 5, "{keys:?}");
        for key in keys {
            assert!(w::hint(key).is_some(), "{key}");
        }
    }

    /// rb's rule phrases a card draws read in the screens' words (plain words pass 2): the standing line, the result,
    /// the rule row's lead and a call's "what rb read"; the colour and every anchor keep rb's word.
    #[test]
    fn a_cards_rule_words_are_plain() {
        let mut doc = example("owner");
        dig(&mut doc, &["findings", "c1", "says"]).as_dict_mut().unwrap().insert("integrity".into(), Value::str("ok"));
        let f = dig(&mut doc, &["findings", "c3"]).as_dict_mut().unwrap();
        f.insert("standing".into(), Value::str("criterion untested"));
        f.insert("state".into(), Value::str("untested"));
        f.insert("good".into(), Value::Bool(false));
        let says = f.get_mut("says").unwrap().as_dict_mut().unwrap();
        says.insert("rule".into(), Value::str("criterion undetermined: 2 units; the criterion needs 3"));
        let r = dig(&mut doc, &["findings", "c3", "reading"]).as_dict_mut().unwrap();
        r.insert("result".into(), Value::str("untested"));
        let page = all(&doc, Surface::Page);
        let c3 = card(&page, "c-c3");
        // the card says its verdict once (FIX2): no state words after it; checks are a row of The rule as written
        assert!(!c3.contains("<p class=\"sti\">"), "{c3}");
        assert_eq!(text_of(level(c3, "understand")).to_lowercase().matches("not tested").count(), 1, "{}", text_of(level(c3, "understand")));
        // rb's rule line is a source detail now, in the screens' words
        assert!(c3.contains("<dt>rb reads</dt><dd>2 units; the criterion needs 3</dd>"), "{c3}");
        let c1 = card(&page, "c-c1");
        let rule = &c1[c1.find("id=\"c-c1:rule\"").unwrap()..];
        assert!(rule[..rule.find("</section>").unwrap()].contains(&format!("<dt>{}</dt><dd class=\"dim\">ok</dd>", term("integrity", "checks"))), "{c1}");
        let call = card(&page, "call-c3");
        // the call says the rule's state in the rule card's own sentence, never rb's rule line (P8)
        let f3 = at(&doc, "findings.c3").unwrap();
        assert!(call.contains(&format!("<p class=\"verdict\">{}</p>", sentence_html_to(&verdict(f3), "c-c3:runs", "c-c3:rule"))) && !call.contains("the criterion needs 3"), "{call}");
        for cid in ["c1", "c3", "c5"] {
            let st = card(&page, &finding_anchor(cid));
            let st = &st[st.find("<p class=\"str\">").unwrap()..];
            let st = text_of(&st[..st.find("</p>").unwrap()]);
            for old in ["criterion", "criteria", "untested", "undetermined"] {
                assert!(!st.contains(old), "{cid}: {st}");
            }
        }
    }

    /// OWNER-READER: on an export a call is recorded from its call card, its actions said once there; a finding a call
    /// waits on says why, for every viewer (a reader reads it as status, under "Still open": every open item stays), and
    /// sends the owner and an export with no viewer named to the call card's Decide. A reader is offered no action and
    /// no rb command anywhere on it. rb page and rb open keep the finding's own Decide, with no viewer's marks.
    #[test]
    fn a_reader_reads_why_a_call_is_open_and_is_offered_no_action() {
        let doc = example("owner");
        let html = all(&doc, Surface::Export);
        for viewer in ["none", "owner", "reader"] {
            let page = as_viewer(&html, viewer);
            let c2 = card(&page, "c-c2");
            let part = &c2[c2[..c2.find("id=\"c-c2:decide\"").expect("the part")].rfind('<').unwrap()..];
            let text = text_of(part);
            // the card's verdict says the state; its reason, the state again, is not said here (FIX2)
            assert!(!text.contains("Not reproduced") && !text.contains("estimate 0.7597 outside 0.7613"), "{viewer}: {text}");
            assert_eq!(text.contains(NOTHING_RECORDED), viewer == "reader", "{viewer}: {text}");
            assert_eq!(text.starts_with("Record a call"), viewer != "reader", "{viewer}: {text}");
            assert_eq!(text.starts_with(STILL_OPEN), viewer == "reader", "{viewer}: {text}");
            assert_eq!(part.contains("<a href=\"#call-c2:decide\">its call card</a>"), viewer != "reader", "{viewer}");
            assert!(!c2.contains("rb decide") && !c2.contains("<button") && !c2.contains("<input"), "{viewer}: {c2}");
            // its tab says what the part is to each viewer
            let tab = if viewer == "reader" { "<span data-vw=\"reader\">Still open</span>" } else { "<span data-vw=\"none owner\">Decide</span>" };
            assert!(c2.contains(&format!("<a href=\"#c-c2:decide\">{tab}</a>")), "{viewer}: {c2}");
            // the call card is where the call is recorded: its actions for the owner and an export with no viewer
            let call = card(&page, "call-c2");
            assert_eq!(call.contains("id=\"call-c2:decide\"") && call.contains("rb decide c2 accept --why"), viewer != "reader", "{viewer}");
        }
        for surface in [Surface::Page, Surface::Open] {
            let page = all(&doc, surface);
            assert!(!page.contains("data-vw=") && card(&page, "c-c2").contains("rb decide c2 accept --why"), "{surface:?}");
        }
        // a call with no action to offer still says why; its card is the place it lands
        let mut bare = doc.clone();
        for c in dig(&mut bare, &["calls"]).as_list_mut().unwrap() {
            c.as_dict_mut().unwrap().insert("actions".into(), Value::List(Vec::new()));
        }
        let c2 = as_viewer(&all(&bare, Surface::Export), "reader");
        assert!(text_of(card(&c2, "c-c2")).contains("Not reproduced") && card(&c2, "c-c2").contains("id=\"c-c2:decide\""));
        let c2 = all(&bare, Surface::Export);
        assert!(card(&c2, "c-c2").contains("<a href=\"#call-c2\">its call card</a>"));
        // and on rb page and rb open, where the finding's own tab and review link land on it
        for surface in [Surface::Page, Surface::Open] {
            let c2 = all(&bare, surface);
            let c2 = card(&c2, "c-c2");
            assert!(c2.contains("href=\"#c-c2:decide\"") && c2.contains("id=\"c-c2:decide\"") && text_of(level(c2, "decide")).contains(NOTHING_RECORDED) && !text_of(level(c2, "decide")).contains("Why this needs a call"), "{surface:?}");
        }
    }

    /// The owner's example with M's citations (CITE-FIELDS.md, Oct 25), as the export gives them: README.md:3 quoted
    /// and cited by rule c1's statement and the project's note n1, a log line not quoted on a shared page, a line the
    /// file does not have, and a file rb quotes more lines of than 8. `surface` "open" gives the log an editor link.
    fn cited(open: bool) -> Value {
        let mut doc = example(if open { "live" } else { "owner" });
        let lines = |from: i64, cited: &[i64]| {
            let list: Vec<String> = (from..from + 3)
                .map(|n| format!(r#"{{"n": {n}, "text": "line {n} <of> the file", "cited": {}}}"#, cited.contains(&n)))
                .collect();
            format!("[{}]", list.join(", "))
        };
        let cite = |key: &str, path: &str, name: &str, folder: &str, line: i64, end: i64, lines: &str, extra: &str| {
            let label = if end == line { format!("{path} \u{b7} line {line}") } else { format!("{path} \u{b7} lines {line}-{end}") };
            format!(
                r#""{key}": {{"key": "{key}", "raw": ["{key}"], "path": "{path}", "name": "{name}", "folder": "{folder}", "line": {line}, "end": {end},
                "label": "{label}", "commit": "6adf8a5c0ffee00000000000000000000000000a", "commit_short": "6adf8a5",
                "read": "read at commit 6adf8a5", "sha256": "7029e0be441faa31bb434075899b69a46f0c607ff2e05ff232b06a971ad19e63",
                "tracked": true, "quoted": true, "lines": {lines}, "cut": 0, "missing": "", "not_quoted": "", "open_url": "",
                "cited_by": [], {extra}}}"#
            )
        };
        let all = [
            cite("README.md:3", "README.md", "README.md", "", 3, 3, &lines(2, &[3]), r#""_by": [{"kind": "criterion", "id": "c1"}, {"kind": "note", "id": "n1"}]"#),
            cite("logs/agent.log:669", "logs/agent.log", "agent.log", "logs/", 669, 669, &lines(668, &[669]), &format!(r#""_x": "{}""#, if open { "open" } else { "export" })),
            cite("README.md:40", "README.md", "README.md", "", 40, 40, "[]", r#""_x": "missing""#),
            cite("docs/plan.md:10-20", "docs/plan.md", "plan.md", "docs/", 10, 20, &lines(9, &[10, 11]), r#""_x": "cut""#),
        ];
        let mut c = crate::pyjson::loads(&format!("{{{}}}", all.join(", "))).unwrap();
        let m = c.as_dict_mut().unwrap();
        let by: Value = crate::pyjson::loads(r#"[{"kind": "criterion", "id": "c1"}, {"kind": "note", "id": "n1"}]"#).unwrap();
        m.get_mut("README.md:3").unwrap().as_dict_mut().unwrap().insert("cited_by".into(), by);
        let log = m.get_mut("logs/agent.log:669").unwrap().as_dict_mut().unwrap();
        if open {
            log.insert("open_url".into(), Value::str("vscode://file/home/ana/int8/logs/agent.log:669"));
            log.insert("read".into(), Value::str("read from the file as it is now; git does not track it"));
        } else {
            log.insert("quoted".into(), Value::Bool(false));
            log.insert("lines".into(), Value::List(Vec::new()));
            log.insert("sha256".into(), Value::str(""));
            log.insert("read".into(), Value::str(""));
            log.insert("not_quoted".into(), Value::str("Not quoted on a shared page"));
        }
        let miss = m.get_mut("README.md:40").unwrap().as_dict_mut().unwrap();
        miss.insert("quoted".into(), Value::Bool(false));
        miss.insert("missing".into(), Value::str("README.md has no line 40 at commit 6adf8a5"));
        miss.insert("read".into(), Value::str(""));
        m.get_mut("docs/plan.md:10-20").unwrap().as_dict_mut().unwrap().insert("cut".into(), Value::Int(9));
        doc.as_dict_mut().unwrap().insert("citations".into(), c);
        let st = dig(&mut doc, &["findings", "c1"]).as_dict_mut().unwrap();
        st.insert("statement".into(), Value::str("int8 holds top1 within 1 point of fp32, as README.md:3 says (see logs/agent.log:669 and README.md:40)"));
        doc
    }

    /// CITE item 3: every citation the document resolved has its Source view at `cite-<key>`, where views.rs's chips
    /// lead: the file by name with its line, the cited lines with their numbers and the cited ones marked, the commit
    /// it was read at, the sha256 in Source details, and which rules and notes cite it, each a link. A card's own author
    /// text draws each citation as the same chip. `one` and `card` serve it to rb open's inspector.
    #[test]
    fn a_citation_opens_its_source_view() {
        let doc = cited(false);
        let page = all(&doc, Surface::Export);
        let src = card(&page, "cite-README.md:3");
        assert!(src.starts_with("<div class=\"card source\" id=\"cite-README.md:3\" data-kind=\"source\" data-home=\"q-q1\""), "{src}");
        assert!(level(src, "understand").contains("<b>README.md</b> \u{b7} line 3"), "{src}");
        let lines = &src[src.find("id=\"cite-README.md:3:lines\"").unwrap()..];
        assert!(lines.contains("<pre class=\"srclines\"><span class=\"sl\"><span class=\"ln\">2</span><span class=\"cm\"> </span> line 2 &lt;of&gt; the file</span>"), "{lines}");
        assert!(lines.contains("<span class=\"sl on\"><span class=\"ln\">3</span><span class=\"cm\" title=\"cited\">\u{25b8}</span> line 3 &lt;of&gt; the file</span>"), "{lines}");
        assert!(text_of(lines).contains("read at commit 6adf8a5"));
        let rx = level(src, "trace");
        assert!(rx.contains("<dt>sha256</dt><dd class=\"mono\">7029e0be") && rx.contains("<dt>commit</dt><dd class=\"mono\">6adf8a5c0ffee") && !level(src, "examine").contains("7029e0be"), "{rx}");
        let by = &src[src.find(":cited-by\"").unwrap()..];
        assert!(by.contains("Rule <a href=\"#c-c1\">") && by.contains("<a class=\"mono id\" href=\"#c-c1\">c1</a>"), "{by}");
        // a note by what it is on, never by its id (FIX7)
        assert!(by.contains("A note on the project") && !text_of(by).contains("n1"), "{by}");
        // a card's author text: each citation a chip that opens its Source view, the rest verbatim
        let c1 = card(&page, "c-c1");
        let chip = "<a class=\"cite\" href=\"#cite-README.md:3\" data-cite=\"README.md:3\" title=\"README.md\"><b>README.md</b> \u{b7} line 3</a>";
        assert!(c1.contains(&format!("as {chip} says")), "{c1}");
        assert!(c1.contains("<a class=\"cite nq\" href=\"#cite-logs/agent.log:669\"") && c1.contains("<span class=\"cdir\">logs/</span><b>agent.log</b>"), "{c1}");
        assert!(c1.contains("<span class=\"cmiss\">(Not quoted on a shared page)</span>") && c1.contains("(README.md has no line 40 at commit 6adf8a5)"), "{c1}");
        // a log a shared page may not quote: its file and line, said so, no line of it; one rb could not read says so
        let log = card(&page, "cite-logs/agent.log:669");
        assert!(text_of(log).contains("Not quoted on a shared page") && !log.contains("srclines") && !log.contains("line 669 &lt;of"), "{log}");
        assert!(!log.contains("Open the file") && text_of(level(log, "understand")).contains("logs/agent.log \u{b7} line 669"));
        let miss = card(&page, "cite-README.md:40");
        assert!(text_of(miss).contains("README.md has no line 40 at commit 6adf8a5") && !miss.contains("srclines"), "{miss}");
        assert!(text_of(card(&page, "cite-docs/plan.md:10-20")).contains("9 more cited lines not shown: rb quotes at most 8 lines of a citation."));
        assert!(text_of(card(&page, "cite-docs/plan.md:10-20")).contains("Only rb's own lines cite it here"));
        // each once, and the inspector gets the same card by its anchor and by its kind
        for key in ["README.md:3", "logs/agent.log:669", "README.md:40", "docs/plan.md:10-20"] {
            let id = source_anchor(key);
            assert_eq!(page.matches(&format!(" id=\"{id}\"")).count(), 1, "{id}");
            assert_eq!(one(&doc, Surface::Export, &id).as_deref(), Some(card(&page, &id)), "{id}");
            assert_eq!(super::card(&doc, "source", key, Surface::Export).as_deref(), Some(card(&page, &id)), "{id}");
        }
        // as M's finder reads them (ledger::cite::spans): none run into a longer line number, a word or a longer path, or
        // after `$`, `%`, `{` or `=`; one before a decimal, a comma or a letter beyond ASCII is one
        let cases = [
            ("README.md:30", 0), ("README.md:3x", 0), ("README.md:3_", 0), ("README.md:3:4", 0), ("docs/README.md:3", 0), ("x=README.md:3", 0),
            ("{README.md:3}", 0), ("$README.md:3", 0), ("%README.md:3", 0), ("(README.md:3).", 1), ("README.md:3, then", 1), ("README.md:3.5", 1),
            ("README.md:3,5", 1), ("\u{e9}README.md:3", 1), ("README.md:3\u{e9}", 1),
        ];
        for (said, chips) in cases {
            let mut d = doc.clone();
            dig(&mut d, &["findings", "c1"]).as_dict_mut().unwrap().insert("statement".into(), Value::str(said));
            assert_eq!(card(&all(&d, Surface::Export), "c-c1").matches("class=\"cite").count(), chips, "{said}");
        }
        // a call that cites a file is listed by whose call it is on each surface, with marks for viewers only on an export
        let mut d = doc.clone();
        let by: Value = crate::pyjson::loads(r#"[{"kind": "call", "id": "c2"}]"#).unwrap();
        dig(&mut d, &["citations", "README.md:3"]).as_dict_mut().unwrap().insert("cited_by".into(), by);
        let on_page = all(&d, Surface::Page);
        assert!(card(&on_page, "cite-README.md:3").contains("<li>Your call on <a href=\"#call-c2\">c2</a></li>") && !on_page.contains("data-vw"));
        assert!(text_of(card(&as_viewer(&all(&d, Surface::Export), "reader"), "cite-README.md:3")).contains("An open call on c2"));
        // a citer's id drills where its title does, by its kind, though a resource shares the experiment's id
        let mut d = doc.clone();
        let by: Value = crate::pyjson::loads(r#"[{"kind": "experiment", "id": "e1"}]"#).unwrap();
        dig(&mut d, &["citations", "README.md:3"]).as_dict_mut().unwrap().insert("cited_by".into(), by);
        let r = entries(&d, "resources")[0].1.clone();
        dig(&mut d, &["resources"]).as_dict_mut().unwrap().insert("e1".into(), r);
        let src = card(&all(&d, Surface::Export), "cite-README.md:3").to_string();
        assert!(src.contains("<a class=\"mono id\" href=\"#x-e1\">e1</a>") && !src.contains("href=\"#r-e1\""), "{src}");
        // a document with no citations draws no Source view, and its author text as before
        let mut plain = example("owner");
        plain.as_dict_mut().unwrap().shift_remove("citations");
        let plain = all(&plain, Surface::Export);
        assert!(!plain.contains("data-kind=\"source\"") && !plain.contains("class=\"cite"));
    }

    /// On rb open the Source view quotes the local file and opens it in the user's editor (`open_url`, M's vscode://
    /// link); a page never offers it, and a past version (whose checkout rb takes away) neither.
    #[test]
    fn rb_open_opens_the_cited_file() {
        let doc = cited(true);
        let page = all(&doc, Surface::Open);
        let log = card(&page, "cite-logs/agent.log:669");
        assert!(log.contains("<a class=\"openfile\" href=\"vscode://file/home/ana/int8/logs/agent.log:669\">Open the file</a>"), "{log}");
        assert!(log.contains("srclines") && text_of(log).contains("read from the file as it is now; git does not track it"));
        // the editor is the user's own: no product is named
        assert!(text_of(log).contains("Open the file in your editor, at line 669") && !text_of(&page).contains("VS Code"), "{}", text_of(log));
        assert!(!all(&doc, Surface::Page).contains("Open the file"));
        let mut past = doc.clone();
        past.as_dict_mut().unwrap().insert("places".into(), crate::pyjson::loads(r#"{"place": {"version": {"sha": "4364dc8c1a2b"}}}"#).unwrap());
        assert!(!all(&past, Surface::Open).contains("Open the file"));
    }

    /// The math record's c1 as the owner read it (plain words pass 4): the verdict says the state word first, the
    /// reading, then how far it stands from the rule's number, in one sentence and in points for a difference of
    /// percentages.
    fn step_by_step(estimate: f64, lo: f64, hi: f64, state: &str, standing: &str) -> Value {
        let mut doc = example("owner");
        let f = dig(&mut doc, &["findings", "c1"]).as_dict_mut().unwrap();
        f.insert("state".into(), Value::str(state));
        f.insert("standing".into(), Value::str(standing));
        f.insert("subject_label".into(), Value::str("step_by_step"));
        f.insert("reference_label".into(), Value::str("plain baseline"));
        f.insert("rule".into(), Value::str("change.accuracy at least 5"));
        f.insert("design_cells".into(), Value::List(vec![]));
        f.insert("units_line".into(), Value::str("3 seeds from 6 runs"));
        dig(&mut doc, &["findings", "c1", "rests_on"]).as_dict_mut().unwrap().insert("call".into(), Value::str(""));
        let r = dig(&mut doc, &["findings", "c1", "reading"]).as_dict_mut().unwrap();
        r.insert("estimate".into(), Value::Float(estimate));
        r.insert("lo".into(), Value::Float(lo));
        r.insert("hi".into(), Value::Float(hi));
        r.insert("unit_text".into(), Value::str("percent"));
        r.insert("result".into(), Value::str("gain"));
        let b = dig(&mut doc, &["findings", "c1", "check_band"]).as_dict_mut().unwrap();
        b.insert("comparator".into(), Value::str("at least"));
        b.insert("target".into(), Value::Float(5.0));
        b.insert("tolerance".into(), Value::Null);
        at(&doc, "findings.c1").unwrap().clone()
    }

    #[test]
    fn a_verdict_says_the_state_the_reading_and_how_far_from_the_rule() {
        let f = step_by_step(3.6, 0.619, 6.581, "misses", "criterion not met \u{b7} gain");
        assert_eq!(sentence_text(&verdict(&f)), "Not met: step by step is 3.60 points higher than the plain baseline (+0.62 to +6.58), short of the 5 points the rule asks for.");
        let f = step_by_step(6.4, 5.2, 7.6, "meets", "criterion met \u{b7} gain");
        assert_eq!(sentence_text(&verdict(&f)), "Met: step by step is 6.40 points higher than the plain baseline (+5.20 to +7.60), 1.40 points past the 5 points it asks for.");
        // met on its estimate alone: said so after the reading
        let f = step_by_step(6.4, 4.2, 8.6, "meets", "estimate meets criterion \u{b7} gain");
        assert!(sentence_text(&verdict(&f)).ends_with("1.40 points past the 5 points it asks for; its interval does not show it yet."));
        // the state words, each first
        for (state, word) in [("reproduced", "Reproduced"), ("not reproduced", "Not reproduced"), ("undetermined", "Can't tell yet"), ("untested", "Not tested")] {
            let f = step_by_step(3.6, 0.619, 6.581, state, state);
            assert!(sentence_text(&verdict(&f)).starts_with(&format!("{word}:")), "{state}: {}", verdict(&f));
        }
    }

    /// FIX3, one wording per thing: the verdict's threshold says its unit; a variant compared with itself names each side
    /// by its selectors; a percentage reads "%"; a metric with no arm reads by its title; the margin keeps the reading's
    /// digits and unit; a rule card says its reading's sentence once.
    #[test]
    fn a_rule_card_says_each_thing_one_way() {
        let f = step_by_step(3.6, 0.619, 6.581, "misses", "criterion not met \u{b7} gain");
        assert!(sentence_text(&verdict(&f)).ends_with("short of the 5 points the rule asks for."), "{}", verdict(&f));
        assert_eq!(rule_passes(&f), "The estimate is at least 5 points (step by step against the plain baseline).");
        // a self-comparison
        let mut w = step_by_step(-0.05, 0.0, 0.0, "meets", "estimate meets criterion \u{b7} point only");
        let d = w.as_dict_mut().unwrap();
        d.insert("subject_label".into(), Value::str("wide"));
        d.insert("reference_label".into(), Value::str("wide"));
        d.insert("rule".into(), Value::str("val_loss: wide vs wide [where width=512; subject where loops=8; reference where loops=6] \u{2264} 0"));
        let r = dig(&mut w, &["reading"]).as_dict_mut().unwrap();
        r.insert("lo".into(), Value::Null);
        r.insert("hi".into(), Value::Null);
        r.insert("unit_text".into(), Value::str("nats"));
        let b = dig(&mut w, &["check_band"]).as_dict_mut().unwrap();
        b.insert("comparator".into(), Value::str("at most"));
        b.insert("target".into(), Value::Float(0.0));
        let v = sentence_text(&verdict(&w));
        assert!(v.starts_with("Met: wide at loops 8 is 0.05 nats lower than wide at loops 6 (no interval yet), 0.05 nats under the 0 nats it allows"), "{v}");
        assert!(rule_passes(&w).ends_with("(wide at loops 8 against wide at loops 6)."), "{}", rule_passes(&w));
        // a level in percent, and a metric with no arm by its title; a unit that is the metric's own name is none
        let mut l = step_by_step(0.81, 0.73, 0.89, "undetermined", "criterion undetermined \u{b7} measured");
        l.as_dict_mut().unwrap().insert("reference_label".into(), Value::str(""));
        l.as_dict_mut().unwrap().insert("subject_label".into(), Value::str("wide"));
        assert_eq!(sentence_text(&verdict(&l)), "Can't tell yet: wide: 0.81% (0.73% to 0.89%).");
        l.as_dict_mut().unwrap().insert("subject_label".into(), Value::str(""));
        dig(&mut l, &["row"]).as_dict_mut().unwrap().insert("metric".into(), Value::str("top1"));
        dig(&mut l, &["reading"]).as_dict_mut().unwrap().insert("unit_text".into(), Value::str("top1"));
        assert_eq!(sentence_text(&verdict(&l)), "Can't tell yet: Top-1: 0.81 (0.73 to 0.89).");
        // the card: the reading's sentence once (the verdict), what it is from once, the margin in the reading's digits
        let mut doc = example("owner");
        let mut m = step_by_step(3.6, 0.619, 6.581, "misses", "criterion not met \u{b7} gain");
        dig(&mut m, &["row"]).as_dict_mut().unwrap().insert("margin".into(), Value::str("margin -1.4"));
        dig(&mut doc, &["findings"]).as_dict_mut().unwrap().insert("c1".into(), m);
        let page = all(&doc, Surface::Page);
        let text = text_of(card(&page, "c-c1"));
        assert_eq!(text.matches("3.60 points higher than the plain baseline").count(), 1, "{text}");
        let c = card(&page, "c-c1");
        let measured = &c[c.find("id=\"c-c1:reading\"").unwrap()..];
        let measured = text_of(&measured[..measured.find("</section>").unwrap()]);
        assert_eq!(measured.matches("3 seeds from 6 runs").count(), 1, "{measured}");
        assert!(card(&page, "c-c1").contains("<dt>margin</dt><dd><a href=\"#c-c1:reading\">-1.40 points</a></dd>"), "{text}");
        // the margin in the verdict's digits, whatever digits rb's own margin text has ("1.412" for a 1.41 the verdict says)
        let mut doc = example("owner");
        let mut m = step_by_step(6.4123, 5.2, 7.6, "meets", "criterion met \u{b7} gain");
        dig(&mut m, &["row"]).as_dict_mut().unwrap().insert("margin".into(), Value::str("margin 1.412"));
        assert!(sentence_text(&verdict(&m)).contains("1.41 points past the 5 points it asks for"), "{}", verdict(&m));
        dig(&mut doc, &["findings"]).as_dict_mut().unwrap().insert("c1".into(), m);
        let page = all(&doc, Surface::Page);
        assert!(card(&page, "c-c1").contains("<dt>margin</dt><dd><a href=\"#c-c1:reading\">1.41 points</a></dd>"), "{}", card(&page, "c-c1"));
        // the tolerance with its unit, as the verdict says it; a ratio has no unit
        let mut e = step_by_step(-0.5, -0.6, -0.4, "reproduced", "reproduced");
        let b = dig(&mut e, &["check_band"]).as_dict_mut().unwrap();
        b.insert("comparator".into(), Value::str("within"));
        b.insert("target".into(), Value::Float(-0.5));
        b.insert("tolerance".into(), Value::Float(0.05));
        assert!(rule_passes(&e).starts_with("The estimate is within 0.05 points of -0.5 points"), "{}", rule_passes(&e));
        dig(&mut e, &["row"]).as_dict_mut().unwrap().insert("metric".into(), Value::str("ratio.latency_ms"));
        dig(&mut e, &["reading"]).as_dict_mut().unwrap().insert("unit_text".into(), Value::str("ms"));
        assert!(!sentence_text(&verdict(&e)).contains(" ms"), "{}", verdict(&e));
        // an analysis card's big number says a difference of percentages in points, as the rule card does
        let mut doc = example("owner");
        let c4 = dig(&mut doc, &["findings", "c4"]).as_dict_mut().unwrap();
        c4.insert("reference_label".into(), Value::str("fp32 baseline"));
        let r = dig(&mut doc, &["findings", "c4", "reading"]).as_dict_mut().unwrap();
        r.insert("unit_text".into(), Value::str("%"));
        r.insert("estimate_text".into(), Value::str("+0.08"));
        let page = all(&doc, Surface::Page);
        let a1 = card(&page, &analysis_anchor("a1"));
        assert!(a1.contains("<span class=\"u\"> points</span>") && !a1.contains("<span class=\"u\">%</span>"), "{a1}");
        // an estimate that is 0 but for floating-point noise reads in two decimals, its margin with it
        let mut z = step_by_step(1e-17, -0.657241, 0.657241, "meets", "estimate meets criterion \u{b7} unresolved");
        dig(&mut z, &["reading"]).as_dict_mut().unwrap().insert("unit_text".into(), Value::str(""));
        let b = dig(&mut z, &["check_band"]).as_dict_mut().unwrap();
        b.insert("comparator".into(), Value::str("at most"));
        b.insert("target".into(), Value::Float(0.0));
        assert_eq!(reading_digits(&z), 2);
        assert!(sentence_text(&verdict(&z)).starts_with("Met: step by step is the same as the plain baseline (-0.66 to +0.66)"), "{}", verdict(&z));
        // every rule card of the demo: its reading's sentence at most once
        let doc = example("owner");
        let page = all(&doc, Surface::Page);
        for (id, f) in at(&doc, "findings").and_then(Value::as_dict).unwrap() {
            let Some((said, _)) = reading_parts(f) else { continue };
            let said = sentence_text(&said);
            assert!(text_of(card(&page, &format!("c-{id}"))).matches(&said).count() <= 1, "{id}: {said}");
        }
    }

    /// FIX4: one wording per thing on a card. A level's bounds keep its unit; the big number, the margin and the verdict
    /// round one number one way; a variant is its plain name in Runs it rests on and on a run's card, a metric its
    /// title; a level's runs are never "the difference"; the figure's rule label has its unit.
    #[test]
    fn a_card_says_one_number_one_way() {
        // a level's bounds keep their unit, any unit
        let mut l = step_by_step(7.1, 6.9, 7.3, "undetermined", "criterion undetermined \u{b7} measured");
        l.as_dict_mut().unwrap().insert("reference_label".into(), Value::str(""));
        l.as_dict_mut().unwrap().insert("subject_label".into(), Value::str("int8"));
        dig(&mut l, &["reading"]).as_dict_mut().unwrap().insert("unit_text".into(), Value::str("ms"));
        assert_eq!(sentence_text(&verdict(&l)), "Can't tell yet: INT8: 7.10 ms (6.90 ms to 7.30 ms).");
        // one number, one rounding: rb's text "-0.075" of an estimate a hair past it reads 0.08 in the verdict, the big
        // number and the margin alike, rb's text on hover
        let mut doc = example("owner");
        let mut w = step_by_step(-0.0750000004, 0.0, 0.0, "meets", "estimate meets criterion \u{b7} point only");
        let r = dig(&mut w, &["reading"]).as_dict_mut().unwrap();
        r.insert("lo".into(), Value::Null);
        r.insert("hi".into(), Value::Null);
        r.insert("unit_text".into(), Value::str("nats"));
        r.insert("estimate_text".into(), Value::str("-0.075"));
        r.insert("interval_text".into(), Value::str(""));
        let b = dig(&mut w, &["check_band"]).as_dict_mut().unwrap();
        b.insert("comparator".into(), Value::str("at most"));
        b.insert("target".into(), Value::Float(0.0));
        // M's margin comes in the verdict's digits and unit (FIX4), drawn as M words it (FIX5)
        dig(&mut w, &["row"]).as_dict_mut().unwrap().insert("margin".into(), Value::str("margin 0.08 nats"));
        assert!(sentence_text(&verdict(&w)).contains("is 0.08 nats lower than the plain baseline (no interval yet), 0.08 nats under the 0 nats it allows"), "{}", verdict(&w));
        dig(&mut doc, &["findings"]).as_dict_mut().unwrap().insert("c1".into(), w.clone());
        let page = all(&doc, Surface::Page);
        let c = card(&page, "c-c1");
        assert!(c.contains("<span class=\"e\"><span class=\"nb\" title=\"-0.075\">-0.08</span></span>"), "{c}");
        assert!(c.contains("<dt>margin</dt><dd><a href=\"#c-c1:reading\">0.08 nats</a></dd>"), "{c}");
        assert!(!text_of(c).contains("0.07"), "{}", text_of(c));
        // the figure's rule label says its number with its unit, as the verdict does
        assert!(figure::draw(&w, false).contains(">the rule: 0 nats or less</text>"), "{}", figure::draw(&w, false));
        let m = step_by_step(3.6, 0.619, 6.581, "misses", "criterion not met \u{b7} gain");
        assert!(figure::draw(&m, false).contains(">the rule: 5 points or more</text>"), "{}", figure::draw(&m, false));
        let mut lv = step_by_step(90.48, 89.92, 91.04, "meets", "criterion met \u{b7} measured");
        lv.as_dict_mut().unwrap().insert("reference_label".into(), Value::str(""));
        dig(&mut lv, &["check_band"]).as_dict_mut().unwrap().insert("target".into(), Value::Float(90.0));
        assert!(figure::draw(&lv, false).contains(">the rule: 90% or more</text>"), "{}", figure::draw(&lv, false));
        // the big reading says a level as the verdict does, each bound with its unit, in brackets
        let mut doc = example("owner");
        let mut lv = step_by_step(13.07, 11.0, 15.14, "undetermined", "criterion undetermined \u{b7} measured");
        lv.as_dict_mut().unwrap().insert("reference_label".into(), Value::str(""));
        let r = dig(&mut lv, &["reading"]).as_dict_mut().unwrap();
        r.insert("estimate_text".into(), Value::str("13.07"));
        r.insert("interval_text".into(), Value::str("[11, 15.14]"));
        dig(&mut doc, &["findings"]).as_dict_mut().unwrap().insert("c1".into(), lv);
        let page = all(&doc, Surface::Page);
        let c = card(&page, "c-c1");
        let big = &c[c.find("<div class=\"big\">").unwrap()..];
        assert_eq!(text_of(&big[..big.find("</div>").unwrap()]), "13.07% (11.00% to 15.14%)", "{c}");
        // an analysis card's big number is the number its sentence reads, in its digits
        let mut doc = example("owner");
        let c4 = dig(&mut doc, &["findings", "c4"]).as_dict_mut().unwrap();
        c4.insert("reference_label".into(), Value::str("fp32 baseline"));
        let r = dig(&mut doc, &["findings", "c4", "reading"]).as_dict_mut().unwrap();
        r.insert("unit_text".into(), Value::str("nats"));
        r.insert("estimate".into(), Value::Float(-0.0750000004));
        r.insert("estimate_text".into(), Value::str("-0.075"));
        r.insert("lo".into(), Value::Null);
        r.insert("hi".into(), Value::Null);
        let page = all(&doc, Surface::Page);
        let a1 = card(&page, &analysis_anchor("a1"));
        assert!(a1.contains("<span class=\"e\"><span class=\"nb\" title=\"-0.075\">-0.08</span></span>") && text_of(a1).contains("0.08 nats lower"), "{a1}");
        // Runs it rests on: each arm by its plain name, as the verdict names it
        let obs = |arm: &str, run: &str, seed: &str| crate::obj! {"arm" => arm, "run" => run, "counted" => true, "value_text" => "75.1", "per_run" => crate::obj! {"seed" => seed}};
        let mut doc = example("owner");
        let mut m = step_by_step(3.6, 0.619, 6.581, "misses", "criterion not met \u{b7} gain");
        m.as_dict_mut().unwrap().insert("observations".into(), Value::List(vec![obs("step_by_step", "runs1", "1"), obs("plain baseline", "runs2b", "1")]));
        dig(&mut doc, &["findings"]).as_dict_mut().unwrap().insert("c1".into(), m);
        let page = all(&doc, Surface::Page);
        let c = card(&page, "c-c1");
        let runs = &c[c.find("id=\"c-c1:runs\"").unwrap()..];
        let runs = &runs[..runs.find("</section>").unwrap()];
        assert!(runs.contains("<td>step by step</td>") && !runs.contains("step_by_step"), "{runs}");
        // a level's runs give its arm's number, never the difference
        let mut doc = example("owner");
        let mut lv = step_by_step(90.48, 89.92, 91.04, "meets", "criterion met \u{b7} measured");
        lv.as_dict_mut().unwrap().insert("reference_label".into(), Value::str(""));
        lv.as_dict_mut().unwrap().insert("subject_label".into(), Value::str("int8"));
        lv.as_dict_mut().unwrap().insert("observations".into(), Value::List(vec![obs("", "runs1", "1"), obs("", "runs2b", "2")]));
        dig(&mut doc, &["findings"]).as_dict_mut().unwrap().insert("c1".into(), lv);
        let page = all(&doc, Surface::Page);
        let c = card(&page, "c-c1");
        let runs = text_of(&c[c.find("id=\"c-c1:runs\"").unwrap()..]);
        assert!(runs.contains("Every run here is INT8.") && !runs.contains("difference"), "{runs}");
        // a run's card: each result by its metric's title, then its arm's plain name
        let mut doc = example("owner");
        dig(&mut doc, &["runs", "runs1"]).as_dict_mut().unwrap().insert("results".into(), Value::str("step_by_step.val_loss=0.5 \u{b7} plain.val_loss=0.6"));
        let page = all(&doc, Surface::Page);
        let run = card(&page, "run-runs1");
        assert!(run.contains("<dt>Val loss, step by step</dt>") && run.contains("<dt>Val loss, plain</dt>"), "{run}");
    }

    /// FIX5: a variant's card heads its metrics by their titles and says its means in rb's digits with their units; a
    /// metric's title keeps an acronym's case; a rule's runs carry their unit; the margin is drawn as M words it.
    #[test]
    fn a_card_says_metrics_by_title_and_numbers_with_their_units() {
        assert_eq!(metric_said("onnx_top1"), "ONNX top-1");
        assert_eq!(metric_said("int8_top1"), "INT8 top-1");
        assert_eq!(metric_said("top1"), "Top-1");
        assert_eq!(metric_said("val_loss"), "Val loss");
        assert_eq!(metric_said("auc"), "AUC");
        assert_eq!(metric_said("share_under_2pt"), "Share under 2pt");
        let mut doc = example("owner");
        let e1 = dig(&mut doc, &["experiments"]).as_list_mut().unwrap().iter_mut().find(|x| s(x, "id") == "e1").unwrap();
        for row in dig(e1, &["table", "rows"]).as_list_mut().unwrap() {
            let ms = row.as_dict_mut().unwrap().get_mut("metrics").unwrap().as_dict_mut().unwrap();
            ms.get_mut("latency_ms").unwrap().as_dict_mut().unwrap().insert("mean".into(), Value::Float(8.066666666666666));
            ms.get_mut("top1").unwrap().as_dict_mut().unwrap().insert("mean".into(), Value::Float(75.68666666666667));
        }
        let pct = dig(e1, &["table", "metrics"]).as_list_mut().unwrap().iter_mut().find(|m| s(m, "name") == "top1").unwrap();
        pct.as_dict_mut().unwrap().insert("unit".into(), Value::str("percent"));
        let page = all(&doc, Surface::Page);
        let v = card(&page, &variant_anchor("e1", "fp32"));
        let text = text_of(v);
        for want in ["<th>Top-1</th>", "<th>ONNX top-1</th>", "<th>Latency</th>"] {
            assert!(v.contains(want), "{want}: {v}");
        }
        assert!(text.contains("8.07 ms") && text.contains("75.69%"), "{text}");
        for raw in ["latency_ms", "onnx_top1", "8.0666", "75.6866", ">top1<"] {
            assert!(!v.contains(raw), "{raw}: {v}");
        }
        // rb's label for a metric where it gives one, as the grid titles it
        let mut doc2 = doc.clone();
        let e1 = dig(&mut doc2, &["experiments"]).as_list_mut().unwrap().iter_mut().find(|x| s(x, "id") == "e1").unwrap();
        let lat = dig(e1, &["table", "metrics"]).as_list_mut().unwrap().iter_mut().find(|m| s(m, "name") == "latency_ms").unwrap();
        lat.as_dict_mut().unwrap().insert("label".into(), Value::str("Inference time"));
        assert!(card(&all(&doc2, Surface::Page), &variant_anchor("e1", "fp32")).contains("<th>Inference time</th>"));
        // c1's runs: an arm's own number in the level's unit, a run's difference in points
        let obs = |arm: &str, run: &str, seed: &str, value: &str| crate::obj! {"arm" => arm, "run" => run, "counted" => true, "value_text" => value, "per_run" => crate::obj! {"seed" => seed}};
        let mut doc = example("owner");
        let mut m = step_by_step(3.6, 0.619, 6.581, "misses", "criterion not met \u{b7} gain");
        m.as_dict_mut().unwrap().insert("observations".into(), Value::List(vec![obs("step_by_step", "runs1", "1", "14"), obs("plain", "runs2b", "1", "10.4")]));
        dig(&mut m, &["row"]).as_dict_mut().unwrap().insert("margin".into(), Value::str("margin -1.40 points"));
        dig(&mut doc, &["findings"]).as_dict_mut().unwrap().insert("c1".into(), m);
        let page = all(&doc, Surface::Page);
        let c = card(&page, "c-c1");
        let runs = text_of(&c[c.find("id=\"c-c1:runs\"").unwrap()..]);
        assert!(runs.contains("14.00%") && runs.contains("10.40%"), "{runs}");
        assert!(text_of(c).contains("margin-1.40 points") || text_of(c).contains("margin -1.40 points"), "{}", text_of(c));
        let mut doc = example("owner");
        let mut m = step_by_step(3.6, 0.619, 6.581, "misses", "criterion not met \u{b7} gain");
        m.as_dict_mut().unwrap().insert("observations".into(), Value::List(vec![obs("", "runs1", "1", "3.6"), obs("", "runs2b", "2", "1")]));
        dig(&mut doc, &["findings"]).as_dict_mut().unwrap().insert("c1".into(), m);
        let page = all(&doc, Surface::Page);
        let c = card(&page, "c-c1");
        let runs = text_of(&c[c.find("id=\"c-c1:runs\"").unwrap()..]);
        assert!(runs.contains("3.60 points") && runs.contains("1.00 point") && !runs.contains("1.00 points"), "{runs}");
    }

    /// What a reading is (a gain, a loss, a level measured) is never drawn as a rule's state on any card, and a call's
    /// card says its verdict once, under its title, with no status chips; a reader's has no call box at all.
    #[test]
    fn no_card_says_a_kind_word_as_status_and_a_call_says_its_verdict_once() {
        let mut doc = example("owner");
        let math = step_by_step(3.6, 0.619, 6.581, "misses", "criterion not met \u{b7} gain");
        doc.as_dict_mut().unwrap().get_mut("findings").unwrap().as_dict_mut().unwrap().insert("c1".into(), math);
        let call = list(&doc, "calls")[0].clone();
        let mut c1 = call;
        for (k, v) in [("subject", "c1"), ("title", "Step by step scores at least 5 points above the plain prompt"), ("what", "c1 misses its rule and no call on it is recorded: Step by step scores at least 5 points above the plain prompt")] {
            c1.as_dict_mut().unwrap().insert(k.into(), Value::str(v));
        }
        let reason = c1.as_dict_mut().unwrap().get_mut("reason").unwrap().as_dict_mut().unwrap();
        reason.insert("label".into(), Value::str("Rule not met"));
        reason.insert("detail".into(), Value::str("estimate +3.6 < 5"));
        doc.as_dict_mut().unwrap().get_mut("calls").unwrap().as_list_mut().unwrap().push(c1);
        let export = all(&doc, Surface::Export);
        let pages = [("page", all(&doc, Surface::Page)), ("open", all(&doc, Surface::Open)), ("none", as_viewer(&export, "none")), ("owner", as_viewer(&export, "owner")), ("reader", as_viewer(&export, "reader"))];
        for (name, page) in &pages {
            for el in page.split("<a class=\"sw").skip(1) {
                let said = text_of(&el[el.find('>').unwrap() + 1..el.find("</a>").unwrap()]);
                assert!(!["gain", "loss", "unresolved", "measured", "higher", "lower", "equivalent"].contains(&said.trim()), "{name}: {said}");
            }
            let c = card(page, "call-c1");
            let text = text_of(c);
            assert_eq!(c.matches("<p class=\"verdict\">").count(), 1, "{name}");
            assert!(text.contains("Not met: step by step is 3.60 points higher than the plain baseline (+0.62 to +6.58), short of the 5 points the rule asks for."), "{name}: {text}");
            assert_eq!(text.to_lowercase().matches("not met").count(), 1, "{name}: {text}");
            assert_eq!(text.matches("Step by step scores at least 5 points above the plain prompt").count(), 1, "the title once: {name}");
            assert!(!c.contains("class=\"stand\"") && !text.contains("gain") && !text.contains("Review this result"), "{name}: {text}");
            assert!(text.contains("From 3 seeds (6 runs) \u{b7} the whole finding") && text.contains("Nothing is recorded on it yet."), "{name}: {text}");
            // the figure marks 0 and the rule's number, labels the shaded part, and gives the dot its value
            for label in [">0</text>", ">5</text>", ">the rule: 5 points or more</text>", ">3.60</text>"] {
                assert!(c.contains(label), "{name}: {label}");
            }
            let reader = *name == "reader";
            assert_eq!(text.contains("Record a call") || c.contains("data-level=\"decide\""), !reader, "{name}");
            assert!(text.contains(if reader { "Still open \u{b7} c1" } else if *name == "none" { "Open call \u{b7} c1" } else { "Your call \u{b7} c1" }), "{name}: {text}");
        }
    }

    /// How often a state phrase is said in a text, lowercased, as a phrase of its own: "met" is not counted inside "not
    /// met", "meets" or "unmet".
    fn said_count(text: &str, phrase: &str) -> usize {
        let t = text.to_lowercase();
        let p = phrase.to_lowercase();
        let b = t.as_bytes();
        t.match_indices(&p)
            .filter(|(i, m)| {
                let before = t[..*i].chars().last().map_or(true, |c| !c.is_alphabetic() && c != '\'');
                let after = b.get(i + m.len()).map_or(true, |c| !(*c as char).is_alphanumeric());
                let negated = !p.starts_with("not ") && t[..*i].ends_with("not ");
                before && after && !negated
            })
            .count()
    }

    /// FIX2: a rule's card says its verdict once, under its title: no state words after it ("This rule: not met ·
    /// checks: ok"), and its call box holds the actions and its line about the terminal, never the rule's state again
    /// ("Rule not met · estimate +3.6 < 5"). For every state, on every surface, for each export viewer and at a past
    /// version, the status phrase is said once in the card's text; checks are told in The rule as written.
    #[test]
    fn a_rule_card_says_its_verdict_once() {
        let past = crate::pyjson::loads(include_str!("../../../tests/fixtures/shell/int8-demo.past.view.json")).unwrap();
        let states = [
            ("meets", "criterion met \u{b7} gain", "met"),
            ("misses", "criterion not met \u{b7} gain", "not met"),
            ("reproduced", "reproduced", "reproduced"),
            ("not reproduced", "not reproduced", "not reproduced"),
            ("undetermined", "criterion undetermined", "can't tell yet"),
            ("untested", "criterion untested \u{b7} untested", "not tested"),
        ];
        for (state, standing, phrase) in states {
            for (base, at_past) in [(example("owner"), false), (past.clone(), true)] {
                let mut doc = base;
                let mut math = step_by_step(3.6, 0.619, 6.581, state, standing);
                math.as_dict_mut().unwrap().get_mut("says").unwrap().as_dict_mut().unwrap().insert("integrity".into(), Value::str("ok"));
                doc.as_dict_mut().unwrap().get_mut("findings").unwrap().as_dict_mut().unwrap().insert("c1".into(), math);
                let mut c1 = example("owner").get("calls").and_then(|c| c.as_list().map(|l| l[0].clone())).unwrap();
                for (k, v) in [("subject", "c1"), ("title", "Step by step scores at least 5 points above the plain prompt")] {
                    c1.as_dict_mut().unwrap().insert(k.into(), Value::str(v));
                }
                let reason = c1.as_dict_mut().unwrap().get_mut("reason").unwrap().as_dict_mut().unwrap();
                reason.insert("label".into(), Value::str("Rule not met"));
                reason.insert("detail".into(), Value::str("estimate +3.6 < 5"));
                doc.as_dict_mut().unwrap().get_mut("calls").unwrap().as_list_mut().unwrap().retain(|c| s(c, "subject") != "c1");
                doc.as_dict_mut().unwrap().get_mut("calls").unwrap().as_list_mut().unwrap().push(c1);
                let pages: Vec<(&str, String)> = if at_past {
                    vec![("past", all(&doc, Surface::Open))]
                } else {
                    let export = all(&doc, Surface::Export);
                    vec![("page", all(&doc, Surface::Page)), ("open", all(&doc, Surface::Open)), ("none", as_viewer(&export, "none")), ("owner", as_viewer(&export, "owner")), ("reader", as_viewer(&export, "reader"))]
                };
                for (name, page) in &pages {
                    let c = card(page, "c-c1");
                    let text = text_of(c);
                    assert_eq!(said_count(&text, phrase), 1, "{state} {name}: {text}");
                    assert!(!c.contains("<p class=\"sti\">") && !text_of(level(c, "understand")).contains("checks"), "{state} {name}: {text}");
                    let said = sentence_text(&verdict(at(&doc, "findings.c1").unwrap()));
                    assert!(text_of(level(c, "understand")).contains(&said), "{state} {name}: {said} not in {text}");
                    // checks are a row of The rule as written
                    let rule = &c[c.find("id=\"c-c1:rule\"").unwrap()..];
                    assert!(text_of(&rule[..rule.find("</section>").unwrap()]).contains("checks"), "{state} {name}");
                    // the call box: no reason that is the rule's state again, no rb shorthand of it
                    if let Some(i) = c.find("id=\"c-c1:decide\"") {
                        let part = text_of(&c[c[..i].rfind('<').unwrap()..]);
                        assert!(!part.contains("Rule not met") && !part.contains("+3.6 < 5"), "{state} {name}: {part}");
                        for (_, _, p) in states {
                            assert_eq!(said_count(&part, p), 0, "{state} {name}: {part}");
                        }
                    }
                }
            }
        }
    }

    /// FIX2: a finding's call box keeps a reason its verdict does not say ("Too little evidence"), without the parts of
    /// rb's detail the card says already (the rule's state, the count it rests on).
    #[test]
    fn a_findings_call_box_keeps_only_what_its_verdict_does_not_say() {
        let mut doc = example("owner");
        for c in dig(&mut doc, &["calls"]).as_list_mut().unwrap() {
            if s(c, "subject") == "c2" {
                let reason = c.as_dict_mut().unwrap().get_mut("reason").unwrap().as_dict_mut().unwrap();
                reason.insert("label".into(), Value::str("Too little evidence"));
                reason.insert("detail".into(), Value::str("1 run \u{b7} 2 photos \u{b7} criterion met \u{b7} one seed short"));
            }
        }
        for page in [all(&doc, Surface::Page), as_viewer(&all(&doc, Surface::Export), "owner")] {
            let c2 = card(&page, "c-c2");
            let part = text_of(&c2[c2[..c2.find("id=\"c-c2:decide\"").unwrap()].rfind('<').unwrap()..]);
            assert!(part.contains("Too little evidence \u{b7} one seed short"), "{part}");
            assert!(!part.contains("1 run") && !part.contains("2 photos") && !part.contains("criterion met"), "{part}");
        }
    }

    /// What the verdict does not say stays on a call's card, once: an undetermined rule's reason, a direction call's
    /// question, and the gate it reads; what it says (the state, the count) is not said again; the rule's number in it
    /// drills to the rule and an amount's unit agrees with the number shown.
    #[test]
    fn a_call_keeps_what_its_verdict_does_not_say() {
        let mut doc = example("owner");
        // c6 undetermined for a reason other than its units: the reason and its detail stay
        let f = dig(&mut doc, &["findings", "c6"]).as_dict_mut().unwrap();
        f.insert("state".into(), Value::str("undetermined"));
        f.insert("standing".into(), Value::str("criterion undetermined"));
        let c6 = doc.as_dict_mut().unwrap().get_mut("calls").unwrap().as_list_mut().unwrap().iter_mut().find(|c| s(c, "subject") == "c6").unwrap();
        let r = c6.as_dict_mut().unwrap().get_mut("reason").unwrap().as_dict_mut().unwrap();
        r.insert("label".into(), Value::str("Rule: can't tell yet"));
        r.insert("detail".into(), Value::str("a cited number's settings differ from the source's"));
        let page = all(&doc, Surface::Page);
        let c6 = text_of(card(&page, "call-c6"));
        // the label is the verdict's state again (FIX2): the detail stays as its own line, the state said once
        assert!(c6.contains("Can't tell yet: ") && c6.contains("A cited number's settings differ from the source's"), "{c6}");
        assert_eq!(said_count(&c6, "can't tell yet"), 1, "{c6}");
        // and on the rule's own card, whose call box says why and not the state
        let card6 = card(&page, "c-c6");
        let part = text_of(level(card6, "decide"));
        assert!(part.contains("A cited number's settings differ from the source's") && said_count(&part, "can't tell yet") == 0, "{part}");
        assert_eq!(said_count(&text_of(card6), "can't tell yet"), 1, "{}", text_of(card6));
        // c3: "Too little evidence" stays; its "1 run" and "criterion met" are the card's own lines already
        let c3 = text_of(card(&page, "call-c3"));
        assert!(c3.contains("Too little evidence") && c3.matches("1 run").count() == 1 && !c3.contains("criterion met"), "{c3}");
        // the rule's number drills to the rule, the reading's to its runs
        let c2 = card(&page, "call-c2");
        assert!(c2.contains("of the cited <a href=\"#c-c2:rule\">0.7613</a>.") && c2.contains("<a href=\"#c-c2:runs\">0.7597</a>"), "{c2}");
        // a direction call says what it asks, and the gate it reads is in its eyebrow
        let e2 = card(&page, "call-e2");
        assert!(text_of(e2).contains("Choose whether this work continues") && e2.contains("gate <a class=\"mono id\" href=\"#c-c5\">c5</a></div>"), "{e2}");
        assert!(!text_of(e2).contains(". \u{b7}"), "{e2}");
        // 6.004 against 5 is said 1.00 point past, as shown
        let f = step_by_step(6.004, 5.5, 6.5, "meets", "criterion met \u{b7} gain");
        assert!(sentence_text(&verdict(&f)).contains("1.00 point past the 5"), "{}", verdict(&f));
    }

    /// FIX9 (Look 9 item 4): the cited file's card names a call that waits for a confirmation by what it is, as
    /// History's rows say it, never by the id rb minted for it.
    #[test]
    fn a_cited_files_card_names_a_waiting_call_by_what_it_is() {
        let doc = example("owner");
        let src = text_of(card(&all(&doc, Surface::Page), "cite-notes.md:3"));
        assert!(src.contains("A past call to park int8 mentioned, not confirmed"), "{src}");
        assert!(!src.contains("dec4"), "{src}");
        let mut d = doc.clone();
        let by: Value = crate::pyjson::loads(r#"[{"kind": "call", "id": "dec7"}]"#).unwrap();
        dig(&mut d, &["citations", "notes.md:3"]).as_dict_mut().unwrap().insert("cited_by".into(), by);
        let src = text_of(card(&all(&d, Surface::Page), "cite-notes.md:3"));
        assert!(src.contains("A proposed call to accept c3, not made") && !src.contains("dec7"), "{src}");
    }

    /// FIX9 (M's request 151): two runs with one label read apart on every card that names them, as the model's
    /// runs.{id}.named tells them apart after the label; and from their times where the document has no `named`.
    #[test]
    fn two_runs_with_one_label_read_apart_as_the_model_names_them() {
        let doc = example("owner");
        let runs = entries(&doc, "runs");
        let twins: Vec<&str> = runs.iter().filter(|(_, r)| s(r, "label") == "fp32 and int8 \u{b7} seed 2").map(|(k, _)| *k).collect();
        assert_eq!(twins.len(), 2);
        let page = all(&doc, Surface::Page);
        let d = Doc::new(&doc, Surface::Page);
        for (rid, r) in &runs {
            let named = s(r, "named");
            let after = named.strip_prefix(&format!("run {}", s(r, "label"))).unwrap();
            assert_eq!(d.run_named(rid), format!("{}{after}", run_title(s(r, "label"))), "{rid}");
            assert_eq!(after.is_empty(), !twins.contains(rid), "{rid}: {named}");
        }
        let said: Vec<String> = twins.iter().map(|r| d.run_named(r)).collect();
        assert_ne!(said[0], said[1]);
        for (rid, name) in twins.iter().zip(&said) {
            assert!(name.contains(", recorded "), "{name}");
            // the run's own card says it in its title, and a link to it by the same words
            let head = card(&page, &run_anchor(rid));
            assert!(head.contains(&format!("<h3 class=\"tt\">{}</h3>", esc(name))), "{head}");
        }
        // with no `named` (a model before contract 1.9), History's rows tell them apart; with neither, their times, and one
        // minute's runs their order
        let mut bare = doc.clone();
        for rid in &twins {
            dig(&mut bare, &["runs", rid]).as_dict_mut().unwrap().shift_remove("named");
        }
        // History's row that records each says the same words after the label, as the model wrote them
        assert_eq!(twins.iter().map(|r| Doc::new(&bare, Surface::Page).run_named(r)).collect::<Vec<_>>(), said);
        assert_eq!(said_apart(", recorded 2026-10-13 14:12:05 UTC, the 2nd of 2: note: x"), Some(", recorded 2026-10-13 14:12:05 UTC, the 2nd of 2"));
        assert_eq!(said_apart(", recorded 2026-10-13 14:12 UTC: note: x"), Some(", recorded 2026-10-13 14:12 UTC"));
        assert_eq!(said_apart(": note: recorded 2026-10-13 14:12 UTC"), None);
        dig(&mut bare, &["history"]).as_dict_mut().unwrap().shift_remove("log");
        let d = Doc::new(&bare, Surface::Page);
        let said: Vec<String> = twins.iter().map(|r| d.run_named(r)).collect();
        assert_ne!(said[0], said[1]);
        for rid in &twins {
            dig(&mut bare, &["runs", rid]).as_dict_mut().unwrap().insert("at".into(), Value::str("2026-10-13 14:12 UTC"));
        }
        let d = Doc::new(&bare, Surface::Page);
        assert_eq!(
            twins.iter().map(|r| d.run_named(r)).collect::<Vec<_>>(),
            ["1st", "2nd"].map(|n| format!("FP32 and INT8, seed 2, recorded 2026-10-13 14:12 UTC, the {n} of 2"))
        );
    }
}
