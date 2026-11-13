//! The card (UX5 2.1): one record, or one thing the record cites, as fields in order, each with what it opens. A check, a
//! run, a setting, a commit, a file (or a folder), a decision and an experiment each have one. Every surface draws a card
//! from this one value: `rb show`'s fields in a terminal, rb ui's right pane and the web's inspector. `card` in
//! `rb show --json` holds it: `kind`, `id`, `title`, `fields[]` (`key`, `label`, `value`, `items[]` of `text` and
//! `opens`, `opens`, `said`, and a check's `figure`) and `cites[]` (`kind`, `target`, `opens`), every thing it cites
//! once, each with the one command that opens it. Words are labels: what rb computed is a value or a mark, and the
//! author's text is said as written.

use crate::errors::Res;
use crate::ledger::Ledger;
use crate::pyjson::py_str;
use crate::value::Value;
use std::path::Path;

/// The kinds of thing a card is drawn for.
pub const KINDS: [&str; 8] = ["check", "run", "setting", "commit", "file", "folder", "decision", "experiment"];

/// List items a field shows before `+n more`, which opens the long form.
pub const ITEMS: usize = 6;
/// Lines of a file a card shows before `+n more lines`, which names the next lines to open.
pub const LINES: usize = 40;

fn s(v: &Value, k: &str) -> String {
    v.get(k).filter(|x| !x.is_null()).map(py_str).unwrap_or_default()
}

fn l<'a>(v: &'a Value, k: &str) -> &'a [Value] {
    match v.get(k) {
        Some(Value::List(x)) => x,
        _ => &[],
    }
}

fn f(v: &Value, k: &str) -> Option<f64> {
    v.get(k).and_then(Value::as_f64).filter(|x| x.is_finite())
}

/// `rb show <target>`: the command that opens a thing the CLI cites.
pub fn show(target: &str) -> String {
    format!("rb show {}", quoted(target))
}

/// A target as one shell word: quoted where it holds a character a shell reads.
fn quoted(t: &str) -> String {
    if !t.is_empty() && t.chars().all(|c| c.is_ascii_alphanumeric() || "_-./:#@=+,".contains(c)) {
        t.to_string()
    } else {
        format!("'{}'", t.replace('\'', "'\\''"))
    }
}

/// A day as the screens say it: `14 Oct 2026`, or `14 Oct` with `short`; `` where the time does not read.
pub fn day(iso: &str, short: bool) -> String {
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let b = iso.as_bytes();
    if b.len() < 10 || b[4] != b'-' || b[7] != b'-' {
        return String::new();
    }
    let (y, m, d) = (&iso[0..4], iso[5..7].parse::<usize>().unwrap_or(0), iso[8..10].trim_start_matches('0'));
    if !(1..=12).contains(&m) {
        return String::new();
    }
    if short {
        format!("{d} {}", MONTHS[m - 1])
    } else {
        format!("{d} {} {y}", MONTHS[m - 1])
    }
}

/// A time with its minute, in UTC: `14 Oct 2026 01:53 UTC`.
fn minute(iso: &str) -> String {
    let d = day(iso, false);
    if d.is_empty() || iso.len() < 16 {
        return d;
    }
    format!("{d} {} UTC", &iso[11..16])
}

/// One field of a card.
#[derive(Default, Clone)]
pub struct Field {
    pub key: &'static str,
    pub label: &'static str,
    pub value: String,
    pub items: Vec<(String, String)>,
    pub opens: String,
    pub said: String,
    pub figure: Option<Value>,
    /// a command the field gives (`rb confirm <id>`), its own part and never inside the value's text: a screen that
    /// offers no command (a past version, a reader's page) draws the field without it
    pub command: String,
}

impl Field {
    fn new(key: &'static str, label: &'static str, value: impl Into<String>) -> Field {
        Field { key, label, value: value.into(), ..Default::default() }
    }

    fn opens(mut self, cmd: impl Into<String>) -> Field {
        self.opens = cmd.into();
        self
    }

    fn said(mut self, said: impl Into<String>) -> Field {
        self.said = said.into();
        self
    }

    fn command(mut self, cmd: impl Into<String>) -> Field {
        self.command = cmd.into();
        self
    }

    /// The value with its command after it, as a terminal says the field (`□ not confirmed · rb confirm e1`).
    pub fn said_with_command(&self) -> String {
        match (self.value.is_empty(), self.command.is_empty()) {
            (_, true) => self.value.clone(),
            (true, false) => self.command.clone(),
            (false, false) => format!("{} \u{00b7} {}", self.value, self.command),
        }
    }

    /// Items, each with what it opens, cut to ITEMS with a last item that opens the rest.
    fn items(mut self, xs: Vec<(String, String)>, rest: &str) -> Field {
        let n = xs.len();
        self.items = xs.into_iter().take(ITEMS).collect();
        if n > ITEMS {
            self.items.push((format!("+{} more", n - ITEMS), rest.to_string()));
        }
        self
    }

    fn to_value(&self) -> Value {
        let mut d = crate::dict! {"key" => self.key, "label" => self.label, "value" => self.value.clone(),
            "items" => Value::List(self.items.iter().map(|(t, o)| crate::obj! {"text" => t.clone(), "opens" => if o.is_empty() { Value::Null } else { Value::str(o.clone()) }, "said" => item_said(t)}).collect()),
            "opens" => if self.opens.is_empty() { Value::Null } else { Value::str(self.opens.clone()) }, "said" => self.said.clone()};
        if let Some(fig) = &self.figure {
            d.insert("figure".into(), fig.clone());
        }
        if !self.command.is_empty() {
            d.insert("command".into(), Value::str(self.command.clone()));
        }
        Value::Dict(d)
    }
}

/// An item's words: its leading mark said as the label it stands for (`◧ c1` is "c1: can't tell yet"), else its text.
fn item_said(t: &str) -> String {
    const SAID: [(&str, &str); 11] = [
        ("\u{25a0} ", "passed"),
        ("\u{25b2} ", "failed"),
        ("\u{25e7} ", "can't tell yet"),
        ("\u{25a1} ", "no results yet"),
        ("\u{25cb} ", "not checked against a source"),
        ("? ", "not known"),
        ("\u{2713} ", "as it was"),
        ("\u{2717} ", "failed"),
        ("\u{25b3} ", "changed since"),
        ("\u{2205} ", "gone"),
        ("\u{00b1} ", "with changes no commit holds"),
    ];
    for (m, label) in SAID {
        if let Some(rest) = t.strip_prefix(m) {
            return format!("{rest}: {label}");
        }
    }
    for (m, label) in [(" \u{2713}", "unchanged since it was recorded"), (" \u{25b3}", "changed since it was recorded"), (" \u{2205}", "gone"), (" \u{00b1}", "with changes no commit holds")] {
        if let Some(rest) = t.strip_suffix(m) {
            return format!("{rest}: {label}");
        }
    }
    t.to_string()
}

/// A card: what it is, its fields in order, and everything it cites once.
pub struct Card {
    pub kind: &'static str,
    pub id: String,
    pub title: String,
    pub fields: Vec<Field>,
    /// the long form, `rb show <id> --long`, where there is one
    pub long: String,
}

impl Card {
    fn new(kind: &'static str, id: impl Into<String>, title: impl Into<String>) -> Card {
        Card { kind, id: id.into(), title: title.into(), fields: Vec::new(), long: String::new() }
    }

    fn push(&mut self, f: Field) {
        if !f.value.is_empty() || !f.items.is_empty() || f.figure.is_some() || !f.command.is_empty() {
            self.fields.push(f);
        }
    }

    /// OPEN saying that any id or path on the card opens with `rb show`. No field lists what each mark means: the
    /// card says each in words beside it (the ruling of Nov 12 20:35 +08, MP-1112-2347 B1).
    fn finish(mut self) -> Card {
        if let Some(o) = self.fields.iter_mut().find(|f| f.key == "open") {
            o.items.push(("\u{00b7} rb show <an id or path above> opens it".to_string(), String::new()));
        }
        self
    }

    /// Each thing a field opens, once, with its kind.
    pub fn cites(&self, led: &Ledger) -> Vec<Value> {
        let mut seen: Vec<String> = Vec::new();
        let mut out = Vec::new();
        let mut add = |cmd: &str| {
            if cmd.is_empty() || seen.iter().any(|x| x == cmd) {
                return;
            }
            seen.push(cmd.to_string());
            let (kind, target) = cite_of(led, cmd);
            out.push(crate::obj! {"kind" => kind, "target" => target, "opens" => cmd});
        };
        for f in &self.fields {
            add(&f.opens);
            for (_, o) in &f.items {
                add(o);
            }
            // a path a text names (a reason's `notes.md:5`) opens as the command's argument, the text unchanged
            if f.key != "lines" && f.key != "open" {
                for sp in crate::ledger::cite::spans(&f.value) {
                    add(&show(&sp.key));
                }
            }
        }
        out
    }

    pub fn to_value(&self, led: &Ledger) -> Value {
        crate::obj! {"kind" => self.kind, "id" => self.id.clone(), "title" => self.title.clone(),
            "fields" => Value::List(self.fields.iter().map(Field::to_value).collect()),
            "cites" => Value::List(self.cites(led)), "long" => if self.long.is_empty() { Value::Null } else { Value::str(self.long.clone()) }}
    }
}

/// What a command opens: its kind (`record`, `setting`, `file`, `folder`, `commit`, `long` for a record's long form,
/// `command` for a command of git's) and its target.
fn cite_of(led: &Ledger, cmd: &str) -> (&'static str, String) {
    let unq = |t: &str| t.trim_matches('\'').to_string();
    if let Some(rest) = cmd.strip_prefix("rb show ") {
        if let Some(t) = rest.strip_suffix(" --long") {
            return ("long", unq(t));
        }
        let t = unq(rest);
        let kind = if led.kind_of(&t).ok().flatten().is_some() {
            "record"
        } else if t.contains('/') && led.kind_of(&crate::ledger::parse_address(&t).0).ok().flatten() == Some("experiment") {
            "setting"
        } else if t.ends_with('/') {
            "folder"
        } else if hexish(&t) {
            "commit"
        } else {
            "file"
        };
        return (kind, t);
    }
    if let Some(rest) = cmd.strip_prefix("rb files") {
        return ("folder", unq(rest.trim()));
    }
    ("command", cmd.to_string())
}

