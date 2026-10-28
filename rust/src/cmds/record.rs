//! The commands that write the record: init, questions, experiments, variants, notes, metrics, settings, criteria,
//! runs, resources, analyses, retractions, freezes and decisions. Each says what happened.

use super::*;
use crate::assess;
use crate::ledger::{self, Added, FromRow, NewAnalysis, NewCriterion, NewExperiment, NewResource, NewRun, PastGiven, SetSetting};
use crate::value::Value;

type R = Res<i32>;

pub fn cmd_init(ns: &Namespace, out: &mut Out) -> R {
    let cwd = std::env::current_dir().unwrap_or_default();
    let title = s(ns, "title").filter(|t| !t.is_empty()).unwrap_or_else(|| cwd.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default());
    let id = s(ns, "id").filter(|i| !i.is_empty());
    let led = Ledger::init(&cwd, &title, id.as_deref(), None, None, flag(ns, "new_project"))?;
    let proj = led.project()?;
    out.say(format!("Project {} started: {}", repr_str(&proj.id), crate::sys::slashed(&led.dir)));
    out.say("Rabbit Brain YAML files beside the code: commit .rb/ with it, so the record travels with the work.");
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("project", proj.dump(false)));
    out.data.insert("dir".into(), Value::Str(crate::sys::slashed(&led.dir)));
    let consent = if flag(ns, "no_agents") { None } else { Some(set_up_agents(&led.root, out, flag(ns, "yes"))?) };
    // rb's first look here, once rb init has written all it writes, is when the record begins: what is there now is the
    // work it began after, and only a results file that lands later is proposed as a run
    crate::journal::look(&led);
    if crate::dry::active() {
        init_plan(&led, out, consent);
    }
    // the one line the researcher pastes into the agent's chat; run by an agent, it is that agent's next step
    out.say("");
    if crate::skill::session_runtime().is_some() {
        out.say(crate::skill::PASTE_LINE);
        out.next = vec!["rb context --model <your model id>".into()];
    } else {
        out.say("Then paste this line into your agent's chat:");
        out.say(format!("  {}", crate::skill::PASTE_LINE));
    }
    out.data.insert("paste".into(), Value::str(crate::skill::PASTE_LINE));
    Ok(0)
}

pub fn cmd_project_title(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let project = led.set_project_title(&s_or(ns, "title"), &s_or(ns, "why"))?;
    out.say(format!("Project {} title: {}", repr_str(&project.id), project.title));
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("project", project.dump(false)));
    Ok(0)
}

/// What rb init --dry-run says it would write: each path under .rb/, each instruction file it would make, each file
/// that was there it would change and how, and what later commands add (`data.plan`, `data.later`).
fn init_plan(led: &Ledger, out: &mut Out, consent: Option<crate::skill::Consent>) {
    let shown = |p: &Path| crate::sources::rel_to(p, &led.root).unwrap_or_else(|| crate::sys::slashed(p));
    let held = crate::dry::held_now();
    let mut plan: Vec<Value> = Vec::new();
    let mut record: Vec<String> = Vec::new();
    let mut lines: Vec<String> = Vec::new();
    let mut outside = false;
    for (path, how) in &held {
        let rel = shown(path);
        if path.starts_with(&led.dir) {
            record.push(path.strip_prefix(&led.dir).map(crate::sys::slashed).unwrap_or_else(|_| rel.clone()));
            plan.push(crate::obj! {"path" => rel, "how" => *how, "what" => "the record"});
            continue;
        }
        outside = true;
        // AGENTS.md as it is now: rb's section goes in place of one between rb's marker lines, else at its end
        let agents_md = path.file_name().map_or(false, |n| n == "AGENTS.md");
        let replaces = agents_md.then(|| std::fs::read(path).ok()).flatten().and_then(|b| String::from_utf8(b).ok()).and_then(|now| {
            crate::splice::section_span(&now, crate::skill::BEGIN, crate::skill::END).and_then(|(s, e)| crate::splice::lines_of(&now, &now[s..e]))
        });
        let in_place = replaces.map(|(a, b)| format!("rb's section in place of the one between rb's markers (lines {a} to {b}); nothing else in it changes"));
        // a write a link takes elsewhere is named with the file it lands in; one outside the repository waits for the yes
        let link = crate::skill::through_link(path, &led.root);
        let outside = link.as_ref().map_or(false, |l| l.outside);
        let what = match (*how, path.file_name().and_then(|n| n.to_str())) {
            ("change", Some("AGENTS.md")) if in_place.is_some() => in_place.as_deref().unwrap_or_default(),
            ("change", Some("AGENTS.md")) => "rb's section added at its end; nothing else in it changes",
            ("change", Some(".mcp.json")) => "rb's MCP server added to its mcpServers; nothing else in it changes",
            ("change", _) => "written again: rb's own file",
            _ => "new",
        };
        let asks = (*how == "change" || outside) && consent == Some(crate::skill::Consent::Ask);
        lines.push(format!("  {}: {what}{}", crate::skill::named(path, &led.root), if asks { " (rb init asks you first)" } else { "" }));
        let mut entry = crate::obj! {"path" => rel, "how" => *how, "what" => what};
        if let (Some(l), Value::Dict(d)) = (&link, &mut entry) {
            d.insert("real_path".into(), Value::Str(crate::sys::slashed(&l.real)));
        }
        plan.push(entry);
    }
    let mut said = vec!["rb init would write:".to_string(), format!("  .rb/: the record ({}), and rb's lock and local copies (.lock, cache/), which .rb/.gitignore keeps out of git", record.join(", "))];
    for own in [".lock", "cache/"] {
        plan.push(crate::obj! {"path" => format!("{}/{own}", shown(&led.dir)), "how" => "new", "what" => "rb's own, kept out of git"});
    }
    said.extend(lines);
    let home = crate::owned::rb_home(&led.root);
    let ledger = crate::owned::ledger_path(&led.root).or_else(|| (home.is_none()).then(|| led.dir.join("cache").join(crate::owned::FILE)));
    // the journal of rb init's look goes in git's folder, outside .rb/, and rb's list of what it writes there says so
    let outside = outside || home.is_some();
    if outside {
        if let Some(ledger) = &ledger {
            said.push(format!("  {}: rb's list of what it writes outside .rb/", shown(ledger)));
            plan.push(crate::obj! {"path" => shown(ledger), "how" => if ledger.exists() { "add to" } else { "new" }, "what" => "rb's list of what it writes outside .rb/"});
        }
    }
    // the look rb init makes as the record begins keeps what it saw in the journal
    let journal = match &home {
        Some(h) => format!("{}/", shown(&h.join("journal"))),
        None => format!("{}/journal/", shown(&led.dir)),
    };
    said.push(format!("  {journal}: what rb sees land in the repository, from the look rb init makes as the record begins"));
    plan.push(crate::obj! {"path" => journal, "how" => "new", "what" => "what rb sees land in the repository, from its first look"});
    let mut later: Vec<Value> = Vec::new();
    said.push("Later commands add:".into());
    let mut add_later = |path: String, by: &str, what: &str| {
        said.push(format!("  {path}: {what} ({by})"));
        later.push(crate::obj! {"path" => path, "by" => by, "what" => what});
    };
    if let Some(h) = &home {
        add_later("refs/rb/snapshots/<tree>".into(), "rb run add, rb journal run", "a ref in git to the code a run was recorded at; git update-ref -d removes it");
        add_later(shown(&h.join("views")) + "/", "rb open, rb ui", "a past version of the record, checked out while the screen shows it");
        if let Some(seen) = h.parent().map(|c| c.join("rb-last-seen.json")) {
            add_later(shown(&seen), "rb ui", "what you last saw, so rb ui can say what is new since");
        }
    }
    if let Some(ledger) = ledger.filter(|_| !outside) {
        add_later(shown(&ledger), "rb run add, rb page, rb export", "rb's list of what it writes outside .rb/");
    }
    add_later("rb-page.html".into(), "rb page", "the record as one page, beside .rb/ by default");
    add_later("rb-export/".into(), "rb export", "the page with a manifest, beside .rb/ by default");
    add_later("rb-export.zip".into(), "rb export --zip", "the same, as one zip file");
    out.data.insert("plan".into(), Value::List(plan));
    out.data.insert("later".into(), Value::List(later));
    out.plan = Some(said);
}

/// Install rb's instructions for the agents found at `root` (rb init, and rb reconcile --apply when it starts the record).
/// A file that is there changes only with `yes`, or once the user agrees in their own terminal, asked once. Returns
/// the consent it had.
pub fn set_up_agents(root: &Path, out: &mut Out, yes: bool) -> Res<crate::skill::Consent> {
    out.say("");
    out.say("For the agents here:");
    if crate::skill::session_runtime().is_some() {
        // the session running this would load none of it: nothing is installed, and rb says how, for later sessions
        let later = crate::skill::later_commands(root);
        let mut said = vec!["Nothing installed: a session already running loads no skill or MCP server added during it. For later sessions:".to_string()];
        said.extend(later.iter().map(|l| format!("  {l}")));
        out.say_all(said.iter().map(|l| format!("  {l}")));
        out.data.insert(
            "agents".into(),
            crate::obj! {"said" => Value::list_of_str(said), "written" => Value::List(vec![]), "ignored" => Value::List(vec![]), "later" => Value::list_of_str(later)},
        );
        return Ok(crate::skill::Consent::No);
    }
    let consent = crate::skill::consent(yes, out.json_mode);
    let mut ask = |edits: &[&crate::skill::Install]| -> bool {
        crate::skill::ask_once(std::mem::take(&mut out.lines), &crate::skill::question(edits, root), &crate::skill::prompt(edits, root))
    };
    let done = crate::skill::setup_agents(root, consent, &mut ask)?;
    let mut said = done.said;
    let kept_out = crate::skill::ignored(root, &[done.written.clone(), done.present.clone()].concat());
    said.extend(kept_out.iter().map(|p| crate::skill::ignored_said(p)));
    for line in &said {
        out.say(format!("  {line}"));
    }
    let mut agents = crate::dict! {
        "said" => Value::list_of_str(said), "written" => Value::list_of_str(done.written), "ignored" => Value::list_of_str(kept_out), "later" => Value::List(vec![])
    };
    if !done.left.is_empty() {
        agents.insert("left".into(), Value::List(done.left));
    }
    out.data.insert("agents".into(), Value::Dict(agents));
    Ok(consent)
}

pub fn cmd_question_add(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let q = led.add_question(&s_or(ns, "text"), s(ns, "id").as_deref(), s(ns, "parent").as_deref())?;
    out.say(format!("{}: {}{}", q.id, q.text, q.parent.as_ref().filter(|p| !p.is_empty()).map(|p| format!(" · under {p}")).unwrap_or_default()));
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("question", q.dump(false)));
    out.next = vec![format!("rb experiment add \"<what would answer it>\" -q {} --hypothesis \"<what you expect>\"", q.id)];
    Ok(0)
}

/// rb question reword: the question's new wording, kept as a revision (the log keeps the old one, who and why).
pub fn cmd_question_reword(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let id = s_or(ns, "question");
    let before: Option<Question> = led.load(&id).ok();
    let q = led.reword_question(&id, &s_or(ns, "text"), &s_or(ns, "why"))?;
    if before.as_ref().map_or(false, |b| b.text == q.text) {
        out.say(format!("{} already reads so: nothing written.", q.id));
    } else {
        out.say(format!("{} now reads: {}", q.id, q.text));
        if let Some(b) = &before {
            out.say(format!("  It read: {}", b.text));
            out.say("  The log keeps both wordings; its experiments, evidence and calls stay with it.");
        }
    }
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("question", q.dump(false)));
    Ok(0)
}

pub fn cmd_question_move(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let parent = s(ns, "parent");
    let top = flag(ns, "top");
    if parent.is_none() == !top {
        return Err(RBError::msg("E_USAGE", "rb question move <question> --parent <question>, or --top"));
    }
    let q = led.move_question(&s_or(ns, "question"), if top { None } else { parent.as_deref() }, &s_or(ns, "why"))?;
    out.say(format!(
        "{} is now {}: its experiments and their evidence go with it.",
        q.id,
        match q.parent.as_ref().filter(|p| !p.is_empty()) {
            Some(p) => format!("under {p}"),
            None => "at the top".into(),
        }
    ));
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("question", q.dump(false)));
    Ok(0)
}

pub fn cmd_experiment_gate(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let eid = s_or(ns, "experiment");
    let before: Experiment = led.load(&eid)?;
    let e = led.set_gate(&eid, s(ns, "if_holds").as_deref(), s(ns, "if_not").as_deref(), &s_or(ns, "why"))?;
    let now_line = crate::view::gate_line(&e.if_holds, &e.if_not);
    let was = crate::view::gate_line(&before.if_holds, &before.if_not);
    // the gate as every screen says it: the words given once, and whether it is the user's
    let changed = (&e.if_holds, &e.if_not) != (&before.if_holds, &before.if_not);
    let (words, by) = if changed { (led.words.clone(), led.actor()?.id) } else { led.gate_words()?.remove(&e.id).unwrap_or_default() };
    let design = led.design(&e.id)?.unwrap_or_default();
    let said = crate::view::gate_said(&e.if_holds, &e.if_not, &words, &design.writer(&by, "gate"), "");
    if changed {
        out.say(format!("{} gates: {said}", e.id));
    } else {
        out.say(format!("{} already gates: {said}", e.id));
    }
    // a gate a session set waits with its design, whose line says how it is listed to confirm
    if let Some(line) = design.standing_line() {
        out.say(format!("  {line}"));
    }
    if !was.is_empty() && was != now_line {
        out.say(format!("  before: {was} (kept in rb log {})", e.id));
    }
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("experiment", e.dump(false)));
    Ok(0)
}

pub fn cmd_experiment_file(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let e = led.file_under(&s_or(ns, "experiment"), &s_or(ns, "question"), flag(ns, "remove"))?;
    out.say(format!("{} is filed under {}.", e.id, if e.questions.is_empty() { "no question".to_string() } else { e.questions.join(", ") }));
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("experiment", e.dump(false)));
    Ok(0)
}

fn experiment_said(out: &mut Out, e: &Experiment, like: Option<&str>) {
    let arms = e.variants.iter().map(|v| format!("{} ({})", v.name, v.role)).collect::<Vec<_>>().join(" vs ");
    out.say(format!(
        "Experiment {}: {}{}{}{}",
        e.id,
        e.title,
        if arms.is_empty() { String::new() } else { format!(" · {arms}") },
        if e.varies.is_empty() { String::new() } else { format!(" · varies {}", e.varies.join(", ")) },
        if e.questions.is_empty() { String::new() } else { format!(" · under {}", e.questions.join(", ")) }
    ));
    if !e.hypothesis.is_empty() {
        out.say(format!("  hypothesis: {}{}", e.hypothesis, crate::view::why_part(&e.why, &e.created_by).map(|(label, text)| format!(" ({label}: {text})")).unwrap_or_default()));
    }
    if !e.purpose.is_empty() {
        out.say(format!("  purpose: {}", e.purpose));
    }
    if let Some(l) = like.filter(|l| !l.is_empty()) {
        out.say(format!("  settings copied from {l} as provisional: {}; verify them with rb spec verify {}", e.settings.len(), e.id));
    }
    out.say("  Planned: nothing in the record tests it yet, and it shows as a planned direction until its first run or analysis.");
    if e.variants.is_empty() {
        out.say(format!("  No variants yet. A comparison needs them: rb variant add {} <name> --role baseline|candidate", e.id));
    }
}

pub fn cmd_experiment_add(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let varies = comma_list(&list(ns, "varies"));
    let a = NewExperiment {
        title: s_or(ns, "title"),
        id: s(ns, "id"),
        question: None,
        questions: list(ns, "question"),
        hypothesis: s_or(ns, "hypothesis"),
        why: s_or(ns, "why"),
        purpose: s_or(ns, "purpose"),
        baseline: s(ns, "baseline"),
        candidates: list(ns, "candidate"),
        varies,
        like: s(ns, "like"),
        if_holds: s_or(ns, "if_holds"),
        if_not: s_or(ns, "if_not"),
    };
    let mut e = led.add_experiment(&a)?;
    if let Some(note) = s(ns, "note").filter(|n| !n.is_empty()) {
        led.add_note(Some(&e.id), &note)?;
        e = led.load(&e.id)?;
    }
    experiment_said(out, &e, s(ns, "like").as_deref());
    // the variants --baseline and --candidate made, said, so none is added again
    if a.baseline.is_some() || !a.candidates.is_empty() {
        let made: Vec<String> = e.variants.iter().map(|v| format!("{} ({})", v.name, v.role)).collect();
        if let Some(first) = e.variants.first() {
            out.say(format!(
                "  Made {} {}: {}; a setting of one is rb spec set {} {}.<setting> <value>.",
                made.len(),
                if made.len() == 1 { "variant" } else { "variants" },
                made.join(", "),
                e.id,
                first.name
            ));
        }
    }
    out.notes.extend(varies_same(&led, &e.id, None)?);
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("experiment", e.dump(false)));
    out.next = vec![
        format!("rb spec set {} --from <config.yaml> --keys \"<the keys that define it>\"", e.id),
        format!("rb criterion add \"<what it should show>\" -e {}{} --metric <name> --at-most <x>", e.id, led.question_arg(&e)),
    ];
    Ok(0)
}

