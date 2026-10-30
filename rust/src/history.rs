//! History in sittings (BP 5H): the log's writes as a screen tells them, with no writer. A sitting is the writes with no
//! gap over five minutes between them, split on time only, since a split on writer or session would show where another
//! writer took over, even with no name. A sitting holds one line per kind of change: consecutive writes of one kind,
//! with one reason, told as one sentence that keeps its verb and drops its subject, with its time span, its reason as
//! written and where its first write sits in the log. The reason is handed in its voice: the user's own as their why, a
//! session's as its note, which a screen never says as the user's. The user's words given with a write stay in rb log,
//! as does who wrote each line. The shell's model calls `of` for the document's `history` and `home.latest` (SPRINT-OCT24.md 1.3).

use crate::errors::Res;
use crate::ledger::Ledger;
use crate::record::{Analysis, Criterion, Decision, Experiment, Run};
use crate::value::{Dict, DictExt, Value};
use indexmap::IndexMap;
use std::collections::HashMap;

/// The longest pause between two writes of one sitting.
pub const GAP_SECONDS: i64 = 5 * 60;

/// One line of a sitting: consecutive writes of one kind of change.
#[derive(Debug, Clone, PartialEq)]
pub struct Line {
    pub first: String,
    pub last: String,
    pub n: usize,
    pub sentence: String,
    pub refs: Vec<String>,
    /// the user's own reason, as written; empty where the reason is a session's
    pub why: String,
    /// a session's reason, as written, which a screen says as its note ("note: ... · not confirmed"), never in the
    /// user's voice: a proposal's, a past call's it mentions, the one a confirmation of a proposal carries, any reason a
    /// session's write gave
    pub note: String,
    /// where the line's first write sits in `Ledger::log_entries`, as the log rows count it
    pub log_index: usize,
}

/// Writes with no gap over five minutes between them, oldest line first.
#[derive(Debug, Clone, PartialEq)]
pub struct Sitting {
    pub first: String,
    pub last: String,
    pub n: usize,
    /// the calls made in it: made or confirmed, never one proposed or a past call mentioned
    pub calls: usize,
    pub head: String,
    pub lines: Vec<Line>,
}

/// The record's history: every write, newest sitting first.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct History {
    pub writes: usize,
    pub sittings: Vec<Sitting>,
    /// each write's own sentence, by where it sits in the log, oldest first: what the log's rows say of it
    pub said: Vec<(usize, String)>,
}

/// The Overview's "Latest changes": the newest sitting's head and the calls made in it.
#[derive(Debug, Clone, PartialEq)]
pub struct Latest {
    pub head: String,
    pub calls_made: usize,
    pub line: String,
}

/// The history of `led`'s record, from its log. It reads the log and the records its lines name, and writes nothing:
/// no seen marker, so a screen that draws it changes no one's "since".
pub fn of(led: &Ledger) -> Res<History> {
    let rows = led.log_entries()?;
    let mut facts = Facts::of(led)?;
    (facts.names, facts.bases) = named_apart(led, &rows)?;
    Ok(told(&rows, &facts))
}

impl History {
    /// "47 changes recorded".
    pub fn summary(&self) -> String {
        format!("{} recorded", counted(self.writes, "change", "changes"))
    }

    /// The newest sitting as the Overview's first screen says it; none for a log with no write.
    pub fn latest(&self) -> Option<Latest> {
        let s = self.sittings.first()?;
        let head = span_head(&s.first, &s.last, s.n);
        Some(Latest { line: format!("Latest changes: {head} · {}", calls_said(s.calls)), head, calls_made: s.calls })
    }

    /// The write at `log_index` alone, in History's words (a proposal as one, never as a call made): what history.log's
    /// row says of it. None for a session's line, which records no write.
    pub fn sentence_at(&self, log_index: usize) -> Option<&str> {
        self.said.binary_search_by_key(&log_index, |(i, _)| *i).ok().map(|k| self.said[k].1.as_str())
    }

    /// Keep the refs a screen can link, those `held` holds (the objects the document holds): a retracted criterion or
    /// analysis, or a note, is named in its line's sentence alone.
    pub fn retain_refs(&mut self, held: impl Fn(&str) -> bool) {
        for l in self.sittings.iter_mut().flat_map(|s| s.lines.iter_mut()) {
            l.refs.retain(|r| held(r));
        }
    }

    /// `history.summary` and `history.sittings` as the document holds them (the model adds `log`).
    pub fn to_value(&self) -> Value {
        crate::obj! {"summary" => self.summary(), "sittings" => Value::List(self.sittings.iter().map(Sitting::to_value).collect())}
    }
}

impl Sitting {
    pub fn to_value(&self) -> Value {
        crate::obj! {
            "first" => self.first.clone(), "last" => self.last.clone(), "n" => self.n, "calls" => self.calls, "head" => self.head.clone(),
            "lines" => Value::List(self.lines.iter().map(Line::to_value).collect())
        }
    }
}

impl Line {
    pub fn to_value(&self) -> Value {
        crate::obj! {
            "first" => self.first.clone(), "last" => self.last.clone(), "n" => self.n, "sentence" => self.sentence.clone(),
            "refs" => Value::list_of_str(self.refs.clone()), "why" => self.why.clone(), "note" => self.note.clone(), "log_index" => self.log_index
        }
    }
}

impl Latest {
    pub fn to_value(&self) -> Value {
        crate::obj! {"head" => self.head.clone(), "calls_made" => self.calls_made, "line" => self.line.clone()}
    }
}

/// How a decision reads as a call, worked out as rb status works it out.
#[derive(Debug, Clone, PartialEq)]
enum Standing {
    /// recorded outside an agent session
    Made,
    /// the user's confirmation of a call a session proposed
    Confirmed,
    /// recorded outside an agent session as made before it was recorded, on the day given
    PastMade(String),
    /// a session's call, which waits for one confirmation and changes nothing
    Proposed,
    /// a past call a session mentioned: the day given, and the document's file and line
    PastMentioned(String, String),
    /// its file was edited outside rb since: nobody's call
    Unread,
}

#[derive(Debug, Clone)]
struct Call {
    subject: String,
    action: String,
    why: String,
    standing: Standing,
}

#[derive(Debug, Clone, Default)]
struct RunFacts {
    experiment: String,
    made: Vec<String>,
    row: String,
}

/// What a line needs of the record beyond its log line, read once.
#[derive(Debug, Clone, Default)]
struct Facts {
    project: String,
    calls: HashMap<String, Call>,
    runs: HashMap<String, RunFacts>,
    titles: HashMap<String, String>,
    experiment_of: HashMap<String, String>,
    /// (criterion, question) -> the interpretation its link reads now, where it reads as the user's
    owned_links: HashMap<(String, String), String>,
    /// what a screen says for a record rb minted an id for, which it never shows as text (FIX7): a run, an analysis, a call
    names: HashMap<String, String>,
    /// the words before ", recorded <when>" of a record rb told apart by when it was recorded (`named_apart`)
    bases: HashMap<String, String>,
}

impl Facts {
    /// A record as a sentence names it: by its words where rb minted its id (`names`), else its kind and its id.
    fn record(&self, kind: &str, id: &str) -> String {
        match (self.names.get(id), kind) {
            (Some(w), _) => w.clone(),
            (None, "note") => "a note".to_string(),
            _ => format!("{} {id}", noun(kind)),
        }
    }

    /// A record's words as a line History opens with "Recorded" says them: the time rb told it apart by said once.
    fn at_words(&self, id: &str) -> Option<String> {
        self.names.get(id).map(|w| as_recorded(w, self.bases.get(id).map_or("", String::as_str)))
    }

    /// A record a line lists by its words where it has them, else by its id.
    fn word(&self, id: &str) -> String {
        self.names.get(id).cloned().unwrap_or_else(|| id.to_string())
    }
}

/// What every screen says for each run, each analysis rb minted an id for and each call, by id (FIX7, FIX8), as the
/// model's History log rows say them (shell/model.rs minted_names and run_names): a run as "run <its label>", "a run of
/// e1" where it has none, told apart from a run whose words read the same by when it was recorded, to the second where
/// the minute is shared, then ", the 2nd of 2" where that is shared too; an analysis by its title ("analysis <title>");
/// a call by what it is ("a call to accept c1", "a proposed call to accept c1, not made"); an analysis with no title by
/// its experiment and when it was recorded ("an analysis on e1, recorded 2026-10-14 09:30 UTC", M's settled words).
pub fn names_of(led: &Ledger, rows: &[Dict]) -> Res<HashMap<String, String>> {
    Ok(named_apart(led, rows)?.0)
}

/// Each record's words (`names_of`), and the words before ", recorded <when>" where rb told a record apart by when it
/// was recorded (a run whose words another's share, an analysis with no title), so History's own "Recorded" says that
/// time once and never rewrites a label's own words (`as_recorded`).
fn named_apart(led: &Ledger, rows: &[Dict]) -> Res<(HashMap<String, String>, HashMap<String, String>)> {
    let mut seq: HashMap<String, usize> = HashMap::new();
    for (i, row) in rows.iter().enumerate() {
        if row.get_str("kind") == Some("run") {
            if let Some(id) = row.get_str("id") {
                seq.entry(id.to_string()).or_insert(i);
            }
        }
    }
    let runs = led.all::<Run>()?;
    let exps: HashMap<String, Experiment> = led.all::<Experiment>()?.iter().map(|e| (e.id.clone(), e.clone())).collect();
    let said: IndexMap<String, String> = runs
        .iter()
        .map(|r| {
            let label = crate::reproduce::run_label(led, exps.get(&r.experiment), r);
            (r.id.clone(), if label.is_empty() { format!("a run of {}", r.experiment) } else { format!("run {label}") })
        })
        .collect();
    let mut times: Vec<(String, String)> = runs.iter().map(|r| (r.id.clone(), if r.receipt.at.is_empty() { r.created_at.clone() } else { r.receipt.at.clone() })).collect();
    times.sort_by_key(|(id, _)| seq.get(id).copied().unwrap_or(usize::MAX));
    let times: IndexMap<String, String> = times.into_iter().collect();
    let mut names = twins_told_apart(&said, &times);
    let mut bases: HashMap<String, String> = said.iter().filter(|(id, w)| names.get(*id) != Some(*w)).map(|(id, w)| (id.clone(), w.clone())).collect();
    for a in led.all::<Analysis>()?.iter().filter(|a| minted_id(&a.id, "a")) {
        // one with no title is said by its experiment and when it was recorded
        let words = match a.title.trim() {
            "" => {
                bases.insert(a.id.clone(), format!("{UNTITLED}{}", a.experiment));
                format!("{UNTITLED}{}, recorded {}", a.experiment, minute(&a.created_at))
            }
            t => format!("analysis {t}"),
        };
        names.insert(a.id.clone(), words);
    }
    // a call a session recorded says so, as the call's own line does: it was never a call made
    let auth = led.authorities()?;
    for d in led.all::<Decision>()?.iter() {
        let (action, on) = (&d.action, named(&d.subject));
        let words = match auth.get(&d.id).map(|a| a.state) {
            Some("proposed") => format!("a proposed call to {action} {on}, not made"),
            Some("past_unconfirmed") => format!("a past call to {action} {on} mentioned, not confirmed"),
            _ => format!("a call to {action} {on}"),
        };
        names.insert(d.id.clone(), words);
    }
    Ok((names, bases))
}

/// How the words of an analysis with no title open: "an analysis on e1, recorded <minute> UTC".
const UNTITLED: &str = "an analysis on ";

/// A record's words where History's own "Recorded" opens the sentence: "run X at 2026-10-14 09:25 UTC" for "run X,
/// recorded 2026-10-14 09:25 UTC", so a line never says "recorded" twice of one record. Only the time rb adds after
/// `base`, the words it told apart, is said so; words of a label itself ("tag alpha, recorded 2026-10-14 09:20 UTC"
/// as a setting's value) stay as recorded.
pub(crate) fn as_recorded(words: &str, base: &str) -> String {
    match words.strip_prefix(base).and_then(|r| r.strip_prefix(", recorded ")) {
        Some(when) if !base.is_empty() && is_minute(when) => format!("{base} at {when}"),
        _ => words.to_string(),
    }
}

/// Whether `t` opens with a time as rb says one when it tells runs apart: "2026-10-14 09:25 UTC", or to the second.
fn is_minute(t: &str) -> bool {
    let b = t.as_bytes();
    let digits = |r: std::ops::Range<usize>| b[r].iter().all(u8::is_ascii_digit);
    b.len() >= 20 && digits(0..4) && b[4] == b'-' && digits(5..7) && b[7] == b'-' && digits(8..10) && b[10] == b' ' && digits(11..13) && b[13] == b':' && digits(14..16)
}

/// Each run's words (`said`), with a run whose words another's share told apart by when it was recorded (`times`, in
/// the order the log recorded them).
fn twins_told_apart(said: &IndexMap<String, String>, times: &IndexMap<String, String>) -> HashMap<String, String> {
    let at = |k: &str| times.get(k).cloned().unwrap_or_default();
    let to_minute = |k: &str| minute(&at(k));
    let to_second = |k: &str| match crate::pytime::parse_iso(&at(k)) {
        Some(t) => t.with_timezone(&chrono::Utc).format("%Y-%m-%d %H:%M:%S UTC").to_string(),
        None => at(k),
    };
    let mut out = HashMap::new();
    for (id, words) in said {
        let twins: Vec<&String> = said.iter().filter(|(_, w)| *w == words).map(|(k, _)| k).collect();
        if twins.len() < 2 {
            out.insert(id.clone(), words.clone());
            continue;
        }
        let when = if twins.iter().filter(|k| to_minute(k) == to_minute(id)).count() > 1 { to_second(id) } else { to_minute(id) };
        let mut alike: Vec<&&String> = twins.iter().filter(|k| to_second(k) == to_second(id)).collect();
        alike.sort_by_key(|k| times.get_index_of(k.as_str()).unwrap_or(usize::MAX));
        let place = if alike.len() > 1 {
            let n = alike.iter().position(|k| **k == id).unwrap_or(0) + 1;
            format!(", the {} of {}", ordinal(n), alike.len())
        } else {
            String::new()
        };
        out.insert(id.clone(), format!("{words}, recorded {when}{place}"));
    }
    out
}

/// "1st", "2nd", "3rd", "11th".
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

/// Whether `id` has the shape of one rb minted for a kind: its prefix and six base-32 characters, not all digits.
fn minted_id(id: &str, prefix: &str) -> bool {
    id.strip_prefix(prefix).is_some_and(|t| t.len() == 6 && t.chars().all(|c| matches!(c, 'a'..='z' | '2'..='7')) && !t.chars().all(|c| c.is_ascii_digit()))
}

impl Facts {
    fn of(led: &Ledger) -> Res<Facts> {
        let mut f = Facts { project: led.project().map(|p| p.title).unwrap_or_default(), ..Default::default() };
        let auth = led.authorities()?;
        for d in led.all::<Decision>()?.iter() {
            let standing = match auth.get(&d.id) {
                _ if led.outside_rb("decision", &d.id)? => Standing::Unread,
                Some(a) if a.confirms.is_some() => Standing::Confirmed,
                Some(a) if a.is_call() => match &d.past {
                    Some(p) => Standing::PastMade(day_given(&p.when)),
                    None => Standing::Made,
                },
                Some(a) if a.state == "past_unconfirmed" => {
                    let p = d.past.clone().unwrap_or(crate::record::Past { when: String::new(), source: None });
                    Standing::PastMentioned(day_given(&p.when), p.source.map(|s| format!("{}:{}", crate::view::page_path(&s.path, None), s.line)).unwrap_or_default())
                }
                _ => Standing::Proposed,
            };
            f.calls.insert(d.id.clone(), Call { subject: d.subject.clone(), action: d.action.clone(), why: d.why.clone(), standing });
        }
        for r in led.all::<Run>()?.iter() {
            let row = match &r.receipt.row {
                Some(row) if row.rowid.is_some() => format!("row {} of table {}", row.rowid.unwrap_or_default(), row.table),
                Some(row) => format!("a row of table {}", row.table),
                None => String::new(),
            };
            f.runs.insert(r.id.clone(), RunFacts { experiment: r.experiment.clone(), made: r.outputs.iter().map(|o| o.resource.clone()).collect(), row });
        }
        for e in led.all::<Experiment>()?.iter() {
            f.titles.insert(e.id.clone(), e.title.clone());
        }
        let mut designs: HashMap<String, crate::ledger::design::Design> = HashMap::new();
        for c in led.all::<Criterion>()?.iter() {
            f.experiment_of.insert(c.id.clone(), c.experiment.clone());
            for l in c.question_links.iter().filter(|l| l.retracted.is_none() && !l.when_met.is_empty()) {
                // a session's interpretation reads as the user's once their confirmation of the design covers it
                let by = if crate::view::not_confirmed(&l.declared_by).is_empty() {
                    String::new()
                } else {
                    if !designs.contains_key(&c.experiment) {
                        designs.insert(c.experiment.clone(), led.design(&c.experiment)?.unwrap_or_default());
                    }
                    designs[&c.experiment].link_writer(&c.id, l)
                };
                if crate::view::not_confirmed(&by).is_empty() {
                    f.owned_links.insert((c.id.clone(), l.question.clone()), l.when_met.clone());
                }
            }
        }
        Ok(f)
    }
}

