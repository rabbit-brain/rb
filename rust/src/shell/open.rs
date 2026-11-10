//! What rb open adds to the frame (A fills it): the top bar's Where and Version pickers, the scope
//! bar, Elsewhere and the past-version rule. R's frame calls each on rb open only; each draws from the document's
//! `places` (Live tier) and draws nothing where the document has none.
//!
//! A picker is a link: `data-place` names the place (empty for the place rb open started on) and `data-at` the version
//! (empty for now). The script shows the choice through rb open's live module, which fetches it from the page itself,
//! as rb open asks; the link's own address keeps the choice in the page's address (`#overview&place=<id>&at=<sha>`).
//! A version is named by its commit and the time it was committed, never by its subject or its author.

use crate::value::Value;

fn esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            _ => out.push(c),
        }
    }
    out
}

/// A value as it stands in the address: what is not a letter, a digit or one of `-._~` as %XX of its bytes.
fn quote(s: &str) -> String {
    s.bytes()
        .map(|b| if b.is_ascii_alphanumeric() || b"-._~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") })
        .collect()
}

fn at<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |v, k| v.get(k)).filter(|v| !v.is_null())
}

fn text<'a>(v: &'a Value, path: &str) -> &'a str {
    at(v, path).and_then(Value::as_str).unwrap_or("")
}

fn list<'a>(v: &'a Value, path: &str) -> &'a [Value] {
    at(v, path).and_then(Value::as_list).map(Vec::as_slice).unwrap_or(&[])
}

fn truthy(v: &Value, path: &str) -> bool {
    at(v, path).is_some_and(Value::truthy)
}

/// The address a choice keeps: the Overview of that place and version; the start place, live, is the view alone.
fn href(place: &str, sha: &str) -> String {
    let mut h = String::from("#overview");
    if !place.is_empty() {
        h.push_str("&place=");
        h.push_str(&quote(place));
    }
    if !sha.is_empty() {
        h.push_str("&at=");
        h.push_str(&quote(sha));
    }
    h
}

/// A place's label in a picker's list: it breaks only after a ' · ' or a '/', never at a hyphen inside a name.
fn unbroken(label: &str) -> String {
    let mut h = String::new();
    for (i, part) in label.split(" \u{b7} ").enumerate() {
        if i > 0 {
            h.push_str(" \u{b7} ");
        }
        let segs: Vec<&str> = part.split('/').collect();
        for (k, seg) in segs.iter().enumerate() {
            let tail = if k + 1 < segs.len() { "/" } else { "" };
            h.push_str(&format!("<span class=\"nw\">{}{tail}</span>", esc(seg)));
            if k + 1 < segs.len() {
                h.push_str("<wbr>");
            }
        }
    }
    h
}

/// A command to copy, drawn as the frame draws one.
fn cmdbox(cmd: &str) -> String {
    format!("<div class=\"cmdbox\" data-copybtn><pre class=\"cmd\">{}</pre></div>", esc(cmd))
}

/// The day a version was committed, as its button and the scope bar say it ("2026-10-13"): with the minute ("2026-10-13
/// 15:02 UTC") only where another version listed was committed the same day, so two never read alike. Its commit is in
/// the Version list alone.
fn version_day(when: &str, versions: &[Value]) -> String {
    let day = when.get(..10).unwrap_or(when);
    let same = versions.iter().filter(|v| text(v, "at").get(..10) == Some(day)).count();
    if same > 1 { when.to_string() } else { day.to_string() }
}

/// An evidence line's text, each part it draws in monospace (`code`) in a code element.
fn coded(l: &Value) -> String {
    let code: Vec<String> = list(l, "code").iter().filter_map(|c| c.as_str().map(str::to_string)).collect();
    crate::checkout::segments(text(l, "text"), &code).iter().map(|(m, p)| if *m { format!("<code>{}</code>", esc(p)) } else { esc(p) }).collect()
}

/// The lines under the place shown in the Where list: the evidence one step down from the words on the button, as
/// checkout.rs made it for every surface (PARITY 4): what rb found, dated by a time rb open's live script ages, with the
/// commit's Copy (all 40 hex); git's own words, the files, the code, in monospace where they are git's or a path; the
/// command that checks it, with its own Copy; and what record files are. A 1.15 document has only the fold.
fn fold_lines(doc: &Value) -> String {
    let evidence = list(doc, "checkout.evidence");
    if evidence.is_empty() {
        return fold_lines_115(doc);
    }
    evidence
        .iter()
        .map(|l| match text(l, "kind") {
            "found" => {
                let when = text(l, "when");
                let iso = text(l, "at");
                let mut line = coded(l);
                if !when.is_empty() && !iso.is_empty() {
                    let tag = format!(
                        "<time datetime=\"{}\" title=\"{}\" data-checked>{}</time>",
                        esc(iso),
                        esc(&crate::ledger::support::minute(iso)),
                        esc(when)
                    );
                    line = line.replacen(&esc(when), &tag, 1);
                }
                let mut h = format!("<p class=\"fl\" id=\"where-found\">{line}</p>");
                let sha = text(l, "sha");
                if !sha.is_empty() {
                    h.push_str(&format!(
                        "<p class=\"cprow\" data-copybtn=\"where-sha\"><span class=\"sha\" id=\"where-sha\" hidden>{}</span>\
                         <button type=\"button\" class=\"cp\" data-act=\"copy\" data-from=\"where-sha\" aria-label=\"Copy the commit\">Copy</button></p>",
                        esc(sha)
                    ));
                }
                h
            }
            "git" => format!("<p class=\"fl\"><code>{}</code></p>", esc(text(l, "text"))),
            "check" => {
                let says = text(l, "says");
                format!(
                    "<p class=\"fl\">{}</p><div class=\"cmdbox\"><pre class=\"cmd\">{}</pre><button type=\"button\" class=\"cp\" data-act=\"copy\" aria-label=\"Copy the command\">Copy</button></div>{}",
                    esc(text(l, "text")),
                    esc(text(l, "cmd")),
                    if says.is_empty() { String::new() } else { format!("<p class=\"fl\">{}</p>", esc(says)) }
                )
            }
            "define" => format!("<p class=\"fl dim\">{}</p>", esc(text(l, "text"))),
            _ => format!("<p class=\"fl\">{}</p>", coded(l)),
        })
        .collect()
}

