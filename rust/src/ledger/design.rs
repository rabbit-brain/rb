//! An experiment's design: the questions it is filed under, its hypothesis or purpose, its why, its variants and their
//! roles, what it varies, its settings with their sources, its criteria (rule and role) and how each bears on a
//! question, its gate and the words given with it, its freeze and amendments. Its runs, analyses, notes, relations and
//! the calls on it are not its design.
//!
//! Its standing is read from the log alone, by a snapshot rule. A design no session's line wrote any part of carries no
//! mark. Otherwise it reads confirmed only while it is exactly a design the user owns: one they confirmed (the
//! confirmation's line keeps it), or one their own write made from a design they owned at that moment (that write's
//! line keeps it, `detail.owned`, worked out when it was written and never later). Owning is a set test over those
//! lines, so a merge, a clone, a rebase or a clock needs no order and no copy of what rb wrote: each can only make rb
//! ask again. A design a session touched waits for one confirmation, whatever is written after it. A confirmation
//! changes no reading.
//!
//! A design's key is a hash over rb's structured form of its parts (ids, values, sources, roles, rules, words), never
//! over how rb says them, so a later rb that words a part differently reopens nothing. A confirmation the first builds
//! wrote kept the parts as rb said them, which a session's change can leave as they were: it is a design the user owns,
//! read in that form, only while no session's line it did not see wrote a part of it and rb wrote every file of it as
//! it stands; one that names no line it saw is taken to have seen none, and no change since it is said. A line an
//! earlier rb wrote, which compared parts as rb said them, says nothing rb can rest on of what it changed: it wrote a
//! part where it wrote a file of the design.

use super::*;

/// What a criterion's link to a question says it would mean when met, where no `--when-met` gave it.
const NO_INTERPRETATION: &str = "no interpretation";

/// One part of a design, as it reads now: a key that names it, its value as rb says it (as the first builds kept a
/// confirmation), and rb's structured form of it, which the design's key is taken over.
#[derive(Debug, Clone, PartialEq)]
pub struct Part {
    pub key: String,
    pub value: String,
    pub raw: Value,
}

impl Part {
    pub fn label(&self) -> String {
        label_of(&self.key)
    }
}

/// A setting's name as rb's text says it: a variant's own (`<variant>/<name>` in a part's key, so it never shares a
/// key with a shared setting of the same dotted name) as `<variant>.<name>`.
fn addressed(name: &str) -> String {
    name.replacen('/', ".", 1)
}

/// A part's name as rb's text says it.
pub fn label_of(key: &str) -> String {
    let (kind, rest) = key.split_once(':').unwrap_or((key, ""));
    match kind {
        "question" => format!("question {rest}"),
        "varies" if !rest.is_empty() => format!("varies {rest}"),
        // the parts said together: what it varies, and the gate's two branches
        "varies" => "what it varies".into(),
        "gate" if rest.is_empty() => "the gate".into(),
        "optional" => format!("setting {} as optional", addressed(rest)),
        "factor" => format!("setting {} as a fixed design factor", addressed(rest)),
        "cited" => format!("setting {}'s cited value", addressed(rest)),
        "gate" if rest == "if_holds" => "the gate if it holds".into(),
        "gate" if rest == "if_not" => "the gate if not".into(),
        "variant" => format!("variant {rest}"),
        "setting" => format!("setting {}", addressed(rest)),
        "source" => format!("setting {}'s source", addressed(rest)),
        "rule" => format!("criterion {rest}'s rule"),
        "role" => format!("criterion {rest}'s role"),
        "link" => {
            let (c, q) = rest.split_once(':').unwrap_or((rest, ""));
            format!("what meeting {c} would mean for {q}")
        }
        "gate_words" => "the gate's words (as given)".into(),
        "freeze" => "the freeze".into(),
        "freeze_why" => "the freeze's reason".into(),
        "amendment" => format!("amendment {rest}"),
        "amendment_why" => format!("amendment {rest}'s reason"),
        // a file of it, where no part of it can be named: written since, or edited outside rb
        "written" | "rewritten" | "edited" => rest.to_string(),
        other => other.to_string(),
    }
}

/// A part's value as rb's line says it: words someone gave are quoted and escaped (a line break as `\n`), and so is a
/// value with a character a screen would hide; a value that holds a comma or `·` is quoted, so where one ends and the
/// next begins is plain.
fn said_value(key: &str, v: &str) -> String {
    // a link's interpretation is quoted and escaped, as a link's words are listed; told apart, as its part holds it
    if let Some(rest) = v.strip_prefix("when met, ").filter(|_| key.starts_with("link:")) {
        return met_words(key, v).map_or_else(|| visible(rest), quoted);
    }
    let worded = matches!(key.split(':').next().unwrap_or(""), "hypothesis" | "purpose" | "why" | "gate_words" | "freeze_why" | "amendment_why");
    if worded || v.chars().any(hidden) {
        quoted(v)
    } else if v.contains(',') || v.contains('·') {
        format!("\"{v}\"")
    } else {
        v.to_string()
    }
}

/// What a link's part says meeting its criterion would mean, as the words given (`when met, "<words>"`).
fn met_words<'v>(key: &str, v: &'v str) -> Option<&'v str> {
    v.strip_prefix("when met, \"").and_then(|w| w.strip_suffix('"')).filter(|_| key.starts_with("link:"))
}

/// Whether a was and a now would draw alike on a screen: the same but for spaces at their ends, or a space of another
/// kind (a no-break space).
fn drawn_alike(was: &str, now: &str) -> bool {
    let drawn = |t: &str| t.chars().map(|c| if c.is_whitespace() { ' ' } else { c }).collect::<String>().trim().to_string();
    drawn(was) == drawn(now)
}

/// A value quoted and escaped, with every space but a plain one as its escape: how a was and a now that would draw
/// alike are told apart.
fn told(key: &str, v: &str) -> String {
    quoted(met_words(key, v).unwrap_or(v)).chars().map(|c| if c.is_whitespace() && c != ' ' { format!("\\u{:04x}", c as u32) } else { c.to_string() }).collect()
}

/// How a design differs from the one the user owns closest to it: a part changed, added or removed, with what it read
/// there and what it reads now, where the record keeps both.
#[derive(Debug, Clone, PartialEq)]
pub struct Change {
    pub key: String,
    pub change: &'static str,
    pub was: Option<String>,
    pub now: Option<String>,
}

impl Change {
    fn label(&self) -> String {
        label_of(&self.key)
    }

    /// Whether it is a change rb can name and not say: a part a session's line says it changed that reads as before in
    /// rb's words, against a design kept only in them.
    fn bare(&self) -> bool {
        self.change == "changed" && self.was.is_none() && self.now.is_none()
    }

    /// One line, rb's words over the record's: `changed since you confirmed it: <what>, was <old>, now <new>`.
    pub fn said(&self) -> String {
        let label = self.label();
        let v = |x: &str| said_value(&self.key, x);
        let (kind, rest) = self.key.split_once(':').unwrap_or((self.key.as_str(), ""));
        let what = match (kind, self.change, self.was.as_deref(), self.now.as_deref()) {
            // where no part of it is said, or another is, which may be one it holds
            ("written" | "rewritten", ..) => format!("{rest} was written again"),
            ("edited", ..) => format!("{rest} was edited outside rb"),
            // filing a criterion under a question, or taking it out, is said as that
            ("link", "added" | "removed", _, now) => {
                let (c, q) = rest.split_once(':').unwrap_or((rest, ""));
                match (self.change, now) {
                    ("added", Some(n)) if n != NO_INTERPRETATION => format!("criterion {c} filed under {q}, when met {}", v(n)),
                    ("added", _) => format!("criterion {c} filed under {q}"),
                    _ => format!("criterion {c} no longer filed under {q}"),
                }
            }
            ("variant", "added", _, Some(n)) => format!("{label} added as {n}"),
            ("variant", "removed", Some(w), _) => format!("{label} ({w}) removed"),
            ("freeze", "added", _, Some(n)) => n.to_string(),
            ("amendment", "added", _, Some(n)) => format!("amended: {}", visible(n)),
            (_, "added", _, Some(n)) => format!("{label} added: {}", v(n)),
            (_, "removed", Some(w), _) => format!("{label} removed, was {}", v(w)),
            // a was and a now a screen would draw alike are each quoted and escaped in full
            (_, "changed", Some(w), Some(n)) if drawn_alike(&v(w), &v(n)) => format!("{label}, was {}, now {}", told(&self.key, w), told(&self.key, n)),
            (_, "changed", Some(w), Some(n)) => format!("{label}, was {}, now {}", v(w), v(n)),
            (_, "added" | "removed", _, _) => format!("{label} {}", self.change),
            _ => label,
        };
        visible(&format!("{}: {what}", since()))
    }

    pub fn to_value(&self) -> Value {
        crate::obj! {"part" => self.label(), "key" => self.key.clone(), "change" => self.change, "was" => self.was.clone(), "now" => self.now.clone()}
    }
}

/// How a change since the confirmation is said: to the user, who confirmed it; on a page anyone may be sent, without
/// naming who did.
fn since() -> &'static str {
    if crate::view::reader() == crate::view::Reader::Page { "changed since it was confirmed" } else { "changed since you confirmed it" }
}

/// The part a design's lines and its data say an element under: every question it is filed under together
/// (`questions`), what it varies (`varies`), its gate's two branches (`gate`), and a setting's value with its marks
/// (`setting:<name>`); every other part is said as itself.
fn group_of(key: &str) -> Option<String> {
    let (kind, rest) = key.split_once(':')?;
    match kind {
        "question" => Some("questions".into()),
        "varies" => Some("varies".into()),
        "gate" => Some("gate".into()),
        "setting" | "optional" | "factor" | "cited" => Some(format!("setting:{rest}")),
        _ => None,
    }
}

/// What a part said together reads as, from its elements (`keys`, read by `get`): none where none of them is there. A
/// gate's branches are each `listed` as rb confirm lists them where a change is said, else as written.
fn group_value(group: &str, keys: &[String], get: &dyn Fn(&str) -> Option<String>, listed: bool) -> Option<String> {
    match group {
        "questions" | "varies" => {
            let all: Vec<String> = keys.iter().filter_map(|k| get(k)).collect();
            (!all.is_empty()).then(|| all.join(", "))
        }
        "gate" => {
            // a change's was and now list each branch, so one that holds `· if not:` or a line break never reads as another
            let branch = |k: &str| get(k).map(|b| if listed { listed_text(&b) } else { b }).unwrap_or_default();
            let line = crate::view::gate_line(&branch("gate:if_holds"), &branch("gate:if_not"));
            (!line.is_empty()).then_some(line)
        }
        _ => {
            let name = group.strip_prefix("setting:").unwrap_or("");
            let mut v = get(&format!("setting:{name}"))?;
            if get(&format!("optional:{name}")).is_some() {
                v.push_str(" (optional)");
            }
            if get(&format!("factor:{name}")).is_some() {
                v.push_str(" (a fixed design factor)");
            }
            if let Some(c) = get(&format!("cited:{name}")) {
                v.push_str(&format!(" (the cited source used {c})"));
            }
            Some(v)
        }
    }
}

/// The parts as a design's data says them (`design.parts`): the elements of one part together, where its first one is,
/// as written.
fn grouped_parts(parts: &[Part]) -> Vec<Part> {
    let get = |k: &str| parts.iter().find(|p| p.key == k).map(|p| p.value.clone());
    let mut out: Vec<Part> = Vec::new();
    let mut said: HashSet<String> = HashSet::new();
    for p in parts {
        match group_of(&p.key) {
            None => out.push(p.clone()),
            Some(g) if said.insert(g.clone()) => {
                let keys: Vec<String> = parts.iter().filter(|x| group_of(&x.key).as_deref() == Some(g.as_str())).map(|x| x.key.clone()).collect();
                if let Some(value) = group_value(&g, &keys, &get, false) {
                    out.push(Part { key: g, value, raw: Value::Null });
                }
            }
            Some(_) => (),
        }
    }
    out
}

/// The changes as a design's lines and data say them: the elements of one part changed together are one change of
/// that part, with what it read in the design the user owns and what it reads now.
fn grouped_changes(elements: &[Change], parts: &[Part]) -> Vec<Change> {
    let now: HashMap<&str, &str> = parts.iter().map(|p| (p.key.as_str(), p.value.as_str())).collect();
    let mut out: Vec<Change> = Vec::new();
    let mut said: HashSet<String> = HashSet::new();
    for c in elements {
        let Some(g) = group_of(&c.key) else {
            out.push(c.clone());
            continue;
        };
        if !said.insert(g.clone()) {
            continue;
        }
        // a part changed where rb says it alike, with no was or now to say, is said bare
        let members: Vec<&Change> = elements.iter().filter(|x| group_of(&x.key).as_deref() == Some(g.as_str()) && !x.bare()).collect();
        if members.is_empty() {
            out.push(Change { key: g, change: "changed", was: None, now: None });
            continue;
        }
        let mut keys: Vec<String> = parts.iter().filter(|p| group_of(&p.key).as_deref() == Some(g.as_str())).map(|p| p.key.clone()).collect();
        for m in &members {
            if !keys.contains(&m.key) {
                keys.push(m.key.clone());
            }
        }
        let was_of = |k: &str| match members.iter().find(|x| x.key == k) {
            Some(x) => x.was.clone(),
            None => now.get(k).map(|s| s.to_string()),
        };
        let now_of = |k: &str| match members.iter().find(|x| x.key == k) {
            Some(x) => x.now.clone(),
            None => now.get(k).map(|s| s.to_string()),
        };
        let (was, is) = (group_value(&g, &keys, &was_of, true), group_value(&g, &keys, &now_of, true));
        let change = match (&was, &is) {
            (None, _) => "added",
            (_, None) => "removed",
            _ => "changed",
        };
        out.push(Change { key: g, change, was, now: is });
    }
    out
}

/// An experiment's design and its standing: `yours` (no session wrote any part of it), `not_confirmed` (a session
/// wrote some of it, and it is no design the user owns), `confirmed` (it is exactly a design the user owns: one they
/// confirmed, or made from one with their own writes), or `changed` (it is none, and the user owns an earlier one,
/// whose difference it says). The middle two of those that wait take one rb confirm.
#[derive(Debug, Clone, Default)]
pub struct Design {
    pub experiment: String,
    pub parts: Vec<Part>,
    /// a short hash of rb's structured form, kept as the design's summary key
    pub key: String,
    /// the full identity used by a confirmation command; a short-key collision
    /// must never confirm a different design from the one that was listed
    confirmation_key: String,
    pub state: &'static str,
    pub changes: Vec<Change>,
    /// the latest confirmation: when, and who confirmed it (in the data only)
    pub confirmed: Option<(String, String)>,
    /// the parts that read as in the design the user owns closest to it
    covered: HashSet<String>,
    /// whether the gate a session set waits with the design
    gate_waits: bool,
    /// whether, of the gate's branches and its words, only its first branch is one a session's line may have written
    /// as it reads now and not as in a design the user owns
    gate_first: bool,
    /// who last wrote its why, where a line says
    why_writer: String,
    /// whether a session's line may have written its gate, or its why, as they read now
    session_gate: bool,
    session_why: bool,
    /// the last session that wrote a part of it
    session: String,
    /// what is missing from a design a session wrote some of: "hypothesis or purpose", "why"
    pub not_recorded: Vec<&'static str>,
    /// the parts that differ from the design the user owns closest to it, each apart (every part where they own none)
    waits: Vec<String>,
}

impl Design {
    /// Whether it waits for the user's one confirmation.
    pub fn pending(&self) -> bool {
        matches!(self.state, "not_confirmed" | "changed")
    }

    /// The command that confirms it as it reads now (on a screen that shows another place, with the cd to it first).
    pub fn confirm(&self) -> String {
        crate::view::from_here(&format!("rb confirm {}@{}", self.experiment, self.confirmation_key()))
    }

    pub fn confirmation_key(&self) -> &str {
        if self.confirmation_key.is_empty() { &self.key } else { &self.confirmation_key }
    }

    /// The command where it waits, else nothing.
    pub fn waiting_command(&self) -> String {
        if self.pending() { self.confirm() } else { String::new() }
    }

    /// Whether a part reads as in a design the user owns: words a session gave there read as confirmed.
    pub fn covers(&self, key: &str) -> bool {
        self.covered.contains(key)
    }

    /// Whether what hangs on the whole design reads as the user's: what meeting a criterion would mean is decided by its
    /// rules, its variants' roles and the settings that say which runs count, so it is theirs only while the design is.
    fn settled(&self) -> bool {
        self.state == "confirmed"
    }

    /// Whether the gate a session set waits with its design: a branch of it, or its words, is not as in a design the
    /// user owns.
    pub fn gate_waits(&self) -> bool {
        self.gate_waits
    }

    /// Whether a gate that waits says so on its first branch, said on its own line, as the only one a session wrote:
    /// else after the gate, on its last line.
    pub fn gate_first(&self) -> bool {
        self.gate_waits && self.gate_first
    }

    /// Whether the gate and the words given with it read as in a design the user confirmed or owns since, whatever
    /// else differs.
    pub fn covers_gate(&self) -> bool {
        let gate: Vec<&Part> = self.parts.iter().filter(|p| p.key.starts_with("gate:") || p.key == "gate_words").collect();
        self.confirmed.is_some() && !self.gate_waits && !gate.is_empty() && gate.iter().all(|p| self.covers(&p.key))
    }

    /// Whose voice words given with a part read in, by the standing and the values sessions' lines wrote, never by the
    /// times of the lines (`by`, whoever the latest line says wrote them): the user's where the part reads as in a design
    /// they own (words a session gave there read as a note, `view::CONFIRMED`, and a gate as the confirmer's); a
    /// session's where the design waits and a session's line may have written the part (its gate, or its why) as it
    /// reads now, though a later line by the user's clock or on another branch be theirs; else `by`.
    pub fn writer(&self, by: &str, key: &str) -> String {
        if by.is_empty() {
            return String::new();
        }
        let a_session = |waits: bool| if waits && theirs(by) && !self.session.is_empty() { Some(self.session.clone()) } else { None };
        match (key, &self.confirmed) {
            ("gate", Some((_, confirmer))) if !theirs(by) && self.covers_gate() => confirmer.clone(),
            ("gate", _) => a_session(self.gate_waits).unwrap_or_else(|| by.to_string()),
            ("why", _) if theirs(by) => a_session(self.pending() && self.session_why && !self.covers("why")).unwrap_or_else(|| by.to_string()),
            _ if !theirs(by) && self.covers(key) => crate::view::CONFIRMED.into(),
            _ => by.to_string(),
        }
    }

    /// Who an interpretation a criterion's link gives reads as declared by: where the design is one the user owns and
    /// the link reads as in it, `view::CONFIRMED`; an earlier declaration a merge left beside it keeps its writer.
    pub fn link_writer(&self, criterion: &str, l: &QuestionLink) -> String {
        let key = format!("link:{criterion}:{}", l.question);
        let reads = self.parts.iter().find(|p| p.key == key).map(|p| p.value.as_str());
        if self.settled() && reads == Some(link_value(l).as_str()) { self.writer(&l.declared_by, &key) } else { l.declared_by.clone() }
    }

    /// The command that lists it, part by part, with the one that confirms it as listed (on a screen that shows another
    /// place, with the cd to it first).
    pub fn listing(&self) -> String {
        crate::view::from_here(&format!("rb confirm {}", self.experiment))
    }

