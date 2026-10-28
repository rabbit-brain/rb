//! The commands that keep the record in shape: rb fmt, rb doctor, the journal (rb journal, scan and run) and rb
//! reconcile.

use super::*;
use crate::journal::{self as jr, Found};
use crate::ledger::{find_root, Obj, DIR, UNSHALLOW, UNSHALLOW_CI};
use crate::repo::{self, FolderView};
use indexmap::IndexMap;

type R = Res<i32>;

const EXIT_CHECK_FAILED: i32 = 1;

fn gs(v: &Value, k: &str) -> String {
    v.get(k).map(pyjson::py_str).unwrap_or_default()
}

fn plural(n: usize, one: &str, many: &str) -> String {
    if n == 1 {
        one.to_string()
    } else {
        many.to_string()
    }
}

// ---------------------------------------------------------------- rb fmt


/// Path.rglob("*.yaml"): every entry below `dir` whose name ends in .yaml, symlinked directories not entered.
fn rglob_yaml(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    let mut entries: Vec<std::fs::DirEntry> = rd.flatten().collect();
    entries.sort_by_key(|e| e.file_name());
    for e in entries {
        let p = e.path();
        if e.file_name().to_string_lossy().ends_with(crate::ledger::EXT) {
            out.push(p.clone());
        }
        let is_link = e.file_type().map_or(false, |t| t.is_symlink());
        if !is_link && p.is_dir() {
            rglob_yaml(&p, out);
        }
    }
}

/// sorted(paths): pathlib orders by parts, not by the text.
fn sort_paths(paths: &mut [PathBuf]) {
    paths.sort_by(|a, b| {
        let pa: Vec<String> = a.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect();
        let pb: Vec<String> = b.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect();
        pa.cmp(&pb)
    });
}

pub fn cmd_fmt(ns: &Namespace, out: &mut Out) -> R {
    let check = flag(ns, "check");
    let given = list(ns, "paths");
    let mut files: Vec<PathBuf> = Vec::new();
    if !given.is_empty() {
        for p in &given {
            let path = PathBuf::from(pathlib_str(p));
            if path.is_dir() {
                let mut found = Vec::new();
                rglob_yaml(&path, &mut found);
                sort_paths(&mut found);
                files.extend(found);
            } else {
                files.push(path);
            }
        }
    } else {
        let root = find_root(None).ok_or_else(|| crate::ledger::no_project(None))?;
        let dir = root.join(DIR);
        let mut found = Vec::new();
        rglob_yaml(&dir, &mut found);
        sort_paths(&mut found);
        files = found.into_iter().filter(|p| !p.strip_prefix(&dir).map_or(false, |r| r.components().any(|c| c.as_os_str() == "cache"))).collect();
    }
    let mut changed: Vec<String> = Vec::new();
    let mut broken: Vec<Value> = Vec::new();
    for path in &files {
        let shown = crate::sys::slashed(path);
        // a named file of another kind (rb's own JSON, a .gitignore) may read as YAML, and is still not one to rewrite
        if path.is_file() && !path.file_name().map_or(false, |n| n.to_string_lossy().ends_with(crate::ledger::EXT)) {
            let error = format!("{shown}: not a .yaml file, so rb fmt leaves it as it is");
            broken.push(crate::obj! {"path" => shown.clone(), "error" => error, "fix" => "Name .yaml files, or the directories that hold them."});
            continue;
        }
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(e) => {
                broken.push(crate::obj! {"path" => shown.clone(), "error" => format!("{shown}: {}", crate::sys::os_words(&e)), "fix" => ""});
                continue;
            }
        };
        let text = match std::str::from_utf8(&bytes) {
            Ok(t) => t.to_string(),
            Err(e) => {
                broken.push(crate::obj! {"path" => shown.clone(), "error" => format!("{shown}: {}", crate::rbyaml::not_utf8(&bytes, &e)), "fix" => "Save the file as UTF-8."});
                continue;
            }
        };
        let doc = match crate::rbyaml::load(&text, &shown) {
            Ok(d) => d,
            Err(e) => {
                broken.push(crate::obj! {"path" => shown.clone(), "error" => e.to_string(), "fix" => e.fix.clone()});
                continue;
            }
        };
        // what the reader took, the writer can write: a failure here is rb's own
        let canonical = crate::rbyaml::dumps(&doc.data, Some(&doc.comments)).map_err(|e| RBError::fault(format!("{shown} read, and could not be written back: {}", e.0)))?;
        if canonical != text {
            changed.push(shown.clone());
            if !check {
                let tmp = path.with_file_name(format!("{}.fmt.tmp", path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()));
                std::fs::write(&tmp, canonical.as_bytes()).map_err(|e| RBError::could_not_write(&e, &tmp))?;
                crate::sys::replace(&tmp, path).map_err(|e| RBError::could_not_write(&e, path))?;
            }
        }
    }
    for b in &broken {
        out.say(gs(b, "error"));
    }
    let verb = if check { "not canonical" } else { "rewritten" };
    out.say(
        format!("{} {}: {} {verb}", files.len(), plural(files.len(), "file", "files"), changed.len())
            + &if broken.is_empty() { String::new() } else { format!(", {} not Rabbit Brain YAML", broken.len()) }
            + ".",
    );
    for c in changed.iter().take(20) {
        out.say(format!("  {c}"));
    }
    let failed = !broken.is_empty() || (check && !changed.is_empty());
    out.data.insert("files".into(), Value::Int(files.len() as i64));
    out.data.insert("changed".into(), Value::list_of_str(changed));
    out.data.insert("broken".into(), Value::List(broken));
    out.data.insert("check".into(), Value::Bool(check));
    Ok(if failed { EXIT_CHECK_FAILED } else { 0 })
}

// ---------------------------------------------------------------- rb doctor

fn problem(check: &str, detail: String, fix: &str) -> Value {
    crate::obj! {"check" => check, "ok" => false, "detail" => detail, "fix" => fix}
}

pub fn cmd_doctor(ns: &Namespace, out: &mut Out) -> R {
    let root = find_root(None).ok_or_else(|| crate::ledger::no_project(None))?;
    // not Ledger::open: doctor must run when a file is corrupt
    let led = Ledger::new(&root, "cli", None).at(None);
    research_doctor(ns, out, &led)
}