/// The Where list's lines as a 1.15 document carries them (checkout.fold): the commit, with Copy, a note, or git's
/// own words in monospace (the rule git ignores record files by is quoted in monospace too).
fn fold_lines_115(doc: &Value) -> String {
    let rule = text(doc, "checkout.ignored_by");
    list(doc, "checkout.fold")
        .iter()
        .map(|l| {
            let t = text(l, "text");
            match text(l, "kind") {
                "commit" => format!(
                    "<p class=\"fl\">{}</p><p class=\"cprow\" data-copybtn=\"where-sha\"><span class=\"sha\" id=\"where-sha\" hidden>{}</span>\
                     <button type=\"button\" class=\"cp\" data-act=\"copy\" data-from=\"where-sha\" aria-label=\"Copy the commit\">Copy</button></p>",
                    esc(t),
                    esc(text(l, "sha"))
                ),
                "git" => format!("<p class=\"fl\"><code>{}</code></p>", esc(t)),
                _ => match t.split_once(rule).filter(|_| !rule.is_empty()) {
                    Some((a, b)) => format!("<p class=\"fl\">{}<code>{}</code>{}</p>", esc(a), esc(rule), esc(b)),
                    None => format!("<p class=\"fl\">{}</p>", esc(t)),
                },
            }
        })
        .collect()
}

/// The worktree rb open was started in, where it has no record of its own: its own commit, said as yours.
fn yours(p: &Value) -> String {
    let Some(h) = at(p, "started_here") else { return String::new() };
    let sha: String = text(h, "commit").chars().take(12).collect();
    let mut t = if sha.is_empty() { "your checkout \u{b7} no commit yet".to_string() } else { format!("your checkout \u{b7} at commit {sha}") };
    if !sha.is_empty() && truthy(h, "changes_outside_record") {
        t.push_str(", with changes outside the record not committed");
    }
    format!("<p class=\"fl\">{}</p>", esc(&t))
}