    /// The line an experiment's own view says once while it waits: `design not confirmed`, with the command that lists
    /// it to confirm, since a view of the experiment does not show every part its key names; at a past version, which
    /// offers no command, `design not confirmed` alone.
    pub fn standing_line(&self) -> Option<String> {
        self.pending().then(|| if crate::view::at_past_version() { "design not confirmed".to_string() } else { format!("design not confirmed · list it to confirm: {}", self.listing()) })
    }

    /// One line per difference from the design the user owns closest to it; a criterion, a setting, the freeze or an
    /// amendment added or taken back whole is one line, the freeze and an amendment with the reason given (a session's,
    /// so its note), and a setting's value or source an added amendment says as it changed is said by that amendment.
    pub fn change_lines(&self) -> Vec<String> {
        // the part a whole one is named by
        let head = |key: &str| -> Option<String> {
            let (kind, rest) = key.split_once(':').unwrap_or((key, ""));
            Some(match kind {
                "rule" | "role" => format!("rule:{rest}"),
                "setting" | "source" => format!("setting:{rest}"),
                "freeze" | "freeze_why" => "freeze".to_string(),
                "amendment" | "amendment_why" => format!("amendment:{rest}"),
                _ => return None,
            })
        };
        let whole = |h: &str, kind: &str| {
            let group: Vec<&Change> = self.changes.iter().filter(|x| head(&x.key).as_deref() == Some(h)).collect();
            group.iter().all(|x| x.change == kind) && group.iter().any(|x| x.key == h)
        };
        let value = |key: String, kind: &str| -> Option<String> {
            self.changes.iter().find(|x| x.key == key).and_then(|x| if kind == "added" { x.now.clone() } else { x.was.clone() })
        };
        let note = |key: String| value(key, "added").map(|why| format!(" · note: {}", listed_text(&why))).unwrap_or_default();
        // what each amendment a session added says it changed: a setting's value, a new setting, its source
        let amended: Vec<&str> = self.changes.iter().filter(|x| x.key.starts_with("amendment:") && x.change == "added").filter_map(|x| x.now.as_deref()).collect();
        let by_amendment = |c: &Change| -> bool {
            let (kind, name) = c.key.split_once(':').unwrap_or((c.key.as_str(), ""));
            let name = addressed(name);
            let (was, now) = (c.was.as_deref().unwrap_or("none"), c.now.as_deref().unwrap_or("none"));
            let step = match (kind, c.change) {
                ("setting", "added") if c.now.is_some() => return amended.iter().any(|a| *a == format!("setting {name}: new, {now}")),
                ("setting", "changed") if c.was.is_some() && c.now.is_some() => format!("{was} -> {now}"),
                ("source", _) if c.was.is_some() || c.now.is_some() => format!("source {was} -> {now}"),
                _ => return false,
            };
            amended.iter().any(|a| a.starts_with(&format!("setting {name}: ")) && a.split("; ").any(|part| part == step || part.ends_with(&format!(": {step}"))))
        };
        // a setting an amendment says, whose source changed with it: the source is said alone, as the amendment does not
        let amended_away: Vec<bool> = self.changes.iter().map(by_amendment).collect();
        let halves: HashSet<String> = self.changes.iter().zip(&amended_away).filter(|(_, away)| **away).filter_map(|(c, _)| head(&c.key)).collect();
        let mut out: Vec<String> = Vec::new();
        let mut said: HashSet<String> = HashSet::new();
        for (c, away) in self.changes.iter().zip(&amended_away) {
            if *away {
                continue;
            }
            // a criterion taken back whole takes its links with it
            if let Some(rest) = c.key.strip_prefix("link:") {
                let crit = rest.split_once(':').map_or(rest, |(c, _)| c);
                if c.change == "removed" && whole(&format!("rule:{crit}"), "removed") {
                    continue;
                }
            }
            let Some(h) = head(&c.key) else {
                out.push(c.said());
                continue;
            };
            if said.contains(&h) {
                continue;
            }
            let Some(kind) = ["added", "removed"].into_iter().find(|k| whole(&h, k) && !halves.contains(&h)) else {
                out.push(c.said());
                continue;
            };
            said.insert(h.clone());
            let (what, rest) = h.split_once(':').unwrap_or((h.as_str(), ""));
            let main = value(h.clone(), kind).map(|v| said_value(&h, &v));
            let line = match (what, kind) {
                ("rule", _) => {
                    let role = value(format!("role:{rest}"), kind).map(|r| format!(" ({r})")).unwrap_or_default();
                    let main = main.map(|m| format!("{m}{role}")).unwrap_or_default();
                    if kind == "added" { format!("criterion {rest} added: {main}") } else { format!("criterion {rest} removed, was {main}") }
                }
                ("setting", _) => {
                    let from = value(format!("source:{rest}"), kind).map(|s| format!(" from {s}")).unwrap_or_default();
                    let main = main.map(|m| format!("{m}{from}")).unwrap_or_default();
                    let rest = addressed(rest);
                    if kind == "added" { format!("setting {rest} added: {main}") } else { format!("setting {rest} removed, was {main}") }
                }
                ("freeze", "added") => main.unwrap_or_else(|| "frozen".into()) + &note("freeze_why".into()),
                ("freeze", _) => format!("the freeze removed, was {}", main.unwrap_or_default()),
                (_, "added") => format!("amended: {}", value(h.clone(), kind).unwrap_or_default()) + &note(format!("amendment_why:{rest}")),
                _ => format!("amendment {rest} removed"),
            };
            out.push(visible(&format!("{}: {line}", since())));
        }
        out
    }

    /// What its own view says of it, in order: the standing line, then how it differs from the design the user owns.
    pub fn lines(&self) -> Vec<String> {
        let mut out: Vec<String> = self.standing_line().into_iter().collect();
        if self.pending() {
            out.extend(self.change_lines());
        }
        out
    }

    /// The parts that wait for the user, each with the session that wrote some of it: what a clone carries, so a design
    /// that waits here waits there too.
    pub fn unconfirmed(&self) -> Vec<(String, String)> {
        if !self.pending() || self.session.is_empty() {
            return Vec::new();
        }
        self.waits.iter().map(|k| (k.clone(), self.session.clone())).collect()
    }

    /// The data: what --json, MCP and the SDK carry on the experiment.
    pub fn to_value(&self) -> Value {
        crate::obj! {
            "standing" => self.state, "pending" => self.pending(), "key" => self.key.clone(), "confirm" => self.waiting_command(),
            "changes" => Value::List(self.changes.iter().map(Change::to_value).collect()),
            "not_recorded" => Value::list_of_str(self.not_recorded.iter().map(|s| s.to_string())),
            "confirmed" => self.confirmed.as_ref().map(|(at, by)| crate::obj! {"at" => at.clone(), "by" => by.clone()}).unwrap_or(Value::Null),
            "parts" => Value::List(grouped_parts(&said_parts(&self.parts)).iter().map(|p| crate::obj! {"key" => p.key.clone(), "part" => p.label(), "value" => p.value.clone()}).collect())
        }
    }
}

// ---------------------------------------------------------------- a design's parts, and its key

/// rb's structured form of a design: each part's key with its structured value, in key order.
fn structured(parts: &[Part]) -> Dict {
    let mut keyed: Vec<(&String, &Value)> = parts.iter().map(|p| (&p.key, &p.raw)).collect();
    keyed.sort_by(|a, b| a.0.cmp(b.0));
    keyed.into_iter().map(|(k, v)| (k.clone(), v.clone())).collect()
}

/// The full hash of a design in rb's structured form: whether two designs are the same one.
fn digest(form: &Dict) -> String {
    #[cfg(test)]
    tests::DIGESTS.with(|n| n.set(n.get() + 1));
    crate::rbyaml::sha256_hex(canonical(&Value::Dict(form.clone())).as_bytes())
}

/// A design's short summary key, independent of how its parts are worded.
#[cfg(test)]
fn design_key(parts: &[Part]) -> String {
    digest(&structured(parts)).chars().take(7).collect()
}

/// The parts as rb says them, as the first builds kept a confirmation: each with a value to say, in the order listed.
fn said_parts(parts: &[Part]) -> Vec<Part> {
    parts.iter().filter(|p| !p.value.is_empty()).cloned().collect()
}

/// The hash the first builds named a design by, over the parts as rb says them, in the order listed: what an earlier
/// rb checks a confirmation's `detail.parts` against.
fn said_key(listed: &[(String, String)]) -> String {
    let said = Value::List(listed.iter().map(|(k, v)| Value::list_of_str([k.clone(), v.clone()])).collect());
    crate::rbyaml::sha256_hex(canonical(&said).as_bytes()).chars().take(7).collect()
}

/// A structured value with nothing in it left out: no null, empty text, empty list or empty map, so a field a later
/// rb adds, unset, leaves the form as it was.
fn stripped(d: Dict) -> Value {
    Value::Dict(
        d.into_iter()
            .filter(|(_, v)| match v {
                Value::Null => false,
                Value::Str(s) => !s.is_empty(),
                Value::List(l) => !l.is_empty(),
                Value::Dict(m) => !m.is_empty(),
                _ => true,
            })
            .collect(),
    )
}

/// A source as the writer gave it: what rb found when it last read it is left out, so a check changes no design.
fn source_form(s: &Source) -> Value {
    let mut d = s.dump(true);
    d.shift_remove("resolved");
    stripped(d)
}

/// What a criterion's rule is made of, structured: what it reads, how it compares, and how many units it needs.
const RULE_FIELDS: &[&str] = &["metric", "subject", "reference", "estimand", "where", "subject_where", "reference_where", "comparator", "target", "tolerance", "margin", "over", "min_n", "noise"];

fn rule_form(c: &Criterion) -> Value {
    let all = c.dump(true);
    stripped(RULE_FIELDS.iter().filter_map(|k| all.get(*k).map(|v| (k.to_string(), v.clone()))).collect())
}

/// A part's structured value as rb says it: how a design the user owns reads where it differs from this one.
fn said_of(key: &str, raw: &Value) -> String {
    #[cfg(test)]
    tests::SAID.with(|n| n.set(n.get() + 1));
    let kind = key.split(':').next().unwrap_or("");
    let text = || raw.as_str().map(str::to_string).unwrap_or_else(|| canonical(raw));
    let source = |v: &Value| v.as_dict().and_then(|d| Source::validate(&Value::Dict(d.clone()), &mut Vec::new(), &mut Vec::new())).map(|s| s.label());
    match kind {
        "setting" if raw.get("per_run").map_or(false, Value::truthy) => "per run".into(),
        "setting" => show(raw.get("value").unwrap_or(&Value::Null)),
        "source" => source(raw).unwrap_or_else(|| canonical(raw)),
        "optional" => "optional".into(),
        "factor" => "a fixed design factor".into(),
        "cited" => format!("{}{}", show(raw.get("value").unwrap_or(&Value::Null)), raw.get("source").and_then(source).map(|s| format!(" from {s}")).unwrap_or_default()),
        "rule" => {
            let mut d = raw.as_dict().cloned().unwrap_or_default();
            for (k, v) in [("created_by", "human:rb"), ("created_at", ""), ("id", "c"), ("experiment", "e"), ("statement", "s")] {
                d.insert(k.into(), Value::Str(v.into()));
            }
            Criterion::validate(&Value::Dict(d), &mut Vec::new(), &mut Vec::new()).map(|c| c.rule()).unwrap_or_else(|| canonical(raw))
        }
        "link" => {
            let w = text();
            if w.is_empty() { NO_INTERPRETATION.into() } else { format!("when met, \"{}\"", crate::view::on_one_line(&w)) }
        }
        "freeze" => format!("frozen {}", text().chars().take(10).collect::<String>()),
        _ => text(),
    }
}

/// Parts in the order rb lists them: questions, hypothesis, purpose, why, variants, what it varies, settings,
/// criteria, gate, the gate's words, freeze, amendments.
type Ranked = Vec<(u8, Part)>;

/// Where a part comes in that order, by its key.
fn rank_of(key: &str) -> u8 {
    match key.split(':').next().unwrap_or("") {
        "question" | "questions" => 0,
        "hypothesis" => 1,
        "purpose" => 2,
        "why" => 3,
        "variant" => 4,
        "varies" => 5,
        "setting" | "source" | "optional" | "factor" | "cited" => 6,
        "rule" | "role" | "link" => 7,
        "gate" => 8,
        "gate_words" => 9,
        "freeze" | "freeze_why" => 10,
        _ => 11,
    }
}

fn push(out: &mut Ranked, rank: u8, key: String, value: String, raw: Value) {
    out.push((rank, Part { key, value, raw }));
}

/// A part that is words or an id, there only when it says something.
fn text(out: &mut Ranked, rank: u8, key: String, said: &str) {
    if !said.is_empty() {
        push(out, rank, key, said.to_string(), Value::Str(said.to_string()));
    }
}

/// A setting's parts: its value, its source, and its marks (optional, a fixed design factor, what a cited source used),
/// each a part of its own.
fn setting_parts(out: &mut Ranked, name: &str, s: &Setting) {
    let (said, raw) = if s.per_run { ("per run".to_string(), crate::obj! {"per_run" => true}) } else { (show(&s.value), crate::obj! {"value" => s.value.clone()}) };
    push(out, 6, format!("setting:{name}"), said, raw);
    if let Some(src) = &s.source {
        push(out, 6, format!("source:{name}"), src.label(), source_form(src));
    }
    if !s.required {
        push(out, 6, format!("optional:{name}"), "optional".into(), Value::Bool(true));
    }
    if s.design_factor == Some(true) {
        push(out, 6, format!("factor:{name}"), "a fixed design factor".into(), Value::Bool(true));
    }
    if let Some(c) = &s.cited {
        let said = format!("{}{}", show(&c.value), c.source.as_ref().map(|src| format!(" from {}", src.label())).unwrap_or_default());
        let raw = crate::obj! {"value" => c.value.clone(), "source" => c.source.as_ref().map(source_form).unwrap_or(Value::Null)};
        push(out, 6, format!("cited:{name}"), said, raw);
    }
}

/// The parts of a setting a write states (`which`: setting, source, optional, factor, cited), each with what it reads
/// as rb says it (null where the setting holds no such part): what that write's line names.
pub(crate) fn stated_parts(name: &str, s: &Setting, which: &[&str]) -> Dict {
    let mut out: Ranked = Vec::new();
    setting_parts(&mut out, name, s);
    which
        .iter()
        .map(|k| {
            let key = format!("{k}:{name}");
            let v = out.iter().find(|(_, p)| p.key == key && !p.value.is_empty()).map_or(Value::Null, |(_, p)| Value::Str(p.value.clone()));
            (key, v)
        })
        .collect()
}

/// A setting's value as rb confirm lists it: its value, whether it is optional, a fixed design factor, and what a
/// cited source used, each as `listed_value` and `listed_source` say it.
fn setting_value(s: &Setting) -> String {
    let mut v = if s.per_run { "per run".to_string() } else { listed_value(&s.value) };
    if !s.required {
        v.push_str(" (optional)");
    }
    if s.design_factor == Some(true) {
        v.push_str(" (a fixed design factor)");
    }
    if let Some(c) = &s.cited {
        v.push_str(&format!(" (the cited source used {}{})", listed_value(&c.value), c.source.as_ref().map(|src| format!(" from {}", listed_source(src))).unwrap_or_default()));
    }
    v
}

/// Whether a terminal or a page would hide a character, or act on it, where it shows text: a control character (a line
/// break, an escape that moves the cursor), a line or paragraph separator, a format character (a mark that is not shown
/// or turns the text's direction, a soft hyphen, a tag), or one Unicode says a screen draws as nothing where it cannot
/// show it (a variation selector, a Hangul filler, a joiner): Unicode 15.1's Cc, Zl, Zp, Cf and
/// Default_Ignorable_Code_Point.
fn hidden(c: char) -> bool {
    c.is_control()
        || matches!(c as u32, 0xad | 0x34f | 0x600..=0x605 | 0x61c | 0x6dd | 0x70f | 0x890..=0x891 | 0x8e2 | 0x115f..=0x1160 | 0x17b4..=0x17b5
            | 0x180b..=0x180f | 0x200b..=0x200f | 0x2028..=0x202e | 0x2060..=0x206f | 0x3164 | 0xfe00..=0xfe0f | 0xfeff | 0xffa0
            | 0xfff0..=0xfffb | 0x110bd | 0x110cd | 0x13430..=0x1343f | 0x1bca0..=0x1bca3 | 0x1d173..=0x1d17a | 0xe0000..=0xe0fff)
}

/// A line as rb shows a design where the user confirms it: each character `hidden` says a screen would hide or act on is
/// written as its escape as in JSON (`\n`, `\u001b`, one past U+FFFF as its two halves, `\udb40\udc41`), so what shows
/// is what the record holds.
pub(crate) fn visible(line: &str) -> String {
    if !line.chars().any(hidden) {
        return line.to_string();
    }
    line.chars()
        .map(|c| match c {
            '\n' => "\\n".to_string(),
            '\r' => "\\r".to_string(),
            '\t' => "\\t".to_string(),
            c if hidden(c) => c.encode_utf16(&mut [0; 2]).iter().map(|u| format!("\\u{u:04x}")).collect(),
            c => c.to_string(),
        })
        .collect()
}

/// Text as every screen draws it in a record where something a session wrote can wait for the user
/// (`Ledger::shows_escapes`): line by line, each character `hidden` says a screen would hide or act on as its escape,
/// as rb confirm lists it.
pub fn shown(text: &str) -> String {
    if !text.chars().any(hidden) {
        return text.to_string();
    }
    text.split('\n').map(visible).collect::<Vec<_>>().join("\n")
}

/// Whether `shown` draws any escape in text: a character `hidden` says a screen would hide or act on, but a line break.
pub fn draws_escapes(text: &str) -> bool {
    text.chars().any(|c| c != '\n' && hidden(c))
}

/// What a screen acts on rather than draws (an id, a command, a path it runs in), which `shown_in` leaves as it is.
const ACTED_ON: &[&str] = &["id", "ref", "request_id", "note_ids", "argv", "dismiss_key", "confirm", "list", "command", "run_in", "place"];

/// Each piece of text in a screen's data (rb open's state and cards, rb ui's home and cards) as `shown` draws it: whether
/// it drew an escape in any.
pub fn shown_in(v: &mut Value) -> bool {
    match v {
        Value::Str(s) if s.chars().any(hidden) => {
            let drawn = shown(s);
            let escaped = drawn != *s;
            *s = drawn;
            escaped
        }
        Value::List(l) => l.iter_mut().fold(false, |any, x| shown_in(x) | any),
        Value::Dict(d) => d.iter_mut().filter(|(k, _)| !ACTED_ON.contains(&k.as_str()) && !k.ends_with("_id")).fold(false, |any, (_, x)| shown_in(x) | any),
        _ => false,
    }
}

/// Text quoted and escaped as in JSON, and as `visible` says what a screen would hide.
fn quoted(text: &str) -> String {
    visible(&canonical(&Value::Str(text.to_string())))
}

/// Words given with a design as rb confirm lists them: as given where they are plain words on one line, else quoted and
/// escaped, so two texts the key tells apart never list alike (a line break and a space, a trailing space, a `·` where
/// another part could begin, a quote, a character a screen would hide).
fn listed_text(text: &str) -> String {
    if text.chars().any(hidden) || text.contains(['"', '\\', '·']) || text.trim() != text { quoted(text) } else { text.to_string() }
}

/// A value as rb confirm lists it. Quote and escape text, including text that itself
/// looks quoted or ends in a type label; a label alone can hide a different value.
fn listed_value(v: &Value) -> String {
    match v {
        Value::Null => "unknown".into(),
        _ => visible(&canonical(v)),
    }
}