// ---------------------------------------------------------------- what a subject names

/// What `rb show <subject>` names, where it is not a record of the ledger: a file and the lines or key in it, at a
/// commit or now; a folder; a commit.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Cite {
    pub path: String,
    pub line: usize,
    pub last: usize,
    pub key: String,
    pub commit: String,
    pub folder: bool,
}

fn hexish(t: &str) -> bool {
    (7..=40).contains(&t.len()) && t.chars().all(|c| c.is_ascii_hexdigit())
}

/// A citation as the CLI prints it: `path`, `path:3`, `path:3-9`, `path#key.path`, `path@commit` (with a line or a key
/// either side), `folder/`. None where it reads as none of them.
pub fn parse(subject: &str) -> Option<Cite> {
    let mut c = Cite::default();
    let mut rest = subject.trim().to_string();
    if rest.is_empty() || rest.contains("://") {
        return None;
    }
    // @commit, at the end or before #key or :lines
    if let Some(at) = rest.rfind('@') {
        let tail = &rest[at + 1..];
        let end = tail.find(|ch: char| ch == '#' || ch == ':').unwrap_or(tail.len());
        if hexish(&tail[..end]) {
            c.commit = tail[..end].to_string();
            rest = format!("{}{}", &rest[..at], &tail[end..]);
        }
    }
    if let Some((p, k)) = rest.split_once('#') {
        c.key = k.to_string();
        rest = p.to_string();
    } else if let Some((p, lines)) = rest.rsplit_once(':') {
        let (a, b) = lines.split_once('-').unwrap_or((lines, lines));
        if let (Ok(a), Ok(b)) = (a.parse::<usize>(), b.parse::<usize>()) {
            if a >= 1 && b >= a {
                c.line = a;
                c.last = b;
                rest = p.to_string();
            }
        }
    }
    let rest = rest.trim_start_matches("./").to_string();
    // rb's own files are records, read as records: who wrote each is no line a card shows
    if rest.is_empty() || rest.starts_with('/') || rest.split('/').any(|x| x == "..") || rest.split('/').next() == Some(".rb") || rest.contains('\\') {
        return None;
    }
    c.folder = rest.ends_with('/');
    c.path = rest.trim_end_matches('/').to_string();
    Some(c)
}

/// The commit `t` names in the repository at `root`, in full; None where it names none.
pub fn commit_of(root: &Path, t: &str) -> Option<String> {
    if !hexish(t) {
        return None;
    }
    crate::sources::git(root, &["rev-parse", "--verify", "-q", &format!("{t}^{{commit}}")]).map(|x| x.trim().to_string()).filter(|x| x.len() == 40)
}

/// A file's bytes, at a commit (rb's own copy in a bundle where git has none) or as it is now.
fn bytes_of(led: &Ledger, path: &str, commit: &str) -> Option<Vec<u8>> {
    if commit.is_empty() {
        if !crate::sources::inside_project(&led.root, path) {
            return None;
        }
        let p = led.root.join(path);
        let m = std::fs::metadata(&p).ok()?;
        if !m.is_file() || m.len() > crate::ledger::cite::MOST_BYTES {
            return None;
        }
        return std::fs::read(p).ok();
    }
    let full = commit_of(&led.root, commit).unwrap_or_else(|| commit.to_string());
    let rel = crate::sources::git_top(&led.root).and_then(|top| crate::sources::rel_to(&crate::sources::resolve_path(&led.root).join(path), &top)).unwrap_or_else(|| path.to_string());
    crate::sources::git_bytes(&led.root, &["cat-file", "blob", &format!("{full}:{rel}")]).or_else(|| {
        // a bundle carries the sources its checks read at a commit in .rb/cache/commits/
        std::fs::read(led.root.join(".rb/cache/commits").join(&full).join(path)).ok()
    })
}

/// Whether `rb show` can open `c`: a file or a folder in the project now, or a file at the commit it names.
pub fn opens(led: &Ledger, c: &Cite) -> bool {
    if c.folder || (c.commit.is_empty() && c.key.is_empty() && c.line == 0 && led.root.join(&c.path).is_dir()) {
        return c.commit.is_empty() && crate::sources::inside_project(&led.root, &c.path) && led.root.join(&c.path).is_dir() && !c.path.starts_with(".rb") && !c.path.is_empty();
    }
    bytes_of(led, &c.path, &c.commit).is_some()
}

/// The line a key path names in a config's text: each part found inside the block of the part before it (lines
/// indented deeper, as YAML and JSON nest; a `[section]` as TOML does), as YAML, JSON or TOML write a key.
pub fn key_line(text: &str, key: &str) -> Option<usize> {
    let lines: Vec<&str> = text.lines().collect();
    let indent = |l: &str| l.len() - l.trim_start().len();
    let names = |l: &str, part: &str| {
        let t = l.trim_start().trim_start_matches("- ");
        t.starts_with(&format!("{part}:"))
            || t.starts_with(&format!("\"{part}\":"))
            || t.starts_with(&format!("\"{part}\" :"))
            || t.starts_with(&format!("'{part}':"))
            || (t.starts_with(part) && t[part.len()..].trim_start().starts_with('=') && !t[part.len()..].trim_start().starts_with("=="))
    };
    let parts: Vec<&str> = key.split('.').filter(|p| !p.is_empty()).collect();
    // TOML: the longest [section] the key starts with, then the rest inside it
    for cut in (1..parts.len()).rev() {
        let head = format!("[{}]", parts[..cut].join("."));
        if let Some(h) = lines.iter().position(|l| l.trim() == head) {
            let rest = parts[cut..].join(".");
            let end = (h + 1..lines.len()).find(|&n| lines[n].trim_start().starts_with('[')).unwrap_or(lines.len());
            return (h + 1..end).find(|&n| names(lines[n], &rest)).map(|n| n + 1);
        }
    }
    let (mut from, mut to, mut floor) = (0, lines.len(), None::<usize>);
    let mut found = None;
    for part in &parts {
        let hit = (from..to).find(|&n| names(lines[n], part) && floor.map_or(true, |f| indent(lines[n]) > f) && {
            // directly inside the block: no line between at a shallower depth than this one
            true
        })?;
        let depth = indent(lines[hit]);
        if floor.map_or(false, |f| depth <= f) {
            return None;
        }
        found = Some(hit + 1);
        floor = Some(depth);
        from = hit + 1;
        to = (hit + 1..lines.len()).find(|&n| !lines[n].trim().is_empty() && indent(lines[n]) <= depth && !lines[n].trim_start().starts_with(['}', ']'])).unwrap_or(lines.len());
    }
    found
}

// ---------------------------------------------------------------- the cards

/// The card of `subject`, where it names one of the kinds a card is drawn for; None otherwise (a question, a resource,
/// an analysis and a note keep their long form).
pub fn of(led: &Ledger, subject: &str) -> Res<Option<Card>> {
    Ok(card_of(led, subject)?.map(Card::finish))
}

fn card_of(led: &Ledger, subject: &str) -> Res<Option<Card>> {
    if let Some(k) = led.kind_of(subject)? {
        return match k {
            "criterion" => check(led, subject).map(Some),
            "run" => run(led, subject).map(Some),
            "decision" => decision(led, subject).map(Some),
            "experiment" => experiment(led, subject).map(Some),
            _ => Ok(None),
        };
    }
    let (head, name) = crate::ledger::parse_address(subject);
    if name.is_some() && led.kind_of(&head)? == Some("experiment") {
        return match led.resolve_setting(subject) {
            Ok(_) => setting(led, subject).map(Some),
            Err(_) => Ok(None),
        };
    }
    if let Some(mut c) = parse(subject) {
        // a path is read from where the command runs, as every path rb takes is; else from the project's top
        if !opens(led, &c) {
            let here = std::env::current_dir().ok().and_then(|d| crate::sources::rel_to(&crate::sources::resolve_path(&d), &crate::sources::resolve_path(&led.root)));
            if let Some(rel) = here.filter(|r| !r.is_empty() && r != ".") {
                let mut c2 = c.clone();
                c2.path = format!("{}/{}", rel.trim_end_matches('/'), c.path);
                if opens(led, &c2) {
                    c = c2;
                }
            }
        }
        if opens(led, &c) {
            return Ok(Some(if c.folder || (c.commit.is_empty() && c.key.is_empty() && c.line == 0 && led.root.join(&c.path).is_dir()) { folder(led, &c)? } else { file(led, &c)? }));
        }
    }
    if let Some(full) = commit_of(&led.root, subject) {
        return commit(led, &full).map(Some);
    }
    Ok(None)
}

/// The number as the check reads it, signed for a difference: 4 significant digits, as rb's readings say them.
fn num(x: Option<f64>, signed: bool) -> String {
    crate::assess::n4(x, signed).replace('-', "\u{2212}")
}

/// The unit of a metric a check names (`top1`, `change.top1`, a variant's `int8.top1`), as the catalogue holds it: the
/// name itself, else the name after its arm's.
pub fn metric_unit(led: &Ledger, metric: &str) -> Res<String> {
    let m = metric.strip_prefix("change.").unwrap_or(metric);
    let mut names = vec![m];
    if let Some((_, rest)) = m.split_once('.') {
        names.push(rest);
    }
    for n in names {
        if let Some(e) = led.catalogue_entry(n)? {
            return Ok(e.unit);
        }
    }
    Ok(String::new())
}