/// The Where and Version pickers, each a fold that lists its choices as links, the one shown marked current, one rb
/// open cannot show said with why. Where is drawn at every place and in every state: its button names the place (at
/// the place rb open started on, live, as its checkout names it: its branch, "no branch", or its folder where git
/// cannot say; elsewhere by its label's branch) and, while a place is shown as it is now, how its checkout holds the
/// record, in rb's words; its list says the place shown first, with its commit one step down, then each other place
/// with rb's line for it. The fold opens without the script too, and stays within a 390 px screen.
pub fn top_bar(doc: &Value) -> String {
    let Some(place) = at(doc, "places.place") else { return String::new() };
    let places = list(doc, "places.places");
    let versions = list(doc, "places.versions");
    let id = text(place, "id");
    let version = text(place, "version.sha");
    // the start place's id, from the list: a choice of it is the start place's, written with no id
    let start = if truthy(place, "start") { id } else { "" };
    let live = version.is_empty();
    let name = match text(doc, "checkout.name") {
        n if !n.is_empty() && live && truthy(place, "start") => n.to_string(),
        _ => text(place, "label").split(" \u{b7} ").next().unwrap_or("").to_string(),
    };
    // the words, while the place is shown as it is now: dim where every record file is committed, in ink otherwise
    let words = match text(doc, "checkout.said") {
        said if !said.is_empty() && live => format!(" <span class=\"gs\" data-state=\"{}\">\u{b7} {}</span>", esc(text(doc, "checkout.state")), esc(said)),
        _ => String::new(),
    };
    let items: String = places
        .iter()
        .map(|p| {
            let pid = text(p, "id");
            let label = unbroken(text(p, "label"));
            let to = if pid == start { "" } else { pid };
            let tip = if text(p, "path").is_empty() { String::new() } else { format!(" title=\"{}\"", esc(text(p, "path"))) };
            let link = |more: &str| format!("<a href=\"{}\" data-place=\"{}\" data-at=\"\"{tip}{more}>{label}</a>", esc(&href(to, "")), esc(to));
            if pid == id {
                // the place shown, its lines after its name, which a screen reader reads with it; its whole path as
                // text where its label does not say it in short (a single checkout)
                let single = !text(p, "label").contains(" \u{b7} ");
                let path = if single { format!("<p class=\"fl pth\" id=\"where-path\">{}</p>", esc(text(place, "path"))) } else { String::new() };
                let lines = if live { fold_lines(doc) } else { String::new() };
                // a screen reader reads the path and what rb found with the link, the rest in reading order (a 1.15
                // document's fold has no found line: the whole block, as before)
                let described = match (single, list(doc, "checkout.evidence").is_empty()) {
                    (_, true) => "where-here",
                    (true, false) => "where-path where-found",
                    (false, false) => "where-found",
                };
                let current = if live { format!(" aria-current=\"true\" aria-describedby=\"{described}\"") } else { String::new() };
                let current = current.as_str();
                format!("<li class=\"here\">{}<div class=\"fls\" id=\"where-here\">{path}{lines}{}</div></li>", link(current), yours(p))
            } else if truthy(p, "can_show") {
                let said = text(p, "said");
                let said = if said.is_empty() { String::new() } else { format!("<p class=\"fl\">{}</p>", esc(said)) };
                format!("<li>{}<div class=\"fls\">{said}{}</div></li>", link(""), yours(p))
            } else {
                format!("<li><span class=\"off\">{label}</span> <span class=\"why\">{}</span>{}</li>", esc(text(p, "why_not")), yours(p))
            }
        })
        .collect();
    let mut h = format!(
        "<details class=\"pick\" data-pick=\"where\" data-fold=\"where\"><summary title=\"{}\"><span class=\"dim\">Where</span> <span class=\"lab\">{}</span>{words}</summary><ul class=\"picks\">{items}</ul></details>",
        // the tooltip says the whole path, which the list says too
        esc(text(place, "path")),
        esc(&name)
    );
    if !versions.is_empty() {
        let to = if id == start { "" } else { id };
        let now = format!(
            "<li><a href=\"{}\" data-place=\"{}\" data-at=\"\"{}>now</a></li>",
            esc(&href(to, "")),
            esc(to),
            if live { " aria-current=\"true\"" } else { "" }
        );
        let past: String = versions
            .iter()
            .map(|v| {
                let sha = text(v, "sha");
                let current = if sha == version { " aria-current=\"true\"" } else { "" };
                format!(
                    "<li><a href=\"{}\" data-place=\"{}\" data-at=\"{}\"{current}><span class=\"mono\">{}</span> \u{b7} {}</a></li>",
                    esc(&href(to, sha)),
                    esc(to),
                    esc(sha),
                    esc(&sha.chars().take(12).collect::<String>()),
                    esc(text(v, "at"))
                )
            })
            .collect();
        // a past version is named on its button by its day; its commit is in the list
        let now_is = if live { "now".to_string() } else { esc(&version_day(text(place, "version.at"), versions)) };
        h.push_str(&format!(
            "<details class=\"pick\" data-pick=\"version\" data-fold=\"version\"><summary><span class=\"dim\">Version</span> <span class=\"lab\">{now_is}</span></summary><ul class=\"picks\">{now}{past}</ul></details>"
        ));
    }
    // the live module swaps its children in with the rest of the page, so it is there while it holds nothing; the
    // script says in it what a refused choice said
    format!("<span class=\"where\" id=\"where\" data-swap>{h}<span class=\"failed\" role=\"status\" aria-live=\"polite\"></span></span>")
}

/// Where you are, in words, away from the live start place, with the way back; at the live start place the bar is
/// there and holds nothing, so the live module can swap one in.
pub fn scope_bar(doc: &Value) -> String {
    if at(doc, "places.place").is_none() {
        return String::new();
    }
    let inner = match at(doc, "places.scope_bar") {
        None => String::new(),
        Some(bar) => {
            let sha = text(doc, "places.place.version.sha");
            let at_attr = if sha.is_empty() { String::new() } else { format!(" data-at=\"{}\"", esc(sha)) };
            let back = text(bar, "href");
            format!(
                "<p class=\"bar\"{at_attr} data-title=\"{}\"><span class=\"said\" title=\"{}\">{}</span> <a class=\"back\" href=\"{}\" data-place=\"{}\" data-at=\"\">{}</a></p>",
                esc(&title_away(doc)),
                esc(text(doc, "places.place.path")),
                esc(&folder_line(&bar_line(doc, text(bar, "line")), text(doc, "places.place.label"))),
                esc(if back.is_empty() { "#overview" } else { back }),
                esc(&place_of(back)),
                esc(text(bar, "back"))
            )
        }
    };
    format!("<div class=\"scope\" id=\"scope\" role=\"region\" aria-label=\"What rb open shows\" data-swap>{inner}</div>")
}

/// The scope bar's line: at a past version, the place and the day it was committed ("main · ~/int8-resnet50 as of
/// 2026-10-13 · read only"), its commit in the Version list alone; elsewhere as rb gives it.
fn bar_line(doc: &Value, line: &str) -> String {
    match at(doc, "places.place.version") {
        Some(v) => format!("{} as of {} \u{b7} read only", text(doc, "places.place.label"), version_day(text(v, "at"), list(doc, "places.versions"))),
        None => line.to_string(),
    }
}

