//! `rb open`: the catalogue, live, served from the user's own machine (127.0.0.1) and nowhere else. It only reads:
//! every POST is refused with 405, and a request that names a host other than 127.0.0.1, localhost or [::1] with 403.
//! The page is drawn by `shell/views.rs`. It shows the record it started on, live, or another place of the clone
//! or a past version of a record, chosen on the page by an id or a commit the server listed (places.rs); those are read
//! inside a preview (dry.rs), so nothing is written there, and never scanned.
//! `/` is the shell's page with the live module (shell/assets/live.js),
//! `/digest.json` answers the key of what a page shows, and `/o/<id>.html` one card.

use crate::errors::{RBError, Res};
use crate::ledger::Ledger;
use crate::places::{self, Chosen, Place, Version};
use crate::pyjson;
use crate::value::{Dict, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const DEFAULT_PORT: i64 = 8787;
const SCAN_EVERY: Duration = Duration::from_secs(10);
/// The shell's live module, which rb open alone adds to the page.
pub const LIVE_JS: &str = include_str!("shell/assets/live.js");

static LAST_SCAN: Mutex<Option<HashMap<String, Instant>>> = Mutex::new(None);
/// How long the places and versions listed hold before git is asked again.
const PLACES_EVERY: Duration = Duration::from_secs(10);
#[allow(clippy::type_complexity)]
static PLACES: Mutex<Option<(PathBuf, Instant, Vec<Stamp>, Vec<Place>)>> = Mutex::new(None);
#[allow(clippy::type_complexity)]
static VERSIONS: Mutex<Option<HashMap<PathBuf, (Instant, Vec<Version>)>>> = Mutex::new(None);
/// This server's folder for past versions, made when one is first asked for and taken away when the server stops.
static VIEWS: Mutex<Option<places::Owner>> = Mutex::new(None);
/// What the home said of the other places, as worked out at most PLACES_EVERY ago.
#[allow(clippy::type_complexity)]
static ELSEWHERE: Mutex<Option<(PathBuf, Instant, Vec<Stamp>, Value)>> = Mutex::new(None);

/// The log of the record at `root` as it stands (its size and time); None where there is none to read.
type Stamp = Option<(u64, i64)>;

/// What the places and Elsewhere said is listed again at once after a write to the record rb open started on, since
/// the shell keys its page on them: its place's line and what the others wait on follow that write in the same poll,
/// so the page swaps once for it, not again when the listing lapses.
fn stamp_of(root: &Path) -> Stamp {
    crate::dry::size_time(&root.join(crate::ledger::DIR).join("log.jsonl"))
}

/// The logs of the record at `root` and of every other record of the places `list` names: a write to any of them
/// lists the places and Elsewhere again at once, so a page shown at another place, and the home's Elsewhere, follow a
/// write there in the same poll too, never again when the listing lapses.
fn stamps(root: &Path, list: &[Place]) -> Vec<Stamp> {
    let others = list.iter().filter_map(|p| p.root.as_deref()).filter(|r| *r != root);
    std::iter::once(stamp_of(root)).chain(others.map(stamp_of)).collect()
}

/// The places of the clone the record at `root` is in, as listed at most PLACES_EVERY ago and since the last write to
/// any record they hold.
fn places_of(root: &Path) -> Vec<Place> {
    let mut held = PLACES.lock().unwrap_or_else(|e| e.into_inner());
    if let Some((r, at, s, list)) = held.as_ref() {
        if r == root && at.elapsed() < PLACES_EVERY && *s == stamps(root, list) {
            return list.clone();
        }
    }
    let list = places::list(root);
    *held = Some((root.to_path_buf(), Instant::now(), stamps(root, &list), list.clone()));
    list
}

/// The versions of a place's record, as listed at most PLACES_EVERY ago.
fn versions_of(p: &Place) -> Vec<Version> {
    let Some(root) = p.root.clone() else { return Vec::new() };
    let mut held = VERSIONS.lock().unwrap_or_else(|e| e.into_inner());
    let map = held.get_or_insert_with(HashMap::new);
    if let Some((at, list)) = map.get(&root) {
        if at.elapsed() < PLACES_EVERY {
            return list.clone();
        }
    }
    let list = places::versions(&root);
    map.insert(root, (Instant::now(), list.clone()));
    list
}

/// This server's views folder, made the first time a past version is asked for.
fn views_folder(root: &Path) -> Option<PathBuf> {
    let mut held = VIEWS.lock().unwrap_or_else(|e| e.into_inner());
    if held.is_none() {
        *held = places::Owner::start(root);
    }
    held.as_ref().map(|o| o.dir.clone())
}

/// What a request shows: the place and version it chooses (`place` and `at`, as the server listed them), else the
/// record the server started on, live. Err says why nothing is shown for what it chose.
struct Shown {
    chosen: Chosen,
    places: Vec<Place>,
    versions: Vec<Version>,
}

impl Shown {
    fn of(root: &Path, query: &str) -> Result<Shown, String> {
        let (place, at) = (query_value(query, "place"), query_value(query, "at"));
        let list = places_of(root);
        let chosen = places::choose(root, &list, Some(&place), Some(&at), &versions_of, &|| views_folder(root))?;
        let versions = versions_of(&chosen.place);
        Ok(Shown { chosen, places: list, versions })
    }

    /// The place shown, against which each run says where it was recorded.
    fn context(&self) -> crate::view::PlaceShown {
        places::shown(&self.chosen, &self.places)
    }

    /// The record shown is the one the server started on, live: scanned on a cadence, as always.
    fn live(&self) -> bool {
        self.chosen.start
    }

    /// A fingerprint of what is shown: the record's own, live; with the place and version for any other, so a switch
    /// between two places that hold the same record redraws.
    fn digest(&self, of_record: String) -> String {
        placed_digest(of_record, &self.chosen)
    }

    /// The query that asks for what is shown again: none at the place rb open started on, live; else the place and
    /// the version, by the id and commit the server listed.
    fn query(&self) -> String {
        let mut q = Vec::new();
        if self.chosen.place.record != "this" {
            q.push(format!("place={}", quote(&self.chosen.place.id)));
        }
        if let Some(v) = &self.chosen.version {
            q.push(format!("at={}", quote(&v.sha)));
        }
        if q.is_empty() { String::new() } else { format!("?{}", q.join("&")) }
    }

    /// Where the screen's data says where it shows, beside the record: `place` and `places`.
    fn add_to(&self, payload: &mut Value) {
        if let Value::Dict(d) = payload {
            d.insert("place".into(), self.chosen.value(&self.versions));
            d.insert("places".into(), Value::List(self.places.iter().map(Place::to_value).collect()));
        }
    }
}

/// What rb open's home says of each other place, on the place it started on, live (`elsewhere`): its label in short,
/// its whole path, what it is with its own counts and last write (`said`), what it holds that is not merged here, as
/// rb status's Elsewhere says it (`adds`), whether it can be shown and why not, and the calls that wait there: each
/// one that waits for a confirmation with the one command that confirms it, and how many wait on the user, with the
/// command that lists them. Nothing is added up across places. Worked out at most PLACES_EVERY ago and since the
/// last write to any listed record.
pub(crate) fn elsewhere_rows(led: &Ledger, list: &[Place]) -> Value {
    let stamp = stamps(&led.root, list);
    let mut held = ELSEWHERE.lock().unwrap_or_else(|e| e.into_inner());
    if let Some((r, at, s, rows)) = held.as_ref() {
        if *r == led.root && at.elapsed() < PLACES_EVERY && *s == stamp {
            return rows.clone();
        }
    }
    let found = crate::elsewhere::elsewhere(led, None).unwrap_or_default();
    let said = crate::elsewhere::said_on_screen(led, &found).unwrap_or_default();
    let here = held_here(led);
    let adds = |p: &Place| -> String {
        found.iter().zip(&said).find(|(e, _)| e.value.get("path").and_then(Value::as_str) == Some(p.listed.as_str())).map(|(_, (a, _))| a.clone()).unwrap_or_default()
    };
    let rows = Value::List(
        list.iter()
            .filter(|p| p.record != "this")
            .map(|p| {
                let mut row = p.to_value();
                let (waiting, calls) = if p.readable() { waiting_there(p, &here) } else { (Vec::new(), 0) };
                if let Value::Dict(d) = &mut row {
                    d.insert("adds".into(), Value::Str(adds(p)));
                    d.insert("waiting".into(), Value::List(waiting));
                    d.insert("calls".into(), Value::Int(calls as i64));
                    d.insert("calls_command".into(), Value::Str(if calls > 0 { format!("cd {} && rb status", places::shell_word(&p.record_dir)) } else { String::new() }));
                }
                row
            })
            .collect(),
    );
    *held = Some((led.root.clone(), Instant::now(), stamp, rows.clone()));
    rows
}

/// What this record holds of what may wait for a confirmation in another place: each call, by its id, and each gate
/// and design, by its key, as `rb confirm` names them. One merged here waits, or was confirmed, here.
pub(crate) fn held_here(led: &Ledger) -> std::collections::HashSet<String> {
    let mut held: std::collections::HashSet<String> = led.all::<crate::record::Decision>().map(|ds| ds.iter().map(|d| d.id.clone()).collect()).unwrap_or_default();
    let words = led.gate_words().unwrap_or_default();
    for e in led.live::<crate::record::Experiment>().unwrap_or_default().iter().filter(|e| !(e.if_holds.is_empty() && e.if_not.is_empty())) {
        let said = words.get(&e.id).map(|(w, _)| w.as_str()).unwrap_or("");
        held.insert(format!("{}@{}", e.id, crate::view::gate_key(&e.if_holds, &e.if_not, said)));
    }
    // and each design, by its key: the same design here waits, or was confirmed, here
    for (id, d) in led.designs().map(|d| d.as_ref().clone()).unwrap_or_default() {
        held.insert(format!("{id}@{}", d.confirmation_key()));
    }
    held
}

/// The calls that wait in another place's own record, read there and never written: each that waits for a confirmation
/// ({kind, text, confirm}, the command with the cd to its record) and is not held `here` too, and how many wait on the
/// user there.
pub(crate) fn waiting_there(p: &Place, here: &std::collections::HashSet<String>) -> (Vec<Value>, usize) {
    let Some(root) = p.root.clone() else { return (Vec::new(), 0) };
    let read = crate::dry::reading(|| -> Res<(Vec<Value>, usize)> {
        let led = Ledger::open(Some(&root))?;
        let st = Value::Dict(led.status(None)?);
        let rows: Vec<crate::view::PendingRow> = crate::view::for_a_screen(|| crate::view::pending_rows(&st)).into_iter().map(crate::view::PendingRow::drawn).collect();
        let pending = crate::ledger::PENDING_CODES;
        let calls = match st.get("open") {
            Some(Value::List(items)) => items
                .iter()
                .filter(|i| i.get("who").and_then(Value::as_str) == Some("human") && !i.get("code").and_then(Value::as_str).map_or(false, |c| pending.contains(&c)))
                .count(),
            _ => 0,
        };
        let cd = places::shell_word(&p.record_dir);
        let waiting = rows
            .iter()
            .filter(|r| !here.contains(&r.id))
            .map(|r| {
                let mut row = crate::dict! {"kind" => r.kind.clone(), "text" => r.text.clone(), "confirm" => format!("cd {cd} && {}", r.confirm)};
                // a design is confirmed only as it was listed, and this list says it in one line: the command there that
                // lists it, which ends with the one that confirms it
                if r.kind == "design" {
                    row.insert("list".into(), Value::Str(format!("cd {cd} && rb confirm {}", r.id.split('@').next().unwrap_or(""))));
                }
                Value::Dict(row)
            })
            .collect();
        Ok((waiting, calls))
    });
    read.unwrap_or_default()
}

/// The journal's look at the record rb open started on, at most once every SCAN_EVERY whichever route asks.
fn scan_on_cadence(led: &Ledger) {
    let key = led.root.to_string_lossy().into_owned();
    let due = {
        let mut guard = LAST_SCAN.lock().unwrap();
        let map = guard.get_or_insert_with(HashMap::new);
        let now = Instant::now();
        let due = map.get(&key).map_or(true, |t| now.duration_since(*t) > SCAN_EVERY);
        if due {
            map.insert(key, now);
        }
        due
    };
    if due {
        let _ = crate::journal::scan(led); // observation is best effort: a journal that cannot be kept does not stop the view
    }
}

/// The fingerprint `of_record` as a place shows it: the record's own at the place rb open started on, live; with the
/// place and version for any other, so a switch between two places that hold the same record redraws.
fn placed_digest(of_record: String, chosen: &Chosen) -> String {
    if chosen.start {
        return of_record;
    }
    let at = chosen.version.as_ref().map(|v| v.sha.clone()).unwrap_or_default();
    crate::sources::sha256_bytes(format!("{of_record}\n{}\n{at}", chosen.place.id).as_bytes())
}

/// The key of what a page of rb open shows (BP 4a): the record's digest, which covers its writes, tampering and newer
/// files, with what the journal found that nobody recorded and the changed files nothing cites, which it does not; and
/// where it shows the record, as /state.json's place, places and elsewhere say it (the place chosen with its record's
/// versions, every place of the clone `listed` as rb open lists them, and on the place it started on what the others
/// hold), which the record's digest does not cover either; as the place chosen shows it.
pub(crate) fn live_key(led: &Ledger, chosen: &Chosen, listed: &[Place]) -> Res<String> {
    let mut said = found_said(led)?;
    said.insert("place".into(), chosen.value(&versions_of(&chosen.place)));
    said.insert("places".into(), Value::List(listed.iter().map(Place::to_value).collect()));
    // as state_payload says it: on the place rb open started on, when there is another
    if chosen.start && listed.len() > 1 {
        said.insert("elsewhere".into(), elsewhere_rows(led, listed));
    }
    Ok(placed_digest(keyed(led, said)?, chosen))
}

/// What the journal found that nobody recorded, the results folders nothing reads and the changed files nothing
/// cites, with the line that counts them; and what rb computes on reading the record (a source that now states
/// another value, a resource whose copy is gone, a folder to index again), as rb status says it, which moves with no
/// record file written (the record's reading has no time of the journal's look, which the scope line says apart). One rule
/// for every key that follows them.
fn found_said(led: &Ledger) -> Res<Dict> {
    let (asks, unplaced, uncited, line) = crate::view::found(led);
    let status = led.status(None)?;
    Ok(crate::dict! {
        "status" => Value::Dict(status),
        "found" => Value::List(asks.iter().map(|a| crate::list![a.request_id.clone(), a.title.clone(), a.asked_at.clone(), Value::list_of_str(a.argv.iter().cloned())]).collect()),
        "unplaced" => Value::list_of_str(unplaced),
        "uncited" => Value::List(uncited.iter().map(|u| crate::list![u.key.clone(), u.said()]).collect()),
        "line" => line.map_or(Value::Null, |(long, short)| crate::list![long, short])
    })
}

/// The record's digest with `said`, in one sha256.
fn keyed(led: &Ledger, said: Dict) -> Res<String> {
    let said = pyjson::dumps_with(&Value::Dict(said), &pyjson::Dumps { sort_keys: true, ..Default::default() }).unwrap_or_default();
    Ok(crate::sources::sha256_bytes(format!("{}\n{said}", led.state_digest()?).as_bytes()))
}

/// `read` on the record of the place and version the query chose: the record rb open started on, live, after the
/// journal's look on its cadence; any other inside a preview, so nothing is written there. Err says why nothing is
/// shown for what the query chose.
fn on_shown<T>(root: &Path, query: &str, read: impl FnOnce(&Ledger, &Shown) -> Res<T>) -> Res<Result<T, String>> {
    let shown = match Shown::of(root, query) {
        Ok(s) => s,
        Err(why) => return Ok(Err(why)),
    };
    if shown.live() {
        let led = Ledger::open(Some(root))?;
        scan_on_cadence(&led);
        return read(&led, &shown).map(Ok);
    }
    let got = crate::dry::reading(|| read(&Ledger::open(Some(&shown.chosen.root))?, &shown));
    match (got, shown.chosen.gone_now()) {
        (Err(_), Some(why)) => Ok(Err(why)),
        (got, _) => got.map(Ok),
    }
}

/// The shell's page of what the query chose, as rb open serves it: the live document drawn, with the live module.
/// The key is taken before the document is built, so a write between the two redraws once more, never not at all.
pub fn shell_payload(root: &Path, query: &str) -> Res<Result<String, String>> {
    on_shown(root, query, |led, shown| {
        let key = live_key(led, &shown.chosen, &shown.places)?;
        let page = crate::shell::page(led, crate::shell::Surface::Open, Some(&shown.chosen))?;
        Ok(with_live(&page, &key, &shown.query()))
    })
}

/// What the live module asks every few seconds: the key of what the query chose, and the scope line (where rb looks
/// and when it last did), which the page says again in place; the key leaves the time out, or every look would redraw.
pub fn digest_payload(root: &Path, query: &str) -> Res<Result<Value, String>> {
    on_shown(root, query, |led, shown| {
        // as the home says it: a place shown in a preview is not looked at
        let scope = (!crate::dry::active()).then(|| crate::journal::Scope::of(led));
        Ok(crate::obj! {
            "key" => live_key(led, &shown.chosen, &shown.places)?,
            "scope" => Value::opt_str(scope.as_ref().map(|s| s.said())),
            "scope_short" => Value::opt_str(scope.as_ref().map(|s| s.said_short()))
        })
    })
}

/// One card of what the query chose, as the shell draws it (`/o/<id>.html`), or rb's words for an id that names none.
pub fn node_payload(root: &Path, id: &str, query: &str) -> Res<Result<String, String>> {
    let got = on_shown(root, query, |led, shown| {
        let doc = crate::shell::document(led, crate::shell::Surface::Open, Some(&shown.chosen))?;
        Ok(card_in(&crate::shell::nodes::all(&doc, crate::shell::Surface::Open), id))
    })?;
    Ok(got.and_then(|card| card.ok_or_else(|| format!("nothing here is called {}", pyjson::repr_str(id)))))
}

/// The page as rb open serves it: the live module at the end of its head, with the key of what the page shows and the
/// query that asks for it again.
fn with_live(page: &str, key: &str, query: &str) -> String {
    let attr = |s: &str| s.replace('&', "&amp;").replace('"', "&quot;").replace('<', "&lt;").replace('>', "&gt;");
    let tag = format!("<script id=\"rb-live\" data-key=\"{}\" data-query=\"{}\">{LIVE_JS}</script>", attr(key), attr(query));
    let at = head_end(page).or_else(|| body_end(page)).unwrap_or(page.len());
    format!("{}{tag}{}", &page[..at], &page[at..])
}

/// Where the head of `page` (rb's own markup) ends: its end tag, or the body's start tag where that is left out; never
/// inside a comment, an attribute's value, or the text of a script, a style, a title or a textarea, which may hold
/// either.
fn head_end(page: &str) -> Option<usize> {
    let mut found = None;
    tags(page, |t| {
        if (t.closing && t.name == "head") || (!t.closing && t.name == "body") {
            found = Some(t.at);
            return false;
        }
        true
    });
    found
}

/// Where the last end tag of the body is, as `tags` reads the page.
fn body_end(page: &str) -> Option<usize> {
    let mut found = None;
    tags(page, |t| {
        if t.closing && t.name == "body" {
            found = Some(t.at);
        }
        true
    });
    found
}

/// The elements whose text is never markup, as a browser reads them: up to their own end tag, nothing in it is a tag.
/// A `/>` does not close one: it opens as any start tag does.
const RAW_TEXT: [&str; 8] = ["script", "style", "title", "textarea", "xmp", "iframe", "noembed", "noframes"];

/// A tag of rb's markup, as `tags` meets it: where it starts and ends, its name (in lower case) and whether it is an
/// end tag.
struct Tag<'a> {
    at: usize,
    end: usize,
    name: String,
    closing: bool,
    text: &'a str,
}