/// A number at a check's edge, as the card's band formatter says it (4 significant digits, a true minus): every
/// surface's chart says the check's number in these words.
pub fn band_number(x: f64, signed: bool) -> String {
    num(Some(x), signed)
}

/// The unit a check's numbers are in, as the card says it: the metric's unit, a difference of a percentage in points.
pub fn unit_said(unit: &str, signed: bool) -> String {
    match unit {
        "%" | "percent" if signed => "points".to_string(),
        "percent" => "%".to_string(),
        u => u.to_string(),
    }
}

/// What a check accepts, in words a layman reads: `−1 or more`, `0.05 or less`, `5.4 ± 0.05`, `above 0`.
fn passes(side: &str, edge: Option<f64>, tol: f64, signed: bool) -> String {
    let Some(e) = edge else { return String::new() };
    let e_s = num(Some(e), signed && e != 0.0);
    match side {
        "ge" => format!("{e_s} or more"),
        "le" => format!("{e_s} or less"),
        "gt" => format!("above {e_s}"),
        "lt" => format!("below {e_s}"),
        // a band, as its two ends: ± is the mark of code no commit holds
        _ => {
            let (a, b) = band_ends(e, tol, signed);
            format!("{a} to {b}")
        }
    }
}

/// A band's two ends, in 4 significant digits, more where 4 would say both ends alike (`91.245 to 91.255`, never
/// `91.25 to 91.25`).
pub fn band_ends(e: f64, tol: f64, signed: bool) -> (String, String) {
    let at = |x: f64, k: usize| {
        let spec = if signed && x != 0.0 { format!("+.{k}g") } else { format!(".{k}g") };
        crate::pyfloat::fmt_float(x, &spec).replace('-', "\u{2212}")
    };
    let k = (4..=15).find(|k| at(e - tol, *k) != at(e + tol, *k)).unwrap_or(15);
    (at(e - tol, k), at(e + tol, k))
}

fn plural(n: usize, one: &str) -> String {
    if n == 1 {
        format!("1 {one}")
    } else {
        format!("{n} {one}s")
    }
}

/// The runs a finding counts, in its order.
fn counted_runs(fv: &Value) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for o in l(fv, "observations") {
        let r = s(o, "run");
        if o.get("counted").map_or(false, Value::truthy) && !out.contains(&r) {
            out.push(r);
        }
    }
    out
}

/// The commit a run's code is named by: the code its command started with, else the checkout it was recorded in; with
/// whether that code had changes no commit holds.
fn run_commit(r: &crate::record::Run) -> Option<(String, bool)> {
    let p = &r.receipt.produced;
    if let Some(c) = p.get("commit").filter(|v| v.truthy()).map(py_str) {
        let dirty = p.get("tree").map_or(false, Value::truthy) && p.get("dirty").map_or(false, Value::truthy);
        return Some((c, dirty));
    }
    let a = r.receipt.attached.as_ref()?;
    let c = a.get("commit").filter(|v| v.truthy()).map(py_str)?;
    Some((c, a.get("dirty").map_or(false, Value::truthy)))
}

/// A check as an item of a list: its state's square and its id, and, where git took a write to it out of the record,
/// that it was taken out (its own card says what).
fn check_item(led: &Ledger, id: &str, state: &str) -> Res<String> {
    let took = led.took_said("criterion", id)?.is_some();
    Ok(format!("{} {id}{}", crate::overview::mark_of(state), if took { TOOK } else { "" }))
}

/// What an item says after it where git took a write to it out of the record.
const TOOK: &str = " (a write taken out by git)";

fn check(led: &Ledger, id: &str) -> Res<Card> {
    let c: crate::record::Criterion = led.load(id)?;
    let fd = led.finding(&c)?;
    let fv = Value::Dict(fd.dump(false));
    let state = s(&fv, "rule_outcome");
    // its statement, where the words given with it are that statement, said once and as given (view::statement_said)
    let statement = crate::view::statement_said(&c.statement, &c.words, &c.created_by).unwrap_or_else(|| c.statement.clone());
    let mut card = Card::new("check", id, statement.clone());
    card.long = format!("{} --long", show(id));
    let mut head = Field::new("check", "CHECK", if c.retracted.is_some() { "retracted".to_string() } else { format!("{} {}", crate::overview::mark_of(&state), crate::overview::label_of(&state)) });
    let mut meta = vec![(c.role.clone(), String::new()), (c.experiment.clone(), show(&c.experiment))];
    for q in c.question_links.iter().filter(|q| q.retracted.is_none()).map(|q| q.question.clone()) {
        if !meta.iter().any(|(t, _)| *t == q) {
            meta.push((q.clone(), show(&q)));
        }
    }
    head = head.items(meta, &card.long).said(s(fv.get("says").unwrap_or(&Value::Null), "headline"));
    card.push(head);
    // its kind: a claim someone stated (a cited number or ordering) it checks, or a rule declared for the work
    let claim = c.origin == "cited";
    card.push(Field::new("kind", "KIND", if claim { "claim" } else { "rule" }).said(if claim {
        "it checks a claim a file states"
    } else {
        "a rule declared for this work"
    }));
    card.push(Field::new("statement", "", statement.clone()));
    // the result as a figure: its dot and range against the zone the check accepts
    let cmp = fv.get("comparison").cloned().unwrap_or(Value::Null);
    let directions: Option<bool> = {
        let metric = c.metric.strip_prefix("change.").unwrap_or(&c.metric).to_string();
        led.catalogue_entry(&metric)?.map(|m| m.direction).and_then(|d| match d.as_str() {
            "maximize" => Some(true),
            "minimize" => Some(false),
            _ => None,
        })
    };
    let (edge, tol, side) = crate::overview::zone(&c, directions);
    let signed = !c.reference.is_empty() || c.metric.starts_with("change.");
    let says = fv.get("says").cloned().unwrap_or(Value::Null);
    if let Some(est) = f(&cmp, "estimate") {
        let (lo, hi) = (f(&cmp, "lo"), f(&cmp, "hi"));
        // a difference of a percentage is in points
        let unit = unit_said(&metric_unit(led, &c.metric)?, signed);
        let fig = crate::obj! {"group" => crate::overview::measure(&c), "est" => Value::opt_f64(Some(est)), "lo" => Value::opt_f64(lo), "hi" => Value::opt_f64(hi),
            "threshold" => Value::opt_f64(edge), "tol" => tol, "side" => side, "state" => state.clone(), "difference" => signed};
        // the result and its range in the chart's digits (`+3.60 points  +0.62…+6.58`), one formatter for every surface
        let (est_t, range_t) = crate::overview::row_numbers(&fig);
        let range = if range_t.is_empty() { "(no range yet)".to_string() } else { range_t };
        let mut fld = Field::new("result", "RESULT", format!("{}  {range}", crate::overview::with_unit(&est_t, &unit))).said(s(&says, "comparison"));
        fld.figure = Some(fig);
        card.push(fld);
    } else {
        card.push(Field::new("result", "RESULT", "no result yet").said(s(&says, "comparison")));
    }
    let reads_range = matches!(c.comparator.as_str(), "better" | "noninferior" | "equivalent");
    let pass = passes(side, edge, tol, signed);
    if !pass.is_empty() {
        card.push(Field::new("passes", "PASSES", format!("\u{2591} {pass}{}", if reads_range { ", its whole range" } else { "" })).said(s(&says, "rule")));
    }
    // the number it reproduces, where it cites one: the file and line that state it
    if let Some(src) = c.source.as_ref().filter(|x| x.kind == "file") {
        if let Some(p) = src.path.as_deref() {
            let t = match src.line.filter(|n| *n > 0).or_else(|| src.resolved.as_ref().and_then(|r| r.line).filter(|n| *n > 0)) {
                Some(n) => format!("{p}:{n}"),
                None => p.to_string(),
            };
            let fld = Field::new("cited", "CITED", "").items(vec![(t.clone(), show(&t))], &card.long).said(src.label());
            card.push(fld);
        }
    }
    // the units it rests on
    let units = fv.get("units").cloned().unwrap_or(Value::Null);
    let n = units.get("n").and_then(Value::as_i64).unwrap_or(0) as usize;
    let unit = s(&units, "unit");
    let unit = if unit.is_empty() { "unit".to_string() } else { unit };
    let label: &'static str = if unit == "seed" { "SEEDS" } else { "UNITS" };
    if n > 0 || state != "untested" {
        let need = if c.min_n > 1 && (n as i64) < c.min_n { format!(", needs {}", c.min_n) } else { String::new() };
        let runs_n = units.get("runs").and_then(Value::as_i64).unwrap_or(0) as usize;
        let from = if runs_n > n { format!(" from {}", plural(runs_n, "run")) } else { String::new() };
        card.push(Field::new("units", label, format!("{}{from}{need}", plural(n, &unit))).said(s(&says, "units")));
    }
    // when it was written against when its results were made
    let timing = s(&fv, "timing");
    let declared = fv.get("declared").map(|d| s(d, "at")).unwrap_or_default();
    // its results' first day, on its subject's runs as its timing reads them (MP-1112-2347 S1, S2): a baseline's runs
    // that came first are no result of the rule's; every counted run where none names the subject
    let subject = fv.get("comparison").map(|c| s(c, "subject")).unwrap_or_default();
    let produced_of = |only: bool| -> Vec<String> {
        l(&fv, "observations")
            .iter()
            .filter(|o| o.get("counted").map_or(false, Value::truthy) && (!only || s(o, "arm") == subject))
            .map(|o| s(o, "produced_at"))
            .filter(|x| !x.is_empty())
            .collect()
    };
    let produced = match produced_of(!subject.is_empty()) {
        p if p.is_empty() => produced_of(false),
        p => p,
    };
    let first = produced.iter().min().cloned().unwrap_or_default();
    let (mark, word) = match timing.as_str() {
        "after_evidence" => ("\u{203e}", "after its results"),
        "after_exposure" => ("\u{203e}", "after its data was seen"),
        "unknown" => ("?", "order unknown"),
        // a check with no results has no order to say
        "" | "untested" | "taken_out" => ("", ""),
        _ => ("\u{2713}", "before its results"),
    };
    if !word.is_empty() || !declared.is_empty() {
        // the two placed in the order their days came (no glossary, show instead): a check with no results yet, or whose
        // order is unknown, says its own day alone
        let arrow = match (first.is_empty() || matches!(timing.as_str(), "untested" | "unknown" | "taken_out"), timing.as_str()) {
            (true, _) => format!("check {}", day(&declared, false)),
            // in the order their days came, whatever the timing's word (S2)
            (false, _) if before(&first, &declared) => format!("results {} \u{2500}\u{2500}\u{25b6} check {}", day(&first, false), day(&declared, false)),
            (false, _) => format!("check {} \u{2500}\u{2500}\u{25b6} results {}", day(&declared, false), day(&first, false)),
        };
        let v = if word.is_empty() { arrow } else { format!("{mark} {word}  \u{00b7}  {arrow}") };
        card.push(Field::new("written", "WRITTEN", v).said(s(&says, "timing")));
    }
    // the runs it counts, each opening its card
    let runs = counted_runs(&fv);
    let all = l(&fv, "observations").iter().map(|o| s(o, "run")).collect::<std::collections::BTreeSet<_>>().len();
    let not = all.saturating_sub(runs.len());
    let mut rf = Field::new("runs", "RUNS", "").items(runs.iter().map(|r| (r.clone(), show(r))).collect(), &card.long);
    if not > 0 {
        rf.items.push((format!("+{not} not counted"), card.long.clone()));
    }
    card.push(rf);
    // the settings it selects by, and its experiment's settings not checked against a source
    let mut sets: Vec<(String, String)> = Vec::new();
    for (k, v) in c.r#where.iter().chain(c.subject_where.iter()) {
        let t = format!("{k} = {}", py_str(v));
        if !sets.iter().any(|x| x.0 == t) {
            sets.push((t, show(&format!("{}/{k}", c.experiment))));
        }
    }
    for cv in l(&fv, "caveats").iter().filter(|x| matches!(s(x, "code").as_str(), "provisional" | "unknown" | "unknown_optional")) {
        let subject = s(cv, "subject");
        let name = subject.split_once('/').map(|x| x.1.to_string()).unwrap_or(subject.clone());
        let mk = if s(cv, "code") == "provisional" { "\u{25cb}" } else { "?" };
        sets.push((format!("{mk} {name}"), show(&subject)));
    }
    if !sets.is_empty() {
        card.push(Field::new("settings", "SETTINGS", "").items(sets, &card.long).said("\u{25cb} not checked against a source; ? not known"));
    }
    // the code its runs ran, each commit once
    let mut code: Vec<(String, String)> = Vec::new();
    for rid in &runs {
        let Ok(r) = led.load::<crate::record::Run>(rid) else { continue };
        if let Some((cm, dirty)) = run_commit(&r) {
            let dirty = dirty || r.receipt.config.as_ref().and_then(|c| crate::repo::config_dirty_line(&led.root, &c.path, c.sha256.as_deref())).is_some();
            let short: String = cm.chars().take(12).collect();
            let t = if dirty { format!("{short} \u{00b1}") } else { short.clone() };
            if !code.iter().any(|x| x.0.starts_with(&short)) {
                code.push((t, show(&short)));
            }
        }
    }
    if !code.is_empty() {
        let any = code.iter().any(|(t, _)| t.ends_with('\u{00b1}'));
        let fld = Field::new("code", "CODE", "").items(code, &card.long);
        card.push(if any { fld.said("\u{00b1}: with changes no commit holds") } else { fld });
    }
    // problems with what it rests on: counted here, said in full by the long form
    let problems = l(&fv, "integrity");
    if !problems.is_empty() {
        let codes: Vec<String> = problems.iter().map(|p| s(p, "code").replace('_', " ")).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
        card.push(Field::new("problems", "PROBLEMS", format!("\u{2717} {}: {}", problems.len(), codes.join(", "))).opens(card.long.clone()).said(s(&says, "integrity")));
    }
    // the decision on it
    let review = fv.get("review").cloned().unwrap_or(Value::Null);
    let rid = s(&review, "decision");
    let rid = if rid.is_empty() { s(&review, "id") } else { rid };
    if review.is_null() || review.as_dict().map_or(true, |d| d.is_empty()) {
        card.push(Field::new("decision", "DECISION", "none").said("no decision"));
    } else {
        let v = format!("{} {}", s(&review, "action"), day(&s(&review, "at"), false)).trim().to_string();
        let mut fld = Field::new("decision", "DECISION", v).said(s(&says, "review"));
        if !rid.is_empty() {
            fld.items = vec![(rid.clone(), show(&rid))];
        }
        card.push(fld);
    }
    card.push(Field::new("open", "OPEN", card.long.clone()).opens(card.long.clone()));
    Ok(card)
}