pub fn cmd_experiment_relate(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let eid = s_or(ns, "experiment");
    let rel = led.relate(&eid, &s_or(ns, "relation"), &s_or(ns, "target"), &s_or(ns, "note"))?;
    out.say(format!("{eid} {} {}{}", rel.kind.replace('_', " "), rel.target, if rel.note.is_empty() { String::new() } else { format!(": {}{}", rel.note, crate::view::not_confirmed(&rel.by)) }));
    out.say("  A relation is structure: it changes no finding.");
    actor_line(out, &led)?;
    let mut d = crate::dict! {"kind" => "relation", "experiment_id" => eid};
    for (k, v) in rel.dump(false) {
        d.insert(k, v);
    }
    out.data.insert("object".into(), Value::Dict(d));
    Ok(0)
}

pub fn cmd_note_add(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let exp = s(ns, "experiment");
    let note = led.add_note(exp.as_deref(), &s_or(ns, "text"))?;
    out.say(format!("Note {} on {}: {}", note.id.clone().unwrap_or_default(), exp.clone().unwrap_or_else(|| "the project".into()), note.text));
    out.say("  Kept verbatim with who wrote it; a note changes no finding.");
    actor_line(out, &led)?;
    let mut d = crate::dict! {"kind" => "note", "experiment_id" => exp};
    for (k, v) in note.dump(false) {
        d.insert(k, v);
    }
    out.data.insert("object".into(), Value::Dict(d));
    Ok(0)
}

pub fn cmd_variant_add(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let eid = s_or(ns, "experiment");
    let amend_ = amend(ns)?;
    let v = led.add_variant(&eid, &s_or(ns, "name"), &s_or(ns, "role"), &s_or(ns, "note"), amend_.as_deref(), &s_or(ns, "why"))?;
    out.say(format!("{eid}: variant {} ({})", v.name, v.role));
    actor_line(out, &led)?;
    let mut d = crate::dict! {"kind" => "variant", "id" => format!("{eid}/{}", v.name), "experiment_id" => eid.clone()};
    for (k, val) in v.dump(false) {
        d.insert(k, val);
    }
    out.data.insert("object".into(), Value::Dict(d));
    out.next = vec![format!("rb spec set {eid} {}.<setting> <value> --source <file#key>", v.name)];
    Ok(0)
}

pub fn cmd_metric_add(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let direction = if flag(ns, "minimize") {
        "minimize"
    } else if flag(ns, "maximize") {
        "maximize"
    } else {
        "none"
    };
    let m = led.add_metric(&s_or(ns, "name"), &s_or(ns, "unit"), direction, &list(ns, "alias"), &s_or(ns, "description"))?;
    let word = match direction {
        "minimize" => "lower is better",
        "maximize" => "higher is better",
        _ => "no direction",
    };
    out.say(format!(
        "Metric {}{}: {word}{}",
        m.id,
        if m.unit.is_empty() { String::new() } else { format!(" ({})", m.unit) },
        if m.aliases.is_empty() { String::new() } else { format!(" · also called {}", m.aliases.join(", ")) }
    ));
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("metric", m.dump(false)));
    Ok(0)
}

fn setting_obj(experiment: &str, name: &str, st: &Setting) -> Value {
    let mut d = crate::dict! {"kind" => "setting", "id" => format!("{experiment}/{name}"), "experiment_id" => experiment, "address" => format!("{experiment}/{name}")};
    for (k, v) in st.dump(false) {
        d.insert(k, v);
    }
    Value::Dict(d)
}

pub fn failure(row: &Dict) -> Value {
    crate::obj! {
        "code" => row.get("code").cloned().unwrap_or(Value::Null), "subject" => row.get("setting").cloned().unwrap_or(Value::Null),
        "reason" => row.get("reason").cloned().unwrap_or(Value::Null),
        "candidates" => row.get("candidates").filter(|v| v.truthy()).cloned().unwrap_or(Value::List(Vec::new())),
        "fix" => "point --source at the line or key that states it, give --term with the word the file uses, or set the value the source states"
    }
}

pub fn say_row(out: &mut Out, r: &Dict) {
    let setting = get_s(r, "setting");
    if r.get("skipped").map_or(false, |v| v.truthy()) {
        out.say(format!("  {setting}: {}", get_s(r, "reason")));
        return;
    }
    if r.get("ok").map_or(false, |v| v.truthy()) {
        let at = match r.get("commit").filter(|v| v.truthy()) {
            Some(c) => format!(" @{}", trunc(&pyjson::py_str(c), 7)),
            None => " (uncommitted file)".into(),
        };
        out.say(format!("  {setting}: verified in {}{at}, reads {}", get_s(r, "where"), pyjson::py_repr(r.get("read").unwrap_or(&Value::Null))));
        return;
    }
    out.say(format!(
        "  {setting}: {}{}, not verified ({}): {}",
        get_s(r, "after"),
        if r.get("conflict").map_or(false, |v| v.truthy()) { " · CONFLICT" } else { "" },
        get_s(r, "code"),
        get_s(r, "reason")
    ));
    if let Some(Value::List(cands)) = r.get("candidates") {
        for c in cands {
            out.say(format!("      line {}: {}", c.get("line").map(pyjson::py_str).unwrap_or_default(), c.get("text").map(pyjson::py_str).unwrap_or_default()));
        }
    }
}

pub fn cmd_spec_set(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    if s(ns, "from_file").is_some() {
        if flag(ns, "design_factor") || flag(ns, "sample_factor") {
            return Err(RBError::msg("E_USAGE", "Declare a design factor by name; --from reads fixed setting values."));
        }
        return spec_from(ns, out, &led);
    }
    let Some(name0) = s(ns, "name") else {
        return Err(RBError::msg("E_USAGE", "rb spec set <experiment> <name> [<value>] --source <file#key>, or rb spec set <experiment> --from <file> --keys \"...\""));
    };
    let unknown = flag(ns, "unknown");
    if s(ns, "value").is_some() && unknown {
        return Err(RBError::msg("E_OBJECT_INVALID", "Give a value or --unknown, not both."));
    }
    let mut value = match s(ns, "value") {
        Some(v) => parse_value(&v)?,
        None => Value::Null,
    };
    let source = parse_source(s(ns, "source").as_deref(), s(ns, "quote").as_deref(), s(ns, "commit").as_deref(), s(ns, "locator").as_deref(), s(ns, "term").as_deref(), &led.root)?;
    let mut cited = None;
    if let Some(cv) = s(ns, "cited") {
        let csrc = match s(ns, "cited_source").filter(|x| !x.is_empty()) {
            Some(cs) => parse_source(Some(&cs), None, None, None, None, &led.root)?,
            None => None,
        };
        cited = Some(Cited { value: parse_value(&cv)?, source: csrc });
    }
    let required = if flag(ns, "optional") {
        Some(false)
    } else if flag(ns, "required") {
        Some(true)
    } else {
        None
    };
    let name = match s(ns, "variant").filter(|v| !v.is_empty()) {
        Some(v) => format!("{v}.{name0}"),
        None => name0.clone(),
    };
    let per_run = flag(ns, "per_run");
    let experiment = s_or(ns, "experiment");
    let spec_before = led.load::<Experiment>(&experiment).ok().map(|e| led.spec_sha(&e));
    if value.is_null() && !unknown && !per_run && !flag(ns, "design_factor") {
        if let Some(src) = &source {
            if src.key.as_ref().map_or(false, |k| !k.is_empty()) || src.kind == "run" || src.line.map_or(false, |l| l != 0) {
                value = read_value(&led, src, &experiment, &name)?;
            }
        }
    }
    let (setting, row) = led.set_setting(
        &experiment,
        &name,
        SetSetting {
            value,
            unknown,
            source,
            required,
            per_run: if per_run { Some(true) } else { None },
            design_factor: if flag(ns, "design_factor") { Some(true) } else if flag(ns, "sample_factor") { Some(false) } else { None },
            cited,
            note: s(ns, "note"),
            amend: amend(ns)?,
            verify: !flag(ns, "no_verify"),
            why: s_or(ns, "why"),
        },
    )?;
    // verified from a line no commit before a past run held reads provisional, as the note under it says
    let later = postdates_notes(&led, &experiment, &[(name.clone(), setting.clone())])?;
    let reads = if setting.status == "verified" && !later.is_empty() { "provisional" } else { setting.status.as_str() };
    let status = format!("{reads}{}", if setting.required { "" } else { " (optional)" });
    let where_ = match &setting.source {
        Some(src) if reads == "verified" => format!(" in {}", src.label()),
        Some(src) => format!(" · source {}", src.label()),
        None => String::new(),
    };
    if setting.per_run {
        out.say(format!("{experiment}/{name} · per-run: each run gives its own, with --set {name}=<value> on rb run add"));
        if setting.design_factor == Some(true) { out.say("  Fixed design factor: comparisons across its cells are descriptive, with no sampling interval across cells."); }
    } else {
        out.say(format!("{experiment}/{name} = {} · {status}{where_}", show(&setting.value)));
    }
    let row_failed = row.as_ref().map_or(false, |r| !r.get("ok").map_or(false, |v| v.truthy()));
    if row_failed {
        say_row(out, row.as_ref().unwrap());
    } else if setting.status == "provisional" && setting.source.as_ref().map_or(true, |s| !s.checkable()) {
        out.say("  It stays provisional until a file (file#key, file:LINE, or --quote) or a run's JSON that states it is its source; a url or a note is recorded but cannot be checked.");
    }
    let spec_after = led.load::<Experiment>(&experiment).ok().map(|e| led.spec_sha(&e));
    if spec_before != spec_after && led.all::<Run>()?.iter().any(|r| r.experiment == experiment && r.retracted.is_none()) {
        out.say(SPEC_CHANGED);
    }
    out.notes.extend(later);
    out.notes.extend(varies_same(&led, &experiment, Some(&name))?);
    actor_line(out, &led)?;
    let failures: Vec<Value> = match &row {
        Some(r) if row_failed && !r.get("skipped").map_or(false, |v| v.truthy()) => vec![failure(r)],
        _ => Vec::new(),
    };
    let passed = failures.is_empty();
    out.data.insert("object".into(), setting_obj(&experiment, &name, &setting));
    out.data.insert("outcome".into(), crate::obj! {"passed" => passed, "failures" => Value::List(failures)});
    if setting.status == "provisional" && setting.source.as_ref().map_or(false, |s| s.checkable()) && flag(ns, "no_verify") {
        out.next = vec![format!("rb spec verify {experiment} {name}")];
    }
    Ok(if passed { 0 } else { 1 })
}

/// A factor the experiment varies that every variant holds at one value, said by the write that leaves it so as rb
/// status says it after the runs (varies_unchanged), with how each variant gets its own. With `touched`, only the
/// factor that setting is.
pub(crate) fn varies_same(led: &Ledger, experiment: &str, touched: Option<&str>) -> Res<Vec<String>> {
    let Ok(exp) = led.load::<Experiment>(experiment) else { return Ok(Vec::new()) };
    Ok(led
        .confounds(&exp, None)?
        .into_iter()
        .filter(|c| c.code == "varies_unchanged")
        .filter_map(|c| {
            let factor = c.subject.split_once('/').map(|(_, f)| f.to_string())?;
            touched.map_or(true, |n| n == factor || n.ends_with(&format!(".{factor}"))).then(|| {
                format!("{}; give each variant its own: rb spec set {} <variant>.{factor} <value> --source <file#key>", c.text, exp.id)
            })
        })
        .collect())
}

/// What a write that verified settings says of a file that postdates a past run already recorded, once per file, as
/// findings say it (source_postdates_run).
pub(crate) fn postdates_notes(led: &Ledger, experiment: &str, set: &[(String, Setting)]) -> Res<Vec<String>> {
    let Ok(exp) = led.load::<Experiment>(experiment) else { return Ok(Vec::new()) };
    postdates_lines(led, &exp, set, None)
}

/// What a run add says of a verified setting of its experiment whose file postdates the run it records, once per file,
/// as findings say it.
pub(crate) fn postdates_run(led: &Ledger, experiment: &str, run: &str) -> Res<Vec<String>> {
    let Ok(exp) = led.load::<Experiment>(experiment) else { return Ok(Vec::new()) };
    let set: Vec<(String, Setting)> = exp.all_settings().into_iter().filter(|(_, s)| !s.per_run).map(|(n, s)| (n, s.clone())).collect();
    postdates_lines(led, &exp, &set, Some(run))
}

fn postdates_lines(led: &Ledger, exp: &Experiment, set: &[(String, Setting)], run: Option<&str>) -> Res<Vec<String>> {
    type Key = (String, String, String, Option<(String, String, Option<i64>)>);
    let mut later: indexmap::IndexMap<Key, Vec<String>> = indexmap::IndexMap::new();
    for (name, setting) in set.iter().filter(|(_, st)| st.status == "verified") {
        if let Some((path, _, rid, day, at)) = led.source_postdates(exp, name, setting, None, run)? {
            later.entry((path, rid, day, at)).or_default().push(name.clone());
        }
    }
    Ok(later.into_iter().map(|((path, rid, day, at), names)| crate::ledger::findings::postdates_said(&names, &path, &rid, &day, at.as_ref())).collect())
}

/// With a key path or a run pointer the value is optional: rb reads it.
pub(crate) fn read_value(led: &Ledger, source: &Source, experiment: &str, name: &str) -> Res<Value> {
    let path = source.path.clone().unwrap_or_else(|| "None".into());
    let no_value = |why: String| RBError::msg("E_SOURCE_UNRESOLVED", format!("{} has no value to read ({why}); give the value.", source.label()));
    let got: Value = if source.kind == "run" {
        let p = sources::run_json_path(&led.root, &path);
        let text = match std::fs::read(&p) {
            Ok(b) => String::from_utf8_lossy(&b).into_owned(),
            Err(e) => return Err(no_value(crate::sys::os_words(&e))),
        };
        let doc = pyjson::loads(&text).map_err(|e| no_value(format!("it is not JSON: {e}")))?;
        let Some(pointer) = source.pointer.clone() else { return Err(no_value("it names no JSON pointer".into())) };
        sources::pointer_get(&doc, &pointer).map(|v| v.clone()).map_err(|step| no_value(format!("nothing at {step} there")))?
    } else if let Some(key) = source.key.as_deref().filter(|_| crate::sqlite::is_database(&sources::full_path(&led.root, &path))) {
        sources::row_cell(&led.root, &path, key).map(|(v, _)| v).map_err(|u| no_value(u.reason))?
    } else {
        let (data, _) = sources::read_file(&led.root, &path, source.commit.as_deref()).map_err(|u| RBError::msg(&u.code, u.reason))?;
        let text = String::from_utf8_lossy(&data).into_owned();
        let mut key = source.key.clone();
        let line = source.line.filter(|l| *l != 0);
        if key.is_none() && line.is_some() && sources::structured_kind(&path) != Some("yaml") {
            let lines = sources::split_lines(&text);
            let l = line.unwrap();
            if l > lines.len() as i64 {
                return Err(RBError::msg("E_SOURCE_UNRESOLVED", format!("{path} has {} {}, not {l}.", lines.len(), assess::plural("line", lines.len() as i64))));
            }
            let idx = if l >= 1 { (l - 1) as usize } else { (lines.len() as i64 + l - 1).max(0) as usize };
            let cleaned = sources::cut_trailing_comment(&path, &lines, idx);
            let term = source.term.clone().filter(|t| !t.is_empty()).unwrap_or_else(|| ledger::last_part(name).to_string());
            return match sources::nearest_number(&cleaned, &term) {
                Some(v) => Ok(v),
                None => Err(RBError::msg(
                    "E_SOURCE_UNRESOLVED",
                    format!("{} names no number for {name}; give the value: rb spec set {experiment} {name} <value> {}", source.label(), source.args(false)),
                )),
            };
        }
        if key.is_none() && line.is_some() {
            key = sources::yaml_key_at(&text, line.unwrap().max(0) as usize);
            match &key {
                None => return Err(RBError::msg("E_SOURCE_UNRESOLVED", format!("{} holds no single value to read; give the value.", source.label()))),
                Some(k) => {
                    if !sources::key_names(k, name, source.term.as_deref()) {
                        return Err(RBError::msg(
                            "E_SOURCE_UNRESOLVED",
                            format!("{} is the key {k}, not {name}. Point at {name}'s own line or key, or give --term.", source.label()),
                        ));
                    }
                }
            }
        }
        let kind = sources::structured_kind(&path).unwrap_or("json");
        let doc = sources::parse_structured(&text, kind, &path).map_err(|u| RBError::msg(&u.code, u.reason))?;
        let Some(k) = key else { return Err(no_value("it names no key".into())) };
        sources::get_key(&doc, &k).map(|v| v.clone()).map_err(|step| no_value(format!("no {step} there")))?
    };
    let is_section = match &got {
        Value::Dict(_) => true,
        Value::List(l) => !(!l.is_empty() && l.iter().all(|x| !matches!(x, Value::Dict(_) | Value::List(_)))),
        _ => false,
    };
    if is_section {
        return Err(RBError::msg("E_OBJECT_INVALID", format!("{} is a section, not a value.", source.label())));
    }
    Ok(got)
}

