//! The Overview's four blocks (UX5 1.2, round 1), drawn from the document's `overview` (overview.rs): the question, So
//! far (the strip of its checks by state, and one chart of each result against what its check accepts), What it rests
//! on (four marks, each opening its evidence) and Decisions (rows with a kind chip, a count on the heading), then the
//! other questions. Shown, not told: each element's words are its `said`, for screen readers, never on the screen.

use super::*;
use crate::overview as ov;

use crate::shell::fields::{self, track};

/// The anchor of a rests-on mark's card.
pub(super) fn rest_anchor(key: &str) -> String {
    format!("rest-{key}")
}

/// The chart's rows: a panel per measure, each row its check's name (with the chip `claim` where it checks a claim),
/// its track, and at its end its state's square at the strip's size, the result with its unit and its range in grey.
/// The check's number and 0 are said on the axis once per panel where its rows share them, else on each row.
fn chart_html(rows: &[&Value], to: &dyn Fn(&str) -> String) -> String {
    let mut out = String::new();
    let mut i = 0;
    while i < rows.len() {
        let head = text(rows[i], "group").to_string();
        let mut j = i;
        while j < rows.len() && text(rows[j], "group") == head {
            j += 1;
        }
        let panel = &rows[i..j];
        let (lo, hi) = ov::domain(panel);
        let edges: Vec<&str> = panel.iter().map(|r| text(r, "edge_text")).collect();
        let shared = edges.iter().all(|e| *e == edges[0]);
        let mut body = String::new();
        for (k, r) in panel.iter().enumerate() {
            let id = text(r, "id");
            let last = k + 1 == panel.len();
            let labels = if shared { (last, last) } else { (true, last) };
            let claim = if r.get("claim").and_then(Value::as_bool).unwrap_or(false) { format!(" <span class=\"chip k kl\">{}</span>", w::CHIP_CLAIM) } else { String::new() };
            let took = if text(r, "taken_out").is_empty() { String::new() } else { format!("<span class=\"took s-undetermined\">{}</span>", esc(ov::TAKEN_ROW)) };
            let short = if r.get("short").and_then(Value::as_bool).unwrap_or(false) { " sh" } else { "" };
            let state = text(r, "state");
            let late = if r.get("late").and_then(Value::as_bool).unwrap_or(false) { " late" } else { "" };
            let range = text(r, "range_text");
            let range = if range.is_empty() { String::new() } else { format!("<span class=\"og\">{}</span>", esc(range)) };
            let result = text(r, "result_text");
            let result = if result.is_empty() { text(r, "est_text") } else { result };
            body += &format!(
                "<a class=\"ovr{short}\" href=\"#{}\"><span class=\"ol\"><span class=\"au\">{}</span>{claim}{took}</span>{}<span class=\"oe\"><b class=\"s-{state}{late}\">{}</b> {}</span>{range}</a>",
                esc(&to(id)),
                esc(text(r, "label")),
                track(r, lo, hi, labels),
                ov::mark_of(state),
                esc(result)
            );
        }
        // the panel's measure opens Rules showing its rules alone (closed form: a label opens what it names)
        let ids: Vec<String> = panel.iter().map(|r| format!("c-{}", text(r, "id"))).collect();
        out += &format!("<div class=\"ovp\"><p class=\"gh\"><a href=\"#criteria\" data-ids=\"{}\">{}</a></p>{body}</div>", esc(&ids.join(" ")), esc(&head));
        i = j;
    }
    out
}

/// A row's chip as each viewer of the export reads it (OWNER-READER), each with what it is on hover and keyboard focus:
/// the owner's word, and a reader's "open".
fn viewer_chip(kind: &str, word: &str, tip: &str) -> String {
    format!(
        "<span class=\"chip k {kind}\" tabindex=\"0\" title=\"{tip}\" data-vw=\"none owner\">{word}</span><span class=\"chip k {kind}\" tabindex=\"0\" title=\"{}\" data-vw=\"reader\">{}</span>",
        w::TIP_OPEN,
        w::CHIP_OPEN
    )
}

impl<'a> Page<'a> {
    /// Where the Overview opens a check, an experiment or a decision: its field card (fields.rs), where the page holds
    /// one or rb open serves it, else where it opened before.
    fn card_to(&self, subject: &str) -> String {
        if fields::has(subject) {
            return fields::to(subject);
        }
        self.story_to(subject)
    }

