//! The web's card (UX5 2.1 and 4, round 2): C's card model (card.rs), a check, run, setting, commit, file, decision or
//! experiment as its fields in their order, drawn for the inspector. Never a paragraph: a field is its label, its value,
//! then its items, each opening what it names. What a field opens on the CLI (`rb show <x>`) opens here as the card or
//! view the page holds for it: another field card (`#k-<subject>`), a question's view, a file's card at its cited lines,
//! Files at a folder, or the long form, the card the page drew before (`rb show <x> --long`). A git command is shown as
//! the command it is.
//!
//! A page (rb page, rb export) holds every card its Overview and its cards open, drawn here once each; rb open holds
//! none in its `/` and serves each when it is opened (`/o/k-<subject>.html`), so its first answer stays small. The cards
//! a page draws come with the document, never inside it: the document's shape (the contract) does not change.

use super::{at, esc, list, text, Surface};
use crate::errors::Res;
use crate::ledger::Ledger;
use crate::pyjson::py_str;
use crate::value::Value;
use std::cell::RefCell;
use std::collections::BTreeSet;

/// The cards a page draws, by their subjects; on rb open (`lazy`), none drawn, each served when it is opened.
#[derive(Default, Clone)]
pub struct Cards {
    pub list: Vec<Value>,
    pub lazy: bool,
    /// each subject a card was asked for, with the card's own id (`32c91d6` is the commit card `32c91d6f13af`)
    pub asked: Vec<(String, String)>,
    /// on rb open (lazy), the subjects it serves a field card for (`listed`): a link made by `opens` leads to one
    pub open: Option<std::sync::Arc<BTreeSet<String>>>,
}

thread_local! {
    static NOW: RefCell<Option<Cards>> = const { RefCell::new(None) };
}

/// Draws `f` with `cards` as the page's field cards.
pub fn with<T>(cards: Cards, f: impl FnOnce() -> T) -> T {
    let before = NOW.with(|n| n.replace(Some(cards)));
    let out = f();
    NOW.with(|n| *n.borrow_mut() = before);
    out
}

fn now<T>(f: impl FnOnce(&Cards) -> T) -> Option<T> {
    NOW.with(|n| n.borrow().as_ref().map(f))
}

/// Whether the page opens `subject`'s field card: a page that holds it, or rb open, which serves any.
pub fn has(subject: &str) -> bool {
    card_of(subject).is_some()
}

/// Whether a link to `subject`'s field card leads to one: on a page, where the page holds it; on rb open, where rb open
/// serves it (an analysis's record or a commit no run names has none there), so no link a page draws is dead.
pub fn opens(subject: &str) -> bool {
    let hex = |t: &str| t.len() >= 7 && t.chars().all(|c| c.is_ascii_hexdigit());
    now(|c| match (&c.open, c.lazy) {
        (Some(o), true) => o.contains(subject) || (hex(subject) && o.iter().any(|t| hex(t) && (t.starts_with(subject) || subject.starts_with(t.as_str())))),
        _ => card_of(subject).is_some(),
    })
    .unwrap_or(false)
}

/// The anchor of `subject`'s field card: the card's own id's, as the page holds it; on rb open, the subject's.
pub fn to(subject: &str) -> String {
    anchor(&card_of(subject).unwrap_or_else(|| subject.to_string()))
}

fn card_of(subject: &str) -> Option<String> {
    now(|c| {
        if c.lazy {
            return Some(subject.to_string());
        }
        c.asked.iter().find(|(s, _)| s == subject).map(|(_, id)| id.clone())
    })
    .flatten()
}

/// The subject a card was drawn for: its id, as `rb show` takes it.
fn subject_of(card: &Value) -> String {
    text(card, "id").to_string()
}

/// A card's anchor: `k-` and its subject, each character a hash or an address would read otherwise written `~` and
/// its byte in hex, so `configs/data.yaml#mean` is `k-configs/data.yaml~23mean`.
pub fn anchor(subject: &str) -> String {
    let mut out = String::from("k-");
    for b in subject.bytes() {
        if b.is_ascii_alphanumeric() || b"._/:@=+,-".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("~{b:02x}"));
        }
    }
    out
}