/// What a source holds that rb's short form of it leaves out: the term it is read by, the whole of a long quote, its
/// line beside a key.
fn source_extras(s: &Source) -> Vec<String> {
    let mut out = Vec::new();
    if let (Some(_), Some(l)) = (s.key.as_deref().filter(|k| !k.is_empty()), s.line.filter(|l| *l != 0)) {
        out.push(format!("line {l}"));
    }
    if let Some(q) = s.quote.as_deref().filter(|q| q.chars().count() > 60) {
        out.push(format!("quote {}", quoted(q)));
    }
    if let Some(t) = s.term.as_deref().filter(|t| !t.is_empty()) {
        out.push(format!("term {}", quoted(t)));
    }
    out
}

/// A source as rb confirm lists it, with every recorded field that its key covers: rb's short form
/// (`configs/train.yaml#optim.lr`), with what that form leaves out after it (`docs/train.md:3 (term "learning rate")`,
/// a commit in full), where the short form says each field apart; else each field by its name, so two sources never
/// list alike.
fn listed_source(s: &Source) -> String {
    // a character that ends a field of the short form, or that a screen would hide; a path ends at `#` and `:` too
    let ends = |c: char| c.is_whitespace() || hidden(c) || "\"\\@()".contains(c);
    let plain = |t: &str| !t.is_empty() && !t.chars().any(ends);
    let short = match s.kind.as_str() {
        "file" => {
            s.path.as_deref().map_or(false, |p| plain(p) && !p.contains(['#', ':']))
                && s.key.as_deref().map_or(true, plain)
                && s.quote.as_deref().map_or(true, |q| q.trim() == q && !q.chars().any(|c| hidden(c) || "\"\\".contains(c)))
                && s.pointer.is_none()
                && s.url.is_none()
                && s.locator.is_none()
                && s.note.is_empty()
        }
        // `note: <its words>`, where nothing follows the words
        "note" => {
            [&s.path, &s.key, &s.quote, &s.term, &s.commit, &s.pointer, &s.url, &s.locator].iter().all(|f| f.is_none())
                && s.line.is_none()
                && !s.note.is_empty()
                && s.note.trim() == s.note
                && !s.note.chars().any(|c| hidden(c) || "\"\\".contains(c))
        }
        _ => false,
    };
    if short {
        let mut extras = source_extras(s);
        if let Some(c) = s.commit.as_deref().filter(|c| c.chars().count() > 7) {
            extras.push(format!("commit {c}"));
        }
        return if extras.is_empty() { s.label() } else { format!("{} ({})", s.label(), extras.join("; ")) };
    }
    let raw = source_form(s);
    let fields: Vec<String> = raw.as_dict().into_iter().flatten()
        .filter(|(k, _)| k.as_str() != "kind")
        .map(|(k, v)| format!("{k} {}", visible(&canonical(v))))
        .collect();
    format!("{} ({})", s.kind, fields.join(", "))
}

/// A criterion's selectors as rb confirm lists them, with text quoted and escaped.
fn listed_rule(c: &Criterion) -> String {
    let quoted = |m: &Dict| -> Dict { m.iter().map(|(k, v)| (k.clone(), Value::Str(canonical(v)))).collect() };
    let mut shown = c.clone();
    shown.r#where = quoted(&c.r#where);
    shown.subject_where = quoted(&c.subject_where);
    shown.reference_where = quoted(&c.reference_where);
    // each number with every digit it needs to read back as itself
    visible(&shown.rule_with(crate::assess::exact))
}

/// What a criterion's link to a question says it would mean when met, as rb says its part.
fn link_value(l: &QuestionLink) -> String {
    if l.when_met.is_empty() { NO_INTERPRETATION.into() } else { format!("when met, \"{}\"", crate::view::on_one_line(&l.when_met)) }
}

/// What a link says as rb confirm lists it: the question, with what meeting it would mean there where that is recorded.
fn link_said(l: &QuestionLink) -> String {
    if l.when_met.is_empty() { format!(" · for {}", l.question) } else { format!(" · for {}, when met, {}", l.question, quoted(&l.when_met)) }
}

/// Keep the boundaries between a gate's branches and words visible in a confirmation.
/// Ordinary branch text reads as before; punctuation that could name another part is quoted and escaped.
fn listed_gate(if_holds: &str, if_not: &str, words: &str, by: &str) -> String {
    let said_words = if words.is_empty() { String::new() } else { crate::view::say_words(&quoted(words), by) };
    let mut used_words = false;
    let mut parts: Vec<String> = [("if it holds", if_holds), ("if not", if_not)]
        .into_iter().filter(|(_, text)| !text.is_empty()).map(|(label, text)| {
            let value = if !said_words.is_empty() && text == words && !used_words {
                used_words = true;
                said_words.clone()
            } else {
                listed_text(text)
            };
            format!("{label}: {value}")
        }).collect();
    if parts.is_empty() {
        return String::new();
    }
    if !said_words.is_empty() && !used_words {
        parts.push(said_words);
    }
    parts.join(" · ") + crate::view::not_confirmed(by)
}

/// The parts an experiment's own file holds: all but its variants', its criteria' and the gate's words.
fn own_parts(e: &Experiment, out: &mut Ranked) {
    for q in &e.questions {
        text(out, 0, format!("question:{q}"), q);
    }
    text(out, 1, "hypothesis".into(), &e.hypothesis);
    text(out, 2, "purpose".into(), &e.purpose);
    text(out, 3, "why".into(), &e.why);
    for f in &e.varies {
        text(out, 5, format!("varies:{f}"), f);
    }
    for s in e.settings.iter().filter(|s| s.retracted.is_none()) {
        setting_parts(out, &s.name, s);
    }
    text(out, 8, "gate:if_holds".into(), &e.if_holds);
    text(out, 8, "gate:if_not".into(), &e.if_not);
    if let Some(f) = &e.frozen {
        push(out, 10, "freeze".into(), format!("frozen {}", f.at.chars().take(10).collect::<String>()), Value::Str(f.at.clone()));
        text(out, 10, "freeze_why".into(), &f.why);
    }
    for (i, a) in e.amendments.iter().enumerate() {
        text(out, 11, format!("amendment:{}", i + 1), &a.change);
        text(out, 11, format!("amendment_why:{}", i + 1), &a.why);
    }
}

/// The parts a variant's file holds: its role and its own settings, none once it is taken back.
fn variant_parts(v: &Variant, out: &mut Ranked) {
    if v.retracted.is_some() {
        return;
    }
    text(out, 4, format!("variant:{}", v.name), &v.role);
    for s in v.settings.iter().filter(|s| s.retracted.is_none()) {
        setting_parts(out, &format!("{}/{}", v.name, s.name), s);
    }
}

/// The parts a criterion's file holds: its rule, its role and each question it bears on, none once it is taken back.
fn criterion_parts(c: &Criterion, out: &mut Ranked) {
    if c.retracted.is_some() {
        return;
    }
    push(out, 7, format!("rule:{}", c.id), c.rule(), rule_form(c));
    text(out, 7, format!("role:{}", c.id), &c.role);
    for l in c.active_links() {
        push(out, 7, format!("link:{}:{}", c.id, l.question), link_value(l), Value::Str(l.when_met.clone()));
    }
}

/// A file of a design in another form than it stands in: its kind and id, and its form (none: no such file).
type Swap<'a> = (&'a str, &'a str, Option<&'a Dict>);

/// A file an adoption takes, as it was before the edits: its kind and id, and its form there (none: no such file), or
/// none where rb cannot be certain of it.
pub(crate) type Before = (String, String, Option<Option<Dict>>);

/// The parts of a design as they read, in the order rb lists them; with `swaps`, some of its files in other forms.
fn parts_of(e: &Experiment, criteria: &[&Criterion], gate_words: &str, swaps: &[Swap]) -> Vec<Part> {
    let mut out: Ranked = Vec::new();
    let (mut errs, mut loc) = (Vec::new(), Vec::new());
    let swapped = |kind: &str, id: &str| swaps.iter().find(|(k, i, _)| *k == kind && *i == id).map(|(_, _, f)| *f);
    let own = match swapped("experiment", &e.id) {
        Some(form) => form.and_then(|f| Experiment::validate(&Value::Dict(without_newer_relations("experiment", f.clone())), &mut loc, &mut errs)),
        None => Some(e.clone()),
    };
    let (if_holds, if_not) = own.as_ref().map_or((String::new(), String::new()), |x| (x.if_holds.clone(), x.if_not.clone()));
    if let Some(x) = &own {
        own_parts(x, &mut out);
    }
    for v in &e.variants {
        match swapped("variant", &format!("{}/{}", e.id, v.name)) {
            Some(form) => {
                if let Some(x) = form.and_then(|f| Variant::validate(&Value::Dict(f.clone()), &mut loc, &mut errs)) {
                    variant_parts(&x, &mut out);
                }
            }
            None => variant_parts(v, &mut out),
        }
    }
    // a variant a swapped form holds that the record no longer does
    for (kind, id, form) in swaps {
        let name = id.split_once('/').map_or("", |(_, n)| n);
        if let ("variant", Some(form)) = (*kind, form) {
            if !e.variants.iter().any(|v| v.name == name) {
                if let Some(x) = Variant::validate(&Value::Dict((*form).clone()), &mut loc, &mut errs) {
                    variant_parts(&x, &mut out);
                }
            }
        }
    }
    let crit = |form: &Dict| {
        let mut f = form.clone();
        if !f.contains_key("experiment") {
            f.insert("experiment".into(), Value::Str(e.id.clone()));
        }
        Criterion::validate(&Value::Dict(without_newer_relations("criterion", f)), &mut Vec::new(), &mut Vec::new())
    };
    for c in criteria {
        match swapped("criterion", &c.id) {
            Some(form) => {
                if let Some(x) = form.and_then(crit) {
                    criterion_parts(&x, &mut out);
                }
            }
            None => criterion_parts(c, &mut out),
        }
    }
    for (kind, id, form) in swaps {
        if let ("criterion", Some(form)) = (*kind, form) {
            if !criteria.iter().any(|c| c.id == *id) {
                if let Some(x) = crit(form) {
                    criterion_parts(&x, &mut out);
                }
            }
        }
    }
    if !(if_holds.is_empty() && if_not.is_empty()) {
        text(&mut out, 9, "gate_words".into(), gate_words);
    }
    out.sort_by_key(|(rank, _)| *rank);
    out.into_iter().map(|(_, p)| p).collect()
}

// ---------------------------------------------------------------- what rb says of a design's parts in a line

/// The parts of a design one file holds, by key: each as rb says it, with rb's structured form of it.
pub(crate) type FileParts = HashMap<String, (String, Value)>;

/// The parts of a design one file holds, from its stored form; nothing when it cannot be read as one.
fn form_parts(kind: &str, experiment: &str, form: Dict) -> Option<FileParts> {
    let mut out: Ranked = Vec::new();
    let (mut errs, mut loc) = (Vec::new(), Vec::new());
    match kind {
        "experiment" => own_parts(&Experiment::validate(&Value::Dict(without_newer_relations("experiment", form)), &mut loc, &mut errs)?, &mut out),
        "variant" => variant_parts(&Variant::validate(&Value::Dict(form), &mut loc, &mut errs)?, &mut out),
        "criterion" => {
            let mut form = form;
            if !form.contains_key("experiment") {
                form.insert("experiment".into(), Value::Str(experiment.to_string()));
            }
            criterion_parts(&Criterion::validate(&Value::Dict(without_newer_relations("criterion", form)), &mut loc, &mut errs)?, &mut out)
        }
        _ => return None,
    }
    errs.is_empty().then(|| out.into_iter().map(|(_, p)| (p.key, (p.value, p.raw))).collect())
}

/// The parts of a design one file holds, as its stored form says them; nothing when it cannot be read as one.
pub(crate) fn file_parts(kind: &str, experiment: &str, form: &Dict) -> Option<FileParts> {
    form_parts(kind, experiment, form.clone())
}

/// The experiment a design's file belongs to: its own id, a variant's experiment, a criterion's.
pub(crate) fn file_experiment(kind: &str, id: &str, form: &Dict) -> Option<String> {
    match kind {
        "experiment" => Some(id.to_string()),
        "variant" => id.split_once('/').map(|(e, _)| e.to_string()),
        "criterion" => form.get_str("experiment").map(str::to_string),
        _ => None,
    }
}

/// What a write changed of the design one file holds, as its log line keeps it (`detail.changed_parts`): each part whose
/// structured form is not what any of the forms before it held (one, or each side of a merge), so a value's type or a
/// source's term counts though rb says it alike, with what it reads after it as rb says it (null where it is gone). On
/// the user's line (`with_sources`) a setting's value comes with where it came from, as an earlier rb reads it, so a
/// write that changed one says its source; a session's line names a source only where it changed. A line that changed
/// none of a design's parts names that design with none: no part of it was written.
pub(crate) fn parts_changed(before: &[FileParts], after: &FileParts, into: &mut Dict, with_sources: bool) {
    let empty = HashMap::new();
    let before: Vec<&FileParts> = if before.is_empty() { vec![&empty] } else { before.iter().collect() };
    let mut keys: Vec<&String> = after.keys().chain(before.iter().flat_map(|b| b.keys())).collect();
    keys.sort();
    keys.dedup();
    let same = |a: Option<&(String, Value)>, b: Option<&(String, Value)>| match (a, b) {
        (Some((_, x)), Some((_, y))) => canonical(x) == canonical(y),
        (a, b) => a.is_none() && b.is_none(),
    };
    let moved = |k: &str| before.iter().all(|b| !same(b.get(k), after.get(k)));
    let said = |k: &str| after.get(k).map_or(Value::Null, |(v, _)| Value::Str(v.clone()));
    for k in keys {
        if moved(k) {
            into.insert(k.clone(), said(k));
            if let Some(n) = k.strip_prefix("setting:").filter(|_| with_sources) {
                into.insert(format!("source:{n}"), said(&format!("source:{n}")));
            }
        }
    }
}

/// What an adoption changed of the designs a file belongs to (`detail.changed_parts`), from the forms rb last wrote of it
/// (`before`, each of its own experiment's design) to the file as it stands (`after`, with the experiment it is of; none
/// where it is gone; `path`, the experiment its path says, for a form that does not): a file moved to another experiment
/// takes its parts from one design and gives them to the other. None where a form cannot be read as a design's file.
pub(crate) fn adoption_changes(kind: &str, id: &str, before: &[Dict], after: Option<(&str, &Dict)>, path: Option<&str>, with_sources: bool) -> Option<Dict> {
    let of = |f: &Dict| file_experiment(kind, id, f).or_else(|| after.map(|(x, _)| x.to_string())).or_else(|| path.map(str::to_string));
    let mut exps: Vec<String> = Vec::new();
    for x in before.iter().filter_map(of).chain(after.map(|(x, _)| x.to_string())) {
        if !exps.contains(&x) {
            exps.push(x);
        }
    }
    let mut out = Dict::new();
    for x in &exps {
        let b: Vec<FileParts> = before.iter().filter(|f| of(f).as_deref() == Some(x.as_str())).map(|f| file_parts(kind, x, f)).collect::<Option<_>>()?;
        let a = match after {
            Some((now, f)) if now == x => file_parts(kind, x, f)?,
            _ => HashMap::new(),
        };
        let mut changed = Dict::new();
        parts_changed(&b, &a, &mut changed, with_sources);
        out.insert(x.clone(), Value::Dict(changed));
    }
    Some(out)
}

// ---------------------------------------------------------------- the designs the user owns

/// The parts a confirmation the first builds wrote kept, as listed: `[key, value]` pairs in order, or a map of whole
/// parts from the very first.
fn listed_parts(kept: &Value) -> Option<(Vec<(String, String)>, bool)> {
    match kept {
        Value::List(items) => items
            .iter()
            .map(|item| match item.as_list().map(|l| l.as_slice()) {
                Some([Value::Str(k), Value::Str(v)]) => Some((k.clone(), v.clone())),
                _ => None,
            })
            .collect::<Option<Vec<_>>>()
            .map(|l| (l, false)),
        Value::Dict(d) => Some((d.iter().map(|(k, v)| (k.clone(), crate::pyjson::py_str(v))).collect(), true)),
        _ => None,
    }
}

/// The parts a confirmation the very first builds kept (each part whole, a variant's own setting as
/// `<variant>.<name>`) as the elements they are read by now: each question, each setting varied, each branch of the
/// gate, a setting's value apart from its marks.
fn elements_of(listed: &[(String, String)], e: &Experiment, ever: Option<&Vec<String>>) -> Vec<(String, String)> {
    let variant = |name: &str| -> String {
        match name.split_once('.') {
            Some((v, n)) if e.variants.iter().any(|x| x.name == v) || ever.map_or(false, |all| all.iter().any(|x| x == v)) => format!("{v}/{n}"),
            _ => name.to_string(),
        }
    };
    let mut out: Vec<(String, String)> = Vec::new();
    let mut add = |key: String, value: &str| {
        if !value.is_empty() {
            out.push((key, value.to_string()));
        }
    };
    for (key, value) in listed {
        let (kind, rest) = key.split_once(':').unwrap_or((key.as_str(), ""));
        match kind {
            "questions" => value.split(", ").for_each(|q| add(format!("question:{q}"), q)),
            "varies" => value.split(", ").for_each(|f| add(format!("varies:{f}"), f)),
            "gate" => {
                let (holds, not) = match value.strip_prefix("if it holds: ") {
                    Some(rest) => rest.split_once(" · if not: ").unwrap_or((rest, "")),
                    None => ("", value.strip_prefix("if not: ").unwrap_or("")),
                };
                add("gate:if_holds".into(), holds);
                add("gate:if_not".into(), not);
            }
            "setting" => {
                let name = variant(rest);
                let mut v = value.as_str();
                let mut marks: Vec<(String, String)> = Vec::new();
                if let Some(at) = v.rfind(" (the cited source used ").filter(|_| v.ends_with(')')) {
                    marks.push((format!("cited:{name}"), v[at + " (the cited source used ".len()..v.len() - 1].to_string()));
                    v = &v[..at];
                }
                if let Some(rest) = v.strip_suffix(" (a fixed design factor)") {
                    marks.push((format!("factor:{name}"), "a fixed design factor".into()));
                    v = rest;
                }
                if let Some(rest) = v.strip_suffix(" (optional)") {
                    marks.push((format!("optional:{name}"), "optional".into()));
                    v = rest;
                }
                add(format!("setting:{name}"), v);
                marks.into_iter().rev().for_each(|(k, m)| add(k, &m));
            }
            "source" => add(format!("source:{}", variant(rest)), value),
            _ => add(key.clone(), value),
        }
    }
    out
}

/// A design the user owns, as a line keeps it: rb's structured form of its parts under its short hash (`detail.owned`),
/// or, from a confirmation the first builds wrote, the parts as rb said them (each whole where they were kept as a map),
/// with the lines it saw where it kept them (`detail.seen`, by `line_key`).
enum Kept {
    /// its parts are where the line keeps them (`kept_entry`): whole, or what changed from the design it names
    Form { key: String },
    Said(Vec<(String, String)>, bool, Option<HashSet<String>>),
}

/// A log line's short hash, as a confirmation the first builds wrote keeps the lines it saw.
fn line_key(row: &Dict) -> String {
    let said = Value::list_of_str(["at", "op", "kind", "id", "sha256"].map(|k| row.get_str(k).unwrap_or("").to_string()));
    crate::rbyaml::sha256_hex(canonical(&said).as_bytes()).chars().take(12).collect()
}