    /// Where a decision's subject, a mark or a square opens: a check's card, an experiment, a question, the list of what
    /// waits for a confirmation.
    pub(super) fn story_to(&self, subject: &str) -> String {
        if self.finding(subject).is_some() {
            return self.criterion_to(subject);
        }
        for (prefix, key) in [("x-", "experiments"), ("q-", "questions")] {
            if list(self.doc, key).iter().any(|v| text(v, "id") == subject) {
                return format!("{prefix}{subject}");
            }
        }
        subject.to_string()
    }

    /// The strip: a square per check, grouped by state, each group's count and label.
    fn strip_html(&self, s: &Value, big: bool) -> String {
        // a question with no checks yet draws no strip (an empty figure would read as a part with nothing in it)
        if list(s, "groups").is_empty() {
            return String::new();
        }
        let groups: String = list(s, "groups")
            .iter()
            .map(|g| {
                let state = text(g, "state");
                let marks: String = list(g, "checks")
                    .iter()
                    .map(|c| {
                        let id = text(c, "id");
                        let late = if c.get("late").and_then(Value::as_bool).unwrap_or(false) { " late" } else { "" };
                        format!("<a class=\"s-{state}{late}\" href=\"#{}\">{}</a>", esc(&self.card_to(id)), text(g, "mark"))
                    })
                    .collect();
                // the count opens Rules filtered to its state (owner, Nov 13 2026: counts click)
                format!(
                    "<span class=\"grp\">{marks}<a class=\"ct\" href=\"#criteria\" data-outcome=\"{}\"><b>{}</b> {}</a></span>",
                    esc(state),
                    count(g, "n").unwrap_or(0),
                    esc(text(g, "label"))
                )
            })
            .collect();
        format!("<div class=\"ovstrip{}\" role=\"img\" aria-label=\"{}\">{groups}</div>", if big { " big" } else { "" }, esc(text(s, "said")))
    }

    /// The Overview: the project, the question, So far, What it rests on, Decisions, Other questions; nothing else.
    pub(super) fn story(&self) -> String {
        let d = self.doc;
        let o = d.get("overview").cloned().unwrap_or(Value::Null);
        let title = text(d, "project.title");
        let mut h: Vec<String> = Vec::new();
        let q = o.get("question").filter(|q| q.is_dict());
        match q {
            Some(q) => {
                // the project's title is the top bar's; the question leads
                h.push(format!("<h1 id=\"h-overview\" class=\"ovq\">{}</h1>", a(&format!("q-{}", text(q, "id")), &au_title(text(q, "text")), "sq")));
            }
            // a document from before the view model (contract 1.18 and earlier): its first top-level question, as it reads
            None => match list(d, "questions").iter().find(|q| count(q, "depth").unwrap_or(0) == 0) {
                Some(q) if d.get("overview").is_none() => {
                    h.push(format!("<h1 id=\"h-overview\" class=\"ovq\">{}</h1>", a(&format!("q-{}", text(q, "id")), &au_title(text(q, "text")), "sq")));
                }
                _ => h.push(format!("<h1 id=\"h-overview\">{}</h1>", au_title(title))),
            },
        }
        h.push(self.overview_line());
        // a record with no question yet is shown so (the UX lead's rule, Nov 13 2026): its chip, the outline of the
        // question it would ask, and in your view the line to paste as its Next step
        if q.is_none() && list(d, "questions").is_empty() {
            h.push(format!("<p class=\"roll\">{}</p>{}{}", super::empty_chip(w::NO_QUESTION_YET), super::ghost("card"), self.next_card(crate::skill::PASTE_LINE)));
        }
        if let Some(q) = q {
            let state = text(q, "state");
            // the state opens the question, where rb's line says why it stands so (its words on hover)
            let why = list(d, "home.standing").iter().find(|s| text(s, "question") == text(q, "id")).map(|s| text(s, "line")).unwrap_or("");
            h.push(format!(
                "<h2 class=\"ovh2\">{} <a class=\"pill\" href=\"#q-{}\" title=\"{}\">{}</a></h2>",
                w::SO_FAR,
                esc(text(q, "id")),
                esc(why),
                esc(&capitalised(state))
            ));
            h.push(self.strip_html(o.get("strip").unwrap_or(&Value::Null), true));
            let rows: Vec<&Value> = list(&o, "chart").iter().collect();
            if !rows.is_empty() {
                let shown = &rows[..rows.len().min(ov::CHART_ROWS)];
                let to = |id: &str| self.card_to(id);
                // rows of HTML, drawn at the width they are shown at: a phone's labels are the body's text
                let fig = chart_html(shown, &to);
                let more = if rows.len() > shown.len() { format!("<p class=\"more\">{}</p>", a(&format!("q-{}", text(q, "id")), &format!("+{} \u{203a}", rows.len() - shown.len()), "")) } else { String::new() };
                // no key under it: each row says its result and range in words, its panel head what passes
                h.push(format!("<figure class=\"ovchart\">{fig}{more}</figure>"));
            }
            let rests: String = list(&o, "rests")
                .iter()
                .map(|r| {
                    // each mark opens its card: what it counts, one row each, each opening its own card
                    let to = rest_anchor(text(r, "key"));
                    let ok = match r.get("ok").and_then(Value::as_bool) {
                        Some(true) => " ok",
                        Some(false) => " no",
                        None => "",
                    };
                    format!("<a class=\"mk{ok}\" href=\"#{}\">{}</a>", esc(&to), esc(&ov::rest_said(r, false)))
                })
                .collect();
            h.push(format!("<h2 class=\"ovh2\">{}</h2><div class=\"marks\">{rests}</div>", w::RESTS_ON));
        }
        h.push(self.decisions_block(&o));
        let others: String = list(&o, "others")
            .iter()
            .map(|x| format!("<li>{}{}</li>", a(&format!("q-{}", text(x, "id")), &au_title(text(x, "text")), ""), self.strip_html(x.get("strip").unwrap_or(&Value::Null), false)))
            .collect();
        if !others.is_empty() {
            h.push(format!("<h2 class=\"ovh2\">{}</h2><ul class=\"oq\">{others}</ul>", w::OTHER_QUESTIONS));
        }
        section("overview", title, w::OVERVIEW, "insp-none", &h.concat())
    }