/// The subject an anchor names: `anchor`'s inverse, None for an anchor no field card has.
pub fn subject(anchor: &str) -> Option<String> {
    let rest = anchor.strip_prefix("k-")?;
    let b = rest.as_bytes();
    let mut out: Vec<u8> = Vec::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'~' && i + 3 <= b.len() {
            let hex = std::str::from_utf8(b.get(i + 1..i + 3)?).ok()?;
            out.push(u8::from_str_radix(hex, 16).ok()?);
            i += 3;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok().filter(|s| !s.is_empty())
}

/// The subjects a page opens: every check, run (but those past the receipts' cap, which keep their short card),
/// experiment and decision, then what their cards open of the kinds a field card is drawn for (a setting, a commit, a
/// file's line by its key or at a commit), and so on, each once.
pub fn of(led: &Ledger, doc: &Value, surface: Surface) -> Res<Cards> {
    if surface == Surface::Open {
        return Ok(Cards { list: Vec::new(), lazy: true, asked: Vec::new(), open: Some(listed(led)?) });
    }
    // read as a page reads, for anyone it may be sent to: a session's words as given, the user's left out
    crate::view::for_a_page(|| page_cards(led, doc))
}

fn page_cards(led: &Ledger, doc: &Value) -> Res<Cards> {
    let mut todo: Vec<String> = Vec::new();
    for (cid, _) in dict(doc, "findings") {
        todo.push(cid.to_string());
    }
    let short: BTreeSet<&str> = super::nodes::past_the_cap(doc).into_iter().collect();
    for (rid, _) in dict(doc, "runs") {
        if !short.contains(rid) {
            todo.push(rid.to_string());
        }
    }
    for x in list(doc, "experiments") {
        todo.push(text(x, "id").to_string());
    }
    for d in led.all::<crate::record::Decision>()?.iter().filter(|d| d.retracted.is_none()) {
        todo.push(d.id.clone());
    }
    let escapes = led.shows_escapes()?;
    let trees = crate::view::page_trees_of(led);
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut out: Vec<Value> = Vec::new();
    let mut asked: Vec<(String, String)> = Vec::new();
    let mut i = 0;
    while i < todo.len() {
        let s = todo[i].clone();
        i += 1;
        if walled(&s) || !seen.insert(s.clone()) {
            continue;
        }
        let Some(mut v) = web_card(led, &s, escapes)? else { continue };
        // a page names no worktree's path: a file inside a linked worktree nested in the checkout by its path there
        if !trees.is_empty() {
            unplace(&mut v, &trees, "");
        }
        // a page, which anyone may be sent, leaves the user's words out, as its other cards do
        if let Value::Dict(d) = &mut v {
            if let Some(Value::List(fs)) = d.get_mut("fields") {
                // nor names a viewer's checkout, which a page has none of
                fs.retain(|f| !matches!(text(f, "key"), "words" | "your_checkout"));
            }
        }
        for c in list(&v, "cites") {
            let (kind, target) = (text(c, "kind"), text(c, "target"));
            let wanted = match kind {
                "setting" | "commit" => true,
                "record" => !seen.contains(target),
                "file" => target.contains('#') || target.contains('@'),
                _ => false,
            };
            if wanted && !seen.contains(target) {
                todo.push(target.to_string());
            }
        }
        // a record of a kind a field card is not drawn for (a question, a resource, a note) opens its own view or card
        if matches!(text(&v, "kind"), "check" | "run" | "setting" | "commit" | "file" | "decision" | "experiment") {
            let id = subject_of(&v);
            asked.push((s.clone(), id.clone()));
            if !out.iter().any(|k| subject_of(k) == id) {
                out.push(v);
            }
        }
    }
    // within the page's size: the field cards of checks, decisions, experiments, settings, commits and lines first, then
    // runs, the newest first, while they fit (BUDGET); a run with no field card opens the card the page drew for it before
    let mut cards = Cards { list: out, lazy: false, asked, open: None };
    let sizes: Vec<usize> = with(cards.clone(), || cards.list.iter().map(|k| draw(k, doc).len()).collect());
    let mut used: usize = cards.list.iter().zip(&sizes).filter(|(k, _)| text(k, "kind") != "run").map(|(_, n)| n).sum();
    let mut runs: Vec<(String, usize)> = cards.list.iter().zip(&sizes).filter(|(k, _)| text(k, "kind") == "run").map(|(k, n)| (subject_of(k), *n)).collect();
    let at = |rid: &str| at(doc, &format!("runs.{rid}.at")).map(py_str).unwrap_or_default();
    runs.sort_by(|a, b| at(&b.0).cmp(&at(&a.0)));
    let mut keep: BTreeSet<String> = BTreeSet::new();
    for (rid, n) in runs {
        if used + n > BUDGET {
            break;
        }
        used += n;
        keep.insert(rid);
    }
    cards.list.retain(|k| text(k, "kind") != "run" || keep.contains(&subject_of(k)));
    let ids: BTreeSet<String> = cards.list.iter().map(subject_of).collect();
    cards.asked.retain(|(_, id)| ids.contains(id));
    Ok(cards)
}

/// The bytes a page gives its field cards, at most (the page's size budget, SMALL_PAGE and SCALE_RAW in the tests).
pub const BUDGET: usize = 120_000;

/// `v` with each path inside one of `trees` (a linked worktree nested in the checkout) said by its path inside it, then
/// "(in another worktree)"; a command that names one opens nothing on a page.
fn unplace(v: &mut Value, trees: &[String], key: &str) {
    match v {
        Value::Dict(d) => {
            for (k, x) in d.iter_mut() {
                unplace(x, trees, k);
            }
        }
        Value::List(xs) => {
            for x in xs.iter_mut() {
                unplace(x, trees, key);
            }
        }
        Value::Str(s) if trees.iter().any(|t| s.contains(&format!("{t}/"))) => {
            if key == "opens" {
                *v = Value::Null;
                return;
            }
            let mut out = String::new();
            for (i, word) in s.split(' ').enumerate() {
                if i > 0 {
                    out.push(' ');
                }
                match trees.iter().find_map(|t| word.strip_prefix(&format!("{t}/"))) {
                    Some(rest) => out.push_str(&format!("{rest}{}", crate::view::IN_ANOTHER_WORKTREE)),
                    None => out.push_str(word),
                }
            }
            *s = out;
        }
        _ => {}
    }
}

fn dict<'a>(v: &'a Value, key: &str) -> Vec<(&'a str, &'a Value)> {
    at(v, key).and_then(Value::as_dict).map_or_else(Vec::new, |d| d.iter().map(|(k, v)| (k.as_str(), v)).collect())
}