fn run(led: &Ledger, id: &str) -> Res<Card> {
    let r: crate::record::Run = led.load(id)?;
    let mut card = Card::new("run", id, String::new());
    card.long = format!("{} --long", show(id));
    let mut head = Field::new("run", "RUN", "").items(vec![(r.experiment.clone(), show(&r.experiment))], &card.long);
    if r.retracted.is_some() {
        head.value = "retracted".into();
    }
    card.push(head);
    let per: Vec<(String, String)> = r.per_run.iter().map(|(k, v)| (format!("{k} = {}", py_str(v)), String::new())).collect();
    card.push(Field::new("per_run", "PER RUN", "").items(per, &card.long));
    let mut ms: Vec<(&String, &f64)> = r.metrics.iter().collect();
    ms.sort_by(|a, b| a.0.cmp(b.0));
    let mut nums: Vec<(String, String)> = Vec::new();
    for (k, v) in &ms {
        let base = k.rsplit('.').next().unwrap_or(k);
        let unit = led.catalogue_entry(k)?.or(led.catalogue_entry(base)?).map(|m| m.unit).unwrap_or_default();
        nums.push((format!("{k} = {}{}", crate::pyfloat::run_number(**v), if unit.is_empty() { String::new() } else { format!(" {unit}") }), String::new()));
    }
    card.push(Field::new("numbers", "NUMBERS", "").items(nums, &card.long));
    let made = r.produced_at.clone().filter(|x| !x.is_empty());
    let recorded = r.receipt.at.clone();
    let when = match &made {
        Some(m) if day(m, false) != day(&recorded, false) => format!("made {} \u{2500}\u{2500}\u{25b6} recorded {}", minute(m), day(&recorded, false)),
        Some(m) => format!("made {} \u{00b7} recorded the same day", minute(m)),
        None => format!("made: not known \u{00b7} recorded {}", day(&recorded, false)),
    };
    card.push(Field::new("when", "WHEN", when).said(crate::ledger::support::produced_line(&r)));
    // the files it named, each with whether it is as it was
    let mut files: Vec<(String, String)> = Vec::new();
    for fr in &r.files {
        let state = led.file_state(fr);
        let mark = match state {
            "here" => "\u{2713}",
            "gone" => "\u{2205}",
            _ => "\u{25b3}",
        };
        files.push((format!("{} {mark}", fr.path), show(&fr.path)));
    }
    if let Some(cfg) = r.receipt.config.as_ref() {
        if !files.iter().any(|x| x.0.starts_with(&format!("{} ", cfg.path))) {
            let now = crate::sources::file_ref(&led.root, &cfg.path).ok().and_then(|f| f.get("sha256").map(py_str));
            let mark = match (&now, cfg.sha256.as_deref().filter(|x| !x.is_empty())) {
                (None, _) => " \u{2205}",
                (Some(n), Some(was)) if n != was => " \u{25b3}",
                (Some(_), Some(_)) => " \u{2713}",
                _ => "",
            };
            files.push((format!("{}{mark}", cfg.path), show(&cfg.path)));
        }
    }
    card.push(Field::new("files", "FILES", "").items(files, &card.long).said("\u{2713} unchanged since it was recorded; \u{25b3} changed; \u{2205} gone"));
    // what its own config says of its code, in the config's words, where it says the code had changes
    let cfg_dirty = r.receipt.config.as_ref().and_then(|c| crate::repo::config_dirty_line(&led.root, &c.path, c.sha256.as_deref()));
    if let Some((cm, dirty)) = run_commit(&r) {
        let short: String = cm.chars().take(12).collect();
        let text = if dirty || cfg_dirty.is_some() { format!("{short} \u{00b1}") } else { short.clone() };
        let fld = Field::new("code", "CODE", "").items(vec![(text, show(&short))], &card.long);
        card.push(match &cfg_dirty {
            Some(l) => fld.said(l.clone()),
            None if dirty => fld.said("with changes no commit holds"),
            None => fld,
        });
    }
    // the checks that count it
    let mut counts: Vec<(String, String)> = Vec::new();
    for c in led.all::<crate::record::Criterion>()?.iter().filter(|c| c.experiment == r.experiment && c.retracted.is_none()) {
        let fv = Value::Dict(led.finding(c)?.dump(false));
        if counted_runs(&fv).contains(&r.id) {
            counts.push((check_item(led, &c.id, &s(&fv, "rule_outcome"))?, show(&c.id)));
        }
    }
    let took = counts.iter().any(|(t, _)| t.ends_with(TOOK));
    let fld = Field::new("counts_in", "COUNTS IN", if counts.is_empty() { "no check" } else { "" }).items(counts, &card.long);
    card.push(if took { fld.said("a write to a check this run counts in was taken out by git") } else { fld });
    let reads: Vec<(String, String)> = r.inputs.iter().map(|u| (if u.role.is_empty() { u.resource.clone() } else { format!("{} ({})", u.resource, u.role) }, show(&u.resource))).collect();
    card.push(Field::new("read", "READ", "").items(reads, &card.long));
    let made_res: Vec<(String, String)> = r.outputs.iter().map(|u| (u.resource.clone(), show(&u.resource))).collect();
    card.push(Field::new("made", "MADE", "").items(made_res, &card.long));
    if let Some(g) = crate::ledger::support::run_command(&r) {
        let how = match g.given {
            "at_the_time" => "given when it ran",
            "observed" => "seen by rb as it ran",
            "logged" => "in a log written then",
            _ => "given later",
        };
        card.push(Field::new("command", "COMMAND", format!("{} \u{00b7} {how}", g.text)).said(g.line()));
    }
    card.push(Field::new("open", "OPEN", card.long.clone()).opens(card.long.clone()));
    Ok(card)
}