/// The scope bar's line with the place's path said by its folder alone ("exp-b · .worktrees/b" reads "exp-b · b"); the
/// whole path is the line's tooltip.
fn folder_line(line: &str, label: &str) -> String {
    match label.split_once(" \u{b7} ") {
        Some((name, path)) if line.contains(label) => {
            let folder = path.trim_end_matches('/').rsplit('/').next().unwrap_or(path);
            line.replacen(label, &format!("{name} \u{b7} {folder}"), 1)
        }
        _ => line.to_string(),
    }
}

/// The place an address names (`&place=<id>`), as it stands there; none for the place rb open started on.
fn place_of(href: &str) -> String {
    href.split('&').find_map(|p| p.strip_prefix("place=")).unwrap_or("").to_string()
}

/// The Overview's Elsewhere, at the live start place only: each other place with what rb says of it (its own counts,
/// never added up), what it holds not merged here, and what waits there, each with the one command that confirms it
/// there; a place rb open can show is a link to it, one it cannot says why.
pub fn elsewhere(doc: &Value) -> String {
    let rows = list(doc, "places.elsewhere");
    if rows.is_empty() || !truthy(doc, "places.place.start") || at(doc, "places.place.version").is_some() {
        return String::new();
    }
    let items: String = rows
        .iter()
        .map(|x| {
            let label = esc(text(x, "label"));
            let name = if truthy(x, "can_show") {
                format!("<a href=\"{}\" data-place=\"{}\" data-at=\"\" title=\"{}\">{label}</a>", esc(&href(text(x, "place"), "")), esc(text(x, "place")), esc(text(x, "path")))
            } else {
                format!("<span title=\"{}\">{label}</span> <span class=\"dim\">{}</span>", esc(text(x, "path")), esc(text(x, "why_not")))
            };
            // what rb says of the place, one idea a line: past three items, what the place is, then the rest quietly
            let said: Vec<&str> = text(x, "said").split(" \u{b7} ").collect();
            let said = if said.len() > 3 {
                format!("<p class=\"said\">{}</p><p class=\"said\">{}</p>", esc(said[0]), esc(&said[1..].join(" \u{b7} ")))
            } else {
                format!("<p class=\"said\">{}</p>", esc(&said.join(" \u{b7} ")))
            };
            let mut li = format!("<li><p class=\"place-name\">{name}</p>{said}");
            let adds = text(x, "adds");
            if !adds.is_empty() {
                li.push_str(&format!("<p class=\"said\">not merged here: {}</p>", esc(adds)));
            }
            let pending = list(x, "pending");
            if !pending.is_empty() {
                let rows: String = pending
                    .iter()
                    .map(|r| {
                        let lines: String = list(r, "lines").iter().filter_map(Value::as_str).map(|l| format!("<p class=\"wp\">{}</p>", esc(l))).collect();
                        // a design's command lists it to confirm (no screen shows every part it would confirm); a
                        // call's confirms it
                        let lead = if text(r, "kind") == "design" { "List it to confirm, in your terminal:" } else { "Confirm it in your own terminal:" };
                        format!("<li><p class=\"wt\">{}</p>{lines}<p class=\"cf\">{lead}</p>{}</li>", esc(text(r, "text")), cmdbox(text(r, "confirm")))
                    })
                    .collect();
                li.push_str(&format!(
                    "<details class=\"fold\" data-fold=\"elsewhere-{}\"><summary>Waiting there for your confirmation ({})</summary><ul class=\"wl\" data-waiting>{rows}</ul></details>",
                    esc(text(x, "place")),
                    pending.len()
                ));
            }
            let n = at(x, "waiting.n").and_then(Value::as_i64).unwrap_or(0);
            let calls = text(x, "waiting.list");
            if n > 0 && !calls.is_empty() {
                let said = if n == 1 { "1 call waits on you there; this lists it with its command:".to_string() } else { format!("{n} calls wait on you there; this lists them with their commands:") };
                li.push_str(&format!("<p class=\"w\">{said}</p>{}", cmdbox(calls)));
            }
            li.push_str("</li>");
            li
        })
        .collect();
    // folded on the Overview (OVERVIEW-V2: what is not an answer, a warning or a decision here sits one click down); its
    // head counts the places, never what they hold, which is not added up across places
    let n = rows.len();
    let places = if n == 1 { "1 other worktree".to_string() } else { format!("{n} other worktrees") };
    format!(
        "<section class=\"elsewhere\" id=\"elsewhere\" aria-labelledby=\"h-elsewhere\"><details class=\"fold\" data-fold=\"elsewhere\"><summary><h2 id=\"h-elsewhere\">Elsewhere</h2> <span class=\"dim\">\u{b7} {places}</span></summary>\
         <p class=\"sm dim\">The other worktrees of this repository, each with what rb found there.</p><ul class=\"place-list\">{items}</ul></details></section>"
    )
}

/// What a past version withholds, with rb's reason (the scope bar above it holds the way back): no command to copy, since a call copied there would
/// be recorded on today's readings. Held empty while a place is shown as it is now, so the live module can swap it in.
pub fn past_version(doc: &Value) -> String {
    if at(doc, "places.place").is_none() {
        return String::new();
    }
    let inner = if at(doc, "places.place.version").is_none() {
        String::new()
    } else {
        // the way back is the scope bar's, said once
        format!("<p class=\"withheld\">{}</p>", esc(text(doc, "places.scope_bar.withheld")))
    };
    format!("<div class=\"past\" id=\"withheld\" data-swap>{inner}</div>")
}