/// Every card the page holds, drawn: none on rb open, which serves each on request.
pub fn all(doc: &Value) -> String {
    now(|c| c.list.iter().map(|k| draw(k, doc)).collect::<String>()).unwrap_or_default()
}

/// What a field card on rb open links by: the ids of the records rb open's page draws a card or a view for (checks,
/// runs, resources, analyses, questions, experiments), in the document's shape, with nothing else. A file opens its
/// field card there.
pub fn index(led: &Ledger) -> Res<Value> {
    use crate::value::Dict;
    let ids = |xs: Vec<String>| Value::Dict(xs.into_iter().map(|i| (i, Value::Null)).collect::<Dict>());
    let rows = |xs: Vec<String>| Value::List(xs.into_iter().map(|i| crate::obj! {"id" => i}).collect());
    let live = |r: &Option<crate::record::Retraction>| r.is_none();
    Ok(crate::obj! {
        "findings" => ids(led.all::<crate::record::Criterion>()?.iter().filter(|c| live(&c.retracted)).map(|c| c.id.clone()).collect()),
        "runs" => ids(led.all::<crate::record::Run>()?.iter().map(|r| r.id.clone()).collect()),
        "resources" => ids(led.all::<crate::record::Resource>()?.iter().map(|r| r.id.clone()).collect()),
        "analyses" => ids(led.all::<crate::record::Analysis>()?.iter().map(|a| a.id.clone()).collect()),
        "questions" => rows(led.all::<crate::record::Question>()?.iter().filter(|q| live(&q.retracted)).map(|q| q.id.clone()).collect()),
        "experiments" => rows(led.all::<crate::record::Experiment>()?.iter().filter(|x| live(&x.retracted)).map(|x| x.id.clone()).collect()),
    })
}

/// `subject`'s card as the web draws it (a page and rb open alike): C's card model, each character a screen would hide
/// drawn as its escape where the record shows them so, and a file git ignores said by its path and that git ignores it,
/// with none of its lines (it may hold what no one meant to keep: a token, a writer's name). The terminal's `rb show`
/// is the user's own screen, and draws them.
fn web_card(led: &Ledger, subject: &str, escapes: bool) -> Res<Option<Value>> {
    let Some(mut card) = crate::card::of(led, subject)? else { return Ok(None) };
    if matches!(card.kind, "file" | "folder") {
        // as the card read it (a path rb show reads from where the command runs, too), with every link followed
        let Some(c) = crate::card::parse(&card.id) else { return Ok(None) };
        // a folder opens Files on the web; nor is a file drawn that is, or leads to, git's or rb's own, or outside
        if card.kind == "folder" || walled_file(led, &c.path) {
            return Ok(None);
        }
        if ignored(led, &c.path) {
            // its path and that git ignores it: none of its lines, nor whether a key or a line is in it
            card.fields.retain(|f| !matches!(f.key, "lines" | "key"));
            if let Some(head) = card.fields.first_mut().filter(|_| c.commit.is_empty()) {
                head.value = "\u{2298} ignored by git".into();
            }
            // the mark that points at a cited line goes with the lines; ⊘ is said
            for f in card.fields.iter_mut().filter(|f| f.key == "marks") {
                let mut marks: Vec<String> = f.value.split("  \u{00b7}  ").filter(|m| !m.starts_with('\u{25b6}') && !m.starts_with('\u{2298}')).map(str::to_string).collect();
                marks.push("\u{2298} ignored by git".into());
                f.value = marks.join("  \u{00b7}  ");
            }
        }
    }
    let mut v = card.to_value(led);
    if escapes {
        crate::ledger::design::shown_in(&mut v);
    }
    Ok(Some(v))
}

