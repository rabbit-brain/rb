//! Optional product usage, separate from the research record. Nothing is collected until a human explicitly enables
//! it. Events are constructed from enums alone; record contents, arguments, names, paths and error text never enter
//! this module. Consent, a random installation id and a bounded queue live in the user's settings, outside .rb/.
//! This is installation activity, not a count of people. A human actor is an inference from the calling environment.
//! Delivery never runs in a process anyone waits on: an operation hands a due delivery to a detached
//! `rb telemetry flush`, and the queue's lock is never held across the network.

use crate::errors::{RBError, Res};
use crate::pyjson;
use crate::value::{Dict, Value};
use chrono::{DateTime, Utc};
use std::cell::Cell;
use std::collections::HashSet;
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant};

const MAX_EVENTS: usize = 100;
const MAX_BYTES: u64 = 256 * 1024;
const MAX_AGE_SECONDS: i64 = 30 * 24 * 60 * 60;
const FLUSH_INTERVAL_SECONDS: i64 = 60;
// A queued milestone goes out without waiting for the interval, but attempts stay at least this far apart.
const MILESTONE_FLOOR_SECONDS: i64 = 5;
// After a failed send the next attempt waits the interval, doubled per consecutive failure, at most this long.
const RETRY_WAIT_LIMIT_SECONDS: i64 = 60 * 60;
const CONNECT_BUDGET: Duration = Duration::from_secs(3);
const SEND_BUDGET: Duration = Duration::from_secs(10);
// Whoever holds the queue's lock only reads and rewrites a small local file.
const CAPTURE_WAIT: Duration = Duration::from_millis(50);
const CONTROL_WAIT: Duration = Duration::from_secs(2);
// A background delivery sends again while events keep arriving, this many times at most.
const DELIVERY_ROUNDS: u32 = 12;
// Set on the detached `rb telemetry flush` an operation starts; the user's own flush sends once and returns.
const BACKGROUND: &str = "RB_TELEMETRY_BACKGROUND";
const MILESTONES: [&str; 5] = ["rb_telemetry_enabled", "rb_project_initialized", "rb_record_ready", "rb_record_inspected", "rb_context_delivered"];
const CI_VARIABLES: [&str; 10] = [
    "CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD", "JENKINS_URL", "TEAMCITY_VERSION", "CODEBUILD_BUILD_ARN",
    "bamboo_planKey", "BUILDER_OUTPUT",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Surface { Cli, Sdk, Mcp, Tui, Web }
impl Surface {
    fn word(self) -> &'static str { match self { Self::Cli => "cli", Self::Sdk => "sdk", Self::Mcp => "mcp", Self::Tui => "tui", Self::Web => "web" } }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Actor { Human, Agent, Unknown }
impl Actor {
    fn word(self) -> &'static str { match self { Self::Human => "human_inferred", Self::Agent => "agent", Self::Unknown => "unknown" } }
    fn from_word(s: &str) -> Option<Self> { [Self::Human, Self::Agent, Self::Unknown].into_iter().find(|a| a.word() == s) }
    /// Buckets an existing attribution without retaining its name. This does not change research attribution.
    pub fn from_actor(actor: &str) -> Self {
        if actor.starts_with("agent:") { Self::Agent } else if actor.starts_with("human:") { Self::Human } else { Self::Unknown }
    }
    /// No git lookup and no user name are needed for product metrics.
    pub fn detect() -> Self {
        if crate::actor::detect_runtime().is_some() { return Self::Agent; }
        match std::env::var("RB_ACTOR") { Ok(a) => Self::from_actor(&a), Err(_) => Self::Human }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome { Success, CheckFailed, InputError, InternalError }
impl Outcome {
    pub fn from_exit(code: i32) -> Self { match code { 0 => Self::Success, 1 => Self::CheckFailed, 2 => Self::InputError, _ => Self::InternalError } }
    fn word(self) -> &'static str { match self { Self::Success => "success", Self::CheckFailed => "check_failed", Self::InputError => "input_error", Self::InternalError => "internal_error" } }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RecordRoute { Declared, Reconciled }
impl RecordRoute { fn word(self) -> &'static str { match self { Self::Declared => "declared", Self::Reconciled => "reconciled" } } }

/// The private field ensures no caller can put a path, argument or object name in the command property.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Command(&'static str);
const COMMANDS: &[&str] = &[
    "init", "project title", "question add", "question move", "question reword", "experiment add", "experiment file", "experiment relate", "experiment gate",
    "variant add", "note add", "metric add", "spec set", "spec verify", "spec vary", "criterion add", "criterion file", "criterion relate", "run add", "analysis add",
    "resource add", "resource locate", "resource expose", "freeze", "decide", "confirm", "retract", "status", "show", "compare", "log", "context",
    "fmt", "doctor", "journal", "journal scan", "journal run", "reconcile", "compile", "page", "export", "clone", "sync", "login",
    "logout", "env check", "skill", "skill add", "ui", "open", "docs", "schema", "version",
];
impl Command {
    pub fn from_name(name: &str) -> Option<Self> { COMMANDS.iter().copied().find(|c| *c == name).map(Self) }
    /// The SDK's own lookups behind a handle (`load`) are no command anyone ran.
    pub fn from_bridge(op: &str) -> Option<Self> {
        Self::from_name(match op {
            "set_project_title" => "project title",
            "add_question" => "question add", "move_question" => "question move", "reword_question" => "question reword", "add_experiment" => "experiment add", "add_metric" => "metric add",
            "add_note" => "note add", "add_resource" => "resource add", "locate_resource" => "resource locate", "expose" => "resource expose",
            "add_variant" => "variant add", "set_gate" => "experiment gate", "relate" => "experiment relate",
            "add_criterion" => "criterion add", "file_criterion" => "criterion file", "relate_criterion" => "criterion relate", "add_analysis" => "analysis add", "add_run" => "run add",
            "set_setting" => "spec set", "verify_settings" => "spec verify", "declare_varies" => "spec vary", "scan" => "journal scan",
            "get" | "finding" | "relations" => "show",
            "init" | "clone" | "status" | "context" | "log" | "reconcile" | "journal" | "export" | "decide" | "retract" | "compare" | "freeze" => op,
            _ => return None,
        })
    }
}

#[derive(Clone, Copy, Debug)]
pub enum Event {
    TelemetryEnabled { surface: Surface, actor: Actor },
    OperationCompleted { command: Command, surface: Surface, actor: Actor, outcome: Outcome, duration_ms: u64 },
    ProjectInitialized { surface: Surface, actor: Actor },
    RecordReady { route: RecordRoute, surface: Surface, actor: Actor },
    RecordInspected { surface: Surface, actor: Actor },
    ContextDelivered { surface: Surface, actor: Actor },
}

impl Event {
    fn parts(self) -> (&'static str, Surface, Actor, Dict) {
        let mut props = Dict::new();
        match self {
            Self::TelemetryEnabled { surface, actor } => ("rb_telemetry_enabled", surface, actor, props),
            Self::OperationCompleted { command, surface, actor, outcome, duration_ms } => {
                props.insert("command".into(), command.0.into());
                props.insert("outcome".into(), outcome.word().into());
                props.insert("duration_bucket".into(), duration_bucket(duration_ms).into());
                ("rb_operation_completed", surface, actor, props)
            }
            Self::ProjectInitialized { surface, actor } => ("rb_project_initialized", surface, actor, props),
            Self::RecordReady { route, surface, actor } => {
                props.insert("route".into(), route.word().into());
                ("rb_record_ready", surface, actor, props)
            }
            Self::RecordInspected { surface, actor } => ("rb_record_inspected", surface, actor, props),
            Self::ContextDelivered { surface, actor } => ("rb_context_delivered", surface, actor, props),
        }
    }
}

fn duration_bucket(ms: u64) -> &'static str { match ms { 0..=9 => "under_10ms", 10..=99 => "10_99ms", 100..=999 => "100_999ms", 1000..=9999 => "1_9s", _ => "10s_or_more" } }

// The waits after which the queue's lock was given up on this thread, for the tests: what a wait was, without a clock.
#[cfg(test)]
thread_local! {
    static GAVE_UP: std::cell::RefCell<Vec<Duration>> = const { std::cell::RefCell::new(Vec::new()) };
}
thread_local! {
    static SUPPRESSED: Cell<usize> = const { Cell::new(0) };
    // Whether this thread queued an event since `flush_if_captured` last looked.
    static CAPTURED: Cell<bool> = const { Cell::new(false) };
}
/// Keep this guard alive while a command invokes other commands, or while a tutorial runs. Nested guards compose.
pub struct Suppression { _not_send: std::marker::PhantomData<std::rc::Rc<()>> }
pub fn suppress() -> Suppression {
    SUPPRESSED.with(|n| n.set(n.get().saturating_add(1)));
    Suppression { _not_send: std::marker::PhantomData }
}
impl Drop for Suppression { fn drop(&mut self) { SUPPRESSED.with(|n| n.set(n.get().saturating_sub(1))); } }

/// What this process's environment says: a switch that suppresses analytics, and a cohort that overrides the
/// installation's own for this process.
#[derive(Clone, Debug)]
struct Policy { reason: Option<&'static str>, cohort: Option<&'static str> }
impl Policy {
    fn read() -> Self { Self::from_env(|k| std::env::var(k).ok()) }
    fn from_env(get: impl Fn(&str) -> Option<String>) -> Self {
        let on = |k: &str| get(k).map_or(false, |v| !v.is_empty() && !matches!(v.to_ascii_lowercase().as_str(), "0" | "false" | "no"));
        // RB_TELEMETRY never opts in, and any value but an explicit yes turns analytics off.
        let off = |v: String| !matches!(v.trim().to_ascii_lowercase().as_str(), "1" | "true" | "on" | "yes");
        let reason = if on("DO_NOT_TRACK") { Some("do_not_track") }
        else if get("RB_TELEMETRY").map_or(false, off) { Some("environment_disabled") }
        else if on("RB_TELEMETRY_SUPPRESS") { Some("parent_suppressed") }
        else if CI_VARIABLES.iter().any(|k| on(k)) { Some("ci") }
        else if ["RB_TEST_NOW", "RB_TEST_SEED", "RB_TEST_VERSION", "RB_TEST_ENV", "RB_TEST_FAULT", "RB_TEST_SHELL", "RB_TEST_SHELL_MODEL", "RB_TEST_SHELL_DUMP", "RB_TEST_SYNC_PACE", "PYTEST_CURRENT_TEST"].iter().any(|k| get(k).is_some()) { Some("test") }
        else { None };
        let cohort = get("RB_TELEMETRY_COHORT").map(|v| v.trim().to_ascii_lowercase()).filter(|v| !v.is_empty())
            .map(|v| if v == "external" { "external" } else { "internal" });
        Self { reason, cohort }
    }
}

/// A debug build, or one with no destination built in (a source or editable install), is the project's own.
fn build_cohort() -> &'static str {
    if cfg!(debug_assertions) || option_env!("RB_POSTHOG_KEY").map_or(true, str::is_empty) { "internal" } else { "external" }
}

/// What `rb telemetry enable` adds when a switch keeps anything from being collected yet.
pub fn switch_note(reason: &str) -> Option<&'static str> {
    Some(match reason {
        "do_not_track" => "DO_NOT_TRACK is set, so nothing is collected until it is unset.",
        "environment_disabled" => "RB_TELEMETRY is set to a value that turns analytics off, so nothing is collected until it is unset.",
        "parent_suppressed" => "RB_TELEMETRY_SUPPRESS is set, so nothing is collected until it is unset.",
        "ci" => "This is a CI environment, so nothing is collected here.",
        "test" => "This is a test harness, so nothing is collected here.",
        _ => return None,
    })
}

fn random_uuid() -> Option<String> {
    let mut bytes = [0u8; 16];
    if !crate::sys::random_bytes(&mut bytes) { return None; }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
    Some(format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..]))
}

fn uuid_ok(s: &str) -> bool {
    s.len() == 36 && s.bytes().enumerate().all(|(i, c)| if [8, 13, 18, 23].contains(&i) { c == b'-' } else { c.is_ascii_hexdigit() })
}

fn key_ok(s: &str) -> bool {
    s.starts_with("phc_") && (9..=256).contains(&s.len()) && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_')
}

fn host_ok(s: &str) -> bool {
    let Ok(uri) = s.parse::<ureq::http::Uri>() else { return false };
    let transport = uri.scheme_str() == Some("https") || (uri.scheme_str() == Some("http") && uri.host() == Some("127.0.0.1"));
    transport && uri.authority().map_or(false, |a| !a.as_str().contains('@'))
        && uri.host().map_or(false, |h| !h.is_empty()) && uri.path() == "/" && uri.query().is_none() && !s.contains('#')
}

fn target(key: Option<&str>, host: Option<&str>) -> Res<(Option<String>, Option<String>)> {
    let key = key.filter(|s| !s.is_empty()).map(str::to_string)
        .or_else(|| std::env::var("RB_POSTHOG_KEY").ok().filter(|s| !s.is_empty()))
        .or_else(|| option_env!("RB_POSTHOG_KEY").filter(|s| !s.is_empty()).map(str::to_string));
    let host = host.filter(|s| !s.is_empty()).map(str::to_string)
        .or_else(|| std::env::var("RB_POSTHOG_HOST").ok().filter(|s| !s.is_empty()))
        .or_else(|| option_env!("RB_POSTHOG_HOST").filter(|s| !s.is_empty()).map(str::to_string));
    if key.as_deref().map_or(false, |s| !key_ok(s)) {
        return Err(RBError::msg("E_USAGE", "Use the public PostHog project capture key beginning phc_, not a personal API key."));
    }
    let host = host.map(|h| h.trim_end_matches('/').to_string());
    if host.as_deref().map_or(false, |s| !host_ok(s)) {
        return Err(RBError::msg("E_USAGE", "Use an HTTPS telemetry origin without credentials, path, query or fragment. Local transport tests may use http://127.0.0.1 with a port."));
    }
    Ok((key, host))
}

fn inside_record(path: &Path) -> bool {
    path.components().any(|c| match c {
        // case-insensitive volumes open .RB as .rb, and Windows also drops a name's trailing dots and spaces
        Component::Normal(n) => n.to_str().map_or(false, |s| (if cfg!(windows) { s.trim_end_matches(['.', ' ']) } else { s }).eq_ignore_ascii_case(".rb")),
        _ => false,
    })
}

// The general JSON reader is recursive. Our state needs only four levels; refuse pathological local data before parsing.
fn shallow_json(text: &str) -> bool {
    let (mut depth, mut quoted, mut escaped) = (0usize, false, false);
    for c in text.bytes() {
        if quoted {
            if escaped { escaped = false; }
            else if c == b'\\' { escaped = true; }
            else if c == b'"' { quoted = false; }
        } else {
            match c {
                b'"' => quoted = true,
                b'[' | b'{' => { depth += 1; if depth > 8 { return false; } },
                b']' | b'}' => depth = depth.saturating_sub(1),
                _ => (),
            }
        }
    }
    true
}

#[derive(Clone)]
struct Store { dir: PathBuf }
impl Store {
    fn configured() -> Option<Self> {
        let nonempty = |k: &str| std::env::var_os(k).filter(|v| !v.is_empty());
        let base = nonempty("RB_CONFIG_DIR").map(PathBuf::from).unwrap_or_else(|| {
            nonempty("XDG_CONFIG_HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(crate::sys::config_home())).join("rabbit-brain")
        });
        Self::at_base(&base)
    }
    fn at_base(base: &Path) -> Option<Self> {
        let dir = base.join("telemetry");
        // '..' can step through a directory that does not exist yet, where the check below cannot follow it.
        if !dir.is_absolute() || inside_record(&dir) || dir.components().any(|c| c == Component::ParentDir) { return None; }
        // An existing ancestor can be a symlink. Resolve it before permitting any write beneath it.
        let ancestor = dir.ancestors().find(|p| p.exists())?;
        let real = std::fs::canonicalize(ancestor).ok()?;
        if inside_record(&real) { return None; }
        Some(Self { dir })
    }
    fn path(&self) -> PathBuf { self.dir.join("state.json") }
    fn read(&self) -> Option<State> {
        let p = self.path();
        let md = std::fs::symlink_metadata(&p).ok()?;
        // A regular file only: opening a FIFO would block, and a link could lead anywhere.
        if !md.file_type().is_file() || md.len() > MAX_BYTES { return None; }
        let mut text = String::new();
        File::open(p).ok()?.take(MAX_BYTES + 1).read_to_string(&mut text).ok()?;
        if text.len() as u64 > MAX_BYTES || !shallow_json(&text) { return None; }
        State::read(&pyjson::loads(&text).ok()?)
    }
    /// One of this module's lock files, opened (made if need be) only when it is a regular file.
    fn lock_file(&self, name: &str) -> io::Result<File> {
        let p = self.dir.join(name);
        if std::fs::symlink_metadata(&p).map_or(false, |m| !m.file_type().is_file()) { return Err(io::ErrorKind::PermissionDenied.into()); }
        let mut o = OpenOptions::new();
        o.read(true).write(true).create(true);
        #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; o.mode(0o600); }
        o.open(p)
    }
    /// The queue's lock, waited for no longer than `wait`: WouldBlock while another process still holds it.
    fn lock(&self, create: bool, wait: Duration) -> io::Result<File> {
        if create { std::fs::create_dir_all(&self.dir)?; }
        let f = self.lock_file("lock")?;
        let deadline = Instant::now() + wait;
        loop {
            match f.try_lock() {
                Ok(()) => return Ok(f),
                Err(std::fs::TryLockError::WouldBlock) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(2)),
                Err(std::fs::TryLockError::WouldBlock) => {
                    #[cfg(test)]
                    GAVE_UP.with(|g| g.borrow_mut().push(wait));
                    return Err(io::ErrorKind::WouldBlock.into());
                }
                Err(std::fs::TryLockError::Error(e)) => return Err(e),
            }
        }
    }
    /// Held by the one background delivery for as long as it runs; the system lets go of it if that process dies.
    fn deliverer(&self) -> io::Result<File> {
        let f = self.lock_file("delivery")?;
        match f.try_lock() {
            Ok(()) => Ok(f),
            Err(std::fs::TryLockError::WouldBlock) => Err(io::ErrorKind::WouldBlock.into()),
            Err(std::fs::TryLockError::Error(e)) => Err(e),
        }
    }
    fn delivering(&self) -> bool {
        matches!(self.deliverer(), Err(e) if e.kind() == io::ErrorKind::WouldBlock)
    }
    fn write(&self, state: &State) -> io::Result<()> {
        let text = pyjson::dumps(&state.value());
        if text.len() as u64 > MAX_BYTES { return Err(io::ErrorKind::InvalidData.into()); }
        let uuid = random_uuid().ok_or(io::ErrorKind::Other)?;
        let tmp = self.dir.join(format!(".state-{uuid}.tmp"));
        let mut o = OpenOptions::new();
        o.create_new(true).write(true);
        #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; o.mode(0o600); }
        let mut f = o.open(&tmp)?;
        let written = f.write_all(text.as_bytes());
        drop(f);
        // Unlike research writes, optional telemetry never retries a sharing violation or waits for a file holder.
        let result = written.and_then(|_| std::fs::rename(&tmp, self.path()));
        if result.is_err() { let _ = std::fs::remove_file(tmp); }
        result
    }
}

#[derive(Clone)]
struct State {
    id: String,
    key: Option<String>,
    host: Option<String>,
    // Fixed at opt-in. A state written before it was kept takes the build's own.
    cohort: Option<&'static str>,
    // Who opted in while a switch was set: the consent event waits for the first event collected after it.
    consent_pending: Option<Actor>,
    queue: Vec<Value>,
    last_attempt: i64,
    // Sends that failed in a row, and whether one of them was refused for good; a send that succeeds clears both.
    failures: u32,
    refused: bool,
}
impl State {
    fn new(id: String) -> Self {
        Self { id, key: None, host: None, cohort: None, consent_pending: None, queue: vec![], last_attempt: 0, failures: 0, refused: false }
    }
    fn read(v: &Value) -> Option<Self> {
        if v.get("schema_version")?.as_i64()? != 1 || v.get("enabled")?.as_bool()? != true { return None; }
        let id = v.get_str("installation_id")?.to_string();
        if !uuid_ok(&id) { return None; }
        let key = v.get_str("project_key").map(str::to_string);
        let host = v.get_str("host").map(str::to_string);
        if key.as_deref().map_or(false, |s| !key_ok(s)) || host.as_deref().map_or(false, |s| !host_ok(s)) { return None; }
        let cohort = match v.get("cohort") {
            None | Some(Value::Null) => None,
            Some(c) => Some(["internal", "external"].into_iter().find(|k| c.as_str() == Some(*k))?),
        };
        let consent_pending = match v.get("consent_pending") {
            None | Some(Value::Null) => None,
            Some(a) => Some(Actor::from_word(a.as_str()?)?),
        };
        let queue = v.get("queue")?.as_list()?.iter().filter(|e| valid_event(e, &id)).take(MAX_EVENTS).cloned().collect();
        let last_attempt = v.get("last_attempt").and_then(Value::as_i64).unwrap_or(0);
        let failures = v.get("failures").and_then(Value::as_i64).and_then(|n| u32::try_from(n).ok()).unwrap_or(0);
        let refused = v.get("refused").and_then(Value::as_bool).unwrap_or(false);
        let mut state = Self { id, key, host, cohort, consent_pending, queue, last_attempt, failures, refused };
        state.prune(Utc::now().timestamp());
        Some(state)
    }
    fn value(&self) -> Value {
        crate::obj! {"schema_version" => 1, "enabled" => true, "installation_id" => self.id.clone(),
            "project_key" => self.key.clone(), "host" => self.host.clone(), "cohort" => self.cohort,
            "consent_pending" => self.consent_pending.map(Actor::word), "queue" => self.queue.clone(), "last_attempt" => self.last_attempt,
            "failures" => i64::from(self.failures), "refused" => self.refused}
    }
    fn cohort(&self, policy: &Policy) -> &'static str { policy.cohort.or(self.cohort).unwrap_or_else(build_cohort) }
    /// Consent given under a switch goes into the queue once nothing suppresses collection, ahead of what follows it.
    fn queue_consent(&mut self, policy: &Policy) {
        let Some(actor) = self.consent_pending.filter(|_| policy.reason.is_none()) else { return };
        if let Some(event) = make_event(Event::TelemetryEnabled { surface: Surface::Cli, actor }, &self.id, self.cohort(policy)) {
            self.queue.push(event);
            self.consent_pending = None;
        }
    }
    fn prune(&mut self, now: i64) {
        self.queue.retain(|e| e.get_str("timestamp").and_then(|s| DateTime::parse_from_rfc3339(s).ok())
            .map_or(false, |t| (now - MAX_AGE_SECONDS..=now + 60).contains(&t.timestamp())));
        // Over the cap, operations go first, then milestones, the consent event last; the oldest first within each.
        for rank in 0..3 {
            let mut excess = self.queue.len().saturating_sub(MAX_EVENTS);
            if excess == 0 { break; }
            self.queue.retain(|e| if excess > 0 && eviction_rank(e) == rank { excess -= 1; false } else { true });
        }
    }
    fn payload(&self) -> Option<Value> {
        Some(crate::obj! {"api_key" => self.key.clone()?, "batch" => self.queue.clone()})
    }
    /// Whether an operation should hand a delivery to the background now. The end of a long-running surface sends
    /// what is left as a milestone would, never sooner than the floor. After a failed send nothing goes sooner than
    /// the retry wait, a milestone or a surface's end included.
    fn due(&self, now: i64, at_end: bool) -> bool {
        if self.queue.is_empty() || self.key.is_none() || self.host.is_none() { return false; }
        if now < self.last_attempt { return true; }
        let waited = now - self.last_attempt;
        if self.failures > 0 { return waited >= self.retry_wait(); }
        waited >= FLUSH_INTERVAL_SECONDS || (waited >= MILESTONE_FLOOR_SECONDS && (at_end || self.queue.iter().any(is_milestone)))
    }
    /// The interval doubled for each failed send after the first, at most an hour; the hour once a send was refused.
    fn retry_wait(&self) -> i64 {
        if self.refused { return RETRY_WAIT_LIMIT_SECONDS; }
        (FLUSH_INTERVAL_SECONDS << self.failures.saturating_sub(1).min(6)).min(RETRY_WAIT_LIMIT_SECONDS)
    }
}

fn is_milestone(e: &Value) -> bool { e.get_str("event").map_or(false, |n| MILESTONES.contains(&n)) }

fn eviction_rank(e: &Value) -> u8 {
    match e.get_str("event") { Some("rb_operation_completed") => 0, Some("rb_telemetry_enabled") => 2, _ => 1 }
}

fn known(v: Option<&Value>, choices: &[&str]) -> bool { v.and_then(Value::as_str).map_or(false, |s| choices.contains(&s)) }

/// Validate again at the disk boundary. A hand-edited queue cannot turn an allowlisted event into an arbitrary upload.
fn valid_event(v: &Value, id: &str) -> bool {
    let Some(d) = v.as_dict() else { return false };
    if d.len() != 5 || !["event", "uuid", "timestamp", "distinct_id", "properties"].iter().all(|k| d.contains_key(*k)) { return false; }
    if v.get_str("distinct_id") != Some(id) || !v.get_str("uuid").map_or(false, uuid_ok) { return false; }
    if !v.get_str("timestamp").map_or(false, |s| s.len() <= 30 && DateTime::parse_from_rfc3339(s).is_ok()) { return false; }
    let Some(p) = v.get("properties").and_then(Value::as_dict) else { return false };
    let extra = match v.get_str("event") {
        Some("rb_operation_completed") => 3,
        Some("rb_record_ready") => 1,
        Some("rb_telemetry_enabled" | "rb_project_initialized" | "rb_record_inspected" | "rb_context_delivered") => 0,
        _ => return false,
    };
    if p.len() != 10 + extra || p.get("schema_version").and_then(Value::as_i64) != Some(1)
        || p.get("$process_person_profile").and_then(Value::as_bool) != Some(false)
        || p.get("$geoip_disable").and_then(Value::as_bool) != Some(true)
        || p.get("$ip").and_then(Value::as_str) != Some("0.0.0.0") { return false; }
    let Some(version) = p.get("rb_version").and_then(Value::as_str) else { return false };
    if !version_ok(version) || !known(p.get("os"), &["linux", "macos", "windows", "other"])
        || !known(p.get("arch"), &["x86_64", "aarch64", "x86", "arm", "other"])
        || !known(p.get("surface"), &["cli", "sdk", "mcp", "tui", "web"])
        || !known(p.get("actor"), &["human_inferred", "agent", "unknown"])
        || !known(p.get("cohort"), &["external", "internal"]) { return false; }
    match extra {
        3 => p.get("command").and_then(Value::as_str).and_then(Command::from_name).is_some()
            && known(p.get("outcome"), &["success", "check_failed", "input_error", "internal_error"])
            && known(p.get("duration_bucket"), &["under_10ms", "10_99ms", "100_999ms", "1_9s", "10s_or_more"]),
        1 => known(p.get("route"), &["declared", "reconciled"]),
        _ => true,
    }
}

/// A release's version as PEP 440 writes it canonically: 0.6.0, 0.6.0rc1, 0.6.0.post1, 0.6.0.dev20261003.
fn version_ok(s: &str) -> bool {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    s.len() <= 64 && RE.get_or_init(|| regex::Regex::new(r"^([0-9]+!)?[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?$").unwrap()).is_match(s)
}

fn make_event(event: Event, id: &str, cohort: &str) -> Option<Value> {
    let (name, surface, actor, mut p) = event.parts();
    p.extend(crate::dict! {"schema_version" => 1, "rb_version" => crate::VERSION,
        "os" => match std::env::consts::OS { "linux" => "linux", "macos" => "macos", "windows" => "windows", _ => "other" },
        "arch" => match std::env::consts::ARCH { "x86_64" => "x86_64", "aarch64" => "aarch64", "x86" => "x86", "arm" => "arm", _ => "other" },
        "surface" => surface.word(), "actor" => actor.word(), "cohort" => cohort,
        "$process_person_profile" => false, "$geoip_disable" => true, "$ip" => "0.0.0.0"});
    let event = crate::obj! {"event" => name, "uuid" => random_uuid()?, "distinct_id" => id,
        "timestamp" => Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true), "properties" => p};
    valid_event(&event, id).then_some(event)
}

fn capture_at(store: &Store, events: &[Event], policy: &Policy) {
    if events.is_empty() || policy.reason.is_some() || store.read().is_none() { return; }
    let Ok(_lock) = store.lock(false, CAPTURE_WAIT) else { return };
    let Some(mut state) = store.read() else { return };
    state.queue_consent(policy);
    let (id, cohort) = (state.id.clone(), state.cohort(policy));
    state.queue.extend(events.iter().filter_map(|e| make_event(*e, &id, cohort)));
    state.prune(Utc::now().timestamp());
    if store.write(&state).is_ok() {
        CAPTURED.with(|c| c.set(true));
    }
}

/// Queue only, one operation's events together. Call at the outer operation boundary, never inside ledger reads or
/// per-file writes.
pub fn capture(events: &[Event]) {
    if SUPPRESSED.with(|n| n.get() != 0) { return; }
    if let Some(store) = Store::configured() { capture_at(&store, events, &Policy::read()); }
}

/// Whether an observation can be queued. A missing capture target still permits an explicitly opted-in local queue.
/// This check does no write, name lookup or network work; callers can skip any expensive activation checks when false.
pub fn is_active() -> bool {
    !SUPPRESSED.with(|n| n.get() != 0) && Policy::read().reason.is_none()
        && Store::configured().and_then(|s| s.read()).is_some()
}

fn state_status(state: Option<&State>, policy: &Policy) -> Value {
    let enabled = state.is_some();
    let configured = state.map_or(false, |s| s.key.is_some() && s.host.is_some());
    crate::obj! {"enabled" => enabled, "active" => enabled && configured && policy.reason.is_none(), "configured" => configured,
        "reason" => policy.reason.or(if !enabled { Some("not_enabled") } else if !configured { Some("target_not_configured") } else { None }),
        "queued" => state.map_or(0, |s| s.queue.len()), "cohort" => state.map_or_else(|| policy.cohort.unwrap_or_else(build_cohort), |s| s.cohort(policy)),
        "queue_limit" => MAX_EVENTS, "retention_days" => 30, "host" => state.and_then(|s| s.host.clone())}
}

pub fn status() -> Value {
    let state = Store::configured().and_then(|s| s.read());
    state_status(state.as_ref(), &Policy::read())
}

/// Shows the batch retained for upload; api_key is null if no target key is configured (such a batch cannot be sent).
/// A configured key here is a public capture key, never a secret.
pub fn preview() -> Value {
    let state = Store::configured().and_then(|s| s.read());
    let mut value = state_status(state.as_ref(), &Policy::read());
    if let Some(d) = value.as_dict_mut() {
        d.insert("payload".into(), state.as_ref().map(|s| crate::obj! {"api_key" => s.key.clone(), "batch" => s.queue.clone()}).unwrap_or(Value::Null));
    }
    value
}

fn settings_error() -> RBError {
    RBError::msg_fix("E_WRITE_FAILED", "The telemetry settings could not be updated.", "Use a writable user settings directory outside .rb, then try again.")
}

fn busy_error() -> RBError {
    RBError::msg_fix("E_WRITE_FAILED", "Another rb process is updating analytics right now, so the settings were left as they are.", "Try again in a moment.")
}

fn lock_error(e: io::Error) -> RBError {
    if e.kind() == io::ErrorKind::WouldBlock { busy_error() } else { settings_error() }
}

/// Explicit opt-in only, and the user's: an agent session is refused, as rb ui and rb open refuse it. Queues
/// consent once per installation identity but makes no network request. Missing capture settings keep events local.
pub fn enable(project_key: Option<&str>, host: Option<&str>) -> Res<Value> {
    if crate::actor::detect_runtime().is_some() {
        return Err(RBError::msg_fix("E_USAGE", "Usage analytics is the user's to turn on, and this is an agent session.", "The user runs rb telemetry enable in their own terminal."));
    }
    let (key, host) = target(project_key, host)?;
    let store = Store::configured().ok_or_else(settings_error)?;
    enable_at(&store, key, host, &Policy::read(), Actor::detect())
}

fn enable_at(store: &Store, key: Option<String>, host: Option<String>, policy: &Policy, actor: Actor) -> Res<Value> {
    let _lock = store.lock(true, CONTROL_WAIT).map_err(lock_error)?;
    let old = store.read();
    let mut new_identity = old.is_none();
    let mut state = match old { Some(s) => s, None => State::new(random_uuid().ok_or_else(settings_error)?) };
    let new_key = key.or_else(|| state.key.clone());
    let new_host = host.or_else(|| state.host.clone());
    // Do not forward an old project's queue or identifier to a new capture destination.
    if (state.key.is_some() && state.key != new_key) || (state.host.is_some() && state.host != new_host) {
        state.queue.clear();
        state.id = random_uuid().ok_or_else(settings_error)?;
        state.last_attempt = 0;
        // what the old destination refused says nothing of the new one
        (state.failures, state.refused) = (0, false);
        new_identity = true;
    }
    state.key = new_key;
    state.host = new_host;
    state.cohort = policy.cohort.or(state.cohort).or(Some(build_cohort()));
    if new_identity {
        state.consent_pending = Some(actor);
    }
    state.queue_consent(policy);
    store.write(&state).map_err(|_| settings_error())?;
    Ok(state_status(Some(&state), policy))
}

/// Remove consent, the installation id, capture settings and all queued events. The next opt-in starts a new identity.
pub fn disable() -> Res<Value> {
    let store = Store::configured().ok_or_else(settings_error)?;
    disable_at(&store)?;
    Ok(state_status(None, &Policy::read()))
}

fn disable_at(store: &Store) -> Res<()> {
    if !store.dir.exists() { return Ok(()); }
    // A lock file that cannot be used stops every writer too, so the opt-out goes ahead without it.
    let _lock = match store.lock(false, CONTROL_WAIT) {
        Err(e) if e.kind() == io::ErrorKind::WouldBlock => return Err(busy_error()),
        held => held.ok(),
    };
    match crate::sys::remove(&store.path()) {
        Err(e) if e.kind() != io::ErrorKind::NotFound => return Err(settings_error()),
        _ => {}
    }
    // A killed atomic write can leave its private temporary file. Remove only this module's UUID-named files.
    for entry in std::fs::read_dir(&store.dir).map_err(|_| settings_error())? {
        let entry = entry.map_err(|_| settings_error())?;
        let name = entry.file_name();
        if name.to_str().and_then(|s| s.strip_prefix(".state-")).and_then(|s| s.strip_suffix(".tmp")).map_or(false, uuid_ok) {
            crate::sys::remove(&entry.path()).map_err(|_| settings_error())?;
        }
    }
    Ok(())
}

/// How a send ended. Refused is a 4xx other than 408 and 429: the destination will not take the batch, and asking
/// again soon would only be refused again.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Sent { Accepted, Failed, Refused }
impl Sent {
    fn from_status(status: u16) -> Self {
        match status { 200..=299 => Self::Accepted, 408 | 429 => Self::Failed, 400..=499 => Self::Refused, _ => Self::Failed }
    }
}

/// One bounded HTTPS request, sized for a cold connection; redirects are refused so the batch cannot silently change
/// destination. The receiver necessarily sees the network connection's IP even though the event suppresses GeoIP and
/// person enrichment.
fn send(host: &str, payload: &Value) -> Sent {
    let tls = ureq::tls::TlsConfig::builder().root_certs(ureq::tls::RootCerts::PlatformVerifier).build();
    let agent: ureq::Agent = ureq::Agent::config_builder().http_status_as_error(false).max_redirects(0).tls_config(tls)
        .timeout_global(Some(SEND_BUDGET)).timeout_resolve(Some(CONNECT_BUDGET)).timeout_connect(Some(CONNECT_BUDGET))
        .user_agent("rb-telemetry/1").build().into();
    let req = ureq::http::Request::builder().method("POST").uri(format!("{host}/batch/"))
        .header("content-type", "application/json").body(pyjson::dumps(payload).into_bytes());
    let Ok(req) = req else { return Sent::Failed };
    let Ok(response) = agent.run(req) else { return Sent::Failed };
    Sent::from_status(response.status().as_u16())
}

/// One delivery. Under the lock: take the batch and stamp the attempt. Without it: send. Under it again, and only for
/// the same installation: remove what was sent, so whatever was queued meanwhile stays, or count the failure, so the
/// next attempt waits longer. `held` is a background delivery's own lock, let go of under the queue's lock once
/// nothing is left, so an operation that queues after that starts the next delivery.
fn flush_once(store: &Store, policy: &Policy, held: &mut Option<File>, transmit: impl FnOnce(&str, &Value) -> Sent) -> Value {
    let result = |sent: usize, reason: &str| crate::obj! {"sent" => sent, "reason" => reason};
    if let Some(reason) = policy.reason { return result(0, reason); }
    if store.read().is_none() { return result(0, "not_enabled"); }
    let (id, host, payload, uuids) = {
        let Ok(_lock) = store.lock(false, CONTROL_WAIT) else { return result(0, "busy") };
        let Some(mut state) = store.read() else { return result(0, "not_enabled") };
        let (Some(host), Some(payload)) = (state.host.clone(), state.payload()) else { return result(0, "target_not_configured") };
        if state.queue.is_empty() {
            held.take();
            return result(0, "empty");
        }
        state.last_attempt = Utc::now().timestamp();
        if store.write(&state).is_err() { return result(0, "storage_unavailable"); }
        let uuids: HashSet<String> = state.queue.iter().filter_map(|e| e.get_str("uuid").map(str::to_string)).collect();
        (state.id, host, payload, uuids)
    };
    let sent = transmit(&host, &payload);
    // A removal that fails sends the same UUIDs again later, which ingestion deduplicates.
    if let Ok(_lock) = store.lock(false, CONTROL_WAIT) {
        if let Some(mut state) = store.read().filter(|s| s.id == id) {
            if sent == Sent::Accepted {
                state.queue.retain(|e| e.get_str("uuid").map_or(true, |u| !uuids.contains(u)));
                (state.failures, state.refused) = (0, false);
            } else {
                (state.failures, state.refused) = (state.failures.saturating_add(1), state.refused || sent == Sent::Refused);
            }
            let _ = store.write(&state);
        }
    }
    match sent {
        Sent::Accepted => result(uuids.len(), "sent"),
        Sent::Failed => result(0, "not_sent"),
        Sent::Refused => result(0, "refused"),
    }
}

/// The detached `rb telemetry flush` an operation starts, one at a time: it sends what is queued, then, while events
/// keep arriving, waits out the floor and sends those too. It stops at a failed send or when nothing new came.
fn deliver(store: &Store) -> Value {
    let Ok(held) = store.deliverer() else { return crate::obj! {"sent" => 0, "reason" => "busy"} };
    let mut held = Some(held);
    let (mut sent, mut reason) = (0, String::from("empty"));
    for round in 0..DELIVERY_ROUNDS {
        if round > 0 { std::thread::sleep(Duration::from_secs(MILESTONE_FLOOR_SECONDS as u64)); }
        let answer = flush_once(store, &Policy::read(), &mut held, send);
        reason = answer.get_str("reason").unwrap_or("empty").to_string();
        if reason != "sent" { break; }
        sent += answer.get("sent").and_then(Value::as_i64).unwrap_or(0);
    }
    crate::obj! {"sent" => sent, "reason" => reason}
}

/// `rb telemetry flush`. The user's sends once, whatever wait a failure set, and says what happened; it respects the
/// privacy switches. The one an operation starts in the background keeps delivering while events arrive.
pub fn flush() -> Value {
    if SUPPRESSED.with(|n| n.get() != 0) { return crate::obj! {"sent" => 0, "reason" => "nested_operation"}; }
    let Some(store) = Store::configured() else { return crate::obj! {"sent" => 0, "reason" => "settings_unavailable"} };
    if std::env::var_os(BACKGROUND).is_some() { return deliver(&store); }
    // The switches, read here, stop it before anything is sent: an explicit flush cannot bypass them.
    flush_once(&store, &Policy::read(), &mut None, send)
}

/// After an operation: when a delivery is due, hand it to a detached `rb telemetry flush` and return at once.
pub fn flush_if_due() { start_delivery(false) }

/// After a request to a long-running surface (the SDK's and the screen's bridge, rb mcp): only a request that queued
/// an event starts a delivery, so polling, which queues none, never drives the network.
pub fn flush_if_captured() {
    if CAPTURED.with(|c| c.replace(false)) { flush_if_due(); }
}

/// When a long-running surface ends (rb mcp, rb open, the screen): one delivery for whatever is still queued.
pub fn flush_at_end() { start_delivery(true) }

fn start_delivery(at_end: bool) {
    if SUPPRESSED.with(|n| n.get() != 0) || Policy::read().reason.is_some() { return; }
    let Some(store) = Store::configured() else { return };
    if !claim(&store, Utc::now().timestamp(), at_end) { return; }
    if let Ok(exe) = std::env::current_exe() {
        crate::sys::spawn_detached(&exe, &["telemetry", "flush"], &[(BACKGROUND, "1")]);
    }
}

/// Stamp the attempt under the lock, so operations that finish together start one delivery, and none while one runs.
fn claim(store: &Store, now: i64, at_end: bool) -> bool {
    // Most operations find nothing due on the unlocked read and never touch the lock.
    if !store.read().map_or(false, |s| s.due(now, at_end)) { return false; }
    let Ok(_lock) = store.lock(false, CAPTURE_WAIT) else { return false };
    let Some(mut state) = store.read() else { return false };
    if !state.due(now, at_end) || store.delivering() { return false; }
    state.last_attempt = now;
    store.write(&state).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A store of its own, and the test's turn among the tests of a lock (crate::sys::turns).
    struct Scratch(Store, crate::sys::turns::Locking);
    impl Scratch {
        fn new() -> Self {
            let dir = std::env::temp_dir().join(format!("rb-telemetry-test-{}", random_uuid().unwrap()));
            Self(Store { dir }, crate::sys::turns::locking())
        }
        fn enable(&self) -> State {
            let _lock = self.0.lock(true, CONTROL_WAIT).unwrap();
            let mut state = State::new(random_uuid().unwrap());
            state.key = Some("phc_testcapture".into());
            state.host = Some("https://capture.example.test".into());
            self.0.write(&state).unwrap();
            state
        }
        fn flush(&self, policy: &Policy, transmit: impl FnOnce(&str, &Value) -> Sent) -> Value { flush_once(&self.0, policy, &mut None, transmit) }
        /// A capture the test expects to be queued: asked for again while the queue's lock looks taken (see settled).
        fn capture(&self, events: &[Event], policy: &Policy) {
            CAPTURED.with(|c| c.set(false));
            assert!(settled(|| { capture_at(&self.0, events, policy); CAPTURED.with(|c| c.replace(false)) }), "the capture was never queued");
        }
        /// The delivery's lock, taken when the test expects it free.
        fn deliverer(&self) -> File {
            let mut held = None;
            assert!(settled(|| { held = self.0.deliverer().ok(); held.is_some() }), "the delivery's lock is held");
            held.unwrap()
        }
        fn names(&self) -> Vec<String> { self.0.read().unwrap().queue.iter().map(|e| e.get_str("event").unwrap().to_string()).collect() }
    }
    impl Drop for Scratch { fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0.dir); } }
    /// Another test that starts a program gives it a copy of every file this process has open, a lock file's
    /// among them, and with it the lock, until the program runs; on a busy machine that can take a while. What a
    /// test expects to find free it asks for again within this bound, which nothing here comes near unless what it
    /// asks for is really held.
    const SETTLE: Duration = Duration::from_secs(10);
    fn settled(mut ok: impl FnMut() -> bool) -> bool {
        let deadline = Instant::now() + SETTLE;
        while !ok() {
            if Instant::now() >= deadline { return false; }
            std::thread::sleep(Duration::from_millis(5));
        }
        true
    }
    fn policy() -> Policy { Policy { reason: None, cohort: None } }
    fn operation() -> Event { Event::OperationCompleted { command: Command::from_name("run add").unwrap(), surface: Surface::Cli, actor: Actor::Agent, outcome: Outcome::Success, duration_ms: 34 } }
    fn inspected() -> Event { Event::RecordInspected { surface: Surface::Cli, actor: Actor::Human } }

    #[test]
    fn default_off_creates_nothing_and_never_calls_a_sender() {
        let t = Scratch::new();
        capture_at(&t.0, &[operation()], &policy());
        assert!(!t.0.dir.exists());
        let answer = t.flush(&policy(), |_, _| panic!("off must not send"));
        assert_eq!(answer.get_str("reason"), Some("not_enabled"));
        assert!(!claim(&t.0, Utc::now().timestamp(), true));
        assert!(!t.0.dir.exists());
    }

    #[test]
    fn switches_and_ci_win_and_an_environment_one_never_opts_in() {
        for (key, value, reason) in [("DO_NOT_TRACK", "1", "do_not_track"), ("RB_TELEMETRY", "0", "environment_disabled"),
            ("GITHUB_ACTIONS", "true", "ci"), ("CI", "1", "ci"), ("RB_TEST_SEED", "abc", "test"), ("RB_TEST_SYNC_PACE", "0.01", "test"), ("RB_TELEMETRY_SUPPRESS", "1", "parent_suppressed"),
            ("TEAMCITY_VERSION", "2024.1", "ci"), ("CODEBUILD_BUILD_ARN", "arn:aws:codebuild:x", "ci"), ("bamboo_planKey", "PROJ-PLAN", "ci"),
            ("BUILDER_OUTPUT", "/builder/outputs", "ci")] {
            let p = Policy::from_env(|k| if k == key { Some(value.into()) } else { None });
            assert_eq!(p.reason, Some(reason), "{key}={value}");
        }
        assert_eq!(Policy::from_env(|k| (k == "BUILD_NUMBER").then(|| "12".into())).reason, None);
        assert!(!state_status(None, &policy()).get("enabled").unwrap().as_bool().unwrap());
    }

    #[test]
    fn rb_telemetry_switches_off_on_anything_but_an_explicit_yes() {
        for value in ["0", "false", "off", "no", "n", "disabled", " 0", "NO", "", "  ", "2", "nope"] {
            let p = Policy::from_env(|k| (k == "RB_TELEMETRY").then(|| value.into()));
            assert_eq!(p.reason, Some("environment_disabled"), "{value:?}");
        }
        for value in ["1", "true", "on", "yes", " YES ", "True"] {
            assert_eq!(Policy::from_env(|k| (k == "RB_TELEMETRY").then(|| value.into())).reason, None, "{value:?}");
        }
    }

    #[test]
    fn the_cohort_belongs_to_the_installation_and_a_process_can_override_it() {
        assert_eq!(Policy::from_env(|_| None).cohort, None);
        for (value, cohort) in [("internal", Some("internal")), ("Internal", Some("internal")), ("INTERNAL ", Some("internal")),
            ("External", Some("external")), ("1", Some("internal")), ("", None)] {
            assert_eq!(Policy::from_env(|k| (k == "RB_TELEMETRY_COHORT").then(|| value.into())).cohort, cohort, "{value:?}");
        }
        // cargo test builds without a destination: the project's own
        assert_eq!(build_cohort(), "internal");
        let t = Scratch::new();
        let external = Policy { reason: None, cohort: Some("external") };
        enable_at(&t.0, None, None, &external, Actor::Human).unwrap();
        assert_eq!(t.0.read().unwrap().cohort, Some("external"));
        t.capture(&[operation()], &policy());
        t.capture(&[operation()], &Policy { reason: None, cohort: Some("internal") });
        let cohorts: Vec<String> = t.0.read().unwrap().queue.iter().map(|e| e.get("properties").unwrap().get_str("cohort").unwrap().to_string()).collect();
        assert_eq!(cohorts, ["external", "external", "internal"]);
        enable_at(&t.0, None, None, &policy(), Actor::Human).unwrap();
        assert_eq!(t.0.read().unwrap().cohort, Some("external"));
        assert_eq!(state_status(t.0.read().as_ref(), &policy()).get_str("cohort"), Some("external"));
    }

    #[test]
    fn queue_has_only_enum_values_and_random_identity() {
        let t = Scratch::new();
        let initial = t.enable();
        t.capture(&[operation()], &policy());
        let state = t.0.read().unwrap();
        assert_eq!(state.queue.len(), 1);
        assert_eq!(state.id, initial.id);
        let event = &state.queue[0];
        assert_eq!(event.get_str("distinct_id"), Some(state.id.as_str()));
        assert!(valid_event(event, &state.id));
        let props = event.get("properties").unwrap();
        assert_eq!(props.get_str("actor"), Some("agent"));
        assert_eq!(props.get_str("duration_bucket"), Some("10_99ms"));
        assert_eq!(props.get("$process_person_profile").unwrap().as_bool(), Some(false));
        assert_ne!(random_uuid(), random_uuid());
        assert!(Command::from_name("run add private-model loss=0.001 --from /private/results.json").is_none());
        assert!(Command::from_bridge("/private/project").is_none());
        assert_eq!(Command::from_bridge("add_run"), Command::from_name("run add"));
        assert_eq!(Command::from_bridge("get"), Command::from_name("show"));
        assert!(Command::from_bridge("load").is_none());
    }

    #[test]
    fn every_release_spelling_of_the_version_makes_events() {
        assert!(make_event(operation(), &random_uuid().unwrap(), "external").is_some(), "{}", crate::VERSION);
        for v in ["0.6.0", "0.6.0.dev0", "0.6.0.dev20261003", "0.6.0rc1", "0.6.0a1", "0.6.0b12", "0.6.0.post1", "0.6.0rc1.post2.dev3", "1!2.0", "2026.10"] {
            assert!(version_ok(v), "{v}");
        }
        let long = "9".repeat(65);
        for v in ["0.6.0+local", "0.6.0 ", "v0.6.0", "0.6.0-private", "", "0.6.0rc", long.as_str()] {
            assert!(!version_ok(v), "{v}");
        }
    }

    #[test]
    fn corrupted_or_enriched_events_are_never_previewed_or_uploaded() {
        let t = Scratch::new();
        let mut state = t.enable();
        let good = make_event(operation(), &state.id, "external").unwrap();
        let mut extra = good.clone();
        extra.as_dict_mut().unwrap().get_mut("properties").unwrap().as_dict_mut().unwrap().insert("research_title".into(), "private work".into());
        let mut arbitrary = good.clone();
        arbitrary.as_dict_mut().unwrap().get_mut("properties").unwrap().as_dict_mut().unwrap().insert("command".into(), "private work".into());
        state.queue = vec![good, extra, arbitrary];
        t.0.write(&state).unwrap();
        let kept = t.0.read().unwrap();
        assert_eq!(kept.queue.len(), 1);
        assert!(!pyjson::dumps(&kept.payload().unwrap()).contains("private work"));
    }

    #[test]
    fn queue_is_bounded_and_old_events_expire() {
        let t = Scratch::new();
        let mut state = t.enable();
        let event = make_event(operation(), &state.id, "external").unwrap();
        state.queue = vec![event; MAX_EVENTS + 9];
        state.prune(Utc::now().timestamp());
        assert_eq!(state.queue.len(), MAX_EVENTS);
        state.prune(Utc::now().timestamp() + MAX_AGE_SECONDS + 1);
        assert!(state.queue.is_empty());
    }

    #[test]
    fn a_full_queue_gives_up_operations_before_milestones_and_consent_last() {
        let t = Scratch::new();
        let mut state = t.enable();
        let id = state.id.clone();
        let consent = make_event(Event::TelemetryEnabled { surface: Surface::Cli, actor: Actor::Human }, &id, "external").unwrap();
        let ready = make_event(Event::RecordReady { route: RecordRoute::Declared, surface: Surface::Sdk, actor: Actor::Human }, &id, "external").unwrap();
        state.queue = vec![consent.clone()];
        state.queue.extend((0..60).map(|_| make_event(operation(), &id, "external").unwrap()));
        state.queue.extend((0..60).map(|_| ready.clone()));
        state.prune(Utc::now().timestamp());
        assert_eq!(state.queue.len(), MAX_EVENTS);
        assert_eq!(state.queue[0], consent);
        assert_eq!(state.queue.iter().filter(|e| e.get_str("event") == Some("rb_record_ready")).count(), 60);
        state.queue.extend((0..60).map(|_| ready.clone()));
        state.prune(Utc::now().timestamp());
        assert_eq!(state.queue.len(), MAX_EVENTS);
        assert_eq!(state.queue[0], consent);
        assert!(state.queue.iter().all(|e| e.get_str("event") != Some("rb_operation_completed")));
    }

    #[test]
    fn destination_refuses_tokens_and_non_https_or_credentialled_addresses() {
        assert!(host_ok("https://us.i.posthog.com"));
        assert!(host_ok("http://127.0.0.1:43127"));
        for h in ["http://example.com", "https://user:password@example.com", "https://example.com/path", "https://example.com/?query=x", "https://example.com/#x"] { assert!(!host_ok(h), "{h}"); }
        assert!(key_ok("phc_publiccapturekey"));
        assert!(!key_ok("phx_personalapikey"));
        assert!(!key_ok("phc_a\nprivate"));
        assert!(inside_record(Path::new("/project/.rb/cache/telemetry")));
        assert!(inside_record(Path::new("/project/.RB/settings")) && inside_record(Path::new("/project/.Rb/x")));
        assert!(!inside_record(Path::new("/project/.rbx/settings")) && !inside_record(Path::new("/project/rb/settings")));
    }

    #[test]
    fn suppression_is_nested_and_does_not_outlive_its_scope() {
        assert!(!SUPPRESSED.with(|n| n.get() > 0));
        let one = suppress();
        { let _two = suppress(); assert_eq!(SUPPRESSED.with(Cell::get), 2); }
        assert_eq!(SUPPRESSED.with(Cell::get), 1);
        drop(one);
        assert_eq!(SUPPRESSED.with(Cell::get), 0);
    }

    #[test]
    fn unsuccessful_sender_keeps_exact_identifiers_for_retry() {
        let t = Scratch::new();
        t.enable();
        t.capture(&[operation()], &policy());
        let before = t.0.read().unwrap().queue;
        let answer = t.flush(&policy(), |_, payload| { assert_eq!(payload.get("batch").unwrap().as_list().unwrap(), &before); Sent::Failed });
        assert_eq!(answer.get_str("reason"), Some("not_sent"));
        assert_eq!(t.0.read().unwrap().queue, before);
        let sent = t.flush(&policy(), |_, payload| {
            assert_eq!(payload.get("batch").unwrap().as_list().unwrap(), &before);
            Sent::Accepted
        });
        assert_eq!(sent.get("sent").and_then(Value::as_i64), Some(1));
        assert!(t.0.read().unwrap().queue.is_empty());
        assert_eq!(t.flush(&policy(), |_, _| panic!("nothing to send")).get_str("reason"), Some("empty"));
    }

    #[test]
    fn a_capture_during_a_slow_delivery_is_kept() {
        let t = Scratch::new();
        t.enable();
        t.capture(&[operation()], &policy());
        let answer = t.flush(&policy(), |_, payload| {
            assert_eq!(payload.get("batch").unwrap().as_list().unwrap().len(), 1);
            // the lock is free while the batch is on the wire: taken at once, without waiting at all
            assert!(settled(|| t.0.lock(false, Duration::ZERO).is_ok()), "the queue's lock is held across the network");
            t.capture(&[inspected()], &policy());
            assert_eq!(t.0.read().unwrap().queue.len(), 2);
            Sent::Accepted
        });
        assert_eq!(answer.get("sent").and_then(Value::as_i64), Some(1));
        assert_eq!(t.names(), ["rb_record_inspected"]);
    }

    #[test]
    fn a_delivery_never_edits_the_queue_of_a_new_identity() {
        let t = Scratch::new();
        t.enable();
        t.capture(&[operation()], &policy());
        t.flush(&policy(), |_, _| {
            disable_at(&t.0).unwrap();
            enable_at(&t.0, Some("phc_testcapture".into()), Some("https://capture.example.test".into()), &policy(), Actor::Human).unwrap();
            Sent::Accepted
        });
        assert_eq!(t.names(), ["rb_telemetry_enabled"]);
    }

    #[test]
    fn a_milestone_goes_out_after_the_floor_and_operations_wait_for_the_interval() {
        let t = Scratch::new();
        let mut state = t.enable();
        let now = Utc::now().timestamp();
        assert!(!state.due(now, false) && !state.due(now, true), "nothing queued");
        state.queue.push(make_event(operation(), &state.id, "internal").unwrap());
        state.last_attempt = now - 10;
        assert!(!state.due(now, false));
        assert!(state.due(now, true), "a long-running surface ending sends what is left");
        state.last_attempt = now - 1;
        assert!(!state.due(now, true), "but not within the floor");
        state.last_attempt = now - FLUSH_INTERVAL_SECONDS;
        assert!(state.due(now, false));
        state.queue.push(make_event(inspected(), &state.id, "internal").unwrap());
        state.last_attempt = now - MILESTONE_FLOOR_SECONDS + 1;
        assert!(!state.due(now, false), "never twice within the floor");
        state.last_attempt = now - MILESTONE_FLOOR_SECONDS;
        assert!(state.due(now, false));
        state.host = None;
        assert!(!state.due(now, true), "no destination, nothing to start");
    }

    #[test]
    fn one_delivery_at_a_time_and_none_twice_within_the_floor() {
        let t = Scratch::new();
        t.enable();
        t.capture(&[inspected()], &policy());
        let now = Utc::now().timestamp();
        let held = t.deliverer();
        assert!(t.0.delivering());
        assert!(!claim(&t.0, now, true), "a running delivery takes what arrives");
        drop(held);
        assert!(settled(|| !t.0.delivering()));
        assert!(settled(|| claim(&t.0, now, false)));
        assert_eq!(t.0.read().unwrap().last_attempt, now);
        assert!(!claim(&t.0, now + 1, false), "the attempt was stamped");
        assert!(settled(|| claim(&t.0, now + MILESTONE_FLOOR_SECONDS, false)));
    }

    #[test]
    fn each_failed_send_doubles_the_wait_up_to_an_hour_and_a_success_ends_it() {
        let t = Scratch::new();
        t.enable();
        t.capture(&[inspected()], &policy()); // a milestone: due after the floor while nothing has failed
        let now = Utc::now().timestamp();
        let mut waits = vec![];
        for failures in 1..=8 {
            assert_eq!(t.flush(&policy(), |_, _| Sent::Failed).get_str("reason"), Some("not_sent"));
            let mut state = t.0.read().unwrap();
            assert_eq!((state.failures, state.refused), (failures, false));
            let wait = state.retry_wait();
            waits.push(wait);
            state.last_attempt = now - wait + 1;
            assert!(!state.due(now, false) && !state.due(now, true), "neither a milestone nor a surface's end goes sooner");
            state.last_attempt = now - wait;
            assert!(state.due(now, false));
        }
        assert_eq!(waits, [60, 120, 240, 480, 960, 1920, 3600, 3600]);
        let at = t.0.read().unwrap().last_attempt;
        assert!(!claim(&t.0, at + RETRY_WAIT_LIMIT_SECONDS - 1, true), "nothing is started within the wait");
        assert!(settled(|| claim(&t.0, at + RETRY_WAIT_LIMIT_SECONDS, false)));
        assert_eq!(t.flush(&policy(), |_, _| Sent::Accepted).get("sent").and_then(Value::as_i64), Some(1));
        let mut state = t.0.read().unwrap();
        assert_eq!((state.failures, state.refused), (0, false));
        state.queue.push(make_event(inspected(), &state.id, "internal").unwrap());
        state.last_attempt = now - MILESTONE_FLOOR_SECONDS;
        assert!(state.due(now, false), "a milestone goes out after the floor again");
    }

    #[test]
    fn a_refusal_for_good_waits_the_hour_until_a_send_succeeds_or_the_destination_changes() {
        let t = Scratch::new();
        t.enable();
        t.capture(&[inspected()], &policy());
        assert_eq!(t.flush(&policy(), |_, _| Sent::Refused).get_str("reason"), Some("refused"));
        let state = t.0.read().unwrap();
        assert_eq!((state.failures, state.refused, state.retry_wait()), (1, true, RETRY_WAIT_LIMIT_SECONDS));
        assert!(!claim(&t.0, state.last_attempt + RETRY_WAIT_LIMIT_SECONDS - 1, true));
        t.flush(&policy(), |_, _| Sent::Failed);
        assert_eq!(t.0.read().unwrap().retry_wait(), RETRY_WAIT_LIMIT_SECONDS, "a failure after a refusal does not shorten the wait");
        t.flush(&policy(), |_, _| Sent::Accepted);
        let cleared = t.0.read().unwrap();
        assert_eq!((cleared.failures, cleared.refused), (0, false));
        t.capture(&[inspected()], &policy());
        t.flush(&policy(), |_, _| Sent::Refused);
        enable_at(&t.0, Some("phc_newcapture".into()), None, &policy(), Actor::Human).unwrap();
        let fresh = t.0.read().unwrap();
        assert_eq!((fresh.failures, fresh.refused, fresh.last_attempt), (0, false, 0), "a new destination starts without a wait");
    }

    #[test]
    fn a_refusal_is_told_from_a_failure_worth_trying_again() {
        for (status, sent) in [(200, Sent::Accepted), (204, Sent::Accepted), (301, Sent::Failed), (400, Sent::Refused), (401, Sent::Refused),
            (403, Sent::Refused), (404, Sent::Refused), (408, Sent::Failed), (413, Sent::Refused), (429, Sent::Failed), (500, Sent::Failed), (503, Sent::Failed)] {
            assert_eq!(Sent::from_status(status), sent, "{status}");
        }
    }

    #[test]
    fn only_a_capture_that_queued_an_event_marks_its_thread() {
        let t = Scratch::new();
        CAPTURED.with(|c| c.set(false));
        capture_at(&t.0, &[operation()], &policy());
        assert!(!CAPTURED.with(Cell::get), "not enabled: nothing queued");
        t.enable();
        capture_at(&t.0, &[operation()], &Policy { reason: Some("do_not_track"), cohort: None });
        assert!(!CAPTURED.with(Cell::get), "a switch: nothing queued");
        t.capture(&[operation()], &policy());
    }

    #[test]
    fn a_background_delivery_lets_go_of_its_lock_under_the_queue_lock_when_nothing_is_left() {
        let t = Scratch::new();
        t.enable();
        let mut held = Some(t.deliverer());
        let answer = flush_once(&t.0, &policy(), &mut held, |_, _| panic!("nothing to send"));
        assert_eq!(answer.get_str("reason"), Some("empty"));
        assert!(held.is_none() && settled(|| !t.0.delivering()));
    }

    #[test]
    fn suppression_blocks_collection_and_forced_delivery() {
        let t = Scratch::new();
        t.enable();
        t.capture(&[operation()], &policy());
        let before = t.0.read().unwrap().queue;
        let no = Policy { reason: Some("do_not_track"), cohort: None };
        capture_at(&t.0, &[operation()], &no);
        let answer = t.flush(&no, |_, _| panic!("forced flush cannot bypass opt-out"));
        assert_eq!(answer.get_str("reason"), Some("do_not_track"));
        assert_eq!(t.0.read().unwrap().queue, before);
    }

    #[test]
    fn consent_is_once_per_identity_and_disable_erases_everything() {
        let t = Scratch::new();
        enable_at(&t.0, None, None, &policy(), Actor::Human).unwrap();
        let initial = t.0.read().unwrap();
        assert_eq!(initial.queue.len(), 1);
        assert_eq!(initial.queue[0].get_str("event"), Some("rb_telemetry_enabled"));
        assert!(initial.payload().is_none());
        // Supplying the previously absent destination keeps the already previewable queue and consent event.
        enable_at(&t.0, Some("phc_testcapture".into()), Some("http://127.0.0.1:12345".into()), &policy(), Actor::Human).unwrap();
        let again = t.0.read().unwrap();
        assert_eq!(again.id, initial.id);
        assert_eq!(again.queue, initial.queue);
        assert!(again.payload().is_some());
        disable_at(&t.0).unwrap();
        assert!(!t.0.path().exists());
        assert!(t.0.read().is_none());
        enable_at(&t.0, None, None, &policy(), Actor::Human).unwrap();
        let next = t.0.read().unwrap();
        assert_ne!(initial.id, next.id);
        assert_eq!(next.queue.len(), 1);
    }

    #[test]
    fn consent_given_under_a_switch_goes_out_with_the_first_event_after_it() {
        let t = Scratch::new();
        let dnt = Policy { reason: Some("do_not_track"), cohort: None };
        enable_at(&t.0, Some("phc_testcapture".into()), Some("http://127.0.0.1:12345".into()), &dnt, Actor::Human).unwrap();
        let held = t.0.read().unwrap();
        assert!(held.queue.is_empty() && held.consent_pending == Some(Actor::Human));
        capture_at(&t.0, &[operation()], &dnt);
        assert!(t.0.read().unwrap().queue.is_empty());
        t.capture(&[operation()], &policy());
        assert_eq!(t.names(), ["rb_telemetry_enabled", "rb_operation_completed"]);
        assert_eq!(t.0.read().unwrap().queue[0].get("properties").unwrap().get_str("actor"), Some("human_inferred"));
        assert!(t.0.read().unwrap().consent_pending.is_none());
        t.capture(&[operation()], &policy());
        assert_eq!(t.names().iter().filter(|n| *n == "rb_telemetry_enabled").count(), 1);
        // enabling again once the switch is gone queues a consent still waiting
        let u = Scratch::new();
        enable_at(&u.0, None, None, &dnt, Actor::Human).unwrap();
        enable_at(&u.0, None, None, &policy(), Actor::Human).unwrap();
        assert_eq!(u.names(), ["rb_telemetry_enabled"]);
    }

    #[test]
    fn disable_erases_interrupted_write_files_even_without_a_state_file() {
        let t = Scratch::new();
        let state = t.enable();
        let leftover = t.0.dir.join(format!(".state-{}.tmp", random_uuid().unwrap()));
        std::fs::write(&leftover, pyjson::dumps(&state.value())).unwrap();
        let unrelated = t.0.dir.join(".state-not-a-uuid.tmp");
        std::fs::write(&unrelated, "unrelated").unwrap();
        std::fs::remove_file(t.0.path()).unwrap();
        disable_at(&t.0).unwrap();
        assert!(!leftover.exists());
        assert!(unrelated.exists());
        assert!(!t.0.path().exists());
    }

    #[test]
    fn changing_a_destination_never_exports_the_previous_identity_or_queue() {
        let t = Scratch::new();
        let initial = t.enable();
        t.capture(&[operation()], &policy());
        enable_at(&t.0, Some("phc_newcapture".into()), None, &policy(), Actor::Agent).unwrap();
        let changed = t.0.read().unwrap();
        assert_ne!(initial.id, changed.id);
        assert_eq!(changed.queue.len(), 1);
        assert_eq!(changed.queue[0].get_str("event"), Some("rb_telemetry_enabled"));
    }

    #[test]
    fn settings_refuse_research_storage_and_relative_locations() {
        assert!(Store::at_base(Path::new("relative/settings")).is_none());
        assert!(Store::at_base(Path::new("/project/.rb/settings")).is_none());
        assert!(Store::at_base(Path::new("/project/.RB/settings")).is_none());
        let t = Scratch::new();
        assert!(Store::at_base(&t.0.dir).is_some());
        assert!(Store::at_base(&t.0.dir.join("missing/../settings")).is_none());
        assert!(!t.0.dir.exists());
    }

    #[test]
    fn malformed_state_cannot_trigger_unbounded_recursive_parsing() {
        let t = Scratch::new();
        t.enable();
        std::fs::write(t.0.path(), "[".repeat(100_000)).unwrap();
        assert!(t.0.read().is_none());
        assert!(shallow_json(r#"{"text":"[[[[[[[[[[[\\\"[[[[[[["}"#));
    }

    #[cfg(unix)]
    #[test]
    fn settings_refuse_symlinks_into_research_and_are_owner_readable() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let t = Scratch::new();
        let record = t.0.dir.join("project/.rb");
        std::fs::create_dir_all(&record).unwrap();
        let link = t.0.dir.join("settings-link");
        symlink(&record, &link).unwrap();
        assert!(Store::at_base(&link).is_none());
        t.enable();
        assert_eq!(std::fs::metadata(t.0.path()).unwrap().permissions().mode() & 0o777, 0o600);
    }

    #[cfg(unix)]
    #[test]
    fn a_state_or_lock_that_is_not_a_regular_file_is_refused_without_waiting() {
        let t = Scratch::new();
        t.enable();
        let fifo = |p: &Path| {
            let c = std::ffi::CString::new(p.to_str().unwrap()).unwrap();
            assert_eq!(unsafe { libc::mkfifo(c.as_ptr(), 0o600) }, 0);
        };
        std::fs::remove_file(t.0.path()).unwrap();
        fifo(&t.0.path());
        GAVE_UP.with(|g| g.borrow_mut().clear());
        assert!(t.0.read().is_none());
        capture_at(&t.0, &[operation()], &policy());
        disable_at(&t.0).unwrap();
        assert!(std::fs::symlink_metadata(t.0.path()).is_err(), "the opt-out still clears it");
        t.enable();
        let lock = t.0.dir.join("lock");
        std::fs::remove_file(&lock).unwrap();
        fifo(&lock);
        assert!(t.0.lock(false, CONTROL_WAIT).is_err_and(|e| e.kind() != io::ErrorKind::WouldBlock));
        disable_at(&t.0).unwrap();
        assert!(t.0.read().is_none());
        assert!(GAVE_UP.with(|g| g.take()).is_empty(), "nothing was waited for");
    }

    #[test]
    fn a_busy_queue_waits_briefly_then_drops_an_observation() {
        let t = Scratch::new();
        t.enable();
        // the wait hands the lock over when its holder lets go: no deadline is in play
        let held = t.0.lock(false, CONTROL_WAIT).unwrap();
        let release = std::thread::spawn(move || { std::thread::sleep(Duration::from_millis(10)); drop(held); });
        drop(t.0.lock(false, Duration::from_secs(60)).expect("a holder that lets go within the wait hands the lock over"));
        release.join().unwrap();
        t.capture(&[operation()], &policy());
        assert_eq!(t.0.read().unwrap().queue.len(), 1);
        // a holder that never lets go: the observation is waited for CAPTURE_WAIT, then dropped
        let _held = t.0.lock(false, CONTROL_WAIT).unwrap();
        GAVE_UP.with(|g| g.borrow_mut().clear());
        let started = Instant::now();
        capture_at(&t.0, &[operation()], &policy());
        assert!(started.elapsed() >= CAPTURE_WAIT);
        assert_eq!(GAVE_UP.with(|g| g.take()), [CAPTURE_WAIT], "given up after the capture's wait, not a longer one");
        assert_eq!(t.0.read().unwrap().queue.len(), 1);
    }

    #[test]
    fn controls_wait_for_the_lock_and_then_say_another_process_has_it() {
        let t = Scratch::new();
        t.enable();
        let _held = t.0.lock(false, CONTROL_WAIT).unwrap();
        let started = Instant::now();
        let err = disable_at(&t.0).unwrap_err();
        assert!(started.elapsed() >= CONTROL_WAIT);
        assert!(err.message.contains("Another rb process"), "{}", err.message);
        assert!(t.0.path().exists());
    }
}