/// The kinds of change a line tells. Consecutive writes of one kind, on one target, with one reason, are one line.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Started,
    Retitled,
    Reworded,
    Question,
    Moved,
    Metric,
    Experiment,
    Resource,
    Criterion,
    Variant,
    Settings,
    Varies,
    Checked,
    Amended,
    Locked,
    GateSet,
    GateConfirmed,
    DesignConfirmed,
    Call,
    Runs,
    Retracted,
    Analyses,
    Notes,
    Filed,
    Unfiled,
    Interpretation,
    Related,
    Unrelated,
    Located,
    Exposed,
    Indexed,
    Reconciled,
    Adopted,
    Restored,
    Renamed,
    Copied,
    Other,
}

/// One write as its line tells it.
#[derive(Debug, Clone)]
struct Said {
    kind: Kind,
    /// what the line is on: an experiment, a question, a kind of record; a write told alone has its log index
    target: String,
    /// what this write adds to its line's sentence: an id, a setting, a change
    part: String,
    /// the part as a sentence that lists it says it: a run or an analysis by its words, never the id rb minted; empty
    /// where it is the part itself
    shown: String,
    /// a run's words as a line History opens with "Recorded" lists them, its time said once ("run X at <when>")
    at: String,
    /// the experiment a run or a change is on, for a line that counts them by experiment
    on: String,
    one: String,
    refs: Vec<String>,
    why: String,
    /// the reason is a session's, said as its note
    note: bool,
    call: bool,
    /// a setting's line: whether rb's check of its source, written right after it, found the value there
    checked: Option<bool>,
}

impl Said {
    fn new(kind: Kind, target: &str, part: &str, one: String, refs: Vec<String>, why: &str) -> Said {
        Said { kind, target: target.to_string(), part: part.to_string(), shown: String::new(), at: String::new(), on: String::new(), one, refs, why: why.to_string(), note: false, call: false, checked: None }
    }
}

/// The history of these log lines (`Ledger::log_entries`, oldest first), with what `f` holds of their records.
fn told(rows: &[Dict], f: &Facts) -> History {
    // a session's line records no write
    let writes: Vec<(usize, &Dict, Said)> = rows.iter().enumerate().filter(|(_, r)| !crate::ledger::is_session_line(r)).map(|(i, r)| (i, r, said(i, r, f))).collect();
    // each write alone, before a line folds it into another's
    let said_alone: Vec<(usize, String)> = writes.iter().map(|(i, _, s)| (*i, s.one.clone())).collect();
    let writes = fold_checks(writes);
    let mut sittings: Vec<Vec<(usize, &Dict, Said)>> = Vec::new();
    for w in writes {
        let joins = sittings.last().and_then(|s| s.last()).map_or(false, |(_, prev, _)| within(at_of(prev), at_of(w.1)));
        match sittings.last_mut() {
            Some(s) if joins => s.push(w),
            _ => sittings.push(vec![w]),
        }
    }
    let total = sittings.iter().map(Vec::len).sum();
    let mut out: Vec<Sitting> = sittings.into_iter().map(|s| sitting(fold_replacements(s))).collect();
    out.reverse();
    History { writes: total, sittings: out, said: said_alone }
}

/// rb's check of a setting's source, written right after the setting, told on the setting's line: "Set train.epochs to
/// 30 on e1, stated by its source", one line where the log holds two writes.
fn fold_checks(ws: Vec<(usize, &Dict, Said)>) -> Vec<(usize, &Dict, Said)> {
    let mut out: Vec<(usize, &Dict, Said)> = Vec::with_capacity(ws.len());
    for (i, row, said) in ws {
        let folded = match out.last() {
            Some((_, _, prev))
                if said.kind == Kind::Checked && prev.kind == Kind::Settings && prev.checked.is_none() && prev.target == said.target && prev.part == said.part && said.why.is_empty() =>
            {
                let ok = said.checked == Some(true);
                Some(Said { checked: Some(ok), one: with_check(&prev.one, ok), ..prev.clone() })
            }
            _ => None,
        };
        out.push((i, row, folded.unwrap_or(said)));
    }
    out
}

/// A setting's sentence with what rb's check of its source found.
fn with_check(one: &str, ok: bool) -> String {
    if ok { format!("{one}, stated by its source") } else { format!("{one}; rb did not find it in its source") }
}

/// The retraction rb writes for the run a new run replaces, told on the new run's line.
fn fold_replacements(s: Vec<(usize, &Dict, Said)>) -> Vec<(usize, &Dict, Said)> {
    let mut out: Vec<(usize, &Dict, Said)> = Vec::with_capacity(s.len());
    for (i, row, mut said) in s {
        let by = detail_of(row).get_str("replaced_by").map(str::to_string);
        if let (Some(by), Some((_, _, prev))) = (by, out.last()) {
            if prev.kind == Kind::Runs && prev.part == by && row.get_str("op") == Some("retract") {
                said = Said { part: String::new(), refs: Vec::new(), call: false, ..prev.clone() };
            }
        }
        out.push((i, row, said));
    }
    out
}

fn sitting(s: Vec<(usize, &Dict, Said)>) -> Sitting {
    let mut lines: Vec<Vec<(usize, &Dict, Said)>> = Vec::new();
    for w in s.iter().cloned() {
        let joins = lines.last().and_then(|l| l.last()).map_or(false, |(_, _, p)| p.kind == w.2.kind && p.target == w.2.target && p.why == w.2.why && p.note == w.2.note);
        match lines.last_mut() {
            Some(l) if joins => l.push(w),
            _ => lines.push(vec![w]),
        }
    }
    let (first, last) = (minute(at_of(s[0].1)), minute(at_of(s[s.len() - 1].1)));
    let calls = s.iter().filter(|(_, _, w)| w.call).count();
    Sitting {
        head: format!("{} · {}", span_head(&first, &last, s.len()), calls_said(calls)),
        first,
        last,
        n: s.len(),
        calls,
        lines: lines.iter().map(|l| line(l)).collect(),
    }
}

fn line(l: &[(usize, &Dict, Said)]) -> Line {
    let members: Vec<&Said> = l.iter().map(|(_, _, s)| s).collect();
    let mut refs: Vec<String> = Vec::new();
    for r in members.iter().flat_map(|s| s.refs.iter()) {
        if !r.is_empty() && !refs.contains(r) {
            refs.push(r.clone());
        }
    }
    let (why, note) = if members[0].note { (String::new(), members[0].why.clone()) } else { (members[0].why.clone(), String::new()) };
    Line { first: minute(at_of(l[0].1)), last: minute(at_of(l[l.len() - 1].1)), n: l.len(), sentence: sentence(&members), refs, why, note, log_index: l[0].0 }
}

/// The sentence of a line: its write's own when it holds one, else one that counts or lists them. Each leads with what
/// happened, in plain words, and names a record by its id after that, never first.
fn sentence(members: &[&Said]) -> String {
    let lead = members[0];
    let mut parts: Vec<String> = Vec::new();
    for s in members.iter().filter(|s| !s.part.is_empty()) {
        if !parts.contains(&s.part) {
            parts.push(s.part.clone());
        }
    }
    // one record or change: the sentence of its last write, which says how the line leaves it
    if parts.len() <= 1 {
        return members.iter().rev().find(|s| !s.part.is_empty()).unwrap_or(&lead).one.clone();
    }
    let n = parts.len();
    let t = &lead.target;
    // each part as the line lists it: by its words where a write gave them
    let ids = parts.clone();
    let parts: Vec<String> =
        parts.iter().map(|p| members.iter().find(|s| s.part == *p && !s.shown.is_empty()).map_or_else(|| p.clone(), |s| s.shown.clone())).collect();
    // runs a line opened with "Recorded" lists say when each was recorded without the word again
    let parts: Vec<String> = if lead.kind == Kind::Runs {
        parts.into_iter().zip(&ids).map(|(p, id)| members.iter().find(|s| s.part == **id && !s.at.is_empty()).map_or(p, |s| s.at.clone())).collect()
    } else {
        parts
    };
    match lead.kind {
        Kind::Question if t.is_empty() => format!("Added {n} questions: {}", listed(&parts)),
        Kind::Question => format!("Added {n} questions under {t}: {}", listed(&parts)),
        Kind::Metric => format!("Added {n} metrics: {}", listed(&parts)),
        Kind::Experiment => format!("Added {n} experiments: {}", listed(&parts)),
        Kind::Resource => format!("Added {n} resources: {}", listed(&parts)),
        Kind::Criterion => format!("Added {n} rules to {t}: {}", listed(&parts)),
        Kind::Variant => format!("Added {n} variants to {t}: {}", listed(&parts)),
        Kind::Settings => {
            // what rb's checks of their sources found, the last check of each setting
            let found = parts.iter().filter(|p| members.iter().rev().find(|s| s.part == **p && s.checked.is_some()).is_some_and(|s| s.checked == Some(true))).count();
            let checked = parts.iter().filter(|p| members.iter().any(|s| s.part == **p && s.checked.is_some())).count();
            match () {
                _ if found == n => format!("Set {n} settings on {t}, each stated by its source: {}", listed(&parts)),
                _ if checked > 0 => format!("Set {n} settings on {t}: {}; rb found {found} of them in their sources", listed(&parts)),
                _ => format!("Set {n} settings on {t}: {}", listed(&parts)),
            }
        }
        Kind::Varies => format!("Declared what {t} varies: {}", and_joined(&parts)),
        Kind::Checked => {
            // a check of several settings is one part, "a, b": count the settings, not the checks
            let mut names: Vec<String> = Vec::new();
            for name in parts.iter().flat_map(|p| p.split(", ")) {
                if !names.iter().any(|x| x == name) {
                    names.push(name.to_string());
                }
            }
            format!("Checked {} settings on {t} against their sources: {}", names.len(), listed(&names))
        }
        Kind::Amended => format!("Amended {t} after locking it: {}", crate::assess::first_few(&parts, 3, "; ")),
        Kind::Locked => format!("Locked {}", and_joined(&parts)),
        Kind::DesignConfirmed if t == "gate" => format!("Confirmed the designs of {}, with their gates", and_joined(&parts)),
        Kind::DesignConfirmed => format!("Confirmed the designs of {}", and_joined(&parts)),
        Kind::Runs => {
            let runs: Vec<&&Said> = members.iter().filter(|s| !s.part.is_empty()).collect();
            if runs.len() <= 5 {
                format!("Recorded {} runs: {}", runs.len(), parted(&parts))
            } else {
                // many runs are counted by the experiment they are on, in the order the line first names each
                let mut by: Vec<(String, usize)> = Vec::new();
                for s in &runs {
                    match by.iter_mut().find(|(e, _)| *e == s.on) {
                        Some((_, k)) => *k += 1,
                        None => by.push((s.on.clone(), 1)),
                    }
                }
                format!("Recorded {} runs: {}", runs.len(), by.iter().map(|(e, k)| format!("{k} on {e}")).collect::<Vec<_>>().join(", "))
            }
        }
        Kind::Retracted if t == "setting" || t == "variant" => {
            // "e1/bar" is rb's address: said as "bar of e1", by experiment where they share one
            let split: Vec<(&str, &str)> = parts.iter().filter_map(|p| p.split_once('/')).collect();
            match split.first() {
                Some((exp, _)) if split.len() == n && split.iter().all(|(e, _)| e == exp) => {
                    format!("Retracted {n} {} of {exp}: {}", plural_of(t), listed(&split.iter().map(|(_, x)| x.to_string()).collect::<Vec<_>>()))
                }
                _ => format!("Retracted {n} {}: {}", plural_of(t), listed(&parts.iter().map(|p| named(p)).collect::<Vec<_>>())),
            }
        }
        Kind::Retracted if t == "run" || t == "decision" => format!("Retracted {n} {}: {}", plural_of(t), parted(&parts)),
        Kind::Retracted => format!("Retracted {n} {}: {}", plural_of(t), listed(&parts)),
        Kind::Analyses => {
            // analyses with no title are counted, never listed as "an analysis on e1, recorded ..." again and again
            let titled: Vec<String> = parts.iter().filter(|p| !p.starts_with(UNTITLED)).cloned().collect();
            match n - titled.len() {
                0 => format!("Recorded {n} analyses on {t}: {}", parted(&parts)),
                k if titled.is_empty() => format!("Recorded {k} analyses on {t}, with no title"),
                k => format!("Recorded {n} analyses on {t}: {}, and {k} with no title", parted(&titled)),
            }
        }
        Kind::Notes => format!("Added {n} notes on {t}"),
        Kind::Filed => format!("Filed {} under {t}", and_joined(&parts)),
        Kind::Unfiled => format!("Took {} out of {t}", and_joined(&parts)),
        Kind::Located => format!("Recorded where {} are kept", and_joined(&parts)),
        Kind::Adopted => format!("Kept {n} records as they read now: {}", parted(&parts)),
        Kind::Restored => format!("Restored {n} records: {}", parted(&parts)),
        Kind::Copied => format!("Copied {n} records from the clone: {}", parted(&parts)),
        // a kind told one write to a line
        _ => lead.one.clone(),
    }
}

/// A setting's change as rb's log line says it ("setting lr: new, 0.0003", "setting lr: 0.0003 -> 0.001; source a ->
/// b"), in plain words: "Set lr to 0.0003 on e1", "Changed lr on e1: from 0.0003 to 0.001, a new source". A change rb
/// cannot read part by part is said as a change of the setting, and the log keeps its words.
fn setting_said(exp: &str, name: &str, change: &str) -> String {
    let fallback = format!("Changed {name} on {exp}");
    let Some(rest) = change.strip_prefix(&format!("setting {name}: ")) else { return fallback };
    if let Some(new) = rest.strip_prefix("new, ") {
        return match new {
            v if v.ends_with(" (per-run)") => format!("Added {name} to {exp}, given by each run"),
            "unknown" => format!("Added {name} to {exp}, its value not known yet"),
            v if v.ends_with(" (optional)") => format!("Set {name} to {} on {exp}, optional", v.trim_end_matches(" (optional)")),
            v => format!("Set {name} to {v} on {exp}"),
        };
    }
    if rest == "unchanged" {
        return format!("Set {name} on {exp} again, unchanged");
    }
    // rb joins the parts with "; ", which a source's label or a value may hold too: a piece that opens no part of its
    // own belongs to the part before it
    let opens = |p: &str| ["required -> ", "optional -> ", "now ", "no longer ", "source ", "note changed", "cited "].iter().any(|o| p.starts_with(o));
    let mut parts: Vec<String> = Vec::new();
    for piece in rest.split("; ") {
        match parts.last_mut() {
            Some(last) if !opens(piece) => {
                last.push_str("; ");
                last.push_str(piece);
            }
            _ => parts.push(piece.to_string()),
        }
    }
    let mut said: Vec<String> = Vec::new();
    for part in parts.iter().map(String::as_str) {
        let phrase = match part {
            "required -> optional" => "now optional".to_string(),
            "optional -> required" => "now required".to_string(),
            "now per-run" => "now given by each run".to_string(),
            "no longer per-run" => "now one value for every run".to_string(),
            "now a fixed design factor" | "no longer a fixed design factor" => part.to_string(),
            "note changed" => "a new note".to_string(),
            p if p.starts_with("source ") && p.ends_with(" -> none") => "no source now".to_string(),
            p if p.starts_with("source ") && p.contains(" -> ") => "a new source".to_string(),
            p if p.starts_with("cited ") => match p["cited ".len()..].split_once(" -> ") {
                Some((a, b)) => format!("its cited value from {a} to {b}"),
                None => return fallback,
            },
            p => match p.split_once(" -> ") {
                // a value's type change ("(was str)") stays in the log
                Some((a, b)) if !b.contains(" -> ") => format!("from {a} to {}", b.split(" (was ").next().unwrap_or(b)),
                _ => return fallback,
            },
        };
        said.push(phrase);
    }
    match said.split_first() {
        Some((first, rest)) if first.starts_with("from ") => {
            format!("Changed {name} on {exp} {}", std::iter::once(first).chain(rest).cloned().collect::<Vec<_>>().join(", "))
        }
        _ => format!("Changed {name} on {exp}: {}", said.join(", ")),
    }
}

/// What rb's guard adds to an amendment's change when it also adopts changes made since the last record.
const ADOPTS: &str = "; also adopts changes made without an amendment since the last record";

/// rb reconcile's line ("+2 experiments (sweep, warm) · 2 results quoted · ...") as one plain sentence: the experiments
/// it added, where it added any; the rest of rb's line stays in the raw log.
fn reconciled_said(line: &str) -> String {
    let first = line.split(" \u{b7} ").next().unwrap_or("");
    match first.strip_prefix('+').and_then(|r| r.split_once(' ')) {
        Some((n, rest)) if n.parse::<usize>().is_ok() && (rest.starts_with("experiment ") || rest.starts_with("experiments ")) => {
            format!("Read the repository with rb reconcile and added {n} {rest}")
        }
        _ => "Read the repository with rb reconcile".to_string(),
    }
}

/// A kind of record as the screens name it: a criterion is a rule, a decision a call.
fn noun(kind: &str) -> String {
    match kind {
        "criterion" => "rule".into(),
        "decision" => "call".into(),
        k => k.replace('_', " "),
    }
}