/// Each tag of `html` in order, as a browser reads them, to `meet` until it answers false: past comments (to `-->`,
/// or `--!>`; `<!-->` and `<!--->` end where they start), past `>` inside a value quoted after its `=`, and past the text of each RAW_TEXT element up to its own end tag (`</`
/// and its name, then `>`, `/` or white space: `</scripts>` ends nothing). Returns where it stopped.
fn tags<'a>(html: &'a str, mut meet: impl FnMut(&Tag<'a>) -> bool) -> usize {
    let b = html.as_bytes();
    let low = html.to_ascii_lowercase();
    let ends_name = |at: usize| b.get(at).map_or(true, |&c| c == b'>' || c == b'/' || c.is_ascii_whitespace());
    let mut i = 0;
    while let Some(lt) = html[i..].find('<').map(|j| i + j) {
        if html[lt..].starts_with("<!--") {
            let rest = &low[lt + 4..];
            i = if rest.starts_with('>') {
                lt + 5
            } else if rest.starts_with("->") {
                lt + 6
            } else {
                let close = [rest.find("-->").map(|j| lt + 4 + j + 3), rest.find("--!>").map(|j| lt + 4 + j + 4)];
                close.into_iter().flatten().min().unwrap_or(b.len())
            };
            continue;
        }
        let closing = b.get(lt + 1) == Some(&b'/');
        let from = lt + 1 + closing as usize;
        let name_end = html[from..].find(|c: char| c.is_ascii_whitespace() || c == '>' || c == '/').map_or(b.len(), |j| from + j);
        let name = low[from..name_end].to_string();
        if name.is_empty() || !name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-') {
            i = lt + 1; // a < that starts no tag is text
            continue;
        }
        // the tag's end, past values quoted right after their =, which may hold a >; a quote anywhere else is text
        let (mut j, mut quote, mut after_eq) = (name_end, None, false);
        while j < b.len() {
            match (quote, b[j]) {
                (None, b'"' | b'\'') if after_eq => quote = Some(b[j]),
                (Some(q), c) if c == q => quote = None,
                (None, b'>') => break,
                _ => {}
            }
            if quote.is_none() && !b[j].is_ascii_whitespace() {
                after_eq = b[j] == b'=';
            }
            j += 1;
        }
        i = (j + 1).min(b.len());
        let tag = Tag { at: lt, end: i, name, closing, text: &html[lt..i] };
        if !meet(&tag) {
            return lt;
        }
        if !tag.closing && RAW_TEXT.contains(&tag.name.as_str()) {
            let shut = format!("</{}", tag.name);
            let mut k = i;
            i = loop {
                match low[k..].find(&shut).map(|j| k + j) {
                    Some(at) if ends_name(at + shut.len()) => break at,
                    Some(at) => k = at + shut.len(),
                    None => break b.len(),
                }
            };
        }
    }
    b.len()
}