/// A design the user owns, and where in the log the line that keeps it is.
struct Owned {
    pos: usize,
    kept: Kept,
}

/// What a line keeps of a design the user owns (`detail.owned[<experiment>]`): its short key, full hash, and parts in
/// rb's structured form, whole, or as what changed from the design it was made from (`from`, its short key;
/// `from_full`, its full hash; `parts`, each part that differs or was added; `removed`, the keys taken away).
fn kept_entry<'a>(row: &'a Dict, experiment: &str) -> Option<&'a Value> {
    #[cfg(test)]
    tests::READS.with(|n| n.set(n.get() + 1));
    row.get("detail")?.get("owned")?.get(experiment)
}

/// How many lines in a row keep a design the user owns as what changed, before one keeps it whole again: so working one
/// out reads few lines, and a line whose earlier ones git did not bring in costs few designs.
const CHANGES_IN_A_ROW: usize = 16;

/// The key a line keeps a design the user owns under (`detail.owned[<experiment>]`), where it keeps its parts too.
/// Whether the parts hash to the key is asked only of a design a reading rests on (`Designs::sound`).
fn owned_key(entry: &Value) -> Option<String> {
    entry.get("parts")?.as_dict()?;
    entry.get_str("key").filter(|k| k.len() == 7).map(str::to_string)
}

/// Whether two structured values are the same, as their canonical form says: the same kind, and the same in every part.
fn identical(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Dict(x), Value::Dict(y)) => same_dict(x, y),
        (Value::List(x), Value::List(y)) => x.len() == y.len() && x.iter().zip(y).all(|(v, w)| identical(v, w)),
        (Value::Str(x), Value::Str(y)) => x == y,
        (Value::Null, Value::Null) => true,
        (Value::Int(x), Value::Int(y)) => x == y,
        (Value::Bool(x), Value::Bool(y)) => x == y,
        (Value::BigInt(x), Value::BigInt(y)) => x == y,
        // as the canonical form says them: 0.0 and -0.0 apart, every NaN alike
        (Value::Float(x), Value::Float(y)) => x.to_bits() == y.to_bits() || (x.is_nan() && y.is_nan()),
        _ => false,
    }
}

fn same_dict(x: &Dict, y: &Dict) -> bool {
    x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).map_or(false, |w| identical(v, w)))
}

/// Each design the user owns once, at the latest line that keeps it: a design kept again adds nothing to the set.
fn once_each(log: &[Dict], experiment: &str, owned: Vec<Owned>) -> Vec<Owned> {
    let entry = |o: &Owned| log.get(o.pos).and_then(|r| kept_entry(r, experiment));
    let mut out: Vec<Owned> = Vec::new();
    // where each key is kept in `out`, so a design kept again is found without reading every other
    let mut at: HashMap<String, Vec<usize>> = HashMap::new();
    for o in owned.into_iter().rev() {
        if let Kept::Form { key } = &o.kept {
            let same = at.get(key).into_iter().flatten().any(|&i| matches!((entry(&out[i]), entry(&o)), (Some(a), Some(b)) if identical(a, b)));
            if same {
                continue;
            }
            at.entry(key.clone()).or_default().push(out.len());
        }
        out.push(o);
    }
    out.reverse();
    out
}

/// What a confirmation's line keeps of the design it confirms (`detail.owned`): its key and its parts in rb's structured
/// form, whole.
fn owned_entry(parts: &[Part]) -> Value {
    let form = structured(parts);
    let full = digest(&form);
    crate::obj! {"key" => full.chars().take(7).collect::<String>(), "full" => full, "parts" => Value::Dict(form)}
}

/// What a line says it compared a write's parts in (`detail.parts_compared`): rb's structured form. An earlier rb
/// compared them as it said them, so its `changed_parts` can name no part where only a value's type or a source's term
/// changed.
const COMPARED: &str = "structured";

/// Keeps on a write's line what it changed of each design (`detail.changed_parts`), and, on a session's line, the only
/// kind whose parts decide a design's standing, that it compared them in rb's structured form (the user's line also
/// names the parts it states, as an earlier rb reads them).
pub(crate) fn keep_changed(detail: &mut Dict, changed: Dict, who: &Actor) {
    detail.insert("changed_parts".into(), Value::Dict(changed));
    if !who.is_human() {
        detail.insert("parts_compared".into(), Value::Str(COMPARED.into()));
    }
}

/// What a line says it changed of each design, where it says it compared the parts in rb's structured form: none on a
/// line an earlier rb wrote.
fn changed_parts(row: &Dict) -> Option<&Dict> {
    let d = row.get("detail")?;
    (d.get_str("parts_compared") == Some(COMPARED)).then(|| d.get("changed_parts").and_then(Value::as_dict)).flatten()
}

/// Whether a session's line wrote a part of an experiment's design: a gate line gives its words, whatever its file
/// holds; a line that says which parts it changed, compared in rb's structured form, says it; else any write to one of
/// the design's files but those that change none of its parts (a note or its retraction, a relation, a check of a
/// source, a reindex, a restore of what rb wrote), as AGENTS.md lists them.
fn wrote_a_part(row: &Dict, experiment: &str, gate: bool) -> bool {
    if gate {
        return true;
    }
    let detail = row.get("detail");
    if let Some(changed) = changed_parts(row).and_then(|c| c.get(experiment)).and_then(Value::as_dict) {
        return !changed.is_empty();
    }
    let op = row.get_str("op").unwrap_or("");
    let a_note = op == "retract" && detail.and_then(|d| d.get_str("note")).is_some();
    !a_note && !matches!(op, "note" | "relate" | "unrelate" | "spec_verify" | "reindex" | "restore")
}

/// The records a log line wrote, as `writes_in` reads them, each with the path it wrote: its kind, its id and its path.
fn written(row: &Dict) -> Vec<(&str, &str, Option<&str>)> {
    fn named(v: &Value) -> Option<(&str, &str)> {
        Some((v.get_str("kind").filter(|s| !s.is_empty())?, v.get_str("id").filter(|s| !s.is_empty())?))
    }
    let sha = |v: &Value| v.get("sha256").map_or(false, |s| s.truthy());
    let mut out = Vec::new();
    let (kind, id) = (row.get_str("kind").filter(|s| !s.is_empty()), row.get_str("id").filter(|s| !s.is_empty()));
    let has = row.get("sha256").map_or(false, |s| s.truthy()) || row.get_str("op") == Some("adopt_delete");
    if let (Some(k), Some(i), true) = (kind, id, has) {
        out.push((k, i, row.get_str("path")));
    }
    for p in row.get("parts").and_then(Value::as_list).into_iter().flatten() {
        if let (Some((k, i)), true) = (named(p), sha(p)) {
            out.push((k, i, p.get_str("path")));
        }
    }
    out
}

/// The experiment each criterion is of (`of`): as it reads, or as the first line that made it said; and every
/// experiment a line says one was of (`ever`): its path, its experiment, the designs it changed, wherever the line is
/// in the log.
#[derive(Default)]
struct Filed {
    of: HashMap<String, String>,
    ever: HashMap<String, Vec<String>>,
}

impl Filed {
    fn read(crits: &[Criterion], log: &[Dict]) -> Filed {
        let mut f = Filed { of: crits.iter().map(|c| (c.id.clone(), c.experiment.clone())).collect(), ever: HashMap::new() };
        for c in crits {
            f.was_of(&c.id, &c.experiment);
        }
        for row in log {
            for (kind, id, path) in written(row) {
                if kind == "criterion" {
                    if let Some(e) = path.and_then(|p| Ledger::path_experiment("criterion", Path::new(p))) {
                        f.was_of(id, &e);
                    }
                }
            }
            if row.get_str("kind") == Some("criterion") {
                let id = row.get_str("id").unwrap_or("");
                let detail = row.get("detail");
                if let Some(e) = detail.and_then(|d| d.get_str("experiment")) {
                    f.of.entry(id.to_string()).or_insert_with(|| e.to_string());
                    f.was_of(id, e);
                }
                for e in detail.and_then(|d| d.get("changed_parts")).and_then(Value::as_dict).into_iter().flat_map(|c| c.keys()) {
                    f.was_of(id, e);
                }
            }
        }
        f
    }

    fn was_of(&mut self, id: &str, e: &str) {
        let all = self.ever.entry(id.to_string()).or_default();
        if !e.is_empty() && !all.iter().any(|x| x == e) {
            all.push(e.to_string());
        }
    }
}

/// The experiments whose design's files a line wrote, by `filed`: a criterion's, on a line that does not say in rb's
/// structured form what it changed, every experiment a line says it was of, as such a line may have moved it; every
/// design the line says it changed, as a criterion moved from one experiment's to another's is of both; and a gate
/// line's own, as it gives the gate's words whatever its file holds (a confirmation names the design it confirms and
/// writes none).
fn line_experiments(row: &Dict, filed: &Filed) -> Vec<String> {
    let mut exps: Vec<String> = Vec::new();
    let mut add = |e: &str| {
        if !exps.iter().any(|x| x == e) {
            exps.push(e.to_string());
        }
    };
    let says = changed_parts(row).is_some();
    for (k, i, _) in written(row) {
        match k {
            "experiment" => add(i),
            "variant" => i.split_once('/').into_iter().for_each(|(e, _)| add(e)),
            "criterion" if !says => filed.ever.get(i).into_iter().flatten().for_each(|e| add(e)),
            "criterion" => filed.of.get(i).into_iter().for_each(|e| add(e)),
            _ => (),
        }
    }
    let detail = row.get("detail");
    for e in detail.and_then(|d| d.get("changed_parts")).and_then(Value::as_dict).into_iter().flat_map(|c| c.keys()) {
        add(e);
    }
    let gate = row.get_str("op") == Some("gate") && row.get_str("kind") == Some("experiment");
    if gate && !detail.map_or(false, |d| d.get_str("design").is_some()) {
        add(row.get_str("id").unwrap_or(""));
    }
    exps
}

/// What a clone's line keeps of the parts that waited where it was cloned from (`detail.unconfirmed_parts`), each by
/// the session that wrote it.
fn waited(row: &Dict) -> Option<&Dict> {
    (row.get_str("op") == Some("clone")).then(|| row.get("detail").and_then(|d| d.get("unconfirmed_parts")).and_then(Value::as_dict)).flatten()
}

/// The designs of `exps` (`line_experiments`) a line touched, each with the session that did: those a session's line
/// wrote a part of, then, on a clone's line, each one, where a session wrote a part that waited where it was cloned from.
fn touched_in<'a>(row: &'a Dict, exps: &'a [String]) -> Vec<(&'a str, &'a str)> {
    let actor = row.get_str("actor").unwrap_or("");
    let gate = row.get_str("op") == Some("gate") && row.get_str("kind") == Some("experiment");
    let id = row.get_str("id").unwrap_or("");
    let mut out: Vec<(&str, &str)> = Vec::new();
    if !actor.is_empty() && !theirs(actor) {
        out.extend(exps.iter().filter(|e| wrote_a_part(row, e, gate && e.as_str() == id)).map(|e| (e.as_str(), actor)));
    }
    if let Some(by) = waited(row).and_then(|w| w.values().filter_map(Value::as_str).find(|by| !theirs(by))) {
        out.extend(exps.iter().map(|e| (e.as_str(), by)));
    }
    out
}

