//! The command registry: each command's implementation, by the name the declared command line gives it
//! (`func` in cli_parser.py). Every declared command is here: a test holds the two to each other.

use crate::argparse::Namespace;
use crate::cli::{Handler, Out};
use crate::cmds::record as r;
use crate::errors::Res;

pub const LEDGER_COMMANDS: &[&str] = &[
    "init", "project title", "question add", "question move", "question reword", "experiment add", "experiment file", "experiment relate", "experiment gate", "variant add",
    "note add", "metric add", "spec set", "spec verify", "spec vary", "criterion add", "criterion file", "criterion relate", "run add", "analysis add", "resource add",
    "resource locate", "resource expose", "freeze", "decide", "confirm", "retract", "status", "show", "compare", "log", "context", "fmt",
    "journal", "journal scan", "journal run", "reconcile", "compile", "page", "export", "clone", "sync", "login", "logout",
    "env check",
];

pub fn handler(func: &str) -> Option<Handler> {
    Some(match func {
        "cmd_version" => cmd_version,
        "cmd_telemetry" => cmd_telemetry,
        "cmd_skill" => crate::skill::cmd_skill,
        "cmd_remove" => crate::remove::cmd_remove,
        "cmd_init" => r::cmd_init,
        "cmd_project_title" => r::cmd_project_title,
        "cmd_question_add" => r::cmd_question_add,
        "cmd_question_move" => r::cmd_question_move,
        "cmd_question_reword" => r::cmd_question_reword,
        "cmd_experiment_add" => r::cmd_experiment_add,
        "cmd_experiment_file" => r::cmd_experiment_file,
        "cmd_experiment_relate" => r::cmd_experiment_relate,
        "cmd_experiment_gate" => r::cmd_experiment_gate,
        "cmd_note_add" => r::cmd_note_add,
        "cmd_variant_add" => r::cmd_variant_add,
        "cmd_metric_add" => r::cmd_metric_add,
        "cmd_spec_set" => r::cmd_spec_set,
        "cmd_spec_verify" => r::cmd_spec_verify,
        "cmd_spec_vary" => r::cmd_spec_vary,
        "cmd_criterion_add" => r::cmd_criterion_add,
        "cmd_criterion_file" => r::cmd_criterion_file,
        "cmd_criterion_relate" => r::cmd_criterion_relate,
        "cmd_analysis_add" => r::cmd_analysis_add,
        "cmd_run_add" => r::cmd_run_add,
        "cmd_resource_add" => r::cmd_resource_add,
        "cmd_resource_locate" => r::cmd_resource_locate,
        "cmd_resource_expose" => r::cmd_resource_expose,
        "cmd_retract" => r::cmd_retract,
        "cmd_freeze" => r::cmd_freeze,
        "cmd_decide" => r::cmd_decide,
        "cmd_confirm" => r::cmd_confirm,
        "cmd_status" => crate::cmds::reading::cmd_status,
        "cmd_show" => crate::cmds::reading::cmd_show,
        "cmd_compare" => crate::cmds::reading::cmd_compare,
        "cmd_log" => crate::cmds::reading::cmd_log,
        "cmd_context" => crate::cmds::reading::cmd_context,
        "cmd_fmt" => crate::cmds::upkeep::cmd_fmt,
        "cmd_doctor" => crate::cmds::upkeep::cmd_doctor,
        "cmd_journal" => crate::cmds::upkeep::cmd_journal,
        "cmd_journal_scan" => crate::cmds::upkeep::cmd_journal_scan,
        "cmd_journal_run" => crate::cmds::upkeep::cmd_journal_run,
        "cmd_reconcile" => crate::cmds::upkeep::cmd_reconcile,
        "cmd_docs" => crate::cmds::info::cmd_docs,
        "cmd_schema" => crate::cmds::info::cmd_schema,
        "cmd_env_check" => crate::cmds::info::cmd_env_check,
        "cmd_page" => crate::cmds::share::cmd_page,
        "cmd_export" => crate::cmds::share::cmd_export,
        "cmd_clone" => crate::cmds::share::cmd_clone,
        "cmd_compile" => crate::cmds::info::cmd_compile,
        "cmd_tour" => crate::cmds::tour::cmd_tour,
        "cmd_open" => crate::cmds::info::cmd_open,
        "cmd_ui" => crate::cmds::info::cmd_ui,
        "cmd_login" => crate::cmds::share::cmd_login,
        "cmd_logout" => crate::cmds::share::cmd_logout,
        "cmd_feedback" => crate::cmds::reading::cmd_feedback,
        "cmd_config_auto_update" => crate::ledger::updates::cmd_config,
        "cmd_sync" => crate::cmds::share::cmd_sync,
        _ => return None,
    })
}

fn cmd_version(_ns: &Namespace, out: &mut Out) -> Res<i32> {
    out.say(format!("rabbit-brain {}", crate::version()));
    out.data.insert("version".into(), crate::value::Value::Str(crate::version().into()));
    Ok(0)
}

fn cmd_telemetry(ns: &Namespace, out: &mut Out) -> Res<i32> {
    let action = crate::cli::ns_str(ns, "sub_command").unwrap_or_default();
    let value = match action.as_str() {
        "enable" => crate::telemetry::enable(crate::cli::ns_str(ns, "project_key").as_deref(), crate::cli::ns_str(ns, "host").as_deref())?,
        "disable" => crate::telemetry::disable()?,
        "preview" => crate::telemetry::preview(),
        "flush" => crate::telemetry::flush(),
        _ => crate::telemetry::status(),
    };
    if action == "enable" {
        out.say("Usage analytics enabled for this installation. Research content is never included.");
        if let Some(note) = value.get_str("reason").and_then(crate::telemetry::switch_note) {
            out.say(note);
        }
        out.say("Inspect pending events: rb telemetry preview. Stop and clear local analytics: rb telemetry disable.");
    }
    if action == "disable" {
        out.say("Usage analytics disabled. The local installation ID and pending events have been removed.");
    }
    out.say(crate::pyjson::dumps_with(&value, &crate::pyjson::Dumps::indent(2)).unwrap_or_default());
    out.data.insert("telemetry".into(), value);
    Ok(0)
}

#[cfg(test)]
mod tests {
    use crate::value::Value;

    fn funcs(parser: &Value, out: &mut Vec<String>) {
        if let Some(f) = parser.get("defaults").and_then(|d| d.get("func")).and_then(|f| f.as_str()) {
            out.push(f.to_string());
        }
        for action in parser.get("actions").and_then(|a| a.as_list()).into_iter().flatten() {
            for pair in action.get("parsers").and_then(|p| p.as_list()).into_iter().flatten() {
                if let Some(sub) = pair.as_list().and_then(|l| l.get(1)) {
                    funcs(sub, out);
                }
            }
        }
    }

    /// Every command the declared command line names (cli_spec.json, written from cli_parser.py) has its function
    /// here: a command added to the declarations and not to this registry would parse and then have nothing to run.
    #[test]
    fn every_declared_command_has_its_function() {
        let spec = crate::pyjson::loads(include_str!("cli_spec.json")).expect("cli_spec.json");
        let mut names = Vec::new();
        funcs(&spec, &mut names);
        assert!(names.len() > 40, "{} commands found in the spec", names.len());
        let missing: Vec<&String> = names.iter().filter(|f| super::handler(f).is_none()).collect();
        assert!(missing.is_empty(), "declared and not implemented: {missing:?}");
    }
}