/// Why a config value is not one rb records as a setting, or None.
fn not_a_value(v: &Value) -> Option<String> {
    match v {
        Value::Null => Some("empty".into()),
        Value::Dict(_) => Some("a section".into()),
        Value::List(l) => {
            if !l.is_empty() && l.iter().all(|x| not_a_value(x).is_none() && !matches!(x, Value::List(_))) {
                None
            } else {
                Some("a list of sections or an empty list".into())
            }
        }
        Value::Bool(_) | Value::Int(_) | Value::BigInt(_) => None,
        Value::Float(f) => {
            if f.is_finite() {
                None
            } else {
                Some("not a finite number".into())
            }
        }
        Value::Str(s) => {
            if strip(s).is_empty() {
                Some("empty text".into())
            } else {
                None
            }
        }
    }
}

/// fnmatch.fnmatchcase(name, pattern): * ? [seq] [!seq], whole-string.
pub fn fnmatchcase(name: &str, pattern: &str) -> bool {
    let mut re = String::from("(?s)^");
    let chars: Vec<char> = pattern.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        i += 1;
        match c {
            '*' => re.push_str(".*"),
            '?' => re.push('.'),
            '[' => {
                let mut j = i;
                if j < chars.len() && chars[j] == '!' {
                    j += 1;
                }
                if j < chars.len() && chars[j] == ']' {
                    j += 1;
                }
                while j < chars.len() && chars[j] != ']' {
                    j += 1;
                }
                if j >= chars.len() {
                    re.push_str("\\[");
                } else {
                    let mut stuff: String = chars[i..j].iter().collect();
                    stuff = stuff.replace('\\', "\\\\");
                    i = j + 1;
                    let body = if let Some(rest) = stuff.strip_prefix('!') { format!("^{rest}") } else if stuff.starts_with('^') { format!("\\{stuff}") } else { stuff };
                    re.push_str(&format!("[{body}]"));
                }
            }
            other => re.push_str(&regex::escape(&other.to_string())),
        }
    }
    re.push('$');
    regex::Regex::new(&re).map(|r| r.is_match(name)).unwrap_or(false)
}

/// Record the keys that define an experiment from its config, each sourced by key path and verified.
fn spec_from(ns: &Namespace, out: &mut Out, led: &Ledger) -> R {
    let path = crate::sys::typed(&s_or(ns, "from_file"));
    let Some(kind) = sources::structured_kind(&path) else {
        return Err(RBError::msg("E_OBJECT_INVALID", format!("--from reads YAML, JSON or TOML; {path} is none of those.")));
    };
    let rel = root_relative(&path, &led.root);
    let full = if Path::new(&rel).is_absolute() { PathBuf::from(&rel) } else { led.root.join(&rel) };
    let text = match std::fs::read(&full) {
        Ok(b) => String::from_utf8_lossy(&b).into_owned(),
        Err(e) => return Err(RBError::could_not_read(&e, &path)),
    };
    let doc = sources::unwrap_wandb(sources::parse_structured(&text, kind, &path).map_err(|u| RBError::msg("E_OBJECT_INVALID", u.reason))?);
    let flat: Dict = sources::flatten(&doc, "").into_iter().filter(|(k, _)| !k.starts_with('_')).collect();
    let keys = list(ns, "keys");
    if keys.is_empty() {
        let tops: std::collections::BTreeSet<String> = flat.keys().map(|k| k.split('.').next().unwrap_or("").to_string()).collect();
        let tops: Vec<String> = tops.into_iter().collect();
        let sections: Vec<&String> = tops.iter().filter(|t| flat.keys().any(|k| k.starts_with(&format!("{t}.")))).collect();
        let example = match sections.first() {
            Some(sct) => format!("{sct}.*"),
            None => tops.first().cloned().unwrap_or_default(),
        };
        let msg = if flat.len() == 1 {
            format!("{path} has 1 key; pick it with --keys if it defines the experiment: --keys \"{example}\"")
        } else if !tops.is_empty() {
            format!("{path} has {} keys; pick the ones that define the experiment with --keys, e.g. --keys \"{example}\"", flat.len())
        } else {
            format!("{path} has no keys.")
        };
        return Err(RBError::msg_fix(
            "E_OBJECT_INVALID",
            msg,
            "Choosing which keys define the experiment is the researcher's call: a key like log_every_n_steps should not stop runs counting when it changes.",
        )
        .with_problems(vec![format!("top-level: {}", tops.iter().take(20).cloned().collect::<Vec<_>>().join(", "))]));
    }
    let patterns = comma_list(&keys);
    let chosen: Vec<&String> = flat.keys().filter(|k| patterns.iter().any(|p| fnmatchcase(k, p))).collect();
    if chosen.is_empty() {
        return Err(RBError::msg("E_OBJECT_INVALID", format!("--keys {} matches none of {path}'s {} {}.", patterns.join(", "), flat.len(), assess::plural("key", flat.len() as i64))));
    }
    let mut skipped: Vec<String> = Vec::new();
    let mut todo: Vec<(String, Value)> = Vec::new();
    for key in chosen {
        let value = flat[key].clone();
        match not_a_value(&value) {
            Some(why) => skipped.push(format!("{key} ({why})")),
            None => todo.push((key.clone(), value)),
        }
    }
    let experiment = s_or(ns, "experiment");
    let variant = s(ns, "variant").filter(|v| !v.is_empty());
    let mut rows: Vec<(String, Setting, Option<Dict>)> = Vec::new();
    for (key, value) in todo {
        let name = match &variant {
            Some(v) => format!("{v}.{key}"),
            None => key.clone(),
        };
        let src = Source {
            kind: "file".into(),
            path: Some(rel.clone()),
            key: Some(key.clone()),
            line: None,
            quote: None,
            term: None,
            commit: s(ns, "commit"),
            pointer: None,
            url: None,
            locator: None,
            note: String::new(),
            resolved: None,
        };
        let (setting, row) = led.set_setting(
            &experiment,
            &name,
            SetSetting {
                value,
                source: Some(src),
                required: if flag(ns, "optional") { Some(false) } else { None },
                amend: amend(ns)?,
                verify: !flag(ns, "no_verify"),
                why: s_or(ns, "why"),
                ..Default::default()
            },
        )?;
        rows.push((name, setting, row));
    }
    // one verified from a line no commit before a past run held reads provisional, as the note under it says
    let exp_now = led.load::<Experiment>(&experiment).ok();
    let mut verified = 0;
    for (n, st, _) in &rows {
        if st.status == "verified" && !exp_now.as_ref().map_or(Ok(None), |e| led.source_postdates(e, n, st, None, None))?.is_some() {
            verified += 1;
        }
    }
    let commit = rows.iter().find_map(|(_, _, r)| r.as_ref().and_then(|r| r.get("commit")).filter(|v| v.truthy()).map(pyjson::py_str));
    out.say(format!(
        "{experiment}: {} {} from {rel}{} · {verified} verified",
        rows.len(),
        assess::plural("setting", rows.len() as i64),
        commit.as_ref().map(|c| format!(" @{}", trunc(c, 7))).unwrap_or_default()
    ));
    for (_, _, row) in &rows {
        if let Some(r) = row {
            if !r.get("ok").map_or(false, |v| v.truthy()) {
                say_row(out, r);
            }
        }
    }
    if !skipped.is_empty() {
        out.warn(&format!("not values, so not settings: {}", assess::first_few(&skipped, 12, ", ")));
    }
    out.notes.extend(postdates_notes(led, &experiment, &rows.iter().map(|(n, st, _)| (n.clone(), st.clone())).collect::<Vec<_>>())?);
    for (n, _, _) in &rows {
        out.notes.extend(varies_same(led, &experiment, Some(n))?);
    }
    actor_line(out, led)?;
    let failures: Vec<Value> = rows
        .iter()
        .filter_map(|(_, _, r)| r.as_ref())
        .filter(|r| !r.get("ok").map_or(false, |v| v.truthy()) && !r.get("skipped").map_or(false, |v| v.truthy()))
        .map(failure)
        .collect();
    let passed = failures.is_empty();
    let settings: Vec<Value> = rows
        .iter()
        .map(|(n, st, _)| {
            let mut d = crate::dict! {"kind" => "setting", "id" => format!("{experiment}/{n}"), "address" => format!("{experiment}/{n}")};
            for (k, v) in st.dump(false) {
                d.insert(k, v);
            }
            Value::Dict(d)
        })
        .collect();
    out.data.insert("object".into(), crate::obj! {"kind" => "settings", "experiment_id" => experiment.clone(), "from" => rel.clone(), "settings" => Value::List(settings)});
    out.data.insert("outcome".into(), crate::obj! {"passed" => passed, "failures" => Value::List(failures)});
    Ok(if passed { 0 } else { 1 })
}

pub fn cmd_spec_verify(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let experiment = s_or(ns, "experiment");
    let rows = led.verify_settings(&experiment, &list(ns, "names"))?;
    if rows.is_empty() {
        out.say(format!("{experiment} has no setting with a source to check."));
    }
    let ok = |r: &Dict| r.get("ok").map_or(false, |v| v.truthy());
    let skipped = |r: &Dict| r.get("skipped").map_or(false, |v| v.truthy());
    // a line no commit before a past run held states it, so it reads provisional, as the note under it says
    let exp = led.load::<Experiment>(&experiment)?;
    let mut later: Vec<(String, Setting)> = Vec::new();
    for r in &rows {
        let name = get_s(r, "setting");
        match exp.lookup(&name).filter(|_| ok(r) && !skipped(r)) {
            Some(st) if led.status_read(&exp, &name, st)? == "provisional" => {
                let at = r.get("commit").filter(|v| v.truthy()).map(|c| format!(" @{}", trunc(&pyjson::py_str(c), 7))).unwrap_or_else(|| " (uncommitted file)".into());
                let read = pyjson::py_repr(r.get("read").unwrap_or(&Value::Null));
                out.say(format!("  {name}: provisional, {}{at} reads {read}, but it postdates a run (below)", get_s(r, "where")));
                later.push((name, st.clone()));
            }
            _ => say_row(out, r),
        }
    }
    let failures: Vec<Value> = rows.iter().filter(|r| !ok(r) && !skipped(r)).map(failure).collect();
    let verified = rows.iter().filter(|r| ok(r) && !skipped(r)).count() - later.len();
    let provisional = if later.is_empty() { String::new() } else { format!(" · {} provisional", later.len()) };
    out.say(format!("{verified} verified{provisional} · {} failed · {} not checkable", failures.len(), rows.iter().filter(|r| skipped(r)).count()));
    out.notes.extend(postdates_notes(&led, &experiment, &later)?);
    actor_line(out, &led)?;
    let passed = failures.is_empty();
    out.data.insert("experiment_id".into(), Value::Str(experiment));
    out.data.insert("results".into(), Value::List(rows.into_iter().map(Value::Dict).collect()));
    out.data.insert("verified_count".into(), Value::from(verified));
    out.data.insert("outcome".into(), crate::obj! {"passed" => passed, "failures" => Value::List(failures)});
    Ok(if passed { 0 } else { 1 })
}

/// What a spec change says while the experiment has runs: they no longer count, and how one is read again under it.
const SPEC_CHANGED: &str =
    "  The spec changed: runs recorded before this no longer count toward its criteria. To read one again under this spec, give its rb run add with --replaces <run> --why \"...\".";

pub fn cmd_spec_vary(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let names = comma_list(&list(ns, "names"));
    let amend_ = amend(ns)?;
    let (exp, new) = led.declare_varies(&s_or(ns, "experiment"), &names, amend_.as_deref(), &s_or(ns, "why"))?;
    out.say(format!("{} varies {} on purpose; a difference in any other setting is a confound.", exp.id, exp.varies.join(", ")));
    if led.all::<Run>()?.iter().any(|r| r.experiment == exp.id && r.retracted.is_none()) {
        out.say(SPEC_CHANGED);
    }
    for n in &new {
        out.notes.extend(varies_same(&led, &exp.id, Some(n))?);
    }
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("experiment", exp.dump(false)));
    out.data.insert("declared".into(), Value::list_of_str(new));
    Ok(0)
}

pub fn cmd_criterion_relate(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let (kind, target, remove) = (s_or(ns, "relation"), s_or(ns, "target"), flag(ns, "remove"));
    let c = led.relate_criterion(&s_or(ns, "criterion"), &kind, &target, s(ns, "orientation").as_deref(), remove, &s_or(ns, "why"), amend(ns)?.as_deref())?;
    out.say(format!("{}: {} relation to {}", c.id, if remove { "withdrew" } else { "declared" }, target));
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("criterion", c.dump(false)));
    let latest = c.relations.iter().rev().find(|r| r.kind == kind && r.target == target);
    if remove {
        if let Some(relation) = latest.filter(|r| r.retracted.is_some()) {
            out.data.insert("relation".into(), Value::Dict(Ledger::withdrawn_relation(&c, relation)));
        }
    } else if let Some(relation) = latest.filter(|r| r.retracted.is_none()) {
        let reading = led.criterion_relation(&c, relation, &indexmap::IndexMap::new())?;
        if let Some(Value::Str(summary)) = reading.get("summary") {
            let said = crate::ledger::relations::relation_problems(&reading);
            out.say(if said.is_empty() { summary.clone() } else { format!("{summary} Integrity: {said}.") });
        }
        out.data.insert("relation".into(), Value::Dict(reading));
    }
    Ok(0)
}

pub fn cmd_criterion_file(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let amendment = amend(ns)?;
    let before = led.load::<Criterion>(&s_or(ns, "criterion")).ok().map(|c| c.dump(false));
    let crit = led.file_criterion(&s_or(ns, "criterion"), &s_or(ns, "question"), s(ns, "when_met").as_deref(), flag(ns, "remove"), &s_or(ns, "why"), amendment.as_deref())?;
    if before.as_ref() == Some(&crit.dump(false)) {
        // the same words keep the declaration as it was: nothing is written, and a design that waits still waits
        out.say(format!("{}: already declared for question {} in these words; nothing changed.", crit.id, s_or(ns, "question")));
        if let Some(line) = led.design(&crit.experiment)?.and_then(|d| d.standing_line()) {
            out.say(format!("  {line}"));
        }
    } else {
        out.say(format!("{}: {} question {}.", crit.id, if flag(ns, "remove") { "declaration retracted for" } else { "declared for" }, s_or(ns, "question")));
    }
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("criterion", crit.dump(false)));
    Ok(0)
}