/// An amendment's change, as rb's guard writes it, in plain words: a setting's, a variant's, a criterion's (a rule's,
/// by its statement where the record holds one); as rb wrote it where it is no form rb writes.
fn amendment_said(exp: &str, change: &str) -> String {
    // rb's guard adds this when the amendment also adopts a drift: said as a clause of its own after the change
    if let Some(change) = change.strip_suffix(ADOPTS) {
        return format!("{}, and kept changes made without an amendment since it was locked", amendment_said(exp, change));
    }
    if let Some(rest) = change.strip_prefix("setting ") {
        if let Some(name) = rest.strip_suffix(" retracted") {
            return format!("retracted setting {name}");
        }
        if let Some((name, _)) = rest.split_once(": ") {
            let plain = setting_said(exp, name, change).replacen(&format!(" on {exp}"), "", 1);
            // after "Amended e1 after locking it: ", "changed lr from a to b"
            let mut c = plain.chars();
            return c.next().map(|f| f.to_lowercase().chain(c).collect()).unwrap_or_default();
        }
    }
    if let Some(v) = change.strip_prefix("variant ").and_then(|r| r.strip_suffix(" retracted")) {
        return format!("retracted variant {v}");
    }
    if let Some(v) = change.strip_prefix("new variant ") {
        return format!("added variant {v}");
    }
    if let Some(names) = change.strip_prefix("varies ").and_then(|r| r.strip_suffix(" on purpose")) {
        return format!("declared it varies {names}");
    }
    if let Some((id, _)) = change.strip_prefix("new criterion ").and_then(|r| r.split_once(": ")) {
        return format!("added rule {id}");
    }
    if let Some(id) = change.strip_prefix("criterion ").and_then(|r| r.strip_suffix(" retracted")) {
        return format!("retracted rule {id}");
    }
    if let Some((id, what)) = change.strip_prefix("criterion ").and_then(|r| r.split_once(": ")) {
        let said = if let Some(q) = what.strip_prefix("file under question ") {
            format!("filed rule {id} under {q}")
        } else if let Some(q) = what.strip_prefix("unfile question ") {
            format!("took rule {id} out of {q}")
        } else if let Some(rel) = what.strip_prefix("relate ") {
            format!("recorded that rule {id} {rel}")
        } else if let Some(rel) = what.strip_prefix("withdraw ") {
            format!("withdrew that rule {id} {rel}")
        } else {
            format!("rule {id}: {what}")
        };
        return said;
    }
    change.to_string()
}

/// One write, told, with the voice of its reason: a call's as rb's call lines say it (`call_said`), any other write's as
/// rb log says a write's reason (`view::why_part` of its writer), the user's own as their why and a session's as its note.
fn said(i: usize, row: &Dict, f: &Facts) -> Said {
    let mut s = write_said(i, row, f);
    if s.kind != Kind::Call {
        s.note = matches!(crate::view::why_part(&s.why, row.get_str("actor").unwrap_or("")), Some(("note", _)));
    }
    s
}

/// One write, told: its kind of change, its sentence, the records it names and its reason. Nothing here reads who wrote
/// it, but for whether an interpretation reads as the user's: a session's line says it is not confirmed.
fn write_said(i: usize, row: &Dict, f: &Facts) -> Said {
    let op = row.get_str("op").unwrap_or("");
    let kind = row.get_str("kind").unwrap_or("");
    let id = row.get("id").map(crate::pyjson::py_str).unwrap_or_default();
    let d = detail_of(row);
    let text = |k: &str| d.get_str(k).filter(|s| !s.is_empty()).unwrap_or("").to_string();
    let why = text("why");
    let alone = i.to_string();
    // a change a frozen experiment took is told as an amendment of it, whatever wrote it
    if let Some(Value::Dict(a)) = d.get("amendment") {
        let exp = match kind {
            "experiment" => id.clone(),
            _ => Some(text("experiment")).filter(|e| !e.is_empty()).or_else(|| f.experiment_of.get(&id).cloned()).unwrap_or_else(|| id.clone()),
        };
        let change = a.get_str("change").filter(|c| !c.is_empty()).map(|c| amendment_said(&exp, c)).unwrap_or_else(|| "its spec as it stands".to_string());
        let mut refs = vec![exp.clone()];
        if id != exp {
            refs.push(id.clone());
        }
        let mut s = Said::new(Kind::Amended, &exp, &change, format!("Amended {exp} after locking it: {change}"), refs, a.get_str("why").unwrap_or(""));
        s.on = exp;
        return s;
    }
    match (op, kind) {
        ("init", _) => {
            let title = Some(text("title")).filter(|t| !t.is_empty()).unwrap_or_else(|| f.project.clone());
            Said::new(Kind::Started, &alone, "", format!("Started the record: {title}"), Vec::new(), &why)
        }
        ("clone", "project") => Said::new(Kind::Started, &alone, "", format!("Started the record from a clone: {}", text("title")), Vec::new(), &why),
        ("clone", _) => {
            let mut s = Said::new(Kind::Copied, "", &id, format!("Copied {} from the clone", f.record(kind, &id)), vec![id.clone()], &why);
            s.shown = f.record(kind, &id);
            s
        }
        ("title", _) => Said::new(Kind::Retitled, &alone, "", format!("Retitled the project from \"{}\" to \"{}\"", text("before"), text("title")), Vec::new(), &why),
        ("reword", "question") => {
            Said::new(Kind::Reworded, &alone, "", format!("Reworded question {id} from \"{}\" to \"{}\"", text("before"), text("text")), vec![id.clone()], &why)
        }
        ("add", "question") => {
            let parent = text("parent");
            let under = if parent.is_empty() { String::new() } else { format!(" under {parent}") };
            // a question's own words are said where the question is, escaped as every screen escapes them; History names it
            Said::new(Kind::Question, &parent, &id, format!("Added question {id}{under}"), vec![id.clone(), parent.clone()], &why)
        }
        ("move", _) => {
            let to = match text("parent") {
                p if p.is_empty() => "to the top".to_string(),
                p => format!("under {p}"),
            };
            Said::new(Kind::Moved, &alone, "", format!("Moved {id} {to}"), vec![id.clone(), text("parent")], &why)
        }
        ("add", "metric") => Said::new(Kind::Metric, "", &id, format!("Added metric {id}"), Vec::new(), &why),
        ("add", "experiment") => {
            let one = match f.titles.get(&id).filter(|t| !t.is_empty() && **t != id) {
                Some(t) => format!("Added an experiment: {t} ({id})"),
                None => format!("Added experiment {id}"),
            };
            Said::new(Kind::Experiment, "", &id, one, vec![id.clone()], &why)
        }
        ("add", "resource") => Said::new(Kind::Resource, "", &id, format!("Added resource {id}"), vec![id.clone()], &why),
        ("add", "criterion") => {
            let exp = Some(text("experiment")).filter(|e| !e.is_empty()).or_else(|| f.experiment_of.get(&id).cloned()).unwrap_or_default();
            // a rule's statement may be the words given, said once where the rule is, as given: History names the rule
            Said::new(Kind::Criterion, &exp, &id, format!("Added rule {id} to {exp}"), vec![id.clone(), exp.clone()], &why)
        }
        ("add", _) => Said::new(Kind::Other, &alone, "", format!("Added {} {id}", noun(kind)), vec![id.clone()], &why),
        ("add_variant", _) => {
            let v = format!("{} ({})", text("variant"), text("role"));
            Said::new(Kind::Variant, &id, &v, format!("Added variant {v} to {id}"), vec![id.clone()], &why)
        }
        ("spec_set", _) => {
            let name = text("setting");
            Said::new(Kind::Settings, &id, &name, setting_said(&id, &name, &text("change")), vec![id.clone()], &why)
        }
        ("spec_vary", _) => {
            let names: Vec<String> = d.get("varies").and_then(Value::as_list).map(|l| l.iter().map(crate::pyjson::py_str).collect()).unwrap_or_default();
            let names = and_joined(&names);
            Said::new(Kind::Varies, &id, &names, format!("Declared what {id} varies: {names}"), vec![id.clone()], &why)
        }
        ("spec_verify", _) => {
            let mut names: Vec<String> = d.get("results").and_then(Value::as_list).map(|l| l.iter().filter_map(|r| r.get_str("setting")).map(str::to_string).collect()).unwrap_or_default();
            if names.is_empty() && !text("setting").is_empty() {
                names.push(text("setting"));
            }
            let one = match names.len() {
                0 => format!("Checked the settings of {id} against their sources"),
                1 => format!("Checked {} on {id} against its source", names[0]),
                _ => format!("Checked {} settings on {id} against their sources: {}", names.len(), listed(&names)),
            };
            // whether every check found its value: rb's outcome for one setting, each result's for several
            let found = match d.get("results").and_then(Value::as_list) {
                Some(rs) if !rs.is_empty() => rs.iter().all(|r| r.get("ok") == Some(&Value::Bool(true))),
                _ => text("outcome") == "verified",
            };
            let mut s = Said::new(Kind::Checked, &id, &names.join(", "), one, vec![id.clone()], &why);
            s.checked = Some(found);
            s
        }
        ("freeze", _) => Said::new(Kind::Locked, "", &id, format!("Locked {id}"), vec![id.clone()], &why),
        // a design confirmed while it holds a gate is confirmed with its gate, which rb's line names
        ("gate", _) if !text("design").is_empty() && text("confirms") == "gate" => {
            Said::new(Kind::DesignConfirmed, "gate", &id, format!("Confirmed the design of {id}, with its gate"), vec![id.clone()], &why)
        }
        ("gate", _) if !text("design").is_empty() || text("confirms") == "design" => {
            Said::new(Kind::DesignConfirmed, "", &id, format!("Confirmed the design of {id}"), vec![id.clone()], &why)
        }
        ("gate", _) if text("confirms") == "gate" => Said::new(Kind::GateConfirmed, &id, "", format!("Confirmed the gate of {id}"), vec![id.clone()], &why),
        ("gate", _) => Said::new(Kind::GateSet, &id, "", format!("Set the gate of {id}"), vec![id.clone()], &why),
        ("decide", _) => call_said(&alone, &id, &d, f),
        ("record", _) => {
            let run = f.runs.get(&id).cloned().unwrap_or_default();
            let exp = Some(text("experiment")).filter(|e| !e.is_empty()).unwrap_or(run.experiment);
            // the run by its words, never the id rb minted, which its link keeps (FIX7)
            let at = f.at_words(&id);
            let mut one = match at.clone() {
                Some(w) if w.starts_with(&format!("a run of {exp}")) => format!("Recorded {w}"),
                Some(w) => format!("Recorded a run on {exp}: {w}"),
                None => format!("Recorded a run on {exp}"),
            };
            let mut refs = vec![id.clone()];
            if !text("replaces").is_empty() {
                one.push_str(&format!(", replacing {}", f.word(&text("replaces"))));
                refs.push(text("replaces"));
            }
            if !run.made.is_empty() {
                one.push_str(&format!(", which made {}", and_joined(&run.made)));
                refs.extend(run.made.iter().cloned());
            }
            if !run.row.is_empty() {
                one.push_str(&format!(", from {}", run.row));
            }
            // a run the record does not hold has no words: its id, as rb's log keeps it
            if !f.names.contains_key(&id) {
                one.push_str(&format!(" ({id})"));
            }
            refs.push(exp.clone());
            let mut s = Said::new(Kind::Runs, "", &id, one, refs, &why);
            s.shown = f.names.get(&id).cloned().unwrap_or_default();
            s.at = at.unwrap_or_default();
            s.on = exp;
            s
        }
        ("retract", _) => {
            let scope: Vec<String> = d.get("scope").and_then(Value::as_list).map(|l| l.iter().map(crate::pyjson::py_str).collect()).unwrap_or_default();
            let (what, part, one) = if !scope.is_empty() {
                let of = f.word(&id);
                ("measurement", format!("{} of {of}", and_joined(&scope)), format!("Retracted {} of {of}", and_joined(&scope)))
            } else if !text("variant").is_empty() {
                ("variant", format!("{id}/{}", text("variant")), format!("Retracted variant {} of {id}", text("variant")))
            } else if !text("setting").is_empty() {
                ("setting", format!("{id}/{}", text("setting")), format!("Retracted setting {} of {id}", text("setting")))
            } else if !text("note").is_empty() {
                ("note", text("note"), format!("Retracted a note on {}", if kind == "project" { "the project".to_string() } else { id.clone() }))
            } else if kind == "decision" {
                let on = f.calls.get(&id).map(|c| format!(" on {}", named(&c.subject))).unwrap_or_default();
                ("decision", id.clone(), format!("Retracted a call{on}"))
            } else {
                (kind, id.clone(), format!("Retracted {}", f.record(kind, &id)))
            };
            let target = if kind == "project" { String::new() } else { id.clone() };
            let mut s = Said::new(Kind::Retracted, what, &part, one, vec![target], &why);
            if part == id {
                s.shown = f.names.get(&id).cloned().unwrap_or_default();
            }
            s
        }
        ("analyze", _) => {
            let exp = text("experiment");
            let replacing: Vec<String> = d.get("supersedes").and_then(Value::as_list).map(|l| l.iter().map(crate::pyjson::py_str).collect()).unwrap_or_default();
            // an analysis rb minted an id for by its title, which its link keeps (FIX7); one with a chosen id by that id
            let mut one = match f.names.get(&id) {
                // one with no title says when it was recorded once: "Recorded an analysis on e1 at <minute> UTC"
                Some(w) if w.starts_with(UNTITLED) => format!("Recorded {}", f.at_words(&id).unwrap_or_default()),
                Some(w) => format!("Recorded an analysis on {exp}: {w}"),
                None => format!("Recorded an analysis on {exp}"),
            };
            if !replacing.is_empty() {
                one.push_str(&format!(", replacing {}", and_joined(&replacing.iter().map(|a| f.word(a)).collect::<Vec<_>>())));
            }
            if !f.names.contains_key(&id) {
                one.push_str(&format!(" ({id})"));
            }
            let mut refs = vec![id.clone()];
            refs.extend(replacing.iter().cloned());
            let mut s = Said::new(Kind::Analyses, &exp, &id, one, refs, &why);
            s.shown = f.names.get(&id).cloned().unwrap_or_default();
            s
        }
        ("amend", _) => Said::new(Kind::Amended, &id, "its spec as it stands", format!("Amended {id} after locking it: its spec as it stands"), vec![id.clone()], &why),
        ("note", _) => {
            let on = if kind == "project" { "the project".to_string() } else { id.clone() };
            let refs = if kind == "project" { Vec::new() } else { vec![id.clone()] };
            Said::new(Kind::Notes, &on, &i.to_string(), format!("Added a note on {on}"), refs, &why)
        }
        ("file", "criterion") if matches!(d.get("when_met"), Some(Value::Str(_))) => {
            let q = text("question");
            let when_met = text("when_met");
            let one = if when_met.is_empty() {
                format!("Cleared the note on what rule {id} means for {q}")
            } else {
                // as the user's where they declared it, or their confirmation of the design covers it as it reads now
                let theirs = crate::view::words_are_theirs(row.get_str("actor").unwrap_or("")) || f.owned_links.get(&(id.clone(), q.clone())) == Some(&when_met);
                format!("Added a note on what rule {id} means for {q}{}", if theirs { "" } else { ", not confirmed" })
            };
            Said::new(Kind::Interpretation, &alone, "", one, vec![id.clone(), q], &why)
        }
        ("file", _) => {
            let q = text("question");
            Said::new(Kind::Filed, &q, &id, format!("Filed {id} under {q}"), vec![id.clone(), q.clone()], &why)
        }
        ("unfile", _) => {
            let q = text("question");
            Said::new(Kind::Unfiled, &q, &id, format!("Took {id} out of {q}"), vec![id.clone(), q.clone()], &why)
        }
        ("relate", "experiment") => {
            let one = format!("Recorded that {id} {} {}", text("relation").replace('_', " "), text("target"));
            Said::new(Kind::Related, &alone, "", one, vec![id.clone(), text("target")], &why)
        }
        ("relate", _) => Said::new(Kind::Related, &alone, "", format!("Recorded that rule {id} {} {}", text("kind"), text("target")), vec![id.clone(), text("target")], &why),
        ("unrelate", _) => {
            let one = format!("Withdrew that rule {id} {} {}", text("kind"), text("target"));
            Said::new(Kind::Unrelated, &alone, "", one, vec![id.clone(), text("target")], &why)
        }
        ("locate", _) => Said::new(Kind::Located, "", &id, format!("Recorded where {id} is kept"), vec![id.clone()], &why),
        ("expose", _) => {
            let one = match text("how").as_str() {
                h if h.is_empty() => format!("Recorded how {id} was used"),
                "trained" => format!("Recorded that {id} was trained on"),
                "selected" => format!("Recorded that {id} was used for selection"),
                "evaluated" => format!("Recorded that {id} was evaluated on"),
                h => format!("Recorded that {id} was {h}"),
            };
            Said::new(Kind::Exposed, &alone, "", one, vec![id.clone()], &why)
        }
        ("reconcile", _) => Said::new(Kind::Indexed, &alone, "", format!("Added experiment {id} from {}", text("folder")), vec![id.clone()], &why),
        ("reindex", "project") => Said::new(Kind::Indexed, &alone, "", "Read the repository's checks again".to_string(), Vec::new(), &why),
        ("reindex", _) => Said::new(Kind::Indexed, &alone, "", format!("Read {id} again from {}", text("folder")), vec![id.clone()], &why),
        ("reconciled", _) => Said::new(Kind::Reconciled, &alone, "", reconciled_said(&text("line")), Vec::new(), &why),
        ("adopt", _) => Said::new(Kind::Adopted, "", &f.record(kind, &id), format!("Kept {} as it reads now", f.record(kind, &id)), vec![id.clone()], &why),
        ("adopt_delete", _) => Said::new(Kind::Adopted, &alone, "", format!("Kept the removal of {}", f.record(kind, &id)), Vec::new(), &why),
        ("restore", _) => Said::new(Kind::Restored, "", &f.record(kind, &id), format!("Restored {}", f.record(kind, &id)), vec![id.clone()], &why),
        ("reid", _) => Said::new(Kind::Renamed, &alone, "", format!("Renamed {} {} to {id}", noun(kind), text("was")), vec![id.clone()], &why),
        // an op a newer rb writes, which this rb has no sentence for: what it changed, never the op's own name
        _ => Said::new(Kind::Other, &alone, "", format!("Changed {}", f.record(kind, &id)).trim().to_string(), vec![id.clone()], &why),
    }
}

