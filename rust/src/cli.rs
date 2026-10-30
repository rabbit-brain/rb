//! The `rb` command line. Every command accepts --json and prints exactly one JSON object on stdout; logs go to stderr.
//! The flow: parse with rb's argparse tree, run the command, print the envelope or the human lines. Every failure is
//! said the same way, a fault in rb's own code included (`guarded`): `rb <command>: <what happened>`, then the fix.

use crate::argparse::{self, Namespace, Stop};
use crate::errors::{RBError, Res, EXIT_CHECK_FAILED, EXIT_ENVIRONMENT, EXIT_INVALID, EXIT_OK};
use crate::pyjson::{self, Dumps};
use crate::value::{Dict, Value};
use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};

static PIPE_CLOSED: AtomicBool = AtomicBool::new(false);

/// Where standard error goes while rb runs one of its own command lines in-process.
#[derive(Clone, Copy, PartialEq)]
enum ErrTo {
    Terminal,
    Dropped,
    Kept,
}

struct Capture {
    out: String,
    err_to: ErrTo,
    err: String,
}

thread_local! {
    /// While rb runs one of its own command lines in-process (rb reconcile --apply recording a run, the tour in JSON, a
    /// tool call of rb mcp), what it prints on standard output is kept here; standard error goes to the terminal, nowhere,
    /// or here too, as each caller chooses.
    static CAPTURE: std::cell::RefCell<Option<Capture>> = const { std::cell::RefCell::new(None) };
    /// The command a command line ran, once it parsed: what decides the analytics delivery that follows it.
    static RAN: std::cell::RefCell<Option<String>> = const { std::cell::RefCell::new(None) };
}

fn ran(command: &str) {
    RAN.with(|r| *r.borrow_mut() = Some(command.to_string()));
}

/// The command the command line under way ran, as its words ("init", "skill add"), once it parsed.
pub fn command_ran() -> Option<String> {
    RAN.with(|r| r.borrow().clone())
}

fn capture_run(argv: Vec<String>, err_to: ErrTo) -> (i32, String, String) {
    let prev = CAPTURE.with(|c| c.replace(Some(Capture { out: String::new(), err_to, err: String::new() })));
    let outer = RAN.with(|r| r.borrow_mut().take());
    let code = run_whole(argv); // a fault anywhere in it is said into what is kept, and what was kept before comes back
    RAN.with(|r| *r.borrow_mut() = outer);
    let got = CAPTURE.with(|c| c.replace(prev));
    let (out, err) = got.map(|g| (g.out, g.err)).unwrap_or_default();
    (code, out, err)
}

/// One rb command line run in this process, as `main` runs it, with what it printed on standard output; standard
/// error dropped.
pub fn run_captured(argv: Vec<String>) -> (i32, String) {
    run_capturing(argv, true)
}

/// The same, with standard error dropped or left to the terminal.
pub fn run_capturing(argv: Vec<String>, drop_stderr: bool) -> (i32, String) {
    let (code, out, _) = capture_run(argv, if drop_stderr { ErrTo::Dropped } else { ErrTo::Terminal });
    (code, out)
}

/// The same, with what it printed on standard error kept too: (exit code, stdout, stderr).
pub fn run_capturing_all(argv: Vec<String>) -> (i32, String, String) {
    capture_run(argv, ErrTo::Kept)
}

/// Whether rb is running one of its own command lines in this process, whose standard output it keeps.
pub fn capturing() -> bool {
    CAPTURE.with(|c| c.borrow().is_some())
}

/// Write to stdout; a reader that left early (rb ... | head) is not an error.
pub fn stdout_write(text: &str) {
    let kept = CAPTURE.with(|c| match c.borrow_mut().as_mut() {
        Some(cap) => {
            cap.out.push_str(text);
            true
        }
        None => false,
    });
    if kept {
        return;
    }
    if PIPE_CLOSED.load(Ordering::Relaxed) {
        return;
    }
    let mut o = std::io::stdout().lock();
    if o.write_all(text.as_bytes()).and_then(|_| o.flush()).is_err() {
        PIPE_CLOSED.store(true, Ordering::Relaxed);
    }
}

/// Write to stderr, or where an in-process run sends it.
pub fn stderr_write(text: &str) {
    let to = CAPTURE.with(|c| match c.borrow_mut().as_mut() {
        Some(cap) if cap.err_to == ErrTo::Kept => {
            cap.err.push_str(text);
            ErrTo::Kept
        }
        Some(cap) => cap.err_to,
        None => ErrTo::Terminal,
    });
    if to == ErrTo::Terminal {
        let mut e = std::io::stderr().lock();
        let _ = e.write_all(text.as_bytes());
    }
}

