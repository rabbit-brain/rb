//! The view model: what the user's screen shows, computed here and only drawn by renderers. The briefs `rb status`
//! leads with are computed here too.

use crate::errors::Res;
use crate::ledger::{call_line, made, show, Ledger};
use crate::pyjson::py_str;
use crate::record::*;
use crate::surface::{self, CriterionFields, Row};
use crate::value::{Dict, Value};
use indexmap::IndexMap;
use std::collections::HashSet;

pub const EARLIER: &str = " · made on an earlier reading";
/// A past call was made before any reading rb holds: what moved is what its criteria read when it was recorded.
pub const EARLIER_PAST: &str = " · its readings have moved since it was recorded";

/// The note a call that is not current carries: a past call's readings are never what it was made on.
pub fn earlier(past: bool) -> &'static str {
    if past { EARLIER_PAST } else { EARLIER }
}

/// The decision an experiment gates, in its author's words: what happens if it holds, and if not.
pub fn gate_line(if_holds: &str, if_not: &str) -> String {
    let parts: Vec<String> = [
        if if_holds.is_empty() { String::new() } else { format!("if it holds: {if_holds}") },
        if if_not.is_empty() { String::new() } else { format!("if not: {if_not}") },
    ]
    .into_iter()
    .filter(|x| !x.is_empty())
    .collect();
    parts.join(" · ")
}

/// Whether a decision in rb status's data confirms a call a session recorded: its reason is then that session's note.
pub fn confirms_one(d: &Value) -> bool {
    d.get("authority").and_then(|a| a.get("confirms")).map_or(false, |c| c.truthy())
}

/// Who wrote words a session gave with a part of a design the user has confirmed since, as `not_confirmed` and
/// `why_part` read it: still a session's note, and no longer waiting. No actor rb records is named so.
pub const CONFIRMED: &str = "confirmed:";

/// What a note or an interpretation a session wrote adds after it on a screen: that the user has not confirmed it. A
/// write from the user's own terminal adds nothing, nor one the user's confirmation of a design covers (`CONFIRMED`);
/// no screen says who wrote either.
pub fn not_confirmed(by: &str) -> &'static str {
    if by.is_empty() || words_are_theirs(by) || by == CONFIRMED { "" } else { " · not confirmed" }
}

/// An experiment's why as a line labels it: the user's own as their reason ("why"), a session's as the note it is, with
/// what it rests on in its words and, until the user confirms the design, that they have not confirmed it, never in
/// rb's voice; no screen says who wrote.
pub fn why_part(why: &str, by: &str) -> Option<(&'static str, String)> {
    if why.is_empty() {
        None
    } else if by.is_empty() || words_are_theirs(by) {
        Some(("why", why.to_string()))
    } else {
        Some(("note", format!("{}{}", on_one_line(why), not_confirmed(by))))
    }
}

/// The reason a write gave, after its line (a freeze's, an amendment's, a retraction's, a supersession's): `<sep><why>`
/// for the user's own, ` · note: <why> · not confirmed` for a session's, as `why_part` says a session's why.
pub fn reason_part(why: &str, by: &str, sep: &str) -> String {
    reason_with_words(why, "", by, sep)
}

/// `reason_part` with the words given at the write, said after the reason as `with_words` says them.
pub fn reason_with_words(why: &str, words: &str, by: &str, sep: &str) -> String {
    match why_part(why, by) {
        None => String::new(),
        Some(("why", text)) => format!("{sep}{}", with_words(&text, words, by)),
        Some(_) => format!(" · note: {}", note_said(&on_one_line(why), words, by)),
    }
}

/// A reason a write gave, said in a sentence of its own: the user's as given, a session's as `note: <why> · not
/// confirmed`.
pub fn reason_said(why: &str, by: &str) -> String {
    match why_part(why, by) {
        None => String::new(),
        Some(("why", text)) => text,
        Some((label, text)) => format!("{label}: {text}"),
    }
}

/// A note as a screen line says it: its text, the words given with it, and whether the user has confirmed it.
pub fn note_said(text: &str, words: &str, by: &str) -> String {
    with_words(text, words, by) + not_confirmed(by)
}

/// The gate line with the words given when it was set, said as `gate_parts` says them.
pub fn gate_said(if_holds: &str, if_not: &str, words: &str, by: &str, confirm: &str) -> String {
    gate_parts(if_holds, if_not, words, by, confirm, false).into_iter().map(|(k, t)| format!("{k}: {t}")).collect::<Vec<_>>().join(" · ")
}

/// The gate as labelled lines ("if it holds", "if not"), with the words given when it was set: in place of a part
/// that is those words, so they are said once, else after the last part. Words a session gave are said as given on a
/// page too, so its first-person words never stand there bare. A gate a session set ends as its note does, `· not
/// confirmed`, and, where `confirm` is given, with the command that makes it the user's (its design's, which it waits
/// with), as a call that waits does, a page included; `by` is who set it. A view that says its design's standing line
/// gives no command here: an experiment waits in one place on a screen.
/// On two lines, `first` puts that mark on the first branch, where only it is one a session wrote (`Design::gate_first`).
pub fn gate_parts(if_holds: &str, if_not: &str, words: &str, by: &str, confirm: &str, first: bool) -> Vec<(&'static str, String)> {
    let mut parts: Vec<(&'static str, String)> =
        [("if it holds", if_holds), ("if not", if_not)].into_iter().filter(|(_, t)| !t.is_empty()).map(|(k, t)| (k, t.to_string())).collect();
    let said = match reader() {
        Reader::Page if !words.is_empty() && !words_are_theirs(by) => format!("words (as given): \"{}\"", on_one_line(words)),
        _ => your_words(words, by),
    };
    let same = |t: &str| on_one_line(t).trim() == on_one_line(words).trim();
    if said.is_empty() {
    } else if let Some(part) = parts.iter_mut().find(|(_, t)| same(t)) {
        part.1 = said;
    } else if let Some(last) = parts.last_mut() {
        last.1 = format!("{} · {said}", last.1);
    }
    let at = if first { parts.first_mut() } else { parts.last_mut() };
    if let Some(part) = at {
        part.1.push_str(not_confirmed(by));
        if !not_confirmed(by).is_empty() && !confirm.is_empty() {
            part.1.push_str(&format!(" · confirm: {confirm}"));
        }
    }
    parts
}

/// A question's reading as rb's text says it: its sentences, with a note every criterion of one experiment here carries
/// said once, before the first of them, and left off each. --json keeps every sentence whole.
pub fn summary_lines(inquiry: &Value) -> Vec<String> {
    let summary: Vec<String> = list_of(inquiry, "summary").iter().map(py_str).collect();
    let direct = list_of(inquiry, "direct");
    // a sentence that is one criterion's reading, with its experiment and what qualifies it
    let row = |line: &str| -> Option<(String, Vec<String>)> {
        let r = direct.iter().find(|r| get_or(r, "summary", "") == line)?;
        Some((get_or(r, "experiment", ""), list_of(r, "qualifications").iter().map(py_str).filter(|q| line.contains(q.as_str())).collect()))
    };
    let mut by: IndexMap<String, Vec<Vec<String>>> = IndexMap::new();
    for line in &summary {
        if let Some((e, quals)) = row(line) {
            by.entry(e).or_default().push(quals);
        }
    }
    let shared: IndexMap<String, Vec<String>> = by
        .into_iter()
        .filter(|(_, rows)| rows.len() > 1)
        .map(|(e, rows)| {
            let common: Vec<String> = rows[0].iter().filter(|q| rows[1..].iter().all(|r| r.contains(q))).cloned().collect();
            (e, common)
        })
        .filter(|(_, common)| !common.is_empty())
        .collect();
    let mut out = Vec::new();
    let mut said: Vec<String> = Vec::new();
    for line in summary {
        let Some(common) = row(&line).and_then(|(e, _)| shared.get(&e).map(|c| (e, c))) else {
            out.push(line);
            continue;
        };
        let (e, common) = common;
        if !said.contains(&e) {
            said.push(e.clone());
            out.push(format!("On every {e} criterion below: {}.", common.join("; ")));
        }
        let mut line = line;
        for q in common {
            if let Some(at) = line.find(&format!("; {q}")) {
                line.replace_range(at..at + q.len() + 2, "");
            } else if let Some(at) = line.find(&format!(" {q}; ")) {
                line.replace_range(at + 1..at + q.len() + 3, "");
            } else if line.ends_with(&format!(" {q}.")) {
                line.truncate(line.len() - q.len() - 2);
            }
        }
        out.push(line);
    }
    out
}

/// The command a gate line gives beside a session's gate: none while its design waits with it (the design's own line,
/// and the one line for the designs that wait, say how), else the gate's own (`gate_confirm`).
pub fn gate_command(experiment: &str, if_holds: &str, if_not: &str, words: &str, design_confirm: &str) -> String {
    if design_confirm.is_empty() { gate_confirm(experiment, if_holds, if_not, words) } else { String::new() }
}

/// The command that confirms a gate as it reads now: the experiment's id and a short hash of the gate and its words,
/// so a gate changed after it was listed is not confirmed in its place. A gate a session set waits with its design,
/// whose command (`Design::confirm`) confirms both; this one names a gate an earlier listing gave.
pub fn gate_confirm(experiment: &str, if_holds: &str, if_not: &str, words: &str) -> String {
    from_here(&format!("rb confirm {experiment}@{}", gate_key(if_holds, if_not, words)))
}

/// The short hash of a gate and the words given with it, as `rb confirm <experiment>@<hash>` names it.
pub fn gate_key(if_holds: &str, if_not: &str, words: &str) -> String {
    let said = Value::list_of_str([if_holds.to_string(), if_not.to_string(), words.to_string()]);
    crate::rbyaml::sha256_hex(crate::ledger::canonical(&said).as_bytes()).chars().take(7).collect()
}

/// A criterion's statement where the words given with it are that statement: those words, said once, as `gate_parts`
/// says a gate's (as given where a session gave them, on a page too); None when they are not its statement.
pub fn statement_said(statement: &str, words: &str, by: &str) -> Option<String> {
    if words.trim().is_empty() || on_one_line(statement).trim() != on_one_line(words).trim() {
        return None;
    }
    Some(match reader() {
        Reader::Page if words_are_theirs(by) => statement.to_string(),
        Reader::Page => format!("words (as given): \"{}\"", on_one_line(words)),
        _ => your_words(words, by),
    })
}

/// A finding's statement as a line leads with it, and the words a line still adds after it: none when they are it.
pub fn finding_statement(f: &Value) -> (String, String, String) {
    let declared = f.get("declared").filter(|d| d.truthy());
    let words = declared.and_then(|d| d.get("words")).filter(|w| w.truthy()).map(py_str).unwrap_or_default();
    let by = declared.map(|d| get_or(d, "by", "")).unwrap_or_default();
    let statement = get_or(f, "statement", "");
    match statement_said(&statement, &words, &by) {
        Some(said) => (said, String::new(), by),
        None => (statement, words, by),
    }
}

/// Who reads what is being built, which says how the user's words are said there: rb's text tags them (`your words:`),
/// the user's own screens (rb open, rb ui) say them without the tag, which would tell that an agent wrote the line, and a
/// page anyone may be sent (rb page, rb export, rb sync) leaves them out, as it leaves out the machine's platform.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Reader {
    Text,
    Screen,
    Page,
}

thread_local! {
    static READER: std::cell::Cell<Reader> = const { std::cell::Cell::new(Reader::Text) };
}

/// What `build` makes, made for `reader`, or for whoever reads more widely than it, as a page made inside a screen is.
fn made_for<T>(reader: Reader, build: impl FnOnce() -> T) -> T {
    struct Restore(Reader);
    impl Drop for Restore {
        fn drop(&mut self) {
            READER.with(|r| r.set(self.0));
        }
    }
    let _restore = Restore(READER.with(|r| r.replace(reader.max(r.get()))));
    build()
}

/// What `build` makes, made for the user's own screen (rb open, rb ui): the user's words are said without their tag.
pub fn for_a_screen<T>(build: impl FnOnce() -> T) -> T {
    made_for(Reader::Screen, build)
}

/// What `build` makes, made for a page anyone may be sent: the user's words stay off it, wherever rb would say them.
pub fn for_a_page<T>(build: impl FnOnce() -> T) -> T {
    made_for(Reader::Page, build)
}

/// Who reads what is being built now.
pub fn reader() -> Reader {
    READER.with(|r| r.get())
}

// ---------------------------------------------------------------- a page names no place

thread_local! {
    /// While a page is made: the linked worktrees nested in its record's root, by their paths from that root.
    static PAGE_TREES: std::cell::RefCell<Option<Vec<String>>> = const { std::cell::RefCell::new(None) };
}

/// The linked worktrees nested in the record's root, by their paths from it ("/"-separated): those git lists, and
/// every one a receipt or a log line keeps (one removed since included).
pub fn page_trees_of(led: &Ledger) -> Vec<String> {
    let root = crate::sources::resolve_path(&led.root);
    let mut paths: Vec<std::path::PathBuf> = Vec::new();
    if let Some(top) = crate::sources::git_top(&led.root) {
        paths.extend(crate::elsewhere::worktrees(&top).into_iter().map(|w| w.path).filter(|p| *p != top));
    }
    let kept = |v: Option<&Value>| v.and_then(|w| w.get("path")).and_then(Value::as_str).map(|p| crate::sources::resolve_path(std::path::Path::new(p)));
    if let Ok(runs) = led.all::<Run>() {
        paths.extend(runs.iter().filter_map(|r| kept(r.receipt.attached.as_ref().and_then(|a| a.get("worktree")))));
    }
    if let Ok(rows) = led.log_entries() {
        paths.extend(rows.iter().filter_map(|r| kept(r.get("worktree"))));
    }
    let mut rels: Vec<String> = paths.iter().filter(|p| **p != root).filter_map(|p| crate::sources::rel_to(p, &root)).filter(|r| !r.is_empty()).collect();
    // a record read away from where it was written (a clone of a bundle): a worktree it keeps by its last parts, as the
    // paths written there begin, from the root of the record that was its checkout's
    let listed: Vec<std::path::PathBuf> = crate::sources::git_top(&led.root).map(|top| crate::elsewhere::worktrees(&top).into_iter().map(|w| w.path).collect()).unwrap_or_default();
    for p in paths.iter().filter(|p| !p.starts_with(&root) && !listed.contains(p)) {
        let parts: Vec<String> = p.components().filter_map(|c| match c {
            std::path::Component::Normal(s) => Some(s.to_string_lossy().into_owned()),
            _ => None,
        }).collect();
        for k in 2..=parts.len().min(4) {
            rels.push(parts[parts.len() - k..].join("/"));
        }
    }
    rels.sort_by_key(|r| std::cmp::Reverse(r.len()));
    rels.dedup();
    rels
}

/// What `build` makes as a page of `led`'s record: the paths of the worktrees nested in it are known while it is made,
/// unless a page made for another record's sake (an export's bundle) set them first.
pub fn with_page_trees<T>(trees: Vec<String>, build: impl FnOnce() -> T) -> T {
    struct Restore(Option<Vec<String>>);
    impl Drop for Restore {
        fn drop(&mut self) {
            PAGE_TREES.with(|t| *t.borrow_mut() = self.0.take());
        }
    }
    let had = PAGE_TREES.with(|t| t.borrow().clone());
    let _restore = Restore(had.clone());
    if had.is_none() {
        PAGE_TREES.with(|t| *t.borrow_mut() = Some(trees));
    }
    build()
}

/// Whether a page is being made whose worktrees are known.
pub fn page_trees_known() -> bool {
    PAGE_TREES.with(|t| t.borrow().is_some())
}

/// The words a page says after a file's path inside another worktree.
pub const IN_ANOTHER_WORKTREE: &str = " (in another worktree)";

/// A file's path from the record's root as a page says it: one inside a linked worktree is named by its path inside
/// that worktree, with the words saying it was in another worktree, since a page anyone may be sent names no place.
/// `worktree` is the path the run's receipt keeps for the worktree it was recorded in. Every other reader gets the path
/// as it is.
pub fn page_path(path: &str, worktree: Option<&str>) -> String {
    if reader() == Reader::Screen {
        return match screen_path(path) {
            Some(i) => format!("{} (in {})", i.path, i.place),
            None => path.to_string(),
        };
    }
    if reader() != Reader::Page {
        return path.to_string();
    }
    let trees = PAGE_TREES.with(|t| t.borrow().clone()).unwrap_or_default();
    for t in &trees {
        if let Some(rest) = path.strip_prefix(t.as_str()).and_then(|r| r.strip_prefix('/')).filter(|r| !r.is_empty()) {
            return format!("{rest}{IN_ANOTHER_WORKTREE}");
        }
    }
    // a record read away from where it was written (a clone of a bundle): the run's worktree by its last parts
    if let Some(w) = worktree {
        let tail: Vec<&str> = w.split(['/', '\\']).filter(|s| !s.is_empty()).collect();
        let parts: Vec<&str> = path.split('/').collect();
        for k in (2..=tail.len().min(parts.len().saturating_sub(1))).rev() {
            if parts[..k] == tail[tail.len() - k..] {
                return format!("{}{IN_ANOTHER_WORKTREE}", parts[k..].join("/"));
            }
        }
    }
    path.to_string()
}

/// A file a run names, as a page and the user's screens say it (page_path), with the worktree it is in beside it where
/// the run was recorded in a worktree with a record of its own, whose files it names from there: in short on the user's
/// screens, as another worktree on a page. rb's text says the path as recorded.
pub fn named_file(led: &Ledger, path: &str, run: Option<&Run>) -> String {
    let said = page_path(path, None);
    if said != path || reader() == Reader::Text {
        return said;
    }
    let Some(own) = run.and_then(|r| r.receipt.attached.as_ref()).and_then(|a| a.get("worktree")).and_then(|w| w.get("path")).and_then(Value::as_str) else {
        return said;
    };
    let shown = place_shown(led);
    if crate::sources::resolve_path(std::path::Path::new(own)) == shown.top {
        return said;
    }
    match (reader(), run.and_then(|r| observed_in(r, &shown))) {
        (Reader::Screen, Some(o)) => format!("{path} (in {})", o.short),
        (Reader::Page, _) => format!("{path}{IN_ANOTHER_WORKTREE}"),
        _ => said,
    }
}