/// Where `path` leads in the checkout, every link followed, from the checkout's top; None where it leads outside it.
/// A path no file is at now (one at a commit) is itself.
fn real_path(led: &Ledger, path: &str) -> Option<String> {
    let top = std::fs::canonicalize(&led.root).ok()?;
    match std::fs::canonicalize(led.root.join(path)) {
        Ok(real) => real.strip_prefix(&top).ok().map(|r| r.to_string_lossy().replace('\\', "/")),
        Err(_) => Some(path.to_string()),
    }
}

/// Whether a file subject's path, or where it leads (a link to `.git/config`, a folder linked to `.git`), is git's or
/// rb's own, or outside the checkout.
fn walled_file(led: &Ledger, path: &str) -> bool {
    walled(path) || real_path(led, path).map_or(true, |r| walled(&r))
}

/// Whether git ignores a file subject's path, or where it leads: at a commit too, since what git ignores now may be a
/// file someone committed and then kept out (a token).
fn ignored(led: &Ledger, path: &str) -> bool {
    let check = |p: &str| crate::sources::git(&led.root, &["check-ignore", "-q", "--", p]).is_some();
    check(path) || real_path(led, path).map_or(false, |r| r != path && check(&r))
}

/// Whether a subject names something no web card shows, whatever opens it: git's own folder (its config and logs name
/// whoever committed) and rb's (its log names who made each write), at any depth.
fn walled(subject: &str) -> bool {
    let path = crate::card::parse(subject).map(|c| c.path).unwrap_or_else(|| subject.to_string());
    path.trim_start_matches("./").split('/').any(|part| part == ".git" || part == ".rb")
}

/// Whether rb open's page lists `subject` as a field card: a check, run, experiment or decision, a setting of an
/// experiment, a file the Files tab lists (git tracks it, or lists it untracked and not ignored, or the record names
/// it) at its lines, a key or a commit, or else whatever the page's cards reach (`listed`). Never git's folder or rb's.
pub fn opened_here(led: &Ledger, subject: &str) -> Res<bool> {
    if walled(subject) {
        return Ok(false);
    }
    if let Some(k) = led.kind_of(subject)? {
        return Ok(matches!(k, "criterion" | "run" | "experiment" | "decision"));
    }
    let (head, name) = crate::ledger::parse_address(subject);
    if name.is_some() && led.kind_of(&head)? == Some("experiment") && led.resolve_setting(subject).is_ok() {
        return Ok(true);
    }
    if let Some(c) = crate::card::parse(subject).filter(|c| !c.folder) {
        // a folder opens Files on the web, never a card; and a link leads where it leads
        if walled_file(led, &c.path) || (c.commit.is_empty() && led.root.join(&c.path).is_dir()) {
            return Ok(false);
        }
        let files = crate::files::of(led, None, true)?;
        if files.as_list().map_or(false, |fs| fs.iter().any(|f| text(f, "path") == c.path)) {
            return Ok(true);
        }
    }
    // a commit a run's code is named by (its command's start, else the checkout it was recorded in)
    if (7..=40).contains(&subject.len()) && subject.chars().all(|c| c.is_ascii_hexdigit()) {
        let names = |v: Option<&crate::value::Dict>| v.and_then(|v| v.get("commit")).filter(|c| c.truthy()).map(py_str).map_or(false, |c| c.len() >= 7 && (c.starts_with(subject) || subject.starts_with(&c)));
        if led.all::<crate::record::Run>()?.iter().any(|r| names(Some(&r.receipt.produced)) || names(r.receipt.attached.as_ref())) {
            return Ok(true);
        }
    }
    Ok(listed(led)?.contains(subject))
}