pub fn cmd_criterion_add(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let mut given: Vec<(&str, Option<f64>)> = Vec::new();
    for (c, k) in [("at_most", "at_most"), ("at_least", "at_least"), ("equals", "equals")] {
        if let Some(v) = f(ns, k) {
            given.push((c, Some(v)));
        }
    }
    if flag(ns, "better") {
        given.push(("better", None));
    }
    for (c, k) in [("noninferior", "noninferior"), ("equivalent", "equivalent")] {
        if let Some(v) = f(ns, k) {
            given.push((c, Some(v)));
        }
    }
    if given.len() != 1 {
        return Err(RBError::msg(
            "E_OBJECT_INVALID",
            "A criterion needs exactly one rule: --at-most X, --at-least X, --equals X --tolerance T, --better, --noninferior MARGIN, or --equivalent MARGIN.",
        ));
    }
    let (comparator, value) = given[0];
    let target = if matches!(comparator, "at_most" | "at_least" | "equals") { value } else { None };
    let margin = if matches!(comparator, "noninferior" | "equivalent") { value } else { None };
    let over = if flag(ns, "each") {
        "each".to_string()
    } else if flag(ns, "bound") {
        "bound".to_string()
    } else {
        s(ns, "over").filter(|o| !o.is_empty()).unwrap_or_else(|| "mean".into())
    };
    let source = parse_source(s(ns, "source").as_deref(), s(ns, "quote").as_deref(), s(ns, "commit").as_deref(), s(ns, "locator").as_deref(), s(ns, "term").as_deref(), &led.root)?;
    let a = NewCriterion {
        statement: s_or(ns, "statement"),
        experiment: s_or(ns, "experiment"),
        metric: s_or(ns, "metric"),
        comparator: comparator.into(),
        target,
        tolerance: f(ns, "tolerance"),
        source,
        over,
        min_n: arg(ns, "min_n").clone(),
        noise: f(ns, "noise"),
        note: s_or(ns, "note"),
        amend: amend(ns)?,
        id: s(ns, "id"),
        subject: s_or(ns, "subject"),
        reference: s_or(ns, "reference"),
        estimand: s_or(ns, "estimand"),
        r#where: parse_pairs(&list(ns, "where"), "--where", false)?,
        subject_where: parse_pairs(&list(ns, "subject_where"), "--subject-where", false)?,
        reference_where: parse_pairs(&list(ns, "reference_where"), "--reference-where", false)?,
        margin,
        role: s(ns, "role").unwrap_or_else(|| "primary".into()),
        questions: list(ns, "question"),
        when_met: s_or(ns, "when_met"),
        why: s_or(ns, "why"),
    };
    let crit = led.add_criterion(&a)?;
    let fd = led.finding(&crit)?;
    if let Some(near) = led.metric_near(&crit)? {
        out.warn_of(&led, &near);
    }
    out.say(format!("{}: {}", crit.id, crate::view::statement_said(&crit.statement, &crit.words, &crit.created_by).unwrap_or_else(|| crit.statement.clone())));
    out.say(format!("  {} · {} · on {}", crit.rule(), crit.role, crit.experiment));
    if let Some(said) = crit.cited_ordering_said() {
        out.say(format!("  cited: {said}"));
    } else if let Some(src) = &crit.source {
        out.say(format!(
            "  cited: {} · {}",
            src.label(),
            match &src.resolved {
                Some(r) => format!("states {} (read {})", crit.target.map(assess::declared).unwrap_or_else(|| "None".into()), repr_str(&r.text)),
                None => "recorded, not checked".into(),
            }
        ));
    }
    out.say(format!("  {}", fd.says.get("timing").cloned().unwrap_or_default()));
    if fd.comparison.is_some() {
        out.say(format!("  it already reads {}: {}", fd.says.get("comparison").cloned().unwrap_or_default(), fd.says.get("rule").cloned().unwrap_or_default()));
    }
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("criterion", crit.dump(false)));
    out.data.insert("finding".into(), Value::Dict(fd.dump(false)));
    out.next = vec![led.run_hint(&crit)?];
    Ok(0)
}

/// What `--from` gives a run: its numbers, what in the file is not a number (`skipped`, named in a warning headed
/// `shown`), and from a row of a SQLite database the per-run values and the produced time its columns hold
/// (`@<column>`), with the row's references for the receipt.
pub struct FromFile {
    pub metrics: Dict,
    pub skipped: Vec<String>,
    pub shown: String,
    pub per_run: Dict,
    pub produced_at: Option<String>,
    pub row: Option<ledger::FromRow>,
}

/// The parts of `text` between the commas outside a JSON-quoted text ("lr=0.1,bs=32").
fn split_unquoted(text: &str) -> Vec<String> {
    let (mut parts, mut part, mut quoted, mut escaped) = (Vec::new(), String::new(), false, false);
    for c in text.chars() {
        match c {
            ',' if !quoted => {
                parts.push(std::mem::take(&mut part));
                continue;
            }
            '"' if !escaped => quoted = !quoted,
            _ => {}
        }
        escaped = quoted && c == '\\' && !escaped;
        part.push(c);
    }
    parts.push(part);
    parts
}

/// Whether `text` holds an '=' outside a JSON-quoted text.
fn eq_unquoted(text: &str) -> bool {
    let (mut quoted, mut escaped) = (false, false);
    text.chars().any(|c| {
        let eq = c == '=' && !quoted;
        if c == '"' && !escaped {
            quoted = !quoted;
        }
        escaped = quoted && c == '\\' && !escaped;
        eq
    })
}

/// --where a=1,b=2 --where c=3: one NAME=VALUE each, the spaces around its '=' dropped. A comma splits the pairs, except
/// inside a JSON-quoted value ("lr=0.1,bs=32") or where no NAME= follows it (name=a,b). A value holding an '=' with a
/// pair after it (overrides=lr=0.1,bs=32) reads two ways, and is refused rather than read one of them.
pub fn where_items(items: &[String]) -> Res<Vec<String>> {
    let mut out: Vec<String> = Vec::new();
    for item in items {
        let mut pairs: Vec<String> = Vec::new();
        for part in split_unquoted(item) {
            match pairs.last_mut() {
                Some(last) if !eq_unquoted(&part) => {
                    last.push(',');
                    last.push_str(&part);
                }
                _ => pairs.push(part),
            }
        }
        let pairs: Vec<String> = pairs
            .iter()
            .map(|p| match crate::ledger::partition(p, "=") {
                (k, true, v) => format!("{}={}", strip(k), strip(v)),
                (k, false, _) => strip(k).to_string(),
            })
            .filter(|p| !p.is_empty())
            .collect();
        if let Some(i) = (0..pairs.len().saturating_sub(1)).find(|i| eq_unquoted(crate::ledger::partition(&pairs[*i], "=").2)) {
            let whole = pairs[i..].join(",");
            let (k, _, v) = crate::ledger::partition(&whole, "=");
            return Err(RBError::msg_fix(
                "E_OBJECT_INVALID",
                format!("--where {item} reads two ways: {}, or {k} holding {v}.", pairs[i..].join(" and ")),
                format!(
                    "Quote a value that holds a comma, --where {}, or give each pair its own --where: {}.",
                    crate::cli::shlex_quote(&format!("{k}={}", crate::sqlite::value_said(&Value::Str(v.to_string())))),
                    pairs[i..].iter().map(|p| format!("--where {}", crate::cli::shlex_quote(p))).collect::<Vec<_>>().join(" ")
                ),
            ));
        }
        out.extend(pairs);
    }
    Ok(out)
}

/// The numbers of the file `path` names, as `rb run add --from` reads them: a JSON, YAML or TOML object (`keys`
/// narrows it), or one row of a SQLite database, picked by `table` and `where_`, whose columns that name a metric the
/// project declares are its numbers (`select` narrows them). A per-run value or a produced time given as `@<column>`
/// is that column's value in the row.
pub fn read_from(led: &Ledger, path: &str, keys: &[String], table: Option<&str>, where_: &Dict, select: &[String], per_run: Dict, produced_at: Option<String>) -> Res<FromFile> {
    let shown = pathlib_str(path);
    let file = PathBuf::from(crate::sys::typed(path));
    let table = table.filter(|t| !t.is_empty());
    // a file that is not there or cannot be read is said so, never taken for a file that is not a database
    let database = crate::sqlite::header(&file).map_err(|e| RBError::could_not_read(&e, &shown))?;
    if !database {
        if table.is_some() || !where_.is_empty() || !select.is_empty() {
            return Err(RBError::msg_fix(
                "E_OBJECT_INVALID",
                format!("--table, --where and --select read a row of a SQLite database, and {shown} is not one: its first bytes are not SQLite's."),
                "Read a JSON, YAML or TOML file without them, or give --from the database.",
            ));
        }
        let (mut nums, mut skipped) = metrics_from_file(path)?;
        if !keys.is_empty() {
            let patterns = comma_list(keys);
            nums = nums.into_iter().filter(|(k, _)| patterns.iter().any(|p| fnmatchcase(k, p))).collect();
            skipped.retain(|k| patterns.iter().any(|p| fnmatchcase(k, p)));
            if nums.is_empty() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("--keys {} matches no number in {shown}.", patterns.join(", "))));
            }
        }
        return Ok(FromFile { metrics: nums, skipped, shown, per_run, produced_at, row: None });
    }
    if !keys.is_empty() {
        return Err(RBError::msg(
            "E_OBJECT_INVALID",
            format!("--keys picks the numbers of a JSON, YAML or TOML file; {shown} is a SQLite database, whose metric columns --select picks."),
        ));
    }
    let Some(table) = table else {
        let tables = crate::sqlite::tables(&file, &shown)?;
        return Err(RBError::msg_fix(
            "E_OBJECT_INVALID",
            format!("{shown} is a SQLite database, and rb reads one row of it as a run: its tables are {}.", crate::sqlite::tables_said(&tables)),
            format!("Name the row: rb run add <experiment> --from {} --table <table> --where <column>=<value>", crate::cli::shlex_quote(&shown)),
        ));
    };
    let pairs: Vec<(String, Value)> = where_.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
    let (row, stamp) = crate::sqlite::read_row(&file, &shown, table, &pairs)?;
    let names = crate::journal::names(led)?;
    let all_columns = || row.cells.iter().map(|(c, _)| c.as_str()).collect::<Vec<_>>().join(", ");
    let declared = |c: &str| crate::journal::canon_of(c, &names).is_some();
    let mut chosen: Vec<&(String, crate::sqlite::Cell)> = Vec::new();
    if select.is_empty() {
        chosen.extend(row.cells.iter().filter(|(c, _)| declared(c)));
        if chosen.is_empty() {
            return Err(RBError::msg_fix(
                "E_OBJECT_INVALID",
                format!("No column of table {} in {shown} names a metric the project declares; its columns are {}.", row.table, all_columns()),
                "Declare the metric a column holds: rb metric add <name>, with --alias <column> when the column calls it otherwise.",
            ));
        }
    }
    for wanted in comma_list(select) {
        let Some(cell) = row.column(&wanted) else {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("--select {wanted}: table {} in {shown} has no column {wanted}; its columns are {}.", row.table, all_columns())));
        };
        if !declared(&cell.0) {
            return Err(RBError::msg_fix(
                "E_OBJECT_INVALID",
                format!("--select {wanted}: {} names no metric the project declares.", cell.0),
                format!("Declare it first: rb metric add {}, or add it as an --alias of the metric it holds.", crate::cli::shlex_quote(&cell.0)),
            ));
        }
        if !chosen.contains(&cell) {
            chosen.push(cell);
        }
    }
    let shown_row = format!("{shown}, {}", row.place());
    let mut metrics = Dict::new();
    let mut skipped = Vec::new();
    let mut columns = Vec::new();
    for (column, cell) in chosen {
        match cell.number() {
            Some(x) => {
                metrics.insert(column.clone(), Value::Float(x));
                columns.push((column.clone(), column.clone()));
            }
            None => skipped.push(format!("{column} ({})", cell.said())),
        }
    }
    if metrics.is_empty() {
        return Err(RBError::msg_fix(
            "E_OBJECT_INVALID",
            format!("{shown_row} holds no number in its metric columns: {}.", skipped.join(", ")),
            "Name a row that holds them, or --select the columns that do.",
        ));
    }
    // the row's own value where a per-run value or the produced time names a column, or a column of the one row of
    // another table that holds what the run's row holds in the column linking them: @<table>.<column>
    // (the value's name in the receipt, its cell, the row that holds it, the column there)
    let from_column = |flag: &str, name: &str| -> Res<(String, crate::sqlite::Cell, String, String)> {
        if let Some((c, cell)) = row.column(name) {
            return Ok((c.clone(), cell.clone(), shown_row.clone(), c.clone()));
        }
        match crate::sources::row_key_of(name) {
            Some((other, links, column)) => linked_cell(&file, &shown, &row, stamp, flag, &other, &links, &column),
            None => Err(RBError::msg("E_OBJECT_INVALID", format!("{flag}: table {} in {shown} has no column {name}; its columns are {}.", row.table, all_columns()))),
        }
    };
    let mut resolved = Dict::new();
    let mut per_run_columns = Vec::new();
    for (k, v) in per_run {
        let Some(name) = v.as_str().and_then(|t| t.strip_prefix('@')).map(|n| n.to_string()) else {
            resolved.insert(k, v);
            continue;
        };
        let flag = format!("--set {k}=@{name}");
        let (column, cell, holder, held_in) = from_column(&flag, &name)?;
        match cell.value().filter(|x| x.as_str().map_or(true, |t| !strip(t).is_empty())) {
            Some(x) => {
                resolved.insert(k.clone(), x);
                per_run_columns.push((k, column));
            }
            None => {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{flag}: {holder} holds {} in {held_in}, which is no per-run value.", cell.said())));
            }
        }
    }
    let mut produced_column = None;
    let produced_at = match produced_at.as_deref().and_then(|t| t.strip_prefix('@')) {
        Some(name) => {
            let flag = format!("--produced-at @{name}");
            let (column, cell, holder, held_in) = from_column(&flag, name)?;
            match &cell {
                crate::sqlite::Cell::Text(t) if ledger::norm_time(t).is_some() => {
                    produced_column = Some(column);
                    Some(t.clone())
                }
                _ => {
                    return Err(RBError::msg_fix(
                        "E_OBJECT_INVALID",
                        format!("{flag}: {holder} holds {} in {held_in}, which is not an ISO date or time.", cell.said()),
                        "Give the time as it is known (--produced-at <ISO date or time>), or --produced-at unknown.",
                    ))
                }
            }
        }
        None => produced_at,
    };
    let from = FromRow {
        path: root_relative(path, &led.root),
        table: row.table.clone(),
        key: row.key_values(),
        rowid: row.rowid,
        columns,
        per_run: per_run_columns,
        produced_at: produced_column,
        identity: row.identity(),
        stamp,
    };
    Ok(FromFile { metrics, skipped, shown: shown_row, per_run: resolved, produced_at, row: Some(from) })
}

/// A column of the one row of another table linked to the run's row (`@configs.seed`, `@configs[config_id].seed`): the
/// row of `other` whose link columns hold what the run's row holds in them. With no link named, the link is the one
/// column the two tables share by name. Said as `configs[config_id=c1].seed`, the row it was read from in its name, with
/// that row as a refusal names it (`results.db, table configs, rowid 2 (config_id=c1)`) and the column there.
fn linked_cell(
    file: &std::path::Path, shown: &str, row: &crate::sqlite::Row, stamp: crate::sqlite::Stamp, flag: &str, other: &str, links: &[String], column: &str,
) -> Res<(String, crate::sqlite::Cell, String, String)> {
    let (tables, _) = crate::sqlite::table_columns(file, shown)?;
    let Some(t) = tables.iter().find(|t| t.name == other).or_else(|| tables.iter().find(|t| t.name.eq_ignore_ascii_case(other))) else {
        let names: Vec<&str> = tables.iter().map(|t| t.name.as_str()).collect();
        return Err(RBError::msg("E_OBJECT_INVALID", format!("{flag}: {shown} has no table {other}; its tables are {}.", names.join(", "))));
    };
    let links: Vec<String> = match links.iter().flat_map(|l| l.split(',')).map(|l| strip(l).to_string()).filter(|l| !l.is_empty()).collect::<Vec<_>>() {
        named if !named.is_empty() => named,
        _ => {
            let shared: Vec<String> = row.cells.iter().map(|(c, _)| c.clone()).filter(|c| t.columns.iter().any(|o| o.eq_ignore_ascii_case(c))).collect();
            if shared.len() != 1 {
                let said = if shared.is_empty() { "no column name".to_string() } else { format!("{} column names ({})", shared.len(), shared.join(", ")) };
                return Err(RBError::msg_fix(
                    "E_OBJECT_INVALID",
                    format!("{flag}: table {} and table {} in {shown} share {said}, so rb cannot tell which row of {} is this run's.", row.table, t.name, t.name),
                    format!("Name the column that links them: @{}[<column>].{column}", t.name),
                ));
            }
            shared
        }
    };
    let mut pairs: Vec<(String, Value)> = Vec::new();
    for l in &links {
        let Some((c, cell)) = row.column(l) else {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("{flag}: table {} in {shown} has no column {l} to link it to table {}.", row.table, t.name)));
        };
        match cell.value() {
            Some(v) => pairs.push((c.clone(), v)),
            None => return Err(RBError::msg("E_OBJECT_INVALID", format!("{flag}: {shown}, {} holds {} in {c}, which links it to no row.", row.place(), cell.said()))),
        }
    }
    // no row or several: the link names the row, so the fix is another link, never --where (which picks the run's row)
    let (linked, again) = crate::sqlite::read_row(file, shown, &t.name, &pairs).map_err(|e| {
        let fix = match e.extra.get("matched") {
            Some(Value::Int(0)) => format!("No row of table {} holds what this run's row holds in {}: name the column that links them, @{}[<column>].{column}", t.name, links.join(", "), t.name),
            Some(Value::Int(_)) => format!("{} does not pick one row of table {}: name the columns that do, @{}[<column>,<column>].{column}", links.join(", "), t.name, t.name),
            _ => e.fix.clone(),
        };
        RBError::msg_fix(&e.code, format!("{flag}: {}", e.message), fix)
    })?;
    if again != stamp {
        return Err(RBError::msg_fix("E_OBJECT_INVALID", format!("{shown} changed while rb read it."), "Read it again."));
    }
    let Some((c, cell)) = linked.column(column) else {
        let columns: Vec<&str> = linked.cells.iter().map(|(c, _)| c.as_str()).collect();
        return Err(RBError::msg("E_OBJECT_INVALID", format!("{flag}: table {} in {shown} has no column {column}; its columns are {}.", linked.table, columns.join(", "))));
    };
    let by = crate::sqlite::pairs_said(&linked.key_values());
    Ok((format!("{}[{by}].{c}", linked.table), cell.clone(), format!("{shown}, {} ({by})", linked.place()), c.clone()))
}