/// The card of `html` (rb's own markup) whose id is `id`: its element, start tag to end tag. An end tag rb's markup
/// leaves out closes with its parent, as a browser reads it: the card then ends before its parent's end tag, or at
/// the end of the document when nothing closes it. A card is drawn in an element whose end tag only its parent or its
/// own closes (article, section, div); one whose end tag a browser implies at a sibling (p, li, dt, dd, tr, td, option)
/// is not read here, and rb draws none.
fn card_in(html: &str, id: &str) -> Option<String> {
    const VOID: [&str; 14] = ["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "param"];
    let mut open: Vec<String> = Vec::new();
    let mut start: Option<(usize, usize)> = None; // where the card starts, and how deep
    let mut cut: Option<String> = None;
    tags(html, |t| {
        if t.closing {
            if let Some(k) = open.iter().rposition(|n| *n == t.name) {
                open.truncate(k);
                if let Some((s, depth)) = start {
                    // the card's own end tag, or its parent's, which closes it too
                    if open.len() <= depth {
                        cut = Some(html[s..if open.len() < depth { t.at } else { t.end }].to_string());
                        return false;
                    }
                }
            }
            return true;
        }
        let void = VOID.contains(&t.name.as_str()) || t.text.ends_with("/>");
        if start.is_none() && attr_of(t.text, "id").as_deref() == Some(id) && attr_of(t.text, "class").map_or(false, |c| c.split_whitespace().any(|w| w == "card")) {
            if void {
                cut = Some(t.text.to_string());
                return false;
            }
            start = Some((t.at, open.len()));
        }
        if !void {
            open.push(t.name.clone());
        }
        true
    });
    // a card nothing closes runs to the end of the document
    cut.or_else(|| start.map(|(s, _)| html[s..].to_string()))
}

/// An attribute's value in a start tag rb wrote, its entities read.
fn attr_of(tag: &str, name: &str) -> Option<String> {
    let b = tag.as_bytes();
    let mut i = tag.find(|c: char| c.is_ascii_whitespace())?;
    while i < b.len() {
        while i < b.len() && (b[i].is_ascii_whitespace() || b[i] == b'/') {
            i += 1;
        }
        let k = i;
        while i < b.len() && !b[i].is_ascii_whitespace() && !matches!(b[i], b'=' | b'>' | b'/') {
            i += 1;
        }
        let key = tag[k..i].to_ascii_lowercase();
        let mut value = String::new();
        let valued = b.get(i) == Some(&b'=');
        if valued {
            i += 1;
            let (from, to) = match b.get(i) {
                Some(&q) if q == b'"' || q == b'\'' => (i + 1, tag[i + 1..].find(q as char).map_or(b.len(), |j| i + 1 + j)),
                _ => (i, tag[i..].find(|c: char| c.is_ascii_whitespace() || c == '>').map_or(b.len(), |j| i + j)),
            };
            value = tag[from..to.min(b.len())].to_string();
            i = to + 1;
        }
        if key.is_empty() {
            // a > or / that names nothing is passed over; past a value read for no name, i is already past it
            if !valued {
                i += 1;
            }
            continue;
        }
        if key == name {
            return Some(value.replace("&quot;", "\"").replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"));
        }
    }
    None
}

/// urllib.parse.quote(s, safe=""): every byte outside the unreserved set as %XX.
fn quote(s: &str) -> String {
    s.bytes().map(|c| if c.is_ascii_alphanumeric() || b"-._~".contains(&c) { (c as char).to_string() } else { format!("%{c:02X}") }).collect()
}

/// time.strftime("%H:%M:%S"), in local time.
fn clock() -> String {
    let (_, _, _, hour, minute, second) = crate::sys::local_time(crate::sys::now_secs());
    format!("{hour:02}:{minute:02}:{second:02}")
}

/// The whole home view, computed fresh: open the ledger, scan the watched places on a cadence, and hand back what the
/// screen draws, with the place it shows (`place`) and the places it can show (`places`). Another place or a past
/// version (`query`) is read without a scan, and nothing is written there. Err says why nothing is shown for what the
/// query chose.
pub fn state_payload(root: &Path, query: &str) -> Res<Result<Value, String>> {
    let shown = match Shown::of(root, query) {
        Ok(s) => s,
        Err(why) => return Ok(Err(why)),
    };
    let ctx = shown.context();
    let mut elsewhere: Option<Value> = None;
    let (mut home, digest) = if shown.live() {
        let led = Ledger::open(Some(root))?;
        // the other places, as the home says them, when there is another
        if shown.places.len() > 1 {
            elsewhere = Some(elsewhere_rows(&led, &shown.places));
        }
        scan_on_cadence(&led);
        let home = drawn(&led, crate::view::for_a_screen(|| crate::view::at_place(ctx, || crate::view::home(&led, None).map(|h| h.to_value())))?)?;
        (home, led.state_digest()?)
    } else {
        let read = crate::dry::reading(|| {
            let led = Ledger::open(Some(&shown.chosen.root))?;
            let home = drawn(&led, crate::view::for_a_screen(|| crate::view::at_place(ctx, || crate::view::home(&led, None).map(|h| h.to_value())))?)?;
            Ok((home, led.state_digest()?))
        });
        match (read, shown.chosen.gone_now()) {
            (Err(_), Some(why)) => return Ok(Err(why)),
            (read, _) => read?,
        }
    };
    places::confirm_from_here(&mut home, &shown.chosen);
    let mut payload = crate::obj! {"home" => home, "digest" => shown.digest(digest), "rb_version" => crate::version(), "at" => clock()};
    shown.add_to(&mut payload);
    if let (Value::Dict(d), Some(rows)) = (&mut payload, elsewhere) {
        d.insert("elsewhere".into(), rows);
    }
    Ok(Ok(payload))
}

/// What the page draws as it is, as every screen draws text where something a session wrote can wait for the user
/// (`design::shown`): every text of the home and of a card, but the ids, commands and paths the page acts on; with
/// `escaped` where it draws an escape, so a word of them wraps however long, as on a page that draws one.
fn drawn(led: &Ledger, mut v: Value) -> Res<Value> {
    if led.shows_escapes()? && crate::ledger::design::shown_in(&mut v) {
        if let Value::Dict(d) = &mut v {
            d.insert("escaped".into(), Value::Bool(true));
        }
    }
    Ok(v)
}

/// One card, computed fresh: `o:<id>` is an object of the catalogue; anything else is something rb found. Of the place
/// and version the query chose, as state_payload reads them.
pub fn card_payload(root: &Path, r: &str, query: &str) -> Res<Result<Value, String>> {
    let shown = match Shown::of(root, query) {
        Ok(s) => s,
        Err(why) => return Ok(Err(why)),
    };
    let ctx = shown.context();
    let read = || -> Res<(Option<Value>, String, String)> {
        let led = Ledger::open(Some(&shown.chosen.root))?;
        let (card, missing) = crate::view::for_a_screen(|| {
            crate::view::at_place(ctx, || -> Res<_> {
                Ok(match r.strip_prefix("o:") {
                    Some(id) => (crate::view::object_card(&led, id)?.map(|c| c.to_value()), id.to_string()),
                    None => (crate::view::card(&led, r)?.map(|c| c.to_value()), r.to_string()),
                })
            })
        })?;
        let card = match card {
            Some(c) => Some(drawn(&led, c)?),
            None => None,
        };
        Ok((card, missing, led.state_digest()?))
    };
    let (card, missing, digest) = match (if shown.live() { read() } else { crate::dry::reading(read) }, shown.chosen.gone_now()) {
        (Err(_), Some(why)) => return Ok(Err(why)),
        (read, _) => read?,
    };
    Ok(match card {
        Some(c) => Ok(crate::obj! {"card" => c, "digest" => shown.digest(digest)}),
        None => Err(format!("nothing here is called {}", pyjson::repr_str(&missing))),
    })
}

/// The page rb page would write of the place and version the query chose.
pub fn page_payload(root: &Path, query: &str) -> Res<Result<String, String>> {
    let shown = match Shown::of(root, query) {
        Ok(s) => s,
        Err(why) => return Ok(Err(why)),
    };
    let render = || -> Res<String> {
        let led = Ledger::open(Some(&shown.chosen.root))?;
        crate::shell::page(&led, crate::shell::Surface::Page, None)
    };
    Ok(match (if shown.live() { render() } else { crate::dry::reading(render) }, shown.chosen.gone_now()) {
        (Err(_), Some(why)) => Err(why),
        (page, _) => Ok(page?),
    })
}

/// urllib.parse.unquote on one query field ('+' already a space): %XX escapes decoded as UTF-8 with replacement.
fn unquote(s: &str) -> String {
    let hex = |c: u8| (c as char).to_digit(16).map(|d| d as u8);
    let b = s.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let (Some(h), Some(l)) = (hex(b[i + 1]), hex(b[i + 2])) {
                out.push(h * 16 + l);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// parse_qs(query).get(name, [""])[0]: fields split on '&' only, a field without '=' or with an empty value skipped.
fn query_value(query: &str, name: &str) -> String {
    for field in query.split('&') {
        let Some((k, v)) = field.split_once('=') else { continue };
        if v.is_empty() {
            continue;
        }
        if unquote(&k.replace('+', " ")) == name {
            return unquote(&v.replace('+', " "));
        }
    }
    String::new()
}

/// urlparse(path) as the handler reads it: (path, query), or None where urlparse raises (an unbalanced IPv6 bracket).
fn url_parts(url: &str) -> Option<(String, String)> {
    let mut url: String = url.trim_start_matches(|c: char| c <= ' ').chars().filter(|c| !matches!(c, '\t' | '\r' | '\n')).collect();
    let mut scheme = String::new();
    if let Some(i) = url.find(':') {
        let head = &url[..i];
        if i > 0 && head.starts_with(|c: char| c.is_ascii_alphabetic()) && head.chars().all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c)) {
            scheme = head.to_ascii_lowercase();
            url = url[i + 1..].to_string();
        }
    }
    if url.starts_with("//") {
        let delim = url[2..].find(|c| matches!(c, '/' | '?' | '#')).map_or(url.len(), |d| d + 2);
        let netloc = &url[2..delim];
        if netloc.contains('[') != netloc.contains(']') {
            return None;
        }
        url = url[delim..].to_string();
    }
    if let Some(i) = url.find('#') {
        url.truncate(i);
    }
    let mut query = String::new();
    if let Some(i) = url.find('?') {
        query = url[i + 1..].to_string();
        url.truncate(i);
    }
    const USES_PARAMS: [&str; 16] = ["", "ftp", "hdl", "prospero", "http", "imap", "https", "shttp", "rtsp", "rtsps", "rtspu", "sip", "sips", "mms", "sftp", "tel"];
    if USES_PARAMS.contains(&scheme.as_str()) && url.contains(';') {
        let cut = match url.rfind('/') {
            Some(slash) => url[slash..].find(';').map(|j| j + slash),
            None => url.find(';'),
        };
        if let Some(i) = cut {
            url.truncate(i);
        }
    }
    Some((url, query))
}

/// http.server's table: the reason phrase and the explanation its error page gives.
fn responses(code: u16) -> (&'static str, &'static str) {
    match code {
        200 => ("OK", "Request fulfilled, document follows"),
        400 => ("Bad Request", "Bad request syntax or unsupported method"),
        403 => ("Forbidden", "Request forbidden -- authorization will not succeed"),
        404 => ("Not Found", "Nothing matches the given URI"),
        405 => ("Method Not Allowed", "Specified method is invalid for this resource"),
        414 => ("Request-URI Too Long", "URI is too long"),
        431 => ("Request Header Fields Too Large", "The server is unwilling to process the request because its header fields are too large"),
        501 => ("Not Implemented", "Server does not support this operation"),
        505 => ("HTTP Version Not Supported", "Cannot fulfill request"),
        _ => ("Internal Server Error", "Server got itself in trouble"),
    }
}

const ERROR_PAGE: &str = "<!DOCTYPE HTML>\n<html lang=\"en\">\n    <head>\n        <meta charset=\"utf-8\">\n        <title>Error response</title>\n    </head>\n    <body>\n        <h1>Error response</h1>\n        <p>Error code: {code}</p>\n        <p>Message: {message}.</p>\n        <p>Error code explanation: {code} - {explain}.</p>\n    </body>\n</html>\n";

fn http_date() -> String {
    // the wall clock, as http.server dates its answers (never the fixed test clock)
    chrono::Utc::now().format("%a, %d %b %Y %H:%M:%S GMT").to_string()
}

/// One connection, answered the way BaseHTTPRequestHandler answers it: HTTP/1.0, one request, then closed.
struct Conn {
    w: TcpStream,
    version: String, // the request's; an HTTP/0.9 request is answered with the body alone
    command: String,
}

impl Conn {
    fn head(&mut self, code: u16, reason: &str, headers: &[(&str, String)]) {
        if self.version == "HTTP/0.9" {
            return;
        }
        // the status line is latin-1, as the request line was read
        let mut h: Vec<u8> = format!("HTTP/1.0 {code} {reason}\r\n").chars().map(|c| c as u32 as u8).collect();
        h.extend_from_slice(format!("Server: rb/{}\r\nDate: {}\r\n", crate::version(), http_date()).as_bytes());
        for (k, v) in headers {
            h.extend_from_slice(format!("{k}: {v}\r\n").as_bytes());
        }
        h.extend_from_slice(b"\r\n");
        let _ = self.w.write_all(&h);
    }

    /// One answer; HEAD gets the headers without the body.
    fn send(&mut self, code: u16, ctype: &str, body: &[u8]) {
        let headers = [("Content-Type", ctype.to_string()), ("Content-Length", body.len().to_string()), ("Cache-Control", "no-store".to_string())];
        self.head(code, responses(code).0, &headers);
        if self.command != "HEAD" {
            let _ = self.w.write_all(body);
        }
    }

    fn json(&mut self, code: u16, v: &Value) {
        self.send(code, "application/json; charset=utf-8", pyjson::dumps(v).as_bytes());
    }

    /// send_error: the message is the status line's reason too; HEAD gets the headers without the page.
    fn error(&mut self, code: u16, message: Option<String>, explain: Option<String>) {
        let (short, long) = responses(code);
        let esc = |t: &str| t.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
        let message = message.unwrap_or_else(|| short.to_string());
        let body = ERROR_PAGE
            .replace("{code}", &code.to_string())
            .replace("{message}", &esc(&message))
            .replace("{explain}", &esc(&explain.unwrap_or_else(|| long.to_string())));
        let headers = [("Connection", "close".to_string()), ("Content-Type", "text/html;charset=utf-8".to_string()), ("Content-Length", body.len().to_string())];
        self.head(code, &message, &headers);
        if self.command != "HEAD" {
            let _ = self.w.write_all(body.as_bytes());
        }
    }
}

/// readline(limit): through the next newline, at most `limit` bytes.
fn read_line(r: &mut BufReader<TcpStream>, limit: u64) -> Vec<u8> {
    let mut buf = Vec::new();
    let _ = (&mut *r).take(limit).read_until(b'\n', &mut buf);
    buf
}

/// str.isspace for a character of a latin-1 line, which is what str.split() splits on.
fn py_space(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\x0b' | '\x0c' | '\r' | '\x1c'..='\x1f' | ' ' | '\u{85}' | '\u{a0}')
}

/// What rb open acts on in a request: its method, its target, and three of its headers.
struct Request {
    command: String,
    path: String,
    usage: bool,        // X-RB-Usage: 1
    foreign_host: bool, // a Host that is not this machine's loopback, as a page whose name was rebound to 127.0.0.1 sends
    cross_site: bool,   // a Sec-Fetch-Site other than same-origin
}

/// Whether a Host header names this machine's loopback, its port aside: 127.0.0.1, localhost or [::1].
fn loopback_host(value: &str) -> bool {
    let host = match value.rfind(':') {
        Some(i) if !value[i..].contains(']') => &value[..i],
        _ => value,
    };
    ["127.0.0.1", "localhost", "[::1]"].iter().any(|h| host.eq_ignore_ascii_case(h))
}

/// The parsed request, or None once an error is answered.
fn parse_request(r: &mut BufReader<TcpStream>, c: &mut Conn) -> Option<Request> {
    let raw = read_line(r, 65537);
    if raw.len() > 65536 {
        c.version = String::new();
        c.error(414, None, None);
        return None;
    }
    if raw.is_empty() {
        return None;
    }
    let line: String = raw.iter().map(|&b| b as char).collect::<String>().trim_end_matches(['\r', '\n']).to_string();
    let words: Vec<&str> = line.split(py_space).filter(|w| !w.is_empty()).collect();
    if words.is_empty() {
        return None;
    }
    if words.len() >= 3 {
        let version = *words.last().unwrap();
        let parsed = version.strip_prefix("HTTP/").and_then(|base| {
            let parts: Vec<&str> = base.split('.').collect();
            if parts.len() != 2 || parts.iter().any(|p| p.is_empty() || p.len() > 10 || !p.bytes().all(|b| b.is_ascii_digit())) {
                return None;
            }
            Some((base, parts[0].parse::<u64>().ok()?, parts[1].parse::<u64>().ok()?))
        });
        let Some((base, major, minor)) = parsed else {
            c.error(400, Some(format!("Bad request version ({})", pyjson::repr_str(version))), None);
            return None;
        };
        if (major, minor) >= (2, 0) {
            c.error(505, Some(format!("Invalid HTTP version ({base})")), None);
            return None;
        }
        c.version = version.to_string();
    }
    if !(2..=3).contains(&words.len()) {
        c.error(400, Some(format!("Bad request syntax ({})", pyjson::repr_str(&line))), None);
        return None;
    }
    let (command, mut path) = (words[0].to_string(), words[1].to_string());
    if words.len() == 2 && command != "GET" {
        c.error(400, Some(format!("Bad HTTP/0.9 request type ({})", pyjson::repr_str(&command))), None);
        return None;
    }
    c.command = command.clone();
    if path.starts_with("//") {
        path = format!("/{}", path.trim_start_matches('/'));
    }
    // the headers: read and set aside, as http.client.parse_headers reads them, with its limits
    let mut count = 0;
    let (mut usage, mut foreign_host, mut cross_site) = (false, false, false);
    loop {
        let h = read_line(r, 65537);
        if h.len() > 65536 {
            c.error(431, Some("Line too long".into()), Some("got more than 65536 bytes when reading header line".into()));
            return None;
        }
        count += 1;
        if count > 100 {
            c.error(431, Some("Too many headers".into()), Some("got more than 100 headers".into()));
            return None;
        }
        if h.is_empty() || h == b"\n" || h == b"\r\n" {
            break;
        }
        // latin-1, as http.client decodes a header line, so a Host that is not UTF-8 is still read, and refused
        let header: String = h.iter().map(|&b| b as char).collect();
        let Some((name, value)) = header.split_once(':') else { continue };
        let value = value.trim();
        if name.eq_ignore_ascii_case("X-RB-Usage") {
            usage |= value == "1";
        } else if name.eq_ignore_ascii_case("Host") {
            foreign_host |= !loopback_host(value);
        } else if name.eq_ignore_ascii_case("Sec-Fetch-Site") {
            cross_site |= !value.eq_ignore_ascii_case("same-origin");
        }
    }
    Some(Request { command, path, usage, foreign_host, cross_site })
}

/// The id `/o/<id>.html` names.
fn node_id(path: &str) -> Option<String> {
    path.strip_prefix("/o/").and_then(|p| p.strip_suffix(".html")).map(unquote).filter(|id| !id.is_empty())
}

/// A route of rb open's shell.
fn shell_route(path: &str) -> bool {
    matches!(path, "/" | "/index.html" | "/digest.json") || node_id(path).is_some()
}

fn do_get(c: &mut Conn, root: &Path, req: &Request) {
    let Some((path, query)) = url_parts(&req.path) else { return }; // urlparse raised: the Python server drops the connection
    let result: Res<()> = crate::cli::guarded(|| -> Res<()> {
        match path.as_str() {
            "/telemetry/inspected" => {
                // Cross-origin images/preloads cannot add this header. There is no CORS permission
                // or preflight route, so only a deliberate same-origin fetch can record UI usage.
                if !req.usage || req.cross_site {
                    c.json(403, &crate::obj! {"error" => "explicit local UI request required"});
                    return Ok(());
                }
                // Fixed event, no query values or record fields. The page calls this when the record first
                // renders and when a card opens; /state.json polling never emits usage. Research remains read-only.
                crate::usage::inspected(crate::telemetry::Surface::Web);
                c.json(200, &crate::obj! {"ok" => true});
            }
            p if req.cross_site && (!query_value(&query, "place").is_empty() || !query_value(&query, "at").is_empty())
                && (matches!(p, "/state.json" | "/card.json" | "/page.html") || shell_route(p)) =>
            {
                // a page elsewhere, or an address typed in, cannot make this server check out a version, or read
                // another place, blind
                c.json(403, &crate::obj! {"error" => "A place or a version is chosen on rb open's own page."});
            }
            "/" | "/index.html" => match shell_payload(root, &query)? {
                Ok(page) => c.send(200, "text/html; charset=utf-8", page.as_bytes()),
                Err(why) => c.json(404, &crate::obj! {"error" => why}),
            },
            "/digest.json" => match digest_payload(root, &query)? {
                Ok(v) => c.json(200, &v),
                Err(why) => c.json(404, &crate::obj! {"error" => why}),
            },
            p if node_id(p).is_some() => match node_payload(root, &node_id(p).unwrap_or_default(), &query)? {
                Ok(card) => c.send(200, "text/html; charset=utf-8", card.as_bytes()),
                Err(why) => c.json(404, &crate::obj! {"error" => why}),
            },
            "/state.json" => match state_payload(root, &query)? {
                Ok(v) => c.json(200, &v),
                Err(why) => c.json(404, &crate::obj! {"error" => why}),
            },
            "/card.json" => {
                let r = query_value(&query, "ref");
                if r.is_empty() {
                    c.json(400, &crate::obj! {"error" => "card.json takes ?ref=<id>"});
                    return Ok(());
                }
                match card_payload(root, &r, &query)? {
                    Ok(v) => c.json(200, &v),
                    Err(why) => c.json(404, &crate::obj! {"error" => why}),
                }
            }
            "/page.html" => match page_payload(root, &query)? {
                Ok(html) => c.send(200, "text/html; charset=utf-8", html.as_bytes()),
                Err(why) => c.json(404, &crate::obj! {"error" => why}),
            },
            other => c.json(404, &crate::obj! {"error" => format!("{other} is not served here")}),
        }
        Ok(())
    })
    .and_then(|r| r);
    if let Err(e) = result {
        // a fault in rb itself is the server's (500); anything else is the record saying no (404)
        let status = if e.exit_code == crate::errors::EXIT_ENVIRONMENT { 500 } else { 404 };
        // with the line that says what to do, as rb's text gives it under the message
        c.json(status, &crate::obj! {"error" => if e.message.is_empty() { e.code.clone() } else { e.message.clone() }, "code" => e.code.clone(), "fix" => e.fix.clone()});
    }
}

fn handle(stream: TcpStream, root: PathBuf) {
    let Ok(read_half) = stream.try_clone() else { return };
    let mut r = BufReader::new(read_half);
    let mut c = Conn { w: stream, version: "HTTP/0.9".into(), command: String::new() };
    if let Some(req) = parse_request(&mut r, &mut c) {
        if req.foreign_host {
            // a page whose name was rebound to 127.0.0.1 is same-origin here; the name it sends gives it away
            c.json(403, &crate::obj! {"error" => "rb open answers only at 127.0.0.1, localhost or [::1]. Open the link it printed."});
        } else {
            match req.command.as_str() {
                "GET" => do_get(&mut c, &root, &req),
                "POST" => c.json(405, &crate::obj! {"error" => "rb open only reads. Decisions run in your terminal."}),
                other => c.error(501, Some(format!("Unsupported method ({})", pyjson::repr_str(other))), None),
            }
        }
    }
    let _ = c.w.flush();
    let _ = c.w.shutdown(std::net::Shutdown::Write);
}

pub fn not_a_port(given: &str) -> RBError {
    RBError::msg_fix("E_USAGE", format!("{given} is not a port: a port is a number from 0 to 65535."), "Pick another: rb open --port <n>.")
}

/// A loopback-only, read-only server for this project, until Ctrl-C.
pub fn serve(root: &Path, port: i64) -> Res<i32> {
    let listener = match u16::try_from(port).ok().map(|p| TcpListener::bind(("127.0.0.1", p))) {
        Some(Ok(l)) => l,
        Some(Err(e)) => {
            let strerror = match e.kind() {
                // the two that happen, in the same words on every system (Windows has a sentence of its own for each)
                std::io::ErrorKind::AddrInUse => "Address already in use".to_string(),
                std::io::ErrorKind::PermissionDenied => "Permission denied".to_string(),
                _ => crate::sys::os_words(&e),
            };
            return Err(RBError::msg_fix("E_USAGE", format!("Port {port} is not available ({strerror})."), "Pick another: rb open --port <n>."));
        }
        None => return Err(not_a_port(&port.to_string())),
    };
    // past versions a server or a screen left when it was stopped without the chance to take them away
    if let Some(home) = places::views_home(root) {
        places::sweep(&home);
    }
    let bound = listener.local_addr().map(|a| a.port()).unwrap_or(0);
    crate::cli::eprintln_err(&format!("rb open · http://127.0.0.1:{bound}/"));
    crate::cli::eprintln_err("this machine only.");
    crate::cli::eprintln_err(&format!("from another machine, bring it over ssh:  ssh -N -L {bound}:127.0.0.1:{bound} <this-host>  then open the same link there"));
    crate::cli::eprintln_err("Ctrl-C stops the server.");
    // so does a request to end and a terminal that closed: each takes the past versions it checked out away first
    let ctrl_c = crate::sys::catch_endings();
    let _ = listener.set_nonblocking(true);
    while !crate::sys::interrupted() {
        match listener.accept() {
            Ok((stream, _)) => {
                let _ = stream.set_nonblocking(false);
                let root = root.to_path_buf();
                std::thread::spawn(move || handle(stream, root));
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(25)),
            Err(_) => std::thread::sleep(Duration::from_millis(25)),
        }
    }
    drop(ctrl_c);
    drop(VIEWS.lock().unwrap_or_else(|e| e.into_inner()).take()); // the past versions it checked out go with it
    if !crate::sys::by_ctrl_c() {
        crate::sys::end_interrupted();
    }
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::{card_in, loopback_host, node_id, quote, with_live, Shown, LIVE_JS};
    use crate::places::{Chosen, Place, Version};
    use std::path::PathBuf;

    /// The page says how to ask for itself again: nothing at the place rb open started on, live; the place's id away
    /// from it; the commit of a past version, the start place's or another's.
    #[test]
    fn a_page_asks_for_its_place_and_version_again() {
        let place = |id: &str, record: &'static str| Place {
            id: id.into(), listed: String::new(), path: PathBuf::new(), branch: None, head: None, dirty: false, record, root: None,
            record_dir: String::new(), uncommitted: 0, last_write: None, runs: 0, gone: false, short: String::new(), many: true,
        };
        let shown = |p: Place, sha: Option<&str>, start: bool| Shown {
            chosen: Chosen { root: PathBuf::new(), place: p, version: sha.map(|s| Version { sha: s.into(), committed: String::new() }), start },
            places: Vec::new(), versions: Vec::new(),
        };
        assert_eq!(shown(place("p_0a1b2c3d4e", "this"), None, true).query(), "");
        assert_eq!(shown(place("p_0a1b2c3d4e", "this"), Some("3f2a9c1"), false).query(), "?at=3f2a9c1");
        assert_eq!(shown(place("p_5f6e7d8c9b", "own"), None, false).query(), "?place=p_5f6e7d8c9b");
        assert_eq!(shown(place("p_5f6e7d8c9b", "separate"), Some("3f2a9c1"), false).query(), "?place=p_5f6e7d8c9b&at=3f2a9c1");
    }

    /// rb open's page carries the live module once, at the end of its head, with the key and the query in attributes
    /// it cannot break out of; a page whose head's end tag is left out gets it where its body starts, one with neither
    /// before its body ends, and a fragment at its end.
    #[test]
    fn the_live_module_goes_at_the_end_of_the_head() {
        let tag = |k: &str, q: &str| format!("<script id=\"rb-live\" data-key=\"{k}\" data-query=\"{q}\">{LIVE_JS}</script>");
        let page = "<!doctype html><html><head><script>a()</script></head><body><main id=\"main\"></main></body></html>";
        let served = with_live(page, "k1", "?place=p_1&at=a\"b<c>");
        assert_eq!(served, page.replace("</head>", &(tag("k1", "?place=p_1&amp;at=a&quot;b&lt;c&gt;") + "</head>")));
        assert_eq!(served.matches("id=\"rb-live\"").count(), 1);
        assert_eq!(with_live("<body><p>x</p></body>", "k", ""), format!("{}<body><p>x</p></body>", tag("k", "")));
        assert_eq!(with_live("<p>x</p></body>", "k", ""), format!("<p>x</p>{}</body>", tag("k", "")));
        assert_eq!(with_live("<p>x</p>", "k", ""), format!("<p>x</p>{}", tag("k", "")));
        // a head whose script, style or comment holds the text of an end tag: the module goes after them, in the head
        let held = "<html><head><!-- </head> --><style>p::after{content:\"</head>\"}</style><script>s='</head><body>'</script>\
                    <SCRIPT type=\"x\">t='</head>'</SCRIPT></head><body><p>x</p></body></html>";
        assert_eq!(with_live(held, "k", ""), held.replace("</SCRIPT></head>", &format!("</SCRIPT>{}</head>", tag("k", ""))));
        // a head whose end tag is left out ends where the body starts
        assert_eq!(with_live("<head><title>t</title><body class=\"b\"><p>x</p></body>", "k", ""),
                   format!("<head><title>t</title>{}<body class=\"b\"><p>x</p></body>", tag("k", "")));
        // a script's text ends only at its own end tag, `</script` then >, / or white space; an attribute's value, a
        // title's and a textarea's text hold no tag either; a comment ends at --!> too; a body's end tag in capitals
        for (head, text) in [
            ("<script>x='</scripts>'; y='</head>'</script>", "<script>x='</scripts>'; y='</head>'</script>"),
            ("<script>x='</script-x></head>'</script >", "<script>x='</script-x></head>'</script >"),
            ("<meta name=\"d\" content=\"a</head>b\">", "<meta name=\"d\" content=\"a</head>b\">"),
            ("<meta name=\"d\" content=\"a>b</head>c\">", "<meta name=\"d\" content=\"a>b</head>c\">"),
            ("<title>a</head>b</title><textarea></head></textarea>", "<title>a</head>b</title><textarea></head></textarea>"),
            ("<!-- a --!><style>p{}</style>", "<!-- a --!><style>p{}</style>"),
        ] {
            let page = format!("<html><head>{head}</head><body><p>x</p></body></html>");
            assert_eq!(with_live(&page, "k", ""), format!("<html><head>{text}{}</head><body><p>x</p></body></html>", tag("k", "")), "{head}");
        }
        assert_eq!(with_live("<p>x</p></BODY></HTML>", "k", ""), format!("<p>x</p>{}</BODY></HTML>", tag("k", "")));
        // <!--> and <!---> end where they start; a quote not right after = is text; a script's /> opens it all the same
        for (head, text) in [
            ("<!--><style>p{}</style>", "<!--><style>p{}</style>"),
            ("<!---><style>p{}</style>", "<!---><style>p{}</style>"),
            ("<meta name=d content=it's>", "<meta name=d content=it's>"),
            ("<script src=x />let s='</head>'</script>", "<script src=x />let s='</head>'</script>"),
            ("<noembed></head></noembed>", "<noembed></head></noembed>"),
        ] {
            let page = format!("<html><head>{head}</head><body><p>x</p></body></html>");
            assert_eq!(with_live(&page, "k", ""), format!("<html><head>{text}{}</head><body><p>x</p></body></html>", tag("k", "")), "{head}");
        }
        // with no head, the body's last end tag as the scanner reads it, never one inside a comment or a longer name
        assert_eq!(with_live("<p>x</p></body><!-- </body> --></bodyx>", "k", ""), format!("<p>x</p>{}</body><!-- </body> --></bodyx>", tag("k", "")));
        // nothing in the module ends its own element early
        assert!(!LIVE_JS.to_ascii_lowercase().contains("</script") && !LIVE_JS.contains("<!--") && !LIVE_JS.trim().is_empty());
    }

    /// `/o/<id>.html` serves the card whose id it names, whole: its own nested elements, a void or self-closed one, a
    /// > inside a quoted value, a comment and a script that hold its name, and an end tag left out inside it.
    #[test]
    fn a_card_is_cut_whole_from_the_cards() {
        let c1 = "<article class=\"card\" id=\"c-c1\" data-home=\"q-q1\"><details class=\"rc\" data-fold=\"c1:r\"><summary>receipts</summary>\
                  <dl><dt>a</dt><dd title=\"x > y\">b<br>c</dd></dl></details><svg><circle r=\"1\"/></svg><p>open<div>in</div></article>";
        let all = format!("<!-- id=\"c-c1\" class=\"card\" --><article class=\"card call\" id=\"call-c1\"><p>x</p></article>{c1}\
                           <script>let s='<article class=\"card\" id=\"c-c2\">';</script><article class='card' id='c-c2'><p>two</p></article>");
        assert_eq!(card_in(&all, "c-c1").as_deref(), Some(c1));
        assert_eq!(card_in(&all, "call-c1").as_deref(), Some("<article class=\"card call\" id=\"call-c1\"><p>x</p></article>"));
        assert_eq!(card_in(&all, "c-c2").as_deref(), Some("<article class='card' id='c-c2'><p>two</p></article>"));
        let crumb = "<article class=\"card\" data-crumb=\"run > receipt\" id=\"run-r1\"><p>r1</p></article>";
        assert_eq!(card_in(&format!("<p>x</p>{crumb}"), "run-r1").as_deref(), Some(crumb));
        // an id that names no card, or names something that is not one
        assert_eq!(card_in(&all, "c-c3"), None);
        assert_eq!(card_in("<div id=\"c-c1\"><p>not a card</p></div>", "c-c1"), None);
        assert_eq!(card_in("<article class=\"cards\" id=\"c-c1\"></article>", "c-c1"), None);
        // a card whose end tag is left out ends where its parent does, before the parent's end tag
        assert_eq!(card_in("<div><article class=\"card\" id=\"a-a1\"><p>x</p></div><p>after</p>", "a-a1").as_deref(),
                   Some("<article class=\"card\" id=\"a-a1\"><p>x</p>"));
        assert_eq!(card_in("<section><div><article class=\"card\" id=\"a-a1\"><p>x</section>", "a-a1").as_deref(),
                   Some("<article class=\"card\" id=\"a-a1\"><p>x"));
        // a script inside it ends only at its own end tag; a card nothing closes runs to the end of the document
        let scripted = "<article class=\"card\" id=\"s-1\"><script>let t='</scripts></article>';</script><p>after</p></article>";
        assert_eq!(card_in(&format!("<div>{scripted}</div>"), "s-1").as_deref(), Some(scripted));
        assert_eq!(card_in("<main><p>x</p><article class=\"card\" id=\"x\"><p>y", "x").as_deref(), Some("<article class=\"card\" id=\"x\"><p>y"));
        // a comment that ends where it starts hides nothing; a value read for no name never cuts a character in two
        assert_eq!(card_in("<div><article class=\"card\" id=\"x\"><!--></article><p>after</p></div>", "x").as_deref(),
                   Some("<article class=\"card\" id=\"x\"><!--></article>"));
        assert_eq!(card_in("<div =a 日 class=\"card\" id=\"u\">x</div>", "u").as_deref(), Some("<div =a 日 class=\"card\" id=\"u\">x</div>"));
    }

    /// The id an address names, its escapes read; anything else under /o/ names none.
    #[test]
    fn an_address_names_one_card() {
        assert_eq!(node_id("/o/c-c1.html").as_deref(), Some("c-c1"));
        assert_eq!(node_id("/o/x-e1%3Aam%3A1.html").as_deref(), Some("x-e1:am:1"));
        for path in ["/o/.html", "/o/c-c1", "/o/c-c1.json", "/c-c1.html", "/o"] {
            assert_eq!(node_id(path), None, "{path}");
        }
        assert_eq!(quote("p_1a2b"), "p_1a2b");
        assert_eq!(quote("a b&c=d/é"), "a%20b%26c%3Dd%2F%C3%A9");
    }

    /// This machine's loopback names at any port, since a tunnel picks its own; any other name, a rebound one, never.
    #[test]
    fn only_a_loopback_host_is_answered() {
        for host in ["127.0.0.1", "127.0.0.1:8787", "localhost", "localhost:9000", "LocalHost:1", "[::1]", "[::1]:8787"] {
            assert!(loopback_host(host), "{host}");
        }
        for host in ["", "attacker.example", "attacker.example:8787", "127.0.0.1.attacker.example", "localhost.:8787", "127.0.0.2",
                     "0.0.0.0:8787", "::1", "[::1]x", "127.0.0.1:80:90", "localhost@attacker.example"] {
            assert!(!loopback_host(host), "{host}");
        }
    }
}