/// The subjects rb open's page lists as field cards: every check, run, experiment and decision, as a page draws them,
/// then each record, setting, commit and file their cards cite, and so on, as the page's links reach them, each once.
/// rb open serves a field card for these alone: never one for a path the page does not name (git's folder, a file git
/// ignores that nothing cites, any file of the checkout typed into the address).
pub fn listed(led: &Ledger) -> Res<std::sync::Arc<BTreeSet<String>>> {
    // kept for the checkout, the record and the commit it was worked out at: a card is drawn for each subject in it, so a
    // file's lines read as they are now, and a file git ignores since draws none of them
    let key = format!(
        "{}\n{}\n{}",
        led.root.display(),
        led.state_digest()?,
        crate::sources::git(&led.root, &["rev-parse", "-q", "--verify", "HEAD"]).unwrap_or_default().trim()
    );
    let mut kept = LISTED.lock().unwrap_or_else(|e| e.into_inner());
    if let Some((_, got)) = kept.iter().find(|(k, _)| *k == key) {
        return Ok(got.clone());
    }
    let got = std::sync::Arc::new(reach(led)?);
    kept.insert(0, (key, got.clone()));
    // rb open shows a few places and versions at most: the latest of each
    kept.truncate(8);
    Ok(got)
}

static LISTED: std::sync::Mutex<Vec<(String, std::sync::Arc<BTreeSet<String>>)>> = std::sync::Mutex::new(Vec::new());

fn reach(led: &Ledger) -> Res<BTreeSet<String>> {
    let mut todo: Vec<String> = Vec::new();
    todo.extend(led.all::<crate::record::Criterion>()?.iter().map(|c| c.id.clone()));
    todo.extend(led.all::<crate::record::Run>()?.iter().map(|r| r.id.clone()));
    todo.extend(led.all::<crate::record::Experiment>()?.iter().map(|x| x.id.clone()));
    todo.extend(led.all::<crate::record::Decision>()?.iter().map(|d| d.id.clone()));
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut out: BTreeSet<String> = BTreeSet::new();
    while let Some(s) = todo.pop() {
        if walled(&s) || !seen.insert(s.clone()) {
            continue;
        }
        let Some(v) = web_card(led, &s, false)? else { continue };
        out.insert(s);
        for c in list(&v, "cites") {
            let (kind, target) = (text(c, "kind"), text(c, "target"));
            // a folder opens Files, and a long form or a command of git's no field card
            if matches!(kind, "record" | "setting" | "commit" | "file") && !seen.contains(target) {
                todo.push(target.to_string());
            }
        }
    }
    Ok(out)
}

/// rb open's answer for one card: `anchor`'s, drawn against rb open's document, or None; None too for a subject its
/// page does not list (`listed`).
pub fn one(led: &Ledger, doc: &Value, anchor: &str) -> Res<Option<String>> {
    let Some(subject) = subject(anchor) else { return Ok(None) };
    if !opened_here(led, &subject)? {
        return Ok(None);
    }
    let Some(v) = web_card(led, &subject, led.shows_escapes()?)? else { return Ok(None) };
    let html = with(Cards { list: Vec::new(), lazy: true, asked: Vec::new(), open: Some(listed(led)?) }, || draw(&v, doc));
    // drawn at the anchor it was asked for (`k-logs/agent.log:719` is the card of `logs/agent.log`, at that line), which
    // the trail that waits for it names
    let own = format!(" id=\"{}\"", esc(&self::anchor(&subject_of(&v))));
    Ok(Some(html.replacen(&own, &format!(" id=\"{}\"", esc(anchor)), 1)))
}

/// Where a command a field opens leads on the page: an anchor and the attributes its link carries, or None where the
/// page holds nothing for it (a git command, a file the page does not name).
pub fn href(cmd: &str, doc: &Value) -> Option<(String, String)> {
    let unq = |t: &str| t.trim_matches('\'').replace("'\\''", "'");
    if let Some(rest) = cmd.strip_prefix("rb files") {
        let t = unq(rest.trim());
        return Some(("files".into(), format!(" data-folder=\"{}\"", esc(&t))));
    }
    let rest = cmd.strip_prefix("rb show ")?;
    if let Some(t) = rest.strip_suffix(" --long") {
        return long(&unq(t), doc).map(|a| (a, String::new()));
    }
    let t = unq(rest);
    let ids = |key: &str| dict(doc, key).iter().any(|(k, _)| *k == t);
    let field = |old: Option<String>| if has(&t) { Some(to(&t)) } else { old };
    if ids("findings") {
        return field(Some(super::nodes::finding_anchor(&t))).map(|a| (a, String::new()));
    }
    if ids("runs") {
        return field(Some(super::nodes::run_anchor(&t))).map(|a| (a, String::new()));
    }
    if ids("resources") {
        return Some((super::nodes::resource_anchor(&t), String::new()));
    }
    if ids("analyses") {
        return Some((super::nodes::analysis_anchor(&t), String::new()));
    }
    if list(doc, "questions").iter().any(|q| text(q, "id") == t) {
        return Some((format!("q-{t}"), String::new()));
    }
    if list(doc, "experiments").iter().any(|x| text(x, "id") == t) {
        return field(Some(format!("x-{t}"))).map(|a| (a, String::new()));
    }
    if let Some((x, _)) = t.split_once('/') {
        if list(doc, "experiments").iter().any(|e| text(e, "id") == x) {
            return field(Some(format!("x-{x}"))).map(|a| (a, String::new()));
        }
    }
    if t.ends_with('/') {
        return Some(("files".into(), format!(" data-folder=\"{}\"", esc(&t))));
    }
    if (7..=40).contains(&t.len()) && t.chars().all(|c| c.is_ascii_hexdigit()) {
        return field(None).map(|a| (a, String::new()));
    }
    if let Some(c) = crate::card::parse(&t) {
        let named = list(doc, "files").iter().any(|f| text(f, "path") == c.path);
        let file = named.then(|| super::nodes::file_anchor(&c.path));
        if !c.key.is_empty() || !c.commit.is_empty() {
            return field(file).map(|a| (a, String::new()));
        }
        // a cited line: the Source view that quotes it, else the file's card
        if c.line > 0 && dict(doc, "citations").iter().any(|(k, _)| *k == t) {
            return Some((super::nodes::source_anchor(&t), String::new()));
        }
        return field(file).map(|a| (a, String::new()));
    }
    // a decision, or any other record a field card is drawn for
    field(None).map(|a| (a, String::new()))
}