fn setting(led: &Ledger, address: &str) -> Res<Card> {
    let (exp, name, st) = led.resolve_setting(address)?;
    let address = format!("{}/{name}", exp.id);
    let mut card = Card::new("setting", address.clone(), name.clone());
    card.long = format!("{} --long", show(&address));
    let status = led.status_read(&exp, &name, &st)?;
    let (mark, word) = if st.per_run {
        ("\u{25c7}", "each run gives its own")
    } else {
        match st.status.as_str() {
            "verified" if status.starts_with("verified") => ("\u{2713}", "checked against its source"),
            "unknown" => ("?", "not known"),
            "inherited" => ("\u{21b3}", "inherited"),
            _ => ("\u{25cb}", "not checked"),
        }
    };
    let value = if st.per_run { String::new() } else { format!("{} = {}  ", name, py_str(&st.value)) };
    card.push(Field::new("setting", "SETTING", format!("{value}{mark} {word}")).items(vec![(exp.id.clone(), show(&exp.id))], &card.long).said(status));
    if let Some(src) = &st.source {
        let target = match (src.kind.as_str(), &src.path) {
            ("file", Some(p)) => {
                let mut t = p.clone();
                if let Some(k) = src.key.as_deref().filter(|k| !k.is_empty()) {
                    t.push('#');
                    t.push_str(k);
                } else if let Some(n) = src.line.filter(|n| *n > 0) {
                    t.push_str(&format!(":{n}"));
                }
                if let Some(c) = src.commit.as_deref().filter(|c| !c.is_empty()) {
                    t.push_str(&format!("@{}", c.chars().take(12).collect::<String>()));
                }
                t
            }
            ("run", Some(p)) => p.clone(),
            _ => String::new(),
        };
        let mut fld = Field::new("source", "SOURCE", if target.is_empty() { src.label() } else { String::new() });
        if !target.is_empty() && parse(&target).map_or(false, |c| opens(led, &c)) {
            fld.items = vec![(target.clone(), show(&target))];
        } else if !target.is_empty() {
            fld.value = format!("{target} \u{2205} not here");
        }
        card.push(fld.said(src.label()));
        if let Some(c) = src.resolved.as_ref().and_then(|r| r.commit.clone()).filter(|c| !c.is_empty()) {
            let short: String = c.chars().take(12).collect();
            card.push(Field::new("read_at", "READ AT", "").items(vec![(short.clone(), show(&short))], &card.long));
        }
    }
    let checks: Vec<(String, String)> = led
        .log_about(None, Some(&address))?
        .into_iter()
        .filter(|r| r.get("op").and_then(|v| v.as_str()) == Some("spec_verify"))
        .map(|r| {
            let d = crate::ledger::checked(&r, &name).map(Value::Dict).unwrap_or(Value::empty_dict());
            let ok = s(&d, "outcome");
            let mk = if ok.contains("verified") || ok == "ok" || d.get("ok").map_or(false, Value::truthy) { "\u{2713}" } else { "\u{2717}" };
            (format!("{mk} {}", day(&r.get("at").map(py_str).unwrap_or_default(), true)), String::new())
        })
        .collect();
    card.push(Field::new("checks", "CHECKS", "").items(checks, &card.long));
    let used: Vec<(String, String)> = led.all::<crate::record::Criterion>()?.iter().filter(|c| c.experiment == exp.id && c.retracted.is_none()).map(|c| (c.id.clone(), show(&c.id))).collect();
    card.push(Field::new("used_by", "USED BY", "").items(used, &card.long));
    card.push(Field::new("open", "OPEN", card.long.clone()).opens(card.long.clone()));
    Ok(card)
}

fn commit(led: &Ledger, full: &str) -> Res<Card> {
    let short: String = full.chars().take(12).collect();
    let mut card = Card::new("commit", short.clone(), String::new());
    let when = crate::sources::git(&led.root, &["show", "-s", "--format=%cI", full]).map(|x| x.trim().to_string()).unwrap_or_default();
    let utc = crate::sources::git(&led.root, &["show", "-s", "--format=%ct", full]).and_then(|x| x.trim().parse::<i64>().ok()).and_then(|s| chrono::DateTime::<chrono::Utc>::from_timestamp(s, 0)).map(|d| crate::pytime::iso_ms(&d)).unwrap_or(when);
    card.fields.push(Field::new("commit", "COMMIT", ""));
    card.push(Field::new("committed", "COMMITTED", minute(&utc)));
    let mut runs: Vec<(String, String)> = Vec::new();
    for r in led.all::<crate::record::Run>()?.iter().filter(|r| r.retracted.is_none()) {
        if run_commit(r).map_or(false, |(c, _)| c.starts_with(&short) || full.starts_with(&c)) {
            runs.push((r.id.clone(), show(&r.id)));
        }
    }
    let n = runs.len();
    let mut code_of = Field::new("code_of", "CODE OF", if n == 0 { "no run".to_string() } else { plural(n, "run") });
    // every run, as a commit's card has no long form to send the rest to
    code_of.items = runs;
    card.push(code_of);
    // the files the runs name (their results, their configs) are never a difference of code
    let named: std::collections::HashSet<String> = led
        .all::<crate::record::Run>()?
        .iter()
        .flat_map(|r| r.files.iter().map(|f| f.path.clone()).chain(r.receipt.config.iter().map(|c| c.path.clone())))
        .collect();
    // asked of a copy of the index: git diff refreshes the index it reads, and no read of rb's rewrites the user's
    let diff = crate::sources::git_diff_bytes(&led.root, &["--name-only", "--relative", full, "--", ".", ":(exclude).rb"])
        .map(|b| String::from_utf8_lossy(&b).lines().filter(|l| !l.is_empty() && !named.contains(*l)).count());
    card.push(Field::new(
        "your_checkout",
        "YOUR CHECKOUT",
        match diff {
            Some(0) => "\u{2713} the same files".to_string(),
            Some(n) => format!("differs in {}", plural(n, "file")),
            None => "rb could not compare it".to_string(),
        },
    ));
    card.push(Field::new("open", "OPEN", format!("git show {short} --stat")));
    Ok(card)
}

/// What the record names a file by: each record that cites it, with what it opens.
fn cited_by(led: &Ledger, path: &str) -> Res<Vec<(String, String)>> {
    let mut out: Vec<(String, String)> = Vec::new();
    let mut add = |t: String| {
        if !out.iter().any(|x| x.0 == t) {
            out.push((t.clone(), show(&t)));
        }
    };
    let names = |src: &Option<crate::record::Source>| src.as_ref().and_then(|s| s.path.as_deref()).map_or(false, |p| p.trim_start_matches("./") == path);
    for e in led.all::<crate::record::Experiment>()?.iter().filter(|e| e.retracted.is_none()) {
        for st in e.settings.iter().filter(|x| x.retracted.is_none()) {
            if names(&st.source) {
                add(format!("{}/{}", e.id, st.name));
            }
        }
        for v in &e.variants {
            for st in v.settings.iter().filter(|x| x.retracted.is_none()) {
                if names(&st.source) {
                    add(format!("{}/{}.{}", e.id, v.name, st.name));
                }
            }
        }
        if crate::ledger::cite::spans(&e.why).iter().any(|sp| sp.path == path) || e.notes.iter().filter(|n| n.retracted.is_none()).any(|n| crate::ledger::cite::spans(&n.text).iter().any(|sp| sp.path == path)) {
            add(e.id.clone());
        }
    }
    for c in led.all::<crate::record::Criterion>()?.iter().filter(|c| c.retracted.is_none()) {
        if names(&c.source) {
            add(c.id.clone());
        }
    }
    for r in led.all::<crate::record::Run>()?.iter().filter(|r| r.retracted.is_none()) {
        if r.files.iter().any(|f| f.path == path) || r.receipt.config.as_ref().map_or(false, |c| c.path == path) {
            add(r.id.clone());
        }
    }
    for d in led.all::<crate::record::Decision>()?.iter().filter(|d| d.retracted.is_none()) {
        let src = d.past.as_ref().and_then(|p| p.source.as_ref()).map(|s| s.path.clone()).unwrap_or_default();
        if src == path || crate::ledger::cite::spans(&d.why).iter().any(|sp| sp.path == path) {
            add(d.id.clone());
        }
    }
    Ok(out)
}

