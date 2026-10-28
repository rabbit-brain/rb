//! What rb open adds to the frame (A fills it): the top bar's place line and its Where and Version pickers, the scope
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

/// The place line and the Where and Version pickers: the place's line while it is live; with another place or a past
/// version, each picker a fold that lists its choices as links, the one shown marked current, one rb open cannot show
/// said with why. The fold opens without the script too, and stays within a 390 px screen.
pub fn top_bar(doc: &Value) -> String {
    let Some(place) = at(doc, "places.place") else { return String::new() };
    let places = list(doc, "places.places");
    let versions = list(doc, "places.versions");
    let id = text(place, "id");
    let version = text(place, "version.sha");
    // the start place's id, from the list: a choice of it is the start place's, written with no id
    let start = if truthy(place, "start") { id } else { "" };
    let mut h = String::new();
    // a past version is said by the scope bar, in its own words; where the Where picker names the place, the line says
    // only the rest of what it says, so the bar names the place once
    let pickers = places.len() > 1 || !versions.is_empty();
    let full = if version.is_empty() { text(place, "line") } else { "" };
    let label = text(place, "label");
    let path = text(place, "path");
    // the bar never says a path, at any width: every part of the line that is the place's path (the label's short form
    // or the whole one) loses it, wherever it sits; the path is the Where list's and the tooltip's
    let paths: Vec<&str> = [label.split_once(" \u{b7} ").map_or("", |(_, p)| p), path].into_iter().filter(|p| !p.is_empty()).collect();
    let mut parts: Vec<String> = full
        .split(" \u{b7} ")
        .filter_map(|part| {
            let rest = paths.iter().find(|p| part.starts_with(*p)).map_or(part, |p| part[p.len()..].trim_start_matches(':').trim_start());
            (!rest.is_empty()).then(|| rest.to_string())
        })
        .collect();
    // a place with no branch to name it (a record outside git says "detached at" and nothing) is named by its folder
    let folder = path.trim_end_matches('/').rsplit('/').next().unwrap_or("");
    if let Some(first) = parts.first_mut() {
        if first.trim() == "detached at" && !folder.is_empty() {
            *first = folder.to_string();
        }
    } else if !full.is_empty() && !folder.is_empty() {
        parts.push(folder.to_string());
    }
    // with a Where picker its button names the place; the line says only the rest
    let name = label.split(" \u{b7} ").next().unwrap_or("");
    if pickers && !name.is_empty() && parts.first().is_some_and(|f| f == name) {
        parts.remove(0);
    }
    let joined = parts.join(" \u{b7} ");
    let line = joined.as_str();
    if !line.is_empty() {
        h.push_str(&format!("<span class=\"pl\" title=\"{}\">{}</span>", esc(text(place, "path")), esc(line)));
    }
    if pickers {
        let mut shown = String::new();
        let items: String = places
            .iter()
            .map(|p| {
                let pid = text(p, "id");
                let label = unbroken(text(p, "label"));
                let to = if pid == start { "" } else { pid };
                let tip = if text(p, "path").is_empty() { String::new() } else { format!(" title=\"{}\"", esc(text(p, "path"))) };
                let link = |current: &str| format!("<li><a href=\"{}\" data-place=\"{}\" data-at=\"\"{tip}{current}>{label}</a></li>", esc(&href(to, "")), esc(to));
                if pid == id {
                    // the button names the place by its branch or worktree alone; its path is in its tooltip and the list
                    shown = esc(text(p, "label").split(" \u{b7} ").next().unwrap_or(""));
                    link(if version.is_empty() { " aria-current=\"true\"" } else { "" })
                } else if truthy(p, "can_show") {
                    link("")
                } else {
                    format!("<li><span class=\"off\">{label}</span> <span class=\"why\">{}</span></li>", esc(text(p, "why_not")))
                }
            })
            .collect();
        h.push_str(&format!(
            "<details class=\"pick\" data-pick=\"where\"><summary title=\"{}\"><span class=\"dim\">Where</span> <span class=\"lab\">{shown}</span></summary><ul class=\"picks\">{items}</ul></details>",
            // the tooltip says the whole path and what the place line says, which the bar from 1280 leaves out
            esc(&[text(place, "path"), line].iter().filter(|x| !x.is_empty()).copied().collect::<Vec<_>>().join(" \u{b7} "))
        ));
        if !versions.is_empty() {
            let to = if id == start { "" } else { id };
            let now = format!(
                "<li><a href=\"{}\" data-place=\"{}\" data-at=\"\"{}>now</a></li>",
                esc(&href(to, "")),
                esc(to),
                if version.is_empty() { " aria-current=\"true\"" } else { "" }
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
            let now_is = if version.is_empty() { "now".to_string() } else { format!("<span class=\"mono\">{}</span>", esc(&version.chars().take(12).collect::<String>())) };
            h.push_str(&format!(
                "<details class=\"pick\" data-pick=\"version\"><summary><span class=\"dim\">Version</span> <span class=\"lab\">{now_is}</span></summary><ul class=\"picks\">{now}{past}</ul></details>"
            ));
        }
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
                esc(&folder_line(text(bar, "line"), text(doc, "places.place.label"))),
                esc(if back.is_empty() { "#overview" } else { back }),
                esc(&place_of(back)),
                esc(text(bar, "back"))
            )
        }
    };
    format!("<div class=\"scope\" id=\"scope\" role=\"region\" aria-label=\"What rb open shows\" data-swap>{inner}</div>")
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
/// version shown ("exp-b · .worktrees/b · ", "main · ~/int8-resnet50 as of 3f2a9c1 · "), so tabs and history tell
/// them apart; nothing at the live start place.
pub fn title_away(doc: &Value) -> String {
    if at(doc, "places.scope_bar").is_none() {
        return String::new();
    }
    let sha = text(doc, "places.place.version.sha");
    let as_of = if sha.is_empty() { String::new() } else { format!(" as of {}", sha.chars().take(12).collect::<String>()) };
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

    #[test]
    fn a_single_checkout_s_top_bar_says_no_path_at_any_width() {
        // one place, no committed version: no picker, so the bar's own line names the place, by its branch alone; the
        // whole path is its tooltip (the film rule: no absolute path in the top bar)
        let one = |line: &str, label: &str| {
            crate::pyjson::loads(&format!(
                r#"{{"places": {{"place": {{"id": "main", "label": "{label}", "path": "/srv/ana/int8-resnet50", "start": true, "version": null, "line": "{line}"}},
                "places": [{{"id": "main", "label": "main · /srv/ana/int8-resnet50", "can_show": true, "why_not": ""}}], "versions": [], "scope_bar": null, "elsewhere": []}}}}"#
            ))
            .unwrap()
        };
        for (line, label, said) in [
            ("main · /srv/ana/int8-resnet50", "main · /srv/ana/int8-resnet50", "main"),
            ("main · /srv/ana/int8-resnet50 · 2 not committed", "main · /srv/ana/int8-resnet50", "main · 2 not committed"),
            // the short label M gives the place: drawn as it is
            ("main", "main", "main"),
            ("main · int8-resnet50", "main · int8-resnet50", "main"),
            // a record outside git has no branch: its folder names it
            ("detached at  · /srv/ana/int8-resnet50", "detached at  · /srv/ana/int8-resnet50", "int8-resnet50"),
            // a line that does not start with the label's name still loses the path
            ("main-old · /srv/ana/int8-resnet50", "main · /srv/ana/int8-resnet50", "main-old"),
        ] {
            let t = top_bar(&one(line, label));
            assert!(t.contains(&format!("<span class=\"pl\" title=\"/srv/ana/int8-resnet50\">{said}</span>")), "{t}");
            let shown: String = t.split('"').enumerate().filter(|(i, _)| i % 2 == 0).map(|(_, x)| x).collect();
            assert!(!shown.contains("/srv/") && (said == "int8-resnet50" || !shown.contains("int8-resnet50<")), "{t}");
        }
    }

    #[test]
    fn the_start_place_says_its_line_and_lists_the_places_and_versions() {
        let live = doc("live");
        let top = top_bar(&live);
        assert!(top.starts_with("<span class=\"where\" id=\"where\" data-swap>"), "{top}");
        // the place is named once, by the Where picker; the line says the rest
        assert!(top.contains("<span class=\"pl\" title=\"/home/ana/int8-resnet50\">2 record files not committed</span>"), "{top}");
        // its button names the place by its branch alone, the path in its tooltip
        assert!(top.contains("<summary title=\"/home/ana/int8-resnet50 · 2 record files not committed\"><span class=\"dim\">Where</span> <span class=\"lab\">main</span></summary>"), "{top}");
        assert!(top.contains("<summary><span class=\"dim\">Version</span> <span class=\"lab\">now</span></summary>"), "{top}");
        assert!(top.contains("<a href=\"#overview\" data-place=\"\" data-at=\"\" aria-current=\"true\"><span class=\"nw\">main</span> · <span class=\"nw\">~/</span><wbr><span class=\"nw\">int8-resnet50</span></a>"), "{top}");
        assert!(top.contains("<a href=\"#overview&amp;place=exp-b\" data-place=\"exp-b\" data-at=\"\"><span class=\"nw\">exp-b</span> · <span class=\"nw\">.worktrees/</span><wbr><span class=\"nw\">b</span></a>"), "{top}");
        assert!(top.contains("<a href=\"#overview\" data-place=\"\" data-at=\"\" aria-current=\"true\">now</a>"), "{top}");
        assert!(top.contains("<a href=\"#overview&amp;at=3f2a9c1\" data-place=\"\" data-at=\"3f2a9c1\"><span class=\"mono\">3f2a9c1</span> · 2026-10-13 15:02 UTC</a>"), "{top}");
        // a version is its commit and its time: never its subject
        assert!(!top.contains("the record, as of e2"), "{top}");
        // held empty at the live start place, so the live module can swap one in
        assert_eq!(scope_bar(&live), "<div class=\"scope\" id=\"scope\" role=\"region\" aria-label=\"What rb open shows\" data-swap></div>");
        assert_eq!(past_version(&live), "<div class=\"past\" id=\"withheld\" data-swap></div>");
        assert_eq!(title_away(&live), "");
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
        // its line names it with its path whole, as rb open says it away: the picker says the place, the line the rest
        let text = std::fs::read_to_string(format!("{}/../tests/fixtures/shell/int8-demo.away.view.json", env!("CARGO_MANIFEST_DIR"))).unwrap();
        let line = "exp-b · .worktrees/b · 1 run not merged into main";
        assert!(text.contains(&format!("\"line\": \"{line}\"")), "the away example's place line");
        let whole = text.replacen(&format!("\"line\": \"{line}\""), "\"line\": \"exp-b · /home/ana/int8-resnet50/.worktrees/b · its own record · 1 record file not committed\"", 1);
        let t = top_bar(&crate::pyjson::loads(&whole).unwrap());
        assert!(t.contains(">its own record · 1 record file not committed</span>") && !t.contains(">exp-b · /home"), "{t}");
        assert!(top.contains("<a href=\"#overview&amp;place=exp-b\" data-place=\"exp-b\" data-at=\"\" aria-current=\"true\">"), "{top}");
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
        assert!(bar.contains(" data-at=\"3f2a9c1\" data-title=\"main · ~/int8-resnet50 as of 3f2a9c1 · \"") && bar.contains(">Back to now</a>"), "{bar}");
        assert!(bar.contains("main · int8-resnet50 as of 3f2a9c1 · 2026-10-13 15:02 UTC · read only"), "{bar}");
        let w = past_version(&past);
        assert_eq!(
            w,
            "<div class=\"past\" id=\"withheld\" data-swap><p class=\"withheld\">A past version offers no command: a call copied here would be recorded on \
             today&#39;s readings.</p></div>"
        );
        assert_eq!(title_away(&past), "main · ~/int8-resnet50 as of 3f2a9c1 · ");
        let top = top_bar(&past);
        assert!(!top.contains("class=\"pl\""), "the bar says the past version, not the place line: {top}");
        assert!(top.contains("data-at=\"3f2a9c1\" aria-current=\"true\""), "{top}");
        assert_eq!(elsewhere(&past), "");
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