/// The long form of `t` on the page: the card or view the page drew for it before field cards.
fn long(t: &str, doc: &Value) -> Option<String> {
    let ids = |key: &str| dict(doc, key).iter().any(|(k, _)| *k == t);
    if ids("findings") {
        return Some(super::nodes::finding_anchor(t));
    }
    if ids("runs") {
        return Some(super::nodes::run_anchor(t));
    }
    let x = t.split('/').next().unwrap_or(t);
    if list(doc, "experiments").iter().any(|e| text(e, "id") == x) {
        return Some(format!("x-{x}"));
    }
    if list(doc, "files").iter().any(|f| text(f, "path") == t) {
        return Some(super::nodes::file_anchor(t));
    }
    // a decision's long form is its line in History
    Some("history".into())
}

/// A link to what `cmd` opens, around `inner` (escaped already); the text alone where the page holds nothing for it.
fn link(cmd: &str, inner: &str, doc: &Value, title: &str) -> String {
    let title = if title.is_empty() { String::new() } else { format!(" title=\"{}\"", esc(title)) };
    match href(cmd, doc) {
        Some((a, attrs)) => format!("<a href=\"#{}\"{attrs}{title}>{inner}</a>", esc(&a)),
        None if cmd.starts_with("git ") => format!("<code{title}>{inner}</code>"),
        None => format!("<span{title}>{inner}</span>"),
    }
}

fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    text(v, k)
}

/// A field's value with the state's square coloured: `■ passed` reads as the strip draws it.
fn marked(value: &str) -> String {
    for (state, mark, _) in crate::overview::STATES {
        if let Some(rest) = value.strip_prefix(mark) {
            return format!("<b class=\"sq s-{state}\">{mark}</b>{}", esc(rest));
        }
    }
    esc(value)
}