/// Where each value of a run read from a row came from: int8.acc1 from column acc1, seed from column seed, produced at
/// from column finished_at.
pub fn row_columns(row: &RowRead) -> Vec<String> {
    let mut out: Vec<String> = row.columns.iter().chain(row.per_run.iter()).map(|(k, c)| format!("{k} from column {c}")).collect();
    out.extend(row.produced_at.iter().map(|c| format!("produced at from column {c}")));
    out
}

/// The row a run was read from, as a line says it: read from results.db, table evals, rowid 3 (precision=int8, seed=1).
pub fn row_words(row: &RowRead) -> String {
    let key = crate::sqlite::pairs_said(&row.r#where);
    format!(
        "read from {}, table {}{}{}",
        row.path,
        row.table,
        row.rowid.map(|r| format!(", rowid {r}")).unwrap_or_default(),
        if key.is_empty() { String::new() } else { format!(" ({key})") }
    )
}

pub fn cmd_run_add(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let tokens = list(ns, "pairs");
    let pairs = split_pairs(&tokens);
    let bad: Vec<&String> = tokens.iter().filter(|t| !t.contains('=')).collect();
    if !bad.is_empty() {
        return Err(RBError::msg("E_USAGE", format!("Numbers are NAME=VALUE (got {}).", bad.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", "))));
    }
    let mut metrics = parse_pairs(&pairs, "metric", true)?;
    let links = parse_pairs(&list(ns, "link"), "--link", false)?;
    let mut per_run = parse_pairs(&list(ns, "set"), "--set", false)?;
    let host: Dict = parse_pairs(&list(ns, "host"), "--host", false)?.into_iter().map(|(k, v)| (k, Value::Str(pyjson::py_str(&v)))).collect();
    let mut files: Vec<String> = list(ns, "artifact").iter().map(|f| root_relative(f, &led.root)).collect();
    let mut numbers_from: Vec<String> = Vec::new();
    let mut basis = "typed".to_string();
    let mut produced_at = s(ns, "produced_at");
    let mut row = None;
    let keys = list(ns, "keys");
    let (table, select) = (s(ns, "table"), list(ns, "select"));
    let where_ = parse_pairs(&where_items(&list(ns, "where"))?, "--where", false)?;
    let from_file = s(ns, "from_file");
    if !keys.is_empty() && from_file.is_none() {
        return Err(RBError::msg("E_OBJECT_INVALID", "--keys chooses which numbers --from takes; give --from <file>."));
    }
    if (table.is_some() || !where_.is_empty() || !select.is_empty()) && from_file.is_none() {
        return Err(RBError::msg("E_OBJECT_INVALID", "--table, --where and --select name a row of the SQLite database --from reads; give --from <database>."));
    }
    if let Some(ff) = &from_file {
        let got = read_from(&led, ff, &keys, table.as_deref(), &where_, &select, per_run, produced_at)?;
        let mut overlap: Vec<&String> = got.metrics.keys().filter(|k| metrics.contains_key(*k)).collect();
        overlap.sort();
        if !overlap.is_empty() {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("NAME=VALUE and --from both give {}.", overlap.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", "))));
        }
        for (k, v) in got.metrics {
            metrics.insert(k, v);
        }
        (per_run, produced_at, row) = (got.per_run, got.produced_at, got.row);
        files.push(root_relative(ff, &led.root));
        numbers_from.push(root_relative(ff, &led.root));
        basis = if pairs.is_empty() { "file".into() } else { "typed".into() };
        // what the file holds that is not a number is a note after the run, never a line said as a failure is
        if !got.skipped.is_empty() {
            out.notes.push(format!(
                "{}: not numbers, so not metrics: {}",
                got.shown,
                got.skipped.iter().map(|k| if k.is_empty() { "(top level)".to_string() } else { k.clone() }).collect::<Vec<_>>().join(", ")
            ));
        }
    }
    let config = s(ns, "config").filter(|c| !c.is_empty()).map(|c| root_relative(&c, &led.root));
    let experiment = s_or(ns, "experiment");
    let result: Added<Run> = led.add_run(
        &experiment,
        NewRun {
            metrics: metrics.into_iter().collect(),
            variant: s(ns, "variant"),
            files,
            links: links.into_iter().map(|(k, v)| (k, pyjson::py_str(&v))).collect(),
            command: s(ns, "command_text"),
            command_source: s(ns, "command_source"),
            per_run,
            config,
            commit: s(ns, "commit"),
            basis,
            again: again(ns)?,
            note: s_or(ns, "note"),
            inputs: parse_uses(&list(ns, "input"), "--input")?,
            outputs: parse_uses(&list(ns, "output"), "--output")?,
            host: host.into_iter().map(|(k, v)| (k, pyjson::py_str(&v))).collect(),
            repeats: s(ns, "repeats"),
            produced_at,
            replaces: s(ns, "replaces"),
            why: s_or(ns, "why"),
            tree: s(ns, "tree"),
            numbers_from,
            row,
        },
    )?;
    let run = result.record;
    if result.duplicate_of.is_some() {
        let by_output = !run.files.is_empty();
        let same_row = run.receipt.row.as_ref().map(|r| format!("the same row of {}", r.path));
        let why_same = match &same_row {
            Some(w) => w.as_str(),
            None if by_output => "the same output files",
            None => "the same numbers, per-run values and command",
        };
        // the run that holds it was recorded under an earlier spec: its warning says how it is read again under this one
        let earlier = led.load::<Experiment>(&run.experiment).map_or(false, |e| led.spec_sha(&e) != run.spec_sha256);
        out.say(format!(
            "Already recorded as {} ({why_same}); nothing written.{}",
            run.id,
            if earlier {
                String::new()
            } else if same_row.is_some() {
                " One row is one run; a second execution that wrote the same row is recorded with --again --why \"...\".".to_string()
            } else if by_output {
                " The same output is one run; a second execution that wrote byte-identical output is recorded with --again --why \"...\".".to_string()
            } else {
                format!(" A second execution is recorded with --repeats {} when it checks that one, or with --again --why \"...\".", run.id)
            }
        ));
        if let Some(w) = &result.warning {
            out.warn(w);
        }
        actor_line(out, &led)?;
        out.data.insert("object".into(), obj("run", run.dump(false)));
        out.data.insert("duplicate_of".into(), Value::Str(run.id.clone()));
        return Ok(0);
    }
    let mut sorted: Vec<(&String, &f64)> = run.metrics.iter().collect();
    sorted.sort_by(|a, b| a.0.cmp(b.0));
    out.say(format!(
        "{} on {}: {}{}",
        run.id,
        run.experiment,
        sorted.iter().map(|(k, v)| format!("{k}={}", g(**v))).collect::<Vec<_>>().join(", "),
        if run.per_run.is_empty() { String::new() } else { format!(" · {}", run.per_run.iter().map(|(k, v)| format!("{k}={}", show(v))).collect::<Vec<_>>().join(", ")) }
    ));
    if let Some(w) = &result.warning {
        out.warn(w);
    }
    if let Some(r) = &run.receipt.row {
        out.say(format!("  {}", row_words(r)));
    }
    if !run.inputs.is_empty() || !run.outputs.is_empty() {
        let mut parts: Vec<String> = run.inputs.iter().map(|u| format!("read {}{}", u.resource, if u.role.is_empty() { String::new() } else { format!(" ({})", u.role) })).collect();
        parts.extend(run.outputs.iter().map(|u| format!("made {}{}", u.resource, if u.role.is_empty() { String::new() } else { format!(" ({})", u.role) })));
        out.say(format!("  {}", parts.join(" · ")));
    }
    if let Some(cfg) = &run.receipt.config {
        if !cfg.mismatches.is_empty() {
            out.say(format!(
                "  Ran with a different spec: {}. It will not count.",
                cfg.mismatches.iter().map(|m| format!("{} {} (spec {})", get_s(m, "name"), show(m.get("ran").unwrap_or(&Value::Null)), show(m.get("spec").unwrap_or(&Value::Null)))).collect::<Vec<_>>().join("; ")
            ));
        } else if !cfg.matches.is_empty() {
            out.say(format!(
                "  Ran with the spec: {} of its settings found in {}, {}{}",
                cfg.matches.len(),
                cfg.path,
                if cfg.matches.len() == 1 { "matching" } else { "all matching" },
                if cfg.absent.is_empty() { ".".to_string() } else { format!(" ({} not mentioned there).", cfg.absent.len()) }
            ));
        } else {
            out.say(format!("  {} mentions none of the spec's settings, so nothing was compared: the run's config is unchecked.", cfg.path));
        }
    }
    match &run.receipt.attached {
        None => out.say("  Not in a git repository: the receipt has no commit."),
        Some(git) if git.is_empty() => out.say("  Not in a git repository: the receipt has no commit."),
        Some(git) => {
            if git.get("commit").map_or(true, |v| v.is_null()) {
                out.say("  The repository has no commit yet; the receipt records the uncommitted tree's hash.");
            } else if git.get("dirty").map_or(false, |v| v.truthy()) {
                out.say(format!("  The tree had uncommitted changes when recorded (hash {}).", trunc(&get_s(git, "diff_sha256"), 12)));
                let watched = crate::ledger::support::run_command(&run).map_or(false, |g| g.given == "observed");
                out.say_all(crate::cmds::exact_code_at(&led.root, git, crate::ledger::made_before_recorded(&run), watched));
            }
        }
    }
    if let Some(r) = run.repeats.as_ref().filter(|r| !r.is_empty()) {
        out.say(format!("  It repeats {r}: a check of that run, in its unit, never a new one."));
    }
    if let Some(r) = run.replaces.as_ref().filter(|r| !r.is_empty()) {
        out.say(format!("  It replaces {r}, which is retracted and stays on record: rb show {r}"));
    }
    if run.produced_basis != "recorded" {
        let column = run.receipt.row.as_ref().and_then(|r| r.produced_at.as_ref()).map(|c| format!(", column {c}")).unwrap_or_default();
        out.say(format!(
            "  Produced: {}",
            match run.produced_at.as_ref().filter(|p| !p.is_empty()) {
                Some(p) => format!("{} ({}{column})", trunc(p, 19).replace('T', " "), run.produced_basis),
                None if run.receipt.row.is_some() && s(ns, "produced_at").is_none() => {
                    "when is unknown; a database's times are not its row's. --produced-at @<column> reads a time the row holds.".into()
                }
                None => "when is unknown".into(),
            }
        ));
    }
    // a verified setting it is recorded under, read from a file made after it, is said at the write as on the finding
    out.notes.extend(postdates_run(&led, &run.experiment, &run.id)?);
    let criteria: Vec<Criterion> = led.live::<Criterion>()?.into_iter().filter(|c| c.experiment == run.experiment).collect();
    let mut findings = Vec::new();
    for c in &criteria {
        findings.push(led.finding(c)?);
    }
    for fd in &findings {
        out.say(format!("  {} {}: {}", fd.criterion, fd.says.get("headline").cloned().unwrap_or_default(), fd.says.get("comparison").cloned().unwrap_or_default()));
    }
    actor_line(out, &led)?;
    let exp: Experiment = led.load(&run.experiment)?;
    let unread = led.unread_metrics(&exp, std::slice::from_ref(&run), &criteria)?;
    if let Some(w) = nothing_counts(&led, &run, if from_file.is_some() { Shape::File } else { Shape::Typed })? {
        out.warn(&w);
    }
    out.data.insert("object".into(), obj("run", run.dump(false)));
    out.data.insert("findings".into(), Value::List(findings.iter().map(|f| Value::Dict(f.dump(false))).collect()));
    out.data.insert("unread_metrics".into(), Value::list_of_str(unread));
    let mut names: Vec<&String> = run.metrics.keys().collect();
    names.sort_by(|a, b| (!a.starts_with("change."), *a).cmp(&(!b.starts_with("change."), *b)));
    let pick = names.first().map(|s| s.to_string()).unwrap_or_default();
    out.next = findings.iter().take(3).map(|f| format!("rb show {}", f.criterion)).collect();
    if out.next.is_empty() {
        out.next = vec![format!("rb criterion add \"<what it should show>\" -e {}{} --metric {pick} --at-most <x>   (write the criterion before the next run)", run.experiment, led.question_arg(&exp))];
    }
    Ok(0)
}

/// How the numbers of a run were given, so its notice says how to give them: typed (NAME=VALUE), a file (--from) or the
/// SDK's run.log.
#[derive(Clone, Copy, PartialEq)]
pub enum Shape {
    Typed,
    File,
    Sdk,
}

/// The notice a new run gets when no criterion of its experiment reads any of its numbers, wherever it was recorded
/// (rb run add, the SDK, MCP): None while a criterion reads one, or none is declared.
pub fn nothing_counts(led: &Ledger, run: &Run, shape: Shape) -> Res<Option<String>> {
    let criteria: Vec<Criterion> = led.live::<Criterion>()?.into_iter().filter(|c| c.experiment == run.experiment).collect();
    if criteria.is_empty() || run.metrics.is_empty() {
        return Ok(None);
    }
    let exp: Experiment = led.load(&run.experiment)?;
    let unread = led.unread_metrics(&exp, std::slice::from_ref(run), &criteria)?;
    if unread.len() != run.metrics.len() {
        return Ok(None);
    }
    Ok(Some(counts_toward_nothing(led, &exp, &unread, &criteria, shape)?))
}

/// One line for a run none of whose numbers a criterion of its experiment reads: the names its criteria read, in the
/// shape a run gives them (a variant's numbers, never the change between them, which rb computes).
fn counts_toward_nothing(led: &Ledger, exp: &Experiment, unread: &[String], criteria: &[Criterion], shape_of: Shape) -> Res<String> {
    let read = led.read_metrics(exp, criteria)?;
    let given: Vec<&String> = read.iter().filter(|m| !m.starts_with("change.")).collect();
    let wanted: Vec<&String> = if given.is_empty() { read.iter().collect() } else { given };
    let shown = &wanted[..wanted.len().min(4)];
    let shape = if shape_of == Shape::Sdk {
        format!("a run logs them as run.log({{{}}})", shown.iter().map(|m| format!("\"{m}\": ...")).collect::<Vec<_>>().join(", "))
    } else if shape_of == Shape::File {
        let mut nested: indexmap::IndexMap<String, Vec<String>> = indexmap::IndexMap::new();
        let mut flat: Vec<String> = Vec::new();
        for m in shown {
            match m.split_once('.') {
                Some((v, name)) if exp.variant(v).is_some() => nested.entry(v.to_string()).or_default().push(format!("\"{name}\": ...")),
                _ => flat.push(format!("\"{m}\": ...")),
            }
        }
        let parts: Vec<String> = nested.into_iter().map(|(v, names)| format!("\"{v}\": {{{}}}", names.join(", "))).chain(flat).collect();
        format!("a file gives them as {{{}}}", parts.join(", "))
    } else {
        format!("a run gives them as {}", shown.iter().map(|m| format!("{m}=<value>")).collect::<Vec<_>>().join(" "))
    };
    let one_arm = exp.live_variants().len() > 1 && unread.iter().any(|k| !k.contains('.'));
    let one_variant = if shape_of == Shape::Sdk { ", or variant=<name> takes one variant's numbers" } else { ", or --variant <name> takes one variant's numbers" };
    let said = |names: &[&String]| crate::assess::first_few(&names.iter().map(|s| s.to_string()).collect::<Vec<_>>(), 4, ", ");
    // a criterion on a name close to one this run gives is most likely misspelt: said so, never given as the name to use
    let leaves: Vec<&str> = unread.iter().map(|k| crate::ledger::last_part(k)).collect();
    for c in criteria {
        let base = crate::ledger::last_part(&c.metric);
        if leaves.contains(&base) || led.catalogue_entry(base)?.is_some() {
            continue;
        }
        if let Some(near) = crate::ledger::near_name(base, leaves.iter().copied()) {
            return Ok(format!(
                "{}: no criterion reads {}, so this run counts toward nothing. {} reads {base}, a close name to the {near} this run gives: if it means {near}, take it back (rb retract {} --why \"...\") and declare it again on {near}.",
                exp.id,
                said(&unread.iter().collect::<Vec<_>>()),
                c.id,
                c.id
            ));
        }
    }
    Ok(format!(
        "{}: no criterion reads {}, so this run counts toward nothing. Its criteria read {}: {shape}{}.",
        exp.id,
        said(&unread.iter().collect::<Vec<_>>()),
        said(&wanted),
        if one_arm { one_variant } else { "" }
    ))
}