/// What the page's title says before the view's away from the live start place (the scope bar carries it, and the
/// script puts it first in the title): the place in short, and the
/// version shown by the UTC day it was committed, as the scope bar says it ("exp-b · .worktrees/b · ", "main ·
/// ~/int8-resnet50 as of 2026-10-13 · "), so tabs and history tell them apart; nothing at the live start place.
pub fn title_away(doc: &Value) -> String {
    if at(doc, "places.scope_bar").is_none() {
        return String::new();
    }
    let as_of = match at(doc, "places.place.version") {
        Some(v) => format!(" as of {}", version_day(text(v, "at"), list(doc, "places.versions"))),
        None => String::new(),
    };
    format!("{}{as_of} \u{b7} ", text(doc, "places.place.label"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(name: &str) -> Value {
        let path = format!("{}/../tests/fixtures/shell/int8-demo.{name}.view.json", env!("CARGO_MANIFEST_DIR"));
        crate::pyjson::loads(&std::fs::read_to_string(&path).unwrap()).unwrap()
    }

    #[test]
    fn a_page_without_places_draws_none_of_rb_open_s_parts() {
        let owner = doc("owner");
        assert_eq!((top_bar(&owner), scope_bar(&owner), elsewhere(&owner), past_version(&owner)), (String::new(), String::new(), String::new(), String::new()));
    }

    /// A single checkout's bar: Where is drawn in every state, its button naming the place as its checkout names it
    /// (its branch, "no branch", or its folder where git cannot say) and how the checkout holds the record, in rb's
    /// words, never a path or a commit; the path is the button's tooltip and the list's text, the commit one step down.
    fn alone(checkout: &str) -> Value {
        crate::pyjson::loads(&format!(
            r#"{{"checkout": {checkout}, "places": {{"place": {{"id": "main", "label": "main", "path": "/srv/ana/int8-resnet50", "start": true, "version": null, "line": "main"}},
            "places": [{{"id": "main", "label": "main", "can_show": true, "why_not": "", "said": "this record", "started_here": null}}], "versions": [], "scope_bar": null, "elsewhere": []}}}}"#
        ))
        .unwrap()
    }

    fn checkout_of(c: &crate::checkout::Checkout) -> String {
        crate::pyjson::dumps(&c.to_value())
    }

    #[test]
    fn a_single_checkout_s_where_button_says_its_words_and_no_path_or_commit() {
        use crate::checkout::{Checkout, State};
        let sha = "9fa4f89a1b2c3d4e5f60718293a4b5c6d7e8f901";
        let mut c = Checkout {
            state: State::Committed, commit: Some(sha.into()), branch: Some("main".into()), on_branch: true, folder: "int8-resnet50".into(),
            record_files_not_committed: 0, record_files_ignored: 0, ignored_by: None, more_rules: 0, record_held: None, changes_outside_record: false,
            why: None, timed_out: false, root: Default::default(), top: None, rb_dir: ".rb".into(), not_committed_names: Vec::new(),
            ignored_names: Vec::new(), listed: Vec::new(),
        };
        // a 1.15 document (no evidence): the fold, as before
        let face = |t: &str| t[..t.find("<ul class=\"picks\">").unwrap()].to_string();
        let t = top_bar(&alone(&checkout_of(&c)));
        assert!(t.contains("<details class=\"pick\" data-pick=\"where\" data-fold=\"where\"><summary title=\"/srv/ana/int8-resnet50\"><span class=\"dim\">Where</span> <span class=\"lab\">main</span> <span class=\"gs\" data-state=\"committed\">· all record files committed</span></summary>"), "{t}");
        // the list: the place shown, its path as text, then its commit with Copy, which copies all 40 hex
        assert!(t.contains("aria-current=\"true\" aria-describedby=\"where-here\"><span class=\"nw\">main</span></a><div class=\"fls\" id=\"where-here\"><p class=\"fl pth\" id=\"where-path\">/srv/ana/int8-resnet50</p><p class=\"fl\">at commit 9fa4f89a1b2c</p>"), "{t}");
        assert!(t.contains(&format!("<span class=\"sha\" id=\"where-sha\" hidden>{sha}</span><button type=\"button\" class=\"cp\" data-act=\"copy\" data-from=\"where-sha\"")), "{t}");
        // no place line, no Version picker with no committed version
        assert!(!t.contains("class=\"pl\"") && !t.contains("data-pick=\"version\""), "{t}");
        // the other states: the words as rb made them, the name its checkout's
        c.state = State::NotCommitted;
        c.record_files_not_committed = 2;
        assert!(face(&top_bar(&alone(&checkout_of(&c)))).contains("<span class=\"lab\">main</span> <span class=\"gs\" data-state=\"not_committed\">· 2 record files not committed</span>"));
        c.branch = None;
        c.on_branch = false;
        assert!(face(&top_bar(&alone(&checkout_of(&c)))).contains("<span class=\"lab\">no branch</span> <span class=\"gs\" data-state=\"not_committed\">"));
        c.state = State::Ignored;
        c.record_files_ignored = 12;
        c.ignored_by = Some(".gitignore:4:.rb/".into());
        let t = top_bar(&alone(&checkout_of(&c)));
        assert!(face(&t).contains("· 12 record files ignored by git</span>"), "{t}");
        // the rule git ignores them by, in monospace
        assert!(t.contains("<p class=\"fl\">git ignores 12 record files (<code>.gitignore:4:.rb/</code>), so a commit leaves them out</p>"), "{t}");
        let gone = Checkout { why: Some("fatal: detected dubious ownership in repository at '/srv/int8'".into()), commit: None, ..c.clone() };
        let gone = Checkout { state: State::Unreadable, ..gone };
        let t = top_bar(&alone(&checkout_of(&gone)));
        assert!(face(&t).contains("<span class=\"lab\">int8-resnet50</span> <span class=\"gs\" data-state=\"unreadable\">· rb could not read git</span>"), "{t}");
        assert!(t.contains("<p class=\"fl\"><code>fatal: detected dubious ownership in repository at &#39;/srv/int8&#39;</code></p>"), "{t}");
        for (state, said) in [(State::NotInGit, "not in git"), (State::GitNotFound, "git not found")] {
            let t = top_bar(&alone(&checkout_of(&Checkout { state, ..gone.clone() })));
            assert!(face(&t).contains(&format!("<span class=\"lab\">int8-resnet50</span> <span class=\"gs\" data-state=\"{}\">· {said}</span>", state.key())), "{t}");
        }
        // the face never says a path or a commit, in any state
        for t in [top_bar(&alone(&checkout_of(&c))), top_bar(&alone(&checkout_of(&gone)))] {
            let shown: String = face(&t).split('"').enumerate().filter(|(i, _)| i % 2 == 0).map(|(_, x)| x).collect();
            assert!(!shown.contains("/srv/") && !shown.contains("9fa4f89a1b2c"), "{t}");
        }
    }

    #[test]
    fn the_start_place_says_its_words_and_lists_the_places_and_versions() {
        let live = doc("live");
        let top = top_bar(&live);
        assert!(top.starts_with("<span class=\"where\" id=\"where\" data-swap>"), "{top}");
        // the button names the place by its checkout's name, then its words; the whole path is its tooltip
        assert!(top.contains("<summary title=\"/home/ana/int8-resnet50\"><span class=\"dim\">Where</span> <span class=\"lab\">main</span> <span class=\"gs\" data-state=\"not_committed\">· 2 record files not committed</span></summary>"), "{top}");
        assert!(!top.contains("class=\"pl\""), "the place line is retired: {top}");
        assert!(top.contains("<summary><span class=\"dim\">Version</span> <span class=\"lab\">now</span></summary>"), "{top}");
        // the list: the place shown first, the evidence one step down (PARITY 4), the commit with Copy, the command in a
        // box with its own Copy, what record files are
        assert!(top.contains("<li class=\"here\"><a href=\"#overview\" data-place=\"\" data-at=\"\" aria-current=\"true\" aria-describedby=\"where-found\"><span class=\"nw\">main</span> · <span class=\"nw\">~/</span><wbr><span class=\"nw\">int8-resnet50</span></a><div class=\"fls\" id=\"where-here\"><p class=\"fl\" id=\"where-found\">rb checked the record with git <time datetime=\"2026-10-19T09:41:07.000+00:00\" title=\"2026-10-19 09:41 UTC\" data-checked>just now</time>: 2 of its 41 record files are new, changed or deleted since commit 3f2a9c1e8d0b.</p>"), "{top}");
        assert!(top.contains("<p class=\"fl\">Not committed: <code>.rb/experiments/int8/runs/run4k2xa9.yaml</code> (new) and <code>.rb/log.jsonl</code> (changed)</p><p class=\"fl\">This checkout has changes outside the record not committed.</p>"), "{top}");
        assert!(top.contains("<div class=\"cmdbox\"><pre class=\"cmd\">git -C /home/ana/int8-resnet50 status --short --untracked-files=all -- .rb</pre><button type=\"button\" class=\"cp\" data-act=\"copy\" aria-label=\"Copy the command\">Copy</button></div>"), "{top}");
        assert!(top.contains("<p class=\"fl dim\">Record files are the files rb keeps"), "{top}");
        assert!(top.contains("hidden>3f2a9c1e8d0b4c7a9e2f61d5b0c8a7e4f3d2c1b0</span>"), "{top}");
        // each other place with rb's line for it
        assert!(top.contains("<li><a href=\"#overview&amp;place=exp-b\" data-place=\"exp-b\" data-at=\"\"><span class=\"nw\">exp-b</span> · <span class=\"nw\">.worktrees/</span><wbr><span class=\"nw\">b</span></a><div class=\"fls\"><p class=\"fl\">its own record · 1 record file not committed · last write 2026-10-18 16:02 UTC</p></div></li>"), "{top}");
        assert!(top.contains("<a href=\"#overview\" data-place=\"\" data-at=\"\" aria-current=\"true\">now</a>"), "{top}");
        assert!(top.contains("<a href=\"#overview&amp;at=3f2a9c1\" data-place=\"\" data-at=\"3f2a9c1\"><span class=\"mono\">3f2a9c1</span> · 2026-10-13 15:02 UTC</a>"), "{top}");
        // each picker keeps its fold across a live redraw
        assert!(top.contains("data-pick=\"where\" data-fold=\"where\"") && top.contains("data-pick=\"version\" data-fold=\"version\""), "{top}");
        // a version is its commit and its time: never its subject
        assert!(!top.contains("the record, as of e2"), "{top}");
        // held empty at the live start place, so the live module can swap one in
        assert_eq!(scope_bar(&live), "<div class=\"scope\" id=\"scope\" role=\"region\" aria-label=\"What rb open shows\" data-swap></div>");
        assert_eq!(past_version(&live), "<div class=\"past\" id=\"withheld\" data-swap></div>");
        assert_eq!(title_away(&live), "");
    }

    /// The worktree rb open was started in, with no record of its own, says its own commit as yours in the list.
    #[test]
    fn the_worktree_rb_open_was_started_in_says_its_commit_as_yours() {
        let text = std::fs::read_to_string(format!("{}/../tests/fixtures/shell/int8-demo.live.view.json", env!("CARGO_MANIFEST_DIR"))).unwrap();
        let mut live = crate::pyjson::loads(&text).unwrap();
        let mine = crate::obj! {"commit" => "5d1e0a7c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e", "changes_outside_record" => true};
        if let Some(Value::List(ps)) = live.as_dict_mut().and_then(|d| d.get_mut("places")).and_then(|p| p.as_dict_mut()).and_then(|p| p.get_mut("places")) {
            ps[1].as_dict_mut().unwrap().insert("started_here".into(), mine);
        }
        let top = top_bar(&live);
        assert!(top.contains("<p class=\"fl\">your checkout · at commit 5d1e0a7c4b3a, with changes outside the record not committed</p></div></li>"), "{top}");
    }

    #[test]
    fn another_place_says_where_it_is_with_the_way_back() {
        let away = doc("away");
        let bar = scope_bar(&away);
        // the path said by its folder alone, the whole path its tooltip
        assert!(bar.contains(" title=\"/home/ana/int8-resnet50/.worktrees/b\">Viewing exp-b · b</span>") && bar.contains(">Back to main</a>"), "{bar}");
        assert!(bar.contains(" data-title=\"exp-b · .worktrees/b · \""), "{bar}");
        assert!(bar.contains("data-place=\"\" data-at=\"\"") && !bar.contains(" data-at=\"3"), "{bar}");
        let top = top_bar(&away);
        // its button names it by its branch, with its own words; its commit one step down
        assert!(top.contains("<span class=\"lab\">exp-b</span> <span class=\"gs\" data-state=\"not_committed\">· 1 record file not committed</span></summary>"), "{top}");
        assert!(top.contains("aria-current=\"true\" aria-describedby=\"where-found\"") && top.contains("since commit 5d1e0a7c4b3a.</p>"), "{top}");
        assert!(top.contains("git -C /home/ana/int8-resnet50/.worktrees/b status"), "its own folder: {top}");
        assert!(top.contains("<a href=\"#overview&amp;place=exp-b\" data-place=\"exp-b\" data-at=\"\" aria-current=\"true\""), "{top}");
        // the start place, chosen from away, is chosen by its own id: the list says which place started
        assert!(top.contains("data-place=\"main\""), "{top}");
        assert_eq!(elsewhere(&away), "", "Elsewhere is the live start place's");
        assert_eq!(past_version(&away), "<div class=\"past\" id=\"withheld\" data-swap></div>");
        assert_eq!(title_away(&away), "exp-b · .worktrees/b · ");
    }

    #[test]
    fn a_past_version_is_read_only_and_withholds_every_command() {
        let past = doc("past");
        let bar = scope_bar(&past);
        assert!(bar.contains(" data-at=\"3f2a9c1\" data-title=\"main · ~/int8-resnet50 as of 2026-10-13 · \"") && bar.contains(">Back to now</a>"), "{bar}");
        // the place and the day; the commit is in the Version list
        assert!(bar.contains(">main · int8-resnet50 as of 2026-10-13 · read only</span>"), "{bar}");
        let w = past_version(&past);
        assert_eq!(
            w,
            "<div class=\"past\" id=\"withheld\" data-swap><p class=\"withheld\">A past version offers no command: a call copied here would be recorded on \
             today&#39;s readings.</p></div>"
        );
        assert_eq!(title_away(&past), "main · ~/int8-resnet50 as of 2026-10-13 · ");
        let top = top_bar(&past);
        // no words on Where, and no commit on the face: the Version button says the day
        assert!(top.contains("<span class=\"lab\">main</span></summary>") && !top.contains("class=\"gs\""), "{top}");
        assert!(top.contains("<summary><span class=\"dim\">Version</span> <span class=\"lab\">2026-10-13</span></summary>"), "{top}");
        assert!(top.contains("data-at=\"3f2a9c1\" aria-current=\"true\""), "{top}");
        assert_eq!(elsewhere(&past), "");
        // two versions listed on one day: the button and the scope bar add the minute
        let mut two = doc("past");
        let versions = crate::pyjson::loads(r#"[{"sha": "3f2a9c1", "at": "2026-10-13 15:02 UTC", "subject": ""}, {"sha": "1b2c3d4", "at": "2026-10-13 09:10 UTC", "subject": ""}]"#).unwrap();
        two.as_dict_mut().unwrap().get_mut("places").unwrap().as_dict_mut().unwrap().insert("versions".into(), versions);
        assert!(top_bar(&two).contains("<span class=\"lab\">2026-10-13 15:02 UTC</span>"));
        assert!(scope_bar(&two).contains(">main · int8-resnet50 as of 2026-10-13 15:02 UTC · read only</span>"));
        assert_eq!(title_away(&two), "main · ~/int8-resnet50 as of 2026-10-13 15:02 UTC · ");
    }

    #[test]
    fn elsewhere_lists_each_other_place_and_what_waits_there_with_its_command() {
        let e = elsewhere(&doc("live"));
        assert!(e.starts_with("<section class=\"elsewhere\" id=\"elsewhere\""), "{e}");
        // folded on the Overview, its head counting the places alone
        assert!(e.contains("<details class=\"fold\" data-fold=\"elsewhere\"><summary><h2 id=\"h-elsewhere\">Elsewhere</h2> <span class=\"dim\">\u{b7} 1 other worktree</span></summary>"), "{e}");
        assert!(e.contains("<a href=\"#overview&amp;place=exp-b\" data-place=\"exp-b\" data-at=\"\" title=\"/home/ana/int8-resnet50/.worktrees/b\">exp-b · .worktrees/b</a>"), "{e}");
        assert!(e.contains("<p class=\"said\">1 run · last write 2026-10-18 16:02 UTC</p><p class=\"said\">not merged here: 1 run not merged here</p>"), "{e}");
        assert!(e.contains("<ul class=\"wl\" data-waiting><li><p class=\"wt\">accept c1, proposed, not made · note: seed 5 agrees · not confirmed</p>"), "{e}");
        assert!(e.contains("<pre class=\"cmd\">cd /home/ana/int8-resnet50/.worktrees/b &amp;&amp; rb confirm dec9</pre>"), "{e}");
        assert!(e.contains("1 call waits on you there; this lists it with its command:</p><div class=\"cmdbox\" data-copybtn><pre class=\"cmd\">cd /home/ana/int8-resnet50/.worktrees/b &amp;&amp; rb status</pre>"), "{e}");
    }

    #[test]
    fn elsewhere_says_a_place_in_lines_of_at_most_three_items() {
        // Design pass 1 (P2): four items of what rb says of a place are two lines, what it is, then the rest
        let d = crate::pyjson::loads(
            r#"{"places": {"place": {"start": true}, "elsewhere": [{"place": "exp-b", "label": "exp-b · .worktrees/b", "path": "/p/.worktrees/b", "can_show": true, "said": "its own record · 2 record files not committed · 1 run recorded from it · last write 2026-10-18 16:02 UTC", "adds": "", "waiting": {"n": 0, "list": ""}}]}}"#,
        )
        .unwrap();
        let e = elsewhere(&d);
        assert!(e.contains("<p class=\"said\">its own record</p><p class=\"said\">2 record files not committed · 1 run recorded from it · last write 2026-10-18 16:02 UTC</p>"), "{e}");
    }

    #[test]
    fn elsewhere_says_a_designs_command_lists_it_and_a_calls_confirms_it() {
        let d = crate::pyjson::loads(
            r#"{"places": {"place": {"start": true}, "elsewhere": [{"place": "exp-c", "label": "exp-c · .worktrees/c", "path": "/p/.worktrees/c", "can_show": true, "said": "", "adds": "", "waiting": {"n": 0, "list": ""},
            "pending": [{"kind": "design", "text": "the design of lat", "confirm": "cd /p/.worktrees/c && rb confirm lat", "lines": []},
                        {"kind": "call", "text": "accept c1", "confirm": "cd /p/.worktrees/c && rb confirm dec9", "lines": []}]}]}}"#,
        )
        .unwrap();
        let e = elsewhere(&d);
        assert!(e.contains("<p class=\"wt\">the design of lat</p><p class=\"cf\">List it to confirm, in your terminal:</p><div class=\"cmdbox\" data-copybtn><pre class=\"cmd\">cd /p/.worktrees/c &amp;&amp; rb confirm lat</pre>"), "{e}");
        assert!(e.contains("<p class=\"wt\">accept c1</p><p class=\"cf\">Confirm it in your own terminal:</p><div class=\"cmdbox\" data-copybtn><pre class=\"cmd\">cd /p/.worktrees/c &amp;&amp; rb confirm dec9</pre>"), "{e}");
    }

    #[test]
    fn rb_open_s_parts_escape_what_the_record_says() {
        let path = format!("{}/../tests/fixtures/shell/int8-demo.live.view.json", env!("CARGO_MANIFEST_DIR"));
        let text = std::fs::read_to_string(&path).unwrap();
        let text = text.replace("\"label\": \"exp-b · .worktrees/b\"", "\"label\": \"<b>exp-b</b> \\\"q\\\"\"").replace("\"id\": \"exp-b\"", "\"id\": \"x&y\"");
        let top = top_bar(&crate::pyjson::loads(&text).unwrap());
        assert!(top.contains("&lt;b&gt;exp-b&lt;/</span><wbr><span class=\"nw\">b&gt; &quot;q&quot;</span>") && !top.contains("<b>exp-b"), "{top}");
        assert!(top.contains("data-place=\"x&amp;y\"") && top.contains("place=x%26y"), "{top}");
    }
}