    /// A card for each mark of What it rests on (the rulings of Nov 12, row 10): what the mark counts, one row each, so
    /// the word is shown by what it counts. "planned first": each check, written before or after its results; a cited
    /// number "reproduced": each check of a claim with its state and result; "tries": each experiment with its runs;
    /// "code kept": each experiment, opening its Reproduce.
    pub(super) fn rest_cards(&self) -> String {
        let o = self.doc.get("overview").cloned().unwrap_or(Value::Null);
        if o.get("question").filter(|q| q.is_dict()).is_none() {
            return String::new();
        }
        let checks: Vec<(String, String, bool)> = list(&o, "strip.groups")
            .iter()
            .flat_map(|g| list(g, "checks").iter().map(move |c| (text(c, "id").to_string(), text(g, "state").to_string(), c.get("late").and_then(Value::as_bool).unwrap_or(false))))
            .collect();
        let sq = |state: &str, late: bool| format!("<b class=\"sq s-{state}{}\">{}</b>", if late { " late" } else { "" }, ov::mark_of(state));
        let mut exps: Vec<String> = Vec::new();
        for (id, _, _) in &checks {
            let x = self.doc.get("findings").and_then(|f| f.get(id.as_str())).map_or("", |f| text(f, "experiment"));
            if !x.is_empty() && !exps.iter().any(|e| e == x) {
                exps.push(x.to_string());
            }
        }
        if exps.is_empty() {
            exps = list(self.doc, "experiments").iter().map(|e| text(e, "id").to_string()).collect();
        }
        let mut out = String::new();
        for r in list(&o, "rests") {
            let key = text(r, "key");
            let rows: Vec<String> = match key {
                "planned" => checks
                    .iter()
                    .filter(|(_, state, _)| state != "untested")
                    .map(|(id, state, late)| {
                        // each check placed in time against its results (no glossary, show instead)
                        let f = self.doc.get("findings").and_then(|f| f.get(id.as_str()));
                        let when = |k: &str| f.map_or("", |f| text(f, &format!("when.{k}")));
                        // the whole row opens the rule: its square, its id, and its two days
                        format!("<li><a href=\"#{}\">{} {} {}</a></li>", esc(&self.card_to(id)), sq(state, *late), esc(id), esc(&w::in_time(*late, when("rule"), when("results"))))
                    })
                    .collect(),
                "reproduced" => list(&o, "chart")
                    .iter()
                    .filter(|c| c.get("claim").and_then(Value::as_bool).unwrap_or(false))
                    .map(|c| format!("<li><a href=\"#{}\">{} {} {}</a></li>", esc(&self.card_to(text(c, "id"))), sq(text(c, "state"), false), esc(text(c, "id")), esc(text(c, "result_text"))))
                    .collect(),
                "tries" => exps
                    .iter()
                    .map(|x| {
                        let runs = list(self.doc, "experiments").iter().find(|e| text(e, "id") == x).and_then(|e| count(e, "runs")).unwrap_or(0);
                        format!("<li><a href=\"#{}\">{}</a> <a href=\"#x-{}:runs\">{runs} run{}</a></li>", esc(&self.card_to(x)), esc(x), esc(x), if runs == 1 { "" } else { "s" })
                    })
                    .collect(),
                _ => exps.iter().map(|x| format!("<li><a href=\"#x-{}\">{}</a></li>", esc(x), esc(x))).collect(),
            };
            out += &format!(
                "<div class=\"card rc\" id=\"{0}\" data-kind=\"rest\" data-home=\"overview\" data-crumb=\"{1}\"><h2 class=\"tt fh\"><a href=\"#{0}:list\">{2}</a></h2><p class=\"dim sm\"><a href=\"#{0}:list\">{3}</a></p><ul class=\"rl\" id=\"{0}:list\">{4}</ul></div>",
                rest_anchor(key),
                esc(text(r, "label")),
                esc(text(r, "text")),
                esc(text(r, "said")),
                rows.concat()
            );
        }
        out
    }

