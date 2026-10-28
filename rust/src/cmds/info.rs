//! The commands that say what rb is: rb docs, rb schema, and rb env check.

use super::*;
use indexmap::IndexMap;

type R = Res<i32>;

pub fn cmd_docs(ns: &Namespace, out: &mut Out) -> R {
    if flag(ns, "errors") {
        let mut table = Dict::new();
        for (code, meaning, fix) in crate::errors_table::ERRORS {
            out.say(format!("{code}: {meaning} → {fix}"));
            table.insert(code.to_string(), crate::obj! {"meaning" => *meaning, "fix" => *fix});
        }
        out.data = crate::dict! {"errors" => Value::Dict(table)};
        return Ok(0);
    }
    let text = if flag(ns, "start") { crate::skill::START_MD } else { crate::skill::AGENTS_MD };
    out.say(text.trim_end_matches('\n').to_string());
    out.data = crate::dict! {"markdown" => text};
    Ok(0)
}

/// What `rb schema <name>` prints and returns (`schemas.json`, written by rust/gen/gen_schemas.py).
pub fn cmd_schema(ns: &Namespace, out: &mut Out) -> R {
    let name = s_or(ns, "name");
    // the parser takes only the names the table has (a test holds the two together); one it lacks is a fault in this build
    let Some(entry) = crate::record::schemas().get(&name) else { return Err(RBError::fault(format!("this rb has no schema called {name}"))) };
    if let Some(Value::List(lines)) = entry.get("lines") {
        out.say_all(lines.iter().map(pyjson::py_str));
    }
    out.data = match entry.get("data") {
        Some(Value::Dict(d)) => d.clone(),
        _ => Dict::new(),
    };
    Ok(0)
}

/// Never blocks: exit 0, differences said plainly. The record's environments come from the receipts of the runs that
/// count; declared expectations come from `env.*` settings.
pub fn cmd_env_check(_ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let here = crate::environment::fingerprint(Some(&led.dir.join("cache")));
    let here_s = |k: &str| here.get(k).map(pyjson::py_str);
    let mut seen: IndexMap<String, IndexMap<String, Vec<String>>> = IndexMap::new();
    // a run made before it was recorded keeps the recorder's environment on its receipt, not the one it ran in
    let mut before: Vec<String> = Vec::new();
    for ev in led.standing_runs()?.iter() {
        if crate::ledger::made_before_recorded(ev) {
            before.push(ev.id.clone());
            continue;
        }
        for (key, value) in &ev.receipt.environment {
            seen.entry(key.clone()).or_default().entry(pyjson::py_str(value)).or_default().push(ev.id.clone());
        }
    }
    let mut declared: Vec<Dict> = Vec::new();
    let mut flags: Vec<String> = Vec::new();
    for exp in led.live::<Experiment>()? {
        let env: Vec<&Setting> = exp.settings.iter().filter(|st| st.name.starts_with("env.") && st.retracted.is_none()).collect();
        if env.is_empty() {
            continue;
        }
        let caveats = led.setting_caveats(&exp, led.has_origin(&exp)?, None)?;
        for st in env {
            let mut row = crate::dict! {"experiment" => exp.id.clone(), "name" => st.name.clone(), "value" => st.value.clone(), "status" => st.status.clone()};
            // flagged as rb show <experiment> flags it: what this read finds of a verified source, or a conflict on
            // record; keyed only when there is one
            let now = crate::ledger::source_problem(&caveats, &exp, &st.name);
            if let Some(c) = now {
                row.insert("caveats".into(), Value::List(vec![Value::Dict(c.dump(false))]));
            }
            flags.push(now.map(|c| format!(" · {}", c.code.to_uppercase())).unwrap_or_default());
            declared.push(row);
        }
    }
    let mut lines: Vec<String> = vec!["This machine:".into()];
    for (key, value) in &here {
        lines.push(format!("  {key} = {}", pyjson::py_str(value)));
    }
    let mut differs = 0;
    if !seen.is_empty() {
        lines.push("Against the record's runs:".into());
        let mut keys: Vec<&String> = seen.keys().collect();
        keys.sort();
        for key in keys {
            let values = &seen[key];
            let mut vs: Vec<&String> = values.keys().collect();
            vs.sort();
            for value in vs {
                let ids = &values[value];
                let same = here_s(key).as_deref() == Some(value.as_str());
                if !same {
                    differs += 1;
                }
                let shown = ids.iter().take(3).cloned().collect::<Vec<_>>().join(", ") + &if ids.len() > 3 { format!(" and {} more", ids.len() - 3) } else { String::new() };
                lines.push(
                    format!("  {} {key} = {value} on {shown}", if same { "=" } else { "≠" })
                        + &if same { String::new() } else { format!(" (here: {})", here_s(key).unwrap_or_else(|| "absent".into())) },
                );
            }
        }
    } else if before.is_empty() {
        lines.push("No runs on the record yet, so nothing to compare against.".into());
    }
    if !before.is_empty() {
        let ids = crate::assess::first_three(&before, ", ");
        lines.push(if before.len() == 1 {
            format!("Left out: 1 run made before it was recorded ({ids}): its receipt keeps the environment that recorded it, not the one it ran in.")
        } else {
            format!("Left out: {} runs made before they were recorded ({ids}): their receipts keep the environment that recorded them, not the ones they ran in.", before.len())
        });
    }
    if !declared.is_empty() {
        lines.push("Declared env.* settings:".into());
        for (d, flag) in declared.iter().zip(&flags) {
            let name = get_s(d, "name");
            let key: String = name.chars().skip(4).collect();
            let value = d.get("value").cloned().unwrap_or(Value::Null);
            let matched: Option<bool> = if value.is_null() { None } else { Some(here_s(&key).as_deref() == Some(pyjson::py_str(&value).as_str())) };
            let mark = match matched {
                Some(true) => "=",
                Some(false) => "≠",
                None => "·",
            };
            if mark == "≠" {
                differs += 1;
            }
            lines.push(
                format!("  {mark} {}/{name} = {} ({}{flag})", get_s(d, "experiment"), pyjson::py_str(&value), d.get("status").map(pyjson::py_str).unwrap_or_else(|| "None".into()))
                    + &if mark == "≠" { format!(" (here: {})", here_s(&key).unwrap_or_else(|| "absent".into())) } else { String::new() },
            );
        }
    }
    if differs > 0 {
        lines.push("Differences are facts, not errors: a rerun under a different environment is what the borderline".into());
    } else if !seen.is_empty() || !declared.is_empty() || before.is_empty() {
        lines.push("Nothing differs from what the record has seen.".into());
    }
    if differs > 0 {
        lines.push("markings exist for. Record your runs; the receipts keep both environments on the record.".into());
    }
    out.say_all(lines);
    let seen_v: Dict = seen
        .into_iter()
        .map(|(k, vs)| (k, Value::Dict(vs.into_iter().map(|(v, ids)| (v, Value::list_of_str(ids))).collect())))
        .collect();
    out.data = crate::dict! {
        "here" => Value::Dict(here.clone()), "seen" => Value::Dict(seen_v),
        "declared" => Value::List(declared.into_iter().map(Value::Dict).collect()), "differs" => differs,
        "made_before" => Value::list_of_str(before)
    };
    Ok(0)
}