/// The lines of `path` each setting reads its value from, by its key or its line, as `rb files <path>` shows them:
/// each setting's address, the cite that opens it (`path#key`, `path:3`), the line it names, and the lines around it.
pub fn setting_lines(led: &Ledger, path: &str) -> Res<Vec<(String, String, usize, Vec<(usize, String)>)>> {
    let mut out = Vec::new();
    for e in led.all::<crate::record::Experiment>()?.iter().filter(|e| e.retracted.is_none()) {
        let shared = e.settings.iter().map(|s| (s.name.clone(), s));
        let own = e.variants.iter().flat_map(|v| v.settings.iter().map(move |s| (format!("{}.{}", v.name, s.name), s)));
        for (name, st) in shared.chain(own).filter(|(_, s)| s.retracted.is_none()) {
            let Some(src) = st.source.as_ref().filter(|x| x.kind == "file" && x.path.as_deref().map(|p| p.trim_start_matches("./")) == Some(path)) else { continue };
            // read where the setting reads it: at its commit where it names one, else as the file is now
            let commit = src.commit.clone().unwrap_or_default();
            let Some(bytes) = bytes_of(led, path, &commit) else { continue };
            let text = String::from_utf8_lossy(&bytes).into_owned();
            let lines: Vec<&str> = text.lines().collect();
            let at_c = if commit.is_empty() { String::new() } else { format!("@{}", commit.chars().take(12).collect::<String>()) };
            let (at, cite) = match (src.key.as_deref().filter(|k| !k.is_empty()), src.line.filter(|n| *n > 0)) {
                (Some(k), _) => (key_line(&text, k), format!("{path}#{k}{at_c}")),
                (None, Some(n)) => (Some(n as usize), format!("{path}:{n}{at_c}")),
                _ => (None, String::new()),
            };
            let Some(at) = at.filter(|n| *n >= 1 && *n <= lines.len()) else { continue };
            let around = (at.saturating_sub(1).max(1)..=(at + 1).min(lines.len())).map(|n| (n, lines[n - 1].to_string())).collect();
            out.push((format!("{}/{name}", e.id), cite, at, around));
        }
    }
    Ok(out)
}

/// How git holds a file now: committed, not committed, not tracked, ignored, or not in this clone.
fn git_state(led: &Ledger, path: &str) -> &'static str {
    if !led.root.join(path).exists() {
        return "\u{2205} not in this clone";
    }
    if crate::sources::git_top(&led.root).is_none() {
        return "not in git";
    }
    if crate::sources::git(&led.root, &["check-ignore", "-q", "--", path]).is_some() {
        return "\u{2298} ignored by git";
    }
    match crate::sources::git(&led.root, &["status", "--porcelain", "--untracked-files=all", "--", path]) {
        Some(o) if o.trim().is_empty() => "committed",
        Some(o) if o.starts_with("??") => "\u{00b1} not committed (new)",
        Some(_) => "\u{00b1} not committed (changed)",
        None => "rb could not read git",
    }
}

fn file(led: &Ledger, c: &Cite) -> Res<Card> {
    let bytes = bytes_of(led, &c.path, &c.commit).unwrap_or_default();
    let text = String::from_utf8_lossy(&bytes).into_owned();
    let lines: Vec<&str> = text.lines().collect();
    let mut target = c.path.clone();
    if !c.commit.is_empty() {
        target.push_str(&format!("@{}", c.commit));
    }
    let mut card = Card::new("file", target.clone(), c.path.clone());
    let (folder, _) = c.path.rsplit_once('/').unwrap_or(("", &c.path));
    let state = if c.commit.is_empty() { git_state(led, &c.path).to_string() } else { format!("at commit {}", c.commit.chars().take(12).collect::<String>()) };
    let mut head = Field::new("file", "FILE", state);
    if !folder.is_empty() {
        head.items = vec![(format!("{folder}/"), show(&format!("{folder}/")))];
    }
    card.push(head);
    card.push(Field::new("cited_by", "CITED BY", "").items(cited_by(led, &c.path)?, &show(&c.path)));
    // which lines: the ones named, the key's, else the file from its top
    let (a, b) = if c.line > 0 {
        (c.line, c.last.min(lines.len().max(c.line)))
    } else if !c.key.is_empty() {
        match key_line(&text, &c.key) {
            Some(n) => (n, n),
            None => (0, 0),
        }
    } else {
        (0, 0)
    };
    if !c.key.is_empty() && a == 0 {
        card.push(Field::new("key", "KEY", format!("{} not found in the file", c.key)));
    }
    let binary = bytes.iter().take(8000).any(|x| *x == 0);
    if binary {
        card.push(Field::new("lines", "LINES", format!("not text \u{00b7} {} bytes", bytes.len())));
    } else if a > lines.len() {
        card.push(Field::new("lines", "LINES", format!("line {a} is past the end: the file has {}", plural(lines.len(), "line"))));
    } else if !lines.is_empty() {
        // the lines named, with two around them, or the file from its top; never more than LINES at once
        let (from, to) = if a > 0 { (a.saturating_sub(2).max(1), (b + 2).min(lines.len())) } else { (1, lines.len()) };
        let to = to.min(from + LINES - 1);
        let shown: Vec<(String, String)> = (from..=to)
            .map(|n| {
                let mark = if a > 0 && n >= a && n <= b { "\u{25b6}" } else { " " };
                (format!("{mark}{n:>4}  {}", lines.get(n - 1).copied().unwrap_or("")), String::new())
            })
            .collect();
        let mut fld = Field { key: "lines", label: "LINES", items: shown, ..Default::default() };
        if to < lines.len() {
            let last = (to + LINES).min(lines.len());
            let next = format!("{}:{}-{last}", c.path, to + 1);
            let next = if c.commit.is_empty() { next } else { format!("{next}@{}", c.commit) };
            fld.items.push((format!("+{} more lines, the next {}", lines.len() - to, last - to), show(&next)));
        }
        card.fields.push(fld);
    }
    let open = if a > 0 || folder.is_empty() { show(&target) } else { show(&format!("{folder}/")) };
    card.push(Field::new("open", "OPEN", open.clone()).opens(open));
    Ok(card)
}

fn folder(led: &Ledger, c: &Cite) -> Res<Card> {
    let path = c.path.clone();
    let mut card = Card::new("folder", format!("{path}/"), path.clone());
    let mut entries: Vec<(String, bool)> = std::fs::read_dir(led.root.join(&path))
        .map(|rd| rd.filter_map(|e| e.ok()).map(|e| (e.file_name().to_string_lossy().into_owned(), e.path().is_dir())).filter(|(n, _)| !n.starts_with('.') || n == ".hydra").collect())
        .unwrap_or_default();
    entries.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    let up = path.rsplit_once('/').map(|x| format!("{}/", x.0));
    let mut head = Field::new("folder", "FOLDER", format!("{}, {}", plural(entries.iter().filter(|e| e.1).count(), "folder"), plural(entries.iter().filter(|e| !e.1).count(), "file")));
    if let Some(u) = up {
        head.items = vec![(u.clone(), show(&u))];
    }
    card.push(head);
    let items: Vec<(String, String)> = entries
        .iter()
        .map(|(n, d)| {
            let p = if *d { format!("{path}/{n}/") } else { format!("{path}/{n}") };
            (if *d { format!("{n}/") } else { n.clone() }, show(&p))
        })
        .collect();
    card.fields.push(Field { key: "entries", label: "IN IT", items, ..Default::default() });
    Ok(card)
}

fn decision(led: &Ledger, id: &str) -> Res<Card> {
    let d: crate::record::Decision = led.load(id)?;
    let st = Value::Dict(led.status(None)?);
    let row = l(&st, "decisions").iter().find(|x| s(x, "id") == id).cloned().unwrap_or(Value::Null);
    // a call that waits for a confirmation is listed under pending, not under decisions
    let waiting = l(&st, "pending").iter().find(|p| s(p, "decision") == id || s(p, "id") == id).cloned();
    let auth = match (&waiting, row.get("authority")) {
        (Some(p), _) => crate::obj! {"state" => if p.get("past").map_or(false, Value::truthy) { "past_unconfirmed" } else { "proposed" }, "pending" => true, "basis" => ""},
        (None, Some(a)) => a.clone(),
        (None, None) => Value::Null,
    };
    let mut card = Card::new("decision", id, format!("{} {}", d.action, d.subject));
    card.long = format!("{} --long", show(id));
    let subject_opens = if led.kind_of(&d.subject)?.is_some() || d.subject.contains('/') { show(&d.subject) } else { String::new() };
    card.push(Field::new("decision", "DECISION", d.action.clone()).items(vec![(d.subject.clone(), subject_opens)], &card.long));
    let state = match s(&auth, "state").as_str() {
        "direct" | "confirmed" => "\u{25a0} made",
        "proposed" => "\u{25a1} proposed, waits for a confirmation",
        "past_unconfirmed" => "\u{25a1} a past decision, waits for a confirmation",
        _ if d.retracted.is_some() => "retracted",
        _ => "",
    };
    card.push(Field::new("state", "STATE", state).said(s(&auth, "basis")));
    // a session's reason is its note on the call, as every line says it (`· note: ...`); the user's own, their why
    let note = crate::view::not_confirmed(&d.by);
    // a confirmation keeps the reason the session gave as its note (AGENTS.md, "Whose call it is")
    let theirs = crate::view::words_are_theirs(&d.by) && s(&auth, "state") != "confirmed";
    let why = match (d.why.is_empty(), theirs) {
        (true, _) => String::new(),
        (false, true) => d.why.clone(),
        (false, false) => format!("\u{b7} note: {}{note}", d.why),
    };
    card.push(Field::new("why", "WHY", why));
    if !d.words.is_empty() {
        card.push(Field::new("words", "WORDS", crate::view::your_words(&d.words, &d.by)));
    }
    let when = match &d.past {
        Some(p) => format!("before the record: {}", if p.when.is_empty() { "when not known".to_string() } else { p.when.clone() }),
        None => day(&d.at, false),
    };
    card.push(Field::new("when", "WHEN", when));
    if !d.revisit.is_empty() {
        card.push(Field::new("revisit", "REVISIT", d.revisit.clone()));
    }
    if let Some(src) = d.past.as_ref().and_then(|p| p.source.as_ref()) {
        let (path, line) = (src.path.clone(), src.line);
        if !path.is_empty() {
            let t = if line > 0 { format!("{path}:{line}") } else { path };
            card.push(Field::new("source", "SOURCE", "").items(vec![(t.clone(), show(&t))], &card.long));
        }
    }
    let on: Vec<(String, String)> = d.basis.keys().filter(|k| led.kind_of(k).ok().flatten().is_some()).map(|k| (k.clone(), show(k))).collect();
    card.push(Field::new("made_on", "MADE ON", "").items(on, &card.long));
    if auth.get("pending").map_or(false, Value::truthy) {
        card.push(Field::new("confirm", "CONFIRM", "").command(format!("rb confirm {id}")));
    }
    card.push(Field::new("open", "OPEN", card.long.clone()).opens(card.long.clone()));
    Ok(card)
}