    /// Decisions (the UX lead's "Still open", Nov 13 2026): what waits, counted in chips by kind ("5 decisions", "1
    /// design to confirm"), each filtering the rows under it; each row one line, wholly a link to its card: a status
    /// chip, the title cut near 70 characters (the whole on hover), the key number as a mini bar, and a chevron. A
    /// reader of a shared page is shown the record's state alone ("5 results not decided"), no row, chip or link.
    fn decisions_block(&self, o: &Value) -> String {
        let rows = list(o, "decisions");
        let made = count(o, "made").unwrap_or(0);
        let made = if made > 0 { format!(" <a class=\"chip\" href=\"#history\">{} <b>{made}</b> \u{203a}</a>", w::MADE) } else { String::new() };
        // the calls that rest on a write git took out, counted apart from those made (overview.rs)
        let took = ov::made_taken_said(count(o, "made_taken_out").unwrap_or(0));
        let made = if took.is_empty() { made } else { format!("{made} <span class=\"took s-undetermined\">{}</span>", esc(&took)) };
        // the kinds, in the order they are counted: decisions, calls to confirm, designs to confirm
        let kind_of = |x: &Value| match (text(x, "kind"), text(x, "code")) {
            ("confirm", "design_pending") => "design",
            ("confirm", _) => "confirm",
            _ => "decide",
        };
        let designs: usize = rows.iter().filter(|x| kind_of(x) == "design").map(|x| text(x, "title").split(' ').next().and_then(|n| n.parse::<usize>().ok()).unwrap_or(1)).sum();
        let n_of = |k: &str| rows.iter().filter(|x| kind_of(x) == k).count();
        let plural = |n: usize, one: &str, many: &str| format!("{n} {}", if n == 1 { one } else { many });
        let kinds: Vec<(&str, String)> = [
            ("decide", n_of("decide"), plural(n_of("decide"), "decision", "decisions")),
            ("confirm", n_of("confirm"), plural(n_of("confirm"), "call to confirm", "calls to confirm")),
            ("design", n_of("design"), plural(designs, "design to confirm", "designs to confirm")),
        ]
        .into_iter()
        .filter(|k| k.1 > 0)
        .map(|k| (k.0, k.2))
        .collect();
        let filters: String = kinds
            .iter()
            .map(|(k, said)| format!("<button type=\"button\" class=\"dchip\" data-filter=\"{k}\" aria-pressed=\"false\">{}</button>", esc(said)))
            .collect();
        // every row, so a kind's chip shows all of its rows
        let li: String = rows.iter().map(|x| self.still_row(x, kind_of(x))).collect();
        // nothing waits only where nothing does: no call, no confirmation and nothing rb found unrecorded (guarantee 17)
        let found = !list(self.doc, "home.found").is_empty() || count(self.doc, "home.found_total").unwrap_or(0) > 0;
        let empty = if rows.is_empty() && !found && list(self.doc, "pending.rows").is_empty() { format!("<p class=\"roll\">{}</p>", super::empty_chip(w::NOTHING_WAITS.trim_end_matches('.'))) } else { String::new() };
        let head = format!("<h2 class=\"ovh2\" id=\"decisions\">{}{made}</h2>", w::DECISIONS);
        if rows.is_empty() {
            return format!("{head}{empty}");
        }
        let owner = format!("<div class=\"dfil\" role=\"group\" aria-label=\"{}\">{filters}</div><ul class=\"dq dql\" data-queue>{li}</ul>", w::DECISIONS);
        // a reader of a shared page reads what is undecided as the record's state: how many results no call is made on
        // (a rule's miss or doubt with no call, a gate missed), and no heading where that is none
        let n = rows.iter().filter(|x| kind_of(x) == "decide" && matches!(text(x, "code"), "undecided" | "gate_missed")).count();
        // the heading is drawn once, so the page holds one #decisions: beside every viewer's words where the reader has a
        // state to read, else with the owner's list alone
        if self.surface == Surface::Open {
            return format!("{head}{owner}");
        }
        if n == 0 {
            let owner = format!("{head}{owner}");
            return by_viewer("div", "", &owner, &owner, "");
        }
        // the count opens the rules, where each is said with its state (closed form)
        let reader = format!("<p class=\"dstate\"><a href=\"#criteria\">{}</a></p>", esc(&plural(n, "result not decided", "results not decided")));
        format!("{head}{}", by_viewer("div", "", &owner, &owner, &reader))
    }