pub fn println_out(text: &str) {
    stdout_write(&format!("{text}\n"));
}

pub fn eprintln_err(text: &str) {
    stderr_write(&format!("{text}\n"));
}

// ---------------------------------------------------------------- the planned commands

pub const PLANNED: &[(&str, &str)] = &[
    ("paper", "rb has no paper command yet. To record a paper by hand: save its text in the repository, then rb criterion add \"...\" -e <exp> -q <question> --metric <m> --equals <x> --tolerance <t> --source papers/<id>.txt --quote \"...\" --locator \"Table 1\"; rb spec set <exp> <name> --unknown for what it does not state."),
    ("rerun", "rb has no rerun command: run the evaluation again, then record it with rb run add (rb show <experiment> lists what a rerun needs)."),
    ("serve", "rb has no server beyond rb open: rb status, rb show <id> and rb compare <experiment> read the same record."),
    ("reproduce", "rb has no reproduce command: rb clone <bundle> starts from someone's record, and rb experiment add --like <experiment> copies a spec to run again."),
    ("publish", "rb does not publish: rb export writes the page, and with --with-record the record too, for you to host anywhere."),
];

// ---------------------------------------------------------------- output

/// Collects human lines or a JSON envelope; prints one or the other at the end.
pub struct Out {
    pub command: String,
    pub json_mode: bool,
    pub lines: Vec<String>,
    pub data: Dict,
    pub next: Vec<String>,
    pub warnings: Vec<String>,
    /// What the command's writes call for after them (LaTeX in an agent's new text): a line after the command's own,
    /// and with --json among `data.warnings`.
    pub notes: Vec<String>,
    pub delta: Vec<Value>,
    /// What a quiet command (rb status, rb context) printed of what rb_delta tells, by key: only that counts as told.
    pub told: Option<Vec<String>>,
    /// The log lines a quiet command printed, by digest: only those writes count as told.
    pub told_lines: Option<Vec<String>>,
    /// A write previewed with --dry-run: what it would write, in rb's text, said after the command's own lines.
    pub dry: Option<Vec<String>>,
    /// What a command previewed says of itself in place of the files' contents (rb init --dry-run's plan).
    pub plan: Option<Vec<String>>,
    /// Whether its lines, the commands after them and its warnings are drawn as `design::shown` draws text, in a record
    /// where something a session wrote can wait for the user (`Ledger::shows_escapes`), once worked out: only where what
    /// it draws holds a character a screen would hide. --json keeps the text as written.
    shown: Option<bool>,
}

impl Out {
    pub fn new(command: &str, json_mode: bool) -> Out {
        Out {
            command: command.to_string(),
            json_mode,
            lines: Vec::new(),
            data: Dict::new(),
            next: Vec::new(),
            warnings: Vec::new(),
            notes: Vec::new(),
            delta: Vec::new(),
            told: None,
            told_lines: None,
            dry: None,
            plan: None,
            shown: None,
        }
    }

    /// Once its command has said them: its lines and the commands after them drawn as every screen of `led` draws text,
    /// with what a screen would hide escaped where something a session wrote can wait for the user there. Whether it can
    /// is worked out only where they hold such a character, and never for --json, which draws nothing.
    pub fn shows_for(&mut self, led: &crate::ledger::Ledger) {
        if !self.json_mode && self.lines.iter().chain(&self.next).any(|l| crate::ledger::design::draws_escapes(l)) {
            self.shown_by(led);
        }
    }

    /// As shows_for, worked out now, whatever it draws: before a write changes what waits.
    pub fn shows_now(&mut self, led: &crate::ledger::Ledger) {
        self.shown_by(led);
    }

    fn shown_by(&mut self, led: &crate::ledger::Ledger) {
        if self.shown.is_none() {
            self.shown = Some(led.shows_escapes().unwrap_or(false));
        }
    }

    /// A warning about `led`'s record, drawn as its screens draw text; data.warnings keeps it as written.
    pub fn warn_of(&mut self, led: &crate::ledger::Ledger, text: &str) {
        if crate::ledger::design::draws_escapes(text) {
            self.shown_by(led);
        }
        self.warn(text);
    }

    pub fn say<S: Into<String>>(&mut self, text: S) {
        self.lines.push(text.into());
    }

    pub fn say_all<I: IntoIterator<Item = S>, S: Into<String>>(&mut self, items: I) {
        for s in items {
            self.lines.push(s.into());
        }
    }