/// Whether a session touched any design (`touched_in`), as the design index reads it, from the log alone where it can
/// say: the criteria (`crits`) are read only for a line that wrote one and may have touched a design through it.
fn any_touched(log: &[Dict], crits: impl FnOnce() -> Res<Rc<Vec<Criterion>>>) -> Res<bool> {
    let unread = Filed::default();
    let mut crits = Some(crits);
    let mut filed: Option<Filed> = None;
    for row in log.iter().filter(|r| !is_session_line(r)) {
        if !touched_in(row, &line_experiments(row, &unread)).is_empty() {
            return Ok(true);
        }
        let actor = row.get_str("actor").unwrap_or("");
        let may = (!actor.is_empty() && !theirs(actor)) || waited(row).is_some();
        if may && written(row).iter().any(|(k, _, _)| *k == "criterion") {
            if filed.is_none() {
                let all = (crits.take().expect("read once"))()?;
                filed = Some(Filed::read(&all, log));
            }
            if !touched_in(row, &line_experiments(row, filed.as_ref().expect("read"))).is_empty() {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

/// What the design reading takes from the record once per state of it: which experiments a session wrote a part of,
/// the designs the user owns, and each design worked out as it is asked for.
struct Designs {
    log: Rc<Vec<Dict>>,
    exps: Rc<Vec<Experiment>>,
    crits: Rc<Vec<Criterion>>,
    words: HashMap<String, (String, String)>,
    /// each experiment a session's line wrote a part of, with the last session that did, and those lines
    touched: HashMap<String, String>,
    session_lines: HashMap<String, Vec<usize>>,
    /// each experiment's designs the user owns, in log order
    owned: HashMap<String, Vec<Owned>>,
    /// each experiment's confirmations, by log position
    confirmations: HashMap<String, Vec<usize>>,
    /// each experiment's variants a line wrote, taken back or not: how the very first confirmations name a variant's
    /// own setting
    variants: HashMap<String, Vec<String>>,
    /// who last wrote each experiment's why, where a line says
    why_by: HashMap<String, String>,
    /// what a session's line wrote of each experiment's gate (its branches, its words) and its why, as values
    session_said: HashMap<String, SessionSaid>,
    /// each design a line keeps, worked out as it is asked for, and those being worked out
    resolved: RefCell<HashMap<(String, usize), (Rc<Dict>, usize)>>,
    resolving: RefCell<HashSet<(String, usize)>>,
    done: RefCell<HashMap<String, Design>>,
    all: RefCell<Option<Rc<HashMap<String, Design>>>>,
}

/// What sessions' lines wrote of an experiment's gate and why: each part's values, by key (`gate:if_holds`,
/// `gate:if_not`, `gate_words`, `why`), and whether a line that names none may have written the gate, or the why.
#[derive(Default)]
struct SessionSaid {
    values: HashSet<(String, String)>,
    any_gate: bool,
    any_why: bool,
}

impl SessionSaid {
    /// Whether a session's line may have written this value of a part: a set test over the lines, never their order.
    fn may_have(&self, key: &str, value: &str) -> bool {
        (if key == "why" { self.any_why } else { self.any_gate }) || self.values.contains(&(key.to_string(), value.to_string()))
    }
}

impl Designs {
    /// Whether a design, as its parts read (`full`, the full hash of their structured form), is exactly one the user
    /// owns: rb's structured form kept by a line, or, for a confirmation the first builds wrote, the parts as rb says
    /// them, where `as_said` (whether rb wrote every file of it as it stands: an edit outside rb can change what rb says
    /// alike). Only a design kept under the same short hash is hashed.
    fn owns(&self, e: &Experiment, parts: &[Part], full: &str, as_said: &dyn Fn() -> bool) -> bool {
        let mut said: Option<HashMap<String, String>> = None;
        self.owned.get(&e.id).into_iter().flatten().any(|o| match &o.kept {
            Kept::Form { key } => full.starts_with(key.as_str()) && self.form(&e.id, o).map_or(false, |kept| digest(&kept) == full),
            Kept::Said(listed, whole, _) => {
                self.saw_every_session_line(&e.id, o) && self.elements(e, listed, *whole) == *said.get_or_insert_with(|| said_map(parts)) && as_said()
            }
        })
    }

    /// Whether a confirmation the first builds wrote saw every session's line that wrote a part of this design: it kept
    /// the parts as rb says them, which a session's change can leave as they were (a value's type, a source's term), so
    /// it is a design the user owns only while no such change can be in it. Lines it does not name it did not see; one
    /// that names none (group E's first build) saw none rb can rest on, since a merge or a clock puts a line anywhere in
    /// the log.
    fn saw_every_session_line(&self, experiment: &str, o: &Owned) -> bool {
        let Kept::Said(_, _, seen) = &o.kept else { return true };
        self.unseen(experiment, seen.as_ref()).next().is_none()
    }

    /// The session's lines that wrote a part of this design which a confirmation the first builds wrote did not see.
    fn unseen<'a>(&'a self, experiment: &str, seen: Option<&'a HashSet<String>>) -> impl Iterator<Item = usize> + 'a {
        let lines: &[usize] = self.session_lines.get(experiment).map_or(&[], Vec::as_slice);
        lines.iter().copied().filter(move |&p| seen.map_or(true, |saw| !saw.contains(&line_key(&self.log[p]))))
    }

    /// Whether a line keeps this design (`full`, the full hash of its structured form) as one the user owns.
    fn keeps(&self, experiment: &str, full: &str) -> bool {
        self.owned.get(experiment).into_iter().flatten().any(|o| matches!(&o.kept, Kept::Form { key } if full.starts_with(key.as_str()) && self.form(experiment, o).map_or(false, |p| digest(&p) == full)))
    }

    /// The parts a design the user owns is kept with, in rb's structured form, where a line keeps them so.
    fn form(&self, experiment: &str, o: &Owned) -> Option<Rc<Dict>> {
        matches!(o.kept, Kept::Form { .. }).then(|| self.resolve(experiment, o.pos)).flatten().map(|(d, _)| d)
    }

    /// The design a line keeps (`pos`), in rb's structured form, with how many lines of changes it rests on. A
    /// compressed snapshot names its parent by its full hash, wherever that parent occurs in the log. Earlier lines
    /// naming only a short key are usable only when all readable parents under it are the same design.
    fn resolve(&self, experiment: &str, pos: usize) -> Option<(Rc<Dict>, usize)> {
        let slot = (experiment.to_string(), pos);
        if let Some(hit) = self.resolved.borrow().get(&slot) {
            return Some(hit.clone());
        }
        if !self.resolving.borrow_mut().insert(slot.clone()) {
            return None;
        }
        let out = (|| {
            let entry = self.log.get(pos).and_then(|r| kept_entry(r, experiment))?;
            let key = owned_key(entry)?;
            let parts = entry.get("parts")?.as_dict()?;
            let (form, depth) = match entry.get("from") {
                None => (parts.clone(), 0),
                Some(named) => {
                    let from = named.as_str()?;
                    let full = match entry.get("from_full") {
                        Some(v) => Some(v.as_str()?),
                        None => None,
                    };
                    if from.len() != 7 || full.map_or(false, |f| f.len() != 64 || !f.starts_with(from)) {
                        return None;
                    }
                    let mut base: Option<(Rc<Dict>, usize, String)> = None;
                    for candidate in self.owned.get(experiment)?.iter().filter(|b| b.pos != pos && matches!(&b.kept, Kept::Form { key } if key == from)) {
                        // The recorded full hash narrows the candidates; resolve still checks it against the parts.
                        let stored = kept_entry(&self.log[candidate.pos], experiment).and_then(|e| e.get_str("full"));
                        if let (Some(wanted), Some(stored)) = (full, stored) {
                            if wanted != stored {
                                continue;
                            }
                        }
                        let Some((form, n)) = self.resolve(experiment, candidate.pos) else { continue };
                        // a base whose line keeps its full hash was checked against it as it was worked out
                        let hash = match (full, stored) {
                            (Some(wanted), Some(_)) => wanted.to_string(),
                            _ => digest(&form),
                        };
                        if full.map_or(false, |f| f != hash) {
                            continue;
                        }
                        if let Some((_, _, previous)) = &base {
                            if previous != &hash {
                                return None;
                            }
                        }
                        if base.as_ref().map_or(true, |(_, depth, _)| n < *depth) {
                            base = Some((form, n, hash));
                        }
                        if full.is_some() {
                            break;
                        }
                    }
                    let (base, n, _) = base?;
                    let mut form = (*base).clone();
                    for k in entry.get("removed").and_then(Value::as_list).into_iter().flatten().filter_map(Value::as_str) {
                        form.shift_remove(k);
                    }
                    form.extend(parts.iter().map(|(k, v)| (k.clone(), v.clone())));
                    (form, n + 1)
                }
            };
            let hash = digest(&form);
            if !hash.starts_with(&key) || entry.get("full").map_or(false, |v| v.as_str() != Some(hash.as_str())) {
                return None;
            }
            Some((Rc::new(form), depth))
        })();
        self.resolving.borrow_mut().remove(&slot);
        if let Some(hit) = &out {
            self.resolved.borrow_mut().insert(slot, hit.clone());
        }
        out
    }

    /// What a write's line keeps of the design it made (`parts`) from one the user owned (`from`, its full hash):
    /// what changed, naming that design, until CHANGES_IN_A_ROW lines require a whole snapshot again. Short keys stay
    /// in their existing fields; full hashes identify the designs even where two short keys coincide.
    fn entry_from(&self, experiment: &str, from: &str, parts: &[Part]) -> Value {
        let form = structured(parts);
        let full = digest(&form);
        let key: String = full.chars().take(7).collect();
        let base = self.owned.get(experiment).into_iter().flatten().filter(|o| matches!(&o.kept, Kept::Form { key } if from.starts_with(key.as_str())))
            .find_map(|o| self.resolve(experiment, o.pos).filter(|(d, _)| digest(d) == from));
        match base {
            Some((was, n)) if n + 1 < CHANGES_IN_A_ROW => {
                let changed: Dict = form.iter().filter(|(k, v)| was.get(k.as_str()).map_or(true, |w| !identical(w, v))).map(|(k, v)| (k.clone(), v.clone())).collect();
                let removed: Vec<String> = was.keys().filter(|k| !form.contains_key(k.as_str())).cloned().collect();
                let mut entry = crate::dict! {"key" => key, "full" => full, "from" => from.chars().take(7).collect::<String>(), "from_full" => from, "parts" => Value::Dict(changed)};
                if !removed.is_empty() {
                    entry.insert("removed".into(), Value::list_of_str(removed));
                }
                Value::Dict(entry)
            }
            _ => crate::obj! {"key" => key, "full" => full, "parts" => Value::Dict(form)},
        }
    }

    /// The keys of the parts a design a line keeps (`pos`) and the design as it reads (`now`) differ in, worked out from
    /// what the line keeps: a whole design part by part, one kept as what changed from the design it names (by its full
    /// hash) from that design's own keys and the parts the line changed, so weighing every design the user owns reads
    /// each line's parts once and hashes none. None where a line names its design by a short key alone, or names no
    /// design a line keeps. Nothing here says the design is one rb kept: `sound` asks that of the one a reading rests on.
    fn apart(&self, experiment: &str, pos: usize, now: &Reading, by_full: &HashMap<&str, usize>, memo: &mut HashMap<usize, Option<Rc<HashSet<String>>>>) -> Option<Rc<HashSet<String>>> {
        if let Some(hit) = memo.get(&pos) {
            return hit.clone();
        }
        // a line that names itself, through others, has none
        memo.insert(pos, None);
        let out = (|| {
            let entry = kept_entry(self.log.get(pos)?, experiment)?;
            owned_key(entry)?;
            let parts = entry.get("parts")?.as_dict()?;
            if entry.get("from").is_none() {
                return Some(differing(parts, now).into_iter().collect::<HashSet<String>>());
            }
            let base = *by_full.get(entry.get_str("from_full")?).filter(|b| **b != pos)?;
            let mut keys = (*self.apart(experiment, base, now, by_full, memo)?).clone();
            for k in entry.get("removed").and_then(Value::as_list).into_iter().flatten().filter_map(Value::as_str) {
                keys.remove(k);
                if now.at.contains_key(k) {
                    keys.insert(k.to_string());
                }
            }
            for (k, v) in parts {
                keys.remove(k.as_str());
                if now.at.get(k.as_str()).map_or(true, |&n| !identical(v, now.list[n].1)) {
                    keys.insert(k.clone());
                }
            }
            Some(keys)
        })()
        .map(Rc::new);
        memo.insert(pos, out.clone());
        out
    }

    /// Whether what a line keeps is a design rb kept: a form's parts hash to the key it keeps them under.
    fn sound(&self, experiment: &str, o: &Owned) -> bool {
        match &o.kept {
            Kept::Form { key } => self.form(experiment, o).map_or(false, |p| digest(&p).starts_with(key.as_str())),
            Kept::Said(..) => true,
        }
    }

    /// What a confirmation the first builds wrote kept, as the elements a design is read by now.
    fn elements(&self, e: &Experiment, listed: &[(String, String)], whole: bool) -> HashMap<String, String> {
        if whole { elements_of(listed, e, self.variants.get(&e.id)).into_iter().collect() } else { listed.iter().cloned().collect() }
    }
}

/// The parts as rb says them, by key.
fn said_map(parts: &[Part]) -> HashMap<String, String> {
    said_parts(parts).into_iter().map(|p| (p.key, p.value)).collect()
}

impl Ledger {
    /// The design reading's index of the record, built once per state of the record and of its log.
    fn design_index(&self) -> Res<Rc<Designs>> {
        let exps = self.all::<Experiment>()?;
        let crits = self.all::<Criterion>()?;
        let log = self.log_entries()?;
        if let Some(hit) = self.objs.borrow().get("design") {
            if let Ok(d) = hit.clone().downcast::<Designs>() {
                if Rc::ptr_eq(&d.log, &log) && Rc::ptr_eq(&d.exps, &exps) && Rc::ptr_eq(&d.crits, &crits) {
                    return Ok(d);
                }
            }
        }
        let mut ix = Designs {
            log: log.clone(),
            exps,
            crits: crits.clone(),
            words: self.gate_words()?,
            touched: HashMap::new(),
            session_lines: HashMap::new(),
            owned: HashMap::new(),
            confirmations: HashMap::new(),
            variants: HashMap::new(),
            why_by: HashMap::new(),
            session_said: HashMap::new(),
            resolved: RefCell::new(HashMap::new()),
            resolving: RefCell::new(HashSet::new()),
            done: RefCell::new(HashMap::new()),
            all: RefCell::new(None),
        };
        let filed = Filed::read(&crits, &log);
        for (pos, row) in log.iter().enumerate() {
            if is_session_line(row) {
                continue;
            }
            let (actor, op, kind, id) = (row.get_str("actor").unwrap_or(""), row.get_str("op").unwrap_or(""), row.get_str("kind").unwrap_or(""), row.get_str("id").unwrap_or(""));
            let detail = row.get("detail");
            let exps = line_experiments(row, &filed);
            for (k, i, _) in written(row) {
                if let ("variant", Some((e, v))) = (k, i.split_once('/')) {
                    let names = ix.variants.entry(e.to_string()).or_default();
                    if !names.iter().any(|n| n == v) {
                        names.push(v.to_string());
                    }
                }
            }
            let gate = op == "gate" && kind == "experiment";
            let confirmation = gate && detail.map_or(false, |d| d.get_str("design").is_some());
            for (e, by) in touched_in(row, &exps) {
                ix.touched.insert(e.to_string(), by.to_string());
                ix.session_lines.entry(e.to_string()).or_default().push(pos);
            }
            if theirs(actor) {
                let kept: Option<&Dict> = detail.and_then(|d| d.get("owned")).and_then(Value::as_dict);
                for (e, entry) in kept.into_iter().flatten() {
                    if let Some(key) = owned_key(entry) {
                        ix.owned.entry(e.clone()).or_default().push(Owned { pos, kept: Kept::Form { key } });
                    }
                }
                if confirmation {
                    let d = detail.unwrap();
                    // as the first builds kept it: the parts as rb said them, under the hash they name
                    if !kept.map_or(false, |k| k.contains_key(id)) {
                        let said = d.get_str("design").zip(d.get("parts").and_then(listed_parts));
                        if let Some((key, (listed, whole))) = said {
                            let mut once: HashSet<&str> = HashSet::new();
                            if said_key(&listed) == key && listed.iter().all(|(k, _)| once.insert(k.as_str())) {
                                let seen = d.get("seen").and_then(Value::as_list).map(|l| l.iter().filter_map(|x| x.as_str().map(str::to_string)).collect());
                                ix.owned.entry(id.to_string()).or_default().push(Owned { pos, kept: Kept::Said(listed, whole, seen) });
                            }
                        }
                    }
                    if ix.owned.get(id).map_or(false, |o| o.iter().any(|o| o.pos == pos)) {
                        ix.confirmations.entry(id.to_string()).or_default().push(pos);
                    }
                }
            }
            let waited = waited(row);
            // who last wrote the why: a line that says it changed it, a line that made the experiment, or a session's
            // adoption that does not say what it changed
            if kind == "experiment" && exps.iter().any(|e| e == id) && !gate {
                let wrote = |by: &str| waited.and_then(|w| w.get_str("why")).unwrap_or(by).to_string();
                match detail.and_then(|d| d.get("changed_parts")).and_then(|c| c.get(id)).and_then(Value::as_dict) {
                    Some(changed) if changed.contains_key("why") => {
                        ix.why_by.insert(id.to_string(), wrote(actor));
                    }
                    Some(_) => (),
                    None if matches!(op, "add" | "reconcile" | "clone") || (op == "adopt" && !theirs(actor)) => {
                        ix.why_by.insert(id.to_string(), wrote(actor));
                    }
                    None => (),
                }
            }
            // what a session's line wrote of the gate or the why: the values it says it changed, and the words a gate line
            // gives; a line that does not say (an older rb's that made, rebuilt or adopted the experiment's file, or a gate
            // line with no parts named) may have written any value, and so may a clone of a design that waited
            let in_session = !actor.is_empty() && !theirs(actor);
            let gate_part = |k: &str| k == "gate" || k == "gate_words" || k.starts_with("gate:");
            for e in &exps {
                let named = detail.and_then(|d| d.get("changed_parts")).and_then(|c| c.get(e)).and_then(Value::as_dict);
                let older = named.is_none() && kind == "experiment" && id == e && matches!(op, "add" | "reconcile" | "clone" | "adopt");
                if in_session && wrote_a_part(row, e, gate && e == id) {
                    let said = ix.session_said.entry(e.clone()).or_default();
                    if gate && e == id {
                        said.values.insert(("gate_words".into(), row.get_str("words").unwrap_or("").to_string()));
                        said.any_gate |= named.is_none();
                    }
                    said.any_gate |= older;
                    said.any_why |= older;
                    for (k, v) in named.into_iter().flatten() {
                        if gate_part(k) || k == "why" {
                            said.values.insert((k.clone(), v.as_str().unwrap_or("").to_string()));
                        }
                    }
                }
                for (k, _) in waited.into_iter().flatten().filter(|(_, by)| by.as_str().map_or(false, |b| !theirs(b))) {
                    let said = ix.session_said.entry(e.clone()).or_default();
                    said.any_gate |= gate_part(k);
                    said.any_why |= k == "why";
                }
            }
        }
        ix.owned = std::mem::take(&mut ix.owned).into_iter().map(|(e, o)| { let once = once_each(&log, &e, o); (e, once) }).collect();
        let ix = Rc::new(ix);
        self.objs.borrow_mut().insert("design", ix.clone() as Rc<dyn Any>);
        Ok(ix)
    }

    /// Every live experiment's design, by id, worked out once per state of the record and of its log.
    pub fn designs(&self) -> Res<Rc<HashMap<String, Design>>> {
        let ix = self.design_index()?;
        if let Some(all) = ix.all.borrow().as_ref() {
            return Ok(all.clone());
        }
        let mut map: HashMap<String, Design> = HashMap::new();
        for e in ix.exps.iter().filter(|e| e.retracted.is_none()) {
            map.insert(e.id.clone(), self.design_in(&ix, e)?);
        }
        let map = Rc::new(map);
        *ix.all.borrow_mut() = Some(map.clone());
        Ok(map)
    }

    /// One live experiment's design, worked out alone.
    pub fn design(&self, id: &str) -> Res<Option<Design>> {
        let ix = self.design_index()?;
        match ix.exps.iter().find(|e| e.id == id && e.retracted.is_none()) {
            Some(e) => Ok(Some(self.design_in(&ix, e)?)),
            None => Ok(None),
        }
    }

    fn design_in(&self, ix: &Designs, e: &Experiment) -> Res<Design> {
        if let Some(d) = ix.done.borrow().get(&e.id) {
            return Ok(d.clone());
        }
        // the files of it edited outside rb, as rb doctor lists them; none where rb cannot tell
        let edited = || -> Option<Vec<String>> {
            let (files, tampered) = (self.design_files(e).ok()?, self.tampered().ok()?);
            let of = |kind: &str, id: &str| tampered.iter().find(|t| t.get_str("kind") == Some(kind) && t.get_str("id") == Some(id)).map(|t| t.get_str("path").unwrap_or(id).to_string());
            Some(files.iter().filter_map(|(kind, id)| of(kind, id)).collect())
        };
        let d = design_of(ix, e, &edited);
        ix.done.borrow_mut().insert(e.id.clone(), d.clone());
        Ok(d)
    }

    /// The live experiments whose design waits for the user's confirmation, in the record's order.
    pub fn pending_designs(&self) -> Res<Vec<(Experiment, Design)>> {
        let designs = self.designs()?;
        Ok(self.live::<Experiment>()?.into_iter().filter_map(|e| designs.get(&e.id).filter(|d| d.pending()).cloned().map(|d| (e, d))).collect())
    }

    /// Whether something a session wrote can wait here for the user's confirmation, or has: a design a session's line
    /// wrote a part of, or a call recorded in a session. Every screen of such a record then draws its text as `shown`
    /// does, so nothing the user confirms hides from them; a record no session wrote in reads as it always has.
    pub fn shows_escapes(&self) -> Res<bool> {
        // whether a session touched a design, from the log, with the criteria only where a line rests on them: rb log
        // needs nothing else of the design index
        if any_touched(&self.log_entries()?, || self.all::<Criterion>())? {
            return Ok(true);
        }
        Ok(self.authorities()?.values().any(|a| matches!(a.state, "proposed" | "past_unconfirmed")))
    }

    /// The command that confirms an experiment's design where it waits, else nothing.
    pub fn design_command(&self, id: &str) -> String {
        self.design(id).ok().flatten().map(|d| d.waiting_command()).unwrap_or_default()
    }

    /// For a write the user is about to make to these experiments' designs: each design a session wrote some of that is,
    /// before the write, one the user owns, with its full hash. Only their writes from such a design make one they own.
    pub(crate) fn owned_before(&self, who: &Actor, experiments: &[String]) -> Res<Vec<(String, String)>> {
        if !who.is_human() || experiments.is_empty() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        for x in experiments {
            if let Some(d) = self.design(x)?.filter(|d| d.state == "confirmed") {
                out.push((x.clone(), digest(&structured(&d.parts))));
            }
        }
        Ok(out)
    }

    /// What a write's line keeps of the designs it made from ones the user owned (`detail.owned`): each design as it
    /// reads once written, where it is not the one before and no line keeps it yet; `words`, the gate's words the line
    /// gives, where it sets them.
    pub(crate) fn owned_after(&self, before: &[(String, String)], words: Option<(&str, &str)>) -> Res<Option<Value>> {
        let ix = self.design_index()?;
        let mut out = Dict::new();
        for (x, before_full) in before {
            let Ok(e) = self.load::<Experiment>(x) else { continue };
            if e.retracted.is_some() {
                continue;
            }
            let crits = self.all::<Criterion>()?;
            let criteria: Vec<&Criterion> = crits.iter().filter(|c| c.experiment == *x).collect();
            let said = match words.filter(|(id, _)| id == x) {
                Some((_, w)) => w.to_string(),
                None => self.gate_words()?.remove(x).map(|(w, _)| w).unwrap_or_default(),
            };
            let parts = parts_of(&e, &criteria, &said, &[]);
            let full = digest(&structured(&parts));
            if full != *before_full && !ix.keeps(x, &full) {
                out.insert(x.clone(), ix.entry_from(x, before_full, &parts));
            }
        }
        Ok((!out.is_empty()).then(|| Value::Dict(out)))
    }

    /// What the user's adoption of a design's files keeps of the design it made (`detail.owned`), on the line that
    /// adopts the last of them: where a session wrote some of it and the design before (`files`, each as it was before
    /// the edits; `words`, its gate's words then) is one the user owns, the design as it stands, with no words to its
    /// gate where the adoption set it. Where rb is not certain what a file was before (`form_before_adopting`: a merge, a
    /// form it holds no copy of, a write the times of the lines put last), nothing is kept, and the design waits.
    pub(crate) fn owned_by_adopting(&self, who: &Actor, experiment: &str, files: &[Before], words: &str, gate_set: bool) -> Res<Option<Value>> {
        if !who.is_human() || files.iter().any(|(_, _, before)| before.is_none()) {
            return Ok(None);
        }
        let ix = self.design_index()?;
        let Some(e) = ix.exps.iter().find(|e| e.id == experiment && e.retracted.is_none()) else { return Ok(None) };
        if !ix.touched.contains_key(experiment) {
            return Ok(None);
        }
        let criteria: Vec<&Criterion> = ix.crits.iter().filter(|c| c.experiment == experiment).collect();
        let swaps: Vec<Swap> = files.iter().map(|(k, i, before)| (k.as_str(), i.as_str(), before.as_ref().and_then(Option::as_ref))).collect();
        let was = parts_of(e, &criteria, words, &swaps);
        let before_full = digest(&structured(&was));
        if !ix.owns(e, &was, &before_full, &|| true) {
            return Ok(None);
        }
        let now = parts_of(e, &criteria, if gate_set { "" } else { ix.words.get(experiment).map(|(w, _)| w.as_str()).unwrap_or("") }, &[]);
        let full = digest(&structured(&now));
        Ok((full != before_full && !ix.keeps(experiment, &full)).then(|| crate::obj! {experiment => ix.entry_from(experiment, &before_full, &now)}))
    }

    /// The user's confirmation of an experiment's design as it was listed (`key`), with its gate, by the gate's
    /// mechanism: one log line that names what it confirms and keeps the design as listed, which changes no file. A
    /// design changed after it was listed is refused, with what it reads now and the command that confirms that.
    /// Returns the design as it reads once confirmed.
    pub fn confirm_design(&self, id: &str, key: &str) -> Res<Design> {
        let who = self.actor()?;
        if !who.is_human() {
            return Err(RBError::msg("E_USAGE", "rb confirm makes a design the user's own: it runs only in their own terminal."));
        }
        self.locked(|| {
            let exp: Experiment = self.load(id)?;
            if exp.retracted.is_some() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{id} was retracted: there is nothing to confirm.")));
            }
            let Some(d) = self.design(id)?.filter(Design::pending) else {
                return Err(RBError::msg_fix("E_OBJECT_INVALID", format!("{id}'s design waits for no confirmation."), "rb confirm lists what waits."));
            };
            if key != d.confirmation_key() {
                // a key an earlier rb listed for the gate alone names the gate as it still reads: it waits with its design
                let words = self.gate_words()?.remove(id).map(|(w, _)| w).unwrap_or_default();
                let gate_key = (!(exp.if_holds.is_empty() && exp.if_not.is_empty())).then(|| crate::view::gate_key(&exp.if_holds, &exp.if_not, &words));
                let earlier = said_key(&said_parts(&d.parts).into_iter().map(|p| (p.key, p.value)).collect::<Vec<_>>());
                let said = if key == d.key {
                    format!("{key} is a short design key and can name more than one design. List {id} again to confirm it. It reads now:")
                } else if gate_key.as_deref() == Some(key) {
                    format!("{id}'s gate waits with its design now, and is confirmed with it. It reads now:")
                } else if earlier == key {
                    // the hash an earlier rb named a design by, over how rb says it, which a change rb says alike leaves
                    format!("{key} is a key an earlier rb listed, and rb cannot tell from it whether {id}'s design changed since. It reads now:")
                } else {
                    format!("{id}'s design does not match {key}: it changed after it was listed, or rb never listed that key. It reads now:")
                };
                return Err(RBError::msg_fix("E_OBJECT_INVALID", said, format!("To confirm it as it reads now: {}", d.confirm())).with_problems(self.design_said(&exp, &d)?));
            }
            // a file of it edited outside rb holds what rb did not write, which nobody can confirm as theirs
            for (kind, fid) in &self.design_files(&exp)? {
                if self.outside_rb(kind, fid)? {
                    return Err(RBError::msg_fix("E_STATE_EDITED", format!("{fid} was changed outside rb, and a design rb did not write is nobody's."), "rb doctor"));
                }
            }
            // what it keeps is what the key names, in rb's structured form; and, for an earlier rb, the parts as rb says
            // them under the hash it names them by
            let said: Vec<(String, String)> = said_parts(&d.parts).into_iter().map(|p| (p.key, p.value)).collect();
            let kept = Value::List(said.iter().map(|(k, v)| Value::list_of_str([k.clone(), v.clone()])).collect());
            // an older rb reads it as the gate's confirmation, which it also is
            let confirms = if exp.if_holds.is_empty() && exp.if_not.is_empty() { "design" } else { "gate" };
            self.log(
                &who,
                "gate",
                "experiment",
                id,
                None,
                None,
                crate::dict! {
                    "confirms" => confirms, "design" => said_key(&said), "parts" => kept, "if_holds" => exp.if_holds.clone(), "if_not" => exp.if_not.clone(),
                    "owned" => crate::obj! {id => owned_entry(&d.parts)}
                },
            )?;
            Ok(self.design(id)?.unwrap_or(d))
        })
    }

    /// The files an experiment's design is in: its own, its live variants', its live criteria'.
    fn design_files(&self, exp: &Experiment) -> Res<Vec<(&'static str, String)>> {
        let mut files: Vec<(&'static str, String)> = vec![("experiment", exp.id.clone())];
        files.extend(exp.live_variants().iter().map(|v| ("variant", format!("{}/{}", exp.id, v.name))));
        files.extend(self.criteria_of(&exp.id)?.iter().filter(|c| c.retracted.is_none()).map(|c| ("criterion", c.id.clone())));
        Ok(files)
    }

    /// The design as rb confirm lists it, part by part and briefly, as lines: what it rests on, in the record's
    /// words, the words given with it as given, and what a design a session wrote some of does not record.
    pub fn design_said(&self, exp: &Experiment, d: &Design) -> Res<Vec<String>> {
        let mut out: Vec<String> = Vec::new();
        let part = |key: &str| d.parts.iter().find(|p| p.key == key).map(|p| p.value.clone());
        if !exp.questions.is_empty() {
            out.push(format!("questions: {}", exp.questions.join(", ")));
        }
        for k in ["hypothesis", "purpose"] {
            if let Some(t) = part(k) {
                out.push(format!("{k}: {}", listed_text(&t)));
            }
        }
        if d.not_recorded.contains(&"hypothesis or purpose") {
            out.push("hypothesis or purpose: not recorded".into());
        }
        if let Some((label, text)) = crate::view::why_part(&listed_text(&exp.why), &self.why_by(exp, d)) {
            out.push(format!("{label}: {text}"));
        } else if d.not_recorded.contains(&"why") {
            out.push("why: not recorded".into());
        }
        let variants: Vec<String> = exp.live_variants().iter().map(|v| format!("{} ({})", v.name, v.role)).collect();
        if !variants.is_empty() || !exp.varies.is_empty() {
            let mut line = if variants.is_empty() { "no variants".to_string() } else { format!("variants: {}", variants.join(", ")) };
            if !exp.varies.is_empty() {
                line.push_str(&format!(" · varies {}", exp.varies.join(", ")));
            }
            out.push(line);
        }
        for (full, s) in exp.all_settings() {
            out.push(format!("setting {full}: {}", setting_value(s)) + &s.source.as_ref().map(|src| format!(" from {}", listed_source(src))).unwrap_or_default());
        }
        for c in self.criteria_of(&exp.id)?.iter().filter(|c| c.retracted.is_none()) {
            // each question it bears on, with what meeting it would mean there where that is recorded
            let links: String = c.active_links().iter().map(|l| link_said(l)).collect();
            out.push(format!("criterion {} ({}): {}{links}", c.id, c.role, listed_rule(c)));
        }
        let (words, by) = self.gate_words()?.remove(&exp.id).unwrap_or_default();
        let gate = listed_gate(&exp.if_holds, &exp.if_not, &words, &d.writer(&by, "gate"));
        if !gate.is_empty() {
            out.push(format!("gate: {gate}"));
        }
        // the freeze and each amendment with the reason given, as rb show says them: what a confirmation covers
        if let Some(f) = &exp.frozen {
            out.push(format!("frozen {}", f.at) + &crate::view::reason_part(&listed_text(&f.why), &d.writer(&f.by, "freeze_why"), ": "));
        }
        for (i, a) in exp.amendments.iter().enumerate() {
            let day: String = a.at.chars().take(10).collect();
            let by = d.writer(&a.by, &format!("amendment_why:{}", i + 1));
            out.push(format!("amended {day}: {}", listed_text(&a.change)) + &crate::view::reason_part(&listed_text(&a.why), &by, " · because: "));
        }
        out.extend(d.change_lines());
        // what is left of the record's words, ids and roles among them, shows as it is held, never acted on
        Ok(out.iter().map(|l| visible(l)).collect())
    }

    /// Who an experiment's why reads as written by on a screen: whoever last wrote the why, as the lines that say so
    /// name them (whoever made the experiment, where none says), and a session's why that reads as in a design the user
    /// owns reads as its note, with no `· not confirmed` (`view::why_part` on what this gives).
    pub fn why_by(&self, exp: &Experiment, d: &Design) -> String {
        d.writer(if d.why_writer.is_empty() { &exp.created_by } else { &d.why_writer }, "why")
    }
}

/// A design as it reads now, and its standing: whether a session's line wrote a part of it, and whether it is exactly a
/// design the user owns; where it is not and they own one, how it differs from the one closest to it (the fewest parts
/// apart, the latest in the log of those). Only then is it compared part by part.
fn design_of(ix: &Designs, e: &Experiment, edited: &dyn Fn() -> Option<Vec<String>>) -> Design {
    let as_said = &|| edited().map_or(false, |files| files.is_empty());
    let criteria: Vec<&Criterion> = ix.crits.iter().filter(|c| c.experiment == e.id).collect();
    let words = ix.words.get(&e.id).map_or("", |(w, _)| w.as_str());
    let parts = parts_of(e, &criteria, words, &[]);
    let full = digest(&structured(&parts));
    let mut d = Design { experiment: e.id.clone(), key: full.chars().take(7).collect(), confirmation_key: full.clone(), state: "yours", ..Default::default() };
    d.why_writer = ix.why_by.get(&e.id).cloned().unwrap_or_default();
    // a gate or a why reads in a session's voice where a session's line may have written it as it reads now
    if let Some(said) = ix.session_said.get(&e.id) {
        let now = |key: &str| parts.iter().find(|p| p.key == key).map(|p| p.value.as_str());
        d.session_gate = ["gate:if_holds", "gate:if_not", "gate_words"].iter().any(|k| now(k).map_or(false, |v| said.may_have(k, v)));
        d.session_why = now("why").map_or(false, |v| said.may_have("why", v));
    }
    let Some(session) = ix.touched.get(&e.id) else {
        // one a session made with no design of its own (an import's) records neither, as its views say
        if made_in_a_session(e) {
            d.not_recorded = not_recorded(e);
        }
        d.parts = parts;
        return d;
    };
    d.session = session.clone();
    d.not_recorded = not_recorded(e);
    d.confirmed = ix.confirmations.get(&e.id).and_then(|c| c.last()).map(|&pos| {
        let r = &ix.log[pos];
        (r.get_str("at").unwrap_or("").to_string(), r.get_str("actor").unwrap_or("").to_string())
    });
    let shown = said_parts(&parts);
    let owned: &[Owned] = ix.owned.get(&e.id).map_or(&[], Vec::as_slice);
    if ix.owns(e, &parts, &full, as_said) {
        d.state = "confirmed";
        d.covered = parts.iter().map(|p| p.key.clone()).collect();
    } else if !owned.is_empty() {
        // each design the user owns, by how many parts it differs from this one; ties to the latest in the log
        let now = Reading::of(parts.iter().map(|p| (p.key.as_str(), &p.raw, p.value.as_str())));
        let said: Vec<(&str, Value, &str)> = shown.iter().map(|p| (p.key.as_str(), Value::Str(p.value.clone()), p.value.as_str())).collect();
        let now_said = Reading::of(said.iter().map(|(k, v, s)| (*k, v, *s)));
        // against a design kept as rb says it, part by part, with a part a session's line it did not see changed said bare
        let said_against = |o: &Owned| -> Vec<Change> {
            match &o.kept {
                Kept::Form { .. } => Vec::new(),
                Kept::Said(listed, whole, seen) => {
                    // one that names no line it saw cannot say what changed since it: any session's line may be after it
                    let Some(saw) = seen else { return Vec::new() };
                    let kept: Vec<(String, Value)> = (if *whole { elements_of(listed, e, ix.variants.get(&e.id)) } else { listed.clone() }).into_iter().map(|(k, v)| (k, Value::Str(v))).collect();
                    let mut elements = differences(kept.iter().map(|(k, v)| (k.as_str(), v)), &now_said, |a, b| a.as_str() == b.as_str(), |_, v| v.as_str().unwrap_or("").to_string());
                    // a source said as it reads now with what its short form leaves out (its term, a long quote, a line)
                    for c in elements.iter_mut().filter(|c| c.key.starts_with("source:")) {
                        if let (Some(n), Some(p)) = (c.now.as_deref(), parts.iter().find(|p| p.key == c.key)) {
                            c.now = Some(said_apart(&c.key, &p.raw, n, true).unwrap_or_else(|| n.to_string()));
                        }
                    }
                    // each part a session's line it did not see names, where rb says it alike: changed, was as it
                    // was listed, and now with what rb's words for it leave out; the files such a line wrote, where
                    // it names none of them rb can say, or where an earlier rb wrote it
                    let mut written_files: Vec<String> = Vec::new();
                    for p in ix.unseen(&e.id, Some(saw)) {
                        let row = &ix.log[p];
                        let named = row.get("detail").and_then(|d| d.get("changed_parts")).and_then(|c| c.get(&e.id)).and_then(Value::as_dict);
                        // a line compared in rb's structured form names what it changed (a source only where it
                        // changed); an earlier rb's line names what it changed in rb's words, which say alike what the
                        // listing could not show (a value's type, a source's term), so it says nothing rb can rest on
                        // of a part it names that reads alike, nor of one it does not name: the files it wrote are named
                        let structured = changed_parts(row).is_some();
                        let mut said = false;
                        for k in named.into_iter().flat_map(|c| c.keys()).filter(|_| structured) {
                            if !(now_said.at.contains_key(k.as_str()) || kept.iter().any(|(x, _)| x == k)) {
                                continue;
                            }
                            if elements.iter().any(|c| c.key == *k) {
                                said = true;
                                continue;
                            }
                            let was = kept.iter().find(|(x, _)| x == k).and_then(|(_, v)| v.as_str()).map(str::to_string);
                            let now = parts.iter().find(|p| p.key == *k).and_then(|p| said_apart(k, &p.raw, &p.value, false));
                            match (was, now) {
                                (Some(was), Some(now)) => elements.push(Change { key: k.clone(), change: "changed", was: Some(was), now: Some(now) }),
                                // a change rb can name and not say
                                _ => elements.push(Change { key: k.clone(), change: "changed", was: None, now: None }),
                            }
                            said = true;
                        }
                        if !said || !structured {
                            for (kind, id, path) in written(row) {
                                let of = match kind {
                                    "experiment" => id == e.id,
                                    "variant" => id.split_once('/').map_or(false, |(x, _)| x == e.id),
                                    _ => kind == "criterion",
                                };
                                if let Some(path) = path.filter(|p| of && !p.is_empty() && !written_files.iter().any(|w| w == p)) {
                                    written_files.push(path.to_string());
                                }
                            }
                        }
                    }
                    // the files written since that rb names no part of or an earlier rb wrote, and every file edited
                    // outside rb, which says the most of one
                    let edited_files = edited().unwrap_or_default();
                    let as_before = elements.is_empty();
                    elements.extend(written_files.into_iter().filter(|f| !edited_files.contains(f)).map(|f| Change { key: format!("{}:{f}", if as_before { "written" } else { "rewritten" }), change: "changed", was: None, now: None }));
                    elements.extend(edited_files.into_iter().map(|f| Change { key: format!("edited:{f}"), change: "changed", was: None, now: None }));
                    elements
                }
            }
        };
        // weighed by the parts that differ, then said in full against the closest alone
        // where each design a line keeps whole or as what changed is, by its full hash: the first line that keeps it
        let mut by_full: HashMap<&str, usize> = HashMap::new();
        for o in owned.iter().filter(|o| matches!(o.kept, Kept::Form { .. })) {
            if let Some(full) = kept_entry(&ix.log[o.pos], &e.id).and_then(|x| x.get_str("full")) {
                by_full.entry(full).or_insert(o.pos);
            }
        }
        let mut memo = HashMap::new();
        let mut each: Vec<(usize, usize)> = owned
            .iter()
            .enumerate()
            .filter_map(|(n, o)| {
                let keys: Vec<String> = match &o.kept {
                    Kept::Form { .. } => match ix.apart(&e.id, o.pos, &now, &by_full, &mut memo) {
                        Some(keys) => keys.iter().cloned().collect(),
                        None => differing(ix.form(&e.id, o)?.as_ref(), &now),
                    },
                    // one that says no difference tells nothing of what changed: the design is not said against it
                    Kept::Said(..) => Some(said_against(o).into_iter().map(|c| c.key).collect::<Vec<_>>()).filter(|k| !k.is_empty())?,
                };
                Some((weight_of(&keys), n))
            })
            .collect();
        each.sort_by_key(|(w, n)| (*w, std::cmp::Reverse(*n)));
        let closest = each.into_iter().map(|(_, n)| &owned[n]).find(|o| ix.sound(&e.id, o)).and_then(|o| match &o.kept {
            Kept::Form { .. } => ix.form(&e.id, o).map(|kept| (differences(kept.iter().map(|(k, v)| (k.as_str(), v)), &now, identical, said_of), false)),
            Kept::Said(..) => Some((said_against(o), true)),
        });
        match closest {
            Some((mut elements, as_said)) => {
                d.state = "changed";
                // in the order rb lists the parts: by kind, a setting's or a criterion's parts together, then as listed
                let order: Vec<&str> = parts.iter().map(|p| p.key.as_str()).collect();
                let named = |k: &str| -> String {
                    let (kind, rest) = k.split_once(':').unwrap_or((k, ""));
                    match kind {
                        "setting" | "source" | "optional" | "factor" | "cited" => format!("setting:{rest}"),
                        "rule" | "role" | "link" => format!("criterion:{}", rest.split(':').next().unwrap_or("")),
                        _ => k.to_string(),
                    }
                };
                let first = |k: &str| order.iter().position(|o| named(o) == named(k)).unwrap_or(usize::MAX);
                // a part no longer there comes with the rest of its own, in the order rb lists a part's elements
                let within = |k: &str| ["rule", "role", "link", "setting", "source", "optional", "factor", "cited", "freeze", "freeze_why", "amendment", "amendment_why"]
                    .iter()
                    .position(|x| k.split(':').next() == Some(*x))
                    .unwrap_or(0);
                elements.sort_by_key(|c| (rank_of(&c.key), first(&c.key), named(&c.key), order.iter().position(|o| *o == c.key).unwrap_or(usize::MAX), within(&c.key)));
                d.covered = parts.iter().filter(|p| !elements.iter().any(|c| c.key == p.key)).map(|p| p.key.clone()).collect();
                d.waits = elements.iter().map(|c| c.key.clone()).collect();
                d.changes = grouped_changes(&elements, if as_said { &shown } else { &parts });
            }
            None => d.state = "not_confirmed",
        }
    } else {
        d.state = "not_confirmed";
    }
    if d.state == "not_confirmed" {
        d.waits = shown.iter().map(|p| p.key.clone()).collect();
    }
    let gated = !(e.if_holds.is_empty() && e.if_not.is_empty());
    let gate_covered = parts.iter().filter(|p| p.key.starts_with("gate:") || p.key == "gate_words").all(|p| d.covered.contains(&p.key));
    d.gate_waits = d.pending() && gated && d.session_gate && !gate_covered;
    if let Some(said) = ix.session_said.get(&e.id) {
        let sessions = |key: &str| parts.iter().find(|p| p.key == key).map_or(false, |p| said.may_have(key, &p.value) && !d.covered.contains(key));
        d.gate_first = sessions("gate:if_holds") && !sessions("gate:if_not") && !sessions("gate_words");
    }
    d.parts = parts;
    d
}

/// How many parts differences come to, as a design's lines say them: the questions it is filed under, what it varies,
/// its gate with the words given with it, a setting with its marks and source, a criterion (with each question it bears
/// on, where it came or went whole), the freeze and an amendment with their reasons, each one.
fn weight_of(keys: &[String]) -> usize {
    let rules: HashSet<&str> = keys.iter().filter(|k| k.starts_with("rule:")).map(String::as_str).collect();
    let whole = |key: &str| -> String {
        let (kind, rest) = key.split_once(':').unwrap_or((key, ""));
        let first = rest.split(':').next().unwrap_or("");
        match kind {
            "question" => "questions".into(),
            "varies" => "varies".into(),
            "gate" | "gate_words" => "gate".into(),
            "setting" | "source" | "optional" | "factor" | "cited" => format!("setting:{rest}"),
            "rule" | "role" => format!("criterion:{first}"),
            "link" if rules.contains(format!("rule:{first}").as_str()) => format!("criterion:{first}"),
            "freeze" | "freeze_why" => "freeze".into(),
            "amendment" | "amendment_why" => format!("amendment:{rest}"),
            _ => key.to_string(),
        }
    };
    keys.iter().map(|k| whole(k)).collect::<HashSet<String>>().len()
}

/// The keys of the parts a design the user owns (`kept`, in rb's structured form) and one as it reads (`now`) differ in.
fn differing(kept: &Dict, now: &Reading) -> Vec<String> {
    let mut out: Vec<String> = kept.iter().filter(|(k, v)| now.at.get(k.as_str()).map_or(true, |&n| !identical(v, now.list[n].1))).map(|(k, _)| k.clone()).collect();
    out.extend(now.list.iter().filter(|(k, _, _)| !kept.contains_key(*k)).map(|(k, _, _)| k.to_string()));
    out
}

/// A design's parts as it reads, to compare a design the user owns with: each part's key, its value to compare, and
/// what rb says of it, in the order listed, with where each key is.
struct Reading<'a> {
    list: Vec<(&'a str, &'a Value, &'a str)>,
    at: HashMap<&'a str, usize>,
}

impl<'a> Reading<'a> {
    fn of(parts: impl Iterator<Item = (&'a str, &'a Value, &'a str)>) -> Self {
        let list: Vec<(&str, &Value, &str)> = parts.collect();
        let at = list.iter().enumerate().map(|(n, (k, _, _))| (*k, n)).collect();
        Reading { list, at }
    }
}

/// How a design the user owns (`kept`) differs from one as it reads (`now`), part by part: each part changed, added or
/// removed, with what it read there and reads now as rb says them. A part is compared by its value (`same`); what it
/// read there is said only where it differs (`say`), and what it reads now as rb says it; where rb says the two alike,
/// with what tells them apart after each.
fn differences<'k>(kept: impl Iterator<Item = (&'k str, &'k Value)>, now: &Reading, same: impl Fn(&Value, &Value) -> bool, say: impl Fn(&str, &Value) -> String) -> Vec<Change> {
    let mut out: Vec<Change> = Vec::new();
    let mut had: HashSet<&str> = HashSet::new();
    for (k, v) in kept {
        had.insert(k);
        match now.at.get(k).map(|&n| now.list[n]) {
            Some((_, n, _)) if same(v, n) => (),
            Some((_, n, said)) => {
                let was = say(k, v);
                let (was, is) = if was == said { told_apart(v, n, said) } else { (was, said.to_string()) };
                out.push(Change { key: k.to_string(), change: "changed", was: Some(was), now: Some(is) })
            }
            None => out.push(Change { key: k.to_string(), change: "removed", was: Some(say(k, v)), now: None }),
        }
    }
    for (k, _, said) in &now.list {
        if !had.contains(k) {
            out.push(Change { key: k.to_string(), change: "added", was: None, now: Some(said.to_string()) });
        }
    }
    out
}

/// A part as it reads now, with what rb's words for it leave out after them (`3 (text)`, `docs/train.md:3 (term
/// "rate")`, a rule's `(where seed "1")`): how a change rb says alike is said against a design kept only in those
/// words. None where nothing is left out. `only_told`: only what rb's words would not read as (a value's kind where
/// its words read as another, a source's term, quote or line), for a part a line may have written as it was.
fn said_apart(key: &str, raw: &Value, said: &str, only_told: bool) -> Option<String> {
    let kind = |v: &Value| -> &'static str {
        match v {
            Value::Str(_) => "text",
            Value::Int(_) | Value::BigInt(_) | Value::Float(_) => "a number",
            Value::Bool(_) => "true or false",
            Value::List(_) => "a list",
            Value::Dict(_) => "a map",
            Value::Null => "none",
        }
    };
    // a value's kind where its words read as a value of another (`3` as text), as a value typed to rb reads
    let apart = |v: &Value| -> Option<&'static str> {
        let words = crate::pyjson::loads(&show(v)).ok().filter(|w| !matches!(w, Value::Null | Value::Dict(_))).unwrap_or_else(|| Value::Str(show(v)));
        (!only_told || kind(&words) != kind(v)).then(|| kind(v))
    };
    let source = |v: &Value| -> Option<Vec<String>> {
        let s = Source::validate(&Value::Dict(v.as_dict()?.clone()), &mut Vec::new(), &mut Vec::new())?;
        let mut out = source_extras(&s);
        if !only_told && s.term.as_deref().map_or(true, str::is_empty) && s.kind == "file" {
            out.push("no term".into());
        }
        (!out.is_empty()).then_some(out)
    };
    let field = |v: &Value| match v {
        Value::Str(t) => quoted(t),
        other => canonical(other),
    };
    let told: Vec<String> = match key.split(':').next().unwrap_or("") {
        "setting" => apart(raw.get("value")?).map(str::to_string).into_iter().collect(),
        "source" => source(raw).unwrap_or_default(),
        "cited" => apart(raw.get("value")?).map(|k| format!("value {k}")).into_iter().chain(raw.get("source").and_then(source).into_iter().flatten()).collect(),
        "rule" => [("where", "where"), ("subject_where", "subject where"), ("reference_where", "reference where")]
            .iter()
            .flat_map(|(f, label)| raw.get(f).and_then(Value::as_dict).into_iter().flatten().filter(|(_, v)| apart(v).is_some()).map(move |(k, v)| format!("{label} {k} {}", field(v))))
            .collect(),
        _ => Vec::new(),
    };
    (!told.is_empty()).then(|| format!("{said} ({})", told.join("; ")))
}

/// Two values rb says alike (`said`), told apart: each with what differs in its structured form after it, a field by
/// its name and value (a source's term, a rule's noise), or the kind of a value (`3 (a number)`, `3 (text)`).
fn told_apart(was: &Value, now: &Value, said: &str) -> (String, String) {
    let (a, b) = what_differs(was, now);
    (format!("{said} ({a})"), format!("{said} ({b})"))
}

fn what_differs(was: &Value, now: &Value) -> (String, String) {
    let kind = |v: &Value| -> String {
        match v {
            Value::Str(_) => "text",
            Value::Int(_) | Value::BigInt(_) | Value::Float(_) => "a number",
            Value::Bool(_) => "true or false",
            Value::List(_) => "a list",
            Value::Dict(_) => "a map",
            Value::Null => "none",
        }
        .to_string()
    };
    let field = |v: &Value| match v {
        Value::Str(t) => quoted(t),
        other => canonical(other),
    };
    // each field that differs, by its path (`source term`), with its value on each side
    fn apart(x: &Dict, y: &Dict, at: &str, field: &dyn Fn(&Value) -> String, out: &mut (Vec<String>, Vec<String>)) {
        let mut fields: Vec<&String> = x.keys().chain(y.keys()).collect();
        fields.sort();
        fields.dedup();
        for f in fields {
            let name = if at.is_empty() { f.clone() } else { format!("{at} {f}") };
            match (x.get(f), y.get(f)) {
                (Some(a), Some(b)) if identical(a, b) => (),
                (Some(Value::Dict(a)), Some(Value::Dict(b))) => apart(a, b, &name, field, out),
                (a, b) => {
                    out.0.push(format!("{name} {}", a.map_or("none".to_string(), field)));
                    out.1.push(format!("{name} {}", b.map_or("none".to_string(), field)));
                }
            }
        }
    }
    match (was, now) {
        (Value::Dict(x), Value::Dict(y)) => {
            if let (Some(a), Some(b)) = (x.get("value"), y.get("value")) {
                let rest = |d: &Dict| d.iter().filter(|(k, _)| *k != "value").map(|(k, v)| (k.clone(), v.clone())).collect::<Dict>();
                if std::mem::discriminant(a) != std::mem::discriminant(b) && same_dict(&rest(x), &rest(y)) {
                    return (kind(a), kind(b));
                }
            }
            let mut out = (Vec::new(), Vec::new());
            apart(x, y, "", &field, &mut out);
            (out.0.join("; "), out.1.join("; "))
        }
        _ if std::mem::discriminant(was) != std::mem::discriminant(now) => (kind(was), kind(now)),
        // text that reads alike, told apart as it is held: a line break as `\n`
        _ => (was.as_str().map_or_else(|| canonical(was), visible), now.as_str().map_or_else(|| canonical(now), visible)),
    }
}

/// Whether a session made the experiment: rb wrote it for one (an older record that keeps no writer is the user's).
fn made_in_a_session(e: &Experiment) -> bool {
    !crate::view::not_confirmed(&e.created_by).is_empty()
}

/// What an experiment does not record of what a design says: its hypothesis or purpose, its why.
fn not_recorded(e: &Experiment) -> Vec<&'static str> {
    let mut out = Vec::new();
    if e.hypothesis.trim().is_empty() && e.purpose.trim().is_empty() {
        out.push("hypothesis or purpose");
    }
    if e.why.trim().is_empty() {
        out.push("why");
    }
    out
}

/// Whether a write was the user's own: made outside an agent session, as rb wrote it for them.
fn theirs(by: &str) -> bool {
    crate::view::words_are_theirs(by)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn change(key: &str, change: &'static str, was: Option<&str>, now: Option<&str>) -> Change {
        Change { key: key.into(), change, was: was.map(str::to_string), now: now.map(str::to_string) }
    }

    #[test]
    fn a_change_says_what_it_was_and_is_where_the_record_keeps_both() {
        assert_eq!(
            change("rule:acc-tol", "changed", Some("change.top1 ≥ -0.5"), Some("change.top1 ≥ -0.3")).said(),
            "changed since you confirmed it: criterion acc-tol's rule, was change.top1 ≥ -0.5, now change.top1 ≥ -0.3"
        );
        assert_eq!(change("setting:optim.lr", "changed", None, None).said(), "changed since you confirmed it: setting optim.lr");
        assert_eq!(change("gate_words", "removed", Some("ship it"), None).said(), "changed since you confirmed it: the gate's words (as given) removed, was \"ship it\"");
        // words are quoted, and so is a value that holds a comma or a `·`, so where was ends is plain
        assert_eq!(
            change("questions", "changed", Some("q-acc"), Some("q-acc, q-lat")).said(),
            "changed since you confirmed it: questions, was q-acc, now \"q-acc, q-lat\""
        );
        assert_eq!(
            change("gate", "changed", Some("if it holds: ship INT8 · if not: keep FP32"), Some("if it holds: ship INT8 next week · if not: keep FP32")).said(),
            "changed since you confirmed it: the gate, was \"if it holds: ship INT8 · if not: keep FP32\", now \"if it holds: ship INT8 next week · if not: keep FP32\""
        );
        assert_eq!(change("varies", "changed", Some("precision"), Some("precision, optim.lr")).said(), "changed since you confirmed it: what it varies, was precision, now \"precision, optim.lr\"");
        assert_eq!(change("hypothesis", "changed", Some("INT8 keeps top-1"), Some("INT8 loses top-1")).said(), "changed since you confirmed it: hypothesis, was \"INT8 keeps top-1\", now \"INT8 loses top-1\"");
        assert_eq!(change("variant:int8-pc", "added", None, Some("ablation")).said(), "changed since you confirmed it: variant int8-pc added as ablation");
        assert_eq!(change("link:c1:q-lat", "added", None, Some("when met, \"INT8 is fast enough\"")).said(), "changed since you confirmed it: criterion c1 filed under q-lat, when met \"INT8 is fast enough\"");
        assert_eq!(change("link:c1:q-acc", "added", None, Some("no interpretation")).said(), "changed since you confirmed it: criterion c1 filed under q-acc");
        assert_eq!(change("link:c1:q-acc", "removed", Some("no interpretation"), None).said(), "changed since you confirmed it: criterion c1 no longer filed under q-acc");
        assert_eq!(
            change("link:c1:q-acc", "changed", Some("no interpretation"), Some("when met, \"INT8 is enough\"")).said(),
            "changed since you confirmed it: what meeting c1 would mean for q-acc, was no interpretation, now \"INT8 is enough\""
        );
        assert_eq!(change("freeze", "added", None, Some("frozen 2026-10-13")).said(), "changed since you confirmed it: frozen 2026-10-13");
    }

    #[test]
    fn a_part_added_or_taken_back_whole_is_one_line() {
        let d = Design {
            experiment: "int8".into(),
            state: "changed",
            changes: vec![
                change("rule:c9", "added", None, Some("int8.top1 ≥ 0.75")),
                change("role:c9", "added", None, Some("primary")),
                change("link:c9:q-acc", "added", None, Some("when met, \"INT8 is good\"")),
                change("setting:seed", "removed", Some("3"), None),
                change("source:seed", "removed", Some("configs/train.yaml#seed"), None),
                change("rule:acc-tol", "changed", Some("a"), Some("b")),
                change("role:acc-tol", "added", None, Some("gate")),
                change("rule:c2", "removed", Some("c2 ≥ 1"), None),
                change("role:c2", "removed", Some("primary"), None),
                change("link:c2:q-acc", "removed", Some("no interpretation"), None),
                change("freeze", "added", None, Some("frozen 2026-10-14")),
                change("freeze_why", "added", None, Some("the plan locks it")),
                change("amendment:2", "added", None, Some("setting seed: now a fixed design factor")),
                change("amendment_why:2", "added", None, Some("seeds are fixed cells")),
            ],
            ..Default::default()
        };
        assert_eq!(
            d.change_lines(),
            vec![
                "changed since you confirmed it: criterion c9 added: int8.top1 ≥ 0.75 (primary)",
                "changed since you confirmed it: criterion c9 filed under q-acc, when met \"INT8 is good\"",
                "changed since you confirmed it: setting seed removed, was 3 from configs/train.yaml#seed",
                "changed since you confirmed it: criterion acc-tol's rule, was a, now b",
                "changed since you confirmed it: criterion acc-tol's role added: gate",
                "changed since you confirmed it: criterion c2 removed, was c2 ≥ 1 (primary)",
                "changed since you confirmed it: frozen 2026-10-14 · note: the plan locks it",
                "changed since you confirmed it: amended: setting seed: now a fixed design factor · note: seeds are fixed cells",
            ]
        );
        // a setting's value and source an amendment says as they changed are said once, by the amendment
        let d = Design {
            experiment: "int8".into(),
            state: "changed",
            changes: vec![
                change("setting:optim.lr", "changed", Some("0.001"), Some("0.003")),
                change("source:optim.lr", "changed", Some("configs/a.yaml#optim.lr"), Some("configs/b.yaml#optim.lr")),
                change("setting:optim.wd", "changed", Some("0.1"), Some("0.2")),
                change("amendment:1", "added", None, Some("setting optim.lr: 0.001 -> 0.003; source configs/a.yaml#optim.lr -> configs/b.yaml#optim.lr")),
                change("amendment_why:1", "added", None, Some("the warmup was too short")),
                change("amendment:2", "added", None, Some("setting optim.wd: 0.15 -> 0.2")),
            ],
            ..Default::default()
        };
        assert_eq!(
            d.change_lines(),
            vec![
                "changed since you confirmed it: setting optim.wd, was 0.1, now 0.2",
                "changed since you confirmed it: amended: setting optim.lr: 0.001 -> 0.003; source configs/a.yaml#optim.lr -> configs/b.yaml#optim.lr · note: the warmup was too short",
                "changed since you confirmed it: amended: setting optim.wd: 0.15 -> 0.2",
            ]
        );
        // recheck of group E, round 3: a source an amendment took away, and a setting it added, were said twice
        let d = Design {
            experiment: "int8".into(),
            state: "changed",
            changes: vec![
                change("setting:optim.wd", "changed", Some("0.0001"), Some("0.0005")),
                change("source:optim.wd", "removed", Some("configs/train.yaml#optim.wd"), None),
                change("setting:optim.mom", "added", None, Some("0.9")),
                change("source:optim.mom", "added", None, Some("configs/train.yaml#optim.mom")),
                change("amendment:1", "added", None, Some("setting optim.wd: 0.0001 -> 0.0005; source configs/train.yaml#optim.wd -> none")),
                change("amendment:2", "added", None, Some("setting optim.mom: new, 0.9")),
                change("amendment_why:2", "added", None, Some("the plan adds momentum")),
            ],
            ..Default::default()
        };
        assert_eq!(
            d.change_lines(),
            vec![
                "changed since you confirmed it: setting optim.mom's source added: configs/train.yaml#optim.mom",
                "changed since you confirmed it: amended: setting optim.wd: 0.0001 -> 0.0005; source configs/train.yaml#optim.wd -> none",
                "changed since you confirmed it: amended: setting optim.mom: new, 0.9 · note: the plan adds momentum",
            ]
        );
        assert_eq!(d.standing_line().as_deref(), Some("design not confirmed · list it to confirm: rb confirm int8"));
        assert_eq!(crate::view::as_past_version(true, || d.standing_line()).as_deref(), Some("design not confirmed"));
    }

    struct Scratch(std::path::PathBuf);
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    thread_local! {
        /// how many parts of a design the user owns rb said, to say how a design differs from it
        pub(super) static SAID: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
        /// how many designs rb hashed
        pub(super) static DIGESTS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
        /// how many times rb read what a line keeps of a design the user owns
        pub(super) static READS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
    }

    #[test]
    fn weighing_the_designs_the_user_owns_hashes_only_the_closest() {
        // review of the snapshot rule, round 3: a wide design with 400 of the user's writes since a confirmation, then a
        // session's change, read 2.1 times slower than group F's, as every design the user owns was rebuilt and hashed
        // to weigh it; each is weighed from what its line kept, and only the one the design is said against is hashed
        let _turn = crate::sys::turns::starting();
        let dir = Scratch(std::env::temp_dir().join(format!("rb-design-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32))));
        std::fs::create_dir_all(&dir.0).unwrap();
        let led = Ledger::init(&dir.0, "Design fixture", Some("fixture"), Some("sdk"), Some("design-test"), false).unwrap();
        led.add_experiment(&NewExperiment { title: "e1".into(), id: Some("e1".into()), hypothesis: "e1 holds".into(), ..Default::default() }).unwrap();
        let e: Experiment = led.load("e1").unwrap();
        let now = structured(&parts_of(&e, &[], "", &[]));
        let mut lines = vec![crate::obj! {"at" => "2026-10-14T09:00:00+00:00", "actor" => "agent:fixture", "op" => "note", "kind" => "experiment", "id" => "e1",
            "detail" => crate::obj! {"changed_parts" => crate::obj! {"e1" => crate::obj! {"hypothesis" => "e1 holds"}}, "parts_compared" => COMPARED}}];
        // 300 designs the user owns, each with a why of its own: kept whole every CHANGES_IN_A_ROW, else as what changed
        let mut prev: Option<String> = None;
        for n in 0..300 {
            let mut form = now.clone();
            form.insert("why".into(), Value::Str(format!("reason {n}")));
            let full = digest(&form);
            let key: String = full.chars().take(7).collect();
            let entry = match prev.as_ref().filter(|_| n % CHANGES_IN_A_ROW != 0) {
                Some(from) => crate::obj! {"key" => key, "full" => full.clone(), "from" => from.chars().take(7).collect::<String>(), "from_full" => from.clone(),
                    "parts" => crate::obj! {"why" => format!("reason {n}")}},
                None => crate::obj! {"key" => key, "full" => full.clone(), "parts" => Value::Dict(form)},
            };
            prev = Some(full);
            lines.push(crate::obj! {"at" => format!("2026-10-14T10:{:02}:{:02}+00:00", n / 60, n % 60), "actor" => "human:ana", "op" => "spec_set", "kind" => "experiment",
                "id" => "e1", "detail" => crate::obj! {"owned" => crate::obj! {"e1" => entry}}});
        }
        let log = dir.0.join(".rb").join("log.jsonl");
        let mut text = std::fs::read_to_string(&log).unwrap();
        for l in &lines {
            text.push_str(&crate::pyjson::dumps(l));
            text.push('\n');
        }
        std::fs::write(&log, text).unwrap();
        DIGESTS.with(|n| n.set(0));
        READS.with(|n| n.set(0));
        let d = led.design("e1").unwrap().unwrap();
        assert_eq!((d.state, d.changes.iter().map(|c| c.key.as_str()).collect::<Vec<_>>()), ("changed", vec!["why"]));
        assert_eq!(d.changes[0].was.as_deref(), Some("reason 299"));
        // the closest's own chain of lines, each hashed once as it is worked out, and no other design
        let hashed = DIGESTS.with(|n| n.get());
        assert!(hashed <= CHANGES_IN_A_ROW + 4, "{hashed} designs hashed to weigh 300");
        // each line read a few times, never once for every other line
        let reads = READS.with(|n| n.get());
        assert!(reads <= 10 * 300, "{reads} reads of what 300 lines keep");
    }

    #[test]
    fn only_the_closest_design_the_user_owns_is_said_part_by_part() {
        // review of the snapshot rule, round 1: a wide design read up to 1.9 times slower than group F's, as every design
        // the user owns was compared and said part by part on every read; each is weighed, and only the closest said
        let _turn = crate::sys::turns::starting();
        let dir = Scratch(std::env::temp_dir().join(format!("rb-design-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32))));
        std::fs::create_dir_all(&dir.0).unwrap();
        let led = Ledger::init(&dir.0, "Design fixture", Some("fixture"), Some("sdk"), Some("design-test"), false).unwrap();
        led.add_experiment(&NewExperiment { title: "e1".into(), id: Some("e1".into()), hypothesis: "e1 holds".into(), ..Default::default() }).unwrap();
        let e: Experiment = led.load("e1").unwrap();
        let now = structured(&parts_of(&e, &[], "", &[]));
        // a session's line wrote its hypothesis; the user owns 50 designs, each with a why and settings of its own
        let mut lines = vec![crate::obj! {"at" => "2026-10-14T09:00:00+00:00", "actor" => "agent:fixture", "op" => "note", "kind" => "experiment", "id" => "e1",
            "detail" => crate::obj! {"changed_parts" => crate::obj! {"e1" => crate::obj! {"hypothesis" => "e1 holds"}}, "parts_compared" => COMPARED}}];
        for n in 0..50 {
            let mut kept = now.clone();
            kept.insert("why".into(), Value::Str(format!("reason {n}")));
            for k in 0..n % 5 {
                kept.insert(format!("setting:s{k}"), crate::obj! {"value" => n});
            }
            let key: String = digest(&kept).chars().take(7).collect();
            lines.push(crate::obj! {"at" => format!("2026-10-14T10:{n:02}:00+00:00"), "actor" => "human:ana", "op" => "spec_set", "kind" => "experiment", "id" => "e1",
                "detail" => crate::obj! {"owned" => crate::obj! {"e1" => crate::obj! {"key" => key, "parts" => Value::Dict(kept)}}}});
        }
        let log = dir.0.join(".rb").join("log.jsonl");
        let mut text = std::fs::read_to_string(&log).unwrap();
        for l in &lines {
            text.push_str(&crate::pyjson::dumps(l));
            text.push('\n');
        }
        std::fs::write(&log, text).unwrap();
        SAID.with(|n| n.set(0));
        let d = led.design("e1").unwrap().unwrap();
        assert_eq!((d.state, d.changes.iter().map(|c| c.key.as_str()).collect::<Vec<_>>()), ("changed", vec!["why"]));
        assert_eq!(d.changes[0].was.as_deref(), Some("reason 45"));
        assert_eq!(SAID.with(|n| n.get()), 1);
    }

    #[test]
    fn whether_a_session_touched_a_design_is_read_from_the_log_without_the_design_index() {
        // trial of the snapshot rule's closing pass: rb log, drawing a line that holds a character a screen would hide,
        // worked out every design (each experiment's and criterion's file, each design the user owns) only to know
        // whether to escape it, 1.22 times group F's time on a log of 2,000 lines; it reads the log, and the criteria
        // only where a line's experiments rest on them, and says what the index says
        let _turn = crate::sys::turns::starting();
        let dir = Scratch(std::env::temp_dir().join(format!("rb-design-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32))));
        std::fs::create_dir_all(&dir.0).unwrap();
        let led = Ledger::init(&dir.0, "Design fixture", Some("fixture"), Some("sdk"), Some("design-test"), false).unwrap();
        led.add_experiment(&NewExperiment { title: "e1".into(), id: Some("e1".into()), hypothesis: "e1 holds".into(), ..Default::default() }).unwrap();
        let led = Ledger::new(&dir.0, "cli", None);
        assert!(led.shows_escapes().unwrap());
        assert!(!led.worked_out("design"), "the design index was worked out to decide on escapes");
        assert!(!led.design_index().unwrap().touched.is_empty());

        let crit = |id: &str, exp: &str| {
            let d = crate::dict! {"created_by" => "human:ana", "created_at" => "2026-10-14T09:00:00+00:00", "id" => id, "experiment" => exp,
                "statement" => "INT8 loses at most 0.5 top-1", "metric" => "change.top1", "comparator" => "at_least", "target" => -0.005, "over" => "mean",
                "role" => "primary", "min_n" => 1, "origin" => "own"};
            Criterion::validate(&Value::Dict(d), &mut Vec::new(), &mut Vec::new()).expect("a criterion")
        };
        let row = |actor: &str, op: &str, kind: &str, id: &str, detail: Value| {
            crate::dict! {"at" => "2026-10-14T09:00:00+00:00", "actor" => actor, "op" => op, "kind" => kind, "id" => id, "sha256" => "ab", "detail" => detail}
        };
        let structured = |changed: Value| crate::obj! {"changed_parts" => changed, "parts_compared" => COMPARED};
        // what the index takes, line by line, with every criterion read
        let index = |log: &[Dict], crits: &[Criterion]| {
            let filed = Filed::read(crits, log);
            log.iter().filter(|r| !is_session_line(r)).any(|r| !touched_in(r, &line_experiments(r, &filed)).is_empty())
        };
        let read = std::cell::Cell::new(0);
        let check = |log: Vec<Dict>, crits: Vec<Criterion>, touched: bool, reads: usize| {
            read.set(0);
            let all = Rc::new(crits.clone());
            let got = any_touched(&log, || {
                read.set(read.get() + 1);
                Ok(all)
            })
            .unwrap();
            assert_eq!((got, index(&log, &crits), read.get()), (touched, touched, reads), "{log:?}");
        };
        let ana = "human:ana";
        let agent = "agent:fixture";
        // the user's own lines, and a session's that change no part (a note, a write that names a design with none)
        check(vec![row(ana, "add", "experiment", "e1", crate::obj! {})], vec![], false, 0);
        check(vec![row(agent, "note", "experiment", "e1", crate::obj! {})], vec![], false, 0);
        check(vec![row(agent, "spec_set", "experiment", "e1", structured(crate::obj! {"e1" => crate::obj! {}}))], vec![], false, 0);
        // a session's write to an experiment's file, or its gate; a clone of a design whose parts a session wrote
        check(vec![row(agent, "spec_set", "experiment", "e1", crate::obj! {})], vec![], true, 0);
        check(vec![row(agent, "gate", "experiment", "e1", crate::obj! {"if_holds" => "ship INT8"})], vec![], true, 0);
        check(vec![row(ana, "clone", "experiment", "e1", crate::obj! {"unconfirmed_parts" => crate::obj! {"why" => agent}})], vec![], true, 0);
        // a session's criterion line that names a design with no part changed: the criterion's own experiment decides,
        // which only its file says
        let moved = row(agent, "edit", "criterion", "c1", structured(crate::obj! {"e1" => crate::obj! {}}));
        check(vec![moved.clone()], vec![crit("c1", "e2")], true, 1);
        check(vec![moved.clone()], vec![crit("c1", "e1")], false, 1);
        check(vec![moved], vec![], false, 1);
        // a session's criterion line an earlier rb wrote, of the experiment its path says
        let mut older = row(agent, "add", "criterion", "c1", crate::obj! {});
        older.insert("path".into(), Value::Str(".rb/experiments/e1/criteria.yaml".into()));
        check(vec![older], vec![], true, 1);
    }

    #[test]
    fn one_experiments_design_is_worked_out_alone() {
        // rb show <exp> reads one design: it never works out every experiment's (review of group E: +30% on rb show)
        let _turn = crate::sys::turns::starting();
        let dir = Scratch(std::env::temp_dir().join(format!("rb-design-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32))));
        std::fs::create_dir_all(&dir.0).unwrap();
        let led = Ledger::init(&dir.0, "Design fixture", Some("fixture"), Some("sdk"), Some("design-test"), false).unwrap();
        for id in ["e1", "e2", "e3"] {
            led.add_experiment(&NewExperiment { title: id.into(), id: Some(id.into()), hypothesis: format!("{id} holds"), ..Default::default() }).unwrap();
        }
        assert_eq!(led.design("e2").unwrap().unwrap().experiment, "e2");
        let ix = led.design_index().unwrap();
        assert_eq!(ix.done.borrow().keys().cloned().collect::<Vec<_>>(), vec!["e2".to_string()]);
        assert!(ix.all.borrow().is_none());
        assert_eq!(led.designs().unwrap().len(), 3);
    }

    #[test]
    fn each_design_the_user_owns_is_read_once_at_its_latest_line() {
        // review of the snapshot rule, round 1: every kept design was hashed and compared on every read, a design kept
        // again included, so a wide design read up to 1.9 times slower than group F's
        let form = |lr: f64| crate::dict! {"setting:optim.lr" => crate::obj! {"value" => lr}, "hypothesis" => "INT8 keeps top-1"};
        let key = |lr: f64| -> String { digest(&form(lr)).chars().take(7).collect() };
        let line = |lr: f64, key: String| crate::dict! {"detail" => crate::obj! {"owned" => crate::obj! {"int8" => crate::obj! {"key" => key, "parts" => Value::Dict(form(lr))}}}};
        let lrs = [0.0, 0.001, 0.002, 0.001, 0.002, 0.003];
        let mut log: Vec<Dict> = lrs.iter().map(|lr| line(*lr, key(*lr))).collect();
        log.push(line(0.001, "0000000".into()));
        let kept = |pos: usize| Owned { pos, kept: Kept::Form { key: log[pos].get("detail").unwrap().get("owned").unwrap().get("int8").unwrap().get_str("key").unwrap().to_string() } };
        let once = once_each(&log, "int8", (1..=5).map(kept).collect());
        assert_eq!(once.iter().map(|o| o.pos).collect::<Vec<_>>(), vec![3, 4, 5]);
        let parts = |pos: usize| kept_entry(&log[pos], "int8").and_then(|e| e.get("parts")).and_then(Value::as_dict).cloned().unwrap();
        assert!(digest(&parts(1)).starts_with(&key(0.001)) && !digest(&parts(6)).starts_with("0000000"));
        // a value's kind is part of what it is: 3 and "3" are two designs, and 1 and true too
        assert!(!identical(&Value::Int(3), &Value::Str("3".into())) && !identical(&Value::Int(1), &Value::Bool(true)));
        assert!(identical(&crate::obj! {"a" => 1, "b" => "x"}, &crate::obj! {"b" => "x", "a" => 1}));
    }

    #[test]
    fn two_values_rb_says_alike_are_told_apart() {
        // review of the snapshot rule, round 1: `was 3, now 3`
        let v = |x: Value| crate::obj! {"value" => x};
        assert_eq!(told_apart(&v(Value::Int(3)), &v(Value::Str("3".into())), "3"), ("3 (a number)".into(), "3 (text)".into()));
        let src = |term: &str| crate::obj! {"kind" => "file", "path" => "docs/train.md", "line" => 3, "term" => term};
        assert_eq!(told_apart(&src("learning rate"), &src("weight decay"), "docs/train.md:3"), ("docs/train.md:3 (term \"learning rate\")".into(), "docs/train.md:3 (term \"weight decay\")".into()));
        let mut none = src("x");
        none.as_dict_mut().unwrap().shift_remove("term");
        assert_eq!(told_apart(&src("x"), &none, "docs/train.md:3").1, "docs/train.md:3 (term none)");
        assert_eq!(told_apart(&Value::Str("2026-10-14T09:00:00+00:00".into()), &Value::Str("2026-10-14T10:00:00+00:00".into()), "frozen 2026-10-14").0, "frozen 2026-10-14 (2026-10-14T09:00:00+00:00)");
        // a field inside a field is named by its path
        let cited = |term: &str| crate::obj! {"value" => 0.01, "source" => src(term)};
        assert_eq!(told_apart(&cited("a"), &cited("b"), "0.01 from docs/train.md:3").1, "0.01 from docs/train.md:3 (source term \"b\")");
    }

    #[test]
    fn no_two_sources_list_alike() {
        // review of the snapshot rule, round 3: rb confirm listed every source field by its name (`from file (path
        // "configs/train.yaml", key "optim.lr")`), where group E and rb show say `configs/train.yaml#optim.lr`; it says
        // the short form, with what it leaves out after it, wherever that form says each field apart
        let src = |d: Dict| Source::validate(&Value::Dict(d), &mut Vec::new(), &mut Vec::new());
        let file = |path: &str, key: Option<&str>| {
            let mut d = crate::dict! {"kind" => "file", "path" => path};
            if let Some(k) = key {
                d.insert("key".into(), Value::Str(k.into()));
            }
            d
        };
        assert_eq!(listed_source(&src(file("configs/train.yaml", Some("optim.lr"))).unwrap()), "configs/train.yaml#optim.lr");
        let mut d = file("docs/train.md", None);
        d.insert("line".into(), Value::Int(3));
        d.insert("term".into(), Value::Str("learning rate".into()));
        assert_eq!(listed_source(&src(d).unwrap()), "docs/train.md:3 (term \"learning rate\")");
        let mut d = file("configs/train.yaml", Some("optim.lr"));
        d.insert("line".into(), Value::Int(2));
        d.insert("commit".into(), Value::Str("abcdef0123456789abcdef0123456789abcdef01".into()));
        assert_eq!(listed_source(&src(d).unwrap()), "configs/train.yaml#optim.lr @abcdef0 (line 2; commit abcdef0123456789abcdef0123456789abcdef01)");
        // every source these fields make lists apart from every other
        let long = "a".repeat(70);
        let mut said: HashMap<String, String> = HashMap::new();
        let mut n = 0;
        for path in ["a", "a.yaml", "a#b.yaml", "a b.yaml", "a:3", "c/d.md", "r=1.db"] {
            for key in [None, Some(""), Some("x"), Some("x#y"), Some("x y"), Some("b.yaml#x"), Some("x:3"), Some("t[id=c2].d"), Some("x\"y"), Some("x@y")] {
                for line in [None, Some(0), Some(3)] {
                    for quote in [None, Some(""), Some("short words"), Some("q\"x"), Some(long.as_str()), Some("a.yaml#x")] {
                        for term in [None, Some(""), Some("rate"), Some("r\"t")] {
                            for commit in [None, Some(""), Some("abcdef0"), Some("abcdef01"), Some("abc def")] {
                                for (locator, note) in [(None, ""), (Some("p3"), ""), (Some("line 3"), ""), (None, "n")] {
                                    let mut d = file(path, key);
                                    for (k, v) in [("line", line.map(Value::Int)), ("quote", quote.map(|q| Value::Str(q.into()))), ("term", term.map(|t| Value::Str(t.into()))),
                                        ("commit", commit.map(|c| Value::Str(c.into()))), ("locator", locator.map(|l| Value::Str(l.into())))] {
                                        if let Some(v) = v {
                                            d.insert(k.into(), v);
                                        }
                                    }
                                    d.insert("note".into(), Value::Str(note.into()));
                                    let Some(s) = src(d) else { continue };
                                    let form = canonical(&source_form(&s));
                                    let listed = listed_source(&s);
                                    if let Some(other) = said.insert(listed.clone(), form.clone()) {
                                        assert_eq!(other, form, "{listed}");
                                    }
                                    n += 1;
                                }
                            }
                        }
                    }
                }
            }
        }
        // a note's words, a url, a run's output: each lists apart from every other source
        for (kind, field, text) in [("note", "note", "configs.seed in results.db"), ("note", "note", "a (term \"b\")"), ("note", "note", "note: x"), ("note", "note", "x "),
            ("url", "url", "https://example.org/a#b"), ("url", "url", "a.yaml#x"), ("run", "path", "out/m.json")] {
            for locator in [None, Some("p3")] {
                let mut d = crate::dict! {"kind" => kind, field => text};
                if kind == "run" {
                    d.insert("pointer".into(), Value::Str("/top1".into()));
                }
                if let Some(l) = locator {
                    d.insert("locator".into(), Value::Str(l.into()));
                }
                let Some(s) = src(d) else { continue };
                let (form, listed) = (canonical(&source_form(&s)), listed_source(&s));
                if let Some(other) = said.insert(listed.clone(), form.clone()) {
                    assert_eq!(other, form, "{listed}");
                }
                n += 1;
            }
        }
        let mut d = crate::dict! {"kind" => "note", "note" => "configs.seed in results.db"};
        d.insert("locator".into(), Value::Null);
        assert_eq!(listed_source(&src(d).unwrap()), "note: configs.seed in results.db");
        let mut d = file("results.db", Some("configs[config_id=a91f03c6d82e5b47].width"));
        d.insert("line".into(), Value::Int(4));
        assert_eq!(listed_source(&src(d).unwrap()), "results.db#configs[config_id=a91f03c6d82e5b47].width (line 4)");
        assert!(n > 1000, "{n}");
    }

    fn part(key: &str, value: &str, raw: Value) -> Part {
        Part { key: key.into(), value: value.into(), raw }
    }

    #[test]
    fn the_key_names_the_structured_parts_never_how_rb_says_them() {
        let parts = |said: &str, raw: Value| vec![part("rule:acc-tol", said, raw)];
        let rule = || crate::obj! {"metric" => "change.top1", "comparator" => "at_least", "target" => -0.005, "over" => "mean", "min_n" => 1};
        // a later rb that says a rule otherwise names the same design
        assert_eq!(design_key(&parts("change.top1 ≥ -0.005", rule())), design_key(&parts("top-1 change at least -0.005", rule())));
        let mut other = rule();
        other.as_dict_mut().unwrap().insert("target".into(), Value::Float(-0.003));
        assert_ne!(design_key(&parts("change.top1 ≥ -0.005", rule())), design_key(&parts("change.top1 ≥ -0.005", other)));
        // the order parts are listed in names nothing
        let two = vec![part("hypothesis", "h", Value::Str("h".into())), part("why", "w", Value::Str("w".into()))];
        let swapped: Vec<Part> = two.iter().rev().cloned().collect();
        assert_eq!(design_key(&two), design_key(&swapped));
        assert_eq!(design_key(&two).len(), 7);
        // what rb found when it last read a source is no part of it
        let src = |resolved: bool| {
            let mut d = crate::dict! {"kind" => "file", "path" => "configs/train.yaml", "key" => "optim.lr", "note" => ""};
            if resolved {
                d.insert("resolved".into(), crate::obj! {"at" => "2026-10-14T09:00:00+00:00", "sha256" => "ab", "line" => 2, "text" => "lr: 0.001"});
            }
            Source::validate(&Value::Dict(d), &mut Vec::new(), &mut Vec::new()).unwrap()
        };
        assert_eq!(canonical(&source_form(&src(true))), canonical(&source_form(&src(false))));
        // what a design the user owns reads as, said from its structured form, is what rb says of the part now
        assert_eq!(said_of("rule:acc-tol", &rule()), "change.top1 ≥ -0.005");
        assert_eq!(said_of("source:optim.lr", &source_form(&src(true))), "configs/train.yaml#optim.lr");
        assert_eq!(said_of("setting:optim.lr", &crate::obj! {"value" => 0.001}), "0.001");
        assert_eq!(said_of("setting:seed", &crate::obj! {"per_run" => true}), "per run");
        assert_eq!(said_of("link:acc-tol:q-acc", &Value::Str(String::new())), "no interpretation");
        assert_eq!(said_of("freeze", &Value::Str("2026-10-14T09:00:00+00:00".into())), "frozen 2026-10-14");
    }
}