    /// One row of what waits: wholly a link to its card (a rule's tabbed card where the page draws it), a status chip
    /// in its state's colour (the status said once), the title cut near 70 characters with the whole on hover, the key
    /// number as a mini bar (the estimate a dot, its interval a line, the rule's number a tick), and a chevron.
    fn still_row(&self, x: &Value, kind: &str) -> String {
        let subject = text(x, "subject");
        let f = self.doc.get("findings").and_then(|m| m.get(subject));
        let (state, said) = match (kind, f) {
            ("design", _) => ("design", "design".to_string()),
            ("confirm", _) => ("design", "to confirm".to_string()),
            (_, Some(f)) => {
                let st = text(f, "state");
                let said = match st {
                    "misses" => "not met",
                    "not reproduced" => "not reproduced",
                    "gate missed" => "gate missed",
                    "undetermined" | "gate undetermined" => "can't tell yet",
                    "untested" | "gate untested" => "no results yet",
                    "meets" | "gate met" => "met",
                    "reproduced" => "reproduced",
                    other => other,
                };
                let cls = match st {
                    "misses" | "not reproduced" | "gate missed" => "misses",
                    "undetermined" | "gate undetermined" => "undetermined",
                    "untested" | "gate untested" => "untested",
                    _ => "meets",
                };
                (cls, said.to_string())
            }
            _ if text(x, "code") == "gate_missed" => ("misses", "gate missed".to_string()),
            _ => ("undetermined", "to decide".to_string()),
        };
        // where it opens: a rule's own card (its tabs), else where the row opened before
        let card = format!("c-{subject}");
        let to = if f.is_some() && self.ids.contains(&esc(&card)) {
            card
        } else if kind != "decide" {
            if text(x, "cmd").strip_prefix("rb confirm ").map_or(false, |id| !id.contains(' ') && fields::has(id)) {
                fields::to(text(x, "cmd").trim_start_matches("rb confirm "))
            } else if list(self.doc, "pending.rows").is_empty() {
                "your-call".to_string()
            } else {
                "to-confirm".to_string()
            }
        } else if fields::has(subject) {
            fields::to(subject)
        } else {
            let to = self.story_to(subject);
            if self.ids.contains(&esc(&to)) { to } else { "your-call".to_string() }
        };
        let full = text(x, "title");
        let short = if full.chars().count() > 72 {
            let cut: String = full.chars().take(70).collect();
            format!("{}\u{2026}", cut.rsplit_once(' ').map_or(cut.as_str(), |(h, _)| h).trim_end_matches([',', ';', ':', '.']))
        } else {
            full.to_string()
        };
        let took = text(x, "taken_out");
        let took = if took.is_empty() { String::new() } else { format!("<span class=\"took s-undetermined\">{}</span>", esc(took)) };
        let bar = f.map(mini_bar).unwrap_or_default();
        format!(
            "<li class=\"dr dl1\" data-kind=\"{kind}\"><a class=\"drow\" href=\"#{}\" title=\"{}\" data-preview=\"{}\"><span class=\"st s-{state}\">{}</span><span class=\"dt\">{}</span>{took}{bar}<span class=\"chev\" aria-hidden=\"true\">\u{203a}</span></a></li>",
            esc(&to),
            esc(full),
            if f.is_some() { esc(&format!("c-{subject}")) } else { String::new() },
            esc(&said),
            au_title(&short),
        )
    }
    /// Your call's list (the rulings of Nov 12 07:15 +08): every row Decisions holds, each with its kind chip and its
    /// note, a confirmation with its command; the badge on the pin counts them.
    pub(super) fn your_call_list(&self) -> String {
        let o = self.doc.get("overview").cloned().unwrap_or(Value::Null);
        let rows = list(&o, "decisions");
        if rows.is_empty() {
            return String::new();
        }
        // the export, which cannot know who reads it, says no row's note in rb's voice to "you"; no queue (j and k open
        // the view's calls one by one, as before)
        format!("<ul class=\"dq yl\" id=\"yc-list\">{}</ul>", self.decision_rows(rows, rows.len(), self.surface != Surface::Export))
    }