pub fn cmd_resource_add(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let mut locations = Vec::new();
    for at in list(ns, "at") {
        let (provider, sep, r) = ledger::partition(&at, ":");
        if !sep || !PROVIDERS.contains(&provider) || r.is_empty() {
            return Err(RBError::msg(
                "E_OBJECT_INVALID",
                format!(
                    "--at is PROVIDER:REF with a provider of {} (got {}), e.g. --at s3:s3://bucket/imagenet or --at wandb:team/proj/imagenet:v3",
                    PROVIDERS.join(", "),
                    repr_str(&at)
                ),
            ));
        }
        locations.push((provider.to_string(), r.to_string()));
    }
    let res = led.add_resource(&NewResource {
        id: s_or(ns, "id"),
        r#type: s_or(ns, "type"),
        name: s_or(ns, "name"),
        version: s_or(ns, "version"),
        subset: s_or(ns, "subset"),
        sha256: s(ns, "sha256"),
        digest: s_or(ns, "digest"),
        source: s_or(ns, "source"),
        url: s(ns, "url"),
        recipe: s(ns, "recipe"),
        locations,
        from_path: s(ns, "file"),
        note: s_or(ns, "note"),
    })?;
    let av = led.availability(&res);
    out.say(format!(
        "Resource {}: {}{} ({}) · {}{}",
        res.id,
        res.name,
        if res.version.is_empty() { String::new() } else { format!(" {}", res.version) },
        res.r#type,
        get_s(&av, "state"),
        res.sha256.as_ref().filter(|x| !x.is_empty()).map(|x| format!(" · sha256 {}", trunc(x, 12))).unwrap_or_default()
    ));
    for loc in av.get("locations").and_then(|v| v.as_list()).cloned().unwrap_or_default() {
        out.say(format!("  {}:{} · {}", loc.get_str("provider").unwrap_or(""), loc.get_str("ref").unwrap_or(""), loc.get_str("state").unwrap_or("")));
    }
    let state = get_s(&av, "state");
    if state == "reconstructible" || state == "lost" {
        let how = get_s(&av, "how");
        out.say(format!(
            "  No copy is recorded anywhere; {}",
            if how.is_empty() { "record where it comes from with --source, --url or --recipe".to_string() } else { format!("how to get it again: {how}") }
        ));
    }
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("resource", res.dump(false)));
    out.data.insert("availability".into(), Value::Dict(av));
    out.next = vec![format!("rb run add <experiment> ... --input {}", res.id)];
    Ok(0)
}

pub fn cmd_resource_locate(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let provider = s_or(ns, "provider");
    let res = led.locate_resource(&s_or(ns, "id"), &provider, &s_or(ns, "ref"), &s_or(ns, "note"))?;
    let av = led.availability(&res);
    out.say(format!("{} is at {provider}:{} · {} now", res.id, res.locations.last().map(|l| l.r#ref.clone()).unwrap_or_default(), get_s(&av, "state")));
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("resource", res.dump(false)));
    out.data.insert("availability".into(), Value::Dict(av));
    Ok(0)
}

pub fn cmd_resource_expose(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let res = led.expose_resource(&s_or(ns, "id"), &s_or(ns, "how"), &s_or(ns, "why"), s(ns, "at").as_deref())?;
    let e = res.exposures.last().cloned().unwrap();
    let when = match e.at.as_ref().filter(|a| !a.is_empty()) {
        Some(a) => format!("at {}", trunc(a, 19).replace('T', " ")),
        None => "at a time nobody recorded".into(),
    };
    let has_at = e.at.as_ref().map_or(false, |a| !a.is_empty());
    out.say(format!(
        "{} {}{} {when}{}{}",
        res.id,
        e.how,
        crate::actor::by_writer(&e.by),
        if e.reported && has_at { " (as reported)" } else { "" },
        if e.why.is_empty() { String::new() } else { format!(": {}", e.why) }
    ));
    out.say(if has_at {
        "  A criterion declared after this, on evidence that reads it, says it was declared after the data was seen."
    } else {
        "  A criterion on evidence that reads it cannot be shown to come before this: its order reads unknown."
    });
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("resource", res.dump(false)));
    out.data.insert("exposure".into(), Value::Dict(e.dump(false)));
    Ok(0)
}

/// One comparison an analysis produced: SUBJECT[/REFERENCE]:METRIC[@ESTIMAND]=ESTIMATE[LO,HI].
pub fn parse_compare(text: &str) -> Res<Dict> {
    let t = strip(text).replace('\u{2212}', "-");
    let re = crate::model::regex_for(
        r"^\s*(?:([^:/=\[\]]+?)(?:\s*(?:/|\bvs\b)\s*([^:=\[\]]+?))?\s*:)?\s*([^=@\s]+)(?:@([^=\s]+))?\s*=\s*([-+]?[0-9.eE+-]+)\s*(?:\[\s*([-+]?[0-9.eE+-]+)\s*,\s*([-+]?[0-9.eE+-]+)\s*\])?\s*$",
    );
    let Some(m) = re.captures(&t) else {
        return Err(RBError::msg(
            "E_OBJECT_INVALID",
            format!("--compare is SUBJECT/REFERENCE:METRIC=ESTIMATE[LO,HI], e.g. new/old:score=0.12[0.07,0.17] (got {}).", repr_str(text)),
        ));
    };
    let grp = |i: usize| m.get(i).map(|x| x.as_str().to_string());
    let num = |x: &str| crate::pyfloat::parse_float(x);
    let bad = || RBError::msg("E_OBJECT_INVALID", format!("--compare {}: the numbers do not parse.", repr_str(text)));
    let est = num(&grp(5).unwrap()).ok_or_else(bad)?;
    let lo = match grp(6) {
        Some(x) => Some(num(&x).ok_or_else(bad)?),
        None => None,
    };
    let hi = match grp(7) {
        Some(x) => Some(num(&x).ok_or_else(bad)?),
        None => None,
    };
    Ok(crate::dict! {
        "subject" => strip(&grp(1).unwrap_or_default()).to_string(), "reference" => strip(&grp(2).unwrap_or_default()).to_string(),
        "metric" => grp(3).unwrap_or_default(), "estimand" => grp(4).unwrap_or_default(), "estimate" => est,
        "lo" => Value::opt_f64(lo), "hi" => Value::opt_f64(hi)
    })
}

/// The comparisons in an analysis's output file.
pub fn comparisons_from_file(path: &str) -> Res<Vec<Dict>> {
    let shown = pathlib_str(path);
    let kind = sources::structured_kind(&shown).unwrap_or("json");
    let text = read_text(Path::new(path), &shown)?;
    let text = text.strip_prefix('\u{feff}').map(|s| s.to_string()).unwrap_or(text);
    let doc = sources::parse_structured(&text, kind, &shown).map_err(|u| RBError::msg("E_OBJECT_INVALID", u.reason))?;
    let items = match &doc {
        Value::Dict(d) => d.get("comparisons").cloned().unwrap_or(Value::Null),
        other => other.clone(),
    };
    let ok = matches!(&items, Value::List(l) if !l.is_empty() && l.iter().all(|x| x.is_dict()));
    if !ok {
        return Err(RBError::msg(
            "E_OBJECT_INVALID",
            format!("{shown} lists no comparisons: a list of objects with metric and estimate (and subject, reference, lo, hi, n, unit)."),
        ));
    }
    let keep = ["subject", "reference", "metric", "estimand", "estimate", "lo", "hi", "level", "unit", "n", "note"];
    let mut out = Vec::new();
    for x in items.as_list().unwrap() {
        let d = x.as_dict().unwrap();
        if !d.contains_key("metric") || !d.contains_key("estimate") {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("{shown}: every comparison has a metric and an estimate.")));
        }
        out.push(keep.iter().filter_map(|k| d.get(*k).filter(|v| !v.is_null()).map(|v| (k.to_string(), v.clone()))).collect());
    }
    Ok(out)
}

pub fn cmd_analysis_add(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let mut comps: Vec<Dict> = Vec::new();
    for c in list(ns, "compare") {
        comps.push(parse_compare(&c)?);
    }
    let mut files: Vec<String> = list(ns, "artifact").iter().map(|f| root_relative(f, &led.root)).collect();
    let mut computed = false;
    let mut read_notes: Vec<(&'static str, String)> = Vec::new();
    if let Some(ff) = s(ns, "from_file") {
        let read = comparisons_from_file(&ff)?;
        read_notes = read.iter().filter_map(|c| c.get("note").and_then(Value::as_str)).map(|n| ("note", n.to_string())).collect();
        comps.extend(read);
        files.push(root_relative(&ff, &led.root));
        computed = list(ns, "compare").is_empty();
    }
    let source = parse_source(s(ns, "source").as_deref(), s(ns, "quote").as_deref(), s(ns, "commit").as_deref(), s(ns, "locator").as_deref(), None, &led.root)?;
    let reads = comma_list(&list(ns, "reads"));
    let supersedes = comma_list(&list(ns, "supersedes"));
    let experiment = s_or(ns, "experiment");
    let res = led.add_analysis(
        &experiment,
        NewAnalysis {
            comparisons: comps,
            title: s_or(ns, "title"),
            reads,
            inputs: parse_uses(&list(ns, "input"), "--input")?,
            files,
            command: s(ns, "command_text"),
            method: s_or(ns, "method"),
            unit: s_or(ns, "unit"),
            units: ns.get("units").filter(|v| !v.is_null()).cloned(),
            draws: ns.get("draws").filter(|v| !v.is_null()).cloned(),
            level: ns.get("level").filter(|v| !v.is_null()).cloned(),
            source,
            computed,
            supersedes,
            why: s_or(ns, "why"),
            produced_at: s(ns, "produced_at"),
            note: s_or(ns, "note"),
            id: s(ns, "id"),
        },
    )?;
    // a comparison's note from the output file is the file's, which rb hashed: no line asks to reword it
    crate::plain::copied(&read_notes);
    let a = res.record;
    if res.duplicate_of.is_some() {
        out.say(format!("Already recorded as {} (the same comparisons, files and reads); nothing written.", a.id));
        out.data.insert("object".into(), obj("analysis", a.dump(false)));
        out.data.insert("duplicate_of".into(), Value::Str(a.id.clone()));
        return Ok(0);
    }
    let n = a.comparisons.len();
    out.say(format!(
        "{} on {}: {n} comparison{} ({}){}{}",
        a.id,
        a.experiment,
        if n == 1 { "" } else { "s" },
        a.basis,
        if a.method.is_empty() { String::new() } else { format!(" · {}", a.method) },
        match a.units {
            Some(u) if u != 0 && !a.unit.is_empty() => format!(" · {u} {}", assess::plural(&a.unit, u)),
            _ => String::new(),
        }
    ));
    for c in &a.comparisons {
        // a level is one arm's number, so it has no sign; each as the rule of a criterion reading it says it
        let [est, lo, hi] = led.comparison_said(&a.experiment, c)?;
        let iv = match (c.lo, c.hi) {
            (Some(_), h) => format!(" [{lo}, {}]", if h.is_some() { hi } else { "None".into() }),
            _ => " (no interval)".into(),
        };
        let arms = if !c.reference.is_empty() {
            format!("{} vs {}", c.subject, c.reference)
        } else if !c.subject.is_empty() {
            c.subject.clone()
        } else {
            "whole run".into()
        };
        out.say(format!(
            "  {}: {arms}{}: {}{iv}{}",
            c.metric,
            if c.estimand.is_empty() { String::new() } else { format!(" ({})", c.estimand) },
            est,
            c.source.as_ref().map(|s| format!(" · stated in {}", s.label())).unwrap_or_default()
        ));
    }
    if !a.supersedes.is_empty() {
        out.say(format!("  supersedes {}{}. Their readings stay as history.", a.supersedes.join(", "), crate::view::reason_part(&a.why, &a.created_by, ": ")));
    }
    if let Some(w) = &res.warning {
        out.warn(w);
    }
    let mut read_here = Vec::new();
    for c in led.live::<Criterion>()? {
        if c.experiment == a.experiment {
            let fd = led.finding(&c)?;
            if fd.comparison.as_ref().map_or(false, |x| x.source == a.id) {
                read_here.push(fd);
            }
        }
    }
    for fd in &read_here {
        out.say(format!("  {} {}: {}", fd.criterion, fd.says.get("headline").cloned().unwrap_or_default(), fd.says.get("comparison").cloned().unwrap_or_default()));
    }
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("analysis", a.dump(false)));
    out.data.insert("findings".into(), Value::List(read_here.iter().map(|f| Value::Dict(f.dump(false))).collect()));
    out.next = read_here.iter().take(3).map(|f| format!("rb show {}", f.criterion)).collect();
    if out.next.is_empty() {
        let c0 = &a.comparisons[0];
        out.next = vec![format!(
            "rb criterion add \"<what it should show>\" -e {}{} --metric {} --subject {}{} --better",
            a.experiment,
            led.load::<Experiment>(&a.experiment).map_or_else(|_| " -q <question>".to_string(), |e| led.question_arg(&e)),
            c0.metric,
            if c0.subject.is_empty() { "<arm>".to_string() } else { c0.subject.clone() },
            if c0.reference.is_empty() { String::new() } else { format!(" --reference {}", c0.reference) }
        )];
    }
    Ok(0)
}

pub fn cmd_retract(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let why = s(ns, "why").filter(|w| !w.is_empty()).or_else(|| s(ns, "reason").filter(|w| !w.is_empty()));
    let Some(why) = why else {
        return Err(RBError::msg("E_USAGE", "rb retract <id> --why \"<reason>\""));
    };
    let scope = comma_list(&list(ns, "scope"));
    let subject = s_or(ns, "subject");
    let (head, _) = ledger::parse_address(&subject);
    let kind0 = led.kind_of(&head)?;
    let exp_id: Option<String> = match kind0 {
        Some("run") => Some(led.load::<Run>(&subject)?.experiment),
        Some("criterion") => Some(led.load::<Criterion>(&subject)?.experiment),
        Some("experiment") => Some(head.clone()),
        _ => None,
    };
    let mut before: indexmap::IndexMap<String, Finding> = indexmap::IndexMap::new();
    if let Some(e) = &exp_id {
        for c in led.live::<Criterion>()? {
            if c.experiment == *e {
                before.insert(c.id.clone(), led.finding(&c)?);
            }
        }
    }
    let (kind, sid, deps) = led.retract(&subject, &why, if scope.is_empty() { None } else { Some(&scope) })?;
    let who = led.actor()?.id;
    let by = crate::actor::by_writer(&who);
    if !scope.is_empty() {
        out.say(format!("{sid} ({kind}) superseded for {}{by}{}.", scope.join(", "), crate::view::reason_part(&why, &who, ": ")));
        out.say("Those measurements no longer count; the rest of this run still does, and both facts stay on the record.");
    } else {
        out.say(format!("{sid} ({kind}) retracted{by}{}. It stays on record and in the log, and no longer counts.", crate::view::reason_with_words(&why, &led.words, &who, ": ")));
    }
    if !deps.is_empty() {
        out.say(format!("  it rested on: {}", deps.join(", ")));
    }
    let mut changes: Vec<Value> = Vec::new();
    for c in led.live::<Criterion>()? {
        if let Some(b) = before.get(&c.id) {
            let after = led.finding(&c)?;
            let bs = |k: &str| b.says.get(k).cloned();
            let as_ = |k: &str| after.says.get(k).cloned();
            // taking back a run the criterion could have been fitted to can move the timing of a reading alone
            let moved = b.timing != after.timing && b.comparison.is_some() && after.comparison.is_some();
            let timing = |f: &Finding| if moved { format!(" · {}", crate::assess::timing_said(f)) } else { String::new() };
            if bs("comparison") != as_("comparison") || bs("headline") != as_("headline") || moved {
                let was = format!("{}: {}{}", bs("headline").unwrap_or_default(), bs("comparison").unwrap_or_default(), timing(b));
                let now_ = format!("{}: {}{}", as_("headline").unwrap_or_default(), as_("comparison").unwrap_or_default(), timing(&after));
                out.say(format!("  {}: {was}", c.id));
                out.say(format!("      now {now_}"));
                changes.push(crate::obj! {"criterion_id" => c.id.clone(), "before" => was, "after" => now_});
            }
        }
    }
    actor_line(out, &led)?;
    out.data.insert("object".into(), crate::obj! {"kind" => kind, "id" => sid, "retracted" => crate::obj! {"why" => why, "scope" => Value::list_of_str(scope)}});
    out.data.insert("rested_on".into(), Value::list_of_str(deps));
    out.data.insert("finding_changes".into(), Value::List(changes));
    Ok(0)
}