fn experiment(led: &Ledger, id: &str) -> Res<Card> {
    let e: crate::record::Experiment = led.load(id)?;
    let st = Value::Dict(led.status(None)?);
    let row = l(&st, "experiments").iter().find(|x| s(x, "id") == id).cloned().unwrap_or(Value::Null);
    let mut card = Card::new("experiment", id, e.title.clone());
    card.long = format!("{} --long", show(id));
    card.push(Field::new("experiment", "EXPERIMENT", s(&row, "state")).items(e.questions.iter().map(|q| (q.clone(), show(q))).collect(), &card.long));
    card.push(Field::new("title", "", e.title.clone()));
    if !e.hypothesis.is_empty() {
        card.push(Field::new("hypothesis", "EXPECTS", e.hypothesis.clone()));
    } else if !e.purpose.is_empty() {
        card.push(Field::new("purpose", "PURPOSE", e.purpose.clone()));
    }
    let variants: Vec<(String, String)> = e.variants.iter().filter(|v| v.retracted.is_none()).map(|v| (format!("{} ({})", v.name, v.role), String::new())).collect();
    card.push(Field::new("variants", "VARIANTS", "").items(variants, &card.long));
    if !e.varies.is_empty() {
        card.push(Field::new("varies", "VARIES", e.varies.join(", ")));
    }
    let checks: Vec<(String, String)> = l(&st, "findings")
        .iter()
        .filter(|x| s(x, "experiment") == id)
        .map(|x| Ok((check_item(led, &s(x, "criterion"), &s(x, "rule_outcome"))?, show(&s(x, "criterion")))))
        .collect::<Res<Vec<_>>>()?;
    let took = checks.iter().any(|(t, _)| t.ends_with(TOOK));
    let fld = Field::new("checks", "CHECKS", if checks.is_empty() { "none yet" } else { "" }).items(checks, &card.long);
    card.push(if took { fld.said("a write to one of its checks was taken out by git") } else { fld });
    let counts = row.get("settings_count").cloned().unwrap_or(Value::Null);
    let n = |k: &str| counts.get(k).and_then(Value::as_i64).unwrap_or(0);
    let mut sv = vec![format!("\u{2713} {} checked", n("verified"))];
    if n("provisional") > 0 {
        sv.push(format!("\u{25cb} {} not checked", n("provisional")));
    }
    if n("unknown") > 0 {
        sv.push(format!("? {} not known", n("unknown")));
    }
    let open_sets: Vec<(String, String)> = e
        .settings
        .iter()
        .filter(|x| x.retracted.is_none() && !x.per_run && matches!(x.status.as_str(), "provisional" | "unknown"))
        .map(|x| (format!("{} {}", if x.status == "unknown" { "?" } else { "\u{25cb}" }, x.name), show(&format!("{id}/{}", x.name))))
        .collect();
    card.push(Field::new("settings", "SETTINGS", sv.join("  ")).items(open_sets, &card.long));
    let runs: Vec<(String, String)> = led.all::<crate::record::Run>()?.iter().filter(|r| r.experiment == id && r.retracted.is_none()).map(|r| (r.id.clone(), show(&r.id))).collect();
    let nr = runs.len();
    card.push(Field::new("runs", "RUNS", plural(nr, "run")).items(runs, &card.long));
    if !e.if_holds.is_empty() || !e.if_not.is_empty() {
        // with the words given when it was set, said once and as given where a session gave them, as every line says a
        // gate (view::gate_said)
        let (words, by) = led.gate_words()?.get(id).cloned().unwrap_or_default();
        card.push(Field::new("gate", "NEXT", crate::view::gate_said(&e.if_holds, &e.if_not, &words, &by, "")));
    }
    let design = row.get("design").cloned().unwrap_or(Value::Null);
    let standing = s(&design, "standing");
    if matches!(standing.as_str(), "not_confirmed" | "changed") {
        let said = if standing == "changed" { "the plan changed since it was confirmed" } else { "the plan is not confirmed" };
        card.push(Field::new("plan", "PLAN", "\u{25a1} not confirmed").command(format!("rb confirm {id}")).said(said));
    }
    card.push(Field::new("open", "OPEN", card.long.clone()).opens(card.long.clone()));
    Ok(card)
}

// ---------------------------------------------------------------- drawing it in a terminal

/// How a card is drawn: the terminal's width, ASCII glyphs where the locale is not UTF-8, colour on a terminal, and
/// each field's words under it.
#[derive(Clone, Copy)]
pub struct Draw {
    pub width: usize,
    pub ascii: bool,
    pub color: bool,
    pub words: bool,
}

/// Each glyph a card draws and its ASCII stand-in, as rb status draws them.
const GLYPHS: &[(char, &str)] = &[
    ('■', "#"),
    ('▲', "^"),
    ('◧', "%"),
    ('□', "."),
    ('●', "*"),
    ('○', "o"),
    ('├', "|"),
    ('─', "-"),
    ('┤', "|"),
    ('░', "~"),
    ('┆', ":"),
    ('┼', "+"),
    ('‾', "_"),
    ('✓', "+"),
    ('✗', "x"),
    ('▶', ">"),
    ('·', "-"),
    ('…', "..."),
    ('−', "-"),
    ('±', "+-"),
    ('∅', "0"),
    ('⊘', "-"),
    ('△', "^"),
    ('◇', "<>"),
    ('↳', "<-"),
    ('—', "-"),
    ('❝', "\""),
    ('▣', "o"),
];

impl Draw {
    pub fn here(words: bool) -> Draw {
        let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
        let locale = env("LC_ALL").or_else(|| env("LC_CTYPE")).or_else(|| env("LANG"));
        let utf8 = locale.map_or(true, |l| {
            let l = l.to_ascii_lowercase();
            l.contains("utf-8") || l.contains("utf8")
        });
        Draw {
            width: crate::argparse::terminal_width().clamp(40, 200),
            ascii: !utf8,
            color: crate::sys::isatty(1) && env("NO_COLOR").is_none() && env("TERM").as_deref() != Some("dumb"),
            words,
        }
    }

    /// rb's own text with each glyph drawn as this terminal takes it.
    fn text(&self, t: &str) -> String {
        if !self.ascii {
            return t.to_string();
        }
        t.chars().map(|c| GLYPHS.iter().find(|x| x.0 == c).map_or(c.to_string(), |x| x.1.to_string())).collect()
    }

    fn paint(&self, t: &str, style: &str) -> String {
        if !self.color || t.is_empty() {
            t.to_string()
        } else {
            format!("\x1b[{style}m{t}\x1b[0m")
        }
    }
}

fn cols(t: &str) -> usize {
    let mut n = 0;
    let mut esc = false;
    for c in t.chars() {
        if esc {
            esc = c != 'm';
        } else if c == '\x1b' {
            esc = true;
        } else {
            n += 1;
        }
    }
    n
}

/// Words wrapped to `w` columns, the first line after `first`, the rest after `next`.
fn wrap(text: &str, w: usize, first: &str, next: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut line = first.to_string();
    let mut empty = true;
    for word in text.split(' ').filter(|x| !x.is_empty()) {
        let need = cols(word) + if empty { 0 } else { 1 };
        if !empty && need > w.saturating_sub(cols(&line)) {
            out.push(line);
            line = next.to_string();
            empty = true;
        }
        if !empty {
            line.push(' ');
        }
        line.push_str(word);
        empty = false;
    }
    if !empty || out.is_empty() {
        out.push(line);
    }
    out
}

/// Text cut into rows of `w` characters, at a space where one falls in the row's last third.
fn wrap_chars(text: &str, w: usize) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let mut end = (i + w).min(chars.len());
        if end < chars.len() {
            if let Some(sp) = (i + w * 2 / 3..end).rev().find(|&k| chars[k] == ' ') {
                end = sp + 1;
            }
        }
        out.push(chars[i..end].iter().collect::<String>().trim_end().to_string());
        i = end;
    }
    if out.is_empty() {
        out.push(String::new());
    }
    out
}

/// Pieces joined with two spaces, wrapped between pieces.
fn wrap_pieces(pieces: &[String], w: usize, first: &str, next: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut line = first.to_string();
    let mut empty = true;
    for p in pieces {
        if !empty && cols(&line) + 2 + cols(p) > w {
            out.push(line);
            line = next.to_string();
            empty = true;
        }
        if !empty {
            line.push_str("  ");
        }
        line.push_str(p);
        empty = false;
    }
    if !empty {
        out.push(line);
    }
    out
}