    pub fn warn(&mut self, text: &str) {
        self.warnings.push(text.to_string());
        let drawn = if self.shown == Some(true) { crate::ledger::design::shown(text) } else { text.to_string() };
        eprintln_err(&format!("rb {}: {}", self.command, drawn));
    }

    pub fn emit(&mut self, ok: bool, errors: Vec<Value>) {
        if self.json_mode {
            let mut data = self.data.clone();
            if !self.warnings.is_empty() || !self.notes.is_empty() {
                data.insert("warnings".into(), Value::list_of_str(self.warnings.iter().chain(&self.notes).cloned()));
            }
            let env = crate::obj! {
                "ok" => ok,
                "rb_version" => crate::version(),
                "command" => self.command.clone(),
                "data" => Value::Dict(data),
                "errors" => Value::List(errors),
                "next" => Value::list_of_str(self.next.clone()),
                "rb_delta" => Value::List(self.delta.clone()),
            };
            println_out(&pyjson::dumps_with(&env, &Dumps::indent(1)).unwrap_or_default());
        } else {
            let said = !self.lines.is_empty() || !self.notes.is_empty();
            if self.dry.is_some() {
                println_out(if said { "Dry run: nothing was written. The write would say:" } else { "Dry run: nothing was written." });
            }
            if !self.lines.is_empty() {
                let shown = self.shown == Some(true);
                let lines: Vec<String> = if shown { self.lines.iter().map(|l| crate::ledger::design::shown(l)).collect() } else { self.lines.clone() };
                println_out(&lines.join("\n"));
            }
            for note in &self.notes {
                println_out(&format!("  {note}"));
            }
            if let Some(dry) = &self.dry {
                println_out(&format!("{}{}", if said { "\n" } else { "" }, dry.join("\n")));
            }
            if !self.delta.is_empty() {
                println_out(&crate::delta::lines(&self.delta).join("\n"));
            }
            if !self.next.is_empty() {
                let next = self.next.join("  ·  ");
                println_out(&format!("\nNext: {}", if self.shown == Some(true) { crate::ledger::design::shown(&next) } else { next }));
            }
        }
    }
}

// ---------------------------------------------------------------- namespace access

pub fn ns_str(ns: &Namespace, k: &str) -> Option<String> {
    ns.get(k).and_then(|v| v.as_str()).map(|s| s.to_string())
}
pub fn ns_bool(ns: &Namespace, k: &str) -> bool {
    ns.get(k).map_or(false, |v| v.truthy())
}
pub fn ns_f64(ns: &Namespace, k: &str) -> Option<f64> {
    ns.get(k).and_then(|v| v.as_f64())
}
pub fn ns_i64(ns: &Namespace, k: &str) -> Option<i64> {
    ns.get(k).and_then(|v| v.as_i64())
}
pub fn ns_list(ns: &Namespace, k: &str) -> Vec<String> {
    match ns.get(k) {
        Some(Value::List(l)) => l.iter().map(|v| v.as_str().map(|s| s.to_string()).unwrap_or_else(|| pyjson::py_str(v))).collect(),
        _ => Vec::new(),
    }
}
pub fn ns_opt_list(ns: &Namespace, k: &str) -> Option<Vec<String>> {
    match ns.get(k) {
        Some(Value::List(_)) => Some(ns_list(ns, k)),
        _ => None,
    }
}

// ---------------------------------------------------------------- shlex

/// shlex.quote(s)
pub fn shlex_quote(s: &str) -> String {
    if s.is_empty() {
        return "''".into();
    }
    if s.chars().all(|c| c.is_ascii_alphanumeric() || "_@%+=:,./-".contains(c)) {
        return s.to_string();
    }
    format!("'{}'", s.replace('\'', "'\"'\"'"))
}

pub fn shlex_join<S: AsRef<str>>(parts: &[S]) -> String {
    parts.iter().map(|p| shlex_quote(p.as_ref())).collect::<Vec<_>>().join(" ")
}

// ---------------------------------------------------------------- help

pub const DESCRIPTION: &str = "The record of the experiments your coding agents run, as Rabbit Brain YAML files in your repo: questions, experiments with their hypothesis, variants and criteria, runs with receipts, and the resources they used. Agents record; rb checks and computes findings; you decide. Documentation: rb docs. New here? Run rb tour.";