/// How a confirmation's line opens: the call is the user's now, and the note on it (the reason the session gave) is not;
/// History says that note in rb's note form alone, never "· not confirmed" beside the call (shell/history_view.rs).
pub const CONFIRMED: &str = "Confirmed a call: ";

/// A decision's line: a call made or confirmed, which counts as a call made; a call proposed or a past call mentioned,
/// which never does, and says so.
fn call_said(alone: &str, id: &str, d: &Dict, f: &Facts) -> Said {
    let Some(c) = f.calls.get(id) else {
        let subject = d.get_str("subject").unwrap_or(id);
        return Said::new(Kind::Call, alone, "", format!("Recorded a call on {}", named(subject)), vec![record_of(subject)], "");
    };
    let (one, call) = match &c.standing {
        Standing::Made => (format!("Made a call: {}", done(c)), true),
        Standing::Confirmed => (format!("{CONFIRMED}{}", done(c)), true),
        Standing::PastMade(day) => (format!("Recorded a past call: {}{}", done(c), on_day(day)), true),
        Standing::Proposed => (format!("Proposed a call to {} {}, not made", c.action, named(&c.subject)), false),
        Standing::PastMentioned(day, source) => {
            let from = if source.is_empty() { String::new() } else { format!(", from {source}") };
            (format!("Mentioned a past call to {} {}{}{from}, not confirmed", c.action, named(&c.subject), on_day(day)), false)
        }
        Standing::Unread => (format!("Recorded a call on {}, edited outside rb since", named(&c.subject)), false),
    };
    let mut s = Said::new(Kind::Call, alone, "", one, vec![record_of(&c.subject)], &c.why);
    s.call = call;
    // as ledger::reason_said says a call's reason: the user's own call's is their why; a proposal's, a past call's a
    // session mentioned and the one a confirmation carries are the session's note, and a file edited outside rb is nobody's
    s.note = !matches!(c.standing, Standing::Made | Standing::PastMade(_));
    s
}

/// A call as made: "accepted c1", "put c2 under investigation".
fn done(c: &Call) -> String {
    match c.action.as_str() {
        "investigate" => format!("put {} under investigation", named(&c.subject)),
        a => format!("{} {}", crate::ledger::made(a), named(&c.subject)),
    }
}

fn on_day(day: &str) -> String {
    if day.is_empty() { String::new() } else { format!(", dated {day}") }
}

/// The day a past call was given as made on, as given; nothing where nobody said.
fn day_given(when: &str) -> String {
    match when.trim() {
        "unknown" => String::new(),
        w => w.to_string(),
    }
}

/// A call's subject as a screen says it: a setting (`e1/lr`) as "lr of e1", any other record by its id.
fn named(subject: &str) -> String {
    match subject.split_once('/') {
        Some((exp, setting)) if !exp.is_empty() && !setting.is_empty() => format!("{setting} of {exp}"),
        _ => subject.to_string(),
    }
}

/// The record a call is on: a setting's (`e1/lr`) is its experiment's.
fn record_of(subject: &str) -> String {
    subject.split('/').next().unwrap_or(subject).to_string()
}

fn detail_of(row: &Dict) -> Dict {
    match row.get("detail") {
        Some(Value::Dict(d)) => d.clone(),
        _ => Dict::new(),
    }
}

fn at_of(row: &Dict) -> &str {
    row.get_str("at").unwrap_or("")
}

/// Whether `b` follows `a` by five minutes or less. A time rb cannot read starts a sitting of its own.
fn within(a: &str, b: &str) -> bool {
    match (crate::pytime::parse_iso(a), crate::pytime::parse_iso(b)) {
        (Some(a), Some(b)) => (b - a).num_seconds() <= GAP_SECONDS,
        _ => false,
    }
}

/// "2026-10-19 09:25 UTC" of a log time.
fn minute(at: &str) -> String {
    match crate::pytime::parse_iso(at) {
        Some(t) => t.with_timezone(&chrono::Utc).format("%Y-%m-%d %H:%M UTC").to_string(),
        None => at.chars().take(16).collect::<String>().replace('T', " "),
    }
}

/// "2026-10-19, 09:25 to 09:40 UTC · 4 changes" of a span `minute` gave: one time where both are one minute, the second
/// day where the span crosses midnight. With the calls made after it, a head is three items, as every line of a screen.
fn span_head(first: &str, last: &str, n: usize) -> String {
    let writes = counted(n, "change", "changes");
    let (Some((d0, t0)), Some((d1, t1))) = (split_minute(first), split_minute(last)) else { return format!("{first} · {writes}") };
    let span = match () {
        _ if d0 == d1 && t0 == t1 => format!("{t0} UTC"),
        _ if d0 == d1 => format!("{t0} to {t1} UTC"),
        _ => format!("{t0} to {d1} {t1} UTC"),
    };
    format!("{d0}, {span} · {writes}")
}

fn split_minute(m: &str) -> Option<(&str, &str)> {
    let m = m.strip_suffix(" UTC")?;
    m.split_once(' ')
}

fn calls_said(n: usize) -> String {
    if n == 0 { "no calls made".to_string() } else { counted(n, "call made", "calls made") }
}

fn counted(n: usize, one: &str, many: &str) -> String {
    format!("{n} {}", if n == 1 { one } else { many })
}

/// "runs", "criteria": a kind of record a retraction line counts.
fn plural_of(what: &str) -> String {
    match what {
        "criterion" => "rules".into(),
        "analysis" => "analyses".into(),
        "decision" => "calls".into(),
        "measurement" => "measurements".into(),
        w => format!("{w}s"),
    }
}

/// The first five, then how many more.
fn listed(items: &[String]) -> String {
    crate::assess::first_few(items, 5, ", ")
}

/// The first five, then how many more, parted by semicolons where one holds a comma (a run's words can).
fn parted(items: &[String]) -> String {
    crate::assess::first_few(items, 5, if items.iter().any(|x| x.contains(',')) { "; " } else { ", " })
}