/// A resource's location as a screen says it, `provider:ref · state`: a local file inside a nested worktree named, on a
/// page, by its path inside it, and on the user's screens too where `short` asks (as `page_path` names it).
fn location_said(loc: &Value, short: bool) -> String {
    let (provider, r#ref) = (get_or(loc, "provider", ""), get_or(loc, "ref", ""));
    let shown = if provider != "local" || (reader() == Reader::Screen && !short) { r#ref } else { page_path(&r#ref, None) };
    format!("{provider}:{shown} · {}", get_or(loc, "state", ""))
}

/// rb's own words that name a recorded file (`path`, and `path:line`), as a page and the user's screens say them: the
/// file named by its path inside a nested worktree. rb's text gets the words as they are.
pub fn page_words(text: &str, path: &str, line: Option<i64>) -> String {
    if reader() == Reader::Text || page_path(path, None) == path {
        return text.to_string();
    }
    let mut out = text.to_string();
    if let Some(l) = line {
        let at = format!("{path}:{l}");
        out = out.replace(&at, &page_path(&at, None));
    }
    out.replace(path, &page_path(path, None))
}

// ---------------------------------------------------------------- a file in a worktree, as the user's screens name it

thread_local! {
    /// While the user's screen is made: the linked worktrees nested in its record's root, each by its path from that root
    /// and its label in short.
    static SCREEN_TREES: std::cell::RefCell<Option<Vec<(String, String, std::path::PathBuf)>>> = const { std::cell::RefCell::new(None) };
}

/// A file inside a linked worktree nested in the record's root, as the user's screens name it: its path inside that
/// worktree, the worktree's label in short, and the worktree's path from the record's root.
pub struct Inside {
    pub path: String,
    pub place: String,
    /// the worktree's top, resolved
    pub top: std::path::PathBuf,
}

/// A path from the record's root, while the user's screen is made, where it is inside a linked worktree nested there.
pub fn screen_path(path: &str) -> Option<Inside> {
    if reader() != Reader::Screen {
        return None;
    }
    SCREEN_TREES.with(|t| {
        t.borrow().as_ref()?.iter().find_map(|(tree, place, top)| {
            let rest = path.strip_prefix(tree.as_str())?.strip_prefix('/').filter(|r| !r.is_empty())?;
            Some(Inside { path: rest.to_string(), place: place.clone(), top: top.clone() })
        })
    })
}

/// The linked worktrees nested in the record's root at the place shown, each by its path from that root (longest
/// first) and its label in short: those git lists, and every one a run's receipt or a log line keeps.
fn screen_trees_of(led: &Ledger) -> Vec<(String, String, std::path::PathBuf)> {
    let shown = place_shown(led);
    let root = crate::sources::resolve_path(&shown.root);
    let mut out: Vec<(String, String, std::path::PathBuf)> = Vec::new();
    let mut add = |path: &std::path::Path, label: String| {
        if let Some(rel) = crate::sources::rel_to(path, &root).filter(|r| !r.is_empty()) {
            if !out.iter().any(|(r, _, _)| *r == rel) {
                out.push((rel, label, path.to_path_buf()));
            }
        }
    };
    for (path, label) in &shown.labels {
        add(path, label.clone());
    }
    let kept = |v: Option<&Value>| v.and_then(|w| w.get("path")).and_then(Value::as_str).map(|p| crate::sources::resolve_path(std::path::Path::new(p)));
    if let Ok(runs) = led.all::<Run>() {
        for r in runs.iter() {
            let at = r.receipt.attached.as_ref();
            if let Some(path) = kept(at.and_then(|a| a.get("worktree"))) {
                let branch = at.and_then(|a| a.get("branch")).and_then(Value::as_str).filter(|b| !b.is_empty());
                let short = shown.short_of(&path, &crate::sys::slashed(&path));
                add(&path, crate::elsewhere::label(branch, &short, at.and_then(|a| a.get("commit")).and_then(Value::as_str)));
            }
        }
    }
    if let Ok(rows) = led.log_entries() {
        for r in rows.iter() {
            if let Some(path) = kept(r.get("worktree")) {
                add(&path, shown.short_of(&path, &crate::sys::slashed(&path)));
            }
        }
    }
    out.sort_by_key(|(r, _, _)| std::cmp::Reverse(r.len()));
    out
}

/// What `build` makes for the user's screen, the worktrees nested in `led`'s record known while it is made, unless a
/// screen made around it set them first.
fn with_screen_trees<T>(led: &Ledger, build: impl FnOnce() -> T) -> T {
    if reader() != Reader::Screen || SCREEN_TREES.with(|t| t.borrow().is_some()) {
        return build();
    }
    struct Restore;
    impl Drop for Restore {
        fn drop(&mut self) {
            SCREEN_TREES.with(|t| *t.borrow_mut() = None);
        }
    }
    let trees = screen_trees_of(led);
    let _restore = Restore;
    SCREEN_TREES.with(|t| *t.borrow_mut() = Some(trees));
    build()
}

// ---------------------------------------------------------------- where a run was recorded

/// The place a screen shows, against which a run says where it was recorded: the top of the worktree whose record it
/// is, its branch, whether it is a linked worktree (not the clone's main one), and each worktree of the clone (its
/// resolved path, its path as git lists it, and whether it is gone).
#[derive(Debug, Clone, Default)]
pub struct PlaceShown {
    pub top: std::path::PathBuf,
    pub branch: Option<String>,
    pub linked: bool,
    pub trees: Vec<(std::path::PathBuf, String, bool)>,
    /// the top of the clone's main checkout, which a short path is said from
    pub main: std::path::PathBuf,
    /// each worktree's path as the user's screens name it (places::short_path, told apart)
    pub short: Vec<(std::path::PathBuf, String)>,
    /// each worktree's label as the user's screens name it: its branch, then its path in short
    pub labels: Vec<(std::path::PathBuf, String)>,
    /// the places a screen can show, by their resolved paths: each one's id, and the branch checked out there now
    pub shows: Vec<(std::path::PathBuf, String, Option<String>)>,
    /// the record's root at the place shown, as it is live (a past version's too), which a run's file paths are from
    pub root: std::path::PathBuf,
    /// what a command to the record shown starts with, typed in the terminal the screen started in: `cd <path> && `
    /// where that is another place's record (a past version's call is confirmed where it lives now), else nothing
    pub cd: String,
}

/// A command to the record a screen shows, as typed in the terminal the screen started in (`PlaceShown::cd`): with the
/// cd to that record first where it is another place's. Outside a screen, the command as it is.
pub fn from_here(command: &str) -> String {
    let cd = SHOWN.with(|s| s.borrow().as_ref().map(|p| p.cd.clone())).unwrap_or_default();
    if cd.is_empty() || command.is_empty() || command.starts_with("cd ") { command.to_string() } else { format!("{cd}{command}") }
}

/// The id or key an rb confirm command names, past a cd to another place first: what a pending row is listed by.
fn confirmed_id(command: &str) -> Option<&str> {
    command.rfind("rb confirm ").map(|at| &command[at + "rb confirm ".len()..])
}

thread_local! {
    static SHOWN: std::cell::RefCell<Option<PlaceShown>> = const { std::cell::RefCell::new(None) };
    static PAST: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Whether the screen being drawn shows a past version, read only: it offers no command to copy (guarantee 38), so a
/// line that would name one says what it says without it.
pub fn at_past_version() -> bool {
    PAST.with(std::cell::Cell::get)
}

/// `build`, drawn as a screen showing a past version when `past` is true (`at_past_version`).
pub fn as_past_version<T>(past: bool, build: impl FnOnce() -> T) -> T {
    struct Restore(bool);
    impl Drop for Restore {
        fn drop(&mut self) {
            PAST.with(|p| p.set(self.0));
        }
    }
    let _restore = Restore(PAST.with(|p| p.replace(past)));
    build()
}

/// What `build` makes for a screen that shows `shown` (a past version shows its place's record): by default a screen
/// shows the place of the record it reads.
pub fn at_place<T>(shown: PlaceShown, build: impl FnOnce() -> T) -> T {
    struct Restore(Option<PlaceShown>);
    impl Drop for Restore {
        fn drop(&mut self) {
            SHOWN.with(|s| *s.borrow_mut() = self.0.take());
        }
    }
    let _restore = Restore(SHOWN.with(|s| s.borrow_mut().replace(shown)));
    build()
}

fn place_shown(led: &Ledger) -> PlaceShown {
    SHOWN.with(|s| s.borrow().clone()).unwrap_or_else(|| {
        let top = crate::sources::git_top(&led.root).unwrap_or_else(|| led.root.clone());
        let listed = crate::elsewhere::worktrees(&top);
        let branch = listed.iter().find(|w| w.path == top).and_then(|w| w.branch.clone());
        let main = listed.first().map(|w| w.path.clone()).unwrap_or_else(|| top.clone());
        let short = short_paths(&listed, &main);
        let labels = listed.iter().zip(&short).map(|(w, (_, s))| (w.path.clone(), crate::elsewhere::label(w.branch.as_deref(), s, w.head.as_deref()))).collect();
        let trees = listed.into_iter().map(|w| (w.path, w.listed, w.gone)).collect();
        PlaceShown { linked: crate::ledger::linked_top(&top).is_some(), top, branch, trees, main, short, labels, shows: Vec::new(), root: led.root.clone(), cd: String::new() }
    })
}

/// Each worktree's short path (places::short_path), two that would read the same named as git lists them.
fn short_paths(listed: &[crate::elsewhere::Worktree], main: &std::path::Path) -> Vec<(std::path::PathBuf, String)> {
    let label = |w: &crate::elsewhere::Worktree, path: &str| crate::elsewhere::label(w.branch.as_deref(), path, w.head.as_deref());
    let short: Vec<String> = listed.iter().map(|w| crate::places::short_path(&w.listed, &w.path, main)).collect();
    let labels: Vec<String> = listed.iter().zip(&short).map(|(w, s)| label(w, s)).collect();
    listed
        .iter()
        .enumerate()
        .map(|(i, w)| (w.path.clone(), if labels.iter().enumerate().any(|(j, l)| j != i && *l == labels[i]) { w.listed.clone() } else { short[i].clone() }))
        .collect()
}

impl PlaceShown {
    /// A path a receipt keeps, as the user's screens name it: as the place listed there is named, else in short, unless
    /// that would read as a listed place's.
    fn short_of(&self, resolved: &std::path::Path, kept: &str) -> String {
        if let Some((_, s)) = self.short.iter().find(|(p, _)| p == resolved) {
            return s.clone();
        }
        let s = crate::places::short_path(kept, resolved, &self.main);
        if self.short.iter().any(|(_, x)| *x == s) {
            kept.to_string()
        } else {
            s
        }
    }

    /// The id of the place at `resolved`, where a screen can show it and it is still on `branch` (a worktree moved to
    /// another branch since shows other work).
    fn id_of(&self, resolved: &std::path::Path, branch: Option<&str>) -> Option<String> {
        self.shows.iter().find(|(p, _, b)| p == resolved && b.as_deref() == branch).map(|(_, id, _)| id.clone())
    }
}

/// Where a run was recorded, where that is not the place shown: the place as rb's text names it, and as the user's
/// screens do, and the run's `observed_in`.
#[derive(Debug, Clone)]
pub struct Observed {
    /// its branch and path, as git gives them
    pub place: String,
    /// its branch and its path in short, as the user's screens name a place
    pub short: String,
    /// its id, where a screen can show it (it is listed, and holds a record to show)
    pub id: Option<String>,
    /// whether the worktree is removed since
    pub gone: bool,
    /// {label, commit, dirty, gone, label_short, place}
    pub value: Value,
}

/// What a note says after a place removed since, as a run's line says it.
const GONE: &str = " (removed since)";

impl Observed {
    /// The place as a note names it: `short` on the user's screens, with what follows a place removed since.
    fn named(&self, short: bool) -> String {
        format!("{}{}", if short { &self.short } else { &self.place }, if self.gone { GONE } else { "" })
    }
}

/// Where a run was recorded, where its receipt names a worktree other than the place shown: the place's label (its
/// branch and path when recorded, as git lists the path while it is there), and `observed_in` {label, commit, dirty,
/// gone, label_short, place}, the label saying "recorded in <place> at <commit>, with uncommitted changes (removed
/// since)", label_short the same with the place named in short, and place its id where a screen can show it.
pub fn observed_in(run: &Run, shown: &PlaceShown) -> Option<Observed> {
    let at = run.receipt.attached.as_ref()?;
    let Some(path) = at.get("worktree").and_then(|w| w.get("path")).and_then(|p| p.as_str()) else { return observed_on_branch(at, shown) };
    let resolved = crate::sources::resolve_path(std::path::Path::new(path));
    if resolved == shown.top {
        return None;
    }
    let listed = shown.trees.iter().find(|(p, _, _)| *p == resolved);
    // gone as git lists it, or with no checkout there now: one a listing made seconds ago or cut short leaves out is not
    let gone = listed.map_or(false, |(_, _, gone)| *gone) || !resolved.join(".git").exists();
    let shown_path = listed.map(|(_, l, _)| l.clone()).unwrap_or_else(|| path.to_string());
    let branch = at.get("branch").and_then(|b| b.as_str()).filter(|b| !b.is_empty());
    let commit = at.get("commit").and_then(|c| c.as_str()).filter(|c| !c.is_empty());
    let dirty = at.get("dirty").map_or(false, |d| d.truthy());
    let place = crate::elsewhere::label(branch, &shown_path, commit);
    let short = crate::elsewhere::label(branch, &shown.short_of(&resolved, &shown_path), commit);
    let tail = match (branch, commit) {
        (Some(_), Some(c)) => format!(" at {}", c.chars().take(12).collect::<String>()),
        _ => String::new(),
    } + if dirty { ", with uncommitted changes" } else { "" }
        + if gone { GONE } else { "" };
    let id = if gone { None } else { shown.id_of(&resolved, branch) };
    let value = crate::obj! {
        "label" => format!("recorded in {place}{tail}"), "commit" => commit.map(|c| Value::Str(c.to_string())).unwrap_or(Value::Null), "dirty" => dirty,
        "gone" => gone, "label_short" => format!("recorded in {short}{tail}"), "place" => id.clone().map(Value::Str).unwrap_or(Value::Null)
    };
    Some(Observed { place, short, id, gone, value })
}

/// Where a run whose receipt keeps no worktree was recorded, shown at a linked worktree's record: such a receipt was
/// written in the clone's main worktree (a write from a linked one keeps its path) or by a build that kept none, so it
/// says the branch and commit it keeps ("recorded in main at 1cb5fd140e6e"), where that branch is not the place's.
fn observed_on_branch(at: &Dict, shown: &PlaceShown) -> Option<Observed> {
    let branch = at.get("branch").and_then(|b| b.as_str()).filter(|b| !b.is_empty())?;
    let commit = at.get("commit").and_then(|c| c.as_str()).filter(|c| !c.is_empty())?;
    if !shown.linked || shown.branch.as_deref() == Some(branch) {
        return None;
    }
    let dirty = at.get("dirty").map_or(false, |d| d.truthy());
    let label = format!("recorded in {branch} at {}", commit.chars().take(12).collect::<String>()) + if dirty { ", with uncommitted changes" } else { "" };
    // rb cannot tell which checkout such a receipt was written in, so it links to none
    let value = crate::obj! {"label" => label.clone(), "commit" => commit, "dirty" => dirty, "gone" => false, "label_short" => label, "place" => Value::Null};
    Some(Observed { place: branch.to_string(), short: branch.to_string(), id: None, gone: false, value })
}

/// Where each run of the record was recorded, by its id, where that is not the place shown: on the user's own screens
/// only, since a page anyone may be sent names no place.
fn observed_runs(led: &Ledger) -> Res<std::collections::HashMap<String, Observed>> {
    if reader() != Reader::Screen {
        return Ok(std::collections::HashMap::new());
    }
    observed_by_run(led)
}

/// The quiet note on each finding whose counted runs include one recorded in another place than the record's, by its
/// criterion ("" where none is): what rb status says under a finding, the user's own screen as rb open and rb ui are.
pub fn observed_notes(led: &Ledger, findings: &[Value]) -> Res<IndexMap<String, String>> {
    let observed = observed_by_run(led)?;
    Ok(findings.iter().map(|f| (get_or(f, "criterion", ""), observed_note(f, &observed).text)).collect())
}

fn observed_by_run(led: &Ledger) -> Res<std::collections::HashMap<String, Observed>> {
    let runs = led.all::<Run>()?;
    // with no receipt that keeps a worktree, only a linked worktree's record has runs recorded elsewhere (read from files)
    let kept = runs.iter().any(|r| r.receipt.attached.as_ref().map_or(false, |a| a.contains_key("worktree")));
    if !kept && !SHOWN.with(|s| s.borrow().as_ref().map(|p| p.linked)).unwrap_or_else(|| crate::ledger::linked_top(&led.root).is_some()) {
        return Ok(std::collections::HashMap::new());
    }
    let shown = place_shown(led);
    Ok(runs.iter().filter_map(|r| observed_in(r, &shown).map(|o| (r.id.clone(), o))).collect())
}

/// The quiet note on a finding whose counted runs include one recorded in another place, as rb's text says it (`text`:
/// "1 of 2 runs was recorded in exp-b · /p/.worktrees/b") and as the user's screens do (`short`: "... in exp-b ·
/// .worktrees/b"). It asks nothing.
pub struct ObservedNote {
    pub text: String,
    pub short: String,
    /// the places it names that a screen can show: each one's id and its label in short
    pub places: Vec<(String, String)>,
}

/// The counted runs of a finding, by id, each once, in the order it observes them.
fn counted_runs_of(f: &Value) -> Vec<String> {
    let mut runs: Vec<String> = Vec::new();
    for o in list_of(f, "observations").iter().filter(|o| o.get("counted").map_or(false, |c| c.truthy())) {
        let id = get_or(o, "run", "");
        if !id.is_empty() && !runs.contains(&id) {
            runs.push(id);
        }
    }
    runs
}

fn observed_note(f: &Value, observed: &std::collections::HashMap<String, Observed>) -> ObservedNote {
    let none = || ObservedNote { text: String::new(), short: String::new(), places: Vec::new() };
    if observed.is_empty() {
        return none();
    }
    let runs = counted_runs_of(f);
    let mut by: IndexMap<String, (String, usize)> = IndexMap::new();
    let mut places: Vec<(String, String)> = Vec::new();
    for r in &runs {
        if let Some(o) = observed.get(r) {
            by.entry(o.named(false)).or_insert_with(|| (o.named(true), 0)).1 += 1;
            if let Some(id) = o.id.as_ref().filter(|id| !places.iter().any(|(x, _)| x == *id)) {
                places.push((id.clone(), o.short.clone()));
            }
        }
    }
    let (k, total) = (by.values().map(|(_, n)| n).sum::<usize>(), runs.len());
    if k == 0 {
        return none();
    }
    let of = format!("{} {} recorded", crate::assess::k_of_n(k, total, "run", "runs"), if k == 1 { "was" } else { "were" });
    let said = |name: &dyn Fn(&String, &String) -> String| match by.len() {
        1 => by.iter().next().map(|(p, (s, _))| format!("{of} in {}", name(p, s))).unwrap_or_default(),
        _ => format!("{of} elsewhere: {}", by.iter().map(|(p, (s, n))| format!("{n} in {}", name(p, s))).collect::<Vec<_>>().join("; ")),
    };
    ObservedNote { text: said(&|p, _| p.clone()), short: said(&|_, s| s.clone()), places }
}

// ---------------------------------------------------------------- the code a reading's runs ran

/// The code a run ran, where its receipt holds the code that made it: the commit it ran at, and the git tree of its
/// files as they were then (the commit itself where it had no uncommitted changes).
#[derive(Debug, Clone)]
pub(crate) struct RunCode {
    pub(crate) commit: String,
    pub(crate) tree: String,
}

/// What a reading's runs ran, where they were recorded in more than one place and did not all run the same code, as
/// rb's text says it (`text`, each place by its branch and path), as the user's screens do (`short`), and as a page
/// does (`page`, by commits alone): "1 of 2 runs ran other code: exp-b · .worktrees/b at 4364dc8c1a2b, with
/// uncommitted changes". "" in each where they all ran the same code, or rb cannot tell.
#[derive(Debug, Clone, Default)]
pub struct CodeNote {
    pub text: String,
    pub short: String,
    pub page: String,
}

/// Where each run of a record was recorded, when another place than the one shown, and what code each ran (worked
/// out for a run when a note first needs it): what the note on a reading's code is worked out from.
pub struct CodeNotes {
    observed: std::collections::HashMap<String, Observed>,
    runs: std::rc::Rc<Vec<Run>>,
    at: std::collections::HashMap<String, usize>,
    root: std::path::PathBuf,
    codes: std::cell::RefCell<std::collections::HashMap<String, Option<RunCode>>>,
}

/// One run a note counts: its place ("" for the place shown), the code it ran (a key that is the same for the same
/// code), its commit, and whether it had changes its commit does not hold.
struct Ran<'a> {
    run: &'a String,
    place: String,
    key: String,
    commit: String,
    dirty: bool,
}

impl CodeNotes {
    /// Of `led`'s record, read once: nothing when no run was recorded in another place, and then no git call.
    pub fn of(led: &Ledger) -> Res<CodeNotes> {
        let observed = observed_by_run(led)?;
        let runs = if observed.is_empty() { Default::default() } else { led.all::<Run>()? };
        let at = runs.iter().enumerate().map(|(i, r)| (r.id.clone(), i)).collect();
        Ok(CodeNotes { observed, runs, at, root: led.root.clone(), codes: Default::default() })
    }

    /// The note on a reading that counts `runs` (by id), as the reader of what is being built says it.
    pub fn said(&self, runs: &[String]) -> String {
        let n = self.note(runs);
        match reader() {
            Reader::Text => n.text,
            Reader::Screen => n.short,
            Reader::Page => n.page,
        }
    }

    fn code(&self, run: &str) -> Option<RunCode> {
        self.codes.borrow_mut().entry(run.to_string()).or_insert_with(|| self.at.get(run).and_then(|i| code_of(&self.runs[*i], &self.root))).clone()
    }

    /// The note on a reading that counts `runs` (by id), where one of them was recorded in another place than the one
    /// shown. Other code is code no run recorded here ran; with none recorded here, code no run of the place that holds
    /// the most ran, and where two places hold the most, how the runs' code falls apart is said. It says nothing where
    /// any of the runs has a receipt that does not hold the code that made it (a run made before it was recorded keeps
    /// the recording checkout's code) or that rb cannot read the code from.
    pub fn note(&self, runs: &[String]) -> CodeNote {
        if !runs.iter().any(|r| self.observed.contains_key(r)) {
            return CodeNote::default();
        }
        let mut known: Vec<(&String, RunCode)> = Vec::new();
        for r in runs {
            match self.code(r) {
                Some(c) => known.push((r, c)),
                None => return CodeNote::default(),
            }
        }
        // what any of the runs read or wrote is no run's code
        let named: std::collections::HashSet<String> = known.iter().filter_map(|(r, _)| self.at.get(*r)).flat_map(|i| named_paths(&self.runs[*i], &self.root)).collect();
        let place_of = |r: &String| self.observed.get(r).map(|o| o.named(false)).unwrap_or_default();
        let mut ran: Vec<Ran> = Vec::new();
        for (r, c) in &known {
            match code_key(&self.root, c, &named) {
                Some((key, dirty)) => ran.push(Ran { run: r, place: place_of(r), key, commit: c.commit.clone(), dirty }),
                None => return CodeNote::default(),
            }
        }
        // the places by how many of the runs each holds
        let mut held: IndexMap<String, usize> = IndexMap::new();
        for x in &ran {
            *held.entry(x.place.clone()).or_default() += 1;
        }
        if held.len() < 2 || ran.iter().all(|x| x.key == ran[0].key) {
            return CodeNote::default();
        }
        let most = held.values().copied().max().unwrap_or(0);
        let tops: Vec<&String> = held.iter().filter(|(_, n)| **n == most).map(|(p, _)| p).collect();
        let reference = if held.contains_key("") { Some(String::new()) } else if tops.len() == 1 { Some(tops[0].clone()) } else { None };
        let short_of = |r: &String| self.observed.get(r).map(|o| o.named(true)).unwrap_or_default();
        let at = |commit: &str, dirty: bool| commit.chars().take(12).collect::<String>() + if dirty { ", with uncommitted changes" } else { "" };
        let k = ran.len();
        let Some(p) = reference else { return tied(&ran, &short_of, &at) };
        let same: Vec<&String> = ran.iter().filter(|x| x.place == p).map(|x| &x.key).collect();
        let other: Vec<&Ran> = ran.iter().filter(|x| !same.contains(&&x.key)).collect();
        if other.is_empty() {
            return CodeNote::default();
        }
        // said by place, commit and whether there were uncommitted changes; a page names no place, so by commit alone
        let mut groups: IndexMap<(String, String, bool), (String, usize)> = IndexMap::new();
        let mut paged: IndexMap<(String, bool), usize> = IndexMap::new();
        for x in &other {
            groups.entry((x.place.clone(), x.commit.clone(), x.dirty)).or_insert_with(|| (short_of(x.run), 0)).1 += 1;
            *paged.entry((x.commit.clone(), x.dirty)).or_default() += 1;
        }
        let lead = format!("{} of {k} runs ran other code", other.len());
        let one = groups.len() == 1;
        let named = |name: &dyn Fn(&(String, String, bool), &String) -> String| -> String {
            let parts: Vec<String> = groups.iter().map(|(key, (short, m))| if one { name(key, short) } else { format!("{m} in {}", name(key, short)) }).collect();
            format!("{lead}: {}", parts.join("; "))
        };
        let page = if paged.len() == 1 {
            paged.keys().next().map(|(c, d)| format!("{lead}: commit {}", at(c, *d))).unwrap_or_default()
        } else {
            format!("{lead}: {}", paged.iter().map(|((c, d), m)| format!("{m} at commit {}", at(c, *d))).collect::<Vec<_>>().join("; "))
        };
        CodeNote {
            text: named(&|key, _| format!("{} at {}", key.0, at(&key.1, key.2))),
            short: named(&|key, short| format!("{short} at {}", at(&key.1, key.2))),
            page,
        }
    }
}

/// The note on runs from places that hold as many of them each, none of them the place shown, which did not all run
/// the same code: the runs by the code they ran, each code at its commit, with the places that ran it.
/// "3 runs did not all run the same code: 2 at 1443886b14f9 (1 in exp-b · .worktrees/b, 1 in exp-c · .worktrees/c);
/// 1 in exp-d · .worktrees/d at b2c7d62c0416"; "2 runs ran different code: ..." where no two ran the same.
fn tied(ran: &[Ran], short_of: &dyn Fn(&String) -> String, at: &dyn Fn(&str, bool) -> String) -> CodeNote {
    let mut codes: IndexMap<&String, IndexMap<(String, bool), IndexMap<String, (String, usize)>>> = IndexMap::new();
    for x in ran {
        let places = codes.entry(&x.key).or_default().entry((x.commit.clone(), x.dirty)).or_default();
        places.entry(x.place.clone()).or_insert_with(|| (short_of(x.run), 0)).1 += 1;
    }
    let k = ran.len();
    let lead = if codes.len() == k { format!("{k} runs ran different code") } else { format!("{k} runs did not all run the same code") };
    let said = |name: &dyn Fn(&String, &String) -> String| -> String {
        let parts: Vec<String> = codes
            .values()
            .map(|commits| {
                let each: Vec<String> = commits
                    .iter()
                    .map(|((c, d), places)| {
                        let m: usize = places.values().map(|(_, m)| m).sum();
                        if places.len() == 1 {
                            places.iter().map(|(p, (s, _))| format!("{m} in {} at {}", name(p, s), at(c, *d))).collect()
                        } else {
                            let ins: Vec<String> = places.iter().map(|(p, (s, n))| format!("{n} in {}", name(p, s))).collect();
                            format!("{m} at {} ({})", at(c, *d), ins.join(", "))
                        }
                    })
                    .collect();
                each.join(", ")
            })
            .collect();
        format!("{lead}: {}", parts.join("; "))
    };
    let page: Vec<String> = codes
        .values()
        .map(|commits| commits.iter().map(|((c, d), places)| format!("{} at commit {}", places.values().map(|(_, m)| m).sum::<usize>(), at(c, *d))).collect::<Vec<_>>().join(", "))
        .collect();
    CodeNote { text: said(&|p, _| p.clone()), short: said(&|_, s| s.clone()), page: format!("{lead}: {}", page.join("; ")) }
}

/// The code a run ran, where its receipt holds the code that made it (RunCode): the commit and tree rb's journal saw
/// it run at, where it watched the command make the run's numbers; otherwise the repository as the run was recorded,
/// which a run made before it was recorded does not hold (it keeps the recording checkout's code), nor does a receipt
/// that says it was made at another commit or code than the checkout it was recorded in, one that names no commit or
/// keeps no tree of its uncommitted changes, or one that left a file out of that tree.
pub(crate) fn code_of(run: &Run, root: &std::path::Path) -> Option<RunCode> {
    let named = |d: &Dict, k: &str| d.get(k).and_then(Value::as_str).filter(|v| !v.is_empty()).map(str::to_string);
    let produced = &run.receipt.produced;
    let made = named(produced, "commit").map(|c| RunCode { tree: named(produced, "tree").unwrap_or_else(|| c.clone()), commit: c });
    if let Some(m) = made.as_ref().filter(|_| crate::ledger::support::run_command(run).map_or(false, |g| g.given == "observed")) {
        return Some(m.clone());
    }
    if crate::ledger::made_before_recorded(run) {
        return None;
    }
    let at = run.receipt.attached.as_ref()?;
    let commit = named(at, "commit")?;
    let recorded = if at.get("dirty").map_or(false, |d| d.truthy()) {
        if at.get("left_out").map_or(false, |n| n.truthy()) {
            return None;
        }
        RunCode { tree: named(at, "tree")?, commit }
    } else {
        RunCode { tree: commit.clone(), commit }
    };
    // what the run says it was made at must be what it was recorded at, the files it names aside
    if named(produced, "tree").is_some() && made.is_none() {
        return None;
    }
    match made {
        Some(m) if m.commit != recorded.commit => None,
        Some(m) => {
            let own: std::collections::HashSet<String> = named_paths(run, root).collect();
            (code_key(root, &m, &own)? == code_key(root, &recorded, &own)?).then_some(recorded)
        }
        None => Some(recorded),
    }
}

/// The paths, from the top of a checkout, of the files a run names (what its numbers were read from, its artifacts,
/// a row's database), as its own checkout holds them: what it read or wrote, not code.
fn named_paths<'a>(run: &'a Run, root: &'a std::path::Path) -> impl Iterator<Item = String> + 'a {
    let top = crate::sources::git_top(root);
    let own = run.receipt.attached.as_ref().and_then(|a| a.get("worktree")).and_then(|w| w.get("path")).and_then(Value::as_str).map(|p| crate::sources::resolve_path(std::path::Path::new(p)));
    run.files.iter().flat_map(move |f| {
        let full = crate::sources::resolve_path(&root.join(&f.path));
        [top.as_ref(), own.as_ref()].into_iter().flatten().filter_map(|base| crate::sources::rel_to(&full, base)).collect::<Vec<_>>()
    })
}

/// What a run's code is, as a key that is the same for the same code, and whether it differs from its commit's: each
/// file its commit holds, as it was when the run ran. The record's own folder and the files `named` are left out, and
/// so is a file its commit does not hold, which may be one a run wrote. None where this clone does not have the
/// commit or the tree.
fn code_key(root: &std::path::Path, c: &RunCode, named: &std::collections::HashSet<String>) -> Option<(String, bool)> {
    let top = crate::sources::git_top(root)?;
    // the record's folder from the repository's top: ".rb", or "sub/.rb" for a record below it
    let record = crate::sources::rel_to(&crate::sources::resolve_path(&root.join(crate::ledger::DIR)), &top)?;
    let base = tree_files(&top, &c.commit)?;
    let then = if c.tree == c.commit { base.clone() } else { tree_files(&top, &c.tree)? };
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    let mut dirty = false;
    let inside = format!("{record}/");
    for (path, entry) in base.iter() {
        if *path == record || path.starts_with(&inside) || named.contains(path) {
            continue;
        }
        let was = then.get(path);
        dirty |= was != Some(entry);
        h.update(path.as_bytes());
        h.update(b"\0");
        h.update(was.map_or(&b"-"[..], |e| e.as_bytes()));
        h.update(b"\0");
    }
    Some((crate::rbyaml::hex(&h.finalize()), dirty))
}

/// Each file a commit or a tree holds, by its path from the top, with its mode and object. None where this clone does
/// not have it. Kept for as long as rb runs once git has given it: what a commit holds never changes.
fn tree_files(top: &std::path::Path, treeish: &str) -> Option<std::sync::Arc<std::collections::BTreeMap<String, String>>> {
    type Files = std::sync::Arc<std::collections::BTreeMap<String, String>>;
    static KEPT: std::sync::Mutex<Option<std::collections::HashMap<(std::path::PathBuf, String), Files>>> = std::sync::Mutex::new(None);
    let key = (top.to_path_buf(), treeish.to_string());
    if let Some(f) = KEPT.lock().unwrap_or_else(|e| e.into_inner()).as_ref().and_then(|m| m.get(&key).cloned()) {
        return Some(f);
    }
    let listed = crate::sources::git_bytes(top, &["ls-tree", "-r", "-z", "--full-tree", &format!("{treeish}^{{tree}}")])?;
    let mut files = std::collections::BTreeMap::new();
    for entry in listed.split(|b| *b == 0).filter(|e| !e.is_empty()) {
        let tab = entry.iter().position(|b| *b == b'\t')?;
        files.insert(String::from_utf8_lossy(&entry[tab + 1..]).into_owned(), String::from_utf8_lossy(&entry[..tab]).into_owned());
    }
    let files: Files = std::sync::Arc::new(files);
    KEPT.lock().unwrap_or_else(|e| e.into_inner()).get_or_insert_with(Default::default).insert(key, files.clone());
    Some(files)
}

/// The note on each finding's code, by its criterion ("" where none is), as rb's text says it: what rb status says
/// under a finding, beside where its runs were recorded.
pub fn code_notes(led: &Ledger, findings: &[Value]) -> Res<IndexMap<String, String>> {
    let notes = CodeNotes::of(led)?;
    Ok(findings.iter().map(|f| (get_or(f, "criterion", ""), notes.note(&counted_runs_of(f)).text)).collect())
}

/// Whether the user's own words may be said here: everywhere but a page anyone may be sent.
pub fn words_shown() -> bool {
    reader() != Reader::Page
}

/// Whether the words given with a write read as the user's own: they gave them in their own terminal, which a write
/// recorded outside an agent session is, a confirmation of a call included. Words an agent session gave claim the
/// user said them, which rb cannot know.
pub fn words_are_theirs(by: &str) -> bool {
    by.starts_with("human:")
}

/// The words given with a write by `by`, already quoted, as what is being built says them. The user's own: after
/// `your words:` in rb's text, alone on the user's own screens. Words an agent session gave: after `words (as given):`
/// everywhere, never as the user's. A page anyone may be sent says neither.
pub fn say_words(quoted: &str, by: &str) -> String {
    match reader() {
        Reader::Page => String::new(),
        _ if !words_are_theirs(by) => format!("words (as given): {quoted}"),
        Reader::Text => format!("your words: {quoted}"),
        Reader::Screen => quoted.to_string(),
    }
}

/// The words given with a write by `by`, whole, verbatim, in straight quotes, as `say_words` says them, on one line.
pub fn your_words(words: &str, by: &str) -> String {
    if words.is_empty() {
        String::new()
    } else {
        say_words(&format!("\"{}\"", on_one_line(words)), by)
    }
}

/// Text as a screen line holds it: each line break, with the spaces around it, as one space. The record and the JSON
/// keep the breaks.
pub fn on_one_line(text: &str) -> String {
    if !text.contains(['\n', '\r']) {
        return text.to_string();
    }
    text.split(['\n', '\r']).map(str::trim).filter(|l| !l.is_empty()).collect::<Vec<_>>().join(" ")
}

/// A line that ends on the reason the record gives, followed by the words given with the write by `by`.
pub fn with_words(text: &str, words: &str, by: &str) -> String {
    match your_words(words, by) {
        said if said.is_empty() => text.to_string(),
        said => format!("{text} · {said}"),
    }
}

// ---------------------------------------------------------------- dict access, as the Python reads it

/// `d.get(k, default)` put in an f-string: the stored value's text, or the default when the key is missing.
fn get_or(d: &Value, k: &str, default: &str) -> String {
    match d.get(k) {
        Some(v) => py_str(v),
        None => default.to_string(),
    }
}

/// `d.get(k) or default`: the value's text when it is truthy.
fn get_truthy(d: &Value, k: &str) -> Option<String> {
    d.get(k).filter(|v| v.truthy()).map(py_str)
}

/// The notes a record's data lists that are not taken back: what a screen lists.
fn live_notes(d: &Value) -> impl Iterator<Item = &Value> {
    list_of(d, "notes").iter().filter(|n| !n.get("retracted").map_or(false, |r| r.truthy()))
}

/// A note as a screen names it: `note <id>`, by which `rb retract` takes it back.
pub fn note_label(id: Option<&str>) -> String {
    match id.filter(|i| !i.is_empty()) {
        Some(id) => format!("note {id}"),
        None => "note".to_string(),
    }
}

fn list_of<'a>(d: &'a Value, k: &str) -> &'a [Value] {
    match d.get(k) {
        Some(Value::List(l)) => l,
        _ => &[],
    }
}

/// A card's list cut to its first `k` lines, then one saying how many more.
fn more(lines: &mut Vec<String>, k: usize) {
    if lines.len() > k {
        let n = lines.len() - k;
        lines.truncate(k);
        lines.push(format!("and {n} more"));
    }
}

// ---------------------------------------------------------------- the brief

/// One criterion on a human surface: the statement verbatim, and the instrument row under it.
#[derive(Debug, Clone, Default)]
pub struct FindingLine {
    pub criterion_id: String,
    pub statement: String,
    pub numbers: String,
    pub row: Option<Row>,
    pub sentence: String,
    pub standing: String,
    pub good: bool,
    pub rule: String,
    pub role: String,
    pub says: Value,
    pub your_words: String,
    /// on the user's own screens, where counted runs were recorded in another place: one quiet note
    pub observed: String,
    /// the same note with each place named in short, as the user's screens name a place
    pub observed_short: String,
    /// the places that note names which a screen can show: {id, label}, label as observed_short says it
    pub observed_places: Vec<(String, String)>,
    /// where its counted runs, recorded in more than one place, did not all run the same code: one quiet note, the
    /// place in short on the user's screens and by commit alone on a page
    pub other_code: String,
}