pub const HELP_GROUPS: &[(&str, &[&str])] = &[
    ("The record", &["init", "project", "question", "experiment", "variant", "note", "metric", "spec", "criterion", "run", "analysis", "resource"]),
    ("Calls", &["freeze", "decide", "confirm", "retract"]),
    ("Reading it", &["open", "ui", "status", "show", "compare", "log", "context", "page"]),
    ("Carrying it", &["export", "clone", "env"]),
    ("For agents", &["mcp", "skill", "journal", "reconcile", "compile"]),
    ("Upkeep", &["doctor", "fmt", "telemetry", "config"]),
    ("Leaving", &["remove"]),
    ("Reference", &["tour", "docs", "schema", "version", "feedback"]),
    ("rabbitbrain.ai", &["login", "logout", "sync"]),
];

pub fn print_help() {
    let top = argparse::rb_parser();
    let helps: std::collections::HashMap<String, Option<String>> = top.subcommands().into_iter().collect();
    let mut lines: Vec<String> = vec!["usage: rb <command> [...]".into(), String::new(), DESCRIPTION.into(), String::new()];
    for (title, names) in HELP_GROUPS {
        lines.push(format!("{title}:"));
        for n in *names {
            let h = helps.get(*n).cloned().flatten().unwrap_or_default();
            lines.push(format!("  {}{}", crate::pyfloat::fmt_str(n, "<12"), if h.is_empty() { String::new() } else { format!(" {h}") }));
        }
        lines.push(String::new());
    }
    lines.push("rb <command> --help for its arguments. Start: rb init \"<the work's theme>\", then rb question add for each question the work asks, and rb context.".into());
    println_out(&lines.join("\n"));
}

// ---------------------------------------------------------------- the Python beside rb: the screen, a run's environment

/// The Python interpreter that has rabbit_brain: RB_PYTHON, else the one beside this binary, else python3 on PATH.
/// On Windows pip puts rb in the environment's Scripts\ folder: python.exe is beside it in a virtual environment and
/// one folder up otherwise.
pub fn python() -> Option<std::path::PathBuf> {
    if let Ok(p) = std::env::var("RB_PYTHON") {
        if !p.is_empty() {
            return Some(p.into());
        }
    }
    // the rb someone runs is often a link into the environment that holds it (pipx, uv tool): look beside the real one
    if let Ok(exe) = std::env::current_exe().map(|p| crate::sys::canonicalize(&p).unwrap_or(p)) {
        let beside = exe.parent();
        let above = beside.and_then(|d| d.parent()).filter(|_| cfg!(windows));
        for dir in [beside, above].into_iter().flatten() {
            for name in crate::sys::PYTHON_NAMES {
                let p = dir.join(name);
                if p.is_file() {
                    return Some(p);
                }
            }
        }
    }
    for name in crate::sys::PYTHON_NAMES {
        if let Some(paths) = std::env::var_os("PATH") {
            for d in std::env::split_paths(&paths) {
                let p = d.join(name);
                if p.is_file() {
                    return Some(p);
                }
            }
        }
    }
    None
}

/// What starts the screen: rb's Python package, or one line saying why this Python cannot load it.
const SCREEN: &str = r#"import sys
try:
    from rabbit_brain.tui import main
except ImportError as exc:
    sys.stderr.write("rb ui: the screen is drawn by rb's Python package, and " + sys.executable + " cannot load it (" + str(exc) + ").\n"
                     "  \u2192 Install it there: pip install \"rabbit-brain==@VERSION@\". Or set RB_PYTHON to the Python that has it.\n")
    sys.exit(3)
sys.exit(main(sys.argv[1:]))
"#;