/// What restore or adopt did with one record, or that it left the record as it is, and why.
fn went_over(verb: &str, d: &Dict) -> String {
    match get_s(d, "done").strip_prefix("left as it is: ") {
        Some(why) => format!("left {} as it is: {why}", get_s(d, "path")),
        None => format!("{verb} {}: {}", get_s(d, "path"), get_s(d, "done")),
    }
}

/// What can go wrong with .rb/ that no single command would notice. Every file is read on its own, so one that does not
/// parse is listed rather than stopping the check.
pub fn research_doctor(ns: &Namespace, out: &mut Out, led: &Ledger) -> R {
    let (restore, adopt) = (flag(ns, "restore"), flag(ns, "adopt"));
    if restore && adopt {
        return Err(RBError::msg("E_USAGE", "rb doctor takes --restore or --adopt, not both."));
    }
    if restore {
        let restored = led.restore()?;
        for d in &restored {
            out.say(went_over("restored", d));
        }
        if restored.is_empty() {
            out.say("Nothing to restore: every record is what rb last wrote.");
        }
        out.data.insert("restored".into(), Value::List(restored.into_iter().map(Value::Dict).collect()));
    }
    if adopt {
        let Some(why) = s(ns, "why").filter(|w| !w.is_empty()) else {
            return Err(RBError::msg("E_OBJECT_INVALID", "rb doctor --adopt needs --why \"<reason>\": the reason is what the adoption records."));
        };
        let pins = list(ns, "seen");
        let mut seen: Option<Dict> = None;
        if !pins.is_empty() {
            let mut d = Dict::new();
            for pin in &pins {
                let (path, sha) = match pin.rfind('@') {
                    Some(i) => (&pin[..i], &pin[i + 1..]),
                    None => ("", pin.as_str()),
                };
                if path.is_empty() || sha.is_empty() {
                    return Err(RBError::msg("E_USAGE", format!("--seen takes PATH@SHA256 (or PATH@deleted), got {}.", pyjson::repr_str(pin))));
                }
                d.insert(pathlib_str(path), Value::Str(sha.to_string()));
            }
            seen = Some(d);
        }
        let adopted = led.adopt(&why, seen.as_ref())?;
        for d in &adopted {
            out.say(went_over("adopted", d));
        }
        out.data.insert("adopted".into(), Value::List(adopted.into_iter().map(Value::Dict).collect()));
    }
    // the identity check: how this shell's writes are recorded, the one place rb's text says it
    let a = crate::actor::current(Some(&led.root))?;
    let stamp = match (&a.ignored, &a.runtime) {
        (Some(ig), Some(rt)) if !ig.is_empty() && !rt.is_empty() => format!(" (RB_ACTOR={ig} is ignored in an agent session)"),
        _ => String::new(),
    };
    let mut checks: Vec<Value> = vec![crate::obj! {"check" => "actor", "ok" => true, "detail" => format!("{}, from {}{stamp}", a.id, a.via)}];
    let mut problems: Vec<Value> = Vec::new();
    let fix_edit = "rb doctor --restore puts back what rb last wrote; if the change is right, rb doctor --adopt --why \"...\" takes it";
    let mut parsed: Vec<(&'static str, Obj)> = Vec::new();
    led.forget_index();
    for (kind, p) in led.record_files() {
        let rel = led.rel(&p);
        let text = if p.exists() { std::fs::read(&p).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default() } else { String::new() };
        if text.contains("<<<<<<<") || text.contains(">>>>>>>") {
            problems.push(problem(
                "merge_conflict",
                format!("{rel} has conflict markers"),
                "resolve it by keeping both sides' changes, which rb reads as its own; where both sides changed one value, keep the side with the later write (the other stays in log.jsonl); then rb doctor again",
            ));
            continue;
        }
        let read: Result<(), Result<RBError, crate::ledger::Unreadable>> = (|| {
            if kind == "project" {
                let proj = led.project().map_err(Ok)?;
                parsed.push(("project", Obj::Project(proj)));
                return Ok(());
            }
            for (id, _) in led.in_(kind, &p).map_err(Err)?.iter() {
                if crate::ledger::is_part(kind) {
                    led.load_any(kind, &id).map_err(Ok)?;
                } else {
                    parsed.push((kind, led.load_obj(kind, &id).map_err(Ok)?));
                }
            }
            Ok(())
        })();
        match read {
            Ok(()) => {}
            Err(Ok(e)) => {
                if e.code == "E_STATE_NEWER" {
                    // a file edited by hand says so, with the restore, in place of the bare upgrade
                    let upgrade = crate::errors::meaning("E_STATE_NEWER").map_or(true, |(_, f)| f == e.fix);
                    // a message that names its file (a part an experiment is read with names the part's) is said once
                    let said = if e.message.starts_with(&format!("{}/", crate::ledger::DIR)) { e.message.clone() } else { format!("{rel}: {}", e.message) };
                    if !problems.iter().any(|p| gs(p, "detail") == said) {
                        problems.push(problem("state_newer", said, if upgrade { "pip install --upgrade rabbit-brain" } else { &e.fix }));
                    }
                } else {
                    let said = crate::assess::first_few(&e.problems, 2, "; ");
                    problems.push(problem("corrupt", format!("{rel} is not a valid {kind}: {}", if said.is_empty() { e.message.clone() } else { said }), fix_edit));
                }
            }
            Err(Err(u)) => {
                let newer = matches!(u, crate::ledger::Unreadable::Newer(_));
                problems.push(problem(if newer { "state_newer" } else { "corrupt" }, u.text().to_string(), if newer { "pip install --upgrade rabbit-brain" } else { fix_edit }));
            }
        }
    }
    let tamper: Vec<Dict> = match led.tampered() {
        Ok(t) => t.as_ref().clone(),
        Err(e) => {
            problems.push(problem("log", e.message.clone(), "resolve the merge in .rb/log.jsonl by keeping every line from both sides"));
            Vec::new()
        }
    };
    for t in &tamper {
        let tv = Value::Dict(t.clone());
        if tv.get("collision").map_or(false, |v| v.truthy()) {
            problems.push(problem("id_collision", gs(&tv, "why"), "rb doctor --restore gives the second record an id of its own"));
        } else if tv.get("shallow").map_or(false, |v| v.truthy()) {
            problems.push(problem(
                "shallow_clone",
                format!("{}: {}", gs(&tv, "path"), gs(&tv, "why")),
                &format!("{UNSHALLOW} fetches the whole history, then rb doctor again; {UNSHALLOW_CI}"),
            ));
        } else if tv.get("merge").map_or(false, |v| v.truthy()) {
            problems.push(problem(
                "merged_outside_rb",
                format!("{}: {}", gs(&tv, "path"), gs(&tv, "why")),
                "rb doctor --adopt --why \"...\" keeps the merge as it stands; or keep both sides' changes, which rb reads as its own in the merge commit itself (git commit --amend once it is made), or the later write, which rb doctor --restore puts back",
            ));
        } else {
            let path = gs(&tv, "path");
            if !problems.iter().any(|p| gs(p, "detail").starts_with(&path)) {
                problems.push(problem("edited_outside_rb", format!("{path}: {}", gs(&tv, "why")), fix_edit));
            }
        }
    }
    let kept: Vec<String> = led.read_by_kept_merges().unwrap_or_default().into_iter().map(|(k, i)| format!("{k} {i}")).collect();
    if !kept.is_empty() {
        let one = kept.len() == 1;
        let (reads, merges, it) = if one { ("reads", "the merge", "it") } else { ("read", "the merges", "them") };
        let detail = format!("{} {reads} as rb's own by {merges} rb export kept in {DIR}/{}, checked again here, where git cannot show {it}", kept.join(", "), crate::ledger::KEPT_MERGES);
        checks.push(crate::obj! {"check" => "kept_merges", "ok" => true, "detail" => detail});
    }
    for u in led.newer()? {
        let why = get_s(&u, "why");
        if !problems.iter().any(|p| gs(p, "detail").contains(&why)) {
            problems.push(problem("state_newer", why, "pip install --upgrade rabbit-brain"));
        }
    }
    let mut ids: IndexMap<String, &str> = IndexMap::new();
    for kind in ["question", "experiment", "criterion", "run", "analysis", "metric", "resource", "decision"] {
        for (k, o) in &parsed {
            if *k != kind {
                continue;
            }
            let id = o.id().to_string();
            if let Some(was) = ids.get(&id) {
                if *was != kind {
                    problems.push(problem("duplicate_id", format!("{id} is both a {was} and a {kind}"), "retract one and add it again"));
                }
            }
            ids.entry(id).or_insert(kind);
        }
    }
    for (_, o) in &parsed {
        if let Obj::Run(r) = o {
            for u in r.inputs.iter().chain(r.outputs.iter()) {
                if ids.get(&u.resource).copied() != Some("resource") {
                    problems.push(problem("dangling", format!("run {} names resource {}, which is missing", r.id, u.resource), fix_edit));
                }
            }
        }
    }
    for (_, o) in &parsed {
        if let Obj::Experiment(e) = o {
            for (full, st) in e.all_settings() {
                if let Some(src) = &st.source {
                    if let Some(path) = src.path.as_ref().filter(|p| !p.is_empty()) {
                        if (src.kind == "file" || src.kind == "run") && !crate::sources::inside_project(&led.root, path) {
                            problems.push(problem(
                                "outside",
                                format!("{}/{full} reads {path}, outside the project", e.id),
                                "copy it into the repository and set the source again",
                            ));
                        }
                    }
                }
            }
        }
    }
    checks.extend(problems.iter().cloned());
    for c in &checks {
        let ok = c.get("ok").map_or(false, |v| v.truthy());
        let fix = gs(c, "fix");
        out.say(
            format!("{} {}: {}", if ok { "ok  " } else { "FAIL" }, gs(c, "check"), gs(c, "detail"))
                + &if fix.is_empty() { String::new() } else { format!("\n     → {fix}") },
        );
    }
    let passed = problems.is_empty();
    out.data.insert("checks".into(), Value::List(checks));
    out.data.insert("outcome".into(), crate::obj! {"passed" => passed, "failures" => Value::List(problems)});
    Ok(if passed { 0 } else { EXIT_CHECK_FAILED })
}

// ---------------------------------------------------------------- the journal

/// rows[-last:] if last else rows
fn py_last<T>(rows: Vec<T>, last: i64) -> Vec<T> {
    if last == 0 {
        return rows;
    }
    let n = rows.len() as i64;
    let start = if last > 0 { (n - last).max(0) } else { (-last).min(n) };
    rows.into_iter().skip(start as usize).collect()
}

/// A journal time as rb writes one: the time, and its UTC offset in seconds when it has one. None for anything else.
fn iso(v: Option<&Value>) -> Option<(chrono::NaiveDateTime, Option<i32>)> {
    match v {
        Some(Value::Str(s)) => crate::pytime::fromisoformat(s).map(|(t, o)| (t, o.map(|x| x as i32))),
        _ => None,
    }
}

/// The seconds from one journal time to another. None when either is not a time, or only one of them says its offset.
fn seconds_between(a: Option<&Value>, b: Option<&Value>) -> Option<f64> {
    let (na, oa) = iso(a)?;
    let (nb, ob) = iso(b)?;
    let micros = match (oa, ob) {
        (Some(x), Some(y)) => ((na - chrono::Duration::seconds(x as i64)) - (nb - chrono::Duration::seconds(y as i64))).num_microseconds(),
        (None, None) => (na - nb).num_microseconds(),
        _ => return None,
    };
    Some(micros.unwrap_or(i64::MAX) as f64 / 1e6)
}

/// A field that is text when it is there: Some("") when absent or null, None when it holds anything else.
fn text_or_absent(ev: &Value, k: &str) -> Option<String> {
    match ev.get(k) {
        None | Some(Value::Null) => Some(String::new()),
        Some(Value::Str(s)) => Some(s.clone()),
        Some(_) => None,
    }
}

/// What a journal entry prints as. Ok(None) for a kind of entry this rb does not list; Err when rb cannot read the
/// entry (it is not as rb writes one: a file something else wrote into, or a hand edit), which the listing passes over
/// and counts.
fn journal_line(ev: &Value) -> Result<Option<String>, ()> {
    let op = ev.get("op").and_then(|v| v.as_str());
    if !matches!(op, Some("file" | "git" | "run" | "dismiss")) {
        return Ok(None);
    }
    let Some(Value::Str(at)) = ev.get("at") else { return Err(()) };
    let at: String = at.chars().skip(11).take(5).collect();
    let text = |k: &str| text_or_absent(ev, k).ok_or(());
    Ok(Some(match op {
        Some("file") => {
            let Some(Value::Str(change)) = ev.get("change") else { return Err(()) };
            let path = text("path")?;
            let written = text("written")?;
            // a file is "last written" at another time when it was seen more than a minute from when it was written
            let late = !written.is_empty() && seconds_between(ev.get("at"), ev.get("written")).ok_or(())?.abs() > 60.0;
            let when = if late { format!("   (last written {} UTC)", written.chars().take(16).collect::<String>().replace('T', " ")) } else { String::new() };
            format!("  {at}  {change:<8} {path}{when}")
        }
        Some("git") => {
            let commit = text("commit")?;
            // checked as rb writes it, never said: a branch's name often names an agent (--json keeps it)
            text("branch")?;
            let commit = if commit.is_empty() { "none".to_string() } else { commit.chars().take(10).collect() };
            // the paths the move changed, counted: rb journal --json lists them, the first 4000 where git listed more
            let moved = match (ev.get("from").and_then(|f| f.as_str()), ev.get("changed").and_then(|c| c.as_list())) {
                (Some(from), Some(paths)) if !paths.is_empty() => {
                    let more = if ev.get("cut").map_or(false, |c| c.truthy()) && paths.len() >= jr::MAX_FILES { " or more" } else { "" };
                    format!(" · {}{more} {} changed since {}", paths.len(), plural(paths.len(), "path", "paths"), from.chars().take(10).collect::<String>())
                }
                _ => String::new(),
            };
            format!("  {at}  git      commit {commit}") + if ev.get("dirty").map_or(false, |d| d.truthy()) { ", tree dirty" } else { "" } + &moved
        }
        Some("run") => {
            let Some(Value::Str(command)) = ev.get("command") else { return Err(()) };
            let Some(exit @ Value::Int(_)) = ev.get("exit") else { return Err(()) };
            let Some(secs @ (Value::Int(_) | Value::Float(_))) = ev.get("secs") else { return Err(()) };
            format!("  {at}  ran      {command} (exit {}, {}s)", pyjson::py_str(exit), pyjson::py_str(secs))
        }
        Some("dismiss") => {
            let Some(Value::Str(key)) = ev.get("key") else { return Err(()) };
            let why = text("why")?;
            // only the user's word leaves an item out of the user's screens, and only it is listed here: a session's
            // stays in --json
            if !jr::users_word(ev) {
                return Ok(None);
            }
            let what = ev.get("what").and_then(|w| w.as_str()).map_or_else(|| key.chars().take(16).collect::<String>(), str::to_string);
            format!("  {at}  left out {what}") + &if why.is_empty() { String::new() } else { format!(": \"{why}\"") }
        }
        _ => return Ok(None),
    }))
}

pub fn cmd_journal(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let n = arg(ns, "n").as_i64().unwrap_or(20);
    let rows = py_last(jr::entries(&led.root, None), n);
    if rows.is_empty() {
        // said from when rb last looked: a look that found nothing to write down writes nothing
        let first = match jr::Scope::of(&led).looked_at {
            Some(at) => format!("The journal is empty here: rb last looked at {} UTC and wrote nothing down.", at.chars().take(16).collect::<String>().replace('T', " ")),
            None => "The journal is empty here: rb has not looked at this repository yet.".to_string(),
        };
        out.say(format!(
            "{first} rb status, rb context and rb journal scan look once, rb open and rb ui look while they are open, and rb journal run -- <cmd> wraps a command so its exit code and files are on record."
        ));
    }
    let mut passed_over = 0usize;
    for e in &rows {
        let mut e = e.clone();
        // what an item left out is, by its path or its command, never by the key it is kept under
        if e.get("op").and_then(|v| v.as_str()) == Some("dismiss") {
            if let Some(what) = e.get("key").and_then(|v| v.as_str()).and_then(|k| jr::left_out_what(&led.root, k)) {
                e.insert("what".into(), Value::Str(what));
            }
        }
        match journal_line(&Value::Dict(e)) {
            Ok(Some(line)) => out.say(line),
            Ok(None) => {}
            Err(()) => passed_over += 1,
        }
    }
    if passed_over > 0 {
        out.say(format!(
            "  {passed_over} {} not be read, and {} not listed: rb journal --json gives every entry as it is.",
            plural(passed_over, "entry could", "entries could"),
            plural(passed_over, "is", "are")
        ));
    }
    let count = rows.len();
    out.data.insert("entries".into(), Value::List(rows.into_iter().map(Value::Dict).collect()));
    out.data.insert("entry_count".into(), Value::Int(count as i64));
    out.next = vec!["rb reconcile".into()];
    Ok(0)
}

pub fn cmd_journal_scan(_ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let s = Value::Dict(jr::scan(&led)?);
    let num = |k: &str| s.get(k).and_then(|v| v.as_i64()).unwrap_or(0);
    let flag = |k: &str| s.get(k).map_or(false, |v| v.truthy());
    let in_git = jr::load_state(&led.root).get("git").map_or(false, |g| g.is_dict());
    // HEAD moved, or only the working tree changed: said apart
    let head = flag("head_moved");
    let tree = !head && (flag("git_moved") || flag("tree_changed"));
    let line = format!("{} new, {} changed, {} gone", num("new"), num("changed"), num("gone"))
        + if head { ", git moved" } else if tree { ", the working tree changed" } else { "" };
    let quiet = num("new") == 0 && num("changed") == 0 && num("gone") == 0 && !head && !tree;
    let still = if in_git { "No file where rb looks changed since its last look, and git did not move." } else { "No file where rb looks changed since its last look." };
    out.say(
        (if quiet { still.to_string() } else { line }) + if flag("truncated") { " The walk hit its cap; some places were not looked at." } else { "" },
    );
    out.next = if num("new") != 0 || num("changed") != 0 { vec!["rb reconcile".into()] } else { Vec::new() };
    out.data.insert("scan".into(), s);
    Ok(0)
}

pub fn cmd_journal_run(ns: &Namespace, out: &mut Out) -> R {
    let mut cmd = list(ns, "cmd");
    if cmd.first().map(|c| c.as_str()) == Some("--") {
        cmd.remove(0);
    }
    if cmd.is_empty() {
        return Err(RBError::msg("E_USAGE", "rb journal run -- <command ...>: the command comes after --."));
    }
    let led = Ledger::open(None)?;
    // with --json, standard output carries the envelope alone: the command's own output goes to standard error
    let (rc, metrics, tail) = jr::run_reporting(&led, &cmd, if flag(ns, "json") { 2 } else { 1 })?;
    out.say(format!("exit {rc}. The command, its exit code and what it wrote are in the journal."));
    if !metrics.is_empty() {
        let mut ms: Vec<(&String, &Value)> = metrics.iter().collect();
        ms.sort_by(|a, b| a.0.cmp(b.0));
        out.say(
            "It printed its metrics: ".to_string()
                + &ms.iter().take(8).map(|(k, v)| format!("{k}={}", crate::pyfloat::fmt_num(v, "g"))).collect::<Vec<_>>().join(", ")
                + if metrics.len() > 8 { ", ..." } else { "" }
                + ". "
                // the command ran and is journaled whatever rb can say of it
                + &if rc != 0 {
                    "It failed, so rb reconcile proposes no run of them.".to_string()
                } else {
                    jr::printed_said(&led, &crate::cli::shlex_join(&cmd), &metrics).unwrap_or_else(|_| "rb reconcile says what it does with them.".into())
                },
        );
    }
    out.data.insert("exit".into(), Value::Int(rc as i64));
    out.data.insert("command".into(), Value::Str(crate::cli::shlex_join(&cmd)));
    out.data.insert("metrics".into(), Value::Dict(metrics));
    out.data.insert("tail".into(), Value::list_of_str(tail));
    out.next = vec!["rb reconcile".into()];
    Ok(if rc == 0 { 0 } else { EXIT_CHECK_FAILED })
}

// ---------------------------------------------------------------- rb reconcile

fn found_value(it: &Found) -> Value {
    crate::obj! {
        "id" => it.id.clone(), "path" => it.path.clone(), "at" => it.at.clone(), "metrics" => Value::Dict(it.metrics.clone()),
        "experiment" => it.experiment.clone(), "key" => it.key.clone(), "options" => Value::list_of_str(it.options.clone()),
        "argv" => Value::list_of_str(it.argv.clone()), "facts" => Value::list_of_str(it.facts.clone()), "run_in" => crate::sys::slashed(&it.here)
    }
}

/// The command that records a found item, as the user types it where they are: with `cd <its checkout> && ` first
/// where that is another directory, since its paths are from there.
fn typed(it: &Found) -> String {
    let line = format!("rb {}", crate::cli::shlex_join(&it.argv));
    let cwd = std::env::current_dir().map(|d| crate::sources::resolve_path(&d)).unwrap_or_default();
    if cwd == crate::sources::resolve_path(&it.here) {
        line
    } else {
        format!("cd {} && {line}", crate::places::shell_word(&crate::sys::slashed(&it.here)))
    }
}

/// Run `f` in `dir`, and come back to where rb was.
fn run_in<T>(dir: &std::path::Path, f: impl FnOnce() -> T) -> T {
    struct Back(Option<std::path::PathBuf>);
    impl Drop for Back {
        fn drop(&mut self) {
            if let Some(d) = self.0.take() {
                let _ = std::env::set_current_dir(d);
            }
        }
    }
    let was = std::env::current_dir().ok();
    let moved = was.as_ref().map_or(false, |w| crate::sources::resolve_path(w) != crate::sources::resolve_path(dir)) && std::env::set_current_dir(dir).is_ok();
    let _back = Back(if moved { was } else { None });
    f()
}

fn quote_line(q: &Quote, folder: &str) -> String {
    let label = match q.kind.as_str() {
        "title" => "title",
        "summary" => "says",
        "result" => "result",
        "limitation" => "limitation",
        _ => "next",
    };
    let text = if q.text.chars().count() <= 400 {
        q.text.clone()
    } else {
        format!("{} ...", crate::rbyaml::py_rstrip(&q.text.chars().take(400).collect::<String>()))
    };
    format!("  {label}: \"{text}\" ({})", repo::where_of(q, folder)) + if q.cut { " (continues in the file)" } else { "" }
}

/// The repository by its commit: a branch's name often names an agent, which rb's words never do (data.repository
/// keeps the branch).
fn head_line(plan: &repo::Plan) -> String {
    match &plan.commit {
        Some(c) => format!("The repository at {}", c.chars().take(7).collect::<String>()),
        None => "The repository (no commit yet)".to_string(),
    }
}

fn folders_line(plan: &repo::Plan) -> String {
    format!("{} results folder{}", plan.folders.len(), if plan.folders.len() == 1 { "" } else { "s" })
        + &if plan.guides.is_empty() { String::new() } else { format!(", {} reproduction guide{}", plan.guides.len(), if plan.guides.len() == 1 { "" } else { "s" }) }
}

/// Record each result reconcile placed without doubt, through the same rb run add anyone would type: (what was
/// recorded, what was not, each with why).
fn record_found(placed: &[Found]) -> (Vec<String>, Vec<String>) {
    let mut done = Vec::new();
    let mut not_done = Vec::new();
    for it in placed {
        let mut argv = it.argv.clone();
        argv.push("--json".into());
        // run where it was found, as anyone would type it there: its paths and its code are that checkout's
        let (code, text) = run_in(&it.here, || crate::cli::run_captured(argv));
        let env = pyjson::loads(&text).unwrap_or(Value::empty_dict());
        // the ref a run's code went into is said once, by this command, whose own write it is
        for told in env.get("data").and_then(|d| d.get("warnings")).and_then(Value::as_list).into_iter().flatten().map(pyjson::py_str) {
            if crate::sources::is_snapshot_ref_told(&told) {
                crate::plain::tell(told);
            }
        }
        let rid = env.get("data").and_then(|d| d.get("object")).and_then(|o| o.get("id")).filter(|v| v.truthy()).map(pyjson::py_str);
        let line = crate::cli::shlex_join(&it.argv);
        match rid {
            Some(r) if code == 0 => done.push(format!("{r}: rb {line}")),
            _ => {
                let msgs: Vec<String> = match env.get("errors") {
                    Some(Value::List(l)) => l.iter().map(|e| e.get("message").map(pyjson::py_str).unwrap_or_default()).collect(),
                    _ => Vec::new(),
                };
                not_done.push(format!("rb {line} ({})", msgs.join("; ")));
            }
        }
    }
    (done, not_done)
}

/// The changed files nothing cites, the results found, what rb cannot place and the folders it reads, each of which rb
/// reconcile's text lists at most this many of: --json lists every one.
const SHOWN_CHANGES: usize = 100;

pub fn cmd_reconcile(ns: &Namespace, out: &mut Out) -> R {
    let apply = flag(ns, "apply");
    let mut led = match Ledger::open(None) {
        Ok(l) => l,
        Err(e) if e.code == "E_NO_PROJECT" => return reconcile_unrecorded(ns, out),
        Err(e) => return Err(e),
    };
    let s = jr::scan(&led)?;
    let mut plan = repo::compile_repo(&led)?;
    let mut applied: Option<Dict> = None;
    // as its reader reads it: the user's view for the user, and a session's own briefing's for a session, so what a
    // session's apply recorded leaves its list and stays on the user's
    let view = jr::Seen::Briefing;
    if apply {
        // the changed files nothing cites, as this look found them: their paths go on the log line, recorded
        let seen = jr::found_all(&led, view)?.uncited;
        applied = Some(repo::apply(&led, &plan, &seen)?);
        led = Ledger::open(None)?;
    }
    let mut all = jr::found_all(&led, view)?;
    let mut recorded: Vec<String> = Vec::new();
    let mut not_recorded: Vec<String> = Vec::new();
    if applied.is_some() && !all.placed.is_empty() {
        (recorded, not_recorded) = record_found(&all.placed);
        led = Ledger::open(None)?;
        all = jr::found_all(&led, view)?;
        // a folder a run it recorded named is that experiment's now: the folders read as they are after it
        if !recorded.is_empty() {
            plan = repo::compile_repo(&led)?;
        }
    }
    let jr::FoundAll { placed, unplaced, uncited, uncited_cut, uncited_from_head, .. } = all;
    let gv = repo::git_view(&led.root);
    out.say(format!("{}: {}.", head_line(&plan), folders_line(&plan)));
    if let Some(a) = &applied {
        // the runs it recorded count in its headline with what it indexed
        let line = get_s(a, "line");
        let runs = (!recorded.is_empty()).then(|| format!("{} {} recorded", recorded.len(), plural(recorded.len(), "run", "runs")));
        let said = match runs {
            Some(r) if line == "nothing changed" => r,
            Some(r) => format!("{line} · {r}"),
            None => line,
        };
        out.say("");
        out.say(format!("Kept in the record: {said}."));
    }
    let mut reads = Dict::new();
    // the folders of results files alone that no run named: one counted line, each still read into --json
    let loose: Vec<String> = plan.folders.iter().filter(|fp| fp.unplaced()).map(|fp| fp.folder.clone()).collect();
    if !loose.is_empty() {
        out.say("");
        out.say(loose_line(&loose, "no recorded run named"));
    }
    let mut blocks = 0usize;
    for fp in &plan.folders {
        if fp.unplaced() {
            let read = repo::read_folder_of(&led, &FolderView::of_plan(fp), Some(&gv))?;
            reads.insert(fp.folder.clone(), Value::Dict(read));
            continue;
        }
        blocks += 1;
        let eid = fp.experiment.clone().unwrap_or_else(|| fp.id.clone());
        let exp: Option<Experiment> = if (applied.is_some() && fp.adds()) || fp.experiment.is_some() { Some(led.load(&eid)?) } else { None };
        let state = if !fp.read_by.is_empty() && fp.experiment.is_none() {
            format!("read by the runs of {}", crate::assess::first_three(&fp.read_by, ", "))
        } else if fp.experiment.is_none() && applied.is_none() {
            "new, not in the record".to_string()
        } else if fp.changes.is_empty() || applied.is_some() {
            "in the record".to_string()
        } else {
            format!("in the record; changed: {}", fp.changes.join("; "))
        };
        // a folder no experiment of its own stands for is named by its path alone, and keyed so in --json
        let own = fp.experiment.is_some() || fp.adds();
        let shown = blocks <= SHOWN_CHANGES;
        if shown {
            out.say("");
            out.say(if own { format!("{eid}  {}  {state}", fp.folder) } else { format!("{}  {state}", fp.folder) });
            for q in &fp.quotes {
                if q.kind == "command" {
                    continue;
                }
                out.say(quote_line(q, &fp.folder));
            }
        }
        let read = match exp.as_ref().filter(|e| !e.folder.is_empty()) {
            Some(e) => repo::read_folder(&led, e, Some(&gv))?,
            None => repo::read_folder_of(&led, &FolderView::of_plan(fp), Some(&gv))?,
        };
        if shown {
            out.say_all(repo::facts(&read).into_iter().map(|line| format!("  {line}")));
        }
        reads.insert(if own { eid } else { fp.folder.clone() }, Value::Dict(read));
    }
    if blocks > SHOWN_CHANGES {
        out.say("");
        out.say(format!("and {} more folders: rb reconcile --json reads every one", blocks - SHOWN_CHANGES));
    }
    if !plan.project_checks.is_empty() {
        out.say("");
        out.say("Checks the repository runs on no folder in particular:");
        for c in &plan.project_checks {
            let o = Value::Dict(repo::check_outcome(&led, c)?);
            let not_run = matches!(o.get("ok"), None | Some(Value::Null));
            out.say(
                format!("  {} · {}", c.path, gs(&o, "state"))
                    + &if not_run && !c.command.is_empty() { format!(" · run it: rb journal run -- {}", c.command) } else { String::new() },
            );
        }
    }
    if !recorded.is_empty() {
        out.say("");
        out.say(format!("Runs recorded from the repository ({}):", recorded.len()));
        out.say_all(recorded.iter().map(|r| format!("  {r}")));
    }
    if !not_recorded.is_empty() {
        out.say("");
        out.say(format!("Not recorded ({}):", not_recorded.len()));
        out.say_all(not_recorded.iter().map(|r| format!("  {r}")));
    }
    if !placed.is_empty() {
        out.say("");
        out.say(format!("Results in the repository, not in the record ({}):", placed.len()));
        for it in placed.iter().take(SHOWN_CHANGES) {
            let mut ms: Vec<(&String, &Value)> = it.metrics.iter().collect();
            ms.sort_by(|a, b| a.0.cmp(b.0));
            let what = format!("{}: ", it.path)
                + &ms.iter().take(4).map(|(k, v)| format!("{k}={}", crate::pyfloat::fmt_num(v, "g"))).collect::<Vec<_>>().join(", ")
                + if it.metrics.len() > 4 { ", ..." } else { "" };
            out.say(format!("  {what}"));
            out.say(format!("    {}", typed(it)));
        }
        if placed.len() > SHOWN_CHANGES {
            out.say(format!("  and {} more: rb reconcile --json lists every one", placed.len() - SHOWN_CHANGES));
        }
    }
    if !unplaced.is_empty() {
        out.say("");
        out.say(format!("Observed and unplaced ({}):", unplaced.len()));
        out.say_all(unplaced.iter().take(SHOWN_CHANGES).map(|l| format!("  {l}")));
        if unplaced.len() > SHOWN_CHANGES {
            out.say(format!("  and {} more: rb reconcile --json lists every one", unplaced.len() - SHOWN_CHANGES));
        }
    }
    if !uncited.is_empty() {
        out.say("");
        out.say(format!("Changed .json, .yaml and .toml files that nothing in the record cites ({}{}):", uncited.len(), if uncited_cut { " or more" } else { "" }));
        out.say_all(uncited.iter().take(SHOWN_CHANGES).map(|u| format!("  {}", u.said())));
        if uncited.len() > SHOWN_CHANGES {
            out.say(format!("  and {} more: rb reconcile --json lists every one", uncited.len() - SHOWN_CHANGES));
        }
    }
    // a count that starts at HEAD only because this clone lacks where it started says so, listed or not
    if let Some((head, gone)) = &uncited_from_head {
        out.say("");
        out.say(format!(
            "Changed .json, .yaml and .toml files are counted from {}. This clone does not hold {}, where the count started, or any commit before it.",
            head.chars().take(7).collect::<String>(),
            gone.chars().take(7).collect::<String>()
        ));
    }
    let usual = result_roots();
    // and any folder outside those that rb watches because a run named it
    let others: Vec<String> = jr::watch_roots(&led)
        .iter()
        .map(|r| {
            let rel = r.strip_prefix(&led.root).map(|p| p.to_string_lossy().replace('\\', "/")).unwrap_or_default();
            format!("{}/", if rel.is_empty() { ".".to_string() } else { rel })
        })
        .filter(|w| !usual.iter().any(|u| w.starts_with(u.as_str())))
        .collect();
    if plan.folders.is_empty() && placed.is_empty() && unplaced.is_empty() && uncited.is_empty() {
        let where_ = format!("the usual results folders ({})", usual.join(", ")) + &if others.is_empty() { String::new() } else { format!(" or {}", others.join(", ")) };
        out.say(
            format!("Nothing to compile in {where_}.")
                + if s.get("truncated").map_or(false, |v| v.truthy()) { " The walk hit its cap; some places were not looked at." } else { "" },
        );
    }
    let new: Vec<&String> = plan.folders.iter().filter(|fp| fp.adds()).map(|fp| &fp.id).collect();
    let changed: Vec<&String> = plan.folders.iter().filter(|fp| fp.experiment.is_some() && !fp.changes.is_empty()).map(|fp| &fp.id).collect();
    let pending = applied.is_none() && (!new.is_empty() || !changed.is_empty() || !placed.is_empty() || !uncited.is_empty());
    if pending {
        let mut parts: Vec<String> = Vec::new();
        if !new.is_empty() {
            parts.push(format!("{} experiment{} to add", new.len(), if new.len() == 1 { "" } else { "s" }));
        }
        if !changed.is_empty() {
            parts.push(format!("{} to re-index", changed.len()));
        }
        if !placed.is_empty() {
            parts.push(format!("{} {} to record", placed.len(), plural(placed.len(), "run", "runs")));
        }
        if uncited.len() > repo::MAX_RECORDED {
            parts.push(format!("the first {} of {} changed files to record by path", repo::MAX_RECORDED, uncited.len()));
        } else if !uncited.is_empty() {
            parts.push(format!("{} changed {} to record by path", uncited.len(), plural(uncited.len(), "file", "files")));
        }
        out.say("");
        out.say(format!("rb reconcile --apply keeps this in the record: {}.", parts.join(", ")));
    }
    out.data.insert("repository".into(), Value::Dict(repo::plan_as_dict(&plan)));
    out.data.insert("read".into(), Value::Dict(reads));
    out.data.insert("applied".into(), applied.map(Value::Dict).unwrap_or(Value::Null));
    out.data.insert("recorded".into(), Value::list_of_str(recorded));
    out.data.insert("not_recorded".into(), Value::list_of_str(not_recorded));
    out.data.insert("found".into(), Value::List(placed.iter().map(found_value).collect()));
    out.data.insert("unplaced".into(), Value::list_of_str(unplaced));
    out.data.insert("uncited_changes".into(), Value::List(uncited.iter().map(|u| u.to_value()).collect()));
    out.data.insert(
        "uncited_from_head".into(),
        uncited_from_head.map_or(Value::Null, |(head, gone)| crate::obj! {"commit" => head, "not_in_clone" => gone}),
    );
    out.data.insert("scan".into(), Value::Dict(s));
    out.data.insert("looked_in".into(), Value::list_of_str(usual.into_iter().chain(others)));
    // the next step never adds an experiment nobody declared: --apply is offered only while it would add none
    let mut next: Vec<String> = if pending && new.is_empty() { vec!["rb reconcile --apply".into()] } else { Vec::new() };
    next.extend(placed.iter().take(2).map(typed));
    out.next = next;
    Ok(0)
}

/// The results folders of results files alone, in one line: the first three by path and how many more, and the rule once.
fn loose_line(folders: &[String], what: &str) -> String {
    let rule = "rb run add <experiment> --from <its file> records a run from";
    match folders {
        [one] => format!("{one} holds results files {what}. With no report of its own it is no experiment: {rule} it."),
        many => format!(
            "{} results folders hold results files {what}: {}. With no report of its own none is an experiment: {rule} one.",
            many.len(),
            crate::assess::first_few(many, 3, ", ")
        ),
    }
}

/// The folders a repository's results are looked for in: results/, outputs/, out/ and the rest.
fn result_roots() -> Vec<String> {
    repo::RESULT_ROOTS.iter().map(|r| format!("{r}/")).collect()
}

/// rb reconcile in a repository with no record yet: read it all the same, and say what --apply would start. With
/// --apply, start the record at the repository's root, titled as the repository titles itself, and keep it there.
fn reconcile_unrecorded(ns: &Namespace, out: &mut Out) -> R {
    let cwd = std::env::current_dir().unwrap_or_default();
    let root = repo::repository_root(&cwd);
    let (title, title_from) = repo::repository_title(&root);
    let named = format!("\"{title}\"") + &if title_from.is_empty() { ", the folder's name".to_string() } else { format!(" ({title_from})") };
    if flag(ns, "apply") {
        let kept = crate::plain::set_aside();
        Ledger::init(&root, &title, None, None, None, false)?;
        crate::plain::put_back(kept); // the title is the repository's own words, not the writer's
        out.say(format!("Started the record in {}, titled {named}. Commit .rb/ with the code.", crate::sys::slashed(&root.join(DIR))));
        out.data.insert(
            "started".into(),
            crate::obj! {"root" => crate::sys::slashed(&root), "title" => title.clone(), "from" => title_from.clone()},
        );
        if !flag(ns, "no_agents") {
            let _ = super::record::set_up_agents(&root, out, flag(ns, "yes"))?;
        }
        out.say("");
        return cmd_reconcile(ns, out);
    }
    let blank = repo::Unrecorded { root: root.clone() };
    let plan = repo::compile_repo(&blank)?;
    let gv = repo::git_view(&root);
    out.say(format!("{}: {}. No record here yet.", head_line(&plan), folders_line(&plan)));
    let mut reads = Dict::new();
    let loose: Vec<String> = plan.folders.iter().filter(|fp| !fp.adds()).map(|fp| fp.folder.clone()).collect();
    if !loose.is_empty() {
        out.say("");
        out.say(loose_line(&loose, "nothing records yet"));
    }
    let mut blocks = 0usize;
    for fp in &plan.folders {
        if !fp.adds() {
            let read = repo::read_folder_of(&blank, &FolderView::of_plan(fp), Some(&gv))?;
            reads.insert(fp.folder.clone(), Value::Dict(read));
            continue;
        }
        blocks += 1;
        let read = repo::read_folder_of(&blank, &FolderView::of_plan(fp), Some(&gv))?;
        reads.insert(fp.id.clone(), Value::Dict(read.clone()));
        if blocks > SHOWN_CHANGES {
            continue;
        }
        out.say("");
        out.say(format!("{}  {}  new", fp.id, fp.folder));
        for q in &fp.quotes {
            if q.kind == "command" {
                continue;
            }
            out.say(quote_line(q, &fp.folder));
        }
        out.say_all(repo::facts(&read).into_iter().map(|line| format!("  {line}")));
    }
    if blocks > SHOWN_CHANGES {
        out.say("");
        out.say(format!("and {} more folders: rb reconcile --json reads every one", blocks - SHOWN_CHANGES));
    }
    if !plan.project_checks.is_empty() {
        out.say("");
        out.say("Checks the repository runs on no folder in particular:");
        for c in &plan.project_checks {
            out.say(format!("  {} · not run under rb's watch", c.path) + &if c.command.is_empty() { String::new() } else { format!(" · run it: rb journal run -- {}", c.command) });
        }
    }
    if let Some(there) = crate::elsewhere::record_elsewhere(&cwd) {
        // a worktree whose project's record is in another worktree: bring that record here, never start a second one
        out.say("");
        out.say(there.message());
        out.say(there.fix(false));
        out.next = Vec::new();
    } else if plan.folders.iter().any(|fp| fp.adds()) {
        let adds = plan.folders.iter().filter(|fp| fp.adds()).count();
        out.say("");
        out.say(format!("rb reconcile --apply starts the record here, titled {named}, and keeps this in it: {adds} experiment{} to add.", if adds == 1 { "" } else { "s" }));
        out.next = vec![format!("rb init \"{title}\""), "rb docs --start".into()];
    } else if !plan.folders.is_empty() {
        out.say("");
        out.say(format!("No results folder here holds a report, so rb reconcile --apply would add no experiment. Start the record: rb init \"{title}\""));
        out.next = vec![format!("rb init \"{title}\""), "rb docs --start".into()];
    } else {
        out.say(format!("Nothing to compile: no results folder ({}) holds a report or a results file yet.", result_roots().join(", ")));
        out.say("");
        out.say(format!("Start the record anyway: rb init \"{title}\""));
        out.next = vec![format!("rb init \"{title}\""), "rb docs --start".into()];
    }
    out.say("");
    out.say(crate::compiler::START_POINTER);
    out.data.insert("repository".into(), Value::Dict(repo::plan_as_dict(&plan)));
    out.data.insert("read".into(), Value::Dict(reads));
    out.data.insert("applied".into(), Value::Null);
    out.data.insert("recorded".into(), Value::List(Vec::new()));
    out.data.insert("not_recorded".into(), Value::List(Vec::new()));
    out.data.insert("found".into(), Value::List(Vec::new()));
    out.data.insert("unplaced".into(), Value::List(Vec::new()));
    out.data.insert("uncited_changes".into(), Value::List(Vec::new()));
    out.data.insert("uncited_from_head".into(), Value::Null);
    out.data.insert("scan".into(), Value::Null);
    out.data.insert("looked_in".into(), Value::list_of_str(result_roots()));
    out.data.insert("record".into(), Value::Null);
    out.data.insert(
        "would_start".into(),
        crate::obj! {"root" => crate::sys::slashed(&root), "title" => title.clone(), "from" => title_from.clone()},
    );
    Ok(0)
}