    /// Decision rows: the first `most`, each a kind chip, the check's state where it is about one, the title, for a
    /// confirmation its command (the owner's alone), and with `notes` rb's note on what it waits for.
    fn decision_rows(&self, rows: &[Value], most: usize, notes: bool) -> String {
        rows
            .iter()
            .take(most)
            .map(|x| {
                let kind = text(x, "kind");
                let mark = text(x, "mark");
                let sq = if ["■", "▲", "◧", "□"].contains(&mark) {
                    let state = ov::STATES.iter().find(|s| s.1 == mark).map_or("", |s| s.0);
                    format!("<span class=\"sqm s-{state}\">{mark}</span>")
                } else {
                    String::new()
                };
                // on an export a call is recorded from its call card (nodes.rs), so a call's row opens that card
                let call = format!("call-{}", text(x, "subject"));
                let cmd = text(x, "cmd");
                let call_id = cmd.strip_prefix("rb confirm ").filter(|id| !id.contains(' ')).unwrap_or("");
                let to = if kind == "confirm" && !call_id.is_empty() && fields::has(call_id) {
                    fields::to(call_id)
                } else if kind == "confirm" {
                    "to-confirm".to_string()
                } else if fields::has(text(x, "subject")) {
                    fields::to(text(x, "subject"))
                } else if self.surface == Surface::Export && self.ids.contains(&esc(&call)) {
                    call
                } else {
                    self.story_to(text(x, "subject"))
                };
                // the list of what waits is drawn in Experiments while anything waits, after this view's ids are known
                let waits = to == "to-confirm" && !list(self.doc, "pending.rows").is_empty();
                let lazy = to.starts_with("k-") && fields::has(text(x, "subject"));
                let to = if waits || lazy || self.ids.contains(&esc(&to)) { to } else { "your-call".to_string() };
                // a row of designs that wait is said by what it is (UX8 D5): "2 designs to confirm", never "2 plans"
                let named = text(x, "title");
                let named = match named.split_once(' ') {
                    Some((n, "plan" | "plans")) if n.chars().all(|c| c.is_ascii_digit()) => format!("{n} {} to confirm", if n == "1" { "design" } else { "designs" }),
                    _ => named.to_string(),
                };
                let title = au_title(&named);
                // a row resting on a write git took out says so on the row, in rb's words for it
                let took = text(x, "taken_out");
                let title = if took.is_empty() { title } else { format!("{title}<span class=\"took s-undetermined\">{}</span>", esc(took)) };
                // on Your call's list, rb's note on what the row waits for, inside the row's link: it opens what it says
                let title = if notes && !text(x, "said").is_empty() { format!("{title}<span class=\"dn\">{}</span>", esc(text(x, "said"))) } else { title };
                let title = a(&to, &title, "");
                // a reader of a shared page is asked to do nothing: a call reads "open", and a confirmation, the owner's
                // alone, is not drawn for them (OWNER-READER), on rb page and on the export alike
                let shared = self.surface != Surface::Open;
                if kind == "confirm" {
                    // the action is a prompt for their agent, never a command to type (owner, msg 47); the
                    // confirming itself stays theirs, in their own terminal (G5)
                    let on = text(x, "subject");
                    let cmd = if cmd.is_empty() || self.past {
                        String::new()
                    } else if on.is_empty() || call_id.is_empty() {
                        super::prompt_button(&super::placed(w::ALL_PROMPT, cmd))
                    } else {
                        super::prompt_button(&super::placed(&format!("{}{on}{}", w::CALL_PROMPT_HEAD, w::CALL_PROMPT_TAIL), cmd))
                    };
                    // a reader reads the row as open, with no command (DESIGN-UX5 2.2), so the rows match the count
                    if shared {
                        let chip = viewer_chip("kc", w::CHIP_CONFIRM, w::TIP_CONFIRM);
                        format!("<li class=\"dr\">{chip}{title}{}</li>", if cmd.is_empty() { cmd } else { by_viewer("div", " class=\"dcw\"", &cmd, &cmd, "") })
                    } else {
                        format!("<li class=\"dr\"><span class=\"chip k kc\" tabindex=\"0\" title=\"{}\">{}</span>{title}{cmd}</li>", w::TIP_CONFIRM, w::CHIP_CONFIRM)
                    }
                } else if shared {
                    let chip = viewer_chip("kd", w::CHIP_DECIDE, w::TIP_DECIDE);
                    format!("<li class=\"dr\">{chip}{sq}{title}</li>")
                } else {
                    format!("<li class=\"dr\"><span class=\"chip k kd\" tabindex=\"0\" title=\"{}\">{}</span>{sq}{title}</li>", w::TIP_DECIDE, w::CHIP_DECIDE)
                }
            })
            .collect()
    }
}