/// "a", "a and b", "a, b and c".
fn and_joined(items: &[String]) -> String {
    match items {
        [] => String::new(),
        [one] => one.clone(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::ledger::{NewCriterion, NewExperiment};
    use crate::value::Dict;

    /// A log line at 2026-10-06 10:00 UTC plus `secs`, by `actor`, in `session`, through `via`.
    fn line_at(secs: i64, actor: &str, session: &str, via: &str, op: &str, kind: &str, id: &str, detail: Value) -> Dict {
        let at = crate::pytime::parse_iso("2026-10-06T10:00:00+00:00").unwrap() + chrono::Duration::seconds(secs);
        let mut d = crate::dict! {"at" => at.format("%Y-%m-%dT%H:%M:%S%.3f+00:00").to_string(), "actor" => actor, "actor_via" => "detected:CLAUDECODE",
            "via" => via, "op" => op, "kind" => kind, "id" => id, "detail" => detail};
        if !session.is_empty() {
            d.insert("session".into(), Value::Str(session.into()));
        }
        d
    }

    fn sentences(h: &History) -> Vec<Vec<String>> {
        h.sittings.iter().map(|s| s.lines.iter().map(|l| l.sentence.clone()).collect()).collect()
    }

    fn strings(v: &Value, out: &mut Vec<String>) {
        match v {
            Value::Str(s) => out.push(s.clone()),
            Value::List(l) => l.iter().for_each(|x| strings(x, out)),
            Value::Dict(d) => d.iter().for_each(|(k, x)| {
                out.push(k.clone());
                strings(x, out)
            }),
            _ => {}
        }
    }

    struct Scratch(std::path::PathBuf);
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn scratch() -> Scratch {
        let dir = Scratch(std::env::temp_dir().join(format!("rb-history-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32))));
        std::fs::create_dir_all(&dir.0).unwrap();
        dir
    }

    #[test]
    fn history_splits_on_time_only() {
        // three writers in three sessions through two channels, within five minutes of each other, are one sitting, and
        // two criteria on one experiment by two of them are one line; a gap of exactly five minutes still joins, one
        // second more splits
        let ana = "human:ana";
        let (cc, cx) = ("agent:claude-code", "agent:codex");
        let rows = vec![
            line_at(0, ana, "", "cli", "add", "question", "q1", crate::obj! {"parent" => Value::Null}),
            line_at(60, cc, "sess-a", "cli", "session", "session", "sess-a", crate::obj! {}),
            line_at(60, cc, "sess-a", "cli", "add", "experiment", "e1", crate::obj! {"questions" => crate::list!["q1"]}),
            line_at(120, cx, "sess-b", "mcp", "add", "criterion", "c1", crate::obj! {"experiment" => "e1", "rule" => "change.top1 ≥ -0.005"}),
            line_at(170, ana, "", "cli", "add", "criterion", "c2", crate::obj! {"experiment" => "e1", "rule" => "change.top1 ≥ -0.01"}),
            line_at(470, cc, "sess-a", "sdk", "freeze", "experiment", "e1", crate::obj! {"why" => "before the runs"}),
            line_at(771, ana, "", "cli", "add", "question", "q2", crate::obj! {"parent" => "q1"}),
        ];
        let h = told(&rows, &Facts::default());
        assert_eq!(h.writes, 6, "a session's line is no write");
        assert_eq!(h.sittings.iter().map(|s| s.n).collect::<Vec<_>>(), vec![1, 5], "newest first, split at the gap over five minutes only");
        assert_eq!(
            sentences(&h),
            vec![
                vec!["Added question q2 under q1".to_string()],
                vec!["Added question q1".into(), "Added experiment e1".into(), "Added 2 rules to e1: c1, c2".into(), "Locked e1".into()],
            ]
        );
        let old = &h.sittings[1];
        assert_eq!((old.first.as_str(), old.last.as_str()), ("2026-10-06 10:00 UTC", "2026-10-06 10:07 UTC"));
        assert_eq!(old.head, "2026-10-06, 10:00 to 10:07 UTC · 5 changes · no calls made");
        assert_eq!(h.sittings[0].head, "2026-10-06, 10:12 UTC · 1 change · no calls made");
        // where each line's first write sits in the log, session lines counted, as the log rows count them
        assert_eq!(old.lines.iter().map(|l| l.log_index).collect::<Vec<_>>(), vec![0, 2, 3, 5]);
        assert_eq!(old.lines[2].n, 2);
        // the session's reason to lock is its note, never the user's why
        assert_eq!((old.lines[3].why.as_str(), old.lines[3].note.as_str()), ("", "before the runs"));
        // the same writes, every one by one writer, in one session, through one channel, are split and told the same:
        // only a reason's voice follows its writer
        let one: Vec<Dict> = rows
            .iter()
            .map(|r| {
                let mut r = r.clone();
                r.insert("actor".into(), Value::Str(ana.into()));
                r.insert("via".into(), Value::Str("cli".into()));
                r.shift_remove("session");
                r
            })
            .collect();
        let unvoiced = |h: &History| {
            let mut h = h.clone();
            for l in h.sittings.iter_mut().flat_map(|s| s.lines.iter_mut()) {
                l.why = format!("{}{}", l.why, std::mem::take(&mut l.note));
            }
            h
        };
        assert_eq!(unvoiced(&told(&one, &Facts::default())), unvoiced(&h));
        assert_eq!(h.summary(), "6 changes recorded");
    }

    fn call(subject: &str, action: &str, why: &str, standing: Standing) -> Call {
        Call { subject: subject.into(), action: action.into(), why: why.into(), standing }
    }

    #[test]
    fn a_proposal_is_not_a_call_made() {
        // a session's proposal and a past call it mentions wait for a confirmation and change nothing: neither counts
        // as a call made, in the sitting's head or in Latest changes, and each says so; the user's own call and their
        // confirmation of a proposal each count once
        let agent = "agent:claude-code";
        let ana = "human:ana";
        let mut f = Facts::default();
        f.calls.insert("dprop".into(), call("c3", "accept", "one seed is enough for the export check", Standing::Proposed));
        f.calls.insert("dpast".into(), call("e1", "park", "the notes say so", Standing::PastMentioned("2026-05-07".into(), "notes.md:3".into())));
        f.calls.insert("dmade".into(), call("c1", "accept", "holds on all three seeds", Standing::Made));
        f.calls.insert("dconf".into(), call("c3", "accept", "one seed is enough for the export check", Standing::Confirmed));
        let decide = |secs, actor, id: &str, subject: &str, action: &str, confirms: Option<&str>| {
            let mut d = crate::dict! {"subject" => subject, "action" => action, "value" => Value::Null};
            if let Some(c) = confirms {
                d.insert("confirms".into(), Value::Str(c.into()));
            }
            line_at(secs, actor, "sess-a", "cli", "decide", "decision", id, Value::Dict(d))
        };
        let waiting = vec![decide(0, agent, "dprop", "c3", "accept", None), decide(60, agent, "dpast", "e1", "park", None)];
        let h = told(&waiting, &f);
        assert_eq!(h.sittings[0].calls, 0);
        assert_eq!(
            sentences(&h)[0],
            vec!["Proposed a call to accept c3, not made", "Mentioned a past call to park e1, dated 2026-05-07, from notes.md:3, not confirmed"]
        );
        let latest = h.latest().unwrap();
        assert_eq!((latest.calls_made, latest.line.as_str()), (0, "Latest changes: 2026-10-06, 10:00 to 10:01 UTC · 2 changes · no calls made"));
        // the session's reason for its proposal is its note; the user's own call keeps theirs
        assert_eq!((h.sittings[0].lines[0].why.as_str(), h.sittings[0].lines[0].note.as_str()), ("", "one seed is enough for the export check"));
        // the user confirms the proposal, then makes a call of their own
        let mut all = waiting.clone();
        all.push(decide(120, ana, "dconf", "c3", "accept", Some("dprop")));
        all.push(decide(180, ana, "dmade", "c1", "accept", None));
        let h = told(&all, &f);
        assert_eq!(sentences(&h)[0][2..], ["Confirmed a call: accepted c3", "Made a call: accepted c1"]);
        let voices: Vec<(&str, &str)> = h.sittings[0].lines.iter().map(|l| (l.why.as_str(), l.note.as_str())).collect();
        assert_eq!(
            voices,
            vec![
                ("", "one seed is enough for the export check"),
                ("", "the notes say so"),
                ("", "one seed is enough for the export check"),
                ("holds on all three seeds", "")
            ],
            "a confirmation carries the session's reason, which stays its note"
        );
        assert_eq!(h.sittings[0].calls, 2);
        assert!(h.sittings[0].head.ends_with(" · 4 changes · 2 calls made"), "{}", h.sittings[0].head);
        let latest = h.latest().unwrap();
        assert_eq!((latest.calls_made, latest.head.as_str()), (2, "2026-10-06, 10:00 to 10:03 UTC · 4 changes"));
        assert_eq!(latest.line, "Latest changes: 2026-10-06, 10:00 to 10:03 UTC · 4 changes · 2 calls made");
        // a call whose file was edited outside rb since is nobody's: it is not counted, and what its file says is no
        // one's why
        f.calls.get_mut("dmade").unwrap().standing = Standing::Unread;
        let h = told(&all, &f);
        assert_eq!((h.sittings[0].calls, h.sittings[0].lines[3].sentence.as_str()), (1, "Recorded a call on c1, edited outside rb since"));
        assert_eq!(h.sittings[0].lines[3].why, "");
    }

    #[test]
    fn a_proposal_rb_wrote_is_not_a_call_made() {
        // through rb's own writes: a session proposes a call and mentions a past one, and the record reads both as
        // waiting, so the history counts no call made
        let _turn = crate::sys::turns::starting();
        let dir = scratch();
        let led = Ledger::init(&dir.0, "INT8 ResNet-50 on ImageNet", Some("int8"), Some("sdk"), Some("wf-agent-x"), false).unwrap();
        led.add_question("Does INT8 keep top-1 within half a point?", Some("q1"), None).unwrap();
        led.decide("q1", "continue", "keep going with three seeds", "", None).unwrap();
        led.decide("q1", "park", "the notes park it", "", Some(&crate::ledger::PastGiven { when: "2026-05-07".into(), source: None })).unwrap();
        let h = of(&led).unwrap();
        assert_eq!(h.sittings.len(), 1);
        let s = &h.sittings[0];
        assert_eq!(s.calls, 0, "{s:?}");
        assert_eq!(
            s.lines.iter().map(|l| l.sentence.as_str()).collect::<Vec<_>>(),
            vec![
                "Started the record: INT8 ResNet-50 on ImageNet",
                "Added question q1",
                "Proposed a call to continue q1, not made",
                "Mentioned a past call to park q1, dated 2026-05-07, not confirmed"
            ]
        );
        assert!(h.latest().unwrap().line.ends_with(" · 4 changes · no calls made"));
        // and nothing in it names who wrote
        let mut said = Vec::new();
        strings(&h.to_value(), &mut said);
        strings(&h.latest().unwrap().to_value(), &mut said);
        for s in &said {
            for mark in ["wf-agent-x", "agent", "human", "session", "sdk", " via "] {
                assert!(!s.contains(mark), "{mark:?} in {s:?}");
            }
        }
    }

    #[test]
    fn no_names_in_any_sitting() {
        // a write of every kind the history tells, each by a named writer in a session through a channel, with the
        // user's words given, in a worktree on a branch named as an agent names its own: no sitting, line, head or
        // Latest changes says any of it
        let marks = ["wf-ana", "wf-agent-x", "test-model-wf", "sess-wf-77", "detected:", "claude/wf", "wf-worktree", "the words wf gave", "agent", "human", "session", " via "];
        let mut rows: Vec<Dict> = Vec::new();
        let mut at = 0;
        let mut w = |actor: &str, op: &str, kind: &str, id: &str, detail: Value| {
            at += 30;
            let mut r = line_at(at, actor, "sess-wf-77", "mcp", op, kind, id, detail);
            r.insert("agent".into(), crate::obj! {"name" => "wf-agent-x", "model" => "test-model-wf", "provider" => "anthropic"});
            r.insert("words".into(), Value::Str("the words wf gave".into()));
            r.insert("worktree".into(), crate::obj! {"path" => "/tmp/wf-worktree", "branch" => "claude/wf"});
            r.insert("ignored_rb_actor".into(), Value::Str("human:wf-ana".into()));
            rows.push(r);
        };
        let (h_, a_) = ("human:wf-ana", "agent:wf-agent-x");
        w(h_, "init", "project", "int8", crate::obj! {"title" => "INT8 ResNet-50 on ImageNet"});
        w(a_, "title", "project", "int8", crate::obj! {"before" => "INT8", "title" => "INT8 ResNet-50 on ImageNet", "why" => "a clearer title"});
        w(a_, "add", "question", "q1", crate::obj! {"parent" => Value::Null});
        w(h_, "move", "question", "q1", crate::obj! {"parent" => Value::Null, "why" => "it stands on its own"});
        w(a_, "add", "metric", "top1", crate::obj! {});
        w(a_, "add", "experiment", "e1", crate::obj! {"questions" => crate::list!["q1"]});
        w(a_, "add_variant", "experiment", "e1", crate::obj! {"variant" => "int8", "role" => "candidate"});
        w(a_, "spec_set", "experiment", "e1", crate::obj! {"setting" => "calib_images", "change" => "setting calib_images: new, 512"});
        w(a_, "spec_vary", "experiment", "e1", crate::obj! {"varies" => crate::list!["precision"]});
        w(a_, "spec_verify", "experiment", "e1", crate::obj! {"results" => crate::list![crate::obj! {"setting" => "calib_images", "ok" => true}]});
        w(a_, "add", "resource", "imagenet-val", crate::obj! {"type" => "dataset"});
        w(a_, "locate", "resource", "imagenet-val", crate::obj! {"provider" => "local", "ref" => "/tmp/wf-worktree/data"});
        w(a_, "expose", "resource", "imagenet-val", crate::obj! {"how" => "evaluated"});
        w(a_, "add", "criterion", "c1", crate::obj! {"experiment" => "e1", "rule" => "change.top1 ≥ -0.005"});
        w(a_, "file", "criterion", "c1", crate::obj! {"question" => "q1", "when_met" => "INT8 keeps top-1", "why" => "the same comparison"});
        w(a_, "file", "experiment", "e1", crate::obj! {"question" => "q1"});
        w(a_, "relate", "criterion", "c1", crate::obj! {"kind" => "reverses", "target" => "c2", "why" => "one change"});
        w(a_, "unrelate", "criterion", "c1", crate::obj! {"kind" => "reverses", "target" => "c2"});
        w(a_, "relate", "experiment", "e2", crate::obj! {"relation" => "follows_from", "target" => "e1"});
        w(a_, "unfile", "criterion", "c1", crate::obj! {"question" => "q1"});
        w(a_, "gate", "experiment", "e1", crate::obj! {"before" => crate::obj! {"if_holds" => "", "if_not" => ""}, "why" => "plainer words"});
        w(a_, "freeze", "experiment", "e1", crate::obj! {"why" => "before the runs"});
        w(a_, "spec_set", "experiment", "e1", crate::obj! {"setting" => "calib_images", "amendment" => crate::obj! {"change" => "setting calib_images: 512 -> 1024", "why" => "more images"}});
        w(h_, "gate", "experiment", "e1", crate::obj! {"confirms" => "design", "design" => "abc1234"});
        w(h_, "gate", "experiment", "e1", crate::obj! {"confirms" => "gate", "if_holds" => "ship INT8", "if_not" => "keep fp32"});
        w(h_, "gate", "experiment", "e2", crate::obj! {"confirms" => "gate", "design" => "def5678", "if_holds" => "ship INT8", "if_not" => "keep fp32"});
        w(a_, "record", "run", "runs1", crate::obj! {"experiment" => "e1"});
        w(a_, "record", "run", "runs2b", crate::obj! {"experiment" => "e1", "replaces" => "runs2"});
        w(a_, "retract", "run", "runs2", crate::obj! {"why" => "replaced by runs2b: the train split", "replaced_by" => "runs2b"});
        w(a_, "retract", "run", "runs1", crate::obj! {"why" => "evaluated on the train split", "scope" => crate::list!["int8.top1"]});
        w(a_, "analyze", "analysis", "a1", crate::obj! {"experiment" => "e1", "supersedes" => crate::list![]});
        w(a_, "record", "run", "runs3", crate::obj! {"experiment" => "e1"});
        w(a_, "note", "experiment", "e1", crate::obj! {"text" => "a note on e1"});
        w(a_, "note", "project", "int8", crate::obj! {"text" => "a project note"});
        w(a_, "decide", "decision", "dprop", crate::obj! {"subject" => "c1", "action" => "accept"});
        w(h_, "decide", "decision", "dconf", crate::obj! {"subject" => "c1", "action" => "accept", "confirms" => "dprop"});
        w(a_, "decide", "decision", "dgone", crate::obj! {"subject" => "e1/calib_images", "action" => "accept"});
        w(a_, "reconcile", "experiment", "sweep", crate::obj! {"folder" => "results/sweep/"});
        w(a_, "reconciled", "project", "int8", crate::obj! {"line" => "+1 experiment (sweep)"});
        w(h_, "adopt", "note", "n1", crate::obj! {"why" => "reworded it"});
        w(h_, "restore", "experiment", "e1", crate::obj! {});
        w(h_, "reid", "experiment", "e3", crate::obj! {"was" => "e9"});
        w(a_, "clone", "experiment", "e4", crate::obj! {});
        w(a_, "a_newer_op", "experiment", "e1", crate::obj! {});
        let mut f = Facts::default();
        f.calls.insert("dprop".into(), call("c1", "accept", "it holds", Standing::Proposed));
        f.calls.insert("dconf".into(), call("c1", "accept", "it holds", Standing::Confirmed));
        let h = told(&rows, &f);
        let mut said = Vec::new();
        strings(&h.to_value(), &mut said);
        strings(&h.latest().unwrap().to_value(), &mut said);
        for s in &said {
            for mark in marks {
                assert!(!s.contains(mark), "{mark:?} in {s:?}");
            }
        }
        assert_eq!(h.sittings.len(), 1);
        assert_eq!(h.sittings[0].n, rows.len());
        assert_eq!(h.sittings[0].calls, 1);
        let all = sentences(&h).concat();
        for want in [
            "Retitled the project from \"INT8\" to \"INT8 ResNet-50 on ImageNet\"",
            "Added a note on what rule c1 means for q1, not confirmed",
            "Amended e1 after locking it: changed calib_images from 512 to 1024",
            "Confirmed the design of e1",
            "Confirmed the gate of e1",
            "Confirmed the design of e2, with its gate",
            "Set the gate of e1",
            "Recorded 2 runs: runs1, runs2b",
            "Retracted int8.top1 of runs1",
            "Recorded a run on e1 (runs3)",
            "Read the repository with rb reconcile and added 1 experiment (sweep)",
            "Added experiment sweep from results/sweep/",
            "Recorded a call on calib_images of e1",
            "Changed experiment e1",
        ] {
            assert!(all.iter().any(|s| s == want), "{want:?} not in {all:#?}");
        }
        // an op this rb has no sentence for is told in rb's words, never by its own name
        assert!(!all.iter().any(|s| s.contains("a_newer_op")), "{all:#?}");
    }

    #[test]
    fn consecutive_writes_of_one_kind_are_one_line() {
        let ana = "human:ana";
        let mut f = Facts::default();
        f.runs.insert("runs1".into(), RunFacts { experiment: "e1".into(), made: vec!["ckpt-int8-s1".into()], row: String::new() });
        f.runs.insert("runsql7".into(), RunFacts { experiment: "e1".into(), made: Vec::new(), row: "row 7 of table runs".into() });
        f.titles.insert("e1".into(), "int8 against fp32".into());
        let amendment = crate::obj! {"change" => "new criterion c2: change.top1 ≥ -0.01", "why" => "a second budget"};
        let rows = vec![
            line_at(0, ana, "", "cli", "add", "experiment", "e1", crate::obj! {}),
            line_at(10, ana, "", "cli", "add", "criterion", "c1", crate::obj! {"experiment" => "e1"}),
            line_at(20, ana, "", "cli", "add", "criterion", "c3", crate::obj! {"experiment" => "e1"}),
            line_at(30, ana, "", "cli", "file", "criterion", "c1", crate::obj! {"question" => "q1"}),
            line_at(40, ana, "", "cli", "file", "criterion", "c3", crate::obj! {"question" => "q1"}),
            line_at(45, ana, "", "cli", "file", "experiment", "e1", crate::obj! {"question" => "q1"}),
            line_at(50, ana, "", "cli", "freeze", "experiment", "e1", crate::obj! {}),
            line_at(60, ana, "", "cli", "record", "run", "runs1", crate::obj! {"experiment" => "e1"}),
            line_at(70, ana, "", "cli", "record", "run", "runs2", crate::obj! {"experiment" => "e1"}),
            // one amendment rb writes as two lines: the experiment's and the new rule's
            line_at(80, ana, "", "cli", "amend", "experiment", "e1", crate::obj! {"amendment" => amendment.clone()}),
            line_at(80, ana, "", "cli", "add", "criterion", "c2", crate::obj! {"experiment" => "e1", "amendment" => amendment, "why" => "a second budget"}),
            // a run that replaces another, and the retraction rb writes with it
            line_at(100, ana, "", "cli", "record", "run", "runs2b", crate::obj! {"experiment" => "e1", "replaces" => "runs2"}),
            line_at(100, ana, "", "cli", "retract", "run", "runs2", crate::obj! {"why" => "replaced by runs2b: ", "replaced_by" => "runs2b"}),
            line_at(110, ana, "", "cli", "note", "experiment", "e1", crate::obj! {"text" => "a"}),
            line_at(111, ana, "", "cli", "note", "experiment", "e1", crate::obj! {"text" => "b"}),
            line_at(120, ana, "", "cli", "record", "run", "runsql7", crate::obj! {"experiment" => "e1"}),
        ];
        let h = told(&rows, &f);
        let lines = &h.sittings[0].lines;
        assert_eq!(
            lines.iter().map(|l| (l.sentence.as_str(), l.n)).collect::<Vec<_>>(),
            vec![
                ("Added an experiment: int8 against fp32 (e1)", 1),
                ("Added 2 rules to e1: c1, c3", 2),
                ("Filed c1, c3 and e1 under q1", 3),
                ("Locked e1", 1),
                ("Recorded 2 runs: runs1, runs2", 2),
                ("Amended e1 after locking it: added rule c2", 2),
                ("Recorded a run on e1, replacing runs2 (runs2b)", 2),
                ("Added 2 notes on e1", 2),
                ("Recorded a run on e1, from row 7 of table runs (runsql7)", 1),
            ]
        );
        assert_eq!(lines[4].refs, vec!["runs1", "ckpt-int8-s1", "e1", "runs2"]);
        assert_eq!(lines[5].refs, vec!["e1", "c2"]);
        assert_eq!(lines[5].why, "a second budget");
        assert_eq!(lines[6].refs, vec!["runs2b", "runs2", "e1"]);
        assert_eq!(lines.iter().map(|l| l.log_index).collect::<Vec<_>>(), vec![0, 1, 3, 6, 7, 9, 11, 13, 15]);
        // one run alone says what it made
        let h = told(&rows[7..8], &f);
        assert_eq!(h.sittings[0].lines[0].sentence, "Recorded a run on e1, which made ckpt-int8-s1 (runs1)");
        // many runs are counted by experiment
        let many: Vec<Dict> = (0..7).map(|k| line_at(k * 10, ana, "", "cli", "record", "run", &format!("r{k}"), crate::obj! {"experiment" => if k < 4 { "e1" } else { "e2" }})).collect();
        assert_eq!(told(&many, &f).sittings[0].lines[0].sentence, "Recorded 7 runs: 4 on e1, 3 on e2");
        // two writes with reasons of their own are two lines
        let two = vec![
            line_at(0, ana, "", "cli", "retract", "run", "runs1", crate::obj! {"why" => "evaluated on the train split"}),
            line_at(10, ana, "", "cli", "retract", "run", "runs2", crate::obj! {"why" => "a broken checkpoint"}),
            line_at(20, ana, "", "cli", "retract", "run", "runs3", crate::obj! {"why" => "a broken checkpoint"}),
        ];
        let h = told(&two, &f);
        assert_eq!(sentences(&h)[0], vec!["Retracted run runs1", "Retracted 2 runs: runs2, runs3"]);
        assert_eq!(h.sittings[0].lines[1].why, "a broken checkpoint");
    }

    #[test]
    fn a_heads_span_says_its_days_and_an_unread_time_stands_alone() {
        let ana = "human:ana";
        let rows = vec![
            line_at(50_280, ana, "", "cli", "add", "question", "q1", crate::obj! {}),
            line_at(50_520, ana, "", "cli", "add", "question", "q2", crate::obj! {}),
        ];
        // 2026-10-06 23:58 to 2026-10-07 00:02
        let h = told(&rows, &Facts::default());
        assert_eq!(h.sittings[0].head, "2026-10-06, 23:58 to 2026-10-07 00:02 UTC · 2 changes · no calls made");
        let mut odd = rows.clone();
        odd.insert(1, crate::dict! {"at" => "sometime", "op" => "add", "kind" => "question", "id" => "q9", "actor" => ana});
        let h = told(&odd, &Facts::default());
        assert_eq!(h.sittings.len(), 3, "{h:?}");
        assert_eq!(h.sittings[1].head, "sometime · 1 change · no calls made");
        assert!(told(&[], &Facts::default()).latest().is_none());
    }

    #[test]
    fn the_history_writes_nothing() {
        // reading the history leaves every file of the record as it was, the user's seen marker included (rb open draws
        // it and never writes the marker)
        let _turn = crate::sys::turns::starting();
        let dir = scratch();
        let led = Ledger::init(&dir.0, "INT8 ResNet-50 on ImageNet", Some("int8"), Some("sdk"), Some("wf-agent-x"), false).unwrap();
        led.add_question("Does INT8 keep top-1?", Some("q1"), None).unwrap();
        let files = |root: &std::path::Path| {
            let mut out = Vec::new();
            let mut todo = vec![root.to_path_buf()];
            while let Some(p) = todo.pop() {
                for e in std::fs::read_dir(&p).unwrap().flatten() {
                    let md = e.metadata().unwrap();
                    if md.is_dir() {
                        todo.push(e.path());
                    } else {
                        out.push((e.path(), md.len(), md.modified().ok()));
                    }
                }
            }
            out.sort();
            out
        };
        let before = files(&dir.0);
        let led = Ledger::new(&dir.0, "sdk", Some("wf-agent-x".into()));
        let h = of(&led).unwrap();
        assert_eq!(h.writes, 2);
        assert_eq!(files(&dir.0), before);
    }

    #[test]
    fn a_sessions_reason_is_its_note() {
        // the reason a write gave reads in its voice: the user's own as their why, a session's as its note, for every
        // kind of write that gives one; two writes of one kind with the same words, one the user's and one a session's,
        // are two lines, so neither takes the other's voice
        let (ana, cc) = ("human:ana", "agent:claude-code");
        let mut f = Facts::default();
        f.calls.insert("dmade".into(), call("c1", "accept", "holds on all three seeds", Standing::Made));
        f.calls.insert("dpastmade".into(), call("e1", "park", "the notes park it", Standing::PastMade("2026-05-07".into())));
        f.calls.insert("dprop".into(), call("c3", "accept", "one seed is enough", Standing::Proposed));
        f.calls.insert("dconf".into(), call("c3", "accept", "one seed is enough", Standing::Confirmed));
        f.calls.insert("dpast".into(), call("e1", "park", "the notes say so", Standing::PastMentioned(String::new(), String::new())));
        f.calls.insert("dgone".into(), call("c4", "accept", "edited since", Standing::Unread));
        let decide = |secs, actor, id: &str| line_at(secs, actor, "", "cli", "decide", "decision", id, crate::obj! {"subject" => "x", "action" => "accept"});
        let amendment = |why: &str| crate::obj! {"setting" => "calib_images", "amendment" => crate::obj! {"change" => "setting calib_images: 512 -> 1024", "why" => why}};
        let rows = vec![
            line_at(0, ana, "", "cli", "title", "project", "int8", crate::obj! {"before" => "INT8", "title" => "INT8 study", "why" => "a clearer title"}),
            line_at(10, cc, "sess-a", "cli", "title", "project", "int8", crate::obj! {"before" => "INT8 study", "title" => "INT8", "why" => "a shorter title"}),
            line_at(20, ana, "", "cli", "freeze", "experiment", "e1", crate::obj! {"why" => "before the runs"}),
            line_at(30, cc, "sess-a", "cli", "freeze", "experiment", "e2", crate::obj! {"why" => "before the runs"}),
            line_at(40, ana, "", "cli", "spec_set", "experiment", "e1", amendment("more images")),
            line_at(50, cc, "sess-a", "cli", "spec_set", "experiment", "e2", amendment("more images")),
            line_at(60, cc, "sess-a", "cli", "retract", "run", "runs1", crate::obj! {"why" => "evaluated on the train split"}),
            line_at(70, ana, "", "cli", "retract", "run", "runs2", crate::obj! {"why" => "evaluated on the train split"}),
            line_at(80, cc, "sess-a", "cli", "file", "criterion", "c1", crate::obj! {"question" => "q1", "when_met" => "INT8 keeps top-1", "why" => "the same comparison"}),
            decide(90, ana, "dmade"),
            decide(100, ana, "dpastmade"),
            decide(110, cc, "dprop"),
            decide(120, ana, "dconf"),
            decide(130, cc, "dpast"),
            decide(140, ana, "dgone"),
        ];
        let h = told(&rows, &f);
        let got: Vec<(&str, &str, &str)> = h.sittings[0].lines.iter().map(|l| (l.sentence.as_str(), l.why.as_str(), l.note.as_str())).collect();
        assert_eq!(
            got,
            vec![
                ("Retitled the project from \"INT8\" to \"INT8 study\"", "a clearer title", ""),
                ("Retitled the project from \"INT8 study\" to \"INT8\"", "", "a shorter title"),
                ("Locked e1", "before the runs", ""),
                ("Locked e2", "", "before the runs"),
                ("Amended e1 after locking it: changed calib_images from 512 to 1024", "more images", ""),
                ("Amended e2 after locking it: changed calib_images from 512 to 1024", "", "more images"),
                ("Retracted run runs1", "", "evaluated on the train split"),
                ("Retracted run runs2", "evaluated on the train split", ""),
                ("Added a note on what rule c1 means for q1, not confirmed", "", "the same comparison"),
                ("Made a call: accepted c1", "holds on all three seeds", ""),
                ("Recorded a past call: parked e1, dated 2026-05-07", "the notes park it", ""),
                ("Proposed a call to accept c3, not made", "", "one seed is enough"),
                ("Confirmed a call: accepted c3", "", "one seed is enough"),
                ("Mentioned a past call to park e1, not confirmed", "", "the notes say so"),
                ("Recorded a call on c4, edited outside rb since", "", "edited since"),
            ]
        );
        // the document holds both, a session's reason never under why
        let v = h.sittings[0].lines[3].to_value();
        assert_eq!((v.get_str("why"), v.get_str("note")), (Some(""), Some("before the runs")));
    }

    #[test]
    fn a_design_confirmed_with_its_gate_says_so() {
        // rb confirm of a design that holds a gate confirms the gate with it, and its line says so; designs confirmed
        // with and without one are lines apart
        let ana = "human:ana";
        let gate = |secs, id: &str, with_gate: bool| {
            let confirms = if with_gate { "gate" } else { "design" };
            line_at(secs, ana, "", "cli", "gate", "experiment", id, crate::obj! {"confirms" => confirms, "design" => "abc1234"})
        };
        let h = told(&[gate(0, "e1", true)], &Facts::default());
        assert_eq!(sentences(&h)[0], vec!["Confirmed the design of e1, with its gate"]);
        let h = told(&[gate(0, "e1", true), gate(10, "e2", true), gate(20, "e3", false), gate(30, "e4", false)], &Facts::default());
        assert_eq!(sentences(&h)[0], vec!["Confirmed the designs of e1 and e2, with their gates", "Confirmed the designs of e3 and e4"]);
    }

    #[test]
    fn refs_keep_only_what_a_screen_can_link() {
        // a line names a retracted criterion or a record the document does not hold in its sentence alone; a note by what it is
        let ana = "human:ana";
        let rows = vec![
            line_at(0, ana, "", "cli", "retract", "criterion", "c4", crate::obj! {"why" => "a wrong rule"}),
            line_at(10, ana, "", "cli", "record", "run", "runs1", crate::obj! {"experiment" => "e1"}),
            line_at(20, ana, "", "cli", "adopt", "note", "n1", crate::obj! {}),
        ];
        let mut h = told(&rows, &Facts::default());
        assert_eq!(h.sittings[0].lines.iter().map(|l| l.refs.clone()).collect::<Vec<_>>(), vec![vec!["c4"], vec!["runs1", "e1"], vec!["n1"]]);
        h.retain_refs(|r| r == "runs1");
        assert_eq!(h.sittings[0].lines.iter().map(|l| l.refs.clone()).collect::<Vec<_>>(), vec![Vec::<String>::new(), vec!["runs1".into()], Vec::new()]);
        assert_eq!(sentences(&h)[0], vec!["Retracted rule c4", "Recorded a run on e1 (runs1)", "Kept a note as it reads now"]);
    }

    #[test]
    fn each_write_has_its_own_sentence() {
        // the log's rows say each write alone in History's words: a session's proposal as one, never as a call made; the
        // retraction rb writes for a replaced run as itself, though its sitting's line tells it with the new run
        let (ana, cc) = ("human:ana", "agent:claude-code");
        let mut f = Facts::default();
        f.calls.insert("dprop".into(), call("c3", "accept", "one seed is enough", Standing::Proposed));
        let rows = vec![
            line_at(0, cc, "sess-a", "cli", "session", "session", "sess-a", crate::obj! {}),
            line_at(0, cc, "sess-a", "cli", "decide", "decision", "dprop", crate::obj! {"subject" => "c3", "action" => "accept"}),
            line_at(10, ana, "", "cli", "record", "run", "runs2b", crate::obj! {"experiment" => "e1", "replaces" => "runs2"}),
            line_at(10, ana, "", "cli", "retract", "run", "runs2", crate::obj! {"why" => "replaced by runs2b: ", "replaced_by" => "runs2b"}),
        ];
        let h = told(&rows, &f);
        assert_eq!(sentences(&h)[0], vec!["Proposed a call to accept c3, not made", "Recorded a run on e1, replacing runs2 (runs2b)"]);
        assert_eq!(
            (0..5).map(|i| h.sentence_at(i)).collect::<Vec<_>>(),
            vec![None, Some("Proposed a call to accept c3, not made"), Some("Recorded a run on e1, replacing runs2 (runs2b)"), Some("Retracted run runs2"), None]
        );
    }

    // ------------------------------------------------ rb's authority, through rb's own writes

    /// The lines the record's log holds.
    fn log_len(root: &std::path::Path) -> usize {
        std::fs::read_to_string(root.join(".rb").join("log.jsonl")).unwrap().lines().filter(|l| !l.trim().is_empty()).count()
    }

    /// The log's lines from `from` on, as the user's own terminal writes them: human:ana, with no session, agent or
    /// session line. rb writes as the user only where no agent's variable is set, which a test cannot promise (CI sets
    /// one), so these tests write through rb in a session and then read those lines as the user's, as rb's authority
    /// reads whose a write is (from the log line, never the record's file).
    fn as_the_user(root: &std::path::Path, from: usize) {
        let log = root.join(".rb").join("log.jsonl");
        let mut out = String::new();
        for (i, l) in std::fs::read_to_string(&log).unwrap().lines().filter(|l| !l.trim().is_empty()).enumerate() {
            if i < from {
                out.push_str(l);
                out.push('\n');
                continue;
            }
            let Ok(Value::Dict(mut d)) = crate::pyjson::loads(l) else { panic!("not a log line: {l}") };
            if crate::ledger::is_session_line(&d) {
                continue;
            }
            d.insert("actor".into(), Value::Str("human:ana".into()));
            d.insert("actor_via".into(), Value::Str("RB_ACTOR".into()));
            d.insert("via".into(), Value::Str("cli".into()));
            d.shift_remove("session");
            d.shift_remove("agent");
            out.push_str(&crate::pyjson::jsonl_line(&Value::Dict(d)));
        }
        std::fs::write(&log, out).unwrap();
        crate::ledger::forget_files();
        crate::ledger::forget_worked();
    }

    /// The file rb last wrote a record to, as its log line names it.
    fn file_of(root: &std::path::Path, id: &str) -> std::path::PathBuf {
        let text = std::fs::read_to_string(root.join(".rb").join("log.jsonl")).unwrap();
        let path = text
            .lines()
            .filter_map(|l| match crate::pyjson::loads(l) {
                Ok(Value::Dict(d)) if d.get_str("id") == Some(id) => d.get_str("path").map(str::to_string),
                _ => None,
            })
            .last()
            .unwrap_or_else(|| panic!("no file of {id}"));
        root.join(path)
    }

    /// A record a session starts, as rb writes it in an agent session.
    fn started(dir: &Scratch) -> Ledger {
        let led = Ledger::init(&dir.0, "INT8 ResNet-50 on ImageNet", Some("int8"), Some("sdk"), Some("wf-agent-x"), false).unwrap();
        led.add_question("Does INT8 keep top-1 within half a point?", Some("q1"), None).unwrap();
        led
    }

    /// The record read afresh, after its log or a file changed under it.
    fn afresh(dir: &Scratch) -> Ledger {
        Ledger::new(&dir.0, "sdk", Some("wf-agent-x".into()))
    }

    fn told_lines(h: &History) -> Vec<(String, String, String)> {
        h.sittings[0].lines.iter().map(|l| (l.sentence.clone(), l.why.clone(), l.note.clone())).collect()
    }

    #[test]
    fn a_call_edited_outside_rb_since_is_nobodys() {
        // the user's own call counts, its reason theirs; once its file is edited outside rb it is nobody's call: it no
        // longer counts, its line says so, and what the file says now is never the user's why
        let _turn = crate::sys::turns::starting();
        let dir = scratch();
        let led = started(&dir);
        let k = log_len(&dir.0);
        let d = led.decide("q1", "continue", "one more calibration size", "", None).unwrap();
        as_the_user(&dir.0, k);
        let h = of(&afresh(&dir)).unwrap();
        assert_eq!(h.sittings[0].calls, 1);
        let (sentence, why, note) = told_lines(&h).last().unwrap().clone();
        assert_eq!((sentence.as_str(), why.as_str(), note.as_str()), ("Made a call: continued q1", "one more calibration size", ""));
        let f = file_of(&dir.0, &d.id);
        let text = std::fs::read_to_string(&f).unwrap();
        assert!(text.contains("one more calibration size"), "{text}");
        std::fs::write(&f, text.replace("one more calibration size", "two more calibration sizes")).unwrap();
        crate::ledger::forget_files();
        let h = of(&afresh(&dir)).unwrap();
        assert_eq!(h.sittings[0].calls, 0, "{h:?}");
        let (sentence, why, _) = told_lines(&h).last().unwrap().clone();
        assert_eq!((sentence.as_str(), why.as_str()), ("Recorded a call on q1, edited outside rb since", ""));
        assert!(h.latest().unwrap().line.ends_with(" · 3 changes · no calls made"), "{:?}", h.latest());
    }

    #[test]
    fn a_sessions_interpretation_is_not_confirmed_and_the_users_is_theirs() {
        // a session links a criterion to its question with what meeting it would mean: its line says the interpretation
        // is not confirmed, and its reason is its note; the user's own link reads as theirs, with their why; the
        // session's reason to lock is its note
        let _turn = crate::sys::turns::starting();
        let dir = scratch();
        let led = started(&dir);
        led.add_metric("top1", "acc", "maximize", &[], "").unwrap();
        let e = NewExperiment {
            title: "int8 against fp32".into(),
            id: Some("e1".into()),
            questions: vec!["q1".into()],
            baseline: Some("fp32".into()),
            candidates: vec!["int8".into()],
            ..Default::default()
        };
        led.add_experiment(&e).unwrap();
        for (id, at_least) in [("c1", -0.005), ("c3", -0.01)] {
            let c = NewCriterion {
                statement: format!("INT8 loses at most {at_least} top-1"),
                experiment: "e1".into(),
                metric: "change.top1".into(),
                comparator: "at_least".into(),
                target: Some(at_least),
                id: Some(id.into()),
                ..Default::default()
            };
            led.add_criterion(&c).unwrap();
        }
        led.file_criterion("c1", "q1", Some("INT8 keeps top-1 within half a point"), false, "the same comparison", None).unwrap();
        let k = log_len(&dir.0);
        led.file_criterion("c3", "q1", Some("INT8 keeps top-1 within a point"), false, "a looser budget", None).unwrap();
        as_the_user(&dir.0, k);
        let led = afresh(&dir);
        led.freeze("e1", "before the runs", false).unwrap();
        let h = of(&afresh(&dir)).unwrap();
        let lines = told_lines(&h);
        let told: Vec<(&str, &str, &str)> = lines.iter().skip_while(|l| !l.0.starts_with("Added a note on what rule")).map(|(s, w, n)| (s.as_str(), w.as_str(), n.as_str())).collect();
        assert_eq!(
            told,
            vec![
                ("Added a note on what rule c1 means for q1, not confirmed", "", "the same comparison"),
                ("Added a note on what rule c3 means for q1", "a looser budget", ""),
                ("Locked e1", "", "before the runs"),
            ]
        );
    }

    #[test]
    fn the_users_confirmation_and_their_own_past_call_are_calls_made() {
        // a session proposes a call and the user confirms it: one call made, told as confirmed, its reason still the
        // session's note; the user's own past call counts too, its reason theirs
        let _turn = crate::sys::turns::starting();
        let dir = scratch();
        let led = started(&dir);
        let p = led.decide("q1", "continue", "keep going with three seeds", "", None).unwrap();
        let k = log_len(&dir.0);
        led.confirm(&p.id).unwrap();
        led.decide("q1", "park", "the notes park it", "", Some(&crate::ledger::PastGiven { when: "2026-05-07".into(), source: None })).unwrap();
        as_the_user(&dir.0, k);
        let h = of(&afresh(&dir)).unwrap();
        let lines = told_lines(&h);
        let told: Vec<(&str, &str, &str)> = lines.iter().map(|(s, w, n)| (s.as_str(), w.as_str(), n.as_str())).collect();
        assert_eq!(
            told,
            vec![
                ("Started the record: INT8 ResNet-50 on ImageNet", "", ""),
                ("Added question q1", "", ""),
                ("Proposed a call to continue q1, not made", "", "keep going with three seeds"),
                ("Confirmed a call: continued q1", "", "keep going with three seeds"),
                ("Recorded a past call: parked q1, dated 2026-05-07", "the notes park it", ""),
            ]
        );
        assert_eq!(h.sittings[0].calls, 2);
        assert!(h.latest().unwrap().line.ends_with(" · 5 changes · 2 calls made"), "{:?}", h.latest());
    }

    #[test]
    fn a_settings_change_reads_in_plain_words() {
        // rb's log words for a setting ("setting lr: 0.0003 -> 0.001; source a -> b") never show on History
        assert_eq!(setting_said("e1", "train.epochs", "setting train.epochs: new, 30"), "Set train.epochs to 30 on e1");
        assert_eq!(setting_said("e1", "seed", "setting seed: new, unknown (per-run)"), "Added seed to e1, given by each run");
        assert_eq!(setting_said("e1", "calib", "setting calib: new, unknown"), "Added calib to e1, its value not known yet");
        assert_eq!(setting_said("e1", "notes", "setting notes: new, x (optional)"), "Set notes to x on e1, optional");
        assert_eq!(setting_said("e1", "calib_images", "setting calib_images: 512 -> 1024"), "Changed calib_images on e1 from 512 to 1024");
        assert_eq!(setting_said("e1", "lr", "setting lr: 3 -> \"3\" (was int)"), "Changed lr on e1 from 3 to \"3\"");
        assert_eq!(
            setting_said("e1", "int8.precision", "setting int8.precision: source configs/quant.yaml#backend -> note: INT8 model from quantize.py"),
            "Changed int8.precision on e1: a new source"
        );
        assert_eq!(
            setting_said("e1", "seed", "setting seed: 1 -> unknown; now per-run; optional -> required; note changed; cited 1 -> 2"),
            "Changed seed on e1 from 1 to unknown, now given by each run, now required, a new note, its cited value from 1 to 2"
        );
        assert_eq!(setting_said("e1", "lr", "setting lr: unchanged"), "Set lr on e1 again, unchanged");
        assert_eq!(setting_said("e1", "lr", "setting lr: 0.001 -> 0.002; source configs/train.yaml#optim.lr -> none"), "Changed lr on e1 from 0.001 to 0.002, no source now");
        assert_eq!(setting_said("e1", "lr", "setting lr: source none -> configs/train.yaml#optim.lr"), "Changed lr on e1: a new source");
        assert_eq!(reconciled_said("+2 experiments (sweep, warm) \u{b7} 2 results quoted \u{b7} 1 next step quoted"), "Read the repository with rb reconcile and added 2 experiments (sweep, warm)");
        assert_eq!(reconciled_said("1 re-indexed (warm) \u{b7} checks: 2 passed"), "Read the repository with rb reconcile");
        // a source's label or a value that holds rb's "; " stays one part
        assert_eq!(
            setting_said("e1", "int8.precision", "setting int8.precision: source configs/quant.yaml#backend -> note: INT8 model; int8.top1 in metrics.json"),
            "Changed int8.precision on e1: a new source"
        );
        assert_eq!(setting_said("e1", "tag", "setting tag: a; b -> c; d; now per-run"), "Changed tag on e1 from a; b to c; d, now given by each run");
        // what rb cannot read part by part is said as a change of the setting; the log keeps rb's words
        assert_eq!(setting_said("e1", "lr", "setting lr: something new"), "Changed lr on e1");
        assert_eq!(setting_said("e1", "lr", "setting other: new, 1"), "Changed lr on e1");
        assert_eq!(setting_said("e1", "lr", ""), "Changed lr on e1");
        for (change, said) in [
            ("setting calib_images: 512 -> 1024", "changed calib_images from 512 to 1024"),
            ("setting cuda retracted", "retracted setting cuda"),
            ("variant fp16 retracted", "retracted variant fp16"),
            ("new variant fp16 (ablation)", "added variant fp16 (ablation)"),
            ("varies precision, calib on purpose", "declared it varies precision, calib"),
            ("new criterion c2: change.top1 ≥ -0.01", "added rule c2"),
            ("criterion c2 retracted", "retracted rule c2"),
            ("criterion c4: file under question q1", "filed rule c4 under q1"),
            ("criterion c4: unfile question q1", "took rule c4 out of q1"),
            ("criterion c4: relate reverses c7", "recorded that rule c4 reverses c7"),
            ("criterion c4: withdraw reverses c7", "withdrew that rule c4 reverses c7"),
            ("adopted the experiment as it is now", "adopted the experiment as it is now"),
            ("criterion c2 retracted; also adopts changes made without an amendment since the last record", "retracted rule c2, and kept changes made without an amendment since it was locked"),
            ("setting c: 10 -> 11; also adopts changes made without an amendment since the last record", "changed c from 10 to 11, and kept changes made without an amendment since it was locked"),
        ] {
            assert_eq!(amendment_said("e1", change), said, "{change}");
        }
    }

    #[test]
    fn a_settings_check_is_told_on_its_line() {
        // rb's check of a setting's source, written right after it, is one line with the setting: what it found there
        let ana = "human:ana";
        let set = |t: i64, name: &str, v: &str| line_at(t, ana, "", "cli", "spec_set", "experiment", "e1", crate::obj! {"setting" => name, "change" => format!("setting {name}: new, {v}")});
        let check = |t: i64, name: &str, outcome: &str| line_at(t, ana, "", "cli", "spec_verify", "experiment", "e1", crate::obj! {"setting" => name, "outcome" => outcome});
        let h = told(&[set(0, "train.epochs", "30"), check(1, "train.epochs", "verified")], &Facts::default());
        assert_eq!(sentences(&h), vec![vec!["Set train.epochs to 30 on e1, stated by its source".to_string()]]);
        assert_eq!((h.sittings[0].lines[0].n, h.writes), (2, 2), "both writes are counted");
        let h = told(&[set(0, "train.epochs", "30"), check(1, "train.epochs", "conflict")], &Facts::default());
        assert_eq!(sentences(&h), vec![vec!["Set train.epochs to 30 on e1; rb did not find it in its source".to_string()]]);
        // several settings, each checked after it, are one line that says what the checks found
        let rows = [set(0, "a", "1"), check(1, "a", "verified"), set(2, "b", "2"), check(3, "b", "verified"), set(4, "c", "3"), check(5, "c", "verified")];
        assert_eq!(sentences(&told(&rows, &Facts::default())), vec![vec!["Set 3 settings on e1, each stated by its source: a, b, c".to_string()]]);
        let rows = [set(0, "a", "1"), check(1, "a", "verified"), set(2, "b", "2"), check(3, "b", "stale"), set(4, "c", "3")];
        assert_eq!(sentences(&told(&rows, &Facts::default())), vec![vec!["Set 3 settings on e1: a, b, c; rb found 1 of them in their sources".to_string()]]);
        // a check of another setting, or of several at once, stays a line of its own
        let many = line_at(1, ana, "", "cli", "spec_verify", "experiment", "e1", crate::obj! {"results" => crate::list![crate::obj! {"setting" => "a", "ok" => true}, crate::obj! {"setting" => "b", "ok" => false}]});
        let h = told(&[set(0, "a", "1"), many], &Facts::default());
        assert_eq!(sentences(&h), vec![vec!["Set a to 1 on e1".to_string(), "Checked 2 settings on e1 against their sources: a, b".to_string()]]);
        let h = told(&[set(0, "a", "1"), check(1, "b", "verified")], &Facts::default());
        assert_eq!(sentences(&h)[0][1], "Checked b on e1 against its source");
    }

    #[test]
    fn a_question_reworded_reads_as_its_words_changed() {
        // M's request: a question's rewording is told as a retitle is, each rewording a line of its own
        let ana = "human:ana";
        let rows = [
            line_at(0, ana, "", "cli", "add", "question", "q-acc", crate::obj! {"parent" => Value::Null}),
            line_at(60, ana, "", "cli", "reword", "question", "q-acc", crate::obj! {"before" => "Does INT8 cost accuracy?", "text" => "Does INT8 cost top-1 on ImageNet?", "why" => "say the metric"}),
            line_at(120, ana, "", "cli", "reword", "question", "q-acc", crate::obj! {"before" => "Does INT8 cost top-1 on ImageNet?", "text" => "Does INT8 cost top-1?"}),
        ];
        let h = told(&rows, &Facts::default());
        let lines = told_lines(&h);
        assert_eq!(lines[0].0, "Added question q-acc");
        assert_eq!(lines[1], ("Reworded question q-acc from \"Does INT8 cost accuracy?\" to \"Does INT8 cost top-1 on ImageNet?\"".to_string(), "say the metric".to_string(), String::new()));
        assert_eq!(h.sittings[0].lines[1].refs, vec!["q-acc".to_string()]);
        assert_eq!(lines.len(), 3, "each rewording is a line of its own");
        // through rb's own writes
        let dir = scratch();
        let led = Ledger::init(&dir.0, "INT8 ResNet-50 on ImageNet", Some("int8"), Some("sdk"), Some("wf-agent-x"), false).unwrap();
        led.add_question("Does INT8 cost accuracy?", Some("q-acc"), None).unwrap();
        led.reword_question("q-acc", "Does INT8 cost top-1?", "say the metric").unwrap();
        let all: Vec<String> = of(&afresh(&dir)).unwrap().sittings.iter().flat_map(|s| s.lines.iter().map(|l| l.sentence.clone())).collect();
        assert!(all.contains(&"Reworded question q-acc from \"Does INT8 cost accuracy?\" to \"Does INT8 cost top-1?\"".to_string()), "{all:#?}");
    }

    #[test]
    fn counts_say_what_they_count_and_a_head_is_three_items() {
        let ana = "human:ana";
        let rows = [line_at(0, ana, "", "cli", "freeze", "experiment", "e1", crate::obj! {}), line_at(60, ana, "", "cli", "freeze", "experiment", "e2", crate::obj! {})];
        let h = told(&rows, &Facts::default());
        assert_eq!(h.summary(), "2 changes recorded");
        assert_eq!(h.sittings[0].head, "2026-10-06, 10:00 to 10:01 UTC · 2 changes · no calls made");
        assert_eq!(h.sittings[0].head.matches(" \u{b7} ").count(), 2, "three items, never a longer chain");
        assert_eq!(told(&rows[..1], &Facts::default()).summary(), "1 change recorded");
        // the screens' nouns: a criterion is a rule, a decision a call
        assert_eq!((noun("criterion"), noun("decision"), noun("run"), noun("a_newer_kind")), ("rule".into(), "call".into(), "run".into(), "a newer kind".into()));
        // a setting a call is on reads as the setting of its experiment, never as rb's address e1/lr
        assert_eq!((named("e1/train.epochs"), named("c1"), named("/x"), named("e1/")), ("train.epochs of e1".into(), "c1".into(), "/x".into(), "e1/".into()));
    }

    #[test]
    fn a_run_and_an_analysis_are_named_by_their_words() {
        // FIX9: no id rb minted is a sitting's text; its words are the model's (runs.{id}.named), the ids stay in refs
        let ana = "human:ana";
        let names: HashMap<String, String> = [
            ("runaaaaaa", "run fp32 and int8 · seed 1, recorded 2026-10-14 09:20 UTC"),
            ("runbbbbbb", "run fp32 and int8 · seed 1, recorded 2026-10-14 09:25 UTC"),
            ("runcccccc", "run fp32 and int8 · seed 2"),
            ("adddddd", "analysis per-image accuracy"),
            ("deeeeee", "a call to accept c1"),
        ]
        .into_iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect();
        let seed1 = "run fp32 and int8 · seed 1".to_string();
        let bases: HashMap<String, String> = [("runaaaaaa".to_string(), seed1.clone()), ("runbbbbbb".to_string(), seed1)].into_iter().collect();
        let f = Facts { names, bases, ..Default::default() };
        let rec = |t: i64, id: &str, d: Value| line_at(t, ana, "", "cli", "record", "run", id, d);
        let h = told(&[rec(0, "runaaaaaa", crate::obj! {"experiment" => "e1"}), rec(10, "runcccccc", crate::obj! {"experiment" => "e1"})], &f);
        assert_eq!(sentences(&h)[0], vec!["Recorded 2 runs: run fp32 and int8 · seed 1 at 2026-10-14 09:20 UTC, run fp32 and int8 · seed 2"]);
        assert_eq!(h.sittings[0].lines[0].refs, vec!["runaaaaaa", "e1", "runcccccc"]);
        let rows = [
            rec(0, "runbbbbbb", crate::obj! {"experiment" => "e1", "replaces" => "runaaaaaa"}),
            line_at(1, ana, "", "cli", "retract", "run", "runaaaaaa", crate::obj! {"replaced_by" => "runbbbbbb"}),
        ];
        let h = told(&rows, &f);
        assert_eq!(
            sentences(&h)[0],
            vec!["Recorded a run on e1: run fp32 and int8 · seed 1 at 2026-10-14 09:25 UTC, replacing run fp32 and int8 · seed 1, recorded 2026-10-14 09:20 UTC"]
        );
        assert_eq!(h.sittings[0].lines[0].refs, vec!["runbbbbbb", "runaaaaaa", "e1"]);
        let retract = |t: i64, kind: &str, id: &str, d: Value| line_at(t, ana, "", "cli", "retract", kind, id, d);
        assert_eq!(sentences(&told(&[retract(0, "run", "runcccccc", crate::obj! {})], &f))[0], vec!["Retracted run fp32 and int8 · seed 2"]);
        let two = [retract(0, "run", "runcccccc", crate::obj! {}), retract(1, "run", "runaaaaaa", crate::obj! {})];
        assert_eq!(sentences(&told(&two, &f))[0], vec!["Retracted 2 runs: run fp32 and int8 · seed 2; run fp32 and int8 · seed 1, recorded 2026-10-14 09:20 UTC"]);
        let scoped = retract(0, "run", "runcccccc", crate::obj! {"scope" => crate::list!["wall_s"]});
        assert_eq!(sentences(&told(&[scoped], &f))[0], vec!["Retracted wall_s of run fp32 and int8 · seed 2"]);
        assert_eq!(sentences(&told(&[retract(0, "decision", "deeeeee", crate::obj! {}), retract(1, "decision", "dffffff", crate::obj! {})], &f))[0], vec!["Retracted 2 calls: a call to accept c1, dffffff"]);
        let an = |t: i64, id: &str, d: Value| line_at(t, ana, "", "cli", "analyze", "analysis", id, d);
        assert_eq!(sentences(&told(&[an(0, "adddddd", crate::obj! {"experiment" => "e1"})], &f))[0], vec!["Recorded an analysis on e1: analysis per-image accuracy"]);
        assert_eq!(sentences(&told(&[an(0, "a2", crate::obj! {"experiment" => "e1", "supersedes" => crate::list!["adddddd"]})], &f))[0], vec!["Recorded an analysis on e1, replacing analysis per-image accuracy (a2)"]);
        for (op, said) in [("adopt", "Kept run fp32 and int8 · seed 2 as it reads now"), ("restore", "Restored run fp32 and int8 · seed 2"), ("clone", "Copied run fp32 and int8 · seed 2 from the clone"), ("future", "Changed run fp32 and int8 · seed 2")] {
            assert_eq!(sentences(&told(&[line_at(0, ana, "", "cli", op, "run", "runcccccc", crate::obj! {})], &f))[0], vec![said]);
        }
        // an analysis with no title is said by its experiment and when it was recorded (M's words), "recorded" once where
        // History's own "Recorded" opens the line; a proposal taken back stays a proposal
        let f2 = Facts {
            names: [("aggggggg", "an analysis on e1, recorded 2026-10-14 09:30 UTC"), ("ahhhhhh", "an analysis on e1, recorded 2026-10-14 09:31 UTC"), ("dhhhhhh", "a proposed call to park e1, not made")]
                .into_iter()
                .map(|(a, b)| (a.to_string(), b.to_string()))
                .chain(f.names.clone())
                .collect(),
            bases: [("aggggggg", "an analysis on e1"), ("ahhhhhh", "an analysis on e1")].into_iter().map(|(a, b)| (a.to_string(), b.to_string())).chain(f.bases.clone()).collect(),
            ..Default::default()
        };
        assert_eq!(sentences(&told(&[an(0, "aggggggg", crate::obj! {"experiment" => "e1"})], &f2))[0], vec!["Recorded an analysis on e1 at 2026-10-14 09:30 UTC"]);
        let calls = [retract(0, "decision", "dhhhhhh", crate::obj! {}), retract(1, "decision", "deeeeee", crate::obj! {})];
        assert_eq!(sentences(&told(&calls, &f2))[0], vec!["Retracted 2 calls: a proposed call to park e1, not made; a call to accept c1"]);
        assert_eq!(sentences(&told(&[retract(0, "analysis", "aggggggg", crate::obj! {})], &f2))[0], vec!["Retracted an analysis on e1, recorded 2026-10-14 09:30 UTC"]);
        // analyses with no title are counted, never listed one by one
        let three = [an(0, "aggggggg", crate::obj! {"experiment" => "e1"}), an(1, "ahhhhhh", crate::obj! {"experiment" => "e1"}), an(2, "adddddd", crate::obj! {"experiment" => "e1"})];
        assert_eq!(sentences(&told(&three, &f2))[0], vec!["Recorded 3 analyses on e1: analysis per-image accuracy, and 2 with no title"]);
        assert_eq!(sentences(&told(&three[..2], &f2))[0], vec!["Recorded 2 analyses on e1, with no title"]);
        let anew = an(0, "adddddd", crate::obj! {"experiment" => "e1", "supersedes" => crate::list!["aggggggg"]});
        assert_eq!(sentences(&told(&[anew], &f2))[0], vec!["Recorded an analysis on e1: analysis per-image accuracy, replacing an analysis on e1, recorded 2026-10-14 09:30 UTC"]);
        // a run told apart by when it was recorded says "recorded" once (MP-1028-0216): History's own "Recorded" opens it
        let f3 = Facts {
            names: [("runaaaaaa".to_string(), "a run of e1, recorded 2026-10-14 09:20:05 UTC, the 2nd of 2".to_string())].into_iter().collect(),
            bases: [("runaaaaaa".to_string(), "a run of e1".to_string())].into_iter().collect(),
            ..Default::default()
        };
        assert_eq!(sentences(&told(&[rec(0, "runaaaaaa", crate::obj! {"experiment" => "e1"})], &f3))[0], vec!["Recorded a run of e1 at 2026-10-14 09:20:05 UTC, the 2nd of 2"]);
        assert_eq!(as_recorded("run a, recorded 2026-10-14 09:20 UTC", "run a"), "run a at 2026-10-14 09:20 UTC");
        assert_eq!(as_recorded("run a · seed 1", "run a · seed 1"), "run a · seed 1");
        // words of the label itself are the run's own (review): only the time rb adds after the words it told apart is
        // said with "at", even where the label reads like rb's time (Codex, 061b)
        assert_eq!(as_recorded("run a · tag alpha, recorded beta", ""), "run a · tag alpha, recorded beta");
        assert_eq!(as_recorded("run a · tag alpha, recorded 2026-10-14 09:20 UTC", ""), "run a · tag alpha, recorded 2026-10-14 09:20 UTC");
        assert_eq!(
            as_recorded("run a · tag alpha, recorded beta, recorded 2026-10-14 09:20:05 UTC, the 2nd of 2", "run a · tag alpha, recorded beta"),
            "run a · tag alpha, recorded beta at 2026-10-14 09:20:05 UTC, the 2nd of 2"
        );
        let own = Facts { names: [("runaaaaaa".to_string(), "run fp32 and int8 · tag alpha, recorded 2026-10-14 09:20 UTC".to_string())].into_iter().collect(), ..Default::default() };
        assert_eq!(sentences(&told(&[rec(0, "runaaaaaa", crate::obj! {"experiment" => "e1"})], &own))[0], vec!["Recorded a run on e1: run fp32 and int8 · tag alpha, recorded 2026-10-14 09:20 UTC"]);
        // a run with no label is a run of its experiment, said once
        let f = Facts { names: [("runaaaaaa".to_string(), "a run of e1".to_string())].into_iter().collect(), ..Default::default() };
        assert_eq!(sentences(&told(&[rec(0, "runaaaaaa", crate::obj! {"experiment" => "e1"})], &f))[0], vec!["Recorded a run of e1"]);
    }

    #[test]
    fn twin_runs_are_told_apart_as_the_model_tells_them() {
        let said: IndexMap<String, String> = ["r1", "r2", "r3", "r4"].iter().map(|k| (k.to_string(), if *k == "r4" { "run b".to_string() } else { "run a".to_string() })).collect();
        let times: IndexMap<String, String> = [("r1", "2026-10-14T09:20:05+00:00"), ("r2", "2026-10-14T09:20:05+00:00"), ("r3", "2026-10-14T09:20:40+00:00"), ("r4", "2026-10-14T09:20:05+00:00")]
            .iter()
            .map(|(a, b)| (a.to_string(), b.to_string()))
            .collect();
        let n = twins_told_apart(&said, &times);
        assert_eq!(n["r1"], "run a, recorded 2026-10-14 09:20:05 UTC, the 1st of 2");
        assert_eq!(n["r2"], "run a, recorded 2026-10-14 09:20:05 UTC, the 2nd of 2");
        assert_eq!(n["r3"], "run a, recorded 2026-10-14 09:20:40 UTC");
        assert_eq!(n["r4"], "run b");
        let apart: IndexMap<String, String> = [("r1", "2026-10-14T09:20:05+00:00"), ("r2", "2026-10-14T09:25:00+00:00")].iter().map(|(a, b)| (a.to_string(), b.to_string())).collect();
        let n = twins_told_apart(&said.iter().take(2).map(|(a, b)| (a.clone(), b.clone())).collect(), &apart);
        assert_eq!((n["r1"].as_str(), n["r2"].as_str()), ("run a, recorded 2026-10-14 09:20 UTC", "run a, recorded 2026-10-14 09:25 UTC"));
        assert_eq!(ordinal(11), "11th");
        assert_eq!(ordinal(22), "22nd");
    }

    #[test]
    fn grouped_lines_count_and_name_what_they_hold() {
        // review of design pass 1: two checks of two settings each are four settings; settings and variants taken back
        // read as "bar of e1"; rules are rules when counted; an exposure says what the data was used for
        let ana = "human:ana";
        let verify = |t: i64, a: &str, b: &str| line_at(t, ana, "", "cli", "spec_verify", "experiment", "e1", crate::obj! {"results" => crate::list![crate::obj! {"setting" => a, "ok" => true}, crate::obj! {"setting" => b, "ok" => true}]});
        assert_eq!(sentences(&told(&[verify(0, "a", "b"), verify(1, "c", "d")], &Facts::default())), vec![vec!["Checked 4 settings on e1 against their sources: a, b, c, d".to_string()]]);
        let retract = |t: i64, kind: &str, id: &str, d: Value| line_at(t, ana, "", "cli", "retract", kind, id, d);
        let rows = [retract(0, "experiment", "e1", crate::obj! {"setting" => "bar"}), retract(1, "experiment", "e1", crate::obj! {"setting" => "baz"})];
        assert_eq!(sentences(&told(&rows, &Facts::default())), vec![vec!["Retracted 2 settings of e1: bar, baz".to_string()]]);
        assert_eq!(sentences(&told(&rows[..1], &Facts::default())), vec![vec!["Retracted setting bar of e1".to_string()]]);
        let rows = [retract(0, "experiment", "e1", crate::obj! {"variant" => "fp16"}), retract(1, "experiment", "e2", crate::obj! {"variant" => "fp16"})];
        assert_eq!(sentences(&told(&rows, &Facts::default())), vec![vec!["Retracted 2 variants: fp16 of e1, fp16 of e2".to_string()]]);
        assert_eq!(sentences(&told(&rows[..1], &Facts::default())), vec![vec!["Retracted variant fp16 of e1".to_string()]]);
        let rows = [retract(0, "criterion", "c1", crate::obj! {}), retract(1, "criterion", "c2", crate::obj! {})];
        assert_eq!(sentences(&told(&rows, &Facts::default())), vec![vec!["Retracted 2 rules: c1, c2".to_string()]]);
        for (how, said) in [("trained", "Recorded that ds was trained on"), ("selected", "Recorded that ds was used for selection"), ("evaluated", "Recorded that ds was evaluated on"), ("inspected", "Recorded that ds was inspected")] {
            let h = told(&[line_at(0, ana, "", "cli", "expose", "resource", "ds", crate::obj! {"how" => how})], &Facts::default());
            assert_eq!(h.sittings[0].lines[0].sentence, said);
        }
    }

    /// The contract examples' log as rb writes it (tests/fixtures/shell/int8-demo.*.view.json): each write's time from the
    /// example's own history.log, its runs' labels and times, its experiments' titles and its calls from the example's
    /// records, and what rb's log line holds beyond what the example shows (an op, a detail, who wrote it) from this
    /// table. History told from it is what the examples must hold, so they cannot drift from what rb writes. One row
    /// stands for each write the example's log lists: an amendment that adds a rule is its criterion's row, where rb also
    /// writes the experiment's, since the example's log counts it once. Facts are made here, not read from a ledger by
    /// Facts::of and named_apart, which the tests over a real record above cover.
    fn example_history(v: &Value) -> History {
        let (ana, helper) = ("human:ana", "agent:helper");
        let amend = |change: &str, why: &str| crate::obj! {"change" => change, "why" => why};
        let table: Vec<(&str, &str, &str, &str, Value)> = vec![
            (ana, "init", "project", "int8-resnet50", crate::obj! {"title" => "INT8 ResNet-50 on ImageNet"}),
            (ana, "add", "question", "q1", crate::obj! {"parent" => Value::Null}),
            (ana, "add", "experiment", "e1", crate::obj! {"questions" => crate::list!["q1"]}),
            (ana, "spec_set", "experiment", "e1", crate::obj! {"setting" => "seed", "change" => "setting seed: new, unknown (per-run)"}),
            (ana, "add", "criterion", "c1", crate::obj! {"experiment" => "e1"}),
            (ana, "add", "criterion", "c3", crate::obj! {"experiment" => "e1"}),
            (ana, "add", "criterion", "c4", crate::obj! {"experiment" => "e1"}),
            (ana, "file", "criterion", "c1", crate::obj! {"question" => "q1"}),
            (ana, "file", "criterion", "c3", crate::obj! {"question" => "q1"}),
            (ana, "file", "criterion", "c4", crate::obj! {"question" => "q1"}),
            (ana, "add", "resource", "imagenet-val", crate::obj! {}),
            (ana, "add", "resource", "imagenet-calib-512", crate::obj! {}),
            (ana, "add", "resource", "resnet50-fp32", crate::obj! {}),
            (ana, "freeze", "experiment", "e1", crate::obj! {}),
            (ana, "gate", "experiment", "e1", crate::obj! {"confirms" => "design", "design" => "94f795b74f5c"}),
            (ana, "record", "run", "runs1", crate::obj! {"experiment" => "e1"}),
            (ana, "record", "run", "runs2", crate::obj! {"experiment" => "e1"}),
            (ana, "record", "run", "runsql7", crate::obj! {"experiment" => "e1"}),
            (ana, "add", "criterion", "c6", crate::obj! {"experiment" => "e1", "amendment" => amend("new criterion c6: onnx_top1", "")}),
            (ana, "retract", "run", "runs2", crate::obj! {"why" => "evaluated on the train split"}),
            (ana, "record", "run", "runs2b", crate::obj! {"experiment" => "e1", "replaces" => "runs2"}),
            (ana, "add", "question", "q1a", crate::obj! {"parent" => "q1"}),
            (ana, "add", "experiment", "e2", crate::obj! {"questions" => crate::list![]}),
            (ana, "spec_set", "experiment", "e2", crate::obj! {"setting" => "calib_images", "change" => "setting calib_images: new, unknown (per-run)"}),
            (ana, "add", "criterion", "c5", crate::obj! {"experiment" => "e2"}),
            (ana, "gate", "experiment", "e2", crate::obj! {"if_holds" => "ship int8 calibrated on 512 images", "if_not" => "keep fp32 for serving"}),
            (ana, "file", "experiment", "e2", crate::obj! {"question" => "q1a"}),
            (ana, "file", "criterion", "c5", crate::obj! {"question" => "q1a"}),
            (ana, "freeze", "experiment", "e2", crate::obj! {}),
            (ana, "record", "run", "runc128", crate::obj! {"experiment" => "e2"}),
            (ana, "record", "run", "runc512", crate::obj! {"experiment" => "e2"}),
            (ana, "record", "run", "runc1024", crate::obj! {"experiment" => "e2"}),
            (ana, "record", "run", "runc2048", crate::obj! {"experiment" => "e2"}),
            (ana, "record", "run", "runs3", crate::obj! {"experiment" => "e1"}),
            (ana, "analyze", "analysis", "a1", crate::obj! {"experiment" => "e1"}),
            (ana, "add", "criterion", "c2", crate::obj! {"experiment" => "e1", "amendment" => amend("new criterion c2: fp32.top1", "")}),
            (ana, "file", "criterion", "c2", crate::obj! {"question" => "q1"}),
            (ana, "decide", "decision", "dec1", crate::obj! {"subject" => "c1"}),
            (helper, "decide", "decision", "dec5", crate::obj! {"subject" => "c4"}),
            (ana, "decide", "decision", "dec6", crate::obj! {"subject" => "c4", "confirms" => "dec5"}),
            (ana, "file", "criterion", "c4", crate::obj! {"question" => "q1", "when_met" => "int8 serves within the accuracy budget", "why" => "c1 and c4 hold on three seeds"}),
            (ana, "note", "experiment", "e1", crate::obj! {"note" => "n1"}),
            (ana, "amend", "experiment", "e1", crate::obj! {"amendment" => amend("hypothesis", "")}),
            (ana, "amend", "experiment", "e2", crate::obj! {"amendment" => amend("unit run -> calib_images", "the cells are the unit")}),
            (helper, "file", "criterion", "c5", crate::obj! {"question" => "q1a", "when_met" => "512 images are enough", "why" => "only the 128-image cell misses"}),
            (helper, "decide", "decision", "dec7", crate::obj! {"subject" => "c3"}),
            (helper, "decide", "decision", "dec4", crate::obj! {"subject" => "int8"}),
        ];
        let log = v.get("history").unwrap().get("log").unwrap().as_list().unwrap();
        assert_eq!(log.len(), table.len(), "the table holds one line for each write of the example's log");
        let at = |i: usize| {
            let row = log.iter().find(|r| r.get("log_index") == Some(&Value::Int(i as i64))).unwrap_or_else(|| panic!("no log_index {i}"));
            let m = row.get_str("at").unwrap().strip_suffix(" UTC").unwrap().replace(' ', "T");
            format!("{m}:00.000+00:00")
        };
        let rows: Vec<Dict> = table.into_iter().enumerate()
            .map(|(i, (actor, op, kind, id, detail))| crate::dict! {"at" => at(i), "actor" => actor, "via" => "cli", "op" => op, "kind" => kind, "id" => id, "detail" => detail})
            .collect();
        let mut f = Facts { project: v.get("project").unwrap().get_str("title").unwrap().to_string(), ..Default::default() };
        for e in v.get("experiments").unwrap().as_list().unwrap() {
            f.titles.insert(e.get_str("id").unwrap().to_string(), e.get_str("title").unwrap().to_string());
        }
        // each run as names_of says it: "run <its label>", told apart from a run whose words read the same by when
        let runs = v.get("runs").unwrap().as_dict().unwrap();
        let mut said: IndexMap<String, String> = IndexMap::new();
        let mut times: IndexMap<String, String> = IndexMap::new();
        for row in rows.iter().filter(|r| r.get_str("op") == Some("record")) {
            let id = row.get_str("id").unwrap();
            let r = &runs[id];
            said.insert(id.to_string(), format!("run {}", r.get_str("label").unwrap()));
            times.insert(id.to_string(), r.get_str("at").unwrap().replace(" UTC", ":00+00:00").replace(' ', "T"));
            let made: Vec<String> = r.get("made").and_then(Value::as_list).unwrap().iter().map(crate::pyjson::py_str).collect();
            let row = if id == "runsql7" { "row 7 of table runs".to_string() } else { String::new() };
            f.runs.insert(id.to_string(), RunFacts { experiment: r.get_str("experiment_id").unwrap().to_string(), made, row });
        }
        f.names = twins_told_apart(&said, &times);
        for (id, r) in runs {
            assert_eq!(f.names.get(id).map(String::as_str), r.get_str("named"), "runs.{id}.named is what twins_told_apart says of its label");
        }
        f.bases = said.iter().filter(|(id, w)| f.names.get(*id) != Some(*w)).map(|(id, w)| (id.clone(), w.clone())).collect();
        for c in ["c1", "c2", "c3", "c4", "c6"] {
            f.experiment_of.insert(c.into(), "e1".into());
        }
        f.experiment_of.insert("c5".into(), "e2".into());
        // the calls: c1 made, a proposal on c4 the user confirmed, a proposal on c3, and a past call a session mentioned
        let call = |subject: &str, action: &str, why: &str, standing: Standing| Call { subject: subject.into(), action: action.into(), why: why.into(), standing };
        f.calls.insert("dec1".into(), call("c1", "accept", "holds on all three seeds", Standing::Made));
        f.calls.insert("dec5".into(), call("c4", "accept", "per-class drop stays under 2 points", Standing::Proposed));
        f.calls.insert("dec6".into(), call("c4", "accept", "per-class drop stays under 2 points", Standing::Confirmed));
        let pending = v.get("pending").unwrap().get("rows").unwrap().as_list().unwrap();
        assert!(pending.iter().any(|p| p.get_str("id") == Some("dec7")) && pending.iter().any(|p| p.get_str("id") == Some("dec4")));
        f.calls.insert("dec7".into(), call("c3", "accept", "one seed is enough for the export check\\u200b", Standing::Proposed));
        f.calls.insert("dec4".into(), call("int8", "park", "", Standing::PastMentioned("2026-05-07".into(), "notes.md:3".into())));
        let mut h = told(&rows, &f);
        // a line links only what the document holds, as the shell's model keeps them (model.rs held_ids)
        let mut held: std::collections::HashSet<String> = std::collections::HashSet::new();
        for k in ["questions", "experiments"] {
            held.extend(v.get(k).unwrap().as_list().unwrap().iter().filter_map(|x| x.get_str("id")).map(str::to_string));
        }
        for k in ["findings", "runs", "resources", "analyses"] {
            held.extend(v.get(k).unwrap().as_dict().unwrap().keys().cloned());
        }
        h.retain_refs(|r| held.contains(r));
        h
    }

    #[test]
    fn the_contract_examples_say_history_as_history_tells_it() {
        // the orchestrator's contract test (Oct 29 15:40 +08 item 2): every example's summary, sittings and Latest
        // changes are what this file tells of the examples' log, word for word
        let live = crate::pyjson::loads(include_str!("../../tests/fixtures/shell/int8-demo.live.view.json")).unwrap();
        let h = example_history(&live);
        let want = crate::obj! {"history" => h.to_value(), "latest" => h.latest().map_or(Value::Null, |l| l.to_value())};
        if let Ok(out) = std::env::var("RB_EXAMPLE_HISTORY_OUT") {
            std::fs::write(out, crate::pyjson::dumps(&want)).unwrap();
        }
        for (name, text) in [
            ("live", include_str!("../../tests/fixtures/shell/int8-demo.live.view.json")),
            ("owner", include_str!("../../tests/fixtures/shell/int8-demo.owner.view.json")),
            ("away", include_str!("../../tests/fixtures/shell/int8-demo.away.view.json")),
            ("past", include_str!("../../tests/fixtures/shell/int8-demo.past.view.json")),
        ] {
            let v = crate::pyjson::loads(text).unwrap();
            let held = crate::obj! {"history" => crate::obj! {"summary" => v.get("history").unwrap().get("summary").unwrap().clone(), "sittings" => v.get("history").unwrap().get("sittings").unwrap().clone()}, "latest" => v.get("home").unwrap().get("latest").unwrap().clone()};
            let (got, want) = (crate::pyjson::dumps(&held), crate::pyjson::dumps(&want));
            assert!(got == want, "int8-demo.{name}.view.json's History is not what history.rs tells of its log:\n held: {got}\n told: {want}");
        }
    }
}