pub fn cmd_freeze(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let amend_ = flag(ns, "amend");
    let why = s_or(ns, "why");
    if amend_ && why.is_empty() {
        return Err(RBError::msg("E_OBJECT_INVALID", "rb freeze --amend needs --why \"<reason>\": the reason is what the amendment records."));
    }
    let eid = s_or(ns, "experiment");
    let fr = led.freeze(&eid, &why, amend_)?;
    let exp: Experiment = led.load(&eid)?;
    if amend_ {
        let am = exp.amendments.last().unwrap();
        out.say(format!("{} amended: the freeze record now matches it as it is ({} -> {}) · {}", exp.id, trunc(&am.before, 12), trunc(&am.after, 12), am.why));
    } else {
        out.say(format!(
            "{} frozen{}: spec {}{}{}",
            exp.id,
            crate::actor::by_writer(&fr.by),
            trunc(&fr.sha256, 12),
            if fr.why.is_empty() { String::new() } else { format!(" · {}", fr.why) },
            words(&fr.words, &fr.by)
        ));
    }
    out.say_all(freeze_review(&led, &exp)?);
    if !amend_ {
        out.say("  Changing a setting or its source, a variant, what it varies, or a criterion now needs --amend --why \"<reason>\"; the amendment is kept with who made it.");
        if !fr.after_runs.is_empty() {
            out.say(format!(
                "  {} already has runs ({}): each criterion's timing says which evidence existed before it was declared.",
                exp.id,
                fr.after_runs.join(", ")
            ));
        }
    }
    let unknown: Vec<String> = exp.all_settings().into_iter().filter(|(_, s)| s.value.is_null() && s.required && !s.per_run).map(|(n, _)| n).collect();
    if !unknown.is_empty() {
        out.say(format!(
            "  Required settings still unknown: {}. Every finding on it lists them as integrity problems until they are known.",
            unknown.join(", ")
        ));
    }
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("experiment", exp.dump(false)));
    out.data.insert("unknown_required".into(), Value::list_of_str(unknown));
    out.next = vec![format!("rb run add {} --from <metrics.json> --command \"...\"", exp.id)];
    Ok(0)
}

/// How a setting's source names it, when not by the setting's own name.
fn named_otherwise(name: &str, st: &Setting) -> Option<String> {
    let src = st.source.as_ref()?;
    if st.status != "verified" {
        return None;
    }
    if let Some(t) = src.term.as_ref().filter(|t| !t.is_empty()) {
        return Some(format!("matched by the words {}", repr_str(t)));
    }
    let key = src.key.clone().filter(|k| !k.is_empty()).or_else(|| if src.kind == "run" { src.pointer.clone().filter(|p| !p.is_empty()) } else { None })?;
    let steps = sources::key_steps(&key);
    if steps.last().map(|s| s.to_lowercase()) != Some(ledger::last_part(name).to_lowercase()) {
        return Some(format!("read from {key}"));
    }
    None
}

/// What freezing an experiment locks, the way anyone should read it before they do.
pub fn freeze_review(led: &Ledger, exp: &Experiment) -> Res<Vec<String>> {
    let mut lines = vec![
        format!("  {}: {}", exp.id, exp.title),
        format!(
            "    variants: {}{}",
            exp.live_variants().iter().map(|v| format!("{} ({})", v.name, v.role)).collect::<Vec<_>>().join(", "),
            if exp.varies.is_empty() { " · varies nothing declared".to_string() } else { format!(" · varies {}", exp.varies.join(", ")) }
        ),
    ];
    let mut look = Vec::new();
    for c in led.criteria_of(&exp.id)?.into_iter().filter(|c| c.retracted.is_none()) {
        let said = match crate::actor::shown_writer(&c.created_by) {
            Some(name) => format!("   (declared by {name}{})", words(&c.words, &c.created_by)),
            None if !c.words.is_empty() => format!("   ({})", crate::view::your_words(&c.words, &c.created_by)),
            None => String::new(),
        };
        lines.push(format!("    criterion {} ({}): {}{said}", c.id, c.role, c.rule()));
        // a rule no human declared is put to whoever locks it, in words that name no writer
        if !c.created_by.starts_with("human:") {
            look.push(format!("    look: check {} before locking it; freezing locks it as the test. Is it the one you mean?", c.id));
        }
    }
    let caveats = led.setting_caveats(exp, led.has_origin(exp)?, None)?;
    for (full, st) in exp.all_settings() {
        let state = if st.per_run { "per-run".to_string() } else { st.status.clone() };
        let where_ = st.source.as_ref().map(|x| x.label()).unwrap_or_else(|| "no source".into());
        // flagged as rb show <experiment> flags it: what this read finds of a verified source, or a conflict on record
        let now = ledger::source_problem(&caveats, exp, &full);
        lines.push(
            format!("    {full} = {} · {state} · {where_}", if st.per_run { "(each run)".to_string() } else { show(&st.value) })
                + &now.map(|c| format!(" · {}", c.code.to_uppercase())).unwrap_or_default(),
        );
        if let Some(said) = crate::sources::said_conflict(st) {
            look.push(format!("    look: {full} conflicts with its source: {said}"));
        } else if let Some(c) = now {
            look.push(format!("    look: {}", c.text));
        } else if let Some(why) = named_otherwise(&full, st) {
            look.push(format!("    look: {full} is verified, {why}: is that this setting?"));
        }
    }
    let unknown: Vec<String> = exp.all_settings().into_iter().filter(|(_, s)| s.value.is_null() && s.required && !s.per_run).map(|(n, _)| n).collect();
    if !unknown.is_empty() {
        look.push(format!("    look: unknown and required: {}", unknown.join(", ")));
    }
    lines.extend(look);
    Ok(lines)
}

/// What rb decide --past is given: when the call was made and the document that states it (--source <file>[:<line>]
/// with --quote), each only with --past.
fn past_given(ns: &Namespace, root: &std::path::Path) -> Res<Option<PastGiven>> {
    past_from(flag(ns, "past"), s(ns, "when"), s(ns, "source"), s(ns, "quote"), root)
}

/// A past call as the command line, the SDK and MCP give it: --when, --source and --quote each only with --past, and a
/// source's path read from where the command runs, as every --source is.
pub fn past_from(past: bool, when: Option<String>, source: Option<String>, quote: Option<String>, root: &std::path::Path) -> Res<Option<PastGiven>> {
    if !past {
        if when.is_some() || source.is_some() || quote.is_some() {
            return Err(RBError::msg("E_OBJECT_INVALID", "--when, --source and --quote say when a past call was made and what states it: give --past with them."));
        }
        return Ok(None);
    }
    if source.is_some() != quote.is_some() {
        return Err(RBError::msg("E_OBJECT_INVALID", "--source and --quote go together: the document, and its words that state the call."));
    }
    let source = match (source, quote) {
        (Some(spec), Some(q)) => {
            let (head, sep, tail) = ledger::rpartition(&spec, ":");
            let (path, line) = if sep && !head.is_empty() && !tail.is_empty() && tail.chars().all(|c| c.is_ascii_digit()) { (head.to_string(), tail.parse::<i64>().ok()) } else { (spec.clone(), None) };
            Some((root_relative(&path, root), line, q))
        }
        _ => None,
    };
    Ok(Some(PastGiven { when: when.unwrap_or_default(), source }))
}

/// A decision as rb's text says it, with its authority: a call as made (a past one with when it was made, as given,
/// Retrospective), or a call that waits for a confirmation as proposed, not made, or as a past call mentioned, not
/// confirmed. The words given with it are said as whose they are.
pub fn decision_said(d: &Decision, a: &ledger::Authority, word: &str) -> String {
    let revisit = if d.revisit.is_empty() { String::new() } else { format!(" · revisit: {}", d.revisit) };
    // once the user confirmed the call, the words it came with are theirs; until then they are a session's, whatever
    // its file says of who wrote it
    let said = words(&d.words, if a.confirmed_by.is_some() { CONFIRMED } else if a.is_call() { &d.by } else { "" });
    match a.state {
        "proposed" => {
            let how = if a.confirmed_by.is_some() { "proposed" } else { "proposed, not made" };
            format!("{how}: {} {}{}{said}{revisit}", d.action, d.subject, ledger::session_note(&d.why))
        }
        "past_unconfirmed" => {
            let when = d.past.as_ref().map(ledger::past_when).unwrap_or_default();
            let how = if a.confirmed_by.is_some() { "a past call mentioned" } else { "a past call mentioned, not confirmed" };
            let src = d.past.as_ref().and_then(|p| p.source.as_ref()).map(|s| format!(": {}", ledger::source_said(s))).unwrap_or_default();
            format!("{how}: {} {} ({when}){src}{}{said}{revisit}", d.action, d.subject, ledger::session_note(&d.why))
        }
        _ => {
            let when = crate::view::past_phrase(&Value::Dict(d.dump(false)));
            format!("{word} {}{}{when}{}{}{revisit}", d.subject, crate::actor::by_writer(&d.by), ledger::reason_said(&d.why, &d.by, d.past.is_some(), a.confirms.is_some()), words(&d.words, &d.by))
        }
    }
}

/// What a call that waits for a confirmation says beside its record (data.warnings, an RBWarning from the SDK).
pub fn waits_said(d: &Decision) -> String {
    let what = if d.past.is_some() { "a past call mentioned, not confirmed" } else { "proposed, not made" };
    format!("{} is {what}: it changes nothing until it is confirmed (rb confirm {}, in your own terminal).", d.id, d.id)
}

/// Who a confirmed call's words are, as view::words_are_theirs reads a writer: the user's, who confirmed it.
const CONFIRMED: &str = "human:";

/// What a decision's authority adds under it: the document a past call is given from, in its own words with its file
/// and line, and what git shows of it; and the one command that confirms what waits for a confirmation.
pub fn authority_lines(d: &Decision, a: &ledger::Authority) -> Vec<String> {
    let mut lines = Vec::new();
    if d.past.as_ref().map_or(false, |p| p.source.is_some()) {
        if a.state == "past_unconfirmed" {
            lines.push(format!("  {}", a.basis));
        }
    }
    if let Some(target) = &a.confirms {
        lines.push(format!("  confirms {target}"));
    }
    if let Some(by) = &a.confirmed_by {
        lines.push(format!("  confirmed as {by}"));
    }
    if let Some(by) = &a.answered_by {
        lines.push(format!("  answered by {by}, a call made on {} after it", d.subject));
    }
    if a.pending {
        lines.push(format!("  It changes nothing until it is confirmed: rb confirm {}, in your own terminal.", d.id));
    }
    lines
}

/// What rb decide and rb confirm say under a call they record: what it was made on, that it goes against its rule, and
/// what a call made now does to its line of work.
fn call_lines(led: &Ledger, d: &Decision, a: &ledger::Authority, kind: Option<&str>) -> Res<Vec<String>> {
    let mut lines = Vec::new();
    if !a.pending {
        for (cid, b) in &d.basis {
            lines.push(format!("  {}", led.basis_line(d, cid, b, true)));
        }
        if d.inquiry_basis.as_ref().and_then(|b| b.get("criteria")).and_then(Value::as_dict).map_or(false, |c| c.is_empty()) {
            lines.push("  No criterion is linked directly to this question; this call has no criterion evidence in its basis.".into());
        }
    }
    if kind == Some("criterion") {
        let b = d.basis.get(&d.subject).cloned().unwrap_or(Value::Null);
        let rule = b.get("rule").map(pyjson::py_str);
        let against = (d.action == "accept" && matches!(rule.as_deref(), Some("misses") | Some("undetermined"))) || (d.action == "reject" && rule.as_deref() == Some("meets"));
        if against {
            let recorded = if a.is_call() { "It is recorded as made" } else { "It is recorded as it was given" };
            lines.push(format!("  This goes against the rule ({}). {recorded}; rb status shows both.", led.basis_rule(&d.subject, &b)));
        }
    }
    if a.is_call() {
        if kind == Some("experiment") && d.basis.is_empty() && !led.standing_runs()?.iter().any(|r| r.experiment == d.subject) {
            lines.push(format!("  {} has no evidence recorded yet: this call rests on nothing in the record.", d.subject));
        }
        if d.action == "investigate" {
            lines.push("  It stays open, marked investigating, until someone accepts or rejects it.".into());
        }
        if d.action == "park" {
            lines.push(format!(
                "  Parked: the line pauses; nothing is refuted by it.{}",
                if d.revisit.is_empty() { String::new() } else { format!(" Look again when: {}", d.revisit) }
            ));
        }
    }
    Ok(lines)
}

pub fn cmd_decide(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let past = past_given(ns, &led.root)?;
    let d = led.decide(&s_or(ns, "subject"), &s_or(ns, "action"), &s_or(ns, "why"), &s_or(ns, "revisit"), past.as_ref())?;
    let a = led.authority(&d.id)?.ok_or_else(|| RBError::fault(format!("the decision {} just written has no authority", d.id)))?;
    let kind: Option<String> = if d.subject.contains('/') { Some("setting".into()) } else { led.kind_of(&d.subject)?.map(|k| k.to_string()) };
    let word = if kind.as_deref() == Some("setting") && d.action == "accept" { "vouched for" } else { ledger::made(&d.action) };
    out.say(format!("{}: {}", d.id, decision_said(&d, &a, word)));
    out.say_all(authority_lines(&d, &a));
    if a.pending {
        // what an agent's write says beside its record: the SDK gives it as an RBWarning, --json among data.warnings
        out.warnings.push(waits_said(&d));
    }
    out.say_all(call_lines(&led, &d, &a, kind.as_deref())?);
    actor_line(out, &led)?;
    out.data.insert("object".into(), obj("decision", d.dump(false)));
    out.data.insert("authority".into(), a.to_value());
    out.next = vec!["rb status".into()];
    Ok(0)
}

