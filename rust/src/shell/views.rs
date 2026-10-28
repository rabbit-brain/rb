//! The shell's views (the frame, the Overview, Research, a question, All criteria, Experiments, an experiment, Runs,
//! Resources, History, Your call), each drawn from the document alone (R1 to R4, R8 fill them).
//!
//! Three voices, by form: rb's lines plain, as the document gives them; author text verbatim in an element of its own
//! (`class="au"`), never with rb's words inside it; a session's text in rb's note form, "not confirmed". A link is drawn
//! only to an element the page holds: a target another group draws later (a card, a question's evidence) falls back to
//! the view that holds it.

use super::{at, count, esc, list, nodes, open, strings as w, text, Surface, SCRIPT};
use crate::value::Value;
use std::collections::HashSet;
use std::sync::OnceLock;

const SHELL_CSS: &str = include_str!("assets/shell.css");
const OPEN_CSS: &str = include_str!("assets/open.css");
const PRINT_CSS: &str = include_str!("assets/print.css");
/// The pointer guide's styles (A's): only where the script that draws the guide runs, so never on an export.
const GUIDE_CSS: &str = include_str!("assets/guide.css");

/// The whole page of `doc` on `surface`.
pub fn page(doc: &Value, surface: Surface) -> String {
    // what the cards draw first, then the views twice: once to learn every id they draw, once to link to them, so a link
    // is drawn only where an element is
    let cards = nodes::all(doc, surface);
    let open = open_parts(doc, surface);
    let mut p = Page { doc, surface, ids: HashSet::new(), card_ids: HashSet::new(), elsewhere: &open.elsewhere, most_late: Default::default(), past: past_version(doc, surface) };
    ids_in(&cards, &mut p.card_ids);
    // the views' own card, what else is open in the record (P12): drawn below with the views' ids, named here so a
    // link to it is drawn
    if p.open_items_body().0 > 0 {
        p.card_ids.insert("open-items".to_string());
    }
    p.ids = p.card_ids.clone();
    let words = glossary();
    ids_in(&words, &mut p.ids);
    let mut parts = p.views();
    let mut ids = p.ids.clone();
    for part in &parts {
        ids_in(part, &mut ids);
    }
    p.ids = ids;
    parts = p.views();
    let [research, experiments, record] = parts;
    let overview = p.overview();
    let calls = p.your_call();
    let cards = format!("{cards}{}", p.open_items_card());
    let title = text(doc, "project.title");
    let script = match surface {
        Surface::Page | Surface::Open if !SCRIPT.trim().is_empty() => format!("<script>{SCRIPT}</script>"),
        _ => String::new(),
    };
    format!(
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">\
         <title>{}</title><link rel=\"icon\" href=\"{}\"><style>{}</style>{script}</head><body>\
         <a class=\"skip\" href=\"#main\">{}</a>{}{}{}<main id=\"main\" tabindex=\"-1\">{}\
         <div class=\"sec\" id=\"sec-overview\">{overview}</div>\
         <div class=\"sec\" id=\"sec-research\"><p class=\"sechd\">{}</p>{research}</div>\
         <div class=\"sec\" id=\"sec-experiments\"><p class=\"sechd\">{}</p>{experiments}</div>\
         <div class=\"sec\" id=\"record\"><p class=\"sechd\">{}</p>{record}</div>\
         <div class=\"sec\" id=\"sec-calls\">{calls}</div>\
         <div class=\"nodes\">{cards}</div><div class=\"inone\" id=\"insp-none\" hidden><p class=\"dim\">{}</p></div>{}</main>{}</body></html>",
        esc(title),
        crate::page_assets::FAVICON,
        styles(surface),
        w::SKIP,
        top_bar(doc, surface, &open.top, &p.rerun_button("rrbar")),
        nav(doc, surface),
        open.scope,
        open.past,
        w::RESEARCH,
        w::EXPERIMENTS,
        w::RECORD,
        w::NOTHING_PICKED,
        about(doc, surface, &words),
        p.rerun_panel(),
    )
}

/// The glossary, folded: each term the page draws with a hint, and rb's line for it at its anchor, which the term names
/// as what describes it.
fn glossary() -> String {
    let rows: String = w::HINTS.iter().map(|(t, line)| format!("<dt>{}</dt><dd id=\"{}\">{}</dd>", esc(t), w::glossary_id(t), esc(line))).collect();
    // in the About view, its fold open: the view is where the words are read (design pass 1, P10)
    format!("<details class=\"fold words\" data-fold=\"words\" open><summary>{}</summary><div id=\"words\"><dl class=\"gloss\">{rows}</dl></div></details>", w::WORDS)
}

/// rb open's parts of the frame (A's open.rs): the top bar's pickers, the scope bar, what a past version withholds and
/// the Overview's Elsewhere.
#[derive(Default)]
struct OpenParts {
    top: String,
    scope: String,
    past: String,
    elsewhere: String,
}

/// rb open's parts of the frame, on rb open only: a page (rb page, rb export) names no place, whatever it is given.
fn open_parts(doc: &Value, surface: Surface) -> OpenParts {
    if surface != Surface::Open {
        return OpenParts::default();
    }
    OpenParts {
        top: hook("top_bar", open::top_bar, doc),
        scope: hook("scope_bar", open::scope_bar, doc),
        past: hook("past_version", open::past_version, doc),
        elsewhere: hook("elsewhere", open::elsewhere, doc),
    }
}

#[cfg(not(test))]
fn hook(_name: &str, draw: fn(&Value) -> String, doc: &Value) -> String {
    draw(doc)
}

#[cfg(test)]
thread_local! {
    /// A test's seam: while set, each of A's hooks draws a marker naming itself, so a test sees where its output lands
    /// while A's stubs draw nothing.
    static MARK_HOOKS: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

#[cfg(test)]
fn hook(name: &str, draw: fn(&Value) -> String, doc: &Value) -> String {
    if MARK_HOOKS.with(|m| m.get()) {
        format!("<i>HOOK-MARK:{name}</i>")
    } else {
        draw(doc)
    }
}

/// The page's stylesheets, rb open's after the shell's, the pointer guide's where its script runs (rb page and rb
/// open, never an export) and print's last, each without its comments and indentation, made once.
fn styles(surface: Surface) -> String {
    static SHELL: OnceLock<String> = OnceLock::new();
    static OPEN: OnceLock<String> = OnceLock::new();
    static GUIDE: OnceLock<String> = OnceLock::new();
    static PRINT: OnceLock<String> = OnceLock::new();
    let open = if surface == Surface::Open { OPEN.get_or_init(|| squeeze(OPEN_CSS)).as_str() } else { "" };
    let guide = if matches!(surface, Surface::Page | Surface::Open) { GUIDE.get_or_init(|| squeeze(GUIDE_CSS)).as_str() } else { "" };
    format!("{}{open}{guide}{}", SHELL.get_or_init(|| squeeze(SHELL_CSS)), PRINT.get_or_init(|| squeeze(PRINT_CSS)))
}

fn squeeze(css: &str) -> String {
    let mut out = String::with_capacity(css.len());
    let mut rest = css;
    while let Some(i) = rest.find("/*") {
        out.push_str(&rest[..i]);
        rest = rest[i..].find("*/").map_or("", |j| &rest[i + j + 2..]);
    }
    out.push_str(rest);
    tight(&out)
}

/// CSS with no whitespace a browser does not read: none at either end of a run of it next to `{`, `}`, `;`, `,` or
/// `>`, or after a `:`, and no `;` before a `}`. A run between two words (a descendant selector, `and (`, `1px solid`)
/// stays one space, and what a quoted string holds stays as written.
fn tight(css: &str) -> String {
    let mut out = String::with_capacity(css.len());
    let mut quote: Option<char> = None;
    let mut chars = css.chars().peekable();
    while let Some(c) = chars.next() {
        if let Some(q) = quote {
            out.push(c);
            if c == '\\' {
                out.extend(chars.next());
            } else if c == q {
                quote = None;
            }
        } else if c.is_whitespace() {
            while chars.next_if(|n| n.is_whitespace()).is_some() {}
            let before = out.chars().last().map_or(true, |p| ":;{},>".contains(p));
            if !before && !chars.peek().map_or(true, |n| "{};,>".contains(*n)) {
                out.push(' ');
            }
        } else if c == '}' && out.ends_with(';') {
            out.pop();
            out.push(c);
        } else {
            if c == '"' || c == '\'' {
                quote = Some(c);
            }
            out.push(c);
        }
    }
    out
}

/// Every `id` an HTML fragment draws, as written in it.
fn ids_in(html: &str, into: &mut HashSet<String>) {
    let mut rest = html;
    while let Some(i) = rest.find(" id=\"") {
        rest = &rest[i + 5..];
        if let Some(j) = rest.find('"') {
            into.insert(rest[..j].to_string());
            rest = &rest[j..];
        }
    }
}

/// A link inside the page; `inner` is HTML already.
fn a(target: &str, inner: &str, class: &str) -> String {
    let class = if class.is_empty() { String::new() } else { format!(" class=\"{class}\"") };
    format!("<a{class} href=\"#{}\">{inner}</a>", esc(target))
}

/// One object a line may name: the words it may be said by, the first as it is said apart from it; where it links;
/// the link's class.
type Form = (Vec<String>, String, &'static str);

/// `line` with each form linked at its first whole-word mention (longer words first, so a short id never takes part
/// of a run's name), one link to each place; with the forms the line does not say, in the order given.
fn linked_forms<'f>(line: &str, forms: &'f [Form]) -> (String, Vec<&'f Form>) {
    let mut order: Vec<(&String, &'f Form)> = Vec::new();
    let mut places: Vec<&String> = Vec::new();
    for f in forms.iter().filter(|f| !f.0.is_empty()) {
        if !places.contains(&&f.1) {
            places.push(&f.1);
            order.extend(f.0.iter().filter(|n| !n.is_empty()).map(|n| (n, f)));
        }
    }
    order.sort_by_key(|(n, _)| std::cmp::Reverse(n.len()));
    let mut spans: Vec<(usize, usize, &'f Form)> = Vec::new();
    for (n, f) in order {
        if spans.iter().any(|(_, _, g)| std::ptr::eq(*g, f)) {
            continue;
        }
        let mut from = 0;
        while let Some(i) = line[from..].find(n.as_str()).map(|i| i + from) {
            let end = i + n.len();
            if !joins(line[..i].chars().rev()) && !joins(line[end..].chars()) && !spans.iter().any(|(s, e, _)| i < *e && end > *s) {
                spans.push((i, end, f));
                break;
            }
            from = end;
        }
    }
    let unsaid: Vec<&'f Form> = forms
        .iter()
        .enumerate()
        .filter(|(k, f)| !f.0.is_empty() && !forms[..*k].iter().any(|g| g.1 == f.1) && !spans.iter().any(|(_, _, g)| g.1 == f.1))
        .map(|(_, f)| f)
        .collect();
    spans.sort_by_key(|(s, _, _)| *s);
    let mut out = String::new();
    let mut at_ = 0;
    for (s, e, f) in spans {
        out.push_str(&esc(&line[at_..s]));
        out.push_str(&a(&f.1, &esc(&line[s..e]), f.2));
        at_ = e;
    }
    out.push_str(&esc(&line[at_..]));
    (out, unsaid)
}

/// Whether the characters next to a word, read away from it, join it to more of an id: an id character, or a run of
/// "." with one after it (ids may hold a ".", so e1 is never linked inside e1.ckpt; a sentence's own "." ends it).
fn joins(mut next: impl Iterator<Item = char>) -> bool {
    let part = |c: char| c.is_alphanumeric() || c == '-' || c == '_';
    loop {
        match next.next() {
            Some('.') => continue,
            c => return c.is_some_and(part),
        }
    }
}

/// Author text, verbatim, in an element of its own.
fn au(s: &str) -> String {
    format!("<span class=\"au\">{}</span>", esc(s))
}

/// A title the author wrote (a project's, a question's, an experiment's), as `au` draws it, each hyphenated word kept on
/// one line, so a narrow screen never breaks "ResNet-50" at its hyphen.
fn au_title(s: &str) -> String {
    format!("<span class=\"au\">{}</span>", whole_words(s))
}

/// A title cut to about 40 characters at a word's end, with an ellipsis where it was cut (plain words pass 4: the
/// breadcrumb and the inspector's head name titles, not ids).
fn cut_title(s: &str) -> String {
    let s = s.trim();
    if s.chars().count() <= 44 {
        return s.to_string();
    }
    let head: String = s.chars().take(40).collect();
    let head = match head.rfind(' ') {
        Some(i) if i >= 20 => &head[..i],
        _ => head.as_str(),
    };
    format!("{}\u{2026}", head.trim_end_matches(|c: char| c == ',' || c == ';' || c == ':' || c.is_whitespace()))
}

/// A breadcrumb's part: the author's title, cut, then its id, dim.
fn crumb_title(title: &str, id: &str) -> String {
    if title.trim().is_empty() {
        return esc(id);
    }
    format!("<span class=\"au\">{}</span> <span class=\"cid\">{}</span>", esc(&cut_title(title)), esc(id))
}

/// Text escaped, each word joined by a hyphen (`ResNet-50`, `per-channel`) kept on one line, so a line breaks between
/// words and never at a word's own hyphen; a word too long for a narrow line is left to break.
fn whole_words(s: &str) -> String {
    s.split(' ')
        .map(|t| {
            let b = t.as_bytes();
            let joined = t.len() <= 24 && (1..b.len().saturating_sub(1)).any(|i| b[i] == b'-' && b[i - 1].is_ascii_alphanumeric() && b[i + 1].is_ascii_alphanumeric());
            if joined { format!("<span class=\"nw\">{}</span>", esc(t)) } else { esc(t) }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// A command to copy: selected whole by one click without script; the script adds its button at `data-copybtn`.
fn cmdbox(cmd: &str) -> String {
    format!("<div class=\"cmdbox\" data-copybtn><pre class=\"cmd\">{}</pre></div>", esc(cmd))
}

/// The words each viewer of the export reads (OWNER-READER). The hub names the viewer with its rb-viewer meta: owner
/// on the owner's own record address, reader on a share link; with none (an export opened from disk) the export's own
/// words stand. Each wording is drawn once, as `tag` with `attrs`, marked with the viewers it is for (`data-vw`), and
/// shell.css shows only the viewer's; "" draws nothing for that viewer. One wording for all three is drawn unmarked.
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

/// A line of rb's as the reader of a shared page reads it: its parts (" · " apart) that hand over a command, and a
/// closing ": rb ..." clause, left out, since the reader's view shows no command (OWNER-READER); a design's standing
/// said as a sentence. "" when nothing is left.
fn reader_line(line: &str) -> String {
    let parts: Vec<&str> = line
        .split(" \u{b7} ")
        .map(|p| p.split_once(": rb ").map_or(p, |(head, _)| head))
        .filter(|p| !p.trim().is_empty() && !p.trim_start().starts_with("rb ") && !p.starts_with("list it to confirm"))
        .collect();
    let said = parts.join(" \u{b7} ");
    if said == "design not confirmed" {
        return w::READER_DESIGN_NOT_CONFIRMED.to_string();
    }
    if !said.is_empty() && line.contains(": rb ") && !said.ends_with('.') && line.ends_with('.') {
        return format!("{said}.");
    }
    said
}

/// Reproduce's readiness in one plain sentence (design pass 1, target 6), rb's ready line on its hover: "" where rb gives
/// none.
fn ready_said(r: Option<&Value>) -> String {
    let Some(r) = r.filter(|r| r.is_dict()) else { return String::new() };
    let rows = list(r, "ready");
    let line = text(r, "ready_line");
    if rows.is_empty() {
        return if line.is_empty() { String::new() } else { esc(line) };
    }
    let ok = rows.iter().filter(|x| text(x, "state") == "ok").count();
    format!("<span title=\"{}\">{}</span>", esc(line), esc(&w::rerun_ready(ok, rows.len())))
}

/// An experiment's header line (design pass 1, target 6): rb's line led by the word "Experiment", with "not locked" left
/// out, since most experiments are not (P6); any other line as rb gives it.
fn who_said(who: &str) -> String {
    match who.strip_prefix("experiment \u{b7} ") {
        Some(rest) => {
            let parts: Vec<&str> = rest.split(" \u{b7} ").filter(|p| *p != "not locked").collect();
            format!("Experiment \u{b7} {}", parts.join(" \u{b7} "))
        }
        None => who.to_string(),
    }
}

/// What waits "for a confirmation" in the owner's voice: "for your confirmation" (fix 2).
fn owner_voice(html: &str) -> String {
    html.replace("for a confirmation", "for your confirmation")
}

/// "a", "a and b", "a, b and c".
fn and_said(parts: &[String]) -> String {
    match parts {
        [] => String::new(),
        [one] => one.clone(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}

fn plural(n: i64, one: &str, many: &str) -> String {
    format!("{n} {}", if n == 1 { one } else { many })
}

/// A number and its tolerance never split across lines ("0.7613 ± 0.001").
fn nb(s: &str) -> String {
    s.replace(" \u{b1} ", "\u{a0}\u{b1}\u{a0}")
}

/// A term with rb's line for it, shown on hover and focus with or without script, and described by its glossary entry.
fn term(word: &str, inner: &str) -> String {
    term_as(word, inner)
}

/// `inner` as the glossary's term `key` ("open (question)" for a question's status).
fn term_as(key: &str, inner: &str) -> String {
    match w::hint(key) {
        Some(line) => format!("<span class=\"term\" data-hint=\"{}\" aria-describedby=\"{}\">{inner}</span>", esc(line), w::glossary_id(key)),
        None => inner.to_string(),
    }
}
/// One standing word in its colour; with no colour, plain. Every piece of a line sits in an element of its own, so the
/// line reads in order to anything that reads elements.
fn word(part: &str, muted: &[String]) -> String {
    let said = said_plain(part);
    match crate::surface::part_class(part, muted).as_str() {
        "dim" => format!("<span>{said}</span>"),
        cls => format!("<span class=\"sw {cls}\">{said}</span>"),
    }
}

/// rb's line that leads with its rule word ("criterion met: estimate -0.0029 at least -0.01"), its lead in the screens'
/// plain words ("rule met: ..."), the rest as rb gives it.
fn said_lead(line: &str) -> String {
    match line.split_once(": ") {
        Some((lead, rest)) if w::plain_word(lead) != lead => format!("{}: {}", esc(&w::plain_word(lead)), esc(rest)),
        _ => esc(line),
    }
}

/// rb's phrase in the screens' plain words (w::plain_word), escaped, with the glossary's line for it where it has one
/// ("can't tell yet", "not tested"); its count outside the hover.
fn said_plain(phrase: &str) -> String {
    let said = w::plain_word(phrase);
    let (n, key) = match said.split_once(' ') {
        Some((n, rest)) if n.chars().all(|c| c.is_ascii_digit()) => (format!("{n} "), rest.to_string()),
        _ => (String::new(), said.clone()),
    };
    if w::hint(&key).is_some() && key != "rule" { format!("{}{}", esc(&n), term(&key, &esc(&key))) } else { esc(&said) }
}

/// rb's separator between the parts of a line, as an element of its own.
const DOT: &str = "<span> \u{b7} </span>";
/// The same, in text a sentence or an attribute holds.
const DOT_TEXT: &str = " \u{b7} ";
/// The dot that leads a cell's count ("13.07 · 3 seeds").
const DOT_TEXT_TRIM: &str = "\u{b7} ";

/// rb's lead before a question's tally (SHELL-SPEC section 12).
const LINKED_HERE: &str = "linked here: ";

fn texts(v: &Value, path: &str) -> Vec<String> {
    list(v, path).iter().filter_map(Value::as_str).map(str::to_string).collect()
}

/// The top bar: rb's mark, the project, when the document was made; rb open's pickers (A's, drawn on rb open only); on a
/// surface with the script, the theme and keyboard controls it drives.
/// The theme control's three icons, the one for the theme in force shown: a half-filled circle for the system's, a sun
/// for light, a moon for dark. Lines and fills in the text's colour, with no text inside the SVG (the hub's gate
/// refuses raw text there): the button's label names the theme.
const THEME_ICONS: &str = "<svg class=\"th th-sys\" viewBox=\"0 0 16 16\" aria-hidden=\"true\"><circle cx=\"8\" cy=\"8\" r=\"6\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\"/><path d=\"M8 2a6 6 0 0 1 0 12z\" fill=\"currentColor\"/></svg>\
<svg class=\"th th-light\" viewBox=\"0 0 16 16\" aria-hidden=\"true\"><circle cx=\"8\" cy=\"8\" r=\"3\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\"/><path d=\"M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3\" stroke=\"currentColor\" stroke-width=\"1.5\" stroke-linecap=\"round\"/></svg>\
<svg class=\"th th-dark\" viewBox=\"0 0 16 16\" aria-hidden=\"true\"><path d=\"M13.5 10.2A6 6 0 0 1 5.8 2.5a6 6 0 1 0 7.7 7.7z\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\" stroke-linejoin=\"round\"/></svg>";

fn top_bar(doc: &Value, surface: Surface, pickers: &str, rerun: &str) -> String {
    // the export, which the hub serves under its own bar, keeps About and the controls in the navigator instead
    let end = if surface == Surface::Export { String::new() } else { controls(surface) };
    // one bar (plain words pass 4, one bar on the hub): rb's mark and the record's title, then the view's name, which the
    // script writes in .vcrumb; the time small, as the snapshot's. The record's id, rb's version and a fork's parent are
    // About's, never the bar's
    format!(
        "<header class=\"top\" id=\"top\" data-swap><span class=\"rbwm\">rb<span class=\"rbdot\">.</span></span><span class=\"ttl\">{}</span>\
         <span class=\"vcrumb\"></span>{pickers}<span class=\"end\">{rerun}<span class=\"stamp\">{}</span>{end}</span></header>",
        au_title(text(doc, "project.title")),
        esc(text(doc, "at")),
    )
}

/// About and, where the page carries the script, the theme and keyboard controls it drives: in the top bar on rb page and
/// rb open; on the export, which runs no script of its own, About alone, in the navigator (the hub hides the page's own
/// bar under its, plain words pass 4), where the hub's copy of the script adds its controls beside it.
fn controls(surface: Surface) -> String {
    let ctl = format!(
        "<button type=\"button\" class=\"ctl ico\" data-theme aria-label=\"{t}\" title=\"{t}\">{THEME_ICONS}</button><button type=\"button\" class=\"ctl\" data-keys aria-label=\"{}\">?</button>",
        w::KEYBOARD,
        t = w::THEME
    );
    let about = format!("<a class=\"ctl abt\" href=\"#about\">{}</a>", w::ABOUT);
    match surface {
        Surface::Export => format!("<div class=\"navctl\">{about}</div>"),
        _ => format!("{about}{ctl}"),
    }
}

/// About's line on the record itself: its id, rb's version, and the project it was cloned from, when that is an address.
fn record_line(doc: &Value) -> String {
    let parent = text(doc, "project.parent");
    let fork = if parent.starts_with("https://") || parent.starts_with("http://") {
        format!(" \u{b7} {}<a href=\"{}\">{}</a>", w::A_FORK_OF, esc(parent), esc(parent))
    } else {
        String::new()
    };
    format!(
        "<p class=\"recid dim\">{} <span class=\"mono\">{}</span> \u{b7} rb {}{fork}</p>",
        w::RESEARCH_RECORD_CAP,
        esc(text(doc, "project.id")),
        esc(text(doc, "rb_version")),
    )
}

/// The nav: Overview · Research (the questions whole, All criteria) · Experiments (each by id and short title) ·
/// Record (Runs, Resources, History), with Your call (N) at the foot. Counts are rb's; absent, none is drawn.
fn nav(doc: &Value, surface: Surface) -> String {
    let n = |key: &str| count(doc, &format!("home.counts.{key}")).map(|n| format!(" <span class=\"n\">{n}</span>")).unwrap_or_default();
    let qs: String = list(doc, "questions")
        .iter()
        .map(|q| {
            let deep = count(q, "depth").unwrap_or(0) > 0;
            format!(
                "<li><a{} href=\"#q-{}\"><span class=\"tx q\">{}{}</span></a></li>",
                if deep { " class=\"d1\"" } else { "" },
                esc(text(q, "id")),
                if deep { "\u{21b3} " } else { "" },
                au_title(text(q, "text"))
            )
        })
        .collect();
    let xs: String = list(doc, "experiments")
        .iter()
        .map(|x| {
            let short = Some(text(x, "short_title")).filter(|s| !s.is_empty()).unwrap_or(text(x, "title"));
            format!(
                // the title first and the id last, small (design pass 1, P3)
                "<li><a href=\"#x-{id}\" title=\"{}\"><span class=\"tx x\">{} <span class=\"mono\">{id}</span></span></a></li>",
                esc(text(x, "title")),
                au_title(short),
                id = esc(text(x, "id"))
            )
        })
        .collect();
    // Your call's badge counts the user's calls alone (your_call.n): a proposed or past call and a design that waits
    // for a confirmation are listed apart, never under Your call (AGENTS.md, "Whose call it is"), so the Overview's
    // queue, which holds them too, may count more
    let yc = count(doc, "your_call.n").map(|n| format!(" <span class=\"n\">{n}</span>")).unwrap_or_default();
    let foot = if surface == Surface::Export { controls(surface) } else { String::new() };
    format!(
        "<nav class=\"nav\" id=\"nav\" aria-label=\"Research record\"><ul><li><a href=\"#overview\">{}</a></li>\
         <li><a href=\"#research\">{}{}</a><ul class=\"sub\">{qs}<li><a href=\"#criteria\"><span class=\"tx\">{}</span>{}</a></li></ul></li>\
         <li><a href=\"#experiments\">{}{}</a><ul class=\"sub\">{xs}</ul></li>\
         <li><span class=\"grp\">{}</span><ul class=\"rec\"><li><a href=\"#runs\">{}{}</a></li><li><a href=\"#resources\">{}{}</a></li><li><a href=\"#history\">{}</a></li></ul></li>\
         <li class=\"yc\"><a href=\"#your-call\">{}{yc}</a></li></ul>{foot}</nav>",
        w::OVERVIEW,
        w::RESEARCH,
        n("questions"),
        w::ALL_CRITERIA,
        n("criteria"),
        w::EXPERIMENTS,
        n("experiments"),
        w::RECORD,
        w::RUNS,
        n("runs"),
        w::RESOURCES,
        n("resources"),
        w::HISTORY,
        if surface == Surface::Export { by_viewer("span", "", w::OPEN_CALLS, w::YOUR_CALL, w::OPEN_CALLS) } else { w::YOUR_CALL.to_string() },
    )
}

/// The footer: the honesty line, once; what made the page and when; how to read the record.
fn footer(doc: &Value, surface: Surface) -> String {
    let made = match surface {
        Surface::Open => format!("<span>{}</span><span>{}</span>", w::OPEN_MADE, w::OPEN_WHERE),
        _ => format!("<span>{}{}</span>", w::PAGE_MADE, esc(text(doc, "at"))),
    };
    let computed = if surface == Surface::Open { String::new() } else { format!("<p>{}</p>", w::HOW_TO_READ_COMPUTED) };
    format!(
        "<footer class=\"foot\"><p class=\"honest1\">{} {}</p>{made}<details data-fold=\"how-to-read\"><summary>{}</summary><p>{}</p>{computed}</details></footer>",
        w::HONEST,
        w::RB_NEVER_DECIDES,
        w::HOW_TO_READ,
        w::HOW_TO_READ_AXES
    )
}

/// The About view (design pass 1, P10, target 11): what made the page, the honesty line, how to read the record and the
/// words it uses, once, reached from the top bar; never at the foot of every view. Glossary hovers on terms stay.
fn about(doc: &Value, surface: Surface, words: &str) -> String {
    let body = format!("<h1 id=\"h-about\">{}</h1>{}{}{words}", w::ABOUT_TITLE, record_line(doc), footer(doc, surface));
    format!("<div class=\"sec\" id=\"sec-about\">{}</div>", section("about", &with_project(doc, w::ABOUT), w::ABOUT, "insp-none", &body))
}

/// A view's section, with what the script reads: its title, its crumb and its rail (the inspector's root).
fn section(id: &str, title: &str, crumb: &str, root: &str, body: &str) -> String {
    format!(
        "<section class=\"view\" id=\"{id}\" data-title=\"{}\" data-crumb=\"{}\" data-root=\"{}\" aria-labelledby=\"h-{id}\">{body}</section>",
        esc(title),
        esc(crumb),
        esc(root)
    )
}

fn with_project(doc: &Value, what: &str) -> String {
    format!("{what} \u{b7} {}", text(doc, "project.title"))
}

/// Why rb asks for a call, first: its label, then its qualifier. Neutral ink: a call is not a standing.
fn reason(c: &Value) -> String {
    let detail: Vec<&str> = [text(c, "reason.detail"), text(c, "reason.also")].into_iter().filter(|s| !s.is_empty()).collect();
    // drawn verbatim (the contract's drawn field): its plain words are the model's (M)
    let detail = if detail.is_empty() { String::new() } else { format!("<span class=\"rd2\"> \u{b7} {}</span>", esc(&nb(&detail.join(" \u{b7} ")))) };
    format!("<span class=\"rsn\"><span class=\"rl\">{}</span>{detail}</span>", esc(text(c, "reason.label")))
}

/// A call in a rail, in two lines: why rb asks, then what it is about with its id last. `to` is where it links while
/// the page holds no card for it.
fn call_li(c: &Value, to: &str) -> String {
    let s = text(c, "subject");
    format!(
        "<a class=\"callrow\" href=\"#{}\" data-for=\"call-{}\">{}<span class=\"t\">{} <span class=\"mono\">{}</span></span></a>",
        esc(to),
        esc(s),
        reason(c),
        au(text(c, "title")),
        esc(s)
    )
}

struct Page<'a> {
    doc: &'a Value,
    surface: Surface,
    /// The ids the page holds outside the Overview and Your call.
    ids: HashSet<String>,
    /// The ids the cards draw (C's nodes.rs): where a card is drawn, a view does not draw its stand-in.
    card_ids: HashSet<String>,
    /// rb open's Elsewhere, empty on a page.
    elsewhere: &'a str,
    /// The tags every row of the evidence being drawn shares, said once in its heading and left out of each row
    /// (OVERVIEW-V2).
    most_late: std::cell::Cell<bool>,
    /// A past version, on rb open: it offers no command (guarantee 38), since a call copied there would be recorded on
    /// today's readings.
    past: bool,
}

/// Whether rb open draws a past version of the record (the place's version names a commit).
fn past_version(doc: &Value, surface: Surface) -> bool {
    surface == Surface::Open && !text(doc, "places.place.version.sha").is_empty()
}

/// One row of the Overview's queue: its kind, what it asks (a verb phrase, HTML), one line of numbers or names (HTML),
/// where its one action goes, the action's word, and what Your call draws for it at the Decide level.
struct QItem<'a> {
    kind: String,
    title: String,
    /// what stands, for a reader of a shared page, who is asked to do nothing (HTML; "" where the title says it)
    reader: String,
    detail: String,
    target: String,
    action: String,
    src: QSrc<'a>,
}

/// What a queue row stands for in the document.
enum QSrc<'a> {
    Call(&'a Value),
    Pending,
    Found,
    Uncited,
    None,
}

/// The views every page holds, which a link may name before the page's ids are known.
// to-confirm: the fold of what waits, drawn wherever a queue row names a design or a call waiting for a confirmation
const FIXED: [&str; 10] = ["overview", "research", "criteria", "experiments", "runs", "resources", "history", "your-call", "how-made", "to-confirm"];

/// What a waiting row's line is about, alone ("continue e2 (2026-05-07)", "investigate c6"): rb's line up to its first
/// part's quote or note, so a queue row carries no path, hash or command (OVERVIEW-V2: they sit one click down).
fn pending_said(line: &str) -> &str {
    let first = line.split(" \u{b7} ").next().unwrap_or("");
    first.split(": ").next().unwrap_or("").trim()
}

/// `s` with its first letter in upper case ("open" reads "Open" as a card's state word).
fn capitalised(s: &str) -> String {
    let mut c = s.chars();
    c.next().map(|f| f.to_uppercase().chain(c).collect()).unwrap_or_default()
}

/// A place as the Overview's header names it: its branch, the label's first part ("main" of "main · ~/p"); a detached
/// checkout is "detached", its commit left to the Where picker; a record outside git, with no branch or commit, none.
fn place_name(label: &str) -> &str {
    let name = label.split(" \u{b7} ").next().unwrap_or("").trim();
    match name.strip_prefix("detached at") {
        Some(commit) if commit.trim().is_empty() => "",
        Some(_) => "detached",
        None => name,
    }
}

/// A state as plain text for a label.
fn word_plain(s: &str) -> String {
    capitalised(s.split(' ').next().unwrap_or(""))
}

/// The counts rb gives as linked here ("linked here: 4 criteria met · 1 not reproduced; below it: ..."), each as its
/// rule word and number ("meets", 4), so a tally is drawn short and coloured as rb colours the whole phrase.
fn linked_counts(rollup: &str) -> Vec<(String, i64)> {
    let here = rollup.strip_prefix(LINKED_HERE).unwrap_or(if rollup.contains(':') { "" } else { rollup });
    let here = here.split("; ").next().unwrap_or("");
    here.split(" \u{b7} ")
        .filter_map(|part| {
            let (n, phrase) = part.trim().split_once(' ')?;
            let n: i64 = n.parse().ok()?;
            let phrase = phrase.strip_prefix("criteria ").or_else(|| phrase.strip_prefix("criterion ")).map_or(phrase.to_string(), |rest| match rest {
                "met" => "meets".to_string(),
                "not met" => "misses".to_string(),
                other => other.to_string(),
            });
            Some((phrase, n))
        })
        .collect()
}

/// Tallies as the document gives them, each a rule word `state` and its `n` (or a [word, n] pair), as pairs.
fn tally_pairs(l: &[Value]) -> Vec<(String, i64)> {
    l.iter()
        .filter_map(|t| match t.as_list() {
            Some(p) => Some((p.first()?.as_str()?.to_string(), super::whole(p.get(1)?)?)),
            None => Some((text(t, "state").to_string(), count(t, "n")?)),
        })
        .collect()
}
/// A finding's tag key as a quiet tag with rb's line for it ("retrospective" reads "rule written after the results").
fn tag_of(key: &str) -> String {
    let (hint, label) = match key {
        "criterion_untested" => ("not tested", "not tested"),
        "retrospective" => ("rule written after the results", "rule written after the results"),
        "uncertainty_remains" => ("Uncertainty remains", "Uncertainty remains"),
        other => (other, other),
    };
    format!(" {}", term_as(hint, &format!("<span class=\"tag\">{}</span>", esc(label))))
}

/// A rule word as a tally says it, short and plain: "met", "not met", "can't tell yet", "not tested"; any other ("not
/// reproduced", "gate missed") as rb gives it.
fn short_rule(word: &str) -> &str {
    match word {
        "meets" => "met",
        "misses" => "not met",
        "undetermined" => "can't tell yet",
        "untested" => "not tested",
        other => other,
    }
}

/// rb's tally of rule words (home.tally's [word, n] pairs) as the screens say it (plain words pass 3): "Rules: 4 met ·
/// 1 not reproduced · 4 can't tell yet", each word short and plain in its colour, what the counts count leading them.
fn rule_tally(pairs: &[Value], muted: &[String]) -> String {
    let words: Vec<String> = pairs
        .iter()
        .filter_map(|t| {
            let pair = t.as_list()?;
            let rule = pair.first()?.as_str()?;
            let n = super::whole(pair.get(1)?)?;
            let full = format!("{n} {}", crate::assess::rule_label(rule, n));
            let said = said_plain(&format!("{n} {}", short_rule(rule)));
            Some(match crate::surface::part_class(&full, muted).as_str() {
                "dim" => format!("<span>{said}</span>"),
                cls => format!("<span class=\"sw {cls}\">{said}</span>"),
            })
        })
        .collect();
    if words.is_empty() { String::new() } else { format!("<span class=\"k\">{}</span>{}", w::RULES_LEAD, words.join(DOT)) }
}

/// A number as a screen says it (design pass 1, P5): `x` to `places` decimals (two, see `places`); full precision stays
/// on hover and in Details, a sign where `signed`, never "-0.00".
fn dec(x: f64, places: usize, signed: bool) -> String {
    let s = format!("{x:.places$}");
    let zero = s.trim_start_matches('-').chars().all(|c| c == '0' || c == '.');
    let s = if zero { s.trim_start_matches('-').to_string() } else { s };
    if signed && x > 0.0 && !zero { format!("+{s}") } else { s }
}

/// A number as a finding's sentence says it (model's decp): `places` decimals, and a number under 0.01 its first two
/// significant figures, so it never reads as 0; never "-0".
fn own_digits(x: f64, places: usize, signed: bool) -> String {
    let a = x.abs();
    let own = if a == 0.0 || a >= 0.01 { 2 } else { (2 - a.log10().floor() as i64 - 1).clamp(2, 12) as usize };
    dec(x, own.max(places), signed)
}

/// How many decimals numbers of these sizes take on a screen: two (P5), or as many as show two significant digits of
/// the largest where all are under 0.1 on a scale with no percent (a top-1 of 0.7597 differs by 0.0029, never "0.00").
fn places(unit: &str, xs: &[f64]) -> usize {
    let m = xs.iter().map(|x| x.abs()).fold(0.0_f64, f64::max);
    if unit == "%" || m >= 0.1 || m == 0.0 || !m.is_finite() {
        return 2;
    }
    ((1.0 - m.log10().floor()) as usize).clamp(2, 6)
}

/// A unit a sentence can say after a number ("%", "ms", "KB"); a metric's own name, which rb gives where it has no unit,
/// is none.
fn said_unit(unit: &str) -> &str {
    match unit {
        "%" | "ms" | "s" | "us" | "KB" | "MB" | "GB" | "kb" | "mb" | "gb" | "nats" | "bits" | "points" | "x" => unit,
        _ => "",
    }
}

/// "3 seeds", "1 run": what a reading's units are, counted.
fn units_said(n: i64, unit: &str) -> String {
    let unit = if unit.is_empty() { "run" } else { unit };
    if n == 1 {
        format!("1 {unit}")
    } else if unit.ends_with("ss") || unit.ends_with('x') || unit.ends_with("ch") || unit.ends_with("sh") {
        format!("{n} {unit}es")
    } else if unit.ends_with('s') {
        format!("{n} {unit}")
    } else {
        format!("{n} {unit}s")
    }
}

/// rb's reading of a rule as one plain sentence (design pass 1, P5 and P8): "int8 is 0.08 points lower than the fp32
/// baseline, -0.23 to +0.07, 3 seeds."; a level "fp32 baseline measured 90.48%, 89.92 to 91.04, 3 seeds; the cited
/// number is 91.25%."; none "No results yet.". Drawn from the reading's own numbers (contract 1.6's `reading`); rb's
/// own form (the comparison with its full precision) stays in the row's Details. "" where the reading gives no number.
fn plain_reading(f: &Value) -> String {
    let r = at(f, "reading");
    let num = |k: &str| r.and_then(|r| at(r, k)).and_then(Value::as_f64);
    let Some(e) = num("estimate") else {
        return if text(f, "state") == "untested" || text(f, "reading.result") == "untested" { "No results yet.".to_string() } else { String::new() };
    };
    let given = text(f, "reading.unit_text");
    // the unit as rb says it in a sentence (unit_said: "points" for a difference of percentages, "%" for one), else
    // rb's own unit; a percentage by any name is "%", never a name (fix 2)
    let given = match text(f, "reading.unit_said") { "points" | "%" => "%", _ => given };
    let unit = said_unit(unit_word(given));
    // a metric with no unit is named once by its title, as the grid heads it ("on Top-1"), never its key (fix 3)
    let metric = if unit.is_empty() && !given.is_empty() { metric_label(given, "") } else { String::new() };
    let n = count(f, "reading.n").unwrap_or(0);
    let (lo, hi) = (num("lo"), num("hi"));
    let (subject, reference) = arms_said(f);
    let (subject, reference) = (subject.as_str(), reference.as_str());
    // a level where there is no reference, or the reference is the cited number itself (a cited level, "fp32 baseline
    // against cited"); a cited rule comparing two arms reads as a comparison, its threshold no "cited number"
    let cited = at(f, "cited").is_some_and(Value::is_dict) && (reference.is_empty() || reference == "cited" || reference.starts_with("cited "));
    let level = reference.is_empty() || cited;
    let target = if cited { at(f, "check_band.target").and_then(Value::as_f64) } else { None };
    // a ratio (a rule on ratio.<metric>) is said as rb's short reading says it, never as a difference
    let ratio = text(f, "rule").starts_with("ratio.");
    // a level's decimals tell its numbers apart (its interval's width, its distance from a cited number); a
    // difference's show two significant digits of the largest
    let p = if level {
        places(unit, &[lo.zip(hi).map(|(l, h)| h - l), target.map(|t| e - t)].into_iter().flatten().filter(|x| *x != 0.0).collect::<Vec<_>>())
    } else {
        places(unit, &[Some(e), lo, hi].into_iter().flatten().collect::<Vec<_>>())
    };
    // a level with nothing to tell it from keeps rb's own digits (a share of 0.994 against a rule of 0.99)
    let bare = level && lo.is_none() && target.is_none();
    let shown = |x: f64| if bare && x == e && !text(f, "reading.estimate_text").is_empty() { text(f, "reading.estimate_text").to_string() } else { dec(x, p, false) };
    let with_unit = |x: f64| if unit == "%" { format!("{}%", shown(x)) } else if unit.is_empty() { shown(x) } else { format!("{} {unit}", shown(x)) };
    let mut out = if ratio && !level {
        let the = the_of(reference);
        format!("{subject} against {the}{reference}: {} {}", if given.is_empty() { "ratio" } else { given }, dec(e, p, false))
    } else if level {
        // an arm is its number ("int8 is 75.70%", "fp32 baseline is 0.7597 on top1"); with no arm named, rb measured it
        let on = if metric.is_empty() { String::new() } else { format!(" on {metric}") };
        // with no arm named, the metric, by the title its row gives it ("ONNX top-1 is 75.38%", FIX5), never
        // "measured"; a row that gives none, the metric's own title
        let titled = text(f, "reading_short").split_once(" is ").map(|(t, _)| t.to_string()).filter(|t| !t.is_empty())
            .unwrap_or_else(|| { let m = text(f, "row.metric"); metric_label(m.rsplit('.').next().unwrap_or(m), "") });
        if subject.is_empty() && titled.is_empty() { format!("measured {}{on}", with_unit(e)) }
        else { format!("{} is {}{on}", if subject.is_empty() { titled.as_str() } else { subject }, with_unit(e)) }
    } else {
        let size = dec(e.abs(), p, false);
        let by = if unit == "%" { "points" } else { unit };
        let by = if by.is_empty() { String::new() } else { format!(" {by}") };
        let the = the_of(reference);
        let on = if metric.is_empty() { String::new() } else { format!(" on {metric}") };
        if e == 0.0 {
            format!("{subject} is level with {the}{reference}{on}")
        } else if size.trim_start_matches(['0', '.']).is_empty() {
            format!("{subject} is under {}{by} {} than {the}{reference}{on}", dec(10f64.powi(-(p as i32)), p, false), if e < 0.0 { "lower" } else { "higher" })
        } else {
            format!("{subject} is {size}{by} {} than {the}{reference}{on}", if e < 0.0 { "lower" } else { "higher" })
        }
    };
    // the interval in brackets after its number, as the cards say it (fix 3); a level's bounds keep its unit
    // ("13.07% (11.00% to 15.14%)"), a difference's are the points or units just said ("(+0.62 to +6.58)")
    if let (Some(lo), Some(hi)) = (lo, hi) {
        let bound = |x: f64| if level { with_unit(x) } else { dec(x, p, !ratio) };
        let interval = format!(" ({} to {})", bound(lo), bound(hi));
        // after the number, before what it is on: "0.7597 (0.7591 to 0.7603) on Top-1"
        let on = if metric.is_empty() { String::new() } else { format!(" on {metric}") };
        match out.strip_suffix(&on).filter(|_| !on.is_empty()) {
            Some(head) => out = format!("{head}{interval}{on}"),
            None => out.push_str(&interval),
        }
    }
    if n > 0 {
        out.push_str(&format!(", {}", units_said(n, text(f, "reading.unit"))));
    }
    if lo.is_none() && n > 0 && n < 3 && text(f, "state") == "undetermined" {
        out.push_str(", no interval yet (rb gives one from 3)");
    }
    // the cited number in the rule's own digits, as every view says it ("49.6%", never "49.60%")
    if let Some(t) = target {
        let digits = cited_digits(f, t);
        out.push_str(&format!("; the cited number is {}", if unit == "%" { format!("{digits}%") } else if unit.is_empty() { digits } else { format!("{digits} {unit}") }));
    }
    // a sentence led by a variant's own name keeps it as written ("int8 is ..."); rb's words are capitalised
    if !subject.is_empty() {
        return format!("{out}.");
    }
    let mut c = out.chars();
    let first: String = c.next().map(|x| x.to_uppercase().collect()).unwrap_or_default();
    format!("{first}{}.", c.as_str())
}

/// A cited number as its rule writes it: rb's rule text ("... = 49.6 ± 0.05"), else the number's shortest digits.
fn cited_digits(f: &Value, t: f64) -> String {
    let rule = text(f, "rule");
    let given = rule.rsplit_once(" = ").and_then(|(_, r)| r.split(" \u{b1}").next()).map(str::trim).filter(|d| d.parse::<f64>().is_ok_and(|x| x == t));
    given.map(str::to_string).unwrap_or_else(|| {
        let r = crate::pyfloat::repr(t);
        r.strip_suffix(".0").map(str::to_string).unwrap_or(r)
    })
}

/// A reading's arm label with its variant in plain words and its role as rb gives it: "fp32 baseline" reads "FP32
/// baseline", "step_by_step" "step by step".
fn arm_plain(label: &str) -> String {
    let (name, role) = [" baseline", " control", " ablation", " candidate"].iter().find_map(|r| label.strip_suffix(r).map(|n| (n, *r))).unwrap_or((label, ""));
    if name.is_empty() { label.to_string() } else { format!("{}{role}", plain_value(name, false)) }
}

/// What goes before an arm named after "than" or "against": "the" before a role's label ("the fp32 baseline"), nothing
/// before a variant's own name ("than int8"), which reads as a name.
fn the_of(reference: &str) -> &'static str {
    let role = [" baseline", " control", " ablation"].iter().any(|r| reference.ends_with(r));
    if role && !reference.starts_with("the ") { "the " } else { "" }
}

/// The two arms a reading compares, as a sentence names them: their labels, and where a variant is compared with
/// itself, the settings each side is selected by ("wide at loops 8", "wide at loops 6"), read from the rule rb words
/// ("[...; subject where loops=8; reference where loops=6]"), so the two sides never read as one.
fn arms_said(f: &Value) -> (String, String) {
    // a variant by its plain name, as the cards and the grid say it ("step by step", "the FP32 baseline"; fix 3)
    let (subject, reference) = (arm_plain(text(f, "subject_label")), arm_plain(text(f, "reference_label")));
    if subject.is_empty() || subject != reference {
        return (subject, reference);
    }
    let rule = text(f, "rule");
    // rb's scope is the bracket after the arms, up to the rule's last "]" (a quoted value may hold one)
    let scope = rule.find(" [").and_then(|i| rule.rfind(']').filter(|j| *j > i).map(|j| &rule[i + 2..j])).unwrap_or("");
    let side = |label: &str, bare: bool| {
        scope.split("; ").find_map(|part| part.strip_prefix(label)).map(|sel| {
            sel.split(", ").map(|kv| { let kv = kv.replacen('=', " ", 1); if bare { kv.replace('"', "") } else { kv } }).collect::<Vec<_>>().join(" and ")
        })
    };
    // a text value's quotes are dropped unless they are what tells the two sides apart ("1" and 1)
    let sides = [true, false].into_iter().map(|bare| (side("subject where ", bare), side("reference where ", bare))).find(|(s, r)| s.is_some() && s != r);
    match sides {
        Some((Some(s), Some(r))) => (format!("{subject} at {s}"), format!("{reference} at {r}")),
        Some((Some(s), None)) => (format!("{subject} at {s}"), reference),
        _ => (subject, reference),
    }
}

/// A setting's key as a heading says it (design pass 1, target 5): "calib_images" reads "Calib images"; a dotted key by
/// its own last name ("data.batch_size" reads "Batch size"), never its section as a capitalised word.
fn plain_key(k: &str) -> String {
    plain_words(k.rsplit('.').next().filter(|t| !t.is_empty()).unwrap_or(k))
}

/// A record's name in words, whole: its dots and underscores as spaces, capitalised ("eval.top1" reads "Eval top1").
fn plain_words(k: &str) -> String {
    capitalised(&k.replace(['.', '_'], " "))
}

/// A setting's key in words among the others a screen shows with it: by its own last name, unless another of them has
/// that name too ("model.lr" and "optim.lr"), when each is said whole, so two settings never read as one.
fn key_words(k: &str, keys: &[String]) -> String {
    let last = |x: &str| x.rsplit('.').next().unwrap_or(x).to_string();
    if keys.iter().any(|o| o != k && last(o) == last(k)) { plain_words(k) } else { plain_key(k) }
}

/// A setting's value in plain words (PIVOT): a numeric format (fp32, int8) in capitals, else its underscores as spaces,
/// capitalised where it leads (`lead`): "Step by step" heads a row, "step by step" sits in a sentence.
fn plain_value(v: &str, lead: bool) -> String {
    let arm = arm_label(v);
    if arm != v {
        return arm;
    }
    let v = v.replace('_', " ");
    if lead { capitalised(&v) } else { v }
}

/// A setting under a column, in plain words: its value alone where the value reads alone ("Answer tag"); a count by its
/// one-word name ("250 problems"); any other number or flag with its key ("Calib images: 128").
fn setting_said(k: &str, v: &str, keys: &[String]) -> String {
    let bare = v.parse::<f64>().is_ok() || matches!(v, "true" | "false" | "null");
    if !bare {
        return plain_value(v, true);
    }
    if !k.contains(['_', '.']) && k.len() > 2 && k.ends_with('s') && v.parse::<f64>().is_ok() {
        return format!("{v} {k}");
    }
    format!("{}: {v}", key_words(k, keys))
}

/// Every setting key a comparison's rows name (the conditions, what a variant holds apart, what it varies), so a
/// heading can tell two of the same last name apart.
fn table_keys(x: &Value) -> Vec<String> {
    let mut keys: Vec<String> = Vec::new();
    for r in list(x, "table.rows") {
        for k in ["where", "unvaried", "varies"] {
            if let Some(Value::Dict(m)) = at(r, k) {
                keys.extend(m.keys().map(|k| k.to_string()).filter(|k| !keys.contains(k)).collect::<Vec<_>>());
            }
        }
    }
    keys
}

/// A unit as a sentence says it: a percentage, by any of the names a catalogue gives it ("percent", "pct"), is "%".
fn unit_word(unit: &str) -> &str {
    if matches!(unit.trim().to_ascii_lowercase().as_str(), "%" | "percent" | "per cent" | "pct" | "percentage" | "percentages") { "%" } else { unit }
}

/// A metric's title over its grid: rb's label where it gives one, else its name in plain words and its unit ("Accuracy
/// (%)").
fn metric_title(m: &Value) -> String {
    let label = text(m, "label");
    if !label.is_empty() {
        return label.to_string();
    }
    // one title per metric on a page (fix 2): the grid's and the runs table's are the same words
    metric_label(text(m, "name"), text(m, "unit"))
}

/// A variant's name as a column says it: a numeric format (fp32, int8, bf16) in capitals as it is written in prose;
/// any other name as recorded.
fn arm_label(v: &str) -> String {
    let (head, digits) = v.split_at(v.find(|c: char| c.is_ascii_digit()).unwrap_or(v.len()));
    if ["fp", "int", "uint", "bf", "tf"].contains(&head) && !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit()) { v.to_uppercase() } else { v.to_string() }
}

/// A metric's key as a heading says it (design pass 1, target 6): "top1" reads "Top-1 (%)", "size_kb" "Size (KB)";
/// never the key in capitals.
fn metric_label(name: &str, unit: &str) -> String {
    // a unit that is the metric's own name is none; a percentage by any name is "%"
    let unit = if unit == name { "" } else { unit_word(unit) };
    // a name that ends in its unit says it once ("latency_ms" in ms is "Latency (ms)")
    let name = name.strip_suffix(&format!("_{unit}")).filter(|n| !n.is_empty() && !unit.is_empty()).unwrap_or(name);
    let (base, unit) = match name.rsplit_once('_') {
        Some((b, u)) if ["kb", "mb", "gb", "ms"].contains(&u) && (unit.is_empty() || unit.eq_ignore_ascii_case(u)) => (b.to_string(), if u == "ms" { u.to_string() } else { u.to_uppercase() }),
        _ => (name.to_string(), unit.to_string()),
    };
    // "top1" is "top-1" wherever it stands in the name, as prose writes it; an acronym (C's list) and a code of letters
    // then digits (fp32, int8) keep their capitals, as nodes.rs's metric_said says them: "onnx_top1" reads "ONNX top-1"
    let topn = |t: &str| t.strip_prefix("top").filter(|d| !d.is_empty() && d.chars().all(|c| c.is_ascii_digit())).map(|d| format!("top-{d}"));
    let word = |t: &str| {
        let low = t.to_lowercase();
        if let Some(x) = topn(&low) {
            return x;
        }
        let letters = low.trim_end_matches(|c: char| c.is_ascii_digit());
        let code = !letters.is_empty() && letters.len() < low.len() && letters.chars().all(|c| c.is_ascii_lowercase());
        if code || w::ACRONYMS.contains(&low.as_str()) { low.to_uppercase() } else { t.to_string() }
    };
    // a metric is said by its whole name: "eval.top1" and "train.top1" are two metrics
    let base = capitalised(&base.replace(['.', '_'], " ").split(' ').filter(|t| !t.is_empty()).map(word).collect::<Vec<_>>().join(" "));
    if unit.is_empty() { base } else { format!("{base} ({unit})") }
}

/// A difference with its direction in words, once (design pass 1, P5): "0.15 lower", "0.04 higher", "no difference".
fn diff_said(d: f64, p: usize) -> String {
    let size = dec(d.abs(), p, false);
    // a difference too small for its column's decimals keeps its direction: "under 0.01 lower", never "no difference"
    if size.trim_start_matches(['0', '.']).is_empty() && d != 0.0 {
        return format!("under {} {}", dec(10f64.powi(-(p as i32)), p, false), if d < 0.0 { "lower" } else { "higher" });
    }
    if size.trim_start_matches(['0', '.']).is_empty() { "no difference".to_string() } else { format!("{size} {}", if d < 0.0 { "lower" } else { "higher" }) }
}

/// "k=v · k=v" as pairs.
fn kv_pairs(s: &str) -> Vec<(String, String)> {
    s.split(" \u{b7} ").filter_map(|kv| kv.split_once('=')).map(|(k, v)| (k.trim().to_string(), v.trim().to_string())).collect()
}

/// A run's numbers by (arm, metric), from rb's line of them (`metrics_text`: "fp32: top1=0.7598 onnx_top1=0.7569 ·
/// int8: top1=0.7569 ..."; or "fp32.top1=90.64 · int8.top1=90.46"; or "top1=90.4", no arm), else its `results` where it
/// holds them.
fn run_numbers(r: &Value) -> Vec<((String, String), String)> {
    let said = Some(text(r, "metrics_text")).filter(|t| t.contains('=')).unwrap_or(text(r, "results"));
    let mut out = Vec::new();
    for part in said.split(" \u{b7} ") {
        let (arm, rest) = match part.split_once(": ") {
            Some((arm, rest)) if !arm.contains('=') && !arm.contains(' ') => (arm.trim(), rest),
            _ => ("", part),
        };
        for kv in rest.split_whitespace() {
            let Some((k, v)) = kv.split_once('=') else { continue };
            let key = match (arm, k.split_once('.')) {
                ("", Some((a, m))) => (a.to_string(), m.to_string()),
                (a, _) => (a.to_string(), k.to_string()),
            };
            out.push((key, v.to_string()));
        }
    }
    out
}

/// Rules written after their results, counted once at a list's head (design pass 1, P6): ("10 of 13 rules were written
/// after their results came in", whether most were), "" and false where none was.
fn late_head(fs: &[&Value]) -> (String, bool) {
    let late = fs.iter().filter(|f| texts(f, "tags").iter().any(|t| t == "retrospective")).count();
    if late == 0 {
        return (String::new(), false);
    }
    let (counted, rest) = w::made_criteria(late as i64, fs.len() as i64);
    // most, among the rules that have results (one with none has no timing to be an exception in)
    let tested = fs.iter().filter(|f| text(f, "state") != "untested").count();
    (format!("{counted}{}", rest.trim_end_matches(" \u{b7} ")), late * 2 > tested)
}

/// A row's one tag, only where it is the exception (design pass 1, P6): where most rules were written after their
/// results, a rule written before them; else a rule written after them.
fn timing_tag(f: &Value, most_late: bool) -> String {
    let late = texts(f, "tags").iter().any(|t| t == "retrospective");
    // a rule with no results has no timing to be an exception in
    if text(f, "state") == "untested" {
        return String::new();
    }
    match (late, most_late) {
        (true, false) => tag_of("retrospective"),
        (false, true) => format!(" {}", term_as("rule written before the results", &format!("<span class=\"tag\">{}</span>", w::WRITTEN_BEFORE))),
        _ => String::new(),
    }
}

impl<'a> Page<'a> {
    fn cmd(&self, cmd: &str) -> String {
        if self.past { String::new() } else { cmdbox(cmd) }
    }

    /// Every citation in `s` the document resolved (`citations`, M's model: CITE item 1), in order and apart: its byte
    /// span and its entry. Found by the forms the document says each one appears in (`raw`, and its key), never by a
    /// pattern of the view's own, and only where it stands as a citation: not run into a path, a word or a number.
    fn cites_in<'s>(&self, s: &'s str) -> Vec<(usize, usize, &'a Value)> {
        let Some(Value::Dict(all)) = at(self.doc, "citations") else { return Vec::new() };
        let path_char = |c: char| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '/' | '-' | ':' | '@' | '#' | '~' | '\\');
        let mut found: Vec<(usize, usize, &Value)> = Vec::new();
        for c in all.values() {
            let raw = texts(c, "raw");
            let mut forms: Vec<&str> = raw.iter().map(String::as_str).collect();
            let key = text(c, "key");
            forms.push(key);
            for form in forms.into_iter().filter(|f| !f.is_empty()) {
                for (i, _) in s.match_indices(form) {
                    let end = i + form.len();
                    let before = s[..i].chars().next_back();
                    let after: Vec<char> = s[end..].chars().take(2).collect();
                    let runs_on = match after.as_slice() {
                        [c, ..] if c.is_ascii_alphanumeric() || *c == '_' => true,
                        ['-' | ':', d, ..] if d.is_ascii_digit() => true,
                        _ => false,
                    };
                    if before.is_some_and(path_char) || runs_on {
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

    /// A citation as a chip (CITE item 2): "README.md · line 3" in the UI font, the path's last part bold and its
    /// folder before it, opening its Source view in the inspector (C's card `cite-<key>`) where the page holds one.
    /// What rb could not read is said beside it, and so is a line a shared page may not quote.
    fn cite_chip(&self, c: &Value) -> String {
        let (key, name, folder, label) = (text(c, "key"), text(c, "name"), text(c, "folder"), text(c, "label"));
        // the label names the file by its path, its folder first ("logs/agent.log · lines 581-584")
        let inner = match label.strip_prefix(&format!("{folder}{name}")).filter(|_| !name.is_empty()) {
            Some(rest) => {
                let folder = if folder.is_empty() { String::new() } else { format!("<span class=\"cdir\">{}</span>", esc(folder)) };
                format!("{folder}<b>{}</b>{}", esc(name), esc(rest))
            }
            None => esc(if label.is_empty() { key } else { label }),
        };
        let (missing, not_quoted) = (text(c, "missing"), text(c, "not_quoted"));
        let class = format!("cite{}{}", if missing.is_empty() { "" } else { " miss" }, if not_quoted.is_empty() { "" } else { " nq" });
        let hover = [text(c, "path"), missing, not_quoted].into_iter().filter(|s| !s.is_empty()).collect::<Vec<_>>().join(" \u{b7} ");
        let id = format!("cite-{key}");
        let chip = if self.ids.contains(&esc(&id)) {
            format!("<a class=\"{class}\" href=\"#{}\" data-cite=\"{}\" title=\"{}\">{inner}</a>", esc(&id), esc(key), esc(&hover))
        } else {
            format!("<span class=\"{class}\" data-cite=\"{}\" title=\"{}\">{inner}</span>", esc(key), esc(&hover))
        };
        // what rb could not read, and a line a shared page may not quote, said beside the chip (CITE items 1 and 4)
        match [missing, not_quoted].into_iter().find(|s| !s.is_empty()) {
            Some(said) => format!("{chip} <span class=\"cmiss\">({})</span>", esc(said)),
            None => chip,
        }
    }

    /// Author text, verbatim, as `au` draws it, each citation in it a chip (CITE item 2); its other words linked to
    /// `to` where it is not "", the chips apart, since a link holds no link.
    fn au_cited(&self, s: &str, to: &str, class: &str) -> String {
        let cites = self.cites_in(s);
        let words = |w: &str| if to.is_empty() || w.trim().is_empty() { esc(w) } else { a(to, &esc(w), class) };
        if cites.is_empty() {
            return if to.is_empty() { au(s) } else { a(to, &au(s), class) };
        }
        let mut out = String::new();
        let mut from = 0;
        for (i, end, c) in cites {
            out.push_str(&words(&s[from..i]));
            out.push_str(&self.cite_chip(c));
            from = end;
        }
        out.push_str(&words(&s[from..]));
        format!("<span class=\"au\">{out}</span>")
    }

    /// rb's line for a call, the note a session gave in it drawn as its words where rb hands the note apart: only after
    /// rb's "note: " is it the writer's.
    fn said_with_note(&self, line: &str, note: &str) -> String {
        let marked = format!("{}{note}", w::NOTE);
        match line.find(&marked).filter(|_| !note.is_empty()) {
            Some(i) => {
                let (before, after) = (&line[..i + w::NOTE.len()], &line[i + marked.len()..]);
                format!("<span>{}</span>{}<span>{}</span>", esc(before), self.au_cited(note, "", ""), esc(after))
            }
            None => format!("<span>{}</span>", esc(line)),
        }
    }

    /// An action (a call's command, a confirmation's, the Next step's words to paste, a button): on the export, shown
    /// in the owner's working view and on a page with no viewer named, never in the reader's (OWNER-READER).
    fn act(&self, html: String) -> String {
        if self.surface != Surface::Export || html.is_empty() {
            return html;
        }
        let tag = if html.starts_with("<div") || html.starts_with("<p") { "div" } else { "span" };
        by_viewer(tag, "", &html, &html, "")
    }

    /// A line of rb's that ends in a command: whole where the viewer may act on it, in words alone in the reader's view
    /// of the export.
    fn said_to_act(&self, tag: &str, attrs: &str, line: &str, whole: &str) -> String {
        if self.surface != Surface::Export {
            return format!("<{tag}{attrs}>{whole}</{tag}>");
        }
        by_viewer(tag, attrs, whole, whole, &esc(&reader_line(line)))
    }

    /// A line of rb's on a past version, without its parts (" · " apart) that name a command the document records
    /// (a step's, a run's, a resource's way to get it again): a past version shows no command (guarantee 38).
    fn said_past(&self, line: &str) -> String {
        if !self.past {
            return line.to_string();
        }
        fn walk<'v>(v: &'v Value, out: &mut Vec<&'v str>) {
            match v {
                Value::Dict(d) => {
                    for (k, x) in d.iter() {
                        match (k.as_str(), x) {
                            ("command", Value::Str(c)) if !c.trim().is_empty() => out.push(c.trim()),
                            ("command", x) if !text(x, "text").trim().is_empty() => out.push(text(x, "text").trim()),
                            _ => walk(x, out),
                        }
                    }
                }
                Value::List(l) => l.iter().for_each(|x| walk(x, out)),
                _ => {}
            }
        }
        let mut cmds = Vec::new();
        walk(self.doc, &mut cmds);
        line.split(" \u{b7} ").filter(|part| !cmds.iter().any(|c| nodes::names_command(part, c))).collect::<Vec<_>>().join(" \u{b7} ")
    }

    /// The Next step's words to paste: in a box to copy, but on a past version (guarantee 38), where they are words.
    fn prompt(&self, words: &str) -> String {
        if self.past {
            format!("<p class=\"prompt\">\"{}\"</p>", esc(words))
        } else {
            // the reader of the export reads the Next step's words, with no box to copy them from (OWNER-READER: an open
            // item stays in view; only the action goes)
            let boxed = format!("<div class=\"cmdbox\" data-copybtn><pre class=\"cmd prompt\">\"{}\"</pre></div>", esc(words));
            if self.surface == Surface::Export {
                by_viewer("div", "", &boxed, &boxed, &format!("<p class=\"prompt\">\"{}\"</p>", esc(words)))
            } else {
                boxed
            }
        }
    }

    /// `want` when the page holds it, else `or`.
    fn to<'b>(&self, want: &'b str, or: &'b str) -> &'b str {
        if self.ids.contains(&esc(want)) {
            want
        } else {
            or
        }
    }

    /// rb's words on a page that leaves the machine, where they say what waits "for a confirmation" in no one's voice:
    /// each viewer's, the owner's view saying "your" as rb page and rb open do (fix 2, Look 3); elsewhere as they are.
    fn voiced(&self, html: &str) -> String {
        let owner = owner_voice(html);
        if self.surface == Surface::Export && owner != html { by_viewer("span", "", html, &owner, html) } else { html.to_string() }
    }

    fn call_card(&self, c: &Value) -> String {
        let card = format!("call-{}", text(c, "subject"));
        if self.ids.contains(&esc(&card)) {
            card
        } else {
            "your-call".to_string()
        }
    }

    /// Every view but the Overview and Your call: Research with each question and All criteria; Experiments with each
    /// experiment; the record (Runs, Resources, History).
    fn views(&self) -> [String; 3] {
        let doc = self.doc;
        let research = self.research() + &list(doc, "questions").iter().map(|q| self.question(q)).collect::<String>() + &self.criteria();
        let experiments = self.experiments() + &list(doc, "experiments").iter().map(|x| self.experiment(x)).collect::<String>();
        let record = self.runs() + &self.resources() + &self.history();
        [research, experiments, record]
    }

    /// Experiments: one row per experiment, its title, id, rb's state with its hint, its design standing quietly, and
    /// where its Reproduce is with rb's ready line.
    fn experiments(&self) -> String {
        let rows: String = list(self.doc, "experiments")
            .iter()
            .map(|x| {
                let eid = text(x, "id");
                let xid = format!("x-{eid}");
                let state = text(x, "state");
                let state = if state.is_empty() { String::new() } else { format!("{DOT}{}", self.state_term(state)) };
                let standing = design_said(x);
                let standing = if standing.is_empty() { String::new() } else { format!("{DOT}<span class=\"dim\">{}</span>", esc(standing)) };
                // its criteria counted by rule, as rb's catalogue row says them (home.tree), green only where rb says so
                let counts = list(self.doc, "home.tree")
                    .iter()
                    .find(|t| text(t, "kind") == "experiment" && text(t, "id") == eid)
                    .map(|t| {
                        let muted = texts(t, "muted");
                        let parts: Vec<String> = text(t, "standing").split(" \u{b7} ").filter(|p| p.starts_with(|c: char| c.is_ascii_digit())).map(|p| word(p, &muted)).collect();
                        if parts.is_empty() { String::new() } else { format!("{DOT}<span class=\"tl\"><span class=\"k\">{}</span>{}</span>", w::RULES_LEAD, parts.join(DOT)) }
                    })
                    .unwrap_or_default();
                // Reproduce's readiness in one sentence linked to the panel, rb's ready line on its hover (target 6)
                let ready = ready_said(at(x, "reproduce"));
                let repro = format!("{xid}:reproduce");
                let repro = if self.ids.contains(&esc(&repro)) && !ready.is_empty() { format!("<div class=\"r3 sm\">{}</div>", a(&repro, &ready, "")) } else { String::new() };
                format!(
                    // the title first and its id last, small (design pass 1, P3); its state said of the experiment
                    "<div class=\"srow\" data-go=\"{}\"><div class=\"r1\">{} <span class=\"cid\">{}</span></div><div class=\"r3\"><span>{}</span>{state}{counts}{standing}</div>{repro}</div>",
                    esc(&xid),
                    a(&xid, &au_title(text(x, "title")), "sq"),
                    esc(eid),
                    w::EXPERIMENT_WORD
                )
            })
            .collect();
        let rows = if rows.is_empty() { String::new() } else { format!("<nav class=\"strip\" aria-label=\"{}\">{rows}</nav>", w::EXPERIMENTS) };
        // a nav label is its page's title (design pass 1, P9)
        let body = format!("<h1 id=\"h-experiments\">{}</h1><p class=\"lede\">{}</p>{rows}", w::EXPERIMENTS, w::EXPERIMENTS_LEDE);
        section("experiments", &with_project(self.doc, w::EXPERIMENTS), w::EXPERIMENTS, "insp-none", &body)
    }

    /// An experiment's state word with rb's line for it: "locked" has its own, every other state the experiment's.
    fn state_term(&self, state: &str) -> String {
        let key = if state == "locked" || state == "not locked" { state } else { "open (experiment)" };
        term_as(key, &esc(state))
    }

    /// An experiment: its who line; its design standing once, with what changed since it was confirmed and what is not
    /// recorded; its facts verbatim; its comparison with the runs right under it; its criteria; the analyses that read
    /// its runs; Reproduce; then its settings, notes and amendments folded.
    fn experiment(&self, x: &Value) -> String {
        let doc = self.doc;
        let eid = text(x, "id");
        let xid = format!("x-{eid}");
        let mut h = vec![
            format!("<p class=\"crumb\">{}<span class=\"sep\">\u{203a}</span>{}</p>", a("experiments", w::EXPERIMENTS, ""), crumb_title(text(x, "title"), eid)),
            format!("<h1 id=\"h-{}\">{}</h1>", esc(&xid), au_title(text(x, "title"))),
        ];
        let who = text(x, "who_line");
        if !who.is_empty() {
            h.push(format!("<p class=\"who\" id=\"{}\">{}</p>", esc(&format!("{xid}:lock")), esc(&who_said(who))));
        }
        // OVERVIEW-V2: what it compares first (rb's line, the baseline labelled as one), with what it varies
        let compares = text(x, "compares");
        if !compares.is_empty() {
            let varies = texts(x, "varies");
            let varies = if varies.is_empty() { String::new() } else { format!("<span> \u{b7} {} </span><span class=\"mono\">{}</span>", w::VARIES, esc(&varies.join(", "))) };
            h.push(format!("<p class=\"cmpl\"><span>{}</span>{varies}</p>", esc(compares)));
        }
        h.push(self.design(x, &xid));
        h.push(self.facts(x));
        h.push(self.comparison(x, &xid));
        h.push(self.experiment_runs(x, &xid));
        h.push(self.experiment_criteria(x, &xid));
        h.push(self.experiment_analyses(x));
        h.push(self.reproduce_panel(x, &xid));
        h.push(self.experiment_folds(x, &xid));
        format!(
            "<article class=\"view obj\" id=\"{}\" data-nav=\"experiments\" data-crumb=\"{}\" data-title=\"{}\" data-root=\"{}\" aria-labelledby=\"h-{}\">{}</article>",
            esc(&xid),
            esc(eid),
            esc(&with_project(doc, eid)),
            "insp-none",
            esc(&xid),
            h.concat()
        )
    }

    /// The design's standing, once: rb's word, unless rb's own line already says it; then each line rb gives (what
    /// changed since it was confirmed), and each part not recorded.
    fn design(&self, x: &Value, xid: &str) -> String {
        // a design the user wrote, or one they confirmed as it stands, carries no mark; one that waits says so, once
        let standing = design_said(x);
        let lines = texts(x, "design.lines");
        let mut out = String::new();
        if !standing.is_empty() && !lines.iter().any(|l| l.starts_with(standing)) {
            out.push_str(&format!("<p class=\"dstd\">{}</p>", esc(standing)));
        }
        for l in &lines {
            // a command at the line's end gets a Copy of its own (design pass 1, target 6): the line reads whole, as rb
            // says it, the command held apart for the copy; none on a past version
            let line = match l.rsplit_once(": rb confirm ") {
                Some((_, rest)) if !rest.contains(' ') && !self.past => {
                    let id = format!("{xid}:confirm");
                    format!(
                        "{}<span class=\"cmdsrc\" id=\"{}\" hidden>rb confirm {}</span><span data-copybtn=\"{}\" data-label=\"{}\"></span>",
                        esc(l),
                        esc(&id),
                        esc(rest),
                        esc(&id),
                        w::COPY
                    )
                }
                _ => esc(l),
            };
            out.push_str(&self.said_to_act("p", " class=\"dline\"", l, &line));
        }
        for part in texts(x, "design.not_recorded") {
            out.push_str(&format!("<p class=\"dline dim\">{}{}</p>", esc(&part), w::NOT_RECORDED));
        }
        if out.is_empty() {
            return String::new();
        }
        format!("<div class=\"design\" id=\"{}\">{out}</div>", esc(&format!("{xid}:design")))
    }

    /// The experiment's facts, verbatim: its hypothesis, purpose and why, and the decision it gates, either way, with
    /// rb's words on the gate.
    fn facts(&self, x: &Value) -> String {
        let mut rows: Vec<(String, String)> = Vec::new();
        // the hypothesis or purpose on one line, its whole text one click away (OVERVIEW-V2): the text once, folded to a
        // line until it is opened
        for (label, key) in [(w::HYPOTHESIS, "hypothesis"), (w::PURPOSE, "purpose")] {
            let t = text(x, key);
            if !t.is_empty() {
                rows.push((label.to_string(), format!("<details class=\"oneline\" data-fold=\"x-{}:{key}\"><summary>{}</summary></details>", esc(text(x, "id")), self.au_cited(t, "", ""))));
            }
        }
        // what its variants differ in on purpose, as rb show says it (said once: beside what it compares, where rb says that)
        let varies = texts(x, "varies");
        if !varies.is_empty() && text(x, "compares").is_empty() {
            rows.push((w::VARIES.to_string(), esc(&varies.join(", "))));
        }
        if let Some([label, why]) = at(x, "why_part").and_then(Value::as_list).map(Vec::as_slice) {
            if let (Some(label), Some(why)) = (label.as_str(), why.as_str()) {
                if !why.is_empty() {
                    rows.push((if label.is_empty() { w::WHY.to_string() } else { label.to_string() }, self.au_cited(why, "", "")));
                }
            }
        }
        for part in list(x, "gate_parts") {
            let Some([k, v]) = part.as_list().map(Vec::as_slice) else { continue };
            let (Some(k), Some(v)) = (k.as_str(), v.as_str()) else { continue };
            match k {
                // the document names each branch in rb's words ("if it holds", "if not"); an earlier one by its key
                "holds" | w::IF_IT_HOLDS => rows.push((w::IF_IT_HOLDS.to_string(), self.au_cited(v, "", ""))),
                "not" | w::IF_NOT => rows.push((w::IF_NOT.to_string(), self.au_cited(v, "", ""))),
                _ if !v.is_empty() => rows.push((String::new(), esc(v))),
                _ => {}
            }
        }
        let said = text(x, "gate_words_said");
        if !said.is_empty() {
            rows.push((String::new(), esc(said)));
        }
        // how it stands to other experiments, in rb's line for each, and the calls made on it, each in rb's line
        for line in texts(x, "relation_lines") {
            rows.push((w::RELATION.to_string(), esc(&line)));
        }
        let eid = text(x, "id");
        for m in list(self.doc, "made_calls").iter().filter(|m| text(m, "subject") == eid) {
            rows.push((w::DECISION.to_string(), self.said_with_note(text(m, "line"), text(m, "note"))));
        }
        if rows.is_empty() {
            return String::new();
        }
        let rows: String = rows.iter().map(|(k, v)| format!("<dt>{}</dt><dd>{v}</dd>", esc(k))).collect();
        // a design a session wrote any part of keeps its spaces, as its changed-since lines do (.dline), so the gate's
        // own row reads `ship  INT8` where that line does; the user's own design draws as it did
        let class = if design_waits(x) { "facts parts" } else { "facts" };
        format!("<dl class=\"{class}\">{rows}</dl>")
    }

    /// The experiment's comparison as a grid (PIVOT, DESIGN-PASS-1 addendum 4): numbers measured under different settings
    /// never share a column unannounced. Rows are what the experiment varies, columns each set of the other settings rb
    /// reads its rows under, and a difference is said only within a column, against the baseline there.
    /// Two variants under several sets of settings turn it on its side: each set a row, the two variants and their
    /// difference the columns, so the difference still reads within one set. A cell of one run opens that run; a cell
    /// of several, the experiment's runs right under the grid (a card per cell would put the scale record's export
    /// past its size budget).
    fn comparison(&self, x: &Value, xid: &str) -> String {
        let metrics = list(x, "table.metrics");
        let rows = list(x, "table.rows");
        if metrics.is_empty() || rows.is_empty() {
            return String::new();
        }
        let conds = texts(x, "table.conditions");
        let set_of = |r: &Value| -> Vec<(String, String)> {
            // the conditions rb selected its runs by, and the settings its variant holds apart from the others without
            // the experiment varying them (`unvaried`): a column never mixes two values of either
            let mut out: Vec<(String, String)> = Vec::new();
            for key in ["where", "unvaried"] {
                if let Some(Value::Dict(m)) = at(r, key) {
                    out.extend(m.iter().map(|(k, v)| (k.to_string(), v.as_str().map(str::to_string).unwrap_or_else(|| crate::pyjson::dumps(v)))));
                }
            }
            out.sort_by_key(|(k, _)| conds.iter().position(|c| c == k).unwrap_or(usize::MAX));
            out
        };
        let within = |small: &[(String, String)], big: &[(String, String)]| small.iter().all(|kv| big.contains(kv));
        let keys = table_keys(x);
        // the variants, the baseline first, each with what it varies in plain words
        let mut variants: Vec<(&str, &Value)> = Vec::new();
        for r in rows.iter().filter(|r| text(r, "role") == "baseline").chain(rows.iter().filter(|r| text(r, "role") != "baseline")) {
            if !variants.iter().any(|(v, _)| *v == text(r, "variant")) {
                variants.push((text(r, "variant"), r));
            }
        }
        let baseline = rows.iter().find(|r| text(r, "role") == "baseline").map(|r| text(r, "variant")).unwrap_or("");
        // a row's name: the value it varies in plain words ("Step by step"), each with its key where it varies more than
        // one ("FP32 · Calib: 1024"); and the baseline as a sentence names it ("than plain", "than the baseline")
        let varied = |r: &Value| -> (String, String) {
            let vs: Vec<(String, String)> = match at(r, "varies") {
                Some(Value::Dict(m)) if !m.is_empty() => m.iter().map(|(k, v)| (k.to_string(), v.as_str().map(str::to_string).unwrap_or_else(|| crate::pyjson::dumps(v)))).collect(),
                _ => vec![(String::new(), text(r, "variant").to_string())],
            };
            match vs.as_slice() {
                [(_, v)] => (plain_value(v, true), plain_value(v, false)),
                many => (many.iter().map(|(k, v)| if k.is_empty() { plain_value(v, true) } else { setting_said(k, v, &keys) }).collect::<Vec<_>>().join(DOT_TEXT), w::THE_BASELINE.to_string()),
            }
        };
        // the sets of the other settings; a set only the baseline holds that every other set contains (a setting that
        // does not apply to it) is no column of its own: its cell stands in each
        let mut sets: Vec<Vec<(String, String)>> = Vec::new();
        for r in rows.iter() {
            let s = set_of(r);
            if !sets.contains(&s) {
                sets.push(s);
            }
        }
        let cols: Vec<Vec<(String, String)>> = sets
            .iter()
            .filter(|s| {
                let only_base = rows.iter().filter(|r| set_of(r) == **s).all(|r| text(r, "variant") == baseline);
                !(only_base && sets.iter().any(|o| o != *s && o.len() > s.len() && within(s, o)))
            })
            .cloned()
            .collect();
        // a variant's row under a set: its own, else the baseline's under a set the column contains
        let row_at = |variant: &str, col: &[(String, String)]| -> Option<&Value> {
            rows.iter().find(|r| text(r, "variant") == variant && set_of(r) == col).or_else(|| {
                if variant != baseline {
                    return None;
                }
                rows.iter().filter(|r| text(r, "variant") == variant && within(&set_of(r), col)).max_by_key(|r| set_of(r).len())
            })
        };
        let col_label = |col: &[(String, String)]| col.iter().map(|(k, v)| setting_said(k, v, &keys)).collect::<Vec<_>>().join(DOT_TEXT);
        let side = variants.len() == 2 && cols.len() > 1;
        let mut grids = Vec::new();
        // one set of settings: one table, a column per metric; several: a grid per metric, its title above it
        let groups: Vec<Vec<&Value>> = if cols.len() == 1 { vec![metrics.iter().collect()] } else { metrics.iter().map(|m| vec![m]).collect() };
        for (gi, group) in groups.iter().enumerate() {
            fn cell_of<'v>(row: Option<&'v Value>, m: &Value) -> Option<&'v Value> {
                row.and_then(|r| r.get("metrics")).and_then(|ms| ms.get(text(m, "name"))).filter(|c| c.is_dict())
            }
            // each metric's decimals, from the size of what tells its values apart within a column (P5)
            let place_of = |m: &Value| -> (usize, usize) {
                let unit = text(m, "unit");
                let mut gaps = Vec::new();
                let mut deltas = Vec::new();
                for col in &cols {
                    let base = cell_of(row_at(baseline, col), m).and_then(|c| at(c, "mean")).and_then(Value::as_f64);
                    for (v, _) in &variants {
                        if let Some(c) = cell_of(row_at(v, col), m) {
                            if let (Some(b), Some(x)) = (base, at(c, "mean").and_then(Value::as_f64)) {
                                if *v != baseline && x != b {
                                    gaps.push(x - b);
                                }
                            }
                            if let Some(d) = at(c, "delta").and_then(Value::as_f64) {
                                deltas.push(d);
                            }
                        }
                    }
                }
                (places(said_unit(unit_word(unit)), &gaps), places(said_unit(unit_word(unit)), &deltas))
            };
            let title = |m: &Value| metric_title(m);
            // a set under which no variant has this metric is no column (or, on its side, no row) of its grid
            let shown_cols: Vec<&Vec<(String, String)>> = cols.iter().filter(|c| group.iter().any(|m| variants.iter().any(|(v, _)| cell_of(row_at(v, c), m).is_some()))).collect();
            let value_cell = |v: &str, col: &[(String, String)], m: &Value| -> String {
                let Some(cell) = cell_of(row_at(v, col), m) else { return format!("<td>{}</td>", w::NOT_RUN) };
                let Some(mean) = at(cell, "mean").and_then(Value::as_f64) else { return format!("<td>{}</td>", w::NOT_RUN) };
                let shown = dec(mean, place_of(m).0, false);
                let n = count(cell, "n").unwrap_or(0);
                // rb's count where it gives one ("3 seeds", "1 run"); else one unit is one run, more counted by their unit
                let units = match text(cell, "count_said") {
                    "" if n == 1 => units_said(1, ""),
                    "" => units_said(n, &self.unit_of(x, cell)),
                    said => said.to_string(),
                };
                // no hover of rb's digits: the runs under the grid give each run's, and the scale record's export has no
                // room for them under its size budget
                let said = format!("<b>{}</b> {DOT_TEXT_TRIM}{}", esc(&shown), esc(&units));
                // a document that lists no cell's runs (before rb gave `run_ids`) opens the runs under the grid
                let runs = texts(cell, "run_ids");
                let said = match runs.as_slice() {
                    [] if at(cell, "run_ids").is_some() => said,
                    [one] => a(&self.run_to(one), &said, ""),
                    _ if self.ids.contains(&esc(&format!("{xid}:runs"))) => a(&format!("{xid}:runs"), &said, ""),
                    _ => said,
                };
                let other = text(cell, "other_code");
                let other = if other.is_empty() { String::new() } else { format!("<span class=\"gd dim\">{}</span>", esc(other)) };
                // a difference only against a baseline in the same column: one rb gave against a baseline elsewhere
                // would cross columns
                let base_here = cell_of(row_at(baseline, col), m).is_some();
                let diff = if v == baseline || side || !base_here { String::new() } else { self.difference(x, cell, m, &varied(variants[0].1).1, place_of(m).1) };
                format!("<td>{said}{diff}{other}</td>")
            };
            let mut body = String::new();
            let head;
            if side {
                let (b, o) = (variants[0], variants[1]);
                let m = group[0];
                let base_head = format!("{} <span class=\"tag\">{}</span>", esc(&varied(b.1).0), w::BASELINE_TAG);
                head = format!("<tr><th></th><th>{base_head}</th><th>{}</th><th>{}</th></tr>", esc(&varied(o.1).0), w::DIFFERENCE);
                for col in shown_cols.iter().copied() {
                    let label = col_label(col);
                    let bc = value_cell(b.0, col, m);
                    let oc = value_cell(o.0, col, m);
                    let diff = match (cell_of(row_at(b.0, col), m), cell_of(row_at(o.0, col), m)) {
                        (Some(_), Some(c)) => self.difference(x, c, m, &varied(b.1).1, place_of(m).1),
                        _ => String::new(),
                    };
                    body.push_str(&format!("<tr><th>{}</th>{bc}{oc}<td>{diff}</td></tr>", esc(&label)));
                }
            } else {
                let heads: String = if cols.len() == 1 {
                    group.iter().map(|m| format!("<th>{}</th>", esc(&title(m)))).collect()
                } else {
                    shown_cols.iter().map(|c| format!("<th>{}</th>", esc(&col_label(c)))).collect()
                };
                head = format!("<tr><th></th>{heads}</tr>");
                // a variant with no value for this metric under any set is no row of its grid
                for (v, r) in variants.iter().filter(|(v, _)| shown_cols.iter().any(|c| group.iter().any(|m| cell_of(row_at(v, c), m).is_some()))) {
                    let tag = if *v == baseline { format!(" <span class=\"tag\">{}</span>", w::BASELINE_TAG) } else { String::new() };
                    let label = varied(r).0;
                    let cells: String = if cols.len() == 1 {
                        group.iter().map(|m| value_cell(v, &cols[0], m)).collect()
                    } else {
                        shown_cols.iter().map(|c| value_cell(v, c, group[0])).collect()
                    };
                    body.push_str(&format!("<tr><th>{}{tag}</th>{cells}</tr>", esc(&label)));
                }
            }
            let gtitle = if cols.len() == 1 { String::new() } else { format!("<h3 class=\"gt\">{}</h3>", esc(&title(group[0]))) };
            let _ = gi;
            grids.push(format!("{gtitle}<div class=\"wrap\"><table class=\"grid\"><thead>{head}</thead><tbody>{body}</tbody></table></div>"));
        }
        let mut notes = Vec::new();
        if cols.len() > 1 {
            notes.push(if side { w::GRID_ROWS_APART } else { w::GRID_COLUMNS_APART }.to_string());
        }
        if let Some(n) = count(x, "counted") {
            notes.push(w::from_runs(n));
        }
        let notes: String = notes.iter().map(|n| format!("<p class=\"dim sm gnote\">{}</p>", esc(n))).collect();
        let html = format!(
            "<section id=\"{}\" aria-labelledby=\"h-{}\"><h2 id=\"h-{}\">{}</h2>{}{notes}</section>",
            esc(&format!("{xid}:cmp")),
            esc(&format!("{xid}:cmp")),
            esc(&format!("{xid}:cmp")),
            w::COMPARISON,
            grids.concat()
        );
        html
    }

    /// What a cell's units are: rb's word for them where it gives one (`unit`, "" where each run is its own unit), else
    /// the unit the experiment's rules read by.
    fn unit_of(&self, x: &Value, cell: &Value) -> String {
        if let Some(u) = at(cell, "unit").and_then(Value::as_str).or_else(|| at(x, "table.unit").and_then(Value::as_str)).filter(|u| !u.is_empty()) {
            return if u == "run" { String::new() } else { u.to_string() };
        }
        // a rule read across design cells counts cells, which a cell of the grid is not; rb's "unit" names none
        texts(x, "findings").iter().filter_map(|c| self.finding(c)).map(|f| text(f, "reading.unit")).find(|u| !matches!(*u, "" | "design cell" | "unit" | "run")).unwrap_or("").to_string()
    }

    /// A cell's difference with the baseline in its column, as a short sentence: "3.60 points higher than plain (+0.62
    /// to +6.58)", or with no interval, why not. Where a rule reads the comparison, the rule's own reading, linked to it;
    /// "" where the cell has no difference.
    fn difference(&self, x: &Value, cell: &Value, m: &Value, base: &str, p: usize) -> String {
        let unit = at(cell, "unit_said").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| {
            let u = unit_word(text(m, "unit"));
            if u == "%" { "points".to_string() } else { said_unit(u).to_string() }
        });
        let units = self.unit_of(x, cell);
        let mixed = at(cell, "differs_in").is_some_and(Value::truthy);
        // the rule whose comparison this is, which the difference opens: rb's cell names it (`criteria`), or a rule reads
        // the cell otherwise (`read_by`)
        let rule = Some(text(cell, "criterion")).filter(|c| !c.is_empty()).map(str::to_string).or_else(|| texts(cell, "criteria").into_iter().next()).or_else(|| list(cell, "read_by").first().map(|r| text(r, "criterion").to_string())).filter(|c| !c.is_empty());
        // the cell's own numbers, always: rb's change within this column (a rule's reading can cover other runs); a
        // rule's reading only where the cell gives no change
        let num = |v: Option<&Value>, k: &str| v.and_then(|v| at(v, k)).and_then(Value::as_f64);
        let reading = rule.as_deref().and_then(|c| self.finding(c)).and_then(|f| at(f, "reading")).filter(|r| at(r, "estimate").and_then(Value::as_f64).is_some());
        let (est, lo, hi, ratio) = match (at(cell, "delta").and_then(Value::as_f64), reading) {
            (Some(d), _) => (Some(d), num(Some(cell), "lo"), num(Some(cell), "hi"), false),
            (None, Some(r)) => (num(Some(r), "estimate"), num(Some(r), "lo"), num(Some(r), "hi"), rule.as_deref().and_then(|c| self.finding(c)).is_some_and(|f| text(f, "rule").starts_with("ratio."))),
            _ => (None, None, None, false),
        };
        let Some(e) = est else { return String::new() };
        let xs: Vec<f64> = [Some(e), lo, hi].into_iter().flatten().collect();
        // where a rule reads the cell and gives its edges (check_band), the digits its finding's sentence says them in
        // (FIX5, model's said_places): 2 decimals at least, apart from each other and from the rule's number, more only
        // where these would put a number on or across an edge ("0.16 points lower", never rb compare's 0.1567 beside
        // ≥ -1); else the screen's own
        let band = rule.as_deref().and_then(|c| self.finding(c)).and_then(|f| at(f, "check_band").map(|b| (f, b)));
        let (edges, target): (Vec<f64>, Option<f64>) = match band.map(|(f, b)| (text(f, "rule"), num(Some(b), "target"), num(Some(b), "tolerance"), text(b, "comparator"))) {
            Some((rule, Some(t), tol, how)) => {
                // a margin either side of 0, and no number of its own, for a rule that asks for no threshold
                let sided = ["better", "no worse than", "within ±"].iter().any(|w| rule.contains(w));
                let edges = match (how, tol) {
                    ("within", Some(tol)) if tol != 0.0 => vec![t - tol, t + tol],
                    _ if rule.contains("no worse than") => vec![-t.abs(), t.abs()],
                    _ => vec![t],
                };
                (edges, (!sided).then_some(t))
            }
            _ => (Vec::new(), None),
        };
        let p = if edges.is_empty() || ratio {
            places(said_unit(unit_word(text(m, "unit"))), &xs).max(p.min(6))
        } else {
            let all: Vec<f64> = xs.iter().copied().chain(target).collect();
            let apart = (2..=6).find(|&q| all.iter().enumerate().all(|(i, a)| all[i + 1..].iter().all(|b| (a - b).abs() <= crate::assess::close(*a, *b) || own_digits(*a, q, false) != own_digits(*b, q, false)))).unwrap_or(6);
            let keeps = |q: usize| xs.iter().all(|&x| edges.iter().all(|&m| {
                let said: f64 = own_digits(x, q, false).parse().unwrap_or(x);
                (x - m).abs() <= crate::assess::close(x, m) || (said - m).signum() == (x - m).signum() && said != m
            }));
            (apart..=17).find(|&q| keeps(q)).unwrap_or(apart)
        };
        // a number under 0.01 keeps two significant figures where a rule's sentence says it, as the finding does
        let dec = |x: f64, p: usize, signed: bool| if edges.is_empty() || ratio { dec(x, p, signed) } else { own_digits(x, p, signed) };
        let by = if unit.is_empty() { String::new() } else { format!(" {unit}") };
        let mut said = if ratio {
            format!("{} times {base}", dec(e, p, false))
        } else if e == 0.0 {
            format!("level with {base}")
        } else {
            // a primary rule's own digits where the cell knows no edge of it and the screen's would say another number
            // (a change of +0.05000004 that misses at most 0.05 never reads 0.05): rb compare's `said`, for the change
            // the cell is
            let digits = Some(text(cell, "said").trim_start_matches(['+', '-'])).filter(|d| edges.is_empty() && !d.is_empty() && at(cell, "primary").is_some_and(Value::truthy));
            let size = match digits {
                Some(d) if d.parse::<f64>().ok() != dec(e.abs(), p, false).parse::<f64>().ok() => d.to_string(),
                _ => dec(e.abs(), p, false),
            };
            let dir = if e < 0.0 { w::LOWER } else { w::HIGHER };
            if size.trim_start_matches(['0', '.']).is_empty() {
                format!("under {}{by} {dir} than {base}", dec(10f64.powi(-(p as i32)), p, false))
            } else {
                format!("{size}{by} {dir} than {base}")
            }
        };
        if let (Some(lo), Some(hi)) = (lo, hi) {
            said.push_str(&format!(" ({} to {})", dec(lo, p, !ratio), dec(hi, p, !ratio)));
        } else {
            // rb's words for why there is none, where it gives them (`no_interval`); a document before them, what the
            // cell's basis says
            let many = units_said(2, &units).trim_start_matches("2 ").to_string();
            let why = match at(cell, "no_interval").and_then(Value::as_str) {
                Some(given) => given.to_string(),
                None if text(cell, "basis").contains("unpaired") => format!("{many} don't match, so no interval"),
                // one unit is one run, as the cell says it
                None if text(cell, "basis") == "n=1" || count(cell, "n") == Some(1) => "one run, so no interval".to_string(),
                None if reading.is_none() && count(cell, "n").is_some_and(|n| n < 3) => format!("too few {many} for an interval"),
                None => String::new(),
            };
            if !why.is_empty() {
                said.push_str(&format!("{DOT_TEXT}{why}"));
            }
        }
        if mixed {
            let keys = table_keys(x);
            let d: Vec<String> = texts(cell, "differs_in").iter().map(|k| key_words(k, &keys).to_lowercase()).collect();
            said.push_str(&format!("{DOT_TEXT}{} {}", w::ALSO_DIFFERS_IN, d.join(" and ")));
        }
        let said = esc(&said);
        let said = match rule.as_deref() {
            Some(c) => a(&self.criterion_to(c), &said, ""),
            None => said,
        };
        format!("<span class=\"gd\">{said}</span>")
    }

    /// A run as the page names it: its label (P's run_label: its arms and per-run values), else its experiment's, never
    /// its id (Look 7: no raw id is visible text).
    fn run_said(&self, rid: &str) -> String {
        let run = self.doc.get("runs").and_then(|m| m.get(rid)).filter(|r| r.is_dict());
        let label = run.map_or("", |r| text(r, "label"));
        if !label.is_empty() {
            return label.to_string();
        }
        match run.map_or("", |r| text(r, "experiment_id")) {
            "" => w::A_RUN.to_string(),
            x => format!("{}{x}", w::RUN_OF),
        }
    }

    /// An object the page links by name: a run by its label, an analysis by its title, anything else (an experiment, a
    /// rule, a question, a resource: ids someone chose) as it is.
    fn named(&self, id: &str) -> String {
        let d = self.doc;
        if d.get("runs").and_then(|m| m.get(id)).is_some_and(Value::is_dict) {
            return self.run_said(id);
        }
        if let Some(an) = d.get("analyses").and_then(|m| m.get(id)).filter(|v| v.is_dict()) {
            let t = text(an, "title");
            return if t.is_empty() { w::AN_ANALYSIS.to_string() } else { t.to_string() };
        }
        id.to_string()
    }

    /// Where a run links: its receipt, else its row in Runs.
    fn run_to(&self, rid: &str) -> String {
        let card = format!("run-{rid}");
        if self.ids.contains(&esc(&card)) {
            card
        } else {
            self.to(&format!("rr-{rid}"), "runs").to_string()
        }
    }

    /// The experiment's runs, right under the comparison whose means they make: each run's label, its per-run values and
    /// its numbers; the retracted ones folded apart, each with rb's line.
    fn experiment_runs(&self, x: &Value, xid: &str) -> String {
        let runs: Vec<(&str, &Value)> = texts(x, "run_ids").iter().filter_map(|r| self.doc.get("runs").and_then(|m| m.get(r)).filter(|v| v.is_dict()).map(|v| (text(v, "run_id"), v))).collect();
        if runs.is_empty() {
            return String::new();
        }
        // the same table as Runs (design pass 1, targets 5 and 6), a retracted run struck at its foot
        let (stand, gone): (Vec<_>, Vec<_>) = runs.iter().copied().partition(|(_, r)| !at(r, "retracted").is_some_and(Value::is_dict));
        let gone_n = gone.len();
        let all: Vec<(&str, &Value)> = stand.iter().chain(gone.iter()).copied().collect();
        let (baseline, units) = self.baseline_units(text(x, "id"));
        let retracted = if gone_n > 0 { format!(", {gone_n} {}", w::RETRACTED) } else { String::new() };
        format!(
            "<section class=\"xruns\" id=\"{}\" aria-labelledby=\"h-{}\"><h2 id=\"h-{}\">{} <span class=\"n\">({}{retracted})</span></h2>{}</section>",
            esc(&format!("{xid}:runs")),
            esc(&format!("{xid}:runs")),
            esc(&format!("{xid}:runs")),
            w::RUNS,
            stand.len(),
            self.run_table(&all, &baseline, &units, false)
        )
    }

    /// The experiment's rules, as Rules draws them (design pass 1, target 3): each rule's sentence with its state, rb's
    /// reading in a plain sentence under it and its id trailing; rules written after their results counted once.
    fn experiment_criteria(&self, x: &Value, xid: &str) -> String {
        let fs: Vec<(String, &Value)> = texts(x, "findings").into_iter().filter_map(|c| self.finding(&c).map(|f| (c, f))).collect();
        if fs.is_empty() {
            return String::new();
        }
        let (late, most_late) = late_head(&fs.iter().map(|(_, f)| *f).collect::<Vec<_>>());
        let late = if late.is_empty() { String::new() } else { format!("<p class=\"quiet late\">{}</p>", term_as("rule written after the results", &esc(&late))) };
        // a light row each, opening the rule; its machinery is the rule's own (Rules, its card), never drawn twice
        let rows: String = fs
            .iter()
            .map(|(c, f)| {
                // its sentence and state, its id trailing; rb's reading is the rule's own, one click away (the page's
                // size budget holds the scale record's 60 rules)
                format!(
                    "<div class=\"xrule\" data-go=\"{}\"><p class=\"xr1\">{}<span class=\"sw {}\">{}</span></p><p class=\"xr2\">{} <span class=\"cid\">{}</span></p></div>",
                    esc(&self.criterion_to(c)),
                    self.au_cited(text(f, "statement"), &self.criterion_to(c), "stm"),
                    state_of(f).1,
                    said_plain(state_of(f).0),
                    timing_tag(f, most_late),
                    esc(c)
                )
            })
            .collect();
        format!(
            "<section id=\"{}\" aria-labelledby=\"h-{}\"><h2 id=\"h-{}\">{} <span class=\"n\">({})</span></h2>{late}<div class=\"list\">{rows}</div></section>",
            esc(&format!("{xid}:criteria")),
            esc(&format!("{xid}:criteria")),
            esc(&format!("{xid}:criteria")),
            w::ITS_CRITERIA,
            fs.len()
        )
    }

    /// The analyses that read the experiment's runs: each id, title and what it reads.
    fn experiment_analyses(&self, x: &Value) -> String {
        let mine = texts(x, "run_ids");
        let rows: String = analyses(self.doc)
            .into_iter()
            .filter(|an| texts(an, "reads").iter().any(|r| mine.contains(r)))
            .map(|an| self.analysis_li(an))
            .collect();
        if rows.is_empty() {
            return String::new();
        }
        format!("<h2>{}</h2><ul class=\"plain\">{rows}</ul>", w::ANALYSES)
    }

    fn analysis_li(&self, an: &Value) -> String {
        let id = text(an, "id");
        let card = format!("a-{id}");
        // named by its title, linked to its card: never by its id
        let title = if text(an, "title").is_empty() { esc(w::AN_ANALYSIS) } else { au(text(an, "title")) };
        let title = if self.ids.contains(&esc(&card)) { a(&card, &title, "") } else { title };
        let reads: Vec<String> = texts(an, "reads").iter().map(|r| a(&self.run_to(r), &esc(&self.named(r)), "")).collect();
        let reads = if reads.is_empty() { String::new() } else { format!(" <span class=\"dim\">\u{b7} {} {}</span>", w::READS, reads.join(", ")) };
        format!("<li>{title}{reads}<br><span class=\"dim sm\">{}</span></li>", esc(text(an, "recorded")))
    }

    /// Reproduce, from the document only: rb's ready line; the whole recipe as one script, as rb wrote it; what it takes
    /// to run it again, a row each; the recipe's steps, each with its command and where it comes from; the per-run
    /// values; the settings; the commands as given, apart; what a run made; and the Next step, in words.
    fn reproduce_panel(&self, x: &Value, xid: &str) -> String {
        let Some(r) = at(x, "reproduce").filter(|r| r.is_dict()) else { return String::new() };
        let recipe = format!("{xid}:recipe");
        let script = text(r, "script");
        let copy = if script.is_empty() { String::new() } else { format!("<span data-copybtn=\"{}\" data-label=\"{}\"></span>", esc(&recipe), w::COPY_RECIPE) };
        // OVERVIEW-V2: the recipe sits one click down, under its fold; the fold's line is its heading and rb's ready line
        let ready = ready_said(Some(r));
        let ready = if ready.is_empty() { String::new() } else { format!(" <span class=\"readyline\">{ready}</span>") };
        let head = format!("<summary><h2 id=\"h-{}\">{}</h2>{ready}</summary>", esc(&format!("{xid}:reproduce")), w::REPRODUCE_LINK);
        let mut h = Vec::new();
        // rb's own ready line, its glyphs and counts, first inside the fold (P8)
        let line = text(r, "ready_line");
        if !line.is_empty() {
            h.push(format!("<p class=\"dim sm readyline\">{}</p>", esc(line)));
        }
        // on an export with a clone address, this experiment's own prompt to copy (REPRODUCE-WORDS.md): its id, title and
        // rules; with no script it is selectable text
        // its rules, by id: a gate reads met or missed, never reproduced, so it is not one the prompt asks about
        let mut rules: Vec<String> = texts(x, "findings").into_iter().filter(|c| text(self.doc, &format!("findings.{c}.role")) != "gate").collect();
        rules.sort();
        if let Some(prompt) = self.rerun_prompt_html(Some((text(x, "id"), text(x, "title"), &rules))) {
            let id = format!("{xid}:prompt");
            h.push(format!(
                "<div class=\"rrx rrun\"{}><span data-copybtn=\"{}\" data-label=\"{}\"></span><pre class=\"cmd rprompt\" id=\"{}\">{prompt}</pre></div>",
                self.rerun_addresses(),
                esc(&id),
                w::COPY_PROMPT_EXPERIMENT,
                esc(&id)
            ));
        }
        // a past version offers no command (guarantee 38): no script, no copy
        if !script.is_empty() && !self.past {
            h.push(format!(
                // its Copy recipe beside the script it copies, inside Reproduce's fold
                "<details class=\"fold\" data-fold=\"{}\"><summary>{}</summary><div class=\"cmdbox\">{copy}<pre class=\"cmd\" id=\"{}\">{}</pre></div></details>",
                esc(&format!("{xid}:script")),
                w::WHOLE_RECIPE,
                esc(&recipe),
                esc(script)
            ));
        }
        let ready_rows: String = list(r, "ready")
            .iter()
            .map(|row| {
                let (state, kind) = (text(row, "state"), text(row, "label"));
                let (kind, rest) = ready_parts(kind, state, text(row, "text"));
                let rest = self.said_past(&rest);
                format!(
                    "<li><span class=\"g\" aria-hidden=\"true\">{}</span><span class=\"w\">{}</span><span class=\"d\"><span class=\"s\">{}</span>{}</span></li>",
                    glyph(state),
                    esc(kind),
                    esc(state),
                    if rest.is_empty() { String::new() } else { format!(" \u{b7} {}", self.linked(&rest, &texts(row, "refs"))) }
                )
            })
            .collect();
        if !ready_rows.is_empty() {
            h.push(format!("<h3>{}</h3><ul class=\"ready\">{ready_rows}</ul>", w::WHAT_IT_TAKES));
        }
        let mut steps: Vec<String> = list(r, "steps")
            .iter()
            .map(|s| {
                let cmd = text(s, "command");
                let cmd = if cmd.is_empty() || !at(s, "runnable").map_or(true, Value::truthy) { String::new() } else { self.cmd(cmd) };
                let from = text(s, "from");
                let from = if from.is_empty() { String::new() } else { format!("<div class=\"from\">{}</div>", esc(from)) };
                let (what, label) = (text(s, "what"), text(s, "label"));
                // a label that already leads with what the step is says it once
                // the glossary's meaning stays on the words where the recipe says them
                let b = if what == w::COMMAND_AS_GIVEN { term(w::COMMAND_AS_GIVEN, &format!("<b>{}</b>", esc(what))) } else { format!("<b>{}</b>", esc(what)) };
                let head = match label.strip_prefix(what).filter(|rest| !what.is_empty() && (rest.is_empty() || rest.starts_with(" \u{b7} "))) {
                    Some(rest) => format!("{b}{}", self.linked(rest, &texts(s, "refs"))),
                    None => format!("{b} \u{b7} {}", self.linked(label, &texts(s, "refs"))),
                };
                format!("<li><div class=\"sl\">{head}</div>{cmd}{from}</li>")
            })
            .collect();
        steps.extend(self.per_run(r));
        let given = text(r, "command_as_given.line");
        let made: String = list(r, "made")
            .iter()
            .map(|m| {
                let cmd = text(m, "command");
                let from = text(m, "from");
                let from = if from.is_empty() { String::new() } else { format!("<div class=\"from\">{}</div>", esc(from)) };
                let refs = [text(m, "run").to_string(), text(m, "maker").to_string(), text(m, "resource").to_string()];
                format!("<p class=\"madeby\">{}</p>{}{from}", self.linked(text(m, "line"), &refs), if cmd.is_empty() { String::new() } else { self.cmd(cmd) })
            })
            .collect();
        // the recipe's own command step already says how many runs record their command: what a run made goes under
        // it, and rb's line saying the same count is not drawn a second time
        let said_step = list(r, "steps").iter().position(|s| [text(s, "what"), text(s, "label")].iter().any(|t| t.starts_with(w::COMMAND_AS_GIVEN)));
        match said_step {
            Some(i) if !made.is_empty() => steps[i] = format!("{}{made}</li>", steps[i].strip_suffix("</li>").unwrap_or(&steps[i])),
            Some(_) => {}
            None if !given.is_empty() || !made.is_empty() => {
                steps.push(format!("<li><div class=\"sl\">{}</div>{made}</li>", term(w::COMMAND_AS_GIVEN, &format!("<span class=\"unk\">{}</span>", esc(given)))))
            }
            None => {}
        }
        if !steps.is_empty() {
            h.push(format!("<h3>{}</h3><ol class=\"steps\">{}</ol>", w::RECIPE, steps.concat()));
        }
        let commands: String = list(r, "commands")
            .iter()
            .map(|c| {
                let rid = text(c, "run");
                // how rb knows the command: Reproduce's own words (`given`, P's) where it hands them, else its run's in the
                // same document, else "observed" where rb says it watched; never a guess
                let kind = self.doc.get("runs").and_then(|m| m.get(rid)).map(|r| text(r, "command.kind")).unwrap_or("");
                let how = [text(c, "given"), kind].into_iter().find(|k| !k.is_empty());
                let how = how.unwrap_or(if at(c, "observed").is_some_and(Value::truthy) { w::OBSERVED } else { "" });
                format!("<li>{} <span class=\"dim\">{}</span>{}</li>", a(&self.run_to(rid), &esc(&self.run_said(rid)), ""), esc(how), self.cmd(text(c, "command")))
            })
            .collect();
        if !commands.is_empty() && !self.past {
            h.push(format!("<details class=\"fold\" data-fold=\"{}\"><summary>{}</summary><ul class=\"plain\">{commands}</ul></details>", esc(&format!("{xid}:commands")), w::COMMANDS_AS_RECORDED));
        }
        let prompt = text(r, "prompt");
        if !prompt.is_empty() {
            h.push(format!("<h3>{}</h3>{}", w::NEXT_STEP, self.prompt(prompt)));
        }
        format!(
            "<section class=\"repro\" id=\"{}\" aria-labelledby=\"h-{}\"><details class=\"fold rfold\" data-fold=\"{}\">{head}{}</details></section>",
            esc(&format!("{xid}:reproduce")),
            esc(&format!("{xid}:reproduce")),
            esc(&format!("{xid}:reproduce-fold")),
            h.concat()
        )
    }

    /// The recipe's per-run step (the values by arm, each run linked to its receipt) and its settings step (shared, by
    /// arm, and unknown).
    fn per_run(&self, r: &Value) -> Vec<String> {
        let keys = texts(r, "per_run.keys");
        let mut out = Vec::new();
        if let Some(Value::Dict(by_arm)) = at(r, "per_run.by_arm") {
            let rows: String = by_arm
                .iter()
                .map(|(arm, runs)| {
                    let runs: Vec<String> = runs
                        .as_list()
                        .map(Vec::as_slice)
                        .unwrap_or(&[])
                        .iter()
                        .map(|v| {
                            let vals: Vec<&str> = keys.iter().map(|k| text(v, k)).collect();
                            a(&self.run_to(text(v, "run")), &esc(&vals.join(" ")), "mono")
                        })
                        .collect();
                    format!("<span class=\"arm\">{}</span><span>{}</span>", esc(arm), runs.join(" \u{b7} "))
                })
                .collect();
            if !rows.is_empty() {
                out.push(format!("<li><div class=\"sl\"><b>{}</b> \u{b7} {}</div><div class=\"perrun\">{rows}</div></li>", w::PER_RUN, esc(&keys.join(" \u{b7} "))));
            }
        }
        let setting = |s: &Value| format!("{}={}", text(s, "name"), text(s, "value"));
        let mut rows = String::new();
        let shared: Vec<String> = list(r, "settings.shared").iter().map(setting).collect();
        if !shared.is_empty() {
            rows.push_str(&format!("<span class=\"arm\">{}</span><span class=\"mono\">{}</span>", w::SHARED, esc(&shared.join(" "))));
        }
        if let Some(Value::Dict(by_arm)) = at(r, "settings.by_arm") {
            for (arm, ss) in by_arm {
                let ss: Vec<String> = ss.as_list().map(Vec::as_slice).unwrap_or(&[]).iter().map(setting).collect();
                rows.push_str(&format!("<span class=\"arm\">{}</span><span class=\"mono\">{}</span>", esc(arm), esc(&ss.join(" "))));
            }
        }
        let unknown = texts(r, "settings.unknown");
        if !unknown.is_empty() {
            rows.push_str(&format!("<span class=\"arm\">{}</span><span class=\"mono\">{}</span>", w::UNKNOWN, esc(&unknown.join(", "))));
        }
        if !rows.is_empty() {
            out.push(format!("<li><div class=\"sl\"><b>{}</b></div><div class=\"perrun\">{rows}</div></li>", w::SETTINGS));
        }
        out
    }

    /// rb's line with each object it names linked where the page holds it: the first whole-word mention of each ref.
    fn linked(&self, line: &str, refs: &[String]) -> String {
        let forms: Vec<Form> = refs.iter().filter(|r| !r.is_empty()).filter_map(|r| self.object_to(r).map(|to| (vec![r.clone()], to, ""))).collect();
        linked_forms(line, &forms).0
    }

    /// Where an object of the record links, by its id: a question, an experiment, a criterion, a run, a resource or an
    /// analysis, each at its card or view, else its row; nothing when the page holds none.
    fn object_to(&self, id: &str) -> Option<String> {
        let d = self.doc;
        let has = |k: &str| d.get(k).and_then(|m| m.get(id)).is_some_and(Value::is_dict);
        let want = if list(d, "questions").iter().any(|q| text(q, "id") == id) {
            format!("q-{id}")
        } else if list(d, "experiments").iter().any(|x| text(x, "id") == id) {
            format!("x-{id}")
        } else if has("findings") {
            self.criterion_to(id)
        } else if has("runs") {
            self.run_to(id)
        } else if has("resources") {
            self.resource_to(id)
        } else if has("analyses") {
            format!("a-{id}")
        } else {
            return None;
        };
        self.ids.contains(&esc(&want)).then_some(want)
    }

    fn resource_to(&self, id: &str) -> String {
        let card = format!("r-{id}");
        if self.ids.contains(&esc(&card)) {
            card
        } else {
            self.to(&format!("rs-{id}"), "resources").to_string()
        }
    }

    /// The folds under an experiment: its settings with their sources and status, its notes verbatim, and the lock with
    /// each change made after it at its own anchor.
    fn experiment_folds(&self, x: &Value, xid: &str) -> String {
        let mut out = String::new();
        let settings = at(x, "reproduce.settings");
        if let Some(s) = settings {
            let row = |scope: &str, v: &Value| {
                let status = text(v, "status");
                let status = if status == "verified" { term("verified", &esc(status)) } else { esc(status) };
                format!(
                    "<tr><td>{}</td><td class=\"mono\">{}</td><td class=\"mono\">{}</td><td>{status}</td><td class=\"mono dim\">{}</td></tr>",
                    esc(scope),
                    esc(text(v, "name")),
                    esc(text(v, "value")),
                    esc(text(v, "source"))
                )
            };
            let mut rows: String = list(s, "shared").iter().map(|v| row(w::SHARED, v)).collect();
            if let Some(Value::Dict(by_arm)) = at(s, "by_arm") {
                for (arm, vs) in by_arm {
                    rows.extend(vs.as_list().map(Vec::as_slice).unwrap_or(&[]).iter().map(|v| row(arm, v)));
                }
            }
            for u in texts(s, "unknown") {
                rows.push_str(&format!("<tr><td></td><td class=\"mono\">{}</td><td></td><td>{}</td><td></td></tr>", esc(&u), w::UNKNOWN));
            }
            // OVERVIEW-V2: settings and config values sit under Source details
            let line = text(x, "settings_line");
            let head = if line.is_empty() { w::SOURCE_DETAILS.to_string() } else { format!("{} \u{b7} {line}", w::SOURCE_DETAILS) };
            if !rows.is_empty() {
                out.push_str(&format!(
                    "<details class=\"fold\" data-fold=\"{}\"><summary>{}</summary><div class=\"wrap\" id=\"{}\"><table><tbody>{rows}</tbody></table></div></details>",
                    esc(&format!("{xid}:settings-fold")),
                    esc(&head),
                    esc(&format!("{xid}:settings"))
                ));
            }
        }
        let ids = texts(x, "note_ids");
        let notes: Vec<(String, String)> = list(x, "notes")
            .iter()
            .enumerate()
            .map(|(i, n)| (ids.get(i).cloned().unwrap_or_default(), n.as_str().map(str::to_string).unwrap_or_else(|| text(n, "text").to_string())))
            .filter(|(_, n)| !n.is_empty())
            .collect();
        if !notes.is_empty() {
            let items: String = notes
                .iter()
                .map(|(id, n)| {
                    // named by what it is on, never by its id (Look 7); the id stays the item's own, for rb retract
                    let at = if id.is_empty() { String::new() } else { format!(" data-id=\"{}\"", esc(id)) };
                    format!("<li class=\"note\"{at}><span class=\"nd2\">{}{}:</span> {}</li>", w::NOTE_ON, esc(text(x, "id")), self.au_cited(n, "", ""))
                })
                .collect();
            out.push_str(&format!("<details class=\"fold\" data-fold=\"{}\"><summary>{} ({})</summary><ul class=\"notes plain\">{items}</ul></details>", esc(&format!("{xid}:notes")), w::NOTES, notes.len()));
        }
        let ams = list(x, "amendments");
        if !ams.is_empty() {
            let items: String = ams
                .iter()
                .map(|am| {
                    let why = text(am, "why");
                    let why = if why.is_empty() { String::new() } else { format!("<span>: </span>{}", self.au_cited(why, "", "")) };
                    format!(
                        "<li id=\"{}\"><span class=\"dim\">{}</span><span> \u{b7} </span>{}{why}</li>",
                        esc(&format!("x-{}", text(am, "id"))),
                        esc(text(am, "at")),
                        self.linked(text(am, "line"), &texts(am, "refs"))
                    )
                })
                .collect();
            out.push_str(&format!("<details class=\"fold\" data-fold=\"{}\"><summary>{}</summary><ul class=\"plain\">{items}</ul></details>", esc(&format!("{xid}:amendments")), w::amended_times(ams.len())));
        }
        out
    }

    /// Runs: rb's line, how many runs stand and how many retracted stay on record, then each experiment's runs, a row
    /// each folding its receipt rows, the retracted ones last in their group, struck, with rb's line; then the analyses.
    /// An experiment's baseline variant and its metrics' units, as its comparison table gives them.
    fn baseline_units(&self, eid: &str) -> (String, Vec<(String, String)>) {
        let x = list(self.doc, "experiments").iter().find(|x| text(x, "id") == eid);
        let baseline = x.and_then(|x| list(x, "table.rows").iter().find(|r| text(r, "role") == "baseline").map(|r| text(r, "variant").to_string())).unwrap_or_default();
        let mut units: Vec<(String, String)> = x.map(|x| list(x, "table.metrics").iter().map(|m| (text(m, "name").to_string(), text(m, "unit").to_string())).collect()).unwrap_or_default();
        // FIX4: a metric its comparison table leaves out (one a run names for no arm, as the INT8 demo's onnx_top1) keeps
        // the unit rb's reading of it says, in any experiment, so its title reads as its neighbours' do ("ONNX top-1 (%)"
        // beside "Top-1 (%)") in every runs table
        if let Some(Value::Dict(fs)) = at(self.doc, "findings") {
            // a unit is the metric's, so any experiment's reading of it says it; a level's reading only: a difference's
            // unit ("points") is not the metric's
            for f in fs.values() {
                let m = text(f, "row.metric");
                let unit = text(f, "reading.unit_said");
                if !m.is_empty() && !m.starts_with("change.") && !unit.is_empty() && !units.iter().any(|(n, _)| n == m) {
                    units.push((m.to_string(), unit.to_string()));
                }
            }
        }
        (baseline, units)
    }

    /// One table of runs (design pass 1, target 5): the settings that vary among them as columns, then each metric per
    /// arm, then the difference against the baseline in words; the run's id trailing, small. What every run shares, and
    /// when they were produced and recorded where that is one day each, is said once above it.
    fn run_table(&self, runs: &[(&str, &Value)], baseline: &str, units: &[(String, String)], anchors: bool) -> String {
        if runs.is_empty() {
            return String::new();
        }
        let settings: Vec<Vec<(String, String)>> = runs.iter().map(|(_, r)| kv_pairs(text(r, "per_run_text"))).collect();
        let mut keys: Vec<String> = Vec::new();
        for ps in &settings {
            for (k, _) in ps {
                if !keys.contains(k) {
                    keys.push(k.clone());
                }
            }
        }
        let value = |ps: &Vec<(String, String)>, k: &str| ps.iter().find(|(x, _)| x == k).map(|(_, v)| v.clone()).unwrap_or_default();
        let (vary, same): (Vec<String>, Vec<String>) = keys.into_iter().partition(|k| settings.iter().map(|ps| value(ps, k)).collect::<HashSet<_>>().len() > 1);
        let numbers: Vec<Vec<((String, String), String)>> = runs.iter().map(|(_, r)| run_numbers(r)).collect();
        let mut cols: Vec<(String, String)> = Vec::new();
        for ns in &numbers {
            for (key, _) in ns {
                if !cols.contains(key) {
                    cols.push(key.clone());
                }
            }
        }
        let metrics: Vec<String> = cols.iter().fold(Vec::new(), |mut v, (_, m)| {
            if !v.contains(m) {
                v.push(m.clone());
            }
            v
        });
        // each metric's columns together, the baseline's first
        cols.sort_by_key(|(arm, m)| (metrics.iter().position(|x| x == m), arm != baseline));
        // a difference where a metric has the baseline's column and one other
        let diffs: Vec<(String, String)> = metrics
            .iter()
            .filter_map(|m| {
                let arms: Vec<&String> = cols.iter().filter(|(_, x)| x == m).map(|(a, _)| a).collect();
                (arms.len() == 2 && arms.iter().any(|a| *a == baseline)).then(|| (m.clone(), arms.iter().find(|a| **a != baseline).map(|a| a.to_string()).unwrap_or_default()))
            })
            .collect();
        let unit = |m: &str| units.iter().find(|(x, _)| x == m).map(|(_, u)| u.as_str()).unwrap_or("");
        // each metric's decimals, from the size of its differences (P5)
        let num_at = |ns: &Vec<((String, String), String)>, key: &(String, String)| ns.iter().find(|(k, _)| k == key).and_then(|(_, v)| v.parse::<f64>().ok());
        let mplaces: Vec<(String, usize)> = metrics
            .iter()
            .map(|m| {
                let ds: Vec<f64> = diffs.iter().filter(|(x, _)| x == m).flat_map(|(_, other)| numbers.iter().filter_map(|ns| Some(num_at(ns, &(other.clone(), m.clone()))? - num_at(ns, &(baseline.to_string(), m.clone()))?)).collect::<Vec<_>>()).collect();
                (m.clone(), places(unit(m), &ds))
            })
            .collect();
        let places_of = |m: &str| mplaces.iter().find(|(x, _)| x == m).map_or(2, |(_, p)| *p);
        let days = |k: &str| runs.iter().map(|(_, r)| text(r, k).split(' ').next().unwrap_or("").to_string()).collect::<HashSet<_>>();
        let (produced, recorded) = (days("produced"), days("at"));
        // when each run was produced and when it was recorded, both always: once above the table where every run shares
        // the day, else in a column (an unknown production day said as unknown)
        let (p_once, r_once) = (produced.len() == 1, recorded.len() == 1);
        let day = |d: &str| if d.is_empty() { w::UNKNOWN_DAY.to_string() } else { d.to_string() };
        let mut quiet = Vec::new();
        let shown: Vec<String> = vary.iter().chain(same.iter()).cloned().collect();
        let shared: Vec<String> = same.iter().map(|k| format!("{} {}", key_words(k, &shown).to_lowercase(), value(&settings[0], k))).filter(|s| !s.ends_with(' ')).collect();
        if !shared.is_empty() {
            quiet.push(format!("{}{}", w::EVERY_RUN, shared.join(", ")));
        }
        let mut when = Vec::new();
        if p_once {
            when.push(format!("{}{}", w::PRODUCED_CAP, day(produced.iter().next().map(String::as_str).unwrap_or(""))));
        }
        if r_once {
            let r = recorded.iter().next().cloned().unwrap_or_default();
            when.push(if p_once { format!("{}{r}", w::RECORDED) } else { format!("{}{r}", w::RECORDED_CAP) });
        }
        if !when.is_empty() {
            quiet.push(when.join(", "));
        }
        let quiet = if quiet.is_empty() { String::new() } else { format!("<p class=\"dim sm rq\">{}</p>", esc(&quiet.join(". "))) };
        // fix 3: each metric's title once, as the grid says it, over its arms by their plain names ("Plain", "Step by
        // step", "FP32"), never a variant's key glued to it; a table with no arm column keeps one row of heads
        let two = cols.iter().any(|(arm, _)| !arm.is_empty());
        let span = if two { " rowspan=\"2\"" } else { "" };
        let mut head: String = vary.iter().map(|k| format!("<th scope=\"col\"{span}>{}</th>", esc(&key_words(k, &shown)))).collect();
        let mut under = String::new();
        // each metric's arms, then its difference (the comparison's order)
        for m in &metrics {
            let label = metric_label(m, unit(m));
            let arms: Vec<&String> = cols.iter().filter(|(_, x)| x == m).map(|(a, _)| a).collect();
            let ds: Vec<&String> = diffs.iter().filter(|(x, _)| x == m).map(|(_, o)| o).collect();
            if arms.iter().all(|a| a.is_empty()) && ds.is_empty() {
                head.push_str(&format!("<th scope=\"col\" class=\"num\"{span}>{}</th>", esc(&label)));
                continue;
            }
            head.push_str(&format!("<th scope=\"colgroup\" class=\"num mh\" colspan=\"{}\">{}</th>", arms.len() + ds.len(), esc(&label)));
            for arm in arms {
                let said = if arm.is_empty() { label.clone() } else { plain_value(arm, true) };
                under.push_str(&format!("<th scope=\"col\" class=\"num\">{}</th>", esc(&said)));
            }
            for other in ds {
                under.push_str(&format!("<th scope=\"col\" class=\"num\" title=\"{} {} {}\">{}</th>", esc(&plain_value(other, true)), w::AGAINST, esc(&plain_value(baseline, false)), w::DIFFERENCE));
            }
        }
        if !p_once {
            head.push_str(&format!("<th scope=\"col\"{span}>{}</th>", w::PRODUCED_CAP.trim()));
        }
        if !r_once {
            head.push_str(&format!("<th scope=\"col\"{span}>{}</th>", w::RECORDED_CAP.trim()));
        }
        head.push_str(&format!("<th scope=\"col\"{span}><span class=\"sr\">{}</span></th>", w::RUN));
        let head = if two { format!("{head}</tr><tr>{under}") } else { head };
        let rows: String = runs
            .iter()
            .zip(settings.iter().zip(numbers.iter()))
            .map(|((rid, r), (ps, ns))| {
                let gone = at(r, "retracted").is_some_and(Value::is_dict);
                let mut cells: String = vary.iter().map(|k| format!("<td>{}</td>", esc(&value(ps, k)))).collect();
                let num = |key: &(String, String)| ns.iter().find(|(k, _)| k == key).and_then(|(_, v)| v.parse::<f64>().ok());
                for m in &metrics {
                    for key in cols.iter().filter(|(_, x)| x == m) {
                        let full = ns.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_str()).unwrap_or("");
                        let shown = num(key).map(|v| dec(v, places_of(&key.1), false)).unwrap_or_else(|| full.to_string());
                        cells.push_str(&format!("<td class=\"num\" title=\"{}\">{}</td>", esc(full), esc(&shown)));
                    }
                    for (_, other) in diffs.iter().filter(|(x, _)| x == m) {
                        let raw = num(&(other.clone(), m.clone())).zip(num(&(baseline.to_string(), m.clone()))).map(|(a, b)| a - b);
                        let d = raw.map(|x| diff_said(x, places_of(m))).unwrap_or_default();
                        // the full difference on hover, as rb compare would print it
                        let full = raw.map(|x| crate::pyfloat::fmt_num(&Value::Float(x), ".4g")).unwrap_or_default();
                        cells.push_str(&format!("<td class=\"num diff\" title=\"{}\">{}</td>", esc(&full), esc(&d)));
                    }
                }
                if !p_once {
                    cells.push_str(&format!("<td class=\"nw\">{}</td>", esc(&day(text(r, "produced")))));
                }
                if !r_once {
                    cells.push_str(&format!("<td class=\"nw\">{}</td>", esc(text(r, "at").split(' ').next().unwrap_or(""))));
                }
                // a retracted run says so before its receipt's link, so the cell never reads "receipt retracted"
                let tag = if gone { format!("<span class=\"tag\">{}</span> \u{b7} ", w::RUN_RETRACTED) } else { String::new() };
                format!(
                    "<tr{} data-go=\"{}\"{}>{cells}<td class=\"rid\">{tag}{}</td></tr>",
                    if anchors { format!(" id=\"rr-{}\"", esc(rid)) } else { String::new() },
                    esc(&self.run_to(rid)),
                    if gone { " class=\"gone\"" } else { "" },
                    // the row names the run; its last cell opens its receipt, never saying its id
                    a(&self.run_to(rid), w::RECEIPT_LINK, "")
                )
            })
            .collect();
        format!("{quiet}<div class=\"wrap rw\"><table class=\"runs2\"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>")
    }

    fn runs(&self) -> String {
        let d = self.doc;
        let mut h = vec![format!("<h1 id=\"h-runs\">{}</h1><p class=\"lede\">{}</p>", w::RUNS, w::RUNS_LEDE)];
        let all: Vec<(&str, &Value)> = match d.get("runs") {
            Some(Value::Dict(m)) => m.iter().filter(|(_, v)| v.is_dict()).map(|(k, v)| (k.as_str(), v)).collect(),
            _ => Vec::new(),
        };
        let is_gone = |r: &Value| at(r, "retracted").is_some_and(Value::is_dict);
        let gone = all.iter().filter(|(_, r)| is_gone(r)).count() as i64;
        if !all.is_empty() {
            h.push(format!("<p class=\"counts\">{}</p>", esc(&w::runs_stand(all.len() as i64 - gone, gone))));
        }
        let mut order: Vec<String> = list(d, "experiments").iter().map(|x| text(x, "id").to_string()).collect();
        for (_, r) in &all {
            let e = text(r, "experiment_id").to_string();
            if !order.contains(&e) {
                order.push(e);
            }
        }
        // one table per experiment (design pass 1, target 5), its title first and its id trailing
        for eid in &order {
            let mut mine: Vec<(&str, &Value)> = all.iter().filter(|(_, r)| text(r, "experiment_id") == eid).copied().collect();
            if mine.is_empty() {
                continue;
            }
            mine.sort_by_key(|(_, r)| is_gone(r));
            let g = mine.iter().filter(|(_, r)| is_gone(r)).count();
            let title = list(d, "experiments").iter().find(|x| text(x, "id") == eid).map(|x| au(text(x, "title"))).unwrap_or_else(|| esc(eid));
            let n = format!("{}{}", plural((mine.len() - g) as i64, "run", "runs"), if g > 0 { format!(" \u{b7} {g} {}", w::RETRACTED) } else { String::new() });
            let (baseline, units) = self.baseline_units(eid);
            h.push(format!(
                "<div data-group><div class=\"grouphd\"><h2>{title}</h2> {}<span class=\"dim sm\">{n}</span></div>{}</div>",
                a(self.to(&format!("x-{eid}"), "experiments"), &format!("<span class=\"cid\">{}</span>", esc(eid)), ""),
                self.run_table(&mine, &baseline, &units, true)
            ));
        }
        let ans: String = analyses(d).into_iter().map(|an| self.analysis_li(an)).collect();
        if !ans.is_empty() {
            let n = analyses(d).len() as i64;
            h.push(format!(
                "<section id=\"runs-analyses\" aria-labelledby=\"h-runs-analyses\"><h2 id=\"h-runs-analyses\">{} <span class=\"n\">\u{b7} {}</span></h2><ul class=\"plain\">{ans}</ul></section>",
                w::ANALYSES,
                plural(n, "analysis", "analyses")
            ));
        }
        section("runs", &with_project(d, w::RUNS), w::RUNS, "insp-none", &h.concat())
    }

    /// Resources: rb's line, then a row per resource folding what it is: its type and role, how many runs read it and
    /// how many criteria rest on it, its availability in neutral ink, and how to get it again on the row.
    fn resources(&self) -> String {
        let d = self.doc;
        let rows: String = match d.get("resources") {
            Some(Value::Dict(m)) => m.iter().filter(|(_, v)| v.is_dict()).map(|(id, r)| self.resource_row(id, r)).collect(),
            _ => String::new(),
        };
        let rows = if rows.is_empty() { String::new() } else { format!("<div class=\"list\">{rows}</div>") };
        let body = format!("<h1 id=\"h-resources\">{}</h1><p class=\"lede\">{}</p>{rows}", w::RESOURCES, w::RESOURCES_LEDE);
        section("resources", &with_project(d, w::RESOURCES), w::RESOURCES, "insp-none", &body)
    }

    fn resource_row(&self, id: &str, r: &Value) -> String {
        let read = list(r, "read_by.runs").len() as i64;
        let rest = list(r, "rests_on.criteria").len() as i64;
        let mut facts: Vec<String> = [text(r, "type"), text(r, "role")].into_iter().filter(|s| !s.is_empty()).map(esc).collect();
        if read > 0 {
            facts.push(format!("{} {}", w::READ_BY, plural(read, "run", "runs")));
        }
        if rest > 0 {
            facts.push(format!("{} {}", plural(rest, "rule depends", "rules depend"), "on it"));
        }
        let availability = text(r, "availability");
        let availability = if availability == "reconstructible" { term("reconstructible", &esc(availability)) } else { esc(availability) };
        // a recipe's command as code; rb's sentence for one with no command as text
        let command = text(r, "acquisition.command");
        let again = if !command.is_empty() && !self.past {
            format!("<span class=\"cg\">{} \u{b7} <code>{}</code></span>", w::HOW_TO_GET_IT_AGAIN, esc(command))
        } else if !self.said_past(text(r, "how")).is_empty() {
            format!("<span class=\"cg\">{} \u{b7} {}</span>", w::HOW_TO_GET_IT_AGAIN, esc(&self.said_past(text(r, "how"))))
        } else {
            String::new()
        };
        let mut body: Vec<(String, String)> = Vec::new();
        let identity = text(r, "identity");
        if !identity.is_empty() {
            body.push((w::IDENTITY.into(), esc(identity)));
        }
        let wheres = texts(r, "where");
        if !wheres.is_empty() {
            body.push((w::WHERE.into(), format!("<span class=\"mono\">{}</span>", esc(&wheres.join(" \u{b7} ")))));
        }
        let from = text(r, "acquisition.from");
        if !from.is_empty() {
            body.push((w::HOW_TO_GET_IT_AGAIN.into(), format!("<span class=\"mono\">{}</span>", esc(from))));
        }
        let runs: Vec<String> = list(r, "read_by.runs").iter().map(|x| a(&self.run_to(text(x, "run")), &esc(&self.run_said(text(x, "run"))), "")).collect();
        if !runs.is_empty() {
            body.push((w::READ_BY.into(), runs.join(" ")));
        }
        let made: Vec<String> = list(r, "made_by").iter().map(|x| a(&self.run_to(text(x, "run")), &esc(&self.run_said(text(x, "run"))), "")).collect();
        if !made.is_empty() {
            body.push((w::MADE_BY.into(), made.join(" ")));
        }
        let url = text(r, "url");
        if !url.is_empty() {
            body.push((w::URL.into(), format!("<span class=\"mono\">{}</span>", esc(url))));
        }
        let body: String = body.iter().map(|(k, v)| format!("<dt>{}</dt><dd>{v}</dd>", esc(k))).collect();
        format!(
            "<details class=\"nd resrow\" data-row data-for=\"r-{}\" data-fold=\"rs-{}\"><summary class=\"cr\" id=\"rs-{}\"><span class=\"cs\"><span class=\"cid\">{}</span>{}</span>\
             <span class=\"cw\">{availability}</span><span class=\"cv\">{}</span>{again}</summary><div class=\"cbody\"><dl class=\"ax\">{body}</dl></div></details>",
            esc(id),
            esc(id),
            esc(id),
            esc(id),
            au(text(r, "name")),
            facts.join(" \u{b7} ")
        )
    }

    /// History, until its sittings: rb's line, how many writes the log holds, then every write newest first, as the log
    /// keeps it, with no writer: its time and rb's sentence, each object it names a link.
    fn history(&self) -> String {
        let d = self.doc;
        let mut h = vec![format!("<h1 id=\"h-history\">{}</h1><p class=\"lede\">{}</p>", w::HISTORY, w::HISTORY_LEDE)];
        // H's sittings (with their summary) where the document holds them, each record linked only where the page holds it
        let sittings = super::history_view::sittings(d, &|id| self.ids.contains(&esc(id)));
        let summary = text(d, "history.summary");
        if !sittings.is_empty() {
            h.push(sittings);
        } else if !summary.is_empty() {
            h.push(format!("<p class=\"counts\">{}</p>", esc(summary)));
        }
        let mut log: Vec<&Value> = list(d, "history.log").iter().collect();
        log.sort_by_key(|e| std::cmp::Reverse(count(e, "log_index").unwrap_or(0)));
        let rows: String = log
            .iter()
            .map(|e| {
                let n = count(e, "log_index").map(|n| format!(" id=\"log-{n}\"")).unwrap_or_default();
                // rb's sentence with each object it names linked where it says it (a run by its name, an id as it
                // is), then only the objects it does not say, so no row says a run or a call twice
                let forms: Vec<Form> = texts(e, "refs")
                    .iter()
                    .filter_map(|r| {
                        let to = self.object_to(r)?;
                        let n = self.named(r);
                        if n == *r {
                            return Some((vec![n], to, "mono"));
                        }
                        // a run as every line names it (told apart from one whose label reads the same), else by its label
                        let apart = d.get("runs").and_then(|m| m.get(r)).map_or("", |x| text(x, "named"));
                        Some((if apart.is_empty() { vec![n] } else { vec![apart.to_string(), n] }, to, ""))
                    })
                    .collect();
                let (sentence, unsaid) = linked_forms(text(e, "sentence"), &forms);
                let refs: Vec<String> = unsaid.iter().map(|(n, to, c)| a(to, &esc(&n[0]), c)).collect();
                let refs = if refs.is_empty() { String::new() } else { format!("<span class=\"refs\"> \u{b7} {}</span>", refs.join(" ")) };
                format!("<li{n}><span class=\"t\">{}</span> <span>{sentence}</span>{refs}</li>", esc(text(e, "at")))
            })
            .collect();
        if !rows.is_empty() {
            h.push(format!("<h2>{}</h2><ol class=\"hlist log\">{rows}</ol>", w::LOG));
        }
        section("history", &with_project(d, w::HISTORY), w::HISTORY, "insp-none", &h.concat())
    }

    /// The finding of criterion `cid`, if the document holds one.
    fn finding(&self, cid: &str) -> Option<&'a Value> {
        self.doc.get("findings").and_then(|f| f.get(cid)).filter(|f| f.is_dict())
    }

    /// Where a criterion links: its card, else its row in All criteria.
    fn criterion_to(&self, cid: &str) -> String {
        let card = format!("c-{cid}");
        if self.ids.contains(&esc(&card)) {
            card
        } else {
            self.to(&format!("cr-{cid}"), "criteria").to_string()
        }
    }

    /// A part of a criterion's card (`:rule`, `:reading`, `:runs`), else wherever the criterion links.
    fn criterion_part(&self, cid: &str, part: &str) -> String {
        let want = format!("c-{cid}:{part}");
        if self.ids.contains(&esc(&want)) {
            want
        } else {
            self.criterion_to(cid)
        }
    }

    /// The call waiting on the user about `subject`, if one is.
    fn call_on(&self, subject: &str) -> Option<&'a Value> {
        list(self.doc, "calls").iter().find(|c| text(c, "subject") == subject)
    }

    /// The call a criterion's row leads to: the one on the criterion, else, for a missed gate, the direction call on
    /// its experiment that the miss asks for.
    fn row_call(&self, cid: &str, f: &Value) -> Option<&'a Value> {
        self.call_on(cid).or_else(|| {
            let eid = text(f, "experiment");
            (text(f, "role") == "gate" && !eid.is_empty())
                .then(|| list(self.doc, "calls").iter().find(|c| text(c, "subject") == eid && text(c, "code") == "gate_missed"))
                .flatten()
        })
    }

    /// rb's open items saying that `cid`, filed under no question, is linked to none: said in its row's fold.
    fn unfiled_items_on(&self, cid: &str) -> Vec<&'a Value> {
        list(self.doc, "unfiled.open_items").iter().filter(|o| unlinked_item(o, cid)).collect()
    }

    /// Whether anything waits on the user anywhere on the page: a call, a found item or a confirmation.
    fn anything_waits(&self) -> bool {
        !list(self.doc, "calls").is_empty() || count(self.doc, "your_call.n").unwrap_or(0) > 0 || !list(self.doc, "home.found").is_empty()
            || count(self.doc, "home.found_total").unwrap_or(0) > 0 || self.has_pending()
    }

    /// Research: the same cards as the Overview, answers included; the work filed under no question, in rb's words; the
    /// tally, linked to All criteria.
    fn research(&self) -> String {
        let d = self.doc;
        // each question as the Overview's card says it, a sub-question set in under its parent (design pass 1, target 1)
        let cards: String = list(d, "questions").iter().map(|q| self.question_card(q, "rs")).collect();
        let cards = if cards.is_empty() { String::new() } else { format!("<div class=\"cards\" aria-label=\"Questions\">{cards}</div>") };
        let body = format!(
            "<h1 id=\"h-research\">{}</h1><p class=\"lede\">{}</p>{cards}{}",
            w::RESEARCH,
            w::RESEARCH_LEDE,
            self.unfiled(),
        );
        section("research", &with_project(d, w::RESEARCH), w::RESEARCH, "insp-none", &body)
    }

    /// The work filed under no question, under rb's line for it: its criteria as evidence rows, the calls on them, each
    /// led by its reason, and what is open on them. Shown while there is any.
    fn unfiled(&self) -> String {
        let d = self.doc;
        let line = text(d, "unfiled.line");
        let criteria: Vec<String> = texts(d, "unfiled.criteria").into_iter().filter(|c| self.finding(c).is_some()).collect();
        let calls: Vec<&Value> = texts(d, "unfiled.calls").iter().filter_map(|s| self.call_on(s)).collect();
        let open = list(d, "unfiled.open_items");
        // the experiments filed under no question, each by its title, so none is in no inquiry
        let xs: Vec<&Value> = texts(d, "unfiled.experiments").iter().filter_map(|e| list(d, "experiments").iter().find(|x| text(x, "id") == e.as_str())).collect();
        if line.is_empty() || (criteria.is_empty() && calls.is_empty() && open.is_empty() && xs.is_empty()) {
            return String::new();
        }
        let xs: String = xs
            .iter()
            .map(|x| format!("<div class=\"xrow\">{}<div class=\"dim sm\">{}</div></div>", a(self.to(&format!("x-{}", text(x, "id")), "experiments"), &au_title(text(x, "title")), ""), esc(text(x, "who_line"))))
            .collect();
        let rows = if criteria.is_empty() { String::new() } else { self.ev_list(&criteria, "unfiled") };
        // each call and each item once: one a row leads to, or that is about a row's criterion alone, is said there
        let reached: Vec<&str> = criteria.iter().filter_map(|c| self.finding(c).and_then(|f| self.row_call(c, f))).map(|c| text(c, "subject")).collect();
        let calls: Vec<&Value> = calls.into_iter().filter(|c| !reached.contains(&text(c, "subject"))).collect();
        let open: Vec<&Value> = open.iter().filter(|o| !criteria.iter().any(|c| unlinked_item(o, c))).collect();
        let calls = if calls.is_empty() {
            String::new()
        } else {
            format!("<div class=\"calls-in\">{}</div>", calls.iter().map(|c| call_li(c, &self.call_card(c))).collect::<String>())
        };
        let open: String = open.iter().map(|o| format!("<li>{}{}</li>", esc(text(o, "what")), units_after(o))).collect();
        let open = if open.is_empty() { String::new() } else { format!("<ul class=\"oi\">{open}</ul>") };
        format!("<section class=\"unfiled\" id=\"unfiled\" aria-labelledby=\"h-unfiled\"><h2 id=\"h-unfiled\">{}</h2>{xs}{rows}{calls}{open}</section>", esc(line))
    }

    /// A question, meaning first: its text, its status on a line of its own, rb's headline with each qualification,
    /// what is linked here; then the evidence by inquiry, a recorded interpretation's criteria under it (one a session
    /// wrote and the user has not confirmed in rb's note form), the rest apart; its rail (its calls, each once and led
    /// by its reason, the calls made on its work, the experiments behind it, the Next step); its sub-questions; and the
    /// folds that hold each qualification and each reading.
    fn question(&self, q: &Value) -> String {
        let doc = self.doc;
        let id = text(q, "id");
        let qid = format!("q-{id}");
        let parent = text(q, "parent");
        // the breadcrumb names titles, each cut short, its id dim after it (plain words pass 4): every question above
        // this one, from the top
        let mut above: Vec<&Value> = Vec::new();
        let mut up = parent.to_string();
        while !up.is_empty() && above.len() < 8 {
            let Some(p) = list(doc, "questions").iter().find(|k| text(k, "id") == up) else { break };
            above.push(p);
            up = text(p, "parent").to_string();
        }
        let above: String = above
            .iter()
            .rev()
            .map(|p| {
                let pid = text(p, "id");
                let said = crumb_title(text(p, "text"), pid);
                let to = format!("q-{pid}");
                format!("<span class=\"sep\">\u{203a}</span>{}", if self.ids.contains(&esc(&to)) { a(&to, &said, "") } else { said })
            })
            .collect();
        let crumbs = format!(
            "<p class=\"crumb\">{}{above}<span class=\"sep\">\u{203a}</span>{}</p>",
            a("research", w::RESEARCH, ""),
            crumb_title(text(q, "text"), id)
        );
        // its state names its subject, as the card's does (plain words pass 3): "Open question"
        let status = text(q, "status");
        let state = list(doc, "home.standing").iter().find(|s| text(s, "question") == id).map(|s| text(s, "state_word")).filter(|t| !t.is_empty()).unwrap_or(status.split(' ').next().unwrap_or(""));
        let said = w::question_state(state);
        let status = if status.split(' ').next().is_some_and(|s| s.eq_ignore_ascii_case("open")) { term_as("open (question)", &esc(&said)) } else { esc(&said) };
        let mine: Vec<String> = texts(q, "findings").into_iter().filter(|c| self.finding(c).is_some()).collect();
        let mut h = vec![crumbs, format!("<h1 id=\"h-{}\">{}</h1><p class=\"meta\">{status}</p>", esc(&qid), au_title(text(q, "text")))];
        // a question no rule is linked to says one thing under its status (plain words pass 4), never a headline and a
        // box that say it again
        h.push(if mine.is_empty() { self.empty_question(q, &qid) } else { self.evidence_line(q, &qid) });
        h.push(self.relations(q, &qid));
        if !mine.is_empty() {
            h.push(self.evidence(q, id, &qid, &mine));
        }
        h.push(self.question_rail(q, id, &qid));
        let kids: String = list(doc, "questions").iter().filter(|k| text(k, "parent") == id).map(|k| self.question_card(k, &format!("sb-{id}"))).collect();
        if !kids.is_empty() {
            h.push(format!("<h2 class=\"subq\">{}</h2><div class=\"cards\" aria-label=\"{}\">{kids}</div>", w::SUB_QUESTION, w::SUB_QUESTION));
        }
        h.push(self.question_folds(q, id, &qid, &mine));
        format!(
            "<article class=\"view obj\" id=\"{}\" data-nav=\"research\" data-crumb=\"{}\" data-title=\"{}\" data-root=\"{}\" aria-labelledby=\"h-{}\">{}</article>",
            esc(&qid),
            esc(id),
            esc(&with_project(doc, id)),
            esc(&format!("{qid}:rail")),
            esc(&qid),
            h.concat()
        )
    }

    /// rb's headline on the evidence, with its hint, and each qualification linked to where it is held; then what is
    /// linked here, each count of a reading linked to its group.
    fn evidence_line(&self, q: &Value, qid: &str) -> String {
        // as the question's card says it (design pass 1, target 1): rb's line on why, then the rules linked here led by
        // what they are ("Rules: 4 met · 4 not tested"), each linked to its group; the qualifications are in the folds
        let id = text(q, "id");
        let s = list(self.doc, "home.standing").iter().find(|s| text(s, "question") == id);
        let line = s.map(|s| text(s, "line")).filter(|l| !l.is_empty());
        let headline = text(q, "overview.headline");
        let lead = match line {
            Some(l) => format!("<p class=\"roll\"><span class=\"hl\">{}</span></p>", self.voiced(&esc(l))),
            None if !headline.is_empty() => format!("<p class=\"roll\">{}</p>", term_as("headline", &format!("<span class=\"hl\">{}</span>", esc(headline)))),
            None => String::new(),
        };
        let tallies = self.tallies(q, s, qid).replacen("class=\"stt\"", "class=\"roll stt\"", 1);
        format!("{lead}{tallies}")
    }

    /// rb's list of met words that count a finding not drawn green, for question `id`.
    fn muted_of(&self, id: &str) -> Vec<String> {
        list(self.doc, "home.tree").iter().find(|r| text(r, "kind") == "question" && text(r, "id") == id).map(|r| texts(r, "muted")).unwrap_or_default()
    }

    /// A question nothing is linked to directly, in one sentence (plain words pass 4): no rule tests it yet, never a
    /// headline, a standing line or tallies beside it (a top-level question's count its sub-questions' rules); then,
    /// where the viewer may act, the command rb status gives for it (its open item's `do`), and the notes that name it
    /// (`cited_by`). No link to another view's notes.
    fn empty_question(&self, q: &Value, qid: &str) -> String {
        let id = text(q, "id");
        // rb's next step for such a question: the command its open item carries, never one of the view's own
        let next = list(self.doc, "open_items")
            .iter()
            .flat_map(|g| list(g, "items").iter())
            .find(|o| text(o, "code") == "open_question" && text(o, "subject") == id)
            .map(|o| text(o, "do"))
            .filter(|d| !d.is_empty())
            .map(|d| self.act(self.cmd(d)))
            .unwrap_or_default();
        let notes = self.notes_naming(q);
        // rb's one reading of it, the line its Overview card says too (M's request 135): a parent whose rules are linked to
        // its sub-questions says so, never that no rule tests it
        let said = Some(text(q, "no_rule")).filter(|t| !t.is_empty()).unwrap_or(w::NO_RULE_TESTS);
        // in each viewer's voice on the export, as its Overview card says it
        format!("<p class=\"roll\" id=\"{}\"><span class=\"hl\">{}</span></p>{next}{notes}", esc(&format!("{qid}:evidence")), self.voiced(&esc(said)))
    }

    /// The notes whose words name question `q` (its `cited_by`, each a note: a project note by its id, an experiment's
    /// by its `text`), listed on its page under their own heading, each in its voice; "" with none.
    fn notes_naming(&self, q: &Value) -> String {
        let items: String = list(q, "cited_by")
            .iter()
            .filter(|c| text(c, "kind") == "note")
            .filter_map(|c| match list(self.doc, "home.notes").iter().find(|n| !text(c, "id").is_empty() && text(n, "id") == text(c, "id")) {
                Some(n) => Some(self.note_li(n)),
                None if !text(c, "text").is_empty() => Some(format!("<li class=\"note pnote\">{}</li>", self.au_cited(text(c, "text"), "", ""))),
                None => None,
            })
            .collect();
        if items.is_empty() {
            return String::new();
        }
        format!("<h2 class=\"qnh\">{}</h2><ul class=\"notes plain\">{items}</ul>", w::NOTES_ABOUT_QUESTION)
    }

    /// rb's computed comparison, when the question has one: its sentence as the answer, then each pair of findings
    /// with rb's word between them, and rb's line on their order.
    fn relations(&self, q: &Value, qid: &str) -> String {
        let rels = list(q, "overview.relations");
        if rels.is_empty() {
            return String::new();
        }
        let said = rels.iter().map(|r| text(r, "summary")).find(|s| !s.is_empty()).unwrap_or("");
        let pairs: String = rels
            .iter()
            .flat_map(|r| {
                let word = Some(text(r, "kind")).filter(|s| !s.is_empty()).unwrap_or("reverses");
                list(r, "comparisons").iter().map(move |c| (word, text(c, "source"), text(c, "target")))
            })
            .map(|(word, s, t)| format!("<div class=\"pair\">{}<div class=\"pv\">{}</div>{}</div>", self.pair_card(s), esc(word), self.pair_card(t)))
            .collect();
        // each declaration the comparison rests on, as declared, where the document hands them (never who declared it)
        let decls: String = rels
            .iter()
            .flat_map(|r| list(r, "declarations").iter())
            .filter(|d| d.is_dict())
            .map(|d| {
                let rows: String = d
                    .as_dict()
                    .into_iter()
                    .flat_map(|m| m.iter())
                    // never who declared it, and on a page anyone may be sent never the words given with it
                    .filter(|(k, v)| !WRITER_KEYS.contains(&k.as_str()) && (self.surface == Surface::Open || k.as_str() != "words") && v.truthy())
                    .map(|(k, v)| format!("<dt>{}</dt><dd>{}</dd>", esc(k), esc(&v.as_str().map(str::to_string).unwrap_or_else(|| crate::pyjson::dumps(v)))))
                    .collect();
                format!("<dl class=\"ax\">{rows}</dl>")
            })
            .collect();
        let decls = if decls.is_empty() {
            String::new()
        } else {
            format!("<details class=\"fold\" data-fold=\"{}\"><summary>{}</summary>{decls}</details>", esc(&format!("{qid}:rel:declared")), w::DECLARATION_AND_SCOPE)
        };
        format!(
            "<section class=\"rel\" id=\"{}\"><p class=\"by\">{}</p><h2 class=\"said\">{}</h2><div class=\"pairs\">{pairs}</div><p class=\"dim sm\">{}</p>{decls}</section>",
            esc(&format!("{qid}:rel")),
            term(w::COMPUTED_COMPARISON, w::COMPUTED_COMPARISON),
            esc(said),
            w::DIRECTIONS_ALIGNED
        )
    }

    /// One side of a computed comparison, read as an evidence row is: the experiment's short title, the statement, the
    /// state word alone in colour, the reading with its result word, the units line, then the id.
    fn pair_card(&self, cid: &str) -> String {
        let Some(f) = self.finding(cid) else { return format!("<div class=\"pc\"><p class=\"cid\">{}</p></div>", esc(cid)) };
        let x = list(self.doc, "experiments").iter().find(|x| text(x, "id") == text(f, "experiment"));
        let short = x.map(|x| Some(text(x, "short_title")).filter(|s| !s.is_empty()).unwrap_or(text(x, "title"))).unwrap_or("");
        let short = if short.is_empty() { String::new() } else { format!("<p class=\"px\">{}</p>", au(short)) };
        format!(
            "<div class=\"pc\" data-go=\"{}\">{short}{}<p class=\"pl\">{}</p><p class=\"pl evv\">{}</p><p class=\"pl evu\">{}</p><p class=\"cid\">{}</p></div>",
            esc(&self.criterion_to(cid)),
            self.au_cited(text(f, "statement"), &self.criterion_to(cid), "stm"),
            self.state_word(cid, f),
            self.reading_line(cid, f),
            self.units_line(cid, f),
            esc(cid)
        )
    }

    /// The evidence by inquiry: a group per recorded interpretation, the one resting on the most criteria first, its
    /// criteria under it by role; then the criteria no interpretation names. A question with neither an interpretation
    /// nor a computed comparison says so where its answer would be.
    fn evidence(&self, q: &Value, id: &str, qid: &str, mine: &[String]) -> String {
        // rules written after their results, counted once at the head; a row carries the tag only as the exception (P6)
        let fs: Vec<&Value> = mine.iter().filter_map(|c| self.finding(c)).collect();
        let (late, most_late) = late_head(&fs);
        self.most_late.set(most_late);
        let out = self.evidence_groups(q, id, qid, mine);
        self.most_late.set(false);
        let late = if late.is_empty() { String::new() } else { format!("<p class=\"quiet late\">{}</p>", term_as("rule written after the results", &esc(&late))) };
        out.replacen("<!--tags-->", &late, 1)
    }

    fn evidence_groups(&self, q: &Value, id: &str, qid: &str, mine: &[String]) -> String {
        let ips = list(q, "overview.interpretations");
        let mut order: Vec<usize> = (0..ips.len()).collect();
        // what the user confirmed leads; a session's note never reads above it
        order.sort_by_key(|&i| (!at(&ips[i], "confirmed").is_some_and(Value::truthy), std::cmp::Reverse(list(&ips[i], "criteria").len())));
        let mut placed: HashSet<String> = HashSet::new();
        let mut groups = String::new();
        for i in order {
            let ip = &ips[i];
            let cs: Vec<String> = by_role(self, mine.iter().filter(|c| texts(ip, "criteria").contains(c) && !placed.contains(*c)).cloned().collect());
            placed.extend(cs.iter().cloned());
            let active = at(ip, "active").map_or(true, Value::truthy);
            let confirmed = at(ip, "confirmed").is_some_and(Value::truthy);
            // a recorded interpretation is a note (plain words pass 3, D3): "A note, not confirmed: <its words>." with
            // its state in the line, then whether its rule holds as a sentence of its own; never a tag
            let said = self.note_line("said note", text(ip, "text"), text(ip, "holds"), confirmed, "").replacen("<p ", "<h3 ", 1);
            let said = format!("{}</h3>", said.strip_suffix("</p>").unwrap_or(&said));
            let declared = text(ip, "declared");
            let when = if declared.is_empty() {
                String::new()
            } else {
                format!("<details class=\"ipm\" data-fold=\"{}\"><summary>{}</summary><p>{}</p></details>", esc(&format!("{qid}:ip:{}:when", i + 1)), w::WHEN_AND_WHY, esc(declared))
            };
            // the calls on its criteria, each in rb's line, beside the interpretation, which a call does not change
            // (a call made on the question's work is said here once, its session's note drawn as its words, and its rail's
            // Calls made leaves it out)
            let calls: String = texts(ip, "calls")
                .iter()
                .map(|c| match self.made_in_group(id, c).filter(|m| !text(m, "note").is_empty()) {
                    Some(m) => format!("<p class=\"ipcall\"><span>{}</span>{}</p>", esc(&format!("Call on {}: ", text(m, "subject"))), self.said_with_note(text(m, "line"), text(m, "note"))),
                    None => format!("<p class=\"ipcall\">{}</p>", esc(c)),
                })
                .collect();
            groups.push_str(&format!(
                "<section class=\"igroup{}{}\" id=\"{}\">{said}{calls}{when}{}</section>",
                if active { "" } else { " cond" },
                if confirmed { "" } else { " unconfirmed" },
                esc(&format!("{qid}:ip:{}", i + 1)),
                self.ev_list_held(&cs, id, !declared.is_empty())
            ));
        }
        let rest = by_role(self, mine.iter().filter(|c| !placed.contains(*c)).cloned().collect());
        if !rest.is_empty() {
            let head = if !ips.is_empty() {
                format!("<h3 class=\"by\">{}</h3>", w::NOT_IN_AN_INTERPRETATION)
            } else if list(q, "overview.relations").is_empty() {
                format!("<h3 class=\"said none\">{}</h3>", w::NO_INTERPRETATION_YET)
            } else {
                String::new()
            };
            groups.push_str(&format!("<section class=\"igroup rest\">{head}{}</section>", self.ev_list(&rest, id)));
        }
        format!(
            "<h2 class=\"evh2\" id=\"{}\">{} <span class=\"n\">({})</span></h2><!--tags-->{groups}",
            esc(&format!("{qid}:evidence")),
            w::ITS_CRITERIA,
            mine.len()
        )
    }

    fn ev_list(&self, cids: &[String], qid: &str) -> String {
        self.ev_list_held(cids, qid, false)
    }

    /// The rows of a list; `held` where the interpretation group above them holds the question link's when and why
    /// ("when and why"), so no row's disclosure says it again (SHELL-SPEC section 16's first saving).
    fn ev_list_held(&self, cids: &[String], qid: &str, held: bool) -> String {
        format!("<ul class=\"evl\">{}</ul>", cids.iter().filter_map(|c| self.finding(c).map(|f| self.ev_row(c, f, qid, held))).collect::<String>())
    }

    /// An evidence row, compact (OVERVIEW-V2): the claim and rb's state word; rb's reading, the baseline or reference
    /// labelled as one (`reading_short`, contract 1.6) or else its estimate set apart from its interval and unit with the
    /// result word dim after it, then at most two tags and, while a call on it waits, the link to that call; the units it
    /// rests on, rb's reason on a miss and the rest of the machinery one disclosure away.
    fn ev_row(&self, cid: &str, f: &Value, qid: &str, held: bool) -> String {
        // rb's reading in a plain sentence (design pass 1, P8), rb's own form in Details; the one timing tag only where
        // this row is the exception (P6)
        let plain = plain_reading(f);
        let plain = if plain.is_empty() { text(f, "reading_short").to_string() } else { plain };
        let reading = if plain.is_empty() { self.reading_line(cid, f) } else { a(&self.criterion_part(cid, "reading"), &esc(&plain), "v rs") };
        let call = self.row_call(cid, f).map(|c| format!(" {}", a(&self.call_card(c), self.calls_word(), "callk"))).unwrap_or_default();
        let units = self.units_line_bare(cid, f);
        let units = if units.is_empty() { String::new() } else { format!("<p class=\"evu\">{units}</p>") };
        // rb's own form of the reading, with its full precision and result word, one disclosure away (P8)
        let units = format!("{}{units}", self.rb_reading(cid, f));
        format!(
            "<li class=\"evr\" id=\"{}\" data-go=\"{}\" data-row><div class=\"evh\">{}{}</div><p class=\"evv\">{reading}{}{call} <span class=\"cid\">{}</span></p>\
             <details class=\"evm\" data-fold=\"{}\"><summary>{}<span class=\"sr\"> of {}</span></summary>{units}{}</details></li>",
            esc(&format!("f-{cid}:{qid}")),
            esc(&self.criterion_to(cid)),
            self.au_cited(text(f, "statement"), &self.criterion_to(cid), "stm"),
            self.state_word(cid, f),
            timing_tag(f, self.most_late.get()),
            esc(cid),
            esc(&format!("f-{cid}:{qid}:details")),
            w::DETAILS,
            esc(cid),
            self.machinery(cid, f, qid, held)
        )
    }

    /// rb's state word, the rule axis of the standing, linked to the declared criterion: coloured as rb colours it, a
    /// met word on a finding not drawn green dim, and a gate's word neutral (a gate is policy, not a finding).
    fn state_word(&self, cid: &str, f: &Value) -> String {
        let (state, cls) = state_of(f);
        a(&self.criterion_part(cid, "rule"), &said_plain(state), &format!("sw {cls} evs"))
    }

    /// rb's reading: the estimate, its interval and unit as rb hands them apart, then the result word dim after it; with
    /// no parts, rb's numbers as they are. An integrity problem follows in rb's words.
    fn reading_line(&self, cid: &str, f: &Value) -> String {
        let value = value_html(f);
        let value = if value.is_empty() { value } else { a(&self.criterion_part(cid, "reading"), &value, "v") };
        value + &result_after(f)
    }

    /// The units the reading rests on, in plain words, linked to the runs; on a miss, rb's reason; and, while a call on
    /// it waits, a link to that call.
    fn units_line(&self, cid: &str, f: &Value) -> String {
        let mut out = self.units_line_bare(cid, f);
        if let Some(c) = self.row_call(cid, f) {
            out.push_str(&format!(" {}", a(&self.call_card(c), self.calls_word(), "callk")));
        }
        out
    }

    /// The units the reading rests on, linked to the runs, and on a miss rb's reason: what a compact row folds.
    fn units_line_bare(&self, cid: &str, f: &Value) -> String {
        let units = text(f, "units_line");
        let mut out = if units.is_empty() { String::new() } else { a(&self.criterion_part(cid, "runs"), &esc(units), "") };
        let detail = text(f, "rule_detail");
        if !detail.is_empty() {
            out.push_str(&format!("<span class=\"rdt\"> \u{b7} {}</span>", a(&self.criterion_part(cid, "rule"), &esc(&nb(detail)), "")));
        }
        out
    }

    /// What sits behind a row's disclosure, in rb's words: the criterion (its id, role and experiment), the rule as rb
    /// reads it and as declared, rb's comparison, the units, the timing (with a unit amended after the lock, linked to
    /// that amendment and its why), integrity when it is not ok, the question link's time (where no group above
    /// holds it), and a waiting call.
    fn machinery(&self, cid: &str, f: &Value, qid: &str, held: bool) -> String {
        let eid = text(f, "experiment");
        let x = if eid.is_empty() { String::new() } else { format!(" \u{b7} on {}", a(self.to(&format!("x-{eid}"), "experiments"), &esc(eid), "")) };
        let role = text(f, "role");
        let role = if role.is_empty() { String::new() } else { format!(" \u{b7} {}", esc(role)) };
        let cited = if at(f, "cited").is_some_and(Value::is_dict) { format!(" \u{b7} {}", w::CITED) } else { String::new() };
        let mut rows = vec![("rule", format!("{}{role}{x}{cited}", a(&self.criterion_to(cid), &esc(cid), "mono")))];
        let mut rule = said_lead(text(f, "says.rule"));
        for r2 in [text(f, "rule").to_string(), rule_and_margin(f)] {
            if !r2.is_empty() {
                rule.push_str(&format!("<span class=\"r2\">{}</span>", esc(&nb(&r2))));
            }
        }
        rows.push(("as declared", rule));
        rows.push(("reading", esc(text(f, "says.comparison"))));
        rows.push(("units", esc(text(f, "says.units"))));
        let mut timing = esc(text(f, "says.timing"));
        if let Some(am) = at(f, "unit_amended").filter(|v| v.is_dict()) {
            let to = format!("x-{}", text(am, "amendment"));
            let to = self.to(&to, &format!("x-{eid}")).to_string();
            let line = esc(text(am, "line"));
            let line = if self.ids.contains(&esc(&to)) { a(&to, &line, "") } else { line };
            let why = text(am, "why");
            let why = if why.is_empty() { String::new() } else { format!("<span> \u{b7} why: </span>{}", self.au_cited(why, "", "")) };
            timing.push_str(&format!("<span class=\"r2\">{line}{why}</span>"));
        }
        rows.push(("timing", timing));
        let integrity = text(f, "says.integrity");
        if !integrity.is_empty() && integrity != "ok" {
            rows.push(("checks", esc(integrity)));
        }
        if let Some(link) = list(f, "receipt_rows").iter().filter_map(Value::as_list).filter(|_| !held).find(|r| {
            r.first().and_then(Value::as_str) == Some("question link") && r.get(2).and_then(Value::as_list).is_some_and(|refs| refs.iter().any(|x| x.as_str() == Some(qid)))
        }) {
            rows.push(("question link", esc(link.get(1).and_then(Value::as_str).unwrap_or(""))));
        }
        if qid == "unfiled" {
            let items: Vec<String> = self.unfiled_items_on(cid).iter().map(|o| esc(text(o, "what"))).collect();
            rows.push(("question link", items.join("<br>")));
        }
        if let Some(c) = self.row_call(cid, f) {
            rows.push(("review", a(&self.call_card(c), &self.calls_name(), "")));
        }
        let rows: String = rows.into_iter().filter(|(_, v)| !v.is_empty()).map(|(k, v)| format!("<dt>{k}</dt><dd>{v}</dd>")).collect();
        format!("<dl class=\"ax evd\">{rows}</dl>")
    }

    /// A question's rail: its calls, each once and led by its reason; the calls made on its work, each in rb's line;
    /// the experiments behind it, each with its who line and where Reproduce is; and the Next step, in words.
    fn question_rail(&self, q: &Value, id: &str, qid: &str) -> String {
        let doc = self.doc;
        let calls: Vec<&Value> = texts(q, "calls").iter().filter_map(|s| self.call_on(s)).collect();
        // Your call heads the rail while a call waits here, or while nothing waits anywhere (rb's line says so); else
        // the rail opens on the experiments behind it, with no empty "Your call (0)" above them
        let mut r = Vec::new();
        if !calls.is_empty() {
            let rows: String = calls.iter().map(|c| call_li(c, &self.call_card(c))).collect();
            r.push(format!("<h2 id=\"calls-{}\">{} ({})</h2>", esc(id), self.calls_name(), calls.len()));
            let how = if self.surface == Surface::Export {
                by_viewer("p", " class=\"honest\"", w::OPEN_CALLS_LEDE, w::OWNER_CALLS_LEDE, w::READER_CALLS_LEDE)
            } else {
                format!("<p class=\"honest\">{}</p>", w::CALL_IN_YOUR_TERMINAL)
            };
            r.push(format!("<div class=\"calls-in\">{rows}</div>{how}"));
        } else if !self.anything_waits() {
            r.push(format!("<h2 id=\"calls-{}\">{} (0)</h2>", esc(id), self.calls_name()));
            r.push(if self.surface == Surface::Export {
                by_viewer("p", " class=\"dim sm\"", w::NO_CALL_OPEN, w::NOTHING_WAITS_HERE, w::NO_CALL_OPEN)
            } else {
                format!("<p class=\"dim sm\">{}</p>", w::NOTHING_WAITS_HERE)
            });
        }

        let in_groups: Vec<String> = list(q, "overview.interpretations").iter().flat_map(|ip| texts(ip, "calls")).collect();
        let made: String = list(doc, "made_calls")
            .iter()
            .filter(|m| text(m, "question") == id && !in_groups.iter().any(|c| self.made_in_group(id, c).is_some_and(|g| std::ptr::eq(g, *m))))
            .map(|m| self.made_call(m, id))
            .collect();
        if !made.is_empty() {
            let h = if r.is_empty() { format!("<h2 id=\"calls-{}\">{}</h2>", esc(id), w::CALLS_MADE) } else { format!("<h3>{}</h3>", w::CALLS_MADE) };
            r.push(format!("{h}<ul class=\"made plain\">{made}</ul>"));
        }
        let xs: String = texts(q, "experiments")
            .iter()
            .filter_map(|eid| list(doc, "experiments").iter().find(|x| text(x, "id") == eid))
            .map(|x| {
                let eid = text(x, "id");
                let ready = ready_said(at(x, "reproduce"));
                let repro = format!("x-{eid}:reproduce");
                let repro = if self.ids.contains(&esc(&repro)) && !ready.is_empty() { format!("<div class=\"sm\">{}</div>", a(&repro, &ready, "")) } else { String::new() };
                format!(
                    "<div class=\"xrow\">{}<div class=\"dim sm\">{}</div>{repro}</div>",
                    a(self.to(&format!("x-{eid}"), "experiments"), &au_title(text(x, "title")), ""),
                    esc(&who_said(text(x, "who_line")))
                )
            })
            .collect();
        let lead = r.is_empty();
        r.push(if lead { format!("<h2 id=\"calls-{}\">{}</h2>", esc(id), w::EXPERIMENTS_BEHIND) } else { format!("<h3>{}</h3>", w::EXPERIMENTS_BEHIND) });
        r.push(if xs.is_empty() { format!("<p class=\"dim sm\">{}</p>", w::NO_EXPERIMENT_HERE) } else { xs });
        let next = text(q, "next_step");
        if !next.is_empty() {
            r.push(format!("<h3>{}</h3>{}", w::NEXT_STEP, self.prompt(next)));
        }
        format!(
            "<section class=\"qrail\" id=\"{}\" data-rail data-rootname=\"{rail}\" aria-label=\"{rail}\">{}</section>",
            esc(&format!("{qid}:rail")),
            r.concat(),
            // headed by what it shows (plain words pass 4): the calls while one waits here, else the question
            rail = esc(&if calls.is_empty() { cut_title(text(q, "text")) } else { format!("{} \u{b7} {id}", self.calls_word()) })
        )
    }

    /// The call made on question `id`'s work that an interpretation group's line `c` says ("Call on <subject>: <its
    /// line>"): drawn there, beside the interpretation, and not again under Calls made.
    fn made_in_group(&self, id: &str, c: &str) -> Option<&'a Value> {
        list(self.doc, "made_calls").iter().find(|m| text(m, "question") == id && c == format!("Call on {}: {}", text(m, "subject"), text(m, "line")))
    }

    /// A call made on a question's work, once: what it is on, then rb's line, the note a session gave in it drawn as its
    /// words, where rb hands the note apart.
    fn made_call(&self, m: &Value, qid: &str) -> String {
        let s = text(m, "subject");
        let row = format!("f-{s}:{qid}");
        // what the call is on trails as its id, and opens it (design pass 1, P3 and P11): its row here, else its own
        // place in the record (a setting's experiment settings)
        let to = if self.ids.contains(&esc(&row)) {
            Some(row)
        } else {
            self.object_to(s).or_else(|| s.split_once('/').map(|(e, _)| format!("x-{e}:settings")).filter(|t| self.ids.contains(&esc(t))))
        };
        let chip = format!("<span class=\"cid\">{}</span>", esc(s));
        let on = match to {
            Some(to) => a(&to, &chip, ""),
            None => chip,
        };
        let said = self.said_with_note(text(m, "line"), text(m, "note"));
        format!("<li>{said} {on}</li>")
    }

    /// The folds under a question: each qualification with what it means and the criteria that carry it; each reading
    /// with its criteria. A card C's nodes draw for one stands in its place.
    fn question_folds(&self, q: &Value, id: &str, qid: &str, mine: &[String]) -> String {
        let mut out = String::new();
        let quals: String = list(q, "overview.limits")
            .iter()
            .enumerate()
            .filter(|(i, _)| !self.card_ids.contains(&esc(&format!("{qid}:lim:{i}"))))
            .map(|(i, l)| {
                let label = &w::plain_word(text(l, "label"));
                let means = w::hint(label).map(|h| format!("<p class=\"dim sm\">{}</p>", esc(h))).unwrap_or_default();
                format!(
                    "<div class=\"qual\" id=\"{}\"><p class=\"qh\">{}</p>{means}{}</div>",
                    esc(&format!("{qid}:lim:{i}")),
                    term(label, &esc(label)),
                    self.criteria_links(&texts(l, "criteria"), id)
                )
            })
            .collect();
        if !quals.is_empty() {
            out.push_str(&format!("<details class=\"fold\" data-fold=\"{}\"><summary>{}</summary>{quals}</details>", esc(&format!("{qid}:quals")), w::WHICH_COMPARISONS));
        }
        let mut readings: Vec<(String, String, Vec<String>)> = Vec::new();
        for c in mine {
            let state = self.finding(c).map(|f| text(f, "standing").split(" \u{b7} ").next().unwrap_or("").to_string()).unwrap_or_default();
            if state.is_empty() {
                continue;
            }
            let key = reading_key(&state);
            match readings.iter_mut().find(|(k, _, _)| *k == key) {
                Some((_, _, cs)) => cs.push(c.clone()),
                None => readings.push((key, state, vec![c.clone()])),
            }
        }
        let groups: String = readings
            .iter()
            .filter(|(k, _, _)| !self.card_ids.contains(&esc(&format!("{qid}:rd:{k}"))))
            .map(|(k, state, cs)| {
                format!(
                    "<div class=\"rdg\" id=\"{}\"><p class=\"qh\">{} <span class=\"n\">\u{b7} {}</span></p>{}</div>",
                    esc(&format!("{qid}:rd:{k}")),
                    said_plain(state),
                    cs.len(),
                    self.criteria_links(cs, id)
                )
            })
            .collect();
        if !groups.is_empty() {
            out.push_str(&format!("<details class=\"fold\" data-fold=\"{}\"><summary>{}</summary>{groups}</details>", esc(&format!("{qid}:readings")), w::BY_READING));
        }
        out
    }

    /// Criteria as a list of links to their rows on question `qid`, each with its statement.
    fn criteria_links(&self, cids: &[String], qid: &str) -> String {
        let items: String = cids
            .iter()
            .map(|c| {
                let row = format!("f-{c}:{qid}");
                let to = if self.ids.contains(&esc(&row)) { row } else { self.criterion_to(c) };
                let statement = self.finding(c).map(|f| format!(" {}", self.au_cited(text(f, "statement"), "", ""))).unwrap_or_default();
                format!("<li>{}{statement}</li>", a(&to, &esc(c), "mono"))
            })
            .collect();
        format!("<ul class=\"plain\">{items}</ul>")
    }

    /// All criteria: the tally, a filter where the script runs, then every criterion grouped by experiment in rb's
    /// order, each a row folding what rb says of it.
    fn criteria(&self) -> String {
        let d = self.doc;
        let n = count(d, "home.counts.criteria").map(|n| format!(" <span class=\"n\">({n})</span>")).unwrap_or_default();
        let crumb = format!("<p class=\"crumb\">{}<span class=\"sep\">\u{203a}</span>{}</p>", a("research", w::RESEARCH, ""), w::ALL_CRITERIA);
        // the display noun with its line: commands call a rule a criterion (plain words pass 1)
        let mut h = vec![crumb, format!("<h1 id=\"h-criteria\">{}{n}</h1>", term_as("rule", w::ALL_CRITERIA)), self.tally_words()];
        // rules written after their results, counted once here; a row carries the tag only as the exception (P6)
        let all: Vec<&Value> = match d.get("findings") {
            Some(Value::Dict(m)) => m.values().collect(),
            _ => Vec::new(),
        };
        let (late, most_late) = late_head(&all);
        if !late.is_empty() {
            h.push(format!("<p class=\"quiet late\">{}</p>", term_as("rule written after the results", &esc(&late))));
        }
        if self.surface != Surface::Export {
            h.push(format!(
                "<div class=\"filterbar app-only\"><input type=\"search\" aria-label=\"{}\" placeholder=\"{}\"><kbd>/</kbd></div>",
                w::FILTER_CRITERIA,
                w::FILTER_HINT
            ));
        }
        let findings: Vec<(&str, &Value)> = match d.get("findings") {
            Some(Value::Dict(m)) => m.iter().map(|(k, v)| (k.as_str(), v)).collect(),
            _ => Vec::new(),
        };
        let mut shown: HashSet<&str> = HashSet::new();
        let mut groups: Vec<(String, String, Vec<&str>)> = Vec::new();
        for x in list(d, "experiments") {
            let eid = text(x, "id");
            let mut cs: Vec<&str> = texts(x, "findings").iter().filter_map(|c| findings.iter().find(|(k, _)| k == c).map(|(k, _)| *k)).collect();
            let more: Vec<&str> = findings.iter().filter(|(k, f)| text(f, "experiment") == eid && !cs.contains(k)).map(|(k, _)| *k).collect();
            cs.extend(more);
            shown.extend(cs.iter().copied());
            if !cs.is_empty() {
                groups.push((eid.to_string(), text(x, "title").to_string(), cs));
            }
        }
        for (k, f) in &findings {
            if !shown.contains(k) {
                let eid = text(f, "experiment");
                match groups.iter_mut().find(|(e, _, _)| e == eid) {
                    Some((_, _, cs)) => cs.push(k),
                    None => groups.push((eid.to_string(), String::new(), vec![k])),
                }
            }
        }
        for (eid, title, cs) in &groups {
            let title = if title.is_empty() { esc(eid) } else { au(title) };
            let title = if title.is_empty() { String::new() } else { format!("<h2>{title}</h2>") };
            let id = if eid.is_empty() { String::new() } else { a(self.to(&format!("x-{eid}"), "experiments"), &esc(eid), "id") };
            let rows: String = cs.iter().filter_map(|c| self.finding(c).map(|f| self.criterion_row(c, f, most_late, &format!("cr-{c}")))).collect();
            h.push(format!(
                "<div data-group><div class=\"grouphd\">{title}{id}<span class=\"dim sm\">{}</span></div><div class=\"list\">{rows}</div></div>",
                plural(cs.len() as i64, "rule", "rules")
            ));
        }
        section("criteria", &with_project(d, w::ALL_CRITERIA), w::ALL_CRITERIA, "insp-none", &h.concat())
    }

    /// A rule in Rules (design pass 1, target 3): its sentence with its state on the right; under it rb's reading in a
    /// plain sentence, the one timing tag where this row is the exception, and its id trailing; the machinery rb says of
    /// it one disclosure away.
    /// rb's own form of a rule's reading ("int8 against the fp32 baseline: -0.0029 [-0.0046, -0.0012] top1 · loss"), as
    /// rb gives it, for a row's Details (design pass 1, P8: the plain sentence leads, rb's form stays one click down).
    fn rb_reading(&self, cid: &str, f: &Value) -> String {
        let short = text(f, "reading_short");
        let said = if short.is_empty() { self.reading_line(cid, f) } else { esc(&nb(short)) + &result_word(f) };
        if said.is_empty() { String::new() } else { format!("<p class=\"evu rbr\">{said}</p>") }
    }

    /// `key` is the row's id and fold key ("cr-c1" in Rules, "x-e1:cr-c1" in its experiment), one per page.
    fn criterion_row(&self, cid: &str, f: &Value, most_late: bool, key: &str) -> String {
        let plain = plain_reading(f);
        let plain = if plain.is_empty() { esc(text(f, "reading_short")) } else { esc(&plain) };
        format!(
            "<details class=\"nd krow\" data-row data-for=\"c-{}\" data-fold=\"{}\"><summary class=\"cr\" id=\"{}\"><span class=\"cs\">{}</span>\
             <span class=\"cw\"><span class=\"sw {}\">{}</span></span><span class=\"cv\"><span class=\"pr\">{plain}</span>{} <span class=\"cid\">{}</span></span></summary>\
             <div class=\"cbody\">{}{}<p class=\"evu\">{}</p></div></details>",
            esc(cid),
            esc(key),
            esc(key),
            self.au_cited(text(f, "statement"), "", ""),
            state_of(f).1,
            said_plain(state_of(f).0),
            timing_tag(f, most_late),
            esc(cid),
            self.rb_reading(cid, f),
            self.machinery(cid, f, "", false),
            self.units_line(cid, f),
        )
    }

    /// The tally alone: rb's standing words with their counts, coloured.
    fn tally_words(&self) -> String {
        let said = rule_tally(list(self.doc, "home.tally"), &texts(self.doc, "home.muted"));
        if said.is_empty() { String::new() } else { format!("<p class=\"quiet\">{said}</p>") }
    }

    /// What the calls are called on this surface: "Your call", or on the export "Open calls" (polish 1, Oct 22 00:10).
    fn calls_name(&self) -> String {
        if self.surface == Surface::Export { by_viewer("span", "", w::OPEN_CALLS, w::YOUR_CALL, w::OPEN_CALLS) } else { w::YOUR_CALL.to_string() }
    }

    /// The calls' name in an attribute (a view's title and crumb), which holds no markup: the export's own word.
    fn calls_word(&self) -> &'static str {
        if self.surface == Surface::Export { w::OPEN_CALLS } else { w::YOUR_CALL }
    }



    /// "Your call (3)", its count the user's calls (your_call.n); on the export "Open calls (3)" (polish 1: a page that
    /// leaves the machine cannot know who reads it).
    fn your_call_head(&self) -> String {
        match count(self.doc, "your_call.n") {
            Some(n) => format!("{} ({n})", self.calls_word()),
            None => self.calls_word().to_string(),
        }
    }

    /// The same head as markup: on the export, each viewer's name for the calls (OWNER-READER).
    fn your_call_head_html(&self) -> String {
        match count(self.doc, "your_call.n") {
            Some(n) => format!("{} ({n})", self.calls_name()),
            None => self.calls_name(),
        }
    }

    fn has_pending(&self) -> bool {
        !list(self.doc, "pending.rows").is_empty()
    }

    fn has_open_items(&self) -> bool {
        !list(self.doc, "open_items").is_empty() || !list(self.doc, "unfiled.open_items").is_empty()
    }

    /// The Overview (OVERVIEW-V2.md): not the record but its projection onto the user's calls, top to bottom. The title
    /// and one line (the place, the recorded runs, when the record last changed); a standing card per top-level
    /// question; Evidence integrity, only when something weakens the whole body of evidence; Needs your judgment, the
    /// one queue; the research questions, a row each; then, folded, how rb reads this repository, the recent changes and
    /// the project's notes. Its inspector shows its empty state, never a second copy of the queue.
    fn overview(&self) -> String {
        let d = self.doc;
        let title = text(d, "project.title");
        let mut h = vec![format!("<h1 id=\"h-overview\">{}</h1>", au_title(title)), self.overview_line()];
        h.extend(self.standings());
        h.push(self.integrity());
        h.push(self.queue_section());
        h.push(self.waiting_fold());
        h.push(self.question_rows());
        h.push(self.how_rb_reads());
        h.push(self.recent_fold());
        h.push(self.notes());
        h.push(self.reproduce());
        section("overview", title, w::OVERVIEW, "insp-none", &h.concat())
    }

    /// "main · 22 recorded runs · changed 2026-10-20 01:09 UTC": the place (rb open's own screen only), the runs, and
    /// when the record last changed, linked to History. The other counts live in the nav.
    fn overview_line(&self) -> String {
        let d = self.doc;
        let mut parts = Vec::new();
        if self.surface == Surface::Open {
            // the place by its name ("main"), never its id (p_...) nor its path: the label's first part, its branch
            let place = place_name(text(d, "places.place.label"));
            if !place.is_empty() {
                parts.push(format!("<span class=\"place\">{}</span>", esc(place)));
            }
        }
        if let Some(r) = count(d, "home.counts.runs") {
            let (one, many) = w::RECORDED_RUNS_N;
            parts.push(a("runs", &plural(r, one, many), ""));
        }
        let last = text(d, "home.last_write.at");
        if !last.is_empty() {
            // the exact time, never a relative one: a page is a snapshot (OVERVIEW-V2's header). rb open's live view
            // says it relative in the browser, from the <time>'s datetime (A's script), so no page's bytes move with the
            // clock
            let iso = last.strip_suffix(" UTC").map(|t| format!("{}Z", t.replacen(' ', "T", 1))).unwrap_or_default();
            let when = if iso.is_empty() { esc(last) } else { format!("<time datetime=\"{}\" data-changed>{}</time>", esc(&iso), esc(last)) };
            parts.push(format!("<a href=\"#history\">{}{when}</a>", w::CHANGED));
        }
        if parts.is_empty() {
            return String::new();
        }
        format!("<p class=\"counts ovh\">{}</p>", parts.join(DOT))
    }

    /// One standing card per top-level question: the question in the user's words, rb's state word, rb's line on why,
    /// the non-zero tallies (each linked to its readings), and a recorded interpretation in one line. Never the
    /// interpretation's prose beyond that line, hypotheses, settings or sub-question detail. The document's
    /// `home.standing` where it gives one (contract 1.6); otherwise drawn from the question as rb gives it.
    fn standings(&self) -> Vec<String> {
        let given = list(self.doc, "home.standing");
        let tops: Vec<&Value> = list(self.doc, "questions").iter().filter(|q| count(q, "depth").unwrap_or(0) == 0).collect();
        tops.iter()
            .map(|q| {
                let id = text(q, "id");
                let s = given.iter().find(|s| text(s, "question") == id);
                self.standing_card(q, s, "st")
            })
            .collect()
    }

    /// One question's card, the same on the Overview, Research and a question's sub-questions (design pass 1, target
    /// 1), its anchor `{prefix}-{id}`: rb's standing for it where the document gives one, else drawn from the question.
    fn question_card(&self, q: &Value, prefix: &str) -> String {
        let id = text(q, "id");
        let s = list(self.doc, "home.standing").iter().find(|s| text(s, "question") == id);
        self.standing_card(q, s, prefix)
    }

    fn standing_card(&self, q: &Value, s: Option<&Value>, prefix: &str) -> String {
        let id = text(q, "id");
        let qid = format!("q-{id}");
        let status = s.map(|s| text(s, "state")).filter(|t| !t.is_empty()).unwrap_or(text(q, "status"));
        // rb's own state word where it gives one (contract 1.6's state_word), else the question's first word
        let word = s.map(|s| text(s, "state_word")).filter(|t| !t.is_empty()).map(str::to_string).unwrap_or_else(|| capitalised(status.split(' ').next().unwrap_or("")));
        // the state names its subject (plain words pass 3): "Open question", "Answered question"
        let said = w::question_state(&word);
        let word = if status.split(' ').next().is_some_and(|s| s.eq_ignore_ascii_case("open")) { term_as("open (question)", &esc(&said)) } else { esc(&said) };
        let head = format!("<p class=\"sth\"><span class=\"stw\">{word}</span></p>");
        let question = format!("<p class=\"stq\">{}</p>", a(&qid, &au_title(text(q, "text")), "sq"));
        // rb's line on why: the document's reason, else rb's headline on the question's evidence, as the question's
        // view says it
        let reason = s.map(|s| text(s, "line")).filter(|t| !t.is_empty());
        let reason = match reason {
            Some(r) => format!("<p class=\"str\">{}</p>", self.voiced(&esc(r))),
            None => {
                let hl = text(q, "overview.headline");
                if hl.is_empty() { String::new() } else { format!("<p class=\"str\">{}</p>", term_as("headline", &esc(hl))) }
            }
        };
        let tallies = self.tallies(q, s, &qid);
        let interp = self.interpretation_line(q, s, &qid);
        let deep = if count(q, "depth").unwrap_or(0) > 0 && prefix != "st" { " d1" } else { "" };
        format!(
            "<section class=\"stand{deep}\" id=\"{prefix}-{}\" aria-label=\"{}\" data-go=\"{}\">{head}{question}{reason}{tallies}{interp}</section>",
            esc(id),
            esc(&word_plain(status)),
            esc(&qid)
        )
    }

    /// The tallies of a standing card: only the non-zero ones, each in rb's colour and linked to its readings. The
    /// document's (`tallies`, each a rule word `state` and its `n`) where it gives them; else the counts rb gives as
    /// linked here, else rb's catalogue row's.
    fn tallies(&self, q: &Value, s: Option<&Value>, qid: &str) -> String {
        let muted = self.muted_of(text(q, "id"));
        // where rb gives the standing, its tallies are the card's, none included
        let pairs = match s.map(|s| list(s, "tallies")) {
            Some(l) => tally_pairs(l),
            None => self.counts_of(q),
        };
        let words: Vec<String> = pairs
            .iter()
            .filter(|(_, n)| *n > 0)
            .map(|(word, n)| {
                let said = said_plain(&format!("{n} {}", short_rule(word)));
                let full = format!("{n} {}", crate::assess::rule_label(word, *n));
                let to = format!("{qid}:rd:{}", reading_key(&full));
                let cls = crate::surface::part_class(&full, &muted);
                let cls = if cls == "dim" { "mute".to_string() } else { cls };
                if self.ids.contains(&esc(&to)) { a(&to, &said, &format!("sw {cls}")) } else { format!("<span class=\"sw {cls}\">{said}</span>") }
            })
            .collect();
        if words.is_empty() {
            return String::new();
        }
        // what the counts count leads them (plain words pass 3): "Rules: 4 met · 1 not reproduced"
        format!("<p class=\"stt\"><span class=\"k\">{}</span>{}</p>", w::RULES_LEAD, words.join(DOT))
    }

    /// A question's counts by rule word: the document's (`questions[].tallies`, contract 1.6), else the counts rb gives
    /// as linked here, else rb's catalogue row's.
    fn counts_of(&self, q: &Value) -> Vec<(String, i64)> {
        let given = list(q, "tallies");
        if !given.is_empty() {
            return tally_pairs(given);
        }
        let linked = linked_counts(text(q, "rollup"));
        if linked.is_empty() { self.tree_counts(text(q, "id")) } else { linked }
    }

    /// The counts rb's catalogue row gives a question (home.tree's standing, "open · 3 criteria met · 1 not
    /// reproduced"), where it says no linked-here line (a question with nothing below it).
    fn tree_counts(&self, id: &str) -> Vec<(String, i64)> {
        let standing = list(self.doc, "home.tree").iter().find(|r| text(r, "kind") == "question" && text(r, "id") == id).map(|r| text(r, "standing")).unwrap_or("");
        let counted: Vec<&str> = standing.split(" \u{b7} ").filter(|p| p.starts_with(|c: char| c.is_ascii_digit())).collect();
        linked_counts(&counted.join(" \u{b7} "))
    }

    /// "Your reading: <the words>, its criterion met" (the model's label, w::reading_label), one line, its tags after it (not confirmed, where it
    /// waits for the user's confirmation). The document's (`interpretation`) where it gives one; else the question's lead.
    fn interpretation_line(&self, q: &Value, s: Option<&Value>, qid: &str) -> String {
        // where rb gives the standing's interpretation (contract 1.6), it is the one drawn, and an empty one means none
        // is active: only rb's own computed comparison, which is not an interpretation, is drawn from the lead then
        let given = s.and_then(|s| at(s, "interpretation"));
        let (said, holds, confirmed, to) = match given.filter(|i| !text(i, "text").is_empty()) {
            Some(i) => (text(i, "text"), text(i, "holds"), at(i, "confirmed").is_some_and(Value::truthy), qid.to_string()),
            None => {
                let Some(lead) = lead_of(q).filter(|l| given.is_none() || is_comparison(l)) else { return String::new() };
                // rb's computed comparison is rb's own sentence, in rb's voice, said once on the card
                if is_comparison(lead) {
                    let said = text(lead, "text");
                    if said.is_empty() {
                        return String::new();
                    }
                    let to = self.to(&format!("{qid}:rel"), qid).to_string();
                    let index = count(lead, "index").unwrap_or(0).max(0) as usize;
                    let n = list(q, "overview.relations").get(index).map_or(0, |r| list(r, "comparisons").len());
                    let declared = if n == 0 { String::new() } else { format!("<span> \u{b7} {}</span>", plural(n as i64, "declared comparison", "declared comparisons")) };
                    return format!(
                        "<p class=\"sti\">{}<span>: </span>{}{declared}</p>",
                        term(w::COMPUTED_COMPARISON, &format!("<span class=\"k\">{}</span>", esc(w::COMPUTED_COMPARISON))),
                        a(&to, &esc(said), "ans")
                    );
                }
                if !at(lead, "active").map_or(true, Value::truthy) {
                    return String::new();
                }
                let index = count(lead, "index").unwrap_or(0).max(0) as usize;
                let holds = list(q, "overview.interpretations").get(index).map(|ip| text(ip, "holds")).unwrap_or("");
                (text(lead, "text"), holds, at(lead, "confirmed").is_some_and(Value::truthy), format!("{qid}:ip:{}", index + 1))
            }
        };
        if said.is_empty() {
            return String::new();
        }
        // a recorded interpretation is a note (plain words pass 3): "A note, not confirmed: <its words>." with its
        // state in the line, never a tag, then its rule's state as a sentence of its own
        self.note_line("sti", said, holds, confirmed, self.to(&to, qid))
    }

    /// A recorded interpretation as a note (plain words pass 3): "A note, not confirmed: <its words>." (or "Note: ..."
    /// once confirmed), its words linked to `to`, then whether its rule holds as a sentence of its own.
    fn note_line(&self, class: &str, said: &str, holds: &str, confirmed: bool, to: &str) -> String {
        let said = said.trim_end();
        let stop = if said.ends_with(['.', '!', '?']) { "" } else { "." };
        let words = self.au_cited(said, to, "ans");
        let holds = w::holds_sentence(holds);
        let holds = if holds.is_empty() { String::new() } else { format!(" <span class=\"hs\">{}</span>", esc(&holds)) };
        format!(
            "<p class=\"{class}\">{}{words}{stop}{holds}</p>",
            term_as(w::RECORDED_INTERPRETATION, &format!("<span class=\"k\">{}: </span>", w::note_label(confirmed)))
        )
    }

    /// Evidence integrity, only when something weakens the whole body of evidence: each part one plain line with its
    /// numbers, linked to its list, then rb's line on what they affect. The document's (`home.integrity`) where it gives
    /// it; else the parts of rb's line on how the record was made.
    fn integrity(&self) -> String {
        let d = self.doc;
        let (parts, affects): (Vec<String>, String) = match at(d, "home.integrity") {
            Some(i) => (
                list(i, "parts")
                    .iter()
                    .filter_map(|p| {
                        let line = text(p, "line");
                        if line.is_empty() {
                            return None;
                        }
                        // each part links to the list it counts
                        let to = match text(p, "kind") {
                            "after_evidence" => "criteria",
                            "without_command" | "retracted" => "runs",
                            _ => "",
                        };
                        Some(if to.is_empty() { esc(line) } else { a(to, &esc(line), "") })
                    })
                    .collect(),
                text(i, "affects").to_string(),
            ),
            None => {
                let line = text(d, "home.made.line");
                let late = count(d, "home.made.after_evidence").unwrap_or(0);
                let given = count(d, "home.made.with_command");
                let runs = count(d, "home.made.runs").unwrap_or(0);
                let retracted = count(d, "home.counts.retracted").unwrap_or(0);
                // shown only when a part weakens the evidence: criteria declared after it, runs without their command
                if line.is_empty() || (late == 0 && given.map_or(true, |g| g >= runs) && retracted == 0) {
                    (Vec::new(), String::new())
                } else {
                    let mut parts: Vec<String> = line
                        .split(" \u{b7} ")
                        .enumerate()
                        .map(|(i, p)| {
                            let said = capitalised(p);
                            if i == 0 && late > 0 {
                                return a(self.to("criteria", "overview"), &esc(&said), "");
                            }
                            // "command (as given)" keeps rb's line for it
                            match said.strip_suffix(w::COMMAND_AS_GIVEN) {
                                Some(head) => format!("<span>{}</span>{}", esc(head), term(w::COMMAND_AS_GIVEN, &esc(w::COMMAND_AS_GIVEN))),
                                None => esc(&said),
                            }
                        })
                        .collect();
                    if retracted > 0 {
                        parts.push(a("runs", &esc(&capitalised(&format!("{} retracted", plural(retracted, "run", "runs")))), ""));
                    }
                    (parts, String::new())
                }
            }
        };
        if parts.is_empty() {
            return String::new();
        }
        let lines: String = parts.iter().map(|p| format!("<p>{p}</p>")).collect();
        let affects = if affects.is_empty() { String::new() } else { format!("<p class=\"ia\">{}</p>", esc(&affects)) };
        format!(
            "<section class=\"integ\" id=\"integrity\" aria-labelledby=\"h-integrity\"><h2 id=\"h-integrity\">{}<span class=\"wsign\" aria-hidden=\"true\">\u{26a0}</span></h2>{lines}{affects}</section>",
            w::EVIDENCE_INTEGRITY
        )
    }

    /// The one queue: everything rb cannot settle and the user can, in the plan's fixed order. Each row a verb phrase,
    /// one line of numbers or names, and one action, which opens the place it names (rb open's script opens the
    /// inspector there); never a command, hash or path. Its heading counts nothing: the line under it does. On the export, "Needs judgment" with
    /// its lead line: a page that leaves the machine cannot know who reads it, and "Open calls" there names the nav item,
    /// which counts calls only (the lexicon's D3).
    fn queue_section(&self) -> String {
        let items = self.queue();
        let export = self.surface == Surface::Export;
        // on the export each viewer reads its own words (OWNER-READER): the owner's working view says rb open's, the
        // reader's says the rows are still open, as status, not tasks
        let head = if export { by_viewer("span", "", w::NEEDS_JUDGMENT, w::NEEDS_YOUR_JUDGMENT, w::STILL_OPEN) } else { w::NEEDS_YOUR_JUDGMENT.to_string() };
        // the heading carries no number (FIX4, proposal 1): the line under it counts what the rows stand for, and the
        // nav's Your call counts the calls; a count of rows beside them read as a third total that added up to neither
        // fix 3: each number names what it counts and counts things, never rows: one row confirms two designs, so the
        // waiting part counts the list of what waits by its kinds, and the repository part what rb found and the files
        let calls = items.iter().filter(|it| matches!(it.kind.as_str(), "call" | "baseline") || matches!(it.src, QSrc::Call(_))).count();
        let found_rows = items.iter().filter(|it| it.kind == "found").count();
        let uncited_rows = items.iter().filter(|it| it.kind == "uncited").count();
        let confirm_rows = items.len() - calls - found_rows - uncited_rows;
        let mut parts: Vec<String> = Vec::new();
        if calls > 0 {
            parts.push(plural(calls as i64, w::QN_CALL, w::QN_CALLS));
        }
        if confirm_rows > 0 {
            let pending = list(self.doc, "pending.rows");
            let known: Vec<&str> = w::QN_WAITS.iter().map(|(k, _, _)| *k).collect();
            let mut kinds: Vec<String> = w::QN_WAITS
                .iter()
                .map(|(k, one, many)| (pending.iter().filter(|r| text(r, "kind") == *k).count(), one, many))
                .filter(|(n, _, _)| *n > 0)
                .map(|(n, one, many)| plural(n as i64, one, many))
                .collect();
            let other = pending.iter().filter(|r| !known.contains(&text(r, "kind"))).count();
            if other > 0 {
                kinds.push(plural(other as i64, w::QN_WAITS_OTHER.0, w::QN_WAITS_OTHER.1));
            }
            // a queue that names what waits where the list of it is not given still says its rows
            let said = if kinds.is_empty() { confirm_rows.to_string() } else { and_said(&kinds) };
            parts.push(format!("{said} {}", w::QN_CONFIRM));
        }
        let mut repo: Vec<String> = Vec::new();
        if found_rows > 0 {
            let n = count(self.doc, "home.found_total").map_or(0, |n| n.max(0) as usize).max(list(self.doc, "home.found").len()).max(found_rows);
            repo.push(plural(n as i64, w::QN_RESULT.0, w::QN_RESULT.1));
        }
        if uncited_rows > 0 {
            let n = (count(self.doc, "home.uncited").unwrap_or(0).max(0) as usize).max(uncited_rows);
            repo.push(plural(n as i64, w::QN_UNCITED.0, w::QN_UNCITED.1));
        }
        if !repo.is_empty() {
            parts.push(format!("{} {}", and_said(&repo), w::QN_FOUND));
        }
        let counted = parts.join(DOT);
        let counted = if counted.is_empty() { String::new() } else if export { self.voiced(&counted) } else { owner_voice(&counted) };
        let counted = if counted.is_empty() { String::new() } else { format!("<p class=\"qn sm dim\">{counted}</p>") };
        // what a row is for, in each viewer's words, where there is a row (plain words pass 4): the owner's view and a file
        // with no viewer named decide; a reader's makes no call
        let lede = if export && !items.is_empty() { by_viewer("p", " class=\"lede sm\"", w::QUEUE_LEDE, w::QUEUE_LEDE, w::QUEUE_LEDE_READER) } else { String::new() };
        let rows: String = items
            .iter()
            .map(|it| {
                let detail = if it.detail.is_empty() { String::new() } else { format!("<span class=\"qd\">{}</span>", it.detail) };
                // its one action; the export draws none, for any viewer: the row itself opens its item, where the owner's
                // working view records the call (OWNER-READER, C's composer)
                let action = if export || it.target.is_empty() {
                    String::new()
                } else {
                    format!("<a class=\"qbtn\" href=\"#{}\" data-decide=\"{}\">{}</a>", esc(&it.target), esc(&it.target), esc(&it.action))
                };
                // every row opens its item (design pass 1, P11): the whole row follows its target, its words a link,
                // so a row opens with no script and takes keyboard focus; on the export too, which has no button
                let to = it.target.clone();
                // a reader's row says what stands, never an ask (PIVOT): drawn for the reader's meta, the owner's ask
                // for the owner and a page with no viewer named; rb open has no reader
                let said = if it.reader.is_empty() || self.surface == Surface::Open { it.title.clone() } else { by_viewer("span", "", &it.title, &it.title, &it.reader) };
                let (go, title) = if to.is_empty() {
                    (String::new(), said)
                } else {
                    (format!(" data-go=\"{}\"", esc(&to)), a(&to, &said, "qa"))
                };
                format!("<li class=\"qrow\" data-kind=\"{}\"{go}><span class=\"qt\"><span class=\"qv\">{title}</span>{detail}</span>{action}</li>", esc(&it.kind))
            })
            .collect();
        let body = if rows.is_empty() {
            if export {
                by_viewer("p", " class=\"dim\"", w::NOTHING_NEEDS_JUDGMENT_EXPORT, w::NOTHING_NEEDS_JUDGMENT, w::READER_NOTHING_OPEN)
            } else {
                format!("<p class=\"dim\">{}</p>", w::NOTHING_NEEDS_JUDGMENT)
            }
        } else {
            format!("<ul class=\"queue\">{rows}</ul>")
        };
        let open = text(self.doc, "home.open_line");
        let open = if open.is_empty() { String::new() } else if self.ids.contains("open-items") { format!("<p class=\"openrec sm\">{}</p>", a("open-items", &esc(open), "")) } else { format!("<p class=\"openrec sm dim\">{}</p>", esc(open)) };
        let all = if export { by_viewer("p", " class=\"sm\"", &a("your-call", w::SEE_OPEN_CALLS, ""), &a("your-call", w::SEE_YOUR_CALLS, ""), &a("your-call", w::SEE_OPEN_CALLS, "")) } else { String::new() };
        format!("<section class=\"judge\" id=\"queue\" aria-labelledby=\"h-queue\"><h2 id=\"h-queue\">{head}</h2>{counted}{lede}{body}{open}{all}</section>")
    }

    /// What waits for the user's confirmation, folded under the queue: each in its group, in rb's words, ending in the
    /// one command that confirms it in the user's own terminal. Listed apart from the calls, never under Your call
    /// (AGENTS.md), and one click down: the queue's rows say it without a command.
    fn waiting_fold(&self) -> String {
        let n = list(self.doc, "pending.rows").len();
        if n == 0 {
            return String::new();
        }
        let head = if self.surface == Surface::Export { self.voiced(w::WAITING_EXPORT) } else { w::WAITING.to_string() };
        // the id inside the fold, so a link to it (Your call's, the queue's) opens the fold around it
        // no count of its own: the Overview counts only its queue, whose rows already name these (Look 3)
        format!("<details class=\"fold waitblk\" data-fold=\"to-confirm\"><summary>{head}</summary>{}</details>", self.waiting())
    }

    /// The research questions below the standing cards, a row each: the question, its state and a compact tally,
    /// sub-questions indented, at most two levels deep. A top-level question has its card above, so it is not listed
    /// again; where there are several, each one's sub-questions follow a quiet line naming it.
    fn question_rows(&self) -> String {
        let qs = list(self.doc, "questions");
        let tops = qs.iter().filter(|q| count(q, "depth").unwrap_or(0) == 0).count();
        let mut rows = String::new();
        for q in qs {
            let depth = count(q, "depth").unwrap_or(0);
            if depth == 0 {
                if tops > 1 && qs.iter().any(|k| text(k, "parent") == text(q, "id")) {
                    // the parent named by its words, as the nav names it, never by its bare id (fix 2)
                    let to = format!("q-{}", text(q, "id"));
                    let words = au_title(text(q, "text"));
                    rows.push_str(&format!("<li class=\"qgrp\">{}</li>", if self.ids.contains(&esc(&to)) { a(&to, &words, "") } else { words }));
                }
                continue;
            }
            let id = text(q, "id");
            let qid = format!("q-{id}");
            // its state names its subject and its counts lead with what they count (plain words pass 3): "Open
            // question · Rules: 4 met · 4 not tested"
            let status = text(q, "status");
            let said = w::question_state(status.split(' ').next().unwrap_or(""));
            let status = if status.split(' ').next().is_some_and(|s| s.eq_ignore_ascii_case("open")) { term_as("open (question)", &esc(&said)) } else { esc(&said) };
            let muted = self.muted_of(id);
            let tally: Vec<String> = self.counts_of(q)
                .iter()
                .filter(|(_, n)| *n > 0)
                .map(|(word, n)| {
                    let full = format!("{n} {}", crate::assess::rule_label(word, *n));
                    let cls = crate::surface::part_class(&full, &muted);
                    let said = said_plain(&format!("{n} {}", short_rule(word)));
                    if cls == "dim" { format!("<span>{said}</span>") } else { format!("<span class=\"sw {cls}\">{said}</span>") }
                })
                .collect();
            let tally = if tally.is_empty() { String::new() } else { format!("{DOT}<span class=\"k\">{}</span>{}", w::RULES_LEAD, tally.join(DOT)) };
            rows.push_str(&format!(
                "<li class=\"qr{}\" data-go=\"{}\">{}<span class=\"qs\">{status}{tally}</span></li>",
                if depth > 1 { " d1" } else { "" },
                esc(&qid),
                a(&qid, &au_title(text(q, "text")), "sq")
            ));
        }
        if rows.is_empty() {
            return String::new();
        }
        format!("<section class=\"qrows\" id=\"ov-questions\" aria-labelledby=\"h-ov-questions\"><h2 id=\"h-ov-questions\">{}</h2><ul class=\"qlist\">{rows}</ul></section>", w::RESEARCH_QUESTIONS)
    }

    /// How rb reads this repository, folded: where the record started, rb's scope line, the changed files nothing
    /// cites, and what rb found, each verbatim from rb.
    fn how_rb_reads(&self) -> String {
        let p = |s: String, live: &str| if s.is_empty() { s } else { format!("<p{live}>{s}</p>") };
        // its time sits in the span rb's scope line keeps a time in (`nw`), the one rb sync --watch reads as the clock's
        let short = nowrap_times(&esc(text(self.doc, "home.scope_short")));
        let short = if short.is_empty() { short } else { format!("<span data-live=\"scope_short\">{short}</span>") };
        let body = [
            p(self.began(), ""),
            p(nowrap_times(&esc(text(self.doc, "home.scope"))), " data-live=\"scope\""),
            if text(self.doc, "home.scope").is_empty() { p(short, "") } else { String::new() },
            {
                let line = text(self.doc, "home.uncited_line");
                if line.is_empty() { String::new() } else { self.said_to_act("p", "", line, &esc(line)) }
            },
            self.found(),
            // rb open's other worktrees, with their paths and commands: one click down, never on the Overview's face
            self.elsewhere.to_string(),
        ]
        .concat();
        if body.is_empty() {
            return String::new();
        }
        // the id sits on a block around the fold: a fold carries none (its state is the script's, by data-fold)
        format!("<div class=\"howblk\" id=\"how-made\"><details class=\"fold how\" data-fold=\"how-made\"><summary>{}</summary>{body}</details></div>", w::HOW_RB_READS)
    }

    /// The latest changes and the newest sitting, folded: History holds them whole.
    fn recent_fold(&self) -> String {
        let latest = super::history_view::latest(self.doc);
        let recent = super::history_view::recent(self.doc, &|id| self.ids.contains(&esc(id)));
        if latest.is_empty() && recent.is_empty() {
            return String::new();
        }
        format!("<details class=\"fold\" data-fold=\"recent\"><summary>{}</summary>{latest}{recent}</details>", w::RECENT_CHANGES)
    }

    /// The queue as drawn: the document's (`home.queue`, contract 1.6) in its rank order where it gives one; else what
    /// the document holds in the plan's order: the calls (rb's order), the designs that wait for a confirmation, the
    /// other confirmations, what rb found, and the changed files nothing cites.
    fn queue(&self) -> Vec<QItem<'a>> {
        let d = self.doc;
        let pending = list(d, "pending.rows");
        // where rb gives the queue (contract 1.6), it is the queue, an empty one included: nothing from 1.5's fields
        // is added to it
        if let Some(given) = at(d, "home.queue").and_then(Value::as_list) {
            let mut out: Vec<(i64, QItem<'a>)> = given
                .iter()
                .enumerate()
                .map(|(i, e)| {
                    let kind = text(e, "kind").to_string();
                    let target = text(e, "target");
                    // the object a row stands for, as the page holds it: a call's card, else its finding's card, else
                    // its experiment's view; a design's experiment; what waits for a confirmation, what rb found and
                    // the changed files nothing cites, each where the page says it
                    let (to, src) = match kind.as_str() {
                        "found" => ("found".to_string(), QSrc::Found),
                        "uncited" => ("how-made".to_string(), QSrc::Uncited),
                        // one design opens its experiment; several open the list of what waits, which names each
                        "design" if list(e, "targets").len() > 1 => ("to-confirm".to_string(), QSrc::Pending),
                        "design" => (format!("x-{target}"), QSrc::Pending),
                        "pending_call" | "proposed" | "past" | "gate" => ("to-confirm".to_string(), QSrc::Pending),
                        _ => {
                            let to = [format!("call-{target}"), format!("c-{target}"), format!("x-{target}")].into_iter().find(|t| self.ids.contains(&esc(t))).unwrap_or_default();
                            (to, self.call_on(target).map_or(QSrc::None, QSrc::Call))
                        }
                    };
                    // the fold of what waits is drawn only where something waits
                    let drawn = self.ids.contains(&esc(&to)) || (FIXED.contains(&to.as_str()) && (to != "to-confirm" || !list(d, "pending.rows").is_empty()));
                    let to = if drawn { to } else { "your-call".to_string() };
                    let rank = count(e, "rank").unwrap_or(i as i64);
                    (rank, QItem { kind, title: esc(text(e, "title")), reader: esc(text(e, "reader_title")), detail: esc(&nb(text(e, "detail"))), target: to, action: text(e, "action").to_string(), src })
                })
                .collect();
            out.sort_by_key(|(r, _)| *r);
            return out.into_iter().map(|(_, it)| it).collect();
        }
        let mut out = Vec::new();
        for c in list(d, "calls") {
            let detail = [text(c, "reason.detail"), text(c, "reason.also")].into_iter().filter(|s| !s.is_empty()).collect::<Vec<_>>().join(" \u{b7} ");
            let label = text(c, "reason.label");
            let title = if label.is_empty() { au(text(c, "title")) } else { format!("<span class=\"rl2\">{}:</span> {}", esc(label), au(text(c, "title"))) };
            out.push(QItem { kind: "call".into(), title, reader: String::new(), detail: esc(&nb(&detail)), target: self.call_card(c), action: w::REVIEW.into(), src: QSrc::Call(c) });
        }
        let designs: Vec<&'a Value> = pending.iter().filter(|r| text(r, "kind") == "design").collect();
        if !designs.is_empty() {
            let ids: Vec<String> = designs.iter().map(|r| text(r, "id").split('@').next().unwrap_or("").to_string()).filter(|s| !s.is_empty()).collect();
            let first = if ids.len() > 1 { "to-confirm".to_string() } else { ids.first().map(|e| format!("x-{e}")).filter(|x| self.ids.contains(&esc(x))).unwrap_or_else(|| "your-call".into()) };
            let detail = ids.iter().map(|e| format!("<span class=\"mono\">{}</span>", esc(e))).collect::<Vec<_>>().join(DOT);
            out.push(QItem { kind: "design".into(), title: esc(&w::confirm_designs(designs.len())), reader: String::new(), detail, target: first, action: w::REVIEW.into(), src: QSrc::Pending });
        }
        for r in pending.iter().filter(|r| text(r, "kind") != "design") {
            let title = match text(r, "kind") {
                "proposed" => w::CONFIRM_PROPOSED,
                "past" => w::CONFIRM_PAST,
                "gate" => w::CONFIRM_GATE,
                _ => w::CONFIRM_OTHER,
            };
            out.push(QItem { kind: text(r, "kind").into(), title: esc(title), reader: String::new(), detail: esc(pending_said(text(r, "text"))), target: "to-confirm".into(), action: w::REVIEW.into(), src: QSrc::Pending });
        }
        let found = count(d, "home.found_total").map_or(list(d, "home.found").len(), |n| n.max(0) as usize).max(list(d, "home.found").len());
        if found > 0 {
            out.push(QItem { kind: "found".into(), title: esc(&w::record_found(found)), reader: String::new(), detail: String::new(), target: "found".into(), action: w::INSPECT.into(), src: QSrc::Found });
        }
        let uncited = count(d, "home.uncited").unwrap_or(0);
        if uncited > 0 {
            out.push(QItem { kind: "uncited".into(), title: esc(&w::inspect_uncited(uncited as usize)), reader: String::new(), detail: String::new(), target: "how-made".into(), action: w::INSPECT.into(), src: QSrc::Uncited });
        }
        out
    }

    /// Where the record started, in rb's words: its day linked to History and its runs to Runs, when rb hands the line
    /// in parts (`began_parts`, pieces and where each links) that read as the line does; otherwise the line as it is.
    fn began(&self) -> String {
        let line = text(self.doc, "home.began");
        let parts: Vec<(&str, &str)> = list(self.doc, "home.began_parts")
            .iter()
            .filter_map(|p| match p.as_list().map(Vec::as_slice) {
                Some([t, to]) => Some((t.as_str()?, to.as_str()?)),
                _ => None,
            })
            .collect();
        if parts.is_empty() || parts.iter().map(|(t, _)| *t).collect::<String>() != line {
            return esc(line);
        }
        parts
            .iter()
            .map(|(t, to)| match *to {
                "history" | "runs" => a(self.to(to, "overview"), &esc(t), ""),
                _ => format!("<span>{}</span>", esc(t)),
            })
            .collect()
    }

    /// Found in the repository (N): what rb found and nobody recorded, each with rb's hint, capped "and N more". Never
    /// counted under Your call, and never a command: recording happens in rb ui.
    fn found(&self) -> String {
        self.found_as("found")
    }

    /// Found in the repository, at the anchor `id`: the Overview's fold holds it at `found`, Your call at its own.
    fn found_as(&self, id: &str) -> String {
        let found = list(self.doc, "home.found");
        let total = count(self.doc, "home.found_total").map_or(found.len(), |n| n.max(0) as usize).max(found.len());
        if total == 0 {
            return String::new();
        }
        let shown = found.len().min(w::FOUND_CAP);
        let items: String = found[..shown]
            .iter()
            .map(|f| {
                let hint = |how: &str| esc(&[text(f, "hint"), w::FOUND_BY, how].into_iter().filter(|s| !s.is_empty()).collect::<Vec<_>>().join(" \u{b7} "));
                // how to record it is an action: not in the reader's view of the export (OWNER-READER)
                let hint = if self.surface == Surface::Export {
                    by_viewer("span", " class=\"fh\"", &hint(w::FOUND_HOW_NONE), &hint(w::FOUND_HOW), &hint(""))
                } else {
                    format!("<span class=\"fh\">{}</span>", hint(w::FOUND_HOW))
                };
                format!("<li><span class=\"ft\">{}</span> {hint}</li>", esc(text(f, "title")))
            })
            .collect();
        let more = if total > shown { format!("<p class=\"sm dim\">{}</p>", w::and_more(total - shown)) } else { String::new() };
        format!("<section class=\"found\" id=\"{id}\" aria-labelledby=\"h-{id}\"><h2 id=\"h-{id}\">{} ({total})</h2><ul class=\"fl\">{items}</ul>{more}</section>", w::FOUND, id = esc(id))
    }

    /// What waits for the user's confirmation, apart from Your call: each in its group, in rb's words, ending in the one
    /// command that confirms it, in the user's own terminal; rb's line that confirms them all only while no design
    /// waits.
    fn waiting(&self) -> String {
        let all = list(self.doc, "pending.rows");
        if all.is_empty() {
            return String::new();
        }
        let rows = all;
        let known: Vec<&str> = w::WAITING_GROUPS.iter().map(|(k, _)| *k).collect();
        let mut out = String::new();
        let groups = w::WAITING_GROUPS.iter().map(|(k, t)| (Some(*k), *t)).chain([(None, w::WAITING_OTHER)]);
        for (kind, title) in groups {
            let these: Vec<&Value> = rows.iter().filter(|r| kind.map_or(!known.contains(&text(r, "kind")), |k| text(r, "kind") == k)).collect();
            if these.is_empty() {
                continue;
            }
            let items: String = these.iter().map(|r| self.waiting_row(r)).collect();
            // the export's last group in no one's voice but the owner's (fix 2)
            let title = if self.surface == Surface::Export && kind.is_none() { self.voiced(w::WAITING_OTHER_EXPORT) } else { title.to_string() };
            out.push_str(&format!("<h3>{title} ({})</h3><ul class=\"wl\">{items}</ul>", these.len()));
        }
        let designs = text(self.doc, "pending.designs_line");
        if !designs.is_empty() {
            out.push_str(&self.said_to_act("p", " class=\"sm dim\"", designs, &esc(designs)));
        }
        if !all.iter().any(|r| text(r, "kind") == "design") {
            out.push_str(&self.act(confirm_all(at(self.doc, "pending.confirm_all"), self.past)));
        }
        format!("<div class=\"waiting\" id=\"to-confirm\">{out}</div>")
    }

    fn waiting_row(&self, r: &Value) -> String {
        let said = esc(text(r, "text"));
        // a design opens its experiment, where its standing also shows
        let eid = text(r, "id").split('@').next().unwrap_or("");
        let x = format!("x-{eid}");
        let said = if text(r, "kind") == "design" && !eid.is_empty() && self.ids.contains(&esc(&x)) { a(&x, &said, "") } else { said };
        // a design that waits is one line on every surface: its title, linked to its experiment (whose view holds the
        // design and what changed in it), and the command that lists it to confirm (rb confirm <exp>); never its
        // listing here, nor the command that confirms it as listed, which only a reader of that listing should run
        if text(r, "kind") == "design" {
            let listing = listing_of(text(r, "confirm"));
            let listing = if listing.is_empty() { String::new() } else { self.act(self.cmd(&listing)) };
            return format!("<li class=\"wd\"><p class=\"wt\">{said}</p>{listing}</li>");
        }
        let lines: String = texts(r, "lines").iter().map(|l| format!("<p class=\"wp\">{}</p>", esc(l))).collect();
        let confirm = text(r, "confirm");
        let here = if self.surface == Surface::Export {
            by_viewer("p", " class=\"cf\"", w::CONFIRM_HERE_EXPORT, w::CONFIRM_HERE_EXPORT, w::READER_NOT_CONFIRMED)
        } else {
            format!("<p class=\"cf\">{}</p>", w::CONFIRM_HERE)
        };
        let confirm = if confirm.is_empty() || self.past { String::new() } else { format!("{here}{}", self.act(self.cmd(confirm))) };
        format!("<li><p class=\"wt\">{said}</p>{lines}{confirm}</li>")
    }

    /// Notes on the project, folded: each note's date, then its text verbatim.
    fn notes(&self) -> String {
        let notes = list(self.doc, "home.notes");
        if notes.is_empty() {
            return String::new();
        }
        let items: String = notes.iter().map(|n| self.note_li(n)).collect();
        format!("<details class=\"fold\" data-fold=\"notes\"><summary>{} ({})</summary><ul class=\"notes plain\" id=\"notes\">{items}</ul></details>", w::NOTES_ON_THE_PROJECT, notes.len())
    }

    /// One of the project's notes, led by rb's "note on the project (<day>): " once, whether the document gives the day with the
    /// text or apart.
    fn note_li(&self, n: &Value) -> String {
        let (id, day, said) = (text(n, "id"), text(n, "at"), text(n, "text"));
        let day = if day.is_empty() || said.starts_with(day) { String::new() } else { format!(" ({})", esc(day)) };
        let at = if id.is_empty() { String::new() } else { format!(" data-id=\"{}\"", esc(id)) };
        format!("<li class=\"note pnote\"{at}><span class=\"nd2\">{}{}{day}:</span> {}</li>", w::NOTE_ON, w::THE_PROJECT, self.au_cited(said, "", ""))
    }

    /// The record's clone address and its page's, on an export that has one (rb export --url, rb sync --with-record);
    /// none on rb page and rb open, and never at a past version (guarantee 38). REPRODUCE-WORDS.md.
    fn rerun_at(&self) -> Option<(String, String)> {
        let bundle = text(self.doc, "reproduce_url");
        if self.surface != Surface::Export || self.past || bundle.is_empty() {
            return None;
        }
        // the export's own address, as the hub's rb-page meta names it: the folder it was published at (--url), which
        // serves page.html beside the bundle, else the bundle's own
        let page = bundle.strip_suffix("bundle.zip").filter(|base| base.ends_with('/')).unwrap_or(bundle).to_string();
        Some((page, bundle.to_string()))
    }

    /// The prompt a reader copies, as HTML: rb's words whole and escaped.
    fn rerun_prompt_html(&self, experiment: Option<(&str, &str, &[String])>) -> Option<String> {
        let (page, bundle) = self.rerun_at()?;
        Some(esc(&w::rerun_prompt(&page, &bundle, text(self.doc, "project.id"), experiment)))
    }

    /// rb's own addresses on what says them (`data-rb-page`, `data-rb-bundle`), so the hub's metas (rb-page, rb-clone)
    /// take their place in the text, which rb draws whole.
    fn rerun_addresses(&self) -> String {
        self.rerun_at().map(|(page, bundle)| format!(" data-rb-page=\"{}\" data-rb-bundle=\"{}\"", esc(&page), esc(&bundle))).unwrap_or_default()
    }

    /// "Rerun this research", a link to the panel (`data-rerun`: A's script opens it there; with no script it is the
    /// panel's anchor, which shell.css shows as its target), where the export has a clone address.
    fn rerun_button(&self, class: &str) -> String {
        if self.rerun_at().is_none() {
            return String::new();
        }
        format!("<a class=\"rrb rrun {class}\" href=\"#rerun\" data-rerun>{}</a>", w::RERUN)
    }

    /// The panel the button opens: its line, the prompt with Copy prompt, the two commands for someone with no agent,
    /// the code line rb export says, and what a clone carries.
    fn rerun_panel(&self) -> String {
        let (Some((_, bundle)), Some(prompt)) = (self.rerun_at(), self.rerun_prompt_html(None)) else { return String::new() };
        let repo = text(self.doc, "reproduce_repo.url");
        let code = if repo.is_empty() {
            w::CODE_UNNAMED.to_string()
        } else {
            let shown = if repo.starts_with("http://") || repo.starts_with("https://") { format!("<a href=\"{}\">{}</a>", esc(repo), esc(repo)) } else { esc(repo) };
            let commit: String = text(self.doc, "reproduce_repo.commit").chars().take(10).collect();
            let at = if commit.is_empty() { String::new() } else { format!("{}<span class=\"oid\">{}</span>", w::AT_COMMIT, esc(&commit)) };
            // what the copy had not committed when it was exported (C's `repo.changed`, read where the manifest has it)
            let changed = texts(self.doc, "reproduce_repo.changed");
            let changed = if changed.is_empty() {
                String::new()
            } else {
                let shown: Vec<String> = changed.iter().take(5).map(|p| format!("<span class=\"mono\">{}</span>", esc(p))).collect();
                let more = if changed.len() > 5 { format!(" {}", w::and_more(changed.len() - 5)) } else { String::new() };
                format!("{}{}{more}", w::WITH_CHANGES_NOT_COMMITTED, shown.join(", "))
            };
            format!("{}{shown}{at}{changed}{}", w::CODE_THE_RUNS_USED, w::CODE_TRAVELS)
        };
        let folder = text(self.doc, "project.id");
        format!(
            "<aside class=\"rerun rrun\" id=\"rerun\" aria-labelledby=\"h-rerun\"{}><a class=\"rrclose\" href=\"#overview\">{}</a><h2 id=\"h-rerun\">{}</h2><p>{}</p>\
             <div class=\"cmdbox rpbox\"><span data-copybtn=\"rerun-prompt\" data-label=\"{}\"></span><pre class=\"cmd rprompt\" id=\"rerun-prompt\">{prompt}</pre></div>\
             <p class=\"sm\">{}</p>{}{}{}<p class=\"sm\">{code}</p><p class=\"sm dim\">{}</p></aside>",
            self.rerun_addresses(),
            w::CLOSE,
            w::RERUN,
            w::RERUN_LINE,
            w::COPY_PROMPT,
            w::RERUN_BY_HAND,
            cmdbox("pip install rabbit-brain"),
            // the prompt's steps 2 and 3, by hand: the same folder, then rb context in it
            cmdbox(&format!("rb clone {bundle} {folder}")),
            cmdbox(&format!("cd {folder} && rb context")),
            w::REPRODUCE_AFTER
        )
    }

    /// Reproduce this project, on a page published at an address (rb export --url): how anyone reruns it, as the one
    /// button that opens the Rerun panel, which says the prompt, the commands, the code line and what a clone carries
    /// once for the whole page (REPRODUCE-WORDS.md).
    fn reproduce(&self) -> String {
        if self.rerun_at().is_none() {
            return String::new();
        }
        format!(
            "<section class=\"repro rrun\" id=\"reproduce\" aria-labelledby=\"h-reproduce\"><h2 id=\"h-reproduce\">{}</h2><p>{}</p>{}</section>",
            w::REPRODUCE,
            w::REPRODUCE_LEDE,
            self.rerun_button("rrov")
        )
    }

    /// Your call: the user's calls, each at the Decide level, in the Overview queue's order: rb's quiet line, each call
    /// with its actions, one link to what waits for a confirmation in rb's words (listed apart, never under Your call:
    /// AGENTS.md), then what is open in the record, folded. On the export it is "Open calls", with no line that says
    /// "you" about a call.
    fn your_call(&self) -> String {
        let d = self.doc;
        let export = self.surface == Surface::Export;
        let head = self.your_call_head();
        let lede = if export {
            by_viewer("p", " class=\"lede\"", w::OPEN_CALLS_LEDE, w::OWNER_CALLS_LEDE, w::READER_CALLS_LEDE)
        } else {
            let said = [text(d, "home.quiet"), w::CALL_IN_YOUR_TERMINAL].into_iter().filter(|s| !s.is_empty()).map(esc).collect::<Vec<_>>().join(" ");
            format!("<p class=\"lede\">{said}</p>")
        };
        let alias = if export { "<span id=\"open-calls\"></span>" } else { "" };
        let mut h = vec![format!("{alias}<h1 id=\"h-your-call\">{}</h1>{lede}", self.your_call_head_html())];
        if !export {
            h.push(format!("<p class=\"keys app-only sm dim\">{}</p>", w::KEYS_LINE));
        }
        // the calls in the queue's order, then any the queue does not hold
        let mut calls: Vec<&Value> = self.queue().iter().filter_map(|it| if let QSrc::Call(c) = it.src { Some(c) } else { None }).collect();
        for c in list(d, "calls") {
            if !calls.iter().any(|x| std::ptr::eq(*x, c)) {
                calls.push(c);
            }
        }
        if !calls.is_empty() {
            h.push(format!("<div class=\"list\">{}</div>", calls.iter().map(|c| self.inbox_row(c)).collect::<String>()));
        }
        let waiting = text(d, "home.waiting_line");
        if self.has_pending() && !waiting.is_empty() && !export {
            h.push(format!("<p class=\"sm wlink\">{}</p>", a("to-confirm", &esc(waiting), "")));
        } else if self.has_pending() && export {
            let owner = owner_voice(&if waiting.is_empty() { w::WAITING_EXPORT.to_string() } else { esc(waiting) });
            let link = |words: &str| a("to-confirm", words, "");
            h.push(by_viewer("p", " class=\"sm wlink\"", &link(w::WAITING_EXPORT), &link(&owner), &link(w::WAITING_EXPORT)));
        }
        h.push(self.open_items());
        let first = Some(text(d, "your_call.first")).filter(|s| !s.is_empty()).or_else(|| calls.first().map(|c| text(c, "subject")));
        let root = first.map(|s| format!("call-{s}")).filter(|c| self.ids.contains(&esc(c))).unwrap_or_else(|| "insp-none".into());
        format!(
            "<section class=\"view\" id=\"your-call\" data-nav=\"your-call\" data-title=\"{}\" data-crumb=\"{}\" data-root=\"{}\" aria-labelledby=\"h-your-call\">{}</section>",
            esc(&with_project(d, &head)),
            self.calls_word(),
            esc(&root),
            h.concat()
        )
    }

    /// A call in the inbox: why rb asks, then what it is about with its id last; folded under it, rb's label and each
    /// action's line as rb gives it, with what its review will read.
    fn inbox_row(&self, c: &Value) -> String {
        let s = text(c, "subject");
        let acts: String = list(c, "actions")
            .iter()
            .map(|x| {
                let cons = text(x, "consequence");
                let cons = if cons.is_empty() { String::new() } else { format!("<span class=\"cons\">{}{}</span>", w::REVIEW_WILL_READ, esc(cons)) };
                format!("<li><span class=\"an\">{}</span>{}{cons}</li>", esc(text(x, "label")), self.cmd(text(x, "line")))
            })
            .collect();
        let label = text(c, "label");
        let label = if label.is_empty() { String::new() } else { format!("<p class=\"lab\">{}</p>", esc(label)) };
        // its label and actions are the owner's to act on: the reader's view of the export reads the row alone
        let body = self.act(format!("<div>{label}<ul class=\"acts\">{acts}</ul></div>"));
        format!(
            "<details class=\"nd crow\" data-row data-for=\"call-{}\" data-fold=\"call-{}:row\"><summary class=\"cr\"><span class=\"cs\">{}<span class=\"ct2\">{} <span class=\"mono\">{}</span></span></span></summary>\
             <div class=\"cbody\">{body}</div></details>",
            esc(s),
            esc(s),
            reason(c),
            self.au_cited(text(c, "title"), "", ""),
            esc(s)
        )
    }

    /// Your call's line to what else is open in the record: it opens the list in the inspector beside Your call (P12,
    /// plain words pass 4: a list of details is never a fold another view's link jumps to).
    fn open_items(&self) -> String {
        let (total, _) = self.open_items_body();
        if total == 0 {
            return String::new();
        }
        // a heading of its own, as the fold it replaces was a section of Your call
        format!("<h2 class=\"oilink\">{}</h2>", a("open-items", &format!("{} ({total}) \u{2192}", w::OPEN_IN_THE_RECORD), ""))
    }

    /// What else is open in the record, once, as a card the inspector opens (P12): the Overview's line under the queue
    /// and Your call's line open it beside the view they are on, which stays where it was. "" when nothing is open.
    pub fn open_items_card(&self) -> String {
        let (total, body) = self.open_items_body();
        if total == 0 {
            return String::new();
        }
        format!(
            "<div class=\"card oic\" id=\"open-items\" data-kind=\"open-items\" data-home=\"your-call\" data-crumb=\"{}\"><div class=\"lv\" data-level=\"understand\"><h3 class=\"tt\">{} ({total})</h3>{body}</div></div>",
            w::OPEN_IN_THE_RECORD,
            w::OPEN_IN_THE_RECORD
        )
    }

    /// Open in the record, grouped by kind with rb's heading and count; the work filed under no question
    /// last. Each item's `what`, never a command. How many items, and the list.
    fn open_items_body(&self) -> (usize, String) {
        if !self.has_open_items() {
            return (0, String::new());
        }
        // a checklist grouped by what is missing (design pass 1, target 2): each group a plain sentence with its count,
        // each item one short sentence linked to its node, its id a trailing chip; rb's own line where the item is none
        // the shell can split
        let mut total = 0;
        let mut body = String::new();
        for g in list(self.doc, "open_items") {
            let code = text(g, "code");
            let rows: Vec<String> = list(g, "items").iter().flat_map(|o| self.open_rows(code, o)).collect();
            if rows.is_empty() {
                continue;
            }
            total += rows.len();
            let given = Some(text(g, "head")).filter(|s| !s.is_empty()).unwrap_or(code);
            // rb's own heading, where the shell says the group in a plain sentence, stays one click down (P8)
            let (head, under, raw) = match w::open_group(code, rows.len()) {
                Some((head, under)) => {
                    let said = [given, text(g, "under")].iter().filter(|s| !s.is_empty()).copied().collect::<Vec<_>>().join(". ");
                    let raw = format!("<details class=\"fold raw\" data-fold=\"{}\"><summary>{}</summary><p class=\"dim sm\">{}</p></details>", esc(&format!("oi:{code}")), w::DETAILS, esc(&said));
                    (head, under, raw)
                }
                None => (format!("{} ({})", given, rows.len()), text(g, "under").to_string(), String::new()),
            };
            let under = if under.is_empty() { String::new() } else { format!(" <span class=\"dim\">{}</span>", esc(&under)) };
            body.push_str(&format!("<h4 class=\"ogh\">{}{under}</h4><ul class=\"oi chk\">{}</ul>{raw}", esc(&head), rows.concat()));
        }
        let unfiled = list(self.doc, "unfiled.open_items");
        if !unfiled.is_empty() {
            total += unfiled.len();
            body.push_str(&format!(
                "<h4 class=\"ogh\">{} ({})</h4><ul class=\"oi chk\">{}</ul>",
                esc(text(self.doc, "unfiled.line")),
                unfiled.len(),
                unfiled.iter().map(|o| format!("<li>{}</li>", esc(text(o, "what")))).collect::<String>()
            ));
        }
        (total, body)
    }

    /// An open item's rows: one per rule, setting, folder or run it names where the shell can say each in a short
    /// sentence ("Per-channel INT8 at 512 calibration images: no runs yet" c-pc-512), else rb's line once.
    fn open_rows(&self, code: &str, o: &Value) -> Vec<String> {
        let what = text(o, "what");
        let subjects: Vec<&str> = text(o, "subject").split(", ").filter(|s| !s.is_empty()).collect();
        let chip = |id: &str, to: &str| {
            let id = format!("<span class=\"cid\">{}</span>", esc(&self.named(id)));
            if to.is_empty() { id } else { a(to, &id, "") }
        };
        match code {
            "untested" | "no_interval" if subjects.iter().all(|c| self.finding(c).is_some()) => subjects
                .iter()
                .map(|c| {
                    let f = self.finding(c).unwrap();
                    let n = count(f, "reading.n").unwrap_or(0);
                    let state = if n == 0 { w::NO_RUNS_YET.to_string() } else { format!("{} so far", units_said(n, text(f, "reading.unit"))) };
                    format!("<li>{}<span class=\"dim\">: {}</span> {}</li>", self.au_cited(text(f, "statement"), &self.criterion_to(c), "ans"), esc(&state), chip(c, ""))
                })
                .collect(),
            "late" if !texts(o, "criteria").is_empty() => texts(o, "criteria")
                .iter()
                .filter_map(|c| self.finding(c).map(|f| (c, f)))
                .map(|(c, f)| {
                    // rb's units line "c-pt-512 3 of 3 units": how many of its results came before it was written
                    let units = text(o, "units").split(" \u{b7} ").find_map(|u| u.strip_prefix(&format!("{c} "))).unwrap_or("");
                    let came = units.strip_suffix(" units").or_else(|| units.strip_suffix(" unit")).and_then(|u| u.split_once(" of ")).map(|(k, n)| w::came_first(k, n)).unwrap_or_default();
                    let came = if came.is_empty() { String::new() } else { format!("<span class=\"dim\">: {}</span>", esc(&came)) };
                    format!("<li>{}{came} {}</li>", self.au_cited(text(f, "statement"), &self.criterion_to(c), "ans"), chip(c, ""))
                })
                .collect(),
            "provisional" => {
                // "e-ptq/fp32.precision: fp32.precision = fp32 is provisional: ... (source note: FP32 model as trained; ...)"
                let said = what.split_once(": ").map_or(what, |(_, r)| r);
                let parsed = said.split_once(" = ").and_then(|(name, rest)| rest.split_once(" is provisional").map(|(v, rest)| (name, v, rest)));
                let row = match parsed {
                    Some((name, value, rest)) => {
                        let eid = subjects.first().and_then(|s| s.split_once('/')).map_or("", |(e, _)| e);
                        let note = rest.split_once("(source note: ").map(|(_, n)| n.split([';', ')']).next().unwrap_or("").trim()).filter(|n| !n.is_empty());
                        let note = note.map(|n| format!(" {}", w::its_note(n))).unwrap_or_default();
                        let place = if eid.is_empty() { String::new() } else { format!(" in {eid}") };
                        format!("{}{place}: {}{note}", name, w::given_not_checked(value))
                    }
                    None => said.to_string(),
                };
                let to = subjects.first().and_then(|s| s.split_once('/')).map(|(e, _)| self.to(&format!("x-{e}"), "").to_string()).unwrap_or_default();
                vec![format!("<li>{} {}</li>", esc(&row), chip(subjects.first().copied().unwrap_or(""), &to))]
            }

            _ => {
                let to = subjects.first().map(|s| self.object_to(s).unwrap_or_default()).unwrap_or_default();
                let units = text(o, "units");
                let units = if units.is_empty() { String::new() } else { format!(" <span class=\"dim\">\u{b7} {}</span>", esc(units)) };
                vec![format!("<li>{}{units}{}</li>", esc(what), subjects.first().map(|s| format!(" {}", chip(s, &to))).unwrap_or_default())]
            }
        }
    }
}

/// rb's state word, the rule axis of a finding's standing, and its colour: as rb colours it, a met word on a finding
/// not drawn green dim, and a gate's word neutral (a gate is policy, not a finding).
fn state_of(f: &Value) -> (&str, &'static str) {
    let state = text(f, "standing").split(" \u{b7} ").next().unwrap_or("");
    let good = at(f, "good").map_or(false, Value::truthy);
    let cls = match crate::surface::standing_class(state).as_str() {
        _ if text(f, "role") == "gate" => "mute",
        "good" if good => "good",
        "bad" => "bad",
        "warn" => "warn",
        _ => "mute",
    };
    (state, cls)
}

/// rb's value text: the estimate, its interval and unit as rb hands them apart; with no parts, rb's numbers as they are.
fn value_html(f: &Value) -> String {
    let parts = [("e", text(f, "reading.estimate_text")), ("iv", text(f, "reading.interval_text")), ("u", text(f, "reading.unit_text"))];
    if parts[0].1.is_empty() {
        return esc(text(f, "numbers"));
    }
    parts.iter().filter(|(_, t)| !t.is_empty()).map(|(c, t)| format!("<span class=\"{c}\">{}</span>", esc(t))).collect::<Vec<_>>().join(" ")
}

/// The result word dim after the number it qualifies, then an integrity problem in rb's words (rb's reason for a miss
/// is the units line's, so it is not said twice).
fn result_after(f: &Value) -> String {
    let mut out = result_word(f);
    let why = text(f, "row.why");
    if !why.is_empty() && why != text(f, "rule_detail") {
        out.push_str(&format!("<span class=\"res\"> \u{b7} {}</span>", esc(why)));
    }
    out
}

/// The result word dim after the number it qualifies, "" where rb gives none.
fn result_word(f: &Value) -> String {
    let result = text(f, "reading.result");
    if result.is_empty() { String::new() } else { format!("<span class=\"res\"> \u{b7} {}</span>", said_plain(result)) }
}

/// rb's glyph for a readiness row: ok, partial, or anything else (unknown, missing).
/// A readiness row's kind and the rest of rb's sentence after it: the kind in its own column (the state's where rb names
/// none), the outcome once in its place, so "ok · commit ..." under "code" never reads "ok | ok · commit ...", and
/// "code: unknown · ..." under "code" never says the kind twice.
fn ready_parts<'t>(kind: &'t str, state: &'t str, line: &'t str) -> (&'t str, &'t str) {
    let after = |line: &'t str, word: &str| -> Option<&'t str> {
        let rest = line.strip_prefix(word).filter(|_| !word.is_empty())?;
        ["", " \u{b7} ", ": "].iter().filter(|sep| if sep.is_empty() { rest.is_empty() } else { rest.starts_with(**sep) }).map(|sep| &rest[sep.len()..]).next()
    };
    let rest = after(line, kind).unwrap_or(line);
    let rest = after(rest, state).unwrap_or(rest);
    (if kind.is_empty() { state } else { kind }, rest)
}

fn glyph(state: &str) -> &'static str {
    match state {
        "ok" => "\u{2713}",
        "partial" => "\u{25d0}",
        _ => "\u{25cb}",
    }
}


/// The design's standing in rb's words, where it waits: rb's data names it by a code (`not_confirmed`, `changed`),
/// which a screen never draws; a standing already in words is drawn as given. Nothing for a design that does not wait.
fn design_said(x: &Value) -> &str {
    if !design_waits(x) {
        return "";
    }
    match text(x, "design.standing") {
        "not_confirmed" | "changed" => w::DESIGN_NOT_CONFIRMED,
        s if s.contains(' ') => s,
        _ => "",
    }
}

/// The keys of a record that say who made a write, through what, in which session or with which agent: a screen draws
/// none of them, whatever the document holds there.
const WRITER_KEYS: [&str; 9] = ["by", "declared_by", "set_by", "created_by", "actor", "actor_via", "via", "session", "agent"];

/// `said` (escaped) with each time rb writes ("2026-10-20 11:14 UTC") kept on one line.
fn nowrap_times(said: &str) -> String {
    let b = said.as_bytes();
    let shape = |i: usize| -> bool {
        // YYYY-MM-DD HH:MM UTC
        let pat = b"dddd-dd-dd dd:dd UTC";
        i + pat.len() <= b.len() && pat.iter().enumerate().all(|(k, &p)| if p == b'd' { b[i + k].is_ascii_digit() } else { b[i + k] == p })
    };
    let (mut out, mut i, mut from) = (String::new(), 0, 0);
    while i < b.len() {
        if shape(i) && (i == 0 || !b[i - 1].is_ascii_digit()) {
            out.push_str(&said[from..i]);
            out.push_str(&format!("<span class=\"nw\">{}</span>", &said[i..i + 20]));
            i += 20;
            from = i;
        } else {
            i += 1;
        }
    }
    out.push_str(&said[from..]);
    out
}


/// Whether an experiment's design waits for the user's confirmation: rb says so (`design.pending`), and while rb gives
/// no flag, a standing that says it is not confirmed. A design the user wrote, or confirmed as it stands, carries no
/// mark.
fn design_waits(x: &Value) -> bool {
    match at(x, "design.pending") {
        Some(v) => v.truthy(),
        None => text(x, "design.standing").contains("not confirmed"),
    }
}

/// The analyses the document holds, in its order.
fn analyses(doc: &Value) -> Vec<&Value> {
    match doc.get("analyses") {
        Some(Value::Dict(m)) => m.values().filter(|v| v.is_dict()).collect(),
        _ => Vec::new(),
    }
}

/// The criteria in rb's role order: primary, secondary, exploratory, gate, then any other role.
fn by_role(p: &Page, mut cids: Vec<String>) -> Vec<String> {
    let rank = |c: &String| match p.finding(c).map(|f| text(f, "role")).unwrap_or("") {
        "primary" => 0,
        "secondary" => 1,
        "exploratory" => 2,
        "gate" => 3,
        _ => 4,
    };
    cids.sort_by_key(rank);
    cids
}

/// A reading's key, the same for its count words and its finding's state: "3 criteria met" and "criterion met" are
/// "criteria-met".
fn reading_key(part: &str) -> String {
    let word = part.trim();
    let word = match word.split_once(' ') {
        Some((n, rest)) if n.chars().all(|c| c.is_ascii_digit()) => rest,
        _ => word,
    };
    let word = word.strip_prefix("criterion ").map_or(word.to_string(), |rest| format!("criteria {rest}"));
    word.split(|c: char| !c.is_ascii_alphanumeric()).filter(|s| !s.is_empty()).collect::<Vec<_>>().join("-").to_lowercase()
}

/// The command that lists a design to confirm, from the one rb gives to confirm it as listed: the same command, where it
/// runs included (`cd <its checkout> && `), without the hash; none where rb gives none (a past version offers none).
fn listing_of(confirm: &str) -> String {
    match confirm.rfind("rb confirm ") {
        Some(i) => {
            let (head, tail) = confirm.split_at(i + "rb confirm ".len());
            let exp = tail.split('@').next().unwrap_or(tail);
            format!("{head}{exp}")
        }
        None => confirm.to_string(),
    }
}

/// Whether open item `o` is rb's line that criterion `cid` is linked to no question, and about `cid` alone.
fn unlinked_item(o: &Value, cid: &str) -> bool {
    text(o, "code") == "unassigned_criterion" && texts(o, "criteria") == [cid.to_string()]
}

fn rule_and_margin(f: &Value) -> String {
    let criterion = text(f, "row.criterion");
    let criterion = if matches!(criterion, "" | "better" | "worse") { String::new() } else { format!("rule {criterion}") };
    let margin = text(f, "row.margin");
    let margin = if margin.is_empty() || margin.starts_with("margin") { margin.to_string() } else { format!("margin {margin}") };
    [criterion, margin].into_iter().filter(|s| !s.is_empty()).collect::<Vec<_>>().join(" \u{b7} ")
}

/// " · <units>" after an open item's words, when it counts units.
fn units_after(o: &Value) -> String {
    let units = text(o, "units");
    if units.is_empty() {
        String::new()
    } else {
        format!(" <span class=\"dim\">\u{b7} {}</span>", esc(units))
    }
}

/// What a question's card leads with, when the document gives a lead with words.
fn lead_of(q: &Value) -> Option<&Value> {
    at(q, "lead").filter(|l| !text(l, "text").is_empty())
}

/// A lead that is rb's computed comparison, not a recorded interpretation.
fn is_comparison(lead: &Value) -> bool {
    matches!(text(lead, "kind"), "comparison" | "relation")
}

/// rb's line that confirms everything waiting at once, as the document gives it: a line, or a line and its command.
fn confirm_all(v: Option<&Value>, past: bool) -> String {
    match v {
        Some(Value::Str(s)) if !s.is_empty() => format!("<p class=\"sm\">{}</p>", esc(s)),
        Some(v @ Value::Dict(_)) => {
            let line = Some(text(v, "line")).filter(|s| !s.is_empty()).unwrap_or(text(v, "head"));
            let cmd = Some(text(v, "confirm")).filter(|s| !s.is_empty()).unwrap_or(text(v, "command"));
            let line = if line.is_empty() { String::new() } else { format!("<p class=\"sm\">{}</p>", esc(line)) };
            line + &if cmd.is_empty() || past { String::new() } else { cmdbox(cmd) }
        }
        _ => String::new(),
    }
}

#[cfg(test)]
mod tests {
    /// PIVOT: a grid's heads say settings in plain words, keys only where a value alone would not read, a metric's
    /// unit once.
    #[test]
    fn a_grids_heads_say_settings_in_plain_words() {
        assert_eq!(setting_said("checker", "answer_tag", &[]), "Answer tag");
        assert_eq!(setting_said("problems", "250", &[]), "250 problems");
        assert_eq!(setting_said("calib_images", "128", &[]), "Calib images: 128");
        assert_eq!(setting_said("seed", "0", &[]), "Seed: 0");
        assert_eq!(setting_said("ema", "true", &[]), "Ema: true");
        assert_eq!(plain_value("step_by_step", true), "Step by step");
        assert_eq!(plain_value("step_by_step", false), "step by step");
        assert_eq!(plain_value("int8", false), "INT8");
        let m = crate::pyjson::loads(r#"{"name": "latency_ms", "unit": "ms"}"#).unwrap();
        assert_eq!(metric_title(&m), "Latency (ms)");
        let m = crate::pyjson::loads(r#"{"name": "accuracy", "unit": "percent"}"#).unwrap();
        assert_eq!(metric_title(&m), "Accuracy (%)");
    }

    use super::*;

    /// The page as `viewer` reads it (OWNER-READER): every element marked for other viewers (data-vw) cut out, as
    /// shell.css hides it, and the marks left on the rest, so a test reads the words that show.
    fn as_viewer(html: &str, viewer: &str) -> String {
        let mut out = String::new();
        let mut rest = html;
        while let Some(i) = rest.find(" data-vw=\"") {
            let open = rest[..i].rfind('<').unwrap();
            let names_end = i + 10 + rest[i + 10..].find('"').unwrap();
            let names = &rest[i + 10..names_end];
            let tag: String = rest[open + 1..].chars().take_while(|c| c.is_ascii_alphanumeric()).collect();
            if names.split(' ').any(|n| n == viewer) {
                // kept: the element whole, without its mark
                out.push_str(&rest[..i]);
                rest = &rest[names_end + 1..];
                continue;
            }
            out.push_str(&rest[..open]);
            // cut to its own closing tag, counting the same tag opened inside it
            let (mut depth, mut j) = (0usize, open);
            loop {
                let next_open = rest[j + 1..].find(&format!("<{tag}")).map(|k| k + j + 1);
                let next_close = rest[j + 1..].find(&format!("</{tag}>")).map(|k| k + j + 1).unwrap();
                match next_open {
                    Some(o) if o < next_close => {
                        depth += 1;
                        j = o;
                    }
                    _ if depth > 0 => {
                        depth -= 1;
                        j = next_close;
                    }
                    _ => {
                        rest = &rest[next_close + tag.len() + 3..];
                        break;
                    }
                }
            }
        }
        out.push_str(rest);
        out
    }

    /// The document's citations as M's model gives them (CITE-FIELDS.md, Oct 25): README.md:3 and :7 quoted, a log's
    /// line never quoted on a shared page, a line the file does not have.
    fn cited_doc() -> Value {
        let mut d = doc("owner");
        let cite = |key: &str, path: &str, line: u32, missing: &str, nq: &str| {
            let (folder, name) = path.rsplit_once('/').map_or(("".to_string(), path.to_string()), |(f, n)| (format!("{f}/"), n.to_string()));
            crate::pyjson::loads(&format!(
                r#"{{"key": "{key}", "raw": ["{key}"], "path": "{path}", "name": "{name}", "folder": "{folder}", "line": {line}, "end": {line}, "label": "{path} \u00b7 line {line}", "missing": "{missing}", "not_quoted": "{nq}", "quoted": {}}}"#,
                missing.is_empty() && nq.is_empty()
            ))
            .unwrap()
        };
        if let Value::Dict(m) = &mut d {
            let mut c = crate::value::Dict::new();
            c.insert("README.md:3".to_string(), cite("README.md:3", "README.md", 3, "", ""));
            c.insert("README.md:7".to_string(), cite("README.md:7", "README.md", 7, "", ""));
            c.insert("logs/agent.log:2809".to_string(), cite("logs/agent.log:2809", "logs/agent.log", 2809, "", "Not quoted on a shared page"));
            c.insert("README.md:40".to_string(), cite("README.md:40", "README.md", 40, "README.md has no line 40 at commit 6adf8a5", ""));
            m.insert("citations".into(), Value::Dict(c));
        }
        d
    }

    fn page_of_doc(d: &Value, surface: Surface) -> Page<'_> {
        Page { doc: d, surface, ids: HashSet::new(), card_ids: HashSet::new(), elsewhere: "", most_late: Default::default(), past: false }
    }

    /// CITE item 2: a note's file:line citations are chips, "README.md · line 3" with the file's name bold, each found by
    /// the forms the document gives (never README.md:30 for README.md:3, nor one run into a longer path); a chip opens
    /// its Source view where the page holds its card (C's `cite-<key>`); a line rb could not read says so beside it, and
    /// one a shared page may not quote says so beside it too. A note with no citation is drawn as before.
    #[test]
    fn a_notes_citations_are_chips_that_open_their_source() {
        let d = cited_doc();
        let mut p = page_of_doc(&d, Surface::Export);
        let said = p.au_cited("see README.md:3 and README.md:7", "", "");
        assert_eq!(
            said,
            "<span class=\"au\">see <span class=\"cite\" data-cite=\"README.md:3\" title=\"README.md\"><b>README.md</b> \u{b7} line 3</span> and \
             <span class=\"cite\" data-cite=\"README.md:7\" title=\"README.md\"><b>README.md</b> \u{b7} line 7</span></span>"
        );
        p.ids.insert(esc("cite-README.md:3"));
        assert!(p.au_cited("see README.md:3.", "", "").contains("<a class=\"cite\" href=\"#cite-README.md:3\" data-cite=\"README.md:3\""));
        let log = p.au_cited("cited logs/agent.log:2809", "", "");
        assert!(log.contains("<span class=\"cite nq\" data-cite=\"logs/agent.log:2809\" title=\"logs/agent.log \u{b7} Not quoted on a shared page\"><span class=\"cdir\">logs/</span><b>agent.log</b> \u{b7} line 2809</span> <span class=\"cmiss\">(Not quoted on a shared page)</span>"), "{log}");
        assert!(p.au_cited("README.md:40", "", "").ends_with("</span> <span class=\"cmiss\">(README.md has no line 40 at commit 6adf8a5)</span></span>"));
        for none in ["README.md:30", "xREADME.md:3", "docs/README.md:3", "README.md:3-4", "README.md:3rd", "README.md:3:4"] {
            assert!(!p.au_cited(none, "", "").contains("cite"), "{none}");
        }
        assert_eq!(p.au_cited("no file here", "", ""), au("no file here"));
        // a note whose words open its rule: the words linked, each chip apart (a link holds no link)
        let linked = p.au_cited("as README.md:7 says", "c-c1", "ans");
        assert!(linked.starts_with("<span class=\"au\"><a class=\"ans\" href=\"#c-c1\">as </a><span class=\"cite\"") && linked.ends_with("<a class=\"ans\" href=\"#c-c1\"> says</a></span>"), "{linked}");
        // no citations in the document: as before
        let bare = doc("owner");
        assert_eq!(page_of_doc(&bare, Surface::Page).au_cited("see README.md:3", "", ""), au("see README.md:3"));
    }

    /// OWNER-READER: each wording once, marked with the viewers it is for; one wording for all three unmarked; ""
    /// draws nothing for its viewer.
    #[test]
    fn a_wording_is_drawn_once_for_the_viewers_it_is_for() {
        assert_eq!(by_viewer("span", "", "Open calls", "Your call", "Open calls"), "<span data-vw=\"none reader\">Open calls</span><span data-vw=\"owner\">Your call</span>");
        assert_eq!(by_viewer("p", " class=\"x\"", "a", "a", "a"), "<p class=\"x\">a</p>");
        assert_eq!(by_viewer("div", "", "<b>do</b>", "<b>do</b>", ""), "<div data-vw=\"none owner\"><b>do</b></div>");
        assert_eq!(as_viewer("<p>x <span data-vw=\"none reader\">Open calls</span><span data-vw=\"owner\">Your <span>call</span></span>.</p>", "owner"), "<p>x <span>Your <span>call</span></span>.</p>");
    }

    /// The reader's view shows no command: rb's line keeps what it says of the state and drops what hands a command over.
    #[test]
    fn a_line_in_the_readers_view_hands_over_no_command() {
        assert_eq!(reader_line("design not confirmed \u{b7} list it to confirm: rb confirm e1"), w::READER_DESIGN_NOT_CONFIRMED);
        assert_eq!(reader_line("The design of 2 experiments is not confirmed: rb confirm lists them."), "The design of 2 experiments is not confirmed.");
        assert_eq!(reader_line("Changed .json, .yaml or .toml files that nothing in the record cites (2): rb reconcile lists each by path."), "Changed .json, .yaml or .toml files that nothing in the record cites (2).");
        assert_eq!(reader_line("2 changed since you confirmed it"), "2 changed since you confirmed it");
    }

    /// OWNER-READER: the export names its calls and their lead line for each viewer the hub names: the owner's working
    /// view says Your call, Needs your judgment and that the page gives the command, with each call's actions; the reader's says Open calls and Still open, with no command, button or action; with no viewer
    /// named the export's own words stand. rb page and rb open draw no mark at all.
    #[test]
    fn the_export_names_the_calls_for_the_owner_and_for_the_reader() {
        let mut d = doc("owner");
        if let Value::Dict(m) = &mut d {
            let call = crate::pyjson::loads(r#"{"subject": "c4", "title": "INT8 answers in 7 ms or less", "label": "Review this result", "actions": [{"label": "Accept", "line": "rb decide c4 accept --why '<why>'", "consequence": "accepted"}]}"#).unwrap();
            m.insert("calls".into(), Value::List(vec![call]));
            m.insert("your_call".into(), crate::pyjson::loads(r#"{"n": 1}"#).unwrap());
            m.insert("pending".into(), crate::pyjson::loads(r#"{"rows": [{"kind": "proposed", "id": "d1", "text": "investigate c6", "confirm": "rb confirm d1"}], "designs_line": "The design of 1 experiment is not confirmed: rb confirm lists them."}"#).unwrap());
        }
        let export = page(&d, Surface::Export);
        // the views alone: the inspector's cards are C's (nodes.rs), which say their own part (OWNER-READER, Owners)
        let views = |v: &str| {
            let said = as_viewer(&export, v);
            said[said.find("<body").unwrap()..said.find("<div class=\"nodes\">").unwrap()].to_string()
        };
        let (none, owner, reader) = (views("none"), views("owner"), views("reader"));
        for (said, has, lacks) in [
            (&none, [w::OPEN_CALLS, w::NEEDS_JUDGMENT, w::OPEN_CALLS_LEDE], [">Your call", w::STILL_OPEN, w::OWNER_CALLS_LEDE]),
            (&owner, [w::YOUR_CALL, w::NEEDS_YOUR_JUDGMENT, w::OWNER_CALLS_LEDE], [">Open calls<", w::STILL_OPEN, w::READER_CALLS_LEDE]),
            (&reader, [w::OPEN_CALLS, w::STILL_OPEN, w::READER_CALLS_LEDE], [">Your call", ">Needs judgment", w::OWNER_CALLS_LEDE]),
        ] {
            for h in has {
                assert!(said.contains(h), "{h}");
            }
            for l in lacks {
                assert!(!said.contains(l), "{l}: {}", &said[said.find(l).unwrap().saturating_sub(300)..]);
            }
        }
        // the owner and the export opened from disk keep the calls' actions and the commands that confirm; the reader
        // reads each call's row, and the state in words
        for said in [&none, &owner] {
            assert!(said.contains("rb decide c4 accept") && said.contains("rb confirm d1"));
        }
        assert!(!reader.contains("rb decide") && !reader.contains("rb confirm") && !reader.contains("data-decide=") && !reader.contains("data-copybtn"), "{reader}");
        assert!(reader.contains("INT8 answers in 7 ms or less") && reader.contains(w::READER_NOT_CONFIRMED) && reader.contains("The design of 1 experiment is not confirmed."));
        // an attribute holds the export's own word: the script says the owner's (A, OWNER-READER)
        assert!(export.contains("data-title=\"Open calls (1) \u{b7} INT8 ResNet-50 on ImageNet\" data-crumb=\"Open calls\" data-root="), "{export}");
        for surface in [Surface::Page, Surface::Open] {
            let html = page(&d, surface);
            assert!(!html.contains("data-vw=") && html.contains(w::YOUR_CALL), "{surface:?}");
        }
    }

    fn doc(tier: &str) -> Value {
        crate::pyjson::loads(&format!(
            "{{\"contract\": \"rb-shell-view/1.0\", \"tier\": \"{tier}\", \"project\": {{\"id\": \"int8-resnet50\", \"title\": \"INT8 ResNet-50 on ImageNet\"}}}}"
        ))
        .unwrap()
    }

    /// A design that waits lists itself to confirm with rb's own command, where it runs kept, and the hash cut.
    #[test]
    fn a_designs_row_lists_it_with_rbs_command() {
        assert_eq!(listing_of("rb confirm e1@94f795b7"), "rb confirm e1");
        assert_eq!(listing_of("cd /p/wt-b && rb confirm e1@94f795b7"), "cd /p/wt-b && rb confirm e1");
        assert_eq!(listing_of(""), "");
    }

    /// One count, one meaning (D3): the export's empty queue is "Needs judgment" with no count and says "Nothing needs
    /// judgment."; rb page and rb open keep "Needs your judgment" and their own empty line.
    #[test]
    fn an_exports_empty_queue_needs_no_judgment() {
        let export = as_viewer(&page(&doc("owner"), Surface::Export), "none");
        assert!(export.contains("<h2 id=\"h-queue\"><span>Needs judgment</span></h2>"), "{export}");
        assert!(export.contains(w::NOTHING_NEEDS_JUDGMENT_EXPORT) && !export.contains(w::NOTHING_NEEDS_JUDGMENT));
        for (surface, tier) in [(Surface::Page, "owner"), (Surface::Open, "live")] {
            let html = page(&doc(tier), surface);
            assert!(html.contains("<h2 id=\"h-queue\">Needs your judgment</h2>") && html.contains(w::NOTHING_NEEDS_JUDGMENT), "{surface:?}");
            assert!(!html.contains(w::NOTHING_NEEDS_JUDGMENT_EXPORT), "{surface:?}");
        }
    }

    /// One bar (plain words pass 4): the top bar says rb's mark, the record's title, an empty slot for the view's name and
    /// the time; the record's id and rb's version are About's. rb page and rb open keep About and the controls in the bar;
    /// the export, which the hub shows under its own bar, draws About in the navigator and no control.
    #[test]
    fn the_top_bar_names_the_record_by_its_title_and_about_says_its_id() {
        for surface in [Surface::Page, Surface::Export, Surface::Open] {
            let mut d = doc("owner");
            if let Value::Dict(m) = &mut d {
                m.insert("rb_version".into(), Value::Str("0.6.0".into()));
                m.insert("at".into(), Value::Str("2026-10-25 01:12 UTC".into()));
            }
            let html = page(&d, surface);
            let top = &html[html.find("<header").unwrap()..];
            let top = &top[..top.find("</header>").unwrap()];
            assert!(top.contains("<span class=\"ttl\"><span class=\"au\">INT8 ") && top.contains("<span class=\"vcrumb\"></span>") && top.contains("2026-10-25 01:12 UTC"), "{surface:?}: {top}");
            assert!(!top.contains("int8-resnet50") && !top.contains("0.6.0") && !top.contains("research record"), "{surface:?}: {top}");
            let about = &html[html.find("id=\"about\"").unwrap()..];
            assert!(about.contains("Research record <span class=\"mono\">int8-resnet50</span> \u{b7} rb 0.6.0"), "{surface:?}");
            let nav = &html[html.find("<nav class=\"nav\"").unwrap()..];
            let nav = &nav[..nav.find("</nav>").unwrap()];
            assert_eq!(top.contains("href=\"#about\""), surface != Surface::Export, "{surface:?}");
            assert_eq!(nav.contains("<div class=\"navctl\"><a class=\"ctl abt\" href=\"#about\">"), surface == Surface::Export, "{surface:?}");
            assert_eq!(html[html.find("<body>").unwrap()..html.find("<main").unwrap()].contains("data-theme"), surface != Surface::Export, "{surface:?}");
        }
    }

    /// A page's styles keep no space a browser does not read (guarantee 29's small page is under 212,000 bytes with
    /// them), and keep every one it does: between two words of a selector or a value, and inside a quoted string.
    #[test]
    fn the_styles_keep_only_the_spaces_a_browser_reads() {
        let css = "/* a note */\n.nav  .ttl > a ,\n.cp:hover {\n  border: 1px solid var(--line) ;\n  content: \", \\\" { ; }\" ;\n}\n@media (max-width: 1279px) and (hover: none) {\n  html.app .rail { display: grid; }\n}\n";
        assert_eq!(
            squeeze(css),
            ".nav .ttl>a,.cp:hover{border:1px solid var(--line);content:\", \\\" { ; }\"}@media (max-width:1279px) and (hover:none){html.app .rail{display:grid}}"
        );
        let all = styles(Surface::Open);
        for gap in [": ", "; ", " {", "{ ", ", ", ";}", "\n"] {
            assert!(!all.contains(gap), "{gap:?}");
        }
    }

    /// rb page and rb open inline exactly `shell::SCRIPT`, the bytes the export names by sha256; the export inlines none.
    #[test]
    fn a_page_inlines_the_script_rb_names() {
        for (surface, tier) in [(Surface::Page, "owner"), (Surface::Export, "owner"), (Surface::Open, "live")] {
            let html = page(&doc(tier), surface);
            let runs = surface != Surface::Export && !SCRIPT.trim().is_empty();
            assert_eq!(html.contains(&format!("<script>{SCRIPT}</script>")), runs, "{surface:?}");
            assert_eq!(html.contains("<script"), runs, "{surface:?}");
        }
    }

    /// The pointer guide's styles go where its script runs: rb page and rb open inline guide.css, squeezed as the
    /// shell's own are, after the shell's and before print's; rb export, which runs no script, inlines none of it.
    #[test]
    fn the_guide_is_styled_where_its_script_runs() {
        let guide = squeeze(GUIDE_CSS);
        assert!(guide.contains(".guide-spot") && !guide.contains("/*"));
        for surface in [Surface::Page, Surface::Export, Surface::Open] {
            let html = page(&doc("owner"), surface);
            let at = html.find(&guide);
            assert_eq!(at.is_some(), surface != Surface::Export, "{surface:?}");
            if let Some(at) = at {
                let (shell, print) = (html.find(&squeeze(SHELL_CSS)).unwrap(), html.find(&squeeze(PRINT_CSS)).unwrap());
                assert!(shell < at && at < print, "{surface:?}: the guide's rules sit after the shell's and before print's");
            }
            assert_eq!(html.contains(".guide-spot"), surface != Surface::Export, "{surface:?}");
        }
    }

    /// A's four hooks (the pickers, the scope bar, a past version's notice, Elsewhere) are drawn on rb open and on no
    /// page: rb page and rb export draw none of them, whatever the document holds, before the tier check is reached.
    #[test]
    fn only_rb_open_draws_its_places() {
        MARK_HOOKS.with(|m| m.set(true));
        let drawn: Vec<(Surface, String)> = [Surface::Page, Surface::Export, Surface::Open].into_iter().map(|s| (s, page(&doc("owner"), s))).collect();
        MARK_HOOKS.with(|m| m.set(false));
        for (surface, html) in &drawn {
            let marks = html.matches("HOOK-MARK:").count();
            if *surface == Surface::Open {
                for name in ["top_bar", "scope_bar", "past_version", "elsewhere"] {
                    assert_eq!(html.matches(&format!("HOOK-MARK:{name}<")).count(), 1, "{name} on rb open");
                }
                assert_eq!(marks, 4);
            } else {
                assert_eq!(marks, 0, "{surface:?} drew one of rb open's places");
            }
        }
        assert!(!page(&doc("owner"), Surface::Open).contains("HOOK-MARK"), "the seam is off once the test ends");
    }

    /// Guarantee 38: a past version offers no command. rb open's views of one draw no command box, no copy slot and no
    /// command as code (the cards are C's, rb open's frame A's), while the same document drawn live keeps them.
    #[test]
    fn a_past_version_draws_no_command_in_the_views() {
        const PAST: &str = include_str!("../../../tests/fixtures/shell/int8-demo.past.view.json");
        let views = |doc: &Value| {
            let html = page(doc, Surface::Open);
            let (a, b) = (html.find("<div class=\"nodes\">").unwrap(), html.find("<div class=\"inone\"").unwrap());
            let body = html.find("<body>").unwrap();
            format!("{}{}", &html[body..a], &html[b..])
        };
        let past = crate::pyjson::loads(PAST).unwrap();
        let marks = ["class=\"cmdbox\"", "class=\"cmd", "data-copybtn", "<code>", "rb confirm e1@"];
        let live_html = {
            let mut live = past.clone();
            live.as_dict_mut().unwrap().get_mut("places").unwrap().as_dict_mut().unwrap().get_mut("place").unwrap().as_dict_mut().unwrap().insert("version".into(), Value::Null);
            views(&live)
        };
        assert!(marks[..3].iter().all(|m| live_html.contains(m)), "drawn live, the views offer their commands");
        let html = views(&past);
        for m in marks {
            assert!(!html.contains(m), "a past version draws {m}");
        }
        assert!(live_html.contains("cmd prompt") && html.contains("<p class=\"prompt\">"), "the Next step stays, in words");
        // nor as words: a step's command, a resource's way to get it again
        assert!(live_html.contains("python get_data.py --split val"));
        assert!(!html.contains("python get_data.py") && !html.contains("python quantize.py"), "a past version names a command");
    }

    /// A past version leaves out a part that names a recorded command as a whole span, never one holding it inside a
    /// word ("eval" in "evaluation data", "sh" in "sha256") or a one-word command with more after it ("python 3.11.9").
    #[test]
    fn a_past_version_drops_only_the_parts_that_name_a_command() {
        let doc = crate::pyjson::loads(r#"{"runs": {"r1": {"command": "eval"}, "r2": {"command": {"text": "python"}}, "r3": {"command": "sh"}, "r4": {"command": "python eval.py --seed 1"}}}"#).unwrap();
        let p = Page { doc: &doc, surface: Surface::Open, ids: HashSet::new(), card_ids: HashSet::new(), elsewhere: "", most_late: Default::default(), past: true };
        let line = "evaluation data \u{b7} python 3.11.9 \u{b7} sha256 ab12 \u{b7} command: eval \u{b7} python \u{b7} ran python eval.py --seed 1 \u{b7} python eval.py --seed 10";
        assert_eq!(p.said_past(line), "evaluation data \u{b7} python 3.11.9 \u{b7} sha256 ab12 \u{b7} python eval.py --seed 10");
        // the cards' rule: a command a sentence's full stop follows is still named, as on the cards (MP-1024-0356)
        let line = "made by python eval.py --seed 1. \u{b7} again: python eval.py --seed 1...) \u{b7} python eval.py --seed 1.5";
        assert_eq!(p.said_past(line), "python eval.py --seed 1.5");
    }

    /// The live example as rb open's tab draws it, its place given rb's real id (Look 3).
    fn live_open() -> (Value, String) {
        let mut live = crate::pyjson::loads(include_str!("../../../tests/fixtures/shell/int8-demo.live.view.json")).unwrap();
        let place = live.as_dict_mut().unwrap().get_mut("places").unwrap().as_dict_mut().unwrap().get_mut("place").unwrap().as_dict_mut().unwrap();
        place.insert("id".into(), Value::Str("p_9e48e7a24e".into()));
        let html = page(&live, Surface::Open);
        (live, html)
    }

    fn overview_of(html: &str) -> &str {
        let at = html.find("<section class=\"view\" id=\"overview\"").or_else(|| html.find("id=\"overview\"")).unwrap();
        let end = html[at + 1..].find("<section class=\"view\"").map_or(html.len(), |e| at + 1 + e);
        &html[at..end]
    }

    /// Look 3, film pass 1: the Overview's header names the place by its branch ("main"), never by rb's id nor its path.
    #[test]
    fn the_overviews_header_names_the_place_main_never_its_id() {
        let (_, html) = live_open();
        let line = &html[html.find("<p class=\"counts ovh\">").unwrap()..];
        let line = &line[..line.find("</p>").unwrap()];
        assert!(line.contains("<span class=\"place\">main</span>"), "{line}");
        assert!(!html.contains("p_9e48e7a24e") && !line.contains("~/"), "{line}");
        assert_eq!(place_name("detached at 4364dc8c1a2b \u{b7} ~/p"), "detached");
        // a record outside git: no branch and no commit, so no name rather than words cut short
        assert_eq!(place_name("detached at  \u{b7} /tmp/e/nogit"), "");
    }

    /// Look 3, film pass 3: Elsewhere, with a worktree's absolute path and its cd ... && rb status, sits inside the fold
    /// How rb reads this repository, never on the Overview's face.
    #[test]
    fn elsewhere_sits_under_how_rb_reads_this_repository() {
        let (_, html) = live_open();
        let fold = html.find("data-fold=\"how-made\"").expect("the fold");
        let end = fold + html[fold..].find("</details></div>").unwrap();
        let at = html.find("id=\"elsewhere\"").expect("Elsewhere is drawn");
        assert!(fold < at && at < end, "Elsewhere is inside the fold");
        let outside = format!("{}{}", &html[..fold], &html[end..]);
        assert!(!outside.contains("/home/ana/int8-resnet50/.worktrees/b"), "a worktree's path outside the fold");
    }

    /// Look 3, film pass 4: the Overview carries one count of what waits, the queue's; Your call's is in the nav only,
    /// and the fold of what waits for a confirmation counts nothing of its own. Fix 2: under its count of rows, what
    /// they are, counted, its calls the same number as the nav's Your call.
    #[test]
    fn the_overview_counts_only_its_queue() {
        let (live, html) = live_open();
        let o = overview_of(&html);
        assert!(o.contains("<summary>Waiting for your confirmation</summary>"), "the fold has no count");
        let n = list(&live, "home.queue").len();
        let yours = count(&live, "your_call.n").unwrap();
        assert_ne!(yours as usize, n);
        // FIX4 (proposal 1): the heading counts nothing, so it never reads as a third total beside the line and the nav
        assert!(n > 0 && o.contains(&format!("<h2 id=\"h-queue\">{}</h2>", w::NEEDS_YOUR_JUDGMENT)), "the queue's heading is its words alone");
        let export = page(&live, Surface::Export);
        let head = &export[export.find("<h2 id=\"h-queue\">").expect("the export's queue") + 18..];
        let head = &head[..head.find("</h2>").unwrap()];
        assert!(!head.contains("class=\"n\"") && !head.chars().any(|c| c.is_ascii_digit()), "{head}");
        let counted = &o[o.find("<p class=\"qn sm dim\">").expect("the queue says what its rows are")..];
        let counted = &counted[..counted.find("</p>").unwrap()];
        // fix 3: things, never rows, each named, so the line agrees with the list of what waits (4 there: rb status's
        // 1 proposed call, 1 past call and 2 designs, which one row confirms) and with what rb found
        assert_eq!(list(&live, "pending.rows").len(), 4);
        assert_eq!(counted, format!("<p class=\"qn sm dim\">{yours} calls{DOT}1 proposed call, 1 past call and 2 designs waiting for your confirmation{DOT}1 result and 1 changed file from the repository"));
        let nav = &html[html.find("<nav class=\"nav\"").unwrap()..];
        assert!(nav.contains(&format!("{}<span class=\"n\">{yours}</span>", w::YOUR_CALL.to_string() + " ")), "the nav's badge counts the same calls");
        for bad in [format!("({yours})"), "Your call".to_string()] { assert!(!o.contains(&bad), "{bad}: {}", &o[o.find(&bad).unwrap().saturating_sub(300)..][..400]); }
    }

    /// Rerun this research (REPRODUCE-WORDS.md): only an export with a clone address draws it, for every viewer and never
    /// at a past version or on rb page or rb open; the panel's prompt is rb's text whole, rb's addresses beside it for
    /// the hub's metas to take the place of; an experiment's Reproduce place has its own prompt naming it and its rules; with no repository
    /// the code line says the bundle does not say where the code is.
    #[test]
    fn rerun_this_research_is_drawn_only_with_a_clone_address() {
        let (mut live, _) = live_open();
        let d = live.as_dict_mut().unwrap();
        d.insert("reproduce_url".into(), Value::Str(String::new()));
        let bare = page(&live, Surface::Export);
        assert!(!bare.contains(w::RERUN) && !bare.contains("id=\"rerun\"") && !bare.contains("data-rb-bundle"));
        let at = "https://example.org/int8/bundle.zip";
        live.as_dict_mut().unwrap().insert("reproduce_url".into(), Value::Str(at.into()));
        // one of e1's rules a gate, as the INT8 demo's g1 is
        let c2 = live.as_dict_mut().unwrap().get_mut("findings").unwrap().as_dict_mut().unwrap().get_mut("c2").unwrap();
        c2.as_dict_mut().unwrap().insert("role".into(), Value::Str("gate".into()));
        for surface in [Surface::Page, Surface::Open] {
            assert!(!page(&live, surface).contains(w::RERUN), "{surface:?}");
        }
        let html = page(&live, Surface::Export);
        // the bar's button, at the right before the time, and the Overview's, each opening the one panel
        let bar = &html[html.find("<header class=\"top\"").unwrap()..];
        let bar = &bar[..bar.find("</header>").unwrap()];
        assert!(bar.contains(&format!("<span class=\"end\"><a class=\"rrb rrun rrbar\" href=\"#rerun\" data-rerun>{}</a><span class=\"stamp\">", w::RERUN)), "{bar}");
        assert_eq!(html.matches("href=\"#rerun\" data-rerun>").count(), 2);
        assert_eq!(html.matches("id=\"rerun\"").count(), 1);
        let panel = &html[html.find("<aside class=\"rerun rrun\" id=\"rerun\"").unwrap()..];
        let panel = &panel[..panel.find("</aside>").unwrap()];
        let id = text(&live, "project.id").to_string();
        // the page by the one address the reader has, as the hub's rb-page meta names it: the folder --url gave
        let prompt = w::rerun_prompt("https://example.org/int8/", at, &id, None);
        let shown = &panel[panel.find("id=\"rerun-prompt\">").unwrap() + 18..];
        let shown = &shown[..shown.find("</pre>").unwrap()];
        // rb's text whole; rb's own addresses on the panel, for the hub's metas to take the place of
        assert_eq!(shown, esc(&prompt));
        assert!(panel.contains(&format!(" data-rb-page=\"https://example.org/int8/\" data-rb-bundle=\"{at}\">")), "{panel}");
        assert!(!html.contains("page.html"));
        assert!(panel.contains(&format!("data-copybtn=\"rerun-prompt\" data-label=\"{}\"", w::COPY_PROMPT)) && panel.contains(w::RERUN_LINE));
        // by hand, the prompt's steps: the same folder in rb clone, then rb context in it
        assert!(panel.contains(">pip install rabbit-brain</pre>") && panel.contains(&format!(">rb clone {at} {id}</pre>")), "{panel}");
        assert!(panel.contains(&format!(">cd {id} &amp;&amp; rb context</pre>")) && prompt.contains(&format!("rb clone {at} {id}.")), "{panel}");
        // with no script, a Close back to the Overview, before the title (A's script draws its own in the dialog)
        assert!(panel.contains(&format!("><a class=\"rrclose\" href=\"#overview\">{}</a><h2 id=\"h-rerun\">", w::CLOSE)), "{panel}");
        assert!(panel.contains(w::CODE_UNNAMED) && panel.contains(w::REPRODUCE_AFTER));
        // an experiment's own prompt, in its Reproduce place
        let rules = texts(&list(&live, "experiments")[0], "findings");
        let eid = text(&list(&live, "experiments")[0], "id").to_string();
        let title = text(&list(&live, "experiments")[0], "title").to_string();
        assert!(!rules.is_empty());
        let own = w::rerun_prompt("P", "B", &id, Some((&eid, &title, &rules)));
        assert!(own.contains(&format!("Run experiment {eid}, \u{201c}{title}\u{201d}: its commands")));
        // the experiment's own prompt names its rules by id, its gates left out: a gate is met or missed, never reproduced
        let gates: Vec<&String> = rules.iter().filter(|c| text(&live, &format!("findings.{c}.role")) == "gate").collect();
        assert!(!gates.is_empty());
        let mut asked: Vec<String> = rules.iter().filter(|c| !gates.contains(c)).cloned().collect();
        asked.sort();
        let drawn = &html[html.find(&format!("id=\"x-{eid}:prompt\">")).unwrap()..];
        let drawn = &drawn[..drawn.find("</pre>").unwrap()];
        assert!(drawn.contains(&format!("which of its rules ({}) reproduced", asked.join(", "))), "{drawn}");
        assert!(gates.iter().all(|g| !drawn.contains(&format!("{g},")) && !drawn.contains(&format!("{g})"))));
        assert!(html.contains(&format!("data-copybtn=\"x-{eid}:prompt\" data-label=\"{}\"", w::COPY_PROMPT_EXPERIMENT)));
        // with the repository named, the code line rb export says
        let repo = crate::pyjson::loads(r#"{"url": "https://github.com/ana/int8-resnet.git", "commit": "5f04ee1180aa"}"#).unwrap();
        live.as_dict_mut().unwrap().insert("reproduce_repo".into(), repo);
        let html = page(&live, Surface::Export);
        let said = "The code the runs used: <a href=\"https://github.com/ana/int8-resnet.git\">https://github.com/ana/int8-resnet.git</a> at commit <span class=\"oid\">5f04ee1180</span>";
        assert!(html.contains(&format!("{said}. The record and")) && !html.contains(w::CODE_UNNAMED) && !html.contains("played against"), "{html}");
        // what the copy had not committed (C's repo.changed, where the manifest has it), after the commit
        let repo = crate::pyjson::loads(r#"{"url": "https://github.com/ana/int8-resnet.git", "commit": "5f04ee1180aa", "changed": ["configs/sweep.yaml"]}"#).unwrap();
        live.as_dict_mut().unwrap().insert("reproduce_repo".into(), repo);
        let html = page(&live, Surface::Export);
        assert!(html.contains(&format!("{said}, with changes not committed: <span class=\"mono\">configs/sweep.yaml</span>. The record and")), "{html}");
        let repo = crate::pyjson::loads(r#"{"url": "https://github.com/ana/int8-resnet.git", "commit": "5f04ee1180aa", "changed": []}"#).unwrap();
        live.as_dict_mut().unwrap().insert("reproduce_repo".into(), repo);
        assert!(page(&live, Surface::Export).contains(&format!("{said}. The record and")));
    }

    /// Look 7: no raw id is visible text. The parts the views draw name a run by its label, a note by what it is on, an
    /// analysis by its title; a run's row opens its receipt. The cards (C's), the recipes (P's) and rb's sentences (the
    /// log's, a finding's) are left out here: they are their writers' requests.
    #[test]
    fn the_views_show_no_raw_id() {
        let (mut live, _) = live_open();
        let d = live.as_dict_mut().unwrap();
        d.get_mut("home").unwrap().as_dict_mut().unwrap().insert("notes".into(), crate::pyjson::loads(r#"[{"id": "nq2v7kd", "text": "Calibration used 512 ImageNet train images.", "at": "2026-10-11"}]"#).unwrap());
        let x = d.get_mut("experiments").unwrap().as_list_mut().unwrap()[0].as_dict_mut().unwrap();
        x.insert("notes".into(), crate::pyjson::loads(r#"["The held-out set is not in the pretraining data."]"#).unwrap());
        x.insert("note_ids".into(), crate::pyjson::loads(r#"["n3w5yhb"]"#).unwrap());
        let eid = x.get("id").and_then(Value::as_str).unwrap().to_string();
        let mut ids: Vec<String> = live.get("runs").and_then(Value::as_dict).unwrap().keys().cloned().collect();
        ids.extend(["nq2v7kd", "n3w5yhb"].map(String::from));
        // others' words, which name ids until their writers' requests land: P's recipes, rb's sentences
        fn scrub(v: &mut Value, ids: &[String], theirs: bool) {
            match v {
                Value::Dict(m) => m.iter_mut().for_each(|(k, x)| scrub(x, ids, theirs || ["reproduce", "says", "sentence", "timing"].contains(&k.as_str()))),
                Value::List(l) => l.iter_mut().for_each(|x| scrub(x, ids, theirs)),
                Value::Str(t) if theirs => ids.iter().for_each(|id| *t = t.replace(id.as_str(), "ID")),
                _ => {}
            }
        }
        scrub(&mut live, &ids, false);
        for surface in [Surface::Export, Surface::Page, Surface::Open] {
            let html = page(&live, surface);
            let mut shown = html.clone();
            // others' parts: the cards, the recipes and commands (rb's sentences of the log are scrubbed above; the
            // objects each History row names after its sentence are the page's, and are read)
            if let (Some(a), Some(b)) = (shown.find("<div class=\"nodes\">"), shown.find("<div class=\"inone\"")) {
                shown.replace_range(a..b, "");
            }
            for (open, close) in [("<pre", "</pre>"), ("<script", "</script>"), ("<style", "</style>")] {
                while let Some(a) = shown.find(open) {
                    let b = shown[a..].find(close).map_or(shown.len(), |e| a + e + close.len());
                    shown.replace_range(a..b, "");
                }
            }
            // the visible text: what is between tags
            let mut text_ = String::new();
            let mut inside = false;
            for c in shown.chars() {
                match c {
                    '<' => inside = true,
                    '>' => {
                        inside = false;
                        text_.push(' ');
                    }
                    c if !inside => text_.push(c),
                    _ => {}
                }
            }
            let words: Vec<&str> = text_.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_')).collect();
            for id in &ids {
                assert!(!words.contains(&id.as_str()), "{surface:?} shows {id}: {}", text_.find(id.as_str()).map(|i| text_[i.saturating_sub(300)..(i + 80).min(text_.len())].to_string()).unwrap_or_default());
            }
            assert!(html.contains(&format!("<span class=\"nd2\">note on {eid}:</span>")) && html.contains("<span class=\"nd2\">note on the project (2026-10-11):</span>"), "{surface:?}");
            assert!(html.contains("data-id=\"n3w5yhb\"") && html.contains(&format!(">{}</a></td></tr>", w::RECEIPT_LINK)), "{surface:?}");
            // each site by name (MP-1027-1335's check): the History row's objects after its sentence, the Runs tables'
            // rows, the Analyses list with what it reads
            let log = &html[html.find("<ol class=\"hlist log\">").expect("the log")..];
            let log = &log[..log.find("</ol>").unwrap()];
            let row = log.split("<li").find(|r| r.contains(">run fp32 and int8 \u{b7} seed 1</a>")).expect("runs1's row");
            assert!(row.contains("<span>record <a href=\"#") && row.matches("seed 1").count() == 1, "{surface:?}: {row}");
            let an = html.split("<li>").find(|r| r.starts_with("<a") && r.contains(">per-class accuracy, int8 against fp32</a>")).expect("the analysis");
            assert!(an.contains(">fp32 and int8 \u{b7} seed 1</a>"), "{surface:?}: {an}");
        }
    }

    /// A Raw log row says each object once (M's FIX8 request; FIX9): an object its sentence says is linked inside the
    /// sentence, a run by its name, and only the objects it does not say follow it.
    #[test]
    fn a_log_row_says_each_object_once() {
        let (live, _) = live_open();
        for surface in [Surface::Export, Surface::Page, Surface::Open] {
            let html = page(&live, surface);
            let log = &html[html.find("<ol class=\"hlist log\">").expect("the log")..];
            let log = &log[..log.find("</ol>").unwrap()];
            let row = |has: &str| log.split("<li").find(|r| r.contains(has)).unwrap_or_else(|| panic!("{surface:?}: no row with {has}")).to_string();
            let call = row("proposed a call: accept <a class=\"mono\" href=\"#c-c4\">");
            assert!(call.contains("accept <a class=\"mono\" href=\"#") && call.matches(">c4<").count() == 1 && !call.contains("class=\"refs\""), "{surface:?}: {call}");
            let run = row(">run fp32 and int8 \u{b7} seed 3</a>");
            assert!(run.matches("seed 3").count() == 1 && !run.contains("class=\"refs\""), "{surface:?}: {run}");
            // two runs with one label: the replacing run linked in its sentence, the replaced one after it, said apart
            let again = row("seed 2, recorded 2026-10-13 14:12 UTC</a>");
            assert!(again.contains("<span class=\"refs\"> \u{b7} <a href=\"#") && again.contains(">run fp32 and int8 \u{b7} seed 2, recorded 2026-10-06 10:32 UTC</a></span>"), "{surface:?}: {again}");
            // a ref the sentence does not say still follows it
            let made = row(">run fp32 and int8 \u{b7} seed 1</a>");
            assert!(made.contains("class=\"refs\"") && made.contains("ckpt-int8-s1"), "{surface:?}: {made}");
        }
    }

    /// A word is linked only where it stands whole: an id is never linked inside a dotted id (ids may hold a "."), and
    /// a sentence's own "." ends it (FIX9 review).
    #[test]
    fn an_id_is_linked_whole_never_inside_a_dotted_id() {
        let forms: Vec<Form> = vec![(vec!["e1".to_string()], "x-e1".to_string(), "")];
        let (html, unsaid) = linked_forms("record e1.ckpt for e1", &forms);
        assert_eq!(html, "record e1.ckpt for <a href=\"#x-e1\">e1</a>");
        assert!(unsaid.is_empty());
        let (html, _) = linked_forms("locked e1.", &forms);
        assert_eq!(html, "locked <a href=\"#x-e1\">e1</a>.");
        let (html, unsaid) = linked_forms("record e1.ckpt", &forms);
        assert_eq!((html.as_str(), unsaid.len()), ("record e1.ckpt", 1));
    }

    /// A retracted run's last cell in a Runs table says the run is retracted, then its receipt's link: never "receipt
    /// retracted" (Look 9, FIX9).
    #[test]
    fn a_retracted_runs_cell_says_the_run_is_retracted() {
        let (live, _) = live_open();
        for surface in [Surface::Export, Surface::Page, Surface::Open] {
            let html = page(&live, surface);
            let gone: Vec<&str> = html.split("<tr").filter(|r| r.contains(" class=\"gone\">") && r.contains("<td class=\"rid\">")).collect();
            assert!(!gone.is_empty(), "{surface:?}");
            for r in gone {
                let cell = &r[r.find("<td class=\"rid\">").unwrap()..];
                assert!(cell.starts_with(&format!("<td class=\"rid\"><span class=\"tag\">{}</span> \u{b7} <a href=", w::RUN_RETRACTED)), "{surface:?}: {cell}");
                assert!(cell.contains(&format!(">{}</a></td></tr>", w::RECEIPT_LINK)), "{surface:?}: {cell}");
            }
        }
    }

    /// The prompt's steps are P's flow (out/rerun/prompt.md): one rule is named as one, several as a list; rb clone
    /// gets the code, or the agent stops, with the --code that gets it; the command that puts a run's code here is said
    /// in words, never as git's line, so the step holds if rb's line changes.
    #[test]
    fn a_prompt_names_its_experiments_rules() {
        let one = w::rerun_prompt("P", "B", "int8", Some(("e1", "INT8 vs FP32", &["c1".to_string()])));
        assert!(one.contains("tell me whether its rule c1 reproduced, with its numbers.") && one.starts_with("Rerun this research with Rabbit Brain (rb). Its record: P\n\n1. Install rb: pip install rabbit-brain\n2. Clone the record into a new folder: rb clone B int8. It gets the code too,"));
        let all = w::rerun_prompt("P", "B", "int8", None);
        assert!(all.contains("4. Get those resources. Run each experiment's commands in the order rb context names them, each as rb journal run -- <command>;") && all.contains("which rules reproduced and which did not, with their numbers."));
        assert!(all.contains("tell me what it says and stop: once I give you an address of the code, rb clone B <another new folder> --code <that address> gets it."));
        assert!(all.contains("where rb context names a command that puts a run's code here, run that one first.") && !all.contains("git ") && !all.contains("checkout"));
        assert!(!all.contains('\u{2014}') && one.contains("Run experiment e1, \u{201c}INT8 vs FP32\u{201d}: its commands in the order") && !all.contains("Run experiment"));
        // the record lands in the code's checkout (CODE_TRAVELS), and rb reconcile names the arm where a run states it
        assert!(all.contains("at the commit the record names, and puts the record in it. If") && !all.contains("beside"), "{all}");
        assert!(all.contains("record the run it proposes with the rb run add line it prints.\n5. ") && !all.contains("--variant"), "{all}");
    }

    /// A's second review: a title holding quotes keeps rb's quotes the only ones that open and close it, its own made
    /// typographic, so a copied prompt never reads `"INT8 vs "FP32" ..."`.
    #[test]
    fn a_title_holding_a_quote_is_quoted_once() {
        let own = w::rerun_prompt("P", "B", "int8", Some(("e1", "INT8 vs \"FP32\" on ana's held-out set", &["c1".to_string()])));
        assert!(own.contains("Run experiment e1, \u{201c}INT8 vs \u{201c}FP32\u{201d} on ana\u{2019}s held-out set\u{201d}: its commands"), "{own}");
        let step = own.lines().find(|l| l.starts_with("4. ")).unwrap();
        assert!(!step.contains('"') && !step.contains("ana's"), "{step}");
    }

    /// FIX4: a metric the comparison table leaves out (named for no arm) heads the runs table with the unit rb's reading
    /// of it says, as its neighbours do: "ONNX top-1 (%)" beside "Top-1 (%)", never one with a unit and one without.
    #[test]
    fn a_metric_named_for_no_arm_heads_the_runs_table_with_its_unit() {
        let mut live = crate::pyjson::loads(include_str!("../../../tests/fixtures/shell/int8-demo.live.view.json")).unwrap();
        let d = live.as_dict_mut().unwrap();
        for x in d.get_mut("experiments").unwrap().as_list_mut().unwrap() {
            let x = x.as_dict_mut().unwrap();
            if x.get("id").and_then(Value::as_str) == Some("e1") {
                let t = x.get_mut("table").unwrap().as_dict_mut().unwrap().get_mut("metrics").unwrap().as_list_mut().unwrap();
                t.retain(|m| m.get("name").and_then(Value::as_str) != Some("onnx_top1"));
            }
        }
        // read in another experiment: a unit is the metric's (review should 2: e2's runs table read "ONNX top-1"); a
        // difference's reading never lends its unit ("points")
        let level = crate::pyjson::loads(r#"{"experiment": "e2", "row": {"metric": "onnx_top1"}, "reading": {"unit_said": "%"}}"#).unwrap();
        let diff = crate::pyjson::loads(r#"{"experiment": "e1", "row": {"metric": "change.onnx_top1"}, "reading": {"unit_said": "points"}}"#).unwrap();
        d.get_mut("findings").unwrap().as_dict_mut().unwrap().insert("c0".into(), diff);
        d.get_mut("findings").unwrap().as_dict_mut().unwrap().insert("c9".into(), level);
        let html = page(&live, Surface::Open);
        assert!(html.contains(">ONNX top-1 (%)</th>"), "the runs table's head");
        assert!(!html.contains(">ONNX top-1</th>"));
    }

    /// Fix 3 (M's request 135): a question's own view says rb's questions[].no_rule, the line its Overview card says, so
    /// a parent whose rule is linked to its sub-question never reads "No rule tests this question yet." there.
    #[test]
    fn a_question_s_view_says_its_overview_line() {
        let mut live = crate::pyjson::loads(include_str!("../../../tests/fixtures/shell/int8-demo.live.view.json")).unwrap();
        let line = "No rule is linked to this question itself; 1 rule is linked to its sub-questions.";
        for q in live.as_dict_mut().unwrap().get_mut("questions").unwrap().as_list_mut().unwrap() {
            let q = q.as_dict_mut().unwrap();
            if q.get("id").and_then(Value::as_str) == Some("q1") {
                q.insert("findings".into(), Value::List(Vec::new()));
                q.insert("no_rule".into(), Value::Str(line.into()));
            }
        }
        let html = page(&live, Surface::Open);
        let at = html.find("id=\"q-q1\"").expect("q1's view");
        let view = &html[at..];
        let view = &view[..view[1..].find("<section class=\"view\"").map_or(view.len(), |e| e + 1)];
        assert!(view.contains(&format!("<span class=\"hl\">{line}</span>")), "{view}");
        assert!(!view.contains(w::NO_RULE_TESTS));
        // on the export, in each viewer's voice, as the Overview card says it (review: the owner's "your confirmation")
        let waits = "No rule tests this question yet. 1 experiment design waits for a confirmation.";
        for q in live.as_dict_mut().unwrap().get_mut("questions").unwrap().as_list_mut().unwrap() {
            let q = q.as_dict_mut().unwrap();
            if q.get("id").and_then(Value::as_str) == Some("q1") {
                q.insert("no_rule".into(), Value::Str(waits.into()));
            }
        }
        let html = page(&live, Surface::Export);
        let view = &html[html.find("id=\"q-q1:evidence\"").expect("q1's line")..];
        let view = &view[..view.find("</p>").unwrap()];
        assert!(view.contains(&format!("<span data-vw=\"owner\">{}</span>", owner_voice(waits))) && view.contains(&format!("<span data-vw=\"none reader\">{waits}</span>")), "{view}");
    }

    /// Look 3, film pass 5: rb open's top bar shows no absolute path at any width: what it says, its hidden parts too,
    /// names a place by its short path; the whole path is only on hover (a title), as AGENTS.md says.
    #[test]
    fn the_top_bar_says_no_absolute_path() {
        let (live, html) = live_open();
        let top = &html[html.find("<header").unwrap()..];
        let top = &top[..top.find("</header>").unwrap()];
        let mut said = String::new();
        let mut inside = false;
        for c in top.chars() {
            match c {
                '<' => inside = true,
                '>' => { inside = false; said.push(' '); }
                _ if !inside => said.push(c),
                _ => {}
            }
        }
        assert!(said.contains("main"), "{said}");
        let mut paths = vec![text(&live, "places.place.path").to_string()];
        paths.extend(list(&live, "places.places").iter().map(|p| text(p, "path").to_string()));
        for p in paths.iter().filter(|p| !p.is_empty()) {
            assert!(!said.contains(p.as_str()), "the top bar says {p}: {said}");
        }
        assert!(!said.contains("/home/"), "{said}");
    }

    /// Design pass 1, P5: numbers show two decimals, or two significant digits where all are under 0.1 on a scale with no
    /// percent, so a difference of 0.0029 on a top-1 of 0.76 never reads "0.00"; never "-0.00".
    #[test]
    fn numbers_take_the_decimals_their_scale_needs() {
        assert_eq!(places("%", &[-0.08, -0.23, 0.07]), 2);
        assert_eq!(places("", &[-0.0029, -0.0046, -0.0012]), 4);
        assert_eq!(places("", &[0.64]), 2);
        assert_eq!(places("", &[]), 2);
        assert_eq!(dec(-0.0029, 4, false), "-0.0029");
        assert_eq!(dec(0.07, 2, true), "+0.07");
        assert_eq!(dec(-0.001, 2, true), "0.00");
        assert_eq!(diff_said(-0.0029, 4), "0.0029 lower");
        assert_eq!(diff_said(0.001, 2), "under 0.01 higher");
        assert_eq!(diff_said(0.0, 2), "no difference");
        assert_eq!(units_said(3, "class"), "3 classes");
        assert_eq!(units_said(3, "seed"), "3 seeds");
    }

    /// Design pass 1, targets 5 and 6: keys and arms read as words in a heading, never a key in capitals.
    #[test]
    fn a_heading_says_a_metric_and_an_arm_in_words() {
        assert_eq!(metric_label("top1", "%"), "Top-1 (%)");
        assert_eq!(metric_label("size_kb", ""), "Size (KB)");
        assert_eq!(metric_label("latency_ms", "ms"), "Latency (ms)");
        assert_eq!(plain_key("calib_images"), "Calib images");
        assert_eq!(arm_label("int8"), "INT8");
        assert_eq!(arm_label("per_channel"), "per_channel");
    }

    /// Design pass 1, target 6: an experiment's header is one line led by what it is, the usual "not locked" left out.
    #[test]
    fn an_experiments_header_is_one_line() {
        assert_eq!(who_said("experiment \u{b7} open \u{b7} not locked \u{b7} 14 runs"), "Experiment \u{b7} open \u{b7} 14 runs");
        let locked = "locked 2026-10-06 10:20 UTC: the spec and criteria are locked; a later change is recorded as an amendment.";
        assert_eq!(who_said(locked), locked);
        let r = crate::pyjson::loads(r#"{"ready_line": "to run again: x", "ready": [{"state": "ok"}, {"state": "ok"}, {"state": "partial"}, {"state": "unknown"}]}"#).unwrap();
        assert_eq!(ready_said(Some(&r)), "<span title=\"to run again: x\">Rerun recipe: 2 of 4 parts ready</span>");
    }

    /// A run's numbers read from rb's line of them, whichever form it takes.
    #[test]
    fn a_runs_numbers_read_by_arm_and_metric() {
        let r = crate::pyjson::loads(r#"{"metrics_text": "fp32: top1=0.7598 onnx_top1=0.7569 \u00b7 int8: top1=0.7569", "results": "results/seed1/metrics.json"}"#).unwrap();
        let k = |a: &str, m: &str, v: &str| ((a.to_string(), m.to_string()), v.to_string());
        assert_eq!(run_numbers(&r), vec![k("fp32", "top1", "0.7598"), k("fp32", "onnx_top1", "0.7569"), k("int8", "top1", "0.7569")]);
        let r = crate::pyjson::loads(r#"{"metrics_text": "", "results": "fp32.top1=90.64 \u00b7 int8.top1=90.46"}"#).unwrap();
        assert_eq!(run_numbers(&r), vec![k("fp32", "top1", "90.64"), k("int8", "top1", "90.46")]);
    }

    /// Design pass 1, P8: rb's reading of a rule as one plain sentence, its direction in words, once.
    #[test]
    fn a_reading_is_one_plain_sentence() {
        let f = crate::pyjson::loads(r#"{"state": "meets", "subject_label": "int8", "reference_label": "fp32 baseline", "rule": "change.top1 at least -1",
            "reading": {"estimate": -0.08, "lo": -0.23, "hi": 0.07, "n": 3, "unit": "seed", "unit_text": "%"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "INT8 is 0.08 points lower than the FP32 baseline (-0.23 to +0.07), 3 seeds.");
        // a cited rule over two arms is a comparison; a cited level names the cited number
        let f = crate::pyjson::loads(r#"{"state": "undetermined", "subject_label": "pc", "reference_label": "pt baseline", "cited": {"path": "R.md"}, "check_band": {"target": 0},
            "reading": {"estimate": 0.004, "lo": -0.0109, "hi": 0.0189, "n": 3, "unit": "seed", "unit_text": "%"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "pc is under 0.01 points higher than the pt baseline (-0.01 to +0.02), 3 seeds.");
        let f = crate::pyjson::loads(r#"{"state": "untested", "reading": {"estimate": null}}"#).unwrap();
        assert_eq!(plain_reading(&f), "No results yet.");
    }

    /// FIX5 (M's FIX4 request): a level with no arm named is said by the title its row gives the metric, as the row
    /// and the card say it, never "Measured"; a row that gives none, the metric's own title.
    #[test]
    fn a_level_with_no_arm_is_said_by_its_metrics_title() {
        let f = crate::pyjson::loads(r#"{"state": "undetermined", "subject_label": "", "rule": "onnx_top1 at least 75.5", "reading_short": "ONNX top-1 is 75.38%",
            "row": {"metric": "onnx_top1"}, "reading": {"estimate": 75.38, "lo": null, "hi": null, "n": 1, "unit": "seed", "unit_text": "%", "unit_said": "%"}}"#).unwrap();
        assert!(plain_reading(&f).starts_with("ONNX top-1 is 75.38%, 1 seed"), "{}", plain_reading(&f));
        let f = crate::pyjson::loads(r#"{"state": "meets", "subject_label": "", "rule": "onnx_top1 at least 75.5", "reading_short": "",
            "row": {"metric": "onnx_top1"}, "reading": {"estimate": 75.6, "lo": null, "hi": null, "n": 1, "unit": "seed", "unit_text": "%", "unit_said": "%"}}"#).unwrap();
        assert!(plain_reading(&f).starts_with("ONNX top-1 is 75.60%"), "{}", plain_reading(&f));
    }

    /// Fix 2, item 1: a unit is said, never used as a name. A percentage by any catalogue name is "%" for a level and
    /// "points" for a difference; a variant compared with itself names the setting each side is selected by.
    #[test]
    fn a_unit_is_said_and_a_self_comparison_names_its_sides() {
        let f = crate::pyjson::loads(r#"{"state": "meets", "subject_label": "int8", "rule": "int8.top1 at least 70",
            "reading": {"estimate": 75.7, "lo": null, "hi": null, "n": 3, "unit": "seed", "unit_text": "percent", "unit_said": "%"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "INT8 is 75.70%, 3 seeds.");
        let f = crate::pyjson::loads(r#"{"state": "meets", "subject_label": "int8", "reference_label": "fp32 baseline", "rule": "change.top1 at least -1",
            "reading": {"estimate": -0.5, "lo": null, "hi": null, "n": 3, "unit": "seed", "unit_text": "percent", "unit_said": "points"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "INT8 is 0.50 points lower than the FP32 baseline, 3 seeds.");
        // with no unit_said (an older document), the catalogue's "pct" still reads as a percentage
        let f = crate::pyjson::loads(r#"{"state": "meets", "subject_label": "int8", "reference_label": "fp32 baseline",
            "reading": {"estimate": -0.5, "n": 3, "unit": "seed", "unit_text": "pct"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "INT8 is 0.50 points lower than the FP32 baseline, 3 seeds.");
        let f = crate::pyjson::loads(r#"{"state": "meets", "subject_label": "int8", "reference_label": "int8",
            "rule": "val_loss: int8 vs int8 [where seed=1; subject where width=512; reference where width=256] ≤ 0",
            "reading": {"estimate": -0.05, "n": 2, "unit": "", "unit_text": "nats", "unit_said": "nats"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "INT8 at width 512 is 0.050 nats lower than INT8 at width 256, 2 runs.");
        // a text value's quotes stay where they are what tells the sides apart; a quoted "]" is part of the value
        let f = crate::pyjson::loads(r#"{"state": "meets", "subject_label": "int8", "reference_label": "int8",
            "rule": "val_loss: int8 vs int8 [subject where mode=\"1\"; reference where mode=1] \u2264 0",
            "reading": {"estimate": -0.1, "n": 2, "unit": "", "unit_text": "nats"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "INT8 at mode \"1\" is 0.10 nats lower than INT8 at mode 1, 2 runs.");
        let f = crate::pyjson::loads(r#"{"state": "meets", "subject_label": "int8", "reference_label": "int8",
            "rule": "val_loss: int8 vs int8 [subject where tag=\"a]\"; reference where tag=\"b]\"] \u2264 0",
            "reading": {"estimate": -0.1, "n": 2, "unit": "", "unit_text": "nats"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "INT8 at tag a] is 0.10 nats lower than INT8 at tag b], 2 runs.");
        // a metric with no unit is still named once, after its number
        let f = crate::pyjson::loads(r#"{"state": "meets", "subject_label": "fp32 baseline", "reading": {"estimate": 0.7597, "n": 3, "unit": "seed", "unit_text": "top1", "unit_said": "top1"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "FP32 baseline is 0.76 on Top-1, 3 seeds.");
    }

    /// Fix 3, item 4 (Look 4): the question's rule list says a variant in plain words, an interval with its unit in the
    /// cards' brackets, and a cited number in its rule's own digits, as every view says it.
    #[test]
    fn a_reading_says_names_intervals_and_cited_numbers_as_the_cards_do() {
        let f = crate::pyjson::loads(r#"{"state": "misses", "subject_label": "step_by_step", "reference_label": "plain baseline", "rule": "accuracy: step_by_step vs plain [where checker=answer_tag] ≥ 5",
            "reading": {"estimate": 3.6, "lo": 0.619, "hi": 6.581, "n": 3, "unit": "seed", "unit_text": "percent", "unit_said": "points"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "step by step is 3.60 points higher than the plain baseline (+0.62 to +6.58), 3 seeds.");
        let f = crate::pyjson::loads(r#"{"state": "misses", "subject_label": "plain baseline", "reference_label": "cited", "cited": {"path": "README.md"}, "check_band": {"target": 49.6},
            "rule": "accuracy: plain [where checker=answer_tag, problems=250] = 49.6 ± 0.05",
            "reading": {"estimate": 13.0667, "lo": 11.0, "hi": 15.14, "n": 3, "unit": "seed", "unit_text": "percent", "unit_said": "%"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "plain baseline is 13.07% (11.00% to 15.14%), 3 seeds; the cited number is 49.6%.");
        // with no rule text, the number's own shortest digits
        let f = crate::pyjson::loads(r#"{"state": "misses", "subject_label": "fp32 baseline", "reference_label": "cited", "cited": {"path": "R.md"}, "check_band": {"target": 0.7613},
            "reading": {"estimate": 0.7597, "lo": 0.7591, "hi": 0.7603, "n": 3, "unit": "seed", "unit_text": "top1"}}"#).unwrap();
        assert_eq!(plain_reading(&f), "FP32 baseline is 0.7597 (0.7591 to 0.7603) on Top-1, 3 seeds; the cited number is 0.7613.");
    }

    /// Fix 2, item 2: one title per metric on a page, and a record's dotted key by its own name in words.
    #[test]
    fn one_title_per_metric_and_keys_in_words() {
        let m = crate::pyjson::loads(r#"{"name": "top1", "unit": "percent"}"#).unwrap();
        assert_eq!(metric_title(&m), metric_label("top1", "percent"));
        assert_eq!(metric_title(&m), "Top-1 (%)");
        assert_eq!(metric_label("top1", "%"), "Top-1 (%)");
        // an acronym (C's list) and a precision code keep their capitals, as nodes.rs's metric_said says them (FIX6)
        assert_eq!(metric_label("onnx_top1", ""), "ONNX top-1");
        assert_eq!(metric_label("int8_top1", "%"), "INT8 top-1 (%)");
        assert_eq!(metric_label("gpu_latency_ms", "ms"), "GPU latency (ms)");
        assert_eq!(metric_label("val.f1", ""), "Val F1");
        assert_eq!(metric_label("share_under_2pt", ""), "Share under 2pt");
        assert_eq!(metric_label("top1", "top1"), "Top-1");
        assert_eq!(setting_said("data.batch_size", "32", &[]), "Batch size: 32");
        assert_eq!(plain_key("optim.lr"), "Lr");
        // two of one last name are each said whole, in a heading and a metric's title, so two never read as one
        let keys = vec!["model.lr".to_string(), "optim.lr".to_string(), "data.batch_size".to_string()];
        assert_eq!(setting_said("optim.lr", "0.1", &keys), "Optim lr: 0.1");
        assert_eq!(setting_said("data.batch_size", "32", &keys), "Batch size: 32");
        assert_ne!(metric_label("eval.top1", "pct"), metric_label("train.top1", "pct"));
        assert_eq!(metric_label("eval.top1", "pct"), "Eval top-1 (%)");
    }

    /// Fix 2, item 5: what waits "for a confirmation" is the owner's on the owner's view of the export, as rb page and
    /// rb open say it; a reader's and a page with no viewer keep "a".
    #[test]
    fn the_owner_s_view_says_your_confirmation() {
        assert_eq!(owner_voice("1 experiment design waits for a confirmation."), "1 experiment design waits for your confirmation.");
        assert_eq!(owner_voice(w::WAITING_EXPORT), w::WAITING);
        assert_eq!(owner_voice(w::WAITING_OTHER_EXPORT), w::WAITING_OTHER);
        let (live, _) = live_open();
        let html = page(&live, Surface::Export);
        let fold = &html[html.find("data-fold=\"to-confirm\"><summary>").expect("the fold")..];
        let summary = &fold[..fold.find("</summary>").unwrap()];
        assert!(summary.contains("<span data-vw=\"owner\">Waiting for your confirmation</span>"), "{summary}");
        assert!(summary.contains("<span data-vw=\"none reader\">Waiting for a confirmation</span>"), "{summary}");
        // every owner's line on the export that says what waits says "your"
        let owner_lines = html.split("data-vw=\"owner\">").skip(1).map(|t| &t[..t.find('<').unwrap_or(t.len())]);
        for line in owner_lines {
            assert!(!line.contains("for a confirmation"), "{line}");
        }
    }

    /// Fix 3 (independent check, later): an export opened with no viewer named says how a found result is recorded in no
    /// one's voice; "from your screen" is the owner's line alone.
    #[test]
    fn an_export_with_no_viewer_says_no_your_for_what_rb_found() {
        let (live, _) = live_open();
        assert!(!list(&live, "home.found").is_empty());
        let html = page(&live, Surface::Export);
        let found = &html[html.find("<ul class=\"fl\">").expect("what rb found")..];
        let found = &found[..found.find("</ul>").unwrap()];
        assert!(found.contains(" data-vw=\"none\">") && found.contains(w::FOUND_HOW_NONE), "{found}");
        for part in found.split("<span class=\"fh\"").skip(1) {
            assert!(!part.contains("from your screen") || part.starts_with(" data-vw=\"owner\""), "{part}");
        }
        // FIX4 (independent check, later): one row says once that it is not in the record, in rb's hint, and then how
        let none = &found[found.find(" data-vw=\"none\">").unwrap()..];
        let none = &none[..none.find("</span>").unwrap()];
        assert!(none.contains("not in the record") && !none.contains("not recorded"), "{none}");
    }

    /// The timing glossary says what M's rb context says: a rule whose baseline's runs alone came first is read on its
    /// subject's runs.
    #[test]
    fn the_timing_glossary_says_how_a_baseline_first_reads() {
        let (_, said) = w::HINTS.iter().find(|(k, _)| *k == "timing").unwrap();
        assert!(said.starts_with("declared before the evidence (before its runs, when only its baseline's runs came first: timing is read on the subject's runs);"), "{said}");
    }

    /// Fix 2, item 4: no bare id heads a group of sub-questions on the Overview; the parent is named by its words, as the
    /// nav names it.
    #[test]
    fn a_group_of_sub_questions_is_headed_by_its_parent_s_words() {
        let (mut live, _) = live_open();
        let qs = live.as_dict_mut().unwrap().get_mut("questions").unwrap();
        let q2 = crate::pyjson::loads(r#"{"id": "q2", "text": "Is INT8 fast enough to serve on one A100?", "depth": 0, "status": "open"}"#).unwrap();
        if let Value::List(l) = qs { l.push(q2) }
        let html = page(&live, Surface::Open);
        let o = overview_of(&html);
        let grp = &o[o.find("<li class=\"qgrp\">").expect("a group line")..];
        let grp = &grp[..grp.find("</li>").unwrap()];
        assert!(grp.contains("href=\"#q-q1\"") && grp.contains("ImageNet"), "{grp}");
        assert!(!grp.contains(">q1</a>"), "{grp}");
    }
}