/// One card, drawn: its head (its kind's label, its id and its first field's value and items), the author's words
/// under it, then each field.
pub fn draw(card: &Value, doc: &Value) -> String {
    let kind = s(card, "kind");
    let id = subject_of(card);
    let fields = list(card, "fields");
    // the long form, where each value this card gives is worked out: what a value with nothing of its own to open opens
    // (closed form: every value opens its source)
    let long_to = fields.iter().find(|f| s(f, "key") == "open").and_then(|f| at(f, "opens").map(py_str)).and_then(|o| href(&o, doc)).map(|(a, _)| a);
    let mut h = String::new();
    let mut body = String::new();
    for (i, f) in fields.iter().enumerate() {
        let key = s(f, "key");
        let label = s(f, "label");
        let items = items_html(f, doc, None);
        if i == 0 {
            h = format!("<h2 class=\"tt fh\"><span class=\"fl\">{}</span> <span class=\"mono\">{}</span> {}{items}</h2>", esc(label), esc(&id), marked(s(f, "value")));
            continue;
        }
        if label.is_empty() {
            // the author's words, as written (a check's statement, an experiment's title)
            body += &format!("<p class=\"au fs\">{}</p>", esc(s(f, "value")));
            continue;
        }
        if key == "open" {
            // the long form, one step away; the CLI's way to open an id is the CLI's
            let open = at(f, "opens").map(py_str).unwrap_or_default();
            if let Some((a, attrs)) = (!open.is_empty()).then(|| href(&open, doc)).flatten() {
                body += &format!("<dt><a href=\"#{1}\"{attrs}>{0}</a></dt><dd><a href=\"#{1}\"{attrs}>{2}</a></dd>", esc(label), esc(&a), super::strings::LONG_FORM);
            }
            continue;
        }
        let value = match at(f, "opens").map(py_str).filter(|o| !o.is_empty()) {
            Some(o) => link(&o, &marked(s(f, "value")), doc, ""),
            None if s(f, "value").is_empty() => String::new(),
            None => marked(s(f, "value")),
        };
        // a command the field gives is drawn as a command: its box, which a reader's view holds back (script.js), and
        // none at a past version, which offers no command (guarantee 38); from another place, with the cd to it first
        let cmd = s(f, "command");
        // a confirmation is offered as a prompt for their agent, never a command to type (owner, msg 47); the
        // confirming itself stays theirs, in their own terminal (G5)
        let value = if cmd.is_empty() || crate::view::at_past_version() {
            value
        } else if cmd.starts_with("rb confirm") || (cmd.starts_with("cd ") && cmd.contains(" && rb confirm")) {
            let on = if id.is_empty() { s(f, "value").to_string() } else { id.clone() };
            format!("{value}{}", super::views::prompt_button(&super::views::placed(&format!("{}{on}{}", super::strings::CALL_PROMPT_HEAD, super::strings::CALL_PROMPT_TAIL), &cmd)))
        } else {
            format!("{value}<div class=\"cmdbox\" data-copybtn><pre class=\"cmd\">{}</pre></div>", esc(&crate::view::from_here(cmd)))
        };
        let fig = at(f, "figure").map(figure).unwrap_or_default();
        let cls = if key == "marks" { " class=\"fmk\"" } else { "" };
        let lines = if key == "lines" { lines_html(f) } else { String::new() };
        let items = if key == "lines" { String::new() } else { items };
        body += &format!("<dt{cls}>{}</dt><dd{cls}>{value}{items}{fig}{lines}</dd>", esc(label));
    }
    // the card is one row that opens the long form, where each value it gives is worked out (closed form); a value
    // with a card of its own opens that card first
    let go = long_to.as_deref().map(|a| format!(" data-go=\"{}\"", esc(a))).unwrap_or_default();
    format!(
        "<div class=\"card fc\" id=\"{}\" data-kind=\"{kind}\" data-crumb=\"{}\"><div class=\"fcb\"{go}>{h}<dl class=\"fd\">{body}</dl></div></div>",
        esc(&anchor(&id)),
        esc(&id)
    )
}

/// A field's items, each opening its own; the CLI's own hint (`· rb show <an id or path above> opens it`) left out.
fn items_html(f: &Value, doc: &Value, long_to: Option<&str>) -> String {
    let mut out = String::new();
    for it in list(f, "items") {
        let t = s(it, "text");
        let o = at(it, "opens").map(py_str).unwrap_or_default();
        if o.is_empty() && t.starts_with("\u{b7} rb show") {
            continue;
        }
        let said = s(it, "said");
        let title = if said == t { "" } else { said };
        let inner = marked(t);
        out += " ";
        out += &if o.is_empty() {
            let fi = if title.is_empty() { format!("<span class=\"fi\">{inner}</span>") } else { format!("<span class=\"fi\" title=\"{}\">{inner}</span>", esc(title)) };
            match long_to {
                Some(a) => format!("<a href=\"#{}\">{fi}</a>", esc(a)),
                None => fi,
            }
        } else if href(&o, doc).is_none() && long_to.is_some() {
            // what the page holds no card for opens the long form, where it is said in full
            format!("<a href=\"#{}\"{}>{inner}</a>", esc(long_to.unwrap_or("")), if title.is_empty() { String::new() } else { format!(" title=\"{}\"", esc(title)) })
        } else {
            link(&o, &inner, doc, title)
        };
    }
    out
}

/// A file card's lines, the one it cites marked.
fn lines_html(f: &Value) -> String {
    let rows: Vec<String> = list(f, "items")
        .iter()
        .map(|it| {
            let t = s(it, "text");
            match t.strip_prefix('\u{25b6}') {
                Some(rest) => format!("<b>\u{25b6}{}</b>", esc(rest)),
                None => esc(t),
            }
        })
        .collect();
    if rows.is_empty() {
        return String::new();
    }
    format!("<pre class=\"flines\">{}</pre>", rows.join("\n"))
}