/// A rule's key number as a mini bar (the UX lead's Still open, Nov 13 2026): the interval a line, the estimate a dot,
/// the rule's number a tick, on a shared scale with a little room either side; nothing where the reading has no number.
fn mini_bar(f: &Value) -> String {
    let num = |k: &str| {
        k.split('.').try_fold(f, |v, p| v.get(p)).and_then(Value::as_f64).filter(|v| v.is_finite())
    };
    let Some(est) = num("reading.estimate") else { return String::new() };
    let (lo, hi, tick) = (num("reading.lo"), num("reading.hi"), num("check_band.target"));
    let vals: Vec<f64> = [Some(est), lo, hi, tick].into_iter().flatten().collect();
    let (mut a, mut b) = vals.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), v| (a.min(*v), b.max(*v)));
    let span = if b > a { b - a } else if a != 0.0 { a.abs() } else { 1.0 };
    a -= span * 0.12;
    b += span * 0.12;
    let x = |v: f64| 3.0 + (v - a) / (b - a) * 82.0;
    let mut p = String::new();
    if let (Some(lo), Some(hi)) = (lo, hi) {
        p += &format!("<line class=\"iv\" x1=\"{:.1}\" y1=\"8\" x2=\"{:.1}\" y2=\"8\"/>", x(lo), x(hi));
    }
    if let Some(t) = tick {
        p += &format!("<line class=\"tk\" x1=\"{0:.1}\" y1=\"2\" x2=\"{0:.1}\" y2=\"14\"/>", x(t));
    }
    p += &format!("<circle class=\"est\" cx=\"{:.1}\" cy=\"8\" r=\"3\"/>", x(est));
    format!("<svg class=\"mbar\" viewBox=\"0 0 88 16\" width=\"88\" height=\"16\" aria-hidden=\"true\">{p}</svg>")
}