/// rb confirm: the user's one action that makes a call proposed, or a past call mentioned, in an agent session theirs,
/// and an experiment's design a session wrote any of, with its gate. It records the same call as whoever confirms it,
/// and a design as it was listed, outside an agent session only: a confirmation an agent passes makes nothing the
/// user's. With no id it lists what waits; with an experiment's id alone it lists that design; with --all it says what
/// it confirms, then confirms it.
pub fn cmd_confirm(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    // what it lists and what its receipt says are drawn as the record read before the confirmation
    out.shows_now(&led);
    let who = led.actor()?;
    if who.runtime.is_some() || crate::actor::agent_session_sign().is_some() {
        return Err(RBError::msg_fix(
            "E_USAGE",
            "rb confirm makes a call or a design the user's own, and this is an agent session: a confirmation an agent passes makes nothing the user's.",
            "The user runs it in their own terminal: rb confirm <id> (or c on rb ui). rb status lists what waits, with its command.",
        ));
    }
    if !who.is_human() {
        return Err(RBError::msg_fix("E_USAGE", "rb confirm makes a call or a design the user's own, and RB_ACTOR names an agent here.", "Unset RB_ACTOR, then run it in your own terminal."));
    }
    // each one given once, in the order given: the same id twice confirms it once
    let mut given: Vec<String> = Vec::new();
    for id in list(ns, "ids") {
        if !given.contains(&id) {
            given.push(id);
        }
    }
    let all = flag(ns, "all");
    if all && !given.is_empty() {
        return Err(RBError::msg("E_USAGE", "Give the calls to confirm, or --all, not both."));
    }
    // an experiment's id lists its design while it waits; <experiment>@<hash> confirms its design (with its gate) only
    // as it was listed, or a gate an earlier listing named where no design waits with it; every other id is a call's
    let mut ids: Vec<String> = Vec::new();
    let mut gate_ids: Vec<(String, Option<String>)> = Vec::new();
    for id in given {
        let (exp, key) = match id.split_once('@') {
            Some((e, k)) => (e.to_string(), Some(k.to_string())),
            None => (id.clone(), None),
        };
        if led.kind_of(&exp)? == Some("experiment") {
            if key.as_deref() == Some("") {
                return Err(RBError::msg_fix("E_USAGE", format!("{id} names no key after the @."), format!("rb confirm {exp} lists its design with its key: rb confirm {exp}@<key>.")));
            }
            gate_ids.push((exp, key));
        } else {
            ids.push(id);
        }
    }
    let designs = led.pending_designs()?;
    let design_of = |id: &str| designs.iter().find(|(e, _)| e.id == id).map(|(_, d)| d.clone());
    // a gate a session set waits with its design; one is said apart only where no design waits with it
    let gates: Vec<Experiment> = led.pending_gates()?.into_iter().filter(|e| design_of(&e.id).is_none()).collect();
    let gates_before: Vec<Experiment> = led.pending_gates()?;
    let gate_words = led.gate_words()?;
    // with its command where it waits; where --all is confirming it, without
    let gate_with = |e: &Experiment, command: bool| -> String {
        let (words, by) = gate_words.get(&e.id).cloned().unwrap_or_default();
        let confirm = if command { crate::view::gate_confirm(&e.id, &e.if_holds, &e.if_not, &words) } else { String::new() };
        format!("{} gates: {}", e.id, crate::view::gate_said(&e.if_holds, &e.if_not, &words, &by, &confirm))
    };
    let gate_said = |e: &Experiment| gate_with(e, true);
    let gate_rows = |w: &[Experiment]| -> Value {
        Value::List(
            w.iter()
                .map(|e| {
                    let words = gate_words.get(&e.id).map(|(w, _)| w.clone()).unwrap_or_default();
                    let confirm = match design_of(&e.id) {
                        Some(d) => d.confirm(),
                        None => crate::view::gate_confirm(&e.id, &e.if_holds, &e.if_not, &words),
                    };
                    crate::obj! {"experiment" => e.id.clone(), "if_holds" => e.if_holds.clone(), "if_not" => e.if_not.clone(), "words" => words, "confirm" => confirm}
                })
                .collect(),
        )
    };
    // a design, part by part and briefly, with its command where it waits
    let design_lines = |e: &Experiment, d: &ledger::Design, command: bool| -> Res<Vec<String>> {
        let mut l = vec![ledger::design::visible(&if e.title.is_empty() || e.title == e.id { format!("  {}", e.id) } else { format!("  {}: {}", e.id, e.title) })];
        l.extend(led.design_said(e, d)?.into_iter().map(|x| format!("    {x}")));
        if command {
            l.push(format!("    confirm: {}", d.confirm()));
        }
        Ok(l)
    };
    let design_rows = |w: &[(Experiment, ledger::Design)]| -> Res<Value> {
        let mut rows = Vec::new();
        for (e, d) in w {
            rows.push(crate::obj! {
                "experiment" => e.id.clone(), "title" => e.title.clone(), "standing" => d.state, "key" => d.key.clone(), "confirm" => d.waiting_command(),
                "changes" => Value::List(d.changes.iter().map(|c| c.to_value()).collect()), "lines" => Value::list_of_str(led.design_said(e, d)?)
            });
        }
        Ok(Value::List(rows))
    };
    let waiting = led.pending_calls()?;
    let said = |d: &Decision, a: &ledger::Authority| -> Res<String> {
        let why_not = if d.past.as_ref().map_or(false, |p| p.source.is_some()) { format!(" · {}", a.basis) } else { String::new() };
        // what a screen would hide or act on shows as its escape, as in a design's listing: the user confirms what they read
        let moved: String = led.rule_moved(d)?.iter().map(|m| format!("\n      {}", ledger::design::visible(m))).collect();
        let after = match &d.past {
            Some(p) => ledger::runs_after_said(&d.subject, &p.when, &led.runs_after_past_call(d)?),
            None => String::new(),
        };
        Ok(ledger::design::visible(&format!("{}  {}{why_not}{after}", d.id, decision_said(d, a, ledger::made(&d.action)))) + &moved)
    };
    let rows = |w: &[(Decision, ledger::Authority)]| -> Value {
        Value::List(w.iter().map(|(d, a)| { let mut r = d.dump(false); r.insert("authority".into(), a.to_value()); Value::Dict(r) }).collect())
    };
    let n = |k: usize, one: &str, many: &str| if k == 1 { format!("1 {one}") } else { format!("{k} {many}") };
    // an experiment named alone, whose design waits: that design, as it reads now, with its command
    let listed: Vec<(Experiment, ledger::Design)> = gate_ids.iter().filter(|(_, key)| key.is_none()).filter_map(|(id, _)| designs.iter().find(|(e, _)| &e.id == id).cloned()).collect();
    if ids.is_empty() && !all && !gate_ids.is_empty() && listed.len() == gate_ids.len() {
        out.say(format!("{} for a confirmation ({}):", if listed.len() == 1 { "Design waiting" } else { "Designs waiting" }, listed.len()));
        for (e, d) in &listed {
            out.say_all(design_lines(e, d, true)?);
        }
        // several named: the one command that confirms them all as listed, as rb confirm alone ends
        if listed.len() > 1 {
            let every: Vec<String> = listed.iter().map(|(e, d)| format!("{}@{}", e.id, d.confirmation_key())).collect();
            out.say(format!("rb confirm <experiment>@<hash> confirms one; rb confirm {} confirms these {} as listed.", every.join(" "), every.len()));
        }
        out.data.insert("pending".into(), rows(&waiting));
        out.data.insert("confirmed".into(), Value::List(Vec::new()));
        out.data.insert("gates_pending".into(), gate_rows(&gates_before));
        out.data.insert("gates_confirmed".into(), Value::List(Vec::new()));
        out.data.insert("designs_pending".into(), design_rows(&listed)?);
        out.data.insert("designs_confirmed".into(), Value::List(Vec::new()));
        return Ok(0);
    }
    if ids.is_empty() && gate_ids.is_empty() && !all {
        if waiting.is_empty() && gates.is_empty() && designs.is_empty() {
            out.say("Nothing waits for a confirmation.");
        } else if !waiting.is_empty() {
            out.say(format!("Waiting for a confirmation ({}):", waiting.len()));
            for (d, a) in &waiting {
                out.say(format!("  {}", said(d, a)?));
            }
        }
        // the gates a session set that no design waits with: each is the user's on one rb confirm <experiment>@<hash>
        if !gates.is_empty() {
            out.say(format!("Gates waiting for a confirmation ({}):", gates.len()));
            for e in &gates {
                out.say(format!("  {}", gate_said(e)));
            }
        }
        // then each design that waits, part by part, with its gate: the user's on one rb confirm <experiment>@<hash>
        if !designs.is_empty() {
            out.say(format!("Designs waiting for a confirmation ({}):", designs.len()));
            for (e, d) in &designs {
                out.say_all(design_lines(e, d, true)?);
            }
        }
        // one line for the command that confirms them all as listed, each by its id: a call alone on its subject (which
        // of several is the user's is theirs to say), a gate or a design by its hash; --all confirms what reads when it
        // runs, which a session may have changed or added to since this listing
        let alone: Vec<&Decision> = waiting.iter().map(|(d, _)| d).filter(|d| waiting.iter().filter(|(x, _)| x.subject == d.subject).count() == 1).collect();
        let mut every: Vec<String> = alone.iter().map(|d| d.id.clone()).collect();
        every.extend(gates.iter().map(|e| {
            let words = gate_words.get(&e.id).map(|(w, _)| w.clone()).unwrap_or_default();
            format!("{}@{}", e.id, crate::view::gate_key(&e.if_holds, &e.if_not, &words))
        }));
        every.extend(designs.iter().map(|(e, d)| format!("{}@{}", e.id, d.confirmation_key())));
        let counts = [alone.len(), gates.len(), designs.len()];
        let kinds: Vec<String> = [(counts[0], "call", "calls"), (counts[1], "gate", "gates"), (counts[2], "design", "designs")]
            .into_iter()
            .filter(|(k, _, _)| *k > 0)
            .map(|(k, one, many)| n(k, one, many))
            .collect();
        let these = match kinds.as_slice() {
            // "these 2 calls and 3 designs", but "1 call and 1 design" with no "these"
            [rest @ .., last] if !rest.is_empty() => format!("{}{} and {last}", if counts.contains(&1) { "" } else { "these " }, rest.join(", ")),
            // "these 3": calls alone, or gates and designs alone
            _ => format!("these {}", every.len()),
        };
        let one = if waiting.is_empty() { "rb confirm <experiment>@<hash> confirms one" } else { "rb confirm <id> confirms one" };
        match (waiting.as_slice(), gates.len() + designs.len()) {
            ([], 0) => (),
            ([(d, _)], 0) => out.say(format!("rb confirm {} confirms it.", d.id)),
            ([], 1) => (),
            _ if every.len() < 2 => out.say(format!("{one}.")),
            _ => out.say(format!("{one}; rb confirm {} confirms {these} as listed.", every.join(" "))),
        }
        out.data.insert("pending".into(), rows(&waiting));
        out.data.insert("confirmed".into(), Value::List(Vec::new()));
        out.data.insert("gates_pending".into(), gate_rows(&gates_before));
        out.data.insert("gates_confirmed".into(), Value::List(Vec::new()));
        out.data.insert("designs_pending".into(), design_rows(&designs)?);
        out.data.insert("designs_confirmed".into(), Value::List(Vec::new()));
        return Ok(0);
    }
    // --all confirms no subject that more than one call waits on: which of them is the user's is theirs to say
    let mut on: std::collections::HashMap<&str, Vec<&str>> = std::collections::HashMap::new();
    for (d, _) in &waiting {
        on.entry(d.subject.as_str()).or_default().push(d.id.as_str());
    }
    let alone: Vec<&(Decision, ledger::Authority)> = waiting.iter().filter(|(d, _)| on[d.subject.as_str()].len() == 1).collect();
    let targets: Vec<String> = if all { alone.iter().map(|(d, _)| d.id.clone()).collect() } else { ids };
    if all {
        if waiting.is_empty() && gates.is_empty() && designs.is_empty() {
            out.say("Nothing waits for a confirmation.");
        } else if !alone.is_empty() {
            out.say(format!("Confirming {} call{}:", alone.len(), if alone.len() == 1 { "" } else { "s" }));
            for (d, a) in &alone {
                out.say(format!("  {}", said(d, a)?));
            }
        }
        let mut skipped: Vec<(&str, &Vec<&str>)> = on.iter().filter(|(_, v)| v.len() > 1).map(|(k, v)| (*k, v)).collect();
        skipped.sort_by_key(|(_, v)| waiting.iter().position(|(d, _)| d.id == v[0]));
        for (subject, them) in skipped {
            out.say(format!("Not confirmed: {} calls wait on {subject} ({}). Confirm the one that is yours: rb confirm <id>.", them.len(), them.join(", ")));
        }
    }
    // after the calls, the gates no design waits with, and the designs: those named, or with --all every one, each as
    // listed above
    let (gate_targets, design_targets): (Vec<(String, Option<String>)>, Vec<(String, Option<String>)>) = if all {
        let gate_targets = gates
            .iter()
            .map(|e| {
                let words = gate_words.get(&e.id).map(|(w, _)| w.clone()).unwrap_or_default();
                (e.id.clone(), Some(crate::view::gate_key(&e.if_holds, &e.if_not, &words)))
            })
            .collect();
        (gate_targets, designs.iter().map(|(e, d)| (e.id.clone(), Some(d.confirmation_key().to_string()))).collect())
    } else {
        gate_ids.into_iter().partition(|(id, _)| design_of(id).is_none())
    };
    // a design is confirmed only as it was listed: named alone among others, it is listed first
    if let Some((id, _)) = design_targets.iter().find(|(_, key)| key.is_none()) {
        let cmd = design_of(id).map(|d| d.confirm()).unwrap_or_default();
        return Err(RBError::msg_fix("E_USAGE", format!("{id}'s design waits, and is confirmed only as it was listed: rb confirm {id} lists it."), format!("To confirm it as it reads now: {cmd}")));
    }
    let mut confirmed: Vec<Value> = Vec::new();
    let mut done: Vec<String> = Vec::new();
    for id in &targets {
        let given = led.load::<Decision>(id).ok();
        let answered = led.authority(id)?.and_then(|a| a.answered_by);
        let d = match led.confirm(id) {
            Ok(d) => d,
            Err(mut e) if !done.is_empty() => {
                e.message = format!("{} Confirmed before it: {}.", e.message, done.join(", "));
                return Err(e);
            }
            Err(e) => return Err(e),
        };
        let a = led.authority(&d.id)?.ok_or_else(|| RBError::fault(format!("the decision {} just written has no authority", d.id)))?;
        let kind: Option<String> = if d.subject.contains('/') { Some("setting".into()) } else { led.kind_of(&d.subject)?.map(|k| k.to_string()) };
        let word = if kind.as_deref() == Some("setting") && d.action == "accept" { "vouched for" } else { ledger::made(&d.action) };
        out.say(format!("{}: {}   (confirms {id})", d.id, decision_said(&d, &a, word)));
        if let Some(by) = answered {
            out.say(format!("  {id} was answered by {by}, a call made on {} after it was proposed: this call now comes after that one.", d.subject));
        }
        if let Some(p) = &d.past {
            let after = ledger::runs_after_said(&d.subject, &p.when, &led.runs_after_past_call(&d)?);
            if let Some(said) = after.strip_prefix(" · ") {
                out.say(format!("  {said}"));
            }
        }
        if let Some(given) = &given {
            out.say_all(led.rule_moved(given)?.iter().map(|m| format!("  {m}")).collect::<Vec<_>>());
        }
        out.say_all(call_lines(&led, &d, &a, kind.as_deref())?);
        done.push(format!("{id} as {}", d.id));
        let mut row = crate::dict! {"confirms" => id.clone()};
        row.insert("object".into(), obj("decision", d.dump(false)));
        row.insert("authority".into(), a.to_value());
        confirmed.push(Value::Dict(row));
    }
    if all && !gate_targets.is_empty() {
        out.say(format!("Confirming {} gate{}:", gate_targets.len(), if gate_targets.len() == 1 { "" } else { "s" }));
        for e in &gates {
            out.say(format!("  {}", gate_with(e, false)));
        }
    }
    let mut gates_confirmed: Vec<Experiment> = Vec::new();
    for (id, key) in &gate_targets {
        let e = match led.confirm_gate(id, key.as_deref()) {
            Ok(e) => e,
            Err(mut e) if !done.is_empty() => {
                e.message = format!("{} Confirmed before it: {}.", e.message, done.join(", "));
                return Err(e);
            }
            Err(e) => return Err(e),
        };
        let (words, by) = led.gate_words()?.remove(&e.id).unwrap_or_default();
        out.say(format!("{}'s gate is confirmed: {}", e.id, crate::view::gate_said(&e.if_holds, &e.if_not, &words, &by, "")));
        done.push(format!("{}'s gate", e.id));
        gates_confirmed.push(e);
    }
    if all && !design_targets.is_empty() {
        out.say(format!("Confirming {}:", n(design_targets.len(), "design", "designs")));
        for (e, d) in &designs {
            out.say_all(design_lines(e, d, false)?);
        }
    }
    let mut designs_confirmed: Vec<(Experiment, ledger::Design)> = Vec::new();
    for (id, key) in &design_targets {
        let d = match led.confirm_design(id, key.as_deref().unwrap_or_default()) {
            Ok(d) => d,
            Err(mut e) if !done.is_empty() => {
                e.message = format!("{} Confirmed before it: {}.", e.message, done.join(", "));
                return Err(e);
            }
            Err(e) => return Err(e),
        };
        let e: Experiment = led.load(id)?;
        out.say(format!("{id}'s design is confirmed."));
        // its gate is the user's now, with its words
        if !(e.if_holds.is_empty() && e.if_not.is_empty()) {
            let (words, by) = led.gate_words()?.remove(&e.id).unwrap_or_default();
            out.say(format!("  gate: {}", crate::view::gate_said(&e.if_holds, &e.if_not, &words, &d.writer(&by, "gate"), "")));
        }
        if gates_before.iter().any(|g| g.id == e.id) {
            gates_confirmed.push(e.clone());
        }
        done.push(format!("{id}'s design"));
        designs_confirmed.push((e, d));
    }
    out.data.insert("confirmed".into(), Value::List(confirmed));
    out.data.insert("pending".into(), rows(&led.pending_calls()?));
    out.data.insert("gates_confirmed".into(), gate_rows(&gates_confirmed));
    out.data.insert("gates_pending".into(), gate_rows(&led.pending_gates()?));
    out.data.insert("designs_confirmed".into(), design_rows(&designs_confirmed)?);
    out.data.insert("designs_pending".into(), design_rows(&led.pending_designs()?)?);
    out.next = vec!["rb status".into()];
    Ok(0)
}