/// Open the user's screen on the record at `root`, and wait for it to close. The screen asks this rb for what it
/// draws (RB_BIN names it), and runs rb confirm as the user would type it in the checkout the user is in (the worktree
/// nested in the record's checkout, where they are in one); what it records, it records where rb found it. While it
/// is open this rb holds a folder for the past versions it shows (RB_VIEWS), and takes it away when it closes. An
/// install that cannot start the screen is said as any failure is, with what to install.
pub fn open_screen(root: &std::path::Path) -> Res<i32> {
    let cannot = |what: String| {
        RBError::msg_fix("E_INTERNAL", format!("the screen is drawn by rb's Python package, and {what}."), format!("Install it: pip install \"rabbit-brain=={}\". Or set RB_PYTHON to the Python that has it.", crate::version()))
            .with_exit(EXIT_ENVIRONMENT)
    };
    let Some(py) = python() else { return Err(cannot("no Python was found".into())) };
    let here = std::env::current_dir().map(|d| crate::ledger::checkout_of(&d, root)).unwrap_or_else(|_| root.to_path_buf());
    let mut cmd = std::process::Command::new(&py);
    cmd.arg("-c").arg(SCREEN.replace("@VERSION@", crate::version())).arg(root).arg(&here);
    let views = crate::places::Owner::start(root);
    if let Some(v) = &views {
        cmd.env("RB_VIEWS", v.token());
    }
    if cfg!(windows) && std::env::var_os("PYTHONUTF8").is_none() {
        cmd.env("PYTHONUTF8", "1"); // rb's own Python code, reading what rb writes: UTF-8, whatever the system's code page
    }
    if std::env::var_os("RB_BIN").is_none() {
        if let Ok(exe) = std::env::current_exe() {
            cmd.env("RB_BIN", exe);
        }
    }
    // a request to end, a terminal that closed or Ctrl-C ends the screen, then this rb, once the folder is gone
    let endings = crate::sys::catch_endings();
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return Err(cannot(format!("{} could not be started ({})", crate::sys::slashed(&py), crate::sys::os_words(&e)))),
    };
    let mut passed = false;
    let status = loop {
        match child.try_wait() {
            Ok(Some(st)) => break Some(st),
            Ok(None) => {
                if crate::sys::interrupted() && !passed {
                    crate::sys::pass_on(&child);
                    passed = true;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Err(_) => break child.wait().ok(),
        }
    };
    drop(endings);
    drop(views);
    if crate::sys::interrupted() {
        crate::sys::end_interrupted();
    }
    Ok(status.and_then(|st| st.code()).unwrap_or(EXIT_ENVIRONMENT))
}

// ---------------------------------------------------------------- the commands

pub type Handler = fn(&Namespace, &mut Out) -> Res<i32>;

/// A command's implementation, by the name the declared command line gives it.
pub fn handler(func: &str) -> Option<Handler> {
    crate::commands::handler(func)
}

/// The commands that carry rb_delta in an agent session.
pub fn delta_command(command: &str) -> bool {
    crate::commands::LEDGER_COMMANDS.contains(&command) && !matches!(command, "init" | "clone" | "fmt" | "login" | "logout" | "sync")
}

thread_local! {
    /// What the fault said and where, while a guard on this thread is waiting for it.
    static FAULT: std::cell::RefCell<Option<String>> = const { std::cell::RefCell::new(None) };
    static GUARDS: std::cell::Cell<u32> = const { std::cell::Cell::new(0) };
}
static FAULT_HOOK: std::sync::Once = std::sync::Once::new();

/// The two lines every failure gets, for one said outside the command runner.
fn failure_lines(command: &str, err: &RBError) -> String {
    format!("rb{}{}: {}\n  → {}\n", if command.is_empty() { "" } else { " " }, command, err.message, err.fix)
}

/// Run `f`. A fault in rb's own code (a panic) comes back as the E_INTERNAL it is, to be said as every failure is
/// said, instead of the runtime's own report of it. On a thread with no guard the fault is still said in rb's words.
pub fn guarded<T>(f: impl FnOnce() -> T) -> Res<T> {
    FAULT_HOOK.call_once(|| {
        std::panic::set_hook(Box::new(|info| {
            let payload = info.payload();
            let what = payload.downcast_ref::<&str>().map(|s| s.to_string()).or_else(|| payload.downcast_ref::<String>().cloned()).unwrap_or_else(|| "no message".into());
            let at = info.location().map(|l| format!(" ({}:{})", l.file().replace('\\', "/"), l.line())).unwrap_or_default();
            let text = format!("{what}{at}");
            // the report link says where in rb's own source the fault happened and a fingerprint of what it said
            crate::ledger::report_link::note(&what, info.location().map(|l| l.file()), info.location().map_or(0, |l| l.line()));
            if GUARDS.with(|g| g.get()) > 0 {
                FAULT.with(|f| *f.borrow_mut() = Some(text));
            } else {
                stderr_write(&failure_lines("", &crate::ledger::report_link::reported(RBError::fault(text), "")));
            }
        }));
    });
    GUARDS.with(|g| g.set(g.get() + 1));
    let got = std::panic::catch_unwind(std::panic::AssertUnwindSafe(f));
    GUARDS.with(|g| g.set(g.get().saturating_sub(1)));
    got.map_err(|_| RBError::fault(FAULT.with(|f| f.borrow_mut().take()).unwrap_or_else(|| "no message".into())))
}

/// A failure with no command to name: the two lines, or the envelope when --json was asked for.
fn say_bare(err: &RBError, json_mode: bool) -> i32 {
    let err = &crate::ledger::report_link::reported(err.clone(), "");
    if json_mode {
        Out::new("rb", true).emit(false, vec![err.to_value()]);
    } else {
        stderr_write(&failure_lines("", err));
    }
    err.exit_code
}

/// One command line, start to finish. A fault outside a command's own guard (reading the command line, working out
/// what changed since the session last looked, printing) is said here, with no command to name.
fn run_whole(argv: Vec<String>) -> i32 {
    let json_mode = argv.iter().any(|a| a == "--json");
    match guarded(|| run(argv)) {
        Ok(code) => code,
        Err(err) => say_bare(&err, json_mode),
    }
}

pub fn main(argv: Vec<String>) -> i32 {
    // the analytics controls never upload, wherever the flags sit before their command word
    let local_control = argv.iter().find(|a| !a.starts_with('-')).map_or(false, |s| s == "telemetry");
    // a command line run inside another (rb tour's steps) gives the outer one its own command back after it
    let outer = RAN.with(|r| r.borrow_mut().take());
    let code = run_whole(argv);
    let inner = RAN.with(|r| std::mem::replace(&mut *r.borrow_mut(), outer));
    match inner.as_deref() {
        // a command line that did not parse uploads nothing, and rb mcp delivers as its session ends
        _ if local_control => {}
        None | Some("mcp") => {}
        Some("ui" | "open") => {
            let _ = guarded(crate::telemetry::flush_at_end);
        }
        Some(_) => {
            let _ = guarded(crate::telemetry::flush_if_due);
        }
    }
    if PIPE_CLOSED.load(Ordering::Relaxed) {
        return EXIT_OK;
    }
    code
}

/// rb as the system starts it. An argument that is not text (bytes that are not UTF-8) names nothing rb can read, so
/// it is a usage error, said before anything runs.
pub fn main_os(args: Vec<std::ffi::OsString>) -> i32 {
    let mut argv = Vec::with_capacity(args.len());
    let mut not_text: Option<String> = None;
    for a in args {
        match a.into_string() {
            Ok(s) => argv.push(s),
            Err(raw) => not_text = not_text.or(Some(raw.to_string_lossy().into_owned())),
        }
    }
    match not_text {
        None => main(argv),
        Some(shown) => {
            let err = RBError::msg_fix("E_USAGE", format!("An argument is not UTF-8 text: {shown}"), "Give it as text. A file whose name is not UTF-8 needs another name first.");
            say_bare(&err, argv.iter().any(|a| a == "--json"))
        }
    }
}

fn run(argv: Vec<String>) -> i32 {
    crate::ledger::forget_worked(); // what an earlier command line worked out is not this one's
    let json_mode = argv.iter().any(|a| a == "--json");
    let top = argparse::rb_parser();
    if argv.is_empty() || argv[0] == "-h" || argv[0] == "--help" {
        if argv.is_empty() && isatty(0) && isatty(1) && crate::actor::detect_runtime().is_none() {
            if let Some(root) = crate::ledger::find_root(None) {
                // bare rb in a project, in the user's terminal: their screen, the same operation as rb ui
                ran("ui");
                let (surface, actor) = crate::usage::cli_surface("ui");
                let usage = crate::usage::Operation::start("ui", surface, actor, false);
                let code = open_screen(&root).unwrap_or_else(|err| {
                    stderr_write(&failure_lines("ui", &err));
                    err.exit_code
                });
                let _ = guarded(|| usage.finish(code, false, None, false, false));
                return code;
            }
        }
        print_help();
        return EXIT_OK;
    }
    if argv[0] == crate::ledger::updates::WORKER {
        return crate::ledger::updates::work(); // the background check for a newer rb: in no help text
    }
    if argv[0] == "bridge" {
        return crate::bridge::serve(&argv[1..]); // rb's Python package asking rb (the SDK, the terminal UI): in no help text
    }
    if argv[0] == "mcp" && !argv.iter().any(|a| a == "-h" || a == "--help") {
        return match top.parse_args(&argv) {
            Ok(ns) => {
                ran("mcp");
                crate::mcp::serve(ns_str(&ns, "agent"))
            }
            Err(Stop::Usage(u)) => {
                eprintln_err(&format!("rb mcp: {}", u.message));
                EXIT_INVALID
            }
            Err(Stop::Exit(code, text)) => {
                stdout_write(&text);
                code
            }
        };
    }
    if let Some((_, fix)) = PLANNED.iter().find(|(k, _)| *k == argv[0]) {
        let mut out = Out::new(&argv[0], json_mode);
        let err = RBError::msg_fix("E_NOT_AVAILABLE", format!("`rb {}` is not part of rabbit-brain {}.", argv[0], crate::version()), *fix);
        if !json_mode {
            eprintln_err(&format!("rb {}: {}", argv[0], err.message));
            eprintln_err(&format!("  → {}", err.fix));
        }
        out.emit(false, vec![err.to_value()]);
        return EXIT_INVALID;
    }
    let ns = match top.parse_args(&argv) {
        Ok(ns) => ns,
        Err(Stop::Exit(code, text)) => {
            stdout_write(&text);
            return code;
        }
        Err(Stop::Usage(u)) => {
            let command: String = argv.iter().take(2).filter(|a| !a.starts_with('-')).cloned().collect::<Vec<_>>().join(" ");
            let command = if command.is_empty() { "rb".to_string() } else { command };
            let err = RBError::msg("E_USAGE", u.message.clone());
            if json_mode {
                Out::new(&command, true).emit(false, vec![err.to_value()]);
            } else {
                eprintln_err(u.usage.trim_end_matches('\n'));
                eprintln_err(&format!("rb {}: {}", command, u.message));
            }
            return EXIT_INVALID;
        }
    };
    let cmd = ns_str(&ns, "command").unwrap_or_default();
    let func = ns_str(&ns, "func").unwrap_or_default();
    let mut parts = vec![cmd.clone()];
    if let Some(s) = ns_str(&ns, "sub_command") {
        parts.push(s);
    }
    let command = parts.join(" ");
    ran(&command);
    // an agent's session hook looking once (rb journal scan --hook <agent>, which rb init installs): it says nothing and
    // exits 0 whatever happens, starts no check, sync or count, and tells the session nothing, since what it told would
    // reach no one and would be told to the session as said
    if command == "journal scan" {
        if let Some(agent) = ns_str(&ns, "hook") {
            let _ = guarded(|| crate::journal::hook_look(&agent));
            // and no analytics delivery after it: main delivers only after a command it ran
            RAN.with(|r| *r.borrow_mut() = None);
            return EXIT_OK;
        }
    }
    // at most once a day, a background check for a newer rb (layer 3 of updates without the user): never a delay
    if !ns_bool(&ns, "dry_run") {
        let _ = guarded(crate::ledger::updates::start_check);
    }
    crate::sources::forget_reads();
    let (surface, actor) = crate::usage::cli_surface(&command);
    let usage = crate::usage::Operation::start(&command, surface, actor, false);
    let mut out = Out::new(&command, ns_bool(&ns, "json"));
    crate::ledger::set_words(ns_str(&ns, "words"));
    // --dry-run: the write runs every check it makes, and what it would write is kept aside, never written (rb remove's
    // own --dry-run shows its plan, and writes nothing to keep aside)
    let dry = ns_bool(&ns, "dry_run") && cmd != "remove";
    if dry {
        crate::dry::begin();
    }
    // a command line run inside another (rb reconcile --apply recording a run) keeps its notes, and leaves the other's
    let enclosing = crate::plain::set_aside();
    let result = match handler(&func) {
        Some(h) => guarded(|| {
            // RB_TEST_FAULT=<text> makes a command stop on a fault of rb's own, as RB_TEST_NOW fixes the clock: the
            // tests hold how one is said
            if let Some(text) = std::env::var("RB_TEST_FAULT").ok().filter(|t| !t.is_empty()) {
                panic!("{text}");
            }
            h(&ns, &mut out)
        })
        .and_then(|r| r),
        // the command line declares it (cli_spec.json) and this build has no code for it: a build fault, never the
        // user's mistake (a test holds the registry to the declarations)
        None => Err(RBError::msg_fix("E_INTERNAL", format!("this rb has no code for the command ({func})."), format!("Reinstall rb: pip install --force-reinstall \"rabbit-brain=={}\".", crate::version()))
            .with_exit(EXIT_ENVIRONMENT)),
    };
    crate::ledger::set_words(None);
    if dry {
        if let Some(preview) = crate::dry::end() {
            // a record rb init would start is not on disk: its paths are said from where it would be
            let root = crate::ledger::find_root(None).or_else(|| (cmd == "init").then(|| std::env::current_dir().unwrap_or_default()));
            out.data.insert("dry_run".into(), Value::Bool(true));
            out.data.insert("would_write".into(), crate::dry::would_write(&preview, root.as_deref()));
            out.dry = Some(match out.plan.take().filter(|_| result.is_ok()) {
                Some(plan) => plan,
                None => crate::dry::said(&preview, root.as_deref(), result.is_err()),
            });
        }
    }
    let latex = crate::plain::take_note();
    crate::plain::put_back(enclosing);
    match result {
        Ok(code) => {
            out.notes.extend(latex);
            // a preview tells nothing of what changed since the session last looked, and remembers nothing of it
            if delta_command(&command) && !dry {
                out.delta = crate::delta::for_command(&command, out.told.as_deref(), out.told_lines.as_deref());
            }
            out.emit(code == EXIT_OK || code == EXIT_CHECK_FAILED, Vec::new());
            // for rb installed with pip, one quiet line at most once a day when a newer rb is out
            if !dry {
                let _ = guarded(crate::ledger::updates::say_after);
            }
            let _ = guarded(|| {
                let success = code == EXIT_OK;
                let initialized = success && !dry && (cmd == "init" || cmd == "clone" || out.data.contains_key("started"));
                let recorded = success && !dry && matches!(command.as_str(), "run add" | "analysis add")
                    && !out.data.contains_key("duplicate_of");
                let ready = if recorded {
                    Some(crate::telemetry::RecordRoute::Declared)
                } else if success && cmd == "reconcile" && ns_bool(&ns, "apply") && crate::usage::reconciled(&out.data) {
                    Some(crate::telemetry::RecordRoute::Reconciled)
                } else { None };
                usage.finish(code, initialized, ready,
                    (success || code == EXIT_CHECK_FAILED) && matches!(cmd.as_str(), "status" | "show" | "compare" | "log" | "page"),
                    success && cmd == "context");
            });
            // after an upgrade, a project set up to sync re-syncs once, in the background (layer 2 of updates without
            // the user); a sync that went through keeps the version that wrote its page
            if !dry {
                let _ = guarded(|| crate::ledger::resync::after_command(&command, code, ns_str(&ns, "hub"), ns_bool(&ns, "with_record"), ns_bool(&ns, "watch")));
            }
            code
        }
        Err(err) => {
            // a fault in rb carries the report link: its fix line, and report_url under --json
            let err = crate::ledger::report_link::reported(err, &command);
            if !out.json_mode {
                eprintln_err(&format!("rb {}: {}", command, err.message));
                for pr in &err.problems {
                    eprintln_err(&format!("  - {pr}"));
                }
                if !err.fix.is_empty() {
                    eprintln_err(&format!("  → {}", err.fix));
                }
            }
            out.emit(false, vec![err.to_value()]);
            let _ = guarded(|| usage.finish(err.exit_code, false, None, false, false));
            err.exit_code
        }
    }
}

pub fn isatty(fd: i32) -> bool {
    crate::sys::isatty(fd)
}

pub fn exit_ok() -> Res<i32> {
    Ok(EXIT_OK)
}

#[cfg(test)]
mod tests {
    use super::Out;

    /// rb log, and every reading command run with --json, worked out whether its record draws escapes before it said
    /// anything: about 13 ms of rb log's 90 on a log of 2,000 lines. It is worked out only once the text it draws holds
    /// a character a screen would hide, and never for --json.
    #[test]
    fn whether_a_record_draws_escapes_is_worked_out_only_for_text_that_holds_a_hidden_character() {
        let root = std::env::temp_dir().join(format!("rb-drawn-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(root.join(".rb")).unwrap();
        std::fs::write(root.join(".rb/project.yaml"), "rb: 1\nkind: project\nid: int8-resnet-50\ntitle: INT8 ResNet-50\ncreated_by: human:ana\n").unwrap();
        let hidden = format!("ship int8 · note: it holds{}", '\u{e0020}');
        let led = crate::ledger::Ledger::new(&root, "cli", None);
        let mut out = Out::new("log", true);
        out.say(hidden.clone());
        out.shows_for(&led);
        assert!(!led.worked_out("authority"), "--json draws nothing");
        let mut out = Out::new("log", false);
        out.say("2026-10-17 09:08 decide decision dab12cd\nNothing logged.");
        out.next.push("rb confirm dab12cd".into());
        out.shows_for(&led);
        assert!(!led.worked_out("authority"), "nothing it draws holds a hidden character, a line break aside");
        out.say(hidden);
        out.shows_for(&led);
        assert!(led.worked_out("authority"), "no session wrote a design here, so whether a call waits is worked out");
        let _ = std::fs::remove_dir_all(&root);
    }
}