/// The result's figure: the zone its check accepts shaded, the check's line, the range and the dot.
fn bar(fig: &Value, w: usize, d: &Draw) -> String {
    let mut vals: Vec<f64> = ["est", "lo", "hi"].iter().filter_map(|k| f(fig, k)).collect();
    if let Some(t) = f(fig, "threshold") {
        let tol = f(fig, "tol").unwrap_or(0.0);
        vals.extend([t - tol, t + tol]);
    }
    let lo = vals.iter().cloned().fold(f64::INFINITY, f64::min);
    let hi = vals.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let (lo, hi) = if !lo.is_finite() || !hi.is_finite() {
        (-1.0, 1.0)
    } else {
        let pad = if hi > lo { (hi - lo) * 0.12 } else if lo != 0.0 { lo.abs() * 0.05 } else { 0.5 };
        (lo - pad, hi + pad)
    };
    let x = |v: f64| -> usize { (((v - lo) / (hi - lo)) * (w as f64 - 1.0)).round().clamp(0.0, w as f64 - 1.0) as usize };
    let mut cells: Vec<(char, bool)> = vec![(' ', false); w];
    let t = f(fig, "threshold");
    if let Some(t) = t {
        let tol = f(fig, "tol").unwrap_or(0.0);
        let span: Vec<usize> = match s(fig, "side").as_str() {
            "ge" | "gt" => (x(t)..w).collect(),
            "le" | "lt" => (0..=x(t)).collect(),
            _ => (x(t - tol)..=x(t + tol)).collect(),
        };
        for i in span {
            cells[i] = ('░', true);
        }
    }
    match (f(fig, "est"), f(fig, "lo"), f(fig, "hi")) {
        (Some(e), Some(a), Some(b)) if b > a => {
            let (a, b) = (x(a), x(b));
            for c in cells.iter_mut().take(b + 1).skip(a) {
                *c = ('─', false);
            }
            cells[a] = ('├', false);
            cells[b] = ('┤', false);
            cells[x(e)] = ('●', false);
        }
        (Some(e), _, _) => cells[x(e)] = ('○', false),
        _ => {}
    }
    if let Some(t) = t {
        let at = x(t);
        if cells[at].0 == ' ' || cells[at].1 {
            cells[at] = ('┆', false);
        } else if cells[at].0 == '─' {
            // the check's line inside the range: where what passes starts stays in sight
            cells[at] = ('┼', false);
        }
    }
    let mut out = String::new();
    let mut zone = String::new();
    for (g, z) in cells {
        let g = d.text(&g.to_string());
        if z {
            zone.push_str(&g);
            continue;
        }
        if !zone.is_empty() {
            out += &d.paint(&zone, "2");
            zone.clear();
        }
        out.push_str(&g);
    }
    if !zone.is_empty() {
        out += &d.paint(&zone, "2");
    }
    out
}

/// The card as lines for a terminal: a head, then each field as its label and its value, wrapped to the width, never
/// a paragraph. `esc` draws someone's text as rb's screens draw it (escapes where a screen would hide a character).
pub fn lines(card: &Card, d: &Draw, esc: &dyn Fn(&str) -> String) -> Vec<String> {
    const LW: usize = 14;
    let w = d.width;
    let pad = " ".repeat(LW);
    let mut out: Vec<String> = Vec::new();
    for (i, fld) in card.fields.iter().enumerate() {
        let label = format!("{:<LW$}", fld.label);
        let label = if fld.label.is_empty() { pad.clone() } else { d.paint(&label, "2") };
        let mut value = esc(&d.text(&fld.said_with_command()));
        if fld.key == "statement" || fld.key == "title" {
            // the author's words, as written, under a bar
            let bar = d.text("┆ ");
            out.extend(wrap(&esc(&fld.value), w, &format!("{bar}"), &bar));
            continue;
        }
        if i == 0 {
            let head = format!("{} {}", fld.label, card.id);
            value = format!("{}  {}", d.paint(&head, "1"), value).trim_end().to_string();
            let mut pieces = vec![value];
            pieces.extend(fld.items.iter().map(|(t, _)| d.paint(&esc(&d.text(t)), "2")));
            out.extend(wrap_pieces(&pieces, w, "", "  "));
            if d.words && !fld.said.is_empty() {
                out.extend(wrap(&d.text(&fld.said), w, &format!("{pad}  "), &format!("{pad}  ")));
            }
            continue;
        }
        if fld.key == "lines" {
            if !value.is_empty() {
                out.extend(wrap(&value, w, &label, &pad));
            } else {
                out.push(label.trim_end().to_string());
            }
            for (t, o) in &fld.items {
                if o.is_empty() {
                    // a file's line, wrapped under its number, never cut
                    let t = esc(&d.text(t));
                    let head: String = t.chars().take(7).collect();
                    let body: String = t.chars().skip(7).collect();
                    let mut rows = wrap_chars(&body, w.saturating_sub(7).max(10));
                    let first = rows.remove(0);
                    out.push(format!("{head}{first}"));
                    out.extend(rows.into_iter().map(|r| format!("       {r}")));
                } else {
                    out.extend(wrap(&format!("{}: {}", d.text(t), o), w, "  ", "    ").into_iter().map(|l| d.paint(&l, "2")));
                }
            }
            continue;
        }
        if let Some(fig) = &fld.figure {
            let room = w.saturating_sub(LW + cols(&value) + 2);
            if room >= 12 {
                out.push(format!("{label}{}  {value}", bar(fig, room.min(48), d)));
            } else {
                // too narrow for both on one line: the number, then the figure under it
                out.extend(wrap(&value, w, &label, &pad));
                out.push(format!("{pad}{}", bar(fig, w.saturating_sub(LW).clamp(8, 48), d)));
            }
            if d.words && !fld.said.is_empty() {
                out.extend(wrap(&d.text(&fld.said), w, &format!("{pad}  "), &format!("{pad}  ")));
            }
            continue;
        }
        let mut pieces: Vec<String> = Vec::new();
        if !value.is_empty() {
            pieces.push(value);
        }
        for (t, o) in &fld.items {
            let t = esc(&d.text(t));
            // a list's tail names the command that shows the rest
            let t = if t.starts_with('+') && !o.is_empty() { format!("{t} ({o})") } else { t };
            pieces.push(if o.is_empty() { t } else { d.paint(&t, "4") });
        }
        if fld.key == "open" || fld.key == "confirm" {
            // each command whole on a line where the line is too narrow for both
            out.extend(wrap_pieces(&pieces, w, &label, &pad));
        } else if fld.key == "why" || fld.key == "words" || fld.key == "hypothesis" || fld.key == "purpose" || fld.key == "gate" || fld.key == "command" {
            out.extend(wrap(&pieces.join("  "), w, &label, &pad));
        } else if fld.items.is_empty() {
            out.extend(wrap(&pieces.join("  "), w, &label, &pad));
        } else {
            out.extend(wrap_pieces(&pieces, w, &label, &pad));
        }
        if d.words && !fld.said.is_empty() && fld.key != "open" {
            out.extend(wrap(&d.text(&fld.said), w, &format!("{pad}  "), &format!("{pad}  ")));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_every_form_the_cli_prints() {
        let p = |t: &str| parse(t).unwrap();
        assert_eq!(p("README.md:3"), Cite { path: "README.md".into(), line: 3, last: 3, ..Default::default() });
        assert_eq!(p("logs/agent.log:581-584"), Cite { path: "logs/agent.log".into(), line: 581, last: 584, ..Default::default() });
        assert_eq!(p("configs/data.yaml#normalize.mean"), Cite { path: "configs/data.yaml".into(), key: "normalize.mean".into(), ..Default::default() });
        assert_eq!(p("configs/sweep.yaml@32c91d6"), Cite { path: "configs/sweep.yaml".into(), commit: "32c91d6".into(), ..Default::default() });
        assert_eq!(p("configs/sweep.yaml#a.b@32c91d6"), Cite { path: "configs/sweep.yaml".into(), key: "a.b".into(), commit: "32c91d6".into(), ..Default::default() });
        assert_eq!(p("configs/sweep.yaml@32c91d6:4"), Cite { path: "configs/sweep.yaml".into(), line: 4, last: 4, commit: "32c91d6".into(), ..Default::default() });
        assert_eq!(p("results/r004_fp32_s0/"), Cite { path: "results/r004_fp32_s0".into(), folder: true, ..Default::default() });
        assert_eq!(parse("/etc/passwd"), None);
        assert_eq!(parse("../x"), None);
        assert_eq!(parse("https://x.org/a"), None);
        // rb's own files are records, never a file card that would show who wrote each
        assert_eq!(parse(".rb/log.jsonl"), None);
        assert_eq!(parse(".rb/experiments/e1/runs/r.yaml#receipt.actor"), None);
    }

    #[test]
    fn finds_a_key_path_in_yaml_json_and_toml() {
        let y = "data:\n  normalize:\n    mean: [0.5]\n    std: [0.5]\noptim:\n  lr: 0.1\n";
        assert_eq!(key_line(y, "data.normalize.std"), Some(4));
        assert_eq!(key_line(y, "optim.lr"), Some(6));
        assert_eq!(key_line(y, "optim.momentum"), None);
        let j = "{\n  \"optim\": {\n    \"lr\": 0.1\n  }\n}\n";
        assert_eq!(key_line(j, "optim.lr"), Some(3));
        let t = "[optim]\nlr = 0.1\n";
        assert_eq!(key_line(t, "optim.lr"), Some(2));
        // a part is found inside its parent's block only: x.b is not the top-level b
        let n = "x:\n  y: 1\nb: 2\n";
        assert_eq!(key_line(n, "x.b"), None);
        assert_eq!(key_line(n, "x.y"), Some(2));
        assert_eq!(key_line(n, "b"), Some(3));
    }

    #[test]
    fn a_long_list_ends_in_what_opens_the_rest() {
        let f = Field::new("runs", "RUNS", "").items((0..9).map(|i| (format!("run{i}"), format!("rb show run{i}"))).collect(), "rb show c1 --long");
        assert_eq!(f.items.len(), ITEMS + 1);
        assert_eq!(f.items.last().unwrap(), &("+3 more".to_string(), "rb show c1 --long".to_string()));
    }

    #[test]
    fn days_read_plainly() {
        assert_eq!(day("2026-10-14T01:53:55.000+00:00", false), "14 Oct 2026");
        assert_eq!(day("2026-10-04", true), "4 Oct");
        assert_eq!(day("garbage", true), "");
    }
}

/// Whether time `a` came before time `b`, each read with its own offset; as text where either does not read as a time.
fn before(a: &str, b: &str) -> bool {
    match (crate::pytime::parse_iso(a), crate::pytime::parse_iso(b)) {
        (Some(x), Some(y)) => x < y,
        _ => a < b,
    }
}