pub fn cmd_compile(ns: &Namespace, out: &mut Out) -> R {
    crate::compiler::cmd_compile(&list(ns, "paths"), out)
}

/// rb ui: the user's screen in their own terminal. The screen is rb's Python package (it draws with textual); what it
/// shows it asks this rb for.
pub fn cmd_ui(_ns: &Namespace, _out: &mut Out) -> R {
    let Some(root) = crate::ledger::find_root(None) else {
        let err = crate::ledger::no_project(None);
        if err.message != RBError::new("E_NO_PROJECT").message {
            return Err(err); // the project's record is in another worktree
        }
        return Err(RBError::msg(
            "E_NO_PROJECT",
            format!("There is no record (.rb/) here or above. Your agent sets it up: paste this line into your agent's chat: {}", crate::skill::PASTE_LINE),
        ));
    };
    if crate::actor::agent_session_sign().is_some() {
        // A full-screen app would hang an agent driving a pty, and the user's controls are not the agent's to press.
        return Err(RBError::msg("E_USAGE", "rb ui is an interactive screen, and this is an agent session: rb status prints the same state."));
    }
    if !crate::cli::isatty(0) || !crate::cli::isatty(1) {
        return Err(RBError::msg("E_USAGE", "rb ui needs an interactive terminal. Without one, rb status prints the same state."));
    }
    crate::cli::open_screen(&root)
}

pub fn cmd_open(ns: &Namespace, _out: &mut Out) -> R {
    let root = crate::ledger::find_root(None).ok_or_else(|| crate::ledger::no_project(None))?;
    if crate::actor::detect_runtime().is_some() {
        // a serve loop would hang an agent, and this is the user's surface anyway
        return Err(RBError::msg(
            "E_USAGE",
            "rb open is the user's view and this is an agent session. The state is in rb status; the user opens it in their own terminal.",
        ));
    }
    // a number that is not a port is said as it was given, however large: it is never the default port
    let port = match arg(ns, "port") {
        Value::Null => crate::webui::DEFAULT_PORT,
        given => given.as_i64().filter(|p| (0..=65535).contains(p)).ok_or_else(|| crate::webui::not_a_port(&pyjson::py_str(given)))?,
    };
    crate::webui::serve(&root, port)
}