#[derive(Debug, Clone, Default)]
pub struct ExperimentBrief {
    pub id: String,
    pub title: String,
    /// its title as a navigator line and a paired card name it (short_title)
    pub short_title: String,
    pub findings: Vec<FindingLine>,
    pub scope: Option<String>,
    pub counted: i64,
    pub table: Option<Dict>,
    pub arms: String,
    pub varies: Vec<String>,
    pub state: String,
    pub hypothesis: String,
    pub if_holds: String,
    pub if_not: String,
    /// the words given when the gate was set, and who gave them
    pub gate_words: String,
    pub gate_by: String,
    pub purpose: String,
    pub why: String,
    /// who gave the why, which says how a screen says it (`why_part`); never on a screen itself
    pub why_by: String,
    pub notes: Vec<String>,
    /// each note's short id, beside `notes`, so a screen can name the one to retract
    pub note_ids: Vec<String>,
    pub relations: Vec<String>,
    pub rerun: Vec<Dict>,
    pub runs: i64,
    pub folder: String,
    pub said: Vec<Dict>,
    pub repo: Vec<String>,
    pub repo_brief: String,
    /// its design's standing (`Design::state`), and what its own view says of it: the standing line with its command
    /// while it waits, and what changed since the user confirmed it
    pub design_standing: String,
    pub design_lines: Vec<String>,
    /// the command that confirms its design while it waits, else nothing
    pub design_confirm: String,
    /// whether its gate's mark goes on its first branch, the only one a session wrote (`Design::gate_first`)
    pub gate_first: bool,
    /// what a design a session wrote any of does not record ("hypothesis or purpose", "why")
    pub not_recorded: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct QuestionBrief {
    pub id: Option<String>,
    pub text: String,
    pub status: String,
    pub experiments: Vec<ExperimentBrief>,
    pub changed: Vec<String>,
    pub still_open: Vec<String>,
    pub next: Vec<String>,
    pub ruled: Vec<String>,
    pub depth: usize,
    pub parent: Option<String>,
    pub rollup: String,
    pub inquiry: Value,
    pub inquiry_findings: Vec<FindingLine>,
    /// A bounded human overview. Authored interpretations and evidence qualifications stay distinct.
    pub overview: Value,
    pub summary: Vec<String>,
}

/// Whether an item of rb status's list is a call waiting on you: the user's, and not one that waits for a confirmation.
fn waits_on_you(i: &Value) -> bool {
    get_or(i, "who", "") == "human" && !crate::ledger::PENDING_CODES.contains(&get_or(i, "code", "").as_str())
}

/// A call waiting on you, as a screen lists it: what it asks (its label, and the title of what it is on), why, and the
/// command that makes it.
fn call_value(i: &Value, words: &IndexMap<String, String>) -> Value {
    let subject = get_or(i, "subject", "");
    let label = match get_or(i, "code", "").as_str() {
        "decision_outdated" => "Revisit the earlier call",
        "gate_missed" => "Choose whether this work continues",
        "investigating" => "Review the investigation",
        _ => "Review this result",
    };
    crate::obj! {"subject" => subject.clone(), "label" => label, "title" => words.get(&subject).cloned().unwrap_or(subject), "text" => get_or(i, "what", ""), "command" => get_or(i, "do", "")}
}

/// What a screen calls each criterion, experiment and question by: its statement, title or text.
fn subject_words(stv: &Value) -> IndexMap<String, String> {
    let mut words: IndexMap<String, String> = IndexMap::new();
    for x in list_of(stv, "findings") {
        let id = get_or(x, "criterion", "");
        words.insert(id.clone(), get_or(x, "statement", &id));
    }
    for x in list_of(stv, "experiments") {
        let id = get_or(x, "id", "");
        words.insert(id.clone(), get_or(x, "title", &id));
    }
    for x in list_of(stv, "questions") {
        let id = get_or(x, "id", "");
        words.insert(id.clone(), get_or(x, "text", &id));
    }
    words
}

/// The subject an item of rb status's list for the user is decided on: a missed gate's experiment, as its command
/// decides it, else the item's own (the first, where rb status lists several in one item), with the criterion it is on.
fn decided_on(led: &Ledger, i: &Value) -> Res<(String, Option<Criterion>)> {
    let item = get_or(i, "subject", "");
    let first = item.split(", ").next().unwrap_or("").to_string();
    let crit: Option<Criterion> = if led.kind_of(&first)? == Some("criterion") { Some(led.load::<Criterion>(&first)?) } else { None };
    let on = match (get_or(i, "code", "").as_str(), &crit) {
        ("gate_missed", Some(c)) => c.experiment.clone(),
        _ => item,
    };
    Ok((on, crit))
}

/// How many calls wait on you: the subjects rb status asks you to decide, each once (D2), as Your call lists them.
pub fn calls_waiting(led: &Ledger, st: &Value) -> Res<usize> {
    let mut seen: HashSet<String> = HashSet::new();
    for i in list_of(st, "open").iter().filter(|i| waits_on_you(i)) {
        seen.insert(decided_on(led, i)?.0);
    }
    Ok(seen.len())
}

/// Each call waiting on you once (D2: the user's calls alone, never what rb found or what waits for a confirmation),
/// one per subject it is decided on, in rb status's order: what it asks and on what (`subject`, `kind`, `label`,
/// `title`), rb's words for it (`text`, and `what`) and its command (`command`), where it is decided (`at`, and
/// `question`, `decided_at`), rb's code, why rb asks (`reason`, SHELL-SPEC 15.5) and the calls it offers (`actions`).
/// Where rb status lists more than one item on a subject (a call made on an earlier reading under investigation, two
/// missed gates of one experiment), the call is the first item's, offers every item's actions, the first's first, and
/// says the others' reasons after its own (`also`); a missed gate's reason says each gate's comparison, "; " apart.
fn waiting_calls(led: &Ledger, st: &Value, briefs: &[QuestionBrief], loose: &[String]) -> Res<Vec<Value>> {
    let words = subject_words(st);
    let mut calls: IndexMap<String, Value> = IndexMap::new();
    for i in list_of(st, "open").iter().filter(|i| waits_on_you(i)) {
        let (on, crit) = decided_on(led, i)?;
        let (item, text, code) = (get_or(i, "subject", ""), get_or(i, "what", ""), get_or(i, "code", ""));
        // the criteria it is on: a missed gate's, each one rb status lists in the item
        let mut on_criteria: Vec<(Criterion, Finding)> = Vec::new();
        for id in item.split(", ") {
            if led.kind_of(id)? == Some("criterion") {
                let c = led.load::<Criterion>(id)?;
                let f = led.finding(&c)?;
                on_criteria.push((c, f));
            }
        }
        let mut reason = crate::ledger::views::call_reason(&code, &text, on_criteria.first().map(|(c, f)| (c, f)));
        if code == "gate_missed" && on_criteria.len() > 1 {
            reason.detail = on_criteria.iter().map(|(c, f)| format!("{}: {}", c.id, f.says.get("comparison").cloned().unwrap_or_default())).collect::<Vec<_>>().join("; ");
        }
        let last = led.decisions_on(&on)?.pop().map(|d| d.action);
        let kind = led.kind_of(&on)?.unwrap_or("");
        let actions: Vec<Value> = crate::ledger::views::call_actions(&code, kind, &on, last.as_deref())
            .into_iter()
            .map(|a| crate::obj! {"action" => a.action, "label" => a.label, "argv" => Value::list_of_str(a.argv), "line" => a.line, "consequence" => a.consequence})
            .collect();
        if let Some(Value::Dict(first)) = calls.get_mut(&on) {
            // one call settles every item on its subject: it offers each one's actions and says each one's reason
            if let Some(Value::List(had)) = first.get_mut("actions") {
                for a in actions {
                    if !had.iter().any(|h| h.get("action") == a.get("action")) {
                        had.push(a);
                    }
                }
            }
            if let Some(Value::Dict(r)) = first.get_mut("reason") {
                let more = [reason.label, reason.detail].into_iter().filter(|x| !x.is_empty()).collect::<Vec<_>>().join(" · ");
                let also = get_or(&Value::Dict(r.clone()), "also", "");
                r.insert("also".into(), (if also.is_empty() { more } else { format!("{also} · {more}") }).into());
            }
            continue;
        }
        let mut call = call_value(i, &words);
        let question = decided_at(led, briefs, loose, &on, crit.as_ref(), Some((&item, &text)))?;
        if let Value::Dict(d) = &mut call {
            d.insert("subject".into(), on.clone().into());
            d.insert("title".into(), words.get(&on).cloned().unwrap_or_else(|| on.clone()).into());
            d.insert("at".into(), question.clone().into());
            d.insert("kind".into(), kind.into());
            d.insert("code".into(), code.into());
            d.insert("question".into(), question.into());
            d.insert("what".into(), text.into());
            d.insert("reason".into(), crate::obj! {"label" => reason.label, "detail" => reason.detail, "also" => reason.also});
            d.insert("actions".into(), Value::List(actions));
        }
        calls.insert(on, call);
    }
    Ok(calls.into_values().collect())
}

/// Where a call on `subject` is decided: with the work linked to no question (":unfiled") where its criterion is
/// (`loose`); else the inquiry whose Your call lists it (`listed`: rb status's item subject and words), a
/// sub-question's before the parent that lists its calls too; else where its subject is filed, or the experiment of
/// its setting or criterion; else ":unfiled".
pub fn decided_at(led: &Ledger, briefs: &[QuestionBrief], loose: &[String], subject: &str, crit: Option<&Criterion>, listed: Option<(&str, &str)>) -> Res<String> {
    let crit = match crit {
        Some(c) => Some(c.clone()),
        None if led.kind_of(subject)? == Some("criterion") => Some(led.load::<Criterion>(subject)?),
        None => None,
    };
    if crit.as_ref().map_or(false, |c| loose.contains(&c.id)) || loose.iter().any(|c| c == subject) {
        return Ok(":unfiled".into());
    }
    let mut at: Option<&QuestionBrief> = None;
    if let Some((item, text)) = listed {
        for b in briefs {
            let lists = list_of(&b.overview, "calls").iter().any(|c| get_or(c, "subject", "") == item && get_or(c, "text", "") == text);
            if lists && at.map_or(true, |a| b.depth > a.depth) {
                at = Some(b);
            }
        }
    }
    if let Some(b) = at {
        return Ok(b.id.clone().unwrap_or_else(|| ":unfiled".into()));
    }
    // a setting is its experiment's, as `<exp>/<name>` says
    let experiment = crit.as_ref().map(|c| c.experiment.clone()).unwrap_or_else(|| subject.split('/').next().unwrap_or("").to_string());
    for s in [subject, experiment.as_str()] {
        let q = filed_at(briefs, s);
        if !q.is_empty() {
            return Ok(q);
        }
    }
    Ok(":unfiled".into())
}

/// The question whose page holds a subject: the deepest that holds it among its findings or experiments, or is it
/// (":unfiled" for work filed under none, empty where none holds it).
pub fn filed_at(briefs: &[QuestionBrief], subject: &str) -> String {
    let mut at: Option<&QuestionBrief> = None;
    for b in briefs {
        let holds = b.id.as_deref() == Some(subject) || b.inquiry_findings.iter().any(|f| f.criterion_id == subject) || b.experiments.iter().any(|e| e.id == subject);
        if holds && at.map_or(true, |a| b.depth > a.depth) {
            at = Some(b);
        }
    }
    at.map(|b| b.id.clone().unwrap_or_else(|| ":unfiled".into())).unwrap_or_default()
}

/// What is still open in the record and not the user's to call (rb status's items for the agent or anyone), grouped
/// by rb's code in the order rb lists them, each group headed in rb's words (`ledger::views::open_head`), each item
/// with its subject, code, rb's words, its criteria and, for a late item, how many of each criterion's units came
/// before it was declared; and work linked to no question apart (`unfiled`): its criteria (`loose_criteria`), the
/// calls on it, and its open items.
fn open_groups(st: &Value, loose: &[String], calls: &[Value]) -> (Vec<Value>, Value) {
    let findings: IndexMap<String, &Value> = list_of(st, "findings").iter().map(|f| (get_or(f, "criterion", ""), f)).collect();
    let variants: IndexMap<String, Vec<String>> = list_of(st, "experiments")
        .iter()
        .map(|e| (get_or(e, "id", ""), list_of(e, "variants").iter().map(|v| get_or(v, "name", "")).collect()))
        .collect();
    // each group's items, one line each, as a checklist counts them
    let mut groups: IndexMap<String, Vec<Value>> = IndexMap::new();
    let mut unfiled_items = Vec::new();
    for i in list_of(st, "open").iter().filter(|i| get_or(i, "who", "") != "human") {
        let code = get_or(i, "code", "");
        for v in plain_open_items(i, &findings, &variants) {
            let criteria: Vec<String> = list_of(&v, "criteria").iter().map(py_str).collect();
            if !criteria.is_empty() && criteria.iter().all(|c| loose.contains(c)) {
                unfiled_items.push(v);
            } else {
                groups.entry(code.clone()).or_default().push(v);
            }
        }
    }
    let grouped = groups
        .into_iter()
        .map(|(code, items)| {
            let (head, under) = crate::ledger::views::open_head(&code);
            crate::obj! {"code" => code, "head" => head, "n" => items.len(), "under" => under, "items" => Value::List(items)}
        })
        .collect();
    let unfiled = crate::obj! {
        "line" => UNFILED, "criteria" => strs(loose),
        "calls" => Value::list_of_str(calls.iter().filter(|c| get_or(c, "question", "") == ":unfiled").map(|c| get_or(c, "subject", ""))),
        "open_items" => Value::List(unfiled_items)
    };
    (grouped, unfiled)
}

/// rb status's open item `i` as the screens list it (DESIGN-PASS-1 target 2): one short plain sentence per thing, its
/// subject (the id a screen shows as a trailing chip and links) apart, never the sentence's first word; a criterion's
/// item one per criterion, by its statement. rb's own sentence stays in rb status
/// and on the thing's own view.
fn plain_open_items(i: &Value, findings: &IndexMap<String, &Value>, variants: &IndexMap<String, Vec<String>>) -> Vec<Value> {
    let (code, subject, what) = (get_or(i, "code", ""), get_or(i, "subject", ""), get_or(i, "what", ""));
    let mut criteria: Vec<String> = list_of(i, "criteria").iter().map(py_str).collect();
    if criteria.is_empty() {
        criteria = subject.split(", ").filter(|c| findings.contains_key(*c)).map(str::to_string).collect();
    }
    let item = |subject: &str, what: String, units: String, criteria: Vec<String>| crate::obj! {"subject" => subject, "code" => code.clone(), "what" => what, "units" => units, "criteria" => strs(&criteria)};
    // a statement as every line leads with it: the words given that are it, said once as given (finding_statement)
    let statement = |c: &str| findings.get(c).map(|f| finding_statement(f).0).filter(|s| !s.is_empty()).unwrap_or_else(|| c.to_string());
    let so_far = |c: &str| {
        let u = findings.get(c).and_then(|f| f.get("units"));
        let (n, runs) = (u.and_then(|u| u.get("n")).and_then(Value::as_i64).unwrap_or(0), u.and_then(|u| u.get("runs")).and_then(Value::as_i64).unwrap_or(0));
        let unit = u.map(|u| get_or(u, "unit", "")).unwrap_or_default();
        if n == runs { format!("{n} {} so far", crate::assess::plural("run", n)) } else { format!("{n} {} so far", crate::ledger::views::unit_noun(&unit, n)) }
    };
    // rb's rule sentence for criterion `c` (its finding's says.rule), which says why rb cannot judge it yet
    let rule_says = |c: &str| findings.get(c).and_then(|f| f.get("says")).map(|s| get_or(s, "rule", "")).unwrap_or_default();
    let per_criterion = matches!(
        code.as_str(),
        "untested" | "inherited" | "too_few_units" | "no_interval" | "direction_unknown" | "late" | "timing_unknown" | "unassigned_criterion" | "experiment_retracted"
    );
    if per_criterion && !criteria.is_empty() {
        return criteria
            .iter()
            .map(|c| {
                let stmt = statement(c);
                let said = match code.as_str() {
                    // the reason as rb's rule sentence gives it: fixed design cells give no interval however many
                    // runs there are; an unpaired reading needs 3 in each arm; else 3 units
                    "no_interval" => {
                        let says = rule_says(c);
                        if says.contains("fixed design cells") {
                            format!("{stmt}: read across fixed design cells, which give no interval")
                        } else if says.contains("in each") {
                            format!("{stmt}: {}; an interval needs 3 in each arm", so_far(c))
                        } else {
                            format!("{stmt}: {}; an interval needs 3", so_far(c))
                        }
                    }
                    "direction_unknown" => format!("{stmt}: its metric has no better direction recorded"),
                    "too_few_units" => {
                        let needs = what.split("needs ").nth(1).and_then(|t| t.split(|ch: char| !ch.is_ascii_digit()).next()).filter(|d| !d.is_empty());
                        match needs {
                            Some(k) => format!("{stmt}: {}; the rule needs {k}", so_far(c)),
                            None => format!("{stmt}: {}", so_far(c)),
                        }
                    }
                    // written after some of its results came in, or after its data was seen
                    "late" => match findings.get(c).map(|f| units_before(f)) {
                        Some((k, n)) if k > 0 => format!("{stmt}: written after {k} of its {n} {} came in", crate::assess::plural("result", n as i64)),
                        _ => format!("{stmt}: written after its data was seen"),
                    },
                    "timing_unknown" => format!("{stmt}: when its results came in is unknown"),
                    "unassigned_criterion" => format!("{stmt}: linked to no question, so no question's answer reads it"),
                    "experiment_retracted" => format!("{stmt}: its experiment was taken back"),
                    _ => stmt,
                };
                item(c, said, String::new(), vec![c.clone()])
            })
            .collect();
    }
    match code.as_str() {
        "provisional" => {
            let (exp, name) = subject.split_once('/').unwrap_or(("", subject.as_str()));
            let value = what.split_once(&format!("{name} = ")).and_then(|(_, t)| t.split_once(" is provisional")).map(|(v, _)| v.to_string()).unwrap_or_default();
            let after = |mark: &str| what.split_once(mark).map(|(_, t)| t.trim_end_matches(')').trim_end_matches(", not checkable").to_string());
            // a check rb made that failed is said as one, never as no check
            let given = if value.is_empty() { String::new() } else { format!("given as {value}") };
            let said = if what.contains("does not state it") {
                format!("{given}; its source does not state it.")
            } else {
                let source = if let Some(note) = after("(source note: ") {
                    let stop = if note.ends_with(['.', '!', '?']) { "" } else { "." };
                    format!(" Its note: {note}{stop}")
                } else if let Some(src) = after("(source ") {
                    format!(" Its source, {src}, is not one rb can check.")
                } else {
                    String::new()
                };
                format!("{given}{}not checked against a source.{source}", if given.is_empty() { "" } else { ", " })
            };
            vec![item(&subject, format!("{} in {exp}: {said}", setting_words(name, variants.get(exp))), String::new(), Vec::new())]
        }
        "unknown" | "unknown_optional" if subject.contains('/') => {
            let (exp, name) = subject.split_once('/').unwrap_or_default();
            vec![item(&subject, format!("{} in {exp}: no value yet", setting_words(name, variants.get(exp))), String::new(), Vec::new())]
        }
        "unindexed_folder" => {
            let folders: Vec<String> = list_of(i, "folders").iter().map(py_str).collect();
            let said = match folders.len() {
                0 => subject.clone(),
                1 => format!("1 folder: {}", folders[0]),
                n => format!("{n} folders: {}", crate::assess::first_few(&folders, 3, ", ")),
            };
            vec![item(&subject, said, String::new(), Vec::new())]
        }
        "command_later" => {
            let n = list_of(i, "records").len().max(1);
            let said = if n == 1 { "1 run was recorded after it ran; nothing rb saw shows the command that made it".to_string() } else { format!("{n} runs were recorded after they ran; nothing rb saw shows the command that made them") };
            vec![item(&subject, said, String::new(), Vec::new())]
        }
        _ => {
            // rb's sentence, its subject's id off its front where a colon follows it (the screen shows it after), rb's
            // own rule phrases in the screens' words; quoted words, paths and names in it stay as they are
            let rest = what.strip_prefix(&format!("{subject}: ")).unwrap_or(&what);
            let mut said = rest.to_string();
            for (rb, screen) in [("no recorded criterion", "no rule"), ("criterion undetermined", "rule: can't tell yet"), ("criterion untested", "rule: not tested"),
                ("criterion not met", "rule not met"), ("criterion met", "rule met")] {
                said = said.replace(rb, screen);
            }
            // a sentence led by rb's own word starts upper; one led by a name (an id, a variant, an experiment) keeps
            // it as written ("qcalib is open"), as rb status says it
            let named = |w: &str| subject.split(", ").any(|s| s == w) || findings.contains_key(w) || variants.contains_key(w) || variants.values().any(|vs| vs.iter().any(|v| v == w));
            let plain_first = said.split(' ').next().map_or(false, |w| w.chars().all(|c| c.is_ascii_lowercase()) && !named(w));
            vec![item(&subject, if plain_first { up_first_char(&said) } else { said }, String::new(), criteria)]
        }
    }
}

/// A setting's name as a sentence says it: a variant's own by its variant then its key ("fp32 precision"), any other
/// as it is named.
fn setting_words(name: &str, variants: Option<&Vec<String>>) -> String {
    match name.split_once('.') {
        Some((v, key)) if variants.map_or(false, |vs| vs.iter().any(|x| x == v)) => format!("{v} {key}"),
        _ => name.to_string(),
    }
}

/// The criteria of work linked to no question: an experiment's filed under none, and those linked to no question
/// where the record links any (rb status's unassigned_criterion).
fn loose_criteria(st: &Value, briefs: &[QuestionBrief]) -> Vec<String> {
    let mut loose: Vec<String> = briefs.iter().filter(|b| b.id.is_none()).flat_map(|b| b.experiments.iter().flat_map(|e| e.findings.iter())).map(|f| f.criterion_id.clone()).collect();
    for i in list_of(st, "open").iter().filter(|i| get_or(i, "code", "") == "unassigned_criterion") {
        let c = get_or(i, "subject", "");
        if !loose.contains(&c) {
            loose.push(c);
        }
    }
    loose
}

/// What a screen calls work linked to no question, an experiment filed under none or a criterion linked to none.
pub const UNFILED: &str = "Work linked to no question";

/// Of the units a finding in rb status's data counts, how many were produced before its criterion was declared, and
/// how many it counts.
fn units_before(f: &Value) -> (usize, usize) {
    let mut all: Vec<String> = Vec::new();
    let mut before: Vec<String> = Vec::new();
    for o in list_of(f, "observations").iter().filter(|o| o.get("counted").map_or(false, Value::truthy)) {
        let unit = get_or(o, "unit", "");
        if o.get("before_declared").map_or(false, Value::truthy) && !before.contains(&unit) {
            before.push(unit.clone());
        }
        if !all.contains(&unit) {
            all.push(unit);
        }
    }
    (before.len(), all.len())
}

fn inquiry_overview(inquiry: &Value, opens: &[&Value], words: &IndexMap<String, String>, decided: &IndexMap<String, String>) -> Value {
    let rows = list_of(inquiry, "direct");
    // each criterion's rule word, whether its reading has an interval, and how many units it rests on
    let mut evidence: IndexMap<String, (String, bool, i64)> = IndexMap::new();
    let mut interpretations: Vec<Value> = Vec::new();
    let mut limits: IndexMap<String, Vec<String>> = IndexMap::new();
    let mut measured = false;
    let mut blocked = false;
    let mut unresolved = false;
    let mut point = false;
    for row in rows {
        let cid = get_or(row, "criterion", "");
        let f = row.get("finding").unwrap_or(&Value::Null);
        measured |= f.get("comparison").map_or(false, |c| !c.is_null());
        let word = surface::standing_words(f).split(" · ").next().unwrap_or("").to_string();
        let interval = f.get("comparison").and_then(|c| c.get("lo")).map_or(false, |lo| lo.as_f64().is_some());
        let units = f.get("units").and_then(|u| u.get("n")).and_then(Value::as_i64).unwrap_or(0);
        evidence.insert(cid.clone(), (word, interval, units));
        let result = get_or(f, "result", "");
        let issue = !list_of(row, "integrity").is_empty();
        blocked |= issue;
        unresolved |= result == "unresolved";
        point |= result == "point_only";
        for (yes, label) in [
            (issue, "Integrity needs review"),
            (f.get("comparison").and_then(|c| c.get("design")).map_or(false, Value::truthy), "Fixed design cells"),
            (result == "point_only", "No interval"),
            (result == "unresolved", "Uncertainty remains"),
            (
                matches!(
                    get_or(f, "timing", "").as_str(),
                    "after_evidence" | "after_exposure"
                ) || matches!(
                    get_or(row, "link_timing", "").as_str(),
                    "after_evidence" | "after_exposure"
                ),
                "Retrospective",
            ),
            (get_or(f, "timing", "") == "unknown", "Timing unknown"),
        ] {
            if yes {
                limits.entry(label.into()).or_default().push(cid.clone());
            }
        }
        // every declaration the row reads, so each one a merge left is in view
        let links: Vec<&Value> = match row.get("links") {
            Some(Value::List(links)) if !links.is_empty() => links.iter().collect(),
            _ => row.get("link").into_iter().collect(),
        };
        let active = row
            .get("interpretation_active")
            .map_or(false, Value::truthy);
        let primary = get_or(row, "role", "") == "primary";
        // the latest call on the criterion stays beside its interpretation, whatever it decided
        let decision = f.get("review").filter(|r| r.truthy()).map(|r| get_or(r, "decision", "")).unwrap_or_default();
        let call = call_said(f, decided.get(&decision).map(|s| s.as_str()).unwrap_or("")).map(|c| format!("Call on {cid}: {c}"));
        for link in links {
            let text = get_or(link, "when_met", "");
            if text.is_empty() {
                continue;
            }
            // grouped by what a reader sees, its words and whether it is active: two writers' same words read as one
            let author = crate::actor::shown_writer(&get_or(link, "declared_by", "")).unwrap_or_default();
            // a session's interpretation the user confirmed with its experiment's design reads as confirmed
            let covered = row.get("interpretation_confirmed").map_or(false, Value::truthy) && row.get("link").map_or(false, |l| get_or(l, "when_met", "") == text);
            let confirmed = not_confirmed(&get_or(link, "declared_by", "")).is_empty() || covered;
            if let Some(Value::Dict(group)) = interpretations.iter_mut().find(|g| {
                get_or(g, "text", "") == text
                    && get_or(g, "author", "") == author
                    && g.get("active").map_or(false, Value::truthy) == active
                    && g.get("confirmed").map_or(true, Value::truthy) == confirmed
            }) {
                if let Some(Value::List(ids)) = group.get_mut("criteria") {
                    if !ids.contains(&Value::Str(cid.clone())) {
                        ids.push(Value::Str(cid.clone()));
                    }
                }
                if primary {
                    group.insert("primary".into(), Value::Bool(true));
                }
                if let (Some(Value::List(calls)), Some(call)) = (group.get_mut("calls"), &call) {
                    if !calls.contains(&Value::Str(call.clone())) {
                        calls.push(Value::Str(call.clone()));
                    }
                }
            } else {
                interpretations.push(crate::obj! {"text" => text, "by" => author.clone(), "author" => author, "active" => active, "primary" => primary, "confirmed" => confirmed,
                    "criteria" => Value::list_of_str(vec![cid.clone()]), "calls" => Value::list_of_str(call.clone())});
            }
        }
    }
    interpretations.sort_by_key(|g| {
        (
            !g.get("primary").map_or(false, Value::truthy),
            !g.get("active").map_or(false, Value::truthy),
        )
    });
    // the lead rule (SHELL-SPEC 2): the interpretation resting on the most criteria first, a tie going to the stronger
    // evidence, more criteria read with an interval, then more units; rb's order above where all of it ties
    let of = |g: &Value| -> Vec<(String, bool, i64)> {
        list_of(g, "criteria").iter().filter_map(|c| evidence.get(&py_str(c)).cloned()).collect()
    };
    // an active interpretation (its rule met, its evidence sound, not a gate) before a conditional one
    interpretations.sort_by_key(|g| {
        let e = of(g);
        (
            !g.get("active").map_or(false, Value::truthy),
            std::cmp::Reverse(list_of(g, "criteria").len()),
            std::cmp::Reverse(e.iter().filter(|x| x.1).count()),
            std::cmp::Reverse(e.iter().map(|x| x.2).sum::<i64>()),
        )
    });
    // the same words the user declared and a session declared too, each as active: the user's first, so no session's
    // copy leads them; a conditional group never moves before an active one
    let mut i = 0;
    while i < interpretations.len() {
        let text = get_or(&interpretations[i], "text", "");
        let active = interpretations[i].get("active").map_or(false, Value::truthy);
        let unconfirmed = !interpretations[i].get("confirmed").map_or(true, Value::truthy);
        if let Some(j) = (i + 1..interpretations.len()).find(|j| {
            let g = &interpretations[*j];
            unconfirmed && get_or(g, "text", "") == text && g.get("confirmed").map_or(false, Value::truthy) && g.get("active").map_or(false, Value::truthy) == active
        }) {
            let theirs = interpretations.remove(j);
            interpretations.insert(i, theirs);
        }
        i += 1;
    }
    for g in interpretations.iter_mut() {
        let words: Vec<String> = of(g).into_iter().map(|x| x.0).collect();
        if let Value::Dict(d) = g {
            d.insert("holds".into(), Value::Str(holds_said(&words)));
        }
    }
    let mut relations: Vec<Value> = Vec::new();
    // the relations each limit rests on, by their place in `relations`
    let mut raised: IndexMap<String, Vec<usize>> = IndexMap::new();
    for relation in list_of(inquiry, "relations") {
        let summary = get_or(relation, "summary", "");
        let outcome = get_or(relation, "outcome", "");
        let pair = crate::obj! {"source" => get_or(relation, "source", ""), "target" => get_or(relation, "target", "")};
        // the declarations each comparison rests on, as declared: what it relates and why, never who declared it
        const DECLARED: [&str; 7] = ["criterion", "kind", "target", "orientation", "declared_at", "why", "words"];
        let decls: Vec<Value> = list_of(relation, "declarations")
            .iter()
            .filter_map(|d| d.as_dict())
            .map(|m| Value::Dict(DECLARED.iter().map(|k| (k.to_string(), Value::Str(m.get(*k).and_then(Value::as_str).unwrap_or("").to_string()))).collect()))
            .collect();
        let at = match relations.iter().position(|g| get_or(g, "summary", "") == summary) {
            Some(i) => {
                if let Some(Value::Dict(group)) = relations.get_mut(i) {
                    if let Some(Value::List(pairs)) = group.get_mut("comparisons") { pairs.push(pair); }
                    if let Some(Value::List(held)) = group.get_mut("declarations") { held.extend(decls); }
                }
                i
            }
            None => {
                relations.push(crate::obj! {"summary" => summary, "outcome" => outcome.clone(), "comparisons" => Value::List(vec![pair]), "declarations" => Value::List(decls)});
                relations.len() - 1
            }
        };
        let label = match outcome.as_str() {
            "observed_reversal" => Some("Reversal observed; repeatability uncertain"),
            "integrity" => {
                blocked = true;
                Some("Alignment needs review")
            }
            _ => None,
        };
        if let Some(label) = label {
            limits.entry(label.into()).or_default().push(get_or(relation, "source", ""));
            let at_ = raised.entry(label.into()).or_default();
            if !at_.contains(&at) {
                at_.push(at);
            }
        }
    }
    let headline = if rows.is_empty() {
        if list_of(inquiry, "unmapped").is_empty() {
            "No rule tests this question yet"
        } else {
            "Evidence has not been assigned to this inquiry"
        }
    } else if blocked {
        "Evidence needs review"
    } else if !measured {
        "No evidence recorded yet"
    } else if unresolved {
        "Measured differences; uncertainty remains"
    } else if point {
        "Evidence includes point estimates only"
    } else {
        "Evidence available for review"
    };
    // a call that waits for a confirmation is listed in a place of its own, never as one to review here
    // each subject's call once, as Your call lists it: one call settles every item rb status lists on it
    let mut seen: HashSet<String> = HashSet::new();
    let calls: Vec<Value> = opens.iter().filter(|i| waits_on_you(i) && seen.insert(get_or(i, "subject", ""))).map(|i| call_value(i, words)).collect();
    // what a question's card leads with: rb's computed comparison first, else the first interpretation in the lead
    // rule's order; and how many more there are
    // a relation rb cannot read is no computed comparison: it never leads
    let readable = relations.iter().position(|r| get_or(r, "outcome", "") != "integrity");
    let lead = match (readable, interpretations.first()) {
        (Some(i), _) => crate::obj! {
            "kind" => "relation", "index" => i, "text" => get_or(&relations[i], "summary", ""), "active" => true, "confirmed" => true
        },
        (None, Some(g)) => crate::obj! {
            "kind" => "interpretation", "index" => 0, "text" => get_or(g, "text", ""),
            "active" => g.get("active").map_or(false, Value::truthy), "confirmed" => g.get("confirmed").map_or(false, Value::truthy)
        },
        (None, None) => Value::Null,
    };
    let more = (relations.len() + interpretations.len()).saturating_sub(if lead.is_null() { 0 } else { 1 });
    crate::obj! {
        "headline" => headline, "tone" => if blocked { "warn" } else { "dim" }, "lead" => lead, "more" => more,
        "interpretations" => Value::List(interpretations), "relations" => Value::List(relations),
        "limits" => Value::List(
            limits
                .into_iter()
                .map(|(label, ids)| {
                    let rels = raised.get(&label).map(|r| Value::List(r.iter().map(|i| Value::Int(*i as i64)).collect())).unwrap_or(Value::List(Vec::new()));
                    crate::obj! {"label" => label, "criteria" => Value::list_of_str(ids), "relations" => rels}
                })
                .collect()
        ),
        "calls" => Value::List(calls), "evidence_count" => rows.len(), "unmapped_count" => list_of(inquiry, "unmapped").len()
    }
}

/// When a past call in rb status's data was made, as its line says it after the verb: " May 7 (a past call,
/// Retrospective)", " (a past call from docs/calls.md:12, "We park INT8", date unknown, Retrospective)"; nothing for a
/// call recorded as made when it was recorded.
pub fn past_phrase(d: &Value) -> String {
    match d.get("past").filter(|p| p.truthy()).map(|p| (Past::validate(p, &mut Vec::new(), &mut Vec::new()), p)) {
        Some((Some(past), _)) => {
            let line = crate::ledger::call_line("accept", "", "", "", Some(&past), false);
            line.strip_prefix("accepted").map(|l| l.strip_suffix(": ").unwrap_or(l)).unwrap_or("").to_string()
        }
        _ => String::new(),
    }
}

fn lead(statement: &str) -> String {
    let s = crate::rbyaml::py_strip(statement);
    if s.ends_with('.') || s.ends_with('?') || s.ends_with('!') || s.ends_with(':') {
        s.to_string()
    } else {
        format!("{s}.")
    }
}


/// Whether the rules an interpretation rests on hold, in the screens' words for rb's rule words, counted (SHELL-SPEC
/// 12, PLAIN-WORDS-PASS-1): "its rule met", "its 3 rules met", "its rule: estimate meets its rule", "its 2 rules: 1
/// rule met · gate missed (1)". What the record reads, never a call on the question.
pub fn holds_said(words: &[String]) -> String {
    let shown: Vec<String> = words.iter().map(|w| screen_rule_word(w)).collect();
    let mut counts: IndexMap<&str, usize> = IndexMap::new();
    for w in &shown {
        *counts.entry(w.as_str()).or_insert(0) += 1;
    }
    let n = shown.len();
    // DESIGN-PASS-1 / PLAIN-WORDS-PASS-3: the state of the rules a note rests on is a sentence of its own, with its
    // subject ("Its rule is met."), never a bare word after the note's words
    let (its, is, has) = if n == 1 { ("its rule".to_string(), "is", "has") } else { (format!("its {n} rules"), "are", "have") };
    let up = |s: String| up_first_char(&s);
    match counts.len() {
        0 => String::new(),
        1 => match shown[0].as_str() {
            "rule met" => up(format!("{its} {is} met.")),
            "rule not met" => up(format!("{its} {is} not met.")),
            "can't tell yet" => format!("rb can't tell yet whether {its} {is} met."),
            "not tested" => up(format!("{its} {has} no results yet.")),
            "estimate meets its rule" if n == 1 => "Its rule is met on its estimate.".to_string(),
            "recorded values meet the rule" if n == 1 => "Its rule is met by the recorded values.".to_string(),
            w => up(format!("{its}: {w}.")),
        },
        _ => {
            // the tally's own words after the rules' count: "Its 3 rules: 2 met · 1 can't tell yet."
            let parts: Vec<String> = counts.iter().map(|(w, k)| format!("{k} {}", w.strip_prefix("rule ").unwrap_or(w))).collect();
            up(format!("{its}: {}.", parts.join(" \u{b7} ")))
        }
    }
}

/// rb's rule word (assess::rule_word, the word commands and the record's text keep) as the screens say it
/// (PLAIN-WORDS-PASS-1): a criterion is a rule there, and a rule rb cannot apply yet and one with no results stay apart.
pub fn screen_rule_word(w: &str) -> String {
    match w {
        "criterion met" => "rule met",
        "criterion not met" => "rule not met",
        "criterion undetermined" => "can't tell yet",
        "criterion untested" => "not tested",
        "estimate meets criterion" => "estimate meets its rule",
        "recorded values meet criterion" => "recorded values meet the rule",
        "gate undetermined" => "gate: can't tell yet",
        "gate untested" => "gate: not tested",
        other => other,
    }
    .to_string()
}

/// A finding's review phrase with the decision's words right after the reason, ahead of the earlier-reading note.
fn review_words(review: &str, current: bool, words: &str, by: &str) -> String {
    if words.is_empty() {
        return review.to_string();
    }
    for mark in [EARLIER, EARLIER_PAST] {
        if !current && review.contains(mark) {
            let at = review.rfind(mark).unwrap();
            let (head, tail) = (&review[..at], &review[at + mark.len()..]);
            return format!("{}{mark}{tail}", with_words(head, words, by));
        }
    }
    with_words(review, words, by)
}

/// The latest call on a criterion as its finding says it, with the decision's words: 'rejected 2026-10-07: ...'. The
/// reason is the caller's own text, kept as written; who made the call (its review's `by`) says whose the words are.
pub fn call_said(f: &Value, decided: &str) -> Option<String> {
    let review = f.get("says").filter(|s| s.truthy()).and_then(|s| get_truthy(s, "review")).filter(|r| r != "no decision")?;
    let current = f.get("review").filter(|r| r.truthy()).and_then(|r| r.get("current")).map_or(true, |c| c.truthy());
    let by = f.get("review").filter(|r| r.truthy()).map(|r| get_or(r, "by", "")).unwrap_or_default();
    Some(review_words(&review, current, decided, &by))
}

/// One criterion as a sentence: every phrase rb's own (the finding's `says`), the user's words after its declaration
/// and after the call on it.
pub fn finding_line(f: &Value, decided: &str) -> FindingLine {
    let says = f.get("says").filter(|s| s.truthy()).cloned().unwrap_or(Value::empty_dict());
    let (statement, declared, declared_by) = finding_statement(f);
    let mut parts: Vec<String> = vec![get_truthy(&says, "comparison").unwrap_or_else(|| format!("{}: no reading yet", get_or(f, "rule", "")))];
    if let Some(r) = get_truthy(&says, "result").filter(|r| r != "untested") {
        parts.push(r);
    }
    parts.push(get_truthy(&says, "rule").unwrap_or_else(|| crate::assess::rule_label(&get_or(f, "rule_outcome", "untested"), 1)));
    if let Some(t) = get_truthy(&says, "timing") {
        parts.push(with_words(&t, &declared, &declared_by));
    }
    let problems = list_of(f, "integrity");
    if !problems.is_empty() {
        parts.push(format!(
            "integrity: {}{}",
            get_or(&problems[0], "text", ""),
            if problems.len() > 1 { format!(" (and {} more)", problems.len() - 1) } else { String::new() }
        ));
    }
    if let Some(call) = call_said(f, decided) {
        parts.push(format!("decided: {call}"));
    }
    let body = parts.into_iter().filter(|p| !p.is_empty()).collect::<Vec<_>>().join("; ") + ".";
    FindingLine {
        criterion_id: get_or(f, "criterion", ""),
        statement: statement.clone(),
        numbers: String::new(),
        row: None,
        sentence: format!("{} {body}", lead(&statement)),
        standing: surface::standing_words(f),
        good: surface::green(f),
        rule: get_or(f, "rule", ""),
        role: get_or(f, "role", "primary"),
        says,
        your_words: your_words(&declared, &declared_by),
        observed: String::new(),
        observed_short: String::new(),
        observed_places: Vec::new(),
        other_code: String::new(),
    }
}

/// The declared counting structure: population.* settings, in the order they were declared.
fn scope_line(exp: &Experiment) -> Option<String> {
    let parts: Vec<String> = exp
        .all_settings()
        .into_iter()
        .filter(|(name, s)| name.contains("population.") && s.retracted.is_none() && !s.value.is_null())
        .map(|(name, s)| format!("{} = {}", name.split_once("population.").map(|(_, t)| t).unwrap_or(""), show(&s.value)))
        .collect();
    if parts.is_empty() {
        None
    } else {
        Some(parts.join(" · "))
    }
}

/// One wording for what a retraction did: the reason, and the user's words right after it.
pub fn retraction_words(r: &Retraction) -> String {
    let why = reason_with_words(&r.why, &r.words, &r.by, ": ");
    let by = crate::actor::by_writer(&r.by);
    if !r.scope.is_empty() {
        return format!("superseded for {}{by}{why} (its other numbers still count)", r.scope.join(", "));
    }
    format!("retracted{by}{why}")
}

/// What moved, semantically: runs recorded and superseded, criteria locked, specs amended. Newest first, dated.
pub fn changed(led: &Ledger, exps: &[Experiment], limit: usize) -> Res<Vec<String>> {
    let titles: IndexMap<String, String> = exps.iter().map(|e| (e.id.clone(), if e.title.is_empty() { e.id.clone() } else { e.title.clone() })).collect();
    let by = crate::actor::by_writer;
    let mut events: Vec<(String, String)> = Vec::new();
    for r in led.all::<Run>()?.iter() {
        let Some(title) = titles.get(&r.experiment) else { continue };
        let who = crate::actor::shown_writer(&r.created_by).map(|name| format!(", by {name}")).unwrap_or_default();
        events.push((r.created_at.clone(), format!("a run recorded on {title}{who}")));
        if let Some(ret) = &r.retracted {
            events.push((ret.at.clone(), format!("a run {}", retraction_words(ret))));
        }
    }
    for exp in exps {
        let title = &titles[&exp.id];
        if let Some(fz) = &exp.frozen {
            let locked = format!("{title}: criteria locked{}{}", by(&fz.by), reason_part(&fz.why, &fz.by, ": "));
            events.push((fz.at.clone(), with_words(&locked, &fz.words, &fz.by)));
        }
        for a in &exp.amendments {
            events.push((a.at.clone(), with_words(&format!("{title}: amended{}: {}", by(&a.by), a.change), &a.words, &a.by)));
        }
    }
    events.sort_by(|a, b| b.0.cmp(&a.0));
    let mut lines: Vec<String> = Vec::new();
    let mut counts: IndexMap<(String, String), usize> = IndexMap::new();
    for (at, line) in events {
        let day: String = at.chars().take(10).collect();
        if let Some(rest) = line.strip_prefix("a run recorded") {
            let key = (day.clone(), rest.to_string());
            let n = counts.entry(key.clone()).or_insert(0);
            *n += 1;
            if *n == 1 {
                lines.push(format!("{day}  {line}"));
            } else if let Some(idx) = lines.iter().position(|x| x.starts_with(&day) && x.ends_with(&key.1)) {
                lines[idx] = format!("{day}  {n} runs recorded{}", key.1);
            }
            continue;
        }
        lines.push(format!("{day}  {line}"));
    }
    lines.truncate(limit);
    Ok(lines)
}

/// The questions as a tree, depth first, each with its depth; a question whose parent is not here sits at the top.
pub fn question_order(questions: &[Value]) -> Vec<(Value, usize)> {
    let ids: HashSet<String> = questions.iter().map(|q| get_or(q, "id", "")).collect();
    let mut kids: IndexMap<Option<String>, Vec<Value>> = IndexMap::new();
    for q in questions {
        let parent = q.get("parent").filter(|p| !p.is_null()).map(py_str).filter(|p| ids.contains(p));
        kids.entry(parent).or_default().push(q.clone());
    }
    let mut out: Vec<(Value, usize)> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    fn walk(kids: &IndexMap<Option<String>, Vec<Value>>, parent: Option<String>, depth: usize, out: &mut Vec<(Value, usize)>, seen: &mut HashSet<String>) {
        let Some(list) = kids.get(&parent) else { return };
        for q in list {
            let id = get_or(q, "id", "");
            if seen.contains(&id) {
                continue;
            }
            seen.insert(id.clone());
            out.push((q.clone(), depth));
            walk(kids, Some(id), depth + 1, out, seen);
        }
    }
    walk(&kids, None, 0, &mut out, &mut seen);
    for q in questions {
        if !seen.contains(&get_or(q, "id", "")) {
            out.push((q.clone(), 0));
        }
    }
    out
}

fn tally_rank(key: &str) -> usize {
    crate::cmds::tally_order(key)
}

/// Per question, in the question tree's order, where the work stands, in sentences computed from the record.
pub fn briefs(led: &Ledger, st: &Dict) -> Res<Vec<QuestionBrief>> {
    let stv = Value::Dict(st.clone());
    let findings = list_of(&stv, "findings").to_vec();
    let exps = list_of(&stv, "experiments").to_vec();
    let mut crit_objs: IndexMap<String, Criterion> = IndexMap::new();
    for f in &findings {
        let id = get_or(f, "criterion", "");
        if let Ok(c) = led.load::<Criterion>(&id) {
            crit_objs.insert(id, c);
        }
    }
    let metrics = list_of(&stv, "metrics");
    let units: IndexMap<String, String> = metrics.iter().map(|m| (get_or(m, "id", ""), get_or(m, "unit", ""))).collect();
    let directions: IndexMap<String, String> = metrics.iter().map(|m| (get_or(m, "id", ""), get_or(m, "direction", "none"))).collect();
    let mut aliases: IndexMap<String, String> = IndexMap::new();
    for m in metrics {
        let id = get_or(m, "id", "");
        aliases.insert(id.clone(), id.clone());
        for a in list_of(m, "aliases") {
            aliases.insert(py_str(a), id.clone());
        }
    }
    let decided: IndexMap<String, String> = list_of(&stv, "decisions")
        .iter()
        .filter(|d| d.get("id").map_or(false, |v| v.truthy()))
        .map(|d| (get_or(d, "id", ""), get_truthy(d, "words").unwrap_or_default()))
        .collect();

    let line = |f: &Value| -> FindingLine {
        let decision = f.get("review").filter(|r| r.truthy()).and_then(|r| r.get("decision")).filter(|d| d.truthy()).map(py_str).unwrap_or_default();
        let mut fl = finding_line(f, decided.get(&decision).map(|s| s.as_str()).unwrap_or(""));
        if let Some(obj) = crit_objs.get(&get_or(f, "criterion", "")) {
            let base = if !obj.subject.is_empty() || !obj.reference.is_empty() {
                obj.metric.clone()
            } else {
                obj.metric.rsplit_once('.').map(|(_, t)| t.to_string()).unwrap_or_else(|| obj.metric.clone())
            };
            let unit = units.get(aliases.get(&base).unwrap_or(&base)).cloned().unwrap_or_default();
            let direction = directions.get(aliases.get(&base).unwrap_or(&base)).map(|d| d.as_str()).unwrap_or("none");
            let r = surface::row(
                &CriterionFields {
                    metric: &obj.metric,
                    reads: obj.reads(),
                    comparator: &obj.comparator,
                    target: obj.target,
                    tolerance: obj.tolerance,
                    margin: obj.margin,
                    over: &obj.over,
                    direction,
                },
                f,
                &unit,
            );
            fl.numbers = surface::numbers_line(&r);
            fl.row = Some(r);
        }
        fl
    };

    let exp_brief = |e: &Value| -> Res<ExperimentBrief> {
        let eid = get_or(e, "id", "");
        let mine: Vec<FindingLine> = findings.iter().filter(|f| f.get("experiment").map(py_str).as_deref() == Some(eid.as_str())).map(&line).collect();
        let mut table: Option<Dict> = None;
        let mut counted = 0i64;
        if let Ok(cmp) = led.compare(&eid) {
            counted = cmp.get("run_count").and_then(|v| v.as_i64()).unwrap_or(0);
            if let Some(Value::List(rows)) = cmp.get("rows") {
                if rows.iter().any(|r| r.get("metrics").map_or(false, |m| m.truthy())) {
                    table = Some(cmp.clone());
                }
            }
        }
        let (mut scope, mut arms, mut varies, mut rerun) = (None, String::new(), Vec::new(), Vec::new());
        let (mut folder, mut said, mut repo_facts, mut repo_line) = (String::new(), Vec::new(), Vec::new(), String::new());
        let mut why_by = String::new();
        let mut gate_by = get_or(e, "gate_by", "");
        let design = led.design(&eid)?.unwrap_or_default();
        let _ = (|| -> Res<()> {
            let obj: Experiment = led.load(&eid)?;
            folder = obj.folder.clone();
            why_by = led.why_by(&obj, &design);
            gate_by = design.writer(&gate_by, "gate");
            said = obj
                .quotes
                .iter()
                .filter(|q| ["summary", "result", "limitation", "next"].contains(&q.kind.as_str()))
                .map(|q| crate::dict! {"kind" => q.kind.clone(), "text" => q.text.clone(), "where" => crate::repo::where_of(q, &obj.folder), "cut" => q.cut})
                .collect();
            if let Some(Value::Dict(read)) = stv.get("repository").filter(|r| r.truthy()).and_then(|r| r.get("experiments")).filter(|r| r.truthy()).and_then(|r| r.get(&obj.id)) {
                if !read.is_empty() {
                    repo_facts = crate::repo::facts(read);
                    repo_line = crate::repo::brief(read);
                }
            }
            scope = scope_line(&obj);
            let mut live: Vec<&Variant> = obj.variants.iter().filter(|v| v.retracted.is_none()).collect();
            live.sort_by_key(|v| if v.role == "baseline" { 0 } else { 1 });
            arms = live.iter().map(|v| v.name.clone()).collect::<Vec<_>>().join(" vs ");
            varies = obj.varies.clone();
            rerun = led.rerunnability(&obj)?;
            Ok(())
        })();
        Ok(ExperimentBrief {
            id: eid.clone(),
            title: get_or(e, "title", &eid),
            // one rule with Next step's and Reproduce's (next_step.rs): up to the first ": " where that leaves 8 characters
            short_title: crate::next_step::short_title(&get_or(e, "title", &eid)),
            findings: mine,
            scope,
            counted,
            table,
            arms,
            varies,
            state: get_or(e, "state", "planned"),
            hypothesis: get_or(e, "hypothesis", ""),
            if_holds: get_or(e, "if_holds", ""),
            if_not: get_or(e, "if_not", ""),
            gate_words: get_or(e, "gate_words", ""),
            gate_by,
            purpose: get_or(e, "purpose", ""),
            why: get_or(e, "why", ""),
            why_by,
            notes: live_notes(e)
                .map(|n| note_said(&get_or(n, "text", ""), &get_or(n, "words", ""), &get_or(n, "by", "")) + &crate::actor::shown_writer(&get_or(n, "by", "")).map(|name| format!(" ({name})")).unwrap_or_default())
                .collect(),
            note_ids: live_notes(e).map(|n| get_truthy(n, "id").unwrap_or_default()).collect(),
            relations: list_of(e, "relations").iter().map(|r| format!("{} {}", get_or(r, "kind", "").replace('_', " "), get_or(r, "target", ""))).collect(),
            rerun,
            runs: e.get("run_count").and_then(|v| v.as_i64()).unwrap_or(0),
            folder,
            said,
            repo: repo_facts,
            repo_brief: repo_line,
            design_standing: design.state.to_string(),
            design_lines: design.lines(),
            design_confirm: design.waiting_command(),
            gate_first: design.gate_first(),
            not_recorded: design.not_recorded.iter().map(|s| s.to_string()).collect(),
        })
    };

    let words = subject_words(&stv);
    let questions = list_of(&stv, "questions").to_vec();

    let section = |q: Option<&Value>, eids: &[String], depth: usize| -> Res<QuestionBrief> {
        let qid = q.map(|q| get_or(q, "id", ""));
        let inquiry = q.and_then(|q| q.get("inquiry")).cloned().unwrap_or(Value::empty_dict());
        let summary = list_of(&inquiry, "summary").iter().map(py_str).collect();
        let relevant: HashSet<String> = list_of(&inquiry, "direct").iter().map(|r| get_or(r, "criterion", "")).collect();
        let inquiry_findings = list_of(&inquiry, "direct").iter().filter_map(|r| {
            let mut scoped = r.get("finding").filter(|f| !f.is_null())?.clone();
            // The same surface rule evaluates the finding in this inquiry's integrity context. The canonical
            // numeric finding remains complete and unchanged in the experiment and global catalogue.
            if let Value::Dict(ref mut d) = scoped {
                d.insert("integrity".into(), r.get("integrity").cloned().unwrap_or(Value::List(Vec::new())));
            }
            Some(line(&scoped))
        }).collect();
        let eset: HashSet<&String> = eids.iter().collect();
        let mut subtree: HashSet<String> = eids.iter().cloned().collect();
        if let Some(id) = &qid {
            subtree.insert(id.clone());
        }
        for f in &findings {
            if qid.is_none() && f.get("experiment").map(py_str).map_or(false, |e| eset.contains(&e)) || relevant.contains(&get_or(f, "criterion", "")) {
                subtree.insert(get_or(f, "criterion", ""));
            }
        }
        let opens: Vec<&Value> = list_of(&stv, "open")
            .iter()
            .filter(|i| {
                let subject = get_or(i, "subject", "");
                subtree.contains(&subject) || eids.iter().any(|e| subject.starts_with(&format!("{e}/"))) || subject.split(", ").any(|s| subtree.contains(s))
            })
            .collect();
        let overview = inquiry_overview(&inquiry, &opens, &words, &decided);
        let mut still: Vec<String> = Vec::new();
        for i in &opens {
            if i.get("code").and_then(|v| v.as_str()) == Some("open_question") && Some(get_or(i, "subject", "")) == qid {
                continue;
            }
            let what = get_or(i, "what", "");
            if !still.contains(&what) {
                still.push(what);
            }
        }
        let mut nxt: Vec<String> = Vec::new();
        for i in &opens {
            let d = get_or(i, "do", "");
            if !nxt.contains(&d) {
                nxt.push(d);
            }
        }
        let mut ruled: Vec<String> = Vec::new();
        for d in list_of(&stv, "decisions") {
            let subject = get_or(d, "subject", "");
            let in_tree = d.get("subject").map_or(false, |s| subtree.contains(&py_str(s))) || eids.iter().any(|e| subject.starts_with(&format!("{e}/")));
            if in_tree && !d.get("retracted").map_or(false, |r| r.truthy()) {
                // a call on a question is said with the wording it was made on, never with words given since
                let said = match led.load::<crate::record::Question>(&subject) {
                    Ok(q) => led.question_text_at(&q, &get_or(d, "at", ""))?,
                    Err(_) => words.get(&subject).cloned().unwrap_or_else(|| subject.clone()),
                };
                let by = get_or(d, "by", "");
                let past = d.get("past").map_or(false, |p| p.truthy());
                let why = if (past && !words_are_theirs(&by)) || confirms_one(d) { format!(" · note: {}", get_or(d, "why", "")) } else { format!(" ({})", get_or(d, "why", "")) };
                let text = format!("{}{}{}: \"{said}\"{why}", made(&get_or(d, "action", "")), crate::actor::by_writer(&by), past_phrase(d));
                let current = d.get("current").map_or(true, |c| c.truthy());
                ruled.push(with_words(&text, &get_truthy(d, "words").unwrap_or_default(), &by) + &crate::ledger::runs_after_of(d) + if current { "" } else { earlier(past) });
            }
        }
        let mut exps_here: Vec<ExperimentBrief> = Vec::new();
        for e in &exps {
            if eset.contains(&get_or(e, "id", "")) {
                exps_here.push(exp_brief(e)?);
            }
        }
        let led_exps: Vec<Experiment> = eids.iter().filter_map(|id| led.load::<Experiment>(id).ok()).collect();
        let mut rollup = String::new();
        if let Some(q) = q {
            let r = q.get("rollup").filter(|r| r.truthy()).cloned().unwrap_or(Value::empty_dict());
            let mut rules: Vec<(String, i64)> = match r.get("rules") {
                Some(Value::Dict(d)) => d.iter().map(|(k, v)| (k.clone(), v.as_i64().unwrap_or(0))).collect(),
                _ => Vec::new(),
            };
            rules.sort_by_key(|(k, _)| tally_rank(k));
            // the criteria linked to this question, apart from what sits below it in the tree
            let linked: Vec<String> = rules.iter().map(|(k, n)| format!("{n} {}", crate::assess::rule_label(k, *n))).collect();
            let mut below: Vec<String> = Vec::new();
            let kids = list_of(&r, "children");
            if !kids.is_empty() {
                let open_children = list_of(&r, "open_children");
                below.push(format!(
                    "{} sub-question{}{}",
                    kids.len(),
                    if kids.len() == 1 { "" } else { "s" },
                    if open_children.is_empty() { String::new() } else { format!(", {} open", open_children.len()) }
                ));
            }
            let planned = list_of(&r, "untested").iter().filter(|u| py_str(u).starts_with("experiment ")).count();
            if planned > 0 {
                below.push(format!("{planned} planned"));
            }
            if !kids.is_empty() || depth > 0 {
                let mut said: Vec<String> = Vec::new();
                if !linked.is_empty() {
                    said.push(format!("linked here: {}", linked.join(" · ")));
                }
                if !below.is_empty() {
                    said.push(format!("below it: {}", below.join(" · ")));
                }
                rollup = said.join("; ");
            }
        }
        still.truncate(4);
        nxt.truncate(3);
        let ruled = if ruled.len() > 3 { ruled[ruled.len() - 3..].to_vec() } else { ruled };
        Ok(QuestionBrief {
            id: qid.clone(),
            text: q.map(|q| get_or(q, "text", qid.as_deref().unwrap_or(""))).unwrap_or_default(),
            status: q.map(|q| get_or(q, "state", "open")).unwrap_or_default(),
            experiments: exps_here,
            changed: changed(led, &led_exps, 4)?,
            still_open: still,
            next: nxt,
            ruled,
            depth,
            parent: q.and_then(|q| q.get("parent")).filter(|p| !p.is_null()).map(py_str),
            rollup,
            // the reading as a screen carries it; rb status --json keeps the record's
            inquiry: crate::actor::on_screen(&inquiry, words_shown()),
            inquiry_findings,
            overview,
            summary,
        })
    };

    let mut out: Vec<QuestionBrief> = Vec::new();
    let known: HashSet<String> = questions.iter().map(|q| get_or(q, "id", "")).collect();
    for (q, depth) in question_order(&questions) {
        let qid = get_or(&q, "id", "");
        let linked: HashSet<String> = q.get("inquiry").map(|i| list_of(i, "direct").iter().map(|r| get_or(r, "experiment", "")).collect()).unwrap_or_default();
        let eids: Vec<String> = exps.iter().filter(|e| list_of(e, "questions").iter().any(|x| py_str(x) == qid) || linked.contains(&get_or(e, "id", ""))).map(|e| get_or(e, "id", "")).collect();
        out.push(section(Some(&q), &eids, depth)?);
    }
    let unfiled: Vec<String> = exps
        .iter()
        .filter(|e| !list_of(e, "questions").iter().any(|x| known.contains(&py_str(x))))
        .map(|e| get_or(e, "id", ""))
        .collect();
    if !unfiled.is_empty() {
        out.push(section(None, &unfiled, 0)?);
    }
    Ok(out)
}

// ---------------------------------------------------------------- the user's screens: home, the catalogue, cards

/// One item under Your call: something rb found in the repository and not in the record.
#[derive(Debug, Clone, Default)]
pub struct Ask {
    pub request_id: String,
    pub title: String,
    pub hint: String,
    pub asked_by: String,
    pub asked_at: String,
    pub argv: Vec<String>,
}

/// One run as the runs drawer lists it.
#[derive(Debug, Clone, Default)]
pub struct RunRow {
    pub when: String,
    pub experiment: String,
    pub results: String,
    pub by: String,
    pub note: String,
    pub experiment_id: String,
    pub run_id: String,
    pub metrics: Dict,
    pub per_run: Dict,
    pub session: String,
    pub receipt: Dict,
    /// the produced day, when it is known: the day the run was made, not the day it was recorded
    pub produced: String,
    /// what the run's card says of it, as lines: the page's receipt
    pub facts: Vec<String>,
    /// on the user's own screens, where it was recorded when that is another place than the one shown
    pub observed_in: Option<Value>,
}

/// One resource as the resources drawer lists it.
#[derive(Debug, Clone, Default)]
pub struct ResourceRow {
    pub resource_id: String,
    pub name: String,
    pub r#type: String,
    pub availability: String,
    pub where_: Vec<String>,
    /// the same, a local file inside a linked worktree said as the user's screens name it
    pub where_short: Vec<String>,
    pub how: String,
    pub used_by: i64,
    pub identity: String,
    pub exact: bool,
}

#[derive(Debug, Clone, Default)]
pub struct TreeRow {
    pub kind: String,
    pub id: String,
    pub label: String,
    pub standing: String,
    pub depth: usize,
    /// The met words in `standing` drawn without green: a finding they count is not green.
    pub muted: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct Home {
    pub title: String,
    pub project_id: String,
    pub you: String,
    pub since: Option<String>,
    pub found: Vec<Ask>,
    pub unplaced: Vec<String>,
    /// How many .json, .yaml and .toml files changed that nothing in the record cites, and rb status's line for them.
    pub uncited: usize,
    /// Those files' paths, each with when rb first saw its change.
    pub uncited_paths: Vec<(String, String)>,
    pub uncited_line: Option<String>,
    /// What rb looks at, when it last looked and what it does not read or see: rb status's scope line.
    pub scope: Option<String>,
    /// The count line and the scope line where a screen has one short line for each.
    pub uncited_short: Option<String>,
    pub scope_short: Option<String>,
    /// How many of rb status's items are not the user's calls, with what kind of unknown they are: '2 open · 1 setting
    /// unknown'.
    pub agent_line: Option<String>,
    /// The same, as a screen says it: '2 still open in the record · 1 setting unknown'.
    pub open_line: Option<String>,
    /// How many, with the same kind, as the shell's Overview says it under what needs a judgment, saying what they are
    /// not so the count is never read as more of what waits, in every voice: 'Also open: 2 items that need no decision ·
    /// 1 setting unknown'. Not in the home op's JSON.
    pub open_besides: Option<String>,
    pub tree: Vec<TreeRow>,
    pub quiet: Option<String>,
    /// The same line on a page that leaves the machine (rb export), in no one's voice (`quiet_line_away`).
    pub quiet_away: Option<String>,
    /// How many calls wait on you, one per subject they are decided on (D2): the quiet line then says so.
    pub waiting: usize,
    /// Those calls, each once, with where each is decided, why rb asks and the calls it offers (`waiting_calls`).
    pub calls: Vec<Value>,
    /// What is still open in the record and not the user's to call, grouped by rb's code (`open_groups`).
    pub open_items: Vec<Value>,
    /// Work filed under no question: its criteria, the calls on it and its open items.
    pub unfiled: Value,
    pub tally: Vec<(String, i64)>,
    /// The met words of `tally` drawn without green.
    pub muted: Vec<String>,
    pub live: Vec<String>,
    pub weak_identity: Option<String>,
    /// where the record began, when runs came before it: the adoption boundary, in one quiet line
    pub began: Option<String>,
    pub briefs: Vec<QuestionBrief>,
    pub runs: Vec<RunRow>,
    pub resources: Vec<ResourceRow>,
    pub notes: Vec<String>,
    /// The calls that wait for a confirmation, in a group of their own: never one of the user's calls to review, and on
    /// no page.
    pub pending: Vec<PendingRow>,
    /// What the record holds, counted (`record_counts`): questions, experiments, criteria, runs, runs taken back,
    /// analyses, resources, the calls waiting on you, and the writes.
    pub counts: Value,
    /// How the record was made, counted from it (`made`): criteria declared after their evidence existed (the line: rules
    /// written after their results came in), runs that record their command (as given), and rb's line for both.
    pub made: Value,
    /// What `made` counts, by id: the criteria declared after their evidence and the runs with no command as given.
    pub weakened: Weakened,
    /// When the record was last written, to the minute in UTC; "" for a record no write has made yet.
    pub last_write: String,
}

/// One call that waits for a confirmation, as the user's screens list it.
#[derive(Debug, Clone, Default)]
pub struct PendingRow {
    pub id: String,
    /// proposed, past (a past call mentioned), gate (a gate a session set, said apart only where its design does not
    /// wait with it), or design (an experiment's design, with its gate)
    pub kind: String,
    pub text: String,
    pub confirm: String,
    /// a design's parts, as rb confirm lists them
    pub lines: Vec<String>,
    /// what a call is on (none for a gate or a design)
    pub subject: String,
}

#[derive(Debug, Clone, Default)]
pub struct PassBar {
    pub criterion_id: String,
    pub text: String,
    pub rule: String,
    pub standing: String,
    pub muted: Vec<String>,
}

/// One found item, or one object of the catalogue, opened, in the user's voice.
#[derive(Debug, Clone, Default)]
pub struct Card {
    pub request_id: String,
    pub title: String,
    pub asked_by: String,
    pub asked_at: String,
    pub lead: String,
    pub pass_bars: Vec<PassBar>,
    pub blockers: Vec<String>,
    pub looks: Vec<String>,
    pub argv: Vec<String>,
    pub yes_label: String,
    pub kind: String,
    pub facts: Vec<String>,
    /// which of `facts` are its design's standing and changed-since lines: a page keeps their spaces as rb says them
    pub design_lines: std::ops::Range<usize>,
    pub dismiss_key: String,
    pub who_line: String,
    pub bars_label: String,
    pub reports: String,
    pub sentences: Vec<String>,
    pub runs: Vec<(String, String)>,
    /// a found item's: the checkout its `argv` runs in, as typed (journal::Found::here); handed to rb ui alone, which
    /// runs it there, and never drawn
    pub run_in: String,
}

fn human_name(actor_id: &str) -> String {
    match actor_id.split_once(':') {
        Some((_, n)) => n.to_string(),
        None => actor_id.to_string(),
    }
}

/// A writer on a screen: the name actor::shown_writer gives, or nothing.
fn shown(id: &str) -> String {
    crate::actor::shown_writer(id).unwrap_or_default()
}

fn found_ask(it: &crate::journal::Found) -> Ask {
    let exp = it.experiment.clone().unwrap_or_else(|| "None".into());
    // what a command printed is named by the command as given, never by a part of it read as a file's name
    let title = if it.printed {
        format!("Record what `{}` printed on {exp}", crate::cmds::short_middle(&it.path, 48))
    } else {
        format!("Record {} on {exp}", it.path.rsplit('/').next().unwrap_or(""))
    };
    Ask {
        request_id: it.id.clone(),
        title,
        hint: "in the repository, not in the record".into(),
        asked_by: "rb, watching the repository".into(),
        asked_at: it.at.clone(),
        argv: it.argv.clone(),
    }
}

/// What rb found in the repository and nobody recorded, as asks, with what it cannot place and the changed files
/// nothing in the record cites; nothing when the observation buffer cannot be read. Two that would read the same are
/// told apart: what a command printed by its first numbers, a file by its path.
pub fn found(led: &Ledger) -> (Vec<Ask>, Vec<String>, Vec<crate::journal::Uncited>, Option<(String, String)>) {
    match crate::journal::found_all(led, crate::journal::Seen::User) {
        Ok(all) => {
            let line = (!all.uncited.is_empty()).then(|| (crate::cmds::reading::uncited_words(&all) + ".", crate::cmds::reading::uncited_short(&all)));
            let crate::journal::FoundAll { placed, unplaced, uncited, .. } = all;
            let mut asks: Vec<Ask> = placed.iter().map(found_ask).collect();
            let titles: Vec<String> = asks.iter().map(|a| a.title.clone()).collect();
            for (a, it) in asks.iter_mut().zip(&placed) {
                if titles.iter().filter(|t| **t == a.title).count() < 2 {
                    continue;
                }
                if it.printed {
                    let mut ms: Vec<(&String, &Value)> = it.metrics.iter().collect();
                    ms.sort_by(|x, y| x.0.cmp(y.0));
                    let said: Vec<String> = ms.iter().take(2).map(|(k, v)| format!("{k}={}", crate::pyfloat::fmt_num(v, "g"))).collect();
                    a.title = format!("{} · {}", a.title, said.join(", "));
                } else {
                    a.title = format!("Record {} on {}", it.path, it.experiment.clone().unwrap_or_else(|| "None".into()));
                }
            }
            (asks, unplaced, uncited, line)
        }
        Err(_) => (Vec::new(), Vec::new(), Vec::new(), None),
    }
}

/// The met words ('meets', 'reproduced') a count of these findings draws without green: one of the findings it counts
/// is not green. A gate met is never green (D4), so it is drawn neutral already and never muted.
fn muted(findings: &[&Value]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for f in findings.iter().filter(|f| get_or(f, "rule_outcome", "") == "meets" && !surface::green(f)) {
        let w = crate::assess::rule_word("meets", &get_or(f, "role", "primary"), &get_or(f, "origin", "own"));
        if surface::standing_class(&w) == "good" && !out.contains(&w) {
            out.push(w);
        }
    }
    out
}

fn pass_bar(f: &Value) -> PassBar {
    PassBar {
        criterion_id: get_or(f, "criterion", ""),
        text: finding_statement(f).0,
        rule: get_or(f, "rule", ""),
        standing: surface::standing_words(f),
        muted: muted(&[f]),
    }
}

/// Criteria by the rule word, in reading order: "2 criteria met · 1 gate missed · 3 criteria untested".
fn rule_tally(findings: &[&Value]) -> String {
    let mut tally: IndexMap<String, i64> = IndexMap::new();
    for f in findings {
        let w = crate::assess::rule_word(&get_or(f, "rule_outcome", "untested"), &get_or(f, "role", "primary"), &get_or(f, "origin", "own"));
        *tally.entry(w).or_insert(0) += 1;
    }
    let mut items: Vec<(String, i64)> = tally.into_iter().collect();
    items.sort_by_key(|(k, _)| tally_rank(k));
    items.iter().map(|(k, n)| format!("{n} {}", crate::assess::rule_label(k, *n))).collect::<Vec<_>>().join(" · ")
}

fn tree(st: &Value) -> Vec<TreeRow> {
    let mut rows: Vec<TreeRow> = Vec::new();
    let findings = list_of(st, "findings");
    let experiments = list_of(st, "experiments");
    let mut placed: HashSet<String> = HashSet::new();
    let exp_row = |e: &Value, depth: usize, rows: &mut Vec<TreeRow>, placed: &mut HashSet<String>| {
        let id = get_or(e, "id", "");
        let mine: Vec<&Value> = findings.iter().filter(|f| f.get("experiment").map(py_str).as_deref() == Some(id.as_str())).collect();
        let mut parts = vec![get_or(e, "state", "planned")];
        if !mine.is_empty() {
            parts.push(rule_tally(&mine));
        }
        parts.push(if e.get("frozen").map_or(false, |f| f.truthy()) { "locked".into() } else { "not locked".into() });
        rows.push(TreeRow { kind: "experiment".into(), id: id.clone(), label: get_or(e, "title", &id), standing: parts.join(" · "), depth, muted: muted(&mine) });
        placed.insert(id);
    };
    for (q, depth) in question_order(list_of(st, "questions")) {
        let qid = get_or(&q, "id", "");
        let roll = q.get("rollup").filter(|r| r.truthy()).cloned().unwrap_or(Value::empty_dict());
        let mut rules: Vec<(String, i64)> = match roll.get("rules") {
            Some(Value::Dict(d)) => d.iter().map(|(k, v)| (k.clone(), v.as_i64().unwrap_or(0))).collect(),
            _ => Vec::new(),
        };
        rules.sort_by_key(|(k, _)| tally_rank(k));
        let mut parts = vec![get_or(&q, "state", "open")];
        parts.extend(rules.iter().map(|(k, n)| format!("{n} {}", crate::assess::rule_label(k, *n))));
        let inquiry = q.get("inquiry").cloned().unwrap_or(Value::empty_dict());
        let selected: HashSet<String> = list_of(&inquiry, "direct").iter().map(|r| get_or(r, "criterion", "")).collect();
        let below: Vec<&Value> = findings.iter().filter(|f| selected.contains(&get_or(f, "criterion", ""))).collect();
        let mut q_muted = muted(&below);
        for row in list_of(&inquiry, "direct").iter().filter(|r| !r.get("clean").map_or(false, Value::truthy)) {
            if let Some(f) = row.get("finding") {
                let word = crate::assess::rule_word(&get_or(f, "rule_outcome", "untested"), &get_or(f, "role", "primary"), &get_or(f, "origin", "own"));
                if !q_muted.contains(&word) { q_muted.push(word); }
            }
        }
        rows.push(TreeRow { kind: "question".into(), id: qid.clone(), label: get_or(&q, "text", &qid), standing: parts.join(" · "), depth, muted: q_muted });
        for e in experiments {
            if list_of(e, "questions").iter().any(|x| py_str(x) == qid) {
                exp_row(e, depth + 1, &mut rows, &mut placed);
            }
        }
    }
    for e in experiments {
        if !placed.contains(&get_or(e, "id", "")) {
            exp_row(e, 0, &mut rows, &mut placed);
        }
    }
    for r in list_of(st, "resources") {
        let av = r.get("availability").filter(|a| a.truthy()).cloned().unwrap_or(Value::empty_dict());
        let ident = av.get("identity").filter(|i| i.truthy()).map(|i| get_or(i, "level", "")).unwrap_or_default();
        let standing = get_or(&av, "state", "") + if ident == "name" { " · name only" } else { "" };
        let id = get_or(r, "id", "");
        let label = get_or(r, "name", &id) + &get_truthy(r, "version").map(|v| format!(" {v}")).unwrap_or_default();
        rows.push(TreeRow { kind: "resource".into(), id, label, standing, depth: 0, muted: Vec::new() });
    }
    rows
}

/// Where the user's last-seen marker lives: the git common dir, personal and uncommitted, shared across worktrees.
fn seen_path(root: &std::path::Path) -> std::path::PathBuf {
    if let Some(top) = crate::sources::git(root, &["rev-parse", "--git-common-dir"]) {
        let t = crate::rbyaml::py_strip(&top).to_string();
        if !t.is_empty() {
            let mut p = std::path::PathBuf::from(&t);
            if !p.is_absolute() {
                p = crate::sys::canonicalize(root.join(&p)).unwrap_or_else(|_| root.join(&p));
            }
            if p.is_dir() {
                return p.join("rb-last-seen.json");
            }
        }
    }
    root.join(crate::ledger::DIR).join("journal").join("last-seen.json")
}

/// The user's last look as the marker keeps it: how many log lines there were, and the lines they have seen, by
/// digest, in the order they first saw them (an earlier rb kept the count alone).
fn seen_mark(root: &std::path::Path) -> (Option<i64>, Option<Vec<String>>) {
    let Some(mark) = std::fs::read_to_string(seen_path(root)).ok().and_then(|text| crate::pyjson::loads(&text).ok()) else { return (None, None) };
    let count = match mark.get("log_lines") {
        Some(Value::Int(i)) => Some(*i),
        Some(Value::Bool(b)) => Some(*b as i64),
        Some(Value::Float(f)) if f.is_finite() => Some(f.trunc() as i64), // int(3.7) is 3
        Some(Value::Str(s)) => crate::rbyaml::py_strip(s).parse::<i64>().ok(),
        _ => None,
    };
    (count, crate::delta::remembered(mark.get("seen_lines")))
}

/// The user looked: remember each log line they have now seen, after those they had seen before. Returns (how many
/// lines they had seen by their last look, -1 the first time; the log's lines now). What they have seen is only added
/// to, so that many of its first lines are what they had seen then, wherever a merge has sorted the log since.
pub fn mark_seen(led: &Ledger) -> Res<(i64, i64)> {
    let (count, before) = seen_mark(&led.root);
    let old = match &before {
        Some(seen) => seen.len() as i64,
        None => count.unwrap_or(-1),
    };
    let rows = led.log_entries()?;
    let digests: Vec<String> = rows.iter().map(crate::ledger::line_digest).collect();
    let lines = rows.len() as i64;
    // the count still, for an earlier rb that shares the marker
    let mark = crate::obj! {
        "log_lines" => lines, "at" => crate::pytime::now(), "seen_lines" => Value::list_of_str(crate::delta::remember(before, &digests))
    };
    // no marker only means since falls back to the heuristic
    let at = seen_path(&led.root);
    let first = !at.exists();
    if crate::ledger::write_atomic(&at, crate::pyjson::dumps(&mark).as_bytes()).is_ok() && first && !at.starts_with(&led.dir) {
        // rb rewrites it at each look, so the ledger keeps where it is, not its bytes
        crate::owned::record(&led.root, "repo", crate::dict! {"path" => crate::sys::slashed(&at), "how" => "created"});
    }
    Ok((old, lines))
}

/// What was written since the user last looked, whoever wrote it, counted with no writer named: 'Since you last looked:
/// 3 writes, 2 runs recorded · last 2026-10-10'. A baseline is how many lines the user had seen by then, as `mark_seen`
/// counts them. With no look kept at all, nothing is said.
fn since(led: &Ledger, baseline: Option<i64>) -> Res<Option<String>> {
    let entries = led.log_entries()?;
    let op = |r: &Dict| r.get("op").map(py_str).unwrap_or_default();
    // a session's line records no write
    let fresh: Vec<&Dict> = match seen_mark(&led.root) {
        (_, Some(seen)) => {
            let upto = baseline.map_or(seen.len(), |b| b.clamp(0, seen.len() as i64) as usize);
            let known: HashSet<&str> = seen[..upto].iter().map(String::as_str).collect();
            entries.iter().filter(|r| !crate::ledger::is_session_line(r) && !known.contains(crate::ledger::line_digest(r).as_str())).collect()
        }
        // no marker, or an earlier rb's, which keeps a count alone: by position, as that rb read it
        (count, None) => {
            // never from the user's own last write: what follows it would be only what others wrote
            let Some(baseline) = baseline.or(count) else { return Ok(None) };
            entries.iter().skip(baseline.max(0) as usize).filter(|r| !crate::ledger::is_session_line(r)).collect()
        }
    };
    let writes: Vec<&Dict> = fresh.iter().filter(|r| op(r) != "reconciled").cloned().collect();
    let compiled: Vec<&Dict> = fresh.iter().filter(|r| op(r) == "reconciled").cloned().collect();
    let line_of = |r: &Dict| {
        let d = r.get("detail").filter(|d| d.truthy()).cloned().unwrap_or(Value::empty_dict());
        d.get("line").map(py_str).unwrap_or_default()
    };
    let mut parts: Vec<String> = Vec::new();
    if let Some(last) = writes.last() {
        let runs = writes.iter().filter(|r| op(r) == "record").count();
        parts.push(
            format!("{} write{}", writes.len(), if writes.len() == 1 { "" } else { "s" })
                + &if runs > 0 { format!(", {runs} run{} recorded", if runs == 1 { "" } else { "s" }) } else { String::new() },
        );
        parts.push(format!("last {}", last.get("at").map(py_str).unwrap_or_default().chars().take(10).collect::<String>()));
    }
    if let Some(c) = compiled.last() {
        parts.push(format!("reconcile: {}", line_of(c)));
    }
    Ok((!parts.is_empty()).then(|| format!("Since you last looked: {}", parts.join(" · "))))
}

/// One experiment's criteria counted by rule as rb status counts the record's (its tally), in the same order, and the
/// words of a met rule a screen draws muted, as the home gives both.
pub fn experiment_tally(st: &Value, exp: &str) -> (Vec<(String, i64)>, Vec<String>) {
    let findings: Vec<&Value> = list_of(st, "findings").iter().filter(|f| get_or(f, "experiment", "") == exp).collect();
    let mut counts = Dict::new();
    for f in &findings {
        let w = crate::assess::rule_word(&get_or(f, "rule_outcome", "untested"), &get_or(f, "role", "primary"), &get_or(f, "origin", "own"));
        let n = counts.get(&w).and_then(Value::as_i64).unwrap_or(0) + 1;
        counts.insert(w, Value::Int(n));
    }
    (tally_of(&crate::obj! {"tally" => Value::Dict(counts)}), muted(&findings))
}

fn tally_of(st: &Value) -> Vec<(String, i64)> {
    let mut raw: IndexMap<String, i64> = match st.get("tally") {
        Some(Value::Dict(d)) => d.iter().map(|(k, n)| (k.replace('_', " "), n.as_i64().unwrap_or(0))).collect(),
        _ => IndexMap::new(),
    };
    let mut ordered: Vec<(String, i64)> = Vec::new();
    for k in crate::cmds::TALLY_ORDER {
        if let Some(n) = raw.shift_remove(*k) {
            ordered.push((k.to_string(), n));
        }
    }
    let mut rest: Vec<(String, i64)> = raw.into_iter().collect();
    rest.sort();
    ordered.extend(rest);
    ordered
}

/// The observed tail, worded here so renderers never invent a word: never a file rb leaves out of what changed (a
/// lockfile, a CI or tool config, its own page), which rb journal still lists.
fn live(led: &Ledger, last: usize) -> Vec<String> {
    let mut out = Vec::new();
    for e in crate::journal::entries(&led.root, Some(last + 40)) {
        let ev = Value::Dict(e);
        let at: String = get_or(&ev, "at", "").chars().skip(11).take(5).collect();
        match ev.get("op").and_then(|v| v.as_str()) {
            Some("file") if crate::journal::left_alone(&get_or(&ev, "path", "")) => {}
            Some("file") => out.push(format!("{at}  {}  {}", get_or(&ev, "change", ""), get_or(&ev, "path", ""))),
            Some("git") => {
                let commit = get_truthy(&ev, "commit").unwrap_or_else(|| "no commit".into());
                out.push(format!("{at}  git  {}", commit.chars().take(9).collect::<String>()) + if ev.get("dirty").map_or(false, |d| d.truthy()) { " · tree dirty" } else { "" });
            }
            Some("run") => out.push(format!("{at}  ran  {} · exit {}", get_or(&ev, "command", ""), ev.get("exit").map(py_str).unwrap_or_else(|| "None".into()))),
            _ => {}
        }
    }
    let from = out.len().saturating_sub(last);
    out.split_off(from)
}

/// The line under the work, as rb status reads the record: the calls it lists for the user (who: human) are counted as
/// waiting on you, with where they are listed, after what rb found (which the screens list themselves) or in its place;
/// nothing waits on you only while neither does, nor a call that waits for your confirmation, which the screens list
/// apart. The rest of what rb status lists is counted as open in the record; while designs alone wait for a
/// confirmation, which may be a long while as the user reads them, the line still counts it.
/// The quiet line on a page that leaves the machine (rb export): the same facts in no one's voice, since anyone may be
/// sent it: "1 call is open.", "2 designs wait for a confirmation, listed apart.", "No call is open. ..."; nothing to paste.
fn quiet_line_away(calls: usize, found: usize, recorded: bool, met: i64, open: usize, pending: &[PendingRow]) -> Option<String> {
    if calls > 0 {
        return Some(if calls == 1 { "1 call is open.".to_string() } else { format!("{calls} calls are open.") });
    }
    if found > 0 {
        return None;
    }
    if !recorded {
        return Some("Nothing recorded yet.".into());
    }
    let counted = if open == 0 {
        String::new()
    } else {
        format!(
            " {met} {}; {open} {} still open in the record.",
            if met == 1 { "rule is met" } else { "rules are met" },
            if open == 1 { "thing is" } else { "things are" }
        )
    };
    if !pending.is_empty() {
        let wait = if pending.len() == 1 { "waits" } else { "wait" };
        return Some(format!("{} {wait} for a confirmation, listed apart.{counted}", waiting_said(pending)));
    }
    Some(format!("No call is open.{counted}"))
}

fn quiet_line(calls: usize, found: usize, recorded: bool, met: i64, open: usize, pending: &[PendingRow]) -> Option<String> {
    if calls > 0 {
        return Some(format!("{calls} {} on you.", if calls == 1 { "call waits" } else { "calls wait" }));
    }
    if found > 0 {
        return None;
    }
    let counted = || -> String {
        if open == 0 {
            return "When rb finds a run nobody recorded, it appears here.".into();
        }
        format!(
            "{met} {}; {open} {} still open in the record.",
            if met == 1 { "rule is met" } else { "rules are met" },
            if open == 1 { "thing is" } else { "things are" }
        )
    };
    if !pending.is_empty() {
        let wait = if pending.len() == 1 { "waits" } else { "wait" };
        let head = format!("{} {wait} for your confirmation, listed apart", waiting_said(pending));
        if recorded && pending.iter().all(|r| r.kind == "design") {
            return Some(format!("{head}. {}", counted()));
        }
        return Some(format!("{head}; nothing else waits on you."));
    }
    if !recorded {
        return Some(format!("Nothing recorded yet. To start, paste this line into your agent's chat: {}", crate::skill::PASTE_LINE));
    }
    Some(format!("Nothing waits on you. {}", counted()))
}

/// The home view. The since line shows on the user's screen alone, so only it asks for one: `since_from` is Some(how
/// many lines they had seen by their last look, as `mark_seen` counts them, or None to read their marker).
pub fn home(led: &Ledger, since_from: Option<Option<i64>>) -> Res<Home> {
    with_screen_trees(led, || home_in(led, since_from))
}

fn home_in(led: &Ledger, since_from: Option<Option<i64>>) -> Res<Home> {
    led.locked(|| {
        let st = Value::Dict(led.status(None)?);
        let proj = st.get("project").cloned().unwrap_or(Value::empty_dict());
        let a = st.get("actor").cloned().unwrap_or(Value::empty_dict());
        let via = get_or(&a, "via", "");
        let source = match via.as_str() {
            "git-email" | "git-name" => "from git".to_string(),
            "login" => "from this machine's login".to_string(),
            _ => via.clone(),
        };
        let weak = if via == "login" {
            Some("a yes is recorded under this machine's login name; set git user.email to be yourself".to_string())
        } else {
            None
        };
        // split as rb status splits them by who usually acts: the user's calls, and the rest, still open in the record; a
        // call that waits for a confirmation is in a group of its own (pending)
        let human = |i: &&Value| i.get("who").and_then(|v| v.as_str()) == Some("human");
        let open_items: Vec<&Value> = list_of(&st, "open").iter().filter(|i| !human(i)).collect();
        let pending: Vec<PendingRow> = pending_rows(&st).into_iter().map(PendingRow::drawn).collect();
        let (mut agent_line, mut open_line, mut besides_detail) = (None, None, None::<String>);
        if !open_items.is_empty() {
            let unknown: Vec<&&Value> = open_items.iter().filter(|i| i.get("code").and_then(|v| v.as_str()) == Some("unknown")).collect();
            let names: HashSet<String> = unknown.iter().map(|i| get_or(i, "subject", "").rsplit('.').next().unwrap_or("").to_string()).collect();
            let detail = if !unknown.is_empty() && names.len() == 1 && names.contains("sha256") {
                format!(" · {} checkpoint hash{} missing", unknown.len(), if unknown.len() == 1 { "" } else { "es" })
            } else if !unknown.is_empty() {
                format!(" · {} setting{} unknown", unknown.len(), if unknown.len() == 1 { "" } else { "s" })
            } else {
                String::new()
            };
            agent_line = Some(format!("{} open{detail}", open_items.len()));
            open_line = Some(format!("{} still open in the record{detail}", open_items.len()));
            besides_detail = Some(detail.clone());
        }
        let (asks, unplaced, uncited, uncited_line) = found(led);
        // what rb looks at and when it last did, for the place it watches; a past version or another place shown in a
        // preview is not looked at
        let scope = (!crate::dry::active()).then(|| crate::journal::Scope::of(led));
        let rows = tree(&st);
        let t = st.get("tally").cloned().unwrap_or(Value::empty_dict());
        let good = t.get("meets").and_then(|v| v.as_i64()).unwrap_or(0) + t.get("reproduced").and_then(|v| v.as_i64()).unwrap_or(0);
        let since_line = match since_from {
            Some(baseline) => since(led, baseline)?,
            None => None,
        };
        let tally = tally_of(&st);
        let muted = muted(&list_of(&st, "findings").iter().collect::<Vec<_>>());
        let live_lines = live(led, 3);
        let st_dict = match &st {
            Value::Dict(d) => d.clone(),
            _ => Dict::new(),
        };
        let mut qbriefs = briefs(led, &st_dict)?;
        // what code the runs each reading counts ran, where they were recorded in more than one place
        let code = CodeNotes::of(led)?;
        if !code.observed.is_empty() {
            let notes: IndexMap<String, String> = list_of(&st, "findings").iter().map(|f| (get_or(f, "criterion", ""), code.said(&counted_runs_of(f)))).collect();
            for b in qbriefs.iter_mut() {
                for fl in b.experiments.iter_mut().flat_map(|e| e.findings.iter_mut()).chain(b.inquiry_findings.iter_mut()) {
                    fl.other_code = notes.get(&fl.criterion_id).cloned().unwrap_or_default();
                }
            }
        }
        let observed = observed_runs(led)?;
        if !observed.is_empty() {
            let notes: IndexMap<String, ObservedNote> = list_of(&st, "findings").iter().map(|f| (get_or(f, "criterion", ""), observed_note(f, &observed))).collect();
            for b in qbriefs.iter_mut() {
                for fl in b.experiments.iter_mut().flat_map(|e| e.findings.iter_mut()).chain(b.inquiry_findings.iter_mut()) {
                    if let Some(n) = notes.get(&fl.criterion_id) {
                        fl.observed = n.text.clone();
                        fl.observed_short = n.short.clone();
                        fl.observed_places = n.places.clone();
                    }
                }
            }
        }
        let loose = loose_criteria(&st, &qbriefs);
        let calls = waiting_calls(led, &st, &qbriefs, &loose)?;
        // the calls waiting on you, one per subject, as Your call counts them
        let waiting = calls.len();
        let (open_groups, unfiled) = open_groups(&st, &loose, &calls);
        // the things still open as the screens list them (DESIGN-PASS-1 target 2): every line that counts them says the
        // same number as the list does
        let open_things = open_groups.iter().map(|g| g.get("n").and_then(Value::as_i64).unwrap_or(0) as usize).sum::<usize>() + list_of(&unfiled, "open_items").len();
        let open_besides = besides_detail.map(|d| open_besides_line(open_things, &d));
        let quiet = quiet_line(waiting, asks.len(), !rows.is_empty(), good, open_things, &pending);
        let quiet_away = quiet_line_away(waiting, asks.len(), !rows.is_empty(), good, open_things, &pending);
        let runs = run_rows(led, 120)?;
        let resources = resource_rows(led)?;
        let notes = live_notes(&proj)
            .map(|n| {
                let who = shown(&get_or(n, "by", ""));
                let day: String = get_or(n, "at", "").chars().take(10).collect();
                let label = note_label(get_truthy(n, "id").as_deref());
                note_said(&format!("{label} ({}{day}): {}", if who.is_empty() { String::new() } else { format!("{who}, ") }, get_or(n, "text", "")), &get_or(n, "words", ""), &get_or(n, "by", ""))
            })
            .collect();
        let counts = record_counts(led, &st, waiting)?;
        let (made, weakened) = record_made_with(led, &st)?;
        let last_write = last_write(led)?;
        Ok(Home {
            title: get_or(&proj, "title", ""),
            began: led.record_start()?.map(|s| s.words()),
            project_id: get_or(&proj, "id", ""),
            you: format!("you: {} · {source}", human_name(&get_or(&a, "id", ""))),
            since: since_line,
            found: asks,
            unplaced,
            uncited: uncited.len(),
            uncited_paths: uncited.iter().map(|u| (u.path.clone(), u.at.clone())).collect(),
            uncited_line: uncited_line.as_ref().map(|l| l.0.clone()),
            uncited_short: uncited_line.map(|l| l.1),
            scope: scope.as_ref().map(|s| s.said()),
            scope_short: scope.map(|s| s.said_short()),
            agent_line,
            open_line,
            open_besides,
            tree: rows,
            quiet,
            quiet_away,
            waiting,
            calls,
            open_items: open_groups,
            unfiled,
            tally,
            muted,
            live: live_lines,
            weak_identity: weak,
            briefs: qbriefs,
            runs,
            resources,
            notes,
            pending,
            counts,
            made,
            weakened,
            last_write,
        })
    })
}

/// What a record holds, counted as rb status counts it: its questions, experiments, criteria, runs that stand and those
/// taken back, analyses and resources; the calls waiting on you (D2's count, one per subject); and its writes, the
/// log's lines but those that only say what a session runs on.
pub fn record_counts(led: &Ledger, st: &Value, calls: usize) -> Res<Value> {
    let n = |k: &str| st.get("counts").and_then(|c| c.get(k)).and_then(Value::as_i64).unwrap_or(0);
    let runs = led.all::<Run>()?;
    let writes = led.log_entries()?.iter().filter(|r| !crate::ledger::is_session_line(r)).count();
    Ok(crate::obj! {
        "questions" => n("question_count"), "experiments" => n("experiment_count"), "criteria" => n("criterion_count"), "runs" => n("run_count"),
        "retracted" => runs.iter().filter(|r| r.retracted.is_some()).count(), "analyses" => n("analysis_count"), "resources" => n("resource_count"),
        "calls" => calls, "writes" => writes
    })
}

/// How a record was made, counted from it on each read (SHELL-SPEC 12): of its criteria, how many were declared after
/// their evidence existed (each finding's timing); of its runs that stand, how many record their command as given
/// (made when recorded, watched by rb journal run, or cited from a log line, never one given later); and rb's line for
/// both, "All 6 rules were written after their results came in · 2 of 8 runs record their command (as given)".
pub fn record_made(led: &Ledger, st: &Value) -> Res<Value> {
    Ok(record_made_with(led, st)?.0)
}

/// What weakens the whole body of evidence, by id, as `record_made` counts it: the criteria declared after their
/// evidence existed, and the runs that stand and do not record their command (as given).
#[derive(Debug, Clone, Default)]
pub struct Weakened {
    pub late: Vec<String>,
    pub no_command: Vec<String>,
}

/// `record_made`, with the ids it counts.
pub fn record_made_with(led: &Ledger, st: &Value) -> Res<(Value, Weakened)> {
    let findings = list_of(st, "findings");
    let criteria = findings.len();
    let late: Vec<String> = findings.iter().filter(|f| get_or(f, "timing", "") == "after_evidence").map(|f| get_or(f, "criterion", "")).collect();
    let after = late.len();
    let runs = led.standing_runs()?;
    let no_command: Vec<String> = runs.iter().filter(|r| !crate::ledger::support::run_command(r).map_or(false, |g| g.made_it())).map(|r| r.id.clone()).collect();
    let with = runs.len() - no_command.len();
    let crit = match (after, criteria) {
        (_, 0) => String::new(),
        // PLAIN-WORDS-PASS-1: rules written after their results came in, as the Overview's integrity line says it
        (0, _) => "no rule was written after its results came in".to_string(),
        (1, 1) => "The one rule was written after its results came in".to_string(),
        (a, n) if a == n => format!("All {n} rules were written after their results came in"),
        (a, n) => format!("{a} of {n} rules {} written after {} results came in", if a == 1 { "was" } else { "were" }, if a == 1 { "its" } else { "their" }),
    };
    let ran = match (with, runs.len()) {
        (_, 0) => String::new(),
        (0, _) => "no run records its command (as given)".to_string(),
        (1, 1) => "the one run records its command (as given)".to_string(),
        (w, r) if w == r => format!("all {r} runs record their command (as given)"),
        (w, r) => format!("{w} of {r} runs {} (as given)", if w == 1 { "records its command" } else { "record their command" }),
    };
    let line = [crit, ran].into_iter().filter(|x| !x.is_empty()).collect::<Vec<_>>().join(" · ");
    let line = up_first_char(&line);
    Ok((crate::obj! {"criteria" => criteria, "after_evidence" => after, "runs" => runs.len(), "with_command" => with, "line" => line}, Weakened { late, no_command }))
}

fn up_first_char(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
        None => String::new(),
    }
}

/// When a record was last written, as its log says: its last write's time, to the minute in UTC; a line that only
/// says what a session runs on is no write.
pub fn last_write(led: &Ledger) -> Res<String> {
    Ok(led
        .log_entries()?
        .iter()
        .rev()
        .find(|r| !crate::ledger::is_session_line(r))
        .and_then(|r| r.get("at").and_then(Value::as_str).map(crate::ledger::support::minute))
        .unwrap_or_default())
}

/// What waits for a confirmation, as the user's screens list it in a group of its own: each call's id, whether it is
/// proposed or a past call mentioned, the call in rb's words over the record's own with the words given with it (never
/// as the user's), and the command that confirms it. It names no writer, and a page leaves it out.
pub fn pending_rows(st: &Value) -> Vec<PendingRow> {
    let designs: HashSet<String> = list_of(st, "designs_pending").iter().map(|d| get_or(d, "experiment", "")).collect();
    list_of(st, "pending")
        .iter()
        .map(|d| {
            let id = get_or(d, "id", "");
            let past = d.get("past").filter(|p| p.truthy());
            let authority = d.get("authority").cloned().unwrap_or(Value::empty_dict());
            let mut text = match past.and_then(|p| Past::validate(p, &mut Vec::new(), &mut Vec::new())) {
                None => format!("{} {}{}", get_or(d, "action", ""), get_or(d, "subject", ""), crate::ledger::session_note(&get_or(d, "why", ""))),
                // the document's words, with file and line, are what it rests on; the reason given is a note on it
                Some(p) => {
                    let src = p.source.as_ref().map(|s| format!(": {}", crate::ledger::source_said(s))).unwrap_or_default();
                    let why = crate::ledger::session_note(&get_or(d, "why", ""));
                    format!("{} {} ({}){src}{why}", get_or(d, "action", ""), get_or(d, "subject", ""), crate::ledger::past_when(&p))
                }
            };
            text = with_words(&text, &get_truthy(d, "words").unwrap_or_default(), &get_or(d, "by", ""));
            if let Some(src) = past.and_then(|p| Past::validate(p, &mut Vec::new(), &mut Vec::new())).and_then(|p| p.source) {
                text.push_str(&format!(" · {}", page_words(&get_or(&authority, "basis", ""), &src.path, Some(src.line))));
            }
            text.push_str(&crate::ledger::runs_after_of(d));
            let kind = if past.is_some() { "past" } else { "proposed" };
            PendingRow { id: id.clone(), kind: kind.into(), text, confirm: format!("rb confirm {id}"), lines: Vec::new(), subject: get_or(d, "subject", "") }
        })
        // then each gate a session set whose design does not wait with it
        .chain(list_of(st, "gates_pending").iter().filter(|g| !designs.contains(&get_or(g, "experiment", ""))).map(|g| {
            let (e, holds, not, words) = (get_or(g, "experiment", ""), get_or(g, "if_holds", ""), get_or(g, "if_not", ""), get_or(g, "words", ""));
            let confirm = get_or(g, "confirm", "");
            let id = confirmed_id(&confirm).unwrap_or(&e).to_string();
            // set by a session, which is why it waits: said as gate_parts says one, with no command of its own
            PendingRow { id, kind: "gate".into(), text: format!("{e} gates: {}", gate_said(&holds, &not, &words, "agent:", "")), confirm, ..Default::default() }
        }))
        // then each design that waits, with its gate: named by its experiment, its parts as rb confirm lists them
        .chain(list_of(st, "designs_pending").iter().map(|d| {
            let (e, title) = (get_or(d, "experiment", ""), get_or(d, "title", ""));
            let confirm = get_or(d, "confirm", "");
            let id = confirmed_id(&confirm).unwrap_or(&e).to_string();
            let text = if title.is_empty() || title == e { format!("{e}'s design") } else { format!("{e}'s design: {title}") };
            PendingRow { id, kind: "design".into(), text, confirm, lines: list_of(d, "lines").iter().map(py_str).collect(), ..Default::default() }
        }))
        .collect()
}

impl PendingRow {
    /// The row as a screen draws it: what a screen would hide or act on as its escape, as rb confirm lists it, so the user
    /// confirms what they read. The record's text, as --json, MCP and the SDK give it, stays as written.
    pub fn drawn(mut self) -> PendingRow {
        self.text = crate::ledger::design::visible(&self.text);
        self
    }
}

/// The rows one command confirms as they are listed, each by the id it is listed with (a call alone on its subject,
/// since which of several is the user's is theirs to say; a gate or a design by its hash), and that command:
/// `rb confirm --all` confirms what reads when it runs, which a session may have changed or added to since.
pub fn confirm_listed(rows: &[PendingRow]) -> (Vec<PendingRow>, String) {
    let alone = |r: &PendingRow| r.subject.is_empty() || rows.iter().filter(|x| x.subject == r.subject).count() == 1;
    let named: Vec<PendingRow> = rows.iter().filter(|r| alone(r)).cloned().collect();
    let cmd = format!("rb confirm {}", named.iter().map(|r| r.id.as_str()).collect::<Vec<_>>().join(" "));
    (named, cmd)
}

/// The line under what waits that confirms all of it as listed, where more than one waits and no design does (a
/// design is read before it is confirmed, so rb confirm lists it first): `Confirm all 3: rb confirm <id> <id> <id>`.
pub fn confirm_all_line(rows: &[PendingRow]) -> Option<(String, String)> {
    let (named, cmd) = confirm_listed(rows);
    if named.len() < 2 || rows.iter().any(|r| r.kind == "design") {
        return None;
    }
    let n = named.len();
    let count = if named.iter().any(|r| r.kind == "gate") { format!("{n} ({})", waiting_said(&named)) } else { n.to_string() };
    Some((format!("Confirm {}{count}", if n == rows.len() { "all " } else { "" }), cmd))
}

/// What waits for a confirmation, counted by kind: "3 calls and 1 design".
pub fn waiting_said(rows: &[PendingRow]) -> String {
    let n = |k: usize, one: &str, many: &str| if k == 1 { format!("1 {one}") } else { format!("{k} {many}") };
    let count = |kind: &str| rows.iter().filter(|r| r.kind == kind).count();
    let (gates, designs) = (count("gate"), count("design"));
    let calls = rows.len() - gates - designs;
    let said: Vec<String> = [(calls, "call", "calls"), (gates, "gate", "gates"), (designs, "design", "designs")]
        .into_iter()
        .filter(|(k, _, _)| *k > 0)
        .map(|(k, one, many)| n(k, one, many))
        .collect();
    match said.as_slice() {
        [] => n(0, "call", "calls"),
        [one] => one.clone(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}

/// The one line rb status, rb context and a page's summary say for every experiment whose design waits, however many:
/// how many, and that rb confirm lists them (a status of one experiment: that rb confirm <experiment> lists it); at a
/// past version, which offers no command, how many alone.
pub fn designs_line(rows: &[PendingRow], scope: Option<&str>) -> Option<String> {
    match rows.iter().filter(|r| r.kind == "design").count() {
        0 => None,
        n if at_past_version() => Some(format!("The design of {n} experiment{} is not confirmed.", if n == 1 { "" } else { "s" })),
        1 => Some(format!("The design of 1 experiment is not confirmed: rb confirm {}lists it.", scope.map(|x| format!("{x} ")).unwrap_or_default())),
        n => Some(format!("The design of {n} experiments is not confirmed: rb confirm lists them.")),
    }
}

/// `n` open items in the record that are not calls (some ask for work, some for nothing), and what kind of unknown they
/// are (`detail`, ' · ...' or nothing), as the shell's Overview says them under what needs a judgment: one wording in
/// every voice, saying what they are not so the count is never read as more of what waits.
fn open_besides_line(n: usize, detail: &str) -> String {
    if n == 1 { format!("Also open: 1 item that needs no decision{detail}") } else { format!("Also open: {n} items that need no decision{detail}") }
}

/// The receipt as stored, projected for the on-demand card: every value verbatim, every label a stored field's name, and
/// its config check as it bears on what the run was given. Who recorded it, how rb knew, through what, in which session
/// and on which branch or in which worktree stay in the record: a screen shows none of them. The machine's platform is the user's own
/// screens' (rb open, rb ui), never a page's.
pub fn receipt_card(run: &Run) -> Dict {
    let rc = &run.receipt;
    let cfg = match rc.config.as_ref().map(|c| c.given()) {
        Some(c) => crate::obj! {
            "sha256" => c.sha256.clone(), "matches" => Value::list_of_str(c.matches.clone()),
            "mismatches" => Value::List(c.mismatches.iter().cloned().map(Value::Dict).collect()), "absent" => Value::list_of_str(c.absent.clone())
        },
        None => Value::Null,
    };
    let uses = |us: &[ResourceUse]| -> Value {
        Value::List(
            us.iter()
                .map(|u| {
                    crate::obj! {"resource" => u.resource.clone(), "role" => u.role.clone(), "availability" => u.availability.clone().unwrap_or_default(),
                    "sha256" => u.sha256.clone().unwrap_or_default()}
                })
                .collect(),
        )
    };
    let mut attached = rc.attached.clone().unwrap_or_default();
    attached.shift_remove("branch");
    let worktree = attached.shift_remove("worktree").and_then(|w| w.get("path").and_then(Value::as_str).map(str::to_string));
    let worktree = worktree.as_deref();
    let mut card = crate::dict! {
        "at" => rc.at.clone(),
        "command" => rc.command.clone().unwrap_or_default(),
        "attached" => Value::Dict(attached),
        "produced" => Value::Dict(rc.produced.clone()),
        "environment" => Value::Dict(
            rc.environment.iter().filter(|(k, _)| k.as_str() != "platform" || reader() != Reader::Page).map(|(k, v)| (k.clone(), Value::Str(py_str(v)))).collect()
        ),
        // its environment is the recorder's, not the one it ran in
        "made_before" => crate::ledger::made_before_recorded(run),
        "config" => cfg,
        // a database row's numbers are a row's, though the record keeps `file`
        "basis" => if run.basis == "file" && run.receipt.row.is_some() { "row".to_string() } else { run.basis.clone() }, "spec_frozen" => run.spec_frozen, "synthetic" => run.synthetic,
        "files" => Value::List(run.files.iter().map(|f| inside_of(crate::obj! {"path" => kept_path(&f.path, worktree), "sha256" => f.sha256.clone(), "bytes" => f.bytes}, &f.path, run)).collect()),
        "inputs" => uses(&run.inputs),
        "outputs" => uses(&run.outputs),
        "host" => Value::Dict(run.host.iter().map(|(k, v)| (k.clone(), Value::Str(v.clone()))).collect()),
        "links" => Value::Dict(run.links.iter().map(|(k, v)| (k.clone(), Value::Str(v.clone()))).collect()),
        "note" => if run.note.is_empty() { String::new() } else { run.note.clone() + not_confirmed(&run.created_by) },
        // when it was produced and recorded, how rb knows its command made it, and a file written after it
        "dated" => crate::ledger::support::produced_line(run)
    };
    let given = crate::ledger::support::run_command(run);
    card.insert("command_label".into(), Value::Str(given.as_ref().map(|g| g.label().to_string()).unwrap_or_default()));
    // the log line it cites, named as this reader names a path: inside a linked worktree, by its path there
    let mut how = given.as_ref().filter(|g| g.given != "at_the_time").map(|g| g.how.clone()).unwrap_or_default();
    if let Some(seen) = rc.extra.get("command_seen") {
        if let Some(p) = seen.get("path").and_then(Value::as_str).filter(|p| !p.is_empty()) {
            // the file and its line as one, so the place said after it follows both
            let line = seen.get("line").map(py_str).unwrap_or_default();
            let cited = if line.is_empty() { p.to_string() } else { format!("{p}:{line}") };
            let said = page_path(&cited, worktree);
            if said != cited {
                how = how.replace(&cited, &said);
            }
            // and the file alone, where rb's words name it without its line
            let file = page_path(p, worktree);
            if file != p {
                how = how.replace(p, &file);
            }
        }
    }
    card.insert("command_how".into(), Value::Str(how));
    card.insert("written_after".into(), Value::Str(crate::ledger::support::file_sentence(run).unwrap_or_default()));
    // the row of a database its numbers were read from, as stored: database, table, key, rowid and each value's column
    if let Some(row) = &rc.row {
        let mut d = row.dump(false);
        d.retain(|k, _| !row.extra.contains_key(k));
        let path = row.path.clone();
        if let Some(Value::Str(p)) = d.get_mut("path") {
            *p = kept_path(p, worktree);
        }
        card.insert("row".into(), inside_of(Value::Dict(d), &path, run));
    }
    card
}

/// A recorded path as a receipt's card keeps it: whole on the user's screens, which say it in short beside it
/// (`inside_of`), and as a page names it.
fn kept_path(path: &str, worktree: Option<&str>) -> String {
    if reader() == Reader::Screen {
        path.to_string()
    } else {
        page_path(path, worktree)
    }
}

/// A receipt's file or row, on the user's screens, with how they name its path when it is inside a linked worktree
/// nested in the record: `inside` (its path inside that worktree), `in_place` (the worktree, in short) and
/// `recorded_there` (whether the run was recorded in that worktree, which its card says once).
fn inside_of(mut v: Value, path: &str, run: &Run) -> Value {
    if let (Value::Dict(d), Some(i)) = (&mut v, screen_path(path)) {
        let there = recorded_in(run, &i);
        d.insert("inside".into(), Value::Str(i.path));
        d.insert("in_place".into(), Value::Str(i.place));
        d.insert("recorded_there".into(), Value::Bool(there));
    }
    v
}

/// Whether `run` was recorded in the worktree a file is inside.
fn recorded_in(run: &Run, i: &Inside) -> bool {
    let own = run.receipt.attached.as_ref().and_then(|a| a.get("worktree")).and_then(|w| w.get("path")).and_then(Value::as_str);
    own.map(|o| crate::sources::resolve_path(std::path::Path::new(o))).as_ref() == Some(&i.top)
}

/// A run's file path as its card says it on the user's screens: inside a linked worktree, by its path there, with
/// the worktree said beside it unless it is the one the run was recorded in, which the card says once.
fn run_file_said(path: &str, run: &Run) -> String {
    let Some(i) = screen_path(path) else { return path.to_string() };
    if recorded_in(run, &i) {
        i.path
    } else {
        format!("{} (in {})", i.path, i.place)
    }
}

/// Every run as one drawer row, newest first.
pub fn run_rows(led: &Ledger, limit: usize) -> Res<Vec<RunRow>> {
    let titles: IndexMap<String, String> = led.all::<Experiment>()?.iter().map(|x| (x.id.clone(), if x.title.is_empty() { x.id.clone() } else { x.title.clone() })).collect();
    let observed = observed_runs(led)?;
    let mut rows: Vec<RunRow> = Vec::new();
    for r in led.all::<Run>()?.iter() {
        let pairs: Vec<String> = r.metrics.iter().map(|(k, v)| format!("{k}={}", show(&Value::Float(*v)))).collect();
        let results = pairs.iter().take(6).cloned().collect::<Vec<_>>().join(" · ") + &if pairs.len() > 6 { format!(" · +{} more", pairs.len() - 6) } else { String::new() };
        rows.push(RunRow {
            when: r.created_at.chars().take(10).collect(),
            experiment: titles.get(&r.experiment).cloned().unwrap_or_else(|| r.experiment.clone()),
            results: if results.is_empty() { "no numbers".into() } else { results },
            by: shown(&r.created_by),
            note: r.retracted.as_ref().map(retraction_words).unwrap_or_default(),
            experiment_id: r.experiment.clone(),
            run_id: r.id.clone(),
            metrics: r.metrics.iter().map(|(k, v)| (k.clone(), Value::Str(show(&Value::Float(*v))))).collect(),
            per_run: r.per_run.iter().map(|(k, v)| (k.clone(), Value::Str(show(v)))).collect(),
            // a session id is never on a screen; the record keeps it
            session: String::new(),
            produced: r.produced_at.as_deref().filter(|p| !p.is_empty() && r.produced_basis != "unknown").map(|p| p.chars().take(10).collect()).unwrap_or_default(),
            facts: {
                let card = Value::Dict(receipt_card(r));
                let (mut facts, blockers) = run_facts(r, &card);
                facts.extend(blockers);
                facts
            },
            receipt: receipt_card(r),
            observed_in: observed.get(&r.id).map(|o| o.value.clone()),
        });
    }
    rows.reverse();
    rows.truncate(limit);
    Ok(rows)
}

pub fn resource_rows(led: &Ledger) -> Res<Vec<ResourceRow>> {
    let runs = led.standing_runs()?;
    let mut out = Vec::new();
    for res in led.live::<Resource>()? {
        let av = Value::Dict(led.availability(&res));
        let ident = av.get("identity").cloned().unwrap_or(Value::empty_dict());
        out.push(ResourceRow {
            resource_id: res.id.clone(),
            name: res.name.clone() + &if res.version.is_empty() { String::new() } else { format!(" {}", res.version) },
            r#type: res.r#type.clone(),
            availability: get_or(&av, "state", ""),
            where_: list_of(&av, "locations").iter().map(|l| location_said(l, false)).collect(),
            where_short: list_of(&av, "locations").iter().map(|l| location_said(l, true)).collect(),
            how: get_truthy(&av, "how").unwrap_or_default(),
            used_by: runs.iter().filter(|r| r.inputs.iter().chain(r.outputs.iter()).any(|u| u.resource == res.id)).count() as i64,
            identity: get_or(&ident, "text", ""),
            exact: get_or(&ident, "level", "") == "exact",
        });
    }
    Ok(out)
}

/// One line of a run's receipt: the receipt's field it says (`field`: produced, run, command, inputs, outputs, host,
/// repository, config, basis, environment, results, row, paths, links, note, retracted), rb's label for it, what it
/// says, the ids it names, and whether it stops the run counting.
#[derive(Debug, Clone, Default)]
pub struct RunPart {
    pub field: &'static str,
    pub label: String,
    pub text: String,
    pub refs: Vec<String>,
    pub blocker: bool,
}

impl RunPart {
    /// The line as the run's card says it: "<label>: <text>", or the text where it has no label.
    pub fn line(&self) -> String {
        if self.label.is_empty() {
            self.text.clone()
        } else {
            format!("{}: {}", self.label, self.text)
        }
    }

    /// The line as a receipt row says it beside its field: the text alone where the label only names the field.
    pub fn row_text(&self) -> String {
        if self.label == self.field || (self.field == "results" && self.label == "file") {
            self.text.clone()
        } else {
            self.line()
        }
    }
}

/// What a run's card says of it, read from its receipt card: when it was produced and recorded, what it read and
/// made, its command and how rb knows that command made it, the repository when it was recorded, its files (and when
/// the one its numbers came from was written, after the run), and what stops it counting.
pub fn run_facts(run: &Run, rc: &Value) -> (Vec<String>, Vec<String>) {
    let parts = run_parts(run, rc);
    (parts.iter().filter(|p| !p.blocker).map(RunPart::line).collect(), parts.iter().filter(|p| p.blocker).map(RunPart::line).collect())
}

/// `run_facts` in its parts, each with the receipt's field it says (RunPart): one rule for the run's card and its
/// receipt rows.
pub fn run_parts(run: &Run, rc: &Value) -> Vec<RunPart> {
    let cut = |s: &str, n: usize| s.chars().take(n).collect::<String>();
    let part = |field: &'static str, label: &str, text: String| RunPart { field, label: label.to_string(), text, ..Default::default() };
    let mut out: Vec<RunPart> = Vec::new();
    let dated = get_or(rc, "dated", "");
    if !dated.is_empty() {
        out.push(part("produced", "", dated));
    }
    if !run.per_run.is_empty() {
        out.push(part("run", "run", run.per_run.iter().map(|(k, v)| format!("{k}={}", show(v))).collect::<Vec<_>>().join(" · ")));
    }
    let cmd = get_or(rc, "command", "");
    if !cmd.is_empty() {
        // a command given later reads as one, never as the command that made the numbers
        out.push(part("command", &get_or(rc, "command_label", "command (as given)"), cmd));
        let how = get_or(rc, "command_how", "");
        if !how.is_empty() {
            out.push(part("command", "how rb knows", how));
        }
    }
    for (word, key) in [("read", "inputs"), ("made", "outputs")] {
        for u in list_of(rc, key) {
            let (res, role, avail, sha) = (get_or(u, "resource", ""), get_or(u, "role", ""), get_or(u, "availability", ""), get_or(u, "sha256", ""));
            let text = res.clone()
                + &if role.is_empty() { String::new() } else { format!(" ({role})") }
                + &if avail.is_empty() { String::new() } else { format!(" · {avail} when recorded") }
                + &if sha.is_empty() { String::new() } else { format!(" · sha256 {}", cut(&sha, 12)) };
            out.push(RunPart { field: if key == "inputs" { "inputs" } else { "outputs" }, label: word.into(), text, refs: vec![res], blocker: false });
        }
    }
    if let Some(Value::Dict(h)) = rc.get("host") {
        if !h.is_empty() {
            out.push(part("host", "host", h.iter().map(|(k, v)| format!("{k}={}", py_str(v))).collect::<Vec<_>>().join(" · ")));
        }
    }
    let at = rc.get("attached").cloned().unwrap_or(Value::empty_dict());
    if at.truthy() {
        let parts: Vec<String> = [
            if at.get("commit").map_or(false, |v| v.truthy()) { format!("commit {}", cut(&get_or(&at, "commit", ""), 10)) } else { "no commit".into() },
            if at.get("dirty").map_or(false, |v| v.truthy()) { "tree dirty".into() } else { "tree clean".into() },
        ]
        .into_iter()
        .filter(|x| !x.is_empty())
        .collect();
        let when = if rc.get("made_before").map_or(false, |v| v.truthy()) { "repository when recorded, not when run" } else { "repository when recorded" };
        out.push(part("repository", when, parts.join(" · ")));
    }
    if let Some(cfg) = rc.get("config").filter(|v| v.truthy()) {
        let absent: Vec<String> = list_of(cfg, "absent").iter().map(py_str).collect();
        out.push(part(
            "config",
            "config",
            format!("{} matched", list_of(cfg, "matches").len()) + &if absent.is_empty() { String::new() } else { format!(" · absent: {}", absent.join(", ")) },
        ));
        for m in list_of(cfg, "mismatches") {
            out.push(RunPart { blocker: true, ..part("config", &get_or(m, "name", ""), format!("spec {}, ran {}", get_or(m, "spec", ""), get_or(m, "ran", ""))) });
        }
    }
    // the spec locked, as a screen names a freeze (SHELL-SPEC's "locked")
    out.push(part(
        "basis",
        "basis",
        get_or(rc, "basis", "")
            + if rc.get("spec_frozen").map_or(false, |v| v.truthy()) { " · spec locked" } else { " · spec not locked" }
            + if rc.get("synthetic").map_or(false, |v| v.truthy()) { " · synthetic" } else { "" },
    ));
    let env: Vec<String> = match rc.get("environment") {
        Some(Value::Dict(e)) => e.iter().map(|(k, v)| format!("{k} {}", py_str(v))).collect(),
        _ => Vec::new(),
    };
    out.push(part(
        "environment",
        "environment",
        env.join(" · ") + if rc.get("made_before").map_or(false, |v| v.truthy()) { " (where it was recorded: the run was made before it was recorded)" } else { "" },
    ));
    // on the user's screens a path inside a linked worktree is said in short, and kept whole once, last
    let mut whole: Vec<String> = Vec::new();
    for f in list_of(rc, "files") {
        let bytes = get_or(f, "bytes", "");
        let unit = if bytes == "1" { "byte" } else { "bytes" };
        let path = get_or(f, "path", "");
        let said = run_file_said(&path, run);
        if said != path {
            whole.push(path);
        }
        out.push(part("results", "file", format!("{said} · sha256 {} · {bytes} {unit}", cut(&get_or(f, "sha256", ""), 12))));
    }
    let after = get_or(rc, "written_after", "");
    if !after.is_empty() {
        out.push(part("results", "", after));
    }
    if let Some(row) = &run.receipt.row {
        let mut row = row.clone();
        let worktree = run.receipt.attached.as_ref().and_then(|a| a.get("worktree")).and_then(|w| w.get("path")).and_then(Value::as_str);
        if reader() == Reader::Screen {
            let said = run_file_said(&row.path, run);
            if said != row.path && !whole.contains(&row.path) {
                whole.push(row.path.clone());
            }
            row.path = said;
        } else {
            row.path = page_path(&row.path, worktree);
        }
        let row = &row;
        out.push(part("row", "row", crate::cmds::record::row_words(row)));
        out.push(part("row", "columns", crate::cmds::record::row_columns(row).join("; ")));
    }
    if !whole.is_empty() {
        out.push(part("paths", if whole.len() == 1 { "path as recorded" } else { "paths as recorded" }, whole.join(", ")));
    }
    if let Some(Value::Dict(l)) = rc.get("links") {
        for (k, v) in l {
            out.push(part("links", k, py_str(v)));
        }
    }
    let note = get_or(rc, "note", "");
    if !note.is_empty() {
        out.push(part("note", "note", note));
    }
    if let Some(r) = &run.retracted {
        out.push(RunPart { blocker: true, ..part("retracted", "", retraction_words(r)) });
    }
    out
}

/// A question, experiment, criterion, run or resource, opened from the catalogue.
/// An experiment's settings as its card counts them, in one line ("settings: 2 per-run · 3 verified"; "" with none),
/// each counted as rb status counts it, and the settings with a problem, each with rb's words for it.
pub fn settings_of(led: &Ledger, exp: &Experiment) -> Res<(String, Vec<(String, &'static str)>)> {
    let mut tally: IndexMap<String, i64> = IndexMap::new();
    let mut problems: Vec<(String, &'static str)> = Vec::new();
    let caveats = led.setting_caveats(exp, led.has_origin(exp)?, None)?;
    for (nm, s) in exp.all_settings() {
        // counted as rb status counts it: a source that states another value or no longer states it, on record or as
        // this read finds it, under that word
        let now = crate::ledger::source_problem(&caveats, exp, &nm);
        let key = if s.per_run {
            "per-run".to_string()
        } else {
            match now {
                Some(cv) => cv.code.clone(),
                None => led.status_read(exp, &nm, s)?,
            }
        };
        *tally.entry(key).or_insert(0) += 1;
        if s.conflict.is_some() {
            problems.push((nm, "its source states a different value"));
        } else if let Some(cv) = now {
            problems.push((nm, if cv.code == "conflict" { "its source now states another value" } else { "its source no longer states it" }));
        } else if s.value.is_null() && s.required && !s.per_run {
            problems.push((nm, "unknown"));
        }
    }
    if tally.is_empty() {
        return Ok((String::new(), problems));
    }
    let mut items: Vec<(String, i64)> = tally.into_iter().collect();
    items.sort();
    Ok(("settings: ".to_string() + &items.iter().map(|(k, n)| format!("{n} {}", crate::assess::rule_label(k, *n))).collect::<Vec<_>>().join(" · "), problems))
}

/// An experiment's relations as its card says them, one line each: "reproduces: exp_01... (cloned from <address>)",
/// "extends: wid (<note> · not confirmed)".
pub fn relation_lines(exp: &Experiment) -> Vec<String> {
    exp.relations
        .iter()
        .map(|rel| {
            // the note rb clone writes names where the bundle came from: said only when that is a web address, and
            // without credentials or a signed query; never a path on the machine that cloned it (the parent keeps it)
            let note = match rel.note.strip_prefix("cloned from ").filter(|_| rel.kind == "reproduces").map(crate::http::shareable) {
                Some(from) if crate::http::is_address(&from) => format!("cloned from {from}"),
                Some(_) => String::new(),
                None if rel.note.is_empty() => String::new(),
                None => rel.note.clone() + not_confirmed(&rel.by),
            };
            format!("{}: {}", rel.kind.replace('_', " "), rel.target) + &if note.is_empty() { String::new() } else { format!(" ({note})") }
        })
        .collect()
}

pub fn object_card(led: &Ledger, subject: &str) -> Res<Option<Card>> {
    with_screen_trees(led, || led.locked(|| object_card_in(led, subject)))
}

fn object_card_in(led: &Ledger, subject: &str) -> Res<Option<Card>> {
    let st = Value::Dict(led.status(None)?);
    let kind = led.kind_of(subject)?;
    let findings = list_of(&st, "findings");
    let mut c = Card { request_id: subject.to_string(), kind: "object".into(), ..Default::default() };
    let cut = |s: &str, n: usize| s.chars().take(n).collect::<String>();
    match kind {
        Some("run") => {
            let run: Run = led.load(subject)?;
            let rc = Value::Dict(receipt_card(&run));
            let titles: IndexMap<String, String> = led.all::<Experiment>()?.iter().map(|x| (x.id.clone(), if x.title.is_empty() { x.id.clone() } else { x.title.clone() })).collect();
            c.title = titles.get(&run.experiment).cloned().unwrap_or_else(|| run.experiment.clone());
            let lead = run.metrics.iter().map(|(k, v)| format!("{k}={}", show(&Value::Float(*v)))).collect::<Vec<_>>().join(" · ");
            c.lead = if lead.is_empty() { "no numbers".into() } else { lead };
            c.who_line = format!("run · recorded {}{}", cut(&get_or(&rc, "at", ""), 19).replace('T', " "), crate::actor::by_writer(&run.receipt.actor));
            let (facts, blockers) = run_facts(&run, &rc);
            c.facts.extend(facts);
            if reader() == Reader::Screen {
                if let Some(o) = observed_in(&run, &place_shown(led)) {
                    c.facts.push(get_or(&o.value, "label_short", ""));
                }
            }
            c.blockers.extend(blockers);
            return Ok(Some(c));
        }
        Some("experiment") => {
            let Some(e) = list_of(&st, "experiments").iter().find(|x| get_or(x, "id", "") == subject).cloned() else { return Ok(None) };
            let exp: Experiment = led.load(subject)?;
            c.title = get_or(&e, "title", subject);
            let arms = list_of(&e, "variants").iter().map(|v| format!("{} ({})", get_or(v, "name", ""), get_or(v, "role", ""))).collect::<Vec<_>>().join(" vs ");
            let varies: Vec<String> = list_of(&e, "varies").iter().map(py_str).collect();
            c.lead = (if arms.is_empty() { "no variants yet".to_string() } else { arms }) + &if varies.is_empty() { String::new() } else { format!(" · varies {}", varies.join(", ")) };
            let n = e.get("run_count").and_then(|v| v.as_i64()).unwrap_or(0);
            c.who_line = format!(
                "experiment · {} · {} · {n} run{}",
                get_or(&e, "state", "planned"),
                if e.get("frozen").map_or(false, |f| f.truthy()) { "locked" } else { "not locked" },
                if n == 1 { "" } else { "s" }
            );
            // its design's standing, once, with the command while it waits, and what changed since the user confirmed it
            let design = led.design(subject)?.unwrap_or_default();
            let from = c.facts.len();
            c.facts.extend(design.lines());
            c.design_lines = from..c.facts.len();
            for (label, text) in [("purpose", &exp.purpose), ("hypothesis", &exp.hypothesis)] {
                if !text.is_empty() {
                    c.facts.push(format!("{label}: {text}"));
                }
            }
            if design.not_recorded.contains(&"hypothesis or purpose") {
                c.facts.push("hypothesis or purpose: not recorded".into());
            }
            match why_part(&exp.why, &led.why_by(&exp, &design)) {
                Some((label, text)) => c.facts.push(format!("{label}: {text}")),
                None if design.not_recorded.contains(&"why") => c.facts.push("why: not recorded".into()),
                None => (),
            }
            let words = get_or(&e, "gate_words", "");
            let command = gate_command(&exp.id, &exp.if_holds, &exp.if_not, &words, &design.waiting_command());
            for (label, text) in gate_parts(&exp.if_holds, &exp.if_not, &words, &design.writer(&get_or(&e, "gate_by", ""), "gate"), &command, design.gate_first()) {
                c.facts.push(format!("{label}: {text}"));
            }
            if !exp.folder.is_empty() {
                c.facts.push(format!("in the repository: {}", exp.folder));
                for q in &exp.quotes {
                    if ["summary", "result", "limitation", "next"].contains(&q.kind.as_str()) {
                        c.facts.push(format!("{}: \"{}\" ({})", q.kind, q.text, crate::repo::where_of(q, &exp.folder)));
                    }
                }
                c.facts.extend(crate::repo::facts(&crate::repo::read_folder(led, &exp, None)?));
            }
            let (settings, problems) = settings_of(led, &exp)?;
            if !settings.is_empty() {
                c.facts.push(settings);
            }
            let said = led.note_words()?;
            for note in exp.notes.iter().filter(|n| n.retracted.is_none()) {
                let who = shown(&note.by);
                let w = note.id.as_ref().and_then(|id| said.get(id)).cloned().unwrap_or_default();
                let label = note_label(note.id.as_deref());
                c.facts.push(note_said(&if who.is_empty() { format!("{label}: {}", note.text) } else { format!("{label} ({who}): {}", note.text) }, &w, &note.by));
            }
            c.facts.extend(relation_lines(&exp));
            for row in led.rerunnability(&exp)? {
                c.facts.push(format!(
                    "to run again · {}: {} · {}",
                    row.get("what").map(py_str).unwrap_or_default(),
                    row.get("state").map(py_str).unwrap_or_default(),
                    row.get("detail").map(py_str).unwrap_or_default()
                ));
            }
            // a line a setting while six lines hold them, else a line a reason, so no reason is crowded out
            c.blockers = if problems.len() <= 6 {
                problems.iter().map(|(nm, why)| format!("{nm}    {why}")).collect()
            } else {
                let mut by: IndexMap<&str, Vec<&str>> = IndexMap::new();
                for (nm, why) in &problems {
                    by.entry(*why).or_default().push(nm.as_str());
                }
                by.iter()
                    .map(|(why, names)| {
                        let more = if names.len() > 6 { format!(" and {} more", names.len() - 6) } else { String::new() };
                        format!("{}{more}    {why}", names.iter().take(6).copied().collect::<Vec<_>>().join(", "))
                    })
                    .collect()
            };
            let mut runs: Vec<Run> = led.all::<Run>()?.iter().filter(|r| r.experiment == subject).cloned().collect();
            runs.sort_by(|a, b| b.created_at.cmp(&a.created_at));
            for run in runs.iter().take(6) {
                let nums = run.metrics.iter().take(3).map(|(k, v)| format!("{k}={}", show(&Value::Float(*v)))).collect::<Vec<_>>().join(" · ");
                let per = run.per_run.iter().map(|(k, v)| format!("{k}={}", show(v))).collect::<Vec<_>>().join(" · ");
                let tail = run.retracted.as_ref().map(retraction_words).unwrap_or_default();
                let label = [cut(&run.created_at, 10), per, nums, shown(&run.created_by), tail]
                    .into_iter()
                    .filter(|x| !x.is_empty())
                    .collect::<Vec<_>>()
                    .join(" · ");
                c.runs.push((label, run.id.clone()));
            }
            let analyses: Vec<Analysis> = led.all::<Analysis>()?.iter().filter(|a| a.experiment == subject).cloned().collect();
            let from = analyses.len().saturating_sub(4);
            for a in &analyses[from..] {
                let mut comps: Vec<String> = Vec::new();
                for cmp in &a.comparisons {
                    // an interval is its two bounds: one bound alone is not shown as one
                    let [est, lo, hi] = led.comparison_said(subject, cmp)?;
                    let iv = match (cmp.lo, cmp.hi) {
                        (Some(_), Some(_)) => format!(" [{lo}, {hi}]"),
                        _ => String::new(),
                    };
                    comps.push(format!(
                        "{} {}{} {}{iv}",
                        cmp.metric,
                        cmp.subject,
                        if cmp.reference.is_empty() { String::new() } else { format!(" vs {}", cmp.reference) },
                        est
                    ));
                }
                c.facts.push(
                    format!("analysis ({}{}): {}", a.basis, if a.method.is_empty() { String::new() } else { format!(", {}", a.method) }, crate::assess::first_few(&comps, 3, "; "))
                        + if a.retracted.is_some() { " · retracted" } else { "" }
                        + &if a.supersedes.is_empty() { String::new() } else { " · supersedes an earlier one".to_string() + &reason_part(&a.why, &a.created_by, ": ") },
                );
            }
            let decs = led.decisions_on(subject)?;
            let from = decs.len().saturating_sub(3);
            for d in &decs[from..] {
                c.facts.push(
                    with_words(&format!("decision: {}", call_line(&d.action, &d.by, &d.at, &d.why, d.past.as_ref(), led.confirms(&d.id)?)), &d.words, &d.by)
                        + &crate::ledger::runs_after_said(&d.subject, &d.past.as_ref().map(|p| p.when.clone()).unwrap_or_default(), &led.runs_after_past_call(d)?)
                        + &if d.revisit.is_empty() { String::new() } else { format!(" · revisit: {}", d.revisit) },
                );
            }
            c.pass_bars = findings
                .iter()
                .filter(|f| f.get("experiment").map(py_str).as_deref() == Some(subject))
                .map(pass_bar)
                .collect();
            let observed = observed_runs(led)?;
            let code = CodeNotes::of(led)?;
            for f in findings.iter().filter(|f| f.get("experiment").map(py_str).as_deref() == Some(subject)) {
                for note in [observed_note(f, &observed).short, code.said(&counted_runs_of(f))] {
                    if !note.is_empty() {
                        c.facts.push(format!("{}: {note}", get_or(f, "criterion", "")));
                    }
                }
            }
            c.bars_label = if c.pass_bars.is_empty() { String::new() } else { "Its criteria:".into() };
        }
        Some("criterion") => {
            let crit: Criterion = led.load(subject)?;
            let f = match findings.iter().find(|x| get_or(x, "criterion", "") == subject) {
                Some(f) => f.clone(),
                None => Value::Dict(led.finding(&crit)?.dump(false)),
            };
            let said = statement_said(&crit.statement, &crit.words, &crit.created_by);
            let crit_words = if said.is_some() { String::new() } else { crit.words.clone() };
            c.title = said.unwrap_or_else(|| crit.statement.clone());
            c.lead = format!("{} · {}", get_or(&f, "rule", ""), surface::standing_words(&f));
            let on = list_of(&st, "experiments")
                .iter()
                .find(|e| get_or(e, "id", "") == crit.experiment)
                .map(|e| e.get("title").map(py_str).unwrap_or_else(|| "None".into()))
                .unwrap_or_else(|| crit.experiment.clone());
            let who = shown(&crit.created_by);
            c.who_line = with_words(&(format!("criterion · {} · on {on}", crit.role) + &if who.is_empty() { String::new() } else { format!(" · declared by {who}") }), &crit_words, &crit.created_by);
            let review = f.get("review").filter(|r| r.truthy()).cloned().unwrap_or(Value::empty_dict());
            let decided_map: IndexMap<String, String> = list_of(&st, "decisions")
                .iter()
                .filter(|d| d.get("id").map_or(false, |v| v.truthy()))
                .map(|d| (get_or(d, "id", ""), get_truthy(d, "words").unwrap_or_default()))
                .collect();
            let decided = decided_map.get(&get_truthy(&review, "decision").unwrap_or_default()).cloned().unwrap_or_default();
            c.sentences.push(finding_line(&f, &decided).sentence);
            let says = f.get("says").filter(|s| s.truthy()).cloned().unwrap_or(Value::empty_dict());
            for key in ["comparison", "result", "rule", "timing", "units", "review", "history"] {
                if let Some(said) = get_truthy(&says, key).filter(|s| s != "no decision") {
                    let said = match key {
                        "timing" => with_words(&said, &crit_words, &crit.created_by),
                        "review" => review_words(&said, review.get("current").map_or(true, |v| v.truthy()), &decided, &get_or(&review, "by", "")),
                        _ => said,
                    };
                    c.facts.push(format!("{key}: {said}"));
                }
            }
            for o in list_of(&f, "observations") {
                let value = o.get("value").cloned().unwrap_or(Value::Null);
                let said = match value.as_f64() {
                    Some(x) if get_truthy(o, "arm").is_none() => crate::assess::observed(&crit, x, crit.compares()),
                    _ => crate::pyfloat::fmt_num(&value, "g"),
                };
                c.facts.push(
                    format!("{}: {said} · unit {}", get_or(o, "run", ""), get_or(o, "unit", ""))
                        + &if o.get("counted").map_or(false, |v| v.truthy()) { String::new() } else { format!(" · not counted: {}", get_or(o, "reason", "")) }
                        + &get_truthy(o, "repeats").map(|r| format!(" · repeats {r}")).unwrap_or_default()
                        + if o.get("before_declared").map_or(false, |v| v.truthy()) { " · produced before the declaration" } else { "" },
                );
            }
            for note in [observed_note(&f, &observed_runs(led)?).short, CodeNotes::of(led)?.said(&counted_runs_of(&f))] {
                if !note.is_empty() {
                    c.facts.push(note);
                }
            }
            c.looks = list_of(&f, "integrity").iter().map(|x| get_or(x, "text", "")).collect();
            more(&mut c.looks, 4);
        }
        Some("resource") => {
            let res: Resource = led.load(subject)?;
            let av = Value::Dict(led.availability(&res));
            c.title = res.name.clone() + &if res.version.is_empty() { String::new() } else { format!(" {}", res.version) };
            c.lead = format!("{} · {}", res.r#type, get_or(&av, "state", ""));
            c.who_line = format!("resource · {}", av.get("identity").map(|i| get_or(i, "text", "")).unwrap_or_default());
            if !res.subset.is_empty() {
                c.facts.push(format!("subset: {}", res.subset));
            }
            // on the user's screens a local file inside a linked worktree is said in short, and kept whole once, after
            let mut whole: Vec<String> = Vec::new();
            for loc in list_of(&av, "locations") {
                let said = location_said(loc, true);
                if reader() == Reader::Screen && said != location_said(loc, false) {
                    whole.push(get_or(loc, "ref", ""));
                }
                c.facts.push(said.replacen(':', ": ", 1));
            }
            if !whole.is_empty() {
                c.facts.push(format!("{}: {}", if whole.len() == 1 { "path as recorded" } else { "paths as recorded" }, whole.join(", ")));
            }
            if let Some(how) = get_truthy(&av, "how") {
                c.facts.push(format!("how to get it again: {how}"));
            }
            let users: Vec<Run> = led.standing_runs()?.iter().filter(|r| r.inputs.iter().chain(r.outputs.iter()).any(|u| u.resource == res.id)).cloned().collect();
            for r in users.iter().take(6) {
                let nums = r.metrics.iter().take(3).map(|(k, v)| format!("{k}={}", show(&Value::Float(*v)))).collect::<Vec<_>>().join(" · ");
                let who = shown(&r.created_by);
                c.runs.push((format!("{} · {}{nums}", cut(&r.created_at, 10), if who.is_empty() { String::new() } else { format!("{who} · ") }), r.id.clone()));
            }
        }
        _ => {
            let qs = list_of(&st, "questions");
            let Some(q) = qs.iter().find(|x| get_or(x, "id", "") == subject).cloned() else { return Ok(None) };
            c.title = get_or(&q, "text", subject);
            c.who_line = format!("question · {}", get_or(&q, "state", "")) + &get_truthy(&q, "parent").map(|p| format!(" · under {p}")).unwrap_or_default();
            let st_dict = match &st {
                Value::Dict(d) => d.clone(),
                _ => Dict::new(),
            };
            if let Some(b) = briefs(led, &st_dict)?.into_iter().find(|x| x.id.as_deref() == Some(subject)) {
                // the reading rb open's state.json carries, whole, as the screens draw it
                c.sentences.extend(b.summary.clone());
                for eb in &b.experiments {
                    if let Some(scope) = eb.scope.as_ref().filter(|s| !s.is_empty()) {
                        c.facts.push(format!("declared population: {scope}"));
                    }
                }
                let mut changed = b.changed.clone();
                more(&mut changed, 3);
                c.facts.extend(changed);
                let from = b.ruled.len().saturating_sub(2);
                c.facts.extend(b.ruled[from..].iter().cloned());
                c.looks = b.still_open.clone();
                more(&mut c.looks, 3);
            }
        }
    }
    Ok(Some(c))
}

/// What rb found in the repository, opened by its id; None when nothing found goes by it now.
pub fn card(led: &Ledger, request_id: &str) -> Res<Option<Card>> {
    with_screen_trees(led, || led.locked(|| {
        let Some(it) = crate::journal::find(led, request_id)? else { return Ok(None) };
        Ok(Some(record_card(led, &it)?))
    }))
}

fn record_card(led: &Ledger, it: &crate::journal::Found) -> Res<Card> {
    let ask = found_ask(it);
    let st = Value::Dict(led.status(None)?);
    let exp = it.experiment.clone().unwrap_or_else(|| "None".into());
    let bars: Vec<PassBar> = list_of(&st, "findings")
        .iter()
        .filter(|f| f.get("experiment").map(py_str).as_deref() == it.experiment.as_deref())
        .map(pass_bar)
        .collect();
    let mut ms: Vec<(&String, &Value)> = it.metrics.iter().collect();
    ms.sort_by(|a, b| a.0.cmp(b.0));
    let reports = "It reports: ".to_string()
        + &ms.iter().take(8).map(|(k, v)| format!("{k}={}", crate::pyfloat::fmt_num(v, "g"))).collect::<Vec<_>>().join(", ")
        + &if it.metrics.len() > 8 { format!(" and {} more", it.metrics.len() - 8) } else { String::new() };
    let day: String = it.at.chars().take(10).collect();
    Ok(Card {
        request_id: it.id.clone(),
        title: ask.title,
        asked_by: ask.asked_by,
        asked_at: day.clone(),
        lead: format!("In the repository, not in the record. Recording adds it to {exp} as a run, in your name."),
        pass_bars: bars,
        argv: it.argv.clone(),
        yes_label: "record it".into(),
        kind: "record".into(),
        facts: it.facts.clone(),
        dismiss_key: it.key.clone(),
        who_line: format!("found by rb watching the repository · {day}"),
        bars_label: "What these numbers will be read against:".into(),
        reports,
        run_in: crate::sys::slashed(&it.here),
        ..Default::default()
    })
}

// ---------------------------------------------------------------- the view model as JSON (dataclasses.asdict)

fn strs(v: &[String]) -> Value {
    Value::list_of_str(v.to_vec())
}

fn opt_s(v: &Option<String>) -> Value {
    match v {
        Some(s) => Value::Str(s.clone()),
        None => Value::Null,
    }
}

impl Row {
    pub fn to_value(&self) -> Value {
        crate::obj! {
            "metric" => self.metric.clone(), "value" => self.value.clone(), "criterion" => self.criterion.clone(), "margin" => self.margin.clone(),
            "runs" => self.runs.clone(), "standing" => self.standing.clone(), "why" => self.why.clone(), "good" => self.good, "negative" => self.negative
        }
    }
}

impl FindingLine {
    pub fn to_value(&self) -> Value {
        crate::obj! {
            "criterion_id" => self.criterion_id.clone(), "statement" => self.statement.clone(), "numbers" => self.numbers.clone(),
            "row" => self.row.as_ref().map(|r| r.to_value()).unwrap_or(Value::Null), "sentence" => self.sentence.clone(),
            "standing" => self.standing.clone(), "good" => self.good, "rule" => self.rule.clone(), "role" => self.role.clone(),
            "says" => self.says.clone(), "your_words" => self.your_words.clone(), "observed" => self.observed.clone(),
            "observed_short" => self.observed_short.clone(),
            "observed_places" => Value::List(self.observed_places.iter().map(|(id, label)| crate::obj! {"id" => id.clone(), "label" => label.clone()}).collect()),
            "other_code" => self.other_code.clone()
        }
    }
}

impl ExperimentBrief {
    /// Its gate line, with the words given when it was set, as the reader being built for says them. A session's gate
    /// that waits with its design ends `· not confirmed` and gives no command: the design's own line says how it is
    /// confirmed, so it waits in one place on a screen; one that waits apart gives its own.
    pub fn gate_said(&self) -> String {
        gate_said(&self.if_holds, &self.if_not, &self.gate_words, &self.gate_by, &gate_command(&self.id, &self.if_holds, &self.if_not, &self.gate_words, &self.design_confirm))
    }

    fn gate_line_empty(&self) -> bool {
        self.if_holds.is_empty() && self.if_not.is_empty()
    }

    pub fn to_value(&self) -> Value {
        crate::obj! {
            "id" => self.id.clone(), "title" => self.title.clone(), "short_title" => self.short_title.clone(), "findings" => Value::List(self.findings.iter().map(|f| f.to_value()).collect()),
            "scope" => opt_s(&self.scope), "counted" => self.counted, "table" => self.table.clone().map(Value::Dict).unwrap_or(Value::Null),
            "arms" => self.arms.clone(), "varies" => strs(&self.varies), "state" => self.state.clone(), "hypothesis" => self.hypothesis.clone(),
            "if_holds" => self.if_holds.clone(), "if_not" => self.if_not.clone(), "purpose" => self.purpose.clone(), "why" => self.why.clone(),
            "why_part" => why_part(&self.why, &self.why_by).map_or(Value::Null, |(k, t)| Value::list_of_str([k.to_string(), t])),
            "gate_words_said" => if self.gate_line_empty() { String::new() } else { your_words(&self.gate_words, &self.gate_by) },
            "gate_parts" => Value::List(
                gate_parts(&self.if_holds, &self.if_not, &self.gate_words, &self.gate_by, &gate_command(&self.id, &self.if_holds, &self.if_not, &self.gate_words, &self.design_confirm), self.gate_first)
                    .into_iter()
                    .map(|(k, t)| Value::list_of_str([k.to_string(), t]))
                    .collect()
            ),
            "notes" => strs(&self.notes), "note_ids" => strs(&self.note_ids), "relations" => strs(&self.relations), "rerun" => Value::List(self.rerun.iter().cloned().map(Value::Dict).collect()),
            "runs" => self.runs, "folder" => self.folder.clone(), "said" => Value::List(self.said.iter().cloned().map(Value::Dict).collect()),
            "repo" => strs(&self.repo), "repo_brief" => self.repo_brief.clone(),
            "design" => crate::obj! {
                "standing" => self.design_standing.clone(), "pending" => !self.design_confirm.is_empty(), "confirm" => self.design_confirm.clone(),
                "lines" => strs(&self.design_lines), "not_recorded" => strs(&self.not_recorded)
            }
        }
    }
}

impl QuestionBrief {
    pub fn to_value(&self) -> Value {
        crate::obj! {
            "id" => opt_s(&self.id), "text" => self.text.clone(), "status" => self.status.clone(),
            "experiments" => Value::List(self.experiments.iter().map(|e| e.to_value()).collect()), "changed" => strs(&self.changed),
            "still_open" => strs(&self.still_open), "next" => strs(&self.next), "ruled" => strs(&self.ruled), "depth" => self.depth,
            "parent" => opt_s(&self.parent), "rollup" => self.rollup.clone(),
            "inquiry" => self.inquiry.clone(), "inquiry_findings" => Value::List(self.inquiry_findings.iter().map(|f| f.to_value()).collect()), "overview" => self.overview.clone(), "summary" => strs(&self.summary)
        }
    }
}

impl Home {
    pub fn to_value(&self) -> Value {
        crate::obj! {
            "title" => self.title.clone(), "project_id" => self.project_id.clone(), "you" => self.you.clone(), "since" => opt_s(&self.since),
            "found" => Value::List(self.found.iter().map(|a| crate::obj! {
                "request_id" => a.request_id.clone(), "title" => a.title.clone(), "hint" => a.hint.clone(), "asked_by" => a.asked_by.clone(),
                "asked_at" => a.asked_at.clone(), "argv" => strs(&a.argv)
            }).collect()),
            "unplaced" => strs(&self.unplaced), "agent_line" => opt_s(&self.agent_line), "open_line" => opt_s(&self.open_line),
            "uncited" => self.uncited, "uncited_line" => opt_s(&self.uncited_line), "scope" => opt_s(&self.scope),
            "uncited_short" => opt_s(&self.uncited_short), "scope_short" => opt_s(&self.scope_short),
            "tree" => Value::List(self.tree.iter().map(|t| crate::obj! {
                "kind" => t.kind.clone(), "id" => t.id.clone(), "label" => t.label.clone(), "standing" => t.standing.clone(), "depth" => t.depth,
                "muted" => strs(&t.muted)
            }).collect()),
            "quiet" => opt_s(&self.quiet), "waiting" => self.waiting, "calls" => Value::List(self.calls.clone()),
            "counts" => self.counts.clone(), "made" => self.made.clone(), "last_write" => self.last_write.clone(),
            "open_items" => Value::List(self.open_items.clone()), "unfiled" => self.unfiled.clone(),
            "tally" => Value::List(self.tally.iter().map(|(k, n)| crate::list![k.clone(), *n]).collect()),
            "muted" => strs(&self.muted),
            "live" => strs(&self.live), "weak_identity" => opt_s(&self.weak_identity), "began" => opt_s(&self.began),
            "briefs" => Value::List(self.briefs.iter().map(|b| b.to_value()).collect()),
            "runs" => Value::List(self.runs.iter().map(|r| crate::obj! {
                "when" => r.when.clone(), "experiment" => r.experiment.clone(), "results" => r.results.clone(), "by" => r.by.clone(), "note" => r.note.clone(),
                "experiment_id" => r.experiment_id.clone(), "run_id" => r.run_id.clone(), "metrics" => Value::Dict(r.metrics.clone()),
                "per_run" => Value::Dict(r.per_run.clone()), "session" => r.session.clone(), "receipt" => Value::Dict(r.receipt.clone()),
                "produced" => r.produced.clone(), "facts" => strs(&r.facts), "observed_in" => r.observed_in.clone().unwrap_or(Value::Null)
            }).collect()),
            "resources" => Value::List(self.resources.iter().map(|r| crate::obj! {
                "resource_id" => r.resource_id.clone(), "name" => r.name.clone(), "type" => r.r#type.clone(), "availability" => r.availability.clone(),
                "where" => strs(&r.where_), "where_short" => strs(&r.where_short), "how" => r.how.clone(), "used_by" => r.used_by, "identity" => r.identity.clone(), "exact" => r.exact
            }).collect()),
            "notes" => strs(&self.notes),
            "pending" => Value::List(self.pending.iter().map(|p| {
                let mut row = crate::dict! {"id" => p.id.clone(), "kind" => p.kind.clone(), "text" => p.text.clone(), "confirm" => p.confirm.clone()};
                if p.kind == "design" {
                    row.insert("lines".into(), strs(&p.lines));
                }
                Value::Dict(row)
            }).collect())
        }
    }
}

impl Card {
    pub fn to_value(&self) -> Value {
        crate::obj! {
            "request_id" => self.request_id.clone(), "title" => self.title.clone(), "asked_by" => self.asked_by.clone(), "asked_at" => self.asked_at.clone(),
            "lead" => self.lead.clone(),
            "pass_bars" => Value::List(self.pass_bars.iter().map(|p| crate::obj! {
                "criterion_id" => p.criterion_id.clone(), "text" => p.text.clone(), "rule" => p.rule.clone(), "standing" => p.standing.clone(),
                "muted" => strs(&p.muted)
            }).collect()),
            "blockers" => strs(&self.blockers), "looks" => strs(&self.looks), "argv" => strs(&self.argv), "yes_label" => self.yes_label.clone(),
            "kind" => self.kind.clone(), "facts" => strs(&self.facts), "dismiss_key" => self.dismiss_key.clone(), "who_line" => self.who_line.clone(),
            "bars_label" => self.bars_label.clone(), "reports" => self.reports.clone(), "sentences" => strs(&self.sentences),
            "runs" => Value::List(self.runs.iter().map(|(label, id)| crate::obj! {"label" => label.clone(), "id" => id.clone()}).collect())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{quiet_line, quiet_line_away, tree};
    use crate::value::Value;
    use crate::{list, obj};

    /// Nothing waits on you only while rb status lists no call for the user and rb found nothing nobody recorded: the
    /// calls waiting on you are counted, one or many, naming no command or section of rb's, and never as "more" beside
    /// what rb found, which is no call; the rest of what rb status lists is still open in the record. No line names a role.
    #[test]
    fn nothing_waits_on_you_only_while_nothing_does() {
        let rows = |kinds: &[&str]| -> Vec<super::PendingRow> {
            kinds.iter().map(|k| super::PendingRow { id: "x".into(), kind: k.to_string(), ..Default::default() }).collect()
        };
        let says = |calls, found, recorded, met, open| quiet_line(calls, found, recorded, met, open, &[]).unwrap_or_default();
        assert_eq!(says(1, 0, true, 3, 2), "1 call waits on you.");
        assert_eq!(says(3, 0, true, 0, 0), "3 calls wait on you.");
        assert_eq!(says(1, 2, true, 3, 2), "1 call waits on you.");
        assert_eq!(says(2, 1, false, 0, 0), "2 calls wait on you.");
        assert_eq!(says(1, 0, false, 0, 0), "1 call waits on you.");
        assert_eq!(quiet_line(0, 1, true, 3, 2, &[]), None);               // the screen lists what rb found itself
        // a call that waits for a confirmation is listed apart, and the line never says nothing waits while one does
        assert_eq!(quiet_line(0, 0, true, 3, 2, &rows(&["proposed"])).unwrap(), "1 call waits for your confirmation, listed apart; nothing else waits on you.");
        assert_eq!(quiet_line(0, 0, true, 3, 2, &rows(&["proposed", "past"])).unwrap(), "2 calls wait for your confirmation, listed apart; nothing else waits on you.");
        assert_eq!(quiet_line(1, 0, true, 3, 2, &rows(&["proposed", "past"])).unwrap(), "1 call waits on you.");
        // a gate a session set waits as a call does, and is counted as a gate
        assert_eq!(quiet_line(0, 0, true, 3, 2, &rows(&["gate"])).unwrap(), "1 gate waits for your confirmation, listed apart; nothing else waits on you.");
        assert_eq!(
            quiet_line(0, 0, true, 3, 2, &rows(&["proposed", "past", "gate"])).unwrap(),
            "2 calls and 1 gate wait for your confirmation, listed apart; nothing else waits on you."
        );
        // an experiment's design waits as a call does, counted as a design, and in one line for rb status however many;
        // while designs alone wait, the line still counts the record (trial of group E)
        // a page that leaves the machine says the same facts in no one's voice
        assert_eq!(quiet_line_away(1, 0, true, 3, 2, &[]).unwrap(), "1 call is open.");
        assert_eq!(quiet_line_away(2, 0, true, 3, 2, &[]).unwrap(), "2 calls are open.");
        assert_eq!(quiet_line_away(0, 1, true, 3, 2, &[]), None);
        assert_eq!(quiet_line_away(0, 0, false, 0, 0, &[]).unwrap(), "Nothing recorded yet.");
        assert_eq!(quiet_line_away(0, 0, true, 3, 2, &[]).unwrap(), "No call is open. 3 rules are met; 2 things are still open in the record.");
        assert_eq!(quiet_line_away(0, 0, true, 3, 0, &[]).unwrap(), "No call is open.");
        assert_eq!(quiet_line_away(0, 0, true, 3, 0, &rows(&["design", "design"])).unwrap(), "2 designs wait for a confirmation, listed apart.");
        assert_eq!(quiet_line(0, 0, true, 3, 2, &rows(&["design"])).unwrap(), "1 design waits for your confirmation, listed apart. 3 rules are met; 2 things are still open in the record.");
        assert_eq!(quiet_line(0, 0, true, 0, 1, &rows(&["design", "design"])).unwrap(), "2 designs wait for your confirmation, listed apart. 0 rules are met; 1 thing is still open in the record.");
        assert_eq!(quiet_line(0, 0, true, 1, 0, &rows(&["design"])).unwrap(), "1 design waits for your confirmation, listed apart. When rb finds a run nobody recorded, it appears here.");
        assert_eq!(
            quiet_line(0, 0, true, 3, 2, &rows(&["proposed", "design", "design"])).unwrap(),
            "1 call and 2 designs wait for your confirmation, listed apart; nothing else waits on you."
        );
        assert_eq!(super::designs_line(&rows(&["proposed"]), None), None);
        assert_eq!(super::designs_line(&rows(&["design"]), None).unwrap(), "The design of 1 experiment is not confirmed: rb confirm lists it.");
        assert_eq!(super::designs_line(&rows(&["proposed", "design", "design"]), None).unwrap(), "The design of 2 experiments is not confirmed: rb confirm lists them.");
        // a status of one experiment points at the listing of that one, not at rb confirm's whole list
        assert_eq!(super::designs_line(&rows(&["design"]), Some("bsz")).unwrap(), "The design of 1 experiment is not confirmed: rb confirm bsz lists it.");
        assert_eq!(super::open_besides_line(9, ""), "Also open: 9 items that need no decision");
        assert_eq!(super::open_besides_line(1, " · 1 setting unknown"), "Also open: 1 item that needs no decision · 1 setting unknown");
        // a past version offers no command (guarantee 38)
        super::as_past_version(true, || {
            assert_eq!(super::designs_line(&rows(&["design"]), None).unwrap(), "The design of 1 experiment is not confirmed.");
            assert_eq!(super::designs_line(&rows(&["design", "design"]), Some("bsz")).unwrap(), "The design of 2 experiments is not confirmed.");
        });
        assert!(super::designs_line(&rows(&["design"]), None).unwrap().ends_with("rb confirm lists it."));
        assert_eq!(says(0, 0, true, 3, 7), "Nothing waits on you. 3 rules are met; 7 things are still open in the record.");
        assert_eq!(says(0, 0, true, 1, 1), "Nothing waits on you. 1 rule is met; 1 thing is still open in the record.");
        assert_eq!(says(0, 0, true, 2, 0), "Nothing waits on you. When rb finds a run nobody recorded, it appears here.");
        assert!(says(0, 0, false, 0, 0).starts_with("Nothing recorded yet."));
        for calls in 0..3 {
            for open in 0..3 {
                let said = says(calls, 0, true, 1, open).to_lowercase();
                assert!(!["agent", "human", "session"].iter().any(|w| said.contains(w)), "{said}");
            }
        }
    }

    /// DESIGN-PASS-1 target 2: rb status's open items as the screens list them: one plain sentence per rule, by its
    /// statement; a setting by its variant and key, its value and its note; folders and runs counted in the sentence;
    /// no sentence led by its id, and rb's other sentences in the screens' rule words.
    #[test]
    fn open_items_are_plain_sentences() {
        use super::{get_or, IndexMap};
        use crate::obj;
        let f = |stmt: &str, n: i64| obj! {"criterion" => "x", "statement" => stmt, "units" => obj! {"n" => n, "unit" => "seed", "runs" => n}};
        let (a, b) = (f("Per-channel INT8 at 128 calibration images is no more than 1 point below FP32", 1), f("Per-channel INT8 at 512 calibration images is no more than 1 point below FP32", 2));
        let findings: IndexMap<String, &Value> = [("c-pc-128".to_string(), &a), ("c-pc-512".to_string(), &b)].into_iter().collect();
        let variants: IndexMap<String, Vec<String>> = [("e-ptq".to_string(), vec!["fp32".to_string(), "int8".to_string()])].into_iter().collect();
        let said = |i: Value| super::plain_open_items(&i, &findings, &variants).iter().map(|v| (get_or(v, "subject", ""), get_or(v, "what", ""))).collect::<Vec<_>>();
        let pair = |s: &str, w: &str| (s.to_string(), w.to_string());
        assert_eq!(
            said(obj! {"code" => "no_interval", "subject" => "c-pc-128, c-pc-512", "what" => "c-pc-128, c-pc-512: criterion undetermined: a noninferior rule reads the interval: 1 unit: rb gives an interval from 3"}),
            [pair("c-pc-128", "Per-channel INT8 at 128 calibration images is no more than 1 point below FP32: 1 run so far; an interval needs 3"),
             pair("c-pc-512", "Per-channel INT8 at 512 calibration images is no more than 1 point below FP32: 2 runs so far; an interval needs 3")]
        );
        assert_eq!(
            said(obj! {"code" => "too_few_units", "subject" => "c-pc-512", "what" => "c-pc-512: criterion undetermined: 2 units; the criterion needs 3"}),
            [pair("c-pc-512", "Per-channel INT8 at 512 calibration images is no more than 1 point below FP32: 2 runs so far; the rule needs 3")]
        );
        assert_eq!(
            said(obj! {"code" => "provisional", "subject" => "e-ptq/fp32.precision",
                "what" => "e-ptq/fp32.precision: fp32.precision = fp32 is provisional: no check of it is recorded (source note: FP32 model as trained, not checkable)"}),
            [pair("e-ptq/fp32.precision", "fp32 precision in e-ptq: given as fp32, not checked against a source. Its note: FP32 model as trained.")]
        );
        assert_eq!(
            said(obj! {"code" => "provisional", "subject" => "e-ptq/lr", "what" => "e-ptq/lr: lr = 0.0003 is provisional: rb read train.yaml and it does not state it (no line names lr)"}),
            [pair("e-ptq/lr", "lr in e-ptq: given as 0.0003; its source does not state it.")]
        );
        assert_eq!(
            said(obj! {"code" => "unknown", "subject" => "e-ptq/depth", "what" => "e-ptq/depth is unknown and required"}),
            [pair("e-ptq/depth", "depth in e-ptq: no value yet")]
        );
        assert_eq!(
            said(obj! {"code" => "stale", "subject" => "e-ptq/lr", "what" => "e-ptq/lr: configs/criteria.yaml no longer states it"}),
            [pair("e-ptq/lr", "configs/criteria.yaml no longer states it")]
        );
        let folders = obj! {"code" => "unindexed_folder", "subject" => "results/a/, results/b/", "what" => "2 results folders hold ...", "folders" => crate::list!["results/a/", "results/b/"]};
        assert_eq!(said(folders), [pair("results/a/, results/b/", "2 folders: results/a/, results/b/")]);
        assert_eq!(
            said(obj! {"code" => "unread_metric", "subject" => "e2", "what" => "e2: runs report onnx_top1, which no recorded criterion reads"}),
            [pair("e2", "Runs report onnx_top1, which no rule reads")]
        );
    }

    /// PLAIN-WORDS-PASS-1: the screens say rule where rb's rule words say criterion, and keep a rule rb cannot apply
    /// yet apart from one with no results.
    #[test]
    fn what_an_interpretation_rests_on_is_said_in_the_screens_words() {
        let w = |xs: &[&str]| super::holds_said(&xs.iter().map(|x| x.to_string()).collect::<Vec<_>>());
        assert_eq!(w(&["criterion met"]), "Its rule is met.");
        assert_eq!(w(&["criterion met", "criterion met", "criterion met"]), "Its 3 rules are met.");
        assert_eq!(w(&["criterion not met"]), "Its rule is not met.");
        assert_eq!(w(&["criterion undetermined"]), "rb can't tell yet whether its rule is met.");
        assert_eq!(w(&["criterion untested", "criterion untested"]), "Its 2 rules have no results yet.");
        assert_eq!(w(&["estimate meets criterion"]), "Its rule is met on its estimate.");
        assert_eq!(w(&["criterion met", "gate missed"]), "Its 2 rules: 1 met · 1 gate missed.");
        assert_eq!(w(&["criterion undetermined", "criterion untested"]), "Its 2 rules: 1 can't tell yet · 1 not tested.");
    }

    /// A page anyone may be sent says none of the user's words, wherever rb would say them, and everything else rb
    /// builds says them still, after the page, and after a page that failed.
    #[test]
    fn a_page_says_none_of_the_users_words() {
        use super::{for_a_page, with_words, words_shown, your_words};
        const ANA: &str = "human:ana";
        assert_eq!(with_words("accepted 2026-10-10: why", "ship it", ANA), "accepted 2026-10-10: why · your words: \"ship it\"");
        let on_a_page = for_a_page(|| (with_words("accepted 2026-10-10: why", "ship it", ANA), your_words("ship it", "agent:codex"), words_shown()));
        assert_eq!(on_a_page, ("accepted 2026-10-10: why".to_string(), String::new(), false));
        assert!(words_shown() && your_words("ship it", ANA) == "your words: \"ship it\"");
        assert!(std::panic::catch_unwind(|| for_a_page(|| panic!("a page that failed"))).is_err());
        assert!(words_shown());
    }

    /// The user's own screens say the user's words whole, with no tag; a page made from a screen still says none, and
    /// rb's text tags them again after the screen.
    #[test]
    fn a_screen_says_the_users_words_without_their_tag() {
        use super::{for_a_page, for_a_screen, say_words, with_words, your_words};
        const ANA: &str = "human:ana";
        let on_a_screen = for_a_screen(|| {
            (with_words("accepted 2026-10-10: why", "ship it", ANA), your_words("ship it", ANA), say_words("'ship it'", ANA), for_a_page(|| your_words("ship it", ANA)))
        });
        assert_eq!(on_a_screen, ("accepted 2026-10-10: why · \"ship it\"".to_string(), "\"ship it\"".to_string(), "'ship it'".to_string(), String::new()));
        assert_eq!(for_a_page(|| for_a_screen(|| your_words("ship it", ANA))), "");
        assert_eq!(your_words("ship it", ANA), "your words: \"ship it\"");
    }

    /// Words an agent session gave claim the user said them, which rb cannot know: rb's text and the user's screens
    /// say them as the words given with the write, never as the user's, and a page says none.
    #[test]
    fn words_an_agent_session_gave_never_read_as_the_users() {
        use super::{for_a_page, for_a_screen, with_words, your_words};
        for by in ["agent:claude-code", "agent:codex", ""] {
            assert_eq!(with_words("accepted 2026-10-10: why", "ship it", by), "accepted 2026-10-10: why · words (as given): \"ship it\"");
            assert_eq!(for_a_screen(|| your_words("ship it", by)), "words (as given): \"ship it\"");
            assert_eq!(for_a_page(|| your_words("ship it", by)), "");
        }
    }

    /// A catalogue row's count of met rules is muted by every finding it counts that is not green, found as the ledger
    /// finds them to make the count: an experiment's own and a question's explicitly linked criteria. Descendants
    /// do not enter that count, even when a merge leaves questions beneath one another. A finding is not green with a problem
    /// under it or met only on its estimate, its interval across the threshold or no interval at all; a sound gate
    /// beside a muted rule is not muted.
    #[test]
    fn a_count_is_muted_by_every_finding_it_counts() {
        let question = |id: &str, parent: Option<&str>, criteria: &[&str]| obj! {
            "id" => id, "text" => id, "state" => "open", "parent" => parent.map_or(Value::Null, Value::from),
            "inquiry" => obj! {"direct" => Value::List(criteria.iter().map(|c| obj! {"criterion" => *c, "clean" => true}).collect())}
        };
        let experiment = |id: &str, questions: &[&str]| obj! {"id" => id, "title" => id, "state" => "open", "questions" => Value::list_of_str(questions.iter().map(|q| q.to_string()))};
        let met = |exp: &str, role: &str, origin: &str, how: &str| obj! {
            "criterion" => format!("{exp}:{role}"), "experiment" => exp, "rule_outcome" => "meets", "role" => role, "origin" => origin,
            "result" => if how == "point" { "point_only" } else { "measured" },
            "comparison" => if how == "point" { obj! {"estimate" => 0.8} } else { obj! {"estimate" => 0.8, "lo" => 0.75, "hi" => 0.85} },
            "integrity" => if how == "problem" { list![obj! {"code" => "conflict"}] } else { list![] },
            "caveats" => if how == "straddles" { list![obj! {"code" => "straddles"}] } else { list![] }
        };
        let st = obj! {
            "questions" => list![
                question("q1", None, &["e1:primary", "e1:gate"]),
                question("q1a", Some("q1"), &["e2:primary"]),
                question("q2", Some("q3"), &["e3:primary"]),
                question("q3", Some("q2"), &["e4:primary"]),
                question("q4", None, &[]),
                question("q4a", Some("q4"), &[]),
                question("q4b", Some("q4a"), &["e7:primary"])
            ],
            "experiments" => list![
                experiment("e1", &["q1"]),
                experiment("e2", &["q1a"]),
                experiment("e3", &["q2"]),
                experiment("e4", &["q3"]),
                experiment("e5", &[]),
                experiment("e6", &[]),
                experiment("e7", &["q4b"])
            ],
            "findings" => list![
                met("e1", "primary", "own", "sound"),
                met("e1", "gate", "own", "sound"),
                met("e2", "primary", "own", "problem"),
                met("e3", "primary", "own", "sound"),
                met("e4", "primary", "own", "straddles"),
                met("e5", "primary", "cited", "problem"),
                met("e6", "primary", "own", "point"),
                met("e7", "primary", "own", "problem")
            ],
            "resources" => list![obj! {"id" => "r1", "name" => "r1", "availability" => obj! {"state" => "present"}}]
        };
        let muted: Vec<(String, Vec<String>)> = tree(&st).into_iter().map(|r| (r.id, r.muted)).collect();
        let row = |id: &str, words: &[&str]| (id.to_string(), words.iter().map(|w| w.to_string()).collect::<Vec<_>>());
        let met = ["meets"];
        assert_eq!(
            muted,
            [
                row("q1", &[]),
                row("e1", &[]),
                row("q1a", &met),
                row("e2", &met),
                row("q4", &[]),
                row("q4a", &[]),
                row("q4b", &met),
                row("e7", &met),
                row("q2", &[]),
                row("e3", &[]),
                row("q3", &met),
                row("e4", &met),
                row("e5", &["reproduced"]),
                row("e6", &met),
                row("r1", &[]),
            ]
        );
    }
}