/// A check's result as one chart row's track: its zone, edge, 0, range and dot, with the check's number on the axis.
fn figure(fig: &Value) -> String {
    let mut row = fig.clone();
    let edge = crate::overview::edge_text(&row);
    if let Value::Dict(d) = &mut row {
        d.insert("edge_text".into(), edge.into());
    }
    let (lo, hi) = crate::overview::domain(&[&row]);
    format!("<span class=\"ff\">{}</span>", track(&row, lo, hi, (true, true)))
}

fn n(v: &Value, k: &str) -> Option<f64> {
    v.get(k).and_then(Value::as_f64).filter(|x| x.is_finite())
}

/// A place on a panel's axis, as a share of the track's width: `left:37.5%`.
fn pct(v: f64, lo: f64, hi: f64) -> f64 {
    ((v - lo) / (hi - lo) * 100.0).clamp(0.0, 100.0)
}

/// The check's number as its axis label says it: the number at the zone's solid edge (`−1`, `5`), or a band by its two
/// ends (`49.55 to 49.65`), as the card's band formatter says them (`overview::edge_text`).
fn edge_number(r: &Value) -> String {
    let t = text(r, "edge_text");
    ["from ", "up to ", "above ", "below "].iter().find_map(|w| t.strip_prefix(w)).unwrap_or(t).to_string()
}

/// One row's track: the zone its check accepts with its solid edge, 0 where a difference is read (dashed), the range
/// and the result, and under it, where `labels` says so, the axis: the check's number at its edge and 0 at the dashed
/// line. Places are shares of the track, so the track is drawn at the width it is shown at, a phone's too.
pub(crate) fn track(r: &Value, lo: f64, hi: f64, labels: (bool, bool)) -> String {
    let x = |v: f64| pct(v, lo, hi);
    let mut h = String::new();
    let mut edge_at: Option<f64> = None;
    if let Some(t) = n(r, "threshold") {
        let tol = n(r, "tol").unwrap_or(0.0);
        let (a, b) = match text(r, "side") {
            "ge" | "gt" => (x(t), 100.0),
            "le" | "lt" => (0.0, x(t)),
            _ => (x(t - tol), x(t + tol)),
        };
        h += &format!("<i class=\"z\" style=\"left:{a:.1}%;width:{:.1}%\"></i>", (b - a).max(0.6));
        h += &format!("<i class=\"e\" style=\"left:{:.1}%\"></i>", x(t));
        edge_at = Some(x(t));
    }
    let zero = r.get("difference").and_then(Value::as_bool).unwrap_or(false) && lo < 0.0 && 0.0 < hi;
    if zero {
        h += &format!("<i class=\"o\" style=\"left:{:.1}%\"></i>", x(0.0));
    }
    let est = n(r, "est").unwrap_or(0.0);
    match (n(r, "lo"), n(r, "hi")) {
        (Some(a), Some(b)) if b > a => {
            h += &format!("<i class=\"b\" style=\"left:{:.1}%;width:{:.1}%\"></i><i class=\"d\" style=\"left:{:.1}%\"></i>", x(a), x(b) - x(a), x(est));
        }
        _ => h += &format!("<i class=\"d h\" style=\"left:{:.1}%\"></i>", x(est)),
    }
    // the axis: the check's number at its edge, and 0 at the dashed line where it is not the same place
    // a label near an end of the track reads inward, so it stays inside its box
    let side = |p: f64| if p < 12.0 { " class=\"l\"" } else if p > 88.0 { " class=\"r\"" } else { "" };
    let mut ax = String::new();
    if labels.0 {
        if let Some(e) = edge_at {
            ax += &format!("<span{} style=\"left:{e:.1}%\">{}</span>", side(e), esc(&edge_number(r)));
        }
    }
    if labels.1 && zero && edge_at.map_or(true, |e| (e - x(0.0)).abs() > 9.0) {
        ax += &format!("<span{} style=\"left:{:.1}%\">0</span>", side(x(0.0)), x(0.0));
    }
    let ax = if ax.is_empty() { String::new() } else { format!("<span class=\"oax\">{ax}</span>") };
    format!("<span class=\"ot\">{h}</span>{ax}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_anchor_reads_back_as_its_subject() {
        for s in ["c-pt-512", "e-ptq/calib_images", "configs/data.yaml#normalize.mean", "README.md:3-9", "a b&c~d", "2982f0fae9b4", "x.yaml@32c91d6"] {
            let a = anchor(s);
            assert!(!a.contains('#') && !a.contains('&') && !a.contains(' ') && !a.contains('%'), "{a}");
            assert_eq!(subject(&a).as_deref(), Some(s));
        }
        assert_eq!(anchor("configs/data.yaml#mean"), "k-configs/data.yaml~23mean");
        assert_eq!(subject("c-1"), None);
    }
}
